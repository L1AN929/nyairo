"""0.2 Managed Artifact Representation Binding, Revocable PermissionLease + CombinedVeto, and Forget/DeleteCase Lifecycle.

Deterministic zero-LLM governance module (``llm_calls == 0``) aligning with:
- 17-工程契约与证据完整设计 §5.1-§5.2 & §7 (P09, P22-P25)
- F-010..F-014 (ManagedArtifact / ArtifactVersion / RepresentationBinding)
- F-070, F-149..F-154 (PermissionLease, CombinedVeto, Ch.14 Scenario 8 race, Forget/DeleteCase, Derivation/ReadBarrier)

Core invariants
---------------
1. All JSON state mutations acquire ``state_tx_guard.state_file_lock`` and persist via
   ``state_tx_guard.atomic_write_json``.
2. All read / inspect / evaluation / barrier-check functions are 100% zero-write:
   they never create directories, never create ``.lock`` files, and never alter file mtimes.
3. All paths are validated against workspace root traversal, symlinks, and junctions.
4. Opaque tombstone digests use per-control-ledger HMAC-SHA256; neither recoverable plaintext
   nor raw unsalted ``sha256(secret)`` is ever written into manifests, tombstones, logs,
   or clarification prompts.
5. Neutral TA phrasing throughout (no hardcoded character names or gendered pronouns).
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import stat
import sys
import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

try:
    from components.life.scripts import state_tx_guard as _tx
except ImportError:  # pragma: no cover
    _LIFE_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
    if _LIFE_SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, _LIFE_SCRIPTS_DIR)
    import state_tx_guard as _tx  # type: ignore[no-redef]


SCHEMA_VERSION = "nyairo.v02.representation_lease_forget.v1"
CONTROL_LEDGER_SCHEMA = "nyairo.v02.control_highwater_ledger.v1"

STATE_REL_PATH = ("governance", "v02_representation_lease_forget_state.json")
CONTROL_LEDGER_REL_PATH = ("control_authority", "v02_control_highwater_ledger.json")

DEFAULT_TZ = timezone(timedelta(hours=8))

# ManagedArtifact orthogonal axes
LIFECYCLE_ACTIVE = "ACTIVE"
LIFECYCLE_ARCHIVED = "ARCHIVED"
LIFECYCLE_TOMBSTONED = "TOMBSTONED"
VALID_LIFECYCLES = frozenset({LIFECYCLE_ACTIVE, LIFECYCLE_ARCHIVED, LIFECYCLE_TOMBSTONED})

CONTENT_AVAILABLE = "AVAILABLE"
CONTENT_UNAVAILABLE = "UNAVAILABLE"
CONTENT_CORRUPT = "CORRUPT"

REP_CURRENT = "CURRENT"
REP_MISSING = "MISSING"
REP_DIVERGED = "DIVERGED"
REP_UNBOUND = "UNBOUND"
REPRESENTATION_STATES = (REP_CURRENT, REP_MISSING, REP_DIVERGED, REP_UNBOUND)

SOURCE_ACTION_COMMIT = "ACTION_COMMIT"
SOURCE_EXPLICIT_IMPORT = "EXPLICIT_IMPORT"

# PermissionLease & CombinedVeto
RES_RESERVED = "RESERVED"
RES_CONSUMED = "CONSUMED"
RES_UNKNOWN_HELD = "UNKNOWN_HELD"
RES_RELEASED = "RELEASED"

OUTCOME_PENDING = "PENDING"
OUTCOME_SUCCEEDED = "SUCCEEDED"
OUTCOME_FAILED = "FAILED"
OUTCOME_UNKNOWN = "UNKNOWN"
OUTCOME_NOT_EXECUTED = "NOT_EXECUTED"

CODE_LEASE_REVOKED_BEFORE_DISPATCH = "LEASE_REVOKED_BEFORE_DISPATCH"
CODE_SOURCE_REVISION_CHANGED = "SOURCE_REVISION_CHANGED"
CODE_BLOCKED_CONFLICT = "BLOCKED_CONFLICT"

# Forget / DeleteCase & Multi-hop Derivation
DERIVATION_SURFACES: tuple[str, ...] = (
    "episode",
    "period",
    "p12_narrative",
    "vector_index",
    "belief",
    "drafts",
    "v03_ecology",
    "v04_neighborhood",
    "v04_elastic_play",
    "v05_living_town",
    "v05_home_craft",
    "v05_inner_spark",
    "v08_composable_body",
    "v08_living_traces",
    "v08_emergent_echoes",
    "lived_context",
)

CANONICAL_REPRESENTATION_LEASE_SCOPES: tuple[str, ...] = (
    "skill_book",
    "episodic_memory",
    "private_diary",
    "personal_artifact",
)

CASE_HOLD_PENDING = "HOLD_PENDING_SCOPE_CLARIFICATION"
CASE_WAITING_RETENTION = "WAITING_RETENTION"
CASE_COMPLETE = "COMPLETE"
CASE_RETRACTED = "RETRACTED_ONLY"
CASE_CANCELLED = "HOLD_CANCELLED_EXPLICIT"

_AMBIGUOUS_FORGET_PATTERNS = (
    "忘掉刚才那段",
    "忘掉刚才那句",
    "刚才那段忘了吧",
    "forget what i just said",
    "forget that part",
    "forget earlier",
)

_EXPLICIT_FORGET_PATTERNS = (
    "删掉记录和相关记忆",
    "彻底删除",
    "确认删除全部",
    "delete record and derived memories",
    "explicit_delete",
)

MAX_OPERATION_LEDGER = 512
MAX_AUDIT_LOG = 512


def _now_iso(now_iso: Optional[str] = None) -> str:
    if isinstance(now_iso, str) and now_iso.strip():
        return now_iso.strip()
    return datetime.now(DEFAULT_TZ).isoformat(timespec="seconds")


def _parse_dt(raw: Optional[str]) -> Optional[datetime]:
    if not raw or not isinstance(raw, str):
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


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_text(text: str) -> str:
    return _sha256_bytes(text.encode("utf-8"))


def _state_path(day_root: Path | str) -> Path:
    return Path(day_root).joinpath(*STATE_REL_PATH)


CONTROL_ROOT_ENV_VAR = "NYAIRO_V02_CONTROL_ROOT"


def resolve_v02_control_root(
    day_root: Path | str,
    control_root: Path | str | None = None,
) -> Path:
    """Resolve external authority root for v0.2 control highwater & V-C seals.

    Priority: explicit ``control_root`` -> ``NYAIRO_V02_CONTROL_ROOT`` env var -> ``day_root``.
    """
    if control_root is not None and str(control_root).strip():
        return Path(control_root)
    env_val = str(os.environ.get(CONTROL_ROOT_ENV_VAR) or "").strip()
    if env_val:
        return Path(env_val)
    return Path(day_root)


def _control_ledger_path(day_root: Path | str, control_root: Path | str | None = None) -> Path:
    base = resolve_v02_control_root(day_root, control_root)
    return base.joinpath(*CONTROL_LEDGER_REL_PATH)


def _empty_state() -> dict[str, Any]:
    return {
        "schema": SCHEMA_VERSION,
        "revision": 0,
        "next_op_seq": 0,
        "last_control_deletion_seq": 0,
        "last_control_revocation_seq": 0,
        "operations": {},
        "committed_op_ring": [],
        "artifacts": {},
        "versions": {},
        "erased_version_ids": [],
        "representations": {},
        "external_edit_observations": [],
        "leases": {},
        "dispatches": {},
        "delivery_outbox": [],
        "sources": {},
        "derivations": {},
        "forget_cases": {},
        "holds": {},
        "audit_log": [],
    }


def _empty_control_ledger() -> dict[str, Any]:
    return {
        "schema": CONTROL_LEDGER_SCHEMA,
        "control_epoch": 1,
        "writer_fence_epoch": 1,
        "deletion_highwater_seq": 0,
        "revocation_highwater_seq": 0,
        "hmac_key_hex": secrets.token_hex(32),
        "tombstones": {},
        "revoked_leases": {},
        "erased_version_ids": [],
    }


def _read_json_with_health(path: Path) -> tuple[Optional[dict[str, Any]], bool, bool]:
    """Read JSON dict fail-closed; return ``(doc, exists_on_disk, is_corrupt)``."""
    try:
        exists_on_disk = path.is_symlink() or os.path.lexists(path)
    except OSError:
        return None, True, True
    if not exists_on_disk:
        return None, False, False
    try:
        if path.is_symlink() or not path.is_file():
            return None, True, True
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None, True, True
        return data, True, False
    except (OSError, UnicodeDecodeError, ValueError, TypeError):
        return None, True, True


def _read_json_readonly(path: Path) -> Optional[dict[str, Any]]:
    doc, _exists, is_corrupt = _read_json_with_health(path)
    return None if is_corrupt else doc


def _sanitize_dict_list_fields(
    doc: dict[str, Any],
    *,
    dict_keys: tuple[str, ...],
    list_keys: tuple[str, ...],
) -> bool:
    corrupt = False
    for k in dict_keys:
        if k in doc and not isinstance(doc[k], Mapping):
            doc[k] = {}
            corrupt = True
    for k in list_keys:
        val = doc.get(k)
        if k in doc and (not isinstance(val, list)):
            doc[k] = []
            corrupt = True
    return corrupt


def _load_state_readonly(day_root: Path | str) -> tuple[dict[str, Any], bool]:
    doc, exists, is_corrupt = _read_json_with_health(_state_path(day_root))
    base = _empty_state()
    if is_corrupt:
        base["_corrupt_on_disk"] = True
        return base, True
    if doc is None:
        return base, exists
    base.update(doc)
    if _sanitize_dict_list_fields(
        base,
        dict_keys=(
            "operations",
            "artifacts",
            "versions",
            "representations",
            "leases",
            "dispatches",
            "sources",
            "derivations",
            "forget_cases",
            "holds",
        ),
        list_keys=(
            "committed_op_ring",
            "erased_version_ids",
            "external_edit_observations",
            "delivery_outbox",
            "audit_log",
        ),
    ):
        base["_corrupt_fields"] = True
    return base, True


def _load_control_readonly(
    day_root: Path | str,
    control_root: Path | str | None = None,
) -> tuple[dict[str, Any], bool]:
    primary_path = _control_ledger_path(day_root, control_root)
    legacy_path = Path(day_root).joinpath(*CONTROL_LEDGER_REL_PATH)
    same_path = False
    try:
        same_path = primary_path.resolve() == legacy_path.resolve()
    except OSError:
        same_path = primary_path == legacy_path

    doc, exists, is_corrupt = _read_json_with_health(primary_path)
    leg_doc: Optional[dict[str, Any]] = None
    leg_exists = False
    leg_corrupt = False
    if not same_path:
        leg_doc, leg_exists, leg_corrupt = _read_json_with_health(legacy_path)

    base = _empty_control_ledger()
    if is_corrupt or leg_corrupt:
        base["_corrupt_on_disk"] = True
        return base, True
    if doc is None and leg_doc is None:
        return base, (exists or leg_exists)

    if leg_doc is not None:
        base.update(leg_doc)
    if doc is not None:
        if leg_doc is not None:
            merged_tombs = dict(leg_doc.get("tombstones") or {}) if isinstance(leg_doc.get("tombstones"), Mapping) else {}
            if isinstance(doc.get("tombstones"), Mapping):
                merged_tombs.update(doc.get("tombstones") or {})
            merged_rev = dict(leg_doc.get("revoked_leases") or {}) if isinstance(leg_doc.get("revoked_leases"), Mapping) else {}
            if isinstance(doc.get("revoked_leases"), Mapping):
                merged_rev.update(doc.get("revoked_leases") or {})
            base.update(doc)
            base["tombstones"] = merged_tombs
            base["revoked_leases"] = merged_rev
            try:
                base["deletion_highwater_seq"] = max(
                    int(doc.get("deletion_highwater_seq") or 0),
                    int(leg_doc.get("deletion_highwater_seq") or 0),
                )
                base["revocation_highwater_seq"] = max(
                    int(doc.get("revocation_highwater_seq") or 0),
                    int(leg_doc.get("revocation_highwater_seq") or 0),
                )
                base["writer_fence_epoch"] = max(
                    int(doc.get("writer_fence_epoch") or 1),
                    int(leg_doc.get("writer_fence_epoch") or 1),
                )
            except (TypeError, ValueError):
                base["_corrupt_fields"] = True
                return base, True
            if not str(base.get("hmac_key_hex") or "").strip() and str(leg_doc.get("hmac_key_hex") or "").strip():
                base["hmac_key_hex"] = leg_doc["hmac_key_hex"]
            merged_erased = list(
                dict.fromkeys(
                    list(leg_doc.get("erased_version_ids") or ())
                    + list(doc.get("erased_version_ids") or ())
                )
            )
            base["erased_version_ids"] = merged_erased
        else:
            base.update(doc)

    if _sanitize_dict_list_fields(
        base,
        dict_keys=("tombstones", "revoked_leases"),
        list_keys=("erased_version_ids",),
    ):
        base["_corrupt_fields"] = True
    return base, True


def _ensure_control_ledger_locked(
    day_root: Path | str,
    control_root: Path | str | None = None,
) -> dict[str, Any]:
    """Read or initialize the external control ledger inside its own lock."""
    cpath = _control_ledger_path(day_root, control_root)
    doc, exists, is_corrupt = _read_json_with_health(cpath)
    if is_corrupt:
        raise RuntimeError(f"Control authority ledger is corrupt: {cpath}")
    if doc is not None:
        base = _empty_control_ledger()
        base.update(doc)
        _sanitize_dict_list_fields(
            base,
            dict_keys=("tombstones", "revoked_leases"),
            list_keys=("erased_version_ids",),
        )
        return base
    fresh = _empty_control_ledger()
    legacy_path = Path(day_root).joinpath(*CONTROL_LEDGER_REL_PATH)
    try:
        is_different = cpath.resolve() != legacy_path.resolve()
    except OSError:
        is_different = cpath != legacy_path
    if is_different:
        leg_doc, _leg_exists, leg_corrupt = _read_json_with_health(legacy_path)
        if leg_corrupt:
            raise RuntimeError(f"Legacy control authority ledger is corrupt: {legacy_path}")
        if leg_doc is not None:
            fresh.update(leg_doc)
            _sanitize_dict_list_fields(
                fresh,
                dict_keys=("tombstones", "revoked_leases"),
                list_keys=("erased_version_ids",),
            )
    _tx.atomic_write_json(cpath, fresh, sort_keys=True)
    return fresh


def _hmac_tombstone_digest(hmac_key_hex: str, domain: str, item_id: str) -> str:
    key = bytes.fromhex(hmac_key_hex)
    msg = f"{domain}:{item_id}".encode("utf-8")
    return "tomb_hmac:" + hmac.new(key, msg, hashlib.sha256).hexdigest()


def _opaque_source_token(hmac_key_hex: str, item_id: str) -> str:
    key = bytes.fromhex(hmac_key_hex)
    msg = f"opaque_ref:{item_id}".encode("utf-8")
    return "src_tok:" + hmac.new(key, msg, hashlib.sha256).hexdigest()[:24]


def _is_junction(path: Path) -> bool:
    is_junc = getattr(os.path, "isjunction", None)
    if callable(is_junc):
        try:
            if is_junc(path):
                return True
        except OSError:
            return True
    try:
        st = os.lstat(path)
        reparse_flag = getattr(st, "st_file_attributes", 0)
        if reparse_flag & 0x0400:  # FILE_ATTRIBUTE_REPARSE_POINT on Windows
            return True
    except OSError:
        pass
    return False


def _resolve_safe_workspace_path(
    workspace_root: Path | str,
    rel_or_abs_path: Path | str,
) -> tuple[bool, Optional[Path], str]:
    """Validate path stays strictly inside ``workspace_root`` without symlinks or junctions."""
    raw_str = str(rel_or_abs_path or "")
    if not raw_str or "\x00" in raw_str:
        return False, None, "INVALID_EMPTY_OR_NUL_PATH"
    root = Path(workspace_root)
    try:
        root_resolved = root.resolve()
    except OSError:
        return False, None, "WORKSPACE_ROOT_UNRESOLVABLE"

    candidate = Path(raw_str)
    if not candidate.is_absolute():
        candidate = root / candidate

    # Walk ancestor chain inside root (and root itself) checking for symlinks / junctions.
    try:
        # Check relative parts manually to catch symlinks before resolve() hides them.
        rel_parts = candidate.relative_to(root).parts if candidate.is_absolute() else Path(raw_str).parts
    except ValueError:
        # Could still be absolute path pointing inside root_resolved; verify via resolved.
        rel_parts = None

    if rel_parts is not None:
        if any(part == ".." for part in rel_parts):
            return False, None, "PATH_TRAVERSAL_FORBIDDEN"
        probe = root
        for part in rel_parts:
            probe = probe / part
            if probe.exists() or probe.is_symlink():
                if probe.is_symlink() or _is_junction(probe):
                    return False, None, "PATH_ESCAPE_OR_SYMLINK_FORBIDDEN"
    else:
        if candidate.is_symlink() or _is_junction(candidate):
            return False, None, "PATH_ESCAPE_OR_SYMLINK_FORBIDDEN"

    try:
        cand_resolved = candidate.resolve()
        cand_resolved.relative_to(root_resolved)
    except (OSError, ValueError):
        return False, None, "PATH_ESCAPE_OR_SYMLINK_FORBIDDEN"

    # Also ensure candidate itself is not a dangling symlink.
    if candidate.is_symlink() or _is_junction(candidate):
        return False, None, "PATH_ESCAPE_OR_SYMLINK_FORBIDDEN"

    return True, cand_resolved, "OK"


def _append_audit(state: dict[str, Any], event_kind: str, detail: Mapping[str, Any], ts: str) -> None:
    log = state.setdefault("audit_log", [])
    entry = {"ts": ts, "event_kind": event_kind}
    entry.update(copy.deepcopy(dict(detail)))
    log.append(entry)
    if len(log) > MAX_AUDIT_LOG:
        del log[: len(log) - MAX_AUDIT_LOG]


def _check_idempotent_op(
    day_root: Path | str,
    state: Mapping[str, Any],
    operation_id: str,
    payload_hash: str,
) -> Optional[tuple[int, dict[str, Any]]]:
    if not operation_id:
        return 400, {"ok": False, "code": "OPERATION_ID_REQUIRED", "llm_calls": 0}
    status, existing = _tx.reconcile_evicted_or_active_op(
        day_root,
        state,
        operation_id,
        payload_hash=payload_hash,
    )
    if status == "conflict":
        return 409, {
            "ok": False,
            "code": "OPERATION_ID_PAYLOAD_CONFLICT",
            "operation_id": operation_id,
            "llm_calls": 0,
        }
    if status in ("hit_full", "hit_evicted") and isinstance(existing, Mapping):
        resp = copy.deepcopy(dict(existing.get("response") or existing))
        resp["idempotent_replay"] = True
        resp["llm_calls"] = 0
        return int(existing.get("http_status") or 200), resp
    return None


def _commit_state_and_op(
    day_root: Path | str,
    state: dict[str, Any],
    operation_id: str,
    payload_hash: str,
    http_status: int,
    response: dict[str, Any],
    ts: str,
) -> tuple[int, dict[str, Any]]:
    if state.get("_corrupt_on_disk") or state.get("_corrupt_fields"):
        return 503, {
            "ok": False,
            "code": "CORRUPT_GOVERNANCE_AUTHORITY_BLOCKED",
            "llm_calls": 0,
        }
    state["revision"] = int(state.get("revision") or 0) + 1
    _tx.stamp_receipt(
        state,
        operation_id,
        {"http_status": http_status, "response": copy.deepcopy(response), "ok": http_status < 400},
        revision=state["revision"],
        payload_hash=payload_hash,
        committed_at=ts,
    )
    _tx.trim_receipts_by_op_seq(state, max_keep=MAX_OPERATION_LEDGER)
    _tx.atomic_write_json(_state_path(day_root), state, sort_keys=True)
    out = copy.deepcopy(response)
    out["llm_calls"] = 0
    return http_status, out


def _verify_control_integrity_readonly(
    state: Mapping[str, Any],
    state_exists: bool,
    control: Mapping[str, Any],
    control_exists: bool,
) -> tuple[bool, str]:
    if (
        state.get("_corrupt_on_disk")
        or state.get("_corrupt_fields")
        or control.get("_corrupt_on_disk")
        or control.get("_corrupt_fields")
    ):
        return False, "CORRUPT_GOVERNANCE_AUTHORITY_BLOCKED"
    try:
        seen_del = int(state.get("last_control_deletion_seq") or 0)
        seen_rev = int(state.get("last_control_revocation_seq") or 0)
    except (TypeError, ValueError):
        return False, "CORRUPT_GOVERNANCE_AUTHORITY_BLOCKED"
    if (seen_del > 0 or seen_rev > 0) and not control_exists:
        return False, "CONTROL_AUTHORITY_MISSING"
    if state_exists and not control_exists and (state.get("forget_cases") or state.get("leases")):
        # If governance state already recorded control-governed objects without control ledger, fail closed.
        if seen_del > 0 or seen_rev > 0:
            return False, "CONTROL_AUTHORITY_MISSING"
    if control_exists:
        try:
            ctrl_del = int(control.get("deletion_highwater_seq") or 0)
            ctrl_rev = int(control.get("revocation_highwater_seq") or 0)
        except (TypeError, ValueError):
            return False, "CORRUPT_GOVERNANCE_AUTHORITY_BLOCKED"
        if seen_del > ctrl_del or seen_rev > ctrl_rev:
            # Control ledger rolled back behind business state!
            return False, "CONTROL_AUTHORITY_STALE_ROLLBACK"
    return True, "OK"


# =============================================================================
# 1. Managed Artifact Workspace .md Representation Binding (F-010..F-014)
# =============================================================================


def _mint_version_id(state: Mapping[str, Any], control: Mapping[str, Any]) -> str:
    retired = set(state.get("erased_version_ids") or ()) | set(control.get("erased_version_ids") or ())
    existing = set((state.get("versions") or {}).keys())
    while True:
        candidate = f"ver_{uuid.uuid4().hex[:16]}"
        if candidate not in retired and candidate not in existing:
            return candidate


def create_managed_artifact(
    day_root: Path | str,
    *,
    artifact_id: str,
    controller_principal_ref: str,
    subject_id: str,
    initial_body: str,
    operation_id: str,
    title: str = "",
    visibility: str = "private",
    writer_epoch: int = 1,
    origin_branch_id: str = "main",
    run_id: str = "run_1",
    meaning_version: str = "m.v1",
    explicit_version_id: Optional[str] = None,
    control_root: Path | str | None = None,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Create a new ManagedArtifact root with initial ACTION_COMMIT version."""
    ts = _now_iso(now_iso)
    body_text = str(initial_body)
    payload_hash = _tx.payload_fingerprint(
        {
            "op": "create_managed_artifact",
            "artifact_id": artifact_id,
            "controller": controller_principal_ref,
            "subject_id": subject_id,
            "body_sha": _sha256_text(body_text),
            "title": title,
            "visibility": visibility,
            "explicit_version_id": explicit_version_id,
        }
    )

    spath = _state_path(day_root)
    cpath = _control_ledger_path(day_root, control_root)
    with _tx.state_file_lock(spath), _tx.state_file_lock(cpath):
        state, _ = _load_state_readonly(day_root)
        control = _ensure_control_ledger_locked(day_root, control_root)

        replay = _check_idempotent_op(day_root, state, operation_id, payload_hash)
        if replay is not None:
            return replay

        if int(writer_epoch) < int(control.get("writer_fence_epoch") or 1):
            return 409, {"ok": False, "code": "STALE_WRITER_EPOCH", "llm_calls": 0}

        if artifact_id in state["artifacts"]:
            return 409, {"ok": False, "code": "ARTIFACT_ALREADY_EXISTS", "artifact_id": artifact_id, "llm_calls": 0}

        retired = set(state.get("erased_version_ids") or ()) | set(control.get("erased_version_ids") or ())
        if explicit_version_id:
            if explicit_version_id in retired or explicit_version_id in state["versions"]:
                return 409, {
                    "ok": False,
                    "code": "ERASED_VERSION_ID_REUSE_FORBIDDEN",
                    "version_id": explicit_version_id,
                    "llm_calls": 0,
                }
            version_id = explicit_version_id
        else:
            version_id = _mint_version_id(state, control)

        body_bytes = body_text.encode("utf-8")
        sha = _sha256_bytes(body_bytes)
        receipt_ref = f"crcpt:{uuid.uuid4().hex[:16]}"
        version_doc = {
            "version_id": version_id,
            "artifact_id": artifact_id,
            "parents": [],
            "content": {
                "blob_id": f"blob:{sha[:16]}",
                "sha256": sha,
                "byte_length": len(body_bytes),
                "media_type": "text/markdown",
                "encoding": "utf-8",
                "body": body_text,
            },
            "source": {
                "kind": SOURCE_ACTION_COMMIT,
                "action_ref": {"owner": "S10", "id": f"act:{operation_id}"},
                "source_evidence_refs": [],
                "creator_receipt_ref": {"owner": "S01", "id": receipt_ref},
            },
            "writer": {
                "subject_id": subject_id,
                "origin_branch_id": origin_branch_id,
                "run_id": run_id,
                "writer_epoch": int(writer_epoch),
            },
            "created_at": ts,
            "meaning_version": meaning_version,
            "payload_erased": False,
        }
        artifact_doc = {
            "artifact_id": artifact_id,
            "managed_or_external": "MANAGED",
            "controller_principal_ref": controller_principal_ref,
            "subject_id": subject_id,
            "title": title,
            "visibility": visibility,
            "current_head": version_id,
            "lifecycle": LIFECYCLE_ACTIVE,
            "content_health": CONTENT_AVAILABLE,
            "revision": 1,
            "meaning_version": meaning_version,
            "created_at": ts,
        }
        state["versions"][version_id] = version_doc
        state["artifacts"][artifact_id] = artifact_doc
        resp = {
            "ok": True,
            "code": "ARTIFACT_CREATED",
            "artifact_id": artifact_id,
            "version_id": version_id,
            "current_head": version_id,
            "revision": 1,
            "creator_receipt_ref": receipt_ref,
        }
        return _commit_state_and_op(day_root, state, operation_id, payload_hash, 200, resp, ts)


def commit_artifact_version(
    day_root: Path | str,
    *,
    artifact_id: str,
    expected_head: str,
    body: str,
    operation_id: str,
    action_ref_id: str = "act:commit",
    subject_id: str = "subject",
    writer_epoch: int = 1,
    origin_branch_id: str = "main",
    run_id: str = "run_1",
    explicit_version_id: Optional[str] = None,
    control_root: Path | str | None = None,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """CAS-commit a new canonical ArtifactVersion via ACTION_COMMIT without silently touching workspace .md."""
    ts = _now_iso(now_iso)
    body_text = str(body)
    payload_hash = _tx.payload_fingerprint(
        {
            "op": "commit_artifact_version",
            "artifact_id": artifact_id,
            "expected_head": expected_head,
            "body_sha": _sha256_text(body_text),
            "explicit_version_id": explicit_version_id,
        }
    )

    spath = _state_path(day_root)
    cpath = _control_ledger_path(day_root, control_root)
    with _tx.state_file_lock(spath), _tx.state_file_lock(cpath):
        state, _ = _load_state_readonly(day_root)
        control = _ensure_control_ledger_locked(day_root, control_root)

        replay = _check_idempotent_op(day_root, state, operation_id, payload_hash)
        if replay is not None:
            return replay

        if int(writer_epoch) < int(control.get("writer_fence_epoch") or 1):
            return 409, {"ok": False, "code": "STALE_WRITER_EPOCH", "llm_calls": 0}

        art = state["artifacts"].get(artifact_id)
        if art is None:
            return 404, {"ok": False, "code": "ARTIFACT_NOT_FOUND", "artifact_id": artifact_id, "llm_calls": 0}
        if art["lifecycle"] == LIFECYCLE_TOMBSTONED:
            return 403, {"ok": False, "code": "ARTIFACT_TOMBSTONED", "artifact_id": artifact_id, "llm_calls": 0}
        if art["lifecycle"] != LIFECYCLE_ACTIVE:
            return 409, {"ok": False, "code": "ARTIFACT_ARCHIVED_READONLY", "artifact_id": artifact_id, "llm_calls": 0}
        if art["current_head"] != expected_head:
            return 409, {
                "ok": False,
                "code": "HEAD_CONFLICT",
                "expected_head": expected_head,
                "current_head": art["current_head"],
                "llm_calls": 0,
            }

        retired = set(state.get("erased_version_ids") or ()) | set(control.get("erased_version_ids") or ())
        if explicit_version_id:
            if explicit_version_id in retired or explicit_version_id in state["versions"]:
                return 409, {
                    "ok": False,
                    "code": "ERASED_VERSION_ID_REUSE_FORBIDDEN",
                    "version_id": explicit_version_id,
                    "llm_calls": 0,
                }
            version_id = explicit_version_id
        else:
            version_id = _mint_version_id(state, control)

        body_bytes = body_text.encode("utf-8")
        sha = _sha256_bytes(body_bytes)
        receipt_ref = f"crcpt:{uuid.uuid4().hex[:16]}"
        state["versions"][version_id] = {
            "version_id": version_id,
            "artifact_id": artifact_id,
            "parents": [expected_head],
            "content": {
                "blob_id": f"blob:{sha[:16]}",
                "sha256": sha,
                "byte_length": len(body_bytes),
                "media_type": "text/markdown",
                "encoding": "utf-8",
                "body": body_text,
            },
            "source": {
                "kind": SOURCE_ACTION_COMMIT,
                "action_ref": {"owner": "S10", "id": action_ref_id},
                "source_evidence_refs": [],
                "creator_receipt_ref": {"owner": "S01", "id": receipt_ref},
            },
            "writer": {
                "subject_id": subject_id,
                "origin_branch_id": origin_branch_id,
                "run_id": run_id,
                "writer_epoch": int(writer_epoch),
            },
            "created_at": ts,
            "meaning_version": art["meaning_version"],
            "payload_erased": False,
        }
        art["current_head"] = version_id
        art["content_health"] = CONTENT_AVAILABLE
        art["revision"] = int(art["revision"]) + 1
        resp = {
            "ok": True,
            "code": "VERSION_COMMITTED",
            "artifact_id": artifact_id,
            "version_id": version_id,
            "current_head": version_id,
            "revision": art["revision"],
            "creator_receipt_ref": receipt_ref,
        }
        return _commit_state_and_op(day_root, state, operation_id, payload_hash, 200, resp, ts)


def change_artifact_lifecycle(
    day_root: Path | str,
    *,
    artifact_id: str,
    target_lifecycle: str,
    expected_revision: int,
    operation_id: str,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Transition ManagedArtifact lifecycle: ACTIVE <-> ARCHIVED -> TOMBSTONED or ACTIVE -> TOMBSTONED."""
    ts = _now_iso(now_iso)
    if target_lifecycle not in VALID_LIFECYCLES:
        return 400, {"ok": False, "code": "INVALID_LIFECYCLE", "llm_calls": 0}
    payload_hash = _tx.payload_fingerprint(
        {
            "op": "change_artifact_lifecycle",
            "artifact_id": artifact_id,
            "target_lifecycle": target_lifecycle,
            "expected_revision": int(expected_revision),
        }
    )
    spath = _state_path(day_root)
    with _tx.state_file_lock(spath):
        state, _ = _load_state_readonly(day_root)
        replay = _check_idempotent_op(day_root, state, operation_id, payload_hash)
        if replay is not None:
            return replay
        art = state["artifacts"].get(artifact_id)
        if art is None:
            return 404, {"ok": False, "code": "ARTIFACT_NOT_FOUND", "llm_calls": 0}
        if int(art["revision"]) != int(expected_revision):
            return 409, {"ok": False, "code": "REVISION_CONFLICT", "llm_calls": 0}
        if art["lifecycle"] == LIFECYCLE_TOMBSTONED:
            return 409, {"ok": False, "code": "TOMBSTONED_IS_TERMINAL", "llm_calls": 0}
        if target_lifecycle == LIFECYCLE_TOMBSTONED:
            mirror_res = _mirror_v02_forget_to_native_authority(
                day_root,
                target_ids=[artifact_id],
                case_id=f"art_tomb:{artifact_id}",
                operation_id=operation_id,
            )
            if mirror_res.get("fail_closed"):
                return 503, {
                    "ok": False,
                    "code": str(mirror_res.get("error_code") or "MEMORY_AUTHORITY_UNAVAILABLE"),
                    "llm_calls": 0,
                }
        art["lifecycle"] = target_lifecycle
        art["revision"] = int(art["revision"]) + 1
        resp = {
            "ok": True,
            "code": "LIFECYCLE_UPDATED",
            "artifact_id": artifact_id,
            "lifecycle": target_lifecycle,
            "revision": art["revision"],
        }
        return _commit_state_and_op(day_root, state, operation_id, payload_hash, 200, resp, ts)


def bind_representation(
    day_root: Path | str,
    *,
    artifact_id: str,
    workspace_root: Path | str,
    workspace_rel_path: str,
    operation_id: str,
    write_projection: bool = True,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Bind a ManagedArtifact to a workspace .md path and optionally project its current head."""
    ts = _now_iso(now_iso)
    safe_ok, resolved_path, path_code = _resolve_safe_workspace_path(workspace_root, workspace_rel_path)
    if not safe_ok or resolved_path is None:
        return 403, {"ok": False, "code": path_code, "llm_calls": 0}

    payload_hash = _tx.payload_fingerprint(
        {
            "op": "bind_representation",
            "artifact_id": artifact_id,
            "workspace_rel_path": workspace_rel_path,
            "write_projection": bool(write_projection),
        }
    )
    spath = _state_path(day_root)
    with _tx.state_file_lock(spath):
        state, _ = _load_state_readonly(day_root)
        replay = _check_idempotent_op(day_root, state, operation_id, payload_hash)
        if replay is not None:
            return replay

        art = state["artifacts"].get(artifact_id)
        if art is None:
            return 404, {"ok": False, "code": "ARTIFACT_NOT_FOUND", "llm_calls": 0}
        if art["lifecycle"] == LIFECYCLE_TOMBSTONED:
            return 403, {"ok": False, "code": "ARTIFACT_TOMBSTONED", "llm_calls": 0}

        head_id = art["current_head"]
        ver = state["versions"].get(head_id)
        if ver is None or ver.get("payload_erased") or art["content_health"] != CONTENT_AVAILABLE:
            return 409, {"ok": False, "code": "CANONICAL_CONTENT_UNAVAILABLE", "llm_calls": 0}

        body_bytes = str(ver["content"]["body"]).encode("utf-8")
        known_hash = _sha256_bytes(body_bytes)
        if write_projection:
            resolved_path.parent.mkdir(parents=True, exist_ok=True)
            resolved_path.write_bytes(body_bytes)
            health = REP_CURRENT
        else:
            if not resolved_path.exists():
                health = REP_MISSING
            else:
                disk_hash = _sha256_bytes(resolved_path.read_bytes())
                health = REP_CURRENT if disk_hash == known_hash else REP_DIVERGED

        state["representations"][artifact_id] = {
            "artifact_id": artifact_id,
            "workspace_root": str(Path(workspace_root).resolve()),
            "workspace_rel_path": workspace_rel_path,
            "bound_path": str(resolved_path),
            "known_projection_hash": known_hash,
            "generated_version": head_id,
            "health": health,
            "updated_at": ts,
        }
        resp = {
            "ok": True,
            "code": "REPRESENTATION_BOUND",
            "artifact_id": artifact_id,
            "representation_health": health,
            "known_projection_hash": known_hash,
            "generated_version": head_id,
        }
        return _commit_state_and_op(day_root, state, operation_id, payload_hash, 200, resp, ts)


def inspect_representation(
    day_root: Path | str,
    *,
    artifact_id: str,
    workspace_root: Path | str | None = None,
    has_subject_capability: bool = True,
) -> tuple[int, dict[str, Any]]:
    """Zero-write live inspection returning CURRENT / MISSING / DIVERGED / UNBOUND."""
    state, _ = _load_state_readonly(day_root)
    art = state["artifacts"].get(artifact_id)
    if art is None:
        return 404, {"ok": False, "code": "ARTIFACT_NOT_FOUND", "llm_calls": 0}

    if not has_subject_capability and art.get("visibility") != "shared":
        return 200, {
            "ok": True,
            "artifact_id": artifact_id,
            "visible": False,
            "reason": "PARTNER_PERSPECTIVE_REDACTED",
            "llm_calls": 0,
        }

    binding = state["representations"].get(artifact_id)
    if not isinstance(binding, Mapping):
        return 200, {
            "ok": True,
            "artifact_id": artifact_id,
            "lifecycle": art["lifecycle"],
            "content_health": art["content_health"],
            "representation_health": REP_UNBOUND,
            "current_head": art["current_head"],
            "known_projection_hash": None,
            "generated_version": None,
            "external_edit_detected": False,
            "llm_calls": 0,
        }

    ws_root = Path(workspace_root) if workspace_root is not None else Path(binding["workspace_root"])
    rel_path = str(binding.get("workspace_rel_path") or binding.get("bound_path") or "")
    safe_ok, resolved_path, path_code = _resolve_safe_workspace_path(ws_root, rel_path)
    if not safe_ok or resolved_path is None:
        return 200, {
            "ok": True,
            "artifact_id": artifact_id,
            "lifecycle": art["lifecycle"],
            "content_health": art["content_health"],
            "representation_health": REP_DIVERGED,
            "security_violation": path_code,
            "current_head": art["current_head"],
            "known_projection_hash": binding.get("known_projection_hash"),
            "generated_version": binding.get("generated_version"),
            "external_edit_detected": False,
            "llm_calls": 0,
        }

    if not resolved_path.exists():
        return 200, {
            "ok": True,
            "artifact_id": artifact_id,
            "lifecycle": art["lifecycle"],
            "content_health": art["content_health"],
            "representation_health": REP_MISSING,
            "current_head": art["current_head"],
            "known_projection_hash": binding.get("known_projection_hash"),
            "generated_version": binding.get("generated_version"),
            "external_edit_detected": False,
            "llm_calls": 0,
        }

    disk_sha = _sha256_bytes(resolved_path.read_bytes())
    known_hash = str(binding.get("known_projection_hash") or "")
    gen_ver = binding.get("generated_version")
    cur_head = art["current_head"]
    hash_changed = disk_sha != known_hash
    head_moved = gen_ver != cur_head
    rep_health = REP_CURRENT if (not hash_changed and not head_moved) else REP_DIVERGED

    return 200, {
        "ok": True,
        "artifact_id": artifact_id,
        "lifecycle": art["lifecycle"],
        "content_health": art["content_health"],
        "representation_health": rep_health,
        "current_head": cur_head,
        "known_projection_hash": known_hash,
        "generated_version": gen_ver,
        "observed_sha256": disk_sha,
        "external_edit_detected": hash_changed,
        "llm_calls": 0,
    }


def _record_external_observation_locked(
    state: dict[str, Any],
    *,
    artifact_id: str,
    binding: dict[str, Any],
    observed_sha256: str,
    actor_ref: str,
    trusted_actor_identity: bool,
    ts: str,
) -> dict[str, Any]:
    obs_id = f"obs_{uuid.uuid4().hex[:14]}"
    obs = {
        "observation_id": obs_id,
        "kind": "ExternalEditObservation",
        "artifact_id": artifact_id,
        "observed_path": binding["workspace_rel_path"],
        "observed_sha256": observed_sha256,
        "known_projection_hash": binding["known_projection_hash"],
        "base_projection_version": binding["generated_version"],
        "current_head_at_observation": state["artifacts"][artifact_id]["current_head"],
        "actor_ref": actor_ref,
        "trusted_actor_identity": bool(trusted_actor_identity),
        "attribution": "EXTERNAL_UNVERIFIED" if not trusted_actor_identity else "EXTERNAL_TRUSTED_ACTOR",
        "attributed_as_ta_action_commit": False,
        "observed_at": ts,
    }
    state["external_edit_observations"].append(obs)
    binding["health"] = REP_DIVERGED
    binding["pending_observation_id"] = obs_id
    binding["last_observed_sha256"] = observed_sha256
    return obs


def observe_external_edit(
    day_root: Path | str,
    *,
    artifact_id: str,
    workspace_root: Path | str,
    operation_id: str,
    actor_ref: str = "external_unverified",
    trusted_actor_identity: bool = False,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Observe an external modification to the bound .md file without attributing it as TA ACTION_COMMIT."""
    ts = _now_iso(now_iso)
    payload_hash = _tx.payload_fingerprint(
        {
            "op": "observe_external_edit",
            "artifact_id": artifact_id,
            "actor_ref": actor_ref,
            "trusted_actor_identity": bool(trusted_actor_identity),
        }
    )
    spath = _state_path(day_root)
    with _tx.state_file_lock(spath):
        state, _ = _load_state_readonly(day_root)
        replay = _check_idempotent_op(day_root, state, operation_id, payload_hash)
        if replay is not None:
            return replay

        art = state["artifacts"].get(artifact_id)
        binding = state["representations"].get(artifact_id)
        if art is None or binding is None:
            return 404, {"ok": False, "code": "REPRESENTATION_NOT_BOUND", "llm_calls": 0}

        safe_ok, resolved_path, path_code = _resolve_safe_workspace_path(
            workspace_root, str(binding["workspace_rel_path"])
        )
        if not safe_ok or resolved_path is None:
            return 403, {"ok": False, "code": path_code, "llm_calls": 0}
        if not resolved_path.exists():
            binding["health"] = REP_MISSING
            resp = {
                "ok": True,
                "code": "REPRESENTATION_MISSING",
                "representation_health": REP_MISSING,
            }
            return _commit_state_and_op(day_root, state, operation_id, payload_hash, 200, resp, ts)

        disk_sha = _sha256_bytes(resolved_path.read_bytes())
        if disk_sha == binding["known_projection_hash"] and binding["generated_version"] == art["current_head"]:
            binding["health"] = REP_CURRENT
            resp = {
                "ok": True,
                "code": "NO_EXTERNAL_EDIT",
                "representation_health": REP_CURRENT,
            }
            return _commit_state_and_op(day_root, state, operation_id, payload_hash, 200, resp, ts)

        obs = _record_external_observation_locked(
            state,
            artifact_id=artifact_id,
            binding=binding,
            observed_sha256=disk_sha,
            actor_ref=actor_ref,
            trusted_actor_identity=trusted_actor_identity,
            ts=ts,
        )
        resp = {
            "ok": True,
            "code": "EXTERNAL_EDIT_OBSERVED",
            "representation_health": REP_DIVERGED,
            "observation": obs,
        }
        return _commit_state_and_op(day_root, state, operation_id, payload_hash, 200, resp, ts)


def explicit_import_representation(
    day_root: Path | str,
    *,
    artifact_id: str,
    workspace_root: Path | str,
    base_projection_version: str,
    actor_ref: str,
    operation_id: str,
    trusted_actor_identity: bool = True,
    explicit_import_permission: bool = True,
    require_prior_observation: bool = True,
    explicit_version_id: Optional[str] = None,
    expected_observed_sha256: Optional[str] = None,
    control_root: Path | str | None = None,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Explicitly import an externally edited .md file as EXPLICIT_IMPORT; fails closed 409 BLOCKED_CONFLICT on head mismatch."""
    ts = _now_iso(now_iso)
    if not explicit_import_permission or not trusted_actor_identity:
        return 403, {
            "ok": False,
            "code": "EXPLICIT_IMPORT_PERMISSION_REQUIRED",
            "llm_calls": 0,
        }

    expected_obs_clean = (
        expected_observed_sha256.strip().lower()
        if isinstance(expected_observed_sha256, str) and expected_observed_sha256.strip()
        else None
    )
    payload_hash = _tx.payload_fingerprint(
        {
            "op": "explicit_import_representation",
            "artifact_id": artifact_id,
            "base_projection_version": base_projection_version,
            "actor_ref": actor_ref,
            "explicit_version_id": explicit_version_id,
            **(
                {"expected_observed_sha256": expected_obs_clean}
                if expected_obs_clean is not None
                else {}
            ),
        }
    )

    spath = _state_path(day_root)
    cpath = _control_ledger_path(day_root, control_root)
    with _tx.state_file_lock(spath), _tx.state_file_lock(cpath):
        state, _ = _load_state_readonly(day_root)
        control = _ensure_control_ledger_locked(day_root, control_root)

        replay = _check_idempotent_op(day_root, state, operation_id, payload_hash)
        if replay is not None:
            if replay[0] == 409 and operation_id:
                existing_op = (state.get("operations") or {}).get(operation_id)
                ex_resp = existing_op.get("response") if isinstance(existing_op, Mapping) else None
                if (
                    isinstance(ex_resp, Mapping)
                    and ex_resp.get("code") == "EXPLICIT_IMPORT_COMMITTED"
                    and ex_resp.get("artifact_id") == artifact_id
                    and base_projection_version == ex_resp.get("current_head")
                ):
                    ver_row = (state.get("versions") or {}).get(str(ex_resp.get("version_id") or ""))
                    ver_actor = str(((ver_row or {}).get("source") or {}).get("actor_ref") or "")
                    ver_sha = str(((ver_row or {}).get("content") or {}).get("sha256") or "").lower()
                    same_actor = (not ver_actor) or (ver_actor == actor_ref)
                    same_sha = (expected_obs_clean is None) or (ver_sha == expected_obs_clean)
                    same_exp_ver = (explicit_version_id is None) or (explicit_version_id == ex_resp.get("version_id"))
                    if same_actor and same_sha and same_exp_ver:
                        out_rep = copy.deepcopy(dict(ex_resp))
                        out_rep["idempotent_replay"] = True
                        out_rep["llm_calls"] = 0
                        return int(existing_op.get("http_status") or 200), out_rep
            return replay

        art = state["artifacts"].get(artifact_id)
        binding = state["representations"].get(artifact_id)
        if art is None or binding is None:
            return 404, {"ok": False, "code": "REPRESENTATION_NOT_BOUND", "llm_calls": 0}
        if art["lifecycle"] == LIFECYCLE_TOMBSTONED:
            return 403, {"ok": False, "code": "ARTIFACT_TOMBSTONED", "llm_calls": 0}

        bound_ws_raw = str(binding.get("workspace_root") or "").strip()
        if bound_ws_raw:
            try:
                if Path(workspace_root).resolve() != Path(bound_ws_raw).resolve():
                    return 403, {"ok": False, "code": "WORKSPACE_ROOT_MISMATCH", "llm_calls": 0}
            except OSError:
                return 403, {"ok": False, "code": "WORKSPACE_ROOT_MISMATCH", "llm_calls": 0}

        safe_ok, resolved_path, path_code = _resolve_safe_workspace_path(
            workspace_root, str(binding["workspace_rel_path"])
        )
        if not safe_ok or resolved_path is None:
            return 403, {"ok": False, "code": path_code, "llm_calls": 0}
        if not resolved_path.exists() or not resolved_path.is_file():
            return 409, {"ok": False, "code": "REPRESENTATION_MISSING_CANNOT_IMPORT", "llm_calls": 0}

        try:
            file_size = resolved_path.stat().st_size
        except OSError:
            return 409, {"ok": False, "code": "REPRESENTATION_MISSING_CANNOT_IMPORT", "llm_calls": 0}
        if file_size > 2 * 1024 * 1024:
            return 413, {"ok": False, "code": "IMPORT_FILE_TOO_LARGE", "llm_calls": 0}

        disk_bytes = resolved_path.read_bytes()
        try:
            body_text = disk_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return 422, {
                "ok": False,
                "code": "INVALID_UTF8_MARKDOWN",
                "reason": "Workspace file is not valid UTF-8 text",
                "llm_calls": 0,
            }
        disk_sha = _sha256_bytes(disk_bytes)
        if expected_obs_clean is not None and disk_sha != expected_obs_clean:
            return 409, {
                "ok": False,
                "code": "OBSERVED_CONTENT_CHANGED_SINCE_REVIEW",
                "expected_observed_sha256": expected_obs_clean,
                "actual_disk_sha256": disk_sha,
                "llm_calls": 0,
            }

        # Ensure an ExternalEditObservation exists for this external change before import.
        matching_obs = [
            obs
            for obs in state["external_edit_observations"]
            if obs.get("artifact_id") == artifact_id and obs.get("observed_sha256") == disk_sha
        ]
        if not matching_obs:
            if require_prior_observation:
                obs = _record_external_observation_locked(
                    state,
                    artifact_id=artifact_id,
                    binding=binding,
                    observed_sha256=disk_sha,
                    actor_ref=actor_ref,
                    trusted_actor_identity=trusted_actor_identity,
                    ts=ts,
                )
                matching_obs = [obs]
            else:
                return 409, {"ok": False, "code": "EXTERNAL_EDIT_OBSERVATION_REQUIRED", "llm_calls": 0}

        # Fail closed with 409 BLOCKED_CONFLICT if base_projection != current_head (zero silent three-way merge).
        if base_projection_version != art["current_head"]:
            conflict_resp = {
                "ok": False,
                "code": CODE_BLOCKED_CONFLICT,
                "reason": "BASE_PROJECTION_BEHIND_CURRENT_HEAD",
                "base_projection_version": base_projection_version,
                "current_head": art["current_head"],
                "silent_merge_attempted": False,
                "observation_id": matching_obs[-1]["observation_id"],
            }
            return _commit_state_and_op(day_root, state, operation_id, payload_hash, 409, conflict_resp, ts)

        retired = set(state.get("erased_version_ids") or ()) | set(control.get("erased_version_ids") or ())
        if explicit_version_id:
            if explicit_version_id in retired or explicit_version_id in state["versions"]:
                return 409, {
                    "ok": False,
                    "code": "ERASED_VERSION_ID_REUSE_FORBIDDEN",
                    "version_id": explicit_version_id,
                    "llm_calls": 0,
                }
            new_version_id = explicit_version_id
        else:
            new_version_id = _mint_version_id(state, control)

        receipt_ref = f"crcpt:{uuid.uuid4().hex[:16]}"
        state["versions"][new_version_id] = {
            "version_id": new_version_id,
            "artifact_id": artifact_id,
            "parents": [art["current_head"]],
            "content": {
                "blob_id": f"blob:{disk_sha[:16]}",
                "sha256": disk_sha,
                "byte_length": len(disk_bytes),
                "media_type": "text/markdown",
                "encoding": "utf-8",
                "body": body_text,
            },
            "source": {
                "kind": SOURCE_EXPLICIT_IMPORT,
                "actor_ref": actor_ref,
                "observation_id": matching_obs[-1]["observation_id"],
                "action_ref": {"owner": "S01", "id": f"import:{operation_id}"},
                "source_evidence_refs": [{"owner": "S01", "id": matching_obs[-1]["observation_id"]}],
                "creator_receipt_ref": {"owner": "S01", "id": receipt_ref},
            },
            "writer": {
                "subject_id": art["subject_id"],
                "origin_branch_id": "external_import",
                "run_id": operation_id,
                "writer_epoch": int(control.get("writer_fence_epoch") or 1),
            },
            "created_at": ts,
            "meaning_version": art["meaning_version"],
            "payload_erased": False,
        }
        art["current_head"] = new_version_id
        art["content_health"] = CONTENT_AVAILABLE
        art["revision"] = int(art["revision"]) + 1
        binding["known_projection_hash"] = disk_sha
        binding["generated_version"] = new_version_id
        binding["health"] = REP_CURRENT
        binding["updated_at"] = ts
        resp = {
            "ok": True,
            "code": "EXPLICIT_IMPORT_COMMITTED",
            "source_kind": SOURCE_EXPLICIT_IMPORT,
            "artifact_id": artifact_id,
            "version_id": new_version_id,
            "current_head": new_version_id,
            "revision": art["revision"],
            "representation_health": REP_CURRENT,
            "observation_id": matching_obs[-1]["observation_id"],
        }
        return _commit_state_and_op(day_root, state, operation_id, payload_hash, 200, resp, ts)


def rebuild_projection_from_canonical(
    day_root: Path | str,
    *,
    artifact_id: str,
    workspace_root: Path | str,
    operation_id: str,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Rebuild a MISSING workspace .md projection from canonical head; refuses to clobber DIVERGED external edits."""
    ts = _now_iso(now_iso)
    payload_hash = _tx.payload_fingerprint({"op": "rebuild_projection", "artifact_id": artifact_id})
    spath = _state_path(day_root)
    with _tx.state_file_lock(spath):
        state, _ = _load_state_readonly(day_root)
        replay = _check_idempotent_op(day_root, state, operation_id, payload_hash)
        if replay is not None:
            return replay

        art = state["artifacts"].get(artifact_id)
        binding = state["representations"].get(artifact_id)
        if art is None or binding is None:
            return 404, {"ok": False, "code": "REPRESENTATION_NOT_BOUND", "llm_calls": 0}
        if art["lifecycle"] == LIFECYCLE_TOMBSTONED or art["content_health"] != CONTENT_AVAILABLE:
            return 409, {"ok": False, "code": "CANONICAL_CONTENT_UNAVAILABLE", "llm_calls": 0}

        safe_ok, resolved_path, path_code = _resolve_safe_workspace_path(
            workspace_root, str(binding["workspace_rel_path"])
        )
        if not safe_ok or resolved_path is None:
            return 403, {"ok": False, "code": path_code, "llm_calls": 0}

        if resolved_path.exists():
            disk_sha = _sha256_bytes(resolved_path.read_bytes())
            if disk_sha != binding["known_projection_hash"]:
                return 409, {
                    "ok": False,
                    "code": CODE_BLOCKED_CONFLICT,
                    "reason": "WILL_NOT_OVERWRITE_EXTERNAL_DIVERGENCE",
                    "llm_calls": 0,
                }

        head_ver = state["versions"].get(art["current_head"])
        if head_ver is None or head_ver.get("payload_erased"):
            return 409, {"ok": False, "code": "CANONICAL_CONTENT_UNAVAILABLE", "llm_calls": 0}

        body_bytes = str(head_ver["content"]["body"]).encode("utf-8")
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        resolved_path.write_bytes(body_bytes)
        new_sha = _sha256_bytes(body_bytes)
        binding["known_projection_hash"] = new_sha
        binding["generated_version"] = art["current_head"]
        binding["health"] = REP_CURRENT
        binding["updated_at"] = ts
        resp = {
            "ok": True,
            "code": "PROJECTION_REBUILT",
            "artifact_id": artifact_id,
            "representation_health": REP_CURRENT,
            "generated_version": art["current_head"],
        }
        return _commit_state_and_op(day_root, state, operation_id, payload_hash, 200, resp, ts)


def erase_version_payload_privacy(
    day_root: Path | str,
    *,
    artifact_id: str,
    version_id: str,
    operation_id: str,
    sanitize_cover_title: bool = True,
    replacement_title: str = "[REDACTED_SHELL]",
    control_root: Path | str | None = None,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Privacy-erase a version's payload while keeping non-sensitive identity/shell and retiring its version_id forever."""
    ts = _now_iso(now_iso)
    payload_hash = _tx.payload_fingerprint(
        {
            "op": "erase_version_payload_privacy",
            "artifact_id": artifact_id,
            "version_id": version_id,
            "sanitize_cover_title": bool(sanitize_cover_title),
        }
    )
    spath = _state_path(day_root)
    cpath = _control_ledger_path(day_root, control_root)
    with _tx.state_file_lock(spath), _tx.state_file_lock(cpath):
        state, _ = _load_state_readonly(day_root)
        control = _ensure_control_ledger_locked(day_root, control_root)
        replay = _check_idempotent_op(day_root, state, operation_id, payload_hash)
        if replay is not None:
            return replay

        art = state["artifacts"].get(artifact_id)
        ver = state["versions"].get(version_id)
        if art is None or ver is None or ver["artifact_id"] != artifact_id:
            return 404, {"ok": False, "code": "VERSION_NOT_FOUND", "llm_calls": 0}

        ver["payload_erased"] = True
        ver["erased_at"] = ts
        ver["content"]["body"] = ""
        ver["content"]["sha256"] = "ERASED"
        ver["content"]["blob_id"] = "blob:ERASED"
        ver["content"]["byte_length"] = 0

        if art["current_head"] == version_id:
            art["content_health"] = CONTENT_UNAVAILABLE
        if sanitize_cover_title and art.get("title"):
            art["title"] = replacement_title

        if version_id not in state["erased_version_ids"]:
            state["erased_version_ids"].append(version_id)
        if version_id not in control["erased_version_ids"]:
            control["erased_version_ids"].append(version_id)
        control["deletion_highwater_seq"] = int(control.get("deletion_highwater_seq") or 0) + 1
        state["last_control_deletion_seq"] = control["deletion_highwater_seq"]
        _tx.atomic_write_json(cpath, control, sort_keys=True)

        resp = {
            "ok": True,
            "code": "VERSION_PAYLOAD_ERASED_SHELL_RETAINED",
            "artifact_id": artifact_id,
            "version_id": version_id,
            "identity_retained": True,
            "payload_erased": True,
            "retired_version_ids_count": len(state["erased_version_ids"]),
            "title": art.get("title", ""),
        }
        return _commit_state_and_op(day_root, state, operation_id, payload_hash, 200, resp, ts)


# =============================================================================
# 2. Revocable Audience PermissionLease + CombinedVeto + Ch.14 Scenario 8
# =============================================================================


def _is_verified_grantor(grantor: str) -> bool:
    if not isinstance(grantor, str) or not grantor.strip():
        return False
    lowered = grantor.strip().lower()
    if lowered.startswith("user_text:") or lowered.startswith("unverified:"):
        return False
    return True


def issue_permission_lease(
    day_root: Path | str,
    *,
    grant_id: str,
    grantor: str,
    subject: str,
    capability: str,
    scope: str,
    typed_target: Mapping[str, Any],
    audience: str,
    operation_id: str,
    authority_scope: str = "life-supply.disclosure.v1",
    max_uses: int = 1,
    payload_binding: Optional[str] = None,
    valid_from: Optional[str] = None,
    expires_at: Optional[str] = None,
    delegation_allowed: bool = False,
    evidence_ref: str = "evid:grant",
    parent_chain: Sequence[str] = (),
    meaning_version: str = "m.v1",
    workspace_root: Path | str | None = None,
    control_root: Path | str | None = None,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Issue a versioned, audience-bound, revocable PermissionLease."""
    ts = _now_iso(now_iso)
    if not _is_verified_grantor(grantor):
        return 403, {"ok": False, "code": "UNVERIFIED_GRANTOR_REFUSED", "llm_calls": 0}
    if int(max_uses) < 1:
        return 400, {"ok": False, "code": "INVALID_MAX_USES", "llm_calls": 0}
    if not isinstance(typed_target, Mapping) or not typed_target.get("kind") or not typed_target.get("id"):
        return 400, {"ok": False, "code": "TYPED_TARGET_REQUIRED", "llm_calls": 0}
    if int(max_uses) == 1 and capability in ("DISCLOSE_MANAGED_ARTIFACT", "OUTBOUND_SEND") and not payload_binding:
        return 400, {"ok": False, "code": "ONE_SHOT_PAYLOAD_BINDING_REQUIRED", "llm_calls": 0}
    if typed_target.get("path"):
        ws = workspace_root if workspace_root is not None else day_root
        ok_p, _, code_p = _resolve_safe_workspace_path(ws, str(typed_target["path"]))
        if not ok_p:
            return 403, {"ok": False, "code": code_p, "llm_calls": 0}

    payload_hash = _tx.payload_fingerprint(
        {
            "op": "issue_permission_lease",
            "grant_id": grant_id,
            "grantor": grantor,
            "subject": subject,
            "capability": capability,
            "scope": scope,
            "typed_target": dict(typed_target),
            "audience": audience,
            "max_uses": int(max_uses),
            "payload_binding": payload_binding,
        }
    )

    spath = _state_path(day_root)
    cpath = _control_ledger_path(day_root, control_root)
    with _tx.state_file_lock(spath), _tx.state_file_lock(cpath):
        state, _ = _load_state_readonly(day_root)
        _ensure_control_ledger_locked(day_root, control_root)
        replay = _check_idempotent_op(day_root, state, operation_id, payload_hash)
        if replay is not None:
            return replay
        if grant_id in state["leases"]:
            return 409, {"ok": False, "code": "GRANT_ALREADY_EXISTS", "grant_id": grant_id, "llm_calls": 0}

        lease = {
            "grant_id": grant_id,
            "grantor": grantor,
            "subject": subject,
            "capability": capability,
            "scope": scope,
            "typed_target": copy.deepcopy(dict(typed_target)),
            "payload_binding": payload_binding,
            "valid_from": valid_from or ts,
            "expires_at": expires_at,
            "revision": 1,
            "revocation": {
                "revoked": False,
                "revoked_at": None,
                "revocation_revision": 0,
                "reason": "",
            },
            "use_count": 0,
            "max_uses": int(max_uses),
            "delegation": {"allowed": bool(delegation_allowed)},
            "audience": audience,
            "authority_scope": authority_scope,
            "meaning_version": meaning_version,
            "evidence_ref": evidence_ref,
            "parent_chain": list(parent_chain),
            "reservations": {},
            "issued_at": ts,
        }
        state["leases"][grant_id] = lease
        resp = {
            "ok": True,
            "code": "LEASE_ISSUED",
            "grant_id": grant_id,
            "revision": 1,
            "max_uses": int(max_uses),
        }
        return _commit_state_and_op(day_root, state, operation_id, payload_hash, 200, resp, ts)


def delegate_permission_lease(
    day_root: Path | str,
    *,
    parent_grant_id: str,
    new_grant_id: str,
    actor_ref: str,
    operation_id: str,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Attempt lease delegation; Stage 1 forbids delegation by default."""
    ts = _now_iso(now_iso)
    state, _ = _load_state_readonly(day_root)
    parent = state["leases"].get(parent_grant_id)
    if parent is None:
        return 404, {"ok": False, "code": "LEASE_NOT_FOUND", "llm_calls": 0}
    if not bool((parent.get("delegation") or {}).get("allowed")):
        return 403, {"ok": False, "code": "DELEGATION_FORBIDDEN", "parent_grant_id": parent_grant_id, "llm_calls": 0}
    return issue_permission_lease(
        day_root,
        grant_id=new_grant_id,
        grantor=actor_ref,
        subject=parent["subject"],
        capability=parent["capability"],
        scope=parent["scope"],
        typed_target=parent["typed_target"],
        audience=parent["audience"],
        operation_id=operation_id,
        max_uses=1,
        payload_binding=parent.get("payload_binding"),
        delegation_allowed=False,
        parent_chain=list(parent.get("parent_chain") or []) + [parent_grant_id],
        now_iso=ts,
    )


def revoke_permission_lease(
    day_root: Path | str,
    *,
    grant_id: str,
    actor_ref: str,
    operation_id: str,
    reason: str = "operator_revoked",
    control_root: Path | str | None = None,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Revoke a PermissionLease and bump the external control revocation high-water sequence."""
    ts = _now_iso(now_iso)
    payload_hash = _tx.payload_fingerprint(
        {"op": "revoke_permission_lease", "grant_id": grant_id, "actor_ref": actor_ref, "reason": reason}
    )
    spath = _state_path(day_root)
    cpath = _control_ledger_path(day_root, control_root)
    with _tx.state_file_lock(spath), _tx.state_file_lock(cpath):
        state, _ = _load_state_readonly(day_root)
        control = _ensure_control_ledger_locked(day_root, control_root)
        replay = _check_idempotent_op(day_root, state, operation_id, payload_hash)
        if replay is not None:
            return replay

        lease = state["leases"].get(grant_id)
        if lease is None:
            return 404, {"ok": False, "code": "LEASE_NOT_FOUND", "grant_id": grant_id, "llm_calls": 0}

        new_rev = int(lease["revision"]) + 1
        lease["revision"] = new_rev
        lease["revocation"] = {
            "revoked": True,
            "revoked_at": ts,
            "revocation_revision": new_rev,
            "reason": reason,
            "revoked_by": actor_ref,
        }

        ctrl_seq = int(control.get("revocation_highwater_seq") or 0) + 1
        control["revocation_highwater_seq"] = ctrl_seq
        control["revoked_leases"][grant_id] = {
            "revocation_revision": new_rev,
            "revoked_at": ts,
            "revocation_seq": ctrl_seq,
        }
        state["last_control_revocation_seq"] = ctrl_seq
        _tx.atomic_write_json(cpath, control, sort_keys=True)

        resp = {
            "ok": True,
            "code": "LEASE_REVOKED",
            "grant_id": grant_id,
            "revision": new_rev,
            "revocation_seq": ctrl_seq,
        }
        return _commit_state_and_op(day_root, state, operation_id, payload_hash, 200, resp, ts)


def _active_reserved_count(lease: Mapping[str, Any], exclude_action_id: Optional[str] = None) -> int:
    count = 0
    for act_id, res in (lease.get("reservations") or {}).items():
        if exclude_action_id and act_id == exclude_action_id:
            continue
        if isinstance(res, Mapping) and res.get("status") in (RES_RESERVED, RES_UNKNOWN_HELD):
            count += 1
    return count


def _source_tombstoned_or_held(
    state: Mapping[str, Any],
    control: Mapping[str, Any],
    source_id: str,
) -> tuple[bool, str]:
    src = (state.get("sources") or {}).get(source_id)
    if isinstance(src, Mapping):
        if src.get("status") == LIFECYCLE_TOMBSTONED:
            return True, "SOURCE_TOMBSTONED"
        if src.get("hold_active"):
            return True, CASE_HOLD_PENDING
    hmac_key = control.get("hmac_key_hex")
    if isinstance(hmac_key, str) and len(hmac_key) == 64:
        digest = _hmac_tombstone_digest(hmac_key, "source", source_id)
        if digest in (control.get("tombstones") or {}):
            return True, "SOURCE_TOMBSTONED_BY_CONTROL_BARRIER"
    return False, "OK"


def _evaluate_veto_on_loaded(
    state: Mapping[str, Any],
    state_exists: bool,
    control: Mapping[str, Any],
    control_exists: bool,
    *,
    grant_id: str,
    subject: str,
    capability: str,
    target_id: str,
    audience: str,
    payload_hash: str,
    logical_action_id: Optional[str] = None,
    privacy_state: Optional[Mapping[str, Any]] = None,
    boundary_state: Optional[Mapping[str, Any]] = None,
    timing_state: Optional[Mapping[str, Any]] = None,
    budget_state: Optional[Mapping[str, Any]] = None,
    writer_epoch: int = 1,
    source_id: Optional[str] = None,
    expected_source_revision: Optional[int] = None,
    target_path: Optional[str] = None,
    workspace_root: Path | str | None = None,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    now_dt = _parse_dt(_now_iso(now_iso)) or datetime.now(DEFAULT_TZ)
    ctrl_ok, ctrl_code = _verify_control_integrity_readonly(state, state_exists, control, control_exists)

    # 1) Grant check
    lease = (state.get("leases") or {}).get(grant_id)
    ctrl_revoked = grant_id in (control.get("revoked_leases") or {})
    grant_res = {"allowed": True, "owner_ref": "S14", "revision": 0, "reason_code": "OK"}
    if lease is None:
        grant_res = {"allowed": False, "owner_ref": "S14", "revision": 0, "reason_code": "LEASE_NOT_FOUND"}
    else:
        grant_res["revision"] = int(lease.get("revision") or 0)
        rev_info = lease.get("revocation") or {}
        if rev_info.get("revoked") or ctrl_revoked:
            grant_res = {
                "allowed": False,
                "owner_ref": "S14",
                "revision": grant_res["revision"],
                "reason_code": CODE_LEASE_REVOKED_BEFORE_DISPATCH,
            }
        elif lease.get("subject") != subject:
            grant_res = {
                "allowed": False,
                "owner_ref": "S14",
                "revision": grant_res["revision"],
                "reason_code": "SUBJECT_MISMATCH",
            }
        elif lease.get("capability") != capability:
            grant_res = {
                "allowed": False,
                "owner_ref": "S14",
                "revision": grant_res["revision"],
                "reason_code": "CAPABILITY_MISMATCH",
            }
        elif lease.get("audience") != audience:
            grant_res = {
                "allowed": False,
                "owner_ref": "S14",
                "revision": grant_res["revision"],
                "reason_code": "AUDIENCE_MISMATCH",
            }
        elif (lease.get("typed_target") or {}).get("id") != target_id:
            grant_res = {
                "allowed": False,
                "owner_ref": "S14",
                "revision": grant_res["revision"],
                "reason_code": "TARGET_ID_MISMATCH",
            }
        elif lease.get("payload_binding") is not None and lease.get("payload_binding") != payload_hash:
            grant_res = {
                "allowed": False,
                "owner_ref": "S14",
                "revision": grant_res["revision"],
                "reason_code": "PAYLOAD_BINDING_MISMATCH",
            }
        else:
            exp_dt = _parse_dt(lease.get("expires_at"))
            vf_dt = _parse_dt(lease.get("valid_from"))
            if vf_dt is not None and now_dt < vf_dt:
                grant_res = {
                    "allowed": False,
                    "owner_ref": "S14",
                    "revision": grant_res["revision"],
                    "reason_code": "LEASE_NOT_YET_VALID",
                }
            elif exp_dt is not None and now_dt >= exp_dt:
                grant_res = {
                    "allowed": False,
                    "owner_ref": "S14",
                    "revision": grant_res["revision"],
                    "reason_code": "LEASE_EXPIRED",
                }
            elif target_path is not None and workspace_root is not None:
                ok_p, _, code_p = _resolve_safe_workspace_path(workspace_root, target_path)
                if not ok_p:
                    grant_res = {
                        "allowed": False,
                        "owner_ref": "S14",
                        "revision": grant_res["revision"],
                        "reason_code": code_p,
                    }

        # Check one-shot / bounded max_uses reservation if still allowed
        if grant_res["allowed"]:
            reservations = lease.get("reservations") or {}
            existing_res = reservations.get(logical_action_id) if logical_action_id else None
            if isinstance(existing_res, Mapping) and existing_res.get("status") in (RES_RESERVED, RES_UNKNOWN_HELD):
                if existing_res.get("payload_hash") != payload_hash:
                    grant_res = {
                        "allowed": False,
                        "owner_ref": "S14",
                        "revision": grant_res["revision"],
                        "reason_code": "PAYLOAD_BINDING_MISMATCH",
                    }
            else:
                used = int(lease.get("use_count") or 0) + _active_reserved_count(
                    lease, exclude_action_id=logical_action_id
                )
                if used >= int(lease.get("max_uses") or 1):
                    grant_res = {
                        "allowed": False,
                        "owner_ref": "S14",
                        "revision": grant_res["revision"],
                        "reason_code": "LEASE_EXHAUSTED",
                    }

    # 2) Privacy check (intimacy NEVER offsets deny; also checks source hold/tombstone/revision)
    p_in = dict(privacy_state or {})
    priv_res = {
        "allowed": bool(p_in.get("allowed", True)),
        "owner_ref": str(p_in.get("owner_ref", "S14")),
        "revision": int(p_in.get("revision", 1)),
        "reason_code": str(p_in.get("reason_code", "OK" if p_in.get("allowed", True) else "PRIVACY_DENIED")),
    }
    current_source_revision: Optional[int] = None
    if source_id:
        blocked_src, blk_reason = _source_tombstoned_or_held(state, control, source_id)
        src_doc = (state.get("sources") or {}).get(source_id)
        if isinstance(src_doc, Mapping):
            current_source_revision = int(src_doc.get("revision") or 0)
        if blocked_src:
            priv_res = {
                "allowed": False,
                "owner_ref": "S14",
                "revision": current_source_revision or 0,
                "reason_code": blk_reason,
            }
        elif expected_source_revision is not None and current_source_revision != int(expected_source_revision):
            priv_res = {
                "allowed": False,
                "owner_ref": "S05",
                "revision": current_source_revision or 0,
                "reason_code": CODE_SOURCE_REVISION_CHANGED,
            }
    if priv_res["allowed"] and target_id in (state.get("artifacts") or {}):
        art_doc = state["artifacts"][target_id]
        if art_doc.get("lifecycle") == LIFECYCLE_TOMBSTONED:
            priv_res = {
                "allowed": False,
                "owner_ref": "S01",
                "revision": int(art_doc.get("revision") or 0),
                "reason_code": "ARTIFACT_TOMBSTONED",
            }

    # 3) Interaction boundary check (S11; closeness/intimacy never overrides hard deny)
    b_in = dict(boundary_state or {})
    bound_res = {
        "allowed": bool(b_in.get("allowed", True)),
        "owner_ref": str(b_in.get("owner_ref", "S11")),
        "revision": int(b_in.get("revision", 1)),
        "reason_code": str(
            b_in.get("reason_code", "OK" if b_in.get("allowed", True) else "INTERACTION_BOUNDARY_DENIED")
        ),
    }

    # 4) Timing window check (DND/quiet hours never bypassed by long-term grant unless urgent capability)
    t_in = dict(timing_state or {})
    in_quiet = bool(t_in.get("in_quiet_hours", False))
    urgent_cap = bool(t_in.get("urgent_authorized", False))
    timing_allowed = bool(t_in.get("allowed", True)) and (not in_quiet or urgent_cap)
    timing_res = {
        "allowed": timing_allowed,
        "owner_ref": str(t_in.get("owner_ref", "RuntimeClock")),
        "revision": int(t_in.get("revision", 1)),
        "reason_code": "OK" if timing_allowed else str(t_in.get("reason_code", "DEFERRED_TIMING_WINDOW")),
    }

    # 5) Budget check
    bg_in = dict(budget_state or {})
    remaining = float(bg_in.get("remaining", 100.0))
    required = float(bg_in.get("required_cost", 0.0))
    budget_allowed = bool(bg_in.get("allowed", True)) and (remaining >= required)
    budget_res = {
        "allowed": budget_allowed,
        "owner_ref": str(bg_in.get("owner_ref", "BudgetGate")),
        "revision": int(bg_in.get("revision", 1)),
        "reason_code": "OK" if budget_allowed else str(bg_in.get("reason_code", "BUDGET_INSUFFICIENT")),
    }

    # 6) Writer fence check
    ctrl_fence = int(control.get("writer_fence_epoch") or 1)
    writer_allowed = ctrl_ok and (int(writer_epoch) >= ctrl_fence)
    writer_res = {
        "allowed": writer_allowed,
        "owner_ref": "ControlAuthority",
        "revision": ctrl_fence,
        "reason_code": "OK" if writer_allowed else (ctrl_code if not ctrl_ok else "STALE_WRITER_EPOCH"),
    }

    components = {
        "grant": grant_res,
        "privacy": priv_res,
        "interaction_boundary": bound_res,
        "timing": timing_res,
        "budget": budget_res,
        "writer_fence": writer_res,
    }
    all_allowed = all(item["allowed"] for item in components.values())

    if all_allowed:
        http_status, code = 200, "COMBINED_VETO_PASSED"
    elif not grant_res["allowed"]:
        if grant_res["reason_code"] in (
            CODE_LEASE_REVOKED_BEFORE_DISPATCH,
            "SUBJECT_MISMATCH",
            "CAPABILITY_MISMATCH",
            "AUDIENCE_MISMATCH",
            "TARGET_ID_MISMATCH",
            "LEASE_EXPIRED",
            "LEASE_NOT_YET_VALID",
            "PATH_ESCAPE_OR_SYMLINK_FORBIDDEN",
        ):
            http_status = 403
        elif grant_res["reason_code"] == "LEASE_NOT_FOUND":
            http_status = 404
        else:
            http_status = 409
        code = grant_res["reason_code"]
    elif not priv_res["allowed"]:
        if priv_res["reason_code"] == CODE_SOURCE_REVISION_CHANGED:
            http_status = 409
        else:
            http_status = 403
        code = priv_res["reason_code"]
    elif not bound_res["allowed"]:
        http_status, code = 403, bound_res["reason_code"]
    elif not timing_res["allowed"]:
        http_status, code = 409, timing_res["reason_code"]
    elif not budget_res["allowed"]:
        http_status, code = 402, budget_res["reason_code"]
    else:
        http_status, code = 409, writer_res["reason_code"]

    return http_status, {
        "ok": all_allowed,
        "allowed": all_allowed,
        "code": code,
        "grant_id": grant_id,
        "lease_revision": grant_res["revision"],
        "source_revision": current_source_revision,
        "veto_components": components,
        "llm_calls": 0,
    }


def evaluate_combined_veto(
    day_root: Path | str,
    *,
    grant_id: str,
    subject: str,
    capability: str,
    target_id: str,
    audience: str,
    payload_hash: str,
    logical_action_id: Optional[str] = None,
    privacy_state: Optional[Mapping[str, Any]] = None,
    boundary_state: Optional[Mapping[str, Any]] = None,
    timing_state: Optional[Mapping[str, Any]] = None,
    budget_state: Optional[Mapping[str, Any]] = None,
    writer_epoch: int = 1,
    source_id: Optional[str] = None,
    expected_source_revision: Optional[int] = None,
    target_path: Optional[str] = None,
    workspace_root: Path | str | None = None,
    serialized_context: Optional[Mapping[str, Any]] = None,
    control_root: Path | str | None = None,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Zero-write CombinedVeto check; ignores any client-supplied pre-verified flags (P09)."""
    del serialized_context  # P09: never trust serialized verified=True flags without owner re-check
    state, state_exists = _load_state_readonly(day_root)
    control, control_exists = _load_control_readonly(day_root, control_root)
    return _evaluate_veto_on_loaded(
        state,
        state_exists,
        control,
        control_exists,
        grant_id=grant_id,
        subject=subject,
        capability=capability,
        target_id=target_id,
        audience=audience,
        payload_hash=payload_hash,
        logical_action_id=logical_action_id,
        privacy_state=privacy_state,
        boundary_state=boundary_state,
        timing_state=timing_state,
        budget_state=budget_state,
        writer_epoch=writer_epoch,
        source_id=source_id,
        expected_source_revision=expected_source_revision,
        target_path=target_path,
        workspace_root=workspace_root,
        now_iso=now_iso,
    )


def read_lease_t1(
    day_root: Path | str,
    *,
    grant_id: str,
    subject: str,
    capability: str,
    target_id: str,
    audience: str,
    payload_hash: str,
    source_id: Optional[str] = None,
    control_root: Path | str | None = None,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """T1 zero-write read of lease and source revision before dispatch."""
    return evaluate_combined_veto(
        day_root,
        grant_id=grant_id,
        subject=subject,
        capability=capability,
        target_id=target_id,
        audience=audience,
        payload_hash=payload_hash,
        source_id=source_id,
        control_root=control_root,
        now_iso=now_iso,
    )


def reserve_dispatch_td(
    day_root: Path | str,
    *,
    grant_id: str,
    logical_action_id: str,
    payload_hash: str,
    subject: str,
    capability: str,
    target_id: str,
    audience: str,
    operation_id: str,
    source_id: Optional[str] = None,
    expected_source_revision: Optional[int] = None,
    privacy_state: Optional[Mapping[str, Any]] = None,
    boundary_state: Optional[Mapping[str, Any]] = None,
    timing_state: Optional[Mapping[str, Any]] = None,
    budget_state: Optional[Mapping[str, Any]] = None,
    writer_epoch: int = 1,
    control_root: Path | str | None = None,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """TD atomic reservation: same logical_action_id+payload reuses reservation; competing action is rejected."""
    ts = _now_iso(now_iso)
    op_hash = _tx.payload_fingerprint(
        {
            "op": "reserve_dispatch_td",
            "grant_id": grant_id,
            "logical_action_id": logical_action_id,
            "payload_hash": payload_hash,
            "target_id": target_id,
        }
    )
    spath = _state_path(day_root)
    cpath = _control_ledger_path(day_root, control_root)
    with _tx.state_file_lock(spath), _tx.state_file_lock(cpath):
        state, state_exists = _load_state_readonly(day_root)
        control, control_exists = _load_control_readonly(day_root, control_root)
        if not control_exists:
            control = _ensure_control_ledger_locked(day_root, control_root)
            control_exists = True

        replay = _check_idempotent_op(day_root, state, operation_id, op_hash)
        if replay is not None:
            return replay

        status, veto_res = _evaluate_veto_on_loaded(
            state,
            state_exists,
            control,
            control_exists,
            grant_id=grant_id,
            subject=subject,
            capability=capability,
            target_id=target_id,
            audience=audience,
            payload_hash=payload_hash,
            logical_action_id=logical_action_id,
            privacy_state=privacy_state,
            boundary_state=boundary_state,
            timing_state=timing_state,
            budget_state=budget_state,
            writer_epoch=writer_epoch,
            source_id=source_id,
            expected_source_revision=expected_source_revision,
            now_iso=ts,
        )
        if status != 200:
            return status, veto_res

        lease = state["leases"][grant_id]
        reservations = lease.setdefault("reservations", {})
        existing = reservations.get(logical_action_id)
        if isinstance(existing, dict) and existing.get("status") in (RES_RESERVED, RES_UNKNOWN_HELD):
            resp = {
                "ok": True,
                "code": "RESERVATION_REUSED",
                "grant_id": grant_id,
                "logical_action_id": logical_action_id,
                "reservation_status": existing["status"],
                "lease_revision": lease["revision"],
                "source_revision": veto_res["source_revision"],
            }
            return _commit_state_and_op(day_root, state, operation_id, op_hash, 200, resp, ts)

        reservations[logical_action_id] = {
            "logical_action_id": logical_action_id,
            "payload_hash": payload_hash,
            "status": RES_RESERVED,
            "admitted_at": ts,
            "lease_revision_at_td": lease["revision"],
            "source_revision_at_td": veto_res["source_revision"],
        }
        state["dispatches"][logical_action_id] = {
            "logical_action_id": logical_action_id,
            "grant_id": grant_id,
            "payload_hash": payload_hash,
            "subject": subject,
            "capability": capability,
            "target_id": target_id,
            "audience": audience,
            "source_id": source_id,
            "expected_source_revision": veto_res["source_revision"],
            "phase": "PREPARED",
            "outcome": OUTCOME_PENDING,
            "sent_count": 0,
            "admitted_at": ts,
            "revoked_at": None,
            "submit_at": None,
            "cancel_attempted": False,
        }
        resp = {
            "ok": True,
            "code": "DISPATCH_RESERVED",
            "grant_id": grant_id,
            "logical_action_id": logical_action_id,
            "reservation_status": RES_RESERVED,
            "lease_revision": lease["revision"],
            "source_revision": veto_res["source_revision"],
        }
        return _commit_state_and_op(day_root, state, operation_id, op_hash, 200, resp, ts)


def execute_guarded_dispatch(
    day_root: Path | str,
    *,
    grant_id: str,
    logical_action_id: str,
    payload_hash: str,
    payload_text: str,
    subject: str,
    capability: str,
    target_id: str,
    audience: str,
    operation_id: str,
    expected_lease_revision: Optional[int] = None,
    expected_source_revision: Optional[int] = None,
    source_id: Optional[str] = None,
    privacy_state: Optional[Mapping[str, Any]] = None,
    boundary_state: Optional[Mapping[str, Any]] = None,
    timing_state: Optional[Mapping[str, Any]] = None,
    budget_state: Optional[Mapping[str, Any]] = None,
    writer_epoch: int = 1,
    transport_fn: Optional[Callable[[Mapping[str, Any]], str]] = None,
    control_root: Path | str | None = None,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """T2 linearized pre-dispatch gate + side-effect dispatch; blocks Ch.14 Scenario 8 race with sent_count == 0."""
    ts = _now_iso(now_iso)
    op_hash = _tx.payload_fingerprint(
        {
            "op": "execute_guarded_dispatch",
            "grant_id": grant_id,
            "logical_action_id": logical_action_id,
            "payload_hash": payload_hash,
            "target_id": target_id,
        }
    )

    spath = _state_path(day_root)
    cpath = _control_ledger_path(day_root, control_root)
    with _tx.state_file_lock(spath), _tx.state_file_lock(cpath):
        state, state_exists = _load_state_readonly(day_root)
        control, control_exists = _load_control_readonly(day_root, control_root)
        if not control_exists:
            control = _ensure_control_ledger_locked(day_root, control_root)
            control_exists = True

        replay = _check_idempotent_op(day_root, state, operation_id, op_hash)
        if replay is not None:
            return replay

        status, veto_res = _evaluate_veto_on_loaded(
            state,
            state_exists,
            control,
            control_exists,
            grant_id=grant_id,
            subject=subject,
            capability=capability,
            target_id=target_id,
            audience=audience,
            payload_hash=payload_hash,
            logical_action_id=logical_action_id,
            privacy_state=privacy_state,
            boundary_state=boundary_state,
            timing_state=timing_state,
            budget_state=budget_state,
            writer_epoch=writer_epoch,
            source_id=source_id,
            expected_source_revision=expected_source_revision,
            now_iso=ts,
        )
        if status == 200 and expected_lease_revision is not None:
            if int(veto_res["lease_revision"]) != int(expected_lease_revision):
                status = 409
                veto_res = {
                    "ok": False,
                    "allowed": False,
                    "code": "LEASE_REVISION_CHANGED",
                    "lease_revision": veto_res["lease_revision"],
                    "expected_lease_revision": int(expected_lease_revision),
                }

        if status != 200:
            sent_total = len(state.get("delivery_outbox") or [])
            blocked_resp = {
                "ok": False,
                "allowed": False,
                "code": veto_res["code"],
                "delivered": False,
                "sent_count": sent_total,
                "action_sent_count": 0,
                "grant_id": grant_id,
                "logical_action_id": logical_action_id,
            }
            return _commit_state_and_op(day_root, state, operation_id, op_hash, status, blocked_resp, ts)

        lease = state["leases"][grant_id]
        reservations = lease.setdefault("reservations", {})
        res_entry = reservations.get(logical_action_id)
        if res_entry is None:
            res_entry = {
                "logical_action_id": logical_action_id,
                "payload_hash": payload_hash,
                "status": RES_RESERVED,
                "admitted_at": ts,
            }
            reservations[logical_action_id] = res_entry

        outcome = OUTCOME_SUCCEEDED
        if transport_fn is not None:
            try:
                raw_outcome = transport_fn(
                    {
                        "grant_id": grant_id,
                        "logical_action_id": logical_action_id,
                        "target_id": target_id,
                        "audience": audience,
                        "payload_text": payload_text,
                    }
                )
                if raw_outcome in (OUTCOME_SUCCEEDED, OUTCOME_UNKNOWN, OUTCOME_FAILED, OUTCOME_NOT_EXECUTED):
                    outcome = raw_outcome
                else:
                    outcome = OUTCOME_UNKNOWN
            except Exception:
                outcome = OUTCOME_UNKNOWN

        action_sent = 0
        if outcome == OUTCOME_SUCCEEDED:
            state["delivery_outbox"].append(
                {
                    "delivery_id": f"dlv_{uuid.uuid4().hex[:14]}",
                    "grant_id": grant_id,
                    "logical_action_id": logical_action_id,
                    "audience": audience,
                    "target_id": target_id,
                    "payload_hash": payload_hash,
                    "delivered_at": ts,
                }
            )
            res_entry["status"] = RES_CONSUMED
            lease["use_count"] = int(lease.get("use_count") or 0) + 1
            action_sent = 1
        elif outcome == OUTCOME_UNKNOWN:
            # UNKNOWN retains the one-shot reservation without releasing it to another action.
            res_entry["status"] = RES_UNKNOWN_HELD
        elif outcome == OUTCOME_FAILED:
            res_entry["status"] = RES_UNKNOWN_HELD

        dispatch_rec = state["dispatches"].setdefault(
            logical_action_id,
            {"logical_action_id": logical_action_id, "grant_id": grant_id, "payload_hash": payload_hash},
        )
        dispatch_rec.update(
            {
                "phase": "CLOSED" if outcome == OUTCOME_SUCCEEDED else "QUIESCED_UNRESOLVED",
                "outcome": outcome,
                "sent_count": action_sent,
                "submit_at": ts,
            }
        )
        resp = {
            "ok": outcome == OUTCOME_SUCCEEDED,
            "code": "DISPATCH_DELIVERED" if outcome == OUTCOME_SUCCEEDED else "DISPATCH_OUTCOME_UNKNOWN",
            "delivered": outcome == OUTCOME_SUCCEEDED,
            "outcome": outcome,
            "sent_count": len(state["delivery_outbox"]),
            "action_sent_count": action_sent,
            "reservation_status": res_entry["status"],
            "grant_id": grant_id,
            "logical_action_id": logical_action_id,
        }
        return _commit_state_and_op(day_root, state, operation_id, op_hash, 200, resp, ts)


def settle_dispatch_outcome(
    day_root: Path | str,
    *,
    grant_id: str,
    logical_action_id: str,
    outcome: str,
    operation_id: str,
    writer_fenced_proof: bool = False,
    store_integrity_proof: bool = False,
    cancel_requested: bool = False,
    control_root: Path | str | None = None,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Settle a reserved/in-flight dispatch outcome; UNKNOWN keeps one-shot reservation locked."""
    ts = _now_iso(now_iso)
    op_hash = _tx.payload_fingerprint(
        {
            "op": "settle_dispatch_outcome",
            "grant_id": grant_id,
            "logical_action_id": logical_action_id,
            "outcome": outcome,
            "writer_fenced_proof": bool(writer_fenced_proof),
            "store_integrity_proof": bool(store_integrity_proof),
        }
    )
    spath = _state_path(day_root)
    cpath = _control_ledger_path(day_root, control_root)
    with _tx.state_file_lock(spath), _tx.state_file_lock(cpath):
        state, _ = _load_state_readonly(day_root)
        _ensure_control_ledger_locked(day_root, control_root)
        replay = _check_idempotent_op(day_root, state, operation_id, op_hash)
        if replay is not None:
            return replay

        lease = state["leases"].get(grant_id)
        if lease is None:
            return 404, {"ok": False, "code": "LEASE_NOT_FOUND", "llm_calls": 0}
        res_entry = (lease.get("reservations") or {}).get(logical_action_id)
        if res_entry is None:
            return 404, {"ok": False, "code": "RESERVATION_NOT_FOUND", "llm_calls": 0}

        released = False
        if outcome == OUTCOME_UNKNOWN:
            res_entry["status"] = RES_UNKNOWN_HELD
        elif outcome == OUTCOME_NOT_EXECUTED:
            if writer_fenced_proof and store_integrity_proof:
                res_entry["status"] = RES_RELEASED
                released = True
            else:
                res_entry["status"] = RES_UNKNOWN_HELD
        elif outcome == OUTCOME_SUCCEEDED:
            if res_entry["status"] != RES_CONSUMED:
                res_entry["status"] = RES_CONSUMED
                lease["use_count"] = int(lease.get("use_count") or 0) + 1
        else:
            res_entry["status"] = RES_UNKNOWN_HELD

        disp = state["dispatches"].get(logical_action_id)
        if isinstance(disp, dict):
            disp["outcome"] = outcome
            disp["cancel_attempted"] = bool(cancel_requested or disp.get("cancel_attempted"))

        resp = {
            "ok": True,
            "code": "OUTCOME_SETTLED",
            "grant_id": grant_id,
            "logical_action_id": logical_action_id,
            "outcome": outcome,
            "reservation_status": res_entry["status"],
            "released": released,
        }
        return _commit_state_and_op(day_root, state, operation_id, op_hash, 200, resp, ts)


def record_post_dispatch_revocation(
    day_root: Path | str,
    *,
    grant_id: str,
    logical_action_id: str,
    actor_ref: str,
    operation_id: str,
    reason: str = "in_flight_revoke",
    control_root: Path | str | None = None,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Record revocation after TD/submit: prevents future sends, attempts cancel, never falsifies already-delivered receipt."""
    ts = _now_iso(now_iso)
    status, rev_res = revoke_permission_lease(
        day_root,
        grant_id=grant_id,
        actor_ref=actor_ref,
        operation_id=f"{operation_id}:revoke",
        reason=reason,
        control_root=control_root,
        now_iso=ts,
    )
    if status != 200:
        return status, rev_res

    op_hash = _tx.payload_fingerprint(
        {"op": "post_dispatch_revocation", "grant_id": grant_id, "logical_action_id": logical_action_id}
    )
    spath = _state_path(day_root)
    with _tx.state_file_lock(spath):
        state, _ = _load_state_readonly(day_root)
        replay = _check_idempotent_op(day_root, state, operation_id, op_hash)
        if replay is not None:
            return replay
        disp = state["dispatches"].get(logical_action_id)
        if disp is None:
            return 404, {"ok": False, "code": "DISPATCH_NOT_FOUND", "llm_calls": 0}
        disp["revoked_at"] = ts
        disp["cancel_attempted"] = True
        already_sent = int(disp.get("sent_count") or 0) > 0
        disp["delivery_retracted"] = False
        resp = {
            "ok": True,
            "code": "POST_DISPATCH_REVOCATION_RECORDED",
            "grant_id": grant_id,
            "logical_action_id": logical_action_id,
            "cancel_attempted": True,
            "already_delivered_preserved": already_sent,
            "delivery_retracted": False,
        }
        return _commit_state_and_op(day_root, state, operation_id, op_hash, 200, resp, ts)


# =============================================================================
# 3. Forget/DeleteCase Lifecycle + Multi-Hop Derivation/ReadBarrier (P22-P25)
# =============================================================================


def ingest_source(
    day_root: Path | str,
    *,
    source_id: str,
    text: str,
    operation_id: str,
    generation: int = 1,
    visibility: str = "private",
    topic: Optional[str] = None,
    session_id: Optional[str] = None,
    control_root: Path | str | None = None,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Ingest a source record with explicit generation and revision."""
    ts = _now_iso(now_iso)
    op_hash = _tx.payload_fingerprint(
        {"op": "ingest_source", "source_id": source_id, "text_sha": _sha256_text(text), "generation": int(generation)}
    )
    spath = _state_path(day_root)
    cpath = _control_ledger_path(day_root, control_root)
    with _tx.state_file_lock(spath), _tx.state_file_lock(cpath):
        state, _ = _load_state_readonly(day_root)
        control = _ensure_control_ledger_locked(day_root, control_root)
        replay = _check_idempotent_op(day_root, state, operation_id, op_hash)
        if replay is not None:
            return replay

        digest = _hmac_tombstone_digest(control["hmac_key_hex"], "source", source_id)
        tomb = (control.get("tombstones") or {}).get(digest)
        if tomb is not None and int(generation) <= int(tomb.get("generation") or 0):
            return 409, {"ok": False, "code": "STALE_GENERATION_REJECTED", "llm_calls": 0}

        existing = state["sources"].get(source_id)
        if existing is not None:
            if existing.get("status") == LIFECYCLE_TOMBSTONED and int(generation) <= int(
                existing.get("tombstoned_generation") or existing.get("generation") or 0
            ):
                return 409, {"ok": False, "code": "STALE_GENERATION_REJECTED", "llm_calls": 0}
            new_rev = int(existing.get("revision") or 0) + 1
        else:
            new_rev = 1

        src_doc: dict[str, Any] = {
            "source_id": source_id,
            "content": text,
            "generation": int(generation),
            "revision": new_rev,
            "visibility": visibility,
            "status": LIFECYCLE_ACTIVE,
            "hold_active": False,
            "hold_id": None,
            "tombstoned_generation": 0,
            "recorded_at": ts,
        }
        if topic is not None and str(topic).strip():
            src_doc["topic"] = str(topic).strip()
        if session_id is not None and str(session_id).strip():
            src_doc["session_id"] = str(session_id).strip()
        state["sources"][source_id] = src_doc
        resp = {
            "ok": True,
            "code": "SOURCE_INGESTED",
            "source_id": source_id,
            "revision": new_rev,
            "generation": int(generation),
        }
        return _commit_state_and_op(day_root, state, operation_id, op_hash, 200, resp, ts)


def register_derivation(
    day_root: Path | str,
    *,
    node_id: str,
    surface: str,
    source_refs: Sequence[Mapping[str, Any]],
    content: str,
    generation: int,
    operation_id: str,
    parent_node_ids: Sequence[str] = (),
    transform_use: str = "memory_derivation",
    visibility: str = "private",
    control_root: Path | str | None = None,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Register a derived node across episode/period/p12_narrative/vector_index/belief/drafts with barrier checks."""
    ts = _now_iso(now_iso)
    if surface not in DERIVATION_SURFACES:
        return 400, {"ok": False, "code": "INVALID_DERIVATION_SURFACE", "surface": surface, "llm_calls": 0}
    if not source_refs and not parent_node_ids:
        return 400, {"ok": False, "code": "DERIVATION_REQUIRES_EXACT_SOURCES", "llm_calls": 0}

    op_hash = _tx.payload_fingerprint(
        {
            "op": "register_derivation",
            "node_id": node_id,
            "surface": surface,
            "source_refs": [dict(r) for r in source_refs],
            "parent_node_ids": list(parent_node_ids),
            "generation": int(generation),
            "content_sha": _sha256_text(content),
        }
    )

    spath = _state_path(day_root)
    cpath = _control_ledger_path(day_root, control_root)
    with _tx.state_file_lock(spath), _tx.state_file_lock(cpath):
        state, _ = _load_state_readonly(day_root)
        control = _ensure_control_ledger_locked(day_root, control_root)
        replay = _check_idempotent_op(day_root, state, operation_id, op_hash)
        if replay is not None:
            return replay

        existing_node = state["derivations"].get(node_id)
        if existing_node is not None and existing_node.get("status") == LIFECYCLE_TOMBSTONED:
            if int(generation) <= int(existing_node.get("tombstoned_generation") or 0):
                return 409, {"ok": False, "code": "STALE_GENERATION_REJECTED", "llm_calls": 0}

        root_set: set[str] = set()
        for ref in source_refs:
            sid = str(ref.get("source_id") or "")
            if not sid:
                return 400, {"ok": False, "code": "MISSING_SOURCE_ID", "llm_calls": 0}
            root_set.add(sid)

        for pid in parent_node_ids:
            pnode = state["derivations"].get(pid)
            if pnode is None:
                return 409, {"ok": False, "code": "MISSING_PARENT_DEPENDENCY", "parent_id": pid, "llm_calls": 0}
            if pnode.get("status") == LIFECYCLE_TOMBSTONED:
                return 403, {"ok": False, "code": "DERIVATION_PARENT_TOMBSTONED", "parent_id": pid, "llm_calls": 0}
            if pnode.get("hold_active"):
                return 403, {"ok": False, "code": CASE_HOLD_PENDING, "parent_id": pid, "llm_calls": 0}
            for rs in pnode.get("root_set") or ():
                root_set.add(str(rs))

        # Check every root source for hold, tombstone, control barrier, or stale generation.
        for sid in sorted(root_set):
            src = state["sources"].get(sid)
            digest = _hmac_tombstone_digest(control["hmac_key_hex"], "source", sid)
            ctrl_tomb = (control.get("tombstones") or {}).get(digest)
            if ctrl_tomb is not None:
                if int(generation) <= int(ctrl_tomb.get("generation") or 0):
                    return 409, {
                        "ok": False,
                        "code": "STALE_GENERATION_REJECTED",
                        "source_id": sid,
                        "llm_calls": 0,
                    }
                return 403, {
                    "ok": False,
                    "code": "DERIVATION_SOURCE_TOMBSTONED",
                    "source_id": sid,
                    "llm_calls": 0,
                }
            if src is None:
                return 409, {"ok": False, "code": "MISSING_SOURCE_DEPENDENCY", "source_id": sid, "llm_calls": 0}
            if src.get("hold_active"):
                return 403, {"ok": False, "code": CASE_HOLD_PENDING, "source_id": sid, "llm_calls": 0}
            if src.get("status") == LIFECYCLE_TOMBSTONED:
                if int(generation) <= int(src.get("tombstoned_generation") or src.get("generation") or 0):
                    return 409, {"ok": False, "code": "STALE_GENERATION_REJECTED", "source_id": sid, "llm_calls": 0}
                return 403, {"ok": False, "code": "DERIVATION_SOURCE_TOMBSTONED", "source_id": sid, "llm_calls": 0}
            if int(generation) < int(src.get("generation") or 1):
                return 409, {"ok": False, "code": "STALE_GENERATION_REJECTED", "source_id": sid, "llm_calls": 0}

        node = {
            "node_id": node_id,
            "surface": surface,
            "source_refs": [dict(r) for r in source_refs],
            "parent_node_ids": list(parent_node_ids),
            "root_set": sorted(root_set),
            "transform_use": transform_use,
            "generation": int(generation),
            "content": content,
            "visibility": visibility,
            "status": LIFECYCLE_ACTIVE,
            "hold_active": False,
            "erased": False,
            "tombstoned_generation": 0,
            "committed_at": ts,
        }
        state["derivations"][node_id] = node
        resp = {
            "ok": True,
            "code": "DERIVATION_REGISTERED",
            "node_id": node_id,
            "surface": surface,
            "root_set": node["root_set"],
            "generation": int(generation),
        }
        return _commit_state_and_op(day_root, state, operation_id, op_hash, 200, resp, ts)


def _classify_forget_command(
    command_text: str,
    explicit_scope: Optional[Sequence[str]],
    explicit_confirmation: bool,
) -> bool:
    """Return True if the forget command is ambiguous and requires HOLD_PENDING_SCOPE_CLARIFICATION."""
    if explicit_confirmation and explicit_scope:
        return False
    lowered = command_text.strip().lower()
    if any(pat in lowered for pat in _EXPLICIT_FORGET_PATTERNS):
        return False
    if any(pat in lowered for pat in _AMBIGUOUS_FORGET_PATTERNS):
        return True
    if not explicit_scope and not explicit_confirmation:
        return True
    return False


def _is_source_partner_eligible(
    src: Mapping[str, Any],
    *,
    conversation_id: Optional[str] = None,
    target_domain: Optional[str] = None,
) -> bool:
    if src.get("status") != LIFECYCLE_ACTIVE:
        return False
    if conversation_id:
        src_conv = str(src.get("conversation_id") or src.get("session_id") or "").strip()
        if src_conv and src_conv != conversation_id:
            return False
    if target_domain == "conversation":
        src_dom = str(src.get("domain") or "").strip().lower()
        if src_dom and src_dom not in ("conversation", "chat", "partner", "shared"):
            return False
    if src.get("private") is True or src.get("shared_with_partner") is False:
        return False
    vis = str(src.get("visibility") or "").strip().lower()
    if vis == "private":
        return bool(src.get("shared_with_partner") is True)
    return (
        vis == "shared"
        or src.get("shared_with_partner") is True
        or str(src.get("actor_side") or "").strip().lower() == "partner"
    )


def _find_latest_unique_source(
    state: Mapping[str, Any],
    *,
    actor_side: str = "subject",
    conversation_id: Optional[str] = None,
    target_domain: Optional[str] = None,
) -> list[str]:
    norm_actor = str(actor_side or "subject").strip().lower()
    active: list[tuple[str, str]] = []
    for sid, src in (state.get("sources") or {}).items():
        if not isinstance(src, Mapping) or src.get("status") != LIFECYCLE_ACTIVE:
            continue
        if norm_actor == "partner":
            if not _is_source_partner_eligible(
                src, conversation_id=conversation_id, target_domain=target_domain
            ):
                continue
        elif conversation_id:
            src_conv = str(src.get("conversation_id") or src.get("session_id") or "").strip()
            if src_conv and src_conv != conversation_id:
                continue
        active.append((str(src.get("recorded_at") or ""), sid))
    if not active:
        return []
    active.sort()
    return [active[-1][1]]


def _select_sources_for_scope(
    state: Mapping[str, Any],
    hold: Mapping[str, Any],
    scope_choice: Optional[str],
    window_minutes: Optional[int],
    *,
    actor_side: Optional[str],
    now_iso: str,
) -> list[str]:
    sources_map = state.get("sources") if isinstance(state.get("sources"), Mapping) else {}
    raw_cands = [str(s).strip() for s in (hold.get("candidate_source_ids") or ()) if str(s).strip()]
    indexed = list(enumerate(raw_cands))
    indexed.sort(key=lambda pair: (str((sources_map.get(pair[1]) or {}).get("recorded_at") or ""), pair[0]))
    candidates = [sid for _, sid in indexed]
    if not scope_choice:
        scoped = hold.get("scoped_source_ids")
        if isinstance(scoped, Sequence) and not isinstance(scoped, (str, bytes)):
            return [str(s).strip() for s in scoped if str(s).strip()]
        return candidates

    choice = scope_choice.strip().lower()
    if choice in ("last_turn", "single_turn"):
        return [candidates[-1]] if candidates else []

    is_partner = str(actor_side or "").strip().lower() == "partner"
    cand_set = set(candidates)

    def _partner_allowed(sid: str, sdoc: Mapping[str, Any]) -> bool:
        if not is_partner:
            return True
        if sid in cand_set:
            return True
        return _is_source_partner_eligible(sdoc)

    if choice in ("current_topic", "whole_topic", "topic"):
        anchor_sid = candidates[-1] if candidates else ""
        anchor_doc = sources_map.get(anchor_sid) if isinstance(sources_map.get(anchor_sid), Mapping) else {}
        anchor_topic = str(anchor_doc.get("topic") or anchor_doc.get("topic_id") or "").strip()
        if not anchor_topic:
            return candidates
        matched: list[str] = []
        for sid in candidates:
            sdoc = sources_map.get(sid)
            if isinstance(sdoc, Mapping) and str(sdoc.get("topic") or sdoc.get("topic_id") or "").strip() == anchor_topic:
                matched.append(sid)
        for sid, sdoc in sorted(sources_map.items()):
            if not isinstance(sdoc, Mapping) or sid in matched:
                continue
            if sdoc.get("status") != LIFECYCLE_ACTIVE or not _partner_allowed(str(sid), sdoc):
                continue
            if str(sdoc.get("topic") or sdoc.get("topic_id") or "").strip() == anchor_topic:
                matched.append(str(sid))
        return matched or ([anchor_sid] if anchor_sid else candidates)

    if choice == "session_segment":
        anchor_sid = candidates[-1] if candidates else ""
        anchor_doc = sources_map.get(anchor_sid) if isinstance(sources_map.get(anchor_sid), Mapping) else {}
        anchor_sess = str(anchor_doc.get("session_id") or anchor_doc.get("segment_id") or "").strip()
        if not anchor_sess:
            return candidates
        matched = []
        for sid in candidates:
            sdoc = sources_map.get(sid)
            if isinstance(sdoc, Mapping) and str(sdoc.get("session_id") or sdoc.get("segment_id") or "").strip() == anchor_sess:
                matched.append(sid)
        for sid, sdoc in sorted(sources_map.items()):
            if not isinstance(sdoc, Mapping) or sid in matched:
                continue
            if sdoc.get("status") != LIFECYCLE_ACTIVE or not _partner_allowed(str(sid), sdoc):
                continue
            if str(sdoc.get("session_id") or sdoc.get("segment_id") or "").strip() == anchor_sess:
                matched.append(str(sid))
        return matched or ([anchor_sid] if anchor_sid else candidates)

    if choice == "time_window":
        wm = max(1, min(1440, int(window_minutes or 30)))
        ref_dt = _parse_dt(str(hold.get("created_at") or now_iso)) or _parse_dt(now_iso)
        if ref_dt is None:
            return candidates
        cutoff = ref_dt - timedelta(minutes=wm)
        matched = []
        any_parsed = False
        for sid in candidates:
            sdoc = sources_map.get(sid)
            rec_dt = _parse_dt(str((sdoc or {}).get("recorded_at") or "")) if isinstance(sdoc, Mapping) else None
            if rec_dt is not None:
                any_parsed = True
                if cutoff <= rec_dt <= ref_dt:
                    matched.append(sid)
        for sid, sdoc in sorted(sources_map.items()):
            if not isinstance(sdoc, Mapping) or sid in matched:
                continue
            if sdoc.get("status") != LIFECYCLE_ACTIVE or not _partner_allowed(str(sid), sdoc):
                continue
            rec_dt = _parse_dt(str(sdoc.get("recorded_at") or ""))
            if rec_dt is not None:
                any_parsed = True
                if cutoff <= rec_dt <= ref_dt:
                    matched.append(str(sid))
        return matched if any_parsed else candidates

    return candidates


def _recompute_active_holds_on_state(state: Mapping[str, Any], *, skip_hold_id: Optional[str] = None) -> set[str]:
    still_held_sources: set[str] = set()
    for hid, hdoc in (state.get("holds") or {}).items():
        if hid == skip_hold_id or not isinstance(hdoc, Mapping):
            continue
        if bool(hdoc.get("read_blocked")) or str(hdoc.get("status") or "") in (CASE_HOLD_PENDING, CASE_RETRACTED):
            active_list = hdoc.get("scoped_source_ids") if "scoped_source_ids" in hdoc else hdoc.get("candidate_source_ids")
            for sid in active_list or ():
                if str(sid).strip():
                    still_held_sources.add(str(sid).strip())
    return still_held_sources


def _sync_all_hold_active_flags(
    state: dict[str, Any],
    *,
    cleared_hold_id: Optional[str] = None,
) -> set[str]:
    """Recompute per-source and per-derivation ``hold_active`` from all currently active holds."""
    active_held = _recompute_active_holds_on_state(state)
    sources_map = state.get("sources") if isinstance(state.get("sources"), Mapping) else {}
    for sid, src in sources_map.items():
        if not isinstance(src, dict) or src.get("status") == LIFECYCLE_TOMBSTONED:
            continue
        is_now_held = str(sid).strip() in active_held
        src["hold_active"] = is_now_held
        if not is_now_held and (cleared_hold_id is None or src.get("hold_id") == cleared_hold_id):
            src["hold_id"] = None
    derivs_map = state.get("derivations") if isinstance(state.get("derivations"), Mapping) else {}
    for _nid, ndoc in derivs_map.items():
        if not isinstance(ndoc, dict) or ndoc.get("status") == LIFECYCLE_TOMBSTONED or ndoc.get("erased"):
            continue
        roots = {str(r).strip() for r in (ndoc.get("root_set") or ()) if str(r).strip()}
        ndoc["hold_active"] = bool(roots & active_held)
    return active_held


def submit_forget_command(
    day_root: Path | str,
    *,
    command_text: str,
    operation_id: str,
    recent_source_ids: Sequence[str] = (),
    explicit_scope: Optional[Sequence[str]] = None,
    explicit_confirmation: bool = False,
    unreachable_copies: Sequence[str] = (),
    generation: Optional[int] = None,
    actor_side: str = "subject",
    shared_with_partner: Optional[bool] = None,
    conversation_id: Optional[str] = None,
    target_domain: Optional[str] = None,
    require_candidates: bool = False,
    control_root: Path | str | None = None,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Admit a forget command: ambiguous -> HOLD_PENDING_SCOPE_CLARIFICATION; explicit -> immediate erasure."""
    ts = _now_iso(now_iso)
    norm_actor = str(actor_side or "subject").strip().lower() or "subject"
    norm_conv = str(conversation_id or "").strip() or None
    norm_domain = str(target_domain or "").strip().lower() or None
    is_partner_visible = True if norm_actor == "partner" else bool(shared_with_partner)
    is_ambiguous = _classify_forget_command(command_text, explicit_scope, explicit_confirmation)
    if not is_ambiguous:
        target_ids = sorted(set(explicit_scope or recent_source_ids or ()))
        if not target_ids:
            target_ids = _find_latest_unique_source(
                _load_state_readonly(day_root)[0],
                actor_side=norm_actor,
                conversation_id=norm_conv,
                target_domain=norm_domain,
            )
        return execute_explicit_forget(
            day_root,
            source_ids=target_ids,
            operation_id=operation_id,
            generation=generation,
            unreachable_copies=unreachable_copies,
            actor_side=norm_actor,
            shared_with_partner=is_partner_visible,
            control_root=control_root,
            now_iso=ts,
        )

    op_hash_doc: dict[str, Any] = {
        "op": "submit_forget_hold",
        "command_sha": _sha256_text(command_text),
        "recent_source_ids": list(recent_source_ids),
    }
    if norm_actor != "subject":
        op_hash_doc["actor_side"] = norm_actor
    if norm_conv:
        op_hash_doc["conversation_id"] = norm_conv
    if norm_domain:
        op_hash_doc["target_domain"] = norm_domain
    if require_candidates:
        op_hash_doc["require_candidates"] = True
    op_hash = _tx.payload_fingerprint(op_hash_doc)

    spath = _state_path(day_root)
    cpath = _control_ledger_path(day_root, control_root)
    with _tx.state_file_lock(spath), _tx.state_file_lock(cpath):
        state, _ = _load_state_readonly(day_root)
        control = _ensure_control_ledger_locked(day_root, control_root)
        replay = _check_idempotent_op(day_root, state, operation_id, op_hash)
        if replay is not None:
            return replay

        supplied_ids = sorted({str(s).strip() for s in recent_source_ids if str(s).strip()})
        if supplied_ids:
            if require_candidates:
                sources_map = state.get("sources") or {}
                for sid in supplied_ids:
                    src = sources_map.get(sid)
                    if not isinstance(src, Mapping):
                        return 403, {"ok": False, "code": "FORBIDDEN_FORGET_CASE_SCOPE", "llm_calls": 0}
                    if norm_actor == "partner" and not _is_source_partner_eligible(
                        src, conversation_id=norm_conv, target_domain=norm_domain
                    ):
                        return 403, {"ok": False, "code": "FORBIDDEN_FORGET_CASE_SCOPE", "llm_calls": 0}
            candidate_ids = supplied_ids
        else:
            candidate_ids = _find_latest_unique_source(
                state,
                actor_side=norm_actor,
                conversation_id=norm_conv,
                target_domain=norm_domain,
            )

        if not candidate_ids and (require_candidates or norm_actor == "partner"):
            return 409, {
                "ok": False,
                "code": "FORGET_NO_ELIGIBLE_CANDIDATES",
                "status": "NO_CANDIDATE_NOT_EXECUTED",
                "read_blocked": False,
                "outbound_blocked": False,
                "llm_calls": 0,
            }

        blocked_tokens: list[str] = []
        blocked_derivations: list[str] = []
        for sid in candidate_ids:
            src = state["sources"].get(sid)
            if isinstance(src, dict):
                src["hold_active"] = True
            blocked_tokens.append(_opaque_source_token(control["hmac_key_hex"], sid))

        for nid, node in state["derivations"].items():
            if isinstance(node, dict) and set(node.get("root_set") or ()) & set(candidate_ids):
                node["hold_active"] = True
                blocked_derivations.append(nid)

        hold_id = f"hold_{uuid.uuid4().hex[:14]}"
        for sid in candidate_ids:
            src = state["sources"].get(sid)
            if isinstance(src, dict):
                src["hold_id"] = hold_id

        # Zero echo of any secret text in the clarification prompt!
        clarification_prompt = (
            "The recent segment has been placed on immediate read and outbound hold. "
            "Please clarify whether you want to retract the preference/intent only, "
            "or permanently delete the stored record and all derived memories."
        )
        hold_doc = {
            "hold_id": hold_id,
            "status": CASE_HOLD_PENDING,
            "actor_side": norm_actor,
            "shared_with_partner": is_partner_visible,
            **({"conversation_id": norm_conv} if norm_conv else {}),
            **({"target_domain": norm_domain} if norm_domain else {}),
            "candidate_source_ids": candidate_ids,
            "opaque_candidate_tokens": blocked_tokens,
            "blocked_derivation_ids": sorted(blocked_derivations),
            "read_blocked": True,
            "outbound_blocked": True,
            "never_auto_release_on_timeout": True,
            "clarification_prompt": clarification_prompt,
            "created_at": ts,
        }
        state["holds"][hold_id] = hold_doc
        _append_audit(
            state,
            "FORGET_HOLD_CREATED",
            {
                "hold_id": hold_id,
                "status": CASE_HOLD_PENDING,
                "opaque_tokens": blocked_tokens,
            },
            ts,
        )
        resp = {
            "ok": True,
            "code": CASE_HOLD_PENDING,
            "status": CASE_HOLD_PENDING,
            "hold_id": hold_id,
            "read_blocked": True,
            "outbound_blocked": True,
            "never_auto_release_on_timeout": True,
            "clarification_prompt": clarification_prompt,
            "opaque_candidate_tokens": blocked_tokens,
        }
        return _commit_state_and_op(day_root, state, operation_id, op_hash, 200, resp, ts)


def check_hold_status(
    day_root: Path | str,
    *,
    hold_id: str,
    elapsed_seconds: float = 0.0,
) -> tuple[int, dict[str, Any]]:
    """Zero-write hold status check: HOLD_PENDING_SCOPE_CLARIFICATION NEVER times out into auto-resurrection."""
    del elapsed_seconds  # Timeout NEVER releases a hold case!
    state, _ = _load_state_readonly(day_root)
    hold = state["holds"].get(hold_id)
    if hold is None:
        return 404, {"ok": False, "code": "HOLD_NOT_FOUND", "llm_calls": 0}
    return 200, {
        "ok": True,
        "hold_id": hold_id,
        "status": hold["status"],
        "read_blocked": bool(hold["read_blocked"]),
        "outbound_blocked": bool(hold["outbound_blocked"]),
        "never_auto_release_on_timeout": True,
        "clarification_prompt": hold["clarification_prompt"],
        "llm_calls": 0,
    }


def resolve_hold_case(
    day_root: Path | str,
    *,
    hold_id: str,
    resolution: str,
    operation_id: str,
    scope_choice: Optional[str] = None,
    window_minutes: Optional[int] = None,
    actor_side: Optional[str] = None,
    unreachable_copies: Sequence[str] = (),
    control_root: Path | str | None = None,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Resolve an active HOLD_PENDING_SCOPE_CLARIFICATION via confirm_delete, retract_only, or explicit_cancel_hold."""
    ts = _now_iso(now_iso)
    if resolution not in ("confirm_delete", "retract_only", "explicit_cancel_hold"):
        return 400, {"ok": False, "code": "INVALID_HOLD_RESOLUTION", "llm_calls": 0}

    state_ro, _ = _load_state_readonly(day_root)
    hold_ro = state_ro["holds"].get(hold_id)
    if hold_ro is None:
        return 404, {"ok": False, "code": "HOLD_NOT_FOUND", "llm_calls": 0}

    norm_actor = str(actor_side or "").strip().lower()
    if norm_actor == "partner":
        is_partner_allowed = (
            str(hold_ro.get("actor_side") or "").strip().lower() == "partner"
            or hold_ro.get("shared_with_partner") is True
            or str(hold_ro.get("visibility") or "").strip().lower() == "shared"
        )
        if not is_partner_allowed:
            return 403, {"ok": False, "code": "FORBIDDEN_FORGET_CASE_SCOPE", "llm_calls": 0}

    if resolution == "confirm_delete":
        selected_ids = _select_sources_for_scope(
            state_ro,
            hold_ro,
            scope_choice,
            window_minutes,
            actor_side=actor_side,
            now_iso=ts,
        )
        if not selected_ids:
            selected_ids = list(hold_ro.get("candidate_source_ids") or ())
        status, del_res = execute_explicit_forget(
            day_root,
            source_ids=selected_ids,
            operation_id=operation_id,
            unreachable_copies=unreachable_copies,
            actor_side=norm_actor or str(hold_ro.get("actor_side") or "subject"),
            shared_with_partner=bool(hold_ro.get("shared_with_partner")),
            control_root=control_root,
            now_iso=ts,
        )
        if status == 200:
            spath = _state_path(day_root)
            with _tx.state_file_lock(spath):
                st, _ = _load_state_readonly(day_root)
                if hold_id in st["holds"]:
                    h_mut = st["holds"][hold_id]
                    h_mut["status"] = del_res["status"]
                    h_mut["read_blocked"] = False
                    h_mut["outbound_blocked"] = False
                    h_mut["scoped_source_ids"] = selected_ids
                    if scope_choice:
                        h_mut["selected_scope"] = scope_choice
                    h_mut["resolved_at"] = ts
                    _sync_all_hold_active_flags(st, cleared_hold_id=hold_id)
                    _tx.atomic_write_json(spath, st, sort_keys=True)
        return status, del_res

    op_hash = _tx.payload_fingerprint(
        {
            "op": "resolve_hold_case",
            "hold_id": hold_id,
            "resolution": resolution,
            **(
                {"scope_choice": scope_choice.strip().lower()}
                if isinstance(scope_choice, str) and scope_choice.strip()
                else {}
            ),
            **({"window_minutes": int(window_minutes)} if window_minutes is not None else {}),
        }
    )
    spath = _state_path(day_root)
    cpath = _control_ledger_path(day_root, control_root)
    with _tx.state_file_lock(spath), _tx.state_file_lock(cpath):
        state, _ = _load_state_readonly(day_root)
        control = _ensure_control_ledger_locked(day_root, control_root)
        replay = _check_idempotent_op(day_root, state, operation_id, op_hash)
        if replay is not None:
            return replay

        hold = state["holds"].get(hold_id)
        if hold is None:
            return 404, {"ok": False, "code": "HOLD_NOT_FOUND", "llm_calls": 0}
        if resolution == "retract_only":
            prior_ids = list(
                dict.fromkeys(
                    str(s).strip()
                    for s in (*(hold.get("candidate_source_ids") or ()), *(hold.get("scoped_source_ids") or ()))
                    if str(s).strip()
                )
            )
            selected_ids = _select_sources_for_scope(
                state,
                hold,
                scope_choice,
                window_minutes,
                actor_side=actor_side,
                now_iso=ts,
            )
            selected_set = set(selected_ids)
            hold["status"] = CASE_RETRACTED
            hold["read_blocked"] = True
            hold["outbound_blocked"] = True
            hold["scoped_source_ids"] = selected_ids
            if scope_choice:
                hold["selected_scope"] = scope_choice.strip().lower()
            if window_minutes is not None:
                hold["window_minutes"] = int(window_minutes)
            hold["resolved_at"] = ts

            all_held = _sync_all_hold_active_flags(state, cleared_hold_id=hold_id)
            for sid in selected_ids:
                src = state["sources"].get(sid)
                if isinstance(src, dict) and src.get("status") != LIFECYCLE_TOMBSTONED:
                    src["hold_id"] = hold_id

            scoped_derivs: list[str] = []
            for nid, ndoc in state["derivations"].items():
                if not isinstance(ndoc, dict) or ndoc.get("status") == LIFECYCLE_TOMBSTONED:
                    continue
                roots = {str(r).strip() for r in (ndoc.get("root_set") or ()) if str(r).strip()}
                if roots & selected_set:
                    scoped_derivs.append(nid)

            hold["blocked_derivation_ids"] = sorted(scoped_derivs)
            hold["opaque_candidate_tokens"] = [
                _opaque_source_token(control["hmac_key_hex"], sid) for sid in selected_ids
            ]
            resp = {
                "ok": True,
                "code": CASE_RETRACTED,
                "hold_id": hold_id,
                "status": CASE_RETRACTED,
                "selected_scope": hold.get("selected_scope"),
                "selected_source_ids": list(selected_ids),
                "released_source_ids": [sid for sid in prior_ids if sid not in selected_set],
                "scoped_count": len(selected_ids),
            }
        else:
            hold["status"] = CASE_CANCELLED
            hold["read_blocked"] = False
            hold["outbound_blocked"] = False
            hold["scoped_source_ids"] = []
            hold["blocked_derivation_ids"] = []
            if scope_choice:
                hold["selected_scope"] = scope_choice.strip().lower()
            hold["resolved_at"] = ts
            _sync_all_hold_active_flags(state, cleared_hold_id=hold_id)
            resp = {"ok": True, "code": CASE_CANCELLED, "hold_id": hold_id, "status": CASE_CANCELLED}
        return _commit_state_and_op(day_root, state, operation_id, op_hash, 200, resp, ts)


def execute_explicit_forget(
    day_root: Path | str,
    *,
    source_ids: Sequence[str],
    operation_id: str,
    generation: Optional[int] = None,
    unreachable_copies: Sequence[str] = (),
    actor_side: str = "subject",
    shared_with_partner: Optional[bool] = None,
    control_root: Path | str | None = None,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Execute multi-hop forget: barrier-first HMAC tombstones, 6-surface erasure, WAITING_RETENTION on unreachable copies."""
    ts = _now_iso(now_iso)
    norm_actor = str(actor_side or "subject").strip().lower() or "subject"
    is_partner_visible = True if norm_actor == "partner" else bool(shared_with_partner)
    clean_ids = sorted({str(sid).strip() for sid in source_ids if str(sid).strip()})
    if not clean_ids:
        return 400, {"ok": False, "code": "FORGET_SCOPE_REQUIRED", "llm_calls": 0}

    unreachable_list = sorted({str(c).strip() for c in unreachable_copies if str(c).strip()})
    op_hash = _tx.payload_fingerprint(
        {
            "op": "execute_explicit_forget",
            "source_ids": clean_ids,
            "generation": generation,
            "unreachable_copies": unreachable_list,
        }
    )

    spath = _state_path(day_root)
    cpath = _control_ledger_path(day_root, control_root)
    with _tx.state_file_lock(spath), _tx.state_file_lock(cpath):
        state, _ = _load_state_readonly(day_root)
        control = _ensure_control_ledger_locked(day_root, control_root)
        replay = _check_idempotent_op(day_root, state, operation_id, op_hash)
        if replay is not None:
            return replay

        case_id = f"fcase_{uuid.uuid4().hex[:14]}"
        tombstone_digests: list[str] = []
        opaque_tokens: list[str] = []
        hmac_key = control["hmac_key_hex"]

        # Step 1: Write external control barrier FIRST (barrier before payload).
        # Mirror into native MemoryDeletionAuthority when authority.db & controls.db exist on POSIX
        # so rolling back JSON sidecars to a pre-forget snapshot can NEVER resurrect forgotten items.
        native_mirror = _mirror_v02_forget_to_native_authority(
            day_root,
            target_ids=clean_ids,
            case_id=case_id,
            operation_id=operation_id,
            control_root=control_root,
        )
        if native_mirror.get("fail_closed"):
            return 503, {
                "ok": False,
                "code": str(native_mirror.get("error_code") or "MEMORY_AUTHORITY_UNAVAILABLE"),
                "llm_calls": 0,
            }

        for sid in clean_ids:
            src = state["sources"].get(sid)
            eff_gen = int(generation) if generation is not None else int((src or {}).get("generation") or 1)
            digest = _hmac_tombstone_digest(hmac_key, "source", sid)
            tok = _opaque_source_token(hmac_key, sid)
            tombstone_digests.append(digest)
            opaque_tokens.append(tok)
            control["tombstones"][digest] = {
                "tombstone_digest": digest,
                "opaque_source_token": tok,
                "domain": "source",
                "generation": eff_gen,
                "case_id": case_id,
                "recorded_at": ts,
            }

        control["deletion_highwater_seq"] = int(control.get("deletion_highwater_seq") or 0) + 1
        state["last_control_deletion_seq"] = control["deletion_highwater_seq"]
        _tx.atomic_write_json(cpath, control, sort_keys=True)

        # Step 2: Erase source payloads & mark tombstoned (never store plaintext or raw secret sha256).
        for sid in clean_ids:
            src = state["sources"].get(sid)
            if isinstance(src, dict):
                eff_gen = int(generation) if generation is not None else int(src.get("generation") or 1)
                src["status"] = LIFECYCLE_TOMBSTONED
                src["content"] = ""
                src["hold_active"] = False
                src["tombstoned_generation"] = max(int(src.get("tombstoned_generation") or 0), eff_gen)
                src["revision"] = int(src.get("revision") or 0) + 1

        # Step 3: Multi-hop invalidation & payload erasure across all DERIVATION_SURFACES.
        surface_acks: dict[str, list[str]] = {surf: [] for surf in DERIVATION_SURFACES}
        target_set = set(clean_ids)
        for nid, node in state["derivations"].items():
            if not isinstance(node, dict):
                continue
            node_roots = set(node.get("root_set") or ())
            if node_roots & target_set:
                node["status"] = LIFECYCLE_TOMBSTONED
                node["erased"] = True
                node["content"] = ""
                node["hold_active"] = False
                eff_gen = int(generation) if generation is not None else int(node.get("generation") or 1)
                node["tombstoned_generation"] = max(int(node.get("tombstoned_generation") or 0), eff_gen)
                node["tombstoned_at"] = ts
                surf = str(node.get("surface") or "")
                if surf in surface_acks:
                    surface_acks[surf].append(nid)

        case_status = CASE_WAITING_RETENTION if unreachable_list else CASE_COMPLETE
        case_doc = {
            "case_id": case_id,
            "status": case_status,
            "complete": case_status == CASE_COMPLETE,
            "actor_side": norm_actor,
            "shared_with_partner": is_partner_visible,
            "tombstone_digests": tombstone_digests,
            "opaque_source_tokens": opaque_tokens,
            "digest_algorithm": "HMAC-SHA256",
            "surface_acks": surface_acks,
            "remaining_unreachable_copies": unreachable_list,
            "deletion_highwater_seq": control["deletion_highwater_seq"],
            "native_authority_mirrored": bool(native_mirror.get("mirrored")),
            "created_at": ts,
        }
        state["forget_cases"][case_id] = case_doc
        _append_audit(
            state,
            "FORGET_CASE_EXECUTED",
            {
                "case_id": case_id,
                "status": case_status,
                "tombstone_digests": tombstone_digests,
                "remaining_unreachable_copies": unreachable_list,
                "native_authority_mirrored": bool(native_mirror.get("mirrored")),
            },
            ts,
        )
        resp = {
            "ok": True,
            "code": case_status,
            "case_id": case_id,
            "status": case_status,
            "complete": case_status == CASE_COMPLETE,
            "tombstone_digests": tombstone_digests,
            "opaque_source_tokens": opaque_tokens,
            "surface_acks": surface_acks,
            "remaining_unreachable_copies": unreachable_list,
            "deletion_highwater_seq": control["deletion_highwater_seq"],
            "native_authority_mirrored": bool(native_mirror.get("mirrored")),
        }
        return _commit_state_and_op(day_root, state, operation_id, op_hash, 200, resp, ts)


def acknowledge_retention_copy(
    day_root: Path | str,
    *,
    case_id: str,
    copy_id: str,
    operation_id: str,
    now_iso: Optional[str] = None,
) -> tuple[int, dict[str, Any]]:
    """Acknowledge deletion of an external/offline copy; transitions WAITING_RETENTION -> COMPLETE when all cleared."""
    ts = _now_iso(now_iso)
    op_hash = _tx.payload_fingerprint({"op": "ack_retention_copy", "case_id": case_id, "copy_id": copy_id})
    spath = _state_path(day_root)
    with _tx.state_file_lock(spath):
        state, _ = _load_state_readonly(day_root)
        replay = _check_idempotent_op(day_root, state, operation_id, op_hash)
        if replay is not None:
            return replay

        case_doc = state["forget_cases"].get(case_id)
        if case_doc is None:
            return 404, {"ok": False, "code": "FORGET_CASE_NOT_FOUND", "llm_calls": 0}

        rem = [c for c in (case_doc.get("remaining_unreachable_copies") or []) if c != copy_id]
        case_doc["remaining_unreachable_copies"] = rem
        if not rem:
            case_doc["status"] = CASE_COMPLETE
            case_doc["complete"] = True
            case_doc["completed_at"] = ts
        resp = {
            "ok": True,
            "code": case_doc["status"],
            "case_id": case_id,
            "status": case_doc["status"],
            "complete": bool(case_doc["complete"]),
            "remaining_unreachable_copies": rem,
        }
        return _commit_state_and_op(day_root, state, operation_id, op_hash, 200, resp, ts)


_NATIVE_AUTHORITY_GENESIS_DIGEST = hashlib.sha256(b"nyairo-memory-authority-v1").hexdigest()

_NATIVE_AUTHORITY_ENV_VARS: tuple[str, ...] = (
    "NYAIRO_MEMORY_AUTHORITY_DB",
    "NYA02_MEMORY_AUTHORITY_DB",
    "CHIYO_LIFE_MEMORY_AUTHORITY_DB",
)
_NATIVE_CONTROLS_ENV_VARS: tuple[str, ...] = (
    "NYAIRO_MEMORY_CONTROLS_DB",
    "NYA02_MEMORY_CONTROLS_DB",
    "CHIYO_LIFE_MEMORY_CONTROLS_DB",
)
_NATIVE_EVIDENCE_ENV_VARS: tuple[str, ...] = (
    "NYAIRO_MEMORY_DB",
    "NYA02_MEMORY_DB",
    "CHIYO_LIFE_MEMORY_DB",
)
_NATIVE_AUTHORITY_MIN_SEQ_ENV_VARS: tuple[str, ...] = (
    "NYAIRO_MEMORY_AUTHORITY_MIN_SEQ",
    "NYA02_MEMORY_AUTHORITY_MIN_SEQ",
    "CHIYO_LIFE_MEMORY_AUTHORITY_MIN_SEQ",
)
_NATIVE_AUTHORITY_FLOOR_ENV_VARS: tuple[str, ...] = (
    "NYAIRO_MEMORY_AUTHORITY_FLOOR",
    "NYA02_MEMORY_AUTHORITY_FLOOR",
    "CHIYO_LIFE_MEMORY_AUTHORITY_FLOOR",
)

_ENTITY_BARRIER_SCALAR_KEYS: tuple[str, ...] = (
    "artifact_id",
    "source_id",
    "derivation_id",
    "node_id",
    "work_id",
    "source_work_id",
    "source_artifact_id",
    "artifact_ref",
    "work_ref",
    "linked_work_ref",
    "target_work_ref",
    "fallback_artifact_ref",
    "item_id",
    "trace_id",
    "object_id",
    "script_id",
    "scrap_id",
    "period_id",
    "chapter_id",
    "practice_id",
    "routine_id",
    "entry_id",
    "episode_id",
    "episode_key",
    "claim_id",
    "memory_id",
    "case_id",
    "hold_id",
    "plan_id",
    "wait_id",
    "mark_id",
    "book_code",
    "book_id",
    "desire_id",
    "dream_id",
    "notice_id",
    "proposal_id",
    "postcard_id",
    "concern_ref",
    "project_id",
    "tension_id",
    "encounter_id",
    "outbound_id",
    "event_id",
    "turn_id",
    "focus_ref",
    "record_id",
    "note_id",
    "favor_id",
    "carton_id",
    "spark_id",
    "card_id",
    "suggestion_id",
    "journey_id",
    "annotation_id",
    "annotation_ref",
    "experiment_id",
    "phrasing_id",
    "preference_id",
    "session_id",
    "keepsake_id",
    "surprise_id",
    "alternative_id",
    "source_ref",
    "receipt_ref",
    "action_ref",
    "evidence_ref",
    "goal_id",
    "escrow_id",
    "stipend_id",
    "gift_id",
    "care_id",
    "reply_id",
    "option_id",
    "conflict_id",
    "reflection_id",
    "regret_id",
    "rule_id",
    "question_id",
    "inquiry_id",
    "cue_id",
    "capsule_id",
    "gesture_id",
    "tool_id",
    "resp_id",
    "revision_id",
    "tradeoff_id",
    "activity_id",
    "sleep_activity_id",
    "hint_id",
    "regular_id",
    "direction_id",
    "game_id",
    "booklet_id",
    "cloth_id",
    "letter_id",
    "conversation_ref",
    "reminder_ref",
    "payload_ref",
    "active_step_ref",
    "fictional_prop_object_id",
    "supply_object_id",
    "garment_object_id",
    "visit_id",
    "followup_id",
    "shortcut_id",
    "spectacle_id",
    "pref_id",
    "adjustment_id",
    "clue_id",
    "enforcement_id",
    "hypothesis_id",
    "s03_claim_id",
    "source_regret_id",
    "coaster_id",
    "errand_id",
    "rumor_id",
    "box_id",
    "consignment_id",
    "concern_id",
    "plant_id",
    "patch_id",
    "divider_id",
    "draft_id",
    "edit_id",
    "branch_id",
    "partner_annotation_id",
    "subject_annotation_id",
    "deliberation_id",
    "transaction_id",
    "receipt_id",
    "support_id",
    "last_outbound_id",
    "pending_outbound_id",
    "frost_id",
    "world_anchor",
    "origin_ref",
    "ref",
    "id",
)

_ENTITY_BARRIER_LIST_KEYS: tuple[str, ...] = (
    "root_set",
    "source_refs",
    "provenance_refs",
    "origin_refs",
    "episode_ids",
    "durable_claim_ids",
    "source_ids",
    "source_event_ids",
    "event_ids",
    "blocked_event_ids",
    "linked_work_refs",
    "related_ids",
    "attested_source_refs",
)


def _resolve_instance_paths_safe(day_root: Path | str) -> Any:
    """Zero-write wrapper around ``resolve_instance_paths``."""
    try:
        from components.world.scripts.instance_paths import resolve_instance_paths

        return resolve_instance_paths(day_root)
    except ImportError:
        world_scripts = str(Path(__file__).resolve().parents[2] / "world" / "scripts")
        if world_scripts not in sys.path:
            sys.path.insert(0, world_scripts)
        try:
            from instance_paths import resolve_instance_paths  # type: ignore[import-not-found]

            return resolve_instance_paths(day_root)
        except Exception:
            return None
    except Exception:
        return None


def _import_memory_authority() -> Any:
    """Best-effort import of ``components/native/memory_authority.py``."""
    native_dir = str(Path(__file__).resolve().parents[2] / "native")
    if native_dir not in sys.path:
        sys.path.insert(0, native_dir)
    try:
        import memory_authority as _ma  # type: ignore[import-not-found]

        return _ma
    except Exception:
        return None


def _first_env_path(names: tuple[str, ...]) -> Optional[Path]:
    for name in names:
        raw = str(os.environ.get(name) or "").strip()
        if raw:
            return Path(raw).expanduser()
    return None


def _first_existing_lexists(candidates: Sequence[Optional[Path]]) -> Optional[Path]:
    seen: set[str] = set()
    for cand in candidates:
        if cand is None:
            continue
        key = str(cand)
        if key in seen:
            continue
        seen.add(key)
        try:
            if os.path.lexists(cand):
                return cand
        except OSError:
            continue
    return None


def _resolve_native_memory_db_paths(
    day_root: Path | str,
    *,
    control_root: Path | str | None = None,
) -> tuple[Optional[Path], Optional[Path], Optional[Path]]:
    """Locate existing native ``authority.db``, ``controls.db``, and ``evidence.db`` with 0 writes."""
    root = Path(day_root)
    paths = _resolve_instance_paths_safe(root)
    ext_ctrl_root = resolve_v02_control_root(root, control_root)

    ctrl_candidates: list[Optional[Path]] = [
        _first_env_path(_NATIVE_CONTROLS_ENV_VARS),
        getattr(paths, "memory_controls_db", None) if paths is not None else None,
        root / "memory" / "controls.db",
        root / "one-day" / "life" / "memory" / "controls.db",
        root / "life" / "memory" / "controls.db",
    ]
    controls_path = _first_existing_lexists(ctrl_candidates)

    auth_candidates: list[Optional[Path]] = [
        _first_env_path(_NATIVE_AUTHORITY_ENV_VARS),
        getattr(paths, "memory_authority_db", None) if paths is not None else None,
    ]
    if control_root is not None or ext_ctrl_root != root:
        auth_candidates.append(ext_ctrl_root / "authority.db")
    auth_candidates.extend(
        [
            root.parent / f"{root.name}-memory-authority" / "authority.db",
            root / "retained" / "authority.db",
            root / "memory" / "authority.db",
            root / "control_authority" / "authority.db",
        ]
    )
    authority_path = _first_existing_lexists(auth_candidates)

    ev_candidates: list[Optional[Path]] = [
        _first_env_path(_NATIVE_EVIDENCE_ENV_VARS),
        getattr(paths, "memory_evidence_db", None) if paths is not None else None,
        (controls_path.parent / "evidence.db") if controls_path is not None else None,
        root / "memory" / "evidence.db",
        root / "one-day" / "life" / "memory" / "evidence.db",
    ]
    evidence_path = _first_existing_lexists(ev_candidates)
    return authority_path, controls_path, evidence_path


def _open_sqlite_readonly_zero_write(path: Path) -> sqlite3.Connection:
    """Open an existing SQLite DB strictly read-only without ever creating ``-wal`` or ``-shm``."""
    resolved = path.resolve()
    has_wal = Path(f"{resolved}-wal").exists()
    has_shm = Path(f"{resolved}-shm").exists()
    query = "?mode=ro" if (has_wal and has_shm) else "?mode=ro&immutable=1"
    try:
        conn = sqlite3.connect(resolved.as_uri() + query, uri=True, timeout=2.0)
    except sqlite3.OperationalError:
        if query != "?mode=ro&immutable=1":
            conn = sqlite3.connect(resolved.as_uri() + "?mode=ro&immutable=1", uri=True, timeout=2.0)
        else:
            raise
    conn.row_factory = sqlite3.Row
    return conn


def _normalize_native_blocked_sources(raw_value: Any) -> list[dict[str, Any]]:
    """Mirror ``memory_authority.normalize_sources`` so chain verification matches byte-for-byte."""
    if raw_value in (None, ""):
        return []
    items = raw_value
    if isinstance(items, (bytes, bytearray)):
        items = json.loads(items.decode("utf-8"))
    elif isinstance(items, str):
        items = json.loads(items)
    if not isinstance(items, (list, tuple)):
        raise ValueError("blocked_sources must be a list")
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("blocked_sources entries must be objects")
        kind = str(item.get("kind") or "").strip()
        ref = str(item.get("ref") or "").strip()
        namespace = str(item.get("namespace") or "").strip()
        if not kind or not ref:
            raise ValueError("a typed source needs a kind and an opaque ref")
        entry: dict[str, Any] = {"kind": kind, "namespace": namespace, "ref": ref}
        generation = item.get("generation")
        if generation not in (None, ""):
            entry["generation"] = str(generation)
        out.append(entry)
    out.sort(
        key=lambda item: (
            item["kind"],
            item["namespace"],
            item["ref"],
            item.get("generation", ""),
        )
    )
    unique: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for entry in out:
        key = tuple(sorted(entry.items()))
        if key in seen:
            continue
        seen.add(key)
        unique.append(entry)
    return unique


def _read_native_authority_zero_write(auth_path: Path) -> dict[str, Any]:
    """Verify hash-chain, integrity, and trusted floor of an existing ``authority.db`` with 0 writes."""
    res: dict[str, Any] = {
        "healthy": False,
        "code": "MEMORY_AUTHORITY_UNAVAILABLE",
        "blocked_events": set(),
        "blocked_turns": set(),
        "blocked_source_refs": set(),
    }
    try:
        info = auth_path.lstat()
    except OSError:
        res["code"] = "MEMORY_AUTHORITY_DB_UNREADABLE"
        return res
    if not stat.S_ISREG(info.st_mode):
        res["code"] = "MEMORY_AUTHORITY_DB_NOT_A_FILE"
        return res

    try:
        conn = _open_sqlite_readonly_zero_write(auth_path)
    except Exception:
        res["code"] = "MEMORY_AUTHORITY_DB_UNREADABLE"
        return res

    try:
        conn.execute("BEGIN")
        qc_row = conn.execute("PRAGMA quick_check").fetchone()
        if qc_row is None or str(qc_row[0]).lower() != "ok":
            res["code"] = "MEMORY_AUTHORITY_INTEGRITY_FAILURE"
            return res
        tables = {
            str(r[0])
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if not {"authority_records", "authority_head"} <= tables:
            res["code"] = "MEMORY_AUTHORITY_SCHEMA_MISMATCH"
            return res
        head = conn.execute("SELECT seq, digest FROM authority_head WHERE id=1").fetchone()
        if head is None:
            res["code"] = "MEMORY_AUTHORITY_HEAD_INVALID"
            return res
        head_seq = int(head[0])
        head_digest = str(head[1])
        rows = conn.execute("SELECT * FROM authority_records ORDER BY seq").fetchall()
    except (sqlite3.Error, ValueError, TypeError):
        res["code"] = "MEMORY_AUTHORITY_DB_UNREADABLE"
        return res
    finally:
        conn.close()

    if len(rows) != head_seq:
        res["code"] = "MEMORY_AUTHORITY_CHAIN_INVALID"
        return res

    previous = _NATIVE_AUTHORITY_GENESIS_DIGEST
    blocked_events: set[str] = set()
    blocked_turns: set[str] = set()
    blocked_source_refs: set[str] = set()

    for index, row in enumerate(rows, start=1):
        try:
            if int(row["seq"]) != index:
                res["code"] = "MEMORY_AUTHORITY_CHAIN_INVALID"
                return res
            raw_blocked = row["blocked_event_ids"]
            if isinstance(raw_blocked, str):
                raw_blocked = json.loads(raw_blocked)
            payload: dict[str, Any] = {
                "seq": int(row["seq"]),
                "operation_id": str(row["operation_id"]),
                "owner": str(row["owner"]),
                "conversation": str(row["conversation"]),
                "target_event": str(row["target_event"]),
                "turn_id": str(row["turn_id"]) if row["turn_id"] is not None else None,
                "blocked_event_ids": sorted(str(item) for item in (raw_blocked or [])),
                "fingerprint": str(row["fingerprint"]),
                "created_at": str(row["created_at"]),
            }
            try:
                raw_sources = row["blocked_sources_json"]
            except (KeyError, IndexError):
                raw_sources = None
            sources = _normalize_native_blocked_sources(raw_sources)
            if sources:
                payload["blocked_sources"] = sources
            canonical = json.dumps(
                payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
            expected = hashlib.sha256(previous.encode("utf-8") + b"\n" + canonical).hexdigest()
            if expected != str(row["digest"]):
                res["code"] = "MEMORY_AUTHORITY_CHAIN_INVALID"
                return res
            previous = expected
        except (ValueError, TypeError, KeyError):
            res["code"] = "MEMORY_AUTHORITY_CHAIN_INVALID"
            return res

        for eid in payload["blocked_event_ids"]:
            es = str(eid).strip()
            if es:
                blocked_events.add(es)
        if payload["turn_id"]:
            ts_id = str(payload["turn_id"]).strip()
            if ts_id:
                blocked_turns.add(ts_id)
        for src in payload.get("blocked_sources") or ():
            ref = str(src.get("ref") or "").strip()
            if ref:
                blocked_source_refs.add(ref)

    if previous != head_digest:
        res["code"] = "MEMORY_AUTHORITY_CHAIN_INVALID"
        return res

    # Verify trusted floor (operator min_seq and/or sibling <authority>.floor.json).
    floor_seqs: list[int] = []
    for env_name in _NATIVE_AUTHORITY_MIN_SEQ_ENV_VARS:
        raw_min = str(os.environ.get(env_name) or "").strip()
        if not raw_min:
            continue
        try:
            floor_seqs.append(max(0, int(raw_min)))
        except ValueError:
            res["code"] = "MEMORY_AUTHORITY_HANDOFF_INVALID"
            return res
        break

    floor_override = _first_env_path(_NATIVE_AUTHORITY_FLOOR_ENV_VARS)
    floor_path = floor_override if floor_override is not None else Path(str(auth_path) + ".floor.json")
    try:
        floor_exists = os.path.lexists(floor_path)
    except OSError:
        res["code"] = "MEMORY_AUTHORITY_HANDOFF_INVALID"
        return res
    if floor_exists:
        try:
            if floor_path.is_symlink() or not floor_path.is_file():
                res["code"] = "MEMORY_AUTHORITY_HANDOFF_INVALID"
                return res
            floor_doc = json.loads(floor_path.read_text(encoding="utf-8"))
            if not isinstance(floor_doc, Mapping):
                raise ValueError("floor not mapping")
            f_seq = int(floor_doc["seq"])
            if f_seq < 0:
                raise ValueError("negative floor seq")
            f_digest = floor_doc.get("head_digest")
            f_digest_str = str(f_digest) if f_digest else None
        except (OSError, ValueError, TypeError, KeyError):
            res["code"] = "MEMORY_AUTHORITY_HANDOFF_INVALID"
            return res
        if f_digest_str and 0 < f_seq <= len(rows):
            if str(rows[f_seq - 1]["digest"]) != f_digest_str:
                res["code"] = "MEMORY_AUTHORITY_HANDOFF_INVALID"
                return res
        floor_seqs.append(f_seq)

    if not floor_seqs:
        res["code"] = "MEMORY_AUTHORITY_CURRENTNESS_UNPROVEN"
        return res
    if head_seq < max(floor_seqs):
        res["code"] = "MEMORY_AUTHORITY_OLDER_THAN_TRUSTED"
        return res

    res["healthy"] = True
    res["code"] = "OK"
    res["blocked_events"] = blocked_events
    res["blocked_turns"] = blocked_turns
    res["blocked_source_refs"] = blocked_source_refs
    return res


def _read_native_controls_zero_write(
    controls_path: Path,
    *,
    evidence_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Read blocked events/turns from an existing native ``controls.db`` with 0 writes."""
    res: dict[str, Any] = {
        "healthy": False,
        "code": "MEMORY_CONTROLS_UNHEALTHY",
        "blocked_events": set(),
        "blocked_turns": set(),
        "blocked_source_refs": set(),
    }
    try:
        info = controls_path.lstat()
    except OSError:
        return res
    if not stat.S_ISREG(info.st_mode):
        return res

    try:
        conn = _open_sqlite_readonly_zero_write(controls_path)
    except Exception:
        return res

    blocked_events: set[str] = set()
    blocked_turns: set[str] = set()
    target_pairs: list[tuple[str, str]] = []
    try:
        qc_row = conn.execute("PRAGMA quick_check").fetchone()
        if qc_row is None or str(qc_row[0]).lower() != "ok":
            res["code"] = "MEMORY_CONTROLS_INTEGRITY_FAILURE"
            return res
        tables = {
            str(r[0])
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if "memory_controls" not in tables:
            res["code"] = "MEMORY_CONTROLS_SCHEMA_MISMATCH"
            return res
        rows = conn.execute(
            "SELECT kind, target_event, conversation FROM memory_controls ORDER BY rowid"
        ).fetchall()
        for row in rows:
            tgt = str(row["target_event"] or "").strip()
            conv = str(row["conversation"] or "").strip()
            if tgt:
                blocked_events.add(tgt)
                target_pairs.append((conv, tgt))
        if "memory_erasures" in tables:
            er_rows = conn.execute("SELECT event_ids_json FROM memory_erasures").fetchall()
            for er in er_rows:
                raw_ids = er["event_ids_json"]
                parsed = json.loads(raw_ids) if isinstance(raw_ids, str) else raw_ids
                if isinstance(parsed, Sequence) and not isinstance(parsed, (str, bytes)):
                    for eid in parsed:
                        es = str(eid or "").strip()
                        if es:
                            blocked_events.add(es)
    except (sqlite3.Error, ValueError, TypeError):
        res["code"] = "MEMORY_CONTROLS_UNHEALTHY"
        return res
    finally:
        conn.close()

    # Best-effort turn/sibling closure from existing evidence.db (strictly zero-write).
    if target_pairs and evidence_path is not None:
        try:
            ev_info = evidence_path.lstat()
            if stat.S_ISREG(ev_info.st_mode):
                ev_conn = _open_sqlite_readonly_zero_write(evidence_path)
                try:
                    ev_tables = {
                        str(r[0])
                        for r in ev_conn.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        ).fetchall()
                    }
                    if "evidence_events" in ev_tables:
                        for conv, tgt in target_pairs:
                            if conv:
                                ev_row = ev_conn.execute(
                                    "SELECT turn_id FROM evidence_events WHERE event_id=? AND conversation_id=?",
                                    (tgt, conv),
                                ).fetchone()
                            else:
                                ev_row = ev_conn.execute(
                                    "SELECT turn_id, conversation_id FROM evidence_events WHERE event_id=?",
                                    (tgt,),
                                ).fetchone()
                            if ev_row is None:
                                continue
                            tid = str(ev_row["turn_id"] or "").strip()
                            if tid:
                                blocked_turns.add(tid)
                                eff_conv = conv or str(ev_row["conversation_id"] or "").strip()
                                sibs = ev_conn.execute(
                                    "SELECT event_id FROM evidence_events WHERE conversation_id=? AND turn_id=?",
                                    (eff_conv, tid),
                                ).fetchall()
                                for srow in sibs:
                                    seid = str(srow["event_id"] or "").strip()
                                    if seid:
                                        blocked_events.add(seid)
                finally:
                    ev_conn.close()
        except Exception:
            pass

    res["healthy"] = True
    res["code"] = "OK"
    res["blocked_events"] = blocked_events
    res["blocked_turns"] = blocked_turns
    return res


def _resolve_native_owner_and_conversation(day_root: Path | str) -> tuple[str, str]:
    owner = str(os.environ.get("NYA02_MEMORY_SUBJECT") or "").strip()
    if not owner:
        paths = _resolve_instance_paths_safe(day_root)
        inst_json = getattr(paths, "instance_json", None) if paths is not None else None
        if isinstance(inst_json, Path) and inst_json.is_file():
            doc = _read_json_readonly(inst_json)
            if isinstance(doc, Mapping) and str(doc.get("subject") or "").strip():
                owner = str(doc.get("subject")).strip()
    if not owner:
        owner = "chiyo"
    return owner, f"life:{owner}"


def _mirror_v02_forget_to_native_authority(
    day_root: Path | str,
    *,
    target_ids: Sequence[str],
    case_id: str,
    operation_id: str,
    control_root: Path | str | None = None,
) -> dict[str, Any]:
    """Mirror explicit v02 forget targets into native ``MemoryDeletionAuthority`` when present on POSIX."""
    clean_targets = sorted({str(t).strip() for t in target_ids if str(t).strip()})
    if not clean_targets:
        return {"mirrored": False, "reason": "NO_TARGETS"}
    if os.name != "posix":
        return {"mirrored": False, "reason": "NON_POSIX"}

    auth_path, controls_path, _ = _resolve_native_memory_db_paths(day_root, control_root=control_root)
    if auth_path is None or controls_path is None:
        return {"mirrored": False, "reason": "NATIVE_DB_ABSENT"}
    try:
        if auth_path.is_symlink() or not auth_path.is_file():
            return {"mirrored": False, "fail_closed": True, "error_code": "MEMORY_AUTHORITY_DB_NOT_A_FILE"}
        if controls_path.is_symlink() or not controls_path.is_file():
            return {"mirrored": False, "reason": "CONTROLS_DB_NOT_FILE"}
    except OSError:
        return {"mirrored": False, "fail_closed": True, "error_code": "MEMORY_AUTHORITY_UNAVAILABLE"}

    ma_mod = _import_memory_authority()
    if ma_mod is None:
        return {"mirrored": False, "reason": "MEMORY_AUTHORITY_MODULE_UNAVAILABLE"}

    owner, conversation = _resolve_native_owner_and_conversation(day_root)
    blocked_sources = [
        {"kind": "v02_forget", "namespace": owner, "ref": tid}
        for tid in clean_targets
    ]
    op_id = f"v02-forget:{str(operation_id).strip()}"
    fp = _sha256_text(
        json.dumps(
            {"op": "v02_forget_mirror", "operation_id": op_id, "sources": blocked_sources},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
    )
    authority = ma_mod.MemoryDeletionAuthority(auth_path, owner=owner, conversation=conversation)
    try:
        commit_status = authority.commit(
            operation_id=op_id,
            target_event=f"v02_forget:{case_id}",
            turn_id=None,
            blocked_event_ids=[],
            fingerprint=fp,
            blocked_sources=blocked_sources,
        )
        return {
            "mirrored": True,
            "status": str(commit_status),
            "authority_db": str(auth_path),
            "owner": owner,
            "conversation": conversation,
            "source_count": len(blocked_sources),
        }
    except ma_mod.MemoryAuthorityUnavailable as exc:
        return {
            "mirrored": False,
            "fail_closed": True,
            "error_code": str(exc.code or "MEMORY_AUTHORITY_UNAVAILABLE"),
        }


def load_unified_forget_barrier_readonly(
    day_root: Path | str,
    *,
    control_root: Path | str | None = None,
) -> dict[str, Any]:
    """Unified zero-write Forget & anti-resurrection barrier across v02 JSON, control HMAC ledger, V-C seals, and native SQLite authority/controls."""
    root = Path(day_root)
    state, state_exists = _load_state_readonly(root)
    control, control_exists = _load_control_readonly(root, control_root)
    ctrl_ok, ctrl_code = _verify_control_integrity_readonly(state, state_exists, control, control_exists)

    hmac_key_hex = str(control.get("hmac_key_hex") or "").strip()
    valid_hex_key = False
    if hmac_key_hex:
        try:
            bytes.fromhex(hmac_key_hex)
            valid_hex_key = True
        except ValueError:
            valid_hex_key = False

    tombstone_digests: set[str] = {
        str(k).strip() for k in (control.get("tombstones") or {}).keys() if str(k).strip()
    }
    opaque_tokens: set[str] = set()
    for tmeta in (control.get("tombstones") or {}).values():
        if isinstance(tmeta, Mapping):
            tok = str(tmeta.get("opaque_source_token") or "").strip()
            if tok:
                opaque_tokens.add(tok)

    blocked_ids: set[str] = set()
    active_hold_ids: set[str] = set()
    barrier_active = False

    for hid, hold in (state.get("holds") or {}).items():
        if not isinstance(hold, Mapping):
            continue
        st = str(hold.get("status") or "").upper()
        if bool(hold.get("read_blocked")) or st in (CASE_HOLD_PENDING, CASE_RETRACTED):
            barrier_active = True
            active_hold_ids.add(str(hid))
            src_list = hold.get("scoped_source_ids") if "scoped_source_ids" in hold else hold.get("candidate_source_ids")
            for sid in src_list or ():
                if str(sid).strip():
                    blocked_ids.add(str(sid).strip())
            for did in hold.get("blocked_derivation_ids") or ():
                if str(did).strip():
                    blocked_ids.add(str(did).strip())
            for tok in hold.get("opaque_candidate_tokens") or ():
                if str(tok).strip():
                    opaque_tokens.add(str(tok).strip())

    for fcase in (state.get("forget_cases") or {}).values():
        if not isinstance(fcase, Mapping):
            continue
        barrier_active = True
        for dig in fcase.get("tombstone_digests") or ():
            if str(dig).strip():
                tombstone_digests.add(str(dig).strip())
        for tok in fcase.get("opaque_source_tokens") or ():
            if str(tok).strip():
                opaque_tokens.add(str(tok).strip())

    if tombstone_digests:
        barrier_active = True

    for sid, src in (state.get("sources") or {}).items():
        if not isinstance(src, Mapping):
            continue
        sid_s = str(sid).strip()
        if not sid_s:
            continue
        is_tomb = src.get("status") == LIFECYCLE_TOMBSTONED
        is_held = bool(src.get("hold_active"))
        if not is_tomb and valid_hex_key:
            is_tomb = (
                _hmac_tombstone_digest(hmac_key_hex, "source", sid_s) in tombstone_digests
                or _opaque_source_token(hmac_key_hex, sid_s) in opaque_tokens
            )
        if is_tomb or is_held:
            barrier_active = True
            blocked_ids.add(sid_s)

    derivs = state.get("derivations") if isinstance(state.get("derivations"), Mapping) else {}
    changed = True
    while changed:
        changed = False
        for did, node in derivs.items():
            if not isinstance(node, Mapping):
                continue
            did_s = str(did).strip()
            if not did_s or did_s in blocked_ids:
                continue
            roots = {str(r).strip() for r in (node.get("root_set") or ()) if str(r).strip()}
            is_tomb = node.get("status") == LIFECYCLE_TOMBSTONED or bool(node.get("erased"))
            if not is_tomb and valid_hex_key:
                is_tomb = (
                    _hmac_tombstone_digest(hmac_key_hex, "derivation", did_s) in tombstone_digests
                    or any(_hmac_tombstone_digest(hmac_key_hex, "source", r) in tombstone_digests for r in roots)
                    or any(_opaque_source_token(hmac_key_hex, r) in opaque_tokens for r in roots)
                )
            if is_tomb or bool(node.get("hold_active")) or (roots & blocked_ids):
                barrier_active = True
                blocked_ids.add(did_s)
                changed = True

    for aid, art in (state.get("artifacts") or {}).items():
        if not isinstance(art, Mapping):
            continue
        aid_s = str(aid).strip()
        if not aid_s:
            continue
        is_tomb = art.get("lifecycle") == LIFECYCLE_TOMBSTONED
        if not is_tomb and valid_hex_key:
            is_tomb = (
                _hmac_tombstone_digest(hmac_key_hex, "artifact", aid_s) in tombstone_digests
                or _hmac_tombstone_digest(hmac_key_hex, "source", aid_s) in tombstone_digests
            )
        if is_tomb or bool(art.get("hold_active")):
            barrier_active = True
            blocked_ids.add(aid_s)

    rp_state = _read_json_readonly(root / "memory" / "v02_recall_projection_state.json")
    if isinstance(rp_state, Mapping):
        for mid in (rp_state.get("tombstones") or {}).keys():
            if str(mid).strip():
                barrier_active = True
                blocked_ids.add(str(mid).strip())

    vc_sealed_digests: set[str] = set()
    ext_ctrl_root = resolve_v02_control_root(root, control_root)
    vc_roots: list[Path] = [ext_ctrl_root]
    try:
        if ext_ctrl_root.resolve() != root.resolve():
            vc_roots.append(root)
    except OSError:
        if ext_ctrl_root != root:
            vc_roots.append(root)
    for vc_root in vc_roots:
        for vc_rel in (
            Path("governance") / "v02_vc_authority_watermark.json",
            Path("memory") / "v02_vc_authority_seal.json",
        ):
            vc_seal_doc = _read_json_readonly(vc_root / vc_rel)
            if isinstance(vc_seal_doc, Mapping):
                for key in ("sealed_digests", "sealed_item_digests", "revoked_digests"):
                    raw_col = vc_seal_doc.get(key)
                    iter_digs = raw_col.keys() if isinstance(raw_col, Mapping) else (raw_col or ())
                    for dig in iter_digs:
                        ds = str(dig).strip()
                        if ds:
                            barrier_active = True
                            vc_sealed_digests.add(ds)

    # If a backup/rollback cleared hold_active or tombstones, resolve opaque tokens back against any known IDs in sources/artifacts/memories
    if valid_hex_key and opaque_tokens:
        for coll in (
            state.get("sources") or {},
            state.get("artifacts") or {},
            (rp_state.get("memories") or {}) if isinstance(rp_state, Mapping) else {},
        ):
            if isinstance(coll, Mapping):
                for cid in coll.keys():
                    cs = str(cid).strip()
                    if cs and (
                        _opaque_source_token(hmac_key_hex, cs) in opaque_tokens
                        or _hmac_tombstone_digest(hmac_key_hex, "source", cs) in tombstone_digests
                    ):
                        blocked_ids.add(cs)

    # Merge native SQLite authority.db and controls.db strictly zero-write (never opens missing files).
    native_blocked_events: set[str] = set()
    native_blocked_turns: set[str] = set()
    native_blocked_source_refs: set[str] = set()
    native_sources_checked: list[str] = []
    native_fail_closed = False
    native_fail_code: Optional[str] = None

    auth_db_path, controls_db_path, evidence_db_path = _resolve_native_memory_db_paths(
        root, control_root=control_root
    )
    if auth_db_path is not None:
        native_sources_checked.append(str(auth_db_path))
        auth_res = _read_native_authority_zero_write(auth_db_path)
        if not auth_res.get("healthy"):
            native_fail_closed = True
            native_fail_code = str(auth_res.get("code") or "MEMORY_AUTHORITY_UNAVAILABLE")
        else:
            native_blocked_events.update(auth_res.get("blocked_events") or ())
            native_blocked_turns.update(auth_res.get("blocked_turns") or ())
            native_blocked_source_refs.update(auth_res.get("blocked_source_refs") or ())

    if controls_db_path is not None:
        native_sources_checked.append(str(controls_db_path))
        ctrl_res = _read_native_controls_zero_write(
            controls_db_path, evidence_path=evidence_db_path
        )
        if not ctrl_res.get("healthy"):
            native_fail_closed = True
            if native_fail_code is None:
                native_fail_code = str(ctrl_res.get("code") or "MEMORY_CONTROLS_UNHEALTHY")
        else:
            native_blocked_events.update(ctrl_res.get("blocked_events") or ())
            native_blocked_turns.update(ctrl_res.get("blocked_turns") or ())
            native_blocked_source_refs.update(ctrl_res.get("blocked_source_refs") or ())

    native_union = native_blocked_events | native_blocked_turns | native_blocked_source_refs
    if native_union:
        blocked_ids.update(native_union)
        barrier_active = True

    effective_fail_closed = (not ctrl_ok) or native_fail_closed
    effective_integrity_code = ctrl_code if not ctrl_ok else (native_fail_code or ctrl_code)
    if effective_fail_closed:
        barrier_active = True

    return {
        "raw_rlf": state if state_exists else {},
        "control": control if control_exists else {},
        "fail_closed": effective_fail_closed,
        "integrity_code": effective_integrity_code,
        "barrier_active": barrier_active,
        "blocked_ids": blocked_ids,
        "active_hold_ids": active_hold_ids,
        "hmac_key_hex": hmac_key_hex if valid_hex_key else "",
        "tombstone_digests": tombstone_digests,
        "opaque_tokens": opaque_tokens,
        "vc_sealed_digests": vc_sealed_digests,
        "native_blocked_events": native_blocked_events,
        "native_blocked_turns": native_blocked_turns,
        "native_blocked_source_refs": native_blocked_source_refs,
        "native_blocked_count": len(native_union),
        "native_sources_checked": native_sources_checked,
    }


def _iter_colon_suffix_probes(cand: str, *, max_tails: int = 16) -> list[str]:
    s = str(cand or "").strip()
    if not s:
        return []
    probes: list[str] = [s]
    if ":" in s:
        pos = 0
        while len(probes) <= max_tails:
            idx = s.find(":", pos)
            if idx < 0:
                break
            pos = idx + 1
            tail = s[pos:].strip()
            if len(tail) >= 2 and tail not in probes:
                probes.append(tail)
        last_tail = s.rsplit(":", 1)[1].strip()
        if len(last_tail) >= 2 and last_tail not in probes:
            probes.append(last_tail)
    return probes


def _candidate_hits_barrier(cand: str, barrier: Mapping[str, Any]) -> bool:
    probes = _iter_colon_suffix_probes(cand)
    if not probes:
        return False
    blocked = barrier.get("blocked_ids") or ()
    tomb_digs = barrier.get("tombstone_digests") or ()
    opaque_toks = barrier.get("opaque_tokens") or ()
    vc_digs = barrier.get("vc_sealed_digests") or ()
    hmac_hex = str(barrier.get("hmac_key_hex") or "")

    for item in probes:
        if item in blocked:
            return True
        if hmac_hex and (tomb_digs or opaque_toks):
            if _opaque_source_token(hmac_hex, item) in opaque_toks:
                return True
            if tomb_digs:
                for dom in ("source", "artifact", "derivation", "memory"):
                    if _hmac_tombstone_digest(hmac_hex, dom, item) in tomb_digs:
                        return True
        if vc_digs:
            for vc_prefix in ("nyairo-vc-seal-v1:", "vc:"):
                vc_dig = hashlib.sha256(f"{vc_prefix}{item}".encode("utf-8")).hexdigest()
                if vc_dig in vc_digs:
                    return True
    return False


def is_entity_forget_blocked(entity: Any, barrier: Mapping[str, Any]) -> bool:
    """Return True if an item/ID hits any Forget hold, HMAC tombstone, V-C seal, native SQLite authority/controls, or fail-closed integrity barrier."""
    if not isinstance(barrier, Mapping):
        barrier = {}
    if isinstance(entity, str):
        s = entity.strip()
        if not s:
            return False
        if barrier.get("fail_closed"):
            return True
        return _candidate_hits_barrier(s, barrier)
    if not isinstance(entity, Mapping):
        return False
    if entity.get("hold_active") or entity.get("erased"):
        return True
    if str(entity.get("status") or "").upper() == LIFECYCLE_TOMBSTONED:
        return True
    if str(entity.get("lifecycle") or "").upper() == LIFECYCLE_TOMBSTONED:
        return True
    if barrier.get("fail_closed"):
        for gkey in _ENTITY_BARRIER_SCALAR_KEYS:
            val = entity.get(gkey)
            if isinstance(val, str) and val.strip():
                return True
        for lkey in _ENTITY_BARRIER_LIST_KEYS:
            if entity.get(lkey):
                return True
    for key in _ENTITY_BARRIER_SCALAR_KEYS:
        val = entity.get(key)
        if isinstance(val, str) and _candidate_hits_barrier(val, barrier):
            return True
    for list_key in _ENTITY_BARRIER_LIST_KEYS:
        seq = entity.get(list_key)
        if isinstance(seq, Sequence) and not isinstance(seq, (str, bytes)):
            for elem in seq:
                if isinstance(elem, str) and _candidate_hits_barrier(elem, barrier):
                    return True
                elif isinstance(elem, Mapping) and is_entity_forget_blocked(elem, barrier):
                    return True
                elif isinstance(elem, Sequence) and not isinstance(elem, (str, bytes)):
                    parts = [str(p).strip() for p in elem if isinstance(p, str) and str(p).strip()]
                    for part in parts:
                        if _candidate_hits_barrier(part, barrier):
                            return True
                    if len(parts) >= 2 and _candidate_hits_barrier(f"{parts[0]}:{parts[1]}", barrier):
                        return True
    prov = entity.get("provenance")
    if isinstance(prov, Mapping):
        for pv in prov.values():
            if isinstance(pv, str) and _candidate_hits_barrier(pv, barrier):
                return True
    return False


def filter_collection_by_barrier(
    items: Any,
    barrier: Mapping[str, Any],
    *,
    _depth: int = 0,
) -> Any:
    """Filter a list/sequence or nested mapping through the unified forget barrier.

    When ``barrier.get("fail_closed")`` is True, any sequence/list returns ``[]``.
    """
    if not isinstance(barrier, Mapping):
        return copy.deepcopy(items) if isinstance(items, (list, tuple, Mapping)) else items
    fail_closed = bool(barrier.get("fail_closed"))
    if items is None:
        return None
    if isinstance(items, (list, tuple)):
        if fail_closed:
            return []
        kept: list[Any] = []
        for elem in items:
            if is_entity_forget_blocked(elem, barrier):
                continue
            if _depth < 12 and isinstance(elem, (Mapping, list, tuple)):
                kept.append(filter_collection_by_barrier(elem, barrier, _depth=_depth + 1))
            else:
                kept.append(elem)
        return kept
    if isinstance(items, Mapping):
        if _depth >= 12:
            return dict(items)
        if is_entity_forget_blocked(items, barrier):
            return {}
        out: dict[str, Any] = {}
        for k, v in items.items():
            ks = str(k)
            if _candidate_hits_barrier(ks, barrier):
                continue
            if isinstance(v, (list, tuple)):
                out[k] = filter_collection_by_barrier(v, barrier, _depth=_depth + 1)
            elif isinstance(v, Mapping):
                if is_entity_forget_blocked(v, barrier):
                    continue
                out[k] = filter_collection_by_barrier(v, barrier, _depth=_depth + 1)
            else:
                out[k] = v
        return out
    if isinstance(items, str) and is_entity_forget_blocked(items, barrier):
        return None
    return items


def filter_document_by_barrier(
    doc: Any,
    barrier: Mapping[str, Any],
    *,
    preserve_keys: Iterable[str] = (),
    nullable_entity_keys: Iterable[str] = (),
    _depth: int = 0,
) -> Any:
    """Filter a projected view document through the unified forget barrier while preserving schema metadata keys."""
    if not isinstance(barrier, Mapping):
        return copy.deepcopy(doc) if isinstance(doc, (list, tuple, Mapping)) else doc
    if doc is None or _depth > 12:
        return doc
    preserve = set(preserve_keys)
    nullable_entities = set(nullable_entity_keys)
    if isinstance(doc, (list, tuple)):
        return filter_collection_by_barrier(doc, barrier, _depth=_depth)
    if isinstance(doc, Mapping):
        out: dict[str, Any] = {}
        for k, v in doc.items():
            ks = str(k)
            if ks in preserve:
                out[k] = copy.deepcopy(v)
                continue
            if _candidate_hits_barrier(ks, barrier):
                continue
            if isinstance(v, (list, tuple)):
                out[k] = filter_collection_by_barrier(v, barrier, _depth=_depth + 1)
            elif isinstance(v, Mapping):
                if is_entity_forget_blocked(v, barrier):
                    if ks in nullable_entities:
                        out[k] = None
                    continue
                out[k] = filter_document_by_barrier(
                    v,
                    barrier,
                    preserve_keys=preserve,
                    nullable_entity_keys=nullable_entities,
                    _depth=_depth + 1,
                )
            elif isinstance(v, str) and ks in _ENTITY_BARRIER_SCALAR_KEYS and is_entity_forget_blocked(v, barrier):
                out[k] = None
            else:
                out[k] = v
        return out
    return doc


def read_with_barrier(
    day_root: Path | str,
    *,
    target_kind: str,
    target_id: str,
    query_mode: str = "normal",
    has_subject_capability: bool = True,
    control_root: Path | str | None = None,
) -> tuple[int, dict[str, Any]]:
    """Zero-write Derivation/ReadBarrier across normal and history queries (history never bypasses delete/hold/privacy)."""
    del query_mode  # Normal and history queries enforce the exact same barriers.
    state, state_exists = _load_state_readonly(day_root)
    control, control_exists = _load_control_readonly(day_root, control_root)

    ctrl_ok, ctrl_code = _verify_control_integrity_readonly(state, state_exists, control, control_exists)
    if not ctrl_ok:
        return 503, {"ok": False, "code": ctrl_code, "blocked": True, "llm_calls": 0}

    if target_kind == "source":
        blocked, reason = _source_tombstoned_or_held(state, control, target_id)
        if blocked:
            return 403, {"ok": False, "code": reason, "blocked": True, "llm_calls": 0}
        src = state["sources"].get(target_id)
        if src is None:
            return 404, {"ok": False, "code": "SOURCE_NOT_FOUND", "blocked": True, "llm_calls": 0}
        if not has_subject_capability and src.get("visibility") != "shared":
            return 403, {"ok": False, "code": "PARTNER_PERSPECTIVE_REDACTED", "blocked": True, "llm_calls": 0}
        return 200, {
            "ok": True,
            "code": "READ_ALLOWED",
            "blocked": False,
            "target_kind": "source",
            "target_id": target_id,
            "content": src["content"],
            "revision": src["revision"],
            "generation": src["generation"],
            "llm_calls": 0,
        }

    if target_kind == "derivation":
        node = state["derivations"].get(target_id)
        if node is None:
            return 404, {"ok": False, "code": "DERIVATION_NOT_FOUND", "blocked": True, "llm_calls": 0}
        if node.get("status") == LIFECYCLE_TOMBSTONED or node.get("erased"):
            return 403, {"ok": False, "code": "DERIVATION_TOMBSTONED", "blocked": True, "llm_calls": 0}
        if node.get("hold_active"):
            return 403, {"ok": False, "code": CASE_HOLD_PENDING, "blocked": True, "llm_calls": 0}

        # Check every root_set dependency against local state AND external control ledger tombstones.
        for sid in node.get("root_set") or ():
            blocked, reason = _source_tombstoned_or_held(state, control, str(sid))
            if blocked:
                return 403, {"ok": False, "code": reason, "blocked": True, "root_source_id": sid, "llm_calls": 0}
            if str(sid) not in state["sources"]:
                return 403, {
                    "ok": False,
                    "code": "MISSING_ROOT_DEPENDENCY_ISOLATED",
                    "blocked": True,
                    "root_source_id": sid,
                    "llm_calls": 0,
                }

        if not has_subject_capability and node.get("visibility") != "shared":
            return 403, {"ok": False, "code": "PARTNER_PERSPECTIVE_REDACTED", "blocked": True, "llm_calls": 0}
        return 200, {
            "ok": True,
            "code": "READ_ALLOWED",
            "blocked": False,
            "target_kind": "derivation",
            "target_id": target_id,
            "surface": node["surface"],
            "content": node["content"],
            "generation": node["generation"],
            "llm_calls": 0,
        }

    if target_kind == "artifact":
        art = state["artifacts"].get(target_id)
        if art is None:
            return 404, {"ok": False, "code": "ARTIFACT_NOT_FOUND", "blocked": True, "llm_calls": 0}
        if art.get("lifecycle") == LIFECYCLE_TOMBSTONED:
            return 403, {"ok": False, "code": "ARTIFACT_TOMBSTONED", "blocked": True, "llm_calls": 0}
        if not has_subject_capability and art.get("visibility") != "shared":
            return 403, {"ok": False, "code": "PARTNER_PERSPECTIVE_REDACTED", "blocked": True, "llm_calls": 0}
        head_ver = state["versions"].get(art["current_head"]) or {}
        if head_ver.get("payload_erased") or art.get("content_health") != CONTENT_AVAILABLE:
            return 200, {
                "ok": True,
                "code": "SHELL_ONLY_PAYLOAD_ERASED",
                "blocked": False,
                "target_kind": "artifact",
                "target_id": target_id,
                "version_id": art["current_head"],
                "title": art.get("title", ""),
                "content": "",
                "payload_erased": True,
                "llm_calls": 0,
            }
        return 200, {
            "ok": True,
            "code": "READ_ALLOWED",
            "blocked": False,
            "target_kind": "artifact",
            "target_id": target_id,
            "version_id": art["current_head"],
            "title": art.get("title", ""),
            "content": (head_ver.get("content") or {}).get("body", ""),
            "payload_erased": False,
            "llm_calls": 0,
        }

    return 400, {"ok": False, "code": "INVALID_TARGET_KIND", "blocked": True, "llm_calls": 0}


def project_governance_view(
    day_root: Path | str,
    *,
    has_subject_capability: bool = False,
    control_root: Path | str | None = None,
) -> tuple[int, dict[str, Any]]:
    """Zero-write governance summary projection with strict partner-perspective zero-leak filtering."""
    state, state_exists = _load_state_readonly(day_root)
    control, control_exists = _load_control_readonly(day_root, control_root)
    ctrl_ok, ctrl_code = _verify_control_integrity_readonly(state, state_exists, control, control_exists)

    visible_artifacts: list[dict[str, Any]] = []
    for aid, art in sorted((state.get("artifacts") or {}).items()):
        if not isinstance(art, Mapping):
            continue
        if art.get("lifecycle") == LIFECYCLE_TOMBSTONED:
            continue
        if not has_subject_capability and art.get("visibility") != "shared":
            continue
        visible_artifacts.append(
            {
                "artifact_id": aid,
                "title": art.get("title", ""),
                "lifecycle": art.get("lifecycle"),
                "current_head": art.get("current_head"),
                "visibility": art.get("visibility"),
            }
        )

    out: dict[str, Any] = {
        "ok": ctrl_ok,
        "code": "OK" if ctrl_ok else ctrl_code,
        "schema": SCHEMA_VERSION,
        "llm_calls": 0,
        "control_integrity_ok": ctrl_ok,
        "visible_artifacts": visible_artifacts,
        "sent_count": len(state.get("delivery_outbox") or []),
    }
    if has_subject_capability:
        out["active_holds_count"] = sum(
            1 for h in (state.get("holds") or {}).values() if h.get("status") == CASE_HOLD_PENDING
        )
        out["forget_cases_count"] = len(state.get("forget_cases") or {})
        out["active_leases_count"] = sum(
            1
            for lease in (state.get("leases") or {}).values()
            if not (lease.get("revocation") or {}).get("revoked")
        )
    return (200 if ctrl_ok else 503), out


# ---------------------------------------------------------------------------
# Live Production Representation Lease Enforcement & Zero-Write Health View
# ---------------------------------------------------------------------------

LIVE_REP_LEASE_FILENAME = "representation_leases.json"


def _live_rep_lease_path(day_root: Path | str) -> Path:
    return Path(day_root) / "v02" / LIVE_REP_LEASE_FILENAME


def _normalize_rep_scope(scope: str) -> str:
    clean = str(scope or "").strip().lower()
    if not clean or len(clean) > 64:
        raise ValueError("INVALID_LEASE_SCOPE")
    for ch in clean:
        if not (ch.isalnum() or ch in ("_", "-", ":", ".")):
            raise ValueError("INVALID_LEASE_SCOPE")
    return clean


def _load_live_rep_leases_zero_write(day_root: Path | str) -> tuple[dict[str, Any], bool]:
    path = _live_rep_lease_path(day_root)
    if not path.is_file() or path.is_symlink():
        return {"schema": "nyairo.v02.live_rep_leases.v1", "leases": []}, False
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(doc, Mapping) and isinstance(doc.get("leases"), list):
            return {"schema": str(doc.get("schema") or "nyairo.v02.live_rep_leases.v1"), "leases": list(doc["leases"])}, True
    except Exception:
        pass
    return {"schema": "nyairo.v02.live_rep_leases.v1", "leases": []}, False


def read_lease_health_zero_write(
    day_root: Path | str,
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Strictly zero-write health view over live representation leases and governance leases."""
    doc, live_present = _load_live_rep_leases_zero_write(day_root)
    state, state_exists = _load_state_readonly(day_root)
    now_dt = _parse_dt(_now_iso(now_iso)) or datetime.now(timezone.utc)

    active_count = 0
    expired_count = 0
    revoked_count = 0
    blocked_scopes: list[str] = []
    projected_leases: list[dict[str, Any]] = []

    latest_by_scope: dict[str, dict[str, Any]] = {}
    for row in doc.get("leases") or []:
        if not isinstance(row, Mapping):
            continue
        scope = str(row.get("scope") or "").strip().lower()
        if not scope:
            continue
        revoked = bool(row.get("revoked", False))
        exp_dt = _parse_dt(row.get("expires_at"))
        expired = (not revoked) and (exp_dt is not None and exp_dt <= now_dt)
        status = "revoked" if revoked else ("expired" if expired else "active")
        item = {
            "lease_id": str(row.get("lease_id") or ""),
            "scope": scope,
            "status": status,
            "issued_at": row.get("issued_at"),
            "expires_at": row.get("expires_at"),
            "revoked_at": row.get("revoked_at"),
            "reason_cn": str(row.get("reason_cn") or ""),
        }
        latest_by_scope[scope] = item
        projected_leases.append(item)

    for scope, item in sorted(latest_by_scope.items()):
        st = item["status"]
        if st == "active":
            active_count += 1
        elif st == "expired":
            expired_count += 1
            blocked_scopes.append(scope)
        elif st == "revoked":
            revoked_count += 1
            blocked_scopes.append(scope)

    gov_leases = state.get("leases") if isinstance(state.get("leases"), Mapping) else {}
    for lid, ldoc in sorted(gov_leases.items()):
        if not isinstance(ldoc, Mapping):
            continue
        if (ldoc.get("revocation") or {}).get("revoked"):
            revoked_count += 1
        else:
            exp_dt = _parse_dt(ldoc.get("expires_at"))
            if exp_dt is not None and exp_dt <= now_dt:
                expired_count += 1
            else:
                active_count += 1

    return {
        "present": bool(live_present or state_exists),
        "active_count": active_count,
        "expired_count": expired_count,
        "revoked_count": revoked_count,
        "blocked_scopes": blocked_scopes,
        "leases": projected_leases[-12:],
        "llm_calls": 0,
    }


def enforce_active_representation_lease(
    day_root: Path | str,
    *,
    scope: str,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Zero-write gate checking whether a representation lease blocks ``scope``.

    Backward-compatible: if no lease was issued/recorded for ``scope``, access is allowed
    (``NO_RESTRICTIVE_LEASE``). Once a lease exists for ``scope``, revocation or expiry
    strictly blocks guarded operations.
    """
    norm_scope = _normalize_rep_scope(scope)
    doc, live_present = _load_live_rep_leases_zero_write(day_root)
    if not live_present:
        return {"allowed": True, "reason": "NO_RESTRICTIVE_LEASE", "scope": norm_scope, "lease_id": None}
    now_dt = _parse_dt(_now_iso(now_iso)) or datetime.now(timezone.utc)
    matched: Optional[Mapping[str, Any]] = None
    for row in doc.get("leases") or []:
        if isinstance(row, Mapping) and str(row.get("scope") or "").strip().lower() == norm_scope:
            matched = row
    if matched is None:
        return {"allowed": True, "reason": "NO_RESTRICTIVE_LEASE", "scope": norm_scope, "lease_id": None}
    lid = str(matched.get("lease_id") or "")
    if bool(matched.get("revoked", False)):
        return {
            "allowed": False,
            "reason": "REPRESENTATION_LEASE_REVOKED",
            "scope": norm_scope,
            "lease_id": lid,
            "reason_cn": str(matched.get("reason_cn") or "该范围的表征权限租约已吊销"),
        }
    exp_dt = _parse_dt(matched.get("expires_at"))
    if exp_dt is not None and exp_dt <= now_dt:
        return {
            "allowed": False,
            "reason": "REPRESENTATION_LEASE_EXPIRED",
            "scope": norm_scope,
            "lease_id": lid,
            "reason_cn": "该范围的表征权限租约已到期",
        }
    return {
        "allowed": True,
        "reason": "LEASE_ACTIVE",
        "scope": norm_scope,
        "lease_id": lid,
        "expires_at": matched.get("expires_at"),
    }


def upsert_or_revoke_representation_lease(
    day_root: Path | str,
    *,
    action: str,
    scope: str,
    lease_id: Optional[str] = None,
    ttl_minutes: int = 120,
    reason: str = "",
    actor_side: str = "subject",
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Issue/renew or revoke a live representation lease under ``<day_root>/v02/representation_leases.json``."""
    norm_action = str(action or "").strip().lower()
    if norm_action not in ("issue", "revoke"):
        raise ValueError("INVALID_LEASE_ACTION")
    norm_scope = _normalize_rep_scope(scope)
    ts = _now_iso(now_iso)
    now_dt = _parse_dt(ts) or datetime.now(timezone.utc)
    path = _live_rep_lease_path(day_root)
    with _tx.state_file_lock(path):
        doc, _ = _load_live_rep_leases_zero_write(day_root)
        leases = [dict(r) for r in (doc.get("leases") or []) if isinstance(r, Mapping)]
        if norm_action == "issue":
            bounded_ttl = max(1, min(60 * 24 * 30, int(ttl_minutes)))
            exp_iso = (now_dt + timedelta(minutes=bounded_ttl)).isoformat()
            lid = (
                str(lease_id).strip()[:64]
                if isinstance(lease_id, str) and lease_id.strip()
                else f"rlease_{norm_scope}_{_sha256_text(f'{norm_scope}|{ts}')[:8]}"
            )
            record = {
                "lease_id": lid,
                "scope": norm_scope,
                "issued_by": str(actor_side or "subject").strip().lower(),
                "issued_at": ts,
                "expires_at": exp_iso,
                "revoked": False,
                "revoked_at": None,
                "reason_cn": str(reason or "已签发限时表征租约").strip()[:160],
            }
            leases.append(record)
            leases = leases[-48:]
            doc["leases"] = leases
            doc["updated_at"] = ts
            _tx.atomic_write_json(path, doc, sort_keys=True)
            return {
                "ok": True,
                "action": "issue_representation_lease",
                "lease": record,
                "llm_calls": 0,
            }
        # Revoke: mark latest matching lease revoked, or record an explicit revoked tombstone entry
        target_lid = str(lease_id).strip() if isinstance(lease_id, str) and lease_id.strip() else None
        revoked_rec: Optional[dict[str, Any]] = None
        for row in reversed(leases):
            if str(row.get("scope") or "").strip().lower() != norm_scope:
                continue
            if target_lid and str(row.get("lease_id") or "") != target_lid:
                continue
            row["revoked"] = True
            row["revoked_at"] = ts
            row["revoked_by"] = str(actor_side or "partner").strip().lower()
            if str(reason or "").strip():
                row["reason_cn"] = str(reason).strip()[:160]
            revoked_rec = row
            break
        if revoked_rec is None:
            lid = target_lid or f"rlease_{norm_scope}_revoked_{_sha256_text(f'{norm_scope}|{ts}')[:8]}"
            revoked_rec = {
                "lease_id": lid,
                "scope": norm_scope,
                "issued_by": "preemptive_revoke",
                "issued_at": ts,
                "expires_at": ts,
                "revoked": True,
                "revoked_at": ts,
                "revoked_by": str(actor_side or "partner").strip().lower(),
                "reason_cn": str(reason or "已主动吊销该范围的表征租约").strip()[:160],
            }
            leases.append(revoked_rec)
        leases = leases[-48:]
        doc["leases"] = leases
        doc["updated_at"] = ts
        _tx.atomic_write_json(path, doc, sort_keys=True)
        return {
            "ok": True,
            "action": "revoke_representation_lease",
            "lease": revoked_rec,
            "llm_calls": 0,
        }

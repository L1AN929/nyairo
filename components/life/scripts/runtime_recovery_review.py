"""Assessment of one Life instance root, in two separate entries.

``review()`` is strictly a *reader*.  It never acquires the LR-2 writer lease, never calls
``load_state`` (which would replay a WAL), never creates a directory, never talks to a model
and never writes business data.  It answers from the files the owners already wrote plus the
owners' own read ports, and it says ``UNKNOWN`` where the files alone cannot prove the answer.

``recover()`` is the one explicit, operator-requested *mutating* entry.  It assembles the
existing production runtime on this instance (which takes the real writer leases), calls the
existing ``LifeIntegrationCoordinator.recover_all_and_reconcile(auto_reconcile_unknown_actions=
True)`` and AR-0's own ``reconcile_action`` for anything still ``UNKNOWN``, then closes and
releases.  It never submits, never mints a new operation key, never replays a successful action,
never starts the resident and never calls a model.  It is only reachable through the CLI's
explicit ``--recover`` flag; importing this module changes nothing.

Why it exists (design VIII.13 / Q14-Q45): ``owner commit before Action success`` means a
crash can leave a durable commit intent (WAL) or a fenced-but-unanswered submission.  A normal
restart must be told apart from "there is an unrecovered commit" and from "another live process
holds the writer authority".  Treating the mere presence of ``commit_intent.json`` as a permanent
refusal is wrong, and reading a half-written snapshot behind it is wrong too: the WAL schema and
the journal length together tell which owner committed how far.

Named limits, deliberately not papered over:

* the writer flock is **not** probed (that would take it); the marker file plus a live pid is
  evidence of *held*, never evidence of *free*;
* the process scan is this process's view only, not a system-wide owner inventory;
* a configured model endpoint is a description, not a verified response;
* there is no formal backup/restore or migration entry in these stores (see
  ``backup_restore`` / ``migration`` in the result: ``NOT_IMPLEMENTED``).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

REVIEW_SCHEMA = "chiyo.infra.runtime_recovery_review.v1"

CLASS_ASSEMBLABLE = "ASSEMBLABLE"
CLASS_NEEDS_RECOVERY = "NEEDS_EXISTING_RECOVERY"
CLASS_READ_BLOCKED = "READ_BLOCKED"
CLASS_MANUAL = "MANUAL"
CLASS_UNKNOWN = "UNKNOWN"

RECOVER_SCHEMA = "chiyo.infra.runtime_recovery_recover.v1"

#: Outcome of one explicit recovery run.
STATUS_RECOVERED = "RECOVERED"
STATUS_NOTHING_TO_DO = "NOTHING_TO_DO"
STATUS_PARTIAL = "PARTIAL"
STATUS_STILL_UNKNOWN = "STILL_UNKNOWN"
STATUS_BLOCKED = "BLOCKED"

#: Instance-owned names only.  No model variable is read or echoed by ``recover()``.
MEMORY_DB_ENV = "NYA02_MEMORY_DB"
MEMORY_CONTROLS_DB_ENV = "NYA02_MEMORY_CONTROLS_DB"
MEMORY_SUBJECT_ENV = "NYA02_MEMORY_SUBJECT"

INSTANCE_FILENAME = "instance.json"
INSTANCE_SCHEMA = "life.one_day.v1"
MODEL_ENV_FILENAME = "model_env"
LIFE_DIRNAME = "life"
LIFE_STATE_DIRNAME = "state"
AUTHORITY_MARKER = Path("run") / "life_authority.json"

#: WAL files this instance can legitimately carry, and the schema that names its owner.
#: AG-1 and the AG-2 integration store share the *filename* ``commit_intent.json`` in the same
#: state root; only the schema inside the document says whose commit is pending.
WAL_SPECS = (
    {"path": "action_reality.wal", "schema": "chiyo.action_reality.wal.v1",
     "store": "action_reality", "journal": "action_reality_journal.jsonl"},
    {"path": "commit_intent.json", "schema": "chiyo.agency.decision_commit_intent.v1",
     "store": "agency_decision", "journal": "agency_decision_journal.jsonl"},
    {"path": "commit_intent.json", "schema": "chiyo.agency.life_integration_wal.v1",
     "store": "life_integration", "journal": "life_integration_journal.jsonl"},
)

#: Actions in any of these states still need the owner's own recovery/reconciliation entry.
UNRESOLVED_ACTION_STATES = frozenset({"UNKNOWN", "SUBMITTED", "SCHEDULED", "PREPARED"})


def _read_json(path: Path) -> tuple[bool, Any, Optional[str]]:
    try:
        return True, json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return False, None, "NOT_FOUND"
    except (OSError, ValueError) as exc:
        return False, None, "%s: %s" % (type(exc).__name__, exc)


def _count_lines(path: Path) -> Optional[int]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())
    except FileNotFoundError:
        return 0
    except OSError:
        return None


def _pid_alive(pid: Any) -> Optional[bool]:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    try:
        os.kill(value, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def _life_state_root(root: Path) -> Path:
    return root / LIFE_DIRNAME / LIFE_STATE_DIRNAME


def _instance(root: Path) -> dict[str, Any]:
    path = root / INSTANCE_FILENAME
    ok, data, error = _read_json(path)
    if not ok:
        return {"status": CLASS_READ_BLOCKED, "path": str(path),
                "reason": "INSTANCE_UNREADABLE", "detail": error}
    if not isinstance(data, dict) or data.get("schema_version") != INSTANCE_SCHEMA:
        return {"status": CLASS_READ_BLOCKED, "path": str(path),
                "reason": "INSTANCE_SCHEMA_MISMATCH",
                "observed_schema": (data or {}).get("schema_version")
                if isinstance(data, dict) else None,
                "expected_schema": INSTANCE_SCHEMA}
    return {"status": "OK", "path": str(path), "data": data}


def _config(root: Path, data: Mapping[str, Any]) -> dict[str, Any]:
    """Configuration *description* only: no request, no key, no network."""

    from_env = dict(data.get("environment") or {})
    key_present = bool(str(from_env.get("NYA02_MODEL_API_KEY") or "").strip())
    if not key_present:
        try:
            for line in (root / MODEL_ENV_FILENAME).read_text(encoding="utf-8").splitlines():
                name, _, value = line.partition("=")
                if name.strip() == "NYA02_MODEL_API_KEY" and value.strip():
                    key_present = True
                    break
        except OSError:
            pass
    base_url = str(from_env.get("NYA02_MODEL_BASE_URL") or "").strip()
    model = str(from_env.get("NYA02_MODEL_NAME") or "").strip()
    provider = str(from_env.get("NYA02_MODEL_PROVIDER") or "").strip() or None
    usable = bool(base_url and model and key_present)
    return {
        "usable_for_requests": usable,
        "provider": provider,
        "model": model or None,
        "base_url": base_url or None,
        "api_key_present": key_present,
        "verified_response": False,
        "note": "a description is not a working model; no request is made by this reader",
    }


def _writer(root: Path, state_root: Path) -> dict[str, Any]:
    """Read the authority marker; never take the flock."""

    marker_path = state_root / AUTHORITY_MARKER
    ok, marker, error = _read_json(marker_path)
    if not ok:
        if error == "NOT_FOUND":
            return {"status": CLASS_UNKNOWN, "reason": "NO_AUTHORITY_MARKER",
                    "flock_probed": False, "marker_path": str(marker_path),
                    "note": "an absent marker is not proof the lease is free"}
        return {"status": CLASS_READ_BLOCKED, "reason": "AUTHORITY_MARKER_UNREADABLE",
                "detail": error, "flock_probed": False}
    if not isinstance(marker, dict):
        return {"status": CLASS_READ_BLOCKED, "reason": "AUTHORITY_MARKER_INVALID",
                "flock_probed": False}
    pid = marker.get("pid")
    alive = _pid_alive(pid)
    if alive is True:
        return {"status": CLASS_MANUAL, "reason": "LEASE_MARKED_HELD_BY_LIVE_PROCESS",
                "pid": pid, "instance_id": marker.get("instance_id"),
                "acquired_at": marker.get("acquired_at"),
                "flock_probed": False, "marker_path": str(marker_path)}
    if alive is False:
        return {"status": CLASS_UNKNOWN, "reason": "MARKER_PID_NOT_RUNNING",
                "pid": pid, "flock_probed": False, "marker_path": str(marker_path),
                "note": "stale marker; the flock may still be free or held by another pid"}
    return {"status": CLASS_UNKNOWN, "reason": "MARKER_PID_UNREADABLE",
            "pid": pid, "flock_probed": False, "marker_path": str(marker_path)}


def _wal(state_root: Path) -> dict[str, Any]:
    """Attribute each WAL file to its owner by schema, then compare to the journal.

    This mirrors ``recover_from_wal_if_needed``'s *decision* without performing its writes:
    a WAL is pending when the journal has ``store_revision - 1`` entries; it is already
    applied (snapshot may be behind) when the journal already has ``store_revision`` entries.
    """

    by_path: dict[str, dict[str, Any]] = {}
    for spec in WAL_SPECS:
        path = state_root / spec["path"]
        if not path.exists():
            continue
        ok, wal_doc, error = _read_json(path)
        if not ok or not isinstance(wal_doc, dict):
            by_path.setdefault(str(path), {
                "store": spec["store"], "path": str(path), "status": "UNREADABLE",
                "detail": error})
            continue
        schema = wal_doc.get("schema_version")
        if schema != spec["schema"]:
            # Same filename, another store's schema.  Do not claim it for this store; the
            # file stays recorded and is resolved below (either by the matching spec or as
            # an unknown owner).
            by_path.setdefault(str(path), {
                "store": spec["store"], "path": str(path), "status": "UNMATCHED_SCHEMA",
                "observed_schema": schema, "expected_schema": spec["schema"]})
            continue
        try:
            target = int(wal_doc.get("store_revision"))
        except (TypeError, ValueError):
            by_path[str(path)] = {"store": spec["store"], "path": str(path),
                                  "status": "NO_STORE_REVISION"}
            continue
        entries = _count_lines(state_root / spec["journal"])
        if entries is None:
            by_path[str(path)] = {"store": spec["store"], "path": str(path),
                                  "status": "JOURNAL_UNREADABLE", "store_revision": target}
            continue
        if entries == target - 1:
            status = "PENDING_COMMIT"
        elif entries >= target:
            status = "ALREADY_IN_JOURNAL"
        else:
            status = "JOURNAL_AHEAD"
        by_path[str(path)] = {"store": spec["store"], "path": str(path), "status": status,
                              "store_revision": target, "journal_entries": entries}
    # A file whose schema matched no known owner is not "absent": it is an unresolved file.
    for path, row in by_path.items():
        if row.get("status") == "UNMATCHED_SCHEMA":
            row = dict(row)
            row["status"] = "UNKNOWN_SCHEMA"
            by_path[path] = row
    rows = list(by_path.values())
    pending = [row for row in rows
               if row.get("status") in {"PENDING_COMMIT", "ALREADY_IN_JOURNAL"}]
    unknown = [row for row in rows
               if row.get("status") in {"UNREADABLE", "NO_STORE_REVISION",
                                        "JOURNAL_UNREADABLE", "JOURNAL_AHEAD",
                                        "UNKNOWN_SCHEMA"}]
    return {"present": bool(rows), "rows": rows, "pending": pending, "unknown": unknown}


def _actions(state_root: Path) -> dict[str, Any]:
    """Read AR-0's snapshot directly (never ``load_state``, which replays the WAL)."""

    state_path = state_root / "action_reality_state.json"
    ok, state, error = _read_json(state_path)
    if not ok and error == "NOT_FOUND":
        return {"state_present": False, "actions": [], "open_fences": [],
                "unresolved": []}
    if not ok or not isinstance(state, dict):
        return {"state_present": True, "unreadable": True, "detail": error,
                "actions": [], "open_fences": [], "unresolved": []}
    actions = dict(state.get("actions") or {})
    fences = dict(state.get("open_submission_intents") or {})
    rows: list[dict[str, Any]] = []
    for action_id in sorted(set(actions) | set(fences)):
        act = dict(actions.get(action_id) or {})
        fence = dict(fences.get(action_id) or {})
        status = str(act.get("status") or "")
        if not fence and status not in UNRESOLVED_ACTION_STATES:
            continue
        rows.append({
            "action_id": action_id,
            "status": status or None,
            "action_kind": act.get("action_kind"),
            "idempotency_key": act.get("idempotency_key") or fence.get("idempotency_key"),
            "last_attempt_ref": act.get("last_attempt_ref") or fence.get("attempt_id"),
            "open_submission_fence": bool(fence),
            "reconciliation_refs": list(act.get("reconciliation_refs") or []),
        })
    return {"state_present": True, "revision": state.get("revision"),
            "actions": rows, "open_fences": sorted(fences),
            "unresolved": [row for row in rows
                           if row["open_submission_fence"]
                           or row["status"] in UNRESOLVED_ACTION_STATES]}


def _operation_key(idempotency_key: Any) -> Optional[str]:
    """The existing Supply operation mapping, reused rather than re-invented."""

    if not idempotency_key:
        return None
    try:
        from managed_artifact_action_adapter import SupplyManagedArtifactActionAdapter

        return SupplyManagedArtifactActionAdapter.operation_key_for(str(idempotency_key))
    except Exception:
        return None


def _owner_operations(actions: Mapping[str, Any], *, supply_socket: Optional[str],
                      subject: Optional[str]) -> dict[str, Any]:
    """Ask the ManagedArtifact owner what became of each unresolved action's operation.

    This is a read over the owner's own socket; when the socket is missing or silent the
    answer is ``UNKNOWN``/``UNAVAILABLE``, never "nothing happened".
    """

    unresolved = list(actions.get("unresolved") or [])
    if not unresolved:
        return {"checked": 0, "rows": [], "status": "NOTHING_TO_QUERY"}
    if not supply_socket or not subject:
        return {"checked": 0, "status": "NOT_QUERIED",
                "reason": "SUPPLY_SOCKET_OR_SUBJECT_NOT_GIVEN",
                "rows": [{"action_id": row["action_id"],
                          "operation_key": _operation_key(row.get("idempotency_key")),
                          "result": "NOT_QUERIED"} for row in unresolved]}
    rows: list[dict[str, Any]] = []
    try:
        from life_artifact_port import LifeArtifactPort
    except Exception as exc:
        return {"checked": 0, "status": "PORT_UNAVAILABLE",
                "detail": "%s: %s" % (type(exc).__name__, exc), "rows": []}
    try:
        port = LifeArtifactPort(str(supply_socket), str(subject))
    except Exception as exc:
        return {"checked": 0, "status": "PORT_UNAVAILABLE",
                "detail": "%s: %s" % (type(exc).__name__, exc), "rows": []}
    for row in unresolved:
        operation_key = _operation_key(row.get("idempotency_key"))
        entry = {"action_id": row["action_id"], "operation_key": operation_key}
        if not operation_key:
            entry["result"] = "NO_OPERATION_KEY"
            rows.append(entry)
            continue
        try:
            found = port.operation_row(operation_key)
        except Exception as exc:
            entry["result"] = "OWNER_UNQUERYABLE"
            entry["detail"] = "%s: %s" % (type(exc).__name__, exc)
            rows.append(entry)
            continue
        entry["result"] = "OWNER_SAYS_LANDED" if found is not None else "OWNER_SAYS_NO_ROW"
        if found is not None:
            entry["artifact_id"] = found.get("artifact_id")
            entry["version_id"] = found.get("version_id")
            entry["operation_kind"] = found.get("operation_kind")
        rows.append(entry)
    return {"checked": len(rows), "status": "QUERIED", "rows": rows}


def _capability_gates() -> dict[str, Any]:
    """F165/F166: is there a formal backup/restore or migration entry at all?"""

    return {
        "backup_restore": {
            "status": "NOT_IMPLEMENTED",
            "reason": "NO_FORMAL_RESTORE_ENTRY",
            "detail": ("these stores expose per-store WAL recovery only; there is no restore "
                       "entry that takes a backup, a newer deletion/revocation floor, and a "
                       "fresh outside writer epoch. A normal restart is not a backup restore."),
        },
        "migration": {
            "status": "NOT_IMPLEMENTED",
            "reason": "NO_FORMAL_MIGRATION_ENTRY",
            "detail": ("state files carry schema_version, but no entry validates schema/meaning/"
                       "authority before migrating. Reading them by hand is not a migration."),
        },
        "transaction_profile": {
            "single_sqlite_reference": "NOT_THIS_TREE",
            "detail": ("the stores are JSON snapshot + hash-chained journal + WAL files, not the "
                       "single-SQLite reference profile; T1 all-or-nothing is not claimed."),
        },
    }


def review(instance_root: Path | str, *, supply_socket: Optional[str] = None,
           subject: Optional[str] = None) -> dict[str, Any]:
    """Return the read-only recovery assessment.  Creates and changes nothing."""

    root = Path(instance_root).expanduser()
    instance = _instance(root)
    result: dict[str, Any] = {
        "schema_version": REVIEW_SCHEMA,
        "instance": str(root),
        "read_only": True,
        "flock_taken": False,
        "store_reads": "direct snapshot/journal files only (no load_state, no WAL replay)",
    }
    if instance["status"] != "OK":
        result.update({"class": CLASS_READ_BLOCKED, "reason": instance["reason"],
                       "instance_check": instance,
                       "next_legal_entry": {
                           "entry": "operator",
                           "action": "point --instance at a readable life.one_day.v1 root; "
                                     "this reader did not create or repair anything"}})
        return result

    data = instance["data"]
    # Never echo the instance environment: it can carry a model key.  Only key names and the
    # subject are reportable.
    instance_public = {key: value for key, value in instance.items() if key != "data"}
    instance_public["subject_id"] = data.get("subject_id")
    instance_public["environment_keys"] = sorted(str(name) for name in
                                                 (data.get("environment") or {}))
    state_root = _life_state_root(root)
    writer = _writer(root, state_root)
    wal = _wal(state_root)
    actions = _actions(state_root)
    operations = _owner_operations(actions, supply_socket=supply_socket, subject=subject)
    config = _config(root, data)
    gates = _capability_gates()

    # -- classification ----------------------------------------------------------------
    reasons: list[str] = []
    if writer["status"] == CLASS_READ_BLOCKED:
        klass = CLASS_READ_BLOCKED
        reasons.append(writer["reason"])
    elif writer["status"] == CLASS_MANUAL:
        klass = CLASS_MANUAL
        reasons.append(writer["reason"])
    elif wal["unknown"]:
        klass = CLASS_UNKNOWN
        reasons.extend("WAL_" + str(row.get("status")) for row in wal["unknown"])
    elif wal["pending"]:
        klass = CLASS_NEEDS_RECOVERY
        reasons.extend("WAL_PENDING:" + str(row.get("store")) for row in wal["pending"])
    elif actions.get("unreadable"):
        klass = CLASS_READ_BLOCKED
        reasons.append("ACTION_STATE_UNREADABLE")
    elif actions.get("unresolved"):
        klass = CLASS_NEEDS_RECOVERY
        reasons.extend("ACTION_UNRESOLVED:" + str(row["action_id"])
                       for row in actions["unresolved"])
    elif writer["status"] == CLASS_UNKNOWN:
        # No unrecovered commit and no unresolved action, but the writer state cannot be proven
        # without taking the flock.  This does not block a normal start; it is named as unknown.
        klass = CLASS_ASSEMBLABLE
        reasons.append("WRITER_" + str(writer["reason"]))
    else:
        klass = CLASS_ASSEMBLABLE

    if klass == CLASS_ASSEMBLABLE:
        next_entry = {
            "entry": "normal_start",
            "action": ("start the instance through the existing user entry (for the phone/Supply "
                       "tree: scripts/nyairo_day.py start --root <root>; for a fast day: "
                       "scripts/fast_day.py start ...). No recovery call is required by the files."),
        }
    elif klass == CLASS_NEEDS_RECOVERY:
        next_entry = {
            "entry": "existing_recovery_entry",
            "action": ("assemble the real runtime on this root and call the existing "
                       "LifeIntegrationCoordinator.recover_all_and_reconcile("
                       "auto_reconcile_unknown_actions=True); it runs AG-1 recover, LR-3/LR-4 "
                       "cold start, AR-0 recover (fence -> UNKNOWN) and owner reconciliation. "
                       "Do not mint a new operation key and do not replay a successful action."),
        }
    elif klass == CLASS_MANUAL:
        next_entry = {
            "entry": "operator_stop",
            "action": ("another live process holds the Life writer authority; stop it through its "
                       "own entry (nyairo_day.py stop / fast_day.py stop), then re-read this root. "
                       "This reader did not take the lock and cannot release it."),
        }
    elif klass == CLASS_READ_BLOCKED:
        next_entry = {
            "entry": "operator_inspect",
            "action": "a state file is unreadable or has an unexpected schema; stop and inspect "
                      "the named file. Do not delete it to 'unblock' a start.",
        }
    else:
        next_entry = {
            "entry": "operator_inspect",
            "action": ("the durable files do not decide this case (see reasons); inspect the named "
                       "WAL/journal before starting. This reader did not replay or repair it."),
        }

    result.update({
        "class": klass,
        "reasons": reasons,
        "instance_check": instance_public,
        "state_root": str(state_root),
        "state_root_exists": state_root.is_dir(),
        "writer": writer,
        "wal": wal,
        "actions": actions,
        # When a commit intent is still pending, the snapshot was written before it: it is a
        # lower bound, not the current truth.  Say so instead of presenting it as authoritative.
        "snapshot_may_be_stale": bool(wal["pending"]),
        "owner_operations": operations,
        "config": config,
        "gates": gates,
        "next_legal_entry": next_entry,
        "unevaluated": [
            "the writer flock was not taken; 'free' is not claimed",
            "process scan is this process's own view, not a system-wide owner inventory",
            "model response was not verified (configuration description only)",
            "owner operation query requires --supply-socket and --subject; otherwise NOT_QUERIED",
            "backup/restore and migration entries do not exist in this tree",
        ],
    })
    return result


# ---------------------------------------------------------------------------------------------
# Explicit recovery (the only mutating entry in this module)
# ---------------------------------------------------------------------------------------------


def _bounded(exc: BaseException, limit: int = 300) -> str:
    return ("%s: %s" % (type(exc).__name__, str(exc)))[:limit]


def _recover_config(data: Mapping[str, Any], env: Mapping[str, str], *,
                    supply_socket: Optional[str], subject: Optional[str]) -> dict[str, Any]:
    """The instance's own bindings, with the same explicit operator overrides as ``review``.

    Only names the instance/owner already owns are consulted; model variables are not read.
    Memory paths may come from the instance environment or the caller's environment, exactly
    as the existing fixture/assembly wiring does.
    """

    def pick(*names: str) -> Optional[str]:
        for name in names:
            value = str(env.get(name) or "").strip()
            if value:
                return value
            value = str(os.environ.get(name) or "").strip()
            if value:
                return value
        return None

    return {
        "supply_socket": str(supply_socket or data.get("supply_socket")
                             or pick("NYAIRO_LIFE_SUPPLY_SOCKET", "LIFE_SUPPLY_SOCKET",
                                     "CHIYO_NATIVE_LIFE_SUPPLY_SOCKET") or "").strip() or None,
        "subject": str(subject or data.get("subject_id")
                       or pick("NYAIRO_LIFE_SUBJECT", "NYA02_SUBJECT") or "").strip() or None,
        "workspace_id": str(data.get("workspace_id")
                            or pick("NYAIRO_LIFE_WORKSPACE_ID",
                                    "CHIYO_LIFE_SUPPLY_WORKSPACE_ID") or "").strip() or None,
        "grant_id": str(data.get("artifact_grant_id")
                        or pick("NYAIRO_ARTIFACT_GRANT",
                                "CHIYO_LIFE_SUPPLY_ARTIFACT_GRANT") or "").strip() or None,
        "memory_db": pick(MEMORY_DB_ENV),
        "memory_controls_db": pick(MEMORY_CONTROLS_DB_ENV),
        "memory_subject": pick(MEMORY_SUBJECT_ENV) or "chiyo",
    }


def _action_operation_key(adapter: Any, action: Mapping[str, Any]) -> Optional[str]:
    idem = str(action.get("idempotency_key") or "")
    if not idem:
        return None
    try:
        return str(adapter.operation_key_for(idem))
    except Exception:
        return None


def _artifact_versions(port: Any, artifact_id: Any) -> Optional[int]:
    if not artifact_id:
        return None
    try:
        return int(port.list_versions(str(artifact_id), limit=1)["total_versions"])
    except Exception:
        return None


def _activity_snapshot(runtime: Any, action: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    ref = str(action.get("activity_ref") or "")
    if not ref:
        return None
    try:
        row = dict(runtime.activity_read.get_activity(ref) or {})
    except Exception:
        return None
    return {"activity_id": row.get("activity_id") or ref,
            "status": row.get("status"), "revision": row.get("revision")}


def _delivery_summary(recovery: Mapping[str, Any], consumer: Any) -> tuple[dict[str, Any],
                                                                          dict[str, Any]]:
    dispatch = dict(recovery.get("handoff_dispatch") or {})
    deliveries = [dict(row) for row in dispatch.get("deliveries") or []]
    dispositions: dict[str, int] = {}
    for row in deliveries:
        code = str(row.get("consumer_disposition") or row.get("status") or "UNKNOWN")
        dispositions[code] = dispositions.get(code, 0) + 1
    result_delivery = {
        "dispatched": len(deliveries),
        "delivered": sum(1 for row in deliveries if row.get("status") == "DELIVERED"),
        "deferred": sum(1 for row in deliveries if row.get("status") == "DEFERRED"),
        "failed_pending_retry": sum(1 for row in deliveries
                                    if row.get("status") == "FAILED_PENDING_RETRY"),
        "consumer_dispositions": dispositions,
        "pending_handoff_ids": [str(item) for item in
                                (dispatch.get("pending_handoff_ids") or [])][:20],
    }
    ar1 = dict(recovery.get("ar1_recovery") or {})
    consequence_delivery = {
        "settlements": ar1.get("settlement_count"),
        "outbox_events": ar1.get("outbox_event_count"),
        "pending_outbox": len(ar1.get("pending_outbox_event_ids") or []),
        "conflicts": ar1.get("conflict_count"),
    }
    memory = {
        "status": "WIRED" if consumer is not None else "NOT_CONFIGURED",
        "accepted": getattr(consumer, "accepted", None) if consumer is not None else None,
        "duplicates": getattr(consumer, "duplicates", None) if consumer is not None else None,
        "deferred": getattr(consumer, "deferred", None) if consumer is not None else None,
        "ignored": getattr(consumer, "ignored", None) if consumer is not None else None,
    }
    return result_delivery, {"consequence_delivery": consequence_delivery, "memory": memory}


def _handoff_state(result_delivery: Mapping[str, Any]) -> dict[str, Any]:
    """Summarise the experience-handoff dispatch that this recovery actually performed.

    A pending handoff is *work*: the same entry must not answer ``NOTHING_TO_DO`` merely
    because every Action is already SUCCEEDED.  Only the whitelisted counts/ids are kept.
    """

    dispatched = int(result_delivery.get("dispatched") or 0)
    delivered = int(result_delivery.get("delivered") or 0)
    deferred = int(result_delivery.get("deferred") or 0)
    failed = int(result_delivery.get("failed_pending_retry") or 0)
    pending = [str(item) for item in (result_delivery.get("pending_handoff_ids") or [])][:20]
    attempted = bool(dispatched or pending)
    settled = (attempted and not pending and not deferred and not failed
               and delivered == dispatched)
    return {
        "attempted": attempted,
        "dispatched": dispatched,
        "delivered": delivered,
        "deferred": deferred,
        "failed_pending_retry": failed,
        "pending_handoff_ids": pending,
        "settled": settled,
    }


def _decide_status(action_status: str, handoff: Mapping[str, Any]) -> str:
    """Combine Action reconciliation with the experience-handoff outcome.

    ``NOTHING_TO_DO``/``RECOVERED`` from the Action side must not hide a handoff that was
    retried but deferred, failed or is still pending in the original identity.
    """

    if not handoff.get("attempted"):
        return action_status
    if action_status == STATUS_NOTHING_TO_DO:
        return STATUS_RECOVERED if handoff.get("settled") else STATUS_PARTIAL
    if action_status == STATUS_RECOVERED and not handoff.get("settled"):
        return STATUS_PARTIAL
    return action_status


def _domain_state(delivery: Mapping[str, Any]) -> str:
    """Classify one AR-1 consumer delivery without hiding a policy refusal as a failure."""
    status = str(delivery.get("status") or "")
    disposition = delivery.get("consumer_disposition")
    error = str(delivery.get("last_error_ref") or "")
    if status == "DELIVERED_TO_CONSUMER":
        if disposition == "ACCEPT":
            return "CONSUMED"
        if disposition == "IGNORE":
            return "IGNORED"
        return str(disposition or status)
    if status == "DELIVERY_FAILED":
        if "ProductionExecutionRefused" in error:
            return "POLICY_REFUSED"
        return "FAILED"
    return status or "UNKNOWN"


def _consequence_projection(runtime: Any, rows: Sequence[Mapping[str, Any]], consumer: Any,
                            ar1_recovery: Mapping[str, Any],
                            handoff_dispatch: Mapping[str, Any]) -> dict[str, Any]:
    """Read-only per-domain consequence projection for the actions this recovery touched.

    It never writes and never re-dispatches: it answers which consumer domains actually took
    the settlement, which ones refused by production policy, whether LR-5 bound the original
    settlement, and why a Memory experience handoff does or does not exist.
    """
    settlement_read = getattr(runtime, "settlement_read", None)
    progress_read = getattr(runtime, "progress_read", None)
    read_service = getattr(runtime, "read_service", None)
    events: list[dict[str, Any]] = []
    activity_refs: list[str] = []
    for row in rows:
        action_id = str(row.get("action_id") or "")
        if not action_id or settlement_read is None:
            continue
        settlement = settlement_read.get_settlement(action_id)
        if not settlement:
            events.append({"action_id": action_id, "settlement_ref": None,
                           "state": "NO_SETTLEMENT", "deliveries": []})
            continue
        settlement_id = str(settlement.get("settlement_id") or "")
        outbox = list(settlement_read.list_outbox_events(action_id=action_id) or [])
        activity_ref = settlement.get("activity_ref") or (
            outbox[-1].get("activity_ref") if outbox else None)
        if activity_ref and str(activity_ref) not in activity_refs:
            activity_refs.append(str(activity_ref))
        life_refs: list[str] = []
        if progress_read is not None and activity_ref:
            try:
                life_refs = list(progress_read.list_consumed_settlement_refs(activity_ref) or [])
            except Exception:
                life_refs = []
        deliveries: list[dict[str, Any]] = []
        domain_states: dict[str, str] = {}
        for event in outbox:
            event_id = str(event.get("event_id") or "")
            for delivery in settlement_read.get_delivery_status(event_id) or []:
                state = _domain_state(delivery)
                deliveries.append({
                    "event_id": event_id,
                    "domain": str(delivery.get("consumer_domain") or ""),
                    "status": str(delivery.get("status") or ""),
                    "disposition": delivery.get("consumer_disposition"),
                    "state": state,
                    "last_error_ref": delivery.get("last_error_ref"),
                    "attempt": delivery.get("attempt"),
                })
                if state not in {"POLICY_REFUSED", "FAILED"}:
                    domain_states[str(delivery.get("consumer_domain") or "")] = state
                else:
                    domain_states.setdefault(
                        str(delivery.get("consumer_domain") or ""), state)
        events.append({
            "action_id": action_id,
            "settlement_ref": settlement_id,
            "settlement_status": settlement.get("settlement_status"),
            "outcome_kind": settlement.get("outcome_kind"),
            "activity_ref": activity_ref,
            "owner_artifact_id": row.get("artifact_id"),
            "life_consumed": bool(settlement_id and settlement_id in life_refs),
            "life_consumed_settlement_refs": life_refs,
            "consequence_domains": domain_states,
            "deliveries": deliveries,
        })

    handoffs: list[dict[str, Any]] = []
    activity_status: dict[str, Any] = {}
    if read_service is not None:
        for activity_ref in activity_refs:
            try:
                handoffs.extend(read_service.list_experience_handoffs(
                    activity_id=activity_ref) or [])
            except Exception:
                pass
            activity_read = getattr(runtime, "activity_read", None)
            if activity_read is not None:
                try:
                    row = dict(activity_read.get_activity(activity_ref) or {})
                    activity_status[activity_ref] = row.get("status")
                except Exception:
                    activity_status[activity_ref] = None
    handoff_deliveries = [dict(item) for item in
                          (handoff_dispatch.get("deliveries") or [])]
    existing = len(handoffs)
    dispatched_now = len(handoff_deliveries)
    if dispatched_now:
        handoff_reason = "本次恢复投递了 %d 条经历交接（状态见下）" % dispatched_now
    elif existing:
        handoff_reason = ("该 Activity 已有 %d 条经历交接，本次恢复没有新增/重投" % existing)
    elif activity_status and all(str(status or "") in {"ACTIVE", "WAITING"}
                                 for status in activity_status.values()):
        handoff_reason = ("未生成经历交接：Activity 仍 ACTIVE/WAITING，既有合同只在 "
                          "COMPLETED/ABANDONED/CONFLICT 生成")
    elif activity_status:
        handoff_reason = "Activity 已终态但本次恢复没有生成经历交接，需按既有合同核查"
    else:
        handoff_reason = "本次恢复没有可关联的 Activity"

    accepted = getattr(consumer, "accepted", None) if consumer is not None else None
    if consumer is None:
        memory_reason = "Memory 未配置，本次恢复没有准入通道"
    elif dispatched_now == 0 and existing:
        memory_reason = ("本次恢复没有新的经历交接可准入；既有交接的准入状态不在本次进程计数中，"
                         "accepted=0 不等于 AR1 memory 通知成功或失败")
    elif dispatched_now == 0:
        memory_reason = "没有经历交接可准入（上游未生成），accepted 为 0 不是写入拒绝"
    elif not accepted:
        memory_reason = "有经历交接但本次恢复没有被 Memory 准入，状态见 memory 计数"
    else:
        memory_reason = "经历交接已由 Memory 准入，accepted 计数见 memory"

    refused_domains = sorted({str(item.get("domain"))
                              for event in events
                              for item in event.get("deliveries") or []
                              if item.get("state") == "POLICY_REFUSED"})
    failed_domains = sorted({str(item.get("domain"))
                             for event in events
                             for item in event.get("deliveries") or []
                             if item.get("state") == "FAILED"})
    pending = list(ar1_recovery.get("pending_outbox_event_ids") or [])
    if pending and refused_domains and not failed_domains:
        pending_reason = ("未清零事件被策略拒绝域（%s）保持 pending：生产 RefusingSettlementConsumer "
                          "按设计拒写，不是漏投；已消费域见各事件" % ",".join(refused_domains))
    elif pending:
        pending_reason = "仍有投递失败域（%s）保持 pending，属于真实待处理" % ",".join(
            failed_domains or refused_domains or ["?"])
    else:
        pending_reason = None

    return {
        "events": events,
        "handoff": {
            "existing_handoff_events": existing,
            "dispatched_now": dispatched_now,
            "delivered": sum(1 for row in handoff_deliveries
                             if row.get("status") == "DELIVERED"),
            "failed_pending_retry": sum(1 for row in handoff_deliveries
                                        if row.get("status") == "FAILED_PENDING_RETRY"),
            "pending_handoff_ids": list(handoff_dispatch.get("pending_handoff_ids") or [])[:20],
            "reason": handoff_reason,
        },
        "memory_admission": {
            "status": "WIRED" if consumer is not None else "NOT_CONFIGURED",
            "accepted": accepted,
            "reason": memory_reason,
        },
        "summary": {
            "life_consumed_settlements": sum(1 for event in events
                                             if event.get("life_consumed")),
            "policy_refused_domains": refused_domains,
            "failed_domains": failed_domains,
            "pending_outbox": len(pending),
            "pending_reason": pending_reason,
        },
    }


def recover(instance_root: Path | str, *, supply_socket: Optional[str] = None,
            subject: Optional[str] = None) -> dict[str, Any]:
    """Explicitly recover this instance through the existing entries, then release.

    The default (importing or calling ``review``) remains pure read.  This function is only
    called when an operator asks for it.  It:

    1. refuses before assembly when the instance is unreadable, the owner binding is
       incomplete, or the read-only pre-check already says MANUAL / READ_BLOCKED;
    2. assembles the real production runtime on this instance (taking the real lease) and
       builds the same real Supply adapter the fixture uses;
    3. calls ``recover_all_and_reconcile(auto_reconcile_unknown_actions=True)`` and then AR-0's
       own ``reconcile_action`` for every action still ``UNKNOWN`` -- the original operation
       only, no submit, no new key, no model request;
    4. reports a whitelist: action / operation ids, confirmed / still-unknown / blocked,
       result and consequence delivery status, and whether the lease was released.

    An already-SUCCEEDED Action is not "nothing to do" when the same entry retried an
    experience handoff: a handoff still deferred / FAILED_PENDING_RETRY keeps the original
    pending id and makes the status ``PARTIAL`` rather than ``NOTHING_TO_DO``/``RECOVERED``.

    If the owner cannot be queried the action stays ``STILL_UNKNOWN``; if another writer holds
    the lease, assembly refuses before any business write.
    """

    root = Path(instance_root).expanduser()
    result: dict[str, Any] = {
        "schema_version": RECOVER_SCHEMA,
        "instance": str(root),
        "pid": os.getpid(),
        "mode": "RECOVER",
        "read_only": False,
        "review_entry_unchanged": True,
        "writes": ("existing recovery entries only: store WAL recovery, LR cold start, "
                   "AR-0 fence -> UNKNOWN, original-operation reconciliation; no submit, "
                   "no new operation key, no Decision, no model request"),
    }
    instance = _instance(root)
    if instance["status"] != "OK":
        result.update({"status": STATUS_BLOCKED, "reason": instance["reason"],
                       "detail": instance.get("detail"),
                       "observed_schema": instance.get("observed_schema")})
        return result

    data = instance["data"]
    cfg = _recover_config(data, dict(data.get("environment") or {}),
                          supply_socket=supply_socket, subject=subject)
    result["owner"] = {"subject_id": cfg["subject"], "workspace_id": cfg["workspace_id"],
                       "supply_socket_configured": bool(cfg["supply_socket"]),
                       "grant_present": bool(cfg["grant_id"]),
                       "memory_configured": bool(cfg["memory_db"])}
    missing = [name for name in ("supply_socket", "subject", "workspace_id", "grant_id")
               if not cfg[name]]
    if missing:
        result.update({"status": STATUS_BLOCKED, "reason": "OWNER_CONFIG_INCOMPLETE",
                       "missing": missing,
                       "next": "point the instance at its own owner bindings (or pass "
                               "--supply-socket/--subject); nothing was assembled or written"})
        return result

    pre = review(root, supply_socket=cfg["supply_socket"], subject=cfg["subject"])
    result["precheck"] = {"class": pre.get("class"), "reasons": list(pre.get("reasons") or [])}
    if pre.get("class") in {CLASS_MANUAL, CLASS_READ_BLOCKED}:
        result.update({"status": STATUS_BLOCKED,
                       "reason": "PRECHECK_" + str(pre.get("class")),
                       "detail": list(pre.get("reasons") or []),
                       "next": "stop the other writer / inspect the unreadable file; this "
                               "entry did not take the lease or write"})
        return result

    state_root = _life_state_root(root)
    runtime = None
    adapter = None
    consumer = None
    port = None
    released: Optional[bool] = None
    release_error: Optional[str] = None
    blocked_actions: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    recovery: dict[str, Any] = {}
    try:
        try:
            import life_artifact_port as lap
            import life_runtime_production as lrp
            import managed_artifact_action_adapter as maaa
            import life_integration_ag2 as ag2

            port = lap.LifeArtifactPort(str(cfg["supply_socket"]), str(cfg["subject"]))
            adapter = maaa.SupplyManagedArtifactActionAdapter(
                port, grant_id=str(cfg["grant_id"]),
                workspace_id=str(cfg["workspace_id"]))
            if cfg["memory_db"]:
                from life_experience_memory_consumer import MemoryAdmissionExperienceConsumer
                from life_experience_port import LifeExperiencePort

                memory_port = LifeExperiencePort(
                    str(cfg["memory_db"]),
                    controls_db=(str(cfg["memory_controls_db"])
                                 if cfg["memory_controls_db"] else None),
                    subject=str(cfg["memory_subject"]))
                consumer = MemoryAdmissionExperienceConsumer(port=memory_port, enabled=True)
            switches = ag2.LifeRuntimeKillSwitches(
                life_runtime_enabled=True, agency_enabled=True,
                action_execution_enabled=True, proactive_enabled=False)
            runtime = lrp.build_production_life_runtime(
                state_root, kill_switches=switches, install_root=root / "life",
                artifact_action_adapter=adapter, experience_consumer=consumer)
        except Exception as exc:
            result.update({"status": STATUS_BLOCKED, "reason": "RUNTIME_ASSEMBLY_REFUSED",
                           "detail": _bounded(exc),
                           "next": "another writer may hold the lease, or a state file is "
                                   "unreadable; this entry wrote no business data"})
            return result

        result["writer"] = {"lease_acquired": True, "marker_flock_probed": True}
        actions_before = {str(row.get("action_id")): dict(row)
                          for row in runtime.action_read.list_actions()}
        unresolved_before = sorted(
            aid for aid, row in actions_before.items()
            if str(row.get("status")) in UNRESOLVED_ACTION_STATES)
        versions_before = {
            aid: _artifact_versions(port, (actions_before[aid].get("frozen_payload") or {})
                                    .get("artifact_id"))
            for aid in unresolved_before}

        recovery_failed = False
        try:
            recovery = dict(runtime.coordinator.recover_all_and_reconcile(
                auto_reconcile_unknown_actions=True) or {})
        except Exception as exc:
            recovery_failed = True
            result.update({"status": STATUS_BLOCKED, "reason": "RECOVERY_ENTRY_FAILED",
                           "detail": _bounded(exc)})

        if not recovery_failed:
            # AR-0's own entry for UNKNOWN actions the integration recovery did not own.  It
            # asks the adapter about the *original* operation key; this entry never submits.
            extra_reconciled: list[dict[str, Any]] = []
            for aid in sorted(aid for aid, row in actions_before.items()
                              if str(row.get("status")) == "UNKNOWN"):
                current = runtime.action_read.get_action(aid) or {}
                if str(current.get("status")) != "UNKNOWN":
                    continue
                try:
                    outcome = runtime.action_command.reconcile_action(action_id=aid)
                    rec = dict((outcome or {}).get("reconciliation") or {})
                    extra_reconciled.append({"action_id": aid, "result": rec.get("result"),
                                             "resolved_status": rec.get("resolved_status")})
                except Exception as exc:
                    blocked_actions.append({"action_id": aid, "reason": _bounded(exc)})
            result["extra_reconciled"] = extra_reconciled

            changed = set(unresolved_before)
            for aid in sorted(actions_before):
                after = runtime.action_read.get_action(aid) or {}
                if str(after.get("status")) != str(actions_before[aid].get("status")):
                    changed.add(aid)
                if str(after.get("status")) in UNRESOLVED_ACTION_STATES:
                    changed.add(aid)
            for aid in sorted(changed):
                after = runtime.action_read.get_action(aid) or {}
                payload = dict(after.get("frozen_payload") or {})
                recons = list(runtime.action_read.list_reconciliations(aid) or [])
                last = dict(recons[-1] or {}) if recons else {}
                status_after = str(after.get("status") or "")
                reconciliation_result = last.get("result")
                if (status_after == "SUCCEEDED"
                        and reconciliation_result == "CONFIRMED_SUCCEEDED"):
                    delivery = "CONFIRMED_BY_OWNER"
                elif status_after == "UNKNOWN":
                    delivery = "STILL_UNKNOWN"
                elif status_after in {"SUCCEEDED", "FAILED"}:
                    delivery = "RESOLVED_WITHOUT_OWNER_CONFIRMATION"
                else:
                    delivery = "NOT_APPLICABLE"
                rows.append({
                    "action_id": aid,
                    "action_kind": after.get("action_kind"),
                    "operation_key": _action_operation_key(adapter, after),
                    "status_before": actions_before.get(aid, {}).get("status"),
                    "status_after": status_after or None,
                    "reconciliation_result": reconciliation_result,
                    "reconciliation_strategy": last.get("strategy"),
                    "result_delivery": delivery,
                    "artifact_id": payload.get("artifact_id"),
                    "artifact_versions_before": versions_before.get(aid),
                    "artifact_versions_after": _artifact_versions(
                        port, payload.get("artifact_id")),
                    "activity": _activity_snapshot(runtime, after),
                })

            confirmed = [row["action_id"] for row in rows
                         if row["result_delivery"] == "CONFIRMED_BY_OWNER"]
            still_unknown = [row["action_id"] for row in rows
                             if row["result_delivery"] == "STILL_UNKNOWN"]
            if not rows:
                status = STATUS_NOTHING_TO_DO
            elif still_unknown and not confirmed:
                status = STATUS_STILL_UNKNOWN
            elif still_unknown:
                status = STATUS_PARTIAL
            else:
                status = STATUS_RECOVERED

            result.update({
                "status": status,
                "confirmed": confirmed,
                "still_unknown": still_unknown,
                "blocked": blocked_actions,
                "actions": rows,
                "recovery": {
                    "llm_calls": int(recovery.get("llm_calls") or 0),
                    "reconciled_integration_count": len(
                        recovery.get("reconciled_integration_ids") or []),
                    "fenced_to_unknown": list(
                        (recovery.get("ar0_recovery") or {}).get("fenced_to_unknown_action_ids")
                        or []),
                },
                "adapter_submit_calls": int(getattr(adapter, "submit_call_count", 0)),
            })
            result_delivery, extra = _delivery_summary(recovery, consumer)
            result["result_delivery"] = result_delivery
            result.update(extra)
            # The same entry also owns the experience-handoff retry: an already-SUCCEEDED
            # Action must not make a deferred/pending handoff look like NOTHING_TO_DO.
            handoff = _handoff_state(result_delivery)
            result["status"] = _decide_status(str(result.get("status") or ""), handoff)
            result["handoff_recovery"] = handoff
            result["consequence_domains"] = _consequence_projection(
                runtime, rows, consumer,
                recovery.get("ar1_recovery") or {},
                recovery.get("handoff_dispatch") or {})
    finally:
        if runtime is not None:
            try:
                runtime.close()
                released = (not bool(getattr(runtime.lease, "held", True))
                            and not bool(getattr(runtime.integration_lease, "held", True)))
            except Exception as exc:
                release_error = _bounded(exc)

    result["writer"] = {"lease_acquired": True, "released": released,
                        "release_error": release_error}
    result["unevaluated"] = [
        "the crash matrix is not exercised; this is the existing recovery entry on one root",
        "handoff delivery is reported as the consumer answered; a receipt is not a Memory row",
        "backup/restore and migration entries still do not exist in this tree",
    ]
    return result

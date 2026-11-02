#!/usr/bin/env python3
"""F-153 operator CLI: the local write entry for explicit forget requests.

The normal form names **the instance**, not a state root and a subject:

    python scripts/forget_request_cli.py --instance <instance root> \
        request --operation-key operator:forget-1 \
        --source-ref artifact:... [--event evt:...] \
        [--hold] [--retention-pending] [--uncovered derived_surface]

    ... status --case forget:...
    ... retry  --case forget:...
    ... list [--limit N]

``--instance`` reads the instance's own nested Life environment (the same loader the phone uses)
and derives: the Memory database, control ledger, retained deletion authority, owner and the
coordination case database.  The caller cannot pass an owner or an "operator" flag, and a
participant/contact identity is never treated as forget authority.

Refused by name (exit 2): a caller-supplied owner, an instance root that is a symlink / not a
directory / owned by another OS user, or a case/authority file inside the instance's own
backup/restore set.  Unavailable (exit 3): the instance has no configured Memory binding, or the
coordination database or authority cannot be opened.  There is no HTTP write route.

The barrier is retained before any payload work; a status of ``BLOCKED`` means it was not
established and nothing is reported as forgotten.
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any, Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "native"),
              str(ROOT / "components" / "native" / "src"),
              str(ROOT / "components" / "supply")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from forget_request import (  # noqa: E402
    BLOCKED,
    ForgetCaseStore,
    ForgetRefused,
    forget_case_path_from_env,
)

EXIT_OK = 0
EXIT_REFUSED = 2
EXIT_UNAVAILABLE = 3


class BindingError(RuntimeError):
    def __init__(self, code: str, detail: str = "", *, exit_code: int = EXIT_UNAVAILABLE) -> None:
        super().__init__("%s: %s" % (code, detail) if detail else code)
        self.code = code
        self.detail = detail
        self.exit_code = int(exit_code)


def _lstat(path: Path) -> Any:
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise BindingError("FORGET_INSTANCE_UNREADABLE",
                           "%s: %s" % (path.name, type(exc).__name__)) from exc


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _validate_local_file(path: Path, label: str) -> None:
    """A retained/config file this operator may rely on: no symlink, own user, a real file.

    A file that does not exist yet is allowed (the store creates it with 0600).  This is the
    same local, non-transferable boundary as the instance-root check.  POSIX mode bits are not
    enforced here because the instance may live on a Windows DrvFs mount where ``chmod`` is not
    authoritative; the file is still created 0600 by its owner.
    """

    info = _lstat(path)
    if info is None:
        return
    if stat.S_ISLNK(info.st_mode):
        raise BindingError("FORGET_RETAINED_PATH_UNSAFE", "%s is a symlink" % label,
                           exit_code=EXIT_REFUSED)
    if not stat.S_ISREG(info.st_mode):
        raise BindingError("FORGET_RETAINED_PATH_UNSAFE", "%s is not a regular file" % label,
                           exit_code=EXIT_REFUSED)
    getuid = getattr(os, "getuid", None)
    if getuid is not None and int(info.st_uid) != int(getuid()):
        raise BindingError("FORGET_RETAINED_PATH_UNSAFE",
                           "%s is owned by another OS user" % label, exit_code=EXIT_REFUSED)


def validate_instance_root(root: Path) -> Path:
    """The operator's own instance directory, never a symlink, never someone else's."""

    info = _lstat(root)
    if info is None:
        raise BindingError("FORGET_INSTANCE_MISSING", "the instance root does not exist")
    if stat.S_ISLNK(info.st_mode):
        raise BindingError("FORGET_INSTANCE_SYMLINK", "the instance root is a symlink",
                           exit_code=EXIT_REFUSED)
    if not stat.S_ISDIR(info.st_mode):
        raise BindingError("FORGET_INSTANCE_NOT_A_DIR", "the instance root is not a directory",
                           exit_code=EXIT_REFUSED)
    getuid = getattr(os, "getuid", None)
    if getuid is not None and int(info.st_uid) != int(getuid()):
        raise BindingError("FORGET_INSTANCE_NOT_OWNER",
                           "the instance root is owned by another OS user", exit_code=EXIT_REFUSED)
    return root.resolve()


def _nested_environment(root: Path) -> Mapping[str, str]:
    from components.phone import life as phone_life

    _validate_local_file(phone_life.nested_life_dir(root) / "instance.json",
                         "instance config")
    try:
        nested = phone_life.read_nested_life(root)
    except Exception as exc:
        raise BindingError("FORGET_INSTANCE_INVALID", type(exc).__name__) from exc
    if nested is None:
        raise BindingError("FORGET_INSTANCE_MISSING",
                           "this root has no one-day/instance.json; prepare it first")
    environment = nested.get("environment") or {}
    if not isinstance(environment, Mapping):
        raise BindingError("FORGET_INSTANCE_INVALID", "the nested environment is not an object")
    return {str(key): str(value) for key, value in environment.items() if value is not None}


def _first(environment: Mapping[str, str], names: tuple[str, ...]) -> str:
    for name in names:
        value = str(environment.get(name) or "").strip()
        if value:
            return value
    return ""


def bind_store(instance: Path, *, case_db: Optional[str] = None,
               environment: Optional[Mapping[str, str]] = None) -> ForgetCaseStore:
    root = validate_instance_root(instance)
    env = dict(environment if environment is not None else _nested_environment(root))
    database = _first(env, ("NYA02_MEMORY_DB", "CHIYO_LIFE_MEMORY_DB"))
    controls = _first(env, ("NYA02_MEMORY_CONTROLS_DB", "CHIYO_LIFE_MEMORY_CONTROLS_DB"))
    authority = _first(env, ("NYA02_MEMORY_AUTHORITY_DB", "CHIYO_LIFE_MEMORY_AUTHORITY_DB"))
    owner = _first(env, ("NYA02_MEMORY_SUBJECT", "NYA02_SUBJECT"))
    if not database or not owner:
        raise BindingError("FORGET_MEMORY_NOT_BOUND",
                           "this instance has no explicit Memory binding; enable it first")
    if not authority:
        raise BindingError("FORGET_AUTHORITY_REQUIRED",
                           "this instance has no retained deletion authority configured")
    # The runtime builds its gate from the instance's own environment.  A CLI that points at a
    # different coordination database would leave the product read path blind to the cases it
    # writes, so a mismatched override is refused instead of silently accepted.
    configured_case = forget_case_path_from_env(env)
    if configured_case is None:
        raise BindingError("FORGET_CASE_DB_REQUIRED",
                           "configure NYA02_FORGET_CASE_DB in the instance environment")
    if case_db:
        override = Path(case_db)
        if override.resolve() != configured_case.resolve():
            raise BindingError("FORGET_CASE_DB_MISMATCH",
                               "the coordination database must match the instance configuration",
                               exit_code=EXIT_REFUSED)
        case_path = override
    else:
        case_path = configured_case
    for label, path in (("case", case_path), ("authority", Path(authority))):
        if _inside(path, root):
            raise BindingError("FORGET_RETAINED_PATH_INSIDE_INSTANCE",
                               "the %s file must live outside the instance's own restore set"
                               % label, exit_code=EXIT_REFUSED)
        _validate_local_file(path, label)
    minimum: Optional[int] = None
    raw = _first(env, ("NYA02_MEMORY_AUTHORITY_MIN_SEQ", "CHIYO_LIFE_MEMORY_AUTHORITY_MIN_SEQ"))
    if raw:
        try:
            minimum = int(raw)
        except ValueError:
            minimum = None
    supply_store = None
    supply_db = _first(env, ("NYA02_SUPPLY_ARTIFACT_DB", "CHIYO_LIFE_SUPPLY_ARTIFACT_DB"))
    if supply_db and Path(supply_db).is_file():
        try:
            from lifesupply.artifact.store import ManagedArtifactStore

            projection_root = _first(env, ("NYA02_SUPPLY_PROJECTION_ROOT",
                                           "CHIYO_LIFE_SUPPLY_PROJECTION_ROOT"))
            # The operator opens the *service's* canonical file.  Taking the owner's real writer
            # fence is what makes that legal: offline it wins the lock and re-asserts the epoch;
            # with a live service it is refused by name instead of writing beside it.
            supply_store = ManagedArtifactStore(
                supply_db, projection_root=projection_root or None,
                fences_root=str(Path(supply_db).parent / "recovery" / "fences"),
                writer_instance="forget-cli:%d" % os.getpid())
        except Exception as exc:
            raise BindingError("FORGET_SUPPLY_OWNER_UNUSABLE",
                               type(exc).__name__) from exc
    store = ForgetCaseStore(case_path, evidence_db=database, controls_db=controls or database,
                            authority_db=authority, owner=owner,
                            conversation="life:" + owner, authority_min_seq=minimum,
                            supply_store=supply_store)
    try:
        store.initialize()
    except Exception as exc:
        raise BindingError("FORGET_CASE_DB_UNREADABLE", type(exc).__name__) from exc
    return store


def _emit(payload: Mapping[str, Any]) -> None:
    print(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True))


def _exit_for(answer: Mapping[str, Any]) -> int:
    return EXIT_UNAVAILABLE if str(answer.get("status") or "") == BLOCKED else EXIT_OK


def cmd_request(store: ForgetCaseStore, args: argparse.Namespace) -> int:
    getuid = getattr(os, "getuid", None)
    answer = store.request(
        operation_key=args.operation_key, source_refs=args.source_ref or [],
        event_ids=args.event or [], retention_pending=bool(args.retention_pending),
        uncovered_surfaces=args.uncovered or [], hold_only=bool(args.hold),
        requesting_owner=store.owner,
        requesting_uid=int(getuid()) if getuid is not None else None)
    _emit({"ok": bool(answer.get("ok")), "status": answer.get("status"),
           "case_id": answer.get("case_id"), "state": answer.get("status"),
           "reason": answer.get("reason"),
           "barrier_seq": answer.get("barrier_seq")})
    return _exit_for(answer)


def cmd_status(store: ForgetCaseStore, args: argparse.Namespace) -> int:
    answer = store.status(args.case)
    _emit({"ok": bool(answer.get("ok")), "status": answer.get("status"),
           "case_id": answer.get("case_id"), "reason": answer.get("reason"),
           "barrier_seq": answer.get("barrier_seq"),
           "owner_acks": answer.get("owner_acks"),
           "uncovered": answer.get("uncovered")})
    return _exit_for(answer) if answer.get("ok") else EXIT_UNAVAILABLE


def cmd_retry(store: ForgetCaseStore, args: argparse.Namespace) -> int:
    answer = store.retry(args.case)
    _emit({"ok": bool(answer.get("ok")), "status": answer.get("status"),
           "case_id": answer.get("case_id"), "reason": answer.get("reason"),
           "barrier_seq": answer.get("barrier_seq")})
    return _exit_for(answer)


def cmd_confirm(store: ForgetCaseStore, args: argparse.Namespace) -> int:
    answer = store.confirm(args.case)
    _emit({"ok": bool(answer.get("ok")), "status": answer.get("status"),
           "case_id": answer.get("case_id"), "reason": answer.get("reason"),
           "barrier_seq": answer.get("barrier_seq")})
    return _exit_for(answer)


def cmd_list(store: ForgetCaseStore, args: argparse.Namespace) -> int:
    _emit({"ok": True, "cases": store.list_cases(limit=args.limit)})
    return EXIT_OK


def cmd_recover(store: ForgetCaseStore, args: argparse.Namespace) -> int:
    """Sweep a recovered copy with the retained authority, then open it by marker.

    This is the operator-facing activation entry: without it the recovered owner objects stay
    closed (reads and new writes both refuse), and the marker names the exact authority head
    that was swept so a later deletion closes the copy again.
    """

    from forget_request import (RECOVERY_STATE_FILE, RecoveryBlocked, RecoveryGate,
                                recovered_controls_identity, recovery_apply)
    from lifesupply.artifact.store import ManagedArtifactStore

    recovery_dir = Path(args.recovery_dir)
    if not recovery_dir.is_dir():
        _emit({"ok": False, "status": "unavailable", "reason": "RECOVERY_DIR_MISSING"})
        return EXIT_UNAVAILABLE
    # Activation binds the barrier's *controls* half, so there is no legal activation that
    # omits it.  Without this refusal the CLI used to write an OPEN marker on evidence alone
    # and the formal runtime accepted it; a missing binding is now named before any sweep.
    if not args.recovered_controls:
        _emit({"ok": False, "status": "rejected", "reason": "RECOVERED_CONTROLS_REQUIRED",
               "detail": "recover must name the control ledger it read"})
        return EXIT_REFUSED
    live_paths = (store.controls_db, store.evidence_db, store.authority_db, store.path)
    # A recovered controls copy is read and bound *before* any owner fence is taken or any body
    # is swept: taking the Supply writer fence is already a write, and finding out afterwards
    # that the ledger was another owner's would mean the sweep ran on an unproven barrier.
    if args.recovered_controls:
        try:
            recovered_controls_identity(
                args.recovered_controls, owner=store.owner, conversation=store.conversation,
                recovery_dir=recovery_dir, recovered_evidence=args.recovered_evidence,
                forbidden_paths=live_paths)
        except RecoveryBlocked as exc:
            _emit({"ok": False, "status": "rejected" if exc.code not in (
                "RECOVERED_CONTROLS_MISSING", "RECOVERED_CONTROLS_UNREADABLE",
                "RECOVERED_CONTROLS_EVIDENCE_UNREADABLE") else "unavailable",
                "reason": exc.code, "detail": exc.detail[:160]})
            return (EXIT_UNAVAILABLE if exc.code in (
                "RECOVERED_CONTROLS_MISSING", "RECOVERED_CONTROLS_UNREADABLE",
                "RECOVERED_CONTROLS_EVIDENCE_UNREADABLE") else EXIT_REFUSED)
    gate = RecoveryGate(recovery_dir / RECOVERY_STATE_FILE, owner=store.owner,
                        conversation=store.conversation, authority_db=store.authority_db,
                        minimum_seq=store.authority_min_seq)
    recovered_supply = None
    if args.recovered_supply:
        recovered_supply = ManagedArtifactStore(
            args.recovered_supply, projection_root=args.recovered_projection or None,
            recovery_gate=gate,
            fences_root=str(Path(args.recovered_supply).parent / "recovery" / "fences"),
            writer_instance="forget-recover-cli:%d" % os.getpid())
    answer = recovery_apply(authority_db=store.authority_db, owner=store.owner,
                           conversation=store.conversation,
                           recovered_evidence=args.recovered_evidence,
                           authority_min_seq=store.authority_min_seq,
                           supply_store=recovered_supply, recovery_gate=gate,
                           recovered_controls=args.recovered_controls,
                           recovery_dir=recovery_dir,
                           forbidden_controls_paths=live_paths)
    _emit({"ok": answer.get("status") in ("RECOVERED_OPEN", "RECOVERY_ONLY_APPLIED"),
           "status": answer.get("status"), "reason": answer.get("reason"),
           "barrier_seq": answer.get("barrier_seq"),
           "erased_events": answer.get("erased_events"),
           "recovered_controls": answer.get("recovered_controls"),
           "projection_reconcile": answer.get("projection_reconcile"),
           "derived": answer.get("derived") or {}, "cases": answer.get("cases") or []})
    return (EXIT_OK if answer.get("status") in ("RECOVERED_OPEN", "RECOVERY_ONLY_APPLIED")
            else EXIT_UNAVAILABLE)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="forget_request_cli")
    parser.add_argument("--instance", required=True,
                        help="the phone/Life instance root this operator owns")
    parser.add_argument("--case-db", default=None,
                        help="the coordination database outside the instance's restore set "
                             "(default: NYA02_FORGET_CASE_DB from the instance environment)")
    sub = parser.add_subparsers(dest="command", required=True)

    request = sub.add_parser("request")
    request.add_argument("--operation-key", required=True, dest="operation_key")
    request.add_argument("--source-ref", action="append", default=[], dest="source_ref")
    request.add_argument("--event", action="append", default=[])
    request.add_argument("--hold", action="store_true",
                         help="record the request without touching any payload")
    request.add_argument("--retention-pending", action="store_true", dest="retention_pending",
                         help="an offline backup of the payload is still retained")
    request.add_argument("--uncovered", action="append", default=[],
                         help="a derived surface this build cannot clean (repeatable)")
    request.set_defaults(handler=cmd_request)

    status = sub.add_parser("status")
    status.add_argument("--case", required=True)
    status.set_defaults(handler=cmd_status)

    retry = sub.add_parser("retry")
    retry.add_argument("--case", required=True)
    retry.set_defaults(handler=cmd_retry)

    confirm = sub.add_parser("confirm",
                             help="explicitly turn a retained hold into the deletion it covers")
    confirm.add_argument("--case", required=True)
    confirm.set_defaults(handler=cmd_confirm)

    listing = sub.add_parser("list")
    listing.add_argument("--limit", type=int, default=20)
    listing.set_defaults(handler=cmd_list)

    recover = sub.add_parser("recover",
                             help="sweep a recovered copy and activate it for reads/new writes")
    recover.add_argument("--recovery-dir", required=True, dest="recovery_dir",
                         help="the recovered copy's own directory (holds the activation marker)")
    recover.add_argument("--recovered-evidence", required=True, dest="recovered_evidence")
    recover.add_argument("--recovered-controls", default=None, dest="recovered_controls",
                         help="the control ledger inside the recovered copy (required: an "
                              "activation without it is refused)")
    recover.add_argument("--recovered-supply", default=None, dest="recovered_supply")
    recover.add_argument("--recovered-projection", default=None, dest="recovered_projection")
    recover.set_defaults(handler=cmd_recover)

    args = parser.parse_args(argv)
    try:
        store = bind_store(Path(args.instance), case_db=args.case_db)
        return int(args.handler(store, args))
    except ForgetRefused as exc:
        _emit({"ok": False, "status": "rejected", "reason": exc.code})
        return EXIT_REFUSED
    except BindingError as exc:
        _emit({"ok": False, "status": "unavailable", "reason": exc.code,
               "detail": exc.detail[:160]})
        return exc.exit_code
    except Exception as exc:  # never echo paths or content
        _emit({"ok": False, "status": "error", "reason": "FORGET_REQUEST_FAILED",
               "error_type": type(exc).__name__})
        return EXIT_UNAVAILABLE


if __name__ == "__main__":
    raise SystemExit(main())

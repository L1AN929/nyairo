#!/usr/bin/env python3
"""F-140 operator CLI: the only local write entry to the participant registry.

The normal form names **the instance**, not a state root and a subject:

    python scripts/participant_registry_cli.py --instance <instance root> \
        create --display-name "..." [--alias "..."] --operation-key operator:create-1

    ... list [--limit N]
    ... get --participant-id participant:...
    ... bind-endpoint --participant participant:... --provider local_inbox \
        --account-key <stable key> --operation-key operator:bind-1
    ... link-event --event-ref contact_evt:... --participant participant:... \
        [--endpoint endpoint:...] --operation-key operator:link-1
    ... resolve-event --event-ref contact_evt:...
    ... summary
    ... binding

``--instance`` reads the instance's own file through the existing loader
(``phone_service.load_instance`` for a phone root, ``one_day_life.read_instance`` for a Life
root) and derives, without asking the caller: the Life state root, the subject the instance's
own inbox and phone read surface use, and the same ``home_ref`` the phone binds.  It refuses by
name when the instance file is missing/unreadable, when the state root is absent or a symlink,
or when the state root's real inbox belongs to a different subject -- a first registration can
never claim a subject on someone else's root.

``--state-root`` / ``--subject`` remain only as a compatibility form for a root that **already**
carries verifiable Life data: an existing participant ledger (the owner verifies its subject) or
an inbox whose recorded subject matches.  A bare existing directory plus a caller-supplied
subject cannot create the first ledger.  ``--subject`` is never an authority by itself.

Exit codes: ``0`` ok / applied / already applied / a legitimate answer (including
``UNRESOLVED``); ``2`` a named rejection, conflict or a caller argument that contradicts the
instance; ``3`` the ledger or the instance cannot be read/verified (the answer is never
"empty").  Only the local OS owner runs this; there is no HTTP write route and no public
registration.
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
              str(ROOT / "components" / "life" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from participant_registry import (  # noqa: E402
    DB_FILENAME,
    ParticipantRegistry,
    ParticipantRegistryError,
)

EXIT_OK = 0
EXIT_REJECTED = 2
EXIT_UNAVAILABLE = 3

#: The two instance files this repository already writes.  The loaders decide, not this list.
CANONICAL_STATE_FILENAME = "canonical_life_state.json"
INBOX_STATE_FILENAME = "life_local_contact.json"


class BindingError(RuntimeError):
    """The instance cannot be bound.  ``reason_code`` is a ``PARTICIPANT_*`` code."""

    def __init__(self, reason_code: str, detail: str = "",
                 *, exit_code: int = EXIT_UNAVAILABLE) -> None:
        super().__init__("%s: %s" % (reason_code, detail) if detail else reason_code)
        self.reason_code = str(reason_code)
        self.detail = str(detail)
        self.exit_code = int(exit_code)


def _exit_code(answer: Mapping[str, Any]) -> int:
    status = str(answer.get("status") or "")
    if status in ("rejected", "conflict"):
        return EXIT_REJECTED
    if answer.get("ok"):
        return EXIT_OK
    return EXIT_UNAVAILABLE


def _lstat(path: Path) -> Any:
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise BindingError("PARTICIPANT_INSTANCE_ROOT_UNREADABLE",
                           "%s: %s" % (path, exc.__class__.__name__)) from exc


def _read_json_document(path: Path, reason_code: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise BindingError(reason_code, "%s: %s" % (path.name, exc.__class__.__name__)) from exc
    except ValueError as exc:
        raise BindingError(reason_code, "%s: not JSON" % path.name) from exc


def _state_evidence(state_root: Path) -> dict[str, str]:
    """What the root's own Life records say about the subject.

    ``canonical_life_state.json`` records the **Life/global** subject (``chiyo.global`` for a
    prepared day), which is a different domain from the instance/inbox subject; it is reported
    and never compared for equality.  ``life_local_contact.json`` records the inbox subject --
    the same value the phone's visit surface reads with -- and that one must agree.
    """

    evidence = {"canonical_life_subject": "", "inbox_subject": ""}
    for name, key, unreadable in (
            (CANONICAL_STATE_FILENAME, "canonical_life_subject",
             "PARTICIPANT_CANONICAL_STATE_UNREADABLE"),
            (INBOX_STATE_FILENAME, "inbox_subject", "PARTICIPANT_INBOX_STATE_UNREADABLE")):
        path = state_root / name
        info = _lstat(path)
        if info is None:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise BindingError("PARTICIPANT_INSTANCE_EVIDENCE_SYMLINK", str(path)[:160])
        if not stat.S_ISREG(info.st_mode):
            raise BindingError(unreadable, "%s is not a regular file" % name)
        payload = _read_json_document(path, unreadable)
        if not isinstance(payload, Mapping):
            raise BindingError(unreadable, "%s is not an object" % name)
        evidence[key] = str(payload.get("subject_id") or "").strip()
    return evidence


def _phone_modules() -> Any:
    from components.phone import life as phone_life
    from components.phone import service as phone_service
    return phone_life, phone_service


def _one_day_module() -> Any:
    import one_day_life as odl
    return odl


def _first(environment: Mapping[str, Any], names: tuple[str, ...]) -> str:
    for name in names:
        value = str(environment.get(name) or "").strip()
        if value:
            return value
    return ""


def resolve_instance(instance: Path) -> dict[str, Any]:
    """Derive this instance's state root, subject and home from its own file.  No guessing."""

    instance = Path(instance).expanduser()
    phone_life, phone_service = _phone_modules()
    phone: Optional[Mapping[str, Any]] = None
    phone_error = ""
    try:
        phone = phone_service.load_instance(instance)
    except phone_service.PhoneServiceError as exc:
        phone_error = str(exc)

    if phone is not None:
        try:
            nested = phone_life.read_nested_life(instance)
        except phone_life.PhoneLifeError as exc:
            raise BindingError("PARTICIPANT_INSTANCE_NESTED_INVALID", str(exc)[:160]) from exc
        state_root = Path(phone_life.life_state_root(instance, nested))
        subject = str(phone.get("subject") or "").strip()
        subject_source = "phone_instance.subject"
        environment = dict((nested or {}).get("environment") or {})
        from components.phone import contact as phone_contact
        home_ref, home_source = phone_contact.home_ref_for(environment, phone, root=instance)
        kind = "phone_instance"
        phone_subject = subject
    else:
        odl = _one_day_module()
        try:
            data = odl.read_instance(instance)
        except odl.OneDayError as exc:
            raise BindingError(
                "PARTICIPANT_INSTANCE_UNREADABLE",
                "%s (phone loader: %s)" % (str(exc)[:120], phone_error[:80])) from exc
        state_root = Path(odl.life_state_root(instance))
        environment = dict(data.get("environment") or {})
        subject = (str(data.get("subject_id") or "").strip()
                   or _first(environment, ("NYA02_SUBJECT",
                                           "CHIYO_NATIVE_LIFE_SUPPLY_SUBJECT")))
        subject_source = "life_one_day_instance.subject_id"
        home_ref = (_first(environment, ("CHIYO_WORLD_HOME", "HERMES_HOME", "NYA02_LIFE_HOME"))
                    or str(data.get("world_home") or "").strip())
        home_source = "instance:environment" if home_ref else ""
        kind = "life_one_day_instance"
        phone_subject = ""

    if not subject:
        raise BindingError("PARTICIPANT_INSTANCE_SUBJECT_MISSING",
                           "the instance file names no subject")

    info = _lstat(state_root)
    if info is None:
        raise BindingError("PARTICIPANT_INSTANCE_STATE_ROOT_ABSENT", str(state_root))
    if stat.S_ISLNK(info.st_mode):
        raise BindingError("PARTICIPANT_STATE_ROOT_SYMLINK", str(state_root))
    if not stat.S_ISDIR(info.st_mode):
        raise BindingError("PARTICIPANT_STATE_ROOT_INVALID", str(state_root))

    evidence = _state_evidence(state_root)
    if evidence["inbox_subject"] and evidence["inbox_subject"] != subject:
        raise BindingError(
            "PARTICIPANT_INSTANCE_BINDING_MISMATCH",
            "the inbox on this root belongs to %s, the instance says %s"
            % (evidence["inbox_subject"][:60], subject[:60]))

    return {
        "instance_root": str(instance),
        "instance_kind": kind,
        "state_root": str(state_root),
        "subject": subject,
        "subject_source": subject_source,
        "phone_subject": phone_subject or None,
        "canonical_life_subject": evidence["canonical_life_subject"] or None,
        "inbox_subject": evidence["inbox_subject"] or None,
        "home_ref": home_ref,
        "home_source": home_source,
        "subject_matches_canonical_life_state": (
            None if not evidence["canonical_life_subject"]
            else evidence["canonical_life_subject"] == subject),
    }


def _same_path(left: Path, right: Path) -> bool:
    try:
        return Path(left).expanduser().resolve() == Path(right).expanduser().resolve()
    except OSError:
        return False


def _compat_binding(state_root: Path, subject: str, *, writes: bool) -> dict[str, Any]:
    """The legacy form, allowed only where the root already speaks for the subject."""

    state_root = Path(state_root).expanduser()
    info = _lstat(state_root)
    if info is None or not stat.S_ISDIR(info.st_mode):
        raise BindingError("PARTICIPANT_INSTANCE_ROOT_INVALID", str(state_root))
    ledger = (state_root / DB_FILENAME).exists()
    evidence = _state_evidence(state_root)
    inbox_subject = evidence["inbox_subject"]
    if not ledger and inbox_subject and inbox_subject != subject:
        raise BindingError(
            "PARTICIPANT_INSTANCE_BINDING_MISMATCH",
            "the inbox on this root belongs to %s, not %s"
            % (inbox_subject[:60], subject[:60]))
    if writes and not ledger and not inbox_subject:
        raise BindingError(
            "PARTICIPANT_INSTANCE_UNVERIFIABLE",
            "pass --instance: a bare directory and a caller-supplied subject cannot open a "
            "first participant ledger", exit_code=EXIT_REJECTED)
    return {
        "instance_root": None,
        "instance_kind": "explicit_state_root",
        "state_root": str(state_root),
        "subject": subject,
        "subject_source": "caller:--subject (verified against this root)",
        "phone_subject": None,
        "canonical_life_subject": evidence["canonical_life_subject"] or None,
        "inbox_subject": inbox_subject or None,
        "home_ref": "",
        "home_source": "",
        "subject_matches_canonical_life_state": (
            None if not evidence["canonical_life_subject"]
            else evidence["canonical_life_subject"] == subject),
    }


def resolve_binding(args: argparse.Namespace, *, writes: bool) -> dict[str, Any]:
    if args.instance:
        binding = resolve_instance(Path(args.instance))
        if args.state_root and not _same_path(Path(args.state_root), Path(binding["state_root"])):
            raise BindingError(
                "PARTICIPANT_INSTANCE_ARGUMENT_MISMATCH",
                "--state-root is not this instance's Life state root (%s)"
                % binding["state_root"], exit_code=EXIT_REJECTED)
        if args.subject and str(args.subject).strip() != binding["subject"]:
            raise BindingError(
                "PARTICIPANT_INSTANCE_ARGUMENT_MISMATCH",
                "--subject %s is not this instance's subject (%s)"
                % (str(args.subject).strip()[:60], binding["subject"][:60]),
                exit_code=EXIT_REJECTED)
        return binding
    if not args.state_root or not args.subject:
        raise BindingError("PARTICIPANT_INSTANCE_REQUIRED",
                           "pass --instance, or both --state-root and --subject",
                           exit_code=EXIT_REJECTED)
    subject = str(args.subject).strip()
    if not subject:
        raise BindingError("PARTICIPANT_SUBJECT_REQUIRED", exit_code=EXIT_REJECTED)
    return _compat_binding(Path(args.state_root), subject, writes=writes)


def _registry(binding: Mapping[str, Any], args: argparse.Namespace,
              *, with_inbox: bool = False) -> ParticipantRegistry:
    state_root = Path(str(binding["state_root"]))
    inbox = None
    if with_inbox:
        home = str(binding.get("home_ref") or "").strip()
        if args.home_ref:
            given = str(args.home_ref).strip()
            if home and given != home:
                raise BindingError(
                    "PARTICIPANT_HOME_REF_MISMATCH",
                    "--home-ref %s is not this instance's home (%s)"
                    % (given[:80], home[:80]), exit_code=EXIT_REJECTED)
            home = given
        if not home:
            raise BindingError("PARTICIPANT_HOME_REF_UNBOUND",
                               "linking verifies the real inbox; the instance names no home "
                               "and no --home-ref was given", exit_code=EXIT_REJECTED)
        from life_local_contact import LocalContactInbox
        inbox = LocalContactInbox(state_root, subject_id=str(binding["subject"]),
                                  home_ref=home)
    return ParticipantRegistry(state_root, subject_id=str(binding["subject"]), inbox=inbox)


def _print(answer: Mapping[str, Any]) -> int:
    sys.stdout.write(json.dumps(dict(answer), ensure_ascii=False, sort_keys=True) + "\n")
    sys.stdout.flush()
    return _exit_code(answer)


def _print_binding(binding: Mapping[str, Any]) -> int:
    sys.stdout.write(json.dumps({"ok": True, "status": "ok", "reason_code": "", "detail": "",
                                 "writes_performed": 0,
                                 "data": {"binding": dict(binding)}},
                                ensure_ascii=False, sort_keys=True) + "\n")
    sys.stdout.flush()
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="local participant registry (operator only)")
    parser.add_argument("--instance", default=None,
                        help="this instance's root (phone instance.json or one-day Life "
                             "instance.json); state root, subject and home come from it")
    parser.add_argument("--state-root", default=None,
                        help="compatibility form only: an existing Life state root that already "
                             "carries this subject (inbox or participant ledger)")
    parser.add_argument("--subject", default=None,
                        help="compatibility form only: the instance's existing subject")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="register one participant")
    create.add_argument("--display-name", required=True)
    create.add_argument("--alias", action="append", default=[])
    create.add_argument("--operation-key", required=True)

    listing = sub.add_parser("list", help="bounded participant list")
    listing.add_argument("--limit", type=int, default=None)

    get = sub.add_parser("get", help="read one participant")
    get.add_argument("--participant-id", required=True)

    bind = sub.add_parser("bind-endpoint", help="bind one explicit transport endpoint")
    bind.add_argument("--participant", required=True)
    bind.add_argument("--provider", required=True)
    bind.add_argument("--account-key", required=True)
    bind.add_argument("--operation-key", required=True)

    link = sub.add_parser("link-event", help="link one real inbox event to a participant")
    link.add_argument("--event-ref", required=True)
    link.add_argument("--participant", required=True)
    link.add_argument("--endpoint", default=None)
    link.add_argument("--home-ref", default=None,
                      help="legacy form only; with --instance the instance's own home is used")
    link.add_argument("--operation-key", required=True)

    resolve = sub.add_parser("resolve-event", help="read one event's resolution")
    resolve.add_argument("--event-ref", required=True)

    sub.add_parser("summary", help="counts only")
    sub.add_parser("binding", help="read-only: the derived instance binding, no ledger read")
    return parser


#: Commands that write.  The compatibility form is only allowed on a root that can verify them.
WRITE_COMMANDS = frozenset({"create", "bind-endpoint", "link-event"})


def run(args: argparse.Namespace) -> int:
    command = str(args.command)
    try:
        binding = resolve_binding(args, writes=command in WRITE_COMMANDS)
        if command == "binding":
            return _print_binding(binding)
        if command == "create":
            registry = _registry(binding, args)
            return _print(registry.create(display_name=args.display_name,
                                          aliases=args.alias,
                                          operation_key=args.operation_key))
        if command == "list":
            registry = _registry(binding, args)
            return _print(registry.list_participants(limit=args.limit))
        if command == "get":
            registry = _registry(binding, args)
            return _print(registry.get_participant(args.participant_id))
        if command == "bind-endpoint":
            registry = _registry(binding, args)
            return _print(registry.bind_endpoint(participant_id=args.participant,
                                                 provider=args.provider,
                                                 account_key=args.account_key,
                                                 operation_key=args.operation_key))
        if command == "link-event":
            registry = _registry(binding, args, with_inbox=True)
            return _print(registry.link_event(event_ref=args.event_ref,
                                              participant_id=args.participant,
                                              endpoint_id=args.endpoint,
                                              operation_key=args.operation_key))
        if command == "resolve-event":
            registry = _registry(binding, args)
            return _print(registry.resolve_event(args.event_ref))
        if command == "summary":
            registry = _registry(binding, args)
            return _print(registry.summary())
    except BindingError as exc:
        sys.stdout.write(json.dumps({"ok": False, "status": "rejected" if
                                     exc.exit_code == EXIT_REJECTED else "unavailable",
                                     "reason_code": exc.reason_code,
                                     "detail": exc.detail, "data": None,
                                     "writes_performed": 0},
                                    ensure_ascii=False, sort_keys=True) + "\n")
        return exc.exit_code
    except ParticipantRegistryError as exc:
        sys.stdout.write(json.dumps({"ok": False, "status": "unavailable",
                                     "reason_code": exc.reason_code,
                                     "detail": exc.detail, "data": None,
                                     "writes_performed": 0},
                                    ensure_ascii=False, sort_keys=True) + "\n")
        return EXIT_UNAVAILABLE
    return EXIT_UNAVAILABLE


def main(argv: Optional[list[str]] = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())

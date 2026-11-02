#!/usr/bin/env python3
"""Local operator CLI for the narrow disclosure permit (issue / query / revoke).

One permit names exactly: subject + source artifact id + source version id + the existing local
target (``local:owner`` / local inbox).  It carries no body and no token, and it never sends
anything by itself: a send is still a separate, explicit act, and the owner checks the permit in
the same transaction that records the local delivery.

This file only talks to an already-running Life Supply owner over its Unix socket.  The caller's
identity is the socket peer's own uid (``SO_PEERCRED``); nothing here can name itself operator.
Output is JSON with ids, statuses, reasons and counts only -- never a body, prompt or token.

Usage::

    python scripts/disclosure_authority_cli.py --instance <root> issue \
        --source <artifact-id> --version <artifactversion-id> --operation-id <id> [--max-uses 1]
    python scripts/disclosure_authority_cli.py --instance <root> query
    python scripts/disclosure_authority_cli.py --instance <root> revoke \
        --grant-id <grant> --operation-id <id>

``--instance`` binds the instance's own supply socket and subject (read from its prepared
instance file, never from the environment); an explicit ``--subject`` (or ``--socket``) that
disagrees with the instance is refused.  ``--socket`` remains the low-level entry for a test or
an isolated root, and is still authenticated only by the socket peer's SO_PEERCRED uid.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"), str(ROOT / "components" / "supply")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from lifesupply.governance.authority import (  # noqa: E402
    DISCLOSE_CAPABILITY,
    DISCLOSURE_TARGET_LOCAL_OWNER,
    disclosure_scope,
)


def _call(socket_path: str, request: dict[str, Any], *, timeout: float = 30.0) -> dict[str, Any]:
    body = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(float(timeout))
        client.connect(str(socket_path))
        client.sendall(body + b"\n")
        line = client.makefile("rb").readline(1 << 20)
    if not line:
        return {"status": "UNAVAILABLE", "reason": "EMPTY_RESPONSE"}
    try:
        answer = json.loads(line.decode("utf-8"))
    except (UnicodeError, ValueError):
        return {"status": "UNAVAILABLE", "reason": "MALFORMED_RESPONSE"}
    return answer if isinstance(answer, dict) else {"status": "UNAVAILABLE",
                                                    "reason": "UNTYPED_RESPONSE"}


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _issue(args: argparse.Namespace) -> int:
    scope = disclosure_scope(args.source, args.version)
    answer = _call(args.socket, {
        "op": "issue_grant",
        "grant": {
            "subject": args.subject,
            "capability": DISCLOSE_CAPABILITY,
            "scope": scope,
            "target": DISCLOSURE_TARGET_LOCAL_OWNER,
            "operation_id": args.operation_id,
            "use_mode": "ONE_SHOT" if int(args.max_uses) == 1 else "REUSABLE",
            "max_uses": int(args.max_uses),
        },
    })
    _print({"action": "issue", "status": str(answer.get("status") or ""),
            "reason": str(answer.get("reason") or answer.get("detail") or "")[:120],
            "grant_id": answer.get("grant_id"), "revision": answer.get("revision")})
    return 0 if answer.get("status") == "ALLOW" else 1


def _query(args: argparse.Namespace) -> int:
    request: dict[str, Any] = {"op": "list_active_grants"}
    if args.subject:
        request["subject"] = args.subject
    answer = _call(args.socket, request)
    if answer.get("status") != "OK":
        _print({"action": "query", "status": str(answer.get("status") or ""),
                "reason": str(answer.get("reason") or answer.get("detail") or "")[:120],
                "count": 0})
        return 1
    rows = [row for row in (answer.get("grants") or [])
            if str(row.get("capability") or "") == DISCLOSE_CAPABILITY]
    grants = [{"grant_id": row.get("grant_id"), "subject": row.get("subject"),
               "scope": row.get("scope"), "target": row.get("target"),
               "state": row.get("revocation_state"), "revision": row.get("revision"),
               "uses": row.get("uses"), "max_uses": row.get("max_uses")}
              for row in rows]
    _print({"action": "query", "status": "OK", "count": len(grants), "grants": grants})
    return 0


def _revoke(args: argparse.Namespace) -> int:
    current = _call(args.socket, {"op": "get_grant", "grant_id": args.grant_id})
    row = current.get("grant") if current.get("status") == "OK" else None
    if not isinstance(row, dict):
        _print({"action": "revoke", "status": str(current.get("status") or "NOT_FOUND"),
                "reason": "GRANT_NOT_FOUND", "grant_id": args.grant_id})
        return 1
    if row.get("revocation_state") == "REVOKED":
        # Repeated revoke is idempotent for the operator: the wanted fact already holds.
        _print({"action": "revoke", "status": "NO_CHANGE", "reason": "ALREADY_REVOKED",
                "grant_id": args.grant_id, "revision": row.get("revision")})
        return 0
    answer = _call(args.socket, {
        "op": "revoke_grant", "grant_id": args.grant_id,
        "expected_revision": int(row.get("revision") or 0),
        "operation_id": args.operation_id,
    })
    _print({"action": "revoke", "status": str(answer.get("status") or ""),
            "reason": str(answer.get("reason") or answer.get("detail") or "")[:120],
            "grant_id": args.grant_id, "revision": answer.get("revision")})
    return 0 if answer.get("status") in {"REVOKED", "NO_CHANGE"} else 1


def _instance_target(instance: str) -> tuple[str | None, str | None, dict[str, Any] | None]:
    """Read one prepared instance's own socket and subject (never its environment/secrets)."""
    try:
        life_scripts = ROOT / "components" / "life" / "scripts"
        if str(life_scripts) not in sys.path:
            sys.path.append(str(life_scripts))
        import one_day_life as odl

        data = odl.read_instance(instance)
    except Exception as exc:  # a missing/invalid instance is a definite refusal, not a guess
        return None, None, {"action": "resolve", "status": "UNAVAILABLE",
                            "reason": "INSTANCE_UNREADABLE:%s" % type(exc).__name__}
    socket_path = str(data.get("supply_socket") or "")
    subject = str(data.get("subject_id") or "")
    if not socket_path or not subject:
        return None, None, {"action": "resolve", "status": "UNAVAILABLE",
                            "reason": "INSTANCE_INCOMPLETE"}
    return socket_path, subject, None


def _resolve_target(args: argparse.Namespace) -> dict[str, Any] | None:
    """Bind ``--instance`` (preferred) or ``--socket``; an explicit subject must be the same one."""
    socket_path = args.socket
    # ``revoke`` names the grant only; it has no subject argument to bind.
    subject = getattr(args, "subject", None)
    if args.instance:
        instance_socket, instance_subject, error = _instance_target(args.instance)
        if error is not None:
            return error
        if socket_path and os.path.realpath(str(socket_path)) != os.path.realpath(instance_socket):
            return {"action": "resolve", "status": "DENY", "reason": "SOCKET_INSTANCE_MISMATCH"}
        if subject and str(subject) != instance_subject:
            # The permit is bound to the instance's own subject; naming another one here would
            # let the caller silently retarget the operator action.
            return {"action": "resolve", "status": "DENY", "reason": "SUBJECT_INSTANCE_MISMATCH"}
        socket_path, subject = instance_socket, instance_subject
    if not socket_path:
        return {"action": "resolve", "status": "DENY", "reason": "SOCKET_OR_INSTANCE_REQUIRED"}
    args.socket = str(socket_path)
    if hasattr(args, "subject"):
        args.subject = subject
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="narrow disclosure permit CLI")
    parser.add_argument("--socket", default=None, help="Life Supply owner socket path")
    parser.add_argument("--instance", default=None,
                        help="prepared instance root; binds its own supply socket and subject")
    sub = parser.add_subparsers(dest="command", required=True)

    issue = sub.add_parser("issue")
    issue.add_argument("--subject", default=None)
    issue.add_argument("--source", required=True, help="source artifact id")
    issue.add_argument("--version", required=True, help="exact source version id")
    issue.add_argument("--operation-id", required=True)
    issue.add_argument("--max-uses", default=1, type=int)
    issue.set_defaults(func=_issue)

    query = sub.add_parser("query")
    query.add_argument("--subject", default=None)
    query.set_defaults(func=_query)

    revoke = sub.add_parser("revoke")
    revoke.add_argument("--grant-id", required=True)
    revoke.add_argument("--operation-id", required=True)
    revoke.set_defaults(func=_revoke)

    args = parser.parse_args(argv)
    error = _resolve_target(args)
    if error is not None:
        _print(error)
        return 1
    if args.command == "issue" and not args.subject:
        _print({"action": "issue", "status": "DENY", "reason": "SUBJECT_REQUIRED"})
        return 1
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())

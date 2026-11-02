#!/usr/bin/env python3
"""S03 operator CLI -- explicit reported evidence, correction and current reads.

This is the operator's own channel, not a model tool:

* ``report`` records a **REPORTED** claim.  The source must be an explicit
  ``operator_report:`` / ``report:`` reference; the CLI never claims a World
  observation, never writes World and never writes Memory.
* ``correct`` / ``retract`` invalidate exactly one named source and re-derive the
  claims that depended on it.  A retraction withholds the old payload in current
  and history; a correction may carry a replacement value.
* ``current`` / ``slot`` are pure reads over the same aggregate.

The database is created by a new root's ``prepare``; this CLI never creates one.

Examples::

    python scripts/s03_claims_cli.py current --db /path/s03/claims.sqlite3
    python scripts/s03_claims_cli.py report --db ... --predicate place_closing_time \
        --scope bookstore --value 22:00 \
        --context '{"local_date":"2026-11-20","timezone":"Asia/Tokyo"}' \
        --source operator_report:shift-a --operation-key op:report:1
    python scripts/s03_claims_cli.py correct --db ... \
        --source operator_report:shift-a --operation-key op:correct:1 \
        --value 20:30
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components" / "life" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import s03_claims as s03  # noqa: E402


def _json(value: str) -> dict:
    try:
        parsed = json.loads(value)
    except ValueError as error:
        raise SystemExit("context must be JSON: %s" % error)
    if not isinstance(parsed, dict):
        raise SystemExit("context must be a JSON object")
    return parsed


def _service(args) -> s03.S03ClaimService:
    return s03.S03ClaimService(args.db, subject=args.subject)


def cmd_current(args) -> int:
    view = _service(args).current_view(now=args.now, limit=args.limit)
    print(json.dumps(view, ensure_ascii=False, indent=1, sort_keys=True))
    return 0


def cmd_slot(args) -> int:
    service = _service(args)
    slot = service.slot_view(slot_id=args.slot_id, now=args.now)
    history = (service.history(slot_id=args.slot_id, now=args.now)
               if slot.get("reason_code") != s03.REASON_NO_SLOT
               else {"claims": [], "reason_code": s03.REASON_NO_SLOT})
    print(json.dumps({"slot": slot, "history": history}, ensure_ascii=False,
                     indent=1, sort_keys=True))
    return 0


def cmd_report(args) -> int:
    answer = _service(args).report(
        predicate_id=args.predicate, scope_id=args.scope, value=args.value,
        context=_json(args.context) if args.context else None,
        source_ref=args.source, source_revision=args.source_revision,
        valid_from=args.valid_from, valid_to=args.valid_to,
        observed_at=args.observed_at, now=args.now,
        operation_key=args.operation_key,
        revalidate_after_seconds=args.revalidate_after_seconds)
    print(json.dumps(answer, ensure_ascii=False, indent=1, sort_keys=True))
    return 0


def cmd_correct(args) -> int:
    replacement = None
    if args.value is not None:
        replacement = {"value": args.value, "valid_from": args.valid_from,
                       "valid_to": args.valid_to, "observed_at": args.observed_at}
    answer = _service(args).correct_source(
        source_ref=args.source, now=args.now, operation_key=args.operation_key,
        slot_id=args.slot_id, replacement=replacement)
    print(json.dumps(answer, ensure_ascii=False, indent=1, sort_keys=True))
    return 0


def cmd_retract(args) -> int:
    answer = _service(args).retract_source(
        source_ref=args.source, now=args.now, operation_key=args.operation_key,
        slot_id=args.slot_id)
    print(json.dumps(answer, ensure_ascii=False, indent=1, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", required=True, help="an existing S03 claims.sqlite3")
    common.add_argument("--subject", required=True)
    common.add_argument("--now", default=None,
                        help="injected instant (ISO-8601); default is wall clock")
    sub = parser.add_subparsers(dest="command", required=True)

    current = sub.add_parser("current", parents=[common],
                             help="bounded current claims (pure read)")
    current.add_argument("--limit", type=int, default=20)
    current.set_defaults(func=cmd_current)

    slot = sub.add_parser("slot", parents=[common],
                          help="one slot's status and revision chain")
    slot.add_argument("--slot-id", required=True)
    slot.set_defaults(func=cmd_slot)

    report = sub.add_parser("report", parents=[common],
                            help="record one explicitly reported claim")
    report.add_argument("--predicate", required=True)
    report.add_argument("--scope", required=True)
    report.add_argument("--value", required=True)
    report.add_argument("--context", default=None, help="JSON object")
    report.add_argument("--source", required=True)
    report.add_argument("--source-revision", default=None)
    report.add_argument("--valid-from", default=None)
    report.add_argument("--valid-to", default=None)
    report.add_argument("--observed-at", default=None)
    report.add_argument("--revalidate-after-seconds", type=int, default=None)
    report.add_argument("--operation-key", required=True)
    report.set_defaults(func=cmd_report)

    correct = sub.add_parser("correct", parents=[common],
                             help="correct one source (optionally replace)")
    correct.add_argument("--source", required=True)
    correct.add_argument("--slot-id", default=None)
    correct.add_argument("--value", default=None)
    correct.add_argument("--valid-from", default=None)
    correct.add_argument("--valid-to", default=None)
    correct.add_argument("--observed-at", default=None)
    correct.add_argument("--operation-key", required=True)
    correct.set_defaults(func=cmd_correct)

    retract = sub.add_parser("retract", parents=[common],
                             help="retract one source (no replacement)")
    retract.add_argument("--source", required=True)
    retract.add_argument("--slot-id", default=None)
    retract.add_argument("--operation-key", required=True)
    retract.set_defaults(func=cmd_retract)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except s03.S03ClaimsError as error:
        print(json.dumps({"ok": False,
                          "reason_code": getattr(error, "reason_code",
                                                 type(error).__name__),
                          "detail": str(error)[:200]}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

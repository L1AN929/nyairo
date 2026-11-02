#!/usr/bin/env python3
"""S10 CLI: read one Action (or recent Actions) as request / attempt / receipt / reconcile.

This is a **pure read** surface over the AR-0 ledger of one Life instance.  It prints what
the ledger actually recorded and nothing else:

* ``PREPARED`` is a frozen request, not a dispatch and not a send;
* ``attempt.started_at`` is a local attempt start, not a provider receipt;
* ``ACCEPTED`` is not ``DELIVERED`` and neither is the user having received it;
* ``UNKNOWN`` stays ``UNKNOWN`` (a lost answer/timeout is never shown as ``FAILED``);
* a field the ledger did not record is printed as ``NOT_RECORDED``.

No payload/content/正文/credential is printed.  Nothing is proposed, submitted, reconciled or
recovered; if a commit WAL is pending, the CLI refuses with ``WAL_PENDING_RECOVERY`` instead
of replaying it (replay is a write).

Usage::

    python scripts/s10_action_review_cli.py --instance <life-instance> --limit 20
    python scripts/s10_action_review_cli.py --instance <life-instance> --action-id actn:xxxx
    python scripts/s10_action_review_cli.py --state-root <instance>/life/state --format text
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import action_outcome_review as review  # noqa: E402


def _state_root(args: argparse.Namespace) -> Path:
    if args.state_root:
        return Path(args.state_root)
    # The Life state root lives at ``<instance>/life/state`` (one_day_life.life_state_root).
    return Path(args.instance) / "life" / "state"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only projection of AR-0 request/attempt/receipt/reconciliation facts.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--instance", help="Life instance root (uses <instance>/life/state)")
    source.add_argument("--state-root", help="the directory that holds action_reality_state.json")
    parser.add_argument("--action-id", default=None, help="one logical action id (actn:...)")
    parser.add_argument("--limit", type=int, default=50,
                        help="how many recent actions to list (<=0 means all); default 50")
    parser.add_argument("--format", choices=("json", "text"), default="json")
    args = parser.parse_args(argv)

    state_root = _state_root(args)
    if not state_root.exists():
        print(json.dumps({"ok": False, "reason": "STATE_ROOT_NOT_FOUND",
                          "state_root": str(state_root)}, ensure_ascii=False))
        return 2
    limit = None if args.limit is not None and args.limit <= 0 else args.limit
    envelope = review.build_action_review(
        state_root, action_id=args.action_id, limit=limit)
    if args.format == "text":
        print(review.render_text(envelope))
    else:
        print(json.dumps(envelope, ensure_ascii=False, indent=2, default=str))
    return 0 if envelope.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

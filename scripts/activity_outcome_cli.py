#!/usr/bin/env python3
"""Formal local entry for Activity FAILED / BLOCKED.

Read-only ``show`` parses the owner's own canonical file.  ``fail`` and ``block`` build the
*real* production runtime for one prepared instance, so the write goes through the existing
LR-2 command + admission capability -- this tool never edits JSON and never mints a capability
of its own.  The runtime takes the canonical lease; stop any resident for the same instance
first (the owner refuses otherwise).

Examples (local, no product model):
    python3 -B scripts/activity_outcome_cli.py show --instance /tmp/nyairo-...
    python3 -B scripts/activity_outcome_cli.py fail --instance /tmp/nyairo-... \
        --activity-id actv_... --criterion criterion:ARTIFACT_ARCHIVED \
        --evidence artifact:<id> --operation-key review-1
    python3 -B scripts/activity_outcome_cli.py block --instance /tmp/nyairo-... \
        --activity-id actv_... --blocked-on-ref wait_dep:artifact.x.y \
        --operation-key block-1
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import activity_outcome_assessment as ao  # noqa: E402
import life_artifact_port as lap  # noqa: E402
import life_integration_ag2 as ag2  # noqa: E402
import life_runtime_production as lrp  # noqa: E402
import managed_artifact_action_adapter as maaa  # noqa: E402
import one_day_life as odl  # noqa: E402


def _clock() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _print(value) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, indent=1))


def _state_root(args) -> Path:
    if args.state_root:
        return Path(args.state_root)
    return odl.life_state_root(Path(args.instance))


def cmd_show(args) -> int:
    state_root = _state_root(args)
    out: dict = {"state_root": str(state_root), "activities": [], "conditions": {},
                 "eligibilities": {}}
    path = state_root / "canonical_life_state.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        out["error"] = "CANONICAL_STATE_ABSENT"
        _print(out)
        return 2
    except (OSError, ValueError) as exc:
        out["error"] = "CANONICAL_STATE_UNREADABLE: %s" % type(exc).__name__
        _print(out)
        return 2
    for activity_id, act in sorted((state.get("activities") or {}).items()):
        if not isinstance(act, dict):
            continue
        if args.status and str(act.get("status")) not in set(args.status):
            continue
        out["activities"].append({
            "activity_id": activity_id,
            "status": act.get("status"),
            "schema_version": act.get("schema_version"),
            "revision": act.get("revision"),
            "title": act.get("title"),
            "terminal_reason_code": act.get("terminal_reason_code"),
            "blocked_on_ref": act.get("blocked_on_ref"),
            "blocked_reason_code": act.get("blocked_reason_code"),
            "failure_assessment_ref": act.get("failure_assessment_ref"),
            "failure_criteria_refs": act.get("failure_criteria_refs"),
            "failure_evidence_refs": act.get("failure_evidence_refs"),
            "unresolved_action_refs": act.get("unresolved_action_refs"),
            "completion_evidence_ref": act.get("completion_evidence_ref"),
        })
    for name, parts in (
        ("conditions", ("waiting_agenda", "resume_conditions", "resume_conditions_state.json")),
        ("eligibilities", ("waiting_agenda", "eligibility", "resume_eligibility_state.json")),
    ):
        row_path = state_root.joinpath(*parts)
        try:
            row_state = json.loads(row_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError):
            continue
        key = "conditions" if name == "conditions" else "eligibilities"
        out[name] = [
            {k: row.get(k) for k in (
                "condition_id" if name == "conditions" else "eligibility_id",
                "activity_id", "status", "activity_revision", "resume_condition_ref",
                "waiting_episode_id", "expires_at")}
            for row in (row_state.get(key) or {}).values() if isinstance(row, dict)
        ]
    out["foreground_activity_ref"] = state.get("foreground_activity_ref")
    out["revision"] = state.get("revision")
    _print(out)
    return 0


def _progress_views(state_root: Path) -> tuple[dict, list, str]:
    """Read-only reconciliations + late-terminal log, from the snapshot or its journal.

    A live owner may hold its newest state only in the hash-chained journal until the next
    snapshot flush, so the journal's own per-activity deltas are read when the snapshot is
    absent.  Nothing is written, rebuilt or replayed.
    """
    snapshot = state_root / "activity_progress_state.json"
    try:
        data = json.loads(snapshot.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        data = None
    if isinstance(data, dict):
        return (dict(data.get("reconciliations_by_activity") or {}),
                list(data.get("late_terminal_evidence_log") or []), "snapshot")
    journal = state_root / "activity_progress_journal.jsonl"
    try:
        raw = journal.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, ValueError):
        return {}, [], "absent"
    reconciliations: dict = {}
    late: list = []
    seen: set = set()
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        delta = entry.get("state_delta") or {}
        act_delta = delta.get("act_delta") or {}
        if isinstance(act_delta, dict):
            rows = act_delta.get("reconciliations_by_activity")
            if isinstance(rows, list) and delta.get("activity_id"):
                reconciliations[str(delta["activity_id"])] = rows
        append = delta.get("late_terminal_append")
        if isinstance(append, dict):
            key = (append.get("progress_evidence_id"), append.get("reconciliation_id"))
            if key not in seen:
                seen.add(key)
                late.append(append)
    return reconciliations, late, "journal"


def cmd_corrections(args) -> int:
    """Read-only: show LR-5 post-completion conflicts (late corrections), nothing else.

    The record is the owner's own progress state/journal -- no runtime is built, no lease is
    taken and nothing is written.  Only ids/status/reasons are printed, never the evidence
    body, a title or a private text.
    """
    state_root = _state_root(args)
    out: dict = {"state_root": str(state_root), "reconciliations": [],
                 "late_terminal_evidence": [], "activities": []}
    reconciliations, late_terminal, source = _progress_views(state_root)
    if source == "absent":
        out["error"] = "PROGRESS_STATE_ABSENT"
        _print(out)
        return 2
    out["source"] = source
    progress = {"reconciliations_by_activity": reconciliations}
    wanted = args.activity_id
    for activity_id, rows in sorted((progress.get("reconciliations_by_activity") or {}).items()):
        if wanted and activity_id != wanted:
            continue
        for row in rows or ():
            out["reconciliations"].append({
                "activity_id": activity_id,
                "reconciliation_id": row.get("reconciliation_id"),
                "status": row.get("status"),
                "reason_code": row.get("reason_code"),
                "conflict_kind": row.get("conflict_kind"),
                "completed_at_revision": row.get("completed_at_revision"),
                "completion_evidence_ref": row.get("completion_evidence_ref"),
                "conflicting_evidence_ref": row.get("conflicting_evidence_ref"),
                "conflicting_settlement_ref": row.get("conflicting_settlement_ref"),
                "detected_at": row.get("detected_at"),
            })
    for row in late_terminal or ():
        if not isinstance(row, dict):
            continue
        if wanted and str(row.get("activity_id")) != wanted:
            continue
        out["late_terminal_evidence"].append({
            "activity_id": row.get("activity_id"),
            "terminal_status": row.get("terminal_status"),
            "progress_evidence_id": row.get("progress_evidence_id"),
            "source_ref": row.get("source_ref"),
            "is_conflicting_correction": row.get("is_conflicting_correction"),
            "reconciliation_id": row.get("reconciliation_id"),
            "recorded_at": row.get("recorded_at"),
        })
    canonical_path = state_root / "canonical_life_state.json"
    try:
        canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        canonical = {}
    for activity_id, act in sorted((canonical.get("activities") or {}).items()):
        if not isinstance(act, dict):
            continue
        if wanted and activity_id != wanted:
            continue
        if not (wanted or out["reconciliations"] or out["late_terminal_evidence"]):
            continue
        out["activities"].append({
            "activity_id": activity_id,
            "status": act.get("status"),
            "revision": act.get("revision"),
            "completion_evidence_ref": act.get("completion_evidence_ref"),
        })
    out["foreground_activity_ref"] = canonical.get("foreground_activity_ref")
    _print(out)
    return 0


def _build_instance_runtime(args):
    instance = Path(args.instance)
    data = odl.read_instance(instance)
    env = odl.instance_environment(instance)
    socket = str(env.get("LIFE_SUPPLY_SOCKET") or "")
    if not socket:
        raise SystemExit("NO_SUPPLY_SOCKET: the instance has no LIFE_SUPPLY_SOCKET")
    port = lap.LifeArtifactPort(socket, str(data["subject_id"]))
    adapter = maaa.SupplyManagedArtifactActionAdapter(
        port, grant_id=str(data["artifact_grant_id"]),
        workspace_id=str(data["workspace_id"]))
    switches = ag2.LifeRuntimeKillSwitches(
        life_runtime_enabled=True, agency_enabled=True, action_execution_enabled=True,
        proactive_enabled=False)
    runtime = lrp.build_production_life_runtime(
        odl.life_state_root(instance), kill_switches=switches, cognition_adapter=None,
        install_root=instance / "life", artifact_action_adapter=adapter)
    return runtime, data, port


def cmd_fail(args) -> int:
    runtime = None
    try:
        runtime, data, _port = _build_instance_runtime(args)
        act = runtime.activity_read.get_activity(args.activity_id)
        if act is None:
            _print({"error": "ACTIVITY_NOT_FOUND", "activity_id": args.activity_id})
            return 2
        scope = list(args.scope or [])
        if not scope:
            named = [ref for ref in (act.get("subject_refs") or []) if isinstance(ref, str)]
            if not named:
                _print({"error": "NO_GOAL_SCOPE", "activity_id": args.activity_id})
                return 2
            scope = [named[0]]
        assessment = runtime.activity_outcome.assess_failure(
            args.activity_id, goal_scope_refs=scope,
            criterion_refs=list(args.criterion), evidence_refs=list(args.evidence),
            unresolved_action_refs=list(args.unresolved or []),
            assessed_at=args.assessed_at or _clock())
        result = runtime.activity_outcome.fail_activity(
            args.activity_id, assessment,
            idempotency_key=args.operation_key,
            occurred_at=args.occurred_at or _clock(),
            observed_at=args.observed_at or args.occurred_at or _clock())
        _print({
            "ok": True,
            "activity_id": args.activity_id,
            "status": (result.activity or {}).get("status"),
            "revision": (result.activity or {}).get("revision"),
            "assessment": assessment.to_payload(),
            "committed": bool(result.committed),
            "idempotent_replay": bool(result.idempotent_replay),
            # The production AG-2 handoff this fail operation really finished (once), or a
            # named reason it could not.  A missing integration record is not a Memory write.
            "handoff": (getattr(result, "failure_handoff", None)
                        or runtime.activity_outcome.last_failure_handoff),
        })
        return 0
    except Exception as exc:
        _print({"ok": False, "error": "%s: %s" % (type(exc).__name__, str(exc)[:400])})
        return 1
    finally:
        if runtime is not None:
            try:
                runtime.close()
            except Exception:
                pass


def cmd_block(args) -> int:
    runtime = None
    try:
        runtime, _data, _port = _build_instance_runtime(args)
        result = runtime.activity_outcome.block_activity(
            args.activity_id,
            blocked_on_ref=args.blocked_on_ref,
            blocked_reason_code=args.blocked_reason_code,
            criterion_refs=list(args.criterion or []),
            idempotency_key=args.operation_key,
            occurred_at=args.occurred_at or _clock(),
            observed_at=args.observed_at or args.occurred_at or _clock())
        _print({
            "ok": True,
            "activity_id": args.activity_id,
            "status": (result.activity or {}).get("status"),
            "revision": (result.activity or {}).get("revision"),
            "blocked_on_ref": (result.activity or {}).get("blocked_on_ref"),
            "committed": bool(result.committed),
            "idempotent_replay": bool(result.idempotent_replay),
        })
        return 0
    except Exception as exc:
        _print({"ok": False, "error": "%s: %s" % (type(exc).__name__, str(exc)[:400])})
        return 1
    finally:
        if runtime is not None:
            try:
                runtime.close()
            except Exception:
                pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    show = sub.add_parser("show", help="read-only Activity outcome view")
    show.add_argument("--instance")
    show.add_argument("--state-root")
    show.add_argument("--status", action="append")
    show.set_defaults(func=cmd_show)

    corrections = sub.add_parser(
        "corrections", help="read-only post-completion conflict / late-correction view")
    corrections.add_argument("--instance")
    corrections.add_argument("--state-root")
    corrections.add_argument("--activity-id")
    corrections.set_defaults(func=cmd_corrections)

    fail = sub.add_parser("fail", help="owner-assessed FAILED terminal")
    fail.add_argument("--instance", required=True)
    fail.add_argument("--activity-id", required=True)
    fail.add_argument("--scope", action="append", default=[])
    fail.add_argument("--criterion", action="append", required=True)
    fail.add_argument("--evidence", action="append", required=True)
    fail.add_argument("--unresolved", action="append", default=[])
    fail.add_argument("--operation-key", required=True)
    fail.add_argument("--assessed-at")
    fail.add_argument("--occurred-at")
    fail.add_argument("--observed-at")
    fail.set_defaults(func=cmd_fail)

    block = sub.add_parser("block", help="record a BLOCKED basis (non-terminal)")
    block.add_argument("--instance", required=True)
    block.add_argument("--activity-id", required=True)
    block.add_argument("--blocked-on-ref", required=True)
    block.add_argument("--blocked-reason-code", default="BLOCKED_BY_CONDITION")
    block.add_argument("--criterion", action="append", default=[])
    block.add_argument("--operation-key", required=True)
    block.add_argument("--occurred-at")
    block.add_argument("--observed-at")
    block.set_defaults(func=cmd_block)

    args = parser.parse_args(argv)
    if args.command == "show" and not (args.instance or args.state_root):
        parser.error("show needs --instance or --state-root")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

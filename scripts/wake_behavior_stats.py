#!/usr/bin/env python3
"""Read-only behaviour and candidate-cost statistics for one ended fast day.

What this is
------------
A whitelist reader over the records an already-stopped instance left on disk.  It
answers "how many wakes, decisions, candidates, actions and movement seconds were
really recorded, and in which domain" without opening a store, starting a service or
calling a model.  It never prints prompt text, private material, note bodies or
model reasoning; only ids/refs, counts, timestamps, reason codes and sizes.

The numbers are kept apart on purpose:

* ``resident_summary.counters.wakes`` is a cumulative read-surface counter, not the
  number of unique wake records.  Both are reported side by side.
* a Decision attempt is not a committed Decision, and neither is a request-ledger
  reservation.  ``RULE_RESOLVED`` rounds are local rules (0 model calls); only
  ``COGNITIVE_REQUIRED`` attempts count as model decision rounds.
* a Life adoption (START transition) is not an AR-0 owner receipt, and a World
  ``prepared`` intent is not a World ``committed`` mutation.
* movement time is the union of recorded ``travel.started_at -> arrived_at``
  intervals in the World business-clock domain.  A missing end or a missing clock
  makes the total UNKNOWN/PARTIAL -- it is never counted as zero.  The share of the
  day spent in *all* activities is NOT MEASURABLE from these records.

Usage:
    python scripts/wake_behavior_stats.py --root /root/nyairo-fastday-20261024-e
    python scripts/wake_behavior_stats.py --root ... --pretty
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

SCHEMA = "wake_behavior_stats.v1"


# ---------------------------------------------------------------------------
# safe readers
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _load_jsonl(path: Path) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    malformed = 0
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                try:
                    item = json.loads(text)
                except ValueError:
                    malformed += 1
                    continue
                if isinstance(item, dict):
                    rows.append(item)
                else:
                    malformed += 1
    except OSError:
        return [], malformed
    return rows, malformed


def _ts(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _seconds(start: Any, end: Any) -> Optional[float]:
    first, last = _ts(start), _ts(end)
    if first is None or last is None:
        return None
    value = (last - first).total_seconds()
    return value if value >= 0 else None


def _merge_intervals(intervals: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    ordered = sorted(intervals)
    merged: list[tuple[float, float]] = []
    for start, end in ordered:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _round(value: Optional[float], digits: int = 3) -> Optional[float]:
    return None if value is None else round(float(value), digits)


def _counter(counter: Counter) -> dict[str, int]:
    return {str(key): int(value) for key, value in sorted(counter.items(),
                                                          key=lambda pair: str(pair[0]))}


def _state_paths(root: Path) -> dict[str, Path]:
    state = root / "one-day" / "life" / "state"
    world = root / "one-day" / "world-home" / "data"
    return {
        "summary": state / "resident_summary.json",
        "wakes": state / "autonomous_wakes.jsonl",
        "resident_journal": state / "resident_journal.jsonl",
        "decisions": state / "agency_decision_journal.jsonl",
        "candidates": state / "candidate_audit_journal.jsonl",
        "transitions": state / "life_transitions.jsonl",
        "action_reality": state / "action_reality_journal.jsonl",
        "settlements": state / "result_settlement_journal.jsonl",
        "ledger": state / "life_request_budget.json",
        "experiment_budget": state / "fastday" / "experiment_budget.json",
        "clock": state / "fastday" / "fastday_clock.json",
        "run": state / "fastday" / "run.json",
        "fastday": root / "fastday.json",
        "world_execution": world / "life_execution.jsonl",
        "world_travel": world / "world" / "world_travel_delivery.json",
    }


# ---------------------------------------------------------------------------
# sections
# ---------------------------------------------------------------------------


def _run_and_coverage(summary: Mapping[str, Any], run: Mapping[str, Any],
                      clock: Mapping[str, Any]) -> dict[str, Any]:
    time_domain = summary.get("time_domain") or {}
    return {
        "run_id": run.get("run_id") or clock.get("run_id") or summary.get("time_domain", {}).get("run_id"),
        "execution_mode": run.get("execution_mode"),
        "clock_only": run.get("clock_only"),
        "real_start": run.get("real_start"),
        "real_end": run.get("real_end"),
        "stop_reason": run.get("stop_reason"),
        "mode": clock.get("mode") or time_domain.get("mode"),
        "domain_id": clock.get("domain_id") or time_domain.get("domain_id"),
        "rate": clock.get("rate") if clock.get("rate") is not None else time_domain.get("rate"),
        "logical_seconds": (clock.get("logical_seconds")
                            if clock.get("logical_seconds") is not None
                            else time_domain.get("logical_elapsed_seconds")),
        "logical_checkpoint": clock.get("logical_checkpoint") or time_domain.get("logical_at"),
        "clock_state": clock.get("state"),
        "not_real_24h": time_domain.get("not_real_24h"),
        "starts": summary.get("starts"),
        "ticks": summary.get("ticks"),
        "covered_seconds": summary.get("covered_seconds"),
        "observed_seconds": summary.get("observed_seconds"),
        "wall_span_seconds": summary.get("wall_span_seconds"),
        "offline_gap_seconds": summary.get("offline_gap_seconds"),
        "unknown_seconds": summary.get("unknown_seconds"),
        "overlap_seconds": summary.get("overlap_seconds"),
        "continuous": summary.get("continuous"),
        "coverage_complete": summary.get("coverage_complete"),
        "process_online_seconds": summary.get("process_online_seconds"),
        "soak_verified": summary.get("soak_verified"),
        "memory_continuity_wired": (summary.get("memory_binding") or {}).get("continuity_wired"),
        "runtime_assembly": (summary.get("runtime_assembly") or {}).get("state"),
        "situation_wiring": (summary.get("situation_wiring") or {}).get("status"),
    }


def _wakes(rows: Sequence[Mapping[str, Any]], summary: Mapping[str, Any]) -> dict[str, Any]:
    counters = summary.get("counters") or {}
    by_class = Counter(str(row.get("status_class") or "unknown") for row in rows)
    by_outcome = Counter(str(row.get("outcome") or "SILENCE_NOT_DUE") for row in rows)
    by_kind = Counter(str(row.get("kind") or "unknown") for row in rows)
    by_decision = Counter(str(row.get("decision")) for row in rows if row.get("decision"))
    llm_rounds = sum(int(row.get("llm_calls") or 0) for row in rows)
    read_surface = {
        "counter_wakes": counters.get("wakes"),
        "by_class": counters.get("by_class") or {},
        "journey_arrivals": counters.get("journey_arrivals"),
        "journey_settlements": counters.get("journey_settlements"),
        "model_calls": counters.get("model_calls"),
        "rule_resolved_windows": counters.get("rule_resolved_windows"),
        "windows_with_material": counters.get("windows_with_material"),
    }
    discrepancies = []
    if counters.get("wakes") not in (None, len(rows)):
        discrepancies.append({
            "metric": "wakes",
            "read_surface": int(counters.get("wakes")),
            "unique_wake_records": len(rows),
            "note": ("counters.wakes is the resident's cumulative read-surface counter "
                     "(silence/arrival projections repeat); autonomous_wakes.jsonl holds "
                     "the unique wake records"),
        })
    return {
        "records": len(rows),
        "by_status_class": _counter(by_class),
        "by_outcome": _counter(by_outcome),
        "by_kind": _counter(by_kind),
        "by_decision": _counter(by_decision),
        "unacted": {
            "abandoned": int(by_outcome.get("ABANDONED", 0)),
            "decision_failed": int(by_outcome.get("DECISION_FAILED", 0)),
            "no_step_available": int(by_outcome.get("NO_STEP_AVAILABLE", 0)),
            "no_action": int(by_outcome.get("NO_ACTION", 0)),
        },
        "llm_rounds_from_wakes": llm_rounds,
        "read_surface": read_surface,
        "read_surface_discrepancies": discrepancies,
    }


def _cognition_rounds(journal: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Per-request diagnostics: candidate count, finish reason, latency, retry index.

    Only whitelisted metadata leaves this function; no prompt/response/reasoning text.
    """

    started: dict[tuple[str, int], dict[str, Any]] = {}
    finished: dict[tuple[str, int], dict[str, Any]] = {}
    attempts = 0
    for row in journal:
        if row.get("kind") != "cognition_diagnostic":
            continue
        event = str(row.get("event") or "")
        key = (str(row.get("opportunity_id") or ""), int(row.get("schema_retry_index") or 0))
        if event == "cognition_send_started":
            attempts += 1
            started[key] = {
                "at": row.get("at"),
                "candidate_count": row.get("candidate_count"),
                "schema_retry_index": int(row.get("schema_retry_index") or 0),
            }
        elif event in {"cognition_result", "cognition_send_failed", "cognition_provider_error"}:
            target = finished.setdefault(key, {})
            for name in ("finish_reason", "error_category", "latency_ms",
                         "max_output_tokens_sent", "json_parsed", "response_received",
                         "candidate_count"):
                if row.get(name) is not None:
                    target[name] = row.get(name)

    rounds = []
    for key, start in sorted(started.items(), key=lambda pair: str(pair[1].get("at"))):
        item = dict(start)
        item.update({k: v for k, v in finished.get(key, {}).items() if k != "candidate_count"})
        rounds.append(item)
    counts = [int(item["candidate_count"]) for item in rounds
              if isinstance(item.get("candidate_count"), int)]
    return {
        "send_attempts": attempts,
        "rounds": rounds,
        "candidate_counts": counts,
        "candidate_count_max": max(counts) if counts else None,
        "candidate_count_median": _round(statistics.median(counts), 1) if counts else None,
        "finish_reasons": _counter(Counter(str(item.get("finish_reason"))
                                          for item in rounds if item.get("finish_reason"))),
        "error_categories": _counter(Counter(str(item.get("error_category"))
                                            for item in rounds if item.get("error_category"))),
        "schema_retry_rounds": sum(1 for item in rounds
                                   if int(item.get("schema_retry_index") or 0) > 0),
    }


def _decisions(journal: Sequence[Mapping[str, Any]], ledger: Mapping[str, Any],
               experiment: Mapping[str, Any], fastday: Mapping[str, Any],
               summary: Mapping[str, Any]) -> dict[str, Any]:
    payloads = [row.get("payload") for row in journal if isinstance(row.get("payload"), Mapping)]
    opened = [p for p in payloads if p.get("event_type") == "DECISION_ATTEMPT_OPENED"]
    staged = [p for p in payloads if p.get("event_type") == "COGNITION_RESPONSE_STAGED"]
    committed = [p for p in payloads if p.get("event_type") == "DECISION_COMMITTED"]
    failed = [p for p in payloads if p.get("event_type") == "DECISION_ATTEMPT_FAILED"]
    attempt_routes = Counter(str(p.get("route") or "UNKNOWN") for p in opened)
    committed_routes = Counter(str(p.get("decision_route") or "UNKNOWN") for p in committed)
    decisions = Counter(str(p.get("decision")) for p in committed)
    model_runs = {str(p.get("model_run_ref")) for p in payloads if p.get("model_run_ref")}

    tokens = ledger.get("tokens") or []
    token_kinds = Counter(str(token.get("kind") or "unknown")
                          for token in tokens if isinstance(token, Mapping))
    reserved = int(experiment.get("reserved_calls")
                   or (ledger.get("windows") or {}).get(ledger.get("run_id") or "", {}).get("calls")
                   or len(tokens))
    historical = int(fastday.get("historical_requests")
                     or experiment.get("historical_requests") or 0)
    total_limit = int(fastday.get("request_total_limit")
                      or experiment.get("authorized_total_limit")
                      or summary.get("budget", {}).get("max_cognition_calls_per_day") or 0)
    max_new = int(fastday.get("max_new_requests")
                  or experiment.get("max_new_requests") or 0)
    total_used = historical + reserved

    rule_resolved = int(attempt_routes.get("RULE_RESOLVED", 0))
    model_decision_rounds = sum(count for route, count in attempt_routes.items()
                                if route != "RULE_RESOLVED")
    no_action_model = sum(1 for p in committed
                          if p.get("decision") == "NO_ACTION"
                          and str(p.get("decision_route") or "") != "RULE_RESOLVED")
    return {
        "attempts_opened": len(opened),
        "responses_staged": len(staged),
        "committed": len(committed),
        "failed": len(failed),
        "unique_decision_ids": len({str(p.get("decision_id")) for p in committed
                                    if p.get("decision_id")}),
        "unique_model_runs": len(model_runs),
        "attempt_routes": _counter(attempt_routes),
        "committed_routes": _counter(committed_routes),
        "decisions": _counter(decisions),
        "failure_classes": _counter(Counter(str(p.get("failure_class") or "UNKNOWN")
                                            for p in failed)),
        "model_decision_rounds": model_decision_rounds,
        "rule_resolved_rounds": rule_resolved,
        "no_action_model_chosen": no_action_model,
        "no_action_rule_resolved": sum(1 for p in committed
                                       if p.get("decision") == "NO_ACTION"
                                       and str(p.get("decision_route") or "") == "RULE_RESOLVED"),
        "ledger": {
            "source": "life_request_budget.json",
            "reserved": reserved,
            "by_kind": _counter(token_kinds),
            "utc_day_calls": sum(int(v) for v in (ledger.get("days") or {}).values()
                                 if isinstance(v, int)),
            "run_window": ledger.get("run_id"),
            "run_window_calls": (ledger.get("windows") or {}).get(ledger.get("run_id") or "", {}).get("calls"),
        },
        "experiment": {
            "historical_requests": historical,
            "reserved_calls": reserved,
            "max_new_requests": max_new,
            "experiment_remaining": (max_new - reserved) if max_new else None,
            "authorized_total_limit": total_limit,
            "total_used": total_used,
            "total_remaining": (total_limit - total_used) if total_limit else None,
            "source": experiment.get("kind"),
        },
        "count_semantics": (
            "model_decision_rounds counts decision attempts whose route is not "
            "RULE_RESOLVED; rule_resolved_rounds counts local-rule attempts (0 model "
            "calls). Reserved requests come only from the persisted request ledger."
        ),
    }


def _candidates(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    materialized = [row for row in rows if row.get("event_type") == "MATERIALIZED"]
    invalidated = [row for row in rows if row.get("event_type") == "INVALIDATED"]
    ids = {str(row.get("candidate_id")) for row in materialized}
    # The audit row does not carry target_refs/constraints, so a repeated
    # (source_kind, source_ref, revision) is *not* proof of a duplicate semantic
    # candidate: two different constraints may legitimately share one source ref.
    # Only a repeated candidate_id is a proven duplicate.
    same_source = Counter((str(row.get("source_kind")), str(row.get("source_ref")),
                           str(row.get("source_revision"))) for row in materialized)
    reappeared = [{"source_kind": key[0], "source_ref": key[1], "source_revision": key[2],
                   "count": count}
                  for key, count in same_source.items() if count > 1]
    by_kind = Counter(str(row.get("source_kind")) for row in materialized)
    reasons = Counter(str(row.get("reason_code")) for row in invalidated)

    by_round: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in materialized:
        by_round[str(row.get("materialized_at"))].append(row)
    rounds = []
    for at in sorted(by_round, key=str):
        group = by_round[at]
        rounds.append({
            "at": at,
            "materialized": len(group),
            "unique_candidates": len({str(row.get("candidate_id")) for row in group}),
            "by_source_kind": _counter(Counter(str(row.get("source_kind")) for row in group)),
        })
    per_round = [int(item["materialized"]) for item in rounds]

    invalidated_ids = {str(row.get("candidate_id")) for row in invalidated}
    open_at_end = sorted(ids - invalidated_ids)
    return {
        "audit_rows": len(rows),
        "materialized": len(materialized),
        "invalidated": len(invalidated),
        "unique_materialized_candidates": len(ids),
        "duplicate_candidate_id_materializations": len(materialized) - len(ids),
        "same_source_revision_reappearances": reappeared,
        "same_source_revision_note": (
            "a repeated (source_kind, source_ref, source_revision) may be a different "
            "constraint on the same source; it is not called a duplicate semantic "
            "candidate unless the candidate_id itself repeats"),
        "materialized_by_source_kind": _counter(by_kind),
        "invalidation_reasons": _counter(reasons),
        "materialization_rounds": rounds,
        "candidates_per_round": per_round,
        "candidates_per_round_max": max(per_round) if per_round else None,
        "candidates_per_round_median": _round(statistics.median(per_round), 1) if per_round else None,
        "open_at_end": len(open_at_end),
        "note": ("materialized_at on an INVALIDATED row is the candidate's original "
                 "creation time, not the invalidation time; rounds here group only "
                 "MATERIALIZED rows by their own materialized_at."),
    }


def _actions(transitions: Sequence[Mapping[str, Any]],
             action_reality: Sequence[Mapping[str, Any]],
             world_execution: Sequence[Mapping[str, Any]],
             summary: Mapping[str, Any]) -> dict[str, Any]:
    transition_types = Counter(str(row.get("transition_type")) for row in transitions)
    transition_reasons = Counter(str(row.get("reason_code")) for row in transitions)
    action_events = Counter(str(row.get("event_type")) for row in action_reality)
    action_status = Counter(str(row.get("to_status")) for row in action_reality
                            if row.get("to_status"))
    receipt_claims = Counter(str(row.get("status_claim")) for row in action_reality
                             if row.get("event_type") == "RECEIPT_INGESTED")
    unknown_actions = sum(1 for row in action_reality
                          if str(row.get("to_status") or "") == "UNKNOWN"
                          or str(row.get("status_claim") or "") == "UNKNOWN")

    world_prepared = [row for row in world_execution if row.get("status") == "prepared"]
    world_committed = [row for row in world_execution if row.get("status") == "committed"]
    world_unknown = sum(1 for row in world_execution
                        if str(row.get("status") or "").lower() == "unknown")
    prepared_by_type = Counter(str(row.get("action_type")) for row in world_prepared)
    committed_by_type = Counter(str(row.get("action_type")) for row in world_committed)
    reconciliation = sum(1 for row in world_execution if row.get("reconciliation_required"))

    counters = summary.get("counters") or {}
    return {
        "life": {
            "transition_types": _counter(transition_types),
            "transition_reasons": _counter(transition_reasons),
            "starts": int(transition_types.get("START", 0)),
            "adoptions": int(sum(1 for row in transitions
                                 if row.get("transition_type") == "START"
                                 and str(row.get("reason_code") or "") == "ADOPTED_DECISION")),
            "abandons": int(transition_types.get("ABANDON", 0)),
            "completes": int(transition_types.get("COMPLETE", 0)),
            "checkpoints": int(transition_types.get("CHECKPOINT", 0)),
        },
        "ar0": {
            "events": _counter(action_events),
            "to_status": _counter(action_status),
            "owner_receipts_succeeded": int(receipt_claims.get("SUCCEEDED", 0)),
            "receipt_claims": _counter(receipt_claims),
            "result_evidence_recorded": int(action_events.get("RESULT_EVIDENCE_RECORDED", 0)),
            "unknown_actions": unknown_actions,
        },
        "world": {
            "prepared_intents": len(world_prepared),
            "committed": len(world_committed),
            "prepared_by_action_type": _counter(prepared_by_type),
            "committed_by_action_type": _counter(committed_by_type),
            "pose_prepared": int(prepared_by_type.get("POSE", 0)),
            "move_prepared": int(prepared_by_type.get("MOVE", 0)),
            "pose_committed": int(committed_by_type.get("POSE", 0)),
            "move_committed": int(committed_by_type.get("MOVE", 0)),
            "reconciliation_required": reconciliation,
            "unknown_entries": world_unknown,
        },
        "summary": {
            "actions": counters.get("actions"),
            "outcomes": counters.get("outcomes") or {},
            "owner_confirmed_versions": counters.get("owner_confirmed_versions"),
            "owner_version_unknown": counters.get("owner_version_unknown"),
            "pending_unknown_max": counters.get("pending_unknown_max"),
            "unknown_settlements": counters.get("unknown_settlements"),
        },
        "separation": (
            "intent (AR-0 proposed/prepared, World prepared) != adoption (Life START) "
            "!= owner receipt (AR-0 RECEIPT_INGESTED) != World committed mutation. "
            "UNKNOWN, ABANDON and POSE are reported as their own counters."
        ),
    }


def _movement(travels_doc: Optional[Mapping[str, Any]], clock: Mapping[str, Any],
              summary: Mapping[str, Any]) -> dict[str, Any]:
    time_domain = summary.get("time_domain") or {}
    logical_seconds = clock.get("logical_seconds")
    if logical_seconds is None:
        logical_seconds = time_domain.get("logical_elapsed_seconds")
    rate = clock.get("rate")
    if rate is None:
        rate = time_domain.get("rate")
    domain = clock.get("domain_id") or time_domain.get("domain_id")

    base = {
        "domain": domain or "UNKNOWN",
        "domain_basis": ("World owner's business clock (fastday_clock.json)"
                         if clock.get("domain_id") else "UNKNOWN"),
        "rate": rate,
        "logical_day_seconds": logical_seconds,
        "travels_recorded": 0,
        "arrived": 0,
        "intervals_counted": 0,
        "unknown_intervals": 0,
        "not_arrived": 0,
        "movement_seconds": None,
        "known_movement_seconds": None,
        "movement_share_of_logical_day": None,
        "real_equivalent_seconds": None,
        "real_equivalent_basis": "DERIVED_FROM_BUSINESS_CLOCK",
        "total_activity_share": "NOT_MEASURABLE",
        "total_activity_share_reason": (
            "only World travel records a start and an end in this instance; the other "
            "activities (reading, writing, resting, staying) have no comparable duration "
            "in the whitelisted records, so no total activity share is computed"
        ),
        "status": "UNAVAILABLE",
        "travels": [],
    }
    if not isinstance(travels_doc, Mapping):
        base["reason"] = "world_travel_delivery.json is missing or unreadable"
        return base

    rows = [row for row in (travels_doc.get("travels") or []) if isinstance(row, Mapping)]
    base["travels_recorded"] = len(rows)
    intervals: list[tuple[float, float]] = []
    for row in rows:
        status = str(row.get("status") or "")
        seconds = _seconds(row.get("started_at"), row.get("arrived_at"))
        item = {
            "route_id": row.get("route_id"),
            "origin": row.get("origin_location_id"),
            "destination": row.get("destination_location_id"),
            "status": status,
            "started_at": row.get("started_at"),
            "arrived_at": row.get("arrived_at"),
            "route_duration_seconds": row.get("route_duration_seconds"),
            "observed_seconds": _round(seconds, 0),
        }
        base["travels"].append(item)
        if status == "ARRIVED" and seconds is not None:
            start = _ts(row.get("started_at"))
            intervals.append((start.timestamp(), start.timestamp() + seconds))
            base["arrived"] += 1
        elif status == "ARRIVED":
            base["unknown_intervals"] += 1
        else:
            base["not_arrived"] += 1

    if not rows:
        base["status"] = "UNAVAILABLE"
        base["reason"] = "no travel records"
        return base
    if not intervals:
        base["status"] = "UNKNOWN"
        base["reason"] = "no arrival with both a start and an end"
        return base

    merged = _merge_intervals(intervals)
    known = sum(end - start for start, end in merged)
    base["intervals_counted"] = len(merged)
    base["known_movement_seconds"] = _round(known)
    complete = base["unknown_intervals"] == 0 and base["not_arrived"] == 0
    if complete:
        base["status"] = "COMPLETE"
        base["movement_seconds"] = _round(known)
        if isinstance(logical_seconds, (int, float)) and logical_seconds > 0:
            base["movement_share_of_logical_day"] = _round(known / float(logical_seconds), 6)
        if isinstance(rate, (int, float)) and rate > 0:
            base["real_equivalent_seconds"] = _round(known / float(rate))
    else:
        base["status"] = "PARTIAL"
        base["reason"] = ("some travels have no usable start/end or did not arrive; "
                          "movement_seconds is withheld, known_movement_seconds is partial")
    return base


def _budget(fastday: Mapping[str, Any], experiment: Mapping[str, Any],
            ledger: Mapping[str, Any], summary: Mapping[str, Any]) -> dict[str, Any]:
    reserved = int(experiment.get("reserved_calls")
                   or len(ledger.get("tokens") or []))
    historical = int(fastday.get("historical_requests")
                     or experiment.get("historical_requests") or 0)
    total_limit = int(fastday.get("request_total_limit")
                      or experiment.get("authorized_total_limit")
                      or summary.get("budget", {}).get("max_cognition_calls_per_day") or 0)
    max_new = int(fastday.get("max_new_requests")
                  or experiment.get("max_new_requests") or 0)
    total_used = historical + reserved
    return {
        "configured_total_limit": total_limit,
        "historical_requests": historical,
        "experiment_reserved_calls": reserved,
        "experiment_max_new_requests": max_new,
        "experiment_remaining": (max_new - reserved) if max_new else None,
        "total_used": total_used,
        "total_remaining": (total_limit - total_used) if total_limit else None,
        "source": "fastday.json + fastday/experiment_budget.json + life_request_budget.json",
    }


# ---------------------------------------------------------------------------
# assembly
# ---------------------------------------------------------------------------


def collect(root: Path) -> dict[str, Any]:
    paths = _state_paths(root)
    summary = _load_json(paths["summary"]) or {}
    run = _load_json(paths["run"]) or {}
    clock = _load_json(paths["clock"]) or {}
    fastday = _load_json(paths["fastday"]) or {}
    experiment = _load_json(paths["experiment_budget"]) or {}
    ledger = _load_json(paths["ledger"]) or {}
    travel_doc = _load_json(paths["world_travel"])

    wakes, wakes_bad = _load_jsonl(paths["wakes"])
    journal, journal_bad = _load_jsonl(paths["resident_journal"])
    decisions, decisions_bad = _load_jsonl(paths["decisions"])
    candidates, candidates_bad = _load_jsonl(paths["candidates"])
    transitions, transitions_bad = _load_jsonl(paths["transitions"])
    action_reality, actions_bad = _load_jsonl(paths["action_reality"])
    world_execution, world_bad = _load_jsonl(paths["world_execution"])

    result = {
        "schema_version": SCHEMA,
        "root": str(root),
        "run": _run_and_coverage(summary, run, clock),
        "wakes": _wakes(wakes, summary),
        "cognition": _cognition_rounds(journal),
        "decisions": _decisions(decisions, ledger, experiment, fastday, summary),
        "candidates": _candidates(candidates),
        "actions": _actions(transitions, action_reality, world_execution, summary),
        "movement": _movement(travel_doc, clock, summary),
        "budget": _budget(fastday, experiment, ledger, summary),
        "read_surface": {
            "resident_ticks": summary.get("ticks"),
            "tick_rows_in_journal": sum(1 for row in journal if row.get("tick") is not None),
            "unique_wake_records": len(wakes),
            "unique_travel_records": len((travel_doc or {}).get("travels") or [])
            if isinstance(travel_doc, Mapping) else None,
        },
        "malformed_jsonl": {
            "wakes": wakes_bad,
            "resident_journal": journal_bad,
            "decisions": decisions_bad,
            "candidates": candidates_bad,
            "transitions": transitions_bad,
            "action_reality": actions_bad,
            "world_execution": world_bad,
        },
    }

    warnings: list[dict[str, Any]] = []
    if wakes_bad or journal_bad or decisions_bad or candidates_bad:
        warnings.append({"code": "MALFORMED_JSONL",
                         "detail": "some journal rows could not be parsed; counts are lower bounds"})
    summary_wakes = (summary.get("counters") or {}).get("wakes")
    if summary_wakes not in (None, len(wakes)):
        warnings.append({"code": "READ_SURFACE_WAKE_COUNT",
                         "detail": "counters.wakes is cumulative; use unique wake records"})
    arrivals = (summary.get("counters") or {}).get("journey_arrivals")
    travel_rows = len((travel_doc or {}).get("travels") or []) if isinstance(travel_doc, Mapping) else 0
    if arrivals not in (None, travel_rows):
        warnings.append({"code": "ARRIVAL_PROJECTION_REPEATED",
                         "summary_journey_arrivals": arrivals,
                         "unique_arrived_travels": travel_rows,
                         "detail": "arrivals is a repeated read-surface projection, not a count of trips"})
    if str(clock.get("state") or "") and str((summary.get("time_domain") or {}).get("state") or "") \
            and str(clock.get("state")) != str((summary.get("time_domain") or {}).get("state")):
        warnings.append({"code": "CLOCK_STATE_STALE_LABEL",
                         "summary": (summary.get("time_domain") or {}).get("state"),
                         "clock": clock.get("state"),
                         "detail": "the summary's time_domain label may predate the clock's final STOPPED state"})
    if result["movement"]["status"] != "COMPLETE":
        warnings.append({"code": "MOVEMENT_NOT_COMPLETE",
                         "status": result["movement"]["status"],
                         "detail": "movement seconds are not claimed as a complete total"})
    if result["candidates"]["duplicate_candidate_id_materializations"]:
        warnings.append({"code": "DUPLICATE_CANDIDATE_ID_MATERIALIZED",
                         "count": result["candidates"]["duplicate_candidate_id_materializations"]})
    if result["candidates"]["same_source_revision_reappearances"]:
        warnings.append({"code": "SAME_SOURCE_REVISION_REAPPEARED",
                         "count": len(result["candidates"]["same_source_revision_reappearances"])})
    if result["decisions"]["model_decision_rounds"] != result["wakes"]["llm_rounds_from_wakes"]:
        warnings.append({"code": "WAKE_LLM_ROUNDS_DIFFER",
                         "model_decision_rounds": result["decisions"]["model_decision_rounds"],
                         "wake_llm_rounds": result["wakes"]["llm_rounds_from_wakes"]})
    result["warnings"] = warnings
    result["claim_boundary"] = (
        "Counts only.  No candidate score, stage duration, adoption quality, "
        "understanding or G1 result is inferred from them."
    )
    return result


def _pretty(result: Mapping[str, Any]) -> str:
    run = result["run"]
    wakes = result["wakes"]
    cog = result["cognition"]
    dec = result["decisions"]
    cand = result["candidates"]
    act = result["actions"]
    move = result["movement"]
    budget = result["budget"]
    lines = [
        "run: %s mode=%s logical=%ss real=%ss..%ss clock=%s" % (
            run.get("run_id"), run.get("mode"), run.get("logical_seconds"),
            run.get("real_start"), run.get("real_end"), run.get("clock_state")),
        "wakes: %s records (summary counter %s); classes=%s; unacted=%s" % (
            wakes["records"], (wakes["read_surface"] or {}).get("counter_wakes"),
            wakes["by_status_class"], wakes["unacted"]),
        "cognition: %s sends, candidate counts=%s, finish=%s, errors=%s" % (
            cog["send_attempts"], cog["candidate_counts"], cog["finish_reasons"],
            cog["error_categories"]),
        "decisions: attempts=%s committed=%s failed=%s model_rounds=%s rule=%s ledger=%s" % (
            dec["attempts_opened"], dec["committed"], dec["failed"],
            dec["model_decision_rounds"], dec["rule_resolved_rounds"],
            dec["ledger"]["by_kind"]),
        "candidates: unique=%s audit_rows=%s per_round=%s dup_id=%s same_source=%s open_at_end=%s" % (
            cand["unique_materialized_candidates"], cand["audit_rows"],
            cand["candidates_per_round"], cand["duplicate_candidate_id_materializations"],
            len(cand["same_source_revision_reappearances"]), cand["open_at_end"]),
        "actions: life starts=%s abandons=%s completes=%s | ar0 receipts=%s unknown=%s | "
        "world prepared=%s committed=%s pose=%s/%s move=%s/%s" % (
            act["life"]["starts"], act["life"]["abandons"], act["life"]["completes"],
            act["ar0"]["owner_receipts_succeeded"], act["ar0"]["unknown_actions"],
            act["world"]["prepared_intents"], act["world"]["committed"],
            act["world"]["pose_prepared"], act["world"]["pose_committed"],
            act["world"]["move_prepared"], act["world"]["move_committed"]),
        "movement: status=%s seconds=%s known=%s share=%s domain=%s | total activity share=%s" % (
            move["status"], move["movement_seconds"], move["known_movement_seconds"],
            move["movement_share_of_logical_day"], move["domain"], move["total_activity_share"]),
        "budget: configured=%s historical=%s reserved=%s used=%s remaining=%s" % (
            budget["configured_total_limit"], budget["historical_requests"],
            budget["experiment_reserved_calls"], budget["total_used"],
            budget["total_remaining"]),
        "warnings: %s" % (", ".join(str(item.get("code")) for item in result["warnings"]) or "-"),
    ]
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", required=True, help="ended instance root")
    parser.add_argument("--pretty", action="store_true", help="human-readable summary")
    args = parser.parse_args(argv)
    root = Path(args.root)
    if not root.is_dir():
        print(json.dumps({"error": "ROOT_NOT_FOUND", "root": str(root)}))
        return 2
    result = collect(root)
    if args.pretty:
        print(_pretty(result))
    else:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

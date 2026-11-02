#!/usr/bin/env python3
"""INFRA F-167 demo: only this Action's own owner result counts as this Activity's progress.

What this really runs (local only, 0 product model, 0 network, 0 old instance)
-----------------------------------------------------------------------------
* a real Life Supply owner on its own Unix socket, a real prepared instance, a real
  ``ProductionLifeRuntime`` and ``assemble_autonomous_life``;
* real AG-1 (scripted Decision answers), AG-2, LR-2, LR-4, AR-0, AR-1, LR-5 and M0 Memory;
* two real Activities: work A (started by a real Decision) and work B (started by a real
  Decision with ``act=False`` so no step runs yet), each with its own real artifact.

The check
---------
The LR-5 consumer must verify a settled-result notification against the AR-1 owner's own
stored event and settlement record.  A *real* receipt from Action B that is re-labelled as
Activity A must not become A's progress, checkpoint or completion, and must not be recorded
under the event id in a way that would stop B from consuming its own result later.

Controls
--------
1. A's own settled event was consumed by A (real round-1 progress).
2. Negative (F-167): B's real settled event, re-labelled ``activity_ref=A`` with A's target
   ref, is refused with zero A evidence / checkpoint / completion / Memory writes.
3. Negative: an event id AR-1 never emitted is refused (do not trust the payload's own
   ``result`` claim).
4. B's untampered real event is still consumable by B after those refusals, and re-delivering
   it is an idempotent replay (no second progress update).
5. A's own event re-delivered is an idempotent replay.

Boundaries: the Decision answers and the content writer are scripted doubles; no product
model and no 24h/G1 claim.  The re-labelled event is a deliberate mis-binding in the harness.

Run (WSL/POSIX)::

    PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/infra_source_binding_demo.py
"""

from __future__ import annotations

import copy
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "tests"), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "native"),
              str(ROOT / "components" / "native" / "src"),
              str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_artifact_port as lap  # noqa: E402
import result_method_behavior_smoke as base  # noqa: E402

FAILURES: list[str] = []
CHECKS: dict[str, bool] = {}


def say(text: str) -> None:
    print(text)
    sys.stdout.flush()


def check(name: str, condition: Any, detail: Any = None) -> bool:
    ok = bool(condition)
    CHECKS[name] = ok
    say("CHECK %-74s %s" % (name, "PASS" if ok else "FAIL"))
    if not ok:
        FAILURES.append(name)
        if detail is not None:
            say("  detail: " + json.dumps(detail, ensure_ascii=False, default=str)[:1600])
    return ok


def _refs(row: Mapping[str, Any]) -> list[str]:
    return [str(ref) for ref in (row.get("target_refs") or [])]


def _closeout(row: Mapping[str, Any]) -> bool:
    return any(str(ref).startswith("closeout:") for ref in _refs(row))


def candidates(chain, observed_at: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    cset = chain.runtime.candidate_materializer.build_candidate_set(observed_at=observed_at)
    for ref in cset.candidate_refs:
        payload = dict(chain.runtime.candidate_read.get_candidate(str(ref)) or {})
        payload.setdefault("candidate_id", str(ref))
        out.append(payload)
    return out


def choose_start(chain, artifact: Optional[str], observed_at: str, *, exclude_artifact: Optional[str] = None) -> dict:
    # The candidate payload does not carry the per-round legal set (AG-1 decides that);
    # pick the non-closeout work candidate for this artifact and let real AG-1 validate START.
    for row in candidates(chain, observed_at):
        if _closeout(row):
            continue
        refs = " ".join(_refs(row))
        if artifact and str(artifact).split(":")[-1] not in refs:
            continue
        if exclude_artifact and str(exclude_artifact).split(":")[-1] in refs:
            continue
        return row
    raise SystemExit("no work candidate offered (artifact=%r)" % (artifact,))


def choose_pause(chain, observed_at: str) -> dict:
    for row in candidates(chain, observed_at):
        if str(row.get("candidate_kind") or "") == "CONTINUE_CURRENT":
            return row
    raise SystemExit("no current-activity candidate offered for PAUSE")


def run_round(chain, decision: str, candidate_id: Optional[str], observed_at: str, *,
              act: bool = True) -> dict:
    chain.cognition.script = {"decision": decision, "selected_candidate_id": candidate_id,
                              "deferred_candidate_ids": []}
    return dict(chain.cycle.evaluate_once(
        trigger_ref="evt:infra-f167:%d" % int(time.time() * 1000 % 1_000_000),
        observed_at=observed_at, act=act) or {})


def activity_for(chain, artifact: str) -> Optional[dict]:
    state = chain.runtime.activity_store.load_verified_state_and_journal(_include_journal=False)[0]
    wanted = str(artifact)
    for row in (state.get("activities") or {}).values():
        if str(row.get("origin_ref") or "") == wanted:
            return dict(row)
    return None


def progress_of(chain, activity_id: str) -> dict:
    return dict(chain.runtime.progress_read.get_progress_state(activity_id) or {})


def active_settlements(chain, activity_id: str) -> list[str]:
    state = chain.runtime.progress_store.load_state(_mutable=True)
    rows = state["active_evidences_by_activity"].get(activity_id) or []
    return [str(row.get("settlement_ref")) for row in rows if row.get("settlement_ref")]


def consumed_source_refs(chain, activity_id: str) -> set[str]:
    state = chain.runtime.progress_store.load_state(_mutable=True)
    return {
        str(row.get("evidence", {}).get("source_ref"))
        for row in state["consumed_evidences_by_id"].values()
        if row.get("bound_activity_id") == activity_id
    }


def memory_count(chain) -> int:
    try:
        return len(chain.memory_port.recent(limit=1000))
    except Exception:
        return -1


def settle_manual_action(chain, *, activity_id: str, artifact: str, content: str,
                         decision_ref: Optional[str], adoption_ref: Optional[str],
                         correlation_id: Optional[str], tag: str) -> dict:
    """One real owner action on an existing Activity: AR-0 propose/prepare/submit -> AR-1 settle."""
    runtime = chain.runtime
    head = str((chain.port.read_head(artifact) or {}).get("version_id") or "")
    tag_key = "infra-f167-" + tag
    prop = runtime.action_command.propose_action(
        action_kind="FILE_ACTION", target_ref="artifact:%s" % artifact,
        payload_ref="payload:" + tag_key,
        payload_data={"op": "commit_artifact_version", "artifact_id": artifact,
                      "expected_head": head, "content": content},
        idempotency_key=tag_key, source_refs=[r for r in (adoption_ref, decision_ref) if r],
        decision_ref=decision_ref, authorization_ref=adoption_ref, activity_ref=activity_id,
    )
    action_id = str(prop["action"]["action_id"])
    runtime.action_command.prepare_action(action_id=action_id)
    runtime.action_command.submit_action(
        action_id=action_id, executor_capability=runtime.executor_capability)
    settled = runtime.settlement_service.settle_action(
        action_id=action_id, correlation_id=correlation_id)
    return {"action_id": action_id, "settlement": settled["settlement"],
            "event": settled["outbox_event"]}


def real_context_for_activity(chain, activity_id: str) -> dict:
    state = chain.runtime.integration_commit.read_state(mutable=True)
    for row in (state.get("integrations_by_id") or {}).values():
        if str(row.get("activity_ref") or "") == activity_id:
            return dict(row)
    return {}


def main() -> int:
    summary: dict[str, Any] = {"checks": {}, "limitations": [
        "Decision answers and the content writer are scripted doubles; no product model was called",
        "the re-labelled event is a deliberate mis-binding in the harness, not a real AR-1 emission",
        "no crash matrix, no 24h run, no G1 claim",
    ]}
    chain = None
    try:
        chain = base.build_chain("infra-f167")
        runtime = chain.runtime
        artifact_a = str(chain.data["artifact_id"])
        created_b = chain.port.create_artifact(
            workspace_id=str(chain.data["workspace_id"]), title="F167 work B",
            content="B seed line.\n", grant_id=str(chain.data["artifact_grant_id"]),
            operation_key=lap.operation_key_for("infra-f167", "create-b", "b"))
        if not (created_b.applied and created_b.artifact_id):
            raise SystemExit("owner did not create work B: %r" % (created_b.to_dict(),))
        artifact_b = str(created_b.artifact_id)

        t0 = time.time()
        def at(minutes: int) -> str:
            return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t0 + 60 * minutes)) + "+00:00"

        # 1. Real START of work A through the real Decision/Adoption chain.
        start_a = choose_start(chain, artifact_a, at(0))
        round_a = run_round(chain, "START", start_a["candidate_id"], at(0), act=True)
        act_a = activity_for(chain, artifact_a)
        check("round 1 START adopts a real Activity A", act_a is not None and round_a.get("adoption_status") == "APPLIED", round_a)
        assert act_a is not None
        aid_a = str(act_a["activity_id"])
        event_a = dict(next(
            ev for ev in runtime.settlement_read.list_outbox_events()
            if str(ev.get("activity_ref") or "") == aid_a))
        check("A consumed its own real settled event", event_a.get("settlement_ref") in active_settlements(chain, aid_a),
              {"settlement_ref": event_a.get("settlement_ref"), "active": active_settlements(chain, aid_a)})

        # 2. PAUSE A through a real Decision; the slot is released.
        pause_row = choose_pause(chain, at(60))
        round_pause = run_round(chain, "PAUSE", pause_row["candidate_id"], at(60), act=True)
        act_a = activity_for(chain, artifact_a)
        check("round 2 PAUSE adopts and releases A", round_pause.get("adoption_status") == "APPLIED"
              and act_a is not None and act_a.get("status") == "PAUSED", round_pause)

        # 3. START work B (real Decision + Adoption) without executing a step.
        start_b = choose_start(chain, artifact_b, at(120), exclude_artifact=artifact_a)
        round_b = run_round(chain, "START", start_b["candidate_id"], at(120), act=False)
        act_b = activity_for(chain, artifact_b)
        check("round 3 START creates Activity B without a step", act_b is not None
              and round_b.get("adoption_status") == "APPLIED", round_b)
        assert act_b is not None
        aid_b = str(act_b["activity_id"])
        context_b = real_context_for_activity(chain, aid_b)

        # 4. One real owner action on B (real Supply), settled by AR-1 but NOT dispatched yet.
        action_b = settle_manual_action(
            chain, activity_id=aid_b, artifact=artifact_b, content="B real new line.\n",
            decision_ref=context_b.get("decision_ref"), adoption_ref=context_b.get("adoption_ref"),
            correlation_id=context_b.get("correlation_id"), tag="b-1")
        event_b = dict(action_b["event"])
        check("AR-1 settled B's real action", event_b.get("activity_ref") == aid_b
              and event_b.get("settlement_status") == "SETTLED", event_b)
        settlements_b_before = active_settlements(chain, aid_b)

        # 5. NEGATIVE: B's real settlement event re-labelled as Activity A.
        memory_before = memory_count(chain)
        a_progress_before = progress_of(chain, aid_a)
        doctored = copy.deepcopy(event_b)
        doctored["activity_ref"] = aid_a
        doctored["target_ref"] = artifact_a
        doctored_result = runtime.progress_coordinator.receive_settled_event(doctored)
        check("re-labelled real receipt is refused", doctored_result.disposition == "IGNORE",
              {"disposition": doctored_result.disposition, "reason": doctored_result.reason})
        check("refusal names the activity identity mismatch",
              str(doctored_result.reason) == "SETTLEMENT_ACTIVITY_MISMATCH", doctored_result.reason)
        check("A gains no evidence from B's receipt", event_b.get("settlement_ref") not in active_settlements(chain, aid_a),
              active_settlements(chain, aid_a))
        check("A's checkpoint/progress did not move", progress_of(chain, aid_a) == a_progress_before,
              {"before": a_progress_before, "after": progress_of(chain, aid_a)})
        act_a = activity_for(chain, artifact_a)
        check("A is still PAUSED / not completed", act_a is not None and act_a.get("status") == "PAUSED"
              and not act_a.get("completion_evidence_ref"), act_a)

        # 6. NEGATIVE: an event id AR-1 never emitted is refused (do not trust the payload).
        fabricated = copy.deepcopy(event_b)
        fabricated["event_id"] = "srev:f167fabricated0001"
        fabricated["settlement_ref"] = "rset:f167fabricated0001"
        fabricated["activity_ref"] = aid_a
        fabricated["target_ref"] = artifact_a
        fabricated_result = runtime.progress_coordinator.receive_settled_event(fabricated)
        check("event not emitted by AR-1 is refused",
              fabricated_result.disposition == "IGNORE"
              and str(fabricated_result.reason) == "SETTLEMENT_EVENT_NOT_FOUND_IN_AUTHORITY",
              fabricated_result.reason)
        check("no Memory record was written by the refusals", memory_count(chain) == memory_before,
              {"before": memory_before, "after": memory_count(chain)})

        # 7. The untampered real event is still consumable by B after those refusals.
        dispatch = runtime.settlement_service.dispatch_outbox(event_id=str(event_b["event_id"]))
        check("B's untampered real event is still accepted", event_b.get("settlement_ref") in active_settlements(chain, aid_b),
              {"settlement_ref": event_b.get("settlement_ref"), "active": active_settlements(chain, aid_b)})
        check("dispatch reports the life consumer", bool(dispatch), dispatch)
        evidence_b_after = list(active_settlements(chain, aid_b))

        # 8. Re-delivery is an idempotent replay, not a second progress update.
        replay_b = runtime.progress_coordinator.receive_settled_event(event_b)
        check("re-delivering B's event is an idempotent replay", bool(replay_b.idempotent_replay), replay_b.reason)
        replay_a = runtime.progress_coordinator.receive_settled_event(event_a)
        check("re-delivering A's own event is an idempotent replay", bool(replay_a.idempotent_replay), replay_a.reason)
        check("B evidence count did not grow on replay", active_settlements(chain, aid_b) == evidence_b_after,
              active_settlements(chain, aid_b))

        summary["facts"] = {
            "activity_a": aid_a, "activity_b": aid_b,
            "event_a": event_a.get("event_id"), "event_b": event_b.get("event_id"),
            "doctored_disposition": doctored_result.disposition,
            "doctored_reason": doctored_result.reason,
            "fabricated_reason": fabricated_result.reason,
            "a_settlements": active_settlements(chain, aid_a),
            "b_settlements": active_settlements(chain, aid_b),
            "memory_records": memory_count(chain),
        }
        summary["checks"] = dict(CHECKS)
        ok = not FAILURES
        summary["ok"] = ok
        say("DEMO_OK" if ok else "DEMO_FAIL")
        print(json.dumps(summary, ensure_ascii=False, default=str, indent=1))
        return 0 if ok else 1
    finally:
        if chain is not None:
            try:
                base.close_chain(chain)
            except Exception as exc:  # the owner's own stop path; never bare-unlink here
                say("close_chain warning: %s" % exc)


if __name__ == "__main__":
    raise SystemExit(main())

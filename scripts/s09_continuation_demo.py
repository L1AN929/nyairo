#!/usr/bin/env python3
"""S09 demo: one continuous Activity through stop, wait, version change and end.

What is real (local only, 0 product model calls, 0 network)
----------------------------------------------------------
* a real Life Supply owner on its own Unix socket, a real instance prepared by
  ``one_day_life.prepare``, the real production Life runtime and
  ``assemble_autonomous_life``;
* real AG-0/AG-1/AG-2, LR-2/LR-3/LR-4/LR-5, AR-0/AR-1 and the real Supply owner
  receipts/versions/CAS;
* every Decision is a scripted double that only selects inside the real AG-1 legal
  set, and every content answer is a scripted double.  No product model is called.

The single chain demonstrates the six S09 behaviours
-----------------------------------------------------
1. F-103 continuity: adopt one work Activity, commit a real version, save the stop
   point, later RESUME the *same* Activity (same id, no second START).
2. F-104 safe-boundary interruption: a focused Activity is paused only at a real
   boundary event; an ATOMIC Activity refuses the same PAUSE and is not forced to
   stop.
3. F-105 external wait: the writer confirms ``no_change``, a formal WAIT binds the
   owner head dependency; a WAITING front Activity no longer occupies the execution
   slot, so another real Activity starts while it waits.  Only a real owner head
   change lets LR-4 emit one OPEN eligibility, and NO_ACTION keeps it waiting.
4. F-106 version change: RESUME reads the fresh owner head; a concurrent head change
   during a step makes the owner refuse the stale expected head (no silent rebase),
   and a later fresh Decision continues on the new head.
5. F-107 completion: an ordinary committed version does NOT complete the work; a
   bounded closeout Activity completes only after the owner's own archive receipt
   satisfies the LR-5 completion contract (``completion_evidence_ref`` set).
6. F-108 outcome separation: active ABANDON, Action FAILED, Action UNKNOWN
   (reconciled by the original owner operation), and Activity failure/blocked
   equivalents are kept apart.  External CANCEL is refused because the production
   runtime mints no CANCEL authority capability, and the current Activity enum has
   no terminal FAILED/BLOCKED state; both are reported as named gaps, not invented.

Boundaries: this is not G1, not a 24h run, and not evidence that a real model
naturally makes these choices.  The World is not used; all effects are on the
real Supply owner and the real Life/Activity owners.  Decision/content are doubles.

Run (WSL/POSIX)::

    PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/s09_continuation_demo.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "phone"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "tests"), str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import activity_continuity as ac  # noqa: E402
import activity_interruption as ai  # noqa: E402
import life_artifact_port as lap  # noqa: E402
import managed_artifact_action_adapter as maaa  # noqa: E402
import model_continuation_writer as mcw  # noqa: E402
import one_day_life as odl  # noqa: E402
import result_method_behavior_smoke as base  # noqa: E402
import v05_restart_continuation_smoke as restart  # noqa: E402
from components.phone import own_day_read  # noqa: E402

TS0 = "2026-11-20T09:00:00+00:00"          # 17:00 Asia/Shanghai
DAY = "2026-11-20"
FAILURES: list[str] = []


# ---------------------------------------------------------------------------------------------
# small reporting helpers
# ---------------------------------------------------------------------------------------------


def say(text: str) -> None:
    print(text)
    sys.stdout.flush()


def check(name: str, condition: Any, detail: Any = None) -> bool:
    ok = bool(condition)
    say("CHECK %-72s %s" % (name, "PASS" if ok else "FAIL"))
    if not ok:
        FAILURES.append(name)
        if detail is not None:
            say("  detail: " + json.dumps(detail, ensure_ascii=False, default=str)[:1800])
    return ok


def brief(out: Mapping[str, Any]) -> dict[str, Any]:
    return {key: out.get(key) for key in (
        "outcome", "decision", "adoption_status", "step_path", "detail", "error",
        "activity_ref", "action_id", "failure_class", "exit")}


def activity(chain, activity_id: str) -> dict[str, Any]:
    return dict(chain.runtime.activity_read.get_activity(str(activity_id)) or {})


def canonical(chain) -> dict[str, Any]:
    state, _journal = chain.runtime.activity_read.read_canonical_state(
        include_journal=False)
    return dict(state or {})


def head(chain, artifact_id: str) -> str:
    return str((chain.port.read_head(str(artifact_id)) or {}).get("version_id") or "")


def versions(chain, artifact_id: str) -> list[dict[str, Any]]:
    listed = chain.port.list_versions(str(artifact_id), limit=50) or {}
    return list(listed.get("entries") or [])


def version_numbers(chain, artifact_id: str) -> list[int]:
    return sorted(int(row.get("version_number") or 0) for row in versions(chain, artifact_id))


def conditions(chain) -> dict[str, Any]:
    return dict(chain.runtime.waiting_coordinator.condition_store.load_state()
                .get("conditions") or {})


def eligibilities(chain) -> dict[str, Any]:
    return dict(chain.runtime.waiting_coordinator.eligibility_store.load_state()
                .get("eligibilities") or {})


def open_eligibilities(chain) -> list[dict[str, Any]]:
    return [row for row in eligibilities(chain).values() if row.get("status") == "OPEN"]


def pending_interruptions(chain, activity_id: str) -> list[dict[str, Any]]:
    state = chain.runtime.interruption_coordinator.interruption_store.load_state() or {}
    return [row for row in (state.get("pending_interruptions") or {}).values()
            if str(row.get("activity_id") or "") == str(activity_id)
            and str(row.get("status") or "") in {"OPEN", "REGISTERED", "PENDING",
                                                  "READY_AT_BOUNDARY"}]


def candidate_set(chain):
    return chain.runtime.candidate_materializer.build_candidate_set(observed_at=TS0)


def candidate(chain, candidate_id: str) -> dict[str, Any]:
    return dict(chain.runtime.candidate_read.get_candidate(str(candidate_id)) or {})


def find_candidate(chain, predicate) -> Optional[str]:
    for ref in candidate_set(chain).candidate_refs:
        payload = candidate(chain, str(ref))
        if predicate(payload, str(ref)):
            return str(ref)
    return None


def find_work_candidate(chain, artifact_id: str) -> Optional[str]:
    want = str(artifact_id).split(":")[-1]

    def matches(payload, _ref):
        refs = [str(value) for value in (payload.get("target_refs") or [])]
        return (any(want in value for value in refs)
                and not any(value.startswith("closeout:") for value in refs))

    return find_candidate(chain, matches)


def find_closeout_candidate(chain) -> Optional[str]:
    return find_candidate(chain, lambda payload, _ref: any(
        str(value).startswith("closeout:") for value in (payload.get("target_refs") or [])))


def find_kind_candidate(chain, kind: str) -> Optional[str]:
    return find_candidate(chain, lambda payload, _ref:
                          str(payload.get("candidate_kind") or "") == kind)


def find_current_candidate(chain, artifact_id: Optional[str] = None) -> Optional[str]:
    """The current-activity candidate for this round, preferring the fresh owner head.

    A failed step can leave the previous candidate (same Activity, older declared version)
    in the set alongside a newly materialised one.  Selecting the older one would make the
    next CONTINUE/PAUSE illegal for a reason the subject did not choose, so prefer the
    candidate whose targets name the owner's current head; fall back to the last one.
    """

    matches: list[tuple[str, dict[str, Any]]] = []
    for ref in candidate_set(chain).candidate_refs:
        payload = candidate(chain, str(ref))
        if str(payload.get("candidate_kind") or "") == "CONTINUE_CURRENT":
            matches.append((str(ref), payload))
    if not matches:
        return None
    if artifact_id:
        wanted = head(chain, artifact_id).split(":")[-1]
        if wanted:
            for ref, payload in matches:
                if any(wanted in str(value) for value in (payload.get("target_refs") or [])):
                    return ref
    return matches[-1][0]


def run_round(chain, tag: str) -> dict[str, Any]:
    return dict(chain.cycle.evaluate_once(
        trigger_ref="evt:s09:%s" % tag, observed_at=TS0, act=True) or {})


def choose(chain, decision: str, candidate_id: Optional[str], **extra) -> None:
    script = {"decision": decision, "selected_candidate_id": candidate_id,
              "deferred_candidate_ids": []}
    script.update(extra)
    chain.cognition.script = script


def bump_work(chain, artifact_id: str, tag: str) -> dict[str, Any]:
    """A real second writer on the same owner head (CAS, not a fake revision)."""

    current = chain.port.resume_point(str(artifact_id))
    result = chain.port.commit_version(
        artifact_id=str(artifact_id),
        expected_head=str(current.head_version_id or ""),
        content="%s\n\n【外部写者 %s】\n" % (current.content or "", tag),
        grant_id=str(chain.data["artifact_grant_id"]),
        operation_key=lap.operation_key_for("s09-continuation", tag, "external"))
    return result.to_dict()


def phone_rows(chain) -> list[dict[str, Any]]:
    state_root = odl.life_state_root(chain.root / "instance")
    rows, _source = own_day_read.read_today_activities(state_root, DAY)
    return rows


# ---------------------------------------------------------------------------------------------
# writers and restart
# ---------------------------------------------------------------------------------------------


class WriterControl:
    """The content double the cycle sees, with two controllable fault seams.

    ``no_change`` makes the writer answer the legal ``ModelNoChange`` (the source of
    the WAIT basis).  ``before_write`` runs a real owner write immediately before the
    cycle's own commit, which is how the stale-expected-head CAS is exercised.
    """

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.no_change = False
        self.before_write = None

    def next_content(self, point: Any, *, observed_at: str,
                     context: Optional[Mapping[str, Any]] = None) -> str:
        if self.no_change:
            raise mcw.ModelNoChange("stub: nothing to add on this head")
        hook = self.before_write
        self.before_write = None
        if hook is not None:
            hook()
        if context is not None:
            return self.inner.next_content(point, observed_at=observed_at, context=context)
        return self.inner.next_content(point, observed_at=observed_at)


def bind(chain, control: Optional[WriterControl] = None) -> WriterControl:
    chain.cycle._business_clock = lambda: datetime.fromisoformat(TS0)
    inner = chain.cycle.writer._inner
    if control is None:
        control = WriterControl(inner)
    else:
        control.inner = inner
    chain.cycle.writer._inner = control
    return control


def close_chain(chain) -> dict[str, Any]:
    report = restart.close_chain(chain)
    check("the owner was stopped with drain + its own socket guard",
          report.get("closed") is True and report.get("service_stopped") is True
          and report.get("service_drained") is True
          and str(report.get("service_socket_handling") or "") in {
              "STALE_SOCKET_REMOVED", "NO_SOCKET", "ABSENT", "CLEAN"},
          report)
    return report


def reopen_chain(root: Path, control: WriterControl):
    reopened = restart.open_chain(Path(root), "s09-p2")
    control = bind(reopened, control)
    check("the same root re-assembled a new runtime and read the same Activity state",
          bool(canonical(reopened).get("activities")), canonical(reopened))
    return reopened, control


# ---------------------------------------------------------------------------------------------
# one chain
# ---------------------------------------------------------------------------------------------


def main() -> int:
    chain = base.build_chain("s09-continuation")
    control = bind(chain)
    work = str(chain.data["artifact_id"])
    grant = str(chain.data["artifact_grant_id"])
    workspace = str(chain.data["workspace_id"])
    summary: dict[str, Any] = {"root": str(chain.root), "work": work, "rounds": {}}

    say("\n== F-103/F-105/F-106: one work Activity through write, WAIT, RESUME ==")
    initial_head = head(chain, work)
    choose(chain, "START", find_work_candidate(chain, work))
    first = run_round(chain, "start")
    summary["rounds"]["start"] = brief(first)
    check("a real START adopted the work Activity and committed a real version",
          first.get("outcome") == "ACTED" and first.get("decision") == "START"
          and first.get("adoption_status") == "APPLIED"
          and bool((first.get("step_receipt") or {}).get("version_id")),
          first)
    activity_a = str(first.get("activity_ref") or "")
    record_a = activity(chain, activity_a)
    check("the stop point is recorded on the Activity and the owner's own head",
          record_a.get("status") == ac.STATUS_ACTIVE
          and bool(record_a.get("checkpoint_ref"))
          and head(chain, work) != initial_head,
          {"activity": record_a, "head": head(chain, work)})
    version_after_start = version_numbers(chain, work)
    check("the first branch wrote a new version on top of the seed",
          len(version_after_start) >= 2 and version_after_start[-1] == 2,
          version_after_start)

    # no_change is the only legal source of the WAIT basis.
    control.no_change = True
    choose(chain, "CONTINUE", find_current_candidate(chain, work))
    no_change = run_round(chain, "no-change")
    summary["rounds"]["no_change"] = brief(no_change)
    check("the writer's no_change is a legal no-op, not a failed step",
          no_change.get("outcome") == "NO_ACTION"
          and (no_change.get("work_no_change") or {}).get("version_created") is False,
          no_change)
    control.no_change = False

    choose(chain, "WAIT", find_current_candidate(chain, work))
    waited = run_round(chain, "wait")
    summary["rounds"]["wait"] = brief(waited)
    record_a = activity(chain, activity_a)
    check("WAIT binds the owner-head dependency and leaves the Activity WAITING",
          waited.get("decision") == "WAIT"
          and waited.get("adoption_status") == "APPLIED"
          and record_a.get("status") == ac.STATUS_WAITING
          and bool(record_a.get("waiting_on_ref")),
          {"out": waited, "activity": record_a})
    check("the wait has a real owner head baseline, not a timer",
          any(str(row.get("waiting_on_ref") or "").startswith("wait_dep:artifact.")
              for row in conditions(chain).values()),
          conditions(chain))

    # a real owner head change is the only thing that can satisfy the condition
    external = bump_work(chain, work, "wait-head")
    check("the owner really advanced the head while the Activity waited",
          external.get("effect_state") == "APPLIED", external)
    maintained = chain.cycle.maintain_waiting(observed_at=TS0)
    open_rows = open_eligibilities(chain)
    check("maintenance spent no request, did not auto-resume, and emitted one OPEN eligibility",
          int(maintained.get("llm_calls") or 0) == 0
          and not maintained.get("auto_resumed")
          and len(open_rows) == 1
          and str(open_rows[0].get("activity_id")) == activity_a,
          {"maintained": maintained, "open": open_rows})
    check("the Activity is still WAITING after the eligibility opens",
          activity(chain, activity_a).get("status") == ac.STATUS_WAITING,
          activity(chain, activity_a))

    # the public read sees WAITING with the owner-supported RESUME entry
    row_a = next((row for row in phone_rows(chain) if row.get("ref") == activity_a), None)
    check("the public /today read shows WAITING and the owner-supported RESUME entry",
          bool(row_a) and row_a.get("status") == ac.STATUS_WAITING
          and (row_a.get("recovery") or {}).get("next_legal_entry") == "RESUME"
          and (row_a.get("recovery") or {}).get("reason_code")
          == own_day_read.RECOVERY_ELIGIBILITY_OPEN,
          row_a)

    choose(chain, "NO_ACTION", None)
    quiet = run_round(chain, "no-action-while-open")
    check("NO_ACTION keeps the Activity WAITING and the eligibility OPEN",
          quiet.get("decision") == "NO_ACTION"
          and activity(chain, activity_a).get("status") == ac.STATUS_WAITING
          and len(open_eligibilities(chain)) == 1,
          {"out": quiet, "activity": activity(chain, activity_a)})
    # The NO_ACTION above suppressed the exact same situation; a real new opportunity is
    # about to appear, so ask for one fresh consideration.  This only lifts the silence --
    # it does not resume, act, or consume the eligibility.
    released = chain.cycle.reconsideration.release(refs=[activity_a, work])
    check("an explicit fresh consideration releases only the suppression, not the Activity",
          isinstance(released, list)
          and activity(chain, activity_a).get("status") == ac.STATUS_WAITING
          and len(open_eligibilities(chain)) == 1,
          {"released": released, "activity": activity(chain, activity_a)})

    # ---- F-105: a WAITING front Activity does not occupy the execution slot ------------
    say("\n== F-105 slot release: another real Activity starts while A waits ==")
    work_b = chain.port.create_artifact(
        workspace_id=workspace, title="作品乙", content="乙的第一句。\n", grant_id=grant,
        operation_key=lap.operation_key_for("s09-continuation", "create", "b"))
    check("the owner created a second real work",
          work_b.applied and bool(work_b.artifact_id), work_b.to_dict())
    choose(chain, "START", find_work_candidate(chain, work_b.artifact_id))
    started_b = run_round(chain, "start-b-while-a-waits")
    activity_b = str(started_b.get("activity_ref") or "")
    check("START B succeeds while A is WAITING: the execution slot was really released",
          started_b.get("outcome") == "ACTED"
          and started_b.get("decision") == "START"
          and bool(activity_b)
          and activity(chain, activity_a).get("status") == ac.STATUS_WAITING,
          {"out": started_b, "a": activity(chain, activity_a),
           "b": activity(chain, activity_b)})

    # ---- F-104/F-108: ATOMIC refuses the pause, external CANCEL is a different terminal
    say("\n== F-104/F-108: ATOMIC refuses the pause; external CANCEL is a named gap ==")
    chain.runtime.interruption_coordinator.configure_activity_attention(
        activity_id=activity_b, attention_occupancy=ai.OCCUPANCY_ATOMIC,
        interruptibility=ai.INTERRUPTIBILITY_ATOMIC,
        atomic_region_ref="atomic_region:s09:demo:1", max_atomic_duration_seconds=60)
    choose(chain, "PAUSE", find_current_candidate(chain, work_b.artifact_id))
    atomic_pause = run_round(chain, "atomic-pause")
    check("ATOMIC refuses the PAUSE instead of forcing a stop",
          atomic_pause.get("outcome") == "DECISION_FAILED"
          and "ATOMIC" in str(atomic_pause.get("detail") or "")
          and activity(chain, activity_b).get("status") == ac.STATUS_ACTIVE,
          {"out": atomic_pause, "activity": activity(chain, activity_b)})

    record_b = activity(chain, activity_b)
    cancel_refusal = None
    try:
        chain.runtime.activity_command.cancel_activity(
            activity_id=activity_b, expected_revision=int(record_b["revision"]),
            idempotency_key="s09:external-cancel:b",
            source_refs=["auth:revocation:s09-demo-b"],
            reason_code="CANCELLED_BY_DECISION", occurred_at=TS0, observed_at=TS0)
    except Exception as exc:  # expected: no production CANCEL consequence capability
        cancel_refusal = "%s: %s" % (type(exc).__name__, str(exc))
    check("external CANCEL is refused by owner-backed admission (no CANCEL capability minted)",
          cancel_refusal is not None
          and "NO_AUTHORITY_CAPABILITY_FOR_TRANSITION" in cancel_refusal
          and activity(chain, activity_b).get("status") == ac.STATUS_ACTIVE,
          {"refusal": cancel_refusal, "activity": activity(chain, activity_b)})
    summary["named_gap_cancel"] = (
        "CANCELLED exists in LR-2 but the production runtime mints no CANCEL "
        "ACTIVITY_CONSEQUENCE capability; adding one is a new permission, so the "
        "external-cancel sub-item is a named STOP and was not invented")

    # leave the ATOMIC branch through the owner's own ATOMIC_EXIT boundary, not a guessed stop
    events = chain.runtime.interruption_coordinator.assess_events(
        [{"event_kind": ai.EVENT_KIND_COMMUNICATION,
          "event_id": "comm_evt:s09:demo:atomic",
          "channel_ref": "channel:internal_adoption",
          "message_ref": "msg:s09:demo:atomic",
          "message_class": ai.MESSAGE_CLASS_EXPLICIT_HOLD_REQUEST,
          "request_boundary_pause": True,
          "source_refs": ["evt:s09:atomic-pause"],
          "occurred_at": TS0, "observed_at": TS0}],
        apply_immediate_pause=False)
    atomic_exit = {
        "boundary_ref": "atomic_exit:s09:demo:1",
        "activity_ref": activity_b,
        "source_ref": "executor_boundary:s09:demo:1",
        "boundary_kind": "ATOMIC_EXIT",
        "atomic_region_ref": "atomic_region:s09:demo:1",
        "occurred_at": TS0, "observed_at": TS0,
        "recorded_at": TS0, "enqueued_at": TS0,
        "causal_parent_refs": [],
        "source_refs": ["atomic_exit:s09:demo:1", "executor_boundary:s09:demo:1"],
    }
    atomic_boundary = chain.runtime.interruption_coordinator.handle_boundary_reached(atomic_exit)
    check("the ATOMIC branch ends only at its own exit boundary and frees the slot",
          bool(events)
          and activity(chain, activity_b).get("status") == ac.STATUS_PAUSED
          and canonical(chain).get("foreground_activity_ref") is None,
          {"events": events, "boundary": atomic_boundary,
           "activity": activity(chain, activity_b)})

    # ---- F-103: ordered same-root re-assembly while A is WAITING + OPEN ----------------
    say("\n== F-103 restart: the same root re-assembled while WAITING + OPEN ==")
    waiting_before = activity(chain, activity_a)
    condition_before = dict(conditions(chain))
    eligibility_before = dict(eligibilities(chain))
    close_chain(chain)
    chain, control = reopen_chain(chain.root, control)
    waiting_after = activity(chain, activity_a)
    check("the WAITING Activity, its stop point and revision survived the restart",
          waiting_after.get("status") == ac.STATUS_WAITING
          and waiting_after.get("revision") == waiting_before.get("revision")
          and waiting_after.get("waiting_on_ref") == waiting_before.get("waiting_on_ref")
          and waiting_after.get("checkpoint_ref") == waiting_before.get("checkpoint_ref"),
          {"before": waiting_before, "after": waiting_after})
    check("the resume condition and the OPEN eligibility survived without duplication",
          dict(conditions(chain)) == condition_before
          and dict(eligibilities(chain)) == eligibility_before
          and len(open_eligibilities(chain)) == 1,
          {"condition_before": condition_before, "condition_after": conditions(chain),
           "elig_before": eligibility_before, "elig_after": eligibilities(chain)})

    # a stale Activity revision must not be reported as a resume
    _elig = open_eligibilities(chain)[0]
    _waiting = activity(chain, activity_a)
    stale_refusal = None
    other_refusal = None
    try:
        chain.runtime.waiting_coordinator.explicit_resume_from_eligibility(
            eligibility_id=str(_elig["eligibility_id"]),
            expected_revision=int(_waiting["revision"]) - 1,
            idempotency_key="s09:stale-resume",
            decision_ref="dec:s09-stale-resume",
            adoption_ref="adp:s09-stale-resume",
            source_refs=["evt:s09:stale-resume"],
            occurred_at=TS0, observed_at=TS0)
    except ac.RevisionConflictError as exc:
        stale_refusal = "%s: %s" % (type(exc).__name__, str(exc))
    except Exception as exc:  # a different refusal is a real failure, not the control
        other_refusal = "%s: %s" % (type(exc).__name__, str(exc))
    check("a stale resume revision is refused; the eligibility and Activity stay untouched",
          stale_refusal is not None and other_refusal is None
          and activity(chain, activity_a).get("status") == ac.STATUS_WAITING
          and len(open_eligibilities(chain)) == 1,
          {"stale_refusal": stale_refusal, "other": other_refusal,
           "activity": activity(chain, activity_a),
           "open": len(open_eligibilities(chain))})

    # a new real owner fact after the restart releases the same-state fingerprint
    bump_work(chain, work, "after-restart")
    chain.cycle.maintain_waiting(observed_at=TS0)
    head_before_resume = head(chain, work)
    choose(chain, "RESUME", find_kind_candidate(chain, "RESUME_ACTIVITY"))
    resumed = run_round(chain, "resume")
    summary["rounds"]["resume"] = brief(resumed)
    record_a = activity(chain, activity_a)
    consumption = {row.get("status") for row in eligibilities(chain).values()}
    check("RESUME returns to the same Activity id, not a new START",
          resumed.get("decision") == "RESUME"
          and resumed.get("adoption_status") == "APPLIED"
          and str(resumed.get("activity_ref") or "") == activity_a
          and record_a.get("status") == ac.STATUS_ACTIVE,
          {"out": resumed, "activity": record_a})
    check("the resume consumed the eligibility and wrote on the fresh owner head",
          "CONSUMED" in consumption
          and (resumed.get("step_receipt") or {}).get("expected_head")
          == head_before_resume,
          {"consumption": consumption, "step": resumed.get("step_receipt"),
           "head": head(chain, work)})
    version_after_resume = version_numbers(chain, work)
    check("the old committed version was not replayed: versions are unique",
          len(version_after_resume) == len(set(version_after_resume))
          and version_after_resume[-1] > version_after_start[-1],
          version_after_resume)

    # ---- F-106: a concurrent owner head makes the stale step refused, then fresh -------
    say("\n== F-106 version change: stale expected head is refused, never silently rebased ==")
    control.before_write = lambda: bump_work(chain, work, "concurrent")
    choose(chain, "CONTINUE", find_current_candidate(chain, work))
    refused = run_round(chain, "cas-refused")
    summary["rounds"]["cas_refused"] = brief(refused)
    record_a = activity(chain, activity_a)
    check("the concurrent head made the owner refuse the stale expected head",
          refused.get("outcome") == "STEP_REFUSED"
          and (refused.get("step_receipt") or {}).get("status") == "FAILED"
          and (refused.get("step_receipt") or {}).get("reason") == "CONFLICT",
          refused)
    check("the refused step left the Activity ACTIVE and did not write a version",
          record_a.get("status") == ac.STATUS_ACTIVE
          and version_numbers(chain, work)[-1] == version_after_resume[-1] + 1,
          {"activity": record_a, "versions": version_numbers(chain, work)})
    head_before_fresh = head(chain, work)
    choose(chain, "CONTINUE", find_current_candidate(chain, work))
    fresh = run_round(chain, "cas-fresh")
    check("a fresh Decision reads the new head and continues there",
          fresh.get("outcome") == "ACTED"
          and (fresh.get("step_receipt") or {}).get("expected_head") == head_before_fresh,
          brief(fresh))

    # ---- F-104: safe-boundary interruption on work A ----------------------------------
    say("\n== F-104 safe boundary: pending first, PAUSED only at the real boundary ==")
    chain.runtime.interruption_coordinator.configure_activity_attention(
        activity_id=activity_a, attention_occupancy=ai.OCCUPANCY_FOCUSED,
        interruptibility=ai.INTERRUPTIBILITY_SAFE_BOUNDARY,
        atomic_region_ref=None, max_atomic_duration_seconds=60)
    choose(chain, "PAUSE", find_current_candidate(chain, work))
    pending_out = run_round(chain, "safe-boundary-pause")
    summary["rounds"]["safe_boundary"] = brief(pending_out)
    record_a = activity(chain, activity_a)
    pendings = pending_interruptions(chain, activity_a)
    check("a safe-boundary PAUSE only registers a pending interruption; the Activity stays ACTIVE",
          pending_out.get("decision") == "PAUSE"
          and pending_out.get("adoption_status") == "APPLIED"
          and record_a.get("status") == ac.STATUS_ACTIVE
          and bool(pendings),
          {"out": brief(pending_out), "activity": record_a, "pendings": pendings})

    versions_before_boundary = version_numbers(chain, work)
    boundary = {
        "boundary_ref": "boundary:s09:demo:1",
        "activity_ref": activity_a,
        "source_ref": "step_boundary:s09:demo:1",
        "boundary_kind": "SAFE_BOUNDARY",
        "occurred_at": TS0, "observed_at": TS0,
        "recorded_at": TS0, "enqueued_at": TS0,
        "causal_parent_refs": [],
        "source_refs": ["boundary:s09:demo:1", "step_boundary:s09:demo:1"],
    }
    boundary_result = chain.runtime.interruption_coordinator.handle_boundary_reached(boundary)
    record_a = activity(chain, activity_a)
    check("the real boundary event pauses the same Activity and replays no version",
          record_a.get("status") == ac.STATUS_PAUSED
          and record_a.get("foreground_released") is True
          and version_numbers(chain, work) == versions_before_boundary,
          {"boundary": boundary_result, "activity": record_a,
           "versions": version_numbers(chain, work)})

    # ---- F-108: active ABANDON is a different terminal --------------------------------
    say("\n== F-108 active ABANDON on its own work ==")
    work_c = chain.port.create_artifact(
        workspace_id=workspace, title="作品丙", content="丙的第一句。\n", grant_id=grant,
        operation_key=lap.operation_key_for("s09-continuation", "create", "c"))
    choose(chain, "START", find_work_candidate(chain, work_c.artifact_id))
    started_c = run_round(chain, "start-c")
    activity_c = str(started_c.get("activity_ref") or "")
    choose(chain, "ABANDON", find_current_candidate(chain, work_c.artifact_id))
    abandoned = run_round(chain, "abandon-c")
    summary["rounds"]["abandon"] = brief(abandoned)
    check("a formal ABANDON is the subject's own terminal, not a cancel or a failure",
          abandoned.get("decision") == "ABANDON"
          and activity(chain, activity_c).get("status") == ac.STATUS_ABANDONED
          and bool(activity(chain, activity_c).get("terminal_reason_code")),
          {"out": brief(abandoned), "activity": activity(chain, activity_c)})

    paused_row = next((row for row in phone_rows(chain)
                       if row.get("ref") == activity_a), None)
    check("the public read says PAUSED with no open eligibility, not a fake next entry",
          bool(paused_row) and paused_row.get("status") == ac.STATUS_PAUSED
          and (paused_row.get("recovery") or {}).get("reason_code")
          == own_day_read.RECOVERY_NO_OPEN_ELIGIBILITY
          and (paused_row.get("recovery") or {}).get("next_legal_entry") is None,
          paused_row)

    # ---- F-107/F-108: closeout Action FAILED, then UNKNOWN across a restart -----------
    say("\n== F-107/F-108: closeout completes only on the owner's own archive receipt ==")
    check("a committed version alone never completed the work Activity",
          activity(chain, activity_a).get("completion_evidence_ref") is None,
          activity(chain, activity_a))

    # attempt 1: the owner revision moves between the Decision and the archive -> Action FAILED
    adapter = chain.runtime.file_adapter
    original_submit = adapter.submit
    state = {"bumped": False}

    def submit_with_concurrent_revision(*args, **kwargs):
        if not state["bumped"]:
            state["bumped"] = True
            bump_work(chain, work, "closeout-conflict")
        return original_submit(*args, **kwargs)

    adapter.submit = submit_with_concurrent_revision
    _close_ref = find_closeout_candidate(chain)
    choose(chain, "START", _close_ref)
    failed_closeout = run_round(chain, "closeout-conflict")
    summary["rounds"]["closeout_conflict"] = brief(failed_closeout)
    failed_action = str(failed_closeout.get("action_id") or "")
    action_row = dict(chain.runtime.action_read.get_action(failed_action) or {}) \
        if failed_action else {}
    attempts = chain.runtime.action_read.list_attempts(failed_action) if failed_action else []
    closeout_failed_activity = str(failed_closeout.get("activity_ref") or "")
    check("the stale archive is a definite Action FAILED, not an UNKNOWN",
          failed_closeout.get("outcome") == "STEP_REFUSED"
          and action_row.get("status") == "FAILED"
          and bool(attempts)
          and attempts[0].get("transport_result") == "DEFINITE_PROVIDER_REJECTION",
          {"out": failed_closeout, "action": action_row,
           "attempts": [dict(row) for row in attempts]})
    check("the failed Action did not become an Activity FAILED or a completion",
          activity(chain, closeout_failed_activity).get("status") == ac.STATUS_ACTIVE
          and activity(chain, closeout_failed_activity).get("completion_evidence_ref") is None
          and chain.port.resume_point(work).lifecycle == "ACTIVE",
          activity(chain, closeout_failed_activity))
    choose(chain, "ABANDON", find_current_candidate(chain, work))
    abandoned_attempt = run_round(chain, "abandon-failed-closeout")
    check("the failed closeout attempt is ABANDONED; the work is untouched",
          abandoned_attempt.get("decision") == "ABANDON"
          and activity(chain, closeout_failed_activity).get("status") == ac.STATUS_ABANDONED
          and chain.port.resume_point(work).lifecycle == "ACTIVE",
          {"out": abandoned_attempt, "activity": activity(chain, closeout_failed_activity),
           "work": chain.port.resume_point(work).lifecycle})

    # attempt 2: the owner archive really happens, the answer is lost -> Action UNKNOWN
    adapter.submit = original_submit
    adapter.fault_injection_drop_response_after_write = True
    choose(chain, "START", find_closeout_candidate(chain))
    lost = run_round(chain, "closeout-lost-answer")
    summary["rounds"]["closeout_unknown"] = brief(lost)
    unknown_action = str(lost.get("action_id") or "")
    lost_activity = str(lost.get("activity_ref") or "")
    check("the lost answer is an Action UNKNOWN, and the Activity is not completed",
          lost.get("outcome") == "STEP_UNKNOWN"
          and (chain.runtime.action_read.get_action(unknown_action) or {}).get("status")
          == "UNKNOWN"
          and activity(chain, lost_activity).get("status") == ac.STATUS_ACTIVE
          and activity(chain, lost_activity).get("completion_evidence_ref") is None,
          {"out": lost, "activity": activity(chain, lost_activity)})
    check("the owner effect is already real even though the answer was lost",
          chain.port.resume_point(work).lifecycle == "ARCHIVED",
          chain.port.resume_point(work).lifecycle)
    lost_action_row = dict(chain.runtime.action_read.get_action(unknown_action) or {})
    archive_key = maaa.SupplyManagedArtifactActionAdapter.operation_key_for(
        str(lost_action_row.get("idempotency_key") or ""))
    archive_row_before = chain.port.operation_row(archive_key)
    check("the owner has exactly one real archive operation for this action",
          bool(archive_row_before)
          and str(archive_row_before.get("op")
                  or archive_row_before.get("operation_kind") or "").upper()
          in {"ARCHIVE_ARTIFACT", "ARCHIVED"},
          archive_row_before)

    # ---- restart while the Action is UNKNOWN ------------------------------------------
    say("\n== ordered restart with an unresolved Action ==")
    close_chain(chain)
    chain, control = reopen_chain(chain.root, control)
    unresolved = dict(chain.runtime.action_read.get_action(unknown_action) or {})
    check("the unresolved Action survived the restart and is still UNKNOWN",
          unresolved.get("status") == "UNKNOWN", unresolved)
    check("the Activity and the owner effect also survived the restart",
          activity(chain, lost_activity).get("status") == ac.STATUS_ACTIVE
          and chain.port.resume_point(work).lifecycle == "ARCHIVED",
          {"activity": activity(chain, lost_activity),
           "work": chain.port.resume_point(work).lifecycle})

    reconciled = chain.cycle.reconcile_unknown_step(
        action_id=unknown_action, occurred_at=TS0, reason="S09 demo lost answer")
    summary["reconcile"] = reconciled
    completed = activity(chain, lost_activity)
    check("reconciliation used the original owner operation, not a second archive",
          reconciled.get("ok") is True
          and (reconciled.get("reconciliation") or {}).get("result") == "CONFIRMED_SUCCEEDED"
          and reconciled.get("reason_code") == "RECONCILED_SUCCEEDED",
          reconciled)
    check("the closeout Activity completes only with the owner's archive evidence",
          completed.get("status") == ac.STATUS_COMPLETED
          and bool(completed.get("completion_evidence_ref"))
          and completed.get("terminal_reason_code") == "COMPLETED_WITH_EVIDENCE",
          completed)
    archive_row_after = chain.port.operation_row(archive_key)
    check("reconciliation did not mint a second archive operation",
          bool(archive_row_after)
          and str(archive_row_after.get("operation_key") or "") == str(archive_key)
          and archive_row_after.get("revision") == archive_row_before.get("revision"),
          {"before": archive_row_before, "after": archive_row_after})

    # ---- final phone read: all terminals are distinct ---------------------------------
    say("\n== public /today read: WAITING / PAUSED / ABANDONED / COMPLETED ==")
    final_rows = phone_rows(chain)
    by_ref = {str(row.get("ref") or ""): row for row in final_rows}
    summary["phone"] = {
        "statuses": {ref: row.get("status") for ref, row in by_ref.items()},
        "recovery": {ref: (row.get("recovery") or {}).get("reason_code")
                     for ref, row in by_ref.items()},
    }
    check("the read keeps PAUSED (no open eligibility) apart from ABANDONED and COMPLETED",
          (by_ref.get(activity_a) or {}).get("status") == ac.STATUS_PAUSED
          and (by_ref.get(activity_b) or {}).get("status") == ac.STATUS_PAUSED
          and (by_ref.get(activity_b) or {}).get("recovery", {}).get("reason_code")
          == own_day_read.RECOVERY_NO_OPEN_ELIGIBILITY
          and (by_ref.get(activity_c) or {}).get("status") == ac.STATUS_ABANDONED
          and (by_ref.get(lost_activity) or {}).get("status") == ac.STATUS_COMPLETED,
          {"row_a": by_ref.get(activity_a), "row_b": by_ref.get(activity_b),
           "row_c": by_ref.get(activity_c), "row_closeout": by_ref.get(lost_activity)})
    check("a step failure/blocked result is not shown as an Activity FAILED status",
          failed_closeout.get("outcome") == "STEP_REFUSED"
          and (by_ref.get(closeout_failed_activity) or {}).get("status")
          == ac.STATUS_ABANDONED,
          {"round": failed_closeout.get("outcome"),
           "row": by_ref.get(closeout_failed_activity)})

    # named gap, not invented: the current Activity enum has no FAILED/BLOCKED terminal.
    summary["named_gap"] = {
        "activity_terminal_statuses": sorted(ac.TERMINAL_ACTIVITY_STATUSES),
        "failed_blocked": "NO_PHYSICAL_ENUM; Action/step failure and the blocked "
                          "outcome remain evidence while the Activity stays ACTIVE/WAITING",
        "external_cancel": summary.get("named_gap_cancel"),
    }
    say("NAMED_GAP Activity FAILED/BLOCKED has no physical enum; not invented. "
        "Action FAILED/UNKNOWN are evidence only; the Activity status is unchanged.")

    try:
        close_chain(chain)
    except Exception as exc:  # noqa: BLE001 - printed, not hidden
        say("  close error: %s: %s" % (type(exc).__name__, str(exc)[:200]))
    summary["ok"] = not FAILURES
    say("\n" + json.dumps({"ok": summary["ok"], "root": summary["root"],
                           "named_gap": summary["named_gap"]},
                          ensure_ascii=False, default=str))
    if FAILURES:
        say("FAILED %d check(s): %s" % (len(FAILURES), ", ".join(FAILURES)))
        return 1
    say("S09_CONTINUATION_DEMO_OK")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as exc:  # a failed setup is a failed demo, never silent
        say("DEMO_FAIL: %s: %s" % (type(exc).__name__, str(exc)[:400]))
        raise SystemExit(1)

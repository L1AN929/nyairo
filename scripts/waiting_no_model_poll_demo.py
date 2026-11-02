#!/usr/bin/env python3
"""A long logical wait that never polls the model: WAIT → upkeep → one OPEN → RESUME.

What is real (local only, 0 product model calls, 0 network)
----------------------------------------------------------
* a real Life Supply owner on its own Unix socket, a real instance prepared by
  ``one_day_life.prepare``, the real production Life runtime and
  ``assemble_autonomous_life`` (same fixture as ``scripts/s09_continuation_demo.py``);
* real AG-0/AG-1/AG-2, LR-2/LR-4, AR-0/AR-1, and the real Supply owner head/CAS;
* the formal upkeep entry ``AutonomousLifeRunner.maintain`` -- the same object the
  assembly builds for a resident, constructed here over the fixture's own cycle
  because the shared fixture hands back ``cycle`` but not the runner.  It is a verbatim
  pass-through to ``AutonomousLifeCycle.maintain_waiting``;
* every Decision is a scripted double that only selects inside the real AG-1 legal set,
  every content answer is a scripted double.  No product model is called.

What this demonstrates
----------------------
1. A real WAIT (AG-1 → AG-2) binds the owner-head dependency of the *same* Activity and
   leaves it WAITING with one PENDING condition (no timer, no TTL).
2. Logical time is injected explicitly (``+1h``, ``+6h``, ``+24h``): upkeep re-reads the
   owner on every tick, the head is unchanged every time, the condition stays PENDING, and
   the upkeep spends **zero** model calls, mints no Action and writes no version.  The
   budget snapshot does not move across an upkeep call.  This is not a real day — the
   clock is moved by argument, not by waiting.
3. An orderly stop/drain/close + re-assembly of the *same root* in the *same process*
   (same PID; this is NOT a cross-process restart) keeps the WAITING Activity, the stop
   point, the condition and its baseline.
4. Only a real owner head change lets upkeep emit exactly one OPEN eligibility; the
   Activity is still WAITING, and repeating upkeep does not duplicate it.
5. ``NO_ACTION`` keeps waiting and the OPEN eligibility.  That round is a normal cognition
   round and it *is* paid (``llm_calls == 1``): the demo separates "upkeep spent nothing"
   from "normal consideration is not free", and never silences the model to fake zero.
6. A fresh Decision RESUME returns to the same Activity id, consumes the eligibility once
   and continues on the new owner head.

A second, unrelated work B is created and must stay untouched throughout.

Boundaries: this is not G1, not a 24h soak, not evidence that a real model naturally makes
these choices.  No real product model, no World, no old instance.

Run (WSL/POSIX)::

    PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/waiting_no_model_poll_demo.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
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

# The shared fixture creates its root under this directory; point it at a retained
# mkdtemp parent so this run never cleans up its own evidence (no rmtree/cleanup).
# (a short path matters: the owner's socket lives under this root and AF_UNIX caps ~108 bytes)
_FIXTURE_PARENT = tempfile.mkdtemp(prefix="nyairo-wnmp-")
os.environ.setdefault("NYAIRO_RESULT_METHOD_FIXTURE_ROOT", _FIXTURE_PARENT)

import activity_continuity as ac  # noqa: E402
import autonomous_life_runner as alr  # noqa: E402
import life_artifact_port as lap  # noqa: E402
import one_day_life as odl  # noqa: E402
import model_continuation_writer as mcw  # noqa: E402
import result_method_behavior_smoke as fixture  # noqa: E402
import v05_restart_continuation_smoke as restart  # noqa: E402

TS0 = "2026-11-20T09:00:00+00:00"
FAILURES: list[str] = []


# ---------------------------------------------------------------------------------------------
# reporting (stdout carries refs / status / counts / bools only -- never text or whole objects)
# ---------------------------------------------------------------------------------------------


_SCALAR = (str, int, float, bool, type(None))


def safe(value: Any, *, depth: int = 0) -> Any:
    """Keep only shallow scalars; never print a nested object or a long text."""

    if isinstance(value, Mapping):
        if depth >= 1:
            return "map:%d" % len(value)
        out: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if isinstance(item, _SCALAR):
                out[name] = (item[:120] if isinstance(item, str) and len(item) > 120 else item)
            elif isinstance(item, (list, tuple, set)):
                out[name] = "list:%d" % len(item)
            elif isinstance(item, Mapping):
                out[name] = "map:%d" % len(item)
            else:
                out[name] = type(item).__name__
        return out
    if isinstance(value, (list, tuple, set)):
        return "list:%d" % len(value)
    if isinstance(value, _SCALAR):
        return value[:120] if isinstance(value, str) and len(value) > 120 else value
    return type(value).__name__


def say(text: str) -> None:
    print(text)
    sys.stdout.flush()


def check(name: str, condition: Any, detail: Any = None) -> bool:
    ok = bool(condition)
    say("CHECK %-70s %s" % (name, "PASS" if ok else "FAIL"))
    if not ok:
        FAILURES.append(name)
        if detail is not None:
            say("  detail: " + json.dumps(safe(detail), ensure_ascii=False, default=str)[:900])
    return ok


# ---------------------------------------------------------------------------------------------
# a movable logical clock (explicit injection -- it is not a real day)
# ---------------------------------------------------------------------------------------------


class LogicalClock:
    def __init__(self, iso: str) -> None:
        self.current = datetime.fromisoformat(iso)

    def at(self, iso: str) -> datetime:
        self.current = datetime.fromisoformat(iso)
        return self.current

    def shift(self, **delta: float) -> datetime:
        self.current = self.current + timedelta(**delta)
        return self.current

    def now(self) -> datetime:
        return self.current

    def iso(self) -> str:
        return self.current.isoformat()


def plus(iso: str, **delta: float) -> str:
    return (datetime.fromisoformat(iso) + timedelta(**delta)).isoformat()


# ---------------------------------------------------------------------------------------------
# reads (owner / Activity / LR-4 stores)
# ---------------------------------------------------------------------------------------------


def activity(chain, activity_id: str) -> dict[str, Any]:
    return dict(chain.runtime.activity_read.get_activity(str(activity_id)) or {})


def canonical(chain) -> dict[str, Any]:
    state, _journal = chain.runtime.activity_read.read_canonical_state(include_journal=False)
    return dict(state or {})


def head(chain, artifact_id: str) -> str:
    return str((chain.port.read_head(str(artifact_id)) or {}).get("version_id") or "")


def version_numbers(chain, artifact_id: str) -> list[int]:
    listed = chain.port.list_versions(str(artifact_id), limit=50) or {}
    rows = list(listed.get("entries") or [])
    return sorted(int(row.get("version_number") or 0) for row in rows)


def conditions(chain) -> dict[str, Any]:
    return dict(chain.runtime.waiting_coordinator.condition_store.load_state()
                .get("conditions") or {})


def eligibilities(chain) -> dict[str, Any]:
    return dict(chain.runtime.waiting_coordinator.eligibility_store.load_state()
                .get("eligibilities") or {})


def open_eligibilities(chain) -> list[dict[str, Any]]:
    return [row for row in eligibilities(chain).values() if row.get("status") == "OPEN"]


def wait_condition(chain) -> dict[str, Any]:
    for row in conditions(chain).values():
        if str(row.get("waiting_on_ref") or "").startswith("wait_dep:artifact."):
            return dict(row)
    return {}


def action_count(chain) -> int:
    return len(chain.runtime.action_read.list_actions())


def budget_counts(chain) -> dict[str, Any]:
    snap = dict(chain.cycle.budget.snapshot() or {})
    requests = snap.get("requests") if isinstance(snap.get("requests"), Mapping) else {}
    return {
        "spent_today": snap.get("spent_today"),
        "cognition_used_today": snap.get("cognition_used_today"),
        "actions_used_today": snap.get("actions_used_today"),
        "requests_used": requests.get("used") if isinstance(requests, Mapping) else None,
    }


# ---------------------------------------------------------------------------------------------
# the round drivers (same shapes the s09 demo uses)
# ---------------------------------------------------------------------------------------------


def candidates(chain, clock: LogicalClock):
    return chain.runtime.candidate_materializer.build_candidate_set(observed_at=clock.iso())


def candidate_payload(chain, candidate_id: str) -> dict[str, Any]:
    return dict(chain.runtime.candidate_read.get_candidate(str(candidate_id)) or {})


def find_candidate(chain, clock: LogicalClock, predicate) -> Optional[str]:
    for ref in candidates(chain, clock).candidate_refs:
        payload = candidate_payload(chain, str(ref))
        if predicate(payload, str(ref)):
            return str(ref)
    return None


def find_current_candidate(chain, clock: LogicalClock, artifact_id: str) -> Optional[str]:
    """The fresh CONTINUE_CURRENT candidate whose targets name the owner's current head."""

    wanted = head(chain, artifact_id).split(":")[-1]
    matches: list[tuple[str, dict[str, Any]]] = []
    for ref in candidates(chain, clock).candidate_refs:
        payload = candidate_payload(chain, str(ref))
        if str(payload.get("candidate_kind") or "") == "CONTINUE_CURRENT":
            matches.append((str(ref), payload))
    if not matches:
        return None
    if wanted:
        for ref, payload in matches:
            if any(wanted in str(value) for value in (payload.get("target_refs") or [])):
                return ref
    return matches[-1][0]


def find_work_candidate(chain, clock: LogicalClock, artifact_id: str) -> Optional[str]:
    """The fresh START candidate for this artifact (never a closeout one)."""

    want = str(artifact_id).split(":")[-1]

    def matches(payload, _ref):
        refs = [str(value) for value in (payload.get("target_refs") or [])]
        return (any(want in value for value in refs)
                and not any(value.startswith("closeout:") for value in refs))

    return find_candidate(chain, clock, matches)


def find_kind_candidate(chain, clock: LogicalClock, kind: str) -> Optional[str]:
    return find_candidate(chain, clock, lambda payload, _ref:
                          str(payload.get("candidate_kind") or "") == kind)


def choose(chain, decision: str, candidate_id: Optional[str]) -> None:
    chain.cognition.script = {"decision": decision, "selected_candidate_id": candidate_id,
                              "deferred_candidate_ids": []}


def run_round(chain, clock: LogicalClock, tag: str) -> dict[str, Any]:
    return dict(chain.cycle.evaluate_once(
        trigger_ref="evt:waiting-no-model-poll:%s" % tag,
        observed_at=clock.iso(), act=True) or {})


class WriterControl:
    """The content double the cycle sees; ``no_change`` is the legal source of a WAIT basis."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.no_change = False

    def next_content(self, point: Any, *, observed_at: str,
                     context: Optional[Mapping[str, Any]] = None) -> str:
        if self.no_change:
            raise mcw.ModelNoChange("stub: nothing to add on this head")
        if context is not None:
            return self.inner.next_content(point, observed_at=observed_at, context=context)
        return self.inner.next_content(point, observed_at=observed_at)


def bind(chain, clock: LogicalClock, control: Optional[WriterControl] = None) -> WriterControl:
    chain.cycle._business_clock = clock.now
    inner = chain.cycle.writer._inner
    if control is None:
        control = WriterControl(inner)
    else:
        control.inner = inner
    chain.cycle.writer._inner = control
    return control


def make_runner(chain, clock: LogicalClock) -> Any:
    """The real runner, built over the fixture's cycle exactly as assembly builds it."""

    state_root = odl.life_state_root(Path(chain.root) / "instance")
    return alr.AutonomousLifeRunner(chain.cycle, state_root=state_root, env=chain.env,
                                    enabled=True, clock=clock.now)


def upkeep(chain, runner, clock: LogicalClock) -> dict[str, Any]:
    return dict(runner.maintain(now=clock.now()) or {})


def quiet_upkeep(report: Mapping[str, Any], *, expect_checked: int = 1) -> bool:
    """An upkeep that really looked at the owner, saw the same head and spent nothing."""

    checked = int(report.get("checked") or 0)
    return (str(report.get("state") or "") == "OK"
            and int(report.get("llm_calls") or 0) == 0
            and report.get("auto_resumed") is False
            and int(report.get("changed") or 0) == 0
            and int(report.get("evaluated") or 0) == 0
            and not list(report.get("satisfied") or [])
            and not list(report.get("emitted_eligibilities") or [])
            and not list(report.get("errors") or [])
            and checked >= expect_checked
            and int(report.get("unchanged") or 0) == checked)


def bump_work(chain, artifact_id: str, tag: str) -> dict[str, Any]:
    """A real second writer on the same owner head (CAS, not a fabricated revision)."""

    current = chain.port.resume_point(str(artifact_id))
    result = chain.port.commit_version(
        artifact_id=str(artifact_id),
        expected_head=str(current.head_version_id or ""),
        content="%s\n\n【外部写者 %s】\n" % (current.content or "", tag),
        grant_id=str(chain.data["artifact_grant_id"]),
        operation_key=lap.operation_key_for("waiting-no-model-poll", tag, "external"))
    return result.to_dict()


# ---------------------------------------------------------------------------------------------
# one chain
# ---------------------------------------------------------------------------------------------


def main() -> int:
    import os as _os  # local alias so the printed pid is unambiguous

    clock = LogicalClock(TS0)
    chain = fixture.build_chain("wnmp")
    control = bind(chain, clock)
    runner = make_runner(chain, clock)
    summary: dict[str, Any] = {"root": str(chain.root), "pid": _os.getpid(),
                               "fixture_parent": _FIXTURE_PARENT, "upkeep": []}

    work = str(chain.data["artifact_id"])
    grant = str(chain.data["artifact_grant_id"])
    workspace = str(chain.data["workspace_id"])
    summary["work"] = work

    # an unrelated second work that must not change anywhere in this run
    work_b = chain.port.create_artifact(
        workspace_id=workspace, title="无关作品", content="与本次等待无关。\n", grant_id=grant,
        operation_key=lap.operation_key_for("waiting-no-model-poll", "create", "b"))
    check("the owner created the unrelated work B", work_b.applied and bool(work_b.artifact_id),
          {"applied": work_b.applied})
    work_b_id = str(work_b.artifact_id)
    b_head_before, b_versions_before = head(chain, work_b_id), version_numbers(chain, work_b_id)

    say("\n== 1. a real START, a real no_change, then a formal WAIT on the same Activity ==")
    choose(chain, "START", find_work_candidate(chain, clock, work))
    started = run_round(chain, clock, "start")
    activity_a = str(started.get("activity_ref") or "")
    check("START adopted work A and committed a real version",
          started.get("outcome") == "ACTED" and started.get("decision") == "START"
          and bool((started.get("step_receipt") or {}).get("version_id")),
          {"outcome": started.get("outcome"), "decision": started.get("decision")})
    summary["activity"] = activity_a
    versions_after_start = version_numbers(chain, work)
    summary["versions_after_start"] = versions_after_start

    control.no_change = True
    choose(chain, "CONTINUE", find_current_candidate(chain, clock, work))
    no_change = run_round(chain, clock, "no-change")
    check("the writer's no_change is a legal no-op, and it is the WAIT basis",
          no_change.get("outcome") == "NO_ACTION"
          and (no_change.get("work_no_change") or {}).get("version_created") is False,
          {"outcome": no_change.get("outcome")})
    control.no_change = False

    baseline_head = head(chain, work)
    choose(chain, "WAIT", find_current_candidate(chain, clock, work))
    waited = run_round(chain, clock, "wait")
    record_a = activity(chain, activity_a)
    condition = wait_condition(chain)
    check("WAIT bound the owner-head dependency on the same Activity and left it WAITING",
          waited.get("decision") == "WAIT" and waited.get("adoption_status") == "APPLIED"
          and record_a.get("status") == ac.STATUS_WAITING
          and bool(record_a.get("waiting_on_ref"))
          and condition.get("status") == "PENDING",
          {"decision": waited.get("decision"), "status": record_a.get("status"),
           "condition_status": condition.get("status")})
    check("the condition's baseline is the owner's own head, and it carries no deadline",
          str((condition.get("parameters") or {}).get("baseline_head_ref") or "")
          == baseline_head
          and condition.get("expires_at") is None,
          {"baseline_matches": str((condition.get("parameters") or {})
                                    .get("baseline_head_ref") or "") == baseline_head,
           "expires_at": condition.get("expires_at")})
    condition_id = str(condition.get("condition_id") or "")
    versions_at_wait = version_numbers(chain, work)
    actions_at_wait = action_count(chain)
    summary.update({"condition": condition_id, "baseline_head": baseline_head,
                    "versions_at_wait": len(versions_at_wait), "actions_at_wait": actions_at_wait})

    # ---------------------------------------------------------------- 2. long logical upkeep
    say("\n== 2. upkeep across +1h / +6h / +24h of injected logical time, same head ==")
    for label, iso in (("plus-1h", plus(TS0, hours=1)),
                       ("plus-6h", plus(TS0, hours=6)),
                       ("plus-24h", plus(TS0, hours=24))):
        clock.at(iso)
        budget_before = budget_counts(chain)
        cognition_before = int(chain.cognition.call_count)
        content_before = len(chain.client.prompts)
        report = upkeep(chain, runner, clock)
        row = {
            "at": label, "checked": report.get("checked"),
            "unchanged": report.get("unchanged"), "changed": report.get("changed"),
            "llm_calls": report.get("llm_calls"),
            "emitted": len(list(report.get("emitted_eligibilities") or [])),
            "errors": len(list(report.get("errors") or [])),
            "budget_flat": budget_counts(chain) == budget_before,
            "cognition_delta": int(chain.cognition.call_count) - cognition_before,
            "content_delta": len(chain.client.prompts) - content_before,
        }
        summary["upkeep"].append(row)
        check("upkeep %s: re-read the owner, same head, zero model, zero effect"
              % label,
              quiet_upkeep(report) and row["budget_flat"] and row["cognition_delta"] == 0
              and row["content_delta"] == 0,
              row)
        check("upkeep %s: the Activity is still WAITING, the condition still PENDING, "
              "no version and no Action" % label,
              activity(chain, activity_a).get("status") == ac.STATUS_WAITING
              and wait_condition(chain).get("status") == "PENDING"
              and version_numbers(chain, work) == versions_at_wait
              and action_count(chain) == actions_at_wait
              and not open_eligibilities(chain),
              {"status": activity(chain, activity_a).get("status"),
               "condition": wait_condition(chain).get("status"),
               "open": len(open_eligibilities(chain))})

    # ------------------------------------------------- 3. orderly re-assembly of the same root
    say("\n== 3. orderly stop/drain + re-assembly of the same root (same PID) ==")
    waiting_before = activity(chain, activity_a)
    condition_before = dict(wait_condition(chain))
    report = restart.close_chain(chain)
    check("the owner stopped with drain and its own socket guard",
          report.get("closed") is True and report.get("service_stopped") is True
          and report.get("service_drained") is True
          and str(report.get("service_socket_handling") or "") in {
              "STALE_SOCKET_REMOVED", "NO_SOCKET", "ABSENT", "CLEAN"},
          report)

    clock.at(plus(TS0, hours=24))
    chain = restart.open_chain(Path(summary["root"]), "wnmp-p2")
    control = bind(chain, clock, control)
    runner = make_runner(chain, clock)
    waiting_after = activity(chain, activity_a)
    check("the WAITING Activity, stop point and revision survived the re-assembly",
          waiting_after.get("status") == ac.STATUS_WAITING
          and waiting_after.get("revision") == waiting_before.get("revision")
          and waiting_after.get("waiting_on_ref") == waiting_before.get("waiting_on_ref")
          and waiting_after.get("checkpoint_ref") == waiting_before.get("checkpoint_ref"),
          {"status": waiting_after.get("status"),
           "revision_same": waiting_after.get("revision") == waiting_before.get("revision")})
    check("the PENDING condition survived with the same id, baseline and no deadline",
          dict(wait_condition(chain)) == condition_before
          and wait_condition(chain).get("expires_at") is None
          and not open_eligibilities(chain),
          {"same_id": wait_condition(chain).get("condition_id") == condition_id,
           "status": wait_condition(chain).get("status"),
           "open": len(open_eligibilities(chain))})
    say("NOTE same PID %d: this is an in-process re-assembly, not a cross-process restart."
        % summary["pid"])

    clock.at(plus(TS0, hours=25))
    same_head_again = upkeep(chain, runner, clock)
    check("upkeep after the re-assembly still sees the same head and emits nothing",
          quiet_upkeep(same_head_again) and not open_eligibilities(chain)
          and activity(chain, activity_a).get("status") == ac.STATUS_WAITING,
          {"checked": same_head_again.get("checked"),
           "unchanged": same_head_again.get("unchanged"),
           "llm_calls": same_head_again.get("llm_calls"),
           "open": len(open_eligibilities(chain))})

    # ------------------------------------------------------ 4. a real head change opens once
    say("\n== 4. only a real owner head change emits exactly one OPEN eligibility ==")
    external = bump_work(chain, work, "wait-head")
    new_head = head(chain, work)
    check("the owner really advanced the head while the Activity waited",
          external.get("effect_state") == "APPLIED" and new_head != baseline_head,
          {"effect_state": external.get("effect_state")})
    versions_before_open = version_numbers(chain, work)
    actions_before_open = action_count(chain)
    opened = upkeep(chain, runner, clock)
    open_rows = open_eligibilities(chain)
    summary["open_after_head_change"] = len(open_rows)
    check("one upkeep emitted exactly one OPEN eligibility, zero model, no auto-resume",
          int(opened.get("llm_calls") or 0) == 0 and opened.get("auto_resumed") is False
          and len(open_rows) == 1
          and str(open_rows[0].get("activity_id")) == activity_a,
          {"llm_calls": opened.get("llm_calls"), "open": len(open_rows),
           "emitted": len(list(opened.get("emitted_eligibilities") or []))})
    check("the Activity is still WAITING and nothing was written by the upkeep",
          activity(chain, activity_a).get("status") == ac.STATUS_WAITING
          and version_numbers(chain, work) == versions_before_open
          and action_count(chain) == actions_before_open,
          {"status": activity(chain, activity_a).get("status")})

    for label, iso in (("plus-1h", plus(TS0, hours=26)), ("plus-2h", plus(TS0, hours=27))):
        clock.at(iso)
        again = upkeep(chain, runner, clock)
        check("repeated upkeep %s does not duplicate the eligibility and spends nothing"
              % label,
              int(again.get("llm_calls") or 0) == 0
              and len(open_eligibilities(chain)) == 1
              and version_numbers(chain, work) == versions_before_open,
              {"llm_calls": again.get("llm_calls"),
               "open": len(open_eligibilities(chain)),
               "emitted": len(list(again.get("emitted_eligibilities") or []))})

    # ------------------------------------- 5. NO_ACTION keeps waiting; normal cognition is paid
    say("\n== 5. NO_ACTION while OPEN: waiting kept, and that round is a paid cognition ==")
    clock.at(plus(TS0, hours=28))
    choose(chain, "NO_ACTION", None)
    cognition_before = int(chain.cognition.call_count)
    quiet = run_round(chain, clock, "no-action-while-open")
    round_llm_calls = int(quiet.get("llm_calls") or 0)
    summary["no_action_round"] = {"outcome": quiet.get("outcome"),
                                  "llm_calls": round_llm_calls,
                                  "cognition_delta": int(chain.cognition.call_count)
                                  - cognition_before}
    check("the NO_ACTION round is a normal paid cognition round (not a zero-cost wait poll)",
          quiet.get("decision") == "NO_ACTION" and round_llm_calls == 1
          and int(chain.cognition.call_count) - cognition_before == 1,
          summary["no_action_round"])
    check("NO_ACTION kept the Activity WAITING and the eligibility OPEN",
          activity(chain, activity_a).get("status") == ac.STATUS_WAITING
          and len(open_eligibilities(chain)) == 1,
          {"status": activity(chain, activity_a).get("status"),
           "open": len(open_eligibilities(chain))})

    released = chain.cycle.reconsideration.release(refs=[activity_a, work])
    check("an explicit fresh consideration only lifts the suppression, not the Activity",
          isinstance(released, list) and activity(chain, activity_a).get("status")
          == ac.STATUS_WAITING and len(open_eligibilities(chain)) == 1,
          {"released": len(released), "open": len(open_eligibilities(chain))})

    # ------------------------------------------------------ 6. a fresh RESUME on the new head
    say("\n== 6. a fresh Decision RESUME returns to the same Activity and consumes once ==")
    clock.at(plus(TS0, hours=29))
    resume_candidate = find_kind_candidate(chain, clock, "RESUME_ACTIVITY")
    choose(chain, "RESUME", resume_candidate)
    resumed = run_round(chain, clock, "resume")
    record_a = activity(chain, activity_a)
    consumption = sorted({str(row.get("status") or "") for row in eligibilities(chain).values()})
    summary["resume"] = {"decision": resumed.get("decision"),
                         "outcome": resumed.get("outcome"),
                         "same_activity": str(resumed.get("activity_ref") or "") == activity_a,
                         "activity_status": record_a.get("status"),
                         "eligibility_statuses": consumption,
                         "llm_calls": int(resumed.get("llm_calls") or 0)}
    check("RESUME returned to the same Activity id, not a new START",
          resumed.get("decision") == "RESUME" and resumed.get("adoption_status") == "APPLIED"
          and str(resumed.get("activity_ref") or "") == activity_a
          and record_a.get("status") == ac.STATUS_ACTIVE,
          summary["resume"])
    check("the resume consumed the eligibility and wrote on the fresh owner head",
          "CONSUMED" in consumption
          and (resumed.get("step_receipt") or {}).get("expected_head") == new_head,
          {"consumption": consumption,
           "expected_head_matches": (resumed.get("step_receipt") or {}).get("expected_head")
           == new_head})
    versions_after_resume = version_numbers(chain, work)
    check("the resume wrote a strictly newer version and replayed nothing",
          len(versions_after_resume) == len(set(versions_after_resume))
          and versions_after_resume[-1] > versions_at_wait[-1],
          {"versions": len(versions_after_resume)})

    # ------------------------------------------------------------------ 7. B really untouched
    say("\n== 7. the unrelated work B was never touched ==")
    check("work B's head and versions are exactly what they were",
          head(chain, work_b_id) == b_head_before
          and version_numbers(chain, work_b_id) == b_versions_before,
          {"head_same": head(chain, work_b_id) == b_head_before,
           "versions": len(version_numbers(chain, work_b_id))})

    summary.update({
        "ok": not FAILURES,
        "maintenance_calls": len(summary["upkeep"]) + 4,
        "maintenance_llm_calls": sum(int(row.get("llm_calls") or 0) for row in summary["upkeep"]),
        "decision_round_llm_calls": round_llm_calls + int(resumed.get("llm_calls") or 0),
        "b_untouched": head(chain, work_b_id) == b_head_before,
        "same_pid_reassembly": True,
        "versions_final": len(versions_after_resume),
        "note": "logical clock is injected by argument; this is not a real day or a cross-process restart",
    })

    try:
        restart.close_chain(chain)
    except Exception as exc:  # printed, not hidden
        say("  close error: %s: %s" % (type(exc).__name__, str(exc)[:160]))

    say("\n" + json.dumps(summary, ensure_ascii=False, default=str))
    if FAILURES:
        say("FAILED %d check(s): %s" % (len(FAILURES), ", ".join(FAILURES)))
        return 1
    say("WAITING_NO_MODEL_POLL_DEMO_OK")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as exc:  # a failed setup is a failed demo, never silent
        say("DEMO_FAIL: %s: %s" % (type(exc).__name__, str(exc)[:300]))
        raise SystemExit(1)

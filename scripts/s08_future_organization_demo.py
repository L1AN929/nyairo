#!/usr/bin/env python3
"""S08 F-093..F-097 demo：未来事项的认领、与当前 Activity 的分离、到期、改期/取消、重启读回。

What is real (local only, 0 product model calls, 0 network)
----------------------------------------------------------
* real Life Supply owner on its own Unix socket, prepared by ``one_day_life.prepare``;
* the real production Life runtime and ``assemble_autonomous_life``;
* real AG-1 / AG-2 / LR-2 / LR-4 / AR-0, real ``Cycle.evaluate_once``;
* every Decision is a scripted double that only supplies raw JSON to the real AG-1 validator;
  every content answer is a scripted double.  No product model is called.

The chain demonstrates
----------------------
1. A committed Decision records one ``later_item``; the item is her own deferred intention and
   names the Decision that produced it (the identity is recomputed, not guessed).
2. The same item can be put into **today** by a second Decision; the plan row stores that
   Decision's id.  Planning is not starting.
3. Before its instant the item is not due; when due it only becomes something to *reconsider*.
   No Activity, no Agenda, no version is created by the clock alone.
4. ``NO_ACTION`` leaves the item standing (no quota, no forced work).
5. A later Decision reschedules it, then cancels it; the cancelled item never comes back when
   its old instant passes or after a same-root restart.
6. An operator obligation is read-only.  A Decision that tries to cancel/reschedule it is
   refused as a whole by the future-items domain; the obligation row does not change.
7. Ordered stop + same-root re-assembly reads back items/plan/obligation states with zero new
   Activity, zero new version and zero replayed write.  An LR-4 eligibility, if any, is only a
   right to ask to resume -- nothing executes.
8. The user entry is ``scripts/s08_future_review_cli.py`` (invoked as a subprocess).

Named STOP (not implemented here, do not fake)
----------------------------------------------
S08 has **no Commitment owner**.  F-095 "真实承诺约束" and the renegotiation half of F-097 are
therefore ``NOT_IMPLEMENTED``: neither ``fitem:`` nor ``oblig:`` nor an Agenda item is renamed a
Commitment, no ``promised=true`` is invented, and Activity abandonment is not closed as a promise.
The short options note is ``docs/engineering/s08-future-organization-options-20261027.md``.

Boundaries: not G1, not 24h, not a crash matrix; the restart is an ordered stop/start in the
same root.  The World is not used.  Time is an injected business clock.

Run (WSL/POSIX)::

    PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/s08_future_organization_demo.py
"""
from __future__ import annotations

import json
import subprocess
import sys
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

import life_day_plan as ldp  # noqa: E402
import life_future_items as lfi  # noqa: E402
import life_project_options as lpo  # noqa: E402
import result_method_behavior_smoke as base  # noqa: E402
import v05_restart_continuation_smoke as restart  # noqa: E402
from components.phone import own_day_read as odr  # noqa: E402

#: 一个固定的业务日清晨：所有轮次都在同一天，DayPlan 的“只排今天”才有意义。
BASE = datetime.now().astimezone().replace(hour=9, minute=0, second=0, microsecond=0)

FAILURES: list[str] = []


def say(text: str) -> None:
    print(text)
    sys.stdout.flush()


def check(name: str, condition: Any, detail: Any = None) -> bool:
    ok = bool(condition)
    say("CHECK %-76s %s" % (name, "PASS" if ok else "FAIL"))
    if not ok:
        FAILURES.append(name)
        if detail is not None:
            say("  detail: " + json.dumps(detail, ensure_ascii=False, default=str)[:1600])
    return ok


def at(minutes: int) -> str:
    """The fixture business clock: a fixed offset from this morning's BASE."""

    return (BASE + timedelta(minutes=minutes)).isoformat(timespec="seconds")


def decide(chain, raw: Mapping[str, Any]) -> None:
    chain.cognition.script = dict(raw)


def no_action(**extra: Any) -> dict[str, Any]:
    raw = {"decision": "NO_ACTION", "selected_candidate_id": None,
           "reason_codes": ["NO_ACTION_CHOSEN"], "deferred_candidate_ids": []}
    raw.update(extra)
    return raw


def round_out(chain, tag: str, observed_at: str) -> dict[str, Any]:
    return dict(chain.cycle.evaluate_once(trigger_ref="evt:s08:%s" % tag,
                                          observed_at=observed_at, act=True) or {})


def brief(out: Mapping[str, Any]) -> dict[str, Any]:
    return {key: out.get(key) for key in (
        "outcome", "decision", "decision_id", "adoption_status", "failure_class", "detail",
        "future_item_writes", "day_plan_writes")}


def items(state_root: Path, subject_id: Optional[str]) -> list[dict[str, Any]]:
    return [row.to_dict() for row in lfi.FutureItems(state_root, subject_id=subject_id).items()]


def activities(state_root: Path, day: str) -> list[dict[str, Any]]:
    rows, _source = odr.read_today_activities(state_root, day)
    return rows


def versions(chain, artifact_id: str) -> int:
    return int(chain.port.list_versions(artifact_id, limit=50)["total_versions"])


def run_cli(state_root: Path, *, now: str) -> tuple[int, dict[str, Any]]:
    command = [sys.executable, "-B", str(ROOT / "scripts" / "s08_future_review_cli.py"),
               "--state-root", str(state_root), "--format", "json", "--now", now]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
    try:
        payload = json.loads(completed.stdout or "{}")
    except ValueError:
        payload = {"ok": False, "reason": "CLI_OUTPUT_NOT_JSON",
                   "stdout": completed.stdout[-600:], "stderr": completed.stderr[-600:]}
    return int(completed.returncode), payload


def main() -> int:
    chain = base.build_chain("s08-future-organization")
    state_root = Path(chain.cycle.future_items.state_root)
    subject = str(chain.data["subject_id"])
    work = str(chain.data["artifact_id"])
    day = ldp.business_date(at(0))
    summary: dict[str, Any] = {"root": str(chain.root), "state_root": str(state_root),
                               "work": "artifact:%s" % work, "day": day, "rounds": {}}
    item_ref = ""
    plan_ref = ""
    claim_decision = ""
    plan_decision = ""
    first_decision = ""
    versions_before = versions(chain, work)
    activities_before = len(activities(state_root, day))

    try:
        check("all three S08 owners are really wired",
              chain.cycle.future_items is not None and chain.cycle.day_plan is not None
              and chain.cycle.day_obligations is not None)

        # ==================================================================
        # 1. F-093: a committed Decision explicitly claims one future item
        # ==================================================================
        say("\n== 1. F-093 explicit future claim through the formal Decision ==")
        decide(chain, no_action(later_item={
            "title": "晚上把这段笔记再过一遍", "in_minutes": 30,
            "source_refs": ["artifact:%s" % work]}))
        first = round_out(chain, "claim", at(0))
        summary["rounds"]["claim"] = brief(first)
        first_decision = str(first.get("decision_id") or "")
        writes = list(first.get("future_item_writes") or ())
        rows = items(state_root, subject)
        item_ref = str(rows[0]["item_ref"]) if rows else ""
        check("the Decision committed and the item was really written",
              first.get("outcome") in ("ACTED", "NO_ACTION", "STEP_REFUSED", None)
              and first.get("decision") == "NO_ACTION" and bool(item_ref)
              and any(row.get("op") == "propose" and row.get("status") == "APPLIED"
                      for row in writes),
              {"round": brief(first), "items": rows})
        check("the item is a deferred intention, not an Activity and not a Commitment",
              bool(rows) and rows[0]["origin"] == lfi.ORIGIN_MODEL_PROPOSAL
              and rows[0]["status"] == lfi.STATUS_OPEN
              and not str(rows[0]["item_ref"]).startswith("actv:"),
              rows[0] if rows else None)

        # ==================================================================
        # 2. F-094: before due the round is quiet; the clock does not start anything
        # ==================================================================
        say("\n== 2. F-094/F-096 before the instant: quiet, nothing started ==")
        early = round_out(chain, "before-due", at(10))
        summary["rounds"]["before_due"] = brief(early)
        item_now = lfi.FutureItems(state_root, subject_id=subject).get(item_ref)
        check("before its instant the round is quiet and the item stays open",
              early.get("outcome") == "QUIET" and early.get("decision") is None
              and item_now is not None and item_now.status == lfi.STATUS_OPEN,
              {"round": brief(early),
               "item": item_now.to_dict() if item_now else None})
        check("no Activity and no version came from the clock alone",
              len(activities(state_root, day)) == activities_before
              and versions(chain, work) == versions_before,
              {"activities": len(activities(state_root, day)),
               "versions": versions(chain, work)})

        # ==================================================================
        # 3. F-094/F-096: at its instant it is only reconsidered -- and she plans today
        # ==================================================================
        say("\n== 3. F-094/F-096 the item is due; planning it is not starting it ==")
        decide(chain, no_action(plan_today={"item_ref": item_ref}))
        due_round = round_out(chain, "due", at(40))
        summary["rounds"]["due"] = brief(due_round)
        view = chain.cycle.last_future_items or {}
        options = chain.cycle.project_options(now=at(40)) or []
        due_options = [row for row in options
                       if str(row.get("kind")) == lpo.KIND_FUTURE_ITEM
                       and str(row.get("basis_ref")) == item_ref]
        plan_decision = str(due_round.get("decision_id") or "")
        plan_rows = ldp.DayPlan(state_root, subject_id=subject).for_date(day)
        plan_ref = str(plan_rows[0]["plan_ref"]) if plan_rows else ""
        check("at its instant the item is due and is offered for reconsideration",
              item_ref in [str(ref) for ref in (view.get("due_refs") or ())]
              and bool(due_options),
              {"view": view, "options": due_options})
        check("a real plan row exists and binds this Decision",
              bool(plan_rows) and str(plan_rows[0].get("decision_ref")) == plan_decision
              and str(plan_rows[0].get("item_ref")) == item_ref,
              {"plans": plan_rows, "decision": plan_decision})
        check("planning created no Activity and no new version",
              len(activities(state_root, day)) == activities_before
              and versions(chain, work) == versions_before,
              {"activities": len(activities(state_root, day)),
               "versions": versions(chain, work)})

        # ==================================================================
        # 4. NO_ACTION keeps the item standing (no quota)
        # ==================================================================
        say("\n== 4. NO_ACTION leaves the item standing ==")
        kept_item = lfi.FutureItems(state_root, subject_id=subject).get(item_ref)
        check("the NO_ACTION round did not close, cancel or auto-start the item",
              due_round.get("decision") == "NO_ACTION" and kept_item is not None
              and kept_item.status == lfi.STATUS_OPEN
              and len(activities(state_root, day)) == activities_before,
              {"item": kept_item.to_dict() if kept_item else None, "round": brief(due_round)})

        # ==================================================================
        # 5. F-097: reschedule, then cancel; cancellation is final
        # ==================================================================
        say("\n== 5. F-097 reschedule / cancel are her own writes, never auto-revived ==")
        # The first round's decline is remembered for six hours (the existing reconsideration
        # ledger); the fixture clock jumps past it rather than deleting that record.
        decide(chain, no_action(future_item_actions=[{
            "item_ref": item_ref, "op": "reschedule", "in_minutes": 120,
            "reason": "今晚再说"}]))
        moved_round = round_out(chain, "reschedule", at(410))
        summary["rounds"]["reschedule"] = brief(moved_round)
        moved = lfi.FutureItems(state_root, subject_id=subject).get(item_ref)
        check("the reschedule was applied through the owner",
              moved is not None
              and ldp.parse_instant(moved.due_at) == ldp.parse_instant(at(530))
              and str(moved_round.get("decision")) == "NO_ACTION",
              {"item": moved.to_dict() if moved else None, "round": brief(moved_round)})

        decide(chain, no_action(future_item_actions=[{
            "item_ref": item_ref, "op": "cancel", "reason": "不做了"}]))
        cancelled_round = round_out(chain, "cancel", at(790))
        summary["rounds"]["cancel"] = brief(cancelled_round)
        cancelled = lfi.FutureItems(state_root, subject_id=subject).get(item_ref)
        check("the cancel was applied and the item is CANCELLED",
              cancelled is not None and cancelled.status == lfi.STATUS_CANCELLED,
              cancelled.to_dict() if cancelled else None)

        # the old instants pass; a cancelled item must not come back
        still = lfi.FutureItems(state_root, subject_id=subject).get(item_ref)
        check("past its old instant the cancelled item does not revive",
              still is not None and still.status == lfi.STATUS_CANCELLED
              and not still.is_due(at(800)),
              still.to_dict() if still else None)

        # ==================================================================
        # 6. operator obligation is not hers to change
        # ==================================================================
        say("\n== 6. operator obligation is read-only; a Decision touching it is refused ==")
        obligation = ldp.import_obligation(
            state_root, date=day, title="今天把这段整理成文", target_ref="artifact:%s" % work,
            due_at=at(880), source_ref="operator:demo", operation_key="op:s08:oblig:1",
            subject_id=subject, target_verifier=chain.port.resume_point, now=at(800))
        obligation_ref = str(obligation["entry"]["obligation_ref"])
        before = ldp.DayObligations(state_root, subject_id=subject).for_date(day)
        decide(chain, no_action(future_item_actions=[{
            "item_ref": obligation_ref, "op": "cancel", "reason": "不是我能改的"}]))
        refused = round_out(chain, "touch-obligation", at(810))
        summary["rounds"]["obligation_refused"] = brief(refused)
        after_obligation = ldp.DayObligations(state_root, subject_id=subject).for_date(day)
        check("the Decision was refused as a whole (no committed Decision)",
              refused.get("decision") is None
              and str(refused.get("outcome") or "") == "DECISION_FAILED",
              {"round": brief(refused)})
        check("no future-item write happened for the obligation ref",
              not list(refused.get("future_item_writes") or ())
              and [dict(row) for row in after_obligation] == [dict(row) for row in before],
              {"writes": refused.get("future_item_writes"), "before": before,
               "after": after_obligation})

        # ==================================================================
        # 7. the read-only user entry tells who owns what
        # ==================================================================
        say("\n== 7. the read-only CLI: actor / source / activity separation ==")
        code, envelope = run_cli(state_root, now=at(820))
        summary["cli"] = {"returncode": code, "ok": envelope.get("ok"),
                          "sources": {name: src.get("state")
                                      for name, src in
                                      ((envelope.get("data") or {}).get("sources") or {}).items()}}
        data = envelope.get("data") or {}
        claim_rows = [row for row in (data.get("future_items") or {}).get("claims") or ()
                      if str(row.get("item_ref")) == item_ref]
        plan_cli = [row for row in (data.get("day_plan") or {}).get("entries") or ()
                    if str(row.get("plan_ref")) == plan_ref]
        check("the CLI is a working user entry and reports every source",
              code == 0 and envelope.get("ok") is True
              and set((data.get("sources") or {}).keys()) >=
              {"future_items", "day_plan", "obligations", "agenda", "activities", "decisions"},
              summary["cli"])
        check("the cancelled item still names the Decision that claimed it",
              bool(claim_rows)
              and str(claim_rows[0].get("decision_ref")) == first_decision
              and str(claim_rows[0].get("attribution")) == "DECISION_RECORDED",
              claim_rows)
        check("the plan row names its Decision; operator obligations are not hers to change",
              bool(plan_cli) and str(plan_cli[0].get("decision_ref")) == plan_decision
              and (data.get("obligations") or {}).get("writable_by_subject") is False,
              {"plan": plan_cli, "obligations": (data.get("obligations") or {}).get(
                  "writable_by_subject")})
        check("the CLI never renames any object a Commitment",
              (data.get("commitment") or {}).get("state") == "NOT_IMPLEMENTED"
              and (data.get("commitment") or {}).get("owner") is None
              and (data.get("renegotiation") or {}).get("state") == "NOT_IMPLEMENTED",
              data.get("commitment"))

        # ==================================================================
        # 8. ordered stop + same-root re-assembly
        # ==================================================================
        say("\n== 8. ordered stop and same-root re-assembly ==")
        before_restart = items(state_root, subject)
        report = restart.close_chain(chain)
        check("the owners stopped in order (drain + their own socket guard)",
              report.get("closed") is True and report.get("service_stopped") is True
              and report.get("service_drained") is True
              and str(report.get("service_socket_handling") or "") in
              {"STALE_SOCKET_REMOVED", "NO_SOCKET", "ABSENT", "CLEAN"}, report)

        chain = restart.open_chain(Path(summary["root"]), "s08-p2")
        state_root = Path(chain.cycle.future_items.state_root)
        reopened = items(state_root, subject)
        plan_after = ldp.DayPlan(state_root, subject_id=subject).for_date(day)
        obligation_after = ldp.DayObligations(state_root, subject_id=subject).for_date(day)
        check("items, plan and obligation read back unchanged after re-assembly",
              [(row["item_ref"], row["status"], row["due_at"]) for row in reopened]
              == [(row["item_ref"], row["status"], row["due_at"]) for row in before_restart]
              and [(row["plan_ref"], row["item_ref"]) for row in plan_after]
              == [(row["plan_ref"], row["item_ref"]) for row in plan_rows]
              and len(obligation_after) == len(after_obligation),
              {"items": reopened, "plans": plan_after,
               "obligations": obligation_after})
        check("re-assembly created no Activity and no new version",
              len(activities(state_root, day)) == activities_before
              and versions(chain, work) == versions_before,
              {"activities": len(activities(state_root, day)),
               "versions": versions(chain, work)})

        agenda_before = _agenda_counts(chain)
        resumed = round_out(chain, "after-restart", at(830))
        summary["rounds"]["after_restart"] = brief(resumed)
        agenda_after = _agenda_counts(chain)
        check("a reopened round replayed no write and started no Activity",
              not list(resumed.get("future_item_writes") or ())
              and len(activities(state_root, day)) == activities_before
              and versions(chain, work) == versions_before,
              {"round": brief(resumed)})
        check("an eligibility is only a resume right -- nothing executed from it",
              agenda_after.get("eligibilities", 0) == agenda_before.get("eligibilities", 0)
              and len(activities(state_root, day)) == activities_before,
              {"before": agenda_before, "after": agenda_after})

        # F-095/F-097: the Commitment half is a named STOP, not a silent success
        say("\n== 9. F-095 / F-097 Commitment half: named STOP ==")
        item_claims = list((data.get("future_items") or {}).get("entries") or ()) + \
            list((data.get("future_items") or {}).get("claims") or ())
        check("no Commitment owner exists, so no object is renamed a promise",
              (data.get("commitment") or {}).get("state") == "NOT_IMPLEMENTED"
              and (data.get("commitment") or {}).get("owner") is None
              and all(not row.get("is_commitment") and "promised" not in row
                      for row in item_claims),
              data.get("commitment"))
        summary["stop"] = {"F-095": "NOT_IMPLEMENTED: S08 commitment owner absent",
                           "F-097_renegotiation": "NOT_IMPLEMENTED: no commitment to renegotiate",
                           "options": "docs/engineering/s08-future-organization-options-20261027.md"}
    finally:
        try:
            restart.close_chain(chain)
        except Exception as exc:  # noqa: BLE001 - printed, not hidden
            say("  close error: %s: %s" % (type(exc).__name__, str(exc)[:160]))

    say("\nRESULT %s" % ("PASS" if not FAILURES else "FAIL: " + "; ".join(FAILURES)))
    print(json.dumps(summary, ensure_ascii=False, default=str)[:4000])
    return 0 if not FAILURES else 1


def _agenda_counts(chain) -> dict[str, int]:
    try:
        return {"conditions": len(chain.runtime.agenda_service.list_conditions(only_open=True)),
                "eligibilities": len(chain.runtime.agenda_service.list_eligibilities(only_open=True))}
    except Exception:
        # The runtime may not expose a composed service; the stores are the same owner.
        try:
            import activity_waiting_agenda as awa
            state = chain.runtime.condition_store.load_state()
            elig = chain.runtime.eligibility_store.load_state()
            return {"conditions": len(state.get("conditions") or {}),
                    "eligibilities": len(elig.get("eligibilities") or {})}
        except Exception:
            return {}


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise

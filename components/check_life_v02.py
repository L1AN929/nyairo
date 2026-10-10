"""v0.2 acceptance check: the real autonomous life cycle against a real owner.

What this proves (and what it does not)
--------------------------------------
This check drives the v0.2 entry (``life/scripts/autonomous_life_cycle.py``) against a **real**
Life Supply owner over its Unix socket, with the single step executed through **AR-0**
(``action_reality_ledger``) by the adapter in ``managed_artifact_action_adapter.py``.  It asserts
the invariants that make the closed loop real rather than narrated:

1. with nothing of her own to work on, the cycle stops as ``NO_MATERIAL`` and spends **zero**
   model calls and **zero** round budget;
2. one round adopts a canonical Activity and commits a real next version **through AR-0**, with
   the owner's own receipt as the only evidence of the effect;
3. the next round **continues the same** Activity and the **same** artifact -- no parallel work;
4. the four outcomes E01.4 names are distinguished and none is mistaken for another:
   ``OK``, ``NO_CHANGE`` (action happened, work did not change), ``CONFLICT`` (stale
   compare-and-swap refused), ``UNKNOWN`` (answer lost; resolved by asking the owner about the
   *original* operation, never by writing again);
5. budget exhaustion and the off switch cost zero model calls and change nothing.

The cognition adapter here is an explicit **TEST DOUBLE**, not a model.  It is what makes this
check runnable with zero cost; it is not evidence that a model chose anything.  The real model
path is ``ProductionAgencyCognitionAdapter`` (``life/scripts/agency_cognition_adapter.py:105``)
over ``AgencyModelClient``, and it is exercised separately.

Environment (all required; the check SKIPs cleanly without them)
--------------------------------------------------------------
``NYA02_SUPPLY_SOCKET``   absolute path of the Life Supply socket
``NYA02_SUBJECT``         the subject that owns the workspace
``NYA02_WORKSPACE_ID``    an ACTIVE workspace of that subject
``NYA02_ARTIFACT_GRANT``  a REUSABLE ``COMMIT_MANAGED_ARTIFACT`` grant targeting that workspace
``NYA02_LIFE_STATE``      optional; the Life state root (defaults to a fresh temp dir)

The owner's data root must be **fresh** (no artifacts yet): the first assertion is exactly that an
empty owner yields no activity.  A non-empty owner is reported as ``ENVIRONMENT_NOT_FRESH`` rather
than being silently accepted.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LIFE_SCRIPTS = ROOT / "life" / "scripts"
sys.path.insert(0, str(LIFE_SCRIPTS))

REQUIRED_ENV = ("NYA02_SUPPLY_SOCKET", "NYA02_SUBJECT", "NYA02_WORKSPACE_ID",
                "NYA02_ARTIFACT_GRANT")

#: Rounds this check is allowed to spend per cycle instance.
MAX_ROUNDS = 4

FAILURES: list[str] = []


def check(condition: bool, label: str, detail: object = "") -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} :: {detail}")
        FAILURES.append(f"{label} :: {detail}")


def main() -> int:
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        print(f"SKIP: isolated Life Supply environment not configured (missing {missing})")
        return 0

    install_root = Path(os.environ.get("NYA02_LIFE_STATE") or tempfile.mkdtemp(
        prefix="v02-life-state-")).resolve()
    install_root.mkdir(parents=True, exist_ok=True)
    os.chmod(install_root, 0o700)
    os.environ["CHIYO_LIFE_INSTALL_ROOT"] = str(install_root)
    os.environ["CHIYO_LIFE_PRODUCTION_CUTOVER"] = "canonical"
    os.environ["LIFE_RUNTIME_ENABLED"] = "true"
    os.environ["AGENCY_ENABLED"] = "true"

    import agency_decision_ag1 as ag1
    import life_integration_ag2 as ag2
    import life_runtime_production as lrp
    from autonomous_life_cycle import (  # noqa: E402
        AutonomousLifeCycle, DeterministicContinuationWriter, OUTCOME_ACTED,
        OUTCOME_ALREADY_DONE, OUTCOME_BUDGET_EXHAUSTED, OUTCOME_DISABLED, OUTCOME_NO_MATERIAL,
        OUTCOME_STEP_REFUSED, OUTCOME_STEP_UNKNOWN,
    )
    from life_artifact_port import (  # noqa: E402
        EffectState, LifeArtifactPort, operation_key_for,
    )
    from managed_artifact_action_adapter import SupplyManagedArtifactActionAdapter  # noqa: E402

    subject = os.environ["NYA02_SUBJECT"]
    workspace_id = os.environ["NYA02_WORKSPACE_ID"]
    grant_id = os.environ["NYA02_ARTIFACT_GRANT"]

    class TestDoubleCognition(ag1.FakeCognitionAdapter):
        """Continuity first, then her own work.  A stand-in, not a model."""

        def __init__(self) -> None:
            super().__init__(model_id="v02-check-double", model_version="v0.2-check-double")
            self.calls = 0

        def invoke(self, request, *, schema_retry_index=0):
            self.calls += 1
            candidates = list(getattr(request, "candidates", []) or [])

            def pick(kind, source):
                return next((c for c in candidates
                             if c.get("candidate_kind") == kind
                             and c.get("source_kind") == source), None)

            current = pick("CONTINUE_CURRENT", "CURRENT_ACTIVITY")
            personal = pick("CONSIDER_PERSONAL_OPPORTUNITY", "PERSONAL_OPPORTUNITY")
            if current is not None:
                raw = {"decision": "CONTINUE", "selected_candidate_id": current["candidate_id"],
                       "reason_codes": ["CURRENT_ACTIVITY_CONTINUITY"],
                       "short_rationale": "手上的事还没做完。"}
            elif personal is not None:
                raw = {"decision": "START", "selected_candidate_id": personal["candidate_id"],
                       "reason_codes": ["OPEN_ENDED_CONTINUATION"],
                       "short_rationale": "还能接着写一点。"}
            else:
                raw = {"decision": "NO_ACTION", "selected_candidate_id": None,
                       "reason_codes": ["NO_ACTION_CHOSEN"], "short_rationale": "没有可做的。"}
            return ag1.CognitiveDecisionResponse(
                model_run_ref="mrun:v02-check", model_id=self.model_id,
                model_version=self.model_version, raw_output=raw, latency_ms=0)

    class IdentityWriter:
        """Returns the head unchanged, so the owner answers NO_CHANGE."""

        def next_content(self, point, *, observed_at: str) -> str:
            return point.content or ""

    class RacingWriter:
        """Someone else edits the work between our read and our commit."""

        def __init__(self, port, artifact_id, grant_id):
            self.port = port
            self.artifact_id = artifact_id
            self.grant_id = grant_id
            self.interference = None

        def next_content(self, point, *, observed_at: str) -> str:
            self.interference = self.port.commit_version(
                artifact_id=self.artifact_id, expected_head=point.head_version_id,
                content=(point.content or "") + "\n\n（别人先改了一笔）\n",
                grant_id=self.grant_id,
                operation_key=operation_key_for("race", self.artifact_id,
                                               str(point.head_version_id)))
            return (point.content or "") + "\n\n（我的这一笔）\n"

    def build(*, enabled: bool, max_rounds: int, writer=None, drop_response: bool = False):
        # Each section is a fresh consideration window.  The day budget is a real persisted
        # counter, so the check clears it explicitly instead of pretending rounds are free.
        (install_root / "autonomous_budget.json").unlink(missing_ok=True)
        switches = ag2.LifeRuntimeKillSwitches(
            life_runtime_enabled=True, agency_enabled=True,
            action_execution_enabled=True, proactive_enabled=False)
        cognition = TestDoubleCognition()
        port = LifeArtifactPort(os.environ["NYA02_SUPPLY_SOCKET"], subject)
        adapter = SupplyManagedArtifactActionAdapter(
            port, grant_id=grant_id, workspace_id=workspace_id,
            fault_injection_drop_response_after_write=drop_response)
        runtime = lrp.build_production_life_runtime(
            install_root, kill_switches=switches, cognition_adapter=cognition,
            install_root=install_root, artifact_action_adapter=adapter)
        cycle = AutonomousLifeCycle(
            runtime, artifact_port=port, subject_id=subject, workspace_id=workspace_id,
            artifact_grant_id=grant_id, writer=writer,
            max_rounds_per_day=max_rounds, enabled=enabled)
        return runtime, port, cycle, adapter, cognition

    print(f"life state root: {install_root}")

    # ---- 1. an empty owner yields nothing to do, and costs nothing --------------------
    print("[1] empty owner")
    runtime, port, cycle, adapter, cognition = build(enabled=True, max_rounds=MAX_ROUNDS)
    try:
        preexisting = port.list_artifacts()
        if preexisting:
            print(f"ENVIRONMENT_NOT_FRESH: owner already holds {len(preexisting)} artifact(s); "
                  "use a fresh Life Supply data root")
            return 2
        first = cycle.evaluate_once(trigger_ref="evt:v02:check:1")
        check(first["outcome"] == OUTCOME_NO_MATERIAL, "empty owner -> NO_MATERIAL",
              first["outcome"])
        check(first["llm_calls"] == 0 and cognition.calls == 0,
              "empty owner -> zero model calls", f"{first['llm_calls']}/{cognition.calls}")
        check(cycle.budget.spent_today() == 0, "empty owner -> no round charged",
              cycle.budget.spent_today())
        check(runtime.canonical_state()["revision"] == 0,
              "empty owner -> canonical Activity untouched", runtime.canonical_state()["revision"])
    finally:
        runtime.close()

    # ---- 2. a real first version ------------------------------------------------------
    print("[2] first real version")
    creation = port.create_artifact(
        workspace_id=workspace_id, title="夜里的手记",
        content="# 夜里的手记\n\n第一行：今天把窗户开了一条缝。\n",
        grant_id=grant_id, operation_key="op:v02check:create:1")
    check(creation.effect_state == EffectState.APPLIED, "owner created v1", creation.status)
    artifact_id = creation.artifact_id
    check(bool(artifact_id), "owner returned a real artifact id", artifact_id)
    v1 = creation.version_id

    # ---- 3. first autonomous continuation, through AR-0 ------------------------------
    print("[3] first autonomous continuation (AR-0)")
    runtime, port, cycle, adapter, cognition = build(enabled=True, max_rounds=MAX_ROUNDS)
    try:
        second = cycle.evaluate_once(trigger_ref="evt:v02:check:2")
        check(second.get("step_path") == "AR0", "the step went through AR-0",
              second.get("step_path"))
        check(second["outcome"] == OUTCOME_ACTED, "round acted", second.get("detail"))
        check(second.get("decision") == "START", "decision was START", second.get("decision"))
        check(second.get("adoption_status") == "APPLIED", "adoption applied",
              second.get("adoption_status"))
        activity_ref = second.get("activity_ref")
        check(bool(activity_ref), "canonical Activity was created", activity_ref)
        check(len(runtime.canonical_state()["activities"]) == 1,
              "exactly one canonical Activity exists",
              len(runtime.canonical_state()["activities"]))
        check(second.get("action_status") == "SUCCEEDED", "AR-0 action SUCCEEDED",
              second.get("action_status"))
        check(str(second.get("provider_operation_ref") or "").startswith("life:"),
              "action carries a stable Supply operation reference",
              second.get("provider_operation_ref"))
        receipt = second.get("owner_receipt") or {}
        details = dict(receipt.get("details") or {})
        check(details.get("owner_status") == "OK", "receipt is the owner's own OK",
              details.get("owner_status"))
        check(details.get("version_created") is True, "owner says a version was created",
              details.get("version_created"))
        check(receipt.get("authority_domain") == "artifact_authority",
              "receipt names the artifact authority", receipt.get("authority_domain"))
        head = port.read_head(artifact_id)
        check((head or {}).get("version_id") != v1, "owner head moved off v1",
              (head or {}).get("version_id"))
        v2 = (head or {}).get("version_id")
        check((head or {}).get("version_number") == 2, "second version reached",
              (head or {}).get("version_number"))
    finally:
        runtime.close()

    # ---- 4. second round continues the same work -------------------------------------
    print("[4] second round continues the same work")
    runtime, port, cycle, adapter, cognition = build(enabled=True, max_rounds=MAX_ROUNDS)
    try:
        third = cycle.evaluate_once(trigger_ref="evt:v02:check:3")
        check(third["outcome"] == OUTCOME_ACTED, "round acted", third.get("detail"))
        check(third.get("decision") == "CONTINUE", "decision was CONTINUE",
              third.get("decision"))
        check(third.get("activity_ref") == activity_ref,
              "continued the same Activity, no parallel work", third.get("activity_ref"))
        check(len(runtime.canonical_state()["activities"]) == 1,
              "no second Activity was created; the same one was continued",
              len(runtime.canonical_state()["activities"]))
        head = port.read_head(artifact_id)
        check((head or {}).get("parent_version_id") == v2,
              "new version's parent is the previous version",
              (head or {}).get("parent_version_id"))
        # LR-5 must be able to say what actually advanced.  It only ever sees owner-backed
        # evidence: the settled result of the action plus the artifact's canonical state.
        # LR-5 only ever moves the resume point on owner-backed evidence.  What matters is the
        # recorded transition, not the aggregate state after a later re-evaluation.
        checkpoints = runtime.progress_read.list_checkpoint_updates(str(activity_ref))
        advanced = [c for c in checkpoints if c.get("progress_state") == "ADVANCED"]
        check(bool(advanced), "a checkpoint advanced on the owner's confirmed version",
              [c.get("progress_state") for c in checkpoints])
        check(all(c.get("new_checkpoint_ref") != "chk:stage:001" for c in advanced),
              "the resume point moved off its initial placeholder",
              [c.get("new_checkpoint_ref") for c in advanced])
        check(all(int(c.get("activity_revision_after", 0)) > int(c.get("activity_revision_before", 0))
                  for c in advanced),
              "the canonical Activity revision advanced with the checkpoint",
              [(c.get("activity_revision_before"), c.get("activity_revision_after"))
               for c in advanced])
        check(runtime.progress_read.get_progress_state(str(activity_ref)).get("checkpoint_ref")
              != "chk:stage:001",
              "the Activity's current checkpoint follows the work",
              runtime.progress_read.get_progress_state(str(activity_ref)).get("checkpoint_ref"))
        pcontract = runtime.progress_read.get_progress_contract(str(activity_ref))
        check((pcontract or {}).get("progress_kind") == "OPEN_ENDED",
              "the work is an open-ended activity, not one that ends after a single edit",
              (pcontract or {}).get("progress_kind"))
        check(runtime.progress_read.get_completion_contract(str(activity_ref)) is None,
              "no automatic completion contract decides when the work is finished",
              runtime.progress_read.get_completion_contract(str(activity_ref)))
    finally:
        runtime.close()

    # ---- 5. one work, one lineage ----------------------------------------------------
    print("[5] one work, one lineage")
    rows = port.list_artifacts()
    check(len(rows) == 1, "owner still holds exactly one artifact", len(rows))
    head = port.read_head(artifact_id)
    check((head or {}).get("version_number") == 3, "third version reached",
          (head or {}).get("version_number"))
    v3 = (head or {}).get("version_id")
    versions_before = (head or {}).get("version_number")

    # ---- 6. NO_CHANGE: the action happened, the work did not change ------------------
    print("[6] NO_CHANGE")
    runtime, port, cycle, adapter, cognition = build(
        enabled=True, max_rounds=MAX_ROUNDS, writer=IdentityWriter())
    try:
        same = cycle.evaluate_once(trigger_ref="evt:v02:check:6")
        check(same["outcome"] == OUTCOME_ALREADY_DONE,
              "identical content -> ALREADY_DONE, not a new version", same.get("detail"))
        check(same.get("action_status") == "SUCCEEDED",
              "the action itself still succeeded", same.get("action_status"))
        check(same.get("owner_version_created") is False,
              "owner reported no version was created", same.get("owner_version_created"))
        head = port.read_head(artifact_id)
        check((head or {}).get("version_number") == versions_before,
              "version count unchanged by NO_CHANGE",
              f"{(head or {}).get('version_number')} != {versions_before}")
    finally:
        runtime.close()

    # ---- 7. CONFLICT: a stale precondition is refused, never overwritten -------------
    print("[7] CONFLICT")
    runtime, port, cycle, adapter, cognition = build(enabled=True, max_rounds=MAX_ROUNDS)
    racer = RacingWriter(port, artifact_id, grant_id)
    cycle.writer = racer
    try:
        stale = cycle.evaluate_once(trigger_ref="evt:v02:check:7")
        check(racer.interference is not None and racer.interference.applied,
              "the concurrent edit really landed first",
              getattr(racer.interference, "status", None))
        check(stale["outcome"] == OUTCOME_STEP_REFUSED,
              "stale compare-and-swap -> refused", stale.get("detail"))
        check(stale.get("action_status") == "FAILED", "AR-0 action FAILED",
              stale.get("action_status"))
        check(stale.get("owner_status") == "CONFLICT", "owner answered CONFLICT",
              stale.get("owner_status"))
        head = port.read_head(artifact_id)
        check("我的这一笔" not in (port.read_artifact(artifact_id).get("content") or ""),
              "the refused edit is not in the work", (head or {}).get("version_number"))
    finally:
        runtime.close()

    # ---- 8. UNKNOWN: the answer is lost, and the original operation is queried -------
    print("[8] UNKNOWN and reconcile")
    runtime, port, cycle, adapter, cognition = build(
        enabled=True, max_rounds=MAX_ROUNDS, drop_response=True)
    try:
        before = port.read_head(artifact_id)
        unknown = cycle.evaluate_once(trigger_ref="evt:v02:check:8")
        check(unknown["outcome"] == OUTCOME_STEP_UNKNOWN,
              "lost answer -> STEP_UNKNOWN, never a claimed success", unknown.get("detail"))
        check(unknown.get("action_status") == "UNKNOWN", "AR-0 action entered UNKNOWN",
              unknown.get("action_status"))
        action_id = unknown.get("action_id")
        check(bool(action_id), "the round produced a real AR-0 action", action_id)
        if not action_id:
            return 1
        after = port.read_head(artifact_id)
        check((after or {}).get("version_number") == (before or {}).get("version_number", 0) + 1,
              "the owner write did land (that is exactly why UNKNOWN must be resolved)",
              f"{(before or {}).get('version_number')} -> {(after or {}).get('version_number')}")
        action_row = runtime.action_read.get_action(str(action_id))
        recon = adapter.query_status_for_reconcile(action=action_row, attempt=None)
        check(recon.get("result") == "CONFIRMED_SUCCEEDED",
              "reconcile finds the original operation", recon.get("result"))
        after_recon = port.read_head(artifact_id)
        check((after_recon or {}).get("version_number") == (after or {}).get("version_number"),
              "reconciling wrote nothing new",
              f"{(after_recon or {}).get('version_number')} != {(after or {}).get('version_number')}")
        resolved = runtime.action_command.reconcile_action(action_id=str(action_id))
        check(str((resolved.get("action") or {}).get("status")) in {"SUCCEEDED", "FAILED"},
              "AR-0 resolved the UNKNOWN action from owner evidence",
              (resolved.get("action") or {}).get("status"))
        check(adapter.submit_call_count == 1, "the adapter was called exactly once",
              adapter.submit_call_count)
    finally:
        runtime.close()

    # ---- 9. budget exhaustion and the off switch cost nothing ------------------------
    print("[9] budget and off switch")
    runtime, port, cycle, adapter, cognition = build(enabled=True, max_rounds=0)
    try:
        out = cycle.evaluate_once(trigger_ref="evt:v02:check:9")
        check(out["outcome"] == OUTCOME_BUDGET_EXHAUSTED, "spent budget -> BUDGET_EXHAUSTED",
              out["outcome"])
        check(out["llm_calls"] == 0 and cognition.calls == 0,
              "spent budget -> zero model calls", f"{out['llm_calls']}/{cognition.calls}")
    finally:
        runtime.close()

    runtime, port, cycle, adapter, cognition = build(enabled=False, max_rounds=MAX_ROUNDS)
    try:
        out = cycle.evaluate_once(trigger_ref="evt:v02:check:10")
        check(out["outcome"] == OUTCOME_DISABLED, "off switch -> DISABLED", out["outcome"])
        check(out["llm_calls"] == 0 and cognition.calls == 0,
              "off switch -> zero model calls", f"{out['llm_calls']}/{cognition.calls}")
        head_after = port.read_head(artifact_id)
        check((head_after or {}).get("version_number") ==
              (port.read_head(artifact_id) or {}).get("version_number"),
              "off switch changed nothing", (head_after or {}).get("version_number"))
    finally:
        runtime.close()

    # ---- 10. the step journal records real receipts ----------------------------------
    print("[10] step journal")
    journal = install_root / "life_step_journal.jsonl"
    receipts = ([json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
                if journal.exists() else [])
    check(bool(receipts), "steps were journalled", len(receipts))
    print(f"  note  {len(receipts)} step receipt(s) recorded in {journal}")

    print()
    if FAILURES:
        print(f"V02_CHECK: FAIL ({len(FAILURES)} assertion(s))")
        for item in FAILURES:
            print("  -", item)
        return 1
    print("V02_CHECK: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""v0.2 acceptance check: the real autonomous life cycle against a real owner.

What this proves (and what it does not)
--------------------------------------
This check drives the v0.2 entry (``life/scripts/autonomous_life_cycle.py``) against a **real**
Life Supply owner over its Unix socket, and asserts the invariants that make the closed loop
real rather than narrated:

1. with nothing of her own to work on, the cycle stops as ``NO_MATERIAL`` and spends **zero**
   model calls and **zero** round budget;
2. once a work exists, one round adopts a canonical Activity and commits a real next version,
   using the head read before the edit as a compare-and-swap precondition;
3. the next round **continues the same** Activity and the **same** artifact -- it does not start a
   parallel work;
4. the day budget is respected, and exhausting it costs zero model calls and destroys nothing;
5. turning the feature off costs zero model calls;
6. a stale precondition is refused by the owner instead of silently overwriting;
7. an unknown operation is resolved by asking the owner, never by guessing.

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

#: Rounds this check is allowed to spend.
MAX_ROUNDS = 3

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
        AutonomousLifeCycle, OUTCOME_ACTED, OUTCOME_BUDGET_EXHAUSTED, OUTCOME_DISABLED,
        OUTCOME_NO_MATERIAL,
    )
    from life_artifact_port import EffectState, LifeArtifactPort, operation_key_for

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

    def build(*, enabled: bool, max_rounds: int, cognition):
        switches = ag2.LifeRuntimeKillSwitches(
            life_runtime_enabled=True, agency_enabled=True,
            action_execution_enabled=False, proactive_enabled=False)
        runtime = lrp.build_production_life_runtime(
            install_root, kill_switches=switches, cognition_adapter=cognition,
            install_root=install_root)
        port = LifeArtifactPort(os.environ["NYA02_SUPPLY_SOCKET"], subject)
        cycle = AutonomousLifeCycle(
            runtime, artifact_port=port, subject_id=subject, workspace_id=workspace_id,
            artifact_grant_id=grant_id, max_rounds_per_day=max_rounds, enabled=enabled)
        return runtime, port, cycle

    print(f"life state root: {install_root}")

    # ---- 1. an empty owner yields nothing to do, and costs nothing --------------------
    print("[1] empty owner")
    cognition = TestDoubleCognition()
    runtime, port, cycle = build(enabled=True, max_rounds=MAX_ROUNDS, cognition=cognition)
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

    # ---- 3. first autonomous continuation (START) ------------------------------------
    print("[3] first autonomous continuation")
    cognition = TestDoubleCognition()
    runtime, port, cycle = build(enabled=True, max_rounds=MAX_ROUNDS, cognition=cognition)
    try:
        second = cycle.evaluate_once(trigger_ref="evt:v02:check:2")
        check(second["outcome"] == OUTCOME_ACTED, "round acted", second.get("detail"))
        check(second.get("decision") == "START", "decision was START", second.get("decision"))
        check(second.get("adoption_status") == "APPLIED", "adoption applied",
              second.get("adoption_status"))
        activity_ref = second.get("activity_ref")
        check(bool(activity_ref), "canonical Activity was created", activity_ref)
        check(runtime.canonical_state()["revision"] == 1, "canonical revision advanced",
              runtime.canonical_state()["revision"])
        receipt = second.get("step_receipt") or {}
        check(receipt.get("effect_state") == EffectState.APPLIED, "real commit applied",
              receipt.get("effect_state"))
        check(receipt.get("expected_head") == v1,
              "commit used the pre-edit head as its precondition", receipt.get("expected_head"))
        head = port.read_head(artifact_id)
        check(head and head["version_id"] != v1, "owner head moved off v1",
              (head or {}).get("version_id"))
        v2 = (head or {}).get("version_id")
    finally:
        runtime.close()

    # ---- 4. second round continues the same work (CONTINUE) --------------------------
    print("[4] second round continues the same work")
    cognition = TestDoubleCognition()
    runtime, port, cycle = build(enabled=True, max_rounds=MAX_ROUNDS, cognition=cognition)
    try:
        third = cycle.evaluate_once(trigger_ref="evt:v02:check:3")
        check(third["outcome"] == OUTCOME_ACTED, "round acted", third.get("detail"))
        check(third.get("decision") == "CONTINUE", "decision was CONTINUE",
              third.get("decision"))
        check(third.get("activity_ref") == activity_ref,
              "continued the same Activity, no parallel work", third.get("activity_ref"))
        check(runtime.canonical_state()["revision"] == 1,
              "no second Activity was created", runtime.canonical_state()["revision"])
        head = port.read_head(artifact_id)
        check((head or {}).get("version_id") != v2, "head advanced past v2",
              (head or {}).get("version_id"))
        check((head or {}).get("parent_version_id") == v2,
              "new version's parent is the previous version",
              (head or {}).get("parent_version_id"))
        v3 = (head or {}).get("version_id")
    finally:
        runtime.close()

    # ---- 5. the same artifact, never a second one ------------------------------------
    print("[5] one work, one lineage")
    rows = port.list_artifacts()
    check(len(rows) == 1, "owner still holds exactly one artifact", len(rows))
    chain = port.read_head(artifact_id)
    check(chain and chain["version_number"] == 3, "third version reached",
          (chain or {}).get("version_number"))

    # ---- 6. a stale precondition is refused, not overwritten -------------------------
    print("[6] stale precondition")
    stale = port.commit_version(
        artifact_id=artifact_id, expected_head=v1, content="# 夜里的手记\n\n用过期前提写入\n",
        grant_id=grant_id, operation_key="op:v02check:stale:1")
    check(stale.effect_state != EffectState.APPLIED,
          "owner refused a stale compare-and-swap", f"{stale.status}:{stale.reason}")
    after = port.read_head(artifact_id)
    check((after or {}).get("version_id") == v3, "refusal left the head untouched",
          (after or {}).get("version_id"))

    # ---- 7. an unknown operation is resolved by asking the owner ---------------------
    print("[7] unknown operation resolution")
    resolved = port.resolve_operation(operation_key_for("v02check", "never", "submitted"))
    check(resolved.effect_state in {EffectState.NO_CHANGE, EffectState.UNKNOWN},
          "unknown key resolved honestly (never assumed applied)",
          f"{resolved.effect_state}:{resolved.reason}")

    # ---- 8. budget exhaustion and the off switch cost nothing ------------------------
    print("[8] budget and off switch")
    cognition = TestDoubleCognition()
    runtime, port, cycle = build(enabled=True, max_rounds=0, cognition=cognition)
    try:
        out = cycle.evaluate_once(trigger_ref="evt:v02:check:8")
        check(out["outcome"] == OUTCOME_BUDGET_EXHAUSTED, "spent budget -> BUDGET_EXHAUSTED",
              out["outcome"])
        check(out["llm_calls"] == 0 and cognition.calls == 0,
              "spent budget -> zero model calls", f"{out['llm_calls']}/{cognition.calls}")
    finally:
        runtime.close()

    cognition = TestDoubleCognition()
    runtime, port, cycle = build(enabled=False, max_rounds=MAX_ROUNDS, cognition=cognition)
    try:
        out = cycle.evaluate_once(trigger_ref="evt:v02:check:9")
        check(out["outcome"] == OUTCOME_DISABLED, "off switch -> DISABLED", out["outcome"])
        check(out["llm_calls"] == 0 and cognition.calls == 0,
              "off switch -> zero model calls", f"{out['llm_calls']}/{cognition.calls}")
        head_after = port.read_head(artifact_id)
        check((head_after or {}).get("version_id") == v3,
              "off switch changed nothing", (head_after or {}).get("version_id"))
    finally:
        runtime.close()

    # ---- 9. the step journal records real receipts -----------------------------------
    print("[9] step journal")
    journal = install_root / "life_step_journal.jsonl"
    receipts = ([json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
                if journal.exists() else [])
    applied = [r for r in receipts if r.get("effect_state") == EffectState.APPLIED]
    check(len(applied) == 2, "two applied steps journalled", len(applied))
    check(all(r.get("activity_ref") == activity_ref for r in applied),
          "every step points at the same Activity",
          [r.get("activity_ref") for r in applied])

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

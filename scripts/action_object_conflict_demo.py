#!/usr/bin/env python3
"""Scene 6, positive chain: a successful Action whose object re-check conflicts is not "completed".

The earlier strict-counterexample run (see the 2026-10-29 stop note) only exercised the
``cycle.evaluate_once(act=True)`` entrance and therefore could not see a seam between
"AR-1 settled" and "LR-5 completed".  That narrow reading is not the whole tree.  This demo
uses the *formal* adoption / execute / settle APIs the tree already owns, step by step, on a
real isolated instance (real Supply owner, real production Life runtime, real AR-0/AR-1/LR-5):

1. ``run_decision_and_adoption_cycle(..., execute_initial_step=False)`` -- a real AG-1
   committed ``START`` Decision is adopted with the closeout ``ActivityAdoptionSpec``
   (``CLOSE_PERSONAL_ARTIFACT``), so the Activity really owns a completion contract, and
   nothing is executed yet;
2. ``create_step_and_propose_action`` + ``submit_step_action_and_apply_post_submit_behavior``
   -- the owner really archives the work, AR-0 records the real provider receipt, and the
   Action reaches ``SUCCEEDED``;
3. ``settle_and_propagate_action_result(additional_evidences=[...])`` -- one object re-check
   (a fixture, marked as such, that disagrees with the archive attestation) enters AR-1
   through its own documented ``additional_evidences`` parameter *before* LR-5 evaluates.
   AR-1 answers ``CONFLICT`` / ``UNKNOWN_EFFECT``; LR-5 answers ``CONFLICT`` and the Activity
   stays ``ACTIVE`` -- it is never flipped to ``COMPLETED``.

The object re-check evidence is a fixture: its values are stated as a fixture and it is not
passed off as a naturally occurring concurrent observation.  It travels through the same
AR-1 intake and LR-5 acceptance a real observer would use; no canonical JSON is edited, no
owner receipt is minted, no Activity is flipped, and no product model is called
(Decision/content are scripted stand-ins).

Controls kept from the original task:

* a re-check bound to a *different* ``action_id`` is refused by AR-1 by name;
* the same conflict evidence re-settled is an idempotent replay (no second write);
* a control chain *without* the conflict really completes, proving completion is the default
  and the conflict -- not some unrelated breakage -- is what blocks it;
* the settled reason is readable from the existing review CLI.

Exit 0 means the scene held: the Action succeeded, the object re-check conflicted, the
Activity was not completed, and no completion is faked.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import result_method_behavior_smoke as base  # noqa: E402
import v05_restart_continuation_smoke as restart  # noqa: E402

_CHECKS: list[tuple[str, bool, object]] = []


def say(text: str) -> None:
    print(text)


def check(name: str, condition: object, detail: object = None) -> bool:
    ok = bool(condition)
    _CHECKS.append((name, ok, detail))
    print("CHECK %-70s %s" % (name, "PASS" if ok else "FAIL"))
    if not ok and detail is not None:
        print("  detail:", json.dumps(detail, ensure_ascii=False, default=str)[:1500])
    return ok


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------------------------
# the real isolated chain (real Supply owner + real production runtime)
# ---------------------------------------------------------------------------------------------


def _open_chain(root: Path, work_artifact_id: str):
    import autonomous_life_assembly as assembly
    import life_artifact_port as lap
    import life_integration_ag2 as ag2
    import life_runtime_production as lrp
    import managed_artifact_action_adapter as maaa
    import one_day_life as odl
    from life_experience_memory_consumer import MemoryAdmissionExperienceConsumer
    from life_experience_port import LifeExperiencePort
    from model_continuation_writer import ModelContinuationWriter

    root = Path(root)
    services = restart._start_services(root / "supply")
    runtime = None
    try:
        instance = root / "instance"
        data = odl.read_instance(instance)
        env = odl.instance_environment(instance)
        memory_dir = root / "memory"
        memory_dir.mkdir(exist_ok=True)
        evidence_db = memory_dir / "memory.sqlite3"
        controls_db = memory_dir / "controls.sqlite3"
        env.update({
            "NYAIRO_LIFE_SUPPLY_SOCKET": str(services.supply_path),
            "NYAIRO_LIFE_SUBJECT": str(data["subject_id"]),
            "NYAIRO_LIFE_WORKSPACE_ID": str(data["workspace_id"]),
            "NYAIRO_ARTIFACT_GRANT": str(data["artifact_grant_id"]),
            "NYA02_MEMORY_DB": str(evidence_db),
            "NYA02_MEMORY_CONTROLS_DB": str(controls_db),
            "NYA02_MEMORY_SUBJECT": "chiyo",
            "CHIYO_LIFE_WORK_ARTIFACT_ID": str(work_artifact_id),
        })
        if data.get("private_items_artifact_id"):
            env.setdefault("CHIYO_LIFE_PRIVATE_ITEMS_ARTIFACT_ID",
                           str(data["private_items_artifact_id"]))
        cognition = base.ScriptedCognition()
        client = base.ScriptedClient()
        writer = ModelContinuationWriter(client, max_new_chars=300)
        port = lap.LifeArtifactPort(str(services.supply_path), data["subject_id"])
        adapter = maaa.SupplyManagedArtifactActionAdapter(
            port, grant_id=data["artifact_grant_id"], workspace_id=data["workspace_id"])
        memory_port = LifeExperiencePort(str(evidence_db), controls_db=str(controls_db),
                                         subject="chiyo")
        consumer = MemoryAdmissionExperienceConsumer(port=memory_port, enabled=True)
        switches = ag2.LifeRuntimeKillSwitches(
            life_runtime_enabled=True, agency_enabled=True, action_execution_enabled=True,
            proactive_enabled=False)
        runtime = lrp.build_production_life_runtime(
            odl.life_state_root(instance), kill_switches=switches, cognition_adapter=cognition,
            install_root=instance / "life",
            artifact_action_adapter=adapter,
            experience_consumer=consumer)
        assembled = assembly.assemble_autonomous_life(
            runtime, environment=env, state_root=odl.life_state_root(instance),
            writer=writer, enabled=True)
        if assembled["state"] != assembly.READY:
            raise SystemExit("assembly not READY: %r" % (assembled,))
        return SimpleNamespace(root=root, services=services, runtime=runtime,
                               cycle=assembled["cycle"], cognition=cognition, client=client,
                               data=data, env=env, port=port, memory_port=memory_port,
                               evidence_db=evidence_db, controls_db=controls_db,
                               instance=instance, state_root=odl.life_state_root(instance))
    except BaseException:
        if runtime is not None:
            try:
                runtime.close()
            except Exception:
                pass
        restart._stop_services(services)
        raise


# ---------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------


def _candidates(chain, observed_at: str) -> list[str]:
    materialized = chain.runtime.candidate_materializer.build_candidate_set(
        observed_at=observed_at)
    return [str(ref) for ref in materialized.candidate_refs]


def _candidate_payload(chain, ref: str) -> dict:
    return dict(chain.runtime.candidate_read.get_candidate(str(ref)) or {})


def find_closeout_candidate(chain, observed_at: str) -> str | None:
    for ref in _candidates(chain, observed_at):
        refs = [str(value) for value in (_candidate_payload(chain, ref).get("target_refs") or ())]
        if any(value.startswith("closeout:") for value in refs):
            return ref
    return None


def choose(chain, decision: str, candidate_id: str | None) -> None:
    chain.cognition.script = {"decision": decision, "selected_candidate_id": candidate_id,
                              "deferred_candidate_ids": []}


def _activity(chain, activity_id: str) -> dict:
    return dict(chain.runtime.activity_read.get_activity(activity_id) or {})


def _versions(chain, artifact_id: str) -> int:
    return int(chain.port.list_versions(artifact_id, limit=50)["total_versions"])


def _progress(chain, activity_id: str) -> dict:
    return dict(chain.runtime.progress_read.get_progress_state(activity_id) or {})


# ---------------------------------------------------------------------------------------------
# the formal step-by-step entrance: adopt (no execute), submit for real, settle on re-check
# ---------------------------------------------------------------------------------------------


def adopt_closeout_without_executing(chain):
    """A real START Decision is adopted into the closeout shape; nothing runs yet."""

    point = chain.cycle._closeout_point() or {}
    if str(point.get("state")) != "READY":
        return point, None, None
    observed_at = _now_iso()
    candidate = find_closeout_candidate(chain, observed_at)
    if candidate is None:
        return point, None, None
    choose(chain, "START", candidate)
    spec = chain.cycle._closeout_adoption_spec(point)
    out = dict(chain.runtime.coordinator.run_decision_and_adoption_cycle(
        trigger_kind="CANDIDATE_SET_CHANGED",
        trigger_ref="evt:object-check:closeout",
        observed_at=observed_at,
        adoption_spec=spec,
        execute_initial_step=False) or {})
    return point, observed_at, out


def prepared_closeout_step(chain, point: dict, observed_at: str, out: dict):
    """Prepare the one archive step through AR-0; the Action exists but has not run."""

    coordinator = chain.runtime.coordinator
    decision_record = (out.get("decision_result") or {}).get("decision_record")
    adoption_record = (out.get("adoption_result") or {}).get("adoption_record")
    integration = out.get("integration_record")
    activity_id = getattr(integration, "activity_ref", None)
    step = chain.cycle._closeout_step_spec(
        point, decision_record=decision_record, observed_at=observed_at)
    bridge = coordinator.action_bridge.create_step_and_propose_action(
        activity_id=activity_id,
        decision_ref=decision_record.decision_id,
        adoption_ref=adoption_record.adoption_id,
        correlation_id=integration.correlation_id,
        step_kind=step["step_kind"],
        action_kind=step["action_kind"],
        target_ref=step["target_ref"],
        payload_spec=dict(step["payload_spec"]),
        expected_effect_predicates=list(step.get("expected_effect_predicates") or []),
        post_submit_life_behavior=step.get("post_submit_life_behavior"),
        idempotency_key=step.get("idempotency_key"),
        occurred_at=observed_at)
    return bridge


def submit_prepared_step(chain, bridge: dict, observed_at: str):
    """The owner really performs the archive; AR-0 records the provider receipt."""

    coordinator = chain.runtime.coordinator
    return dict(coordinator.action_bridge.submit_step_action_and_apply_post_submit_behavior(
        step_spec=bridge["step_spec"],
        action_id=bridge["action"]["action_id"],
        executor_capability=coordinator.executor_capability,
        settle_immediately=True,
        occurred_at=observed_at) or {})


def conflict_recheck(action_id: str, target_ref: str, observed_at: str):
    """One object re-check (a fixture) that disagrees with the archive attestation.

    It is handed to AR-1 through its own ``additional_evidences`` intake, exactly where a
    real observer's second look would arrive.  The hash pair and ``hash_mismatch`` flag are
    fixture values; the note says so.
    """

    import result_settlement_ar1 as ar1

    tag = action_id.replace(":", "-")
    return ar1.SettlementEvidenceInput(
        evidence_id="robse:object-recheck-%s" % tag,
        root_evidence_id="rootev:object-recheck-%s" % tag,
        action_id=action_id,
        source_ref="artifact_authority:object-recheck-fixture",
        authority_domain="artifact_authority",
        evidence_kind="filesystem_observed",
        authority_grade=ar1.AUTH_GRADE_OBSERVER_VERIFIED,
        claim_kind="observer:artifact_observer",
        verified=False,
        occurred_at=observed_at, observed_at=observed_at, recorded_at=observed_at,
        target_ref=str(target_ref),
        expected_hash="sha256:fixture-expected",
        observed_hash="sha256:fixture-observed-mismatch",
        details={"hash_mismatch": True,
                 "fixture": "object re-check stands in for a second owner observation"})


# ---------------------------------------------------------------------------------------------
# 1. the scene: Action succeeds, object re-check conflicts, Activity is not completed
# ---------------------------------------------------------------------------------------------


def object_conflict_round(root: Path, work: str) -> dict:
    say("== 1. a successful archive whose object re-check conflicts is NOT completed ==")
    import result_settlement_ar1 as ar1

    chain = _open_chain(root, work)
    try:
        point, observed_at, out = adopt_closeout_without_executing(chain)
        check("the closeout plan offers the bound work", str(point.get("state")) == "READY",
              {"state": point.get("state")})
        if not out:
            return {"ok": False}

        decision_record = (out.get("decision_result") or {}).get("decision_record")
        adoption_record = (out.get("adoption_result") or {}).get("adoption_record")
        integration = out.get("integration_record")
        activity_id = str(getattr(integration, "activity_ref", "") or "")
        check("AG-1 really committed START for the closeout candidate",
              str(getattr(decision_record, "decision", "")) == "START",
              {"decision": getattr(decision_record, "decision", None)})
        check("AG-2 really adopted it without executing",
              str(getattr(adoption_record, "adoption_status", "")) == "APPLIED"
              and bool(activity_id),
              {"adoption_status": getattr(adoption_record, "adoption_status", None),
               "activity_ref": activity_id})
        activity = _activity(chain, activity_id)
        contract = (chain.runtime.progress_read.get_completion_contract(activity_id)
                    if activity_id else None)
        check("the adopted Activity carries the closeout completion contract",
              str(activity.get("activity_kind") or "") == "CLOSE_PERSONAL_ARTIFACT"
              and contract is not None
              and str(contract.get("criteria_kind")) == "ACTION_RESULT_MATCH",
              {"activity_kind": activity.get("activity_kind"),
               "has_contract": contract is not None,
               "criteria_kind": (contract or {}).get("criteria_kind")})

        bridge = prepared_closeout_step(chain, point, observed_at, out)
        action_id = str(bridge["action"]["action_id"])
        check("AR-0 prepared the archive Action (not run yet)",
              str(bridge["action"].get("status")) == "PREPARED",
              {"action_status": bridge["action"].get("status")})

        # Negative control: a re-check bound to another action is refused by AR-1 by name.
        coordinator = chain.runtime.coordinator
        wrong = ar1.SettlementEvidenceInput(
            evidence_id="robse:object-recheck-wrong-action",
            root_evidence_id="rootev:object-recheck-wrong-action",
            action_id="actn:ffffffffffffffff",
            source_ref="artifact_authority:object-recheck-fixture",
            authority_domain="artifact_authority",
            evidence_kind="filesystem_observed",
            authority_grade=ar1.AUTH_GRADE_OBSERVER_VERIFIED,
            claim_kind="observer:artifact_observer",
            verified=False,
            occurred_at=observed_at, observed_at=observed_at, recorded_at=observed_at,
            target_ref=str(point.get("target_ref")),
            expected_hash="sha256:fixture-expected",
            observed_hash="sha256:fixture-observed-mismatch",
            details={"hash_mismatch": True})
        refused = False
        try:
            coordinator.settlement_service.settle_action(
                action_id=action_id, additional_evidences=[wrong], occurred_at=observed_at)
        except ar1.ContractViolationError:
            refused = True
        check("a re-check bound to a different action is refused (no cross-action bind)",
              refused and str(_activity(chain, activity_id).get("status")) == "ACTIVE",
              {"refused": refused})

        versions_before = _versions(chain, work)
        submit_prepared_step(chain, bridge, observed_at)
        action = dict(chain.runtime.action_read.get_action(action_id) or {})
        receipts = chain.runtime.action_read.list_receipts(action_id)
        owner_view = dict(chain.port.read_artifact(work) or {})
        lifecycle = str((owner_view.get("artifact") or {}).get("lifecycle") or "")
        check("the archive Action really succeeded and left a real owner receipt",
              str(action.get("status")) == "SUCCEEDED" and len(receipts) >= 1,
              {"action_status": action.get("status"), "receipts": len(receipts)})
        check("the owner really archived the work and no new version was minted",
              lifecycle == "ARCHIVED" and _versions(chain, work) == versions_before,
              {"lifecycle": lifecycle, "versions_before": versions_before,
               "versions_now": _versions(chain, work)})

        settled = dict(coordinator.settle_and_propagate_action_result(
            action_id=action_id, integration_id=integration.integration_id,
            additional_evidences=[conflict_recheck(action_id, str(point.get("target_ref")),
                                                   observed_at)],
            occurred_at=observed_at) or {})
        settlement = dict((settled.get("settlement_result") or {}).get("settlement") or {})
        check("AR-1 answers CONFLICT / UNKNOWN_EFFECT for the re-checked object",
              str(settlement.get("settlement_status")) == "CONFLICT"
              and str(settlement.get("outcome_kind")) == "UNKNOWN_EFFECT",
              {"settlement_status": settlement.get("settlement_status"),
               "outcome_kind": settlement.get("outcome_kind")})

        activity_after = _activity(chain, activity_id)
        progress = _progress(chain, activity_id)
        integration_after = dict(chain.runtime.integration_read.read_state(
            mutable=True)["integrations_by_id"][integration.integration_id])
        check("the Activity is NOT completed (stays ACTIVE, no completion evidence)",
              str(activity_after.get("status")) == "ACTIVE"
              and not activity_after.get("completion_evidence_ref"),
              {"activity_status": activity_after.get("status"),
               "completion_evidence_ref": activity_after.get("completion_evidence_ref")})
        check("LR-5 is BLOCKED by the conflict, with a readable reason",
              str(progress.get("progress_state")) == "BLOCKED"
              and str(progress.get("completion_status")) == "CONFLICT"
              and str(progress.get("completion_reason"))
              == "CONFLICTING_EVIDENCE_BLOCKS_COMPLETION",
              progress)
        check("the integration is marked CONFLICT and no completion handoff was made",
              str(integration_after.get("status")) == "CONFLICT"
              and str(integration_after.get("failure_reason_code"))
              == "SETTLEMENT_CONFLICT_DETECTED",
              {"status": integration_after.get("status"),
               "failure_reason_code": integration_after.get("failure_reason_code")})

        # Idempotency: the same re-check re-delivered must not write a second settlement.
        replay = dict(coordinator.settle_and_propagate_action_result(
            action_id=action_id, integration_id=integration.integration_id,
            additional_evidences=[conflict_recheck(action_id, str(point.get("target_ref")),
                                                   observed_at)],
            occurred_at=observed_at) or {})
        check("re-settling the same re-check is an idempotent replay",
              bool((replay.get("settlement_result") or {}).get("idempotent_replay"))
              and str(_activity(chain, activity_id).get("status")) == "ACTIVE",
              {"idempotent_replay":
               (replay.get("settlement_result") or {}).get("idempotent_replay")})

        return {"ok": True, "root": chain.root, "activity_id": activity_id,
                "action_id": action_id, "state_root": str(chain.state_root),
                "completion_reason": progress.get("completion_reason")}
    finally:
        restart.close_chain(chain)


# ---------------------------------------------------------------------------------------------
# 2. control: without a conflicting re-check the same chain really completes
# ---------------------------------------------------------------------------------------------


def completion_control_round(root: Path, work: str) -> dict:
    say("\n== 2. control: the same archive step completes when nothing conflicts ==")
    chain = _open_chain(root, work)
    try:
        point, observed_at, out = adopt_closeout_without_executing(chain)
        if not out:
            return {"ok": False}
        integration = out.get("integration_record")
        activity_id = str(getattr(integration, "activity_ref", "") or "")
        bridge = prepared_closeout_step(chain, point, observed_at, out)
        submit_prepared_step(chain, bridge, observed_at)
        coordinator = chain.runtime.coordinator
        settled = dict(coordinator.settle_and_propagate_action_result(
            action_id=str(bridge["action"]["action_id"]),
            integration_id=integration.integration_id, occurred_at=observed_at) or {})
        settlement = dict((settled.get("settlement_result") or {}).get("settlement") or {})
        activity = _activity(chain, activity_id)
        check("without a conflict AR-1 settles SUCCESS",
              str(settlement.get("settlement_status")) == "SETTLED",
              {"settlement_status": settlement.get("settlement_status")})
        check("and the Activity really reaches COMPLETED with completion evidence",
              str(activity.get("status")) == "COMPLETED"
              and bool(activity.get("completion_evidence_ref")),
              {"activity_status": activity.get("status"),
               "completion_evidence_ref": activity.get("completion_evidence_ref")})
        return {"ok": True}
    finally:
        restart.close_chain(chain)


# ---------------------------------------------------------------------------------------------
# 3. the reason is readable from the existing review CLI
# ---------------------------------------------------------------------------------------------


def readable_from_cli(state_root: str, action_id: str) -> dict:
    say("\n== 3. the settled reason is readable from the existing review CLI ==")
    cli = ROOT / "scripts" / "infra_settlement_review_cli.py"
    proc = subprocess.run(
        [sys.executable, str(cli), "--state-root", state_root,
         "--action-id", action_id, "--format", "json"],
        capture_output=True, text=True, timeout=180)
    text = proc.stdout or ""
    found = "CONFLICTING_EVIDENCE_BLOCKS_COMPLETION" in text
    check("review CLI exits 0 and shows the conflict reason", proc.returncode == 0 and found,
          {"returncode": proc.returncode, "stderr": (proc.stderr or "")[:400],
           "found_reason": found})
    return {"ok": proc.returncode == 0 and found, "returncode": proc.returncode}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)

    root = restart.prepare_fixture()
    import one_day_life as odl

    work = str(odl.read_instance(root / "instance")["artifact_id"])
    print("root:", root)
    print("work:", work)

    conflict = object_conflict_round(root, work)

    control_root = restart.prepare_fixture()
    control_work = str(odl.read_instance(control_root / "instance")["artifact_id"])
    control = completion_control_round(control_root, control_work)

    cli = (readable_from_cli(conflict["state_root"], conflict["action_id"])
           if conflict.get("ok") else {"ok": False})

    failed = [name for name, ok, _detail in _CHECKS if not ok]
    print("\n%d checks, %d failed" % (len(_CHECKS), len(failed)))
    for name in failed:
        print("  FAILED:", name)
    if failed:
        print("RESULT: OBSERVATION_FAILED -- the demo's own reading did not match the tree")
        return 2
    if conflict.get("ok") and control.get("ok") and cli.get("ok"):
        print("RESULT: SCENE_HELD -- Action SUCCEEDED, object re-check CONFLICTed, "
              "Activity not COMPLETED (control completes without the conflict)")
        return 0
    print("RESULT: UNEXPECTED -- the scene or its control could not be observed")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

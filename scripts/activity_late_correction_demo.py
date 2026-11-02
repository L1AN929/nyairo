#!/usr/bin/env python3
"""Scene 7: a late correction after COMPLETED -- the conflict is visible, the terminal
Activity is never silently reopened.

One real isolated instance (real Supply owner + real LR-2/AR-0/AR-1/LR-5 runtime):

1. a bounded Activity A with an existing completion contract (artifact closeout) is
   started by a *formal* ``evaluate_once`` round (scripted Decision, real AG-1/AG-2),
   archives its own work through the real owner and reaches ``COMPLETED``;
2. a real AR-1 settlement correction is produced by the owner's own ``settle_action``
   (a late structured observer report on the same archive Action), delivered through
   the owner outbox to LR-5;
3. LR-5 records ``COMPLETION_CONFLICT`` / ``RECONCILIATION_REQUIRED`` bound to A's own
   ``completion_evidence_ref``; A stays ``COMPLETED``, the foreground is not taken, no
   new Activity/Action/version/Memory row appears;
4. the same correction is delivered again (idempotent), the real CLI can show it, and a
   *new process* reopens the same root and still sees the conflict;
5. named negative controls: an unknown settlement, B's real event re-pointed at A, and a
   correction that supersedes an unrelated settlement are all refused before any effect.

No product model is called (Decision/content are scripted stand-ins); no files or roots
are deleted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
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
    print("CHECK %-64s %s" % (name, "PASS" if ok else "FAIL"))
    if not ok and detail is not None:
        print("  detail:", json.dumps(detail, ensure_ascii=False, default=str)[:1200])
    return ok


def _utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _later(base_iso: str, seconds: int = 120) -> str:
    parsed = datetime.fromisoformat(str(base_iso).replace("Z", "+00:00"))
    return _utc(parsed + timedelta(seconds=seconds))


# ---------------------------------------------------------------------------------------------
# the real isolated chain (one local copy of the standard harness so the seeded work is bound)
# ---------------------------------------------------------------------------------------------


def _open_chain(root: Path, work_artifact_id: str):
    """Re/open the same real owners + a new runtime/assembly in this process.

    This mirrors the repository's restart harness and adds exactly one thing: the seeded
    work is named through ``CHIYO_LIFE_WORK_ARTIFACT_ID`` so the *real* closeout seam can
    offer it.  No owner is replaced and no step is faked.
    """
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
            install_root=instance / "life", artifact_action_adapter=adapter,
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
# formal round helpers (scripted Decision/content; real AG-1/AG-2/AR-0/AR-1/LR-5)
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


def round_out(chain, tag: str, observed_at: str) -> dict:
    return dict(chain.cycle.evaluate_once(
        trigger_ref="evt:late-correction:%s" % tag, observed_at=observed_at, act=True) or {})


def _activity(chain, activity_id: str) -> dict:
    return dict(chain.runtime.activity_read.get_activity(activity_id) or {})


def _canonical(chain) -> dict:
    state, _journal = chain.runtime.activity_read.read_canonical_state(include_journal=False)
    return dict(state or {})


def _versions(chain, artifact_id: str) -> int:
    return int(chain.port.list_versions(artifact_id, limit=50)["total_versions"])


def _memory_rows(chain) -> int:
    return len(chain.memory_port.recent(limit=50))


# ---------------------------------------------------------------------------------------------
# the live phase
# ---------------------------------------------------------------------------------------------


def _create_control_artifact(chain, work: str) -> str:
    """A second real work through the owner, used only as a never-attach control (B)."""
    outcome = chain.port.create_artifact(
        workspace_id=str(chain.data["workspace_id"]),
        title="对照作品 B（不得与 A 的完成相混）",
        content="B 的第一版：仅用于来源/身份防串，不参与 A 的完成。\n",
        grant_id=str(chain.data["artifact_grant_id"]),
        operation_key="op:late-correction:create-b:1")
    if not getattr(outcome, "applied", False) or not outcome.artifact_id:
        raise SystemExit("control artifact B was not created: %r" % (outcome.to_dict(),))
    return str(outcome.artifact_id)


def _b_action_and_settlement(chain, b_artifact: str) -> dict:
    """A real AR-0 action + AR-1 settlement owned by B (never by A)."""
    import action_reality_ledger as ar0
    import managed_artifact_action_adapter as maaa

    head = chain.port.read_head(b_artifact) or {}
    payload = {"op": maaa.OP_COMMIT_VERSION, "artifact_id": b_artifact,
               "expected_head": str(head.get("version_id") or ""),
               "content": "B 的第二版：她的另一端文本。\n"}
    proposed = chain.runtime.action_command.propose_action(
        action_kind=ar0.ACTION_KIND_FILE,
        target_ref="artifact:%s" % b_artifact.split("artifact:")[-1],
        payload_ref="pay:late-correction:b",
        payload_data=payload,
        idempotency_key="ar0:late-correction:b",
        source_refs=["evt:late-correction:b"])
    action_id = str(proposed["action"]["action_id"])
    chain.runtime.action_command.prepare_action(action_id=action_id)
    chain.runtime.action_command.submit_action(
        action_id=action_id, executor_capability=chain.runtime.executor_capability)
    settled = chain.runtime.settlement_service.settle_action(action_id=action_id)
    return {"action_id": action_id,
            "event_id": str((settled.get("outbox_event") or {}).get("event_id") or ""),
            "settlement_id": str(settled["settlement"]["settlement_id"]),
            "status": str(settled["settlement"]["settlement_status"])}


def live_phase(root: Path, work_artifact_id: str) -> int:
    say("== 1. a formal round closes out A through the real owner ==")
    chain = _open_chain(root, work_artifact_id)
    try:
        a_artifact = chain.port.resume_point(work_artifact_id)
        if str(a_artifact.lifecycle) != "ACTIVE":
            raise SystemExit("the seeded work is not ACTIVE: %r" % (a_artifact.lifecycle,))
        versions_before = _versions(chain, work_artifact_id)
        activities_before = set((_canonical(chain).get("activities") or {}))
        memory_before = _memory_rows(chain)
        foreground_before = _canonical(chain).get("foreground_activity_ref")

        observed_at = _utc(_now())
        closeout = find_closeout_candidate(chain, observed_at)
        check("the real closeout seam offers A (an existing completion contract)",
              closeout is not None, {"candidates": _candidates(chain, observed_at)})
        if closeout is None:
            return 1
        choose(chain, "START", closeout)
        closed = round_out(chain, "closeout", observed_at)
        activity_id = str(closed.get("activity_ref") or "")
        action_id = str(closed.get("action_id") or "")
        act = _activity(chain, activity_id) if activity_id else {}
        settlement = (chain.runtime.settlement_read.get_settlement(action_id)
                      if action_id else None)
        check("AG-1/AG-2 really started the closeout and AR-0/AR-1 archived A",
              closed.get("outcome") in {"ACTED", "ALREADY_DONE"}
              and bool(activity_id) and bool(action_id)
              and str((settlement or {}).get("settlement_status")) == "SETTLED",
              {"round": {k: closed.get(k) for k in (
                  "outcome", "decision", "adoption_status", "action_id", "activity_ref",
                  "failure_class", "detail", "error")},
               "settlement": (settlement or {}).get("settlement_status")})
        check("LR-5 evaluated the existing completion contract and A is COMPLETED",
              str(act.get("status")) == "COMPLETED" and bool(act.get("completion_evidence_ref")),
              {"status": act.get("status"),
               "completion_evidence_ref": act.get("completion_evidence_ref"),
               "revision": act.get("revision")})
        check("the owner really archived A; no extra version was invented",
              str((chain.port.read_artifact(work_artifact_id) or {}).get("artifact", {})
                  .get("lifecycle")) == "ARCHIVED"
              and _versions(chain, work_artifact_id) == versions_before,
              {"versions": _versions(chain, work_artifact_id), "before": versions_before})
        check("completion freed the foreground and started no second Activity",
              _canonical(chain).get("foreground_activity_ref") is None
              and set((_canonical(chain).get("activities") or {})) == activities_before | {activity_id},
              {"foreground": _canonical(chain).get("foreground_activity_ref"),
               "activities": sorted((_canonical(chain).get("activities") or {}))})
        if str(act.get("status")) != "COMPLETED":
            return 1

        say("\n== 2. a real late correction supersedes exactly A's completion settlement ==")
        import result_settlement_ar1 as ar1

        original_settlement_id = str((settlement or {})["settlement_id"])
        original_evidence = chain.runtime.action_read.list_result_evidences(action_id)
        expected_hash = next((str(row.get("observed_hash") or "")
                              for row in original_evidence if row.get("observed_hash")), "")
        if not expected_hash:
            head_before = chain.port.read_head(work_artifact_id) or {}
            expected_hash = str(head_before.get("content_sha256") or "")
        late_observed_at = _later(str(act.get("updated_at") or _utc(_now())), 180)
        late = ar1.SettlementEvidenceInput(
            evidence_id="aevid:%s" % hashlib.sha256(
                ("late-audit:%s" % action_id).encode("utf-8")).hexdigest()[:16],
            root_evidence_id="rootev:%s" % hashlib.sha256(
                ("late-audit-root:%s" % action_id).encode("utf-8")).hexdigest()[:16],
            action_id=action_id,
            source_ref="audit:late-archive-check:%s" % action_id,
            authority_domain="artifact_authority",
            evidence_kind=ar1.EVIDENCE_KIND_FILESYSTEM_OBSERVED,
            authority_grade=ar1.AUTH_GRADE_OBSERVER_VERIFIED,
            claim_kind="observer:late_audit",
            verified=False,
            occurred_at=late_observed_at,
            observed_at=late_observed_at,
            recorded_at=late_observed_at,
            # The late observer reports on the artifact the archive was about -- the same
            # target the original archive observer named, so the two really contradict.
            target_ref=str(work_artifact_id),
            expected_hash=expected_hash or "sha256:unknown-expected",
            observed_hash="sha256:%s" % hashlib.sha256(
                ("late-audit-content:%s" % action_id).encode("utf-8")).hexdigest(),
            details={"late_correction": True, "reason": "post_completion_hash_mismatch"})
        corrected = chain.runtime.settlement_service.settle_action(
            action_id=action_id, additional_evidences=[late])
        correction_event = dict(corrected.get("outbox_event") or {})
        check("AR-1 superseded the completion settlement with a CONFLICT correction event",
              str(corrected["settlement"]["settlement_status"]) == "CONFLICT"
              and str(correction_event.get("event_kind")) == "SETTLED_RESULT_CORRECTION"
              and str(correction_event.get("supersedes_settlement_ref")) == original_settlement_id,
              {"settlement": corrected["settlement"]["settlement_status"],
               "event_kind": correction_event.get("event_kind"),
               "supersedes": correction_event.get("supersedes_settlement_ref"),
               "original": original_settlement_id})

        delivered = chain.runtime.settlement_service.dispatch_outbox(
            event_id=str(correction_event.get("event_id") or ""), target_domains=["life"])
        reconciliations = chain.runtime.progress_read.list_reconciliations(activity_id=activity_id)
        late_log = chain.runtime.progress_read.list_late_terminal_evidences(activity_id=activity_id)
        record = reconciliations[-1] if reconciliations else {}
        act_after = _activity(chain, activity_id)
        check("LR-5 recorded COMPLETION_CONFLICT / RECONCILIATION_REQUIRED on A",
              len(reconciliations) == 1
              and str(record.get("status")) == "RECONCILIATION_REQUIRED"
              and str(record.get("conflict_kind")) == "COMPLETION_CONFLICT"
              and str(record.get("completion_evidence_ref")) == str(act.get("completion_evidence_ref"))
              and str(record.get("conflicting_settlement_ref")) == str(
                  corrected["settlement"]["settlement_id"]),
              {"reconciliations": reconciliations, "delivered": delivered.get("dispatched_count")})
        check("the late-terminal log names the correction, not an empty disposition",
              len(late_log) == 1 and bool(late_log[0].get("is_conflicting_correction"))
              and str(late_log[0].get("terminal_status")) == "COMPLETED", late_log)
        check("A stays COMPLETED at the same revision; the foreground stays free",
              str(act_after.get("status")) == "COMPLETED"
              and int(act_after.get("revision") or 0) == int(act.get("revision") or -1)
              and _canonical(chain).get("foreground_activity_ref") is None,
              {"status": act_after.get("status"), "revision": act_after.get("revision")})
        check("no new Activity / version / Action / Memory row was created by the correction",
              set((_canonical(chain).get("activities") or {})) == activities_before | {activity_id}
              and _versions(chain, work_artifact_id) == versions_before
              and _memory_rows(chain) == memory_before
              and int(act_after.get("revision") or 0) == int(act.get("revision") or -1),
              {"activities": sorted((_canonical(chain).get("activities") or {})),
               "versions": _versions(chain, work_artifact_id),
               "memory": _memory_rows(chain)})

        say("\n== 3. the real CLI shows the conflict (read-only) ==")
        code, payload = _run_cli(chain.instance)
        shown = (payload.get("reconciliations") or [])
        check("the CLI returns the owner's reconciliation and A's completion evidence",
              code == 0 and len(shown) == 1
              and str(shown[0].get("completion_evidence_ref")) == str(
                  act.get("completion_evidence_ref"))
              and str(shown[0].get("status")) == "RECONCILIATION_REQUIRED",
              {"code": code, "payload": payload})

        say("\n== 4. the same correction delivered again changes nothing ==")
        before_replay = (len(chain.runtime.progress_read.list_reconciliations(activity_id=activity_id)),
                         _memory_rows(chain), _versions(chain, work_artifact_id))
        chain.runtime.settlement_service.dispatch_outbox(
            event_id=str(correction_event.get("event_id") or ""), target_domains=["life"])
        after_replay = (len(chain.runtime.progress_read.list_reconciliations(activity_id=activity_id)),
                        _memory_rows(chain), _versions(chain, work_artifact_id))
        check("re-delivery is an idempotent replay: same one conflict, no new row/version",
              before_replay == after_replay, {"before": before_replay, "after": after_replay})

        say("\n== 5. named negative controls (all refused before any effect) ==")
        control = _create_control_artifact(chain, work_artifact_id)
        b = _b_action_and_settlement(chain, control)
        baseline = _snapshot(chain, work_artifact_id, activity_id)

        unknown = chain.runtime.progress_coordinator.ingest_progress_evidence({
            "event_id": "scor:%s" % hashlib.sha256(b"unknown-correction").hexdigest()[:16],
            "settlement_ref": "rset:%s" % hashlib.sha256(b"unknown-settlement").hexdigest()[:16],
            "action_id": action_id, "activity_ref": activity_id,
            "settlement_status": "CONFLICT", "outcome_kind": "UNKNOWN_EFFECT"})
        check("an event the AR-1 owner never stored is refused by name",
              str(unknown.get("disposition")) == "SETTLEMENT_EVENT_NOT_FOUND_IN_AUTHORITY",
              unknown)

        stored_b = dict(chain.runtime.settlement_read.get_outbox_event(b["event_id"]) or {})
        forged = dict(stored_b)
        forged["activity_ref"] = activity_id
        mispointed = chain.runtime.progress_coordinator.ingest_progress_evidence(forged)
        check("B's real settled event re-pointed at A is refused (owner says it is B's)",
              str(mispointed.get("disposition")) == "SETTLEMENT_ACTIVITY_MISMATCH",
              {"disposition": mispointed.get("disposition"), "b_event": b["event_id"]})

        # A fresh normalized correction (the LR-5 consumer contract, not a stored AR-1 event)
        # that supersedes a settlement which is not on A's completion chain.
        import activity_progress_completion_lr5 as lr5

        unrelated = lr5.LifeProgressEvidence(
            progress_evidence_id="pevid:%s" % hashlib.sha256(b"unrelated-pevid").hexdigest()[:16],
            source_kind=lr5.SOURCE_KIND_CORRECTION_EVENT,
            source_ref="audit:unrelated-correction:1",
            authority_domain="result_settlement_authority",
            epistemic_kind=lr5.EPISTEMIC_CONFLICTED_CLAIM,
            settlement_ref="rset:%s" % hashlib.sha256(b"unrelated-new").hexdigest()[:16],
            supersedes_settlement_ref="rset:%s" % hashlib.sha256(
                b"unrelated-settlement").hexdigest()[:16],
            settlement_status=lr5.SETTLEMENT_STATUS_CONFLICT,
            outcome_kind=lr5.OUTCOME_UNKNOWN_EFFECT,
            activity_id=activity_id,
            target_ref=str(work_artifact_id),
            occurred_at=late_observed_at, observed_at=late_observed_at,
            recorded_at=late_observed_at,
            idempotency_key="idem_pevid:unrelated-correction:1")
        refused = chain.runtime.progress_coordinator.ingest_progress_evidence(unrelated)
        check("a correction that supersedes an unrelated settlement is refused by name",
              str(refused.get("disposition")) == "CORRECTION_TARGETS_UNRELATED_COMPLETION_EVIDENCE",
              refused)

        final = _snapshot(chain, work_artifact_id, activity_id)
        check("A/B/foreground are byte-identical after every refusal",
              final == baseline,
              {"baseline": baseline, "final": final,
               "b_settlement": b["settlement_id"], "b_status": b["status"]})

        say("\n== 6. close our own chain, then a NEW process reopens the same root ==")
        close_report = restart.close_chain(chain)
        check("our owner stopped in the production order (drain + its own socket guard)",
              close_report.get("closed") is True
              and close_report.get("service_stopped") is True
              and close_report.get("service_drained") is True
              and str(close_report.get("service_socket_handling") or "") in {
                  "STALE_SOCKET_REMOVED", "NO_SOCKET", "ABSENT", "CLEAN"},
              close_report)
    finally:
        try:
            chain.runtime.close()
        except Exception:
            pass

    report_path = root / "late-correction-reopen.json"
    command = [sys.executable, "-B", str(Path(__file__).resolve()),
               "--phase", "reopen", "--root", str(root),
               "--work", str(work_artifact_id), "--activity", str(activity_id),
               "--correction-event", str(correction_event.get("event_id") or ""),
               "--report", str(report_path)]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=900)
    print(completed.stdout[-4000:])
    if completed.returncode != 0:
        print(completed.stderr[-2000:])
    try:
        reopened = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        reopened = {}
    check("the new process exits 0 and finds A COMPLETED with exactly one conflict",
          completed.returncode == 0 and reopened.get("ok") is True
          and reopened.get("status") == "COMPLETED"
          and int(reopened.get("reconciliations") or 0) == 1
          and bool(reopened.get("completion_evidence_ref"))
          and reopened.get("foreground_activity_ref") is None,
          {"exit": completed.returncode, "report": reopened})
    check("the new process re-delivers the same correction with no new conflict/action/Memory",
          reopened.get("replay_reconciliations") == 1
          and reopened.get("replay_versions") == reopened.get("versions")
          and reopened.get("replay_memory") == reopened.get("memory"),
          reopened)
    check("same correction idempotent in the fresh process (cli still shows one conflict)",
          reopened.get("cli_code") == 0 and int(reopened.get("cli_reconciliations") or 0) == 1,
          reopened)

    return _summary()


def _snapshot(chain, work: str, activity_id: str) -> dict:
    """A compact, comparable read of A/B/foreground before and after a refusal."""
    canonical = _canonical(chain)
    act = _activity(chain, activity_id)
    return {
        "status": act.get("status"),
        "revision": act.get("revision"),
        "completed_evidence": act.get("completion_evidence_ref"),
        "activities": sorted(canonical.get("activities") or {}),
        "foreground": canonical.get("foreground_activity_ref"),
        "versions_a": _versions(chain, work),
        "reconciliations": len(chain.runtime.progress_read.list_reconciliations(
            activity_id=activity_id)),
        "late_terminal": len(chain.runtime.progress_read.list_late_terminal_evidences(
            activity_id=activity_id)),
        "memory": _memory_rows(chain),
    }


def _run_cli(instance: Path) -> tuple[int, dict]:
    command = [sys.executable, "-B", str(ROOT / "scripts" / "activity_outcome_cli.py"),
               "corrections", "--instance", str(instance)]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
    try:
        payload = json.loads(completed.stdout or "{}")
    except ValueError:
        payload = {"error": "CLI_OUTPUT_NOT_JSON", "stdout": completed.stdout[-600:],
                   "stderr": completed.stderr[-600:]}
    return int(completed.returncode), payload


# ---------------------------------------------------------------------------------------------
# the reopen phase (a separate process; same root, same owners)
# ---------------------------------------------------------------------------------------------


def reopen_phase(root: Path, work: str, activity_id: str, correction_event: str,
                 report: Path) -> int:
    say("== reopen: same root, new process ==")
    chain = _open_chain(root, work)
    try:
        act = _activity(chain, activity_id)
        reconciliations = chain.runtime.progress_read.list_reconciliations(
            activity_id=activity_id)
        versions = _versions(chain, work)
        memory = _memory_rows(chain)
        chain.runtime.settlement_service.dispatch_outbox(
            event_id=correction_event, target_domains=["life"])
        replay_reconciliations = len(chain.runtime.progress_read.list_reconciliations(
            activity_id=activity_id))
        replay_versions = _versions(chain, work)
        replay_memory = _memory_rows(chain)
        cli_code, cli = _run_cli(chain.instance)
        payload = {
            "ok": True,
            "status": act.get("status"),
            "revision": act.get("revision"),
            "completion_evidence_ref": act.get("completion_evidence_ref"),
            "reconciliations": len(reconciliations),
            "reconciliation_id": (reconciliations[-1]["reconciliation_id"]
                                  if reconciliations else None),
            "conflicting_evidence_ref": (reconciliations[-1]["conflicting_evidence_ref"]
                                         if reconciliations else None),
            "foreground_activity_ref": _canonical(chain).get("foreground_activity_ref"),
            "versions": versions, "replay_versions": replay_versions,
            "memory": memory, "replay_memory": replay_memory,
            "replay_reconciliations": replay_reconciliations,
            "cli_code": cli_code,
            "cli_reconciliations": len(cli.get("reconciliations") or ()),
        }
        report.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1),
                          encoding="utf-8")
        print("REOPEN", json.dumps(payload, ensure_ascii=False, sort_keys=True)[:1200])
        return 0 if (payload["status"] == "COMPLETED" and payload["reconciliations"] == 1
                     and replay_reconciliations == 1) else 1
    finally:
        restart.close_chain(chain)


def _summary() -> int:
    failed = [name for name, ok, _detail in _CHECKS if not ok]
    print("\n%d checks, %d failed" % (len(_CHECKS), len(failed)))
    if failed:
        for name in failed:
            print("  FAILED:", name)
        return 1
    print("RESULT: OK -- the late correction is visible, COMPLETED never reopened")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", default="live", choices=("live", "reopen"))
    parser.add_argument("--root")
    parser.add_argument("--work")
    parser.add_argument("--activity")
    parser.add_argument("--correction-event")
    parser.add_argument("--report")
    args = parser.parse_args(argv)
    if args.phase == "reopen":
        if not (args.root and args.work and args.activity and args.report):
            parser.error("reopen needs --root --work --activity --report")
        return reopen_phase(Path(args.root), args.work, args.activity,
                            str(args.correction_event or ""), Path(args.report))

    root = restart.prepare_fixture()
    import one_day_life as odl

    data = odl.read_instance(root / "instance")
    work = str(data["artifact_id"])
    print("root:", root)
    print("work A:", work)
    return live_phase(root, work)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Zero-model smoke for the round-scoped artifact-read memo.

What is real here
-----------------
* a real ``lifesupply.service.life_supply.LifeSupplyService`` on its own Unix socket;
* a real ``LifeArtifactPort`` (the production transport) over that socket;
* the real ``ArtifactContinuationSource`` and ``CandidateMaterializer``;
* the production ``LifeRuntimeProduction`` assembly and the real
  ``AutonomousLifeCycle.materials()`` read seam.

What is not here
----------------
No model provider is configured or called.  ``product_model_calls`` is asserted to be
0.  The smoke does not claim natural behaviour, a G1 scenario or a 24h run.

It measures the exact duplicate reads one wake makes today (the material check asks
for the continuable work, the candidate build refreshes and materialises the same
owner, and the round's second selection asks again), then repeats the same sequence
inside one round with the memo.  The candidate refs/order must be identical, the
owner calls must go down, and a write inside the round must invalidate the memo.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "world" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

if os.name != "posix":
    print(json.dumps({"status": "SKIPPED", "reason": "POSIX-only owner/socket smoke"}))
    raise SystemExit(0)

import artifact_continuation_source as acs  # noqa: E402
import candidate_sources_ag0 as ag0  # noqa: E402
import life_artifact_port as lap  # noqa: E402

SUBJECT = "chiyo"
OBSERVED_AT = "2026-10-24T09:00:00+08:00"
#: Distinctive bodies: any appearance outside the owner would make the "no body text in a
#: candidate/validation payload" claim false.
SECRET_ARCHIVE_BODY = "归档前的正文-不得进入提示"
SECRET_COMMIT_OLD = "提交前的正文-不得进入提示"
SECRET_COMMIT_NEW = "操作者提交的新正文-不得进入提示"
SECRET_TOMBSTONE_BODY = "墓碑前的正文-不得进入提示"
SECRET_READING_BODY = "阅读材料正文-不得随拒绝信息泄漏"


def _owner_call(socket_path: Path, request: dict, *, timeout: float = 20.0) -> dict:
    body = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(str(socket_path))
        client.sendall(body + b"\n")
        line = client.makefile("rb").readline(1 << 20)
    if not line:
        raise RuntimeError("owner closed the connection without an answer")
    answer = json.loads(line.decode("utf-8"))
    if not isinstance(answer, dict):
        raise RuntimeError("owner answer is not an object")
    return answer


class Owner:
    def __init__(self, root: Path) -> None:
        from lifesupply.service.life_supply import LifeSupplyService, LifeSupplySocketServer
        from lifesupply.service.switches import FeatureSwitches

        self.root = root
        self.socket_path = root / "life-supply.sock"
        uid = os.geteuid()
        self.service = LifeSupplyService(root / "data", operator_uids={uid}, service_uids={uid},
                                         switches=FeatureSwitches.all_on(),
                                         writer_instance="wake-cost-smoke")
        self.server = LifeSupplySocketServer(str(self.socket_path), self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def call(self, request: dict) -> dict:
        return _owner_call(self.socket_path, request)

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.service.close()
        self.thread.join(timeout=5)


def _issue(owner: Owner, *, capability: str, scope: str, target: str,
           authority_scope: str, operation_id: str) -> str:
    answer = owner.call({"op": "issue_grant", "grant": {
        "subject": SUBJECT, "capability": capability, "scope": scope, "target": target,
        "authority_scope": authority_scope, "use_mode": "REUSABLE",
        "operation_id": operation_id}})
    if answer.get("status") != "ALLOW":
        raise RuntimeError("grant refused: %r" % (answer,))
    return str(answer["grant_id"])


def _provision(owner: Owner) -> tuple[str, str]:
    workspace_grant = owner.call({"op": "issue_grant", "grant": {
        "subject": SUBJECT, "capability": "PROVISION_PERSONAL_WORKSPACE",
        "scope": "workspace:personal", "target": "workspace:%s" % SUBJECT,
        "authority_scope": "life-supply.workspace.v1", "use_mode": "ONE_SHOT",
        "operation_id": "issue:workspace:%s" % SUBJECT}})
    if workspace_grant.get("status") != "ALLOW":
        raise RuntimeError("workspace grant refused: %r" % (workspace_grant,))
    answer = owner.call({"op": "provision_workspace",
                         "grant_id": str(workspace_grant["grant_id"]),
                         "operation_key": "wake-cost:workspace:%s" % SUBJECT,
                         "workspace": {"subject_id": SUBJECT}})
    if answer.get("status") != "OK":
        raise RuntimeError("workspace provision failed: %r" % (answer,))
    workspace_id = str(answer["workspace"]["workspace_id"])
    artifact_grant = _issue(owner, capability="COMMIT_MANAGED_ARTIFACT",
                            scope="artifact:personal",
                            target="workspace:%s" % workspace_id,
                            authority_scope="life-supply.artifact.v1",
                            operation_id="issue:artifact:%s" % SUBJECT)
    return workspace_id, artifact_grant


def _create(port: lap.LifeArtifactPort, workspace_id: str, grant_id: str,
            title: str, content: str, tag: str) -> str:
    outcome = port.create_artifact(
        workspace_id=workspace_id, title=title, content=content, grant_id=grant_id,
        operation_key=lap.operation_key_for("wake-cost-smoke", tag, SUBJECT))
    if not outcome.applied:
        raise RuntimeError("artifact create failed: %s" % (outcome.to_dict(),))
    return str(outcome.artifact_id)


def _exercise(port: lap.LifeArtifactPort, *, memoized: bool):
    """The cycle's own read pattern for one round, on one port."""

    if memoized:
        port.begin_round(OBSERVED_AT)
    try:
        source = acs.ArtifactContinuationSource(port, subject_id=SUBJECT)
        registry = ag0.CandidateSourceRegistry({acs.SOURCE_PERSONAL_OPPORTUNITY: source})
        capability = ag0.CandidateProjectionGuard.issue_capability(
            caller_module="ag0_test_harness")
        materializer = ag0.CandidateMaterializer(registry, capability=capability,
                                                 record_audit=False)
        status = source.availability_status()
        cset = materializer.build_candidate_set(observed_at=OBSERVED_AT)
        # materials() asks for the selected work ...
        rows = port.list_artifacts()
        active = [row for row in rows if str(row.get("lifecycle")) == "ACTIVE"]
        point = port.resume_point(str(active[0]["artifact_id"])) if active else None
        # ... and evaluate_once asks again before the Decision.
        rows_again = port.list_artifacts()
        point_again = (port.resume_point(str(rows_again[0]["artifact_id"]))
                       if rows_again else None)
        return {
            "availability": status.get("reason_code"),
            "candidate_refs": list(cset.candidate_refs),
            "resume_heads": [getattr(point, "head_version_id", None),
                             getattr(point_again, "head_version_id", None)],
        }
    finally:
        if memoized:
            port.end_round()


def _candidate_id_for(reader, artifact_id: str) -> str:
    """The OPEN work candidate whose source ref names exactly this artifact."""

    expected = "artifact:%s" % artifact_id
    for row in reader.list_open_candidates():
        if str(row.get("source_ref") or "") == expected:
            return str(row["candidate_id"])
    raise RuntimeError("no personal-opportunity candidate for %s" % artifact_id)


def _formal_recheck_probe(cycle, owner: "Owner", workspace_id: str, grant_id: str) -> dict:
    """External archive / commit / tombstone against the production use-time re-check.

    One round first builds the candidate set (which memoises the owner's list/head answers
    for this round only).  An independent port -- a second real client, not the cycle's --
    then archives one work, commits a new version of another and tombstones a third.  The
    production pre-adoption boundary (``_pause_gate_hook``) drops the memo, and the formal
    ``validate_candidate`` calls that every adoption runs must observe the new owner state.
    """

    setup = lap.LifeArtifactPort(str(owner.socket_path), SUBJECT)
    archive_id = _create(setup, workspace_id, grant_id, "归档对象", SECRET_ARCHIVE_BODY,
                         "recheck-archive")
    commit_id = _create(setup, workspace_id, grant_id, "提交对象", SECRET_COMMIT_OLD,
                        "recheck-commit")
    tombstone_id = _create(setup, workspace_id, grant_id, "墓碑对象", SECRET_TOMBSTONE_BODY,
                           "recheck-tombstone")
    reading_id = _create(setup, workspace_id, grant_id, "阅读材料", SECRET_READING_BODY,
                         "recheck-reading")

    port = cycle.artifact_port
    reader = cycle.runtime.candidate_read
    result: dict = {"artifacts": {"archive": archive_id, "commit": commit_id,
                                  "tombstone": tombstone_id, "reading": reading_id}}
    cycle.begin_round_reads(OBSERVED_AT)
    try:
        cycle.materials(now=OBSERVED_AT)
        candidate_set = reader.build_candidate_set(observed_at=OBSERVED_AT)
        candidate_ids = {
            "archive": _candidate_id_for(reader, archive_id),
            "commit": _candidate_id_for(reader, commit_id),
            "tombstone": _candidate_id_for(reader, tombstone_id),
        }
        result["candidate_set_refs"] = len(candidate_set.get("candidate_refs") or [])
        calls_before = int(port.stats()["inner_call_count"])

        operator = lap.LifeArtifactPort(str(owner.socket_path), SUBJECT)
        archive_view = operator.read_artifact(archive_id)
        archived = operator.archive_artifact(
            artifact_id=archive_id,
            expected_revision=int((archive_view.get("artifact") or {}).get("revision") or 0),
            grant_id=grant_id,
            operation_key=lap.operation_key_for("wake-cost-smoke", "recheck-archive-op",
                                                SUBJECT))
        if not archived.applied:
            raise RuntimeError("external archive refused: %s" % (archived.to_dict(),))

        commit_head = operator.read_head(commit_id) or {}
        committed = operator.commit_version(
            artifact_id=commit_id, expected_head=str(commit_head.get("version_id")),
            content=SECRET_COMMIT_NEW, grant_id=grant_id,
            operation_key=lap.operation_key_for("wake-cost-smoke", "recheck-commit-op",
                                                SUBJECT))
        if not committed.applied:
            raise RuntimeError("external commit refused: %s" % (committed.to_dict(),))

        tombstone_view = operator.read_artifact(tombstone_id)
        tombstoned = owner.call({
            "op": "tombstone", "grant_id": grant_id,
            "operation_key": lap.operation_key_for("wake-cost-smoke", "recheck-tombstone-op",
                                                   SUBJECT),
            "artifact": {"subject_id": SUBJECT, "artifact_id": tombstone_id,
                         "expected_revision": int((tombstone_view.get("artifact") or {})
                                                  .get("revision") or 0)},
        })
        if tombstoned.get("status") != "OK":
            raise RuntimeError("external tombstone refused: %r" % (tombstoned,))

        # The Decision's request has returned: the production pre-adoption boundary.
        cycle._pause_gate_hook(None)
        verdicts = {
            name: reader.validate_candidate(candidate_ids[name], observed_at=OBSERVED_AT)
            for name in ("archive", "commit", "tombstone")
        }
        calls_after = int(port.stats()["inner_call_count"])
    finally:
        cycle.end_round_reads()

    import agency_cognition_adapter as aca

    rendered = []
    for verdict in verdicts.values():
        rendered.append(json.dumps(verdict.get("candidate") or {}, ensure_ascii=False,
                                   sort_keys=True))
        rendered.append(json.dumps(
            aca.ProductionAgencyCognitionAdapter._compact_candidate(
                verdict.get("candidate") or {}), ensure_ascii=False))
    all_payload = "\n".join(rendered)
    bodies = (SECRET_ARCHIVE_BODY, SECRET_COMMIT_OLD, SECRET_COMMIT_NEW,
              SECRET_TOMBSTONE_BODY, SECRET_READING_BODY)
    archive_after = operator.read_artifact(archive_id)
    commit_head_after = operator.read_head(commit_id) or {}
    result.update({
        "archive": {"valid": bool(verdicts["archive"].get("valid")),
                    "reason": verdicts["archive"].get("reason_code"),
                    "lifecycle": (archive_after.get("artifact") or {}).get("lifecycle"),
                    "version_number": (archive_after.get("head") or {}).get("version_number")},
        "commit": {"valid": bool(verdicts["commit"].get("valid")),
                   "reason": verdicts["commit"].get("reason_code"),
                   "version_number": commit_head_after.get("version_number")},
        "tombstone": {"valid": bool(verdicts["tombstone"].get("valid")),
                      "reason": verdicts["tombstone"].get("reason_code")},
        "owner_calls_during_recheck": calls_after - calls_before,
        "fresh_reads_at_recheck": calls_after > calls_before,
        "bodies_absent_from_payloads": not any(body in all_payload for body in bodies),
        "no_new_versions_from_validation": (
            (archive_after.get("head") or {}).get("version_number") == 1
            and commit_head_after.get("version_number") == 2),
        "reading_id": reading_id,
    })
    return result


def _reading_precommit_probe(cycle, owner: "Owner", reading_id: str, doc_id: str,
                             grant_id: str) -> dict:
    """After the private response, a moved material head must block the private write.

    The re-check is the production ``_recheck_reading_sources``.  The read made before the
    (simulated) response wait is in the round memo; the operator commits a new material
    version; the re-check must ask the owner again and refuse, so the private-items
    document is not written against the old page text.
    """

    import autonomous_life_cycle as alc

    operator = lap.LifeArtifactPort(str(owner.socket_path), SUBJECT)
    port = cycle.artifact_port
    cycle.begin_round_reads(OBSERVED_AT)
    try:
        before = port.read_artifact(reading_id)
        old_revision = str((before.get("head") or {}).get("version_id") or "")
        head = operator.read_head(reading_id) or {}
        committed = operator.commit_version(
            artifact_id=reading_id, expected_head=str(head.get("version_id")),
            content=SECRET_COMMIT_NEW, grant_id=grant_id,
            operation_key=lap.operation_key_for("wake-cost-smoke", "recheck-reading-op",
                                                SUBJECT))
        if not committed.applied:
            raise RuntimeError("external reading commit refused: %s" % (committed.to_dict(),))
        doc_head = operator.read_head(doc_id) or {}
        raised = None
        try:
            cycle._recheck_reading_sources(
                reading_id=reading_id, revision=old_revision, doc_id=doc_id,
                doc_head_version=doc_head.get("version_id"),
                source_id="artifact:%s" % reading_id, before={})
        except alc.ReadingResponseFailed as exc:
            raised = str(exc)
        doc_head_after = operator.read_head(doc_id) or {}
    finally:
        cycle.end_round_reads()
    new_head = operator.read_head(reading_id) or {}
    return {
        "refused": raised is not None,
        "reason": raised or "",
        "material_head_moved": str(new_head.get("version_id") or "") != old_revision,
        "private_items_unchanged": (str(doc_head_after.get("version_id") or "")
                                    == str(doc_head.get("version_id") or "")),
        "secret_absent_from_refusal": SECRET_READING_BODY not in (raised or ""),
    }


def _revoked_grant_probe(cycle, owner: "Owner", workspace_id: str, grant_id: str) -> dict:
    """A revoked write grant must refuse a command even though the round still has its read.

    Commands are never cached: the memo is dropped on both sides of every command, and the
    owner -- not a remembered read -- decides whether the write may happen.
    """

    setup = lap.LifeArtifactPort(str(owner.socket_path), SUBJECT)
    target_id = _create(setup, workspace_id, grant_id, "撤权对象", "撤权前的正文",
                        "recheck-revoke")
    port = cycle.artifact_port
    cycle.begin_round_reads(OBSERVED_AT)
    try:
        cached = port.read_artifact(target_id)
        head = port.read_head(target_id) or {}
        grant_view = owner.call({"op": "get_grant", "grant_id": grant_id})
        row = grant_view.get("grant") or {}
        revoked = owner.call({
            "op": "revoke_grant", "grant_id": grant_id,
            "expected_revision": int(row.get("revision") or 0),
            "operation_id": "revoke:wake-cost:%s" % SUBJECT,
        })
        attempt = port.commit_version(
            artifact_id=target_id, expected_head=str(head.get("version_id")),
            content="撤权后的写入", grant_id=grant_id,
            operation_key=lap.operation_key_for("wake-cost-smoke", "recheck-revoked-write",
                                                SUBJECT))
        write_invalidations = int(port.stats()["write_invalidations"])
    finally:
        cycle.end_round_reads()
    fresh = lap.LifeArtifactPort(str(owner.socket_path), SUBJECT)
    after = fresh.read_head(target_id) or {}
    return {
        "cached_read_served": bool(cached.get("content") == "撤权前的正文"),
        "revoke_status": revoked.get("status"),
        "write_applied": bool(attempt.applied),
        "write_status": attempt.status,
        "head_unchanged": (str(after.get("version_id") or "")
                           == str(head.get("version_id") or "")),
        "command_bypassed_the_memo": write_invalidations >= 1,
    }


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="wake-candidate-cost-smoke-", dir="/tmp"))
    owner = Owner(root)
    result: dict = {"root": str(root), "observed_at": OBSERVED_AT,
                    "product_model_calls": 0}
    try:
        workspace_id, grant_id = _provision(owner)
        setup_port = lap.LifeArtifactPort(str(owner.socket_path), SUBJECT)
        work_id = _create(setup_port, workspace_id, grant_id, "作品一", "第一段。", "work-1")
        sibling_id = _create(setup_port, workspace_id, grant_id, "作品二", "第二段。", "work-2")

        # -- baseline: the same round with no memo --------------------------------
        raw = lap.LifeArtifactPort(str(owner.socket_path), SUBJECT)
        raw.call_count = 0
        baseline = _exercise(raw, memoized=False)
        baseline_calls = raw.call_count

        # -- memoized: the same round with the port wrapper ------------------------
        from wake_candidate_cost import RoundCachedArtifactPort
        inner = lap.LifeArtifactPort(str(owner.socket_path), SUBJECT)
        inner.call_count = 0
        cached = RoundCachedArtifactPort(inner)
        cached_result = _exercise(cached, memoized=True)
        cached_calls = inner.call_count
        memo_stats = cached.stats()

        result["surface"] = (
            "real LifeSupplyService + real LifeArtifactPort + real "
            "ArtifactContinuationSource + real CandidateMaterializer")
        result["baseline"] = {"owner_calls": baseline_calls,
                              "candidate_refs": baseline["candidate_refs"],
                              "resume_heads": baseline["resume_heads"]}
        result["memoized"] = {"owner_calls": cached_calls,
                              "calls_saved": baseline_calls - cached_calls,
                              "candidate_refs": cached_result["candidate_refs"],
                              "resume_heads": cached_result["resume_heads"],
                              "memo": memo_stats}
        result["candidate_refs_identical"] = (
            baseline["candidate_refs"] == cached_result["candidate_refs"])
        result["resume_heads_identical"] = (
            baseline["resume_heads"] == cached_result["resume_heads"])

        # -- the production cycle reads through the same wrapper -------------------
        import autonomous_life_cycle as alc
        import life_integration_ag2 as ag2
        import life_runtime_production as lrp

        switches = ag2.LifeRuntimeKillSwitches(
            life_runtime_enabled=True, agency_enabled=True,
            action_execution_enabled=False, proactive_enabled=False)
        install_root = root / "life"
        state_root = install_root / "state"
        state_root.mkdir(parents=True, exist_ok=True)
        os.chmod(install_root, 0o700)
        os.chmod(state_root, 0o700)
        runtime = lrp.build_production_life_runtime(
            root / "life" / "state", kill_switches=switches,
            install_root=root / "life")
        runtime_port = lap.LifeArtifactPort(str(owner.socket_path), SUBJECT)
        cycle = alc.AutonomousLifeCycle(
            runtime, artifact_port=runtime_port, enabled=True, max_rounds_per_day=48,
            env={"AUTONOMOUS_LIFE_ENABLED": "true"}, subject_id=SUBJECT,
            workspace_id=workspace_id, artifact_grant_id=grant_id,
            work_artifact_id=work_id)
        cycle.begin_round_reads(OBSERVED_AT)
        try:
            material = cycle.materials(now=OBSERVED_AT)
            cycle_set = runtime.candidate_read.build_candidate_set(observed_at=OBSERVED_AT)
        finally:
            cycle.end_round_reads()
        cycle_rows = runtime.candidate_read.list_open_candidates()
        personal_rows = [row for row in cycle_rows
                         if row.get("source_kind") == acs.SOURCE_PERSONAL_OPPORTUNITY]

        def _is_closeout(row):
            return any(str(ref).startswith("closeout:")
                       for ref in (row.get("target_refs") or []))

        work_refs = [str(row["candidate_id"]) for row in personal_rows
                     if not _is_closeout(row)]
        closeout_refs = [str(row["candidate_id"]) for row in personal_rows
                         if _is_closeout(row)]
        result["cycle"] = {
            "artifact_port_wrapped": isinstance(cycle.artifact_port, RoundCachedArtifactPort),
            # The AG-0 source must read through the same round-scoped port, or the candidate
            # build would bypass the memo (and its live re-check would be untested).
            "artifact_source_uses_round_memo": (
                getattr(cycle, "artifact_source", None) is not None
                and getattr(cycle.artifact_source, "port", None) is cycle.artifact_port),
            "has_material": bool(material.get("has_material")),
            "continuable_artifact": bool(material.get("continuable_artifact")),
            "candidate_refs": list(cycle_set.get("candidate_refs") or []),
            "work_opportunity_refs": work_refs,
            "closeout_opportunity_refs": closeout_refs,
            "candidate_refs_match_baseline": work_refs == baseline["candidate_refs"],
            "round_memo": cycle.artifact_port.stats(),
            "cognition_calls": int(getattr(
                getattr(runtime, "cognition_adapter", None), "call_count", 0) or 0),
        }

        # -- a real early-exit round through the public entry point ------------------
        # ``evaluate_once`` must close its own round; the runner's ``finally`` is not the
        # only guarantee for a probe or tool that calls the cycle directly.
        enabled_before = cycle.enabled
        cycle.enabled = False
        try:
            early = cycle.evaluate_once(trigger_ref="evt:smoke:disabled",
                                        observed_at=OBSERVED_AT)
            early_memo = cycle.artifact_port.stats()
        finally:
            cycle.enabled = enabled_before
        result["early_exit"] = {
            "outcome": early.get("outcome"),
            "round_memo_active": bool(early_memo["active"]),
            "rounds_opened": int(early_memo["rounds_opened"]),
        }

        # -- real owner + real cycle: external changes vs the use-time re-checks -----
        recheck = _formal_recheck_probe(cycle, owner, workspace_id, grant_id)
        result["formal_recheck"] = recheck
        result["reading_precommit"] = _reading_precommit_probe(
            cycle, owner, recheck["reading_id"], work_id, grant_id)
        runtime.close()

        # -- a write inside the round invalidates the memo -------------------------
        write_port = lap.LifeArtifactPort(str(owner.socket_path), SUBJECT)
        wrapped_write = RoundCachedArtifactPort(write_port)
        wrapped_write.begin_round(OBSERVED_AT)
        before = wrapped_write.read_head(work_id)
        head = write_port.read_head(work_id) or {}
        commit = wrapped_write.commit_version(
            artifact_id=work_id, expected_head=str(head.get("version_id")),
            content="第三段。", grant_id=grant_id,
            operation_key=lap.operation_key_for("wake-cost-smoke", "work-1-v2", SUBJECT))
        after = wrapped_write.read_head(work_id)
        wrapped_write.end_round()
        result["invalidation"] = {
            "commit_applied": bool(commit.applied),
            "before_head": (before or {}).get("version_id"),
            "after_head": (after or {}).get("version_id"),
            "fresh_after_write": bool(
                before and after and before.get("version_id") != after.get("version_id")),
            "write_invalidations": wrapped_write.stats()["write_invalidations"],
        }

        # -- a missing owner still fails closed (no candidate is invented) ---------
        missing_port = RoundCachedArtifactPort(
            lap.LifeArtifactPort(str(root / "missing-owner.sock"), SUBJECT))
        missing_port.begin_round(OBSERVED_AT)
        try:
            missing_source = acs.ArtifactContinuationSource(missing_port, subject_id=SUBJECT)
            missing_registry = ag0.CandidateSourceRegistry(
                {acs.SOURCE_PERSONAL_OPPORTUNITY: missing_source})
            missing_capability = ag0.CandidateProjectionGuard.issue_capability(
                caller_module="ag0_test_harness")
            missing_materializer = ag0.CandidateMaterializer(
                missing_registry, capability=missing_capability, record_audit=False)
            missing_status = missing_source.availability_status()
            missing_set = missing_materializer.build_candidate_set(observed_at=OBSERVED_AT)
        finally:
            missing_port.end_round()
        result["missing_source"] = {
            "availability": missing_status.get("reason_code"),
            "candidate_refs": list(missing_set.candidate_refs),
            "failed_closed": not missing_set.candidate_refs,
        }

        # -- last: revoking the write grant must not be papered over by a cached read ----
        # This spends the shared grant, so it deliberately runs after every other write.
        result["revoked_grant"] = _revoked_grant_probe(cycle, owner, workspace_id, grant_id)

        result["not_verified"] = [
            "natural behaviour or a G1 scenario",
            "real product model adoption",
            "a 24h run",
            "a change that lands between the first read of a wake and the Decision is still "
            "part of that wake's snapshot; only the explicit use-time re-checks (pre-adoption "
            "freshness, reading pre-commit) are promised to observe it",
        ]
        result["ok"] = bool(
            result["candidate_refs_identical"] and result["resume_heads_identical"]
            and cached_calls < baseline_calls
            and result["cycle"]["candidate_refs_match_baseline"]
            and result["cycle"]["artifact_source_uses_round_memo"]
            and result["cycle"]["cognition_calls"] == 0
            and result["early_exit"]["outcome"] == "DISABLED"
            and result["early_exit"]["round_memo_active"] is False
            and result["invalidation"]["fresh_after_write"]
            and result["missing_source"]["failed_closed"]
            and result["formal_recheck"]["archive"]["valid"] is False
            and result["formal_recheck"]["archive"]["lifecycle"] == "ARCHIVED"
            and result["formal_recheck"]["commit"]["valid"] is False
            and result["formal_recheck"]["commit"]["reason"] == "ARTIFACT_HEAD_ADVANCED"
            and result["formal_recheck"]["tombstone"]["valid"] is False
            and result["formal_recheck"]["fresh_reads_at_recheck"]
            and result["formal_recheck"]["bodies_absent_from_payloads"]
            and result["formal_recheck"]["no_new_versions_from_validation"]
            and result["reading_precommit"]["refused"]
            and result["reading_precommit"]["material_head_moved"]
            and result["reading_precommit"]["private_items_unchanged"]
            and result["reading_precommit"]["secret_absent_from_refusal"]
            and result["revoked_grant"]["write_applied"] is False
            and result["revoked_grant"]["head_unchanged"]
            and result["revoked_grant"]["command_bypassed_the_memo"])
    finally:
        owner.stop()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())

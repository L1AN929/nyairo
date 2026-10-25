#!/usr/bin/env python3
"""零模型的真实 owner 冒烟：搁置事项在安静门之前成为候选。

它做什么（全部在本机、0 产品模型、0 外网；只起两个隔离 Unix socket）：

1. 在全新 ``/tmp`` 根里装配**真实** LR-2 Activity owner（lease + canonical store + command
   service）与**真实** LR-4 WaitingCoordinator（condition/agenda/eligibility 三个 store）。
   Decision/Adoption 两个读端口是唯一替身：它们只回答 admission verifier 真正读取的字段，
   用于让每次 LR-2 转换仍走正式 grant / scope / fingerprint 校验。
2. **真实** ``LifeSupplyService`` 在隔离 socket 上创建一件 ManagedArtifact；活动通过
   ``subject_refs`` 绑定这件真实目标，``ResumeEligibleCandidateSource`` 用生产
   ``LifeArtifactPort.read_artifact`` 证明来源仍可读。
3. 真实 START → 真实 ``enter_waiting_with_condition``（时间条件、释放前台）→ 真实
   ``scan_due_agenda`` 在条件到期时由 LR-4 自己发出 OPEN eligibility。**没有手造资格**。
4. 真实 ``AutonomousLifeCycle`` + 真实 ``AutonomousLifeRunner.run_due``：到期检查窗里，
   搁置活动与有效资格先成为 material，AG-0 的正式 ``RESUME_ELIGIBLE`` 源产出正式候选，
   AG-1 的合法集含 ``RESUME``；供给前后 Activity/资格状态、revision 不变（不消费、不恢复）。
5. 负控：同一状态的下一个窗口仍是 ``QUIET``（0 请求）；资格过期读 0；另一前台占位读 0；
   supply owner tombstone 目标后 0 条且资格仍 OPEN；展示后 tombstone 在预采纳 ``refresh``
   拒；owner 停止后 0 条。
6. 所有自有对象在 finally 收尾；夹具根保留在唯一的 ``/tmp`` 子目录，便于复查。

Run:
    PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/parked_candidate_supply_smoke.py
"""

from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import activity_admission as adm  # noqa: E402
import activity_continuity as ac  # noqa: E402
import activity_waiting_agenda as awa  # noqa: E402
import agency_decision_ag1 as ag1  # noqa: E402
import autonomous_life_cycle as alc  # noqa: E402
import autonomous_life_runner as runner  # noqa: E402
import candidate_sources_ag0 as ag0  # noqa: E402
import life_artifact_port as lap  # noqa: E402

T0 = datetime(2026, 10, 23, 0, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(minutes=5)          # condition target reached -> eligibility emitted
T2 = T0 + timedelta(minutes=150)        # after the eligibility's own expiry (T0 + 120)
DECISION_REF = "dec:parkedsmoke"
ACTIVITY_ID = "actv:parkedsmoke"


def _iso(moment: datetime) -> str:
    return moment.isoformat()


# ---------------------------------------------------------------------------
# The only two stand-ins: the owner read ports admission verifies.
# ---------------------------------------------------------------------------


class _DecisionReader:
    def get_decision(self, decision_id):
        if decision_id != DECISION_REF:
            return None
        return {"decision_id": decision_id, "revision": 1,
                "snapshot_fingerprint": f"fp:{decision_id}"}


class _AdoptionReader:
    def __init__(self):
        self.grants: dict[str, tuple[str, str]] = {}

    def grant(self, command_id, mutation_kind, activity_ref):
        self.grants[command_id] = (mutation_kind, activity_ref)

    def get_adoption_by_id(self, adoption_id):
        command_id = adoption_id.split(":", 1)[1] if ":" in adoption_id else ""
        granted = self.grants.get(command_id)
        if granted is None:
            return None
        mutation_kind, activity_ref = granted
        return {
            "adoption_id": adoption_id,
            "decision_ref": DECISION_REF,
            "decision_revision": 1,
            "adoption_status": "APPLIED",
            "revision": 1,
            "authorized_mutation_kind": mutation_kind,
            "authorized_command_id": command_id,
            "created_activity_ref": activity_ref,
            "target_activity_ref": activity_ref,
            "authorized_activity_ref": activity_ref,
        }


class _ActionRead:
    def list_receipts(self, action_id):
        return []

    def list_actions(self, status=None):
        return []


class _ProgressPort:
    def ingest_progress_evidence(self, evidence):
        return {}


class _ScriptedDecision:
    """The Decision/adoption seam: it materializes the *production* candidate set, then NO_ACTION."""

    def __init__(self, candidate_read):
        self.candidate_read = candidate_read
        self.calls = 0
        self.last_candidate_set: dict = {}

    def run_decision_and_adoption_cycle(self, **kwargs):
        self.calls += 1
        self.last_candidate_set = self.candidate_read.build_candidate_set(
            observed_at=kwargs["observed_at"])
        record = SimpleNamespace(decision="NO_ACTION", selected_candidate_ref=None,
                                 reason_codes=[], short_rationale="scripted")
        adoption = SimpleNamespace(adoption_status="APPLIED", rejection_reason_code=None)
        return {
            "correlation_id": "corr:parkedsmoke:1",
            "decision_result": {"decision_record": record},
            "adoption_result": {"adoption_record": adoption},
            "integration_record": SimpleNamespace(activity_ref=None, integration_id="integ:1"),
            "step_execution_result": {},
        }

    def emit_experience_handoff(self, **kwargs):
        return {"dispatch_result": {"deliveries": []}, "handoff_event": None}


class _Runtime:
    def __init__(self, root, lease, store, commands, coordinator, source):
        self.state_root = Path(root)
        self.activity_read = ac.ActivityReadService(store)
        self.eligibility_store = coordinator.eligibility_store
        self.waiting_coordinator = coordinator
        self.action_read = _ActionRead()
        self.action_command = commands
        self.ag2_progress_port = _ProgressPort()
        self.kill_switches = SimpleNamespace(to_dict=lambda: {})
        self.candidate_registry = ag0.CandidateSourceRegistry(
            {ag0.SOURCE_RESUME_ELIGIBLE: source})
        capability = ag0.CandidateProjectionGuard.issue_capability(
            caller_module="candidate_materializer", namespace=ag0.NAMESPACE_PRODUCTION)
        self.candidate_materializer = ag0.CandidateMaterializer(
            self.candidate_registry,
            capability=capability,
            audit_journal=ag0.CandidateAuditJournal(
                Path(root) / "candidate_audit_journal.jsonl"),
            record_audit=False,
        )
        self.candidate_read = ag0.CandidateReadService(self.candidate_materializer)
        self.coordinator = _ScriptedDecision(self.candidate_read)

    def canonical_state(self):
        view = self.activity_read.get_current_life_view()
        return {"foreground_activity_ref": view.get("foreground_activity_ref")}


def _owners(root: Path):
    adoptions = _AdoptionReader()
    issuer = adm.build_admission_issuer(
        _DecisionReader(), adoptions, runtime_epoch="parked-smoke")
    lease = ac.ActivityAuthorityLease(root)
    lease.acquire()
    store = ac.CanonicalActivityStore(
        root, namespace=ac.NAMESPACE_ISOLATED_TEST, _admission_issuer=issuer)
    capability = lease.issue_capability(namespace=ac.NAMESPACE_ISOLATED_TEST)
    commands = ac.ActivityCommandService(store=store, lease=lease, capability=capability)
    coordinator = awa.WaitingCoordinator(
        activity_store=store, lease=lease, command_service=commands,
        namespace=ac.NAMESPACE_ISOLATED_TEST)
    return SimpleNamespace(adoptions=adoptions, lease=lease, store=store,
                           commands=commands, coordinator=coordinator)


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


class _SupplyOwner:
    """A real ``LifeSupplyService`` on its own Unix socket, stopped in ``finally``."""

    def __init__(self, root: Path) -> None:
        from lifesupply.service.life_supply import LifeSupplyService, LifeSupplySocketServer
        from lifesupply.service.switches import FeatureSwitches

        self.socket_path = root / "parked-supply.sock"
        uid = os.geteuid()
        self.service = LifeSupplyService(
            root / "supply-data", operator_uids={uid}, service_uids={uid},
            switches=FeatureSwitches.all_on(), writer_instance="parked-supply-smoke")
        self.server = LifeSupplySocketServer(str(self.socket_path), self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self._stopped = False

    def call(self, request: dict) -> dict:
        return _owner_call(self.socket_path, request)

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self.server.shutdown()
        self.server.server_close()
        self.service.close()
        self.thread.join(timeout=5)


def _supply_fixture(owner: "_SupplyOwner", subject: str) -> tuple[lap.LifeArtifactPort, str, str]:
    """Provision a workspace and create the Artifact the Activity will bind."""

    ws_grant = owner.call({"op": "issue_grant", "grant": {
        "subject": subject, "capability": "PROVISION_PERSONAL_WORKSPACE",
        "scope": "workspace:personal", "target": "workspace:%s" % subject,
        "authority_scope": "life-supply.workspace.v1", "use_mode": "ONE_SHOT",
        "operation_id": "issue:workspace:%s" % subject}})
    if ws_grant.get("status") != "ALLOW":
        raise RuntimeError("workspace grant refused: %r" % (ws_grant,))
    answer = owner.call({"op": "provision_workspace",
                         "grant_id": str(ws_grant["grant_id"]),
                         "operation_key": "parked-smoke:workspace",
                         "workspace": {"subject_id": subject}})
    if answer.get("status") != "OK":
        raise RuntimeError("workspace provision failed: %r" % (answer,))
    workspace_id = str(answer["workspace"]["workspace_id"])
    grant = owner.call({"op": "issue_grant", "grant": {
        "subject": subject, "capability": "COMMIT_MANAGED_ARTIFACT",
        "scope": "artifact:personal", "target": "workspace:%s" % workspace_id,
        "authority_scope": "life-supply.artifact.v1", "use_mode": "REUSABLE",
        "operation_id": "issue:artifact:%s" % subject}})
    if grant.get("status") != "ALLOW":
        raise RuntimeError("artifact grant refused: %r" % (grant,))
    grant_id = str(grant["grant_id"])
    port = lap.LifeArtifactPort(str(owner.socket_path), subject)
    created = port.create_artifact(
        workspace_id=workspace_id, title="搁置作品", content="一页正文", grant_id=grant_id,
        operation_key=lap.operation_key_for("parked-supply-smoke", "create", subject))
    if not created.applied:
        raise RuntimeError("artifact create failed: %s" % (created.to_dict(),))
    return port, grant_id, str(created.artifact_id)


def _tombstone(owner: "_SupplyOwner", port: lap.LifeArtifactPort,
               artifact_id: str, grant_id: str, subject: str) -> None:
    view = port.read_artifact(artifact_id)
    revision = int((view.get("artifact") or {}).get("revision") or 0)
    answer = owner.call({
        "op": "tombstone", "grant_id": grant_id,
        "operation_key": lap.operation_key_for("parked-supply-smoke", "tombstone", subject),
        "artifact": {"subject_id": subject, "artifact_id": artifact_id,
                     "expected_revision": revision},
    })
    if answer.get("status") != "OK":
        raise RuntimeError("tombstone refused: %r" % (answer,))


def _revision(owner) -> int:
    state, _ = owner.store.load_verified_state_and_journal()
    return int(state["revision"])


def _activity(owner, activity_id=ACTIVITY_ID) -> dict:
    state, _ = owner.store.load_verified_state_and_journal()
    return dict((state.get("activities") or {})[activity_id])


def _eligibilities(owner) -> dict:
    state = owner.coordinator.eligibility_store.load_state() or {}
    return dict(state.get("eligibilities") or {})


def _legal_decisions(candidate: ag0.CandidateRecord) -> list[str]:
    context = ag1.DecisionContextSnapshot(
        context_snapshot_id="dctx:parkedsmoke",
        opportunity_ref="dopp:parkedsmoke",
        candidate_set_ref="cset:parkedsmoke",
        life_frame_ref="lframe:parkedsmoke",
        captured_at=_iso(T1),
        source_revisions={"snapshot": 1},
        snapshot_fingerprint="sfp:parkedsmoke",
        current_activity_ref=None,
        current_activity_status=None,
        current_activity_revision=0,
        interruptibility=ag1.INTERRUPTIBILITY_IMMEDIATE,
    )
    capacity = ag1.AgencyCapacitySnapshot(
        capacity_snapshot_id="cap:parkedsmoke",
        captured_at=_iso(T1),
        source_revisions=["snapshot:parkedsmoke"],
        candidate_executability={candidate.candidate_id: {"executability": ag1.EXEC_NOW}},
    )
    return ag1.DecisionOutputValidator.compute_legal_decisions_for_candidate(
        candidate, context_snapshot=context, capacity_snapshot=capacity)


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="nyairo-parked-smoke-", dir="/tmp"))
    owner = _owners(root)
    supply = _SupplyOwner(root)
    subject = ag0.GLOBAL_SUBJECT_ID
    port, grant_id, artifact_id = _supply_fixture(supply, subject)
    summary: dict = {"root": str(root), "checks": {}, "limitations": [
        "eligibility was emitted by real LR4 scan_due_agenda from an operator-chosen time "
        "condition; the time itself is a fixture",
        "the Decision/adoption coordinator and the two admission read ports are stand-ins; "
        "no model was called and no real Decision was committed",
        "the resume target is a real ManagedArtifact read through the production LifeArtifactPort; "
        "the Supply owner's read path is subject-scoped, so a revoked *write* grant is not a "
        "source withdrawal (tombstone/unreadable are the withdrawal cases covered here)",
    ]}
    try:
        # 1. real START bound to a real Artifact -> real WAIT with a time condition that
        # releases the foreground.
        wait_key = "parked-smoke-wait"
        owner.adoptions.grant(wait_key, ac.TRANSITION_WAIT, ACTIVITY_ID)
        start_key = "parked-smoke-start"
        owner.adoptions.grant(start_key, ac.TRANSITION_START, ACTIVITY_ID)
        owner.commands.start_activity(
            activity_kind="CONTINUE_PERSONAL_ARTIFACT",
            title="parked smoke fixture",
            origin_ref=artifact_id,
            source_refs=[artifact_id],
            subject_refs=[artifact_id],
            expected_revision=_revision(owner),
            idempotency_key=start_key,
            decision_ref=DECISION_REF,
            adoption_ref="adp:" + start_key,
            activity_id=ACTIVITY_ID,
        )
        wait_result = owner.coordinator.enter_waiting_with_condition(
            activity_id=ACTIVITY_ID,
            expected_revision=_revision(owner),
            waiting_on_ref="wait_dep:parkedsmoke",
            condition_kind=awa.COND_KIND_TIME_AT_OR_AFTER,
            parameters={"target_time": _iso(T0)},
            condition_expires_at=_iso(T0 + timedelta(minutes=120)),
            release_foreground=True,
            source_refs=["decision:parkedsmoke"],
            idempotency_key=wait_key,
            reason_kind=awa.REASON_KIND_AGENCY_CHOICE,
            decision_ref=DECISION_REF,
            adoption_ref="adp:" + wait_key,
            occurred_at=_iso(T0),
            observed_at=_iso(T0),
        )
        summary["activity_after_wait"] = {
            "status": wait_result["activity"]["status"],
            "revision": wait_result["activity"]["revision"],
            "foreground_released": wait_result["activity"].get("foreground_released"),
        }

        # 2. the owner's own due scan turns the satisfied condition into an OPEN eligibility.
        scanned = owner.coordinator.scan_due_agenda(now_at=_iso(T1))
        emitted = scanned.get("emitted_eligibilities") or []
        rows = _eligibilities(owner)
        open_rows = [row for row in rows.values() if row.get("status") == awa.ELIG_STATUS_OPEN]
        if not emitted or not open_rows:
            summary["checks"]["condition_emitted_eligibility"] = False
            print(json.dumps(summary, ensure_ascii=False, indent=1))
            return 1
        summary["checks"]["condition_emitted_eligibility"] = True
        summary["eligibility"] = {
            "id": open_rows[0]["eligibility_id"],
            "status": open_rows[0]["status"],
            "activity_revision": open_rows[0]["activity_revision"],
        }

        # 3. the real cycle: a due window must see the parked offer before any quiet gate.
        source = ag0.ResumeEligibleCandidateSource(
            owner.coordinator, ac.ActivityReadService(owner.store), target_read=port)
        runtime = _Runtime(root, owner.lease, owner.store, owner.commands,
                           owner.coordinator, source)
        cycle = alc.AutonomousLifeCycle(runtime, enabled=True, max_rounds_per_day=10, env={})
        clock = SimpleNamespace(moment=T1)

        class _Clock:
            def __call__(self):
                return clock.moment

        wake_runner = runner.AutonomousLifeRunner(
            cycle, state_root=root, env={}, interval_minutes=60, clock=_Clock())

        before_activity = _activity(owner)
        before_eligibility = _eligibilities(owner)
        first = wake_runner.run_due()
        after_activity = _activity(owner)
        after_eligibility = _eligibilities(owner)

        refs = list(runtime.coordinator.last_candidate_set.get("candidate_refs") or [])
        candidates = [runtime.candidate_read.get_candidate(ref) for ref in refs]
        resume_candidates = [c for c in candidates if c and c.get("candidate_kind")
                             == ag0.CANDIDATE_KIND_RESUME_ACTIVITY]
        legal = _legal_decisions(
            ag0.CandidateRecord.from_dict(resume_candidates[0])) if resume_candidates else []
        summary["checks"].update({
            "real_supply_target_readable": bool(port.read_artifact(artifact_id)),
            "due_window_considered": first.status_class == "choice",
            "one_resume_candidate": len(resume_candidates) == 1,
            "ag1_resume_legal": "RESUME" in legal,
            "activity_not_resumed": (
                after_activity.get("status") == before_activity.get("status")
                and int(after_activity.get("revision", -1))
                == int(before_activity.get("revision", -1))),
            "eligibility_not_consumed": (
                (after_eligibility.get(open_rows[0]["eligibility_id"]) or {}).get("status")
                == awa.ELIG_STATUS_OPEN),
        })
        summary["wakes"] = {"first": first.status, "first_outcome": first.outcome,
                            "first_llm_calls": first.llm_calls}

        # 4. the next identical window stays quiet: same eligibility is not replayed.
        clock.moment = T1 + timedelta(minutes=61)
        second = wake_runner.run_due()
        summary["checks"]["next_window_quiet"] = second.status == runner.WAKE_QUIET
        summary["wakes"]["second"] = second.status
        summary["wakes"]["second_llm_calls"] = second.llm_calls
        summary["checks"]["quiet_spent_no_request"] = (
            second.llm_calls == 0 and runtime.coordinator.calls == 1)

        # 5. negative: the same read after the eligibility's own expiry supplies nothing.
        expired_items, _snap, _meta = source.discover(observed_at=_iso(T2))
        summary["checks"]["expired_supplies_nothing"] = expired_items == []
        # Keep the exact candidate the source offered while the target was readable; step 7
        # re-asks the owner through ``refresh`` after the target is withdrawn.
        pre_withdrawal, _psnap, _pmeta = source.materialize(observed_at=_iso(T1))

        # 6. negative: another ACTIVE foreground makes the resume candidate invalid.
        front_key = "parked-smoke-front"
        owner.adoptions.grant(front_key, ac.TRANSITION_START, "actv:front")
        owner.commands.start_activity(
            activity_kind="READ_ARTIFACT",
            title="foreground fixture",
            origin_ref="world:parkedsmoke",
            source_refs=["world:parkedsmoke"],
            expected_revision=_revision(owner),
            idempotency_key=front_key,
            decision_ref=DECISION_REF,
            adoption_ref="adp:" + front_key,
            activity_id="actv:front",
        )
        blocked_items, _snap2, _meta2 = source.discover(observed_at=_iso(T1))
        ok, reason = source.validate_source(open_rows[0], observed_at=_iso(T1))
        summary["checks"]["foreground_conflict_supplies_nothing"] = (
            blocked_items == [] and not ok and reason == "FOREGROUND_CONFLICT")

        # 7. the owner no longer serves the target (deleted/tombstoned).  The eligibility is
        #    still OPEN: this is a *source* refusal, not an eligibility refusal, both before a
        #    new offer and at the pre-adoption recheck of the candidate already offered.
        _tombstone(supply, port, artifact_id, grant_id, subject)
        after_delete, _s6, _m6 = source.discover(observed_at=_iso(T1))
        refreshed = (source.refresh(pre_withdrawal[0], observed_at=_iso(T1))
                     if pre_withdrawal else None)
        summary["checks"]["deleted_target_supplies_nothing"] = after_delete == []
        summary["checks"]["deleted_target_refused_at_pre_adoption"] = bool(
            refreshed is not None
            and refreshed.availability_status != ag0.CANDIDATE_STATUS_OPEN
            and refreshed.invalidation_reason == "TARGET_SOURCE_UNREADABLE")
        summary["checks"]["eligibility_still_open_after_target_loss"] = (
            (_eligibilities(owner).get(open_rows[0]["eligibility_id"]) or {}).get("status")
            == awa.ELIG_STATUS_OPEN)

        # 8. the owner itself is unreachable: "cannot prove the source" is never an offer.
        supply.stop()
        unreachable, _s7, _m7 = source.discover(observed_at=_iso(T1))
        ok_unreachable, reason_unreachable = source.validate_source(
            open_rows[0], observed_at=_iso(T1))
        summary["checks"]["unreachable_owner_supplies_nothing"] = (
            unreachable == [] and not ok_unreachable
            and reason_unreachable == "TARGET_SOURCE_UNREADABLE")

        passed = all(bool(value) for value in summary["checks"].values())
        summary["ok"] = passed
        print(json.dumps(summary, ensure_ascii=False, indent=1))
        return 0 if passed else 1
    finally:
        supply.stop()
        owner.lease.release()


if __name__ == "__main__":
    sys.exit(main())

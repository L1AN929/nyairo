"""Test fixture: the instance's own AG-2 owner issues and persists a Life handoff.

Why this exists
---------------
Since 2026-10-29 the Memory admission consumer only files a handoff that this instance's
own AG-2 owner really persisted (see
``.agents/notes/implemented/bug-fix/2026-10-29-maintenance-experience-boundary.md``): it
looks the ``handoff_id`` up through the owner's read port and compares every provenance
field.  A mapping assembled in a test -- even a shape-correct one -- is refused with
``HANDOFF_SOURCE_UNVERIFIED`` / ``HANDOFF_SOURCE_NOT_FOUND``.  That refusal is correct and
is not switched off here.

The three release acceptance call sites that used to hand-pick their handoff dicts share
this helper instead: a real ``IntegrationStore`` + ``IntegrationAuthorityLease`` + typed
``IntegrationCommitPort`` + the real ``LifeIntegrationCoordinator.emit_experience_handoff``
entry point.  ``emit()`` returns the record read back through the owner's own
``IntegrationReadPort``, and ``verifier`` is the same read
``ProductionLifeRuntime.persisted_experience_handoff`` performs -- so what the consumer
compares against is the record AG-2 wrote, not a copy held in the test process.

What is real / what is a read-only input
----------------------------------------
* real: the AG-2 store, lease, writer capability, commit boundary, journal, handoff id
  derivation, event serialisation and the ``LIFE_INTEGRATION_OPENED`` /
  ``EXPERIENCE_HANDOFF_ENQUEUED`` commit families;
* read-only inputs: the Activity row (revision/kind/completion evidence) and the settlement
  lookup.  This helper only reads them; it never starts an LR-2/AR-1 owner and never writes
  their stores.  The provenance claim is about the AG-2 handoff record, which is real.

This module is a test fixture: no production module imports it.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Mapping, Optional

_ROOT = Path(__file__).resolve().parents[2]
for _entry in (
    _ROOT,
    _ROOT / "components",
    _ROOT / "components" / "life" / "scripts",
    _ROOT / "components" / "native",
    _ROOT / "components" / "native" / "src",
    _ROOT / "components" / "native" / "memory_runtime_v1",
):
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

import activity_continuity as ac  # noqa: E402
import life_integration_ag2 as ag2  # noqa: E402


class _ActivityRead:
    """The AG-2 emit path only reads the Activity row; this fixture only supplies it."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def register(self, activity_id: str, *, revision: int, activity_kind: str,
                 completion_evidence_ref: Optional[str] = None) -> dict[str, Any]:
        row = {
            "activity_id": str(activity_id),
            "revision": int(revision),
            "activity_kind": str(activity_kind),
            "completion_evidence_ref": completion_evidence_ref,
        }
        self.rows[str(activity_id)] = row
        return dict(row)

    def get_activity(self, activity_id: str) -> Optional[dict[str, Any]]:
        row = self.rows.get(str(activity_id))
        return dict(row) if row is not None else None


class _SettlementRead:
    """No settlement is invented; ``emit`` is given settlement refs only when a test asks."""

    def get_settlement_by_id(self, settlement_id: str) -> None:  # noqa: ARG002
        return None


class MemoryOwnerHandoff:
    """One isolated root where the real AG-2 owner issues handoffs and serves the read port."""

    def __init__(self, root: Path | str, *, namespace: str = ac.NAMESPACE_ISOLATED_TEST) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = ag2.IntegrationStore(self.root, namespace=namespace)
        self.lease = ag2.IntegrationAuthorityLease(self.root)
        self.lease.acquire()
        self.capability = ag2.LifeIntegrationAuthority.issue_capability(
            self.lease, namespace=namespace)
        self.commit = ag2.IntegrationCommitPort(self.store, self.lease, self.capability)
        self.read = ag2.IntegrationReadPort(self.store)
        self.activities = _ActivityRead()
        self.consumer = None
        # Only ``integration_commit`` / ``integration_read`` / ``activity_read`` /
        # ``settlement_read`` / ``experience_consumer`` are reached by ``emit_experience_handoff``;
        # the remaining narrow ports stay unset because this fixture drives no cycle.
        self.coordinator = ag2.LifeIntegrationCoordinator(
            integration_commit=self.commit,
            integration_read=self.read,
            kill_switches=ag2.LifeRuntimeKillSwitches.isolated_test_defaults(),
            candidate_materializer=None,
            candidate_read=None,
            decision_service=None,
            decision_read=None,
            adoption_coordinator=None,
            action_bridge=None,
            activity_read=self.activities,
            interruption_coordinator=None,
            waiting_coordinator=None,
            action_command=None,
            action_read=None,
            settlement_service=None,
            settlement_read=_SettlementRead(),
            progress_coordinator=None,
            progress_read=None,
        )

    # -- owner-side writes (all through the real typed commit boundary) -----------------
    def register_activity(self, activity_id: str, *, revision: int = 1,
                          activity_kind: str = "PERSONAL_WORK",
                          completion_evidence_ref: Optional[str] = None) -> dict[str, Any]:
        return self.activities.register(
            activity_id, revision=revision, activity_kind=activity_kind,
            completion_evidence_ref=completion_evidence_ref)

    def open_integration(
        self,
        *,
        integration_id: str,
        correlation_id: str,
        decision_ref: Optional[str] = None,
        adoption_ref: Optional[str] = None,
        action_refs: tuple[str, ...] | list[str] = (),
        settlement_refs: tuple[str, ...] | list[str] = (),
        progress_refs: tuple[str, ...] | list[str] = (),
        completion_ref: Optional[str] = None,
        opportunity_ref: str = "opp:memory-owner-fixture",
        candidate_set_ref: str = "cset:memory-owner-fixture",
    ) -> dict[str, Any]:
        record = ag2.LifeIntegrationRecord(
            integration_id=integration_id,
            correlation_id=correlation_id,
            opportunity_ref=opportunity_ref,
            candidate_set_ref=candidate_set_ref,
            decision_ref=decision_ref,
            adoption_ref=adoption_ref,
            action_refs=list(action_refs),
            settlement_refs=list(settlement_refs),
            progress_refs=list(progress_refs),
            completion_ref=completion_ref,
        )
        self.commit.life_integration_opened(
            map_updates={
                "integrations_by_id": {record.integration_id: record.to_dict()},
                "integration_id_by_correlation": {
                    record.correlation_id: record.integration_id},
            },
            journal_payload={
                "event_type": "LIFE_INTEGRATION_OPENED",
                "integration_id": record.integration_id,
                "correlation_id": record.correlation_id,
                "opportunity_id": record.opportunity_ref,
                "candidate_set_id": record.candidate_set_ref,
            },
        )
        return record.to_dict()

    def attach_consumer(self, consumer: Any) -> bool:
        """Bind the consumer to this owner's read port, exactly as the production runtime does."""

        self.consumer = consumer
        self.coordinator.experience_consumer = consumer
        bind = getattr(consumer, "bind_handoff_verifier", None)
        return bool(bind(self.verifier)) if callable(bind) else False

    def emit(
        self,
        *,
        integration_id: str,
        activity_id: str,
        kind: str,
        occurred_at: Optional[str] = None,
        fact_refs: tuple[str, ...] | list[str] = (),
        artifact_refs: tuple[str, ...] | list[str] = (),
        world_refs: tuple[str, ...] | list[str] = (),
        owner_confirmed_context: Optional[Mapping[str, Any]] = None,
        progress_result_ref: Optional[str] = None,
    ) -> dict[str, Any]:
        """Issue + persist one handoff through the real AG-2 entry and read it back."""

        out = self.coordinator.emit_experience_handoff(
            integration_id=integration_id,
            activity_id=activity_id,
            terminal_or_milestone_kind=kind,
            occurred_at=occurred_at,
            fact_refs=list(fact_refs),
            artifact_refs=list(artifact_refs),
            world_refs=list(world_refs),
            owner_confirmed_context=(dict(owner_confirmed_context)
                                     if owner_confirmed_context is not None else None),
            progress_result_ref=progress_result_ref,
        )
        handoff_id = out["handoff_event"].handoff_id
        return {"handoff_id": handoff_id,
                "record": self.persisted(handoff_id),
                "dispatch": out.get("dispatch_result")}

    # -- owner-side reads ---------------------------------------------------------------
    def persisted(self, handoff_id: str) -> Optional[dict[str, Any]]:
        state = self.read.read_state()
        record = (state.get("handoff_events_by_id") or {}).get(str(handoff_id))
        return dict(record) if isinstance(record, Mapping) else None

    def verifier(self, handoff_id: str) -> Optional[Mapping[str, Any]]:
        """Same read as ``ProductionLifeRuntime.persisted_experience_handoff``."""

        return self.persisted(handoff_id)

    def journal(self) -> list[dict[str, Any]]:
        return self.read.read_journal()

    def close(self) -> None:
        try:
            self.store.flush_snapshot()
        finally:
            if self.lease.held:
                self.lease.release()


__all__ = ["MemoryOwnerHandoff"]

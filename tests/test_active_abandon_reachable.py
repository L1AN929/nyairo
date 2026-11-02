"""ACTIVE 放弃决定性离线实验（一次性，零生产改动）。

目标：在全新隔离根中，用真实 LR2 / AG1 / AG2 走通
ACTIVE -> AG1 合法集包含 ABANDON -> 正式 AG1 commit/submit 写真实 DecisionRecord/store
-> 真实 AG2 adopt_decision 签发原预提交授权 -> 真实 LR2 执行 ABANDON 并产生 receipt
-> 回读同一 Activity 为 ABANDONED。

What is real here
-----------------
* 真实 LR-2：``ActivityAuthorityLease`` / ``CanonicalActivityStore`` /
  ``ActivityCommandService``，ACTIVE 的建立与最终 ABANDON 都经它自己的 admission issuer、
  CAS、幂等与 journal；
* 真实 AG-0：``CurrentActivityCandidateSource`` + ``CandidateMaterializer`` +
  ``CandidateReadService``；
* 真实 AG-1：``AgencyDecisionService.evaluate_opportunity`` 的完整三层管线（硬约束、容量、
  可达性、router、``DecisionOutputValidator``）与 ``AgencyDecisionStore`` 的原子提交；
* 真实 AG-2：``DecisionAdoptionCoordinator.adopt_decision`` 读同一个 AG-1 store、
  写预提交授权、调 LR-2、校验 commit receipt 并落 ``DecisionAdoptionRecord``。

What is a stand-in
------------------
* 模型返回的 Decision 是脚本值（EL3 限定机制），不声称真实模型会自然选择 ABANDON；
* 准备阶段的 START 授权是预置的（与 PAUSED 出口用例同一做法）；
  **ABANDON 那一步的 Decision 与授权都不伪造**：Decision 由真实 AG-1 提交，
  授权由真实 AG-2 写入并由 LR-2 重新验证；
* 没有任何产品模型 / HTTP transport：cognition 是本地硬拒网络的替身。

Run:  python3 -m unittest -v tests.test_active_abandon_reachable
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIFE_SCRIPTS = ROOT / "components" / "life" / "scripts"
NATIVE = ROOT / "components" / "native"
sys.path[:0] = [
    str(ROOT),
    str(LIFE_SCRIPTS),
    str(NATIVE),
    str(NATIVE / "src"),
    str(NATIVE / "memory_runtime_v1"),
]

import activity_admission as adm  # noqa: E402
import activity_continuity as ac  # noqa: E402
import activity_waiting_agenda as awa  # noqa: E402
import agency_decision_ag1 as ag1  # noqa: E402
import candidate_sources_ag0 as ag0  # noqa: E402
import life_integration_ag2 as ag2  # noqa: E402

POSIX_ONLY = unittest.skipUnless(os.name == "posix", "life modules import fcntl (POSIX only)")

#: The only exception type names a receipt may carry.  Anything else is ``UNKNOWN`` -- the
#: receipt never copies an exception message (it may quote private text and is not an enum).
_KNOWN_REASON_TYPES = frozenset({
    "ActivityError",
    "ActivityTransitionError",
    "AdmissionRejected",
    "AdoptionIllegalError",
    "AdoptionStaleError",
    "AgencyDecisionError",
    "AssertionError",
    "CandidateSourceError",
    "CognitionAdapterError",
    "ConsequenceRejected",
    "ContractViolationError",
    "DecisionValidationError",
    "ForegroundConflictError",
    "IdempotencyConflictError",
    "KeyError",
    "LifeIntegrationError",
    "NetworkBlocked",
    "RecoveryRequiredError",
    "RevisionConflictError",
    "TerminalActivityImmutableError",
    "TypeError",
    "UnauthorizedAgencyMutationError",
    "ValueError",
    "WriterCapabilityError",
})

ACTIVITY_ID = "actv:activeabandon"
PREP_DECISION_REF = "dec:activeabandonprep"
CTX_ID = "dctx:activeabandon"
OPP_ID = "dopp:activeabandon"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class _StubCognition:
    """Scripted stand-in for the product adapter: it has no transport and never opens a socket."""

    send_split_capable = False

    def __init__(self) -> None:
        self.raw_output = None
        self.calls = 0
        self.last_legal: tuple[str, ...] = ()

    def invoke(self, request, *, schema_retry_index=0):
        self.calls += 1
        self.last_legal = tuple(getattr(request, "legal_decision_enums", ()) or ())
        return ag1.CognitiveDecisionResponse(
            model_run_ref="mrun:stub:%s" % uuid.uuid4().hex[:8],
            model_id="stub:offline",
            model_version="stub-v1",
            raw_output=copy.deepcopy(self.raw_output),
            latency_ms=1,
        )


class _PreparedDecisionReader:
    """LR-2 admission read port: committed AG-1 records first, prepared START only as fallback."""

    def __init__(self, decision_store: ag1.AgencyDecisionStore) -> None:
        self.decision_store = decision_store
        self.prepared: dict[str, dict] = {}

    def get_decision(self, decision_id: str):
        state = self.decision_store.load_state()
        stored = (state.get("decisions_by_id") or {}).get(decision_id)
        if stored is not None:
            return copy.deepcopy(stored)
        return copy.deepcopy(self.prepared.get(decision_id))


class _AdoptionReader:
    """AG-2 adoption read port: live IntegrationStore first, prepared START grant second."""

    def __init__(self, integration_store: ag2.IntegrationStore) -> None:
        self.integration_store = integration_store
        self.prepared: dict[str, dict] = {}

    def grant(self, command_id: str, mutation_kind: str, activity_ref: str) -> None:
        self.prepared["adp:" + command_id] = {
            "adoption_id": "adp:" + command_id,
            "decision_ref": PREP_DECISION_REF,
            "decision_revision": 1,
            "adoption_status": "APPLIED",
            "revision": 1,
            "authorized_mutation_kind": mutation_kind,
            "authorized_command_id": command_id,
            "created_activity_ref": activity_ref,
            "target_activity_ref": activity_ref,
            "authorized_activity_ref": activity_ref,
        }

    def get_adoption_by_id(self, adoption_id: str):
        state = self.integration_store.load_state()
        live = (state.get("adoptions_by_id") or {}).get(adoption_id)
        if live is not None:
            return copy.deepcopy(live)
        return copy.deepcopy(self.prepared.get(adoption_id))


class _InterruptionPort:
    def get_effective_attention(self, activity):
        return (None, ag1.INTERRUPTIBILITY_IMMEDIATE, None)


class _NetworkBlocked(RuntimeError):
    """Raised by the test-local hard-fail network entry; nothing is ever dialled."""


class _NetworkGuard:
    """A real hard-fail network entry for this test process only.

    Sockets are replaced with a subclass whose ``connect``/``connect_ex`` raise before the OS
    sees a destination, and ``socket.create_connection``/``getaddrinfo`` are replaced too.  The
    counter therefore reflects attempts that were actually intercepted, not an initial zero.
    ``restore()`` puts every original back; it is called from the test's ``finally``.
    """

    def __init__(self) -> None:
        self.installed = False
        self.blocked = 0
        self.selfchecked = False
        self._orig_socket = None
        self._orig_create_connection = None
        self._orig_getaddrinfo = None

    def install(self) -> None:
        import socket as _socket

        guard = self
        orig_socket = _socket.socket

        class _BlockedSocket(orig_socket):  # type: ignore[misc, valid-type]
            def connect(self, *args, **kwargs):
                guard.blocked += 1
                raise _NetworkBlocked("socket.connect is hard-failed in this test")

            def connect_ex(self, *args, **kwargs):
                guard.blocked += 1
                raise _NetworkBlocked("socket.connect_ex is hard-failed in this test")

        def _blocked_create_connection(*args, **kwargs):
            guard.blocked += 1
            raise _NetworkBlocked("socket.create_connection is hard-failed in this test")

        def _blocked_getaddrinfo(*args, **kwargs):
            guard.blocked += 1
            raise _NetworkBlocked("socket.getaddrinfo is hard-failed in this test")

        self._orig_socket = orig_socket
        self._orig_create_connection = _socket.create_connection
        self._orig_getaddrinfo = _socket.getaddrinfo
        _socket.socket = _BlockedSocket
        _socket.create_connection = _blocked_create_connection
        _socket.getaddrinfo = _blocked_getaddrinfo
        self.installed = True

    def selfcheck(self) -> None:
        """Prove the entry really blocks: one loopback attempt must be intercepted and counted."""
        import socket as _socket

        before = self.blocked
        probe = _socket.socket()
        try:
            try:
                probe.connect(("127.0.0.1", 9))
            except _NetworkBlocked:
                pass
            else:
                raise AssertionError("network guard did not block socket.connect")
        finally:
            probe.close()
        if self.blocked != before + 1:
            raise AssertionError("network guard did not count the self-check attempt")
        self.blocked = before  # the probe is not a call made by the chain under test
        self.selfchecked = True

    def restore(self) -> None:
        if not self.installed:
            return
        import socket as _socket

        _socket.socket = self._orig_socket
        _socket.create_connection = self._orig_create_connection
        _socket.getaddrinfo = self._orig_getaddrinfo
        self.installed = False


class _StaticViewHost:
    """Minimal host so the real ``AutonomousLifeCycle.pause_condition_view`` can be called."""

    _last_foreground_record = None


def _pause_prompt_material_conclusion() -> str:
    """Scan only the PAUSE prompt block built by the real view plus the formal adapter whitelist.

    The test process never builds a real prompt (cognition is scripted), so this is a *static
    material* conclusion, not an observation of a sent prompt.  Nothing here prints the text.
    """

    try:
        import agency_cognition_adapter as adapter
        import autonomous_life_cycle as alc
    except Exception:  # noqa: BLE001 - an unbuildable structure is reported as UNKNOWN
        return "UNKNOWN"
    try:
        with_basis = alc.AutonomousLifeCycle.pause_condition_view(
            _StaticViewHost(),
            {
                "basis_ref": "cond:staticmaterial",
                "source": "ARTIFACT_NEXT_VERSION",
                "activity_ref": ACTIVITY_ID,
            },
        )
        without_basis = alc.AutonomousLifeCycle.pause_condition_view(_StaticViewHost(), None)
        bounded = json.dumps(
            [
                adapter._bounded_pause_condition(with_basis),
                adapter._bounded_pause_condition(without_basis),
            ],
            ensure_ascii=False,
            sort_keys=True,
        )
    except Exception:  # noqa: BLE001 - fail closed, never guess a "not found"
        return "UNKNOWN"
    # A whole word only: ``actv:activeabandon`` is an identifier, not a statement about ABANDON.
    return "FOUND" if re.search(r"\babandon\b", bounded, re.IGNORECASE) else "NOT_FOUND_IN_SCANNED_MATERIAL"


class _Harness:
    """One isolated root with real LR-2 / LR-4 / AG-0 / AG-1 / AG-2 owners."""

    def __init__(self) -> None:
        # Kept on purpose: the fixture root is evidence for this run, not throwaway state.
        self.root = Path(tempfile.mkdtemp(prefix="active-abandon-reach-"))

        # AG-1 owner store (read by both LR-2 admission and AG-2 adoption).
        self.ag1_store_root = self.root / "agency"
        self.ag1_store = ag1.AgencyDecisionStore(self.ag1_store_root)

        # AG-2 owner first: its store is the adoption authority LR-2 re-verifies.
        self.integration_lease = ag2.IntegrationAuthorityLease(self.root)
        self.integration_lease.acquire()
        self.integration_store = ag2.IntegrationStore(self.root, namespace=ac.NAMESPACE_ISOLATED_TEST)
        self.integration_capability = ag2.LifeIntegrationAuthority.issue_capability(
            self.integration_lease, namespace=ac.NAMESPACE_ISOLATED_TEST
        )
        self.integration_commit = ag2.IntegrationCommitPort(
            self.integration_store, self.integration_lease, self.integration_capability
        )
        self.adoption_reader = _AdoptionReader(self.integration_store)
        self.lr2_decision_reader = _PreparedDecisionReader(self.ag1_store)
        self.lr2_decision_reader.prepared[PREP_DECISION_REF] = {
            "decision_id": PREP_DECISION_REF,
            "revision": 1,
            "snapshot_fingerprint": "sfp:" + PREP_DECISION_REF,
        }
        self.admission_issuer = adm.build_admission_issuer(
            self.lr2_decision_reader, self.adoption_reader, runtime_epoch="active-abandon-test"
        )

        # LR-2 owner.
        self.lease = ac.ActivityAuthorityLease(self.root)
        self.lease.acquire()
        self.store = ac.CanonicalActivityStore(
            self.root, namespace=ac.NAMESPACE_ISOLATED_TEST, _admission_issuer=self.admission_issuer
        )
        self.activity_capability = self.lease.issue_capability(namespace=ac.NAMESPACE_ISOLATED_TEST)
        self.activity_command = ac.ActivityCommandService(
            store=self.store, lease=self.lease, capability=self.activity_capability
        )
        self.activity_read = ac.ActivityReadService(self.store)
        self.waiting = awa.WaitingCoordinator(
            activity_store=self.store,
            lease=self.lease,
            command_service=self.activity_command,
            namespace=ac.NAMESPACE_ISOLATED_TEST,
        )
        self.interruption = _InterruptionPort()

        # AG-0 owner.
        self.registry = ag0.CandidateSourceRegistry(
            {ag0.SOURCE_CURRENT_ACTIVITY: ag0.CurrentActivityCandidateSource(self.activity_read)}
        )
        self.materializer = ag0.CandidateMaterializer(
            self.registry,
            capability=ag0.CandidateProjectionGuard.issue_capability(
                caller_module="ag0_test_harness", namespace=ac.NAMESPACE_ISOLATED_TEST
            ),
        )
        self.candidate_read = ag0.CandidateReadService(self.materializer)

        # AG-1 owner, fully wired service over the real store.
        self.ag1_lease = ac.ActivityAuthorityLease(self.root / "agency-lease")
        self.ag1_lease.acquire()
        self.ag1_capability = ag1.AgencyDecisionAuthority.issue_capability(
            self.ag1_lease,
            caller_module="ag1_test_harness",
            store_root=self.ag1_store_root,
            namespace=ag1.NAMESPACE_ISOLATED_TEST,
        )
        self.stub = _StubCognition()
        self.ag1_service = ag1.AgencyDecisionService(
            store=self.ag1_store,
            lease=self.ag1_lease,
            capability=self.ag1_capability,
            candidate_read_service=self.candidate_read,
            activity_read_service=self.activity_read,
            cognition_adapter=self.stub,
        )
        self.ag1_read = ag1.AgencyDecisionReadService(self.ag1_store)

        # AG-2 owner, reading the same AG-1 store.
        self.adoption = ag2.DecisionAdoptionCoordinator(
            integration_commit=self.integration_commit,
            decision_read=self.ag1_read,
            candidate_read=self.candidate_read,
            activity_read=self.activity_read,
            activity_command=self.activity_command,
            interruption_coordinator=self.interruption,
            waiting_coordinator=self.waiting,
            progress_coordinator=None,
        )

    def close(self) -> None:
        self.integration_store.flush_snapshot()
        self.ag1_store.flush_snapshot()
        if self.integration_lease.held:
            self.integration_lease.release()
        if self.ag1_lease.held:
            self.ag1_lease.release()
        if self.lease.held:
            self.lease.release()

    # -- LR-2 preparation (START grant is explicitly prepared; ABANDON is not) ---------

    def store_revision(self) -> int:
        state, _ = self.store.load_verified_state_and_journal()
        return int(state["revision"])

    def activity(self, activity_id: str = ACTIVITY_ID) -> dict:
        state, _ = self.store.load_verified_state_and_journal()
        return copy.deepcopy((state.get("activities") or {})[activity_id])

    def foreground_ref(self):
        state, _ = self.store.load_verified_state_and_journal()
        return state.get("foreground_activity_ref")

    def start(self, command_id: str = "activeabandon-start") -> dict:
        self.adoption_reader.grant(command_id, ac.TRANSITION_START, ACTIVITY_ID)
        result = self.activity_command.start_activity(
            activity_kind="READ_ARTIFACT",
            title="ACTIVE abandon reachability fixture",
            origin_ref="world:activeabandon",
            source_refs=["world:activeabandon"],
            expected_revision=self.store_revision(),
            idempotency_key=command_id,
            decision_ref=PREP_DECISION_REF,
            adoption_ref="adp:" + command_id,
            activity_id=ACTIVITY_ID,
        )
        return copy.deepcopy(result.activity)

    # -- AG-0 / AG-1 / AG-2 ------------------------------------------------------------

    def materialize(self, *, observed_at: str):
        candidate_set = self.materializer.build_candidate_set(observed_at=observed_at)
        candidates = [
            self.materializer._candidates_by_id[cid] for cid in candidate_set.candidate_refs
        ]
        return candidate_set, candidates

    def evaluate_abandon(self, *, observed_at: str):
        candidate_set, candidates = self.materialize(observed_at=observed_at)
        candidate = candidates[0]
        self.stub.raw_output = {
            "decision": ag1.DECISION_ABANDON,
            "selected_candidate_id": candidate.candidate_id,
            "reason_codes": [ag1.REASON_ABANDON_NONTERMINAL_CHOSEN],
            "short_rationale": "我决定不再继续这件正在进行的活动。",
            "deferred_candidate_ids": [],
        }
        opportunity = ag1.DecisionOpportunity(
            opportunity_id=OPP_ID,
            trigger_kind="EXPLICIT_REEVALUATION",
            trigger_ref="evt:activeabandon",
            candidate_set_ref=candidate_set.candidate_set_id,
            occurred_at=observed_at,
            observed_at=observed_at,
            recorded_at=observed_at,
            causal_parent_refs=[candidate_set.candidate_set_id],
            idempotency_key="idem:activeabandon",
            current_activity_ref=ACTIVITY_ID,
            status=ag1.OPPORTUNITY_STATUS_OPEN,
        )
        result = self.ag1_service.evaluate_opportunity(
            opportunity=opportunity,
            candidate_set=candidate_set,
            candidates=candidates,
            body_capacity_state="AVAILABLE",
            device_availability_state="AVAILABLE",
            resource_availability_state="AVAILABLE",
            privacy_availability_state="AVAILABLE",
            runtime_load_state="AVAILABLE",
        )
        return result, candidate_set, candidates

    def abandon_transitions(self) -> list[dict]:
        return [
            entry
            for entry in self.store.load_verified_state_and_journal()[1]
            if entry.get("transition_type") == ac.TRANSITION_ABANDON
        ]


class _Receipt(dict):
    """The one safe fixed JSON line: every field is read from a real object."""

    def __init__(self) -> None:
        super().__init__(
            ag1_abandon_offered=None,
            ag1_decision_committed=None,
            ag2_authorization_issued=None,
            lr2_receipt_received=None,
            owner_status=None,
            adoption_status=None,
            blocked_stage=None,
            reason_code=None,
            network_calls=0,
            network_guard_selfchecked=False,
            prompt_says_abandon_withheld="UNKNOWN",
            prompt_material_basis="NONE",
        )


@POSIX_ONLY
class ActiveAbandonReachableTest(unittest.TestCase):
    def setUp(self):
        self.harness = _Harness()

    def tearDown(self):
        self.harness.close()

    def test_active_abandon_chain(self):
        harness = self.harness
        guard = _NetworkGuard()
        receipt = _Receipt()
        stage = "PREPARE_ACTIVE"
        guard.install()
        try:
            guard.selfcheck()
            receipt["network_guard_selfchecked"] = guard.selfchecked

            # Static material only: this process never builds or sends a real prompt.
            receipt["prompt_material_basis"] = "STATIC_MATERIAL"
            receipt["prompt_says_abandon_withheld"] = _pause_prompt_material_conclusion()

            started = harness.start()
            self.assertEqual(started["status"], ac.STATUS_ACTIVE)
            self.assertEqual(harness.foreground_ref(), ACTIVITY_ID)

            observed_at = _now_iso()
            stage = "AG1_EVALUATE"
            result, _candidate_set, _candidates = harness.evaluate_abandon(observed_at=observed_at)
            receipt["network_calls"] = guard.blocked
            receipt["ag1_abandon_offered"] = ag1.DECISION_ABANDON in harness.stub.last_legal
            self.assertTrue(
                receipt["ag1_abandon_offered"],
                "AG-1 legal set did not offer ABANDON: %r" % (harness.stub.last_legal,),
            )

            decision = result.get("decision_record")
            self.assertIsNotNone(decision, result.get("failure_class") or result.get("status"))
            self.assertEqual(decision.decision, ag1.DECISION_ABANDON)

            stored = harness.ag1_read.get_decision(decision.decision_id)
            receipt["ag1_decision_committed"] = stored is not None
            self.assertIsNotNone(stored, "DecisionRecord not found in the same AG-1 store")
            self.assertEqual(stored["decision"], ag1.DECISION_ABANDON)

            stage = "AG2_ADOPT"
            adopted = harness.adoption.adopt_decision(decision.decision_id)
            record = adopted["adoption_record"]
            receipt["adoption_status"] = record.adoption_status
            receipt["ag2_authorization_issued"] = (
                record.authorized_mutation_kind == "ABANDON"
                and bool(record.authorized_command_id)
            )
            self.assertEqual(record.adoption_status, ag2.ADOPTION_STATUS_APPLIED)
            self.assertTrue(receipt["ag2_authorization_issued"], "AG-2 issued no ABANDON authorization")

            stage = "LR2_READBACK"
            final = harness.activity()
            receipt["owner_status"] = final["status"]
            transitions = harness.abandon_transitions()
            receipt["lr2_receipt_received"] = bool(
                transitions
                and record.applied_activity_command_ref == transitions[0].get("transition_id")
            )
            self.assertEqual(final["status"], ac.STATUS_ABANDONED, "same Activity did not reach ABANDONED")
            self.assertTrue(receipt["lr2_receipt_received"], "LR-2 ABANDON transition receipt missing")
            self.assertIsNone(harness.foreground_ref())
            receipt["network_calls"] = guard.blocked
            self.assertTrue(receipt["network_guard_selfchecked"], "network guard self-check did not run")
            self.assertEqual(guard.blocked, 0, "the chain attempted a network call")
            self.assertIn(
                receipt["prompt_says_abandon_withheld"],
                {"FOUND", "NOT_FOUND_IN_SCANNED_MATERIAL", "UNKNOWN"},
            )
            stage = None
        except Exception as exc:  # noqa: BLE001 - the receipt must record the real stop
            receipt["network_calls"] = guard.blocked
            receipt["blocked_stage"] = stage
            name = type(exc).__name__
            receipt["reason_code"] = name if name in _KNOWN_REASON_TYPES else "UNKNOWN"
            raise
        finally:
            guard.restore()
            print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    unittest.main(verbosity=2)

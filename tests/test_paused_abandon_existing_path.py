#!/usr/bin/env python3
"""PAUSED 前台活动：既有 ABANDON 出口的离线机制链与正负控。

这条用例接通的是设计里已经写明的出口，不是新增状态机：

* SRC-0719:12-13 —— READY / ACTIVE / WAITING_EXTERNAL / BLOCKED / **PAUSED** 都可由
  相应 owner 判 ABANDONED；PAUSED 仍是她当前的活动，只是不能继续执行。
* SRC-0007:16 —— 等待、暂停、放弃都是合法生活选择 / transition intent。

What is real here
-----------------
* 真实 LR-2：``ActivityAuthorityLease`` / ``CanonicalActivityStore`` /
  ``ActivityCommandService``，ACTIVE→PAUSED 与最终 ABANDON 都经它自己的 admission
  issuer、CAS、幂等与 journal；
* 真实 AG-0：``CurrentActivityCandidateSource`` + ``CandidateMaterializer`` +
  ``CandidateReadService``，PAUSED 候选来自真实的 LR-2 前台读；
* 真实 AG-1：``HardConstraintEvaluator``、``DecisionOutputValidator`` 的合法集与
  validator 都是真实调用；
* 真实 AG-2：``DecisionAdoptionCoordinator.adopt_decision`` 真的写预授权、调 LR-2、
  校验 commit receipt 并落 ``DecisionAdoptionRecord``；
* 真实 LR-4：``WaitingCoordinator`` 与它自己的三个 store（本单只读核对没有生成资格）。

What is a stand-in
------------------
* 模型返回的 Decision 是脚本值（EL3 限定机制），不声称真实模型会自然选择 ABANDON；
* ``DecisionReader`` 只回答 ``ProvenanceVerifier`` / AG-2 新鲜度检查读取的字段；
  准备阶段（START / PAUSE）的 adoption grant 是预置的，与 ``test_paused_eligible_alignment``
  同一做法。ABANDON 那一步的授权由真实 AG-2 写入并被 LR-2 重新验证；
* interruption 端口是窄替身（IMMEDIATE / ATOMIC 两档），用来验证 ATOMIC 不出口。

不覆盖：真实产品模型、resident、主实例、Commitment 绑定与 S08 assessment（现有 Activity
记录没有 commitment_binding 字段，见 worker 结果报告）、G1 端到端。

为什么现在测的是「出口被扣留」
------------------------------
「PAUSED 可以 ABANDON」只有在**可证**没有未决副作用 / 承诺绑定时才成立。现有接口证不了：
Activity 记录没有承诺字段；AR-0 的 Action 用 ``actv_<id>``，canonical 活动用 ``actv:<id>``，
两套词表不相等。读不到不能当没有，也不许扩词表或删引用来「凑」安全，所以 AG-1 不再把
ABANDON 放进 PAUSED 的合法集，AG-2 也会在 LR-2 之前拒绝同一个目标，具名原因
``PAUSED_ABANDON_WITHHELD_UNPROVEN_SAFE``。本文件因此覆盖：扣留本身、扣留后活动与未决
Action 都不被改动、以及 ACTIVE / ATOMIC / 无授权等既有门仍然生效。

2026-11-01 夹具对齐（PAUSE 释放执行槽之后）
------------------------------------------
一次成功的 PAUSE 现在写 ``foreground_released`` 并清空 ``foreground_activity_ref``
（见 .agents/notes/implemented/feature/2026-10-24-paused-background-single-execution.md）。
因此一个**新**暂停的 Activity：
* 不再被 AG-0 的 CURRENT_ACTIVITY 源投影成 ``CONTINUE_CURRENT`` 候选（它的回来入口是
  RESUME_ELIGIBLE + LR-4 资格，本夹具没有生成资格）；
* 但 AG-1 的 ``validate_decision_output`` 仍按快照里的 ``current_activity_status == PAUSED``
  具名拒绝 ABANDON，AG-2 也仍按目标 Activity 自己的状态在 LR-2 之前拒绝。这两处是状态判定，
  与前台指针无关，所以下面继续用「伪造的 ABANDON」把它们真正打到，而不是跳过。

``context()`` 默认仍构造**旧指针形状**的快照（current_activity_ref 指向该 Activity、
status=PAUSED）。这**不是**说本轮还会投影它；只是为了打到 AG-1 那个按状态判定的具名拒绝门。
真实的新暂停状态是「前台为空」，本文件用 ``foreground_ref() is None`` 和 0 候选断言它。

Run:  python3 -m unittest -v tests.test_paused_abandon_existing_path
"""
from __future__ import annotations

import copy
import os
import sys
import tempfile
import unittest
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
import action_reality_ledger as arl  # noqa: E402
import agency_decision_ag1 as ag1  # noqa: E402
import candidate_sources_ag0 as ag0  # noqa: E402
import life_integration_ag2 as ag2  # noqa: E402

POSIX_ONLY = unittest.skipUnless(os.name == "posix", "life modules import fcntl (POSIX only)")

STAMP = "2026-10-23T00:00:00+00:00"
ACTIVITY_ID = "actv:pausedexit"
PREP_DECISION_REF = "dec:pausedexitprep"
ABANDON_DECISION_ID = "dec:pausedexitabandon"
CTX_ID = "dctx:pausedexit"
OPP_ID = "dopp:pausedexit"
CAP_ID = "cap:pausedexit"
SFP = "sfp:pausedexit"


class _DecisionReader:
    """AG-1 owner read-port stand-in: only the fields the real verifiers read."""

    def __init__(self) -> None:
        self.decisions: dict[str, dict] = {}
        self.contexts: dict[str, dict] = {}
        self.opportunities: dict[str, dict] = {}

    def get_decision(self, decision_id: str):
        return copy.deepcopy(self.decisions.get(decision_id))

    def get_context_snapshot(self, context_id: str):
        return copy.deepcopy(self.contexts.get(context_id))

    def get_opportunity(self, opportunity_id: str):
        return copy.deepcopy(self.opportunities.get(opportunity_id))


class _AdoptionReader:
    """AG-2 owner read-port: live authorisations first, prepared grants second.

    The live half is the real ``IntegrationStore`` state AG-2 wrote through its own
    commit port, so LR-2 re-verifies the command-scoped grant that AG-2 just issued.
    The prepared half only covers the fixture's own START / PAUSE / CHECKPOINT steps.
    """

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
    """Narrow LR-3 port: the two attention modes this test needs to distinguish."""

    def __init__(self) -> None:
        self.mode = ag1.INTERRUPTIBILITY_IMMEDIATE

    def get_effective_attention(self, activity):
        return (None, self.mode, None)


class _Harness:
    """One isolated root with real LR-2 / LR-4 / AG-0 / AG-1(data) / AG-2 owners."""

    def __init__(self) -> None:
        # Kept on purpose: the fixture root is evidence for this run, not throwaway state.
        self.root = Path(tempfile.mkdtemp(prefix="paused-abandon-exit-"))

        self.integration_lease = ag2.IntegrationAuthorityLease(self.root)
        self.integration_lease.acquire()
        self.integration_store = ag2.IntegrationStore(self.root, namespace=ac.NAMESPACE_ISOLATED_TEST)
        self.integration_capability = ag2.LifeIntegrationAuthority.issue_capability(
            self.integration_lease, namespace=ac.NAMESPACE_ISOLATED_TEST
        )
        self.integration_commit = ag2.IntegrationCommitPort(
            self.integration_store, self.integration_lease, self.integration_capability
        )

        self.decision_reader = _DecisionReader()
        self.adoption_reader = _AdoptionReader(self.integration_store)
        self.admission_issuer = adm.build_admission_issuer(
            self.decision_reader, self.adoption_reader, runtime_epoch="paused-abandon-exit-test"
        )

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

        # Real AR-0 ledger, used only to hold one genuinely unresolved Action across ABANDON.
        self.action_store = arl.ActionStore(self.root, namespace=ac.NAMESPACE_ISOLATED_TEST)
        self.action_capability = arl.ActionRealityAuthority.issue_writer_capability(
            self.lease, namespace=ac.NAMESPACE_ISOLATED_TEST
        )
        self.action_command = arl.ActionCommandService(
            store=self.action_store, lease=self.lease, capability=self.action_capability
        )

        self.adoption = ag2.DecisionAdoptionCoordinator(
            integration_commit=self.integration_commit,
            decision_read=self.decision_reader,
            candidate_read=self.candidate_read,
            activity_read=self.activity_read,
            activity_command=self.activity_command,
            interruption_coordinator=self.interruption,
            waiting_coordinator=self.waiting,
            progress_coordinator=None,
        )

        self.decision_reader.decisions[PREP_DECISION_REF] = {
            "decision_id": PREP_DECISION_REF,
            "revision": 1,
            "snapshot_fingerprint": "sfp:" + PREP_DECISION_REF,
        }

    def close(self) -> None:
        self.action_store.flush_snapshot()
        self.integration_store.flush_snapshot()
        if self.integration_lease.held:
            self.integration_lease.release()
        if self.lease.held:
            self.lease.release()

    # -- LR-2 owner commands -------------------------------------------------

    def _grant(self, command_id: str, mutation_kind: str) -> None:
        self.adoption_reader.grant(command_id, mutation_kind, ACTIVITY_ID)

    def store_revision(self) -> int:
        state, _ = self.store.load_verified_state_and_journal()
        return int(state["revision"])

    def activity(self, activity_id: str = ACTIVITY_ID) -> dict:
        state, _ = self.store.load_verified_state_and_journal()
        return copy.deepcopy((state.get("activities") or {})[activity_id])

    def foreground_ref(self):
        state, _ = self.store.load_verified_state_and_journal()
        return state.get("foreground_activity_ref")

    def start(self, activity_id: str = ACTIVITY_ID, command_id: str = "pausedexit-start") -> dict:
        self._grant(command_id, ac.TRANSITION_START)
        result = self.activity_command.start_activity(
            activity_kind="READ_ARTIFACT",
            title="PAUSED abandon exit fixture",
            origin_ref="world:pausedexit",
            source_refs=["world:pausedexit"],
            expected_revision=self.store_revision(),
            idempotency_key=command_id,
            decision_ref=PREP_DECISION_REF,
            adoption_ref="adp:" + command_id,
            activity_id=activity_id,
        )
        return copy.deepcopy(result.activity)

    def pause(self, command_id: str = "pausedexit-pause") -> dict:
        self._grant(command_id, ac.TRANSITION_PAUSE)
        result = self.activity_command.pause_activity(
            activity_id=ACTIVITY_ID,
            expected_revision=self.store_revision(),
            idempotency_key=command_id,
            source_refs=["decision:pausedexit"],
            decision_ref=PREP_DECISION_REF,
            adoption_ref="adp:" + command_id,
        )
        return copy.deepcopy(result.activity)

    def wait(self, command_id: str = "pausedexit-wait") -> dict:
        self._grant(command_id, ac.TRANSITION_WAIT)
        result = self.activity_command.wait_activity(
            activity_id=ACTIVITY_ID,
            waiting_on_ref="wait_dep:pausedexit",
            resume_condition_ref="rcond:pausedexit",
            waiting_episode_id="wep:pausedexit",
            expected_revision=self.store_revision(),
            idempotency_key=command_id,
            source_refs=["decision:pausedexit"],
            decision_ref=PREP_DECISION_REF,
            adoption_ref="adp:" + command_id,
        )
        return copy.deepcopy(result.activity)

    def checkpoint(self, command_id: str = "pausedexit-checkpoint") -> dict:
        self._grant(command_id, ac.TRANSITION_CHECKPOINT)
        result = self.activity_command.update_checkpoint(
            activity_id=ACTIVITY_ID,
            checkpoint_ref="checkpoint:pausedexit2",
            expected_revision=self.store_revision(),
            idempotency_key=command_id,
            source_refs=["decision:pausedexit"],
            decision_ref=PREP_DECISION_REF,
            adoption_ref="adp:" + command_id,
        )
        return copy.deepcopy(result.activity)

    # -- AG-0 / AG-1 / AG-2 --------------------------------------------------

    def materialize(self, *, observed_at: str = STAMP):
        candidate_set = self.materializer.build_candidate_set(observed_at=observed_at)
        candidates = [self.materializer._candidates_by_id[cid] for cid in candidate_set.candidate_refs]
        return candidate_set, candidates

    def context(
        self,
        *,
        activity: dict,
        interruptibility: str = ag1.INTERRUPTIBILITY_IMMEDIATE,
        current_ref: str | None = None,
        current_status: str | None = None,
    ) -> ag1.DecisionContextSnapshot:
        return ag1.DecisionContextSnapshot(
            context_snapshot_id=CTX_ID,
            opportunity_ref=OPP_ID,
            candidate_set_ref="cset:pausedexit",
            life_frame_ref="lframe:pausedexit",
            captured_at=STAMP,
            source_revisions={"snapshot": 1},
            snapshot_fingerprint=SFP,
            current_activity_ref=current_ref if current_ref is not None else activity["activity_id"],
            current_activity_status=current_status if current_status is not None else activity["status"],
            current_activity_revision=int(activity["revision"]),
            interruptibility=interruptibility,
        )

    def capacity(self, candidate, *, exec_now: str = ag1.EXEC_NOW) -> ag1.AgencyCapacitySnapshot:
        return ag1.AgencyCapacitySnapshot(
            capacity_snapshot_id=CAP_ID,
            captured_at=STAMP,
            source_revisions=["snapshot:pausedexit"],
            candidate_executability={candidate.candidate_id: {"executability": exec_now}},
        )

    @staticmethod
    def empty_capacity() -> ag1.AgencyCapacitySnapshot:
        """A capacity snapshot for a *forged* decision that has no projected candidate."""
        return ag1.AgencyCapacitySnapshot(
            capacity_snapshot_id=CAP_ID,
            captured_at=STAMP,
            source_revisions=["snapshot:pausedexit"],
            candidate_executability={},
        )

    @staticmethod
    def forged_abandon_raw() -> dict:
        """A model output that names the PAUSED Activity without a projected candidate.

        A released PAUSED Activity has no CONTINUE_CURRENT candidate at all, so a decision that
        still wants to abandon it can only be a forged/self-asserted one.  That is exactly the
        backstop both AG-1 and AG-2 keep: it must be refused by name, not silently ignored.
        """
        return {
            "decision": ag1.DECISION_ABANDON,
            "selected_candidate_id": None,
            "reason_codes": [ag1.REASON_ABANDON_NONTERMINAL_CHOSEN],
            "short_rationale": "我决定结束这件已经放下的活动。",
            "deferred_candidate_ids": [],
            "target_activity_ref": ACTIVITY_ID,
        }

    def validate_forged_abandon(self, *, context, candidate_set=None):
        """Run the real AG-1 validator over a forged ABANDON with an empty candidate set."""
        candidate_set = candidate_set or ag0.CandidateSet(
            candidate_set_id="cset:pausedexit",
            subject_id=ag1.GLOBAL_SUBJECT_ID,
            candidate_refs=[],
            source_snapshot_refs=[],
            observed_at=STAMP,
            built_at=STAMP,
        )
        return ag1.DecisionOutputValidator.validate_decision_output(
            self.forged_abandon_raw(),
            candidate_set=candidate_set,
            candidates_by_id={},
            constraint_results_by_id={},
            context_snapshot=context,
            capacity_snapshot=self.empty_capacity(),
        )

    def constraints(self, candidates, context):
        return ag1.HardConstraintEvaluator.evaluate_candidates(
            candidates=list(candidates),
            context_snapshot=context,
            candidate_read_service=self.candidate_read,
            activity_read_service=self.activity_read,
            evaluated_at=STAMP,
        )

    @staticmethod
    def paused_continue_candidate() -> ag0.CandidateRecord:
        """The ``CONTINUE_CURRENT`` candidate a *legacy* PAUSED pointer used to project.

        A newly paused Activity is no longer projected; this hand-built record exists only to
        exercise the pure AG-1 legality matrix against a PAUSED current Activity.
        """
        return ag0.CandidateRecord(
            candidate_id="cand:pausedexitpaused",
            candidate_kind=ag0.CANDIDATE_KIND_CONTINUE_CURRENT,
            source_kind=ag0.SOURCE_CURRENT_ACTIVITY,
            source_ref=ACTIVITY_ID,
            availability_status=ag0.CANDIDATE_STATUS_OPEN,
            created_at=STAMP,
            observed_at=STAMP,
            recorded_at=STAMP,
            idempotency_key="cand:pausedexitpaused",
            activity_ref=ACTIVITY_ID,
            causal_parent_refs=[ACTIVITY_ID],
            provenance_refs=[ACTIVITY_ID],
        )

    @staticmethod
    def legal(candidate, context, capacity):
        return ag1.DecisionOutputValidator.compute_legal_decisions_for_candidate(
            candidate, context_snapshot=context, capacity_snapshot=capacity
        )

    def forged_abandon_decision(self) -> ag1.DecisionRecord:
        """A DecisionRecord that skipped AG-1 and still names the PAUSED Activity.

        A released PAUSED Activity has no projected candidate, so this record is deliberately a
        self-asserted one.  Its stored context snapshot matches the *live* state (no foreground
        pointer), so AG-2's freshness check passes and the owner's own PAUSED-status refusal is
        what stops it -- before LR-2 ever sees a command.
        """
        decision = ag1.DecisionRecord(
            subject_id=ag1.GLOBAL_SUBJECT_ID,
            decision_id=ABANDON_DECISION_ID,
            revision=1,
            decision_opportunity_ref=OPP_ID,
            candidate_set_ref="cset:pausedexit",
            candidate_refs=(),
            context_snapshot_ref=CTX_ID,
            hard_constraint_result_refs=(),
            decision=ag1.DECISION_ABANDON,
            reason_codes=(ag1.REASON_ABANDON_NONTERMINAL_CHOSEN,),
            decision_route=ag1.ROUTE_COGNITIVE_REQUIRED,
            policy_version=ag1.POLICY_VERSION_AG1_V1,
            snapshot_fingerprint=SFP,
            created_at=STAMP,
            current_activity_ref=None,
            capacity_snapshot_ref=CAP_ID,
            selected_candidate_ref=None,
            target_activity_ref=ACTIVITY_ID,
        )
        self.decision_reader.decisions[ABANDON_DECISION_ID] = decision.to_dict()
        self.decision_reader.contexts[CTX_ID] = {
            # The live state after a released PAUSE: the pointer is gone.
            "current_activity_ref": None,
            "current_activity_revision": self.store_revision(),
            "candidate_revisions": {},
        }
        self.decision_reader.opportunities[OPP_ID] = {"status": "OPEN"}
        return decision

    def abandon_transitions(self) -> list[dict]:
        return [entry for entry in self.store.load_verified_state_and_journal()[1]
                if entry.get("transition_type") == ac.TRANSITION_ABANDON]

    def abandon_transition(self) -> dict:
        entries = self.abandon_transitions()
        if not entries:
            raise AssertionError("no ABANDON transition in the canonical journal")
        return entries[0]

    def propose_unresolved_action(self) -> dict:
        # ``activity_ref`` is left unbound on purpose: AR-0's ``ActionProposal`` requires
        # the ``actv_<id>`` spelling while the AG/LR-2 canonical refs use ``actv:<id>``.
        # The two vocabularies do not meet, and this ticket does not widen either one.
        result = self.action_command.propose_action(
            action_kind=arl.ACTION_KIND_TOOL,
            target_ref="tool:pausedexit",
            payload_ref="tool_result:pausedexitpending",
            payload_data={"call": "hold"},
            idempotency_key="pausedexit-unresolved-action",
            source_refs=["decision:pausedexit"],
            decision_ref=PREP_DECISION_REF,
        )
        return copy.deepcopy(result["action"])

    def action(self, action_id: str) -> dict:
        return copy.deepcopy(self.action_store.load_state()["actions"][action_id])


@POSIX_ONLY
class PausedAbandonExistingPathTest(unittest.TestCase):
    """A PAUSED Activity offers no execution-shaped exit; AG-2 still withholds ABANDON.

    After v0.4 background pause a newly PAUSED Activity is not the foreground at all, so AG-0
    does not project it.  The named ``PAUSED_ABANDON_WITHHELD_UNPROVEN_SAFE`` doors in AG-1/AG-2
    remain as status-based backstops for a forged Decision that still names it.
    """

    def make_harness(self) -> _Harness:
        harness = _Harness()
        self.addCleanup(harness.close)
        return harness

    def paused_harness(self) -> _Harness:
        harness = self.make_harness()
        started = harness.start()
        self.assertEqual(started["status"], ac.STATUS_ACTIVE)
        paused = harness.pause()
        self.assertEqual(paused["status"], ac.STATUS_PAUSED)
        # v0.4 background pause: a successful PAUSE releases the execution slot.  The record
        # keeps its own PAUSED status and is marked released; the global pointer is gone.
        self.assertTrue(paused.get("foreground_released") is True, paused)
        self.assertIsNone(harness.foreground_ref())
        return harness

    # -- the withheld exit: AG-1 refuses a forged ABANDON, AG-2 refuses the same target --

    def test_paused_projection_offers_no_abandon_and_withholds_it_end_to_end(self):
        harness = self.paused_harness()

        # New rule: a released PAUSED Activity is *not* the foreground, so AG-0's
        # CURRENT_ACTIVITY source does not project it at all.  Its way back is the LR-4
        # RESUME_ELIGIBLE source, which this fixture does not generate.
        candidate_set, candidates = harness.materialize()
        self.assertEqual(candidates, [])
        self.assertEqual(candidate_set.candidate_refs, [])
        self.assertIsNone(harness.foreground_ref())

        # A forged ABANDON that still names the PAUSED Activity is refused by name.  The
        # snapshot keeps the legacy pointer shape only to exercise that status-based backstop;
        # it is not a claim that the round still projects this Activity.
        context = harness.context(activity=harness.activity())
        with self.assertRaises(ag1.DecisionValidationError) as caught:
            harness.validate_forged_abandon(context=context)
        self.assertIn("PAUSED_ABANDON_WITHHELD_UNPROVEN_SAFE", str(caught.exception))
        self.assertEqual(caught.exception.failure_class,
                         ag1.FAILURE_CLASS_ILLEGAL_DECISION_MATRIX)

        final = harness.activity()
        self.assertEqual(final["status"], ac.STATUS_PAUSED)
        self.assertTrue(final.get("foreground_released") is True)
        self.assertIsNone(harness.foreground_ref())
        self.assertIsNone(final.get("completion_evidence_ref"))
        self.assertEqual(harness.abandon_transitions(), [])

    def test_ag2_refuses_a_forged_paused_abandon_before_lr2(self):
        """Even a decision that skipped AG-1's validator cannot abandon the PAUSED Activity."""

        harness = self.paused_harness()
        self.assertEqual(harness.materialize()[1], [], "nothing should be projected")
        decision = harness.forged_abandon_decision()

        with self.assertRaises(ag2.AdoptionIllegalError) as caught:
            harness.adoption.adopt_decision(decision)
        self.assertIn("PAUSED_ABANDON_WITHHELD_UNPROVEN_SAFE", str(caught.exception))

        self.assertEqual(harness.activity()["status"], ac.STATUS_PAUSED)
        self.assertIsNone(harness.foreground_ref())
        self.assertEqual(harness.abandon_transitions(), [])

    def test_unbound_unresolved_action_is_not_same_activity_evidence_and_exit_stays_withheld(self):
        """The old name claimed "same activity, kept unresolved".  It never was.

        ``propose_unresolved_action`` leaves ``activity_ref`` unbound because AR-0's Action
        ledger speaks ``actv_<id>`` and canonical Activities speak ``actv:<id>``.  That gap is
        exactly why the PAUSED exit is withheld: this test proves the action is *not* bound to
        the Activity (so it is not evidence of "no unresolved side effect"), and that neither
        the Activity nor the Action changes while the exit is refused.
        """

        harness = self.paused_harness()
        pending = harness.propose_unresolved_action()
        self.assertEqual(pending["status"], arl.ACTION_STATUS_PROPOSED)
        self.assertIsNone(pending["activity_ref"])
        self.assertNotEqual(pending["activity_ref"], ACTIVITY_ID)

        self.assertEqual(harness.materialize()[1], [])
        context = harness.context(activity=harness.activity())
        with self.assertRaises(ag1.DecisionValidationError) as caught:
            harness.validate_forged_abandon(context=context)
        self.assertIn("PAUSED_ABANDON_WITHHELD_UNPROVEN_SAFE", str(caught.exception))

        self.assertEqual(harness.activity()["status"], ac.STATUS_PAUSED)
        after = harness.action(pending["action_id"])
        self.assertEqual(after["status"], arl.ACTION_STATUS_PROPOSED)
        self.assertIsNone(after["activity_ref"])
        self.assertEqual(after["revision"], pending["revision"])

    # -- negative controls: the remaining doors keep their old predicates ------

    def test_atomic_foreground_withholds_abandon_and_ag2_refuses_it(self):
        harness = self.paused_harness()
        candidate = harness.paused_continue_candidate()
        context = harness.context(
            activity=harness.activity(), interruptibility=ag1.INTERRUPTIBILITY_ATOMIC
        )
        legal = harness.legal(candidate, context, harness.capacity(candidate))
        self.assertNotIn(ag1.DECISION_ABANDON, legal)
        self.assertEqual(legal, [ag1.DECISION_DEFER])

        with self.assertRaises(ag1.DecisionValidationError) as caught:
            harness.validate_forged_abandon(context=context)
        self.assertEqual(caught.exception.failure_class, ag1.FAILURE_CLASS_ATOMIC_VIOLATION)

    def test_capacity_unavailable_does_not_turn_the_withheld_exit_legal(self):
        harness = self.paused_harness()
        candidate = harness.paused_continue_candidate()
        context = harness.context(activity=harness.activity())

        blocked = harness.legal(
            candidate, context, harness.capacity(candidate, exec_now=ag1.EXEC_NOT_NOW)
        )
        self.assertEqual(blocked, [ag1.DECISION_DEFER])
        self.assertNotIn(ag1.DECISION_CONTINUE, blocked)

        # A spent / unavailable budget never becomes a reason to abandon a PAUSED Activity.
        with self.assertRaises(ag1.DecisionValidationError):
            harness.validate_forged_abandon(context=context)
        self.assertEqual(harness.activity()["status"], ac.STATUS_PAUSED)

    def test_active_foreground_keeps_continue_pause_and_abandon(self):
        harness = self.make_harness()
        harness.start()
        _, candidates = harness.materialize()
        self.assertEqual(len(candidates), 1)
        context = harness.context(activity=harness.activity())
        results, allowed = harness.constraints(candidates, context)
        self.assertEqual(len(allowed), 1)
        legal = harness.legal(allowed[0], context, harness.capacity(allowed[0]))
        self.assertIn(ag1.DECISION_CONTINUE, legal)
        self.assertIn(ag1.DECISION_PAUSE, legal)
        self.assertIn(ag1.DECISION_ABANDON, legal)
        self.assertNotIn(ag1.DECISION_RESUME, legal)
        self.assertNotIn(ag1.DECISION_START, legal)

    def test_waiting_foreground_is_not_projected_as_current_activity(self):
        harness = self.make_harness()
        harness.start()
        waited = harness.wait()
        self.assertEqual(waited["status"], ac.STATUS_WAITING)
        candidate_set, candidates = harness.materialize()
        self.assertEqual(candidates, [])
        self.assertEqual(candidate_set.candidate_refs, [])

    def test_withheld_exit_leaves_the_paused_activity_untouched(self):
        harness = self.paused_harness()
        revision_before = int(harness.activity()["revision"])
        self.assertEqual(harness.materialize()[1], [])
        context = harness.context(activity=harness.activity())
        with self.assertRaises(ag1.DecisionValidationError):
            harness.validate_forged_abandon(context=context)

        # No journal transition, no revision move, still PAUSED and still released.
        self.assertEqual(harness.abandon_transitions(), [])
        self.assertEqual(int(harness.activity()["revision"]), revision_before)
        self.assertEqual(harness.activity()["status"], ac.STATUS_PAUSED)
        self.assertIsNone(harness.foreground_ref())

        # LR-2 still refuses a caller that tries to mint the transition on its own.
        with self.assertRaises(ac.WriterCapabilityError):
            harness.activity_command.abandon_activity(
                activity_id=ACTIVITY_ID,
                expected_revision=harness.store_revision(),
                idempotency_key="pausedexit-abandon-withheld",
                source_refs=["decision:pausedexit"],
                decision_ref=ABANDON_DECISION_ID,
                adoption_ref="adp:pausedexit-replayed",
            )
        self.assertEqual(int(harness.activity()["revision"]), revision_before)

    def test_paused_candidate_bound_to_another_activity_is_refused(self):
        harness = self.paused_harness()
        self.assertEqual(harness.materialize()[1], [])

        foreign = ag0.CandidateRecord(
            candidate_id="cand:pausedexitforeign",
            candidate_kind=ag0.CANDIDATE_KIND_CONTINUE_CURRENT,
            source_kind=ag0.SOURCE_CURRENT_ACTIVITY,
            source_ref="actv:pausedexitother",
            availability_status=ag0.CANDIDATE_STATUS_OPEN,
            created_at=STAMP,
            observed_at=STAMP,
            recorded_at=STAMP,
            idempotency_key="cand:pausedexitforeign",
            activity_ref="actv:pausedexitother",
            causal_parent_refs=["actv:pausedexitother"],
            provenance_refs=["actv:pausedexitother"],
        )
        refreshed = harness.materializer.registry.get_adapter(
            ag0.SOURCE_CURRENT_ACTIVITY
        ).refresh(foreign, observed_at=STAMP)
        self.assertNotEqual(refreshed.availability_status, ag0.CANDIDATE_STATUS_OPEN)

        context = harness.context(activity=harness.activity())
        raw = {
            "decision": ag1.DECISION_ABANDON,
            "selected_candidate_id": foreign.candidate_id,
            "reason_codes": [ag1.REASON_ABANDON_NONTERMINAL_CHOSEN],
            "short_rationale": "放弃别的活动。",
            "deferred_candidate_ids": [],
        }
        with self.assertRaises(ag1.DecisionValidationError):
            ag1.DecisionOutputValidator.validate_decision_output(
                raw,
                candidate_set=ag0.CandidateSet(
                    candidate_set_id="cset:pausedexitforeign",
                    subject_id=ag1.GLOBAL_SUBJECT_ID,
                    candidate_refs=[foreign.candidate_id],
                    source_snapshot_refs=[],
                    observed_at=STAMP,
                    built_at=STAMP,
                ),
                candidates_by_id={foreign.candidate_id: foreign},
                constraint_results_by_id={},
                context_snapshot=context,
                capacity_snapshot=harness.capacity(foreign),
            )

    def test_withholding_happens_before_any_ag2_staleness_path(self):
        """The old test drove a real AG-2 stale rejection through the PAUSED ABANDON door.

        That door no longer exists, so the precedence is what is proven here: AG-1 refuses the
        PAUSED target by its own status predicate, before a Decision can reach AG-2 at all.  No
        adoption record is written and the released Activity is unchanged.
        """

        harness = self.paused_harness()
        context = harness.context(activity=harness.activity())
        with self.assertRaises(ag1.DecisionValidationError) as caught:
            harness.validate_forged_abandon(context=context)
        self.assertIn("PAUSED_ABANDON_WITHHELD_UNPROVEN_SAFE", str(caught.exception))

        state = harness.integration_store.load_state()
        self.assertEqual(state.get("adoptions_by_id") or {}, {})
        self.assertEqual(harness.activity()["status"], ac.STATUS_PAUSED)
        self.assertIsNone(harness.foreground_ref())
        self.assertEqual(harness.abandon_transitions(), [])

    def test_lr2_refuses_abandon_without_an_owner_grant(self):
        harness = self.paused_harness()
        # LR-2's own admission issuer refuses before any transition: a caller cannot
        # mint or self-report the Decision/Adoption provenance ABANDON requires.
        with self.assertRaises(ac.WriterCapabilityError):
            harness.activity_command.abandon_activity(
                activity_id=ACTIVITY_ID,
                expected_revision=harness.store_revision(),
                idempotency_key="pausedexit-no-grant",
                source_refs=["decision:pausedexit"],
            )
        self.assertEqual(harness.activity()["status"], ac.STATUS_PAUSED)

    def test_projection_does_not_mint_eligibilities_conditions_or_agenda_items(self):
        harness = self.paused_harness()
        harness.materialize()
        state = harness.waiting.eligibility_store.load_state()
        self.assertEqual(state.get("eligibilities") or {}, {})
        self.assertEqual(harness.waiting.condition_store.load_state().get("conditions") or {}, {})
        self.assertEqual(harness.waiting.agenda_store.load_state().get("agenda_items") or {}, {})
        self.assertEqual(harness.activity()["status"], ac.STATUS_PAUSED)


if __name__ == "__main__":
    unittest.main(verbosity=2)

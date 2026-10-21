"""v0.4 一天生活 B：读尽/撤权/no_change 的只读步骤就绪、具名 NO_STEP_AVAILABLE、
真实采纳核对的 PAUSE/ABANDON、有限可失效的再考虑抑制、以及 W 的 ``bodyact:`` 接入。

只证明机制，不冒充自主
----------------------
本文件的"决策"来自脚本化替身（``ScriptedCoordinator``）：它按事先写死的 Decision 作答，
因此这里证明的是"cycle 怎样处理一个已经提交的 Decision"，**不是**"她自己选了什么"。
0 模型调用、0 网络、0 支付、0 真实实例、0 真实 owner 数据。

范围（每条对应一个真实阻塞）
----------------------------
1. 材料读尽 / 有页可读 / 无前台：``observed_facts`` 里那一个 ASCII 布尔键
   ``current_step_unavailable_true`` 只在**绑定成立且确实无步骤**时出现，且只描述当前 Activity；
2. 有前台却无可执行步骤时选择 CONTINUE → ``NO_STEP_AVAILABLE``（blocked/unavailable），
   既不是 ``ACTED`` 也不是假的 ``NO_ACTION``；
3. PAUSE/ABANDON 只在 owner 真的应用**并且**自己的记录确实是那个状态时才报"已停"；
   被拒是 ``ADOPTION_REJECTED``，应用了却仍 ACTIVE 是 ``STEP_UNSETTLED``；
4. 同一状态（target + revision + 原因）只花一次请求，状态真变了就自动解除，显式解除只认
   自己的 ref（无关事件不能把安静状态变回花钱的请求）；
5. 作品 ``no_change`` 落盘且绑定版本，下一轮读回 ``WORK_NO_CHANGE_CONFIRMED``；
6. ``bodyact:`` 的解析/步骤/完成判据全部来自 W 的 ``life_body_action_targets``：
   POSE 看真实 snapshot 的 pose+orientation，REST 只看真实会话状态，命令成功不算完成；
7. 旧目标被 PAUSED 时不再作为新的个人机会出现（resume 资格归既有 owner 路径）。
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import artifact_continuation_source as acs  # noqa: E402
import autonomous_life_cycle as alc  # noqa: E402
import life_body_action_targets as lbat  # noqa: E402
import life_step_readiness as lsr  # noqa: E402

OBSERVED_AT = "2026-10-26T08:00:00+00:00"
READING_ID = "artifact:read-1"
DOC_ID = "artifact:doc-1"
WORK_ID = "artifact:work-1"
ACTIVITY_REF = "activity:read-1"
FACT_KEY = lsr.STEP_UNAVAILABLE_FACT_KEY
POSE_REF = "bodyact:pose:seated:north"
REST_START_REF = "bodyact:rest:start:rest"

#: 正文特征串：证明它从不进入 observed_facts / 报告。
MATERIAL_SENTINEL = "窗外是一条街"


def _situation(*, state: str, available: bool, version: str = "rev-1",
               next_page=1) -> dict:
    return {
        "state": state,
        "available": bool(available),
        "exhausted": state == "EXHAUSTED",
        "detail": "read out" if state == "EXHAUSTED" else "",
        "material": {"version_id": version, "pages": 3, "title": "材料"},
        "position": {"page": 1 if available else 3, "next_page": next_page,
                     "needs_reread": False},
        "annotations": {"note": 0, "unclear": 0},
        "understanding": "UNKNOWN",
    }


EXHAUSTED = _situation(state="EXHAUSTED", available=False, next_page=None)
AVAILABLE = _situation(state="AVAILABLE", available=True)


class _ActivityRead:
    """The Activity owner's read port, over records the test itself declares."""

    def __init__(self, activities=None, foreground=None):
        self.activities = {str(k): dict(v) for k, v in (activities or {}).items()}
        self.foreground = foreground

    def get_current_life_view(self):
        kind = (self.activities.get(str(self.foreground)) or {}).get("activity_kind")
        return {"foreground_activity_ref": self.foreground, "foreground_activity_kind": kind}

    def get_activity(self, activity_id):
        record = self.activities.get(str(activity_id))
        return dict(record) if record else None

    def list_open_activities(self):
        return [dict(row) for row in self.activities.values()
                if str(row.get("status") or "") in {"ACTIVE", "PAUSED", "WAITING"}]


class _CandidateRead:
    def __init__(self, candidates=None):
        self.candidates = dict(candidates or {})

    def get_candidate(self, candidate_id):
        return dict(self.candidates.get(str(candidate_id)) or {}) or None


class _ActionRead:
    def __init__(self, receipts=None):
        self.receipts = list(receipts or [])

    def list_receipts(self, action_id):
        return [dict(row) for row in self.receipts if row.get("action_id") == action_id]

    def list_actions(self, status=None):
        return []


class _ProgressPort:
    def __init__(self):
        self.evidence = []

    def ingest_progress_evidence(self, evidence):
        self.evidence.append(evidence)
        return {"progress_state": {"progress_state": "IN_PROGRESS"},
                "checkpoint_update": {"checkpoint_update_id": "cp:1", "checkpoint_ref": "cp:1"}}


class _Coordinator:
    """A scripted Decision/Adoption answer -- it is not a model and never claims to be."""

    def __init__(self, *, decision: str = "NO_ACTION", adoption_status: str = "APPLIED",
                 rejection: str = "", candidate_ref: str = ""):
        self.decision = decision
        self.adoption_status = adoption_status
        self.rejection = rejection
        self.candidate_ref = candidate_ref
        self.calls: list[dict] = []
        self.handoffs: list[dict] = []

    def run_decision_and_adoption_cycle(self, **kwargs):
        self.calls.append(kwargs)
        record = SimpleNamespace(
            decision=self.decision,
            selected_candidate_ref=(self.candidate_ref or None),
            reason_codes=[], short_rationale="scripted")
        adoption = SimpleNamespace(adoption_status=self.adoption_status,
                                   rejection_reason_code=(self.rejection or None))
        return {"correlation_id": "corr:1", "decision_result": {"decision_record": record},
                "adoption_result": {"adoption_record": adoption},
                "integration_record": SimpleNamespace(activity_ref=ACTIVITY_REF,
                                                      integration_id="integ:1"),
                "step_execution_result": {}}

    def emit_experience_handoff(self, **kwargs):
        self.handoffs.append(kwargs)
        return {"dispatch_result": {"deliveries": []}, "handoff_event": None}


class _OpportunitySource:
    """AG-0's world-opportunity source, as the cycle feeds it (recording is idempotent by design)."""

    def __init__(self):
        self.recorded: list[dict] = []

    def record_observed_opportunity(self, item):
        self.recorded.append(dict(item))


class _Runtime:
    def __init__(self, *, state_root, activities=None, foreground=None, candidates=None,
                 coordinator=None, receipts=None):
        self.state_root = Path(state_root)
        self.activity_read = _ActivityRead(activities, foreground)
        self.candidate_read = _CandidateRead(candidates)
        self.action_read = _ActionRead(receipts)
        self.coordinator = coordinator or _Coordinator()
        self.ag2_progress_port = _ProgressPort()
        self.kill_switches = SimpleNamespace(to_dict=lambda: {})
        self.candidate_registry = SimpleNamespace(list_adapters=lambda: [])
        self.world_opportunity_source = _OpportunitySource()
        self.artifact_action_adapter = None
        self.world_action_adapter = None
        self.foreground = foreground

    def canonical_state(self):
        return {"foreground_activity_ref": self.foreground}


def _reading_activity(status: str = "ACTIVE", ref: str = ACTIVITY_REF) -> dict:
    """The reading Activity as the owner records it: refs, the adoption lineage, the checkpoint."""

    return {"activity_id": ref, "activity_ref": ref,
            "activity_kind": alc.READING_ACTIVITY_KIND, "status": status,
            "subject_refs": [READING_ID, DOC_ID], "target_ref": READING_ID,
            "source_refs": ["dec:1", "adp:1", "cand:read"],
            "checkpoint_ref": "chk:stage:001", "origin_ref": READING_ID}


def _work_activity(status: str = "ACTIVE", ref: str = "activity:work-1") -> dict:
    return {"activity_id": ref, "activity_ref": ref,
            "activity_kind": "CONTINUE_PERSONAL_ARTIFACT", "status": status,
            "subject_refs": [WORK_ID], "target_ref": WORK_ID}


def _work_activity_with_lineage(status: str = "ACTIVE", ref: str = "activity:work-1") -> dict:
    """The work Activity exactly as the owner records it: refs plus the adoption lineage."""

    record = _work_activity(status=status, ref=ref)
    record["subject_refs"] = [WORK_ID, "artifactversion:h1"]
    record["source_refs"] = ["dec:1", "adp:1", "cand:work",
                             "artifact:artifact:%s" % WORK_ID.split(":", 1)[-1]]
    record["origin_ref"] = WORK_ID
    record["checkpoint_ref"] = "chk:stage:001"
    return record


def _body_activity(status: str = "ACTIVE", ref: str = "activity:body-1") -> dict:
    """The POSE Activity from the parent's chain: the *place* is its first subject ref."""

    return {"activity_id": ref, "activity_ref": ref,
            "activity_kind": alc.BODY_ACTION_ACTIVITY_KIND, "status": status,
            "subject_refs": ["world_fact:place:street_1", "world_fact:stay:home:REST:300",
                             "world_fact:bodyact:pose:seated:south",
                             "world_fact:bodyact:pose:lying:south",
                             "world_fact:bodyact:pose:transitioning:south"],
            "source_refs": ["dec:1", "adp:1", "cand:body", "wopp:pose:home:r1:lying"],
            "origin_ref": "world_fact:place:home", "checkpoint_ref": "chk:stage:001"}


class _OwnerPort:
    """The artifact owner's own id spelling: its reads match ``artifact:<hex>`` exactly.

    A bare identity is a ``NOT_FOUND`` -- which is precisely the trap the cycle fell into: it
    stripped the prefix, got no head, and every work state degraded to "no revision".
    """

    def __init__(self, heads=None):
        self.heads = {str(key): str(value) for key, value in (heads or {}).items()}
        self.calls: list[str] = []

    def available(self):
        return True

    def read_head(self, artifact_id):
        self.calls.append(str(artifact_id))
        version = self.heads.get(str(artifact_id))
        if version is None:
            from life_artifact_port import ArtifactOwnerRefused
            raise ArtifactOwnerRefused("NOT_FOUND", "ARTIFACT_NOT_FOUND", str(artifact_id))
        return {"version_id": version, "version_number": 1}

    def resume_point(self, artifact_id):
        from life_artifact_port import ArtifactResumePoint
        head = self.read_head(artifact_id)
        return ArtifactResumePoint(artifact_id=str(artifact_id), title="w",
                                   head_version_id=head["version_id"], version_number=1,
                                   content="x", lifecycle="ACTIVE", revision=1)


class _ObservingProvider:
    """The World observation seam double: each call reports the *next* state the owner holds.

    ``calls`` is part of the contract under test: a body step must re-read the owner *after* the
    action, so consuming a second observation is exactly what proves the fact is fresh.
    """

    def __init__(self, observations, *, rest_entries=None, fail=False, opportunities=None):
        self._observations = [dict(item) for item in (observations or [{}])] or [{}]
        self._rest = list(rest_entries or [])
        self._opportunities = [dict(item) for item in (opportunities or [])]
        self.fail = bool(fail)
        self.calls = 0
        self.last_observation = dict(self._observations[0])
        self.last_available_actions = ({"actions": [self._rest[0]]} if self._rest else None)
        self.last_catalog = None

    def __call__(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("world gateway unreachable")
        index = min(self.calls - 1, len(self._observations) - 1)
        self.last_observation = dict(self._observations[index])
        entry = self._rest[min(index, len(self._rest) - 1)] if self._rest else None
        self.last_available_actions = {"actions": [entry]} if entry else None
        return [dict(item) for item in self._opportunities]


class DayLifeContinuityTest(unittest.TestCase):
    """The foreground step's readiness, the named no-step result, and the bounded ledger."""

    def _root(self) -> str:
        # Kept on purpose (no TemporaryDirectory cleanup): a failing run must leave its state behind.
        return tempfile.mkdtemp(prefix="nyairov04b-", dir="/tmp")
    def _cycle(self, *, activities=None, foreground=None, situation=None,
               decision="NO_ACTION", adoption_status="APPLIED", rejection="",
               candidate_ref="", max_rounds=12, state_root=None, artifact_port=None,
               world_opportunities=None, candidates=None, provider=None, work_id=None):
        root = state_root or self._root()
        runtime = _Runtime(state_root=root, activities=activities,
                           foreground=foreground, coordinator=_Coordinator(
                               decision=decision, adoption_status=adoption_status,
                               rejection=rejection, candidate_ref=candidate_ref),
                           candidates=candidates)
        cycle = alc.AutonomousLifeCycle(
            runtime, enabled=True, max_rounds_per_day=max_rounds, env={},
            artifact_port=artifact_port, work_artifact_id=work_id)
        cycle.reading_artifact_id = READING_ID
        cycle.private_items_artifact_id = DOC_ID
        if candidate_ref and candidate_ref not in runtime.candidate_read.candidates:
            # The committed Decision's own candidate record: it names the reading material.
            runtime.candidate_read.candidates[candidate_ref] = {
                "candidate_id": candidate_ref, "target_refs": [READING_ID, DOC_ID]}
        if provider is not None:
            cycle.world_opportunity_provider = provider
        if world_opportunities is not None:
            cycle.last_world_opportunities = [dict(item) for item in world_opportunities]
        if situation is not None:
            cycle.private_material_situation = lambda refresh=False, _s=situation: dict(_s)
        return cycle

    def _round(self, cycle, *, trigger_ref="evt:test:1"):
        return cycle.evaluate_once(trigger_ref=trigger_ref, observed_at=OBSERVED_AT)

    # -- 1. the sourced fact ---------------------------------------------------------

    def test_an_exhausted_reading_material_publishes_the_bound_step_unavailable_fact(self):
        cycle = self._cycle(activities={ACTIVITY_REF: _reading_activity()},
                            foreground=ACTIVITY_REF, situation=EXHAUSTED,
                            decision="CONTINUE", candidate_ref="cand:read")
        out = self._round(cycle)

        readiness = out["step_readiness"]
        self.assertIs(readiness["available"], False, readiness)
        self.assertEqual(readiness["reason_code"], "READING_EXHAUSTED", readiness)
        self.assertTrue(readiness["bound"], readiness)
        self.assertEqual(readiness["activity_ref"], ACTIVITY_REF, readiness)
        self.assertEqual(readiness["target_ref"], READING_ID, readiness)
        self.assertEqual(readiness["source_revision"], "rev-1", readiness)

        facts = cycle.runtime.coordinator.calls[-1]["observed_facts"]
        self.assertIs(facts.get(FACT_KEY), True, facts)
        self.assertEqual(out["step_readiness_facts"], [FACT_KEY])
        # The fact carries no material text and no page number of its own.
        self.assertNotIn(MATERIAL_SENTINEL, json.dumps(out, ensure_ascii=False))

    def test_a_page_left_publishes_no_step_unavailable_fact(self):
        cycle = self._cycle(activities={ACTIVITY_REF: _reading_activity()},
                            foreground=ACTIVITY_REF, situation=AVAILABLE,
                            decision="NO_ACTION")
        out = self._round(cycle)

        self.assertIs(out["step_readiness"]["available"], True, out["step_readiness"])
        self.assertNotIn("step_readiness_facts", out)
        self.assertNotIn(FACT_KEY, cycle.runtime.coordinator.calls[-1]["observed_facts"])

    def test_no_foreground_activity_publishes_no_fact(self):
        cycle = self._cycle(activities={}, foreground=None, situation=AVAILABLE,
                            decision="NO_ACTION")
        out = self._round(cycle)

        self.assertEqual(out["step_readiness"]["reason_code"], "NO_FOREGROUND_ACTIVITY",
                         out["step_readiness"])
        self.assertFalse(out["step_readiness"]["bound"], out["step_readiness"])
        self.assertNotIn(FACT_KEY, cycle.runtime.coordinator.calls[-1]["observed_facts"])

    # -- 2. no step is named, never ACTED and never a fake NO_ACTION -----------------

    def test_a_continue_with_no_step_is_named_no_step_available_and_not_acted(self):
        cycle = self._cycle(activities={ACTIVITY_REF: _reading_activity()},
                            foreground=ACTIVITY_REF, situation=EXHAUSTED,
                            decision="CONTINUE", candidate_ref="cand:read")
        out = self._round(cycle)

        self.assertEqual(out["outcome"], alc.OUTCOME_NO_STEP_AVAILABLE, out)
        self.assertNotEqual(out["outcome"], alc.OUTCOME_ACTED)
        self.assertNotEqual(out["outcome"], alc.OUTCOME_NO_ACTION)
        self.assertEqual(out["no_step"]["reason_code"], "READING_STATE_EXHAUSTED", out["no_step"])
        self.assertEqual(out["no_step"]["activity_ref"], ACTIVITY_REF, out["no_step"])
        self.assertEqual(out["llm_calls"], 1)
        self.assertIs(out["owner_evidence"]["owner_version_created"], False, out["owner_evidence"])
        # Nothing was written: the round never reached an owner write at all.
        self.assertEqual(cycle.runtime.ag2_progress_port.evidence, [])

    # -- 3. an exit is reported only when the owner really applied it ----------------

    def test_a_refused_pause_is_reported_as_adoption_rejected_not_paused(self):
        cycle = self._cycle(activities={ACTIVITY_REF: _reading_activity()},
                            foreground=ACTIVITY_REF, situation=EXHAUSTED,
                            decision="PAUSE", adoption_status="REJECTED_STALE",
                            rejection="SELECTED_CANDIDATE_STALE")
        out = self._round(cycle)

        self.assertEqual(out["outcome"], alc.OUTCOME_ADOPTION_REJECTED, out)
        self.assertNotEqual(out["outcome"], alc.OUTCOME_PAUSED)
        self.assertIs(out["exit"]["applied"], False, out["exit"])
        self.assertEqual(out["exit"]["owner_status"], "ACTIVE", out["exit"])

    def test_an_applied_exit_that_the_owner_still_reports_active_is_unsettled(self):
        cycle = self._cycle(activities={ACTIVITY_REF: _reading_activity()},
                            foreground=ACTIVITY_REF, situation=EXHAUSTED,
                            decision="ABANDON", adoption_status="APPLIED")
        out = self._round(cycle)

        self.assertEqual(out["outcome"], alc.OUTCOME_STEP_UNSETTLED, out)
        self.assertNotEqual(out["outcome"], alc.OUTCOME_ABANDONED)
        self.assertIs(out["exit"]["applied"], True, out["exit"])
        self.assertEqual(out["exit"]["owner_status"], "ACTIVE", out["exit"])

    def test_an_applied_pause_that_the_owner_reports_paused_is_reported_paused(self):
        cycle = self._cycle(activities={ACTIVITY_REF: _reading_activity(status="PAUSED")},
                            foreground=ACTIVITY_REF, situation=EXHAUSTED,
                            decision="PAUSE", adoption_status="APPLIED")
        out = self._round(cycle)

        self.assertEqual(out["outcome"], alc.OUTCOME_PAUSED, out)
        self.assertIs(out["exit"]["applied"], True, out["exit"])
        self.assertEqual(out["exit"]["owner_status"], "PAUSED", out["exit"])

    # -- 4. bounded, persistent, releasable reconsideration -------------------------

    def test_the_same_no_step_state_is_considered_once_and_then_quiet(self):
        cycle = self._cycle(activities={ACTIVITY_REF: _reading_activity()},
                            foreground=ACTIVITY_REF, situation=EXHAUSTED,
                            decision="CONTINUE", candidate_ref="cand:read")
        first = self._round(cycle, trigger_ref="evt:test:first")
        self.assertEqual(first["outcome"], alc.OUTCOME_NO_STEP_AVAILABLE, first)
        self.assertEqual(len(cycle.runtime.coordinator.calls), 1)

        second = self._round(cycle, trigger_ref="evt:test:second")
        self.assertEqual(second["outcome"], alc.OUTCOME_QUIET, second)
        self.assertEqual(second["llm_calls"], 0, second)
        self.assertIs(second["reconsideration"]["blocked"], True, second["reconsideration"])
        self.assertEqual(second["reconsideration"]["reason_code"],
                         "SAME_STATE_ALREADY_CONSIDERED", second["reconsideration"])
        # No second request was spent, and no Decision was replayed.
        self.assertEqual(len(cycle.runtime.coordinator.calls), 1)
        ledger = json.loads(Path(cycle.state_root / lsr.RECONSIDERATION_FILENAME)
                            .read_text(encoding="utf-8"))
        self.assertEqual(len(ledger["entries"]), 1, ledger)
        entry = ledger["entries"][0]
        self.assertEqual(entry["target_ref"], READING_ID, entry)
        self.assertEqual(entry["source_revision"], "rev-1", entry)
        self.assertEqual(entry["outcome"], alc.OUTCOME_NO_STEP_AVAILABLE, entry)
        # Only refs/instants/counts: no material text, no rationale.
        self.assertNotIn(MATERIAL_SENTINEL, json.dumps(ledger, ensure_ascii=False))

    def test_a_new_material_revision_lifts_the_suppression(self):
        cycle = self._cycle(activities={ACTIVITY_REF: _reading_activity()},
                            foreground=ACTIVITY_REF, situation=EXHAUSTED,
                            decision="CONTINUE", candidate_ref="cand:read")
        self._round(cycle, trigger_ref="evt:test:first")
        quiet = self._round(cycle, trigger_ref="evt:test:second")
        self.assertEqual(quiet["outcome"], alc.OUTCOME_QUIET, quiet)

        # A real change: the material is re-versioned and has a page again.
        cycle.private_material_situation = lambda refresh=False: _situation(
            state="AVAILABLE", available=True, version="rev-2", next_page=1)
        third = self._round(cycle, trigger_ref="evt:test:third")
        self.assertNotEqual(third["outcome"], alc.OUTCOME_QUIET, third)
        self.assertEqual(len(cycle.runtime.coordinator.calls), 2, third)
        self.assertIs(third["step_readiness"]["available"], True, third["step_readiness"])
        self.assertEqual(third["step_readiness"]["source_revision"], "rev-2",
                         third["step_readiness"])

    def test_an_explicit_release_only_honours_its_own_ref(self):
        cycle = self._cycle(activities={ACTIVITY_REF: _reading_activity()},
                            foreground=ACTIVITY_REF, situation=EXHAUSTED,
                            decision="CONTINUE", candidate_ref="cand:read")
        self._round(cycle, trigger_ref="evt:test:first")
        self.assertEqual(len(cycle.reconsideration.entries()), 1)

        unrelated = cycle.release_reconsideration(refs=["artifact:somebody-else"])
        self.assertEqual(unrelated["released"], [])
        self.assertEqual(len(cycle.reconsideration.entries()), 1)
        self.assertEqual(len(cycle.reconsideration.situations()), 1)

        released = cycle.release_reconsideration(refs=[READING_ID])
        # The ledger has two shapes for this one target: the keyed no-step entry and the
        # whole-situation row that actually keeps the round quiet.  A release that names the target
        # must drop *both* -- releasing only one would leave the state suppressed by the other, and
        # "explicit release" would be a lie.  (This assertion predates the situation rows; the
        # pre-situation ledger had one record per target.)
        self.assertEqual(len(released["released"]), 2, released)
        self.assertEqual(cycle.reconsideration.entries(), [])
        self.assertEqual(cycle.reconsideration.situations(), [])

    def test_a_work_no_change_is_persistent_and_bound_to_its_revision(self):
        cycle = self._cycle(activities={"activity:work-1": _work_activity()},
                            foreground="activity:work-1", situation=None,
                            decision="NO_ACTION")
        # The owner's own head for this work, as the cycle reads it.
        cycle._readiness_revision = lambda activity, target: "v1"
        cycle._round_readiness = lsr.StepReadiness(
            available=True, reason_code=lsr.REASON_ARTIFACT_STEP_AVAILABLE,
            activity_ref="activity:work-1", activity_kind="CONTINUE_PERSONAL_ARTIFACT",
            target_ref=WORK_ID, source_revision="v1")
        out = cycle._work_no_change({"step_path": "AR0"}, llm_calls=1)
        self.assertEqual(out["outcome"], alc.OUTCOME_NO_ACTION, out)
        self.assertTrue(out["work_no_change"]["recorded"], out["work_no_change"])

        # Read back on the *same* revision: named, not "maybe".
        same = cycle.step_readiness(act=True)
        self.assertIs(same.available, False, same.to_dict())
        self.assertEqual(same.reason_code, lsr.REASON_WORK_NO_CHANGE, same.to_dict())
        self.assertEqual(same.fact_keys(), {FACT_KEY: True}, same.to_dict())

        # A new head is a different state, so the step is available again.
        cycle._readiness_revision = lambda activity, target: "v2"
        newer = cycle.step_readiness(act=True)
        self.assertIs(newer.available, True, newer.to_dict())
        self.assertEqual(newer.reason_code, lsr.REASON_ARTIFACT_STEP_AVAILABLE, newer.to_dict())
        self.assertEqual(newer.fact_keys(), {}, newer.to_dict())

    # -- 6. W's bodyact targets ----------------------------------------------------

    def _world_cycle(self, *, observations=None, rest_entries=None, candidate_refs=None,
                     fail_observation=False, pre_observation=None):
        """A cycle wired to the observation seam double.

        ``observations`` are the states the owner reports on each successive read; ``pre_observation``
        is the snapshot the *round* already took before the step was submitted (defaults to the first
        one), so a test can prove the classifier does not reuse it.
        """

        cycle = self._cycle(activities={}, foreground=None, situation=None)
        cycle.world_opportunity_provider = _ObservingProvider(
            observations, rest_entries=rest_entries, fail=fail_observation)
        cycle.last_world_observation = dict(
            pre_observation if pre_observation is not None
            else (observations or [{}])[0])
        cycle.last_world_opportunities = [{"opportunity_id": "wopp:1",
                                           "target_refs": list(candidate_refs or [POSE_REF]),
                                           "location_ref": "place:home"}]
        cycle.runtime.candidate_read.candidates["cand:body"] = {
            "candidate_id": "cand:body", "target_refs": list(candidate_refs or [POSE_REF])}
        return cycle

    def test_a_body_pose_step_is_built_by_w_and_completed_only_by_the_snapshot(self):
        standing = {"location": {"place_id": "home"}, "world_revision": 7,
                    "pose": "standing", "orientation": "north"}
        seated = {"location": {"place_id": "home"}, "world_revision": 8,
                  "pose": "seated", "orientation": "north"}
        cycle = self._world_cycle(observations=[standing, seated])
        decision = SimpleNamespace(decision="START", selected_candidate_ref="cand:body")

        spec = cycle._world_step_spec(decision)
        self.assertEqual(spec["step_kind"], "WORLD_ACTION", spec)
        self.assertEqual(spec["payload_spec"]["verb"], lbat.POSE_VERB, spec)
        self.assertEqual(spec["payload_spec"]["target"], POSE_REF, spec)
        self.assertIn("home", spec["idempotency_key"], spec)
        self.assertIn(POSE_REF, spec["idempotency_key"], spec)

        # The command "succeeded", but the owner's own snapshot still says standing/north.
        base = {"activity_ref": ACTIVITY_REF, "observed_at": OBSERVED_AT}
        out = cycle._classify_body_action(
            dict(base), verb=lbat.POSE_VERB,
            action={"action_id": "act:1", "frozen_payload": {"verb": "POSE", "target": POSE_REF}})
        self.assertEqual(out["outcome"], alc.OUTCOME_STEP_UNSETTLED, out)
        self.assertIs(out["body_action"]["satisfied"], False, out["body_action"])
        self.assertEqual(cycle.runtime.ag2_progress_port.evidence, [])

        # The owner's own *fresh* snapshot shows the pose the Decision asked for (second read).
        done = cycle._classify_body_action(
            dict(base), verb=lbat.POSE_VERB,
            action={"action_id": "act:2", "frozen_payload": {"verb": "POSE", "target": POSE_REF}})
        self.assertEqual(done["outcome"], alc.OUTCOME_ACTED, done)
        evidence = cycle.runtime.ag2_progress_port.evidence[-1]
        self.assertEqual(evidence.observed_facts[lbat.FACT_KEY_BODY_POSE],
                         lbat.POSE_FACT % ("seated", "north"), evidence.observed_facts)

    def test_a_body_pose_step_re_reads_the_world_after_the_action_and_never_the_target(self):
        """The parent's real-owner chain caught this: the fact must come from a *fresh* read.

        Before the fix the classifier answered with the snapshot the round took *before* the step
        was submitted, so a committed ``POSE`` that really happened was reported ``STEP_UNSETTLED``.
        The target says what was *asked for*; only the owner's own post-action observation says what
        is true.  A re-read that fails stays unknown -- it is never answered by the old snapshot or
        by the target ref.
        """

        before = {"location": {"place_id": "home"}, "world_revision": 1,
                  "pose": "standing", "orientation": "south"}
        after = {"location": {"place_id": "home"}, "world_revision": 2,
                 "pose": "lying", "orientation": "south"}
        lying_ref = "bodyact:pose:lying:south"
        action = {"action_id": "act:pose",
                  "frozen_payload": {"verb": lbat.POSE_VERB, "target": lying_ref}}
        base = {"activity_ref": ACTIVITY_REF, "observed_at": OBSERVED_AT}

        cycle = self._world_cycle(observations=[after], pre_observation=before,
                                  candidate_refs=[lying_ref])
        out = cycle._classify_body_action(dict(base), verb=lbat.POSE_VERB, action=action)

        # The pre-action snapshot (standing/south) is not the answer; the fresh read (lying/south) is.
        self.assertEqual(out["outcome"], alc.OUTCOME_ACTED, out)
        self.assertEqual(out["body_action"]["observed"],
                         {"pose": "lying", "orientation": "south"}, out["body_action"])
        self.assertIs(out["body_action"]["observation"]["re_read"], True, out["body_action"])
        self.assertEqual(out["body_action"]["observation"]["world_revision"], 2,
                         out["body_action"])
        self.assertGreaterEqual(cycle.world_opportunity_provider.calls, 1)
        evidence = cycle.runtime.ag2_progress_port.evidence[-1]
        self.assertEqual(evidence.observed_facts[lbat.FACT_KEY_BODY_POSE],
                         lbat.POSE_FACT % ("lying", "south"), evidence.observed_facts)

        # A failed re-read is unknown: the old snapshot and the target are both refused as evidence.
        broken = self._world_cycle(observations=[after], pre_observation=before,
                                   candidate_refs=[lying_ref], fail_observation=True)
        unknown = broken._classify_body_action(dict(base), verb=lbat.POSE_VERB, action=action)
        self.assertEqual(unknown["outcome"], alc.OUTCOME_STEP_UNSETTLED, unknown)
        self.assertIs(unknown["body_action"]["satisfied"], False, unknown["body_action"])
        self.assertIs(unknown["body_action"]["observation"]["re_read"], False,
                      unknown["body_action"])
        self.assertEqual(unknown["body_action"]["observed"],
                         {"pose": None, "orientation": None}, unknown["body_action"])
        self.assertIn("could not be re-read", unknown["detail"])
        self.assertEqual(broken.runtime.ag2_progress_port.evidence, [])

    def test_a_rest_step_is_judged_by_the_session_state_and_never_by_recovery(self):
        rest_entry = {"verb": "REST", "available": True,
                      "current": {"pose": "seated", "recovery_state": "inactive",
                                  "travelling": False, "rest_surface": "bed"}}
        cycle = self._world_cycle(observations=[{"location": {"place_id": "home"},
                                                "world_revision": 3,
                                                "pose": "seated", "orientation": "north"}],
                                  rest_entries=[rest_entry], candidate_refs=[REST_START_REF])
        base = {"activity_ref": ACTIVITY_REF, "observed_at": OBSERVED_AT}
        action = {"action_id": "act:9",
                  "frozen_payload": {"verb": "REST", "target": REST_START_REF}}

        # The REST command committed, but the session is not open: not a completion.
        refused = cycle._classify_body_action(dict(base), verb=lbat.REST_VERB, action=action)
        self.assertEqual(refused["outcome"], alc.OUTCOME_STEP_UNSETTLED, refused)
        self.assertIs(refused["body_action"]["satisfied"], False, refused["body_action"])
        self.assertEqual(refused["body_action"]["observed"],
                         {"recovery_state": "inactive"}, refused["body_action"])

        # The owner's own session state says the session really opened.
        rest_entry["current"]["recovery_state"] = "active"
        done = cycle._classify_body_action(dict(base), verb=lbat.REST_VERB, action=action)
        self.assertEqual(done["outcome"], alc.OUTCOME_ACTED, done)
        evidence = cycle.runtime.ag2_progress_port.evidence[-1]
        self.assertEqual(evidence.observed_facts[lbat.FACT_KEY_BODY_RECOVERY],
                         lbat.RECOVERY_FACT % lbat.RECOVERY_STATE_ACTIVE,
                         evidence.observed_facts)
        # Nothing in the round claims a recovery *effect*, only the session state.
        self.assertNotIn("recovered", json.dumps(done["body_action"], ensure_ascii=False))

    def test_a_body_act_adoption_names_w_fact_key_and_expected_value(self):
        cycle = self._world_cycle(snapshot={"location": {"place_id": "home"},
                                            "world_revision": 5,
                                            "pose": "standing", "orientation": "north"})
        decision = SimpleNamespace(decision="START", selected_candidate_ref="cand:body")
        spec = cycle._world_adoption_spec(cycle.last_world_opportunities, decision=decision)

        self.assertEqual(spec["activity_kind"], alc.BODY_ACTION_ACTIVITY_KIND, spec)
        contract = spec["completion_contract_spec"]
        self.assertEqual(contract["criteria"]["fact_key"], lbat.FACT_KEY_BODY_POSE, contract)
        self.assertEqual(contract["criteria"]["expected_value"],
                         lbat.POSE_FACT % ("seated", "north"), contract)
        self.assertEqual(contract["criteria_kind"], "WORLD_FACT_MATCH", contract)
        self.assertIn("world_fact:%s" % POSE_REF, spec["target_refs"], spec)

    # -- 7. a paused target is resume's, not a fresh opportunity -------------------

    def test_a_paused_target_is_not_offered_as_a_fresh_personal_opportunity(self):
        # The ManagedArtifact owner mints bare hex ids; ``artifact:`` is added by the ref spelling.
        paused_id, other_id = "work-1", "other"

        class _Port:
            def available(self):
                return True

            def list_artifacts(self):
                return [{"artifact_id": paused_id, "lifecycle": "ACTIVE"},
                        {"artifact_id": other_id, "lifecycle": "ACTIVE"}]

            def resume_point(self, artifact_id):
                from life_artifact_port import ArtifactResumePoint
                return ArtifactResumePoint(
                    artifact_id=artifact_id, title="t", head_version_id="v1",
                    version_number=1, content="x", lifecycle="ACTIVE", revision=1)

        source = acs.ArtifactContinuationSource(
            _Port(), subject_id="subject:test",
            paused_target_provider=lambda: {"artifact:%s" % paused_id})
        items, _snap, _counts = source.discover(observed_at=OBSERVED_AT)

        offered = [str(item["source_ref"]) for item in items]
        self.assertEqual(offered, ["artifact:%s" % other_id], offered)
        self.assertEqual(source.last_paused_skip_reasons,
                         {paused_id: "PAUSED_TARGET_REQUIRES_RESUME"},
                         source.last_paused_skip_reasons)

        # With no provider wired the source behaves exactly as before.
        plain = acs.ArtifactContinuationSource(_Port(), subject_id="subject:test")
        plain_items, _snap2, _counts2 = plain.discover(observed_at=OBSERVED_AT)
        self.assertEqual([str(item["source_ref"]) for item in plain_items],
                         ["artifact:%s" % paused_id, "artifact:%s" % other_id])


    # -- 8. the parent's real-owner chain (gen3) regressions --------------------------

    def test_a_no_change_on_a_new_start_is_recorded_against_the_real_owner_head(self):
        """A ``no_change`` during START has no Activity yet -- it must still bind target + head.

        The parent's chain hit this: the writer answered ``no_change`` while the START was being
        adopted, the round had no foreground, so nothing was recorded and the next round asked the
        same question again.  The owner's ids are spelled ``artifact:<hex>`` and its reads match that
        spelling exactly, so the head is read with the owner's own ref -- a bare id is ``NOT_FOUND``
        and would silently degrade every work state to "no revision".
        """

        port = _OwnerPort({"artifact:work-1": "artifact-version:h1"})
        cycle = self._cycle(activities={}, foreground=None, situation=None,
                            artifact_port=port)
        cycle._round_readiness = lsr.StepReadiness(
            available=None, reason_code=lsr.REASON_NO_FOREGROUND)
        cycle._round_work_binding = {"target_ref": WORK_ID,
                                     "source_revision": "artifact-version:h1"}

        out = cycle._work_no_change({"observed_at": OBSERVED_AT}, llm_calls=1)
        self.assertEqual(out["outcome"], alc.OUTCOME_NO_ACTION, out)
        self.assertTrue(out["work_no_change"]["recorded"], out["work_no_change"])
        self.assertEqual(out["work_no_change"]["recorded_target_ref"], WORK_ID,
                         out["work_no_change"])
        self.assertEqual(out["work_no_change"]["recorded_source_revision"],
                         "artifact-version:h1", out["work_no_change"])

        # Next round: the new work Activity holds the foreground.  The head is read from the owner
        # with its own id spelling, so the no_change is read back on exactly that revision.
        cycle.runtime.activity_read = _ActivityRead(
            {"activity:work-1": _work_activity_with_lineage()}, foreground="activity:work-1")
        cycle.runtime.foreground = "activity:work-1"
        cycle.runtime.candidate_read.candidates["cand:work"] = {
            "candidate_id": "cand:work", "target_refs": [WORK_ID, "artifactversion:h1"]}

        ready = cycle.step_readiness(act=True)
        self.assertEqual(ready.target_ref, WORK_ID, ready.to_dict())
        self.assertEqual(ready.source_revision, "artifact-version:h1", ready.to_dict())
        self.assertIs(ready.available, False, ready.to_dict())
        self.assertEqual(ready.reason_code, lsr.REASON_WORK_NO_CHANGE, ready.to_dict())
        self.assertEqual(ready.fact_keys(), {FACT_KEY: True}, ready.to_dict())
        self.assertIn("artifact:work-1", port.calls, port.calls)

    def test_a_body_action_readiness_target_comes_from_its_own_candidate_not_the_first_subject_ref(self):
        """The POSE Activity's first subject ref is the *place*; its target is its own candidate's."""

        cycle = self._cycle(activities={"activity:body-1": _body_activity()},
                            foreground="activity:body-1", situation=None,
                            candidates={"cand:body": {"candidate_id": "cand:body",
                                                      "target_refs": ["bodyact:pose:lying:south"]}})
        cycle.last_world_observation = {"location": {"place_id": "home"}, "world_revision": 1,
                                        "pose": "lying", "orientation": "south"}
        ready = cycle.step_readiness(act=True)
        self.assertEqual(ready.target_ref, "bodyact:pose:lying:south", ready.to_dict())
        self.assertEqual(ready.source_revision, "1", ready.to_dict())
        self.assertTrue(ready.bound, ready.to_dict())

        # With no readable candidate and several refs, the place is never borrowed as "the" target.
        blind = self._cycle(activities={"activity:body-1": _body_activity()},
                            foreground="activity:body-1", situation=None)
        blind.last_world_observation = dict(cycle.last_world_observation)
        unknown = blind.step_readiness(act=True)
        self.assertIsNone(unknown.target_ref, unknown.to_dict())
        self.assertFalse(unknown.bound, unknown.to_dict())

    _HOME_OBSERVATION = {"location": {"place_id": "home"}, "world_revision": 1,
                         "pose": "standing", "orientation": "south"}
    _QUIET_OPPORTUNITIES = [
        {"opportunity_id": "wopp:move:home:r1:street_1", "target_refs": ["place:street_1"]},
        {"opportunity_id": "wopp:stay:home:r1:REST", "target_refs": ["stay:home:REST:300"]},
        {"opportunity_id": "wopp:pose:home:r1:seated",
         "target_refs": ["bodyact:pose:seated:south"]}]

    def _quiet_state(self, *, opportunities=None, observation=None, **overrides):
        """One state the World keeps offering something in -- the case that used to never settle."""

        state = dict(
            activities={ACTIVITY_REF: _reading_activity()}, foreground=ACTIVITY_REF,
            situation=AVAILABLE, decision="NO_ACTION",
            provider=_ObservingProvider(
                [observation or self._HOME_OBSERVATION],
                opportunities=(self._QUIET_OPPORTUNITIES if opportunities is None
                               else opportunities)))
        state.update(overrides)
        return state

    def test_the_same_situation_in_a_new_process_is_quiet_with_world_opportunities_visible(self):
        root = self._root()
        first = self._cycle(state_root=root, **self._quiet_state())
        first.last_world_observation = dict(self._HOME_OBSERVATION)
        out = self._round(first)
        self.assertEqual(out["outcome"], alc.OUTCOME_NO_ACTION, out)
        self.assertEqual(len(first.runtime.coordinator.calls), 1)
        self.assertFalse(out["reconsideration"]["blocked"], out["reconsideration"])

        # A second process on the same state root sees the identical situation -- and spends nothing.
        second = self._cycle(state_root=root, **self._quiet_state())
        second.last_world_observation = dict(self._HOME_OBSERVATION)
        again = self._round(second)
        self.assertEqual(again["outcome"], alc.OUTCOME_QUIET, again)
        self.assertEqual(again["llm_calls"], 0, again)
        self.assertTrue(again["reconsideration"]["blocked"], again["reconsideration"])
        self.assertEqual(second.runtime.coordinator.calls, [])

        # A World tick that changes nothing she can perceive is not a new situation either: the
        # fingerprint is built from what she could act on, not from a revision or a timestamp.
        tick = self._cycle(state_root=root, **self._quiet_state(
            observation=dict(self._HOME_OBSERVATION, world_revision=9)))
        self.assertEqual(self._round(tick)["outcome"], alc.OUTCOME_QUIET)
        self.assertEqual(tick.runtime.coordinator.calls, [])

    def test_a_real_new_opportunity_reopens_the_question(self):
        root = self._root()
        first = self._cycle(state_root=root, **self._quiet_state())
        self._round(first)

        # One more real opportunity target: a different situation, so it earns a request.
        second = self._cycle(state_root=root, **self._quiet_state(
            opportunities=self._QUIET_OPPORTUNITIES + [
                {"opportunity_id": "wopp:pose:home:r1:lying",
                 "target_refs": ["bodyact:pose:lying:south"]}]))
        again = self._round(second)
        self.assertNotEqual(again["outcome"], alc.OUTCOME_QUIET, again)
        self.assertEqual(len(second.runtime.coordinator.calls), 1)


    # -- 9. the parent's gen4 chain: one object, two spellings, a rebuilt process ---------

    #: The work exactly as the parent's owner recorded it (gen4 short chain, read-only fixture).
    _PARENT_WORK_ID = "artifact:c6bedf9f1e7b408a9bf8145bbf4e9389"
    _PARENT_WORK_HEAD = "artifact-version:3092e29dc9de4d7b8f82acca9e161756"
    _PARENT_WORK_ACTIVITY = "actv_e59fe247900d4ed3"
    _PARENT_WORK_CANDIDATE = "cand:359904c047c62585"

    def _parent_work_activity(self, status: str = "ACTIVE") -> dict:
        """The work Activity straight from the parent's owner record.

        ``subject_refs`` is the whole observed set (the object *and* its revision), the object is
        also its ``origin_ref``, and the adoption lineage names a candidate plus this tree's own
        ``artifact:artifact:<id>`` wrapper for the same object -- which is exactly where the second
        spelling came from.
        """

        return {
            "activity_id": self._PARENT_WORK_ACTIVITY,
            "activity_ref": self._PARENT_WORK_ACTIVITY,
            "activity_kind": "CONTINUE_PERSONAL_ARTIFACT", "status": status,
            "subject_refs": [self._PARENT_WORK_ID, self._PARENT_WORK_HEAD],
            "source_refs": ["dec:2fc93cdb81f6fb5f", "adp:b1febcd6502d1560",
                            self._PARENT_WORK_CANDIDATE,
                            "artifact:artifact:%s" % self._PARENT_WORK_ID.split(":", 1)[-1]],
            "checkpoint_ref": "chk:stage:001", "origin_ref": self._PARENT_WORK_ID,
            "adoption_ref": "adp:b1febcd6502d1560", "decision_ref": "dec:2fc93cdb81f6fb5f"}

    def _parent_work_state(self, port):
        """The owner records plus the World state the parent's chain really had."""

        return dict(activities={self._PARENT_WORK_ACTIVITY: self._parent_work_activity()},
                    foreground=self._PARENT_WORK_ACTIVITY, situation=EXHAUSTED,
                    artifact_port=port,
                    provider=_ObservingProvider([self._HOME_OBSERVATION],
                                                opportunities=self._QUIET_OPPORTUNITIES))

    def test_a_work_target_survives_a_rebuild_where_the_candidate_cache_is_gone(self):
        """One object, two spellings, and a second process with no candidate cache at all.

        The parent's chain: a ``no_change`` bound to the owner's own head during START, then the work
        Activity holding the foreground -- and, after a rebuild, the *same* state asked about again
        (``llm_calls=1``) because the readiness lost its target (``NO_BOUND_TARGET``).  The target
        is therefore read from the Activity owner's own record (``origin_ref``/``subject_refs``),
        and this tree's ``artifact:artifact:<id>`` wrapper folds into the owner's ``artifact:<id>``,
        so record and lookup agree across the restart -- without a second store, and without hiding
        the World opportunities that are still visible.
        """

        root = self._root()
        port = _OwnerPort({self._PARENT_WORK_ID: self._PARENT_WORK_HEAD})

        # Round 1 (START still being adopted): no foreground yet, so the round binds the *selected
        # work* and the head the owner really reports.
        start = self._cycle(state_root=root, activities={}, foreground=None, situation=EXHAUSTED,
                            artifact_port=port)
        start.work_artifact_id = self._PARENT_WORK_ID
        start._round_readiness = lsr.StepReadiness(available=None,
                                                   reason_code=lsr.REASON_NO_FOREGROUND)
        start._round_work_binding = {"target_ref": self._PARENT_WORK_ID,
                                     "source_revision": self._PARENT_WORK_HEAD}
        first_out = start._work_no_change({"observed_at": OBSERVED_AT}, llm_calls=1)
        self.assertEqual(first_out["work_no_change"]["recorded_target_ref"],
                         self._PARENT_WORK_ID, first_out["work_no_change"])

        # Process 1: the work Activity holds the foreground and its own candidate names the same
        # object in this tree's spelling.  It must not become a second target.
        first = self._cycle(state_root=root, work_id=self._PARENT_WORK_ID, **self._parent_work_state(port))
        first.runtime.candidate_read.candidates[self._PARENT_WORK_CANDIDATE] = {
            "candidate_id": self._PARENT_WORK_CANDIDATE,
            "target_refs": ["artifact:artifact:%s" % self._PARENT_WORK_ID.split(":", 1)[-1],
                            self._PARENT_WORK_HEAD]}
        out = self._round(first)
        self.assertEqual(out["step_readiness"]["target_ref"], self._PARENT_WORK_ID,
                         out["step_readiness"])
        self.assertEqual(out["step_readiness"]["source_revision"], self._PARENT_WORK_HEAD,
                         out["step_readiness"])
        self.assertEqual(out["step_readiness"]["reason_code"], lsr.REASON_WORK_NO_CHANGE,
                         out["step_readiness"])
        self.assertTrue(out["step_readiness"]["bound"], out["step_readiness"])
        self.assertEqual(out["outcome"], alc.OUTCOME_NO_ACTION, out)
        self.assertEqual(len(first.runtime.coordinator.calls), 1, first.runtime.coordinator.calls)

        # Process 2 (a rebuild): the same owner records, and *no* candidate cache.  The same state is
        # recognised instead of being asked about again, and nothing is hidden or switched off.
        rebuilt = self._cycle(state_root=root, work_id=self._PARENT_WORK_ID,
                              **self._parent_work_state(port))
        again = self._round(rebuilt)
        self.assertEqual(again["step_readiness"]["target_ref"], self._PARENT_WORK_ID,
                         again["step_readiness"])
        self.assertEqual(again["step_readiness"]["reason_code"], lsr.REASON_WORK_NO_CHANGE,
                         again["step_readiness"])
        self.assertEqual(again["outcome"], alc.OUTCOME_QUIET, again)
        self.assertEqual(again["llm_calls"], 0, again)
        self.assertEqual(rebuilt.runtime.coordinator.calls, [])
        self.assertTrue(again["reconsideration"]["blocked"], again["reconsideration"])
        self.assertIn("bodyact:pose:seated:south",
                      again["reconsideration"]["parts"]["opportunities"],
                      again["reconsideration"])

    def test_the_domain_wrapper_and_the_owner_id_are_one_target_across_a_rebuild(self):
        """A record written in one spelling is found by a lookup in the other -- one identity only."""

        self.assertEqual(lsr.namespace_of("artifact:artifact:work-1"), lsr.TARGET_ARTIFACT)
        self.assertEqual(lsr.canonical_target_ref("artifact:artifact:work-1"), WORK_ID)
        self.assertEqual(lsr.canonical_target_ref(WORK_ID), WORK_ID)
        self.assertIsNone(lsr.canonical_target_ref("artifactversion:h1"))

        root = self._root()
        written = lsr.ReconsiderationLedger(root).record(
            readiness=lsr.StepReadiness(available=False, reason_code=lsr.REASON_WORK_NO_CHANGE),
            outcome=alc.OUTCOME_NO_ACTION, at=OBSERVED_AT,
            target_ref="artifact:artifact:work-1", source_revision="artifact-version:h1",
            reason_code=lsr.REASON_WORK_NO_CHANGE)
        self.assertIsNotNone(written, written)
        self.assertEqual(written["target_ref"], WORK_ID, written)

        # A rebuilt process reads the ledger back with the owner's own spelling.
        found = lsr.ReconsiderationLedger(root).no_change(WORK_ID, "artifact-version:h1",
                                                          at=OBSERVED_AT)
        self.assertIsNotNone(found, found)

        # ... and the readiness read from the owner's record alone (no candidate cached at all)
        # is that same no-step fact, bound to the Activity and to the owner's own head.
        port = _OwnerPort({"artifact:work-1": "artifact-version:h1"})
        cycle = self._cycle(activities={"activity:work-1": _work_activity_with_lineage()},
                            foreground="activity:work-1", situation=None, artifact_port=port,
                            state_root=root)
        ready = cycle.step_readiness(act=True)
        self.assertEqual(ready.target_ref, WORK_ID, ready.to_dict())
        self.assertEqual(ready.source_revision, "artifact-version:h1", ready.to_dict())
        self.assertIs(ready.available, False, ready.to_dict())
        self.assertEqual(ready.fact_keys(), {FACT_KEY: True}, ready.to_dict())


if __name__ == "__main__":
    unittest.main()

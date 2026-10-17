"""D（life-v1）R1：同一实例的统一入口、私人生活、真实自主阅读、有限共同时间与 C 接入。

本文件按父级 R1 判据重写：每一步都**强制走成功路径**，并断言真实 owner 事实
（joint Activity 真的 terminal、私人 Activity 真的恢复并留下真实一步、C 由真实 M0 端到端驱动、
房间真的有物件、重启真的是另一个进程）。没有任何 ``if ok else 接受未中断`` 这类分支收口。

覆盖：
1. 共同时间闭环：显式邀请 → LR-3 规范暂停 → LR-4 WAITING 保存点 → AG-1/AG-2 有限共同活动 →
   显式结束事件 → LR-5 完成（joint Activity COMPLETED，前台释放）→ 新 resume eligibility →
   下一轮 AG-0→AG-1→AG-2 RESUME 原私人 Activity（checkpoint/原对象保持，并能继续真实一小步）；
   重复 end 幂等；未 APPLIED 的 begin 明确非成功且私人状态可恢复。
2. C 真实接入：真实 Action/owner 回执 → handoff → M0 准入 → C 读回 → 下一次实际可继续对象变化；
   弱线索负控不变；三 subject 不强制相等。
3. 资格与 status：status/读取不消费资格；暂停/DEFER 不丢资格；成功 RESUME 后不重复 adopt。
4. 真实自主阅读：阅读材料经允许的 PERSONAL_OPPORTUNITY 源成为合法 Candidate → AG-1 选择 →
   AG-2 Activity → AR-0 owner action 真的取页并落书签；作品/阅读两分支都真实且非固定优先。
5. 房间与 Body：隔离 fixture 世界包声明一处区域+家具，真实 observe_room 读到物件并真实 link；
   统一场景真的调用现有 Body 只读口（LIMITED/UNKNOWN 带真实来源）。
6. 真实进程重启：subprocess 起停，同一数据根，新 budget 对象读盘，pause 重启后仍有效。
7. 安静：0 HTTP / 0 模型；NO_ACTION 一回是 1 次决策请求，不称 0 模型。

需要 POSIX（fcntl + AF_UNIX）；原生 Windows 自动跳过。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "native" / "memory_runtime_v1"),
              str(ROOT / "components" / "native" / "integration"), str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from test_one_day_life import CycleTest, StubCognition, StubObservation, StubWriter, posix_only

import autonomous_life_assembly as assembly  # noqa: E402
import autonomous_life_cycle as alc  # noqa: E402
import life_co_presence as cop  # noqa: E402
import life_pause  # noqa: E402
import one_day_life as odl  # noqa: E402

from app.evidence import EvidenceStore  # noqa: E402
from life_experience_memory_consumer import MemoryAdmissionExperienceConsumer  # noqa: E402
from life_experience_port import LifeExperiencePort, life_conversation_id  # noqa: E402
from memory_control import MemoryControlLedger  # noqa: E402

READING = ("第一页：房间里的桌子与窗。\n%%PAGE%%\n"
           "第二页：窗外是一条街，街角有树。\n%%PAGE%%\n"
           "第三页：天色渐暗，风从海上过来。\n")
MEMORY_OWNER = "chiyo"
INVITATION = {
    "event_kind": cop.KIND_INVITATION, "invitation_id": "inv:d:1",
    "source_ref": "channel:d-test", "occurred_at": "2026-10-16T06:00:00+00:00",
    "scope": "一起待一会儿", "requires_engagement": True,
}
END_EVENT = {
    "event_kind": cop.KIND_END, "invitation_id": "inv:d:1",
    "occurred_at": "2026-10-16T06:20:00+00:00", "explicit_end": True,
}
INVITATION_2 = {
    "event_kind": cop.KIND_INVITATION, "invitation_id": "inv:d:2",
    "source_ref": "channel:d-test", "occurred_at": "2026-10-16T06:40:00+00:00",
    "scope": "再待一会儿", "requires_engagement": True,
}
END_EVENT_2 = {
    "event_kind": cop.KIND_END, "invitation_id": "inv:d:2",
    "occurred_at": "2026-10-16T06:55:00+00:00", "explicit_end": True,
}


class MemoryFixture:
    """真实 M0 + 真实控制账本 + 真实边界端口与准入 consumer（C 的既有夹具协议）。"""

    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        os.chmod(root, 0o700)
        self.root = root
        self.evidence_db = root / "evidence.sqlite"
        self.controls_db = root / "controls.sqlite"
        self.store = EvidenceStore(self.evidence_db)
        self.ledger = MemoryControlLedger(self.controls_db, self.evidence_db, owner=MEMORY_OWNER,
                                          conversation=life_conversation_id(MEMORY_OWNER))
        self.ledger.initialize()
        self.port = LifeExperiencePort(self.evidence_db, controls_db=self.controls_db,
                                       subject=MEMORY_OWNER)
        self.consumer = MemoryAdmissionExperienceConsumer(self.port, enabled=True)

    def rows(self, limit: int = 20) -> list[dict]:
        return self.store.recent_verified_outcomes(memory_owner=MEMORY_OWNER, limit=limit)


@posix_only
class DScenarioBase(CycleTest):
    """真实 Supply owner + 真实 World home + 真实 runtime + loopback 替身 + 真实隔离 M0。"""

    # -- fixture -------------------------------------------------------------------------

    def scene_doc(self) -> dict:
        """一处声明了区域与家具的室内场景（只进本用例自己的隔离 fixture 包）。"""
        from components.worldgen import schema as worldgen_schema

        return {
            "schema_version": worldgen_schema.SCENE_SCHEMA,
            "spaces": {"home": {"enabled": True, "entry": "bedroom", "walls": [], "doors": [],
                                "windows": [], "floors": []}},
            "placements": {
                "desk_home": {"kind": "furniture", "x": 1.0, "y": 2.0,
                              "visual": {"shape": "box"}, "actions": [], "behavior": "static",
                              "portable": False, "capacity": 0, "servings": 0,
                              "initial_status": "ok", "interaction": "none",
                              "asset_id": "asset_desk"},
                "bed_home": {"kind": "furniture", "x": 1.5, "y": 2.5,
                             "visual": {"shape": "box"}, "actions": [], "behavior": "static",
                             "portable": False, "capacity": 0, "servings": 0,
                             "initial_status": "ok", "interaction": "none",
                             "asset_id": "asset_chair"},
            },
            "assets": {"asset_desk": {"id": "asset_desk", "kind": "image", "path": "desk.png"},
                       "asset_chair": {"id": "asset_chair", "kind": "image",
                                       "path": "chair.png"}},
            "navigation": {"home": {
                "areas": [{"area_id": "bedroom", "name": "卧室", "kind": "room"}],
                "connections": [], "entrance": "bedroom",
                "furniture_map": {"bedroom": ["desk_home", "bed_home"]}}},
        }

    def make_scene_world(self) -> Path:
        """A world package that really declares a room with furniture (owner-side data)."""
        from components.worldgen import schema as worldgen_schema
        from components.worldgen.tests._setup import make_valid_docs

        docs = make_valid_docs(seed=929711, theme="coastal_town")
        docs["scene.json"] = self.scene_doc()
        package = Path(self.temp_dir(prefix="d-r1-pkg-"))
        worldgen_schema.write_package(package, docs)
        return package

    def memory(self) -> MemoryFixture:
        fixture = MemoryFixture(Path(self.instance) / "memory")
        self.addCleanup(fixture.store.close) if hasattr(fixture.store, "close") else None
        return fixture

    def prepare_scenario(self, **kwargs):
        # The base fixture always passes *its* world package; point it at the scene package
        # this test builds (a declared room with furniture), so no argument is passed twice.
        if not kwargs.pop("keep_fixture_world", False):
            self.world.package_dir = self.make_scene_world()
        memory = kwargs.pop("memory", None) or self.memory()
        result = self.prepare_instance(reading_title="D 场景材料",
                                       reading_content=READING, create_private_items=True,
                                       memory_db=memory.evidence_db,
                                       memory_controls_db=memory.controls_db,
                                       memory_subject=MEMORY_OWNER, **kwargs)
        data = odl.read_instance(self.instance)
        return SimpleNamespace(result=result, data=data, memory=memory)

    def runtime_with_memory(self, cognition, env, data, memory, *, writer=None):
        """真实 runtime，接上真实 Memory consumer 与真实 AR-0 适配器。"""
        import life_artifact_port as lap
        import life_integration_ag2 as ag2
        import life_runtime_production as lrp
        import managed_artifact_action_adapter as maaa
        from world_journey_action_adapter import JourneyWorldResolver, RealJourneyWorldActionAdapter

        state_root = odl.life_state_root(self.instance)
        port = lap.LifeArtifactPort(str(self.services.supply_path), data["subject_id"])
        adapter = maaa.SupplyManagedArtifactActionAdapter(
            port, grant_id=data["artifact_grant_id"], workspace_id=data["workspace_id"])
        switches = ag2.LifeRuntimeKillSwitches(
            life_runtime_enabled=True, agency_enabled=True, action_execution_enabled=True,
            proactive_enabled=False)
        runtime = lrp.build_production_life_runtime(
            state_root, kill_switches=switches, cognition_adapter=cognition,
            install_root=self.instance / "life", artifact_action_adapter=adapter,
            world_action_adapter=RealJourneyWorldActionAdapter(
                JourneyWorldResolver(environment=env)),
            experience_consumer=memory.consumer)
        self._runtimes.append(runtime)
        self.addCleanup(runtime.close)
        return runtime, port

    def assemble(self, runtime, env, *, cognition=None, writer=None, observation=None):
        budget = assembly.request_budget(env, odl.life_state_root(self.instance))
        self.assertIsNotNone(budget, "the instance must carry both request ceilings")
        writer = writer or StubWriter()
        observation = observation or StubObservation(env)
        bind = getattr(observation, "bind_environment", None)
        if callable(bind):
            bind(env)
        assembled = assembly.assemble_autonomous_life(
            runtime, environment=env, state_root=odl.life_state_root(self.instance),
            world_provider=observation, writer=writer, enabled=True, requests=budget)
        self.assertEqual(assembled["state"], assembly.READY, assembled)
        return SimpleNamespace(assembled=assembled, cycle=assembled["cycle"], budget=budget,
                               writer=writer, observation=observation)

    # -- owner read helpers --------------------------------------------------------------

    def _resume_cognition(self):
        """A loopback branch that picks the RESUME-legal candidate, and records whether the
        owner's own resume eligibility was offered to AG-1 at all."""
        import agency_decision_ag1 as ag1

        class ResumeCognition(StubCognition):
            def __init__(inner):
                super().__init__("artifact")
                # NOTE: ``StubCognition.offered`` is already a list, so this flag has its own
                # name instead of shadowing it.
                inner.resume_offered = False
                inner.selected = None

            def invoke(inner, request, *, schema_retry_index=0):
                allowed = [str(code) for code in
                           (getattr(request, "allowed_reason_codes", []) or [])]
                for candidate in (getattr(request, "candidates", []) or []):
                    per = [str(item) for item in (candidate.get("legal_decisions") or [])]
                    if "RESUME" in per:
                        inner.resume_offered = True
                        inner.selected = candidate.get("candidate_id")
                        return ag1.CognitiveDecisionResponse(
                            model_run_ref="model_run:stub:resume", model_id=inner.model_id,
                            model_version=inner.model_version,
                            raw_output={"decision": "RESUME",
                                        "selected_candidate_id": candidate["candidate_id"],
                                        "reason_codes": allowed[:1],
                                        "short_rationale": "go back to my own work",
                                        "deferred_candidate_ids": []},
                            latency_ms=1)
                return super().invoke(request, schema_retry_index=schema_retry_index)

        return ResumeCognition()

    def _continue_cognition(self):
        """A loopback branch that picks the CONTINUE-legal candidate (the legal move when a
        foreground Activity is already running)."""
        import agency_decision_ag1 as ag1

        class ContinueCognition(StubCognition):
            def __init__(inner):
                super().__init__("artifact")
                inner.continue_offered = False

            def invoke(inner, request, *, schema_retry_index=0):
                allowed = [str(code) for code in
                           (getattr(request, "allowed_reason_codes", []) or [])]
                for candidate in (getattr(request, "candidates", []) or []):
                    per = [str(item) for item in (candidate.get("legal_decisions") or [])]
                    if "CONTINUE" in per:
                        inner.continue_offered = True
                        return ag1.CognitiveDecisionResponse(
                            model_run_ref="model_run:stub:continue", model_id=inner.model_id,
                            model_version=inner.model_version,
                            raw_output={"decision": "CONTINUE",
                                        "selected_candidate_id": candidate["candidate_id"],
                                        "reason_codes": allowed[:1],
                                        "short_rationale": "carry on with what is running",
                                        "deferred_candidate_ids": []},
                            latency_ms=1)
                return super().invoke(request, schema_retry_index=schema_retry_index)

        return ContinueCognition()

    def _resume_cognition_for(self, artifact_id: str):
        """A loopback branch that resumes the parked Activity about ``artifact_id``.

        Two parked Activities can hold a resume eligibility at once, and the generic resume
        branch takes the first one it sees -- which Activity gets resumed is not a product
        fact.  This one names the object it is about, the way the drill does.
        """
        import agency_decision_ag1 as ag1

        class ResumeForCognition(StubCognition):
            def __init__(inner):
                super().__init__("artifact")
                inner.resume_offered = False

            def invoke(inner, request, *, schema_retry_index=0):
                allowed = [str(code) for code in
                           (getattr(request, "allowed_reason_codes", []) or [])]
                for candidate in (getattr(request, "candidates", []) or []):
                    per = [str(item) for item in (candidate.get("legal_decisions") or [])]
                    if "RESUME" not in per:
                        continue
                    refs = [str(ref) for ref in (candidate.get("target_refs") or [])]
                    if not any(artifact_id in ref for ref in refs):
                        continue
                    inner.resume_offered = True
                    inner.call_count += 1
                    return ag1.CognitiveDecisionResponse(
                        model_run_ref="model_run:stub:resume", model_id=inner.model_id,
                        model_version=inner.model_version,
                        raw_output={"decision": "RESUME",
                                    "selected_candidate_id": candidate["candidate_id"],
                                    "reason_codes": allowed[:1],
                                    "short_rationale": "go back to my own work",
                                    "deferred_candidate_ids": []},
                        latency_ms=1)
                return super().invoke(request, schema_retry_index=schema_retry_index)

        return ResumeForCognition()

    def _reading_cognition(self, data):
        """A loopback branch that picks the *reading* candidate out of the same window."""
        import agency_decision_ag1 as ag1

        class ReadingCognition(StubCognition):
            def invoke(inner, request, *, schema_retry_index=0):
                allowed = [str(code) for code in
                           (getattr(request, "allowed_reason_codes", []) or [])]
                for candidate in (getattr(request, "candidates", []) or []):
                    refs = [str(ref) for ref in (candidate.get("target_refs") or [])]
                    refs.append(str(candidate.get("source_ref") or ""))
                    if not any(data["reading_artifact_id"] in ref for ref in refs):
                        continue
                    per = [str(item) for item in (candidate.get("legal_decisions") or [])]
                    if "START" not in per and "CONTINUE" not in per:
                        continue
                    inner.call_count += 1
                    return ag1.CognitiveDecisionResponse(
                        model_run_ref="model_run:stub:read", model_id=inner.model_id,
                        model_version=inner.model_version,
                        raw_output={
                            "decision": "CONTINUE" if "CONTINUE" in per else "START",
                            "selected_candidate_id": candidate["candidate_id"],
                            "reason_codes": allowed[:1],
                            "short_rationale": "read a page", "deferred_candidate_ids": []},
                        latency_ms=1)
                return super().invoke(request, schema_retry_index=schema_retry_index)

        return ReadingCognition("artifact")

    def _work_cognition(self, work_artifact_id: str, reading_artifact_id: str):
        """A loopback branch that picks the *work* candidate, and records which of the two
        legal personal opportunities AG-1 really offered in that same window.

        Which candidate the generic stub happens to list first is not a product fact, so a
        test that is about the work branch has to choose it explicitly -- and, to prove the
        branch was legal at all, check that the reading candidate was offered alongside it.
        """
        import agency_decision_ag1 as ag1

        class WorkCognition(StubCognition):
            def __init__(inner):
                super().__init__("artifact")
                inner.work_offered = False
                inner.reading_offered = False

            def invoke(inner, request, *, schema_retry_index=0):
                allowed = [str(code) for code in
                           (getattr(request, "allowed_reason_codes", []) or [])]
                chosen: tuple[dict, list[str]] | None = None
                for candidate in (getattr(request, "candidates", []) or []):
                    candidate = dict(candidate)
                    refs = [str(r) for r in (candidate.get("target_refs") or [])]
                    refs.append(str(candidate.get("source_ref") or ""))
                    if any(reading_artifact_id in ref for ref in refs):
                        inner.reading_offered = True
                    if not any(work_artifact_id in ref for ref in refs):
                        continue
                    inner.work_offered = True
                    per = [str(item) for item in (candidate.get("legal_decisions") or [])]
                    if chosen is None and ("START" in per or "CONTINUE" in per):
                        # Keep scanning: whether the reading candidate was offered in the same
                        # window is part of what this branch reports.
                        chosen = (candidate, per)
                if chosen is not None:
                    candidate, per = chosen
                    inner.call_count += 1
                    return ag1.CognitiveDecisionResponse(
                        model_run_ref="model_run:stub:work", model_id=inner.model_id,
                        model_version=inner.model_version,
                        raw_output={
                            "decision": "CONTINUE" if "CONTINUE" in per else "START",
                            "selected_candidate_id": candidate["candidate_id"],
                            "reason_codes": allowed[:1],
                            "short_rationale": "keep writing", "deferred_candidate_ids": []},
                        latency_ms=1)
                return super().invoke(request, schema_retry_index=schema_retry_index)

        return WorkCognition()

    def supply(self, request: dict) -> dict:
        return odl.supply_call(self.services.supply_path, request)

    def head(self, artifact_id: str) -> dict:
        data = odl.read_instance(self.instance)
        return self.supply({"op": "read_head", "subject": data["subject_id"],
                            "artifact_id": artifact_id})["head"]

    def content(self, artifact_id: str) -> str:
        data = odl.read_instance(self.instance)
        return self.supply({"op": "read_artifact", "subject": data["subject_id"],
                            "artifact_id": artifact_id})["content"]

    def activity(self, runtime, activity_ref: str) -> dict:
        return dict(runtime.activity_read.get_activity(activity_ref) or {})

    def eligibilities(self, runtime) -> list[dict]:
        return [dict(e) for e in runtime.eligibility_store.load_state().get("eligibilities", {})
                .values()]


# =====================================================================================
# 1 + 3：共同时间闭环 + 资格语义
# =====================================================================================


@posix_only
class DCoPresenceClosureTest(DScenarioBase):

    def _start_joint_time(self):
        """Real chain up to an open session with a joint Activity; returns the fixture."""
        prepared = self.prepare_scenario()
        env = self.instance_env()
        # The private round is the *work* branch, chosen explicitly: which personal
        # opportunity the generic stub happens to list first is not a product fact.
        cognition = self._work_cognition(prepared.data["work_artifact_id"],
                                         prepared.data["reading_artifact_id"])
        runtime, port = self.runtime_with_memory(cognition, env, prepared.data, prepared.memory)
        fixture = self.assemble(runtime, env, cognition=cognition)
        fixture.runtime, fixture.port, fixture.data = runtime, port, prepared.data
        fixture.memory, fixture.env = prepared.memory, env
        # A real private Activity must exist first (the work round really commits a version).
        before = self.head(prepared.data["work_artifact_id"])["version_id"]
        first = fixture.cycle.evaluate_once(trigger_ref="evt:d:work", act=True)
        self.assertEqual(first["outcome"], "ACTED", first)
        self.assertNotEqual(self.head(prepared.data["work_artifact_id"])["version_id"], before)
        fixture.private_activity_ref = first["activity_ref"]
        fixture.private_checkpoint = self.activity(runtime, first["activity_ref"])["checkpoint_ref"]
        protocol = cop.FiniteCoPresence(
            runtime, state_root=odl.life_state_root(self.instance),
            subject_id=prepared.data["subject_id"], home=str(odl.life_state_root(self.instance)),
            cycle=fixture.cycle)
        invitation = cop.parse_invitation(INVITATION)
        decision = protocol.consider(invitation, now="2026-10-16T06:01:00+00:00")
        self.assertEqual(decision["decision"], cop.DECISION_ACCEPT, decision)
        started = protocol.begin(invitation, now="2026-10-16T06:03:00+00:00")
        self.assertTrue(started["ok"], started)
        self.assertEqual(started["stage"], "INTERRUPTED", started)
        self.assertTrue(started["pause_applied"], started)
        self.assertEqual(started["activity_effect"], "INTERRUPT_NOW", started)
        self.assertTrue(started["private_waiting"]["ok"], started["private_waiting"])
        self.assertTrue(started["request"]["ok"], started["request"])
        self.assertTrue(started["adoption"]["ok"], started["adoption"])
        self.assertEqual(started["adoption"]["adoption_status"], "APPLIED",
                         started["adoption"])
        fixture.protocol = protocol
        fixture.session = started["session"]
        fixture.joint_ref = started["adoption"]["activity_ref"]
        self.assertTrue(fixture.joint_ref)
        self.assertTrue(fixture.session["single_foreground"])
        # The private work is really WAITING with its resume condition, foreground released.
        private = self.activity(runtime, fixture.private_activity_ref)
        self.assertEqual(private["status"], "WAITING", private)
        self.assertTrue(private.get("resume_condition_ref"), private)
        joint = self.activity(runtime, fixture.joint_ref)
        self.assertEqual(joint["status"], "ACTIVE", joint)
        return fixture

    def test_end_completes_the_joint_activity_and_the_private_work_resumes(self):
        fixture = self._start_joint_time()
        runtime, protocol = fixture.runtime, fixture.protocol

        ended = protocol.end(cop.parse_end(END_EVENT), now="2026-10-16T06:20:00+00:00")
        self.assertTrue(ended["ok"], ended)
        # (a) the joint Activity is really terminal, by the owner's own read-back
        self.assertEqual(ended["joint_activity"]["status"], "COMPLETED", ended["joint_activity"])
        self.assertTrue(ended["joint_completion"]["completed_transition"], ended["joint_completion"])
        self.assertEqual(ended["joint_completion"]["reason_code"], "JOINT_ACTIVITY_COMPLETED")
        self.assertEqual(ended["joint_completion"]["activity_status"], "COMPLETED")
        self.assertNotEqual(ended["joint_completion"]["completion_status"],
                            "NOT_COMPLETE")
        self.assertTrue(ended["joint_completion"]["completion_eligibility_ref"])
        # the single foreground is released: no ACTIVE foreground Activity is left
        self.assertIsNone(runtime.canonical_state().get("foreground_activity_ref"),
                          runtime.canonical_state())
        # (b) the private work has a NEW resume eligibility from the owner
        eligibility = ended["new_eligibility_refs"][0]
        self.assertEqual(eligibility["status"], "OPEN", eligibility)
        self.assertEqual(eligibility["activity_id"], fixture.private_activity_ref)
        self.assertEqual(ended["evaluation"]["llm_calls"], 0)
        self.assertFalse(ended["evaluation"]["auto_resumed"])

        # (c) the next real window resumes the private Activity through Decision+Adoption.
        # The loopback branch is *told* to choose the resume: START on the work would also be
        # legal (AG-1's own choice), and this test is about the legal resume path.
        resume_cognition = self._resume_cognition()
        runtime.decision_service.cognition_adapter = resume_cognition
        resumed = fixture.cycle.evaluate_once(trigger_ref="evt:d:resume", act=True)
        self.assertTrue(resume_cognition.resume_offered,
                        "no resume candidate was offered at all")
        self.assertEqual(resumed["decision"], "RESUME", resumed)
        self.assertEqual(resumed["adoption_status"], "APPLIED", resumed)
        private_after = self.activity(runtime, fixture.private_activity_ref)
        self.assertEqual(private_after["status"], "ACTIVE", private_after)
        self.assertEqual(private_after["checkpoint_ref"], fixture.private_checkpoint,
                         private_after)
        # the eligibility the owner emitted is consumed exactly once
        consumed = [e for e in self.eligibilities(runtime)
                    if e["eligibility_id"] == eligibility["eligibility_id"]]
        self.assertEqual(len(consumed), 1, consumed)
        self.assertEqual(consumed[0]["status"], "CONSUMED", consumed[0])
        self.assertTrue(consumed[0]["consumed_decision_ref"])
        # ... and the private work can really continue: one more real version
        before = self.head(fixture.data["work_artifact_id"])["version_id"]
        runtime.decision_service.cognition_adapter = self._continue_cognition()
        again = fixture.cycle.evaluate_once(trigger_ref="evt:d:continue", act=True)
        self.assertEqual(again["outcome"], "ACTED", again)
        self.assertNotEqual(self.head(fixture.data["work_artifact_id"])["version_id"], before)

        # (d) ending again is an idempotent replay, never a second completion
        replay = protocol.end(cop.parse_end(END_EVENT), now="2026-10-16T06:30:00+00:00")
        self.assertTrue(replay["ok"], replay)
        self.assertTrue(replay["already_closed"], replay)
        self.assertTrue(replay["replayed"], replay)
        self.assertEqual(replay["session"]["state"], "CLOSED")

    def test_status_and_reads_never_consume_an_eligibility(self):
        fixture = self._start_joint_time()
        runtime, protocol = fixture.runtime, fixture.protocol
        ended = protocol.end(cop.parse_end(END_EVENT), now="2026-10-16T06:20:00+00:00")
        self.assertTrue(ended["ok"], ended)
        eligibility_id = ended["new_eligibility_refs"][0]["eligibility_id"]

        # status / timeline are read-only and must not consume it
        for _ in range(3):
            view = odl.status(self.instance)
            self.assertTrue(view["read_only"])
            odl.timeline(self.instance, limit=10)
        still_open = [e for e in self.eligibilities(runtime)
                      if e["eligibility_id"] == eligibility_id]
        self.assertEqual(len(still_open), 1, still_open)
        self.assertEqual(still_open[0]["status"], "OPEN", still_open[0])

        # a paused window does not consume it either
        odl.set_pause(self.instance, True, reason="d-r1")
        held = fixture.cycle.evaluate_once(trigger_ref="evt:d:paused", act=True)
        self.assertEqual(held["outcome"], "PAUSED_BY_OPERATOR", held)
        odl.set_pause(self.instance, False, reason="d-r1")
        self.assertEqual([e for e in self.eligibilities(runtime)
                          if e["eligibility_id"] == eligibility_id][0]["status"], "OPEN")

        # a DEFER round also leaves it adoptable (seen != adopted)
        deferring = StubCognition("defer")
        fixture.cycle.writer = StubWriter()
        saved = fixture.cycle.runtime.decision_service.cognition_adapter
        fixture.cycle.runtime.decision_service.cognition_adapter = deferring
        try:
            deferred = fixture.cycle.evaluate_once(trigger_ref="evt:d:defer", act=True)
        finally:
            fixture.cycle.runtime.decision_service.cognition_adapter = saved
        self.assertIn(deferred["decision"], {"DEFER", "NO_ACTION"}, deferred)
        self.assertEqual([e for e in self.eligibilities(runtime)
                          if e["eligibility_id"] == eligibility_id][0]["status"], "OPEN")

        # and the legal resume still works afterwards, exactly once
        fixture.runtime.decision_service.cognition_adapter = self._resume_cognition()
        first = fixture.cycle.evaluate_once(trigger_ref="evt:d:resume", act=True)
        self.assertEqual(first["decision"], "RESUME", first)
        fixture.runtime.decision_service.cognition_adapter = self._resume_cognition()
        second = fixture.cycle.evaluate_once(trigger_ref="evt:d:resume:again", act=True)
        self.assertNotEqual(second["decision"], "RESUME", second)

    def test_a_failed_adoption_is_not_reported_as_a_started_joint_time(self):
        prepared = self.prepare_scenario()
        env = self.instance_env()
        # the private round is the work branch (chosen explicitly, not by candidate order)
        cognition = self._work_cognition(prepared.data["work_artifact_id"],
                                         prepared.data["reading_artifact_id"])
        runtime, port = self.runtime_with_memory(cognition, env, prepared.data, prepared.memory)
        fixture = self.assemble(runtime, env, cognition=cognition)
        first = fixture.cycle.evaluate_once(trigger_ref="evt:d:work", act=True)
        self.assertEqual(first["outcome"], "ACTED", first)
        protocol = cop.FiniteCoPresence(
            runtime, state_root=odl.life_state_root(self.instance),
            subject_id=prepared.data["subject_id"], home=str(odl.life_state_root(self.instance)),
            cycle=fixture.cycle)
        invitation = cop.parse_invitation(INVITATION)
        # The Decision is forced to decline the joint Activity: a NO_ACTION cognition adapter
        # never selects a candidate, so no joint Activity may exist.
        declining = StubCognition("no_action")
        runtime.decision_service.cognition_adapter = declining
        started = protocol.begin(invitation, now="2026-10-16T06:03:00+00:00")
        self.assertFalse(started["ok"], started)
        self.assertEqual(started["stage"], "ADOPTION_NOT_APPLIED", started)
        self.assertFalse(started["activity_started"])
        recovery = started["private_recovery"]
        self.assertTrue(recovery["recoverable"], recovery)
        self.assertEqual(recovery["activity_ref"], first["activity_ref"])
        # the private work is parked in the owner's own WAITING state -- recoverable, not lost
        private = self.activity(runtime, first["activity_ref"])
        self.assertEqual(private["status"], "WAITING", private)
        self.assertEqual(private["checkpoint_ref"], recovery["activity"]["checkpoint_ref"])
        # no session was opened, so a later end cannot be mistaken for this one
        self.assertEqual(protocol.read_session()["state"], "NONE")

    def test_an_atomic_or_paused_instance_refuses_without_starting_anything(self):
        prepared = self.prepare_scenario()
        env = self.instance_env()
        cognition = StubCognition("artifact")
        runtime, port = self.runtime_with_memory(cognition, env, prepared.data, prepared.memory)
        fixture = self.assemble(runtime, env, cognition=cognition)
        protocol = cop.FiniteCoPresence(
            runtime, state_root=odl.life_state_root(self.instance),
            subject_id=prepared.data["subject_id"], home=str(odl.life_state_root(self.instance)),
            cycle=fixture.cycle)
        invitation = cop.parse_invitation(INVITATION)
        odl.set_pause(self.instance, True, reason="d-r1")
        declined = protocol.consider(invitation, now="2026-10-16T06:01:00+00:00")
        self.assertEqual(declined["decision"], cop.DECISION_DECLINE, declined)
        self.assertEqual(declined["reason_code"], cop.REASON_OPERATOR_PAUSED)
        odl.set_pause(self.instance, False, reason="d-r1")
        atomic = dict(invitation, atomic=True)
        refused = protocol.consider(atomic, now="2026-10-16T06:01:00+00:00")
        self.assertEqual(refused["reason_code"], cop.REASON_ATOMIC, refused)
        # ordinary inbound message: observation only, no pause, no Activity
        observed = protocol.observe({"text": "在吗？"})
        self.assertEqual(observed["effect"], cop.EFFECT_OBSERVATION_ONLY)
        self.assertFalse(observed["pause_requested"])
        self.assertFalse(observed["activity_started"])
        self.assertFalse(life_pause.read(odl.life_state_root(self.instance))["paused"])

    def test_only_the_consumed_eligibility_is_recorded_as_adopted(self):
        """Two OPEN eligibilities, one real RESUME: only the consumed one is recorded.

        "Only a successful adoption consumes" means the watermark may name exactly the
        eligibility the owner reports CONSUMED -- never "everything C happened to show us".
        """
        fixture = self._start_joint_time()
        runtime, protocol, cycle = fixture.runtime, fixture.protocol, fixture.cycle
        data = fixture.data
        cycle.continuity_source = lambda: self._continuity_reader(fixture, runtime)
        ended = protocol.end(cop.parse_end(END_EVENT), now="2026-10-16T06:20:00+00:00")
        self.assertTrue(ended["ok"], ended)
        work_elig = ended["new_eligibility_refs"][0]
        self.assertEqual(work_elig["status"], "OPEN", work_elig)
        # a *second* real Activity has to exist before it can be parked: the reading branch
        # really reads a page and leaves the reading Activity as the single foreground
        runtime.decision_service.cognition_adapter = self._reading_cognition(data)
        reading = cycle.evaluate_once(trigger_ref="evt:d:read", act=True)
        self.assertEqual(reading["outcome"], "ACTED", reading)
        reading_activity = str(reading["activity_ref"])
        self.assertNotEqual(reading_activity, fixture.private_activity_ref)
        second = cop.FiniteCoPresence(
            runtime, state_root=odl.life_state_root(self.instance),
            subject_id=data["subject_id"], home=str(odl.life_state_root(self.instance)),
            cycle=cycle)
        invitation2 = cop.parse_invitation(INVITATION_2)
        self.assertEqual(second.consider(invitation2, now="2026-10-16T06:41:00+00:00")["decision"],
                         cop.DECISION_ACCEPT)
        started2 = second.begin(invitation2, now="2026-10-16T06:43:00+00:00")
        self.assertTrue(started2["ok"], started2)
        ended2 = second.end(cop.parse_end(END_EVENT_2), now="2026-10-16T06:55:00+00:00")
        self.assertTrue(ended2["ok"], ended2)
        reading_elig = ended2["new_eligibility_refs"][0]
        two_open = {item["eligibility_id"]: item["activity_id"]
                    for item in self.eligibilities(runtime) if item["status"] == "OPEN"}
        self.assertEqual(two_open, {work_elig["eligibility_id"]: fixture.private_activity_ref,
                                    reading_elig["eligibility_id"]: reading_activity}, two_open)

        # a DEFER round really considers them and consumes nothing at all
        runtime.decision_service.cognition_adapter = StubCognition("defer")
        deferred = cycle.evaluate_once(trigger_ref="evt:d:defer", act=True)
        self.assertEqual(deferred["decision"], "DEFER", deferred)
        self.assertEqual(cycle.read_continuity_watermark()["adopted_eligibility_refs"], [],
                         cycle.read_continuity_watermark())

        # exactly one RESUME is adopted: only *that* eligibility may be recorded
        runtime.decision_service.cognition_adapter = self._resume_cognition_for(
            data["work_artifact_id"])
        resumed = cycle.evaluate_once(trigger_ref="evt:d:resume", act=True)
        self.assertEqual(resumed["decision"], "RESUME", resumed)
        self.assertEqual(resumed["adoption_status"], "APPLIED", resumed)
        self.assertEqual(resumed.get("continuity_consumed_eligibility_ref"),
                         work_elig["eligibility_id"], resumed)
        watermark = cycle.read_continuity_watermark()
        self.assertEqual(watermark["adopted_eligibility_refs"], [work_elig["eligibility_id"]],
                         watermark)
        # the other one is untouched: still OPEN in LR-4, still offered by the legal AG-0 source
        statuses = {item["eligibility_id"]: item["status"]
                    for item in self.eligibilities(runtime)}
        self.assertEqual(statuses[work_elig["eligibility_id"]], "CONSUMED", statuses)
        self.assertEqual(statuses[reading_elig["eligibility_id"]], "OPEN", statuses)
        offered, _snap, _counts = runtime.resume_eligible_source.discover(
            observed_at="2026-10-16T07:00:00+00:00")
        self.assertIn(reading_elig["eligibility_id"],
                      [str(item.get("eligibility_id")) for item in offered])

        # ... and repeating status/read changes none of it
        for _ in range(3):
            odl.status(self.instance)
            cycle.read_continuity()
        self.assertEqual(cycle.read_continuity_watermark(), watermark)
        after = {item["eligibility_id"]: item["status"]
                 for item in self.eligibilities(runtime)}
        self.assertEqual(after, statuses, after)


# =====================================================================================
# 2：C 真实接入（真实 M0 端到端）
# =====================================================================================


@posix_only
class DContinuityIntegrationTest(DScenarioBase):

    def _fixture_with_memory(self, policy="artifact"):
        prepared = self.prepare_scenario()
        env = self.instance_env()
        cognition = StubCognition(policy)
        runtime, port = self.runtime_with_memory(cognition, env, prepared.data, prepared.memory)
        fixture = self.assemble(runtime, env, cognition=cognition)
        fixture.runtime, fixture.port, fixture.data = runtime, port, prepared.data
        fixture.memory, fixture.env, fixture.cognition = prepared.memory, env, cognition
        return fixture

    def _continuity_reader(self, fixture, runtime):
        from life_continuity_inputs import ContinuityBinding, LifeContinuityInputs

        return LifeContinuityInputs(
            binding=ContinuityBinding(subject=MEMORY_OWNER,
                                      home=str(odl.life_state_root(self.instance)),
                                      now="2026-10-16T07:00:00+00:00",
                                      owner_refs={"activity": "activity:canonical",
                                                  "agenda": "agenda:lr4",
                                                  "artifact": "artifact:supply"}),
            past_source=fixture.memory.port,
            activity_source=runtime.activity_read,
            agenda_source=runtime.waiting_coordinator,
            governance_probe=lambda: {"healthy": fixture.memory.ledger.projection().healthy})

    def test_a_real_owner_result_names_the_next_continue_point_and_the_step_lands(self):
        """One work, real AR-0 action -> receipt -> AG-2 handoff -> M0 -> the next real step.

        No hand-made handoff and no second artifact: the row used here is the one the round
        above really produced.  What history contributes is the *continuation input* (the row's
        own owner result, and the version the owner's receipt for that row's Action reports);
        the step is pinned to the owner's current head, and it then really lands.
        """
        fixture = self._fixture_with_memory()
        runtime, cycle = fixture.runtime, fixture.cycle
        data = fixture.data
        # C is wired exactly as the resident wires it for a Memory-configured instance
        cycle.continuity_source = lambda: self._continuity_reader(fixture, runtime)
        work = self._work_cognition(data["work_artifact_id"], data["reading_artifact_id"])
        runtime.decision_service.cognition_adapter = work
        head_before = self.head(data["work_artifact_id"])["version_id"]
        first = cycle.evaluate_once(trigger_ref="evt:d:c1", act=True)
        self.assertEqual(first["outcome"], "ACTED", first)
        self.assertTrue(work.work_offered, "no work candidate was offered at all")
        self.assertTrue(work.reading_offered,
                        "the reading candidate was not offered in the same window")
        head_after_first = self.head(data["work_artifact_id"])["version_id"]
        self.assertNotEqual(head_after_first, head_before)
        rows = fixture.memory.rows()
        self.assertTrue(rows, "the real owner result must have been admitted to M0")
        self.assertEqual(rows[0]["memory_owner"], MEMORY_OWNER)
        self.assertEqual(rows[0]["delivery_status"], "VERIFIED")
        admitted = fixture.memory.port.recent(limit=5)
        first_rows = [row for row in admitted if row.activity_ref == first["activity_ref"]]
        self.assertEqual(len(first_rows), 1, admitted)
        real_row = first_rows[0]
        self.assertEqual(real_row.artifact_ref, data["work_artifact_id"])
        self.assertTrue(real_row.action_ref, real_row.to_dict())
        receipts = runtime.action_read.list_receipts(real_row.action_ref)
        self.assertTrue(receipts, real_row.action_ref)
        self.assertEqual(receipts[-1]["details"]["version_id"], head_after_first)

        # C reads the real admitted past, and the cycle follows *the row C selected*
        view = cycle.read_continuity()
        self.assertEqual(view["past_state"], "OK", view["source_states"])
        step = view["next_step"]
        self.assertEqual(step["reason_code"], "CONTINUE_FROM_OWNER_CONFIRMED_OBJECT", step)
        self.assertEqual(step["artifact_ref"], data["work_artifact_id"], step)
        self.assertEqual(step["continue_from_ref"], real_row.owner_result, step)
        self.assertEqual(cycle.continuity_continue_object(), data["work_artifact_id"])
        point, selection = cycle._read_resume_point()
        self.assertEqual(selection["selected_by"], "CONTINUITY_HISTORY", selection)
        self.assertTrue(selection.get("owner_verified"), selection)
        self.assertEqual(selection.get("history_continuation_ref"), real_row.owner_result,
                         selection)
        self.assertEqual(selection.get("history_action_ref"), real_row.action_ref, selection)
        self.assertEqual(selection.get("history_version_ref"), head_after_first, selection)
        self.assertEqual(selection.get("history_state"), "OK", selection)
        self.assertEqual(selection["artifact_id"], data["work_artifact_id"], selection)
        self.assertEqual(point.artifact_id, data["work_artifact_id"])
        # ... and it is really the artifact the owner holds (not a Memory string)
        self.assertEqual(point.head_version_id, head_after_first)
        self.assertEqual(self.head(point.artifact_id)["version_id"], point.head_version_id)
        # ... and the next real step is pinned to that owner-confirmed version
        spec = cycle._ar0_step_spec(point, observed_at="2026-10-16T07:01:00+00:00")
        self.assertEqual(spec["payload_spec"]["artifact_id"], data["work_artifact_id"], spec)
        self.assertEqual(spec["payload_spec"]["expected_head"], head_after_first, spec)

        # the step really executes against the owner, still on the same object
        runtime.decision_service.cognition_adapter = self._continue_cognition()
        second = cycle.evaluate_once(trigger_ref="evt:d:c2", act=True)
        self.assertEqual(second["outcome"], "ACTED", second)
        self.assertTrue(second["owner_receipt"], second)
        self.assertEqual(second["artifact_selection"]["artifact_id"],
                         data["work_artifact_id"], second["artifact_selection"])
        self.assertEqual(second["artifact_selection"]["selected_by"], "CONTINUITY_HISTORY",
                         second["artifact_selection"])
        head_after_second = self.head(data["work_artifact_id"])["version_id"]
        self.assertNotEqual(head_after_second, head_after_first)

        # controlled negative 1 (stale): that same real row now names an older version, so the
        # cycle reports STALE_HEAD and still pins the owner's current head
        cycle.last_continuity = view
        stale_point, stale_selection = cycle._read_resume_point()
        self.assertEqual(stale_selection["artifact_id"], data["work_artifact_id"],
                         stale_selection)
        self.assertEqual(stale_selection["history_version_ref"], head_after_first,
                         stale_selection)
        self.assertEqual(stale_selection["history_state"], "STALE_HEAD", stale_selection)
        self.assertEqual(stale_selection["expected_head_source"], "OWNER_CURRENT_HEAD",
                         stale_selection)
        self.assertEqual(stale_point.head_version_id, head_after_second, stale_selection)

        # controlled negative 2 (weak cue): no admitted row, no artifact ref -> the bound work is
        # used instead; the object and the owner pin are unchanged
        cycle.last_continuity = {
            "past_state": "OK",
            "next_step": {"reason_code": "CONTINUE_FROM_OWNER_CONFIRMED_OBJECT",
                          "item_ref": str(first["activity_ref"]),
                          "evidence_refs": ["lexp:no-such-row"],
                          "artifact_ref": None, "continue_from_ref": None},
            "past": [],
        }
        self.assertIsNone(cycle.continuity_continue_object())
        weak_point, weak_selection = cycle._read_resume_point()
        self.assertEqual(weak_selection["selected_by"], "BOUND_WORK", weak_selection)
        self.assertEqual(weak_point.artifact_id, data["work_artifact_id"])
        self.assertEqual(weak_point.head_version_id, head_after_second)

    def test_a_weak_cue_does_not_change_the_continue_object(self):
        fixture = self._fixture_with_memory()
        cycle = fixture.cycle
        first = cycle.evaluate_once(trigger_ref="evt:d:weak", act=True)
        self.assertEqual(first["outcome"], "ACTED", first)
        cycle.work_artifact_id = fixture.data["work_artifact_id"]
        # only free text, no artifact ref: C must not point anywhere
        cycle.last_continuity = {
            "past_state": "OK",
            "next_step": {"reason_code": "CONTINUE_FROM_OWNER_CONFIRMED_OBJECT",
                          "item_ref": "actv:x", "evidence_refs": ["lexp:nope"]},
            "past": [{"source_ref": "lexp:other", "artifact_ref": "artifact:somewhere",
                      "activity_ref": "actv:x"}],
        }
        self.assertIsNone(cycle.continuity_continue_object())
        _, selection = cycle._read_resume_point()
        self.assertEqual(selection["selected_by"], "BOUND_WORK", selection)
        # a hint naming a *document that is not work* is dropped as well
        cycle.last_continuity = {
            "past_state": "OK",
            "next_step": {"reason_code": "CONTINUE_FROM_OWNER_CONFIRMED_OBJECT",
                          "item_ref": "actv:x", "evidence_refs": ["lexp:doc"]},
            "past": [{"source_ref": "lexp:doc",
                      "artifact_ref": cycle.reading_artifact_id,
                      "activity_ref": "actv:x"}],
        }
        _, excluded = cycle._read_resume_point()
        self.assertEqual(excluded["selected_by"], "CONTINUITY_ARTIFACT_EXCLUDED", excluded)

    def test_the_subjects_stay_distinct_and_the_reader_is_fresh(self):
        fixture = self._fixture_with_memory()
        data = fixture.data
        # three different subjects, none forced to be equal
        self.assertEqual(fixture.memory.port.subject, MEMORY_OWNER)
        self.assertEqual(data["subject_id"], "test:w05")
        self.assertNotEqual(fixture.memory.port.subject, data["subject_id"])
        # the canonical Activity subject is a third thing again: it is what the owners speak
        canonical = self.activity(fixture.runtime, fixture.data["work_artifact_id"]) or {}
        canonical_subject = fixture.runtime.canonical_state().get("subject_id")
        self.assertEqual(canonical_subject, "chiyo.global", canonical_subject)
        self.assertNotEqual(canonical_subject, MEMORY_OWNER)
        self.assertNotEqual(canonical_subject, data["subject_id"])
        # a fresh factory means a fresh binding instant per read
        runtime = fixture.runtime
        source = (lambda: self._continuity_reader(fixture, runtime))
        cycle = alc.AutonomousLifeCycle(
            runtime, artifact_port=fixture.port, subject_id=data["subject_id"],
            workspace_id=data["workspace_id"], artifact_grant_id=data["artifact_grant_id"],
            writer=StubWriter(), enabled=True, env=fixture.env, requests=fixture.budget,
            work_artifact_id=data["work_artifact_id"],
            continuity_source=source)
        first = cycle.read_continuity()
        second = cycle.read_continuity()
        self.assertEqual(first["binding"]["subject"], MEMORY_OWNER)
        self.assertIn(second["past_state"], {"OK", "NO_ADMITTED_HISTORY"}, second["past_state"])
        # reading writes nothing: no watermark file before a committed Decision
        self.assertFalse(cycle.continuity_path().exists(),
                         "a read must not consume/advance the eligibility watermark")


# =====================================================================================
# 4：真实自主阅读（PERSONAL_OPPORTUNITY → Decision → Activity → AR-0）
# =====================================================================================


@posix_only
class DAutonomousReadingTest(DScenarioBase):

    def _fixture(self, policy):
        prepared = self.prepare_scenario()
        env = self.instance_env()
        cognition = StubCognition(policy)
        runtime, port = self.runtime_with_memory(cognition, env, prepared.data, prepared.memory)
        fixture = self.assemble(runtime, env, cognition=cognition)
        fixture.runtime, fixture.port, fixture.data = runtime, port, prepared.data
        fixture.cognition = cognition
        return fixture

    def _offered_targets(self, fixture) -> list[list[str]]:
        """The target refs AG-1 really saw, taken from the registered source."""
        out: list[list[str]] = []
        for adapter in fixture.runtime.candidate_registry.list_adapters():
            if getattr(adapter, "source_kind", None) != "PERSONAL_OPPORTUNITY":
                continue
            items, _snap, _counts = adapter.discover(observed_at="2026-10-16T06:00:00+00:00")
            for item in items:
                out.append([str(ref) for ref in (item.get("target_refs") or [])])
        return out

    def test_reading_is_a_legal_candidate_and_really_reads_a_page(self):
        fixture = self._fixture("artifact")
        data = fixture.data
        offered = self._offered_targets(fixture)
        self.assertTrue(any(any(data["reading_artifact_id"] in ref for ref in refs)
                            for refs in offered), offered)
        # the private-items document is *not* offered as work
        self.assertFalse(any(any(data["private_items_artifact_id"] in ref for ref in refs)
                             for refs in offered), offered)
        # force the Decision to select the reading candidate
        fixture.cycle.last_continuity = None
        chosen = {"candidate": None}

        class ReadingCognition(StubCognition):
            def invoke(inner, request, *, schema_retry_index=0):
                candidates = [dict(c) for c in (getattr(request, "candidates", []) or [])]
                for candidate in candidates:
                    targets = [str(r) for r in (candidate.get("target_refs") or [])]
                    if any(data["reading_artifact_id"] in ref for ref in targets):
                        chosen["candidate"] = candidate.get("candidate_id")
                        allowed = [str(code) for code in
                                   (getattr(request, "allowed_reason_codes", []) or [])]
                        import agency_decision_ag1 as ag1

                        return ag1.CognitiveDecisionResponse(
                            model_run_ref="model_run:stub:read", model_id=inner.model_id,
                            model_version=inner.model_version,
                            raw_output={"decision": "START",
                                        "selected_candidate_id": candidate["candidate_id"],
                                        "reason_codes": allowed[:1],
                                        "short_rationale": "read a page",
                                        "deferred_candidate_ids": []},
                            latency_ms=1)
                return super().invoke(request, schema_retry_index=schema_retry_index)

        reader = ReadingCognition("artifact")
        fixture.runtime.decision_service.cognition_adapter = reader
        doc_head_before = self.head(data["private_items_artifact_id"])
        out = fixture.cycle.evaluate_once(trigger_ref="evt:d:read", act=True)
        self.assertTrue(chosen["candidate"], out)
        self.assertEqual(out["outcome"], "ACTED", out)
        self.assertIn("READING", out["step_path"], out)
        self.assertEqual(out["reading"]["page"], 1, out["reading"])
        self.assertEqual(out["reading"]["understanding"], "UNKNOWN")
        # real owner effect: one new version of the private-items document with the bookmark
        doc_head_after = self.head(data["private_items_artifact_id"])
        self.assertNotEqual(doc_head_after["version_id"], doc_head_before["version_id"])
        document = json.loads(self.content(data["private_items_artifact_id"]))
        types = {item["item_type"] for item in document["items"].values()}
        self.assertIn("source_acquisition", types)
        self.assertIn("reading_position", types)
        positions = [item for item in document["items"].values()
                     if item["item_type"] == "reading_position"]
        self.assertEqual(positions[0]["position"], 1)
        acquisitions = [item for item in document["items"].values()
                        if item["item_type"] == "source_acquisition"]
        self.assertEqual(acquisitions[0]["obtained"]["pages"], [1])
        self.assertEqual(acquisitions[0]["source_revision"],
                         self.head(data["reading_artifact_id"])["version_id"])
        # the reading Activity exists and is the single foreground
        activity_ref = out["activity_ref"]
        self.assertTrue(activity_ref, out)
        activity = self.activity(fixture.runtime, activity_ref)
        self.assertIn(activity["status"], {"ACTIVE", "WAITING"}, activity)
        self.assertTrue(activity["activity_kind"] in {"READ_PERSONAL_MATERIAL",
                                                      "PERSONAL_WORK", None}, activity)

    def test_the_work_branch_is_also_real_and_neither_is_fixed_first(self):
        fixture = self._fixture("artifact")
        data = fixture.data
        before = self.head(data["work_artifact_id"])["version_id"]
        # the work branch is chosen explicitly: which personal opportunity the generic stub
        # happens to list first is not a product fact, and this test is about the work branch.
        work = self._work_cognition(data["work_artifact_id"], data["reading_artifact_id"])
        fixture.runtime.decision_service.cognition_adapter = work
        out = fixture.cycle.evaluate_once(trigger_ref="evt:d:work", act=True)
        self.assertEqual(out["outcome"], "ACTED", out)
        self.assertTrue(work.work_offered, out)
        # ... and the reading branch was legal in the *same* window: neither is fixed first
        self.assertTrue(work.reading_offered,
                        "the reading candidate was not offered alongside the work candidate")
        self.assertEqual(out["artifact_selection"]["selected_by"], "BOUND_WORK", out)
        self.assertNotEqual(self.head(data["work_artifact_id"])["version_id"], before)
        # the private-items document was *not* touched by the work branch
        doc = json.loads(self.content(data["private_items_artifact_id"]))
        self.assertEqual(doc["items"], {}, doc)


# =====================================================================================
# 5：房间物件 + 真实 Body 只读口
# =====================================================================================


@posix_only
class DRoomAndBodyTest(DScenarioBase):

    def test_the_room_really_has_objects_and_one_can_be_linked(self):
        prepared = self.prepare_scenario()
        room = prepared.data["room_observation"]
        self.assertEqual(room["status"], "available", room)
        self.assertEqual(room["placement_authority"], "world")
        self.assertTrue(room["objects"], room)
        object_ids = [item["object_id"] for item in room["objects"]]
        self.assertIn("desk_home", object_ids)
        # a fresh read returns the same owner facts (not a cached answer)
        again = odl.observe_room(self.instance)["observation"]
        self.assertEqual([item["object_id"] for item in again["objects"]], object_ids)
        # a real observation link, on an item that really exists in the document
        memo = odl.add_memo(self.instance, body="桌上的那张纸")
        self.assertTrue(memo["ok"], memo)
        item_id = memo["write"]["item_id"]
        linked = odl.link_item_to_room_object(self.instance, item_id=item_id,
                                              object_id="desk_home")
        self.assertTrue(linked["ok"], linked)
        self.assertEqual(linked["write"]["applied"], True, linked)
        # an object that was not observed is refused
        with self.assertRaises(Exception) as caught:
            odl.link_item_to_room_object(self.instance, item_id=item_id,
                                         object_id="not_a_real_object")
        self.assertIn("OBJECT_NOT_OBSERVED", str(caught.exception))

    def test_the_scenario_really_reads_the_body_port(self):
        import life_situation_view as lsv
        from world_body_read_port import WorldBodyReadPort
        from world_body_client import WorldBodyClient

        prepared = self.prepare_scenario()
        data = prepared.data
        gateway = self.gateway()
        client = WorldBodyClient(socket_path=str(gateway.socket_path), mode="canonical",
                                 read_retries=0)
        port = WorldBodyReadPort(client)
        source = lsv.LifeSituationView(home=str(data["world_home"]), body_port=port)
        view = source.build(now="2026-10-16T06:00:00+00:00",
                            budget={"source": "life_request_budget.check", "allowed": True,
                                    "state": "ok", "path": str(odl.life_state_root(self.instance)
                                                               / "life_request_budget.json"),
                                    "utc_day": "2026-10-16", "run_id": "run:test",
                                    "utc_day_calls": 0, "utc_day_max": 8,
                                    "run_window_calls": 0, "run_window_max": 8},
                            observations=[{"observation_ref": "world:observation:1",
                                           "source_ref": "world_body_read_port.get_observation",
                                           "observed_at": "2026-10-16T05:59:00+00:00"}])
        body = view["body"]
        # the real Body read really happened: its answer carries the source's own vocabulary
        self.assertEqual(body["source"], "world_body_read_port.get_body_signals")
        self.assertIn(body["state"], {"LIMITED", "UNKNOWN", "UNAVAILABLE"})
        self.assertNotEqual(body["state"], "AVAILABLE",
                            "no positive capability fact exists; the body must not read healthy")
        self.assertTrue(body["reason_code"], body)
        self.assertFalse(body["owner_asserted"] and body["state"] == "AVAILABLE")
        kwargs = lsv.ag1_capacity_kwargs(view)
        self.assertEqual(kwargs["body_capacity_state"], body["state"])


# =====================================================================================
# 6 + 7：真实进程重启与安静段
# =====================================================================================


@posix_only
class DRestartAndQuietTest(DScenarioBase):

    RESTART_SCRIPT = textwrap.dedent(
        """
        import json, os, sys
        sys.path[:0] = [%r]
        import autonomous_life_assembly as assembly
        import one_day_life as odl

        instance = %r
        env = odl.instance_environment(instance)
        state_root = odl.life_state_root(instance)
        budget = assembly.request_budget(env, state_root)
        out = {"pid": os.getpid(),
               "run_id": env.get(odl.RUN_ID_ENV),
               "budget_view": budget.view() if budget is not None else None,
               "pause": odl.status(instance)["pause"],
               "reading": odl.reading_view(instance),
               "status_class": odl.status(instance)["state"]["state_class"]}
        print("RESTART_PROBE " + json.dumps(out, ensure_ascii=False, default=str))
        """
    )

    def test_a_real_second_process_sees_the_same_budget_pause_and_bookmark(self):
        fixture = self.prepare_scenario()
        data = fixture.data
        # a real bookmark and a real pending state before the restart
        read = odl.read_page(self.instance, page=2, unclear="这一段没懂")
        self.assertTrue(read["ok"], read)
        env = self.instance_env()
        budget = assembly.request_budget(env, odl.life_state_root(self.instance))
        self.assertIsNotNone(budget)
        budget.reserve("decision")
        budget.reserve("content")
        view_before = budget.view()
        self.assertEqual(view_before["utc_day_calls"], 2, view_before)
        # the operator confirms the pause *before* the restart
        odl.set_pause(self.instance, True, reason="d-r1:restart")

        code = self.RESTART_SCRIPT % (str(ROOT / "components" / "life" / "scripts"),
                                      str(self.instance))
        done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                              timeout=300,
                              env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(done.returncode, 0, done.stderr[-2000:])
        probe_line = [line for line in done.stdout.splitlines()
                      if line.startswith("RESTART_PROBE ")][-1]
        probe = json.loads(probe_line.split(" ", 1)[1])
        # a genuinely different process
        self.assertNotEqual(probe["pid"], os.getpid(), probe)
        # the same run identity and the same counters, read from disk by a NEW budget object
        self.assertEqual(probe["run_id"], env[odl.RUN_ID_ENV])
        self.assertEqual(probe["budget_view"]["utc_day_calls"], 2, probe["budget_view"])
        self.assertEqual(probe["budget_view"]["utc_day_max"],
                         view_before["utc_day_max"], probe["budget_view"])
        self.assertEqual(probe["budget_view"]["run_window_max"],
                         view_before["run_window_max"], probe["budget_view"])
        # the pause confirmed before the restart is still effective after it
        self.assertTrue(probe["pause"]["paused"], probe["pause"])
        self.assertEqual(probe["status_class"], odl.STATE_PAUSED, probe)
        # the bookmark / unclear marker survived
        self.assertTrue(probe["reading"]["positions"], probe["reading"])
        self.assertEqual(probe["reading"]["artifact_id"], data["reading_artifact_id"])
        # and the in-process view agrees
        self.assertTrue(odl.status(self.instance)["pause"]["paused"])
        odl.set_pause(self.instance, False, reason="d-r1:restart")

    def test_a_quiet_window_makes_no_model_or_http_request(self):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        from test_one_day_life import StubModel

        hits: list[str] = []

        class Counting(StubModel):
            def _reply(self, body):
                hits.append(str(body.get("model") or "hit"))
                return super()._reply(body)

        model = Counting(policy="no_action")
        self.addCleanup(model.stop)
        prepared = self.prepare_scenario()
        env = self.instance_env()
        env["NYA02_MODEL_BASE_URL"] = model.base_url
        env["NYA02_MODEL_NAME"] = "loopback-stub"
        env["NYA02_MODEL_API_KEY"] = "not-a-real-key"
        cognition = StubCognition("no_action")
        runtime, port = self.runtime_with_memory(cognition, env, prepared.data, prepared.memory)
        fixture = self.assemble(runtime, env, cognition=cognition)
        # 1) a genuinely quiet window: no bound work, no reading material and nothing observed
        #    in the World.  A reachable place *is* real material (a round would legally ask the
        #    model where to go), so the quiet window is the one where the observation seam
        #    reports nothing -- not the one where the observation is silently ignored.
        cycle = fixture.cycle
        cycle.artifact_port = None
        cycle.artifact_source = None
        cycle.world_opportunity_provider = lambda: []
        quiet = cycle.evaluate_once(trigger_ref="evt:d:quiet", act=False)
        self.assertEqual(quiet["outcome"], "NO_MATERIAL", quiet)
        self.assertEqual(quiet["llm_calls"], 0)
        self.assertEqual(cognition.call_count, 0)
        self.assertEqual(hits, [])
        # 2) a real NO_ACTION window is exactly one decision request -- not "zero model"
        cycle.artifact_port = port
        out = cycle.evaluate_once(trigger_ref="evt:d:noaction", act=True)
        self.assertEqual(out["outcome"], "NO_ACTION", out)
        self.assertEqual(out["llm_calls"], 1)
        self.assertEqual(cognition.call_count, 1)
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()

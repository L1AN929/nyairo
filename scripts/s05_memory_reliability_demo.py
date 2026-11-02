#!/usr/bin/env python3
"""S05 F-066..F-071 验收 demo：有来源经历、交接与接纳分离、同根去重、分层，
以及每一项实际做到哪里。

做什么
======
在全新的隔离根里用**真实** Supply owner、**真实** M0（EvidenceStore +
MemoryControlLedger）、**真实** AG-1/AG-2、正式 ``build_production_life_runtime`` +
``assemble_autonomous_life``，以及**真实** Memory 准入 consumer：

1. **F-066 有来源经历**：一次真实作品修改得到 owner 确证结果 → AG-2 结果级 handoff
   （``lexp:result:``）→ consumer 真实写入 M0，得到一条带来源 refs 的 M0 Life
   experience。设计 Q27 的 Episode 分组尚未实现，因此只证「M0 source 可证」，
   不称 Episode 完成。
2. **F-067 交接与接纳分离**：读面分开显示源事件 refs、交接投递 receipt 和 M0 记录
   身份；三者任一缺失都不由另外两项推定。
3. **F-068 共同根事件去重**：同一 Activity 的第二轮真实修改得到另一个确证版本 →
   另一个 handoff、另一条 M0 记录，不被旧 handoff 误吞；把第一轮 handoff 重投一次 →
   M0 返回 ``duplicate``，零新记录、零新版本。仅此现有存储层。
4. **F-069 raw 与 summary 分层**：源证据 / 已接纳 Memory 记录是 AVAILABLE；
   原始记录可读，但 ``episode_summary`` 不存在，具名 ``NOT_IMPLEMENTED``。
5. **F-070 派生失效未实现**：``embedding_index`` / ``derived_invalidation`` 同样
   ``NOT_IMPLEMENTED``；没有派生层就没有可失效的依赖边，不造空表。
6. **F-071 语义“零新记录”未实现**：existing admission 的 ``DUPLICATE`` 是 M0 自己的
   幂等准入结果（零新记录），但设计 Q30 的语义 ``DO_NOT_ADMIT`` / NO_CHANGE 评估器
   不存在，具名报 ``NOT_IMPLEMENTED``。
7. **额外验证（不是 F-071）**：正规关闭 runtime 与 Supply owner，再在同一 root
   重装配，只读读回同一 handoff、同一投递状态、同一 Memory 记录；零新 Action /
   版本 / Memory 记录。
8. **读端负控（可撤，非治理）**：只在读口注入一次可撤的读取故障，验证
   list/read/context 返回具名失败，而不是空表或“可召回”；撤除后恢复正常读取。
   注入不改治理、不写控制记录。

不做什么
========
**不运行治理 DELETE / CORRECT 的扣留链**（NOT_RUN）：既有 DELETE 是原任务不允许的
操作；CORRECT 需要一条直接用户输入事件，本隔离演示没有真实来源。因此“被治理后
不可召回”的链只由只读模块与单测按既有形状覆盖，不在本 demo 里制造，也不把读端
故障注入说成治理撤权。
不调用产品模型、不联网、不起浏览器、不读写任何既有实例或旧 root、不改全局默认 gate
（只在本进程环境里置 off 并在 finally 恢复）、不物理删除、不测试旧备份、
不输出正文/描述/prompt/reasoning/token。

退出码：0 = 全部断言成立；1 = 任一断言失败或夹具起不来。
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "tests"), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge"),
              str(ROOT / "components" / "native"),
              str(ROOT / "components" / "native" / "src"),
              str(ROOT / "components" / "native" / "memory_runtime_v1")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from app.evidence import EvidenceStore  # noqa: E402
from life_experience_port import (  # noqa: E402
    LifeExperiencePort,
    life_conversation_id,
)
from life_experience_memory_consumer import MemoryAdmissionExperienceConsumer  # noqa: E402
from memory_control import MemoryControlLedger  # noqa: E402
from lifesupply.service.life_supply import prepare_socket_path  # noqa: E402

import autonomous_life_assembly as assembly  # noqa: E402
import life_artifact_port as lap  # noqa: E402
import life_integration_ag2 as ag2  # noqa: E402
import life_memory_review as review  # noqa: E402
import life_runtime_production as lrp  # noqa: E402
import managed_artifact_action_adapter as maaa  # noqa: E402
import one_day_life as odl  # noqa: E402
import decision_review as dr  # noqa: E402
import agency_decision_ag1 as ag1  # noqa: E402

from tests.test_one_day_life import (  # noqa: E402
    Services,
    StubObservation,
    StubWriter,
    WorldGateway,
)

GATE_ENV = "WORLD_BODY_INTENT_MUTATION_GATE"
SCRATCH = Path(os.environ.get("NYAIRO_S05_MEMORY_ROOT")
               or "/tmp/nyairo-s05-memory-reliability")
OWNER = "chiyo"
#: 一次固定、明确的合成理由码：不是她的原话，也不冒充。
STUB_REASON = "WORLD_OPPORTUNITY_OBSERVED"


def _check(condition: bool, message: str, detail=None) -> None:
    if condition:
        return
    print("DEMO_FAIL: %s" % message)
    if detail is not None:
        print(json.dumps(detail, ensure_ascii=False, default=str, indent=1)[:6000])
    raise SystemExit(1)


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ScriptedCognition:
    """只从正式 AG-1 给出的候选里挑；优先选那件真实作品，绝不自己造候选。"""

    model_id = "stub:s05-memory-reliability"
    model_version = "s05-memory-reliability-v1"

    def __init__(self, work_artifact_id: str) -> None:
        self.work_artifact_id = str(work_artifact_id)
        self.calls = 0
        self.mode = "work"
        self.work_offered = False
        self.offered: list[dict] = []

    def _response(self, decision: str, candidate_id, reason: str):
        return ag1.CognitiveDecisionResponse(
            model_run_ref="model_run:s05-memory:%d" % self.calls,
            model_id=self.model_id, model_version=self.model_version,
            raw_output={"decision": decision, "selected_candidate_id": candidate_id,
                        "reason_codes": [reason], "short_rationale": "stub: bounded choice",
                        "deferred_candidate_ids": []},
            latency_ms=1)

    def invoke(self, request, *, schema_retry_index: int = 0):
        self.calls += 1
        allowed = [str(code) for code in
                   (getattr(request, "allowed_reason_codes", []) or [])]
        reason = STUB_REASON if STUB_REASON in allowed else (allowed[:1] or [STUB_REASON])[0]
        candidates = [dict(row) for row in (getattr(request, "candidates", None) or [])]
        self.offered.append({
            "mode": self.mode,
            "candidates": [
                {"kind": row.get("candidate_kind"),
                 "refs": [str(ref) for ref in (row.get("target_refs") or [])][:6],
                 "legal": [str(item) for item in (row.get("legal_decisions") or [])]}
                for row in candidates
            ],
        })
        for candidate in candidates:
            refs = [str(ref) for ref in (candidate.get("target_refs") or [])]
            refs.append(str(candidate.get("source_ref") or ""))
            # 同一件作品会同时给出“续写”和“收尾”两个候选；收尾候选带 closeout: 前缀。
            # 本 demo 要真实版本变化，必须选续写，不能选收尾（收尾不产生新版本）。
            if any(ref.startswith("closeout:") for ref in refs):
                continue
            if not any(self.work_artifact_id in ref for ref in refs):
                continue
            legal = [str(item) for item in (candidate.get("legal_decisions") or [])]
            self.work_offered = True
            if self.mode == "continue" and "CONTINUE" in legal:
                return self._response("CONTINUE", candidate.get("candidate_id"), reason)
            if "START" in legal:
                return self._response("START", candidate.get("candidate_id"), reason)
        if self.mode == "continue":
            for candidate in candidates:
                refs = [str(ref) for ref in (candidate.get("target_refs") or [])]
                if any(ref.startswith("closeout:") for ref in refs):
                    continue
                legal = [str(item) for item in (candidate.get("legal_decisions") or [])]
                if "CONTINUE" in legal:
                    return self._response("CONTINUE", candidate.get("candidate_id"), reason)
        return self._response("NO_ACTION", None, reason)


def _physical_rows(evidence_db: Path) -> int:
    """只读计数：证明 duplicate 没多插、控制删除没有物理删。不是治理读路径。"""

    connection = sqlite3.connect(Path(evidence_db).resolve().as_uri() + "?mode=ro", uri=True,
                                 timeout=2)
    try:
        return int(connection.execute("SELECT COUNT(*) FROM evidence_events").fetchone()[0])
    finally:
        connection.close()


class _FlakyReadPort:
    """只在 demo 读口注入一次可撤故障：包住真实 port，不碰写/治理。"""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.failing = False

    def search(self, *args, **kwargs):
        if self.failing:
            raise RuntimeError("injected source read failure (demo, reversible)")
        return self.inner.search(*args, **kwargs)

    def read(self, *args, **kwargs):
        if self.failing:
            raise RuntimeError("injected source read failure (demo, reversible)")
        return self.inner.read(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def _memory_fixture(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    evidence = root / "evidence.sqlite"
    controls = root / "controls.sqlite"
    store = EvidenceStore(evidence)
    ledger = MemoryControlLedger(controls, evidence, owner=OWNER,
                                 conversation=life_conversation_id(OWNER), authority="")
    ledger.initialize()
    port = LifeExperiencePort(evidence, controls_db=controls, subject=OWNER, authority_db="")
    consumer = MemoryAdmissionExperienceConsumer(port=port, enabled=True)
    return {"root": root, "evidence": evidence, "controls": controls, "store": store,
            "ledger": ledger, "port": port, "consumer": consumer}


def _build_runtime(state_root: Path, instance: Path, env: dict, services: Services,
                   data: dict, cognition, consumer):
    port = lap.LifeArtifactPort(str(services.supply_path), data["subject_id"])
    adapter = maaa.SupplyManagedArtifactActionAdapter(
        port, grant_id=data["artifact_grant_id"], workspace_id=data["workspace_id"])
    switches = ag2.LifeRuntimeKillSwitches(
        life_runtime_enabled=True, agency_enabled=True,
        action_execution_enabled=True, proactive_enabled=False)
    runtime = lrp.build_production_life_runtime(
        state_root, kill_switches=switches, cognition_adapter=cognition,
        install_root=instance / "life", artifact_action_adapter=adapter,
        experience_consumer=consumer)
    return runtime


def _assemble(runtime, env: dict, state_root: Path, writer):
    observation = StubObservation(env)
    bind = getattr(observation, "bind_environment", None)
    if callable(bind):
        bind(env)
    assembled = assembly.assemble_autonomous_life(
        runtime, environment=env, state_root=state_root,
        world_provider=observation, writer=writer, enabled=True)
    return assembled


def _service(port, memory) -> "lms.LifeMemoryService":
    import life_memory_service as lms

    return lms.LifeMemoryService(
        port, owner=OWNER, database=str(memory["evidence"]),
        controls_database=str(memory["controls"]), authority_database=None, source="demo")


def main() -> int:
    stamp = "%s-%d" % (time.strftime("%Y%m%d-%H%M%S"), os.getpid())
    run_root = SCRATCH / stamp
    run_root.mkdir(parents=True, exist_ok=True)
    saved_env = {GATE_ENV: os.environ.get(GATE_ENV)}
    summary: dict = {
        "root": str(run_root),
        "checks": {},
        "limitations": [
            "Decision and content are scripted doubles; no product model was called",
            "the Supply owner, M0, the control ledger, AG-1, AG-2 and LR-2 are real code over real files/sockets",
            "Episode grouping / episode summary / embedding index are NOT implemented and are reported as such",
            "F-066 is only sourced at the existing M0 experience layer; it is not Episode completion",
            "the governance DELETE/CORRECT withholding chain is NOT_RUN: no DELETE is issued, and CORRECT needs a direct user-input event this demo does not have",
            "the read-path negative control is a reversible injected source-read fault, not governance revocation",
            "F-071's semantic NO_CHANGE/DO_NOT_ADMIT evaluator does not exist; DUPLICATE is only M0 idempotent admission",
            "the restart is a proper in-process stop/re-assemble on the same root, not a different PID or a crash",
            "no natural model adoption: this is not G1, not a 24h run",
        ],
    }
    services = None
    gateway = None
    runtime = None
    runtime2 = None
    services2 = None
    memory = None
    try:
        instance = run_root / "instance"
        memory = _memory_fixture(run_root / "memory")
        services = Services(run_root / "services")
        prepared = odl.prepare(
            instance=instance, supply_socket=services.supply_path,
            governance_socket=services.gov_path, subject="test:s05-memory-reliability",
            title="S05 memory reliability demo",
            seed_content="验收夹：这一句只用于准备一个真实作品。\n",
            memory_db=memory["evidence"], memory_controls_db=memory["controls"],
            memory_subject=OWNER)
        _check(bool(prepared.get("ok")), "instance preparation failed", prepared)
        data = odl.read_instance(instance)
        world_home = Path(str(data["world_home"]))
        gateway = WorldGateway(world_home, world_id=data["world_id"],
                               environment=odl.instance_environment(instance))
        env = odl.instance_environment(instance)
        env.update(gateway.environment())
        env[GATE_ENV] = "off"
        os.environ[GATE_ENV] = "off"
        state_root = odl.life_state_root(instance)
        cognition = ScriptedCognition(str(data.get("work_artifact_id") or ""))
        runtime = _build_runtime(state_root, instance, env, services, data, cognition,
                                 memory["consumer"])
        assembled = _assemble(runtime, env, state_root, StubWriter())
        _check(assembled.get("state") == assembly.READY, "assembly not READY", assembled)
        cycle = assembled["cycle"]
        cycle.world_home = str(world_home)
        cycle.work_artifact_id = str(data.get("work_artifact_id") or "")
        cycle.reading_artifact_id = str(data.get("reading_artifact_id") or "")
        flaky_port = _FlakyReadPort(memory["port"])
        service = _service(flaky_port, memory)
        readers = dr.open_readers(state_root)
        integration_read = readers["integration_read"]

        def state() -> dict:
            return dict(integration_read.read_state() or {})

        supply_socket = str(services.supply_path)

        def head_version() -> str:
            reply = odl.supply_call(supply_socket, {
                "op": "read_head", "subject": data["subject_id"],
                "artifact_id": data["work_artifact_id"]})
            return str((reply.get("head") or {}).get("version_id") or "")

        baseline_physical = _physical_rows(memory["evidence"])

        # -----------------------------------------------------------------------------
        # F-066 有来源经历 / F-067 交接与接纳分离：
        # 第一轮真实作品修改 → owner 确证结果 → 结果级 handoff → M0 source 记录。
        # Episode 分组未实现，这里只证 M0 source 可证。
        # -----------------------------------------------------------------------------
        version_before = head_version()
        first = cycle.evaluate_once(trigger_ref="evt:s05:r1", observed_at=_iso(), act=True)
        _check(first.get("outcome") == "ACTED", "round 1 did not act",
               {"outcome": first.get("outcome"), "decision": first.get("decision"),
                "error": first.get("error"), "detail": first.get("detail"),
                "offered": cognition.offered})
        handoff1 = dict(first.get("experience_handoff") or {})
        hid1 = str(handoff1.get("handoff_id") or "")
        _check(hid1.startswith("lexp:result:"),
               "round 1 did not produce a result-level handoff", handoff1)
        _check(handoff1.get("disposition") == "ACCEPT",
               "round 1 handoff was not admitted", handoff1)
        version1 = head_version()
        _check(version1 and version1 != version_before,
               "round 1 did not create a real owner version", {"before": version_before,
                                                                "after": version1})
        row1 = review.find_memory_records(service, hid1)
        _check(row1.get("status") == review.RECALL_AVAILABLE and len(row1["records"]) == 1,
               "round 1 has no accepted Memory record", row1)
        event1 = dict(row1["records"][0])
        review1 = review.review_handoff(state(), hid1, service)
        _check(review1["separation"]["handoff_succeeded"] is True,
               "round 1 handoff success was not reported", review1["separation"])
        _check(review1["separation"]["memory_recallable"] is True,
               "round 1 admitted record is not currently recallable", review1["separation"])
        _check(review1["handoff"]["owner_confirmed"]["version_ref"]
               == "artifactversion:" + version1,
               "the handoff does not carry the owner's confirmed version",
               {"confirmed": review1["handoff"]["owner_confirmed"],
                "owner_head": version1})

        # -----------------------------------------------------------------------------
        # F-068 共同根事件去重（异版本不误吞）：
        # 同 Activity 的第二轮真实修改 → 另一个确证版本，不被旧 handoff 吞。
        # -----------------------------------------------------------------------------
        cognition.mode = "continue"
        second = cycle.evaluate_once(trigger_ref="evt:s05:r2", observed_at=_iso(), act=True)
        _check(second.get("outcome") == "ACTED", "round 2 did not act", second.get("error"))
        handoff2 = dict(second.get("experience_handoff") or {})
        hid2 = str(handoff2.get("handoff_id") or "")
        _check(hid2.startswith("lexp:result:") and hid2 != hid1,
               "round 2 was merged into the first handoff", {"first": hid1, "second": hid2})
        _check(handoff2.get("disposition") == "ACCEPT",
               "round 2 handoff was not admitted", handoff2)
        version2 = head_version()
        _check(version2 and version2 != version1,
               "round 2 did not create a second owner version", version2)
        row2 = review.find_memory_records(service, hid2)
        _check(row2.get("status") == review.RECALL_AVAILABLE and len(row2["records"]) == 1,
               "round 2 has no separate Memory record", row2)
        event2 = dict(row2["records"][0])
        _check(event1["event_id"] != event2["event_id"],
               "two confirmed versions collapsed onto one Memory row",
               {"one": event1["event_id"], "two": event2["event_id"]})

        # -----------------------------------------------------------------------------
        # F-068 同结果重复（现有存储层） / F-071 语义评估未实现：
        # 同一结果重投 → M0 duplicate，零新记录、零新版本；
        # DUPLICATE 只是 M0 幂等准入，不是设计 Q30 的语义 NO_CHANGE/DO_NOT_ADMIT。
        # -----------------------------------------------------------------------------
        version_before_replay = head_version()
        physical_before_replay = _physical_rows(memory["evidence"])
        replay = runtime.coordinator.dispatch_experience_handoffs(handoff_id=hid1)
        deliveries = list(replay.get("deliveries") or [])
        _check(deliveries and deliveries[-1].get("consumer_disposition") == "DUPLICATE",
               "re-delivery was not reported as DUPLICATE", replay)
        physical_after_replay = _physical_rows(memory["evidence"])
        _check(physical_after_replay == physical_before_replay,
               "re-delivery inserted another M0 row",
               {"before": physical_before_replay, "after": physical_after_replay})
        _check(head_version() == version_before_replay,
               "re-delivery replayed the writer and changed the artifact", head_version())
        replay_row = review.find_memory_records(service, hid1)
        _check(replay_row.get("status") == review.RECALL_AVAILABLE
               and replay_row["records"][0]["event_id"] == event1["event_id"],
               "re-delivery did not land on the original Memory record", replay_row)
        replay_review = review.review_handoff(state(), hid1, service)
        _check(replay_review["no_admission"]["zero_new_record"] is True,
               "the M0 duplicate answer was not reported as zero-new-record",
               replay_review["no_admission"])
        _check(replay_review["no_admission"]["semantic_do_not_admit"] == review.NOT_IMPLEMENTED,
               "a semantic DO_NOT_ADMIT evaluator was claimed without existing",
               replay_review["no_admission"])

        # -----------------------------------------------------------------------------
        # F-069 raw 与 summary 分层 / F-070 派生失效未实现 / F-066 的 Episode 部分未实现：
        # 原始来源与已接纳 Memory 可读；summary、Episode 分组、embedding 索引、
        # 派生失效都没有实现，不得以空表冒充。
        # -----------------------------------------------------------------------------
        layered = review.review_handoff(state(), hid1, service)["layers"]
        _check(layered["source_evidence"]["status"] == "AVAILABLE",
               "the source evidence layer is missing", layered)
        _check(layered["accepted_memory_record"]["status"] == "AVAILABLE",
               "the accepted Memory layer is missing", layered)
        for layer in ("episode_grouping", "episode_summary", "embedding_index",
                      "derived_invalidation"):
            _check(layered[layer]["status"] == review.NOT_IMPLEMENTED,
                   "an unimplemented derived layer was claimed as available",
                   {layer: layered[layer]})

        # -----------------------------------------------------------------------------
        # 读端负控（可撤，非治理）：读口故障必须具名失败，不得变成空表或“可召回”；
        # 撤除后必须恢复读取。这里不写任何控制记录，也不改治理算法。
        # NOTE(NOT_RUN): 治理 DELETE/CORRECT 的扣留链不在本演示运行 —— DELETE 不被本任务允许，
        # CORRECT 需要一条直接用户输入事件，这里没有真实来源。那条链只由只读模块与单测
        # 按既有形状覆盖，不把本节的故障注入说成治理撤权。
        # -----------------------------------------------------------------------------
        physical_before_fault = _physical_rows(memory["evidence"])
        healthy1 = review.find_memory_records(service, hid1)
        healthy2 = review.find_memory_records(service, hid2)
        _check(healthy1.get("status") == review.RECALL_AVAILABLE
               and healthy2.get("status") == review.RECALL_AVAILABLE,
               "the baseline read used for the negative control is not available",
               {"one": healthy1.get("status"), "two": healthy2.get("status")})
        flaky_port.failing = True
        try:
            failed_rows = review.find_memory_records(service, hid1)
            _check(failed_rows.get("status") == "UNAVAILABLE"
                   and failed_rows.get("records") is None,
                   "a failed source read was reported as a recall answer", failed_rows)
            _check(failed_rows.get("reason") == "MEMORY_READ_FAILED",
                   "the failed source read was not named", failed_rows)
            failed_read = service.read_experience(event1["event_id"])
            _check(failed_read.get("status") == "UNAVAILABLE",
                   "read_experience turned a failed read into another answer",
                   failed_read.get("status"))
            failed_context = service.context(query=hid1, limit=5)
            _check(failed_context.get("status") == "UNAVAILABLE",
                   "context turned a failed read into an empty or successful answer",
                   failed_context.get("status"))
            failed_review = review.review_handoff(state(), hid1, service)
            _check(failed_review["current_recall"]["status"] == "UNAVAILABLE"
                   and failed_review["separation"]["memory_recallable"] is False,
                   "the review conflated a read failure with current recall",
                   failed_review["current_recall"])
            _check(failed_review["layers"]["accepted_memory_record"]["status"]
                   == "UNAVAILABLE",
                   "the layer view turned a read failure into a state answer",
                   failed_review["layers"]["accepted_memory_record"])
        finally:
            flaky_port.failing = False
        restored1 = review.find_memory_records(service, hid1)
        restored2 = review.find_memory_records(service, hid2)
        _check(restored1.get("status") == review.RECALL_AVAILABLE
               and restored2.get("status") == review.RECALL_AVAILABLE,
               "the injected read fault was not fully reversible",
               {"one": restored1.get("status"), "two": restored2.get("status")})
        _check(_physical_rows(memory["evidence"]) == physical_before_fault,
               "the read-path negative control wrote evidence rows",
               {"before": physical_before_fault,
                "after": _physical_rows(memory["evidence"])})
        # 既有 epoch 规则只在出现控制记录时生效；本演示没有控制记录，因此不做扣留断言。
        projection_now = memory["ledger"].projection()
        _check(projection_now.healthy and int(projection_now.controls) == 0,
               "a control row appeared although no governance control was issued",
               {"healthy": projection_now.healthy, "controls": projection_now.controls,
                "reason": projection_now.reason})

        # -----------------------------------------------------------------------------
        # 额外验证（不是 F-071）：正规停启/重装配，只读读回身份、投递与 Memory 记录。
        # 本演示没有治理控制，所以重启后不强求“已治理扣留”，只验零重放。
        # -----------------------------------------------------------------------------
        state_before = state()
        handoffs_before = sorted((state_before.get("handoff_events_by_id") or {}).keys())
        deliveries_before = {
            hid: (state_before.get("handoff_deliveries_by_id") or {}).get(hid)
            for hid in handoffs_before}
        physical_before_restart = _physical_rows(memory["evidence"])
        head_before_restart = head_version()

        runtime.close()
        runtime = None
        socket_path = supply_socket
        services.stop()
        services = None
        # 正规守卫：只清自有、已停止的 socket；活路径/非 socket 会拒绝。
        guard = prepare_socket_path(Path(str(socket_path)))
        services2 = Services(run_root / "services")
        port2 = LifeExperiencePort(memory["evidence"], controls_db=memory["controls"],
                                   subject=OWNER, authority_db="")
        consumer2 = MemoryAdmissionExperienceConsumer(port=port2, enabled=True)
        runtime2 = _build_runtime(state_root, instance, env, services2, data, cognition, consumer2)
        assembled2 = _assemble(runtime2, env, state_root, StubWriter())
        _check(assembled2.get("state") == assembly.READY,
               "the re-assembled runtime is not READY", assembled2)
        service2 = _service(port2, memory)
        readers2 = dr.open_readers(state_root)
        state_after = dict(readers2["integration_read"].read_state() or {})
        handoffs_after = sorted((state_after.get("handoff_events_by_id") or {}).keys())
        _check(handoffs_after == handoffs_before,
               "re-assembly changed the handoff set",
               {"before": handoffs_before, "after": handoffs_after})
        for hid in handoffs_before:
            before = deliveries_before.get(hid) or {}
            after = (state_after.get("handoff_deliveries_by_id") or {}).get(hid) or {}
            _check(before.get("attempt") == after.get("attempt")
                   and before.get("consumer_disposition") == after.get("consumer_disposition"),
                   "re-assembly replayed a handoff delivery", {"hid": hid,
                                                               "before": before, "after": after})
        _check(_physical_rows(memory["evidence"]) == physical_before_restart,
               "re-assembly created a new Memory record", _physical_rows(memory["evidence"]))
        _check(head_version() == head_before_restart,
               "re-assembly created a new artifact version", head_version())
        # 没有治理控制时，两条 M0 记录在重装配后都应仍可由既有读口读回。
        restored_after_restart1 = review.find_memory_records(service2, hid1)
        restored_after_restart2 = review.find_memory_records(service2, hid2)
        _check(restored_after_restart1.get("status") == review.RECALL_AVAILABLE
               and restored_after_restart2.get("status") == review.RECALL_AVAILABLE,
               "a Memory record was lost across re-assembly",
               {"one": restored_after_restart1.get("status"),
                "two": restored_after_restart2.get("status")})

        summary["checks"] = {
            "F066_sourced_memory_record_from_owner_result_M0_source_only": True,
            "F066_episode_grouping_not_implemented": True,
            "F067_handoff_and_admission_separated": True,
            "F068_distinct_confirmed_versions_two_records": True,
            "F068_same_result_redelivery_duplicate_zero_new_row": True,
            "F069_raw_source_and_accepted_memory_available": True,
            "F069_episode_summary_not_implemented": True,
            "F070_embedding_index_and_derived_invalidation_not_implemented": True,
            "F071_semantic_no_change_not_implemented": True,
            "F071_duplicate_is_only_m0_idempotent_admission": True,
            "negative_control_read_fault_named_and_reversible": True,
            "extra_restart_same_handoffs_and_zero_replay": True,
        }
        summary["not_run"] = {
            "governance_delete_or_correct_withholding_chain":
                "NOT_RUN: DELETE is not permitted for this task, and CORRECT needs a direct "
                "user-input event this isolated demo does not have",
        }
        summary["facts"] = {
            "handoff_ids": [hid1, hid2],
            "handoff_identity_namespaces": [review.handoff_identity(hid1),
                                            review.handoff_identity(hid2)],
            "memory_event_ids": [event1["event_id"], event2["event_id"]],
            "operator_versions": [version1, version2],
            "delivery_dispositions": {
                hid1: "ACCEPT+DUPLICATE_retry",
                hid2: "ACCEPT",
            },
            "read_fault_status_while_injected": "UNAVAILABLE",
            "read_status_after_fault_removed": [restored1["status"], restored2["status"]],
            "memory_control_rows": int(projection_now.controls),
            "physical_memory_rows": _physical_rows(memory["evidence"]),
            "restart_socket_guard": guard,
        }
        summary["layers"] = layered
        summary["budget"] = {"product_model_calls": 0, "network": False}
        print("DEMO_OK")
        print(json.dumps(summary, ensure_ascii=False, indent=1))
        return 0
    finally:
        for name, value in saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        for closeable in (runtime2, runtime):
            try:
                if closeable is not None:
                    closeable.close()
            except Exception:
                pass
        for stopping in (services2, services):
            try:
                if stopping is not None:
                    stopping.stop()
            except Exception:
                pass
        try:
            if gateway is not None:
                gateway.stop()
        except Exception:
            pass
        try:
            if memory is not None:
                memory["store"].close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())

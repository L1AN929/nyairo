#!/usr/bin/env python3
"""S04 F-049/F-050/F-051 验收 demo：决定摘要、真实未选项、Decision/Adoption 分离、旧决定复核。

做什么
======
在全新的 ``/tmp`` 根里，用**真实** Supply owner、**真实** AG-1/AG-2/Activity owner 和
正式 ``build_production_life_runtime`` + ``assemble_autonomous_life`` 装配 Life cycle。
Decision 与内容都是脚本替身，业务时钟是机械的（``Clock``），但 AG-1/AG-2/LR-2 与持久化
全是正式代码：

1. **正控 + 未采用（F-050）**：AG-1 正式 ``evaluate_opportunity`` 单独提交一条 ``DEFER``
   决定，**不**进入 AG-2。读面必须报「有决定 / 未采用」，且用正式
   ``validate_decision_for_adoption`` 立即复核时**通过**（活动状态没变）。
2. **主链（F-049）**：正式 ``cycle.evaluate_once`` 让脚本认知 START 一个真实候选；
   真实 AG-1 提交决定、真实 AG-2 采纳并创建 Activity。读面给出摘要、选中项、
   既有处置表里已记录的未选/延期项与理由代码；记录里没有的字段一律 ``NOT_RECORDED``。
3. **旧决定复核（F-051）**：同一条已采纳决定在 Activity 已变化之后，再用正式准入读口
   复核，得到具名拒绝；它在变化前是否有效由第 1 步的正控证明。复核是纯读，前后
   Decision/Adoption/Activity 快照不变。
4. **重装配（零重放）**：重新打开同一 state root 的只读端口，读回同一条 Decision/Adoption，
   决定数/采纳数/Activity 数/Action 数都不变；那条未采用的 DEFER 仍是历史，没有被自动采纳。

不做什么
========
不调用产品模型、不起 HTTP/浏览器、不联网、不读写任何既有实例、不改全局默认 gate
（只在本进程环境字典里置 off 并在 finally 恢复）、不删数据、不输出隐藏推理或完整 prompt。
临时根保留。

退出码：0 = 全部断言成立；1 = 任一断言失败或服务起不来。
"""

from __future__ import annotations

import json
import os
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
              str(ROOT / "components" / "world" / "service" / "bridge")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import action_intent  # noqa: E402
import agency_decision_ag1 as ag1  # noqa: E402
import autonomous_life_assembly as assembly  # noqa: E402
import autonomous_life_cycle as alc  # noqa: E402
import decision_review as dr  # noqa: E402
import life_artifact_port as lap  # noqa: E402
import life_integration_ag2 as ag2  # noqa: E402
import life_runtime_production as lrp  # noqa: E402
import managed_artifact_action_adapter as maaa  # noqa: E402
import one_day_life as odl  # noqa: E402
import world_move_opportunity_source as wmos  # noqa: E402
from world_body_client import WorldBodyClient  # noqa: E402
from world_journey_action_adapter import (  # noqa: E402
    JourneyWorldResolver, RealJourneyWorldActionAdapter)

from tests.test_one_day_life import Services, StubWriter, WorldGateway  # noqa: E402
from tests.test_world_daily_life_continuous import (  # noqa: E402
    WORLD_PACKAGE, Clock, episode_instant)

GATE_ENV = "WORLD_BODY_INTENT_MUTATION_GATE"
BRIDGE_DIR = ROOT / "components" / "world" / "service" / "bridge"
SCRATCH = Path(os.environ.get("NYAIRO_S04_DECISION_REVIEW_ROOT")
               or "/tmp/nyairo-s04-decision-review")
#: 一次只用两条固定合成理由（不是她的原话，也不冒充）。
STUB_REASON = "NO_ACTION_CHOSEN"


def _check(condition: bool, message: str, detail=None) -> None:
    if condition:
        return
    print("DEMO_FAIL: %s" % message)
    if detail is not None:
        print(json.dumps(detail, ensure_ascii=False, default=str, indent=1)[:8000])
    raise SystemExit(1)


class _ScriptedDecision:
    """Decision 替身：只在正式 AG-1 的合法集里挑，返回原始 JSON。"""

    model_id = "stub:s04-decision-review"
    model_version = "s04-decision-review-v1"

    def __init__(self) -> None:
        self.policy = "defer"
        self.calls = 0
        self.summaries: list[dict] = []

    @staticmethod
    def _targets(row) -> list[str]:
        return [str(ref) for ref in (row.get("target_refs") or [])]

    def invoke(self, request, *, schema_retry_index: int = 0):
        self.calls += 1
        candidates = [dict(row) for row in (getattr(request, "candidates", None) or [])]
        allowed = [str(code) for code in (getattr(request, "allowed_reason_codes", None) or [])]
        # 只用本轮真的开放、且语义配得上 START 的固定合成理由；都不是她的原话。
        preferred = "WORLD_OPPORTUNITY_OBSERVED"
        reason = [preferred] if preferred in allowed else (allowed[:1] or [STUB_REASON])
        self.summaries.append({
            "policy": self.policy,
            "candidates": [
                {"candidate_id": row.get("candidate_id"),
                 "candidate_kind": row.get("candidate_kind"),
                 "source_kind": row.get("source_kind"),
                 "legal_decisions": list(row.get("legal_decisions") or [])}
                for row in candidates
            ],
        })
        raw = {"decision": "NO_ACTION", "selected_candidate_id": None,
               "reason_codes": [STUB_REASON], "short_rationale": "stub: none",
               "deferred_candidate_ids": []}
        if self.policy == "defer":
            raw = {"decision": "DEFER", "selected_candidate_id": None,
                   "reason_codes": reason,
                   "short_rationale": "stub: hold this round, nothing adopted",
                   "deferred_candidate_ids": []}
        elif self.policy == "start_any":
            starts = [row for row in candidates
                      if "START" in (row.get("legal_decisions") or [])]
            # 优先不触发内容生成的候选；阅读候选留到没有别的可用时。
            starts.sort(key=lambda r: any(ref.startswith("reading:")
                                           for ref in self._targets(r)))
            if starts:
                raw = {"decision": "START",
                       "selected_candidate_id": starts[0]["candidate_id"],
                       "reason_codes": reason,
                       "short_rationale": "stub: start one available candidate",
                       "deferred_candidate_ids": []}
        return ag1.CognitiveDecisionResponse(
            model_run_ref="model_run:s04-decision-review:%d" % self.calls,
            model_id=self.model_id, model_version=self.model_version,
            raw_output=raw, latency_ms=1)


def _envelope(out: dict) -> dict:
    return {key: out.get(key) for key in (
        "outcome", "decision", "decision_route", "adoption_status", "decision_id",
        "selected_candidate_ref", "activity_ref", "action_id", "detail", "error")}


def _counts(state_root: Path) -> dict:
    """只读计数：决定与采纳。Activity/Action 用 runtime 自己的读口另算。"""

    readers = dr.open_readers(state_root)
    dec_state = readers["decision_read"].store.load_state() or {}
    int_state = readers["integration_read"].read_state() or {}
    return {
        "decisions": len(dec_state.get("decisions_by_id") or {}),
        "adoptions": len(int_state.get("adoptions_by_id") or {}),
    }


def _activity_counts(runtime) -> dict:
    state, _journal = runtime.activity_read.read_canonical_state(include_journal=False)
    return {
        "activities": len((state or {}).get("activities") or {}),
        "actions": len(runtime.action_read.list_actions() or []),
    }


def main() -> int:
    stamp = "%s-%d" % (time.strftime("%Y%m%d-%H%M%S"), os.getpid())
    run_root = SCRATCH / stamp
    run_root.mkdir(parents=True, exist_ok=True)
    saved_env = {GATE_ENV: os.environ.get(GATE_ENV)}
    services = Services(run_root / "services")
    gateway = None
    runtime = None
    summary: dict = {
        "root": str(run_root),
        "checks": {},
        "limitations": [
            "Decision and content are scripted doubles; no product model was called",
            "the Supply owner, AG-1, AG-2 and LR-2 are real code over real files/sockets",
            "the World gateway is real; the world package is an operator fixture; the gate is off",
            "no natural model adoption: this is not G1, not a 24h run",
            "rationale text is withheld by the phone/CLI default; only a local flag shows it",
        ],
    }
    try:
        instance = run_root / "instance"
        prepared = odl.prepare(
            instance=instance, supply_socket=services.supply_path,
            governance_socket=services.gov_path, subject="test:s04-decision-review",
            title="S04 decision review demo",
            seed_content="验收夹：这一句只用于准备一个真实作品。\n",
            reading_title="验收夹阅读材料",
            reading_content="验收夹：这一页只用于准备一个真实阅读材料。\n",
            world_package=WORLD_PACKAGE)
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
        client = WorldBodyClient(environment=env)
        clock = Clock(episode_instant(after=datetime.now(timezone.utc)))
        provider = wmos.WorldMoveOpportunityProvider(
            client=client, action_intent=action_intent, bridge_dir=str(BRIDGE_DIR),
            environment=env, world_home=str(world_home),
            now=lambda: clock.now, observed_now=lambda: clock.now, valid_for_seconds=60)
        cognition = _ScriptedDecision()
        port = lap.LifeArtifactPort(str(services.supply_path), data["subject_id"])
        adapter = maaa.SupplyManagedArtifactActionAdapter(
            port, grant_id=data["artifact_grant_id"], workspace_id=data["workspace_id"])
        switches = ag2.LifeRuntimeKillSwitches(
            life_runtime_enabled=True, agency_enabled=True,
            action_execution_enabled=True, proactive_enabled=False)
        runtime = lrp.build_production_life_runtime(
            state_root, kill_switches=switches, cognition_adapter=cognition,
            install_root=instance / "life", artifact_action_adapter=adapter,
            world_action_adapter=RealJourneyWorldActionAdapter(
                JourneyWorldResolver(environment=env)))
        assembled = assembly.assemble_autonomous_life(
            runtime, environment=env, state_root=state_root,
            world_provider=provider, writer=StubWriter(), enabled=True)
        _check(assembled.get("state") == assembly.READY, "assembly not READY", assembled)
        cycle = assembled["cycle"]
        cycle.world_home = str(world_home)
        cycle.work_artifact_id = str(data.get("work_artifact_id") or "")
        cycle.reading_artifact_id = str(data.get("reading_artifact_id") or "")

        readers = dr.open_readers(state_root)
        decision_read = readers["decision_read"]
        integration_read = readers["integration_read"]
        validator = runtime.coordinator.adoption_coordinator.validate_decision_for_adoption
        baseline = dict(_counts(state_root))
        baseline.update(_activity_counts(runtime))

        # -----------------------------------------------------------------------------
        # 1) 正控 + 未采用：AG-1 单独提交一条 DEFER，不进入 AG-2
        # -----------------------------------------------------------------------------
        cset = runtime.coordinator.candidate_materializer.build_candidate_set(
            observed_at=clock.iso())
        _check(bool(cset.candidate_refs), "the CandidateSet was empty", cset.to_dict())
        opp = ag1.DecisionOpportunity.create(
            trigger_kind="EXPLICIT_REEVALUATION", trigger_ref="evt:s04:control",
            candidate_set=cset, current_activity_ref=None, observed_at=clock.iso(),
            causal_parent_refs=["evt:s04:control", cset.candidate_set_id])
        dec_res = runtime.coordinator.decision_service.evaluate_opportunity(
            opportunity=opp, candidate_set=cset,
            life_frame=runtime.interruption_coordinator.get_current_life_view())
        control_dec = dec_res.get("decision_record")
        _check(control_dec is not None, "the control Decision was not committed", dec_res)
        control_id = control_dec.decision_id

        summary_control = dr.summarize_decision(decision_read, integration_read, control_id)
        _check(summary_control.get("status") == "OK", "control summary not OK", summary_control)
        _check(summary_control.get("decision") == "DEFER",
               "control Decision is not DEFER", summary_control)
        _check(summary_control.get("decision_state") == dr.STATE_DECIDED_NOT_ADOPTED,
               "control Decision is not reported as decided-but-not-adopted", summary_control)
        _check((summary_control.get("adoption") or {}).get("status") == "NOT_ADOPTED",
               "control Adoption is not NOT_ADOPTED", summary_control.get("adoption"))
        _check(summary_control.get("alternatives_recorded"),
               "the control summary lost the recorded alternatives", summary_control)

        live_control = dr.review_live_applicability(
            decision_read, control_id, validator=validator, check_at=clock.iso())
        _check(live_control.get("status") == dr.REVIEW_CURRENT_VALID,
               "positive control did not pass the existing admission check", live_control)

        control_snapshot = dict(_counts(state_root))
        control_snapshot.update(_activity_counts(runtime))

        # -----------------------------------------------------------------------------
        # 2) 主链：正式 evaluate_once 提交决定并由真实 AG-2 采纳
        # -----------------------------------------------------------------------------
        cognition.policy = "start_any"
        first = cycle.evaluate_once(trigger_ref="evt:s04:main", observed_at=clock.iso(),
                                    act=True)
        _check(first.get("decision") == "START",
               "the main round did not START a candidate",
               {"envelope": _envelope(first), "requests": cognition.summaries})
        _check(first.get("adoption_status") == "APPLIED",
               "the main round was not adopted by AG-2", _envelope(first))
        main_id = str(first.get("decision_id") or "")
        _check(bool(main_id), "the main round reported no Decision id", _envelope(first))

        main_summary = dr.summarize_decision(decision_read, integration_read, main_id)
        _check(main_summary.get("status") == "OK", "main summary not OK", main_summary)
        _check(main_summary.get("decision_state") == dr.STATE_DECIDED_AND_ADOPTED,
               "main Decision is not reported as decided-and-adopted", main_summary)
        _check((main_summary.get("adoption") or {}).get("status") == "APPLIED",
               "main Adoption is not APPLIED", main_summary.get("adoption"))
        selected = main_summary.get("selected") or {}
        _check(selected.get("candidate_ref") == first.get("selected_candidate_ref"),
               "the summary's selected candidate is not the committed one",
               {"summary": main_summary.get("selected"), "round": _envelope(first)})
        _check(selected.get("reason_codes"),
               "the selected candidate lost its recorded reason codes", selected)
        # 摘要里记录里没有的字段必须是 NOT_RECORDED，而不是空字符串。
        for row in (main_summary.get("alternatives") or []):
            for field in ("candidate_kind", "source_kind", "source_ref", "disposition"):
                _check(bool(row.get(field)),
                       "an alternative row lost %s" % field, row)
        # rationale 默认扣留：正文不进入读面。
        rationale = main_summary.get("rationale") or {}
        _check("text" not in rationale,
               "the default summary leaked the rationale text", rationale)
        summary["main"] = {
            "decision_id": main_id,
            "decision": main_summary.get("decision"),
            "selected": selected.get("candidate_ref"),
            "reasons": selected.get("reason_codes"),
            "alternatives": len(main_summary.get("alternatives") or []),
            "deferred": len(main_summary.get("deferred") or []),
            "hard_rejected": len(main_summary.get("hard_rejected") or []),
            "adoption_status": (main_summary.get("adoption") or {}).get("status"),
            "rationale_withheld": True,
        }

        # -----------------------------------------------------------------------------
        # 3) 旧决定复核：Activity 已变化后，用正式准入读口得到具名拒绝
        # -----------------------------------------------------------------------------
        before_review = dict(_counts(state_root))
        before_review.update(_activity_counts(runtime))
        live_main = dr.review_live_applicability(
            decision_read, main_id, validator=validator, check_at=clock.iso())
        _check(live_main.get("status") == dr.REVIEW_REJECTED,
               "the old Decision was silently considered still applicable", live_main)
        _check(bool(live_main.get("reason_code")),
               "the rejection has no named reason", live_main)
        after_review = dict(_counts(state_root))
        after_review.update(_activity_counts(runtime))
        _check(before_review == after_review,
               "the review wrote something", {"before": before_review, "after": after_review})
        summary["old_decision_review"] = {
            "status": live_main.get("status"),
            "reason_code": live_main.get("reason_code"),
            "zero_side_effect": True,
        }

        # -----------------------------------------------------------------------------
        # 4) 重装配：同一 state root 的只读端口读回同一条 Decision/Adoption
        # -----------------------------------------------------------------------------
        reread = dr.open_readers(state_root)
        again = dr.summarize_decision(reread["decision_read"], reread["integration_read"], main_id)
        _check(again.get("decision_id") == main_id and
               again.get("decision_state") == dr.STATE_DECIDED_AND_ADOPTED,
               "a fresh reader did not see the same Decision/Adoption", again)
        control_again = dr.summarize_decision(
            reread["decision_read"], reread["integration_read"], control_id)
        _check(control_again.get("decision_state") == dr.STATE_DECIDED_NOT_ADOPTED,
               "the un-adopted Decision was auto-adopted after re-assembly", control_again)
        final_counts = dict(_counts(state_root))
        final_counts.update(_activity_counts(runtime))
        _check(final_counts == after_review,
               "the re-read/re-assembly produced a new record",
               {"after_review": after_review, "final": final_counts})
        summary["reassembly"] = {
            "same_decision": True,
            "same_adoption": True,
            "unadopted_control_still_history": True,
            "counts": final_counts,
        }

        summary["budget"] = {"product_model_calls": 0, "network": False}
        summary["checks"] = {
            "control_decided_not_adopted": True,
            "control_live_valid": True,
            "main_decided_and_adopted": True,
            "alternatives_from_persisted_dispositions": True,
            "rationale_withheld_by_default": True,
            "old_decision_review_rejected": True,
            "review_zero_side_effect": True,
            "reassembly_no_replay": True,
        }
        print("DEMO_OK")
        print(json.dumps(summary, ensure_ascii=False, indent=1))
        return 0
    finally:
        for name, value in saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        try:
            if gateway is not None:
                gateway.stop()
        except Exception:
            pass
        try:
            services.stop()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())

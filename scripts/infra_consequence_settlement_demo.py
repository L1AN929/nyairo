#!/usr/bin/env python3
"""INFRA F155-160 demo：一次真实动作之后，三轴与分域后果各自是什么。

全部在本机隔离夹具内，0 产品模型、0 外网、0 旧实例：

* 真实 Life Supply owner（自己的 Unix socket）、真实实例、真实 ProductionLifeRuntime、
  真实 AG-0/AG-1/AG-2、LR-2/LR-5、AR-0/AR-1、真实 M0 Memory；
* Decision 回答与内容回答是脚本替身，明示；不宣称真实模型自然采用。

演示的六件事
------------
1. 正式 ``evaluate_once`` START → 真实作品版本 → Action SUCCEEDED → AR-1
   ``SETTLED/SUCCESS_EFFECT``；Activity 仍是 ACTIVE。三轴分开显示，文件已保存
   不等于生活结束。
2. 另一次正式 CONTINUE → 第二个 owner 结果：新的 Action、新的 settlement、新的
   ``lexp:result:`` 交接；不会被同一 Activity/root 吞掉。
3. 第三次写入真实成功，但 Memory 经历交接受控故障：版本与结算照旧成功，
   交接 ``FAILED_PENDING_RETRY``、Memory 读面不可召回；复核明确写
   “不是所有记录域都已处理”，envelope 仍 NOT_IMPLEMENTED。
4. 同根有序停启（runtime 关闭 → 同根重装配）后：动作/结算/交接/Memory 都还在，
   成功的 Action 与版本没有被重新执行，未结交接仍如实挂起。
5. 恢复后走既有 ``dispatch_experience_handoffs`` 重投原 handoff：同一身份、一次
   新记录；再投一次是 M0 的存储幂等 ``DUPLICATE``，不是语义 NO_CHANGE。
6. AR-1 层同源事实重投：``settle_action`` 幂等 replay，不新增 settlement/事件；
   同一 outbox event 重投给 LR-5 不新增 checkpoint。

命名缺口（不造假绿）：设计 VIII.7 的 ConsequenceEnvelope / obligation compiler /
target-owner receipt / NOT_APPLICABLE / owner NO_CHANGE / depth-capacity 在本仓库
不存在，复核输出 ``NOT_IMPLEMENTED``；AR-1 只是通知扇出。

Run (WSL/POSIX)::

    PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B \
        scripts/infra_consequence_settlement_demo.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "tests"), str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import infra_settlement_review as review  # noqa: E402
import life_integration_ag2 as ag2  # noqa: E402
import life_memory_service as lms  # noqa: E402
import life_memory_review as memreview  # noqa: E402
import life_runtime_production as lrp  # noqa: E402
import one_day_life as odl  # noqa: E402
import result_method_behavior_smoke as base  # noqa: E402

BASE_TIME = datetime.now().astimezone()
_ROUND = [0]
FAILURES: list[str] = []


def say(text: str) -> None:
    print(text)
    sys.stdout.flush()


def check(name: str, condition: Any, detail: Any = None) -> bool:
    ok = bool(condition)
    say("CHECK %-76s %s" % (name, "PASS" if ok else "FAIL"))
    if not ok:
        FAILURES.append(name)
        if detail is not None:
            say("  detail: " + json.dumps(detail, ensure_ascii=False, default=str)[:1800])
    return ok


def next_instant() -> str:
    _ROUND[0] += 1
    return (BASE_TIME + timedelta(minutes=400 * _ROUND[0])).isoformat()


# ---------------------------------------------------------------------------------------------
# formal evaluate_once helpers (real AG-1/AG-2, scripted decision/content)
# ---------------------------------------------------------------------------------------------


def candidate_refs(chain, observed_at: str) -> list[str]:
    return [str(ref) for ref in chain.runtime.candidate_materializer
            .build_candidate_set(observed_at=observed_at).candidate_refs]


def candidate_payload(chain, ref: str) -> dict[str, Any]:
    return dict(chain.runtime.candidate_read.get_candidate(str(ref)) or {})


def find_work_candidate(chain, artifact_id: str, observed_at: str) -> Optional[str]:
    want = str(artifact_id).split(":")[-1]

    def matches(payload: Mapping[str, Any]) -> bool:
        refs = [str(value) for value in (payload.get("target_refs") or [])]
        return (any(want in value for value in refs)
                and not any(value.startswith("closeout:") for value in refs))

    for ref in candidate_refs(chain, observed_at):
        if matches(candidate_payload(chain, ref)):
            return ref
    return None


def find_current_candidate(chain, artifact_id: str, observed_at: str) -> Optional[str]:
    rows = []
    for ref in candidate_refs(chain, observed_at):
        payload = candidate_payload(chain, ref)
        if str(payload.get("candidate_kind") or "") == "CONTINUE_CURRENT":
            rows.append((ref, payload))
    if not rows:
        return None
    wanted = str(chain.port.resume_point(str(artifact_id)).head_version_id or "").split(":")[-1]
    if wanted:
        for ref, payload in rows:
            if any(wanted in str(value) for value in (payload.get("target_refs") or [])):
                return ref
    return rows[-1][0]


def choose(chain, decision: str, candidate_id: Optional[str]) -> None:
    chain.cognition.script = {"decision": decision, "selected_candidate_id": candidate_id,
                              "deferred_candidate_ids": []}


def round_out(chain, tag: str, observed_at: str) -> dict[str, Any]:
    return dict(chain.cycle.evaluate_once(
        trigger_ref="evt:infra:%s" % tag, observed_at=observed_at, act=True) or {})


def version_count(chain, artifact_id: Any) -> int:
    return int(chain.port.list_versions(str(artifact_id), limit=50)["total_versions"])


def action_row(chain, action_id: str) -> dict[str, Any]:
    return dict(chain.runtime.action_read.get_action(str(action_id)) or {})


def lineage(chain, action_id: str) -> dict[str, Any]:
    return dict(chain.runtime.settlement_read.trace_result_lineage(str(action_id)) or {})


def settlement_of(chain, action_id: str) -> dict[str, Any]:
    return dict(chain.runtime.settlement_read.get_settlement(str(action_id)) or {})


# ---------------------------------------------------------------------------------------------
# controlled Memory fault: the existing consumer, one failure injected at its own boundary
# ---------------------------------------------------------------------------------------------


class FlakyExperienceConsumer:
    """The real Memory consumer with a controlled, switchable failure.

    It is not a second owner and it never writes: when ``down`` it raises before calling the
    real consumer, which is exactly the AG-2 contract (delivery FAILED_PENDING_RETRY, the
    handoff stays pending, the work is not rolled back).  ``down=False`` delegates unchanged.
    """

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.down = False
        self.calls = 0

    def receive_experience_handoff(self, event: Mapping[str, Any]) -> dict[str, Any]:
        self.calls += 1
        if self.down:
            raise RuntimeError("CONTROLLED_MEMORY_UNAVAILABLE")
        return self.inner.receive_experience_handoff(event)


# ---------------------------------------------------------------------------------------------
# the real user entry under test
# ---------------------------------------------------------------------------------------------


def run_cli(instance: Path, *extra: str) -> tuple[int, dict[str, Any]]:
    command = [sys.executable, "-B", str(ROOT / "scripts" / "infra_settlement_review_cli.py"),
               "--instance", str(instance), "--format", "json", *extra]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=180)
    try:
        payload = json.loads(completed.stdout or "{}")
    except ValueError:
        payload = {"ok": False, "reason": "CLI_OUTPUT_NOT_JSON",
                   "stdout": completed.stdout[-600:], "stderr": completed.stderr[-600:]}
    return int(completed.returncode), payload


def memory_records(memory_service: Any, handoff_id: str) -> dict[str, Any]:
    return memreview.find_memory_records(memory_service, str(handoff_id), limit=10)


def handoff_id_of(chain, action_id: str) -> Optional[str]:
    state = dict(chain.runtime.integration_read.read_state() or {})
    for hid, event in (state.get("handoff_events_by_id") or {}).items():
        if action_id in [str(ref) for ref in (dict(event).get("action_refs") or [])]:
            return str(hid)
    return None


def review_one(chain, action_id: str, memory_service: Any) -> dict[str, Any]:
    return review.review_action(action_id=str(action_id),
                                readers=review.open_readers(chain.runtime.state_root),
                                memory_service=memory_service)


def domain_of(row: Mapping[str, Any], domain: str) -> dict[str, Any]:
    for item in row.get("domains") or []:
        if item.get("domain") == domain:
            return dict(item)
    return {}


def main() -> int:
    # AF_UNIX paths are capped (~108 chars); keep the isolated fixture root short.
    base.FIXTURE_ROOT = Path("/tmp/nyf-infra")
    chain = base.build_chain("consequence-settlement")
    instance = chain.root / "instance"
    work = str(chain.data["artifact_id"])
    # The isolated fixture's control ledger has to exist before a read-only projection can be
    # READY (the same setup step ``s05_memory_reliability_demo`` performs).  This is fixture
    # setup, not a product write.
    from memory_control import MemoryControlLedger
    from life_experience_port import life_conversation_id

    MemoryControlLedger(str(chain.controls_db), str(chain.evidence_db), owner="chiyo",
                        conversation=life_conversation_id("chiyo")).initialize()
    memory_service = lms.LifeMemoryService(
        chain.memory_port, owner="chiyo", database=str(chain.evidence_db),
        controls_database=str(chain.controls_db), source="demo")
    record: dict[str, Any] = {"root": str(chain.root), "work": work, "rounds": {}}

    try:
        # ================================================================================
        # 1. formal START: one real owner result, three axes apart
        # ================================================================================
        say("\n== 1. 正式 START：真实作品结果 → Action/结算/活动 三轴分开 ==")
        ts = next_instant()
        choose(chain, "START", find_work_candidate(chain, work, ts))
        r1 = round_out(chain, "start", ts)
        record["rounds"]["start"] = {k: r1.get(k) for k in
                                     ("outcome", "action_id", "activity_ref")}
        a1 = str(r1.get("action_id") or "")
        s1 = settlement_of(chain, a1)
        versions_after_1 = version_count(chain, work)
        check("真实 START 写出作品版本，Action SUCCEEDED",
              r1.get("outcome") == "ACTED" and action_row(chain, a1).get("status") == "SUCCEEDED"
              and versions_after_1 >= 2,
              {"round": record["rounds"]["start"], "versions": versions_after_1})
        check("AR-1 对同一动作给出 SETTLED/SUCCESS_EFFECT",
              s1.get("settlement_status") == "SETTLED"
              and s1.get("outcome_kind") == "SUCCESS_EFFECT",
              s1)
        review1 = review_one(chain, a1, memory_service)
        axes = review1.get("axes") or {}
        action_axis = axes.get(review.AXIS_ACTION) or {}
        activity_axis = axes.get(review.AXIS_ACTIVITY) or {}
        envelope_axis = axes.get(review.AXIS_ENVELOPE) or {}
        record["review_start"] = review1
        check("三轴分开：动作已结算，Activity 仍 ACTIVE，envelope 未实现",
              (action_axis.get("settlement") or {}).get("closed") is True
              and activity_axis.get("status") in {"ACTIVE", "PAUSED", "WAITING"}
              and activity_axis.get("closed") is False
              and envelope_axis.get("status") == review.NOT_IMPLEMENTED,
              {"action": action_axis.get("settlement"), "activity": activity_axis,
               "envelope": envelope_axis.get("status")})
        check("效果声明来自 owner 观测（artifact_written_verified / OBSERVER_VERIFIED）",
              any(claim.get("predicate") == "artifact_written_verified"
                  and claim.get("authority_grade") == "OBSERVER_VERIFIED"
                  for claim in (action_axis.get("settlement") or {}).get("effect_claims") or []),
              (action_axis.get("settlement") or {}).get("effect_claims"))
        life_row = domain_of(review1, "life")
        memory_row = domain_of(review1, "memory")
        h1 = handoff_id_of(chain, a1)
        row1_memory = memory_records(memory_service, h1) if h1 else {}
        check("分域：LIFE 由 LR-5 自己的读面核实，Memory 通知不是 owner receipt",
              life_row.get("status") == review.DELIVERY_ACKNOWLEDGED
              and (life_row.get("owner_read") or {}).get("status") == "VERIFIED_BY_OWNER_READ"
              and memory_row.get("receipt_kind") == "AR1_CONSUMER_ACK"
              and memory_row.get("owner_receipt_verified") is False,
              {"life": life_row, "memory": memory_row})
        check("Memory 的真实后果是 AG-2 经历交接，M0 里可召回",
              h1 and str(h1).startswith("lexp:result:")
              and row1_memory.get("status") == memreview.RECALL_AVAILABLE,
              {"handoff": h1, "memory": row1_memory})
        check("输出不含作品正文",
              "# Result method fixture" not in json.dumps(review1, ensure_ascii=False),
              None)

        # ================================================================================
        # 2. a second owner result is not swallowed by the same Activity/root
        # ================================================================================
        say("\n== 2. 第二次正式 CONTINUE：第二个 owner 结果独立结算与交接 ==")
        ts = next_instant()
        chain.client.payload = {"kind": "append_paragraph", "text": "第二段：继续写下去。"}
        choose(chain, "CONTINUE", find_current_candidate(chain, work, ts))
        r2 = round_out(chain, "continue", ts)
        a2 = str(r2.get("action_id") or "")
        s2 = settlement_of(chain, a2)
        h2 = handoff_id_of(chain, a2)
        row2_memory = memory_records(memory_service, h2) if h2 else {}
        record["rounds"]["continue"] = {"outcome": r2.get("outcome"), "action_id": a2,
                                        "settlement": s2.get("settlement_id"),
                                        "handoff": h2}
        check("第二次结果有新的 Action、新的 settlement 与新的 lexp:result 交接",
              bool(a2) and a2 != a1 and bool(s2.get("settlement_id"))
              and s2.get("settlement_id") != s1.get("settlement_id")
              and bool(h2) and h2 != h1 and str(h2).startswith("lexp:result:"),
              {"a1": a1, "a2": a2, "s1": s1.get("settlement_id"),
               "s2": s2.get("settlement_id"), "h1": h1, "h2": h2})
        check("两个真实结果在 M0 各有一条记录（不被 root 去重吞掉）",
              row2_memory.get("status") == memreview.RECALL_AVAILABLE
              and len(row2_memory.get("records") or []) == 1,
              {"h2": h2, "memory": row2_memory})

        # ================================================================================
        # 3. real work saved, Memory handoff fails under control
        # ================================================================================
        say("\n== 3. 第三次写入成功，但 Memory 经历交接受控故障 ==")
        ts = next_instant()
        flaky = FlakyExperienceConsumer(chain.runtime.coordinator.experience_consumer)
        chain.runtime.coordinator.experience_consumer = flaky
        chain.runtime.experience_consumer = flaky
        flaky.down = True
        chain.client.payload = {"kind": "append_paragraph", "text": "第三段：她还在写。"}
        choose(chain, "CONTINUE", find_current_candidate(chain, work, ts))
        r3 = round_out(chain, "memory-fault", ts)
        a3 = str(r3.get("action_id") or "")
        s3 = settlement_of(chain, a3)
        h3 = handoff_id_of(chain, a3)
        versions_after_3 = version_count(chain, work)
        state3 = dict(chain.runtime.integration_read.read_state() or {})
        delivery3 = dict((state3.get("handoff_deliveries_by_id") or {}).get(str(h3)) or {})
        pending3 = list(state3.get("pending_handoff_ids") or [])
        record["rounds"]["memory_fault"] = {
            "action": a3, "settlement": s3.get("settlement_id"), "handoff": h3,
            "delivery": delivery3, "pending": pending3, "versions": versions_after_3}
        check("真实写入仍然成功，AR-1 结算仍是 SETTLED",
              r3.get("outcome") == "ACTED" and action_row(chain, a3).get("status") == "SUCCEEDED"
              and s3.get("settlement_status") == "SETTLED"
              and versions_after_3 == versions_after_1 + 2,
              {"round": r3.get("outcome"), "settlement": s3.get("settlement_status"),
               "versions": versions_after_3})
        check("Memory 交接明确失败 / 挂起，未被改写成成功",
              delivery3.get("status") == "FAILED_PENDING_RETRY"
              and str(h3) in pending3
              and memory_records(memory_service, h3).get("status")
              == memreview.RECALL_NOT_RECALLABLE,
              {"delivery": delivery3, "pending": pending3})
        review3 = review_one(chain, a3, memory_service)
        handoff3 = next((row for row in review3.get("handoffs") or []
                         if row.get("handoff_id") == h3), {})
        check("复核不报整链结清：记录域未全处理，envelope 仍不可宣称关闭",
              review3.get("closure", {}).get("consequence_domains_processed") is False
              and review3.get("closure", {}).get("envelope_closure_declarable") is False
              and (review3.get("axes") or {}).get(review.AXIS_ENVELOPE, {}).get("status")
              == review.NOT_IMPLEMENTED
              and (handoff3.get("memory_record") or {}).get("status")
              == memreview.RECALL_NOT_RECALLABLE,
              review3.get("closure"))
        code, cli3 = run_cli(instance, "--action-id", a3,
                             "--memory-db", str(chain.evidence_db),
                             "--controls-db", str(chain.controls_db),
                             "--owner", "chiyo")
        cli_closure = (cli3 or {}).get("closure") or {}
        check("正式 CLI（用户入口）如实显示受控故障",
              code == 0 and cli3.get("ok") is True
              and cli_closure.get("consequence_domains_processed") is False
              and cli_closure.get("envelope_closure_declarable") is False,
              {"code": code, "closure": cli_closure,
               "reason": (cli3 or {}).get("reason")})

        # ================================================================================
        # 4. ordered same-root restart keeps the open consequence
        # ================================================================================
        say("\n== 4. 同根有序停启：状态不丢、成功动作不重放 ==")
        attempts_before = {a: int(action_row(chain, a).get("attempt_count") or 0)
                           for a in (a1, a2, a3)}
        settlements_before = [settlement_of(chain, a).get("settlement_id")
                              for a in (a1, a2, a3)]
        chain.runtime.close()
        runtime2 = lrp.build_production_life_runtime(
            odl.life_state_root(instance),
            kill_switches=ag2.LifeRuntimeKillSwitches(
                life_runtime_enabled=True, agency_enabled=True,
                action_execution_enabled=True, proactive_enabled=False),
            cognition_adapter=chain.cognition,
            install_root=instance / "life",
            artifact_action_adapter=chain.runtime.artifact_action_adapter,
            experience_consumer=chain.runtime.experience_consumer.inner
            if isinstance(chain.runtime.experience_consumer, FlakyExperienceConsumer)
            else chain.runtime.experience_consumer,
        )
        chain.runtime = runtime2
        state4 = dict(runtime2.integration_read.read_state() or {})
        delivery4 = dict((state4.get("handoff_deliveries_by_id") or {}).get(str(h3)) or {})
        check("重启后动作/结算/版本/attempt 都没有被重新执行",
              version_count(chain, work) == versions_after_3
              and all(int(action_row(chain, a).get("attempt_count") or 0) == attempts_before[a]
                      for a in (a1, a2, a3))
              and [settlement_of(chain, a).get("settlement_id")
                   for a in (a1, a2, a3)] == settlements_before,
              {"versions": version_count(chain, work), "attempts": attempts_before})
        check("持久未结如实保留：交接仍是 FAILED_PENDING_RETRY 且仍在 pending",
              delivery4.get("status") == "FAILED_PENDING_RETRY"
              and str(h3) in (state4.get("pending_handoff_ids") or []),
              {"delivery": delivery4, "pending": state4.get("pending_handoff_ids")})

        # ================================================================================
        # 5. existing retry reuses the original identity; second delivery is DUPLICATE
        # ================================================================================
        say("\n== 5. 恢复后走既有重投：同一 handoff 身份，一次新记录 ==")
        versions_before_retry = version_count(chain, work)
        retry = dict(runtime2.coordinator.dispatch_experience_handoffs(handoff_id=str(h3)) or {})
        state5 = dict(runtime2.integration_read.read_state() or {})
        delivery5 = dict((state5.get("handoff_deliveries_by_id") or {}).get(str(h3)) or {})
        row5_memory = memory_records(memory_service, h3)
        record["retry"] = {"delivery": delivery5, "memory": row5_memory.get("status")}
        check("重投复用原 handoff 身份，M0 出现一条记录，写入效果不重放",
              delivery5.get("status") == "DELIVERED"
              and delivery5.get("consumer_disposition") == "ACCEPT"
              and str(h3) not in (state5.get("pending_handoff_ids") or [])
              and row5_memory.get("status") == memreview.RECALL_AVAILABLE
              and len(row5_memory.get("records") or []) == 1
              and version_count(chain, work) == versions_before_retry,
              {"delivery": delivery5, "memory": row5_memory})
        again = dict(runtime2.coordinator.dispatch_experience_handoffs(handoff_id=str(h3)) or {})
        state6 = dict(runtime2.integration_read.read_state() or {})
        delivery6 = dict((state6.get("handoff_deliveries_by_id") or {}).get(str(h3)) or {})
        row6_memory = memory_records(memory_service, h3)
        check("同一交接再投一次是 M0 存储幂等 DUPLICATE（不是语义 NO_CHANGE）",
              delivery6.get("consumer_disposition") == "DUPLICATE"
              and len(row6_memory.get("records") or []) == 1,
              {"disposition": delivery6.get("consumer_disposition"),
               "records": len(row6_memory.get("records") or [])})

        # ================================================================================
        # 6. AR-1 same-source replay: idempotent settlement, no duplicate domain effect
        # ================================================================================
        say("\n== 6. AR-1 同源重投：结算幂等、同一事件重投不新增 checkpoint ==")
        history_before = list((lineage(chain, a1).get("settlement_history") or []))
        events_before = list((lineage(chain, a1).get("settled_result_events") or []))
        replay = dict(runtime2.settlement_service.settle_action(action_id=a1) or {})
        history_after = list((lineage(chain, a1).get("settlement_history") or []))
        events_after = list((lineage(chain, a1).get("settled_result_events") or []))
        check("settle_action 同源重投是 idempotent replay，不新增结算/事件",
              replay.get("idempotent_replay") is True
              and len(history_after) == len(history_before)
              and len(events_after) == len(events_before),
              {"replay": replay.get("idempotent_replay"), "history": len(history_after),
               "events": len(events_after)})
        event1 = str((events_before[-1] or {}).get("event_id") or "")
        progress = runtime2.progress_read
        checkpoints_before = list(progress.list_checkpoint_updates(str(r1.get("activity_ref"))) or [])
        redispatch = dict(runtime2.settlement_service.dispatch_outbox(event_id=event1) or {})
        checkpoints_after = list(progress.list_checkpoint_updates(str(r1.get("activity_ref"))) or [])
        check("同一 outbox event 再投给 LR-5 不新增 checkpoint（效果不重放）",
              bool(event1)
              and len(checkpoints_after) == len(checkpoints_before)
              and any(row.get("consumer_domain") == "life"
                      for row in (redispatch.get("deliveries") or [])),
              {"event": event1, "before": len(checkpoints_before),
               "after": len(checkpoints_after),
               "deliveries": redispatch.get("deliveries")})

        # ================================================================================
        # 7. final review through the real CLI
        # ================================================================================
        say("\n== 7. 最终复核：三轴 + 分域，全部可读 ==")
        code, final = run_cli(instance, "--action-id", a3,
                              "--memory-db", str(chain.evidence_db),
                              "--controls-db", str(chain.controls_db),
                              "--owner", "chiyo")
        final_closure = (final.get("closure") or {})
        final_handoff = next((row for row in final.get("handoffs") or []
                              if row.get("handoff_id") == h3), {})
        check("最终 CLI：交接已处理，envelope 仍 NOT_IMPLEMENTED（不冒整协议完成）",
              code == 0
              and final_closure.get("consequence_domains_processed") is True
              and final_closure.get("envelope_closure_declarable") is False
              and ((final.get("axes") or {}).get(review.AXIS_ENVELOPE) or {}).get("status")
              == review.NOT_IMPLEMENTED
              and (final_handoff.get("memory_record") or {}).get("status")
              == memreview.RECALL_AVAILABLE,
              {"closure": final_closure, "handoff": final_handoff.get("memory_record")})
        code, listing = run_cli(instance, "--limit", "5")
        check("列表模式可用且不带正文",
              code == 0 and listing.get("count", 0) >= 3
              and "# Result method fixture" not in json.dumps(listing, ensure_ascii=False),
              {"code": code, "count": listing.get("count")})

    finally:
        try:
            base.close_chain(chain)
        except Exception as exc:  # 关闭失败要打印，不隐藏
            say("close error: %s: %s" % (type(exc).__name__, exc))

    say("\n== summary ==")
    say("failures: %d" % len(FAILURES))
    if FAILURES:
        for name in FAILURES:
            say("  FAILED: %s" % name)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

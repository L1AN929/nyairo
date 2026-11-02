#!/usr/bin/env python3
"""G1 同一作品：中断 → 重启 → UNKNOWN 对账 → CAS 冲突 → NO_ACTION → 继续。

这是一个**可重复的操作示例**，不是测试矩阵：每一步都接上一步真实落盘的状态，
同一个隔离根、同一件作品 A（另建作品 B 只用来证明不串目标）。

全部在本机隔离夹具内，0 产品模型、0 外网、0 旧实例、0 外发：

1. 父调度器只建一个隔离根（世界包 + ``odl.prepare`` + 空 M0 Memory），不持有 runtime。
2. **P1**（PID 1）：真实 ``ProductionLifeRuntime`` + 正式 ``evaluate_once``，经真实 Supply
   owner 写出作品 A 的第一版并产生正式 Activity/结果；再建作品 B 作对照；随后写入
   operator pause（既有中断接口，不伪造 LR-4 资格）。关闭 runtime（真的释放 canonical
   lease）并停掉自己的 owner 后退出。
3. **P2**（PID 2，不同进程）：同一根上**新** runtime / 新 assembly / 新预算。先证明暂停轮
   纯读零写（0 请求、head/版本/M0 不增），再经正式解除接口恢复，用新 Decision 继续
   **同一个** Activity（不重放旧成功），owner 真出新版本。
4. **UNKNOWN**：一次真实已执行的写入在适配器测试故障注入处丢回执，AR-0 保持 UNKNOWN；
   随后只用 owner 自己的 operation 对账（``resolve_operation``，零裸重发）确认真实同一次
   效果，A 的版本只增加那一次。
5. **CAS 冲突**：一轮正式 ``evaluate_once``（脚本 Decision ``CONTINUE``）读出 A 的当前
   head 并把它冻结进 AR-0 的 payload；AR-0 的 submission fence 落盘后，在 demo 层把这一次
   ``adapter.submit`` 包一层（限定 A、只用一次），先让真实 owner 的 **另一个 writer** 推进
   head；于是正式链带旧 ``expected_head`` 过闸，owner 以 CAS 冲突拒。
   Activity 不假完成、不换目标 B、零假 result；之后新 Decision 读当前 head 再沿现规则续 A。
6. **NO_ACTION**：合法无附带写的一轮，A/B/head/回执都不变；再下一轮仍能继续 A 并产生
   真实新版本。

Decision 与内容回答是脚本替身（文件里明示）；真实件是 Supply/World/Activity/Memory
owner、``ProductionLifeRuntime``、AG-0/AG-1/AG-2/AR-0。不宣称真实模型自然采用、完整
G1、EL4 或 24h；这是正常有序停启 + 有界故障注入，不是 crash 矩阵。

Run (POSIX/WSL)::

    PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B \
        scripts/g1_same_work_recovery_demo.py
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
for entry in (str(ROOT), str(SCRIPT_DIR), str(ROOT / "tests"),
              str(ROOT / "components"), str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

A_V1 = "第一版：先把这一节的开头写下来。"
A_V2 = "第二版：重启之后接着这一节往下写。"
A_V3 = "第三版：这次回执丢了，但 owner 那边真的写下去了。"
A_V4 = "第四版：别人先动过 head，这次按当前 head 继续。"
A_V5 = "第五版：安静的一轮之后仍然可以继续写。"
B_BODY = "另一件作品的正文，只用来验证续写不会串到它。"

PHASE_PREFIX = "G1_PHASE "

_BASE = datetime.now().astimezone()
_INDEX = [0]


def stamp() -> str:
    """每轮一个自己的观察时刻；只推进夹具时钟，不碰静默规则。"""
    _INDEX[0] += 1
    return (_BASE + timedelta(minutes=400 * _INDEX[0])).isoformat(timespec="seconds")


CHECKS: list[tuple[str, bool, object]] = []


def check(name: str, condition, detail=None) -> None:
    CHECKS.append((name, bool(condition), detail))
    print("CHECK %-70s %s" % (name, "PASS" if condition else "FAIL"))
    if not condition and detail is not None:
        print("  detail:", json.dumps(detail, ensure_ascii=False, default=str)[:900])


def _bare(value) -> str:
    """Strip the artifact: prefix (the work chain carries it in more than one place)."""

    text = str(value or "").strip()
    while text.startswith("artifact:"):
        text = text[len("artifact:"):]
    return text


def _activity_view(row: dict) -> dict:
    """An Activity record reduced to what a report may print -- never its title or body."""

    row = dict(row or {})
    if not row:
        return {}
    return {"activity_id": row.get("activity_id"), "status": row.get("status"),
            "revision": row.get("revision"), "decision_ref": row.get("decision_ref"),
            "adoption_ref": row.get("adoption_ref")}


#: What one step row may print.  Outcomes, refusals and owner counters only.
_SAFE_STEP_FIELDS = (
    "outcome", "decision", "decision_id", "activity_ref", "target_ref", "action_id",
    "action_status", "owner_status", "owner_version_created", "owner_version_id",
    "llm_calls", "transport_result", "error_class", "result", "reason_codes", "hook_stage",
)


def _safe_phase_view(report: dict) -> dict:
    """The stdout whitelist for one phase: ids, outcomes, refusals, owner version counts."""

    view = {
        "phase": report.get("phase"), "pid": report.get("pid"), "root": report.get("root"),
        "run_id": report.get("run_id"), "artifact_a": report.get("artifact_a"),
        "artifact_b": report.get("artifact_b"), "activity_ref": report.get("activity_ref"),
        "close": {"closed": report.get("closed"),
                  "service_stopped": report.get("service_stopped"),
                  "drained": report.get("service_drained"),
                  "socket_handling": report.get("service_socket_handling")},
        "steps": {},
        "versions": {},
    }
    for key in ("paused_round", "continue_1", "unknown_round", "reconcile", "cas_conflict",
                "continue_after_cas", "no_action", "continue_after_quiet"):
        row = report.get(key)
        if isinstance(row, dict):
            view["steps"][key] = {name: row.get(name) for name in _SAFE_STEP_FIELDS
                                  if name in row}
            if key == "reconcile":
                view["steps"][key]["evidence_count"] = len(row.get("evidences") or [])
    for name, value in report.items():
        if name.startswith("versions") and isinstance(value, int):
            view["versions"][name] = value
    facts = {}
    for name in ("first_outcome", "first_owner_receipt", "first_owner_version", "v1_head",
                 "a_versions", "b_head", "b_versions", "memory_rows", "memory_rows_at_end",
                 "head_at_start", "versions_at_start", "head_before_exit",
                 "versions_before_exit", "head_at_end", "versions_at_end",
                 "state_at_pause", "state_at_start", "pause_at_start"):
        if name in report:
            value = report.get(name)
            if name == "pause_at_start" and isinstance(value, dict):
                value = {"paused": value.get("paused"), "revision": value.get("revision")}
            facts[name] = value
    view["facts"] = facts
    return view


def _dump_phase(report: dict, root: Path) -> None:
    """Keep the full local report in the isolated root; print only the whitelist."""

    path = Path(root) / ("g1-phase-%s.json" % report.get("phase"))
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8")
    print(PHASE_PREFIX + json.dumps(_safe_phase_view(report), ensure_ascii=False,
                                    default=str))


# ---------------------------------------------------------------------------------------------
# 阶段共用
# ---------------------------------------------------------------------------------------------


def _activity(chain, activity_ref: str) -> dict:
    if not activity_ref:
        return {}
    found = chain.runtime.activity_read.get_activity(activity_ref)
    return dict(found) if found else {}


def _memory_rows(chain) -> int:
    return len(chain.memory_port.recent(limit=50))


def _continue_round(chain, artifact_id: str, text: str, *, trigger: str) -> dict:
    """一轮正式 CONTINUE：候选就是本轮真实请求里指向该作品的唯一 CONTINUE。"""

    import v05_result_sharing_continuation_smoke as sharing

    moment = stamp()
    candidate = sharing.continue_candidate(chain, artifact_id, observed_at=moment)
    if not candidate:
        material = chain.runtime.candidate_materializer.build_candidate_set(observed_at=moment)
        raise SystemExit("no exact CONTINUE candidate for %s: %r"
                         % (artifact_id, material.candidate_refs))
    chain.client.payload = {"kind": "append_paragraph", "text": text,
                            "self_note": "stub: continue the same work"}
    chain.cognition.script = {"decision": "CONTINUE", "selected_candidate_id": candidate,
                              "deferred_candidate_ids": []}
    return chain.cycle.evaluate_once(trigger_ref=trigger, act=True, observed_at=moment)


def _quiet_round(chain, *, trigger: str) -> dict:
    chain.cognition.script = {"decision": "NO_ACTION", "selected_candidate_id": None,
                              "deferred_candidate_ids": []}
    moment = stamp()
    return chain.cycle.evaluate_once(trigger_ref=trigger, act=True, observed_at=moment)


def _select_project_starter(chain, moment: str) -> str:
    """把“开工”候选按身份挑出来，用的就是本轮同一观察时刻的候选集。"""

    options = chain.cycle.project_options()
    wanted = [row for row in options if not row.get("review")]
    if not wanted:
        raise SystemExit("no project option on offer: %r" % (options,))
    option_ref = str(wanted[0]["option_ref"])
    candidates = chain.runtime.candidate_materializer.build_candidate_set(
        observed_at=moment).candidate_refs
    selected = None
    for candidate_ref in candidates:
        payload = chain.runtime.candidate_read.get_candidate(str(candidate_ref)) or {}
        if str(payload.get("source_ref")) == option_ref:
            selected = str(candidate_ref)
            break
    if selected is None:
        raise SystemExit("the project option never became a candidate: %s" % option_ref)
    chain.cognition.script = {"decision": "START", "selected_candidate_id": selected,
                              "deferred_candidate_ids": []}
    return option_ref


# ---------------------------------------------------------------------------------------------
# P1：真实第一版 + Activity/结果 + 中断（operator pause）+ 正规释放
# ---------------------------------------------------------------------------------------------


def phase_p1(config: dict) -> dict:
    import one_day_life as odl
    import result_method_behavior_smoke as harness
    import v05_restart_continuation_smoke as base

    root = Path(config["root"])
    chain = base.open_chain(root, "p1")
    report: dict = {"phase": "p1", "pid": os.getpid(), "root": str(root)}
    artifact_a = ""
    artifact_b = ""
    try:
        instance = root / "instance"
        material_id, revision = harness.import_material(chain)
        harness.write_question(chain, material_id, revision)
        chain.client.payload = {"kind": "append_paragraph", "text": A_V1,
                                "self_note": "stub: write the first version"}
        moment = stamp()
        _select_project_starter(chain, moment)
        out = chain.cycle.evaluate_once(trigger_ref="g1:p1:first-write", act=True,
                                        observed_at=moment)
        receipt = out.get("step_receipt") or {}
        artifact_a = str(receipt.get("artifact_id")
                         or (out.get("project_created") or {}).get("artifact_id") or "")
        activity_ref = str(receipt.get("activity_ref") or out.get("activity_ref") or "")
        if not artifact_a:
            raise SystemExit("the first real write created no work artifact (outcome=%s "
                             "decision=%s)" % (out.get("outcome"), out.get("decision")))
        chain.cycle.work_artifact_id = artifact_a
        report.update({
            "artifact_a": artifact_a,
            "run_id": chain.env.get(odl.RUN_ID_ENV),
            "activity_ref": activity_ref,
            "first_outcome": out.get("outcome"),
            "first_owner_receipt": bool(out.get("owner_receipt")),
            "first_owner_version": out.get("owner_version_created"),
            "v1_head": base.head_version(chain, artifact_a),
            "a_versions": base.version_count(chain, artifact_a),
            "activity_after_first": _activity(chain, activity_ref),
            "memory_rows": _memory_rows(chain),
            "budget": (out.get("budget") or {}),
        })
        if out.get("outcome") != "ACTED":
            raise SystemExit("the first write was not ACTED (outcome=%s decision=%s)"
                             % (out.get("outcome"), out.get("decision")))

        # 作品 B：另一件真实作品，只做“不串目标”的对照。
        b = chain.port.create_artifact(
            workspace_id=str(chain.data["workspace_id"]), title="另一件作品",
            content=B_BODY, grant_id=str(chain.data["artifact_grant_id"]),
            operation_key="g1:b-create:%s" % config.get("run_tag", "demo"))
        if not getattr(b, "applied", False):
            raise SystemExit("artifact B was not created (status=%s reason=%s)"
                             % (getattr(b, "status", None), getattr(b, "reason", None)))
        artifact_b = str(b.artifact_id)
        report["artifact_b"] = artifact_b
        report["b_head"] = base.head_version(chain, artifact_b)
        report["b_versions"] = base.version_count(chain, artifact_b)

        # 中断：走既有 operator pause 接口；不写资格、不自动恢复。
        odl.set_pause(instance, True, reason="g1:interrupt")
        report["pause_set"] = odl.status(instance)["pause"]
        report["state_at_pause"] = odl.status(instance)["state"]["state_class"]
        report["head_before_exit"] = base.head_version(chain, artifact_a)
        report["versions_before_exit"] = base.version_count(chain, artifact_a)
    finally:
        if artifact_a:
            report["head_at_end"] = base.head_version(chain, artifact_a)
            report["memory_rows_at_end"] = _memory_rows(chain)
        report.update(base.close_chain(chain))
    _dump_phase(report, root)
    return report


# ---------------------------------------------------------------------------------------------
# P2：新 PID 重启 → 暂停纯读 → 继续 → UNKNOWN 对账 → CAS 冲突 → NO_ACTION → 再继续
# ---------------------------------------------------------------------------------------------


def phase_p2(config: dict) -> dict:
    import one_day_life as odl
    import v05_restart_continuation_smoke as base

    root = Path(config["root"])
    artifact_a = str(config["artifact_a"])
    artifact_b = str(config["artifact_b"])
    chain = base.open_chain(root, "p2")
    chain.cycle.work_artifact_id = artifact_a
    instance = root / "instance"
    activity_ref = str(config.get("activity_ref") or "")
    report: dict = {"phase": "p2", "pid": os.getpid(), "root": str(root),
                    "artifact_a": artifact_a, "artifact_b": artifact_b,
                    "activity_ref": activity_ref}
    try:
        report.update({
            "run_id": chain.env.get(odl.RUN_ID_ENV),
            "pause_at_start": odl.status(instance)["pause"],
            "state_at_start": odl.status(instance)["state"]["state_class"],
            "head_at_start": base.head_version(chain, artifact_a),
            "versions_at_start": base.version_count(chain, artifact_a),
            "b_head_at_start": base.head_version(chain, artifact_b),
            "memory_at_start": _memory_rows(chain),
            "activity_at_start": _activity(chain, activity_ref),
        })

        # -- 1. 暂停轮：纯读零写 ------------------------------------------------
        moment = stamp()
        paused = chain.cycle.evaluate_once(trigger_ref="g1:p2:paused", act=True,
                                           observed_at=moment)
        report["paused_round"] = {"outcome": paused.get("outcome"),
                                  "llm_calls": paused.get("llm_calls")}
        report["head_while_paused"] = base.head_version(chain, artifact_a)
        report["versions_while_paused"] = base.version_count(chain, artifact_a)
        report["memory_while_paused"] = _memory_rows(chain)

        # -- 2. 正式解除暂停，新 Decision 继续同一个 Activity -----------------
        odl.set_pause(instance, False, reason="g1:resume")
        out2 = _continue_round(chain, artifact_a, A_V2, trigger="g1:p2:continue-1")
        report["continue_1"] = {"outcome": out2.get("outcome"),
                                "decision": out2.get("decision"),
                                "llm_calls": out2.get("llm_calls"),
                                "owner_version_created": out2.get("owner_version_created"),
                                "activity_ref": out2.get("activity_ref")
                                or (out2.get("step_receipt") or {}).get("activity_ref")}
        activity_ref = str(report["continue_1"]["activity_ref"] or activity_ref)
        report["activity_after_continue_1"] = _activity(chain, activity_ref)
        report["head_after_continue_1"] = base.head_version(chain, artifact_a)
        report["versions_after_continue_1"] = base.version_count(chain, artifact_a)

        # -- 3. UNKNOWN：真实写入落地，回执在适配器注入处丢失 ------------------
        adapter = chain.runtime.artifact_action_adapter
        adapter.fault_injection_drop_response_after_write = True
        out3 = _continue_round(chain, artifact_a, A_V3, trigger="g1:p2:unknown-write")
        adapter.fault_injection_drop_response_after_write = False
        action_id = str(out3.get("action_id") or "")
        if not action_id:
            raise SystemExit("the UNKNOWN round reported no AR-0 action id (outcome=%s "
                             "decision=%s)" % (out3.get("outcome"), out3.get("decision")))
        action_unknown = chain.runtime.action_read.get_action(action_id) or {}
        report["unknown_round"] = {
            "outcome": out3.get("outcome"),
            "decision": out3.get("decision"),
            "llm_calls": out3.get("llm_calls"),
            "action_id": action_id,
            "action_status": action_unknown.get("status"),
            "owner_version_created": out3.get("owner_version_created"),
            "owner_version_id": out3.get("owner_version_id"),
        }
        report["head_after_unknown"] = base.head_version(chain, artifact_a)
        report["versions_after_unknown"] = base.version_count(chain, artifact_a)
        report["activity_after_unknown"] = _activity(chain, activity_ref)

        # 只允许用 owner 自己的 operation 对账；零裸重发。
        resolved = chain.runtime.action_command.reconcile_action(action_id=action_id)
        action_resolved = chain.runtime.action_read.get_action(action_id) or {}
        reconciliations = chain.runtime.action_read.list_reconciliations(action_id)
        evidences = chain.runtime.action_read.list_result_evidences(action_id)
        report["reconcile"] = {
            "result": (resolved.get("reconciliation") or {}).get("result"),
            "action_status_after": action_resolved.get("status"),
            "from_status": (resolved.get("reconciliation") or {}).get("previous_status"),
            "reconciliations": len(reconciliations),
            "evidences": [{"verified": row.get("verified")}
                          for row in evidences],
        }
        report["versions_after_reconcile"] = base.version_count(chain, artifact_a)
        report["head_after_reconcile"] = base.head_version(chain, artifact_a)
        report["activity_after_reconcile"] = _activity(chain, activity_ref)

        # -- 4. CAS 冲突：正式 Decision CONTINUE，提交前另一真实 writer 推进 head ------
        # 这一段不直接调 AR-0：它跑一轮完整的 ``evaluate_once``，脚本 Decision 选 CONTINUE，
        # AG-1 提交、AG-2 采纳并把 step 绑定到同一个 Activity，AR-0 读出 A 的当前 head 并把它
        # 冻结进 payload。唯一注入点是 ``submit_action`` 既有的 ``fault_hook``：在 submission
        # fence 已落盘、adapter 调用之前（``before_adapter_call``）让真实 owner 的另一个 writer
        # 先推一版。于是正式链带着已经过期的 ``expected_head`` 过闸，由 owner 自己以 CAS 拒绝。
        import managed_artifact_action_adapter as maaa
        import v05_result_sharing_continuation_smoke as sharing

        head_before_cas = base.head_version(chain, artifact_a)
        versions_before_cas = base.version_count(chain, artifact_a)
        memory_before_cas = _memory_rows(chain)
        activity_before_cas = _activity(chain, activity_ref)
        moment = stamp()
        candidate = sharing.continue_candidate(chain, artifact_a, observed_at=moment)
        if not candidate:
            raise SystemExit("no exact CONTINUE candidate for the CAS round: %r"
                             % (chain.runtime.candidate_materializer.build_candidate_set(
                                 observed_at=moment).candidate_refs,))
        chain.client.payload = {"kind": "append_paragraph", "text": A_V4,
                                "self_note": "stub: continue while another writer moves the head"}
        chain.cognition.script = {"decision": "CONTINUE", "selected_candidate_id": candidate,
                                  "deferred_candidate_ids": []}

        # 注入点用 AR-0 既有 adapter 边界：``submit_action`` 已把 submission fence 落盘、
        # 冻结 payload 交给 adapter；这里在 demo 层把这一次 ``adapter.submit`` 包一层，先让
        # 另一个真实 writer 推一版，再原样调用 adapter。
        adapter = chain.runtime.artifact_action_adapter
        original_adapter_submit = adapter.submit
        injection: dict = {"injected": False, "stage": "", "action_id": "",
                           "expected_head": "", "other": None,
                           "head_after_other": "", "versions_after_other": 0}

        def _submit_with_concurrent_writer(*args, **kwargs):
            payload_now = dict(kwargs.get("payload") or {})
            action_now = dict(kwargs.get("action") or {})
            is_target = (str(payload_now.get("op") or "") == maaa.OP_COMMIT_VERSION
                         and str(payload_now.get("artifact_id") or "") == artifact_a)
            if is_target and not injection["injected"]:
                injection["injected"] = True
                injection["stage"] = "before_adapter_owner_call"
                injection["action_id"] = str(action_now.get("action_id") or "")
                injection["expected_head"] = str(payload_now.get("expected_head") or "")
                injection["other"] = chain.port.commit_version(
                    artifact_id=artifact_a,
                    expected_head=injection["expected_head"],
                    content="另一个 writer 推进的一版（与本次提交无关）。",
                    grant_id=str(chain.data["artifact_grant_id"]),
                    operation_key="g1:other-writer:%s" % config.get("run_tag", "demo"))
                injection["head_after_other"] = base.head_version(chain, artifact_a)
                injection["versions_after_other"] = base.version_count(chain, artifact_a)
            return original_adapter_submit(*args, **kwargs)

        adapter.submit = _submit_with_concurrent_writer
        try:
            out4 = chain.cycle.evaluate_once(trigger_ref="g1:p2:cas-formal", act=True,
                                             observed_at=moment)
        finally:
            adapter.submit = original_adapter_submit

        other = injection["other"]
        if not injection["injected"] or not getattr(other, "applied", False):
            raise SystemExit("the concurrent owner writer did not move the head before the "
                             "formal submission (injected=%s status=%s)"
                             % (injection["injected"], getattr(other, "status", None)))
        cas_action_id = str(out4.get("action_id") or injection["action_id"])
        cas_action = chain.runtime.action_read.get_action(cas_action_id) or {}
        cas_attempts = chain.runtime.action_read.list_attempts(cas_action_id)
        cas_attempt = cas_attempts[-1] if cas_attempts else {}
        cas_receipts = chain.runtime.action_read.list_receipts(cas_action_id)
        cas_details = dict((cas_receipts[-1] or {}).get("details") or {}) if cas_receipts else {}
        report["cas_conflict"] = {
            "outcome": out4.get("outcome"),
            "decision": out4.get("decision"),
            "decision_id": out4.get("decision_id"),
            "llm_calls": out4.get("llm_calls"),
            "hook_stage": injection["stage"],
            "formal_expected_head": injection["expected_head"],
            "action_id": cas_action_id,
            "action_status": cas_action.get("status"),
            "action_activity_ref": cas_action.get("activity_ref"),
            "action_target_ref": cas_action.get("target_ref"),
            "transport_result": cas_attempt.get("transport_result"),
            "error_class": cas_attempt.get("error_class"),
            "receipt_claim": (cas_receipts[-1] or {}).get("status_claim") if cas_receipts else None,
            "owner_status": cas_details.get("owner_status") or out4.get("owner_status"),
            "owner_version_created": out4.get("owner_version_created"),
            "owner_version_id": out4.get("owner_version_id"),
            "head_before": head_before_cas,
            "head_after_other_writer": injection["head_after_other"],
            "head_after_submit": base.head_version(chain, artifact_a),
            "versions_before": versions_before_cas,
            "versions_after_other_writer": injection["versions_after_other"],
            "versions_after_submit": base.version_count(chain, artifact_a),
            "activity_before": _activity_view(activity_before_cas),
            "activity_after": _activity_view(_activity(chain, activity_ref)),
            "activity_ref": out4.get("activity_ref"),
            "b_head_after": base.head_version(chain, artifact_b),
            "evidences": len(chain.runtime.action_read.list_result_evidences(cas_action_id)),
            "receipt_count": len(cas_receipts),
            "handoff": bool(out4.get("experience_handoff")),
            "memory_before": memory_before_cas,
            "memory_after": _memory_rows(chain),
        }

        # -- 5. 新 Decision 读当前 head，再沿现规则续 A ------------------------
        out5 = _continue_round(chain, artifact_a, A_V4, trigger="g1:p2:continue-after-cas")
        report["continue_after_cas"] = {
            "outcome": out5.get("outcome"), "decision": out5.get("decision"),
            "owner_version_created": out5.get("owner_version_created"),
            "llm_calls": out5.get("llm_calls")}
        report["head_after_cas_continue"] = base.head_version(chain, artifact_a)
        report["versions_after_cas_continue"] = base.version_count(chain, artifact_a)
        report["activity_after_cas_continue"] = _activity(chain, activity_ref)

        # -- 6. 合法 NO_ACTION：无附带写 ---------------------------------------
        head_before_quiet = base.head_version(chain, artifact_a)
        versions_before_quiet = base.version_count(chain, artifact_a)
        b_before_quiet = base.head_version(chain, artifact_b)
        memory_before_quiet = _memory_rows(chain)
        quiet = _quiet_round(chain, trigger="g1:p2:no-action")
        report["no_action"] = {"outcome": quiet.get("outcome"),
                               "decision": quiet.get("decision"),
                               "llm_calls": quiet.get("llm_calls")}
        report["head_after_quiet"] = base.head_version(chain, artifact_a)
        report["versions_after_quiet"] = base.version_count(chain, artifact_a)
        report["b_after_quiet"] = base.head_version(chain, artifact_b)
        report["memory_after_quiet"] = _memory_rows(chain)
        report["quiet_baseline"] = {"head": head_before_quiet, "versions": versions_before_quiet,
                                    "b_head": b_before_quiet, "memory": memory_before_quiet}

        # -- 7. 安静之后仍可继续 A：真实新版本 ---------------------------------
        out7 = _continue_round(chain, artifact_a, A_V5, trigger="g1:p2:continue-after-quiet")
        report["continue_after_quiet"] = {
            "outcome": out7.get("outcome"), "decision": out7.get("decision"),
            "owner_version_created": out7.get("owner_version_created")}
        report["head_at_end"] = base.head_version(chain, artifact_a)
        report["versions_at_end"] = base.version_count(chain, artifact_a)
        report["activity_at_end"] = _activity(chain, activity_ref)
    finally:
        report.update(base.close_chain(chain))
    _dump_phase(report, root)
    return report


# ---------------------------------------------------------------------------------------------
# 父调度器：只建根、起两个阶段、核对报告
# ---------------------------------------------------------------------------------------------


def _run_phase(phase: str, root: Path, payload: dict, *, timeout: float = 900.0) -> dict:
    config_path = root / ("g1-config-%s.json" % phase)
    config_path.write_text(json.dumps(dict(payload, root=str(root), run_tag=phase),
                                      ensure_ascii=False), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-B", str(Path(__file__).resolve()),
         "--phase", phase, "--config", str(config_path)],
        capture_output=True, text=True, timeout=timeout,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    print(completed.stdout, end="")
    if completed.returncode != 0:
        # The full stderr stays in the isolated root; stdout only gets the last exception line
        # (bounded), never a whole report/owner object or any text body.
        log_path = root / ("g1-stderr-%s.log" % phase)
        log_path.write_text(completed.stderr, encoding="utf-8", errors="replace")
        last = [line for line in completed.stderr.strip().splitlines() if line.strip()]
        print("  %s exited %d; full stderr: %s" % (phase, completed.returncode, log_path))
        if last:
            print("  %s last stderr line: %s" % (phase, last[-1][:400]))
        raise SystemExit("%s exited %d" % (phase, completed.returncode))
    lines = [line for line in completed.stdout.splitlines()
             if line.startswith(PHASE_PREFIX)]
    if not lines:
        raise SystemExit("%s printed no phase report" % phase)
    # stdout carries only the whitelist; the full local report stays in the isolated root and
    # is what the parent asserts against.
    report_path = root / ("g1-phase-%s.json" % phase)
    if report_path.exists():
        return json.loads(report_path.read_text(encoding="utf-8"))
    return json.loads(lines[-1].split(" ", 1)[1])


def _assert_chain(p1: dict, p2: dict) -> None:
    import one_day_life as odl

    check("P1/P2 are two different PIDs", p1["pid"] != p2["pid"], [p1["pid"], p2["pid"]])
    check("both phases reopened the same root", p1["root"] == p2["root"], [p1["root"],
                                                                         p2["root"]])
    check("P1 ran a real write with an owner receipt",
          p1.get("first_outcome") == "ACTED" and bool(p1.get("first_owner_receipt")), p1.get("first_outcome"))
    check("P1 really released the runtime lease and stopped its own owner",
          bool(p1.get("closed")) and bool(p1.get("service_stopped")),
          {"closed": p1.get("closed"), "service": p1.get("service_stopped")})
    check("P1 left the instance paused", bool((p1.get("pause_set") or {}).get("paused")),
          {"paused": (p1.get("pause_set") or {}).get("paused"),
           "revision": (p1.get("pause_set") or {}).get("revision")})
    check("P1/P2 kept the same run identity",
          p1.get("run_id") and p1.get("run_id") == p2.get("run_id"),
          [p1.get("run_id"), p2.get("run_id")])
    check("nothing was written between the two processes",
          p2["head_at_start"] == p1["head_at_end"]
          and p2["versions_at_start"] == p1["versions_before_exit"]
          and p2["memory_at_start"] == p1["memory_rows_at_end"],
          {"head": [p1.get("head_at_end"), p2.get("head_at_start")],
           "versions": [p1.get("versions_before_exit"), p2.get("versions_at_start")],
           "memory": [p1.get("memory_rows_at_end"), p2.get("memory_at_start")]})
    check("P2 starts in the confirmed pause", bool((p2.get("pause_at_start") or {}).get("paused"))
          and p2.get("state_at_start") == odl.STATE_PAUSED, p2.get("state_at_start"))
    check("the paused round is pure read, zero requests",
          (p2.get("paused_round") or {}).get("outcome") == "PAUSED_BY_OPERATOR"
          and (p2.get("paused_round") or {}).get("llm_calls") == 0, p2.get("paused_round"))
    check("the paused round wrote nothing",
          p2["head_while_paused"] == p2["head_at_start"]
          and p2["versions_while_paused"] == p2["versions_at_start"]
          and p2["memory_while_paused"] == p2["memory_at_start"], p2.get("head_while_paused"))
    check("after the resume the same Activity really continued into a new version",
          (p2.get("continue_1") or {}).get("outcome") == "ACTED"
          and (p2.get("continue_1") or {}).get("decision") == "CONTINUE"
          and p2["head_after_continue_1"] != p2["head_at_start"]
          and p2["versions_after_continue_1"] == p2["versions_at_start"] + 1,
          p2.get("continue_1"))
    check("the continuation kept the same foreground Activity",
          (p2.get("activity_after_continue_1") or {}).get("status") == "ACTIVE",
          _activity_view(p2.get("activity_after_continue_1") or {}))

    unknown = p2.get("unknown_round") or {}
    check("the dropped-receipt write stayed UNKNOWN in AR-0",
          unknown.get("outcome") == "STEP_UNKNOWN"
          and unknown.get("action_status") == "UNKNOWN"
          and unknown.get("owner_version_created") is None, unknown)
    check("the UNKNOWN write had really landed exactly once",
          p2["versions_after_unknown"] == p2["versions_after_continue_1"] + 1,
          [p2.get("versions_after_continue_1"), p2.get("versions_after_unknown")])
    check("the Activity was not falsely completed by the UNKNOWN round",
          (p2.get("activity_after_unknown") or {}).get("status") == "ACTIVE",
          _activity_view(p2.get("activity_after_unknown") or {}))
    reconcile = p2.get("reconcile") or {}
    check("only the original operation was reconciled to SUCCEEDED, no bare re-send",
          reconcile.get("result") == "CONFIRMED_SUCCEEDED"
          and reconcile.get("action_status_after") == "SUCCEEDED"
          and reconcile.get("from_status") == "UNKNOWN", reconcile)
    check("reconciliation observed the owner's own effect",
          bool(reconcile.get("evidences")) and all(row.get("verified") for row in reconcile["evidences"]),
          reconcile.get("evidences"))
    check("reconciliation minted no second version",
          p2["versions_after_reconcile"] == p2["versions_after_unknown"],
          [p2.get("versions_after_unknown"), p2.get("versions_after_reconcile")])

    cas = p2.get("cas_conflict") or {}
    check("the CAS round ran the formal chain: Decision committed, step bound to the same Activity",
          cas.get("outcome") == "STEP_REFUSED" and cas.get("decision") == "CONTINUE"
          and bool(cas.get("decision_id"))
          and cas.get("activity_ref") == p2.get("activity_ref")
          and cas.get("action_activity_ref") == p2.get("activity_ref")
          and _bare(cas.get("action_target_ref")) == _bare(p2.get("artifact_a")), cas)
    check("the one-shot hook moved the real head after the fence, before the adapter's owner call",
          cas.get("hook_stage") == "before_adapter_owner_call"
          and cas.get("formal_expected_head") == cas.get("head_before")
          and cas.get("head_after_other_writer") != cas.get("head_before")
          and cas.get("versions_after_other_writer") == cas.get("versions_before") + 1, cas)
    check("the stale formal submission was refused by the owner's CAS",
          cas.get("action_status") == "FAILED" and cas.get("owner_status") == "CONFLICT"
          and cas.get("receipt_claim") == "REJECTED"
          and cas.get("transport_result") == "DEFINITE_PROVIDER_REJECTION", cas)
    check("the CAS refusal wrote nothing and invented no result",
          cas.get("versions_after_submit") == cas.get("versions_after_other_writer")
          and cas.get("head_after_submit") == cas.get("head_after_other_writer")
          and cas.get("evidences") == 0 and cas.get("handoff") is False
          and cas.get("memory_after") == cas.get("memory_before"), cas)
    check("the CAS refusal did not complete or move the Activity",
          (cas.get("activity_after") or {}).get("status")
          == (cas.get("activity_before") or {}).get("status")
          and (cas.get("activity_after") or {}).get("revision")
          == (cas.get("activity_before") or {}).get("revision")
          and cas.get("b_head_after") == p2.get("b_head_at_start"), cas)
    check("a fresh Decision read the current head and continued A",
          (p2.get("continue_after_cas") or {}).get("outcome") == "ACTED"
          and p2["versions_after_cas_continue"] == cas.get("versions_after_submit") + 1,
          p2.get("continue_after_cas"))

    baseline = p2.get("quiet_baseline") or {}
    check("the NO_ACTION round was legal and wrote nothing",
          (p2.get("no_action") or {}).get("outcome") == "NO_ACTION"
          and p2["head_after_quiet"] == baseline.get("head")
          and p2["versions_after_quiet"] == baseline.get("versions")
          and p2["b_after_quiet"] == baseline.get("b_head")
          and p2["memory_after_quiet"] == baseline.get("memory"), p2.get("no_action"))
    check("the work stayed continuable after NO_ACTION",
          (p2.get("continue_after_quiet") or {}).get("outcome") == "ACTED"
          and p2["versions_at_end"] == p2["versions_after_quiet"] + 1,
          p2.get("continue_after_quiet"))
    check("B was never written by any round",
          p2.get("b_head_at_start") == p1.get("b_head") and p2.get("b_after_quiet") == p1.get("b_head"),
          [p1.get("b_head"), p2.get("b_head_at_start")])
    check("P2 also released its lease and stopped its own owner",
          bool(p2.get("closed")) and bool(p2.get("service_stopped")),
          {"closed": p2.get("closed"), "service": p2.get("service_stopped")})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="G1 same-work recovery demo")
    parser.add_argument("--phase", choices=["p1", "p2"])
    parser.add_argument("--config")
    args = parser.parse_args(argv)

    if args.phase:
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))
        if args.phase == "p1":
            phase_p1(config)
            return 0
        phase_p2(config)
        return 0

    import v05_restart_continuation_smoke as base

    root = base.prepare_fixture()
    print("fixture root:", root)
    p1 = _run_phase("p1", Path(root), {})
    p2 = _run_phase("p2", Path(root), {
        "artifact_a": p1["artifact_a"], "artifact_b": p1["artifact_b"],
        "activity_ref": p1.get("activity_ref") or ""})
    _assert_chain(p1, p2)

    failed = [name for name, ok, _detail in CHECKS if not ok]
    print()
    print("PID chain:", p1["pid"], "->", p2["pid"], "(parent", os.getpid(), ")")
    print("work A:", p1["artifact_a"], "versions:", p2.get("versions_at_end"),
          "head:", p2.get("head_at_end"))
    print("work B:", p1["artifact_b"], "head unchanged:", p2.get("b_after_quiet") == p1.get("b_head"))
    for name in failed:
        print("FAILED:", name)
    print("RESULT: %s" % ("FAIL" if failed else "PASS"))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""G1 下一次限定采用：**只有离线入口**的预检 harness。

这是 [预登记](../../docs/engineering/g1-next-prereg-20261031.md) 的离线对应物，不是真实运行入口：

* 本文件只提供 ``--offline-check``（以及内部用的 ``--phase``/``--reserve``）。
  ``--prepare``/``--run``/``--execute-approved`` 会被**明确拒绝**，不写任何文件；
  ``--execute-approved`` 本身不构成授权。
* 全部动作发生在一个 ``tempfile.mkdtemp`` 的隔离根里；不创建/访问 ``/root`` 正式根、
  不 prepare、不 source 密钥、不探测 provider、不发 HTTP。子进程开头安装 socket 硬拒，
  网络连接一律 ``OFFLINE_HTTP_FORBIDDEN``，只有 AF_UNIX 的 owner 通信放行。
* Decision 与内容回答是**脚本替身**（复用 ``g1_same_work_recovery_demo`` 的既有链）；
  真实件是 Supply/Memory/Activity owner、``ProductionLifeRuntime``、AG-0/AG-1/AG-2/AR-0、
  LR-2/LR-4。替身不证明真实模型自然采用，也不证明 G1 已过。
* 判定只认**证据**：首个真实 owner 写入的作品 A 与 Activity actA 是不可替换锚；
  第二次（跨真实 PID）必须是新的 Decision 自选 CONTINUE、绑同一个 actA、产生新 Action、
  owner 新版本/receipt；同源换 Artifact B、换 Activity、重放旧成功一律判未通过。
* 最小负控只覆盖判据真实性：wrong-A / wrong-Activity / 旧成功重放 / 无模型选择 → NOT_COVERED；
  共享 20 请求硬上限 → 第 21 次零发送拒绝；坏账本 → 零发送拒绝；重复启动窗口 → 拒绝。
  不重造仪器矩阵，不访问 a/b/c/d/e 或任何已存在实例根。

Run (POSIX/WSL)::

    PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B \
        scripts/g1_next_offline.py --offline-check
"""

from __future__ import annotations

import argparse
import contextlib
import http.client
import json
import os
import socket
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Optional, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
for entry in (str(ROOT), str(SCRIPT_DIR), str(ROOT / "tests"),
              str(ROOT / "components"), str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

PHASE_PREFIX = "G1_NEXT_PHASE "
EXIT_OK = 0
EXIT_REFUSED = 2
REASON_OFFLINE_ONLY = "OFFLINE_ONLY_NO_REAL_ENTRY"
REASON_REQUEST_CAP = "REQUEST_CAP_REACHED"
REASON_LEDGER_UNREADABLE = "REQUEST_LEDGER_UNREADABLE"
REASON_ALREADY_STARTED = "RUN_ALREADY_STARTED"
REQUEST_CAP = 20
FIXTURE_PREFIX = "g1-next-offline-"

A_V1 = "第一版：先把这一节的开头写下来。"
A_V2 = "第二版：重启之后接着这一节往下写。"
A_V3 = "第三版：这次回执丢了，但 owner 那边真的写下去了。"
A_V4 = "第四版：别人先动过 head，这次按当前 head 继续。"
A_V5 = "第五版：安静的一轮之后仍然可以继续写。"
B_BODY = "另一件作品的正文，只用来验证续写不会串到它。"

CHECKS: list[tuple[str, bool, Any]] = []


# ---------------------------------------------------------------------------------------------
# 硬拒与检查输出
# ---------------------------------------------------------------------------------------------


def _offline_model_env() -> dict[str, str]:
    """让任何误构造的真实 client 指向必然失败的地址；本链本身只用脚本替身。"""

    return {
        "NYA02_MODEL_PROVIDER": "custom",
        "NYA02_MODEL_BASE_URL": "https://offline.invalid/v1",
        "NYA02_MODEL_NAME": "offline-stand-in-model",
        "NYA02_MODEL_API_KEY": "offline-not-a-key",
        "G1_NEXT_OFFLINE": "1",
    }


@contextlib.contextmanager
def _http_forbidden():
    """任何真实网络 connect 都直接失败；AF_UNIX 的 owner 通信放行。"""

    originals = {
        "http": getattr(http.client.HTTPConnection, "connect", None),
        "https": getattr(http.client.HTTPSConnection, "connect", None),
        "socket": getattr(socket.socket, "connect", None),
    }

    def _blocked(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("OFFLINE_HTTP_FORBIDDEN")

    original_socket_connect = originals["socket"]

    def _blocked_inet(sock: Any, *args: Any, **kwargs: Any) -> Any:
        if getattr(sock, "family", None) == socket.AF_UNIX:
            if original_socket_connect is None:  # pragma: no cover - 异常环境
                raise AssertionError("OFFLINE_HTTP_FORBIDDEN")
            return original_socket_connect(sock, *args, **kwargs)
        raise AssertionError("OFFLINE_HTTP_FORBIDDEN")

    http.client.HTTPConnection.connect = _blocked
    http.client.HTTPSConnection.connect = _blocked
    socket.socket.connect = _blocked_inet
    try:
        yield
    finally:
        if originals["http"] is not None:
            http.client.HTTPConnection.connect = originals["http"]
        if originals["https"] is not None:
            http.client.HTTPSConnection.connect = originals["https"]
        if originals["socket"] is not None:
            socket.socket.connect = originals["socket"]


def _check(name: str, condition: Any, detail: Any = None) -> None:
    CHECKS.append((name, bool(condition), detail))
    print("CHECK %-78s %s" % (name, "PASS" if condition else "FAIL"))
    if not condition and detail is not None:
        print("  detail:", json.dumps(detail, ensure_ascii=False, default=str)[:900])


# ---------------------------------------------------------------------------------------------
# 阶段共用（复用既有 demo/夹具，不重造一条新链）
# ---------------------------------------------------------------------------------------------


def _stamp() -> str:
    """每轮一个自己的观察时刻；只推进夹具时钟，不碰静默规则。"""

    import g1_same_work_recovery_demo as demo

    return demo.stamp()


def _memory_rows(chain: Any) -> int:
    from g1_same_work_recovery_demo import _memory_rows as rows

    return rows(chain)


def _activity(chain: Any, activity_ref: str) -> dict:
    from g1_same_work_recovery_demo import _activity as read

    return read(chain, activity_ref)


def _bare(value: Any) -> str:
    from g1_same_work_recovery_demo import _bare as bare

    return bare(value)


def _round_view(out: Mapping[str, Any]) -> dict[str, Any]:
    """一轮结果里可进入判定的字段；只留 id/枚举/布尔/计数。"""

    receipt = dict(out.get("step_receipt") or {})
    return {
        "outcome": out.get("outcome"),
        "decision": out.get("decision"),
        "decision_id": out.get("decision_id"),
        "llm_calls": out.get("llm_calls"),
        "action_id": out.get("action_id") or receipt.get("action_id"),
        "artifact_ref": receipt.get("artifact_id") or out.get("target_ref"),
        "activity_ref": out.get("activity_ref") or receipt.get("activity_ref"),
        "owner_version_created": out.get("owner_version_created"),
        "owner_version_id": out.get("owner_version_id"),
        "owner_status": out.get("owner_status"),
        "budget": out.get("budget"),
    }


def _continue_round(chain: Any, artifact_id: str, text: str, *, trigger: str,
                    capture: dict[str, Any]) -> dict[str, Any]:
    from g1_same_work_recovery_demo import _continue_round as run

    out = run(chain, artifact_id, text, trigger=trigger)
    capture[trigger] = _round_view(out)
    return out


def _quiet_round(chain: Any, *, trigger: str) -> dict[str, Any]:
    from g1_same_work_recovery_demo import _quiet_round as run

    return run(chain, trigger=trigger)


def _safe_phase_view(report: Mapping[str, Any]) -> dict[str, Any]:
    """stdout 白名单：只 id、结果、计数；正文/提示词/异常原文都不进来。"""

    view = {
        "phase": report.get("phase"), "pid": report.get("pid"),
        "root": report.get("root"), "run_id": report.get("run_id"),
        "artifact_a": report.get("artifact_a"), "artifact_b": report.get("artifact_b"),
        "activity_ref": report.get("activity_ref"),
        "first_action_id": report.get("first_action_id"),
        "close": report.get("close") or {},
        "rounds": dict(report.get("rounds") or {}),
        "facts": {name: report.get(name) for name in (
            "first_outcome", "first_owner_receipt", "first_owner_version",
            "v1_head", "a_versions", "b_head", "b_versions", "memory_rows",
            "head_at_start", "versions_at_start", "head_before_exit",
            "versions_before_exit", "head_at_end", "versions_at_end",
            "pause_at_start", "state_at_start", "budget_at_start",
        ) if name in report},
    }
    return view


def _dump_phase(report: Mapping[str, Any], root: Path) -> None:
    path = Path(root) / ("g1next-phase-%s.json" % report.get("phase"))
    path.write_text(json.dumps(dict(report), ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8")
    print(PHASE_PREFIX + json.dumps(_safe_phase_view(report), ensure_ascii=False, default=str))


# ---------------------------------------------------------------------------------------------
# P1：首个真实 owner 写入 = 作品 A 与 Activity actA（不可替换锚）+ 中断 + 正规释放
# ---------------------------------------------------------------------------------------------


def phase_p1(config: Mapping[str, Any]) -> dict[str, Any]:
    import one_day_life as odl
    import result_method_behavior_smoke as harness
    import v05_restart_continuation_smoke as base
    from g1_same_work_recovery_demo import _select_project_starter

    root = Path(str(config["root"]))
    chain = base.open_chain(root, "p1")
    report: dict[str, Any] = {"phase": "p1", "pid": os.getpid(), "root": str(root),
                              "rounds": {}}
    artifact_a = ""
    artifact_b = ""
    try:
        material_id, revision = harness.import_material(chain)
        harness.write_question(chain, material_id, revision)
        chain.client.payload = {"kind": "append_paragraph", "text": A_V1,
                                "self_note": "stub: write the first version"}
        moment = _stamp()
        _select_project_starter(chain, moment)
        out = chain.cycle.evaluate_once(trigger_ref="g1next:p1:first-write", act=True,
                                        observed_at=moment)
        first = _round_view(out)
        report["rounds"]["first_write"] = first
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
            "first_action_id": first.get("action_id"),
            "first_outcome": out.get("outcome"),
            "first_owner_receipt": bool(out.get("owner_receipt")),
            "first_owner_version": out.get("owner_version_created"),
            "v1_head": base.head_version(chain, artifact_a),
            "a_versions": base.version_count(chain, artifact_a),
            "activity_after_first": _activity(chain, activity_ref),
            "memory_rows": _memory_rows(chain),
            "budget_after_first": _budget_snapshot(chain),
        })
        if out.get("outcome") != "ACTED":
            raise SystemExit("the first write was not ACTED (outcome=%s decision=%s)"
                             % (out.get("outcome"), out.get("decision")))

        # 作品 B：另一件真实作品，只做“不串目标/同源替换不成立”的对照。
        b = chain.port.create_artifact(
            workspace_id=str(chain.data["workspace_id"]), title="另一件作品",
            content=B_BODY, grant_id=str(chain.data["artifact_grant_id"]),
            operation_key="g1next:b-create:%s" % config.get("run_tag", "offline"))
        if not getattr(b, "applied", False):
            raise SystemExit("artifact B was not created (status=%s reason=%s)"
                             % (getattr(b, "status", None), getattr(b, "reason", None)))
        artifact_b = str(b.artifact_id)
        report["artifact_b"] = artifact_b
        report["b_head"] = base.head_version(chain, artifact_b)
        report["b_versions"] = base.version_count(chain, artifact_b)

        # 中断：既有 operator pause 接口；不写资格、不自动恢复。
        instance = root / "instance"
        odl.set_pause(instance, True, reason="g1next:interrupt")
        report["pause_set"] = odl.status(instance)["pause"]
        report["state_at_pause"] = odl.status(instance)["state"]["state_class"]
        report["head_before_exit"] = base.head_version(chain, artifact_a)
        report["versions_before_exit"] = base.version_count(chain, artifact_a)
    finally:
        if artifact_a:
            report["head_at_end"] = base.head_version(chain, artifact_a)
            report["memory_rows_at_end"] = _memory_rows(chain)
        report["close"] = base.close_chain(chain)
    _dump_phase(report, root)
    return report


# ---------------------------------------------------------------------------------------------
# P2：不同 PID 重开 → 暂停纯读 → 新 Decision CONTINUE 同 actA → UNKNOWN → CAS → NO_ACTION
# ---------------------------------------------------------------------------------------------


def phase_p2(config: Mapping[str, Any]) -> dict[str, Any]:
    import one_day_life as odl
    import v05_restart_continuation_smoke as base

    root = Path(str(config["root"]))
    artifact_a = str(config["artifact_a"])
    artifact_b = str(config["artifact_b"])
    activity_ref = str(config.get("activity_ref") or "")
    chain = base.open_chain(root, "p2")
    chain.cycle.work_artifact_id = artifact_a
    instance = root / "instance"
    capture: dict[str, Any] = {}
    report: dict[str, Any] = {"phase": "p2", "pid": os.getpid(), "root": str(root),
                              "artifact_a": artifact_a, "artifact_b": artifact_b,
                              "activity_ref": activity_ref, "rounds": capture}
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
            "budget_at_start": _budget_snapshot(chain),
        })

        # -- 1. 暂停轮：纯读零写 ------------------------------------------------
        moment = _stamp()
        paused = chain.cycle.evaluate_once(trigger_ref="g1next:p2:paused", act=True,
                                           observed_at=moment)
        report["paused_round"] = {"outcome": paused.get("outcome"),
                                  "llm_calls": paused.get("llm_calls")}
        report["head_while_paused"] = base.head_version(chain, artifact_a)
        report["versions_while_paused"] = base.version_count(chain, artifact_a)
        report["memory_while_paused"] = _memory_rows(chain)

        # -- 2. 正式解除暂停，新 Decision 继续同一个 Activity -----------------
        odl.set_pause(instance, False, reason="g1next:resume")
        out2 = _continue_round(chain, artifact_a, A_V2, trigger="g1next:p2:continue-1",
                               capture=capture)
        report["head_after_continue_1"] = base.head_version(chain, artifact_a)
        report["versions_after_continue_1"] = base.version_count(chain, artifact_a)
        report["activity_after_continue_1"] = _activity(chain, activity_ref)

        # -- 3. UNKNOWN：真实写入落地，回执在适配器注入处丢失 ------------------
        adapter = chain.runtime.artifact_action_adapter
        adapter.fault_injection_drop_response_after_write = True
        out3 = _continue_round(chain, artifact_a, A_V3, trigger="g1next:p2:unknown-write",
                               capture=capture)
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
        report["versions_after_unknown"] = base.version_count(chain, artifact_a)
        report["activity_after_unknown"] = _activity(chain, activity_ref)

        resolved = chain.runtime.action_command.reconcile_action(action_id=action_id)
        action_resolved = chain.runtime.action_read.get_action(action_id) or {}
        reconciliations = chain.runtime.action_read.list_reconciliations(action_id)
        evidences = chain.runtime.action_read.list_result_evidences(action_id)
        report["reconcile"] = {
            "result": (resolved.get("reconciliation") or {}).get("result"),
            "action_status_after": action_resolved.get("status"),
            "from_status": (resolved.get("reconciliation") or {}).get("previous_status"),
            "reconciliations": len(reconciliations),
            "evidences": [{"verified": row.get("verified")} for row in evidences],
        }
        report["versions_after_reconcile"] = base.version_count(chain, artifact_a)
        report["activity_after_reconcile"] = _activity(chain, activity_ref)

        # -- 4. CAS 冲突：正式 Decision CONTINUE，提交前另一真实 writer 推进 head ------
        import managed_artifact_action_adapter as maaa
        import v05_result_sharing_continuation_smoke as sharing

        head_before_cas = base.head_version(chain, artifact_a)
        versions_before_cas = base.version_count(chain, artifact_a)
        memory_before_cas = _memory_rows(chain)
        activity_before_cas = _activity(chain, activity_ref)
        moment = _stamp()
        candidate = sharing.continue_candidate(chain, artifact_a, observed_at=moment)
        if not candidate:
            raise SystemExit("no exact CONTINUE candidate for the CAS round: %r"
                             % (chain.runtime.candidate_materializer.build_candidate_set(
                                 observed_at=moment).candidate_refs,))
        chain.client.payload = {"kind": "append_paragraph", "text": A_V4,
                                "self_note": "stub: continue while another writer moves the head"}
        chain.cognition.script = {"decision": "CONTINUE", "selected_candidate_id": candidate,
                                  "deferred_candidate_ids": []}

        adapter = chain.runtime.artifact_action_adapter
        original_adapter_submit = adapter.submit
        injection: dict[str, Any] = {"injected": False, "stage": "", "action_id": "",
                                     "expected_head": "", "other": None,
                                     "head_after_other": "", "versions_after_other": 0}

        def _submit_with_concurrent_writer(*args: Any, **kwargs: Any) -> Any:
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
                    operation_key="g1next:other-writer:%s" % config.get("run_tag", "offline"))
                injection["head_after_other"] = base.head_version(chain, artifact_a)
                injection["versions_after_other"] = base.version_count(chain, artifact_a)
            return original_adapter_submit(*args, **kwargs)

        adapter.submit = _submit_with_concurrent_writer
        try:
            out4 = chain.cycle.evaluate_once(trigger_ref="g1next:p2:cas-formal", act=True,
                                             observed_at=moment)
        finally:
            adapter.submit = original_adapter_submit
        capture["g1next:p2:cas-formal"] = _round_view(out4)

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
            "activity_before": activity_before_cas,
            "activity_after": _activity(chain, activity_ref),
            "activity_ref": out4.get("activity_ref"),
            "b_head_after": base.head_version(chain, artifact_b),
            "evidences": len(chain.runtime.action_read.list_result_evidences(cas_action_id)),
            "receipt_count": len(cas_receipts),
            "handoff": bool(out4.get("experience_handoff")),
            "memory_before": memory_before_cas,
            "memory_after": _memory_rows(chain),
        }

        # -- 5. 新 Decision 读当前 head，再沿现规则续 A ------------------------
        out5 = _continue_round(chain, artifact_a, A_V4, trigger="g1next:p2:continue-after-cas",
                               capture=capture)
        report["versions_after_cas_continue"] = base.version_count(chain, artifact_a)
        report["activity_after_cas_continue"] = _activity(chain, activity_ref)

        # -- 6. 合法 NO_ACTION：无附带写 ---------------------------------------
        head_before_quiet = base.head_version(chain, artifact_a)
        versions_before_quiet = base.version_count(chain, artifact_a)
        b_before_quiet = base.head_version(chain, artifact_b)
        memory_before_quiet = _memory_rows(chain)
        quiet = _quiet_round(chain, trigger="g1next:p2:no-action")
        capture["g1next:p2:no-action"] = _round_view(quiet)
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
        out7 = _continue_round(chain, artifact_a, A_V5,
                               trigger="g1next:p2:continue-after-quiet", capture=capture)
        report["head_at_end"] = base.head_version(chain, artifact_a)
        report["versions_at_end"] = base.version_count(chain, artifact_a)
        report["activity_at_end"] = _activity(chain, activity_ref)
        report["budget_at_end"] = _budget_snapshot(chain)
    finally:
        report["close"] = base.close_chain(chain)
    _dump_phase(report, root)
    return report


def _budget_snapshot(chain: Any) -> Optional[dict[str, Any]]:
    """同一实例自己的预算读回；只取计数/上限/run_id，不写账本。"""

    budget = getattr(getattr(chain, "cycle", None), "budget", None)
    if budget is None:
        return None
    try:
        snap = dict(budget.snapshot())
    except Exception as exc:  # noqa: BLE001 - 读失败如实记，不当零
        return {"error": type(exc).__name__}
    return {key: snap.get(key) for key in (
        "spent_today", "remaining_today", "cognition_used_today",
        "cognition_remaining_today", "actions_used_today", "cognition_counter_source",
    )}


# ---------------------------------------------------------------------------------------------
# 父调度器：只建隔离根、起两个真实 PID、核对判定
# ---------------------------------------------------------------------------------------------


def _run_phase(phase: str, root: Path, payload: Mapping[str, Any],
               *, timeout: float = 900.0) -> dict[str, Any]:
    config_path = root / ("g1next-config-%s.json" % phase)
    config_path.write_text(json.dumps(dict(payload, root=str(root),
                                           run_tag="next-%s" % phase),
                                      ensure_ascii=False), encoding="utf-8")
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", **_offline_model_env()}
    completed = subprocess.run(
        [sys.executable, "-B", str(Path(__file__).resolve()),
         "--phase", phase, "--config", str(config_path)],
        capture_output=True, text=True, timeout=timeout, env=env)
    print(completed.stdout, end="")
    if completed.returncode != 0:
        log_path = root / ("g1next-stderr-%s.log" % phase)
        log_path.write_text(completed.stderr, encoding="utf-8", errors="replace")
        last = [line for line in completed.stderr.strip().splitlines() if line.strip()]
        print("  %s exited %d; full stderr: %s" % (phase, completed.returncode, log_path))
        if last:
            print("  %s last stderr line: %s" % (phase, last[-1][:400]))
        raise SystemExit("%s exited %d" % (phase, completed.returncode))
    report_path = root / ("g1next-phase-%s.json" % phase)
    if not report_path.exists():
        raise SystemExit("%s printed no phase report" % phase)
    return json.loads(report_path.read_text(encoding="utf-8"))


def _evaluate_next_g1(p1: Mapping[str, Any], p2: Mapping[str, Any]) -> dict[str, Any]:
    """下一次限定采用的判据：只认 A/actA 锚和这条链自己的证据。"""

    a = _bare(p1.get("artifact_a"))
    act = str(p1.get("activity_ref") or "")
    cont = dict((p2.get("rounds") or {}).get("g1next:p2:continue-1") or {})
    versions_start = int(p2.get("versions_at_start") or 0)
    versions_after = int(p2.get("versions_after_continue_1") or 0)
    criteria = {
        "a_anchor_first_owner_write": bool(a)
        and p1.get("first_outcome") == "ACTED"
        and bool(p1.get("first_owner_receipt"))
        and p1.get("first_owner_version") is True,
        "a_anchor_has_activity": bool(act),
        "cross_pid_restart": bool(p1.get("pid")) and p1.get("pid") != p2.get("pid"),
        "same_run_identity": bool(p1.get("run_id")) and p1.get("run_id") == p2.get("run_id"),
        "no_write_between_pids": p2.get("head_at_start") == p1.get("head_at_end")
        and int(p2.get("versions_at_start") or 0) == int(p1.get("versions_before_exit") or 0)
        and p2.get("memory_at_start") == p1.get("memory_rows_at_end"),
        "budget_read_back_across_pid": bool(p1.get("budget_after_first"))
        and bool(p2.get("budget_at_start"))
        and int((p2.get("budget_at_start") or {}).get("cognition_used_today") or 0)
        == int((p1.get("budget_after_first") or {}).get("cognition_used_today") or 0)
        and int((p2.get("budget_at_start") or {}).get("spent_today") or 0)
        == int((p1.get("budget_after_first") or {}).get("spent_today") or 0),
        "paused_round_zero_request": (p2.get("paused_round") or {}).get("outcome")
        == "PAUSED_BY_OPERATOR"
        and int((p2.get("paused_round") or {}).get("llm_calls") or 0) == 0,
        "model_decision_is_continue": cont.get("outcome") == "ACTED"
        and cont.get("decision") == "CONTINUE",
        "same_artifact_a": bool(cont) and _bare(cont.get("artifact_ref")) == a,
        "same_activity_act_a": bool(cont) and str(cont.get("activity_ref") or "") == act,
        "new_action_bound": bool(cont.get("action_id"))
        and str(cont.get("action_id")) not in {"", str(p1.get("first_action_id") or "\0")},
        "new_owner_version_and_receipt": cont.get("owner_version_created") is True
        and bool(cont.get("owner_version_id"))
        and versions_after == versions_start + 1,
    }
    core = all(criteria.values())
    ancillary = _ancillary_chain(p2)
    return {
        "criteria": criteria,
        "core_covered": core,
        "ancillary": ancillary,
        "ancillary_covered": all(ancillary.values()),
        "result": "COVERED" if core else "NOT_COVERED",
        "real_g1_evidence": "NOT_COVERED",
        "reason": ("OFFLINE_SCRIPTED_STANDIN" if core
                   else "NEXT_G1_CRITERIA_NOT_MET"),
    }


def _ancillary_chain(p2: Mapping[str, Any]) -> dict[str, bool]:
    """整条离线链的旁证；缺一项就如实记 False，不拿核心通过冒充整条 G1。"""

    unknown = p2.get("unknown_round") or {}
    reconcile = p2.get("reconcile") or {}
    cas = p2.get("cas_conflict") or {}
    quiet = p2.get("no_action") or {}
    baseline = p2.get("quiet_baseline") or {}
    rounds = p2.get("rounds") or {}
    after_cas = dict(rounds.get("g1next:p2:continue-after-cas") or {})
    after_quiet = dict(rounds.get("g1next:p2:continue-after-quiet") or {})
    return {
        "unknown_stayed_unknown": unknown.get("outcome") == "STEP_UNKNOWN"
        and unknown.get("action_status") == "UNKNOWN",
        "unknown_reconciled_by_operation": reconcile.get("result") == "CONFIRMED_SUCCEEDED"
        and reconcile.get("from_status") == "UNKNOWN"
        and bool(reconcile.get("evidences"))
        and all(row.get("verified") for row in (reconcile.get("evidences") or [])),
        "cas_refused_by_owner": cas.get("action_status") == "FAILED"
        and cas.get("owner_status") == "CONFLICT"
        and cas.get("receipt_claim") == "REJECTED",
        "continued_after_cas": after_cas.get("outcome") == "ACTED"
        and after_cas.get("owner_version_created") is True,
        "no_action_zero_write": quiet.get("outcome") == "NO_ACTION"
        and p2.get("head_after_quiet") == baseline.get("head")
        and p2.get("versions_after_quiet") == baseline.get("versions")
        and p2.get("b_after_quiet") == baseline.get("b_head"),
        "continued_after_quiet": after_quiet.get("outcome") == "ACTED"
        and after_quiet.get("owner_version_created") is True,
        "b_never_written": p2.get("b_head_at_start") == p2.get("b_after_quiet"),
    }


def _evaluator_negatives(p1: Mapping[str, Any], p2: Mapping[str, Any]) -> dict[str, Any]:
    """判据反例：错作品/错 Activity/旧成功重放/没有模型选择都不能算通过。"""

    rows: dict[str, Any] = {}
    base_cont = dict((p2.get("rounds") or {}).get("g1next:p2:continue-1") or {})

    def _mutated(**changes: Any) -> dict[str, Any]:
        cont = dict(base_cont)
        cont.update(changes)
        rounds = dict(p2.get("rounds") or {})
        rounds["g1next:p2:continue-1"] = cont
        return {**p2, "rounds": rounds}

    wrong_artifact = _mutated(artifact_ref=p1.get("artifact_b"))
    wrong_activity = _mutated(activity_ref="actv:some-other-activity")
    replay = {**_mutated(owner_version_created=False, owner_version_id=None),
              "versions_after_continue_1": p2.get("versions_at_start")}
    no_choice = _mutated(outcome="NO_ACTION", decision="NO_ACTION", owner_version_created=False,
                         owner_version_id=None)
    for name, view in (("wrong_artifact", wrong_artifact),
                       ("wrong_activity", wrong_activity),
                       ("replayed_old_success", replay),
                       ("no_model_choice", no_choice)):
        rows[name] = _evaluate_next_g1(p1, view)["result"]
    rows["baseline"] = _evaluate_next_g1(p1, p2)["result"]
    return rows


class _BudgetRefused(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def _reserve_then_send(budget: Any, inner: Any) -> Any:
    """生产发送口的契约：先查/预留，成功之后才允许内层发出。坏账本在这里就停。"""

    verdict = budget.check()
    if not verdict.get("allowed"):
        raise _BudgetRefused(str(verdict.get("reason_code") or "BUDGET_REFUSED"))
    budget.reserve("decision")
    return inner.invoke(object())


def _budget_negatives(scratch: Path) -> dict[str, Any]:
    """共享 20 请求硬上限：第 21 次在发送前拒绝，内层零调用；坏账本同样零发送。"""

    import g1_real_adoption as g1

    state = scratch / "budget-cap"
    state.mkdir(parents=True, exist_ok=True)
    script = str(Path(__file__).resolve())
    first = subprocess.run([sys.executable, "-B", script, "--reserve",
                            "--state-root", str(state), "--count", "12"],
                           capture_output=True, text=True)
    second = subprocess.run([sys.executable, "-B", script, "--reserve",
                             "--state-root", str(state), "--count", "8"],
                            capture_output=True, text=True)
    budget = g1._budget(state)
    view = budget.view()
    inner_calls: list[int] = []

    class _Inner:
        def invoke(self, request: Any, *, schema_retry_index: int = 0) -> Any:
            inner_calls.append(1)
            return SimpleNamespace(failure_class=None)

    gate = g1.G1CognitionGate(_Inner(), latch=g1.SendLatch(lambda: False), budget=budget)
    refused = ""
    try:
        gate.invoke(object())
    except Exception as exc:  # noqa: BLE001 - 拒绝原因就是证据
        refused = str(getattr(exc, "reason_code", None) or type(exc).__name__)

    corrupt_state = scratch / "budget-corrupt"
    corrupt_state.mkdir(parents=True, exist_ok=True)
    from life_request_budget import budget_path

    budget_path(corrupt_state).write_text("{not-json", encoding="utf-8")
    corrupt_budget = g1._budget(corrupt_state)
    corrupt_calls: list[int] = []

    class _CorruptInner:
        def invoke(self, request: Any, *, schema_retry_index: int = 0) -> Any:
            corrupt_calls.append(1)
            return SimpleNamespace(failure_class=None)

    corrupt_refused = ""
    try:
        _reserve_then_send(corrupt_budget, _CorruptInner())
    except _BudgetRefused as exc:
        corrupt_refused = exc.reason_code

    return {
        "first_rc": first.returncode, "second_rc": second.returncode,
        "run_window_calls": view.get("run_window_calls"),
        "run_window_max": view.get("run_window_max"),
        "refused": refused, "inner_calls_after_cap": len(inner_calls),
        "corrupt_state": corrupt_budget.view().get("state"),
        "corrupt_refused": corrupt_refused,
        "inner_calls_after_corrupt": len(corrupt_calls),
    }


def _duplicate_start_negative(scratch: Path) -> dict[str, Any]:
    """同一个根上的启动窗口只能写一次；第二次必须拒绝。"""

    import g1_real_adoption as g1

    root = scratch / "duplicate-start"
    first = g1._write_window(root)
    second_reason = "accepted"
    try:
        g1._write_window(root)
    except g1.Refused as exc:
        second_reason = exc.reason_code
    return {"first_window": bool(first), "second": second_reason}


def _http_guard_negative() -> dict[str, Any]:
    """证明守卫真的会拦住普通网络连接（AF_UNIX 不走这里）。"""

    fired = ""
    sock = None
    with _http_forbidden():
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect(("127.0.0.1", 9))
        except AssertionError as exc:
            fired = str(exc)
        finally:
            if sock is not None:
                sock.close()
    return {"fired": fired}


def _offline_check() -> dict[str, Any]:
    scratch = Path(tempfile.mkdtemp(prefix=FIXTURE_PREFIX))
    fixture_root = scratch / "fixture"
    print("scratch root:", scratch)
    print("NOTE: Decision/content are scripted stand-ins; offline mechanism only, "
          "not natural adoption and not a G1 pass.")
    guard = _http_guard_negative()
    with _http_forbidden():
        from v05_restart_continuation_smoke import prepare_fixture

        prepare_fixture(root=fixture_root)
        p1 = _run_phase("p1", fixture_root, {})
        p2 = _run_phase("p2", fixture_root, {
            "artifact_a": p1.get("artifact_a") or "",
            "artifact_b": p1.get("artifact_b") or "",
            "activity_ref": p1.get("activity_ref") or "",
        })
    verdict = _evaluate_next_g1(p1, p2)
    negatives = _evaluator_negatives(p1, p2)
    budget = _budget_negatives(scratch)
    duplicate = _duplicate_start_negative(scratch)

    _check("the HTTP guard really blocks a real network connect",
           guard.get("fired") == "OFFLINE_HTTP_FORBIDDEN", guard)
    _check("the offline fixture is a fresh mkdtemp root, not a formal instance",
           str(fixture_root).startswith(str(Path(tempfile.gettempdir())))
           and not str(fixture_root).startswith("/root"), str(fixture_root))
    for name, ok in (verdict.get("criteria") or {}).items():
        _check("next-G1 criterion: %s" % name, ok, verdict)
    _check("the first real owner write anchored artifacts A and activity actA",
           verdict["criteria"].get("a_anchor_first_owner_write")
           and verdict["criteria"].get("a_anchor_has_activity"),
           {"a": p1.get("artifact_a"), "act": p1.get("activity_ref")})
    _check("the second process is a different real PID on the same run identity",
           verdict["criteria"].get("cross_pid_restart")
           and verdict["criteria"].get("same_run_identity"),
           {"p1": p1.get("pid"), "p2": p2.get("pid"), "run": p1.get("run_id")})
    _check("the new Decision continued A on actA with a new Action and a new owner version",
           verdict["criteria"].get("same_artifact_a")
           and verdict["criteria"].get("same_activity_act_a")
           and verdict["criteria"].get("new_action_bound")
           and verdict["criteria"].get("new_owner_version_and_receipt"),
           (p2.get("rounds") or {}).get("g1next:p2:continue-1"))
    _check("the offline next-G1 core evaluator reports COVERED on the real chain",
           verdict.get("result") == "COVERED", verdict)
    _check("the offline result still refuses to claim real G1 evidence",
           verdict.get("real_g1_evidence") == "NOT_COVERED"
           and verdict.get("reason") == "OFFLINE_SCRIPTED_STANDIN", verdict)
    _check("wrong artifact (B) is NOT_COVERED",
           negatives.get("wrong_artifact") == "NOT_COVERED", negatives)
    _check("wrong Activity is NOT_COVERED",
           negatives.get("wrong_activity") == "NOT_COVERED", negatives)
    _check("replaying the old success is NOT_COVERED",
           negatives.get("replayed_old_success") == "NOT_COVERED", negatives)
    _check("no model choice is NOT_COVERED",
           negatives.get("no_model_choice") == "NOT_COVERED", negatives)
    _check("two processes share one persistent 20-request cap; the 21st send is refused "
           "with zero inner calls",
           budget.get("first_rc") == 0 and budget.get("second_rc") == 0
           and budget.get("run_window_calls") == REQUEST_CAP
           and budget.get("refused") == REASON_REQUEST_CAP
           and budget.get("inner_calls_after_cap") == 0, budget)
    _check("a corrupt ledger is refused before any send",
           budget.get("corrupt_state") == "corrupt"
           and budget.get("corrupt_refused") == "REQUEST_BUDGET_STATE_CORRUPT"
           and budget.get("inner_calls_after_corrupt") == 0, budget)
    _check("a second start window on the same root is refused",
           duplicate.get("first_window") is True
           and duplicate.get("second") == REASON_ALREADY_STARTED, duplicate)
    _check("the ancillary UNKNOWN/CAS/NO_ACTION chain ran without claiming the whole G1",
           verdict.get("ancillary_covered") is True, verdict.get("ancillary"))

    failed = [name for name, ok, _detail in CHECKS if not ok]
    print()
    print("scratch kept:", scratch)
    for name in failed:
        print("FAILED:", name)
    print("RESULT: %s" % ("FAIL" if failed else "PASS"))
    return {
        "ok": not failed,
        "scratch_root": str(scratch),
        "checks": [{"name": name, "ok": ok} for name, ok, _detail in CHECKS],
        "next_g1": verdict,
        "negatives": negatives,
        "budget": budget,
        "duplicate_start": duplicate,
        "failed": failed,
    }


# ---------------------------------------------------------------------------------------------
# CLI：离线入口之外的一切都明确拒绝
# ---------------------------------------------------------------------------------------------


def _is_scratch_path(path: Any) -> bool:
    """内部子命令只能碰本 harness 的 mkdtemp 根，绝不能指向 /root 或旧实例。"""

    if not path:
        return False
    try:
        resolved = str(Path(str(path)).resolve())
        temp_root = str(Path(tempfile.gettempdir()).resolve())
    except OSError:
        return False
    return (resolved.startswith(temp_root + os.sep)
            and FIXTURE_PREFIX.strip("-") in resolved)


def _refuse_path(reason: str, path: Any) -> int:
    payload = {"ok": False, "reason_code": reason, "path": str(path),
               "files_written": []}
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    sys.stdout.flush()
    return EXIT_REFUSED


def _refuse_real_entry(argv: Sequence[str]) -> int:
    payload = {
        "ok": False,
        "reason_code": REASON_OFFLINE_ONLY,
        "detail": ("this harness is offline-only; prepare/run/execute-approved are not "
                   "implemented here and nothing was written"),
        "argv": list(argv),
        "files_written": [],
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    sys.stdout.flush()
    return EXIT_REFUSED


def _reserve_internal(state_root: str, count: int) -> int:
    import g1_real_adoption as g1

    budget = g1._budget(Path(state_root))
    for _ in range(int(count)):
        budget.reserve("decision")
    view = budget.view()
    sys.stdout.write(json.dumps({"reserved": int(count),
                                 "run_window_calls": view.get("run_window_calls")},
                                ensure_ascii=False) + "\n")
    return EXIT_OK


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="G1 next offline harness (offline only)")
    parser.add_argument("--offline-check", action="store_true")
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--execute-approved", action="store_true")
    parser.add_argument("--phase", choices=("p1", "p2"))
    parser.add_argument("--config")
    parser.add_argument("--reserve", action="store_true")
    parser.add_argument("--state-root")
    parser.add_argument("--count", type=int, default=1)
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.phase:
        if not args.config:
            parser.error("--phase requires --config")
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))
        if not _is_scratch_path(config.get("root")):
            return _refuse_path("OFFLINE_PATH_REFUSED", config.get("root"))
        with _http_forbidden():
            if args.phase == "p1":
                phase_p1(config)
            else:
                phase_p2(config)
        return EXIT_OK
    if args.reserve:
        if not args.state_root:
            parser.error("--reserve requires --state-root")
        if not _is_scratch_path(args.state_root):
            return _refuse_path("OFFLINE_PATH_REFUSED", args.state_root)
        return _reserve_internal(args.state_root, int(args.count))
    if args.prepare or args.run or args.execute_approved:
        return _refuse_real_entry(sys.argv[1:])
    if args.offline_check or not any((args.prepare, args.run, args.execute_approved)):
        result = _offline_check()
        return EXIT_OK if result.get("ok") else EXIT_REFUSED
    return _refuse_real_entry(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())

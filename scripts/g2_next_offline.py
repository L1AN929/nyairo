#!/usr/bin/env python3
"""G2 历史影响选择 · A 路线离线准备驱动（0 产品调用、0 网络、0 正式根）。

这是**薄封装**，不是第二套仪器。它复用三件既有实现：

* ``history_choice_natural_v2`` — A 路线的只读观察控制器、真实 runtime 装配与正式结构门；
* ``history_choice_experiment`` — 影子两臂的中性文本替换、发送前可比性门、20 对固定顺序、
  阶段/总量额度门与预登记统计；
* ``life_request_budget`` — 发送前持久预留与 run-window 硬上限（正式发送口）。

命令只有 ``--offline-check``：真实 ``prepare`` / ``run`` / ``execute-approved`` 一律拒绝且不写盘；
内部不接任意已有 root（scratch 一律新 mkdtemp、保留）。

本驱动分三层如实报告：

1. 纯离线形状/统计/入口门（合成输入，快速可复跑）；
2. G2 建议的 **52** 持久总账：8 + 40 + 4 全部经过同一个 ``LifeRequestBudget.reserve``
   发送口，第 52 次允许、第 53 次在内层 transport 之前拒；v1 的 60 只留作兼容旁证；
3. **正式 runtime 离线隔离**：新 mkdtemp 里真 owner + 正式 assembly + 正式 cognition 构造，
   在真实 ``build_prompt`` 边界截获两臂最终输入；以及两臂各自独立新 root 的离线承接
   （合法 Decision → Action owner 效果），共享一本 4 格持久账本、不串臂。

真实模型冻结与真实 provider 采样不运行，如实写 ``NOT_IMPLEMENTED``；受控离线场景仍给出
``SECOND_WORK_CANDIDATE_MISSING``（H 占前台，另一件不可执行），这是仍需批准的真实现场条件，
不在离线里强造合法集。G1 仍 NOT_COVERED，本文件只准备仪器，不宣称进入 P2。
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import http.client
import json
import os
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(REPO_ROOT), str(REPO_ROOT / "components"),
               str(REPO_ROOT / "components" / "life" / "scripts"),
               str(REPO_ROOT / "components" / "native"),
               str(REPO_ROOT / "components" / "native" / "memory_runtime_v1"),
               str(REPO_ROOT / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

#: 只读复用既有仪器：不修改它们的常量、落盘格式，也不重跑 v1 的 20 对或 v2 的整批。
import history_choice_experiment as baseline  # noqa: E402
import history_choice_natural_v2 as v2  # noqa: E402

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_REFUSED = 2

PRODUCT_RUN_NOT_AUTHORIZED = "PRODUCT_RUN_NOT_AUTHORIZED"
EXISTING_ROOT_REFUSED = "EXISTING_ROOT_REFUSED"

#: G2 预登记**建议**预算（不是新授权，未支用）：采集 8 + 影子 40 + 最多承接 4 + 诊断 0 = 52。
G2_PROPOSED_BUDGET = {"acquisition": 8, "shadow": 40, "continuation": 4, "diagnostic": 0}
G2_PROPOSED_TOTAL = 52
#: G2 自己的阶段上限（只用于本驱动的离线账本模拟；不改 v1 的 STAGE_CAPS）。
G2_STAGE_CAPS = {"acquisition": 8, "shadow": 40, "continuation": 4}

#: 预登记写死的 20 对固定顺序（与 v1 同一张表；G2 不重抽、不改表）。
PREREG_PAIR_ORDER = ("BA", "AB", "AB", "AB", "AB", "BA", "AB", "BA", "AB", "AB",
                     "BA", "BA", "BA", "AB", "AB", "BA", "BA", "AB", "BA", "BA")

#: 本驱动真实跑不动的层：如实列出，不当通过。
NOT_IMPLEMENTED = {
    "real_model_freeze": "真实模型冻结要真实 provider 与正式根；本驱动用夹具 transport/虚拟时钟。",
    "live_provider_sampling": ("两臂输入隔离与两臂承接已对真实 runtime/owner 离线跑通；"
                              "真实模型一次也没调用，真实 provider 采样未做。"),
    "legal_two_work_scenario_offline": ("受控离线场景仍为 SECOND_WORK_CANDIDATE_MISSING"
                                       "（H 活动占前台，另一件作品不可执行）；可比较场景要"
                                       "批准的真实运行，离线不强造合法集。"),
    "real_prepare_run": "prepare/run/execute-approved 一律拒绝，零写盘。",
}

REAL_GATE_STATUS = "G1_NOT_COVERED; G2_INSTRUMENT_ONLY"


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

class _Keep:
    """用完**不删**的临时目录（本项目 scratch 规矩：全部保留，不清理）。"""

    def __enter__(self) -> Path:
        self.path = Path(tempfile.mkdtemp(prefix="g2-next-offline-"))
        return self.path

    def __exit__(self, *_exc) -> bool:
        return False


def refuse_existing_root(root: Any) -> dict[str, Any]:
    """内部入口守卫：任何「已有 root」都不接。返回拒绝或空 dict（可新建）。"""

    path = Path(str(root))
    if path.exists():
        return {"ok": False, "reason_code": EXISTING_ROOT_REFUSED, "root": str(path),
                "detail": "G2 离线准备只使用新 mkdtemp，不接任意已有 root。"}
    return {}


def product_run(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    """真实 prepare/run/execute-approved：一律拒绝，不写任何文件。"""

    return {"ok": False, "reason_code": PRODUCT_RUN_NOT_AUTHORIZED,
            "detail": ("G2 目前只有离线准备；真实 prepare/run/execute-approved 需本人直接批准"
                       "新预算与运行授权，本入口不写盘。"),
            "requests_sent": 0, "model_called": 0, "files_written": 0,
            "proposed_budget": dict(G2_PROPOSED_BUDGET)}


# ---------------------------------------------------------------------------
# 1. A 控制器真实接缝（复用 natural_v2 的两段演示）
# ---------------------------------------------------------------------------

def natural_controller_check() -> dict[str, Any]:
    """QUIET 不强问、纯读观察不产生请求、H＋双合法候选门拒绝不成立场景。

    决策/内容 transport 与时钟是替身；runtime、runner、owner、writer、M0 准入、结构门是真的。
    观察器零写由 v2 既有 ObserverTest 证明，本驱动不重跑那套旧 suite。
    """

    demo = v2.offline_live_seam_demo()
    quiet = demo.get("quiet") or {}
    h_phase = demo.get("h_phase") or {}
    q_checks = quiet.get("checks") or {}
    h_checks = h_phase.get("checks") or {}
    summary = h_phase.get("summary") or {}
    structural = list(h_phase.get("structural") or [])
    gate_ok = bool(structural and structural[0].get("ok"))
    gate_reason = str((structural[0] or {}).get("reason_code") or "") if structural else ""
    named_rejection = gate_reason in (baseline.REASON_SECOND_WORK_MISSING,
                                      baseline.REASON_SCENARIO_NOT_IDENTIFIABLE)
    result = str(summary.get("result") or "")
    checks = {
        "demo_ok": bool(demo.get("ok")),
        "no_network": bool((demo.get("checks") or {}).get("no_network")),
        "quiet_after_first_request": bool(q_checks.get("quiet_after_first_request")),
        "quiet_within_cap": bool(q_checks.get("requests_within_cap")),
        "h_established": bool(h_checks.get("h_established")),
        "structural_gate_evaluated": bool(h_checks.get("structural_gate_evaluated")),
        "gate_used_offline_transport": bool(h_checks.get("gate_used_the_offline_transport")),
        "named_outcome": bool(h_checks.get("outcome_is_named")),
        # 要么冻结了可比场景，要么对不成立场景给出具名拒绝；绝不静默放行。
        "gate_freezes_or_namedly_rejects": gate_ok or named_rejection,
        "result_is_named": result in (v2.RESULT_FROZEN, v2.RESULT_NOT_IDENTIFIABLE),
    }
    blocking = [key for key, value in checks.items() if not value]
    return {"ok": not blocking, "blocking": blocking, "checks": checks,
            "outcome": result or None,
            "gate": {"ok": gate_ok, "reason_code": gate_reason or None},
            "requests_used": summary.get("requests_used"),
            "observations": summary.get("observations"),
            "scratch_parent": demo.get("scratch_parent"),
            "note": demo.get("note")}


# ---------------------------------------------------------------------------
# 2. 影子构造/解析（合成输入的快速形状门，保留为薄层）
# ---------------------------------------------------------------------------

def _arm_experiences() -> list[dict[str, str]]:
    return [
        {"artifact_ref": "artifact:artifact:h1", "version_ref": "artifactversion:h1v2",
         "text": "这是一段真实的经历正文，用来占住两行。\n第二行里的内容也只用于长度检查。"},
        {"artifact_ref": "artifact:artifact:o1", "version_ref": "artifactversion:o1v1",
         "text": "另一件作品的一段经历正文，长度也不同。"},
    ]


def _payload(experiences: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "prior_experiences": [dict(row) for row in experiences],
        "candidates": [
            {"candidate_id": "cand:h", "target_refs": ["artifact:artifact:h1"],
             "legal_decisions": ["START", "NO_ACTION"]},
            {"candidate_id": "cand:o", "target_refs": ["artifact:artifact:o1"],
             "legal_decisions": ["START", "NO_ACTION"]},
        ],
        "observed_facts": {"body": "ALERT", "where": "home"},
        "capacity_summary": {"overall_capacity_state": "AVAILABLE"},
    }


def _rendered(payload: Mapping[str, Any]) -> str:
    return json.dumps(dict(payload), ensure_ascii=False, sort_keys=True)


def _raises_second_variable(recorder: Callable[[Mapping[str, Any], str], None],
                            payload: Mapping[str, Any]) -> bool:
    try:
        recorder(payload, _rendered(payload))
    except baseline.HistoryGateError as exc:
        return exc.reason_code == baseline.REASON_SECOND_VARIABLE
    except Exception:
        return False
    return False


def shadow_construction_check() -> dict[str, Any]:
    """复用 v1 的中性替换、指纹、形状门与固定顺序；不调用模型，不建 runtime。

    这是**合成输入**的快速形状门；正式 runtime 的最终输入隔离见
    :func:`formal_input_isolation_check`。两者不互相替代。
    """

    arm_a = _arm_experiences()
    arm_b = baseline.neutralise_history(arm_a)
    payload_a = _payload(arm_a)
    payload_b = _payload(arm_b)

    refs_and_lengths_kept = all(
        (a.get("artifact_ref") == b.get("artifact_ref"))
        and (a.get("version_ref") == b.get("version_ref"))
        and (len(str(a.get("text") or "")) == len(str(b.get("text") or "")))
        for a, b in zip(arm_a, arm_b))
    text_really_changed = all(
        str(a.get("text") or "") != str(b.get("text") or "")
        for a, b in zip(arm_a, arm_b))
    only_text_differs = (baseline.prompt_identity(payload_a)
                         == baseline.prompt_identity(payload_b))
    text_identity_equal = (
        baseline.prompt_text_identity(_rendered(payload_a), arm_a)
        == baseline.prompt_text_identity(_rendered(payload_b), arm_b))

    frozen_baseline = {
        "fingerprint": baseline.prompt_identity(payload_a),
        "text_fingerprint": baseline.prompt_text_identity(_rendered(payload_a), arm_a),
        "history_shape": baseline.payload_history_shape(payload_a),
    }
    rec_a = baseline.make_history_recorder(frozen_baseline, arm_a)
    rec_b = baseline.make_history_recorder(frozen_baseline, arm_b)

    accepted = {"a": True, "b": True}
    for label, recorder, payload in (("a", rec_a, payload_a), ("b", rec_b, payload_b)):
        try:
            recorder(payload, _rendered(payload))
        except Exception:
            accepted[label] = False

    changed_candidate = copy.deepcopy(payload_a)
    changed_candidate["candidates"][0]["legal_decisions"] = ["NO_ACTION"]
    trimmed = copy.deepcopy(payload_a)
    trimmed["prior_experiences"] = trimmed["prior_experiences"][:1]
    reshaped = copy.deepcopy(payload_a)
    reshaped["prior_experiences"][0]["version_ref"] = "artifactversion:h1v3"

    order = tuple(str(item) for item in baseline.PAIR_ORDER)
    counts = {"AB": order.count("AB"), "BA": order.count("BA")}
    order_ok = (order == PREREG_PAIR_ORDER and len(order) == baseline.PAIR_COUNT
                and counts == {"AB": 10, "BA": 10})

    checks = {
        "neutral_keeps_refs_and_text_length": refs_and_lengths_kept,
        "neutral_text_really_differs": text_really_changed,
        "only_history_text_changes_between_arms": only_text_differs and text_identity_equal,
        "both_arms_accepted_before_send": accepted["a"] and accepted["b"],
        "changed_candidate_rejected_before_send": _raises_second_variable(rec_a, changed_candidate),
        "trimmed_history_rejected_before_send": _raises_second_variable(rec_a, trimmed),
        "reshaped_history_rejected_before_send": _raises_second_variable(rec_a, reshaped),
        "pair_order_matches_prereg": order_ok,
    }
    blocking = [key for key, value in checks.items() if not value]
    return {"ok": not blocking, "blocking": blocking, "checks": checks,
            "pair_order": list(order), "pair_counts": counts}


# ---------------------------------------------------------------------------
# 3. v1 额度门（兼容旁证：阶段 40 / 总量 60 / 单臂 2）
# ---------------------------------------------------------------------------

def budget_gate_check() -> dict[str, Any]:
    """复用 v1 BatchLedger 的阶段/总量门与 v1 arm_ceiling 的单臂门，全部离线。

    这是 **v1 的 60 口径**，只作兼容旁证；G2 的 52 实际硬限见
    :func:`g2_budget_gate_check`。
    """

    from life_request_budget import LifeRequestBudget, RequestBudgetError, REASON_RUN_WINDOW_LIMIT

    checks: dict[str, Any] = {}
    with _Keep() as parent:
        # 阶段上限：影子阶段 40；第 41 次在 assert_room 处被具名拒绝。
        stage_ledger = baseline.BatchLedger(parent / "stage")
        stage_ledger.start_stage("shadow", {"run_window_calls": 0})
        stage_allowed = stage_ledger.assert_room(
            "shadow", {"run_window_calls": 39}, reserving=1)
        stage_refused = False
        try:
            stage_ledger.assert_room("shadow", {"run_window_calls": 40}, reserving=1)
        except baseline.BatchRefused as exc:
            stage_refused = exc.reason_code == baseline.REASON_STAGE_CAP
        checks["stage_cap_allows_40th"] = bool(stage_allowed)
        checks["stage_cap_refuses_41st"] = stage_refused

        # 总量上限：整批 60；阶段还有位置时，总量已满也必须拒绝。
        total_ledger = baseline.BatchLedger(parent / "total")
        total_ledger.start_stage("shadow", {"run_window_calls": 40})
        total_allowed = total_ledger.assert_room(
            "shadow", {"run_window_calls": 59}, reserving=1)
        total_refused = False
        try:
            total_ledger.assert_room("shadow", {"run_window_calls": 60}, reserving=1)
        except baseline.BatchRefused as exc:
            total_refused = exc.reason_code == baseline.REASON_TOTAL_CAP
        checks["total_cap_allows_60th"] = bool(total_allowed)
        checks["total_cap_refuses_61st"] = total_refused

        # 单臂上限：arm_ceiling 把同一本持久账本的 run_window_max 临时压到「起点 + 2」。
        arm_budget = LifeRequestBudget(parent / "arm", utc_day_max=300,
                                       run_window_max=G2_PROPOSED_TOTAL, run_id="run:g2-arm")
        arm_budget.ensure_window(allow_new=True)
        with baseline.arm_ceiling(arm_budget, baseline.ARM_REQUEST_CAP) as info:
            arm_budget.reserve("decision")
            arm_budget.reserve("content")
            third_refused = False
            try:
                arm_budget.reserve("decision")
            except RequestBudgetError as exc:
                third_refused = exc.reason_code == REASON_RUN_WINDOW_LIMIT
            ceiling = info.get("ceiling")
        restored = arm_budget.run_window_max == G2_PROPOSED_TOTAL
        checks["arm_cap_is_two"] = ceiling == baseline.ARM_REQUEST_CAP == 2
        checks["arm_cap_refuses_third_send"] = third_refused
        checks["arm_cap_restores_total_after"] = restored

    blocking = [key for key, value in checks.items() if not value]
    return {"ok": not blocking, "blocking": blocking, "checks": checks,
            "proposed_budget": dict(G2_PROPOSED_BUDGET),
            "note": "v1 的 60 口径，仅兼容旁证；G2 的 52 见 g2_budget_gate_check。"}


# ---------------------------------------------------------------------------
# 4. G2 的 52：同一本持久账本 + 同一个发送前 reserve 口
# ---------------------------------------------------------------------------

class _CountingSender:
    """最内层 transport 的计数替身：被调用就 +1；可模拟一次真实失败。"""

    send_split_capable = True
    backend = "g2_offline_counter"

    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.fail = bool(fail)

    def invoke(self, request: Any, *, schema_retry_index: int = 0) -> dict[str, Any]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("OFFLINE_TRANSPORT_FAILURE")
        return {"ok": True, "request": request}


@contextlib.contextmanager
def _g2_stage_ceiling(budget: Any, cap: int) -> Any:
    """把本阶段上限落到同一本持久账本上（G2 自己的 8/40/4，不改 v1 常量）。"""

    start = int(budget.view()["run_window_calls"])
    previous = budget.run_window_max
    budget.run_window_max = min(int(previous), start + int(cap))
    try:
        yield {"start": start, "ceiling": int(budget.run_window_max)}
    finally:
        budget.run_window_max = previous


def _send_through_adapter(budget: Any, sender: Any) -> dict[str, Any]:
    """走正式 ``BudgetedCognitionAdapter``：先 reserve，再调内层 transport。"""

    from life_request_budget import BudgetedCognitionAdapter

    adapter = BudgetedCognitionAdapter(sender, budget)
    try:
        adapter.invoke(object())
        return {"sent": True, "calls": sender.calls}
    except Exception as exc:
        return {"sent": False, "calls": sender.calls,
                "reason_code": getattr(exc, "reason_code", type(exc).__name__)}


def g2_budget_gate_check() -> dict[str, Any]:
    """G2 建议 52 的实际硬限：8 + 40 + 4 共一本持久账本、一个共享 reserve 口。

    - 第 52 次允许；第 53 次在 ``LifeRequestBudget.reserve`` 处拒绝，内层 transport 未被调用；
    - 阶段未用额度不可挪（acquisition 只用 6 时，后面最多到 50 而非 52）；
    - 每臂 2，第三次发送被拒；失败预留不退。
    只做必要最小：全部经正式发送口，不用单独求和冒充。
    """

    from life_request_budget import LifeRequestBudget, REASON_RUN_WINDOW_LIMIT

    checks: dict[str, Any] = {}
    with _Keep() as parent:
        budget = LifeRequestBudget(parent / "g2-52", utc_day_max=300,
                                   run_window_max=G2_PROPOSED_TOTAL,
                                   run_id="run:g2-52-offline")
        budget.ensure_window(allow_new=True)

        # 采集 8：最后一次内层失败，但预留已持久化，不退款。
        acquisition_sends: list[dict[str, Any]] = []
        with _g2_stage_ceiling(budget, G2_STAGE_CAPS["acquisition"]) as acq:
            for index in range(8):
                acquisition_sends.append(
                    _send_through_adapter(budget, _CountingSender(fail=(index == 7))))
            ninth_sender = _CountingSender()
            ninth = _send_through_adapter(budget, ninth_sender)
        checks["acquisition_uses_all_8_slots"] = (
            len(acquisition_sends) == 8
            and sum(1 for row in acquisition_sends if row["sent"]) == 7
            and int(budget.view()["run_window_calls"]) == 8)
        checks["failed_send_keeps_its_reservation"] = (
            acquisition_sends[-1]["sent"] is False and acq["ceiling"] == 8)
        checks["acquisition_refuses_9th_before_transport"] = (
            ninth["sent"] is False and ninth_sender.calls == 0
            and ninth["reason_code"] == REASON_RUN_WINDOW_LIMIT)

        # 影子 40。
        with _g2_stage_ceiling(budget, G2_STAGE_CAPS["shadow"]) as shadow:
            shadow_sender = _CountingSender()
            shadow_sent = sum(
                1 for _ in range(40)
                if _send_through_adapter(budget, shadow_sender)["sent"])
            extra_sender = _CountingSender()
            extra = _send_through_adapter(budget, extra_sender)
        checks["shadow_uses_all_40_slots"] = shadow_sent == 40 and shadow["ceiling"] == 48
        checks["shadow_refuses_41st_before_transport"] = (
            extra["sent"] is False and extra_sender.calls == 0
            and extra["reason_code"] == REASON_RUN_WINDOW_LIMIT)

        # 承接 4：两臂各 2，第三次在预留处拒。
        with _g2_stage_ceiling(budget, G2_STAGE_CAPS["continuation"]) as cont:
            arms: dict[str, Any] = {}
            for arm in ("A", "B"):
                with baseline.arm_ceiling(budget, baseline.ARM_REQUEST_CAP) as limit:
                    arm_sender = _CountingSender()
                    arm_sent = sum(
                        1 for _ in range(2)
                        if _send_through_adapter(budget, arm_sender)["sent"])
                    third_sender = _CountingSender()
                    third = _send_through_adapter(budget, third_sender)
                arms[arm] = {"sent": arm_sent, "ceiling": limit["ceiling"],
                             "third_refused": (third["sent"] is False
                                               and third_sender.calls == 0
                                               and third["reason_code"] == REASON_RUN_WINDOW_LIMIT)}
        checks["continuation_each_arm_uses_2"] = all(row["sent"] == 2 for row in arms.values())
        checks["continuation_each_arm_refuses_3rd"] = all(
            row["third_refused"] for row in arms.values())
        checks["continuation_stage_ceiling_is_52"] = cont["ceiling"] == G2_PROPOSED_TOTAL

        # 共享账本到 52；第 53 次必须在 reserve 处拒，内层 transport 不被调用。
        view = budget.view()
        checks["shared_ledger_holds_exactly_52"] = (
            int(view["run_window_calls"]) == G2_PROPOSED_TOTAL)
        checks["persistent_ceiling_is_52"] = int(budget.run_window_max) == G2_PROPOSED_TOTAL
        fifty_third_sender = _CountingSender()
        fifty_third = _send_through_adapter(budget, fifty_third_sender)
        checks["fifty_third_refused_before_transport"] = (
            fifty_third["sent"] is False and fifty_third_sender.calls == 0
            and fifty_third["reason_code"] == REASON_RUN_WINDOW_LIMIT)
        checks["no_refund_api_exists"] = not any(
            hasattr(budget, name) for name in ("release", "refund", "give_back"))

        # 阶段未用不可挪：另一本账本 acquisition 只用 6，后面最多到 50。
        unused = LifeRequestBudget(parent / "g2-52-unused", utc_day_max=300,
                                   run_window_max=G2_PROPOSED_TOTAL,
                                   run_id="run:g2-52-unused")
        unused.ensure_window(allow_new=True)
        with _g2_stage_ceiling(unused, G2_STAGE_CAPS["acquisition"]):
            for _ in range(6):
                _send_through_adapter(unused, _CountingSender())
        with _g2_stage_ceiling(unused, G2_STAGE_CAPS["shadow"]) as unused_shadow:
            unused_shadow_ceiling = unused_shadow["ceiling"]
        with _g2_stage_ceiling(unused, G2_STAGE_CAPS["continuation"]) as unused_cont:
            unused_cont_ceiling = unused_cont["ceiling"]
        # 每个阶段从「当前实际已用」起算，所以少用的 2 格采集额度不会挪给后面；
        # 整批最多 6 + 40 + 4 = 50，到不了建议的 52。
        unused_max_total = (6 + G2_STAGE_CAPS["shadow"]
                            + G2_STAGE_CAPS["continuation"])
        checks["unused_stage_slots_not_transferred"] = (
            unused_shadow_ceiling == 6 + G2_STAGE_CAPS["shadow"]
            and unused_cont_ceiling == 6 + G2_STAGE_CAPS["continuation"]
            and unused_max_total < G2_PROPOSED_TOTAL)

        ledger_view = budget.view()
    blocking = [key for key, value in checks.items() if not value]
    return {"ok": not blocking, "blocking": blocking, "checks": checks,
            "ledger_calls": int(ledger_view.get("run_window_calls") or 0),
            "ledger_cap": int(ledger_view.get("run_window_max") or 0),
            "ledger_state": ledger_view.get("state"),
            "stage_caps": dict(G2_STAGE_CAPS),
            "note": ("52 是 G2 建议、非授权。全部发送都经同一个 LifeRequestBudget.reserve；"
                     "v1 的 60 只作兼容旁证。")}


def proposed_budget_check() -> dict[str, Any]:
    """只核**建议**预算的算术与来源，不支用、不写持久账本。"""

    total = sum(int(value) for value in G2_PROPOSED_BUDGET.values())
    checks = {
        "acquisition_is_8": G2_PROPOSED_BUDGET["acquisition"] == 8,
        "shadow_is_20_pairs_x2": (G2_PROPOSED_BUDGET["shadow"]
                                  == baseline.PAIR_COUNT * 2 == 40),
        "continuation_at_most_4": G2_PROPOSED_BUDGET["continuation"] == 4,
        "diagnostic_zero": G2_PROPOSED_BUDGET["diagnostic"] == 0,
        "total_is_52": total == G2_PROPOSED_TOTAL,
        "acquisition_matches_v2_plan": (
            G2_PROPOSED_BUDGET["acquisition"] == int(v2.DEFAULT_PLAN.request_cap)),
        "v2_plan_future_total_matches": (
            int(v2.DEFAULT_PLAN.future_candidate_total) == G2_PROPOSED_TOTAL),
        "v2_plan_future_continuation_matches": (
            int(v2.DEFAULT_PLAN.future_continuation_requests)
            == G2_PROPOSED_BUDGET["continuation"]),
    }
    blocking = [key for key, value in checks.items() if not value]
    return {"ok": not blocking, "blocking": blocking, "checks": checks,
            "proposed_total": total,
            "note": ("建议值非授权、未支用。v1 阶段表把 continuation 记 8；G2 建议收紧到 4，"
                     "未来若批准真实运行，prepare 必须显式按本条设置，不复用 v1 的 8。"),
            "v1_continuation_cap": baseline.STAGE_CAPS.get("continuation")}


# ---------------------------------------------------------------------------
# 5. 统计：明确合成选择对，核阈值与不完整出口
# ---------------------------------------------------------------------------

def _samples(pair_classes: Sequence[tuple[str, str]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, (a_class, b_class) in enumerate(pair_classes, start=1):
        rows.append({"pair": index, "arm": "A", "classification": a_class})
        rows.append({"pair": index, "arm": "B", "classification": b_class})
    return rows


def _conclusion(stats: Mapping[str, Any]) -> str:
    """把 v1 的预登记结论翻成 G2 的三态出口：检出 / 未检出 / 未完整检验。"""

    raw = str(stats.get("conclusion") or "")
    if raw == "HISTORY_PACKAGE_CHANGED_H_CHOICE":
        return "DETECTED"
    if raw == "NO_DETECTED_DIFFERENCE":
        return "NO_DETECTED_DIFFERENCE"
    if raw == "SHADOW_NOT_RUN":
        return "SHADOW_NOT_RUN"
    return "NOT_TESTED_INCOMPLETE"


def _stats(pair_classes: Sequence[tuple[str, str]], *, stop_reason: str = "") -> dict[str, Any]:
    return baseline.shadow_statistics(
        {"samples": _samples(pair_classes), "stop_reason": stop_reason}, {})


def statistics_check() -> dict[str, Any]:
    """用**明确合成**的选择对验证 v1 预登记统计的阈值与不完全出口；不是模型结果。"""

    H, N = baseline.CLASS_H, baseline.CLASS_NO_ACTION
    strong_positive = _conclusion(_stats([(H, N)] * 18 + [(N, N)] * 2))
    strong_negative = _conclusion(_stats([(N, H)] * 18 + [(N, N)] * 2))
    null_result = _conclusion(_stats([(H, H)] * 10 + [(N, N)] * 10))
    # 边界：6 个 A-only 不一致 -> McNemar p=0.03125、差 0.30，刚好检出。
    edge_detected = _conclusion(_stats([(H, N)] * 6 + [(N, N)] * 14))
    # 边界：5 个 -> p=0.0625、差 0.25，未达预登记阈值。
    edge_not_detected = _conclusion(_stats([(H, N)] * 5 + [(N, N)] * 15))
    incomplete = _conclusion(_stats([(H, N)] * 19))
    stopped = _conclusion(_stats([(H, N)] * 20, stop_reason="PROVIDER_OR_TRANSPORT_FAILURE"))
    boundary_stats = _stats([(H, N)] * 6 + [(N, N)] * 14)

    checks = {
        "strong_positive_detected": strong_positive == "DETECTED",
        "strong_negative_detected": strong_negative == "DETECTED",
        "direction_is_reported": (
            _stats([(N, H)] * 18 + [(N, N)] * 2)["delta"]["delta_count"] == -18),
        "null_is_no_detected_difference": null_result == "NO_DETECTED_DIFFERENCE",
        "boundary_6_of_20_detected": edge_detected == "DETECTED",
        "boundary_5_of_20_not_detected": edge_not_detected == "NO_DETECTED_DIFFERENCE",
        "boundary_p_below_005": float(boundary_stats["mcnemar_two_sided_p"]) < 0.05,
        "incomplete_is_not_tested": incomplete == "NOT_TESTED_INCOMPLETE",
        "stopped_is_not_tested": stopped == "NOT_TESTED_INCOMPLETE",
    }
    blocking = [key for key, value in checks.items() if not value]
    return {"ok": not blocking, "blocking": blocking, "checks": checks,
            "note": "明确合成选择对，只核阈值与出口；不证明历史改变真实模型选择。",
            "boundary": {"p": boundary_stats["mcnemar_two_sided_p"],
                         "delta": boundary_stats["delta"]}}


# ---------------------------------------------------------------------------
# 6. 正式 runtime 的离线输入隔离：真 owner/assembly/cognition 的两臂最终输入
# ---------------------------------------------------------------------------

class _CaptureAdapter:
    """正式 cognition adapter 的薄包装：只在真实 ``build_prompt`` 边界截获最终输入。"""

    def __init__(self, inner: Any, recorded: dict[str, Any]) -> None:
        self.inner = inner
        self.recorded = recorded

    @property
    def send_split_capable(self) -> bool:
        return bool(getattr(self.inner, "send_split_capable", False))

    def build_prompt(self, request: Any) -> tuple[str, dict[str, Any]]:
        return self.inner.build_prompt(request)

    def invoke(self, request: Any, *, schema_retry_index: int = 0) -> Any:
        text, payload = self.inner.build_prompt(request)
        self.recorded["request"] = request
        self.recorded["payload"] = payload
        self.recorded["text"] = text
        try:
            self.recorded["decision_cap"] = int(self.inner.decision_output_cap(request))
        except Exception:
            self.recorded["decision_cap"] = None
        return self.inner.invoke(request, schema_retry_index=schema_retry_index)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


class _StaticPromptInner:
    """发送前门的计数内层：只回放给定的最终输入，被调用即 +1。"""

    send_split_capable = True

    def __init__(self, text: str, payload: Mapping[str, Any]) -> None:
        self.text = text
        self.payload = dict(payload)
        self.calls = 0

    def build_prompt(self, request: Any) -> tuple[str, dict[str, Any]]:
        return self.text, dict(self.payload)

    def invoke(self, request: Any, *, schema_retry_index: int = 0) -> dict[str, Any]:
        self.calls += 1
        return {"ok": True}


def _capture_arm_final_input(context: Mapping[str, Any], frozen: Mapping[str, Any],
                             experiences: Sequence[Mapping[str, Any]], label: str,
                             trigger_index: int) -> dict[str, Any]:
    """在正式 adapter 的真实发送边界截获一臂的最终 payload/text；transport 离线。"""

    recorded: dict[str, Any] = {}
    transport = baseline._offline_no_action_transport()
    inner = baseline.raw_cognition_adapter(dict(context["bundle"]["assembled_env"]),
                                           transport=transport,
                                           admission=context["bundle"]["admission"])
    adapter = _CaptureAdapter(inner, recorded)
    sample_root = (Path(context["root"]) / baseline.EXPERIMENT_DIRNAME
                   / ("isolation-%s" % label))
    service = baseline._make_shadow_service(context["bundle"]["runtime"], sample_root, adapter)
    opportunity = baseline.new_opportunity(
        context["bundle"]["runtime"], frozen,
        trigger_ref="g2:isolation:%s:%02d" % (label, trigger_index))
    try:
        result = service.evaluate_opportunity(
            opportunity=opportunity, **baseline.shadow_kwargs(frozen, experiences))
    except Exception as exc:
        return {"ok": False, "reason_code": "FORMAL_INPUT_CAPTURE_FAILED",
                "detail": "%s: %s" % (type(exc).__name__, str(exc)[:160]),
                "transport_calls": len(transport.calls)}
    if not recorded.get("payload") or not recorded.get("text"):
        return {"ok": False, "reason_code": "FORMAL_INPUT_SEAM_UNAVAILABLE",
                "detail": "formal runtime built no final cognition request",
                "capture_status": (result or {}).get("status"),
                "transport_calls": len(transport.calls)}
    return {"ok": True, "payload": recorded["payload"], "text": recorded["text"],
            "request": recorded["request"],
            "decision_cap": recorded.get("decision_cap"),
            "capture_status": (result or {}).get("status"),
            "transport_calls": len(transport.calls)}


def _gate_decision(payload: Mapping[str, Any], text: str,
                   recorder: Callable[[Mapping[str, Any], str], None]) -> dict[str, Any]:
    """把给定最终输入送进正式发送前门；返回是否被拒、内层是否被调用。"""

    inner = _StaticPromptInner(text, payload)
    gate = baseline.HistoryGateCognitionAdapter(
        inner, stage="g2-isolation", ledger=None, budget=None, recorder=recorder, latch=None)
    try:
        gate.invoke(object())
    except baseline.HistoryGateError as exc:
        return {"refused": True, "reason_code": exc.reason_code, "inner_calls": inner.calls}
    except Exception as exc:
        return {"refused": False, "reason_code": type(exc).__name__, "inner_calls": inner.calls}
    return {"refused": False, "reason_code": "", "inner_calls": inner.calls}


def _rejected_as_second_variable(payload: Mapping[str, Any], text: str,
                                 recorder: Callable[[Mapping[str, Any], str], None]) -> bool:
    decision = _gate_decision(payload, text, recorder)
    return (decision["refused"] and decision["inner_calls"] == 0
            and decision["reason_code"] == baseline.REASON_SECOND_VARIABLE)


def formal_input_isolation_check() -> dict[str, Any]:
    """正式 runtime 两臂最终输入的离线隔离（受控场景；不冒自然可达）。

    新 mkdtemp 里真 owner + 正式 assembly/cognition 构造；脚本 Decision/内容只用于把
    H 建起来，之后两臂的最终输入由正式 ``build_prompt`` 在真实 transport 边界截获。
    只改历史 text；候选/合法集/ref/时间/预算/模板/模型输出 cap 必须一致；改第二变量或
    裁历史必须在**内层发送前**拒。
    """

    scratch_parent = Path(tempfile.mkdtemp(prefix="g2-next-isolation-"))
    plan = v2.DEFAULT_PLAN
    context = v2._open_scratch(scratch_parent, "instance-isolation", plan,
                               decision_transport=v2.ScriptedStartTransport(),
                               content_transport=v2.ScriptedAppendTransport())
    if not context.get("ok"):
        return {"ok": False, "reason_code": context.get("reason_code") or "SCRATCH_SETUP_FAILED",
                "detail": context.get("detail"), "scratch_parent": str(scratch_parent)}
    try:
        virtual = v2.VirtualTime()
        record = v2.ExperimentRecord(scratch_parent / plan.experiment_dirname, plan=plan)
        record.begin_batch(note="g2 offline: formal input isolation")
        summary = v2._controller_for(context, replace(plan, max_observations=3),
                                     record=record, virtual=virtual).run()
        h_ref = summary.get("h_ref")
        if not h_ref:
            return {"ok": False, "reason_code": "H_NOT_ESTABLISHED",
                    "detail": "the controlled offline run did not establish H",
                    "summary": {key: summary.get(key) for key in
                                ("result", "stop_reason", "requests_used")},
                    "scratch_parent": str(scratch_parent)}
        work_refs = ["artifact:%s" % row["artifact_id"]
                     for row in context["prepared"]["works"]]
        frozen_attempt = v2.freeze_and_check(context["bundle"], h_ref=h_ref,
                                             work_refs=work_refs, sleep=virtual.sleep)
        frozen = frozen_attempt.get("frozen")
        if not frozen:
            return {"ok": False, "reason_code": frozen_attempt.get("reason_code") or "FREEZE_FAILED",
                    "detail": frozen_attempt.get("detail"),
                    "scratch_parent": str(scratch_parent)}
        arm_a = [dict(item) for item in frozen["prior_experiences"]]
        arm_b = baseline.neutralise_history(arm_a)
        cap_a = _capture_arm_final_input(context, frozen, arm_a, "a", 0)
        cap_b = _capture_arm_final_input(context, frozen, arm_b, "b", 1)
        if not (cap_a.get("ok") and cap_b.get("ok")):
            return {"ok": False, "reason_code": "FORMAL_INPUT_SEAM_UNAVAILABLE",
                    "detail": {"a": cap_a.get("detail"), "b": cap_b.get("detail")},
                    "scratch_parent": str(scratch_parent)}

        payload_a, text_a = cap_a["payload"], cap_a["text"]
        payload_b, text_b = cap_b["payload"], cap_b["text"]
        identity_ok = (baseline.prompt_identity(payload_a)
                       == baseline.prompt_identity(payload_b))
        text_identity_ok = (baseline.prompt_text_identity(text_a, arm_a)
                            == baseline.prompt_text_identity(text_b, arm_b))
        shapes_ok = (baseline.payload_history_shape(payload_a)
                     == baseline.payload_history_shape(payload_b))
        refs_kept = (len(arm_a) == len(arm_b) and bool(arm_a) and all(
            a.get("artifact_ref") == b.get("artifact_ref")
            and a.get("version_ref") == b.get("version_ref")
            for a, b in zip(arm_a, arm_b)))
        lengths_ok = all(len(str(a.get("text") or "")) == len(str(b.get("text") or ""))
                         for a, b in zip(arm_a, arm_b))
        texts_differ = all(str(a.get("text") or "") != str(b.get("text") or "")
                           for a, b in zip(arm_a, arm_b))
        candidates_ok = (list(getattr(cap_a["request"], "candidates", ()) or ())
                         == list(getattr(cap_b["request"], "candidates", ()) or ()))
        legal_ok = (list(getattr(cap_a["request"], "legal_decision_enums", ()) or ())
                    == list(getattr(cap_b["request"], "legal_decision_enums", ()) or ()))
        caps_ok = (cap_a.get("decision_cap") is not None
                   and cap_a.get("decision_cap") == cap_b.get("decision_cap"))

        frozen_baseline = {
            "fingerprint": baseline.prompt_identity(payload_a),
            "text_fingerprint": baseline.prompt_text_identity(text_a, arm_a),
            "history_shape": baseline.payload_history_shape(payload_a),
        }
        rec_a = baseline.make_history_recorder(frozen_baseline, arm_a)
        rec_b = baseline.make_history_recorder(frozen_baseline, arm_b)
        arm_a_gate = _gate_decision(payload_a, text_a, rec_a)
        arm_b_gate = _gate_decision(payload_b, text_b, rec_b)

        changed_candidate = copy.deepcopy(payload_a)
        changed_candidate["candidates"][0]["legal_decisions"] = ["NO_ACTION"]
        trimmed = copy.deepcopy(payload_a)
        # 「被裁剪」包括整行消失与正文变短：两者都会改变历史形状（refs + 字符数）。
        if trimmed["prior_experiences"]:
            first = trimmed["prior_experiences"][0]
            first["text"] = str(first.get("text") or "")[:-1]
        else:
            trimmed["prior_experiences"] = []
        reshaped = copy.deepcopy(payload_a)
        reshaped["prior_experiences"][0]["version_ref"] = "artifactversion:reshaped"
        changed_facts = copy.deepcopy(payload_a)
        changed_facts["observed_fact_keys"] = ["OFFLINE_EXTRA_FACT"]

        scenario_gate = frozen_attempt.get("gate") or {}
        checks = {
            "real_owner_instance_prepared": len(context["prepared"]["works"]) == 2,
            "h_established_on_real_owner_version": bool(h_ref),
            "real_frozen_history_non_empty": bool(arm_a),
            "both_arms_built_final_request": bool(payload_a) and bool(payload_b),
            "only_history_text_differs": (identity_ok and text_identity_ok and shapes_ok
                                          and refs_kept and lengths_ok),
            "history_text_really_changes": texts_differ,
            "candidates_and_legal_sets_identical": candidates_ok and legal_ok,
            "model_output_cap_identical": caps_ok,
            "arm_a_accepted_before_send": (not arm_a_gate["refused"]
                                           and arm_a_gate["inner_calls"] == 1),
            "arm_b_accepted_before_send": (not arm_b_gate["refused"]
                                           and arm_b_gate["inner_calls"] == 1),
            "changed_candidate_rejected_before_send": _rejected_as_second_variable(
                changed_candidate, _rendered(changed_candidate), rec_a),
            "trimmed_history_rejected_before_send": _rejected_as_second_variable(
                trimmed, _rendered(trimmed), rec_a),
            "reshaped_history_rejected_before_send": _rejected_as_second_variable(
                reshaped, _rendered(reshaped), rec_a),
            "changed_facts_rejected_before_send": _rejected_as_second_variable(
                changed_facts, _rendered(changed_facts), rec_a),
        }
        blocking = [key for key, value in checks.items() if not value]
        return {
            "ok": not blocking, "blocking": blocking, "checks": checks,
            "h_ref": h_ref,
            "history_rows": len(arm_a),
            "history_chars": [len(str(row.get("text") or "")) for row in arm_a],
            "final_prompt_chars": {"a": len(text_a), "b": len(text_b)},
            "decision_caps": {"a": cap_a.get("decision_cap"), "b": cap_b.get("decision_cap")},
            "candidate_count": len(getattr(cap_a["request"], "candidates", ()) or ()),
            "scenario_gate": {key: scenario_gate.get(key) for key in
                              ("ok", "reason_code", "h_executable", "other_work_executable",
                               "no_action_legal", "offline_transport")},
            "known_offline_gap": ("受控离线场景的正式结构门仍为 %s；两臂输入隔离本身已在真实"
                                  "冻结历史上验证，但可比较双作品场景要批准的真实运行。"
                                  % (scenario_gate.get("reason_code") or "UNKNOWN")),
            "scratch_root": str(context["root"]),
            "note": ("脚本 Decision/内容只用于把 H 建起来；最终输入由正式 build_prompt 截获，"
                     "transport 离线且 HTTP 硬拒。不冒自然可达或模型会怎样选。"),
        }
    finally:
        v2._close_scratch(context)


# ---------------------------------------------------------------------------
# 7. 离线承接：两臂各自独立新 root，共享一本 4 格持久账本
# ---------------------------------------------------------------------------

def _open_arm_scratch(parent: Path, name: str, *, decision_transport: Any,
                      content_transport: Any, budget: Any, latch: Any,
                      neutralise: bool = False) -> dict[str, Any]:
    """新建一个承接臂的隔离实例（真 owner + 正式 runtime），预算指向共享持久账本。"""

    plan = v2.DEFAULT_PLAN
    root = Path(parent) / name
    prepared = v2.prepare_scratch_instance(root, plan=plan)
    if not prepared.get("ok"):
        return {"ok": False, "reason_code": prepared.get("reason_code") or "PREPARE_FAILED",
                "detail": prepared.get("detail"), "root": str(root)}
    context: dict[str, Any] = {"ok": True, "root": root, "prepared": prepared,
                               "supply": None, "bundle": None}
    try:
        context["supply"] = baseline.start_supply_owner(root, tag="g2-continuation")
        env = v2.scratch_env(root, plan=plan)
        for key in [item for item in env if item.startswith("NYA02_MODEL_")]:
            env.pop(key, None)
        env.update({key: value for key, value in v2.offline_model_env(plan).items()
                    if key.startswith("NYA02_MODEL_")})
        env["CHIYO_AUTONOMOUS_CHECK_INTERVAL_MINUTES"] = "0"
        state_root = Path(str(env.get("NYA02_RESIDENT_STATE") or ""))
        bundle = baseline.build_experiment_runtime(
            env, root=root, state_root=state_root, budget=budget,
            ledger=v2.PlanGateLedger(plan), stage="continuation",
            cognition_transport=decision_transport, content_transport=content_transport,
            neutralise_experience_history=neutralise, latch=latch)
        bundle["assembled_env"] = env
        context.update({"env": env, "state_root": state_root, "bundle": bundle,
                        "decision_transport": decision_transport,
                        "content_transport": content_transport})
        return context
    except Exception as exc:
        v2._close_scratch(context)
        return {"ok": False, "reason_code": "SCRATCH_SETUP_FAILED",
                "detail": "%s: %s" % (type(exc).__name__, str(exc)[:200]), "root": str(root)}


def continuation_isolation_check() -> dict[str, Any]:
    """两臂离线承接：各自新 root、各自合法 Decision → Action owner 效果、不串臂。

    预算 4 与主 52 同约束：两臂共用同一本持久账本，每臂 ``arm_ceiling`` 只放 2 次，
    第三次在 reserve 处拒；不走 v1 的 ``clone_branch``，也不访问任何已结束 root。
    """

    from life_request_budget import LifeRequestBudget

    with _Keep() as parent:
        budget = LifeRequestBudget(parent / "continuation-ledger", utc_day_max=300,
                                   run_window_max=G2_PROPOSED_BUDGET["continuation"],
                                   run_id="run:g2-continuation-offline")
        budget.ensure_window(allow_new=True)
        latch = baseline.SendLatch(lambda: False)
        arms: dict[str, Any] = {}
        for label in ("A", "B"):
            context = _open_arm_scratch(
                parent, "instance-%s" % label,
                decision_transport=v2.ScriptedStartTransport(),
                content_transport=v2.ScriptedAppendTransport(),
                budget=budget, latch=latch, neutralise=(label == "B"))
            if not context.get("ok"):
                return {"ok": False,
                        "reason_code": context.get("reason_code") or "SCRATCH_SETUP_FAILED",
                        "detail": context.get("detail"), "arm": label}
            try:
                before = int(budget.view()["run_window_calls"])
                with baseline.arm_ceiling(budget, baseline.ARM_REQUEST_CAP) as limit:
                    report = context["bundle"]["cycle"].evaluate_once(
                        trigger_ref="g2:continuation:%s" % label,
                        trigger_kind=baseline._idle_trigger_kind(), act=True)
                    used = int(budget.view()["run_window_calls"]) - before
                    third_sender = _CountingSender()
                    third = _send_through_adapter(budget, third_sender)
                record = baseline._round_record(report)
                works = [str(row["artifact_id"]) for row in context["prepared"]["works"]]
                target = str(record.get("step_work_target") or "")
                arms[label] = {
                    "root": str(context["root"]),
                    "works": works,
                    "used": used,
                    "ceiling": limit["ceiling"],
                    "decision_calls": len(context["decision_transport"].calls),
                    "content_calls": len(context["content_transport"].calls),
                    "outcome": record.get("outcome"),
                    "decision": record.get("decision"),
                    "action_status": record.get("action_status"),
                    "owner_version_created": record.get("owner_version_created"),
                    "owner_version_id": record.get("owner_version_id"),
                    "receipt_effect_state": record.get("receipt_effect_state"),
                    "target_in_own_works": baseline._bare_artifact(target) in
                    {baseline._bare_artifact("artifact:%s" % item) for item in works},
                    "third_refused": (third["sent"] is False
                                      and third_sender.calls == 0
                                      and third["reason_code"]
                                      == "REQUEST_BUDGET_RUN_WINDOW_LIMIT"),
                }
            finally:
                v2._close_scratch(context)

        total = int(budget.view()["run_window_calls"])
        works_a = {baseline._bare_artifact("artifact:%s" % item)
                   for item in arms.get("A", {}).get("works") or []}
        works_b = {baseline._bare_artifact("artifact:%s" % item)
                   for item in arms.get("B", {}).get("works") or []}
        checks = {
            "both_arms_ran": all(arms.get(arm, {}).get("outcome") == "ACTED"
                                 for arm in ("A", "B")),
            "both_arms_owner_effect": all(
                arms.get(arm, {}).get("owner_version_created") is True
                and bool(arms.get(arm, {}).get("owner_version_id"))
                and arms.get(arm, {}).get("receipt_effect_state") == "APPLIED"
                for arm in ("A", "B")),
            "each_arm_used_decision_plus_content": all(
                arms.get(arm, {}).get("used") == 2
                and arms.get(arm, {}).get("decision_calls") == 1
                and arms.get(arm, {}).get("content_calls") == 1
                for arm in ("A", "B")),
            "each_arm_refuses_third_before_transport": all(
                arms.get(arm, {}).get("third_refused") for arm in ("A", "B")),
            "shared_ledger_holds_4": total == G2_PROPOSED_BUDGET["continuation"],
            "separate_roots": (arms.get("A", {}).get("root")
                               != arms.get("B", {}).get("root")),
            "arm_works_are_disjoint": bool(works_a and works_b and works_a.isdisjoint(works_b)),
            "each_arm_targets_its_own_work": all(
                arms.get(arm, {}).get("target_in_own_works") for arm in ("A", "B")),
        }
        blocking = [key for key, value in checks.items() if not value]
        return {"ok": not blocking, "blocking": blocking, "checks": checks,
                "arms": {arm: {key: arms.get(arm, {}).get(key) for key in
                               ("used", "ceiling", "decision", "action_status",
                                "owner_version_created", "receipt_effect_state")}
                         for arm in ("A", "B")},
                "ledger_calls": total,
                "ledger_cap": int(budget.run_window_max),
                "scratch_parent": str(parent),
                "note": ("两臂各自新 mkdtemp root，脚本 Decision/内容替身、正式 owner/AG1/AG2/AR0；"
                         "共享一本 4 格持久账本，不 clone、不碰任何已结束 root。")}


# ---------------------------------------------------------------------------
# 8. 真实入口关闭 + 内部 root 守卫
# ---------------------------------------------------------------------------

def product_entry_check() -> dict[str, Any]:
    """真实 prepare/run/execute-approved 拒绝且零写；已有 root 一律不接。"""

    refusal = product_run()
    with _Keep() as parent:
        existing = parent / "already-there"
        existing.mkdir()
        guard = refuse_existing_root(existing)
        freshly_named = parent / "not-yet"
        fresh_guard = refuse_existing_root(freshly_named)
    checks = {
        "prepare_run_refused": refusal.get("reason_code") == PRODUCT_RUN_NOT_AUTHORIZED,
        "refusal_writes_nothing": refusal.get("files_written") == 0,
        "existing_root_refused": guard.get("reason_code") == EXISTING_ROOT_REFUSED,
        "non_existing_root_allowed_for_fresh_scratch": fresh_guard == {},
    }
    blocking = [key for key, value in checks.items() if not value]
    return {"ok": not blocking, "blocking": blocking, "checks": checks,
            "refusal": refusal}


# ---------------------------------------------------------------------------
# 8. 统一 52 账本 + 同一冻结目标的两臂映射（2026-10-31 收尾）
# ---------------------------------------------------------------------------
#
# 两个既有接缝在上一轮是分开的：52 只是机械计数，两臂承接另建一本 4 格账本、各自
# START 新作品。本轮把它们接在一起：
#
# * 前 48 格：机械计数 transport，全部经同一个 ``LifeRequestBudget.reserve`` 发送口；
# * 后 4 格：真 owner + 正式 runtime 的两臂，各 Decision + content 的实际预留；
# * 两臂执行同一件**本单新建、真 owner 确证**的冻结作品 A/活动 actA/版本 R 的 CONTINUE，
#   从 A 写入前的同一个冻结起点隔离复制（现成 ``clone_branch``），不是各自 START 新作品。
#
# ``clone_branch`` 的现成 ``runtime_env`` 把分支身份硬绑在 v1 的 ``RUN_ID`` 上，所以本单隔离
# scratch 沿用该身份（不改 clone_branch、不改 v2 计划）。这一步不绕任何身份门：门要求的正是
# 这个身份。两臂的 Activity 目标门、影子选择→执行目标的连接、错 A/错 actA 拒都在执行前判。

#: 统一 52 账本的 run 身份。见本节开头：``clone_branch`` 的既有合同要求 v1 ``RUN_ID``。
G2_UNIFIED_RUN_ID = baseline.RUN_ID
G2_SETUP_OBSERVATIONS = 3


def _bare_version(value: Any) -> str:
    text = str(value or "")
    prefix = "artifactversion:"
    return text[len(prefix):] if text.startswith(prefix) else text


def _same_artifact(left: Any, right: Any) -> bool:
    return bool(left) and bool(right) and (
        baseline._bare_artifact(left) == baseline._bare_artifact(right))


def _target_observed_at(frozen: Mapping[str, Any]) -> str:
    """把承接时刻推到既有 reconsideration TTL 之后（同状态重考，不是清 quiet/改规则）。"""

    from life_step_readiness import DEFAULT_TTL_SECONDS

    raw = str(frozen.get("observed_at") or "")
    moment = datetime.fromisoformat(raw) if raw else datetime.now(timezone.utc)
    return (moment + timedelta(seconds=int(DEFAULT_TTL_SECONDS) + 120)).isoformat()


class _ScriptedContinueTransport(baseline.CaptureTransport):
    """离线裁决替身：只对**同一冻结目标**回 CONTINUE；找不到就 NO_ACTION，不 fallback。"""

    def __init__(self, target_ref: str) -> None:
        super().__init__(text="{}")
        self.target_ref = str(target_ref)
        self.matched: list[str] = []
        self.requested_rows: list[dict[str, Any]] = []

    def complete(self, *, system_prompt: str, user_prompt: str,
                 timeout_s: Optional[float] = None,
                 max_output_tokens: Optional[int] = None) -> Any:
        from agency_model_client import ModelCallResult

        self.calls.append({"max_output_tokens": max_output_tokens,
                           "prompt_chars": len(user_prompt or "")})
        answer: dict[str, Any] = {"decision": "NO_ACTION",
                                  "reason_codes": ["NO_ACTION_CHOSEN"]}
        try:
            payload = json.loads(user_prompt)
        except ValueError:
            payload = {}
        rows = [row for row in (payload.get("candidates") or [])
                if isinstance(row, Mapping)]
        self.requested_rows = [{
            "candidate_id": str(row.get("candidate_id") or ""),
            "target_refs": [str(ref) for ref in (row.get("target_refs") or ())],
            "legal_decisions": [str(item) for item in (row.get("legal_decisions") or ())],
        } for row in rows]
        wanted = baseline._bare_artifact(self.target_ref)
        for view in self.requested_rows:
            refs = {baseline._bare_artifact(ref) for ref in view["target_refs"]}
            if wanted in refs and "CONTINUE" in view["legal_decisions"]:
                self.matched.append(view["candidate_id"])
                answer = {"decision": "CONTINUE",
                          "selected_candidate_id": view["candidate_id"],
                          "reason_codes": ["OPEN_ENDED_CONTINUATION"]}
                break
        return ModelCallResult(purpose="offline", provider=self.provider, model=self.model,
                               text=json.dumps(answer, ensure_ascii=False), latency_ms=1,
                               status=200, usage_source="none")


class _SharedCapLedger:
    """正式 adapter 的发送前阶段门：只读共享账本，不另造计数。"""

    def __init__(self, cap: int) -> None:
        self.cap = int(cap)

    def assert_room(self, stage: str, view: Mapping[str, Any], *,
                    reserving: int = 0) -> dict[str, Any]:
        used = int(view.get("run_window_calls") or 0)
        if used + int(reserving) >= self.cap:
            raise baseline.BatchRefused(
                baseline.REASON_STAGE_CAP,
                "shared G2 ceiling %s reached at %s" % (self.cap, used))
        return {"stage": stage, "cap": self.cap, "used": used,
                "stage_remaining": max(0, self.cap - used - int(reserving))}


def _mechanical_48_on(budget: Any) -> list[dict[str, Any]]:
    """前 48 格：明确机械计数 transport，全部走正式 ``BudgetedCognitionAdapter``。

    第 8 次故意失败一次，证明失败预留不退（仍然占格）；这里不假装是自然采集或
    40 次真实选择。
    """

    rows: list[dict[str, Any]] = []
    with _g2_stage_ceiling(budget, 8):
        for index in range(8):
            rows.append(_send_through_adapter(budget, _CountingSender(fail=(index == 7))))
    with _g2_stage_ceiling(budget, 40):
        for _ in range(40):
            rows.append(_send_through_adapter(budget, _CountingSender()))
    return rows


def _frozen_target_plan() -> Any:
    return replace(v2.DEFAULT_PLAN, run_id=G2_UNIFIED_RUN_ID)


def _prepare_frozen_target(parent: Path) -> dict[str, Any]:
    """新 mkdtemp：真 owner 建 H，冻结出**同一件**作品 A/活动 actA/版本 R。"""

    plan = _frozen_target_plan()
    context = v2._open_scratch(
        parent, "instance-target", plan,
        decision_transport=v2.ScriptedStartTransport(),
        content_transport=v2.ScriptedAppendTransport())
    if not context.get("ok"):
        return {"ok": False,
                "reason_code": context.get("reason_code") or "SCRATCH_SETUP_FAILED",
                "detail": context.get("detail")}
    try:
        virtual = v2.VirtualTime()
        record = v2.ExperimentRecord(parent / plan.experiment_dirname, plan=plan)
        record.begin_batch(note="g2 offline: unified budget & frozen target mapping")
        work_refs = ["artifact:%s" % row["artifact_id"]
                     for row in context["prepared"]["works"]]
        summary = v2._controller_for(
            context, replace(plan, max_observations=G2_SETUP_OBSERVATIONS),
            record=record, virtual=virtual).run()
        h_ref = summary.get("h_ref")
        if not h_ref:
            v2._close_scratch(context)
            return {"ok": False, "reason_code": "H_NOT_ESTABLISHED",
                    "detail": "the controlled offline setup did not establish H",
                    "summary": {key: summary.get(key) for key in
                                ("result", "stop_reason", "requests_used")}}
        # 真实 M0 准入是异步的；给既有准入一个真实的短窗口，再冻结。
        time.sleep(0.3)
        attempt = v2.freeze_and_check(context["bundle"], h_ref=h_ref,
                                      work_refs=work_refs, sleep=time.sleep)
        frozen = attempt.get("frozen")
        if not frozen:
            v2._close_scratch(context)
            return {"ok": False,
                    "reason_code": attempt.get("reason_code") or "FREEZE_FAILED",
                    "detail": attempt.get("detail")}
        runtime = context["bundle"]["runtime"]
        activity_ref = str(runtime.current_activity_ref() or "")
        bare = baseline._bare_artifact(h_ref)
        head = context["bundle"]["port"].read_head(bare) or {}
        version_ref = str(head.get("version_id") or "")
        if not version_ref:
            # 同一轮里 owner 刚提交的版本也由 H 的正式证据给出；两处只作交叉核对。
            version_ref = _bare_version((summary.get("h_detail") or {}).get("owner_version_id"))
        rows = [row for row in (frozen.get("prior_experiences") or [])
                if _same_artifact(row.get("artifact_ref"), h_ref)]
        evidence = rows[0] if rows else {}
        same_target = bool(
            activity_ref
            and str(evidence.get("activity_ref") or "") == activity_ref
            and bool(version_ref)
            and _bare_version(evidence.get("version_ref")) == _bare_version(version_ref))
        return {
            "ok": True, "context": context, "plan": plan, "summary": summary,
            "frozen": frozen, "gate": attempt.get("gate") or {},
            "h_ref": h_ref, "activity_ref": activity_ref, "version_ref": version_ref,
            "same_target": same_target, "work_refs": list(work_refs),
            "setup_calls": int(context["budget"].view().get("run_window_calls") or 0),
            "setup_ledger": str(context["budget"].path),
            "root": str(context["root"]),
        }
    except Exception:
        v2._close_scratch(context)
        raise


def _shadow_choice(bundle: Mapping[str, Any], frozen: Mapping[str, Any],
                   experiences: Sequence[Mapping[str, Any]], target_ref: str,
                   label: str) -> dict[str, Any]:
    """影子臂的选择记录（不提交 Decision、不写主状态）；请求是离线脚本裁决。"""

    transport = _ScriptedContinueTransport(target_ref)
    recorded: dict[str, Any] = {}
    inner = baseline.raw_cognition_adapter(
        dict(bundle["assembled_env"]), transport=transport,
        admission=bundle["admission"])
    adapter = _CaptureAdapter(inner, recorded)
    # 影子记录只能落在 v1 既有实验目录名里（``_make_shadow_service`` 的现成隔离断言）。
    sample_root = (Path(str(bundle["root"])) / baseline.EXPERIMENT_DIRNAME
                   / ("target-shadow-%s" % label))
    service = baseline._make_shadow_service(bundle["runtime"], sample_root, adapter)
    opportunity = baseline.new_opportunity(
        bundle["runtime"], frozen, trigger_ref="g2:target-shadow:%s" % label)
    try:
        result = service.evaluate_opportunity(
            opportunity=opportunity,
            **baseline.shadow_kwargs(frozen, experiences))
    except Exception as exc:
        return {"ok": False, "reason_code": "SHADOW_CHOICE_FAILED",
                "detail": "%s: %s" % (type(exc).__name__, str(exc)[:160])}
    decision_record = result.get("decision_record") if isinstance(result, Mapping) else None
    return {
        "ok": True,
        "status": (result or {}).get("status"),
        "decision": getattr(decision_record, "decision", None),
        "selected_candidate_ref": getattr(decision_record, "selected_candidate_ref", None),
        "requested_rows": list(transport.requested_rows),
        "matched_candidate_ids": list(transport.matched),
        "transport_calls": len(transport.calls),
    }


def _shadow_targets(shadow: Mapping[str, Any], target_ref: str) -> bool:
    selected = str((shadow or {}).get("selected_candidate_ref") or "")
    for row in (shadow or {}).get("requested_rows") or []:
        if str(row.get("candidate_id") or "") == selected:
            return bool(
                shadow.get("decision") == "CONTINUE"
                and any(_same_artifact(ref, target_ref)
                        for ref in (row.get("target_refs") or ()))
                and "CONTINUE" in set(row.get("legal_decisions") or ()))
    return False


def _target_execution_precheck(runtime: Any, shadow: Mapping[str, Any], target_ref: str,
                               activity_ref: str, observed_at: str) -> dict[str, Any]:
    """执行前门：影子选择→执行目标必须连接；错 A/错 actA 在这里拒，不 fallback。"""

    selected = str((shadow or {}).get("selected_candidate_ref") or "")
    rows = [row for row in (shadow or {}).get("requested_rows") or []
            if str(row.get("candidate_id") or "") == selected]
    checks: dict[str, Any] = {
        "shadow_decision_is_continue": (shadow or {}).get("decision") == "CONTINUE",
        "shadow_selected_candidate": bool(selected),
        "shadow_candidate_targets_the_frozen_work": bool(rows) and any(
            _same_artifact(ref, target_ref) for ref in (rows[0].get("target_refs") or ()))
        and "CONTINUE" in set(rows[0].get("legal_decisions") or ()),
    }
    try:
        current = runtime.current_activity_ref()
    except Exception:
        current = None
    checks["foreground_is_the_frozen_activity"] = bool(activity_ref) and current == activity_ref
    present: list[Mapping[str, Any]] = []
    try:
        material = runtime.candidate_materializer.build_candidate_set(observed_at=observed_at)
        for ref in list(getattr(material, "candidate_refs", []) or []):
            payload = runtime.candidate_read.get_candidate(str(ref)) or {}
            if str(payload.get("candidate_id") or ref) == selected:
                present.append(payload)
    except Exception:
        present = []
    checks["shadow_candidate_is_still_present_at_the_same_work"] = bool(present) and any(
        _same_artifact(ref, target_ref) for ref in (present[0].get("target_refs") or ()))
    blocking = [key for key, value in checks.items() if not value]
    return {"ok": not blocking, "blocking": blocking, "checks": checks,
            "candidate_id": selected or None, "foreground": current}


def _execute_arm(*, root: Path, budget: Any, plan: Any, label: str, target_ref: str,
                 activity_ref: str, shadow: Mapping[str, Any], version_ref: str,
                 observed_at: str, neutralise: bool) -> dict[str, Any]:
    """一臂承接：真 owner/正式 runtime，正式 Decision CONTINUE 同 A 同 actA。"""

    from life_request_budget import REASON_RUN_WINDOW_LIMIT

    supply = None
    bundle = None
    try:
        supply = baseline.start_supply_owner(root, tag="g2-target-%s" % label)
        env = v2.scratch_env(root, plan=plan)
        for key in [item for item in env if item.startswith(v2._MODEL_ENV_PREFIX)]:
            env.pop(key, None)
        env.update({key: value for key, value in v2.offline_model_env(plan).items()
                    if key.startswith(v2._MODEL_ENV_PREFIX)})
        env["CHIYO_AUTONOMOUS_CHECK_INTERVAL_MINUTES"] = "0"
        state_root = Path(str(env.get("NYA02_RESIDENT_STATE") or ""))
        transport = _ScriptedContinueTransport(target_ref)
        content = v2.ScriptedAppendTransport()
        bundle = baseline.build_experiment_runtime(
            env, root=root, state_root=state_root, budget=budget,
            ledger=_SharedCapLedger(G2_PROPOSED_TOTAL), stage="continuation",
            cognition_transport=transport, content_transport=content,
            neutralise_experience_history=bool(neutralise),
            latch=baseline.SendLatch(lambda: False))
        bundle["assembled_env"] = env
        precheck = _target_execution_precheck(
            bundle["runtime"], shadow, target_ref, activity_ref, observed_at)
        if not precheck.get("ok"):
            return {"ok": False, "reason_code": "TARGET_PRECHECK_REFUSED",
                    "root": str(root), "precheck": precheck, "used": 0,
                    "execution": {}, "third_refused": None}
        before = int(budget.view().get("run_window_calls") or 0)
        with baseline.arm_ceiling(budget, baseline.ARM_REQUEST_CAP) as limit:
            report = bundle["cycle"].evaluate_once(
                trigger_ref="g2:target:%s" % label,
                trigger_kind=baseline._idle_trigger_kind(), act=True,
                observed_at=observed_at)
            after = int(budget.view().get("run_window_calls") or 0)
            third_sender = _CountingSender()
            third = _send_through_adapter(budget, third_sender)
        record = baseline._round_record(report)
        receipt = report.get("step_receipt") or {}
        execution = {
            "outcome": report.get("outcome"),
            "decision": report.get("decision") or record.get("decision"),
            "selected_candidate_ref": (report.get("selected_candidate_ref")
                                       or record.get("selected_candidate_ref")),
            "step_work_target": str(report.get("step_work_target")
                                    or record.get("step_work_target") or ""),
            "activity_ref": str(report.get("activity_ref")
                                or receipt.get("activity_ref") or ""),
            "owner_version_created": report.get("owner_version_created"),
            "owner_version_id": str(report.get("owner_version_id")
                                    or receipt.get("version_id") or ""),
            "effect_state": receipt.get("effect_state"),
            "used": after - before,
            "decision_calls": len(transport.calls),
            "content_calls": len(content.calls),
        }
        checks = {
            "decision_is_continue": execution["decision"] == "CONTINUE",
            "shadow_candidate_used": bool(transport.matched) and (
                not execution["selected_candidate_ref"]
                or str(execution["selected_candidate_ref"]) in transport.matched),
            "executed_same_artifact": _same_artifact(
                execution["step_work_target"], target_ref),
            "executed_same_activity": bool(activity_ref)
            and execution["activity_ref"] == activity_ref,
            "owner_version_created": execution["owner_version_created"] is True,
            "new_owner_version": bool(execution["owner_version_id"]) and (
                _bare_version(execution["owner_version_id"])
                != _bare_version(version_ref)),
            "effect_applied": execution["effect_state"] == "APPLIED",
            "used_decision_plus_content": (execution["used"] == 2
                                           and execution["decision_calls"] == 1
                                           and execution["content_calls"] == 1),
            "third_send_refused_before_transport": (
                third["sent"] is False and third_sender.calls == 0
                and third["reason_code"] == REASON_RUN_WINDOW_LIMIT),
        }
        blocking = [key for key, value in checks.items() if not value]
        return {"ok": not blocking, "blocking": blocking, "checks": checks,
                "root": str(root), "execution": execution, "precheck": precheck,
                "arm_ceiling": dict(limit), "third": third, "used": execution["used"],
                "after": after}
    except Exception as exc:
        return {"ok": False, "reason_code": "ARM_EXECUTION_FAILED",
                "detail": "%s: %s" % (type(exc).__name__, str(exc)[:200]),
                "root": str(root), "used": 0, "execution": {}}
    finally:
        if bundle is not None:
            try:
                bundle["runtime"].close()
            except Exception:
                pass
            baseline.release_runtime(bundle)
        if supply is not None:
            try:
                supply.stop()
            except Exception:
                pass


def _run_frozen_target_arms(*, budget: Any, parent: Path) -> dict[str, Any]:
    """一次真两臂执行：同一冻结 A/actA/R，两臂隔离映射；返回两臂与场景摘要。"""

    prep = _prepare_frozen_target(parent)
    if not prep.get("ok"):
        return {"ok": False,
                "reason_code": prep.get("reason_code") or "FROZEN_TARGET_SETUP_FAILED",
                "detail": prep.get("detail"), "stage": "setup"}
    context = prep["context"]
    closed = False
    try:
        frozen = prep["frozen"]
        arm_a_exps = [dict(row) for row in (frozen.get("prior_experiences") or [])]
        arm_b_exps = baseline.neutralise_history(arm_a_exps)
        shadow_a = _shadow_choice(context["bundle"], frozen, arm_a_exps,
                                  prep["h_ref"], "A")
        shadow_b = _shadow_choice(context["bundle"], frozen, arm_b_exps,
                                  prep["h_ref"], "B")
        if not (shadow_a.get("ok") and shadow_b.get("ok")):
            return {"ok": False, "reason_code": "SHADOW_CHOICE_FAILED",
                    "detail": {"A": shadow_a.get("detail"), "B": shadow_b.get("detail")},
                    "stage": "shadow"}
        observed_at = _target_observed_at(frozen)
        other_work = next((ref for ref in prep["work_refs"]
                           if not _same_artifact(ref, prep["h_ref"])), "")
        negative_artifact = _target_execution_precheck(
            context["bundle"]["runtime"], shadow_a, other_work,
            prep["activity_ref"], observed_at)
        negative_activity = _target_execution_precheck(
            context["bundle"]["runtime"], shadow_a, prep["h_ref"],
            "actv:not-the-frozen-activity", observed_at)
        # setup runtime 必须先真正关闭（flush + 释放 integration authority lock），
        # 否则同一进程随后在主 root 上重建 A 臂会被既有锁判成「另一个活实例」。
        try:
            context["bundle"]["runtime"].close()
        except Exception:
            pass
        v2._close_scratch(context)
        closed = True
        branch = Path(parent) / "instance-target-branch-b"
        clone = baseline.clone_branch(Path(prep["root"]), branch)
        if not clone.get("ok"):
            return {"ok": False,
                    "reason_code": clone.get("reason_code") or "BRANCH_ISOLATION_UNPROVEN",
                    "detail": clone.get("detail") or clone.get("outside"),
                    "stage": "clone", "setup_calls": prep["setup_calls"],
                    "setup_ledger": prep["setup_ledger"]}
        arm_a = _execute_arm(
            root=Path(prep["root"]), budget=budget, plan=prep["plan"], label="A",
            target_ref=prep["h_ref"], activity_ref=prep["activity_ref"],
            shadow=shadow_a, version_ref=prep["version_ref"],
            observed_at=observed_at, neutralise=False)
        arm_b = _execute_arm(
            root=branch, budget=budget, plan=prep["plan"], label="B",
            target_ref=prep["h_ref"], activity_ref=prep["activity_ref"],
            shadow=shadow_b, version_ref=prep["version_ref"],
            observed_at=observed_at, neutralise=True)
        checks = {
            "same_frozen_artifact_activity_version": bool(prep.get("same_target")),
            "both_shadow_choices_target_the_frozen_work": (
                _shadow_targets(shadow_a, prep["h_ref"])
                and _shadow_targets(shadow_b, prep["h_ref"])),
            "arm_roots_are_separate": (str(arm_a.get("root"))
                                       != str(arm_b.get("root"))),
            "arm_a_same_target_and_activity": bool(
                arm_a.get("ok")
                and arm_a["checks"]["executed_same_artifact"]
                and arm_a["checks"]["executed_same_activity"]),
            "arm_b_same_target_and_activity": bool(
                arm_b.get("ok")
                and arm_b["checks"]["executed_same_artifact"]
                and arm_b["checks"]["executed_same_activity"]),
            "each_arm_new_owner_version": bool(
                arm_a.get("ok") and arm_a["checks"]["new_owner_version"]
                and arm_b.get("ok") and arm_b["checks"]["new_owner_version"]),
            "arm_versions_are_distinct": bool(
                (arm_a.get("execution") or {}).get("owner_version_id")
                and (arm_b.get("execution") or {}).get("owner_version_id")
                and (arm_a["execution"]["owner_version_id"]
                     != arm_b["execution"]["owner_version_id"])),
            "wrong_artifact_refused_before_send": (
                negative_artifact.get("ok") is False
                and "shadow_candidate_targets_the_frozen_work"
                in (negative_artifact.get("blocking") or [])),
            "wrong_activity_refused_before_send": (
                negative_activity.get("ok") is False
                and "foreground_is_the_frozen_activity"
                in (negative_activity.get("blocking") or [])),
            "scenario_still_not_identifiable": (
                str((prep.get("gate") or {}).get("reason_code") or "")
                in (baseline.REASON_SECOND_WORK_MISSING,
                    baseline.REASON_SCENARIO_NOT_IDENTIFIABLE)),
            "no_second_work_made_executable": (
                (prep.get("gate") or {}).get("other_work_executable") is False),
        }
        blocking = [key for key, value in checks.items() if not value]
        return {
            "ok": not blocking, "blocking": blocking, "checks": checks,
            "reason_code": "TARGET_MAPPING_CHECKS_FAILED" if blocking else "",
            "artifact": prep["h_ref"], "activity_ref": prep["activity_ref"],
            "version_ref": prep["version_ref"], "observed_at": observed_at,
            "setup_calls": prep["setup_calls"], "setup_ledger": prep["setup_ledger"],
            "scenario_gate": {key: (prep.get("gate") or {}).get(key) for key in
                              ("ok", "reason_code", "h_executable",
                               "other_work_executable", "no_action_legal")},
            "shadows": {"A": _shadow_view(shadow_a), "B": _shadow_view(shadow_b)},
            "arms": {"A": arm_a, "B": arm_b},
            "negatives": {"wrong_artifact": negative_artifact.get("blocking"),
                          "wrong_activity": negative_activity.get("blocking")},
            "clone": {key: clone.get(key) for key in
                      ("ok", "outside", "checked", "shared_authority", "rewritten", "copy")},
            "branch": str(branch), "root": prep["root"],
        }
    finally:
        if not closed:
            v2._close_scratch(context)


def _shadow_view(shadow: Mapping[str, Any]) -> dict[str, Any]:
    return {key: shadow.get(key) for key in
            ("decision", "selected_candidate_ref", "transport_calls")}


def budget_target_map_check() -> dict[str, Any]:
    """本单两项：统一 52 账本真实贯通 + 同一冻结目标的两臂执行映射。

    两项共用**同一次**真两臂执行；账本从头到尾只有一本持久 ``LifeRequestBudget``。
    """

    from life_request_budget import (LifeRequestBudget, REASON_RUN_WINDOW_LIMIT)

    with _Keep() as parent:
        budget_dir = parent / "g2-52-unified"
        budget = LifeRequestBudget(budget_dir, utc_day_max=300,
                                   run_window_max=G2_PROPOSED_TOTAL,
                                   run_id=G2_UNIFIED_RUN_ID)
        budget.ensure_window(allow_new=True)
        mechanical = _mechanical_48_on(budget)
        after_mechanical = int(budget.view().get("run_window_calls") or 0)
        execution = _run_frozen_target_arms(budget=budget, parent=parent)
        after_arms = int(budget.view().get("run_window_calls") or 0)
        fifty_third_sender = _CountingSender()
        fifty_third = _send_through_adapter(budget, fifty_third_sender)
        reopened = LifeRequestBudget(budget_dir, utc_day_max=300,
                                     run_window_max=G2_PROPOSED_TOTAL,
                                     run_id=G2_UNIFIED_RUN_ID)
        window = reopened.ensure_window()
        reopen_view = reopened.view()
        reopened_sender = _CountingSender()
        reopened_refused = _send_through_adapter(reopened, reopened_sender)

        arms = execution.get("arms") or {}
        arm_a = arms.get("A") or {}
        arm_b = arms.get("B") or {}
        arm_a_exec = arm_a.get("execution") or {}
        arm_b_exec = arm_b.get("execution") or {}
        budget_checks = {
            "mechanical_48_fills_the_shared_ledger": (
                after_mechanical == 48
                and sum(1 for row in mechanical if row["sent"]) == 47),
            "failed_mechanical_send_keeps_its_slot": (
                mechanical[7]["sent"] is False and after_mechanical == 48),
            "arm_a_uses_exactly_two": bool(
                arm_a.get("ok") and arm_a.get("used") == 2
                and int(arm_a_exec.get("decision_calls") or 0) == 1
                and int(arm_a_exec.get("content_calls") or 0) == 1),
            "arm_b_uses_exactly_two": bool(
                arm_b.get("ok") and arm_b.get("used") == 2
                and int(arm_b_exec.get("decision_calls") or 0) == 1
                and int(arm_b_exec.get("content_calls") or 0) == 1),
            "ledger_walks_48_then_50_then_52": bool(
                arm_a.get("after") == 50 and arm_b.get("after") == 52),
            "shared_ledger_holds_exactly_52": after_arms == 52,
            "every_arm_request_was_really_reserved": bool(
                bool(arm_a_exec) and bool(arm_b_exec)
                and after_arms == 48 + int(arm_a_exec.get("used") or 0)
                + int(arm_b_exec.get("used") or 0)
                and int(arm_a_exec.get("used") or 0)
                == int(arm_a_exec.get("decision_calls") or 0)
                + int(arm_a_exec.get("content_calls") or 0)
                and int(arm_b_exec.get("used") or 0)
                == int(arm_b_exec.get("decision_calls") or 0)
                + int(arm_b_exec.get("content_calls") or 0)),
            "each_arm_refuses_third_before_transport": bool(
                arm_a.get("ok") and arm_a["checks"]["third_send_refused_before_transport"]
                and arm_b.get("ok")
                and arm_b["checks"]["third_send_refused_before_transport"]),
            "fifty_third_refused_before_transport": (
                fifty_third["sent"] is False and fifty_third_sender.calls == 0
                and fifty_third["reason_code"] == REASON_RUN_WINDOW_LIMIT),
            "reopened_same_path_continues_the_ledger": (
                window.get("state") == "existing"
                and int(window.get("calls") or 0) == 52
                and int(reopen_view.get("run_window_calls") or 0) == 52
                and int(reopen_view.get("run_window_max") or 0) == 52),
            "reopened_instance_still_refuses_53rd": (
                reopened_refused["sent"] is False and reopened_sender.calls == 0
                and reopened_refused["reason_code"] == REASON_RUN_WINDOW_LIMIT),
            "setup_calls_are_on_a_separate_ledger": bool(
                execution.get("ok")
                and execution.get("setup_ledger")
                and execution.get("setup_ledger") != str(budget.path)),
        }
        budget_blocking = [key for key, value in budget_checks.items() if not value]

        if execution.get("ok"):
            mapping_checks = dict(execution.get("checks") or {})
            mapping_blocking = [key for key, value in mapping_checks.items() if not value]
            partial_stop = None
        else:
            mapping_checks = {}
            mapping_blocking = [str(execution.get("reason_code") or "TARGET_MAPPING_STOP")]
            partial_stop = {
                "reason_code": execution.get("reason_code") or "TARGET_MAPPING_STOP",
                "stage": execution.get("stage"),
                "detail": execution.get("detail"),
                "mechanical_48_completed": after_mechanical == 48,
                "four_real_runtime_slots_run": False,
            }

        return {
            "ok": not budget_blocking and not mapping_blocking,
            "budget": {
                "ok": not budget_blocking, "blocking": budget_blocking,
                "checks": budget_checks,
                "ledger_calls": after_arms, "ledger_cap": G2_PROPOSED_TOTAL,
                "after_mechanical": after_mechanical,
                "after_arm_a": arm_a.get("after"), "after_arm_b": arm_b.get("after"),
                "mechanical_sent": sum(1 for row in mechanical if row["sent"]),
                "reopened": {"state": window.get("state"),
                             "calls": int(reopen_view.get("run_window_calls") or 0),
                             "refused_53rd": reopened_refused["sent"] is False},
                "setup_calls": execution.get("setup_calls"),
                "setup_ledger": execution.get("setup_ledger"),
                "note": ("48 格机械计数 transport + 4 格真 owner/正式 runtime；"
                         "全部经同一个 LifeRequestBudget.reserve。"),
            },
            "mapping": {
                "ok": not mapping_blocking, "blocking": mapping_blocking,
                "checks": mapping_checks,
                "artifact": execution.get("artifact"),
                "activity_ref": execution.get("activity_ref"),
                "version_ref": execution.get("version_ref"),
                "observed_at": execution.get("observed_at"),
                "scenario_gate": execution.get("scenario_gate"),
                "shadows": execution.get("shadows"),
                "arms": {label: {key: (row or {}).get(key) for key in
                                 ("ok", "blocking", "reason_code", "detail", "used",
                                  "after", "precheck", "checks")}
                         for label, row in arms.items()},
                "negatives": execution.get("negatives"),
                "clone": execution.get("clone"),
                "note": ("两臂从 A 写入前的同一冻结起点隔离复制（现成 clone_branch），"
                         "正式 CONTINUE 同 A 同 actA；不是各自 START 新作品。"),
            },
            "execution_ok": bool(execution.get("ok")),
            "partial_stop": partial_stop,
            "scratch_parent": str(parent),
            "note": ("本节只验证目标执行接缝与统一预算：场景仍是 "
                     "SECOND_WORK_CANDIDATE_MISSING/SCENARIO_NOT_IDENTIFIABLE，"
                     "不宣称完整 G2 比较。"),
        }


# ---------------------------------------------------------------------------
# offline-check 汇总
# ---------------------------------------------------------------------------

def _hard_disconnect(cls: type, method: str) -> Callable[[], None]:
    original = getattr(cls, method, None)

    def _blocked(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("OFFLINE_HTTP_FORBIDDEN")

    setattr(cls, method, _blocked)

    def restore() -> None:
        if original is not None:
            setattr(cls, method, original)

    return restore


def _run_unit_tests() -> dict[str, Any]:
    try:
        suite = unittest.TestLoader().discover(
            start_dir=str(REPO_ROOT / "tests"), pattern="test_g2_next_offline.py")
    except Exception as exc:
        return {"ok": False, "reason": "%s: %s" % (type(exc).__name__, exc)}
    with open(os.devnull, "w") as sink:
        result = unittest.TextTestRunner(verbosity=0, stream=sink).run(suite)
    return {"ok": bool(result.wasSuccessful()), "run": int(result.testsRun),
            "failures": len(result.failures), "errors": len(result.errors),
            "skipped": len(getattr(result, "skipped", []) or [])}


def _guard(name: str, func: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return func()
    except Exception as exc:  # 任何异常都进阻断面，不吞成绿。
        return {"ok": False, "reason": "%s: %s" % (type(exc).__name__, str(exc)[:240])}


def _budget_target_map_section() -> dict[str, Any]:
    """定向 section：只跑统一预算与冻结目标映射，两项共用同一次真两臂执行。"""

    restore = _hard_disconnect(http.client.HTTPConnection, "connect")
    cache: dict[str, Any] = {}

    def combined() -> dict[str, Any]:
        if "error" in cache:
            raise cache["error"]
        if "value" not in cache:
            try:
                cache["value"] = budget_target_map_check()
            except Exception as exc:  # 意外失败不包装成完成
                cache["error"] = exc
                raise
        return cache["value"]

    def budget_gate() -> dict[str, Any]:
        value = combined()
        return dict(value["budget"])

    def mapping_gate() -> dict[str, Any]:
        value = combined()
        return dict(value["mapping"])

    try:
        gates: dict[str, Any] = {
            "unit_tests": _guard("unit_tests", _run_unit_tests),
            "budget_unified": _guard("budget_unified", budget_gate),
            "frozen_target_mapping": _guard("frozen_target_mapping", mapping_gate),
        }
        value = cache.get("value") or {}
        blocking = [name for name, gate in gates.items() if not (gate or {}).get("ok")]
        return {
            "ok": not blocking, "section": "budget-target-map", "blocking": blocking,
            "gates": gates,
            "partial_stop": value.get("partial_stop"),
            "model_called": 0, "request_sent": 0, "formal_root_created": False,
            "new_roots": "new mkdtemp only, preserved (no delete/rmtree/unlink)",
            "network": "hard-disconnected (http.client.HTTPConnection.connect)",
            "scratch_parent": value.get("scratch_parent"),
        }
    finally:
        restore()


def offline_check(section: Optional[str] = None) -> dict[str, Any]:
    """唯一命令：离线门全跑；全程硬断网、0 产品预算、0 正式根。

    ``section == "budget-target-map"`` 时只跑本单定向检查（统一 52 账本 + 冻结目标映射），
    不重跑旧 10 段/旧 suite/全库。
    """

    if section == "budget-target-map":
        return _budget_target_map_section()
    restore = _hard_disconnect(http.client.HTTPConnection, "connect")
    try:
        gates: dict[str, Any] = {
            "unit_tests": _guard("unit_tests", _run_unit_tests),
            "shadow_construction": _guard("shadow_construction", shadow_construction_check),
            "budget_gate_v1_side_evidence": _guard("budget_gate_v1_side_evidence",
                                                   budget_gate_check),
            "g2_budget_52": _guard("g2_budget_52", g2_budget_gate_check),
            "proposed_budget": _guard("proposed_budget", proposed_budget_check),
            "statistics": _guard("statistics", statistics_check),
            "formal_input_isolation": _guard("formal_input_isolation",
                                             formal_input_isolation_check),
            "continuation_isolation": _guard("continuation_isolation",
                                             continuation_isolation_check),
            "product_entry": _guard("product_entry", product_entry_check),
        }
        # 真实接缝最后跑：它会起真实 supply owner，耗时最长。
        gates["natural_controller"] = _guard("natural_controller", natural_controller_check)
        blocking = [name for name, gate in gates.items() if not (gate or {}).get("ok")]
        return {"ok": not blocking, "blocking": blocking, "gates": gates,
                "not_implemented": dict(NOT_IMPLEMENTED),
                "real_gate_status": REAL_GATE_STATUS,
                "proposed_budget": dict(G2_PROPOSED_BUDGET),
                "model_called": 0, "request_sent": 0, "formal_root_created": False,
                "new_roots": "new mkdtemp only, preserved (no delete/rmtree/unlink)",
                "network": "hard-disconnected (http.client.HTTPConnection.connect)"}
    finally:
        restore()


def _print_summary(result: Mapping[str, Any]) -> None:
    print(json.dumps({key: result.get(key) for key in (
        "ok", "blocking", "model_called", "request_sent", "formal_root_created",
        "real_gate_status", "proposed_budget")}, ensure_ascii=False, indent=1))
    for name, gate in (result.get("gates") or {}).items():
        print("[%s] ok=%s%s" % (
            name, gate.get("ok"),
            "" if gate.get("ok") else " %s" % (
                gate.get("reason") or gate.get("blocking") or gate.get("detail") or "")))
    ledger = (result.get("gates") or {}).get("g2_budget_52") or {}
    if ledger.get("ledger_calls") is not None:
        print("G2 52 账本：calls=%s cap=%s state=%s"
              % (ledger.get("ledger_calls"), ledger.get("ledger_cap"),
                 ledger.get("ledger_state")))
    isolation = (result.get("gates") or {}).get("formal_input_isolation") or {}
    if isolation.get("scenario_gate"):
        print("正式输入隔离：history_rows=%s prompt_chars=%s scenario=%s"
              % (isolation.get("history_rows"), isolation.get("final_prompt_chars"),
                 isolation.get("scenario_gate")))
    continuation = (result.get("gates") or {}).get("continuation_isolation") or {}
    if continuation.get("arms"):
        print("离线承接：arms=%s ledger=%s/%s"
              % (continuation.get("arms"), continuation.get("ledger_calls"),
                 continuation.get("ledger_cap")))
    controller = (result.get("gates") or {}).get("natural_controller") or {}
    if controller.get("gate"):
        print("A 控制器：outcome=%s gate=%s requests=%s observations=%s"
              % (controller.get("outcome"), controller.get("gate"),
                 controller.get("requests_used"), controller.get("observations")))
    stats = (result.get("gates") or {}).get("statistics") or {}
    if stats.get("boundary"):
        print("统计边界：p=%s delta=%s" % (stats["boundary"].get("p"),
                                          stats["boundary"].get("delta")))
    print("未实现（如实列出，不算通过）：")
    for name, detail in (result.get("not_implemented") or {}).items():
        print("  - %s: %s" % (name, detail))


def _print_section_summary(result: Mapping[str, Any]) -> None:
    print(json.dumps({key: result.get(key) for key in (
        "ok", "section", "blocking", "partial_stop", "model_called", "request_sent",
        "formal_root_created")}, ensure_ascii=False, indent=1))
    for name, gate in (result.get("gates") or {}).items():
        print("[%s] ok=%s%s" % (
            name, gate.get("ok"),
            "" if gate.get("ok") else " %s" % (
                gate.get("reason") or gate.get("blocking") or gate.get("detail") or "")))
    budget = (result.get("gates") or {}).get("budget_unified") or {}
    if budget.get("ledger_calls") is not None:
        print("统一账本：calls=%s cap=%s 48→%s→%s setup=%s"
              % (budget.get("ledger_calls"), budget.get("ledger_cap"),
                 budget.get("after_arm_a"), budget.get("after_arm_b"),
                 budget.get("setup_calls")))
    mapping = (result.get("gates") or {}).get("frozen_target_mapping") or {}
    if mapping.get("artifact"):
        print("冻结目标映射：artifact=%s activity=%s 场景=%s"
              % (mapping.get("artifact"), mapping.get("activity_ref"),
                 (mapping.get("scenario_gate") or {}).get("reason_code")))
    if result.get("partial_stop"):
        print("PARTIAL_STOP: %s" % json.dumps(result["partial_stop"],
                                              ensure_ascii=False, default=str))


def cmd_offline_check(argv: Sequence[str]) -> int:
    section = str(argv[0]) if argv and argv[0] else None
    result = offline_check(section)
    if section:
        _print_section_summary(result)
    else:
        _print_summary(result)
    if not result.get("ok"):
        print("阻断面: %s" % ", ".join(result.get("blocking") or []))
        return EXIT_ERROR
    return EXIT_OK


def cmd_product_refused(_argv: Sequence[str]) -> int:
    print(json.dumps(product_run(), ensure_ascii=False, indent=1))
    return EXIT_REFUSED


COMMANDS = {"prepare": cmd_product_refused,
            "run": cmd_product_refused,
            "execute-approved": cmd_product_refused}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="G2 历史影响选择 · A 路线离线准备驱动；真实 prepare/run 未开启。")
    parser.add_argument("--offline-check", action="store_true",
                        help="唯一可执行命令：离线检查")
    parser.add_argument("--section", choices=["budget-target-map"], default=None,
                        help="定向离线 section：只跑本单预算/目标映射检查")
    parser.add_argument("command", nargs="?", choices=sorted(COMMANDS),
                        help="真实入口（一律拒绝）：prepare / run / execute-approved")
    args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])
    if args.offline_check:
        return cmd_offline_check([args.section] if args.section else [])
    if args.command:
        return COMMANDS[args.command]([])
    parser.print_usage(sys.stderr)
    return EXIT_REFUSED


if __name__ == "__main__":
    raise SystemExit(main())

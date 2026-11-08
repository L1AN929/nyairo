#!/usr/bin/env python3
"""v0.6 E：回顾反馈供给修复后的自然观察入口（offline-check / prepare / audit / run）。

这是 [E 预登记](../../docs/engineering/v06-world-natural-e-prereg-20261103.md) 的**薄包装**。
它沿 D 已验收的同一条单 natural phase、同一套安全投影/endpoint 拒绝和 g1 正式自然 phase，
不另造生活循环，也不复制 D 的整套离线矩阵：

* 身份固定为 ``v06-natural-e``（``g1_real_adoption.V06_E_PROFILE``）：
  ``/tmp/nyairo-v06-natural-20261103-e``、独立 authority、``run:v06-world-natural-20261103-e``、
  ``v06:world-natural:20261103-e``、history 232、20 请求 / **1800 真实秒**、
  最晚启动 2026-11-04 00:15 CST、绝对截止 00:50 CST、收尾额外 60 秒。
* 与 D 一样只在本进程树里 ``WORLD_BODY_INTENT_MUTATION_GATE=off``、
  ``CHIYO_WORLD_AMBIENT_EVENTS_ENABLED=1``；父 shell、实例默认和旧批都不动。
* 本批目的是**用修后代码正常跑一次新实例**，观察实际机会/选择/确证结果是否正常留存和收尾。
  它不按关键词/正文/标题判断回顾，不新增 prompt/正文捕获仪器；遇到 review 结果才谈
  自动种子过滤被覆盖，没遇到就是 ``NOT_COVERED``。
* **真实 prepare/run 只实现、不执行**：本轮由父级唯一运行。真实 base URL 只从运行期
  ``NYA02_MODEL_BASE_URL`` 取，无默认；缺失、脱敏占位（``.invalid``）、非 http(s) 协议或
  URL 自带凭据都在写任何东西之前拒绝。

安全字段与 D/B/C 一致：只输出 id/枚举/布尔/计数/路径；不落 prompt、正文、reasoning、
headers、token 或异常原文。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
for entry in (str(ROOT), str(SCRIPT_DIR), str(ROOT / "tests"),
              str(ROOT / "components"), str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"), str(ROOT / "components" / "world" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import g1_real_adoption as g1  # noqa: E402

#: D 的窄安全投影与 endpoint/拒绝证据 helper 是现成且身份无关的，直接复用，
#: 不再复制一份；D 自己的离线根与行为不受影响。
import v06_world_natural_d as dmod  # noqa: E402

BATCH = g1.V06_E_PROFILE.key
PROFILE = g1.V06_E_PROFILE
OFFLINE_PREFIX = "v06-natural-e-offline-"
MAX_OFFLINE_SECONDS = 900.0
EXIT_OK = 0
EXIT_REFUSED = 2
EXIT_FAILED = 3

#: 仓库默认 World 写面：离线检查前后必须零新增（只比较存在/大小/mtime）。
REPO_LIFECYCLE = ROOT / "data" / "world" / "run" / "action_lifecycle.jsonl"


# ---------------------------------------------------------------------------------------------
# 命令（真实路径只实现、不执行）
# ---------------------------------------------------------------------------------------------


def cmd_prepare(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="v06_world_natural_e prepare")
    parser.parse_args(list(argv))
    refusal = dmod._endpoint_refusal(os.environ)
    if refusal is not None:
        payload = dict(refusal, batch=BATCH, root=str(PROFILE.root))
        return dmod._emit(payload, code=EXIT_REFUSED)
    with g1._use_batch(BATCH):
        result = g1.prepare(PROFILE.root)
    payload = dmod._safe_result(result)
    payload["batch"] = BATCH
    payload["latest_start"] = PROFILE.latest_start.isoformat()
    payload["deadline"] = PROFILE.deadline.isoformat()
    payload["window_seconds"] = g1._profile_window_seconds(PROFILE)
    payload["max_ticks"] = g1._profile_max_ticks(PROFILE)
    payload["trace_max_records"] = g1._profile_trace_max_records(PROFILE)
    return dmod._emit(payload, code=EXIT_OK if result.get("ok") else EXIT_REFUSED)


def cmd_audit(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="v06_world_natural_e audit")
    parser.parse_args(list(argv))
    with g1._use_batch(BATCH):
        result = g1.audit(PROFILE.root)
    return dmod._emit(result, code=EXIT_OK if result.get("ok") else EXIT_REFUSED)


def cmd_run(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="v06_world_natural_e run")
    parser.add_argument("--execute-approved", action="store_true")
    parser.add_argument("--capture-candidates", action="store_true")
    args = parser.parse_args(list(argv))
    refusal = dmod._endpoint_refusal(os.environ)
    if refusal is not None:
        payload = dict(refusal, batch=BATCH, root=str(PROFILE.root))
        return dmod._emit(payload, code=EXIT_REFUSED)
    with g1._use_batch(BATCH):
        report = g1.start_run(PROFILE.root,
                              execute_approved=bool(args.execute_approved),
                              capture_candidates=bool(args.capture_candidates))
    payload = dmod._safe_result(report)
    payload["batch"] = BATCH
    payload["window_seconds"] = g1._profile_window_seconds(PROFILE)
    payload["max_ticks"] = g1._profile_max_ticks(PROFILE)
    payload["trace_max_records"] = g1._profile_trace_max_records(PROFILE)
    if report.get("natural") is not None:
        natural = dmod._safe_natural(report.get("natural") or {})
        # E 没有 D 的 client 边界捕获；不把一组全 None 的键当成新观测。
        natural.pop("ambient_client", None)
        payload["natural"] = natural
    return dmod._emit(payload, code=EXIT_OK if report.get("ok") else EXIT_REFUSED)


# ---------------------------------------------------------------------------------------------
# 离线检查：只验 E 自己的封闭值/预算/时间/隔离与一次正常短 natural phase
# ---------------------------------------------------------------------------------------------


def _identity_checks(checks: Any) -> dict[str, Any]:
    old = {
        "a": ("/root/nyairo-g1-real-20261030", "run:g1-real-20261030",
              "g1:real:20261030", 98),
        "b": ("/root/nyairo-g1-real-20261030-b", "run:g1-real-20261030-b",
              "g1:real:20261030-b", 101),
        "c": ("/root/nyairo-g1-real-20261030-c", "run:g1-real-20261030-c",
              "g1:real:20261030-c", 121),
        "d": ("/root/nyairo-pause-observe-20261030-d", "run:pause-observe-20261030-d",
              "pause-observe:20261030-d", 141),
        "e": ("/root/nyairo-world-observe-20261031-e", "run:world-observe-20261031-e",
              "world-observe:20261031-e", 148),
        "f": ("/root/nyairo-g1-real-20261031-f", "run:g1-real-20261031-f",
              "g1:real:20261031-f", 168),
    }
    actual = {key: (str(MODE.root), MODE.run_id, MODE.domain_id, MODE.expected_history)
              for key, MODE in g1.MODE_PROFILES.items() if key in old}
    checks.add("old a-f identities are unchanged", actual == old, actual)
    checks.add("E is the new closed identity", (
        str(PROFILE.root) == "/tmp/nyairo-v06-natural-20261103-e"
        and str(PROFILE.authority_dir) == "/tmp/nyairo-v06-natural-20261103-e-memory-authority"
        and str(PROFILE.authority_db)
        == "/tmp/nyairo-v06-natural-20261103-e-memory-authority/authority.db"
        and PROFILE.run_id == "run:v06-world-natural-20261103-e"
        and PROFILE.domain_id == "v06:world-natural:20261103-e"
        and PROFILE.expected_history == 232
        and PROFILE.latest_start is not None
        and PROFILE.deadline.isoformat() == "2026-11-04T00:50:00+08:00"
        and PROFILE.latest_start.isoformat() == "2026-11-04T00:15:00+08:00"
        and g1._profile_window_seconds(PROFILE) == 1800
        and g1._profile_max_ticks(PROFILE) == 900
        and g1._profile_trace_max_records(PROFILE) == 4000),
        {"root": str(PROFILE.root), "run": PROFILE.run_id, "domain": PROFILE.domain_id,
         "history": PROFILE.expected_history,
         "window": g1._profile_window_seconds(PROFILE),
         "ticks": g1._profile_max_ticks(PROFILE),
         "trace": g1._profile_trace_max_records(PROFILE)})
    checks.add("D keeps its closed values; E did not overwrite the previous batch", (
        str(g1.V06_D_PROFILE.root) == "/tmp/nyairo-v06-natural-20261103-d"
        and g1.V06_D_PROFILE.expected_history == 212
        and g1.V06_D_PROFILE.run_id == "run:v06-world-natural-20261103-d"
        and g1.V06_D_PROFILE.deadline.isoformat() == "2026-11-04T00:50:00+08:00"
        and g1._profile_window_seconds(g1.V06_D_PROFILE) == 1800
        and g1._world_intent_gate(g1.V06_D_PROFILE) == "off"
        and g1._ambient_events_env(g1.V06_D_PROFILE) == "1"),
        {"d_history": g1.V06_D_PROFILE.expected_history})
    checks.add("B and C keep their earlier closed values", (
        str(g1.B_PROFILE.root) == "/tmp/nyairo-v06-natural-20261102-b"
        and g1.B_PROFILE.expected_history == 191
        and g1.B_PROFILE.deadline.isoformat() == "2026-11-02T20:00:00+08:00"
        and str(g1.C_PROFILE.root) == "/tmp/nyairo-v06-natural-20261102-c"
        and g1.C_PROFILE.expected_history == 192
        and g1.C_PROFILE.deadline.isoformat() == "2026-11-02T23:05:00+08:00"
        and g1._profile_window_seconds(g1.C_PROFILE) == 10800),
        {"b_history": g1.B_PROFILE.expected_history,
         "c_history": g1.C_PROFILE.expected_history})
    checks.add("E covers the whole 1800s / 5s tick window before its loop cap",
               g1._profile_max_ticks(PROFILE) * g1.TICK_INTERVAL_SECONDS >= 1800,
               {"ticks": g1._profile_max_ticks(PROFILE),
                "tick_seconds": g1.TICK_INTERVAL_SECONDS})
    checks.add("E is off like D and old d/f; e/B/C stay canonical; a stays unset",
               g1._world_intent_gate(PROFILE) == "off"
               and g1._world_intent_gate(g1.V06_D_PROFILE) == "off"
               and g1._world_intent_gate(g1.E_PROFILE) == "canonical"
               and g1._world_intent_gate(g1.B_PROFILE) == "canonical"
               and g1._world_intent_gate(g1.C_PROFILE) == "canonical"
               and g1._world_intent_gate(g1.D_PROFILE) == "off"
               and g1._world_intent_gate(g1.F_PROFILE) == "off"
               and g1._world_intent_gate(g1.BATCH_PROFILES["a"]) is None,
               {"e": g1._world_intent_gate(PROFILE)})
    checks.add("the ambient switch covers v0.6 D/E only; old batches keep no value",
               g1._ambient_events_env(PROFILE) == "1"
               and g1._ambient_events_env(g1.V06_D_PROFILE) == "1"
               and g1._ambient_events_env(g1.B_PROFILE) is None
               and g1._ambient_events_env(g1.C_PROFILE) is None
               and g1._ambient_events_env(g1.E_PROFILE) is None
               and g1._ambient_events_env(g1.D_PROFILE) is None,
               {"e": g1._ambient_events_env(PROFILE),
                "c": g1._ambient_events_env(g1.C_PROFILE)})
    checks.add("the old natural set and CLI tuples did not grow for E",
               g1.NATURAL_PROFILES == frozenset({"d", "e"})
               and g1.BATCH_CHOICES == ("a", "b", "c", "d")
               and g1.CLI_BATCH_CHOICES == ("a", "b", "c", "d", "e")
               and BATCH not in g1.BATCH_CHOICES
               and BATCH not in g1.CLI_BATCH_CHOICES,
               {"natural": sorted(g1.NATURAL_PROFILES),
                "cli": list(g1.CLI_BATCH_CHOICES)})
    return {"identity": actual, "e_root": str(PROFILE.root), "e_run_id": PROFILE.run_id,
            "e_domain_id": PROFILE.domain_id, "e_history": PROFILE.expected_history,
            "e_deadline": PROFILE.deadline.isoformat(),
            "e_latest_start": PROFILE.latest_start.isoformat(),
            "e_window_seconds": g1._profile_window_seconds(PROFILE),
            "e_max_ticks": g1._profile_max_ticks(PROFILE),
            "e_trace_max_records": g1._profile_trace_max_records(PROFILE)}


def _ledger_scope_checks(checks: Any) -> dict[str, Any]:
    class _LedgerStub:
        def __init__(self, state: str) -> None:
            self.state = state

        def view(self) -> dict[str, Any]:
            return {"state": self.state, "run_window_calls": 7}

    with g1._use_batch(g1.E_PROFILE.key):
        old_lower = g1._ledger_calls(_LedgerStub("corrupt"))
        old_upper = g1._ledger_calls(_LedgerStub("CORRUPT"))
        old_unreadable = g1._ledger_calls(_LedgerStub("UNREADABLE"))
    with g1._use_batch(BATCH):
        new_lower = g1._ledger_calls(_LedgerStub("corrupt"))
        new_upper = g1._ledger_calls(_LedgerStub("CORRUPT"))
        new_unreadable = g1._ledger_calls(_LedgerStub("UNREADABLE"))
    with g1._use_batch(g1.V06_D_PROFILE.key):
        d_lower = g1._ledger_calls(_LedgerStub("corrupt"))
        d_unreadable = g1._ledger_calls(_LedgerStub("UNREADABLE"))
    checks.add("the corrupt-ledger fail-closed covers v0.6 D/E; old batches keep HEAD semantics",
               old_lower == 7 and old_upper is None and old_unreadable == 7
               and new_lower is None and new_upper is None and new_unreadable is None
               and d_lower is None and d_unreadable is None,
               {"old": [old_lower, old_upper, old_unreadable],
                "e": [new_lower, new_upper, new_unreadable],
                "d": [d_lower, d_unreadable]})
    return {"old_lower": old_lower, "old_upper": old_upper,
            "old_unreadable": old_unreadable, "e_lower": new_lower,
            "e_upper": new_upper, "e_unreadable": new_unreadable,
            "d_lower": d_lower, "d_unreadable": d_unreadable}


def _budget_checks(checks: Any, scratch: Path) -> dict[str, Any]:
    cap_root = scratch / "cap-state"
    cap_root.mkdir(parents=True, exist_ok=True)
    budget = g1._budget(cap_root)
    budget.ensure_window(allow_new=True)
    reserved = 0
    for _index in range(g1.REQUEST_CAP):
        budget.reserve("decision")
        reserved += 1
    third_refused = False
    try:
        budget.reserve("decision")
    except Exception:  # noqa: BLE001 - the refusal itself is the evidence
        third_refused = True
    reader = g1._budget(cap_root)
    calls = g1._ledger_calls(reader)
    inner = dmod._Inner()
    gate = g1.G1CognitionGate(inner, latch=g1.SendLatch(lambda: False), budget=reader)
    send_refused = ""
    try:
        gate.invoke(object())
    except g1.GateClosed as exc:
        send_refused = exc.reason_code
    checks.add("20 reservations fill the shared cap and the 21st is refused",
               reserved == g1.REQUEST_CAP and third_refused and calls == g1.REQUEST_CAP,
               {"reserved": reserved, "third_refused": third_refused, "calls": calls})
    checks.add("a full ledger refuses the next send before the inner transport",
               send_refused == g1.REASON_REQUEST_CAP and inner.calls == 0,
               {"raised": send_refused, "inner_calls": inner.calls})

    corrupt_root = scratch / "corrupt-state"
    corrupt_root.mkdir(parents=True, exist_ok=True)
    (corrupt_root / "life_request_budget.json").write_text("{not json", encoding="utf-8")
    corrupt = g1._budget(corrupt_root)
    corrupt_view = corrupt.view()
    corrupt_inner = dmod._Inner()
    corrupt_gate = g1.G1CognitionGate(corrupt_inner, latch=g1.SendLatch(lambda: False),
                                      budget=corrupt)
    corrupt_raised = ""
    try:
        corrupt_gate.invoke(object())
    except g1.GateClosed as exc:
        corrupt_raised = exc.reason_code
    checks.add("a corrupt ledger refuses the send before the inner transport",
               str(corrupt_view.get("state") or "") == "corrupt"
               and g1._ledger_calls(corrupt) is None
               and corrupt_raised == g1.REASON_LEDGER_UNREADABLE
               and corrupt_inner.calls == 0,
               {"state": corrupt_view.get("state"), "raised": corrupt_raised,
                "inner_calls": corrupt_inner.calls})

    accounting = {
        "expected_history": int(PROFILE.expected_history),
        "request_cap": int(g1.REQUEST_CAP),
        "worst_total": int(PROFILE.expected_history) + int(g1.REQUEST_CAP),
        "accounting_cap": int(g1.V06_TOTAL_ACCOUNTING_CAP),
        "utc_day_cap": int(g1.UTC_DAY_CAP),
    }
    checks.add("232 history + 20 cap stays under the 1000 accounting ceiling",
               accounting["worst_total"] <= accounting["accounting_cap"]
               and accounting["utc_day_cap"] == 300,
               accounting)
    checks.add("the accounting guard trips only at the ceiling and never for old batches",
               g1._total_accounting_exceeded(767) is False
               and g1._total_accounting_exceeded(768) is True
               and g1._total_accounting_exceeded(768, profile=g1.C_PROFILE) is False,
               {"at_767": g1._total_accounting_exceeded(767),
                "at_768": g1._total_accounting_exceeded(768)})
    return {"reserved": reserved, "calls": calls, "third_refused": third_refused,
            "send_refused": send_refused, "corrupt_state": corrupt_view.get("state"),
            "corrupt_send_refused": corrupt_raised, "accounting": accounting}


def _send_gate_checks(checks: Any, scratch: Path) -> dict[str, Any]:
    expired = scratch / "expired-window"
    now = g1._wall_now()
    g1._write_json(g1._window_path(expired), {
        "schema": "g1.window.v1", "run_id": PROFILE.run_id,
        "window_seconds": g1._profile_window_seconds(PROFILE),
        "started_monotonic": 0.0, "deadline_monotonic": 0.0,
        "started_wall": g1._iso(now - timedelta(minutes=30)),
        "deadline_wall": g1._iso(now - timedelta(seconds=5)),
    }, private=True)
    inner = dmod._Inner()
    budget_root = scratch / "gate-budget"
    budget_root.mkdir(parents=True, exist_ok=True)
    budget = g1._budget(budget_root)
    budget.ensure_window(allow_new=True)
    gate = g1.G1CognitionGate(inner, latch=g1.SendLatch(lambda: g1._deadline_passed(expired)),
                              budget=budget)
    raised = ""
    try:
        gate.invoke(object())
    except g1.GateClosed as exc:
        raised = exc.reason_code
    checks.add("an expired window refuses every send before the inner transport",
               raised == g1.REASON_DEADLINE_PASSED and inner.calls == 0,
               {"raised": raised, "inner_calls": inner.calls})
    return {"raised": raised, "inner_calls": inner.calls}


def _identity_negative_checks(checks: Any, scratch: Path) -> dict[str, Any]:
    wrong_root = (g1._check_root(g1.V06_D_PROFILE.root) or {}).get("reason_code")
    checks.add("the previous batch's root is refused as an identity mismatch",
               wrong_root == g1.REASON_BATCH_IDENTITY_MISMATCH, wrong_root)

    fake = scratch / "wrong-batch"
    g1._write_json(g1._experiment_dir(fake) / g1.PREREG_FILENAME, {
        "schema": "g1.real.prereg.v1", "batch": g1.V06_D_PROFILE.key,
        "run_id": g1.V06_D_PROFILE.run_id, "domain_id": g1.V06_D_PROFILE.domain_id,
        "root": str(fake), "state_root": str(fake / "one-day"),
        "deadline": g1.V06_D_PROFILE.deadline.isoformat(),
    }, private=True)
    wrong_prereg = g1.audit(fake, expected_root=fake)
    checks.add("a prereg written for another batch is refused",
               wrong_prereg.get("reason_code") == g1.REASON_BATCH_IDENTITY_MISMATCH,
               wrong_prereg.get("reason_code"))

    missing = scratch / "missing-latest-start"
    g1._write_json(g1._experiment_dir(missing) / g1.PREREG_FILENAME, {
        "schema": "g1.real.prereg.v1", "batch": PROFILE.key,
        "run_id": PROFILE.run_id, "domain_id": PROFILE.domain_id,
        "root": str(missing), "state_root": str(missing / "one-day"),
        "deadline": PROFILE.deadline.isoformat(),
    }, private=True)
    missing_latest = g1.audit(missing, expected_root=missing)
    checks.add("a prereg without the E latest-start line is refused",
               missing_latest.get("reason_code") == g1.REASON_BATCH_IDENTITY_MISMATCH,
               missing_latest.get("reason_code"))
    return {"wrong_root": wrong_root,
            "wrong_prereg": wrong_prereg.get("reason_code"),
            "missing_latest_start": missing_latest.get("reason_code")}


def _time_window_checks(checks: Any, root: Path) -> dict[str, Any]:
    with g1.offline_clock_at(PROFILE.latest_start + timedelta(seconds=1)):
        after_latest = g1.start_run(root, expected_root=root, execute_approved=True,
                                    offline=True)
    window_written = g1._window_path(root).exists()
    with g1.offline_clock_at(PROFILE.deadline + timedelta(seconds=1)):
        after_deadline = g1.start_run(root, expected_root=root, execute_approved=True,
                                      offline=True)
    window_written_after = g1._window_path(root).exists()
    checks.add("starting after 2026-11-04 00:15 CST is refused before the window is written",
               after_latest.get("reason_code") == g1.REASON_LATEST_START_PASSED
               and not window_written,
               {"reason": after_latest.get("reason_code"), "window": window_written})
    checks.add("starting after 2026-11-04 00:50 CST is refused before the window is written",
               after_deadline.get("reason_code") == g1.REASON_DEADLINE_PASSED
               and not window_written_after,
               {"reason": after_deadline.get("reason_code"),
                "window": window_written_after})
    return {"after_latest": after_latest.get("reason_code"),
            "after_deadline": after_deadline.get("reason_code"),
            "window_written": window_written}


def _cleanup_deadline_checks(checks: Any) -> dict[str, Any]:
    now = 1000.0
    window = float(g1._profile_window_seconds(PROFILE))
    e_limit = g1._natural_phase_timeout(PROFILE, now + window, now=now)
    old = g1._natural_phase_timeout(g1.E_PROFILE, now + g1.WINDOW_SECONDS, now=now)
    checks.add("E stops its phase tree 60s after a 1800s window; old batches keep 120s",
               abs(e_limit - (window + 60.0)) < 1e-9
               and abs(old - (g1.WINDOW_SECONDS + 120.0)) < 1e-9,
               {"e": e_limit, "old": old})
    started = time.monotonic()
    timed_out = False
    try:
        subprocess.run([sys.executable, "-B", "-c", "import time; time.sleep(0.4)"],
                       timeout=0.15, capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        timed_out = True
    elapsed = time.monotonic() - started
    checks.add("a phase process over its bounded timeout is actually terminated",
               timed_out and elapsed < 5.0, {"elapsed": round(elapsed, 3)})
    return {"e_seconds": e_limit, "old_seconds": old, "timed_out": timed_out,
            "elapsed": round(elapsed, 3)}


def _offline_check() -> dict[str, Any]:
    started = time.monotonic()
    checks = dmod._Checks()
    scratch = Path(tempfile.mkdtemp(prefix=OFFLINE_PREFIX))
    sys.stderr.write("scratch root: %s\n" % scratch)
    sys.stderr.flush()
    repo_before = dmod._stat(REPO_LIFECYCLE)
    parent_env_before = {name: os.environ.get(name)
                         for name in (g1.V06_D_AMBIENT_ENV,
                                      "WORLD_BODY_INTENT_MUTATION_GATE")}
    payload: dict[str, Any] = {"ok": False, "scratch_root": str(scratch),
                               "checks": [], "batch": BATCH}
    with g1._use_batch(BATCH), g1.offline_clock(PROFILE), g1._offline_network_guard(scratch):
        parent_guard = g1._offline_guard_selfcheck()
        payload["network_guard"] = parent_guard
        checks.add("the parent entry refuses AF_INET/AF_INET6/DNS/connect but keeps AF_UNIX",
                   all(parent_guard.get(name) is True for name in
                       ("inet_refused", "inet6_refused", "dns_refused",
                        "connect_refused", "unix_ok")),
                   parent_guard)
        payload["identity"] = _identity_checks(checks)
        payload["ledger_scope"] = _ledger_scope_checks(checks)
        payload["cleanup_deadline"] = _cleanup_deadline_checks(checks)

        root = scratch / "instance-root"
        prepared = g1.prepare(root, expected_root=root, offline=True)
        prereg = g1._read_json(g1._experiment_dir(root) / g1.PREREG_FILENAME)
        caps = dict(prereg.get("caps") or {})
        observation = dict(prereg.get("observation") or {})
        checks.add("prepare reports zero model calls and zero requests",
                   prepared.get("ok") is True
                   and prepared.get("model_called") is False
                   and prepared.get("request_sent") is False,
                   prepared.get("reason_code"))
        checks.add("prepare records the E identity, 232 history and 1800s caps",
                   prepared.get("batch") == BATCH
                   and prepared.get("run_id") == PROFILE.run_id
                   and prepared.get("domain_id") == PROFILE.domain_id
                   and prereg.get("batch") == BATCH
                   and prereg.get("deadline") == PROFILE.deadline.isoformat()
                   and prereg.get("latest_start") == PROFILE.latest_start.isoformat()
                   and int(caps.get("expected_history") or 0) == PROFILE.expected_history
                   and int(caps.get("request_cap") or 0) == g1.REQUEST_CAP
                   and int(caps.get("window_seconds") or 0) == 1800
                   and int(caps.get("max_ticks") or 0) == 900
                   and int(caps.get("trace_max_records") or 0) == 4000
                   and int(caps.get("check_interval_minutes") or 0) == g1.CHECK_INTERVAL_MINUTES
                   and int(caps.get("utc_day_cap") or 0) == g1.UTC_DAY_CAP
                   and int(caps.get("total_accounting_cap") or 0)
                   == g1.V06_TOTAL_ACCOUNTING_CAP,
                   {"caps": caps, "prereg": {k: prereg.get(k) for k in
                                             ("batch", "deadline", "latest_start")}})
        checks.add("prepare records the E ambient switch and off gate as closed facts",
                   observation.get("ambient_events") == "enabled"
                   and observation.get("ambient_env") == g1.V06_D_AMBIENT_ENV
                   and prereg.get("world_intent_gate") == "off",
                   observation)
        no_flag = g1.start_run(root, expected_root=root, execute_approved=False, offline=True)
        checks.add("run without --execute-approved is refused",
                   no_flag.get("reason_code") == g1.REASON_EXECUTE_FLAG_MISSING,
                   no_flag.get("reason_code"))
        payload["prepare"] = dmod._safe_result(prepared)
        payload["caps"] = caps
        payload["budget"] = _budget_checks(checks, scratch)
        payload["send_gate"] = _send_gate_checks(checks, scratch)
        payload["identity_negative"] = _identity_negative_checks(checks, scratch)
        payload["time_window"] = _time_window_checks(checks, root)
        if time.monotonic() - started > MAX_OFFLINE_SECONDS:
            checks.add("offline check stayed inside its own wall budget", False,
                       {"limit": MAX_OFFLINE_SECONDS})
        else:
            checks.add("offline check stayed inside its own wall budget", True)

        # 正式 phase：真实子进程里的标准 Supply/World/runtime，脚本 Decision/内容。
        run = g1.start_run(root, expected_root=root, execute_approved=True, offline=True,
                           capture_candidates=True)
        natural = dict(run.get("natural") or {})
        world = dict(natural.get("world") or {})
        isolation = dict(natural.get("isolation") or {})
        rounds = [dict(row) for row in (natural.get("rounds") or ())]
        wakes = [dict(row.get("wake") or {}) for row in rounds]
        start_wakes = [row for row in wakes if str(row.get("decision") or "") == "START"]
        checks.add("the parent entry ran exactly one natural phase",
                   run.get("ok") is True and natural.get("phase") == g1.PHASE_NATURAL
                   and run.get("p1") is None and run.get("p2") is None,
                   {"ok": run.get("ok"), "stop": run.get("stop_reason")})
        checks.add("the offline phase used scripted transports with zero product requests",
                   natural.get("offline") is True and "model" not in natural,
                   {"offline": natural.get("offline"), "model_key": "model" in natural})
        checks.add("the run reports the E window and tick/trace bounds",
                   int((run.get("window") or {}).get("window_seconds") or 0) == 1800
                   and g1._profile_max_ticks(PROFILE) == 900
                   and g1._profile_trace_max_records(PROFILE) == 4000,
                   {"window": (run.get("window") or {}).get("window_seconds")})
        payload["product_requests"] = 0
        child_guard = dict(natural.get("offline_child_guard") or {})
        payload["child_network_guard"] = child_guard
        checks.add("both own owner children self-report the same network refusal",
                   child_guard.get("ok") is True
                   and int(child_guard.get("found") or 0) == 2
                   and all(all(row.get(name) is True for name in
                               ("inet_refused", "inet6_refused", "dns_refused",
                                "connect_refused", "unix_ok"))
                           for row in (child_guard.get("children") or ())),
                   child_guard)
        close_world = dict(natural.get("close_world") or {})
        close_supply = dict(natural.get("close_supply") or {})
        close_runtime = dict(natural.get("close_runtime") or {})
        checks.add("the phase released its runtime and stopped its own owners",
                   close_runtime.get("closed") is True
                   and close_world.get("socket_live_after") is False
                   and close_supply.get("socket_live_after") is False,
                   {"runtime": close_runtime.get("closed"),
                    "world_live": close_world.get("socket_live_after"),
                    "supply_live": close_supply.get("socket_live_after")})
        checks.add("the phase child used the standard World with an explicit off gate",
                   world.get("started") is True and world.get("ready") is True
                   and world.get("status") == "READY" and world.get("gate") == "off"
                   and world.get("child_env_gate") == "off",
                   world)
        checks.add("the World child itself carries the E ambient switch",
                   world.get("child_env_ambient") == "1", world)
        checks.add("the phase child wrote only inside the scratch root",
                   isolation.get("gate") == "off"
                   and isolation.get("world_home_is_instance") is True
                   and isolation.get("world_home_is_within_root") is True
                   and isolation.get("registry_is_within_root") is True
                   and isolation.get("lifecycle_is_within_root") is True
                   and isolation.get("lifecycle_exists") is True,
                   isolation)
        checks.add("the scripted START produced a formal action in this phase",
                   bool(start_wakes) and all(bool(row.get("action_id")) for row in start_wakes),
                   {"start_rounds": len(start_wakes)})
        checks.add("the scripted START is refused before submission, not run in the background",
                   bool(start_wakes)
                   and all(str(row.get("outcome") or "") == "STEP_REFUSED"
                           and str(row.get("action_status") or "") == "PREPARED"
                           for row in start_wakes),
                   [{"outcome": row.get("outcome"), "action_status": row.get("action_status")}
                    for row in start_wakes])
        effect = dmod._gate_off_refusal(dmod._artifact_view(root))
        checks.add("the attempt is a PRE_SUBMIT_FAILED/GATE_OFF refusal with zero world effect",
                   effect.get("qualifying") is True, effect)
        payload["formal_effect"] = effect
        payload["world"] = dmod._safe_natural(natural)["world"]
        payload["isolation"] = dmod._safe_natural(natural)["isolation"]
        payload["observation"] = dmod._safe_natural(natural)["observation"]
        natural_view = dmod._safe_natural(natural)
        # E 没有 D 的 client 边界捕获；不把一组全 None 的键当成新观测。
        natural_view.pop("ambient_client", None)
        payload["natural"] = natural_view
        payload["run"] = dmod._safe_result(run)

        again = g1.start_run(root, expected_root=root, execute_approved=True, offline=True)
        checks.add("a second run on the same root is refused",
                   again.get("reason_code") == g1.REASON_ALREADY_STARTED,
                   again.get("reason_code"))
        audited = g1.audit(root, expected_root=root)
        checks.add("audit sees the E identity, gate, latest start, window and one phase",
                   audited.get("ok") is True and audited.get("batch") == BATCH
                   and audited.get("run_id") == PROFILE.run_id
                   and audited.get("world_intent_gate") == "off"
                   and audited.get("latest_start") == PROFILE.latest_start.isoformat()
                   and audited.get("window_seconds") == 1800
                   and audited.get("max_ticks") == 900
                   and audited.get("trace_max_records") == 4000
                   and list((audited.get("phases") or {})) == [g1.PHASE_NATURAL],
                   {"ok": audited.get("ok"), "batch": audited.get("batch"),
                    "phases": list((audited.get("phases") or {})),
                    "window_seconds": audited.get("window_seconds")})
        ledger = dict(audited.get("ledger") or {})
        checks.add("the audit ledger belongs to the E run and its own cap",
                   ledger.get("run_id") == PROFILE.run_id
                   and int(ledger.get("run_window_max") or 0) == g1.REQUEST_CAP,
                   ledger)
        payload["audit"] = {"ok": audited.get("ok"), "batch": audited.get("batch"),
                            "run_id": audited.get("run_id"),
                            "world_intent_gate": audited.get("world_intent_gate"),
                            "latest_start": audited.get("latest_start"),
                            "window_seconds": audited.get("window_seconds"),
                            "max_ticks": audited.get("max_ticks"),
                            "trace_max_records": audited.get("trace_max_records"),
                            "phases": list((audited.get("phases") or {}))}
        payload["again"] = {"reason_code": again.get("reason_code")}

    repo_after = dmod._stat(REPO_LIFECYCLE)
    checks.add("the repository default World lifecycle got zero new writes",
               repo_before == repo_after, {"before": repo_before, "after": repo_after})
    parent_env_after = {name: os.environ.get(name) for name in parent_env_before}
    checks.add("the parent process environment keeps its own ambient/gate values",
               parent_env_after == parent_env_before,
               {"before": parent_env_before, "after": parent_env_after})
    checks.add("the real root and authority were never created",
               not Path(PROFILE.root).exists()
               and not Path(PROFILE.authority_dir).exists()
               and not Path(PROFILE.authority_db).exists(),
               {"root": str(PROFILE.root), "authority": str(PROFILE.authority_dir)})

    payload["checks"] = checks.rows
    payload["ok"] = checks.ok
    payload["elapsed_seconds"] = round(time.monotonic() - started, 3)
    # 输出安全扫描：白名单对象里不得出现密钥/提示词/正文/异常原文 token。
    blob = json.dumps(payload, ensure_ascii=False, default=str)
    forbidden = ["offline-not-a-key", "sk-", "Authorization", "Bearer ",
                 "system_prompt", "user_prompt", "completion", "reasoning"]
    hits = [token for token in forbidden if token in blob]
    checks.add("the offline report contains no key, prompt or body text", not hits, hits)
    payload["checks"] = checks.rows
    payload["ok"] = checks.ok
    return payload


def cmd_offline_check(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="v06_world_natural_e offline-check")
    parser.parse_args(list(argv))
    payload = _offline_check()
    return dmod._emit(payload, code=EXIT_OK if payload.get("ok") else EXIT_FAILED)


COMMANDS = {
    "offline-check": cmd_offline_check,
    "prepare": cmd_prepare,
    "audit": cmd_audit,
    "run": cmd_run,
}


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] in {"-h", "--help"}:
        sys.stderr.write(__doc__ or "")
        return EXIT_OK if arguments else EXIT_REFUSED
    handler = COMMANDS.get(arguments[0])
    if handler is None:
        sys.stderr.write("unknown command: %s\n" % (arguments[0],))
        return EXIT_REFUSED
    return int(handler(arguments[1:]))


if __name__ == "__main__":
    raise SystemExit(main())

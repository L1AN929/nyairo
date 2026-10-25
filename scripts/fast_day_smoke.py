#!/usr/bin/env python3
"""显式压缩逻辑日的**零模型完整冒烟**（一条命令，不需要任何手工 env/准备步骤）。

它自己建一个唯一的 ``/tmp/nyairo-fastday-smoke-*`` 根并保留，然后真的走一遍：

    prepare（真 Supply/World owner、新 Memory、公版《桃花源记》节选）
      -> status（双时间/domain）
      -> start（真 resident + 本根自己的 Supply/World 子进程，--no-act --offline-scripted）
      -> pause / resume（逻辑时钟冻结/恢复，不发新请求）
      -> stop（只收本次自有的 supervisor/resident/owner 子进程）
      -> 同 run 重启 -> 再 stop
      -> 最终 status（装配状态、Memory、实验上限、产品请求计数必须为 0）

边界：产品模型调用明确为 0（启动前清掉 ``NYA02_MODEL_*``）；认知/写作面的一个
**不发请求的既有确定性 writer 占位**只为把 runtime 装配起来，summary 会标
``offline_scripted: true``。这不是「她的自主生活成功」，只证明这条链能跑、能停、
能同 run 恢复；也没有任何真实 24h/soak 结论。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
FAST_DAY = REPO_ROOT / "scripts" / "fast_day.py"
RUN_ID = "smoke"
LEDGER_RUN_ID = "run:smoke"
MAX_NEW_REQUESTS = 5
LOGICAL_SECONDS = 120
MODEL_ENV_NAMES = ("NYA02_MODEL_BASE_URL", "NYA02_MODEL_NAME", "NYA02_MODEL_API_KEY",
                   "NYA02_MODEL_PROVIDER")


def _env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("NYA02_MODEL_") and key not in MODEL_ENV_NAMES}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _cli(*arguments: str, timeout: float = 900.0) -> tuple[int, dict[str, Any]]:
    result = subprocess.run(
        [sys.executable, "-B", str(FAST_DAY), *arguments],
        capture_output=True, text=True, timeout=timeout, cwd=str(REPO_ROOT), env=_env())
    text = result.stdout.strip() or "{}"
    try:
        payload = json.loads(text)
    except ValueError:
        payload = {"_stdout": text[-2000:], "_stderr": result.stderr[-2000:]}
    return result.returncode, payload


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return dict(data) if isinstance(data, dict) else {}


def _wait(predicate, *, timeout: float, interval: float = 1.0) -> bool:
    deadline = time.monotonic() + float(timeout)
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class Checks:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def add(self, name: str, ok: bool, detail: Any = None) -> None:
        self.rows.append({"check": name, "ok": bool(ok), "detail": detail})

    @property
    def ok(self) -> bool:
        return bool(self.rows) and all(row["ok"] for row in self.rows)


def main() -> int:
    # mkdtemp 会建目录；prepare 要求**全新**根，所以实验根是它的一个尚未存在的子目录。
    base = Path(tempfile.mkdtemp(prefix="nyairo-fastday-smoke-", dir="/tmp"))
    root = base / "instance"
    state_root = root / "state"
    checks = Checks()
    started_processes: list[subprocess.Popen] = []
    report: dict[str, Any] = {"root": str(root), "run_id": RUN_ID,
                              "ledger_run_id": LEDGER_RUN_ID,
                              "product_model_requests_allowed": 0,
                              "note": ("零模型冒烟：offline_scripted 是既有确定性 writer 占位，"
                                       "不是她的写作；这不是逻辑一天成功，也不是 24h 结论")}
    try:
        # -- 1. prepare：现成实例 + 真 owner + 新 Memory + 公版材料 --------------------
        code, prepared = _cli("prepare", "--root", str(root), "--run-id", RUN_ID,
                              "--logical-seconds", str(LOGICAL_SECONDS),
                              "--real-seconds", "60", "--max-new-requests",
                              str(MAX_NEW_REQUESTS), timeout=900.0)
        report["prepare"] = prepared
        state_root = Path(str(prepared.get("state_root") or state_root))
        checks.add("prepare_exit_0", code == 0 and bool(prepared.get("ok")), code)
        checks.add("prepare_provisioned", bool(prepared.get("provisioned")),
                   prepared.get("provision"))
        checks.add("prepare_new_run_identity",
                   prepared.get("ledger_run_id") == LEDGER_RUN_ID,
                   prepared.get("ledger_run_id"))
        initialized = (prepared.get("provision") or {}).get("initialized") or {}
        nested_instance = _read(root / "one-day" / "instance.json")
        checks.add("prepare_no_synthetic_work_seed",
                   initialized.get("work_artifact") == "none_seed_disabled"
                   and not str(nested_instance.get("work_artifact_id") or ""),
                   {"initialized": initialized,
                    "work_artifact_id": nested_instance.get("work_artifact_id")})
        memory = (prepared.get("provision") or {}).get("memory") or {}
        checks.add("prepare_memory_bound",
                   bool(memory.get("ok")) and memory.get("status") in {"READY", "BOUND"},
                   memory)
        material = (prepared.get("provision") or {}).get("material") or {}
        checks.add("prepare_public_domain_material",
                   bool(material.get("public_domain_excerpt"))
                   and material.get("provided_by") == "operator", material)
        checks.add("prepare_material_is_the_65_char_excerpt",
                   int(material.get("characters") or 0) == 65, material)
        checks.add("prepare_private_items_document_is_empty",
                   initialized.get("private_items_document") is True, initialized)
        if not checks.ok:
            return _finish(report, checks, started_processes)

        # -- 2b. 真实模式缺模型配置：具名非 0，且不起任何自有子进程 ----------------
        code, refused = _cli("start", "--root", str(root),
                             "--duration-seconds", "2", timeout=120.0)
        report["real_mode_refusal"] = refused
        checks.add("real_mode_requires_model_config",
                   code != 0 and refused.get("reason_code") == "NO_REAL_MODEL_CONFIG",
                   {"exit": code, "reason": refused.get("reason_code")})
        checks.add("real_mode_refusal_left_no_children", not _owned_alive(state_root),
                   _owned_pids(state_root))

        # -- 2. status：准备后未启动，双时间/域都要出现 ------------------------------
        code, status = _cli("status", "--root", str(root))
        report["status_prepared"] = status
        checks.add("status_prepared_exit_0", code == 0 and bool(status.get("ok")), code)
        checks.add("status_dual_time",
                   bool(status.get("logical", {}).get("at"))
                   and bool(status.get("real", {}).get("at")), status.get("logical"))
        checks.add("status_domain", status.get("domain_id") == "fastday:%s" % RUN_ID,
                   status.get("domain_id"))
        checks.add("status_run_identity",
                   status.get("ledger_run_id") == LEDGER_RUN_ID
                   and (status.get("clock") or {}).get("run_id") == LEDGER_RUN_ID,
                   {"ledger_run_id": status.get("ledger_run_id"),
                    "clock_run_id": (status.get("clock") or {}).get("run_id")})
        checks.add("status_experiment_cap",
                   (status.get("experiment") or {}).get("max_new_requests")
                   == MAX_NEW_REQUESTS, status.get("experiment"))

        # -- 3. start：真 resident + 自有 owner 子进程，零模型 ----------------------
        first = _start(root, log_name="start-1.log", duration_seconds=12)
        started_processes.append(first)
        running = _wait(lambda: bool(_read(state_root / "resident_summary.json")
                                     .get("runtime_assembly", {}).get("runtime_present")),
                        timeout=180.0)
        summary = _read(state_root / "resident_summary.json")
        assembly = summary.get("runtime_assembly") or {}
        report["resident_after_start"] = {
            "runtime_assembly": assembly,
            "time_domain": summary.get("time_domain"),
            "memory_binding": summary.get("memory_binding"),
            "offline_scripted": summary.get("offline_scripted"),
            "world": summary.get("world"),
        }
        checks.add("resident_runtime_present", running and bool(assembly.get("runtime_present")),
                   assembly)
        checks.add("resident_not_supply_blocked",
                   assembly.get("reason") not in {"SUPPLY_CONFIGURATION_MISSING",
                                                  "NOT_STARTED"}, assembly.get("reason"))
        checks.add("owner_ready", bool(assembly.get("owner_ready"))
                   or (summary.get("world") or {}).get("status") == "READY",
                   summary.get("world"))
        checks.add("resident_memory_loaded",
                   bool((summary.get("memory_binding") or {}).get("database_configured")),
                   summary.get("memory_binding"))
        checks.add("resident_compressed_domain",
                   (summary.get("time_domain") or {}).get("mode") == "COMPRESSED_DEMO",
                   summary.get("time_domain"))
        checks.add("resident_offline_scripted_marked", summary.get("offline_scripted") is True)

        # -- 4. 有界窗口自然结束：自己的子进程必须全部收尾，run 不能变成终态 --------
        _wait(lambda: first.poll() is not None, timeout=180.0)
        checks.add("first_window_exited", first.poll() is not None, first.poll())
        checks.add("no_owned_children_left", not _owned_alive(state_root),
                   _owned_pids(state_root))
        clock_after_first = _read(state_root / "fastday" / "fastday_clock.json")
        checks.add("first_window_not_terminal",
                   clock_after_first.get("state") in {"RUNNING", "FROZEN"},
                   clock_after_first.get("state"))

        # -- 5. 同 run 重启：starts 递增、逻辑时间不倒退 ----------------------------
        second = _start(root, log_name="start-2.log", duration_seconds=120)
        started_processes.append(second)
        restarted = _wait(lambda: (_read(state_root / "resident_summary.json")
                                   .get("starts") or 0) >= 2, timeout=180.0)
        summary_second = _read(state_root / "resident_summary.json")
        checks.add("restart_same_run", restarted, summary_second.get("starts"))
        checks.add("restart_domain_unchanged",
                   (summary_second.get("time_domain") or {}).get("domain_id") ==
                   "fastday:%s" % RUN_ID, summary_second.get("time_domain"))
        checks.add("restart_runtime_present",
                   bool((summary_second.get("runtime_assembly") or {}).get("runtime_present")),
                   summary_second.get("runtime_assembly"))

        # -- 6. pause / resume：冻结逻辑时钟，恢复不重放 -----------------------------
        code, paused = _cli("pause", "--root", str(root))
        report["pause"] = paused
        checks.add("pause_exit_0", code == 0 and bool(paused.get("ok")), code)
        frozen = _wait(lambda: (_read(state_root / "fastday" / "fastday_clock.json")
                                .get("state") == "FROZEN"), timeout=60.0)
        checks.add("pause_freezes_clock", frozen)
        frozen_clock = _read(state_root / "fastday" / "fastday_clock.json")
        time.sleep(3.0)
        still = _read(state_root / "fastday" / "fastday_clock.json")
        checks.add("paused_clock_does_not_advance",
                   frozen_clock.get("logical_checkpoint") == still.get("logical_checkpoint"),
                   {"before": frozen_clock.get("logical_checkpoint"),
                    "after": still.get("logical_checkpoint")})
        code, resumed = _cli("resume", "--root", str(root))
        report["resume"] = resumed
        checks.add("resume_exit_0", code == 0 and bool(resumed.get("ok")), code)
        checks.add("resume_running",
                   _wait(lambda: (_read(state_root / "fastday" / "fastday_clock.json")
                                  .get("state") == "RUNNING"), timeout=60.0))

        # -- 7. stop：只收自有子进程 -----------------------------------------------
        code, stopped_again = _cli("stop", "--root", str(root), timeout=180.0)
        report["stop_second"] = stopped_again
        checks.add("stop_second_exit_0", code == 0 and bool(stopped_again.get("ok")), code)
        _wait(lambda: second.poll() is not None, timeout=120.0)
        checks.add("second_supervisor_exited", second.poll() is not None, second.poll())
        _wait(lambda: not _owned_alive(state_root), timeout=60.0)
        checks.add("stop_left_no_owned_children", not _owned_alive(state_root),
                   _owned_pids(state_root))

        # -- 6. 最终 status：装配/Memory/上限/产品请求 0 ---------------------------
        code, final = _cli("status", "--root", str(root))
        report["status_final"] = final
        checks.add("status_final_exit_0", code == 0 and bool(final.get("ok")), code)
        experiment = final.get("experiment") or {}
        checks.add("experiment_zero_requests",
                   int(experiment.get("used") or 0) == 0
                   and int(experiment.get("max_new_requests") or 0) == MAX_NEW_REQUESTS,
                   experiment)
        ledger = _read(state_root / "life_request_budget.json")
        calls = 0
        for window in (ledger.get("windows") or {}).values():
            calls += int((window or {}).get("calls") or 0)
        checks.add("ledger_zero_product_requests", calls == 0, {"run_window_calls": calls})
        resident = final.get("resident") or {}
        checks.add("final_runtime_assembly_reported",
                   (resident.get("assembly") or {}).get("runtime_present") is True,
                   resident.get("assembly"))
        checks.add("final_execution_mode_is_offline_scripted",
                   final.get("execution_mode") == "offline_scripted",
                   {"execution_mode": final.get("execution_mode"),
                    "model": final.get("model")})
        wall = final.get("wall_budget") or {}
        consumed = float(wall.get("consumed_seconds") or 0.0)
        checks.add("wall_budget_accumulated",
                   0 < consumed <= float(wall.get("max_seconds") or 0)
                   and abs(float(wall.get("remaining_seconds") or -1)
                           - (float(wall.get("max_seconds") or 0) - consumed)) < 0.01,
                   wall)
        record = _read(state_root / "fastday" / "run.json")
        start_wall = record.get("wall_budget_start") or {}
        checks.add("wall_limit_within_remaining",
                   float(record.get("duration_limit_seconds") or 0.0) <=
                   float(start_wall.get("remaining_seconds") or 0.0) + 0.001,
                   {"duration_limit_seconds": record.get("duration_limit_seconds"),
                    "remaining_before": start_wall.get("remaining_seconds")})
        checks.add("not_real_24h", final.get("not_real_24h") is True)

        # -- 7. 真实认知预算读面与预留边界（独立目录，零 socket、零模型发送） -------
        # 复现 2026-10-23 fastday-a：实验闸门包住实例账本后，认知预算被读成 96/0。
        probe = _budget_boundary_probe(base)
        report["budget_boundary_probe"] = probe
        initial = probe["initial"]
        checks.add("budget_probe_empty_ledger_cognition_available",
                   initial["utc_day_calls"] == 0
                   and initial["cognition_used_today"] == 0
                   and initial["cognition_remaining_today"] == 96
                   and initial["may_start_wake"] == [True, "ALLOWED"], initial)
        checks.add("budget_probe_experiment_cap_is_its_own_surface",
                   (initial["experiment_cap"] or {}).get("used") == 0
                   and (initial["experiment_cap"] or {}).get("max_new_requests")
                   == probe["experiment_limit"], initial["experiment_cap"])
        checks.add("budget_probe_utc_day_is_real",
                   initial["utc_day"] == datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                   initial["utc_day"])
        after = probe["after_one_send"]
        checks.add("budget_probe_one_send_reserves_both_counters",
                   after["transport_calls"] == 1 and after["utc_day_calls"] == 1
                   and after["experiment_used"] == 1
                   and after["cognition_used_today"] == 1, after)
        checks.add("budget_probe_cap_exhausted_refuses",
                   (probe.get("cap_exhausted_refusal") or {}).get("reason_code")
                   == "EXPERIMENT_CAP_EXHAUSTED", probe.get("cap_exhausted_refusal"))
        logical = probe["after_logical_day"]
        checks.add("budget_probe_logical_day_does_not_advance_the_real_ledger",
                   logical["logical_elapsed_seconds"] > 23 * 3600
                   and logical["utc_day"] == initial["utc_day"]
                   and logical["utc_day_calls"] == 2
                   and logical["experiment_used"] == 2, logical)
        corrupt = probe["corrupt_ledger"]
        checks.add("budget_probe_corrupt_ledger_fails_closed",
                   corrupt["state"] == "corrupt"
                   and corrupt["utc_day_calls"] is None
                   and corrupt["cognition_used_today"] == 96
                   and corrupt["may_start_wake"]
                   == [False, "COGNITION_BUDGET_EXHAUSTED"], corrupt)

        # -- 8. 显式 300/14/286：真实配置装配 + 每请求预留边界（仍零模型） ----------
        explicit = _explicit_300_boundary_probe(base)
        report["explicit_300_probe"] = explicit
        config = explicit["config"]
        checks.add("explicit_300_prepare_exit_0",
                   config["prepare_exit"] == 0 and config["status_exit"] == 0, config)
        checks.add("explicit_300_configuration_bound",
                   config["fastday"] == {"request_total_limit": 300,
                                         "historical_requests": 14,
                                         "max_new_requests": 286,
                                         "max_cognition_calls_per_day": 300}
                   and config["status_request_limits"] == {
                       "total_limit": 300, "historical_requests": 14,
                       "max_new_requests": 286, "cognition_calls_per_day": 300},
                   config)
        checks.add("explicit_300_cap_file_records_authorization",
                   config["experiment_file"]["max_new_requests"] == 286
                   and config["experiment_file"]["authorized_total_limit"] == 300
                   and config["experiment_file"]["historical_requests"] == 14
                   and config["experiment_file"]["baseline_run_window_calls"] == 0,
                   config["experiment_file"])
        initial_300 = explicit["initial"]
        checks.add("explicit_300_empty_ledger_cognition_available",
                   initial_300["utc_day_calls"] == 0
                   and initial_300["cognition_used_today"] == 0
                   and initial_300["cognition_remaining_today"] == 300
                   and initial_300["may_start_wake"] == [True, "ALLOWED"], initial_300)
        checks.add("explicit_300_experiment_cap_is_its_own_surface",
                   (initial_300["experiment_cap"] or {}).get("max_new_requests") == 286
                   and (initial_300["experiment_cap"] or {}).get("authorized_total_limit")
                   == 300
                   and (initial_300["experiment_cap"] or {}).get("historical_requests") == 14,
                   initial_300["experiment_cap"])
        one = explicit["after_one_send"]
        checks.add("explicit_300_one_send_reserves_both_counters",
                   one["transport_calls"] == 1 and one["utc_day_calls"] == 1
                   and one["experiment_used"] == 1
                   and one["cognition_remaining_today"] == 299, one)
        at_limit = explicit["at_limit"]
        checks.add("explicit_300_287th_refused_before_send",
                   at_limit["transport_calls"] == 286
                   and at_limit["utc_day_calls"] == 286
                   and at_limit["experiment_used"] == 286
                   and at_limit["experiment_remaining"] == 0
                   and at_limit["cognition_remaining_today"] == 14
                   and at_limit["ledger_utc_day_max"] == 300
                   and at_limit["ledger_run_window_max"] == 300, at_limit)
        checks.add("explicit_300_refusal_is_named_and_never_sent",
                   (explicit["refusal"] or {}).get("reason_code")
                   == "EXPERIMENT_CAP_EXHAUSTED"
                   and explicit["refusal"]["transport_calls_before"] == 286
                   and explicit["refusal"]["transport_calls_after"] == 286,
                   explicit["refusal"])
        live = explicit.get("live_instance") or {}
        live_env = live.get("env") or {}
        live_budget = live.get("budget") or {}
        live_requests = live_budget.get("requests") or {}
        checks.add("explicit_300_live_instance_env",
                   live.get("prepare_exit") == 0
                   and live_env.get("CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_UTC_DAY") == "300"
                   and live_env.get("CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_RUN_WINDOW") == "300"
                   and live_env.get("CHIYO_AUTONOMOUS_MAX_COGNITION_CALLS_PER_DAY") == "300"
                   and live_env.get("CHIYO_AUTONOMOUS_RUN_ID") == "run:smoke-300-live",
                   live_env)
        checks.add("explicit_300_live_resident_budget",
                   (live.get("runtime_assembly") or {}).get("runtime_present") is True
                   and (live.get("time_domain") or {}).get("mode") == "COMPRESSED_DEMO"
                   and bool((live.get("memory_binding") or {}).get("database_configured"))
                   and live_requests.get("utc_day_max") == 300
                   and live_requests.get("run_window_max") == 300
                   and live_budget.get("max_cognition_calls_per_day") == 300
                   and (live_requests.get("experiment_cap") or {}).get("max_new_requests")
                   == 286
                   and int(live_requests.get("utc_day_calls") or 0) == 0
                   and live.get("process_exited") is True, live)
        return _finish(report, checks, started_processes)
    finally:
        for process in started_processes:
            if process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass


def _budget_boundary_probe(base: Path) -> dict[str, Any]:
    """零 socket、零 provider：走一遍真实认知预算的读面与预留边界。

    它用与 resident 相同的装配（实验闸门包住实例 ``LifeRequestBudget``，再交给
    cycle 的 ``RoundBudget``）和一次**离线发送替身**，证明：

    * 空账本时认知可用（``utc_day_calls`` 是 0，不是把 96 读成已用尽）；
    * 一次认知发送前预留会同时推进真实账本与实验高水位；
    * 实验上限用满、账本损坏时仍然 fail closed；
    * 逻辑日快进不改变真实 UTC 日，也不给实验上限退款。

    它跑在独立的 ``base/budget-boundary`` 目录，不碰主实例的 run/账本/时钟。
    """

    for entry in (str(REPO_ROOT), str(REPO_ROOT / "components"),
                  str(REPO_ROOT / "components" / "life" / "scripts")):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    import autonomous_life_cycle as lifecycle
    import demo_business_clock as dbc
    import life_request_budget as lrb

    root = base / "budget-boundary"
    state_root = root / "state"
    run_id = "run:smoke-budget"
    limit = 2
    dbc.ExperimentRequestCap(root, run_id=run_id, max_new_requests=limit).create()
    ledger = lrb.LifeRequestBudget(state_root, utc_day_max=96,
                                   run_window_max=96, run_id=run_id)
    ledger.ensure_window(allow_new=True)
    cap = dbc.ExperimentRequestCap(root, run_id=run_id, max_new_requests=limit)
    guarded = dbc.CappedRequestBudget(ledger, cap)

    def _cycle_budget():
        return lifecycle.RoundBudget(state_root, max_rounds_per_day=32,
                                     max_cognition_calls_per_day=96,
                                     requests=guarded)

    class _OfflineSendDouble:
        send_split_capable = True

        def __init__(self) -> None:
            self.calls = 0

        def invoke(self, request, *, schema_retry_index: int = 0):
            self.calls += 1
            return {"ok": True}

    inner = _OfflineSendDouble()
    adapter = lrb.BudgetedCognitionAdapter(inner, guarded)
    initial_view = guarded.view()
    probe: dict[str, Any] = {
        "root": str(root),
        "run_id": run_id,
        "experiment_limit": limit,
        "transport": "offline_send_double: no socket, no provider, 0 model calls",
        "initial": {
            "state": initial_view.get("state"),
            "utc_day": initial_view.get("utc_day"),
            "utc_day_calls": initial_view.get("utc_day_calls"),
            "cognition_used_today": _cycle_budget().cognition_used_today(),
            "cognition_remaining_today": _cycle_budget().cognition_remaining(),
            "may_start_wake": list(_cycle_budget().may_start_wake()),
            "experiment_cap": initial_view.get("experiment_cap"),
        },
    }
    adapter.invoke({"kind": "decision"})
    after_view = guarded.view()
    probe["after_one_send"] = {
        "transport_calls": inner.calls,
        "utc_day_calls": after_view.get("utc_day_calls"),
        "experiment_used": (after_view.get("experiment_cap") or {}).get("used"),
        "cognition_used_today": _cycle_budget().cognition_used_today(),
    }
    adapter.invoke({"kind": "decision"})
    refusal: Optional[dict[str, Any]] = None
    try:
        adapter.invoke({"kind": "decision"})
    except lrb.RequestBudgetError as exc:
        refusal = {"reason_code": exc.reason_code, "detail": exc.detail}
    if refusal is None:
        refusal = {"reason_code": None, "detail": "the exhausted reservation was not refused"}
    refusal["transport_calls"] = inner.calls
    probe["cap_exhausted_refusal"] = refusal

    # 逻辑日快进：真实账本与实验上限都不能被「新的一天」刷新。
    wall = datetime(2026, 10, 23, 8, 0, tzinfo=timezone.utc)
    dbc.create_clock(root, run_id=run_id, rate=24.0, wall=lambda: wall)
    value = 500.0

    def mono() -> float:
        return value

    authority = dbc.BusinessClockAuthority(root, run_id=run_id, rate=24.0,
                                           monotonic=mono, wall=lambda: wall, pid=784)
    authority.acquire()
    value += 3600.0  # 业务上过去 24 小时
    logical_view = guarded.view()
    probe["after_logical_day"] = {
        "logical_elapsed_seconds": (authority.now() - wall).total_seconds(),
        "utc_day": logical_view.get("utc_day"),
        "utc_day_calls": logical_view.get("utc_day_calls"),
        "experiment_used": (logical_view.get("experiment_cap") or {}).get("used"),
    }

    # 坏账：不是 0，而是既有 fail-closed 语义（认知预算按已耗尽处理）。
    ledger.path.write_text("{not json", encoding="utf-8")
    corrupt_view = guarded.view()
    probe["corrupt_ledger"] = {
        "state": corrupt_view.get("state"),
        "utc_day_calls": corrupt_view.get("utc_day_calls"),
        "cognition_used_today": _cycle_budget().cognition_used_today(),
        "may_start_wake": list(_cycle_budget().may_start_wake()),
    }
    return probe


def _explicit_300_boundary_probe(base: Path) -> dict[str, Any]:
    """显式新授权（总限 300/历史 14/新增 286）的装配与真实认知预留边界。

    先用真实 CLI ``prepare --clock-only`` 写一次显式额度，核对 fastday.json 与
    ``experiment_budget.json`` 的绑定；然后在独立目录里用与 resident 相同的装配
    （``CappedRequestBudget`` 包住 ``LifeRequestBudget``，交给 ``RoundBudget``）
    和**离线发送替身**跑满 286 次，证明第 287 次在发送前被拒、transport 计数不超。
    不建 socket、不调用 provider；不是逻辑一天或自主生活结论。
    """

    for entry in (str(REPO_ROOT), str(REPO_ROOT / "components"),
                  str(REPO_ROOT / "components" / "life" / "scripts")):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    import autonomous_life_cycle as lifecycle
    import demo_business_clock as dbc
    import life_request_budget as lrb

    # 1) 真实 CLI 的显式配置落盘：总限/历史/新增只属于这一个新根。
    config_root = base / "budget-boundary-300-config"
    code, prepared = _cli("prepare", "--clock-only", "--root", str(config_root),
                          "--run-id", "smoke-300", "--rate", "24",
                          "--logical-seconds", "120",
                          "--request-total-limit", "300",
                          "--historical-requests", "14",
                          "--max-new-requests", "286", timeout=300.0)
    config = _read(config_root / "fastday.json")
    cap_file = _read(config_root / "state" / "fastday" / "experiment_budget.json")
    code_status, status = _cli("status", "--root", str(config_root), timeout=300.0)
    probe: dict[str, Any] = {
        "config": {
            "prepare_exit": code,
            "status_exit": code_status,
            "fastday": {key: config.get(key) for key in
                        ("request_total_limit", "historical_requests",
                         "max_new_requests", "max_cognition_calls_per_day")},
            "experiment_file": {key: cap_file.get(key) for key in
                                ("max_new_requests", "authorized_total_limit",
                                 "historical_requests", "baseline_run_window_calls",
                                 "hard_config_max")},
            "status_request_limits": status.get("request_limits"),
            "status_experiment": status.get("experiment"),
        },
        "transport": "offline_send_double: no socket, no provider, 0 model calls",
    }

    # 2) 同一份显式配置下的真实预算对象与每请求预留边界。
    root = base / "budget-boundary-300"
    state_root = root / "state"
    run_id = "run:smoke-300"
    total, historical, limit = 300, 14, 286
    dbc.ExperimentRequestCap(root, run_id=run_id, max_new_requests=limit,
                             total_limit=total,
                             historical_requests=historical).create()
    ledger = lrb.LifeRequestBudget(state_root, utc_day_max=total,
                                   run_window_max=total, run_id=run_id)
    ledger.ensure_window(allow_new=True)
    cap = dbc.ExperimentRequestCap(root, run_id=run_id, max_new_requests=limit,
                                   total_limit=total, historical_requests=historical)
    guarded = dbc.CappedRequestBudget(ledger, cap)

    def _cycle_budget():
        return lifecycle.RoundBudget(state_root, max_rounds_per_day=48,
                                     max_cognition_calls_per_day=total,
                                     requests=guarded)

    class _OfflineSendDouble:
        send_split_capable = True

        def __init__(self) -> None:
            self.calls = 0

        def invoke(self, request, *, schema_retry_index: int = 0):
            self.calls += 1
            return {"ok": True}

    inner = _OfflineSendDouble()
    adapter = lrb.BudgetedCognitionAdapter(inner, guarded)
    initial = guarded.view()
    probe["initial"] = {
        "utc_day_calls": initial.get("utc_day_calls"),
        "cognition_used_today": _cycle_budget().cognition_used_today(),
        "cognition_remaining_today": _cycle_budget().cognition_remaining(),
        "may_start_wake": list(_cycle_budget().may_start_wake()),
        "experiment_cap": initial.get("experiment_cap"),
    }
    adapter.invoke({"kind": "decision"})
    after_one = guarded.view()
    probe["after_one_send"] = {
        "transport_calls": inner.calls,
        "utc_day_calls": after_one.get("utc_day_calls"),
        "experiment_used": (after_one.get("experiment_cap") or {}).get("used"),
        "cognition_remaining_today": _cycle_budget().cognition_remaining(),
    }
    for _ in range(limit - 1):
        adapter.invoke({"kind": "decision"})
    at_limit = guarded.view()
    refusal: dict[str, Any] = {}
    before_refusal = inner.calls
    try:
        adapter.invoke({"kind": "decision"})
        refusal = {"reason_code": None, "detail": "the exhausted reservation was not refused"}
    except lrb.RequestBudgetError as exc:
        refusal = {"reason_code": exc.reason_code, "detail": exc.detail}
    refusal["transport_calls_before"] = before_refusal
    refusal["transport_calls_after"] = inner.calls
    probe["at_limit"] = {
        "transport_calls": inner.calls,
        "utc_day_calls": at_limit.get("utc_day_calls"),
        "experiment_used": (at_limit.get("experiment_cap") or {}).get("used"),
        "experiment_remaining": (at_limit.get("experiment_cap") or {}).get("remaining"),
        "cognition_remaining_today": _cycle_budget().cognition_remaining(),
        "ledger_utc_day_max": at_limit.get("utc_day_max"),
        "ledger_run_window_max": at_limit.get("run_window_max"),
    }
    probe["refusal"] = refusal

    # 3) 真实实例装配：显式 300 真的进入 nested env 与 resident 的预算读面。
    instance_root = base / "instance-300"
    code, prepared = _cli("prepare", "--root", str(instance_root),
                          "--run-id", "smoke-300-live",
                          "--logical-seconds", "120", "--real-seconds", "60",
                          "--request-total-limit", "300",
                          "--historical-requests", "14",
                          "--max-new-requests", "286", timeout=900.0)
    nested = (_read(instance_root / "one-day" / "instance.json").get("environment") or {})
    live_state = Path(str(prepared.get("state_root") or ""))
    process = _start(instance_root, log_name="start-300.log", duration_seconds=15)
    summary: dict[str, Any] = {}
    try:
        _wait(lambda: bool(_read(live_state / "resident_summary.json")
                           .get("runtime_assembly", {}).get("runtime_present")),
              timeout=180.0)
        summary = _read(live_state / "resident_summary.json")
    finally:
        _cli("stop", "--root", str(instance_root), timeout=180.0)
        _wait(lambda: process.poll() is not None, timeout=120.0)
        if process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
    probe["live_instance"] = {
        "prepare_exit": code,
        "state_root": str(live_state),
        "env": {key: nested.get(key) for key in
                ("CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_UTC_DAY",
                 "CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_RUN_WINDOW",
                 "CHIYO_AUTONOMOUS_MAX_COGNITION_CALLS_PER_DAY",
                 "CHIYO_AUTONOMOUS_RUN_ID")},
        "time_domain": summary.get("time_domain"),
        "runtime_assembly": summary.get("runtime_assembly"),
        "memory_binding": summary.get("memory_binding"),
        "budget": summary.get("budget"),
        "offline_scripted": summary.get("offline_scripted"),
        "process_exited": process.poll() is not None,
    }
    return probe


def _start(root: Path, *, log_name: str, duration_seconds: int = 20) -> subprocess.Popen:
    log_path = root / "logs" / log_name
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("a", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-B", str(FAST_DAY), "start", "--root", str(root),
         "--no-act", "--offline-scripted", "--poll-seconds", "1",
         "--duration-seconds", str(int(duration_seconds))],
        stdout=handle, stderr=subprocess.STDOUT, cwd=str(REPO_ROOT), env=_env())
    handle.close()
    return process


def _owned_pids(state_root: Path) -> list[int]:
    record = _read(state_root / "fastday" / "run.json")
    pids = [record.get("pid"), (record.get("child") or {}).get("pid")]
    for name in ("supply", "world"):
        pids.append(((record.get("owners") or {}).get(name) or {}).get("pid"))
    return [int(pid) for pid in pids if _alive(pid)]


def _owned_alive(state_root: Path) -> bool:
    return bool(_owned_pids(state_root))


def _alive(pid: Any) -> bool:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    try:
        os.kill(value, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _finish(report: dict[str, Any], checks: Checks,
            processes: list[subprocess.Popen]) -> int:
    for process in processes:
        if process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
    report["checks"] = checks.rows
    report["ok"] = checks.ok
    report["product_model_calls"] = 0
    report["runtime_evidence"] = "offline_scripted 装配；不是她的文字，不是 24h/自主生活结论"
    sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    sys.stdout.flush()
    return 0 if checks.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

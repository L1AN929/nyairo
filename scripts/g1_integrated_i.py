#!/usr/bin/env python3
"""G1 完整端到端的新隔离入口 i（2026-11-07 父级授权）。

它不另造生活循环：身份固定为 ``g1-integrated-i``，prepare/audit/run 全部薄包装
``g1_real_adoption`` 现成的双 PID p1/p2 链、``SubmissionInjector``、``_apply_round``、
``_coverage_from_states`` 与提前武装的 ``_arm_unknown_pre_round``；判定/安全投影/
只读读面直接复用 ``g1_integrated_g``，不复制整套仪器。本文件只负责：

1. **封闭身份**：root ``/tmp/nyairo-g1-real-20261107-i``、独立 authority
   ``/tmp/nyairo-g1-real-20261107-i-memory-authority/authority.db``、
   run ``run:g1-real-20261107-i``、域 ``g1:real:20261107-i``、history 539、
   本 run 新增 20、UTC 日 300、累计上限 1000（539 + 20 最坏 559）、
   窗口 3600 真实秒、tick 5 秒、max_ticks 1000/trace 10000、最晚 23:30 启动、
   00:50 停止新请求、收尾 60 秒。任意别的 root / 身份 / 预算参数都没有入口。
2. **同一套完整门**：12 个布尔与状态顺序仍由 ``g1_integrated_g.gate_projection``
   产生；本入口只换身份，不放松任何判据，也不改 NO_ACTION / 模型合法集 / 合法
   nochange 的原义。未触发即 ``NOT_COVERED``。
3. **i 专属只读观测**：``g1_real_adoption`` 只在 i 的脚本实验路径里包住
   ``writer.next_content`` 与 artifact_edit 的 ``transport.complete``，把本进程
   **本地调用边界**是否携带可读 ``work_context`` 定型成 p1/p2 phase 报告里的安全
   summary。它不证明服务器收到、不改发送次数/参数/返回/异常，也不参与 12 门判定；
   没有请求时状态是 ``NOT_EXERCISED``，解析不出时字段是 ``UNKNOWN``。
4. **离线验收**：``offline-check`` 只在新 ``mkdtemp`` scratch 根里跑，父进程 AF_INET/
   AF_INET6/DNS/create_connection 硬拒，phase 子进程与自有 Supply owner 各装同一份
   局部守卫（AF_UNIX 放行）。它包含 4–6 个聚合新 case 与一条正式双 PID 同根短链；
   不重跑旧 g/h 套件、旧 G1 19/28/37 套件或全库。

真实入口仍由父级执行；worker 不 prepare/run 真实根，不加载密钥。stdout 只输出 id、
固定枚举、布尔和计数。
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
for entry in (str(ROOT), str(SCRIPT_DIR), str(ROOT / "tests"),
              str(ROOT / "components"), str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "world" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import g1_integrated_g as g  # noqa: E402
import g1_real_adoption as g1  # noqa: E402

BATCH = g1.I_PROFILE.key
PROFILE = g1.I_PROFILE
EXIT_OK = g.EXIT_OK
EXIT_REFUSED = g.EXIT_REFUSED
OFFLINE_PREFIX = "g1-integrated-i-offline-"
TEST_PATTERN = "test_g1_integrated_i.py"

RESULT_COVERED = g.RESULT_COVERED
RESULT_NOT_COVERED = g.RESULT_NOT_COVERED

_WORK_OBSERVATION_FIELDS = frozenset({
    "phase", "call_index", "parse_ok", "work_present", "work_state",
    "expected_work_matches", "research_present", "practice_present",
    "earlier_count", "experience_count",
})


def _refuse(reason: str, *, root: Any = None,
            exception_type: Optional[str] = None) -> int:
    payload: dict[str, Any] = {"ok": False,
                               "reason_code": g._safe_enum(reason) or "REFUSED",
                               "batch": BATCH, "files_written": []}
    path = g._safe_path(root)
    if path is not None:
        payload["root"] = path
    if isinstance(exception_type, str) and g._EXC_TYPE_RE.match(exception_type):
        payload["exception_type"] = exception_type
    return g._emit(payload, code=EXIT_REFUSED)


def _offline_root_allowed(root: Path) -> bool:
    return g._is_scratch_root(root, prefix=OFFLINE_PREFIX)


# ---------------------------------------------------------------------------------------------
# CLI：prepare / audit / run / status
# ---------------------------------------------------------------------------------------------


def cmd_prepare(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="g1_integrated_i prepare")
    parser.add_argument("--root", required=True)
    parser.add_argument("--offline-fixture", action="store_true")
    args = parser.parse_args(list(argv))
    root = Path(str(args.root)).expanduser()
    if args.offline_fixture and not _offline_root_allowed(root):
        return _refuse("OFFLINE_PATH_REFUSED", root=root)
    with g1._use_batch(BATCH):
        if args.offline_fixture:
            with g1.offline_clock(), g1._http_forbidden():
                result = g1.prepare(root, expected_root=root, offline=True)
        else:
            result = g1.prepare(root)
    payload = g._safe_result(result)
    payload["batch"] = BATCH
    return g._emit(payload, code=EXIT_OK if result.get("ok") else EXIT_REFUSED)


def cmd_audit(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="g1_integrated_i audit")
    parser.add_argument("--root", required=True)
    parser.add_argument("--offline-fixture", action="store_true")
    args = parser.parse_args(list(argv))
    root = Path(str(args.root)).expanduser()
    if args.offline_fixture and not _offline_root_allowed(root):
        return _refuse("OFFLINE_PATH_REFUSED", root=root)
    with g1._use_batch(BATCH):
        result = g.audit_readonly(root,
                                  expected_root=(root if args.offline_fixture else None),
                                  profile=PROFILE)
    return g._emit(result, code=EXIT_OK if result.get("ok") else EXIT_REFUSED)


def cmd_run(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="g1_integrated_i run")
    parser.add_argument("--root", required=True)
    parser.add_argument("--execute-approved", action="store_true")
    parser.add_argument("--offline-fixture", action="store_true")
    args = parser.parse_args(list(argv))
    root = Path(str(args.root)).expanduser()
    if args.offline_fixture and not _offline_root_allowed(root):
        return _refuse("OFFLINE_PATH_REFUSED", root=root)
    with g1._use_batch(BATCH):
        if args.offline_fixture:
            with g1.offline_clock(), g1._http_forbidden():
                report = g1.start_run(root, expected_root=root,
                                      execute_approved=bool(args.execute_approved),
                                      offline=True)
        else:
            report = g1.start_run(root, execute_approved=bool(args.execute_approved),
                                  offline=False)
    payload = g._safe_result(report)
    payload["batch"] = BATCH
    payload["g1_gate"] = g._projection_for_root(root, report)
    return g._emit(payload, code=EXIT_OK if report.get("ok") else EXIT_REFUSED)


def cmd_status(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="g1_integrated_i status")
    parser.add_argument("--root", required=True)
    parser.add_argument("--offline-fixture", action="store_true")
    args = parser.parse_args(list(argv))
    root = Path(str(args.root)).expanduser()
    if args.offline_fixture and not _offline_root_allowed(root):
        return _refuse("OFFLINE_PATH_REFUSED", root=root)
    with g1._use_batch(BATCH):
        result = g.status(root, expected_root=(root if args.offline_fixture else None),
                          profile=PROFILE)
    return g._emit(result, code=EXIT_OK if result.get("ok") else EXIT_REFUSED)


# ---------------------------------------------------------------------------------------------
# offline-check：4–6 个聚合新 case（测试模块内）+ 正式双 PID 同根短链
# ---------------------------------------------------------------------------------------------

_CHECKS: list[tuple[str, bool, Any]] = []


def _check(name: str, condition: Any, detail: Any = None) -> None:
    _CHECKS.append((name, bool(condition), detail))
    print("CHECK %-72s %s" % (name, "PASS" if condition else "FAIL"))
    if not condition and detail is not None:
        print("  detail:", json.dumps(g._safe_detail(detail), ensure_ascii=False,
                                      sort_keys=True))


def _identity_snapshot() -> dict[str, Any]:
    """只做机械断言：i 是封闭身份，h/g 与 a/b/c/f 的旧默认一个都不漂。"""

    gprofile = g1.G_PROFILE
    hprofile = g1.H_PROFILE
    return {
        "i_key": PROFILE.key, "i_root": str(PROFILE.root),
        "i_authority": str(PROFILE.authority_dir),
        "i_run": PROFILE.run_id, "i_domain": PROFILE.domain_id,
        "i_history": PROFILE.expected_history,
        "i_window": g1._profile_window_seconds(PROFILE),
        "i_max_ticks": g1._profile_max_ticks(PROFILE),
        "i_trace": g1._profile_trace_max_records(PROFILE),
        "i_gate": g1._world_intent_gate(PROFILE),
        "i_natural": g1._is_natural(PROFILE),
        "i_cleanup": g1._phase_cleanup_seconds(PROFILE),
        "h_key": hprofile.key, "h_root": str(hprofile.root),
        "h_run": hprofile.run_id, "h_domain": hprofile.domain_id,
        "h_history": hprofile.expected_history,
        "g_key": gprofile.key, "g_root": str(gprofile.root),
        "g_run": gprofile.run_id, "g_domain": gprofile.domain_id,
        "g_history": gprofile.expected_history,
        "abc_keys": sorted(g1.BATCH_PROFILES),
        "batch_choices": list(g1.BATCH_CHOICES),
        "cli_batch_choices": list(g1.CLI_BATCH_CHOICES),
    }


def _identity_checks(scratch: Path) -> dict[str, Any]:
    taken_root = scratch / "authority-taken" / "instance-root"
    authority = taken_root.parent / ("%s-memory-authority" % taken_root.name)
    authority.mkdir(parents=True, exist_ok=True)
    (authority / "authority.db").write_text("pre-existing authority", encoding="utf-8")
    taken = g1.prepare(taken_root, expected_root=taken_root, offline=True)
    foreign_root = scratch / "foreign-prereg"
    foreign_profile = g1.H_PROFILE
    g1._write_json(g1._experiment_dir(foreign_root) / g1.PREREG_FILENAME, {
        "schema": "g1.real.prereg.v1", "batch": foreign_profile.key,
        "run_id": foreign_profile.run_id, "domain_id": foreign_profile.domain_id,
        "root": str(foreign_root), "state_root": str(foreign_root / "one-day"),
        "deadline": foreign_profile.deadline.isoformat(),
        "latest_start": foreign_profile.latest_start.isoformat(),
    }, private=True)
    foreign = g.audit_readonly(foreign_root, expected_root=foreign_root, profile=PROFILE)
    return {"authority_taken": {"reason_code": taken.get("reason_code"),
                                "root_created": taken_root.exists()},
            "foreign_prereg": foreign.get("reason_code"),
            "wrong_root": (g1._check_root(Path("/tmp/not-this-batch"),
                                          expected=PROFILE.root) or {}).get("reason_code")}


def _observation_view(phase: Any) -> dict[str, Any]:
    row = phase if isinstance(phase, dict) else {}
    return dict(row.get("work_observation") or {})


def _observation_is_safe(view: Any) -> bool:
    """summary 只允许固定形状与有界值；任何别的键/自由文本都算不合规。"""

    if not isinstance(view, dict):
        return False
    if view.get("mode") != g1.WORK_OBSERVATION_MODE:
        return False
    if view.get("state") not in {"OBSERVED", "NOT_EXERCISED"}:
        return False
    calls = view.get("calls")
    if not isinstance(calls, list) or len(calls) > g1.WORK_OBSERVATION_MAX_RECORDS:
        return False
    for item in calls:
        if not isinstance(item, dict) or set(item) != _WORK_OBSERVATION_FIELDS:
            return False
        if item.get("phase") not in {"p1", "p2", "UNKNOWN"}:
            return False
        if not isinstance(item.get("call_index"), int) or isinstance(
                item.get("call_index"), bool):
            return False
        for key in ("parse_ok", "work_present", "research_present",
                    "practice_present"):
            if not isinstance(item.get(key), bool):
                return False
        if item.get("work_state") not in g1.WORK_OBSERVATION_STATES:
            return False
        matches = item.get("expected_work_matches")
        if not (isinstance(matches, bool) or matches == "UNKNOWN"):
            return False
        for key in ("earlier_count", "experience_count"):
            value = item.get(key)
            if value == "UNKNOWN":
                continue
            if (not isinstance(value, int) or isinstance(value, bool)
                    or value < 0 or value > 1000):
                return False
    return True


def _offline_body() -> bool:
    checks_before = len(_CHECKS)
    tests = g._run_new_tests(TEST_PATTERN)
    _check("the new i module collects and runs every aggregated case",
           tests.get("ok") is True and int(tests.get("run") or 0) > 0
           and int(tests.get("skipped") or 0) == 0, tests)

    scratch = Path(tempfile.mkdtemp(prefix=OFFLINE_PREFIX))
    root = scratch / "instance-root"
    print("scratch root:", scratch)
    with g1._use_batch(BATCH), g1.offline_clock(), \
            g1._offline_network_guard(scratch), g1._http_forbidden():
        prepared = g1.prepare(root, expected_root=root, offline=True)
        prereg = g1._read_json(g1._experiment_dir(root) / g1.PREREG_FILENAME)
        caps = dict(prereg.get("caps") or {})
        identity = _identity_snapshot()
        refusals = _identity_checks(scratch)
        _check("i is the only new closed identity and h/g/a/b/c/f do not move",
               PROFILE.key == "g1-integrated-i"
               and str(PROFILE.root) == "/tmp/nyairo-g1-real-20261107-i"
               and str(PROFILE.authority_dir)
               == "/tmp/nyairo-g1-real-20261107-i-memory-authority"
               and str(PROFILE.authority_db)
               == "/tmp/nyairo-g1-real-20261107-i-memory-authority/authority.db"
               and PROFILE.run_id == "run:g1-real-20261107-i"
               and PROFILE.domain_id == "g1:real:20261107-i"
               and PROFILE.expected_history == 539
               and identity["i_window"] == 3600 and identity["i_max_ticks"] == 1000
               and identity["i_trace"] == 10000 and identity["i_gate"] == "off"
               and identity["i_natural"] is False and identity["i_cleanup"] == 60.0
               and identity["h_key"] == "g1-integrated-h"
               and identity["h_history"] == 524
               and identity["g_key"] == "g1-integrated-g"
               and identity["g_history"] == 507
               and identity["abc_keys"] == ["a", "b", "c"]
               and identity["batch_choices"] == ["a", "b", "c", "d"]
               and identity["cli_batch_choices"] == ["a", "b", "c", "d", "e"]
               and refusals["wrong_root"] == g1.REASON_ROOT_NOT_PREREGISTERED
               and refusals["foreign_prereg"] == g1.REASON_BATCH_IDENTITY_MISMATCH
               and refusals["authority_taken"]["reason_code"]
               == g1.REASON_AUTHORITY_NOT_FRESH
               and refusals["authority_taken"]["root_created"] is False,
               {"identity": identity, "refusals": refusals})

        gate_off = g1.start_run(root, expected_root=root, execute_approved=False,
                                offline=True)
        _check("prepare is zero-model/zero-request and run needs --execute-approved",
               prepared.get("ok") is True
               and prepared.get("model_called") is False
               and prepared.get("request_sent") is False
               and prepared.get("batch") == BATCH
               and prepared.get("run_id") == PROFILE.run_id
               and prepared.get("domain_id") == PROFILE.domain_id
               and prereg.get("batch") == BATCH
               and prereg.get("deadline") == PROFILE.deadline.isoformat()
               and prereg.get("latest_start") == PROFILE.latest_start.isoformat()
               and prereg.get("memory_authority")
               == str(root.parent / ("%s-memory-authority" % root.name) / "authority.db")
               and caps.get("request_cap") == 20
               and caps.get("utc_day_cap") == 300
               and caps.get("expected_history") == 539
               and caps.get("window_seconds") == 3600
               and caps.get("max_ticks") == 1000
               and caps.get("trace_max_records") == 10000
               and caps.get("total_accounting_cap") == 1000
               and prereg.get("world_intent_gate") == "off"
               and (prereg.get("observation") or {}).get("mode")
               == g1.WORK_OBSERVATION_MODE
               and gate_off.get("ok") is False
               and gate_off.get("reason_code") == g1.REASON_EXECUTE_FLAG_MISSING,
               {"prepare": g._safe_result(prepared), "caps": caps,
                "gate_off": gate_off.get("reason_code")})

        cap_root = scratch / "cap-state"
        cap_root.mkdir(parents=True, exist_ok=True)
        cap = g1._cross_pid_cap(cap_root, batch=BATCH)
        _check("two independent processes share one 20-request cap (21st refused)",
               cap["first_rc"] == 0 and cap["second_rc"] == 0
               and (cap["first_out"] or {}).get("reserved") == 12
               and (cap["second_out"] or {}).get("reserved") == 8
               and cap["third"] != "reserved" and cap["calls"] == 20
               and cap["run_id"] == PROFILE.run_id, cap)

        audited = g.audit_readonly(root, expected_root=root, profile=PROFILE)
        read_status = g.status(root, expected_root=root, profile=PROFILE)
        blob = json.dumps({"audit": audited, "status": read_status},
                          ensure_ascii=False, default=str)
        forbidden = [g1.MATERIAL_TEXT, g1.CONTROL_TEXT, "offline-not-a-key", "sk-",
                     "api_key", "prompt", "completion"]
        hits = [token for token in forbidden if token and token in blob]
        sentinel = "PRIVATE_SENTINEL_7f3a"
        injected = {
            "ok": True, "detail": sentinel,
            "model_params": {"base_url": sentinel, "api_key": sentinel},
            "ledger": {"state": "ok", "run_window_calls": 3, "utc_day_calls": 3,
                       "run_window_max": 20, "utc_day_max": 300,
                       "recent_tokens": [sentinel]},
            "p1": {"phase": "p1", "model": {"base_url": sentinel, "api_key": sentinel},
                   "rounds": [{"wake": {"decision": "CONTINUE", "detail": sentinel}}],
                   "work_observation": {"mode": g1.WORK_OBSERVATION_MODE,
                                        "state": "OBSERVED",
                                        "detail": sentinel,
                                        "calls": [{"phase": "p1", "call_index": 1,
                                                   "parse_ok": True,
                                                   "work_present": True,
                                                   "work_state": "OK",
                                                   "expected_work_matches": True,
                                                   "research_present": False,
                                                   "practice_present": False,
                                                   "earlier_count": 0,
                                                   "experience_count": 1,
                                                   "body": sentinel}]}},
        }
        leaked = sentinel in json.dumps(g._safe_result(injected), ensure_ascii=False,
                                        default=str)
        ledger_keys = sorted(audited.get("ledger") or {})
        _check("status/audit are pure-read safe fields and the i ledger stays at 0..20",
               audited.get("ok") is True and read_status.get("ok") is True
               and read_status.get("batch") == BATCH
               and read_status.get("run_id") == PROFILE.run_id
               and ledger_keys == ["run_window_calls", "run_window_max", "state",
                                   "utc_day_calls", "utc_day_max"]
               and int((audited.get("ledger") or {}).get("run_window_max") or 0) == 20
               and int((audited.get("ledger") or {}).get("run_window_calls") or 0)
               <= 20
               and (audited.get("window") or {}).get("present") is False
               and not hits and not leaked, {"hits": hits, "leaked": leaked,
                                             "ledger": audited.get("ledger")})

        chain = g1.start_run(root, expected_root=root, execute_approved=True,
                             offline=True)
        p1_full = g._full_phase(root, "p1")
        p2_full = g._full_phase(root, "p2")
        projection = g.gate_projection(p1_full, p2_full, chain)
        guard = g1._offline_child_guard_report(root)
        again = g1.start_run(root, expected_root=root, execute_approved=True,
                             offline=True)
        read_after = g.status(root, expected_root=root, profile=PROFILE)
        observations = {phase: _observation_view((read_after.get("phases") or {}).get(phase))
                        for phase in ("p1", "p2")}
        _check("formal dual-PID chain covers every gate and carries a safe i summary",
               chain.get("ok") is True
               and projection.get("result") == RESULT_COVERED
               and not projection.get("missing")
               and guard.get("ok") is True and int(guard.get("found") or 0) >= 2
               and again.get("reason_code") == g1.REASON_ALREADY_STARTED
               and all(_observation_is_safe(view) for view in observations.values()),
               {"stop_reason": chain.get("stop_reason"),
                "projection": projection, "guard": guard,
                "no_restart": again.get("reason_code"),
                "observations": observations})

    failed = [name for name, ok, _detail in _CHECKS if not ok]
    print()
    print("scratch kept:", scratch)
    for name in failed:
        print("FAILED:", name)
    passed = not failed and tests.get("ok") is True
    print("RESULT: %s" % ("PASS" if passed else "FAIL"))
    print("checks: %d (new %d)" % (len(_CHECKS), len(_CHECKS) - checks_before))
    return passed


def offline_check() -> bool:
    with g1._use_batch(BATCH):
        with g1.offline_clock():
            return _offline_body()


def cmd_offline_check(_argv: Sequence[str]) -> int:
    return EXIT_OK if offline_check() else EXIT_REFUSED


COMMANDS = {
    "offline-check": cmd_offline_check,
    "prepare": cmd_prepare,
    "audit": cmd_audit,
    "run": cmd_run,
    "status": cmd_status,
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
    try:
        return int(handler(arguments[1:]))
    except SystemExit:
        # argparse 的正常退出路径原样透传，不吞成成功。
        raise
    except Exception as exc:  # noqa: BLE001 - fixed code + exception type only
        return _refuse("INTERNAL_ERROR", exception_type=type(exc).__name__)


if __name__ == "__main__":
    raise SystemExit(main())

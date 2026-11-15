#!/usr/bin/env python3
"""G1 f 批（2026-10-31 决策单限定范围）的封闭启动入口。

这是 [预登记](../../docs/engineering/g1-next-prereg-20261031.md) 在批准后的
**真实 prepare/audit/run 薄包装**，不另造生活循环：

* 身份固定为 f，只用 ``g1_real_adoption`` 现成的 prepare/audit/start_run：
  ``/root/nyairo-g1-real-20261031-f``、``run:g1-real-20261031-f``、
  ``g1:real:20261031-f``、history 168、cap 20 / 1800 真实秒、截止
  ``2026-11-01T01:00:00+08:00``；与绝对截止取较早者。
* 复用 p1/p2 两真实进程、同一本持久账本、operator pause、正式 AG-1/AG-2/AR-0
  与 owner 版本；Decision/内容仍是适配器里的正式 transport，封装层不做替身。
* 本入口只多一层**窄判据**：A/actA 锚 + 跨 PID + 新 Action（不等于 P1 首个 Action）
  + 同 A/actA 的新 owner 版本。旧成功重放、换 Activity、同源新作品、换 Action 之外的
  空证据一律 ``NOT_COVERED``；完整 G1 五段分别报标签，不因锚续写成功就宣称整门通过。
* ``--offline-fixture`` 只用于离线验收：只接受 ``mkdtemp`` 下的 ``g1-live-f-*`` 根，
  全链装 HTTP 硬拒、脚本 Decision/内容、显式 offline 账本；**不是**真实入口。
  真实 prepare/run 不加载密钥、不探测 provider——密钥只由父级 run 时 source 环境。

安全字段与旧 harness 一致：只输出 id/枚举/布尔/计数；不落 prompt、正文、headers、
token 或异常原文。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
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

BATCH = "f"
FIXTURE_PREFIX = "g1-live-f-"
EXIT_OK = 0
EXIT_REFUSED = 2
REASON_OFFLINE_PATH = "OFFLINE_PATH_REFUSED"

#: 本批只认这些结果；不把 ``audit`` 的 exit0 当 G1 通过。
RESULT_COVERED = "COVERED_LIMITED"
RESULT_NOT_COVERED = "NOT_COVERED"


# ---------------------------------------------------------------------------------------------
# 离线夹具根守卫（与 g1_next_offline 同一收紧方式）
# ---------------------------------------------------------------------------------------------


def _is_scratch_root(path: Any) -> bool:
    if not path:
        return False
    try:
        resolved = str(Path(str(path)).resolve())
        temp_root = str(Path(tempfile.gettempdir()).resolve())
    except OSError:
        return False
    return (resolved.startswith(temp_root + os.sep)
            and FIXTURE_PREFIX.strip("-") in resolved)


def _refuse(reason: str, *, root: Any = None, detail: str = "") -> int:
    payload = {"ok": False, "reason_code": reason, "batch": BATCH,
               "root": str(root) if root is not None else None,
               "files_written": []}
    if detail:
        payload["detail"] = detail
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    sys.stdout.flush()
    return EXIT_REFUSED


# ---------------------------------------------------------------------------------------------
# 窄判据：只认 A/actA 锚和本轮真实写入
# ---------------------------------------------------------------------------------------------


def _bare(value: Any) -> str:
    return g1._bare(value)


def _matching_continue(p2: Mapping[str, Any], artifact_a: str, activity_ref: str) -> Optional[dict]:
    """p2 的第一条「同 A、同 actA、带新 Action」且真实落新版本的 CONTINUE 轮（无落盘则回退首条匹配轮）。"""

    fallback: Optional[dict] = None
    for row in p2.get("rounds") or ():
        wake = dict((row or {}).get("wake") or {})
        if str(wake.get("decision") or "") != "CONTINUE":
            continue
        if _bare(wake.get("step_artifact")) != artifact_a:
            continue
        if str(wake.get("step_activity") or "") != activity_ref:
            continue
        if not str(wake.get("action_id") or ""):
            continue
        if fallback is None:
            fallback = {"row": row, "wake": wake}
        if wake.get("owner_version_created") is True:
            return {"row": row, "wake": wake}
    return fallback


def _segments(p2: Mapping[str, Any], *, cross_pid: bool) -> dict[str, str]:
    """完整 G1 的五段各自按证据报；缺证据一律 NOT_COVERED，不互相顶替。"""

    injected = dict(p2.get("injections") or {})
    reconcile = dict(p2.get("reconcile") or {})
    cas_owner = dict(p2.get("cas_owner") or {})
    no_action = False
    for row in p2.get("rounds") or ():
        wake = dict((row or {}).get("wake") or {})
        if (str(wake.get("decision") or "") == "NO_ACTION"
                and str(wake.get("outcome") or "") == "NO_ACTION"
                and not str(wake.get("action_id") or "")
                and not str(wake.get("step_artifact") or "")):
            no_action = True
            break
    return {
        "interrupt": ("PAUSED_BY_OPERATOR"
                      if bool((p2.get("pause_at_start") or {}).get("paused"))
                      else "NOT_COVERED"),
        "cross_pid_restart": "CROSS_PID" if cross_pid else "NOT_COVERED",
        "unknown": ("RECONCILED" if reconcile.get("result") == "CONFIRMED_SUCCEEDED"
                    else "NOT_COVERED"),
        "version_conflict": ("CAS_REFUSED" if cas_owner.get("owner_status") == "CONFLICT"
                             else "NOT_COVERED"),
        "no_action": "NO_ACTION_CHOSEN" if no_action else "NOT_COVERED",
        "operator_injections": {
            "unknown_fired": int(injected.get("unknown_fired") or 0),
            "concurrent_fired": int(injected.get("concurrent_fired") or 0),
            "note": "按既有已登记接缝各一次；人为故障，不是自然发生",
        },
    }


def _verdict(p1: Mapping[str, Any], p2: Mapping[str, Any],
             run_report: Mapping[str, Any]) -> dict[str, Any]:
    """本批唯一成功口径：同 A/actA、新 Action、新 owner 版本。"""

    artifact_a = _bare(p1.get("artifact_a"))
    activity_ref = str(p1.get("activity_ref") or "")
    first_action = str(p1.get("first_action_id") or "")
    versions_start = int(p2.get("versions_at_start") or 0)
    versions_end = int(p2.get("versions_at_end") or 0)
    match = _matching_continue(p2, artifact_a, activity_ref)
    wake = dict((match or {}).get("wake") or {})
    action_id = str(wake.get("action_id") or "")
    cross_pid = (bool(p1.get("pid")) and bool(p2.get("pid"))
                 and str(p1.get("pid")) != str(p2.get("pid")))
    same_run = (bool(p1.get("run_id")) and p1.get("run_id") == p2.get("run_id")
                and str(p1.get("run_id") or "") == str(run_report.get("run_id") or ""))
    criteria = {
        "a_anchor_first_owner_write": bool(artifact_a)
        and p1.get("write_confirmed") is True
        and p1.get("owner_version_created") is True
        and bool(activity_ref)
        and bool(first_action),
        "cross_pid_restart": cross_pid,
        "same_run_identity": same_run,
        "paused_round_zero_request": p2.get("paused_round_zero_requests") is True,
        "continued_same_artifact_a": bool(match) and _bare(
            ((match or {}).get("wake") or {}).get("step_artifact")) == artifact_a,
        "continued_same_activity_actA": bool(match)
        and str(wake.get("step_activity") or "") == activity_ref,
        "new_action_bound": bool(action_id) and action_id != first_action,
        "new_owner_version_and_receipt": wake.get("owner_version_created") is True
        and bool(wake.get("step_artifact"))
        and versions_end > versions_start,
    }
    core = all(criteria.values())
    return {
        "result": RESULT_COVERED if core else RESULT_NOT_COVERED,
        "criteria": criteria,
        "anchor": {"artifact_a": artifact_a or None, "activity_ref": activity_ref or None,
                   "first_action_id": first_action or None,
                   "continue_action_id": action_id or None},
        "pids": {"p1": p1.get("pid"), "p2": p2.get("pid")},
        "versions": {"at_start": versions_start, "at_end": versions_end},
        "segments": _segments(p2, cross_pid=cross_pid),
        "real_g1_evidence": "NOT_COVERED",
        "reason": ("ANCHOR_ONLY_LIMITED" if core else "LIMITED_CRITERIA_NOT_MET"),
        "note": "limited same-work anchor only; this is not a G1 pass",
    }


# ---------------------------------------------------------------------------------------------
# 命令
# ---------------------------------------------------------------------------------------------


def _expected_root(root: Path, offline: bool) -> Optional[Path]:
    """真实入口只认 f 的预登记根；离线夹具把期望根收窄成它自己。"""

    return root if offline else None


def _safe_result(result: Mapping[str, Any]) -> dict[str, Any]:
    keys = ("ok", "reason_code", "batch", "run_id", "domain_id", "root", "state_root",
            "started_at", "stop_reason", "result", "coverage", "injections", "ledger",
            "window", "model_called", "request_sent", "detail", "deadline",
            "material", "control")
    return {key: result.get(key) for key in keys if key in result}


def cmd_prepare(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="g1_next_live prepare")
    parser.add_argument("--root", required=True)
    parser.add_argument("--offline-fixture", action="store_true")
    args = parser.parse_args(list(argv))
    root = Path(args.root)
    if args.offline_fixture and not _is_scratch_root(root):
        return _refuse(REASON_OFFLINE_PATH, root=root)
    with g1._use_batch(BATCH):
        if args.offline_fixture:
            with g1._http_forbidden():
                result = g1.prepare(root, expected_root=_expected_root(root, True), offline=True)
        else:
            result = g1.prepare(root)
    payload = _safe_result(result)
    payload["batch"] = BATCH
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    return EXIT_OK if result.get("ok") else EXIT_REFUSED


def cmd_audit(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="g1_next_live audit")
    parser.add_argument("--root", required=True)
    parser.add_argument("--offline-fixture", action="store_true")
    args = parser.parse_args(list(argv))
    root = Path(args.root)
    if args.offline_fixture and not _is_scratch_root(root):
        return _refuse(REASON_OFFLINE_PATH, root=root)
    with g1._use_batch(BATCH):
        if args.offline_fixture:
            with g1._http_forbidden():
                result = g1.audit(root, expected_root=_expected_root(root, True))
        else:
            result = g1.audit(root)
    sys.stdout.write(json.dumps(dict(result), ensure_ascii=False, indent=1,
                                sort_keys=True, default=str) + "\n")
    return EXIT_OK if result.get("ok") else EXIT_REFUSED


def cmd_run(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="g1_next_live run")
    parser.add_argument("--root", required=True)
    parser.add_argument("--execute-approved", action="store_true")
    parser.add_argument("--offline-fixture", action="store_true")
    args = parser.parse_args(list(argv))
    root = Path(args.root)
    if args.offline_fixture and not _is_scratch_root(root):
        return _refuse(REASON_OFFLINE_PATH, root=root)
    with g1._use_batch(BATCH):
        if args.offline_fixture:
            # 离线夹具：给本进程树一个显式参考时刻（截止前 1 小时）。它不是真实入口；
            # 真实根带这个标记会在 g1 内部写入之前被拒，靠的是根路径事实。
            with g1.offline_clock(), g1._http_forbidden():
                report = g1.start_run(root, expected_root=_expected_root(root, True),
                                      execute_approved=bool(args.execute_approved),
                                      offline=True)
        else:
            report = g1.start_run(root, execute_approved=bool(args.execute_approved),
                                  offline=False)
    payload = _safe_result(report)
    payload["batch"] = BATCH
    payload["p1"] = report.get("p1")
    payload["p2"] = report.get("p2")
    if report.get("ok"):
        payload["verdict"] = _verdict(report.get("p1") or {}, report.get("p2") or {}, report)
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=1,
                                sort_keys=True, default=str) + "\n")
    return EXIT_OK if report.get("ok") else EXIT_REFUSED


COMMANDS = {"prepare": cmd_prepare, "audit": cmd_audit, "run": cmd_run}


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

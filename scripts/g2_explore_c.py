#!/usr/bin/env python3
"""G2 explore-c：维护可读 + 一次探索批的薄入口（离线可测；真实 prepare/run 由父级执行）。

本文件**不重建**实验仪器，只把既有四件接成 C 批：

* ``g2_next_live`` —— C_PROFILE 身份、30 请求账本、阶段门、隔离 env、唯一 run 包装；
* ``history_choice_natural_v2`` —— 自然观察控制器、H 检测、冻结与结构门；
* ``history_choice_experiment`` —— 两臂单变量、10 对影子、探索性统计；
* ``autonomous_life_assembly.runner_tick`` —— 每 tick 的维护/事件读侧，写本实验根内
  ``maintain_trace.jsonl``。

C 批固定参数：root ``/tmp/nyairo-g2-explore-20261103-c``，主实例 ``root/main``、authority
``root/memory-authority``、branches ``root/branches``；历史 234 / 累计 1000 / 单根 UTC 300；
总 30 = 采集 8 + 影子 20（10 对）+ 保留 2，**不做两臂执行承接**。真实业务钟不快进；总窗
7200 真秒（采集最多 5400），绝对 2026-11-03 21:45 CST 停新请求、19:30 CST 最晚启动。

边界：不 source 密钥、不探测产品 endpoint、不读写旧根、不删数据；``run`` 只在显式
``--execute-approved`` 下执行，真实根永远不能夹具运行。CLI 只输出固定字段/枚举/计数。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace
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

import g2_natural_dual_choice_probe as dual  # noqa: E402
import g2_next_live as live  # noqa: E402
import history_choice_experiment as baseline  # noqa: E402
import history_choice_natural_v2 as v2  # noqa: E402

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_REFUSED = 2

PROFILE = live.C_PROFILE
TRACE_FILENAME = "maintain_trace.jsonl"
TRACE_FIELDS = live.TRACE_FIELDS


# ---------------------------------------------------------------------------------
# 身份与文件
# ---------------------------------------------------------------------------------

def trace_path(profile: live.LiveProfile = PROFILE) -> Path:
    return Path(str(profile.maintain_trace_path or (profile.batch_root / TRACE_FILENAME)))


def _explore_plan(profile: live.LiveProfile = PROFILE) -> v2.ExperimentPlan:
    """离线短链用的 v2 计划：身份与该批一致，采集窗口/观察次数缩小到夹具尺度。"""

    return replace(
        v2.DEFAULT_PLAN,
        run_name=profile.run_name, run_id=profile.run_id, domain_id=profile.domain_id,
        request_cap=profile.request_cap, window_seconds=60,
        observation_interval_seconds=5,
        max_observations=3, future_candidate_total=profile.request_cap,
        future_shadow_requests=profile.shadow_cap,
        future_continuation_requests=profile.continuation_cap,
        future_diagnostic_requests=0, arm_request_cap=profile.arm_cap,
        utc_day_cap=profile.utc_day_cap, real_root=str(profile.batch_root),
        experiment_dirname="experiment")


def _read_json(path: Path | str) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return dict(data) if isinstance(data, Mapping) else {}


# ---------------------------------------------------------------------------------
# 离线检查
# ---------------------------------------------------------------------------------

def _run_unit_tests(pattern: str = "test_g2_explore_c.py") -> dict[str, Any]:
    """真正收集并运行本功能的新测试；0 例也算失败（不允许 SKIP 假装通过）。

    收集失败只报固定 ``reason_code`` + 异常类型名，不输出异常正文。
    """

    try:
        suite = unittest.TestLoader().discover(
            start_dir=str(REPO_ROOT / "tests"), pattern=str(pattern))
    except Exception as exc:
        return {"ok": False, "reason_code": "UNIT_DISCOVERY_ERROR",
                "kind": type(exc).__name__, "run": 0}
    with open(os.devnull, "w") as sink:
        result = unittest.TextTestRunner(verbosity=0, stream=sink).run(suite)
    return {"ok": (bool(result.wasSuccessful()) and int(result.testsRun) > 0
                   and len(result.skipped) == 0),
            "run": int(result.testsRun), "failures": len(result.failures),
            "errors": len(result.errors), "skipped": len(result.skipped)}


class _ScriptedShadowFactory:
    """影子两臂的离线 transport：每样本一条合法 NO_ACTION，只计数、不联网。"""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, _stage: str, _arm: str) -> Any:
        self.calls += 1
        return v2.CountingTransport(v2._no_action_text(), label="explore-shadow")


def _trace_checks(traces: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not traces:
        return {"trace_rows": 0, "trace_fields": False, "trace_world_three_state": False,
                "trace_world_signature_three_state": False,
                "trace_feed_types_fixed": False}
    row = dict(traces[-1])
    fixed_events = {"RESUME_ELIGIBILITY_OPENED", "ARTIFACT_CHANGED", "WORLD_OPPORTUNITY",
                    "UNKNOWN_EVENT_TYPE"}
    return {
        "trace_rows": len(traces),
        "trace_fields": all(key in row for key in TRACE_FIELDS),
        "trace_counts_not_bool": all(
            isinstance(row.get(key), (int, type(None))) and not isinstance(row.get(key), bool)
            for key in ("checked", "changed", "satisfied", "emitted_eligibilities")),
        # revision 三态只有真实前后值才不是 UNKNOWN；签名另名字段独立三态。
        "trace_world_three_state": str(row.get("world_revision_changed")) in {
            "CHANGED", "NOT_CHANGED", "UNKNOWN"},
        "trace_world_signature_three_state": str(row.get("world_signature_changed")) in {
            "CHANGED", "NOT_CHANGED", "UNKNOWN"},
        "trace_feed_types_fixed": all(
            str(kind) in fixed_events for kind in (row.get("feed_events") or ())),
    }


def _budget_gate_probe(scratch: Path, profile: live.LiveProfile = PROFILE) -> dict[str, Any]:
    """发送前硬门：影子 20 之后第 21 次拒；总账本 30 之后第 31 次拒。"""

    class _View:
        def __init__(self, calls: int) -> None:
            self.calls = int(calls)

        def view(self) -> dict[str, Any]:
            return {"run_window_calls": self.calls}

    shadow_profile = replace(profile, batch_root=scratch / "gate-shadow")
    shadow_ledger = live.LiveStageLedger(shadow_profile, _View(0))
    shadow_ledger.start_stage("shadow")
    allowed_20 = shadow_ledger.assert_room("shadow", {"run_window_calls": 19})
    refused_21 = ""
    try:
        shadow_ledger.assert_room("shadow", {"run_window_calls": 20})
    except baseline.BatchRefused as exc:
        refused_21 = exc.reason_code

    # 隔离阶段门，单看总账本 30→31（阶段 20 门由上一段覆盖）。
    total_profile = replace(profile, batch_root=scratch / "gate-total", shadow_cap=100)
    total_ledger = live.LiveStageLedger(total_profile, _View(0))
    total_ledger.start_stage("shadow")
    allowed_30 = total_ledger.assert_room("shadow", {"run_window_calls": 29})
    refused_31 = ""
    try:
        total_ledger.assert_room("shadow", {"run_window_calls": 30})
    except baseline.BatchRefused as exc:
        refused_31 = exc.reason_code
    return {
        "shadow_20_allowed": bool(allowed_20.get("stage_remaining") == 1),
        "shadow_21_refused": refused_21 == live.R_STAGE_CAP,
        "total_30_allowed": bool(allowed_30.get("total_remaining") == 1),
        "total_31_refused": refused_31 == live.R_TOTAL_CAP,
    }


def _trajectory_digest(chain: Mapping[str, Any]) -> dict[str, Any]:
    """一次短链的「控制流轨迹」：决策/请求/效果序列，不含时间戳、ID 或正文。

    observer on/off 只允许影响 trace 写盘，不允许改变这个摘要。
    """

    checks = chain.get("checks") if isinstance(chain.get("checks"), Mapping) else {}
    return {
        "steps": [tuple(str(step.get(key)) for key in
                        ("step", "status", "after", "state", "open_for_h", "ok"))
                  for step in (chain.get("steps") or ())
                  if isinstance(step, Mapping)],
        "h_established": bool(checks.get("h_established")),
        "owner_version_created": bool(checks.get("owner_version_created")),
        "structural_gate_ok": bool(checks.get("structural_gate_ok")),
        "shadow_requests": (chain.get("shadow") or {}).get("requests_used"),
        "transport_calls": chain.get("transport_calls"),
    }


def _formal_scratch_chain(*, with_observer: bool = True,
                          profile: live.LiveProfile = PROFILE) -> dict[str, Any]:
    """短正式链：真 Supply/M0/runtime → 维护读面 → 脚本 H/PAUSE → 结构门 → 10 对影子。

    Decision 与内容是脚本替身（只抄 AG-1 当场给出的候选/条件），owner、M0、AG-1/AG-2、
    LR-4、结构门与影子发送边界都是真的。它是接线与门位置的证据，不是自然采集。
    ``with_observer=False`` 跑同一条链但不装 tick observer，用来证明读侧旁路不改变控制流。
    """

    scratch = Path(tempfile.mkdtemp(prefix="g2-explore-%s-offline-"
                                    % str(profile.batch_key)))
    plan = _explore_plan(profile)
    decide = dual.DualChoiceTransport()
    content = v2.ScriptedAppendTransport()
    context = v2._open_scratch(scratch, "instance-c", plan,
                               decision_transport=decide, content_transport=content)
    if not context.get("ok"):
        return {"ok": False, "reason_code": str(context.get("reason_code") or "SETUP_FAILED"),
                "kind": str(context.get("detail_kind") or "") or None,
                "with_observer": bool(with_observer), "scratch_parent": str(scratch)}
    bundle = context["bundle"]
    traces: list[dict[str, Any]] = []
    if with_observer and isinstance(bundle.get("assembled"), dict):
        bundle["assembled"]["tick_observer"] = lambda row: traces.append(dict(row))
    checks: dict[str, Any] = {}
    steps: list[dict[str, Any]] = []
    result: dict[str, Any] = {}
    try:
        runtime = bundle["runtime"]
        cycle = bundle["cycle"]

        # 1) 真实一轮：脚本 START → 真 writer → 真 owner 版本
        tick1 = v2._advance_from_runner(bundle)()
        h_activity, h_ref = dual._active_artifact(runtime)
        evidence = tick1.get("extra") if isinstance(tick1.get("extra"), Mapping) else {}
        steps.append({"step": "start", "status": tick1.get("status"),
                      "h_ref": bool(h_ref), "owner_version": bool(
                          evidence.get("owner_version_created"))})

        # 2) 真实一轮：脚本 PAUSE（抄本轮 offer 的条件）并释放前台
        tick2 = v2._advance_from_runner(bundle)() if h_ref else {}
        paused = runtime.activity_read.get_activity(str(h_activity)) if h_activity else {}
        steps.append({"step": "pause", "status": tick2.get("status"),
                      "after": str((paused or {}).get("status") or "")})

        # 3) 正式维护（夹具时间）：LR-4 自己判条件满足并开资格
        upkeep = {}
        open_for_h: list[dict[str, str]] = []
        if h_activity and str((paused or {}).get("status") or "") == "PAUSED":
            upkeep = cycle.maintain_waiting(
                observed_at=dual._after(dual.ADVANCE_MARGIN_MINUTES)) or {}
            open_for_h = [row for row in dual._open_eligibilities(runtime)
                          if row["status"] == "OPEN"
                          and row["activity_id"] == str(h_activity)]
        steps.append({"step": "maintain", "state": upkeep.get("state"),
                      "open_for_h": len(open_for_h)})

        work_refs = ["artifact:%s" % row["artifact_id"]
                     for row in context["prepared"]["works"]]
        gate = v2.freeze_and_check(
            bundle, h_ref=h_ref or "artifact:absent", work_refs=work_refs,
            h_version=(str(evidence.get("owner_version_id") or "") or None),
            sleep=lambda _s: None) if h_ref else {}
        gate_ok = bool(gate.get("ok"))
        steps.append({"step": "gate", "ok": gate_ok,
                      "reason_code": str(gate.get("reason_code") or "")})

        checks.update(_trace_checks(traces))
        checks["h_established"] = bool(h_ref)
        checks["owner_version_created"] = bool(evidence.get("owner_version_created"))
        checks["pause_round_ran"] = str((paused or {}).get("status") or "") == "PAUSED"
        checks["eligibility_open_for_h"] = bool(open_for_h)
        checks["structural_gate_ok"] = gate_ok

        shadow: dict[str, Any] = {}
        statistics: dict[str, Any] = {}
        if gate_ok:
            frozen = gate["frozen"]
            gate_baseline = dict((gate.get("gate") or {}).get("baseline") or {})
            stage_profile = replace(profile, batch_root=scratch / "shadow-profile")
            stage_profile.stages_path.parent.mkdir(parents=True, exist_ok=True)
            stage_ledger = live.LiveStageLedger(stage_profile, context["budget"])
            stage_ledger.start_stage("shadow")
            shadow_factory = _ScriptedShadowFactory()
            # 影子阶段的「本阶段 20 次」要减去采集已经用掉的部分（同一本持久账本）。
            prior_calls = int(context["budget"].view().get("run_window_calls") or 0)
            shadow = baseline.run_shadow(
                bundle, frozen=frozen, baseline=gate_baseline, ledger=stage_ledger,
                budget=context["budget"], env=context["env"],
                deadline_check=lambda: False, cognition_factory=shadow_factory,
                latch=context["latch"], pair_count=int(profile.pairs))
            statistics = baseline.shadow_statistics(shadow, {}, pair_count=live.C_PAIRS)
            checks["shadow_pairs_complete"] = (
                int(statistics.get("pairs_complete") or 0) == int(profile.pairs))
            checks["shadow_requests_20"] = (
                int(shadow.get("requests_used") or 0) - prior_calls
                == int(profile.shadow_cap))
            checks["shadow_exploratory_only"] = (
                statistics.get("conclusion") == "EXPLORATORY_ONLY"
                and statistics.get("exploratory_only") is True
                and statistics.get("mcnemar_two_sided_p") is None)
            checks["shadow_transport_matches"] = (
                shadow_factory.calls == int(profile.shadow_cap))
            # 两臂只在历史正文上不同：refs/字符数/换行对齐相同，正文不同。
            arm_a = list(frozen.get("prior_experiences") or [])
            arm_b = baseline.neutralise_history(arm_a)
            checks["two_arms_only_text"] = bool(arm_a) and all(
                str(a.get("artifact_ref")) == str(b.get("artifact_ref"))
                and str(a.get("version_ref")) == str(b.get("version_ref"))
                and len(str(a.get("text") or "")) == len(str(b.get("text") or ""))
                and str(a.get("text") or "") != str(b.get("text") or "")
                for a, b in zip(arm_a, arm_b))
        else:
            # 场景不可识别就如实留下具名拒，不把影子样本改写成可比结果。
            checks["shadow_pairs_complete"] = False
            checks["shadow_requests_20"] = False
            checks["shadow_exploratory_only"] = False
            checks["shadow_transport_matches"] = False
            checks["two_arms_only_text"] = False

        checks.update(_budget_gate_probe(scratch, profile))
        blocking = [key for key, value in checks.items()
                    if not value and key not in ("shadow_pairs_complete",
                                                 "shadow_requests_20",
                                                 "shadow_exploratory_only",
                                                 "shadow_transport_matches",
                                                 "two_arms_only_text")]
        # 影子区块必须整体成立；不完整就是未测，不报绿。
        shadow_keys = ("shadow_pairs_complete", "shadow_requests_20",
                       "shadow_exploratory_only", "shadow_transport_matches",
                       "two_arms_only_text")
        if gate_ok and not all(checks.get(key) for key in shadow_keys):
            blocking.append("shadow_block")
        checks["observer_enabled"] = bool(with_observer)
        # 采集调用走真实 runner_tick（maintain → feeds → run_due）→ _emit_tick_trace；
        # 只有 with_observer 时才落 trace 行。
        checks["trace_rows_from_runner_tick"] = bool(with_observer) and len(traces) > 0
        result = {
            "ok": not blocking, "blocking": blocking, "checks": checks, "steps": steps,
            "with_observer": bool(with_observer),
            "scratch_parent": str(scratch), "scratch_root": str(context.get("root")),
            "trace_path": str(scratch / "instance-c" / TRACE_FILENAME),
            "trace_rows": len(traces),
            "shadow": {"pairs_complete": statistics.get("pairs_complete"),
                       "a_h": statistics.get("a_h"), "b_h": statistics.get("b_h"),
                       "delta": statistics.get("delta"),
                       "conclusion": statistics.get("conclusion"),
                       "requests_used": shadow.get("requests_used"),
                       "stop_reason": shadow.get("stop_reason")},
            "transport_calls": len(decide.calls) + len(content.calls),
            "note": ("Decision/内容是脚本替身，H 也由脚本装配；它证明维护读面、正式可比输入与"
                     "影子发送边界的位置，不是自然采集或模型采用。"),
        }
        return result
    except Exception as exc:
        return {"ok": False, "reason_code": "SCRATCH_CHAIN_EXCEPTION",
                "kind": type(exc).__name__, "steps": steps,
                "with_observer": bool(with_observer), "scratch_parent": str(scratch)}
    finally:
        try:
            baseline.release_runtime(bundle)
        except Exception:
            pass
        supply = context.get("supply")
        if supply is not None:
            try:
                supply.stop()
            except Exception:
                pass


# ---------------------------------------------------------------------------------
# 网络守卫：复用 g1 既有离线实现（父进程 + owner 子进程继承）
# ---------------------------------------------------------------------------------

_GUARD_ENV_KEYS = ("PYTHONPATH", "NYAIRO_V06_OFFLINE_GUARD",
                   "NYAIRO_V06_OFFLINE_GUARD_STATUS_DIR")


def _install_child_guard(guard_dir: Path) -> dict[str, Any]:
    """把 g1 既有的子进程网络守卫写成 sitecustomize，让本次起的 owner 子进程继承。

    守卫本体来自 ``g1_real_adoption._CHILD_GUARD_SOURCE``（拒 AF_INET/AF_INET6/DNS/
    ``create_connection``，放行 AF_UNIX），不新写第二套。
    """

    import g1_real_adoption as g1

    guard_dir.mkdir(parents=True, exist_ok=True)
    (guard_dir / "sitecustomize.py").write_text(g1._CHILD_GUARD_SOURCE, encoding="utf-8")
    status_dir = guard_dir / "guard-status"
    previous = {key: os.environ.get(key) for key in _GUARD_ENV_KEYS}
    pythonpath = str(guard_dir)
    if previous["PYTHONPATH"]:
        pythonpath = pythonpath + os.pathsep + previous["PYTHONPATH"]
    os.environ["PYTHONPATH"] = pythonpath
    os.environ["NYAIRO_V06_OFFLINE_GUARD"] = "1"
    os.environ["NYAIRO_V06_OFFLINE_GUARD_STATUS_DIR"] = str(status_dir)
    return {"guard_dir": guard_dir, "status_dir": status_dir, "previous": previous}


def _restore_child_guard(state: Mapping[str, Any]) -> None:
    for key, value in (state.get("previous") or {}).items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = str(value)


def _guard_selfcheck() -> dict[str, Any]:
    """父进程实际拒四类、AF_UNIX 可用；只在守卫生效期间调用。"""

    import socket

    def refused(probe: Callable[[], Any]) -> bool:
        try:
            probe()
        except AssertionError:
            return True
        except Exception:
            return False
        return False

    try:
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.close()
        unix_ok = True
    except Exception:
        unix_ok = False
    return {
        "inet_refused": refused(lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM)),
        "inet6_refused": refused(lambda: socket.socket(socket.AF_INET6, socket.SOCK_STREAM)),
        "dns_refused": refused(lambda: socket.getaddrinfo("example.invalid", 80)),
        "connect_refused": refused(
            lambda: socket.create_connection(("192.0.2.1", 80), timeout=0.05)),
        "unix_ok": unix_ok,
    }


def _child_guard_status(status_dir: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    try:
        paths = sorted(Path(status_dir).glob("status-*.json"))
    except OSError:
        paths = []
    for path in paths:
        try:
            rows.append(dict(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    return {"processes": len(rows),
            "all_refuse": bool(rows) and all(
                bool(row.get("inet_refused")) and bool(row.get("inet6_refused"))
                and bool(row.get("dns_refused")) and bool(row.get("connect_refused"))
                and bool(row.get("unix_ok")) for row in rows)}


def offline_check(*, profile: live.LiveProfile = PROFILE,
                  test_pattern: str = "test_g2_explore_c.py",
                  observer_equivalence: bool = True) -> dict[str, Any]:
    """唯一离线检查：新测试真正收集运行 + 短正式链 + 发送前硬门 + 网络硬拒。

    整个函数体（单元、短链、清理）都在 ``g1_real_adoption._offline_network_guard`` 内；
    owner 子进程通过继承的 sitecustomize 守卫同样硬拒四类出站，AF_UNIX 保持可用。
    ``observer_equivalence`` 为真时才另跑一条不装 observer 的等价短链（C 原行为）；
    D2 只跑一条链，验证 tick/维护 trace 与预算接线即可。
    """

    import g1_real_adoption as g1

    guard_dir = Path(tempfile.mkdtemp(prefix="g2-explore-%s-guard-"
                                      % str(profile.batch_key)))
    guard_state = _install_child_guard(guard_dir)
    profile_view = {"run_id": profile.run_id, "domain_id": profile.domain_id,
                    "caps": profile.caps(), "deadline": profile.deadline.isoformat(),
                    "latest_start": (profile.latest_start.isoformat()
                                     if profile.latest_start else None)}
    try:
        with g1._offline_network_guard(guard_dir):
            selfcheck = _guard_selfcheck()
            gates: dict[str, Any] = {"unit_tests": _run_unit_tests(test_pattern)}
            try:
                on_chain = _formal_scratch_chain(with_observer=True, profile=profile)
            except Exception as exc:
                on_chain = {"ok": False, "reason_code": "CHAIN_EXCEPTION",
                            "kind": type(exc).__name__}
            if not isinstance(on_chain.get("checks"), dict):
                on_chain["checks"] = {}
            on_checks = on_chain["checks"]
            if observer_equivalence:
                try:
                    off_chain = _formal_scratch_chain(with_observer=False, profile=profile)
                except Exception as exc:
                    off_chain = {"ok": False, "reason_code": "CHAIN_EXCEPTION",
                                 "kind": type(exc).__name__}
                same_trajectory = _trajectory_digest(on_chain) == _trajectory_digest(off_chain)
                off_quiet = int(off_chain.get("trace_rows") or 0) == 0
                on_checks["observer_on_off_equivalent"] = bool(same_trajectory)
                on_checks["observer_off_no_trace_rows"] = bool(off_quiet)
                if on_chain.get("ok") and not (same_trajectory and off_quiet):
                    on_chain["ok"] = False
                    blocking = on_chain.get("blocking")
                    if not isinstance(blocking, list):
                        blocking = []
                        on_chain["blocking"] = blocking
                    blocking.append("observer_control")
                gates["observer_control"] = {
                    "ok": bool(same_trajectory and off_quiet),
                    "same_trajectory": bool(same_trajectory),
                    "on_trace_rows": int(on_chain.get("trace_rows") or 0),
                    "off_trace_rows": int(off_chain.get("trace_rows") or 0),
                }
            gates["scratch_chain"] = on_chain
            child_guard = _child_guard_status(guard_state["status_dir"])
            gates["network_guard"] = {
                "ok": all(selfcheck.get(key) for key in (
                    "inet_refused", "inet6_refused", "dns_refused", "connect_refused",
                    "unix_ok")) and bool(child_guard.get("all_refuse")),
                "selfcheck": selfcheck, "child": child_guard}
            blocking = [name for name, gate in gates.items() if not (gate or {}).get("ok")]
            return {"ok": not blocking, "blocking": blocking, "gates": gates,
                    "profile": profile_view, "model_called": 0, "request_sent": 0,
                    "formal_root_created": False,
                    "guard_dir": str(guard_dir),
                    "network": ("hard-disconnected: AF_INET/AF_INET6/DNS/create_connection "
                                "refused in parent and owner children; AF_UNIX allowed")}
    finally:
        _restore_child_guard(guard_state)


# ---------------------------------------------------------------------------------
# prepare / audit / run / status
# ---------------------------------------------------------------------------------

def prepare(argv: Sequence[str], *, profile: live.LiveProfile = PROFILE,
            batch: str = live.BATCH_C, prog: str = "g2_explore_c") -> int:
    parser = argparse.ArgumentParser(prog="%s prepare" % prog)
    parser.add_argument("--offline-fixtures", action="store_true")
    parser.add_argument("--root", default=None)
    args = parser.parse_args(list(argv))
    result = live.prepare(profile=profile, offline_fixtures=bool(args.offline_fixtures),
                          root=args.root, batch=batch)
    return _emit(result, code=EXIT_OK if result.get("ok") else EXIT_REFUSED)


def audit(argv: Sequence[str], *, profile: live.LiveProfile = PROFILE,
          batch: str = live.BATCH_C, prog: str = "g2_explore_c") -> int:
    parser = argparse.ArgumentParser(prog="%s audit" % prog)
    parser.add_argument("--offline-fixtures", action="store_true")
    parser.add_argument("--root", default=None)
    args = parser.parse_args(list(argv))
    result = live.audit(profile=profile, offline_fixtures=bool(args.offline_fixtures),
                        root=args.root, batch=batch)
    return _emit(result, code=EXIT_OK if result.get("ok") else EXIT_REFUSED)


def run(argv: Sequence[str], *, profile: live.LiveProfile = PROFILE,
        batch: str = live.BATCH_C, prog: str = "g2_explore_c") -> int:
    parser = argparse.ArgumentParser(prog="%s run" % prog)
    parser.add_argument("--execute-approved", action="store_true")
    args = parser.parse_args(list(argv))
    result = live.run(profile=profile, execute_approved=bool(args.execute_approved),
                      batch=batch)
    if result.get("ok"):
        code = EXIT_OK
    elif result.get("reason_code"):
        code = EXIT_REFUSED
    else:
        code = EXIT_ERROR
    return _emit(result, code=code)


def status(argv: Sequence[str], *, profile: live.LiveProfile = PROFILE,
           prog: str = "g2_explore_c") -> int:
    """纯读：身份、账本、窗口、最近一次维护摘要。不取锁、不建目录、不起服务。"""

    parser = argparse.ArgumentParser(prog="%s status" % prog)
    parser.parse_args(list(argv))
    prereg = _read_json(profile.prereg_path)
    batch = _read_json(profile.batch_path)
    window = _read_json(profile.window_path)
    state_root_text = str(prereg.get("state_root") or "")
    ledger = live._read_ledger_direct(profile, state_root_text) if state_root_text else {}
    trace_file = trace_path(profile)
    lines: list[str] = []
    try:
        lines = [line for line in trace_file.read_text(encoding="utf-8").splitlines()
                 if line.strip()]
    except OSError:
        lines = []
    last: dict[str, Any] = {}
    if lines:
        try:
            last = dict(json.loads(lines[-1]))
        except ValueError:
            last = {}
    payload = {
        "ok": True, "run_id": profile.run_id, "domain_id": profile.domain_id,
        "root": str(profile.batch_root), "prepared": bool(prereg),
        "state": batch.get("state"), "started_at": (batch.get("run") or {}).get("started_at"),
        "stop": batch.get("stop"), "history": profile.expected_history,
        "caps": profile.caps(),
        "window": {"present": bool(window),
                   "deadline_wall": window.get("deadline_wall")},
        "ledger": live._safe_budget(ledger) if ledger else None,
        "maintain_trace": {
            "present": bool(lines), "lines": len(lines),
            "tick": last.get("tick"), "observed_at": last.get("observed_at"),
            "checked": last.get("checked"), "changed": last.get("changed"),
            "satisfied": last.get("satisfied"),
            "emitted_eligibilities": last.get("emitted_eligibilities"),
            "feed_events": last.get("feed_events"),
            "world_revision_changed": last.get("world_revision_changed"),
            "world_revision_basis": last.get("world_revision_basis"),
            "world_signature_changed": last.get("world_signature_changed"),
            "world_signature_basis": last.get("world_signature_basis"),
            "quiet_reason": last.get("quiet_reason"), "status": last.get("status"),
        },
        "deadline": profile.deadline.isoformat(),
        "latest_start": (profile.latest_start.isoformat()
                         if profile.latest_start else None),
    }
    return _emit(payload, code=EXIT_OK)


def _emit(payload: Mapping[str, Any], *, code: int) -> int:
    sys.stdout.write(json.dumps(live._public_run(payload), ensure_ascii=False, indent=1,
                                sort_keys=True) + "\n")
    sys.stdout.flush()
    return int(code)


COMMANDS = {"offline-check": lambda argv: _emit_offline_check(argv),
            "prepare": prepare, "audit": audit, "run": run, "status": status}


def _emit_offline_check(_argv: Sequence[str], *, profile: live.LiveProfile = PROFILE,
                        test_pattern: str = "test_g2_explore_c.py",
                        observer_equivalence: bool = True) -> int:
    result = offline_check(profile=profile, test_pattern=test_pattern,
                           observer_equivalence=observer_equivalence)
    print(json.dumps({key: result[key] for key in ("ok", "blocking", "model_called",
                                                   "request_sent", "network")},
                     ensure_ascii=False, indent=1))
    for name, gate in (result.get("gates") or {}).items():
        detail = ""
        if not gate.get("ok"):
            parts: list[str] = []
            if gate.get("blocking"):
                parts.append("blocking=%s" % ",".join(str(item) for item in gate["blocking"]))
            if gate.get("reason_code"):
                parts.append("reason_code=%s" % gate["reason_code"])
            if gate.get("kind"):
                parts.append("kind=%s" % gate["kind"])
            if gate.get("run") is not None:
                parts.append("run=%s failures=%s errors=%s skipped=%s" % (
                    gate.get("run"), gate.get("failures"), gate.get("errors"),
                    gate.get("skipped")))
            detail = (" " + " ".join(parts)) if parts else ""
        print("[%s] ok=%s%s" % (name, gate.get("ok"), detail))
    gates = result.get("gates") or {}
    chain = gates.get("scratch_chain") or {}
    if chain.get("checks"):
        print("checks:", json.dumps(chain["checks"], ensure_ascii=False, sort_keys=True))
        print("shadow:", json.dumps(chain.get("shadow") or {}, ensure_ascii=False,
                                    sort_keys=True))
    guard = gates.get("network_guard") or {}
    if guard:
        print("network_guard:", json.dumps(
            {"selfcheck": guard.get("selfcheck"), "child": guard.get("child")},
            ensure_ascii=False, sort_keys=True))
    control = gates.get("observer_control") or {}
    if control:
        print("observer_control:", json.dumps(control, ensure_ascii=False, sort_keys=True))
    return EXIT_OK if result.get("ok") else EXIT_ERROR


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
        raise
    except Exception as exc:
        # 顶层安全壳：只报固定 reason_code 与异常类型名，不输出正文或 traceback。
        sys.stdout.write(json.dumps({"ok": False, "reason_code": "TOP_LEVEL_EXCEPTION",
                                     "kind": type(exc).__name__},
                                    ensure_ascii=False, sort_keys=True) + "\n")
        sys.stdout.flush()
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())

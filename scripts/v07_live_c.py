#!/usr/bin/env python3
"""0.7 唯一真实短日 C：在 A 的薄入口上只改配置，不改总脑。

    python -B scripts/v07_live_c.py offline-check
    python -B scripts/v07_live_c.py prepare
    python -B scripts/v07_live_c.py audit
    python -B scripts/v07_live_c.py status
    python -B scripts/v07_live_c.py run --execute-approved

这是什么
--------
A 已经证明「单一正式路径 + 一次性标记 + 实验额度 + bounded_failstop」这套薄入口可用。
C **不复制**它：prepare / audit / run / 额度闸门 / 一次性标记 / 首次故障停全部复用
``scripts/v07_live_a.py``，只固定一份新的 C 身份，并把 **check interval 覆写为 5 逻辑分钟**。

B 为什么后半程完全沉默（诊断结论）
----------------------------------
B 的实例 env 是 ``CHIYO_AUTONOMOUS_CHECK_INTERVAL_MINUTES=30``（one_day_life 默认），
而 B 的整个真实窗口恰好也是 1800 秒 = 30 逻辑分钟（rate=1）。B 最后一次
``last_check_at`` 是 ``2026-11-09T02:00:29.134+00:00``（第三个事件唤醒时写的），
下一次自然窗口是 ``02:30:29``，已经越过 horizon ``02:30:00``，所以整段只有**一个**到期
check window（``NEVER_CHECKED`` 那次），其余全是 ``NOT_DUE``。这不是候选缺失也不是 QUIET：
B 的 ``candidate_audit_journal`` 在 10:00:06/10:00:29 两轮都物化了 MOVE/stay/pose/READ 等多类
候选，模型只是没选 MOVE；从 PLACE 完成（02:00:41）之后根本没有再到期过窗口。C 把窗口缩到
5 分钟，同样 1800 秒里给 5–6 次自然重新观察，不改任何排序/权重/首选，也不清 QUIET。

固定身份（没有 --root，不能换旧根）
----------------------------------
* root ``/tmp/nyairo-v07-live-20261109-c``
* authority ``/tmp/nyairo-v07-live-20261109-c-authority/authority.db``
* run ``run:v07-live:20261109-c``，域 ``v07:live:20261109-c``
* history 566、本 run 新增上限 20、UTC 日 300、共同总限 1000（最坏 586）
* 逻辑起点 ``2026-11-09T11:00:00+09:00``，1:1（rate=1.0，逻辑秒=真实秒=1800）
* 最晚启动 ``2026-11-09T23:15:00+08:00``，绝对停 ``2026-11-09T23:50:00+08:00``

只改配置
--------
prepare 成功后，只把本实例 ``one-day/instance.json`` 里的那一个 key
``CHIYO_AUTONOMOUS_CHECK_INTERVAL_MINUTES`` 改成 ``5``。不改 one_day_life 默认、不改
runner/due 逻辑、不改预算/provider/cap/retry/timeout、不动 Memory 与 owner。audit 把
「目标值」与「实例里真正落盘值 / resident 汇总里的实际值」分开报，缺失保留 UNKNOWN。

输出安全
--------
stdout/audit/status 只输出固定枚举、布尔、计数、安全 id、时间与地点/Activity 状态；
不输出 prompt/completion/reasoning/short_rationale/作品正文/env/token/header/endpoint，
也不抄异常原文。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

REPO = Path(__file__).resolve().parents[1]
for _entry in (REPO, REPO / "scripts", REPO / "tests", REPO / "components",
               REPO / "components" / "life" / "scripts",
               REPO / "components" / "supply",
               REPO / "components" / "world" / "scripts"):
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

import demo_business_clock as dbc  # noqa: E402
import fast_day as fast  # noqa: E402
import v07_live_a as base  # noqa: E402

EXIT_OK = 0
EXIT_REFUSED = 1
OFFLINE_PREFIX = "v07-live-c-offline-"
TEST_PATTERN = "test_v07_live_c.py"
CONSUME_FILENAME = "v07-live-c.consume.json"
SCHEMA_AUDIT = "v07.live.c.audit.v1"
INTERVAL_ENV = "CHIYO_AUTONOMOUS_CHECK_INTERVAL_MINUTES"


@dataclass(frozen=True)
class Identity(base.Identity):
    """C 身份 = A 的字段 + 一个只属于本实例的 check interval（分钟）。"""

    check_interval_minutes: int = 5

    def profile(self) -> dict[str, Any]:
        profile = dict(super().profile())
        profile["check_interval_minutes"] = int(self.check_interval_minutes)
        return profile


IDENTITY = Identity(
    root=Path("/tmp/nyairo-v07-live-20261109-c"),
    authority=Path("/tmp/nyairo-v07-live-20261109-c-authority/authority.db"),
    run_name="v07-live:20261109-c",
    run_id="run:v07-live:20261109-c",
    domain="v07:live:20261109-c",
    anchor=datetime.fromisoformat("2026-11-09T11:00:00+09:00"),
    latest_start=datetime.fromisoformat("2026-11-09T23:15:00+08:00"),
    deadline=datetime.fromisoformat("2026-11-09T23:50:00+08:00"),
    historical_requests=566,
)
PROFILE = IDENTITY.profile()


# ---------------------------------------------------------------------------------------------
# check interval：只读视图 + 唯一一次配置覆写
# ---------------------------------------------------------------------------------------------


def _persisted_interval(root: Path | str) -> Any:
    """实例文件里真正写下的分钟数；读不到就是 UNKNOWN，不猜 0。"""

    try:
        from components.phone import life as phone_life

        nested = phone_life.read_nested_life(root)
    except Exception:
        return "UNKNOWN"
    if not nested:
        return "UNKNOWN"
    raw = str(dict(nested.get("environment") or {}).get(INTERVAL_ENV) or "").strip()
    return int(raw) if raw.isdigit() and len(raw) < 6 else "UNKNOWN"


def _runner_interval(root: Path | str) -> Any:
    """resident 汇总里 runner 实际拿到的分钟数；没有汇总就是 UNKNOWN。"""

    try:
        from components.phone import life as phone_life

        nested = phone_life.read_nested_life(root)
        if not nested:
            return "UNKNOWN"
        state = Path(str(phone_life.life_state_root(root, nested)))
    except Exception:
        return "UNKNOWN"
    summary = base._read_json(state / "resident_summary.json")
    value = summary.get("interval_minutes")
    return value if isinstance(value, int) and not isinstance(value, bool) else "UNKNOWN"


def _interval_view(root: Path | str, identity: Identity = IDENTITY) -> dict[str, Any]:
    return {
        "target_minutes": int(identity.check_interval_minutes),
        "persisted_minutes": _persisted_interval(root),
        "runner_minutes": _runner_interval(root),
        "source": "IDENTITY_TARGET + INSTANCE_ENV + RESIDENT_SUMMARY",
        "note": "目标值是入口固定的；persisted 是实例落盘；runner 是 child 实际装配",
    }


def _patch_interval(root: Path | str, minutes: int) -> dict[str, Any]:
    """只改本实例 instance.json 的那一个 key；失败具名返回，不猜默认。"""

    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        return {"ok": False, "reason_code": "CHECK_INTERVAL_INVALID"}
    if not 1 <= minutes <= 1440:
        return {"ok": False, "reason_code": "CHECK_INTERVAL_INVALID"}
    try:
        from components.phone import life as phone_life

        nested = phone_life.read_nested_life(root)
    except Exception as exc:
        return {"ok": False, "reason_code": "INSTANCE_UNREADABLE",
                "exception_type": type(exc).__name__}
    if nested is None:
        return {"ok": False, "reason_code": "INSTANCE_MISSING"}
    environment = nested.setdefault("environment", {})
    previous = environment.get(INTERVAL_ENV)
    environment[INTERVAL_ENV] = str(minutes)
    try:
        phone_life._write_instance_file(phone_life.nested_life_dir(root), nested)
    except Exception as exc:
        return {"ok": False, "reason_code": "INSTANCE_WRITE_FAILED",
                "exception_type": type(exc).__name__}
    return {"ok": True, "target_minutes": minutes, "persisted_minutes": minutes,
            "previous_minutes": (int(previous) if str(previous or "").strip().isdigit()
                                 else previous if previous is not None else None)}


# ---------------------------------------------------------------------------------------------
# prepare / audit / run：薄包 A 的正式路径，只加一次 interval 覆写
# ---------------------------------------------------------------------------------------------


def configure(root: Path | str, authority: Path | str, *, identity: Identity = IDENTITY,
              provision: bool = True, deadline: Optional[datetime] = None,
              allow_night_start: bool = True) -> dict[str, Any]:
    result = base.configure(root, authority, identity=identity, provision=provision,
                            deadline=deadline, allow_night_start=allow_night_start)
    if provision and isinstance(result, dict) and result.get("ok"):
        applied = _patch_interval(Path(str(root)), identity.check_interval_minutes)
        result["check_interval"] = applied
        if not applied.get("ok"):
            result["ok"] = False
            result["reason_code"] = applied.get("reason_code") or "CHECK_INTERVAL_PATCH_FAILED"
    return result


def prepare(*, identity: Identity = IDENTITY) -> dict[str, Any]:
    now = base._now()
    if now > identity.latest_start:
        return {"ok": False, "reason_code": "LATEST_START_PASSED",
                "profile": identity.profile()}
    if identity.root.is_symlink() or identity.authority.parent.is_symlink():
        return {"ok": False, "reason_code": "ROOT_NOT_FRESH",
                "profile": identity.profile()}
    if identity.root.exists() or identity.authority.parent.exists():
        return {"ok": False, "reason_code": "FORMAL_ROOT_NOT_FRESH",
                "profile": identity.profile()}
    result = configure(identity.root, identity.authority, identity=identity)
    if not result.get("ok"):
        return {"ok": False,
                "reason_code": base._safe_enum(result.get("reason_code"), "PREPARE_REFUSED"),
                "profile": identity.profile()}
    report = audit(identity.root, identity=identity)
    report["created"] = True
    report["model_called"] = False
    report["request_sent"] = False
    report["check_interval_applied"] = result.get("check_interval")
    return report


def audit(root: Path | str = IDENTITY.root, *, identity: Identity = IDENTITY) -> dict[str, Any]:
    report = base.audit(root, identity=identity)
    report["schema"] = SCHEMA_AUDIT
    if report.get("prepared"):
        marker = Path(str(root)) / "run" / CONSUME_FILENAME
        start = dict(report.get("start") or {})
        start["consumed"] = marker.exists()
        report["start"] = start
        report["check_interval"] = _interval_view(root, identity)
    else:
        report["check_interval"] = {
            "target_minutes": int(identity.check_interval_minutes),
            "persisted_minutes": "UNKNOWN", "runner_minutes": "UNKNOWN",
            "source": "NOT_PREPARED"}
    return report


def _consume_once(identity: Identity = IDENTITY) -> bool:
    """首 send 前独占创建一次性标记；没有删除路径，第二次永远 False。"""

    path = identity.root / "run" / CONSUME_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    except OSError:
        return False
    try:
        payload = {"run_id": identity.run_id, "profile": identity.profile(),
                   "consumed_at": base._iso(base._now()), "pid": os.getpid(),
                   "note": "one-shot: no delete/refund/replay path"}
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        return False
    return True


def run(*, identity: Identity = IDENTITY, execute_approved: bool = False,
        now: Optional[datetime] = None) -> dict[str, Any]:
    if not execute_approved:
        return {"ok": False, "reason_code": "EXECUTE_FLAG_MISSING",
                "model_called": False, "profile": identity.profile()}
    moment = now or base._now()
    report = audit(identity.root, identity=identity)
    refusal = base._run_precheck(identity, report, moment)
    if refusal:
        return {"ok": False, "reason_code": refusal, "model_called": False,
                "profile": identity.profile()}
    if _persisted_interval(identity.root) != int(identity.check_interval_minutes):
        return {"ok": False, "reason_code": "CHECK_INTERVAL_MISMATCH",
                "model_called": False, "profile": identity.profile()}
    model = base._model_preflight(identity)
    if not model.get("usable"):
        return {"ok": False, "reason_code": "MODEL_NOT_CONFIGURED",
                "model": {"usable": False, "reason": model.get("reason")},
                "model_called": False, "profile": identity.profile()}
    if not _consume_once(identity):
        return {"ok": False, "reason_code": "ALREADY_CONSUMED",
                "model_called": False, "profile": identity.profile()}
    previous = os.environ.get(fast.WORLD_GATE_ENV)
    os.environ[fast.WORLD_GATE_ENV] = identity.world_gate
    try:
        supervisor = fast.FastDaySupervisor(
            identity.root, poll_seconds=1, room_living=identity.room_living,
            ambient_events=identity.ambient_events)
        supervisor.child_stop_timeout = 15
        supervisor.owner_stop_timeout = 15
        base._install_cap_watch(supervisor, identity)
        result = supervisor.start(duration_seconds=identity.real_seconds)
    finally:
        if previous is None:
            os.environ.pop(fast.WORLD_GATE_ENV, None)
        else:
            os.environ[fast.WORLD_GATE_ENV] = previous
    post = audit(identity.root, identity=identity)
    return base._run_projection(result, post, identity)


# ---------------------------------------------------------------------------------------------
# 离线验收：新测试 + 唯一一条最短正式装配（零模型）+ 网络硬拒
# ---------------------------------------------------------------------------------------------


def _run_new_tests() -> dict[str, Any]:
    try:
        suite = unittest.TestLoader().discover(str(REPO / "tests"), pattern=TEST_PATTERN)
    except Exception as exc:
        return {"ok": False, "run": 0, "skipped": 0,
                "exception_type": type(exc).__name__}
    with open(os.devnull, "w") as sink:
        result = unittest.TextTestRunner(verbosity=0, stream=sink).run(suite)
    return {"ok": bool(result.wasSuccessful()), "run": int(result.testsRun),
            "skipped": len(result.skipped), "failures": len(result.failures),
            "errors": len(result.errors)}


def _formal_scratch_chain(scratch: Path, checks: "base._Checks") -> None:
    """唯一一条最短正式装配：真实 prepare + 真实 supervisor + 注入一次脚本失败。

    这条链只证明「C 的配置真的进了实例与子进程」「首次 provider 故障仍然停」「一次性标记
    不能复活」；它不驱动真实 Decision，也不碰正式根。
    """

    import autonomous_life_assembly as assembly
    import autonomous_life_runner as alr
    import life_pause
    from unittest import mock
    from dataclasses import replace

    from components.phone import demo, life as phone_life

    future = base._now() + timedelta(days=1)
    identity = replace(IDENTITY, root=scratch / "instance",
                       authority=scratch / "authority" / "authority.db",
                       deadline=future, latest_start=future)
    prepared = configure(identity.root, identity.authority, identity=identity,
                         deadline=future)
    checks.check("prepare builds one real isolated C instance with zero model calls",
                 prepared.get("ok") is True
                 and prepared.get("model_called") is False
                 and prepared.get("request_sent") is False)
    if not prepared.get("ok"):
        return
    state = Path(str(prepared["state_root"]))
    checks.check("prepare persists C check interval 5 logical minutes in the instance",
                 _persisted_interval(identity.root) == 5)
    try:
        view = base._instance_view(identity.root)
        overrides = {"WORLD_BODY_INTENT_MUTATION_GATE": identity.world_gate}
        overrides.update(demo._serve_feature_overrides(room_living=identity.room_living))
        life_env = phone_life.map_life_environment(
            view["phone"], view["nested"], base_env={}, feature_overrides=overrides)
        runner = alr.AutonomousLifeRunner(
            type("CScratch", (), {"state_root": scratch / "runner"})(),
            state_root=scratch / "runner", env=life_env)
        runner_minutes = int(runner.interval_minutes)
        child_ok = life_env.get(INTERVAL_ENV) == "5" and runner_minutes == 5
    except Exception:
        child_ok = False
    checks.check("real Life binding + real scheduler read 5 minutes from the child env",
                 child_ok)
    pre = audit(identity.root, identity=identity)
    checks.check("C audit keeps cap20/history566/UTC300/total1000 and clock 1800s rate1",
                 pre.get("ok") is True
                 and pre["caps"]["new_cap"] == 20
                 and pre["caps"]["historical_requests"] == 566
                 and pre["caps"]["utc_day_cap"] == 300
                 and pre["caps"]["authorized_total_limit"] == 1000
                 and pre["clock"]["rate"] == 1.0
                 and pre["clock"]["no_compression"] is True
                 and pre["check_interval"]["target_minutes"] == 5
                 and pre["check_interval"]["persisted_minutes"] == 5
                 and pre["start"]["started_at"] is None
                 and pre["window"]["present"] is False
                 and pre["ledger"]["run_window_calls"] == 0
                 and pre["ledger"]["utc_day_calls"] == 0)

    experiment_file = dbc.experiment_path(state)
    experiment_before = (experiment_file.read_text(encoding="utf-8")
                         if experiment_file.exists() else None)
    before = sorted(str(path.relative_to(identity.root))
                    for path in identity.root.rglob("*") if path.is_file())
    audit(identity.root, identity=identity)
    audit(identity.root, identity=identity)
    after = sorted(str(path.relative_to(identity.root))
                   for path in identity.root.rglob("*") if path.is_file())
    experiment_after = (experiment_file.read_text(encoding="utf-8")
                        if experiment_file.exists() else None)
    checks.check("C audit twice creates no file, no high-water and no ledger entry",
                 before == after
                 and experiment_before == experiment_after
                 and not (state / "life_request_budget.json").exists())

    env = dict(base._ENV, CHIYO_AUTONOMOUS_RUN_ID=identity.run_id,
               NYA02_BOUNDED_FAILSTOP="1",
               NYA02_EXPERIMENT_CUMULATIVE_TOTAL="1000",
               NYA02_FASTDAY_CLOCK=str(dbc.clock_path(state)))
    supervisor = fast.FastDaySupervisor(identity.root, poll_seconds=0.1,
                                        no_act=True, offline_scripted=True,
                                        room_living=identity.room_living)
    original_sleep = supervisor._sleep
    failed: list[bool] = []

    def sleep(seconds: float) -> None:
        original_sleep(seconds)
        if not failed and (state / "resident_journal.jsonl").exists():
            with mock.patch("profile_model_transport.OpenAICompatibleTransport",
                            base._ScriptedFailureTransport):
                budget = assembly.request_budget(env, state)
                guard = assembly.pause_request_admission(state, budget)
                cognition = assembly.model_cognition(
                    env, requests=budget, admission=guard, pause_guard=guard)
                try:
                    cognition.invoke(base._request())
                except Exception:
                    pass
                failed.append(True)

    supervisor._sleep = sleep
    base._ScriptedFailureTransport.sends = 0
    report = supervisor.start(duration_seconds=25)
    checks.check("first provider failure stops the bounded C run and charges one",
                 bool(failed)
                 and report.get("ok") is False
                 and (report.get("supervisor") or {}).get("stop_reason")
                 == "BOUNDED_RUN_FAILSTOP"
                 and base._ScriptedFailureTransport.sends == 1
                 and bool((report.get("supervisor") or {}).get("child", {}).get("exited"))
                 and bool((report.get("supervisor") or {}).get("owners_stopped"))
                 and base._read_json(dbc.clock_path(state)).get("state") == "STOPPED"
                 and bool(life_pause.gate(state).get("paused")))
    again = supervisor.start(duration_seconds=1)
    post = audit(identity.root, identity=identity)
    charged = post["ledger"]["run_window_calls"]
    checks.check("stopped C run cannot revive and the failure stays recorded",
                 again.get("ok") is False
                 and post["failure"]["locked"] is True
                 and post["failure"]["category"] == "PROVIDER_OR_TRANSPORT_FAILURE"
                 and isinstance(charged, int) and 1 <= charged <= 20)
    consume_identity = replace(identity, root=scratch / "one-shot")
    (consume_identity.root / "run").mkdir(parents=True, exist_ok=True)
    checks.check("one-shot marker C: first consumes, second refuses, marker kept",
                 _consume_once(consume_identity) is True
                 and _consume_once(consume_identity) is False
                 and (consume_identity.root / "run" / CONSUME_FILENAME).exists())


def _fingerprint(path: Path) -> Any:
    base_path = Path(path)
    if not base_path.exists():
        return None
    rows: list[tuple[str, bool, int, int]] = []
    try:
        for item in sorted(base_path.rglob("*")):
            try:
                stat = item.stat()
            except OSError:
                continue
            rows.append((str(item.relative_to(base_path)), bool(item.is_dir()),
                         int(stat.st_size), int(stat.st_mtime_ns)))
    except OSError:
        return "UNREADABLE"
    return rows


def _formal_root_fingerprint() -> tuple:
    return (_fingerprint(IDENTITY.root), _fingerprint(IDENTITY.authority.parent))


def offline_check() -> bool:
    import g1_real_adoption as g1
    import g2_explore_c as shared

    checks = base._Checks()
    formal_before = _formal_root_fingerprint()
    scratch = Path(tempfile.mkdtemp(prefix=OFFLINE_PREFIX))
    guard = shared._install_child_guard(scratch / "guard")
    try:
        with g1._offline_network_guard(scratch):
            selfcheck = shared._guard_selfcheck()
            checks.check("network is hard-refused while AF_UNIX stays available",
                         all(v is True for v in selfcheck.values()))
            tests = _run_new_tests()
            checks.check("new v07 live-c tests collect and pass",
                         tests.get("ok") is True and int(tests.get("run") or 0) > 0
                         and int(tests.get("skipped") or 0) == 0)
            _formal_scratch_chain(scratch, checks)
            child = shared._child_guard_status(guard["status_dir"])
            checks.check("owner children inherit the offline network guard",
                         child.get("all_refuse") is True)
            checks.check("offline check never touches the formal C root",
                         _formal_root_fingerprint() == formal_before)
    finally:
        shared._restore_child_guard(guard)
    print()
    print("scratch kept:", scratch)
    print("RESULT: %s" % ("PASS" if checks.ok else "FAIL"))
    print("checks: %d" % len(checks.rows))
    return checks.ok


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------


def _emit(payload: Mapping[str, Any], code: int) -> int:
    sys.stdout.write(json.dumps(dict(payload), ensure_ascii=False, indent=1,
                                sort_keys=True, default=str) + "\n")
    sys.stdout.flush()
    return int(code)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="v07_live_c.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("offline-check")
    sub.add_parser("prepare")
    sub.add_parser("audit")
    sub.add_parser("status")
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--execute-approved", action="store_true",
                            dest="execute_approved")
    try:
        args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    except SystemExit as exc:
        return int(exc.code or 0)
    try:
        if args.command == "offline-check":
            return EXIT_OK if offline_check() else EXIT_REFUSED
        if args.command == "prepare":
            result = prepare()
        elif args.command in {"audit", "status"}:
            result = audit()
        else:
            result = run(execute_approved=bool(getattr(args, "execute_approved", False)))
    except Exception as exc:  # 只报类型名，不抄异常正文
        result = {"ok": False, "reason_code": "ENTRY_FAILED",
                  "exception_type": type(exc).__name__, "model_called": False}
    return _emit(result, EXIT_OK if result.get("ok") else EXIT_REFUSED)


if __name__ == "__main__":
    raise SystemExit(main())

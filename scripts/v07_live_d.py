#!/usr/bin/env python3
"""0.7 唯一真实短日 D：在 C 的薄入口上再跑一次，并观测真实的阅读输出上限。

    python -B scripts/v07_live_d.py offline-check
    python -B scripts/v07_live_d.py prepare
    python -B scripts/v07_live_d.py audit
    python -B scripts/v07_live_d.py status
    python -B scripts/v07_live_d.py run --execute-approved

这是什么
--------
D **不复制** A/C 的机制：prepare / audit / run / 额度闸门 / 一次性标记 / 首次故障停全部复用
``scripts/v07_live_a.py``，check interval 的实例覆写复用 ``scripts/v07_live_c.py``。D 只固定
一份自己的身份，并在 audit 里增加一条**只读配置观测**：正式装配出来的阅读响应实际会带多大的
``max_output_tokens``（读 client 的构造配置，不发送任何请求）。

固定身份（没有 --root，不能换旧根）
----------------------------------
* root ``/tmp/nyairo-v07-live-20261109-d``
* authority ``/tmp/nyairo-v07-live-20261109-d-authority/authority.db``
* run ``run:v07-live:20261109-d``，域 ``v07:live:20261109-d``
* history 568、本 run 新增上限 20、UTC 日 300、共同总限 1000（最坏 588，余 412）
* 逻辑起点 ``2026-11-09T11:00:00+09:00``，1:1（rate=1.0，逻辑秒=真实秒=1800）
* check interval 5 逻辑分钟；canonical / room_living / economy / rhythm 开，ambient 关
* 最晚启动 ``2026-11-09T23:15:00+08:00``，绝对停 ``2026-11-09T23:50:00+08:00``

输出安全
--------
stdout/audit/status 只输出固定枚举、布尔、计数、安全 id、时间与地点/Activity 状态；
不输出 prompt/completion/reasoning/short_rationale/作品正文/env/token/header/endpoint，
也不抄异常原文。读不出来的东西保留 ``UNKNOWN``，不猜 0；``reading_cap`` 明确标注是构造期
观测而不是实发测量。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import unittest
from dataclasses import dataclass, replace
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
import v07_live_c as c07  # noqa: E402

EXIT_OK = 0
EXIT_REFUSED = 1
OFFLINE_PREFIX = "v07-live-d-offline-"
TEST_PATTERNS = ("test_v07_live_d.py", "test_reading_response_token_cap.py")
CONSUME_FILENAME = "v07-live-d.consume.json"
SCHEMA_AUDIT = "v07.live.d.audit.v1"
INTERVAL_ENV = c07.INTERVAL_ENV

#: A sentinel is all ``_model_reading_response`` needs to choose the production wrapper; no
#: budget method is ever called while the cap is merely observed.
_AUDIT_REQUESTS = object()


@dataclass(frozen=True)
class Identity(c07.Identity):
    """D 身份 = C 的字段（含 check interval）+ 本批次的 run 身份与额度。"""


IDENTITY = Identity(
    root=Path("/tmp/nyairo-v07-live-20261109-d"),
    authority=Path("/tmp/nyairo-v07-live-20261109-d-authority/authority.db"),
    run_name="v07-live:20261109-d",
    run_id="run:v07-live:20261109-d",
    domain="v07:live:20261109-d",
    anchor=datetime.fromisoformat("2026-11-09T11:00:00+09:00"),
    latest_start=datetime.fromisoformat("2026-11-09T23:15:00+08:00"),
    deadline=datetime.fromisoformat("2026-11-09T23:50:00+08:00"),
    historical_requests=568,
)
PROFILE = IDENTITY.profile()


# ---------------------------------------------------------------------------------------------
# 阅读输出上限：只读正式装配的配置，不发送
# ---------------------------------------------------------------------------------------------


def _reading_cap_view(root: Path | str) -> dict[str, Any]:
    """正式装配出来的阅读响应会带的 ``max_output_tokens``（构造期观测，不是实发测量）。

    走的是 resident 同一套 ``map_life_environment`` + ``autonomous_life_assembly``；只读取
    ``ModelReadingResponse.max_output_tokens``，不调用 ``complete``。provider 未配置时保留
    UNKNOWN，不猜成 0，也不拿入口里的目标值冒充实测。
    """

    try:
        from components.phone import life as phone_life

        view = base._instance_view(root)
        env = phone_life.map_life_environment(view["phone"], view["nested"],
                                             base_env=dict(os.environ))
        import autonomous_life_assembly as assembly

        response = assembly._model_reading_response(env, requests=_AUDIT_REQUESTS)
    except Exception as exc:  # 只报类型名，不抄异常正文
        return {"max_output_tokens": "UNKNOWN", "source": "UNREADABLE",
                "exception_type": type(exc).__name__, "measured_by_send": False}
    if response is None:
        return {"max_output_tokens": "UNKNOWN", "source": "MODEL_NOT_CONFIGURED",
                "measured_by_send": False}
    cap = getattr(response, "max_output_tokens", None)
    if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
        cap = "UNKNOWN"
    return {"max_output_tokens": cap,
            "source": "ASSEMBLED_CLIENT_CONFIG_OBSERVATION",
            "measured_by_send": False,
            "note": "构造期观测；不发送任何请求，缺失保留 UNKNOWN"}


# ---------------------------------------------------------------------------------------------
# prepare / audit / run：薄包 A/C 的正式路径，只加 D 身份与 reading cap 观测
# ---------------------------------------------------------------------------------------------


def configure(root: Path | str, authority: Path | str, *, identity: Identity = IDENTITY,
              provision: bool = True, deadline: Optional[datetime] = None,
              allow_night_start: bool = True) -> dict[str, Any]:
    return c07.configure(root, authority, identity=identity, provision=provision,
                         deadline=deadline, allow_night_start=allow_night_start)


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
        report["check_interval"] = c07._interval_view(root, identity)
        report["reading_cap"] = _reading_cap_view(root)
    else:
        report["check_interval"] = {
            "target_minutes": int(identity.check_interval_minutes),
            "persisted_minutes": "UNKNOWN", "runner_minutes": "UNKNOWN",
            "source": "NOT_PREPARED"}
        report["reading_cap"] = {
            "max_output_tokens": "UNKNOWN", "source": "NOT_PREPARED",
            "measured_by_send": False}
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
    if c07._persisted_interval(identity.root) != int(identity.check_interval_minutes):
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
# 离线验收：本单聚合用例 + 唯一最短正式装配（零模型）+ 网络硬拒
# ---------------------------------------------------------------------------------------------


class _RecordingTransport:
    """只替最后一段 transport；正式 client / 预算包装器 / 阅读 writer 都真实执行。"""

    send_split_capable = True
    provider = "offline"
    model = "offline"
    instances: list["_RecordingTransport"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = dict(kwargs)
        self.caps: list[Any] = []
        type(self).instances.append(self)

    def complete(self, *, system_prompt: str, user_prompt: str,
                 timeout_s: Optional[float] = None,
                 max_output_tokens: Optional[int] = None) -> Any:
        from types import SimpleNamespace

        self.caps.append(max_output_tokens)
        return SimpleNamespace(text='{"kind":"no_change"}', latency_ms=1)


def _run_new_tests() -> dict[str, Any]:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    try:
        for pattern in TEST_PATTERNS:
            suite.addTests(loader.discover(str(REPO / "tests"), pattern=pattern))
    except Exception as exc:
        return {"ok": False, "run": 0, "skipped": 0,
                "exception_type": type(exc).__name__}
    with open(os.devnull, "w") as sink:
        result = unittest.TextTestRunner(verbosity=0, stream=sink).run(suite)
    return {"ok": bool(result.wasSuccessful()), "run": int(result.testsRun),
            "skipped": len(result.skipped), "failures": len(result.failures),
            "errors": len(result.errors)}


READING_CONTEXT = {
    "source_id": "src:d-offline", "source_revision": "rev:d-offline", "page": 1,
    "text": "离线的一页正文，不来自任何真实材料。", "previous_page": None, "total_pages": 3,
}


def _reading_cap_send_check(scratch: Path, checks: "base._Checks") -> None:
    """零模型的正式阅读链：证明真正发到 transport 的是装配出来的 2048。"""

    import autonomous_life_assembly as assembly
    from life_request_budget import LifeRequestBudget
    from unittest import mock

    state = scratch / "reading-cap-state"
    state.mkdir(parents=True, exist_ok=True)
    _RecordingTransport.instances = []
    env = dict(base._ENV)
    with mock.patch("profile_model_transport.OpenAICompatibleTransport", _RecordingTransport):
        response = assembly._model_reading_response(
            env, requests=LifeRequestBudget(state, utc_day_max=20, run_window_max=20,
                                            run_id="run:reading-cap", failstop=False))
    sent: list[Any] = []
    kind = None
    if response is not None:
        out = response.respond(READING_CONTEXT, observed_at="2026-11-09T00:00:00+00:00")
        kind = out.get("kind")
        if _RecordingTransport.instances:
            sent = _RecordingTransport.instances[0].caps
    checks.check("formal reading chain really sends the assembled 2048 cap (zero model)",
                 response is not None
                 and response.max_output_tokens == 2048
                 and len(_RecordingTransport.instances) == 1
                 and sent == [2048]
                 and kind == "no_change")


def _formal_scratch_chain(scratch: Path, checks: "base._Checks") -> None:
    """唯一一条最短正式装配：真实 prepare + 真实 supervisor + 注入一次脚本失败。"""

    import autonomous_life_assembly as assembly
    import autonomous_life_runner as alr
    import life_pause
    from unittest import mock

    from components.phone import demo, life as phone_life

    future = base._now() + timedelta(days=1)
    identity = replace(IDENTITY, root=scratch / "instance",
                       authority=scratch / "authority" / "authority.db",
                       deadline=future, latest_start=future)
    prepared = configure(identity.root, identity.authority, identity=identity,
                         deadline=future)
    checks.check("prepare builds one real isolated D instance with zero model calls",
                 prepared.get("ok") is True
                 and prepared.get("model_called") is False
                 and prepared.get("request_sent") is False)
    if not prepared.get("ok"):
        return
    state = Path(str(prepared["state_root"]))
    checks.check("prepare persists D check interval 5 logical minutes in the instance",
                 c07._persisted_interval(identity.root) == 5)
    try:
        view = base._instance_view(identity.root)
        overrides = {"WORLD_BODY_INTENT_MUTATION_GATE": identity.world_gate}
        overrides.update(demo._serve_feature_overrides(room_living=identity.room_living))
        life_env = phone_life.map_life_environment(
            view["phone"], view["nested"], base_env={}, feature_overrides=overrides)
        runner = alr.AutonomousLifeRunner(
            type("DScratch", (), {"state_root": scratch / "runner"})(),
            state_root=scratch / "runner", env=life_env)
        child_ok = life_env.get(INTERVAL_ENV) == "5" and int(runner.interval_minutes) == 5
    except Exception:
        child_ok = False
    checks.check("real Life binding + real scheduler read 5 minutes from the child env",
                 child_ok)

    # audit 的 reading cap 只读观测：临时喂入合成 provider 配置，证明它读的是装配值。
    previous = {name: os.environ.get(name) for name in base._ENV}
    os.environ.update(base._ENV)
    try:
        cap_view = _reading_cap_view(identity.root)
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    checks.check("D audit observes the assembled reading cap from trusted config, no send",
                 cap_view.get("max_output_tokens") == 2048
                 and cap_view.get("measured_by_send") is False)

    pre = audit(identity.root, identity=identity)
    checks.check("D audit keeps cap20/history568/UTC300/total1000 and clock 1800s rate1",
                 pre.get("ok") is True
                 and pre["caps"]["new_cap"] == 20
                 and pre["caps"]["historical_requests"] == 568
                 and pre["caps"]["utc_day_cap"] == 300
                 and pre["caps"]["authorized_total_limit"] == 1000
                 and pre["caps"]["worst_case_cumulative"] == 588
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
    checks.check("D audit twice creates no file, no high-water and no ledger entry",
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
    checks.check("first provider failure stops the bounded D run and charges one",
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
    checks.check("stopped D run cannot revive and the failure stays recorded",
                 again.get("ok") is False
                 and post["failure"]["locked"] is True
                 and post["failure"]["category"] == "PROVIDER_OR_TRANSPORT_FAILURE"
                 and isinstance(charged, int) and 1 <= charged <= 20)
    consume_identity = replace(identity, root=scratch / "one-shot")
    (consume_identity.root / "run").mkdir(parents=True, exist_ok=True)
    checks.check("one-shot marker D: first consumes, second refuses, marker kept",
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
            checks.check("new D + reading-cap tests collect and pass",
                         tests.get("ok") is True and int(tests.get("run") or 0) > 0
                         and int(tests.get("skipped") or 0) == 0)
            _reading_cap_send_check(scratch, checks)
            _formal_scratch_chain(scratch, checks)
            child = shared._child_guard_status(guard["status_dir"])
            checks.check("owner children inherit the offline network guard",
                         child.get("all_refuse") is True)
            checks.check("offline check never touches the formal D root",
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
    parser = argparse.ArgumentParser(prog="v07_live_d.py", description=__doc__)
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

#!/usr/bin/env python3
"""v0.4 日常能力有限验收探针：``prepare`` / ``status`` / ``run``。

这不是产品第二入口，也不是新的 runner/store/预算账本：

* ``prepare`` 只在**不存在的新 root** 调用既有 ``phone.demo.prepare_demo(with_world=True)``，
  再用既有 ``autonomous_life_control.set_enabled`` 显式打开这个新实例的自主开关；
* ``run`` 复用既有 ``phone.live_day.run_segment``，同 root / 同 run / 同账本，默认 180 秒、
  每段最多新增 6 次请求，硬拒绝超过 600 秒或无限跑；
* ``status`` 只调用既有的纯读 ``phone.live_day.status``，不建目录、不发模型。

模型配置只从进程环境里的 ``NYA02_MODEL_*`` 解析；密钥不写进实例、``model_env``、配置或报告。
``prepare`` / ``run`` 可以写这份探针自己的报告（放在新 root 的 ``probe/`` 下），它只是验收
证据，不是产品真相，也不含 prompt/正文全文。24h 结论固定为 ``NOT_RUN_BY_USER_SCOPE``。

退出码直接沿用既有入口的真实 ``exit_code``：成功段的 ``0`` 原样保留；缺失/``None`` 才失败关闭为
``1``，非零值原样透传。``ok: true`` 本身从不被当作成功码的依据。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
LIFE_SCRIPTS = REPO_ROOT / "components" / "life" / "scripts"
for _entry in (str(REPO_ROOT), str(LIFE_SCRIPTS)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

SCHEMA_PREPARE = "day_capability_probe.prepare.v1"
SCHEMA_STATUS = "day_capability_probe.status.v1"
SCHEMA_RUN = "day_capability_probe.run.v1"

DEFAULT_DURATION_SECONDS = 180
MAX_DURATION_SECONDS = 600
DEFAULT_POLL_SECONDS = 2
MIN_POLL_SECONDS = 2
DEFAULT_MAX_NEW_REQUESTS = 6
MAX_NEW_REQUESTS = 6

PROBE_DIRNAME = "probe"
PREPARE_REPORT_FILENAME = "prepare-report.json"
RUN_REPORT_FILENAME = "run-report.json"

MODEL_ENV_NAMES = ("NYA02_MODEL_BASE_URL", "NYA02_MODEL_NAME", "NYA02_MODEL_API_KEY",
                   "NYA02_MODEL_PROVIDER")

#: 阅读材料明确标注为验收合成材料：它不冒充她的私人阅读，也不含任何模型决定/理由/经历。
PROBE_READING_TITLE = "验收用测试阅读材料（合成）"
PROBE_READING_CONTENT = (
    "验收夹具第一页：这段文字由验收脚本写入，不是她的私人阅读。\n%%PAGE%%\n"
    "验收夹具第二页：只用于验证阅读入口与翻页。\n"
)

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _int_or_none(value: Any) -> Optional[int]:
    try:
        if value is None or isinstance(value, bool):
            return None
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _exit_code(value: Any, default: int = EXIT_REFUSED) -> int:
    """Preserve a real ``0``; only a missing/unusable value fails closed.

    ``int(value or EXIT_REFUSED)`` is the bug this replaces: a successful owner answer with
    ``exit_code = 0`` was treated as "no value" and rewritten to ``1``.  A missing or ``None``
    exit code still refuses -- ``ok: true`` is never used to invent a success code.
    """

    if value is None or isinstance(value, bool):
        return int(default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def model_environment(environment: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    """The provider variables this process was given.  Never imported from a file or written."""

    source = os.environ if environment is None else environment
    return {name: str(source[name]) for name in MODEL_ENV_NAMES
            if str(source.get(name) or "").strip()}


def model_meta(environment: Optional[Mapping[str, str]] = None) -> dict[str, Any]:
    """A reportable provider summary: model/provider/endpoint host only, never the key."""

    env = model_environment(environment)
    base = env.get("NYA02_MODEL_BASE_URL") or ""
    parts = urlsplit(base)
    endpoint = ("%s://%s" % (parts.scheme, parts.netloc)
                if parts.scheme and parts.netloc else None)
    return {
        "configured": bool(env.get("NYA02_MODEL_BASE_URL") and env.get("NYA02_MODEL_NAME")
                           and env.get("NYA02_MODEL_API_KEY")),
        "provider": env.get("NYA02_MODEL_PROVIDER") or ("custom" if base else None),
        "model": env.get("NYA02_MODEL_NAME") or None,
        "endpoint": endpoint,
        "api_key_present": bool(env.get("NYA02_MODEL_API_KEY")),
        "api_key_recorded": False,
        "source": "process_environment",
    }


def validate_run_bounds(*, duration_seconds: Any = None, poll_seconds: Any = None,
                        max_new_requests: Any = None
                        ) -> tuple[Optional[dict[str, int]], Optional[dict[str, Any]]]:
    """Bound one segment.  The probe refuses to run longer or spend more than the order says."""

    duration = DEFAULT_DURATION_SECONDS if duration_seconds is None else _int_or_none(
        duration_seconds)
    if duration is None or duration <= 0 or duration > MAX_DURATION_SECONDS:
        return None, {"reason_code": "DURATION_OUT_OF_RANGE",
                      "detail": "duration_seconds must be 1..%d" % MAX_DURATION_SECONDS}
    poll = DEFAULT_POLL_SECONDS if poll_seconds is None else _int_or_none(poll_seconds)
    if poll is None or poll < MIN_POLL_SECONDS:
        return None, {"reason_code": "POLL_OUT_OF_RANGE",
                      "detail": "poll_seconds must be >= %d" % MIN_POLL_SECONDS}
    cap = (DEFAULT_MAX_NEW_REQUESTS if max_new_requests is None
           else _int_or_none(max_new_requests))
    if cap is None or cap <= 0 or cap > MAX_NEW_REQUESTS:
        return None, {"reason_code": "REQUEST_CAP_OUT_OF_RANGE",
                      "detail": "max_new_requests must be 1..%d" % MAX_NEW_REQUESTS}
    return {"duration_seconds": duration, "poll_seconds": poll,
            "max_new_requests": cap}, None


def _refused(reason: str, detail: str = "", *, schema: str) -> dict[str, Any]:
    return {"schema": schema, "ok": False, "status": "BLOCKED", "exit_code": EXIT_REFUSED,
            "reason": reason, "detail": detail, "model_called": False}


def _write_report(root: Path, filename: str, payload: Mapping[str, Any]) -> Path:
    directory = root / PROBE_DIRNAME
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=1, sort_keys=True),
                   encoding="utf-8")
    os.replace(tmp, path)
    return path


def _load_modules():
    from components.phone import demo, life as phone_life, live_day

    import autonomous_life_control as control

    return demo, phone_life, live_day, control


def prepare_instance(root: Path | str, *, environment: Optional[Mapping[str, str]] = None,
                     prepare_fn: Optional[Callable[..., Mapping[str, Any]]] = None,
                     enable_fn: Optional[Callable[..., Mapping[str, Any]]] = None
                     ) -> dict[str, Any]:
    """Create one new instance through the existing demo entry and open its own switch."""

    root_path = Path(root).expanduser()
    if root_path.exists():
        report = _refused("ROOT_ALREADY_EXISTS",
                          "prepare only ever creates a brand-new root; it never resets or "
                          "overwrites an existing instance", schema=SCHEMA_PREPARE)
        report["root"] = str(root_path)
        return report
    demo, phone_life, _live_day, control = _load_modules()
    prepare = prepare_fn or demo.prepare_demo
    enable = enable_fn or control.set_enabled
    meta = model_meta(environment)

    try:
        prepared = prepare(root_path, with_world=True, reading_title=PROBE_READING_TITLE,
                           reading_content=PROBE_READING_CONTENT)
    except Exception as exc:  # report the class, never a path/key-bearing message
        report = _refused("PREPARE_FAILED", type(exc).__name__, schema=SCHEMA_PREPARE)
        report["root"] = str(root_path)
        return report
    if not isinstance(prepared, Mapping) or prepared.get("ok") is not True:
        report = _refused("PREPARE_REFUSED", "demo.prepare_demo did not report ok",
                          schema=SCHEMA_PREPARE)
        report["root"] = str(root_path)
        return report

    try:
        phone = phone_life.load_phone_instance(root_path)
        nested = phone_life.read_nested_life(root_path)
        install_root = phone_life.life_install_root(root_path, nested)
        state_root = phone_life.life_state_root(root_path, nested)
        switch = enable(install_root, True, actor="day-capability-probe")
        binding = phone_life.build_binding(phone, nested)
        bound_env = binding["environment"]
    except Exception as exc:  # the instance exists, but this probe will not claim it is ready
        report = _refused("SWITCH_OR_BINDING_FAILED", type(exc).__name__,
                          schema=SCHEMA_PREPARE)
        report.update({"root": str(root_path), "instance_created": True,
                       "autonomous_switch": None})
        report["probe_report"] = str(
            _write_report(root_path, PREPARE_REPORT_FILENAME, report))
        return report
    run_id = bound_env.get("CHIYO_AUTONOMOUS_RUN_ID")
    day_max = _int_or_none(bound_env.get(
        "CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_UTC_DAY"))
    window_max = _int_or_none(bound_env.get(
        "CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_RUN_WINDOW"))
    try:
        from life_request_budget import budget_path

        ledger_present = budget_path(state_root).exists()
    except Exception:
        ledger_present = None
    report = {
        "schema": SCHEMA_PREPARE,
        "ok": True,
        "status": "READY",
        "exit_code": EXIT_OK,
        "prepared_at": _now_iso(),
        "root": str(root_path),
        "instance_file": str(prepared.get("instance_file") or ""),
        "subject": prepared.get("subject"),
        "world_home": prepared.get("world_home"),
        "world_id": prepared.get("world_id"),
        "workspace_id": prepared.get("workspace_id"),
        "supply_socket": prepared.get("supply_socket"),
        "supply_generation": prepared.get("supply_generation"),
        "state_root": str(state_root),
        "run": {"run_id": run_id, "inherited_from_previous_instance": False,
                "new_run_window_requested": False,
                "request_ceilings": {"utc_day": day_max, "run_window": window_max},
                "ceilings_are_96_96": day_max == 96 and window_max == 96,
                "request_ledger_present_before_first_run": ledger_present},
        "autonomous_switch": switch,
        "work": {"seed_content": None, "basis": "demo.prepare_demo passes seed_content=None",
                 "model_decisions_written": False},
        "reading": {"title": PROBE_READING_TITLE, "content_is_synthetic_probe_material": True},
        "model": meta,
        "memory": {"state": "UNBOUND",
                   "basis": "the probe never initialises or binds Memory; a segment without "
                            "memory is reported as degraded, never as a complete loop"},
        "profile_binding": {"NYA02_PROFILE_HOME_passed": False, "HERMES_HOME_passed": False,
                            "hermes_home_equals_world_home": False},
        "prepared_summary": dict((prepared.get("prepared") or {})),
        "world_prepare": dict((prepared.get("world") or {})),
        "writes": {"product_truth": False,
                   "probe_report_allowed": True},
        "model_called": False,
        "twenty_four_hours": _twenty_four_hours(),
        "note": ("prepare 不启动 resident、不发模型请求；自主开关由既有控制入口显式打开，"
                 "不是 probe 自己发明的新开关"),
    }
    report["probe_report"] = str(_write_report(root_path, PREPARE_REPORT_FILENAME, report))
    return report


def _binding(phone_life, root_path: Path, *, environment: Mapping[str, str]):
    phone = phone_life.load_phone_instance(root_path)
    nested = phone_life.read_nested_life(root_path)
    if nested is None:
        return None, None, None, None
    bound = phone_life.map_life_environment(phone, nested, base_env=dict(environment))
    state_root = phone_life.life_state_root(root_path, nested)
    return phone, nested, bound, state_root


def _ledger_view(bound_env: Mapping[str, str], state_root: Path) -> dict[str, Any]:
    """Read the persisted request ledger.  Never opens a window or writes a reservation."""

    try:
        from life_request_budget import LifeRequestBudget

        budget = LifeRequestBudget(
            state_root,
            utc_day_max=_int_or_none(bound_env.get(
                "CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_UTC_DAY")),
            run_window_max=_int_or_none(bound_env.get(
                "CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_RUN_WINDOW")),
            run_id=bound_env.get("CHIYO_AUTONOMOUS_RUN_ID"))
        view = budget.view()
    except Exception as exc:
        return {"state": "UNREADABLE", "error_type": type(exc).__name__}
    return {key: view.get(key) for key in
            ("state", "utc_day", "utc_day_calls", "utc_day_max", "run_id",
             "run_window_calls", "run_window_max")}


def _delta(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Optional[int]]:
    out: dict[str, Optional[int]] = {}
    for key in ("utc_day_calls", "run_window_calls"):
        old, new = before.get(key), after.get(key)
        out[key] = (new - old if isinstance(old, int) and isinstance(new, int) else None)
    return out


def _twenty_four_hours() -> dict[str, Any]:
    return {"status": "NOT_RUN_BY_USER_SCOPE",
            "simulated": False,
            "elapsed_seconds_claimed": 0,
            "basis": "the user explicitly asked not to spend 24h; this probe never turns a "
                     "short segment into a day claim",
            "product_judgement_changed": False}


def _run_report(segment: Mapping[str, Any], meta: Mapping[str, Any],
                before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    summary = dict(segment.get("summary") or {})
    changed = dict(summary.get("changed") or {})
    budget = dict(changed.get("budget") or {})
    doing = dict(summary.get("doing") or {})
    categories = dict(summary.get("categories") or {})
    life = dict(segment.get("life") or {})
    return {
        "schema": SCHEMA_RUN,
        "ok": bool(segment.get("ok")),
        "status": segment.get("status"),
        "exit_code": _exit_code(segment.get("exit_code")),
        "reason": segment.get("reason"),
        "detail": segment.get("detail"),
        "probe_at": _now_iso(),
        "model": {**dict(meta),
                  "calls_this_segment": segment.get("model_calls"),
                  "calls_cumulative": segment.get("model_calls_cumulative"),
                  "ledger_total_requests": changed.get("model_requests"),
                  "ledger_source": changed.get("model_requests_source"),
                  "cognition_calls": changed.get("cognition_calls"),
                  "content_generation_calls": changed.get("content_generation_calls")},
        "requests": {"before": dict(before), "after": dict(after),
                     "delta": _delta(before, after),
                     "same_run_window": bool(
                         before.get("run_id") and before.get("run_id") == after.get("run_id")),
                     "new_run_window_requested": False,
                     "ledger_reset": False,
                     "note": "同一个 run 窗口续用；segment cap 只压低本段上限"},
        "segment": {
            "bounds": segment.get("bounds"),
            "started_at": segment.get("segment_started_at"),
            "ended_at": segment.get("segment_ended_at"),
            "ticks_recorded": (summary.get("segment") or {}).get("ticks_recorded"),
            "processes_started": segment.get("processes_started"),
        },
        "where": summary.get("where"),
        "journey": summary.get("journey"),
        "hands": summary.get("hands"),
        "doing": summary.get("doing"),
        "stop_point": summary.get("stop_point"),
        "next": summary.get("next"),
        "changed": {key: changed.get(key) for key in
                    ("wakes", "ticks", "owner_versions", "owner_version_refs",
                     "owner_version_unknown", "actions", "segment_request_cap")},
        "pause_quiet_fault": {
            "pause": doing.get("pause"),
            "quiet": (((changed.get("wakes") or {}).get("buckets") or {})
                      .get("quiet")),
            "idle_no_candidate": (((changed.get("wakes") or {}).get("buckets") or {})
                                  .get("idle")),
            "blocked": (((changed.get("wakes") or {}).get("buckets") or {})
                        .get("blocked")),
            "unknown": (((changed.get("wakes") or {}).get("buckets") or {})
                        .get("unknown")),
            "fault": categories.get("fault"),
            "note": "安静不是故障；预算耗尽或被挡住也不是休息",
        },
        "budget": budget,
        "services": segment.get("services"),
        "services_stopped": segment.get("services_stopped"),
        "lease": segment.get("lease"),
        "segment_ownership": segment.get("segment_ownership"),
        "memory": {"state": "UNBOUND",
                   "basis": "no Memory database is configured for this probe; the report declares "
                            "the degradation instead of treating a short segment as a complete "
                            "memory loop"},
        "life_reason": life.get("reason"),
        "twenty_four_hours": _twenty_four_hours(),
        "note": ("outer READY / exit 0 只表示这一段正常收尾，不等于模型成功；"
                 "真实决策、动作与 owner 版本引用见 doing / changed / owner_version_refs"),
    }


def run_probe(root: Path | str, *, environment: Optional[Mapping[str, str]] = None,
              duration_seconds: Any = None, poll_seconds: Any = None,
              max_new_requests: Any = None,
              run_fn: Optional[Callable[..., Mapping[str, Any]]] = None) -> dict[str, Any]:
    """Run one bounded segment through the existing live_day entry."""

    bounds, error = validate_run_bounds(duration_seconds=duration_seconds,
                                        poll_seconds=poll_seconds,
                                        max_new_requests=max_new_requests)
    if error is not None:
        report = _refused(str(error["reason_code"]), str(error["detail"]), schema=SCHEMA_RUN)
        report["bounds"] = {"requested": {"duration_seconds": duration_seconds,
                                          "poll_seconds": poll_seconds,
                                          "max_new_requests": max_new_requests}}
        return report
    root_path = Path(root).expanduser()
    if not root_path.exists():
        return _refused("INSTANCE_MISSING", "the phone root does not exist", schema=SCHEMA_RUN)

    env = dict(os.environ if environment is None else environment)
    meta = model_meta(env)
    if not meta["configured"]:
        return _refused("NO_MODEL_PROVIDER",
                        "NYA02_MODEL_BASE_URL / NAME / API_KEY are not all present in the "
                        "process environment; nothing was started", schema=SCHEMA_RUN)

    _demo, phone_life, live_day, _control = _load_modules()
    phone, nested, bound, state_root = _binding(phone_life, root_path, environment=env)
    if nested is None:
        return _refused("NESTED_LIFE_INSTANCE_MISSING",
                        "this phone root has no one-day Life instance", schema=SCHEMA_RUN)
    before = _ledger_view(bound, state_root)
    run = run_fn or live_day.run_segment
    segment = run(root_path, duration_seconds=bounds["duration_seconds"],
                  poll_seconds=bounds["poll_seconds"],
                  max_new_requests=bounds["max_new_requests"], act=True,
                  start_services="auto", environment=env)
    after = _ledger_view(bound, state_root)
    report = _run_report(segment, meta, before, after)
    report["probe_report"] = str(_write_report(root_path, RUN_REPORT_FILENAME, report))
    return report


def status_probe(root: Path | str, *, environment: Optional[Mapping[str, str]] = None,
                 status_fn: Optional[Callable[..., Mapping[str, Any]]] = None) -> dict[str, Any]:
    """Pure read: the existing live_day.status, plus this process's provider meta.  No writes."""

    root_path = Path(root).expanduser()
    if not root_path.exists():
        return _refused("INSTANCE_MISSING", "the phone root does not exist", schema=SCHEMA_STATUS)
    _demo, _phone_life, live_day, _control = _load_modules()
    status = status_fn or live_day.status
    report = dict(status(root_path, live=True))
    summary = dict(report.get("summary") or {})
    return {
        "schema": SCHEMA_STATUS,
        "ok": bool(report.get("ok")),
        "status": report.get("status"),
        "exit_code": _exit_code(report.get("exit_code")),
        "reason": report.get("reason"),
        "detail": report.get("detail"),
        "read_only": True,
        "writes": False,
        "services_started": 0,
        "model_called": False,
        "probe_at": _now_iso(),
        "model": model_meta(environment),
        "state_root": report.get("state_root"),
        "run_id": (((summary.get("changed") or {}).get("budget") or {}).get("run_id")),
        "running_recorded": report.get("running_recorded"),
        "lease": report.get("lease"),
        "where": summary.get("where"),
        "hands": summary.get("hands"),
        "doing": summary.get("doing"),
        "stop_point": summary.get("stop_point"),
        "next": summary.get("next"),
        "model_calls_detail": report.get("model_calls_detail"),
        "memory": {"state": "UNBOUND",
                   "basis": "status never initialises or binds Memory"},
        "twenty_four_hours": _twenty_four_hours(),
    }


def _emit(payload: Mapping[str, Any]) -> None:
    sys.stdout.write(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True) + "\n")
    sys.stdout.flush()


def cmd_prepare(args: argparse.Namespace) -> int:
    report = prepare_instance(Path(args.root))
    _emit(report)
    return _exit_code(report.get("exit_code"))


def cmd_status(args: argparse.Namespace) -> int:
    report = status_probe(Path(args.root))
    _emit(report)
    return _exit_code(report.get("exit_code"))


def cmd_run(args: argparse.Namespace) -> int:
    report = run_probe(Path(args.root), duration_seconds=args.duration_seconds,
                       poll_seconds=args.poll_seconds,
                       max_new_requests=args.max_new_requests)
    _emit(report)
    return _exit_code(report.get("exit_code"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="day_capability_probe.py")
    sub = parser.add_subparsers(dest="command", required=True)

    prepare = sub.add_parser("prepare", help="create one new instance and open its own switch")
    prepare.add_argument("--root", required=True,
                         help="a brand-new phone root; an existing root is refused")
    prepare.set_defaults(handler=cmd_prepare)

    status = sub.add_parser("status", help="read the instance without writing or calling a model")
    status.add_argument("--root", required=True)
    status.set_defaults(handler=cmd_status)

    run = sub.add_parser("run", help="run one bounded segment (<= 600s, <= 6 new requests)")
    run.add_argument("--root", required=True)
    run.add_argument("--duration-seconds", type=int, default=DEFAULT_DURATION_SECONDS)
    run.add_argument("--poll-seconds", type=int, default=DEFAULT_POLL_SECONDS)
    run.add_argument("--max-new-requests", type=int, default=DEFAULT_MAX_NEW_REQUESTS)
    run.set_defaults(handler=cmd_run)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse already printed its own usage
        return int(exc.code or 0)
    return int(args.handler(args))


if __name__ == "__main__":
    sys.exit(main())

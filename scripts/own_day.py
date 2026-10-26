#!/usr/bin/env python3
"""v0.5 操作者入口：为「她自己排一天」写外部义务 / 只读检查今天。

这是义务文档**唯一**的正规写入入口。它不在 Life 的决策/循环里：模型不能调用它，Life 进程
拿到的是 ``life_day_plan.DayObligations``（只读）。首版只接受一个已存在、可路由、可核结果的
``artifact:`` 执行目标；目标不存在、不可读或不可续时具名拒绝，不把"标题"当成"动作"。

用法（在实例自己的环境里）::

    python scripts/own_day.py obligation-import --instance /path/to/instance \
        --date 2026-10-26 --title "把上午那页读完并写一句" \
        --target artifact:<id> --due-at 2026-10-26T10:30:00+08:00 \
        --source-ref operator:2026-10-26 --operation-key oblig:2026-10-26:<id>

    python scripts/own_day.py today --instance /path/to/instance

没有 ``--instance`` 时可用 ``--state-root`` 指向 Life 状态根，但那样必须显式给
``--supply-socket`` / ``--subject``，否则目标无法现场校验，入口会拒绝写入。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
LIFE_SCRIPTS = REPO_ROOT / "components" / "life" / "scripts"
for _entry in (str(REPO_ROOT), str(LIFE_SCRIPTS)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import life_day_plan as ldp  # noqa: E402
import one_day_life as odl  # noqa: E402


def _resolve_roots(*, instance: Optional[str], state_root: Optional[str]) -> tuple[Path, dict]:
    if instance:
        data = odl.read_instance(instance)
        return odl.life_state_root(instance), dict(data)
    if not state_root:
        raise ldp.DayPlanError("OWN_DAY_STATE_ROOT_MISSING",
                               "pass --instance or --state-root")
    return Path(state_root), {}


def _target_verifier(*, instance: Optional[str], supply_socket: Optional[str],
                     subject: Optional[str]):
    """Build the Supply read used to prove an obligation target exists.  Returns (fn, closer)."""

    if not instance and not (supply_socket and subject):
        return None, None
    from life_artifact_port import LifeArtifactPort

    if instance:
        data = odl.read_instance(instance)
        env = odl.instance_environment(instance)
        socket_path = str(env.get("NYAIRO_LIFE_SUPPLY_SOCKET") or "")
        subject_id = str(data.get("subject_id") or "")
    else:
        socket_path = str(supply_socket or "")
        subject_id = str(subject or "")
    if not socket_path or not subject_id:
        return None, None
    port = LifeArtifactPort(socket_path, subject_id)

    def verify(ref: str) -> Any:
        return port.resume_point(str(ref))

    return verify, port


def import_obligation(*, instance: Optional[str] = None,
                      state_root: Optional[str] = None,
                      supply_socket: Optional[str] = None,
                      subject: Optional[str] = None,
                      date: str, title: str, target_ref: str, due_at: str,
                      source_ref: str, operation_key: str,
                      completion: Optional[Mapping[str, Any]] = None,
                      expect_revision: Optional[int] = None,
                      now: Optional[str] = None,
                      target_verifier: Any = None) -> dict[str, Any]:
    """The operator entry: define one obligation, then return the row it really wrote.

    ``target_verifier`` may be supplied by a caller that already holds the owner seam (the
    smoke does this); otherwise it is built from the instance/socket.  With no verifier the write
    is refused by name -- an unverified target is not an executable arrangement.
    """

    root, data = _resolve_roots(instance=instance, state_root=state_root)
    closer = None
    verify = target_verifier
    if verify is None:
        verify, closer = _target_verifier(instance=instance, supply_socket=supply_socket,
                                          subject=subject)
    try:
        return ldp.import_obligation(
            root, date=date, title=title, target_ref=target_ref, due_at=due_at,
            source_ref=source_ref, operation_key=operation_key,
            completion=completion, subject_id=(data.get("subject_id") if data else subject),
            target_verifier=verify, expect_revision=expect_revision, now=now)
    finally:
        if closer is not None:
            try:
                closer.close()
            except Exception:
                pass


def today(*, instance: Optional[str] = None, state_root: Optional[str] = None,
          now: Optional[str] = None) -> dict[str, Any]:
    """Read-only check: today's plan, the obligations, and the projected results."""

    root, data = _resolve_roots(instance=instance, state_root=state_root)
    plan = ldp.DayPlan(root, subject_id=(data.get("subject_id") if data else None))
    obligations = ldp.DayObligations(root, subject_id=(data.get("subject_id") if data else None))
    day = ldp.business_date(now or odl_now())
    plan_view = {
        "status": "OK",
        "date": day,
        "entries": ldp.project_plan_entries(
            plan.for_date(day),
            futures_lookup=lambda ref: _fitem_row(root, ref)),
    }
    obligation_view = ldp.obligation_result_view(obligations, date=day, now=now or odl_now())
    return {"plan": plan_view, "obligations": obligation_view}


def _fitem_row(root: Path, item_ref: str) -> Optional[dict[str, Any]]:
    import life_future_items as lfi

    row = lfi.FutureItems(root).get(item_ref)
    return row.to_dict() if row is not None else None


def odl_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="v0.5 own-day operator entry")
    sub = parser.add_subparsers(dest="command", required=True)

    imp = sub.add_parser("obligation-import", help="define one obligation (operator only)")
    imp.add_argument("--instance")
    imp.add_argument("--state-root")
    imp.add_argument("--supply-socket")
    imp.add_argument("--subject")
    imp.add_argument("--date", required=True)
    imp.add_argument("--title", required=True)
    imp.add_argument("--target", required=True)
    imp.add_argument("--due-at", required=True)
    imp.add_argument("--source-ref", required=True)
    imp.add_argument("--operation-key", required=True)
    imp.add_argument("--expect-revision", type=int)
    imp.add_argument("--now")

    show = sub.add_parser("today", help="read today's plan and obligations")
    show.add_argument("--instance")
    show.add_argument("--state-root")
    show.add_argument("--now")

    args = parser.parse_args(argv)
    try:
        if args.command == "obligation-import":
            result = import_obligation(
                instance=args.instance, state_root=args.state_root,
                supply_socket=args.supply_socket, subject=args.subject,
                date=args.date, title=args.title, target_ref=args.target,
                due_at=args.due_at, source_ref=args.source_ref,
                operation_key=args.operation_key,
                expect_revision=args.expect_revision, now=args.now)
        else:
            result = today(instance=args.instance, state_root=args.state_root, now=args.now)
    except ldp.DayPlanError as exc:
        print(json.dumps({"status": "REFUSED", "reason_code": exc.reason_code,
                          "detail": exc.detail}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "OK", "result": result}, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())

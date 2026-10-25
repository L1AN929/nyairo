#!/usr/bin/env python3
"""v0.4 手机：prepare-demo（可带 --with-world）/ serve / 两个 owner 子进程入口 / 主体 Life 绑定。

    python scripts/phone.py prepare-demo --root /abs/new/root [--with-world]
    python scripts/phone.py serve --instance /abs/root --port 0 [--tick-seconds 5] [--with-room-living]
    python scripts/phone.py life-preflight --instance /abs/root
    python scripts/phone.py life-run --instance /abs/root --once
    python scripts/phone.py enable-instance-memory --instance /abs/root --owner chiyo \
        --operation-key enable-memory-1

``life-preflight`` / ``life-run`` 是本轮"主体用自己的材料"的**无界面**入口：它们复用
``components/phone/life.py``，把手机实例的 live 绑定映射给现有 Life runtime。preflight 纯读
（零写、零模型、无 socket 连接）；run 只起现有的 ``AutonomousLifeResident``，默认有限，
没有真实 provider 配置就诚实 BLOCKED，也从不替操作者打开自主开关、从不自己起 Supply/World。

``serve --with-room-living`` 是本轮房间链（实体店/交付/布置/房间灯）唯一的显式开关：它把
``CHIYO_WORLD_DAILY_RHYTHM_ENABLED`` 与 ``CHIYO_WORLD_ROOM_ENVIRONMENT_ENABLED`` 同时交给手机面
与它自己起的 World/Body 子进程，且**只在本进程内存里生效**（不写回 ``instance.json`` 的
``features``）。不带这个 flag 的启动与旧行为一致。

契约见 ``docs/engineering/v04-phone-api-contract.md``；世界/身体字段与五个动词的参数只按
``docs/engineering/v04-world-body-contract.md``（C 的契约）转发。结果见
``docs/engineering/v04-world-api-result.md``。Life 绑定见
``docs/engineering/rc1-subject-materials-runbook.md``。

``owner-supply`` / ``owner-world`` 是 ``serve`` 自己的子进程入口（不对外开放）：它们各自
就是一个真实 owner —— Life Supply 的 socket 服务与 ``WorldBodyService``。手机自己不发明
这两者，只在自己起的进程里显式启动，并在退出时只停自己起的 pid。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(REPO_ROOT),):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2
EXIT_OWNER_MISMATCH = 3
EXIT_CHILD_TIMEOUT = 4


def _emit(payload) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    sys.stdout.flush()


def cmd_prepare_demo(argv: list[str]) -> int:
    from components.phone import demo

    parser = argparse.ArgumentParser(prog="phone prepare-demo")
    parser.add_argument("--root", required=True)
    parser.add_argument("--seed", type=int, default=demo.DEFAULT_SEED)
    parser.add_argument("--theme", default=demo.DEFAULT_THEME)
    parser.add_argument("--with-world", action="store_true", dest="with_world",
                        help="生成真实室内场景/能力声明，并在新根里初始化一次 substrate 摆放")
    args = parser.parse_args(argv)
    try:
        report = demo.prepare_demo(Path(args.root), seed=args.seed, theme=args.theme,
                                   with_world=bool(args.with_world))
    except demo.PhoneDemoError as exc:
        _emit({"ok": False, "status": "rejected", "reason_code": str(exc)})
        return EXIT_USAGE if str(exc) in {"ROOT_NOT_ABSOLUTE", "ROOT_NOT_EMPTY",
                                          "ROOT_NOT_A_DIRECTORY",
                                          "SUBJECT_MUST_BE_CHIYO"} else EXIT_REFUSED
    _emit(report)
    return EXIT_OK


def cmd_serve(argv: list[str]) -> int:
    from components.phone import demo

    parser = argparse.ArgumentParser(prog="phone serve")
    parser.add_argument("--instance", required=True)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--ready-timeout", type=float, default=demo.CHILD_READY_TIMEOUT)
    parser.add_argument("--tick-seconds", type=float, default=demo.DEFAULT_WORLD_TICK_SECONDS,
                        help="World/Body owner tick 间隔（到达与恢复只在 tick 里推进）")
    parser.add_argument("--with-room-living", action="store_true", dest="with_room_living",
                        help="显式开启 rc1 房间链（daily_rhythm + room environment）："
                             "同一组开关交给手机面与它起的 World/Body 子进程，只在内存里生效")
    args = parser.parse_args(argv)
    if str(args.host).strip() != "127.0.0.1":
        # The bind address is not a caller choice: loopback only.
        _emit({"ok": False, "status": "rejected", "reason_code": "HOST_MUST_BE_LOOPBACK"})
        return EXIT_USAGE
    try:
        demo.serve_instance(Path(args.instance), port=int(args.port),
                            ready_timeout=float(args.ready_timeout),
                            tick_seconds=float(args.tick_seconds),
                            room_living=bool(args.with_room_living))
    except demo.PhoneDemoError as exc:
        reason = str(exc)
        _emit({"ok": False, "status": "rejected", "reason_code": reason})
        return EXIT_OWNER_MISMATCH if reason == "LOCAL_OWNER_UID_MISMATCH" else EXIT_CHILD_TIMEOUT
    except Exception as exc:  # never echo paths/tokens; keep the class name only
        _emit({"ok": False, "status": "error", "reason_code": "PHONE_SERVE_FAILED",
               "error_type": type(exc).__name__})
        return EXIT_REFUSED
    _emit({"ok": True, "status": "stopped"})
    return EXIT_OK


def cmd_owner_supply(argv: list[str]) -> int:
    """A real Life Supply owner process: one canonical db, one socket."""

    parser = argparse.ArgumentParser(prog="phone owner-supply")
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--socket", required=True)
    parser.add_argument("--writer-instance", default=None)
    args = parser.parse_args(argv)

    supply_root = str(REPO_ROOT / "components" / "supply")
    if supply_root not in sys.path:
        sys.path.insert(0, supply_root)
    from lifesupply.service.life_supply import LifeSupplyService, LifeSupplySocketServer
    from lifesupply.service.switches import FeatureSwitches

    uid = os.geteuid()
    socket_path = Path(args.socket)
    # Never delete or steal a socket.  This generation gets its own new path (see
    # components/phone/demo.py: supply_socket_for); if something is already there, refuse loudly
    # instead of calling the owner's unlink-based helper.
    if socket_path.exists() or socket_path.is_symlink():
        sys.stderr.write("refusing to bind an existing path: %s\n" % socket_path.name)
        return EXIT_REFUSED
    service = LifeSupplyService(
        Path(args.data_root),
        operator_uids={uid},
        service_uids={uid},
        switches=FeatureSwitches.all_on(),
        writer_instance=args.writer_instance or "phone-owner-supply:%d" % os.getpid(),
        require_non_root_identity=False,
    )
    if getattr(service, "startup_error", None):
        sys.stderr.write("life-supply startup error: %s\n" % service.startup_error)
        return EXIT_REFUSED
    server = LifeSupplySocketServer(str(socket_path), service)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        # The socket file stays on disk (retained evidence); only this pid's listeners close.
        server.server_close()
        service.close()
    return EXIT_OK


def cmd_owner_world(argv: list[str]) -> int:
    """A real ``WorldBodyService``: its own lease, gateway.sock and Resolver."""

    parser = argparse.ArgumentParser(prog="phone owner-world")
    parser.add_argument("--world-home", required=True)
    parser.add_argument("--package", default="")
    parser.add_argument("--tick-seconds", type=float, default=3600.0)
    #: 显式压缩演示模式：World/Body 进程必须读与 Life 同一个业务时钟域。
    #: 不传即普通真实时间路径，行为不变。
    parser.add_argument("--business-clock", default="")
    parser.add_argument("--clock-domain", default="")
    parser.add_argument("--clock-run-id", default="")
    args = parser.parse_args(argv)

    for entry in ("components/world/service", "components/world/scripts"):
        path = str(REPO_ROOT / entry)
        if path not in sys.path:
            sys.path.insert(0, path)
    import world_body_config as wcfg
    import world_body_service as wbsvc

    environment = dict(os.environ)
    if args.package:
        environment["CHIYO_WORLD_PACKAGE"] = str(args.package)
    try:
        config = wcfg.load_config(environment, runtime_root=REPO_ROOT / "components" / "world",
                                  world_home=args.world_home, runtime_mode="canonical",
                                  authority="production",
                                  world_id=environment.get("CHIYO_WORLD_ID") or None)
    except wcfg.ConfigError as exc:
        sys.stderr.write("world-body config refused: %s\n" % exc)
        return EXIT_USAGE
    clock = None
    if str(args.business_clock or "").strip():
        # 同一个域：缺文件/损坏/域不匹配一律拒绝启动，绝不悄悄退回系统墙钟。
        for entry in (str(REPO_ROOT / "components" / "life" / "scripts"),):
            if entry not in sys.path:
                sys.path.insert(0, entry)
        import demo_business_clock as dbc

        try:
            client = dbc.BusinessClockClient(
                args.business_clock,
                expected_domain=(str(args.clock_domain or "").strip() or None),
                expected_run_id=(str(args.clock_run_id or "").strip() or None))
            client.load()
        except dbc.BusinessClockError as exc:
            sys.stderr.write("world-body clock refused: %s: %s\n"
                             % (exc.reason_code, exc.detail))
            return EXIT_REFUSED
        clock = client.now
    service = wbsvc.WorldBodyService(config, tick_seconds=float(args.tick_seconds),
                                     clock=clock,
                                     instance_id="phone-world:%d" % os.getpid())
    try:
        service.startup()
    except wbsvc.StartupRefused as exc:
        sys.stderr.write("world-body startup refused: %s: %s\n" % (exc.condition, exc.detail))
        return EXIT_REFUSED
    try:
        service.serve()
    finally:
        service.shutdown()
    return EXIT_OK

def cmd_life_preflight(argv: list[str]) -> int:
    """Pure-read readiness of the subject chain on one phone instance (no write, no model)."""

    from components.phone import life

    parser = argparse.ArgumentParser(prog="phone life-preflight")
    parser.add_argument("--instance", required=True,
                        help="the phone instance root (the one ``serve --instance`` uses)")
    args = parser.parse_args(argv)
    try:
        report = life.life_preflight(Path(args.instance))
    except Exception as exc:  # never echo paths/keys; keep the class name only
        _emit({"ok": False, "status": "error", "reason_code": "LIFE_PREFLIGHT_FAILED",
               "error_type": type(exc).__name__})
        return EXIT_REFUSED
    _emit(report)
    return int(report.get("exit_code", life.EXIT_OK))


def cmd_life_run(argv: list[str]) -> int:
    """Run the existing Life resident on this phone instance's live binding.  Finite by default."""

    from components.phone import life

    parser = argparse.ArgumentParser(prog="phone life-run")
    parser.add_argument("--instance", required=True,
                        help="the phone instance root (the one ``serve --instance`` uses)")
    parser.add_argument("--once", action="store_true",
                        help="exactly one bounded tick (start, tick, stop)")
    parser.add_argument("--max-ticks", type=int, default=None,
                        help="upper bound on ticks when not using --once")
    parser.add_argument("--duration-seconds", type=int,
                        default=life.DEFAULT_RUN_DURATION_SECONDS,
                        help="finite wall-clock bound (default %d seconds)"
                             % life.DEFAULT_RUN_DURATION_SECONDS)
    parser.add_argument("--poll-seconds", type=int, default=None,
                        help="the resident's own poll interval (default: its own DEFAULT_POLL_SECONDS)")
    parser.add_argument("--no-act", action="store_true",
                        help="consider only; execute no action")
    args = parser.parse_args(argv)
    try:
        report = life.life_run(Path(args.instance), once=bool(args.once),
                               max_ticks=args.max_ticks,
                               duration_seconds=int(args.duration_seconds),
                               poll_seconds=args.poll_seconds, act=not args.no_act)
    except Exception as exc:  # never echo paths/keys; keep the class name only
        _emit({"ok": False, "status": "error", "reason_code": "LIFE_RUN_FAILED",
               "error_type": type(exc).__name__})
        return EXIT_REFUSED
    _emit(report)
    return int(report.get("exit_code", life.EXIT_BLOCKED))


def cmd_enable_instance_memory(argv: list[str]) -> int:
    """Explicitly give **one** phone instance its own Memory (evidence + separate ledger)."""

    from components.phone import life

    parser = argparse.ArgumentParser(prog="phone enable-instance-memory")
    parser.add_argument("--instance", required=True,
                        help="the phone instance root (the one ``serve --instance`` uses)")
    parser.add_argument("--owner", default=None,
                        help="the Memory owner to bind (default: the resident's own default)")
    parser.add_argument("--operation-key", required=True, dest="operation_key",
                        help="an explicit operation identity for this opt-in write")
    args = parser.parse_args(argv)
    try:
        report = life.enable_instance_memory(Path(args.instance), args.owner,
                                             args.operation_key)
    except Exception as exc:  # never echo paths/keys; keep the class name only
        _emit({"ok": False, "status": "error",
               "reason_code": "ENABLE_INSTANCE_MEMORY_FAILED",
               "error_type": type(exc).__name__})
        return EXIT_REFUSED
    _emit(report)
    return int(report.get("exit_code", life.EXIT_BLOCKED))


COMMANDS = {
    "prepare-demo": cmd_prepare_demo,
    "serve": cmd_serve,
    "owner-supply": cmd_owner_supply,
    "owner-world": cmd_owner_world,
    "life-preflight": cmd_life_preflight,
    "life-run": cmd_life_run,
    "enable-instance-memory": cmd_enable_instance_memory,
}


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] in {"-h", "--help"}:
        sys.stderr.write("usage: phone.py {prepare-demo,serve,owner-supply,owner-world,"
                         "life-preflight,life-run,enable-instance-memory} ...\n")
        return EXIT_USAGE if not arguments else EXIT_OK
    command = arguments[0]
    handler = COMMANDS.get(command)
    if handler is None:
        sys.stderr.write("unknown command: %s\n" % command)
        return EXIT_USAGE
    return handler(arguments[1:])


if __name__ == "__main__":
    sys.exit(main())

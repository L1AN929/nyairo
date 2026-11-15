#!/usr/bin/env python3
"""具身冷暖 / 窗扇与外衣 / 桌边温饮靠垫：一份全新的本地合成根。

    /tmp/rc104-venv/bin/python -B scripts/somatic_comfort_fixture.py --port 3857

这里没有手写的 World 快照、Body 返回、canonical Activity 或经济账本：

* 真实 Supply owner、``one_day_life.prepare``、``WorldGateway``、worldgen 真实室内家具、
  真实相册/分享/购买/阅读停点，全部复用居家四弹的 ``home_hearth_fixture``；
* 显式 ``body_lived_needs_profile.json`` 走 ``body_lived_needs`` 自己的 profile 写口，
  然后按同目录重启 World/Body owner —— 这样 needs 档位是真实 Body v2 的，不是假返回；
* 窗向/窗景仍由 ``window_keepsake_fixture`` 通过 ``world_home_space.customize_home_detail``
  真实写入；本弹不伪造窗区或桌区。

零模型、零 secrets、不复用历史 root；根目录与包目录保留，脚本不删除任何文件。
stdout 只给 root / cap 路径 / 端口 / 真实位置与 needs 档位，不给 CSRF token。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "tests"), str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import home_hearth_fixture as base  # noqa: E402
import one_day_life as odl  # noqa: E402

from components.phone import capability as phone_capability  # noqa: E402

SUBJECT = base.SUBJECT
#: Explicit synthetic initial needs; the Body owner writes its own v2 runtime from them.
NEEDS = {"hunger": 0.35, "thirst": 0.72, "toilet": 0.20}


def _enable_lived_needs(stack: SimpleNamespace) -> dict:
    import body_lived_needs as bln

    body_dir = Path(str(stack.data["world_home"])) / "data" / "body"
    profile = bln.build_profile(hunger=NEEDS["hunger"], thirst=NEEDS["thirst"],
                                toilet=NEEDS["toilet"])
    bln.save_profile(body_dir, profile)
    return {"profile_ref": bln.initial_state(profile)["profile_ref"],
            "initial": dict(NEEDS)}


def _restart_world_with_profile(stack: SimpleNamespace) -> dict:
    from test_one_day_life import WorldGateway

    world_home = Path(str(stack.data["world_home"]))
    world_id = str(stack.data["world_id"])
    environment = odl.instance_environment(stack.instance_dir)
    stack.world.stop()
    stack.world = WorldGateway(world_home, world_id=world_id, environment=environment)
    return {"world_home": str(world_home)}


def build_stack(root: Path | str, *, port: int = 0, capability: object = None
                ) -> SimpleNamespace:
    """One fresh root from the earlier waves' real owner constructors."""

    stack = base.build_stack(root, port=port, capability=capability)
    try:
        stack.needs_profile = _enable_lived_needs(stack)
        _restart_world_with_profile(stack)
        snapshot = stack.service.world()
        data = snapshot.get("data") if isinstance(snapshot.get("data"), dict) else {}
        location = data.get("location") if isinstance(data.get("location"), dict) else {}
        stack.location = {"place_id": location.get("place_id"),
                          "area_id": location.get("area_id")}
        stack.needs = (stack.service.body().get("data") or {}).get("needs")
        return stack
    except BaseException:
        base.close_stack(stack)
        raise


def serve_stack(stack: SimpleNamespace) -> None:
    base.serve_stack(stack)


def close_stack(stack: SimpleNamespace) -> None:
    base.close_stack(stack)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="somatic_comfort_fixture")
    parser.add_argument("--port", type=int, default=3857)
    parser.add_argument("--root", default="")
    args = parser.parse_args(argv)
    root = (Path(args.root) if args.root
            else Path(tempfile.gettempdir()) / ("nyairo-somatic-comfort-%d-%d" % (
                os.getpid(), time.time_ns())))
    stack = build_stack(root, port=int(args.port))
    server = stack.server
    if server.server_address[1] != int(args.port):
        close_stack(stack)
        parser.error("PORT_BIND_FAILED")
    sys.stdout.write(json.dumps({
        "ok": True, "root": str(stack.root), "port": stack.port_number,
        "capability_issued": stack.capability is not None,
        "capability_path": str(phone_capability.capability_path(stack.root)),
        "world_package": str(stack.package),
        "location": stack.location,
        "needs_profile": stack.needs_profile,
        "needs_bands": stack.needs,
        "state_endpoint": "/api/phone/state",
        "endpoints": ["/api/phone/body/somatic", "/api/phone/body/garment-window",
                      "/api/phone/body/care-offering"],
        "note": "window/garment/care conditions are this instance's explicit local "
                "simulation; drink uses the existing owner CONSUME only when a real "
                "thirst-relieving instance is bound",
    }, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    serve_stack(stack)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        close_stack(stack)
        return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

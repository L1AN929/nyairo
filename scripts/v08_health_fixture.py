#!/usr/bin/env python3
"""v0.8 身体健康读面：一份全新的本地合成根（真实 Supply/World/Phone）。

    /tmp/rc104-venv/bin/python -B scripts/v08_health_fixture.py --port 3870

* 复用 ``somatic_comfort_fixture`` 已经跑通的真实 Supply owner、
  ``one_day_life.prepare``、``WorldGateway`` 与 ``PhoneService``；
* 受限事实由 v03 自己的正式入口 ``apply_v03_ecology_command``
  （``record_strain_or_rest`` / ``caught_in_rain``）写入，不手填 ecology 文件；
* 不启动产品模型、不读 secrets、不复用历史 root；根目录保留，脚本不删除文件。

stdout 只给 root / 端口 / 安全的健康与能量档位，不给 token 或私文。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (
    str(ROOT), str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "components" / "world" / "service" / "bridge"),
    str(ROOT / "components" / "supply"),
    str(ROOT / "tests"), str(ROOT / "scripts"),
):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import somatic_comfort_fixture as base  # noqa: E402
import v03_economy_illness_bridge as v03  # noqa: E402


def seed_acute_course(day_root: Path | str, *, now_iso: str) -> dict:
    """Two formal rain exposures through the v03 owner's own command."""

    root = Path(day_root)
    issued = []
    for index in (1, 2):
        v03.apply_v03_ecology_command(
            root,
            {
                "action": "record_strain_or_rest",
                "event_kind": "caught_in_rain",
                "intensity": 1.0,
                "operation_key": "v08-health-rain-%d" % index,
            },
            actor_side="subject",
            now_iso=now_iso,
        )
        issued.append({"operation_key": "v08-health-rain-%d" % index})
    illness = (v03.project_v03_ecology(root, now_iso=now_iso).get("illness") or {})
    return {
        "events": issued,
        "phase": illness.get("phase"),
        "severity": illness.get("severity"),
        "episode_id": illness.get("episode_id"),
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="v08_health_fixture")
    parser.add_argument("--port", type=int, default=3870)
    parser.add_argument("--root", default="")
    args = parser.parse_args(argv)
    root = (Path(args.root) if args.root
            else Path(tempfile.gettempdir()) / ("nyairo-v08-health-%d-%d" % (
                os.getpid(), time.time_ns())))
    stack = base.build_stack(root, port=int(args.port))
    server = stack.server
    if server.server_address[1] != int(args.port):
        base.close_stack(stack)
        parser.error("PORT_BIND_FAILED")
    now_iso = stack.service._somatic_now_iso()
    seeded = seed_acute_course(stack.root, now_iso=now_iso)
    health = stack.service.body_health()[1]
    energy = health.get("energy") or {}
    sys.stdout.write(json.dumps({
        "ok": True,
        "root": str(stack.root),
        "port": stack.port_number,
        "world_package": str(stack.package),
        "health_status": health.get("health_status"),
        "attested": health.get("attested"),
        "health_source": health.get("health_source"),
        "capacity_state": (health.get("capacity") or {}).get("state"),
        "candidate_cap": (health.get("capacity") or {}).get("candidate_cap"),
        "creative_block": (health.get("capacity") or {}).get("creative_block"),
        "fatigue_band": energy.get("fatigue_band"),
        "seeded_events": seeded["events"],
        "endpoints": ["/api/phone/body/health", "/api/phone/body/somatic",
                      "/api/phone/state"],
    }, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    base.serve_stack(stack)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        base.close_stack(stack)
        return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

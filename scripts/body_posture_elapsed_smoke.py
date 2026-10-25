#!/usr/bin/env python3
"""持续姿态积分冒烟：真实 World owner + 可控业务钟，零模型。

不是手造 dict：用真实 ``init_instance`` 世界、真实 substrate、真实
``WorldBodyService``/Resolver，POSE 通过服务自己的 ``act`` 提交。业务时刻由
显式注入的 clock 推进（缩放时间，不真等数小时）；权限/超时仍是真实墙钟。

只对外看 ``body_summary()`` 的定性信号；姿态秒数属于内部证据，仅在本脚本里
打印用于核对。夹具根保留不删，服务由本脚本自己 shutdown，不碰任何旧实例。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (
    str(ROOT),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "components" / "world" / "service" / "bridge"),
):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from components.worldgen.instance import init_instance  # noqa: E402

import body_consequence as bcon  # noqa: E402
import body_runtime as br  # noqa: E402
import world_action_resolver as war  # noqa: E402
import world_body_config as wcfg  # noqa: E402
import world_body_service as wbsvc  # noqa: E402
import world_body_substrate as wbs  # noqa: E402
import world_runtime_data as wrd  # noqa: E402

WORLD_PACKAGE = ROOT / "examples" / "worlds" / "daily_neighborhood_v1"
BRIDGE_DIR = ROOT / "components" / "world" / "service" / "bridge"
START = datetime(2026, 10, 24, 0, 0, tzinfo=timezone.utc)


class ScaledClock:
    """演示业务钟：只推进业务时刻，不碰任何真实安全时间。"""

    def __init__(self, moment: datetime):
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment

    def advance(self, **kwargs) -> datetime:
        self.moment = self.moment + timedelta(**kwargs)
        return self.moment


def _file_fingerprints(root: Path) -> dict[str, str]:
    prints: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        prints[str(path.relative_to(root))] = digest
    return prints


def _last_consequence(service) -> dict:
    records = service.consequence_ledger.read_all()
    return records[-1] if records else {}


def _qualitative(service) -> dict:
    summary = service.body_summary()
    assert summary["raw_values_exposed"] is False, summary
    signal_set = summary.get("signal") or {}
    signals = [
        {"type": item.get("signal_type"), "band": item.get("qualitative_band")}
        for item in (signal_set.get("signals") or [])
    ]
    # pose 是 World owner 的量测；这里只读 substrate，不从 Body 里编一个
    substrate = wbs.SubstrateStore(
        wbs.substrate_path(service.config.world_home)
    ).load()
    return {
        "version": summary.get("version"),
        "pose": (substrate or {}).get("pose"),
        "recovery": summary["recovery"]["state"],
        # 真实 schema：body.signal.v1 的 signals[].signal_type/qualitative_band。
        # 空列表 = 现有阈值未触发（latch 为空），不是解析失败。
        "signals": signals,
        "latch": signal_set.get("latch"),
        "below_thresholds": not signals,
    }


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="nyairo-body-posture-"))
    home = root / "world-home"
    init_instance(WORLD_PACKAGE, home)
    state = json.loads((home / "data" / "world" / "world_state.json").read_text("utf-8"))
    world_id = state["world_id"]
    (home / "run").mkdir(parents=True, exist_ok=True)
    wbs.SubstrateStore(wbs.substrate_path(home)).save(
        wbs.initial_substrate(world_id=world_id)
    )
    body_runtime = br.initial_runtime(at=START)
    br.BodyStore(home / "data" / "body").save_runtime(body_runtime)

    env = {
        "CHIYO_WORLD_PACKAGE": str(WORLD_PACKAGE),
        "CHIYO_WORLD_HOME": str(home),
        "CHIYO_WORLD_ID": world_id,
        "CHIYO_WORLD_LIVING_SKELETON_ENABLED": "1",
        "CHIYO_WORLD_TRAVEL_TIME_ENABLED": "1",
        "CHIYO_WORLD_DAILY_RHYTHM_ENABLED": "0",
        "CHIYO_WORLD_AMBIENT_EVENTS_ENABLED": "0",
        "CHIYO_WORLD_SOCIAL_PRESENCE_ENABLED": "0",
        "CHIYO_WORLD_BODY_SUBSTRATE_ENABLED": "1",
        "WORLD_ACTION_AUTH_REGISTRY_DIR": str(home / "run"),
        "WORLD_ACTION_LIFECYCLE_PATH": str(home / "run" / "action_lifecycle.jsonl"),
        "WORLD_BODY_INTEGRATION_MODE": "canonical",
        "NYAIRO_WORLD_BRIDGE_DIR": str(BRIDGE_DIR),
    }
    saved_env = dict(os.environ)
    os.environ.update(env)
    wrd.reset_cache()

    clock = ScaledClock(START)
    config = wcfg.load_config(
        dict(env), runtime_root=ROOT / "components" / "world",
        world_home=home, runtime_mode="canonical",
        authority="production", world_id=world_id,
    )
    service = wbsvc.WorldBodyService(
        config, clock=clock, tick_seconds=3600.0,
        instance_id="body-posture-smoke-%d" % os.getpid(),
    )
    report: dict = {"root": str(root), "world_id": world_id, "steps": []}
    try:
        service.startup()
        report["startup_ready"] = service.ready

        def act(pose: str) -> dict:
            payload = service.act({
                "action": "POSE",
                "execution_id": "posture-%s-%d" % (pose, clock.moment.timestamp()),
                "actor_id": "chiyo",
                "body_id": "chiyo_body",
                "authority": war.AUTHORITY_VERIFIED_MAIN_SELF,
                "confirm_apply": True,
                "observed_dependencies": service.resolver._dependencies(),
                "params": {"pose": pose, "orientation": "north"},
            })
            record = _last_consequence(service)
            posture = [
                item for item in (record.get("inputs") or [])
                if item.get("class") == "PHYSICAL_RESULT" and item.get("posture_seconds")
            ]
            # 证据只读既有的 canonical runtime input，不新增落盘字段
            interval = (
                {"source": bcon.POSTURE_INTERVAL_SOURCE,
                 "seconds": posture[0]["posture_seconds"],
                 "pose": posture[0].get("pose")}
                if posture else None
            )
            return {
                "status": payload.get("status"),
                "applied_to_body": payload.get("applied_to_body"),
                "posture_interval": interval,
                "body": _qualitative(service),
            }

        report["steps"].append({"at": clock.moment.isoformat(), "action": "POSE seated",
                                "observed": act("seated")})

        clock.advance(hours=2)
        report["steps"].append({"at": clock.moment.isoformat(), "action": "POSE standing",
                                "observed": act("standing")})

        clock.advance(hours=1)
        report["steps"].append({"at": clock.moment.isoformat(), "action": "POSE seated",
                                "observed": act("seated")})

        # 缩放的长逻辑区间：8 小时坐姿，看定性档位是否真的变化（不真等 8 小时）
        clock.advance(hours=8)
        report["steps"].append({"at": clock.moment.isoformat(), "action": "POSE standing (after 8h seated)",
                                "observed": act("standing")})

        # 合法 idle tick（无环境灯、无 REST）不推进 body 游标：4 小时负荷必须
        # 留给下一次真实结算，而不是被空 tick 吞掉，也不能重复记。
        clock.advance(hours=4)
        cursor_before = service.body_state["last_advanced_at"]
        idle_ticks = [service.tick() for _ in range(3)]
        cursor_after = service.body_state["last_advanced_at"]
        report["idle_ticks"] = {
            "returned": idle_ticks,
            "cursor_before": cursor_before,
            "cursor_after": cursor_after,
            "cursor_unchanged": cursor_before == cursor_after,
        }
        report["steps"].append({"at": clock.moment.isoformat(),
                                "action": "POSE seated (after 4h tick-only)",
                                "observed": act("seated")})

        # 重复读零写：同一份磁盘证据在多次只读投影前后必须逐字节不变
        before = _file_fingerprints(root)
        for _ in range(3):
            _qualitative(service)
            service.status()
        after = _file_fingerprints(root)
        report["read_only_zero_write"] = {
            "files": len(before),
            "unchanged": before == after,
            "changed": sorted(
                name for name in set(before) | set(after)
                if before.get(name) != after.get(name)
            ),
        }

        report["ledger"] = {
            "consequences": len(service.consequence_ledger.read_all()),
            "transitions": len(service.body_store.read_transitions()),
        }
        report["body_final"] = _qualitative(service)
        report["ok"] = (
            report["read_only_zero_write"]["unchanged"]
            and [s["observed"]["posture_interval"] for s in report["steps"]]
            == [None, {"source": "elapsed_since_last_body_advance", "seconds": 7200.0,
                       "pose": "seated"},
                {"source": "elapsed_since_last_body_advance", "seconds": 3600.0,
                 "pose": "standing"},
                {"source": "elapsed_since_last_body_advance", "seconds": 28800.0,
                 "pose": "seated"},
                {"source": "elapsed_since_last_body_advance", "seconds": 14400.0,
                 "pose": "standing"}]
            and report["ledger"]["consequences"] == 5
            and all(item is None for item in report["idle_ticks"]["returned"])
            and report["idle_ticks"]["cursor_unchanged"]
        )
    finally:
        shutdown = service.shutdown()
        report["shutdown"] = {
            "lease_released": shutdown.get("lease_released"),
            "authority": shutdown.get("authority"),
            "gateway_socket_exists": service.gateway_socket_path.exists(),
        }
        try:
            os.environ.clear()
            os.environ.update(saved_env)
        finally:
            wrd.reset_cache()

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print("OK" if report.get("ok") else "FAILED")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

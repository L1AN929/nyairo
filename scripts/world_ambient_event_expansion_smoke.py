#!/usr/bin/env python3
"""环境事件目录扩充 + 有界跨天投影的隔离冒烟。

走既有正式路径：默认汐见市 World 数据（``world_runtime_data.active_world``）
→ 既有事件观察入口 ``materialize_ambient_events`` / ``active_ambient_events``
→ 既有 World 读口 ``observe_world``。隔离 root 里用正式 ``WorldStateStore``
与 ``world_timeline`` 建最小世界状态，不手塞最终事件列表，不启动 HTTP、
浏览器或产品 resident，不调用模型。

证明：每日仍受既有上限约束；日常/会变/有限跨天三类都能被正式观察读到；
有限跨天同一 ``event_id`` 在作者给定的日 1/日 2 连续、阶段叙述不同、期限后
消失，+8/+16 天与远未来都不复活；重复读取稳定且整个观察过程零写。

只输出事件 ID、时间窗、类别与数量，不输出私文、prompt、密钥或 headers。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
for entry in (
    ROOT,
    ROOT / "components",
    ROOT / "components" / "world" / "scripts",
):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

START_DAY = date(2026, 10, 24)
SCAN_DAYS = 16
TOKYO = ZoneInfo("Asia/Tokyo")


def _digest_tree(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            result[str(path.relative_to(root))] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return result


def _seed_world(root: Path, environment: dict[str, str]) -> None:
    """用正式 owner store 建最小世界状态与时间线（不写 Body/生活数据）。"""

    import world_foundation as wf
    import world_runtime_data as wrd
    import world_timeline as wt

    world = wrd.active_world(environment)
    world_dir = root / "data" / "world"
    world_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "schema_version": "world.foundation.v0",
        "revision": 1,
        "world_id": world.world_id,
        "timezone": world.timezone_name,
        "location": {"place_id": "nearby_street", "area_id": "nearby_street"},
        "scene": {"scene_id": "home.bedroom", "revision": 1},
        "facts": [],
    }
    wf.WorldStateStore(world_dir / "world_state.json").save(state, expected_revision=1)
    timeline = wt.build_initial_timeline(world.world_id, environment)
    wt.WorldTimelineStore(world_dir / "world_timeline.json").save(
        timeline, expected_revision=1
    )
    (root / "data" / "life_execution.jsonl").write_text("", encoding="utf-8")


def _midpoint(event: dict) -> datetime:
    start = datetime.fromisoformat(event["valid_from"])
    end = datetime.fromisoformat(event["valid_until"])
    return start + (end - start) / 2


def _record(store: dict[str, dict], event: dict, category: str) -> None:
    store.setdefault(
        event["event_type"],
        {
            "event_type": event["event_type"],
            "category": category,
            "event_id": event["event_id"],
            "location_id": event["location_id"],
            "valid_from": event["valid_from"],
            "valid_until": event["valid_until"],
        },
    )


def main() -> int:
    import world_ambient_events as wae
    import world_living_skeleton as wls
    import world_runtime_data as wrd

    root = Path(tempfile.mkdtemp(prefix="nyairo-ambient-smoke-"))
    report: dict = {"result": "FAIL", "root": str(root)}
    try:
        wrd.reset_cache()
        environment = {
            "CHIYO_WORLD_HOME": str(root),
            "CHIYO_WORLD_ID": "shiomi_city",
            "CHIYO_WORLD_LIVING_SKELETON_ENABLED": "1",
            "CHIYO_WORLD_AMBIENT_EVENTS_ENABLED": "1",
            "CHIYO_WORLD_TRAVEL_TIME_ENABLED": "0",
            "CHIYO_WORLD_DAILY_RHYTHM_ENABLED": "0",
            "CHIYO_WORLD_SOCIAL_PRESENCE_ENABLED": "0",
        }
        _seed_world(root, environment)
        before = _digest_tree(root)

        catalog = wae.ambient_template_catalog(environment)
        categories = {
            row["event_type"]: row["category"] for row in catalog["templates"]
        }
        report["catalog"] = {
            "world_id": catalog["world_id"],
            "total": catalog["total"],
            "unique_total": catalog["unique_total"],
            "categories": catalog["categories"],
            "max_events_per_day": catalog["max_events_per_day"],
        }
        assert 20 <= catalog["total"] <= 30, catalog["total"]
        assert catalog["unique_total"] == catalog["total"]

        daily: dict[str, dict] = {}
        changing: dict[str, dict] = {}
        cross: dict[str, dict] = {}

        for offset in range(SCAN_DAYS):
            day = START_DAY + timedelta(days=offset)
            events = wae.materialize_ambient_events(day, environment=environment)
            assert len(events) <= catalog["max_events_per_day"], (day, len(events))
            for event in events:
                category = categories[event["event_type"]]
                moment = _midpoint(event)
                observation = wls.observe_world(
                    hermes_home=root, now=moment, environment=environment
                )
                assert observation["status"] == "available", observation
                projected = {
                    item["event_id"] for item in observation.get("ambient_events", [])
                }
                assert event["event_id"] in projected, (day, event["event_id"])
                if category == "daily":
                    _record(daily, event, category)
                elif category == "changing":
                    _record(changing, event, category)
                else:
                    _record(cross, event, category)

        assert len(daily) >= 2, daily
        assert len(changing) >= 2, changing
        assert len(cross) >= 3, cross

        # 有限跨天：作者给定日 1/日 2 同一身份；期限后不复活。
        anchor_day = START_DAY
        anchor_events = [
            item
            for item in wae.materialize_ambient_events(
                anchor_day, environment=environment
            )
            if item["provenance"] == wae.FINITE_PROVENANCE
        ]
        assert len(anchor_events) == 1, anchor_events
        anchor = anchor_events[0]
        cross_anchor_id = anchor["event_id"]
        cross_anchor_type = anchor["event_type"]
        same_day = [anchor]
        next_day = [
            item
            for item in wae.materialize_ambient_events(
                anchor_day + timedelta(days=1), environment=environment
            )
            if item["event_id"] == cross_anchor_id
        ]
        assert len(same_day) == 1 and len(next_day) == 1, (same_day, next_day)
        assert same_day[0]["payload"]["summary"] != next_day[0]["payload"]["summary"]
        for offset in (2, 8, 16, 365):
            later = wae.materialize_ambient_events(
                anchor_day + timedelta(days=offset), environment=environment
            )
            assert cross_anchor_id not in {item["event_id"] for item in later}, offset
            assert cross_anchor_type not in {item["event_type"] for item in later}, offset

        # 重复读取同一时刻：投影稳定，不重演任何动作。
        repeat_moment = _midpoint(same_day[0])
        first = wls.observe_world(
            hermes_home=root, now=repeat_moment, environment=environment
        )
        second = wls.observe_world(
            hermes_home=root, now=repeat_moment, environment=environment
        )
        assert first["ambient_events"] == second["ambient_events"]

        after = _digest_tree(root)
        assert before == after, "观察过程改写了隔离世界文件"
        report["zero_write"] = True
        report["same_event_across_days"] = {
            "event_type": cross_anchor_type,
            "event_id": cross_anchor_id,
            "anchor_day": anchor_day.isoformat(),
            "day_windows": [
                {"local_date": item["local_date"], "valid_from": item["valid_from"],
                 "valid_until": item["valid_until"]}
                for item in (same_day[0], next_day[0])
            ],
            "expired_on": (anchor_day + timedelta(days=2)).isoformat(),
            "revived_on_plus_8_or_16": False,
        }
        report["daily"] = list(daily.values())
        report["changing"] = list(changing.values())
        report["cross_day"] = list(cross.values())
        report["counts"] = {
            "daily": len(daily),
            "changing": len(changing),
            "cross_day": len(cross),
        }
        report["result"] = "PASS"
        return 0
    except Exception as exc:  # noqa: BLE001 - 冒烟如实报告，不吞掉原因
        report["error"] = f"{type(exc).__name__}: {exc}"
        return 1
    finally:
        # 本冒烟不启动进程、不申请 lease；finally 里如实记录，不伪造释放动作。
        report["finally"] = {
            "processes_started": 0,
            "lease_acquired": False,
            "temporary_root_kept": True,
        }
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())

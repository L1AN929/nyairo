#!/usr/bin/env python3
"""世界包 ambient v2（有限跨天）的隔离冒烟。

走正式路径：仓库内 ``daily_neighborhood_v1`` 的 v2 包 → 既有 schema 校验与
loader → ``world_runtime_data`` → 正式 ``world_living_skeleton.observe_world``。
不手塞事件列表，不启动 HTTP/devserver、产品 resident 或 LLM。

证明：
* 正式观察能读到至少 3 个有限跨天事件在各自日 1/日 2 保持同一 event_id；
* 期限后、+8/+16 天不再出现；
* 清缓存重建读取上下文后同一时刻投影稳定；
* 改前 v1 fixture 包经正式读口的事件输出与固定 golden 一致；
* 观察过程不写隔离世界文件。

只输出安全的事件 ID、时间窗、状态与数量，不输出私文、prompt 或密钥。
"""

from __future__ import annotations

import hashlib
import json
import shutil
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

V2_PACKAGE = ROOT / "examples" / "worlds" / "daily_neighborhood_v1"
LEGACY_AMBIENT = ROOT / "tests" / "fixtures" / "world_ambient_v1_legacy.json"
LEGACY_GOLDEN = ROOT / "tests" / "fixtures" / "world_ambient_v1_legacy_golden.json"
START_DAY = date(2026, 10, 24)


def _digest_tree(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _environment(root: Path, package: Path) -> dict[str, str]:
    return {
        "CHIYO_WORLD_HOME": str(root),
        "CHIYO_WORLD_PACKAGE": str(package),
        "CHIYO_WORLD_ID": "daily_neighborhood_v1",
        "CHIYO_WORLD_LIVING_SKELETON_ENABLED": "1",
        "CHIYO_WORLD_AMBIENT_EVENTS_ENABLED": "1",
        "CHIYO_WORLD_TRAVEL_TIME_ENABLED": "0",
        "CHIYO_WORLD_DAILY_RHYTHM_ENABLED": "0",
        "CHIYO_WORLD_SOCIAL_PRESENCE_ENABLED": "0",
    }


def _seed_world(root: Path, environment: dict[str, str]) -> None:
    """用正式 owner store 建最小世界状态与时间线（不写 Body/生活数据）。"""

    import world_foundation as wf
    import world_runtime_data as wrd
    import world_timeline as wt

    world = wrd.active_world(environment)
    area = world.location("home")["area_id"]
    world_dir = root / "data" / "world"
    world_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "schema_version": "world.foundation.v0",
        "revision": 1,
        "world_id": world.world_id,
        "timezone": world.timezone_name,
        "location": {"place_id": "home", "area_id": area},
        "scene": {"scene_id": "home.bedroom", "revision": 1},
        "facts": [],
    }
    wf.WorldStateStore(world_dir / "world_state.json").save(state, expected_revision=1)
    timeline = wt.build_initial_timeline(world.world_id, environment)
    wt.WorldTimelineStore(world_dir / "world_timeline.json").save(
        timeline, expected_revision=1
    )
    (root / "data" / "life_execution.jsonl").write_text("", encoding="utf-8")


def _move_state(root: Path, environment: dict[str, str], place_id: str) -> None:
    """把隔离世界状态移到指定地点（正式 store，revision 递增）。"""

    import world_foundation as wf
    import world_runtime_data as wrd

    world = wrd.active_world(environment)
    path = root / "data" / "world" / "world_state.json"
    store = wf.WorldStateStore(path)
    state = store.load()
    state = dict(state)
    state["location"] = {
        "place_id": place_id,
        "area_id": world.location(place_id)["area_id"],
    }
    store.save(state, expected_revision=state["revision"])


def _midpoint(event: dict) -> datetime:
    start = datetime.fromisoformat(event["valid_from"])
    end = datetime.fromisoformat(event["valid_until"])
    return start + (end - start) / 2


def _observe(root: Path, environment: dict[str, str], place_id: str, moment: datetime):
    import world_living_skeleton as wls

    _move_state(root, environment, place_id)
    observation = wls.observe_world(
        hermes_home=root, now=moment, environment=environment
    )
    assert observation["status"] == "available", observation
    return observation


def _safe(event: dict) -> dict:
    return {
        "event_id": event["event_id"],
        "location_id": event["location_id"],
        "local_date": event["local_date"],
        "valid_from": event["valid_from"],
        "valid_until": event["valid_until"],
    }


def main() -> int:
    import world_ambient_events as wae
    import world_living_skeleton as wls
    import world_runtime_data as wrd
    from worldgen import schema

    root = Path(tempfile.mkdtemp(prefix="nyairo-ambient-v2-smoke-"))
    report: dict = {"result": "FAIL", "root": str(root)}
    exit_code = 1
    try:
        wrd.reset_cache()
        environment = _environment(root, V2_PACKAGE)
        _seed_world(root, environment)

        catalog = wae.ambient_template_catalog(environment)
        finite_rows = [row for row in catalog["templates"] if row["finite"]]
        report["catalog"] = {
            "world_id": catalog["world_id"],
            "total": catalog["total"],
            "categories": catalog["categories"],
            "finite": len(finite_rows),
            "max_events_per_day": catalog["max_events_per_day"],
        }
        assert 20 <= catalog["total"] <= 30, catalog["total"]
        assert len(finite_rows) == 8, len(finite_rows)

        # 收集 3 个不同的有限跨天事件（按出现顺序）。
        targets: dict[str, dict] = {}
        for offset in range(6):
            day = START_DAY + timedelta(days=offset)
            for event in wae.materialize_ambient_events(day, environment=environment):
                if event["provenance"] != wae.FINITE_PROVENANCE:
                    continue
                targets.setdefault(event["event_id"], event)
        assert len(targets) >= 3, targets
        selected = list(targets.values())[:3]

        verified: list[dict] = []
        for event in selected:
            event_id = event["event_id"]
            day1 = day = date.fromisoformat(event["local_date"])
            place_id = event["location_id"]
            observation1 = _observe(root, environment, place_id, _midpoint(event))
            projected1 = {
                item["event_id"]: item
                for item in observation1.get("ambient_events", [])
            }
            assert event_id in projected1, (event_id, projected1.keys())

            day2 = day1 + timedelta(days=1)
            next_events = wae.materialize_ambient_events(
                day2, environment=environment
            )
            continued = [item for item in next_events if item["event_id"] == event_id]
            assert len(continued) == 1, (event_id, next_events)
            observation2 = _observe(root, environment, place_id, _midpoint(continued[0]))
            projected2 = {
                item["event_id"]: item
                for item in observation2.get("ambient_events", [])
            }
            assert event_id in projected2, (event_id, projected2.keys())
            assert (
                projected1[event_id]["payload"]["summary"]
                != projected2[event_id]["payload"]["summary"]
            )

            for offset in (2, 8, 16):
                later_day = day2 + timedelta(days=offset)
                later = wae.materialize_ambient_events(
                    later_day, environment=environment
                )
                assert event_id not in {item["event_id"] for item in later}, (
                    event_id,
                    later_day,
                )

            verified.append(
                {
                    "event_id": event_id,
                    "location_id": place_id,
                    "day1": _safe(projected1[event_id]),
                    "day2": _safe(projected2[event_id]),
                    "phase_changed": True,
                    "expired_by": (day2 + timedelta(days=2)).isoformat(),
                }
            )

        assert len(verified) >= 3, verified

        # 重建读取上下文：同一时刻同一地点投影稳定。
        event = selected[0]
        moment = _midpoint(event)
        _move_state(root, environment, event["location_id"])
        before_digest = _digest_tree(root)
        first = wls.observe_world(
            hermes_home=root, now=moment, environment=environment
        )
        after_digest = _digest_tree(root)
        assert before_digest == after_digest, "正式观察改写了隔离世界文件"
        wrd.reset_cache()
        second = wls.observe_world(
            hermes_home=root, now=moment, environment=environment
        )
        assert first["ambient_events"] == second["ambient_events"]
        report["reader_restart_stable"] = True
        report["zero_write"] = True

        # 改前 v1 fixture 包：正式入口输出必须与固定 golden 一致。
        legacy_dir = root / "legacy_package"
        shutil.copytree(V2_PACKAGE, legacy_dir)
        (legacy_dir / "ambient.json").write_bytes(LEGACY_AMBIENT.read_bytes())
        schema.write_json(
            legacy_dir / "MANIFEST.json", schema.build_manifest(legacy_dir)
        )
        legacy_env = _environment(root, legacy_dir)
        wrd.reset_cache()
        golden = json.loads(LEGACY_GOLDEN.read_text(encoding="utf-8"))
        for day_text, expected in golden["days"].items():
            actual = wae.materialize_ambient_events(
                date.fromisoformat(day_text), environment=legacy_env
            )
            assert actual == expected, day_text
        legacy_catalog = wae.ambient_template_catalog(legacy_env)
        assert legacy_catalog["categories"]["cross_day"] == 0, legacy_catalog
        legacy_now = datetime(2026, 10, 24, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        legacy_observation = wls.observe_world(
            hermes_home=root, now=legacy_now, environment=legacy_env
        )
        assert legacy_observation["status"] == "available", legacy_observation
        golden_ids = {
            item["event_id"] for item in golden["days"]["2026-10-24"]
        }
        observed_ids = {
            item["event_id"]
            for item in legacy_observation.get("ambient_events", [])
        }
        assert observed_ids <= golden_ids, (observed_ids, golden_ids)
        report["legacy"] = {
            "golden_days": sorted(golden["days"]),
            "catalog_total": legacy_catalog["total"],
            "cross_day": legacy_catalog["categories"]["cross_day"],
            "observe_ambient_count": len(observed_ids),
            "observe_ids_within_golden": True,
        }

        report["finite_events"] = verified
        report["result"] = "PASS"
        exit_code = 0
    except Exception as exc:  # noqa: BLE001 - 冒烟如实报告，不吞掉原因
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        report["finally"] = {
            "processes_started": 0,
            "lease_acquired": False,
            "http_server_started": False,
            "llm_called": False,
            "temporary_root_kept": True,
        }
        report["exit_code"] = exit_code
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""为生成世界初始化一个隔离的 World/Body 实例目录。

一个实例 = 一个独立的 ``CHIYO_WORLD_HOME``：

    <instance_home>/
        data/world/world_state.json      世界状态（初始位置 = home/bedroom）
        data/world/world_timeline.json   空时间线
        data/world/body_substrate.json   身体基座（空放置、standing）
        data/life_execution.jsonl        空执行账本
        instance.json                    实例元数据（绑定的世界包）

实例初始化只使用 rc9 既有的规范写入器（WorldStateStore /
WorldTimelineStore / SubstrateStore），不发明新的状态格式；
不同实例的数据目录完全隔离，不得混用。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Optional

from . import GENERATOR_VERSION, schema

_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "world" / "scripts"


def _ensure_scripts_path() -> None:
    if str(_SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS_DIR))


def _stores():
    _ensure_scripts_path()
    import world_foundation as wf
    import world_timeline as wt

    return wf, wt


def _substrate_module():
    _ensure_scripts_path()
    import world_body_substrate as wbs

    return wbs


INSTANCE_META_FILENAME = "instance.json"


def init_instance(
    package_dir: Path | str,
    instance_home: Path | str,
    *,
    world_id: Optional[str] = None,
) -> dict[str, Any]:
    """在世界包之外创建一个全新的隔离实例。已存在的实例永远不会被覆盖。"""
    package = None
    from .loader import load

    package = load(package_dir)
    expected_id = world_id or package.world_id
    if expected_id != package.world_id:
        raise schema.WorldPackageError(
            f"实例期望 world_id {expected_id!r}，但世界包是 {package.world_id!r}"
        )

    home = Path(instance_home).resolve()
    if home.exists():
        raise FileExistsError(
            f"实例目录已存在，初始化绝不覆盖: {home}（请换一个目录或删除后重建）"
        )
    world_dir = home / "data" / "world"

    wf, wt = _stores()
    state_path = world_dir / "world_state.json"
    timeline_path = world_dir / "world_timeline.json"

    initial_state = {
        "schema_version": "world.foundation.v0",
        "revision": 1,
        "world_id": package.world_id,
        "timezone": package.timezone_name,
        "location": {
            "place_id": package.home_location_id,
            "area_id": package.home_area_id,
        },
        "scene": {
            "scene_id": f"{package.home_location_id}.{package.home_area_id}",
            "revision": 1,
        },
        "facts": [],
    }
    timeline = _build_timeline(wt, package)

    ledger_path = home / "data" / "life_execution.jsonl"

    # 写入顺序：目录 → 世界状态 → 时间线 → 账本 → 基座 → 元数据。
    world_dir.mkdir(parents=True, exist_ok=True)
    wf.WorldStateStore(state_path).save(initial_state, expected_revision=1)
    wt.WorldTimelineStore(timeline_path).save(timeline, expected_revision=1)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text("", encoding="utf-8")

    wbs = _substrate_module()
    substrate = wbs.initial_substrate(world_id=package.world_id, body_id="chiyo_body")
    substrate_store = wbs.SubstrateStore(wbs.substrate_path(home))
    substrate_store.save(substrate)
    binding = wbs.verify_binding(substrate_store.load(), initial_state)
    if not binding["bound"]:
        raise RuntimeError(f"实例基座绑定校验失败: {binding.get('reasons')}")

    meta = {
        "schema_version": "worldgen.instance.v1",
        "world_id": package.world_id,
        "world_name": package.display_name,
        "package_dir": str(package.package_dir),
        "generator_version": package.generator_version,
        "created_by": f"nyairo-worldgen {GENERATOR_VERSION}",
        "created_at_epoch": int(time.time()),
    }
    (home / INSTANCE_META_FILENAME).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 写后核验：用与运行时完全相同的读取路径回读。
    loaded_state = wf.WorldStateStore(state_path).load()
    loaded_timeline = wt.WorldTimelineStore(timeline_path).load()
    if loaded_state is None or loaded_state["world_id"] != package.world_id:
        raise RuntimeError("实例世界状态写入后校验失败")
    if loaded_timeline is None or loaded_timeline["world_id"] != package.world_id:
        raise RuntimeError("实例时间线写入后校验失败")

    return {
        "status": "created",
        "instance_home": str(home),
        "world_id": package.world_id,
        "world_name": package.display_name,
        "initial_location": f"{package.home_location_id}/{package.home_area_id}",
        "package_dir": str(package.package_dir),
        "binding": binding,
    }


def _build_timeline(wt, package) -> dict[str, Any]:
    """用规范构建器产出时间线；白名单按活动世界判断，故在此声明世界包。"""
    import os

    import world_runtime_data as wrd

    previous = os.environ.get(wrd.CHIYO_WORLD_PACKAGE_ENV)
    os.environ[wrd.CHIYO_WORLD_PACKAGE_ENV] = str(package.package_dir)
    wrd.reset_cache()
    try:
        return wt.build_initial_timeline(package.world_id)
    finally:
        if previous is None:
            os.environ.pop(wrd.CHIYO_WORLD_PACKAGE_ENV, None)
        else:
            os.environ[wrd.CHIYO_WORLD_PACKAGE_ENV] = previous
        wrd.reset_cache()


def read_instance_meta(instance_home: Path | str) -> dict[str, Any]:
    path = Path(instance_home) / INSTANCE_META_FILENAME
    if not path.exists():
        raise FileNotFoundError(f"目录不是 worldgen 实例（缺少 {INSTANCE_META_FILENAME}）: {instance_home}")
    return json.loads(path.read_text(encoding="utf-8"))


__all__ = ["INSTANCE_META_FILENAME", "init_instance", "read_instance_meta"]

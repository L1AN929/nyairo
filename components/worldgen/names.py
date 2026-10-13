#!/usr/bin/env python3
"""从模板片段池确定性地组合中文名称与描述。

所有函数只依赖传入的 ``random.Random``，不直接触碰全局随机源，
保证同一种子在任何机器上的组合结果一致。
"""

from __future__ import annotations

import random
from typing import Any


def compose_name(rng: random.Random, candidates: list[str], *, world_name: str, prefix_pool: list[str]) -> str:
    """从候选名列表中选择；``{world_name}``/``{prefix}`` 占位符被替换。"""
    if not candidates:
        return world_name
    name = rng.choice(candidates)
    return (
        name.replace("{world_name}", world_name)
        .replace("{prefix}", rng.choice(prefix_pool))
    )


def compose_description(rng: random.Random, candidates: list[str]) -> str:
    return rng.choice(candidates)


def compose_world_name(rng: random.Random, fragments: dict[str, list[str]]) -> str:
    prefix = rng.choice(fragments["prefix"])
    suffix = rng.choice(fragments["suffix"])
    return f"{prefix}{suffix}"


def pick_weighted(rng: random.Random, items: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    """按 ``weight`` 字段做无放回加权抽取，保持模板顺序返回。"""
    remaining = list(items)
    picked: list[dict[str, Any]] = []
    while remaining and len(picked) < count:
        total = sum(max(1, int(item.get("weight", 1))) for item in remaining)
        needle = rng.uniform(0, total)
        cursor = 0.0
        for index, item in enumerate(remaining):
            cursor += max(1, int(item.get("weight", 1)))
            if needle <= cursor:
                picked.append(remaining.pop(index))
                break
        else:  # pragma: no cover - 浮点兜底
            picked.append(remaining.pop())
    return picked


__all__ = [
    "compose_description",
    "compose_name",
    "compose_world_name",
    "pick_weighted",
]

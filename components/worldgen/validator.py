#!/usr/bin/env python3
"""世界包结构验证器。

在 schema 格式校验之上做图级与规则级检查：

* 道路图从 ``home`` 出发必须能到达全部地点（不允许有出不了门/进不去的区域）；
* ``reachable`` 与 ``edges`` 必须一致（对称、无悬空）;
* 每个地点都必须有营业规则（运行时逐地点查询规则）；
* ``home`` 的内部区域必须是 ``bedroom``（与 rc9 既有世界模块的规范 ID 一致）;
* 世界版本与当前生成器版本不匹配时明确报错，不静默替换。

所有错误都带中文说明与具体字段定位。
"""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any, Optional

from . import schema


class WorldValidationError(ValueError):
    """世界包结构无效。"""


def _fail(messages: list[str], message: str) -> None:
    messages.append(message)


def validate_graph(docs: dict[str, Any]) -> list[str]:
    """返回错误列表；空列表表示结构有效。"""
    messages: list[str] = []
    world = docs["world.json"]
    topology = docs["topology.json"]
    rules = docs["rules.json"]["location_rules"]
    locations = {loc["location_id"]: loc for loc in topology["locations"]}

    if world["generator_version"] != schema.GENERATOR_VERSION:
        _fail(
            messages,
            f"世界版本不匹配：世界包由 {world['generator_version']} 生成，"
            f"当前运行环境是 {schema.GENERATOR_VERSION}。"
            "请使用与生成时配套的生成器版本，或重新生成世界。",
        )

    # ---- reachable 与 edges 一致性 ----------------------------------------
    edge_pairs: dict[str, set[str]] = {loc_id: set() for loc_id in locations}
    for edge in topology["edges"]:
        edge_pairs[edge["from"]].add(edge["to"])
        edge_pairs[edge["to"]].add(edge["from"])
    for loc_id, location in locations.items():
        declared = set(location["reachable"])
        derived = edge_pairs[loc_id]
        if declared != derived:
            missing = sorted(derived - declared)
            extra = sorted(declared - derived)
            _fail(
                messages,
                f"地点 {loc_id} 的 reachable 与道路不一致"
                f"（缺少: {missing}，多出: {extra}）",
            )

    # ---- 连通性：从 home 出发可达全部地点 ----------------------------------
    if "home" not in locations:
        _fail(messages, "缺少住宅地点 home：运行时要求世界有一个 id 为 home 的住宅")
    else:
        seen = {"home"}
        queue = deque(["home"])
        while queue:
            current = queue.popleft()
            for neighbor in sorted(edge_pairs.get(current, ())):
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append(neighbor)
        unreachable = sorted(set(locations) - seen)
        if unreachable:
            _fail(
                messages,
                f"道路图不连通：以下地点无法从 home 到达（会出不了门或进不去）: {unreachable}",
            )

    # ---- home 的室内区域约定 ----------------------------------------------
    home = locations.get("home")
    if home is not None and home["area_id"] != "bedroom":
        _fail(
            messages,
            f"home 的 area_id 必须是 bedroom（运行时家空间/物品投递依赖该规范 ID），"
            f"实际是 {home['area_id']!r}",
        )

    # ---- 营业规则覆盖 -------------------------------------------------------
    missing_rules = sorted(set(locations) - set(rules))
    if missing_rules:
        _fail(messages, f"以下地点缺少营业规则（运行时逐地点查询）: {missing_rules}")
    extra_rules = sorted(set(rules) - set(locations))
    if extra_rules:
        _fail(messages, f"rules.location_rules 包含不存在的地点: {extra_rules}")

    # ---- 最低规模 ------------------------------------------------------------
    if len(locations) < 6:
        _fail(messages, f"地点数量过少（{len(locations)}），无法构成可生活的小镇")

    return messages


def validate(docs: dict[str, Any]) -> list[str]:
    """完整校验：先格式，再结构。返回全部错误信息。"""
    messages: list[str] = []
    for name, validator in schema.DOC_VALIDATORS.items():
        doc = docs.get(name)
        if doc is None:
            _fail(messages, f"缺少 {name}")
            continue
        try:
            docs[name] = validator(doc)
        except schema.WorldPackageError as exc:
            _fail(messages, f"{name}: {exc}")
    if messages:
        return messages
    messages.extend(validate_graph(docs))
    return messages


def validate_package_dir(package_dir, *, verify_manifest: bool = True) -> list[str]:
    """从目录加载并校验，返回错误列表（空 = 有效）。"""
    try:
        docs = schema.load_package(package_dir, verify=verify_manifest)
    except schema.WorldPackageError as exc:
        return [str(exc)]
    return validate(docs)


def validate_package(package_or_docs: Any, *, verify_manifest: bool = True) -> list[str]:
    """统一校验入口：支持世界包目录路径、WorldPackage 实例或内存文档字典。"""
    if isinstance(package_or_docs, (str, Path)):
        return validate_package_dir(package_or_docs, verify_manifest=verify_manifest)
    if hasattr(package_or_docs, "snapshot") and callable(package_or_docs.snapshot):
        docs = package_or_docs.snapshot()
        scene_doc = getattr(package_or_docs, "scene", None)
        if scene_doc is not None:
            docs[schema.SCENE_FILE] = scene_doc
    elif isinstance(package_or_docs, dict):
        docs = dict(package_or_docs)
    else:
        return [f"不支持的世界包输入类型: {type(package_or_docs).__name__}"]
    messages = validate(docs)
    if not messages and schema.SCENE_FILE in docs and docs[schema.SCENE_FILE] is not None:
        try:
            schema.validate_scene_doc(docs[schema.SCENE_FILE])
        except schema.WorldPackageError as exc:
            _fail(messages, f"{schema.SCENE_FILE}: {exc}")
    return messages


__all__ = [
    "WorldValidationError",
    "validate",
    "validate_graph",
    "validate_package",
    "validate_package_dir",
]

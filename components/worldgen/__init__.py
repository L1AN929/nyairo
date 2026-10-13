"""NyAiro WorldGen v0.1.

程序化世界生成与运行时适配：把一个随机种子变成一座可被 rc9 World/Body
运行时真实加载、行走、观察、持久化的小镇。

分层约定：

* 本包（worldgen）负责世界包的生成、校验、预览与实例初始化；
* ``components/world/scripts/world_runtime_data`` 负责在运行时把
  ``CHIYO_WORLD_PACKAGE`` 指向的世界包解析成五个世界模块可用的数据；
* World Runtime 仍然拥有全部状态权威：世界包只是静态世界定义。

本包只使用 Python 标准库，生成器可以脱离 Hermes 独立运行。
"""

from __future__ import annotations

GENERATOR_VERSION = "worldgen.v0.1"
PACKAGE_SCHEMA = "worldgen.package.v1"

__all__ = ["GENERATOR_VERSION", "PACKAGE_SCHEMA"]

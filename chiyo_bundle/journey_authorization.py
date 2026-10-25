"""把 World 自己的正式提交口交给旅程引擎与 World 前台推进器。

为什么需要这层
==============
``world_journey`` / ``world_runner`` / ``world_move_tool`` 都活在 World 侧，不能
反向依赖 Life 的适配器。正式口的实现放在
``components/world/service/bridge/journey_submit_port.py``；本模块只负责按环境
找到那个目录并构造端口。

失败语义
========
找不到目录、模块不可导入或构造失败时抛出异常，由调用方（
``world_journey._default_submit_port``）转成 ``None`` 并失败关闭。这里没有
"退回进程内直写"的开关，引擎也没有直写旁路参数：offline 底层测试要观测
故障窗口时，自己注入一个测试专用 ``submit_port``（见
``tests/test_world_journey.py``），生产调用者选不到那条路。

这里不是第二套授权实现：端口本身只组装 intent，签发、消费、gate 与 TTL 仍由
``intent_executor`` / ``world_action_authorization`` / gateway 决定。
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Optional

#: 显式指定 World bridge 目录（与 Life 侧同名环境变量一致）。
BRIDGE_DIR_ENV = "NYAIRO_WORLD_BRIDGE_DIR"
_MODULE_NAME = "journey_submit_port"


def bridge_directory(environment: Optional[Mapping[str, str]] = None) -> Optional[Path]:
    """返回 bridge 目录；显式环境变量优先，否则用本仓库的相对位置。"""

    env = dict(environment if environment is not None else os.environ)
    explicit = str(env.get(BRIDGE_DIR_ENV) or "").strip()
    if explicit:
        return Path(explicit)
    repo = Path(__file__).resolve().parents[1]
    candidate = repo / "components" / "world" / "service" / "bridge"
    return candidate if candidate.is_dir() else None


def _submit_port_module(directory: Path) -> Any:
    text = str(directory)
    if text not in sys.path:
        sys.path.insert(0, text)
    return importlib.import_module(_MODULE_NAME)


def default_submit_port(*, environment: Optional[Mapping[str, str]] = None) -> Any:
    """构造一个正式提交口；不可达时抛异常（调用方必须失败关闭）。"""

    env = dict(environment if environment is not None else os.environ)
    directory = bridge_directory(env)
    if directory is None or not Path(directory).is_dir():
        raise ImportError("the World bridge directory is not reachable; set %s"
                          % BRIDGE_DIR_ENV)
    module = _submit_port_module(Path(directory))
    return module.default_submit_port(environment=env, bridge_dir=Path(directory))


__all__ = ["BRIDGE_DIR_ENV", "bridge_directory", "default_submit_port"]

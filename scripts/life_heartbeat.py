"""NyAiro 生命推进入口（W03：薄包装，委托前台常驻 runner）。

本文件只做参数转发与兼容别名，实现都在 ``chiyo_bundle/world_runner.py``：

* ``run``    —— 前台常驻推进（单实例 run lease、monotonic 调度、UTC 真实时间）；
* ``once``   —— 一次显式推进（保留 W02 旧调用；不带子命令时默认走这里）；
* ``status`` —— 只读结构化状态（零创建、零写入、不结算到达）；
* ``pause`` / ``resume`` —— 在 journey 锁内切换持久暂停位。

不生成目标、不调用 LLM、不做宵禁/漫游/推送；不安装 systemd/计划任务/daemon，
不注册自启动（未来由 supervisor 托管的命令见 ``docs/engineering/v03-runner-usage.md``）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Mapping, Optional

REL = Path(__file__).resolve().parents[1]
for _path in (str(REL), str(REL / "components/world/scripts")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from chiyo_bundle import journey_authorization, world_journey, world_runner  # noqa: E402

#: 环境装配（显式 world_home/profile 字段，不继承进程世界配置）
build_environment = world_runner.build_environment
#: 一次性推进结果分类
classify_journey_result = world_runner.classify_journey_result
#: 常驻循环
run_forever = world_runner.run_forever
#: 只读状态
runner_status = world_runner.runner_status
#: pause / resume
set_pause = world_runner.set_pause
#: CLI
main = world_runner.main
parse_args = world_runner.parse_args


def advance_once(
    world_home, environment: Mapping[str, str], now: Optional[Any] = None
) -> dict:
    """W02 兼容别名：对已存在的旅程做一次显式推进（不创建任何东西）。"""
    # 下一段是新的 Action：走 World 自己的正式授权入口；不可达时失败关闭。
    try:
        submit_port = journey_authorization.default_submit_port(environment=environment)
    except Exception:
        submit_port = None
    return world_journey.advance_journey(
        hermes_home=world_home, environment=environment, now=now,
        submit_port=submit_port,
    )


if __name__ == "__main__":  # pragma: no cover - CLI 入口
    raise SystemExit(main())

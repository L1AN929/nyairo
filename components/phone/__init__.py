"""v0.4 手机（P 写集）：真实 Supply 私人事项 + W 的 PhoneShopping 的本地 owner 面。

模块边界：

* ``service`` —— 一个实例的读/写面；所有真相来自 Supply 与 World owner，从不本地复制。
* ``server``  —— loopback-only HTTP，一个 JSON 信封 + CSRF/Host/Origin/字段白名单闸门。
* ``demo``    —— ``prepare-demo``（一次真实实例）与 ``serve``（自己起的两个 owner 子进程）。
* ``web/``    —— U 的前端（固定三个静态文件，绝不映射 profile/仓库根）。

命令行入口是 ``scripts/phone.py``；``python -m components.phone`` 等价于它的 ``main``。
"""
from __future__ import annotations

from .server import PhoneHTTPServer, create_server, http_status_for
from .service import PhoneService, PhoneServiceError, PhoneUnavailable, load_instance

__all__ = [
    "PhoneHTTPServer",
    "PhoneService",
    "PhoneServiceError",
    "PhoneUnavailable",
    "create_server",
    "http_status_for",
    "load_instance",
]

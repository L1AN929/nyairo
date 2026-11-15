#!/usr/bin/env python3
"""共享的业务日界（day_key）工具：19 章 §一.4 的唯一权威。

为什么要有这个模块
------------------
在这之前，"今天"有三种互相不一致的答案：

* ``life_request_budget`` 的预算日界：可配置的业务时区，默认固定偏移 ``+08:00``；
* 世界侧六个模块各自硬编码 ``Asia/Tokyo``（+09:00）作为日界/月界默认时区；
* 少数调用点还在函数参数里再写一遍 ``"Asia/Tokyo"`` 字面量。

同一个时刻在 +09:00 与 +08:00 两侧会落在不同的"日"上（23:00-24:00 UTC+8 之间），
预算账本记的日和世界日历记的日因此可能错位。本模块把日界收敛成**一个**答案：
默认固定偏移 +08:00（与 ``life_request_budget.DEFAULT_BUSINESS_TZ`` 同源同值），
可经 ``budget_policy.json`` 或 ``CHIYO_BUSINESS_TZ`` 等环境变量统一覆盖。

本模块只依赖标准库、零写盘、零网络；``life_request_budget`` 与世界侧各日历
消费点引用这里，不允许再各自硬编码时区字面量。
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any, Mapping, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]

SCHEMA_VERSION = "world.business_day_key.v1"

#: 默认业务时区：固定偏移 +08:00，与 ``life_request_budget.DEFAULT_BUSINESS_TZ``
#: 是同一个值、同一个语义。世界日界与预算日界从这里取得同一个答案。
DEFAULT_BUSINESS_TZ = timezone(timedelta(hours=8))
#: 默认业务时区的拼写。固定偏移不是 IANA 名，ZoneInfo 解析不了它，所以凡是
#: 把时区当**字符串**传递/落盘/比对的地方引用这个标签，而把 tzinfo 当对象
#: 使用的地方引用 :data:`DEFAULT_BUSINESS_TZ`。
DEFAULT_BUSINESS_TZ_LABEL = "+08:00"

_OFFSET_RE = re.compile(r"^([+-])(\d{1,2})(?::?(\d{2}))?$")

#: 与 ``life_request_budget`` 完全一致的键名集合：同一个策略文件、同一组环境
#: 变量、同一个默认值。两边永远读出同一个时区。
POLICY_KEYS = ("business_tz", "timezone", "tz", "day_rollover_tz")
ENV_KEYS = ("CHIYO_BUSINESS_TZ", "CHIYO_BUDGET_TZ", "NYA02_BUSINESS_TZ")


def parse_business_tz(spec: Any) -> Optional[tzinfo]:
    """把一个时区拼写解析成 tzinfo；解析不了返回 ``None``，绝不猜。

    接受：tzinfo 本身、数字（小时偏移）、``UTC``/``Z``/``GMT``、``+08:00`` 这类
    固定偏移拼写，以及 IANA 名（``ZoneInfo`` 可解析时）。
    """

    if spec is None:
        return None
    if isinstance(spec, tzinfo):
        return spec
    if isinstance(spec, (int, float)) and not isinstance(spec, bool):
        return timezone(timedelta(hours=float(spec)))
    raw = str(spec).strip()
    if not raw:
        return None
    upper = raw.upper()
    if upper in {"UTC", "Z", "+00:00", "-00:00", "GMT", "UTC+0", "UTC+00:00"}:
        return timezone.utc
    cleaned = raw[3:].strip() if upper.startswith("UTC") and len(raw) > 3 else raw
    match = _OFFSET_RE.fullmatch(cleaned)
    if match:
        sign = -1 if match.group(1) == "-" else 1
        hours = int(match.group(2))
        minutes = int(match.group(3) or "0")
        return timezone(sign * timedelta(hours=hours, minutes=minutes))
    if ZoneInfo is not None:
        try:
            return ZoneInfo(raw)
        except Exception:
            return None
    return None


def format_tz_label(tz: tzinfo) -> str:
    """给报告用的一行时区拼写（IANA 名或 UTC 偏移标签）。"""

    if tz is timezone.utc:
        return "UTC"
    key = getattr(tz, "key", None)
    if isinstance(key, str) and key.strip():
        return key.strip()
    sample = datetime(2026, 1, 1, 12, 0, tzinfo=tz)
    offset = tz.utcoffset(sample)
    if offset is None:
        return DEFAULT_BUSINESS_TZ_LABEL
    total_minutes = int(offset.total_seconds() // 60)
    if total_minutes == 0:
        return "UTC"
    sign = "+" if total_minutes >= 0 else "-"
    abs_minutes = abs(total_minutes)
    return "%s%02d:%02d" % (sign, abs_minutes // 60, abs_minutes % 60)


def _load_policy_tz(state_root: Optional[Path]) -> Optional[tzinfo]:
    """从 Life 预算策略文件里读业务时区（与 budget 同一文件、同一批键）。"""

    if state_root is None:
        return None
    candidates = [
        state_root / "budget_policy.json",
        state_root / "life_budget_policy.json",
    ]
    if state_root.name == "state":
        candidates.extend([
            state_root.parent / "budget_policy.json",
            state_root.parent / "state" / "life_budget_policy.json",
        ])
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, Mapping):
            continue
        for key in POLICY_KEYS:
            parsed = parse_business_tz(payload.get(key))
            if parsed is not None:
                return parsed
    return None


def resolve_default_business_tz(state_root: Optional[Path] = None) -> tzinfo:
    """业务时区的唯一裁决顺序：策略文件 -> 环境变量 -> 默认 +08:00。"""

    policy_tz = _load_policy_tz(state_root)
    if policy_tz is not None:
        return policy_tz
    for env_key in ENV_KEYS:
        env_val = os.environ.get(env_key)
        parsed = parse_business_tz(env_val)
        if parsed is not None:
            return parsed
    return DEFAULT_BUSINESS_TZ


def business_day(moment: Optional[datetime] = None, *, tz: Optional[tzinfo] = None) -> str:
    """一个时刻落在哪个业务日（``%Y-%m-%d``）。这就是共享的 day_key。

    ``moment`` 缺省用当前时刻；naive datetime 按 ``tz`` 原样落位；aware datetime
    先换算到 ``tz`` 再取日期。``tz`` 缺省走 :func:`resolve_default_business_tz`。
    """

    target_tz = tz if tz is not None else resolve_default_business_tz()
    if moment is None:
        dt = datetime.now(target_tz)
    elif moment.tzinfo is None:
        dt = moment.replace(tzinfo=target_tz)
    else:
        dt = moment.astimezone(target_tz)
    return dt.strftime("%Y-%m-%d")


def resolve_zone(spec: Any) -> Optional[tzinfo]:
    """给 ZoneInfo 消费点的替换入口：一种拼写 -> 一个 tzinfo，未知返回 ``None``。

    世界侧原来直接 ``ZoneInfo(WORLD_TIMEZONE)``；默认值换成固定偏移 ``+08:00``
    之后，IANA-only 的 ``ZoneInfo`` 解析不了它，所以一律先走本函数（偏移与
    IANA 都能解析），再由调用方决定失败时是拒绝（fail closed）还是回默认。
    """

    return parse_business_tz(spec)


__all__ = [
    "SCHEMA_VERSION",
    "DEFAULT_BUSINESS_TZ",
    "DEFAULT_BUSINESS_TZ_LABEL",
    "POLICY_KEYS",
    "ENV_KEYS",
    "business_day",
    "format_tz_label",
    "parse_business_tz",
    "resolve_default_business_tz",
    "resolve_zone",
]

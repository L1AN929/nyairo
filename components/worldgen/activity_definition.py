#!/usr/bin/env python3
"""活动定义层：世界包怎样**明确声明**一个可执行的停留定义。

这是三层把关里的第一层——定义成立与否。它只回答"这条 affordance 是不是一个
结构完整的停留定义"，不回答"此刻能不能做"（第二层：时间/营业/权限），也不发
任何执行授权（第三层：AG-0/Decision）。机制 ``stay_at_place`` 只是对已经实现的
"原地停留计划"的定义引用，不是权限、不是新动作。

本版只有两种既有活动允许显式引用该机制：``REST`` 与 ``READ_HERE``。它们都只
对应现有的 ``stay:<place>:<descriptor>:<seconds>`` 计划；``READ_HERE`` 不因此
声称真的取得书页或完成读书。

没有任何 ``execution`` 的 affordance 仍然只是地点对环境的描述（MOVE、橱窗、
长椅……），既不会被升级成动作，也不会被当成错误。区别只看结构：是否显式声明
了 ``execution``，不看 ID 像不像动作、也不解析标题/描述的自由文本。
"""

from __future__ import annotations

from typing import Any, Mapping

#: 本版唯一存在的底层机制：一个已经实现的原地停留计划。
STAY_MECHANISM = "stay_at_place"
#: 允许显式引用 ``stay_at_place`` 的全部合法原地停留活动（排除 MOVE 等移动或未知标签）。
SUPPORTED_STAY_ACTIVITIES = frozenset({
    "SIT", "SIT_AT_CAFE", "REST", "READ_HERE", "READ", "SELF_STUDY", "USE_PHONE",
    "BROWSE_BOOKS", "BROWSE_STORE", "PRAY", "LOOK_AROUND", "WALK",
    "DAZE", "POTTER", "PEOPLE_WATCH",
})
#: ``execution`` 只允许这一个键；未知键一律拒绝。
EXECUTION_KEYS = frozenset({"mechanism"})

#: 稳定、安全（不回显输入值）的错误码。
UNSUPPORTED_ACTIVITY_DEFINITION = "UNSUPPORTED_ACTIVITY_DEFINITION"
INVALID_ACTIVITY_EXECUTION = "INVALID_ACTIVITY_EXECUTION"
INCOMPLETE_ACTIVITY_EXECUTION = "INCOMPLETE_ACTIVITY_EXECUTION"

#: 出错时可以安全写进定位路径的合同字段（闭集，永远不是输入值）。
FIELD_AFFORDANCE_ID = "affordance_id"
FIELD_EXECUTION = "execution"
FIELD_TITLE = "title"
FIELD_SECONDS = "suggested_seconds"
DEFINITION_FIELDS = frozenset(
    {FIELD_AFFORDANCE_ID, FIELD_EXECUTION, FIELD_TITLE, FIELD_SECONDS})


class ActivityDefinitionError(ValueError):
    """一条显式活动定义不成立。

    ``code`` 是稳定的固定错误码；``field`` 是闭集里的合同字段名，供上层拼一条
    安全的结构定位（例如 ``.../affordances/0/suggested_seconds``）。二者都不含
    任何输入值。
    """

    def __init__(self, code: str, field: str = FIELD_EXECUTION) -> None:
        if field not in DEFINITION_FIELDS:
            field = FIELD_EXECUTION
        super().__init__(code)
        self.code = code
        self.field = field


def declares_execution(affordance: Any) -> bool:
    """这条 affordance 是否**显式**声明了执行机制（结构信号，不看 ID）。"""

    return isinstance(affordance, Mapping) and "execution" in affordance


def _plain_title(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text or len(text) > 80:
        return False
    return not any(ord(char) < 32 or ord(char) == 127 for char in text)


def _seconds(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 86400


def validate_execution(affordance: Mapping[str, Any]) -> dict[str, str]:
    """校验并归一化一条显式 ``execution`` 声明。

    通过时返回 ``{"mechanism": "stay_at_place"}``；不成立时抛
    :class:`ActivityDefinitionError`，其 ``code`` 固定且不含任何输入值。
    """

    execution = affordance.get("execution")
    if not isinstance(execution, Mapping) or not execution:
        raise ActivityDefinitionError(INVALID_ACTIVITY_EXECUTION, FIELD_EXECUTION)
    if set(execution) != EXECUTION_KEYS:
        raise ActivityDefinitionError(INVALID_ACTIVITY_EXECUTION, FIELD_EXECUTION)
    mechanism = execution.get("mechanism")
    if not isinstance(mechanism, str) or mechanism != STAY_MECHANISM:
        raise ActivityDefinitionError(INVALID_ACTIVITY_EXECUTION, FIELD_EXECUTION)
    activity_id = affordance.get("affordance_id")
    if not isinstance(activity_id, str) or activity_id not in SUPPORTED_STAY_ACTIVITIES:
        raise ActivityDefinitionError(
            UNSUPPORTED_ACTIVITY_DEFINITION, FIELD_AFFORDANCE_ID)
    # 显式机制必须把展示标题与建议秒数一起写死；不从描述自由文本里解析。
    if not _plain_title(affordance.get("title")):
        raise ActivityDefinitionError(INCOMPLETE_ACTIVITY_EXECUTION, FIELD_TITLE)
    if not _seconds(affordance.get("suggested_seconds")):
        raise ActivityDefinitionError(INCOMPLETE_ACTIVITY_EXECUTION, FIELD_SECONDS)
    return {"mechanism": STAY_MECHANISM}


__all__ = [
    "ActivityDefinitionError",
    "DEFINITION_FIELDS",
    "EXECUTION_KEYS",
    "FIELD_AFFORDANCE_ID",
    "FIELD_EXECUTION",
    "FIELD_SECONDS",
    "FIELD_TITLE",
    "INCOMPLETE_ACTIVITY_EXECUTION",
    "INVALID_ACTIVITY_EXECUTION",
    "STAY_MECHANISM",
    "SUPPORTED_STAY_ACTIVITIES",
    "UNSUPPORTED_ACTIVITY_DEFINITION",
    "declares_execution",
    "validate_execution",
]

"""Resident 读面投影计数：一个 wake 只记一次，一段到达只记一次。

resident journal 每个 tick 写一行，每行都带着 runner 的 ``due`` 投影、事件投影和
旅程结算记录。这些是**读面**：已经关上的检查窗每个 poll 都会再投影一条
``NOT_DUE``；已经完成的旅程在下一段开始前每个 tick 都会再投影一次
``completed + segments_confirmed``。按投影行数计数会把一次安静铺成几百个 wake、
把三段到达铺成几百次 arrival（fastday-e：17 条 wake 记录对
``counters.wakes=327``，3 段确认对 ``journey_arrivals=292``）。

本模块把两条规则放在 resident 之外，实时路径和 journal 重建共用同一套算术：

* wake 只在 runner **真的写进** ``autonomous_wakes.jsonl`` 时计一次。runner 的
  写盘语义是：非静默记录全写；连续静默（``NOT_DUE``/``QUIET``）只写第一条。
  这里用同一个状态机判断，因此不新增持久身份，也不依赖 ``at`` 去重——同一
  时间戳的不同事件仍然各计一次。写盘抛错的记录带
  ``extra["journal_write_failed"]``（wake journal 上没有这一行；resident journal
  的 due 投影会带上该标记，重放时同样不计），不冒充一条已落盘的 wake。
* arrival 按 owner 旅程记录里**已确认段数**的单调增量计数
  （``segments_confirmed``）。同一条完成投影读一百遍增量是 0；同一旅程的第二段
  增量是 1，所以同旅程两段计 2，另一旅程各计各的。缺 ``journey_id`` 或
  ``segments_confirmed`` 的记录不计数——坏身份不编数字。

内存里只留最近若干个旅程/拒绝键做**加速缓存**，它不是权威：缓存未命中时，调用方
可以传入 ``history`` 回调，按目标旅程回读既有 resident journal 的**先前行**，取历史
最大 ``segments_confirmed`` 或已记过的拒绝键。缓存被淘汰不会抹掉持久事实；代价是
未命中时要扫一遍 journal（通常每进程只在淘汰后发生，不是每 tick）。没有 ``history``
的纯 helper 仍按有界缓存工作，不承诺无限历史精确。

没有任何新的落盘格式：状态在每次 ``_replay_journal`` 时从既有 journal 行重建。
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

#: runner 认为“什么都没考虑”的状态。连续出现时只有第一条被写进 wake journal。
QUIET_WAKE_STATUSES = frozenset({"NOT_DUE", "QUIET"})

#: 只在内存里保留最近若干个旅程身份/拒绝键；它们是加速缓存，不是历史权威。
DEFAULT_MAX_TRACKED_JOURNEYS = 32
DEFAULT_MAX_TRACKED_REFUSALS = 64

#: ``history(journey_id, before_tick)`` -> ``{"max_confirmed": int, "refusals": set}``。
#: ``before_tick`` 是“当前行之前”的边界：只有 tick 更小的 journal 行才能作为过去证据。
#: 返回 ``None`` 表示持久源明确不可用（读失败/边界未知），调用方按未知处理。
JourneyHistory = Callable[[str, Any], "Mapping[str, Any] | None"]


def is_wake_record(record: Any) -> bool:
    """``True`` for a real ``WakeRecord.to_dict()``, ``False`` for a synthetic tick dict.

    ``runner_tick`` 在邀请已用掉本 tick 决策时直接返回一个
    ``{"kind": "CHECK_WINDOW", "status": "CONTACT_DECISION_HANDLED", ...}`` 的
    合成 dict；它没有走 ``_record``，也就没有写 wake journal。按“与 journal 对齐”
    的口径，它不算一个 wake。
    """

    if not isinstance(record, Mapping):
        return False
    return (bool(record.get("at"))
            and "status_class" in record
            and bool(record.get("status")))


def journal_write_failed(record: Any) -> bool:
    """``True`` when the runner tried to journal this record and the write raised.

    标记只存在于内存中的 ``extra``；``WakeRecord.to_dict()`` 在写盘成功之后才被
    别人读到，失败时磁盘上没有这一行，因此落盘格式没有变化。
    """

    if not isinstance(record, Mapping):
        return False
    extra = record.get("extra")
    return isinstance(extra, Mapping) and extra.get("journal_write_failed") is True


def is_quiet_wake(record: Any) -> bool:
    return (is_wake_record(record)
            and str(record.get("status") or "") in QUIET_WAKE_STATUSES)


class ProjectionCounter:
    """跨实时 tick 与 journal replay 共用的读面计数状态。

    一个实例只服务于一个 resident 生命周期。它只读记录、只记数字：不调用
    owner、不写 World、不改变任何调度或动作轨迹。
    """

    def __init__(self, *, max_tracked_journeys: int = DEFAULT_MAX_TRACKED_JOURNEYS,
                 max_tracked_refusals: int = DEFAULT_MAX_TRACKED_REFUSALS,
                 history: "JourneyHistory | None" = None) -> None:
        self._quiet_tail = False
        self._journey_confirmed: dict[str, int] = {}
        self._journey_order: list[str] = []
        self._refusal_keys: dict[tuple[str, int, str], None] = {}
        self._max_journeys = max(1, int(max_tracked_journeys))
        self._max_refusals = max(1, int(max_tracked_refusals))
        self._history = history

    # -- wakes ---------------------------------------------------------------------

    def wake_is_recorded(self, record: Any) -> bool:
        """这条投影是否对应 wake journal 里真实写下的一行。

        调用顺序必须与 runner 的 ``_record`` 顺序一致（事件先、check window 后），
        实时路径与 replay 都按 journal 行内的同一顺序喂进来。
        """

        if not is_wake_record(record):
            return False
        quiet = str(record.get("status") or "") in QUIET_WAKE_STATUSES
        if journal_write_failed(record):
            # runner 的静默 streak 标记仍然前进（它已经认为自己写过这一条），所以
            # 计数侧也要跟着吞掉后续静默；但这一条本身没有落盘，不计数。
            self._quiet_tail = quiet
            return False
        if quiet and self._quiet_tail:
            self._quiet_tail = True
            return False
        self._quiet_tail = quiet
        return True

    # -- arrivals ------------------------------------------------------------------

    def journey_delta(self, record: Any, *, before: Any = None) -> tuple[int, int]:
        """返回 ``(new_confirmed_segments, new_refusals)``，均为非负增量。

        只认 owner 旅程记录里的 ``journey_id`` 与单调 ``segments_confirmed``；
        ``arrived`` 布尔值不再作为身份——完成投影会被反复读，布尔值每次都真。
        ``before`` 是当前 journal 行的 tick 边界：history 只回读 tick 更小的行，
        当前行和任何未来行都不能被提前当成过去证据。
        """

        if not isinstance(record, Mapping) or record.get("ok") is not True:
            return (0, 0)
        journey_id = record.get("journey_id")
        if not isinstance(journey_id, str) or not journey_id.strip():
            return (0, 0)
        confirmed = record.get("segments_confirmed")
        if isinstance(confirmed, bool) or not isinstance(confirmed, int) or confirmed < 0:
            return (0, 0)

        journey_id = journey_id.strip()
        evidence: Mapping[str, Any] | None = None
        if journey_id in self._journey_confirmed:
            previous = self._journey_confirmed[journey_id]
        else:
            evidence = self._recover(journey_id, before)
            if evidence is None and self._history is not None:
                # journal 读不出来时不能拿 0 冒充“没来过”：那会把已经确认过的旧段
                # 重新算成一次到达。整条投影按未知丢弃，等下一次可读再算。
                return (0, 0)
            previous = (0 if evidence is None
                        else self._non_negative_int(evidence.get("max_confirmed")))

        arrivals = 0
        if confirmed > previous:
            arrivals = confirmed - previous
        watermark = max(previous, confirmed)
        self._journey_confirmed[journey_id] = watermark
        self._touch_journey(journey_id)

        refusals = 0
        reason = record.get("arrival_refused")
        if isinstance(reason, str) and reason.strip():
            key = (journey_id, confirmed, reason.strip())
            if key not in self._refusal_keys:
                if evidence is None and self._history is not None:
                    evidence = self._recover(journey_id, before)
                known = (evidence is not None
                         and key[1:] in self._evidence_refusals(evidence))
                if known:
                    # 旧拒绝键被 LRU 淘汰过：持久行还在，补回缓存但不再计数。
                    self._remember_refusal(key)
                elif evidence is None and self._history is not None:
                    # 持久源不可用 -> 未知，不假计一条新拒绝。
                    pass
                else:
                    self._remember_refusal(key)
                    refusals = 1
        return (arrivals, refusals)

    def _recover(self, journey_id: str, before: Any) -> Mapping[str, Any] | None:
        if self._history is None:
            return None
        try:
            evidence = self._history(journey_id, before)
        except Exception:  # a read failure is unknown, never a new event
            return None
        if not isinstance(evidence, Mapping):
            return None
        return evidence

    @staticmethod
    def _non_negative_int(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return 0
        return value

    @staticmethod
    def _evidence_refusals(evidence: Mapping[str, Any]) -> set[tuple[int, str]]:
        raw = evidence.get("refusals")
        found: set[tuple[int, str]] = set()
        if not isinstance(raw, (set, frozenset, list, tuple)):
            return found
        for item in raw:
            if (isinstance(item, (tuple, list)) and len(item) == 2
                    and isinstance(item[0], int) and not isinstance(item[0], bool)
                    and isinstance(item[1], str)):
                found.add((item[0], item[1]))
        return found

    def _remember_refusal(self, key: tuple[str, int, str]) -> None:
        self._refusal_keys[key] = None
        while len(self._refusal_keys) > self._max_refusals:
            self._refusal_keys.pop(next(iter(self._refusal_keys)))

    def _touch_journey(self, journey_id: str) -> None:
        if journey_id not in self._journey_order:
            self._journey_order.append(journey_id)
        while len(self._journey_order) > self._max_journeys:
            stale = self._journey_order.pop(0)
            if stale != journey_id:
                self._journey_confirmed.pop(stale, None)

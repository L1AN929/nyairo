#!/usr/bin/env python3
"""v0.4 手机来访面（P 的窄门面）：只投递 A 的 ``LocalContactInbox`` transport 事件。

它是什么
--------
一部手机上「有人来访 / 有一段共同时间」的真实入口：一条普通来话、一次明确范围的邀请、
一次显式结束。三种东西都只是**投递**给她那个实例的本地收件箱：

==========================  ==========================================================
来话 / 邀请 / 结束事件      A 的 ``LocalContactInbox``（``<life state root>/life_local_contact.json``）
处理结果 / 延后条件 / 范围  同上（A 的 operation key 与 result 记录）
共同时间会话（只读）        ``<life state root>/co_presence.json``（B 的
                            ``FiniteCoPresence`` 会话记录；这里只读、只投影几个标签）
操作来源（用户/模型）        ``<root>/phone/operations.jsonl``（P 写，P 读）
==========================  ==========================================================

它不是什么
----------
* **不是第二个 Life writer**：没有 daemon、没有线程、没有 tick。本模块一次调用只做一次
  读写，从不驱动她的活动，也从不 ``claim`` / ``record_result`` —— 那是消费方（B 的
  ``life_co_presence`` 接线）的事。手机只 submit / list / detail / operation。
* **不是 Activity / 关系 owner**：本文件里没有任何 Activity 状态、没有关系分、没有第二把
  pause 开关。一次 invite 是否被接受由她自己的 ``FiniteCoPresence.consider()`` 决定；手机
  的答案是 ``queued``（已记下、还没处理），永远不是 ``accepted``。
* **不是外发渠道**：来源身份固定为 ``local_operator``，渠道只有 ``local_inbox``；本文件里
  没有任何 telegram / HTTP / SMTP 代码路径，也没有「用户已收到」这种话。
* **不接受任意身份或路径**：subject / home / state root 全部由**实例**构造；客户端带来的
  ``uid`` / ``home`` / ``path`` / ``url`` / ``channel`` 在 server 层按名拒绝。

身份与根的来源（不猜）
----------------------
``state_root`` 就是这台实例 Life 运行时的那个根（``phone_life.life_state_root``：先看实例
环境里记录的 ``NYA02_RESIDENT_STATE``，再退到 ``<root>/one-day/life/state``）。``subject``
是手机实例自己的 subject。``home_ref`` 按同一个实例环境里的名字依次取：
``NYA02_LIFE_HOME``（A 自己的名字）→ 实例文件的 ``contact_home_ref`` → ``CHIYO_WORLD_HOME``
→ 实例的 ``world_home``；每一档都记录下来源，客户端永远不能提供。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
_LIFE_SCRIPTS = REPO_ROOT / "components" / "life" / "scripts"
for _entry in (str(REPO_ROOT), str(_LIFE_SCRIPTS)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

#: A 的词表与边界：这里**照抄同一个常量**，不自己再定义一套（漂移就是两个真相源）。
from life_local_contact import (  # noqa: E402
    ALL_KINDS,
    ALL_OUTCOMES,
    ALL_STATES,
    CHANNEL_LOCAL_INBOX,
    KIND_END,
    KIND_INVITATION,
    KIND_MESSAGE,
    MAX_BODY_CHARS,
    MAX_EVENTS,
    MAX_LIST,
    MAX_SCOPE_CHARS,
    OUTCOME_ACCEPTED,
    OUTCOME_DECLINED,
    OUTCOME_DEFERRED,
    OUTCOME_ENDED,
    OUTCOME_OBSERVED,
    OUTCOME_UNKNOWN,
    SOURCE_LOCAL,
    STATE_CLAIMED,
    STATE_DEFERRED,
    STATE_PENDING,
    STATE_PROCESSED,
    STATE_UNKNOWN,
    LocalContactError,
    LocalContactInbox,
)

SCHEMA_CONTACT = "phone.contact.v1"

STATUS_OK = "ok"
STATUS_APPLIED = "applied"
STATUS_ALREADY = "already_applied"
STATUS_REJECTED = "rejected"
STATUS_UNAVAILABLE = "unavailable"
STATUS_NOT_FOUND = "not_found"
STATUS_UNKNOWN = "unknown"
STATUS_ABSENT = "absent"
STATUS_UNREADABLE = "unreadable"
STATUS_CONFLICT = "conflict"

#: 手机上同一次列表最多看多少条（与 A 的 ``MAX_LIST`` 同一件事，不另立上限）。
MAX_CONTACT_LIST = MAX_LIST
#: 一次请求最多几个事件位（A 的容量上限；这里只是同一件事的预检）。
MAX_CONTACT_EVENTS = MAX_EVENTS

#: 一次来话的正文上限（与 A 同一个值，P 只做可见的预检）。
MAX_CONTACT_MESSAGE = MAX_BODY_CHARS
#: 一次邀请的范围/持续时间上限（同上）。
MAX_CONTACT_SCOPE = MAX_SCOPE_CHARS
MAX_CONTACT_SECONDS = 24 * 3600

#: P 的三种意图名字：与 A 的 kind **一一对应**，不做名称翻译。
KIND_MESSAGE_PHONE = KIND_MESSAGE
KIND_INVITATION_PHONE = KIND_INVITATION
KIND_END_PHONE = KIND_END

#: 每一次读都带着这句话：它是**事实声明**，不是界面文案。
LOCAL_ONLY_NOTE = "只进这台实例自己的本地收件箱，没有发到任何外部渠道，也不等于用户已收到"

#: ``GET /contact`` 里那个只读会话块的字段（B 的 ``co_presence.json``，只投影标签）。
SESSION_FIELDS = ("state", "invitation_id", "scope", "opened_at", "joint_activity_id")
#: 会话状态里不算「开着」的那几个（A/B 自己的词，原样保留）。
SESSION_CLOSED_STATES = frozenset({"NONE", "CLOSED", "ENDED", "ABSENT", "UNREADABLE",
                                   "INVALID"})


def _line(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else ("" if value is None else str(value))
    return " ".join(text.split())[:limit]


def _text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return value[:limit]


def _int_or_none(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return int(value)


def _counts(raw: Any) -> dict[str, int]:
    block = raw if isinstance(raw, Mapping) else {}
    return {str(key): (_int_or_none(value) or 0) for key, value in block.items()}


def home_ref_for(environment: Optional[Mapping[str, str]], instance: Mapping[str, Any],
                 *, root: Optional[Path | str] = None) -> tuple[str, str]:
    """The one home value this instance gives its own co-presence protocol.

    Order is deliberate and it matches **B's own binding**: ``autonomous_life_assembly``
    builds ``FiniteCoPresence(home=...)`` from ``CHIYO_WORLD_HOME`` / ``HERMES_HOME`` (falling
    back to the state root), so the ledger's ``home_ref`` must be that same value -- otherwise
    the phone would be recording arrivals under a different home than the protocol it is
    feeding.  After that: an explicit instance field, then the world home the phone already
    binds, then the state root.  Every step is named, nothing is invented, and the caller never
    sees a client-supplied value.
    """

    env = {str(k): str(v) for k, v in (environment or {}).items()}
    world = str(env.get("CHIYO_WORLD_HOME") or env.get("HERMES_HOME") or "").strip()
    if world:
        return world, "environment:CHIYO_WORLD_HOME"
    field = str(instance.get("contact_home_ref") or "").strip()
    if field:
        return field, "instance:contact_home_ref"
    bound = str(instance.get("world_home") or "").strip()
    if bound:
        return bound, "instance:world_home"
    recorded = str(env.get("NYA02_LIFE_HOME") or "").strip()
    if recorded:
        return recorded, "environment:NYA02_LIFE_HOME"
    if root:
        return str(Path(root)), "instance:root"
    return "", ""


def session_path_for(state_root: Path | str) -> Path:
    """B's own co-presence session record, under the same Life state root."""

    return Path(state_root) / "co_presence.json"


class PhoneContact:
    """One instance's local-visit surface over A's inbox.  No store, no timer, no second writer."""

    def __init__(self, *, state_root: Path | str, subject: str, home_ref: str,
                 inbox: Optional[Any] = None, clock: Optional[Any] = None,
                 home_source: str = "", session_path: Optional[Path | str] = None) -> None:
        self.state_root = Path(state_root)
        self.subject = str(subject or "").strip()
        self.home_ref = str(home_ref or "").strip()
        self.home_source = str(home_source or "")
        self._clock = clock
        #: An injected inbox (tests / an outer composition).  ``None`` = build A's own over this
        #: instance's state root and identity -- never over a process-global guess.
        self._inbox = inbox
        self._session_path = (Path(session_path) if session_path is not None
                              else session_path_for(self.state_root))

    # -- binding -------------------------------------------------------------------------

    def _inbox_or_raise(self) -> Any:
        if self._inbox is not None:
            return self._inbox
        if not self.subject:
            raise PhoneContactUnavailable("CONTACT_SUBJECT_UNBOUND")
        if not self.home_ref:
            raise PhoneContactUnavailable("CONTACT_HOME_UNBOUND")
        self._inbox = LocalContactInbox(self.state_root, subject_id=self.subject,
                                        home_ref=self.home_ref, clock=self._clock)
        return self._inbox

    def describe(self) -> dict[str, Any]:
        return {
            "kind": "phone_contact",
            "schema_version": SCHEMA_CONTACT,
            "owner": "LocalContactInbox",
            "state_root_bound": bool(str(self.state_root)),
            "subject": self.subject,
            "home_bound": bool(self.home_ref),
            "home_source": self.home_source,
            "kinds": list(ALL_KINDS),
            "states": list(ALL_STATES),
            "outcomes": list(ALL_OUTCOMES),
            "channel": CHANNEL_LOCAL_INBOX,
            "source": SOURCE_LOCAL,
            "owns_no_store": True,
            "owns_transport_ledger": False,
            "owns_activity": False,
            "owns_relationship": False,
            "starts_second_writer": False,
            "external_send": False,
            "local_only": True,
            "note": LOCAL_ONLY_NOTE,
        }

    # -- the read-only session block ------------------------------------------------------

    def _session(self) -> dict[str, Any]:
        """B's ``co_presence.json``, projected to labels.  A pure read; absent is a real answer."""

        block: dict[str, Any] = {"state": "ABSENT", "readable": False, "open": False,
                                 "source": "life_co_presence_session", "path_bound": True}
        try:
            raw = self._session_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return block
        except OSError:
            block.update({"state": "UNREADABLE", "detail": "session record unreadable"})
            return block
        try:
            data = json.loads(raw)
        except ValueError:
            block.update({"state": "UNREADABLE", "detail": "session record is not JSON"})
            return block
        if not isinstance(data, Mapping):
            block.update({"state": "UNREADABLE", "detail": "session record is not an object"})
            return block
        state = _line(data.get("state"), 24) or "UNKNOWN"
        block.update({
            "state": state,
            "readable": True,
            "open": state not in SESSION_CLOSED_STATES,
            "schema_version": _line(data.get("schema_version"), 60),
            "invitation_id": _line(data.get("invitation_id"), 200),
            "scope": _line(data.get("scope"), MAX_CONTACT_SCOPE),
            "opened_at": _line(data.get("opened_at") or data.get("began_at"), 40),
            "joint_activity_id": _line(data.get("joint_activity_id"), 200),
            "practice_session_ref": _line(data.get("practice_session_ref"), 200),
            "practice_invitation_event_ref": _line(data.get("practice_invitation_event_ref"), 200),
            "ended_at": _line(data.get("ended_at"), 40),
            "end_reason": _line(data.get("end_reason"), 64),
        })
        return block

    # -- reads ----------------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """``GET /contact``: counts, the bounded event list, the session record.  Zero writes."""

        try:
            inbox = self._inbox_or_raise()
        except PhoneContactUnavailable as exc:
            return self._unavailable(exc.reason_code)
        except LocalContactError as exc:
            return self._unavailable(exc.reason_code, detail=exc.detail)
        counts = inbox.summary()
        counts_data = counts.get("data") if isinstance(counts.get("data"), Mapping) else {}
        listing = inbox.list_events(limit=MAX_CONTACT_LIST)
        ok = bool(counts.get("ok"))
        return {
            "ok": ok,
            "status": STATUS_OK if ok else STATUS_UNREADABLE,
            "reason_code": str(counts.get("reason_code") or "")[:64],
            # 账本读不出来不是 200：照实回 503，界面才敢写「读不到」而不是「没有来信」。
            "http_status": 200 if ok else 503,
            "data": {
                "counts": _counts(counts_data.get("counts")),
                "by_kind": _counts(counts_data.get("by_kind")),
                "event_count": _int_or_none(counts_data.get("event_count")) or 0,
                "capacity": _int_or_none(counts_data.get("capacity")) or MAX_CONTACT_EVENTS,
                "ledger_state": str(counts_data.get("ledger_state") or "UNKNOWN"),
                "events": self._rows(listing),
                "events_ok": bool(listing.get("ok")),
                "events_reason_code": str(listing.get("reason_code") or "")[:64],
                "truncated": bool((listing.get("data") or {}).get("truncated")),
                "session": self._session(),
                "state_root_bound": True,
                "subject": self.subject,
                "home_bound": bool(self.home_ref),
                "home_source": self.home_source,
                "channel": CHANNEL_LOCAL_INBOX,
                "source": SOURCE_LOCAL,
                "private_text_included": False,
                "external_send": False,
                "delivered_to_user": False,
                "note": LOCAL_ONLY_NOTE,
                "queued_note": "queued（已记下、还没处理）不等于她答应了；接纳与否由她自己的选择决定",
            },
        }

    def _rows(self, listing: Mapping[str, Any]) -> list[dict[str, Any]]:
        payload = listing.get("data") if isinstance(listing.get("data"), Mapping) else {}
        rows: list[dict[str, Any]] = []
        for raw in payload.get("events") or ():
            if not isinstance(raw, Mapping):
                continue
            rows.append(self._row(raw))
        return rows[:MAX_CONTACT_LIST]

    def _row(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        """One transport row as a *summary*: state, times, scope, lengths -- never the text."""

        row: dict[str, Any] = {
            "event_ref": _line(raw.get("event_ref"), 200),
            "seq": _int_or_none(raw.get("seq")),
            "event_kind": _line(raw.get("event_kind"), 40),
            "effect": _line(raw.get("effect"), 40),
            "processing_state": _line(raw.get("processing_state"), 24),
            "outcome": _line(raw.get("outcome"), 24),
            "outcome_reason_code": _line(raw.get("outcome_reason_code"), 64),
            "occurred_at": _line(raw.get("occurred_at"), 40),
            "received_at": _line(raw.get("received_at"), 40),
            "body_chars": _int_or_none(raw.get("body_chars")) or 0,
            "body_stored": bool(raw.get("body_stored")),
            "body_included": False,
            "scope": _line(raw.get("scope"), MAX_CONTACT_SCOPE),
            "expires_at": _line(raw.get("expires_at"), 40),
            "expired": bool(raw.get("expired")),
            "requires_engagement": raw.get("requires_engagement"),
            "invitation_id": _line(raw.get("invitation_id"), 200),
            "invitation_bound": bool(raw.get("invitation_bound")),
            "defer_condition": _line(raw.get("defer_condition"), MAX_CONTACT_SCOPE),
            "opportunity_ref": _line(raw.get("opportunity_ref"), 200),
            "session_ref": _line(raw.get("session_ref"), 200),
            "attempts": _int_or_none(raw.get("attempts")) or 0,
            "claimed_by": _line(raw.get("claimed_by"), 80),
            "revision": _int_or_none(raw.get("revision")),
            "pause_requested": False,
            "activity_started": False,
            "external_channel_sent": False,
        }
        return row

    def listing(self, *, limit: Optional[int] = None) -> dict[str, Any]:
        """``GET /contact/events``: bounded transport rows, no body text."""

        try:
            inbox = self._inbox_or_raise()
        except PhoneContactUnavailable as exc:
            return self._unavailable(exc.reason_code)
        except LocalContactError as exc:
            return self._unavailable(exc.reason_code, detail=exc.detail)
        cap = MAX_CONTACT_LIST if limit is None else max(1, min(int(limit), MAX_CONTACT_LIST))
        answer = inbox.list_events(limit=cap)
        if not answer.get("ok"):
            return self._from_owner(answer)
        payload = answer.get("data") if isinstance(answer.get("data"), Mapping) else {}
        return {
            "ok": True,
            "status": STATUS_OK,
            "reason_code": "",
            "data": {
                "events": self._rows(answer),
                "matched": _int_or_none(payload.get("matched")) or 0,
                "returned": _int_or_none(payload.get("returned")) or 0,
                "truncated": bool(payload.get("truncated")),
                "ledger_state": str(payload.get("ledger_state") or "UNKNOWN"),
                "private_text_included": False,
                "local_only": True,
                "note": LOCAL_ONLY_NOTE,
            },
        }

    def event(self, event_ref: str, *, include_body: bool = False) -> dict[str, Any]:
        """``GET /contact/events/<ref>``: one event.  The text is only here, and only on ask."""

        ref = str(event_ref or "").strip()
        if not ref:
            return {"ok": False, "status": STATUS_REJECTED,
                    "reason_code": "CONTACT_EVENT_REF_REQUIRED", "data": {}}
        try:
            inbox = self._inbox_or_raise()
        except PhoneContactUnavailable as exc:
            return self._unavailable(exc.reason_code)
        except LocalContactError as exc:
            return self._unavailable(exc.reason_code, detail=exc.detail)
        want_body = bool(include_body)
        answer = inbox.get_event(ref, include_body=want_body)
        if not answer.get("ok"):
            if str(answer.get("status")) == "absent":
                return {"ok": False, "status": STATUS_NOT_FOUND,
                        "reason_code": _line(answer.get("reason_code"), 64)
                        or "CONTACT_EVENT_NOT_FOUND",
                        "data": {"event_ref": ref,
                                 "ledger_state": _line(answer.get("ledger_state"), 20)}}
            return self._from_owner(answer)
        payload = answer.get("data") if isinstance(answer.get("data"), Mapping) else {}
        raw = payload.get("event") if isinstance(payload.get("event"), Mapping) else {}
        row = self._row(raw)
        detail: dict[str, Any] = {
            "event": row,
            "operation_key": _line(raw.get("operation_key"), 128),
            "source": _line(raw.get("source"), 40),
            "channel": _line(raw.get("channel"), 40),
            "source_ref": _line(raw.get("source_ref"), 200),
            "client_identity_ignored": bool(raw.get("client_identity_ignored")),
            "invitation": self._invitation(raw.get("invitation")),
            "end": self._end(raw.get("end")),
            "binding": self._binding(raw.get("binding")),
            "claim": self._claim(raw.get("claim")),
            "result": self._result(raw.get("result")),
            "results": [self._result(item) for item in (raw.get("results") or [])
                        if isinstance(item, Mapping)][:8],
            "body": _text(raw.get("body"), MAX_CONTACT_MESSAGE) if want_body else "",
            "body_included": bool(want_body and raw.get("body")),
            "private_text_included": bool(want_body and raw.get("body")),
            "local_only": True,
            "note": LOCAL_ONLY_NOTE,
        }
        return {"ok": True, "status": STATUS_OK, "reason_code": "", "data": detail}

    def _invitation(self, raw: Any) -> Optional[dict[str, Any]]:
        if not isinstance(raw, Mapping):
            return None
        return {
            "invitation_id": _line(raw.get("invitation_id"), 200),
            "scope": _line(raw.get("scope"), MAX_CONTACT_SCOPE),
            "occurred_at": _line(raw.get("occurred_at"), 40),
            "expires_at": _line(raw.get("expires_at"), 40),
            "requires_engagement": raw.get("requires_engagement"),
            "atomic": bool(raw.get("atomic")),
            "duration_seconds": _int_or_none(raw.get("duration_seconds")),
            "source_ref": _line(raw.get("source_ref"), 200),
        }

    def _end(self, raw: Any) -> Optional[dict[str, Any]]:
        if not isinstance(raw, Mapping):
            return None
        return {
            "invitation_id": _line(raw.get("invitation_id"), 200),
            "explicit_end": bool(raw.get("explicit_end")),
            "completion_evidence_ref": _line(raw.get("completion_evidence_ref"), 200),
            "reason": _line(raw.get("reason"), 64),
            "occurred_at": _line(raw.get("occurred_at"), 40),
        }

    def _binding(self, raw: Any) -> Optional[dict[str, Any]]:
        if not isinstance(raw, Mapping):
            return None
        return {
            "invitation_id": _line(raw.get("invitation_id"), 200),
            "invitation_event_ref": _line(raw.get("invitation_event_ref"), 200),
            # 提交那一刻是否在本账本里找到了那条邀请；bound 要等消费方 record 才算成立。
            "resolved_at_submit": bool(raw.get("resolved_at_submit")),
            "bound": bool(raw.get("bound")),
            "binding_source": _line(raw.get("binding_source"), 40),
            "bound_at": _line(raw.get("bound_at"), 40),
            "reason_code": _line(raw.get("reason_code"), 64),
        }

    def _claim(self, raw: Any) -> Optional[dict[str, Any]]:
        if not isinstance(raw, Mapping):
            return None
        return {
            "consumer": _line(raw.get("consumer"), 80),
            "claimed_at": _line(raw.get("claimed_at"), 40),
            "from_state": _line(raw.get("from_state"), 24),
        }

    def _result(self, raw: Any) -> Optional[dict[str, Any]]:
        if not isinstance(raw, Mapping):
            return None
        return {
            "outcome": _line(raw.get("outcome"), 24),
            "reason_code": _line(raw.get("reason_code"), 64),
            "detail": _text(raw.get("detail"), 400),
            "recorded_at": _line(raw.get("recorded_at"), 40),
            "consumer": _line(raw.get("consumer"), 80),
            "session_ref": _line(raw.get("session_ref"), 200),
            "invitation_ref": _line(raw.get("invitation_ref"), 200),
            "decision_ref": _line(raw.get("decision_ref"), 200),
            "defer_condition": _line(raw.get("defer_condition"), MAX_CONTACT_SCOPE),
            "opportunity_ref": _line(raw.get("opportunity_ref"), 200),
            "owner_refs": [_line(item, 200) for item in (raw.get("owner_refs") or [])][:8],
            # B's v0.4 content half records which practice artifact this event opened/produced.
            # It is an identity only: the work itself is read from A, never from here.
            "practice_session_ref": _line(raw.get("practice_session_ref"), 200),
            "practice_state": _line(raw.get("practice_state"), 24),
            "practice_joint_state": _line(raw.get("practice_joint_state"), 24),
            "terminal": bool(raw.get("terminal")),
            "retryable": bool(raw.get("retryable")),
        }

    def operation(self, operation_key: str) -> dict[str, Any]:
        """``GET /contact/operations/<key>``: reconcile one **original** key.  Pure read.

        ``status`` is A's own vocabulary, unchanged: ``absent`` (nothing landed under this key),
        ``applied`` (it landed; ``data.result`` is the recorded outcome or ``null``),
        ``unknown`` (it landed and the outcome is UNKNOWN -- reconcile with the same key),
        ``unreadable`` (the ledger cannot be read, so the answer is "unknown", never "not
        applied").  Nothing here retries, re-submits or mints a second key.
        """

        key = str(operation_key or "").strip()
        if not key:
            return {"ok": False, "status": STATUS_REJECTED,
                    "reason_code": "CONTACT_OPERATION_KEY_REQUIRED", "data": {}}
        try:
            inbox = self._inbox_or_raise()
        except PhoneContactUnavailable as exc:
            return self._unavailable(exc.reason_code, operation_key=key)
        except LocalContactError as exc:
            return self._unavailable(exc.reason_code, detail=exc.detail, operation_key=key)
        answer = inbox.operation(key)
        payload = answer.get("data") if isinstance(answer.get("data"), Mapping) else {}
        status = str(answer.get("status") or "")
        if status == STATUS_UNREADABLE:
            return {"ok": False, "status": STATUS_UNREADABLE,
                    "reason_code": _line(answer.get("reason_code"), 64) or "CONTACT_STATE_UNREADABLE",
                    "http_status": 503,
                    "data": {"operation_key": key, "records": [], "recorded": False,
                             "note": "只读对账：账本读不出来，结果仍然未知，原键保留"}}
        event = payload.get("event") if isinstance(payload.get("event"), Mapping) else None
        result = payload.get("result") if isinstance(payload.get("result"), Mapping) else None
        return {
            "ok": bool(answer.get("ok", True)),
            "status": status or STATUS_UNKNOWN,
            "reason_code": _line(answer.get("reason_code"), 64) if status == STATUS_UNKNOWN else "",
            # 对账本身读成功了就是 200：``unknown`` 是对这条键的**答案**（她给的结果未知），
            # 不是「这次读取还没完成」，所以不能落成 202。
            "http_status": 200,
            "data": {
                "operation_key": key,
                "kind": _line(payload.get("kind"), 24),
                "event_ref": _line(payload.get("event_ref"), 200),
                "event_state": _line(payload.get("event_state"), 24),
                "outcome": _line(payload.get("outcome"), 24),
                "event": self._row(event) if isinstance(event, Mapping) else None,
                "result": self._result(result),
                "ledger_state": _line(payload.get("ledger_state"), 20),
                "recorded": status != STATUS_ABSENT,
                "retryable": bool(answer.get("retryable")),
                "local_only": True,
                "note": "只读对账：不会用新键重投，也不会改写这条事件",
            },
        }

    # -- the one write --------------------------------------------------------------------

    def submit(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """``POST /contact/events``: one local arrival.  Never an acceptance, never an Activity."""

        try:
            inbox = self._inbox_or_raise()
        except PhoneContactUnavailable as exc:
            return self._unavailable(exc.reason_code)
        except LocalContactError as exc:
            return self._unavailable(exc.reason_code, detail=exc.detail)
        try:
            answer = inbox.submit(payload)
        except LocalContactError as exc:
            return {"ok": False, "status": STATUS_REJECTED, "reason_code": exc.reason_code,
                    "detail": exc.detail, "data": {}}
        status = str(answer.get("status") or "")
        if status == STATUS_UNREADABLE:
            return {"ok": False, "status": STATUS_UNREADABLE,
                    "reason_code": _line(answer.get("reason_code"), 64)
                    or "CONTACT_STATE_UNREADABLE",
                    "detail": _text(answer.get("detail"), 200),
                    "data": {"event_ref": None, "queued": False, "accepted": False}}
        data = answer.get("data") if isinstance(answer.get("data"), Mapping) else {}
        event = data.get("event") if isinstance(data.get("event"), Mapping) else None
        row = self._row(event) if isinstance(event, Mapping) else {}
        return {
            "ok": bool(answer.get("ok")),
            "status": status or STATUS_REJECTED,
            "reason_code": _line(answer.get("reason_code"), 64),
            "detail": _text(answer.get("detail"), 200),
            "data": {
                "event_ref": _line(answer.get("event_ref"), 200),
                "operation_key": _line(answer.get("operation_key"), 128),
                "event": row or None,
                "event_kind": row.get("event_kind"),
                "effect": _line(answer.get("effect"), 40),
                "processing_state": _line(answer.get("processing_state"), 24)
                or row.get("processing_state"),
                # 三个硬事实：这次只投递，没有暂停、没有起活动、没有被接纳。
                "queued": bool(answer.get("ok")) and row.get("processing_state") == STATE_PENDING,
                "accepted": False,
                "pause_requested": False,
                "activity_started": False,
                "owners_written": 0,
                "replayed": bool(answer.get("replayed")),
                "created": bool(answer.get("created")),
                "local_only": True,
                "note": LOCAL_ONLY_NOTE,
                "queued_note": "已记下，还没处理：这不是她答应了，也不是共同时间已经开始",
            },
        }

    # -- envelopes ------------------------------------------------------------------------

    def _unavailable(self, reason_code: str, *, detail: str = "",
                     **extra: Any) -> dict[str, Any]:
        data: dict[str, Any] = {"subject": self.subject, "home_bound": bool(self.home_ref),
                                "state_root_bound": True, "local_only": True}
        data.update(extra)
        out = {"ok": False, "status": STATUS_UNAVAILABLE,
               "reason_code": str(reason_code or "CONTACT_UNAVAILABLE")[:64], "data": data}
        if detail:
            out["detail"] = _text(detail, 200)
        return out

    def _from_owner(self, answer: Mapping[str, Any]) -> dict[str, Any]:
        """One owner refusal, mapped onto P's status vocabulary; its reason code is kept."""

        status = str(answer.get("status") or "")
        reason = _line(answer.get("reason_code"), 64) or "CONTACT_OWNER_REFUSED"
        mapped = {STATUS_UNREADABLE: STATUS_UNREADABLE,
                  STATUS_REJECTED: STATUS_REJECTED,
                  STATUS_UNKNOWN: STATUS_UNKNOWN}.get(status, STATUS_UNAVAILABLE)
        out = {"ok": False, "status": mapped, "reason_code": reason,
               # 读不出来的账本、不可用的 owner 都是 503；只有「按名拒绝」才是 409。
               "http_status": 409 if mapped == STATUS_REJECTED else 503,
               "data": {"ledger_state": _line((answer.get("data") or {}).get("ledger_state")
                                              if isinstance(answer.get("data"), Mapping) else "",
                                              20) or "UNKNOWN"}}
        if answer.get("detail"):
            out["detail"] = _text(answer.get("detail"), 200)
        return out


class PhoneContactUnavailable(RuntimeError):
    """This instance cannot serve the contact surface right now.  Never a fake empty inbox."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        super().__init__("%s:%s" % (reason_code, detail)[:200])
        self.reason_code = str(reason_code)
        self.detail = str(detail)[:200]


#: 号码化的状态/结果词表：界面只查这张表，不自己发明第四个状态。
STATE_TEXT = {
    STATE_PENDING: "已记下（还没处理）",
    STATE_CLAIMED: "她正在处理",
    STATE_DEFERRED: "延后了（有明确条件）",
    STATE_UNKNOWN: "她的答复未知",
    STATE_PROCESSED: "已处理完",
}
OUTCOME_TEXT = {
    OUTCOME_OBSERVED: "只是看到了",
    OUTCOME_ACCEPTED: "她接受了",
    OUTCOME_DECLINED: "她拒绝了",
    OUTCOME_DEFERRED: "她延后了",
    OUTCOME_ENDED: "这次共同时间结束了",
    OUTCOME_UNKNOWN: "结果未知",
}
KIND_TEXT = {
    KIND_MESSAGE: "普通来话",
    KIND_INVITATION: "明确邀请",
    KIND_END: "显式结束",
}

__all__ = [
    "ALL_KINDS",
    "ALL_OUTCOMES",
    "ALL_STATES",
    "CHANNEL_LOCAL_INBOX",
    "KIND_END_PHONE",
    "KIND_INVITATION_PHONE",
    "KIND_MESSAGE_PHONE",
    "KIND_TEXT",
    "LOCAL_ONLY_NOTE",
    "MAX_CONTACT_EVENTS",
    "MAX_CONTACT_LIST",
    "MAX_CONTACT_MESSAGE",
    "MAX_CONTACT_SCOPE",
    "MAX_CONTACT_SECONDS",
    "OUTCOME_TEXT",
    "PhoneContact",
    "PhoneContactUnavailable",
    "SCHEMA_CONTACT",
    "SESSION_FIELDS",
    "STATE_TEXT",
    "home_ref_for",
    "session_path_for",
]

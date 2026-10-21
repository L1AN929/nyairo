#!/usr/bin/env python3
"""v0.4 手机分享面（P 的窄门面）：只调 ``LifeSharingService`` 的四个文档化方法。

它是什么
--------
一部手机上「她愿意把这一段给谁看」的真实入口：草稿、延后、放弃、本地投递、来源当前性。
它**不是**第二个分享服务，也不拥有任何事实：

==========================  ==================================================
一份分享                    Life Supply ``ManagedArtifact``（经 B 的
                            ``private_project_service``；``LifeSharingService`` 里
                            「版本即状态」）
状态机 / CAS / 幂等键 / 收据  B 的 ``LifeSharingService``（本模块只转发）
可选的来源                  这台实例自己 ``list_projects()`` 读得到的作品，加上
                            它绑定的阅读材料（读不到的照实说读不到）
操作来源（用户/模型）        ``<root>/phone/operations.jsonl``（P 写，P 读）
==========================  ==================================================

它不是什么
----------
* 不是新的 store：没有 sqlite、没有 jsonl、没有进程内账本；``describe()`` 明写
  ``owns_no_store=True``。
* 不是外发渠道：``channel`` 只有 ``local_inbox``，受众只有 ``local:owner``；本文件里
  没有任何 telegram / HTTP / SMTP 代码路径。``LOCAL_DELIVERED`` 只是**本实例本地存档**，
  **不等于**用户已读、更不等于外部已送达（``LOCAL_ONLY_NOTE`` 每次都说这件事）。
* 不接受任意来源：来源必须是这台实例现在真的读得到的作品；路径、DB、URL、身份、受众、
  渠道地址一律不接收（server 层拒，这里再按 owner 事实核一次）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LIFE_SCRIPTS = _REPO_ROOT / "components" / "life" / "scripts"
for _entry in (str(_REPO_ROOT), str(_LIFE_SCRIPTS)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

#: B 的词表与边界：这里**照抄同一个常量**，不自己再定义一套（漂移就是两个真相源）。
from life_sharing_service import (  # noqa: E402
    ACTION_DEFER,
    ACTION_DROP,
    ACTION_SEND_LOCAL,
    AUDIENCE_LOCAL_OWNER,
    CHANNEL_LOCAL_INBOX,
    LOCAL_AUDIENCES,
    LOCAL_CHANNELS,
    MAX_CONDITION_CHARS,
    MAX_EXCERPT_CHARS,
    MAX_LIST,
    MAX_REASON_CHARS,
    MAX_SHARE_TEXT_CHARS,
    MAX_TITLE_CHARS,
    MIN_EXPIRY_S,
    MAX_EXPIRY_S,
    STATE_DEFERRED,
    STATE_DRAFT,
    STATE_DROPPED,
    STATE_EXPIRED,
    STATE_LOCAL_DELIVERED,
    SHARE_ACTIONS,
    SHARE_STATES,
    SharingContractRefused,
    SharingUnavailable,
    TERMINAL_STATES,
    is_share_title,
)
from life_sharing_service import LifeSharingService  # noqa: E402

#: P 只做这三件事；``expire`` 是 owner 侧的事实（读面推导 + A 明确落盘），手机不替她过期。
PHONE_SHARE_ACTIONS: tuple[str, ...] = (ACTION_SEND_LOCAL, ACTION_DEFER, ACTION_DROP)
#: 还能再做的动作：终态（DROPPED / EXPIRED）与已投递都没有入口，杜绝「重发」。
SENDABLE_STATES: tuple[str, ...] = (STATE_DRAFT, STATE_DEFERRED)

#: 一台手机上同时能看到的分享条目上限（与 B 的 ``MAX_LIST`` 同一件事）。
MAX_SHARES = MAX_LIST
#: 一条 owner id（``artifact:<32hex>``）的可见长度上限。
MAX_SHARE_ID = 200
MAX_SOURCE_ID = 200
MAX_SOURCE_TITLE = 120

#: 每一次读、写、收据都带着这句话：它是**事实声明**，不是界面文案。
LOCAL_ONLY_NOTE = "本实例本地存档，未向外部发送、未证明用户已读"

STATUS_OK = "ok"
STATUS_APPLIED = "applied"
STATUS_ALREADY = "already_applied"
STATUS_REJECTED = "rejected"
STATUS_CONFLICT = "conflict"
STATUS_NOT_FOUND = "not_found"
STATUS_UNKNOWN = "unknown"
STATUS_UNAVAILABLE = "unavailable"
STATUS_STALE_SOURCE = "stale_source"

#: B 的 status → P 的信封 status。``stale_source`` 原样保留（它是「来源换版」这条**独立**
#: 结论，不是普通 conflict，也不是 deleted）；只把 ``ok`` 读成 ``ok``。
_STATUS_MAP: dict[str, str] = {
    "ok": STATUS_OK,
    "applied": STATUS_APPLIED,
    "already_applied": STATUS_ALREADY,
    "rejected": STATUS_REJECTED,
    "conflict": STATUS_CONFLICT,
    "not_found": STATUS_NOT_FOUND,
    "unknown": STATUS_UNKNOWN,
    "unavailable": STATUS_UNAVAILABLE,
    "stale_source": STATUS_STALE_SOURCE,
}


def _status(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    return _STATUS_MAP.get(value, STATUS_UNAVAILABLE)


def _text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return value[:limit]


def _line(value: Any, limit: int) -> str:
    text = _text(value, limit)
    return " ".join(text.split())


def _one_line(value: Any, limit: int) -> str:
    return _line(value, limit)


def _int_or_none(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return int(value)


def _owner_block(answer: Mapping[str, Any]) -> dict[str, Any]:
    """Every answer carries the owner's own word and its reason code, verbatim."""

    block: dict[str, Any] = {"status": str(answer.get("status") or "")}
    if answer.get("reason_code"):
        block["reason_code"] = _line(answer.get("reason_code"), 64)
    if answer.get("detail"):
        block["detail"] = _text(answer.get("detail"), 200)
    return block


def _source_row(raw: Mapping[str, Any]) -> dict[str, Any]:
    """One recorded source binding: ids, version, revision and lengths -- never its text."""

    row: dict[str, Any] = {
        "artifact_id": _line(raw.get("artifact_id") or raw.get("source_artifact_id"), MAX_SOURCE_ID),
        "version_id": _line(raw.get("version_id") or raw.get("source_version_id"), MAX_SOURCE_ID),
    }
    revision = _int_or_none(raw.get("revision") if "revision" in raw else raw.get("source_revision"))
    if revision is not None:
        row["revision"] = revision
    for key in ("content_chars", "title_chars"):
        value = _int_or_none(raw.get(key) if key in raw else raw.get("source_%s" % key))
        if value is not None:
            row[key] = value
    return row


def _source_current(raw: Mapping[str, Any]) -> dict[str, Any]:
    """The owner's own answer about the source *now*: state, readability, staleness, reason."""

    block: dict[str, Any] = {}
    for key in ("state", "lifecycle", "reason_code", "owner_status", "owner_reason_code"):
        value = _line(raw.get(key), 80)
        if value:
            block[key] = value
    for key in ("readable", "governed", "matches_recorded", "stale"):
        if isinstance(raw.get(key), bool):
            block[key] = bool(raw[key])
    head = _line(raw.get("head_version_id"), MAX_SOURCE_ID)
    if head:
        block["head_version_id"] = head
    return block


def _receipt(raw: Any) -> Optional[dict[str, Any]]:
    """The local delivery record, verbatim enough to be checked -- and always local-only."""

    if not isinstance(raw, Mapping):
        return None
    receipt: dict[str, Any] = {
        "delivery_id": _line(raw.get("delivery_id"), 80),
        "delivered_at": _line(raw.get("delivered_at"), 40),
        "channel": _line(raw.get("channel"), 40),
        "audience": _line(raw.get("audience"), 40),
        "recipient": _line(raw.get("recipient"), 40),
        "note": _line(raw.get("note"), 200) or LOCAL_ONLY_NOTE,
    }
    for key in ("local_only", "external_channel_sent", "external_delivery_confirmed",
                "read_by_audience", "acknowledged", "understood"):
        if isinstance(raw.get(key), bool):
            receipt[key] = bool(raw[key])
    # 两条硬事实不靠 owner 记得写：读面永远明说这不是外发、也不是已读。
    receipt.setdefault("local_only", True)
    receipt.setdefault("external_channel_sent", False)
    receipt.setdefault("read_by_audience", False)
    return receipt


def _lifecycle_blocks(row: Mapping[str, Any]) -> dict[str, Any]:
    blocks: dict[str, Any] = {}
    deferred = row.get("deferred")
    if isinstance(deferred, Mapping):
        blocks["deferred"] = {
            "condition": _one_line(deferred.get("condition"), MAX_CONDITION_CHARS),
            "opportunity_ref": _one_line(deferred.get("opportunity_ref"), MAX_CONDITION_CHARS),
            "deferred_at": _line(deferred.get("deferred_at"), 40),
        }
    elif isinstance(row.get("defer_condition"), str) or isinstance(row.get("opportunity_ref"), str):
        blocks["deferred"] = {
            "condition": _one_line(row.get("defer_condition"), MAX_CONDITION_CHARS),
            "opportunity_ref": _one_line(row.get("opportunity_ref"), MAX_CONDITION_CHARS),
            "deferred_at": _line(row.get("deferred_at"), 40),
        }
    for key in ("dropped", "expired"):
        block = row.get(key)
        if isinstance(block, Mapping):
            blocks[key] = {"reason": _one_line(block.get("reason"), MAX_REASON_CHARS),
                           "at": _line(block.get("%s_at" % key) or block.get("at"), 40)}
    return blocks


class PhoneSharing:
    """One instance's share surface over B's service.  No store, no timer, no send path."""

    def __init__(self, projects: Any, *, subject: str = "", workspace_id: str = "",
                 reading_artifact_id: str = "", clock: Optional[Any] = None) -> None:
        self.service = LifeSharingService(projects, clock=clock)
        self.projects = projects
        self.subject = str(subject or getattr(projects, "subject_id", "") or "")
        self.workspace_id = str(workspace_id or getattr(projects, "workspace_id", "") or "")
        self.reading_artifact_id = str(reading_artifact_id or "")

    # -- introspection -------------------------------------------------------------------

    def describe(self) -> dict[str, Any]:
        return {
            "kind": "phone_sharing",
            "source": "LifeSharingService",
            "states": list(SHARE_STATES),
            "actions": list(PHONE_SHARE_ACTIONS),
            "audiences": list(LOCAL_AUDIENCES),
            "channels": list(LOCAL_CHANNELS),
            "subject_id": self.subject,
            "workspace_id": self.workspace_id,
            "owns_no_store": True,
            "external_send": False,
            "local_only": True,
            "note": LOCAL_ONLY_NOTE,
        }

    # -- reads ---------------------------------------------------------------------------

    def listing(self, *, states: Optional[Iterable[str]] = None,
                limit: Optional[int] = None) -> dict[str, Any]:
        """Every share the owner holds, newest first.  **Never** carries the share text.

        The owner is asked with ``include_text=True`` only so that each row can report *source
        currency* (``text_withheld`` / ``text_available`` / ``source_state`` / ``withheld_count``
        are only computed on that path).  This method then drops the text from every row: the
        detail read is the single place a bounded share text is ever projected.  So the list
        answer really contains no text (``private_text_included=False`` is a fact about this
        payload, not a copy of the request), and a refused source still cannot leak through it.
        """

        try:
            answer = self.service.list_shares(states=states, include_text=True, limit=limit)
        except SharingContractRefused as exc:
            return {"ok": False, "status": STATUS_REJECTED, "reason_code": "SHARE_STATES_INVALID",
                    "detail": _text(exc, 120), "data": {}}
        except SharingUnavailable as exc:
            return {"ok": False, "status": STATUS_UNAVAILABLE,
                    "reason_code": _line(exc, 64) or "SHARES_UNAVAILABLE", "data": {}}
        status = _status(answer.get("status"))
        payload = answer.get("data") if isinstance(answer.get("data"), Mapping) else {}
        rows: list[dict[str, Any]] = []
        for raw in payload.get("shares") or ():
            if not isinstance(raw, Mapping):
                continue
            row: dict[str, Any] = {
                "share_id": _line(raw.get("share_id"), MAX_SHARE_ID),
                "state": _line(raw.get("state"), 30),
                "state_derived": bool(raw.get("state_derived")),
                "title": _line(raw.get("title"), MAX_SOURCE_TITLE),
                "share_revision": _int_or_none(raw.get("share_revision")),
                "head_version_id": _line(raw.get("head_version_id"), MAX_SOURCE_ID),
                "updated_at": _line(raw.get("updated_at"), 40),
                "lifecycle": _line(raw.get("lifecycle"), 20),
                "audience": _line(raw.get("audience"), 40),
                "channel": _line(raw.get("channel"), 40),
                "has_receipt": bool(raw.get("has_receipt")),
                "delivered": bool(raw.get("delivered")),
                "text_chars": _int_or_none(raw.get("text_chars")),
                "text_withheld": bool(raw.get("text_withheld")),
                "text_available": bool(raw.get("text_available", True)),
                "source_state": _line(raw.get("source_state"), 40),
                "source_reason_code": _line(raw.get("source_reason_code"), 64),
                "source": _source_row(raw.get("source") if isinstance(raw.get("source"), Mapping)
                                      else {}),
                "sendable": str(raw.get("state")) in SENDABLE_STATES,
                "actions": [action for action in PHONE_SHARE_ACTIONS
                            if str(raw.get("state")) in SENDABLE_STATES],
            }
            if not row["share_id"]:
                continue
            rows.append(row)
        counts = dict(payload.get("counts") or {})
        inbox = payload.get("inbox") if isinstance(payload.get("inbox"), Mapping) else {}
        data: dict[str, Any] = {
            "shares": rows[:MAX_SHARES],
            "counts": {str(key): _int_or_none(value) or 0 for key, value in counts.items()},
            "truncated": bool(payload.get("truncated")),
            "ignored": [dict(row) for row in (payload.get("ignored") or [])
                        if isinstance(row, Mapping)][:MAX_SHARES],
            "inbox": {"channel": _line(inbox.get("channel"), 40) or CHANNEL_LOCAL_INBOX,
                      "audience": _line(inbox.get("audience"), 40) or AUDIENCE_LOCAL_OWNER,
                      "delivered_count": _int_or_none(inbox.get("delivered_count")) or 0,
                      "local_only": True},
            "withheld_count": _int_or_none(payload.get("withheld_count")) or 0,
            # 列表这一面**没有**正文：它照实说「没有投影任何正文」。
            "private_text_included": False,
            "text_withheld": bool(payload.get("text_withheld")),
            "local_only": True,
            "external_send": False,
            "note": LOCAL_ONLY_NOTE,
            "owner": _owner_block(answer),
        }
        for key in ("failed_artifact_id",):
            if payload.get(key):
                data[key] = _line(payload[key], MAX_SHARE_ID)
        return {"ok": bool(answer.get("ok")), "status": status,
                "reason_code": _line(answer.get("reason_code"), 64),
                "detail": _text(answer.get("detail"), 200), "data": data}

    def detail(self, share_id: str) -> dict[str, Any]:
        """One share as the owner holds it now, with its bounded text **only** while readable."""

        try:
            answer = self.service.read_share(share_id, include_text=True)
        except SharingContractRefused as exc:
            return {"ok": False, "status": STATUS_REJECTED, "reason_code": "SHARE_ID_INVALID",
                    "detail": _text(exc, 120), "data": {}}
        except SharingUnavailable as exc:
            return {"ok": False, "status": STATUS_UNAVAILABLE,
                    "reason_code": _line(exc, 64) or "SHARES_UNAVAILABLE", "data": {}}
        status = _status(answer.get("status"))
        payload = answer.get("data") if isinstance(answer.get("data"), Mapping) else {}
        share = payload.get("share") if isinstance(payload.get("share"), Mapping) else None
        if share is None:
            return {"ok": False, "status": status,
                    "reason_code": _line(answer.get("reason_code"), 64) or "SHARE_NOT_FOUND",
                    "detail": _text(answer.get("detail"), 200), "data": {}}
        state = _line(share.get("state"), 30)
        receipt = _receipt(payload.get("receipt"))
        data: dict[str, Any] = {
            "share": {
                "share_id": _line(share.get("share_id"), MAX_SHARE_ID),
                "state": state,
                "state_derived": bool(share.get("state_derived")),
                "title": _line(share.get("title"), MAX_SOURCE_TITLE),
                "audience": _line(share.get("audience"), 40),
                "channel": _line(share.get("channel"), 40),
                "head_version_id": _line(share.get("head_version_id"), MAX_SOURCE_ID),
                "version_number": _int_or_none(share.get("version_number")),
                "share_revision": _int_or_none(share.get("share_revision")),
                "artifact_revision": _int_or_none(share.get("artifact_revision")),
                "created_at": _line(share.get("created_at"), 40),
                "updated_at": _line(share.get("updated_at"), 40),
                "expires_at": _line(share.get("expires_at"), 40),
                "text": _text(share.get("text"), MAX_SHARE_TEXT_CHARS),
                "text_chars": _int_or_none(share.get("text_chars")),
                "text_withheld": bool(share.get("text_withheld")),
                "text_available": bool(share.get("text_available", True)),
                "source": _source_row(share.get("source") if isinstance(share.get("source"),
                                                                        Mapping) else {}),
                "sendable": state in SENDABLE_STATES,
                "terminal": state in TERMINAL_STATES,
                "actions": [action for action in PHONE_SHARE_ACTIONS
                            if state in SENDABLE_STATES],
            },
            "source_current": _source_current(payload.get("source_current")
                                              if isinstance(payload.get("source_current"), Mapping)
                                              else {}),
            "source_readable": bool(payload.get("source_readable")),
            "receipt": receipt,
            "delivered": bool(payload.get("delivered")),
            "text_withheld": bool(payload.get("text_withheld")),
            "withheld_reason_code": _line(payload.get("withheld_reason_code"), 64),
            "private_text_included": bool(payload.get("private_text_included")),
            "local_only": True,
            "external_send": False,
            "note": LOCAL_ONLY_NOTE,
            "owner": _owner_block(answer),
        }
        data["share"].update(_lifecycle_blocks(share))
        return {"ok": bool(answer.get("ok")), "status": status,
                "reason_code": _line(answer.get("reason_code"), 64),
                "detail": _text(answer.get("detail"), 200), "data": data}

    # -- selectable sources --------------------------------------------------------------

    def sources(self, *, limit: Optional[int] = None) -> dict[str, Any]:
        """Which artifacts this instance may really offer as a source, right now.

        Only two kinds are offered: her own readable projects, plus the reading material this
        instance is bound to.  A share artifact is never a source (it is a derived thing), and a
        path / URL / DB / foreign id is never one either.  Every row is checked against the owner
        before it is offered; a refused read is listed with its reason, never dropped silently and
        never shown as readable.
        """

        cap = MAX_SHARES if limit is None else max(1, min(int(limit), MAX_SHARES))
        try:
            answer = self.projects.list_projects()
        except Exception as exc:  # a broken owner is not "no sources"
            return {"ok": False, "status": STATUS_UNAVAILABLE,
                    "reason_code": "SHARES_PROJECTS_UNAVAILABLE",
                    "detail": type(exc).__name__, "data": {"sources": []}}
        if not isinstance(answer, Mapping) or not answer.get("ok"):
            status = _status(answer.get("status") if isinstance(answer, Mapping) else "")
            return {"ok": False,
                    "status": status if status != STATUS_OK else STATUS_UNAVAILABLE,
                    "reason_code": _line((answer or {}).get("reason_code") if isinstance(answer, Mapping)
                                         else "", 64) or "SHARES_PROJECTS_UNAVAILABLE",
                    "detail": _text((answer or {}).get("detail") if isinstance(answer, Mapping) else "",
                                    200),
                    "data": {"sources": []}}
        rows: list[dict[str, Any]] = []
        for raw in answer.get("projects") or ():
            if not isinstance(raw, Mapping):
                continue
            artifact_id = _line(raw.get("artifact_id"), MAX_SOURCE_ID)
            if not artifact_id or is_share_title(raw.get("title")):
                # 分享件自己是派生物，永远不是来源。
                continue
            rows.append({
                "artifact_id": artifact_id,
                "title": _line(raw.get("title"), MAX_SOURCE_TITLE),
                # 阅读材料也是她真实的作品之一：按自己的 kind 列出，不被当成普通作品，
                # 也不因为「不是项目」就被藏起来。
                "kind": "reading" if artifact_id == self.reading_artifact_id else "project",
                "lifecycle": _line(raw.get("lifecycle"), 20),
                "revision": _int_or_none(raw.get("revision")),
                "head_version_id": _line(raw.get("head_version_id"), MAX_SOURCE_ID),
                "readable": False,
                "reason_code": "",
            })
            if len(rows) >= cap:
                break
        known = {row["artifact_id"] for row in rows}
        if self.reading_artifact_id and self.reading_artifact_id not in known and len(rows) < cap:
            # 阅读绑定不在作品列表里（例如刚被归档）时，单独按它的 id 问一次 owner。
            reading = self._reading_source()
            if reading is not None:
                rows.append(reading)
        for row in rows:
            self._probe(row)
        return {"ok": True, "status": STATUS_OK,
                "data": {"sources": rows, "count": len(rows), "truncated": len(rows) >= cap,
                         "kinds": ["project", "reading"],
                         "audiences": list(LOCAL_AUDIENCES),
                         "channels": list(LOCAL_CHANNELS),
                         "note": LOCAL_ONLY_NOTE,
                         "private_text_included": False}}

    def _reading_source(self) -> Optional[dict[str, Any]]:
        """The reading material this instance is bound to, checked against the owner."""

        if not self.reading_artifact_id:
            return None
        row: dict[str, Any] = {"artifact_id": self.reading_artifact_id, "title": "", "kind": "reading",
                               "lifecycle": "", "revision": None, "head_version_id": "",
                               "readable": False, "reason_code": ""}
        try:
            answer = self.projects.read_project(self.reading_artifact_id, include_history=False)
        except Exception:
            row["reason_code"] = "SOURCE_UNREADABLE"
            return row
        if isinstance(answer, Mapping) and answer.get("ok"):
            project = answer.get("project") if isinstance(answer.get("project"), Mapping) else {}
            row["readable"] = True
            row["title"] = _line(project.get("title"), MAX_SOURCE_TITLE)
            row["lifecycle"] = _line(project.get("lifecycle"), 20)
            row["revision"] = _int_or_none(project.get("revision"))
            row["head_version_id"] = _line(project.get("head_version_id"), MAX_SOURCE_ID)
            return row
        row["reason_code"] = _line((answer or {}).get("reason_code") if isinstance(answer, Mapping)
                                   else "", 64) or "SOURCE_UNREADABLE"
        row["readable"] = False
        return row

    def _probe(self, row: dict[str, Any]) -> None:
        """Ask the owner whether this row is readable *now*; record its reason when it is not."""

        try:
            answer = self.projects.read_project(row["artifact_id"], include_history=False)
        except Exception:
            row["readable"] = False
            row["reason_code"] = "SOURCE_UNREADABLE"
            return
        if isinstance(answer, Mapping) and answer.get("ok"):
            project = answer.get("project") if isinstance(answer.get("project"), Mapping) else {}
            row["readable"] = True
            row["lifecycle"] = _line(project.get("lifecycle"), 20) or row["lifecycle"]
            row["head_version_id"] = (_line(project.get("head_version_id"), MAX_SOURCE_ID)
                                      or row["head_version_id"])
            revision = _int_or_none(project.get("revision"))
            if revision is not None:
                row["revision"] = revision
            if not row["title"]:
                row["title"] = _line(project.get("title"), MAX_SOURCE_TITLE)
            return
        row["readable"] = False
        row["reason_code"] = _line((answer or {}).get("reason_code") if isinstance(answer, Mapping)
                                   else "", 64) or "SOURCE_UNREADABLE"

    # -- writes --------------------------------------------------------------------------

    def prepare(self, *, source_artifact_id: str, share_text: Optional[str] = None,
                excerpt_chars: Optional[int] = None, title: Optional[str] = None,
                expires_after_s: Optional[int] = None,
                operation_key: Optional[str] = None) -> dict[str, Any]:
        """Form one private draft from a source this instance really read, with explicit text.

        The audience and the channel are this module's own fixed constants (``local:owner`` /
        ``local_inbox``): a client never names them, so a draft can never be addressed anywhere.
        """

        offered = self._source_index()
        if offered is None:
            return {"ok": False, "status": STATUS_UNAVAILABLE,
                    "reason_code": "SHARES_PROJECTS_UNAVAILABLE", "data": {}}
        if source_artifact_id not in offered:
            return {"ok": False, "status": STATUS_REJECTED,
                    "reason_code": "SHARE_SOURCE_NOT_OFFERED",
                    "detail": "来源不在这台实例现在真的读得到的作品/阅读绑定里",
                    "data": {"source_artifact_id": source_artifact_id}}
        row = offered[source_artifact_id]
        if not row.get("readable"):
            return {"ok": False, "status": STATUS_REJECTED,
                    "reason_code": _line(row.get("reason_code"), 64) or "SOURCE_UNREADABLE",
                    "detail": "来源现在读不到，不能拿它做分享",
                    "data": {"source_artifact_id": source_artifact_id,
                             "source_state": _line(row.get("reason_code"), 64) or "UNAVAILABLE"}}
        try:
            answer = self.service.prepare_share(
                source_artifact_id=source_artifact_id,
                share_text=share_text,
                excerpt_chars=excerpt_chars,
                title=title,
                audience=AUDIENCE_LOCAL_OWNER,
                channel=CHANNEL_LOCAL_INBOX,
                expires_after_s=expires_after_s,
                operation_key=operation_key,
            )
        except SharingContractRefused as exc:
            return {"ok": False, "status": STATUS_REJECTED, "reason_code": "SHARE_TEXT_INVALID",
                    "detail": _text(exc, 160), "data": {}}
        except SharingUnavailable as exc:
            return {"ok": False, "status": STATUS_UNAVAILABLE,
                    "reason_code": _line(exc, 64) or "SHARES_UNAVAILABLE", "data": {}}
        return self._write_answer(answer, "prepare")

    def act(self, *, share_id: str, action: str, expected_head: str,
            operation_key: Optional[str] = None, defer_condition: Optional[str] = None,
            opportunity_ref: Optional[str] = None, reason: Optional[str] = None) -> dict[str, Any]:
        """One explicit action on one draft: ``send_local`` / ``defer`` / ``drop``."""

        if action not in PHONE_SHARE_ACTIONS:
            return {"ok": False, "status": STATUS_REJECTED, "reason_code": "SHARE_ACTION_REFUSED",
                    "detail": "手机只做 send_local / defer / drop", "data": {}}
        try:
            answer = self.service.apply_action(
                share_id=share_id,
                action=action,
                expected_head=expected_head,
                operation_key=operation_key,
                defer_condition=defer_condition,
                opportunity_ref=opportunity_ref,
                reason=reason,
            )
        except SharingContractRefused as exc:
            return {"ok": False, "status": STATUS_REJECTED,
                    "reason_code": "SHARE_ACTION_REFUSED", "detail": _text(exc, 160), "data": {}}
        except SharingUnavailable as exc:
            return {"ok": False, "status": STATUS_UNAVAILABLE,
                    "reason_code": _line(exc, 64) or "SHARES_UNAVAILABLE", "data": {}}
        return self._write_answer(answer, action)

    # -- shared plumbing -----------------------------------------------------------------

    def _source_index(self) -> Optional[dict[str, dict[str, Any]]]:
        listing = self.sources()
        if not listing.get("ok"):
            return None
        return {str(row.get("artifact_id")): row for row in listing["data"].get("sources") or ()}

    def _write_answer(self, answer: Mapping[str, Any], action: str) -> dict[str, Any]:
        """Map one owner write answer onto the phone envelope, keeping the owner's own words."""

        status = _status(answer.get("status"))
        payload = answer.get("data") if isinstance(answer.get("data"), Mapping) else {}
        data: dict[str, Any] = {"action": action}
        for key in ("share_id", "state", "previous_state", "state_derived", "head_version_id",
                    "current_head_version_id", "share_revision", "artifact_revision",
                    "operation_key", "delivery_count", "title", "audience", "channel",
                    "expires_at", "created_at", "updated_at", "excerpt", "text_chars",
                    "source_state", "governed"):
            if key in payload:
                value = payload[key]
                if isinstance(value, Mapping):
                    data[key] = dict(value)
                elif isinstance(value, (int, bool)) or value is None:
                    data[key] = value
                else:
                    data[key] = _line(value, MAX_SOURCE_ID)
        if isinstance(payload.get("source"), Mapping):
            data["source"] = _source_row(payload["source"])
        if isinstance(payload.get("source_current"), Mapping):
            data["source_current"] = _source_current(payload["source_current"])
        receipt = _receipt(payload.get("receipt"))
        if receipt is not None:
            data["receipt"] = receipt
        if isinstance(payload.get("deferred"), Mapping) or isinstance(payload.get("dropped"),
                                                                     Mapping):
            data.update(_lifecycle_blocks(payload))
        data["reconciled"] = bool(answer.get("reconciled"))
        if answer.get("retryable") is not None:
            data["retryable"] = bool(answer.get("retryable"))
        # 这一次真的落地了吗？只有 ok + applied/already_applied + LOCAL_DELIVERED 才叫「已投递」。
        state = str(data.get("state") or "")
        data["delivered"] = bool(answer.get("ok") and status in (STATUS_APPLIED, STATUS_ALREADY)
                                 and state == STATE_LOCAL_DELIVERED)
        data["state_delivered"] = state == STATE_LOCAL_DELIVERED
        data["local_only"] = True
        data["external_send"] = False
        data["note"] = LOCAL_ONLY_NOTE
        data["owner"] = _owner_block(answer)
        if action == "prepare" and "text" in payload:
            # 她自己写的那句话在她自己的草稿回执里：有界、只在显式 prepare 的答案里。
            data["text"] = _text(payload.get("text"), MAX_SHARE_TEXT_CHARS)
            data["private_text_included"] = bool(payload.get("private_text_included"))
        return {"ok": bool(answer.get("ok")), "status": status,
                "reason_code": _line(answer.get("reason_code"), 64),
                "detail": _text(answer.get("detail"), 200), "data": data}


__all__ = [
    "AUDIENCE_LOCAL_OWNER",
    "CHANNEL_LOCAL_INBOX",
    "LOCAL_AUDIENCES",
    "LOCAL_CHANNELS",
    "LOCAL_ONLY_NOTE",
    "MAX_CONDITION_CHARS",
    "MAX_EXCERPT_CHARS",
    "MAX_EXPIRY_S",
    "MAX_LIST",
    "MAX_REASON_CHARS",
    "MAX_SHARE_ID",
    "MAX_SHARE_TEXT_CHARS",
    "MAX_SHARES",
    "MAX_SOURCE_ID",
    "MAX_SOURCE_TITLE",
    "MAX_TITLE_CHARS",
    "MIN_EXPIRY_S",
    "PHONE_SHARE_ACTIONS",
    "PhoneSharing",
    "SENDABLE_STATES",
    "SHARE_ACTIONS",
    "SHARE_STATES",
    "STATUS_ALREADY",
    "STATUS_APPLIED",
    "STATUS_CONFLICT",
    "STATUS_NOT_FOUND",
    "STATUS_OK",
    "STATUS_REJECTED",
    "STATUS_STALE_SOURCE",
    "STATUS_UNAVAILABLE",
    "STATUS_UNKNOWN",
    "STATE_DEFERRED",
    "STATE_DRAFT",
    "STATE_DROPPED",
    "STATE_EXPIRED",
    "STATE_LOCAL_DELIVERED",
    "TERMINAL_STATES",
    "is_share_title",
]

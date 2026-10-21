#!/usr/bin/env python3
"""v0.4 手机共同实践面（P 的窄门面）：只转发用户自己的输入与用户自己的贡献。

它是什么
--------
一部手机上「围绕一份明确共享的材料，和她在同一件共同作品上做几次来回」的真实入口：
选一份**拥有共享资格**的材料开成一次共同实践、发一句普通的话、改自己已经提交的那一段、
暂停、显式结束、重载后读回真实停点。

==========================  ==========================================================
一次共同实践 / 它的版本      A 的 ``shared_practice_service``（这是 A 独占的 owner；
                            手机这边**没有**第二个作品库、没有聊天库、没有关系库）
共同时间会话（只读标签）      B 的 ``co_presence.json``（本模块只读几个标签，不投影正文）
操作来源（用户/模型）        ``<root>/phone/operations.jsonl``（P 写，P 读；与其它面同一条）
==========================  ==========================================================

它不是什么
----------
* **不是第二个 Life writer**：没有 daemon、没有线程、没有 tick、没有定时器。构造这个对象
  不产生任何后台对象，也不驱动她的活动。
* **不是主体的代言人**：本模块里**没有** ``submit_subject_response`` /
  ``submit_subject_contribution`` / ``commit_work_text`` / ``resume_practice`` /
  ``reopen_practice`` 这些调用路径（它们是 A 的 Life 侧、要绑经核对的来源），也**没有**任何
  一条路径会替她写「她的贡献」。用户的点击只提交**用户自己的**那一份。
* **不是 Activity / 关系 owner**：这里没有 Activity 状态、没有关系分、没有第二把 pause 开关。
  ``pause`` / ``end`` 都只是**用户意图的转发**，能不能成立由 A 判定。
* **不猜 A 的接口**：直接对 A 已发布的方法名（``open_practice`` / ``list_practices`` /
  ``read_practice`` / ``submit_user_contribution`` / ``pause_practice`` / ``end_practice`` /
  ``context_view``）按**具名关键字**调用，并在调用前用 ``inspect.signature`` 核一遍真实签名：
  缺哪一项就报 ``SHARED_PRACTICE_INTERFACE_MISMATCH`` 并列出缺口——**核不过就不调用**，
  绝不用 ``**kwargs`` 去试。
* **不是外发渠道**：没有 telegram / HTTP / SMTP 代码路径；``delivered_to_user`` 恒为 False。

诚实的三条分界
--------------
1. **请求 ≠ 排队 ≠ 决定 ≠ 回应 ≠ 已提交作品**。这一层能给出的最强结论是「这次用户输入已经
   到达 A 的 owner」（``submitted``），以及 A 自己回的 status/reason_code 原样透出。
   A 没有排队概念：没有 Life 在跑的时候，用户这一句只是记在材料里，``queued`` 恒为 False。
2. **``accepted`` 只照抄 A 的答复**。A 只有在存在**经核对来源**的主体回应时才说
   ``accepted=true``；手机自己永远不写这个判断（``accepted_written_by_phone`` 恒为 False）。
3. **GET 是纯读**。``summary`` / ``work`` / ``operation`` 不写任何文件，不迁移，不初始化。

A 的接口与本模块的映射（名字错一个就具名拒绝，不静默降级）
----------------------------------------------------------
=====================================  =============================================
手机这一层                            A 的 ``SharedPracticeService``（caller=user）
=====================================  =============================================
``open_practice``                      ``open_practice(title=, opening_text=,
                                       material_refs=, operation_key=)``
``summary``                            ``list_practices(include_ended=, limit=)``
``work``                               ``read_practice(session_id=, include_history=,
                                       include_material_excerpt=, entry_limit=)``
``submit`` / ``modify``                ``submit_user_contribution(session_id=, text=,
                                       expected_head=, operation_key=)``
``pause``                              ``pause_practice(session_id=, note=,
                                       expected_head=, operation_key=)``
``end``                                ``end_practice(session_id=, reason=,
                                       expected_head=, operation_key=)``
=====================================  =============================================

两条 A 的语义被照实说给界面，而不是由手机补一个假的：

* **恢复 / 重开是她那一半**。A 的 ``resume_practice`` / ``reopen_practice`` 只允许 Life 侧
  调用且必须绑经核对的来源，所以 ``PHONE_ACTIONS`` 里**没有** ``resume``：客户端发
  ``resume`` 会被 server 按名拒绝，界面也不会摆一个替她按的按钮。
* **A 没有「原地改写用户贡献」这个动作**。``modify`` 在这一层是**用户的下一次发言**
  （A 的贡献是不可变历史：旧的那一条留在历史里，新的这一条是「我这一段现在的样子」），
  ``data.edit_kind = "new_turn"``、``data.history_kept = True`` 每次都照实说明。

来源与身份（不猜）
------------------
``state_root`` / ``subject`` / ``workspace_id`` 全部由**实例**构造（``service.py`` 里绑一次），
客户端带来的 ``uid`` / ``home`` / ``state_root`` / ``path`` / ``url`` / ``channel`` /
``workspace_id`` / ``owner`` 在 server 层按名拒绝。选材只有一个入口：``source_artifact_id``
（且必须出现在 owner 现在真的读得到的来源列表里），永远不是任意 path / 任意 artifact id。
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
_LIFE_SCRIPTS = REPO_ROOT / "components" / "life" / "scripts"
for _entry in (str(REPO_ROOT), str(_LIFE_SCRIPTS)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

SCHEMA_SHARED_PRACTICE = "phone.shared_practice.v1"
SCHEMA_SHARED_PRACTICE_INTERFACE = "phone.shared_practice.interface.v1"

STATUS_OK = "ok"
STATUS_APPLIED = "applied"
STATUS_ALREADY = "already_applied"
STATUS_REJECTED = "rejected"
STATUS_CONFLICT = "conflict"
STATUS_NOT_FOUND = "not_found"
STATUS_UNKNOWN = "unknown"
STATUS_UNAVAILABLE = "unavailable"
STATUS_STALE_SOURCE = "stale_source"

#: 手机**只**做这四件事。每一条都是「用户自己的」意图，没有一条代表她。
#: ``resume`` / ``reopen`` 不在这里：A 的 ``ACTOR_WRITES`` 把它们只留给 Life 侧，
#: 手机摆一个按钮就是把「她重新取用」演成了用户的动作。
PHONE_ACTIONS: tuple[str, ...] = ("submit", "modify", "pause", "end")
ACTION_TEXT = {
    "open": "邀请她一起做这一次",
    "submit": "提交我这一句",
    "modify": "改我这一段（新的一轮）",
    "pause": "暂停这次共同实践",
    "end": "显式结束",
}

#: 主体侧的操作**不在**这里：那是 A 的 Life 侧 + B 的 writer。写在这张表里是为了让测试能
#: 断言「手机没有它」，而不是为了让手机调用它。
LIFE_ONLY_OPERATIONS: tuple[str, ...] = (
    "submit_subject_response", "submit_subject_contribution", "commit_work_text",
    "resume_practice", "reopen_practice", "reconfirm_materials",
    "accept", "decline", "claim", "run", "tick",
)

#: C 需要的精确接口 = A 已发布的真实方法名与关键字。``required=False`` 的那几条缺了只是功能
#: 降级（具名原因）；缺了 ``required=True`` 的那几条整个共同实践面就诚实不可用。
#: ``params`` 里只写**真的会传**的关键字，调用点绝不额外传别的东西。
INTERFACE_SPEC: tuple[dict[str, Any], ...] = (
    {"op": "open", "method": "open_practice", "required": True, "caller": "user",
     "params": ("title", "opening_text", "material_refs", "operation_key"),
     "meaning": "把一份明确共享材料开成一次共同实践"},
    {"op": "list", "method": "list_practices", "required": True, "caller": "read",
     "params": ("include_ended", "limit"),
     "meaning": "这台实例现在有哪些共同实践（列表不投影私人正文）"},
    {"op": "read", "method": "read_practice", "required": True, "caller": "read",
     "params": ("session_id", "include_history", "include_material_excerpt", "entry_limit"),
     "meaning": "一次共同实践的全部可见内容：材料引用/版本、双方贡献、作品版本、状态"},
    {"op": "submit_user", "method": "submit_user_contribution", "required": True,
     "caller": "user",
     "params": ("session_id", "text", "expected_head", "operation_key"),
     "meaning": "用户自己的一次发言与贡献（永远不是她那一份）"},
    {"op": "pause", "method": "pause_practice", "required": True, "caller": "user",
     "params": ("session_id", "note", "expected_head", "operation_key"),
     "meaning": "暂停这次共同实践（用户的请求，不是她的决定）"},
    {"op": "end", "method": "end_practice", "required": True, "caller": "user",
     "params": ("session_id", "reason", "expected_head", "operation_key"),
     "meaning": "显式结束这次共同实践"},
    {"op": "context", "method": "context_view", "required": False, "caller": "read",
     "params": ("session_id", "include_material_excerpt", "entry_limit"),
     "meaning": "给 Life 用的有正文上下文（P 只在诊断面读，不显示私人全集）"},
)

#: 一次共同实践的状态词表（A 的词；P 只翻译，不发明第四个）。
PRACTICE_STATE_TEXT = {
    "OPEN": "开着的",
    "PAUSED": "已暂停",
    "ENDED": "已结束",
    "CANDIDATE": "还只是候选",
    "UNKNOWN": "状态未知",
}
#: 不算「还开着」的那几个。
PRACTICE_CLOSED_STATES = frozenset({"ENDED", "CLOSED", "DRAFT", "CANDIDATE", "UNKNOWN", ""})

#: 贡献的来源标签：界面必须能把「谁写的」分开。A 的 ``contributor`` 只有两个值。
CONTRIBUTOR_TEXT = {
    "user": "你",
    "operator": "你",
    "local_operator": "你",
    "subject": "她",
    "life": "她",
    "assistant": "她",
}

#: 每一次读都带着这句话：它是**事实声明**，不是界面文案。
LOCAL_ONLY_NOTE = (
    "这是这台实例本地的共同实践；没有向任何外部渠道发送，"
    "\"已收到\"只表示服务记下了，不等于她读过、更不等于她同意"
)

#: 一次读/写里最多投影多少条（A 自己的上限优先，这里只是预检）。
MAX_LIST = 50
MAX_PRACTICE_ID = 200
MAX_SOURCE_ID = 200
MAX_VERSION_ID = 200
MAX_TITLE = 120
MAX_EXCERPT = 800
MAX_CONTRIBUTIONS = 60
MAX_TEXT = 4000

_STATUS_MAP = {
    "ok": STATUS_OK,
    "applied": STATUS_APPLIED,
    "already_applied": STATUS_ALREADY,
    "rejected": STATUS_REJECTED,
    "conflict": STATUS_CONFLICT,
    "stale_source": STATUS_STALE_SOURCE,
    "not_found": STATUS_NOT_FOUND,
    "absent": STATUS_NOT_FOUND,
    "unknown": STATUS_UNKNOWN,
    "unavailable": STATUS_UNAVAILABLE,
}


def _status(raw: Any) -> str:
    return _STATUS_MAP.get(str(raw or "").strip().lower(), STATUS_UNAVAILABLE)


def _line(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else ("" if value is None else str(value))
    return " ".join(text.split())[:limit]


def _text(value: Any, limit: int) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _int_or_none(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return int(value)


def _maps(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    return [dict(row) for row in value if isinstance(row, Mapping)]


def interface_gap(service: Any) -> list[dict[str, Any]]:
    """Which of A's published-interface requirements a real owner does **not** satisfy.

    Empty list = every required op exists under its published name and accepts exactly the
    named keywords.  This is a *verification* of the live object (``inspect.signature``), not a
    guess: the caller only ever passes those keyword names, and a gap is a named refusal instead
    of a ``TypeError`` at the owner's door.
    """

    gaps: list[dict[str, Any]] = []
    for spec in INTERFACE_SPEC:
        op = str(spec["op"])
        method = str(spec["method"])
        member = getattr(service, method, None)
        if not callable(member):
            if spec["required"]:
                gaps.append({"op": op, "method": method, "missing": "method", "required": True})
            continue
        try:
            signature = inspect.signature(member)
        except (TypeError, ValueError):
            # A C-level or wrapped callable: existence is all we can verify.  It is not a gap.
            continue
        accepts_kwargs = any(parameter.kind is inspect.Parameter.VAR_KEYWORD
                             for parameter in signature.parameters.values())
        if accepts_kwargs:
            continue
        missing = [name for name in spec["params"]
                   if name not in signature.parameters]
        if missing:
            gaps.append({"op": op, "method": method,
                         "missing": "parameter:" + ",".join(missing),
                         "required": bool(spec["required"])})
    return gaps


def _material_of(raw: Mapping[str, Any]) -> dict[str, Any]:
    """The first recorded material, as both the list row and the detail want it."""

    materials = _maps(raw.get("materials"))
    if not materials:
        return {"artifact_id": "", "version_id": "", "title": "", "readable": False,
                "reason_code": "", "state": "", "count": 0}
    first = materials[0]
    state = _line(first.get("state"), 24).upper()
    readable = first.get("readable")
    if not isinstance(readable, bool):
        readable = state in ("READABLE", "MOVED")
    return {
        "artifact_id": _line(first.get("artifact_id"), MAX_SOURCE_ID),
        "version_id": _line(first.get("version_id"), MAX_VERSION_ID),
        "title": _line(first.get("title"), MAX_TITLE),
        "readable": bool(readable),
        "reason_code": _line(first.get("reason_code"), 64),
        "state": state,
        "count": len(materials),
    }


def _entries_of(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    """A's ``contributions`` + ``responses`` as one chronological list (who wrote it first)."""

    rows: list[dict[str, Any]] = []
    for row in _maps(raw.get("contributions")):
        rows.append(dict(row))
    for row in _maps(raw.get("responses")):
        row = dict(row)
        row.setdefault("kind", "response")
        rows.append(row)
    rows.sort(key=lambda item: (int(item.get("seq") or 0), str(item.get("recorded_at") or "")))
    return rows


def _basis_ref(basis: Any) -> str:
    """A's basis block, as the one *reference* it names (never the prose around it)."""

    if not isinstance(basis, Mapping):
        return ""
    for key in ("decision_ref", "event_ref", "action_ref", "adoption_ref", "ref"):
        value = basis.get(key)
        if isinstance(value, str) and value.strip():
            return _line(value, 200)
    return ""


def _basis_kind(basis: Any) -> str:
    """``decision`` / ``event`` / ``action`` / ``adoption`` -- A's own word, or nothing."""

    return _line(basis.get("kind"), 24) if isinstance(basis, Mapping) else ""


def _basis_verified(basis: Any) -> bool:
    """Did A verify that source through its own read port before writing?"""

    return bool(basis.get("verified")) if isinstance(basis, Mapping) else False


def _practice_ref_in(payload: Any) -> str:
    """The practice artifact ref B recorded in a result block (read-only projection)."""

    if not isinstance(payload, Mapping):
        return ""
    blocks = []
    result = payload.get("result")
    if isinstance(result, Mapping):
        blocks.append(result)
    blocks.append(payload)
    for block in blocks:
        for key in ("practice_session_ref", "practice_session"):
            value = block.get(key)
            if isinstance(value, str) and value.strip():
                return _line(value, MAX_PRACTICE_ID)
        shared = block.get("shared_practice")
        if isinstance(shared, Mapping):
            value = shared.get("session_id") or shared.get("practice_id")
            if isinstance(value, str) and value.strip():
                return _line(value, MAX_PRACTICE_ID)
    return ""

def practice_row(raw: Mapping[str, Any]) -> dict[str, Any]:
    """One practice as a *summary* row: ids, state, counts -- never a private body."""

    state = _line(raw.get("state"), 24).upper()
    material = _material_of(raw)
    entries = _entries_of(raw)
    if entries:
        user_rounds: Optional[int] = len([row for row in entries
                                         if _line(row.get("contributor"), 24) == "user"])
        subject_rounds: Optional[int] = len([row for row in entries
                                            if _line(row.get("contributor"), 24) == "subject"])
    else:
        # A's list summary carries counts, not a per-contributor breakdown: never invent one.
        responses = _int_or_none(raw.get("response_count"))
        user_rounds = None
        subject_rounds = responses
    contributions_total = _int_or_none(raw.get("contribution_count"))
    project = raw.get("project") if isinstance(raw.get("project"), Mapping) else {}
    head = raw.get("head_version_id") or project.get("head_version_id")
    revision = raw.get("practice_revision")
    if revision is None:
        revision = raw.get("revision")
    return {
        "practice_id": _line(raw.get("session_id") or raw.get("practice_id"), MAX_PRACTICE_ID),
        "title": _line(raw.get("title"), MAX_TITLE),
        "state": state,
        "state_text": PRACTICE_STATE_TEXT.get(state, state or "服务没有说状态"),
        "open": state not in PRACTICE_CLOSED_STATES,
        "joint_state": _line(raw.get("joint_state"), 24).upper(),
        "head_version_id": _line(head, MAX_VERSION_ID),
        "version_number": _int_or_none(raw.get("version_number") or project.get("version_number")),
        "revision": _int_or_none(revision),
        "source_artifact_id": material["artifact_id"],
        "source_version_id": material["version_id"],
        "source_title": material["title"],
        "source_readable": material["readable"],
        "source_reason_code": material["reason_code"],
        "material_count": material["count"],
        "user_rounds": user_rounds,
        "subject_rounds": subject_rounds,
        "contributions_total": contributions_total,
        "pending_commitments": len(_maps(raw.get("pending_commitments")))
        if isinstance(raw.get("pending_commitments"), (list, tuple))
        else _int_or_none(raw.get("pending_commitments")),
        "updated_at": _line(raw.get("updated_at"), 40),
    }


def contribution_row(raw: Mapping[str, Any], *, round_number: Optional[int] = None) -> dict[str, Any]:
    """One recorded exchange turn.  **Who** wrote it is the first thing we keep."""

    by = _line(raw.get("contributor") or raw.get("by") or raw.get("author"), 24).lower()
    text = raw.get("text")
    return {
        "contribution_id": _line(raw.get("seq") or raw.get("contribution_id") or raw.get("id"),
                                 MAX_PRACTICE_ID),
        "by": by,
        "by_text": CONTRIBUTOR_TEXT.get(by, by or "来源未说明"),
        "is_user": by in ("user", "operator", "local_operator"),
        "is_subject": by in ("subject", "life", "assistant"),
        "round": round_number if round_number is not None else _int_or_none(raw.get("round")),
        "seq": _int_or_none(raw.get("seq")),
        "kind": _line(raw.get("kind"), 24),
        "text": _text(text, MAX_EXCERPT),
        "text_chars": _int_or_none(raw.get("chars")) if "chars" in raw
        else _int_or_none(raw.get("text_chars")),
        "at": _line(raw.get("recorded_at") or raw.get("at") or raw.get("created_at"), 40),
        # 她说的话有没有来源、是不是她自己的那一半 —— 界面必须能分开这三种。
        "origin": _line(raw.get("kind") or raw.get("origin"), 40),
        "decision_ref": _basis_ref(raw.get("basis")),
        "basis_kind": _basis_kind(raw.get("basis")),
        "basis_verified": _basis_verified(raw.get("basis")),
        "commitment_state": _line(raw.get("commitment_state"), 24),
        "draft": bool(raw.get("draft")),
    }


def version_row(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "version_id": _line(raw.get("version_id"), MAX_VERSION_ID),
        "version_number": _int_or_none(raw.get("version_number")),
        "at": _line(raw.get("created_at") or raw.get("at"), 40),
        "by": _line(raw.get("by"), 24),
        "text_chars": _int_or_none(raw.get("content_chars") or raw.get("text_chars")),
        "is_head": bool(raw.get("is_head")),
        # 版本历史里从不带正文：正文只在 "work.text" 这一处。
        "text": "",
    }


class PhoneSharedPractice:
    """One instance's shared-practice surface over A's service.  No store, no timer, no writer."""

    def __init__(self, *, subject: str = "", workspace_id: str = "",
                 service: Optional[Any] = None, transport: Optional[Any] = None,
                 transport_error: str = "", clock: Optional[Any] = None) -> None:
        self.subject = str(subject or "").strip()
        self.workspace_id = str(workspace_id or "").strip()
        self._service = service
        #: The instance's own ``LocalContactInbox`` facade, as a **callable** (built lazily by
        #: ``PhoneService``).  The user's own words and the explicit end travel through it; A's
        #: artifact is the *work*, the inbox is the *transport* to a normal Life runner.
        self._transport = transport
        #: Why A's owner could not be built (named, never guessed around).
        self._transport_error = str(transport_error or "")
        self._clock = clock

    # -- introspection -------------------------------------------------------------------

    @property
    def bound(self) -> bool:
        if self._service is None:
            return False
        return not [gap for gap in interface_gap(self._service) if gap.get("required")]

    def describe(self) -> dict[str, Any]:
        gaps = interface_gap(self._service) if self._service is not None else [
            {"op": spec["op"], "method": spec["method"], "missing": "service",
             "required": bool(spec["required"])}
            for spec in INTERFACE_SPEC if spec["required"]]
        return {
            "kind": "phone_shared_practice",
            "schema_version": SCHEMA_SHARED_PRACTICE,
            "interface_schema": SCHEMA_SHARED_PRACTICE_INTERFACE,
            "owner": "shared_practice_service",
            "bound": self.bound,
            "bound_owner": self._service is not None,
            "interface_gaps": gaps,
            "required_interface": [
                {"op": spec["op"], "method": spec["method"], "caller": spec["caller"],
                 "params": list(spec["params"]), "required": bool(spec["required"])}
                for spec in INTERFACE_SPEC],
            "actions": list(PHONE_ACTIONS),
            "action_text": dict(ACTION_TEXT),
            "subject_only_actions": ["resume", "reopen"],
            "subject": self.subject,
            "workspace_id": self.workspace_id,
            "owns_no_store": True,
            "owns_activity": False,
            "owns_relationship": False,
            "owns_chat_store": False,
            "starts_second_writer": False,
            # 四条硬事实：不替她写、不替她接纳、不把她的话存成聊天、不起 Life。
            "writes_subject_contribution": False,
            "accepts_for_subject": False,
            "stores_private_chat": False,
            "starts_life_daemon": False,
            # 用户自己那一半走**传输**（本实例自己的 LocalContactInbox）；作品只是 A 的工件。
            # 手机不把用户贡献写进 A，也不自己结束作品：那都是正常 Life 处理事件时做的。
            "transport": self._transport_block(),
            "phone_writes_user_contribution": False,
            "phone_ends_practice": False,
            "acceptance_owner": "life",
            "external_send": False,
            "local_only": True,
            "delivered_to_user": False,
            "note": LOCAL_ONLY_NOTE,
        }

    def _transport_block(self) -> dict[str, Any]:
        """How the user's own half travels, and what stays hers."""

        return {
            "kind": "local_contact_inbox",
            "bound": callable(self._transport),
            "reason_code": self._transport_error,
            "user_input": "LOCAL_MESSAGE",
            "invitation": "CO_PRESENCE_INVITATION",
            "explicit_end": "CO_PRESENCE_END",
            "owner": "life",
            "note": "你这一句进本实例自己的来信箱；处理它的是正常 Life，不是手机",
        }

    # -- the one plumbing point ----------------------------------------------------------

    def _unavailable(self, reason_code: str, *, detail: str = "",
                     **extra: Any) -> dict[str, Any]:
        data: dict[str, Any] = {"owner": "shared_practice_service", "bound": self.bound,
                                "local_only": True, "delivered_to_user": False,
                                "accepted": False, "queued": False, "note": LOCAL_ONLY_NOTE}
        data.update(extra)
        out = {"ok": False, "status": STATUS_UNAVAILABLE,
               "reason_code": str(reason_code or "SHARED_PRACTICE_UNAVAILABLE")[:64], "data": data}
        if detail:
            out["detail"] = _text(detail, 200)
        return out

    def _call(self, op: str, *, optional: bool = False, **kwargs: Any) -> dict[str, Any]:
        """One verified call on A's owner.  Never a guessed signature, never a fake answer."""

        spec = next((item for item in INTERFACE_SPEC if item["op"] == op), None)
        if spec is None:
            return self._unavailable("SHARED_PRACTICE_OP_UNKNOWN", op=op)
        if self._service is None:
            return self._unavailable("SHARED_PRACTICE_UNBOUND", op=op,
                                     interface=[item["method"] for item in INTERFACE_SPEC
                                                if item["required"]])
        gaps = interface_gap(self._service)
        blocking = [gap for gap in gaps if gap.get("required")]
        if blocking:
            return self._unavailable("SHARED_PRACTICE_INTERFACE_MISMATCH", op=op,
                                     interface_gaps=blocking)
        method = str(spec["method"])
        member = getattr(self._service, method, None)
        if not callable(member):
            if optional:
                return self._unavailable("SHARED_PRACTICE_OP_UNSUPPORTED", op=op, method=method)
            return self._unavailable("SHARED_PRACTICE_INTERFACE_MISMATCH", op=op,
                                     interface_gaps=[{"op": op, "method": method,
                                                      "missing": "method", "required": True}])
        # Only the declared keywords, and only when the live signature really accepts them.
        try:
            signature = inspect.signature(member)
            accepts_kwargs = any(parameter.kind is inspect.Parameter.VAR_KEYWORD
                                 for parameter in signature.parameters.values())
        except (TypeError, ValueError):
            accepts_kwargs = True
            signature = None
        sent = {}
        for name, value in kwargs.items():
            if name not in spec["params"]:
                continue
            if accepts_kwargs or (signature is not None and name in signature.parameters):
                sent[name] = value
        try:
            answer = member(**sent)
        except TypeError as exc:
            # A real signature disagreement: named refusal, and the caller's key is untouched.
            return self._unavailable("SHARED_PRACTICE_CALL_REFUSED",
                                     detail=type(exc).__name__, op=op, method=method,
                                     error_type=type(exc).__name__)
        except Exception as exc:  # a broken owner is never "nothing happened"
            return self._unavailable("SHARED_PRACTICE_OWNER_ERROR", op=op, method=method,
                                     error_type=type(exc).__name__)
        if not isinstance(answer, Mapping):
            return self._unavailable("SHARED_PRACTICE_ANSWER_INVALID", op=op, method=method)
        return dict(answer)

    # -- reads (pure) --------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """``GET /shared-practice``: what exists now.  Zero writes, never a fake empty list."""

        answer = self._call("list", include_ended=True, limit=MAX_LIST)
        if not answer.get("ok") and not isinstance(answer.get("data"), Mapping):
            return answer
        if not answer.get("ok"):
            # Owner refused the read: keep its own reason, and say the list is **not** empty.
            data = dict(answer.get("data") or {})
            data.setdefault("practices", [])
            data["list_ok"] = False
            data["service"] = self.describe()
            return {"ok": False, "status": _status(answer.get("status")),
                    "reason_code": _line(answer.get("reason_code"), 64)
                    or "SHARED_PRACTICE_UNAVAILABLE",
                    "detail": _text(answer.get("detail"), 200), "data": data}
        payload = answer.get("data") if isinstance(answer.get("data"), Mapping) else answer
        rows = _maps(payload.get("sessions") or payload.get("practices") or payload.get("items"))
        items = [row for row in (practice_row(raw) for raw in rows) if row["practice_id"]]
        return {
            "ok": True, "status": STATUS_OK, "reason_code": "",
            "data": {
                "practices": items[:MAX_LIST],
                "count": len(items),
                "matched": _int_or_none(payload.get("matched")),
                "truncated": bool(payload.get("truncated")) or len(items) > MAX_LIST,
                # 列表这一面**没有**私人正文：它照实说没有投影任何正文。
                "private_text_included": False,
                "transport": self._transport_block(),
                "service": self.describe(),
                "local_only": True,
                "delivered_to_user": False,
                "note": LOCAL_ONLY_NOTE,
                "owner": self._owner_block(answer),
            },
        }

    def work(self, practice_id: str) -> dict[str, Any]:
        """``GET /shared-practice/works/<id>``: material, rounds, contributions, versions."""

        pid = _line(practice_id, MAX_PRACTICE_ID)
        if not pid:
            return {"ok": False, "status": STATUS_REJECTED,
                    "reason_code": "SHARED_PRACTICE_ID_REQUIRED", "data": {}}
        answer = self._call("read", session_id=pid, include_history=True,
                            include_material_excerpt=False, entry_limit=MAX_CONTRIBUTIONS)
        if not answer.get("ok"):
            status = _status(answer.get("status"))
            data = dict(answer.get("data") or {})
            data.setdefault("practice_id", pid)
            return {"ok": False, "status": status,
                    "reason_code": _line(answer.get("reason_code"), 64)
                    or "SHARED_PRACTICE_UNAVAILABLE",
                    "detail": _text(answer.get("detail"), 200), "data": data}
        payload = answer.get("data") if isinstance(answer.get("data"), Mapping) else answer
        project = payload.get("project") if isinstance(payload.get("project"), Mapping) else {}
        row = practice_row({**dict(payload),
                            "head_version_id": payload.get("head_version_id")
                            or project.get("head_version_id"),
                            "version_number": payload.get("version_number")
                            or project.get("version_number")})
        row["practice_id"] = row["practice_id"] or pid
        entries = _entries_of(payload)
        contributions = [contribution_row(item, round_number=index + 1)
                         for index, item in enumerate(entries)][:MAX_CONTRIBUTIONS]
        entries_capped = len(entries) > MAX_CONTRIBUTIONS
        history = payload.get("history") if isinstance(payload.get("history"), Mapping) else {}
        raw_versions = _maps(history.get("entries")) if history else []
        versions = [version_row(item) for item in raw_versions if item.get("version_id")]
        governance = payload.get("governance") if isinstance(payload.get("governance"),
                                                              Mapping) else {}
        work_block = payload.get("work") if isinstance(payload.get("work"), Mapping) else {}
        paused = payload.get("paused") if isinstance(payload.get("paused"), Mapping) else None
        ended = payload.get("ended") if isinstance(payload.get("ended"), Mapping) else None
        blocked = bool(governance.get("blocked"))
        material = _material_of(payload)
        data: dict[str, Any] = {
            "practice": row,
            "contributions": contributions,
            "versions": versions,
            # 两个**分开**的计数：用户输入与主体贡献不是一件事。
            "user_contributions": len([item for item in contributions if item["is_user"]]),
            "subject_contributions": len([item for item in contributions if item["is_subject"]]),
            "entries_truncated": entries_capped or bool(payload.get("contributions_truncated")),
            "material": material,
            "work": {
                "artifact_id": _line(project.get("artifact_id"), MAX_SOURCE_ID),
                "version_id": row["head_version_id"],
                "version_number": row["version_number"],
                "revision": _int_or_none(project.get("revision")),
                "text": _text(work_block.get("text"), MAX_EXCERPT),
                "text_chars": _int_or_none(work_block.get("chars")),
                "available": bool(work_block.get("available")),
                "updated_at": _line(work_block.get("updated_at"), 40),
                "updated_by": _line(work_block.get("updated_by"), 24),
                "version_count": len(versions) or _int_or_none(project.get("version_number")),
            },
            "pause": {
                "paused": row["state"] == "PAUSED" or bool(paused),
                "requested_by": _line((paused or {}).get("by"), 24) or
                ("user" if row["state"] == "PAUSED" else ""),
                "requested_at": _line((paused or {}).get("at"), 40),
                "reason_code": _line(payload.get("governance", {}).get("reason_code")
                                     if isinstance(payload.get("governance"), Mapping) else "", 64),
                "note": _text((paused or {}).get("text"), 200),
            },
            "end": {
                "ended": row["state"] in ("ENDED", "CLOSED"),
                "ended_at": _line((ended or {}).get("at"), 40),
                "reason": _text((ended or {}).get("text"), 120),
            },
            "governance": {
                "blocked": blocked,
                "stale": bool(governance.get("stale")),
                "reason_code": _line(governance.get("reason_code"), 64),
                "redacted": [str(item)[:80] for item in (governance.get("redacted") or [])
                             if isinstance(item, str)][:20],
                "writes_allowed": bool(governance.get("writes_allowed")),
            },
            # 权限边界：界面上能看到的，只有 owner 这次真的投影出来的。
            "permissions": {
                "private_source_exposed": False,
                "bounded_excerpt": True,
                "subject_contribution_visible": bool(
                    [item for item in contributions if item["is_subject"]]),
                "actions": self._actions_for(row),
                "may_write_subject_contribution": False,
                "may_accept_for_subject": False,
            },
            "private_text_included": bool(payload.get("private_text_included")) and not blocked,
            "transport": self._transport_block(),
            "service": self.describe(),
            "local_only": True,
            "delivered_to_user": False,
            "note": LOCAL_ONLY_NOTE,
            "owner": self._owner_block(answer),
        }
        return {"ok": True, "status": STATUS_OK, "reason_code": "", "data": data}

    @staticmethod
    def _actions_for(row: Mapping[str, Any]) -> list[str]:
        state = str(row.get("state") or "")
        if state in ("ENDED", "CLOSED"):
            # 结束了就不能再写：正文与贡献仍然可读，重开是她那一半（A 的 reopen 只给 Life）。
            return []
        if state == "PAUSED":
            return ["end"]
        return list(PHONE_ACTIONS)

    def operation(self, operation_key: str) -> dict[str, Any]:
        """``GET .../operations/<key>``: reconcile one **original** key.  Pure read.

        This is B's own reconciliation read over the instance's inbox ledger: ``absent`` (nothing
        landed under this key -- the same key may be used again), ``applied`` (it landed; the
        event and its recorded outcome are here), ``unknown`` (it landed and the outcome is
        UNKNOWN -- reconcile with the same key, never blind-retry).  P keeps no ledger.
        """

        key = _line(operation_key, 128)
        if not key:
            return {"ok": False, "status": STATUS_REJECTED,
                    "reason_code": "SHARED_PRACTICE_OPERATION_KEY_REQUIRED", "data": {}}
        answer = self._contact_call("operation", key)
        if not answer.get("ok") and not isinstance(answer.get("data"), Mapping):
            return answer
        payload = answer.get("data") if isinstance(answer.get("data"), Mapping) else {}
        status = _line(answer.get("status"), 24) or STATUS_UNKNOWN
        return {
            "ok": bool(answer.get("ok")),
            "status": status,
            "reason_code": _line(answer.get("reason_code"), 64),
            # 对账本身读成功了就是 200：``absent`` / ``unknown`` 是对这条键的答案。
            "http_status": 200,
            "detail": _text(answer.get("detail"), 200),
            "data": {
                "operation_key": key,
                "kind": _line(payload.get("kind"), 24),
                "event_ref": _line(payload.get("event_ref"), 200),
                "event_kind": _line((payload.get("event") or {}).get("event_kind")
                                    if isinstance(payload.get("event"), Mapping) else "", 40),
                "event_state": _line(payload.get("event_state"), 24),
                "outcome": _line(payload.get("outcome"), 24),
                "recorded": bool(payload.get("recorded")),
                "retryable": bool(payload.get("retryable")),
                "practice_id": _practice_ref_in(payload),
                "ledger_state": _line(payload.get("ledger_state"), 20),
                "note": "只读对账：不会用新键重投，也不会改写这次来话",
                "local_only": True,
            },
        }

    # -- the transport (the user's own half travels; her half stays hers) -----------------

    def _contact(self) -> Optional[Any]:
        """This instance's own inbox facade, or ``None`` when it cannot be built."""

        if not callable(self._transport):
            return None
        try:
            return self._transport()
        except Exception:  # an unbound instance is named by the caller, never faked
            return None

    def _contact_call(self, method: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """One call on the inbox facade.  ``unavailable`` is the only thing P adds."""

        contact = self._contact()
        if contact is None:
            return self._unavailable("SHARED_PRACTICE_TRANSPORT_UNBOUND",
                                     transport_error=self._transport_error)
        member = getattr(contact, method, None)
        if not callable(member):
            return self._unavailable("SHARED_PRACTICE_TRANSPORT_UNBOUND", method=method)
        try:
            answer = member(*args, **kwargs)
        except Exception as exc:
            return self._unavailable("SHARED_PRACTICE_TRANSPORT_ERROR",
                                     error_type=type(exc).__name__)
        if not isinstance(answer, Mapping):
            return self._unavailable("SHARED_PRACTICE_TRANSPORT_ANSWER_INVALID")
        return dict(answer)
    def _deliver(self, action: str, event_kind: str, *, operation_key: str,
                 practice_id: str = "", text: Optional[str] = None,
                 scope: Optional[str] = None, reason: Optional[str] = None,
                 invitation_id: Optional[str] = None,
                 material_refs: Optional[Sequence[str]] = None,
                 explicit_end: bool = False) -> dict[str, Any]:
        """Hand one user intent to the inbox.  It is a **request**, never an effect."""

        payload: dict[str, Any] = {"event_kind": event_kind, "operation_key": operation_key}
        if text:
            payload["text"] = text
        if scope:
            payload["scope"] = scope
        if reason:
            payload["reason"] = reason
        if invitation_id:
            payload["invitation_id"] = invitation_id
        if material_refs:
            payload["material_refs"] = [str(item) for item in material_refs]
        if explicit_end:
            payload["explicit_end"] = True
        answer = self._contact_call("submit", payload)
        data = answer.get("data") if isinstance(answer.get("data"), Mapping) else {}
        envelope: dict[str, Any] = {
            "action": action,
            "action_text": ACTION_TEXT.get(action, action),
            "practice_id": _line(practice_id, MAX_PRACTICE_ID),
            "event_ref": _line(data.get("event_ref"), 200),
            "event_kind": _line(data.get("event_kind"), 40) or event_kind,
            "event_state": _line((data.get("event") or {}).get("processing_state")
                                 if isinstance(data.get("event"), Mapping) else "", 24)
            or _line(data.get("processing_state"), 24),
            "operation_key": _line(data.get("operation_key"), 128) or operation_key,
            # 请求 ≠ 排队 ≠ 决定 ≠ 回应 ≠ 已提交作品：这一层只到「已记下」。
            "queued": bool(data.get("queued")),
            "submitted": bool(answer.get("ok")),
            "work_changed": False,
            "subject_responded": False,
            "accepted": False,
            "accepted_written_by_phone": False,
            "subject_contribution_written": False,
            "user_contribution_written": False,
            "phone_wrote_practice": False,
            "pause_requested": False,
            "activity_started": False,
            "starts_life_daemon": False,
            "delivered_to_user": False,
            "local_only": True,
            "retryable": bool(data.get("retryable")),
            "replayed": bool(data.get("replayed")),
            "note": "已记下，还没处理：处理它的是正常 Life；这不是她答应了，也不是作品已经改了",
        }
        if answer.get("ok"):
            return {"ok": True, "status": _line(answer.get("status"), 24) or STATUS_OK,
                    "reason_code": _line(answer.get("reason_code"), 64), "data": envelope}
        out = {"ok": False,
               "status": _line(answer.get("status"), 24) or STATUS_UNAVAILABLE,
               "reason_code": _line(answer.get("reason_code"), 64)
               or "SHARED_PRACTICE_TRANSPORT_REFUSED",
               "data": envelope}
        if answer.get("detail"):
            out["detail"] = _text(answer.get("detail"), 200)
        return out

    def _invitation_for(self, practice_id: str) -> dict[str, Any]:
        """Verify the protocol's durable binding against its original accepted inbox event."""
        summary = self._contact_call("summary")
        if not summary.get("ok"):
            return {}
        session = (summary.get("data") or {}).get("session") or {}
        if (not session.get("readable") or not session.get("open")
                or session.get("practice_session_ref") != practice_id):
            return {}
        ref = _line(session.get("practice_invitation_event_ref"), 200)
        invitation_id = _line(session.get("invitation_id"), 200)
        if not ref or not invitation_id:
            return {}
        detail = self._contact_call("event", ref)
        payload = detail.get("data") or {}
        event = payload.get("event") or {}
        result = payload.get("result") or {}
        if (not detail.get("ok") or event.get("event_kind") != "CO_PRESENCE_INVITATION"
                or event.get("invitation_id") != invitation_id
                or result.get("outcome") != "ACCEPTED"):
            return {}
        return {"invitation_id": invitation_id, "event_ref": ref}

    # -- the four user intents -----------------------------------------------------------

    def open_practice(self, *, source_artifact_id: str, source_version_id: str,
                      title: str, text: str, operation_key: str) -> dict[str, Any]:
        """``POST /shared-practice``: pick one shared material by **inviting** her.

        The work itself is opened by her side when the invitation is really accepted (B's normal
        chain does it, with the materials named here); the phone never opens a second artifact.
        """

        material = _line(source_artifact_id, MAX_SOURCE_ID)
        if not material:
            return {"ok": False, "status": STATUS_REJECTED,
                    "reason_code": "SHARED_PRACTICE_SOURCE_REQUIRED", "data": {}}
        result = self._deliver("open", "CO_PRESENCE_INVITATION", text=text, scope=title,
                               material_refs=[material], operation_key=operation_key)
        data = result.get("data")
        if isinstance(data, dict):
            data["expected_source_version_id"] = _line(source_version_id, MAX_VERSION_ID)
            data["material_refs"] = [material]
            if result.get("ok"):
                data["note"] = ("邀请已进她的来信箱：共同作品要等她那边处理完才存在；"
                                "界面不替她开、也不替她答应")
        return result

    def submit_user(self, *, practice_id: str, text: str, expected_head: str = "",
                    operation_key: str) -> dict[str, Any]:
        """One turn of **the user's own** words, carried to her by the inbox."""

        return self._deliver("submit", "LOCAL_MESSAGE", text=text, operation_key=operation_key,
                             practice_id=_line(practice_id, MAX_PRACTICE_ID))

    def modify(self, *, practice_id: str, text: str, expected_head: str = "",
               operation_key: str) -> dict[str, Any]:
        """The user's next turn, standing in for "edit my paragraph".

        A's contributions are an immutable history and B's transport has no in-place edit, so this
        is a new turn; the earlier one stays.  Saying otherwise here would be a lie.
        """

        result = self._deliver("modify", "LOCAL_MESSAGE", text=text, operation_key=operation_key,
                               practice_id=_line(practice_id, MAX_PRACTICE_ID))
        data = result.get("data")
        if isinstance(data, dict):
            data["edit_kind"] = "new_turn"
            data["history_kept"] = True
            if result.get("ok"):
                data["note"] = ("改我这一段 = 你的新的一轮：上一段留在历史里，"
                                "A 不改写已经记录的贡献")
        return result

    def pause(self, *, practice_id: str, expected_head: str,
              operation_key: str, note: Optional[str] = None) -> dict[str, Any]:
        """Pause **this artifact** (A's published user-permitted state action).

        It stops appending to this work; it does not pause her Activity, and it is not an end.
        """

        answer = self._call("pause", session_id=practice_id, note=note or "",
                            expected_head=expected_head, operation_key=operation_key)
        return self._write_answer(answer, "pause",
                                  fallback_reason="SHARED_PRACTICE_PAUSE_REFUSED")

    def end(self, *, practice_id: str, expected_head: str = "", operation_key: str,
            reason: Optional[str] = None) -> dict[str, Any]:
        """``POST .../actions`` with ``end``: an explicit **END event**, handed to normal Life.

        The phone does not set ``ENDED`` on the artifact on its own: B's chain ends the joint
        session *and* the practice, so the front stage is released in the same step.  When the
        invitation that opened this practice cannot be found, that is a named refusal.
        """

        pid = _line(practice_id, MAX_PRACTICE_ID)
        binding = self._invitation_for(pid)
        if not binding.get("invitation_id"):
            return {"ok": False, "status": STATUS_REJECTED,
                    "reason_code": "SHARED_PRACTICE_INVITATION_UNKNOWN",
                    "detail": "找不到打开这件作品的那次邀请：结束要由她那边按邀请收口",
                    "data": {"action": "end", "practice_id": pid,
                             "event_ref": "", "queued": False, "submitted": False,
                             "accepted": False, "local_only": True}}
        return self._deliver("end", "CO_PRESENCE_END", reason=reason,
                             invitation_id=binding["invitation_id"],
                             explicit_end=True,
                             operation_key=operation_key, practice_id=pid)

    def resume(self, *, practice_id: str, expected_head: str,
               operation_key: str) -> dict[str, Any]:
        """**Refused by design**: ``resume`` is A's Life-side action, not the user's."""

        return {"ok": False, "status": STATUS_REJECTED,
                "reason_code": "SHARED_PRACTICE_ACTION_UNSUPPORTED",
                "detail": "重新取用是她那一半（A 的 resume_practice 只给 Life 侧且要绑来源）",
                "data": {"action": "resume", "actions": list(PHONE_ACTIONS),
                         "subject_only_actions": ["resume", "reopen"],
                         "accepted": False, "queued": False, "local_only": True}}

    def act(self, *, practice_id: str, action: str, expected_head: str = "",
            operation_key: str = "", text: Optional[str] = None,
            reason: Optional[str] = None) -> dict[str, Any]:
        """One explicit action on one practice.  ``action`` is one of ``PHONE_ACTIONS``."""

        name = _line(action, 24).lower()
        if name not in PHONE_ACTIONS:
            return {"ok": False, "status": STATUS_REJECTED,
                    "reason_code": "SHARED_PRACTICE_ACTION_REFUSED",
                    "detail": "手机只做 submit / modify / pause / end；"
                              "resume / reopen 是她的那一半",
                    "data": {"action": name, "actions": list(PHONE_ACTIONS),
                             "subject_only_actions": ["resume", "reopen"]}}
        pid = _line(practice_id, MAX_PRACTICE_ID)
        if not pid:
            return {"ok": False, "status": STATUS_REJECTED,
                    "reason_code": "SHARED_PRACTICE_ID_REQUIRED", "data": {}}
        if name in ("submit", "modify"):
            if not isinstance(text, str) or not text.strip():
                return {"ok": False, "status": STATUS_REJECTED,
                        "reason_code": "SHARED_PRACTICE_TEXT_REQUIRED",
                        "data": {"action": name}}
            if name == "submit":
                return self.submit_user(practice_id=pid, text=text,
                                        expected_head=expected_head,
                                        operation_key=operation_key)
            return self.modify(practice_id=pid, text=text, expected_head=expected_head,
                               operation_key=operation_key)
        if name == "pause":
            return self.pause(practice_id=pid, expected_head=expected_head,
                              operation_key=operation_key, note=reason)
        return self.end(practice_id=pid, expected_head=expected_head,
                        operation_key=operation_key, reason=reason)

    # -- envelopes -----------------------------------------------------------------------

    def _owner_block(self, answer: Mapping[str, Any]) -> dict[str, Any]:
        block: dict[str, Any] = {"status": str(answer.get("status") or "")}
        if answer.get("reason_code"):
            block["reason_code"] = _line(answer.get("reason_code"), 64)
        if answer.get("detail"):
            block["detail"] = _text(answer.get("detail"), 200)
        return block

    def _write_answer(self, answer: Mapping[str, Any], action: str,
                      *, fallback_reason: str) -> dict[str, Any]:
        """One A answer (pause only) as this layer's envelope."""

        status = _status(answer.get("status"))
        payload = answer.get("data") if isinstance(answer.get("data"), Mapping) else {}
        material = _material_of(payload)
        data: dict[str, Any] = {
            "action": action,
            "action_text": ACTION_TEXT.get(action, action),
            "practice_id": _line(payload.get("session_id") or payload.get("practice_id"),
                                 MAX_PRACTICE_ID),
            "state": _line(payload.get("state"), 24).upper(),
            "joint_state": _line(payload.get("joint_state"), 24).upper(),
            "head_version_id": _line(payload.get("head_version_id") or payload.get("version_id"),
                                    MAX_VERSION_ID),
            "version_number": _int_or_none(payload.get("version_number")),
            "practice_revision": _int_or_none(payload.get("practice_revision")),
            "operation_key": _line(payload.get("operation_key"), 128),
            "source_version_id": material["version_id"],
            "queued": False,
            "submitted": False,
            "accepted": bool(payload.get("accepted")),
            "accepted_written_by_phone": False,
            "subject_contribution_written": False,
            "pause_requested": action == "pause",
            "activity_started": False,
            "starts_life_daemon": False,
            "delivered_to_user": False,
            "local_only": True,
            "retryable": bool(answer.get("retryable")),
            "note": LOCAL_ONLY_NOTE,
            "owner": self._owner_block(answer),
        }
        ok = bool(answer.get("ok"))
        if not ok:
            out = {"ok": False,
                   "status": status if status != STATUS_OK else STATUS_UNAVAILABLE,
                   "reason_code": _line(answer.get("reason_code"), 64) or fallback_reason,
                   "data": data}
            if answer.get("detail"):
                out["detail"] = _text(answer.get("detail"), 200)
            return out
        state = str(data["state"] or "").upper()
        data["submitted"] = status in (STATUS_APPLIED, STATUS_ALREADY)
        data["work_changed"] = False
        data["subject_responded"] = bool(payload.get("response_count")) \
            or data["joint_state"] == "JOINT"
        data["stopped"] = state in ("PAUSED", "ENDED", "CLOSED")
        return {"ok": True,
                "status": status if status != STATUS_UNAVAILABLE else STATUS_APPLIED,
                "reason_code": _line(answer.get("reason_code"), 64),
                "detail": _text(answer.get("detail"), 200), "data": data}
__all__ = [
    "ACTION_TEXT",
    "CONTRIBUTOR_TEXT",
    "INTERFACE_SPEC",
    "LIFE_ONLY_OPERATIONS",
    "LOCAL_ONLY_NOTE",
    "MAX_CONTRIBUTIONS",
    "MAX_EXCERPT",
    "MAX_LIST",
    "MAX_PRACTICE_ID",
    "MAX_TEXT",
    "PHONE_ACTIONS",
    "PRACTICE_CLOSED_STATES",
    "PRACTICE_STATE_TEXT",
    "PhoneSharedPractice",
    "SCHEMA_SHARED_PRACTICE",
    "SCHEMA_SHARED_PRACTICE_INTERFACE",
    "STATUS_ALREADY",
    "STATUS_APPLIED",
    "STATUS_CONFLICT",
    "STATUS_NOT_FOUND",
    "STATUS_OK",
    "STATUS_REJECTED",
    "STATUS_STALE_SOURCE",
    "STATUS_UNAVAILABLE",
    "STATUS_UNKNOWN",
    "contribution_row",
    "interface_gap",
    "practice_row",
    "version_row",
]

"""B 包（ROOM/PHONE）：私人日常事项的最小 typed 文档，持久真相归 Life Supply ManagedArtifact。

这是什么，不是什么
------------------
* 它是一份 **typed 私人事项文档** 的编解码 + 命令 + 投影适配器：整份文档存进**现有**
  ``ManagedArtifact`` 的一个 artifact，每次修改提交一个新**版本**。读写全部走已经存在的
  ``life_artifact_port.LifeArtifactPort``（同一个 owner、同一个 Unix socket、同一套 op）。
* 它是 **v1 原型协议**，不冒称原本已有 Phone owner：不新建数据库、不新建表、不改 Supply
  schema、不接 cycle/CLI、不写 World/Body/Memory。协议名就是 ``private_daily_items``。
* 房间位置仍然是 **World 的事实**。本模块只记录调用方**实际观察到**的房间 object ref
  （``room_object_refs`` / ``note_item_at_observed_object``），并把这条记录明确标成「观察
  链接」；这里没有任何 placement 实现，也不把文档存储叫位置。
* 绑定是**显式**的：调用方给出 ``artifact_id``，本模块只读那一个 artifact，绝不
  ``latest_resumable`` / 全表扫描去猜——否则一次误选就会把私人事项写进作品。

语义分层（互相不能顶替）
------------------------
* **source 与版本**：``record_source_acquisition`` 记下来源 id、来源版本、以及「实际取得」
  的回执与范围/页码证据。取得是取得，不是理解。
* **阅读位置**：``set_reading_position`` 只记位置；它的 ``understanding`` 恒为 ``UNKNOWN``，
  本模块**没有**任何 API 能把页码变成「读懂了」。
* **主观未懂标记**：``mark_unclear`` 是**另一条**记录，和位置分开存放。
* **收藏**：``save_reference`` 只存指针（source_id/版本/label），``read=False``、
  ``content_fetched=False``；它既不取全文，也不把来源变成已读。
* **收件**：``receive_item`` 的字段只能来自调用方给出的明确接收记录（``receipt_ref``）；
  ``co_presence`` 恒为 ``False``——一条普通消息的到达不等于完整共处。
* **撤权/失效**：``mark_source_unavailable`` 之后，该来源的正文/摘要一律拒绝（
  ``PrivateItemsSourceUnavailable``）；文档本身**不缓存来源正文**，因此也没有缓存可以复活它。
* **隐私**：``metadata_view`` / ``items`` 走字段**白名单**，默认不返回正文、摘要、未懂标记
  与 locator；要文本必须显式调 ``full_text`` / ``disclosure_excerpt``。
* **段落锚点**：memo 可选 ``reading_anchor``（``source_id`` / ``source_revision`` / ``page``），
  只说明"这条笔记写在哪一页"，不签发理解。旧 memo 没有锚点照旧可读可改；``revise_memo``
  只换正文，绝不抹掉锚点；``reading_anchor`` 属于元数据白名单，正文仍只在 ``full_text`` 里。
* **阅读操作归属**：一次阅读写把"这条 operation_key 属于哪个动作 / 哪一页 / 哪个版本 /
  哪个条目"和它的效果写进**同一个版本**（``READING_OPERATIONS_FIELD``），只用于原键重放与
  跨 kind 冲突判断；它不是第二本账，也不含任何正文。
* **阅读批注计划**：``plan_page_annotation`` 是**纯计划**——在已经由 ``plan_page_read`` 取得
  该页的文档上，最多加**一条**批注（带 ``reading_anchor`` 的 ``memo``，或 ``unclear_marker``）；
  ``no_change`` 一条都不加，也绝不重写停点。它不读 socket、不提交，只把整条意图（同键 +
  不含正文的摘要）与效果留在同一个版本里；同键异意图一律拒绝。

写入语义（CAS / UNKNOWN / 撤权）
--------------------------------
* 每次写都先读真实 head，再用 ``expected_head`` 做 compare-and-swap；head 变了 owner 会
  返回 ``CONFLICT``，本模块**抛错而不覆盖**（``PrivateItemsConflict``）。
* 结果 ``UNKNOWN`` 时**不盲重发**：先 ``resolve_operation`` 对账，再决定是「已落地」还是
  「从未落地（可用同一个 operation_key 重试）」，两者都不是重发。
* owner 拒绝（含 grant 撤权）抛 ``PrivateItemsOwnerRefused``，不改本地缓存。

本模块只新增窄 API；``life_artifact_port.py`` 只多了一个**只读**的 ``operation_row()``
（转发既有 ``lookup_operation`` 读 op，供调用方核对"这个键到底在哪个 artifact 上落了什么"），
现有 create_artifact / commit_markdown / read_artifact / read_head / resolve_operation 未改。
"""
from __future__ import annotations

import copy
import json
import sys
from hashlib import sha256
from collections.abc import Mapping as AbcMapping, Sequence as AbcSequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

_LIFE_SCRIPTS = str(Path(__file__).resolve().parent)
if _LIFE_SCRIPTS not in sys.path:
    sys.path.insert(0, _LIFE_SCRIPTS)

from life_artifact_port import (  # noqa: E402
    ArtifactOutcome,
    EffectState,
    LifeArtifactPort,
    operation_key_for,
)

DOC_TYPE = "nyairo.private_daily_items.v1"
SCHEMA_VERSION = 1

ITEM_MEMO = "memo"
ITEM_SAVED_REFERENCE = "saved_reference"
ITEM_SOURCE_ACQUISITION = "source_acquisition"
ITEM_READING_POSITION = "reading_position"
ITEM_UNCLEAR_MARKER = "unclear_marker"
ITEM_INBOX = "inbox_item"

ITEM_TYPES: tuple[str, ...] = (
    ITEM_MEMO, ITEM_SAVED_REFERENCE, ITEM_SOURCE_ACQUISITION,
    ITEM_READING_POSITION, ITEM_UNCLEAR_MARKER, ITEM_INBOX,
)

#: The write kind used for the observation-link update.  It is not an item type: it updates an
#: existing item and therefore has no metadata row of its own.
_KIND_OBSERVED_LINK = "observed_object_link"
#: Item id prefix per type.  The suffix is derived from the *intent* operation key, so retrying
#: one intent (with the same key) regenerates the same id and therefore the same payload -- which
#: is what makes an owner-side replay safe instead of a duplicate row.
_ID_PREFIX: dict[str, str] = {
    ITEM_MEMO: "memo",
    ITEM_SAVED_REFERENCE: "ref",
    ITEM_SOURCE_ACQUISITION: "acq",
    ITEM_READING_POSITION: "pos",
    ITEM_UNCLEAR_MARKER: "unclear",
    ITEM_INBOX: "inbox",
    #: Not an item type of its own: the key kind for the observation-link write.
    _KIND_OBSERVED_LINK: "obs",
}

INBOX_UNREAD = "UNREAD"
INBOX_READ = "READ"

#: A reading position never asserts comprehension.  There is no other value in this module.
UNDERSTANDING_UNKNOWN = "UNKNOWN"

#: A saved reference is saved, and nothing more.
REFERENCE_SAVED = "SAVED"

#: Hard bound on any private excerpt kept in the document.  Over-long evidence is refused, never
#: silently truncated (a truncated excerpt would be a quiet lie about what was actually obtained).
MAX_EXCERPT_CHARS = 480

#: The fields an optional reading anchor may carry.  An anchor is structural metadata (which real
#: page a note or a stop point belongs to), never private text and never a claim of understanding.
READING_ANCHOR_FIELDS: tuple[str, ...] = ("source_id", "source_revision", "page")

#: Where the minimal per-operation ownership of a reading write is kept: still this one typed
#: document, still one version alongside the effect.  It answers "which action/target did this
#: operation key belong to?" for a replay or a cross-kind reuse check -- it is not a second ledger
#: and it holds no text.
READING_OPERATIONS_FIELD = "reading_operations"
#: Bound on that map.  Oldest entries are dropped: it is recovery evidence, not an archive.
MAX_READING_OPERATIONS = 200

#: 阅读响应批注的三种意图。``no_change`` 是合法结论：这一页什么都不记。
ANNOTATION_NOTE = "note"
ANNOTATION_UNCLEAR = "unclear"
ANNOTATION_NO_CHANGE = "no_change"
ANNOTATION_KINDS: tuple[str, ...] = (ANNOTATION_NOTE, ANNOTATION_UNCLEAR, ANNOTATION_NO_CHANGE)

#: 一条私人笔记 / 一个疑问的上限。与 ``subject_reading_response`` 的解析边界同值（由
#: ``tests/test_subject_reading_annotation_plan.py`` 钉住相等）：超长拒绝，绝不截断。
MAX_ANNOTATION_TITLE_CHARS = 200
MAX_ANNOTATION_TEXT_CHARS = 1200
#: 响应对象允许的键（``self_note`` 只是容忍模型模板，直接丢弃）。
_ANNOTATION_RESPONSE_FIELDS: tuple[str, ...] = ("kind", "title", "body", "note", "self_note")

#: Which non-text fields each item type may expose through ``metadata_view``.  An allow-list, so a
#: new private field cannot leak by accident.
_METADATA_FIELDS: dict[str, tuple[str, ...]] = {
    ITEM_MEMO: ("title", "created_at", "updated_at", "revisions",
                "observed_object_ref", "observed_at", "reading_anchor"),
    ITEM_SAVED_REFERENCE: ("source_id", "source_revision", "label", "receipt_ref", "saved_at",
                           "state", "read", "content_fetched",
                           "observed_object_ref", "observed_at"),
    ITEM_SOURCE_ACQUISITION: ("source_id", "source_revision", "receipt_ref", "grant_ref",
                              "obtained", "acquired_at", "available", "unavailable_reason",
                              "unavailable_at"),
    ITEM_READING_POSITION: ("source_id", "source_revision", "position", "acquisition_id",
                            "set_at", "understanding"),
    ITEM_UNCLEAR_MARKER: ("source_id", "source_revision", "position", "marked_at"),
    ITEM_INBOX: ("receipt_ref", "from_ref", "kind", "received_at", "state", "read_at",
                 "read_evidence_ref", "co_presence"),
}

#: The private text field of each item type, if it has one.  Types absent here hold no text at all.
_PRIVATE_TEXT_FIELD: dict[str, str] = {
    ITEM_MEMO: "body",
    ITEM_UNCLEAR_MARKER: "note",
    ITEM_INBOX: "summary",
    ITEM_SOURCE_ACQUISITION: "excerpt",
}


# ---------------------------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------------------------


class PrivateDailyItemsError(RuntimeError):
    """Base class for this adapter's own refusals and conflicts."""


class PrivateItemsContractRefused(PrivateDailyItemsError):
    """The call itself is not admissible.  Nothing was sent to the owner."""


class PrivateItemsOwnerRefused(PrivateDailyItemsError):
    """The owner answered with a typed refusal (DENY / BLOCKED / REVOKED / NOT_FOUND)."""

    def __init__(self, status: str, reason: str, detail: str = "", **extra: Any) -> None:
        super().__init__(f"{status}:{reason}:{detail}"[:400])
        self.status = status
        self.reason = reason
        self.detail = detail
        self.extra = dict(extra)


class PrivateItemsConflict(PrivateDailyItemsError):
    """The compare-and-swap precondition no longer holds.  Nothing was overwritten."""


class PrivateItemsVersionMismatch(PrivateDailyItemsError):
    """The artifact head is not the version this document claims to be: someone else wrote it."""


class PrivateItemsUnresolved(PrivateDailyItemsError):
    """An UNKNOWN effect could not be reconciled.  Do not resend; resolve the owner first."""


class PrivateItemsSourceUnavailable(PrivateDailyItemsError):
    """The source's material was revoked or otherwise became unavailable.  No cache revives it."""


# ---------------------------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _require_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PrivateItemsContractRefused(f"{name.upper()}_REQUIRED")
    return value


def _require_optional_text(value: Any, name: str) -> Optional[str]:
    if value is None:
        return None
    return _require_text(value, name)


def _require_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, AbcMapping) or not value:
        raise PrivateItemsContractRefused(f"{name.upper()}_REQUIRED")
    return {str(k): v for k, v in value.items()}


def _require_position(value: Any) -> Any:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise PrivateItemsContractRefused("POSITION_REQUIRED")
    if isinstance(value, str) and not value.strip():
        raise PrivateItemsContractRefused("POSITION_REQUIRED")
    return value


def _clean_reading_anchor(value: Any) -> Optional[dict[str, Any]]:
    """Validate one optional memo anchor.  Unknown fields are refused, never quietly dropped."""
    if value is None:
        return None
    if not isinstance(value, AbcMapping):
        raise PrivateItemsContractRefused("READING_ANCHOR_INVALID")
    unknown = sorted(str(key) for key in value if str(key) not in READING_ANCHOR_FIELDS)
    if unknown:
        raise PrivateItemsContractRefused("READING_ANCHOR_UNKNOWN_FIELD:%s" % ",".join(unknown))
    return {
        "source_id": _require_text(value.get("source_id"), "source_id"),
        "source_revision": _require_text(value.get("source_revision"), "source_revision"),
        "page": _require_position(value.get("page")),
    }


def _empty_document(subject_id: str, artifact_id: Optional[str],
                    workspace_id: Optional[str]) -> dict[str, Any]:
    """The document written as version 1 of a freshly created artifact.

    ``artifact_id`` may be ``None`` at creation time -- the owner mints the id -- in which case the
    document is *unbound* and the first real write binds it.  A bound document whose id disagrees
    with the artifact it is read from is refused, never adopted.
    """
    return {
        "doc_type": DOC_TYPE,
        "schema_version": SCHEMA_VERSION,
        "subject_id": str(subject_id),
        "artifact_id": "" if artifact_id is None else str(artifact_id),
        "workspace_id": None if workspace_id is None else str(workspace_id),
        "owner_version_number": 1,
        "doc_revision": 1,
        "updated_at": _utc_now_iso(),
        "order": [],
        "items": {},
    }


def encode_document(document: Mapping[str, Any]) -> str:
    """Deterministic serialization: sorted keys, compact separators, UTF-8 text."""
    return json.dumps(dict(document), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def decode_document(text: str) -> dict[str, Any]:
    """Decode one stored document.  Anything that is not ours is refused, never guessed at."""
    if not isinstance(text, str) or not text.strip():
        raise PrivateItemsContractRefused("DOCUMENT_EMPTY")
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise PrivateItemsContractRefused(f"DOCUMENT_NOT_JSON:{exc}") from exc
    if not isinstance(raw, dict):
        raise PrivateItemsContractRefused("DOCUMENT_NOT_AN_OBJECT")
    if raw.get("doc_type") != DOC_TYPE:
        raise PrivateItemsContractRefused(
            f"NOT_A_PRIVATE_DAILY_ITEMS_DOCUMENT:{raw.get('doc_type')!r}")
    if not isinstance(raw.get("items"), dict):
        raise PrivateItemsContractRefused("DOCUMENT_ITEMS_INVALID")
    if not isinstance(raw.get("order"), list):
        raw["order"] = list(raw["items"])
    return raw


def plan_page_read(
    document: Mapping[str, Any],
    *,
    artifact_id: Optional[str] = None,
    source_id: str,
    source_revision: str,
    receipt_ref: str,
    obtained: Mapping[str, Any],
    excerpt: Optional[str] = None,
    page: Any = None,
    grant_ref: Optional[str] = None,
    head_version_id: Optional[str] = None,
    head_number: Optional[int] = None,
    now: Optional[str] = None,
) -> dict[str, Any]:
    """Pure: the encoded document for one page read, plus the item ids it would create.

    This is the *planning* half of ``record_source_acquisition`` + ``set_reading_position``,
    with no owner call and no write: the caller hands the encoded content to the owner (through
    the port, or through an AR-0 file action), so the same compare-and-swap, the same operation
    key and the same UNKNOWN discipline apply.  ``page`` is the reading position -- a page
    number or a locator string -- and ``understanding`` stays ``UNKNOWN``: a page is where she
    is, not what she understood.
    """

    stamp = now or _utc_now_iso()
    clean_source = _require_text(source_id, "source_id")
    clean_revision = _require_text(source_revision, "source_revision")
    clean_receipt = _require_text(receipt_ref, "receipt_ref")
    clean_obtained = _require_mapping(obtained, "obtained")
    clean_position = _require_position(page)
    if excerpt is not None:
        if not isinstance(excerpt, str):
            raise PrivateItemsContractRefused("EXCERPT_MUST_BE_TEXT")
        if len(excerpt) > MAX_EXCERPT_CHARS:
            raise PrivateItemsContractRefused(
                f"EXCERPT_TOO_LONG:{len(excerpt)}>{MAX_EXCERPT_CHARS}")

    doc = copy.deepcopy(dict(document))
    # The owner's id comes from the caller: a freshly created document is still *unbound*
    # (``create_document_artifact`` writes ``_empty_document(..., None, ...)``), and ``_commit``
    # keys every write on ``self.artifact_id``, not on the document's own field.  Planning must
    # use the same id, or the planned operation key would not match the one the owner would
    # derive from the same intent.
    artifact_id = _require_text(
        str(artifact_id or doc.get("artifact_id") or ""), "artifact_id")
    acq_key = operation_key_for(DOC_TYPE, artifact_id, ITEM_SOURCE_ACQUISITION,
                                clean_source, clean_revision, clean_receipt)
    acq_id = "%s:%s" % (_ID_PREFIX[ITEM_SOURCE_ACQUISITION], acq_key.rsplit(":", 1)[-1][:16])
    pos_key = operation_key_for(DOC_TYPE, artifact_id, ITEM_READING_POSITION,
                                clean_source, clean_revision, str(clean_position))
    pos_id = "%s:%s" % (_ID_PREFIX[ITEM_READING_POSITION], pos_key.rsplit(":", 1)[-1][:16])
    operation_key = operation_key_for(DOC_TYPE, artifact_id, str(head_version_id or "NO_VERSION"),
                                      "page_read", acq_id, pos_id)
    if acq_id in doc["items"] or pos_id in doc["items"]:
        # The same intent already has its own items: one intended page read, one version.
        return {"changed": False, "content": encode_document(doc), "acquisition_id": acq_id,
                "position_id": pos_id, "operation_key": operation_key}

    _insert(doc, acq_id, {
        "item_id": acq_id, "item_type": ITEM_SOURCE_ACQUISITION, "acquisition_id": acq_id,
        "source_id": clean_source, "source_revision": clean_revision,
        "receipt_ref": clean_receipt, "grant_ref": grant_ref, "obtained": clean_obtained,
        "excerpt": excerpt, "acquired_at": stamp, "available": True, "unavailable_reason": "",
    })
    _insert(doc, pos_id, {
        "item_id": pos_id, "item_type": ITEM_READING_POSITION, "position_id": pos_id,
        "source_id": clean_source, "source_revision": clean_revision,
        "position": clean_position, "acquisition_id": acq_id, "set_at": stamp,
        "understanding": UNDERSTANDING_UNKNOWN,
    })
    if not doc.get("artifact_id"):
        doc["artifact_id"] = artifact_id
    doc["owner_version_number"] = int(head_number or 0) + 1
    doc["doc_revision"] = int(head_number or 0) + 1
    doc["updated_at"] = stamp
    return {"changed": True, "content": encode_document(doc), "acquisition_id": acq_id,
            "position_id": pos_id, "operation_key": operation_key,
            "owner_version_number": doc["owner_version_number"]}


# ---------------------------------------------------------------------------------------------
# reader-response annotation: one pure plan, at most one item
# ---------------------------------------------------------------------------------------------


def _annotation_intent_digest(action: str, *parts: Any) -> str:
    """One annotation's stable request binding: action + target + parameters + version.

    The digest carries **no text** (text only feeds the hash), so it can sit in the same version as
    the effect inside ``reading_operations``.  The shape is the one the phone reading facade already
    writes (``reading-intent:<32 hex>``): one kind of record means one kind of check.
    """
    payload = json.dumps([str(action)] + ["" if part is None else str(part) for part in parts],
                         ensure_ascii=False, separators=(",", ":"))
    return "reading-intent:" + sha256(payload.encode("utf-8")).hexdigest()[:32]


def _cap_annotation_text(value: Any, name: str, limit: int) -> Optional[str]:
    """Strip one optional annotation text; refuse what is not text or is too long."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise PrivateItemsContractRefused("ANNOTATION_FIELD_INVALID:%s" % name)
    text = value.strip()
    if not text:
        return None
    if len(text) > int(limit):
        raise PrivateItemsContractRefused(
            "ANNOTATION_%s_TOO_LONG:%d>%d" % (name.upper(), len(text), int(limit)))
    return text


def _clean_annotation_response(response: Any
                               ) -> tuple[str, Optional[str], Optional[str], Optional[str]]:
    """Validate one reader-response intent into ``(kind, title, body, note)``.

    This module keeps its own copy of the three-kind rule and the two length bounds on purpose: a
    typed-document writer must not import the model-facing parser (no model client in this import
    graph), and a plan that trusted an unvalidated mapping would be a back door for "pseudo text"
    on ``no_change``.  The two copies are pinned equal by
    ``tests/test_subject_reading_annotation_plan.py``.
    """
    if not isinstance(response, AbcMapping):
        raise PrivateItemsContractRefused("ANNOTATION_RESPONSE_REQUIRED")
    unknown = sorted(str(key) for key in response if str(key) not in _ANNOTATION_RESPONSE_FIELDS)
    if unknown:
        raise PrivateItemsContractRefused(
            "ANNOTATION_RESPONSE_UNKNOWN_FIELD:%s" % ",".join(unknown))
    raw_kind = response.get("kind")
    kind = raw_kind.strip().lower() if isinstance(raw_kind, str) else ""
    if kind not in ANNOTATION_KINDS:
        raise PrivateItemsContractRefused("ANNOTATION_KIND_INVALID:%s" % str(raw_kind)[:24])
    title = _cap_annotation_text(response.get("title"), "title", MAX_ANNOTATION_TITLE_CHARS)
    body = _cap_annotation_text(response.get("body"), "body", MAX_ANNOTATION_TEXT_CHARS)
    note = _cap_annotation_text(response.get("note"), "note", MAX_ANNOTATION_TEXT_CHARS)

    if kind == ANNOTATION_NOTE:
        if note is not None:
            raise PrivateItemsContractRefused("ANNOTATION_FIELD_NOT_ALLOWED:%s:note" % kind)
        for name, value in (("title", title), ("body", body)):
            if value is None:
                raise PrivateItemsContractRefused("ANNOTATION_FIELD_MISSING:%s" % name)
        return kind, title, body, None

    if kind == ANNOTATION_UNCLEAR:
        for name, value in (("title", title), ("body", body)):
            if value is not None:
                raise PrivateItemsContractRefused(
                    "ANNOTATION_FIELD_NOT_ALLOWED:%s:%s" % (kind, name))
        if note is None:
            raise PrivateItemsContractRefused("ANNOTATION_FIELD_MISSING:note")
        return kind, None, None, note

    # no_change: no text at all.  A "pseudo body" is refused, never quietly dropped.
    for name, value in (("title", title), ("body", body), ("note", note)):
        if value is not None:
            raise PrivateItemsContractRefused(
                "ANNOTATION_FIELD_NOT_ALLOWED:%s:%s" % (kind, name))
    return kind, None, None, None


def _same_position(left: Any, right: Any) -> bool:
    """Whether two recorded positions are the **same** page.  A bool is never a page."""
    if isinstance(left, bool) or isinstance(right, bool):
        return False
    if not isinstance(left, (int, str)) or not isinstance(right, (int, str)):
        return False
    return left == right


def _page_obtained(acquisition: Mapping[str, Any], page: Any) -> bool:
    """Whether that acquisition's own evidence really covers **this** page.

    Only the ``obtained["pages"]`` list the writer recorded counts (``plan_page_read`` and the
    phone path both write ``{"pages": [N], "chars": L}``); a missing or differently shaped evidence
    mapping is not proof, so it answers ``False`` instead of assuming the whole revision was read.
    """
    obtained = acquisition.get("obtained")
    pages = obtained.get("pages") if isinstance(obtained, AbcMapping) else None
    return isinstance(pages, list) and any(_same_position(value, page) for value in pages)


def _annotation_acquisition(doc: Mapping[str, Any], source_id: str, source_revision: str,
                            page: Any) -> dict[str, Any]:
    """The one acquisition that really covers **this** page on **this** revision.

    The shared ``_available_acquisition`` answers a different question ("may I point a stop point at
    this revision at all?") and hands back the revision's *first* candidate.  That is wrong for a
    page annotation as soon as one revision is read page by page -- page 1 and page 2 are two
    acquisitions, and the first one does not cover page 2 -- or when only part of them is revoked
    (the first candidate may be the revoked one).  So this planner selects its own candidate: right
    item type, right source and revision, really available, and its own ``obtained["pages"]`` really
    covering this page.  The shared helper keeps its old meaning for stop points.
    """
    candidates = [a for a in _acquisitions_for(doc, source_id)
                  if str(a.get("source_revision")) == str(source_revision)]
    if not candidates:
        raise PrivateItemsContractRefused(
            "SOURCE_NOT_ACQUIRED:%s@%s" % (source_id, source_revision))
    available = [a for a in candidates if a.get("available", True)]
    if not available:
        raise PrivateItemsSourceUnavailable(
            "SOURCE_UNAVAILABLE:%s@%s:%s" % (source_id, source_revision,
                                             candidates[0].get("unavailable_reason") or ""))
    # The most recent acquisition that really covers this page wins (document order = write order).
    for acquisition in reversed(available):
        if _page_obtained(acquisition, page):
            return acquisition
    revoked = [a for a in candidates
               if not a.get("available", True) and _page_obtained(a, page)]
    if revoked:
        # This page's own material was revoked: a source problem, not a page-number typo.
        raise PrivateItemsSourceUnavailable(
            "SOURCE_UNAVAILABLE:%s@%s:%s" % (source_id, source_revision,
                                             revoked[0].get("unavailable_reason") or ""))
    raise PrivateItemsContractRefused("PAGE_NOT_ACQUIRED:%r" % (page,))


def _annotation_item_id(kind: str, operation_key: str) -> str:
    """Stable item id from the key **alone**: a retry lands on the same row, never a duplicate."""
    prefix = _ID_PREFIX[ITEM_MEMO] if kind == ANNOTATION_NOTE else _ID_PREFIX[ITEM_UNCLEAR_MARKER]
    digest = sha256(("reading-response|%s|%s" % (kind, operation_key)).encode("utf-8")).hexdigest()
    return "%s:%s" % (prefix, digest[:16])


def _annotation_record_matches(record: Mapping[str, Any], *, intent: str) -> bool:
    """Whether that already-recorded operation is the **same** whole intent.

    A record without an ``intent`` proves nothing, so it refuses: a borrowed key (one the phone
    facade or another writer landed) must never look like this intent.
    """
    recorded = str(record.get("intent") or "")
    return bool(recorded) and recorded == str(intent)


def _annotation_item_matches(item: Mapping[str, Any], *, kind: str, source_id: str,
                             source_revision: str, page: Any, title: Optional[str],
                             body: Optional[str], note: Optional[str]) -> bool:
    """Whether the item this key already minted really holds *this* intent's content."""
    item_type = str(item.get("item_type") or "")
    if kind == ANNOTATION_NOTE:
        anchor = item.get("reading_anchor")
        if item_type != ITEM_MEMO or not isinstance(anchor, AbcMapping):
            return False
        return (str(item.get("title") or "") == str(title or "")
                and str(item.get("body") or "") == str(body or "")
                and str(anchor.get("source_id") or "") == str(source_id)
                and str(anchor.get("source_revision") or "") == str(source_revision)
                and _same_position(anchor.get("page"), page))
    if item_type != ITEM_UNCLEAR_MARKER:
        return False
    return (str(item.get("note") or "") == str(note or "")
            and str(item.get("source_id") or "") == str(source_id)
            and str(item.get("source_revision") or "") == str(source_revision)
            and _same_position(item.get("position"), page))


def plan_page_annotation(
    document: Mapping[str, Any],
    *,
    artifact_id: str,
    source_id: str,
    source_revision: str,
    page: Any,
    response: Mapping[str, Any],
    operation_key: str,
    now: str,
) -> dict[str, Any]:
    """Pure: the encoded next document for **one** annotation, or the same document for a replay.

    Input side.  ``document`` must already carry the real page read (``plan_page_read`` wrote it).
    Against that document this plan re-selects the acquisition **for this page**: right item type,
    right source and revision, really available, and its own ``obtained["pages"]`` really covering
    *this* page (see ``_annotation_acquisition``; the shared ``_available_acquisition`` answers a
    different question -- "may I point a stop point at this revision at all?").  A missing
    acquisition, a foreign revision, a revoked source/page or a page that was never obtained all
    refuse -- nothing is planned, and no note is invented to fill the gap.

    What it may produce.  At most **one** item: a ``memo`` (note, with ``reading_anchor``) or an
    ``unclear_marker``.  ``no_change`` produces zero items and returns the document **byte-identical**
    (``changed`` False, ``item_id`` None).  A stop point is never touched: this plan writes no
    ``reading_position`` and invents no page, and ``understanding`` stays ``UNKNOWN``.

    Idempotency.  ``operation_key`` is the caller's whole-intent key.  The intent digest covers
    action + artifact + source + revision + page + kind + title/body/note and is stored with the
    effect in ``reading_operations`` (hash only, no text).  Re-planning the same key with the same
    intent returns ``changed`` False and the item that already exists; the same key with **any**
    different intent is refused (``READING_OPERATION_KEY_REUSED``), so one key can neither mint a
    second note nor rewrite the first.  ``no_change`` writes no effect at all, so it is accepted
    only under a **brand-new** key: a key that already landed a note or a question is refused,
    because "record nothing" under someone else's key is exactly the intent swap the record forbids.
    The item id is derived from the key alone, so a retry lands on the same row instead of a duplicate.

    No owner call, no socket, no write: the caller hands ``content`` to the owner with its own
    compare-and-swap.  ``owner_version_number``/``doc_revision`` are bumped from the document's own
    field (the bound reader already pins that field to the owner head), like ``plan_page_read``.
    """
    doc = copy.deepcopy(dict(document))
    if not isinstance(doc.get("items"), dict):
        raise PrivateItemsContractRefused("DOCUMENT_ITEMS_INVALID")
    if not isinstance(doc.get("order") if doc.get("order") is not None else [], list):
        raise PrivateItemsContractRefused("DOCUMENT_ORDER_INVALID")
    if doc.get("doc_type") not in (None, DOC_TYPE):
        raise PrivateItemsContractRefused(
            "NOT_A_PRIVATE_DAILY_ITEMS_DOCUMENT:%r" % (doc.get("doc_type"),))
    clean_artifact = _require_text(artifact_id, "artifact_id")
    bound = str(doc.get("artifact_id") or "")
    if bound and bound != clean_artifact:
        raise PrivateItemsContractRefused("DOCUMENT_BOUND_TO_ANOTHER_ARTIFACT")
    clean_source = _require_text(source_id, "source_id")
    clean_revision = _require_text(source_revision, "source_revision")
    clean_page = _require_position(page)
    clean_key = _require_text(operation_key, "operation_key")
    stamp = _require_text(now, "now")
    kind, title, body, note = _clean_annotation_response(response)

    intent: Optional[str] = None
    recorded = reading_operation(doc, clean_key)
    if kind == ANNOTATION_NO_CHANGE:
        # no_change 是"这一页什么都不记"，因此只接受**全新键**：这把键已经落过别人的效果
        # （note/unclear 的意图），再拿它来说"不记录"就是换意图复用，直接拒绝。
        if recorded is not None:
            raise PrivateItemsContractRefused("READING_OPERATION_KEY_REUSED:%s" % clean_key)
    else:
        intent = _annotation_intent_digest(
            "annotation", clean_artifact, clean_source, clean_revision, str(clean_page), kind,
            title, body, note)
        # 1) 原键先回读：已经落过的同一条意图是 no-change，不是重写（也不因后来被撤权而翻脸）。
        if recorded is not None:
            if not _annotation_record_matches(recorded, intent=intent):
                raise PrivateItemsContractRefused("READING_OPERATION_KEY_REUSED:%s" % clean_key)
            item_id = recorded.get("item_id")
            return {"content": encode_document(doc), "changed": False,
                    "item_id": None if item_id is None else str(item_id),
                    "kind": kind, "operation_key": clean_key}

    # 2) 真实取得校验：这一版、这一页真的取得过，而且没有被撤权。
    acquisition = _annotation_acquisition(doc, clean_source, clean_revision, clean_page)

    if kind == ANNOTATION_NO_CHANGE:
        # 什么都不记：文档一字节都不变（不偷偷加批注，也不改停点）。
        return {"content": encode_document(doc), "changed": False, "item_id": None,
                "kind": kind, "operation_key": clean_key}

    item_id = _annotation_item_id(kind, clean_key)
    existing = doc["items"].get(item_id)
    if existing is not None:
        # 归属掉出 200 条窗口时仍能从条目本身认出同一条意图；认不出就是借键。
        if not _annotation_item_matches(existing, kind=kind, source_id=clean_source,
                                        source_revision=clean_revision, page=clean_page,
                                        title=title, body=body, note=note):
            raise PrivateItemsContractRefused("READING_OPERATION_KEY_REUSED:%s" % clean_key)
        return {"content": encode_document(doc), "changed": False, "item_id": item_id,
                "kind": kind, "operation_key": clean_key}

    if kind == ANNOTATION_NOTE:
        _insert(doc, item_id, {
            "item_id": item_id, "item_type": ITEM_MEMO, "memo_id": item_id,
            "title": str(title), "body": str(body), "created_at": stamp, "updated_at": stamp,
            "revisions": 1,
            "reading_anchor": {"source_id": clean_source,
                               "source_revision": clean_revision, "page": clean_page},
        })
    else:
        _insert(doc, item_id, {
            "item_id": item_id, "item_type": ITEM_UNCLEAR_MARKER, "marker_id": item_id,
            "source_id": clean_source, "source_revision": clean_revision,
            "position": clean_page, "note": str(note), "marked_at": stamp,
        })
    if not doc.get("artifact_id"):
        doc["artifact_id"] = clean_artifact
    next_number = int(doc.get("owner_version_number") or 0) + 1
    doc["owner_version_number"] = next_number
    doc["doc_revision"] = next_number
    doc["updated_at"] = stamp
    _remember_operation(doc, clean_key, {
        "operation_key": clean_key, "action": "annotation", "kind": kind,
        "source_id": clean_source, "source_revision": clean_revision, "page": clean_page,
        "intent": intent, "acquisition_id": acquisition.get("acquisition_id"),
        "at": stamp, "item_id": item_id,
    })
    return {"content": encode_document(doc), "changed": True, "item_id": item_id,
            "kind": kind, "operation_key": clean_key}


def reading_position_page(document: Mapping[str, Any], *, source_id: str) -> Optional[int]:
    """The highest page number this document records for one source, or ``None``.

    Only used to pick the *next* page to read; a document without a position has none.
    """

    pages: list[int] = []
    for item in _ordered_items(document):
        if item.get("item_type") != ITEM_READING_POSITION:
            continue
        if str(item.get("source_id")) != str(source_id):
            continue
        position = item.get("position")
        if isinstance(position, int) and not isinstance(position, bool):
            pages.append(position)
    return max(pages) if pages else None


def reading_operation(document: Mapping[str, Any],
                      operation_key: str) -> Optional[dict[str, Any]]:
    """The minimal record of one reading write kept in this document, or ``None``.

    It says which action/target an operation key belonged to.  It is recovery evidence for a
    replay (and the reason a reused key can be refused instead of quietly applied); it holds no
    page text and is not exposed by ``metadata_view``.
    """
    ledger = document.get(READING_OPERATIONS_FIELD) if isinstance(document, AbcMapping) else None
    if not isinstance(ledger, AbcMapping):
        return None
    record = ledger.get(str(operation_key))
    return dict(record) if isinstance(record, AbcMapping) else None


def _remember_operation(document: dict[str, Any], key: str,
                        record: Mapping[str, Any]) -> None:
    """Attach one operation record to the document version that carries the effect."""
    ledger = document.get(READING_OPERATIONS_FIELD)
    if not isinstance(ledger, dict):
        ledger = {}
        document[READING_OPERATIONS_FIELD] = ledger
    ledger[str(key)] = {str(name): value for name, value in dict(record).items()
                        if value is not None}
    while len(ledger) > MAX_READING_OPERATIONS:
        ledger.pop(next(iter(ledger)), None)


def _insert(doc: dict[str, Any], item_id: str, item: dict[str, Any]) -> None:
    """Insert one item into the document, keeping the insertion order."""
    doc["items"][item_id] = item
    order = doc.setdefault("order", [])
    if item_id not in order:
        order.append(item_id)


def _ordered_items(doc: Mapping[str, Any]) -> list[dict[str, Any]]:
    items = doc.get("items") or {}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item_id in doc.get("order") or []:
        if item_id in items and item_id not in seen:
            out.append(items[item_id])
            seen.add(item_id)
    for item_id, item in items.items():
        if item_id not in seen:
            out.append(item)
    return out


def _items_of(doc: Mapping[str, Any], item_type: str) -> list[dict[str, Any]]:
    return [i for i in _ordered_items(doc) if i.get("item_type") == item_type]


def _acquisitions_for(doc: Mapping[str, Any], source_id: str) -> list[dict[str, Any]]:
    return [a for a in _items_of(doc, ITEM_SOURCE_ACQUISITION)
            if str(a.get("source_id")) == str(source_id)]


def _current_source_revision(doc: Mapping[str, Any], source_id: str) -> Optional[str]:
    """The revision of the **most recently acquired** version of that source, from real records.

    Ordering comes from when the acquisition actually happened, not from parsing an arbitrary
    revision string: nothing here invents a "newer revision" the owner never handed over.
    """
    ordered = _ordered_items(doc)
    best: Optional[dict[str, Any]] = None
    best_key: Optional[tuple[str, int]] = None
    for index, item in enumerate(ordered):
        if item.get("item_type") != ITEM_SOURCE_ACQUISITION:
            continue
        if str(item.get("source_id")) != str(source_id):
            continue
        key = (str(item.get("acquired_at") or ""), index)
        if best_key is None or key >= best_key:
            best_key, best = key, item
    return None if best is None else str(best.get("source_revision"))


def _needs_reread(doc: Mapping[str, Any], position: Mapping[str, Any]) -> tuple[bool, str]:
    """Whether a bookmark still belongs to the revision it was made on.

    A position is never silently re-pointed at a newer revision: it keeps its own
    ``source_revision`` and simply says it needs another look.
    """
    source_id = str(position.get("source_id"))
    own = str(position.get("source_revision"))
    acquisitions = _acquisitions_for(doc, source_id)
    own_acq = next((a for a in acquisitions
                    if str(a.get("acquisition_id")) == str(position.get("acquisition_id"))), None)
    if own_acq is not None and not own_acq.get("available", True):
        return True, "SOURCE_UNAVAILABLE"
    current = _current_source_revision(doc, source_id)
    if current is not None and current != own:
        return True, "SOURCE_REVISION_ADVANCED"
    return False, ""


def _available_acquisition(doc: Mapping[str, Any], source_id: str, source_revision: str,
                           acquisition_id: Optional[str]) -> dict[str, Any]:
    candidates = [a for a in _acquisitions_for(doc, source_id)
                  if str(a.get("source_revision")) == str(source_revision)]
    if acquisition_id is not None:
        candidates = [a for a in candidates if str(a.get("acquisition_id")) == str(acquisition_id)]
    if not candidates:
        raise PrivateItemsContractRefused(
            "SOURCE_NOT_ACQUIRED:%s@%s" % (source_id, source_revision))
    unavailable = [a for a in candidates if not a.get("available", True)]
    if len(unavailable) == len(candidates):
        raise PrivateItemsSourceUnavailable(
            "SOURCE_UNAVAILABLE:%s@%s:%s" % (source_id, source_revision,
                                             unavailable[0].get("unavailable_reason") or ""))
    return candidates[0]


# ---------------------------------------------------------------------------------------------
# typed item views (read side; the document itself stays JSON-native)
# ---------------------------------------------------------------------------------------------


@dataclass
class WriteResult:
    """What one write actually did, as the owner answered it (plus any reconciliation)."""

    effect_state: str
    applied: bool
    item_id: Optional[str] = None
    operation_key: Optional[str] = None
    version_id: Optional[str] = None
    version_number: Optional[int] = None
    retryable: bool = False
    reconciled: bool = False
    detail: str = ""
    owner_status: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "effect_state": self.effect_state,
            "applied": self.applied,
            "item_id": self.item_id,
            "operation_key": self.operation_key,
            "version_id": self.version_id,
            "version_number": self.version_number,
            "retryable": self.retryable,
            "reconciled": self.reconciled,
            "detail": self.detail,
            "owner_status": self.owner_status,
        }


@dataclass
class RoomObjectRef:
    """One room object the caller **actually observed**.  Not a placement, not an inventory."""

    object_id: str
    name: str = ""
    kind: str = ""
    note: str = ""
    source: str = "world_observation"

    def to_dict(self) -> dict[str, Any]:
        return {"object_id": self.object_id, "name": self.name, "kind": self.kind,
                "note": self.note, "source": self.source}


@dataclass
class Memo:
    memo_id: str
    title: str
    body: str
    created_at: str
    updated_at: str
    revisions: int
    observed_object_ref: Optional[str] = None

    @classmethod
    def from_item(cls, item: Mapping[str, Any]) -> "Memo":
        return cls(memo_id=str(item["memo_id"]), title=str(item.get("title") or ""),
                   body=str(item.get("body") or ""), created_at=str(item.get("created_at") or ""),
                   updated_at=str(item.get("updated_at") or ""),
                   revisions=int(item.get("revisions") or 0),
                   observed_object_ref=item.get("observed_object_ref"))


@dataclass
class SavedReference:
    reference_id: str
    source_id: str
    source_revision: str
    label: str
    receipt_ref: str
    saved_at: str
    locator: Optional[str] = None
    read: bool = False
    content_fetched: bool = False

    @classmethod
    def from_item(cls, item: Mapping[str, Any]) -> "SavedReference":
        return cls(reference_id=str(item["reference_id"]), source_id=str(item["source_id"]),
                   source_revision=str(item["source_revision"]), label=str(item.get("label") or ""),
                   receipt_ref=str(item.get("receipt_ref") or ""),
                   saved_at=str(item.get("saved_at") or ""), locator=item.get("locator"),
                   read=bool(item.get("read")), content_fetched=bool(item.get("content_fetched")))


@dataclass
class SourceAcquisition:
    acquisition_id: str
    source_id: str
    source_revision: str
    receipt_ref: str
    obtained: dict[str, Any]
    acquired_at: str
    grant_ref: Optional[str] = None
    excerpt: Optional[str] = None
    available: bool = True
    unavailable_reason: str = ""

    @classmethod
    def from_item(cls, item: Mapping[str, Any]) -> "SourceAcquisition":
        return cls(acquisition_id=str(item["acquisition_id"]), source_id=str(item["source_id"]),
                   source_revision=str(item["source_revision"]),
                   receipt_ref=str(item.get("receipt_ref") or ""),
                   obtained=dict(item.get("obtained") or {}),
                   acquired_at=str(item.get("acquired_at") or ""),
                   grant_ref=item.get("grant_ref"), excerpt=item.get("excerpt"),
                   available=bool(item.get("available", True)),
                   unavailable_reason=str(item.get("unavailable_reason") or ""))


@dataclass
class ReadingPosition:
    position_id: str
    source_id: str
    source_revision: str
    position: Any
    acquisition_id: str
    set_at: str
    #: Always ``UNKNOWN``: a page number is not evidence of understanding.
    understanding: str = UNDERSTANDING_UNKNOWN
    needs_reread: bool = False
    needs_reread_reason: str = ""

    @classmethod
    def from_item(cls, item: Mapping[str, Any], *, needs_reread: bool = False,
                  needs_reread_reason: str = "") -> "ReadingPosition":
        return cls(position_id=str(item["position_id"]), source_id=str(item["source_id"]),
                   source_revision=str(item["source_revision"]), position=item.get("position"),
                   acquisition_id=str(item.get("acquisition_id") or ""),
                   set_at=str(item.get("set_at") or ""),
                   understanding=str(item.get("understanding") or UNDERSTANDING_UNKNOWN),
                   needs_reread=needs_reread, needs_reread_reason=needs_reread_reason)


@dataclass
class UnclearMarker:
    marker_id: str
    source_id: str
    source_revision: str
    note: str
    marked_at: str
    position: Any = None

    @classmethod
    def from_item(cls, item: Mapping[str, Any]) -> "UnclearMarker":
        return cls(marker_id=str(item["marker_id"]), source_id=str(item["source_id"]),
                   source_revision=str(item["source_revision"]),
                   note=str(item.get("note") or ""), marked_at=str(item.get("marked_at") or ""),
                   position=item.get("position"))


@dataclass
class InboxItem:
    item_id: str
    receipt_ref: str
    from_ref: str
    kind: str
    received_at: str
    state: str = INBOX_UNREAD
    read_at: Optional[str] = None
    read_evidence_ref: Optional[str] = None
    summary: Optional[str] = None
    #: A receipt arriving is not a co-presence record.  This prototype owns no such record.
    co_presence: bool = False

    @classmethod
    def from_item(cls, item: Mapping[str, Any]) -> "InboxItem":
        return cls(item_id=str(item["item_id"]), receipt_ref=str(item.get("receipt_ref") or ""),
                   from_ref=str(item.get("from_ref") or ""), kind=str(item.get("kind") or ""),
                   received_at=str(item.get("received_at") or ""),
                   state=str(item.get("state") or INBOX_UNREAD), read_at=item.get("read_at"),
                   read_evidence_ref=item.get("read_evidence_ref"), summary=item.get("summary"),
                   co_presence=bool(item.get("co_presence")))


# ---------------------------------------------------------------------------------------------
# World observation seam: refs, not placement
# ---------------------------------------------------------------------------------------------


def room_object_refs(observation: Mapping[str, Any]) -> list[RoomObjectRef]:
    """Typed view over the room objects a caller actually observed in a World projection.

    Reads the World observation's own ``local_environment.furniture`` list (what
    ``world_living_skeleton.observe_world`` returns), and tolerates a plain ``objects`` list.
    Only entries that are really there are returned: an unobserved object cannot be referenced.
    """
    if not isinstance(observation, AbcMapping):
        raise PrivateItemsContractRefused("OBSERVATION_REQUIRED")
    rows: Any = None
    environment = observation.get("local_environment")
    if isinstance(environment, AbcMapping):
        rows = environment.get("furniture")
    if rows is None:
        rows = observation.get("objects")
    if not isinstance(rows, AbcSequence) or isinstance(rows, (str, bytes)):
        return []
    refs: list[RoomObjectRef] = []
    for row in rows:
        if not isinstance(row, AbcMapping):
            continue
        object_id = row.get("object_id") or row.get("id")
        if not isinstance(object_id, str) or not object_id:
            continue
        refs.append(RoomObjectRef(object_id=object_id, name=str(row.get("name") or ""),
                                  kind=str(row.get("kind") or ""),
                                  note=str(row.get("note") or "")))
    return refs


def create_document_artifact(port: LifeArtifactPort, *, workspace_id: str, title: str,
                             grant_id: str, operation_key: Optional[str] = None
                             ) -> tuple[str, ArtifactOutcome]:
    """Create the private-daily-items artifact through the port's own ``create_artifact``.

    Returns ``(artifact_id, outcome)``.  The first version is the empty typed document, so a
    later reader can tell "this is our document" from "this is a work of art" without guessing.
    """
    key = operation_key or operation_key_for(DOC_TYPE, "create", str(port.subject_id),
                                             str(workspace_id), str(title))
    outcome = port.create_artifact(
        workspace_id=str(workspace_id), title=str(title),
        content=encode_document(_empty_document(port.subject_id, None, str(workspace_id))),
        grant_id=str(grant_id), operation_key=key)
    if not outcome.applied or not outcome.artifact_id:
        raise PrivateItemsOwnerRefused(outcome.status, outcome.reason, outcome.detail)
    return str(outcome.artifact_id), outcome


# ---------------------------------------------------------------------------------------------
# the adapter
# ---------------------------------------------------------------------------------------------


class PrivateDailyItems:
    """One subject's typed private daily items, persisted as one ManagedArtifact.

    Explicit binding only: the caller names the ``artifact_id``.  ``latest_resumable`` is never
    used, so a private-items write can never land on the work of art by accident.
    """

    def __init__(self, port: LifeArtifactPort, artifact_id: str, *,
                 workspace_id: Optional[str] = None, grant_id: Optional[str] = None,
                 clock: Optional[Callable[[], str]] = None) -> None:
        if not isinstance(port, LifeArtifactPort):
            raise PrivateItemsContractRefused("LIFE_ARTIFACT_PORT_REQUIRED")
        self.port = port
        self.artifact_id = str(artifact_id or "").strip()
        if not self.artifact_id:
            raise PrivateItemsContractRefused("ARTIFACT_ID_REQUIRED")
        self.workspace_id = None if workspace_id is None else str(workspace_id)
        self.grant_id = None if grant_id is None else str(grant_id)
        self._clock = clock or _utc_now_iso
        self._doc: Optional[dict[str, Any]] = None
        self._head_version_id: Optional[str] = None
        self._head_version_number: Optional[int] = None

    # -- binding / reading ---------------------------------------------------------------

    @property
    def subject_id(self) -> str:
        return self.port.subject_id

    def _now(self) -> str:
        return str(self._clock())

    def _read_document(self) -> tuple[dict[str, Any], dict[str, Any], Optional[str], Optional[int]]:
        """Read the bound artifact's head and decode it.  Never scans, never guesses."""
        view = self.port.read_artifact(self.artifact_id)
        artifact = dict(view.get("artifact") or {})
        if str(artifact.get("subject_id") or "") != self.subject_id:
            raise PrivateItemsContractRefused("ARTIFACT_SUBJECT_MISMATCH")
        head = view.get("head") or {}
        version_id = head.get("version_id")
        version_number = head.get("version_number")
        content = view.get("content")
        if version_id is None:
            return (_empty_document(self.subject_id, self.artifact_id, self.workspace_id),
                    artifact, None, None)
        if content is None:
            raise PrivateItemsContractRefused("ARTIFACT_CONTENT_UNAVAILABLE")
        document = decode_document(content)
        bound = str(document.get("artifact_id") or "")
        if bound and bound != self.artifact_id:
            raise PrivateItemsContractRefused("DOCUMENT_BOUND_TO_ANOTHER_ARTIFACT")
        if str(document.get("subject_id")) != self.subject_id:
            raise PrivateItemsContractRefused("DOCUMENT_BOUND_TO_ANOTHER_SUBJECT")
        if int(document.get("owner_version_number") or 0) != int(version_number or 0):
            raise PrivateItemsVersionMismatch(
                "DOCUMENT_VERSION_MISMATCH:document=%s,head=%s"
                % (document.get("owner_version_number"), version_number))
        return document, artifact, str(version_id), int(version_number or 0)

    def bind(self) -> dict[str, Any]:
        """Read the artifact's identity from the owner.  Raises if it is not our document."""
        document, artifact, version_id, version_number = self._read_document()
        return {
            "artifact_id": self.artifact_id,
            "subject_id": self.subject_id,
            "workspace_id": artifact.get("workspace_id"),
            "title": artifact.get("title"),
            "lifecycle": artifact.get("lifecycle"),
            "head_version_id": version_id,
            "version_number": version_number,
            "doc_type": document.get("doc_type"),
            "doc_artifact_binding": document.get("artifact_id") or "",
            "item_count": len(document.get("items") or {}),
        }

    def load(self, *, refresh: bool = False) -> dict[str, Any]:
        """The decoded document.  Cached after the first read; ``refresh`` re-reads the owner."""
        if refresh or self._doc is None:
            document, _artifact, version_id, version_number = self._read_document()
            self._doc, self._head_version_id, self._head_version_number = (
                document, version_id, version_number)
        return self._doc

    # -- metadata (no private text) ------------------------------------------------------

    def _metadata_row(self, document: Mapping[str, Any], item: Mapping[str, Any]) -> dict[str, Any]:
        item_type = str(item.get("item_type") or "")
        row: dict[str, Any] = {"item_id": item.get("item_id"), "item_type": item_type}
        for name in _METADATA_FIELDS.get(item_type, ()):
            if name in item:
                row[name] = item[name]
        if item_type == ITEM_MEMO:
            row["has_text"] = bool(item.get("body"))
        elif item_type == ITEM_SAVED_REFERENCE:
            row["has_locator"] = item.get("locator") is not None
        elif item_type == ITEM_SOURCE_ACQUISITION:
            row["has_excerpt"] = bool(item.get("excerpt"))
        elif item_type == ITEM_UNCLEAR_MARKER:
            row["has_note"] = bool(item.get("note"))
        elif item_type == ITEM_INBOX:
            row["has_summary"] = bool(item.get("summary"))
        elif item_type == ITEM_READING_POSITION:
            needs, reason = _needs_reread(document, item)
            row["needs_reread"] = needs
            row["needs_reread_reason"] = reason
        return row

    def items(self, item_type: Optional[str] = None) -> list[dict[str, Any]]:
        """Metadata rows.  Never contains private text."""
        document = self.load()
        rows = _ordered_items(document)
        if item_type is not None:
            rows = [i for i in rows if i.get("item_type") == item_type]
        return [self._metadata_row(document, item) for item in rows]

    def metadata_view(self) -> dict[str, Any]:
        """The default read: structure, provenance and state, with **no** private text."""
        document = self.load()
        rows = self.items()
        counts = {name: 0 for name in ITEM_TYPES}
        for row in rows:
            counts[str(row.get("item_type"))] = counts.get(str(row.get("item_type")), 0) + 1
        return {
            "doc_type": document.get("doc_type"),
            "schema_version": document.get("schema_version"),
            "artifact_id": document.get("artifact_id"),
            "subject_id": document.get("subject_id"),
            "workspace_id": document.get("workspace_id"),
            "owner_version_number": document.get("owner_version_number"),
            "doc_revision": document.get("doc_revision"),
            "updated_at": document.get("updated_at"),
            "counts": counts,
            "unread_count": sum(1 for r in rows
                                if r.get("item_type") == ITEM_INBOX
                                and r.get("state") == INBOX_UNREAD),
            "needs_reread_count": sum(1 for r in rows if r.get("needs_reread")),
            "items": rows,
            "private_text_included": False,
        }

    def snapshot(self) -> dict[str, Any]:
        """One text-free view of the document: metadata, unread and needs-reread."""
        return {"metadata": self.metadata_view(),
                "unread": self.unread_items(),
                "needs_reread": self.needs_reread_positions(),
                "private_text_included": False}

    def unread_items(self) -> list[dict[str, Any]]:
        """Metadata rows for the unread inbox items.  No summary text."""
        document = self.load()
        return [self._metadata_row(document, item)
                for item in _items_of(document, ITEM_INBOX)
                if str(item.get("state")) == INBOX_UNREAD]

    def needs_reread_positions(self) -> list[dict[str, Any]]:
        """Metadata rows for the bookmarks that no longer match their source revision."""
        document = self.load()
        return [self._metadata_row(document, item)
                for item in _items_of(document, ITEM_READING_POSITION)
                if _needs_reread(document, item)[0]]

    def item(self, item_id: str) -> dict[str, Any]:
        item = (self.load().get("items") or {}).get(str(item_id))
        if item is None:
            raise PrivateItemsContractRefused(f"ITEM_NOT_FOUND:{item_id}")
        return dict(item)

    # -- explicit private-text access ----------------------------------------------------

    def full_text(self, item_id: str) -> str:
        """Explicit access to one item's own text.  Never implicit, never in ``metadata_view``.

        A saved reference holds **no** text at all (its source was never fetched), and a reading
        position is a position, not text: both are refused here rather than answered with
        something that only looks like content.
        """
        item = self.item(item_id)
        item_type = str(item.get("item_type") or "")
        if item_type == ITEM_SOURCE_ACQUISITION:
            return self.source_excerpt(str(item.get("acquisition_id")))
        field_name = _PRIVATE_TEXT_FIELD.get(item_type)
        if field_name is None:
            if item_type == ITEM_SAVED_REFERENCE:
                raise PrivateItemsContractRefused(
                    "SAVED_REFERENCE_HOLDS_NO_TEXT:the source was never fetched")
            raise PrivateItemsContractRefused(f"ITEM_HOLDS_NO_TEXT:{item_type}")
        return str(item.get(field_name) or "")

    def source_excerpt(self, acquisition_id: str, *, max_chars: Optional[int] = None) -> str:
        """The material actually obtained for one acquisition -- refused once it is unavailable."""
        document = self.load()
        item = next((a for a in _items_of(document, ITEM_SOURCE_ACQUISITION)
                     if str(a.get("acquisition_id")) == str(acquisition_id)), None)
        if item is None:
            raise PrivateItemsContractRefused(f"ACQUISITION_NOT_FOUND:{acquisition_id}")
        if not item.get("available", True):
            raise PrivateItemsSourceUnavailable(
                "SOURCE_UNAVAILABLE:%s@%s:%s" % (item.get("source_id"), item.get("source_revision"),
                                                 item.get("unavailable_reason") or ""))
        text = str(item.get("excerpt") or "")
        if max_chars is not None:
            if not isinstance(max_chars, int) or max_chars <= 0:
                raise PrivateItemsContractRefused("MAX_CHARS_INVALID")
            return text[:max_chars]
        return text

    def disclosure_excerpt(self, item_id: str, *, max_chars: int = MAX_EXCERPT_CHARS
                           ) -> dict[str, Any]:
        """A bounded excerpt for a disclosure, explicitly marked as an excerpt.

        The source-unavailable rule still applies: a revoked source is not revived by an excerpt.
        """
        if not isinstance(max_chars, int) or max_chars <= 0:
            raise PrivateItemsContractRefused("MAX_CHARS_INVALID")
        text = self.full_text(item_id)
        return {"item_id": str(item_id), "excerpt": text[:max_chars],
                "chars": len(text[:max_chars]), "truncated": len(text) > max_chars,
                "is_excerpt": True}

    # -- the write core ------------------------------------------------------------------

    def _commit(self, item_type: str, intent_parts: Sequence[Any], mutator: Callable[..., Any],
                *, operation_key: Optional[str] = None,
                record: Optional[Mapping[str, Any]] = None,
                expected_head: Optional[str] = None) -> WriteResult:
        """Read head, apply the mutation in memory, then commit it with a CAS precondition.

        The operation key is derived from the *intent* (kind + payload + the head it was planned
        against), so a retry of the same intent reuses the same key.  The owner keys idempotency on
        that key: an identical retry is replayed (one version, never two), while a *different*
        payload under the same key is refused with CONFLICT -- either way one intended edit cannot
        mint a second version.  A caller retrying a ``retryable`` result must therefore pass the
        same content (including the same ``now``) or the same explicit ``operation_key``.

        ``record`` is optional minimal operation ownership (which action/target a key belonged to).
        It is written into the **same version** as the effect, because a second version for the
        bookkeeping would be exactly the half-success this module refuses to fake.

        ``expected_head`` is the caller's own compare-and-swap precondition.  When it is given it
        is what the **owner** checks (and it is also checked locally first), so a head that moves
        between a caller's pre-read and this commit is refused instead of being silently accepted
        against the newer head.  ``None`` keeps the historical behaviour: CAS against the head
        read right here.
        """
        if self.grant_id is None:
            raise PrivateItemsContractRefused("GRANT_ID_REQUIRED_FOR_WRITES")
        document, _artifact, head_version_id, head_number = self._read_document()
        if expected_head is not None and str(expected_head) != str(head_version_id or ""):
            raise PrivateItemsConflict(
                "CAS_CONFLICT:stale expected_head:%s" % str(operation_key or ""))
        # Empty parts only ever come from an *absent* optional id; dropping them keeps the same
        # intent mapped to the same key while never producing an empty key part.
        parts = [str(part) for part in intent_parts if str(part) != ""]
        key = operation_key or operation_key_for(
            DOC_TYPE, self.artifact_id, str(head_version_id or "NO_VERSION"), item_type, *parts)
        # The item id is derived from the *intent* alone (not from the head), so a redelivered
        # receipt or a repeated identical intent finds its own item instead of minting a second one.
        intent_digest = operation_key_for(DOC_TYPE, self.artifact_id, item_type, *parts)
        auto_id = "%s:%s" % (_ID_PREFIX[item_type], intent_digest.rsplit(":", 1)[-1][:16])
        new_document, changed, result_item_id = mutator(document, auto_id)
        if not changed:
            return WriteResult(effect_state=EffectState.NO_CHANGE, applied=False,
                               item_id=result_item_id or auto_id, operation_key=key,
                               detail="NOTHING_TO_WRITE")
        if not new_document.get("artifact_id"):
            # An unbound document binds itself on its first real write.
            new_document["artifact_id"] = self.artifact_id
        new_document["owner_version_number"] = int(head_number or 0) + 1
        new_document["doc_revision"] = int(head_number or 0) + 1
        new_document["updated_at"] = self._now()
        if record:
            _remember_operation(new_document, key, record)
        outcome = self.port.commit_version(
            artifact_id=self.artifact_id,
            expected_head=head_version_id if expected_head is None else str(expected_head),
            content=encode_document(new_document), grant_id=self.grant_id, operation_key=key)
        return self._settle(outcome, key, new_document, result_item_id or auto_id)

    def _settle(self, outcome: ArtifactOutcome, key: str, new_document: dict[str, Any],
                item_id: str) -> WriteResult:
        state = outcome.effect_state
        if state == EffectState.APPLIED:
            self._doc = new_document
            self._head_version_id = outcome.version_id
            self._head_version_number = outcome.version_number
            return WriteResult(effect_state=state, applied=True, item_id=item_id,
                               operation_key=key, version_id=outcome.version_id,
                               version_number=outcome.version_number,
                               owner_status=outcome.status)
        if state == EffectState.CONFLICT:
            raise PrivateItemsConflict(
                "CAS_CONFLICT:%s:%s" % (outcome.detail or outcome.reason, key))
        if state == EffectState.NO_CHANGE:
            return WriteResult(effect_state=state, applied=False, item_id=item_id,
                               operation_key=key, retryable=False,
                               detail="OWNER_NO_CHANGE", owner_status=outcome.status)
        if state in (EffectState.REFUSED, EffectState.NOT_FOUND):
            raise PrivateItemsOwnerRefused(outcome.status, outcome.reason, outcome.detail,
                                           operation_key=key)
        # UNKNOWN: ask the owner what became of *this* operation, then never resend blindly.
        resolved = self.port.resolve_operation(key)
        if resolved.effect_state == EffectState.APPLIED:
            document, _artifact, version_id, version_number = self._read_document()
            self._doc, self._head_version_id, self._head_version_number = (
                document, version_id, version_number)
            return WriteResult(effect_state=EffectState.APPLIED, applied=True, item_id=item_id,
                               operation_key=key, version_id=version_id,
                               version_number=version_number, reconciled=True,
                               detail="RECONCILED_AFTER_UNKNOWN", owner_status=outcome.status)
        if resolved.effect_state == EffectState.NO_CHANGE:
            return WriteResult(effect_state=EffectState.NO_CHANGE, applied=False, item_id=item_id,
                               operation_key=key, retryable=True, reconciled=True,
                               detail="UNKNOWN_RESOLVED_NEVER_LANDED",
                               owner_status=outcome.status)
        raise PrivateItemsUnresolved(
            "UNKNOWN_NOT_RESOLVED:%s:%s" % (key, resolved.reason or resolved.status))

    # -- memos ---------------------------------------------------------------------------

    def add_memo(self, *, title: str, body: str, memo_id: Optional[str] = None,
                 operation_key: Optional[str] = None, now: Optional[str] = None,
                 reading_anchor: Optional[Mapping[str, Any]] = None,
                 record: Optional[Mapping[str, Any]] = None,
                 expected_head: Optional[str] = None) -> WriteResult:
        """Write a private memo.  ``reading_anchor`` names the real page it was written about.

        The anchor is optional and structural (source id, revision, page): it says *where* the note
        was made, never what was understood, and it is metadata -- ``revise_memo`` only rewrites the
        body, so editing an old note cannot erase its anchor.
        """
        stamp = now or self._now()
        clean_title, clean_body = _require_text(title, "title"), _require_text(body, "body")
        clean_anchor = _clean_reading_anchor(reading_anchor)

        def mutate(document: dict[str, Any], auto_id: str) -> tuple[dict[str, Any], bool, str]:
            item_id = memo_id or auto_id
            if item_id in document["items"]:
                return document, False, item_id
            item: dict[str, Any] = {
                "item_id": item_id, "item_type": ITEM_MEMO, "memo_id": item_id,
                "title": clean_title, "body": clean_body, "created_at": stamp,
                "updated_at": stamp, "revisions": 1,
            }
            if clean_anchor is not None:
                item["reading_anchor"] = clean_anchor
            _insert(document, item_id, item)
            return document, True, item_id

        return self._commit(ITEM_MEMO, ("add", memo_id or "", clean_title, clean_body), mutate,
                            operation_key=operation_key, record=record,
                            expected_head=expected_head)

    def revise_memo(self, memo_id: str, *, body: str, operation_key: Optional[str] = None,
                    now: Optional[str] = None) -> WriteResult:
        stamp = now or self._now()
        clean_body = _require_text(body, "body")

        def mutate(document: dict[str, Any], auto_id: str) -> tuple[dict[str, Any], bool, str]:
            item = document["items"].get(str(memo_id))
            if item is None or item.get("item_type") != ITEM_MEMO:
                raise PrivateItemsContractRefused(f"MEMO_NOT_FOUND:{memo_id}")
            if str(item.get("body")) == clean_body:
                return document, False, str(memo_id)
            item["body"] = clean_body
            item["updated_at"] = stamp
            item["revisions"] = int(item.get("revisions") or 0) + 1
            return document, True, str(memo_id)

        return self._commit(ITEM_MEMO, ("revise", memo_id, clean_body), mutate,
                            operation_key=operation_key)

    # -- saved references (never fetch, never mark read) ----------------------------------

    def save_reference(self, *, source_id: str, source_revision: str, label: str,
                       receipt_ref: str, locator: Optional[str] = None,
                       reference_id: Optional[str] = None,
                       operation_key: Optional[str] = None,
                       now: Optional[str] = None) -> WriteResult:
        """Save a pointer to a source.  No full text is fetched and nothing becomes read."""
        stamp = now or self._now()
        clean_source = _require_text(source_id, "source_id")
        clean_revision = _require_text(source_revision, "source_revision")
        clean_label = _require_text(label, "label")
        clean_receipt = _require_text(receipt_ref, "receipt_ref")
        clean_locator = _require_optional_text(locator, "locator")

        def mutate(document: dict[str, Any], auto_id: str) -> tuple[dict[str, Any], bool, str]:
            item_id = reference_id or auto_id
            if item_id in document["items"]:
                return document, False, item_id
            _insert(document, item_id, {
                "item_id": item_id, "item_type": ITEM_SAVED_REFERENCE, "reference_id": item_id,
                "source_id": clean_source, "source_revision": clean_revision,
                "label": clean_label, "locator": clean_locator, "receipt_ref": clean_receipt,
                "saved_at": stamp, "state": REFERENCE_SAVED, "read": False,
                "content_fetched": False,
            })
            return document, True, item_id

        return self._commit(ITEM_SAVED_REFERENCE,
                            ("save", reference_id or "", clean_source, clean_revision, clean_label),
                            mutate, operation_key=operation_key)

    # -- acquisitions (the evidence of actually obtaining material) ------------------------

    def record_source_acquisition(self, *, source_id: str, source_revision: str, receipt_ref: str,
                                  obtained: Mapping[str, Any], excerpt: Optional[str] = None,
                                  grant_ref: Optional[str] = None,
                                  acquisition_id: Optional[str] = None,
                                  operation_key: Optional[str] = None,
                                  now: Optional[str] = None) -> WriteResult:
        """Record what was **actually obtained**, with its receipt and range/page evidence.

        ``obtained`` is the caller's own evidence (e.g. ``{"pages": [1, 2, 3]}``).  An empty
        evidence mapping is refused: an acquisition with nothing obtained is not an acquisition.
        """
        stamp = now or self._now()
        clean_source = _require_text(source_id, "source_id")
        clean_revision = _require_text(source_revision, "source_revision")
        clean_receipt = _require_text(receipt_ref, "receipt_ref")
        clean_obtained = _require_mapping(obtained, "obtained")
        if excerpt is not None:
            if not isinstance(excerpt, str):
                raise PrivateItemsContractRefused("EXCERPT_MUST_BE_TEXT")
            if len(excerpt) > MAX_EXCERPT_CHARS:
                raise PrivateItemsContractRefused(
                    f"EXCERPT_TOO_LONG:{len(excerpt)}>{MAX_EXCERPT_CHARS}")

        def mutate(document: dict[str, Any], auto_id: str) -> tuple[dict[str, Any], bool, str]:
            item_id = acquisition_id or auto_id
            if item_id in document["items"]:
                return document, False, item_id
            _insert(document, item_id, {
                "item_id": item_id, "item_type": ITEM_SOURCE_ACQUISITION,
                "acquisition_id": item_id, "source_id": clean_source,
                "source_revision": clean_revision, "receipt_ref": clean_receipt,
                "grant_ref": grant_ref, "obtained": clean_obtained, "excerpt": excerpt,
                "acquired_at": stamp, "available": True, "unavailable_reason": "",
            })
            return document, True, item_id

        return self._commit(ITEM_SOURCE_ACQUISITION,
                            ("acquire", acquisition_id or "", clean_source, clean_revision,
                             clean_receipt), mutate, operation_key=operation_key)

    def mark_source_unavailable(self, *, source_id: str, source_revision: str, reason: str,
                                operation_key: Optional[str] = None,
                                now: Optional[str] = None) -> WriteResult:
        """Revoke a source revision's material.  Every later read of it is refused."""
        stamp = now or self._now()
        clean_source = _require_text(source_id, "source_id")
        clean_revision = _require_text(source_revision, "source_revision")
        clean_reason = _require_text(reason, "reason")

        def mutate(document: dict[str, Any], auto_id: str) -> tuple[dict[str, Any], bool, str]:
            targets = [a for a in _acquisitions_for(document, clean_source)
                       if str(a.get("source_revision")) == clean_revision]
            if not targets:
                raise PrivateItemsContractRefused(
                    "SOURCE_NOT_ACQUIRED:%s@%s" % (clean_source, clean_revision))
            changed = False
            for item in targets:
                if item.get("available", True):
                    item["available"] = False
                    item["unavailable_reason"] = clean_reason
                    item["unavailable_at"] = stamp
                    changed = True
            return document, changed, targets[0]["acquisition_id"]

        return self._commit(ITEM_SOURCE_ACQUISITION,
                            ("revoke", clean_source, clean_revision, clean_reason), mutate,
                            operation_key=operation_key)

    # -- reading position (a position, never comprehension) --------------------------------

    def set_reading_position(self, *, source_id: str, source_revision: str, position: Any,
                             acquisition_id: Optional[str] = None,
                             operation_key: Optional[str] = None,
                             now: Optional[str] = None) -> WriteResult:
        """Save where she actually is.  Requires an acquisition of that exact source revision."""
        stamp = now or self._now()
        clean_source = _require_text(source_id, "source_id")
        clean_revision = _require_text(source_revision, "source_revision")
        clean_position = _require_position(position)

        def mutate(document: dict[str, Any], auto_id: str) -> tuple[dict[str, Any], bool, str]:
            acquisition = _available_acquisition(document, clean_source, clean_revision,
                                                 acquisition_id)
            item_id = auto_id
            if item_id in document["items"]:
                return document, False, item_id
            _insert(document, item_id, {
                "item_id": item_id, "item_type": ITEM_READING_POSITION, "position_id": item_id,
                "source_id": clean_source, "source_revision": clean_revision,
                "position": clean_position, "acquisition_id": acquisition["acquisition_id"],
                "set_at": stamp, "understanding": UNDERSTANDING_UNKNOWN,
            })
            return document, True, item_id

        return self._commit(ITEM_READING_POSITION,
                            ("position", clean_source, clean_revision, str(clean_position)),
                            mutate, operation_key=operation_key)

    def record_page_read(self, *, source_id: str, source_revision: str, receipt_ref: str,
                         obtained: Mapping[str, Any], position: Any, excerpt: Optional[str] = None,
                         grant_ref: Optional[str] = None, operation_key: Optional[str] = None,
                         now: Optional[str] = None,
                         record: Optional[Mapping[str, Any]] = None,
                         expected_head: Optional[str] = None) -> dict[str, Any]:
        """One page read = **one** version: the acquisition and the position land together.

        Planning is ``plan_page_read`` (the same pure plan the file-action path uses), so the
        acquisition id, the position id and the "one intent, one version" rule cannot drift
        between the two callers.  ``record`` is the minimal operation ownership this facade keeps
        next to the effect (still inside this document, still one version); it never carries text.

        Returns ``{"write": WriteResult, "acquisition_id", "position_id"}``.  A half-success is not
        representable: either the version holds both items, or it does not hold them at all.
        """

        stamp = now or self._now()
        clean_source = _require_text(source_id, "source_id")
        clean_revision = _require_text(source_revision, "source_revision")
        clean_receipt = _require_text(receipt_ref, "receipt_ref")
        clean_obtained = _require_mapping(obtained, "obtained")
        clean_position = _require_position(position)
        planned_ids: dict[str, Any] = {}
        # 这份 record 会写进**同一个版本**，所以它在这里被补上这次真正用到的条目 id：
        # 重放时"这个键属于哪一条取得/位置"有据可查，而不是靠再算一次。
        stored_record = dict(record) if record else None

        def mutate(document: dict[str, Any], auto_id: str) -> tuple[dict[str, Any], bool, str]:
            plan = plan_page_read(
                document, artifact_id=self.artifact_id, source_id=clean_source,
                source_revision=clean_revision, receipt_ref=clean_receipt,
                obtained=clean_obtained, excerpt=excerpt, page=clean_position,
                grant_ref=grant_ref, now=stamp)
            planned_ids["acquisition_id"] = plan["acquisition_id"]
            planned_ids["position_id"] = plan["position_id"]
            if stored_record is not None:
                stored_record["acquisition_id"] = plan["acquisition_id"]
                stored_record["position_id"] = plan["position_id"]
            if not plan["changed"]:
                return document, False, str(plan["position_id"])
            return decode_document(plan["content"]), True, str(plan["position_id"])

        write = self._commit(ITEM_READING_POSITION,
                            ("page_read", clean_source, clean_revision, clean_receipt,
                             str(clean_position)),
                            mutate, operation_key=operation_key, record=stored_record,
                            expected_head=expected_head)
        return {"write": write, "acquisition_id": planned_ids.get("acquisition_id"),
                "position_id": planned_ids.get("position_id")}

    # -- unclear marker (subjective, separate from the position) --------------------------

    def mark_unclear(self, *, source_id: str, source_revision: str, note: str,
                     position: Any = None, operation_key: Optional[str] = None,
                     now: Optional[str] = None,
                     record: Optional[Mapping[str, Any]] = None,
                     marker_id: Optional[str] = None,
                     expected_head: Optional[str] = None) -> WriteResult:
        """Record "I read this and did not understand it".  A different item from the position.

        ``marker_id`` is optional: a caller that keys its own idempotency (the phone facade keys it
        by operation key) passes the id it derived, so the *same* sentence marked on two different
        pages stays two records instead of collapsing into one.  Without it the historical
        intent-derived id is used, so every existing caller keeps its current behaviour.
        """
        stamp = now or self._now()
        clean_source = _require_text(source_id, "source_id")
        clean_revision = _require_text(source_revision, "source_revision")
        clean_note = _require_text(note, "note")
        clean_position = None if position is None else _require_position(position)
        clean_marker = None if marker_id is None else _require_text(marker_id, "marker_id")

        def mutate(document: dict[str, Any], auto_id: str) -> tuple[dict[str, Any], bool, str]:
            item_id = clean_marker or auto_id
            if item_id in document["items"]:
                return document, False, item_id
            _insert(document, item_id, {
                "item_id": item_id, "item_type": ITEM_UNCLEAR_MARKER, "marker_id": item_id,
                "source_id": clean_source, "source_revision": clean_revision,
                "position": clean_position, "note": clean_note, "marked_at": stamp,
            })
            return document, True, item_id

        return self._commit(ITEM_UNCLEAR_MARKER,
                            ("unclear", clean_marker or "", clean_source, clean_revision,
                             clean_note), mutate,
                            operation_key=operation_key, record=record,
                            expected_head=expected_head)

    # -- inbox (explicit receipts only) ---------------------------------------------------

    def receive_item(self, *, receipt_ref: str, from_ref: str, kind: str,
                     summary: Optional[str] = None, operation_key: Optional[str] = None,
                     now: Optional[str] = None) -> WriteResult:
        """Record that something actually arrived, from an explicit receipt.

        Everything here comes from the caller's receipt record: there is no inference from a
        conversation, and ``co_presence`` stays False -- an arriving message is not co-presence.
        """
        stamp = now or self._now()
        clean_receipt = _require_text(receipt_ref, "receipt_ref")
        clean_from = _require_text(from_ref, "from_ref")
        clean_kind = _require_text(kind, "kind")
        clean_summary = _require_optional_text(summary, "summary")

        def mutate(document: dict[str, Any], auto_id: str) -> tuple[dict[str, Any], bool, str]:
            item_id = auto_id
            if item_id in document["items"]:
                return document, False, item_id
            _insert(document, item_id, {
                "item_id": item_id, "item_type": ITEM_INBOX, "receipt_ref": clean_receipt,
                "from_ref": clean_from, "kind": clean_kind, "summary": clean_summary,
                "received_at": stamp, "state": INBOX_UNREAD, "read_at": None,
                "read_evidence_ref": None, "co_presence": False,
            })
            return document, True, item_id

        return self._commit(ITEM_INBOX, ("receive", clean_receipt, clean_from, clean_kind),
                            mutate, operation_key=operation_key)

    def mark_read(self, item_id: str, *, evidence_ref: str, operation_key: Optional[str] = None,
                  now: Optional[str] = None) -> WriteResult:
        """UNREAD -> READ, and only with the evidence of how it was actually taken in."""
        stamp = now or self._now()
        clean_evidence = _require_text(evidence_ref, "evidence_ref")

        def mutate(document: dict[str, Any], auto_id: str) -> tuple[dict[str, Any], bool, str]:
            item = document["items"].get(str(item_id))
            if item is None or item.get("item_type") != ITEM_INBOX:
                raise PrivateItemsContractRefused(f"INBOX_ITEM_NOT_FOUND:{item_id}")
            if str(item.get("state")) == INBOX_READ:
                return document, False, str(item_id)
            item["state"] = INBOX_READ
            item["read_at"] = stamp
            item["read_evidence_ref"] = clean_evidence
            return document, True, str(item_id)

        return self._commit(ITEM_INBOX, ("read", item_id, clean_evidence), mutate,
                            operation_key=operation_key)

    # -- observation link (World owns placement) -------------------------------------------

    def note_item_at_observed_object(self, item_id: str, *, observation: Mapping[str, Any],
                                     object_id: str, operation_key: Optional[str] = None,
                                     now: Optional[str] = None) -> WriteResult:
        """Link an item to a room object **actually observed** in a World projection.

        This is an observation link, not placement: World owns location.  An object the caller
        did not observe cannot be referenced, and nothing here writes World state.
        """
        stamp = now or self._now()
        clean_object = _require_text(object_id, "object_id")
        refs = room_object_refs(observation)
        if clean_object not in {ref.object_id for ref in refs}:
            raise PrivateItemsContractRefused(
                f"OBJECT_NOT_OBSERVED:{clean_object}")

        def mutate(document: dict[str, Any], auto_id: str) -> tuple[dict[str, Any], bool, str]:
            item = document["items"].get(str(item_id))
            if item is None:
                raise PrivateItemsContractRefused(f"ITEM_NOT_FOUND:{item_id}")
            if str(item.get("observed_object_ref") or "") == clean_object:
                return document, False, str(item_id)
            item["observed_object_ref"] = clean_object
            item["observed_at"] = stamp
            item["observed_ref_source"] = "world_observation"
            return document, True, str(item_id)

        return self._commit(_KIND_OBSERVED_LINK, ("observe", item_id, clean_object), mutate,
                            operation_key=operation_key)


__all__ = [
    "DOC_TYPE",
    "SCHEMA_VERSION",
    "ITEM_MEMO",
    "ITEM_SAVED_REFERENCE",
    "ITEM_SOURCE_ACQUISITION",
    "ITEM_READING_POSITION",
    "ITEM_UNCLEAR_MARKER",
    "ITEM_INBOX",
    "ITEM_TYPES",
    "INBOX_UNREAD",
    "INBOX_READ",
    "UNDERSTANDING_UNKNOWN",
    "REFERENCE_SAVED",
    "MAX_EXCERPT_CHARS",
    "MAX_READING_OPERATIONS",
    "READING_ANCHOR_FIELDS",
    "READING_OPERATIONS_FIELD",
    "PrivateDailyItemsError",
    "PrivateItemsContractRefused",
    "PrivateItemsOwnerRefused",
    "PrivateItemsConflict",
    "PrivateItemsVersionMismatch",
    "PrivateItemsUnresolved",
    "PrivateItemsSourceUnavailable",
    "WriteResult",
    "RoomObjectRef",
    "Memo",
    "SavedReference",
    "SourceAcquisition",
    "ReadingPosition",
    "UnclearMarker",
    "InboxItem",
    "PrivateDailyItems",
    "create_document_artifact",
    "room_object_refs",
    "encode_document",
    "decode_document",
    "reading_operation",
    "ANNOTATION_KINDS",
    "ANNOTATION_NO_CHANGE",
    "ANNOTATION_NOTE",
    "ANNOTATION_UNCLEAR",
    "MAX_ANNOTATION_TEXT_CHARS",
    "MAX_ANNOTATION_TITLE_CHARS",
    "plan_page_annotation",
]

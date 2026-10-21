"""C 侧阅读工作台 facade：真实正文、真实停点、真实私人笔记，全部落在**现有** owner 上。

这是什么，不是什么
------------------
* 材料正文的唯一 owner 仍是 Life Supply 的 ``ManagedArtifact``（实例里的
  ``reading_artifact_id``）；停点与取得证据、私人笔记仍写在 ``PrivateDailyItems`` 那一份 typed
  文档里（``private_items_artifact_id``）。本模块**不建 store、不缓存正文、不复制页码进度、
  不直接读写 Supply SQLite**，只走 ``life_artifact_port`` 已有的 read/commit/lookup op。
* 页码是位置，不是理解：``understanding`` 恒 ``UNKNOWN``；这里没有把页码或批注变成"懂了"的路。
* 一次操作的 ``operation_key`` 绑定 动作 + 目标 + 参数 + 版本：同一个键重放返回
  ``already_applied`` 且不新落一版；同一个键换动作、换页、换正文一律拒绝
  （``READING_OPERATION_KEY_REUSED``），绝不静默成功。判断顺序是**先查已落的操作、后判版本**，
  所以"原键 + 旧 head/revision"的重放仍然回读成功，而不是被误报成冲突。
* 阅读一页 = **一个版本**：取得证据（``source_acquisition``）与位置（``reading_position``）
  在同一个 PDI 文档提交里落地，不存在"只写了一半却报成功"的中间态；材料替换与 PDI 停点是
  **两个独立事务**，各自失败各自诚实。
* 私有文档的 CAS 用的是**调用者给的** ``expected_head``（本模块把它交给 owner 校验），
  不是"先读一遍再拿新 head 去提交"——预检查之后 head 变了，写入仍会被拒。
* GET（``material``）是纯读：不记录取得、不保存位置、不写文件、不创建材料。没有材料就是
  ``unavailable``，不补演示文本；空正文不冒充"已读到内容"。

操作归属与校验（这轮返修的重点）
--------------------------------
* 私人事项侧的写把一条**不含正文**的意图摘要 ``intent``（动作/artifact/source/版本/页/kind 与
  正文的哈希）与效果写进同一版本，重放时比对整条请求绑定，改一个字都算换意图。
* 材料替换不写私人事项文档，所以它核对的是 **owner 自己的操作行**：``artifact_id`` 必须是这份
  材料、``operation_kind`` 必须是 ``COMMIT_MARKDOWN``、``request_digest`` 必须等于本次请求
  （artifact + expected_head + 正文）的摘要。因此"借一个停点/笔记/别的 artifact 的键"、
  "同一个键换正文"都不会因为"当前 head 内容恰好相同"而被当成成功。
* 材料换版之后，原键仍然描述**它当初落下的那个版本**（``source_revision`` 是原 version，
  ``revision_current`` 说明它现在是不是 head），不会被反向误判成 ``KEY_REUSED``。

信封与状态
----------
成功写：``{ok: true, status: "applied" | "already_applied", data: {...}}``；GET 沿用手机
现有 ``ok/status`` 惯例（成功即手机 ``STATUS_OK``，值 ``"applied"``）。domain 拒绝是
``{ok: false, status: "rejected", reason_code: ...}``，供应不可达是 ``unavailable``，
效果未确定是 ``unknown``，查不到是 ``not_found``。每个信封额外带一个 ``http_status`` 只是给
P 的提示（输入错误 400、冲突 409、未确定 202、不可达 503），P 仍按现有 ``http_status_for``
转发；本模块不自己发 HTTP。
"""
from __future__ import annotations

import functools
import json
import sys
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Optional

_LIFE_SCRIPTS = str(Path(__file__).resolve().parent)
if _LIFE_SCRIPTS not in sys.path:
    sys.path.insert(0, _LIFE_SCRIPTS)

from life_artifact_port import (  # noqa: E402
    ArtifactOutcome,
    ArtifactOwnerRefused,
    EffectState,
    LifeArtifactError,
    LifeArtifactPort,
    LifeArtifactUnavailable,
)
import private_daily_items as pdi  # noqa: E402

#: 手机信封状态值。与 ``components/phone/service.py`` 的同名常量逐字一致（那边的
#: ``STATUS_OK`` 就是 ``"applied"``），P 才能直接用现有的 ``http_status_for`` 转发。
STATUS_APPLIED = "applied"
STATUS_ALREADY = "already_applied"
STATUS_REJECTED = "rejected"
STATUS_UNKNOWN = "unknown"
STATUS_UNAVAILABLE = "unavailable"
STATUS_NOT_FOUND = "not_found"

#: 操作人自己的分页标记。与 ``one_day_life.PAGE_MARKER`` 同值；没有标记就是**一页**，
#: 绝不猜分页。两者同值由 ``tests/test_phone_reading_owner.py`` 钉住。
PAGE_MARKER = "%%PAGE%%"

#: 正文边界（UTF-8 bytes）。超界拒绝，不截断——截断等于对"实际导入的是哪段文字"说谎。
MAX_CONTENT_BYTES = 24_000
MAX_PAGE_BYTES = 12_000
MAX_PAGES = 64
#: 注释边界。手机笔记那条路已有自己的上限，这里是本面自己的边界。
MAX_TITLE_CHARS = 200
MAX_ANNOTATION_CHARS = 8_000
MAX_KEY_CHARS = 200

KIND_NOTE = "note"
KIND_UNCLEAR = "unclear"
ANNOTATION_KINDS = (KIND_NOTE, KIND_UNCLEAR)

#: 阅读位置永远不是理解证明。
UNDERSTANDING_UNKNOWN = pdi.UNDERSTANDING_UNKNOWN

#: 一条取得证据覆盖的页码字段。写侧（本模块与 ``one_day_life.read_page``）都记
#: ``{"pages": [N], "chars": L}``；读侧只认这个形状，不猜别的键。
_OBTAINED_PAGES = "pages"

#: owner 的 ManagedArtifact 操作种类（``artifact_operations.operation_kind``）。
_OWNER_KIND_COMMIT = "COMMIT_MARKDOWN"


# ---------------------------------------------------------------------------------------------
# envelopes
# ---------------------------------------------------------------------------------------------


def envelope(ok: bool, status: str, data: Optional[Mapping[str, Any]] = None,
             reason_code: Optional[str] = None, http_status: Optional[int] = None
             ) -> dict[str, Any]:
    """本 facade 的**唯一**返回形状。``http_status`` 只是给 P 的转发提示。"""
    payload: dict[str, Any] = {"ok": bool(ok), "status": str(status)}
    if reason_code:
        payload["reason_code"] = str(reason_code)
    payload["data"] = dict(data or {})
    if http_status is not None:
        payload["http_status"] = int(http_status)
    return payload


def _applied(**data: Any) -> dict[str, Any]:
    return envelope(True, STATUS_APPLIED, data, http_status=200)


def _already(**data: Any) -> dict[str, Any]:
    return envelope(True, STATUS_ALREADY, data, http_status=200)


def _bad(reason: str, **data: Any) -> dict[str, Any]:
    """调用方输入错误：400，且什么都没写。"""
    return envelope(False, STATUS_REJECTED, data, reason, 400)


def _conflict(reason: str, **data: Any) -> dict[str, Any]:
    """domain 冲突（版本/head/页/操作键）：409，且什么都没写。"""
    return envelope(False, STATUS_REJECTED, data, reason, 409)


def _unavailable(reason: str, **data: Any) -> dict[str, Any]:
    return envelope(False, STATUS_UNAVAILABLE, data, reason, 503)


def _unknown(reason: str, **data: Any) -> dict[str, Any]:
    return envelope(False, STATUS_UNKNOWN, data, reason, 202)


def _not_found(reason: str, **data: Any) -> dict[str, Any]:
    return envelope(False, STATUS_NOT_FOUND, data, reason, 404)


# ---------------------------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def reading_pages(content: str) -> list[str]:
    """材料的分页：按操作人自己的标记切；没有标记就是**一页**。"""
    text = str(content)
    if PAGE_MARKER not in text:
        return [text]
    return [page for page in text.split(PAGE_MARKER)]


def reading_source_id(artifact_id: str) -> str:
    """材料的 ``source_id``：``artifact:<id>``，绝不叠第二层前缀。"""
    text = str(artifact_id or "").strip()
    return text if text.startswith("artifact:") else "artifact:%s" % text


def _validated_pages(content: Any) -> dict[str, Any]:
    """导入正文的边界检查。全部按 UTF-8 bytes 算，空白页与空正文一律拒绝。"""
    if not isinstance(content, str):
        raise _Stop(_bad("CONTENT_INVALID"))
    if not content.strip():
        raise _Stop(_bad("CONTENT_EMPTY"))
    size = len(content.encode("utf-8"))
    if size > MAX_CONTENT_BYTES:
        raise _Stop(_bad("CONTENT_TOO_LARGE", bytes=size, max_bytes=MAX_CONTENT_BYTES))
    pages = reading_pages(content)
    if len(pages) > MAX_PAGES:
        raise _Stop(_bad("TOO_MANY_PAGES", pages=len(pages), max_pages=MAX_PAGES))
    sizes: list[int] = []
    for index, page in enumerate(pages, start=1):
        if not page.strip():
            raise _Stop(_bad("BLANK_PAGE", page=index, pages=len(pages)))
        page_size = len(page.encode("utf-8"))
        if page_size > MAX_PAGE_BYTES:
            raise _Stop(_bad("PAGE_TOO_LARGE", page=index, bytes=page_size,
                             max_bytes=MAX_PAGE_BYTES))
        sizes.append(page_size)
    return {"pages": pages, "sizes": sizes, "bytes": size}


def _intent_digest(action: str, *parts: Any) -> str:
    """一条请求绑定的稳定摘要：动作 + 目标 + 参数 + 版本。

    摘要本身**不含正文**（正文只作为哈希输入），所以它可以和效果一起写进私人事项文档，
    用来回答"这个键当初绑的是不是同一个意图"，而不把私人文本搬进操作归属里。
    """
    payload = json.dumps([str(action)] + ["" if part is None else str(part) for part in parts],
                         ensure_ascii=False, separators=(",", ":"))
    return "reading-intent:" + sha256(payload.encode("utf-8")).hexdigest()[:32]


def _commit_request_digest(*, subject_id: str, artifact_id: str, expected_head: str,
                           content: str) -> str:
    """The digest the owner will store for this exact ``commit_markdown`` request.

    ``artifact_operations.request_digest`` is the owner's own durable record of the request it
    executed; reproducing it here (same canonical JSON the store uses) lets a replay prove
    "this key landed *this* artifact, this parent, this content" even after the head moved.
    """
    operation_payload = {"operation": "commit_markdown", "subject_id": str(subject_id),
                         "artifact_id": str(artifact_id), "expected_head": str(expected_head),
                         "content": str(content)}
    request = {"artifact_id": str(artifact_id), "expected_head": str(expected_head),
               "content": str(content), "operation_kind": _OWNER_KIND_COMMIT,
               "operation_payload": operation_payload}
    canonical = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


def _row_result(row: Mapping[str, Any]) -> dict[str, Any]:
    """The original result the owner stored for one operation row (never guessed)."""
    raw = row.get("result_json")
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _record_matches(record: Mapping[str, Any], *, intent: str, action: str, page: int,
                    source_revision: str, kind: Optional[str] = None) -> bool:
    """这条已落的操作，是不是**同一个**意图（动作 + 目标 + 参数 + 版本）。

    有 ``intent`` 摘要时比摘要（它覆盖全部请求绑定，改一个字都不等）；
    更早写下的归属没有摘要，就退回到它真正记过的那几个字段。
    """
    recorded_intent = str(record.get("intent") or "")
    if recorded_intent:
        return recorded_intent == str(intent)
    if str(record.get("action") or "") != str(action):
        return False
    recorded_page = record.get("page")
    if isinstance(recorded_page, bool) or not isinstance(recorded_page, int):
        return False
    if recorded_page != int(page):
        return False
    if str(record.get("source_revision") or "") != str(source_revision):
        return False
    if kind is not None and str(record.get("kind") or "") != str(kind):
        return False
    return True


def _page_acquired(items: Any, *, source_id: str, source_revision: str, page: int) -> bool:
    """这一版这一页是否**真的**取得过。没有取得就不许批注（``PAGE_NOT_ACQUIRED``）。"""
    for row in items.items(pdi.ITEM_SOURCE_ACQUISITION):
        if str(row.get("source_id") or "") != str(source_id):
            continue
        if str(row.get("source_revision") or "") != str(source_revision):
            continue
        if not bool(row.get("available", True)):
            continue
        obtained = row.get("obtained")
        pages = obtained.get(_OBTAINED_PAGES) if isinstance(obtained, dict) else None
        if not isinstance(pages, list):
            continue
        if any(not isinstance(value, bool) and isinstance(value, int) and value == int(page)
               for value in pages):
            return True
    return False


# ---------------------------------------------------------------------------------------------
# control flow: one internal stop, converted to an envelope at the boundary
# ---------------------------------------------------------------------------------------------


class PhoneReadingError(RuntimeError):
    """facade 被误用（参数形状都不是该由 domain 判的那种）。P 应按 500 处理。"""


class _Stop(Exception):
    """内部：立刻带着这个信封返回给调用方。"""

    def __init__(self, answer: Mapping[str, Any]) -> None:
        super().__init__(str(answer.get("reason_code") or answer.get("status")))
        self.answer = dict(answer)


def _guarded(method: Any) -> Any:
    """把 owner/adapter 的拒绝与不可达翻译成同一个信封。"""

    @functools.wraps(method)
    def wrapper(self: "PhoneReading", *args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return method(self, *args, **kwargs)
        except _Stop as stop:
            return stop.answer
        except LifeArtifactUnavailable as exc:
            return _unavailable("SUPPLY_UNAVAILABLE", detail=str(exc)[:200])
        except ArtifactOwnerRefused as exc:
            if str(exc.status) == "NOT_FOUND":
                return _unavailable("READING_MATERIAL_UNAVAILABLE",
                                    detail=str(exc.reason)[:120])
            return _unavailable("SUPPLY_REFUSED", owner_status=str(exc.status),
                                detail=str(exc.detail)[:160])
        except pdi.PrivateItemsUnresolved as exc:
            return _unknown("READING_OPERATION_UNKNOWN", detail=str(exc)[:160])
        except pdi.PrivateItemsSourceUnavailable as exc:
            return _conflict("SOURCE_UNAVAILABLE", detail=str(exc)[:160])
        except pdi.PrivateItemsConflict as exc:
            return _conflict("READING_HEAD_CONFLICT", detail=str(exc)[:160])
        except pdi.PrivateItemsVersionMismatch as exc:
            return _conflict("READING_DOCUMENT_VERSION_MISMATCH", detail=str(exc)[:160])
        except pdi.PrivateItemsOwnerRefused as exc:
            return _conflict("SUPPLY_REFUSED", owner_status=str(exc.status),
                             detail=str(exc.detail)[:160])
        except pdi.PrivateItemsContractRefused as exc:
            return _conflict("READING_WRITE_REFUSED", detail=str(exc)[:160])
        except LifeArtifactError as exc:
            return _unavailable("SUPPLY_UNAVAILABLE", detail=str(exc)[:200])

    return wrapper


# ---------------------------------------------------------------------------------------------
# the facade
# ---------------------------------------------------------------------------------------------


class PhoneReading:
    """一个人的一份阅读材料 + 一份私人事项，按"显式操作"读写。

    绑定是**显式**的：调用方给出 ``reading_artifact_id`` 与 ``private_items_artifact_id``，
    本模块不 ``latest_resumable``、不扫全表、不猜哪个 artifact 是阅读材料。
    """

    def __init__(self, port: LifeArtifactPort, *, reading_artifact_id: str,
                 private_items_artifact_id: str, workspace_id: Optional[str],
                 grant_id: Optional[str], clock: Optional[Any] = None) -> None:
        if not isinstance(port, LifeArtifactPort):
            raise PhoneReadingError("LIFE_ARTIFACT_PORT_REQUIRED")
        self.port = port
        self.reading_artifact_id = str(reading_artifact_id or "").strip()
        self.private_items_artifact_id = str(private_items_artifact_id or "").strip()
        self.workspace_id = None if workspace_id is None else str(workspace_id)
        self.grant_id = None if grant_id is None else str(grant_id)
        self._clock = clock or _utc_now_iso

    # -- binding ---------------------------------------------------------------------------

    @property
    def source_id(self) -> str:
        return reading_source_id(self.reading_artifact_id)

    def _now(self) -> str:
        return str(self._clock())

    # -- input validation ------------------------------------------------------------------

    @staticmethod
    def _page_number(page: Any) -> int:
        """页码严格是整数且非 bool。``True`` 不是第 1 页，``"2"`` 也不是第 2 页。"""
        if isinstance(page, bool) or not isinstance(page, int):
            raise _Stop(_bad("PAGE_INVALID", page=repr(page)[:24]))
        if int(page) < 1:
            raise _Stop(_bad("PAGE_INVALID", page=int(page)))
        return int(page)

    @staticmethod
    def _token(value: Any, reason: str) -> str:
        """不透明的键/版本串：非空、无控制字符、有上限。"""
        if not isinstance(value, str):
            raise _Stop(_bad(reason))
        text = value.strip()
        if not text or len(text) > MAX_KEY_CHARS:
            raise _Stop(_bad(reason))
        if any(ord(char) < 0x20 or ord(char) == 0x7F for char in text):
            raise _Stop(_bad(reason))
        return text

    @staticmethod
    def _text(value: Any, reason: str, limit: int) -> str:
        """人给的正文/标题：必须非空、不超界；原样保存，不悄悄改写。"""
        if not isinstance(value, str) or not value.strip():
            raise _Stop(_bad(reason))
        if len(value.encode("utf-8")) > limit:
            raise _Stop(_bad(reason, max_chars=limit, bytes=len(value.encode("utf-8"))))
        return value

    # -- material (the real artifact) ------------------------------------------------------

    def _read_material(self) -> dict[str, Any]:
        """读材料的真实 head 与真实正文，或者停在诚实的状态上。"""
        if not self.reading_artifact_id:
            raise _Stop(_unavailable("READING_MATERIAL_UNAVAILABLE", available=False,
                                     artifact_id=None, content_available=False,
                                     private_head=None, bookmark=None, annotations=[]))
        try:
            view = self.port.read_artifact(self.reading_artifact_id)
        except LifeArtifactUnavailable as exc:
            raise _Stop(_unavailable("SUPPLY_UNAVAILABLE", detail=str(exc)[:200]))
        except ArtifactOwnerRefused as exc:
            if str(exc.status) == "NOT_FOUND":
                raise _Stop(_unavailable(
                    "READING_MATERIAL_UNAVAILABLE", available=False,
                    artifact_id=self.reading_artifact_id, content_available=False,
                    private_head=None, bookmark=None, annotations=[]))
            raise _Stop(_unavailable("SUPPLY_REFUSED", owner_status=str(exc.status),
                                     detail=str(exc.detail)[:160]))
        artifact = dict(view.get("artifact") or {})
        head = dict(view.get("head") or {})
        content = view.get("content")
        revision = str(head.get("version_id") or "")
        if not revision or not isinstance(content, str) or not content:
            raise _Stop(_unavailable(
                "READING_MATERIAL_UNAVAILABLE", available=False,
                artifact_id=self.reading_artifact_id, content_available=False,
                source_revision=revision or None,
                private_head=None, bookmark=None, annotations=[]))
        return {"artifact": artifact, "head": head, "content": content,
                "revision": revision, "pages": reading_pages(content)}

    def _current_material_or_none(self) -> Optional[dict[str, Any]]:
        """当前材料视图；读不到就是 ``None``（用来描述"当时的效果"时不强求当前 head）。"""
        try:
            return self._read_material()
        except _Stop:
            return None

    # -- private items (read side, degrading honestly) -------------------------------------

    def _private(self) -> tuple[Optional[Any], Optional[dict[str, Any]]]:
        """私人事项 adapter 与它解码后的文档；拿不到就是 ``(None, None)``，不编造。"""
        if not self.private_items_artifact_id:
            return None, None
        try:
            items = pdi.PrivateDailyItems(self.port, self.private_items_artifact_id,
                                          workspace_id=self.workspace_id,
                                          grant_id=self.grant_id)
            return items, items.load()
        except Exception:
            return None, None

    def _require_private(self) -> tuple[Any, dict[str, Any]]:
        items, document = self._private()
        if items is None or document is None:
            raise _Stop(_unavailable("PRIVATE_ITEMS_UNAVAILABLE"))
        return items, document

    def _private_head(self) -> Optional[str]:
        if not self.private_items_artifact_id:
            return None
        try:
            head = self.port.read_head(self.private_items_artifact_id) or {}
        except Exception:
            return None
        return head.get("version_id")

    def _bookmark(self, items: Optional[Any], revision: str) -> Optional[dict[str, Any]]:
        """最近一次**显式保存**的停点（按保存时间，不是最高页）。"""
        if items is None:
            return None
        rows = [row for row in items.items(pdi.ITEM_READING_POSITION)
                if str(row.get("source_id") or "") == self.source_id]
        if not rows:
            return None
        best: Optional[tuple[tuple[str, int], dict[str, Any]]] = None
        for index, row in enumerate(rows):
            key = (str(row.get("set_at") or ""), index)
            if best is None or key >= best[0]:
                best = (key, row)
        row = best[1]
        position = row.get("position")
        page = position if isinstance(position, int) and not isinstance(position, bool) else None
        stale = str(row.get("source_revision") or "") != str(revision)
        return {
            "page": page,
            "source_revision": str(row.get("source_revision") or ""),
            "needs_reread": bool(row.get("needs_reread")) or stale,
            "needs_reread_reason": str(row.get("needs_reread_reason") or "") or
                                   ("SOURCE_REVISION_ADVANCED" if stale else ""),
            "position_id": row.get("item_id"),
            "set_at": row.get("set_at"),
        }

    def _annotations(self, items: Optional[Any], revision: str) -> list[dict[str, Any]]:
        """这份材料相关的批注：note 带锚点，unclear 是它自己的类型；旧版锚点保留自己的版本。"""
        if items is None:
            return []
        rows: list[dict[str, Any]] = []
        for row in items.items(pdi.ITEM_MEMO):
            anchor = row.get("reading_anchor")
            if not isinstance(anchor, dict):
                continue
            if str(anchor.get("source_id") or "") != self.source_id:
                continue
            item_id = str(row.get("item_id"))
            try:
                body = items.full_text(item_id)
            except Exception:
                body = ""
            item_revision = str(anchor.get("source_revision") or "")
            stale = item_revision != str(revision)
            rows.append({
                "id": item_id, "kind": KIND_NOTE, "title": str(row.get("title") or ""),
                "body": body, "source_id": self.source_id,
                "source_revision": item_revision, "page": anchor.get("page"),
                "created_at": row.get("created_at") or row.get("updated_at"),
                "stale": stale, "needs_reread": stale,
            })
        for row in items.items(pdi.ITEM_UNCLEAR_MARKER):
            if str(row.get("source_id") or "") != self.source_id:
                continue
            item_id = str(row.get("item_id"))
            try:
                note = items.full_text(item_id)
            except Exception:
                note = ""
            item_revision = str(row.get("source_revision") or "")
            stale = item_revision != str(revision)
            rows.append({
                "id": item_id, "kind": KIND_UNCLEAR, "note": note,
                "source_id": self.source_id, "source_revision": item_revision,
                "page": row.get("position"), "created_at": row.get("marked_at"),
                "stale": stale, "needs_reread": stale,
            })
        rows.sort(key=lambda row: str(row.get("created_at") or ""))
        return rows

    # -- GET -------------------------------------------------------------------------------

    @_guarded
    def material(self, page: Optional[int] = None) -> dict[str, Any]:
        """当前页的真实正文 + 真实停点 + 相关批注。纯读：零写、零创建。"""
        view = self._read_material()
        revision, pages = view["revision"], view["pages"]
        items, _document = self._private()
        bookmark = self._bookmark(items, revision)
        annotations = self._annotations(items, revision)
        if page is None:
            restore = bookmark.get("page") if isinstance(bookmark, dict) else None
            wanted = int(restore) if (isinstance(restore, int)
                                      and not bookmark.get("needs_reread")) else 1
            wanted = min(wanted, len(pages))
        else:
            wanted = self._page_number(page)
            if wanted > len(pages):
                raise _Stop(_conflict("PAGE_NOT_PRESENT", page=wanted, pages=len(pages)))
        text = pages[wanted - 1]
        return envelope(True, STATUS_APPLIED, {
            "available": True,
            "artifact_id": self.reading_artifact_id,
            "source_id": self.source_id,
            "source_revision": revision,
            "version_number": view["head"].get("version_number"),
            "title": str(view["artifact"].get("title") or ""),
            "page": wanted,
            "pages": len(pages),
            "text": text,
            "content_available": bool(text.strip()),
            "private_head": self._private_head(),
            "bookmark": bookmark,
            "annotations": annotations,
            "understanding": UNDERSTANDING_UNKNOWN,
            "private_items_status": "available" if items is not None else "unavailable",
        }, http_status=200)

    # -- POST: replace the material --------------------------------------------------------

    def _material_data(self, view: Optional[Mapping[str, Any]], key: str, *,
                       version_id: Optional[str], version_number: Optional[int],
                       pages: list[str], request_bytes: int, detail: str = "",
                       owner_status: str = "", effect_state: str = "",
                       version_created: Optional[bool] = None,
                       reconciled: bool = False) -> dict[str, Any]:
        current_revision = str((view or {}).get("revision") or "")
        recorded = str(version_id or "")
        items, _document = self._private()
        return {
            "artifact_id": self.reading_artifact_id,
            "source_id": self.source_id,
            "source_revision": recorded or None,
            "revision_current": bool(recorded) and recorded == current_revision,
            "material_current_revision": current_revision or None,
            "version_number": version_number,
            "pages": len(pages),
            "page": 1,
            "page_bytes": [len(page.encode("utf-8")) for page in pages],
            "request_chars": sum(len(page) for page in pages),
            "request_bytes": int(request_bytes),
            "material_current_chars": len(str(view["content"])) if view else None,
            "title": str((view or {}).get("artifact", {}).get("title") or ""),
            "operation_key": key,
            "owner_status": owner_status,
            "effect_state": effect_state,
            "version_created": version_created,
            "reconciled": bool(reconciled),
            "detail": detail,
            "bookmark": self._bookmark(items, current_revision) if items is not None else None,
            "private_head": self._private_head(),
            "understanding": UNDERSTANDING_UNKNOWN,
        }

    @_guarded
    def replace_material(self, content: str, expected_revision: str,
                         operation_key: str) -> dict[str, Any]:
        """操作人显式给的正文替换实例绑定的那份材料。旧版本仍留在 Supply。"""
        key = self._token(operation_key, "OPERATION_KEY_INVALID")
        expected = self._token(expected_revision, "EXPECTED_REVISION_INVALID")
        checked = _validated_pages(content)
        pages: list[str] = checked["pages"]
        request_digest = _commit_request_digest(
            subject_id=self.port.subject_id, artifact_id=self.reading_artifact_id,
            expected_head=expected, content=str(content))
        if not self.reading_artifact_id:
            raise _Stop(_unavailable("READING_MATERIAL_UNAVAILABLE", available=False,
                                     artifact_id=None, content_available=False))
        if self.grant_id is None:
            raise _Stop(_conflict("READING_GRANT_REQUIRED", operation_key=key))

        # 1) 这个键不能属于别的动作：私人事项侧的停点/批注也用同一个键空间。
        _items, document = self._private()
        local = pdi.reading_operation(document, key) if document is not None else None
        if local is not None:
            raise _Stop(_conflict("READING_OPERATION_KEY_REUSED", operation_key=key,
                                  recorded_action=str(local.get("action") or ""),
                                  recorded_page=local.get("page"),
                                  recorded_artifact=self.private_items_artifact_id))
        # 2) owner 自己的操作行：必须真的是"这份材料 + commit_markdown + 同一个请求"。
        row = self._owner_row(key, strict=True)
        if row is not None:
            return _already(**self._material_replay(key, row, expected=expected,
                                                    request_digest=request_digest,
                                                    pages=pages, request_bytes=checked["bytes"]))

        # 3) 从未落地：调用方看到的 revision 必须仍是真实 head。
        current = self._read_material()
        if str(current["revision"]) != expected:
            raise _Stop(_conflict("READING_REVISION_CONFLICT", expected_revision=expected,
                                  source_revision=str(current["revision"]),
                                  operation_key=key))

        # 4) 真写：旧内容留在 Supply 的版本历史里，新版本才是 head。
        outcome = self.port.commit_version(
            artifact_id=self.reading_artifact_id, expected_head=expected,
            content=str(content), grant_id=self.grant_id, operation_key=key)
        if outcome.effect_state == EffectState.CONFLICT:
            row = self._owner_row(key, strict=True)
            if row is not None:
                return _already(**self._material_replay(key, row, expected=expected,
                                                        request_digest=request_digest,
                                                        pages=pages,
                                                        request_bytes=checked["bytes"]))
            fresh = self._current_material_or_none()
            if fresh is not None and str(fresh["revision"]) != expected:
                raise _Stop(_conflict("READING_REVISION_CONFLICT", expected_revision=expected,
                                      source_revision=str(fresh["revision"]),
                                      operation_key=key))
            raise _Stop(_conflict("READING_OPERATION_KEY_REUSED", operation_key=key,
                                  detail=str(outcome.detail or outcome.reason)[:160]))
        if outcome.effect_state in (EffectState.REFUSED, EffectState.NOT_FOUND):
            raise _Stop(_unavailable("SUPPLY_REFUSED", owner_status=outcome.status,
                                     detail=str(outcome.detail or outcome.reason)[:160]))
        if outcome.effect_state == EffectState.UNKNOWN:
            reconciliation = self._resolve(key)
            if reconciliation.effect_state != EffectState.APPLIED:
                raise _Stop(_unknown("READING_OPERATION_UNKNOWN", operation_key=key,
                                     retryable=True,
                                     detail=reconciliation.reason or outcome.reason or ""))
            fresh = self._current_material_or_none()
            return _applied(**self._material_data(
                fresh, key, version_id=reconciliation.version_id,
                version_number=None, pages=pages, request_bytes=checked["bytes"],
                detail="RECONCILED_AFTER_UNKNOWN", owner_status=outcome.status,
                effect_state=outcome.effect_state, version_created=outcome.version_created,
                reconciled=True))
        fresh = self._current_material_or_none()
        if outcome.effect_state == EffectState.NO_CHANGE or outcome.version_created is False:
            version_id = (outcome.version_id or outcome.head_version_id
                          or (fresh or {}).get("revision"))
            return _already(**self._material_data(
                fresh, key, version_id=version_id,
                version_number=outcome.version_number, pages=pages,
                request_bytes=checked["bytes"], detail="NO_CHANGE_ALREADY_AT_HEAD",
                owner_status=outcome.status, effect_state=outcome.effect_state,
                version_created=outcome.version_created))
        return _applied(**self._material_data(
            fresh, key, version_id=outcome.version_id, version_number=outcome.version_number,
            pages=pages, request_bytes=checked["bytes"], owner_status=outcome.status,
            effect_state=outcome.effect_state, version_created=outcome.version_created))

    def _material_replay(self, key: str, row: Mapping[str, Any], *, expected: str,
                         request_digest: str, pages: list[str],
                         request_bytes: int) -> dict[str, Any]:
        """核对 owner 已落的操作行；对得上就是"这次请求已经落过"，描述原来那个版本。"""
        recorded_artifact = str(row.get("artifact_id") or "")
        recorded_kind = str(row.get("operation_kind") or "")
        if recorded_artifact != self.reading_artifact_id or recorded_kind != _OWNER_KIND_COMMIT:
            raise _Stop(_conflict("READING_OPERATION_KEY_REUSED", operation_key=key,
                                  recorded_artifact=recorded_artifact,
                                  recorded_action=recorded_kind))
        stored_digest = str(row.get("request_digest") or "")
        if stored_digest != request_digest:
            raise _Stop(_conflict("READING_OPERATION_KEY_REUSED", operation_key=key,
                                  detail="REQUEST_DIGEST_MISMATCH",
                                  recorded_expected_revision=None))
        result = _row_result(row)
        version_id = result.get("version_id") or result.get("head_version_id")
        current = self._current_material_or_none()
        return self._material_data(
            current, key, version_id=version_id,
            version_number=result.get("version_number"), pages=pages,
            request_bytes=request_bytes, detail="OPERATION_KEY_REPLAY",
            owner_status=str(row.get("outcome") or ""),
            effect_state=EffectState.APPLIED,
            version_created=result.get("version_created"))

    # -- POST: save the real stop point ----------------------------------------------------

    def _resolve(self, key: str) -> ArtifactOutcome:
        """问 owner：这个键到底落了没有。UNKNOWN 不等于失败，也不等于成功。"""
        try:
            return self.port.resolve_operation(key)
        except LifeArtifactUnavailable as exc:
            raise _Stop(_unavailable("SUPPLY_UNAVAILABLE", detail=str(exc)[:200]))
        except LifeArtifactError as exc:
            raise _Stop(_unavailable("SUPPLY_UNAVAILABLE", detail=str(exc)[:200]))

    def _check_operation(self, *, key: str, intent: str, action: str, page: int,
                         source_revision: str, kind: Optional[str] = None) -> dict[str, Any]:
        """先查已落的操作（本地归属 + owner 的操作行），再交给调用方判版本与写。"""
        items, document = self._require_private()
        record = pdi.reading_operation(document, key)
        if record is not None:
            if _record_matches(record, intent=intent, action=action, page=page,
                               source_revision=source_revision, kind=kind):
                return {"replay": True, "record": record, "items": items}
            raise _Stop(_conflict("READING_OPERATION_KEY_REUSED", operation_key=key,
                                  recorded_action=str(record.get("action") or ""),
                                  recorded_page=record.get("page"),
                                  recorded_kind=record.get("kind") or None,
                                  recorded_artifact=self.private_items_artifact_id))
        row = self._owner_row(key, strict=True)
        if row is not None:
            # 键在 owner 那边确实落过，但不是这份文档里的这个意图（例如用在材料替换上）；
            # 也可能本地归属已被上限裁掉：两种都拒绝，绝不当成本次意图已经完成。
            raise _Stop(_conflict("READING_OPERATION_KEY_REUSED", operation_key=key,
                                  recorded_artifact=str(row.get("artifact_id") or ""),
                                  recorded_action=str(row.get("operation_kind") or ""),
                                  recorded_outcome=str(row.get("outcome") or "")))
        if self.grant_id is None:
            raise _Stop(_conflict("READING_GRANT_REQUIRED", operation_key=key))
        return {"replay": False, "record": None, "items": items}

    def _position_data(self, view: Optional[Mapping[str, Any]], page: int, key: str, *,
                       write: Optional[Any] = None,
                       record: Optional[Mapping[str, Any]] = None,
                       detail: str = "") -> dict[str, Any]:
        recorded = dict(record or {})
        current_revision = str((view or {}).get("revision") or "")
        recorded_revision = str(recorded.get("source_revision") or current_revision)
        pages = list((view or {}).get("pages") or [])
        same = (bool(current_revision) and current_revision == recorded_revision
                and 1 <= page <= len(pages))
        text = pages[page - 1] if same else ""
        total_pages = len(pages) if pages else recorded.get("pages")
        return {
            "page": page,
            "pages": total_pages,
            "source_id": self.source_id,
            "source_revision": recorded_revision or None,
            "revision_current": same,
            "material_current_revision": current_revision or None,
            "version_number": recorded.get("version_number"),
            "title": str((view or {}).get("artifact", {}).get("title") or ""),
            "obtained": {_OBTAINED_PAGES: [page], "chars": len(text)} if same else None,
            "receipt_ref": "read:%s:%s:page-%s" % (self.reading_artifact_id,
                                                   recorded_revision, page),
            "acquisition_id": recorded.get("acquisition_id"),
            "position_id": recorded.get("position_id"),
            "item_id": recorded.get("item_id") or recorded.get("position_id"),
            "private_head": self._private_head(),
            "understanding": UNDERSTANDING_UNKNOWN,
            "operation_key": key,
            "effect_state": write.effect_state if write is not None else "ALREADY_RECORDED",
            "owner_status": write.owner_status if write is not None else "",
            "version_id": write.version_id if write is not None else None,
            "detail": detail or (write.detail if write is not None else "OPERATION_KEY_REPLAY"),
        }

    @_guarded
    def save_position(self, page: int, source_revision: str, expected_head: str,
                      operation_key: str) -> dict[str, Any]:
        """真读这一页，然后把取得证据与位置**一次**提交进同一份私人文档。"""
        wanted = self._page_number(page)
        revision = self._token(source_revision, "SOURCE_REVISION_INVALID")
        expected = self._token(expected_head, "EXPECTED_HEAD_INVALID")
        key = self._token(operation_key, "OPERATION_KEY_INVALID")
        intent = _intent_digest("position", self.reading_artifact_id, self.source_id,
                                revision, wanted)
        view = self._read_material()

        # 1) 原键先回读：旧版本上已经成功过的操作，不能因为后来换版就误称没成功。
        checked = self._check_operation(key=key, intent=intent, action="position",
                                        page=wanted, source_revision=revision)
        if checked["replay"]:
            return _already(**self._position_data(view, wanted, key,
                                                  record=checked["record"],
                                                  detail="OPERATION_KEY_REPLAY"))

        # 2) 新写：必须是当前真实材料版本、且是真实存在的页。
        if revision != str(view["revision"]):
            raise _Stop(_conflict("READING_REVISION_CONFLICT", expected_revision=revision,
                                  source_revision=str(view["revision"]),
                                  operation_key=key))
        if wanted > len(view["pages"]) or not view["pages"][wanted - 1].strip():
            raise _Stop(_conflict("PAGE_NOT_PRESENT", page=wanted,
                                  pages=len(view["pages"]), operation_key=key))
        items = checked["items"]
        text = view["pages"][wanted - 1]
        receipt = "read:%s:%s:page-%s" % (self.reading_artifact_id, revision, wanted)
        stamp = self._now()
        record = {"operation_key": key, "action": "position", "source_id": self.source_id,
                  "source_revision": revision, "page": wanted, "intent": intent,
                  "expected_head": expected, "at": stamp,
                  "pages": len(view["pages"]),
                  "version_number": view["head"].get("version_number")}
        try:
            written = items.record_page_read(
                source_id=self.source_id, source_revision=revision, receipt_ref=receipt,
                obtained={_OBTAINED_PAGES: [wanted], "chars": len(text)}, excerpt=text[:200],
                position=wanted, grant_ref=self.grant_id, operation_key=key, now=stamp,
                record=record, expected_head=expected)
        except pdi.PrivateItemsConflict as exc:
            raise _Stop(self._conflict_from(exc, key=key, expected=expected))
        write = written["write"]
        record = dict(record, acquisition_id=written.get("acquisition_id"),
                      position_id=written.get("position_id"),
                      item_id=written.get("position_id") or written.get("acquisition_id"))
        if write.retryable and not write.applied:
            # owner 说这次从未落地：可以用**同一个键**重试，但绝不是"成功了"。
            raise _Stop(_unknown("READING_OPERATION_UNKNOWN", operation_key=key, retryable=True,
                                 detail=write.detail or "UNKNOWN_RESOLVED_NEVER_LANDED"))
        if not write.applied:
            return _already(**self._position_data(view, wanted, key, record=record,
                                                  detail=write.detail or "NOTHING_TO_WRITE"))
        return _applied(**self._position_data(view, wanted, key, write=write, record=record))

    def _conflict_from(self, exc: Exception, *, key: str, expected: str) -> dict[str, Any]:
        """一次 CAS 冲突到底该报哪儿：head 过期，还是这个键被换了意图用。"""
        head = self._private_head()
        if (head or "") != str(expected):
            return _conflict("READING_HEAD_CONFLICT", expected_head=expected,
                             private_head=head, operation_key=key, detail=str(exc)[:160])
        return _conflict("READING_OPERATION_KEY_REUSED", operation_key=key,
                         expected_head=expected, detail=str(exc)[:160])

    def _owner_row(self, key: str, *, strict: bool) -> Optional[dict[str, Any]]:
        """owner 的操作行。``strict`` 时，读不出来就是"未确定"（绝不能当成"没落过"）。"""
        try:
            return self.port.operation_row(key)
        except ArtifactOwnerRefused as exc:
            if not strict:
                return None
            raise _Stop(_unknown("READING_OPERATION_UNKNOWN", operation_key=key,
                                 owner_status=str(exc.status)))
        except LifeArtifactUnavailable as exc:
            raise _Stop(_unavailable("SUPPLY_UNAVAILABLE", detail=str(exc)[:200]))

    # -- POST: annotate the real page ------------------------------------------------------

    def _annotation_data(self, view: Optional[Mapping[str, Any]], kind: str, page: int, key: str,
                         item_id: Optional[str], *, write: Optional[Any] = None,
                         record: Optional[Mapping[str, Any]] = None,
                         detail: str = "") -> dict[str, Any]:
        recorded = dict(record or {})
        current_revision = str((view or {}).get("revision") or "")
        recorded_revision = str(recorded.get("source_revision") or current_revision)
        return {
            "kind": kind,
            "id": item_id or recorded.get("item_id"),
            "page": page,
            "source_id": self.source_id,
            "source_revision": recorded_revision or None,
            "revision_current": bool(current_revision) and current_revision == recorded_revision,
            "material_current_revision": current_revision or None,
            "anchor": {"source_id": self.source_id,
                       "source_revision": recorded_revision, "page": page},
            "private_head": self._private_head(),
            "understanding": UNDERSTANDING_UNKNOWN,
            "operation_key": key,
            "effect_state": write.effect_state if write is not None else "ALREADY_RECORDED",
            "owner_status": write.owner_status if write is not None else "",
            "item_id": recorded.get("item_id") or item_id,
            "detail": detail or (write.detail if write is not None else "OPERATION_KEY_REPLAY"),
        }

    @_guarded
    def annotate(self, kind: str, page: int, source_revision: str, expected_head: str,
                 operation_key: str, title: Optional[str] = None, body: Optional[str] = None,
                 note: Optional[str] = None) -> dict[str, Any]:
        """对**真实取得过的**那一页写私人笔记（note）或疑问（unclear）。"""
        wanted = self._page_number(page)
        clean_kind = str(kind or "").strip().lower()
        if clean_kind not in ANNOTATION_KINDS:
            raise _Stop(_bad("ANNOTATION_KIND_INVALID", kind=str(kind or "")[:24]))
        revision = self._token(source_revision, "SOURCE_REVISION_INVALID")
        expected = self._token(expected_head, "EXPECTED_HEAD_INVALID")
        key = self._token(operation_key, "OPERATION_KEY_INVALID")
        clean_title: Optional[str] = None
        clean_body: Optional[str] = None
        clean_note: Optional[str] = None
        if clean_kind == KIND_NOTE:
            if note is not None:
                raise _Stop(_bad("ANNOTATION_FIELD_NOT_ALLOWED", kind=clean_kind, field="note"))
            clean_title = self._text(title, "TITLE_INVALID", MAX_TITLE_CHARS)
            clean_body = self._text(body, "BODY_INVALID", MAX_ANNOTATION_CHARS)
        else:
            if title is not None or body is not None:
                raise _Stop(_bad("ANNOTATION_FIELD_NOT_ALLOWED", kind=clean_kind,
                                 field="title/body"))
            clean_note = self._text(note, "NOTE_INVALID", MAX_ANNOTATION_CHARS)
        intent = _intent_digest("annotation", self.reading_artifact_id, self.source_id,
                                revision, wanted, clean_kind, clean_title, clean_body,
                                clean_note)
        view = self._read_material()

        # 1) 原键先回读（旧版本上成功过的笔记/疑问不因换版而误称没成功）。
        checked = self._check_operation(key=key, intent=intent, action="annotation",
                                        page=wanted, source_revision=revision,
                                        kind=clean_kind)
        items = checked["items"]
        if checked["replay"]:
            record = checked["record"] or {}
            return _already(**self._annotation_data(
                view, clean_kind, wanted, key, str(record.get("item_id") or "") or None,
                record=record, detail="OPERATION_KEY_REPLAY"))

        # 2) 新写：当前真实材料页，而且这一版这一页真的取得过。
        if revision != str(view["revision"]):
            raise _Stop(_conflict("READING_REVISION_CONFLICT", expected_revision=revision,
                                  source_revision=str(view["revision"]),
                                  operation_key=key))
        if wanted > len(view["pages"]):
            raise _Stop(_conflict("PAGE_NOT_PRESENT", page=wanted, pages=len(view["pages"]),
                                  operation_key=key))
        if not _page_acquired(items, source_id=self.source_id,
                              source_revision=revision, page=wanted):
            # 没有"取得这一版这一页"的记录：先保存停点，再来写批注。
            raise _Stop(_conflict("PAGE_NOT_ACQUIRED", page=wanted, source_revision=revision,
                                  operation_key=key))

        stamp = self._now()
        if clean_kind == KIND_NOTE:
            item_id = "memo:" + sha256(
                ("phone|reading-note|" + key).encode("utf-8")).hexdigest()[:16]
        else:
            item_id = "unclear:" + sha256(
                ("phone|reading-unclear|" + key).encode("utf-8")).hexdigest()[:16]
        record = {"operation_key": key, "action": "annotation", "kind": clean_kind,
                  "source_id": self.source_id, "source_revision": revision, "page": wanted,
                  "intent": intent, "expected_head": expected, "at": stamp,
                  "item_id": item_id}
        try:
            if clean_kind == KIND_NOTE:
                write = items.add_memo(
                    title=str(clean_title), body=str(clean_body), memo_id=item_id,
                    operation_key=key, now=stamp,
                    reading_anchor={"source_id": self.source_id,
                                    "source_revision": revision, "page": wanted},
                    record=record, expected_head=expected)
            else:
                write = items.mark_unclear(
                    source_id=self.source_id, source_revision=revision, note=str(clean_note),
                    position=wanted, operation_key=key, now=stamp, record=record,
                    marker_id=item_id, expected_head=expected)
        except pdi.PrivateItemsConflict as exc:
            raise _Stop(self._conflict_from(exc, key=key, expected=expected))
        if write.retryable and not write.applied:
            raise _Stop(_unknown("READING_OPERATION_UNKNOWN", operation_key=key, retryable=True,
                                 detail=write.detail or "UNKNOWN_RESOLVED_NEVER_LANDED"))
        if not write.applied:
            return _already(**self._annotation_data(view, clean_kind, wanted, key, item_id,
                                                    record=record,
                                                    detail=write.detail or "NOTHING_TO_WRITE"))
        return _applied(**self._annotation_data(view, clean_kind, wanted, key, item_id,
                                                write=write, record=record))

    # -- GET: one bound operation ----------------------------------------------------------

    def _describe_owner_row(self, row: Mapping[str, Any],
                            current_revision: Optional[str]) -> dict[str, Any]:
        """把 owner 的操作行翻译成一个诚实的描述（材料键不一定在私人事项文档里）。"""
        result = _row_result(row)
        recorded_artifact = str(row.get("artifact_id") or "")
        version_id = result.get("version_id")
        parent_id = result.get("parent_version_id") or result.get("head_version_id")
        is_material = (recorded_artifact == self.reading_artifact_id
                       and str(row.get("operation_kind") or "") == _OWNER_KIND_COMMIT)
        return {
            "action": "material" if is_material else "supply",
            "artifact_id": recorded_artifact,
            "artifact_is_reading_material": is_material,
            "outcome": str(row.get("outcome") or ""),
            "version_id": version_id,
            "version_number": result.get("version_number"),
            "parent_version_id": parent_id,
            "version_created": result.get("version_created"),
            "revision_current": bool(version_id) and str(version_id) == str(current_revision),
            "material_current_revision": current_revision,
        }

    @_guarded
    def operation(self, operation_key: str) -> dict[str, Any]:
        """按 owner 自己的记录回答"这个键做了什么"。未知就保留原键，不换键重放。"""
        key = self._token(operation_key, "OPERATION_KEY_INVALID")
        _items, document = self._private()
        record = pdi.reading_operation(document, key) if document is not None else None
        row = self._owner_row(key, strict=False)
        head = None
        if self.reading_artifact_id:
            try:
                head = self.port.read_head(self.reading_artifact_id) or {}
            except (LifeArtifactError, ArtifactOwnerRefused):
                head = None
        current_revision = str((head or {}).get("version_id") or "") or None

        data: dict[str, Any] = {
            "operation_key": key,
            "record": record,
            "owner_operation": (self._describe_owner_row(row, current_revision)
                                if row is not None else None),
        }
        if record is not None:
            data.update({
                "action": str(record.get("action") or "unknown"),
                "kind": record.get("kind") or None,
                "page": record.get("page"),
                "source_revision": record.get("source_revision"),
                "item_id": record.get("item_id"),
                "effect": "APPLIED",
                "applied": True,
                "artifact_id": self.private_items_artifact_id,
            })
            return envelope(True, STATUS_APPLIED, data, http_status=200)
        if row is not None:
            data.update(self._describe_owner_row(row, current_revision))
            data.update({"effect": "APPLIED", "applied": True})
            return envelope(True, STATUS_APPLIED, data, http_status=200)

        resolved = self._resolve(key)
        data.update({
            "action": "unknown",
            "effect": {EffectState.APPLIED: "APPLIED",
                       EffectState.NO_CHANGE: "NOT_LANDED"}.get(
                           resolved.effect_state, resolved.effect_state),
            "applied": resolved.effect_state == EffectState.APPLIED,
            "owner_reason": resolved.reason,
            "artifact_id": resolved.artifact_id,
            "version_id": resolved.version_id,
        })
        if resolved.effect_state == EffectState.UNKNOWN:
            return _unknown("READING_OPERATION_UNKNOWN", **data)
        return _not_found("READING_OPERATION_NOT_FOUND", **data)


__all__ = [
    "ANNOTATION_KINDS",
    "KIND_NOTE",
    "KIND_UNCLEAR",
    "MAX_ANNOTATION_CHARS",
    "MAX_CONTENT_BYTES",
    "MAX_KEY_CHARS",
    "MAX_PAGES",
    "MAX_PAGE_BYTES",
    "MAX_TITLE_CHARS",
    "PAGE_MARKER",
    "PhoneReading",
    "PhoneReadingError",
    "STATUS_ALREADY",
    "STATUS_APPLIED",
    "STATUS_NOT_FOUND",
    "STATUS_REJECTED",
    "STATUS_UNAVAILABLE",
    "STATUS_UNKNOWN",
    "UNDERSTANDING_UNKNOWN",
    "envelope",
    "reading_pages",
    "reading_source_id",
]

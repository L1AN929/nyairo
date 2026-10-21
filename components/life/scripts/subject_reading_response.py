"""v0.4 A：主体私人阅读的「有限内容响应」——一次只产出一条笔记 / 一个疑问 / 不记录。

这是什么，不是什么
------------------
* 这是私人阅读里**唯一**允许内容模型产出私人文字的地方：输入是 B 给的那一页**真实正文**
  （``source_id`` / ``source_revision`` / ``page`` / ``text`` / ``previous_page`` /
  ``total_pages``），输出是三种意图之一：``note``（标题 + 正文）、``unclear``（一句疑问）、
  ``no_change``（什么都不记）。
* 它**不读 socket、不写 owner、不碰 PrivateDailyItems**：一次「问一次、解析一次、返回一个字典」。
  把响应变成文档版本的是 :func:`private_daily_items.plan_page_annotation`（纯计划，另跑一版），
  提交与 CAS 由 B 做。本模块既不知道 artifact，也不知道操作键。
* 它**不伪造**：没有 client 就拒绝构造（``MODEL_CLIENT_REQUIRED``）；模型调用失败、输出不是
  JSON、kind 不认识、字段越权、长度超界，一律抛 :class:`ReadingResponseError`。调用方拿到的是
  失败，不是一段替身笔记 —— ``no_change`` 是合法答案，"必须产出"不是。
* 正文只作为**这一次请求**的内容：本模块不留 ``raw`` 输出、不把笔记正文写进 ``describe()``。
  ``describe()`` 只有键、调用次数、上限和一个错误码；``last_response_chars`` 只是长度。
* 不解码理解：这里没有把页码、笔记或疑问变成"懂了"的路，``understanding`` 仍归 PDI 的
  ``UNKNOWN``。

严格型（谁都不能悄悄放宽）
--------------------------
* kind 只能是 ``note`` / ``unclear`` / ``no_change``。
* note：``title`` 与 ``body`` 都必须是非空文本，``title`` <= 200 字，``body`` <=
  ``max_note_chars``（默认 1200）；note 不许带 ``note`` 字段。
* unclear：``note`` 必须是非空文本 <= ``max_note_chars``；不许带 ``title`` / ``body``。
* no_change：不许带任何正文 —— ``title`` / ``body`` / ``note`` 只要有一个非空就拒绝。
  空字符串只是模型模板残留，归一化成 ``None``，不会被当成"伪正文"放过，也不会被当成内容。
* 模型输出对象只认 ``kind`` / ``title`` / ``body`` / ``note`` / ``self_note`` 五个键，别的键一律
  拒绝：多出来的字段很可能就是被偷偷塞进来的正文。``self_note`` 允许但**丢弃**（它也可能含私人
  文字，所以既不返回也不记录）。
* 超长**不截断**：截断等于对"她到底写了什么"说谎，宁可失败。
* 空页（``text`` 全空白）拒绝（``READING_TEXT_REQUIRED``）：给模型一个空页面，只会换来编造。
  B 应在这种情况下**不发起**内容请求，照常保存真实取得与停点。
"""
from __future__ import annotations

import json
import re
import sys
from collections.abc import Mapping as AbcMapping
from pathlib import Path
from typing import Any, Mapping, Optional

_LIFE_SCRIPTS = str(Path(__file__).resolve().parent)
if _LIFE_SCRIPTS not in sys.path:
    sys.path.insert(0, _LIFE_SCRIPTS)

from agency_model_client import AgencyModelClientError  # noqa: E402

#: 三种合法意图。之外的 kind 是拒绝，不是默认成 note。
KIND_NOTE = "note"
KIND_UNCLEAR = "unclear"
KIND_NO_CHANGE = "no_change"
RESPONSE_KINDS: tuple[str, ...] = (KIND_NOTE, KIND_UNCLEAR, KIND_NO_CHANGE)

#: 一条私人笔记的标题上限。正文与疑问共用一个上限（``max_note_chars``）。
MAX_TITLE_CHARS = 200
DEFAULT_MAX_PAGE_CHARS = 6000
DEFAULT_MAX_NOTE_CHARS = 1200

#: B 提供的 context 只认这六个键：多一个键就多一条可能被送进模型的私人数据。
CONTEXT_FIELDS: tuple[str, ...] = (
    "source_id", "source_revision", "page", "text", "previous_page", "total_pages",
)
#: 模型输出对象允许的键。``self_note`` 只是为了容忍模型模板，会被丢弃。
OUTPUT_FIELDS: tuple[str, ...] = ("kind", "title", "body", "note", "self_note")

SYSTEM_PROMPT_TEMPLATE = (
    "你在读自己的一份私人材料，眼前只有**这一页**。没有任何人要求你必须产出东西。\n"
    "你可以从下面三种里选一种：\n"
    "1) 写一条私人笔记（kind=\"note\"）：给一个短标题和一个正文，写你在这页上真正注意到的东西；\n"
    "2) 记一个疑问（kind=\"unclear\"）：写一句你没弄明白的地方；\n"
    "3) 什么都不记（kind=\"no_change\"）：这一页不需要留下东西，这也是合法结果，不用交代理由。\n"
    "约束：\n"
    "- 只用这一页的文字，不要编造页面上没有的内容；不要总结全文；不要声称自己\"读懂了\"。\n"
    "- 只回复一个 JSON 对象，键为：\n"
    "  \"kind\": \"note\" | \"unclear\" | \"no_change\"\n"
    "  note 时另给 \"title\"（<=%d 字）与 \"body\"（<=%d 字）；\n"
    "  unclear 时另给 \"note\"（<=%d 字）；\n"
    "  no_change 时不要给任何正文。\n"
    "- 不要输出 JSON 之外的任何字符。"
)

#: 默认渲染（标题上限 + 默认正文上限）。``_system_prompt`` 会按实例自己的 ``max_note_chars``
#: 重渲染，prompt 里承诺的上限永远不会比解析器真正接受的更宽。
SYSTEM_PROMPT = SYSTEM_PROMPT_TEMPLATE % (MAX_TITLE_CHARS, DEFAULT_MAX_NOTE_CHARS,
                                          DEFAULT_MAX_NOTE_CHARS)


class ReadingResponseError(RuntimeError):
    """这一次内容响应不可用。调用方必须把它当失败，不许用替身内容顶上。"""


def _require_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReadingResponseError("%s_REQUIRED" % name.upper())
    return value


def _optional_count(value: Any, name: str) -> Optional[int]:
    """A 1-based page-ish number, or ``None``.  A bool is never a count."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ReadingResponseError("%s_INVALID" % name.upper())
    return int(value)


def _clean_page(value: Any) -> Any:
    """The page a response is about: a 1-based number or a non-empty locator string."""
    if isinstance(value, bool):
        raise ReadingResponseError("PAGE_INVALID")
    if isinstance(value, int):
        if value < 1:
            raise ReadingResponseError("PAGE_INVALID")
        return value
    if isinstance(value, str) and value.strip():
        return value
    raise ReadingResponseError("PAGE_INVALID")


def _clean_context(context: Mapping[str, Any]) -> dict[str, Any]:
    """Validate exactly the context B owns.  Unknown keys are refused, never forwarded."""
    if not isinstance(context, AbcMapping):
        raise ReadingResponseError("READING_CONTEXT_REQUIRED")
    unknown = sorted(str(key) for key in context if str(key) not in CONTEXT_FIELDS)
    if unknown:
        raise ReadingResponseError("READING_CONTEXT_UNKNOWN_FIELD:%s" % ",".join(unknown))
    text = context.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ReadingResponseError("READING_TEXT_REQUIRED")
    return {
        "source_id": _require_text(context.get("source_id"), "source_id"),
        "source_revision": _require_text(context.get("source_revision"), "source_revision"),
        "page": _clean_page(context.get("page")),
        "text": text,
        "previous_page": _optional_count(context.get("previous_page"), "previous_page"),
        "total_pages": _optional_count(context.get("total_pages"), "total_pages"),
    }


def _extract_json(raw: str) -> dict[str, Any]:
    """One JSON object out of the model's text.  Nothing is guessed past that."""
    text = (raw or "").strip()
    if not text:
        raise ReadingResponseError("MODEL_OUTPUT_EMPTY")
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        parsed = json.loads(text)
    except ValueError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ReadingResponseError("MODEL_OUTPUT_NOT_JSON")
        try:
            parsed = json.loads(match.group(0))
        except ValueError as exc:
            raise ReadingResponseError("MODEL_OUTPUT_NOT_JSON:%s" % type(exc).__name__) from exc
    if not isinstance(parsed, dict):
        raise ReadingResponseError("MODEL_OUTPUT_NOT_AN_OBJECT")
    return parsed


def _clean_fields(response: Mapping[str, Any]) -> dict[str, Optional[str]]:
    values: dict[str, Optional[str]] = {}
    for name in ("title", "body", "note"):
        value = response.get(name)
        if value is None:
            values[name] = None
            continue
        if not isinstance(value, str):
            raise ReadingResponseError("RESPONSE_FIELD_INVALID:%s" % name)
        values[name] = value.strip() or None
    return values


def normalise_response(response: Mapping[str, Any], *,
                       max_note_chars: int = DEFAULT_MAX_NOTE_CHARS
                       ) -> tuple[str, Optional[str], Optional[str], Optional[str]]:
    """Validate one response object into ``(kind, title, body, note)``.

    Shared by :meth:`ModelReadingResponse.respond` and (as a pinned mirror) by
    :func:`private_daily_items.plan_page_annotation`, so a parsed response and a planned
    annotation cannot disagree about what is legal.
    """
    if not isinstance(response, AbcMapping):
        raise ReadingResponseError("RESPONSE_REQUIRED")
    unknown = sorted(str(key) for key in response if str(key) not in OUTPUT_FIELDS)
    if unknown:
        raise ReadingResponseError("RESPONSE_UNKNOWN_FIELD:%s" % ",".join(unknown))
    raw_kind = response.get("kind")
    kind = raw_kind.strip().lower() if isinstance(raw_kind, str) else ""
    if kind not in RESPONSE_KINDS:
        raise ReadingResponseError("RESPONSE_KIND_INVALID:%s" % str(raw_kind)[:24])
    values = _clean_fields(response)

    if kind == KIND_NOTE:
        if values["note"] is not None:
            raise ReadingResponseError("RESPONSE_FIELD_NOT_ALLOWED:%s:note" % kind)
        for name in ("title", "body"):
            if values[name] is None:
                raise ReadingResponseError("RESPONSE_FIELD_EMPTY:%s" % name)
        if len(values["title"] or "") > MAX_TITLE_CHARS:
            raise ReadingResponseError("RESPONSE_TITLE_TOO_LONG:%d>%d"
                                       % (len(values["title"] or ""), MAX_TITLE_CHARS))
        if len(values["body"] or "") > int(max_note_chars):
            raise ReadingResponseError("RESPONSE_BODY_TOO_LONG:%d>%d"
                                       % (len(values["body"] or ""), int(max_note_chars)))
        return kind, values["title"], values["body"], None

    if kind == KIND_UNCLEAR:
        for name in ("title", "body"):
            if values[name] is not None:
                raise ReadingResponseError("RESPONSE_FIELD_NOT_ALLOWED:%s:%s" % (kind, name))
        if values["note"] is None:
            raise ReadingResponseError("RESPONSE_FIELD_EMPTY:note")
        if len(values["note"] or "") > int(max_note_chars):
            raise ReadingResponseError("RESPONSE_NOTE_TOO_LONG:%d>%d"
                                       % (len(values["note"] or ""), int(max_note_chars)))
        return kind, None, None, values["note"]

    # no_change: no text at all.  "伪正文" is refused, never quietly dropped.
    for name in ("title", "body", "note"):
        if values[name] is not None:
            raise ReadingResponseError("RESPONSE_FIELD_NOT_ALLOWED:%s:%s" % (kind, name))
    return kind, None, None, None


class ModelReadingResponse:
    """Asks the configured model for at most one private note, one question, or nothing.

    The client is injected exactly like the work writer's: anything with
    ``complete(system_prompt=..., user_prompt=..., timeout_s=..., max_output_tokens=...)``
    returning an object with ``text``.  Tests inject a fake; nothing here reaches a provider.
    """

    def __init__(self, client: Any, *, max_page_chars: int = DEFAULT_MAX_PAGE_CHARS,
                 max_note_chars: int = DEFAULT_MAX_NOTE_CHARS, timeout_s: float = 20,
                 max_output_tokens: int = 512) -> None:
        if client is None or not callable(getattr(client, "complete", None)):
            # No fallback content: without a model there is no response, and B reports
            # NOT_CONFIGURED instead of pretending she wrote something.
            raise ReadingResponseError("MODEL_CLIENT_REQUIRED")
        if isinstance(max_page_chars, bool) or int(max_page_chars) < 1:
            raise ReadingResponseError("MAX_PAGE_CHARS_INVALID")
        if isinstance(max_note_chars, bool) or int(max_note_chars) < 1:
            raise ReadingResponseError("MAX_NOTE_CHARS_INVALID")
        self.client = client
        self.max_page_chars = int(max_page_chars)
        self.max_note_chars = int(max_note_chars)
        self.timeout_s = float(timeout_s)
        self.max_output_tokens = int(max_output_tokens)
        #: Diagnostics only.  No private text is kept here: no raw output, no note body.
        self.call_count = 0
        self.last_kind: Optional[str] = None
        self.last_error: Optional[str] = None
        self.last_prompt_chars = 0
        self.last_response_chars = 0
        self.last_page_text_truncated = False

    # -- prompt -------------------------------------------------------------------------

    def _user_prompt(self, context: Mapping[str, Any], *, observed_at: str) -> tuple[str, bool]:
        text = str(context["text"])
        sent = text[: self.max_page_chars]
        truncated = len(sent) < len(text)
        payload = {
            "source_id": context["source_id"],
            "source_revision": context["source_revision"],
            "page": context["page"],
            "total_pages": context["total_pages"],
            "previous_page": context["previous_page"],
            "observed_at": observed_at,
            "page_text": sent,
            "page_text_chars": len(sent),
            "page_text_truncated": truncated,
            "limits": {"title_chars": MAX_TITLE_CHARS, "note_chars": self.max_note_chars},
            "instruction": "读完这一页：写一条私人笔记、记一个疑问，或者什么都不记。",
        }
        return json.dumps(payload, ensure_ascii=False), truncated

    # -- the response protocol ----------------------------------------------------------

    def respond(self, context: Mapping[str, Any], *, observed_at: str) -> dict[str, Any]:
        """One bounded response for one real page.  Raises on anything unusable.

        Returns ``{"kind", "title", "body", "note", "source_id", "source_revision", "page",
        "observed_at", "prompt_chars", "page_chars", "page_text_truncated"}``.  For
        ``no_change`` the three text fields are ``None`` -- never an empty string that a later
        layer could mistake for content.
        """
        clean = _clean_context(context)
        stamp = _require_text(observed_at, "observed_at")
        user_prompt, truncated = self._user_prompt(clean, observed_at=stamp)
        self.last_prompt_chars = len(user_prompt)
        self.last_error = None
        try:
            result = self.client.complete(
                system_prompt=self._system_prompt(),
                user_prompt=user_prompt,
                timeout_s=self.timeout_s,
                max_output_tokens=self.max_output_tokens,
            )
        except AgencyModelClientError as exc:
            # Only the failure class: a transport message can echo the request (private text).
            self.last_error = "MODEL_CALL_FAILED:%s" % str(exc.failure_class or "UNKNOWN")[:48]
            raise ReadingResponseError(self.last_error) from exc
        except Exception as exc:  # noqa: BLE001 - a model failure is a real failure
            self.last_error = "MODEL_CALL_FAILED:%s" % type(exc).__name__
            raise ReadingResponseError(self.last_error) from exc
        self.call_count += 1
        raw = str(getattr(result, "text", "") or "")
        self.last_response_chars = len(raw)
        try:
            parsed = _extract_json(raw)
            kind, title, body, note = normalise_response(parsed,
                                                        max_note_chars=self.max_note_chars)
        except ReadingResponseError as exc:
            self.last_error = str(exc)[:120]
            raise
        self.last_kind = kind
        self.last_page_text_truncated = truncated
        return {
            "kind": kind,
            "title": title,
            "body": body,
            "note": note,
            "source_id": clean["source_id"],
            "source_revision": clean["source_revision"],
            "page": clean["page"],
            "observed_at": stamp,
            "prompt_chars": len(user_prompt),
            "page_chars": min(len(clean["text"]), self.max_page_chars),
            "page_text_truncated": truncated,
        }

    def _system_prompt(self) -> str:
        """The system prompt rendered with this instance's own note bound."""
        if self.max_note_chars == DEFAULT_MAX_NOTE_CHARS:
            return SYSTEM_PROMPT
        return SYSTEM_PROMPT_TEMPLATE % (MAX_TITLE_CHARS, self.max_note_chars,
                                         self.max_note_chars)

    def describe(self) -> dict[str, Any]:
        """Configuration and counters.  Never a body, a note, or a raw model output."""
        return {
            "kind": "model_reading_response",
            "calls": self.call_count,
            "last_kind": self.last_kind,
            "last_error": self.last_error,
            "last_prompt_chars": self.last_prompt_chars,
            "last_response_chars": self.last_response_chars,
            "last_page_text_truncated": self.last_page_text_truncated,
            "max_page_chars": self.max_page_chars,
            "max_note_chars": self.max_note_chars,
            "timeout_s": self.timeout_s,
            "max_output_tokens": self.max_output_tokens,
        }


__all__ = [
    "DEFAULT_MAX_NOTE_CHARS",
    "DEFAULT_MAX_PAGE_CHARS",
    "KIND_NO_CHANGE",
    "KIND_NOTE",
    "KIND_UNCLEAR",
    "MAX_TITLE_CHARS",
    "ModelReadingResponse",
    "OUTPUT_FIELDS",
    "RESPONSE_KINDS",
    "ReadingResponseError",
    "SYSTEM_PROMPT",
    "SYSTEM_PROMPT_TEMPLATE",
    "normalise_response",
]

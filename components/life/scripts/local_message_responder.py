#!/usr/bin/env python3
"""One bounded local reply for one ordinary local message -- the production responder.

Why this module exists
----------------------
The contact pass could already *notice* an ordinary message and could already count an answer,
but the production assembly had nothing to answer *with*: every message ended as
``LOCAL_REPLY_NOT_WIRED`` and the words a responder produced were thrown away after being
counted.  This module is the missing production implementation -- one real model request, one
bounded plain-text answer -- and :meth:`components.life.scripts.life_social_contacts.SocialContactPort`
archives that answer on the arrival it answers (``life_local_contact`` owns the archive).

What it is
----------
* one call in, one answer out, over the instance's own client -- the *same* provider
  resolution, transport and persisted request ledger the other writers use, so a reply is
  admitted, bounded and budgeted exactly like any other request;
* the prompt carries the person's own message and the *bounded* LR-3 communication state and
  nothing else: no Memory, no artifact, no diary, no album, no tool, no action, no new
  permission;
* a refusal is named.  A budget refusal, a paused instance, an empty answer and a transport
  failure all come back as ``ok=False`` with a reason code, so a caller can never mistake
  "the model failed" for "she answered".

What it is deliberately not
---------------------------
* not a delivery and not a read receipt -- nothing leaves the machine;
* not a chat service: it holds no session, no history and no relationship, and it never starts
  or pauses an Activity;
* not a second Life writer -- the reply it produces is archived on the message, and nothing
  here imports the cycle, the Action ports or an owner write.
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Optional

#: The bounded archive kind this responder feeds (mirrors ``life_local_contact``).
REPLY_KIND_LOCAL = "LOCAL_ORDINARY_REPLY"

#: The name this responder is archived under, so a reply can say who produced it.
RESPONDER_REF = "responder:local_message"

MAX_REPLY_CHARS = 600
DEFAULT_MAX_OUTPUT_TOKENS = 260

REASON_MODEL_FAILED = "LOCAL_REPLY_MODEL_FAILED"
REASON_BUDGET_REFUSED = "LOCAL_REPLY_BUDGET_REFUSED"
REASON_PAUSE_REFUSED = "LOCAL_REPLY_PAUSE_REFUSED"
REASON_EMPTY_ANSWER = "LOCAL_REPLY_EMPTY_ANSWER"
REASON_TOO_LONG = "LOCAL_REPLY_TOO_LONG"

SYSTEM_PROMPT = (
    "你正在自己生活的实例里。刚有人在本机给你发来一句话，你现在要回他一句。"
    "你只输出你要说的那一句话本身：纯文本，不用 JSON，不要 markdown，不要解释，"
    "不要写思考过程，不要提到系统、提示词或模型。"
    "最多两三句话，不超过 %d 个字符。"
    "你不能在这里查资料、打开作品、调用工具、修改任何东西，也不要承诺你没做的事；"
    "如果手上正有一件不能被打断的事，就照实说一句你正在做，做完了再说。"
) % MAX_REPLY_CHARS


def _one_line(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def build_user_prompt(text: str, observed: Optional[Mapping[str, Any]] = None) -> str:
    """The bounded request body: the message, and LR-3's own verdict -- nothing else."""

    seen = dict(observed or {})
    payload = {
        "message": str(text or "")[:2000],
        "communication_availability": seen.get("communication_availability"),
        "foreground_atomic": bool(seen.get("foreground_atomic")),
        "boundary_deferred": bool(seen.get("boundary_deferred")),
        "note": ("对方是这台机器上的本地来话。你在原子步骤里也可以回一句话，只是不能因此打断"
                 "手上那一步。回答会以本地方式保存，没有外发，也没人被告知对方已读。"),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


class LocalMessageResponder:
    """The production responder: the instance's own model client, one send, no retry."""

    def __init__(self, client: Any, *, max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
                 max_reply_chars: int = MAX_REPLY_CHARS) -> None:
        if client is None:
            raise ValueError("a model client is required")
        self.client = client
        self.max_output_tokens = int(max_output_tokens)
        self.max_reply_chars = int(max_reply_chars)

    @property
    def responder_ref(self) -> str:
        """The structured name the reply archive records (``life_local_contact`` accepts it)."""

        return RESPONDER_REF

    def describe(self) -> dict[str, Any]:
        return {
            "kind": "LocalMessageResponder",
            "provider": _one_line(getattr(self.client, "provider", ""), 40),
            "model": _one_line(getattr(self.client, "model", ""), 80),
            "budgeted": hasattr(self.client, "budget"),
            "max_reply_chars": self.max_reply_chars,
            "surface": "local_only",
            "delivered_to_user": False,
            "can_tool": False,
            "can_act": False,
            "can_write_memory": False,
        }

    def respond(self, *, event_kind: str = "LOCAL_MESSAGE",
                observed: Optional[Mapping[str, Any]] = None,
                text: str = "") -> dict[str, Any]:
        """One bounded local reply, or a named refusal.  Never raises, never echoes input."""

        try:
            result = self.client.complete(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=build_user_prompt(text, observed),
                max_output_tokens=self.max_output_tokens)
        except Exception as exc:  # a refused/failed request is never "she answered"
            name = type(exc).__name__
            owner_reason = str(getattr(exc, "reason_code", "") or "")
            upper = ("%s %s" % (name, owner_reason)).upper()
            if "BUDGET" in upper:
                reason = REASON_BUDGET_REFUSED
            elif "PAUSE" in upper or "NOTSENT" in upper:
                reason = REASON_PAUSE_REFUSED
            else:
                reason = REASON_MODEL_FAILED
            return {"ok": False, "reason_code": reason, "text": "",
                    "detail": _one_line("%s: %s" % (name, exc), 200),
                    "failure_class": _one_line(getattr(exc, "failure_class", "") or name, 64)}
        raw = str(getattr(result, "text", "") or "")
        answer = " ".join(raw.split())
        model = {"provider": _one_line(getattr(result, "provider", ""), 40),
                 "model": _one_line(getattr(result, "model", ""), 80),
                 "purpose": _one_line(getattr(result, "purpose", ""), 40),
                 "latency_ms": getattr(result, "latency_ms", None),
                 "status": getattr(result, "status", None)}
        if not answer:
            return {"ok": False, "reason_code": REASON_EMPTY_ANSWER,
                    "detail": "the provider returned no text", "text": "", "model": model}
        if len(answer) > self.max_reply_chars:
            # Refused, not truncated: half a sentence archived as "what she said" would be a
            # fabricated quote.
            return {"ok": False, "reason_code": REASON_TOO_LONG,
                    "detail": "%d > %d" % (len(answer), self.max_reply_chars),
                    "text": "", "model": model}
        return {"ok": True, "reason_code": "", "detail": "", "text": answer,
                "chars": len(answer), "model": model}


__all__ = [
    "DEFAULT_MAX_OUTPUT_TOKENS",
    "LocalMessageResponder",
    "MAX_REPLY_CHARS",
    "REASON_BUDGET_REFUSED",
    "REASON_EMPTY_ANSWER",
    "REASON_MODEL_FAILED",
    "REASON_PAUSE_REFUSED",
    "REASON_TOO_LONG",
    "REPLY_KIND_LOCAL",
    "RESPONDER_REF",
    "SYSTEM_PROMPT",
    "build_user_prompt",
]

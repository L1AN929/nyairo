"""v0.2 B4/E06: the model-backed continuation writer for the booklet.

What it is
----------
The one place a model is allowed to produce *content* for the work.  It is deliberately narrow:
the model proposes a single small edit, and this module merges it into the document that the
owner actually holds.  The model never receives the whole private work and never returns the
whole work.

Rules it keeps
--------------
* The current text always comes from the owner's own ``read_head`` (via the resume point), never
  from a cached prompt or from memory -- E06.2's "read the previous version before continuing".
* Only the tail of the document is sent, bounded by ``max_prompt_chars``, because the work is
  continued at its end.  Private content is not sprayed into the prompt.
* Only two merge kinds are accepted: append one paragraph, or replace the last paragraph.  A
  response that would rewrite the file is refused, and an over-long paragraph is trimmed to
  ``max_new_chars`` and reported as trimmed.
* A model failure is a real failure: it raises, and the cycle reports the round as a failed step
  instead of writing something the model did not say.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

_LIFE_SCRIPTS = str(Path(__file__).resolve().parent)
if _LIFE_SCRIPTS not in sys.path:
    sys.path.insert(0, _LIFE_SCRIPTS)

from agency_model_client import AgencyModelClientError  # noqa: E402
from life_artifact_port import ArtifactResumePoint  # noqa: E402

KIND_APPEND = "append_paragraph"
KIND_REVISE_TAIL = "revise_tail"
ALLOWED_KINDS = frozenset({KIND_APPEND, KIND_REVISE_TAIL})

SYSTEM_PROMPT = (
    "你在续写一本私人的 Markdown 手记。只做一个小改动，不要重写全文，不要解释，不要加标题。\n"
    "只回复一个 JSON 对象，键为：\n"
    '  "kind": "append_paragraph" 或 "revise_tail"\n'
    '  "text": 你要写入的那一段正文（纯文本，不含 Markdown 标题）\n'
    '  "self_note": 一句话说明你为什么这样改（可空）\n'
    "不要输出 JSON 之外的任何字符。"
)



#: How many admitted experiences may enter one content prompt, and how much text in total.
DEFAULT_MAX_EXPERIENCES = 3
DEFAULT_MAX_EXPERIENCE_CHARS = 600
class ModelContentError(RuntimeError):
    """The model did not produce a usable small edit."""


class ModelContinuationWriter:
    """Asks the configured model for one bounded edit of the work it is holding."""

    def __init__(
        self,
        client: Any,
        *,
        max_new_chars: int = 400,
        max_prompt_chars: int = 4000,
        timeout_s: float = 20.0,
        max_output_tokens: int = 512,
        title_hint: Optional[str] = None,
        experience_provider: Optional[Any] = None,
        max_experiences: int = DEFAULT_MAX_EXPERIENCES,
        max_experience_chars: int = DEFAULT_MAX_EXPERIENCE_CHARS,
    ) -> None:
        self.client = client
        self.max_new_chars = int(max_new_chars)
        self.max_prompt_chars = int(max_prompt_chars)
        self.timeout_s = float(timeout_s)
        self.max_output_tokens = int(max_output_tokens)
        self.title_hint = title_hint
        #: A callable returning the bounded, governance-filtered experience view.  Supplying an
        #: empty one is exactly how the A/B control removes memory without deleting anything.
        self.experience_provider = experience_provider
        self.max_experiences = max(0, int(max_experiences))
        self.max_experience_chars = max(0, int(max_experience_chars))
        self.last_experience_ids: list[str] = []
        self.call_count = 0
        self.last_raw_output: Optional[str] = None
        self.last_kind: Optional[str] = None
        self.last_trimmed = False
        self.last_self_note: Optional[str] = None

    # -- prompt ------------------------------------------------------------------------

    def _user_prompt(self, point: ArtifactResumePoint, *, observed_at: str) -> str:
        current = point.content or ""
        tail = current[-self.max_prompt_chars:] if len(current) > self.max_prompt_chars else current
        payload = {
            "work_title": point.title or self.title_hint or "(未命名手记)",
            "current_version_number": point.version_number,
            "current_version_id": point.head_version_id,
            "observed_at": observed_at,
            "already_written_chars": len(current),
            "tail_of_current_text": tail,
            "previous_experiences": self._experience_view(),
            "instruction": (
                f"给这本手记加一小段，或把最后一段改写一下。新内容不超过 {self.max_new_chars} 字。"
                "如果这段手记已经不需要再改，也要照常给出一个最小的改动，不要拒绝。"
            ),
        }
        return json.dumps(payload, ensure_ascii=False)

    def _experience_view(self) -> list[dict[str, Any]]:
        """The bounded, attributable slice of her own admitted experiences.

        Empty when no provider is installed, when the provider fails, or when governance cannot
        establish what is safe to recall.  Memory may inform a choice here; it never supplies the
        work's current text, which still comes from the owner.
        """
        self.last_experience_ids = []
        if self.experience_provider is None or self.max_experiences == 0:
            return []
        try:
            items = list(self.experience_provider() or [])
        except Exception:
            return []
        view: list[dict[str, Any]] = []
        budget = self.max_experience_chars
        for item in items[: self.max_experiences]:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "")
            if budget > 0:
                text = text[:budget]
                budget -= len(text)
            experience_id = item.get("experience_id")
            if experience_id:
                self.last_experience_ids.append(str(experience_id))
            view.append({"experience_id": experience_id, "text": text,
                         "artifact_ref": item.get("artifact_ref"),
                         "version_ref": item.get("version_ref"),
                         "owner_result": item.get("owner_result")})
        return view

    # -- parse -------------------------------------------------------------------------

    @staticmethod
    def _extract_json(raw: str) -> dict[str, Any]:
        text = (raw or "").strip()
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
                raise ModelContentError("MODEL_OUTPUT_NOT_JSON")
            try:
                parsed = json.loads(match.group(0))
            except ValueError as exc:
                raise ModelContentError(f"MODEL_OUTPUT_NOT_JSON:{exc}") from exc
        if not isinstance(parsed, dict):
            raise ModelContentError("MODEL_OUTPUT_NOT_AN_OBJECT")
        return parsed

    # -- merge -------------------------------------------------------------------------

    def _merge(self, current: str, kind: str, text: str) -> str:
        if kind == KIND_APPEND:
            sep = "" if current.endswith("\n\n") or not current else "\n\n"
            return f"{current}{sep}{text}\n"
        # revise_tail: replace the final paragraph, keeping everything before it.
        blocks = re.split(r"\n\s*\n", current.rstrip("\n"))
        if len(blocks) <= 1:
            return f"{text}\n"
        return "\n\n".join(blocks[:-1]) + f"\n\n{text}\n"

    # -- the writer protocol -----------------------------------------------------------

    def next_content(self, point: ArtifactResumePoint, *, observed_at: str) -> str:
        current = point.content or ""
        self.last_trimmed = False
        self.last_self_note = None
        try:
            result = self.client.complete(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=self._user_prompt(point, observed_at=observed_at),
                timeout_s=self.timeout_s,
                max_output_tokens=self.max_output_tokens,
            )
        except AgencyModelClientError as exc:
            raise ModelContentError(f"MODEL_CALL_FAILED:{exc}") from exc
        self.call_count += 1
        raw = str(getattr(result, "text", "") or "")
        self.last_raw_output = raw

        parsed = self._extract_json(raw)
        kind = str(parsed.get("kind") or KIND_APPEND)
        if kind not in ALLOWED_KINDS:
            raise ModelContentError(f"MODEL_EDIT_KIND_REFUSED:{kind}")
        text = str(parsed.get("text") or "").strip()
        if not text:
            raise ModelContentError("MODEL_EDIT_EMPTY")
        if len(text) > self.max_new_chars:
            text = text[: self.max_new_chars].rstrip() + "…"
            self.last_trimmed = True
        self.last_kind = kind
        self.last_self_note = str(parsed.get("self_note") or "")[:200] or None

        merged = self._merge(current, kind, text)
        if merged == current:
            raise ModelContentError("MODEL_EDIT_CHANGED_NOTHING")
        return merged

    def describe(self) -> dict[str, Any]:
        return {"kind": "model_continuation_writer", "calls": self.call_count,
                "last_kind": self.last_kind, "last_trimmed": self.last_trimmed,
                "last_self_note": self.last_self_note,
                "max_new_chars": self.max_new_chars}


__all__ = [
    "ALLOWED_KINDS",
    "KIND_APPEND",
    "KIND_REVISE_TAIL",
    "ModelContentError",
    "ModelContinuationWriter",
    "SYSTEM_PROMPT",
]

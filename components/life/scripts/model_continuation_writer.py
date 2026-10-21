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
* Three answers are accepted: append one paragraph, replace the last paragraph, or
  ``no_change`` -- "there is nothing to write here".  A response that would rewrite the file is
  refused, and an over-long paragraph is trimmed to ``max_new_chars`` and reported as trimmed.
* ``no_change`` is a *legal* answer, not a failure: the model is never asked to invent an edit
  just so that something comes back.  It is carried out of ``next_content`` as the dedicated
  :class:`ModelNoChange` signal, which is deliberately **not** a :class:`ModelContentError` --
  a subject who has nothing to add has not failed, and a round that answers ``no_change`` must
  not be reported as a model failure.
* A model failure is a real failure: it raises, and the cycle reports the round as a failed step
  instead of writing something the model did not say.
* **A machine header stays put.**  When the current text begins with an HTML-comment block (a
  research work's marker, for instance), the merge treats it as a header: ``append`` writes below
  it and ``revise_tail`` rewrites only prose, so the marker can never be moved, trashed or
  rewritten by an edit.  ``split_header`` is a generic Markdown rule, not a second copy of any
  owner's marker spelling.
* **Research context is document context, not memory.**  An optional ``context`` mapping (read by
  the caller from the owner through the research workspace) rides into the prompt under
  ``research_context``.  The work's own text still comes from the owner's head, and the admitted
  experience provider is untouched.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Optional

_LIFE_SCRIPTS = str(Path(__file__).resolve().parent)
if _LIFE_SCRIPTS not in sys.path:
    sys.path.insert(0, _LIFE_SCRIPTS)

from agency_model_client import AgencyModelClientError  # noqa: E402
from life_artifact_port import ArtifactResumePoint  # noqa: E402

KIND_APPEND = "append_paragraph"
KIND_REVISE_TAIL = "revise_tail"
#: "There is nothing to write here."  A legal answer, and the reason no edit is fabricated.
KIND_NO_CHANGE = "no_change"
ALLOWED_KINDS = frozenset({KIND_APPEND, KIND_REVISE_TAIL, KIND_NO_CHANGE})

SYSTEM_PROMPT = (
    "你在续写一本私人的 Markdown 手记。不要重写全文，不要解释，不要加标题。\n"
    "手记的第一行如果是 <!-- ... --> 这样的标记，那是文件自带的，必须原样保留，不要改写也不要删除。\n"
    "只回复一个 JSON 对象，键为：\n"
    '  "kind": "append_paragraph"、"revise_tail" 或 "no_change"\n'
    '  "text": 你要写入的那一段正文（纯文本，不含 Markdown 标题；no_change 时必须为空）\n'
    '  "self_note": 一句话说明你为什么这样改（可空）\n'
    "如果提示里给了 research_context，只用其中的来源正文与版本；不要编造没有列出的来源。\n"
    "如果提示里给了 practice_context，那是她自己写下的做法草案（instruction/scope/uncertainty）"
    "与回顾：只能作为写作参考，不能声称某条做法被执行过、不能被当成权限或稳定人格。\n"
    "如果这一页手记确实不需要再动，就回 no_change —— 不要为了有输出而硬加字。\n"
    "不要输出 JSON 之外的任何字符。"
)


#: v0.4 work-revisit: how much of an *earlier* version may ride into one writing request.  One or
#: two bounded excerpts, never a history dump: the writer is told what this same work said before,
#: not what every version said.
MAX_EARLIER_ENTRIES = 2
MAX_EARLIER_CHARS = 600


def _earlier_view(context: Any) -> list[dict[str, Any]]:
    """The bounded ``earlier_versions`` half of a writing context, or an empty list.

    The caller is expected to have read each excerpt from the owner's ``read_version`` and to have
    passed it through the project service's source gate; this helper only bounds and shapes it, so
    a caller cannot hand the model an unbounded slice of someone's history by accident.
    """
    if not isinstance(context, Mapping):
        return []
    raw = context.get("earlier_versions")
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[dict[str, Any]] = []
    for item in list(raw)[:MAX_EARLIER_ENTRIES]:
        if not isinstance(item, Mapping):
            continue
        excerpt = item.get("excerpt")
        if not isinstance(excerpt, str) or not excerpt.strip():
            continue
        out.append({
            "version_id": str(item.get("version_id") or ""),
            "version_number": item.get("version_number"),
            "excerpt": excerpt[:MAX_EARLIER_CHARS],
        })
    return out
#: How many admitted experiences may enter one content prompt, and how much text in total.
DEFAULT_MAX_EXPERIENCES = 3
DEFAULT_MAX_EXPERIENCE_CHARS = 600
class ModelContentError(RuntimeError):
    """The model did not produce a usable small edit."""


class ModelNoChange(RuntimeError):
    """The model answered, explicitly, that this work needs no change right now.

    It is *not* a :class:`ModelContentError`: nothing failed, no text was produced and nothing
    may be written.  ``next_content`` raises it instead of returning a string, so every caller
    has to decide honestly (the cycle reports a legal no-op) rather than accidentally writing
    an unchanged document as if it were an edit.
    """

    def __init__(self, reason: str = "") -> None:
        super().__init__(reason or "NO_CHANGE")
        self.reason = reason or "NO_CHANGE"


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
        #: How many times the writer answered, explicitly, "nothing to change here".
        self.no_change_count = 0

    # -- prompt ------------------------------------------------------------------------

    def _user_prompt(self, point: ArtifactResumePoint, *, observed_at: str,
                     context: Optional[Mapping[str, Any]] = None) -> str:
        current = point.content or ""
        tail = current[-self.max_prompt_chars:] if len(current) > self.max_prompt_chars else current
        payload: dict[str, Any] = {
            "work_title": point.title or self.title_hint or "(未命名手记)",
            "current_version_number": point.version_number,
            "current_version_id": point.head_version_id,
            "observed_at": observed_at,
            "already_written_chars": len(current),
            "tail_of_current_text": tail,
            "previous_experiences": self._experience_view(),
            "instruction": (
                f"给这本手记加一小段，或把最后一段改写一下。新内容不超过 {self.max_new_chars} 字。"
                "如果这段手记确实不需要再动，就回 kind=no_change（text 留空）——"
                "不要为了有输出而硬加内容。"
            ),
        }
        if context:
            # v0.4 research / C: bounded *document* context the caller read from the owner.  Two
            # halves may arrive: ``research_context`` (B's research workspace read) and
            # ``practice_context`` (B's practice workspace read -- her own methods and reviews).
            # Neither is memory, neither is an admitted experience, and neither replaces the work's
            # own text, which still comes from the owner's head above.  A caller that passes one
            # bare mapping (the pre-v0.4 shape) keeps the old meaning exactly.
            research_context, practice_context = self._split_context(context)
            if research_context:
                payload["research_context"] = research_context
            if practice_context:
                payload["practice_context"] = practice_context
            earlier = _earlier_view(context)
            if earlier:
                # v0.4 work-revisit: a *version-pinned* look at what this same work said earlier.
                # It is not history browsing: at most MAX_EARLIER_ENTRIES entries, each a bounded
                # excerpt the caller read through the owner's ``read_version`` and already passed
                # through the source gate.  A blocked source means the caller sends nothing here.
                payload["earlier_versions"] = earlier
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _split_context(context: Any) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
        """Split the one ``context`` argument into its two documented halves.

        ``{"research_context": ..., "practice_context": ...}`` is the v0.4 C shape; anything else
        is the earlier bare research mapping, kept working byte for byte so a caller (or a test)
        that predates the practice workspace is not silently changed.
        """

        if not isinstance(context, Mapping):
            return None, None
        if "research_context" not in context and "practice_context" not in context:
            return dict(context), None
        research = context.get("research_context")
        practice = context.get("practice_context")
        return (dict(research) if isinstance(research, Mapping) else None,
                dict(practice) if isinstance(practice, Mapping) else None)


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

    @staticmethod
    def split_header(current: str) -> tuple[str, str]:
        """Split a leading HTML-comment block (a machine header) from the document's own text.

        The research marker lives on the first line of a research work, and the owner rewrites
        nothing: so the *writer* is what has to keep it.  This is a generic Markdown rule (a
        leading ``<!-- ... -->`` block is a header, not prose), not a copy of anybody's marker
        spelling -- A must never own a second definition of that marker.
        """

        text = str(current or "")
        stripped = text.lstrip("\n")
        if not stripped.startswith("<!--"):
            return "", text
        end = stripped.find("-->")
        if end < 0:
            return "", text
        header_end = end + len("-->")
        rest = stripped[header_end:]
        if rest and not rest.startswith("\n"):
            return "", text
        return stripped[:header_end], rest.lstrip("\n")

    def _merge(self, current: str, kind: str, text: str) -> str:
        # A header (the research marker, or any leading comment block) is never part of the body:
        # ``append`` writes below it and ``revise_tail`` rewrites only *prose*.  Keeping it out of
        # the merge is what makes "the marker is not at the end of the file" true by construction.
        header, body = self.split_header(current)
        prefix = f"{header}\n\n" if header else ""
        if kind == KIND_APPEND:
            sep = "" if body.endswith("\n\n") or not body else "\n\n"
            return f"{prefix}{body}{sep}{text}\n"
        # revise_tail: replace the final paragraph, keeping everything before it.
        blocks = re.split(r"\n\s*\n", body.rstrip("\n"))
        if not blocks or not blocks[0].strip():
            return f"{prefix}{text}\n"
        if len(blocks) <= 1:
            return f"{prefix}{text}\n"
        return prefix + "\n\n".join(blocks[:-1]) + f"\n\n{text}\n"

    # -- the writer protocol -----------------------------------------------------------

    def next_content(self, point: ArtifactResumePoint, *, observed_at: str,
                     context: Optional[Mapping[str, Any]] = None) -> str:
        current = point.content or ""
        self.last_trimmed = False
        self.last_self_note = None
        try:
            result = self.client.complete(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=self._user_prompt(point, observed_at=observed_at, context=context),
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
        self.last_kind = kind
        self.last_self_note = str(parsed.get("self_note") or "")[:200] or None
        if kind == KIND_NO_CHANGE:
            # A legal "nothing to write here".  It is never smuggled through as an edit of the
            # same bytes (that trick would mint a version that changes nothing), and it may not
            # carry text either -- a no_change with a paragraph in it is a contradiction.
            if text:
                raise ModelContentError("MODEL_NO_CHANGE_CARRIES_TEXT")
            self.no_change_count += 1
            raise ModelNoChange(str(self.last_self_note or "NO_CHANGE"))
        if not text:
            raise ModelContentError("MODEL_EDIT_EMPTY")
        if len(text) > self.max_new_chars:
            text = text[: self.max_new_chars].rstrip() + "…"
            self.last_trimmed = True

        merged = self._merge(current, kind, text)
        if merged == current:
            raise ModelContentError("MODEL_EDIT_CHANGED_NOTHING")
        return merged

    def describe(self) -> dict[str, Any]:
        return {"kind": "model_continuation_writer", "calls": self.call_count,
                "last_kind": self.last_kind, "last_trimmed": self.last_trimmed,
                "last_self_note": self.last_self_note,
                "no_change_count": self.no_change_count,
                "max_new_chars": self.max_new_chars}


__all__ = [
    "ALLOWED_KINDS",
    "KIND_APPEND",
    "KIND_NO_CHANGE",
    "KIND_REVISE_TAIL",
    "ModelContentError",
    "ModelContinuationWriter",
    "ModelNoChange",
    "SYSTEM_PROMPT",
]

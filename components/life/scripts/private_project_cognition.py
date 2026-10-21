"""v0.4 B: bounded, source-backed *proposals* for a private project -- never a creation.

Why this module exists
----------------------
The subject could only continue a work that already existed.  Producing a *new* project needs a
first step: something to make a project out of.  This module turns what she already has --
her own questions beside a material, and the material's own next page -- into at most
``MAX_PROPOSALS`` pure-structure proposals, either 0-LLM (``structural_proposals``) or through
one **bounded** model call (``model_proposals``).

The one rule that makes this safe
---------------------------------
A proposal is **not** a project.  Nothing in this module writes anything, holds a port, or calls
``private_project_service``: there is no import of the owner here at all, so "propose" can never
become "create" by accident.  Creating needs an explicit subject-side choice:
``select_proposal()`` picks one, and only then does the caller hand
``chosen_project_payload(...)`` to ``PrivateProjectService.create_project`` -- which itself
refuses anything but an explicit title and text.

What a proposal may and may not carry
-------------------------------------
* It carries *structure*: a title, a kind, a first step, the seed text (her own words where they
  exist) and up to ``MAX_SOURCE_REFS`` refs to the real sources it came from.
* It never carries a claim that the project exists, an artifact id, a revision, a head, an
  understanding score or text she did not write.  A model that answers outside the bounded shape
  is a failure (``ProjectProposalError``), not something to be trimmed into a plausible proposal.
"""
from __future__ import annotations

import json
import re
from hashlib import sha256
from typing import Any, Iterable, Mapping, Optional, Sequence

SCHEMA_VERSION = "chiyo.private_project_proposals.v1"

#: Where a proposal came from.  ``OPEN`` only ever comes out of a model call.
KIND_QUESTION = "QUESTION_FROM_MATERIAL"
KIND_MATERIAL_NEXT = "MATERIAL_NEXT_PAGE"
KIND_OPEN = "OPEN_DIRECTION"
STRUCTURAL_KINDS = (KIND_QUESTION, KIND_MATERIAL_NEXT)
ALL_KINDS = (KIND_QUESTION, KIND_MATERIAL_NEXT, KIND_OPEN)

ORIGIN_STRUCTURAL = "STRUCTURAL"
ORIGIN_MODEL = "MODEL"

MAX_PROPOSALS = 3
MAX_TITLE_CHARS = 120
MAX_SEED_TEXT_CHARS = 1200
MAX_FIRST_STEP_CHARS = 200
MAX_SOURCE_REFS = 3
MAX_SOURCE_TEXT_CHARS = 400
MAX_EXISTING_PROJECTS = 24
MAX_PROMPT_CHARS = 4000
MAX_MODEL_OUTPUT_TOKENS = 700
DEFAULT_TIMEOUT_S = 25.0

REASON_NO_MODEL = "PROJECT_PROPOSAL_MODEL_UNUSED"
REASON_MODEL_FAILED = "PROJECT_PROPOSAL_MODEL_FAILED"
REASON_NOT_SELECTED = "PROJECT_PROPOSAL_NOT_SELECTED"
REASON_UNKNOWN_PROPOSAL = "PROJECT_PROPOSAL_UNKNOWN_ID"

PROPOSAL_ID_PREFIX = "pproposal:"

_CONTROL = re.compile(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]")


class ProjectProposalError(RuntimeError):
    """The proposals could not be formed as bounded structure.  Never a fabricated proposal."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        super().__init__("%s:%s" % (reason_code, detail) if detail else reason_code)
        self.reason_code = str(reason_code)
        self.detail = str(detail)[:200]


# ---------------------------------------------------------------------------------------------
# small text helpers (bounded, no invented content)
# ---------------------------------------------------------------------------------------------


def _one_line(value: Any, limit: int) -> str:
    if value is None:
        return ""
    text = _CONTROL.sub(" ", str(value))
    text = " ".join(part.strip() for part in text.replace("\r", "\n").split("\n") if part.strip())
    return text[:limit].strip()


def _bounded_text(value: Any, limit: int) -> str:
    if value is None:
        return ""
    text = _CONTROL.sub("", str(value)).replace("\r\n", "\n").replace("\r", "\n")
    return text[:limit].strip()


def _proposal_id(*parts: Any) -> str:
    digest = sha256("\x1f".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:16]
    return PROPOSAL_ID_PREFIX + digest


def _title_key(value: Any) -> str:
    return _one_line(value, MAX_TITLE_CHARS).casefold()


# ---------------------------------------------------------------------------------------------
# the bounded seed: what she already has, spelled as structure
# ---------------------------------------------------------------------------------------------


def bounded_seed(
    *,
    materials: Optional[Mapping[str, Any]] = None,
    questions: Optional[Iterable[Mapping[str, Any]]] = None,
    existing_projects: Optional[Iterable[Mapping[str, Any]]] = None,
) -> dict[str, Any]:
    """One bounded, text-only view of the sources a proposal may be formed from.

    ``materials`` is the reading situation's own shape
    (``subject_private_materials.read_reading_situation``): state, title, pages and the page she
    is on.  ``questions`` are her own ``unclear`` marks / memos (``id`` + ``text``).  Both are
    trimmed to their bounds here, so whatever is handed to a model is the same size every time.
    """
    material = dict(materials or {})
    page = material.get("page") or material.get("next_page")
    material_view = {
        "state": _one_line(material.get("state"), 40),
        "title": _one_line(material.get("title"), MAX_TITLE_CHARS),
        "pages": material.get("pages") if isinstance(material.get("pages"), int) else None,
        "page": page if isinstance(page, int) and not isinstance(page, bool) else None,
        "source_id": _one_line(material.get("source_id"), 120),
        "source_revision": _one_line(material.get("source_revision"), 120),
    }
    question_rows: list[dict[str, Any]] = []
    for row in questions or ():
        if not isinstance(row, Mapping):
            continue
        text = _bounded_text(row.get("text"), MAX_SOURCE_TEXT_CHARS)
        if not text:
            continue
        question_rows.append({
            "ref": _one_line(row.get("id") or row.get("ref"), 120),
            "page": (row.get("page") if isinstance(row.get("page"), int)
                     and not isinstance(row.get("page"), bool) else None),
            "text": text,
        })
        if len(question_rows) >= MAX_PROPOSALS:
            break
    project_rows: list[dict[str, Any]] = []
    for row in existing_projects or ():
        if not isinstance(row, Mapping):
            continue
        title = _one_line(row.get("title"), MAX_TITLE_CHARS)
        if not title:
            continue
        project_rows.append({
            "artifact_id": _one_line(row.get("artifact_id") or row.get("project_ref"), 120),
            "title": title,
            "lifecycle": _one_line(row.get("lifecycle"), 20),
        })
        if len(project_rows) >= MAX_EXISTING_PROJECTS:
            break
    return {
        "schema_version": SCHEMA_VERSION,
        "material": material_view,
        "questions": question_rows,
        "existing_projects": project_rows,
        "bounds": {"max_proposals": MAX_PROPOSALS, "max_title_chars": MAX_TITLE_CHARS,
                   "max_seed_text_chars": MAX_SEED_TEXT_CHARS,
                   "max_source_refs": MAX_SOURCE_REFS},
    }


def _material_refs(seed: Mapping[str, Any]) -> list[str]:
    material = seed.get("material") if isinstance(seed.get("material"), Mapping) else {}
    refs: list[str] = []
    if material.get("source_id"):
        ref = str(material["source_id"])
        if material.get("source_revision"):
            ref = "%s@%s" % (ref, material["source_revision"])
        refs.append(ref)
    return refs


def _existing_titles(seed: Mapping[str, Any]) -> set[str]:
    rows = seed.get("existing_projects")
    if not isinstance(rows, Iterable):
        return set()
    return {_title_key(row.get("title")) for row in rows or () if isinstance(row, Mapping)}


# ---------------------------------------------------------------------------------------------
# 0-LLM proposals: pure structure derived from her own facts
# ---------------------------------------------------------------------------------------------


def structural_proposals(seed: Mapping[str, Any], *, limit: int = MAX_PROPOSALS
                         ) -> list[dict[str, Any]]:
    """At most ``limit`` proposals built only from the seed's own facts (no model, no invention).

    Two sources, both real: a question she wrote beside the material (her own words become the
    seed text), and the material's own next page (the seed text states that fact and where it
    comes from -- it never writes the page for her).  A proposal whose title already exists as a
    project is dropped rather than renamed: a duplicate is not a choice.
    """
    cap = max(0, min(int(limit), MAX_PROPOSALS))
    if cap == 0:
        return []
    existing = _existing_titles(seed)
    proposals: list[dict[str, Any]] = []
    material = seed.get("material") if isinstance(seed.get("material"), Mapping) else {}
    material_refs = _material_refs(seed)

    for row in seed.get("questions") or ():
        if not isinstance(row, Mapping):
            continue
        text = _bounded_text(row.get("text"), MAX_SEED_TEXT_CHARS)
        if not text:
            continue
        page = row.get("page")
        where = "（第 %d 页）" % page if isinstance(page, int) and not isinstance(page, bool) else ""
        title = _one_line("弄清：%s%s" % (_one_line(text, 60), where), MAX_TITLE_CHARS)
        if not title or _title_key(title) in existing:
            continue
        refs = list(material_refs)
        if row.get("ref"):
            refs.append(str(row["ref"])[:120])
        proposals.append(_proposal(
            kind=KIND_QUESTION, title=title, first_step=_one_line(text, MAX_FIRST_STEP_CHARS),
            seed_text=text, source_refs=refs, origin=ORIGIN_STRUCTURAL))
        if len(proposals) >= cap:
            return proposals

    page = material.get("page")
    state = _one_line(material.get("state"), 40)
    title_text = _one_line(material.get("title"), MAX_TITLE_CHARS)
    if isinstance(page, int) and not isinstance(page, bool) and title_text:
        title = _one_line("《%s》第 %d 页：写成一页" % (title_text, page), MAX_TITLE_CHARS)
        if _title_key(title) not in existing:
            seed_text = "第 %d 页还没读（材料：%s，状态 %s）。" % (
                page, title_text, state or "UNKNOWN")
            proposals.append(_proposal(
                kind=KIND_MATERIAL_NEXT, title=title,
                first_step="把第 %d 页读完，再写这一页" % page,
                seed_text=seed_text, source_refs=list(material_refs),
                origin=ORIGIN_STRUCTURAL))
    return proposals[:cap]


def _proposal(*, kind: str, title: str, first_step: str, seed_text: str,
              source_refs: Sequence[str], origin: str) -> dict[str, Any]:
    clean_title = _one_line(title, MAX_TITLE_CHARS)
    clean_seed = _bounded_text(seed_text, MAX_SEED_TEXT_CHARS)
    refs = [ref for ref in (_one_line(r, 120) for r in source_refs or ()) if ref][:MAX_SOURCE_REFS]
    return {
        "proposal_id": _proposal_id(kind, clean_title, clean_seed),
        "kind": kind if kind in ALL_KINDS else KIND_OPEN,
        "title": clean_title,
        "first_step": _one_line(first_step, MAX_FIRST_STEP_CHARS),
        "seed_text": clean_seed,
        "source_refs": refs,
        "origin": origin,
        # A proposal is a choice, not a project: the caller must select it explicitly.
        "requires_selection": True,
    }


# ---------------------------------------------------------------------------------------------
# the bounded model call
# ---------------------------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "你是一个私人项目提案器。只输出 JSON："
    '{"proposals":[{"kind":"QUESTION_FROM_MATERIAL|MATERIAL_NEXT_PAGE|OPEN_DIRECTION",'
    '"title":"...","first_step":"...","seed_text":"..."}]}。'
    "最多 3 条。title 不超过 60 字，first_step 与 seed_text 不超过 200 字。"
    "只能依据给你的材料与疑问；不要编造她没写过的内容，不要声称项目已经存在。"
    "没有值得提的东西就回 {\"proposals\":[]}。"
)


def _user_prompt(seed: Mapping[str, Any], *, limit: int) -> str:
    payload = {
        "material": seed.get("material") or {},
        "questions": seed.get("questions") or [],
        "existing_project_titles": [row.get("title") for row in
                                    (seed.get("existing_projects") or ())
                                    if isinstance(row, Mapping)][:MAX_EXISTING_PROJECTS],
        "max_proposals": max(0, min(int(limit), MAX_PROPOSALS)),
        "instruction": "给出最多 %d 条可选方向；每条都要能从上面的材料/疑问找到根据。"
                       % max(0, min(int(limit), MAX_PROPOSALS)),
    }
    encoded = json.dumps(payload, ensure_ascii=False)
    return encoded[:MAX_PROMPT_CHARS]


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
            raise ProjectProposalError(REASON_MODEL_FAILED, "MODEL_OUTPUT_NOT_JSON") from None
        try:
            parsed = json.loads(match.group(0))
        except ValueError as exc:
            raise ProjectProposalError(REASON_MODEL_FAILED,
                                       "MODEL_OUTPUT_NOT_JSON") from exc
    if not isinstance(parsed, dict):
        raise ProjectProposalError(REASON_MODEL_FAILED, "MODEL_OUTPUT_NOT_AN_OBJECT")
    return parsed


def model_proposals(client: Any, seed: Mapping[str, Any], *, limit: int = MAX_PROPOSALS,
                    timeout_s: float = DEFAULT_TIMEOUT_S,
                    max_output_tokens: int = MAX_MODEL_OUTPUT_TOKENS) -> list[dict[str, Any]]:
    """One bounded model call, turned into at most ``limit`` proposals (or an explicit failure).

    The model is given only the bounded seed and may only answer proposals.  Anything else -- a
    call that raises, an answer that is not JSON, a proposal without a title -- is
    ``ProjectProposalError``: a half-parsed proposal would be text she never wrote.
    """
    if client is None or not callable(getattr(client, "complete", None)):
        raise ProjectProposalError(REASON_NO_MODEL, "no model client")
    cap = max(0, min(int(limit), MAX_PROPOSALS))
    if cap == 0:
        return []
    try:
        result = client.complete(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=_user_prompt(seed, limit=cap),
            timeout_s=float(timeout_s),
            max_output_tokens=int(max_output_tokens),
        )
    except ProjectProposalError:
        raise
    except Exception as exc:
        raise ProjectProposalError(REASON_MODEL_FAILED,
                                   "%s: %s" % (type(exc).__name__, str(exc)[:120])) from exc
    raw = str(getattr(result, "text", "") or "")
    parsed = _extract_json(raw)
    rows = parsed.get("proposals")
    if rows is None:
        rows = []
    if not isinstance(rows, list):
        raise ProjectProposalError(REASON_MODEL_FAILED, "PROPOSALS_NOT_A_LIST")
    existing = _existing_titles(seed)
    proposals: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        title = _one_line(row.get("title"), MAX_TITLE_CHARS)
        seed_text = _bounded_text(row.get("seed_text"), MAX_SEED_TEXT_CHARS)
        if not title or not seed_text:
            # A proposal without a title or without any text is not usable as a project start.
            continue
        if _title_key(title) in existing:
            continue
        kind = _one_line(row.get("kind"), 40)
        proposals.append(_proposal(
            kind=kind, title=title, first_step=_one_line(row.get("first_step"),
                                                         MAX_FIRST_STEP_CHARS),
            seed_text=seed_text, source_refs=_material_refs(seed), origin=ORIGIN_MODEL))
        if len(proposals) >= cap:
            break
    return proposals


def form_proposals(
    seed: Mapping[str, Any],
    *,
    client: Any = None,
    limit: int = MAX_PROPOSALS,
    structural_fallback: bool = True,
) -> dict[str, Any]:
    """The one entry point a caller needs: proposals, where they came from, and nothing more.

    A model that fails does not silently degrade into structural proposals unless the caller asked
    for that; when it does degrade, ``model_error`` says why, so nobody reads a fallback as a
    model's answer.
    """
    cap = max(0, min(int(limit), MAX_PROPOSALS))
    out: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "origin": ORIGIN_STRUCTURAL,
        "proposals": [],
        "model_error": "",
        "requires_selection": True,
        "creates_nothing": True,
    }
    if client is not None:
        try:
            proposals = model_proposals(client, seed, limit=cap)
        except ProjectProposalError as exc:
            out["model_error"] = "%s:%s" % (exc.reason_code, exc.detail) if exc.detail \
                else exc.reason_code
            if not structural_fallback:
                raise
        else:
            out["origin"] = ORIGIN_MODEL
            out["proposals"] = proposals
            return out
    if cap and structural_fallback:
        out["proposals"] = structural_proposals(seed, limit=cap)
    return out


# ---------------------------------------------------------------------------------------------
# the explicit selection (the only path from a proposal to a create call)
# ---------------------------------------------------------------------------------------------


def select_proposal(proposals: Sequence[Mapping[str, Any]], proposal_id: str
                    ) -> dict[str, Any]:
    """Pick one proposal by its id.  This call *is* the explicit choice; there is no default.

    An unknown or empty id is refused: "the first one" would be this module choosing, and the
    choice has to be the subject's.
    """
    wanted = _one_line(proposal_id, 120)
    if not wanted:
        raise ProjectProposalError(REASON_NOT_SELECTED, "a proposal id is required")
    for row in proposals or ():
        if isinstance(row, Mapping) and str(row.get("proposal_id") or "") == wanted:
            return dict(row)
    raise ProjectProposalError(REASON_UNKNOWN_PROPOSAL, wanted)


def chosen_project_payload(proposal: Mapping[str, Any]) -> dict[str, Any]:
    """The explicit title/text pair to hand to ``create_project``.  Writes nothing.

    It refuses a proposal with no title or no seed text rather than filling the gap: an empty
    project would look finished later.
    """
    title = _one_line(proposal.get("title"), MAX_TITLE_CHARS)
    seed_text = _bounded_text(proposal.get("seed_text"), MAX_SEED_TEXT_CHARS)
    if not title:
        raise ProjectProposalError(REASON_NOT_SELECTED, "the chosen proposal has no title")
    if not seed_text:
        raise ProjectProposalError(REASON_NOT_SELECTED, "the chosen proposal has no seed text")
    return {
        "title": title,
        "content": seed_text,
        "proposal_id": _one_line(proposal.get("proposal_id"), 120),
        "kind": _one_line(proposal.get("kind"), 40) or KIND_OPEN,
        "source_refs": list(proposal.get("source_refs") or [])[:MAX_SOURCE_REFS],
    }


def describe() -> dict[str, Any]:
    """What this module is, for a caller that wants to report it."""
    return {
        "kind": "private_project_cognition",
        "schema_version": SCHEMA_VERSION,
        "max_proposals": MAX_PROPOSALS,
        "kinds": list(ALL_KINDS),
        "structural_kinds": list(STRUCTURAL_KINDS),
        "writes_nothing": True,
    }


__all__ = [
    "ALL_KINDS",
    "DEFAULT_TIMEOUT_S",
    "KIND_MATERIAL_NEXT",
    "KIND_OPEN",
    "KIND_QUESTION",
    "MAX_FIRST_STEP_CHARS",
    "MAX_MODEL_OUTPUT_TOKENS",
    "MAX_PROPOSALS",
    "MAX_SEED_TEXT_CHARS",
    "MAX_SOURCE_REFS",
    "MAX_TITLE_CHARS",
    "ORIGIN_MODEL",
    "ORIGIN_STRUCTURAL",
    "PROPOSAL_ID_PREFIX",
    "ProjectProposalError",
    "REASON_MODEL_FAILED",
    "REASON_NOT_SELECTED",
    "REASON_NO_MODEL",
    "REASON_UNKNOWN_PROPOSAL",
    "SCHEMA_VERSION",
    "STRUCTURAL_KINDS",
    "bounded_seed",
    "chosen_project_payload",
    "describe",
    "form_proposals",
    "model_proposals",
    "select_proposal",
    "structural_proposals",
]

#!/usr/bin/env python3
"""v0.4 stage A: bounded research *context* over B's research workspace service.

What this module is
-------------------
The one place the Life cycle talks to the research workspace
(``research_workspace_service.ResearchWorkspaceService``, owned by stage B).  It turns the
service's documented envelopes into the small, bounded views a *decision* prompt and a
*writing* prompt may carry:

* :meth:`ResearchSeam.materials` -- the material hits she can really read right now, with the
  owner's own ``artifact_id`` / ``source_revision`` / declared ``origin`` (never a made-up hit);
* :meth:`ResearchSeam.existing` -- the research works she already has, with the bounded head
  text, the question, the scope and the uncertainty that were actually committed;
* :meth:`ResearchSeam.writing_context` -- per work: its own parsed research facts (question,
  source refs, scope, uncertainty, source checks) **plus the real body text of each source at
  the version the research was built on**, plus the prior research results as *document*
  context;
* :meth:`ResearchSeam.decision_context` -- the same two halves, bounded, for the decision
  prompt;
* :meth:`ResearchSeam.create_research` -- the one write: it forwards to B's service, which asks
  the ManagedArtifact owner to create the work.  This module never creates anything itself.

What it is not
--------------
* **No second store, no second marker.**  The research marker is defined and parsed only by B
  (``build_research_content`` / ``parse_research_content``); this module calls those helpers
  when they exist and otherwise reports "no research facts" -- it never re-implements the
  marker or guesses at the shape.
* **Not a Memory, not an experience provider.**  Everything here is *document context* read
  from the owner through B's service.  It is never handed to the admitted memory provider and
  is never spelled as an experience.
* **Not a claim of currency.**  A source whose revision moved, whose artifact was archived or
  which cannot be read is reported as ``STALE`` / ``UNAVAILABLE``; the CURRENT word comes only
  from B's own ``source_checks``.  Nothing here upgrades a check.
* **Never fabricated on failure.**  A service that is missing, unreachable or refusing yields
  ``[]`` / ``None`` plus a named ``last_error`` -- an unavailable workspace can therefore never
  look like "she has no material" or "she has no research".
"""
from __future__ import annotations

import hashlib
from typing import Any, Iterable, Mapping, Optional, Sequence

#: The four status words a source check may carry (B's own vocabulary).  Spelled here only so a
#: *report* can compare against them; nothing is upgraded, only recognised.
CHECK_CURRENT = "CURRENT"
CHECK_STALE = "STALE"
CHECK_UNAVAILABLE = "UNAVAILABLE"

REASON_SERVICE_MISSING = "RESEARCH_SERVICE_NOT_WIRED"
REASON_SERVICE_UNUSABLE = "RESEARCH_SERVICE_UNUSABLE"
REASON_ANSWER_NOT_A_MAPPING = "RESEARCH_ANSWER_NOT_A_MAPPING"
REASON_ENVELOPE_NOT_OK = "RESEARCH_ENVELOPE_NOT_OK"
REASON_CREATE_REFUSED = "RESEARCH_CREATE_REFUSED"
REASON_NO_ARTIFACT = "RESEARCH_CREATE_CONFIRMED_NO_ARTIFACT"

#: The public API surface A relies on.  Checked once, so a wiring mistake is a named refusal.
REQUIRED_METHODS: tuple[str, ...] = ("list_materials", "read_material", "create_research",
                                     "list_research", "read_research")

#: The two pure helpers B maintains.  A never re-implements either.
PARSE_HELPER = "parse_research_content"
BUILD_HELPER = "build_research_content"


class ResearchSeamError(RuntimeError):
    """The research service refused, or answered outside its documented envelopes."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        super().__init__("%s:%s" % (reason_code, detail) if detail else reason_code)
        self.reason_code = str(reason_code)
        self.detail = str(detail)[:240]


def _one_line(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _bounded_text(value: Any, limit: int) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return text[:limit]


def _artifact_id(value: Any) -> str:
    text = str(value or "").strip()
    return text


def research_module() -> Any:
    """B's ``research_workspace_service`` module, or ``None`` when it is not in this tree.

    Imported lazily: a Life instance that never wires a research workspace must not fail to
    import because that module is absent, and A must not carry a copy of it.
    """

    try:
        import research_workspace_service as rws  # type: ignore
    except Exception:
        return None
    return rws


class ResearchSeam:
    """A bounded, read-mostly view of one research workspace service."""

    def __init__(
        self,
        service: Any = None,
        *,
        max_materials: int = 4,
        max_sources: int = 3,
        max_existing: int = 3,
        excerpt_chars: int = 240,
        source_text_chars: int = 600,
        max_question_chars: int = 200,
        max_scope_chars: int = 240,
        max_uncertainty_chars: int = 200,
    ) -> None:
        self.service = service
        self.max_materials = max(1, int(max_materials))
        self.max_sources = max(1, int(max_sources))
        self.max_existing = max(0, int(max_existing))
        self.excerpt_chars = max(0, int(excerpt_chars))
        self.source_text_chars = max(0, int(source_text_chars))
        self.max_question_chars = max(0, int(max_question_chars))
        self.max_scope_chars = max(0, int(max_scope_chars))
        self.max_uncertainty_chars = max(0, int(max_uncertainty_chars))
        #: Why the last read answered nothing.  ``""`` when the last call really succeeded.
        self.last_error: str = ""
        #: The last material hits / research rows read, for a report (refs and titles only).
        self.last_materials: list[dict[str, Any]] = []
        self.last_research: list[dict[str, Any]] = []

    # -- availability -------------------------------------------------------------------

    def available(self) -> bool:
        service = self.service
        if service is None:
            return False
        return all(callable(getattr(service, name, None)) for name in REQUIRED_METHODS)

    def availability(self) -> dict[str, Any]:
        if self.service is None:
            return {"status": "NOT_WIRED", "reason_code": REASON_SERVICE_MISSING}
        if not self.available():
            missing = [name for name in REQUIRED_METHODS
                       if not callable(getattr(self.service, name, None))]
            return {"status": "UNUSABLE", "reason_code": REASON_SERVICE_UNUSABLE,
                    "missing": missing}
        return {"status": "READY", "reason_code": "RESEARCH_SERVICE_OK",
                "service": type(self.service).__name__}

    def describe(self) -> dict[str, Any]:
        described = self.availability()
        described.update({
            "kind": "research_seam",
            "bounds": {"max_materials": self.max_materials, "max_sources": self.max_sources,
                       "max_existing": self.max_existing, "excerpt_chars": self.excerpt_chars,
                       "source_text_chars": self.source_text_chars},
            # A carries no marker definition of its own; it borrows B's two pure helpers.
            "parse_helper": callable(getattr(self._module(), PARSE_HELPER, None)),
            "build_helper": callable(getattr(self._module(), BUILD_HELPER, None)),
            "writes": ["create_research"],
            "memory": False,
        })
        return described

    def _module(self) -> Any:
        module = research_module()
        if module is not None:
            return module
        # A caller that already holds the module (a test, or an assembly that loaded it by path)
        # may lend it to the service; nothing is ever re-derived from the marker.
        return getattr(self.service, "research_module", None)

    # -- envelopes ----------------------------------------------------------------------

    @staticmethod
    def _envelope(answer: Any) -> dict[str, Any]:
        """One documented envelope -> its ``data`` mapping, or a named refusal."""

        if not isinstance(answer, Mapping):
            raise ResearchSeamError(REASON_ANSWER_NOT_A_MAPPING, type(answer).__name__)
        status = str(answer.get("status") or "")
        # The workspace's envelope carries ``ok`` *and* a status word; a successful write answers
        # ``status: "applied"`` (not ``"ok"``), so success is decided by ``ok`` first and only
        # falls back to the status word when the flag is absent.
        ok = answer.get("ok")
        if ok is False:
            raise ResearchSeamError(
                REASON_ENVELOPE_NOT_OK,
                str(answer.get("reason_code") or status or "RESEARCH_REFUSED")[:80])
        if ok is None and status and status != "ok":
            raise ResearchSeamError(
                REASON_ENVELOPE_NOT_OK,
                str(answer.get("reason_code") or status or "RESEARCH_REFUSED")[:80])
        data = answer.get("data")
        return dict(data) if isinstance(data, Mapping) else {}

    def _call(self, method: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Call one documented method and return its ``data`` (raises ``ResearchSeamError``)."""

        if not self.available():
            raise ResearchSeamError(REASON_SERVICE_MISSING,
                                    "no research workspace service is wired")
        function = getattr(self.service, method)
        try:
            answer = function(*args, **kwargs)
        except ResearchSeamError:
            raise
        except Exception as exc:
            raise ResearchSeamError(REASON_SERVICE_UNUSABLE,
                                    "%s: %s" % (type(exc).__name__, str(exc)[:160])) from exc
        return self._envelope(answer)

    # -- materials ----------------------------------------------------------------------

    def materials(self, *, query: str = "", limit: Optional[int] = None) -> list[dict[str, Any]]:
        """The material hits she can really read, bounded and provenance-carrying.

        An unavailable or failing service yields ``[]`` -- and ``last_error`` says why, so the
        caller can tell "no materials" from "could not ask".  Nothing is invented to fill a list.
        """

        self.last_error = ""
        cap = self.max_materials if limit is None else max(1, min(int(limit), self.max_materials))
        try:
            data = self._call("list_materials", query=str(query or ""), limit=cap)
        except ResearchSeamError as exc:
            self.last_error = "%s:%s" % (exc.reason_code, exc.detail) if exc.detail else exc.reason_code
            self.last_materials = []
            return []
        rows = data.get("materials")
        view: list[dict[str, Any]] = []
        for row in rows or ():
            if not isinstance(row, Mapping):
                continue
            artifact_id = _artifact_id(row.get("artifact_id"))
            if not artifact_id:
                continue
            view.append({
                "artifact_id": artifact_id,
                "title": _one_line(row.get("title"), 120),
                "source_revision": _one_line(row.get("source_revision"), 120),
                "source_kind": _one_line(row.get("source_kind"), 40),
                "origin": _one_line(row.get("origin"), 80),
                "excerpt": _bounded_text(row.get("excerpt"), self.excerpt_chars),
            })
            if len(view) >= cap:
                break
        self.last_materials = view
        return view

    def material_ids(self, *, query: str = "", limit: Optional[int] = None) -> list[str]:
        """The bare ids of the readable materials: they are reading material, never work."""

        ids: list[str] = []
        for row in self.materials(query=query, limit=limit):
            text = str(row.get("artifact_id") or "")
            while text.startswith("artifact:"):
                text = text[len("artifact:"):]
            if text and text not in ids:
                ids.append(text)
        return ids

    def read_material(self, artifact_id: str, *,
                      expected_revision: Optional[str] = None) -> Optional[dict[str, Any]]:
        """One material's real body text at the revision the research named, or ``None``.

        A revision that moved, an archived artifact and an unreadable document all answer the
        same way here: ``None`` plus ``last_error`` -- never the old text dressed as current.
        """

        self.last_error = ""
        target = _artifact_id(artifact_id)
        if not target:
            return None
        kwargs: dict[str, Any] = {}
        if expected_revision:
            kwargs["expected_revision"] = str(expected_revision)
        try:
            data = self._call("read_material", target, **kwargs)
        except ResearchSeamError as exc:
            self.last_error = "%s:%s" % (exc.reason_code, exc.detail) if exc.detail else exc.reason_code
            return None
        material = data.get("material")
        if not isinstance(material, Mapping):
            self.last_error = "RESEARCH_MATERIAL_NOT_IN_ANSWER"
            return None
        return {
            "artifact_id": _artifact_id(material.get("artifact_id")) or target,
            "title": _one_line(material.get("title"), 120),
            "source_revision": _one_line(material.get("source_revision"), 120),
            "source_kind": _one_line(material.get("source_kind"), 40),
            "origin": _one_line(material.get("origin"), 80),
            "availability": _one_line(material.get("availability"), 40),
            "text": _bounded_text(material.get("text"), self.source_text_chars),
        }

    # -- research works -----------------------------------------------------------------

    def existing(self, *, limit: Optional[int] = None,
                 exclude: Sequence[str] = ()) -> list[dict[str, Any]]:
        """The research works she already has, bounded, newest first.

        ``list_research`` gives the titles/heads; each head is then read through
        ``read_research`` for its question/scope/uncertainty and a bounded text excerpt.  A row
        that cannot be read is still reported (with an empty excerpt) -- its existence is a fact
        even when its text is not.
        """

        self.last_error = ""
        cap = self.max_existing if limit is None else max(0, min(int(limit), self.max_existing))
        skipped = {str(value) for value in (exclude or ()) if str(value)}
        if cap == 0:
            return []
        try:
            data = self._call("list_research", limit=cap)
        except ResearchSeamError as exc:
            self.last_error = "%s:%s" % (exc.reason_code, exc.detail) if exc.detail else exc.reason_code
            self.last_research = []
            return []
        rows = data.get("projects")
        view: list[dict[str, Any]] = []
        for row in rows or ():
            if not isinstance(row, Mapping):
                continue
            artifact_id = _artifact_id(row.get("artifact_id"))
            if not artifact_id or artifact_id in skipped:
                continue
            entry = self._read_research_view(artifact_id)
            if entry is None:
                entry = {
                    "artifact_id": artifact_id,
                    "title": _one_line(row.get("title"), 120),
                    "question": "",
                    "scope": "",
                    "uncertainty": "",
                    "head_version_id": _one_line(row.get("head_version_id"), 120),
                    "content_excerpt": "",
                    "source_checks": [],
                    "readable": False,
                }
            view.append(entry)
            if len(view) >= cap:
                break
        self.last_research = view
        return view

    def _read_research_view(self, artifact_id: str) -> Optional[dict[str, Any]]:
        try:
            data = self._call("read_research", artifact_id)
        except ResearchSeamError as exc:
            self.last_error = "%s:%s" % (exc.reason_code, exc.detail) if exc.detail else exc.reason_code
            return None
        project = data.get("project")
        if not isinstance(project, Mapping):
            self.last_error = "RESEARCH_PROJECT_NOT_IN_ANSWER"
            return None
        return self._project_view(project)

    def _project_view(self, project: Mapping[str, Any]) -> dict[str, Any]:
        facts = {}
        content = project.get("content") if isinstance(project.get("content"), str) else ""
        if content:
            facts = self.facts_of(content) or {}
        checks: list[dict[str, Any]] = []
        for row in project.get("source_checks") or ():
            if not isinstance(row, Mapping):
                continue
            checks.append({
                "artifact_id": _one_line(row.get("artifact_id"), 120),
                "source_revision": _one_line(row.get("source_revision"), 120),
                # B spells the word ``status``; A only recognises it, and never upgrades it.
                "check": _one_line(row.get("check") or row.get("status"), 40),
                "detail": _one_line(row.get("reason_code") or row.get("detail"), 120),
            })
            if len(checks) >= self.max_sources:
                break
        refs: list[dict[str, Any]] = []
        for row in project.get("source_refs") or facts.get("source_refs") or ():
            if not isinstance(row, Mapping):
                continue
            refs.append({"artifact_id": _one_line(row.get("artifact_id"), 120),
                         "source_revision": _one_line(row.get("source_revision"), 120)})
            if len(refs) >= self.max_sources:
                break
        return {
            "artifact_id": _artifact_id(project.get("artifact_id")),
            "title": _one_line(project.get("title"), 120),
            "question": _one_line(project.get("question") or facts.get("question"),
                                  self.max_question_chars),
            "scope": _one_line(project.get("scope") or facts.get("scope"), self.max_scope_chars),
            "uncertainty": _one_line(project.get("uncertainty") or facts.get("uncertainty"),
                                     self.max_uncertainty_chars),
            "source_refs": refs,
            "source_checks": checks,
            "head_version_id": _one_line(project.get("head_version_id"), 120),
            # The *text* a later decision/ordinary work may read: B's parsed body (the marker is
            # separated by B's own parser), bounded head+tail so the newest written part is
            # present.  Taking the head of the raw `content` would return the marker JSON and
            # nothing else -- the research would then look like a template with no finding.
            "content_excerpt": self._content_excerpt(facts.get("body"), content),
            "readable": True,
        }

    def _content_excerpt(self, body: Any, content: Any) -> str:
        """A bounded digest of the document's own text, newest part included.

        ``body`` is what B's parser says the document says (marker already removed).  When no
        parser is available the marker is stripped generically -- a leading ``<!-- … -->`` block is
        a header, never a finding -- so an excerpt can never be *only* the marker.  Over-long text
        keeps a small head **and** the tail, because the writer appends at the end: the latest
        tentative finding/method lives there.
        """

        text = body if isinstance(body, str) else ""
        if not text.strip():
            text = self._strip_header(content)
        text = text.strip()
        limit = self.excerpt_chars
        if limit <= 0 or not text:
            return ""
        if len(text) <= limit:
            return text
        head = max(0, limit // 3)
        # Reserve the join (a newline) and the ellipsis too, so the digest never exceeds the bound.
        tail = max(0, limit - head - 2)
        pieces = [text[:head]] if head else []
        if tail:
            pieces.append("…" + text[-tail:])
        return "\n".join(piece for piece in pieces if piece)

    @staticmethod
    def _strip_header(content: Any) -> str:
        """``content`` without a leading HTML-comment block (a generic Markdown rule)."""

        text = str(content or "").lstrip("\n")
        if not text.startswith("<!--"):
            return text
        end = text.find("-->")
        if end < 0:
            return text
        return text[end + len("-->"):].lstrip("\n")

    def facts_of(self, content: Any) -> Optional[dict[str, Any]]:
        """B's own parse of a research document, or ``None`` when nothing can say.

        This is deliberately the *only* way A learns what a research work is: the marker is B's
        contract, and a local copy of it here would be a second definition waiting to drift.
        """

        module = self._module()
        parser = getattr(module, PARSE_HELPER, None) if module is not None else None
        if not callable(parser):
            return None
        try:
            parsed = parser(content if isinstance(content, str) else str(content or ""))
        except Exception:
            return None
        return dict(parsed) if isinstance(parsed, Mapping) else None

    def is_research_content(self, content: Any) -> bool:
        return self.facts_of(content) is not None

    # -- the prompts --------------------------------------------------------------------

    def writing_context(self, artifact_id: str) -> Optional[dict[str, Any]]:
        """The bounded context one work's writing prompt may carry, or ``None``.

        Two halves, both real and both labelled as *document* context:

        * ``own_research`` -- present only when this very work is a research document; it carries
          the question, the scope, the uncertainty, the source refs **with their bodies at the
          versions the research named**, and B's own source checks (CURRENT / STALE /
          UNAVAILABLE, never upgraded here);
        * ``prior_research`` -- the other research works she already has, as a bounded digest of
          question/scope/uncertainty/method excerpt, so an ordinary work can refer to what she
          found out earlier without any of it becoming Memory.
        """

        if not self.available():
            return None
        target = _artifact_id(artifact_id)
        context: dict[str, Any] = {
            "kind": "research_document_context",
            "artifact_id": target,
            "note": ("以下是 owner 里真实存在的研究文档与来源；可以作为写作依据，"
                     "不是记忆、不是信念，也不要声称未列出的来源。"),
        }
        own = None
        if target:
            own = self._read_research_view(target)
        if own is not None:
            # B's own per-source check is the authority for the *word*: an archived source is
            # UNAVAILABLE and a moved one is STALE, exactly as the workspace reported it.  A body is
            # only fetched (and only attached) for a source the workspace still calls CURRENT.
            checks = {str(row.get("artifact_id") or ""): str(row.get("check") or "")
                      for row in own.get("source_checks") or ()}
            sources: list[dict[str, Any]] = []
            for ref in own.get("source_refs") or ():
                ref_id = str(ref.get("artifact_id") or "")
                ref_revision = str(ref.get("source_revision") or "")
                word = checks.get(ref_id, "")
                material = None
                if word in ("", CHECK_CURRENT):
                    material = self.read_material(ref_id,
                                                  expected_revision=ref_revision or None)
                if material is None:
                    # No body, and never the CURRENT word: the status comes from what the owner
                    # said (or, when it said nothing, from why the read failed).
                    stale = "REVISION_MISMATCH" in str(self.last_error or "")
                    sources.append({
                        "artifact_id": ref_id,
                        "source_revision": ref_revision,
                        "availability": (word or
                                         (CHECK_STALE if stale else CHECK_UNAVAILABLE)),
                        "text": "",
                        "detail": (self.last_error or word)[:120],
                    })
                else:
                    sources.append({
                        "artifact_id": material["artifact_id"],
                        "source_revision": material["source_revision"],
                        "title": material["title"],
                        "source_kind": material["source_kind"],
                        "origin": material["origin"],
                        "availability": material["availability"] or CHECK_CURRENT,
                        "text": material["text"],
                    })
                if len(sources) >= self.max_sources:
                    break
            own = dict(own)
            own["sources"] = sources
            context["own_research"] = own
        prior = self.existing(limit=min(2, self.max_existing), exclude=[target])
        if prior:
            context["prior_research"] = [
                {"artifact_id": row.get("artifact_id"), "question": row.get("question"),
                 "scope": row.get("scope"), "uncertainty": row.get("uncertainty"),
                 "excerpt": row.get("content_excerpt"),
                 "checks": [check.get("check") for check in row.get("source_checks") or []]}
                for row in prior]
        if "own_research" not in context and "prior_research" not in context:
            return None
        return context

    def decision_context(self) -> dict[str, Any]:
        """What the decision prompt may see: the material hits, and the research she has.

        Both halves are bounded and carry their own provenance.  A hit whose revision has moved
        is reported with its own ``source_revision`` (the one the hit was read at), so the prompt
        can never read an old revision as the current material.
        """

        materials = self.materials()
        research = self.existing(limit=self.max_existing)
        view: dict[str, Any] = {
            "status": self.availability()["status"],
            "material_hits": [{"artifact_id": row["artifact_id"], "title": row["title"],
                               "source_revision": row["source_revision"],
                               "origin": row["origin"], "excerpt": row["excerpt"]}
                              for row in materials],
            "research_results": [
                {"artifact_id": row.get("artifact_id"), "question": row.get("question"),
                 "scope": row.get("scope"), "uncertainty": row.get("uncertainty"),
                 "excerpt": row.get("content_excerpt"),
                 "source_checks": [c.get("check") for c in row.get("source_checks") or []]}
                for row in research],
        }
        if self.last_error:
            view["last_error"] = self.last_error[:160]
        return view

    # -- the one write ------------------------------------------------------------------

    def create_research(
        self,
        *,
        question: str,
        source_refs: Iterable[Mapping[str, Any]] = (),
        title: Optional[str] = None,
        scope: str = "",
        uncertainty: str = "",
        operation_key: Optional[str] = None,
    ) -> dict[str, Any]:
        """Ask B's service to create one research work, and report exactly what it confirmed.

        The refs handed over are the ones the option was built from -- real ``artifact_id`` +
        ``source_revision`` pairs read from ``list_materials`` -- so the document can only ever
        cite material that existed when the choice was made.  A refusal raises by name; nothing
        here invents an artifact id.
        """

        refs: list[dict[str, str]] = []
        for row in source_refs or ():
            if not isinstance(row, Mapping):
                continue
            artifact_id = _artifact_id(row.get("artifact_id"))
            if not artifact_id:
                continue
            refs.append({"artifact_id": artifact_id,
                         "source_revision": str(row.get("source_revision") or "")})
        if not operation_key:
            # The workspace keys its creation on this; a caller that cannot name the intended
            # effect must not create anything (a retry would otherwise mint a second work).
            raise ResearchSeamError(REASON_CREATE_REFUSED, "OPERATION_KEY_REQUIRED")
        payload: dict[str, Any] = {
            "question": _bounded_text(question, self.max_question_chars) or _bounded_text(
                question, 400),
            "source_refs": refs,
            "scope": _bounded_text(scope, self.max_scope_chars),
            "uncertainty": _bounded_text(uncertainty, self.max_uncertainty_chars),
        }
        if title:
            payload["title"] = _one_line(title, 120)
        if operation_key:
            payload["operation_key"] = str(operation_key)
        try:
            data = self._call("create_research", **payload)
        except ResearchSeamError as exc:
            raise ResearchSeamError(REASON_CREATE_REFUSED,
                                    "%s:%s" % (exc.reason_code, exc.detail)
                                    if exc.detail else exc.reason_code) from exc
        project = data.get("project")
        if not isinstance(project, Mapping):
            raise ResearchSeamError(REASON_CREATE_REFUSED, "RESEARCH_PROJECT_NOT_IN_ANSWER")
        artifact_id = _artifact_id(project.get("artifact_id"))
        if not artifact_id:
            raise ResearchSeamError(REASON_NO_ARTIFACT,
                                    "the research service confirmed no artifact id")
        view = self._project_view(project)
        view["status"] = "OK"
        # The created document's own text, verbatim: the caller reports what the owner confirmed
        # (the decision prompt never uses this key -- it takes only the bounded excerpt).
        view["content"] = project.get("content") if isinstance(project.get("content"), str) else ""
        view["operation_key"] = str(project.get("operation_key") or operation_key or "")
        return view

    # -- quiet state --------------------------------------------------------------------

    def fingerprint_parts(self) -> dict[str, str]:
        """What the quiet ledger may hash for research: ids, versions and check words only.

        Two rounds share these only when the very same materials are readable at the very same
        revisions and the very same research heads carry the very same source checks.  A
        re-imported material, an archived source, an unreadable document or a new research head
        is therefore a new situation -- and *nothing else* is: a mere clock tick moves none of
        these strings, so it can never wake the loop on its own.  No text enters here.
        """

        materials = self.materials()
        material_part = []
        for row in materials:
            digest = hashlib.sha256(
                str(row.get("excerpt") or "").encode("utf-8")).hexdigest()[:8]
            material_part.append("%s@%s@%s@%s" % (row.get("artifact_id"),
                                                  row.get("source_revision"),
                                                  row.get("origin"), digest))
        research_part = []
        if self.max_existing:
            for row in self.existing(limit=self.max_existing):
                checks = ",".join(
                    "%s=%s" % (c.get("artifact_id"), c.get("check"))
                    for c in row.get("source_checks") or ())
                research_part.append("%s@%s@%s@%s" % (
                    row.get("artifact_id"), row.get("head_version_id"),
                    "readable" if row.get("readable") else "unreadable", checks))
        return {"research_materials": ",".join(sorted(material_part)),
                "research_works": ",".join(sorted(research_part))}


__all__ = [
    "BUILD_HELPER",
    "CHECK_CURRENT",
    "CHECK_STALE",
    "CHECK_UNAVAILABLE",
    "PARSE_HELPER",
    "REASON_ANSWER_NOT_A_MAPPING",
    "REASON_CREATE_REFUSED",
    "REASON_ENVELOPE_NOT_OK",
    "REASON_NO_ARTIFACT",
    "REASON_SERVICE_MISSING",
    "REASON_SERVICE_UNUSABLE",
    "REQUIRED_METHODS",
    "ResearchSeam",
    "ResearchSeamError",
    "research_module",
]

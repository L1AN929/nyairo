"""v0.2 WP-02/WP-03: AG-0 candidate source over the subject's own ManagedArtifacts.

What this adds
--------------
AG-0 already has seven admitted ``source_kind`` values and eight admitted ``candidate_kind``
values (``candidate_sources_ag0.py:93`` / ``:143``); ``PERSONAL_OPPORTUNITY`` and
``CONSIDER_PERSONAL_OPPORTUNITY`` are among them, and the forbidden list at ``:106`` exists
precisely so nobody invents kinds like ``IDLE``/``WEAK_MOTIVATION``/``PRIVATE_PROJECT``.  So
this module adds **no new kind**.  It fills the admitted ``PERSONAL_OPPORTUNITY`` source with
the one personal opportunity that actually exists in v0.2: *a work of my own that I can
continue right now*, read from the ManagedArtifact owner through the narrow port.

Design rules kept
-----------------
* 0 LLM.  ``materialize`` is a pure read of the owner plus a deterministic projection.
* The source owns nothing.  It cannot start an Activity or propose an Action; the inherited
  base class raises ``CandidateAdoptionForbiddenError`` for all three writer verbs
  (``candidate_sources_ag0.py:1068``).
* No candidate is invented.  If the owner is unreachable, or holds no ACTIVE artifact with a
  committed head, ``discover`` returns nothing and ``availability_status`` says why.
* The candidate carries the *real* head version as its source revision, so a candidate is
  stale as soon as the work moves -- which is what makes "don't act on a stale read" work.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

_LIFE_SCRIPTS = str(Path(__file__).resolve().parent)
if _LIFE_SCRIPTS not in sys.path:
    sys.path.insert(0, _LIFE_SCRIPTS)

from candidate_sources_ag0 import (  # noqa: E402
    ADAPTER_STATUS_DISABLED,
    ADAPTER_STATUS_READY,
    CANDIDATE_KIND_CONSIDER_PERSONAL_OPPORTUNITY,
    CANDIDATE_STATUS_OPEN,
    CANDIDATE_STATUS_STALE,
    CANDIDATE_STATUS_WITHDRAWN,
    CandidateRecord,
    CandidateSourceAdapter,
    CandidateSourceError,
    DEFAULT_PER_SOURCE_CAP,
    GLOBAL_SUBJECT_ID,
    POLICY_VERSION_AG0_V1,
    SCHEMA_CANDIDATE_RECORD,
    SOURCE_PERSONAL_OPPORTUNITY,
    _require_iso,
    sha256_hex,
)
from life_artifact_port import (  # noqa: E402
    ArtifactOwnerRefused,
    ArtifactResumePoint,
    LifeArtifactPort,
    LifeArtifactUnavailable,
)

#: Availability as the candidate contract spells it (the value AG-1 reads).
AVAILABILITY_OPEN = CANDIDATE_STATUS_OPEN


class ArtifactContinuationSource(CandidateSourceAdapter):
    """Emits one candidate per continuable artifact of this subject.  Read-only, 0 LLM."""

    source_kind = SOURCE_PERSONAL_OPPORTUNITY
    canonical_owner = "chiyo-life-supply (ManagedArtifact owner)"

    def __init__(
        self,
        port: LifeArtifactPort,
        *,
        subject_id: str,
        per_source_cap: int = DEFAULT_PER_SOURCE_CAP,
        exclude_artifact_ids: Optional[Iterable[str]] = None,
        #: v0.4 research: artifacts that are *material* rather than work (the imported sources of a
        #: research workspace).  They are read as reference text, never as a work to continue or to
        #: wrap up, so a fresh read of the workspace decides which ids are excluded -- the caller
        #: cannot know them at construction time, and a stale static set would let one through.
        exclude_artifact_provider: Optional[Callable[[], Any]] = None,
        reading_artifact_id: Optional[str] = None,
        reading_state_provider: Optional[Callable[[], Mapping[str, Any]]] = None,
        paused_target_provider: Optional[Callable[[], Any]] = None,
        closeout_target_provider: Optional[Callable[[], Any]] = None,
        extra_opportunity_provider: Optional[Callable[[], Any]] = None,
    ) -> None:
        super().__init__(per_source_cap=per_source_cap)
        if not subject_id:
            raise CandidateSourceError("subject_id is required")
        self.port = port
        self.subject_id = str(subject_id)
        #: D (life-v1): artifacts that are *not* work (the reading material and the private-items
        #: document).  They are still offered through this admitted PERSONAL_OPPORTUNITY source
        #: only when the caller explicitly wants them; by default the source keeps offering
        #: exactly the continuable work it always did.
        self.exclude_artifact_ids = {str(a) for a in (exclude_artifact_ids or ()) if str(a)}
        self.exclude_artifact_provider = exclude_artifact_provider
        #: rc1-C: the *one* artifact whose opportunity depends on a private reading situation.
        #: Named explicitly by the caller (never inferred), so no other artifact is classified
        #: here and the generic "every ACTIVE artifact is continuable" rule stays untouched.
        self.reading_artifact_id = (str(reading_artifact_id).strip()
                                    if reading_artifact_id else None)
        #: The read-only situation of that material (``subject_private_materials``).  It answers
        #: "is there a page left, on which revision, and where did I really stop?" -- so an
        #: exhausted material stops being an *executable* personal opportunity instead of being
        #: offered again every round, and a progress change makes a stale candidate stale.
        #: With no provider the source behaves exactly as before.
        self.reading_state_provider = reading_state_provider
        #: v0.4 day-life: the artifacts that already have a PAUSED/WAITING Activity of their own.
        #: Such an object is *resume*'s business (LR-4 eligibility + the owner's own RESUME path),
        #: so offering it here as a fresh START would let a round create a second Activity for the
        #: same object and bypass that eligibility.  With no provider the source behaves as before.
        self.paused_target_provider = paused_target_provider
        #: v0.4 C: the artifacts that may be *wrapped up* this round, from W's closeout plan
        #: through the cycle (bound work + ACTIVE owner record + a free foreground slot).  With no
        #: provider -- or while ``artifact_closeout`` is not delivered -- the source emits exactly
        #: the candidates it always did.
        self.closeout_target_provider = closeout_target_provider
        #: v0.4 stage A: the *non-artifact* personal opportunities of this round -- her due "later"
        #: items and the project options her own material/questions support.  The provider is a
        #: read: it returns bounded mappings, each naming one ``fitem:``/``popt:`` ref and the
        #: target it may be carried out on.  No new source kind is invented (the forbidden list
        #: names ``PRIVATE_PROJECT`` precisely so nobody does that) and the source still owns
        #: nothing: the refs are reported, and only a Decision plus an owner verb can act.
        self.extra_opportunity_provider = extra_opportunity_provider
        #: What the last discover()/refresh() saw for the closeout seam, per artifact id.
        self.last_closeout_targets: set[str] = set()
        self.last_closeout_skip_reasons: dict[str, str] = {}
        #: What the last discover()/refresh() refused for that reason, per artifact id (report only).
        self.last_paused_skip_reasons: dict[str, str] = {}
        #: v0.4 research: artifacts the workspace calls material and this source therefore never
        #: offers as work, per artifact id (report only).
        self.last_material_skip_reasons: dict[str, str] = {}
        #: What the last discover()/refresh() saw for that material, per artifact id.
        self.last_reading_state: dict[str, dict[str, Any]] = {}
        self.last_reading_skip_reason: Optional[str] = None
        #: The extra (non-artifact) opportunities of the last read, keyed by their own ref, plus
        #: the reason the seam refused any of them.
        self.last_extra_items: dict[str, dict[str, Any]] = {}
        self.last_extra_skip_reasons: dict[str, str] = {}
        self._revision = 0
        self._last_snapshot_ref = "snap:PERSONAL_OPPORTUNITY:artifact:rev:0"
        #: Filled by the last discover(); the cycle reads it to plan a real step without a
        #: second owner round-trip.
        self.last_resume_points: dict[str, ArtifactResumePoint] = {}

    # -- availability ------------------------------------------------------------------

    def is_enabled(self) -> bool:
        return self.port.available()

    def availability_status(self) -> dict[str, Any]:
        try:
            points = self._read_points()
        except LifeArtifactUnavailable as exc:
            return {
                "source_kind": self.source_kind,
                "status": ADAPTER_STATUS_DISABLED,
                "canonical_owner": self.canonical_owner,
                "reason_code": "ARTIFACT_OWNER_UNREACHABLE",
                "detail": str(exc)[:200],
            }
        except ArtifactOwnerRefused as exc:
            return {
                "source_kind": self.source_kind,
                "status": ADAPTER_STATUS_DISABLED,
                "canonical_owner": self.canonical_owner,
                "reason_code": f"ARTIFACT_OWNER_{exc.status}",
                "detail": f"{exc.reason}:{exc.detail}"[:200],
            }
        return {
            "source_kind": self.source_kind,
            "status": ADAPTER_STATUS_READY,
            "canonical_owner": self.canonical_owner,
            "reason_code": "ARTIFACT_OWNER_OK",
            "continuable_count": len(points),
        }

    # -- discovery ---------------------------------------------------------------------

    def _reading_state(self) -> Optional[dict[str, Any]]:
        """The reading material's situation, read now -- or ``None`` when none is wired.

        A provider that raises is reported as an unreadable situation instead of being
        silently read as "there is a page": the consequence of not knowing is that this
        round does not offer an executable reading opportunity (fail closed), never that a
        page is invented.
        """
        provider = self.reading_state_provider
        if provider is None or not self.reading_artifact_id:
            return None
        try:
            state = provider()
        except Exception as exc:  # a failed read is reported, never guessed
            return {"state": "READING_STATE_READ_FAILED", "available": False, "exhausted": False,
                    "detail": f"{type(exc).__name__}: {exc}"[:200]}
        if not isinstance(state, Mapping):
            return {"state": "READING_STATE_NOT_A_MAPPING", "available": False,
                    "exhausted": False, "detail": ""}
        return dict(state)
    def _paused_targets(self) -> set[str]:
        """The bare ids of the artifacts that already have a PAUSED/WAITING Activity.

        A provider that raises is reported as an unreadable set, and the source then offers the
        artifact as before: failing *closed* here would silently drop every opportunity because an
        unrelated read broke, which is a different lie from the one this guard prevents.
        """

        provider = self.paused_target_provider
        if provider is None:
            return set()
        try:
            raw = provider()
        except Exception:  # a failed read is reported, never guessed
            return set()
        if isinstance(raw, Mapping):
            raw = list(raw)
        out: set[str] = set()
        for item in raw or []:
            text = str(item or "")
            while text.startswith("artifact:"):
                text = text[len("artifact:"):]
            if text:
                out.add(text)
        return out

    def _closeout_targets(self) -> set[str]:
        """The bare ids the closeout seam offers this round.

        A provider that raises, an unwired provider and a plan that is not delivered all answer
        the same thing: nothing.  Failing closed here is correct -- it can only *not* offer a
        wrap-up, never archive something.
        """

        provider = self.closeout_target_provider
        if provider is None:
            return set()
        try:
            raw = provider()
        except Exception:  # a failed read is reported, never guessed
            return set()
        if isinstance(raw, Mapping):
            raw = list(raw)
        out: set[str] = set()
        for item in raw or []:
            text = self._bare_id(item)
            if text:
                out.add(text)
        return out

    @staticmethod
    def _bare_id(value: Any) -> str:
        """The object identity behind an artifact id/ref (the owner's own prefix stripped)."""

        text = str(value or "")
        while text.startswith("artifact:"):
            text = text[len("artifact:"):]
        return text

    def _material_exclusions(self) -> set[str]:
        """Bare ids the research workspace calls *material* right now (never work).

        A provider that raises, an unwired provider and a workspace that answers nothing all mean
        the same thing here: no extra exclusion.  Failing closed in this direction can only leave
        the source behaving exactly as it did before this seam existed, and the alternative (a
        guess about which artifact is material) could hide a real work from her.
        """
        provider = self.exclude_artifact_provider
        if provider is None:
            return set()
        try:
            raw = provider()
        except Exception:
            return set()
        if isinstance(raw, Mapping):
            raw = list(raw)
        out: set[str] = set()
        for item in raw or ():
            text = self._bare_id(item)
            if text:
                out.add(text)
        return out

    # -- the extra (non-artifact) personal opportunities --------------------------------

    #: The ref namespaces this source may offer beyond ManagedArtifacts.  Both are things the
    #: *subject* proposed (a "later" item) or that her own material supports (a project option);
    #: neither is another owner's object and neither can be acted on from here.
    EXTRA_REF_PREFIXES = ("fitem:", "popt:")

    def _extra_raw_items(self, obs_iso: str) -> list[dict[str, Any]]:
        """Read the round's extra opportunities, bounded and normalised, or nothing.

        A provider that raises, an unwired provider and a malformed row all answer the same way:
        nothing is offered.  Failing closed here can only *not* offer an opportunity -- it can
        never create one, and it never hides an artifact opportunity.
        """
        self.last_extra_skip_reasons = {}
        provider = self.extra_opportunity_provider
        if provider is None:
            return []
        try:
            raw = provider()
        except Exception as exc:  # a failed read is reported, never guessed
            self.last_extra_skip_reasons[":provider"] = f"{type(exc).__name__}: {exc}"[:160]
            return []
        if isinstance(raw, Mapping):
            raw = list(raw.values())
        items: list[dict[str, Any]] = []
        for row in raw or []:
            if not isinstance(row, Mapping):
                continue
            ref = str(row.get("source_ref") or row.get("option_ref") or row.get("item_ref") or "")
            if not ref.startswith(self.EXTRA_REF_PREFIXES):
                self.last_extra_skip_reasons[ref or ":empty"] = "EXTRA_REF_NOT_A_PERSONAL_REF"
                continue
            targets = [str(t) for t in (row.get("target_refs") or []) if str(t)]
            if not targets:
                self.last_extra_skip_reasons[ref] = "EXTRA_TARGET_MISSING"
                continue
            # No memoisation here: ``discover`` and ``refresh`` both ask the provider, and a
            # candidate still on offer must stay on offer when it is refreshed in the same round.
            item = {
                "opportunity_id": f"opp:{ref}",
                "subject_id": self.subject_id,
                "source_ref": ref,
                "source_kind": self.source_kind,
                "availability": AVAILABILITY_OPEN,
                "visibility": "PRIVATE",
                "valid_from": obs_iso,
                "valid_until": None,
                "source_revision": max(1, int(row.get("revision") or 1)),
                "target_refs": targets,
                "detail": {
                    "title": str(row.get("title") or "")[:120],
                    "kind": str(row.get("kind") or ""),
                    "extra": str(row.get("kind") or "EXTRA"),
                    "due_at": row.get("due_at"),
                },
            }
            self.last_extra_items[ref] = item
            items.append(item)
        return items


    def _read_points(self) -> list[ArtifactResumePoint]:
        """Every ACTIVE artifact of this subject whose head is committed.

        Artifacts the caller named as *not work* (D: the private-items document) are skipped:
        a JSON private document must never be offered as a piece of writing to continue.

        rc1-C: the one artifact named as the reading material is offered only while its own
        read-only situation says there is something left to read on the **current** revision.
        A finished material, a revoked source and an unreadable private document are all
        normal outcomes -- none of them is turned into an executable personal opportunity,
        and none of them is repaired by guessing a page.
        """
        points: list[ArtifactResumePoint] = []
        self.last_reading_skip_reason = None
        self.last_paused_skip_reasons = {}
        paused = self._paused_targets()
        # v0.4 research: an imported material is reference text, not a work.  The workspace is read
        # once per pass (it is a bounded read), so a material imported a moment ago is already out.
        material_ids = self._material_exclusions()
        self.last_material_skip_reasons = {}
        for row in self.port.list_artifacts():
            if str(row.get("lifecycle")) != "ACTIVE":
                continue
            artifact_id = str(row.get("artifact_id") or "")
            if not artifact_id:
                continue
            if artifact_id in self.exclude_artifact_ids:
                continue
            if self._bare_id(artifact_id) in material_ids:
                # Reported, never silently dropped: "the workspace says this is material" is a
                # different fact from "this work has nothing left in it".
                self.last_material_skip_reasons[artifact_id] = "RESEARCH_MATERIAL_NOT_A_WORK"
                continue
            # v0.4 day-life: a PAUSED/WAITING Activity owns this object's way back (resume
            # eligibility + the owner's RESUME), so no fresh START is offered for it here.  Reported,
            # not silently dropped, and nothing about the paused stop point is touched.
            if self._bare_id(artifact_id) in paused:
                self.last_paused_skip_reasons[artifact_id] = "PAUSED_TARGET_REQUIRES_RESUME"
                continue
            if self.reading_artifact_id and artifact_id == self.reading_artifact_id:
                state = self._reading_state()
                if state is not None:
                    self.last_reading_state[artifact_id] = state
                    if not state.get("available"):
                        # Reported (``last_reading_skip_reason``), not offered: an exhausted
                        # material must stop producing an executable START every round.
                        self.last_reading_skip_reason = str(state.get("state") or "UNAVAILABLE")
                        continue
            point = self.port.resume_point(artifact_id)
            if point.resumable:
                points.append(point)
        return points

    def discover(self, *, observed_at: str) -> tuple[list[dict[str, Any]], str, dict[str, int]]:
        obs_iso = _require_iso(observed_at, "observed_at")
        self.last_resume_points = {}
        self.last_extra_items = {}
        #: v0.4 stage A: the non-artifact opportunities are read *before* the artifact owner is
        #: asked, so an unreachable Supply socket does not silently erase her own "later" items
        #: and project options from the candidate set.  Both halves fail closed on their own.
        extra_items = self._extra_raw_items(obs_iso)
        try:
            points = self._read_points()
        except (LifeArtifactUnavailable, ArtifactOwnerRefused):
            return extra_items, self._snapshot_ref(len(extra_items)), {
                "discovered": len(extra_items), "emitted": len(extra_items), "rejected": 0}

        items: list[dict[str, Any]] = []
        self.last_closeout_targets = self._closeout_targets()
        self.last_closeout_skip_reasons = {}
        for point in points[: self.per_source_cap]:
            self.last_resume_points[point.artifact_id] = point
            items.append(self._item_for(point, obs_iso))
            if self._bare_id(point.artifact_id) in self.last_closeout_targets:
                # v0.4 C: the same object, offered as *wrapping up* rather than continuing.  A
                # second item (not a second source and not a second owner) is what lets the
                # Decision choose between "keep working" and "finish this", which is exactly the
                # choice the design says must never be made for her by a fixed priority.
                items.append(self._closeout_item_for(point, obs_iso))
        items.extend(extra_items)
        return items, self._snapshot_ref(len(items)), {
            "discovered": len(points) + len(extra_items),
            "emitted": len(items),
            "rejected": 0,
        }

    def _closeout_item_for(self, point: ArtifactResumePoint, obs_iso: str) -> dict[str, Any]:
        """The wrap-up item for one already-read work (W's plan owns everything after this)."""

        bare = self._bare_id(point.artifact_id)
        artifact_ref = f"artifact:{point.artifact_id}"
        # W's namespace keeps the owner's id **verbatim** (a real id already begins with
        # ``artifact:``), so the target reads ``closeout:artifact:artifact:<uuid>``.  Building it
        # from the bare id here would be a different string, and the Decision's candidate would
        # then name a target the plan never adopts.
        closeout_ref = f"closeout:artifact:{point.artifact_id}"
        return {
            "opportunity_id": f"opp:closeout:{bare}",
            "subject_id": self.subject_id,
            "source_ref": artifact_ref,
            "source_kind": self.source_kind,
            "availability": AVAILABILITY_OPEN,
            "visibility": "PRIVATE",
            "valid_from": obs_iso,
            "valid_until": None,
            "source_revision": int(point.version_number or 1),
            "target_refs": [closeout_ref, artifact_ref],
            "detail": {
                "title": point.title,
                "head_version_id": point.head_version_id,
                "version_number": point.version_number,
                "closeout": True,
                "closeout_target": closeout_ref,
            },
        }

    def _item_for(self, point: ArtifactResumePoint, obs_iso: str) -> dict[str, Any]:
        """One raw item, in the shape the admitted PERSONAL_OPPORTUNITY source already uses.

        rc1-C: for the reading material -- and only for it -- the item also carries the
        bounded, text-free situation the source read (current revision, current stop page,
        next page, page count, note/question counts, ``needs_reread``).  It is report data:
        it adds no kind, changes no legal decision and holds no page text.
        """
        artifact_ref = f"artifact:{point.artifact_id}"
        version_ref = f"artifactversion:{point.head_version_id}"
        detail: dict[str, Any] = {
            "title": point.title,
            "head_version_id": point.head_version_id,
            "version_number": point.version_number,
            "content_chars": None if point.content is None else len(point.content),
        }
        state = self.last_reading_state.get(point.artifact_id)
        if state is not None and self.reading_artifact_id == point.artifact_id:
            material = state.get("material") if isinstance(state.get("material"), Mapping) else {}
            position = (state.get("position")
                        if isinstance(state.get("position"), Mapping) else {})
            annotations = (state.get("annotations")
                           if isinstance(state.get("annotations"), Mapping) else {})
            detail.update({
                "reading_state": state.get("state"),
                "reading_revision": material.get("version_id"),
                "reading_pages": material.get("pages"),
                "reading_page": position.get("page"),
                "reading_next_page": position.get("next_page"),
                "reading_needs_reread": bool(position.get("needs_reread")),
                "reading_exhausted": bool(state.get("exhausted")),
                "reading_notes": annotations.get("note"),
                "reading_unclear": annotations.get("unclear"),
            })
        return {
            "opportunity_id": f"opp:{point.artifact_id}",
            "subject_id": self.subject_id,
            "source_ref": artifact_ref,
            "source_kind": self.source_kind,
            "availability": AVAILABILITY_OPEN,
            "visibility": "PRIVATE",
            "valid_from": obs_iso,
            "valid_until": None,
            "source_revision": int(point.version_number or 1),
            "target_refs": [artifact_ref, version_ref],
            "detail": detail,
        }

    def _snapshot_ref(self, discovered: int = 0) -> str:
        self._revision += 1
        self._last_snapshot_ref = (
            f"snap:{self.source_kind}:artifact:subject:{self.subject_id}:rev:{self._revision}:n{discovered}"
        )
        return self._last_snapshot_ref

    # -- validation --------------------------------------------------------------------

    def validate_source(
        self, raw_item: Mapping[str, Any], *, observed_at: str
    ) -> tuple[bool, Optional[str]]:
        try:
            _require_iso(observed_at, "observed_at")
        except CandidateSourceError:
            return False, "OBSERVED_AT_INVALID"
        if not isinstance(raw_item, Mapping):
            return False, "ITEM_NOT_A_MAPPING"
        for name in ("opportunity_id", "subject_id", "source_ref", "source_kind",
                     "availability", "visibility", "valid_from"):
            if raw_item.get(name) in (None, ""):
                return False, f"MISSING_FIELD:{name}"
        if str(raw_item.get("subject_id")) != self.subject_id:
            return False, "SUBJECT_MISMATCH"
        if str(raw_item.get("source_kind")) != self.source_kind:
            return False, "SOURCE_KIND_MISMATCH"
        if str(raw_item.get("visibility")) not in {"PRIVATE", "PERSONAL"}:
            return False, "VISIBILITY_NOT_PERSONAL"
        if str(raw_item.get("availability")) != AVAILABILITY_OPEN:
            return False, f"NOT_AVAILABLE:{raw_item.get('availability')}"
        source_ref = str(raw_item.get("source_ref"))
        if not (source_ref.startswith("artifact:") or source_ref.startswith(self.EXTRA_REF_PREFIXES)):
            # v0.4 stage A: the two extra namespaces are admitted here for the same reason the
            # artifact one is -- they are *her own* personal opportunities.  Nothing else is.
            return False, "SOURCE_REF_NOT_A_PERSONAL_OPPORTUNITY"
        if not source_ref.startswith("artifact:") and not raw_item.get("target_refs"):
            return False, "EXTRA_TARGET_MISSING"
        try:
            _require_iso(str(raw_item.get("valid_from")), "valid_from")
        except CandidateSourceError:
            return False, "VALID_FROM_INVALID"
        if int(raw_item.get("source_revision") or 0) < 1:
            return False, "REVISION_INVALID"
        return True, None

    def _extra_candidate(self, item: Mapping[str, Any], *, obs_iso: str,
                         snap_ref: str) -> CandidateRecord:
        """One candidate for a due item / project option (0 LLM, no second source kind)."""

        ref = str(item["source_ref"])
        revision = int(item.get("source_revision") or 1)
        target_refs = [str(r) for r in (item.get("target_refs") or [])]
        cand_id = f"cand:{sha256_hex(f'{self.source_kind}:{ref}:v{revision}')[:16]}"
        sem_id = f"semcand:{sha256_hex(f'{GLOBAL_SUBJECT_ID}:{self.source_kind}:{ref}')[:16]}"
        return CandidateRecord(
            candidate_id=cand_id,
            subject_id=GLOBAL_SUBJECT_ID,
            candidate_kind=CANDIDATE_KIND_CONSIDER_PERSONAL_OPPORTUNITY,
            source_kind=self.source_kind,
            source_ref=ref,
            source_refs=[ref],
            source_revision=revision,
            target_refs=target_refs,
            availability_status=CANDIDATE_STATUS_OPEN,
            created_at=str(item["valid_from"]),
            observed_at=obs_iso,
            recorded_at=obs_iso,
            valid_from=str(item["valid_from"]),
            valid_until=None,
            causal_parent_refs=[str(item["opportunity_id"])],
            provenance_refs=[str(item["opportunity_id"]), ref, snap_ref],
            provenance_chain=[
                {"step": "candidate", "ref": cand_id,
                 "kind": CANDIDATE_KIND_CONSIDER_PERSONAL_OPPORTUNITY},
                {"step": "adapter", "ref": f"adapter:{self.source_kind}",
                 "owner": self.canonical_owner},
                {"step": "personal_opportunity", "ref": ref,
                 "revision": revision, "availability": item["availability"]},
            ],
            semantic_identity=sem_id,
            idempotency_key=f"idem_cand:extra:{ref}:v{revision}",
            policy_version=POLICY_VERSION_AG0_V1,
            schema_version=SCHEMA_CANDIDATE_RECORD,
        )

    # -- materialization ---------------------------------------------------------------

    def materialize(
        self, *, observed_at: str
    ) -> tuple[list[CandidateRecord], str, dict[str, int]]:
        obs_iso = _require_iso(observed_at, "observed_at")
        items, snap_ref, meta = self.discover(observed_at=obs_iso)
        results: list[CandidateRecord] = []
        for item in items:
            if str(item["source_ref"]).startswith(self.EXTRA_REF_PREFIXES):
                # v0.4 stage A: a due "later" item or a project option is a personal opportunity
                # in exactly the admitted sense -- and nothing more.  No artifact id is parsed out
                # of it, no owner is asked, and no step is implied by its existence.
                results.append(self._extra_candidate(item, obs_iso=obs_iso, snap_ref=snap_ref))
                continue
            artifact_id = str(item["source_ref"]).split(":", 1)[1]
            revision = int(item["source_revision"])
            target_refs = [str(r) for r in item.get("target_refs") or []]
            # v0.4 C: a wrap-up item is the *same object* as the continuation item, so its
            # identity has to be different in every place AG-0 dedupes on -- the candidate id, the
            # semantic identity and the idempotency key.  The continuation path's own spellings
            # are untouched, byte for byte.
            detail = item.get("detail") if isinstance(item.get("detail"), Mapping) else {}
            closeout_target = str((detail or {}).get("closeout_target") or "")
            if closeout_target:
                cand_id = f"cand:{sha256_hex(f'{self.source_kind}:closeout:{artifact_id}:v{revision}')[:16]}"
                sem_id = (f"semcand:{sha256_hex(f'{GLOBAL_SUBJECT_ID}:{self.source_kind}:closeout:{artifact_id}')[:16]}")
                idem_key = f"idem_cand:closeout:{artifact_id}:v{revision}"
            else:
                cand_id = f"cand:{sha256_hex(f'{self.source_kind}:{artifact_id}:v{revision}')[:16]}"
                sem_id = f"semcand:{sha256_hex(f'{GLOBAL_SUBJECT_ID}:{self.source_kind}:{artifact_id}')[:16]}"
                idem_key = f"idem_cand:artifact:{artifact_id}:v{revision}"
            results.append(CandidateRecord(
                candidate_id=cand_id,
                subject_id=GLOBAL_SUBJECT_ID,
                candidate_kind=CANDIDATE_KIND_CONSIDER_PERSONAL_OPPORTUNITY,
                source_kind=self.source_kind,
                source_ref=str(item["source_ref"]),
                source_refs=[str(item["source_ref"])],
                source_revision=revision,
                target_refs=target_refs,
                availability_status=CANDIDATE_STATUS_OPEN,
                created_at=str(item["valid_from"]),
                observed_at=obs_iso,
                recorded_at=obs_iso,
                valid_from=str(item["valid_from"]),
                valid_until=None,
                causal_parent_refs=[str(item["opportunity_id"])],
                provenance_refs=[str(item["opportunity_id"]), str(item["source_ref"]), snap_ref],
                provenance_chain=[
                    {"step": "candidate", "ref": cand_id,
                     "kind": CANDIDATE_KIND_CONSIDER_PERSONAL_OPPORTUNITY},
                    {"step": "adapter", "ref": f"adapter:{self.source_kind}",
                     "owner": self.canonical_owner},
                    {"step": "canonical_source", "ref": str(item["source_ref"]),
                     "revision": revision, "availability": item["availability"]},
                ],
                semantic_identity=sem_id,
                idempotency_key=idem_key,
                policy_version=POLICY_VERSION_AG0_V1,
                schema_version=SCHEMA_CANDIDATE_RECORD,
            ))
        return results, snap_ref, meta

    @staticmethod
    def _reading_progress_changed(previous: Mapping[str, Any],
                                  current: Mapping[str, Any]) -> bool:
        """Did the *private progress* move between two situation reads?

        Only facts that describe what was offered are compared: which revision the offer was
        about, which page was the stop, and how far the private-items document had advanced.
        Nothing here is a page number for the next step -- that is the cycle's own read.
        """
        def pick(state: Mapping[str, Any]) -> tuple[Any, Any, Any, Any]:
            material = state.get("material") if isinstance(state.get("material"), Mapping) else {}
            position = (state.get("position")
                        if isinstance(state.get("position"), Mapping) else {})
            private = (state.get("private_items")
                       if isinstance(state.get("private_items"), Mapping) else {})
            return (material.get("version_id"), position.get("page"),
                    position.get("next_page"), private.get("owner_version_number"))

        return pick(previous) != pick(current)

    # -- refresh -----------------------------------------------------------------------

    def refresh(self, candidate: CandidateRecord, *, observed_at: str) -> CandidateRecord:
        obs_iso = _require_iso(observed_at, "observed_at")
        if str(candidate.source_ref or "").startswith(self.EXTRA_REF_PREFIXES):
            # v0.4 stage A: an extra opportunity is alive only while the round's own read still
            # offers it (the item still OPEN and due, or the option still supported).  Re-reading
            # is what makes it withdraw; nothing here remembers a withdrawn item.
            offered = self._extra_raw_items(obs_iso)
            fresh = {str(item["source_ref"]): item for item in offered}.get(
                str(candidate.source_ref))
            if fresh is None:
                return self.invalidate(candidate, reason_code="EXTRA_OPPORTUNITY_NOT_OFFERED",
                                       observed_at=obs_iso,
                                       new_status=CANDIDATE_STATUS_WITHDRAWN)
            if int(fresh.get("source_revision") or 1) != int(candidate.source_revision or 1):
                return self.invalidate(candidate, reason_code="EXTRA_OPPORTUNITY_REVISED",
                                       observed_at=obs_iso,
                                       new_status=CANDIDATE_STATUS_STALE)
            refreshed = CandidateRecord(**{**candidate.__dict__})
            refreshed.availability_status = CANDIDATE_STATUS_OPEN
            refreshed.invalidation_reason = None
            refreshed.recorded_at = obs_iso
            refreshed.revision = int(candidate.revision) + 1
            return refreshed
        artifact_id = str(candidate.source_ref).split(":", 1)[1] if candidate.source_ref else ""
        if not artifact_id:
            return self.invalidate(candidate, reason_code="SOURCE_REF_UNREADABLE",
                                   observed_at=obs_iso, new_status=CANDIDATE_STATUS_STALE)
        if self._bare_id(artifact_id) in self._material_exclusions():
            # v0.4 research: the workspace now calls this artifact material.  A candidate built
            # before that is withdrawn -- material is reference text, and continuing it as a work
            # would be her writing into her own source.
            return self.invalidate(candidate, reason_code="RESEARCH_MATERIAL_NOT_A_WORK",
                                   observed_at=obs_iso,
                                   new_status=CANDIDATE_STATUS_WITHDRAWN)
        try:
            point = self.port.resume_point(artifact_id)
        except LifeArtifactUnavailable:
            return self.invalidate(candidate, reason_code="ARTIFACT_OWNER_UNREACHABLE",
                                   observed_at=obs_iso, new_status=CANDIDATE_STATUS_STALE)
        except ArtifactOwnerRefused as exc:
            status = (CANDIDATE_STATUS_WITHDRAWN if exc.status == "NOT_FOUND"
                      else CANDIDATE_STATUS_STALE)
            return self.invalidate(candidate, reason_code=f"ARTIFACT_OWNER_{exc.status}",
                                   observed_at=obs_iso, new_status=status)
        if not point.resumable:
            return self.invalidate(candidate, reason_code="ARTIFACT_NOT_CONTINUABLE",
                                   observed_at=obs_iso, new_status=CANDIDATE_STATUS_WITHDRAWN)
        if any(str(ref).startswith("closeout:") for ref in (candidate.target_refs or [])):
            # v0.4 C: a wrap-up candidate is bound to *one* offer (the plan's target and the head
            # it was planned on).  It disappears as soon as the plan stops offering it, and it is
            # stale as soon as the head moves -- a wrap-up planned on an older revision is not
            # silently reused against a newer one.
            bare = self._bare_id(artifact_id)
            if bare not in self._closeout_targets():
                self.last_closeout_skip_reasons[artifact_id] = "CLOSEOUT_NOT_OFFERED"
                return self.invalidate(candidate, reason_code="CLOSEOUT_NOT_OFFERED",
                                       observed_at=obs_iso, new_status=CANDIDATE_STATUS_WITHDRAWN)
            if int(point.version_number or 0) != int(candidate.source_revision):
                return self.invalidate(candidate, reason_code="ARTIFACT_HEAD_ADVANCED",
                                       observed_at=obs_iso, new_status=CANDIDATE_STATUS_STALE)
            refreshed_closeout = CandidateRecord(**{**candidate.__dict__})
            refreshed_closeout.availability_status = CANDIDATE_STATUS_OPEN
            refreshed_closeout.invalidation_reason = None
            refreshed_closeout.recorded_at = obs_iso
            refreshed_closeout.revision = int(candidate.revision) + 1
            return refreshed_closeout
        if self._bare_id(artifact_id) in self._paused_targets():
            # v0.4 day-life: the object now has a PAUSED/WAITING Activity of its own, so its way back
            # is resume (LR-4 eligibility + the owner's RESUME), not a new START from this source.
            self.last_paused_skip_reasons[artifact_id] = "PAUSED_TARGET_REQUIRES_RESUME"
            return self.invalidate(candidate, reason_code="PAUSED_TARGET_REQUIRES_RESUME",
                                   observed_at=obs_iso, new_status=CANDIDATE_STATUS_WITHDRAWN)
        if int(point.version_number or 0) != int(candidate.source_revision):
            # The work moved since this candidate was built: it is stale, not wrong.
            return self.invalidate(candidate, reason_code="ARTIFACT_HEAD_ADVANCED",
                                   observed_at=obs_iso, new_status=CANDIDATE_STATUS_STALE)
        if self.reading_artifact_id and artifact_id == self.reading_artifact_id:
            # rc1-C: for the reading material the *progress*, not only the material, decides
            # whether this candidate still describes the offer that was made.  A private-items
            # write (a new stop point, a new page read) or a material that is now finished
            # makes the old candidate stale/withdrawn instead of quietly reusable.
            state = self._reading_state()
            if state is not None:
                previous = self.last_reading_state.get(artifact_id) or {}
                self.last_reading_state[artifact_id] = state
                if not state.get("available"):
                    self.last_reading_skip_reason = str(state.get("state") or "UNAVAILABLE")
                    return self.invalidate(
                        candidate, reason_code="READING_STATE_%s" % str(state.get("state") or ""),
                        observed_at=obs_iso, new_status=CANDIDATE_STATUS_WITHDRAWN)
                if self._reading_progress_changed(previous, state):
                    return self.invalidate(candidate, reason_code="READING_PROGRESS_ADVANCED",
                                           observed_at=obs_iso,
                                           new_status=CANDIDATE_STATUS_STALE)
        refreshed = CandidateRecord(**{**candidate.__dict__})
        refreshed.availability_status = CANDIDATE_STATUS_OPEN
        refreshed.invalidation_reason = None
        refreshed.recorded_at = obs_iso
        refreshed.revision = int(candidate.revision) + 1
        return refreshed


__all__ = ["ArtifactContinuationSource", "AVAILABILITY_OPEN"]

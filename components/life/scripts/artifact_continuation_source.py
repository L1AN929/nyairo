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
from typing import Any, Mapping, Optional

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
    ) -> None:
        super().__init__(per_source_cap=per_source_cap)
        if not subject_id:
            raise CandidateSourceError("subject_id is required")
        self.port = port
        self.subject_id = str(subject_id)
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

    def _read_points(self) -> list[ArtifactResumePoint]:
        """Every ACTIVE artifact of this subject whose head is committed."""
        points: list[ArtifactResumePoint] = []
        for row in self.port.list_artifacts():
            if str(row.get("lifecycle")) != "ACTIVE":
                continue
            artifact_id = str(row.get("artifact_id") or "")
            if not artifact_id:
                continue
            point = self.port.resume_point(artifact_id)
            if point.resumable:
                points.append(point)
        return points

    def discover(self, *, observed_at: str) -> tuple[list[dict[str, Any]], str, dict[str, int]]:
        obs_iso = _require_iso(observed_at, "observed_at")
        self.last_resume_points = {}
        try:
            points = self._read_points()
        except (LifeArtifactUnavailable, ArtifactOwnerRefused):
            return [], self._snapshot_ref(0), {"discovered": 0, "emitted": 0, "rejected": 0}

        items: list[dict[str, Any]] = []
        for point in points[: self.per_source_cap]:
            self.last_resume_points[point.artifact_id] = point
            items.append(self._item_for(point, obs_iso))
        return items, self._snapshot_ref(len(items)), {
            "discovered": len(points),
            "emitted": len(items),
            "rejected": 0,
        }

    def _item_for(self, point: ArtifactResumePoint, obs_iso: str) -> dict[str, Any]:
        """One raw item, in the shape the admitted PERSONAL_OPPORTUNITY source already uses."""
        artifact_ref = f"artifact:{point.artifact_id}"
        version_ref = f"artifactversion:{point.head_version_id}"
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
            "detail": {
                "title": point.title,
                "head_version_id": point.head_version_id,
                "version_number": point.version_number,
                "content_chars": None if point.content is None else len(point.content),
            },
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
        if not str(raw_item.get("source_ref")).startswith("artifact:"):
            return False, "SOURCE_REF_NOT_AN_ARTIFACT"
        try:
            _require_iso(str(raw_item.get("valid_from")), "valid_from")
        except CandidateSourceError:
            return False, "VALID_FROM_INVALID"
        if int(raw_item.get("source_revision") or 0) < 1:
            return False, "REVISION_INVALID"
        return True, None

    # -- materialization ---------------------------------------------------------------

    def materialize(
        self, *, observed_at: str
    ) -> tuple[list[CandidateRecord], str, dict[str, int]]:
        obs_iso = _require_iso(observed_at, "observed_at")
        items, snap_ref, meta = self.discover(observed_at=obs_iso)
        results: list[CandidateRecord] = []
        for item in items:
            artifact_id = str(item["source_ref"]).split(":", 1)[1]
            revision = int(item["source_revision"])
            target_refs = [str(r) for r in item.get("target_refs") or []]
            cand_id = f"cand:{sha256_hex(f'{self.source_kind}:{artifact_id}:v{revision}')[:16]}"
            sem_id = f"semcand:{sha256_hex(f'{GLOBAL_SUBJECT_ID}:{self.source_kind}:{artifact_id}')[:16]}"
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
                idempotency_key=f"idem_cand:artifact:{artifact_id}:v{revision}",
                policy_version=POLICY_VERSION_AG0_V1,
                schema_version=SCHEMA_CANDIDATE_RECORD,
            ))
        return results, snap_ref, meta

    # -- refresh -----------------------------------------------------------------------

    def refresh(self, candidate: CandidateRecord, *, observed_at: str) -> CandidateRecord:
        obs_iso = _require_iso(observed_at, "observed_at")
        artifact_id = str(candidate.source_ref).split(":", 1)[1] if candidate.source_ref else ""
        if not artifact_id:
            return self.invalidate(candidate, reason_code="SOURCE_REF_UNREADABLE",
                                   observed_at=obs_iso, new_status=CANDIDATE_STATUS_STALE)
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
        if int(point.version_number or 0) != int(candidate.source_revision):
            # The work moved since this candidate was built: it is stale, not wrong.
            return self.invalidate(candidate, reason_code="ARTIFACT_HEAD_ADVANCED",
                                   observed_at=obs_iso, new_status=CANDIDATE_STATUS_STALE)
        refreshed = CandidateRecord(**{**candidate.__dict__})
        refreshed.availability_status = CANDIDATE_STATUS_OPEN
        refreshed.invalidation_reason = None
        refreshed.recorded_at = obs_iso
        refreshed.revision = int(candidate.revision) + 1
        return refreshed


__all__ = ["ArtifactContinuationSource", "AVAILABILITY_OPEN"]

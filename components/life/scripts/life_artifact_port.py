"""v0.2 WP-03: the one narrow ManagedArtifact port the Life subject may call.

Why this module exists (WP-00 evidence)
---------------------------------------
``components/supply`` exposes **no** write port callable by the Life subject:
``grep -r "WritePort|write_port|artifact_write|ManagedArtifactPort" components/supply``
returns zero hits, and ``lifesupply/ports/`` only holds ``governance_client.py``, which is
imported by the legacy three-database server alone.  The closest existing Life-side writer is
``chiyo_bundle/presence.py:103 save_note``, and it is hard-wired to ``op="create_artifact"``
with no ``commit_markdown`` path -- so Life could create a document exactly once and never
continue it.  WP-03 needs continuation, so exactly one narrow port is added here.

What this module is, and is not
-------------------------------
* It is a **client of the existing Life Supply owner**, speaking the existing one-line JSON
  protocol over the existing Unix socket (``lifesupply/service/life_supply.py:580`` dispatch,
  ``:223`` READ_OPS, ``:230`` COMMAND_OPS).  It adds no store, no table and no second owner.
* It never opens ``life_supply.sqlite``, never imports the supply package and never takes the
  writer identity.  The service derives the caller identity from ``SO_PEERCRED``
  (``life_supply.py:381``/``:538``); a JSON field can never claim one
  (``validate_request`` refuses identity fields at ``:348``).
* It fails closed.  An unreachable socket, a malformed reply or an untyped reply raises
  ``LifeArtifactUnavailable``.  No artifact, version or receipt is ever invented.
* It keeps ``UNKNOWN`` distinct from success and from failure.  A command whose result cannot
  be established comes back as ``EffectState.UNKNOWN`` and must be resolved through
  ``resolve_operation`` **before** anything is retried -- the retry reuses the same
  ``operation_key``, so a second version cannot be minted for one intended edit.

The write pair (``create_artifact`` + ``commit_markdown``) is the narrow write port the order
authorises.  Both are gated by the service's own ``ARTIFACT_EXTERNAL_ACTION`` switch
(``switches.py:38``) and by a Governance grant reservation (``life_supply.py:655``); this
module can neither bypass nor widen either.
"""
from __future__ import annotations

import json
import socket
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Mapping, Optional, Sequence

#: Read operations this port may send.  Mirrors ``life_supply.READ_OPS``.
READ_OPS = frozenset({
    "list_artifacts", "read_artifact", "read_head", "read_effect_receipt", "lookup_operation",
})
#: Command operations this port may send.  Mirrors the artifact half of ``life_supply.COMMAND_OPS``.
COMMAND_OPS = frozenset({"create_artifact", "commit_markdown"})

DEFAULT_TIMEOUT = 5.0
MAX_RESPONSE_BYTES = 1_000_000

#: Service status strings, as the owner actually returns them.
STATUS_OK = "OK"
STATUS_NO_CHANGE = "NO_CHANGE"
STATUS_BLOCKED = "BLOCKED"
STATUS_DENY = "DENY"
STATUS_NOT_FOUND = "NOT_FOUND"
STATUS_UNKNOWN = "UNKNOWN"
STATUS_KNOWN = "KNOWN"


class LifeArtifactError(RuntimeError):
    """Base class for this port's refusals."""


class LifeArtifactUnavailable(LifeArtifactError):
    """The Life Supply owner could not be reached, or answered unusably."""


class ArtifactOwnerRefused(LifeArtifactError):
    """The owner answered with a typed refusal.  Carries the owner's own reason code."""

    def __init__(self, status: str, reason: str, detail: str = "", **extra: Any) -> None:
        super().__init__(f"{status}:{reason}:{detail}"[:400])
        self.status = status
        self.reason = reason
        self.detail = detail
        self.extra = dict(extra)


class EffectState:
    """What actually happened to the owner's state.  Never inferred from the request."""

    APPLIED = "APPLIED"
    NO_CHANGE = "NO_CHANGE"
    REFUSED = "REFUSED"
    NOT_FOUND = "NOT_FOUND"
    #: The command may or may not have landed.  Must be resolved before any retry.
    UNKNOWN = "UNKNOWN"


@dataclass
class ArtifactOutcome:
    """The owner's answer to one artifact command or read."""

    effect_state: str
    status: str
    reason: str = ""
    detail: str = ""
    artifact_id: Optional[str] = None
    version_id: Optional[str] = None
    version_number: Optional[int] = None
    head_version_id: Optional[str] = None
    parent_version_id: Optional[str] = None
    version_created: Optional[bool] = None
    operation_key: Optional[str] = None
    coordinated: Optional[bool] = None
    reservation_id: Optional[str] = None
    uow_id: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def applied(self) -> bool:
        return self.effect_state == EffectState.APPLIED

    @property
    def unknown(self) -> bool:
        return self.effect_state == EffectState.UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        out = dict(self.raw)
        out["effect_state"] = self.effect_state
        return out


@dataclass
class ArtifactResumePoint:
    """Where a continued work actually stands.  Built from the owner's head, not from memory."""

    artifact_id: str
    title: str
    head_version_id: Optional[str]
    version_number: Optional[int]
    content: Optional[str]
    lifecycle: str
    revision: int

    @property
    def resumable(self) -> bool:
        return (
            self.lifecycle == "ACTIVE"
            and self.head_version_id is not None
            and self.content is not None
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "title": self.title,
            "head_version_id": self.head_version_id,
            "version_number": self.version_number,
            "content_chars": None if self.content is None else len(self.content),
            "lifecycle": self.lifecycle,
            "revision": self.revision,
            "resumable": self.resumable,
        }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def operation_key_for(*parts: str) -> str:
    """A deterministic operation key for one intended effect.

    The owner keys idempotency on ``operation_key`` (``artifact_operations`` is unique on it), so
    the key must be derived from *what is intended*, never from the attempt.  That is what makes
    "resolve the UNKNOWN, then retry with the same key" safe instead of duplicating a version.
    """
    if not parts or not all(isinstance(p, str) and p for p in parts):
        raise LifeArtifactError("operation_key_for requires non-empty string parts")
    digest = sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:32]
    return f"life:{digest}"


class LifeArtifactPort:
    """Narrow read+write port onto the Life Supply ManagedArtifact owner."""

    def __init__(
        self,
        socket_path: str,
        subject_id: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        if not socket_path or not str(socket_path).startswith("/"):
            raise LifeArtifactError(f"socket_path must be an absolute path, got {socket_path!r}")
        if not subject_id:
            raise LifeArtifactError("subject_id is required")
        self.socket_path = str(socket_path)
        self.subject_id = str(subject_id)
        self.timeout = float(timeout)
        self.call_count = 0

    # -- transport ---------------------------------------------------------------------

    def _call(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Send one request and return the parsed answer.  Raises on anything unusable."""
        op = request.get("op")
        if op not in READ_OPS | COMMAND_OPS:
            raise LifeArtifactError(f"refused to send op={op!r}: outside this port's allow-list")
        body = json.dumps(dict(request), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(body) > MAX_RESPONSE_BYTES:
            raise LifeArtifactError("request exceeds the port's own payload bound")
        self.call_count += 1
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(self.timeout)
                client.connect(self.socket_path)
                client.sendall(body + b"\n")
                line = client.makefile("rb").readline(MAX_RESPONSE_BYTES)
        except OSError as exc:
            raise LifeArtifactUnavailable(f"LIFE_SUPPLY_UNREACHABLE:{exc}") from exc
        if not line:
            raise LifeArtifactUnavailable("LIFE_SUPPLY_EMPTY_RESPONSE")
        try:
            answer = json.loads(line.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise LifeArtifactUnavailable("LIFE_SUPPLY_MALFORMED_RESPONSE") from exc
        if not isinstance(answer, dict) or not isinstance(answer.get("status"), str):
            raise LifeArtifactUnavailable("LIFE_SUPPLY_UNTYPED_RESPONSE")
        return answer

    # -- availability ------------------------------------------------------------------

    def available(self) -> bool:
        try:
            answer = self._call({"op": "list_artifacts", "subject": self.subject_id})
        except (LifeArtifactError, ArtifactOwnerRefused):
            return False
        return answer.get("status") == STATUS_OK

    # -- reads -------------------------------------------------------------------------

    def list_artifacts(self) -> list[dict[str, Any]]:
        answer = self._call({"op": "list_artifacts", "subject": self.subject_id})
        if answer.get("status") != STATUS_OK:
            raise ArtifactOwnerRefused(answer.get("status", ""), answer.get("reason", ""),
                                       str(answer.get("detail") or ""))
        rows = answer.get("artifacts")
        return [dict(r) for r in rows] if isinstance(rows, list) else []

    def read_artifact(self, artifact_id: str) -> dict[str, Any]:
        answer = self._call({"op": "read_artifact", "subject": self.subject_id,
                             "artifact_id": str(artifact_id)})
        if answer.get("status") != STATUS_OK:
            raise ArtifactOwnerRefused(answer.get("status", ""), answer.get("reason", ""),
                                       str(answer.get("detail") or ""))
        return {"artifact": dict(answer.get("artifact") or {}),
                "head": dict(answer["head"]) if isinstance(answer.get("head"), dict) else None,
                "content": answer.get("content")}

    def read_head(self, artifact_id: str) -> Optional[dict[str, Any]]:
        answer = self._call({"op": "read_head", "subject": self.subject_id,
                             "artifact_id": str(artifact_id)})
        if answer.get("status") == STATUS_NOT_FOUND:
            return None
        if answer.get("status") != STATUS_OK:
            raise ArtifactOwnerRefused(answer.get("status", ""), answer.get("reason", ""),
                                       str(answer.get("detail") or ""))
        head = answer.get("head")
        return dict(head) if isinstance(head, dict) else None

    def resume_point(self, artifact_id: str) -> ArtifactResumePoint:
        """Read one artifact's real continuation point from the owner."""
        view = self.read_artifact(artifact_id)
        artifact = view["artifact"]
        head = view["head"] or {}
        return ArtifactResumePoint(
            artifact_id=str(artifact.get("artifact_id") or artifact_id),
            title=str(artifact.get("title") or ""),
            head_version_id=head.get("version_id"),
            version_number=head.get("version_number"),
            content=view.get("content"),
            lifecycle=str(artifact.get("lifecycle") or ""),
            revision=int(artifact.get("revision") or 0),
        )

    def latest_resumable(self) -> Optional[ArtifactResumePoint]:
        """The most recently revised ACTIVE artifact of this subject, or None.

        "None" is a real answer: it means the owner holds no continuable work, and the caller
        must not manufacture one.
        """
        best: Optional[dict[str, Any]] = None
        for row in self.list_artifacts():
            if str(row.get("lifecycle")) != "ACTIVE":
                continue
            if best is None or int(row.get("revision") or 0) > int(best.get("revision") or 0):
                best = row
        if best is None:
            return None
        return self.resume_point(str(best["artifact_id"]))

    # -- writes (the narrow write port) ------------------------------------------------

    def create_artifact(
        self,
        *,
        workspace_id: str,
        title: str,
        content: str,
        grant_id: str,
        operation_key: str,
    ) -> ArtifactOutcome:
        """Create a ManagedArtifact and commit its first version, as one owner command."""
        answer = self._call({
            "op": "create_artifact",
            "grant_id": str(grant_id),
            "operation_key": str(operation_key),
            "artifact": {
                "subject_id": self.subject_id,
                "workspace_id": str(workspace_id),
                "title": str(title),
                "content": str(content),
            },
        })
        return self._outcome(answer, operation_key=operation_key)

    def commit_version(
        self,
        *,
        artifact_id: str,
        expected_head: Optional[str],
        content: str,
        grant_id: str,
        operation_key: str,
    ) -> ArtifactOutcome:
        """Commit the next version of an existing artifact.

        ``expected_head`` is the compare-and-swap precondition: passing the head read before the
        edit means a concurrent writer is refused by the owner instead of silently overwritten.
        """
        answer = self._call({
            "op": "commit_markdown",
            "grant_id": str(grant_id),
            "operation_key": str(operation_key),
            "artifact": {
                "subject_id": self.subject_id,
                "artifact_id": str(artifact_id),
                "expected_head": expected_head,
                "content": str(content),
            },
        })
        return self._outcome(answer, operation_key=operation_key)

    # -- resolving an UNKNOWN without duplicating an effect ----------------------------

    def resolve_operation(self, operation_key: str) -> ArtifactOutcome:
        """Ask the owner what became of ``operation_key``.

        Used before retrying anything whose result was UNKNOWN.  ``read_effect_receipt`` is the
        owner's own durable record of the effect; ``lookup_operation`` is the fallback that shows
        the operation row even when no receipt was written.
        """
        try:
            receipt = self._call({"op": "read_effect_receipt", "operation_key": str(operation_key)})
        except LifeArtifactUnavailable:
            raise
        if receipt.get("status") == STATUS_OK and isinstance(receipt.get("receipt"), dict):
            row = receipt["receipt"]
            return ArtifactOutcome(
                effect_state=EffectState.APPLIED,
                status=STATUS_OK,
                reason="EFFECT_RECEIPT_FOUND",
                artifact_id=row.get("artifact_id"),
                version_id=row.get("version_id"),
                operation_key=str(operation_key),
                raw={"receipt": row},
            )
        try:
            found = self._call({"op": "lookup_operation", "owner": "ManagedArtifact",
                                "operation_id": str(operation_key)})
        except LifeArtifactUnavailable:
            raise
        if found.get("status") == STATUS_KNOWN:
            return ArtifactOutcome(
                effect_state=EffectState.APPLIED,
                status=STATUS_OK,
                reason="OPERATION_ROW_FOUND",
                operation_key=str(operation_key),
                raw={"operation": found.get("operation") or {}},
            )
        if found.get("status") == STATUS_NOT_FOUND and receipt.get("status") == STATUS_NOT_FOUND:
            # The owner positively knows nothing happened: safe to retry with the same key.
            return ArtifactOutcome(
                effect_state=EffectState.NO_CHANGE,
                status=STATUS_NOT_FOUND,
                reason="OPERATION_NEVER_LANDED",
                operation_key=str(operation_key),
                raw={},
            )
        return ArtifactOutcome(
            effect_state=EffectState.UNKNOWN,
            status=str(found.get("status") or STATUS_UNKNOWN),
            reason="RESOLUTION_INCONCLUSIVE",
            operation_key=str(operation_key),
            raw={"receipt": receipt, "lookup": found},
        )

    # -- answer classification ---------------------------------------------------------

    def _outcome(self, answer: Mapping[str, Any], *, operation_key: str) -> ArtifactOutcome:
        status = str(answer.get("status") or "")
        base = dict(
            artifact_id=answer.get("artifact_id"),
            version_id=answer.get("version_id"),
            version_number=answer.get("version_number"),
            head_version_id=answer.get("head_version_id"),
            parent_version_id=answer.get("parent_version_id"),
            version_created=answer.get("version_created"),
            operation_key=str(answer.get("operation_key") or operation_key),
            coordinated=answer.get("coordinated"),
            reservation_id=answer.get("reservation_id"),
            uow_id=answer.get("uow_id"),
            reason=str(answer.get("reason") or ""),
            detail=str(answer.get("detail") or ""),
            raw=dict(answer),
        )
        if status == STATUS_OK:
            return ArtifactOutcome(effect_state=EffectState.APPLIED, status=status, **base)
        if status == STATUS_NO_CHANGE:
            return ArtifactOutcome(effect_state=EffectState.NO_CHANGE, status=status, **base)
        if status == STATUS_NOT_FOUND:
            return ArtifactOutcome(effect_state=EffectState.NOT_FOUND, status=status, **base)
        if status in {STATUS_BLOCKED, STATUS_DENY}:
            return ArtifactOutcome(effect_state=EffectState.REFUSED, status=status, **base)
        # Anything else -- including the service's own UNKNOWN -- is an unknown effect.
        return ArtifactOutcome(effect_state=EffectState.UNKNOWN, status=status or STATUS_UNKNOWN,
                               **base)

    def close(self) -> None:
        """No persistent connection is held; present so callers have a uniform lifecycle."""


def parse_grant_ids(raw: str) -> Sequence[str]:
    """Accept the grant id list form used by the profile config (comma separated)."""
    return [part.strip() for part in str(raw or "").split(",") if part.strip()]


__all__ = [
    "ArtifactOutcome",
    "ArtifactOwnerRefused",
    "ArtifactResumePoint",
    "EffectState",
    "LifeArtifactError",
    "LifeArtifactPort",
    "LifeArtifactUnavailable",
    "operation_key_for",
    "parse_grant_ids",
    "COMMAND_OPS",
    "READ_OPS",
    "STATUS_BLOCKED",
    "STATUS_DENY",
    "STATUS_NOT_FOUND",
    "STATUS_NO_CHANGE",
    "STATUS_OK",
    "STATUS_UNKNOWN",
]

"""v0.2 E01/E05: the AR-0 action adapter that really commits a ManagedArtifact version.

Why this module exists
----------------------
AR-0 (``action_reality_ledger``) already owns action proposal, the durable submission fence,
attempts, receipts and reconciliation.  Its only file adapter is ``SandboxFileAdapter``, which
writes inside a temp sandbox directory, and the production composition root installs
``RefusingFileAdapter`` on top of it -- so an adopted Life step could not reach a real work at
all.  E01.2 asks for exactly one narrow bridge instead of a new agent: this adapter.

What it does not do
-------------------
* It never imports ``ManagedArtifactStore`` and never opens the Supply database.  It holds one
  already-verified :class:`LifeArtifactPort` and calls the existing service over its socket, as
  the service principal.  There is no constructor argument for a socket path, a database path or
  a caller role coming from a model.
* It does not decide anything.  AG-1 decides, AG-2 adopts, AR-0 owns the action lifecycle, and
  the Supply owner decides whether the work actually changed.
* It never claims success it did not receive.  Every receipt it returns is built from the
  owner's own answer, and a lost answer stays ``AMBIGUOUS_POST_SUBMISSION_FAILURE``.

The one identity rule that matters
----------------------------------
The Supply ``operation_key`` is derived from the AR-0 action's ``idempotency_key``, which AR-0
already derives deterministically from the step spec.  So one logical action maps to one Supply
operation for its whole life: a reconciliation after an ``UNKNOWN`` looks up the *original*
operation instead of minting a second version.

Why it also re-reads the owner
------------------------------
A receipt is the owner *saying* it wrote, and AR-1's ``FILE_ACTION`` success rule wants the effect
observed.  So after the owner confirms a version, and again when a reconciliation confirms the
original operation, this adapter re-reads that version through the same official read port
(``observed_result_for_action``) and hands AR-0 a :class:`ResultEvidence` only if the owner's own
record still shows that version, produced by that ``operation_key``, with the bytes this action
submitted.  A read that cannot establish that returns no evidence at all, and AR-1 keeps the
action open -- the adapter never turns a claim into an observation.

The archive verb (v0.4 work closeout)
-------------------------------------
``archive_artifact`` is the owner's own lifecycle verb, and this adapter treats it differently from
a commit on purpose: a commit is *progress*, an archive is *the end of one work*.  ``OK`` means
this operation closed this artifact; ``NO_CHANGE`` means it was already closed by someone else,
which is not this action's success; ``CONFLICT`` means the revision the intent was based on is no
longer current, and the caller must reconsider instead of having a newer revision swapped in.  The
observed-result rule is the same as for a commit, only the fact differs: the owner's own operation
row, its effect receipt and the artifact's current state must all agree that *this* key archived
it before an :class:`artifact_archived_verified` attestation is handed over.  The artifact, its
versions and its content survive an archive; nothing here deletes or tombstones anything.
"""
from __future__ import annotations

import copy
import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

_LIFE_SCRIPTS = str(Path(__file__).resolve().parent)
if _LIFE_SCRIPTS not in sys.path:
    sys.path.insert(0, _LIFE_SCRIPTS)

from action_reality_ledger import (  # noqa: E402
    ACTION_KIND_FILE,
    AdapterCapabilities,
    AdapterSubmissionOutcome,
    ActionAdapterProtocol,
    ActionReceipt,
    EVIDENCE_KIND_PROVIDER_REPORTED,
    RECEIPT_CLAIM_FAILED,
    RECEIPT_CLAIM_REJECTED,
    RECEIPT_CLAIM_SUCCEEDED,
    RECON_RESULT_CONFIRMED_SUCCEEDED,
    RECON_RESULT_NOT_SUBMITTED,
    RECON_RESULT_STILL_UNKNOWN,
    RECON_STRATEGY_PROVIDER_LOOKUP,
    ResultEvidence,
    TRANSPORT_ERR_AMBIGUOUS_POST_SUBMIT,
    TRANSPORT_ERR_DEFINITE_REJECT,
    TRANSPORT_OK_SUCCEEDED,
)
from life_artifact_port import (  # noqa: E402
    ArtifactOwnerRefused,
    EffectState,
    LifeArtifactPort,
    LifeArtifactUnavailable,
)
from artifact_closeout import CLOSEOUT_PREDICATE  # noqa: E402

#: The narrow verbs this adapter may execute.  Anything else is refused before submission.
OP_READ_ARTIFACT = "read_artifact"
OP_COMMIT_VERSION = "commit_artifact_version"
#: The owner's own lifecycle verb (``life_supply.COMMAND_OPS`` spells it ``archive_artifact``).  It
#: closes one work and keeps every row, version and the content -- this adapter has no tombstone
#: verb at all, because a tombstone is terminal and hides the content.
OP_ARCHIVE_ARTIFACT = "archive_artifact"
SUPPORTED_OPS = frozenset({OP_READ_ARTIFACT, OP_COMMIT_VERSION, OP_ARCHIVE_ARTIFACT})

#: The kind the owner's own operation ledger records for a lifecycle change: ``artifact/store.py``
#: inserts the target ``lifecycle`` (``"ARCHIVED"``) into ``artifact_operations.operation_kind``.
ARCHIVE_OPERATION_KIND = "ARCHIVED"

#: Authority domain recorded on every receipt, so a later reader can tell which owner spoke.
AUTHORITY_DOMAIN = "artifact_authority"
PROVIDER = "chiyo-life-supply"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ManagedArtifactActionError(RuntimeError):
    """The adapter refused to build or execute this action."""


class SupplyManagedArtifactActionAdapter(ActionAdapterProtocol):
    """AR-0 file adapter whose effect is a real ManagedArtifact version commit."""

    capabilities = AdapterCapabilities(
        adapter_kind="supply_managed_artifact_adapter",
        action_kind=ACTION_KIND_FILE,
        submission_boundary="supply_service_command_dispatched",
        reconcile_strategy=RECON_STRATEGY_PROVIDER_LOOKUP,
        supports_idempotency=True,
        supports_status_query=True,
        supports_receipt=True,
        supports_cancel=False,
        supports_observed_result=True,
    )

    def __init__(
        self,
        port: LifeArtifactPort,
        *,
        grant_id: str,
        workspace_id: Optional[str] = None,
        fault_injection_drop_response_after_write: bool = False,
    ) -> None:
        if not grant_id:
            raise ManagedArtifactActionError("a Governance grant id is required to write")
        self.port = port
        self.grant_id = str(grant_id)
        self.workspace_id = workspace_id
        #: Test-only fault injection: perform the real owner write, then lose the answer, so the
        #: UNKNOWN -> reconcile -> no-second-version path can be exercised honestly.
        self.fault_injection_drop_response_after_write = bool(
            fault_injection_drop_response_after_write)
        self.submit_call_count = 0
        self.completed_by_idem_key: dict[str, AdapterSubmissionOutcome] = {}

    @staticmethod
    def _receipt_id(attempt: Mapping[str, Any], operation_key: str, suffix: str = "") -> str:
        """A receipt id that is stable for one (attempt, operation) pair."""
        seed = f"{attempt['attempt_id']}:{operation_key}{(':' + suffix) if suffix else ''}"
        return f"arcpt:{_sha256_hex(seed)[:16]}"

    # -- identity ----------------------------------------------------------------------

    @staticmethod
    def operation_key_for(idempotency_key: str) -> str:
        """One logical action -> one Supply operation, for the whole life of the action."""
        return f"life:{_sha256_hex(f'ar0:{idempotency_key}')[:32]}"

    # -- prepare -----------------------------------------------------------------------

    def validate_prepare(self, action: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
        from action_reality_ledger import ContractViolationError

        op = payload.get("op")
        if op not in SUPPORTED_OPS:
            raise ContractViolationError(f"unsupported MANAGED_ARTIFACT op={op!r}")
        artifact_id = payload.get("artifact_id")
        if not isinstance(artifact_id, str) or not artifact_id.strip():
            raise ContractViolationError("MANAGED_ARTIFACT action requires a non-empty 'artifact_id'")
        normalized: dict[str, Any] = {"op": op, "artifact_id": artifact_id.strip()}
        if op == OP_COMMIT_VERSION:
            expected_head = payload.get("expected_head")
            if expected_head is not None and not isinstance(expected_head, str):
                raise ContractViolationError("'expected_head' must be a string or null")
            content = payload.get("content")
            if not isinstance(content, str):
                raise ContractViolationError("'content' must be a string")
            normalized["expected_head"] = expected_head
            normalized["content"] = content
            normalized["content_sha256"] = _sha256_hex(content)
        elif op == OP_ARCHIVE_ARTIFACT:
            # The archive intent is exactly these three parts: which owner artifact, the owner
            # revision the decision was based on, and the derived Supply operation key.  The key is
            # not part of the frozen payload because AR-0 already derives it from the action's
            # ``idempotency_key``; a payload that could name its own key would let one intended
            # archive be replayed as another.
            expected_revision = payload.get("expected_revision")
            if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) \
                    or expected_revision < 1:
                raise ContractViolationError(
                    "'expected_revision' must be the integer artifact revision read from the "
                    "owner (>= 1); an archive without one is not a full intent")
            normalized["expected_revision"] = expected_revision
        return normalized

    # -- submit ------------------------------------------------------------------------

    def submit(
        self,
        *,
        executor_capability: Any,
        action: Mapping[str, Any],
        attempt: Mapping[str, Any],
        payload: Mapping[str, Any],
        fault_hook: Optional[Any] = None,
    ) -> AdapterSubmissionOutcome:
        executor_capability.verify()
        idem_key = str(action["idempotency_key"])
        if idem_key in self.completed_by_idem_key:
            # AR-0 already forbids a bare retry after UNKNOWN; this covers a duplicate submit of
            # an action that is already SUBMITTED/SUCCEEDED, and must not write twice.
            return copy.deepcopy(self.completed_by_idem_key[idem_key])

        self.submit_call_count += 1
        operation_key = self.operation_key_for(idem_key)
        op = str(payload["op"])
        artifact_id = str(payload["artifact_id"])

        if op == OP_READ_ARTIFACT:
            return self._do_read(action=action, attempt=attempt, artifact_id=artifact_id,
                                 operation_key=operation_key)

        if op == OP_ARCHIVE_ARTIFACT:
            return self._do_archive(
                action=action, attempt=attempt, artifact_id=artifact_id,
                expected_revision=int(payload["expected_revision"]),
                operation_key=operation_key, fault_hook=fault_hook)

        expected_head = payload.get("expected_head")
        content = str(payload.get("content", ""))

        if self.fault_injection_drop_response_after_write:
            # The owner write really happens; the caller never sees the answer.
            self.fault_injection_drop_response_after_write = False
            try:
                self.port.commit_version(
                    artifact_id=artifact_id, expected_head=expected_head, content=content,
                    grant_id=self.grant_id, operation_key=operation_key)
            except (LifeArtifactUnavailable, ArtifactOwnerRefused):
                pass
            if fault_hook is not None:
                fault_hook("response_lost_after_write")
            return AdapterSubmissionOutcome(
                transport_result=TRANSPORT_ERR_AMBIGUOUS_POST_SUBMIT,
                provider_operation_ref=operation_key,
                error_class="SUPPLY_RESPONSE_LOST",
                error_ref=f"err:{operation_key}:response_lost",
            )

        try:
            outcome = self.port.commit_version(
                artifact_id=artifact_id, expected_head=expected_head, content=content,
                grant_id=self.grant_id, operation_key=operation_key)
        except LifeArtifactUnavailable as exc:
            # The command may or may not have reached the owner.  Never guess.
            return AdapterSubmissionOutcome(
                transport_result=TRANSPORT_ERR_AMBIGUOUS_POST_SUBMIT,
                provider_operation_ref=operation_key,
                error_class="SUPPLY_UNREACHABLE",
                error_ref=f"err:{operation_key}:unreachable",
            )
        except ArtifactOwnerRefused as exc:
            return self._rejection(action=action, attempt=attempt, operation_key=operation_key,
                                   owner_status=exc.status, detail=f"{exc.reason}:{exc.detail}")

        if fault_hook is not None:
            fault_hook("during_adapter_call")

        if outcome.effect_state == EffectState.APPLIED:
            return self._success(
                action=action, attempt=attempt, operation_key=operation_key,
                owner_status=outcome.status, version_id=outcome.version_id,
                version_number=outcome.version_number, version_created=True,
                content_sha256=str(payload.get("content_sha256") or ""),
                details={"owner_status": outcome.status, "version_created": True},
                # The owner said it wrote.  Re-read the owner so AR-1 can settle on an observed
                # effect instead of on a claim; a verification that cannot be established leaves
                # this None and the action keeps its provider-reported receipt only.
                observed_result=self.observed_result_for_action(action=action))
        if outcome.effect_state == EffectState.NO_CHANGE:
            # The owner answered, and it answered "nothing to write".  The action did happen; the
            # work did not change.  Those are two different facts and are kept apart here.
            return self._success(
                action=action, attempt=attempt, operation_key=operation_key,
                owner_status=outcome.status, version_id=outcome.head_version_id,
                version_number=None, version_created=False,
                content_sha256=str(payload.get("content_sha256") or ""),
                details={"owner_status": outcome.status, "version_created": False,
                         "owner_detail": outcome.detail or outcome.reason})
        if outcome.effect_state == EffectState.CONFLICT:
            return self._rejection(
                action=action, attempt=attempt, operation_key=operation_key,
                owner_status=outcome.status,
                detail=f"stale expected_head; owner head is {outcome.raw.get('current_head')!r}")
        if outcome.effect_state in {EffectState.REFUSED, EffectState.NOT_FOUND}:
            return self._rejection(
                action=action, attempt=attempt, operation_key=operation_key,
                owner_status=outcome.status,
                detail=f"{outcome.reason}:{outcome.detail}")
        # Anything else -- including the service's own UNKNOWN -- crossed the boundary with an
        # unestablished result.
        return AdapterSubmissionOutcome(
            transport_result=TRANSPORT_ERR_AMBIGUOUS_POST_SUBMIT,
            provider_operation_ref=operation_key,
            error_class=f"SUPPLY_{outcome.status or 'UNKNOWN'}",
            error_ref=f"err:{operation_key}:{outcome.status or 'unknown'}",
        )

    # -- outcome builders --------------------------------------------------------------

    def _success(self, *, action, attempt, operation_key, owner_status, version_id,
                 version_number, version_created, content_sha256, details,
                 observed_result: Optional[ResultEvidence] = None,
                 receipt_kind: str = "managed_artifact_commit_receipt") -> AdapterSubmissionOutcome:
        evidence_ref = (f"artifactversion:{version_id}" if version_id
                        else f"supply_op:{operation_key}")
        receipt = ActionReceipt(
            receipt_id=self._receipt_id(attempt, operation_key),
            action_id=str(action["action_id"]),
            attempt_id=str(attempt["attempt_id"]),
            receipt_kind=receipt_kind,
            provider=PROVIDER,
            provider_operation_ref=operation_key,
            status_claim=RECEIPT_CLAIM_SUCCEEDED,
            evidence_kind=EVIDENCE_KIND_PROVIDER_REPORTED,
            authority_domain=AUTHORITY_DOMAIN,
            evidence_refs=[evidence_ref],
            occurred_at=_utc_now_iso(),
            observed_at=_utc_now_iso(),
            recorded_at=_utc_now_iso(),
            enqueued_at=_utc_now_iso(),
            source_hash=content_sha256 or None,
            details={"owner_status": owner_status, "version_id": version_id,
                     "version_number": version_number, "version_created": version_created,
                     "operation_key": operation_key, **details},
        )
        return AdapterSubmissionOutcome(
            transport_result=TRANSPORT_OK_SUCCEEDED,
            provider_operation_ref=operation_key,
            receipt=receipt,
            result_evidence=observed_result,
        )

    def _rejection(self, *, action, attempt, operation_key, owner_status,
                   detail) -> AdapterSubmissionOutcome:
        receipt = ActionReceipt(
            receipt_id=self._receipt_id(attempt, operation_key, "rej"),
            action_id=str(action["action_id"]),
            attempt_id=str(attempt["attempt_id"]),
            receipt_kind="managed_artifact_commit_rejection",
            provider=PROVIDER,
            provider_operation_ref=operation_key,
            status_claim=RECEIPT_CLAIM_REJECTED,
            evidence_kind=EVIDENCE_KIND_PROVIDER_REPORTED,
            authority_domain=AUTHORITY_DOMAIN,
            evidence_refs=[f"supply_op:{operation_key}"],
            occurred_at=_utc_now_iso(),
            observed_at=_utc_now_iso(),
            recorded_at=_utc_now_iso(),
            enqueued_at=_utc_now_iso(),
            details={"owner_status": owner_status, "detail": detail,
                     "operation_key": operation_key},
        )
        return AdapterSubmissionOutcome(
            transport_result=TRANSPORT_ERR_DEFINITE_REJECT,
            provider_operation_ref=operation_key,
            receipt=receipt,
            error_class=f"OWNER_{owner_status}",
            error_ref=f"err:{operation_key}:{owner_status}",
        )

    # -- observed result (owner re-read, Section 34/37/77) -----------------------------

    def observed_result_for_action(self, *, action: Mapping[str, Any]) -> Optional[ResultEvidence]:
        """Re-read the owner and return observed result evidence for the version it committed.

        A ``PROVIDER_REPORTED`` receipt is the owner *saying* it wrote; AR-1's ``FILE_ACTION``
        success rule wants the effect actually observed, so this asks the same official Supply read
        port the rest of the Life runtime uses.  It accepts the effect only when all of these hold:

        * the owner's own durable record for *this* action's original ``operation_key``
          (``read_effect_receipt``, falling back to ``lookup_operation``) names a version,
        * that version is the artifact's current head,
        * the head version was produced by that same ``operation_key``, and
        * the head's own stored ``content_sha256`` is the hash of the bytes this action submitted.

        Anything short of that returns ``None``: no evidence is invented, the receipt stays
        provider-reported, and AR-1 keeps the action open.  The evidence id is derived from
        (action, version, observer), so re-verifying the same fact never adds a second root
        evidence, and this method only ever reads -- it can never mint a second version.

        For an ``archive_artifact`` intent the same discipline applies with a different fact: the
        evidence exists only when the owner's own operation row for *this* key says the archive
        happened, the owner's effect receipt for that key says ``APPLIED``, and the artifact's
        current state still reads back ``ARCHIVED`` at exactly the revision that operation
        produced (``_observed_archive_result``).  An artifact that merely *is* ``ARCHIVED`` -- by
        another operation, or before this action existed -- is never turned into this action's
        evidence.
        """
        payload = dict(action.get("frozen_payload") or {})
        op = str(payload.get("op") or "")
        if op == OP_ARCHIVE_ARTIFACT:
            return self._observed_archive_result(action=action, payload=payload)
        if op != OP_COMMIT_VERSION:
            return None
        artifact_id = str(payload.get("artifact_id") or "")
        if not artifact_id:
            return None
        operation_key = self.operation_key_for(str(action["idempotency_key"]))
        try:
            resolved = self.port.resolve_operation(operation_key)
            head = self.port.read_head(artifact_id)
        except (LifeArtifactUnavailable, ArtifactOwnerRefused):
            return None
        if resolved.effect_state != EffectState.APPLIED or not resolved.version_id:
            return None
        version_id = str(resolved.version_id)
        if resolved.artifact_id and str(resolved.artifact_id) != artifact_id:
            return None
        if not head:
            return None
        if str(head.get("version_id") or "") != version_id:
            return None
        if str(head.get("operation_key") or "") != operation_key:
            return None
        content = str(payload.get("content") or "")
        expected_hash = str(payload.get("content_sha256") or _sha256_hex(content))
        observed_hash = str(head.get("content_sha256") or "")
        action_id = str(action["action_id"])
        ts = _utc_now_iso()
        return ResultEvidence(
            evidence_id=f"aevid:{_sha256_hex(f'{action_id}:{version_id}:artifact_observer')[:16]}",
            action_id=action_id,
            observer_kind="artifact_observer",
            artifact_ref=artifact_id,
            verified=bool(observed_hash) and observed_hash == expected_hash,
            expected_hash=expected_hash,
            observed_hash=observed_hash or None,
            source_refs=[f"supply_op:{operation_key}", f"artifactversion:{version_id}"],
            details={
                "observer": "supply_read_port",
                "operation_key": operation_key,
                "artifact_id": artifact_id,
                "version_id": version_id,
                "version_number": head.get("version_number"),
                "version_created": True,
                "owner_status": "OK",
                "hash_mismatch": bool(observed_hash) and observed_hash != expected_hash,
            },
            occurred_at=ts,
            observed_at=ts,
            recorded_at=ts,
        )

    def _observed_archive_result(self, *, action, payload) -> Optional[ResultEvidence]:
        """Three owner-side facts, and only when all three agree: this key archived this artifact.

        1. the owner's **operation row** for this action's original ``operation_key`` exists, is an
           ``ARCHIVED`` lifecycle operation on this artifact, and its own stored result says
           ``OK``/``ARCHIVED`` at a revision;
        2. the owner's **effect receipt** for that same key exists, is ``APPLIED`` and names the
           same artifact;
        3. the artifact **reads back** ``ARCHIVED`` right now, at exactly that revision, so the
           state the operation produced is still the state the owner serves.

        A stale or replayed answer cannot pass this: an ``ARCHIVED`` artifact archived by a
        *different* operation key has no operation row under this key (its own key answers
        ``NO_CHANGE`` without a receipt), and any later lifecycle change moves the revision away
        from the one this operation recorded.  The evidence only ever reads.
        """
        import json

        artifact_id = str(payload.get("artifact_id") or "")
        expected_revision = payload.get("expected_revision")
        if not artifact_id:
            return None
        operation_key = self.operation_key_for(str(action["idempotency_key"]))
        try:
            resolved = self.port.resolve_operation(operation_key)
            if resolved.effect_state != EffectState.APPLIED:
                return None
            if resolved.reason != "EFFECT_RECEIPT_FOUND":
                # Only an effect receipt is the owner saying the effect landed; an operation row
                # without a receipt is exactly the "already archived" (NO_CHANGE) case.
                return None
            receipt = dict((resolved.raw or {}).get("receipt") or {})
            row = self.port.operation_row(operation_key)
            if not isinstance(row, dict):
                return None
            view = self.port.read_artifact(artifact_id)
        except (LifeArtifactUnavailable, ArtifactOwnerRefused):
            return None
        if str(row.get("operation_kind") or "") != ARCHIVE_OPERATION_KIND:
            return None
        if str(row.get("artifact_id") or "") != artifact_id:
            return None
        try:
            recorded = json.loads(str(row.get("result_json") or "{}"))
        except ValueError:
            return None
        if not isinstance(recorded, dict):
            return None
        if str(recorded.get("status") or "") != "OK":
            return None
        if str(recorded.get("lifecycle") or "") != ARCHIVE_OPERATION_KIND:
            return None
        if str(recorded.get("artifact_id") or "") != artifact_id:
            return None
        archived_revision = recorded.get("revision")
        if isinstance(archived_revision, bool) or not isinstance(archived_revision, int):
            return None
        if isinstance(expected_revision, int) and not isinstance(expected_revision, bool) \
                and archived_revision != expected_revision + 1:
            return None
        if str(receipt.get("artifact_id") or "") != artifact_id:
            return None
        if str(receipt.get("status") or "") != "APPLIED":
            return None
        if str(receipt.get("effect_kind") or "") != ARCHIVE_OPERATION_KIND:
            return None
        artifact = dict(view.get("artifact") or {})
        if str(artifact.get("lifecycle") or "") != ARCHIVE_OPERATION_KIND:
            return None
        if int(artifact.get("revision") or 0) != archived_revision:
            return None
        action_id = str(action["action_id"])
        ts = _utc_now_iso()
        return ResultEvidence(
            evidence_id=(
                f"aevid:{_sha256_hex(f'{action_id}:{operation_key}:archive_observer')[:16]}"),
            action_id=action_id,
            observer_kind="artifact_observer",
            artifact_ref=artifact_id,
            verified=True,
            expected_hash=None,
            observed_hash=None,
            source_refs=[f"supply_op:{operation_key}",
                         f"supply_receipt:{receipt.get('receipt_id')}"],
            details={
                "observer": "supply_read_port",
                # The attestation this evidence carries.  It is what the adapter *saw*, and it is
                # recorded next to the three owner facts it was built from.
                "attestation": CLOSEOUT_PREDICATE,
                "predicate": CLOSEOUT_PREDICATE,
                "operation_key": operation_key,
                "operation_kind": ARCHIVE_OPERATION_KIND,
                "artifact_id": artifact_id,
                "lifecycle": ARCHIVE_OPERATION_KIND,
                "revision_before": expected_revision,
                "revision_after": archived_revision,
                "receipt_id": receipt.get("receipt_id"),
                "receipt_status": "APPLIED",
                "operation_outcome": recorded.get("status"),
                "owner_status": "OK",
                "archived_by_this_operation": True,
                "version_created": False,
            },
            occurred_at=ts,
            observed_at=ts,
            recorded_at=ts,
        )

    def _do_read(self, *, action, attempt, artifact_id, operation_key) -> AdapterSubmissionOutcome:
        try:
            view = self.port.read_artifact(artifact_id)
        except LifeArtifactUnavailable:
            return AdapterSubmissionOutcome(
                transport_result=TRANSPORT_ERR_AMBIGUOUS_POST_SUBMIT,
                provider_operation_ref=operation_key,
                error_class="SUPPLY_UNREACHABLE",
                error_ref=f"err:{operation_key}:unreachable")
        except ArtifactOwnerRefused as exc:
            return self._rejection(action=action, attempt=attempt, operation_key=operation_key,
                                   owner_status=exc.status, detail=f"{exc.reason}:{exc.detail}")
        head = view.get("head") or {}
        receipt = ActionReceipt(
            receipt_id=self._receipt_id(attempt, operation_key, "read"),
            action_id=str(action["action_id"]),
            attempt_id=str(attempt["attempt_id"]),
            receipt_kind="managed_artifact_read_receipt",
            provider=PROVIDER,
            provider_operation_ref=operation_key,
            status_claim=RECEIPT_CLAIM_SUCCEEDED,
            evidence_kind=EVIDENCE_KIND_PROVIDER_REPORTED,
            authority_domain=AUTHORITY_DOMAIN,
            evidence_refs=[f"artifactversion:{head.get('version_id')}"],
            occurred_at=_utc_now_iso(),
            observed_at=_utc_now_iso(),
            recorded_at=_utc_now_iso(),
            enqueued_at=_utc_now_iso(),
            details={"owner_status": "OK", "version_id": head.get("version_id"),
                     "version_number": head.get("version_number")},
        )
        return AdapterSubmissionOutcome(
            transport_result=TRANSPORT_OK_SUCCEEDED,
            provider_operation_ref=operation_key,
            receipt=receipt,
        )

    def _do_archive(
        self,
        *,
        action,
        attempt,
        artifact_id,
        expected_revision,
        operation_key,
        fault_hook,
    ) -> AdapterSubmissionOutcome:
        """Close one work through the owner's own ``archive_artifact``, and report only its answer.

        Three answers mean three *different* things and are kept apart:

        * ``OK`` (``APPLIED``) -- this operation archived this artifact.  The receipt says so and
          ``observed_result_for_action`` re-reads the owner before any evidence is handed over.
        * ``NO_CHANGE`` -- the artifact was **already** ``ARCHIVED`` when this key arrived.  The
          work is closed, but not by this operation, so this returns a succeeded action **without**
          any ``artifact_archived_verified`` evidence: an archive someone else did is not evidence
          that this action archived it.
        * ``CONFLICT`` -- the owner's revision moved past the one the intent was based on.  The
          caller must re-read and reconsider; this adapter never swaps in a newer revision itself.
        """
        if self.fault_injection_drop_response_after_write:
            # The owner archive really happens; the caller never sees the answer.  This is the only
            # honest way to exercise "resolve the UNKNOWN with the *original* key".
            self.fault_injection_drop_response_after_write = False
            try:
                self.port.archive_artifact(
                    artifact_id=artifact_id, expected_revision=expected_revision,
                    grant_id=self.grant_id, operation_key=operation_key)
            except (LifeArtifactUnavailable, ArtifactOwnerRefused):
                pass
            if fault_hook is not None:
                fault_hook("response_lost_after_archive")
            return AdapterSubmissionOutcome(
                transport_result=TRANSPORT_ERR_AMBIGUOUS_POST_SUBMIT,
                provider_operation_ref=operation_key,
                error_class="SUPPLY_RESPONSE_LOST",
                error_ref=f"err:{operation_key}:response_lost",
            )
        try:
            outcome = self.port.archive_artifact(
                artifact_id=artifact_id, expected_revision=expected_revision,
                grant_id=self.grant_id, operation_key=operation_key)
        except LifeArtifactUnavailable:
            return AdapterSubmissionOutcome(
                transport_result=TRANSPORT_ERR_AMBIGUOUS_POST_SUBMIT,
                provider_operation_ref=operation_key,
                error_class="SUPPLY_UNREACHABLE",
                error_ref=f"err:{operation_key}:unreachable",
            )
        except ArtifactOwnerRefused as exc:
            return self._rejection(action=action, attempt=attempt, operation_key=operation_key,
                                   owner_status=exc.status, detail=f"{exc.reason}:{exc.detail}")

        if fault_hook is not None:
            fault_hook("during_adapter_call")

        if outcome.effect_state == EffectState.APPLIED:
            return self._success(
                action=action, attempt=attempt, operation_key=operation_key,
                owner_status=outcome.status, version_id=None, version_number=None,
                version_created=False, content_sha256="",
                receipt_kind="managed_artifact_archive_receipt",
                details={"lifecycle": outcome.lifecycle or ARCHIVE_OPERATION_KIND,
                         "owner_revision": outcome.revision,
                         "archived_by_this_operation": True,
                         "attestation": CLOSEOUT_PREDICATE},
                # The owner said it archived.  Ask the same official read port whether the owner's
                # own operation record, its effect receipt and the artifact's current state all
                # agree that *this* key did it; anything short of that leaves this None.
                observed_result=self.observed_result_for_action(action=action))
        if outcome.effect_state == EffectState.NO_CHANGE:
            return self._success(
                action=action, attempt=attempt, operation_key=operation_key,
                owner_status=outcome.status, version_id=None, version_number=None,
                version_created=False, content_sha256="",
                receipt_kind="managed_artifact_archive_receipt",
                details={"lifecycle": outcome.lifecycle or ARCHIVE_OPERATION_KIND,
                         "owner_revision": outcome.revision,
                         "archived_by_this_operation": False,
                         "attestation": None,
                         "owner_detail": (outcome.detail or outcome.reason
                                          or "already archived before this operation")},
                observed_result=None)
        if outcome.effect_state == EffectState.CONFLICT:
            return self._rejection(
                action=action, attempt=attempt, operation_key=operation_key,
                owner_status=outcome.status,
                detail=(f"stale expected_revision; owner revision is "
                        f"{outcome.raw.get('revision')!r}"))
        if outcome.effect_state in {EffectState.REFUSED, EffectState.NOT_FOUND}:
            return self._rejection(
                action=action, attempt=attempt, operation_key=operation_key,
                owner_status=outcome.status, detail=f"{outcome.reason}:{outcome.detail}")
        return AdapterSubmissionOutcome(
            transport_result=TRANSPORT_ERR_AMBIGUOUS_POST_SUBMIT,
            provider_operation_ref=operation_key,
            error_class=f"SUPPLY_{outcome.status or 'UNKNOWN'}",
            error_ref=f"err:{operation_key}:{outcome.status or 'unknown'}",
        )

    # -- reconcile ---------------------------------------------------------------------

    def query_status_for_reconcile(
        self,
        *,
        action: Mapping[str, Any],
        attempt: Optional[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Ask the owner what became of this action's *original* operation."""
        operation_key = self.operation_key_for(str(action["idempotency_key"]))
        query_ref = f"supply_op:{operation_key}"
        try:
            resolved = self.port.resolve_operation(operation_key)
        except LifeArtifactUnavailable:
            return {"strategy": RECON_STRATEGY_PROVIDER_LOOKUP,
                    "result": RECON_RESULT_STILL_UNKNOWN,
                    "artifact_query_ref": query_ref, "evidence_refs": []}
        if resolved.effect_state == EffectState.APPLIED:
            answer = {"strategy": RECON_STRATEGY_PROVIDER_LOOKUP,
                      "result": RECON_RESULT_CONFIRMED_SUCCEEDED,
                      "artifact_query_ref": f"{query_ref}:{resolved.reason}",
                      "evidence_refs": [query_ref]}
            # The owner confirmed *this* operation produced a version.  Re-read that version through
            # the read port so the resolution carries an observed effect, not just a claim; AR-0
            # records it, and AR-1 settles on its own rules.  If it cannot be observed, the
            # reconciliation still stands and no evidence is attached.
            observed = self.observed_result_for_action(action=action)
            if observed is not None:
                answer["result_evidence"] = observed
            return answer
        if resolved.effect_state == EffectState.NO_CHANGE:
            return {"strategy": RECON_STRATEGY_PROVIDER_LOOKUP,
                    "result": RECON_RESULT_NOT_SUBMITTED,
                    "artifact_query_ref": f"{query_ref}:{resolved.reason}",
                    "evidence_refs": [query_ref]}
        if resolved.effect_state in {EffectState.REFUSED, EffectState.NOT_FOUND}:
            return {"strategy": RECON_STRATEGY_PROVIDER_LOOKUP,
                    "result": "CONFIRMED_FAILED",
                    "artifact_query_ref": f"{query_ref}:{resolved.status}",
                    "evidence_refs": [query_ref]}
        return {"strategy": RECON_STRATEGY_PROVIDER_LOOKUP,
                "result": RECON_RESULT_STILL_UNKNOWN,
                "artifact_query_ref": f"{query_ref}:{resolved.reason}",
                "evidence_refs": []}


__all__ = [
    "ARCHIVE_OPERATION_KIND",
    "AUTHORITY_DOMAIN",
    "ManagedArtifactActionError",
    "OP_ARCHIVE_ARTIFACT",
    "OP_COMMIT_VERSION",
    "OP_READ_ARTIFACT",
    "SUPPORTED_OPS",
    "SupplyManagedArtifactActionAdapter",
]

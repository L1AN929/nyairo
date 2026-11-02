"""v0.2 B5/E07 acceptance: a verified outcome becomes a memory, and stays governable.

Deterministic on purpose -- no model is called here.  The real-model A/B control is a separate
script, and the two evidence sets are never reported as one.

What this proves
----------------
1. an AG-2 experience handoff lands in M0 through the single existing writer, with the
   non-chat origin and its own provenance triple;
2. delivering the same handoff twice is a ``duplicate``, not a second experience;
3. the read view returns it, newest first and bounded;
4. a Memory failure defers the handoff instead of rolling anything back;
5. governance: a stop-recall / delete makes the experience disappear from the view;
6. governance that cannot be established yields an empty view (fail closed);
7. the injection boundary: the content writer sees the admitted experience when a provider is
   installed, and sees nothing when it is not -- without deleting anything.

The handoff in 1-4 is issued and persisted by a real AG-2 owner (``tests/fixtures/
memory_owner_handoff.py``: real IntegrationStore/lease/capability/commit boundary and the real
``emit_experience_handoff`` entry), and the consumer's verifier is that same owner's read port.
The source check is never turned off for the acceptance's convenience: a mapping the owner did
not persist is still refused before M0.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NATIVE = ROOT / "native"
LIFE_SCRIPTS = ROOT / "life" / "scripts"
FIXTURES = ROOT.parent / "tests" / "fixtures"
for path in (str(LIFE_SCRIPTS), str(NATIVE), str(NATIVE / "src"),
             str(NATIVE / "memory_runtime_v1"), str(FIXTURES)):
    if path not in sys.path:
        sys.path.insert(0, path)

FAILURES: list[str] = []


def check(condition: bool, label: str, detail: object = "") -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} :: {detail}")
        FAILURES.append(f"{label} :: {detail}")


def _drive(store, ledger, port, subject: str, owner, tmp_path: Path) -> None:
    """Run every acceptance section; ``main`` owns the owner's lease/flush lifecycle."""

    import life_integration_ag2 as ag2
    from app.evidence import LIFE_ORIGIN
    from life_experience_port import LifeExperiencePort, life_conversation_id
    from life_experience_memory_consumer import MemoryAdmissionExperienceConsumer

    # Real AG-2 owner: register the Activity row it reads, open the integration it journals,
    # then let the real ``emit_experience_handoff`` derive/persist the handoff.
    owner.register_activity("actv_test_1", revision=1, activity_kind="WRITE")
    owner.open_integration(
        integration_id="lint:memorycheck0001",
        correlation_id="corr:memorycheck0001",
        decision_ref="dec:1", adoption_ref="adp:1",
        action_refs=["ar0act:1"], settlement_refs=["settle:1"])
    issued = owner.emit(
        integration_id="lint:memorycheck0001",
        activity_id="actv_test_1",
        kind=ag2.HANDOFF_KIND_SIGNIFICANT_PROGRESS,
        occurred_at="2026-10-10T20:00:00+08:00",
        artifact_refs=["artifact:artifact:abc"])
    #: What is delivered below is the record the owner itself wrote and read back, not a copy
    #: assembled here; the consumer compares against the same owner read port.
    handoff = issued["record"]
    check(bool(handoff) and handoff.get("handoff_id") == issued["handoff_id"],
          "the owner persisted the handoff and reads it back", issued["handoff_id"])
    if not handoff:
        # Every later section reads this record; stop here rather than raise a secondary
        # error out of an empty value.  The FAIL above already makes the exit non-zero.
        return

    consumer = MemoryAdmissionExperienceConsumer(
        port, enabled=True, handoff_verifier=owner.verifier)
    check(consumer.describe().get("source_verified") is True,
          "the consumer is bound to the owner's persisted read port", consumer.describe())

    print("[1] a verified outcome lands in M0")
    first = consumer.receive_experience_handoff(dict(handoff))
    check(first.get("disposition") == "ACCEPT", "handoff accepted", first)
    rows = store.recent_verified_outcomes(memory_owner=subject, limit=10)
    check(len(rows) == 1, "exactly one M0 row", len(rows))
    check(rows and rows[0]["source_origin"] == LIFE_ORIGIN,
          "it carries the non-chat origin", rows[0]["source_origin"] if rows else None)
    check(rows and rows[0]["speaker"] == "chiyo" and rows[0]["delivery_status"] == "VERIFIED",
          "its provenance triple is the verified one",
          (rows[0]["speaker"], rows[0]["delivery_status"]) if rows else None)
    check(rows and rows[0]["conversation_id"] == life_conversation_id(subject),
          "it is not written into a chat conversation",
          rows[0]["conversation_id"] if rows else None)
    refs = {item["kind"]: item["id"] for item in (rows[0]["source_refs"] if rows else [])}
    check(refs.get("activity") == "actv_test_1" and refs.get("artifact"),
          "it is traceable to the activity and the work", refs)

    print("[1b] the same door refuses what the owner did not persist")
    unknown = consumer.receive_experience_handoff(
        dict(handoff, handoff_id="lexp:0123456789abcdef"))
    tampered = dict(handoff)
    tampered["action_refs"] = list(tampered.get("action_refs") or []) + ["ar0act:forged"]
    tampered_out = consumer.receive_experience_handoff(tampered)
    shape_only = dict(handoff)
    shape_only.pop("activity_ref", None)
    shape_out = consumer.receive_experience_handoff(shape_only)
    blind = MemoryAdmissionExperienceConsumer(port, enabled=True)
    blind_out = blind.receive_experience_handoff(dict(handoff))

    def _unreadable(_handoff_id):
        raise OSError("owner store unreadable")

    unreadable = MemoryAdmissionExperienceConsumer(
        port, enabled=True, handoff_verifier=_unreadable)
    unreadable_out = unreadable.receive_experience_handoff(dict(handoff))
    check(unknown.get("reason") == "HANDOFF_SOURCE_NOT_FOUND",
          "an id the owner never wrote is not found", unknown)
    check(tampered_out.get("reason") == "HANDOFF_SOURCE_MISMATCH",
          "a copied id with a changed ref is a mismatch", tampered_out)
    check(shape_out.get("reason") == "LIFE_ACTIVITY_REF_MISSING",
          "a handoff without an Activity anchor is refused first", shape_out)
    check(blind_out.get("reason") == "HANDOFF_SOURCE_UNVERIFIED",
          "an unbound consumer refuses instead of assuming provenance", blind_out)
    check(unreadable_out.get("reason") == "HANDOFF_SOURCE_UNREADABLE",
          "a read failure is a refusal, not an assumption", unreadable_out)
    check(all(str(item.get("disposition") or "") == "IGNORE" for item in
              (unknown, tampered_out, shape_out, blind_out, unreadable_out)),
          "none of the five refusals has a disposition that could reach M0")
    check(len(store.recent_verified_outcomes(memory_owner=subject, limit=10)) == 1,
          "the refusals added no M0 row")

    print("[2] the same outcome delivered twice is not two experiences")
    again = consumer.receive_experience_handoff(dict(handoff))
    check(again.get("disposition") == "DUPLICATE", "second delivery is a duplicate", again)
    rows = store.recent_verified_outcomes(memory_owner=subject, limit=10)
    check(len(rows) == 1, "still exactly one M0 row", len(rows))

    print("[3] the read view returns it, bounded and newest first")
    view = port.recent(limit=5)
    check(len(view) == 1, "one experience visible", len(view))
    check(view and view[0].activity_ref == "actv_test_1",
          "the view carries the activity reference", view[0].to_dict() if view else None)
    check(port.recent(limit=0) == [], "limit 0 returns nothing")
    prompt_view = port.prompt_view()
    check(prompt_view and prompt_view[0].get("experience_id"),
          "the model-facing view is attributable", prompt_view)

    print("[4] a Memory failure defers instead of rolling back")
    # A second, genuinely owner-issued handoff: the M0 write fails, so it must DEFER rather
    # than be refused as unverified (that would be a different fact).
    owner.register_activity("actv_test_2", revision=1, activity_kind="WRITE")
    owner.open_integration(
        integration_id="lint:memorycheck0002",
        correlation_id="corr:memorycheck0002")
    second = owner.emit(
        integration_id="lint:memorycheck0002",
        activity_id="actv_test_2",
        kind=ag2.HANDOFF_KIND_COMPLETED,
        occurred_at="2026-10-10T20:05:00+08:00")
    check(bool(second.get("record")), "the second handoff is owner-persisted too",
          second.get("handoff_id"))
    blocked_parent = tmp_path / "not-a-directory"
    blocked_parent.write_text("this is a file, so no database can live under it\n")
    broken = MemoryAdmissionExperienceConsumer(
        LifeExperiencePort(blocked_parent / "x.sqlite", subject=subject),
        enabled=True, handoff_verifier=owner.verifier)
    deferred = broken.receive_experience_handoff(dict(second["record"]))
    check(deferred.get("disposition") == "DEFER", "write failure defers the handoff", deferred)
    check(deferred.get("reason") == "MEMORY_WRITE_FAILED",
          "the defer is the write failure, not a source refusal", deferred)

    print("[5] a stop-recall / delete removes it from the view")
    if not rows:
        # Never index an empty result: a failed section above is reported where it happened,
        # and this dependent section says why it did not run.
        check(False, "the delete section needs an admitted row from section [1]",
              "no row to delete")
        return
    ledger.apply(operation_id="user-control:test-1", kind="DELETE",
                 target=str(rows[0]["event_id"]), actor=subject)
    after_delete = port.recent(limit=5)
    check(after_delete == [], "the blocked experience no longer reaches the view",
          [r.to_dict() for r in after_delete])
    still_there = store.recent_verified_outcomes(memory_owner=subject, limit=10)
    check(len(still_there) == 1, "the M0 evidence itself is untouched (append-only)",
          len(still_there))

    print("[6] governance that cannot be established fails closed")
    evidence_db = tmp_path / "evidence.sqlite"
    blind_port = LifeExperiencePort(evidence_db, controls_db=tmp_path / "missing.sqlite",
                                    subject=subject)
    check(blind_port.recent(limit=5) == [], "unreadable control ledger -> empty view",
          blind_port.recent(limit=5))

    print("[7] the injection boundary (A/B) is a provider, not a deletion")
    from model_continuation_writer import ModelContinuationWriter
    from life_artifact_port import ArtifactResumePoint

    class _FakeClient:
        def complete(self, **kwargs):
            self.last = kwargs
            return type("R", (), {"text": '{"kind":"append_paragraph","text":"下一行。"}'})()

    point = ArtifactResumePoint(artifact_id="artifact:abc", title="夜里的手记",
                                head_version_id="artifact-version:v1", version_number=1,
                                content="# 夜里的手记\n\n第一行。\n", lifecycle="ACTIVE",
                                revision=1)
    with_memory = ModelContinuationWriter(_FakeClient(), experience_provider=lambda: [
        {"experience_id": "ev-1", "text": "上次对结尾的措辞不满意", "artifact_ref": "artifact:abc"}])
    with_memory.next_content(point, observed_at="2026-10-10T20:10:00+08:00")
    check(with_memory.last_experience_ids == ["ev-1"],
          "group A: the admitted experience reaches the content prompt",
          with_memory.last_experience_ids)

    without_memory = ModelContinuationWriter(_FakeClient(), experience_provider=lambda: [])
    without_memory.next_content(point, observed_at="2026-10-10T20:10:00+08:00")
    check(without_memory.last_experience_ids == [],
          "group B: the same writer with an empty view injects nothing",
          without_memory.last_experience_ids)
    check(len(store.recent_verified_outcomes(memory_owner=subject, limit=10)) == 1,
          "group B changed no stored memory", "still one row")

    print("[8] disabled admission is inert")
    off = MemoryAdmissionExperienceConsumer(port, enabled=False)
    result = off.receive_experience_handoff(dict(handoff))
    check(result.get("disposition") == "IGNORE", "disabled consumer ignores", result)
    check(len(store.recent_verified_outcomes(memory_owner=subject, limit=10)) == 1,
          "disabled consumer wrote nothing", "still one row")


def main() -> int:
    from app.evidence import SCHEMA_VERSION, EvidenceStore
    from life_experience_port import LifeExperiencePort, life_conversation_id
    from memory_control import MemoryControlLedger
    from memory_owner_handoff import MemoryOwnerHandoff

    subject = "chiyo"
    with tempfile.TemporaryDirectory(prefix="v02-memory-") as tmp:
        tmp_path = Path(tmp)
        evidence_db = tmp_path / "evidence.sqlite"
        controls_db = tmp_path / "controls.sqlite"
        os.chmod(tmp_path, 0o700)

        store = EvidenceStore(evidence_db)
        check(store.verify().get("issues") == [], "fresh M0 database verifies clean",
              store.verify())
        version = sqlite3.connect(evidence_db).execute(
            "SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        check(version == SCHEMA_VERSION, "schema is at the current version", version)

        # A control ledger must exist before the projection can be healthy.
        ledger = MemoryControlLedger(controls_db, evidence_db, owner=subject,
                                     conversation=life_conversation_id(subject))
        ledger.initialize()

        port = LifeExperiencePort(evidence_db, controls_db=controls_db, subject=subject)
        owner = MemoryOwnerHandoff(tmp_path / "ag2-owner")
        try:
            _drive(store, ledger, port, subject, owner, tmp_path)
        finally:
            owner.close()

    print()
    if FAILURES:
        print(f"MEMORY_CHECK: FAIL ({len(FAILURES)} assertion(s))")
        for item in FAILURES:
            print("  -", item)
        return 1
    print("MEMORY_CHECK: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

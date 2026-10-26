"""Three-fact demonstration of the retained deletion authority (V-C).

Runs on a brand-new isolated Memory (real M0 ``EvidenceStore`` + real control ledger), never a
live instance, never a model, never network. The business files sit under ``memory/`` (the
instance's own backup/restore set); the deletion authority is kept in a separate directory
outside it and is only read, never rewritten by the restore. It shows:

1. restoring only the evidence body after a deletion still withholds it;
2. restoring the evidence body *and* the control ledger together still withholds it, because the
   separately retained deletion authority keeps the decision -- and the restore did not touch it;
3. an ordinary experience created after the deletion is still readable (no governance rule is
   weakened to make that true: it is simply newer than the deletion epoch);
4. the normal DELETE flow itself advances the retained trusted floor (a separate handoff file in
   the retained directory) — no operator-edited number is used here;
5. currentness is separate from the hash chain: a chain-valid older copy below that floor, or a
   copy with no handoff at all, blocks instead of being trusted;
6. when the authority file is not available, reads block with a named reason and a new DELETE is
   refused (it never reports success).

Exit code 0 only when every expectation holds. Nothing is printed beyond short result lines.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "components" / "life" / "scripts",
              ROOT / "components" / "native",
              ROOT / "components" / "native" / "src",
              ROOT / "components" / "native" / "memory_runtime_v1"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from app.evidence import EvidenceStore  # noqa: E402
from life_experience_port import (  # noqa: E402
    LifeExperiencePort,
    MemoryReadUnavailable,
    build_outcome_event,
    life_conversation_id,
)
from life_memory_service import memory_service_from_env  # noqa: E402
from memory_authority import MemoryAuthorityUnavailable, MemoryDeletionAuthority  # noqa: E402
from memory_control import MemoryControlLedger  # noqa: E402

OWNER = "chiyo"
CONVERSATION = life_conversation_id(OWNER)
FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        FAILURES.append("%s%s" % (name, (": " + detail) if detail else ""))
    print("%s %s%s" % ("PASS" if condition else "FAIL", name,
                       ("" if not detail else " — " + detail)))


def append_event(store: EvidenceStore, text: str, source: str, when: datetime) -> str:
    event = build_outcome_event(subject=OWNER, source_ref=source, text=text,
                                occurred_at=when.isoformat())
    store.append(event)
    return event.event_id


def texts(port: LifeExperiencePort) -> list[str]:
    return [record.text for record in port.search(query="", limit=20)]


def main() -> int:
    base = Path(tempfile.mkdtemp(prefix="nyairo-vc-recovery-"))
    # The instance's own backup/restore set: restoring this directory replaces these two files.
    business = base / "memory"
    business.mkdir(parents=True)
    evidence = business / "evidence.db"
    controls = business / "controls.db"
    # The retained deletion authority: a separate directory outside the business root.
    retained = base / "retained"
    retained.mkdir(parents=True)
    authority = retained / "authority.db"

    store = EvidenceStore(evidence)
    now = datetime.now(timezone.utc)
    removed_id = append_event(store, "隔离演示：这条经历将被删除。", "demo:run:delete", now)
    MemoryControlLedger(controls, evidence, owner=OWNER,
                        conversation=CONVERSATION).initialize()
    # A genuinely new empty base: initialization retains floor 0 by itself.  From here on the
    # normal DELETE flow advances it; nothing below hand-sets a minimum sequence.
    MemoryDeletionAuthority(authority, owner=OWNER,
                            conversation=CONVERSATION).initialize()
    floor_file = Path(str(authority) + ".floor.json")

    ledger = MemoryControlLedger(controls, evidence, owner=OWNER,
                                 conversation=CONVERSATION, authority=authority)
    port = LifeExperiencePort(evidence, controls_db=controls, subject=OWNER,
                              authority_db=authority)

    # Snapshot the two rollback-able business files *before* the deletion, exactly like a
    # backup pair.  The authority is outside this pair and is not part of the snapshot.
    evidence_before = business / "evidence.before.db"
    controls_before = business / "controls.before.db"
    shutil.copy2(evidence, evidence_before)
    shutil.copy2(controls, controls_before)
    authority_cold = retained / "authority.cold.db"       # chain-valid copy from before DELETE
    shutil.copy2(authority, authority_cold)

    check("基线：未删经历可读", texts(port) == ["隔离演示：这条经历将被删除。"],
          repr(texts(port)))
    status = ledger.apply(operation_id="demo-delete-1", kind="DELETE", target=removed_id,
                          actor=OWNER)
    check("删除提交到底账后生效", status == "APPLIED" and not texts(port),
          "status=%s" % (status,))
    # The normal flow retained the new floor beside the authority (separate handoff file), with
    # no number supplied by this demo.
    floor_after_delete = json.loads(floor_file.read_text(encoding="utf-8"))
    check("正常 DELETE 后受信下限由正规流程独立推进", floor_after_delete["seq"] == 1,
          "floor_seq=%s" % (floor_after_delete.get("seq"),))

    # 1. Only the evidence body is rolled back; the control ledger stays current.
    shutil.copy2(evidence_before, evidence)
    check("只恢复正文：已删经历仍被扣留",
          not any("将被删除" in text for text in texts(port)), repr(texts(port)))

    # 2. Evidence *and* control ledger are rolled back together (authority untouched).
    authority_digest = authority.read_bytes()
    shutil.copy2(controls_before, controls)
    rows = texts(port)
    check("正文与 controls 同恢复：底账保留最新删除，仍扣留且底账本人未被改写",
          not any("将被删除" in text for text in rows)
          and authority.read_bytes() == authority_digest,
          repr(rows))

    # Ordinary read: a *new* experience after the deletion is newer than the deletion epoch, so
    # it is readable under the original governance rule -- nothing is exempted for it.
    kept_id = append_event(EvidenceStore(evidence), "隔离演示：删除之后写下的普通经历。",
                           "demo:run:keep", datetime.now(timezone.utc))
    rows = texts(port)
    check("正常原可读记录仍读：删除之后的新经历可读，已删经历仍扣留",
          any("删除之后写下" in text for text in rows)
          and not any("将被删除" in text for text in rows)
          and port.read(kept_id) is not None,
          repr(rows))

    # 3. Currentness is not integrity.  Restoring the *pre-delete authority database* over the
    #    retained one (its chain is valid, but it is below the floor the DELETE advanced) blocks;
    #    so does a copy that carries no handoff at all.  The demo itself never supplies a number.
    cold_current = retained / "authority.current.db"
    shutil.copy2(authority, cold_current)
    shutil.copy2(authority_cold, authority)          # old db back in place; floor file untouched
    unaudited = retained / "unaudited"
    unaudited.mkdir()
    no_handoff = unaudited / "authority.db"
    shutil.copy2(cold_current, no_handoff)           # current db, but no retained floor beside it
    blocked: dict[str, str] = {}
    for label, candidate in (
            ("older", LifeExperiencePort(evidence, controls_db=controls, subject=OWNER,
                                         authority_db=authority)),
            ("unproven", LifeExperiencePort(evidence, controls_db=controls, subject=OWNER,
                                            authority_db=no_handoff))):
        try:
            candidate.search(query="")
            blocked[label] = "READ_ALLOWED"
        except MemoryReadUnavailable as exc:
            blocked[label] = exc.code
    check("当前性不可证：旧副本与无交接副本均具名阻断",
          blocked["older"] == "MEMORY_AUTHORITY_OLDER_THAN_TRUSTED"
          and blocked["unproven"] == "MEMORY_AUTHORITY_CURRENTNESS_UNPROVEN",
          "%s / %s" % (blocked["older"], blocked["unproven"]))
    shutil.copy2(cold_current, authority)            # restore the current base for later steps

    # 4. The authority cannot be obtained: reads block by name and DELETE must not succeed.
    service = memory_service_from_env({
        "NYA02_MEMORY_DB": str(evidence),
        "NYA02_MEMORY_CONTROLS_DB": str(controls),
        "NYA02_MEMORY_AUTHORITY_DB": str(authority),
        "NYA02_MEMORY_AUTHORITY_MIN_SEQ": "0",
        "NYA02_MEMORY_SUBJECT": OWNER,
    })
    authority_missing = retained / "authority.missing.db"
    shutil.move(str(authority), str(authority_missing))
    try:
        texts(port)
        read_blocked = False
        read_code = ""
    except MemoryReadUnavailable as exc:
        read_blocked = True
        read_code = exc.code
    connection = sqlite3.connect(controls)
    try:
        controls_rows_before = connection.execute(
            "SELECT count(*) FROM memory_controls").fetchone()[0]
    finally:
        connection.close()
    try:
        ledger.apply(operation_id="demo-delete-2", kind="DELETE", target=kept_id, actor=OWNER)
        delete_refused = False
        delete_code = "REPORTED_SUCCESS"
    except MemoryAuthorityUnavailable as exc:
        delete_refused = True
        delete_code = exc.code
    connection = sqlite3.connect(controls)
    try:
        controls_rows_after = connection.execute(
            "SELECT count(*) FROM memory_controls").fetchone()[0]
    finally:
        connection.close()
    check("底账不可得：读取具名阻断", read_blocked and read_code.startswith("MEMORY_AUTHORITY_"),
          read_code)
    listed = service.list_experiences()
    contextual = service.context()
    check("底账不可得：list/context 具名阻断",
          listed["status"] == "BLOCKED" and contextual["status"] == "BLOCKED"
          and str(listed["reason"]).startswith("MEMORY_AUTHORITY_")
          and str(contextual["reason"]).startswith("MEMORY_AUTHORITY_"),
          "%s / %s" % (listed["reason"], contextual["reason"]))
    check("底账不可得：DELETE 不成功且不写 controls",
          delete_refused and controls_rows_after == controls_rows_before,
          "%s rows=%s->%s" % (delete_code, controls_rows_before, controls_rows_after))

    shutil.rmtree(base, ignore_errors=True)
    if FAILURES:
        print("FAILURES: %d" % (len(FAILURES),))
        return 1
    print("ALL_EXPECTATIONS_MET")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

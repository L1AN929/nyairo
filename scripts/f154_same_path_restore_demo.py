#!/usr/bin/env python3
"""F-154 scenario 9: an older backup restored onto the *same* recovery path.

The activation gate used to bind a **path**.  Stopping the service and copying the pre-deletion
evidence/controls/Supply databases back over those exact paths therefore kept every path equal
while putting the forgotten body back -- and the marker still said OPEN.

This demo walks that one real recovery usage on a fresh synthetic root:

1. real Memory (evidence/controls/authority) + real Supply owner over its socket; a forget request
   through the formal CLI erases the body in the live copy;
2. formal ``recover`` sweeps *equivalent pre-deletion backups* copied into a recovery directory and
   activates them (the body is erased there too);
3. the service is stopped and each of the three databases is replaced, at the **same path**, by
   the pre-deletion backup: public read and new writes are refused by name, no body is returned;
4. a second formal ``recover`` re-stamps the same copy, the de-forgotten body stays unreadable,
   unrelated work and normal new writes work again.

Boolean/status only: no body, prompt or secret is printed.  Nothing is deleted; the synthetic root
and every backup stay on disk.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Reuse the *helpers* of the older demo (real launcher, owner call, CLI wrapper).  None of its
# 67-case narrative is re-run here.
from forget_generation_demo import (  # noqa: E402
    CONVERSATION, OWNER, ROOT, SENTINEL_UNRELATED, SupplyRuntime, append_event, check,
    copy_db, count_rows, issue_grant, memory_service_from_env, owner_call, payload_present,
    provision_workspace, run_cli, sentinel_in_file, start_supply_service, stop_supply_service,
)
from app.evidence import EvidenceStore  # noqa: E402
from forget_request import RECOVERY_STATE_FILE  # noqa: E402
from life_artifact_port import LifeArtifactPort  # noqa: E402
from life_experience_port import build_outcome_event  # noqa: E402
from lifesupply.artifact.store import ManagedArtifactStore  # noqa: E402
from memory_authority import MemoryDeletionAuthority  # noqa: E402
from memory_control import MemoryControlLedger  # noqa: E402

SENTINEL_M0 = "隔离演示：同路径恢复必须仍然忘记的阅读经历。"
SENTINEL_READING = "隔离演示：同路径恢复后仍不可读的正文。"


def _service_status(environment: dict) -> dict:
    service = memory_service_from_env(dict(environment))
    try:
        return dict(service.status() or {})
    finally:
        close = getattr(service, "close", None)
        if close is not None:
            close()


def _version_body_chars(path: Path, version_id: str) -> int:
    """Boolean read of one stored version body length (0 means the body is gone)."""

    connection = sqlite3.connect(str(path))
    try:
        row = connection.execute(
            "SELECT COALESCE(length(content), 0) FROM artifact_versions WHERE version_id=?",
            (str(version_id),)).fetchone()
        return int(row[0]) if row else 0
    finally:
        connection.close()


def _table_rows(path: Path, table: str) -> int:
    connection = sqlite3.connect(str(path))
    try:
        return int(connection.execute("SELECT count(*) FROM %s" % table).fetchone()[0])
    finally:
        connection.close()


def _read_forgotten(environment: dict, event_id: str) -> dict:
    service = memory_service_from_env(dict(environment))
    try:
        answer = service.read_experience(event_id)
        listed = service.list_experiences()
        return {"answer": answer, "listed": listed, "status": service.status()}
    finally:
        close = getattr(service, "close", None)
        if close is not None:
            close()


def _with_supply_service(recovered_env: dict, data_root: Path, tag: str, action):
    """One real launcher/real socket turn on a recovered copy; always stops the launcher."""

    socket_path = Path("/tmp") / ("f154-samepath-%d-%s.sock" % (os.getpid(), tag))
    run_env = dict(recovered_env)
    run_env.update({
        "LIFE_SUPPLY_SOCKET": str(socket_path),
        "LIFE_SUPPLY_DATA_ROOT": str(data_root),
        "LIFE_SUPPLY_OPERATOR_UIDS": str(os.geteuid()),
        "LIFE_SUPPLY_SERVICE_UIDS": str(os.geteuid()),
        "LIFE_SUPPLY_WRITER_INSTANCE": "f154-samepath-%s" % tag,
        "LIFE_SUPPLY_REQUIRE_NON_ROOT": "OFF",
        "LIFE_SUPPLY_SWITCH_ARTIFACT_EXTERNAL_ACTION": "ON",
    })
    process = start_supply_service(run_env)
    try:
        return action(socket_path)
    finally:
        stop_supply_service(process)


def _supply_grant(socket_path: Path, *, operation_id: str, workspace_id: str) -> str:
    answer = owner_call(socket_path, {"op": "issue_grant", "grant": {
        "subject": OWNER, "capability": "COMMIT_MANAGED_ARTIFACT",
        "scope": "artifact:personal", "target": "workspace:%s" % workspace_id,
        "authority_scope": "life-supply.artifact.v1", "use_mode": "REUSABLE",
        "operation_id": operation_id}})
    return str(answer.get("grant_id") or "") if answer.get("status") == "ALLOW" else ""


def main() -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = Path(os.environ.get("FORGET_DEMO_ROOT") or
                (ROOT / ".agents" / "worker-runs" /
                 ("f154-same-path-%s-%d" % (stamp, os.getpid()))))
    instance = base / "instance"
    business = base / "memory"
    retained = base / "retained"
    coordination = base / "forget"
    supply_root = base / "supply"
    for directory in (instance, business, retained, coordination, supply_root):
        directory.mkdir(parents=True, exist_ok=True)
    evidence = business / "evidence.db"
    controls = business / "controls.db"
    authority = retained / "authority.db"
    cases = coordination / "cases.db"
    store = EvidenceStore(evidence)
    MemoryControlLedger(controls, evidence, owner=OWNER,
                        conversation=CONVERSATION).initialize()
    MemoryDeletionAuthority(authority, owner=OWNER, conversation=CONVERSATION).initialize()

    supply_db = supply_root / "life_supply.sqlite"
    projection_root = supply_root / "projection"
    environment = {
        "NYA02_MEMORY_DB": str(evidence),
        "NYA02_MEMORY_CONTROLS_DB": str(controls),
        "NYA02_MEMORY_AUTHORITY_DB": str(authority),
        "NYA02_MEMORY_SUBJECT": OWNER,
        "NYA02_FORGET_CASE_DB": str(cases),
        "NYA02_SUPPLY_ARTIFACT_DB": str(supply_db),
        "NYA02_SUPPLY_PROJECTION_ROOT": str(projection_root),
    }
    nested = instance / "one-day"
    nested.mkdir()
    (nested / "instance.json").write_text(
        json.dumps({"schema_version": "life.one_day.v1", "environment": environment},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(nested / "instance.json", 0o600)

    memory = memory_service_from_env(environment)
    runtime = SupplyRuntime(supply_root)
    try:
        workspace_id = provision_workspace(runtime)
        artifact_grant = issue_grant(
            runtime, capability="COMMIT_MANAGED_ARTIFACT", scope="artifact:personal",
            target="workspace:%s" % workspace_id, authority_scope="life-supply.artifact.v1",
            use_mode="REUSABLE", operation_id="issue:artifact:%s" % OWNER)
        port = runtime.port()
        reading = port.create_artifact(
            workspace_id=workspace_id, title="隔离演示阅读材料", content=SENTINEL_READING,
            grant_id=artifact_grant, operation_key="samepath:reading:v1")
        if not reading.applied:
            raise AssertionError("reading refused: %s/%s" % (reading.status, reading.reason))
        reading_id, reading_version = str(reading.artifact_id), str(reading.version_id or "")
        unrelated = port.create_artifact(
            workspace_id=workspace_id, title="隔离演示无关作品", content=SENTINEL_UNRELATED,
            grant_id=artifact_grant, operation_key="samepath:unrelated:v1")
        if not unrelated.applied:
            raise AssertionError("unrelated refused: %s/%s" % (unrelated.status, unrelated.reason))
        unrelated_id, unrelated_version = str(unrelated.artifact_id), str(unrelated.version_id or "")
        forgotten_id = append_event(store, SENTINEL_M0, "reading:artifact:samepath:1",
                                    refs=[("artifact", reading_id),
                                          ("artifactversion", reading_version)])
        kept_id = append_event(store, "隔离演示：与删除无关的保留经历。",
                               "practice:samepath:keep")
    finally:
        runtime.stop()

    # -- pre-deletion backups, kept for the same-path restore -------------------------------
    before = base / "before"
    before.mkdir(parents=True, exist_ok=True)
    evidence_before = before / "evidence.db"
    controls_before = before / "controls.db"
    authority_before = before / "authority.db"
    supply_before = before / "life_supply.sqlite"
    for source, target in ((evidence, evidence_before), (controls, controls_before),
                           (authority, authority_before), (supply_db, supply_before)):
        copy_db(source, target)

    hold_rc, hold = run_cli(instance, "request", "--operation-key", "op-samepath",
                            "--source-ref", "reading:artifact:samepath:1", "--hold",
                            "--retention-pending")
    check("正式忘记请求：来源扣留", hold_rc == 0 and hold.get("status") == "HELD", str(hold)[:160])
    confirm_rc, confirmed = run_cli(instance, "confirm", "--case", hold["case_id"])
    # The pre-deletion copies above are a real retained backup.  The formal request-time
    # ``--retention-pending`` is how the operator reports it; ``COMPLETE`` while a copy still
    # carries the body would be swallowing that fact.
    check("主 case：备份仍在 → WAITING_RETENTION，不报 COMPLETE",
          confirm_rc == 0 and confirmed.get("status") == "WAITING_RETENTION"
          and confirmed.get("reason") == "FORGET_OFFLINE_BACKUP_RETAINED",
          str(confirmed)[:200])
    local_erasure_complete = (
        not payload_present(evidence, forgotten_id)
        and not sentinel_in_file(evidence, SENTINEL_M0)
        and _version_body_chars(supply_db, reading_version) == 0)
    check("local_erasure_complete：当前受控存储已擦，与 case 保留状态各自独立",
          local_erasure_complete)
    check("删除后：主库正文已擦、无关经历保留",
          not sentinel_in_file(evidence, SENTINEL_M0) and payload_present(evidence, kept_id))

    # -- formal recover of an equivalent pre-deletion copy ----------------------------------
    recovery_dir = base / "recovery"
    recovered_data = recovery_dir / "supply"
    recovered_data.mkdir(parents=True, exist_ok=True)
    recovered_evidence = recovery_dir / "evidence.db"
    recovered_controls = recovery_dir / "controls.db"
    recovered_supply_db = recovered_data / "life_supply.sqlite"
    recovered_projection = recovered_data / "projection"
    copy_db(evidence_before, recovered_evidence)
    copy_db(controls_before, recovered_controls)
    copy_db(supply_before, recovered_supply_db)

    recovered_env = dict(environment)
    recovered_env.update({
        "NYA02_MEMORY_DB": str(recovered_evidence),
        "NYA02_MEMORY_CONTROLS_DB": str(recovered_controls),
        "NYA02_F154_RECOVERY_DIR": str(recovery_dir),
        "NYA02_SUPPLY_ARTIFACT_DB": str(recovered_supply_db),
        "NYA02_SUPPLY_PROJECTION_ROOT": str(recovered_projection),
    })
    marker_path = recovery_dir / RECOVERY_STATE_FILE
    rc, first = run_cli(instance, "recover", "--recovery-dir", str(recovery_dir),
                        "--recovered-evidence", str(recovered_evidence),
                        "--recovered-controls", str(recovered_controls),
                        "--recovered-supply", str(recovered_supply_db),
                        "--recovered-projection", str(recovered_projection))
    marker = json.loads(marker_path.read_text(encoding="utf-8")) if marker_path.is_file() else {}
    check("正式 recover：激活并绑定三个库各自的代际",
          rc == 0 and first.get("status") == "RECOVERED_OPEN"
          and set(marker.get("incarnations") or {}) == {"evidence", "controls", "supply"},
          "rc=%d status=%s marker=%s" % (rc, first.get("status"),
                                         sorted((marker.get("incarnations") or {}))))
    check("恢复副本：被忘正文不可读、无关内容可读",
          _read_forgotten(recovered_env, forgotten_id)["answer"].get("status") != "READY"
          and not sentinel_in_file(recovered_evidence, SENTINEL_M0)
          and _service_status(recovered_env).get("status") == "READY")

    # Post-activation copies: what a *correct* same-path restore looks like (same generation).
    swept = base / "swept"
    swept.mkdir(parents=True, exist_ok=True)
    swept_evidence = swept / "evidence.db"
    swept_controls = swept / "controls.db"
    swept_supply = swept / "life_supply.sqlite"
    for source, target in ((recovered_evidence, swept_evidence),
                           (recovered_controls, swept_controls),
                           (recovered_supply_db, swept_supply)):
        copy_db(source, target)

    # -- the gap: older backups restored onto the same paths --------------------------------
    halves = (
        ("evidence", evidence_before, recovered_evidence, swept_evidence),
        ("controls", controls_before, recovered_controls, swept_controls),
        ("supply", supply_before, recovered_supply_db, swept_supply),
    )
    for index, (kind, old_copy, target, good_copy) in enumerate(halves):
        copy_db(old_copy, target)          # same path, pre-deletion content, no incarnation
        if kind in ("evidence", "controls"):
            status = _service_status(recovered_env)
            refused = (status.get("status") in ("BLOCKED", "UNAVAILABLE")
                       and any(code in json.dumps(status) for code in ("RECOVERY_INCARNATION_MISMATCH", "RECOVERY_INCARNATION_MISSING")))
            service = memory_service_from_env(dict(recovered_env))
            try:
                write_channel = getattr(service, "port", None)
            finally:
                close = getattr(service, "close", None)
                if close is not None:
                    close()
            # Reads and new writes share one port; a blocked binding has no write channel at all.
            check("同路径回填旧 %s：公开读具名拒绝，新写无通道" % kind,
                  bool(refused) and write_channel is None)
        else:
            before_versions = _table_rows(recovered_supply_db, "artifact_versions")
            before_edges = _table_rows(recovered_supply_db, "artifact_source_edges")

            def supply_action(socket_path, index=index):
                port = LifeArtifactPort(str(socket_path), OWNER)
                try:
                    port.read_version(unrelated_id, unrelated_version)
                    read_answer = "ALLOWED"
                except Exception as exc:
                    read_answer = "%s/%s" % (getattr(exc, "status", ""),
                                             getattr(exc, "reason", ""))
                grant_id = _supply_grant(socket_path,
                                         operation_id="samepath:swap:grant:%d" % index,
                                         workspace_id=workspace_id)
                outcome = None
                if grant_id:
                    outcome = port.create_artifact(
                        workspace_id=workspace_id,
                        title="隔离演示：旧备份上的新写必须被拒",
                        content="隔离演示：同路径旧备份不允许新写。",
                        grant_id=grant_id,
                        operation_key="samepath:swap:write:%d" % index)
                return {"read": read_answer, "grant": bool(grant_id),
                        "write": "%s/%s" % (getattr(outcome, "status", ""),
                                            getattr(outcome, "reason", ""))}

            details = _with_supply_service(recovered_env, recovered_data,
                                           "swap-%d" % index, supply_action)
            after_versions = _table_rows(recovered_supply_db, "artifact_versions")
            after_edges = _table_rows(recovered_supply_db, "artifact_source_edges")
            refused = ("RECOVERY_INCARNATION_" in details["read"]
                       and details["grant"]
                       and details["write"].startswith("BLOCKED")
                       and "RECOVERY_INCARNATION_" in details["write"]
                       and after_versions == before_versions
                       and after_edges == before_edges)
            check("同路径回填旧 supply：正式读与合法 grant 的新写都按代际拒绝，零版本/边",
                  bool(refused), str(details)[:200])
        copy_db(good_copy, target)         # the swept copy again: same generation, same path
        if kind == "supply":
            def supply_read(socket_path):
                answer = LifeArtifactPort(str(socket_path), OWNER).read_version(
                    unrelated_id, unrelated_version)
                return answer.get("content") == SENTINEL_UNRELATED
            check("同路径换回被扫副本（supply）：代际相符、无关作品正文可读",
                  _with_supply_service(recovered_env, recovered_data,
                                       "good-%d" % index, supply_read))
        else:
            check("同路径换回被扫副本（%s）：代际相符、重新可用" % kind,
                  _service_status(recovered_env).get("status") == "READY")

    # Body is still not served even while the file carries the de-forgotten rows: every half above
    # was refused *before* a body was opened.
    copy_db(evidence_before, recovered_evidence)
    blocked_service = memory_service_from_env(dict(recovered_env))
    try:
        blocked_status = blocked_service.status()
        blocked_port = getattr(blocked_service, "port", None)
    finally:
        close = getattr(blocked_service, "close", None)
        if close is not None:
            close()
    check("回填正文仍在文件中，但读取入口先拒代际（不先读正文再判），新写同样无通道",
          sentinel_in_file(recovered_evidence, SENTINEL_M0)
          and blocked_status.get("status") in ("BLOCKED", "UNAVAILABLE")
          and blocked_port is None)

    # -- a second formal recover over *all three* old copies --------------------------------
    # The first sweep left controls/supply at the swept generation; restoring only evidence
    # would not prove the re-sweep cleans every half.  Put all three pre-deletion copies back
    # at their paths, keep the old OPEN marker and the current authority, then run the formal
    # recover again.
    copy_db(evidence_before, recovered_evidence)
    copy_db(controls_before, recovered_controls)
    copy_db(supply_before, recovered_supply_db)
    rc2, second = run_cli(instance, "recover", "--recovery-dir", str(recovery_dir),
                          "--recovered-evidence", str(recovered_evidence),
                          "--recovered-controls", str(recovered_controls),
                          "--recovered-supply", str(recovered_supply_db),
                          "--recovered-projection", str(recovered_projection))
    marker2 = json.loads(marker_path.read_text(encoding="utf-8"))
    check("三库同路径全部回填旧备份后，正式 recover 重扫并换发新代际",
          rc2 == 0 and second.get("status") == "RECOVERED_OPEN"
          and marker2.get("incarnations") != marker.get("incarnations"),
          "rc=%d status=%s" % (rc2, second.get("status")))
    forgotten_after = _read_forgotten(recovered_env, forgotten_id)
    # An existing control/epoche also suppresses *older* self-history as a whole (an existing
    # layer, not part of this erasure), so "unrelated kept" is proven at the payload level here;
    # a newly written, post-activation experience is read back in the next block.
    check("重扫后：被删 M0 不可读、无关 M0 正文未被擦",
          forgotten_after["answer"].get("status") != "READY"
          and not payload_present(recovered_evidence, forgotten_id)
          and payload_present(recovered_evidence, kept_id)
          and not sentinel_in_file(recovered_evidence, SENTINEL_M0),
          "forgotten=%s fp=%s kp=%s sentinel=%s" % (
              forgotten_after["answer"].get("status"),
              payload_present(recovered_evidence, forgotten_id),
              payload_present(recovered_evidence, kept_id),
              sentinel_in_file(recovered_evidence, SENTINEL_M0)))

    def after_sweep_read(socket_path):
        port = LifeArtifactPort(str(socket_path), OWNER)
        erased = port.read_version(reading_id, reading_version)
        unrelated = port.read_version(unrelated_id, unrelated_version)
        return {"erased_chars": erased.get("content_chars"),
                "erased_body": erased.get("content") or "",
                "unrelated_ok": (unrelated.get("content") or "") == SENTINEL_UNRELATED}
    swept_read = _with_supply_service(recovered_env, recovered_data,
                                      "after", after_sweep_read)
    check("重扫后：被删作品版本仍在但正文为空，无关作品正文保留",
          not int(swept_read.get("erased_chars") or 0)
          and swept_read.get("erased_body") == ""
          and swept_read.get("unrelated_ok"), str(swept_read)[:160])

    # -- after activation: normal business and governance writes still work -----------------
    service = memory_service_from_env(dict(recovered_env))
    try:
        fresh = build_outcome_event(subject=OWNER, source_ref="life:samepath:after",
                                    text="隔离演示：恢复后正常写下的经历。",
                                    occurred_at=datetime.now(timezone.utc).isoformat())
        written = service.port.append_outcome(fresh)
        fresh_read = service.read_experience(fresh.event_id)
        gov_target = build_outcome_event(
            subject=OWNER, source_ref="life:samepath:governance-target",
            text="隔离演示：恢复后正常治理写的目标。",
            occurred_at=datetime.now(timezone.utc).isoformat())
        gov_written = service.port.append_outcome(gov_target)
        MemoryControlLedger(recovered_controls, recovered_evidence, owner=OWNER,
                            conversation=CONVERSATION, authority="").apply(
            operation_id="samepath:governance:delete:1", kind="DELETE",
            target=gov_target.event_id, actor=OWNER)
        before_reingest = count_rows(recovered_evidence)
        reingested = build_outcome_event(
            subject=OWNER, source_ref="reading:artifact:samepath:1:new",
            text="隔离演示：换 id 的同源重投。",
            occurred_at=datetime.now(timezone.utc).isoformat(),
            refs=[("artifact", reading_id)])
        admission = service.port.append_outcome(reingested)
        rows_after_reingest = count_rows(recovered_evidence)
    finally:
        close = getattr(service, "close", None)
        if close is not None:
            close()
    check("激活后正常业务写：新经历可读",
          written.get("status") in ("inserted", "appended", "APPLIED", "OK")
          and fresh_read.get("status") == "READY")
    check("激活后正常治理写：落库且重开不误报代际错",
          gov_written.get("status") in ("inserted", "appended", "APPLIED", "OK")
          and _service_status(recovered_env).get("status") == "READY")
    check("同已删来源换 event id 重投：写入前拒绝、M0 零增",
          admission.get("status") == "refused"
          and rows_after_reingest == before_reingest,
          "status=%s rows=%d" % (admission.get("status"),
                                 rows_after_reingest - before_reingest))

    # -- fail closed on an old (path-only) marker ------------------------------------------
    saved_marker = marker_path.read_text(encoding="utf-8")
    without = dict(json.loads(saved_marker))
    without.pop("incarnations", None)
    marker_path.write_text(json.dumps(without, sort_keys=True), encoding="utf-8")
    status = _service_status(recovered_env)
    check("旧式无代际 OPEN marker：fail closed，不静默升级",
          status.get("status") in ("BLOCKED", "UNAVAILABLE")
          and "RECOVERY_INCARNATION_UNBOUND" in json.dumps(status))
    marker_path.write_text(saved_marker, encoding="utf-8")
    check("恢复原 marker：再次可用（拒绝是具名的，不是损坏）",
          _service_status(recovered_env).get("status") == "READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

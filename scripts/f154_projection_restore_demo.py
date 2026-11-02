#!/usr/bin/env python3
"""F-154 scenario 9, last derived surface: an old *projection file* put back on its path.

The three databases now carry recovery incarnations, so a pre-deletion copy restored on the same
path is refused by name.  A Markdown projection is a file, not a database: it has no incarnation,
and the projection path only differs from a swept file by its bytes.  This demo walks that one
recovery usage on a fresh synthetic root:

1. real Supply owner + real Memory; one artifact is forgotten through the formal CLI, its
   canonical body and its projection are cleared, and the pre-deletion copies (databases *and*
   projection file) are kept as a retained backup;
2. a formal ``recover`` sweeps an equivalent pre-deletion copy: the restored projection file is
   re-derived from canonical state (empty) instead of being trusted as text;
3. after activation the service is stopped and the pre-deletion projection is copied back over
   the same path.  Restarting the service does not serve the forgotten body: canonical reads are
   empty and the explicit import entry refuses by name before it reads the file;
4. a second formal ``recover`` rebuilds the registered projection from canonical, so the file's
   bytes are actually gone -- the read refusal is not used to claim a file was cleared.  The
   forgotten body stays unreadable, the unrelated artifact keeps its body and projection, a
   normal new version still commits and updates its projection, and re-submitting the erased
   source is refused.

Boolean/status only: no body, prompt or secret is printed.  Nothing is deleted; the synthetic
root, the retained backup and every log stay on disk.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Reuse the helpers of the older demos (real launcher, owner call, CLI wrapper).  None of their
# case narrative is re-run here.
from forget_generation_demo import (  # noqa: E402
    CONVERSATION, FAILURES, OWNER, ROOT, SENTINEL_UNRELATED, SupplyRuntime, append_event, check,
    copy_db, count_rows, issue_grant, memory_service_from_env, payload_present,
    provision_workspace, run_cli, sentinel_in_file,
)
from f154_same_path_restore_demo import _supply_grant, _with_supply_service  # noqa: E402
from app.evidence import EvidenceStore  # noqa: E402
from forget_request import RECOVERY_STATE_FILE  # noqa: E402
from life_artifact_port import LifeArtifactPort  # noqa: E402
from life_experience_port import build_outcome_event  # noqa: E402
from memory_authority import MemoryDeletionAuthority  # noqa: E402
from memory_control import MemoryControlLedger  # noqa: E402

SENTINEL_READING = "隔离演示：被忘记作品的正文，旧投影回填后仍不可读。"
SENTINEL_UNRELATED_V2 = "隔离演示：恢复后正常提交的新版本正文。"


def _binding_rel(db: Path, artifact_id: str) -> str:
    connection = sqlite3.connect(str(db))
    try:
        row = connection.execute(
            "SELECT relative_path FROM artifact_projection_bindings WHERE artifact_id=?",
            (artifact_id,)).fetchone()
    finally:
        connection.close()
    return str(row[0]) if row else ""


def _version_rows(db: Path) -> int:
    connection = sqlite3.connect(str(db))
    try:
        return int(connection.execute(
            "SELECT count(*) FROM artifact_versions").fetchone()[0])
    finally:
        connection.close()


def _version_body_chars(db: Path, version_id: str) -> int:
    connection = sqlite3.connect(str(db))
    try:
        row = connection.execute(
            "SELECT COALESCE(length(content), 0), content_erased FROM artifact_versions"
            " WHERE version_id=?", (version_id,)).fetchone()
        return int(row[0]) if row else 0
    finally:
        connection.close()


def _file_size(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except OSError:
        return -1


def _service_status(environment: dict) -> dict:
    service = memory_service_from_env(dict(environment))
    try:
        return dict(service.status() or {})
    finally:
        close = getattr(service, "close", None)
        if close is not None:
            close()


def _recover(instance: Path, recovery_dir: Path, evidence: Path, controls: Path,
             supply_db: Path, projection: Path) -> tuple[int, dict]:
    return run_cli(instance, "recover", "--recovery-dir", str(recovery_dir),
                   "--recovered-evidence", str(evidence),
                   "--recovered-controls", str(controls),
                   "--recovered-supply", str(supply_db),
                   "--recovered-projection", str(projection))


def main() -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = Path(os.environ.get("FORGET_DEMO_ROOT") or
                (ROOT / ".agents" / "worker-runs" /
                 ("f154-projection-restore-%s-%d" % (stamp, os.getpid()))))
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
            grant_id=artifact_grant, operation_key="projection:reading:v1")
        if not reading.applied:
            raise AssertionError("reading refused: %s/%s" % (reading.status, reading.reason))
        reading_id, reading_version = str(reading.artifact_id), str(reading.version_id or "")
        unrelated = port.create_artifact(
            workspace_id=workspace_id, title="隔离演示无关作品", content=SENTINEL_UNRELATED,
            grant_id=artifact_grant, operation_key="projection:unrelated:v1")
        if not unrelated.applied:
            raise AssertionError("unrelated refused: %s/%s"
                                 % (unrelated.status, unrelated.reason))
        unrelated_id = str(unrelated.artifact_id)
        forgotten_id = append_event(store, "隔离演示：被忘记的阅读经历。",
                                    "reading:artifact:projection:1",
                                    refs=[("artifact", reading_id),
                                          ("artifactversion", reading_version)])
        kept_id = append_event(store, "隔离演示：与删除无关的保留经历。",
                               "practice:projection:keep")
    finally:
        runtime.stop()

    reading_rel = _binding_rel(supply_db, reading_id)
    unrelated_rel = _binding_rel(supply_db, unrelated_id)
    reading_file = projection_root / reading_rel if reading_rel else Path("/nonexistent")
    unrelated_file = projection_root / unrelated_rel if unrelated_rel else Path("/nonexistent")
    check("真 owner 写入：两件作品各有登记投影，正文在文件里",
          bool(reading_rel) and bool(unrelated_rel)
          and sentinel_in_file(reading_file, SENTINEL_READING)
          and sentinel_in_file(unrelated_file, SENTINEL_UNRELATED))

    # -- retained pre-deletion backup (databases *and* the projection file) ------------------
    before = base / "before"
    before_projection = before / "projection"
    before_projection.mkdir(parents=True, exist_ok=True)
    evidence_before = before / "evidence.db"
    controls_before = before / "controls.db"
    authority_before = before / "authority.db"
    supply_before = before / "life_supply.sqlite"
    for source, target in ((evidence, evidence_before), (controls, controls_before),
                           (authority, authority_before), (supply_db, supply_before)):
        copy_db(source, target)
    (before_projection / reading_rel).parent.mkdir(parents=True, exist_ok=True)
    (before_projection / reading_rel).write_bytes(reading_file.read_bytes())
    (before_projection / unrelated_rel).parent.mkdir(parents=True, exist_ok=True)
    (before_projection / unrelated_rel).write_bytes(unrelated_file.read_bytes())
    check("删除前备份：数据库与投影文件都保留正文（retention 的事实）",
          sentinel_in_file(before_projection / reading_rel, SENTINEL_READING))

    # -- formal forget: canonical body and projection are both cleared ----------------------
    hold_rc, hold = run_cli(instance, "request", "--operation-key", "op-projection",
                            "--source-ref", "reading:artifact:projection:1", "--hold",
                            "--retention-pending")
    check("正式忘记请求：来源扣留", hold_rc == 0 and hold.get("status") == "HELD", str(hold)[:160])
    confirm_rc, confirmed = run_cli(instance, "confirm", "--case", hold["case_id"])
    check("主 case：备份仍在 → WAITING_RETENTION，不报 COMPLETE",
          confirm_rc == 0 and confirmed.get("status") == "WAITING_RETENTION"
          and confirmed.get("reason") == "FORGET_OFFLINE_BACKUP_RETAINED",
          str(confirmed)[:200])
    check("删除后：canonical 版本身仍在但正文为空、当前投影文件已清空",
          not payload_present(evidence, forgotten_id)
          and _version_body_chars(supply_db, reading_version) == 0
          and not sentinel_in_file(reading_file, SENTINEL_READING)
          and _file_size(reading_file) == 0)

    # -- first formal recover: the restored pre-deletion projection is re-derived ------------
    recovery_dir = base / "recovery"
    recovered_data = recovery_dir / "supply"
    recovered_projection = recovered_data / "projection"
    recovered_projection.mkdir(parents=True, exist_ok=True)
    recovered_evidence = recovery_dir / "evidence.db"
    recovered_controls = recovery_dir / "controls.db"
    recovered_supply_db = recovered_data / "life_supply.sqlite"
    copy_db(evidence_before, recovered_evidence)
    copy_db(controls_before, recovered_controls)
    copy_db(supply_before, recovered_supply_db)
    (recovered_projection / reading_rel).parent.mkdir(parents=True, exist_ok=True)
    (recovered_projection / reading_rel).write_bytes(
        (before_projection / reading_rel).read_bytes())
    (recovered_projection / unrelated_rel).parent.mkdir(parents=True, exist_ok=True)
    (recovered_projection / unrelated_rel).write_bytes(
        (before_projection / unrelated_rel).read_bytes())
    restored_file = recovered_projection / reading_rel

    recovered_env = dict(environment)
    recovered_env.update({
        "NYA02_MEMORY_DB": str(recovered_evidence),
        "NYA02_MEMORY_CONTROLS_DB": str(recovered_controls),
        "NYA02_F154_RECOVERY_DIR": str(recovery_dir),
        "NYA02_SUPPLY_ARTIFACT_DB": str(recovered_supply_db),
        "NYA02_SUPPLY_PROJECTION_ROOT": str(recovered_projection),
    })
    marker_path = recovery_dir / RECOVERY_STATE_FILE
    rc, first = _recover(instance, recovery_dir, recovered_evidence, recovered_controls,
                         recovered_supply_db, recovered_projection)
    reconcile = first.get("projection_reconcile") or {}
    check("恢复副本里回填的旧投影：正式 recover 后文件实际为空（擦除或重建，不靠标志）",
          rc == 0 and first.get("status") == "RECOVERED_OPEN"
          and str(reconcile.get("status") or "") in ("OK", "NOT_CONFIGURED")
          and not sentinel_in_file(restored_file, SENTINEL_READING)
          and _file_size(restored_file) == 0,
          "rc=%d status=%s reconcile=%s" % (rc, first.get("status"),
                                            reconcile.get("status")))
    marker = json.loads(marker_path.read_text(encoding="utf-8")) if marker_path.is_file() else {}
    check("激活 marker：三库代际 + 投影路径都已绑定",
          set(marker.get("incarnations") or {}) == {"evidence", "controls", "supply"}
          and str(marker.get("recovered_projection") or "") == str(recovered_projection))

    def recovered_read(socket_path):
        reader = LifeArtifactPort(str(socket_path), OWNER)
        erased = reader.read_version(reading_id, reading_version)
        unrelated_view = reader.read_version(unrelated_id, str(unrelated.version_id or ""))
        return {"erased_chars": int(erased.get("content_chars") or 0),
                "erased_body": erased.get("content") or "",
                "unrelated_ok": (unrelated_view.get("content") or "") == SENTINEL_UNRELATED}

    first_read = _with_supply_service(recovered_env, recovered_data, "first", recovered_read)
    check("恢复激活后：被删作品正文为空、无关作品正文完好",
          first_read["erased_chars"] == 0 and first_read["erased_body"] == ""
          and first_read["unrelated_ok"], str(first_read)[:160])

    # -- step 2: stop, put the pre-deletion projection back on the same path ----------------
    restored_file.write_bytes((before_projection / reading_rel).read_bytes())
    check("停服回填：旧投影正文bytes确实回到同一路径",
          sentinel_in_file(restored_file, SENTINEL_READING))

    def restart_probe(socket_path):
        probe = LifeArtifactPort(str(socket_path), OWNER)
        erased = probe.read_version(reading_id, reading_version)
        observe = probe.observe_projection(reading_id)
        candidate = observe.get("candidate") or {}
        grant_id = _supply_grant(socket_path, operation_id="projection:backfill:grant",
                                 workspace_id=workspace_id)
        before_versions = _version_rows(recovered_supply_db)
        imported = None
        if grant_id:
            imported = probe.import_projection(
                artifact_id=reading_id,
                candidate_id=str(candidate.get("candidate_id") or ""),
                base_version_id=candidate.get("base_projection_version"),
                confirm=True, grant_id=grant_id,
                operation_key="projection:backfill:import")
        after_versions = _version_rows(recovered_supply_db)
        return {"erased_chars": int(erased.get("content_chars") or 0),
                "erased_body": erased.get("content") or "",
                "representation": (observe.get("representation_health") or {}).get("state"),
                "observe_has_body": any(key in observe for key in
                                        ("content", "_candidate_content")),
                "candidate": bool(candidate.get("candidate_id")),
                "import_status": getattr(imported, "status", None),
                "import_reason": getattr(imported, "reason", None),
                "versions_before": before_versions, "versions_after": after_versions}

    backfill = _with_supply_service(recovered_env, recovered_data, "backfill", restart_probe)
    check("回填后重启：canonical 读为空，observe 只报表示状态、不带正文",
          backfill["erased_chars"] == 0 and backfill["erased_body"] == ""
          and backfill["representation"] == "DIVERGED"
          and not backfill["observe_has_body"] and backfill["candidate"],
          str(backfill)[:220])
    check("回填后重启：显式 import 在读取文件前按已擦 canonical 具名拒绝，零新版本",
          backfill["import_status"] == "BLOCKED"
          and backfill["import_reason"] == "CANONICAL_PAYLOAD_ERASED"
          and backfill["versions_after"] == backfill["versions_before"],
          "import=%s/%s versions=%d->%d" % (
              backfill["import_status"], backfill["import_reason"],
              backfill["versions_before"], backfill["versions_after"]))
    check("此刻文件里仍有旧正文：不把读取扣留冒充成文件已清",
          sentinel_in_file(restored_file, SENTINEL_READING))

    # -- step 3: a second formal recover rebuilds the registered projection -----------------
    rc2, second = _recover(instance, recovery_dir, recovered_evidence, recovered_controls,
                           recovered_supply_db, recovered_projection)
    second_reconcile = second.get("projection_reconcile") or {}
    check("第二次正式 recover：登记投影从 canonical 重建，回填bytes实际消失",
          rc2 == 0 and second.get("status") == "RECOVERED_OPEN"
          and reading_id in (second_reconcile.get("reconciled") or [])
          and not sentinel_in_file(restored_file, SENTINEL_READING)
          and _file_size(restored_file) == 0,
          "rc=%d status=%s reconciled=%s" % (rc2, second.get("status"),
                                             len(second_reconcile.get("reconciled") or [])))
    marker2 = json.loads(marker_path.read_text(encoding="utf-8"))
    check("换发新代际：同一路径的文件不是上一次激活的副本",
          marker2.get("incarnations") != marker.get("incarnations"))

    def after_reconcile(socket_path):
        probe = LifeArtifactPort(str(socket_path), OWNER)
        erased = probe.read_version(reading_id, reading_version)
        observe = probe.observe_projection(reading_id)
        unrelated_observe = probe.observe_projection(unrelated_id)
        before_versions = _version_rows(recovered_supply_db)
        candidate = observe.get("candidate") or {}
        grant_id = _supply_grant(socket_path, operation_id="projection:after:grant",
                                 workspace_id=workspace_id)
        imported = None
        if grant_id:
            imported = probe.import_projection(
                artifact_id=reading_id,
                candidate_id=str(candidate.get("candidate_id") or "none"),
                base_version_id=candidate.get("base_projection_version"),
                confirm=True, grant_id=grant_id,
                operation_key="projection:after:import")
        after_versions = _version_rows(recovered_supply_db)
        head = probe.read_artifact(unrelated_id).get("head") or {}
        committed = None
        if grant_id and head.get("version_id"):
            committed = probe.commit_version(
                artifact_id=unrelated_id, expected_head=str(head["version_id"]),
                content=SENTINEL_UNRELATED_V2, grant_id=grant_id,
                operation_key="projection:after:commit")
        unrelated_read = probe.read_version(unrelated_id, str(committed.version_id)
                                            if committed is not None and committed.applied
                                            else str(unrelated.version_id or ""))
        return {"erased_chars": int(erased.get("content_chars") or 0),
                "erased_body": erased.get("content") or "",
                "representation": (observe.get("representation_health") or {}).get("state"),
                "unrelated_representation":
                    (unrelated_observe.get("representation_health") or {}).get("state"),
                "import_status": getattr(imported, "status", None),
                "import_reason": getattr(imported, "reason", None),
                "versions_before": before_versions, "versions_after_import": after_versions,
                "commit_status": getattr(committed, "status", None),
                "commit_applied": bool(getattr(committed, "applied", False)),
                "unrelated_content": unrelated_read.get("content") or ""}

    after = _with_supply_service(recovered_env, recovered_data, "after", after_reconcile)
    check("重建后：被删作品读为空、投影为 CURRENT（空表示），import 仍具名拒绝且零新版本",
          after["erased_chars"] == 0 and after["erased_body"] == ""
          and after["representation"] == "CURRENT"
          and after["import_status"] == "BLOCKED"
          and after["import_reason"] == "CANONICAL_PAYLOAD_ERASED"
          and after["versions_after_import"] == after["versions_before"],
          "repr=%s import=%s/%s" % (after["representation"], after["import_status"],
                                    after["import_reason"]))
    check("无关作品：正文与投影保留，正常新版本提交后投影更新为新正文",
          after["commit_applied"] and after["commit_status"] == "OK"
          and after["unrelated_content"] == SENTINEL_UNRELATED_V2
          and after["unrelated_representation"] in ("CURRENT", "DIVERGED")
          and not sentinel_in_file(recovered_projection / unrelated_rel, SENTINEL_UNRELATED)
          and sentinel_in_file(recovered_projection / unrelated_rel, SENTINEL_UNRELATED_V2),
          "commit=%s repr=%s" % (after["commit_status"], after["unrelated_representation"]))

    # -- post-activation Memory: same erased source re-submitted is still refused -----------
    service = memory_service_from_env(dict(recovered_env))
    try:
        before_rows = count_rows(recovered_evidence)
        reingested = build_outcome_event(
            subject=OWNER, source_ref="reading:artifact:projection:1:new",
            text="隔离演示：换 id 的同源重投。",
            occurred_at=datetime.now(timezone.utc).isoformat(),
            refs=[("artifact", reading_id)])
        admission = service.port.append_outcome(reingested)
        rows_after = count_rows(recovered_evidence)
    finally:
        close = getattr(service, "close", None)
        if close is not None:
            close()
    check("已删来源换 event id 重投：写入前拒绝、M0 零增",
          admission.get("status") == "refused" and rows_after == before_rows,
          "status=%s rows=%d" % (admission.get("status"), rows_after - before_rows))
    check("恢复副本 Memory 仍 READY（拒绝是具名的治理结果，不是损坏）",
          _service_status(recovered_env).get("status") == "READY")
    check("无关 M0 经历正文保留",
          payload_present(recovered_evidence, kept_id))

    status_rc, case_status = run_cli(instance, "status", "--case", hold["case_id"])
    check("主 case 仍 WAITING_RETENTION：保留备份没被算成已清理",
          status_rc == 0 and case_status.get("status") == "WAITING_RETENTION"
          and sentinel_in_file(before_projection / reading_rel, SENTINEL_READING))

    print("FIXTURE_ROOT %s" % base)
    if FAILURES:
        print("FAILURES: %d" % len(FAILURES))
        return 1
    print("ALL_EXPECTATIONS_MET")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

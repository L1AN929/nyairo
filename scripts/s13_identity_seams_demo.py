#!/usr/bin/env python3
"""S13 F-140/F-141/F-142 身份接缝的实际演示：真实 Supply owner + 正式 socket 入口。

只走仓库里已有的链路，不新造身份系统：

* 真实 ``LifeSupplyService``（GovernanceAuthority + WriterFence + 协调 UoW）在隔离 root 的
  AF_UNIX socket 上提供正式请求入口，调用者由服务端按 ``SO_PEERCRED`` 判定；
* 签发/撤销 grant 走服务端既有的 ``issue_grant`` / ``revoke_grant``；
* 本演示只验证一处已定义字段的漏传修复：``REVOKE_GRANT`` 的协调日志此前没有写入
  ``subject_id``，而 subject 只能从被撤的 grant 行推导，不能由请求声明。

同时如实记录既有、本次未改的边界（不把它们说成新做的）：
F-142 的 writer fence 已在 grant 行；F-141 的 run/epoch 是 ``WriterFence.instance_id + epoch``，
没有 originBranchId；F-140 的参与者端点只有进程级 SO_PEERCRED，没有参与者注册表。

脚本只打印状态、拒绝码、引用与计数，**绝不打印正文、grant 请求体或任何 token**。
隔离 root 保留，不做物理 DELETE。

    PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/s13_identity_seams_demo.py
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

SUBJECT = "chiyo-s13-identity"
OTHER_SUBJECT = "chiyo-s13-identity-other"
WORKSPACE_SCOPE = "workspace:personal"
WORKSPACE_AUTHORITY = "life-supply.workspace.v1"


def _scratch_base() -> Path:
    override = os.environ.get("PI_SCRATCH_DIR")
    base = (Path(override) / "nyairo-s13-identity" if override
            else Path(os.environ.get("TMPDIR") or "/tmp") / "nyairo-s13-identity")
    base.mkdir(parents=True, exist_ok=True)
    return base


def _new_root() -> Path:
    root = _scratch_base() / ("run-%d-%d" % (os.getpid(), time.time_ns()))
    if root.exists():
        raise RuntimeError("fixture root already exists: %s" % root)
    return root


def _owner_call(socket_path: Path, request: dict[str, Any], *, timeout: float = 30.0) -> dict[str, Any]:
    body = json.dumps(dict(request), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(float(timeout))
        client.connect(str(socket_path))
        client.sendall(body + b"\n")
        line = client.makefile("rb").readline(1 << 20)
    if not line:
        raise RuntimeError("owner closed the connection without an answer")
    answer = json.loads(line.decode("utf-8"))
    if not isinstance(answer, dict):
        raise RuntimeError("owner answer is not an object")
    return answer


class Owner:
    """一个真实 LifeSupplyService + 它自己的 Unix socket 服务。正规停启，不手删 socket。"""

    def __init__(self, root: Path) -> None:
        from lifesupply.service.life_supply import (
            LifeSupplyService, LifeSupplySocketServer, prepare_socket_path)
        from lifesupply.service.switches import FeatureSwitches

        self.root = Path(root)
        self.socket_path = self.root / "life-supply.sock"
        self.socket_handling = prepare_socket_path(self.socket_path)
        uid = os.geteuid()
        self.service = LifeSupplyService(self.root / "data", operator_uids={uid},
                                         service_uids={uid},
                                         switches=FeatureSwitches.all_on(),
                                         writer_instance="s13-identity-demo-%d" % os.getpid())
        self.server = LifeSupplySocketServer(str(self.socket_path), self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def call(self, request: dict[str, Any]) -> dict[str, Any]:
        return _owner_call(self.socket_path, request)

    def journal_count(self) -> int:
        with sqlite3.connect("file:%s?mode=ro" % self.service.db_path, uri=True) as con:
            return int(con.execute("SELECT COUNT(*) FROM life_supply_uow_journal").fetchone()[0])

    def journal_row(self, operation_id: str) -> dict[str, Any] | None:
        with sqlite3.connect("file:%s?mode=ro" % self.service.db_path, uri=True) as con:
            con.row_factory = sqlite3.Row
            row = con.execute(
                "SELECT command,subject_id,caller_principal,participants,outcome"
                " FROM life_supply_uow_journal WHERE operation_id=?", (operation_id,)).fetchone()
            return dict(row) if row else None

    def grant_row(self, grant_id: str) -> dict[str, Any] | None:
        with sqlite3.connect("file:%s?mode=ro" % self.service.db_path, uri=True) as con:
            con.row_factory = sqlite3.Row
            row = con.execute("SELECT * FROM permission_grants WHERE grant_id=?",
                              (grant_id,)).fetchone()
            return dict(row) if row else None

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.drained = self.server.drain(10.0)
        self.service.close()
        self.thread.join(timeout=30)


def _issue(owner: Owner, subject: str, operation_id: str) -> str:
    answer = owner.call({"op": "issue_grant", "grant": {
        "subject": subject,
        "capability": "PROVISION_PERSONAL_WORKSPACE",
        "scope": WORKSPACE_SCOPE,
        "target": "workspace:%s" % subject,
        "authority_scope": WORKSPACE_AUTHORITY,
        "operation_id": operation_id,
    }})
    if answer.get("status") != "ALLOW":
        raise RuntimeError("grant issue refused: %r" % answer)
    return str(answer["grant_id"])


def _revoke(owner: Owner, grant_id: str, operation_id: str,
            expected_revision: int) -> dict[str, Any]:
    return owner.call({"op": "revoke_grant", "grant_id": grant_id,
                       "expected_revision": int(expected_revision),
                       "operation_id": operation_id})


def main() -> int:
    if not hasattr(os, "geteuid"):
        print("result=SKIP_NOT_POSIX")
        return 2
    root = _new_root()
    owner: Owner | None = None
    facts: dict[str, Any] = {"root": str(root)}
    checks: dict[str, bool] = {}
    try:
        owner = Owner(root)
        from lifesupply.identity import SERVICE_PRINCIPAL

        # 1) 正控：签发 → 撤销。撤销请求本身不含任何 subject 字段。
        grant_id = _issue(owner, SUBJECT, "s13:issue:%s" % SUBJECT)
        grant = owner.grant_row(grant_id) or {}
        facts["grant_subject"] = grant.get("subject")
        facts["grant_writer_owner"] = grant.get("writer_owner")
        facts["grant_writer_instance_matches_fence"] = (
            grant.get("writer_instance") == owner.service.fences["Governance"].instance_id)
        facts["grant_writer_epoch_matches_fence"] = (
            int(grant.get("writer_epoch") or 0) == int(owner.service.fences["Governance"].epoch or 0))
        facts["grant_caller_principal"] = grant.get("caller_principal")

        journal_before = owner.journal_count()
        revoke_request = {"op": "revoke_grant", "grant_id": grant_id,
                          "expected_revision": int(grant.get("revision") or 1),
                          "operation_id": "s13:revoke:%s" % SUBJECT}
        facts["revoke_request_carries_subject_field"] = "subject" in revoke_request
        revoked = owner.call(revoke_request)
        facts["revoke_status"] = revoked.get("status")
        journal_after = owner.journal_count()
        row = owner.journal_row("s13:revoke:%s" % SUBJECT) or {}
        facts["journal_command"] = row.get("command")
        facts["journal_subject"] = row.get("subject_id")
        facts["journal_caller_principal"] = row.get("caller_principal")
        facts["journal_outcome"] = row.get("outcome")
        facts["journal_participants"] = row.get("participants")
        facts["journal_rows_added"] = journal_after - journal_before

        checks["revoke_allowed"] = revoked.get("status") == "REVOKED"
        checks["journal_row_written"] = facts["journal_rows_added"] == 1
        checks["journal_subject_derived_from_grant"] = row.get("subject_id") == SUBJECT
        checks["journal_caller_is_service_principal"] = row.get("caller_principal") == SERVICE_PRINCIPAL
        checks["journal_not_request_supplied"] = "subject" not in revoke_request
        checks["grant_row_has_live_writer_identity"] = (
            facts["grant_writer_owner"] == "Governance"
            and facts["grant_writer_instance_matches_fence"]
            and facts["grant_writer_epoch_matches_fence"])

        # 2) 错误身份：请求自报 writer_epoch → 拒绝，零写。
        second = _issue(owner, OTHER_SUBJECT, "s13:issue:%s" % OTHER_SUBJECT)
        second_row = owner.grant_row(second) or {}
        count_before = owner.journal_count()
        forged = owner.call({"op": "revoke_grant", "grant_id": second,
                             "expected_revision": int(second_row.get("revision") or 1),
                             "operation_id": "s13:forged:%s" % OTHER_SUBJECT,
                             "writer_epoch": 99})
        facts["forged_identity_status"] = forged.get("status")
        facts["forged_identity_reason"] = forged.get("reason")
        facts["forged_identity_journal_rows_added"] = owner.journal_count() - count_before
        facts["forged_identity_grant_state"] = (owner.grant_row(second) or {}).get("revocation_state")
        checks["forged_identity_refused"] = (
            forged.get("status") == "DENY"
            and forged.get("reason") == "IDENTITY_FIELD_IN_REQUEST")
        checks["forged_identity_zero_effect"] = (
            facts["forged_identity_journal_rows_added"] == 0
            and facts["forged_identity_grant_state"] == "ACTIVE")

        # 3) 过期 revision：拒绝，零写；subject 归属不变。
        count_before = owner.journal_count()
        stale = _revoke(owner, second, "s13:stale:%s" % OTHER_SUBJECT, expected_revision=999)
        facts["stale_revision_status"] = stale.get("status")
        facts["stale_revision_reason"] = stale.get("reason")
        facts["stale_revision_journal_rows_added"] = owner.journal_count() - count_before
        facts["stale_revision_grant_state"] = (owner.grant_row(second) or {}).get("revocation_state")
        checks["stale_revision_refused"] = stale.get("status") != "REVOKED"
        checks["stale_revision_zero_effect"] = (
            facts["stale_revision_journal_rows_added"] == 0
            and facts["stale_revision_grant_state"] == "ACTIVE")

        facts["all_passed"] = all(checks.values())
        facts["checks"] = checks
        print(json.dumps(facts, ensure_ascii=False, sort_keys=True))
        return 0 if facts["all_passed"] else 1
    finally:
        if owner is not None:
            owner.stop()


if __name__ == "__main__":
    sys.exit(main())

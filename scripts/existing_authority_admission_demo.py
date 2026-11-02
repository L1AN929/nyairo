#!/usr/bin/env python3
"""S13/S14 既有准入的实际演示：真实 Supply owner + 正式 LifeArtifactPort，不新造身份系统。

只走仓库里已有的链路：

* 真实 ``LifeSupplyService``（GovernanceAuthority + ManagedArtifactStore + WriterFence）
  在隔离 root 的 AF_UNIX socket 上提供本机的正式请求入口；
* 写操作一律经 ``LifeArtifactPort.commit_version``（Life 侧的正式 write port）；
* grant 的签发/撤销经服务端既有的 ``issue_grant`` / ``revoke_grant``，调用者身份由服务端
  按 ``SO_PEERCRED`` 判定（本 fixture 用同一 uid 同时作为 operator 与 service principal，
  这是既有测试的隔离做法；不是真实人 grantor）；
* writer 轮替走“正规停旧服务 → 起新服务”，epoch 由 ``WriterFence`` 自己推进。

演示的是“被测路径”的适配，不声称覆盖整个 Python 后端，也不声称本地能力等于真实人身份。
脚本只打印状态、拒绝码和计数，**绝不打印正文、grant 请求体或任何 token**。隔离 root 保留
（不删除、不做物理 DELETE）。

    PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/existing_authority_admission_demo.py
"""
from __future__ import annotations

import json
import os
import socket
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

import life_artifact_port as lap  # noqa: E402

SUBJECT = "chiyo-existing-admission"
ARTIFACT_SCOPE = "artifact:personal"
WORKSPACE_SCOPE = "workspace:personal"
ARTIFACT_AUTHORITY = "life-supply.artifact.v1"
WORKSPACE_AUTHORITY = "life-supply.workspace.v1"

# 合成内容，仅用于断言版本/回执，从不打印。
V1 = "合成作品第一版：只用于准入演示。"
V2 = V1 + "\n第二版：授权有效时写入。"
V3 = V2 + "\n第三版：撤权前成功写入。"
V_REFUSED = V3 + "\n不应落地的第四版。"


def _scratch_base() -> Path:
    override = os.environ.get("PI_SCRATCH_DIR")
    base = (Path(override) / "nyairo-existing-admission" if override
            else Path(os.environ.get("TMPDIR") or "/tmp") / "nyairo-existing-admission")
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
        # 复用正式的 socket guard：只有可证不活、属本 uid 的旧 socket 才会被移除。
        self.socket_handling = prepare_socket_path(self.socket_path)
        uid = os.geteuid()
        self.service = LifeSupplyService(self.root / "data", operator_uids={uid},
                                         service_uids={uid},
                                         switches=FeatureSwitches.all_on(),
                                         writer_instance="existing-admission-demo-%d" % os.getpid())
        self.server = LifeSupplySocketServer(str(self.socket_path), self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def call(self, request: dict[str, Any]) -> dict[str, Any]:
        return _owner_call(self.socket_path, request)

    @property
    def epoch(self) -> int | None:
        return self.service.fences["ManagedArtifact"].epoch

    def stop(self) -> None:
        # 与 run_service 的停止契约一致：先停 accept，再排空在途请求，最后释放 fence。
        self.server.shutdown()
        self.server.server_close()
        self.drained = self.server.drain(10.0)
        self.service.close()
        self.thread.join(timeout=30)


def _issue(owner: Owner, capability: str, scope: str, target: str, authority_scope: str,
           operation_id: str, *, use_mode: str = "REUSABLE", **extra: Any) -> str:
    grant = {"subject": SUBJECT, "capability": capability, "scope": scope, "target": target,
             "authority_scope": authority_scope, "use_mode": use_mode,
             "operation_id": operation_id}
    grant.update(extra)
    answer = owner.call({"op": "issue_grant", "grant": grant})
    if answer.get("status") != "ALLOW":
        raise RuntimeError("grant issue refused: %r" % answer)
    return str(answer["grant_id"])


def _revoke(owner: Owner, grant_id: str, operation_id: str) -> dict[str, Any]:
    row = owner.call({"op": "get_grant", "grant_id": grant_id})
    revision = int((row.get("grant") or {}).get("revision") or 0)
    return owner.call({"op": "revoke_grant", "grant_id": grant_id,
                       "expected_revision": revision, "operation_id": operation_id})


def _grant_state(owner: Owner, grant_id: str) -> str:
    row = owner.call({"op": "get_grant", "grant_id": grant_id})
    return str((row.get("grant") or {}).get("revocation_state") or "")


def _version_count(port: lap.LifeArtifactPort, artifact_id: str) -> int:
    return int(port.list_versions(artifact_id)["count"])


def _receipt_status(owner: Owner, operation_key: str) -> str:
    answer = owner.call({"op": "read_effect_receipt", "operation_key": operation_key})
    return str(answer.get("status") or "")


def _commit(port: lap.LifeArtifactPort, artifact_id: str, grant_id: str,
            content: str, operation_key: str) -> lap.ArtifactOutcome:
    head = port.read_head(artifact_id) or {}
    return port.commit_version(artifact_id=artifact_id,
                               expected_head=head.get("version_id"),
                               content=content, grant_id=grant_id,
                               operation_key=operation_key)


class _RevokeBeforeCommit:
    """受控暂停在正式 port 的 ``_call`` 接缝：commit_markdown 已构造、尚未发出时先撤权。

    不改写 owner 的任何结果，只是在同一客户端线程里先走一次真实的 ``revoke_grant`` 请求，
    再把原来的 commit 请求发出去。用来验证“先 read、后撤权、再提交”不会被放行。
    """

    def __init__(self, owner: Owner, grant_id: str, operation_id: str) -> None:
        self.owner = owner
        self.grant_id = grant_id
        self.operation_id = operation_id
        self.fired = False
        self.revoke_answer: dict[str, Any] = {}
        self._original = None

    def __enter__(self) -> "_RevokeBeforeCommit":
        original = lap.LifeArtifactPort._call

        def wrapper(port_self, request):
            if request.get("op") == "commit_markdown" and not self.fired:
                self.fired = True
                self.revoke_answer = _revoke(self.owner, self.grant_id, self.operation_id)
            return original(port_self, request)

        self._original = original
        lap.LifeArtifactPort._call = wrapper
        return self

    def __exit__(self, *_exc: object) -> bool:
        lap.LifeArtifactPort._call = self._original
        return False


def main() -> int:
    if not hasattr(os, "geteuid"):
        print("result=SKIP_NOT_POSIX")
        return 2
    root = _new_root()
    facts: dict[str, Any] = {}
    owner: Owner | None = None
    replacement: Owner | None = None
    port: lap.LifeArtifactPort | None = None
    try:
        owner = Owner(root)
        facts["root"] = str(root)
        facts["writer_epoch_initial"] = owner.epoch

        # 1) 既有受控入口：先经一次性 workspace grant 建工作区，再签发作用于该工作区的窄 grant。
        workspace_grant = _issue(owner, "PROVISION_PERSONAL_WORKSPACE", WORKSPACE_SCOPE,
                                 "workspace:%s" % SUBJECT, WORKSPACE_AUTHORITY,
                                 "issue:workspace:%s" % SUBJECT, use_mode="ONE_SHOT")
        provisioned = owner.call({"op": "provision_workspace", "grant_id": workspace_grant,
                                  "operation_key": "demo:workspace:%s" % SUBJECT,
                                  "workspace": {"subject_id": SUBJECT}})
        facts["workspace_ready"] = provisioned.get("status") == "OK"
        workspace_id = str((provisioned.get("workspace") or {}).get("workspace_id") or "")
        facts["workspace_id"] = workspace_id
        target = "workspace:%s" % workspace_id

        g1 = _issue(owner, "COMMIT_MANAGED_ARTIFACT", ARTIFACT_SCOPE, target, ARTIFACT_AUTHORITY,
                    "issue:g1:%s" % SUBJECT)
        port = lap.LifeArtifactPort(str(owner.socket_path), SUBJECT)

        created = port.create_artifact(workspace_id=workspace_id, title="准入演示作品",
                                       content=V1, grant_id=g1,
                                       operation_key="demo:create")
        facts["create_applied"] = created.applied
        artifact_id = str(created.artifact_id or "")
        facts["artifact_id"] = artifact_id
        facts["versions_after_create"] = _version_count(port, artifact_id)

        # 2) 有效 grant + 当前 writer：正式写口真实落一版并留下 owner receipt。
        ok_key = "demo:commit-valid"
        outcome = _commit(port, artifact_id, g1, V2, ok_key)
        facts["valid_commit_applied"] = outcome.applied
        facts["valid_commit_status"] = outcome.status
        facts["valid_commit_receipt"] = _receipt_status(owner, ok_key)
        facts["versions_after_valid_commit"] = _version_count(port, artifact_id)

        # 3) 同一 grant 正式撤销后，同一写口拒绝，零新版本/零 receipt/零 effect。
        revoked = _revoke(owner, g1, "revoke:g1:after-valid-commit")
        facts["revoke_but_success_stays"] = (
            revoked.get("status") == "REVOKED"
            and _grant_state(owner, g1) == "REVOKED"
            and _receipt_status(owner, ok_key) == "OK"
            and _version_count(port, artifact_id) == facts["versions_after_valid_commit"])
        refused_key = "demo:commit-after-revoke"
        refused = _commit(port, artifact_id, g1, V_REFUSED, refused_key)
        facts["post_revoke_refused"] = (refused.effect_state == lap.EffectState.REFUSED
                                        and refused.status == "REVOKED")
        facts["post_revoke_zero_effect"] = (
            _version_count(port, artifact_id) == facts["versions_after_valid_commit"]
            and _receipt_status(owner, refused_key) == "NOT_FOUND")

        # 4) 先 read、后撤权、再提交：在正式 port 发出前撤权，仍不得放行。
        g2 = _issue(owner, "COMMIT_MANAGED_ARTIFACT", ARTIFACT_SCOPE, target, ARTIFACT_AUTHORITY,
                    "issue:g2:%s" % SUBJECT)
        port.read_head(artifact_id)  # 调用方先读到的 head 已过期；撤权后仍不得借它提交
        race_key = "demo:read-then-revoke-then-commit"
        with _RevokeBeforeCommit(owner, g2, "revoke:g2:before-send") as race:
            raced = _commit(port, artifact_id, g2, V_REFUSED, race_key)
        facts["race_revoke_fired"] = race.fired and race.revoke_answer.get("status") == "REVOKED"
        facts["race_refused"] = (raced.effect_state == lap.EffectState.REFUSED
                                 and raced.status == "REVOKED")
        facts["race_zero_effect"] = (
            _version_count(port, artifact_id) == facts["versions_after_valid_commit"]
            and _receipt_status(owner, race_key) == "NOT_FOUND")

        # 5) 已提交成功再撤权：旧 SUCCEEDED/receipt 不被倒写；新 Action 被拒。
        g3 = _issue(owner, "COMMIT_MANAGED_ARTIFACT", ARTIFACT_SCOPE, target, ARTIFACT_AUTHORITY,
                    "issue:g3:%s" % SUBJECT)
        third_key = "demo:commit-then-revoke"
        third = _commit(port, artifact_id, g3, V3, third_key)
        before_revoke = _version_count(port, artifact_id)
        _revoke(owner, g3, "revoke:g3:after-success")
        replay = _commit(port, artifact_id, g3, V3, third_key)  # 同 operation_key，历史回执
        facts["success_then_revoke_keeps_receipt"] = (
            third.applied and _receipt_status(owner, third_key) == "OK"
            and _grant_state(owner, g3) == "REVOKED")
        facts["successful_replay_no_new_version"] = (
            _version_count(port, artifact_id) == before_revoke
            and _receipt_status(owner, third_key) == "OK")
        facts["replay_effect_state"] = replay.effect_state
        new_after_revoke = _commit(port, artifact_id, g3, V_REFUSED,
                                   "demo:new-action-after-success-revoke")
        facts["new_action_after_revoke_refused"] = (
            new_after_revoke.effect_state == lap.EffectState.REFUSED
            and _version_count(port, artifact_id) == before_revoke)

        # 6) 既有判据逐项：scope 错 / target 错 / payload 变化 / 过期，都拒且零效果。
        baseline = _version_count(port, artifact_id)
        wrong_scope = _issue(owner, "COMMIT_MANAGED_ARTIFACT", "artifact:elsewhere", target,
                             ARTIFACT_AUTHORITY, "issue:wrong-scope:%s" % SUBJECT)
        wrong_target = _issue(owner, "COMMIT_MANAGED_ARTIFACT", ARTIFACT_SCOPE,
                              "workspace:not-this-one", ARTIFACT_AUTHORITY,
                              "issue:wrong-target:%s" % SUBJECT)
        wrong_payload = _issue(owner, "COMMIT_MANAGED_ARTIFACT", ARTIFACT_SCOPE, target,
                               ARTIFACT_AUTHORITY, "issue:wrong-payload:%s" % SUBJECT,
                               payload_binding={"bound": "different"})
        short_lived = _issue(owner, "COMMIT_MANAGED_ARTIFACT", ARTIFACT_SCOPE, target,
                             ARTIFACT_AUTHORITY, "issue:expiring:%s" % SUBJECT,
                             expires_at=time.time() + 0.4)

        scope_refused = _commit(port, artifact_id, wrong_scope, V_REFUSED, "demo:wrong-scope")
        target_refused = _commit(port, artifact_id, wrong_target, V_REFUSED, "demo:wrong-target")
        payload_refused = _commit(port, artifact_id, wrong_payload, V_REFUSED, "demo:wrong-payload")
        time.sleep(0.6)
        expired_refused = _commit(port, artifact_id, short_lived, V_REFUSED, "demo:expired")

        facts["scope_mismatch_refused"] = scope_refused.status == "SCOPE_MISMATCH"
        facts["target_mismatch_refused"] = target_refused.status == "TARGET_MISMATCH"
        facts["payload_mismatch_refused"] = payload_refused.status == "DENY"
        facts["expired_refused"] = expired_refused.status == "EXPIRED"
        facts["wrong_grants_zero_effect"] = (_version_count(port, artifact_id) == baseline
                                             and _receipt_status(owner, "demo:expired") == "NOT_FOUND")

        # 7) 真实受控 writer 轮替：正规停旧服务，再起新服务；epoch 由 fence 自己推进。
        head_before_replace = (port.read_head(artifact_id) or {}).get("version_id")
        facts["service_stopped_cleanly"] = False
        owner.stop()
        facts["service_stopped_cleanly"] = not owner.thread.is_alive()
        stale_owner = owner
        owner = None

        replacement = Owner(root)
        facts["writer_epoch_replaced"] = (
            int(replacement.epoch or 0) == int(facts["writer_epoch_initial"] or 0) + 1)
        # 旧 writer 对象（真实 fence，已随正规停服释放）不能再写库：不是伪造的 stale 字典。
        stale_refused = ""
        try:
            stale_owner.service.artifact_store.commit_markdown(
                artifact_id=artifact_id, authorized_subject_id=SUBJECT,
                expected_head=head_before_replace,
                content=V_REFUSED, operation_key="demo:stale-writer",
                grant_reservation_id="reservation:stale-writer-probe")
            stale_refused = "WROTE"
        except Exception as exc:  # 具名记录，不隐藏
            stale_refused = type(exc).__name__ + ":" + str(exc).split(":")[0]
        facts["stale_writer_refused"] = stale_refused.startswith("StaleWriter")

        port = lap.LifeArtifactPort(str(replacement.socket_path), SUBJECT)
        g4 = _issue(replacement, "COMMIT_MANAGED_ARTIFACT", ARTIFACT_SCOPE, target,
                    ARTIFACT_AUTHORITY, "issue:g4-after-replace:%s" % SUBJECT)
        current_key = "demo:commit-after-replace"
        current = _commit(port, artifact_id, g4, V_REFUSED, current_key)
        facts["current_writer_commit_applied"] = current.applied
        facts["current_writer_receipt"] = _receipt_status(replacement, current_key)
        facts["versions_after_replace"] = _version_count(port, artifact_id)

        # 8) 有序重开不恢复已撤销 grant / 旧 owner 操作仍可查。
        facts["revoked_grants_stay_revoked"] = (
            _grant_state(replacement, g1) == "REVOKED"
            and _grant_state(replacement, g3) == "REVOKED")
        after_restart = _commit(port, artifact_id, g3, V_REFUSED, "demo:revoked-after-restart")
        facts["revoked_grant_refused_after_restart"] = (
            after_restart.effect_state == lap.EffectState.REFUSED
            and _version_count(port, artifact_id) == facts["versions_after_replace"])
        facts["old_success_receipt_still_readable"] = (
            _receipt_status(replacement, ok_key) == "OK"
            and _receipt_status(replacement, third_key) == "OK")

        facts["final_versions"] = _version_count(port, artifact_id)
        facts["all_passed"] = all(value is True for key, value in facts.items()
                                  if isinstance(value, bool))
    finally:
        for live in (replacement, owner):
            if live is not None:
                try:
                    live.stop()
                except Exception as exc:  # 停服失败要可见，不静默
                    facts["stop_error"] = "%s: %s" % (type(exc).__name__, exc)

    for key in sorted(facts):
        print("%s=%s" % (key, facts[key]))
    failed = sorted(key for key, value in facts.items()
                    if isinstance(value, bool) and value is False)
    if failed:
        print("failed=" + ",".join(failed))
        print("demo=FAIL")
        return 1
    print("demo=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

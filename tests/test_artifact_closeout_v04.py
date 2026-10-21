"""W 包（v0.4 作品收尾）：真实 Life Supply owner + AR-0 文件适配器的收尾通路测试。

这个文件测什么
--------------
`archive_artifact` 是 Supply owner **已有**的生命周期动词（`lifesupply/service/life_supply.py:230-233`，
store 侧 `artifact/store.py:440-495`）。本文件测的是 Life 侧新接的那一段：
`life_artifact_port.LifeArtifactPort.archive_artifact`（port）、
`managed_artifact_action_adapter` 的 `archive_artifact` 分支（AR-0 适配器）、纯计划模块
`artifact_closeout`，以及它们与既有 AR-1/AG-2/LR-5 契约的接口是否真的对得上。

证据分级（照实写）
------------------
* **真实 owner 证据**：除下面两类之外的全部断言。owner 是
  `lifesupply.service.life_supply.LifeSupplyService` 跑在自己的 Unix socket 上，Governance /
  Workspace / ManagedArtifact 同一个 canonical 数据库，落盘在唯一夹具根里。断言的
  `lifecycle` / `revision` / head `version_id` / `version_number` / content / effect receipt /
  `lookup_operation` / grant 拒绝 都是 owner 自己给出的事实。
* **注入的传输答复**（只有 UNKNOWN 对账一项）：真实 owner 写入已经落盘，随后把**那一个 op 的答复**
  改写成 UNKNOWN，用来制造"答复丢了"。落盘事实仍由真实 owner 给出。
* **委托替身**（只有 AR-0 的 executor capability）：`submit` 前 AR-0 会调
  `executor_capability.verify()`。这里给的是一个只回答 `verify()` 的对象 —— 替身只是 AR-0 自己的
  租约，不是 owner、不是结算、也不是判据。

不做的事
--------
* 不删除任何东西，不 tombstone，不测删除；夹具根用唯一 `mkdtemp` 建在
  `/tmp/nyairo-v04-closeout`（可用 `NYAIRO_V04_CLOSEOUT_ROOT` 覆盖），**保留不清理**，也用不到
  `TemporaryDirectory`。
* 不改 Supply store、不建第二 owner、不写 Memory、不写自我信念、不发模型请求、不跑全量回归。
* 需要 POSIX（AF_UNIX + `os.geteuid`）；原生 Windows 自动跳过 owner 组，纯计划组照跑。
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"), str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

# ``artifact_closeout`` 是纯模块（只 stdlib），任何平台都能导入；下面的 owner/结算件不是：
# `action_reality_ledger` → `activity_continuity` 顶层 `import fcntl`，原生 Windows 直接 ImportError。
# 所以这里**显式**降级并把原因写进 skip 消息，而不是让整份文件在 Windows 上导入失败。
import artifact_closeout as ac  # noqa: E402

try:
    import action_reality_ledger as arl  # noqa: E402
    import activity_progress_completion_lr5 as lr5  # noqa: E402
    import life_artifact_port as lap  # noqa: E402
    import managed_artifact_action_adapter as maa  # noqa: E402
    import result_settlement_ar1 as ar1  # noqa: E402

    OWNER_STACK_AVAILABLE = True
    OWNER_STACK_IMPORT_ERROR: str | None = None
except ImportError as _exc:  # pragma: no cover - 原生 Windows 走这条
    arl = lr5 = lap = maa = ar1 = None  # type: ignore[assignment]
    OWNER_STACK_AVAILABLE = False
    OWNER_STACK_IMPORT_ERROR = f"{type(_exc).__name__}: {_exc}"
try:
    import life_integration_ag2 as ag2  # noqa: E402

    AG2_AVAILABLE = True
except ImportError:  # pragma: no cover - 与 OWNER_STACK 同步降级
    ag2 = None  # type: ignore[assignment]
    AG2_AVAILABLE = False


#: 夹具根：唯一创建、保留；不 rm -rf、不重建固定路径、不动共享目录。
BASE = Path(os.environ.get("NYAIRO_V04_CLOSEOUT_ROOT") or "/tmp/nyairo-v04-closeout")
SUBJECT = "test:v04-work-closeout"

posix_only = unittest.skipIf(
    os.name == "nt" or not OWNER_STACK_AVAILABLE,
    "需要 POSIX AF_UNIX/os.geteuid 与可导入的 AR-0/LR-5 栈"
    "（在 WSL/Linux 上运行）" + (f"；本机导入失败：{OWNER_STACK_IMPORT_ERROR}"
                                  if OWNER_STACK_IMPORT_ERROR else ""))


def _unique_root(prefix: str) -> Path:
    BASE.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=str(BASE)))


def _call(socket_path, request, *, timeout: float = 15.0) -> dict:
    """One JSON line to the owner.  Used for operator ops (issue/revoke/grant reads)."""
    body = json.dumps(dict(request), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(float(timeout))
        client.connect(str(socket_path))
        client.sendall(body + b"\n")
        line = client.makefile("rb").readline(1 << 20)
    if not line:
        raise AssertionError("owner closed the connection without an answer")
    answer = json.loads(line.decode("utf-8"))
    if not isinstance(answer, dict):
        raise AssertionError("owner answer is not an object")
    return answer


class OwnerServices:
    """真实 Life Supply owner：一个 canonical db、一个 socket、Governance + Workspace + Artifact。"""

    def __init__(self, root, *, writer_instance: str = "w-closeout") -> None:
        from lifesupply.service.life_supply import LifeSupplyService, LifeSupplySocketServer
        from lifesupply.service.switches import FeatureSwitches

        self.root = Path(root)
        self.socket_path = self.root / "life-supply.sock"
        uid = os.geteuid()
        self.service = LifeSupplyService(self.root / "data", operator_uids={uid},
                                         service_uids={uid},
                                         switches=FeatureSwitches.all_on(),
                                         writer_instance=writer_instance)
        self.server = LifeSupplySocketServer(str(self.socket_path), self.service)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def call(self, request: dict) -> dict:
        return _call(self.socket_path, request)

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.service.close()


class _OwnerExecutorCapability:
    """AR-0's executor capability: the only stubbed thing is AR-0's own lease token."""

    def __init__(self) -> None:
        self.verified = 0

    def verify(self) -> bool:
        self.verified += 1
        return True


class _InjectedLostAnswer:
    """注入故障：把某一个 op 的答复改写成 UNKNOWN，其余请求原样转发真实 owner。

    ``forward=True`` -> 真实写入确实落盘，只是答复丢了（owner 侧应有 operation 行 + effect receipt）。
    """

    def __init__(self, port: lap.LifeArtifactPort, *, op: str, forward: bool = True) -> None:
        self.op = op
        self.forward = forward
        self.seen: list[dict] = []
        self._port = port
        self._real = port._call
        self._patcher: mock._patch | None = None

    def __enter__(self) -> "_InjectedLostAnswer":
        def wrapper(request):
            if request.get("op") != self.op:
                return self._real(request)
            self.seen.append(dict(request))
            if self.forward:
                self._real(request)
            return {"status": "UNKNOWN", "reason": "SERVICE_ERROR",
                    "detail": "injected: the answer was lost"}

        self._patcher = mock.patch.object(self._port, "_call", wrapper)
        self._patcher.start()
        return self

    def __exit__(self, *exc_info) -> bool:
        assert self._patcher is not None
        self._patcher.stop()
        return False


@posix_only
class ArchiveCloseoutOwnerTest(unittest.TestCase):
    """真实 owner + 真实 AR-0 适配器分支。每个测试自建唯一夹具根，保留不清理。"""

    def setUp(self) -> None:
        self.root = _unique_root("w-closeout-")
        self.services = OwnerServices(self.root)
        self.addCleanup(self.services.stop)
        self.workspace_id = self._provision_workspace()
        self.artifact_grant = self._issue_artifact_grant()
        self.port = lap.LifeArtifactPort(str(self.services.socket_path), SUBJECT)
        created = self.port.create_artifact(
            workspace_id=self.workspace_id, title="那篇没写完的东西", content="第一版：开头。",
            grant_id=self.artifact_grant, operation_key="w:create:%s" % SUBJECT)
        self.assertTrue(created.applied, created)
        self.artifact_id = str(created.artifact_id)
        sibling = self.port.create_artifact(
            workspace_id=self.workspace_id, title="另一件还没写完的", content="另一件的开头。",
            grant_id=self.artifact_grant, operation_key="w:create:sibling:%s" % SUBJECT)
        self.assertTrue(sibling.applied, sibling)
        self.sibling_id = str(sibling.artifact_id)

    # -- owner-side plumbing (operator ops, not the subject's) ---------------------------

    def _issue(self, capability: str, scope: str, target: str, authority_scope: str,
               use_mode: str, operation_id: str) -> str:
        answer = self.services.call({"op": "issue_grant", "grant": {
            "subject": SUBJECT, "capability": capability, "scope": scope, "target": target,
            "authority_scope": authority_scope, "use_mode": use_mode,
            "operation_id": operation_id}})
        self.assertEqual(answer.get("status"), "ALLOW", answer)
        return str(answer["grant_id"])

    def _provision_workspace(self) -> str:
        grant = self._issue("PROVISION_PERSONAL_WORKSPACE", "workspace:personal",
                            "workspace:%s" % SUBJECT, "life-supply.workspace.v1", "ONE_SHOT",
                            "issue:workspace:%s" % SUBJECT)
        answer = self.services.call({"op": "provision_workspace", "grant_id": grant,
                                     "operation_key": "w:workspace:%s" % SUBJECT,
                                     "workspace": {"subject_id": SUBJECT}})
        self.assertEqual(answer.get("status"), "OK", answer)
        return str(answer["workspace"]["workspace_id"])

    def _issue_artifact_grant(self) -> str:
        return self._issue("COMMIT_MANAGED_ARTIFACT", "artifact:personal",
                           "workspace:%s" % self.workspace_id, "life-supply.artifact.v1",
                           "REUSABLE", "issue:artifact:%s" % SUBJECT)

    def _revoke(self, grant_id: str) -> dict:
        current = self.services.call({"op": "get_grant", "grant_id": grant_id})
        self.assertEqual(current.get("status"), "OK", current)
        revision = int(current["grant"]["revision"])
        answer = self.services.call({"op": "revoke_grant", "grant_id": grant_id,
                                     "expected_revision": revision,
                                     "operation_id": "revoke:%s:%d" % (grant_id, revision)})
        self.assertEqual(answer.get("status"), "REVOKED", answer)
        return answer

    # -- owner-side reads ---------------------------------------------------------------

    def _state(self, artifact_id: str) -> dict:
        view = self.port.read_artifact(artifact_id)
        artifact = view["artifact"]
        head = view["head"] or {}
        return {"lifecycle": artifact["lifecycle"], "revision": int(artifact["revision"]),
                "head_version_id": artifact.get("head_version_id"),
                "version_number": head.get("version_number"),
                "content": view.get("content")}

    def _revision(self, artifact_id: str) -> int:
        return int(self.port.read_artifact(artifact_id)["artifact"]["revision"])

    def _operation_row(self, operation_key: str) -> dict:
        return _call(self.services.socket_path,
                     {"op": "lookup_operation", "owner": "ManagedArtifact",
                      "operation_id": operation_key})

    def _receipt(self, operation_key: str) -> dict:
        return _call(self.services.socket_path,
                     {"op": "read_effect_receipt", "operation_key": operation_key})

    def _adapter(self, *, drop_response: bool = False) -> maa.SupplyManagedArtifactActionAdapter:
        return maa.SupplyManagedArtifactActionAdapter(
            self.port, grant_id=self.artifact_grant, workspace_id=self.workspace_id,
            fault_injection_drop_response_after_write=drop_response)

    def _step(self, *, step_index: int = 1, activity_ref: str = "act:closeout:test",
              artifact_id: str | None = None, expected_revision: int | None = None) -> dict:
        target = artifact_id or self.artifact_id
        revision = self._revision(target) if expected_revision is None else expected_revision
        return ac.build_closeout_step(
            target, revision, activity_ref=activity_ref,
            correlation_id="cor:closeout:test:%d" % step_index,
            grant_id=self.artifact_grant)

    def _submit(self, adapter, *, step: dict, action_id: str):
        action = {"action_id": action_id, "idempotency_key": step["idempotency_key"],
                  "action_kind": arl.ACTION_KIND_FILE, "target_ref": step["target_ref"]}
        payload = adapter.validate_prepare(action, step["payload_spec"])
        # AR-0 freezes the validated payload on the action at PREPARE time, so the adapter's own
        # re-read (observed_result_for_action) sees it during submit.  The test mirrors that order.
        action["frozen_payload"] = payload
        capability = _OwnerExecutorCapability()
        outcome = adapter.submit(executor_capability=capability, action=action,
                                 attempt={"attempt_id": "aatt:%s" % action_id},
                                 payload=payload)
        self.assertEqual(capability.verified, 1)
        return action, payload, outcome

    # -- 1. the real archive ------------------------------------------------------------

    def test_port_archive_moves_the_lifecycle_and_keeps_every_version(self):
        before = self._state(self.artifact_id)
        self.assertEqual(before["lifecycle"], "ACTIVE")

        outcome = self.port.archive_artifact(
            artifact_id=self.artifact_id, expected_revision=before["revision"],
            grant_id=self.artifact_grant, operation_key="w:archive:ok:1")

        self.assertEqual(outcome.effect_state, lap.EffectState.APPLIED, outcome.raw)
        self.assertEqual(outcome.lifecycle, "ARCHIVED")
        self.assertEqual(outcome.revision, before["revision"] + 1)
        after = self._state(self.artifact_id)
        self.assertEqual(after["lifecycle"], "ARCHIVED")
        self.assertEqual(after["revision"], before["revision"] + 1)
        # 内容、版本号、head 都还在：归档不是删除，也不是 tombstone。
        self.assertEqual(after["head_version_id"], before["head_version_id"])
        self.assertEqual(after["version_number"], before["version_number"])
        self.assertEqual(after["content"], before["content"])
        listed = {str(row["artifact_id"]): row for row in self.port.list_artifacts()}
        self.assertIn(self.artifact_id, listed)
        self.assertEqual(listed[self.artifact_id]["lifecycle"], "ARCHIVED")

    # -- 2. grant ------------------------------------------------------------------------

    def test_archive_without_a_usable_grant_is_refused_and_changes_nothing(self):
        before = self._state(self.artifact_id)
        missing = self.port.archive_artifact(
            artifact_id=self.artifact_id, expected_revision=before["revision"],
            grant_id="grant:never-issued", operation_key="w:archive:nogrant:1")
        self.assertEqual(missing.effect_state, lap.EffectState.REFUSED, missing.raw)
        self.assertEqual(self._state(self.artifact_id), before)

        self._revoke(self.artifact_grant)
        revoked = self.port.archive_artifact(
            artifact_id=self.artifact_id, expected_revision=before["revision"],
            grant_id=self.artifact_grant, operation_key="w:archive:revoked:1")
        self.assertEqual(revoked.effect_state, lap.EffectState.REFUSED, revoked.raw)
        self.assertEqual(self._state(self.artifact_id), before)
        self.assertEqual(self._operation_row("w:archive:revoked:1")["status"], "NOT_FOUND")

    # -- 3. the compare-and-swap precondition -------------------------------------------
    def test_archive_with_a_stale_expected_revision_conflicts_and_stays_active(self):
        before = self._state(self.artifact_id)
        stale = before["revision"] + 5
        outcome = self.port.archive_artifact(
            artifact_id=self.artifact_id, expected_revision=stale,
            grant_id=self.artifact_grant, operation_key="w:archive:stale:1")
        self.assertEqual(outcome.effect_state, lap.EffectState.CONFLICT, outcome.raw)
        # owner 自己报出当前 revision，而不是让调用方猜。
        self.assertEqual(outcome.raw.get("revision"), before["revision"])
        after = self._state(self.artifact_id)
        self.assertEqual(after["lifecycle"], "ACTIVE")
        self.assertEqual(after["revision"], before["revision"])
        self.assertEqual(self._operation_row("w:archive:stale:1")["status"], "NOT_FOUND")

    # -- 4. the original key ------------------------------------------------------------

    def test_archive_replays_the_original_operation_key_without_a_second_change(self):
        before = self._state(self.artifact_id)
        key = "w:archive:replay:1"
        first = self.port.archive_artifact(
            artifact_id=self.artifact_id, expected_revision=before["revision"],
            grant_id=self.artifact_grant, operation_key=key)
        self.assertEqual(first.effect_state, lap.EffectState.APPLIED, first.raw)
        once = self._state(self.artifact_id)
        self.assertEqual(once["revision"], before["revision"] + 1)

        replay = self.port.archive_artifact(
            artifact_id=self.artifact_id, expected_revision=before["revision"],
            grant_id=self.artifact_grant, operation_key=key)
        # 同一个键是同一件事：owner 回放原结果，不会第二次改 lifecycle。
        self.assertEqual(replay.effect_state, lap.EffectState.APPLIED, replay.raw)
        self.assertEqual(replay.revision, first.revision)
        twice = self._state(self.artifact_id)
        self.assertEqual(twice["revision"], once["revision"])
        self.assertEqual(twice["lifecycle"], "ARCHIVED")
        # owner 自己的 operation 行：这件事真的记在它名下，结果 OK。
        self.assertEqual(self._operation_row(key)["operation"]["outcome"], "OK")

    # -- 5. the full intent binds the key ------------------------------------------------

    def test_the_same_key_with_other_parameters_is_a_conflict_not_a_replay(self):
        before = self._state(self.artifact_id)
        key = "w:archive:params:1"
        first = self.port.archive_artifact(
            artifact_id=self.artifact_id, expected_revision=before["revision"],
            grant_id=self.artifact_grant, operation_key=key)
        self.assertEqual(first.effect_state, lap.EffectState.APPLIED, first.raw)
        archived = self._state(self.artifact_id)

        changed = self.port.archive_artifact(
            artifact_id=self.artifact_id, expected_revision=before["revision"] + 7,
            grant_id=self.artifact_grant, operation_key=key)
        self.assertEqual(changed.effect_state, lap.EffectState.CONFLICT, changed.raw)
        # 同一个键换了参数就是另一件事：治理层/owner 直接拒绝，既不回放旧结果，也不改状态。
        self.assertEqual(str(changed.status), "CONFLICT")
        self.assertIn("conflict", str(changed.detail).lower())
        after = self._state(self.artifact_id)
        self.assertEqual(after["revision"], archived["revision"])
        self.assertEqual(after["lifecycle"], "ARCHIVED")

    # -- 6. the adapter's own answer -----------------------------------------------------

    def test_adapter_archive_reports_the_owner_answer_and_attests_from_owner_records(self):
        step = self._step()
        expected_revision = step["payload_spec"]["expected_revision"]
        adapter = self._adapter()
        _action, _payload, outcome = self._submit(
            adapter, step=step, action_id="actn:archive:ok:1")

        self.assertEqual(outcome.transport_result, arl.TRANSPORT_OK_SUCCEEDED)
        self.assertEqual(outcome.receipt.status_claim, arl.RECEIPT_CLAIM_SUCCEEDED)
        self.assertEqual(outcome.receipt.receipt_kind, "managed_artifact_archive_receipt")
        provider_key = adapter.operation_key_for(step["idempotency_key"])
        self.assertEqual(outcome.provider_operation_ref, provider_key)
        self.assertTrue(outcome.receipt.details["archived_by_this_operation"])
        self.assertEqual(outcome.receipt.details["attestation"], ac.CLOSEOUT_PREDICATE)

        evidence = outcome.result_evidence
        self.assertIsNotNone(evidence, "owner 记录齐了却没出证据")
        self.assertTrue(evidence.verified)
        self.assertEqual(evidence.artifact_ref, self.artifact_id)
        self.assertEqual(evidence.details["attestation"], ac.CLOSEOUT_PREDICATE)
        self.assertEqual(evidence.details["operation_key"], provider_key)
        self.assertEqual(evidence.details["operation_kind"], maa.ARCHIVE_OPERATION_KIND)
        self.assertEqual(evidence.details["revision_before"], expected_revision)
        self.assertEqual(evidence.details["revision_after"], expected_revision + 1)
        # 三份 owner 证据彼此对得上：operation 行、effect receipt、回读。
        row = self._operation_row(provider_key)["operation"]
        self.assertEqual(row["operation_kind"], "ARCHIVED")
        self.assertEqual(row["artifact_id"], self.artifact_id)
        receipt = self._receipt(provider_key)["receipt"]
        self.assertEqual(receipt["status"], "APPLIED")
        self.assertEqual(receipt["effect_kind"], "ARCHIVED")
        self.assertEqual(evidence.details["receipt_id"], receipt["receipt_id"])
        self.assertEqual(self._state(self.artifact_id)["lifecycle"], "ARCHIVED")

    # -- 7. UNKNOWN resolved with the original key ---------------------------------------

    def test_adapter_unknown_archive_is_reconciled_with_the_original_key(self):
        step = self._step(step_index=2)
        expected_revision = step["payload_spec"]["expected_revision"]
        adapter = self._adapter()

        # 真实 owner 写入已经落盘，随后**那一个 op 的答复**被改写成 UNKNOWN。
        lost = _InjectedLostAnswer(self.port, op="archive_artifact", forward=True)
        with lost:
            action, payload, outcome = self._submit(
                adapter, step=step, action_id="actn:archive:unknown:1")
        self.assertEqual(outcome.transport_result, arl.TRANSPORT_ERR_AMBIGUOUS_POST_SUBMIT)
        self.assertIsNone(outcome.receipt)
        self.assertEqual(len(lost.seen), 1)
        self.assertEqual(lost.seen[0]["artifact"]["expected_revision"], expected_revision)
        # 写确实落盘了（答复丢了，效果没丢）。
        archived = self._state(self.artifact_id)
        self.assertEqual(archived["lifecycle"], "ARCHIVED")
        self.assertEqual(archived["revision"], expected_revision + 1)

        provider_key = adapter.operation_key_for(step["idempotency_key"])
        resolve = self._adapter()
        reconciled = resolve.query_status_for_reconcile(action=action, attempt={"attempt_id": "a"})
        self.assertEqual(reconciled["result"], arl.RECON_RESULT_CONFIRMED_SUCCEEDED, reconciled)
        self.assertIn(provider_key, reconciled["artifact_query_ref"])
        evidence = reconciled.get("result_evidence")
        self.assertIsNotNone(evidence, "对账确认了原 operation，却没有 owner 证据")
        self.assertEqual(evidence.details["operation_key"], provider_key)
        self.assertEqual(evidence.details["attestation"], ac.CLOSEOUT_PREDICATE)

        # 用原键再交一次也不能造出第二次 lifecycle 变更。
        again = self._adapter()
        _a, _p, replay = self._submit(again, step=step, action_id="actn:archive:unknown:1")
        self.assertEqual(replay.transport_result, arl.TRANSPORT_OK_SUCCEEDED)
        self.assertEqual(self._state(self.artifact_id)["revision"], archived["revision"])
        self.assertEqual(self._state(self.artifact_id)["lifecycle"], "ARCHIVED")

    # -- 8. an archive someone else did is not this action's evidence --------------------

    def test_archive_by_another_operation_is_not_attributed_to_this_action(self):
        before = self._state(self.artifact_id)
        theirs = self.port.archive_artifact(
            artifact_id=self.artifact_id, expected_revision=before["revision"],
            grant_id=self.artifact_grant, operation_key="w:archive:other-agent:1")
        self.assertEqual(theirs.effect_state, lap.EffectState.APPLIED, theirs.raw)
        archived = self._state(self.artifact_id)

        # 本次动作的 expected_revision 就是"已经归档后"的当前 revision，所以 owner 只能回 NO_CHANGE。
        step = self._step(expected_revision=archived["revision"])
        adapter = self._adapter()
        action, _payload, outcome = self._submit(
            adapter, step=step, action_id="actn:archive:late:1")
        self.assertEqual(outcome.transport_result, arl.TRANSPORT_OK_SUCCEEDED)
        self.assertFalse(outcome.receipt.details["archived_by_this_operation"])
        self.assertIsNone(outcome.receipt.details["attestation"])
        # 动作 SUCCEEDED，但没有任何结算证据 —— 别人的归档不能借来当自己的完成。
        self.assertIsNone(outcome.result_evidence)
        self.assertIsNone(adapter.observed_result_for_action(action=action))
        self.assertEqual(self._state(self.artifact_id)["revision"], archived["revision"])

    # -- 9. an incomplete intent never reaches the owner ---------------------------------

    def test_adapter_refuses_an_archive_intent_without_the_full_owner_revision(self):
        from action_reality_ledger import ContractViolationError

        before = self._state(self.artifact_id)
        adapter = self._adapter()
        action = {"action_id": "actn:archive:bad:1", "idempotency_key": "idem:bad:1"}
        for bad in ({"op": maa.OP_ARCHIVE_ARTIFACT, "artifact_id": self.artifact_id},
                    {"op": maa.OP_ARCHIVE_ARTIFACT, "artifact_id": self.artifact_id,
                     "expected_revision": 0},
                    {"op": maa.OP_ARCHIVE_ARTIFACT, "artifact_id": self.artifact_id,
                     "expected_revision": True},
                    {"op": maa.OP_ARCHIVE_ARTIFACT, "artifact_id": self.artifact_id,
                     "expected_revision": "2"},
                    {"op": "delete_artifact", "artifact_id": self.artifact_id,
                     "expected_revision": before["revision"]}):
            with self.assertRaises(ContractViolationError, msg=repr(bad)):
                adapter.validate_prepare(action, bad)
        self.assertEqual(adapter.submit_call_count, 0)
        self.assertEqual(self._state(self.artifact_id), before)

    # -- 10. a refused archive writes nothing --------------------------------------------

    def test_a_refused_archive_writes_nothing_to_the_work_or_its_sibling(self):
        before = self._state(self.artifact_id)
        sibling_before = self._state(self.sibling_id)
        self._revoke(self.artifact_grant)
        step = self._step(step_index=3)
        adapter = self._adapter()
        _action, _payload, outcome = self._submit(
            adapter, step=step, action_id="actn:archive:refused:1")
        self.assertEqual(outcome.transport_result, arl.TRANSPORT_ERR_DEFINITE_REJECT)
        self.assertIn("GOVERNANCE_REFUSED", str(outcome.receipt.details["detail"]))
        self.assertIsNone(outcome.result_evidence)
        self.assertEqual(self._state(self.artifact_id), before)
        self.assertEqual(self._state(self.sibling_id), sibling_before)

    # -- 11. the sibling keeps living ----------------------------------------------------

    def test_sibling_active_work_survives_the_closeout(self):
        sibling_before = self._state(self.sibling_id)
        step = self._step(step_index=4)
        adapter = self._adapter()
        _action, _payload, outcome = self._submit(
            adapter, step=step, action_id="actn:archive:sibling:1")
        self.assertEqual(outcome.transport_result, arl.TRANSPORT_OK_SUCCEEDED)
        self.assertEqual(self._state(self.artifact_id)["lifecycle"], "ARCHIVED")
        self.assertEqual(self._state(self.sibling_id), sibling_before)
        listed = {str(row["artifact_id"]): row for row in self.port.list_artifacts()}
        self.assertEqual(listed[self.sibling_id]["lifecycle"], "ACTIVE")
        self.assertIn(self.artifact_id, listed)
        resumable = self.port.latest_resumable()
        self.assertIsNotNone(resumable)
        self.assertEqual(resumable.artifact_id, self.sibling_id)

    # -- 12. a closed work is not a continuation candidate -------------------------------

    def test_closed_work_is_not_resumable_and_is_not_offered_as_a_candidate(self):
        point = self.port.resume_point(self.artifact_id)
        self.assertTrue(point.resumable)
        self.assertTrue(ac.is_resumable_selection(point))
        self.port.archive_artifact(
            artifact_id=self.artifact_id, expected_revision=point.revision,
            grant_id=self.artifact_grant, operation_key="w:archive:closed:1")
        closed = self.port.resume_point(self.artifact_id)
        self.assertEqual(closed.lifecycle, "ARCHIVED")
        self.assertFalse(closed.resumable)
        self.assertFalse(ac.is_resumable_selection(closed))
        self.assertIsNotNone(self.port.latest_resumable())
        self.assertEqual(self.port.latest_resumable().artifact_id, self.sibling_id)
        self.assertFalse(ac.is_resumable_selection({"lifecycle": "ARCHIVED",
                                                    "head_version_id": closed.head_version_id,
                                                    "content": "x"}))
        self.assertFalse(ac.is_resumable_selection(None))

    # -- 13. the settled archive evidence reaches the closeout contract -----------------

    def test_settled_archive_evidence_satisfies_the_existing_lr5_contract(self):
        step = self._step(step_index=5)
        adapter = self._adapter()
        action, payload, outcome = self._submit(
            adapter, step=step, action_id="actn:archive:settle:1")
        self.assertEqual(outcome.transport_result, arl.TRANSPORT_OK_SUCCEEDED)
        evidence = outcome.result_evidence
        self.assertIsNotNone(evidence)

        # AR-1：既有 FILE_ACTION 判定，一个字没改，用真实 owner 证据结算。
        settlement = ar1.SettlementPolicyRegistry().evaluate(
            action={"action_id": action["action_id"], "action_kind": arl.ACTION_KIND_FILE,
                    "status": arl.ACTION_STATUS_SUCCEEDED},
            attempts=[],
            evidences=[ar1.SettlementEvidenceInput.from_result_evidence(evidence.to_dict())],
            settlement_id="rset:closeout:1",
            recorded_at=evidence.recorded_at,
        )
        self.assertEqual(settlement["settlement_status"], "SETTLED", settlement)
        predicates = [c["predicate"] for c in settlement["effect_claims"]]
        self.assertIn(ac.CLOSEOUT_SETTLED_PREDICATE, predicates)
        claim = [c for c in settlement["effect_claims"]
                 if c["predicate"] == ac.CLOSEOUT_SETTLED_PREDICATE][0]
        self.assertEqual(claim["object_ref"], self.artifact_id)
        self.assertEqual(claim["epistemic_status"], "CONFIRMED")

        # LR-5：把结算事件变成它自己的证据，再用我的完成合同评估 —— 不改 evaluator、不加种类。
        progress = lr5.LifeProgressEvidence.from_settled_result_event(
            {"event_id": "srev:closeout:1", "settlement_ref": "rset:closeout:1",
             "action_id": action["action_id"], "target_ref": self.artifact_id,
             "settlement_status": settlement["settlement_status"],
             "outcome_kind": settlement["outcome_kind"],
             "effect_claims": settlement["effect_claims"],
             "confirmed_effect_refs": settlement["confirmed_effect_refs"],
             "authoritative_result_refs": settlement["authoritative_result_refs"]},
            settlement_record=settlement,
        )
        spec = ac.build_closeout_adoption_spec(self.artifact_id)
        # AG-2 真的接受这份 spec，并把**既有** LR-5 完成合同建出来（种类不变、政策版本是 artifact.v1）。
        self.assertTrue(AG2_AVAILABLE, "AG-2 不可导入，无法验证 adoption spec")
        spec_obj = ag2.ActivityAdoptionSpec.from_dict(spec)
        self.assertEqual(spec_obj.activity_kind, "CLOSE_PERSONAL_ARTIFACT")
        self.assertEqual(spec_obj.origin_ref, ac.closeout_target(self.artifact_id))
        self.assertIn(self.artifact_id, spec_obj.target_refs)
        contract = ag2.DecisionAdoptionCoordinator.build_completion_contract(
            activity_id="act:closeout:test", spec_obj=spec_obj,
            created_at="2026-10-27T00:00:00.000Z")
        self.assertEqual(contract.criteria_kind, "ACTION_RESULT_MATCH")
        self.assertEqual(contract.criteria["expected_artifact_ref"], self.artifact_id)
        self.assertEqual(contract.criteria["required_predicate"], ac.CLOSEOUT_SETTLED_PREDICATE)
        self.assertEqual(contract.policy_version, "chiyo.life.progress.artifact.v1")
        self.assertEqual(list(contract.required_evidence_kinds),
                         ["SETTLED_RESULT_EVENT", "ARTIFACT_CANONICAL_STATE"])
        self.assertEqual(list(contract.required_authority_domains),
                         ["result_settlement_authority", "artifact_authority"])
        criteria = spec["completion_contract_spec"]["criteria"]
        ok, matched, reason = lr5.NaturalCompletionEvaluator._eval_criterion_node(
            criteria_kind=spec["completion_contract_spec"]["criteria_kind"],
            criteria=criteria,
            target_refs=spec["completion_contract_spec"]["target_refs"],
            verified_evs=[progress.to_dict()],
        )
        self.assertTrue(ok, reason)
        self.assertEqual(reason, "ACTION_RESULT_MATCH_SATISFIED")
        self.assertTrue(matched)
        # 而且这条证据只对这件作品成立：换一件作品同一个证据不满足。
        other_ok, _m, other_reason = lr5.NaturalCompletionEvaluator._eval_criterion_node(
            criteria_kind=spec["completion_contract_spec"]["criteria_kind"],
            criteria={**criteria, "expected_artifact_ref": self.sibling_id},
            target_refs=spec["completion_contract_spec"]["target_refs"],
            verified_evs=[progress.to_dict()],
        )
        self.assertFalse(other_ok, other_reason)


class CloseoutPlanTest(unittest.TestCase):
    """纯计划：不需要 owner，也不假装需要。"""

    def test_closeout_plan_names_one_work_byte_for_byte(self):
        # owner 自己的 id 本来就以 `artifact:` 开头（artifact/store.py:217），所以真实 target 是
        # `closeout:artifact:artifact:<uuid>`；不剥、不补、再解析回来一模一样。
        owner_id = "artifact:0f0e0d0c0b0a"
        target = ac.closeout_target(owner_id)
        self.assertEqual(target, "closeout:artifact:artifact:0f0e0d0c0b0a")
        self.assertTrue(ac.is_closeout_target(target))
        parsed = ac.parse_closeout_target(target)
        self.assertEqual(parsed["owner_artifact_id"], owner_id)
        self.assertEqual(parsed["artifact_ref"], owner_id)
        self.assertEqual(parsed["namespace"], "closeout:artifact:")
        self.assertEqual(ac.closeout_target(parsed["owner_artifact_id"]), target)
        # 别的命名空间不归本模块管：拒绝，而不是猜。空 work id / 前后空白 / 内嵌空白也一律拒绝。
        for bad in ("artifact:0f0e", "closeout:artifact:", "closeout:artifact: a",
                    "closeout:artifact:a b", " closeout:artifact:a", "", None, 7):
            with self.assertRaises(ac.CloseoutTargetError, msg=repr(bad)):
                ac.parse_closeout_target(bad)
            self.assertFalse(ac.is_closeout_target(bad))
        # 但 `closeout:artifact:artifact:` 是**合法**的：它的 work id 就是 `artifact:`
        # （本模块全字节保真，不因为看起来像半个前缀就拒绝或改写）。
        self.assertEqual(ac.parse_closeout_target("closeout:artifact:artifact:")["owner_artifact_id"],
                         "artifact:")
        with self.assertRaises(ac.CloseoutTargetError):
            ac.closeout_target("")
        with self.assertRaises(ac.CloseoutTargetError):
            ac.closeout_target(" artifact:x")

        # adoption spec 的形状（纯，任何平台都跑）：keys 就是 ActivityAdoptionSpec 的真实字段名。
        spec_dict = ac.build_closeout_adoption_spec(owner_id, title="收尾一件作品")
        self.assertEqual(spec_dict["target_refs"], [target, owner_id])
        self.assertEqual(spec_dict["origin_ref"], target)
        self.assertEqual(spec_dict["activity_kind"], "CLOSE_PERSONAL_ARTIFACT")
        completion = spec_dict["completion_contract_spec"]
        self.assertEqual(completion["criteria_kind"], "ACTION_RESULT_MATCH")
        self.assertEqual(completion["criteria"]["expected_artifact_ref"], owner_id)
        self.assertEqual(completion["criteria"]["required_predicate"],
                         ac.CLOSEOUT_SETTLED_PREDICATE)
        self.assertEqual(completion["required_evidence_kinds"],
                         ["SETTLED_RESULT_EVENT", "ARTIFACT_CANONICAL_STATE"])
        self.assertEqual(completion["required_authority_domains"],
                         ["result_settlement_authority", "artifact_authority"])
        self.assertEqual(completion["policy_version"], "chiyo.life.progress.artifact.v1")
        # AG-2 真的接受它（需要 AG-2 可导入；WSL/Linux 上由测试 #13 无条件下断言）。
        if AG2_AVAILABLE:
            spec_obj = ag2.ActivityAdoptionSpec.from_dict(spec_dict)
            self.assertEqual(spec_obj.activity_kind, "CLOSE_PERSONAL_ARTIFACT")
            self.assertEqual(spec_obj.origin_ref, target)
            self.assertIn(owner_id, spec_obj.target_refs)
            contract = ag2.DecisionAdoptionCoordinator.build_completion_contract(
                activity_id="act:closeout:plan", spec_obj=spec_obj,
                created_at="2026-10-27T00:00:00.000Z")
            self.assertEqual(contract.criteria_kind, "ACTION_RESULT_MATCH")
            self.assertEqual(contract.criteria["expected_artifact_ref"], owner_id)
            self.assertEqual(contract.criteria["required_predicate"],
                             ac.CLOSEOUT_SETTLED_PREDICATE)
            self.assertEqual(contract.policy_version, "chiyo.life.progress.artifact.v1")
            self.assertEqual(list(contract.required_evidence_kinds),
                             ["SETTLED_RESULT_EVENT", "ARTIFACT_CANONICAL_STATE"])
            self.assertEqual(list(contract.required_authority_domains),
                             ["result_settlement_authority", "artifact_authority"])

        # step：AR-0 的 FILE_ACTION，不是新动作种类；payload 就是完整意图；键由意图决定。
        step = ac.build_closeout_step(owner_id, 7, activity_ref="act:closeout:plan",
                                      grant_id="grant:abc")
        self.assertEqual(step["step_kind"], "FILE_ACTION")
        self.assertEqual(step["action_kind"], "FILE_ACTION")
        self.assertEqual(step["target_ref"], target)
        self.assertEqual(step["payload_spec"], {
            "op": "archive_artifact", "artifact_id": owner_id,
            "expected_revision": 7, "grant_id": "grant:abc"})
        self.assertEqual(step["expected_effect_predicates"], ["artifact_archived_verified"])
        self.assertEqual(step["post_submit_life_behavior"], "KEEP_ACTIVITY_ACTIVE")
        self.assertEqual(step["idempotency_key"],
                         ac.archive_idempotency_key(owner_id, 7, "act:closeout:plan"))
        # 同一件事同一个键；换 revision 或换 activity 都是另一件事。
        self.assertEqual(step["idempotency_key"],
                         ac.build_closeout_step(owner_id, 7, activity_ref="act:closeout:plan",
                                                grant_id="grant:other")["idempotency_key"])
        self.assertNotEqual(step["idempotency_key"],
                            ac.build_closeout_step(owner_id, 8,
                                                   activity_ref="act:closeout:plan")["idempotency_key"])
        self.assertNotEqual(step["idempotency_key"],
                            ac.build_closeout_step(owner_id, 7,
                                                   activity_ref="act:closeout:other")["idempotency_key"])
        # 不完整的意图直接拒绝，不补默认值。
        for bad_revision in (0, -1, True, "7", None):
            with self.assertRaises(ac.CloseoutPlanError, msg=repr(bad_revision)):
                ac.build_closeout_step(owner_id, bad_revision, activity_ref="act:closeout:plan")
        with self.assertRaises(ac.CloseoutPlanError):
            ac.build_closeout_step(owner_id, 7, activity_ref="")


if __name__ == "__main__":
    unittest.main()

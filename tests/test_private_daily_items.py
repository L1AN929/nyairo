"""B 包（ROOM/PHONE）：私人日常事项 —— 真实 Life Supply owner 集成测试。

这个文件测什么
--------------
`components/life/scripts/private_daily_items.py` 把一份 typed 私人事项文档存进**现有**
ManagedArtifact，读写全部走 `life_artifact_port.LifeArtifactPort`。所以这里的 owner 是**真的**：
`lifesupply.service.life_supply.LifeSupplyService` 跑在自己的 Unix socket 上，落盘在唯一夹具根里，
Governance / Workspace / ManagedArtifact 是同一个 canonical 数据库。断言读的是 owner 自己给出的事实
（head `version_id`/`version_number`、effect receipt、`lookup_operation`、grant `revocation_state`），
不是适配器自己说的。

证据分级（回执里照写）
----------------------
* **真实 owner 证据**：除下面两类之外的全部断言。
* **注入的传输答复**（只有 §UNKNOWN 两项）：在真实 owner 之上把**某一个 op 的答复**改写成 UNKNOWN，
  用来制造"答复丢了"。`forward=True` 时真实写入已经落盘，`forward=False` 时连写入都没有发生。
  被改写的那一步在这里标成注入；落盘事实仍由真实 owner 给出。
* **合成输入**（只有房间物件一项）：World observation 是调用方给的一份最小映射，形状取自
  `world_living_skeleton.observe_world` 的 `local_environment.furniture`。本文件不启动 World owner，
  也不声称这份观察来自真实世界运行。

不做的事
--------
* 不删除任何用户数据，也不测删除；夹具根用唯一 `mkdtemp` 建在
  `/tmp/nyaw05r.jdK3q/b-private-items-*`（可用 `NYAIRO_LIFE_V1_B_ROOT` 覆盖），**保留不清理**，
  也不碰任何共享目录。
* 不跑整套 Supply 回归（那是 Supply owner 自己的套件）；这里只跑本适配器需要的最小 owner 面。
* 需要 POSIX（AF_UNIX + fcntl）；原生 Windows 自动跳过。
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

import life_artifact_port as lap  # noqa: E402
import private_daily_items as pdi  # noqa: E402

#: 夹具根：唯一创建、保留；不 rm -rf、不重建固定路径、不动共享目录。
BASE = Path(os.environ.get("NYAIRO_LIFE_V1_B_ROOT") or "/tmp/nyaw05r.jdK3q")
SUBJECT = "test:private-daily-items"

posix_only = unittest.skipIf(os.name == "nt", "需要 POSIX AF_UNIX 与 fcntl（在 WSL/Linux 上运行）")


def _unique_root(prefix: str) -> Path:
    BASE.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=str(BASE)))


def _call(socket_path, request, *, timeout: float = 15.0) -> dict:
    """One JSON line to the owner.  Used for the **operator** ops (issue/revoke/grant reads)."""
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

    def __init__(self, root, *, writer_instance: str = "b-test") -> None:
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

    def restart(self) -> None:
        """真实重开：同一个数据根、新的 owner 实例与新的 socket。"""
        self.stop()
        self.socket_path.unlink(missing_ok=True)
        self.__init__(self.root, writer_instance="b-test-restart")


class _InjectedLostAnswer:
    """注入故障：把某一个 op 的答复改写成 UNKNOWN，其余请求原样转发真实 owner。

    ``forward=True`` -> 真实写入确实落盘，只是答复丢了（owner 侧应有 effect receipt）。
    ``forward=False`` -> 连写入都没有发生（owner 侧 lookup 应为 NOT_FOUND）。
    """

    def __init__(self, port: lap.LifeArtifactPort, *, op: str, forward: bool) -> None:
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
class PrivateItemsOwnerTest(unittest.TestCase):
    """真实 owner + 显式 artifact 绑定。每个测试自建唯一夹具根，保留不清理。"""

    def setUp(self) -> None:
        self.root = _unique_root("b-private-items-")
        self.services = OwnerServices(self.root)
        self.addCleanup(self.services.stop)
        self.workspace_id = self._provision_workspace()
        self.artifact_grant = self._issue_artifact_grant()
        self.port = lap.LifeArtifactPort(str(self.services.socket_path), SUBJECT)
        self.artifact_id, _outcome = pdi.create_document_artifact(
            self.port, workspace_id=self.workspace_id, title="私人日常事项",
            grant_id=self.artifact_grant)
        self.items = self._adapter(self.port)

    # -- owner-side plumbing (operator ops, not the subject's) ---------------------------

    def _adapter(self, port) -> pdi.PrivateDailyItems:
        return pdi.PrivateDailyItems(port, self.artifact_id, workspace_id=self.workspace_id,
                                     grant_id=self.artifact_grant)

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
                                     "operation_key": "b:workspace:%s" % SUBJECT,
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

    def _head(self) -> dict:
        return self.port.read_head(self.artifact_id) or {}

    def _version_number(self) -> int:
        return int(self._head().get("version_number") or 0)

    def _owner_content(self) -> str:
        return str(self.port.read_artifact(self.artifact_id)["content"] or "")

    def _owner_receipt(self, operation_key: str) -> dict:
        return _call(self.services.socket_path,
                     {"op": "read_effect_receipt", "operation_key": operation_key})

    # -- 1. real version + real reopen ---------------------------------------------------

    def test_real_versions_and_reopen_read_back_the_same_items(self):
        first = self.items.add_memo(title="桌上的半页草稿", body="先写两行，明天再说。")
        self.assertTrue(first.applied, first)
        self.assertEqual(first.version_number, 2)
        self.items.record_source_acquisition(
            source_id="book:coastal-plants", source_revision="rev-1",
            receipt_ref="receipt:page-1-4", obtained={"pages": [1, 2, 3, 4]},
            excerpt="第一页实际拿到的内容。")
        self.items.set_reading_position(source_id="book:coastal-plants",
                                        source_revision="rev-1", position=4)
        self.items.mark_unclear(source_id="book:coastal-plants", source_revision="rev-1",
                                note="第三页那段没看懂，再看一遍也许还是不懂。", position=3)
        self.items.save_reference(source_id="song:evening", source_revision="rev-1",
                                  label="傍晚的那首歌", receipt_ref="receipt:save-1")
        self.items.receive_item(receipt_ref="receipt:msg-1", from_ref="user",
                                kind="message", summary="一条到达但还没看的消息")
        version_before = self._version_number()
        self.assertGreaterEqual(version_before, 6)

        # 真实重开：同一个数据根，新的 owner 实例 + 新的 socket + 新的 port/adapter
        self.services.restart()
        reopened_port = lap.LifeArtifactPort(str(self.services.socket_path), SUBJECT)
        reopened = self._adapter(reopened_port)
        binding = reopened.bind()
        self.assertEqual(binding["doc_type"], pdi.DOC_TYPE)
        self.assertEqual(binding["artifact_id"], self.artifact_id)
        self.assertEqual(binding["version_number"], version_before)
        self.assertEqual(reopened.full_text(first.item_id), "先写两行，明天再说。")
        self.assertEqual(reopened.metadata_view()["counts"][pdi.ITEM_MEMO], 1)
        self.assertEqual(reopened.metadata_view()["counts"][pdi.ITEM_INBOX], 1)
        self.assertEqual(reopened.metadata_view()["unread_count"], 1)
        positions = reopened.items(pdi.ITEM_READING_POSITION)
        self.assertEqual(positions[0]["position"], 4)
        self.assertEqual(positions[0]["needs_reread"], False)
        self.assertEqual(len(reopened.items(pdi.ITEM_UNCLEAR_MARKER)), 1)
        self.assertEqual(len(reopened.items(pdi.ITEM_SAVED_REFERENCE)), 1)

    # -- 2. CAS: a stale writer must never overwrite -------------------------------------

    def test_cas_conflict_does_not_overwrite_the_newer_version(self):
        other = self._adapter(lap.LifeArtifactPort(str(self.services.socket_path), SUBJECT))
        other.add_memo(title="B 的第一张", body="B 先写的")
        stale_head = self._head()["version_id"]

        real_call = self.port._call
        interleaved: list[str] = []

        def wrapper(request):
            if request.get("op") == "commit_markdown" and not interleaved:
                interleaved.append(str(request.get("operation_key")))
                # 真实交错：另一次真实提交在 A 的提交到达 owner 之前落盘。
                other.add_memo(title="B 的第二张", body="B 又写了一张")
            return real_call(request)

        with mock.patch.object(self.port, "_call", wrapper):
            with self.assertRaises(pdi.PrivateItemsConflict) as caught:
                self.items.add_memo(title="A 的便签", body="不该覆盖 B")
        self.assertIn("CAS_CONFLICT", str(caught.exception))
        self.assertEqual(len(interleaved), 1)

        head = self._head()
        self.assertNotEqual(head["version_id"], stale_head)
        self.items.load(refresh=True)
        titles = [row["title"] for row in self.items.items(pdi.ITEM_MEMO)]
        self.assertEqual(titles, ["B 的第一张", "B 的第二张"])
        self.assertNotIn("A 的便签", self._owner_content())

    # -- 3. revoked grant is refused by the owner ----------------------------------------

    def test_revoked_grant_refuses_the_write_and_leaves_the_document_alone(self):
        self.items.add_memo(title="撤权之前", body="这张是合法的")
        version_before = self._version_number()
        self._revoke(self.artifact_grant)
        with self.assertRaises(pdi.PrivateItemsOwnerRefused) as caught:
            self.items.add_memo(title="撤权之后", body="不该落盘")
        self.assertEqual(caught.exception.reason, "GOVERNANCE_REFUSED")
        self.assertIn(caught.exception.status, {"REVOKED", "DENY", "BLOCKED"})
        self.assertEqual(self._version_number(), version_before)
        self.assertEqual([row["title"] for row in self.items.items(pdi.ITEM_MEMO)],
                         ["撤权之前"])

    # -- 4. UNKNOWN is reconciled, never resent blindly ----------------------------------

    def test_unknown_that_actually_landed_is_reconciled_by_receipt(self):
        version_before = self._version_number()
        with _InjectedLostAnswer(self.port, op="commit_markdown", forward=True) as injected:
            result = self.items.add_memo(title="答复丢了的便签", body="其实写进去了")
        self.assertEqual(len(injected.seen), 1)
        self.assertTrue(result.applied, result)
        self.assertTrue(result.reconciled)
        self.assertEqual(result.detail, "RECONCILED_AFTER_UNKNOWN")
        self.assertEqual(self._version_number(), version_before + 1)
        receipt = self._owner_receipt(str(result.operation_key))
        self.assertEqual(receipt.get("status"), "OK", receipt)
        self.assertEqual(receipt["receipt"]["artifact_id"], self.artifact_id)
        self.items.load(refresh=True)
        self.assertEqual([row["title"] for row in self.items.items(pdi.ITEM_MEMO)],
                         ["答复丢了的便签"])

    def test_unknown_that_never_landed_is_retryable_with_the_same_key(self):
        version_before = self._version_number()
        stamp = "2026-10-16T00:00:00+00:00"
        with _InjectedLostAnswer(self.port, op="commit_markdown", forward=False):
            result = self.items.add_memo(title="没落盘的便签", body="需要重试", now=stamp)
        self.assertFalse(result.applied, result)
        self.assertTrue(result.retryable)
        self.assertTrue(result.reconciled)
        self.assertEqual(result.detail, "UNKNOWN_RESOLVED_NEVER_LANDED")
        self.assertEqual(self._version_number(), version_before)
        lookup = _call(self.services.socket_path, {"op": "lookup_operation",
                                                   "owner": "ManagedArtifact",
                                                   "operation_id": str(result.operation_key)})
        self.assertEqual(lookup.get("status"), "NOT_FOUND", lookup)

        # 用同一个 operation_key + 同一份内容重试：owner 重放同一次操作，不会产生第二个版本。
        retried = self.items.add_memo(title="没落盘的便签", body="需要重试", now=stamp,
                                      operation_key=str(result.operation_key))
        self.assertTrue(retried.applied, retried)
        self.assertEqual(self._version_number(), version_before + 1)
        self.assertEqual(len(self.items.items(pdi.ITEM_MEMO)), 1)

        # 同一个 key 配不同内容：owner 明确 CONFLICT，也不会写出第二个版本。
        with self.assertRaises(pdi.PrivateItemsConflict):
            self.items.add_memo(title="换了内容的便签", body="同一个 key，不同载荷",
                                operation_key=str(result.operation_key))
        self.assertEqual(self._version_number(), version_before + 1)
        self.assertEqual(len(self.items.items(pdi.ITEM_MEMO)), 1)

    # -- 5. favorites never fetch, never become read -------------------------------------

    def test_saved_reference_never_fetches_text_and_never_marks_read(self):
        saved = self.items.save_reference(source_id="book:unread", source_revision="rev-9",
                                          label="还没打开的一本", receipt_ref="receipt:save-2",
                                          locator="shelf:left-2")
        self.assertTrue(saved.applied, saved)
        row = self.items.items(pdi.ITEM_SAVED_REFERENCE)[0]
        self.assertEqual(row["state"], pdi.REFERENCE_SAVED)
        self.assertFalse(row["read"])
        self.assertFalse(row["content_fetched"])
        self.assertTrue(row["has_locator"])
        self.assertNotIn("shelf:left-2", json.dumps(row, ensure_ascii=False))
        with self.assertRaises(pdi.PrivateItemsContractRefused) as caught:
            self.items.full_text(saved.item_id)
        self.assertIn("SAVED_REFERENCE_HOLDS_NO_TEXT", str(caught.exception))
        # 没取得过的来源不能直接写阅读位置
        with self.assertRaises(pdi.PrivateItemsContractRefused) as caught:
            self.items.set_reading_position(source_id="book:unread", source_revision="rev-9",
                                            position=1)
        self.assertIn("SOURCE_NOT_ACQUIRED", str(caught.exception))
        self.assertEqual(self.items.items(pdi.ITEM_READING_POSITION), [])

    # -- 6. a page number is not understanding -------------------------------------------

    def test_page_number_is_not_understanding_and_unclear_is_a_separate_item(self):
        self.items.record_source_acquisition(source_id="book:hard", source_revision="rev-1",
                                             receipt_ref="receipt:pages-5-6",
                                             obtained={"pages": [5, 6]})
        self.items.set_reading_position(source_id="book:hard", source_revision="rev-1",
                                        position=6)
        self.items.mark_unclear(source_id="book:hard", source_revision="rev-1",
                                note="第六页那段没看懂。", position=6)
        position = self.items.items(pdi.ITEM_READING_POSITION)[0]
        self.assertEqual(position["understanding"], pdi.UNDERSTANDING_UNKNOWN)
        self.assertNotIn("note", position)
        self.assertEqual(len(self.items.items(pdi.ITEM_UNCLEAR_MARKER)), 1)
        self.assertFalse(hasattr(self.items, "mark_understood"))
        self.assertFalse(hasattr(self.items, "set_understanding"))
        self.assertEqual(pdi.UNDERSTANDING_UNKNOWN, "UNKNOWN")

    # -- 7. an old source revision bookmark says "read it again" -------------------------

    def test_old_source_revision_bookmark_needs_reread_and_is_not_repointed(self):
        self.items.record_source_acquisition(source_id="book:edition", source_revision="rev-1",
                                             receipt_ref="receipt:rev-1",
                                             obtained={"pages": [1, 2, 3]},
                                             now="2026-10-16T00:00:01+00:00")
        self.items.set_reading_position(source_id="book:edition", source_revision="rev-1",
                                        position=3)
        self.assertEqual(self.items.items(pdi.ITEM_READING_POSITION)[0]["needs_reread"], False)

        self.items.record_source_acquisition(source_id="book:edition", source_revision="rev-2",
                                             receipt_ref="receipt:rev-2",
                                             obtained={"pages": [1, 2, 3]},
                                             now="2026-10-16T00:00:02+00:00")
        rows = self.items.items(pdi.ITEM_READING_POSITION)
        self.assertEqual(rows[0]["source_revision"], "rev-1")
        self.assertTrue(rows[0]["needs_reread"])
        self.assertEqual(rows[0]["needs_reread_reason"], "SOURCE_REVISION_ADVANCED")
        self.assertEqual(len(self.items.needs_reread_positions()), 1)
        self.assertEqual(self.items.metadata_view()["needs_reread_count"], 1)

        self.items.set_reading_position(source_id="book:edition", source_revision="rev-2",
                                        position=1)
        fresh = [row for row in self.items.items(pdi.ITEM_READING_POSITION)
                 if row["source_revision"] == "rev-2"]
        self.assertEqual(fresh[0]["needs_reread"], False)

    # -- 8. metadata never carries private text -------------------------------------------

    def test_metadata_does_not_leak_private_text(self):
        body = "PRIVATE-BODY-不要出现在元数据里"
        summary = "PRIVATE-SUMMARY-只到达不共处"
        note = "PRIVATE-NOTE-没看懂的那一段"
        locator = "PRIVATE-LOCATOR-架子上第二格"
        excerpt = "PRIVATE-EXCERPT-实际拿到的那一页"
        self.items.add_memo(title="标题可以见", body=body)
        self.items.record_source_acquisition(source_id="book:x", source_revision="rev-1",
                                             receipt_ref="receipt:x", obtained={"pages": [1]},
                                             excerpt=excerpt)
        self.items.save_reference(source_id="book:x", source_revision="rev-1", label="收藏名",
                                  receipt_ref="receipt:x", locator=locator)
        self.items.mark_unclear(source_id="book:x", source_revision="rev-1", note=note)
        self.items.receive_item(receipt_ref="receipt:m", from_ref="user", kind="message",
                                summary=summary)

        rendered = json.dumps(self.items.snapshot(), ensure_ascii=False)
        for secret in (body, summary, note, locator, excerpt):
            self.assertNotIn(secret, rendered)
        metadata = self.items.metadata_view()
        self.assertFalse(metadata["private_text_included"])
        self.assertFalse(self.items.snapshot()["private_text_included"])
        self.assertIn("收藏名", rendered)          # 已可见收藏的 label 是可见的
        flags = {row["item_type"]: row for row in metadata["items"]}
        self.assertTrue(flags[pdi.ITEM_MEMO]["has_text"])
        self.assertTrue(flags[pdi.ITEM_INBOX]["has_summary"])
        self.assertTrue(flags[pdi.ITEM_UNCLEAR_MARKER]["has_note"])
        self.assertTrue(flags[pdi.ITEM_SOURCE_ACQUISITION]["has_excerpt"])

        memo_id = self.items.items(pdi.ITEM_MEMO)[0]["item_id"]
        self.assertEqual(self.items.full_text(memo_id), body)
        self.assertEqual(self.items.source_excerpt(
            self.items.items(pdi.ITEM_SOURCE_ACQUISITION)[0]["item_id"]), excerpt)
        disclosure = self.items.disclosure_excerpt(memo_id, max_chars=8)
        self.assertTrue(disclosure["is_excerpt"])
        self.assertTrue(disclosure["truncated"])
        self.assertEqual(disclosure["excerpt"], body[:8])

    # -- 9. a revoked source is not revived by any cache ---------------------------------

    def test_unavailable_source_is_refused_not_revived(self):
        acquired = self.items.record_source_acquisition(
            source_id="book:gone", source_revision="rev-1", receipt_ref="receipt:gone",
            obtained={"pages": [1, 2]}, excerpt="PRIVATE-EXCERPT-失效前拿到的内容")
        self.assertEqual(self.items.source_excerpt(acquired.item_id),
                         "PRIVATE-EXCERPT-失效前拿到的内容")
        self.items.mark_source_unavailable(source_id="book:gone", source_revision="rev-1",
                                           reason="grant revoked by the operator")
        row = self.items.items(pdi.ITEM_SOURCE_ACQUISITION)[0]
        self.assertFalse(row["available"])
        self.assertEqual(row["unavailable_reason"], "grant revoked by the operator")
        self.assertNotIn("PRIVATE-EXCERPT", json.dumps(row, ensure_ascii=False))
        with self.assertRaises(pdi.PrivateItemsSourceUnavailable):
            self.items.source_excerpt(acquired.item_id)
        with self.assertRaises(pdi.PrivateItemsSourceUnavailable):
            self.items.full_text(acquired.item_id)
        with self.assertRaises(pdi.PrivateItemsSourceUnavailable):
            self.items.disclosure_excerpt(acquired.item_id)
        with self.assertRaises(pdi.PrivateItemsSourceUnavailable):
            self.items.set_reading_position(source_id="book:gone", source_revision="rev-1",
                                            position=2)

    # -- 10. inbox: explicit receipts only, read needs evidence --------------------------

    def test_inbox_needs_an_explicit_receipt_and_read_needs_evidence(self):
        with self.assertRaises(pdi.PrivateItemsContractRefused):
            self.items.receive_item(receipt_ref="", from_ref="user", kind="message")
        received = self.items.receive_item(receipt_ref="receipt:arrive-1", from_ref="user",
                                           kind="message", summary="到了但没看")
        self.assertTrue(received.applied, received)
        row = self.items.items(pdi.ITEM_INBOX)[0]
        self.assertEqual(row["state"], pdi.INBOX_UNREAD)
        self.assertFalse(row["co_presence"])          # 一条消息到达不等于完整共处
        self.assertIsNone(row["read_at"])
        self.assertNotIn("到了但没看", json.dumps(row, ensure_ascii=False))
        self.assertEqual(len(self.items.unread_items()), 1)
        with self.assertRaises(pdi.PrivateItemsContractRefused):
            self.items.mark_read(received.item_id, evidence_ref="")
        self.items.mark_read(received.item_id, evidence_ref="receipt:opened-1")
        row = self.items.items(pdi.ITEM_INBOX)[0]
        self.assertEqual(row["state"], pdi.INBOX_READ)
        self.assertEqual(row["read_evidence_ref"], "receipt:opened-1")
        self.assertEqual(self.items.unread_items(), [])
        # 同一个 receipt 再收一次：不会重复建条目
        again = self.items.receive_item(receipt_ref="receipt:arrive-1", from_ref="user",
                                        kind="message")
        self.assertFalse(again.applied)
        self.assertEqual(len(self.items.items(pdi.ITEM_INBOX)), 1)

    # -- 11. explicit binding: never the work of art ------------------------------------

    def test_explicit_binding_never_picks_the_work_artifact(self):
        work_grant = self.artifact_grant
        work = self.port.create_artifact(
            workspace_id=self.workspace_id, title="她的作品", content="# 作品草稿\n\n（不是私人事项）",
            grant_id=work_grant,
            operation_key=lap.operation_key_for("test", "work", SUBJECT))
        self.assertTrue(work.applied, work)
        work_version = self.port.read_head(str(work.artifact_id))["version_number"]

        self.items.add_memo(title="私人便签", body="只属于私人事项文档")
        self.assertEqual(self.port.read_head(str(work.artifact_id))["version_number"],
                         work_version)
        self.assertNotIn("私人便签", str(self.port.read_artifact(str(work.artifact_id))["content"]))

        # 作品是 markdown，不是我们的文档：显式绑到它上面读就必须拒绝，绝不"当作文档"用。
        wrong = pdi.PrivateDailyItems(self.port, str(work.artifact_id),
                                      workspace_id=self.workspace_id, grant_id=work_grant)
        with self.assertRaises(pdi.PrivateItemsContractRefused) as caught:
            wrong.load()
        self.assertIn("DOCUMENT_NOT_JSON", str(caught.exception))

        # 另一个 JSON artifact、但 doc_type 不是我们的：同样拒绝，并且说清是"不是私人事项文档"。
        foreign = self.port.create_artifact(
            workspace_id=self.workspace_id, title="别人的 JSON",
            content=json.dumps({"doc_type": "someone.else.v1", "items": {}, "order": []}),
            grant_id=work_grant,
            operation_key=lap.operation_key_for("test", "foreign", SUBJECT))
        self.assertTrue(foreign.applied, foreign)
        foreign_adapter = pdi.PrivateDailyItems(self.port, str(foreign.artifact_id),
                                               workspace_id=self.workspace_id,
                                               grant_id=work_grant)
        with self.assertRaises(pdi.PrivateItemsContractRefused) as caught:
            foreign_adapter.load()
        self.assertIn("NOT_A_PRIVATE_DAILY_ITEMS_DOCUMENT", str(caught.exception))
        self.assertEqual(len(self.port.list_artifacts()), 3)

    # -- 12. room objects are observations, not placement -------------------------------

    def test_room_object_refs_are_observation_only(self):
        observation = {"local_environment": {"furniture": [
            {"object_id": "obj:desk", "name": "书桌", "kind": "furniture", "note": ""},
            {"object_id": "obj:lamp", "name": "台灯", "kind": "furniture", "note": ""},
        ]}}
        refs = pdi.room_object_refs(observation)
        self.assertEqual([ref.object_id for ref in refs], ["obj:desk", "obj:lamp"])
        self.assertEqual(refs[0].source, "world_observation")
        self.assertEqual(pdi.room_object_refs({}), [])

        memo = self.items.add_memo(title="放在桌上的便签", body="观察链接，不是位置")
        with self.assertRaises(pdi.PrivateItemsContractRefused) as caught:
            self.items.note_item_at_observed_object(memo.item_id, observation=observation,
                                                    object_id="obj:ghost")
        self.assertIn("OBJECT_NOT_OBSERVED", str(caught.exception))
        self.items.note_item_at_observed_object(memo.item_id, observation=observation,
                                                object_id="obj:desk")
        row = self.items.items(pdi.ITEM_MEMO)[0]
        self.assertEqual(row["observed_object_ref"], "obj:desk")
        self.assertNotIn("placement", json.dumps(row, ensure_ascii=False))
        self.assertNotIn("obj:desk", json.dumps(pdi.room_object_refs({}), ensure_ascii=False))

    # -- 13. local contract refusals never touch the owner ------------------------------

    def test_contract_refusals_are_local_and_send_nothing(self):
        before = self.port.call_count
        reads_only = pdi.PrivateDailyItems(self.port, self.artifact_id,
                                           workspace_id=self.workspace_id)
        with self.assertRaises(pdi.PrivateItemsContractRefused) as caught:
            reads_only.add_memo(title="没有 grant", body="不该发出去")
        self.assertIn("GRANT_ID_REQUIRED_FOR_WRITES", str(caught.exception))
        self.assertEqual(self.port.call_count, before)
        with self.assertRaises(pdi.PrivateItemsContractRefused):
            pdi.PrivateDailyItems(self.port, "")
        with self.assertRaises(pdi.PrivateItemsContractRefused):
            self.items.item("memo:does-not-exist")
        with self.assertRaises(pdi.PrivateItemsContractRefused):
            self.items.record_source_acquisition(source_id="book:y", source_revision="rev-1",
                                                 receipt_ref="receipt:y", obtained={})
        with self.assertRaises(pdi.PrivateItemsContractRefused):
            self.items.mark_source_unavailable(source_id="book:y", source_revision="rev-1",
                                               reason="never acquired")


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)

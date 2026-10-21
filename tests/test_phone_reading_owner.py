"""C 侧阅读工作台窄测试：真实 owner + 定点注入的传输答复。

这个文件测什么
--------------
``components/life/scripts/phone_reading.py``（``PhoneReading``）与它对 ``private_daily_items`` /
``life_artifact_port`` 的窄用法。owner 是**真的**：``lifesupply.service.life_supply`` 跑在自己的
Unix socket 上，落盘在唯一夹具根里，Governance / Workspace / ManagedArtifact 是同一个 canonical
数据库。断言读的是 owner 自己给出的事实（head ``version_id`` / ``version_number``、effect
receipt、``lookup_operation``、artifact 版本号），不是 facade 自己说的。

证据分级
--------
* **真实 owner 证据**：除下面一条之外的全部断言。
* **定点注入的传输答复**（只有 §UNKNOWN 两项）：在真实 owner 之上把**某一个 op 的答复**改写成
  UNKNOWN，用来制造"答复丢了"；``forward=True`` 时真实写入已经落盘，``forward=False`` 时连写入
  都没有发生。落盘事实仍由真实 owner 给出。

不做的事
--------
* 不启动手机 HTTP 服务、不碰真实实例、不访问网络、不调模型；这里只跑 facade 这一层。
* 不删除任何东西，也不清理：夹具根用唯一 ``mkdtemp`` 建在 ``/tmp/nyaw05r.jdK3q/rc1-reading-*``
  （可用 ``NYAIRO_RC1_READING_B_ROOT`` 覆盖），**保留不清理**。
* 不跑整套回归；只跑本 facade 需要的最小 owner 面。
* 需要 POSIX（AF_UNIX + fcntl）；原生 Windows 自动跳过（在 WSL/Linux 上运行）。
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
import phone_reading as pr  # noqa: E402
import private_daily_items as pdi  # noqa: E402

#: 夹具根：唯一创建、保留；不 rm -rf、不重建固定路径、不动共享目录。
BASE = Path(os.environ.get("NYAIRO_RC1_READING_B_ROOT") or "/tmp/nyaw05r.jdK3q")
SUBJECT = "test:rc1-phone-reading"

posix_only = unittest.skipIf(os.name == "nt", "需要 POSIX AF_UNIX 与 fcntl（在 WSL/Linux 上运行）")

PAGE_ONE = "第一页：她把昨晚买到的那盏灯写进本子里。"
PAGE_TWO = "第二页：灯光偏暖，桌面够用，笔尖有点涩。"
PAGE_THREE = "第三页：明天再读一遍这段。"
MATERIAL = "%%PAGE%%".join([PAGE_ONE, PAGE_TWO, PAGE_THREE])
NEW_MATERIAL = "%%PAGE%%".join(["新一页：这一段是替换后的第一段。", "新二页：这一段是替换后的第二段。"])


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
    """真实 Life Supply owner：一个 canonical db、一个 socket、Governance + Workspace + Artifact."""

    def __init__(self, root) -> None:
        from lifesupply.service.life_supply import LifeSupplyService, LifeSupplySocketServer
        from lifesupply.service.switches import FeatureSwitches

        self.root = Path(root)
        self.socket_path = self.root / "life-supply.sock"
        uid = os.geteuid()
        self.service = LifeSupplyService(self.root / "data", operator_uids={uid},
                                         service_uids={uid},
                                         switches=FeatureSwitches.all_on(),
                                         writer_instance="rc1-reading-test")
        self.server = LifeSupplySocketServer(str(self.socket_path), self.service)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def call(self, request: dict) -> dict:
        return _call(self.socket_path, request)

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.service.close()


class _InjectedLostCommit:
    """定点注入：把 ``commit_markdown`` 的答复改写成 UNKNOWN，其余请求原样转发真实 owner。"""

    def __init__(self, port: lap.LifeArtifactPort, *, forward: bool) -> None:
        self.forward = forward
        self.seen: list[dict] = []
        self._port = port
        self._real = port._call

    def __enter__(self) -> "_InjectedLostCommit":
        def wrapper(request):
            if request.get("op") != "commit_markdown":
                return self._real(request)
            self.seen.append(dict(request))
            if self.forward:  # 真实写入落盘，只是答复丢了
                self._real(request)
            return {"status": "UNKNOWN", "reason": "SERVICE_ERROR",
                    "detail": "injected: the answer was lost"}

        self._patcher = mock.patch.object(self._port, "_call", wrapper)
        self._patcher.start()
        return self

    def __exit__(self, *exc_info) -> bool:
        self._patcher.stop()
        return False


@posix_only
class ReadingOwnerTest(unittest.TestCase):
    """真实 owner + 显式 artifact 绑定。每个测试自建唯一夹具根，保留不清理。"""

    def setUp(self) -> None:
        self.root = _unique_root("rc1-reading-owner-")
        self.services = OwnerServices(self.root)
        self.addCleanup(self.services.stop)
        self.workspace_id = self._provision_workspace()
        self.grant = self._issue_artifact_grant()
        self.port = lap.LifeArtifactPort(str(self.services.socket_path), SUBJECT)
        self.items_artifact_id, _ = pdi.create_document_artifact(
            self.port, workspace_id=self.workspace_id, title="私人日常事项", grant_id=self.grant)
        material = self.port.create_artifact(
            workspace_id=self.workspace_id, title="操作人自己的阅读材料", content=MATERIAL,
            grant_id=self.grant,
            operation_key=lap.operation_key_for("test", "reading-material", SUBJECT))
        self.assertTrue(material.applied, material)
        self.reading_artifact_id = str(material.artifact_id)

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
                                     "operation_key": "rc1-reading:workspace:%s" % SUBJECT,
                                     "workspace": {"subject_id": SUBJECT}})
        self.assertEqual(answer.get("status"), "OK", answer)
        return str(answer["workspace"]["workspace_id"])

    def _issue_artifact_grant(self) -> str:
        return self._issue("COMMIT_MANAGED_ARTIFACT", "artifact:personal",
                           "workspace:%s" % self.workspace_id, "life-supply.artifact.v1",
                           "REUSABLE", "issue:artifact:%s" % SUBJECT)

    # -- helpers -----------------------------------------------------------------------

    def facade(self, port=None) -> pr.PhoneReading:
        return pr.PhoneReading(port or self.port,
                               reading_artifact_id=self.reading_artifact_id,
                               private_items_artifact_id=self.items_artifact_id,
                               workspace_id=self.workspace_id, grant_id=self.grant)

    def items(self) -> pdi.PrivateDailyItems:
        return pdi.PrivateDailyItems(self.port, self.items_artifact_id,
                                     workspace_id=self.workspace_id, grant_id=self.grant)

    def material_head(self) -> dict:
        return self.port.read_head(self.reading_artifact_id) or {}

    def pdi_version(self) -> int:
        head = self.port.read_head(self.items_artifact_id) or {}
        return int(head.get("version_number") or 0)

    def source_id(self) -> str:
        return pr.reading_source_id(self.reading_artifact_id)

    def private_head(self) -> str:
        return str(self.port.read_head(self.items_artifact_id)["version_id"])

    def current(self, facade=None) -> dict:
        answer = (facade or self.facade()).material()
        self.assertTrue(answer["ok"], answer)
        return answer["data"]

    def save(self, facade, page: int, key: str, *, revision: str = "",
             head: str = "") -> dict:
        return facade.save_position(page, revision or self.material_head()["version_id"],
                                    head or self.private_head(), key)

    # -- 1. the page is real, and GET writes nothing -------------------------------------

    def test_get_material_returns_the_real_page_and_writes_nothing(self):
        facade = self.facade()
        before_pdi, before_material = self.pdi_version(), self.material_head()["version_number"]
        before_items = self.items().items()

        answer = facade.material()
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(answer["status"], "applied")
        data = answer["data"]
        head = self.material_head()
        self.assertIs(data["available"], True)
        self.assertEqual(data["artifact_id"], self.reading_artifact_id)
        self.assertEqual(data["source_id"], self.source_id())
        self.assertEqual(data["source_revision"], head["version_id"])
        self.assertEqual(data["version_number"], head["version_number"])
        self.assertEqual((data["page"], data["pages"]), (1, 3))
        self.assertEqual(data["text"], PAGE_ONE)
        self.assertIs(data["content_available"], True)
        self.assertIsNone(data["bookmark"])
        self.assertEqual(data["annotations"], [])
        self.assertEqual(data["understanding"], "UNKNOWN")
        self.assertEqual(data["private_head"], self.private_head())
        self.assertEqual(data["private_items_status"], "available")

        # 纯读：两份 owner 文档都一个版本没动，也没有多出任何一条事项。
        self.assertEqual(self.pdi_version(), before_pdi)
        self.assertEqual(self.material_head()["version_number"], before_material)
        self.assertEqual(self.items().items(), before_items)

        second = facade.material(2)["data"]
        self.assertEqual(second["text"], PAGE_TWO)
        # 正文不外泄：当前页之外的正文一个字都不给。
        self.assertNotIn(PAGE_THREE, json.dumps(second, ensure_ascii=False))
        self.assertNotIn(PAGE_ONE, json.dumps(second, ensure_ascii=False))

        # 页边界 / bool 页码：拒绝，且仍然零写。
        out_of_range = facade.material(4)
        self.assertIs(out_of_range["ok"], False)
        self.assertEqual(out_of_range["reason_code"], "PAGE_NOT_PRESENT")
        self.assertEqual(facade.material(True)["reason_code"], "PAGE_INVALID")
        self.assertEqual(facade.material("2")["reason_code"], "PAGE_INVALID")
        self.assertEqual(self.pdi_version(), before_pdi)
        self.assertEqual(self.items().items(), before_items)

    def test_no_material_is_an_honest_unavailable(self):
        facade = pr.PhoneReading(self.port, reading_artifact_id="",
                                 private_items_artifact_id=self.items_artifact_id,
                                 workspace_id=self.workspace_id, grant_id=self.grant)
        before = self.pdi_version()
        answer = facade.material()
        self.assertIs(answer["ok"], False)
        self.assertEqual(answer["status"], "unavailable")
        self.assertEqual(answer["reason_code"], "READING_MATERIAL_UNAVAILABLE")
        self.assertIs(answer["data"]["available"], False)
        self.assertIs(answer["data"]["content_available"], False)
        self.assertIsNone(answer["data"]["bookmark"])
        self.assertEqual(answer["data"]["annotations"], [])
        self.assertEqual(self.pdi_version(), before)

    def test_page_marker_matches_the_marker_one_day_life_uses(self):
        source = (ROOT / "components" / "life" / "scripts" / "one_day_life.py").read_text(
            encoding="utf-8")
        self.assertIn('PAGE_MARKER = "%%PAGE%%"', source)
        self.assertEqual(pr.PAGE_MARKER, "%%PAGE%%")
        self.assertEqual(pr.reading_pages("只有一页"), ["只有一页"])

    # -- 2. one page read = one version; the last stop point is the one restored ----------

    def test_save_position_is_one_version_and_restores_the_last_stop_point(self):
        facade = self.facade()
        before_pdi = self.pdi_version()
        saved = self.save(facade, 2, "key:pos-2")
        self.assertTrue(saved["ok"], saved)
        self.assertEqual(saved["status"], "applied")
        data = saved["data"]
        self.assertEqual(data["page"], 2)
        self.assertEqual(data["source_revision"], self.material_head()["version_id"])
        self.assertEqual(data["obtained"], {"pages": [2], "chars": len(PAGE_TWO)})
        self.assertEqual(data["understanding"], "UNKNOWN")
        self.assertIn("page-2", data["receipt_ref"])

        items = self.items()
        acquisitions = items.items(pdi.ITEM_SOURCE_ACQUISITION)
        positions = items.items(pdi.ITEM_READING_POSITION)
        # 一次阅读一个版本：取得证据与位置同时落地，没有"半成功"。
        self.assertEqual(self.pdi_version(), before_pdi + 1)
        self.assertEqual(len(acquisitions), 1)
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["position"], 2)
        self.assertEqual(positions[0]["acquisition_id"], acquisitions[0]["item_id"])
        self.assertIs(positions[0]["needs_reread"], False)

        # 默认恢复的是"最近一次显式保存的停点"，不是最高页。
        current = self.current(facade)
        self.assertEqual(current["page"], 2)
        self.assertEqual(current["text"], PAGE_TWO)
        self.assertEqual(current["bookmark"]["page"], 2)

        # 回到第 1 页并保存：默认恢复跟着变成第 1 页。
        back = self.save(facade, 1, "key:pos-1")
        self.assertEqual(back["data"]["page"], 1)
        restored = self.current(facade)
        self.assertEqual(restored["page"], 1)
        self.assertEqual(restored["text"], PAGE_ONE)
        self.assertEqual(restored["bookmark"]["page"], 1)
        # 最高页 helper 会说是 2：它不是"最近停点"的替身。
        document = self.items().load()
        self.assertEqual(
            pdi.reading_position_page(document, source_id=self.source_id()), 2)
        # 换一个 port/facade（等于重开）仍然从 owner 读到同一个停点。
        reopened = self.facade(lap.LifeArtifactPort(str(self.services.socket_path), SUBJECT))
        self.assertEqual(self.current(reopened)["page"], 1)

    # -- 3. one intent, one version: replaying a key never mints a second one ------------

    def test_replaying_an_operation_key_never_mints_a_second_version(self):
        facade = self.facade()
        head = self.material_head()["version_id"]
        stale_head = self.private_head()
        first = facade.save_position(1, head, stale_head, "key:replay-position")
        self.assertEqual(first["status"], "applied")
        after_first = self.pdi_version()

        # 原键 + 已经过期的 expected_head：重放必须回读成功，且不能再落一版。
        replay = facade.save_position(1, head, stale_head, "key:replay-position")
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(replay["status"], "already_applied")
        self.assertEqual(replay["data"]["page"], 1)
        self.assertEqual(replay["data"]["detail"], "OPERATION_KEY_REPLAY")
        self.assertEqual(self.pdi_version(), after_first)
        self.assertEqual(len(self.items().items(pdi.ITEM_READING_POSITION)), 1)

        # 材料替换：同一个键 + 旧 revision 重放也是 already_applied，不是第二个版本。
        replacement = MATERIAL + "%%PAGE%%" + "第四页：补在最后的一页。"
        applied = facade.replace_material(replacement, head, "key:replay-material")
        self.assertEqual(applied["status"], "applied", applied)
        self.assertEqual(applied["data"]["pages"], 4)
        version = self.material_head()["version_number"]
        again = facade.replace_material(replacement, head, "key:replay-material")
        self.assertTrue(again["ok"], again)
        self.assertEqual(again["status"], "already_applied")
        self.assertEqual(again["data"]["detail"], "OPERATION_KEY_REPLAY")
        self.assertEqual(self.material_head()["version_number"], version)
        # 旧停点还在，而且明确要重看。
        self.assertEqual(again["data"]["bookmark"]["page"], 1)
        self.assertIs(again["data"]["bookmark"]["needs_reread"], True)

        # owner 侧只多了一个材料版本（旧内容留在版本历史里）。
        self.assertEqual(self.material_head()["version_number"], 2)

    def test_one_key_cannot_be_reused_across_kinds_or_pages(self):
        facade = self.facade()
        saved = self.save(facade, 1, "key:shared")
        self.assertEqual(saved["status"], "applied")
        after = self.pdi_version()

        # 同一个键换 kind：写 note 被拒，且一条 memo 都不多。
        note = facade.annotate("note", 1, self.material_head()["version_id"],
                               self.private_head(), "key:shared", title="标题", body="正文")
        self.assertIs(note["ok"], False)
        self.assertEqual(note["reason_code"], "READING_OPERATION_KEY_REUSED")
        self.assertEqual(note["data"]["recorded_action"], "position")
        self.assertEqual(self.pdi_version(), after)
        self.assertEqual(self.items().items(pdi.ITEM_MEMO), [])

        # 同一个键换页：同样拒绝。
        other = facade.save_position(2, self.material_head()["version_id"],
                                     self.private_head(), "key:shared")
        self.assertIs(other["ok"], False)
        self.assertEqual(other["reason_code"], "READING_OPERATION_KEY_REUSED")
        self.assertEqual(self.pdi_version(), after)

        # 材料替换用过的键，不能拿来当"读了一页"的键。
        replaced = facade.replace_material(NEW_MATERIAL, self.material_head()["version_id"],
                                           "key:material-only")
        self.assertEqual(replaced["status"], "applied")
        again = facade.save_position(1, replaced["data"]["source_revision"],
                                     replaced["data"]["private_head"], "key:material-only")
        self.assertIs(again["ok"], False)
        self.assertEqual(again["reason_code"], "READING_OPERATION_KEY_REUSED")
        self.assertEqual(again["data"]["recorded_artifact"], self.reading_artifact_id)
        self.assertEqual(again["data"]["recorded_action"], "COMMIT_MARKDOWN")

    # -- 4. annotations need a real acquired page, and keep their anchor ------------------

    def test_annotation_needs_a_real_acquired_page_and_keeps_its_anchor(self):
        facade = self.facade()
        head = self.material_head()["version_id"]
        head_now = self.private_head()

        missing = facade.annotate("note", 3, head, head_now, "key:note-page-3",
                                  title="第三页", body="先记一句")
        self.assertIs(missing["ok"], False)
        self.assertEqual(missing["reason_code"], "PAGE_NOT_ACQUIRED")
        self.assertEqual(self.pdi_version(), self.private_head() and self.pdi_version())

        saved = facade.save_position(3, head, head_now, "key:position-page-3")
        self.assertEqual(saved["status"], "applied")
        note = facade.annotate("note", 3, head, saved["data"]["private_head"],
                               "key:note-page-3", title="第三页", body="先记一句")
        self.assertTrue(note["ok"], note)
        self.assertEqual(note["status"], "applied")
        self.assertEqual(note["data"]["kind"], "note")
        self.assertEqual(note["data"]["anchor"],
                         {"source_id": self.source_id(),
                          "source_revision": head, "page": 3})

        # 锚点进元数据白名单；正文仍然只在 full_text 里。
        memo_id = note["data"]["id"]
        rows = self.items().items(pdi.ITEM_MEMO)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["reading_anchor"]["page"], 3)
        self.assertIs(rows[0]["has_text"], True)
        self.assertNotIn("body", rows[0])
        self.assertNotIn("先记一句", json.dumps(self.items().metadata_view(), ensure_ascii=False))
        self.assertIs(self.items().metadata_view()["private_text_included"], False)

        # 旧 note 编辑不抹锚点。
        self.items().revise_memo(memo_id, body="改成另一句")
        self.assertEqual(self.items().item(memo_id)["reading_anchor"]["page"], 3)
        self.assertEqual(self.items().full_text(memo_id), "改成另一句")

        # 她自己的疑问是**另一种**类型，不是把所有批注都写成"没看懂"。
        unclear = facade.annotate("unclear", 1, head, self.private_head(), "key:unclear-1",
                                  note="第一页这段没看懂")
        self.assertIs(unclear["ok"], False)
        self.assertEqual(unclear["reason_code"], "PAGE_NOT_ACQUIRED")
        saved_one = facade.save_position(1, head, self.private_head(), "key:position-page-1")
        unclear = facade.annotate("unclear", 1, head, saved_one["data"]["private_head"],
                                  "key:unclear-1", note="第一页这段没看懂")
        self.assertEqual(unclear["status"], "applied")
        self.assertEqual(unclear["data"]["kind"], "unclear")
        self.assertEqual(len(self.items().items(pdi.ITEM_UNCLEAR_MARKER)), 1)

        # 字段按 kind 白名单。
        self.assertEqual(facade.annotate("note", 1, head, self.private_head(), "key:note-note",
                                         title="t", body="b", note="n")["reason_code"],
                         "ANNOTATION_FIELD_NOT_ALLOWED")
        self.assertEqual(
            facade.annotate("unclear", 1, head, self.private_head(), "key:unclear-body",
                            note="n", body="b")["reason_code"],
            "ANNOTATION_FIELD_NOT_ALLOWED")
        self.assertEqual(facade.annotate("understood", 1, head, self.private_head(), "key:kind",
                                         note="n")["reason_code"], "ANNOTATION_KIND_INVALID")

        # 阅读页把两种批注分别给出来，正文只在这个私人读取面里。
        annotations = self.current(facade)["annotations"]
        kinds = sorted(row["kind"] for row in annotations)
        self.assertEqual(kinds, ["note", "unclear"])
        note_row = [row for row in annotations if row["kind"] == "note"][0]
        self.assertEqual(note_row["body"], "改成另一句")
        self.assertEqual(note_row["page"], 3)
        self.assertIs(note_row["stale"], False)
        self.assertEqual(self.current(facade)["understanding"], "UNKNOWN")

    # -- 5. a new material version makes the old bookmark ask for a re-read --------------

    def test_new_material_version_marks_the_old_stop_point_for_a_reread(self):
        facade = self.facade()
        old_revision = self.material_head()["version_id"]
        saved = self.save(facade, 2, "key:v1-position-2")
        note = facade.annotate("note", 2, old_revision, saved["data"]["private_head"],
                               "key:v1-note-2", title="旧版第二页", body="当时记的")
        self.assertEqual(note["status"], "applied")

        replaced = facade.replace_material(NEW_MATERIAL, old_revision, "key:v1-replace")
        self.assertEqual(replaced["status"], "applied", replaced)
        new_revision = replaced["data"]["source_revision"]
        self.assertNotEqual(new_revision, old_revision)
        self.assertEqual(replaced["data"]["pages"], 2)
        # 旧停点仍然在，并且明确说"这一版已经变了，要重看"。
        self.assertEqual(replaced["data"]["bookmark"]["page"], 2)
        self.assertEqual(replaced["data"]["bookmark"]["source_revision"], old_revision)
        self.assertIs(replaced["data"]["bookmark"]["needs_reread"], True)

        # GET 不把旧页偷偷搬到新版：默认回第 1 页，同时如实报旧停点要重看。
        current = self.current(facade)
        self.assertEqual(current["page"], 1)
        self.assertEqual(current["text"], NEW_MATERIAL.split(pr.PAGE_MARKER)[0])
        self.assertIs(current["bookmark"]["needs_reread"], True)
        self.assertEqual(current["bookmark"]["page"], 2)
        self.assertEqual(current["bookmark"]["needs_reread_reason"], "SOURCE_REVISION_ADVANCED")

        # 旧版上的批注保留自己的版本，并标成过期。
        rows = current["annotations"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_revision"], old_revision)
        self.assertIs(rows[0]["stale"], True)
        self.assertIs(rows[0]["needs_reread"], True)

        # 在旧版本上继续写批注：必须重看，不许静默指到新版同一页。
        stale = facade.annotate("note", 2, old_revision, current["private_head"],
                                "key:v1-note-again", title="旧版", body="旧版第二页")
        self.assertIs(stale["ok"], False)
        self.assertEqual(stale["reason_code"], "READING_REVISION_CONFLICT")

        # 新版停点 + 新版批注都能走通。
        new_saved = facade.save_position(2, new_revision, current["private_head"],
                                        "key:v2-position-2")
        self.assertEqual(new_saved["status"], "applied")
        new_note = facade.annotate("note", 2, new_revision, new_saved["data"]["private_head"],
                                   "key:v2-note-2", title="新版第二页", body="新版记的")
        self.assertEqual(new_note["status"], "applied")
        fresh = self.current(facade)
        self.assertEqual(fresh["page"], 2)
        fresh_rows = [row for row in fresh["annotations"] if row["source_revision"] == new_revision]
        self.assertEqual(len(fresh_rows), 1)
        self.assertIs(fresh_rows[0]["stale"], False)

    # -- 6. revisions, heads, pages and import bounds are refused without writing --------

    def test_revision_head_page_and_import_bounds_are_refused_without_writing(self):
        facade = self.facade()
        head = self.material_head()["version_id"]
        head_now = self.private_head()
        pdi_before = self.pdi_version()
        material_before = self.material_head()["version_number"]

        stale_revision = facade.save_position(1, "%s@deadbeef" % self.source_id(),
                                              head_now, "key:stale-revision")
        self.assertEqual(stale_revision["reason_code"], "READING_REVISION_CONFLICT")
        stale_head = facade.save_position(1, head, "life:not-the-head", "key:stale-head")
        self.assertEqual(stale_head["reason_code"], "READING_HEAD_CONFLICT")
        self.assertEqual(stale_head["data"]["private_head"], head_now)
        for page in (0, -1, 4, True, "2", 2.0):
            answer = facade.save_position(page, head, head_now, "key:bad-page")
            self.assertIs(answer["ok"], False, (page, answer))
            self.assertIn(answer["reason_code"], ("PAGE_INVALID", "PAGE_NOT_PRESENT"), page)
        self.assertEqual(self.pdi_version(), pdi_before)

        for content, reason in (("", "CONTENT_EMPTY"), ("   ", "CONTENT_EMPTY"),
                                (PAGE_ONE + "%%PAGE%%" + "   ", "BLANK_PAGE"),
                                ("x" * 24001, "CONTENT_TOO_LARGE"),
                                ("y" * 12001, "PAGE_TOO_LARGE"),
                                ("%%PAGE%%".join("p%d" % index for index in range(65)),
                                 "TOO_MANY_PAGES")):
            answer = facade.replace_material(content, head, "key:import-%s" % reason)
            self.assertIs(answer["ok"], False, reason)
            self.assertEqual(answer["reason_code"], reason)
        self.assertEqual(self.material_head()["version_number"], material_before)

        # 没有 grant 就没有写路径，但读路径照旧。
        reader = pr.PhoneReading(self.port, reading_artifact_id=self.reading_artifact_id,
                                 private_items_artifact_id=self.items_artifact_id,
                                 workspace_id=self.workspace_id, grant_id=None)
        self.assertTrue(reader.material()["ok"])
        refused = reader.save_position(1, head, head_now, "key:no-grant")
        self.assertEqual(refused["reason_code"], "READING_GRANT_REQUIRED")
        self.assertEqual(self.pdi_version(), pdi_before)

    # -- 7. UNKNOWN is reported, never resent under a new key ----------------------------

    def test_unknown_answer_that_landed_is_reconciled_and_a_lost_one_stays_unknown(self):
        facade = self.facade()
        head = self.material_head()["version_id"]
        head_now = self.private_head()
        before = self.pdi_version()

        # (a) 写入确实落盘，只是答复丢了：靠 owner 自己的回执对账成 applied。
        with _InjectedLostCommit(self.port, forward=True) as injected:
            reconciled = facade.save_position(1, head, head_now, "key:lost-answer")
        self.assertEqual(len(injected.seen), 1)
        self.assertTrue(reconciled["ok"], reconciled)
        self.assertEqual(reconciled["status"], "applied")
        self.assertEqual(reconciled["data"]["detail"], "RECONCILED_AFTER_UNKNOWN")
        self.assertEqual(self.pdi_version(), before + 1)
        self.assertEqual(len(self.items().items(pdi.ITEM_READING_POSITION)), 1)
        # 原键再问一次仍然回读成功，且不新落一版。
        replay = facade.save_position(1, head, head_now, "key:lost-answer")
        self.assertEqual(replay["status"], "already_applied")

        # (b) 连写入都没发生：诚实 unknown（不是失败、不是成功），同一个键可以直接重试。
        head_now = self.private_head()
        before = self.pdi_version()
        with _InjectedLostCommit(self.port, forward=False) as injected:
            unknown = facade.save_position(2, head, head_now, "key:never-landed")
        self.assertEqual(len(injected.seen), 1)
        self.assertIs(unknown["ok"], False)
        self.assertEqual(unknown["status"], "unknown")
        self.assertEqual(unknown["reason_code"], "READING_OPERATION_UNKNOWN")
        self.assertIs(unknown["data"]["retryable"], True)
        self.assertEqual(self.pdi_version(), before)

        retried = facade.save_position(2, head, head_now, "key:never-landed")
        self.assertTrue(retried["ok"], retried)
        self.assertEqual(retried["status"], "applied")
        self.assertEqual(retried["data"]["page"], 2)
        self.assertEqual(self.pdi_version(), before + 1)

        # 未知的键查出来是 typed not_found，不是空成功。
        missing = facade.operation("key:nobody-used-this")
        self.assertIs(missing["ok"], False)
        self.assertEqual(missing["status"], "not_found")
        self.assertEqual(missing["reason_code"], "READING_OPERATION_NOT_FOUND")

    def test_operation_query_reads_the_owner_and_keeps_unknown_distinct(self):
        facade = self.facade()
        saved = self.save(facade, 1, "key:query-me")
        landed = facade.operation("key:query-me")
        self.assertTrue(landed["ok"], landed)
        self.assertEqual(landed["data"]["action"], "position")
        self.assertEqual(landed["data"]["record"]["page"], 1)
        self.assertEqual(landed["data"]["record"]["position_id"],
                         saved["data"]["position_id"])


        # 材料键不在私人事项文档里，只能问 owner；owner 说不清时 query 报 unknown，
        # 既不当成成功也不当成不存在（旧材料键换版后仍能描述原效果）。
        imported = facade.replace_material(NEW_MATERIAL, self.material_head()["version_id"],
                                          "key:query-material")
        self.assertEqual(imported["status"], "applied", imported)
        described = facade.operation("key:query-material")
        self.assertTrue(described["ok"], described)
        self.assertEqual(described["data"]["action"], "material")
        self.assertEqual(described["data"]["version_id"],
                         imported["data"]["source_revision"])
        self.assertIs(described["data"]["revision_current"], True)
        self.assertIs(described["data"]["owner_operation"]["artifact_is_reading_material"],
                      True)

        before = (self.pdi_version(), self.material_head()["version_number"])
        real = self.port._call

        def wrapper(request):
            if request.get("op") == "read_effect_receipt":
                return {"status": "NOT_FOUND", "reason": "NO_RECEIPT"}
            if request.get("op") == "lookup_operation":
                return {"status": "UNKNOWN", "reason": "STORE_UNAVAILABLE"}
            return real(request)

        with mock.patch.object(self.port, "_call", wrapper):
            vague = facade.operation("key:query-material")
        self.assertIs(vague["ok"], False)
        self.assertEqual(vague["status"], "unknown")
        self.assertEqual(vague["reason_code"], "READING_OPERATION_UNKNOWN")
        # 查询没有写任何东西。
        self.assertEqual((self.pdi_version(), self.material_head()["version_number"]), before)

    # -- 8. the payload is part of the binding, not just the action ----------------------

    def test_the_same_key_with_a_changed_payload_is_refused_not_replayed(self):
        facade = self.facade()
        head = self.material_head()["version_id"]
        saved = facade.save_position(1, head, self.private_head(), "key:payload-position")
        self.assertEqual(saved["status"], "applied")

        note = facade.annotate("note", 1, head, self.private_head(), "key:payload-note",
                               title="标题", body="正文一")
        self.assertEqual(note["status"], "applied")
        after_note = self.pdi_version()

        changed_body = facade.annotate("note", 1, head, self.private_head(),
                                       "key:payload-note", title="标题", body="正文二")
        self.assertIs(changed_body["ok"], False)
        self.assertEqual(changed_body["reason_code"], "READING_OPERATION_KEY_REUSED")
        self.assertEqual(changed_body["data"]["recorded_action"], "annotation")
        changed_title = facade.annotate("note", 1, head, self.private_head(),
                                        "key:payload-note", title="另一个标题", body="正文一")
        self.assertEqual(changed_title["reason_code"], "READING_OPERATION_KEY_REUSED")
        self.assertEqual(self.pdi_version(), after_note)
        self.assertEqual(self.items().full_text(note["data"]["id"]), "正文一")
        self.assertEqual(len(self.items().items(pdi.ITEM_MEMO)), 1)

        unclear = facade.annotate("unclear", 1, head, self.private_head(), "key:payload-unclear",
                                  note="看不懂一")
        self.assertEqual(unclear["status"], "applied")
        after_unclear = self.pdi_version()
        changed_note = facade.annotate("unclear", 1, head, self.private_head(),
                                       "key:payload-unclear", note="看不懂二")
        self.assertEqual(changed_note["reason_code"], "READING_OPERATION_KEY_REUSED")
        self.assertEqual(self.pdi_version(), after_unclear)
        self.assertEqual(self.items().full_text(unclear["data"]["id"]), "看不懂一")

    # -- 9. identical questions on different pages stay separate records -----------------

    def test_the_same_question_on_two_pages_keeps_two_records(self):
        facade = self.facade()
        head = self.material_head()["version_id"]
        first = facade.save_position(1, head, self.private_head(), "key:two-pages-pos-1")
        second = facade.save_position(2, head, first["data"]["private_head"],
                                      "key:two-pages-pos-2")
        self.assertEqual(second["status"], "applied")

        same_words = "这一段我都没看懂"
        page_one = facade.annotate("unclear", 1, head, second["data"]["private_head"],
                                   "key:unclear-page-1", note=same_words)
        page_two = facade.annotate("unclear", 2, head, page_one["data"]["private_head"],
                                   "key:unclear-page-2", note=same_words)
        self.assertEqual(page_one["status"], "applied", page_one)
        self.assertEqual(page_two["status"], "applied", page_two)
        self.assertNotEqual(page_one["data"]["id"], page_two["data"]["id"])
        markers = self.items().items(pdi.ITEM_UNCLEAR_MARKER)
        self.assertEqual(len(markers), 2)
        self.assertEqual({row["position"] for row in markers}, {1, 2})
        self.assertEqual({row["item_id"] for row in markers},
                         {page_one["data"]["id"], page_two["data"]["id"]})

        # 查询与重放的身份都精确到条目与页。
        self.assertEqual(facade.operation("key:unclear-page-1")["data"]["item_id"],
                         page_one["data"]["id"])
        self.assertEqual(facade.operation("key:unclear-page-1")["data"]["page"], 1)
        self.assertEqual(facade.operation("key:unclear-page-2")["data"]["item_id"],
                         page_two["data"]["id"])
        self.assertEqual(facade.operation("key:unclear-page-2")["data"]["page"], 2)
        replay = facade.annotate("unclear", 2, head, second["data"]["private_head"],
                                 "key:unclear-page-2", note=same_words)
        self.assertEqual(replay["status"], "already_applied")
        self.assertEqual(replay["data"]["item_id"], page_two["data"]["id"])
        annotations = self.current(facade)["annotations"]
        self.assertEqual(sorted(row["page"] for row in annotations
                                if row["kind"] == "unclear"), [1, 2])

    # -- 10. a borrowed key cannot import the material, even with identical text --------

    def test_a_borrowed_key_cannot_import_the_material_with_identical_content(self):
        facade = self.facade()
        head = self.material_head()["version_id"]
        saved = facade.save_position(1, head, self.private_head(), "key:borrow-position")
        self.assertEqual(saved["status"], "applied")
        note = facade.annotate("note", 1, head, saved["data"]["private_head"],
                               "key:borrow-note", title="标题", body="正文")
        self.assertEqual(note["status"], "applied")
        version_before = self.material_head()["version_number"]

        # 当前 head 的正文和这次导入请求**一字不差**，但键属于停点：仍然是 REUSED，零写。
        borrowed = facade.replace_material(MATERIAL, head, "key:borrow-position")
        self.assertIs(borrowed["ok"], False)
        self.assertEqual(borrowed["reason_code"], "READING_OPERATION_KEY_REUSED")
        self.assertEqual(borrowed["data"]["recorded_action"], "position")
        self.assertEqual(borrowed["data"]["recorded_artifact"],
                         self.items_artifact_id)
        borrowed_note = facade.replace_material(MATERIAL, head, "key:borrow-note")
        self.assertEqual(borrowed_note["reason_code"], "READING_OPERATION_KEY_REUSED")
        self.assertEqual(borrowed_note["data"]["recorded_action"], "annotation")
        self.assertEqual(self.material_head()["version_number"], version_before)
        borrowed_query = facade.operation("key:borrow-position")
        self.assertIs(borrowed_query["data"]["owner_operation"]
                      ["artifact_is_reading_material"], False)
        self.assertEqual(borrowed_query["data"]["owner_operation"]["artifact_id"],
                         self.items_artifact_id)

    # -- 11. an old material key still describes its own original version ---------------

    def test_an_old_material_key_still_describes_its_original_version(self):
        facade = self.facade()
        first_revision = self.material_head()["version_id"]
        second_text = MATERIAL + "%%PAGE%%" + "第四页：补在最后的一页。"
        third_text = NEW_MATERIAL


        second = facade.replace_material(second_text, first_revision, "key:material-v2")
        self.assertEqual(second["status"], "applied", second)
        second_revision = second["data"]["source_revision"]
        third = facade.replace_material(third_text, second_revision, "key:material-v3")
        self.assertEqual(third["status"], "applied", third)
        third_revision = third["data"]["source_revision"]
        self.assertNotEqual(third_revision, second_revision)

        described = facade.operation("key:material-v2")
        self.assertTrue(described["ok"], described)
        self.assertEqual(described["data"]["action"], "material")
        self.assertEqual(described["data"]["version_id"], second_revision)
        self.assertEqual(described["data"]["version_number"], 2)
        self.assertEqual(described["data"]["parent_version_id"], first_revision)
        self.assertIs(described["data"]["revision_current"], False)
        self.assertEqual(described["data"]["material_current_revision"], third_revision)

        # 原键 + 原 expected_revision + 原正文：回读成功，但**不**声称当前正文还是它。
        replay = facade.replace_material(second_text, first_revision, "key:material-v2")
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(replay["status"], "already_applied")
        self.assertEqual(replay["data"]["detail"], "OPERATION_KEY_REPLAY")
        self.assertEqual(replay["data"]["source_revision"], second_revision)
        self.assertIs(replay["data"]["revision_current"], False)
        self.assertEqual(replay["data"]["material_current_revision"], third_revision)
        self.assertEqual(self.material_head()["version_number"], 3)

        # 同一个键换正文 / 换 expected_revision 都是换请求：REUSED，不冒充成功。
        changed = facade.replace_material(second_text + "%%PAGE%%" + "第五页。",
                                          first_revision, "key:material-v2")
        self.assertEqual(changed["reason_code"], "READING_OPERATION_KEY_REUSED")
        rebound = facade.replace_material(second_text, second_revision, "key:material-v2")
        self.assertEqual(rebound["reason_code"], "READING_OPERATION_KEY_REUSED")
        self.assertEqual(self.material_head()["version_number"], 3)

    # -- 12. a head that moves mid-write is refused, not silently accepted --------------

    def test_a_head_that_moves_after_the_precheck_is_still_refused(self):
        facade = self.facade()
        head = self.material_head()["version_id"]
        expected = self.private_head()
        version_before = self.pdi_version()
        other = pdi.PrivateDailyItems(
            lap.LifeArtifactPort(str(self.services.socket_path), SUBJECT),
            self.items_artifact_id, workspace_id=self.workspace_id, grant_id=self.grant)

        real = self.port._call
        interleaved: list[str] = []

        def wrapper(request):
            if request.get("op") == "commit_markdown" and not interleaved:
                interleaved.append(str(request.get("operation_key")))
                # 真实交错：另一次真实提交在本次提交到达 owner 之前落盘。
                other.add_memo(title="别人的便签", body="抢在中间写的一张")
            return real(request)

        with mock.patch.object(self.port, "_call", wrapper):
            refused = facade.save_position(1, head, expected, "key:racing-position")
        self.assertEqual(len(interleaved), 1)
        self.assertIs(refused["ok"], False)
        self.assertEqual(refused["reason_code"], "READING_HEAD_CONFLICT")
        self.assertEqual(refused["data"]["expected_head"], expected)
        self.assertNotEqual(refused["data"]["private_head"], expected)
        # 只有交错那一次写落了盘：我们的取得/位置一条都没有。
        self.assertEqual(self.pdi_version(), version_before + 1)
        self.assertEqual(self.items().items(pdi.ITEM_SOURCE_ACQUISITION), [])
        self.assertEqual(self.items().items(pdi.ITEM_READING_POSITION), [])
        # 同一个键用新鲜 head 重试仍可正常落地（拒绝不等于把键废掉）。
        retried = facade.save_position(1, head, self.private_head(), "key:racing-position")
        self.assertEqual(retried["status"], "applied", retried)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

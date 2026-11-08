#!/usr/bin/env python3
"""在世界里的读书位读完一页，留下一条**属于她自己的**批注。

这件功能补的是"读完就完了"这个缺口：她去书店阅读角，选 ``stay:reading_point:READ_HERE:600``，
待满 600 秒，``STAY_AT_OBSERVED_PLACE`` 按时间 + 世界观察正常完成——然后她的私人日常事项
文档里多一条 ``memo``，形状和读**私人材料**时的批注完全一样（同一份取得记录、同一个停点、
同一个 ``reading_anchor``）。世界里的那段文字只是一个普通来源：``source_id="world:reading_point"``，
``source_revision`` 是这段文字的稳定指纹。

四条合同，全部用真实 owner / 真实 World 服务 / 注入时钟，模型只有替身：

1. 有文字、停留完成 → 私人事项里多一条 memo，形状与私人材料的批注一致；
2. 地点没有文字 → 什么都不写，也不报错；
3. 写作失败（content writer 抛错）→ 停留**仍然是完成的**，批注如实记为没写成，文档一字节没改；
4. 同一段文字重复读 → 不会写出第二条一模一样的批注（既有 operation key 幂等）。

再加一条落盘合同（5）：这份形状报告要真的进 wake journal——成功与各种没写成的原因
都能从盘上的记录看出来；没有读书这件事的轮次，记录里不许凭空多出这个键。

夹具以组合方式复用 ``tests/test_world_neighborhood_life.py`` 的 ``NeighborhoodFixture``（同一个
真实 runtime / World 服务 / 注入时钟），只把世界包复制到临时目录、给 ``reading_point`` 添一段
``reading`` 文字——仓库里的示例包本身不改。所有 home 落在 ``/tmp/nyaw05r.jdK3q/`` 并保留。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge"),
              str(ROOT / "components" / "supply"), str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from test_one_day_life import posix_only  # noqa: E402
import autonomous_life_runner as alr  # noqa: E402
import private_daily_items as pdi  # noqa: E402
import test_world_daily_life_continuous as continuous  # noqa: E402
import test_world_neighborhood_life as nb  # noqa: E402

#: 书店阅读角自己写的一页（≤400 字符，与世界包 content.json 的校验同一口径）。
READING_TEXT = (
    "窗边的旧书摊开在阅读桌上，第一页只有一句话：雨停之前，不必急着回家。"
    "书页边上有前一个读者留下的铅笔线，淡淡的，像是替谁把这句话又读了一遍。"
)
WRITER_TITLE = "窗边的旧书"
WRITER_BODY = "她读到那句“雨停之前，不必急着回家”，觉得可以先记下来。"

#: 她的出行计划：走到阅读角，在那里读一页。
STROLL = (
    ("go", "street_front"),
    ("go", "reading_point"),
    ("stay", None),
)


def _package_with_reading(reading):
    """复制示例世界包，并给 ``reading_point`` 添上（或去掉）那段文字。"""

    nb.FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    base = Path(tempfile.mkdtemp(prefix="world-reading-pkg-", dir=str(nb.FIXTURE_ROOT)))
    package = base / "package"
    shutil.copytree(continuous.WORLD_PACKAGE, package)
    content_path = package / "content.json"
    doc = json.loads(content_path.read_text(encoding="utf-8"))
    entry = doc["entries"].setdefault("reading_point", {})
    if reading is None:
        entry.pop("reading", None)
    else:
        entry["reading"] = reading
    entry.setdefault("title", "书店阅读角")
    content_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    # 世界包自己校验 MANIFEST 的 SHA256：改了 content.json 就把它的新摘要写回去，
    # 否则运行时会拒绝加载（"被篡改的世界"）。只动这一条，别的文件不碰。
    manifest_path = package / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"]["content.json"] = hashlib.sha256(content_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    return package


class ReadingNeighborhoodFixture(nb.NeighborhoodFixture):
    """同一个真实街区实例，只是把世界包换成本次临时复制出来的那份。"""

    def __init__(self, *, reading=READING_TEXT) -> None:
        self._package = _package_with_reading(reading)
        saved = continuous.WORLD_PACKAGE
        continuous.WORLD_PACKAGE = self._package
        try:
            super().__init__()
        finally:
            continuous.WORLD_PACKAGE = saved


class ReadHereCognition(nb.ScriptedNeighborhoodCognition):
    """在阅读角选 ``READ_HERE`` 那一项，而不是同一地点也提供的 ``BROWSE_BOOKS``。"""

    def _choose(self, candidates, facts, here):
        running = next((candidate for candidate in candidates
                        if "CONTINUE" in self._legal(candidate)), None)
        if running is None and self.next_hop == ("stay", None):
            for candidate in candidates:
                if "START" not in self._legal(candidate):
                    continue
                if candidate.get("candidate_kind") not in nb.STARTABLE_KINDS:
                    continue
                if any(":READ_HERE:" in ref for ref in self._refs(candidate)):
                    self.index += 1
                    return (candidate, "START", "在这里读一页")
        return super()._choose(candidates, facts, here)


class StubReadingResponse:
    """内容写入的替身：只有真实模型才会做的那个请求，形状与 ``ModelReadingResponse`` 一致。"""

    model_id = "stub:world-reading"
    model_version = "world-reading-stub-v1"

    def __init__(self, *, kind="note", fail=False) -> None:
        self.kind = kind
        self.fail = fail
        self.calls: list[dict] = []

    def respond(self, context, *, observed_at):
        self.calls.append({"context": dict(context), "observed_at": observed_at})
        if self.fail:
            raise RuntimeError("the stand-in content writer refused")
        if self.kind == "note":
            return {"kind": "note", "title": WRITER_TITLE, "body": WRITER_BODY}
        if self.kind == "no_change":
            return {"kind": "no_change"}
        return {"kind": "unclear", "note": "这一页没有完全读懂。"}


def _items(document, item_type):
    return [item for item in (document.get("items") or {}).values()
            if isinstance(item, dict) and str(item.get("item_type") or "") == item_type]


def _document(field, doc_id):
    """她自己的私人事项文档，从真实 owner 读回来再解开。"""

    return pdi.decode_document(field.base.content(doc_id))


@posix_only
class WorldReadingAnnotationTest(unittest.TestCase):
    """读完世界里的文字之后，留下（或者如实说没留下）一条自己的批注。"""

    def _fixture(self, *, reading=READING_TEXT, writer=None):
        field = ReadingNeighborhoodFixture(reading=reading)
        self.addCleanup(field.close)
        field.use(ReadHereCognition(plan=STROLL))
        field.cycle.reading_response_writer = writer or StubReadingResponse()
        return field

    def _walk_to_reading_corner(self, field) -> None:
        """出门走到书店阅读角，并把时钟推到它开门之后（示例包：10–20 点）。"""

        field.travel("street_front")
        field.clock.advance(4 * 3600, because="morning-to-opening")
        field.travel("reading_point")
        self.assertEqual(field.current_place(), "reading_point")
        # 走到这里时，目录自己的事实说她正站在一个有文字的地点上。
        self.assertTrue(field.cycle._place_reading_text("reading_point"))

    def _read_once(self, field, trigger: str) -> dict:
        handle = field.start_stay("%s:start" % trigger)
        self.assertEqual(handle["stay"].get("affordance"), "READ_HERE", handle["stay"])
        return field.finish_stay(handle)

    # -- 1) 有文字 → 一条 memo，形状和私人材料一致 -------------------------------

    def test_a_finished_read_leaves_one_note_in_her_own_private_items(self):
        field = self._fixture()
        doc_id = field.data["private_items_artifact_id"]
        self.assertTrue(doc_id, "the instance must carry a private-items document")
        self.assertEqual(_items(_document(field, doc_id), "memo"), [])

        self._walk_to_reading_corner(field)
        ended = self._read_once(field, "world-reading:first")

        # 停留本身照旧完成：这是完成**之后**的后果，不是完成条件。
        self.assertEqual((ended.get("stay") or {}).get("phase"), "END", ended)
        self.assertEqual(ended.get("progress_state"), "COMPLETION_ELIGIBLE", ended)
        reading = dict(ended.get("world_reading") or {})
        self.assertEqual(reading.get("state"), "APPLIED", reading)
        self.assertEqual(reading.get("item_type"), "memo", reading)

        document = _document(field, doc_id)
        memos = _items(document, "memo")
        self.assertEqual(len(memos), 1, memos)
        memo = memos[0]
        self.assertEqual(memo.get("title"), WRITER_TITLE, memo)
        self.assertEqual(memo.get("body"), WRITER_BODY, memo)
        anchor = dict(memo.get("reading_anchor") or {})
        self.assertEqual(anchor.get("source_id"), "world:reading_point", anchor)
        self.assertEqual(anchor.get("page"), 1, anchor)
        self.assertTrue(str(anchor.get("source_revision") or ""), anchor)
        # 与私人材料同形：一条取得记录 + 一个停点也在同一份文档里。
        acquisitions = _items(document, "source_acquisition")
        self.assertEqual(len(acquisitions), 1, acquisitions)
        self.assertEqual(acquisitions[0].get("source_id"), "world:reading_point",
                         acquisitions[0])
        self.assertEqual(len(_items(document, "reading_position")), 1, document["items"])
        # 版本号是 owner 真的创建的那一个，不是计划的 +2。
        head = field.head(doc_id)
        self.assertEqual(reading.get("version_number"), head.get("version_number"), reading)
        self.assertEqual(document.get("owner_version_number"), head.get("version_number"),
                         document.get("owner_version_number"))
        self.assertEqual(document.get("doc_revision"), head.get("version_number"),
                         document.get("doc_revision"))
        # 原文和正文都不进报告：报告只有形状。
        self.assertNotIn(READING_TEXT[:12], json.dumps(ended, ensure_ascii=False))
        self.assertNotIn(WRITER_BODY[:12], json.dumps(ended, ensure_ascii=False))

    # -- 2) 地点没有文字 → 什么都不发生 ------------------------------------------

    def test_a_place_without_text_writes_nothing_and_does_not_fail(self):
        field = self._fixture(reading=None)
        doc_id = field.data["private_items_artifact_id"]
        before = field.head(doc_id)

        self._walk_to_reading_corner_quiet(field)
        ended = self._read_once(field, "world-reading:quiet")

        self.assertEqual((ended.get("stay") or {}).get("phase"), "END", ended)
        reading = dict(ended.get("world_reading") or {})
        self.assertEqual(reading.get("state"), "NO_READING_HERE", reading)
        self.assertEqual(_items(_document(field, doc_id), "memo"), [])
        self.assertEqual(field.head(doc_id)["version_id"], before["version_id"])

    def _walk_to_reading_corner_quiet(self, field) -> None:
        field.travel("street_front")
        field.clock.advance(4 * 3600, because="morning-to-opening")
        field.travel("reading_point")
        self.assertEqual(field.current_place(), "reading_point")
        self.assertIsNone(field.cycle._place_reading_text("reading_point"))

    # -- 3) 写作失败 → 停留仍完成，文档没被改动 ----------------------------------

    def test_a_failed_writer_keeps_the_stay_completed_and_the_document_untouched(self):
        writer = StubReadingResponse(fail=True)
        field = self._fixture(writer=writer)
        doc_id = field.data["private_items_artifact_id"]
        before = field.head(doc_id)

        self._walk_to_reading_corner(field)
        ended = self._read_once(field, "world-reading:failed")

        self.assertEqual((ended.get("stay") or {}).get("phase"), "END", ended)
        self.assertEqual(ended.get("progress_state"), "COMPLETION_ELIGIBLE", ended)
        activity = field.activity(str(ended.get("activity_ref") or ""))
        self.assertEqual(activity.get("status"), "COMPLETED", activity)
        reading = dict(ended.get("world_reading") or {})
        self.assertEqual(reading.get("state"), "NOT_WRITTEN", reading)
        self.assertEqual(reading.get("reason"), "READING_RESPONSE_FAILED", reading)
        self.assertIsNone(reading.get("item_type"), reading)
        # 一字节没改：没有取得、没有停点、没有批注，head 也没动。
        self.assertEqual(field.head(doc_id)["version_id"], before["version_id"])
        document = _document(field, doc_id)
        self.assertEqual(_items(document, "memo"), [])
        self.assertEqual(_items(document, "source_acquisition"), [])
        self.assertEqual(len(writer.calls), 1, writer.calls)

    # -- 4) 同一段文字再读一次 → 不会写出第二条一样的批注 ------------------------

    def test_reading_the_same_text_again_does_not_write_a_second_note(self):
        writer = StubReadingResponse()
        field = self._fixture(writer=writer)
        doc_id = field.data["private_items_artifact_id"]

        self._walk_to_reading_corner(field)
        first = self._read_once(field, "world-reading:first")
        self.assertEqual((first.get("world_reading") or {}).get("state"), "APPLIED", first)
        after_first = field.head(doc_id)
        self.assertEqual(len(_items(_document(field, doc_id), "memo")), 1)

        # 第二遍读：同一处地点、同一段文字（同一份目录事实），再走一次"读完之后的后果"。
        # 这里不再起第二个 stay：同一地点同一种停留的 step 身份由既有规则复用，属于本单之外的
        # 既有一致性；本测试要证明的是**批注本身**的 operation key 幂等。
        field.cycle._observe_position()
        second_base: dict = {"observed_at": field.clock.iso()}
        field.cycle._annotate_world_reading(
            second_base, place="reading_point", descriptor="READ_HERE",
            observed_at=field.clock.iso())

        reading = dict(second_base.get("world_reading") or {})
        self.assertIn(reading.get("state"), {"UNCHANGED", "NO_CHANGE"}, reading)
        after_second = field.head(doc_id)
        self.assertEqual(after_second["version_id"], after_first["version_id"], after_second)
        document = _document(field, doc_id)
        self.assertEqual(len(_items(document, "memo")), 1, document["items"])
        self.assertEqual(len(_items(document, "source_acquisition")), 1, document["items"])
        # 模型确实又被问了一次（同一段文字，同样一个请求），只是同一页同一意图只落一条。
        self.assertEqual(len(writer.calls), 2, writer.calls)

    # -- 5) 报告要真的落盘：成功和静默失败在 journal 里分得开 ----------------------

    def _landed(self, field, out) -> dict:
        """把一轮真实输出经 runner 的落盘路径写进 wake journal，读回最后一行。

        ``_record`` / ``_journal`` 就是 ``wake()`` 落盘用的同一段代码，这里只是把这一轮已经
        产生的 ``out`` 直接交给它，不再多起一次完整 wake。
        """

        runner = alr.AutonomousLifeRunner(field.cycle, state_root=str(field.state_root),
                                          env={}, enabled=True,
                                          clock=lambda: field.clock.now)
        runner._record(alr.WAKE_KIND_CHECK_WINDOW, "check-window", alr.WAKE_ACTED, "action",
                       field.clock.now, extra=runner._extra_for(out))
        rows = [json.loads(line) for line
                in runner.wake_log.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertTrue(rows, "the wake journal must carry the row this round wrote")
        row = rows[-1]
        # 写盘本身不许失败：失败会被标进 memory 里的 extra，而盘上什么都没有。
        self.assertNotIn("journal_write_failed", row.get("extra") or {}, row)
        return row

    def test_the_report_lands_in_the_wake_journal_when_the_round_produced_one(self):
        field = self._fixture()
        self._walk_to_reading_corner(field)
        ended = self._read_once(field, "world-reading:landed")
        self.assertEqual((ended.get("world_reading") or {}).get("state"), "APPLIED", ended)

        row = self._landed(field, ended)
        landed = dict((row.get("extra") or {}).get("world_reading") or {})
        self.assertEqual(landed.get("state"), "APPLIED", row)
        self.assertEqual(landed.get("place"), "reading_point", row)
        self.assertEqual(landed.get("descriptor"), "READ_HERE", row)
        self.assertEqual(landed.get("item_type"), "memo", row)
        self.assertEqual(landed.get("version_number"),
                         field.head(field.data["private_items_artifact_id"])["version_number"],
                         row)
        self.assertIsNone(landed.get("reason"), row)
        # 原样带出：这一轮的六个字段一个不少、一个没改。
        self.assertEqual(set(landed), set(ended["world_reading"]), row)
        self.assertLessEqual({"state", "place", "descriptor", "item_type", "version_number",
                              "reason"}, set(landed), row)
        # 只记形状：原文和批注正文都不进 journal。
        self.assertNotIn(READING_TEXT[:12], json.dumps(row, ensure_ascii=False))
        self.assertNotIn(WRITER_BODY[:12], json.dumps(row, ensure_ascii=False))

    def test_a_place_without_text_lands_as_a_named_failure_not_as_silence(self):
        field = self._fixture(reading=None)
        self._walk_to_reading_corner_quiet(field)
        ended = self._read_once(field, "world-reading:quiet-landed")
        self.assertEqual((ended.get("world_reading") or {}).get("state"), "NO_READING_HERE",
                         ended)

        row = self._landed(field, ended)
        landed = dict((row.get("extra") or {}).get("world_reading") or {})
        # 没写成也看得出来：状态和地点都在，item_type 为空说明没有条目被创建。
        self.assertEqual(landed.get("state"), "NO_READING_HERE", row)
        self.assertEqual(landed.get("place"), "reading_point", row)
        self.assertIsNone(landed.get("item_type"), row)
        self.assertIsNone(landed.get("version_number"), row)

    def test_a_round_without_a_report_adds_no_key_at_all(self):
        field = self._fixture()
        # 没有读书这件事的轮次，输出里本来就没有 world_reading 这一项。
        out = {"outcome": "QUIET", "detail": "", "llm_calls": 0}
        self.assertNotIn("world_reading", out)

        row = self._landed(field, out)
        extra = dict(row.get("extra") or {})
        self.assertNotIn("world_reading", extra, extra)
        self.assertNotIn("world_reading", json.dumps(row, ensure_ascii=False))
        # 不是"多了一个空块"：既有的其他键照旧在。
        self.assertIn("budget", extra)
        self.assertIn("reason_codes", extra)
        self.assertIn("selected_candidate_ref", extra)


if __name__ == "__main__":
    unittest.main()

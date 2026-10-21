"""v0.4 A：阅读批注纯计划 —— 不需要 owner，也不许需要 owner。

这个文件测什么
--------------
`private_daily_items.plan_page_annotation` 是**纯计划**：输入是 `plan_page_read` 已经写好"这一页
真的取得过"的那份文档，输出是下一个文档版本（或同一个版本 + `changed` False）。这里用真的
`plan_page_read` 造那种文档，断言计划自己会核实取得/版本/撤权、一步最多一条、同键幂等、
同键异意图拒绝、`no_change` 一个字节都不改、停点与 `understanding` 从不动。

不做的事
--------
* 不起 Supply owner、不建 socket、不落盘、不建临时目录（本文件没有夹具根），不删任何文件。
* 不调用模型：响应是显式给出的三键字典（内容替身），不声称这是真实模型的判断。
"""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"), str(ROOT / "components" / "life" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import private_daily_items as pdi  # noqa: E402
import subject_reading_response as srr  # noqa: E402

ARTIFACT = "artifact:private-items-1"
SUBJECT = "test:subject-reading-annotation"
WORKSPACE = "workspace:test"
SOURCE = "reading:artifact-9"
REVISION = "artifactversion:reading-9:3"
STAMP = "2026-10-25T09:00:00+00:00"


def _blank_document() -> dict:
    """The v1 typed document a freshly created artifact holds (before any write)."""
    return {
        "doc_type": pdi.DOC_TYPE, "schema_version": pdi.SCHEMA_VERSION,
        "subject_id": SUBJECT, "artifact_id": "", "workspace_id": WORKSPACE,
        "owner_version_number": 1, "doc_revision": 1, "updated_at": STAMP,
        "order": [], "items": {},
    }


def _read_document(*, page: int = 2, pages: tuple = (2,), revision: str = REVISION,
                   source: str = SOURCE) -> dict:
    """One document that already carries a real page read, planned by the real planner."""
    plan = pdi.plan_page_read(
        _blank_document(), artifact_id=ARTIFACT, source_id=source, source_revision=revision,
        receipt_ref="read:%s:%s:page-%s" % (ARTIFACT, revision, page),
        obtained={"pages": list(pages), "chars": 120}, excerpt="这一页的摘录。",
        page=page, now=STAMP)
    return pdi.decode_document(plan["content"])


def _of(document: dict, item_type: str) -> list[dict]:
    return [item for item in document["items"].values() if item.get("item_type") == item_type]


def _plan(document: dict, response: dict, *, key: str = "op:note:1", page=2,
          source: str = SOURCE, revision: str = REVISION, artifact: str = ARTIFACT) -> dict:
    return pdi.plan_page_annotation(
        document, artifact_id=artifact, source_id=source, source_revision=revision, page=page,
        response=response, operation_key=key, now=STAMP)


def _read_page(document: dict, *, page: int) -> dict:
    """One more real page read on the same revision: a new acquisition + a new position."""
    plan = pdi.plan_page_read(
        document, artifact_id=ARTIFACT, source_id=SOURCE, source_revision=REVISION,
        receipt_ref="read:%s:%s:page-%s" % (ARTIFACT, REVISION, page),
        obtained={"pages": [page], "chars": 120}, excerpt="这一页的摘录。", page=page,
        head_number=document.get("owner_version_number"), now=STAMP)
    return pdi.decode_document(plan["content"])


NOTE = {"kind": "note", "title": "停顿", "body": "这一页把停顿写成了动作。"}
QUESTION = {"kind": "unclear", "note": "这句话里的“她”指谁？"}
NOTHING = {"kind": "no_change"}


class PageAnnotationPlanTest(unittest.TestCase):
    """纯计划的核实、幂等、异意图拒绝与"不动停点"。"""

    def test_a_note_plan_adds_one_anchored_memo_in_the_next_version(self):
        document = _read_document()
        plan = _plan(document, NOTE)
        self.assertTrue(plan["changed"])
        self.assertEqual(plan["kind"], "note")
        self.assertEqual(plan["operation_key"], "op:note:1")
        self.assertTrue(plan["item_id"].startswith("memo:"))
        after = pdi.decode_document(plan["content"])
        item = after["items"][plan["item_id"]]
        self.assertEqual(item["item_type"], pdi.ITEM_MEMO)
        self.assertEqual(item["title"], "停顿")
        self.assertEqual(item["body"], "这一页把停顿写成了动作。")
        self.assertEqual(item["reading_anchor"],
                         {"source_id": SOURCE, "source_revision": REVISION, "page": 2})
        self.assertEqual(item["revisions"], 1)
        self.assertEqual(len(_of(after, pdi.ITEM_MEMO)), 1)
        self.assertEqual(after["owner_version_number"], document["owner_version_number"] + 1)
        self.assertEqual(after["doc_revision"], after["owner_version_number"])
        self.assertEqual(after["updated_at"], STAMP)
        # 元数据白名单里有锚点、没有正文：下一轮读视图能看到锚点与计数。
        self.assertIn("reading_anchor", pdi._METADATA_FIELDS[pdi.ITEM_MEMO])
        self.assertNotIn("body", pdi._METADATA_FIELDS[pdi.ITEM_MEMO])

    def test_a_note_plan_never_touches_the_stop_point_or_understanding(self):
        document = _read_document()
        positions = copy.deepcopy(_of(document, pdi.ITEM_READING_POSITION))
        acquisitions = copy.deepcopy(_of(document, pdi.ITEM_SOURCE_ACQUISITION))
        after = pdi.decode_document(_plan(document, NOTE)["content"])
        self.assertEqual(_of(after, pdi.ITEM_READING_POSITION), positions)
        self.assertEqual(_of(after, pdi.ITEM_SOURCE_ACQUISITION), acquisitions)
        for item in _of(after, pdi.ITEM_READING_POSITION):
            self.assertEqual(item["understanding"], pdi.UNDERSTANDING_UNKNOWN)
            self.assertEqual(item["position"], 2)

    def test_no_change_plans_nothing_at_all(self):
        document = _read_document()
        plan = _plan(document, NOTHING)
        self.assertFalse(plan["changed"])
        self.assertIsNone(plan["item_id"])
        self.assertEqual(plan["kind"], "no_change")
        self.assertEqual(plan["content"], pdi.encode_document(document))
        after = pdi.decode_document(plan["content"])
        self.assertEqual(after["items"], document["items"])
        self.assertEqual(after["owner_version_number"], document["owner_version_number"])
        self.assertNotIn(pdi.READING_OPERATIONS_FIELD, after)

    def test_a_question_plan_writes_one_unclear_marker(self):
        after = pdi.decode_document(_plan(_read_document(), QUESTION, key="op:unclear:1")["content"])
        markers = _of(after, pdi.ITEM_UNCLEAR_MARKER)
        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0]["note"], "这句话里的“她”指谁？")
        self.assertEqual(markers[0]["position"], 2)
        self.assertEqual(markers[0]["source_revision"], REVISION)
        self.assertEqual(_of(after, pdi.ITEM_MEMO), [])

    def test_a_page_without_a_real_acquisition_is_refused(self):
        with self.assertRaises(pdi.PrivateItemsContractRefused) as caught:
            _plan(_blank_document(), NOTE)
        self.assertIn("SOURCE_NOT_ACQUIRED", str(caught.exception))

    def test_a_revoked_source_refuses_the_plan(self):
        document = _read_document()
        for item in _of(document, pdi.ITEM_SOURCE_ACQUISITION):
            item["available"] = False
            item["unavailable_reason"] = "grant:revoked"
        with self.assertRaises(pdi.PrivateItemsSourceUnavailable) as caught:
            _plan(document, NOTE)
        self.assertIn("SOURCE_UNAVAILABLE", str(caught.exception))

    def test_a_page_that_was_never_obtained_is_refused(self):
        document = _read_document(page=1, pages=(1,))
        with self.assertRaises(pdi.PrivateItemsContractRefused) as caught:
            _plan(document, NOTE, page=2)
        self.assertIn("PAGE_NOT_ACQUIRED", str(caught.exception))

    def test_an_older_revision_is_refused(self):
        document = _read_document()
        with self.assertRaises(pdi.PrivateItemsContractRefused) as caught:
            _plan(document, NOTE, revision="artifactversion:reading-9:2")
        self.assertIn("SOURCE_NOT_ACQUIRED", str(caught.exception))

    def test_a_document_bound_to_another_artifact_is_refused(self):
        document = _read_document()
        with self.assertRaises(pdi.PrivateItemsContractRefused) as caught:
            _plan(document, NOTE, artifact="artifact:someone-else")
        self.assertIn("DOCUMENT_BOUND_TO_ANOTHER_ARTIFACT", str(caught.exception))

    def test_the_same_key_and_intent_replans_as_no_change(self):
        first = _plan(_read_document(), NOTE)
        after = pdi.decode_document(first["content"])
        second = _plan(after, NOTE)
        self.assertFalse(second["changed"])
        self.assertEqual(second["item_id"], first["item_id"])
        self.assertEqual(second["content"], pdi.encode_document(after))
        self.assertEqual(len(_of(after, pdi.ITEM_MEMO)), 1)
        self.assertEqual(list(after[pdi.READING_OPERATIONS_FIELD]), ["op:note:1"])

    def test_the_same_key_with_another_body_is_refused(self):
        first = _plan(_read_document(), NOTE)
        after = pdi.decode_document(first["content"])
        other = {"kind": "note", "title": "停顿", "body": "换一段完全不同的正文。"}
        with self.assertRaises(pdi.PrivateItemsContractRefused) as caught:
            _plan(after, other)
        self.assertIn("READING_OPERATION_KEY_REUSED", str(caught.exception))

    def test_the_same_key_cannot_turn_a_note_into_a_question(self):
        first = _plan(_read_document(), NOTE)
        after = pdi.decode_document(first["content"])
        with self.assertRaises(pdi.PrivateItemsContractRefused) as caught:
            _plan(after, QUESTION)
        self.assertIn("READING_OPERATION_KEY_REUSED", str(caught.exception))

    def test_two_keys_on_the_same_page_are_two_notes(self):
        first = _plan(_read_document(), NOTE, key="op:note:1")
        after = pdi.decode_document(first["content"])
        second = _plan(after, {"kind": "note", "title": "第二个想法", "body": "同一页另一条笔记。"},
                       key="op:note:2")
        self.assertTrue(second["changed"])
        self.assertNotEqual(second["item_id"], first["item_id"])
        final = pdi.decode_document(second["content"])
        self.assertEqual(len(_of(final, pdi.ITEM_MEMO)), 2)
        self.assertEqual(sorted(final[pdi.READING_OPERATIONS_FIELD]), ["op:note:1", "op:note:2"])

    def test_the_operation_record_holds_no_private_text(self):
        after = pdi.decode_document(_plan(_read_document(), NOTE)["content"])
        record = pdi.reading_operation(after, "op:note:1")
        self.assertIsNotNone(record)
        self.assertEqual(record["action"], "annotation")
        self.assertEqual(record["kind"], "note")
        self.assertEqual(record["page"], 2)
        self.assertTrue(str(record["intent"]).startswith("reading-intent:"))
        recorded = json.dumps(record, ensure_ascii=False)
        self.assertNotIn("停顿", recorded)
        self.assertNotIn("这一页把停顿写成了动作。", recorded)
        acquisition = record["acquisition_id"]
        self.assertIn(acquisition, after["items"])

    def test_a_borrowed_operation_key_is_refused(self):
        document = _read_document()
        document[pdi.READING_OPERATIONS_FIELD] = {
            "op:borrowed": {"operation_key": "op:borrowed", "action": "annotation",
                            "kind": "note", "source_id": SOURCE,
                            "source_revision": REVISION, "page": 2}}
        with self.assertRaises(pdi.PrivateItemsContractRefused) as caught:
            _plan(document, NOTE, key="op:borrowed")
        self.assertIn("READING_OPERATION_KEY_REUSED", str(caught.exception))

    def test_no_change_with_pseudo_text_is_refused(self):
        document = _read_document()
        with self.assertRaises(pdi.PrivateItemsContractRefused) as caught:
            _plan(document, {"kind": "no_change", "body": "其实我还是想写一句"})
        self.assertIn("ANNOTATION_FIELD_NOT_ALLOWED:no_change:body", str(caught.exception))

    def test_an_unknown_kind_is_refused(self):
        document = _read_document()
        with self.assertRaises(pdi.PrivateItemsContractRefused) as caught:
            _plan(document, {"kind": "summary", "body": "这一章讲了什么"})
        self.assertIn("ANNOTATION_KIND_INVALID", str(caught.exception))

    def test_the_reader_bounds_match_the_response_parser(self):
        self.assertEqual(pdi.MAX_ANNOTATION_TITLE_CHARS, srr.MAX_TITLE_CHARS)
        self.assertEqual(pdi.MAX_ANNOTATION_TEXT_CHARS, srr.DEFAULT_MAX_NOTE_CHARS)
        self.assertEqual(tuple(pdi.ANNOTATION_KINDS), tuple(srr.RESPONSE_KINDS))
        document = _read_document()
        exactly = {"kind": "note", "title": "题" * pdi.MAX_ANNOTATION_TITLE_CHARS,
                   "body": "字" * pdi.MAX_ANNOTATION_TEXT_CHARS}
        self.assertTrue(_plan(document, exactly, key="op:note:boundary")["changed"])
        over = {"kind": "note", "title": "题", "body": "字" * (pdi.MAX_ANNOTATION_TEXT_CHARS + 1)}
        with self.assertRaises(pdi.PrivateItemsContractRefused) as caught:
            _plan(document, over, key="op:note:over")
        self.assertIn("ANNOTATION_BODY_TOO_LONG", str(caught.exception))

    def test_a_note_that_also_carries_a_question_field_is_refused(self):
        document = _read_document()
        with self.assertRaises(pdi.PrivateItemsContractRefused) as caught:
            _plan(document, {"kind": "note", "title": "标题", "body": "正文",
                             "note": "顺手也记一个疑问"})
        self.assertIn("ANNOTATION_FIELD_NOT_ALLOWED:note:note", str(caught.exception))

    def test_an_extra_response_field_is_refused(self):
        document = _read_document()
        with self.assertRaises(pdi.PrivateItemsContractRefused) as caught:
            _plan(document, {"kind": "note", "title": "标题", "body": "正文", "mood": "平静"})
        self.assertIn("ANNOTATION_RESPONSE_UNKNOWN_FIELD:mood", str(caught.exception))

    def test_a_second_page_on_the_same_revision_annotates_its_own_acquisition(self):
        two_pages = _read_page(_read_page(_blank_document(), page=1), page=2)
        self.assertEqual(len(_of(two_pages, pdi.ITEM_SOURCE_ACQUISITION)), 2)
        plan = _plan(two_pages, NOTE, key="op:note:page2", page=2)
        self.assertTrue(plan["changed"])
        after = pdi.decode_document(plan["content"])
        record = pdi.reading_operation(after, "op:note:page2")
        page_two = [a for a in _of(after, pdi.ITEM_SOURCE_ACQUISITION)
                    if 2 in (a.get("obtained") or {}).get("pages", [])]
        self.assertEqual(len(page_two), 1)
        self.assertEqual(record["acquisition_id"], page_two[0]["acquisition_id"])
        self.assertEqual(after["items"][plan["item_id"]]["reading_anchor"]["page"], 2)
        self.assertEqual(len(_of(after, pdi.ITEM_MEMO)), 1)

    def test_a_revoked_page_one_never_covers_page_two(self):
        two_pages = _read_page(_read_page(_blank_document(), page=1), page=2)
        for item in _of(two_pages, pdi.ITEM_SOURCE_ACQUISITION):
            if 1 in (item.get("obtained") or {}).get("pages", []):
                item["available"] = False
                item["unavailable_reason"] = "grant:revoked"
        # page 2 自己的取得仍然可用：不得因为"这一版的第一条取得被撤权"就整页拒绝。
        plan = _plan(two_pages, NOTE, key="op:note:page2", page=2)
        self.assertTrue(plan["changed"])
        after = pdi.decode_document(plan["content"])
        record = pdi.reading_operation(after, "op:note:page2")
        page_one = [a for a in _of(after, pdi.ITEM_SOURCE_ACQUISITION)
                    if 1 in (a.get("obtained") or {}).get("pages", [])]
        self.assertEqual(len(page_one), 1)
        self.assertNotEqual(record["acquisition_id"], page_one[0]["acquisition_id"])
        # page 1 是它自己的取得被撤权：不许借 page 2 的取得去覆盖它。
        with self.assertRaises(pdi.PrivateItemsSourceUnavailable) as caught:
            _plan(two_pages, NOTE, key="op:note:page1", page=1)
        self.assertIn("SOURCE_UNAVAILABLE", str(caught.exception))

    def test_a_no_change_under_a_key_that_already_landed_a_note_is_refused(self):
        first = _plan(_read_document(), NOTE, key="op:note:1")
        after = pdi.decode_document(first["content"])
        with self.assertRaises(pdi.PrivateItemsContractRefused) as caught:
            _plan(after, NOTHING, key="op:note:1")
        self.assertIn("READING_OPERATION_KEY_REUSED", str(caught.exception))
        # 全新键的 no_change 仍然零写：文档一字节都不变。
        fresh = _plan(after, NOTHING, key="op:nochange:1")
        self.assertFalse(fresh["changed"])
        self.assertIsNone(fresh["item_id"])
        self.assertEqual(fresh["content"], pdi.encode_document(after))


if __name__ == "__main__":
    unittest.main()

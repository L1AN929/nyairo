"""v0.4 A：有限内容响应解析 —— 注入假 client，只测解析与边界，不调用任何真实模型。

这个文件测什么
--------------
`components/life/scripts/subject_reading_response.py` 是私人阅读里唯一允许内容模型产出私人文字的
地方。这里**不连任何 provider、不读密钥**：`_FakeClient` 是明确替身，只把一段预设文本当成
"模型输出"，断言的是这个模块自己的请求边界、三种意图的解析、拒绝与"不留正文"。

不做的事
--------
* 不建 socket、不落盘、不建临时目录（本文件不需要夹具根），不删除任何文件。
* 不声称模型自己做了选择：三种 kind 都是替身给的，不是真实模型判断。
* 不把笔记正文写进日志或断言失败信息里（断言只比对长度、键名与错误码）。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"), str(ROOT / "components" / "life" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from agency_model_client import AgencyModelClientError, ModelCallResult  # noqa: E402
import subject_reading_response as srr  # noqa: E402

CONTEXT = {
    "source_id": "reading:artifact-7",
    "source_revision": "artifactversion:7:2",
    "page": 2,
    "text": "第二页的正文：她读到一句话，停了停。",
    "previous_page": 1,
    "total_pages": 5,
}
OBSERVED_AT = "2026-10-25T09:00:00+00:00"


class _FakeClient:
    """注入的假 client：记下请求，返回一段预设文本（默认是一段合法 JSON）。"""

    def __init__(self, payload=None, *, text=None, error=None) -> None:
        self.payload = payload
        self.text = text
        self.error = error
        self.calls: list[dict] = []

    def complete(self, *, system_prompt, user_prompt, timeout_s=None, max_output_tokens=None):
        self.calls.append({"system_prompt": system_prompt, "user_prompt": user_prompt,
                           "timeout_s": timeout_s, "max_output_tokens": max_output_tokens})
        if self.error is not None:
            raise self.error
        raw = self.text if self.text is not None else json.dumps(self.payload, ensure_ascii=False)
        return ModelCallResult(purpose="agency_cognition", provider="fake", model="fake",
                               text=raw, latency_ms=0)


def _model(payload=None, *, text=None, error=None, **kwargs):
    client = _FakeClient(payload, text=text, error=error)
    return srr.ModelReadingResponse(client, **kwargs), client


class ReadingResponseParseTest(unittest.TestCase):
    """三种意图的合法形状、越权字段、长度与失败关闭。"""

    def test_a_note_comes_back_as_one_private_note_and_the_request_carries_the_real_page(self):
        model, client = _model({"kind": "note", "title": "关于停顿",
                                "body": "这一页把停顿写成了动作。"})
        out = model.respond(CONTEXT, observed_at=OBSERVED_AT)
        self.assertEqual(out["kind"], "note")
        self.assertEqual(out["title"], "关于停顿")
        self.assertEqual(out["body"], "这一页把停顿写成了动作。")
        self.assertIsNone(out["note"])
        self.assertEqual(out["source_id"], CONTEXT["source_id"])
        self.assertEqual(out["source_revision"], CONTEXT["source_revision"])
        self.assertEqual(out["page"], 2)
        self.assertEqual(out["observed_at"], OBSERVED_AT)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(model.describe()["calls"], 1)
        sent = json.loads(client.calls[0]["user_prompt"])
        self.assertEqual(sent["page"], 2)
        self.assertEqual(sent["total_pages"], 5)
        self.assertEqual(sent["previous_page"], 1)
        self.assertEqual(sent["source_revision"], CONTEXT["source_revision"])
        self.assertIn("第二页的正文", sent["page_text"])
        self.assertEqual(sent["limits"]["title_chars"], srr.MAX_TITLE_CHARS)
        self.assertEqual(sent["limits"]["note_chars"], srr.DEFAULT_MAX_NOTE_CHARS)
        self.assertFalse(sent["page_text_truncated"])

    def test_a_question_keeps_only_the_note_field(self):
        model, _client = _model({"kind": "unclear", "note": "这句话里的“她”指谁？"})
        out = model.respond(CONTEXT, observed_at=OBSERVED_AT)
        self.assertEqual(out["kind"], "unclear")
        self.assertIsNone(out["title"])
        self.assertIsNone(out["body"])
        self.assertEqual(out["note"], "这句话里的“她”指谁？")

    def test_no_change_is_a_legal_answer_and_carries_no_text(self):
        model, _client = _model({"kind": "no_change"})
        out = model.respond(CONTEXT, observed_at=OBSERVED_AT)
        self.assertEqual(out["kind"], "no_change")
        self.assertIsNone(out["title"])
        self.assertIsNone(out["body"])
        self.assertIsNone(out["note"])
        self.assertEqual(model.last_kind, "no_change")
        self.assertIsNone(model.last_error)

    def test_a_note_without_a_body_is_refused(self):
        model, _client = _model({"kind": "note", "title": "只有标题"})
        with self.assertRaises(srr.ReadingResponseError) as caught:
            model.respond(CONTEXT, observed_at=OBSERVED_AT)
        self.assertIn("RESPONSE_FIELD_EMPTY:body", str(caught.exception))
        self.assertTrue(model.last_error)
        self.assertIsNone(model.last_kind)

    def test_an_over_long_body_is_refused_instead_of_being_truncated(self):
        model, _client = _model({"kind": "note", "title": "太长", "body": "字" * 1201})
        with self.assertRaises(srr.ReadingResponseError) as caught:
            model.respond(CONTEXT, observed_at=OBSERVED_AT)
        self.assertIn("RESPONSE_BODY_TOO_LONG:1201>1200", str(caught.exception))

    def test_an_over_long_title_is_refused(self):
        model, _client = _model({"kind": "note", "title": "题" * 201, "body": "正文"})
        with self.assertRaises(srr.ReadingResponseError) as caught:
            model.respond(CONTEXT, observed_at=OBSERVED_AT)
        self.assertIn("RESPONSE_TITLE_TOO_LONG:201>200", str(caught.exception))

    def test_a_note_that_also_carries_a_question_field_is_refused(self):
        model, _client = _model({"kind": "note", "title": "标题", "body": "正文",
                                 "note": "顺手也记一个疑问"})
        with self.assertRaises(srr.ReadingResponseError) as caught:
            model.respond(CONTEXT, observed_at=OBSERVED_AT)
        self.assertIn("RESPONSE_FIELD_NOT_ALLOWED:note:note", str(caught.exception))

    def test_a_question_that_also_carries_a_title_is_refused(self):
        model, _client = _model({"kind": "unclear", "title": "标题", "note": "疑问"})
        with self.assertRaises(srr.ReadingResponseError) as caught:
            model.respond(CONTEXT, observed_at=OBSERVED_AT)
        self.assertIn("RESPONSE_FIELD_NOT_ALLOWED:unclear:title", str(caught.exception))

    def test_no_change_with_pseudo_text_is_refused(self):
        model, _client = _model({"kind": "no_change", "body": "其实我还是想写一句"})
        with self.assertRaises(srr.ReadingResponseError) as caught:
            model.respond(CONTEXT, observed_at=OBSERVED_AT)
        self.assertIn("RESPONSE_FIELD_NOT_ALLOWED:no_change:body", str(caught.exception))

    def test_an_unknown_kind_is_refused(self):
        model, _client = _model({"kind": "summary", "body": "这一章讲了什么"})
        with self.assertRaises(srr.ReadingResponseError) as caught:
            model.respond(CONTEXT, observed_at=OBSERVED_AT)
        self.assertIn("RESPONSE_KIND_INVALID:summary", str(caught.exception))

    def test_a_non_json_answer_is_refused(self):
        model, _client = _model(text="这一页我没什么可说的。")
        with self.assertRaises(srr.ReadingResponseError) as caught:
            model.respond(CONTEXT, observed_at=OBSERVED_AT)
        self.assertIn("MODEL_OUTPUT_NOT_JSON", str(caught.exception))

    def test_an_empty_model_output_is_refused(self):
        model, _client = _model(text="   ")
        with self.assertRaises(srr.ReadingResponseError) as caught:
            model.respond(CONTEXT, observed_at=OBSERVED_AT)
        self.assertIn("MODEL_OUTPUT_EMPTY", str(caught.exception))

    def test_an_extra_output_field_is_refused(self):
        model, _client = _model({"kind": "note", "title": "标题", "body": "正文",
                                 "mood": "平静"})
        with self.assertRaises(srr.ReadingResponseError) as caught:
            model.respond(CONTEXT, observed_at=OBSERVED_AT)
        self.assertIn("RESPONSE_UNKNOWN_FIELD:mood", str(caught.exception))

    def test_a_fenced_json_answer_is_still_one_object(self):
        model, _client = _model(text='```json\n{"kind": "no_change"}\n```')
        out = model.respond(CONTEXT, observed_at=OBSERVED_AT)
        self.assertEqual(out["kind"], "no_change")

    def test_the_page_text_is_bounded_before_it_goes_out(self):
        context = dict(CONTEXT, text="页" * 7000)
        model, client = _model({"kind": "no_change"})
        out = model.respond(context, observed_at=OBSERVED_AT)
        sent = json.loads(client.calls[0]["user_prompt"])
        self.assertEqual(len(sent["page_text"]), srr.DEFAULT_MAX_PAGE_CHARS)
        self.assertEqual(sent["page_text_chars"], srr.DEFAULT_MAX_PAGE_CHARS)
        self.assertTrue(sent["page_text_truncated"])
        self.assertTrue(out["page_text_truncated"])
        self.assertEqual(out["page_chars"], srr.DEFAULT_MAX_PAGE_CHARS)

    def test_an_empty_page_is_refused_without_calling_the_model(self):
        model, client = _model({"kind": "no_change"})
        with self.assertRaises(srr.ReadingResponseError) as caught:
            model.respond(dict(CONTEXT, text="   "), observed_at=OBSERVED_AT)
        self.assertIn("READING_TEXT_REQUIRED", str(caught.exception))
        self.assertEqual(client.calls, [])
        self.assertEqual(model.call_count, 0)

    def test_context_with_an_extra_key_is_refused(self):
        model, client = _model({"kind": "no_change"})
        with self.assertRaises(srr.ReadingResponseError) as caught:
            model.respond(dict(CONTEXT, material_body="不该被送出去的整份材料"),
                          observed_at=OBSERVED_AT)
        self.assertIn("READING_CONTEXT_UNKNOWN_FIELD:material_body", str(caught.exception))
        self.assertEqual(client.calls, [])

    def test_a_model_failure_is_a_failure_not_a_fallback(self):
        error = AgencyModelClientError("TRANSPORT_TIMEOUT", "timed out after 20s")
        model, client = _model(error=error)
        with self.assertRaises(srr.ReadingResponseError) as caught:
            model.respond(CONTEXT, observed_at=OBSERVED_AT)
        self.assertIn("MODEL_CALL_FAILED:TRANSPORT_TIMEOUT", str(caught.exception))
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(model.describe()["last_error"],
                         "MODEL_CALL_FAILED:TRANSPORT_TIMEOUT")
        self.assertIsNone(model.last_kind)

    def test_no_client_is_refused_at_construction(self):
        with self.assertRaises(srr.ReadingResponseError) as caught:
            srr.ModelReadingResponse(None)
        self.assertIn("MODEL_CLIENT_REQUIRED", str(caught.exception))

    def test_describe_and_attributes_keep_no_private_text(self):
        model, _client = _model({"kind": "note", "title": "关于停顿",
                                 "body": "这一页把停顿写成了动作。"})
        model.respond(CONTEXT, observed_at=OBSERVED_AT)
        described = json.dumps(model.describe(), ensure_ascii=False)
        self.assertNotIn("关于停顿", described)
        self.assertNotIn("这一页把停顿写成了动作。", described)
        for name, value in vars(model).items():
            if name == "client":
                continue
            self.assertNotIn("这一页把停顿写成了动作。", repr(value))
            self.assertNotIn("关于停顿", repr(value))


if __name__ == "__main__":
    unittest.main()

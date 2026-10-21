"""v0.4 私人读写实践 B：作品 writer 的显式 ``no_change``（不强制改字）。

范围（只证明这一件事，不冒充自主）
----------------------------------
1. ``kind=no_change`` 是一个**合法答复**，不是失败：``next_content`` 抛
   ``ModelNoChange``，而它**不是** ``ModelContentError`` 的子类 —— 所以 cycle 不会把它
   当成「内容生成失败」，也就不会报成 STEP_REFUSED；
2. 它不靠「提交一份同内容的版本」这种技巧实现：没有返回值、没有合并、没有版本；
3. ``no_change`` 不许夹带正文（夹带就是自相矛盾，按契约错误拒绝）；
4. 旧的 ``append_paragraph`` / ``revise_tail`` 行为一字未改；
5. prompt 不再要求「必须给出一个最小的改动」，而是明确允许 ``no_change``。

明确：本文件的模型客户端是**脚本化替身**，0 网络、0 真实模型调用、0 密钥。它证明的是
「契约怎么走」，不是「她自己决定不写」。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components"),
              str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import model_continuation_writer as mcw  # noqa: E402
from life_artifact_port import ArtifactResumePoint  # noqa: E402

OBSERVED_AT = "2026-10-25T06:00:00+00:00"
NOTE_SENTINEL = "这一段是模型自己写的私密心得"


class _StubClient:
    """脚本化模型客户端：只说预先写死的那一句话。"""

    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.calls = 0
        self.last_system_prompt: str | None = None
        self.last_user_prompt: str | None = None

    def complete(self, *, system_prompt: str, user_prompt: str, timeout_s: float,
                 max_output_tokens: int) -> SimpleNamespace:
        self.calls += 1
        self.last_system_prompt = system_prompt
        self.last_user_prompt = user_prompt
        return SimpleNamespace(text=self.payload)


def _point(content: str = "旧的一段。\n") -> ArtifactResumePoint:
    return ArtifactResumePoint(
        artifact_id="artifact:test-work", title="一本私人手记", head_version_id="v1",
        version_number=1, content=content, lifecycle="ACTIVE", revision=1)


def _writer(payload: str) -> tuple[mcw.ModelContinuationWriter, _StubClient]:
    client = _StubClient(payload)
    return mcw.ModelContinuationWriter(client), client


class ModelContinuationNoChangeTest(unittest.TestCase):
    """作品的 no_change 是合法空操作，不是「必须产出」的失败。"""

    def test_a_no_change_answer_raises_the_explicit_signal_and_writes_nothing(self):
        writer, client = _writer('{"kind": "no_change", "text": "", "self_note": "今天不写了"}')

        with self.assertRaises(mcw.ModelNoChange) as caught:
            writer.next_content(_point(), observed_at=OBSERVED_AT)

        self.assertEqual(client.calls, 1, "the request really was made")
        # The signal carries the model's own one-line note, and nothing else.
        self.assertIn("今天不写了", str(caught.exception))
        self.assertEqual(writer.last_kind, mcw.KIND_NO_CHANGE)
        self.assertEqual(writer.no_change_count, 1)
        # A no_change never becomes content: there is no return value to write.
        self.assertFalse(hasattr(writer, "last_content"))

    def test_the_no_change_signal_is_not_a_content_failure(self):
        self.assertFalse(issubclass(mcw.ModelNoChange, mcw.ModelContentError))
        writer, _client = _writer('{"kind": "no_change", "text": ""}')
        try:
            writer.next_content(_point(), observed_at=OBSERVED_AT)
        except mcw.ModelContentError:  # pragma: no cover - the whole point of the test
            self.fail("a legal no_change must never be reported as a content failure")
        except mcw.ModelNoChange:
            pass

    def test_a_no_change_answer_may_not_carry_text(self):
        writer, _client = _writer('{"kind": "no_change", "text": "硬加一句"}')
        with self.assertRaises(mcw.ModelContentError) as caught:
            writer.next_content(_point(), observed_at=OBSERVED_AT)
        self.assertIn("MODEL_NO_CHANGE_CARRIES_TEXT", str(caught.exception))
        self.assertEqual(writer.no_change_count, 0)

    def test_the_prompt_allows_saying_nothing_instead_of_forcing_an_edit(self):
        writer, client = _writer('{"kind": "no_change", "text": ""}')
        # (a) the system prompt names the third kind and drops the "always make a minimal edit"
        #     instruction that forced her to add words she had not chosen.
        self.assertIn("no_change", mcw.SYSTEM_PROMPT)
        self.assertNotIn("也要照常给出一个最小的改动", mcw.SYSTEM_PROMPT)
        # (b) ... and the per-request instruction says the same thing
        try:
            writer.next_content(_point(), observed_at=OBSERVED_AT)
        except mcw.ModelNoChange:
            pass
        self.assertIn("no_change", client.last_user_prompt or "")
        self.assertNotIn("也要照常给出一个最小的改动", client.last_user_prompt or "")

    def test_append_and_revise_are_unchanged(self):
        append, _client = _writer('{"kind": "append_paragraph", "text": "%s"}' % NOTE_SENTINEL)
        merged = append.next_content(_point("旧的一段。"), observed_at=OBSERVED_AT)
        self.assertEqual(merged, "旧的一段。\n\n%s\n" % NOTE_SENTINEL)
        self.assertEqual(append.last_kind, mcw.KIND_APPEND)
        self.assertEqual(append.no_change_count, 0)

        revise, _client2 = _writer('{"kind": "revise_tail", "text": "改写过的一段"}')
        rewritten = revise.next_content(_point("第一段。\n\n第二段。\n"), observed_at=OBSERVED_AT)
        self.assertEqual(rewritten, "第一段。\n\n改写过的一段\n")
        self.assertEqual(revise.last_kind, mcw.KIND_REVISE_TAIL)

    def test_describe_reports_the_no_change_answers(self):
        writer, _client = _writer('{"kind": "no_change", "text": ""}')
        try:
            writer.next_content(_point(), observed_at=OBSERVED_AT)
        except mcw.ModelNoChange:
            pass
        view = writer.describe()
        self.assertEqual(view["kind"], "model_continuation_writer")
        self.assertEqual(view["last_kind"], mcw.KIND_NO_CHANGE)
        self.assertEqual(view["no_change_count"], 1)
        self.assertEqual(view["calls"], 1)
        # The report holds no work text at all.
        self.assertNotIn(NOTE_SENTINEL, str(view))


if __name__ == "__main__":
    unittest.main()

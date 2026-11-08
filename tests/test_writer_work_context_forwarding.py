"""writer 把 ``work_context`` 当作自己的顶层键转发，且不把它误认成裸研究正文。

范围（只证明这一件事，不冒充自主）
----------------------------------
1. 只有 ``work_context`` 的 payload 进入 prompt 后，仍在顶层 ``work_context``；
   ``research_context`` / ``practice_context`` 不出现，也不把 work 嵌进 research。
2. 只有 ``earlier_versions`` 的 payload 不触发旧式「裸研究」分支，旧版摘录照旧有界出现。
3. 四块（work / research / practice / earlier）并存时各走各键，值和来源哨兵不串类；
   整段 user_prompt 是可解析的 JSON。
4. 兼容旧式裸 research mapping、research/practice-only、``None`` / 空 / 非法类型 context；
   现有 cap（prompt 尾部长度、timeout、max_output_tokens）与 ``no_change`` 语义不变。

明确：模型客户端是**脚本化 transport**，0 网络、0 真实模型调用、0 密钥。测试断言的是
``ModelContinuationWriter`` → 正式 ``AgencyModelClient`` → transport 实际收到的 JSON，
不是只调私有 ``_split_context`` 的返回值。
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components"),
              str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import model_continuation_writer as mcw  # noqa: E402
from agency_model_client import AgencyModelClient  # noqa: E402
from life_artifact_port import ArtifactResumePoint  # noqa: E402

OBSERVED_AT = "2026-11-07T12:00:00+00:00"
DEFAULT_PAYLOAD = {"kind": "append_paragraph", "text": "合成的一小段。", "self_note": "synthetic"}

WORK_SENTINEL = "CTX-WORK-SENTINEL-work-only"
EARLIER_SENTINEL = "CTX-EARLIER-SENTINEL-excerpt"
RESEARCH_SENTINEL = "CTX-RESEARCH-SENTINEL-question"
PRACTICE_SENTINEL = "CTX-PRACTICE-SENTINEL-instruction"
LEGACY_SENTINEL = "CTX-LEGACY-SENTINEL-bare-research"

#: Every top-level key the writer is documented to produce.  A new pseudo-field would show up here.
KNOWN_PROMPT_KEYS = frozenset({
    "work_title", "current_version_number", "current_version_id", "observed_at",
    "already_written_chars", "tail_of_current_text", "previous_experiences", "instruction",
    "research_context", "practice_context", "work_context", "earlier_versions",
})


class RecordingTransport:
    """最外层网络替身：记录 ``AgencyModelClient`` 真正送出的 user_prompt，并回脚本编辑。"""

    backend = "scripted-transport"
    provider = "scripted"
    model = "scripted"
    send_split_capable = False

    def __init__(self) -> None:
        self.payload: dict[str, Any] = dict(DEFAULT_PAYLOAD)
        self.prompts: list[dict[str, str]] = []
        self.calls: list[dict[str, Any]] = []

    def complete(self, *, system_prompt: str, user_prompt: str,
                 timeout_s: float | None = None,
                 max_output_tokens: int | None = None) -> Any:
        from agency_model_client import ModelCallResult

        self.prompts.append({"system": system_prompt, "user": user_prompt})
        self.calls.append({"timeout_s": float(timeout_s or 0.0),
                           "max_output_tokens": int(max_output_tokens or 0)})
        return ModelCallResult(purpose="artifact_edit", provider=self.provider,
                               model=self.model,
                               text=json.dumps(self.payload, ensure_ascii=False),
                               latency_ms=1, status=200, usage_source="none")

    def describe(self) -> dict[str, Any]:
        return {"backend": self.backend, "calls": len(self.calls)}


def _point(content: str = "旧的一段。\n") -> ArtifactResumePoint:
    return ArtifactResumePoint(
        artifact_id="artifact:test-work", title="一本私人手记", head_version_id="v1",
        version_number=1, content=content, lifecycle="ACTIVE", revision=1)


def _writer(**kwargs: Any) -> tuple[mcw.ModelContinuationWriter, RecordingTransport]:
    transport = RecordingTransport()
    # 显式 config：正式 client 不读 provider 文件/环境；注入的 transport 就是 provider 路径。
    client = AgencyModelClient(config={"provider": "scripted", "model": "scripted"},
                               transport=transport, purpose="artifact_edit")
    return mcw.ModelContinuationWriter(client, **kwargs), transport


_UNSET = object()


def _prompt_payload(writer: mcw.ModelContinuationWriter, transport: RecordingTransport,
                    context: Any = _UNSET, content: str = "旧的一段。\n") -> dict[str, Any]:
    """走正式 ``next_content`` 并解析 transport 实收的 user_prompt。"""

    before = len(transport.prompts)
    if context is _UNSET:
        writer.next_content(_point(content), observed_at=OBSERVED_AT)
    else:
        writer.next_content(_point(content), observed_at=OBSERVED_AT, context=context)
    if len(transport.prompts) != before + 1:
        raise AssertionError("CALL_COUNT_MISMATCH")
    payload = json.loads(transport.prompts[-1]["user"])
    if not isinstance(payload, dict):
        raise AssertionError("PAYLOAD_NOT_OBJECT")
    return payload


def _work_block(state: str = "OK") -> dict[str, Any]:
    return {
        "schema_version": "chiyo.life.work_context.v1",
        "artifact_id": "artifact:test-work",
        "state": state,
        "reason_code": "" if state == "OK" else "WORK_CONTEXT_NO_WORK_RESULT_EVIDENCE",
        "note": WORK_SENTINEL,
        "work": {"available": True, "title": WORK_SENTINEL},
        "results": {"state": "EMPTY", "rows": []},
    }


def _earlier_versions() -> list[dict[str, Any]]:
    return [{"version_id": "v0", "version_number": 0, "excerpt": EARLIER_SENTINEL}]


class WriterWorkContextForwardingTest(unittest.TestCase):
    """work_context 是独立的顶层键；它既不被丢弃，也不会被当成研究正文。"""

    def test_work_only_and_earlier_only_are_forwarded_as_their_own_keys(self):
        writer, transport = _writer()
        work = _work_block()

        payload = _prompt_payload(writer, transport, {"work_context": work})

        self.assertEqual(payload["work_context"], work)
        self.assertNotIn("research_context", payload)
        self.assertNotIn("practice_context", payload)
        self.assertLessEqual(set(payload), KNOWN_PROMPT_KEYS, "no pseudo-field may appear")

        earlier_payload = _prompt_payload(
            writer, transport, {"earlier_versions": _earlier_versions()})

        # Only ``earlier_versions`` is not a bare research document either.
        self.assertNotIn("research_context", earlier_payload)
        self.assertNotIn("work_context", earlier_payload)
        self.assertEqual(earlier_payload["earlier_versions"][0]["excerpt"], EARLIER_SENTINEL)

    def test_mixed_context_keeps_each_half_in_its_own_key(self):
        writer, transport = _writer()
        research = {"own_research": {"question": RESEARCH_SENTINEL, "sources": []},
                    "prior_research": []}
        practice = {"methods": [{"instruction": PRACTICE_SENTINEL}]}
        work = _work_block(state="PARTIAL")

        payload = _prompt_payload(writer, transport, {
            "work_context": work,
            "research_context": research,
            "practice_context": practice,
            "earlier_versions": _earlier_versions(),
        })

        self.assertEqual(payload["work_context"], work)
        self.assertEqual(payload["research_context"], research)
        self.assertEqual(payload["practice_context"], practice)
        self.assertEqual(payload["earlier_versions"][0]["excerpt"], EARLIER_SENTINEL)
        self.assertNotIn("work_context", payload["research_context"])
        # The sentinels stay in their own half -- no value is copied across keys.
        self.assertNotIn(WORK_SENTINEL,
                         json.dumps(payload["research_context"], ensure_ascii=False))
        self.assertNotIn(RESEARCH_SENTINEL,
                         json.dumps(payload["work_context"], ensure_ascii=False))
        self.assertNotIn(PRACTICE_SENTINEL,
                         json.dumps(payload["work_context"], ensure_ascii=False))
        self.assertLessEqual(set(payload), KNOWN_PROMPT_KEYS)

    def test_legacy_and_absent_context_keep_their_existing_contracts(self):
        writer, transport = _writer()

        # (a) pre-v0.4 bare research mapping still means research, byte for byte.
        legacy = {"own_research": {"question": LEGACY_SENTINEL}}
        legacy_payload = _prompt_payload(writer, transport, legacy)
        self.assertEqual(legacy_payload["research_context"], legacy)
        self.assertNotIn("work_context", legacy_payload)

        # (b) research/practice-only typed container.
        research_blocks = {"own_research": {"question": RESEARCH_SENTINEL}}
        practice_blocks = {"methods": []}
        typed = {"research_context": research_blocks, "practice_context": practice_blocks}
        typed_payload = _prompt_payload(writer, transport, typed)
        self.assertEqual(typed_payload["research_context"], research_blocks)
        self.assertEqual(typed_payload["practice_context"], practice_blocks)
        self.assertNotIn("work_context", typed_payload)

        # (c) no context / empty context must not manufacture research.
        for context in (_UNSET, None, {}):
            payload = _prompt_payload(writer, transport, context)
            self.assertNotIn("research_context", payload)
            self.assertNotIn("practice_context", payload)
            self.assertNotIn("work_context", payload)

        # (d) a non-mapping context and non-mapping typed values follow the existing
        #     "kinds that do not match are simply absent" rule -- no crash, no fake block.
        self.assertNotIn("research_context", _prompt_payload(writer, transport, ["not-a-mapping"]))
        illegal = _prompt_payload(writer, transport, {
            "research_context": "not-a-mapping",
            "practice_context": 7,
            "work_context": ["not", "a", "mapping"],
            "earlier_versions": "not-a-list",
        })
        self.assertNotIn("research_context", illegal)
        self.assertNotIn("practice_context", illegal)
        self.assertNotIn("work_context", illegal)
        self.assertNotIn("earlier_versions", illegal)

    def test_caps_and_no_change_are_unchanged(self):
        writer, transport = _writer(max_new_chars=300, max_prompt_chars=4000,
                                    timeout_s=20.0, max_output_tokens=512)
        long_content = "x" * 4500 + "尾部"

        payload = _prompt_payload(writer, transport, {"work_context": _work_block()},
                                  content=long_content)

        # The product caps still reach the transport, and the prompt tail stays bounded.
        self.assertEqual(transport.calls[-1]["timeout_s"], 20.0)
        self.assertEqual(transport.calls[-1]["max_output_tokens"], 512)
        self.assertEqual(len(payload["tail_of_current_text"]), 4000)
        self.assertTrue(payload["tail_of_current_text"].endswith("尾部"))

        # no_change remains the dedicated signal: one request, no text written, no new surface.
        transport.payload = {"kind": "no_change", "text": "", "self_note": "今天不写了"}
        before = len(transport.prompts)
        with self.assertRaises(mcw.ModelNoChange):
            writer.next_content(_point(), observed_at=OBSERVED_AT,
                                context={"work_context": _work_block()})
        self.assertEqual(len(transport.prompts), before + 1)
        self.assertEqual(writer.no_change_count, 1)
        view = writer.describe()
        self.assertEqual(set(view), {"kind", "calls", "last_kind", "last_trimmed",
                                     "last_self_note", "no_change_count", "max_new_chars"})
        self.assertNotIn(WORK_SENTINEL, json.dumps(view, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()

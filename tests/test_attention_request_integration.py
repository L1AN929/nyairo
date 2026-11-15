#!/usr/bin/env python3
"""30/32 章 A 批：有限注意投影接进**正式决策请求**的集成证据。

「她带着问题去读」此前只发生在 stance GET（``life_attention_stance_containment`` /
``stance_containment_view``）与聊天工具面；本文件证明同一份
``attention_view_projector.build_attention_view``（纯只读、零模型调用、零写盘）如今
进了自主生活回合的 ``decision_situation_context``，并被真实
``ProductionAgencyCognitionAdapter.build_prompt`` 装进**最终请求文本**：

1. 有未决关切（她自己写下的私人笔记）时，关切要点出现在最终请求的
   ``situation_context.attention.concerns`` 里；
2. 32 章夹具语义：未取得的页（页码未取得），其独有内容**不出现**在最终请求里——
   夹具把第 3 页的独有句放进 ``visible_toc``（盘上真的有这句话），请求文本必须
   仍然不带它，而材料标题与已取得页码正常出现；
3. 投影器故障（抛错/无内容）时整块缺席，请求照常生成（fail-open，绝不阻断决策链）。

真实 assembly：真实的 ``AutonomousLifeCycle``（真实 RoundBudget / 真实投影器读盘）
+ 真实的 ``ProductionAgencyCognitionAdapter.build_prompt``；除世界/Supply socket 外
不引入任何第二份编码。
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT, ROOT / "components" / "life" / "scripts"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import agency_cognition_adapter as aa  # noqa: E402
import attention_view_projector as attn_proj  # noqa: E402

_LIFE_ERROR: str | None = None
try:
    import autonomous_life_cycle as alc  # noqa: E402
except Exception as error:  # noqa: BLE001 - 环境限制，不是产品行为
    _LIFE_ERROR = "%s: %s" % (type(error).__name__, error)

needs_life = unittest.skipIf(_LIFE_ERROR is not None,
                             "Life 模块需要 POSIX（fcntl）：%s" % (_LIFE_ERROR or ""))

NOW = "2026-11-13T09:30:00+00:00"
MATERIAL_TITLE = "北岸纪行"
PAGE3_PHRASE = "潮汐在北岸转向的独有句"
CONCERN_NOTE = "潮水为什么在北岸转向？"


def _day_root(prefix: str = "attn-req-") -> Path:
    """一个真实的实例日根：阅读材料（第 3 页未取得）+ 空书桌容器域。

    容器域清空是刻意的：默认书桌场景自带 6 条线索，会把 30 章的
    ``max_visible_cues=6`` 占满，阅读材料面就永远进不了块——夹具要让
    「未取得页的独有句不进请求、已取得部分诚实出现」这条断言真的有牙齿。
    """

    root = Path(tempfile.mkdtemp(prefix=prefix))
    (root / "supply").mkdir(parents=True, exist_ok=True)
    (root / "supply" / "reading_materials.json").write_text(json.dumps({
        "materials": [{
            "scope_ref": "book:north_shore",
            "title": MATERIAL_TITLE,
            "total_pages": 3,
            "enforce_page_gate": True,
            "obtained_pages": [1, 2],
            # 第 3 页的独有句只写在这里（盘上事实）：它属于未取得的页。
            "visible_toc": [{"page": 1, "title": "第一章：出发"},
                            {"page": 3, "title": "第三章：%s" % PAGE3_PHRASE}],
        }],
    }, ensure_ascii=False), encoding="utf-8")
    (root / "life").mkdir(parents=True, exist_ok=True)
    (root / "life" / "attention_stance_containment.json").write_text(json.dumps({
        "schema": "life.v03.attention_stance_containment.v1",
        "schema_version": 1,
        "revision": 0,
        "updated_at": None,
        "origin_writer": "owner",
        "stance_domain": {
            "stances": [], "source_registry": {}, "withdrawn_sources": {},
            "active_stance_ids": [], "obtained_pages_by_scope": {},
        },
        "containment_domain": {
            "subject_place_id": "study_desk",
            "subject_room_id": "study_room",
            "subject_present": True,
            "hand_slots": {"left_hand": None, "right_hand": None},
            "containers": [],
            "items": [],
            "cohabitation": {"partner_present": False, "partner_last_seen_place": None,
                             "partner_last_seen_at": None},
        },
        "operation_receipts": [],
    }, ensure_ascii=False), encoding="utf-8")
    return root


def _cycle(day_root: Path, *, questions=()):
    runtime = SimpleNamespace(state_root=str(Path(tempfile.mkdtemp(prefix="attn-cycle-"))),
                              activity_read=None, action_read=None)
    cycle = alc.AutonomousLifeCycle(
        runtime, subject_id="subject:attn", enabled=True,
        env={"CHIYO_TEST": "1"}, pause_state_root=Path(runtime.state_root),
        continuity_state_root=Path(runtime.state_root),
        project_questions_provider=(lambda: list(questions)))
    cycle._v02_v03_day_root = day_root
    return cycle


def _request(cycle):
    """把真实 decision_situation_context 装进一个最小 DecisionRequest 形状。"""

    return SimpleNamespace(
        candidates=[], policy_version=None,
        legal_decision_enums=["NO_ACTION", "WAIT"],
        allowed_reason_codes=["NO_MATERIAL", "QUIET"],
        current_activity_summary=None, capacity_summary=None,
        observed_fact_keys=[], prior_experiences=[],
        situation_context=cycle.decision_situation_context(now=NOW),
        max_output_tokens=0,
    )


def _final_request_text(cycle) -> str:
    """真实 adapter 的最终请求文本（与生产同一个 build_prompt）。"""

    adapter = aa.ProductionAgencyCognitionAdapter(client=SimpleNamespace())
    text, payload = adapter.build_prompt(_request(cycle))
    json.loads(text)  # 请求文本必须是完整合法 JSON
    return text


@needs_life
class AttentionInRequestIntegrationTest(unittest.TestCase):
    def test_concern_appears_in_the_final_request_when_she_has_one(self) -> None:
        cycle = _cycle(_day_root(), questions=[{"marker_id": "m1", "note": CONCERN_NOTE}])
        text = _final_request_text(cycle)
        self.assertIn("attention", text)
        self.assertIn(CONCERN_NOTE, text)
        payload = json.loads(text)
        attention = payload["situation_context"]["attention"]
        self.assertTrue(attention["concerns"], attention)
        self.assertEqual(attention["concerns"][0]["text"], CONCERN_NOTE)
        self.assertEqual(attention["concerns"][0]["origin"], "EXPLICIT_PRIVATE_NOTE")
        self.assertTrue(attention["not_authority"])

    def test_unobtained_page3_sentence_never_enters_the_request(self) -> None:
        # 32 章夹具：第 3 页未取得——它的独有句必须留在盘上，不进请求。
        cycle = _cycle(_day_root(), questions=[{"marker_id": "m1", "note": CONCERN_NOTE}])
        text = _final_request_text(cycle)
        self.assertNotIn(PAGE3_PHRASE, text)
        # 同时，已取得部分诚实出现：材料标题与已取得页码。
        self.assertIn(MATERIAL_TITLE, text)
        payload = json.loads(text)
        attention = payload["situation_context"]["attention"]
        cues = attention["visible_cues"]
        material_cues = [row for row in cues
                         if row.get("cue_kind") == "reading_material_surface"]
        self.assertTrue(material_cues, attention)
        self.assertEqual(material_cues[0]["obtained_pages"], [1, 2])

    def test_total_block_budget_is_enforced_at_1600_chars(self) -> None:
        cycle = _cycle(_day_root(), questions=[{"marker_id": "m1", "note": CONCERN_NOTE}])
        payload = json.loads(_final_request_text(cycle))
        block = payload["situation_context"]["attention"]
        serialized = json.dumps(block, ensure_ascii=False, sort_keys=True)
        self.assertLessEqual(len(serialized), aa.MAX_ATTENTION_BLOCK_CHARS)

    def test_projector_failure_leaves_the_block_out_and_request_still_fires(self) -> None:
        cycle = _cycle(_day_root(), questions=[{"marker_id": "m1", "note": CONCERN_NOTE}])
        with mock.patch.object(attn_proj, "build_attention_view",
                               side_effect=RuntimeError("projector exploded")):
            ctx = cycle.decision_situation_context(now=NOW)
            self.assertNotIn("attention", ctx)
            text = _final_request_text(cycle)  # 请求照常生成，绝不阻断决策链
        self.assertNotIn("attention", text)
        self.assertIn("situation_context", text)

    def test_empty_view_omits_the_block_and_adapter_honours_empty_input(self) -> None:
        # 空内容不出块（32 章 §6）：投影为空视图/无行 -> 块缺席，请求形状与从前一致。
        empty_view = {
            "kind": attn_proj.ATTENTION_VIEW_KIND, "observed_at": NOW,
            "subject_ref": "subject", "activity_ref": None,
            "concerns": [], "visible_cues": [], "current_views": [],
            "revision_chains": [], "truncated": False, "availability": "EMPTY",
            "omniscience_blocked": True, "llm_calls": 0,
        }
        cycle = _cycle(_day_root())
        with mock.patch.object(attn_proj, "build_attention_view",
                               return_value=empty_view):
            ctx = cycle.decision_situation_context(now=NOW)
            self.assertNotIn("attention", ctx)
            text = _final_request_text(cycle)
        self.assertNotIn('"attention"', text)
        # adapter 直接收到空/坏输入时同样返回 {}，不抛错。
        self.assertEqual(aa._bounded_attention({}), {})
        self.assertEqual(aa._bounded_attention(None), {})
        self.assertEqual(aa._bounded_attention("nope"), {})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

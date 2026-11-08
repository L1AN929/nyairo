#!/usr/bin/env python3
"""自动回顾候选的来源反馈边界：只停「回顾成果再当回顾种子」这一条边。

本文件的边界（照实分级）
------------------------
* 纯供给层：``life_project_options.review_seed`` / ``build_options`` 只按调用方给的
  「owner 已确认是回顾的 artifact id」过滤自动回顾种子；不改 experiences 全局、不删个人机会。
* owner 类型判定：真实 ``PersonalPracticeService``（内存 owner 替身 ``FakePort``）的
  ``read_review`` 第一行标记；标题加不加「回顾：」都不影响，标记才影响。
* 真实 Supply + 真实 M0 + 正式 assembly 的脚本 Decision 全链见
  ``scripts/review_feedback_boundary_check.py check --offline``，不在此重复。

负控重点：
1. review 成果不是种子；普通结果仍可作种子；首项是 review 结果时不吞后面的普通经历。
2. 普通作品标题恰以「回顾：」开头也不误杀；回顾改成别的标题也照样按 owner 标记分类。
3. 源读失败 / 未知类型不冒「已核普通」，本轮自动种子具名扣留，其它候选不受影响。
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(Path(__file__).resolve().parent)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_practice_context as lpc  # noqa: E402
import life_project_options as lpo  # noqa: E402

POSIX_ONLY = unittest.skipIf(os.name == "nt",
                             "真实 owner/socket 夹具只在 POSIX 可用")


def experience(experience_id: str, *, artifact_ref: str = "artifact:work-1",
               excerpt: str = "一条真实经历") -> dict[str, Any]:
    return {
        "experience_id": experience_id,
        "occurred_at": "2026-11-03T08:00:00+00:00",
        "excerpt": excerpt,
        "artifact_ref": artifact_ref,
        "version_ref": "artifactversion:ver-1",
        "outcome_kind": "VERIFIED_RESULT",
    }


class SupplyLayerTests(unittest.TestCase):
    """供给层：过滤只发生在自动回顾种子上。"""

    def test_a_review_result_is_not_used_as_the_next_review_seed(self) -> None:
        row = experience("exp:review-result", artifact_ref="artifact:art-review")
        self.assertIsNone(lpo.review_seed(
            [row], available=True, excluded_artifact_refs=["artifact:art-review"]))
        options = lpo.build_options(
            experiences=[row], practice_available=True,
            review_excluded_artifact_refs=["artifact:art-review"])
        self.assertEqual([opt for opt in options if opt.get("review")], [])

    def test_an_ordinary_result_still_seeds_a_review(self) -> None:
        row = experience("exp:ordinary", artifact_ref="artifact:art-work")
        seed = lpo.review_seed([row], available=True,
                               excluded_artifact_refs=["artifact:art-review"])
        self.assertIsNotNone(seed)
        self.assertEqual([r["experience_id"] for r in seed["experiences"]], ["exp:ordinary"])

    def test_a_review_result_first_does_not_swallow_the_ordinary_row_behind_it(self) -> None:
        review = experience("exp:review-result", artifact_ref="artifact:art-review",
                            excerpt="回顾写出来的那次结果")
        ordinary = experience("exp:ordinary", artifact_ref="artifact:art-work",
                              excerpt="一次普通写作结果")
        seed = lpo.review_seed([review, ordinary], available=True,
                               excluded_artifact_refs=["artifact:art-review"])
        self.assertIsNotNone(seed)
        self.assertEqual([r["experience_id"] for r in seed["experiences"]], ["exp:ordinary"])
        self.assertNotIn("exp:review-result", seed["title"])

    def test_an_experience_with_no_artifact_source_is_a_legal_seed(self) -> None:
        # 世界/身体结果没有 artifact 来源，不可能是任何作品的写作成果。
        row = experience("exp:world", artifact_ref="")
        seed = lpo.review_seed([row], available=True,
                               excluded_artifact_refs=["artifact:art-review"])
        self.assertIsNotNone(seed)

    def test_an_unclassified_source_withholds_the_seed_but_not_other_options(self) -> None:
        row = experience("exp:unknown", artifact_ref="artifact:art-review")
        options = lpo.build_options(
            questions=[{"note": "一个普通问题", "marker_id": "m1"}],
            experiences=[row], practice_available=True,
            review_source_verified=False)
        self.assertEqual([opt for opt in options if opt.get("review")], [])
        self.assertTrue([opt for opt in options if opt.get("kind") == lpo.KIND_QUESTION])

    def test_defaults_preserve_the_old_behaviour(self) -> None:
        row = experience("exp:ordinary", artifact_ref="artifact:art-review")
        options = lpo.build_options(experiences=[row], practice_available=True)
        self.assertEqual([opt.get("kind") for opt in options], [lpo.KIND_REVIEW])


class OwnerClassificationTests(unittest.TestCase):
    """owner 既有读口按第一行实践标记判定，标题不参与。"""

    @classmethod
    def setUpClass(cls) -> None:
        import personal_practice_service as pps
        from test_v04_personal_practice_stage import service_for, seeded_port
        cls.pps = pps
        cls.service = service_for(seeded_port(), memory=None)
        cls.seam = lpc.PracticeSeam(cls.service)

    def test_a_review_is_classified_by_its_marker_not_its_title(self) -> None:
        created = self.service.create_review(
            question="我到底看见了什么？", title="完全不像回顾的标题",
            experience_ids=(), operation_key="test:review:custom-title")
        self.assertTrue(created.get("ok"), created)
        artifact_id = str(created["data"]["project"]["artifact_id"])
        result = self.seam.review_result_artifact_ids([artifact_id])
        self.assertEqual(result["status"], "READY", result)
        self.assertIn(artifact_id, result["review_ids"])

    def test_an_ordinary_work_titled_like_a_review_is_not_a_review(self) -> None:
        # 普通工件正文第一行不是实践标记：即使标题以「回顾：」开头，也不会被误杀。
        self.service.projects.port.seed(
            artifact_id="artifact:art-plain-review-title",
            title="回顾：其实只是普通作品",
            content="普通正文第一行\n第二行\n")
        result = self.seam.review_result_artifact_ids(["artifact:art-plain-review-title"])
        self.assertEqual(result["status"], "READY", result)
        self.assertNotIn("artifact:art-plain-review-title", result["review_ids"])

    def test_a_method_is_a_confident_non_review(self) -> None:
        created = self.service.create_method(
            title="一条做法", instruction="先写结论", operation_key="test:method:1")
        self.assertTrue(created.get("ok"), created)
        artifact_id = str(created["data"]["project"]["artifact_id"])
        result = self.seam.review_result_artifact_ids([artifact_id])
        self.assertEqual(result["status"], "READY", result)
        self.assertNotIn(artifact_id, result["review_ids"])

    def test_a_missing_source_is_unknown_not_ordinary(self) -> None:
        result = self.seam.review_result_artifact_ids(["artifact:does-not-exist"])
        self.assertEqual(result["status"], "UNKNOWN", result)
        self.assertEqual(result["review_ids"], [])

    def test_an_empty_source_ref_is_neither_unknown_nor_a_review(self) -> None:
        result = self.seam.review_result_artifact_ids(["", None])
        self.assertEqual(result["status"], "READY", result)
        self.assertEqual(result["review_ids"], [])
        self.assertEqual(result["checked"], 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

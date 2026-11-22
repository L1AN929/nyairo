#!/usr/bin/env python3
"""Ch.23 有限视野与牵挂滤镜下的书架浏览。

验证：
1. ``project_concern_tinted_shelf_zero_write`` 严格零写入（SHA256 清单 + 目录树 mtime/size 全等）；
2. 同一书架在两个不同牵挂问题下注意到不同切面，且全部条目保持
   ``in_range=True`` / ``understood=False`` / ``full_text_dumped=False`` / ``llm_calls == 0``；
3. ``browse_book`` 经 ``mutate_v04_neighborhood_view`` 回传 ``noticed_angle_cn`` 与
   ``understood=False``，且未知/伪分数字段仍被拒、partner 越权仍 403。
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_WORLD_SCRIPTS = REPO_ROOT / "components" / "world" / "scripts"
for _p in (str(REPO_ROOT), str(_WORLD_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from components.phone.v04_neighborhood_view import (  # noqa: E402
    mutate_v04_neighborhood_view,
)
from components.world.scripts.v04_neighborhood_living import (  # noqa: E402
    default_state,
    project_concern_tinted_shelf_zero_write,
    save_state_atomic,
)

OPEN_AT = "2026-11-17T11:00:00+08:00"


def _sha256_manifest(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rel = str(path.relative_to(root)).replace("\\", "/")
            out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _tree_snapshot(root: Path) -> dict[str, tuple[int, int, int]]:
    out: dict[str, tuple[int, int, int]] = {}
    for path in sorted(root.rglob("*")):
        stat = path.lstat()
        rel = str(path.relative_to(root)).replace("\\", "/")
        out[rel] = (stat.st_mtime_ns, stat.st_size, stat.st_mode)
    return out


class TestConcernTintedShelf(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-ch23-shelf-")
        self.day_root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed_shelf(self) -> None:
        save_state_atomic(self.day_root, default_state())

    def test_01_projection_is_zero_write(self) -> None:
        self._seed_shelf()
        before_manifest = _sha256_manifest(self.day_root)
        before_tree = _tree_snapshot(self.day_root)

        paper = project_concern_tinted_shelf_zero_write(
            self.day_root,
            focus_question="纸盒折叠/修补",
            now_iso=OPEN_AT,
        )
        river = project_concern_tinted_shelf_zero_write(
            self.day_root,
            focus_question="河边长椅/雨天绕路",
            now_iso=OPEN_AT,
        )

        after_manifest = _sha256_manifest(self.day_root)
        after_tree = _tree_snapshot(self.day_root)

        # 两次零写入检查：内容清单与目录树（含 mtime/size/mode）完全一致。
        self.assertEqual(before_manifest, after_manifest)
        self.assertEqual(before_tree, after_tree)
        self.assertEqual(set(before_manifest), set(after_manifest))

        for projection in (paper, river):
            self.assertTrue(projection["zero_write"])
            self.assertEqual(projection["llm_calls"], 0)

    def test_02_two_concerns_notice_different_slices(self) -> None:
        self._seed_shelf()

        paper = project_concern_tinted_shelf_zero_write(
            self.day_root,
            focus_question="纸盒折叠/修补",
            now_iso=OPEN_AT,
        )
        river = project_concern_tinted_shelf_zero_write(
            self.day_root,
            focus_question="河边长椅/雨天绕路",
            now_iso=OPEN_AT,
        )

        paper_ids = [item["book_id"] for item in paper["noticed_items"]]
        river_ids = [item["book_id"] for item in river["noticed_items"]]

        self.assertEqual(paper_ids, ["book_paper_joinery"])
        self.assertEqual(river_ids, ["book_river_sketches"])
        self.assertNotEqual(paper_ids, river_ids)

        for projection in (paper, river):
            self.assertEqual(projection["llm_calls"], 0)
            self.assertTrue(projection["zero_write"])
            self.assertIn(
                projection["noticed_items"][0]["book_id"],
                projection["in_range_book_ids"],
            )
            for item in projection["noticed_items"]:
                self.assertTrue(item["in_range"])
                self.assertTrue(item["noticed"])
                self.assertFalse(item["understood"])
                self.assertFalse(item["full_text_dumped"])
                self.assertTrue(item["noticed_angle_cn"])
                self.assertTrue(item["matched_cues"])

        # 牵挂对不上任何一本时，诚实返回空列表，不强凑。
        unmatched = project_concern_tinted_shelf_zero_write(
            self.day_root,
            focus_question="量子电动力学与超导磁体",
            now_iso=OPEN_AT,
        )
        self.assertEqual(unmatched["noticed_items"], [])
        self.assertTrue(unmatched["in_range_book_ids"])

        # 只给 concern_keywords 也能命中（仍是有限视野，不 dump 全文）。
        keyword_only = project_concern_tinted_shelf_zero_write(
            self.day_root,
            concern_keywords=["折边"],
            now_iso=OPEN_AT,
        )
        self.assertEqual(
            [item["book_id"] for item in keyword_only["noticed_items"]],
            ["book_paper_joinery"],
        )

    def test_03_browse_book_carries_concern_angle_through_view(self) -> None:
        code, response = mutate_v04_neighborhood_view(
            self.day_root,
            {
                "action": "bookstore_service",
                "service": "browse_book",
                "book_id": "book_paper_joinery",
                "focus_question": "纸盒折叠",
            },
            actor_side="subject",
            now_iso=OPEN_AT,
        )
        self.assertEqual(code, 200)
        result = response["result"]
        self.assertEqual(result["focus_question"], "纸盒折叠")
        self.assertIn("纸盒", result["noticed_angle_cn"])
        self.assertFalse(result["understood"])
        self.assertFalse(result["full_text_dumped"])
        self.assertEqual(result["book_id"], "book_paper_joinery")
        self.assertIn("sample_excerpt", result)

        # concern_keywords 单独传入也可触发牵挂角度，但不宣称看懂。
        code_kw, response_kw = mutate_v04_neighborhood_view(
            self.day_root,
            {
                "action": "bookstore_service",
                "service": "browse_book",
                "book_id": "book_paper_joinery",
                "concern_keywords": ["折边"],
            },
            actor_side="subject",
            now_iso=OPEN_AT,
        )
        self.assertEqual(code_kw, 200)
        result_kw = response_kw["result"]
        self.assertIn("折边", result_kw["noticed_angle_cn"])
        self.assertFalse(result_kw["understood"])

        # 不传牵挂字段时，既有返回值不被破坏。
        code_plain, response_plain = mutate_v04_neighborhood_view(
            self.day_root,
            {
                "action": "bookstore_service",
                "service": "browse_book",
                "book_id": "book_paper_joinery",
            },
            actor_side="subject",
            now_iso=OPEN_AT,
        )
        self.assertEqual(code_plain, 200)
        plain = response_plain["result"]
        self.assertNotIn("understood", plain)
        self.assertFalse(plain["full_text_dumped"])
        self.assertIn("toc", plain)

        # 未知字段仍被拒。
        code_bad, error_bad = mutate_v04_neighborhood_view(
            self.day_root,
            {
                "action": "bookstore_service",
                "service": "browse_book",
                "book_id": "book_paper_joinery",
                "focus_question": "纸盒折叠",
                "price": 0,
            },
            actor_side="subject",
            now_iso=OPEN_AT,
        )
        self.assertEqual(code_bad, 400)
        self.assertEqual(error_bad["reason_code"], "INVALID_FIELDS")
        self.assertIn("price", error_bad["invalid_fields"])

        # 伪分数走私字段仍被拒。
        code_mood, error_mood = mutate_v04_neighborhood_view(
            self.day_root,
            {
                "action": "bookstore_service",
                "service": "browse_book",
                "book_id": "book_paper_joinery",
                "focus_question": "纸盒折叠",
                "mood_delta": 3,
            },
            actor_side="subject",
            now_iso=OPEN_AT,
        )
        self.assertEqual(code_mood, 400)
        self.assertEqual(error_mood["reason_code"], "INVALID_FIELDS")

        # partner 无 book store 服务权限，仍 403。
        code_forbidden, error_forbidden = mutate_v04_neighborhood_view(
            self.day_root,
            {
                "action": "bookstore_service",
                "service": "browse_book",
                "book_id": "book_paper_joinery",
                "focus_question": "纸盒折叠",
            },
            actor_side="partner",
            now_iso=OPEN_AT,
        )
        self.assertEqual(code_forbidden, 403)
        self.assertEqual(error_forbidden["reason_code"], "FORBIDDEN_ACTOR_SIDE")


if __name__ == "__main__":
    unittest.main()

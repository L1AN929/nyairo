#!/usr/bin/env python3
"""v03 ecology HTTP 面白名单放行测试（T2 遗留③，2026-11-14 X1 批次）。

T2 在 bridge 域层 ``configure_outbound_quota`` 收录了 ``desktop_note_weekly_cap``
（周便签上限，int 非负 ≤50）与 ``min_outbound_interval_hours``（单次出站最小间隔，
int 非负 ≤720）两个宿主可调旋钮，但 ``components/phone/v03_ecology_view.py`` 的
ACTION_ALLOWED_FIELDS 白名单未放行——HTTP 面此前对这两个键一律 400 INVALID_FIELDS，
只能走设计默认值（2 / 48）。本批放行后覆盖：

* HTTP 配置生效：partner 经 mutate 配置两旋钮 → 200，读回投影
  ``data.reality_bridge.outbound_gate.policy`` 反映配置值；
* 校验规则对齐域层：非 int（字符串/浮点/bool）、负数、超上限（50/720）→ 400，
  reason_code 为域层 ``INVALID_INT:<field>``；0 与上限值合法；
* 白名单边界：未知变体字段仍 400 INVALID_FIELDS，既有旋钮行为不变。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from components.phone import v03_ecology_view as eco_view  # noqa: E402


def _configure(root: Path, extra: dict, now_iso: str = "2026-11-14T10:00:00+08:00"):
    payload = {"action": "configure_outbound_quota"}
    payload.update(extra)
    return eco_view.mutate_v03_ecology_view(root, payload, actor_side="partner",
                                            now_iso=now_iso)


def _policy(root: Path, now_iso: str = "2026-11-14T10:05:00+08:00") -> dict:
    code, body = eco_view.read_v03_ecology_view(root, now_iso=now_iso)
    assert code == 200, body
    return (((body.get("data") or {}).get("reality_bridge") or {})
            .get("outbound_gate") or {}).get("policy") or {}


class EcologyViewKnobWhitelistTest(unittest.TestCase):
    """HTTP 面两旋钮：白名单放行、配置生效、非法值 400。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="v03-eco-knobs-"))
        self.root = self.tmp / "instance"
        self.root.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_whitelist_accepts_both_knobs_and_configuration_takes_effect(self) -> None:
        code, body = _configure(self.root, {"desktop_note_weekly_cap": 5,
                                            "min_outbound_interval_hours": 72})
        self.assertEqual(code, 200)
        self.assertTrue(body.get("ok"))
        policy = _policy(self.root)
        self.assertEqual(policy.get("desktop_note_weekly_cap"), 5,
                         "HTTP 配置的周便签上限必须写进域层并出现在读回投影")
        self.assertEqual(policy.get("min_outbound_interval_hours"), 72,
                         "HTTP 配置的最小出站间隔必须写进域层并出现在读回投影")

    def test_domain_defaults_unchanged_without_configuration(self) -> None:
        policy = _policy(self.root)
        self.assertEqual(policy.get("desktop_note_weekly_cap"), 2)
        self.assertEqual(policy.get("min_outbound_interval_hours"), 48)

    def test_zero_is_a_valid_value_for_both_knobs(self) -> None:
        code, body = _configure(self.root, {"desktop_note_weekly_cap": 0,
                                            "min_outbound_interval_hours": 0})
        self.assertEqual(code, 200)
        policy = _policy(self.root)
        self.assertEqual(policy.get("desktop_note_weekly_cap"), 0)
        self.assertEqual(policy.get("min_outbound_interval_hours"), 0)

    def test_upper_bounds_align_with_the_domain_layer(self) -> None:
        code, _ = _configure(self.root, {"desktop_note_weekly_cap": 50,
                                         "min_outbound_interval_hours": 720})
        self.assertEqual(code, 200, "域层上限 50 / 720 必须在 HTTP 面同样可配")
        policy = _policy(self.root)
        self.assertEqual(policy.get("desktop_note_weekly_cap"), 50)
        self.assertEqual(policy.get("min_outbound_interval_hours"), 720)

    def test_non_int_values_are_refused_400(self) -> None:
        for bad in ("3", 1.5, True, [2]):
            with self.subTest(bad=bad):
                code, body = _configure(self.root, {"desktop_note_weekly_cap": bad})
                self.assertEqual(code, 400)
                self.assertFalse(body.get("ok"))
                self.assertEqual(body.get("reason_code"),
                                 "INVALID_INT:desktop_note_weekly_cap")
        for bad in ("48", 12.0, False, -1):
            with self.subTest(bad=bad):
                code, body = _configure(self.root, {"min_outbound_interval_hours": bad})
                self.assertEqual(code, 400)
                self.assertEqual(body.get("reason_code"),
                                 "INVALID_INT:min_outbound_interval_hours")

    def test_none_is_refused_by_the_view_empty_value_rule(self) -> None:
        # 视图层自己的既有约定：显式 None/空白值按 INVALID_FIELDS 拒（先于域层 int 校验），
        # 与同文件其余旋钮的空值行为一致。
        code, body = _configure(self.root, {"desktop_note_weekly_cap": None})
        self.assertEqual(code, 400)
        self.assertEqual(body.get("reason_code"), "INVALID_FIELDS")
        self.assertIn("desktop_note_weekly_cap", body.get("invalid_fields") or [])

    def test_over_bounds_are_refused_400(self) -> None:
        code, body = _configure(self.root, {"desktop_note_weekly_cap": 51})
        self.assertEqual(code, 400)
        self.assertEqual(body.get("reason_code"), "INVALID_INT:desktop_note_weekly_cap")
        code, body = _configure(self.root, {"min_outbound_interval_hours": 721})
        self.assertEqual(code, 400)
        self.assertEqual(body.get("reason_code"), "INVALID_INT:min_outbound_interval_hours")

    def test_unknown_variant_field_stays_outside_the_whitelist(self) -> None:
        code, body = _configure(self.root, {"desktop_note_weekly_caps": 5})
        self.assertEqual(code, 400)
        self.assertEqual(body.get("reason_code"), "INVALID_FIELDS")
        self.assertIn("desktop_note_weekly_caps", body.get("invalid_fields") or [])

    def test_existing_knobs_keep_working_alongside(self) -> None:
        code, body = _configure(self.root, {"weekly_proactive_cap": 4,
                                            "desktop_note_weekly_cap": 3,
                                            "min_outbound_interval_hours": 96})
        self.assertEqual(code, 200)
        policy = _policy(self.root)
        self.assertEqual(policy.get("weekly_proactive_cap"), 4)
        self.assertEqual(policy.get("desktop_note_weekly_cap"), 3)
        self.assertEqual(policy.get("min_outbound_interval_hours"), 96)


if __name__ == "__main__":
    unittest.main()

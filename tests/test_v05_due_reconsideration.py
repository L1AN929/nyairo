#!/usr/bin/env python3
"""v0.5：EP-12 已有「稍后事项」到点后被正式重新考虑（最小正式链 + 接缝负控）。

本模块只守这一块：

1. 纯域：改期的相对延迟按**本回合自己的观察时刻**解析，而不是校验发生时的墙钟；
   重放同一回合不漂移，越界/未知字段按名拒绝。
2. 纯域负控：存储不可读时如实报 ``READ_FAILED``/``FUTURE_ITEM_STATE_CORRUPT``，
   候选读取失败关闭（0 候选），不会把坏存储读成「没有事项」。
3. 真 owner 最小正式链：正式 ``AutonomousLifeRunner.run_due`` → AG-1 原始 JSON →
   AG-2/AR-0；到期前安静、到点进入材料/指纹/正式候选、同一到期集合再次安静、
   改期按回合时钟延后且不复活。无对象事项永远不作为 ``FUTURE_ITEM_DUE`` 候选。

替身只有 Decision 回答与内容回答（脚本形状，明示）。不宣称真模型自然采用、G1 或 24h。
POSIX 专用（真实 Supply Unix socket）；三个纯域用例在 Windows 也能跑。
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "scripts"), str(ROOT / "tests"),
              str(ROOT / "components"), str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "world" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_future_items as lfi  # noqa: E402

POSIX_ONLY = unittest.skipUnless(os.name == "posix",
                                 "real Supply socket needs POSIX (WSL/Linux)")

_IMPORT_ERROR: str | None = None
if os.name == "posix":  # pragma: no branch - native Windows skips the class below
    try:
        import v05_due_reconsideration_smoke as driver  # noqa: E402
    except Exception as error:  # noqa: BLE001
        _IMPORT_ERROR = "%s: %s" % (type(error).__name__, error)
        driver = None
else:  # pragma: no cover
    driver = None

needs_fixture = unittest.skipIf(_IMPORT_ERROR is not None,
                                "fixture unavailable: %s" % (_IMPORT_ERROR or ""))

#: 一个和真实墙钟明显不同的业务时刻：若改期的延迟被墙钟解析，断言立刻会红。
BUSINESS_NOW = "2026-10-23T09:09:00+00:00"


class RescheduleClockTest(unittest.TestCase):
    """改期延迟只按回合时钟解析；回显形状可重放；越界/未知字段按名拒绝。"""

    def _store(self, root: Path) -> "lfi.FutureItems":
        store = lfi.FutureItems(root, subject_id="subject:test")
        store.propose(title="回头再看这一段", in_minutes=10, now=BUSINESS_NOW)
        return store

    def test_a_relative_reschedule_is_resolved_against_the_round_clock(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(Path(tmp))
            item_ref = store.open_items()[0].item_ref
            actions = lfi.validate_actions(
                [{"item_ref": item_ref, "op": "reschedule", "in_minutes": 3}])
            self.assertEqual(len(actions), 1)
            # 回显：延迟和它此刻解析出的瞬间都在，重建的 DecisionRecord 可以再次校验。
            self.assertEqual(actions[0]["in_minutes"], 3)
            self.assertIn("due_at", actions[0])
            again = lfi.validate_actions(actions)
            self.assertEqual(again, actions)

            applied = lfi.apply_actions(store, actions, now=BUSINESS_NOW)
            self.assertEqual(applied[0]["status"], "APPLIED")
            moved = store.get(item_ref)
            self.assertEqual(moved.due_at, "2026-10-23T09:12:00+00:00")

    def test_a_reschedule_delay_outside_the_bounds_is_refused_by_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(Path(tmp))
            item_ref = store.open_items()[0].item_ref
            for minutes in (0, lfi.MAX_DELAY_MINUTES + 1):
                with self.assertRaises(lfi.FutureItemError) as ctx:
                    lfi.validate_actions(
                        [{"item_ref": item_ref, "op": "reschedule", "in_minutes": minutes}])
                self.assertEqual(ctx.exception.reason_code, "FUTURE_ITEM_DELAY_OUT_OF_BOUNDS")

    def test_unknown_fields_and_a_due_time_on_cancel_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(Path(tmp))
            item_ref = store.open_items()[0].item_ref
            with self.assertRaises(lfi.FutureItemError) as ctx:
                lfi.validate_actions([{"item_ref": item_ref, "op": "cancel", "due_at": BUSINESS_NOW}])
            self.assertEqual(ctx.exception.reason_code,
                             "FUTURE_ITEM_ACTION_DUE_TIME_NOT_APPLICABLE")
            with self.assertRaises(lfi.FutureItemError) as ctx:
                lfi.validate_actions([{"item_ref": item_ref, "op": "cancel", "extra": 1}])
            self.assertEqual(ctx.exception.reason_code, "FUTURE_ITEM_ACTION_UNKNOWN_FIELD")


class UnreadableStoreTest(unittest.TestCase):
    """坏存储是如实报告的故障，不是「没有到期事项」。"""

    def test_a_corrupt_store_is_reported_and_yields_no_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = lfi.FutureItems(root, subject_id="subject:test")
            (root / lfi.STATE_FILENAME).write_text("{not json", encoding="utf-8")
            view = store.view(now=BUSINESS_NOW)
            self.assertEqual(view["status"], "READ_FAILED")
            self.assertEqual(view["reason_code"], "FUTURE_ITEM_STATE_CORRUPT")
            # 候选读取失败关闭：坏存储不会变成一条「到期事项」。
            self.assertEqual(lfi.candidate_items(store, now=BUSINESS_NOW), [])
            with self.assertRaises(lfi.FutureItemError) as ctx:
                store.due(BUSINESS_NOW)
            self.assertEqual(ctx.exception.reason_code, "FUTURE_ITEM_STATE_CORRUPT")


@POSIX_ONLY
@needs_fixture
class DueReconsiderationRunnerTest(unittest.TestCase):
    """真 owner 最小正式链：到期前安静 → 到点进正式候选 → 同一集合安静 → 改期不复活。"""

    def setUp(self) -> None:
        self.root = driver._fresh_root()
        driver.prepare_fixture(self.root)
        self.clock = driver.Clock(driver.at(0))
        self.cognition = driver.ScriptedCognition()
        self.services = driver._start_services(self.root / "supply")
        self.chain = driver.open_chain(self.root, self.services, self.clock, self.cognition)
        self.cognition.seed_artifact = str(self.chain.data["artifact_id"])

    def tearDown(self) -> None:
        try:
            driver.close_chain(self.chain)
        finally:
            driver._stop_services(self.services)

    def test_a_due_item_is_reconsidered_at_its_time_and_rescheduled_on_the_round_clock(self):
        chain = self.chain
        seed = str(chain.data["artifact_id"])

        first = driver.window(chain, driver.at(0), "start_seed")
        self.assertEqual(first["outcome"], "ACTED", first)
        seed_head = driver.head_version(chain, seed)

        driver.window(chain, driver.at(2), "defer_propose",
                      {"title": "回头把这一段再改一遍", "in_minutes": 4,
                       "source_refs": ["artifact:%s" % seed]})
        saved = chain.cycle.future_items.open_items()
        self.assertEqual(len(saved), 1, saved)
        item_a = saved[0]
        self.assertEqual(item_a.due_at, driver.iso(driver.at(6)))
        self.assertEqual(item_a.origin, "MODEL_PROPOSAL")
        writer_calls = int(chain.writer.call_count)

        before = driver.window(chain, driver.at(3))
        self.assertEqual(before["status"], "QUIET", before)
        self.assertEqual(int(before["llm_calls"]), 0)
        self.assertEqual(int(chain.writer.call_count), writer_calls)
        self.assertEqual(chain.cycle.due_future_items(driver.iso(driver.at(3))), [])

        due = driver.window(chain, driver.at(7), "no_action_propose",
                            {"title": "把刚才那个念头写成一段", "in_minutes": 2})
        self.assertEqual(due["decision"], "NO_ACTION", due)
        self.assertIn(item_a.item_ref, driver.parts(due)["due_items"])
        self.assertIn(item_a.item_ref, driver.extra_items(chain))
        self.assertEqual(int(chain.writer.call_count), writer_calls)
        self.assertEqual(driver.head_version(chain, seed), seed_head)

        # 无对象事项：永远不是 FUTURE_ITEM_DUE 候选（只能作为既有项目选项，见冒烟）。
        item_b = next(row for row in chain.cycle.future_items.open_items()
                      if row.item_ref != item_a.item_ref)
        self.assertEqual(list(item_b.source_refs), [])
        self.assertNotIn(item_b.item_ref, driver.extra_items(chain))

        same = driver.window(chain, driver.at(8))
        self.assertEqual(same["status"], "QUIET", same)
        self.assertEqual(int(same["llm_calls"]), 0)

        # 接缝回归：改期的相对延迟按本轮 observed_at 解析，不是真实墙钟。
        driver.window(chain, driver.at(9), "no_action_actions",
                      {"actions": [{"item_ref": item_a.item_ref, "op": "reschedule",
                                    "in_minutes": 3}]})
        moved = chain.cycle.future_items.get(item_a.item_ref)
        self.assertIsNotNone(moved)
        self.assertEqual(moved.due_at, driver.iso(driver.at(12)))

        after = driver.window(chain, driver.at(10))
        self.assertNotEqual(after["status"], "ACTED", after)
        due_refs = [row["item_ref"] for row in
                    chain.cycle.due_future_items(driver.iso(driver.at(10)))]
        self.assertNotIn(item_a.item_ref, due_refs)
        self.assertEqual(due_refs, [item_b.item_ref])
        quiet = driver.window(chain, driver.at(11))
        self.assertEqual(quiet["status"], "QUIET", quiet)
        self.assertEqual(int(quiet["llm_calls"]), 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

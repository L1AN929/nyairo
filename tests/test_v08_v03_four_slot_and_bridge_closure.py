#!/usr/bin/env python3
"""v0.8 Wave 7: 07 章 §四四时段配额结转 + 日常桥接两处接缝打通（0 product-model calls）。

覆盖本单三件事：

* **四时段配额与结转**（``life_request_budget``）：``morning/afternoon/evening/night``
  基础配额 6/7/7/4 次与 12500/14000/14000/9500 tokens；同业务日内前一已过时段未用完
  配额折半滚入下一时段；跨午夜进入新业务日结转归零；日总量恒定受 24 / 50000 封顶；
  原有三槽 ``SLOT_QUOTAS`` 与默认路径逐字节不变。
* **宿主离线间隔**（``run_v02_v03_daily_tick``）：传 ``previous_host_iso`` 跨越 15 分钟
  阈值时按 ``evaluate_host_gap(previous_iso, current_iso)`` 的位置参数正确调用，不再
  ``TypeError``，并真实记入 ``HOST_GAP``。
* **桥接接缝**：``focused`` 真实焦点信号接入套路候选；``RECEIVED_UNOPENED`` 材料出
  至多 1 条低压力 ``v03_material:*`` 候选并可结算为 ``OPENED_PARTIAL``，已
  ``WITHDRAWN`` 的材料既不出候选也强制结算拒绝、零写入。

Run:  python3 -m unittest tests.test_v08_v03_four_slot_and_bridge_closure
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "phone"),
    str(ROOT / "tests"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import v02_v03_daily_loop_bridge as bridge  # noqa: E402
from components.life.scripts import life_dwell as v03_dwell  # noqa: E402
from components.life.scripts import life_request_budget as lrb  # noqa: E402
from components.world.scripts import v08_open_affordance as v08_open  # noqa: E402

T0 = "2026-11-15T09:00:00+08:00"
TZ8 = timezone(timedelta(hours=8))


def _dt(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=TZ8)


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-four-slot-bridge-")
        self.root = Path(self._tmp.name) / "instance"
        (self.root / "phone").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()


# ===========================================================================
# 1. 07 章 §四 四时段配额与结转
# ===========================================================================


class FourSlotQuotaCarryTest(_Base):
    def _budget(self) -> lrb.LifeRequestBudget:
        return lrb.LifeRequestBudget(self.root, utc_day_max=24, run_window_max=1000)

    def test_01_constants_match_ch07_four_slots(self) -> None:
        self.assertEqual(lrb.V03_FOUR_SLOT_QUOTAS, {
            "morning": {"calls": 6, "tokens": 12500},
            "afternoon": {"calls": 7, "tokens": 14000},
            "evening": {"calls": 7, "tokens": 14000},
            "night": {"calls": 4, "tokens": 9500},
        })
        self.assertEqual(lrb.V03_FOUR_SLOT_ORDER,
                         ("morning", "afternoon", "evening", "night"))
        self.assertEqual(lrb.V03_SLOT_CARRY_RATIO, 0.5)
        self.assertEqual(lrb.V03_FOUR_SLOT_DAILY_CALL_CAP, 24)
        self.assertEqual(lrb.V03_FOUR_SLOT_DAILY_TOKEN_CAP, 50000)

    def test_02_afternoon_inherits_half_of_morning_unused(self) -> None:
        budget = self._budget()
        morning = _dt(16, 8)
        afternoon = _dt(16, 13)

        # morning 6 次 / 12500 tokens：用掉 2 次、2000 tokens，剩 4 次、10500 tokens。
        for _ in range(2):
            budget.reserve(slot_mode=lrb.V03_SLOT_MODE, now=morning, est_tokens=1000)

        snapshot = budget.four_slot_snapshot(now=afternoon)
        afternoon_calls = snapshot["slots"]["afternoon"]["calls"]
        afternoon_tokens = snapshot["slots"]["afternoon"]["tokens"]
        self.assertEqual(afternoon_calls["base"], 7)
        self.assertEqual(afternoon_calls["carry"], 2)          # floor(4 * 0.5)
        self.assertEqual(afternoon_calls["effective"], 9)      # 7 + 2
        self.assertEqual(afternoon_tokens["base"], 14000)
        self.assertEqual(afternoon_tokens["carry"], 5250)      # floor(10500 * 0.5)
        self.assertEqual(afternoon_tokens["effective"], 19250)  # 14000 + 5250

        # 午段用满 9 次后第 10 次触顶拒止（同一业务日总消耗仍远低于 24）。
        for _ in range(9):
            budget.reserve(slot_mode=lrb.V03_SLOT_MODE, now=afternoon, est_tokens=1000)
        with self.assertRaises(lrb.RequestBudgetError) as caught:
            budget.reserve(slot_mode=lrb.V03_SLOT_MODE, now=afternoon, est_tokens=1000)
        self.assertEqual(caught.exception.reason_code, lrb.REASON_V03_SLOT_QUOTA_EXCEEDED)

        spent = budget.four_slot_snapshot(now=afternoon)
        self.assertEqual(spent["total_calls_used"], 11)
        self.assertLessEqual(spent["total_calls_used"], lrb.V03_FOUR_SLOT_DAILY_CALL_CAP)
        self.assertLessEqual(spent["total_tokens_used"], lrb.V03_FOUR_SLOT_DAILY_TOKEN_CAP)

    def test_03_cross_midnight_clears_carry(self) -> None:
        budget = self._budget()
        day1_morning = _dt(16, 8)
        day1_afternoon = _dt(16, 13)
        day2_morning = _dt(17, 8)

        for _ in range(2):
            budget.reserve(slot_mode=lrb.V03_SLOT_MODE, now=day1_morning, est_tokens=1000)
        budget.reserve(slot_mode=lrb.V03_SLOT_MODE, now=day1_afternoon, est_tokens=1000)

        # 进入第二天早上：新业务日没有已过时段，结转彻底清零。
        fresh = budget.four_slot_snapshot(now=day2_morning)
        self.assertEqual(fresh["state"], lrb.STATE_ABSENT)
        self.assertEqual(fresh["total_calls_used"], 0)
        for slot in lrb.V03_FOUR_SLOT_ORDER:
            self.assertEqual(fresh["slots"][slot]["calls"]["carry"], 0, slot)
            self.assertEqual(fresh["slots"][slot]["tokens"]["carry"], 0, slot)
        self.assertEqual(fresh["daily_call_cap"], 24)
        self.assertEqual(fresh["daily_token_cap"], 50000)

        budget.reserve(slot_mode=lrb.V03_SLOT_MODE, now=day2_morning, est_tokens=1000)
        after = budget.four_slot_snapshot(now=day2_morning)
        self.assertEqual(after["total_calls_used"], 1)

    def test_04_three_slot_and_default_paths_are_untouched(self) -> None:
        # 既有三槽常量原封不动。
        self.assertEqual(lrb.SLOT_QUOTAS, {
            "DAYTIME_ACTIVE": 2,
            "EVENING_WIND_DOWN": 1,
            "NIGHT_REST": 0,
        })
        default_root = Path(self._tmp.name) / "default_path"
        budget = lrb.LifeRequestBudget(default_root, utc_day_max=24, run_window_max=1000)
        now = _dt(16, 10)
        budget.reserve("decision", now=now)
        raw = json.loads(budget.path.read_text(encoding="utf-8"))
        self.assertNotIn("v03_four_slot_days", raw,
                         "default reserve must not add the optional four-slot sub-key")
        # can_spend without the four-slot mode is exactly check().
        self.assertEqual(budget.can_spend(now=now), budget.check(now=now))

    def test_05_unknown_slot_is_an_explicit_refusal(self) -> None:
        budget = self._budget()
        verdict = budget.check_v03_four_slot(slot_name="midnight_snack", now=_dt(16, 10))
        self.assertFalse(verdict["allowed"])
        self.assertEqual(verdict["reason_code"], lrb.REASON_UNKNOWN_V03_SLOT)


# ===========================================================================
# 2. run_v02_v03_daily_tick 宿主离线间隔（无 TypeError）
# ===========================================================================


class HostGapTickTest(_Base):
    def test_01_tick_records_host_gap_without_type_error(self) -> None:
        previous = "2026-11-16T08:00:00+08:00"
        now = "2026-11-16T10:00:00+08:00"  # 2h offline > 15min threshold

        out = bridge.run_v02_v03_daily_tick(
            self.root, now_iso=now, previous_host_iso=previous
        )
        self.assertTrue(out["ok"], out)
        report = out["host_gap"]
        self.assertIsInstance(report, dict)
        self.assertEqual(report["kind"], v03_dwell.HOST_GAP_KIND)
        self.assertEqual(report["offline_seconds"], 7200)

        state, err = v03_dwell.read_life_dwell_state(self.root)
        self.assertIsNone(err)
        gaps = state.get("host_gaps") or []
        self.assertTrue(gaps, "the real HOST_GAP must be persisted into life_dwell state")
        self.assertEqual(gaps[-1]["kind"], v03_dwell.HOST_GAP_KIND)
        self.assertEqual(out["llm_calls"], 0)

    def test_02_no_previous_instant_is_a_quiet_none(self) -> None:
        out = bridge.run_v02_v03_daily_tick(
            self.root, now_iso="2026-11-16T10:00:00+08:00"
        )
        self.assertTrue(out["ok"], out)
        self.assertIsNone(out["host_gap"])


# ===========================================================================
# 3. focused 焦点信号接入套路候选
# ===========================================================================


class FocusedCueBridgeTest(_Base):
    def _write_focus_signal(self) -> None:
        (self.root / "phone" / "workbench_tray.json").write_text(
            json.dumps(
                {
                    "schema": "nyairo.workbench_tray.v1",
                    "revision": 1,
                    "tray": {
                        "images": [],
                        "materials": [],
                        "work": {
                            "artifact_id": "work_letter",
                            "baseline_version_id": "v1",
                            "baseline_version_number": 1,
                        },
                        "note": None,
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def test_01_focused_routine_is_collected_when_a_real_focus_exists(self) -> None:
        self._write_focus_signal()
        self.assertTrue(v08_open.focused_state_zero_write(self.root))

        routine = v08_open.save_personal_routine(
            self.root,
            {
                "title_cn": "忙起来时先摊开手边的纸",
                "when_cue": "focused",
                "steps": [
                    {"kind": "verb", "object_id": "home_window",
                     "verb": "TOGGLE_ADJUST", "setting_value": "ajar"},
                    {"kind": "trace", "object_id": "home_desk",
                     "gesture_cn": "把纸摊开", "state_sticker_cn": "桌面清出一块地方"},
                ],
            },
            now_iso=T0,
        )

        collected = bridge.collect_v02_v03_loop_candidates(self.root, now_iso=T0)
        matched = [
            desc for desc in collected["descriptors"].values()
            if desc.get("action_family") == "run_personal_routine"
            and desc.get("source_ref") == "v08_routine:%s" % routine["routine_id"]
        ]
        self.assertEqual(len(matched), 1, "the focused routine must surface on a real focus")
        self.assertEqual(collected["llm_calls"], 0)

    def test_02_focused_routine_stays_quiet_without_a_focus_signal(self) -> None:
        routine = v08_open.save_personal_routine(
            self.root,
            {
                "title_cn": "只在专注时才做的小套路",
                "when_cue": "focused",
                "steps": [
                    {"kind": "verb", "object_id": "home_window",
                     "verb": "TOGGLE_ADJUST", "setting_value": "ajar"},
                    {"kind": "trace", "object_id": "home_desk",
                     "gesture_cn": "把纸摊开", "state_sticker_cn": "桌面清出一块地方"},
                ],
            },
            now_iso=T0,
        )
        self.assertFalse(v08_open.focused_state_zero_write(self.root))
        collected = bridge.collect_v02_v03_loop_candidates(self.root, now_iso=T0)
        refs = {desc.get("source_ref") for desc in collected["descriptors"].values()}
        self.assertNotIn("v08_routine:%s" % routine["routine_id"], refs)


# ===========================================================================
# 4. 伴侣递交材料：低压力候选 + OPENED_PARTIAL 结算 + WITHDRAWN 拒止
# ===========================================================================


class SharedMaterialBridgeTest(_Base):
    MATERIAL_ID = "mat_linen_note"

    def _seed_material(self, status: str = "RECEIVED_UNOPENED") -> None:
        payload = {
            "schema_version": "life.v03.encounters.v1",
            "revision": 1,
            "encounters": [],
            "npc_errands_and_hearsay": [],
            "shared_materials": [
                {
                    "material_id": self.MATERIAL_ID,
                    "title_cn": "关于晾晒布巾的一页短文",
                    "excerpt_cn": "阴天晾布要留缝，别贴着墙。",
                    "topic_tag": "晾晒",
                    "page_count": 3,
                    "status": status,
                    "opened_pages": [],
                    "reaction_cn": "",
                    "used_in_artifact_id": None,
                    "dropped_at": T0,
                    "updated_at": T0,
                }
            ],
            "updated_at": T0,
        }
        (self.root / "phone" / bridge.V03_ENCOUNTERS_FILENAME).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

    def _material_descriptors(self, collected: dict) -> list[dict]:
        return [
            desc for desc in collected["descriptors"].values()
            if str(desc.get("source_ref") or "").startswith("v03_material:")
        ]

    def test_01_received_unopened_material_becomes_one_candidate(self) -> None:
        self._seed_material()
        collected = bridge.collect_v02_v03_loop_candidates(self.root, now_iso=T0)
        descs = self._material_descriptors(collected)
        self.assertEqual(len(descs), 1)
        desc = descs[0]
        self.assertEqual(desc["action_family"], "open_shared_material")
        self.assertEqual(desc["source_ref"], "v03_material:%s" % self.MATERIAL_ID)
        self.assertEqual(desc["material_id"], self.MATERIAL_ID)
        self.assertTrue(str(desc.get("title_hint") or "").strip())
        self.assertEqual(collected["llm_calls"], 0)

    def test_02_settlement_advances_to_opened_partial_and_is_idempotent(self) -> None:
        self._seed_material()
        collected = bridge.collect_v02_v03_loop_candidates(self.root, now_iso=T0)
        desc = self._material_descriptors(collected)[0]
        event = bridge.v02_v03_round_event_for_selected_candidate(
            collected, desc["source_ref"], decision_id="material-dec-1"
        )
        self.assertIsNotNone(event)
        self.assertEqual(event["action_kind"], "OPEN_SHARED_MATERIAL")
        self.assertEqual(event["material_id"], self.MATERIAL_ID)

        settled = bridge.settle_v02_v03_round_consequences(self.root, event, now_iso=T0)
        self.assertEqual(settled["status"], "APPLIED", settled)
        self.assertIn("phone.v03_encounters", settled["slices_updated"])
        self.assertEqual(settled["llm_calls"], 0)

        doc = json.loads(
            (self.root / "phone" / bridge.V03_ENCOUNTERS_FILENAME).read_text(encoding="utf-8")
        )
        row = doc["shared_materials"][0]
        self.assertEqual(row["status"], "OPENED_PARTIAL")
        self.assertEqual(row["opened_pages"], [1])

        replay = bridge.settle_v02_v03_round_consequences(self.root, event, now_iso=T0)
        self.assertTrue(replay.get("already_applied"), replay)

    def test_03_withdrawn_material_never_becomes_a_candidate_and_refuses_settlement(self) -> None:
        self._seed_material(status="WITHDRAWN")
        collected = bridge.collect_v02_v03_loop_candidates(self.root, now_iso=T0)
        self.assertEqual(self._material_descriptors(collected), [])

        raw_before = (self.root / "phone" / bridge.V03_ENCOUNTERS_FILENAME).read_bytes()
        forced_event = {
            "actor_side": "life_resident",
            "effect_state": "APPLIED",
            "action_kind": "OPEN_SHARED_MATERIAL",
            "material_id": self.MATERIAL_ID,
            "open_mode": "open_partial",
            "receipt_ref": "v02v03:cand:v03_material_test",
            "operation_key": "forced-withdrawn-material-op",
            "decision_id": "forced-withdrawn-dec",
        }
        refused = bridge.settle_v02_v03_round_consequences(
            self.root, forced_event, now_iso=T0
        )
        self.assertEqual(refused["status"], "REFUSED")
        self.assertEqual(refused["reason_code"], "MATERIAL_WITHDRAWN")
        self.assertEqual(refused["optimistic_writes"], 0)
        raw_after = (self.root / "phone" / bridge.V03_ENCOUNTERS_FILENAME).read_bytes()
        self.assertEqual(raw_before, raw_after, "a refused settlement writes nothing")


if __name__ == "__main__":
    unittest.main()

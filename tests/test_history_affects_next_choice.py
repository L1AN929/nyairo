#!/usr/bin/env python3
"""0.4「历史影响下一次选择」闭环 + 两个负控的定向证据（18 章 §二 Day2 / 14 章 §三.4 / 36 章）。

覆盖四件事：

1. **已购抑制·购买候选（A/B）**：经济 owner 的柜台投影对「已拥有同款」的固定家具
   带持有事实与 ``ALREADY_OWNED_SUPPRESSED``（无持有 -> 候选在；持有同款 ->
   带抑制码）；候选源 ``observed_purchase_opportunities`` 据此把同款候选**滤掉**，
   未持有的可负担候选原样保留。消耗品（线上小店虚拟小点心）不抑制——吃掉的
   证据在 Body substrate，经济账本永不记录消耗，按 OWNED 抑制会杀死买-吃循环。
2. **已购抑制·心愿候选（A/B）**：``arbitrate_six_concerns`` 对 possession 关切的
   focus/origin 命中调用方带来的持有清单（``ownership_context``）时按同一
   reason_code 过滤；不传持有清单时行为与从前逐字节一致。
3. **负控 (a) in-flight 期间 PAUSE 必拒**：共在入口（``FiniteCoPresence.begin``）
   在前台有 in-flight 步（LR-3 拒绝立即中断、只答边界暂停）时，显式拒绝
   （``INTERRUPTION_NOT_APPLIED_NOW``）：不落会话、不启动共同活动、不采用任何
   候选。闸门已在（``life_co_presence.begin``），本文件只补负控测试。
4. **负控 (b) small_treat 信封超额拒付**：possession 关切超出
   ``available_small_treat_jpy`` 信封时按 ``DEFERRED_BY_BUDGET_ENVELOPE`` 拒付，
   不被选中、不落任何满足账（frustration_debt/overdue_penalty 恒 0，关切保持
   ACTIVE，satisfied_count 不动）。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT, ROOT / "components" / "life" / "scripts",
              ROOT / "components" / "world" / "scripts"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import world_property_economy as wpe  # noqa: E402
import world_move_opportunity_source as wmos  # noqa: E402
import everyday_desire_lifecycle as edl  # noqa: E402

NOW = datetime(2026, 10, 20, 3, 0, tzinfo=timezone.utc)  # +08:00 的 11:00，柜台营业中
OCCURRED_AT = "2026-10-20T03:00:00+00:00"


class _StoreFixture(unittest.TestCase):
    """经济 owner 的隔离 home：可写 state 路径 + 营业环境（同 test_phone_shopping_owner 模式）。"""

    ENVIRONMENT = {
        "CHIYO_WORLD_PROPERTY_ECONOMY_ENABLED": "1",
        "CHIYO_WORLD_LIVING_SKELETON_ENABLED": "1",
        "CHIYO_WORLD_DAILY_RHYTHM_ENABLED": "1",
    }

    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="nyairo-owned-ab-"))
        self.addCleanup(lambda: None)  # 夹具根保留不删（本仓测试惯例）
        (self.home / "data" / "world").mkdir(parents=True, exist_ok=True)
        # 她就在柜台（store_shop_projection 只做裸 json 读，不校验世界包）。
        (self.home / "data" / "world" / "world_state.json").write_text(json.dumps({
            "schema_version": "world.foundation.v0",
            "revision": 1,
            "world_id": "shiomi_city",
            "timezone": "Asia/Tokyo",
            "location": {"place_id": wpe.STORE_LOCATION_ID, "area_id": "front"},
        }, ensure_ascii=False), encoding="utf-8")

    def write_state(self, *, catalog_item_id: str, owned: bool) -> None:
        state = wpe._empty_state(created_at="2026-10-20T00:00:00+00:00")
        if owned:
            provenance = {
                "source": wpe.STORE_PURCHASE_SOURCE,
                "purchase_intent_id": "op-fixture-owned",
                "transaction_id": "purchase-fixture-owned",
                "delivery": wpe.STORE_DELIVERY_PROVENANCE,
                "source_turn_id": "turn-fixture",
            }
            state["transactions"] = [{
                "transaction_id": "purchase-fixture-owned",
                "transaction_kind": "PURCHASE",
                "direction": "DEBIT",
                "amount": 5_800,
                "currency": wpe.CURRENCY,
                "occurred_at": "2026-10-20T00:00:00+00:00",
                "catalog_item_id": catalog_item_id,
                "purchase_intent_id": "op-fixture-owned",
                "delivery_location": wpe.HOME_ITEM_LOCATION,
                "provenance": provenance,
            }]
            state["items"] = [{
                "item_instance_id": "item-fixture-owned",
                "catalog_item_id": catalog_item_id,
                "owner": wpe.OWNER,
                "acquired_at": "2026-10-20T00:00:00+00:00",
                "acquisition_kind": "PURCHASE",
                "acquisition_provenance": provenance,
                "acquisition_price": 5_800,
                "currency": wpe.CURRENCY,
                "current_location": wpe.HOME_ITEM_LOCATION,
                "disposition": wpe.DISPOSITION_OWNED,
            }]
        validated = wpe.validate_state(state)
        state_dir = wpe.state_path(self.home).parent
        state_dir.mkdir(parents=True, exist_ok=True)
        wpe.state_path(self.home).write_text(
            json.dumps(validated, ensure_ascii=False, sort_keys=True), encoding="utf-8")

    def store_projection(self) -> dict:
        projection = wpe.store_shop_projection(
            hermes_home=self.home, environment=dict(self.ENVIRONMENT), now=NOW)
        self.assertEqual(projection["status"], "available", projection)
        return projection

    def entry(self, projection: dict, catalog_item_id: str) -> dict:
        return next(item for item in projection["catalog"]
                    if item["catalog_item_id"] == catalog_item_id)

    def purchase_candidates(self, projection: dict) -> list[str]:
        observation = {
            "world_id": "shiomi_city", "world_revision": 11,
            "location": {"place_id": wpe.STORE_LOCATION_ID},
        }
        opportunities = wmos.observed_purchase_opportunities(
            observation, projection=projection, occurred_at=OCCURRED_AT,
            observed_at=OCCURRED_AT)
        return [row["target_refs"][0] for row in opportunities]


class AlreadyOwnedPurchaseABTest(_StoreFixture):
    """A/B：无持有 -> 候选在；持有同款 -> 抑制码在且候选消失。"""

    def test_arm_a_without_ownership_the_candidate_is_on_the_shelf(self) -> None:
        self.write_state(catalog_item_id="basic_bed", owned=False)
        projection = self.store_projection()
        bed = self.entry(projection, "basic_bed")
        self.assertNotIn("suppress_reason_code", bed)
        self.assertFalse(bed.get("already_owned", False))
        self.assertEqual(bed["owned_same_item_count"], 0)
        self.assertIn("buy:basic_bed", self.purchase_candidates(projection))

    def test_arm_b_owned_same_item_is_suppressed_and_filtered(self) -> None:
        self.write_state(catalog_item_id="basic_bed", owned=True)
        projection = self.store_projection()
        bed = self.entry(projection, "basic_bed")
        self.assertTrue(bed["already_owned"])
        self.assertEqual(bed["suppress_reason_code"], wpe.ALREADY_OWNED_SUPPRESSED)
        self.assertEqual(bed["owned_same_item_count"], 1)
        # 候选层过滤：同款消失；未持有的可负担候选（简易椅子 1800 <= 余额）原样在。
        candidates = self.purchase_candidates(projection)
        self.assertNotIn("buy:basic_bed", candidates)
        self.assertIn("buy:simple_chair", candidates)

    def test_consumable_is_never_suppressed_by_ownership(self) -> None:
        # 消耗品不按 OWNED 抑制（吃掉的证据在 Body substrate，经济账本不记消耗）。
        self.write_state(catalog_item_id="virtual_snack_wafer", owned=True)
        projection = wpe.phone_shop_projection(
            hermes_home=self.home, environment=dict(self.ENVIRONMENT), now=NOW)
        self.assertEqual(projection["status"], "available")
        wafer = next(item for item in projection["catalog"]
                     if item["catalog_item_id"] == "virtual_snack_wafer")
        self.assertNotIn("suppress_reason_code", wafer)


class AlreadyOwnedWishABTest(unittest.TestCase):
    """A/B：心愿层按调用方带来的持有清单抑制 possession 关切。"""

    @staticmethod
    def _possession_concern(catalog_item_id: str = "basic_bed") -> dict:
        return {
            "concern_ref": "ccn_bed",
            "domain": "possession",
            "focus_ref": catalog_item_id,
            "origin_refs": [catalog_item_id],
            "origin_kind": "sustained_interest",
            "expression": "想要一张基本床",
            "urgency": "optional",
            "persistence": "retained",
            "cost_scale": "needs_breakpoint",
            "estimated_cost_jpy": 5_800,
        }

    def test_arm_a_without_ownership_context_behaviour_is_unchanged(self) -> None:
        arb = edl.arbitrate_six_concerns([self._possession_concern()])
        self.assertEqual(arb["chosen_primary"]["concern_ref"], "ccn_bed")
        self.assertEqual(arb["filtered_concerns"], [])

    def test_arm_b_owned_same_item_wish_is_suppressed(self) -> None:
        arb = edl.arbitrate_six_concerns(
            [self._possession_concern()],
            ownership_context={"owned_catalog_item_ids": ["basic_bed"]},
        )
        self.assertIsNone(arb["chosen_primary"])
        filtered = arb["filtered_concerns"]
        self.assertEqual(len(filtered), 1, filtered)
        self.assertEqual(filtered[0]["reason_code"], wpe.ALREADY_OWNED_SUPPRESSED)
        self.assertEqual(filtered[0]["concern_ref"], "ccn_bed")

    def test_unrelated_owned_item_does_not_suppress_another_wish(self) -> None:
        arb = edl.arbitrate_six_concerns(
            [self._possession_concern("simple_chair")],
            ownership_context={"owned_catalog_item_ids": ["basic_bed"]},
        )
        self.assertEqual(arb["chosen_primary"]["concern_ref"], "ccn_bed")
        self.assertEqual(arb["filtered_concerns"], [])


@unittest.skipIf(os.name == "nt", "共在协议需要 POSIX（fcntl）")
class CoPresenceInFlightPauseNegativeTest(unittest.TestCase):
    """负控 (a)：in-flight 期间 PAUSE 必拒（闸门已在 begin，补专用负控）。"""

    INVITATION = {
        "event_kind": "CO_PRESENCE_INVITATION",
        "invitation_id": "inv:neg-a",
        "source_ref": "channel:neg-test",
        "occurred_at": "2026-10-16T06:00:00+00:00",
        "scope": "一起待一会儿",
        "requires_engagement": True,
        "duration_seconds": 7200,
    }

    def _co_presence(self, tmp: Path, *, interrupt_effect: str):
        import life_co_presence as cop

        assessment = {"activity_effect": interrupt_effect,
                      "activity_id": "activity:private",
                      "applied_transition_ref": None}

        class _Runtime:
            interruption_coordinator = SimpleNamespace(
                # LR-3 的现状视图：可中断的私人前台（consider 因此 ACCEPT）。
                get_current_life_view=lambda: {
                    "interruptibility": "IMMEDIATE",
                    "communication_availability": "OPEN",
                },
                assess_events=lambda events, apply_immediate_pause: [assessment])
            activity_read = SimpleNamespace(
                list_open_activities=lambda: [{
                    "activity_id": "activity:private", "status": "ACTIVE",
                    "interruptibility": "IMMEDIATE", "revision": 3,
                }])
            waiting_coordinator = None
            request_ledger = None

        return cop.FiniteCoPresence(
            _Runtime(), state_root=tmp, subject_id="subject:neg",
            home="/tmp/nyairo-neg-home", canonical_subject="chiyo.global",
            cycle=SimpleNamespace(budget=None))

    def test_in_flight_pause_is_refused_and_nothing_starts(self) -> None:
        # in-flight：LR-3 不立即中断（前台有未到边界的步），只答边界暂停。
        with tempfile.TemporaryDirectory() as tmp:
            co = self._co_presence(Path(tmp), interrupt_effect="PAUSE_AT_BOUNDARY")
            result = co.begin(self.INVITATION, now="2026-10-16T06:01:00+00:00")
            self.assertFalse(result["ok"], result)
            self.assertEqual(result["reason_code"], "INTERRUPTION_NOT_APPLIED_NOW")
            self.assertFalse(result.get("pause_applied"))
            self.assertFalse(result.get("activity_started"))
            # 未对账的 in-flight 步没有被强行打断，共同会话没有落盘。
            self.assertEqual(co.read_session()["state"], "NONE")

    def test_unreconciled_foreground_never_yields_a_joint_activity(self) -> None:
        # 同语义的第二种 owner 回答（NO_INTERRUPT）同样必须显式拒绝。
        with tempfile.TemporaryDirectory() as tmp:
            co = self._co_presence(Path(tmp), interrupt_effect="NO_INTERRUPT")
            result = co.begin(self.INVITATION, now="2026-10-16T06:01:00+00:00")
            self.assertFalse(result["ok"], result)
            self.assertEqual(result["stage"], "NOT_INTERRUPTED")
            self.assertEqual(co.read_session()["state"], "NONE")


class SmallTreatEnvelopeNegativeTest(unittest.TestCase):
    """负控 (b)：信封超额拒付带 DEFERRED_BY_BUDGET_ENVELOPE 且不落账。"""

    def _concern(self) -> dict:
        return {
            "concern_ref": "ccn_snack",
            "domain": "possession",
            "focus_ref": "virtual_snack_wafer",
            "origin_refs": ["virtual_snack_wafer"],
            "origin_kind": "immediate_opportunity",
            "expression": "想吃一份小点心",
            "urgency": "optional",
            "persistence": "retained",
            "cost_scale": "needs_breakpoint",
            "estimated_cost_jpy": 1000,
        }

    def test_over_envelope_is_refused_with_reason_code_and_no_ledger_write(self) -> None:
        arb = edl.arbitrate_six_concerns(
            [self._concern()],
            budget_context={"available_small_treat_jpy": 240},
        )
        self.assertIsNone(arb["chosen_primary"], arb)
        filtered = arb["filtered_concerns"]
        self.assertEqual(len(filtered), 1, filtered)
        self.assertEqual(filtered[0]["reason_code"], "DEFERRED_BY_BUDGET_ENVELOPE")
        # 不落账：零挫败债、零逾期惩罚、零满足加成（35 章 §一 的恒等式）。
        self.assertEqual(arb["frustration_debt"], 0)
        self.assertEqual(arb["overdue_penalty"], 0)
        self.assertEqual(arb["happiness_bonus_on_completion"], 0)

    def test_apply_path_leaves_the_concern_active_and_counters_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            day_root = Path(tmp)
            emerge = edl.apply_everyday_desire_action(day_root, {
                "action": "emerge_concern",
                "domain": "possession",
                "origin_kind": "immediate_opportunity",
                "origin_refs": ["virtual_snack_wafer"],
                "expression": "想吃一份小点心",
                "persistence": "retained",
                "urgency": "optional",
                "cost_scale": "needs_breakpoint",
                "estimated_cost_jpy": 1000,
                "concern_ref": "ccn_snack",
            }, now_iso=OCCURRED_AT)
            self.assertEqual(emerge["status"], "applied")
            before = edl.read_everyday_desire_state(day_root)[0]
            proj = edl.apply_everyday_desire_action(day_root, {
                "action": "arbitrate_concerns",
                "budget_context": {"available_small_treat_jpy": 240},
            }, now_iso="2026-10-20T12:01:00+00:00")
            arb = proj["action_receipt"]["arbitration"]
            self.assertIsNone(arb["chosen_primary"])
            self.assertEqual(arb["filtered_concerns"][0]["reason_code"],
                             "DEFERRED_BY_BUDGET_ENVELOPE")
            after = edl.read_everyday_desire_state(day_root)[0]
            # 关切保持 ACTIVE（没有"付掉"），满足计数不动， revision 只因仲裁 +1。
            self.assertEqual(after["satisfied_count"], before["satisfied_count"])
            self.assertEqual(after["retained_concerns"][0]["concern_ref"], "ccn_snack")
            self.assertEqual(after["retained_concerns"][0]["status"], "ACTIVE")

    def test_within_envelope_still_pays_normally(self) -> None:
        arb = edl.arbitrate_six_concerns(
            [self._concern()],
            budget_context={"available_small_treat_jpy": 1200},
        )
        self.assertEqual(arb["chosen_primary"]["concern_ref"], "ccn_snack")
        self.assertEqual(arb["filtered_concerns"], [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

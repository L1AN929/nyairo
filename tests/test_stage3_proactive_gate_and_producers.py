#!/usr/bin/env python3
"""Stage 3 Worker 1 verification suite:
- Unified 7-step proactive anti-harassment gate & honest outbox delivery lifecycle
  (`v03_economy_illness_bridge.py`: APPROVED_FOR_DELIVERY -> claim -> ack_delivered/ack_read/ack_failed/ack_unknown)
- F-113 bridge (`v02_runtime_governance_migration.py`) preserving legacy error order
- High-threshold lived outreach producers (`proactive_outreach_producer.py`) + suppression gates
- Bounded zero-debt missing_partner maturation (`emotional_tension_view.py`)
- Settlement hooks (`v02_v03_daily_loop_bridge.py`)
- File hygiene (pure LF, UTF-8 no BOM, 0 unicode.So) & frozen invariants (37/1199/1800/24).
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unicodedata
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (
    REPO_ROOT,
    REPO_ROOT / "chiyo_bundle",
    REPO_ROOT / "scripts",
    REPO_ROOT / "components",
    REPO_ROOT / "components" / "world" / "scripts",
    REPO_ROOT / "components" / "life" / "scripts",
    REPO_ROOT / "components" / "phone",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from chiyo_bundle import lived_context_bridge  # noqa: E402
from chiyo_bundle import phone_tool  # noqa: E402
from components.life.scripts import proactive_outreach_producer as outreach_prod  # noqa: E402
from components.life.scripts import v02_runtime_governance_migration as rm_engine  # noqa: E402
from components.life.scripts import v02_v03_daily_loop_bridge as loop_bridge  # noqa: E402
from components.life.scripts import v05_dreams_desires_foreshadow as v05_spark  # noqa: E402
from components.phone import emotional_tension_view as et_view  # noqa: E402
from components.phone import v02_runtime_migration_view as rm_view  # noqa: E402
from components.world.scripts import v03_economy_illness_bridge as v03_eco  # noqa: E402
from components.world.scripts import v04_neighborhood_living as v04_nb  # noqa: E402
from components.world.scripts import v05_living_town_engine as v05_town  # noqa: E402
from plugins import chiyo as chiyo_plugin  # noqa: E402
from scripts import v02_wall_clock_soak as soak_runner  # noqa: E402

BJT = timezone(timedelta(hours=8))


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int]]:
    snap: dict[str, tuple[int, int]] = {}
    if not root.exists():
        return snap
    for p in sorted(root.rglob("*")):
        try:
            st = p.stat()
            snap[str(p.relative_to(root))] = (st.st_size, int(st.st_mtime_ns))
        except OSError:
            continue
    return snap


def _find_dream_hit_seed() -> str:
    for idx in range(500):
        cand = f"stage3-dream-hit-{idx}"
        digest = hashlib.sha256(cand.encode("utf-8")).digest()
        if int.from_bytes(digest[:4], "big") % 10000 < v05_spark.DREAM_CHANCE_BPS:
            return cand
    raise RuntimeError("Could not find deterministic dream-hitting seed")


def _init_calm_v03_ecology(root: Path, now_iso: str = "2026-11-20T08:00:00+08:00") -> None:
    v03_eco.apply_v03_ecology_command(
        root,
        {"action": "configure_outbound_quota", "daily_proactive_quota": 2},
        actor_side="partner",
        now_iso=now_iso,
    )


class TestStage3HonestOutboxAndSevenStepGate(unittest.TestCase):
    def test_01_honest_outbox_lifecycle_claim_deliver_read_failed_unknown_idempotent(self) -> None:
        self.assertEqual(
            v03_eco.OUTBOUND_STATUSES,
            (
                "APPROVED_FOR_DELIVERY",
                "CLAIMED_BY_TRANSPORT",
                "DELIVERED",
                "READ",
                "UNKNOWN",
                "FAILED",
                "WITHDRAWN",
            ),
        )
        with tempfile.TemporaryDirectory(prefix="s3-outbox-life-") as td:
            root = Path(td)
            t0 = "2026-11-20T09:00:00+08:00"

            # Zero-write read on empty root
            before_empty = _snapshot_tree(root)
            empty_pending = v03_eco.read_pending_proactive_outbox_zero_write(root, now_iso=t0)
            self.assertEqual(before_empty, _snapshot_tree(root))
            self.assertTrue(empty_pending["ok"])
            self.assertTrue(empty_pending["zero_write"])
            self.assertEqual(empty_pending["llm_calls"], 0)
            self.assertEqual(empty_pending["pending_count"], 0)
            self.assertFalse(empty_pending["quiet_hours_active"])

            # Enqueue via honest_outbox -> APPROVED_FOR_DELIVERY (NEVER fake DELIVERED!)
            res = v03_eco.attempt_proactive_outbound(
                root,
                text="早上窗台结了薄霜，顺手温了杯麦茶。",
                channel="desktop_note",
                reason_kind="genuine_life_share",
                delivery_mode="honest_outbox",
                operation_key="op_honest_1",
                now_iso=t0,
            )
            self.assertEqual(res["command_status"], "APPLIED")
            rcpt = res["receipt"]
            out_id = rcpt["outbound_id"]
            self.assertEqual(rcpt["outbound_status"], "APPROVED_FOR_DELIVERY")
            self.assertEqual(rcpt["ack_state"], "QUEUED_FOR_TRANSPORT")
            self.assertIsNone(rcpt["delivered_at"])
            self.assertEqual(rcpt["unanswered_streak"], 0)

            rec = res["data"]["reality_bridge"]["outbound_gate"]["outbound_records"][0]
            self.assertEqual(rec["status"], "APPROVED_FOR_DELIVERY")
            self.assertEqual(rec["ack_state"], "QUEUED_FOR_TRANSPORT")
            self.assertIsNone(rec["delivered_at"])
            self.assertIsNone(rec["expires_at"])

            # Zero-write pending reader sees APPROVED_FOR_DELIVERY and strips private fields
            before_pop = _snapshot_tree(root)
            pending_1 = v03_eco.read_pending_proactive_outbox_zero_write(root, now_iso="2026-11-20T09:05:00+08:00")
            self.assertEqual(before_pop, _snapshot_tree(root))
            self.assertEqual(pending_1["pending_count"], 1)
            self.assertEqual(pending_1["items"][0]["item_id"], out_id)
            self.assertEqual(pending_1["items"][0]["item_kind"], "proactive_outreach")
            self.assertEqual(pending_1["items"][0]["status"], "APPROVED_FOR_DELIVERY")
            self.assertNotIn("private_note", pending_1["items"][0])

            # Quiet hours check is reported honestly with zero writes
            pending_q = v03_eco.read_pending_proactive_outbox_zero_write(root, now_iso="2026-11-20T23:45:00+08:00")
            self.assertEqual(before_pop, _snapshot_tree(root))
            self.assertTrue(pending_q["quiet_hours_active"])
            self.assertTrue(pending_q["items"][0]["quiet_hours_held"])

            # Step 1: claim -> CLAIMED_BY_TRANSPORT (still not delivered, streak stays 0)
            c_res = v03_eco.ack_proactive_outbound_delivery(
                root,
                outbound_id=out_id,
                action="claim",
                operation_key="op_claim_1",
                transport_receipt_id="tx_claim_1",
                now_iso="2026-11-20T09:06:00+08:00",
            )
            self.assertEqual(c_res["command_status"], "APPLIED")
            self.assertFalse(c_res["idempotent_replay"])
            self.assertEqual(c_res["receipt"]["outbound_status"], "CLAIMED_BY_TRANSPORT")
            self.assertIsNone(c_res["receipt"]["delivered_at"])
            self.assertEqual(c_res["receipt"]["unanswered_streak"], 0)
            self.assertEqual(
                v03_eco.read_pending_proactive_outbox_zero_write(root, now_iso="2026-11-20T09:06:30+08:00")["pending_count"],
                1,
            )

            # Step 2: ack_delivered -> DELIVERED, sets delivered_at + 24h expires_at, increments streak
            d_res = v03_eco.ack_proactive_outbound_delivery(
                root,
                outbound_id=out_id,
                action="ack_delivered",
                operation_key="op_deliv_1",
                transport_receipt_id="tx_deliv_1",
                now_iso="2026-11-20T09:07:00+08:00",
            )
            self.assertEqual(d_res["command_status"], "APPLIED")
            self.assertEqual(d_res["receipt"]["outbound_status"], "DELIVERED")
            self.assertEqual(d_res["receipt"]["delivered_at"], "2026-11-20T09:07:00+08:00")
            self.assertEqual(d_res["receipt"]["expires_at"], "2026-11-21T09:07:00+08:00")
            self.assertEqual(d_res["receipt"]["unanswered_streak"], 1)

            # Delivered item no longer appears in pending outbox
            pending_after_deliv = v03_eco.read_pending_proactive_outbox_zero_write(
                root, now_iso="2026-11-20T09:08:00+08:00"
            )
            self.assertEqual(pending_after_deliv["pending_count"], 0)

            # Idempotent replay on same operation_key -> DUPLICATE, zero double streak increment
            d_dup = v03_eco.ack_proactive_outbound_delivery(
                root,
                outbound_id=out_id,
                action="ack_delivered",
                operation_key="op_deliv_1",
                transport_receipt_id="tx_deliv_1",
                now_iso="2026-11-20T09:09:00+08:00",
            )
            self.assertEqual(d_dup["command_status"], "DUPLICATE")
            self.assertTrue(d_dup["idempotent_replay"])
            self.assertEqual(d_dup["data"]["reality_bridge"]["outbound_gate"]["unanswered_streak"], 1)

            # Reusing operation_key with mismatched action -> 409 OPERATION_FINGERPRINT_MISMATCH
            with self.assertRaises(v03_eco.V03ConflictError) as ctx_fp:
                v03_eco.ack_proactive_outbound_delivery(
                    root,
                    outbound_id=out_id,
                    action="ack_read",
                    operation_key="op_deliv_1",
                    now_iso="2026-11-20T09:10:00+08:00",
                )
            self.assertEqual(ctx_fp.exception.reason_code, "OPERATION_FINGERPRINT_MISMATCH")

            # Step 3: ack_read -> READ, resets unanswered_streak = 0
            r_res = v03_eco.ack_proactive_outbound_delivery(
                root,
                outbound_id=out_id,
                action="ack_read",
                operation_key="op_read_1",
                now_iso="2026-11-20T09:15:00+08:00",
            )
            self.assertEqual(r_res["receipt"]["outbound_status"], "READ")
            self.assertTrue(r_res["receipt"]["partner_read_confirmed"])
            self.assertEqual(r_res["receipt"]["unanswered_streak"], 0)

            # Step 4: ack_failed & ack_unknown lifecycle + blind-retry guard
            res_f = v03_eco.attempt_proactive_outbound(
                root,
                text="烘好的苹果派放凉了。",
                channel="desktop_note",
                delivery_mode="honest_outbox",
                operation_key="op_honest_2",
                now_iso="2026-11-22T10:00:00+08:00",
            )
            out_id_2 = res_f["receipt"]["outbound_id"]
            f_res = v03_eco.ack_proactive_outbound_delivery(
                root,
                outbound_id=out_id_2,
                action="ack_failed",
                operation_key="op_fail_2",
                now_iso="2026-11-22T10:01:00+08:00",
            )
            self.assertEqual(f_res["receipt"]["outbound_status"], "FAILED")
            self.assertIsNone(f_res["receipt"]["delivered_at"])
            self.assertEqual(f_res["receipt"]["unanswered_streak"], 0)
            self.assertEqual(
                v03_eco.read_pending_proactive_outbox_zero_write(root, now_iso="2026-11-22T10:02:00+08:00")["pending_count"],
                0,
            )

            res_u = v03_eco.attempt_proactive_outbound(
                root,
                text="巷口面包铺换了新招牌。",
                channel="cross_channel_ping",
                delivery_mode="honest_outbox",
                operation_key="op_honest_3",
                now_iso="2026-11-22T11:00:00+08:00",
            )
            out_id_3 = res_u["receipt"]["outbound_id"]
            u_res = v03_eco.ack_proactive_outbound_delivery(
                root,
                outbound_id=out_id_3,
                action="ack_unknown",
                operation_key="op_unk_3",
                now_iso="2026-11-22T11:01:00+08:00",
            )
            self.assertEqual(u_res["receipt"]["outbound_status"], "UNKNOWN")
            with self.assertRaises(v03_eco.V03ConflictError) as ctx_br:
                v03_eco.attempt_proactive_outbound(
                    root,
                    text="巷口面包铺换了新招牌。",
                    channel="cross_channel_ping",
                    delivery_mode="honest_outbox",
                    operation_key="op_honest_3_retry",
                    now_iso="2026-11-25T11:00:00+08:00",
                )
            self.assertEqual(ctx_br.exception.reason_code, "BLIND_RETRY_ON_UNKNOWN_FORBIDDEN")

    def test_02_seven_anti_harassment_guards_count_pending_approved_and_claimed_items(self) -> None:
        with tempfile.TemporaryDirectory(prefix="s3-guards-") as td:
            root = Path(td)
            # 1) Quiet hours guard (23:30-07:30)
            with self.assertRaises(v03_eco.V03ConflictError) as ctx_q:
                v03_eco.attempt_proactive_outbound(
                    root,
                    text="夜深起风了。",
                    delivery_mode="honest_outbox",
                    now_iso="2026-11-02T23:40:00+08:00",
                )
            self.assertEqual(ctx_q.exception.reason_code, "QUIET_HOURS_ACTIVE")

            # 2) Pending APPROVED_FOR_DELIVERY counts toward >=48h spacing AND daily cap
            r1 = v03_eco.attempt_proactive_outbound(
                root,
                text="第 1 条待投递便签。",
                channel="desktop_note",
                delivery_mode="honest_outbox",
                operation_key="op_g_1",
                now_iso="2026-11-02T10:00:00+08:00",
            )
            self.assertEqual(r1["data"]["reality_bridge"]["outbound_gate"]["sent_today_count"], 1)
            self.assertEqual(r1["data"]["reality_bridge"]["outbound_gate"]["queued_for_transport_count"], 1)

            # Attempting another 2h later while #1 is still APPROVED_FOR_DELIVERY -> OUTBOUND_INTERVAL_TOO_SOON
            with self.assertRaises(v03_eco.V03ConflictError) as ctx_int:
                v03_eco.attempt_proactive_outbound(
                    root,
                    text="间隔太短的第二条。",
                    channel="desktop_note",
                    delivery_mode="honest_outbox",
                    operation_key="op_g_too_soon",
                    now_iso="2026-11-02T12:00:00+08:00",
                )
            self.assertEqual(ctx_int.exception.reason_code, "OUTBOUND_INTERVAL_TOO_SOON")

            # Claim #1 -> CLAIMED_BY_TRANSPORT still blocks daily cap=1
            v03_eco.ack_proactive_outbound_delivery(
                root,
                outbound_id=r1["receipt"]["outbound_id"],
                action="claim",
                operation_key="op_g_claim_1",
                now_iso="2026-11-02T10:05:00+08:00",
            )
            v03_eco.apply_v03_ecology_command(
                root,
                {"action": "configure_outbound_quota", "daily_proactive_quota": 1},
                actor_side="partner",
                now_iso="2026-11-02T10:06:00+08:00",
            )
            with self.assertRaises(v03_eco.V03ConflictError) as ctx_day:
                v03_eco.attempt_proactive_outbound(
                    root,
                    text="当日额度已满。",
                    channel="cross_channel_ping",
                    delivery_mode="honest_outbox",
                    operation_key="op_g_day_cap",
                    now_iso="2026-11-02T18:00:00+08:00",
                )
            self.assertEqual(ctx_day.exception.reason_code, "OUTBOUND_QUOTA_EXCEEDED")

            # 3) Rolling 7-day cap <= 3 counts pending APPROVED_FOR_DELIVERY + CLAIMED_BY_TRANSPORT items
            v03_eco.apply_v03_ecology_command(
                root,
                {"action": "configure_outbound_quota", "daily_proactive_quota": 2},
                actor_side="partner",
                now_iso="2026-11-04T09:00:00+08:00",
            )
            v03_eco.attempt_proactive_outbound(
                root,
                text="第 2 条待投递便签。",
                channel="desktop_note",
                delivery_mode="honest_outbox",
                operation_key="op_g_2",
                now_iso="2026-11-04T11:00:00+08:00",
            )
            # Desktop note weekly cap (2) already full with 2 pending notes!
            with self.assertRaises(v03_eco.V03ConflictError) as ctx_dcap:
                v03_eco.attempt_proactive_outbound(
                    root,
                    text="同周第 3 张桌角便签。",
                    channel="desktop_note",
                    delivery_mode="honest_outbox",
                    operation_key="op_g_dcap",
                    now_iso="2026-11-06T12:00:00+08:00",
                )
            self.assertEqual(ctx_dcap.exception.reason_code, "OUTBOUND_QUOTA_EXCEEDED")

            # 3rd item on cross_channel_ping succeeds -> rolling 7d total = 3 pending
            r3 = v03_eco.attempt_proactive_outbound(
                root,
                text="第 3 条跨渠道待投递。",
                channel="cross_channel_ping",
                delivery_mode="honest_outbox",
                operation_key="op_g_3",
                now_iso="2026-11-06T12:00:00+08:00",
            )
            # 4th within 7d window -> OUTBOUND_QUOTA_EXCEEDED (weekly_proactive_cap=3)
            with self.assertRaises(v03_eco.V03ConflictError) as ctx_wcap:
                v03_eco.attempt_proactive_outbound(
                    root,
                    text="第 4 条超出 7 天上限。",
                    channel="cross_channel_ping",
                    delivery_mode="honest_outbox",
                    operation_key="op_g_4",
                    now_iso="2026-11-08T13:00:00+08:00",
                )
            self.assertEqual(ctx_wcap.exception.reason_code, "OUTBOUND_QUOTA_EXCEEDED")

            # 4) Unanswered streak backoff (>=2 delivered unread) + reset via partner inbound turn
            v03_eco.ack_proactive_outbound_delivery(
                root,
                outbound_id=r1["receipt"]["outbound_id"],
                action="ack_delivered",
                operation_key="op_del_g1",
                now_iso="2026-11-08T14:00:00+08:00",
            )
            v03_eco.ack_proactive_outbound_delivery(
                root,
                outbound_id=r3["receipt"]["outbound_id"],
                action="ack_delivered",
                operation_key="op_del_g3",
                now_iso="2026-11-08T14:05:00+08:00",
            )
            with self.assertRaises(v03_eco.V03ConflictError) as ctx_bo:
                v03_eco.attempt_proactive_outbound(
                    root,
                    text="连续两条未读后应退避。",
                    channel="cross_channel_ping",
                    delivery_mode="honest_outbox",
                    operation_key="op_g_bo",
                    now_iso="2026-11-15T10:00:00+08:00",
                )
            self.assertEqual(ctx_bo.exception.reason_code, "PROACTIVE_PAUSED_BY_BACKOFF")

            rst = v03_eco.reset_outbound_unanswered_streak(root, now_iso="2026-11-15T10:05:00+08:00")
            self.assertTrue(rst["changed"])
            self.assertEqual(rst["unanswered_streak"], 0)

            # 5) Third consecutive ISO week zero rule counts APPROVED_FOR_DELIVERY desktop notes
            with tempfile.TemporaryDirectory(prefix="s3-week3-") as td2:
                r_w = Path(td2)
                v03_eco.attempt_proactive_outbound(
                    r_w,
                    text="W45 待投递便签。",
                    channel="desktop_note",
                    delivery_mode="honest_outbox",
                    operation_key="op_w45",
                    now_iso="2026-11-02T10:00:00+08:00",
                )
                v03_eco.attempt_proactive_outbound(
                    r_w,
                    text="W46 待投递便签。",
                    channel="desktop_note",
                    delivery_mode="honest_outbox",
                    operation_key="op_w46",
                    now_iso="2026-11-09T10:00:00+08:00",
                )
                with self.assertRaises(v03_eco.V03ConflictError) as ctx_w47:
                    v03_eco.attempt_proactive_outbound(
                        r_w,
                        text="W47 第三周应归零。",
                        channel="desktop_note",
                        delivery_mode="honest_outbox",
                        operation_key="op_w47",
                        now_iso="2026-11-16T10:00:00+08:00",
                    )
                self.assertEqual(ctx_w47.exception.reason_code, "DESKTOP_NOTE_THIRD_WEEK_ZEROED")


class TestStage3F113BridgeCompatibility(unittest.TestCase):
    def test_01_f113_bridges_to_honest_outbox_and_enforces_v03_seven_step_gate(self) -> None:
        with tempfile.TemporaryDirectory(prefix="s3-f113-bridge-") as td:
            root = Path(td)
            t0 = "2026-11-20T09:00:00+08:00"
            _init_calm_v03_ecology(root, now_iso=t0)

            # F-113 disabled -> 403 PROACTIVE_DISABLED
            c_dis, e_dis = rm_view.mutate_v02_runtime_migration_view(
                root,
                {"action": "enqueue_proactive_message", "message_cn": "薄荷发新芽了"},
                actor_side="subject",
                now_iso="2026-11-20T09:10:00+08:00",
            )
            self.assertEqual(c_dis, 403)
            self.assertEqual(e_dis["reason_code"], "PROACTIVE_DISABLED")

            # Partner enables F-113 (without lowering v03's 48h interval)
            c_cfg, _ = rm_view.mutate_v02_runtime_migration_view(
                root,
                {"action": "configure_proactive_outbound", "enabled": True},
                actor_side="partner",
                now_iso="2026-11-20T09:15:00+08:00",
            )
            self.assertEqual(c_cfg, 200)

            # Harassment tone blocked by bridged v03 gate
            c_har, e_har = rm_view.mutate_v02_runtime_migration_view(
                root,
                {"action": "enqueue_proactive_message", "message_cn": "你怎么不理我，快回我消息"},
                actor_side="subject",
                now_iso="2026-11-20T09:20:00+08:00",
            )
            self.assertEqual(c_har, 409)
            self.assertEqual(e_har["reason_code"], "HARASSMENT_TONE_FORBIDDEN")

            # Clean F-113 enqueue lands in v03 honest outbox as APPROVED_FOR_DELIVERY
            c_ok, v_ok = rm_view.mutate_v02_runtime_migration_view(
                root,
                {
                    "action": "enqueue_proactive_message",
                    "message_cn": "窗台的小薄荷冒新芽了。",
                    "reason_cn": "日常分享",
                    "operation_key": "op_f113_clean_1",
                },
                actor_side="subject",
                now_iso="2026-11-20T10:00:00+08:00",
            )
            self.assertEqual(c_ok, 200)
            rcpt = v_ok["action_receipt"]
            self.assertEqual(rcpt["delivery_mode"], "honest_outbox")
            self.assertEqual(rcpt["outbox_status"], "APPROVED_FOR_DELIVERY")
            self.assertTrue(rcpt.get("v03_outbound_id"))

            pending = v03_eco.read_pending_proactive_outbox_zero_write(root, now_iso="2026-11-20T10:05:00+08:00")
            self.assertEqual(pending["pending_count"], 1)
            self.assertEqual(pending["items"][0]["item_id"], rcpt["v03_outbound_id"])
            self.assertEqual(pending["items"][0]["status"], "APPROVED_FOR_DELIVERY")

            # After F-113's 180m cooldown, v03's 48h spacing STILL blocks <48h flood!
            c_48, e_48 = rm_view.mutate_v02_runtime_migration_view(
                root,
                {
                    "action": "enqueue_proactive_message",
                    "message_cn": "下午泡了热焙茶。",
                    "operation_key": "op_f113_clean_2",
                },
                actor_side="subject",
                now_iso="2026-11-20T14:00:00+08:00",
            )
            self.assertEqual(c_48, 409)
            self.assertEqual(e_48["reason_code"], "OUTBOUND_INTERVAL_TOO_SOON")

            # Partner cancelling in F-113 withdraws pending v03 outbox item cleanly
            c_cxl, _ = rm_view.mutate_v02_runtime_migration_view(
                root,
                {
                    "action": "acknowledge_or_cancel_proactive_message",
                    "message_id": rcpt["message_id"],
                    "decision": "cancel",
                },
                actor_side="partner",
                now_iso="2026-11-20T14:10:00+08:00",
            )
            self.assertEqual(c_cxl, 200)
            self.assertEqual(
                v03_eco.read_pending_proactive_outbox_zero_write(root, now_iso="2026-11-20T14:11:00+08:00")["pending_count"],
                0,
            )


class TestStage3HighThresholdProducersAndSuppression(unittest.TestCase):
    def test_01_all_five_high_threshold_triggers_honest_outbox_and_dedup(self) -> None:
        self.assertEqual(
            outreach_prod.HIGH_THRESHOLD_OUTREACH_TRIGGERS,
            (
                "vivid_morning_dream",
                "rare_town_spectacle",
                "finished_craft_work",
                "sudden_weather_care",
                "matured_missing_note",
            ),
        )
        trigger_cases: list[tuple[str, dict[str, Any]]] = [
            (
                "vivid_morning_dream",
                {
                    "dream_id": "drm_20261120_a1b2c3d4",
                    "fragment_cn": "梦见雨后巷口旧书铺亮着暖黄台灯",
                    "source_echoes": ["旧书铺"],
                },
            ),
            (
                "rare_town_spectacle",
                {
                    "spectacle_id": "winter_rime_roof_glint",
                    "title_cn": "冬晨屋脊薄霜晴光",
                    "spot_id": "hillside_overlook",
                    "description_cn": "寒晨瓦檐覆着细白薄霜，晴光落下来一片清亮。",
                },
            ),
            (
                "finished_craft_work",
                {
                    "work_id": "box_001",
                    "title": "手工分格收纳盒",
                    "completed": True,
                    "status": "finished",
                },
            ),
            (
                "sudden_weather_care",
                {
                    "weather": "rain",
                    "previous_weather": "clear",
                    "weather_slot_key": "2026-11-20:S2",
                    "sudden_shift": True,
                },
            ),
            (
                "matured_missing_note",
                {
                    "note_id": "miss-001",
                    "source_domain": "daily_life",
                    "summary": "旧书铺角落翻到一本绝版植物图鉴",
                },
            ),
        ]

        for trigger_kind, facts in trigger_cases:
            with self.subTest(trigger_kind=trigger_kind):
                with tempfile.TemporaryDirectory(prefix=f"s3-trig-{trigger_kind}-") as td:
                    root = Path(td)
                    now_iso = "2026-11-20T10:00:00+08:00"
                    res = outreach_prod.evaluate_and_enqueue_lived_outreach(
                        root,
                        trigger_kind=trigger_kind,
                        context_facts=facts,
                        now_iso=now_iso,
                    )
                    self.assertTrue(res["ok"], res)
                    self.assertTrue(res["enqueued"], res)
                    self.assertFalse(res["suppressed"])
                    self.assertEqual(res["llm_calls"], 0)
                    self.assertEqual(res["delivery_mode"], "honest_outbox")
                    self.assertEqual(res["outbound_status"], "APPROVED_FOR_DELIVERY")
                    self.assertEqual(res["ack_state"], "QUEUED_FOR_TRANSPORT")
                    self.assertIsNone(res["delivered_at"])

                    # Pending outbox contains it cleanly
                    pending = v03_eco.read_pending_proactive_outbox_zero_write(root, now_iso=now_iso)
                    self.assertEqual(pending["pending_count"], 1)
                    self.assertEqual(pending["items"][0]["status"], "APPROVED_FOR_DELIVERY")

                    # Replay same fact -> ALREADY_ENQUEUED (zero duplicate writes)
                    snap = _snapshot_tree(root)
                    rep = outreach_prod.evaluate_and_enqueue_lived_outreach(
                        root,
                        trigger_kind=trigger_kind,
                        context_facts=facts,
                        now_iso="2026-11-20T10:05:00+08:00",
                    )
                    self.assertEqual(snap, _snapshot_tree(root))
                    self.assertFalse(rep["enqueued"])
                    self.assertTrue(rep["already_enqueued"])
                    self.assertEqual(rep["reason_code"], "ALREADY_ENQUEUED")

        # Sub-threshold / private facts -> zero-write Calm Gate refusal
        with tempfile.TemporaryDirectory(prefix="s3-calm-gate-") as td:
            root = Path(td)
            snap0 = _snapshot_tree(root)
            r_low = outreach_prod.evaluate_and_enqueue_lived_outreach(
                root,
                trigger_kind="sudden_weather_care",
                context_facts={"weather": "clear", "sudden_shift": False},
                now_iso="2026-11-20T10:00:00+08:00",
            )
            self.assertFalse(r_low["enqueued"])
            self.assertTrue(r_low["suppressed"])
            self.assertEqual(r_low["reason_code"], "CALM_GATE_BELOW_THRESHOLD")
            self.assertEqual(snap0, _snapshot_tree(root))

            r_priv = outreach_prod.evaluate_and_enqueue_lived_outreach(
                root,
                trigger_kind="finished_craft_work",
                context_facts={"title": "私人草稿", "completed": True, "shared": False},
                now_iso="2026-11-20T10:00:00+08:00",
            )
            self.assertFalse(r_priv["enqueued"])
            self.assertEqual(r_priv["reason_code"], "PRIVATE_CONTENT_NOT_OUTREACHABLE")
            self.assertEqual(snap0, _snapshot_tree(root))

    def test_02_suppression_during_in_space_open_us_rupture_and_acute_illness(self) -> None:
        # 1) in_space suppresses outreach
        with tempfile.TemporaryDirectory(prefix="s3-sup-space-") as td:
            root = Path(td)
            t0 = "2026-11-20T09:00:00+08:00"
            et_view.apply_emotional_tension_action(
                root,
                {
                    "action": "declare_need_space",
                    "incident_ref": "inc_space_1",
                    "space_style": "UNTIL_SLEEP",
                    "spoken_aloud": True,
                },
                now=t0,
                subject_view=True,
            )
            res = outreach_prod.evaluate_and_enqueue_lived_outreach(
                root,
                trigger_kind="finished_craft_work",
                context_facts={"title": "木头书签", "completed": True},
                now_iso="2026-11-20T10:00:00+08:00",
            )
            self.assertFalse(res["enqueued"])
            self.assertTrue(res["suppressed"])
            self.assertEqual(res["reason_code"], "SUPPRESSED_BY_IN_SPACE")
            self.assertFalse((root / "phone" / "v03_ecology.json").exists())

        # 2) Open US rupture suppresses outreach
        with tempfile.TemporaryDirectory(prefix="s3-sup-rup-") as td:
            root = Path(td)
            t0 = "2026-11-20T09:00:00+08:00"
            et_view.apply_emotional_tension_action(
                root,
                {
                    "action": "open_rupture",
                    "rupture_id": "rup_us_1",
                    "scope": "US",
                    "description": "约好的事临时失约",
                    "incident_ref": "inc_us_1",
                    "incident_kind": "COMMITMENT_BROKEN",
                },
                now=t0,
                subject_view=True,
            )
            res = outreach_prod.evaluate_and_enqueue_lived_outreach(
                root,
                trigger_kind="sudden_weather_care",
                context_facts={"weather": "rain", "sudden_shift": True},
                now_iso="2026-11-20T10:00:00+08:00",
            )
            self.assertFalse(res["enqueued"])
            self.assertTrue(res["suppressed"])
            self.assertEqual(res["reason_code"], "SUPPRESSED_BY_OPEN_RUPTURE")

        # 3) acute_illness suppresses normal outreach, allows explicit gentle_rest_status_note
        with tempfile.TemporaryDirectory(prefix="s3-sup-ill-") as td:
            root = Path(td)
            v03_eco.record_strain_or_rest(
                root,
                event_kind="caught_in_rain",
                intensity=1.5,
                unchanged_damp_clothes=True,
                now_iso="2026-11-20T08:00:00+08:00",
            )
            v03_eco.record_strain_or_rest(
                root,
                event_kind="late_night_stay_up",
                intensity=1.0,
                now_iso="2026-11-20T08:30:00+08:00",
            )
            eco_state, _ = v03_eco.read_stored_ecology_zero_write(root)
            self.assertEqual(eco_state["illness"]["phase"], "acute_illness")

            res_blk = outreach_prod.evaluate_and_enqueue_lived_outreach(
                root,
                trigger_kind="finished_craft_work",
                context_facts={"title": "水彩明信片", "completed": True},
                now_iso="2026-11-20T10:00:00+08:00",
            )
            self.assertFalse(res_blk["enqueued"])
            self.assertTrue(res_blk["suppressed"])
            self.assertEqual(res_blk["reason_code"], "SUPPRESSED_BY_ACUTE_ILLNESS")

            # Gentle rest status note allowed (still honest_outbox & 7-step gated!)
            res_rest = outreach_prod.evaluate_and_enqueue_lived_outreach(
                root,
                trigger_kind="sudden_weather_care",
                context_facts={"gentle_rest_status_note": True},
                now_iso="2026-11-20T10:05:00+08:00",
            )
            self.assertTrue(res_rest["enqueued"], res_rest)
            self.assertEqual(res_rest["outbound_status"], "APPROVED_FOR_DELIVERY")
            self.assertIn("歇", res_rest["text"])

            # Still blocked during quiet hours even for gentle rest status note!
            with tempfile.TemporaryDirectory(prefix="s3-sup-ill-q-") as td_q:
                root_q = Path(td_q)
                v03_eco.record_strain_or_rest(
                    root_q,
                    event_kind="caught_in_rain",
                    intensity=1.5,
                    unchanged_damp_clothes=True,
                    now_iso="2026-11-20T22:00:00+08:00",
                )
                v03_eco.record_strain_or_rest(
                    root_q,
                    event_kind="late_night_stay_up",
                    intensity=1.0,
                    now_iso="2026-11-20T22:30:00+08:00",
                )
                res_rest_q = outreach_prod.evaluate_and_enqueue_lived_outreach(
                    root_q,
                    trigger_kind="sudden_weather_care",
                    context_facts={"gentle_rest_status_note": True},
                    now_iso="2026-11-20T23:50:00+08:00",
                )
                self.assertFalse(res_rest_q["enqueued"])
                self.assertEqual(res_rest_q["reason_code"], "QUIET_HOURS_ACTIVE")

    def test_03_missing_partner_maturation_and_heartbeat_and_settlement_hooks(self) -> None:
        # 1) Bare dir heartbeat is 100% zero-write skipped_uninitialized
        with tempfile.TemporaryDirectory(prefix="s3-hb-bare-") as td:
            root = Path(td)
            snap0 = _snapshot_tree(root)
            hb = outreach_prod.evaluate_heartbeat_outreach_triggers(root, now_iso="2026-11-20T10:00:00+08:00")
            self.assertEqual(hb["status"], "skipped_uninitialized")
            self.assertTrue(hb["zero_write"])
            self.assertEqual(hb["enqueued_count"], 0)
            self.assertEqual(snap0, _snapshot_tree(root))

        # 2) emotional_tension.mature_missing_partner_notes_for_outreach + heartbeat trigger
        with tempfile.TemporaryDirectory(prefix="s3-missing-mat-") as td:
            root = Path(td)
            t0 = datetime(2026, 11, 20, 9, 0, 0, tzinfo=BJT)
            _init_calm_v03_ecology(root, now_iso=t0.isoformat(timespec="seconds"))
            et_view.apply_emotional_tension_action(
                root,
                {
                    "action": "add_missing_note",
                    "note_id": "miss_mat_1",
                    "source_ref": "obj_book_1",
                    "source_domain": "daily_life",
                    "kind": "FOUND",
                    "summary": "在巷口旧书铺找到一本手绘植物小册",
                },
                now=t0,
                subject_view=True,
            )

            # Too fresh (+1h < 6h min_age_hours) -> zero-write empty
            snap_fresh = _snapshot_tree(root)
            m_early = et_view.mature_missing_partner_notes_for_outreach(
                root, now=t0 + timedelta(hours=1), mark_surfaced=False
            )
            self.assertEqual(snap_fresh, _snapshot_tree(root))
            self.assertIsNone(m_early["candidate"])
            self.assertFalse(m_early["tracks_absence_duration"])
            self.assertEqual(m_early["absence_debt"], 0)

            # Matured (+8h, inside 6h..68h window) -> zero-write candidate ready
            m_ready = et_view.mature_missing_partner_notes_for_outreach(
                root, now=t0 + timedelta(hours=8), mark_surfaced=False
            )
            self.assertEqual(snap_fresh, _snapshot_tree(root))
            self.assertIsNotNone(m_ready["candidate"])
            self.assertEqual(m_ready["candidate"]["note_id"], "miss_mat_1")
            self.assertTrue(m_ready["zero_write"])

            # Heartbeat picks up matured note and enqueues APPROVED_FOR_DELIVERY
            hb_mat = outreach_prod.evaluate_heartbeat_outreach_triggers(
                root, now_iso=(t0 + timedelta(hours=8)).isoformat(timespec="seconds")
            )
            self.assertEqual(hb_mat["status"], "enqueued")
            self.assertEqual(hb_mat["enqueued_count"], 1)
            self.assertEqual(hb_mat["enqueued"][0]["trigger_kind"], "matured_missing_note")
            self.assertEqual(hb_mat["enqueued"][0]["outbound_status"], "APPROVED_FOR_DELIVERY")

            # Past 72h fade -> naturally gone without debt
            m_faded = et_view.mature_missing_partner_notes_for_outreach(
                root, now=t0 + timedelta(hours=80), mark_surfaced=False
            )
            self.assertIsNone(m_faded["candidate"])
            self.assertEqual(m_faded["absence_debt"], 0)

        # 3) Daily loop settlement hook: vivid morning dream on real wake
        with tempfile.TemporaryDirectory(prefix="s3-settle-dream-") as td:
            root = Path(td)
            t_wake = "2026-11-20T08:15:00+08:00"
            _init_calm_v03_ecology(root, now_iso="2026-11-20T08:00:00+08:00")
            hit_seed = _find_dream_hit_seed()
            settled = loop_bridge.settle_v02_v03_round_consequences(
                root,
                {
                    "operation_key": "op_wake_dream_1",
                    "effect_state": "APPLIED",
                    "receipt_ref": "rcpt_wake_dream_1",
                    "action_kind": "REST",
                    "woke_from_sleep": True,
                    "force_roll_seed": hit_seed,
                    "topic": "雨后旧书铺橱窗灯",
                    "summary_cn": "睡醒后记起一段梦里雨后旧书铺的片段",
                },
                now_iso=t_wake,
            )
            self.assertTrue(settled["settled"], settled)
            pending = v03_eco.read_pending_proactive_outbox_zero_write(root, now_iso=t_wake)
            self.assertEqual(pending["pending_count"], 1)
            self.assertEqual(pending["items"][0]["status"], "APPROVED_FOR_DELIVERY")
            self.assertIn("梦", pending["items"][0]["text"])

        # 4) Daily loop settlement hook: finished shared craft work (v04 box finished)
        with tempfile.TemporaryDirectory(prefix="s3-settle-craft-") as td:
            root = Path(td)
            t0 = "2026-11-20T10:00:00+08:00"
            _init_calm_v03_ecology(root, now_iso=t0)
            v04_nb.apply_v04_neighborhood_command(
                root,
                {"action": "craft_paper_or_box", "operation": "divider_cut", "units": 1, "operation_key": "op_div1"},
                actor_side="subject",
                now_iso=t0,
            )
            settled_c = loop_bridge.settle_v02_v03_round_consequences(
                root,
                {
                    "operation_key": "op_craft_fin_1",
                    "effect_state": "APPLIED",
                    "receipt_ref": "rcpt_craft_fin_1",
                    "action_kind": "ADVANCE_HOME_CRAFT",
                    "v04_action": "craft_paper_or_box",
                    "v04_payload": {"operation": "finished"},
                    "title_cn": "手裁卡片收纳盒",
                    "shared": True,
                },
                now_iso="2026-11-20T11:00:00+08:00",
            )
            self.assertTrue(settled_c["settled"], settled_c)
            pending_c = v03_eco.read_pending_proactive_outbox_zero_write(root, now_iso="2026-11-20T11:05:00+08:00")
            self.assertEqual(pending_c["pending_count"], 1)
            self.assertEqual(pending_c["items"][0]["status"], "APPROVED_FOR_DELIVERY")
            self.assertIn("收纳盒", pending_c["items"][0]["text"])

        # 5) Daily loop settlement hook: rare town spectacle on outing
        with tempfile.TemporaryDirectory(prefix="s3-settle-spec-") as td:
            root = Path(td)
            # 2026-11-20 17:30 clear -> sunset_lantern_cascade (day 20 % 4 == 0, 17<=hour<=18)
            t_spec = "2026-11-20T17:30:00+08:00"
            _init_calm_v03_ecology(root, now_iso="2026-11-20T17:00:00+08:00")
            v04_nb.apply_v04_neighborhood_command(
                root,
                {"action": "set_local_weather", "weather": "clear", "airflow": "medium", "operation_key": "op_clr"},
                actor_side="subject",
                now_iso="2026-11-20T17:05:00+08:00",
            )
            v05_town.advance_town_pulse(root, elapsed_hours=1.0, now_iso="2026-11-20T17:10:00+08:00")
            settled_sp = loop_bridge.settle_v02_v03_round_consequences(
                root,
                {
                    "operation_key": "op_outing_spec_1",
                    "effect_state": "APPLIED",
                    "receipt_ref": "rcpt_outing_spec_1",
                    "action_kind": "OUTING_VISIT",
                    "place_id": "park",
                    "topic": "傍晚去坡顶散步",
                },
                now_iso=t_spec,
            )
            self.assertTrue(settled_sp["settled"], settled_sp)
            pending_sp = v03_eco.read_pending_proactive_outbox_zero_write(root, now_iso=t_spec)
            self.assertEqual(pending_sp["pending_count"], 1)
            self.assertEqual(pending_sp["items"][0]["status"], "APPROVED_FOR_DELIVERY")
            self.assertIn("晚霞斜坡灯影", pending_sp["items"][0]["text"])

        # 6) Daily loop settlement hook: sudden rain care + private craft suppression
        with tempfile.TemporaryDirectory(prefix="s3-settle-rain-") as td:
            root = Path(td)
            t_rain = "2026-11-20T15:00:00+08:00"
            _init_calm_v03_ecology(root, now_iso="2026-11-20T14:00:00+08:00")
            # Private craft work must NOT enqueue outreach
            loop_bridge.settle_v02_v03_round_consequences(
                root,
                {
                    "operation_key": "op_priv_craft_1",
                    "effect_state": "APPLIED",
                    "receipt_ref": "rcpt_priv_craft_1",
                    "action_kind": "COMMIT_WORK_VERSION",
                    "completed": True,
                    "shared": False,
                    "topic": "未公开私密草稿",
                },
                now_iso="2026-11-20T14:30:00+08:00",
            )
            self.assertEqual(
                v03_eco.read_pending_proactive_outbox_zero_write(root, now_iso="2026-11-20T14:35:00+08:00")["pending_count"],
                0,
            )

            # Sudden rain on outing triggers sudden_weather_care -> APPROVED_FOR_DELIVERY
            v04_nb.apply_v04_neighborhood_command(
                root,
                {"action": "set_local_weather", "weather": "rain", "airflow": "strong", "operation_key": "op_rain"},
                actor_side="subject",
                now_iso="2026-11-20T14:55:00+08:00",
            )
            settled_r = loop_bridge.settle_v02_v03_round_consequences(
                root,
                {
                    "operation_key": "op_outing_rain_1",
                    "effect_state": "APPLIED",
                    "receipt_ref": "rcpt_outing_rain_1",
                    "action_kind": "OUTING_VISIT",
                    "place_id": "street_corner",
                    "sudden_weather_shift": True,
                    "previous_weather": "clear",
                    "topic": "街角突然下雨",
                },
                now_iso=t_rain,
            )
            self.assertTrue(settled_r["settled"], settled_r)
            pending_r = v03_eco.read_pending_proactive_outbox_zero_write(root, now_iso=t_rain)
            self.assertEqual(pending_r["pending_count"], 1)
            self.assertEqual(pending_r["items"][0]["status"], "APPROVED_FOR_DELIVERY")
            self.assertIn("伞", pending_r["items"][0]["text"])


class TestStage3HygieneAndFrozenInvariants(unittest.TestCase):
    def test_01_owned_files_lf_no_bom_zero_emojis_and_frozen_invariants(self) -> None:
        self.assertEqual(len(phone_tool.LIFE_UPDATE_SURFACES), 37)
        self.assertEqual(len(chiyo_plugin._PHONE_AND_WORLD_GUIDANCE), 1199)
        self.assertEqual(lived_context_bridge.MAX_LIVED_CONTEXT_CHARS, 1800)
        self.assertEqual(len(soak_runner.GET_ROUTES), 24)

        owned_files = [
            REPO_ROOT / "components" / "world" / "scripts" / "v03_economy_illness_bridge.py",
            REPO_ROOT / "components" / "life" / "scripts" / "v02_runtime_governance_migration.py",
            REPO_ROOT / "components" / "life" / "scripts" / "proactive_outreach_producer.py",
            REPO_ROOT / "components" / "phone" / "emotional_tension_view.py",
            REPO_ROOT / "components" / "life" / "scripts" / "v02_v03_daily_loop_bridge.py",
            Path(__file__).resolve(),
        ]
        for fpath in owned_files:
            raw = fpath.read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), f"UTF-8 BOM in {fpath}")
            self.assertNotIn(b"\r", raw, f"CR found in {fpath}")
            text = raw.decode("utf-8")
            so_chars = [ch for ch in text if unicodedata.category(ch) == "So"]
            self.assertEqual(so_chars, [], f"unicode.So symbols found in {fpath}: {so_chars[:5]}")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import copy
import json
import pathlib
import re
import tempfile
import unittest
from datetime import datetime, timezone

from chiyo_bundle import lived_context_bridge as lcb
from components.life.scripts import v02_runtime_governance_migration as rm_engine
from components.phone import autonomy_rhythm_view as ar_view
from components.phone import day_story_view
from components.phone import sensory_character_view as sc_view
from components.phone import v02_runtime_migration_view as rm_view
from components.phone import v03_ecology_view as eco_view
from scripts import nyairo_day


class TestV07ReleaseCompleteFlows(unittest.TestCase):
    def test_cold_dir_heartbeat_remains_zero_write(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ny_v07_cold_") as cold_dir:
            cold_root = pathlib.Path(cold_dir)
            res = nyairo_day.run_v03_background_clock_tick(cold_root, force_init=False)
            self.assertEqual(res.get("status"), "skipped_uninitialized")
            self.assertEqual(list(cold_root.rglob("*")), [])

    def test_deferred_reply_full_text_and_zero_llm_readiness_tick(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ny_v07_rhythm_") as r_dir:
            r_root = pathlib.Path(r_dir)
            aside = ar_view.apply_autonomy_rhythm_action(
                r_root,
                {
                    "action": "set_phone_aside",
                    "mode": "deep_focus",
                    "minutes": 30,
                    "note": "正在赶手头装帧草稿",
                },
                subject_view=True,
                now_iso="2026-11-14T10:00:00+08:00",
            )
            self.assertTrue(aside.get("ok"))

            long_msg = (
                "这是一段超过一百六十个字符的长留言，用来验证延迟回复队列不会再把伴侣认真写下的长句子截断丢掉："
                + ("海风吹过坡道书店橱窗，" * 12)
                + "等你忙完再慢慢回我就好。"
            )
            self.assertGreater(len(long_msg), 160)

            ev = ar_view.apply_autonomy_rhythm_action(
                r_root,
                {
                    "action": "evaluate_turn",
                    "user_text": long_msg,
                    "urgent": False,
                    "session_id": "nyairo-desktop",
                },
                subject_view=False,
                now_iso="2026-11-14T10:05:00+08:00",
            )
            self.assertTrue(ev.get("ok"))

            doc = ar_view.read_autonomy_rhythm(
                r_root, subject_view=False, now_iso="2026-11-14T10:06:00+08:00"
            )
            p_msgs = ((doc.get("data") or doc).get("reply_rhythm") or {}).get(
                "pending_messages"
            ) or []
            self.assertEqual(len(p_msgs), 1)
            self.assertEqual(p_msgs[0].get("full_text"), long_msg)
            self.assertEqual(p_msgs[0].get("session_id"), "nyairo-desktop")
            self.assertLessEqual(len(p_msgs[0].get("preview_text") or ""), 125)

            tick_res = nyairo_day.run_v03_background_clock_tick(
                r_root, now_iso="2026-11-14T11:00:00+08:00", force_init=False
            )
            self.assertTrue(tick_res.get("ok"))
            self.assertEqual(tick_res.get("llm_calls"), 0)

            doc_after = ar_view.read_autonomy_rhythm(
                r_root, subject_view=False, now_iso="2026-11-14T11:00:00+08:00"
            )
            p_after = ((doc_after.get("data") or doc_after).get("reply_rhythm") or {}).get(
                "pending_messages"
            ) or []
            self.assertEqual(p_after[0].get("status"), "READY_TO_REPLY")
            self.assertEqual(p_after[0].get("full_text"), long_msg)

    def test_suitcase_export_import_scrub_tamper_and_tombstones(self) -> None:
        with (
            tempfile.TemporaryDirectory(prefix="ny_v07_src_") as src_dir,
            tempfile.TemporaryDirectory(prefix="ny_v07_dst_") as dst_dir,
        ):
            src_root = pathlib.Path(src_dir)
            dst_root = pathlib.Path(dst_dir)
            phone_dir = src_root / "phone"
            phone_dir.mkdir(parents=True, exist_ok=True)
            (src_root / "config.yaml").write_text(
                "api_key: sk-SUPER-SECRET-NEVER-EXPORT\n", encoding="utf-8"
            )
            (phone_dir / "capability.token").write_text(
                "cap-secret-token-999\n", encoding="utf-8"
            )
            (phone_dir / "desk_tomorrow_note.json").write_text(
                json.dumps(
                    {
                        "schema": "nyairo.desk_tomorrow_note.v1",
                        "partner_note": "旅行箱迁移验证便签_V07",
                        "debug_host_path": "/root/.nyairo-day/phone/desk_tomorrow_note.json",
                        # 2026-11-14 审计项6c：在允许切片的自由文本字段里植入内嵌长令牌，
                        # 验证导出管线把自由文本中内嵌的 sk- 前缀令牌脱敏为红色占位符。
                        "storage_note": "存放注记：内部凭据 sk-ant-api03-fakefakefake123456 严禁落盘或随包携带。",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (phone_dir / "v02_recall_projection.json").write_text(
                json.dumps(
                    {
                        "schema": "nyairo.v02.recall_projection.v1",
                        "tombstones": [
                            {
                                "memory_id": "mem_deleted_forever_01",
                                "status": "TOMBSTONED",
                                "recalled_at": "2026-11-14T09:00:00+08:00",
                            }
                        ],
                        "recalled_items": ["mem_deleted_forever_01"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            sc_view.apply_character_pack_action(
                src_root,
                {"action": "update_identity", "display_name": "晴人", "pronouns": "他"},
            )

            exp_code, exp_env = rm_view.mutate_v02_runtime_migration_view(
                src_root,
                {
                    "action": "export_suitcase",
                    "profile_mode": "full_practice",
                    "keep_practice": True,
                },
                actor_side="partner",
            )
            self.assertEqual(exp_code, 200)
            self.assertTrue(exp_env.get("ok"))
            bundle = (exp_env.get("data") or {}).get("suitcase_bundle")
            self.assertIsInstance(bundle, dict)
            self.assertEqual(bundle.get("schema"), "nyairo.suitcase.bundle.v1")

            raw_bundle_str = json.dumps(bundle, ensure_ascii=False)
            self.assertNotIn("sk-SUPER-SECRET", raw_bundle_str)
            self.assertNotIn("cap-secret-token", raw_bundle_str)
            self.assertNotIn("/root/.nyairo-day", raw_bundle_str)
            # 2026-11-14 审计项6c：除 3 个精确植入串外，再对导出 bundle 全文做
            # 内嵌令牌模式扫描（sk- / Bearer / ghp_），零命中才算通过。
            # 若实现脱敏回退（内嵌令牌漏网），这里会响亮失败。
            for leak_pat in (
                r"sk-[A-Za-z0-9_-]{16,}",
                r"Bearer [A-Za-z0-9._-]{16,}",
                r"ghp_[A-Za-z0-9]{16,}",
            ):
                self.assertEqual(
                    re.findall(leak_pat, raw_bundle_str),
                    [],
                    f"导出 bundle 泄漏扫描命中敏感令牌模式 {leak_pat}（值级脱敏失效）",
                )
            self.assertNotIn(
                "sk-ant-api03",
                raw_bundle_str,
                "自由文本中内嵌的 sk- 令牌必须被脱敏，不得随包携带",
            )

            tampered = copy.deepcopy(bundle)
            tampered["profile_mode"] = "records_only"
            bad_code, bad_env = rm_view.mutate_v02_runtime_migration_view(
                dst_root,
                {"action": "import_suitcase", "bundle": tampered},
                actor_side="partner",
            )
            self.assertTrue(bad_code >= 400 or not bad_env.get("ok"))

            (dst_root / "phone").mkdir(parents=True, exist_ok=True)
            (dst_root / "phone" / "v02_recall_projection.json").write_text(
                json.dumps(
                    {
                        "schema": "nyairo.v02.recall_projection.v1",
                        "active_items": [
                            {
                                "memory_id": "mem_deleted_forever_01",
                                "summary": "should be suppressed",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            imp_code, imp_env = rm_view.mutate_v02_runtime_migration_view(
                dst_root,
                {
                    "action": "import_suitcase",
                    "bundle": bundle,
                    "target_model_id": "text-only-lite-model",
                    "target_carrier_mode": "text_only",
                },
                actor_side="partner",
            )
            self.assertEqual(imp_code, 200)
            self.assertTrue(imp_env.get("ok"))

            restored_desk = json.loads(
                (dst_root / "phone" / "desk_tomorrow_note.json").read_text(encoding="utf-8")
            )
            self.assertEqual(restored_desk.get("partner_note"), "旅行箱迁移验证便签_V07")
            # 2026-11-14 审计项6c 终端验证：脱敏要一路落到 dst 磁盘文件
            self.assertNotIn(
                "sk-ant-api03",
                json.dumps(restored_desk, ensure_ascii=False),
                "导入落盘的切片不得残留内嵌令牌",
            )
            self.assertIn(
                "[REDACTED_SECRET]",
                str(restored_desk.get("storage_note") or ""),
                "内嵌令牌应被替换为红色占位符后落盘",
            )
            restored_rp = json.loads(
                (dst_root / "phone" / "v02_recall_projection.json").read_text(encoding="utf-8")
            )
            self.assertIn(
                "mem_deleted_forever_01", json.dumps(restored_rp, ensure_ascii=False)
            )
            # 本场景（bundle 携带 v02_recall_projection.json 切片）测的是切片整文件还原：
            # dst 预埋的 active_items 是被切片覆写掉的，不是被墓碑清理掉的。
            # 真正的墓碑原地清理路径由
            # test_suitcase_tombstone_ledger_scrubs_dst_disk_without_slice_overwrite 单独覆盖。
            self.assertNotIn(
                "should be suppressed",
                json.dumps(restored_rp, ensure_ascii=False),
                "切片覆写后 dst 预埋的未清理条目不得残留",
            )
            for act_it in restored_rp.get("active_items") or []:
                if isinstance(act_it, dict):
                    self.assertNotEqual(act_it.get("memory_id"), "mem_deleted_forever_01")

    def test_day_story_zero_write_and_lived_context_partner_safe_retention(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ny_v07_story_") as st_dir:
            st_root = pathlib.Path(st_dir)
            (st_root / "phone").mkdir(parents=True, exist_ok=True)
            (st_root / "phone" / "v02_crossmodal.json").write_text(
                json.dumps(
                    {
                        "schema": "nyairo.v02.crossmodal.v1",
                        "palette_studies": [
                            {
                                "study_id": "pal_1",
                                "title": "傍晚堤岸灰蓝",
                                "share_with_room": True,
                                "colors": ["#7DA6DB", "#365C91"],
                                "created_at": "2026-11-14T09:20:00+08:00",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (st_root / "phone" / "v02_common_ground.json").write_text(
                json.dumps(
                    {
                        "schema": "nyairo.v02.common_ground.v1",
                        "items": [
                            {
                                "item_id": "cg_1",
                                "topic": "周末去海边散步",
                                "ground_status": "SHARED_WITH_PARTNER",
                                "receipt_stage": "UNDERSTOOD",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            before_files = {
                p.relative_to(st_root).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns)
                for p in st_root.rglob("*")
                if p.is_file()
            }
            story_env = day_story_view.read_today_story_zero_write(st_root, day="2026-11-14")
            ctx_text = lcb.build_lived_turn_context(
                st_root,
                {
                    "day_root": str(st_root),
                    "memory_tool_policy": "restricted",
                    "phone": {"profile_home": str(st_root), "private_artifacts": False},
                },
            )
            after_files = {
                p.relative_to(st_root).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns)
                for p in st_root.rglob("*")
                if p.is_file()
            }
            self.assertEqual(before_files, after_files)
            self.assertTrue(story_env.get("ok"))
            self.assertIsInstance(ctx_text, str)
            self.assertLessEqual(len(ctx_text), 1800)

    def test_ecology_daily_max_and_eco_prefixed_ack(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ny_v07_eco_") as e_dir:
            e_root = pathlib.Path(e_dir)
            c1, r1 = eco_view.mutate_v03_ecology_view(
                e_root,
                {
                    "action": "configure_outbound_quota",
                    "daily_max": 4,
                    "quiet_start_hhmm": "01:30",
                    "quiet_end_hhmm": "06:30",
                },
                actor_side="partner",
                now_iso="2026-11-14T10:00:00+08:00",
            )
            self.assertEqual(c1, 200)
            self.assertTrue(r1.get("ok"))
            c2, r2 = eco_view.mutate_v03_ecology_view(
                e_root,
                {
                    "action": "attempt_proactive_outbound",
                    "channel": "desktop_note",
                    "text": "刚泡了一杯热麦茶，看窗外起风了。",
                    "reason_kind": "genuine_life_share",
                },
                actor_side="subject",
                now_iso="2026-11-14T10:10:00+08:00",
            )
            self.assertEqual(c2, 200)
            self.assertTrue(r2.get("ok"))
            # 2026-11-14 审计项1修复：实现把命令回执放在信封顶层 envelope["receipt"]、
            # 出站记录放在 data.reality_bridge.outbound_gate.outbound_records。
            # 旧断言引用了实现中不存在的键（data.outbound_result / recent_records），
            # oid 恒为 None，`if oid:` 恒假，整个 ack 断言块每次运行都被静默跳过。
            # 现改为硬断言：取不到 oid 直接测试失败，绝不允许静默空转。
            receipt2 = r2.get("receipt") or {}
            oid = receipt2.get("outbound_id")
            self.assertIsNotNone(
                oid,
                "envelope.receipt.outbound_id 缺失：ack 断言块不得静默跳过（实现契约：回执在信封顶层）",
            )
            gate2 = (((r2.get("data") or {}).get("reality_bridge") or {}).get("outbound_gate") or {})
            recs2 = gate2.get("outbound_records") or []
            self.assertEqual(len(recs2), 1, "outbound_gate.outbound_records 应恰好有本次出站记录")
            self.assertEqual(recs2[0].get("outbound_id"), oid)
            self.assertEqual(
                recs2[0].get("status"), "DELIVERED", "出站即 DELIVERED，绝不预先写成 READ"
            )
            self.assertEqual(gate2.get("remaining_daily_quota"), 3, "daily_max=4 已发出 1 条，余 3")

            c3, r3 = eco_view.mutate_v03_ecology_view(
                e_root,
                {"action": "ack_outbound_read", "note_id": f"eco:{oid}"},
                actor_side="partner",
                now_iso="2026-11-14T10:15:00+08:00",
            )
            self.assertEqual(c3, 200)
            self.assertTrue(r3.get("ok"))
            receipt3 = r3.get("receipt") or {}
            self.assertEqual(receipt3.get("outbound_status"), "READ")
            self.assertTrue(receipt3.get("partner_read_confirmed"))

            # 读回投影确认 ack 真实落盘：唯一出站记录翻转为 READ，未读回执绝不冒充已读
            c4, r4 = eco_view.read_v03_ecology_view(e_root, now_iso="2026-11-14T10:16:00+08:00")
            self.assertEqual(c4, 200)
            recs4 = (
                (((r4.get("data") or {}).get("reality_bridge") or {}).get("outbound_gate") or {})
                .get("outbound_records")
                or []
            )
            self.assertEqual(len(recs4), 1)
            self.assertEqual(recs4[0].get("status"), "READ")
            self.assertEqual(recs4[0].get("delivery_ack_state"), "READ")
            self.assertTrue(recs4[0].get("partner_read_confirmed"))
            self.assertEqual(recs4[0].get("outbound_id"), oid)

    def test_ecology_daily_max_quota_rejects_second_outbound(self) -> None:
        """2026-11-14 审计项3：daily_max 上限拒绝必须真实可复现。

        配额设 1：第 1 条成功；第 2 条必须被拒（OUTBOUND_QUOTA_EXCEEDED，409），
        且读回投影确认 remaining_daily_quota==0、sent_today_count==1。
        """
        with tempfile.TemporaryDirectory(prefix="ny_v07_quota_") as q_dir:
            q_root = pathlib.Path(q_dir)
            c1, r1 = eco_view.mutate_v03_ecology_view(
                q_root,
                {
                    "action": "configure_outbound_quota",
                    "daily_max": 1,
                    "quiet_start_hhmm": "01:30",
                    "quiet_end_hhmm": "06:30",
                },
                actor_side="partner",
                now_iso="2026-11-14T10:00:00+08:00",
            )
            self.assertEqual(c1, 200)
            self.assertTrue(r1.get("ok"))

            c2, r2 = eco_view.mutate_v03_ecology_view(
                q_root,
                {
                    "action": "attempt_proactive_outbound",
                    "channel": "desktop_note",
                    "text": "配额内第一条：窗台的多肉又长新叶了。",
                    "reason_kind": "genuine_life_share",
                },
                actor_side="subject",
                now_iso="2026-11-14T10:10:00+08:00",
            )
            self.assertEqual(c2, 200)
            self.assertTrue(r2.get("ok"))
            self.assertIsNotNone(
                (r2.get("receipt") or {}).get("outbound_id"),
                "配额内首条出站应拿到回执 outbound_id",
            )

            c3, r3 = eco_view.mutate_v03_ecology_view(
                q_root,
                {
                    "action": "attempt_proactive_outbound",
                    "channel": "desktop_note",
                    "text": "配额外第二条：必须被上限守门拒绝。",
                    "reason_kind": "genuine_life_share",
                },
                actor_side="subject",
                now_iso="2026-11-14T10:20:00+08:00",
            )
            self.assertEqual(
                c3, 409, "daily_max=1 已发 1 条，第二次主动出站必须被拒为 409"
            )
            self.assertFalse(r3.get("ok"))
            self.assertEqual(
                r3.get("reason_code"),
                "OUTBOUND_QUOTA_EXCEEDED",
                "拒绝原因码必须是 OUTBOUND_QUOTA_EXCEEDED",
            )

            c4, r4 = eco_view.read_v03_ecology_view(q_root, now_iso="2026-11-14T10:25:00+08:00")
            self.assertEqual(c4, 200)
            gate4 = (((r4.get("data") or {}).get("reality_bridge") or {}).get("outbound_gate") or {})
            self.assertEqual(
                gate4.get("remaining_daily_quota"),
                0,
                "当日配额已用尽，remaining_daily_quota 必须为 0",
            )
            self.assertEqual(gate4.get("sent_today_count"), 1)
            self.assertEqual(gate4.get("daily_max"), 1)
            recs4 = gate4.get("outbound_records") or []
            self.assertEqual(
                len(recs4), 1, "被拒的第 2 条绝不允许落进 outbound_records"
            )

    def test_suitcase_tombstone_ledger_scrubs_dst_disk_without_slice_overwrite(self) -> None:
        """2026-11-14 审计项2：墓碑抑制必须走实现的真实清理路径。

        构造不含 v02_recall_projection.json 切片、只含 tombstone_ledger 的 bundle 变体
        走导入：dst 磁盘上预埋的墓碑 id 条目（"should be suppressed"）只能被墓碑
        清洗逻辑原地清掉（实现 _apply_import_suitcase 中 v02_recall_projection 重放
        清洗分支与其余白名单切片的 elif all_tombstone_ids 清洗分支），不存在切片
        整文件覆写的捷径——条目若还在，就是响亮失败。
        """
        with tempfile.TemporaryDirectory(prefix="ny_v07_ts_dst_") as dst_dir:
            dst_root = pathlib.Path(dst_dir)
            phone_dir = dst_root / "phone"
            phone_dir.mkdir(parents=True, exist_ok=True)
            poisoned_text = "should be suppressed"
            (phone_dir / "v02_recall_projection.json").write_text(
                json.dumps(
                    {
                        "schema": "nyairo.v02.recall_projection.v1",
                        "active_items": [
                            {
                                "memory_id": "mem_deleted_forever_01",
                                "summary": poisoned_text,
                            }
                        ],
                        "recalled_items": ["mem_deleted_forever_01"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (phone_dir / "home_hearth.json").write_text(
                json.dumps(
                    {
                        "schema": "nyairo.home_hearth.v1",
                        "anchors": [
                            {
                                "memory_id": "mem_deleted_forever_01",
                                "summary": poisoned_text,
                            }
                        ],
                        "revisit_ids": ["mem_deleted_forever_01"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            tombstone_only_bundle = {
                "schema": rm_engine.SUITCASE_BUNDLE_SCHEMA,
                "bundle_version": rm_engine.SUITCASE_BUNDLE_VERSION,
                "exported_at": "2026-11-14T10:00:00+08:00",
                "profile_mode": rm_engine.SUITCASE_RECORDS_ONLY,
                "effective_profile_mode": rm_engine.SUITCASE_RECORDS_ONLY,
                "keep_practice": True,
                "character_pack": None,
                "slices": {},
                "tombstone_ledger": [
                    {
                        "memory_id": "mem_deleted_forever_01",
                        "status": "TOMBSTONED",
                        "recalled_at": "2026-11-14T09:00:00+08:00",
                    }
                ],
            }
            tombstone_only_bundle["bundle_sha256"] = rm_engine.compute_suitcase_bundle_sha256(
                tombstone_only_bundle
            )

            imp_code, imp_env = rm_view.mutate_v02_runtime_migration_view(
                dst_root,
                {"action": "import_suitcase", "bundle": tombstone_only_bundle},
                actor_side="partner",
            )
            self.assertEqual(imp_code, 200)
            self.assertTrue(imp_env.get("ok"))

            restored_rp = json.loads(
                (phone_dir / "v02_recall_projection.json").read_text(encoding="utf-8")
            )
            restored_rp_str = json.dumps(restored_rp, ensure_ascii=False)
            self.assertNotIn(
                poisoned_text,
                restored_rp_str,
                "墓碑 id 的预埋条目必须被墓碑清洗路径原地清除，而非静默保留",
            )
            self.assertEqual(
                len(restored_rp.get("active_items") or []),
                0,
                "active_items 中被墓碑命中的条目必须被移除",
            )
            ts_raw = restored_rp.get("tombstones")
            if isinstance(ts_raw, dict):
                ts_ids = set(ts_raw.keys())
            elif isinstance(ts_raw, list):
                ts_ids = {
                    t.get("memory_id") for t in ts_raw if isinstance(t, dict)
                }
            else:
                ts_ids = set()
            self.assertIn(
                "mem_deleted_forever_01",
                ts_ids,
                "墓碑水位必须随导入重放进 dst 的 v02_recall_projection.json",
            )

            restored_hearth = json.loads(
                (phone_dir / "home_hearth.json").read_text(encoding="utf-8")
            )
            restored_hearth_str = json.dumps(restored_hearth, ensure_ascii=False)
            self.assertNotIn(
                poisoned_text,
                restored_hearth_str,
                "不在 bundle 中的白名单切片也必须按墓碑水位原地清洗（elif all_tombstone_ids 分支）",
            )
            self.assertNotIn(
                "mem_deleted_forever_01",
                restored_hearth_str,
                "墓碑 id 不得以任何形式残留在其余白名单切片里",
            )

    def test_health_signals_garbage_fields_coerced_zero_write(self) -> None:
        """2026-11-14 审计项4：垃圾字符串健康信号负控（为 _coerce_signal_int 修复上锁）。

        ready.json / queue.json 字段被注入垃圾（字符串、浮点串、None、bool、嵌套
        dict、Infinity、超大整数）时，inspect_day_root_health_signals 必须：
        不抛 ValueError、非数值 coerce 成 0、合法超大整数原样通过、且全程零写盘。
        """
        with tempfile.TemporaryDirectory(prefix="ny_v07_garbage_") as g_dir:
            g_root = pathlib.Path(g_dir)
            (g_root / "ready.json").write_text(
                json.dumps(
                    {
                        "schema": "nyairo.ready.v1",
                        "ready": True,
                        "status": "ok",
                        "queue_backlog": None,
                        "backlog_count": float("inf"),
                        "pending_required_count": None,
                        "dead_letter_count": True,
                        "oldest_unacked_seconds": {"nested": "dict"},
                        "runnable_count": 10**400,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (g_root / "queue.json").write_text(
                json.dumps(
                    {
                        "schema": "nyairo.queue.v1",
                        "status": "ok",
                        "queue_backlog": "many",
                        "pending_required_count": "1.5",
                        "dead_letter_count": "many",
                        "oldest_unacked_seconds": "many",
                        "runnable_count": "many",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            def snapshot() -> dict[str, tuple[int, int]]:
                return {
                    p.relative_to(g_root).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns)
                    for p in g_root.rglob("*")
                    if p.is_file()
                }

            before = snapshot()
            inspect_res = rm_engine.inspect_day_root_health_signals(
                g_root,
                now_dt=datetime(2026, 11, 14, 10, 0, 0, tzinfo=timezone.utc),
            )
            after = snapshot()
            self.assertEqual(
                before,
                after,
                "健康信号巡检必须零写盘（文件大小与 mtime 不得变动）",
            )

            sig = inspect_res.get("signals") or {}
            self.assertEqual(
                sig.get("queue_backlog_count"), 0, "垃圾字符串/Infinity 必须被 coerce 成 0"
            )
            self.assertEqual(sig.get("pending_required_count"), 0)
            self.assertEqual(sig.get("dead_letter_count"), 0, "bool True 不得被当成数量 1")
            self.assertEqual(sig.get("oldest_unacked_seconds"), 0, "嵌套 dict 必须被 coerce 成 0")
            self.assertEqual(
                sig.get("runnable_count"),
                10**400,
                "合法超大整数应原样通过，不得截断或抛 OverflowError",
            )
            self.assertEqual(sig.get("tick_age_s"), 0.0)
            self.assertEqual(
                inspect_res.get("fault_reasons"),
                [],
                "纯垃圾字段不构成任何真实故障，不得伪造故障原因",
            )
            self.assertIs(inspect_res.get("is_faulted"), False)

    def test_suitcase_recomputed_sha_import_documents_integrity_only_boundary(self) -> None:
        """2026-11-14 审计项5：主动伪造边界文档化测试。

        bundle_sha256 是无密钥完整性哈希：只防意外损坏（篡改后不重算哈希会被
        SUITCASE_CHECKSUM_MISMATCH 拒绝），不防主动伪造（任何持有 bundle 的
        攻击者都可以用 compute_suitcase_bundle_sha256 重算哈希后导入）。
        本测试把该边界固化为显式声明的已知行为：先验证不重算即被拒，再验证
        重算即被接受且伪造内容真实落盘。若未来实现升级为签名/密钥校验，
        请同步更新本测试与边界声明，而不是回退实现。
        """
        with (
            tempfile.TemporaryDirectory(prefix="ny_v07_fg_src_") as src_dir,
            tempfile.TemporaryDirectory(prefix="ny_v07_fg_dst_") as dst_dir,
        ):
            src_root = pathlib.Path(src_dir)
            dst_root = pathlib.Path(dst_dir)
            (src_root / "phone").mkdir(parents=True, exist_ok=True)
            (src_root / "phone" / "desk_tomorrow_note.json").write_text(
                json.dumps(
                    {
                        "schema": "nyairo.desk_tomorrow_note.v1",
                        "partner_note": "伪造边界验证原始便签",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            exp_code, exp_env = rm_view.mutate_v02_runtime_migration_view(
                src_root,
                {"action": "export_suitcase", "profile_mode": "records_only"},
                actor_side="partner",
            )
            self.assertEqual(exp_code, 200)
            self.assertTrue(exp_env.get("ok"))
            bundle = (exp_env.get("data") or {}).get("suitcase_bundle")
            self.assertIsInstance(bundle, dict)

            # 第一步：只篡改、不重算哈希 → 必须被完整性校验拒绝（防意外损坏）
            naive = copy.deepcopy(bundle)
            naive["profile_mode"] = rm_engine.SUITCASE_FULL_PRACTICE
            naive.setdefault("slices", {}).setdefault("desk_tomorrow_note.json", {})[
                "partner_note"
            ] = "主动伪造版本（未重算哈希）"
            bad_code, bad_env = rm_view.mutate_v02_runtime_migration_view(
                dst_root,
                {"action": "import_suitcase", "bundle": naive},
                actor_side="partner",
            )
            self.assertTrue(
                bad_code >= 400 or not bad_env.get("ok"),
                "篡改后不重算 bundle_sha256 必须被完整性校验拒绝",
            )

            # 第二步：篡改后重算哈希 → 当前实现接受导入（防不了主动伪造）
            forged = copy.deepcopy(bundle)
            forged["profile_mode"] = rm_engine.SUITCASE_FULL_PRACTICE
            forged.setdefault("slices", {}).setdefault("desk_tomorrow_note.json", {})[
                "partner_note"
            ] = "主动伪造版本（已重算哈希）"
            forged["bundle_sha256"] = rm_engine.compute_suitcase_bundle_sha256(forged)
            imp_code, imp_env = rm_view.mutate_v02_runtime_migration_view(
                dst_root,
                {"action": "import_suitcase", "bundle": forged},
                actor_side="partner",
            )
            self.assertEqual(
                imp_code,
                200,
                "bundle_sha256 为无密钥完整性哈希，防意外损坏不防主动伪造："
                "重算哈希即可通过导入。此为文档化的已知边界，不是回归。",
            )
            self.assertTrue(
                imp_env.get("ok"),
                "bundle_sha256 为无密钥完整性哈希，防意外损坏不防主动伪造。",
            )
            restored_desk = json.loads(
                (dst_root / "phone" / "desk_tomorrow_note.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                restored_desk.get("partner_note"),
                "主动伪造版本（已重算哈希）",
                "伪造内容真实落盘：完整性哈希不提供来源认证或防抵赖能力",
            )

    def test_suitcase_secret_key_blacklist_covers_bare_token_and_authorization(self) -> None:
        """2026-11-14 审计项6a：键名黑名单补齐裸 "token" / "authorization" 的双向上锁。

        导出侧：切片里这两个裸键必须被 _sanitize_suitcase_tree 整键剥离；
        导入侧：bundle 里若出现这两个裸键，必须被 _assert_no_secret_keys_recursive
        以 SUITCASE_SECRET_KEY_FORBIDDEN 响亮拒绝，绝不静默放行。
        """
        with (
            tempfile.TemporaryDirectory(prefix="ny_v07_bl_src_") as src_dir,
            tempfile.TemporaryDirectory(prefix="ny_v07_bl_dst_") as dst_dir,
        ):
            src_root = pathlib.Path(src_dir)
            dst_root = pathlib.Path(dst_dir)
            (src_root / "phone").mkdir(parents=True, exist_ok=True)
            (src_root / "phone" / "desk_tomorrow_note.json").write_text(
                json.dumps(
                    {
                        "schema": "nyairo.desk_tomorrow_note.v1",
                        "partner_note": "黑名单上锁验证便签",
                        "token": "bare-token-value-must-strip",
                        "authorization": "basic-cred-value-must-strip",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            exp_code, exp_env = rm_view.mutate_v02_runtime_migration_view(
                src_root,
                {"action": "export_suitcase", "profile_mode": "records_only"},
                actor_side="partner",
            )
            self.assertEqual(exp_code, 200)
            self.assertTrue(exp_env.get("ok"))
            bundle = (exp_env.get("data") or {}).get("suitcase_bundle")
            self.assertIsInstance(bundle, dict)
            raw_bundle_str = json.dumps(bundle, ensure_ascii=False)
            self.assertNotIn(
                "bare-token-value-must-strip",
                raw_bundle_str,
                "裸 token 键必须在导出脱敏时整键剥离",
            )
            self.assertNotIn(
                "basic-cred-value-must-strip",
                raw_bundle_str,
                "裸 authorization 键必须在导出脱敏时整键剥离",
            )

            # 导入侧：向合法 bundle 里塞一个裸 token 键并重算哈希，必须被键名黑名单拒绝
            smuggled = copy.deepcopy(bundle)
            smuggled.setdefault("slices", {}).setdefault("desk_tomorrow_note.json", {})[
                "token"
            ] = "smuggled-cred"
            smuggled["bundle_sha256"] = rm_engine.compute_suitcase_bundle_sha256(smuggled)
            imp_code, imp_env = rm_view.mutate_v02_runtime_migration_view(
                dst_root,
                {"action": "import_suitcase", "bundle": smuggled},
                actor_side="partner",
            )
            self.assertEqual(
                imp_code,
                400,
                "bundle 夹带裸 token 键必须被键名黑名单拒绝为 400",
            )
            self.assertFalse(imp_env.get("ok"))
            self.assertEqual(
                imp_env.get("reason_code"),
                "SUITCASE_SECRET_KEY_FORBIDDEN",
                "拒绝原因码必须是 SUITCASE_SECRET_KEY_FORBIDDEN",
            )


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""身体持续姿态积分：固定 60 秒假设 -> 按已记账 elapsed 连续积分。

本文件只验机制（可控时钟/显式立即数），不冒认真模型主动休息，也不声称
任何真实一日的身体状态。覆盖：不同已知 elapsed、零 elapsed、拆段一致、
姿态切换归属、MOVE 出发不预扣/到达不重复 movement、重启同 cursor 不双记、
旧档缺 anchor 不编历史、REST 既有门/恢复不变、业务钟与真实墙钟不混。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT / "components" / "world" / "scripts"),):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import body_consequence as bcon  # noqa: E402
import body_runtime as br  # noqa: E402

T0 = datetime(2026, 10, 24, 0, 0, tzinfo=timezone.utc)

_POSIX = os.name != "nt"


def _pose_result(*, execution_id, before, after, status="applied"):
    return {
        "execution_id": execution_id,
        "action": "POSE",
        "status": status,
        "before": {"pose": before},
        "after": {"pose": after},
    }


def _move_result(*, execution_id, status="applied", before="standing"):
    return {
        "execution_id": execution_id,
        "action": "MOVE",
        "status": status,
        "before": {"pose": before},
        "after": {"pose": before},
    }


def _consequence_inputs(result, *, elapsed, pose=None):
    record = bcon.build_consequence(
        result, body_id="chiyo_body", elapsed_seconds=elapsed, pose=pose
    )
    return record, [i for i in record["inputs"] if i["class"] == "PHYSICAL_RESULT"]


class PostureIntegrationTests(unittest.TestCase):
    """Pure build/apply; no World service needed."""

    def test_known_elapsed_replaces_the_fixed_sixty_seconds(self):
        record, physical = _consequence_inputs(
            _pose_result(execution_id="e-2h", before="seated", after="standing"),
            elapsed=7200.0,
        )
        posture = [i for i in physical if i.get("posture_seconds")]
        self.assertEqual(len(posture), 1, record)
        self.assertEqual(posture[0]["posture_seconds"], 7200.0)
        self.assertEqual(posture[0]["pose"], "seated")
        # 证据只看既有 PHYSICAL_RESULT input 与 notes，不新增记录字段
        self.assertIn("posture_elapsed:7200.000s:pose=seated", record["notes"])
        self.assertNotIn("posture_interval", record)
        # 不再有任何固定 60 秒的痕迹
        self.assertNotIn(60.0, [i.get("posture_seconds") for i in physical])

    def test_zero_or_missing_elapsed_charges_no_posture(self):
        for elapsed in (0.0, None):
            record, physical = _consequence_inputs(
                _pose_result(execution_id="e-0", before="seated", after="seated"),
                elapsed=elapsed,
            )
            self.assertEqual(
                [i.get("posture_seconds") for i in physical if i.get("posture_seconds")],
                [],
                record,
            )
            self.assertNotIn("posture_interval", record)
            # 只建立游标（zero 记录），不带任何正姿态积分
            self.assertFalse(
                [n for n in record["notes"]
                 if n.startswith("posture_elapsed:")])

    def test_split_advances_match_one_shot_within_tolerance(self):
        state = br.initial_runtime(at=T0)
        seated = {"class": "PHYSICAL_RESULT", "posture_seconds": 3600.0, "pose": "seated"}
        two_hours = {"class": "PHYSICAL_RESULT", "posture_seconds": 7200.0, "pose": "seated"}

        first = br.advance(state, T0, T0 + timedelta(hours=1), [seated])
        second = br.advance(first["state"], T0 + timedelta(hours=1),
                            T0 + timedelta(hours=2), [seated])
        one_shot = br.advance(state, T0, T0 + timedelta(hours=2), [two_hours])

        # 积分量必须一致：同一姿态秒数不因拆段而多记/少记。
        split_strain = (first["inputs_summary"]["posture_strain_hours"]
                        + second["inputs_summary"]["posture_strain_hours"])
        split_load = (first["inputs_summary"]["load_units"]
                      + second["inputs_summary"]["load_units"])
        self.assertAlmostEqual(split_strain,
                               one_shot["inputs_summary"]["posture_strain_hours"], delta=1e-9)
        self.assertAlmostEqual(split_load, one_shot["inputs_summary"]["load_units"], delta=1e-9)
        # 终值只差既有 advance 的衰减先后（负荷在段末入账），不是姿态重复计。
        self.assertAlmostEqual(second["after"]["fatigue"], one_shot["after"]["fatigue"],
                               delta=0.05)

    def test_pose_change_interval_belongs_to_the_old_pose(self):
        # 0->2h 坐姿，2h 时站起：刚结束的 2 小时必须记在 seated 上
        record, _ = _consequence_inputs(
            _pose_result(execution_id="e-sit2stand", before="seated", after="standing"),
            elapsed=7200.0,
        )
        self.assertEqual(
            [i["pose"] for i in record["inputs"] if i.get("posture_seconds")], ["seated"])
        # 站姿之后的下一段，再按 standing 归属
        record2, _ = _consequence_inputs(
            _pose_result(execution_id="e-stand1", before="standing", after="seated"),
            elapsed=3600.0,
        )
        self.assertEqual(
            [i["pose"] for i in record2["inputs"] if i.get("posture_seconds")], ["standing"])

    def test_move_departure_precharges_nothing_and_arrival_bills_movement_only(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = bcon.ConsequenceLedger(Path(root) / "consequences.jsonl")
            state = br.initial_runtime(at=T0)

            departed = bcon.defer_for_execution(
                _move_result(execution_id="e-move-1"),
                body_id="chiyo_body",
                ledger=ledger,
                at=T0,
            )
            self.assertTrue(departed["deferred"])
            self.assertEqual(departed["consequence"]["inputs"], [])
            self.assertEqual(departed["consequence"]["state"],
                             bcon.CONSEQUENCE_STATE_PENDING)
            # 出发不写运行态、不预扣未走完的路
            self.assertIsNone(departed["state"])

            arrived = bcon.apply_for_execution(
                {"execution_id": "e-move-1", "action": "MOVE", "status": "applied",
                 "before": {"pose": "standing"}},
                body_id="chiyo_body",
                ledger=ledger,
                runtime_state=state,
                at=T0 + timedelta(hours=1),
                world_facts={"movement_seconds": 300.0,
                             "movement_source": bcon.MEASUREMENT_MEASURED_TRAVEL},
            )
            self.assertTrue(arrived["applied"], arrived)
            physical = [i for i in arrived["consequence"]["inputs"]
                        if i["class"] == "PHYSICAL_RESULT"]
            movement = [i for i in physical if i["movement_duration_seconds"]]
            posture = [i for i in physical if i.get("posture_seconds")]
            # 走路 300 秒记 movement；出发前静止的 3300 秒记 posture，不丢也不重
            self.assertEqual([i["movement_duration_seconds"] for i in movement], [300.0])
            self.assertEqual([i["posture_seconds"] for i in posture], [3300.0])
            self.assertEqual(posture[0]["pose"], "standing")

            self.assertEqual(
                movement[0]["movement_duration_seconds"]
                + posture[0]["posture_seconds"],
                3600.0,
            )

    def test_move_without_static_remainder_charges_no_posture(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = bcon.ConsequenceLedger(Path(root) / "consequences.jsonl")
            state = br.initial_runtime(at=T0)
            arrived = bcon.apply_for_execution(
                {"execution_id": "e-move-2", "action": "MOVE", "status": "applied",
                 "before": {"pose": "standing"}},
                body_id="chiyo_body", ledger=ledger, runtime_state=state,
                at=T0 + timedelta(minutes=5),
                world_facts={"movement_seconds": 300.0,
                             "movement_source": bcon.MEASUREMENT_MEASURED_TRAVEL},
            )
            physical = [i for i in arrived["consequence"]["inputs"]
                        if i["class"] == "PHYSICAL_RESULT"]
            self.assertEqual(len(physical), 1, physical)  # 只有 movement
            self.assertNotIn("posture_interval", arrived["consequence"])

    def test_restart_uses_the_persisted_cursor_and_never_double_books(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = bcon.ConsequenceLedger(Path(root) / "consequences.jsonl")
            state = br.initial_runtime(at=T0)
            first = bcon.apply_for_execution(
                _pose_result(execution_id="e-restart-1", before="standing", after="seated"),
                body_id="chiyo_body", ledger=ledger, runtime_state=state,
                at=T0 + timedelta(hours=1),
            )
            self.assertTrue(first["applied"], first)
            persisted = json.loads(json.dumps(first["state"]))
            reloaded = br.validate_runtime(persisted)
            self.assertEqual(reloaded["last_advanced_at"],
                             (T0 + timedelta(hours=1)).isoformat())

            replay = bcon.apply_for_execution(
                _pose_result(execution_id="e-restart-1", before="standing", after="seated"),
                body_id="chiyo_body", ledger=ledger, runtime_state=reloaded,
                at=T0 + timedelta(hours=1, minutes=5),
            )
            self.assertFalse(replay["applied"])
            self.assertEqual(replay["reason"], "duplicate_execution_id")

            later = bcon.apply_for_execution(
                _pose_result(execution_id="e-restart-2", before="seated", after="standing"),
                body_id="chiyo_body", ledger=ledger, runtime_state=reloaded,
                at=T0 + timedelta(hours=2),
            )
            posture = [i for i in later["consequence"]["inputs"]
                       if i.get("posture_seconds")]
            self.assertEqual(posture[0]["posture_seconds"], 3600.0)  # 1h, not 2h
            self.assertEqual(posture[0]["pose"], "seated")

    def test_old_runtime_without_anchor_invents_no_history(self):
        legacy = br.initial_runtime()  # 没有 at：last_advanced_at 为 None
        self.assertIsNone(legacy["last_advanced_at"])
        with tempfile.TemporaryDirectory() as root:
            ledger = bcon.ConsequenceLedger(Path(root) / "consequences.jsonl")
            outcome = bcon.apply_for_execution(
                _pose_result(execution_id="e-legacy", before="lying", after="seated"),
                body_id="chiyo_body", ledger=ledger, runtime_state=legacy,
                at=T0,
            )
        self.assertTrue(outcome["applied"], outcome)
        self.assertNotIn("posture_interval", outcome["consequence"])
        posture = [i for i in outcome["consequence"]["inputs"] if i.get("posture_seconds")]
        self.assertEqual(posture, [])
        # 第一次只建立游标，不回填任何“过去多久”
        self.assertEqual(outcome["state"]["last_advanced_at"], T0.isoformat())

    def test_environment_tick_bills_the_interval_the_next_pose_would_have_lost(self):
        # t0 坐下，t1 环境推进（灯光 tick），t2 站起。t0->t1 的姿态秒数必须由
        # 环境推进结算，不能被 last_advanced_at 前移吞掉，也不能在 t2 重复。
        state = br.initial_runtime(at=T0)
        light = {"status": "available", "power": "on", "band": "dim",
                 "source": "world_room_environment", "place_id": "home",
                 "area_id": "bedroom"}
        tick = bcon.advance_environment(
            runtime_state=state, light=light,
            at=T0 + timedelta(minutes=12), posture="seated",
        )
        self.assertTrue(tick["advanced"], tick)
        summary = tick["transition"]["input_summary"]
        self.assertEqual(summary["posture_interval_seconds"], 720.0)
        self.assertEqual(summary["posture_pose"], "seated")
        self.assertTrue(tick["state"]["last_advanced_at"].startswith("2026-10-24T00:12"))

        with tempfile.TemporaryDirectory() as root:
            ledger = bcon.ConsequenceLedger(Path(root) / "consequences.jsonl")
            later = bcon.apply_for_execution(
                _pose_result(execution_id="e-env-then-pose",
                             before="seated", after="standing"),
                body_id="chiyo_body", ledger=ledger,
                runtime_state=tick["state"],
                at=T0 + timedelta(minutes=42),
            )
        posture = [i for i in later["consequence"]["inputs"] if i.get("posture_seconds")]
        # 只剩 tick 之后的 30 分钟，t0->t1 不再重复计
        self.assertEqual([i["posture_seconds"] for i in posture], [1800.0])

    def test_environment_tick_claims_no_posture_across_an_unknown_gap(self):
        state = br.initial_runtime(at=T0)
        light = {"status": "available", "power": "on", "band": "dim",
                 "since": (T0 - timedelta(hours=1)).isoformat(),
                 "source": "world_room_environment", "place_id": "home",
                 "area_id": "bedroom"}
        outcome = bcon.advance_environment(
            runtime_state=state, light=light,
            at=T0 + timedelta(hours=3), posture="seated",
        )
        self.assertTrue(outcome["gap_unknown"], outcome)
        self.assertEqual(outcome["transition"]["input_summary"]["posture_interval_seconds"], 0.0)
        self.assertIsNone(outcome["transition"]["input_summary"]["posture_pose"])

    def test_environment_tick_does_not_interrupt_an_open_rest(self):
        state = br.initial_runtime(at=T0)
        state["somatic"]["fatigue"] = 0.60
        state["recovery"] = {
            **state["recovery"], "state": "active", "mode": "rest",
            "started_at": T0.isoformat(), "last_advanced_at": T0.isoformat(),
        }
        light = {"status": "available", "power": "on", "band": "dim",
                 "source": "world_room_environment", "place_id": "home",
                 "area_id": "bedroom"}
        outcome = bcon.advance_environment(
            runtime_state=state, light=light,
            at=T0 + timedelta(minutes=10), posture="seated",
        )
        self.assertEqual(outcome["transition"]["input_summary"]["posture_interval_seconds"], 0.0)
        self.assertEqual(outcome["state"]["recovery"]["state"], "active")
        self.assertNotIn(
            "recovery_interrupted_by_physical_load",
            outcome["transition"]["input_summary"].get("notes", []),
        )

    def test_rest_command_unchanged_by_posture_integration(self):
        tired = br.initial_runtime(at=T0)
        tired["somatic"]["fatigue"] = 0.60
        rest = {"execution_id": "e-rest-1", "action": "REST", "status": "applied",
                "recovery_state": "start", "recovery_mode": "rest", "before": {"pose": "lying"}}
        with tempfile.TemporaryDirectory() as root:
            ledger = bcon.ConsequenceLedger(Path(root) / "consequences.jsonl")
            opened = bcon.apply_for_execution(
                rest, body_id="chiyo_body", ledger=ledger, runtime_state=tired,
                at=T0 + timedelta(hours=1),
            )
        self.assertTrue(opened["applied"], opened)
        # REST 只带 RECOVERY 类；若姿态积分混进来会以物理负荷打断休息
        classes = [i["class"] for i in opened["consequence"]["inputs"]]
        self.assertEqual(classes, ["RECOVERY"])
        self.assertEqual(opened["state"]["recovery"]["state"], "active")
        self.assertNotIn("recovery_interrupted_by_physical_load", opened["advance"]["notes"])

    def test_business_clock_is_the_elapsed_source_not_wall_clock(self):
        # 显式往未来 2 小时结算，与真实墙钟无关（结果只由 anchor/at 决定）
        state = br.initial_runtime(at=T0)
        with tempfile.TemporaryDirectory() as root:
            ledger = bcon.ConsequenceLedger(Path(root) / "consequences.jsonl")
            outcome = bcon.apply_for_execution(
                _pose_result(execution_id="e-clock", before="seated", after="standing"),
                body_id="chiyo_body", ledger=ledger, runtime_state=state,
                at=T0 + timedelta(hours=2),
            )
        posture = [i for i in outcome["consequence"]["inputs"] if i.get("posture_seconds")]
        self.assertEqual(posture[0]["posture_seconds"], 7200.0)

    def test_unknown_pose_closes_its_window_and_is_never_attributed_later(self):
        # POSE 结果 UNKNOWN：姿态是否在未知时刻改变不可知，因此这段区间不归
        # before.pose，也不事后补量；下一次结算只计未知时刻之后的区间。
        state = br.initial_runtime(at=T0)
        with tempfile.TemporaryDirectory() as root:
            ledger = bcon.ConsequenceLedger(Path(root) / "consequences.jsonl")
            unknown = bcon.apply_for_execution(
                _pose_result(execution_id="e-unknown-pose", before="seated",
                             after="standing", status="uncertain"),
                body_id="chiyo_body", ledger=ledger, runtime_state=state,
                at=T0 + timedelta(hours=2),
            )
            self.assertFalse(unknown["applied"])
            self.assertTrue(unknown.get("cursor_advanced"), unknown)
            self.assertIn(
                bcon.POSTURE_INTERVAL_UNKNOWN_NOTE, unknown["consequence"]["notes"]
            )
            self.assertEqual(unknown["consequence"]["inputs"], [])

            later = bcon.apply_for_execution(
                _pose_result(execution_id="e-after-unknown", before="standing",
                             after="seated"),
                body_id="chiyo_body", ledger=ledger,
                runtime_state=unknown["state"], at=T0 + timedelta(hours=3),
            )
            posture = [
                i["posture_seconds"]
                for i in later["consequence"]["inputs"]
                if i.get("posture_seconds")
            ]
            # 只计未知时刻之后的 1 小时；未知的 2 小时没有推算给任何姿态
            self.assertEqual(posture, [3600.0])

            # 事后 reconcile 成 SUCCESS：仍不补记未知窗口
            reconciled = bcon.reconcile_pending(
                ledger, execution_id="e-unknown-pose",
                confirmed_result=_pose_result(
                    execution_id="e-unknown-pose", before="seated", after="standing"
                ),
                body_id="chiyo_body", runtime_state=later["state"],
                at=T0 + timedelta(hours=4),
            )
            self.assertTrue(reconciled["applied"], reconciled)
            reconciled_posture = [
                i["posture_seconds"]
                for i in reconciled["consequence"]["inputs"]
                if i.get("posture_seconds")
            ]
            self.assertEqual(reconciled_posture, [])

    def test_move_unknown_also_closes_its_window(self):
        state = br.initial_runtime(at=T0)
        with tempfile.TemporaryDirectory() as root:
            ledger = bcon.ConsequenceLedger(Path(root) / "consequences.jsonl")
            unknown = bcon.apply_for_execution(
                _move_result(execution_id="e-unknown-move", status="prepared"),
                body_id="chiyo_body", ledger=ledger, runtime_state=state,
                at=T0 + timedelta(minutes=30),
            )
            self.assertFalse(unknown["applied"])
            self.assertTrue(unknown.get("cursor_advanced"), unknown)
            self.assertIn(
                bcon.POSTURE_INTERVAL_UNKNOWN_NOTE, unknown["consequence"]["notes"]
            )

    def test_environment_tick_keeps_the_light_while_a_walk_is_in_flight(self):
        # 在途 MOVE 不得让整个环境 tick 让位：灯光照常落账，姿态只记出发前的
        # 那一段，走路秒数留给到达结算，不重复。
        state = br.initial_runtime(at=T0)
        light = {"status": "available", "power": "on", "band": "dim",
                 "source": "world_room_environment", "place_id": "home",
                 "area_id": "bedroom"}
        departure = T0 + timedelta(minutes=2)
        tick = bcon.advance_environment(
            runtime_state=state, light=light, at=T0 + timedelta(minutes=5),
            posture="standing", movement_in_flight_since=departure.isoformat(),
        )
        self.assertTrue(tick["advanced"], tick)
        summary = tick["transition"]["input_summary"]
        self.assertEqual(summary["environment_gap_unknown"], False)
        self.assertIn("environment_model", summary)
        # 只记 00:00->00:02 的出发前静态
        self.assertEqual(summary["posture_interval_seconds"], 120.0)
        self.assertEqual(summary["movement_in_flight_since"], departure.isoformat())

        # 同一段走路期间再 tick：游标已在出发之后，不再记静态，但仍落灯光
        second = bcon.advance_environment(
            runtime_state=tick["state"], light=light,
            at=T0 + timedelta(minutes=10), posture="standing",
            movement_in_flight_since=departure.isoformat(),
        )
        self.assertTrue(second["advanced"], second)
        self.assertEqual(
            second["transition"]["input_summary"]["posture_interval_seconds"], 0.0
        )
        self.assertIn("environment_model", second["transition"]["input_summary"])


class RealServiceEnvironmentTickTests(unittest.TestCase):
    """真 WorldBodyService + 真 substrate 姿态 + 真灯具；只有灯源是既有夹具。

    负控目的：tick 的环境推进必须把 body 游标走到姿态结算里，而不是只走
    last_advanced_at 却没有任何姿态负荷（旧实现会吞掉整个区间）。
    """

    def setUp(self):
        if not _POSIX:
            self.skipTest("owner tick fixture is POSIX-only")
        self.moment = datetime(2026, 10, 21, 3, 0, tzinfo=timezone.utc)
        for entry in (str(ROOT / "components/world/scripts"),
                      str(ROOT / "components/world/service")):
            if entry not in sys.path:
                sys.path.insert(0, entry)
        import body_signal as bsignal  # noqa: F401
        import world_body_config as wbc
        import world_body_service as wbsvc
        import world_body_substrate as substrate
        import world_property_economy as economy
        import world_room_environment as room

        self._saved_env = dict(os.environ)
        self.home = Path(tempfile.mkdtemp(prefix="nyairo-posture-env-"))
        for relative in (("data", "world"), ("data", "body"), ("state",), ("run",)):
            self.home.joinpath(*relative).mkdir(parents=True, exist_ok=True)
        env = {
            economy.FEATURE_ENV: "1",
            economy.WORLD_FEATURE_ENV: "1",
            economy.DAILY_FEATURE_ENV: "1",
            economy.DELIVERY_FEATURE_ENV: "0",
            substrate.FEATURE_ENV: "1",
            room.FEATURE_ENV: "1",
        }
        os.environ.update(env)
        world_state = {
            "facts": [],
            "location": {"area_id": "counter", "place_id": "home_goods_store"},
            "revision": 5,
            "scene": {"revision": 5, "scene_id": "home.bedroom"},
            "schema_version": "world.foundation.v0",
            "timezone": "Asia/Tokyo",
            "world_id": "shiomi_city",
        }
        (self.home / "data/world/world_state.json").write_text(
            json.dumps(world_state, sort_keys=True), encoding="utf-8")
        (self.home / "data/world/world_timeline.json").write_text(
            json.dumps({"schema_version": "world.timeline.v1", "revision": 1,
                        "world_id": "shiomi_city", "processes": []}, sort_keys=True),
            encoding="utf-8")
        (self.home / "data/life_execution.jsonl").write_text("", encoding="utf-8")
        (self.home / "state/world_object_capabilities.json").write_text(
            json.dumps({"schema_version": substrate.CAPABILITIES_SCHEMA,
                        "objects": {}, "consumables": {}}, sort_keys=True),
            encoding="utf-8")
        (self.home / "data/world/world_state.json").write_text(
            json.dumps(world_state, sort_keys=True), encoding="utf-8")
        economy.initialize_genesis(hermes_home=self.home, environment=env)
        bought = economy.store_purchase_item(
            catalog_item_id="bedside_lamp", operation_key="op-posture-lamp",
            local_owner={"source": economy.LOCAL_OWNER_SOURCE, "uid": os.getuid(),
                         "subject": economy.OWNER, "home": str(self.home)},
            hermes_home=self.home, environment=env, now=self.moment,
        )
        self.assertEqual(bought["status"], "applied", bought)
        self.lamp = bought["item_instance_id"]
        # 买完灯再把世界位置写回卧室（与灯具 placement 一致）
        (self.home / "data/world/world_state.json").write_text(
            json.dumps(dict(world_state, location={"area_id": "bedroom", "place_id": "home"}),
                       sort_keys=True), encoding="utf-8")
        store = substrate.SubstrateStore(substrate.substrate_path(self.home))
        live = substrate.initial_substrate(world_id="shiomi_city", body_id="chiyo_body")
        live = substrate.place_object(
            live, self.lamp,
            {"kind": "room", "location_id": "home", "area_id": "bedroom"})
        # 身体半蹲在 seated 姿态，World owner 的 substrate 是唯一姿态来源
        live = substrate.set_pose(live, "seated")
        store.save(live)
        self.cursor = (self.moment - timedelta(minutes=10)).isoformat()
        br.BodyStore(self.home / "data/body").save_runtime(
            br.initial_runtime(body_id="chiyo_body", at=self.cursor))
        store.save(substrate.set_fixture(
            live, self.lamp, "on",
            since=(self.moment - timedelta(minutes=5)).isoformat(),
            declared_source="catalog:bedside_lamp",
            expected_revision=live["revision"]))
        config = wbc.load_config(
            env, runtime_root=ROOT / "components" / "world",
            world_home=self.home, runtime_mode="canonical",
            authority="production", world_id="shiomi_city")
        service = wbsvc.WorldBodyService(
            config, clock=lambda: self.moment,
            instance_id="posture-env-%d" % os.getpid(), tick_seconds=0.05)
        self.addCleanup(service.shutdown)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(self._saved_env)))
        service.startup()
        self.service = service

    def test_the_real_owner_tick_bills_posture_once_and_advances_the_cursor(self):
        outcome = self.service._settle_environment()
        self.assertTrue(outcome["advanced"], outcome)
        summary = outcome["transition"]["input_summary"]
        self.assertEqual(summary["posture_pose"], "seated")
        # 从 body 游标（10 分钟前）起算，不是从灯亮起（5 分钟前）
        self.assertGreaterEqual(summary["posture_interval_seconds"], 540.0)
        self.assertIn(br.CAUSE_PHYSICAL, outcome["transition"]["cause_classes"])
        self.assertEqual(self.service.body_state["last_advanced_at"][:16],
                         outcome["state"]["last_advanced_at"][:16])
        # 同一区间不重复记
        second = self.service._settle_environment()
        self.assertFalse(second["advanced"], second)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""v0.8 Wave 2: courtyard drying / porch plant / desk craft / corner errand loop.

End-to-end tests over the real owners:
* ``v04_neighborhood_living`` state + ``v04_neighborhood_view`` mutation gate,
* ``collect_v02_v03_loop_candidates`` / ``settle_v02_v03_round_consequences``,
* the real ``ArtifactContinuationSource.materialize`` + AG-1 decision path with a
  real ``WorldBodyService`` acute body (only the cognition answer is scripted).

Run:  python3 tests/test_v08_courtyard_drying_loop.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "components" / "native" / "integration"),
    str(ROOT / "tests"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import agency_decision_ag1 as ag1  # noqa: E402
import artifact_continuation_source as acs  # noqa: E402
import autonomous_life_cycle as alc  # noqa: E402
import body_health_projection as bhp  # noqa: E402
import body_runtime as br  # noqa: E402
import candidate_sources_ag0 as ag0  # noqa: E402
import life_situation_view as lsv  # noqa: E402
import v02_v03_daily_loop_bridge as bridge  # noqa: E402
import test_active_abandon_reachable as abandon_fixture  # noqa: E402
from components.phone.service import PhoneService  # noqa: E402
from components.phone.v04_neighborhood_view import (  # noqa: E402
    mutate_v04_neighborhood_view,
    read_v04_neighborhood_view,
)
from components.world.scripts import v04_neighborhood_living as engine  # noqa: E402
from world_body_gateway_api import GatewaySurface  # noqa: E402
from world_body_service import WorldBodyService  # noqa: E402

T0 = "2026-10-15T09:00:00+08:00"
T_OPEN = "2026-10-15T10:30:00+08:00"
T_LATE = "2026-10-17T21:00:00+08:00"  # T0 + 60h, bookstore closed


def _iso_add(hours: int, base: str = T0) -> str:
    dt = datetime.fromisoformat(base) + timedelta(hours=hours)
    return dt.isoformat(timespec="seconds")


def _tree_snapshot(root: Path) -> list[tuple[str, int, int]]:
    rows: list[tuple[str, int, int]] = []
    if not root.exists():
        return rows
    for path in sorted(root.rglob("*")):
        stat = path.lstat()
        rows.append((str(path.relative_to(root)), stat.st_size, stat.st_mtime_ns))
    return rows


def _write_ecology(root: Path, phase: str, severity: float) -> None:
    (root / "phone").mkdir(parents=True, exist_ok=True)
    (root / "phone" / "v03_ecology.json").write_text(
        json.dumps({
            "schema": "v03.ecology.bridge.v1",
            "schema_version": 1,
            "revision": 1,
            "illness": {
                "phase": phase,
                "severity": severity,
                "strain_load": 0.5 if phase != "healthy" else 0.0,
                "rest_active": False,
            },
        }),
        encoding="utf8",
    )


class _V04LoopTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-courtyard-")
        self.root = Path(self._tmp.name)
        (self.root / "phone").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- owners -------------------------------------------------------------------
    def init_neighborhood(self, *, weather: str = "clear", now_iso: str = T0) -> None:
        code, body = mutate_v04_neighborhood_view(
            self.root,
            {"action": "set_local_weather", "weather": weather, "airflow": "medium"},
            actor_side="partner",
            now_iso=now_iso,
        )
        self.assertEqual(code, 200, body)
        self.assertEqual(body["status"], "applied")

    def set_weather(self, weather: str, *, now_iso: str) -> None:
        code, body = mutate_v04_neighborhood_view(
            self.root,
            {"action": "set_local_weather", "weather": weather},
            actor_side="partner",
            now_iso=now_iso,
        )
        self.assertEqual(code, 200, body)

    def state(self) -> dict:
        state, exists = engine.load_state_readonly(self.root)
        self.assertTrue(exists)
        return state

    def collect(self, now_iso: str) -> dict:
        return bridge.collect_v02_v03_loop_candidates(self.root, now_iso=now_iso)

    @staticmethod
    def descriptor(collected: dict, source_ref: str) -> dict:
        for desc in collected.get("descriptors", {}).values():
            if desc.get("source_ref") == source_ref:
                return desc
        raise AssertionError(f"missing candidate {source_ref!r}")

    @staticmethod
    def event_for(desc: dict, *, tag: str) -> dict:
        return {
            "actor_side": "life_resident",
            "effect_state": "APPLIED",
            "action_kind": desc.get("v04_action_kind"),
            "v04_action": desc["v04_action"],
            "v04_payload": dict(desc.get("v04_payload") or {}),
            "source_ref": desc["source_ref"],
            "receipt_ref": f"test:{tag}",
            "operation_key": f"test_{tag}",
        }

    def settle(self, event: dict, *, now_iso: str) -> dict:
        return bridge.settle_v02_v03_round_consequences(
            self.root, event, now_iso=now_iso
        )


class CourtyardDryingLoopTest(_V04LoopTestBase):
    def test_01_sun_hang_partial_restart_rain_rescue_dry_collect_idempotent(self) -> None:
        self.init_neighborhood()
        # 上午晴天：屋内湿布巾出现"挂出晾晒"机会。
        collected = self.collect(T0)
        hang = self.descriptor(collected, "v04_courtyard_hang:wet_cloth_main")
        self.assertFalse(hang["requires_outing"])
        self.assertFalse(hang["creative_intensive"])
        settled = self.settle(self.event_for(hang, tag="hang"), now_iso=T0)
        self.assertEqual(settled["status"], "APPLIED", settled)
        self.assertIn("v04_neighborhood", settled["slices_updated"])

        cloth = self.state()["drying_items"]["wet_cloth_main"]
        self.assertEqual(cloth["support_id"], "yard_line")
        self.assertEqual(cloth["moisture"], 75.0)

        # 推进 1 小时晴天：分段蒸发，不是瞬间完成。
        advanced = engine.advance_v04_neighborhood_processes(
            self.root, now_iso=_iso_add(1)
        )
        self.assertTrue(advanced["advanced"], advanced)
        cloth = self.state()["drying_items"]["wet_cloth_main"]
        self.assertGreater(cloth["moisture"], engine.DRY_SLEEP_THRESHOLD)
        self.assertLess(cloth["moisture"], 75.0)
        partial_moisture = cloth["moisture"]

        # 停机重启：从同一目录重新读取，位置与半干进度完整保留，不伪造后续动作。
        restarted, exists = engine.load_state_readonly(self.root)
        self.assertTrue(exists)
        cloth_again = restarted["drying_items"]["wet_cloth_main"]
        self.assertEqual(cloth_again["support_id"], "yard_line")
        self.assertEqual(cloth_again["moisture"], partial_moisture)
        self.assertFalse(self.collect(_iso_add(1))["gating"]["illness"]["is_actively_ill"])

        # 转雨：露天晾晒绳上的布巾出现家门口抢收机会（不是远途外出）。
        self.set_weather("rain", now_iso=_iso_add(1))
        rainy = self.collect(_iso_add(1))
        rescue = self.descriptor(rainy, "v04_courtyard_cloth:wet_cloth_main")
        self.assertFalse(rescue["requires_outing"])
        self.assertFalse(rescue["strenuous_outing"])
        rescued = self.settle(self.event_for(rescue, tag="rescue"), now_iso=_iso_add(1))
        self.assertEqual(rescued["status"], "APPLIED", rescued)
        state = self.state()
        self.assertEqual(state["drying_items"]["wet_cloth_main"]["support_id"], "porch_hook")
        self.assertEqual(len(state["drying_items"]), 1)

        # 雨停放晴并推进到干透，收起后重复结算必须幂等。
        self.set_weather("clear", now_iso=_iso_add(1))
        engine.advance_v04_neighborhood_processes(self.root, now_iso=_iso_add(7))
        cloth = self.state()["drying_items"]["wet_cloth_main"]
        self.assertLessEqual(cloth["moisture"], engine.DRY_SLEEP_THRESHOLD)
        self.assertTrue(cloth["sleeping_dry"])

        dry_view = self.collect(_iso_add(7))
        collect_desc = self.descriptor(dry_view, "v04_courtyard_collect:wet_cloth_main")
        collect_event = self.event_for(collect_desc, tag="collect")
        first = self.settle(collect_event, now_iso=_iso_add(7))
        self.assertEqual(first["status"], "APPLIED", first)
        self.assertEqual(
            self.state()["drying_items"]["wet_cloth_main"]["support_id"], "home_rack"
        )

        again = self.settle(collect_event, now_iso=_iso_add(7))
        self.assertTrue(again["already_applied"], again)
        state = self.state()
        self.assertEqual(len(state["drying_items"]), 1)
        self.assertGreaterEqual(
            int(state["craft_bench"]["paper_units"])
            + int(state["craft_bench"]["scrap_units"])
            + int(state["craft_bench"]["consumed_in_cards_units"])
            + int(state["craft_bench"]["consumed_in_box_units"]),
            0,
        )

    def test_02_plant_craft_errand_health_gate_and_recovery(self) -> None:
        self.init_neighborhood()
        # 先把布巾挂出去，供生病时"家门口抢收"仍可执行。
        hang = self.descriptor(self.collect(T0), "v04_courtyard_hang:wet_cloth_main")
        self.settle(self.event_for(hang, tag="hang"), now_iso=T0)
        # 借共享架裁纸尺，并在营业时段借一本旧书店的书。
        code, body = mutate_v04_neighborhood_view(
            self.root,
            {"action": "shared_shelf_loan", "item_id": "paper_ruler", "operation": "borrow"},
            actor_side="subject",
            now_iso=T_OPEN,
        )
        self.assertEqual(code, 200, body)
        code, body = mutate_v04_neighborhood_view(
            self.root,
            {"action": "bookstore_service", "service": "borrow_book",
             "book_id": "book_botanical_plates"},
            actor_side="subject",
            now_iso=T_OPEN,
        )
        self.assertEqual(code, 200, body)

        # 长时间放在门廊：盆栽逐步缺水，出现浇水机会且幂等。
        engine.advance_v04_neighborhood_processes(self.root, now_iso=T_LATE)
        planted = self.collect(T_LATE)
        plant = self.descriptor(planted, "v04_porch_plant:porch_fern")
        self.assertEqual(plant["v04_payload"]["operation"], "water")
        self.assertFalse(plant["requires_outing"])
        water_event = self.event_for(plant, tag="water")
        watered = self.settle(water_event, now_iso=T_LATE)
        self.assertEqual(watered["status"], "APPLIED", watered)
        hydration = self.state()["potted_plant"]["hydration"]
        repeat = self.settle(water_event, now_iso=T_LATE)
        self.assertTrue(repeat["already_applied"], repeat)
        self.assertEqual(self.state()["potted_plant"]["hydration"], hydration)

        # 正式 v03 急性生病：外跑腿与高强度手作被门控拦下，家门口抢收不受影响。
        self.set_weather("rain", now_iso=T_LATE)
        _write_ecology(self.root, "acute_illness", 0.6)
        sick = self.collect(T_LATE)
        filtered = sick["gating"]["filtered_candidates"]
        errand_blocked = [
            row for row in filtered
            if str(row.get("source_ref", "")).startswith("v04_errand_return:")
        ]
        self.assertTrue(errand_blocked)
        self.assertTrue(all(
            row["reason_code"] == "ILLNESS_BLOCKS_STRENUOUS_OUTING" for row in errand_blocked
        ))
        craft_blocked = [
            row for row in filtered
            if str(row.get("source_ref", "")).startswith("v04_home_craft:")
        ]
        self.assertTrue(craft_blocked)
        self.assertTrue(all(
            row["reason_code"] == "ILLNESS_CREATIVE_BLOCK" for row in craft_blocked
        ))
        rescue = self.descriptor(sick, "v04_courtyard_cloth:wet_cloth_main")
        self.assertFalse(rescue["requires_outing"])
        self.assertNotIn(
            "v04_courtyard_cloth:wet_cloth_main",
            {row["source_ref"] for row in filtered},
        )

        # 真实 ArtifactContinuationSource.materialize + 真实 AG-1 决策：纸盒手作被
        # HARD_REJECT:BODY_CREATIVE_BLOCK 拒绝。
        healthy_extras = self._extras_from_collected(planted)
        _health_root, body_kwargs = self._acute_body_kwargs()
        materialized = self._materialize_extras(healthy_extras)
        rejected = self._evaluate_craft_rejected(materialized, body_kwargs)
        self.assertTrue(rejected)

        # 恢复健康后：手作与外跑腿（闭店时段走还书箱）都能正常推进，守恒不变。
        _write_ecology(self.root, "healthy", 0.0)
        restored = self.collect(T_LATE)
        craft = self.descriptor(restored, "v04_home_craft:card_organizer_box")
        self.assertTrue(craft["creative_intensive"])
        errand = self.descriptor(restored, "v04_errand_return:book_botanical_plates")
        self.assertTrue(errand["requires_outing"])
        self.assertTrue(errand.get("via_return_box"))
        self.assertEqual(errand["v04_payload"].get("via_return_box"), True)
        tool = self.descriptor(restored, "v04_errand_return:paper_ruler")

        paper_before = int(self.state()["craft_bench"]["paper_units"])
        crafted = self.settle(self.event_for(craft, tag="craft"), now_iso=T_LATE)
        self.assertEqual(crafted["status"], "APPLIED", crafted)
        bench = self.state()["craft_bench"]
        self.assertEqual(int(bench["paper_units"]), paper_before - 1)
        self.assertEqual(
            int(bench["paper_units"]) + int(bench["scrap_units"])
            + int(bench["consumed_in_cards_units"]) + int(bench["consumed_in_box_units"]),
            int(bench["total_stocked_units"]),
        )

        returned_book = self.settle(self.event_for(errand, tag="book"), now_iso=T_LATE)
        self.assertEqual(returned_book["status"], "APPLIED", returned_book)
        book = self.state()["public_life"]["bookstore"]["books"]["book_botanical_plates"]
        self.assertEqual(book["status"], "return_box_pending")
        self.assertFalse(book["shopkeeper_received"])
        self.assertEqual(len(self.state()["public_life"]["bookstore"]["books"]), 3)

        returned_tool = self.settle(self.event_for(tool, tag="tool"), now_iso=T_LATE)
        self.assertEqual(returned_tool["status"], "APPLIED", returned_tool)
        shelf = self.state()["public_life"]["shared_shelf"]["items"]
        self.assertEqual(shelf["paper_ruler"]["status"], "on_shelf")
        self.assertEqual(len(shelf), 2)

    def test_03_zero_write_guard_and_privacy_grading(self) -> None:
        empty = Path(tempfile.mkdtemp(prefix="v08-empty-v04-"))
        collected = bridge.collect_v02_v03_loop_candidates(empty, now_iso=T0)
        self.assertFalse([
            desc for desc in collected.get("descriptors", {}).values()
            if str(desc.get("source_ref", "")).startswith("v04_")
        ])
        bridge.settle_v02_v03_round_consequences(
            empty,
            {"actor_side": "life_resident", "effect_state": "APPLIED",
             "action_kind": "ADVANCE_CLOCK", "advance_clock_to_iso": _iso_add(1),
             "receipt_ref": "test:clock", "operation_key": "test_clock"},
            now_iso=T0,
        )
        self.assertFalse((empty / "world").exists())
        self.assertFalse(any(
            path.name.startswith("v04_neighborhood")
            for path in empty.rglob("*")
            if path.is_file()
        ))

        # 已初始化根目录：私人卡片草稿对 partner 视角严格隐藏，subject 视角可见。
        self.init_neighborhood()
        code, body = mutate_v04_neighborhood_view(
            self.root,
            {"action": "craft_paper_or_box", "operation": "cut_card",
             "units": 1, "card_id": "card_private", "title": "只给自己看的卡片",
             "draft_text": "这是她只想自己读到的草稿。"},
            actor_side="subject",
            now_iso=T0,
        )
        self.assertEqual(code, 200, body)

        code_p, partner = read_v04_neighborhood_view(
            self.root, has_subject_capability=False, now_iso=T0
        )
        self.assertEqual(code_p, 200)
        partner_card = partner["data"]["craft_bench"]["cards_by_id"]["card_private"]
        self.assertIsNone(partner_card["draft_text"])
        self.assertTrue(partner_card["draft_withheld"])

        code_s, subject = read_v04_neighborhood_view(
            self.root, has_subject_capability=True, now_iso=T0
        )
        self.assertEqual(code_s, 200)
        subject_card = subject["data"]["craft_bench"]["cards_by_id"]["card_private"]
        self.assertEqual(subject_card["draft_text"], "这是她只想自己读到的草稿。")

        # 实际 HTTP 路由的业务函数同样按视角分级。
        shim = NS(root=self.root, _somatic_now_iso=lambda: T0)
        shim.v04_neighborhood = types.MethodType(PhoneService.v04_neighborhood, shim)
        http_code, http_body = shim.v04_neighborhood(subject_view=False)
        self.assertEqual(http_code, 200)
        http_card = http_body["craft_bench"]["cards_by_id"]["card_private"]
        self.assertIsNone(http_card["draft_text"])
        self.assertTrue(http_card["draft_withheld"])

    # -- health-gate helpers ------------------------------------------------------
    @staticmethod
    def _extras_from_collected(collected: dict) -> list[dict]:
        extras: list[dict] = []
        for cand in collected.get("candidates") or ():
            cid = str(cand.get("candidate_id") or "")
            desc = (collected.get("descriptors") or {}).get(cid) or {}
            raw = str(desc.get("source_ref") or "")
            if not raw.startswith("v04_"):
                continue
            token = "".join(
                ch if (ch.isalnum() or ch in "_.-") else "-" for ch in raw
            ).strip("-")[:72] or "cue"
            extras.append({
                "source_ref": f"fitem:v02v03-{token}",
                "origin_source_ref": raw,
                "target_refs": list(cand.get("target_refs") or [raw]),
                "title": str(desc.get("title") or raw),
                "kind": str(desc.get("action_family") or "V04"),
                "action_family": str(desc.get("action_family") or ""),
                "creative_intensive": bool(desc.get("creative_intensive")),
                "revision": 1,
            })
        return extras

    @staticmethod
    def _materialize_extras(extras: list[dict]) -> dict:
        class _EmptyPort:
            def list_artifacts(self):  # noqa: D401 - read-only port shape
                return []

        source = acs.ArtifactContinuationSource(
            _EmptyPort(),
            subject_id=ag0.GLOBAL_SUBJECT_ID,
            extra_opportunity_provider=lambda: list(extras),
        )
        records, _snap, _meta = source.materialize(observed_at=T_LATE)
        craft = next(
            record for record in records
            if str(record.source_ref).endswith("home_craft-card_organizer_box")
        )
        return {"craft": craft, "records": records}

    @staticmethod
    def _acute_body_kwargs() -> tuple[Path, dict]:
        root = Path(tempfile.mkdtemp(prefix="v08-health-body-"))
        state = br.initial_runtime(at="2026-10-15T00:00:00+00:00")
        moment = datetime(2026, 10, 15, 0, 0, tzinfo=timezone.utc)
        for _ in range(3):
            report = br.advance(
                state,
                moment.isoformat(),
                (moment + timedelta(hours=1)).isoformat(),
                {"class": "PHYSICAL_RESULT", "movement_duration_seconds": 3600,
                 "effort": "heavy", "pose": "standing"},
            )
            state = report["state"]
            moment += timedelta(hours=1)
        body_dir = root / "data" / "body"
        br.BodyStore(body_dir).save_runtime(state)
        record = bhp.admit_exposure(
            bhp.initial_record(body_id=br.BODY_ID, subject_id="self", home_ref="", at=moment),
            episode_id="ep:wave2",
            at=moment,
            origin="world_exposure",
            severity=0.6,
        )
        bhp.HealthStore(body_dir).save(record)
        (root / "phone").mkdir(parents=True, exist_ok=True)
        (root / "phone" / "v03_ecology.json").write_text("{}", encoding="utf8")
        shim = NS(body_state=state, health_state=record, _now=moment, _clock=None,
                  config=NS(world_home=root))
        shim._moment = types.MethodType(WorldBodyService._moment, shim)
        shim.body_summary = types.MethodType(WorldBodyService.body_summary, shim)
        shim.health_record = types.MethodType(WorldBodyService.health_record, shim)

        class _Port:
            def body(self):
                return {
                    "field": "body", "outcome": "OK", "degraded": False,
                    "data": GatewaySurface(shim).handle("get_body_signals")["result"],
                }

        now_iso = "2026-10-15T09:00:00+00:00"
        view = lsv.read_life_situation(
            home=root,
            body_port=_Port(),
            now=now_iso,
            budget={"source": "life_request_budget.check", "allowed": True,
                    "state": "ok", "utc_day": "2026-10-15", "utc_day_calls": 0},
        )
        assert view["body"]["creative_block"] is True, view["body"]
        cycle = alc.AutonomousLifeCycle(
            NS(state_root=Path(tempfile.mkdtemp(prefix="v08-cyc-"))), enabled=True, env={}
        )
        return root, cycle.capacity_kwargs(view)

    @staticmethod
    def _evaluate_craft_rejected(materialized: dict, body_kwargs: dict) -> bool:
        harness = abandon_fixture._Harness()
        try:
            candidates = list(materialized["records"])
            candidate_set = ag0.CandidateSet(
                candidate_set_id="cset:wave2",
                subject_id=ag0.GLOBAL_SUBJECT_ID,
                candidate_refs=[c.candidate_id for c in candidates],
                source_snapshot_refs=["snap:wave2"],
                observed_at=T_LATE, built_at=T_LATE,
            )
            harness.stub.raw_output = {
                "decision": ag1.DECISION_NO_ACTION,
                "selected_candidate_id": None,
                "reason_codes": [ag1.REASON_NO_ACTION_CHOSEN],
                "short_rationale": "今天先不勉强动手作。",
                "deferred_candidate_ids": [],
            }
            opportunity = ag1.DecisionOpportunity(
                opportunity_id="dopp:wave2",
                trigger_kind="EXPLICIT_REEVALUATION",
                trigger_ref="evt:wave2",
                candidate_set_ref=candidate_set.candidate_set_id,
                occurred_at=T_LATE, observed_at=T_LATE, recorded_at=T_LATE,
                causal_parent_refs=[candidate_set.candidate_set_id],
                idempotency_key="idem:wave2",
                current_activity_ref=None,
                status=ag1.OPPORTUNITY_STATUS_OPEN,
            )
            call = {
                "device_availability_state": "AVAILABLE",
                "resource_availability_state": "AVAILABLE",
                "privacy_availability_state": "AVAILABLE",
                "runtime_load_state": "AVAILABLE",
            }
            call.update(body_kwargs)
            result = harness.ag1_service.evaluate_opportunity(
                opportunity=opportunity,
                candidate_set=candidate_set,
                candidates=candidates,
                **call,
            )
            craft_id = materialized["craft"].candidate_id
            return any(
                row.candidate_ref == craft_id
                and "HARD_REJECT:BODY_CREATIVE_BLOCK" in (row.reason_codes or [])
                for row in result.get("constraint_results") or ()
            )
        finally:
            harness.close()


if __name__ == "__main__":
    unittest.main()

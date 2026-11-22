#!/usr/bin/env python3
"""Stage 2 verification suite: Pillar 2 Unified Full-Spectrum Desire Engine
(Ch. 35 + Ch. 130 2+2+1 bounded lifecycle, natural decay & real TTL sweep,
deterministic zero-LLM autonomous emergence) + nyairo_day.py heartbeat
desire ticker & realistic 45-minute damp-coat rain strain trigger.

All tests run 100% offline with llm_calls == 0 and strict zero-write GET/uninit checks.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unicodedata
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(REPO_ROOT),
    str(REPO_ROOT / "components"),
    str(REPO_ROOT / "components" / "life" / "scripts"),
    str(REPO_ROOT / "components" / "world" / "scripts"),
    str(REPO_ROOT / "components" / "phone"),
    str(REPO_ROOT / "scripts"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import body_lived_needs as bln  # noqa: E402
import body_runtime as br  # noqa: E402
import nyairo_day  # noqa: E402
from components.life.scripts import everyday_desire_lifecycle as edl  # noqa: E402
from components.life.scripts import v05_dreams_desires_foreshadow as v05_spark  # noqa: E402
from components.world.scripts import v03_economy_illness_bridge as v03_eco  # noqa: E402
from components.world.scripts import v04_neighborhood_living as v04_nb  # noqa: E402


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int]]:
    if not root.exists():
        return {}
    snap: dict[str, tuple[int, int]] = {}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root)).replace("\\", "/")
        try:
            st = p.lstat()
            snap[rel] = (int(st.st_size), int(st.st_mtime_ns))
        except OSError:
            snap[rel] = (-1, -1)
    return snap


def _seed_managed_body_runtime(
    root: Path,
    *,
    at_iso: str,
    fatigue: float = 0.15,
    warmth: float = 0.50,
    hunger: float = 0.0,
    thirst: float = 0.0,
    toilet: float = 0.0,
    cold_tendency_latch: bool = False,
    sleeping: bool = False,
) -> Path:
    world_home = root / "one-day" / "world-home"
    body_dir = world_home / "data" / "body"
    body_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        root / "phone" / "instance.json",
        {"world_home": str(world_home), "subject": "chiyo"},
    )
    prof = bln.build_profile(hunger=hunger, thirst=thirst, toilet=toilet)
    ln_state = bln.initial_state(prof)
    ln_state["last_live_at"] = at_iso
    rt = br.initial_runtime(at=at_iso, lived_needs=ln_state)
    rt["somatic"]["fatigue"] = float(fatigue)
    rt["somatic"]["warmth"] = float(warmth)
    if cold_tendency_latch:
        rt["signal_latch"]["cold_tendency"] = True
    if sleeping:
        rt["recovery"] = {
            "state": "active",
            "started_at": at_iso,
            "last_advanced_at": at_iso,
            "mode": "sleep_like",
            "progress": 0.25,
            "trend": "improving",
        }
    br.BodyStore(body_dir).save_runtime(rt)
    return body_dir / br.RUNTIME_NAME


class Stage2FullSpectrumDesiresTest(unittest.TestCase):
    def _tmp_dir(self, prefix: str) -> Path:
        tmp = Path(tempfile.mkdtemp(prefix=prefix))
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        return tmp

    def test_01_canonical_schema_backward_compat_and_2_2_1_cap_eviction_zero_debt(self) -> None:
        root = self._tmp_dir("s2-cap-")
        t0 = "2026-11-20T09:00:00+08:00"

        # 1. Legacy caller gets canonical optional defaults cleanly
        r0 = v05_spark.record_subject_desire(
            root,
            layer="micro_craving",
            text="想喝一杯热焙茶",
            now_iso=t0,
            actor_side="subject",
        )
        d0 = r0["desire"]
        self.assertEqual(d0["category"], "curiosity_mystery")
        self.assertEqual(d0["origin"], "self_emerged")
        self.assertEqual(d0["intensity"], 0.65)
        self.assertEqual(d0["decay_per_hour"], 0.18)
        self.assertEqual(d0["friction_count"], 0)
        self.assertEqual(d0["estimated_cost_jpy"], 0)
        self.assertFalse(d0["requires_outing"])
        self.assertEqual(d0["status"], "active")
        self.assertTrue(d0["shared"])
        self.assertEqual(d0["frustration_debt"], 0)
        self.assertEqual(r0["llm_calls"], 0)

        # 2. micro_craving 2-slot cap: add 0.45 (older low) and then 0.80 -> 0.45 evicted with zero debt
        r1 = v05_spark.record_subject_desire(
            root,
            layer="micro_craving",
            text="想吃一块海盐曲奇",
            category="appetite_craving",
            origin="manual",
            world_anchor="manual:cookie",
            intensity=0.45,
            estimated_cost_jpy=320,
            requires_outing=True,
            now_iso="2026-11-20T09:05:00+08:00",
            actor_side="subject",
        )
        self.assertEqual(r1["evicted_desires"], [])
        self.assertEqual(r1["active_counts"]["micro_craving"], 2)

        r2 = v05_spark.record_subject_desire(
            root,
            layer="micro_craving",
            text="想在窗边靠一会儿听雨",
            category="cozy_comfort",
            origin="world_spark",
            world_anchor="weather:rain:window",
            intensity=0.80,
            now_iso="2026-11-20T09:10:00+08:00",
            actor_side="subject",
        )
        self.assertEqual(len(r2["evicted_desires"]), 1)
        evicted_micro = r2["evicted_desires"][0]
        self.assertEqual(evicted_micro["desire_id"], r1["desire"]["desire_id"])
        self.assertEqual(evicted_micro["status"], "faded")
        self.assertEqual(evicted_micro["resolution_note"], "自然淡出，不留心理欠账")
        self.assertEqual(evicted_micro["frustration_debt"], 0)
        self.assertEqual(r2["active_counts"]["micro_craving"], 2)

        # 3. mid_itch 2-slot cap: 3 equal-intensity items -> oldest created_at evicted
        m1 = v05_spark.record_subject_desire(
            root,
            layer="mid_itch",
            text="想去旧书铺看看新到的明信片",
            category="curiosity_mystery",
            intensity=0.65,
            now_iso="2026-11-20T09:15:00+08:00",
            actor_side="subject",
        )
        v05_spark.record_subject_desire(
            root,
            layer="mid_itch",
            text="想把河边散步路线画成小图",
            category="craft_ambition",
            intensity=0.65,
            now_iso="2026-11-20T09:20:00+08:00",
            actor_side="subject",
        )
        m3 = v05_spark.record_subject_desire(
            root,
            layer="mid_itch",
            text="想顺路去跟林姨打个招呼",
            category="social_longing",
            intensity=0.65,
            now_iso="2026-11-20T09:25:00+08:00",
            actor_side="subject",
        )
        self.assertEqual(len(m3["evicted_desires"]), 1)
        self.assertEqual(m3["evicted_desires"][0]["desire_id"], m1["desire"]["desire_id"])
        self.assertEqual(m3["evicted_desires"][0]["resolution_note"], "自然淡出，不留心理欠账")
        self.assertEqual(m3["active_counts"]["mid_itch"], 2)

        # 4. long_aspiration 1-slot cap + private intimate_warmth isolation
        l1 = v05_spark.record_subject_desire(
            root,
            layer="long_aspiration",
            text="把这一季的草木观察整理成一本小册子",
            category="craft_ambition",
            intensity=0.70,
            now_iso="2026-11-20T09:30:00+08:00",
            actor_side="subject",
        )
        l2 = v05_spark.record_subject_desire(
            root,
            layer="long_aspiration",
            text="只在安静夜晚留给彼此的小心意",
            category="intimate_warmth",
            shared=True,  # forced private by intimate_warmth boundary
            intensity=0.75,
            now_iso="2026-11-20T09:35:00+08:00",
            actor_side="subject",
        )
        self.assertFalse(l2["desire"]["shared"])
        self.assertEqual(len(l2["evicted_desires"]), 1)
        self.assertEqual(l2["evicted_desires"][0]["desire_id"], l1["desire"]["desire_id"])
        self.assertEqual(l2["active_counts"], {"micro_craving": 2, "mid_itch": 2, "long_aspiration": 1})

        # Partner projection MUST NOT leak private intimate_warmth desire (zero-write GET)
        before_get = _snapshot_tree(root)
        partner_view = v05_spark.project_v05_inner_spark_state(
            root, has_subject_capability=False, now_iso="2026-11-20T09:36:00+08:00"
        )
        self.assertEqual(_snapshot_tree(root), before_get)
        self.assertNotIn("只在安静夜晚", json.dumps(partner_view, ensure_ascii=False))

        # Friction counter increments without frustration debt
        fric = v05_spark.record_desire_friction(
            root,
            desire_id=m3["desire"]["desire_id"],
            friction_reason="weather_rain",
            now_iso="2026-11-20T09:40:00+08:00",
        )
        self.assertTrue(fric["ok"])
        self.assertEqual(fric["friction_count"], 1)
        self.assertEqual(fric["frustration_debt"], 0)

    def test_02_decay_and_real_ttl_sweep_across_both_desire_stores(self) -> None:
        root = self._tmp_dir("s2-decay-")
        t0 = "2026-11-20T08:00:00+08:00"

        # Zero-write check on empty root
        before_empty = _snapshot_tree(root)
        noop = v05_spark.tick_desire_lifecycle(root, now_iso=t0)
        self.assertTrue(noop["ok"])
        self.assertFalse(noop["changed"])
        self.assertEqual(_snapshot_tree(root), before_empty)

        # Seed everyday_desires.json using REAL schema (created_at + ttl_seconds, NO expires_at!)
        edl.apply_everyday_desire_action(
            root,
            {
                "action": "emerge_concern",
                "domain": "comfort",
                "origin_kind": "body_need",
                "origin_refs": ["窗边微凉"],
                "expression": "想顺手倒半杯温水",
                "persistence": "turn_only",
                "ttl_seconds": 600,
            },
            now_iso=t0,
        )
        # Seed one structured retained concern whose material source will vanish + one stale retained concern
        _write_json(
            root / "phone" / "v03_encounters.json",
            {"shared_materials": [{"material_id": "mat_01", "title_cn": "借来的小册", "status": "OPENED_PARTIAL"}]},
        )
        edl.apply_everyday_desire_action(
            root,
            {
                "action": "emerge_concern",
                "domain": "creation",
                "origin_kind": "sustained_interest",
                "origin_refs": ["material:mat_01"],
                "expression": "想把小册里那段抄下来",
                "persistence": "retained",
            },
            now_iso=t0,
        )
        # Withdraw material -> source-lost!
        _write_json(
            root / "phone" / "v03_encounters.json",
            {"shared_materials": [{"material_id": "mat_01", "title_cn": "借来的小册", "status": "WITHDRAWN"}]},
        )

        # Seed v05_inner_spark desires (1 micro_craving decaying 0.18/h, 1 mid_itch with explicit expires_at)
        micro = v05_spark.record_subject_desire(
            root,
            layer="micro_craving",
            text="想烤一片黄油吐司",
            category="appetite_craving",
            intensity=0.65,
            decay_per_hour=0.18,
            now_iso=t0,
            actor_side="subject",
        )["desire"]
        exp_mid = v05_spark.record_subject_desire(
            root,
            layer="mid_itch",
            text="想在午后去邮筒寄张明信片",
            category="restlessness_outing",
            intensity=0.70,
            expires_at="2026-11-20T09:30:00+08:00",
            now_iso=t0,
            actor_side="subject",
        )["desire"]

        # Tick +2h (10:00):
        # - everyday_desires turn_only_buffer (ttl_seconds=600) expires via real created_at+ttl_seconds!
        # - everyday_desires source-lost retained_concern subsides with zero debt!
        # - v05 micro_craving decays: 0.65 - 0.18 * 2 = 0.29 (still active)
        # - v05 mid_itch past expires_at (09:30) fades!
        tick_2h = v05_spark.tick_desire_lifecycle(root, now_iso="2026-11-20T10:00:00+08:00")
        self.assertTrue(tick_2h["changed"])
        self.assertEqual(tick_2h["frustration_debt"], 0)
        self.assertEqual(tick_2h["llm_calls"], 0)
        self.assertIn(micro["desire_id"], tick_2h["spark_decayed_ids"])
        self.assertIn(exp_mid["desire_id"], tick_2h["spark_faded_ids"])
        self.assertEqual(tick_2h["everyday_desires_receipt"]["turn_only_faded_count"], 1)
        self.assertEqual(tick_2h["everyday_desires_receipt"]["source_lost_count"], 1)

        ed_after_2h, ed_err = edl.read_everyday_desire_state(root)
        self.assertIsNone(ed_err)
        self.assertEqual(ed_after_2h["turn_only_buffer"], [])
        native_retained = [r for r in ed_after_2h["retained_concerns"] if r.get("synced_from") != "v05_inner_spark"]
        self.assertEqual(native_retained, [])
        # Synced active_desires reflects only the still-active micro_craving
        self.assertEqual([d["desire_id"] for d in ed_after_2h["active_desires"]], [micro["desire_id"]])
        self.assertAlmostEqual(ed_after_2h["active_desires"][0]["intensity"], 0.29, places=3)

        # Tick +2h more (12:00): micro_craving decays 0.29 - 0.36 -> 0.0 <= 0.08 -> naturally fades!
        tick_4h = v05_spark.tick_desire_lifecycle(root, now_iso="2026-11-20T12:00:00+08:00")
        self.assertTrue(tick_4h["changed"])
        self.assertIn(micro["desire_id"], tick_4h["spark_faded_ids"])
        self.assertEqual(tick_4h["active_counts"], {"micro_craving": 0, "mid_itch": 0, "long_aspiration": 0})

        spark_doc = json.loads((root / "life" / "v05_inner_spark.json").read_text(encoding="utf-8"))
        by_id = {d["desire_id"]: d for d in spark_doc["desires"]}
        self.assertEqual(by_id[micro["desire_id"]]["status"], "faded")
        self.assertEqual(by_id[micro["desire_id"]]["resolution_note"], "自然淡出，不留心理欠账")

        ed_after_4h, _ = edl.read_everyday_desire_state(root)
        self.assertEqual(ed_after_4h["active_desires"], [])

        # Repeating tick at 12:05 with nothing left active -> zero disk writes!
        before_noop = _snapshot_tree(root)
        tick_noop = v05_spark.tick_desire_lifecycle(root, now_iso="2026-11-20T12:05:00+08:00")
        self.assertFalse(tick_noop["changed"])
        self.assertEqual(_snapshot_tree(root), before_noop)

    def test_03_autonomous_full_spectrum_emergence_no_flood_and_coherent_sync(self) -> None:
        # 1. Calm empty root -> 0 emerged, 0 writes
        calm_root = self._tmp_dir("s2-emerge-calm-")
        res_calm = v05_spark.emerge_autonomous_desires(calm_root, now_iso="2026-11-20T14:00:00+08:00")
        self.assertFalse(res_calm["changed"])
        self.assertEqual(res_calm["emerged"], [])
        self.assertEqual(_snapshot_tree(calm_root), {})

        # 2. Sleeping TA -> suppressed, 0 writes
        sleep_root = self._tmp_dir("s2-emerge-sleep-")
        _seed_managed_body_runtime(
            sleep_root,
            at_iso="2026-11-20T02:00:00+08:00",
            hunger=0.80,
            thirst=0.75,
            sleeping=True,
        )
        res_sleep = v05_spark.emerge_autonomous_desires(sleep_root, now_iso="2026-11-20T02:05:00+08:00")
        self.assertFalse(res_sleep["changed"])
        self.assertEqual(res_sleep["reason"], "sleeping_suppressed")
        self.assertFalse((sleep_root / "life" / "v05_inner_spark.json").exists())

        # 3. Rich live state (hunger + rain + unfinished v04 project + town bookbox + social silence + S07 boredom)
        root = self._tmp_dir("s2-emerge-rich-")
        t0 = "2026-11-20T15:00:00+08:00"
        _seed_managed_body_runtime(
            root,
            at_iso=t0,
            fatigue=0.35,
            warmth=0.32,
            hunger=0.78,
            thirst=0.62,
        )
        nb_state = v04_nb.default_state()
        nb_state["environment"]["weather"] = "drizzle"
        nb_state["environment"]["updated_at"] = t0
        _write_json(root / "world" / "v04_neighborhood_state.json", nb_state)
        _write_json(
            root / "phone" / "v04_elastic_play.json",
            {
                "personal_projects": [
                    {
                        "project_id": "proj_rain_zine",
                        "status": "active",
                        "purpose_cn": "雨天街角速写小册",
                        "next_step_cn": "补画旧书铺屋檐水滴",
                        "entries": [{"a": 1}, {"a": 2}],
                    }
                ],
                "temporary_game": {"active": True, "game_kind": "五子棋快局"},
            },
        )
        _write_json(
            root / "world" / "v05_living_town.json",
            {
                "revision": 3,
                "drifting_bookbox": [
                    {
                        "book_id": "book_rain_map",
                        "book_title": "海堤灯塔手账",
                        "status": "IN_BOX",
                        "left_by_side": "partner",
                        "margin_notes": [{"note_cn": "夹了张干草书签"}],
                    }
                ],
                "bulletin_followups": [],
                "regulars": {},
                "alley_kitten": {"feeding_count": 0, "greeting_count": 0},
            },
        )
        _write_json(
            root / "phone" / "autonomy_rhythm.json",
            {"last_partner_message_at": "2026-11-19T12:00:00+08:00"},
        )
        _write_json(
            root / "life" / "s07_affect_records.json",
            {
                "records": [
                    {
                        "record_id": "aff_bored_01",
                        "kind": "mood_shift",
                        "intensity": 65,
                        "note": "轻度发闷有点坐不住",
                        "expires_at": "2026-11-20T22:00:00+08:00",
                    }
                ]
            },
        )
        # Seed existing everyday_desires.json with one native concern to verify coherent non-destructive sync
        edl.apply_everyday_desire_action(
            root,
            {
                "action": "emerge_concern",
                "domain": "exploration",
                "origin_kind": "immediate_opportunity",
                "origin_refs": ["窗台薄荷"],
                "expression": "想看看窗台薄荷有没有冒新芽",
                "persistence": "retained",
            },
            now_iso=t0,
        )

        em1 = v05_spark.emerge_autonomous_desires(root, now_iso=t0)
        self.assertTrue(em1["changed"])
        self.assertEqual(em1["llm_calls"], 0)
        self.assertGreaterEqual(em1["emerged_count"], 3)
        # Strictly bounded by 2+2+1 active cap!
        self.assertLessEqual(em1["active_counts"]["micro_craving"], 2)
        self.assertLessEqual(em1["active_counts"]["mid_itch"], 2)
        self.assertLessEqual(em1["active_counts"]["long_aspiration"], 1)
        # All emerged desires are non-sexual!
        for d in em1["emerged"]:
            self.assertNotEqual(d["category"], "intimate_warmth")
            self.assertIn(d["category"], v05_spark.DESIRE_CATEGORIES)
            self.assertTrue(d["world_anchor"])

        cats = {d["category"] for d in em1["emerged"]}
        self.assertIn("appetite_craving", cats)
        self.assertIn("craft_ambition", cats)

        # Verify everyday_desires.json stayed coherent AND preserved native concern!
        ed_state, _ = edl.read_everyday_desire_state(root)
        self.assertEqual(len(ed_state["active_desires"]), sum(em1["active_counts"].values()))
        self.assertTrue(
            any("窗台薄荷" in str(c.get("expression") or "") for c in ed_state["retained_concerns"])
        )

        # Anti-flood & zero-churn: calling every 60s for 10 ticks writes ZERO bytes!
        snap_after_first = _snapshot_tree(root)
        t_base = datetime.fromisoformat(t0)
        for step in range(1, 11):
            step_iso = (t_base + timedelta(seconds=60 * step)).isoformat(timespec="seconds")
            em_repeat = v05_spark.emerge_autonomous_desires(root, now_iso=step_iso)
            self.assertFalse(em_repeat["changed"], f"unexpected churn at +{60 * step}s")
            self.assertEqual(em_repeat["emerged_count"], 0)
        self.assertEqual(_snapshot_tree(root), snap_after_first)

    def test_04_prolonged_indoor_and_boredom_and_social_silence_emergence(self) -> None:
        # Isolated test verifying prolonged indoor stay + clear weather -> restlessness_outing,
        # social silence -> social_longing, and S07 boredom -> restlessness_outing/curiosity_mystery
        root = self._tmp_dir("s2-emerge-indoor-")
        t0 = "2026-11-20T15:00:00+08:00"
        nb = v04_nb.default_state()
        nb["environment"]["weather"] = "clear"
        nb["journey"]["current_place_id"] = "home"
        nb["journey"]["state"] = "at_place"
        nb["journey"]["arrived_at"] = "2026-11-20T09:00:00+08:00"  # 6h indoors
        _write_json(root / "world" / "v04_neighborhood_state.json", nb)
        _write_json(
            root / "phone" / "autonomy_rhythm.json",
            {"last_partner_message_at": "2026-11-19T10:00:00+08:00"},  # 29h silence
        )
        em = v05_spark.emerge_autonomous_desires(root, now_iso=t0)
        self.assertTrue(em["changed"])
        cats = {d["category"] for d in em["emerged"]}
        self.assertIn("restlessness_outing", cats)
        self.assertIn("social_longing", cats)
        self.assertLessEqual(em["active_counts"]["mid_itch"], 2)

        # S07 mild boredom/curiosity on separate root -> curiosity_mystery when rainy
        root_aff = self._tmp_dir("s2-emerge-aff-")
        nb_rain = v04_nb.default_state()
        nb_rain["environment"]["weather"] = "rain"
        _write_json(root_aff / "world" / "v04_neighborhood_state.json", nb_rain)
        _write_json(
            root_aff / "life" / "s07_affect_records.json",
            {
                "records": [
                    {
                        "record_id": "aff_cur_01",
                        "kind": "drive",
                        "intensity": 55,
                        "note": "有点无聊想找点新鲜事琢磨",
                        "expires_at": "2026-11-20T22:00:00+08:00",
                    }
                ]
            },
        )
        em_aff = v05_spark.emerge_autonomous_desires(root_aff, now_iso=t0)
        self.assertTrue(em_aff["changed"])
        aff_cats = {d["category"] for d in em_aff["emerged"]}
        self.assertIn("cozy_comfort", aff_cats)
        self.assertIn("curiosity_mystery", aff_cats)

    def test_05_nyairo_day_heartbeat_desire_ticker_and_bare_uninitialized_zero_write(self) -> None:
        # 1. Bare unbootstrapped root -> skipped_uninitialized with 0 disk writes
        bare = self._tmp_dir("s2-bare-")
        before_bare = _snapshot_tree(bare)
        res_bare = nyairo_day.run_v03_background_clock_tick(
            bare, now_iso="2026-11-20T10:00:00+08:00", force_init=False
        )
        self.assertTrue(res_bare["ok"])
        self.assertEqual(res_bare["status"], "skipped_uninitialized")
        self.assertEqual(res_bare["slices_ticked"], [])
        self.assertEqual(_snapshot_tree(bare), before_bare)

        # 2. Bootstrapped root: heartbeat sweeps real created_at+ttl_seconds & emerges hunger desire on >=900s
        root = self._tmp_dir("s2-hb-desire-")
        t0 = "2026-11-20T10:00:00+08:00"
        nyairo_day.bootstrap_living_foundation_state(root, now_iso=t0)
        _seed_managed_body_runtime(root, at_iso=t0, hunger=0.75, thirst=0.20, warmth=0.50)

        edl.apply_everyday_desire_action(
            root,
            {
                "action": "emerge_concern",
                "domain": "comfort",
                "origin_kind": "body_need",
                "origin_refs": ["刚烧好的热水"],
                "expression": "想泡一小杯茶",
                "persistence": "turn_only",
                "ttl_seconds": 300,
            },
            now_iso=t0,
        )

        # Advance +20m (10:20 >= 900s) -> sweeps expired turn_only_buffer AND emerges appetite_craving!
        tick1 = nyairo_day.run_v03_background_clock_tick(
            root, now_iso="2026-11-20T10:20:00+08:00", force_init=False
        )
        self.assertTrue(tick1["ok"])
        self.assertEqual(tick1["llm_calls"], 0)
        self.assertIn("everyday_desires", tick1["slices_ticked"])
        d_rcpt = tick1["everyday_desires_receipt"]
        self.assertGreaterEqual(d_rcpt["faded_count"], 1)
        self.assertGreaterEqual(d_rcpt["emerged_count"], 1)

        ed_after, _ = edl.read_everyday_desire_state(root)
        self.assertEqual(ed_after["turn_only_buffer"], [])
        self.assertGreaterEqual(len(ed_after["active_desires"]), 1)

        # Follow-up 60s tick (10:21, <900s): ZERO churn on v05_inner_spark.json and everyday_desires.json!
        spark_mtime = (root / "life" / "v05_inner_spark.json").stat().st_mtime_ns
        ed_mtime = (root / "life" / "everyday_desires.json").stat().st_mtime_ns
        tick2 = nyairo_day.run_v03_background_clock_tick(
            root, now_iso="2026-11-20T10:21:00+08:00", force_init=False
        )
        self.assertNotIn("everyday_desires", tick2["slices_ticked"])
        self.assertEqual((root / "life" / "v05_inner_spark.json").stat().st_mtime_ns, spark_mtime)
        self.assertEqual((root / "life" / "everyday_desires.json").stat().st_mtime_ns, ed_mtime)

    def test_06_realistic_45min_damp_coat_rain_strain_and_legacy_compat(self) -> None:
        # Case A: Damp coat worn < 45m (20m) with fatigue=0.60 -> NO neglect strain yet (0 writes)
        root_a = self._tmp_dir("s2-rain-short-")
        t_wet = "2026-11-20T14:00:00+08:00"
        _seed_managed_body_runtime(root_a, at_iso=t_wet, fatigue=0.60, warmth=0.50)
        eco_state = v03_eco.default_ecology_state()
        eco_state["updated_at"] = t_wet
        _write_json(root_a / "phone" / "v03_ecology.json", eco_state)
        _write_json(
            root_a / "phone" / "somatic_comfort.json",
            {
                "schema_version": 1,
                "revision": 1,
                "updated_at": t_wet,
                "window": {"state": "closed", "updated_at": t_wet},
                "local_position": {"zone": None, "bound_place_id": None, "bound_area_id": None, "declared_at": None},
                "garment": {
                    "garment_id": "daily_coat",
                    "label": "日常外套",
                    "state": "wear",
                    "dryness": "damp",
                    "dampness_source": "verified_outdoor_rain_exposure",
                    "wetted_at": t_wet,
                    "hang_position": None,
                    "drying": None,
                },
                "care": {"drink": None, "cushion": None},
            },
        )
        eco_before = (root_a / "phone" / "v03_ecology.json").stat().st_mtime_ns
        rep_20m = nyairo_day._maybe_tick_v03_ecology(root_a, "2026-11-20T14:20:00+08:00")
        self.assertIsNone(rep_20m)
        self.assertEqual((root_a / "phone" / "v03_ecology.json").stat().st_mtime_ns, eco_before)

        # Advance to +50m (14:50 >= 2700s) with fatigue=0.60 >= 0.55 -> triggers caught_in_rain!
        rep_50m = nyairo_day._maybe_tick_v03_ecology(root_a, "2026-11-20T14:50:00+08:00")
        self.assertIsNotNone(rep_50m)
        assert rep_50m is not None
        self.assertTrue(rep_50m["neglect_strain_recorded"])
        self.assertEqual(rep_50m["llm_calls"], 0)
        eco_after, _ = v03_eco.read_stored_ecology_zero_write(root_a)
        self.assertGreater(float(eco_after["illness"]["strain_load"]), 0.0)
        self.assertEqual(eco_after["illness"]["strain_events"][-1]["event_kind"], "caught_in_rain")

        # 60s later (14:51): 30-min cooldown prevents disk-write spam
        rep_51m = nyairo_day._maybe_tick_v03_ecology(root_a, "2026-11-20T14:51:00+08:00")
        self.assertIsNone(rep_51m)

        # Case B: Cold warmth (<= 0.35) + damp coat >= 45m (even with low fatigue 0.20) -> triggers caught_in_rain!
        root_b = self._tmp_dir("s2-rain-cold-")
        _seed_managed_body_runtime(root_b, at_iso=t_wet, fatigue=0.20, warmth=0.30)
        _write_json(root_b / "phone" / "v03_ecology.json", eco_state)
        _write_json(
            root_b / "phone" / "somatic_comfort.json",
            {
                "schema_version": 1,
                "revision": 1,
                "updated_at": t_wet,
                "garment": {
                    "garment_id": "daily_coat",
                    "state": "wear",
                    "dryness": "damp",
                    "wetted_at": t_wet,
                },
            },
        )
        rep_cold = nyairo_day._maybe_tick_v03_ecology(root_b, "2026-11-20T14:46:00+08:00")
        self.assertIsNotNone(rep_cold)
        assert rep_cold is not None
        self.assertTrue(rep_cold["neglect_strain_recorded"])

        # Case C: Wet coat (dryness="wet") >= 45m even with calm fatigue=0.20 & warmth=0.50 -> triggers caught_in_rain!
        root_c = self._tmp_dir("s2-rain-wet-")
        _seed_managed_body_runtime(root_c, at_iso=t_wet, fatigue=0.20, warmth=0.50)
        _write_json(root_c / "phone" / "v03_ecology.json", eco_state)
        _write_json(
            root_c / "phone" / "somatic_comfort.json",
            {
                "schema_version": 1,
                "revision": 1,
                "updated_at": t_wet,
                "garment": {
                    "garment_id": "daily_coat",
                    "state": "wear",
                    "dryness": "wet",
                    "wetted_at": t_wet,
                },
            },
        )
        rep_wet = nyairo_day._maybe_tick_v03_ecology(root_c, "2026-11-20T14:45:00+08:00")
        self.assertIsNotNone(rep_wet)
        assert rep_wet is not None
        self.assertTrue(rep_wet["neglect_strain_recorded"])

        # Case D: Legacy compatibility (no wetted_at, damp_outerwear_worn=True + body_fatigue >= 0.82) -> triggers!
        root_d = self._tmp_dir("s2-rain-legacy-")
        _seed_managed_body_runtime(root_d, at_iso=t_wet, fatigue=0.85, warmth=0.50)
        _write_json(root_d / "phone" / "v03_ecology.json", eco_state)
        _write_json(
            root_d / "phone" / "somatic_comfort.json",
            {
                "schema_version": 1,
                "revision": 1,
                "updated_at": t_wet,
                "window": {"state": "closed", "updated_at": t_wet},
                "local_position": {"zone": None, "bound_place_id": None, "bound_area_id": None, "declared_at": None},
                "garment": {
                    "garment_id": "daily_coat",
                    "label": "日常外套",
                    "state": "wear",
                    "dryness": "damp",
                    "dampness_source": "self_declared_simulation",
                    "hang_position": None,
                    "drying": None,
                },
                "care": {"drink": None, "cushion": None},
            },
        )
        rep_legacy = nyairo_day._maybe_tick_v03_ecology(root_d, "2026-11-20T14:05:00+08:00")
        self.assertIsNotNone(rep_legacy)
        assert rep_legacy is not None
        self.assertTrue(rep_legacy["neglect_strain_recorded"])

    def test_07_file_hygiene_lf_no_bom_no_unicode_so(self) -> None:
        owned_files = (
            REPO_ROOT / "components" / "life" / "scripts" / "everyday_desire_lifecycle.py",
            REPO_ROOT / "components" / "life" / "scripts" / "v05_dreams_desires_foreshadow.py",
            REPO_ROOT / "scripts" / "nyairo_day.py",
            REPO_ROOT / "tests" / "test_stage2_full_spectrum_desires.py",
        )
        for fpath in owned_files:
            raw = fpath.read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), f"BOM found in {fpath.name}")
            self.assertNotIn(b"\r", raw, f"CR/CRLF found in {fpath.name}")
            text = raw.decode("utf-8")
            so_chars = [ch for ch in text if unicodedata.category(ch) == "So"]
            self.assertEqual(so_chars, [], f"unicode.So chars in {fpath.name}: {so_chars[:5]}")


if __name__ == "__main__":
    unittest.main()

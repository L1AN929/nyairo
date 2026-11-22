#!/usr/bin/env python3
"""Targeted regression suite for v0.8 Backend Living-Sense & Context Quality fixes.

Covers:
1. [P1-3] Local UTC+8 hour conversion in v05_dreams_desires_foreshadow (UTC 00:27 -> 08:27 daytime).
2. [P1-1] 10-thread concurrent v05_living_town_engine.post_bulletin_followup locking (10/10 persisted, 0 lost updates) + zero-write reads/declines.
3. [P1-5] Clear dry 09:49 AM town regulars + weather-coherent window soundscape + +08:00 timezone alignment (preserving explicit Asia/Tokyo overrides).
4. [P2-2] Post-Lidong (2026-11-21) solar-term vs season coherence (winter, zero autumn contradiction).
5. [P1-4 & P2-1] lived_context_bridge Chinese enum translations, clear weather surfacing, clean ellipsis truncation, desire deduplication, Calm Gate & <= 1800 cap.
6. File hygiene (UTF-8 no BOM, pure LF, zero unicode.So, zero gendered pronouns or hardcoded names).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import sys
import tempfile
import threading
import unicodedata
import unittest
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (
    REPO_ROOT,
    REPO_ROOT / "components",
    REPO_ROOT / "components" / "life" / "scripts",
    REPO_ROOT / "components" / "world" / "scripts",
    REPO_ROOT / "components" / "phone",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import body_lived_needs as bln  # noqa: E402
import body_runtime as br  # noqa: E402
from chiyo_bundle import lived_context_bridge as lcb  # noqa: E402
from components.life.scripts import v05_dreams_desires_foreshadow as dd_engine  # noqa: E402
from components.world.scripts import v04_neighborhood_living as nb_engine  # noqa: E402
from components.world.scripts import v05_living_town_engine as lt_engine  # noqa: E402
from components.world.scripts import v05_season_solar_pulse as ssp  # noqa: E402
from components.world.scripts import world_ambient_events as wae  # noqa: E402


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int, str]]:
    snap: dict[str, tuple[int, int, str]] = {}
    if not root.exists():
        return snap
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        st = p.lstat()
        if p.is_file() and not p.is_symlink():
            snap[rel] = (int(st.st_size), int(st.st_mtime_ns), hashlib.sha256(p.read_bytes()).hexdigest())
        else:
            snap[rel] = (0, int(st.st_mtime_ns), "dir")
    return snap


def _seed_body_runtime(root: Path, *, at_iso: str, hunger: float = 0.88, warmth: float = 0.50) -> None:
    world_home = root / "one-day" / "world-home"
    body_dir = world_home / "data" / "body"
    body_dir.mkdir(parents=True, exist_ok=True)
    _write_json(root / "phone" / "instance.json", {"world_home": str(world_home), "subject": "resident"})
    prof = bln.build_profile(hunger=hunger, thirst=0.15, toilet=0.10)
    ln_state = bln.initial_state(prof)
    ln_state["last_live_at"] = at_iso
    rt = br.initial_runtime(at=at_iso, lived_needs=ln_state)
    rt["somatic"]["fatigue"] = 0.20
    rt["somatic"]["warmth"] = float(warmth)
    br.BodyStore(body_dir).save_runtime(rt)


class TestV08LiveSenseAndContextFixes(unittest.TestCase):
    def test_01_utc_timestamp_resolves_to_local_utc8_hour_for_desire_emergence(self) -> None:
        # 2026-11-21T00:27:25+00:00 == 2026-11-21T08:27:25+08:00 (morning, hour=8!)
        utc_morning = "2026-11-21T00:27:25+00:00"
        dt_resolved, _ = dd_engine._resolve_now_dt(utc_morning, strict=True)
        self.assertEqual(dt_resolved.hour, 8)
        self.assertEqual(dt_resolved.tzinfo, dd_engine.DEFAULT_TZ)

        with tempfile.TemporaryDirectory(prefix="v08-tz-desire-") as td:
            root = Path(td)
            _seed_body_runtime(root, at_iso=utc_morning, hunger=0.78, warmth=0.50)
            nb = nb_engine.default_state()
            nb["environment"]["weather"] = "clear"
            _write_json(root / "world" / "v04_neighborhood_state.json", nb)

            em = dd_engine.emerge_autonomous_desires(root, now_iso=utc_morning)
            self.assertTrue(em["changed"])
            texts = [str(d.get("text") or "") for d in em["emerged"]]
            self.assertTrue(any("拐角面包铺" in t for t in texts), texts)
            for t in texts:
                self.assertNotIn("夜里肚子有点饿", t)

            # Direct candidate builder with raw UTC datetime also converts to UTC+8
            direct_cands = dd_engine._build_autonomous_desire_candidates(
                {"hunger": 0.78, "thirst": 0.1, "warmth": 0.5, "weather": "clear", "is_rainy": False},
                datetime(2026, 11, 21, 0, 27, 25, tzinfo=timezone.utc),
            )
            self.assertTrue(any("拐角面包铺" in c["text"] for c in direct_cands))
            self.assertFalse(any("夜里肚子有点饿" in c["text"] for c in direct_cands))

            # Genuine local night (15:30Z == 23:30+08:00) still emits night snack desire
            night_cands = dd_engine._build_autonomous_desire_candidates(
                {"hunger": 0.78, "thirst": 0.1, "warmth": 0.5, "weather": "clear", "is_rainy": False},
                datetime(2026, 11, 21, 15, 30, 0, tzinfo=timezone.utc),
            )
            self.assertTrue(any("夜里肚子有点饿" in c["text"] for c in night_cands))

    def test_02_concurrent_10_thread_bulletin_followups_persist_10_of_10(self) -> None:
        # Case A: 10 concurrent distinct notices -> 10/10 notices persisted & revision == 10
        with tempfile.TemporaryDirectory(prefix="v08-town-conc-dist-") as td:
            root = Path(td)
            barrier = threading.Barrier(10)

            def _post_distinct(idx: int) -> dict[str, Any]:
                barrier.wait(timeout=10.0)
                return lt_engine.post_bulletin_followup(
                    root,
                    notice_id=f"notice_conc_{idx:02d}",
                    title_cn=f"并行布告 #{idx:02d}",
                    followup_cn=f"并发后续记录 #{idx:02d}",
                    operation_key=f"op_conc_dist_{idx:02d}",
                    now_iso="2026-11-21T09:49:23+08:00",
                )

            with ThreadPoolExecutor(max_workers=10) as pool:
                results = list(pool.map(_post_distinct, range(10)))
            self.assertEqual(len(results), 10)
            self.assertTrue(all(r.get("ok") for r in results))

            snap = lt_engine.read_living_town_snapshot(root, now_iso="2026-11-21T09:49:23+08:00")
            self.assertEqual(snap["revision"], 10)
            persisted_ids = {row["notice_id"] for row in snap["bulletin_followups"]}
            for idx in range(10):
                self.assertIn(f"notice_conc_{idx:02d}", persisted_ids)

        # Case B: 10 concurrent posts on the same notice_id -> revision == 10 (0 lost updates) & 6 latest unique followups
        with tempfile.TemporaryDirectory(prefix="v08-town-conc-same-") as td:
            root = Path(td)
            barrier = threading.Barrier(10)

            def _post_same(idx: int) -> dict[str, Any]:
                barrier.wait(timeout=10.0)
                return lt_engine.post_bulletin_followup(
                    root,
                    notice_id="notice_alley_cat",
                    followup_cn=f"同帖并发后续 #{idx:02d}",
                    operation_key=f"op_conc_same_{idx:02d}",
                    now_iso="2026-11-21T09:49:23+08:00",
                )

            with ThreadPoolExecutor(max_workers=10) as pool:
                results = list(pool.map(_post_same, range(10)))
            self.assertEqual(len(results), 10)
            self.assertTrue(all(r.get("ok") for r in results))

            snap = lt_engine.read_living_town_snapshot(root, now_iso="2026-11-21T09:49:23+08:00")
            self.assertEqual(snap["revision"], 10)
            alley = next(n for n in snap["bulletin_followups"] if n["notice_id"] == "notice_alley_cat")
            self.assertEqual(len(alley["followups"]), lt_engine.MAX_BULLETIN_FOLLOWUPS)
            followup_ids = {f["followup_id"] for f in alley["followups"]}
            self.assertEqual(len(followup_ids), lt_engine.MAX_BULLETIN_FOLLOWUPS)

        # Zero-write check on empty root read + declined invite
        with tempfile.TemporaryDirectory(prefix="v08-town-zw-") as td:
            root = Path(td)
            before = _snapshot_tree(root)
            snap = lt_engine.read_living_town_snapshot(root, now_iso="2026-11-21T09:49:23+08:00")
            self.assertTrue(snap["zero_write"])
            self.assertEqual(_snapshot_tree(root), before)

    def test_03_clear_dry_0949_regulars_and_window_soundscape_coherent_with_utc8(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v08-coherent-") as td:
            root = Path(td)
            nb = nb_engine.default_state()
            nb["environment"]["weather"] = "clear"
            nb["environment"]["airflow"] = "strong"
            nb["environment"]["ground_by_place"] = {"yard": "dry", "street_corner": "dry"}
            _write_json(root / "world" / "v04_neighborhood_state.json", nb)

            before = _snapshot_tree(root)
            snap = lt_engine.read_living_town_snapshot(root, now_iso="2026-11-21T09:49:23+08:00")
            self.assertEqual(_snapshot_tree(root), before)
            self.assertEqual(snap["world_context"]["weather"], "clear")
            self.assertEqual(snap["world_context"]["hour"], 9)
            self.assertEqual(snap["ground_strata"]["puddle_depth"], 0.0)

            forbidden_morning_clear = ("雨帘", "雨点", "雨丝", "积水", "午后光影", "傍晚", "晚班", "路灯亮成一排", "余粮袋")
            reg_by_id = {r["regular_id"]: r for r in snap["regulars"]}
            self.assertFalse(reg_by_id["uncle_zhou"]["is_out_now"])
            self.assertFalse(reg_by_id["student_chen"]["is_out_now"])
            self.assertFalse(reg_by_id["feeder_su"]["is_out_now"])
            self.assertTrue(reg_by_id["auntie_lin"]["is_out_now"])
            self.assertTrue(reg_by_id["keeper_he"]["is_out_now"])

            for rid, row in reg_by_id.items():
                scene = str(row["living_scene_cn"])
                self.assertTrue(scene, rid)
                for bad in forbidden_morning_clear:
                    self.assertNotIn(bad, scene, f"{rid} scene contradicted clear 09:49 AM: {scene}")

            # Window soundscape across a full 16-day rotation (including 2026-11-21 and active home_window_sound day 2026-11-17)
            for offset in range(16):
                probe_day = date(2026, 11, 6) + timedelta(days=offset)
                day_events = wae.materialize_ambient_events(
                    probe_day, environment={}, day_root=root
                )
                for ev in day_events:
                    summary = ev["payload"]["summary"]
                    for rain_tok in ("窗外雨声变大", "雨声", "雨点", "雨帘", "积水"):
                        self.assertNotIn(rain_tok, summary, (probe_day, summary))
                    self.assertTrue(ev["valid_from"].endswith("+08:00"), ev["valid_from"])
                    self.assertTrue(ev["valid_until"].endswith("+08:00"), ev["valid_until"])

            # Verify active home_window_sound specifically (explicit weather vs zero-write auto-read from v04_neighborhood_state.json)
            sound_day = date(2026, 11, 17)
            now_sound_local = datetime(2026, 11, 17, 9, 49, 23, tzinfo=ssp.DEFAULT_TZ)
            events_explicit = wae.active_ambient_events(
                sound_day,
                now_sound_local,
                relevant_location_ids={"home"},
                environment={},
                weather="clear",
            )
            events_auto = wae.active_ambient_events(
                sound_day,
                now_sound_local,
                relevant_location_ids={"home"},
                environment={},
                day_root=root,
            )
            self.assertEqual(_snapshot_tree(root), before)
            self.assertTrue(events_explicit)
            self.assertEqual(events_explicit, events_auto)
            self.assertTrue(any(ev["event_type"] == "home_window_sound" for ev in events_auto))
            for ev in events_auto:
                summary = ev["payload"]["summary"]
                for rain_tok in ("窗外雨声变大", "雨声", "雨点", "雨帘", "积水"):
                    self.assertNotIn(rain_tok, summary)
                self.assertTrue(ev["valid_from"].endswith("+08:00"), ev["valid_from"])
                self.assertTrue(ev["valid_until"].endswith("+08:00"), ev["valid_until"])

            # Rainy weather still emits rain soundscape
            events_rain = wae.active_ambient_events(
                sound_day,
                now_sound_local,
                relevant_location_ids={"home"},
                environment={},
                weather="rain",
            )
            self.assertTrue(any("雨声" in ev["payload"]["summary"] for ev in events_rain), events_rain)

            # Timezone alignment: default +08:00, explicit Asia/Tokyo preserved
            now_local = datetime(2026, 11, 21, 9, 49, 23, tzinfo=ssp.DEFAULT_TZ)
            self.assertEqual(wae.WORLD_TIMEZONE, "+08:00")
            self.assertTrue(wae.align_local_time(now_local).endswith("+08:00"))
            tokyo_events = wae.materialize_ambient_events(
                date(2026, 11, 21), timezone_name="Asia/Tokyo", environment={}, weather="clear"
            )
            self.assertTrue(all(ev["valid_from"].endswith("+09:00") for ev in tokyo_events))

    def test_04_post_lidong_2026_11_21_solar_term_season_coherence(self) -> None:
        pulse = ssp.project_season_solar_pulse(now_iso="2026-11-21T09:49:23+08:00")
        st = pulse["solar_term"]
        self.assertEqual(st["term_name_cn"], "立冬")
        self.assertEqual(st["solar_season"], "winter")
        self.assertEqual(st["days_since_term"], 14)
        self.assertEqual(st["next_term_name_cn"], "小雪")
        self.assertEqual(st["days_until_next_term"], 1)

        self.assertEqual(pulse["season"], "winter")
        self.assertEqual(pulse["season_source"], "calendar_live")
        self.assertEqual(pulse["season_label_cn"], "冬景")
        cue = pulse["compact_context_cue_cn"]
        self.assertIn("立冬后", cue)
        self.assertIn("冬景", cue)
        self.assertNotIn("秋意", cue)
        self.assertNotIn("秋风", pulse["microclimate"]["sound_hint_cn"])
        self.assertIn("冬", pulse["microclimate"]["sound_hint_cn"])

        with tempfile.TemporaryDirectory(prefix="v08-season-1121-") as td:
            snap = lt_engine.read_living_town_snapshot(Path(td), now_iso="2026-11-21T09:49:23+08:00")
            self.assertEqual(snap["world_context"]["season"], "winter")
            dumped_ssm = json.dumps(snap["sound_scent_map"], ensure_ascii=False)
            self.assertNotIn("低斜秋阳", dumped_ssm)
            self.assertNotIn("秋风干爽", dumped_ssm)
            self.assertNotIn("秋意", dumped_ssm)

    def test_05_lived_context_bridge_chinese_enums_clear_weather_ellipsis_dedup_and_hygiene(self) -> None:
        self.assertEqual(lcb.MAX_LIVED_CONTEXT_CHARS, 1800)

        with tempfile.TemporaryDirectory(prefix="v08-lcb-quality-") as td:
            root = Path(td)
            now_iso = "2026-11-21T09:49:23+08:00"
            (root / "phone").mkdir(parents=True, exist_ok=True)
            _seed_body_runtime(root, at_iso=now_iso, hunger=0.90, warmth=0.50)

            _write_json(
                root / "world" / "v04_neighborhood_state.json",
                {
                    "schema": "world.v04.neighborhood_living.v1",
                    "revision": 3,
                    "environment": {
                        "weather": "clear",
                        "airflow": "strong",
                        "temp_c": 15,
                        "season": None,
                        "season_explicit": False,
                        "updated_at": None,
                    },
                    "courtyard": {
                        "drying_rack": {
                            "items": [{"label": "棉麻布巾", "state": "dry"}],
                        }
                    },
                    "potted_plant": {
                        "plant_id": "porch_fern",
                        "name": "小叶常春藤",
                        "hydration": 72.0,
                        "leaf_count": 4,
                        "new_leaf_note_cn": "刚抽一片嫩叶",
                    },
                    "public_life": {
                        "shared_shelf": {
                            "items": [
                                {"label": "榉木固定夹（四枚装）", "holder": "shared_shelf"},
                                {"label": "钢边裁纸尺", "holder": "shared_shelf"},
                            ]
                        }
                    },
                },
            )

            desire_text = "肚子有点空了，想顺路去拐角面包铺挑一只刚出炉的盐卷配热茶"
            dd_engine.record_subject_desire(
                root,
                layer="micro_craving",
                text=desire_text,
                category="appetite_craving",
                now_iso=now_iso,
                actor_side="subject",
            )
            lt_engine.post_bulletin_followup(
                root,
                followup_cn="巷口晒太阳的橘猫状态很好。",
                now_iso=now_iso,
            )

            before = _snapshot_tree(root)
            cfg = {"day_root": str(root), "phone": {"enabled": False, "private_artifacts": True}}
            rendered = lcb.build_lived_turn_context(
                root,
                cfg=cfg,
                incoming_text="今天冷不冷，下雨了吗？",
                now_iso=now_iso,
            )
            self.assertEqual(_snapshot_tree(root), before, "lived_context_bridge must stay 100% zero-write")
            self.assertLessEqual(len(rendered), 1800)

            # Chinese enum translations & clear weather surfaced
            self.assertIn("晴朗", rendered)
            self.assertIn("15度", rendered)
            self.assertIn("风:大风", rendered)
            self.assertIn("棉麻布巾(已干)", rendered)
            self.assertIn("(共享架)", rendered)
            for leaked in ("风:strong", "(dry)", "(shared_she)", "shared_shelf", "permit", "身体肚子已经很饿"):
                self.assertNotIn(leaked, rendered, f"unexpected raw/awkward token {leaked!r} in:\n{rendered}")

            # Clean somatic wording and no 3x duplication of the exact same desire
            self.assertIn("体感：肚子已经很饿", rendered)
            self.assertEqual(rendered.count(desire_text), 1, f"desire duplicated in:\n{rendered}")

            # Long desire/margin note truncates cleanly with ellipsis
            long_desire = "这是一条非常长非常长的测试心愿文案用来确认超长截断时会自动带上省略号不会硬切半截词"
            short_d = lcb._format_everyday_desires_micro(
                {"active_desires": [{"status": "active", "text": long_desire, "domain": "food"}]},
                now_dt=ssp.parse_iso_to_default_tz(now_iso),
            )
            self.assertIsNotNone(short_d)
            assert short_d is not None
            self.assertIn("…", short_d)

            long_margin = lcb._extract_v05_margin_note_text(
                {"note_cn": "周叔在页边记了一段很长很长的话用来验证超过上限以后会干干净净补上省略号"}
            )
            self.assertTrue(long_margin.endswith("…"), long_margin)

            # Calm Gate intact: empty root == 0 chars, calm default v04_nb stays silent on ordinary greeting
            calm_empty = Path(td) / "never_created"
            self.assertEqual(lcb.build_lived_turn_context(calm_empty, cfg={}, incoming_text="今天冷不冷", now_iso=now_iso), "")
            self.assertFalse(calm_empty.exists())
            calm_v04 = lcb._format_v04_neighborhood_and_elastic(
                v04_nb={"environment": {"weather": "clear", "airflow": "medium"}},
                v04_ep=None,
                now_dt=ssp.parse_iso_to_default_tz(now_iso),
                incoming_text="你好",
            )
            self.assertIsNone(calm_v04)

    def test_06_file_hygiene_utf8_no_bom_lf_zero_so_zero_pronouns(self) -> None:
        owned_files = (
            REPO_ROOT / "components" / "life" / "scripts" / "v05_dreams_desires_foreshadow.py",
            REPO_ROOT / "components" / "world" / "scripts" / "v05_living_town_engine.py",
            REPO_ROOT / "components" / "world" / "scripts" / "world_ambient_events.py",
            REPO_ROOT / "components" / "world" / "scripts" / "v05_season_solar_pulse.py",
            REPO_ROOT / "chiyo_bundle" / "lived_context_bridge.py",
            Path(__file__).resolve(),
        )
        forbidden_tokens = ("\u5979", "\u4ed6", "\u5343\u4ee3")
        for fpath in owned_files:
            raw = fpath.read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), f"BOM found in {fpath.name}")
            self.assertNotIn(b"\r", raw, f"CRLF/CR found in {fpath.name}")
            text = raw.decode("utf-8")
            so_chars = [ch for ch in text if unicodedata.category(ch) == "So"]
            self.assertEqual(so_chars, [], f"unicode.So chars in {fpath.name}: {so_chars[:5]}")
            for tok in forbidden_tokens:
                self.assertEqual(text.count(tok), 0, f"Forbidden token {tok!r} found in {fpath.name}")


if __name__ == "__main__":
    unittest.main()

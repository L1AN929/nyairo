#!/usr/bin/env python3
"""Deterministic Wave B2 tests (llm_calls == 0):
- F-018..019: External fact source registry & real vs fictional world news layering
- F-029..031: Body state life history & multi-carrier adaptation
- F-144..146: Backup-restore external-history honesty, inherited meaning versions & fork lineage
- HTTP routing in ``components/phone/server.py`` for ``/api/phone/v02/world-lineage``
  and ``/api/phone/v02/ecology-growth`` (CSRF, capability, identity fence, zero-write GET).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "components" / "world" / "service" / "bridge"),
    str(ROOT / "components" / "supply"),
    str(Path(__file__).resolve().parent),
    str(ROOT / "scripts"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from components.life.scripts import v02_world_news_carrier_lineage as wl_engine  # noqa: E402
from components.phone import capability as phone_capability  # noqa: E402
from components.phone import v02_news_carrier_lineage_view as wl_view  # noqa: E402

SUBJECT = "chiyo"
CAP_TOKEN = "v02-waveb2-world-lineage-cap-20261119"
T0 = "2026-11-19T09:00:00+08:00"
T1 = "2026-11-19T10:30:00+08:00"
T2 = "2026-11-19T12:00:00+08:00"

POSIX_ONLY = unittest.skipUnless(
    hasattr(socket, "AF_UNIX") and os.name == "posix",
    "Requires POSIX domain sockets (run inside WSL)",
)

_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F9FF"
    "\U0001FA00-\U0001FAFF"
    "\u2600-\u26FF"
    "\u2700-\u27BF"
    "]"
)


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int, str]]:
    if not root.exists():
        return {}
    snap: dict[str, tuple[int, int, str]] = {}
    for p in sorted(root.rglob("*")):
        if p.name.endswith((".sqlite-shm", ".sqlite-wal")):
            continue
        rel = p.relative_to(root).as_posix()
        try:
            st = p.lstat()
        except FileNotFoundError:
            continue
        if p.is_file() and not p.is_symlink():
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            snap[rel] = (int(st.st_size), int(st.st_mtime_ns), digest)
        else:
            dir_mtime = 0 if rel == "data" else int(st.st_mtime_ns)
            snap[rel] = (0, dir_mtime, "dir")
    return snap


class WaveB2StaticHygieneTest(unittest.TestCase):
    def test_01_lf_encoding_zero_emojis_and_neutral_ta(self) -> None:
        new_files = (
            ROOT / "components" / "life" / "scripts" / "v02_world_news_carrier_lineage.py",
            ROOT / "components" / "phone" / "v02_news_carrier_lineage_view.py",
            ROOT / "tests" / "test_v02_waveb2_news_carrier_lineage.py",
        )
        forbidden_tokens = ("\u5979", "\u5343\u4ee3")
        for fpath in new_files:
            raw = fpath.read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), f"UTF-8 BOM detected in {fpath}")
            self.assertNotIn(b"\r\n", raw, f"CRLF detected in {fpath}")
            text = raw.decode("utf-8")
            self.assertIsNone(_EMOJI_RE.search(text), f"Emoji detected in {fpath}")
            for tok in forbidden_tokens:
                self.assertNotIn(tok, text, f"Non-neutral token {tok!r} detected in {fpath}")

        server_raw = (ROOT / "components" / "phone" / "server.py").read_bytes()
        self.assertFalse(server_raw.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r\n", server_raw)


class WaveB2DomainAndViewTest(unittest.TestCase):
    def setUp(self) -> None:
        self._saved_env = dict(os.environ)
        os.environ.pop(wl_engine.CONTROL_ROOT_ENV_VAR, None)
        self.tmp = Path(tempfile.mkdtemp(prefix="v02-waveb2-unit-"))
        self.day_root = self.tmp / "day_root"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.clear()
        os.environ.update(self._saved_env)

    def test_02_zero_write_get_on_empty_and_populated_roots(self) -> None:
        # Non-existent root
        self.assertFalse(self.day_root.exists())
        code_p, body_p = wl_view.read_v02_world_lineage_view(
            self.day_root, has_subject_capability=False, now_iso=T0
        )
        code_s, body_s = wl_view.read_v02_world_lineage_view(
            self.day_root, has_subject_capability=True, now_iso=T0
        )
        self.assertEqual(code_p, 200)
        self.assertEqual(code_s, 200)
        self.assertFalse(self.day_root.exists())
        self.assertEqual(body_p["llm_calls"], 0)
        self.assertTrue(body_p["zero_write"])
        self.assertFalse(body_p["state_exists"])
        self.assertEqual(body_p["perspective"], "partner")
        self.assertEqual(body_s["perspective"], "subject")

        # Empty existing root
        self.day_root.mkdir(parents=True, exist_ok=True)
        snap_empty_before = _snapshot_tree(self.day_root)
        wl_view.read_v02_world_lineage_view(self.day_root, has_subject_capability=False, now_iso=T0)
        wl_view.read_v02_world_lineage_view(self.day_root, has_subject_capability=True, now_iso=T0)
        self.assertEqual(_snapshot_tree(self.day_root), snap_empty_before)

        # Rejected mutation on empty root must also stay 100% zero-write (no .locks or state dir!)
        c_err, b_err = wl_view.mutate_v02_world_lineage_view(
            self.day_root,
            {"action": "record_body_history_entry", "period_label": "11月", "comfort_summary_cn": "还行"},
            actor_side="partner",
            now_iso=T0,
        )
        self.assertEqual(c_err, 403)
        self.assertEqual(b_err["reason_code"], "FORBIDDEN_ACTOR_SIDE")
        self.assertEqual(_snapshot_tree(self.day_root), snap_empty_before)

        # Populated root GET must remain 100% zero-write
        c_ok, _ = wl_view.mutate_v02_world_lineage_view(
            self.day_root,
            {
                "action": "register_external_fact_source",
                "title_cn": "气象局公开记录",
                "source_kind": "website",
                "source_ref": "https://weather.example.org/records/2026",
                "operation_key": "op-zw-1",
            },
            actor_side="partner",
            now_iso=T0,
        )
        self.assertEqual(c_ok, 200)
        snap_pop_before = _snapshot_tree(self.day_root)
        wl_view.read_v02_world_lineage_view(self.day_root, has_subject_capability=False, now_iso=T1)
        wl_view.read_v02_world_lineage_view(self.day_root, has_subject_capability=True, now_iso=T1)
        self.assertEqual(_snapshot_tree(self.day_root), snap_pop_before)

    def test_03_f018_f019_external_sources_news_layers_and_live_world_merge(self) -> None:
        self.day_root.mkdir(parents=True, exist_ok=True)

        # Register external source with leaked Windows local path -> sanitized!
        c_src1, b_src1 = wl_view.mutate_v02_world_lineage_view(
            self.day_root,
            {
                "action": "register_external_fact_source",
                "title_cn": "个人整理剪报",
                "source_kind": "person_note",
                "source_ref": "C:\\" + "Users\\alice\\Desktop\\private_clip.txt",
                "trust_tier": "PRIMARY_RECORD",
                "shared": False,  # partner must be forced to shared=True
            },
            actor_side="partner",
            now_iso=T0,
        )
        self.assertEqual(c_src1, 200)
        src1 = b_src1["action_receipt"]["fact_source"]
        self.assertTrue(src1["shared"])
        self.assertEqual(src1["registered_by"], "partner")
        self.assertNotIn("alice", src1["source_ref"])
        self.assertIn("[local-path-redacted]", src1["source_ref"])

        # Register external website source
        c_src2, b_src2 = wl_view.mutate_v02_world_lineage_view(
            self.day_root,
            {
                "action": "register_external_fact_source",
                "title_cn": "世界天文年报",
                "source_kind": "website",
                "source_ref": "https://astronomy.example.org/2026-eclipse",
                "trust_tier": "TRUSTED_REFERENCE",
            },
            actor_side="partner",
            now_iso=T0,
        )
        self.assertEqual(c_src2, 200)
        src2_id = b_src2["action_receipt"]["fact_source"]["source_id"]

        # EXTERNAL_REAL_FACT without source_ref or valid source_id -> 400 MISSING_FACT_SOURCE
        c_miss, b_miss = wl_view.mutate_v02_world_lineage_view(
            self.day_root,
            {
                "action": "record_layered_news_item",
                "layer": "EXTERNAL_REAL_FACT",
                "headline_cn": "缺少来源的现实新闻",
            },
            actor_side="partner",
            now_iso=T0,
        )
        self.assertEqual(c_miss, 400)
        self.assertEqual(b_miss["reason_code"], "MISSING_FACT_SOURCE")

        # EXTERNAL_REAL_FACT with valid registered source_id -> succeeds
        c_ext, b_ext = wl_view.mutate_v02_world_lineage_view(
            self.day_root,
            {
                "action": "record_layered_news_item",
                "layer": "EXTERNAL_REAL_FACT",
                "headline_cn": "十一月月偏食观测预报",
                "summary_cn": "天文年报记录本月有一次可见月偏食",
                "source_id": src2_id,
            },
            actor_side="partner",
            now_iso=T0,
        )
        self.assertEqual(c_ext, 200)
        ext_item = b_ext["action_receipt"]["news_item"]
        self.assertFalse(ext_item["is_fictional_town"])
        self.assertEqual(ext_item["layer_label_cn"], "外部现实信息·独立于小镇")
        self.assertTrue(ext_item["verified"])

        # UNVERIFIED_HEARSAY -> is_fictional_town=False, verified=False
        c_hs, b_hs = wl_view.mutate_v02_world_lineage_view(
            self.day_root,
            {
                "action": "record_layered_news_item",
                "layer": "UNVERIFIED_HEARSAY",
                "headline_cn": "听说周末可能下初雪",
                "summary_cn": "只是顺口听人提起，还没查过预报",
            },
            actor_side="partner",
            now_iso=T0,
        )
        self.assertEqual(c_hs, 200)
        hs_item = b_hs["action_receipt"]["news_item"]
        self.assertFalse(hs_item["is_fictional_town"])
        self.assertFalse(hs_item["verified"])
        self.assertEqual(hs_item["layer_label_cn"], "口头听闻·未核证")

        # Partner attempting LOCAL_WORLD_NEWS -> 403 FORBIDDEN_ACTOR_SIDE
        c_forb, b_forb = wl_view.mutate_v02_world_lineage_view(
            self.day_root,
            {
                "action": "record_layered_news_item",
                "layer": "LOCAL_WORLD_NEWS",
                "headline_cn": "伴侣试图伪造小镇通知",
                "source_ref": "town_board",
            },
            actor_side="partner",
            now_iso=T0,
        )
        self.assertEqual(c_forb, 403)
        self.assertEqual(b_forb["reason_code"], "FORBIDDEN_ACTOR_SIDE")

        # Subject attempting LOCAL_WORLD_NEWS with external URL -> 409 CROSS_LAYER_CONTAMINATION
        c_contam1, b_contam1 = wl_view.mutate_v02_world_lineage_view(
            self.day_root,
            {
                "action": "record_layered_news_item",
                "layer": "LOCAL_WORLD_NEWS",
                "headline_cn": "混入外部网址的新闻",
                "source_ref": "https://news.example.com/breaking",
            },
            actor_side="subject",
            now_iso=T0,
        )
        self.assertEqual(c_contam1, 409)
        self.assertEqual(b_contam1["reason_code"], "CROSS_LAYER_CONTAMINATION")

        # Subject attempting LOCAL_WORLD_NEWS referencing external website source_id -> 409
        c_contam2, b_contam2 = wl_view.mutate_v02_world_lineage_view(
            self.day_root,
            {
                "action": "record_layered_news_item",
                "layer": "LOCAL_WORLD_NEWS",
                "headline_cn": "混入已注册外部网站源",
                "source_id": src2_id,
            },
            actor_side="subject",
            now_iso=T0,
        )
        self.assertEqual(c_contam2, 409)
        self.assertEqual(b_contam2["reason_code"], "CROSS_LAYER_CONTAMINATION")

        # Subject recording valid fictional LOCAL_WORLD_NEWS -> 200
        c_loc, b_loc = wl_view.mutate_v02_world_lineage_view(
            self.day_root,
            {
                "action": "record_layered_news_item",
                "layer": "LOCAL_WORLD_NEWS",
                "headline_cn": "巷口面包房换了肉桂卷烤盘",
                "source_ref": "bakery_chalkboard",
                "location_id": "street_corner",
            },
            actor_side="subject",
            now_iso=T1,
        )
        self.assertEqual(c_loc, 200)
        loc_item = b_loc["action_receipt"]["news_item"]
        self.assertTrue(loc_item["is_fictional_town"])
        self.assertEqual(loc_item["layer_label_cn"], "小镇虚构动态")

        # Populate live world/world_ambient_events.json and world/v04_neighborhood_state.json
        world_dir = self.day_root / "world"
        world_dir.mkdir(parents=True, exist_ok=True)
        (world_dir / "world_ambient_events.json").write_text(
            json.dumps(
                [
                    {
                        "event_id": "ambient-2026-11-19-market",
                        "local_date": "2026-11-19",
                        "location_id": "town_square",
                        "event_type": "morning_market",
                        "payload": {"summary": "广场早市摆出了刚摘的柑橘"},
                        "valid_from": T0,
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (world_dir / "v04_neighborhood_state.json").write_text(
            json.dumps(
                {
                    "public_life": {
                        "public_notices": [
                            {
                                "notice_id": "nt-001",
                                "title": "小巷修路绕行提示",
                                "body": "北侧石板路下午维护，可走河边步道",
                                "read_by_subject": True,
                                "expired": False,
                                "posted_at": T0,
                            }
                        ]
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        snap_before_read = _snapshot_tree(self.day_root)
        code_r, view_r = wl_view.read_v02_world_lineage_view(
            self.day_root, has_subject_capability=False, now_iso=T2
        )
        self.assertEqual(code_r, 200)
        self.assertEqual(_snapshot_tree(self.day_root), snap_before_read)

        layered = view_r["layered_news"]
        local_ids = [item["news_id"] for item in layered["local_world_news"]]
        self.assertIn("world-ambient-ambient-2026-11-19-market", local_ids)
        self.assertIn("world-notice-nt-001", local_ids)
        self.assertTrue(all(item["is_fictional_town"] is True for item in layered["local_world_news"]))
        self.assertTrue(all(item["layer"] == "LOCAL_WORLD_NEWS" for item in layered["local_world_news"]))
        self.assertTrue(all(item["layer_label_cn"] == "小镇虚构动态" for item in layered["local_world_news"]))

        # Strictly separated from external_real_facts and unverified_hearsay
        self.assertEqual(len(layered["external_real_facts"]), 1)
        self.assertEqual(layered["external_real_facts"][0]["headline_cn"], "十一月月偏食观测预报")
        self.assertFalse(layered["external_real_facts"][0]["is_fictional_town"])
        self.assertEqual(len(layered["unverified_hearsay"]), 1)
        self.assertFalse(layered["unverified_hearsay"][0]["verified"])

    def test_04_f029_body_history_subject_only_and_privacy_filter(self) -> None:
        self.day_root.mkdir(parents=True, exist_ok=True)
        # Partner forbidden
        c_p, b_p = wl_view.mutate_v02_world_lineage_view(
            self.day_root,
            {
                "action": "record_body_history_entry",
                "period_label": "2026-11 初冬",
                "comfort_summary_cn": "肩颈稍微发紧",
            },
            actor_side="partner",
            now_iso=T0,
        )
        self.assertEqual(c_p, 403)
        self.assertEqual(b_p["reason_code"], "FORBIDDEN_ACTOR_SIDE")

        # Subject records shared and private entries
        c1, _ = wl_view.mutate_v02_world_lineage_view(
            self.day_root,
            {
                "action": "record_body_history_entry",
                "period_label": "2026-11 上旬",
                "comfort_summary_cn": "换季容易手凉，早晚喝热茶会舒服很多",
                "illness_or_strain_tag": "seasonal_chill",
                "rest_adjustment_cn": "晚间减少久坐，提早半小时熄灯",
                "shared": True,
            },
            actor_side="subject",
            now_iso=T0,
        )
        c2, _ = wl_view.mutate_v02_world_lineage_view(
            self.day_root,
            {
                "action": "record_body_history_entry",
                "period_label": "2026-11 中旬",
                "comfort_summary_cn": "个人私密疲劳记录，不想让外部看到",
                "illness_or_strain_tag": "eye_strain",
                "rest_adjustment_cn": "午后闭眼十分钟",
                "shared": False,
            },
            actor_side="subject",
            now_iso=T1,
        )
        self.assertEqual(c1, 200)
        self.assertEqual(c2, 200)

        _, partner_v = wl_view.read_v02_world_lineage_view(
            self.day_root, has_subject_capability=False, now_iso=T2
        )
        _, subject_v = wl_view.read_v02_world_lineage_view(
            self.day_root, has_subject_capability=True, now_iso=T2
        )
        self.assertEqual(len(partner_v["body_history"]), 1)
        self.assertEqual(partner_v["body_history"][0]["period_label"], "2026-11 上旬")
        self.assertEqual(len(subject_v["body_history"]), 2)

    def test_05_f030_f031_carrier_profile_and_honest_adaptations(self) -> None:
        self.day_root.mkdir(parents=True, exist_ok=True)
        # Switch to headless_cli (defaults all capabilities False)
        c_cli, b_cli = wl_view.mutate_v02_world_lineage_view(
            self.day_root,
            {
                "action": "sync_carrier_profile_and_adapt_habits",
                "carrier_kind": "headless_cli",
                "note_cn": "在远程终端里保持文字联络",
            },
            actor_side="partner",
            now_iso=T0,
        )
        self.assertEqual(c_cli, 200)
        ca_cli = b_cli["carrier_adaptation"]
        self.assertEqual(ca_cli["carrier_kind"], "headless_cli")
        self.assertFalse(ca_cli["capabilities"]["visual_preview"])
        self.assertFalse(ca_cli["capabilities"]["audio_output"])
        self.assertFalse(ca_cli["capabilities"]["desk_stage"])
        codes_cli = {item["code"] for item in ca_cli["active_adaptations"]}
        self.assertTrue({"NO_VISUAL_PREVIEW", "NO_AUDIO_OUTPUT", "NO_DESK_STAGE"}.issubset(codes_cli))
        self.assertNotIn("FULL_DESKTOP_CARRIER", codes_cli)

        # Switch to desktop_gui (all capabilities True by default)
        c_desk, b_desk = wl_view.mutate_v02_world_lineage_view(
            self.day_root,
            {
                "action": "sync_carrier_profile_and_adapt_habits",
                "carrier_kind": "desktop_gui",
            },
            actor_side="partner",
            now_iso=T1,
        )
        self.assertEqual(c_desk, 200)
        ca_desk = b_desk["carrier_adaptation"]
        codes_desk = [item["code"] for item in ca_desk["active_adaptations"]]
        self.assertEqual(codes_desk, ["FULL_DESKTOP_CARRIER"])
        self.assertEqual(len(ca_desk["carrier_switch_history"]), 2)
        self.assertEqual(ca_desk["carrier_switch_history"][1]["from_carrier_kind"], "headless_cli")
        self.assertEqual(ca_desk["carrier_switch_history"][1]["to_carrier_kind"], "desktop_gui")

        # Invalid carrier_kind rejected
        c_bad, b_bad = wl_view.mutate_v02_world_lineage_view(
            self.day_root,
            {
                "action": "sync_carrier_profile_and_adapt_habits",
                "carrier_kind": "smart_fridge",
            },
            actor_side="partner",
            now_iso=T1,
        )
        self.assertEqual(c_bad, 400)
        self.assertEqual(b_bad["reason_code"], "INVALID_FIELDS")

    def test_06_f144_backup_restore_honesty_guard(self) -> None:
        self.day_root.mkdir(parents=True, exist_ok=True)
        snap_before = _snapshot_tree(self.day_root)

        # Default: no external control root
        guard_default = wl_engine.read_backup_restore_honesty_zero_write(self.day_root)
        self.assertFalse(guard_default["external_control_isolated"])
        self.assertTrue(guard_default["history_rewrite_blocked"])
        self.assertEqual(guard_default["llm_calls"], 0)
        self.assertTrue(guard_default["zero_write"])
        self.assertEqual(_snapshot_tree(self.day_root), snap_before)

        # Env var pointing to day_root itself is NOT isolated
        os.environ[wl_engine.CONTROL_ROOT_ENV_VAR] = str(self.day_root)
        guard_same = wl_engine.read_backup_restore_honesty_zero_write(self.day_root)
        self.assertFalse(guard_same["external_control_isolated"])

        # Env var pointing to external directory IS isolated and never leaks host path
        ext_ctrl = self.tmp / "external-ctrl-secret-path"
        ext_ctrl.mkdir(parents=True, exist_ok=True)
        os.environ[wl_engine.CONTROL_ROOT_ENV_VAR] = str(ext_ctrl)
        guard_ext = wl_engine.read_backup_restore_honesty_zero_write(self.day_root)
        self.assertTrue(guard_ext["external_control_isolated"])
        self.assertEqual(guard_ext["isolation_source"], "env_control_root")
        self.assertTrue(guard_ext["history_rewrite_blocked"])
        self.assertNotIn("external-ctrl-secret-path", json.dumps(guard_ext, ensure_ascii=False))

        # Unset env var, test sibling <root>-memory-authority/v02-control + vc_authority_status
        os.environ.pop(wl_engine.CONTROL_ROOT_ENV_VAR, None)
        sibling_ctrl = self.tmp / f"{self.day_root.name}-memory-authority" / "v02-control"
        sibling_ctrl.mkdir(parents=True, exist_ok=True)
        phone_dir = self.day_root / "phone"
        phone_dir.mkdir(parents=True, exist_ok=True)
        (phone_dir / "v02_runtime_migration.json").write_text(
            json.dumps(
                {
                    "vc_authority_status": {
                        "watermark_exists": True,
                        "authority_generation": 4,
                        "restore_epoch": 2,
                        "sealed_digest_count": 7,
                        "enforced": True,
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        snap_after_seed = _snapshot_tree(self.day_root)
        guard_sib = wl_engine.read_backup_restore_honesty_zero_write(self.day_root)
        self.assertTrue(guard_sib["external_control_isolated"])
        self.assertEqual(guard_sib["isolation_source"], "sibling_authority_dir")
        self.assertTrue(guard_sib["vc_authority_observed"]["watermark_exists"])
        self.assertEqual(guard_sib["vc_authority_observed"]["authority_generation"], 4)
        self.assertEqual(guard_sib["vc_authority_observed"]["sealed_digest_count"], 7)
        self.assertIn("4", guard_sib["plain_status_cn"])
        self.assertIn("7", guard_sib["plain_status_cn"])
        self.assertEqual(_snapshot_tree(self.day_root), snap_after_seed)

    def test_07_f145_inherited_meaning_versions_and_partner_notes(self) -> None:
        self.day_root.mkdir(parents=True, exist_ok=True)
        # Subject records v1 and v2 on the same subject_ref
        c1, b1 = wl_view.mutate_v02_world_lineage_view(
            self.day_root,
            {
                "action": "record_meaning_version",
                "subject_ref": "obj:ceramic_mug",
                "subject_title_cn": "缺口白瓷杯",
                "earlier_meaning_cn": "刚搬进来时随便买的日常杯子",
                "current_meaning_cn": "成了雨天泡焙茶时顺手拿起的固定杯子",
                "shared": True,
            },
            actor_side="subject",
            now_iso=T0,
        )
        self.assertEqual(c1, 200)
        mv1 = b1["action_receipt"]["meaning_version"]
        self.assertEqual(mv1["version_num"], 1)
        self.assertTrue(mv1["kept_earlier_intact"])

        # v2 omits earlier_meaning_cn -> auto-inherits v1's current_meaning_cn, v1 untouched
        c2, b2 = wl_view.mutate_v02_world_lineage_view(
            self.day_root,
            {
                "action": "record_meaning_version",
                "subject_ref": "obj:ceramic_mug",
                "subject_title_cn": "缺口白瓷杯",
                "current_meaning_cn": "现在更像陪着渡过整个秋天的老物件",
                "shared": True,
            },
            actor_side="subject",
            now_iso=T1,
        )
        self.assertEqual(c2, 200)
        mv2 = b2["action_receipt"]["meaning_version"]
        self.assertEqual(mv2["version_num"], 2)
        self.assertEqual(mv2["earlier_meaning_cn"], mv1["current_meaning_cn"])
        self.assertTrue(mv2["kept_earlier_intact"])

        # Partner proposes a gentle meaning note -> never alters subject's meaning_versions
        c_note, b_note = wl_view.mutate_v02_world_lineage_view(
            self.day_root,
            {
                "action": "propose_meaning_note",
                "subject_ref": "obj:ceramic_mug",
                "subject_title_cn": "缺口白瓷杯",
                "note_cn": "我也记得那天你在窗边捧着它看雨",
            },
            actor_side="partner",
            now_iso=T2,
        )
        self.assertEqual(c_note, 200)
        self.assertEqual(b_note["action_receipt"]["partner_meaning_note"]["status"], "PARTNER_NOTE")
        self.assertEqual(len(b_note["meaning_versions"]), 2)
        self.assertEqual(b_note["meaning_versions"][0]["current_meaning_cn"], mv1["current_meaning_cn"])
        self.assertEqual(len(b_note["partner_meaning_notes"]), 1)

        # Partner attempting record_meaning_version -> 403
        c_forb, b_forb = wl_view.mutate_v02_world_lineage_view(
            self.day_root,
            {
                "action": "record_meaning_version",
                "subject_ref": "obj:ceramic_mug",
                "subject_title_cn": "缺口白瓷杯",
                "current_meaning_cn": "越权改写",
            },
            actor_side="partner",
            now_iso=T2,
        )
        self.assertEqual(c_forb, 403)
        self.assertEqual(b_forb["reason_code"], "FORBIDDEN_ACTOR_SIDE")

    def test_08_f146_fork_lineage_genealogy_invariants(self) -> None:
        self.day_root.mkdir(parents=True, exist_ok=True)
        c_forb, b_forb = wl_view.mutate_v02_world_lineage_view(
            self.day_root,
            {
                "action": "record_lineage_branch",
                "origin_instance_id": "inst-origin-01",
                "branch_label_cn": "越权登记分支",
                "reason_cn": "测试",
            },
            actor_side="partner",
            now_iso=T0,
        )
        self.assertEqual(c_forb, 403)
        self.assertEqual(b_forb["reason_code"], "FORBIDDEN_ACTOR_SIDE")

        c_ok, b_ok = wl_view.mutate_v02_world_lineage_view(
            self.day_root,
            {
                "action": "record_lineage_branch",
                "parent_branch_id": "root-2026-spring",
                "origin_instance_id": "inst-origin-01",
                "branch_label_cn": "迁居到新设备后的独立分支",
                "reason_cn": "旧机保留静态备份，新机从今日起独立继续生活",
                "shared": True,
            },
            actor_side="subject",
            now_iso=T0,
        )
        self.assertEqual(c_ok, 200)
        br = b_ok["action_receipt"]["lineage_branch"]
        self.assertTrue(br["shared_origin_only"])
        self.assertTrue(br["not_same_person_claim"])
        self.assertEqual(br["independent_life_from"], T0)

        # Private lineage branch hidden from partner
        c_priv, _ = wl_view.mutate_v02_world_lineage_view(
            self.day_root,
            {
                "action": "record_lineage_branch",
                "origin_instance_id": "inst-origin-01",
                "branch_label_cn": "未公开测试分支备忘",
                "reason_cn": "本地自查",
                "shared": False,
            },
            actor_side="subject",
            now_iso=T1,
        )
        self.assertEqual(c_priv, 200)
        _, partner_v = wl_view.read_v02_world_lineage_view(
            self.day_root, has_subject_capability=False, now_iso=T2
        )
        _, subject_v = wl_view.read_v02_world_lineage_view(
            self.day_root, has_subject_capability=True, now_iso=T2
        )
        self.assertEqual(len(partner_v["lineage_branches"]), 1)
        self.assertEqual(len(subject_v["lineage_branches"]), 2)

    def test_09_security_forbidden_keys_and_operation_key_idempotency(self) -> None:
        self.day_root.mkdir(parents=True, exist_ok=True)
        # Forbidden identity/score keys -> 400 INVALID_FIELDS
        for bad_payload in (
            {"action": "propose_meaning_note", "actor_side": "subject", "subject_ref": "a", "subject_title_cn": "b", "note_cn": "c"},
            {"action": "propose_meaning_note", "intimacy_score": 99, "subject_ref": "a", "subject_title_cn": "b", "note_cn": "c"},
            {"action": "propose_meaning_note", "unknown_extra": 1, "subject_ref": "a", "subject_title_cn": "b", "note_cn": "c"},
        ):
            code, body = wl_view.mutate_v02_world_lineage_view(
                self.day_root, bad_payload, actor_side="partner", now_iso=T0
            )
            self.assertEqual(code, 400)
            self.assertEqual(body["reason_code"], "INVALID_FIELDS")

        # Idempotent replay + conflict detection
        req = {
            "action": "propose_meaning_note",
            "subject_ref": "place:south_balcony",
            "subject_title_cn": "南阳台",
            "note_cn": "下午三点的斜阳照到木架上很暖",
            "operation_key": "op-note-001",
        }
        c1, b1 = wl_view.mutate_v02_world_lineage_view(
            self.day_root, req, actor_side="partner", now_iso=T0
        )
        self.assertEqual(c1, 200)
        self.assertEqual(b1["command_status"], "APPLIED")
        self.assertFalse(b1["idempotent_replay"])
        rev_after_first = b1["revision"]

        snap_after_first = _snapshot_tree(self.day_root)
        c2, b2 = wl_view.mutate_v02_world_lineage_view(
            self.day_root, req, actor_side="partner", now_iso=T1
        )
        self.assertEqual(c2, 200)
        self.assertEqual(b2["command_status"], "DUPLICATE")
        self.assertEqual(b2["status"], "already_applied")
        self.assertTrue(b2["idempotent_replay"])
        self.assertEqual(b2["revision"], rev_after_first)
        self.assertEqual(len(b2["partner_meaning_notes"]), 1)
        # Replay itself is zero-write!
        self.assertEqual(_snapshot_tree(self.day_root), snap_after_first)

        # Conflicting payload with same operation_key -> 409 OPERATION_KEY_CONFLICT (zero-write)
        req_conflict = dict(req)
        req_conflict["note_cn"] = "完全不同的另一段话"
        c3, b3 = wl_view.mutate_v02_world_lineage_view(
            self.day_root, req_conflict, actor_side="partner", now_iso=T2
        )
        self.assertEqual(c3, 409)
        self.assertEqual(b3["reason_code"], "OPERATION_KEY_CONFLICT")
        self.assertEqual(_snapshot_tree(self.day_root), snap_after_first)


@POSIX_ONLY
class WaveB2LiveHTTPServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import autonomy_rhythm_fixture as fixture

        cls.fixture = fixture
        cls._saved_env = dict(os.environ)
        os.environ["CHIYO_WORLD_LOCAL_OWNER_PHONE"] = SUBJECT
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="v02-waveb2-http-"))
        cls.server_root = cls.temp_dir / "instance"
        cls.capability = phone_capability.Capability(
            token=CAP_TOKEN,
            subject=SUBJECT,
            endpoint="http://127.0.0.1:1",
            issued_at="2026-11-19T09:00:00+08:00",
        )
        cls.stack = fixture.build_stack(cls.server_root, port=0, capability=cls.capability)
        fixture.serve_stack(cls.stack)
        cls.base_url = cls.stack.base

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixture.close_stack(cls.stack)
        shutil.rmtree(cls.temp_dir, ignore_errors=True)
        os.environ.clear()
        os.environ.update(cls._saved_env)

    def _get(self, path: str, *, cap_token: str | None = None) -> tuple[int, dict[str, Any]]:
        headers: dict[str, str] = {}
        if cap_token is not None:
            headers["X-Phone-Capability"] = cap_token
        req = urllib.request.Request(f"{self.base_url}{path}", headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read().decode("utf-8"))

    def _post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        csrf_token: str | None = None,
        cap_token: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        headers = {"Content-Type": "application/json"}
        if csrf_token is not None:
            headers["X-CSRF-Token"] = csrf_token
        if cap_token is not None:
            headers["X-Phone-Capability"] = cap_token
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(f"{self.base_url}{path}", data=raw, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read().decode("utf-8"))

    def _csrf(self) -> str:
        code, state = self._get("/api/phone/state")
        self.assertEqual(code, 200)
        token = str(state.get("csrf_token") or "")
        self.assertTrue(token)
        return token

    def test_10_live_http_world_lineage_and_ecology_routes(self) -> None:
        path = "/api/phone/v02/world-lineage"

        # 1. Zero-write GET (partner & subject)
        snap_before = _snapshot_tree(self.server_root)
        code_p, body_p = self._get(path)
        code_s, body_s = self._get(path, cap_token=CAP_TOKEN)
        self.assertEqual(code_p, 200)
        self.assertEqual(code_s, 200)
        self.assertEqual(body_p["perspective"], "partner")
        self.assertEqual(body_s["perspective"], "subject")
        self.assertEqual(body_p["llm_calls"], 0)
        self.assertTrue(body_p["zero_write"])
        self.assertEqual(_snapshot_tree(self.server_root), snap_before)

        # 2. Unknown query rejected -> 400 UNKNOWN_QUERY
        c_q, b_q = self._get(f"{path}?unexpected=1")
        self.assertEqual(c_q, 400)
        self.assertEqual(b_q["reason_code"], "UNKNOWN_QUERY")

        # 3. Missing CSRF -> 403
        c_nocsrf, _ = self._post(path, {"action": "sync_carrier_profile_and_adapt_habits", "carrier_kind": "headless_cli"})
        self.assertEqual(c_nocsrf, 403)

        csrf = self._csrf()

        # 4. Wrong capability token -> 403 (never silently downgraded)
        c_badcap, _ = self._post(
            path,
            {"action": "sync_carrier_profile_and_adapt_habits", "carrier_kind": "headless_cli"},
            csrf_token=csrf,
            cap_token="wrong-cap-token",
        )
        self.assertEqual(c_badcap, 403)

        # 5. Client identity smuggling -> 409 CLIENT_IDENTITY_REFUSED
        c_uid, b_uid = self._post(
            path,
            {"action": "propose_meaning_note", "uid": "root"},
            csrf_token=csrf,
        )
        self.assertEqual(c_uid, 409)
        self.assertIn("CLIENT_IDENTITY_REFUSED", str(b_uid.get("reason_code") or ""))

        # 6. Partner POST succeeds + idempotent replay
        payload_cli = {
            "action": "sync_carrier_profile_and_adapt_habits",
            "carrier_kind": "headless_cli",
            "operation_key": "http-op-cli-1",
        }
        c_ok, b_ok = self._post(path, payload_cli, csrf_token=csrf)
        self.assertEqual(c_ok, 200)
        self.assertEqual(b_ok["command_status"], "APPLIED")
        self.assertEqual(b_ok["carrier_adaptation"]["carrier_kind"], "headless_cli")

        c_dup, b_dup = self._post(path, payload_cli, csrf_token=csrf)
        self.assertEqual(c_dup, 200)
        self.assertEqual(b_dup["command_status"], "DUPLICATE")
        self.assertEqual(b_dup["status"], "already_applied")

        # 7. Partner blocked on subject-only action & LOCAL_WORLD_NEWS -> 403
        c_sub_forb, b_sub_forb = self._post(
            path,
            {
                "action": "record_body_history_entry",
                "period_label": "2026-11",
                "comfort_summary_cn": "未授权写入",
            },
            csrf_token=csrf,
        )
        self.assertEqual(c_sub_forb, 403)
        self.assertEqual(b_sub_forb["reason_code"], "FORBIDDEN_ACTOR_SIDE")

        c_news_forb, b_news_forb = self._post(
            path,
            {
                "action": "record_layered_news_item",
                "layer": "LOCAL_WORLD_NEWS",
                "headline_cn": "伴侣不能写小镇动态",
            },
            csrf_token=csrf,
        )
        self.assertEqual(c_news_forb, 403)
        self.assertEqual(b_news_forb["reason_code"], "FORBIDDEN_ACTOR_SIDE")

        # 8. Subject capability unlocks private body history & lineage branch
        c_priv_body, _ = self._post(
            path,
            {
                "action": "record_body_history_entry",
                "period_label": "2026-11 降温周",
                "comfort_summary_cn": "仅自己可见的睡眠记录",
                "shared": False,
                "operation_key": "http-op-body-priv",
            },
            csrf_token=csrf,
            cap_token=CAP_TOKEN,
        )
        self.assertEqual(c_priv_body, 200)

        c_lbr, b_lbr = self._post(
            path,
            {
                "action": "record_lineage_branch",
                "origin_instance_id": "inst-http-01",
                "branch_label_cn": "HTTP 验证分支",
                "reason_cn": "测试独立谱系声明",
                "shared": True,
                "operation_key": "http-op-lbr-1",
            },
            csrf_token=csrf,
            cap_token=CAP_TOKEN,
        )
        self.assertEqual(c_lbr, 200)
        self.assertTrue(b_lbr["lineage_branches"][0]["not_same_person_claim"])

        _, get_partner = self._get(path)
        _, get_subject = self._get(path, cap_token=CAP_TOKEN)
        self.assertEqual(len(get_partner["body_history"]), 0)
        self.assertEqual(len(get_subject["body_history"]), 1)
        self.assertEqual(len(get_partner["lineage_branches"]), 1)

        # 9. /api/phone/v02/ecology-growth route is wired (200 if Worker 1 module present, else clean 503)
        c_eco, b_eco = self._get("/api/phone/v02/ecology-growth")
        self.assertIn(c_eco, (200, 503))
        if c_eco == 503:
            self.assertEqual(b_eco["reason_code"], "V02_ECOLOGY_GROWTH_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()

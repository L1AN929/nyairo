#!/usr/bin/env python3
"""Wave B1 Tests: Mid-Scale 0.2 Research Features (F-175 / F-176 / F-177 / F-178).

Deterministic, zero-LLM (``llm_calls == 0``) test suite covering:
1. Static invariants: pure LF, UTF-8 without BOM, zero emojis, neutral ``"TA"``.
2. 100% zero-write GET on empty & populated roots for both
   ``read_v02_ecology_growth_view`` and ``collect_v02_v03_loop_candidates``.
3. F-175: ``snapshot_room_expectations`` -> moving/removing objects in
   ``world/world_state.json`` triggers real ``MOVED_OBJECT`` / ``MISSING_OBJECT``
   detection with ``who_moved == "UNKNOWN"`` -> partner
   ``acknowledge_absence_observation`` overlays partner note without erasing.
4. F-175: ``SHOP_CLOSED_UNEXPECTED`` via ``world/v04_neighborhood_state.json``
   and privacy boundary on private ``record_absence_observation``.
5. F-176: real episodes (paused/failed/abandoned/succeeded, private & shared) in
   ``v02_autobiography_skill_provenance.py`` produce zero-debt opportunities in
   ``read_v02_ecology_growth_view`` and ``collect_v02_v03_loop_candidates``,
   strictly hiding private episodes from partner view.
6. F-177: multi-step ``record_ecology_chain_link`` (``reading -> inquiry ->
   craft -> share``) with organic ``suggested_next_step_cn`` + partner
   ``suggest_ecology_spark`` (``status="OPTIONAL_SPARK"``, ``zero_debt=True``).
7. F-178: ``record_practice_standard_evolution`` (distinguishing evaluation
   standard shift from success-rate gain) + zero-write proposal detection from
   ``v02_autobiography_skill_provenance`` (``trials >= 3``, ``method_patches``)
   and ``phone/v02_crossmodal.json`` (``palette_studies >= 2``).
8. Security: partner calling all 4 subject-only actions returns
   ``403 FORBIDDEN_ACTOR_SIDE`` with zero writes; forged identity/score keys
   and invalid fields return ``400 INVALID_FIELDS`` with zero writes.
9. Idempotency: ``operation_key`` replay returns ``DUPLICATE`` and conflicting
   payload returns ``409 OPERATION_KEY_CONFLICT`` with zero-write preflight.
10. Host filesystem path stripping in projections.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "phone"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from components.life.scripts import (  # noqa: E402
    v02_autobiography_skill_provenance as asp,
    v02_midscale_ecology_engine as eco_engine,
    v02_v03_daily_loop_bridge as loop_bridge,
)
from components.phone import v02_midscale_ecology_view as eco_view  # noqa: E402

T0 = "2026-11-19T09:00:00+08:00"
T1 = "2026-11-19T11:15:00+08:00"
T2 = "2026-11-19T14:30:00+08:00"

_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F6FF"
    "\U0001F900-\U0001FAFF"
    "\U00002600-\U000027BF"
    "]"
)


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int, str]]:
    if not root.exists():
        return {}
    snap: dict[str, tuple[int, int, str]] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        st = path.lstat()
        if path.is_file() and not path.is_symlink():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            snap[rel] = (int(st.st_size), int(st.st_mtime_ns), digest)
        else:
            snap[rel] = (0, int(st.st_mtime_ns), "dir")
    return snap


def _write_json(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    path.write_bytes(text.encode("utf-8"))


class TestV02WaveB1MidscaleEcology(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v02-waveb1-ecology-")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_01_static_invariants_pure_lf_no_emoji_neutral_ta(self) -> None:
        owned_files = (
            ROOT / "components" / "life" / "scripts" / "v02_midscale_ecology_engine.py",
            ROOT / "components" / "phone" / "v02_midscale_ecology_view.py",
            ROOT / "components" / "life" / "scripts" / "v02_v03_daily_loop_bridge.py",
            ROOT / "tests" / "test_v02_waveb1_midscale_ecology.py",
        )
        for fp in owned_files:
            raw = fp.read_bytes()
            self.assertNotIn(b"\r\n", raw, f"CRLF found in {fp}")
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), f"UTF-8 BOM found in {fp}")

        new_files = (
            ROOT / "components" / "life" / "scripts" / "v02_midscale_ecology_engine.py",
            ROOT / "components" / "phone" / "v02_midscale_ecology_view.py",
        )
        for fp in new_files:
            src = fp.read_text(encoding="utf-8")
            self.assertIsNone(_EMOJI_RE.search(src), f"Emoji found in {fp}")
            self.assertNotIn("\u5343\u4ee3", src, f"Hardcoded character name found in {fp}")
            self.assertNotIn("\u5979", src, f"Gendered pronoun found in {fp}")

    def test_02_zero_write_get_empty_and_populated_roots(self) -> None:
        # 1. Empty root
        before_empty = _snapshot_tree(self.root)
        self.assertEqual(before_empty, {})

        p_code, p_body = eco_view.read_v02_ecology_growth_view(
            self.root, has_subject_capability=False, now_iso=T0
        )
        s_code, s_body = eco_view.read_v02_ecology_growth_view(
            self.root, has_subject_capability=True, now_iso=T0
        )
        loop_res = loop_bridge.collect_v02_v03_loop_candidates(self.root, now_iso=T0)

        self.assertEqual(p_code, 200)
        self.assertEqual(s_code, 200)
        self.assertEqual(p_body["llm_calls"], 0)
        self.assertEqual(s_body["llm_calls"], 0)
        self.assertTrue(p_body["zero_write"])
        self.assertFalse(p_body["state_exists"])
        self.assertEqual(p_body["perspective"], "partner")
        self.assertEqual(s_body["perspective"], "subject")
        self.assertEqual(p_body["absence_observations"], [])
        self.assertEqual(p_body["autobiography_opportunities"], [])
        self.assertEqual(p_body["ecology_chains"], [])
        self.assertEqual(p_body["partner_ecology_sparks"], [])
        self.assertEqual(p_body["practice_standard_evolutions"], [])
        self.assertEqual(loop_res["midscale_absence_observations"], [])
        self.assertEqual(loop_res["autobiography_spark_opportunities"], [])
        self.assertEqual(loop_res["resident_context_summary"]["midscale_absence_count"], 0)
        self.assertEqual(
            loop_res["resident_context_summary"]["autobiography_spark_opportunity_count"], 0
        )
        self.assertEqual(_snapshot_tree(self.root), before_empty)

        # 2. Populated root
        _write_json(
            self.root / "world" / "world_state.json",
            {
                "room_objects": [
                    {"item_id": "lamp_1", "title_cn": "小台灯", "place_cn": "窗台", "kind": "object"}
                ]
            },
        )
        c1, _ = eco_view.mutate_v02_ecology_growth_view(
            self.root,
            {"action": "snapshot_room_expectations", "operation_key": "zw_snap_1"},
            actor_side="subject",
            now_iso=T0,
        )
        self.assertEqual(c1, 200)
        before_pop = _snapshot_tree(self.root)

        c2, b2 = eco_view.read_v02_ecology_growth_view(
            self.root, has_subject_capability=False, now_iso=T1
        )
        c3, b3 = eco_view.read_v02_ecology_growth_view(
            self.root, has_subject_capability=True, now_iso=T1
        )
        loop_res_pop = loop_bridge.collect_v02_v03_loop_candidates(self.root, now_iso=T1)

        self.assertEqual(c2, 200)
        self.assertEqual(c3, 200)
        self.assertEqual(b2["room_expectations_count"], 1)
        self.assertEqual(b3["room_expectations_count"], 1)
        self.assertEqual(loop_res_pop["llm_calls"], 0)
        self.assertEqual(_snapshot_tree(self.root), before_pop)

    def test_03_f175_snapshot_move_remove_absence_and_partner_ack(self) -> None:
        world_path = self.root / "world" / "world_state.json"
        _write_json(
            world_path,
            {
                "room_objects": [
                    {"item_id": "desk_lamp", "title_cn": "折臂台灯", "place_cn": "窗台", "kind": "object"},
                    {"item_id": "sketchbook", "title_cn": "速写本", "place_cn": "书桌", "kind": "object"},
                    {"item_id": "tea_tin", "title_cn": "焙茶铁罐", "place_cn": "茶几", "kind": "object"},
                ]
            },
        )

        # Subject snapshots room expectations
        st, body = eco_view.mutate_v02_ecology_growth_view(
            self.root,
            {"action": "snapshot_room_expectations", "operation_key": "snap_room_01"},
            actor_side="subject",
            now_iso=T0,
        )
        self.assertEqual(st, 200)
        self.assertEqual(body["room_expectations_count"], 3)
        self.assertEqual(body["absence_observations"], [])

        # Live disk state changes: lamp moved, tea_tin removed, sketchbook untouched
        _write_json(
            world_path,
            {
                "room_objects": [
                    {"item_id": "desk_lamp", "title_cn": "折臂台灯", "place_cn": "书桌边", "kind": "object"},
                    {"item_id": "sketchbook", "title_cn": "速写本", "place_cn": "书桌", "kind": "object"},
                ]
            },
        )

        tree_before_detect = _snapshot_tree(self.root)
        st_read, read_body = eco_view.read_v02_ecology_growth_view(
            self.root, has_subject_capability=False, now_iso=T1
        )
        loop_out = loop_bridge.collect_v02_v03_loop_candidates(self.root, now_iso=T1)
        self.assertEqual(_snapshot_tree(self.root), tree_before_detect)

        self.assertEqual(st_read, 200)
        obs_by_item = {obs["item_id"]: obs for obs in read_body["absence_observations"]}
        self.assertEqual(set(obs_by_item.keys()), {"desk_lamp", "tea_tin"})

        lamp_obs = obs_by_item["desk_lamp"]
        self.assertEqual(lamp_obs["kind"], "MOVED_OBJECT")
        self.assertEqual(lamp_obs["expected_place_cn"], "窗台")
        self.assertEqual(lamp_obs["observed_place_cn"], "书桌边")
        self.assertEqual(lamp_obs["who_moved"], "UNKNOWN")
        self.assertFalse(lamp_obs["acknowledged_by_partner"])
        self.assertEqual(lamp_obs["partner_note_cn"], "")

        tea_obs = obs_by_item["tea_tin"]
        self.assertEqual(tea_obs["kind"], "MISSING_OBJECT")
        self.assertEqual(tea_obs["expected_place_cn"], "茶几")
        self.assertEqual(tea_obs["who_moved"], "UNKNOWN")

        # Bridge also exposes F-175 absences cleanly
        self.assertEqual(len(loop_out["midscale_absence_observations"]), 2)
        self.assertEqual(loop_out["resident_context_summary"]["midscale_absence_count"], 2)

        # Partner acknowledges lamp movement with a note; observation is preserved (never erased)
        ack_note = "刚才我帮你把台灯挪去书桌边充电了"
        st_ack, ack_body = eco_view.mutate_v02_ecology_growth_view(
            self.root,
            {
                "action": "acknowledge_absence_observation",
                "observation_id": lamp_obs["observation_id"],
                "note_cn": ack_note,
                "operation_key": "ack_lamp_01",
            },
            actor_side="partner",
            now_iso=T2,
        )
        self.assertEqual(st_ack, 200)
        self.assertEqual(ack_body["command_status"], "APPLIED")
        ack_map = {obs["item_id"]: obs for obs in ack_body["absence_observations"]}
        self.assertIn("desk_lamp", ack_map)
        self.assertTrue(ack_map["desk_lamp"]["acknowledged_by_partner"])
        self.assertEqual(ack_map["desk_lamp"]["partner_note_cn"], ack_note)
        # Never guesses mover from free text unless explicitly acknowledged
        self.assertEqual(ack_map["desk_lamp"]["who_moved"], "UNKNOWN")

        # Explicit who_moved acknowledgement is respected
        st_ack2, ack_body2 = eco_view.mutate_v02_ecology_growth_view(
            self.root,
            {
                "action": "acknowledge_absence_observation",
                "observation_id": lamp_obs["observation_id"],
                "note_cn": ack_note,
                "who_moved": "PARTNER",
                "operation_key": "ack_lamp_02",
            },
            actor_side="partner",
            now_iso=T2,
        )
        self.assertEqual(st_ack2, 200)
        ack_map2 = {obs["item_id"]: obs for obs in ack_body2["absence_observations"]}
        self.assertEqual(ack_map2["desk_lamp"]["who_moved"], "PARTNER")

    def test_04_f175_shop_closed_unexpected_and_private_record_visibility(self) -> None:
        v04_path = self.root / "world" / "v04_neighborhood_state.json"
        _write_json(
            v04_path,
            {
                "places": {
                    "bookstore": {
                        "place_id": "bookstore",
                        "name": "街角旧书店",
                        "open_window": "10:00-19:00",
                        "is_open": True,
                    }
                }
            },
        )
        st, _ = eco_view.mutate_v02_ecology_growth_view(
            self.root,
            {"action": "snapshot_room_expectations", "operation_key": "snap_v04_01"},
            actor_side="subject",
            now_iso=T1,
        )
        self.assertEqual(st, 200)

        # Shop unexpectedly closes during open window
        _write_json(
            v04_path,
            {
                "places": {
                    "bookstore": {
                        "place_id": "bookstore",
                        "name": "街角旧书店",
                        "open_window": "10:00-19:00",
                        "is_open": False,
                        "status": "closed_unexpected",
                    }
                }
            },
        )

        # Subject also records a private explicit absence observation
        st_priv, priv_body = eco_view.mutate_v02_ecology_growth_view(
            self.root,
            {
                "action": "record_absence_observation",
                "observation_id": "abs_priv_notebook",
                "item_id": "private_diary",
                "title_cn": "私人便签夹",
                "kind": "MISSING_OBJECT",
                "expected_place_cn": "床头抽屉",
                "shared": False,
                "operation_key": "rec_abs_priv_1",
            },
            actor_side="subject",
            now_iso=T1,
        )
        self.assertEqual(st_priv, 200)
        subj_ids = {obs["observation_id"] for obs in priv_body["absence_observations"]}
        self.assertIn("abs_priv_notebook", subj_ids)
        shop_obs = [obs for obs in priv_body["absence_observations"] if obs["kind"] == "SHOP_CLOSED_UNEXPECTED"]
        self.assertEqual(len(shop_obs), 1)
        self.assertEqual(shop_obs[0]["who_moved"], "UNKNOWN")

        # Partner view sees shop closure but NOT the private observation
        st_p, p_body = eco_view.read_v02_ecology_growth_view(
            self.root, has_subject_capability=False, now_iso=T1
        )
        self.assertEqual(st_p, 200)
        partner_ids = {obs["observation_id"] for obs in p_body["absence_observations"]}
        self.assertNotIn("abs_priv_notebook", partner_ids)
        self.assertEqual(len(partner_ids), 1)

        # Partner cannot acknowledge a hidden private observation (404, zero writes)
        snap_before_404 = _snapshot_tree(self.root)
        st_404, err_404 = eco_view.mutate_v02_ecology_growth_view(
            self.root,
            {
                "action": "acknowledge_absence_observation",
                "observation_id": "abs_priv_notebook",
                "note_cn": "探测私有项",
            },
            actor_side="partner",
            now_iso=T1,
        )
        self.assertEqual(st_404, 404)
        self.assertEqual(err_404["reason_code"], "OBSERVATION_NOT_FOUND")
        self.assertEqual(_snapshot_tree(self.root), snap_before_404)

    def test_05_f176_autobiography_episodes_to_opportunities_and_privacy(self) -> None:
        episodes_to_seed = [
            ("ep_paused_shared", "watercolor", "雨后窗景水彩草稿", "paused", "shared"),
            ("ep_failed_shared", "baking", "抹茶司康第一次试烤", "failed", "shared"),
            ("ep_ok_shared", "reading", "海边潮汐笔记整理", "succeeded", "shared"),
            ("ep_paused_priv", "journal", "未公开深夜日记提纲", "paused", "private"),
            ("ep_aband_priv", "music", "私人哼唱旋律草稿", "abandoned", "private"),
        ]
        for ep_id, dom, title, outcome, vis in episodes_to_seed:
            asp.record_autobiographical_episode(
                self.root,
                {
                    "episode_id": ep_id,
                    "period_id": "2026-11",
                    "domain": dom,
                    "title": title,
                    "summary": f"{title} 过程记录",
                    "outcome": outcome,
                    "visibility": vis,
                },
                now_iso=T0,
            )

        snap_before = _snapshot_tree(self.root)
        st_sub, sub_body = eco_view.read_v02_ecology_growth_view(
            self.root, has_subject_capability=True, now_iso=T1
        )
        st_par, par_body = eco_view.read_v02_ecology_growth_view(
            self.root, has_subject_capability=False, now_iso=T1
        )
        loop_out = loop_bridge.collect_v02_v03_loop_candidates(self.root, now_iso=T1)
        self.assertEqual(_snapshot_tree(self.root), snap_before)

        self.assertEqual(st_sub, 200)
        self.assertEqual(st_par, 200)

        sub_opps = sub_body["autobiography_opportunities"]
        par_opps = par_body["autobiography_opportunities"]
        self.assertEqual(len(sub_opps), 5)
        self.assertEqual(len(par_opps), 3)

        # Every opportunity is gentle and zero_debt
        for opp in sub_opps:
            self.assertTrue(opp["zero_debt"])
            self.assertIn("idea_title_cn", opp)
            self.assertIn("why_now_cn", opp)

        # Partner view never contains private episodes
        par_ep_ids = {opp["source_episode_id"] for opp in par_opps}
        self.assertEqual(par_ep_ids, {"ep_paused_shared", "ep_failed_shared", "ep_ok_shared"})
        self.assertNotIn("ep_paused_priv", par_ep_ids)
        self.assertNotIn("ep_aband_priv", par_ep_ids)
        self.assertNotIn("未公开深夜日记提纲", json.dumps(par_body, ensure_ascii=False))

        # Paused/failed prioritized ahead of succeeded
        self.assertEqual(par_opps[0]["source_outcome"], "paused")
        self.assertEqual(par_opps[1]["source_outcome"], "failed")
        self.assertEqual(par_opps[2]["source_outcome"], "succeeded")

        # Bridge exposes the same autobiography spark opportunities zero-write
        self.assertEqual(len(loop_out["autobiography_spark_opportunities"]), 5)
        self.assertEqual(
            loop_out["resident_context_summary"]["autobiography_spark_opportunity_count"], 5
        )

    def test_06_f177_multistep_ecology_chain_and_partner_optional_spark(self) -> None:
        steps = [
            ("reading", "book:tide_almanac", "翻完潮汐小历第三章"),
            ("inquiry", "inq_0001", "查清楚灰蓝颜料的叠色发灰原因"),
            ("craft", "draft:seawall_card", "试画一张海堤雾蓝小卡"),
            ("share", "share:card_01", "挑出顺眼的一版留给对方看"),
        ]
        chain_id: str | None = None
        for idx, (kind, ref_id, title_cn) in enumerate(steps, start=1):
            payload: dict[str, Any] = {
                "action": "record_ecology_chain_link",
                "step_kind": kind,
                "ref_id": ref_id,
                "title_cn": title_cn,
                "theme_cn": "海堤雾蓝小卡线索",
                "shared": True,
                "operation_key": f"chain_step_{idx}",
            }
            if chain_id is not None:
                payload["chain_id"] = chain_id
            st, body = eco_view.mutate_v02_ecology_growth_view(
                self.root, payload, actor_side="subject", now_iso=T0
            )
            self.assertEqual(st, 200)
            chains = body["ecology_chains"]
            self.assertEqual(len(chains), 1)
            chain_id = chains[0]["chain_id"]
            self.assertEqual(chains[0]["latest_step_kind"], kind)
            self.assertEqual(
                chains[0]["suggested_next_step_cn"],
                eco_engine.SUGGESTED_NEXT_STEP_BY_KIND[kind],
            )

        # Add a private chain that must stay hidden from partner
        st_priv, _ = eco_view.mutate_v02_ecology_growth_view(
            self.root,
            {
                "action": "record_ecology_chain_link",
                "step_kind": "palette",
                "ref_id": "pal_secret_1",
                "title_cn": "私人深夜配色草稿",
                "shared": False,
                "operation_key": "chain_priv_1",
            },
            actor_side="subject",
            now_iso=T1,
        )
        self.assertEqual(st_priv, 200)

        # Partner drops an optional zero-debt spark (never forces adoption)
        st_sp, sp_body = eco_view.mutate_v02_ecology_growth_view(
            self.root,
            {
                "action": "suggest_ecology_spark",
                "theme_cn": "周末河岸旧书摊",
                "spark_text_cn": "路过河边看到新摆出几本插画旧刊，改天有兴致可以随便逛一眼，不去也没事",
                "linked_kind": "outing",
                "operation_key": "spark_01",
            },
            actor_side="partner",
            now_iso=T2,
        )
        self.assertEqual(st_sp, 200)
        self.assertEqual(len(sp_body["partner_ecology_sparks"]), 1)
        spark = sp_body["partner_ecology_sparks"][0]
        self.assertEqual(spark["status"], "OPTIONAL_SPARK")
        self.assertTrue(spark["zero_debt"])
        self.assertTrue(spark["non_coercive"])
        self.assertFalse(spark["auto_adopted"])
        self.assertTrue(spark["shared"])
        self.assertEqual(spark["linked_kind"], "outing")

        # Partner sees only the shared 4-step chain (not the private chain)
        self.assertEqual(len(sp_body["ecology_chains"]), 1)
        self.assertEqual(
            sp_body["ecology_chains"][0]["step_kinds_path"],
            ["reading", "inquiry", "craft", "share"],
        )

        # Subject sees both chains
        st_s, s_body = eco_view.read_v02_ecology_growth_view(
            self.root, has_subject_capability=True, now_iso=T2
        )
        self.assertEqual(st_s, 200)
        self.assertEqual(len(s_body["ecology_chains"]), 2)

    def test_07_f178_practice_standard_evolution_and_provenance_crossmodal_proposals(self) -> None:
        # 1. Subject records an explicit practice-driven evaluation standard shift
        st_rec, rec_body = eco_view.mutate_v02_ecology_growth_view(
            self.root,
            {
                "action": "record_practice_standard_evolution",
                "domain": "watercolor_palette",
                "before_criterion_cn": "以前觉得只要颜色不脏、轮廓画准就算过关",
                "after_criterion_cn": "现在更在意留白呼吸感和明暗过渡是否自然",
                "practice_evidence_count": 4,
                "evidence_refs": ["pal_01", "pal_02", "draft_03", "draft_04"],
                "shared": True,
                "operation_key": "std_evo_01",
            },
            actor_side="subject",
            now_iso=T0,
        )
        self.assertEqual(st_rec, 200)
        self.assertEqual(len(rec_body["practice_standard_evolutions"]), 1)
        recorded = rec_body["practice_standard_evolutions"][0]
        self.assertEqual(recorded["change_category"], "EVALUATION_STANDARD_SHIFT")
        self.assertTrue(recorded["distinguishes_standard_shift_from_success_rate"])
        self.assertTrue(recorded["not_mere_success_rate_gain"])
        self.assertIn("眼光/标准变了", recorded["distinction_note_cn"])
        self.assertIn("成功率提高了", recorded["distinction_note_cn"])

        # 2. Seed skill release (trials >= 3) + shared/private method_patches in provenance
        asp.register_skill_release(
            self.root,
            {
                "method_id": "wash_layering",
                "title_cn": "湿画法叠色",
                "tool_version": "v1",
                "model_id": "local_base",
                "scope": "watercolor",
                "domain": "watercolor",
                "attribution": asp.SKILL_ATTRIBUTION_PRACTICED_MASTERY,
                "criteria_id": "wc_rubric",
                "standard_revision": "rev1",
                "status": asp.SKILL_STATUS_TRIAL,
                "trials": 5,
                "successes": 4,
                "success_rate": 0.8,
            },
            now_iso=T0,
        )
        asp.record_targeted_method_patch(
            self.root,
            {
                "method_id": "wash_layering",
                "scope": "watercolor",
                "failure_symptom_cn": "第二层颜料把底层搅浑",
                "root_cause_hypothesis": "纸面未半干就落笔",
                "method_change_cn": "等待纸面光泽收平后再叠第二层",
                "shared": True,
            },
            now_iso=T0,
        )
        asp.record_targeted_method_patch(
            self.root,
            {
                "method_id": "private_diary_sort",
                "scope": "private_notes",
                "failure_symptom_cn": "私人笔记标签太碎",
                "root_cause_hypothesis": "随手起名太多",
                "method_change_cn": "只保留三个常用分类",
                "shared": False,
            },
            now_iso=T0,
        )

        # 3. Seed phone/v02_crossmodal.json with 2 shared palette_studies
        _write_json(
            self.root / "phone" / "v02_crossmodal.json",
            {
                "schema": "nyairo.v02.crossmodal.v1",
                "revision": 2,
                "palette_studies": [
                    {
                        "study_id": "pal_0001",
                        "title": "晨雾石桥",
                        "shared": True,
                        "dominant_hex": "#7A8FA6",
                        "created_at": T0,
                    },
                    {
                        "study_id": "pal_0002",
                        "title": "傍晚窗灯",
                        "shared": True,
                        "dominant_hex": "#D6A16B",
                        "created_at": T1,
                    },
                ],
            },
        )

        snap_before = _snapshot_tree(self.root)
        subj_props = eco_engine.evaluate_practice_driven_standard_proposals_zero_write(
            self.root, has_subject_capability=True, now_iso=T2
        )
        par_props = eco_engine.evaluate_practice_driven_standard_proposals_zero_write(
            self.root, has_subject_capability=False, now_iso=T2
        )
        self.assertEqual(_snapshot_tree(self.root), snap_before)

        subj_sources = {item["source"] for item in subj_props}
        self.assertEqual(
            subj_sources,
            {
                "subject_recorded",
                "skill_provenance_trials",
                "skill_method_patches",
                "crossmodal_palette_studies",
            },
        )
        # Private method patch surfaces for subject, hidden for partner
        subj_domains = {item["domain"] for item in subj_props}
        par_domains = {item["domain"] for item in par_props}
        self.assertIn("private_notes", subj_domains)
        self.assertNotIn("private_notes", par_domains)
        self.assertIn("color_palette_taste", par_domains)

    def test_08_security_actor_side_boundaries_and_forbidden_keys(self) -> None:
        before = _snapshot_tree(self.root)

        # Partner calling all 4 subject-only actions -> 403 FORBIDDEN_ACTOR_SIDE
        subject_only_payloads = (
            {"action": "snapshot_room_expectations"},
            {
                "action": "record_absence_observation",
                "item_id": "cup_1",
                "title_cn": "马克杯",
                "kind": "MISSING_OBJECT",
                "expected_place_cn": "书桌",
            },
            {
                "action": "record_ecology_chain_link",
                "step_kind": "reading",
                "ref_id": "book:1",
                "title_cn": "读完一章",
            },
            {
                "action": "record_practice_standard_evolution",
                "domain": "sketch",
                "before_criterion_cn": "画完就行",
                "after_criterion_cn": "线条有轻重节奏",
                "practice_evidence_count": 3,
            },
        )
        for payload in subject_only_payloads:
            code, err = eco_view.mutate_v02_ecology_growth_view(
                self.root, payload, actor_side="partner", now_iso=T0
            )
            self.assertEqual(code, 403, f"Expected 403 for {payload['action']}")
            self.assertEqual(err["reason_code"], "FORBIDDEN_ACTOR_SIDE")
            self.assertEqual(err["llm_calls"], 0)

        # Forged identity / score / coercion keys -> 400 INVALID_FIELDS
        forged_payloads = (
            {
                "action": "suggest_ecology_spark",
                "theme_cn": "散步",
                "spark_text_cn": "河边逛逛",
                "actor_side": "subject",
            },
            {
                "action": "suggest_ecology_spark",
                "theme_cn": "散步",
                "spark_text_cn": "河边逛逛",
                "has_subject_capability": True,
            },
            {
                "action": "suggest_ecology_spark",
                "theme_cn": "散步",
                "spark_text_cn": "河边逛逛",
                "intimacy_score": 99,
            },
            {
                "action": "suggest_ecology_spark",
                "theme_cn": "散步",
                "spark_text_cn": "河边逛逛",
                "debt_score": 10,
            },
            {
                "action": "snapshot_room_expectations",
                "items": [
                    {
                        "item_id": "x1",
                        "title_cn": "灯",
                        "expected_place_cn": "桌",
                        "obedience_score": 100,
                    }
                ],
            },
        )
        for fp_payload in forged_payloads:
            actor = "subject" if fp_payload["action"] == "snapshot_room_expectations" else "partner"
            code, err = eco_view.mutate_v02_ecology_growth_view(
                self.root, fp_payload, actor_side=actor, now_iso=T0
            )
            self.assertEqual(code, 400, f"Expected 400 for forged payload {fp_payload}")
            self.assertEqual(err["reason_code"], "INVALID_FIELDS")

        # Invalid enum / count / unknown field -> 400 INVALID_FIELDS
        bad_payloads = (
            ("partner", {"action": "unknown_action"}),
            ("partner", {"action": "suggest_ecology_spark", "theme_cn": "A", "spark_text_cn": "B", "extra": 1}),
            ("partner", {"action": "suggest_ecology_spark", "theme_cn": "A", "spark_text_cn": "B", "linked_kind": "invalid"}),
            ("subject", {"action": "record_ecology_chain_link", "step_kind": "invalid", "ref_id": "r1", "title_cn": "T"}),
            (
                "subject",
                {
                    "action": "record_practice_standard_evolution",
                    "domain": "d",
                    "before_criterion_cn": "a",
                    "after_criterion_cn": "b",
                    "practice_evidence_count": 0,
                },
            ),
            (
                "subject",
                {
                    "action": "record_practice_standard_evolution",
                    "domain": "d",
                    "before_criterion_cn": "a",
                    "after_criterion_cn": "b",
                    "practice_evidence_count": True,
                },
            ),
        )
        for actor, bad_p in bad_payloads:
            code, err = eco_view.mutate_v02_ecology_growth_view(
                self.root, bad_p, actor_side=actor, now_iso=T0
            )
            self.assertEqual(code, 400, f"Expected 400 for {bad_p}")
            self.assertEqual(err["reason_code"], "INVALID_FIELDS")

        # Zero files or locks created on any rejected request!
        self.assertEqual(_snapshot_tree(self.root), before)

    def test_09_operation_key_idempotency_replay_conflict_and_zero_write_preflight(self) -> None:
        first_payload = {
            "action": "suggest_ecology_spark",
            "theme_cn": "晚风散步",
            "spark_text_cn": "听说巷口新开了手工纸店，有空顺路看看就行",
            "linked_kind": "outing",
            "operation_key": "op_spark_idem_1",
        }
        c1, b1 = eco_view.mutate_v02_ecology_growth_view(
            self.root, first_payload, actor_side="partner", now_iso=T0
        )
        self.assertEqual(c1, 200)
        self.assertEqual(b1["status"], "applied")
        self.assertEqual(b1["command_status"], "APPLIED")
        self.assertFalse(b1["idempotent_replay"])
        self.assertEqual(b1["revision"], 1)
        self.assertEqual(len(b1["partner_ecology_sparks"]), 1)

        snap_after_first = _snapshot_tree(self.root)

        # Identical replay -> DUPLICATE, zero disk writes
        c2, b2 = eco_view.mutate_v02_ecology_growth_view(
            self.root, first_payload, actor_side="partner", now_iso=T1
        )
        self.assertEqual(c2, 200)
        self.assertEqual(b2["status"], "already_applied")
        self.assertEqual(b2["command_status"], "DUPLICATE")
        self.assertTrue(b2["idempotent_replay"])
        self.assertEqual(b2["revision"], 1)
        self.assertEqual(len(b2["partner_ecology_sparks"]), 1)
        self.assertEqual(_snapshot_tree(self.root), snap_after_first)

        # Conflicting payload with same operation_key -> 409 OPERATION_KEY_CONFLICT, zero disk writes
        conflict_payload = dict(first_payload)
        conflict_payload["spark_text_cn"] = "完全不同的另一段话"
        c3, b3 = eco_view.mutate_v02_ecology_growth_view(
            self.root, conflict_payload, actor_side="partner", now_iso=T2
        )
        self.assertEqual(c3, 409)
        self.assertEqual(b3["reason_code"], "OPERATION_KEY_CONFLICT")
        self.assertEqual(_snapshot_tree(self.root), snap_after_first)

    def test_10_host_path_stripping_in_projections(self) -> None:
        st, body = eco_view.mutate_v02_ecology_growth_view(
            self.root,
            {
                "action": "record_ecology_chain_link",
                "step_kind": "craft",
                "ref_id": "draft:01",
                "title_cn": "参考 /home/tester/secret/ref.png 和 D:\\private\\work\\a.txt 做的色卡",
                "shared": True,
                "operation_key": "op_path_strip_1",
            },
            actor_side="subject",
            now_iso=T0,
        )
        self.assertEqual(st, 200)
        serialized = json.dumps(body, ensure_ascii=False)
        self.assertNotIn("/home/tester/secret", serialized)
        self.assertNotIn("D:\\\\private", serialized)
        self.assertNotIn("D:\\private", serialized)


if __name__ == "__main__":
    unittest.main()

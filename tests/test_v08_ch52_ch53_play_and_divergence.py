#!/usr/bin/env python3
"""v0.8 Ch.53 §二 fictional-frame isolation & Ch.52 §五 work-bound divergence memo tests."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "components" / "life" / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from components.life.scripts import v04_elastic_play_learning as ep_engine  # noqa: E402
from components.phone import v04_elastic_play_view as ep_view  # noqa: E402


def _snap(root: Path) -> dict[str, tuple[int, int, str]]:
    if not root.exists():
        return {}
    out: dict[str, tuple[int, int, str]] = {}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        st = p.lstat()
        if p.is_file() and not p.is_symlink():
            out[rel] = (
                int(st.st_size),
                int(st.st_mtime_ns),
                hashlib.sha256(p.read_bytes()).hexdigest(),
            )
        else:
            out[rel] = (0, int(st.st_mtime_ns), "dir")
    return out


class Ch53FictionalFrameAndCh52DivergenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="v08-ch52-ch53-"))
        self.day_root = self.tmp / "day"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- helpers -----------------------------------------------------------------
    def _seed_world(self) -> tuple[Path, Path]:
        world = self.day_root / "world"
        world.mkdir(parents=True, exist_ok=True)
        matter = world / "v08_matter_space.json"
        neigh = world / "v04_neighborhood_state.json"
        matter.write_bytes(
            json.dumps(
                {
                    "schema": "nyairo.v08.matter_space.v1",
                    "matter_objects": {
                        "ceramic_mug": {
                            "object_id": "ceramic_mug",
                            "place_id": "study_desk",
                            "zone_id": "desk",
                        }
                    },
                },
                ensure_ascii=False,
            ).encode("utf-8")
        )
        neigh.write_bytes(
            json.dumps(
                {
                    "schema": "nyairo.v04.neighborhood.v1",
                    "visited_place_ids": [],
                    "journeys": [],
                },
                ensure_ascii=False,
            ).encode("utf-8")
        )
        return matter, neigh

    def _mutate(self, payload: dict, *, actor_side: str, now_iso: str | None = None):
        return ep_view.mutate_v04_elastic_play_view(
            self.day_root, payload, actor_side=actor_side, now_iso=now_iso
        )

    # ---- 1. Ch.53 §二 fictional frame isolation -----------------------------------
    def test_01_fictional_frame_never_mutates_world(self) -> None:
        matter, neigh = self._seed_world()
        matter_before = matter.read_bytes()
        neigh_before = neigh.read_bytes()

        code, body = self._mutate(
            {
                "action": "play_temporary_game",
                "game_op": "start",
                "game_kind": "object_logbook",
                "prop_label": "搪瓷杯",
                "rule_summary": "给杯子写一段航海日志",
                "fictional_prop_object_id": "ceramic_mug",
                "fictional_role_cn": "桌面飞船",
            },
            actor_side="partner",
            now_iso="2026-11-16T10:00:00+08:00",
        )
        self.assertEqual(code, 200)
        tg = body["data"]["temporary_game"]
        self.assertTrue(tg["active"])
        self.assertEqual(tg["fictional_prop_object_id"], "ceramic_mug")
        self.assertEqual(tg["fictional_role_cn"], "桌面飞船")
        self.assertIs(tg["fantasy_scope_isolated"], True)
        self.assertIs(tg["world_object_mutated"], False)
        self.assertIs(tg["world_travel_fabricated"], False)
        self.assertEqual(
            tg["fantasy_boundary_note_cn"],
            "假想设定只在这次十分钟小游戏里有效，屋里的真物件保持原样",
        )
        self.assertEqual(body["data"]["llm_calls"], 0)

        # A turn may describe flying across the desk nebula -- still fiction only.
        code, body = self._mutate(
            {
                "action": "play_temporary_game",
                "game_op": "play_turn",
                "turn_text": "这艘船正穿过桌面星云，杯子还稳稳放在桌角",
            },
            actor_side="subject",
            now_iso="2026-11-16T10:02:00+08:00",
        )
        self.assertEqual(code, 200)
        turn = body["data"]["temporary_game"]["turns"][-1]
        self.assertIs(turn["fantasy_scope_isolated"], True)
        self.assertIs(turn["world_object_mutated"], False)
        self.assertIs(turn["world_travel_fabricated"], False)

        # Saving a keepsake carries the fiction labels and isolation flags.
        code, body = self._mutate(
            {
                "action": "play_temporary_game",
                "game_op": "save_keepsake",
                "keepsake_title": "杯子飞船的航海日志",
            },
            actor_side="partner",
            now_iso="2026-11-16T10:04:00+08:00",
        )
        self.assertEqual(code, 200)
        keepsake = body["data"]["temporary_game"]["keepsakes"][0]
        self.assertEqual(keepsake["fictional_prop_object_id"], "ceramic_mug")
        self.assertEqual(keepsake["fictional_role_cn"], "桌面飞船")
        self.assertIs(keepsake["fantasy_scope_isolated"], True)
        self.assertIs(keepsake["world_object_mutated"], False)
        self.assertIs(keepsake["world_travel_fabricated"], False)
        self.assertEqual(body["data"]["game_keepsakes"][0]["fantasy_scope_isolated"], True)

        # Stop clears the active temporary rule set.
        code, body = self._mutate(
            {"action": "play_temporary_game", "game_op": "stop"},
            actor_side="partner",
            now_iso="2026-11-16T10:05:00+08:00",
        )
        self.assertEqual(code, 200)
        stopped_tg = body["data"]["temporary_game"]
        self.assertIs(stopped_tg["active"], False)
        self.assertEqual(stopped_tg["status"], "idle")
        self.assertEqual(stopped_tg["rule"], "")

        # Hard isolation invariant: real world stores are byte-identical.
        self.assertEqual(matter.read_bytes(), matter_before)
        self.assertEqual(neigh.read_bytes(), neigh_before)

        # Zero-write read path around the whole projection.
        before = _snap(self.tmp)
        ep_view.read_v04_elastic_play_view(self.day_root, has_subject_capability=True)
        ep_engine.project_v04_elastic_play(self.day_root, has_subject_capability=False)
        self.assertEqual(before, _snap(self.tmp))

    # ---- 2. Ch.52 §五 work-bound divergence memo ---------------------------------
    def test_02_work_bound_divergence_recalls_only_that_work(self) -> None:
        code, body = self._mutate(
            {
                "action": "propose_or_respond_experiment",
                "exp_op": "propose",
                "experiment_id": "exp-autumn",
                "title": "开头要不要更直接",
                "question": "不读后文，能否看懂是谁在说话？",
                "original_snippet": "那年秋天的信，她一直没寄。",
                "candidate_snippet": "那年秋天，她写了一封信，没有寄。",
                "target_work_ref": "artifact:autumn_draft",
            },
            actor_side="subject",
            now_iso="2026-11-16T11:00:00+08:00",
        )
        self.assertEqual(code, 200)
        proposed = next(
            x
            for x in body["data"]["experiments"]
            if x["experiment_id"] == "exp-autumn"
        )
        self.assertEqual(proposed["target_work_ref"], "artifact:autumn_draft")
        self.assertEqual(body["data"]["llm_calls"], 0)

        code, body = self._mutate(
            {
                "action": "propose_or_respond_experiment",
                "exp_op": "pause_keep_both",
                "experiment_id": "exp-autumn",
                "target_work_ref": "artifact:autumn_draft",
                "divergence_memo_cn": "原版更含蓄，改版更清楚，先不替换",
            },
            actor_side="subject",
            now_iso="2026-11-16T11:05:00+08:00",
        )
        self.assertEqual(code, 200)
        memo_row = next(
            x
            for x in body["data"]["experiments"]
            if x["experiment_id"] == "exp-autumn"
        )
        self.assertEqual(memo_row["status"], "kept_both_unreplaced")
        self.assertEqual(memo_row["outcome"], "原版更含蓄，改版更清楚，先不替换")
        self.assertEqual(memo_row["divergence_memo_cn"], "原版更含蓄，改版更清楚，先不替换")
        self.assertIs(memo_row["nag_daily"], False)
        self.assertEqual(memo_row["affection_penalty"], 0)
        self.assertEqual(memo_row["reopen_trigger_work_ref"], "artifact:autumn_draft")

        # Second, differently-bound divergence via the alias and the default memo.
        code, _ = self._mutate(
            {
                "action": "propose_or_respond_experiment",
                "exp_op": "propose",
                "experiment_id": "exp-spring",
                "title": "结尾要不要再收一点",
                "target_work_ref": "artifact:spring_letter",
            },
            actor_side="partner",
            now_iso="2026-11-16T11:10:00+08:00",
        )
        self.assertEqual(code, 200)
        code, body = self._mutate(
            {
                "action": "propose_or_respond_experiment",
                "exp_op": "shelve_divergence",
                "experiment_id": "exp-spring",
            },
            actor_side="partner",
            now_iso="2026-11-16T11:12:00+08:00",
        )
        self.assertEqual(code, 200)
        spring_row = next(
            x for x in body["data"]["experiments"] if x["experiment_id"] == "exp-spring"
        )
        self.assertEqual(spring_row["status"], "kept_both_unreplaced")
        self.assertEqual(spring_row["divergence_memo_cn"], "原版更含蓄，改版更清楚，先不替换")
        self.assertIs(spring_row["nag_daily"], False)
        self.assertEqual(spring_row["affection_penalty"], 0)

        # Summary surfaces both, still non-nagging.
        summary = body["data"]["unresolved_bound_divergences"]
        self.assertEqual(summary["count"], 2)
        self.assertIs(summary["nag_daily"], False)
        for row in summary["rows"]:
            self.assertIs(row["nag_daily"], False)
            self.assertEqual(row["affection_penalty"], 0)

        # Zero-write helper: only the exact work sees its memo.
        before = _snap(self.tmp)
        autumn_rows = ep_engine.project_divergence_for_work_zero_write(
            self.day_root, "artifact:autumn_draft"
        )
        spring_rows = ep_engine.project_divergence_for_work_zero_write(
            self.day_root, "artifact:spring_letter"
        )
        other_rows = ep_engine.project_divergence_for_work_zero_write(
            self.day_root, "artifact:unrelated_work"
        )
        blank_rows = ep_engine.project_divergence_for_work_zero_write(self.day_root, "")
        self.assertEqual([r["experiment_id"] for r in autumn_rows], ["exp-autumn"])
        self.assertEqual([r["experiment_id"] for r in spring_rows], ["exp-spring"])
        self.assertEqual(other_rows, [])
        self.assertEqual(blank_rows, [])
        for row in autumn_rows:
            self.assertEqual(row["reopen_trigger_work_ref"], "artifact:autumn_draft")
            self.assertIs(row["nag_daily"], False)
            self.assertEqual(row["affection_penalty"], 0)
            self.assertEqual(row["llm_calls"], 0)
        self.assertEqual(before, _snap(self.tmp), "divergence reads must be zero-write")

    # ---- 3. Whitelist / RBAC integrity & no LLM calls ----------------------------
    def test_03_unknown_and_forbidden_fields_still_rejected(self) -> None:
        bad_payloads = [
            # forbidden identity/score key on the newly widened actions
            {
                "action": "play_temporary_game",
                "game_op": "start",
                "game_kind": "odd_naming",
                "prop_label": "杯子",
                "uid": "root",
            },
            {
                "action": "propose_or_respond_experiment",
                "exp_op": "propose",
                "title": "写法对比",
                "intimacy_score": 99,
            },
            # unknown fields on the newly widened actions
            {
                "action": "play_temporary_game",
                "game_op": "start",
                "game_kind": "odd_naming",
                "prop_label": "杯子",
                "fictional_city_id": "desk-city",
            },
            {
                "action": "propose_or_respond_experiment",
                "exp_op": "propose",
                "title": "写法对比",
                "target_work_reference": "artifact:x",
            },
            # forbidden key nested inside an allowed object-shaped field
            {
                "action": "play_temporary_game",
                "game_op": "play_turn",
                "turn_text": "船继续开",
                "meta": {"actor_side": "subject"},
            },
        ]
        for payload in bad_payloads:
            code, resp = self._mutate(payload, actor_side="partner")
            self.assertEqual(code, 400, msg=f"payload should be rejected: {payload}")
            self.assertEqual(resp["reason_code"], "INVALID_FIELDS")

        # RBAC still intact for an action outside the partner whitelist.
        code, resp = self._mutate(
            {"action": "set_day_direction", "direction": "午后整理书架"},
            actor_side="partner",
        )
        self.assertEqual(code, 403)
        self.assertEqual(resp["reason_code"], "FORBIDDEN_ACTOR_SIDE")

        # A clean call still works with llm_calls == 0.
        code, body = self._mutate(
            {
                "action": "play_temporary_game",
                "game_op": "start",
                "game_kind": "object_logbook",
                "prop_label": "杯子",
                "fictional_prop_object_id": "ceramic_mug",
                "fictional_role_cn": "桌面飞船",
            },
            actor_side="partner",
        )
        self.assertEqual(code, 200)
        self.assertEqual(body["data"]["llm_calls"], 0)


if __name__ == "__main__":
    unittest.main()

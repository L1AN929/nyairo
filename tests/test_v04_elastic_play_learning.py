#!/usr/bin/env python3
"""Unit & boundary tests for v0.4 Ch. 45–53 Elastic Direction, Co-Experiment, Scoped Teaching & Playfulness."""

from __future__ import annotations

import hashlib
import json
import os
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
            out[rel] = (int(st.st_size), int(st.st_mtime_ns), hashlib.sha256(p.read_bytes()).hexdigest())
        else:
            out[rel] = (0, int(st.st_mtime_ns), "dir")
    return out


class V04ElasticPlayLearningTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="v04-elastic-play-"))
        self.day_root = self.tmp / "day"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_01_zero_write_read_and_character_neutrality(self) -> None:
        before = _snap(self.tmp)
        code, body = ep_view.read_v04_elastic_play_view(
            self.day_root,
            has_subject_capability=False,
            now_iso="2026-11-13T09:00:00+08:00",
        )
        hint = ep_engine.build_v04_elastic_turn_hint(self.day_root)
        after = _snap(self.tmp)
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["data"]["schema"], "phone.v04.elastic_play.v1")
        self.assertEqual(body["data"]["identity"], {"display_name": "TA", "pronouns": "TA"})
        self.assertEqual(body["data"]["llm_calls"], 0)
        self.assertEqual(hint, "")
        self.assertEqual(before, after, "GET and turn_hint must be 100% zero-write")

        for py_path in (
            ROOT / "components" / "life" / "scripts" / "v04_elastic_play_learning.py",
            ROOT / "components" / "phone" / "v04_elastic_play_view.py",
        ):
            src = py_path.read_text(encoding="utf-8")
            self.assertNotIn("千代", src)

    def test_02_day_direction_rbac_interrupt_and_receipt_guard(self) -> None:
        c403, r403 = ep_view.mutate_v04_elastic_play_view(
            self.day_root,
            {"action": "set_day_direction", "direction": "午后整理书架"},
            actor_side="partner",
        )
        self.assertEqual(c403, 403)
        self.assertEqual(r403["reason_code"], "FORBIDDEN_ACTOR_SIDE")

        c200, r200 = ep_view.mutate_v04_elastic_play_view(
            self.day_root,
            {
                "action": "set_day_direction",
                "direction": "午后整理书签",
                "optional_directions": ["午后整理书签", "傍晚散步看云"],
                "operation_key": "dir-op-1",
            },
            actor_side="subject",
        )
        self.assertEqual(c200, 200)
        self.assertFalse(r200["data"]["day_direction"]["pre_scheduled"])
        self.assertEqual(r200["data"]["day_direction"]["direction"], "午后整理书签")

        # Duplicate replay returns DUPLICATE without bumping revision
        c_dup, r_dup = ep_view.mutate_v04_elastic_play_view(
            self.day_root,
            {
                "action": "set_day_direction",
                "direction": "午后整理书签",
                "optional_directions": ["午后整理书签", "傍晚散步看云"],
                "operation_key": "dir-op-1",
            },
            actor_side="subject",
        )
        self.assertEqual(c_dup, 200)
        self.assertEqual(r_dup["command_status"], "DUPLICATE")
        self.assertEqual(r_dup["data"]["revision"], r200["data"]["revision"])

        # Missing receipt_ref on record_completed -> 409
        c409, r409 = ep_view.mutate_v04_elastic_play_view(
            self.day_root,
            {
                "action": "set_day_direction",
                "mode": "record_completed",
                "completed_summary": "把书签按季节夹好了",
            },
            actor_side="subject",
        )
        self.assertEqual(c409, 409)
        self.assertEqual(r409["reason_code"], "MISSING_COMPLETION_RECEIPT")

        # With real receipt_ref -> 200
        c_done, r_done = ep_view.mutate_v04_elastic_play_view(
            self.day_root,
            {
                "action": "set_day_direction",
                "mode": "record_completed",
                "completed_summary": "把书签按季节夹好了",
                "receipt_ref": "note:bookmark-20261113",
            },
            actor_side="subject",
        )
        self.assertEqual(c_done, 200)
        self.assertEqual(len(r_done["data"]["day_direction"]["completed_items"]), 1)

        # Natural interruption yields without failure debt
        c_int, r_int = ep_view.mutate_v04_elastic_play_view(
            self.day_root,
            {"action": "set_day_direction", "interrupt_reason": "伴侣临时来聊天，顺势停下手头整理"},
            actor_side="subject",
        )
        self.assertEqual(c_int, 200)
        self.assertEqual(r_int["data"]["day_direction"]["status"], "yielded_naturally")

    def test_03_shared_notebook_concurrent_dual_branch_and_reading_notice(self) -> None:
        c1, r1 = ep_view.mutate_v04_elastic_play_view(
            self.day_root,
            {"action": "append_shared_notebook", "text": "第一句：周日去河堤走走"},
            actor_side="partner",
        )
        self.assertEqual(c1, 200)
        self.assertEqual(r1["data"]["shared_notebook"]["head_revision"], 1)

        # Subject appends against head 1 -> head becomes 2
        c2, _ = ep_view.mutate_v04_elastic_play_view(
            self.day_root,
            {"action": "append_shared_notebook", "text": "主体同页补了一句：带上保温杯", "expected_head_revision": 1},
            actor_side="subject",
        )
        self.assertEqual(c2, 200)

        # Partner concurrently appends with stale expected_head_revision=1 -> preserved side-by-side!
        c3, r3 = ep_view.mutate_v04_elastic_play_view(
            self.day_root,
            {"action": "append_shared_notebook", "text": "伴侣并发添了一句：顺路买烤红薯", "expected_head_revision": 1},
            actor_side="partner",
        )
        self.assertEqual(c3, 200)
        sn = r3["data"]["shared_notebook"]
        self.assertTrue(sn["has_concurrent_branch"])
        self.assertEqual(len(sn["entries"]), 3)
        self.assertEqual(len(sn["branches"]), 1)

        # Reading notice
        crn, rrn = ep_view.mutate_v04_elastic_play_view(
            self.day_root,
            {"action": "share_reading_notice", "material_title": "海堤漫步录", "page": 42, "line_note": "风从潮水退去的地方吹来"},
            actor_side="partner",
        )
        self.assertEqual(crn, 200)
        self.assertEqual(rrn["data"]["reading_notices"][0]["page"], 42)

    def test_04_experiments_private_two_phrasings_scoped_prefs_and_practice(self) -> None:
        # Private two phrasings: subject saves, hidden from partner
        cp, _ = ep_view.mutate_v04_elastic_play_view(
            self.day_root,
            {
                "action": "save_two_phrasings",
                "context_note": "开头第一句",
                "phrasing_a": "傍晚的雨停在屋檐边。",
                "phrasing_b": "天快黑时，檐角最后一滴水落下来。",
            },
            actor_side="subject",
        )
        self.assertEqual(cp, 200)
        _, pub_view = ep_view.read_v04_elastic_play_view(self.day_root, has_subject_capability=False)
        self.assertEqual(pub_view["data"]["private_two_phrasings"], [])
        self.assertTrue(pub_view["data"]["private_two_phrasings_withheld"])

        # Co-experiment lifecycle without scoreboard
        ce1, re1 = ep_view.mutate_v04_elastic_play_view(
            self.day_root,
            {
                "action": "propose_or_respond_experiment",
                "exp_op": "propose",
                "experiment_id": "exp-opening",
                "title": "段落开头语气试读",
                "original_snippet": "原句版本",
                "candidate_snippet": "候选版本",
            },
            actor_side="partner",
        )
        self.assertEqual(ce1, 200)
        self.assertFalse(re1["data"]["experiments"][0]["scored"])

        ce2, _ = ep_view.mutate_v04_elastic_play_view(
            self.day_root,
            {
                "action": "propose_or_respond_experiment",
                "exp_op": "record_take",
                "experiment_id": "exp-opening",
                "take_text": "候选版本呼吸感更慢一点",
            },
            actor_side="subject",
        )
        self.assertEqual(ce2, 200)

        ce3, re3 = ep_view.mutate_v04_elastic_play_view(
            self.day_root,
            {"action": "propose_or_respond_experiment", "exp_op": "resolve", "experiment_id": "exp-opening"},
            actor_side="partner",
        )
        self.assertEqual(ce3, 200)
        self.assertEqual(re3["data"]["experiments"][0]["status"], "resolved")

        # Scoped preference never applies to private writing
        csp, rsp = ep_view.mutate_v04_elastic_play_view(
            self.day_root,
            {
                "action": "teach_scoped_preference",
                "scope": "partner_explanation",
                "instruction": "解释技术问题时先给生活比喻",
            },
            actor_side="partner",
        )
        self.assertEqual(csp, 200)
        self.assertFalse(rsp["data"]["scoped_preferences"][0]["applies_to_private_writing"])

        # Practice RBAC: partner cannot offer_prompt; subject cannot submit_answer
        c_p_forbid, _ = ep_view.mutate_v04_elastic_play_view(
            self.day_root,
            {"action": "request_or_answer_practice", "practice_op": "offer_prompt", "prompt_text": "偷塞题目"},
            actor_side="partner",
        )
        self.assertEqual(c_p_forbid, 403)

        c_s_forbid, _ = ep_view.mutate_v04_elastic_play_view(
            self.day_root,
            {"action": "request_or_answer_practice", "practice_op": "submit_answer", "answer_text": "代写答案"},
            actor_side="subject",
        )
        self.assertEqual(c_s_forbid, 403)

        self.assertEqual(
            ep_view.mutate_v04_elastic_play_view(
                self.day_root,
                {"action": "request_or_answer_practice", "practice_op": "request_prompt", "topic": "短句改写"},
                actor_side="partner",
            )[0],
            200,
        )
        self.assertEqual(
            ep_view.mutate_v04_elastic_play_view(
                self.day_root,
                {"action": "request_or_answer_practice", "practice_op": "offer_prompt", "prompt_text": "试试把长句拆成两拍"},
                actor_side="subject",
            )[0],
            200,
        )
        self.assertEqual(
            ep_view.mutate_v04_elastic_play_view(
                self.day_root,
                {"action": "request_or_answer_practice", "practice_op": "submit_answer", "answer_text": "雨停了。屋檐还在滴水。"},
                actor_side="partner",
            )[0],
            200,
        )

    def test_05_temporary_game_and_anti_spoiler_surprise_lifecycle(self) -> None:
        # Start 10-min game -> play turn -> save keepsake -> stop cleanly
        self.assertEqual(
            ep_view.mutate_v04_elastic_play_view(
                self.day_root,
                {
                    "action": "play_temporary_game",
                    "game_op": "start",
                    "game_kind": "odd_naming",
                    "prop_label": "搪瓷杯",
                    "rule_summary": "轮流取外号",
                },
                actor_side="partner",
                now_iso="2026-11-13T10:00:00+08:00",
            )[0],
            200,
        )
        self.assertEqual(
            ep_view.mutate_v04_elastic_play_view(
                self.day_root,
                {"action": "play_temporary_game", "game_op": "play_turn", "turn_text": "叫它白胖船长"},
                actor_side="subject",
                now_iso="2026-11-13T10:02:00+08:00",
            )[0],
            200,
        )
        self.assertEqual(
            ep_view.mutate_v04_elastic_play_view(
                self.day_root,
                {"action": "play_temporary_game", "game_op": "save_keepsake", "keepsake_title": "白胖船长纪念"},
                actor_side="partner",
                now_iso="2026-11-13T10:04:00+08:00",
            )[0],
            200,
        )
        _, stopped = ep_view.mutate_v04_elastic_play_view(
            self.day_root,
            {"action": "play_temporary_game", "game_op": "stop"},
            actor_side="partner",
            now_iso="2026-11-13T10:05:00+08:00",
        )
        self.assertFalse(stopped["data"]["temporary_game"]["active"])
        self.assertEqual(len(stopped["data"]["game_keepsakes"]), 1)

        # Anti-spoiler surprise lifecycle: prepared_waiting -> offered -> later -> reveal
        secret = "银杏叶夹在第四十二页"
        c_prep, _ = ep_view.mutate_v04_elastic_play_view(
            self.day_root,
            {
                "action": "prepare_or_offer_surprise",
                "stage": "prepare",
                "surprise_id": "sp-ginkgo",
                "kind": "pressed_leaf_card",
                "title": secret,
                "content": secret,
            },
            actor_side="subject",
        )
        self.assertEqual(c_prep, 200)

        # Partner read MUST NOT leak secret while prepared_waiting
        _, p_wait = ep_view.read_v04_elastic_play_view(self.day_root, has_subject_capability=False)
        self.assertNotIn(secret, json.dumps(p_wait, ensure_ascii=False))
        self.assertEqual(p_wait["data"]["prepared_surprises"], [])

        # Offer -> partner sees invite_hint only, STILL not secret content!
        c_off, _ = ep_view.mutate_v04_elastic_play_view(
            self.day_root,
            {"action": "prepare_or_offer_surprise", "stage": "offer", "surprise_id": "sp-ginkgo"},
            actor_side="subject",
        )
        self.assertEqual(c_off, 200)
        _, p_offered = ep_view.read_v04_elastic_play_view(self.day_root, has_subject_capability=False)
        self.assertEqual(len(p_offered["data"]["prepared_surprises"]), 1)
        self.assertNotIn(secret, json.dumps(p_offered, ensure_ascii=False))

        # Subject cannot impersonate partner to reveal
        c_imp, _ = ep_view.mutate_v04_elastic_play_view(
            self.day_root,
            {"action": "respond_prepared_surprise", "surprise_id": "sp-ginkgo", "decision": "reveal"},
            actor_side="subject",
        )
        self.assertEqual(c_imp, 403)

        # Partner chooses 'later' -> stays offered gently, then 'reveal' -> content visible!
        c_lat, r_lat = ep_view.mutate_v04_elastic_play_view(
            self.day_root,
            {"action": "respond_prepared_surprise", "surprise_id": "sp-ginkgo", "decision": "later"},
            actor_side="partner",
        )
        self.assertEqual(c_lat, 200)
        self.assertEqual(r_lat["data"]["prepared_surprises"][0]["deferred_count"], 1)

        c_rev, r_rev = ep_view.mutate_v04_elastic_play_view(
            self.day_root,
            {"action": "respond_prepared_surprise", "surprise_id": "sp-ginkgo", "decision": "reveal"},
            actor_side="partner",
        )
        self.assertEqual(c_rev, 200)
        self.assertIn(secret, json.dumps(r_rev, ensure_ascii=False))

    def test_06_adversarial_fields_symlink_and_corruption_guards(self) -> None:
        for bad_payload in (
            {"action": "append_shared_notebook", "text": "ok", "intimacy_score": 99},
            {"action": "append_shared_notebook", "text": "ok", "uid": "root"},
            {"action": "append_shared_notebook", "text": "   "},
            {"action": "unknown_action_xyz"},
        ):
            code, _ = ep_view.mutate_v04_elastic_play_view(self.day_root, bad_payload, actor_side="partner")
            self.assertEqual(code, 400)

        # Corrupted JSON -> 503 STATE_CORRUPTED
        pdir = self.day_root / "phone"
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / ep_engine.STATE_FILENAME).write_text("{corrupted-json", encoding="utf-8")
        c503, r503 = ep_view.read_v04_elastic_play_view(self.day_root)
        self.assertEqual(c503, 503)
        self.assertEqual(r503["reason_code"], "STATE_CORRUPTED")

        if hasattr(os, "symlink") and os.name == "posix":
            (pdir / ep_engine.STATE_FILENAME).unlink()
            outside = self.tmp / "outside.json"
            outside.write_text("{}", encoding="utf-8")
            os.symlink(outside, pdir / ep_engine.STATE_FILENAME)
            csym, rsym = ep_view.read_v04_elastic_play_view(self.day_root)
            self.assertEqual(csym, 403)
            self.assertEqual(rsym["reason_code"], "SYMLINK_PATH_FORBIDDEN")


if __name__ == "__main__":
    unittest.main()

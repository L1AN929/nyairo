#!/usr/bin/env python3
"""v0.4 Ch.48/50 + v3.0 Ch.24：涌现回响五个零大模型小原语与 HTTP 闸门。

Coverage:
* 100% zero-write reads (engine projection, alias, situational matcher, HTTP view,
  summary) on both an absent root and a populated root;
* ``future_self_questions`` never carry a due date / overdue / reminder counter and
  the three revisit resolutions map to open / answered / faded;
* shared idioms are proposed, confirmed and faded by either side;
* ``memory_reinterpretations`` lock ``original_fact_cn`` forever and only append
  revisions; sharing flips the partner-visibility flag;
* sparks keep ``familiarity`` / ``craft_fit`` / ``mood_pull_today`` separate and only
  ``outcome == "helped"`` validates the method (skip is a rest, not a punishment);
* divergent notices keep both sides' attention side-by-side with no agreement score;
* partner projection strips private questions, private sparks and unshared
  reinterpretations without leaking any hidden/private count;
* the situational matcher honours ``include_private`` and always carries the
  obligation-free idiom note;
* the HTTP gate whitelist, identity/score-key refusal, actor boundaries, clean
  404/403, operation-key idempotency and conflict;
* dynamic pronoun resolution (fallback ``TA``) and zero hard-coded names/pronouns
  in the two new modules; ``llm_calls == 0`` everywhere.

Run (WSL):
``/tmp/rc104-venv/bin/python -B -m unittest tests.test_v08_emergent_echoes_engine -v``
"""

from __future__ import annotations

import hashlib
import json
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
    str(ROOT / "tests"),
    str(ROOT / "scripts"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from components.life.scripts import v08_emergent_echoes as engine  # noqa: E402
from components.phone import v08_emergent_echoes_view as view  # noqa: E402

T0 = "2026-11-16T09:00:00+08:00"
T1 = "2026-11-17T09:00:00+08:00"
T2 = "2026-11-18T09:00:00+08:00"

#: The pronoun used only as test *data*; kept escaped so the module scan stays clean.
SHE = "她"


def _snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    snap: dict[str, tuple[int, int, str]] = {}
    if not root.exists():
        return snap
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root)).replace("\\", "/")
        stat = path.lstat()
        if path.is_file() and not path.is_symlink():
            snap[rel] = (
                int(stat.st_size),
                int(stat.st_mtime_ns),
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        else:
            snap[rel] = (0, int(stat.st_mtime_ns), "dir")
    return snap


def _question_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": "plant_future_question",
        "question_cn": "等这一阵忙完，我还会记得抬头看看夜色吗",
        "anchor_kind": "work",
        "anchor_ref": "work:night_shift",
        "keywords": ["夜色", "抬起头"],
        "visibility": "private",
    }
    payload.update(overrides)
    return payload


def _memory_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": "record_memory_reinterpretation",
        "episode_ref": "episode:seawall_summer",
        "episode_title_cn": "那年夏天的海堤",
        "original_fact_cn": "我们坐在海堤上等退潮，一直等到路灯亮起来",
        "prior_view_cn": "当时只觉得风很大、很无聊",
        "new_view_cn": "现在想起那一晚，其实是两个人安静地陪了彼此很久",
        "trigger_summary_cn": "整理旧照片时看见那张海堤的合影",
        "shared_with_partner": False,
    }
    payload.update(overrides)
    return payload


def _spark_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": "record_cross_domain_spark",
        "source_domain": "木工",
        "source_observation_cn": "打磨木面时我喜欢先摸一摸纹路再决定方向",
        "target_domain": "陶艺",
        "try_method_cn": "先只练拉坯的中心，不急着做完整器型",
        "familiarity": "new",
        "craft_fit": "awkward",
        "mood_pull_today": "curious",
        "visibility": "private",
    }
    payload.update(overrides)
    return payload


class V08EmergentEchoesEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-echoes-")
        self.root = Path(self._tmp.name)
        (self.root / "phone").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _mutate(self, payload: dict[str, Any], *, actor_side: str = "subject",
                now_iso: str = T0) -> dict[str, Any]:
        projected, _status = engine.apply_emergent_echoes_mutation(
            self.root, payload, actor_side=actor_side, now_iso=now_iso
        )
        return projected

    def _project(self, *, subject: bool = True) -> dict[str, Any]:
        return engine.read_emergent_echoes_zero_write(
            self.root, has_subject_capability=subject, now_iso=T0
        )

    # -- zero write ----------------------------------------------------------
    def test_01_reads_are_zero_write(self) -> None:
        absent = Path(tempfile.mkdtemp(prefix="v08-empty-")) / "never"
        projected = engine.read_emergent_echoes_zero_write(absent, has_subject_capability=True)
        self.assertFalse(absent.exists(), "a pure read must never create a directory")
        engine.project_emergent_echoes_zero_write(absent)
        matched = engine.match_situational_echoes_zero_write(absent, incoming_text="anything")
        self.assertFalse(absent.exists(), "every zero-write read must stay non-creating")
        self.assertEqual(projected["llm_calls"], 0)
        self.assertEqual(matched["llm_calls"], 0)
        for key in (
            "future_self_questions",
            "shared_idioms",
            "memory_reinterpretations",
            "style_and_interest_sparks",
            "divergent_notices",
        ):
            self.assertEqual(projected[key], [])
            self.assertFalse(projected["state_exists"])

        self._mutate(_question_payload(visibility="shared"))
        before = _snapshot(self.root)
        engine.read_emergent_echoes_zero_write(self.root, has_subject_capability=True, now_iso=T0)
        engine.project_emergent_echoes_zero_write(self.root, has_subject_capability=False, now_iso=T0)
        engine.match_situational_echoes_zero_write(self.root, incoming_text="夜色")
        view.read_v08_emergent_echoes_view(self.root, has_subject_capability=True, now_iso=T0)
        view.summarize_emergent_echoes_for_context(self.root, include_private=True)
        after = _snapshot(self.root)
        self.assertEqual(before, after, "reads must be byte- and mtime-identical")

    # -- feature 1: future self questions -----------------------------------
    def test_02_future_questions_have_no_debt(self) -> None:
        projected = self._mutate(_question_payload())
        row = projected["future_self_questions"][0]
        self.assertTrue(str(row["question_id"]).startswith("fq_"))
        self.assertEqual(row["status"], "open")
        self.assertIsNone(row["revisited_at"])
        self.assertIsNone(row["resolution"])
        self.assertEqual(row["note_cn"], "")
        self.assertEqual(row["keywords"], ["夜色", "抬起头"])
        for forbidden in (
            "due_date",
            "due_at",
            "overdue_count",
            "reminder_count",
            "daily_reminder",
            "streak_days",
        ):
            self.assertNotIn(forbidden, row)
        self.assertNotIn("overdue", json.dumps(row, ensure_ascii=False))

        with self.assertRaises(engine.V08ValidationError):
            self._mutate(_question_payload(question_cn="短"))
        with self.assertRaises(engine.V08ValidationError):
            self._mutate(_question_payload(keywords=[]))
        with self.assertRaises(engine.V08ValidationError):
            self._mutate(_question_payload(anchor_kind="mood"))
        with self.assertRaises(engine.V08ValidationError):
            self._mutate(_question_payload(visibility="public"))

    def test_03_revisit_transitions(self) -> None:
        question_id = self._mutate(_question_payload())["future_self_questions"][0]["question_id"]

        still = self._mutate({
            "action": "revisit_future_question",
            "question_id": question_id,
            "resolution": "still_curious",
            "note_cn": "还是想知道",
        }, now_iso=T1)["future_self_questions"][0]
        self.assertEqual(still["status"], "open")
        self.assertEqual(still["resolution"], "still_curious")
        self.assertEqual(still["revisited_at"], T1)

        answered = self._mutate({
            "action": "revisit_future_question",
            "question_id": question_id,
            "resolution": "answered_now",
        }, now_iso=T2)["future_self_questions"][0]
        self.assertEqual(answered["status"], "answered")

        faded = self._mutate({
            "action": "revisit_future_question",
            "question_id": question_id,
            "resolution": "let_fade",
        }, now_iso=T2)["future_self_questions"][0]
        self.assertEqual(faded["status"], "faded")

        with self.assertRaises(engine.V08NotFoundError) as ctx:
            self._mutate({
                "action": "revisit_future_question",
                "question_id": "fq_missing",
                "resolution": "still_curious",
            })
        self.assertEqual(ctx.exception.status_code, 404)
        with self.assertRaises(engine.V08ValidationError):
            self._mutate({
                "action": "revisit_future_question",
                "question_id": question_id,
                "resolution": "never_mind",
            })

    # -- feature 2: shared idioms -------------------------------------------
    def test_04_shared_idioms_both_sides(self) -> None:
        proposed = self._mutate({
            "action": "propose_shared_idiom",
            "phrase": "老码头",
            "meaning_cn": "累了就先去吹一会儿海风再说",
            "origin_summary_cn": "有一次两个人在码头坐到很晚",
            "origin_ref": "place:old_pier",
        })["shared_idioms"][0]
        self.assertEqual(proposed["proposed_by"], "subject")
        self.assertEqual(proposed["status"], "proposed")
        self.assertIsNone(proposed["confirmed_by"])

        confirmed = self._mutate({
            "action": "confirm_shared_idiom",
            "idiom_id": proposed["idiom_id"],
        }, actor_side="partner", now_iso=T1)["shared_idioms"][0]
        self.assertEqual(confirmed["status"], "confirmed")
        self.assertEqual(confirmed["confirmed_by"], "partner")
        self.assertEqual(confirmed["confirmed_at"], T1)

        faded = self._mutate({
            "action": "fade_shared_idiom",
            "idiom_id": proposed["idiom_id"],
        }, actor_side="partner", now_iso=T2)["shared_idioms"][0]
        self.assertEqual(faded["status"], "faded")

        partner_proposed = self._mutate({
            "action": "propose_shared_idiom",
            "phrase": "留一盏灯",
            "meaning_cn": "晚归时知道有人还在等",
            "origin_summary_cn": "那阵子总是很晚才回家",
        }, actor_side="partner", now_iso=T1)["shared_idioms"][-1]
        self.assertEqual(partner_proposed["proposed_by"], "partner")

        with self.assertRaises(engine.V08NotFoundError) as ctx:
            self._mutate({"action": "confirm_shared_idiom", "idiom_id": "idiom_missing"})
        self.assertEqual(ctx.exception.status_code, 404)

    # -- feature 3: memory reinterpretations --------------------------------
    def test_05_immutable_original_fact_and_revisions(self) -> None:
        first = self._mutate(_memory_payload())["memory_reinterpretations"][0]
        self.assertEqual(first["original_fact_cn"], _memory_payload()["original_fact_cn"])
        self.assertEqual(first["current_view_cn"], _memory_payload()["new_view_cn"])
        self.assertEqual(len(first["revisions"]), 1)
        self.assertFalse(first["shared_with_partner"])

        second = self._mutate(_memory_payload(
            original_fact_cn="一条完全不同的“新事实”，绝不允许覆写",
            prior_view_cn="第二年清明又想起",
            new_view_cn="那其实是两个人第一次学会沉默地待在一起",
            trigger_summary_cn="听到一首旧歌",
            shared_with_partner=True,
        ), now_iso=T1)["memory_reinterpretations"][0]
        self.assertEqual(second["reinterpretation_id"], first["reinterpretation_id"])
        self.assertEqual(second["original_fact_cn"], first["original_fact_cn"])
        self.assertEqual(second["current_view_cn"], "那其实是两个人第一次学会沉默地待在一起")
        self.assertEqual(len(second["revisions"]), 2)
        self.assertEqual(second["revisions"][1]["prior_view_cn"], "第二年清明又想起")
        self.assertEqual(second["revisions"][1]["recorded_at"], T1)
        self.assertTrue(second["shared_with_partner"])

        third = self._mutate(_memory_payload(
            original_fact_cn="第三条也该被忽略",
            new_view_cn="现在只剩一种温和的记得",
            trigger_summary_cn="路过同一段海堤",
        ), now_iso=T2)["memory_reinterpretations"][0]
        self.assertEqual(third["original_fact_cn"], first["original_fact_cn"])
        self.assertEqual(len(third["revisions"]), 3)

        shared = self._mutate({
            "action": "share_memory_reinterpretation",
            "reinterpretation_id": first["reinterpretation_id"],
            "shared_with_partner": False,
        }, now_iso=T2)["memory_reinterpretations"][0]
        self.assertFalse(shared["shared_with_partner"])

        with self.assertRaises(engine.V08NotFoundError) as ctx:
            self._mutate({
                "action": "share_memory_reinterpretation",
                "reinterpretation_id": "mr_missing",
                "shared_with_partner": True,
            })
        self.assertEqual(ctx.exception.status_code, 404)

    # -- feature 4: style / interest sparks ---------------------------------
    def test_06_spark_separation_and_validation(self) -> None:
        spark = self._mutate(_spark_payload())["style_and_interest_sparks"][0]
        self.assertEqual(spark["status"], "untried")
        self.assertFalse(spark["method_validated"])
        self.assertEqual(spark["familiarity"], "new")
        self.assertEqual(spark["craft_fit"], "awkward")
        self.assertEqual(spark["mood_pull_today"], "curious")
        for forbidden in ("skill_level", "obedience_level", "intimacy_score", "streak_days"):
            self.assertNotIn(forbidden, spark)

        resting = self._mutate({
            "action": "settle_spark_attempt",
            "spark_id": spark["spark_id"],
            "outcome": "skipped_today",
        }, now_iso=T1)["style_and_interest_sparks"][0]
        self.assertEqual(resting["status"], "resting")
        self.assertFalse(resting["method_validated"])

        not_fit = self._mutate({
            "action": "settle_spark_attempt",
            "spark_id": spark["spark_id"],
            "outcome": "did_not_fit",
            "choice_trace_cn": "手太生，今天先不勉强",
        }, now_iso=T2)["style_and_interest_sparks"][0]
        self.assertEqual(not_fit["status"], "not_fit")
        self.assertFalse(not_fit["method_validated"])

        second = self._mutate(_spark_payload(
            target_domain="金工",
            try_method_cn="先用软一点的铜片试一次",
        ), now_iso=T1)["style_and_interest_sparks"][-1]
        helped = self._mutate({
            "action": "settle_spark_attempt",
            "spark_id": second["spark_id"],
            "outcome": "helped",
            "choice_trace_cn": "今天更想做能立刻看见形状的事",
        }, now_iso=T2)["style_and_interest_sparks"][-1]
        self.assertEqual(helped["status"], "helped")
        self.assertTrue(helped["method_validated"])

        with self.assertRaises(engine.V08ValidationError):
            self._mutate(_spark_payload(familiarity="expert"))
        with self.assertRaises(engine.V08NotFoundError) as ctx:
            self._mutate({
                "action": "settle_spark_attempt",
                "spark_id": "spark_missing",
                "outcome": "helped",
            })
        self.assertEqual(ctx.exception.status_code, 404)

    # -- feature 5: divergent notices ---------------------------------------
    def test_07_divergent_notices_side_by_side(self) -> None:
        created = self._mutate({
            "action": "subject_add_divergent_notice",
            "target_ref": "shop:old_bookstore",
            "target_title_cn": "巷口旧书店",
            "notice_cn": "我注意到门口那盆薄荷快枯了",
        })["divergent_notices"][0]
        self.assertEqual(created["subject_notice_cn"], "我注意到门口那盆薄荷快枯了")
        self.assertEqual(created["partner_notice_cn"], "")
        self.assertNotIn("agreement_score", created)

        both = self._mutate({
            "action": "partner_add_divergent_notice",
            "target_ref": "shop:old_bookstore",
            "target_title_cn": "巷口旧书店",
            "notice_cn": "我注意的是柜台后面那本翻旧了的海图册",
        }, actor_side="partner", now_iso=T1)["divergent_notices"][0]
        self.assertEqual(both["notice_id"], created["notice_id"])
        self.assertEqual(both["subject_notice_cn"], "我注意到门口那盆薄荷快枯了")
        self.assertEqual(both["partner_notice_cn"], "我注意的是柜台后面那本翻旧了的海图册")
        for forbidden in ("agreement_score", "agreement", "agreement_ratio", "consensus"):
            self.assertNotIn(forbidden, both)

        subject_view = self._project(subject=True)["divergent_notices"][0]
        partner_view = self._project(subject=False)["divergent_notices"][0]
        self.assertEqual(subject_view["partner_notice_cn"], both["partner_notice_cn"])
        self.assertEqual(partner_view["subject_notice_cn"], both["subject_notice_cn"])

        subject_updated = self._mutate({
            "action": "subject_add_divergent_notice",
            "target_ref": "shop:old_bookstore",
            "target_title_cn": "巷口旧书店",
            "notice_cn": "今天再看，薄荷已经被人浇过水了",
        }, now_iso=T2)["divergent_notices"][0]
        self.assertEqual(subject_updated["subject_notice_cn"], "今天再看，薄荷已经被人浇过水了")
        self.assertEqual(subject_updated["partner_notice_cn"], both["partner_notice_cn"])

        second_target = self._mutate({
            "action": "subject_add_divergent_notice",
            "target_ref": "street:lighthouse",
            "target_title_cn": "旧灯塔",
            "notice_cn": "我只看见台阶上的盐霜",
        }, now_iso=T2)["divergent_notices"]
        self.assertEqual(len(second_target), 2)

    # -- partner projection --------------------------------------------------
    def test_08_partner_projection_filters_private_without_counts(self) -> None:
        self._mutate(_question_payload(question_cn="私密的问题会一直藏着", visibility="private"))
        shared_question = self._mutate(_question_payload(
            question_cn="可以和你一起想的问题",
            anchor_ref="topic:together",
            keywords=["一起"],
            visibility="shared",
        ))["future_self_questions"][-1]

        self._mutate(_spark_payload(target_domain="私房菜", visibility="private"))
        shared_spark = self._mutate(_spark_payload(
            target_domain="陶艺",
            visibility="shared",
        ))["style_and_interest_sparks"][-1]

        self._mutate(_memory_payload(shared_with_partner=False))
        shared_memory = self._mutate(_memory_payload(
            episode_ref="episode:lighthouse",
            episode_title_cn="灯塔下的晚饭",
            shared_with_partner=True,
        ))["memory_reinterpretations"][-1]

        partner = self._project(subject=False)
        subject = self._project(subject=True)

        self.assertEqual(partner["perspective"], "partner")
        self.assertEqual([row["question_id"] for row in partner["future_self_questions"]], [shared_question["question_id"]])
        self.assertEqual([row["spark_id"] for row in partner["style_and_interest_sparks"]], [shared_spark["spark_id"]])
        self.assertEqual(
            [row["reinterpretation_id"] for row in partner["memory_reinterpretations"]],
            [shared_memory["reinterpretation_id"]],
        )
        self.assertEqual(len(subject["future_self_questions"]), 2)
        self.assertEqual(len(subject["style_and_interest_sparks"]), 2)
        self.assertEqual(len(subject["memory_reinterpretations"]), 2)

        for forbidden in ("hidden_count", "private_count", "total_count", "hidden_total"):
            self.assertNotIn(forbidden, partner)
            self.assertNotIn(forbidden, partner.get("data") or {})
        self.assertNotIn("hidden", json.dumps(partner, ensure_ascii=False).lower())

    # -- situational matcher -------------------------------------------------
    def test_09_situational_matcher(self) -> None:
        idiom_id = self._mutate({
            "action": "propose_shared_idiom",
            "phrase": "吹海风",
            "meaning_cn": "心里堵的时候先去码头站一会儿",
            "origin_summary_cn": "那次吵完架两个人在码头站到很晚",
        })["shared_idioms"][0]["idiom_id"]
        self._mutate({"action": "confirm_shared_idiom", "idiom_id": idiom_id}, actor_side="partner")

        question = self._mutate(_question_payload(
            question_cn="灯塔的灯还亮着吗",
            anchor_kind="place",
            anchor_ref="place:lighthouse",
            keywords=["蓝灯", "灯塔"],
            visibility="shared",
        ))["future_self_questions"][0]

        self._mutate(_memory_payload(shared_with_partner=True))

        spark = self._mutate(_spark_payload(target_domain="陶艺", visibility="shared"))[
            "style_and_interest_sparks"
        ][0]

        self._mutate({
            "action": "subject_add_divergent_notice",
            "target_ref": "shop:old_bookstore",
            "target_title_cn": "巷口旧书店",
            "notice_cn": "门边的薄荷",
        })

        matched = engine.match_situational_echoes_zero_write(
            self.root,
            incoming_text="今天又想去码头吹海风，顺路看看旧书店和陶艺",
        )
        self.assertEqual(matched["llm_calls"], 0)
        self.assertEqual(len(matched["matched_idioms"]), 1)
        self.assertEqual(
            matched["matched_idioms"][0]["obligation_free_note_cn"],
            engine.OBLIGATION_FREE_NOTE,
        )
        self.assertEqual(matched["matched_sparks"][0]["spark_id"], spark["spark_id"])
        self.assertEqual(matched["matched_notices"][0]["target_ref"], "shop:old_bookstore")

        by_keyword = engine.match_situational_echoes_zero_write(
            self.root, incoming_text="蓝灯还亮着吗"
        )
        self.assertEqual(by_keyword["matched_questions"][0]["question_id"], question["question_id"])

        by_ref = engine.match_situational_echoes_zero_write(
            self.root, active_refs=["place:lighthouse"]
        )
        self.assertEqual(by_ref["matched_questions"][0]["question_id"], question["question_id"])
        self.assertEqual(len(by_ref["matched_reinterpretations"]), 1)

        fallback = engine.match_situational_echoes_zero_write(
            self.root, incoming_text="今天有点安静", include_private=True
        )
        self.assertEqual(len(fallback["matched_reinterpretations"]), 1)

        faded = self._mutate({"action": "fade_shared_idiom", "idiom_id": idiom_id},
                             actor_side="partner", now_iso=T2)
        self.assertEqual(faded["shared_idioms"][0]["status"], "faded")
        after_fade = engine.match_situational_echoes_zero_write(
            self.root, incoming_text="去码头吹海风"
        )
        self.assertEqual(after_fade["matched_idioms"], [])

    def test_10_matcher_private_gating(self) -> None:
        self._mutate(_question_payload(
            question_cn="只对自己说的疑问",
            anchor_kind="topic",
            anchor_ref="topic:secret",
            keywords=["只对自己说"],
            visibility="private",
        ))
        self._mutate(_spark_payload(target_domain="私房菜", visibility="private"))
        self._mutate(_memory_payload(shared_with_partner=False))

        public = engine.match_situational_echoes_zero_write(
            self.root, incoming_text="只对自己说 私房菜 那年夏天的海堤"
        )
        self.assertEqual(public["matched_questions"], [])
        self.assertEqual(public["matched_sparks"], [])
        self.assertEqual(public["matched_reinterpretations"], [])

        private = engine.match_situational_echoes_zero_write(
            self.root,
            incoming_text="只对自己说 私房菜 那年夏天的海堤",
            include_private=True,
        )
        self.assertEqual(len(private["matched_questions"]), 1)
        self.assertEqual(len(private["matched_sparks"]), 1)
        self.assertEqual(len(private["matched_reinterpretations"]), 1)

    # -- identity ------------------------------------------------------------
    def test_11_pronoun_is_dynamic_and_neutral(self) -> None:
        default = self._project(subject=True)
        self.assertEqual(default["pronoun"], "TA")
        self.assertEqual(default["display_name"], "TA")

        pack = {"display_name": "小灯", "pronouns": SHE}
        (self.root / "phone" / "character_pack_state.json").write_text(
            json.dumps(pack, ensure_ascii=False), encoding="utf-8"
        )
        loaded = self._project(subject=True)
        self.assertEqual(loaded["pronoun"], SHE)
        self.assertEqual(loaded["display_name"], "小灯")

        self._mutate(_question_payload(
            question_cn="夜里还会想起那盏灯吗",
            keywords=["夜色"],
            visibility="shared",
        ))
        summary = view.summarize_emergent_echoes_for_context(
            self.root, incoming_text="夜色", include_private=True
        )
        self.assertIn(SHE, summary)

    def test_12_modules_have_no_hardcoded_identity(self) -> None:
        modules = (
            ROOT / "components" / "life" / "scripts" / "v08_emergent_echoes.py",
            ROOT / "components" / "phone" / "v08_emergent_echoes_view.py",
        )
        for path in modules:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("千代", text, f"{path.name} must not hard-code a name")
            self.assertNotIn(SHE, text, f"{path.name} must not hard-code a pronoun")
            self.assertNotIn("他", text, f"{path.name} must not hard-code a pronoun")


class V08EmergentEchoesViewTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-echoes-view-")
        self.root = Path(self._tmp.name)
        (self.root / "phone").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _mutate(self, payload: dict[str, Any], *, actor_side: str = "subject",
                now_iso: str = T0) -> tuple[int, dict[str, Any]]:
        return view.mutate_v08_emergent_echoes_view(
            self.root, payload, actor_side=actor_side, now_iso=now_iso
        )

    def test_20_view_read_zero_write_and_partner_filter(self) -> None:
        absent = Path(tempfile.mkdtemp(prefix="v08-view-empty-")) / "never"
        code, body = view.read_v08_emergent_echoes_view(absent, has_subject_capability=True, now_iso=T0)
        self.assertFalse(absent.exists())
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["data"]["future_self_questions"], [])
        self.assertEqual(body["llm_calls"], 0)

        self._mutate(_question_payload(visibility="private"))
        self._mutate(_question_payload(
            question_cn="能一起想的问题", anchor_ref="topic:together", keywords=["一起"], visibility="shared"
        ))

        before = _snapshot(self.root)
        code, body = view.read_v08_emergent_echoes_view(self.root, has_subject_capability=False, now_iso=T0)
        view.summarize_emergent_echoes_for_context(self.root, incoming_text="一起")
        after = _snapshot(self.root)
        self.assertEqual(before, after)
        self.assertEqual(code, 200)
        self.assertEqual(body["perspective"], "partner")
        self.assertEqual(len(body["data"]["future_self_questions"]), 1)
        self.assertEqual(body["data"]["future_self_questions"][0]["question_cn"], "能一起想的问题")

        _code, subject_body = view.read_v08_emergent_echoes_view(
            self.root, has_subject_capability=True, now_iso=T0
        )
        self.assertEqual(len(subject_body["data"]["future_self_questions"]), 2)

    def test_21_whitelist_and_identity_score_keys(self) -> None:
        unknown = _question_payload()
        unknown["surprise_field"] = 1
        code, body = self._mutate(unknown)
        self.assertEqual(code, 400)
        self.assertEqual(body["reason_code"], "INVALID_FIELDS")

        for key in ("uid", "user_id", "owner_uid", "local_owner_uid", "actor_side",
                    "has_subject_capability", "subject_view", "capability",
                    "intimacy_score", "affection_score", "favorability",
                    "relationship_xp", "skill_level", "streak_days", "obedience_level"):
            payload = _question_payload()
            payload[key] = "forged"
            code, body = self._mutate(payload)
            self.assertEqual(code, 409, f"{key} must be refused with 409")
            self.assertEqual(body["reason_code"], "CLIENT_IDENTITY_REFUSED")

        missing = {"action": "plant_future_question", "question_cn": "只有问题没有锚点"}
        code, body = self._mutate(missing)
        self.assertEqual(code, 400)
        self.assertEqual(body["reason_code"], "INVALID_FIELDS")

        code, body = self._mutate(_question_payload(anchor_kind="mood"))
        self.assertEqual(code, 400)

    def test_22_actor_boundary_and_clean_404(self) -> None:
        code, body = self._mutate(_question_payload(), actor_side="partner")
        self.assertEqual(code, 403)
        self.assertEqual(body["reason_code"], "FORBIDDEN_ACTOR_SIDE")

        code, body = self._mutate(_memory_payload(), actor_side="partner")
        self.assertEqual(code, 403)

        code, body = self._mutate({
            "action": "partner_add_divergent_notice",
            "target_ref": "shop:x",
            "target_title_cn": "某处",
            "notice_cn": "注意到的细节",
        }, actor_side="subject")
        self.assertEqual(code, 403)

        code, body = self._mutate({
            "action": "revisit_future_question",
            "question_id": "fq_missing",
            "resolution": "still_curious",
        })
        self.assertEqual(code, 404)
        self.assertEqual(body["reason_code"], "QUESTION_NOT_FOUND")

        code, body = self._mutate({
            "action": "share_memory_reinterpretation",
            "reinterpretation_id": "mr_missing",
            "shared_with_partner": True,
        })
        self.assertEqual(code, 404)
        self.assertEqual(body["reason_code"], "REINTERPRETATION_NOT_FOUND")

        code, body = self._mutate({"action": "confirm_shared_idiom", "idiom_id": "idiom_missing"})
        self.assertEqual(code, 404)
        self.assertEqual(body["reason_code"], "IDIOM_NOT_FOUND")

        # Partner may propose / confirm an idiom.
        code, body = self._mutate({
            "action": "propose_shared_idiom",
            "phrase": "留一盏灯",
            "meaning_cn": "晚归时知道有人还在等",
            "origin_summary_cn": "那阵子总是很晚才回家",
        }, actor_side="partner")
        self.assertEqual(code, 200)
        idiom_id = body["data"]["shared_idioms"][0]["idiom_id"]

        code, body = self._mutate({
            "action": "confirm_shared_idiom",
            "idiom_id": idiom_id,
        }, actor_side="subject")
        self.assertEqual(code, 200)
        self.assertEqual(body["data"]["shared_idioms"][0]["confirmed_by"], "subject")

    def test_23_operation_key_idempotency_and_conflict(self) -> None:
        payload = _question_payload(operation_key="op-plant-1")
        code, body = self._mutate(payload)
        self.assertEqual(code, 200)
        self.assertEqual(body["command_status"], "APPLIED")
        first_id = body["data"]["future_self_questions"][0]["question_id"]

        code, replay = self._mutate(payload)
        self.assertEqual(code, 200)
        self.assertEqual(replay["command_status"], "DUPLICATE")
        self.assertEqual(replay["status"], "already_applied")
        self.assertEqual(len(replay["data"]["future_self_questions"]), 1)

        conflict = dict(payload)
        conflict["question_cn"] = "同一个操作键却换了内容"
        code, body = self._mutate(conflict)
        self.assertEqual(code, 409)
        self.assertEqual(body["reason_code"], "OPERATION_KEY_CONFLICT")

        code, body = self._mutate({"action": "revisit_future_question",
                                   "question_id": first_id,
                                   "resolution": "still_curious",
                                   "operation_key": "op-revisit-1"})
        self.assertEqual(code, 200)
        self.assertEqual(body["data"]["future_self_questions"][0]["status"], "open")

    def test_24_mutation_envelope_and_summary(self) -> None:
        code, body = self._mutate(_question_payload(
            question_cn="海风大的时候还会想去码头吗",
            keywords=["海风"],
            visibility="shared",
        ))
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["schema"], engine.VIEW_SCHEMA)
        self.assertEqual(body["endpoint"], view.ROUTE_V08_EMERGENT_ECHOES)
        self.assertEqual(body["llm_calls"], 0)
        self.assertEqual(body["data"]["action"], "plant_future_question")

        text = view.summarize_emergent_echoes_for_context(self.root, incoming_text="海风")
        self.assertIn("海风大的时候还会想去码头吗", text)

        self._mutate(_question_payload(
            question_cn="只对自己说的问题",
            keywords=["只对自己说"],
            visibility="private",
        ))
        public = view.summarize_emergent_echoes_for_context(self.root, incoming_text="只对自己说")
        self.assertNotIn("只对自己说的问题", public)
        private = view.summarize_emergent_echoes_for_context(
            self.root, incoming_text="只对自己说", include_private=True
        )
        self.assertIn("只对自己说的问题", private)

    def test_25_server_and_service_wiring(self) -> None:
        from components.phone import server as phone_server
        from components.phone import service as phone_service

        self.assertEqual(phone_server.ROUTE_V08_EMERGENT_ECHOES, "/api/phone/v08/emergent-echoes")
        handler_cls = phone_server.PhoneRequestHandler
        self.assertTrue(hasattr(handler_cls, "_get_v08_emergent_echoes"))
        self.assertTrue(hasattr(handler_cls, "_post_v08_emergent_echoes"))
        service_cls = phone_service.PhoneService
        self.assertTrue(hasattr(service_cls, "v08_emergent_echoes"))
        self.assertTrue(hasattr(service_cls, "v08_emergent_echoes_action"))


if __name__ == "__main__":
    unittest.main()

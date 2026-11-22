#!/usr/bin/env python3
"""v0.8 Ch.16 §三：自传性技法谱系（craft genealogy）与隐私化技法分享。

Coverage (all zero LLM calls):
* ``record_craft_genealogy_step`` stores the full subject-side row, and
  ``match_applicable_craft_genealogy_zero_write`` only surfaces ``worked``
  methods -- a ``misfired`` row is never offered as applicable;
* a method whose ``mismatch_boundary_cn`` fires against the current situation is
  withheld into ``known_mismatches`` instead of being mechanically replayed;
* swapping the two learning histories selects a different ``operation_cn``;
* the partner projection completely omits unshared rows and strips
  ``source_work_ref`` from shared rows; a partner mutation is refused with 403;
* ``operation_key`` replays idempotently and every read stays zero-write.

Run (WSL):
``/tmp/rc104-venv/bin/python -B -m unittest tests.test_v08_craft_genealogy -v``
"""

from __future__ import annotations

import hashlib
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
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from components.life.scripts import v08_emergent_echoes as engine  # noqa: E402
from components.phone import v08_emergent_echoes_view as view  # noqa: E402

T0 = "2026-11-16T09:00:00+08:00"
T1 = "2026-11-17T09:00:00+08:00"


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


def _craft_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": "record_craft_genealogy_step",
        "problem_pattern_cn": "删掉空洞抒情只留物件触感",
        "operation_cn": "改写成一只缺口杯子的细节",
        "domain_tag": "short_prose",
        "outcome": "worked",
        "mismatch_boundary_cn": "快节奏赶工交接时不要慢描细节",
        "source_work_ref": "draft://rainy-letter-v2",
        "shareable_tip_cn": "写不下去时先找一件具体旧物，让触感替你说话",
        "shared_tip_only": True,
    }
    payload.update(overrides)
    return payload


class V08CraftGenealogyTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-craft-")
        self.root = Path(self._tmp.name)
        (self.root / "phone").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _mutate(
        self,
        payload: dict[str, Any],
        *,
        root: Path | None = None,
        actor_side: str = "subject",
        now_iso: str = T0,
    ) -> dict[str, Any]:
        projected, _status = engine.apply_emergent_echoes_mutation(
            root or self.root, payload, actor_side=actor_side, now_iso=now_iso
        )
        return projected

    def _project(self, *, subject: bool = True, root: Path | None = None) -> dict[str, Any]:
        return engine.read_emergent_echoes_zero_write(
            root or self.root, has_subject_capability=subject, now_iso=T0
        )

    # -- 1: worked / misfired / mismatch -------------------------------------
    def test_01_worked_only_and_mismatch_boundary(self) -> None:
        worked_id = self._mutate(_craft_payload())["craft_genealogy"][0]["genealogy_id"]
        self._mutate(_craft_payload(
            problem_pattern_cn="把结尾写成大合唱式总结",
            operation_cn="结尾连用三个感叹句收束",
            mismatch_boundary_cn="克制冷静的讣告文体",
            outcome="misfired",
            source_work_ref="draft://rainy-letter-v9",
        ), now_iso=T1)

        by_domain = engine.match_applicable_craft_genealogy_zero_write(
            self.root, domain_tag="short_prose", include_private=True
        )
        self.assertEqual(by_domain["llm_calls"], 0)
        self.assertEqual(
            [row["genealogy_id"] for row in by_domain["applicable_methods"]], [worked_id]
        )
        self.assertEqual(by_domain["known_mismatches"], [])
        for row in by_domain["applicable_methods"]:
            self.assertEqual(row["outcome"], "worked")
            self.assertEqual(row["match_status"], "applicable")

        by_text = engine.match_applicable_craft_genealogy_zero_write(
            self.root, current_situation_cn="这段又空了，还得删掉空洞抒情只留物件触感"
        )
        self.assertEqual(
            [row["genealogy_id"] for row in by_text["applicable_methods"]], [worked_id]
        )

        # The boundary phrase fires: the old habit must be withheld, not replayed.
        mismatch = engine.match_applicable_craft_genealogy_zero_write(
            self.root,
            domain_tag="short_prose",
            current_situation_cn="今天快节奏赶工交接，根本来不及慢描细节",
        )
        self.assertEqual(mismatch["applicable_methods"], [])
        self.assertEqual(len(mismatch["known_mismatches"]), 1)
        warning = mismatch["known_mismatches"][0]
        self.assertEqual(warning["genealogy_id"], worked_id)
        self.assertEqual(warning["match_status"], "known_mismatch")

        # The situational matcher exposes the same split under its own keys.
        situational = engine.match_situational_echoes_zero_write(
            self.root,
            incoming_text="今天快节奏赶工交接，根本来不及慢描细节",
            include_private=True,
        )
        self.assertEqual(situational["matched_craft_methods"], [])
        self.assertEqual(
            situational["known_mismatch_warnings"][0]["genealogy_id"], worked_id
        )

    # -- 2: swapped learning histories ---------------------------------------
    def test_02_swapped_histories_select_different_operation(self) -> None:
        method_a = {
            "problem_pattern_cn": "人物出场太突然，读的人跟不上",
            "operation_cn": "先写一个日常动作，再引出名字",
            "domain_tag": "short_prose",
            "mismatch_boundary_cn": "群像同时登场的段落不要这样做",
            "source_work_ref": None,
            "shareable_tip_cn": "让人物先动起来再被叫出名字",
            "shared_tip_only": False,
        }
        method_b = {
            "problem_pattern_cn": "人物出场太突然，读的人跟不上",
            "operation_cn": "先写旁人对这个人的反应，再补名字",
            "domain_tag": "short_prose",
            "mismatch_boundary_cn": "独白密集的段落不要这样做",
            "source_work_ref": None,
            "shareable_tip_cn": "借旁观者的反应把人物带进来",
            "shared_tip_only": False,
        }

        other_tmp = tempfile.TemporaryDirectory(prefix="v08-craft-swap-")
        self.addCleanup(other_tmp.cleanup)
        other_root = Path(other_tmp.name)
        (other_root / "phone").mkdir()

        # Root A: method A worked, method B misfired.
        self._mutate(_craft_payload(outcome="worked", **method_a))
        self._mutate(_craft_payload(outcome="misfired", **method_b), now_iso=T1)
        # Root B: the same two methods with their outcomes swapped.
        self._mutate(_craft_payload(outcome="misfired", **method_a), root=other_root)
        self._mutate(_craft_payload(outcome="worked", **method_b), root=other_root, now_iso=T1)

        query = dict(domain_tag="short_prose", current_situation_cn="人物出场太突然")
        picked_a = engine.match_applicable_craft_genealogy_zero_write(
            self.root, include_private=True, **query
        )
        picked_b = engine.match_applicable_craft_genealogy_zero_write(
            other_root, include_private=True, **query
        )
        self.assertEqual(len(picked_a["applicable_methods"]), 1)
        self.assertEqual(len(picked_b["applicable_methods"]), 1)
        self.assertEqual(picked_a["applicable_methods"][0]["operation_cn"], method_a["operation_cn"])
        self.assertEqual(picked_b["applicable_methods"][0]["operation_cn"], method_b["operation_cn"])
        self.assertNotEqual(
            picked_a["applicable_methods"][0]["operation_cn"],
            picked_b["applicable_methods"][0]["operation_cn"],
        )

    # -- 3: privacy boundary -------------------------------------------------
    def test_03_partner_privacy_boundary(self) -> None:
        self._mutate(_craft_payload(
            shared_tip_only=False,
            source_work_ref="draft://rainy-letter-private",
        ))
        self._mutate(_craft_payload(
            problem_pattern_cn="收束太快像在赶场",
            operation_cn="让最后一句话留一点没说完的余地",
            source_work_ref="draft://rainy-letter-shared",
            shareable_tip_cn="结尾留白比说透更有余味",
            shared_tip_only=True,
        ), now_iso=T1)

        subject = self._project(subject=True)
        self.assertEqual(len(subject["craft_genealogy"]), 2)
        self.assertTrue(
            all("source_work_ref" in row for row in subject["craft_genealogy"])
        )

        partner = self._project(subject=False)
        self.assertEqual(len(partner["craft_genealogy"]), 1)
        visible = partner["craft_genealogy"][0]
        self.assertTrue(visible["shared_tip_only"])
        self.assertNotIn("source_work_ref", visible)
        self.assertEqual(visible["shareable_tip_cn"], "结尾留白比说透更有余味")
        for kept in ("problem_pattern_cn", "operation_cn", "mismatch_boundary_cn", "domain_tag", "outcome"):
            self.assertIn(kept, visible)

        partner_match = engine.match_applicable_craft_genealogy_zero_write(
            self.root, domain_tag="short_prose", include_private=False
        )
        self.assertEqual(len(partner_match["applicable_methods"]), 1)
        self.assertNotIn("source_work_ref", partner_match["applicable_methods"][0])
        for row in partner_match["applicable_methods"]:
            self.assertIs(row["shared_tip_only"], True)

        # Partner cannot write the subject's genealogy through the HTTP gate.
        for payload in (
            _craft_payload(),
            {
                "action": "toggle_share_craft_tip",
                "genealogy_id": subject["craft_genealogy"][0]["genealogy_id"],
                "shared_tip_only": False,
            },
        ):
            code, body = view.mutate_v08_emergent_echoes_view(
                self.root, payload, actor_side="partner", now_iso=T0
            )
            self.assertEqual(code, 403)
            self.assertEqual(body["reason_code"], "FORBIDDEN_ACTOR_SIDE")

    # -- 4: idempotency + zero-write -----------------------------------------
    def test_04_operation_key_and_zero_write(self) -> None:
        absent = Path(tempfile.mkdtemp(prefix="v08-craft-empty-")) / "never"
        empty = engine.match_applicable_craft_genealogy_zero_write(
            absent, domain_tag="short_prose", current_situation_cn="随便什么情形"
        )
        self.assertEqual(empty["applicable_methods"], [])
        self.assertEqual(empty["known_mismatches"], [])
        self.assertEqual(empty["llm_calls"], 0)
        view.read_v08_emergent_echoes_view(absent, has_subject_capability=True, now_iso=T0)
        view.summarize_emergent_echoes_for_context(absent, incoming_text="随便什么情形")
        self.assertFalse(absent.exists(), "every craft-genealogy read must be zero-write")

        payload = _craft_payload(operation_key="op-craft-1")
        first, status = engine.apply_emergent_echoes_mutation(
            self.root, payload, actor_side="subject", now_iso=T0
        )
        self.assertEqual(status, "APPLIED")
        genealogy_id = first["craft_genealogy"][0]["genealogy_id"]

        before = _snapshot(self.root)
        engine.match_applicable_craft_genealogy_zero_write(
            self.root, domain_tag="short_prose", current_situation_cn="删掉空洞抒情"
        )
        engine.match_situational_echoes_zero_write(self.root, incoming_text="删掉空洞抒情")
        after = _snapshot(self.root)
        self.assertEqual(before, after, "reads must be byte- and mtime-identical")

        replay, replay_status = engine.apply_emergent_echoes_mutation(
            self.root, payload, actor_side="subject", now_iso=T1
        )
        self.assertEqual(replay_status, "DUPLICATE")
        self.assertEqual(len(replay["craft_genealogy"]), 1)
        self.assertEqual(replay["craft_genealogy"][0]["genealogy_id"], genealogy_id)

        conflict = dict(payload)
        conflict["problem_pattern_cn"] = "同一个操作键却换了问题"
        with self.assertRaises(engine.V08ConflictError) as ctx:
            engine.apply_emergent_echoes_mutation(
                self.root, conflict, actor_side="subject", now_iso=T1
            )
        self.assertEqual(ctx.exception.reason_code, "OPERATION_KEY_CONFLICT")

    # -- 5: toggle share tip --------------------------------------------------
    def test_05_toggle_share_updates_safe_summary_only(self) -> None:
        row = self._mutate(_craft_payload(
            shared_tip_only=False,
            shareable_tip_cn="",
            source_work_ref="draft://rainy-letter-keep",
        ))["craft_genealogy"][0]
        genealogy_id = row["genealogy_id"]

        toggled = self._mutate({
            "action": "toggle_share_craft_tip",
            "genealogy_id": genealogy_id,
            "shared_tip_only": True,
            "shareable_tip_cn": "写不动时先摸一件旧物",
        }, now_iso=T1)["craft_genealogy"][0]
        self.assertTrue(toggled["shared_tip_only"])
        self.assertEqual(toggled["shareable_tip_cn"], "写不动时先摸一件旧物")
        # The safe summary updates; the private work reference is untouched.
        self.assertEqual(toggled["source_work_ref"], "draft://rainy-letter-keep")

        with self.assertRaises(engine.V08ValidationError):
            self._mutate({
                "action": "toggle_share_craft_tip",
                "genealogy_id": genealogy_id,
                "shared_tip_only": "yes",
            })
        with self.assertRaises(engine.V08NotFoundError) as ctx:
            self._mutate({
                "action": "toggle_share_craft_tip",
                "genealogy_id": "cg_missing",
                "shared_tip_only": True,
            })
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(ctx.exception.reason_code, "CRAFT_GENEALOGY_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""v0.8 Ch.16 §三 #18 / §四 #7：个人生活习惯演变与零债淡出（personal rituals）。

Coverage (all zero LLM calls):
* ``sketch_custom -> reuse_variant -> fade_or_omit`` walks a spontaneous small
  practice through a voluntary rename / variant, then a quiet fade linked to a
  *real* ``craft_genealogy`` row that becomes ``technique_retained``; the
  immutable floor (``auto_declared_by_count is False``, ``omission_debt == 0``)
  survives every step;
* an unknown ``linked_genealogy_id`` is rejected (404) instead of forging a
  lineage; ten repetitions of ``reuse_variant`` never auto-declare a ritual; a
  milestone anniversary only opens a consideration opportunity;
* streak / habit / guilt counter keys are refused with 400 through the HTTP gate;
* the partner door is closed (403) and a ``private`` practice disappears from the
  partner projection completely -- no rows, no count, no leaked title;
* every read is strictly zero-write, and a legacy state file without the
  ``personal_rituals`` key is treated as ``[]`` in memory without being rewritten.

Run (Windows):
``python -B -m unittest tests.test_v08_ch16_personal_culture_evolution -v``
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
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from components.life.scripts import v08_emergent_echoes as engine  # noqa: E402
from components.phone import v08_emergent_echoes_view as view  # noqa: E402

T0 = "2026-11-16T09:00:00+08:00"
T1 = "2026-11-17T09:00:00+08:00"
T2 = "2026-11-18T09:00:00+08:00"
T3 = "2026-11-19T09:00:00+08:00"


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
        "problem_pattern_cn": "旧物总是一股脑塞进抽屉，找的时候全乱",
        "operation_cn": "先按用处分三堆，再决定留哪一堆",
        "domain_tag": "home_sorting",
        "outcome": "worked",
        "mismatch_boundary_cn": "赶时间出门前不要重排整个抽屉",
    }
    payload.update(overrides)
    return payload


def _sketch_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": "curate_personal_ritual",
        "ritual_op": "sketch_custom",
        "title_cn": "泡茶前先把杯子烫一遍",
        "origin_trace_cn": "那天手边正好有热水，顺手烫了一下，茶确实更稳",
        "context_scope_cn": "晚上在家写东西时",
        "visibility": "private",
    }
    payload.update(overrides)
    return payload


class V08PersonalRitualsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-rituals-")
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

    def _ritual(self, row: dict[str, Any], key: str = "personal_rituals") -> dict[str, Any]:
        return row[key][0]

    # -- 1: sketch -> reuse -> fade with a real genealogy --------------------
    def test_01_lifecycle_to_technique_retained(self) -> None:
        genealogy_id = self._mutate(_craft_payload())["craft_genealogy"][0]["genealogy_id"]

        sketched = self._ritual(self._mutate(_sketch_payload()))
        ritual_id = sketched["ritual_id"]
        self.assertTrue(ritual_id.startswith("ritual_"))
        self.assertEqual(sketched["status"], "provisional_custom")
        self.assertIs(sketched["auto_declared_by_count"], False)
        self.assertEqual(sketched["omission_debt"], 0)
        self.assertEqual(sketched["origin_trace_cn"], "那天手边正好有热水，顺手烫了一下，茶确实更稳")

        reused = self._ritual(self._mutate({
            "action": "curate_personal_ritual",
            "ritual_op": "reuse_variant",
            "ritual_id": ritual_id,
            "new_title_cn": "烫杯再出汤",
            "variant_note_cn": "换成盖碗也先烫一遍，出汤更顺",
            "context_scope_cn": "朋友来家里喝茶时",
        }, now_iso=T1))
        self.assertEqual(reused["status"], "reused_variant")
        self.assertEqual(reused["title_cn"], "烫杯再出汤")
        self.assertEqual(reused["variant_note_cn"], "换成盖碗也先烫一遍，出汤更顺")
        self.assertEqual(reused["context_scope_cn"], "朋友来家里喝茶时")
        self.assertIs(reused["auto_declared_by_count"], False)
        self.assertEqual(reused["omission_debt"], 0)

        faded = self._ritual(self._mutate({
            "action": "curate_personal_ritual",
            "ritual_op": "fade_or_omit",
            "ritual_id": ritual_id,
            "linked_genealogy_id": genealogy_id,
        }, now_iso=T2))
        self.assertEqual(faded["status"], "technique_retained")
        self.assertEqual(faded["linked_genealogy_id"], genealogy_id)
        self.assertEqual(faded["omission_debt"], 0)
        self.assertIs(faded["auto_declared_by_count"], False)

        # A plain omission with no linked technique fades quietly -- still zero debt.
        plain_id = self._ritual(self._mutate(_sketch_payload(
            title_cn="睡前把明天的衣服搭好",
            origin_trace_cn="连着几天早上赶时间，前一晚顺手搭好就从容多了",
        ), now_iso=T2))["ritual_id"]
        quiet = self._ritual(self._mutate({
            "action": "curate_personal_ritual",
            "ritual_op": "fade_or_omit",
            "ritual_id": plain_id,
        }, now_iso=T3))
        self.assertEqual(quiet["status"], "faded_quietly")
        self.assertEqual(quiet["omission_debt"], 0)
        self.assertIs(quiet["auto_declared_by_count"], False)

        projected = self._project()
        self.assertEqual(projected["llm_calls"], 0)
        self.assertEqual(len(projected["personal_rituals"]), 2)
        for row in projected["personal_rituals"]:
            for forbidden in ("streak_days", "habit_score", "guilt_debt"):
                self.assertNotIn(forbidden, row)

    # -- 2: forged lineage / no auto-declaration / counter keys --------------
    def test_02_no_forged_lineage_and_no_auto_declaration(self) -> None:
        code, body = view.mutate_v08_emergent_echoes_view(
            self.root,
            _sketch_payload(linked_genealogy_id="cg_does_not_exist"),
            actor_side="subject",
            now_iso=T0,
        )
        self.assertEqual(code, 404)
        self.assertEqual(body["reason_code"], "CRAFT_GENEALOGY_NOT_FOUND")

        ritual_id = self._ritual(self._mutate(_sketch_payload()))["ritual_id"]
        code, body = view.mutate_v08_emergent_echoes_view(
            self.root,
            {
                "action": "curate_personal_ritual",
                "ritual_op": "fade_or_omit",
                "ritual_id": ritual_id,
                "linked_genealogy_id": "cg_does_not_exist",
            },
            actor_side="subject",
            now_iso=T1,
        )
        self.assertEqual(code, 404)
        self.assertEqual(body["reason_code"], "CRAFT_GENEALOGY_NOT_FOUND")
        # The refused fade never mutated the stored row.
        self.assertEqual(self._project()["personal_rituals"][0]["status"], "provisional_custom")

        # Ten voluntary reuses never promote a habit into a declared ritual.
        for step in range(10):
            self._mutate({
                "action": "curate_personal_ritual",
                "ritual_op": "reuse_variant",
                "ritual_id": ritual_id,
                "variant_note_cn": f"第{step + 1}次顺手复用",
            }, now_iso=T1)
        after_reuse = self._project()["personal_rituals"][0]
        self.assertEqual(after_reuse["status"], "reused_variant")
        self.assertIs(after_reuse["auto_declared_by_count"], False)
        self.assertEqual(after_reuse["omission_debt"], 0)
        serialized = json.dumps(after_reuse, ensure_ascii=False)
        for absent in ("ritual_declared", "confirmed_ritual", "habit_score", "guilt_debt"):
            self.assertNotIn(absent, serialized)

        # An anniversary only opens a quiet consideration; the status is untouched.
        considered = self._ritual(self._mutate({
            "action": "curate_personal_ritual",
            "ritual_op": "consider_milestone",
            "ritual_id": ritual_id,
        }, now_iso=T2))
        self.assertEqual(considered["status"], "reused_variant")
        self.assertIs(considered["milestone_consideration_open"], True)
        self.assertEqual(considered["milestone_considered_at"], T2)
        self.assertIs(considered["auto_declared_by_count"], False)

        # Streak / habit / guilt counters are refused with 400 at the gate.
        for counter in ("streak_days", "habit_score", "guilt_debt",
                        "auto_declared_by_count", "omission_debt"):
            payload = {
                "action": "curate_personal_ritual",
                "ritual_op": "reuse_variant",
                "ritual_id": ritual_id,
            }
            payload[counter] = 21
            code, body = view.mutate_v08_emergent_echoes_view(
                self.root, payload, actor_side="subject", now_iso=T3
            )
            self.assertEqual(code, 400, f"{counter} must be refused with 400")
            self.assertEqual(body["reason_code"], "INVALID_FIELDS")

        # The whitelist still refuses unrelated fields.
        code, body = view.mutate_v08_emergent_echoes_view(
            self.root,
            {
                "action": "curate_personal_ritual",
                "ritual_op": "reuse_variant",
                "ritual_id": ritual_id,
                "surprise_field": True,
            },
            actor_side="subject",
            now_iso=T3,
        )
        self.assertEqual(code, 400)
        self.assertEqual(body["reason_code"], "INVALID_FIELDS")

    # -- 3: partner door + private invisibility -----------------------------
    def test_03_partner_door_and_private_invisibility(self) -> None:
        private_title = "泡茶前先把杯子烫一遍"
        shared_title = "出门前在门口停三秒看看钥匙"
        self._mutate(_sketch_payload(title_cn=private_title, visibility="private"))
        self._mutate(_sketch_payload(
            title_cn=shared_title,
            origin_trace_cn="有两次忘带钥匙，后来出门前顺手停一下摸一摸口袋",
            context_scope_cn="每次出门",
            visibility="shared",
        ), now_iso=T1)

        stored = self._project()["personal_rituals"]
        private_id = stored[0]["ritual_id"]
        shared_id = stored[1]["ritual_id"]

        # Both practices get a voluntary variant, so the read-only cue is live.
        self._mutate({
            "action": "curate_personal_ritual",
            "ritual_op": "reuse_variant",
            "ritual_id": private_id,
            "variant_note_cn": "阴天的早上也先用热水烫一下",
        }, now_iso=T2)
        self._mutate({
            "action": "curate_personal_ritual",
            "ritual_op": "reuse_variant",
            "ritual_id": shared_id,
            "variant_note_cn": "早上也顺手续上这个停三秒",
        }, now_iso=T2)

        for payload in (
            _sketch_payload(title_cn="替伴侣决定的做法", visibility="shared"),
            {
                "action": "curate_personal_ritual",
                "ritual_op": "reuse_variant",
                "ritual_id": private_id,
                "variant_note_cn": "伴侣想替主体改名",
            },
        ):
            code, body = view.mutate_v08_emergent_echoes_view(
                self.root, payload, actor_side="partner", now_iso=T2
            )
            self.assertEqual(code, 403)
            self.assertEqual(body["reason_code"], "FORBIDDEN_ACTOR_SIDE")

        subject = self._project(subject=True)
        self.assertEqual(len(subject["personal_rituals"]), 2)

        partner = self._project(subject=False)
        self.assertEqual(len(partner["personal_rituals"]), 1)
        visible = partner["personal_rituals"][0]
        self.assertEqual(visible["title_cn"], shared_title)
        self.assertNotIn(private_title, json.dumps(partner, ensure_ascii=False))
        # No hidden count leaks either: the projection carries no count field.
        for key in ("personal_ritual_count", "private_ritual_count", "hidden_count"):
            self.assertNotIn(key, partner)

        # The situational matcher obeys the same visibility gate.
        shared_match = engine.match_situational_echoes_zero_write(
            self.root, include_private=False, now_iso=T1
        )
        self.assertEqual(
            [row["title_cn"] for row in shared_match["relevant_personal_rituals"]],
            [shared_title],
        )
        self.assertNotIn(private_title, json.dumps(shared_match, ensure_ascii=False))

        # A shared reused variant is a compact summary line; a private one is not.
        partner_summary = view.summarize_emergent_echoes_for_context(
            self.root, incoming_text="出门", include_private=False
        )
        self.assertIn("停三秒", partner_summary)
        self.assertNotIn(private_title, partner_summary)

    # -- 4: strict zero-write reads -----------------------------------------
    def test_04_reads_are_zero_write(self) -> None:
        absent = Path(tempfile.mkdtemp(prefix="v08-rituals-empty-")) / "never"
        empty = engine.read_emergent_echoes_zero_write(
            absent, has_subject_capability=True, now_iso=T0
        )
        self.assertEqual(empty["personal_rituals"], [])
        self.assertEqual(empty["llm_calls"], 0)
        engine.match_situational_echoes_zero_write(absent, include_private=True, now_iso=T0)
        view.read_v08_emergent_echoes_view(absent, has_subject_capability=True, now_iso=T0)
        view.summarize_emergent_echoes_for_context(
            absent, incoming_text="随便什么情形", include_private=True
        )
        self.assertFalse(absent.exists(), "an absent root must stay absent after every read")

        # A legacy state file without ``personal_rituals`` reads as [] and is not rewritten.
        legacy_dir = Path(tempfile.mkdtemp(prefix="v08-rituals-legacy-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(legacy_dir, ignore_errors=True))
        (legacy_dir / "phone").mkdir()
        legacy_path = legacy_dir / "phone" / "v08_emergent_echoes.json"
        legacy_path.write_text(
            json.dumps({
                "schema_version": engine.STATE_SCHEMA_VERSION,
                "revision": 3,
                "updated_at": T0,
                "next_seq": 4,
            }, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        before = _snapshot(legacy_dir)
        legacy = engine.read_emergent_echoes_zero_write(
            legacy_dir, has_subject_capability=True, now_iso=T0
        )
        self.assertEqual(legacy["personal_rituals"], [])
        engine.match_situational_echoes_zero_write(
            legacy_dir, include_private=True, now_iso=T0
        )
        self.assertEqual(before, _snapshot(legacy_dir), "reads must not rewrite the state file")

        # Populated root: projection, matcher and summary are byte-identical after reads.
        self._mutate(_sketch_payload())
        ritual_id = self._project()["personal_rituals"][0]["ritual_id"]
        self._mutate({
            "action": "curate_personal_ritual",
            "ritual_op": "reuse_variant",
            "ritual_id": ritual_id,
            "variant_note_cn": "阴天的早上也照做",
        }, now_iso=T1)

        before_populated = _snapshot(self.root)
        self._project(subject=True)
        self._project(subject=False)
        engine.match_situational_echoes_zero_write(
            self.root, incoming_text="泡茶", include_private=True, now_iso=T1
        )
        engine.match_situational_echoes_zero_write(
            self.root, incoming_text="泡茶", include_private=False, now_iso=T1
        )
        view.read_v08_emergent_echoes_view(self.root, has_subject_capability=True, now_iso=T1)
        view.read_v08_emergent_echoes_view(self.root, has_subject_capability=False, now_iso=T1)
        view.summarize_emergent_echoes_for_context(
            self.root, incoming_text="泡茶", include_private=True
        )
        self.assertEqual(
            before_populated,
            _snapshot(self.root),
            "personal-ritual reads must be byte- and mtime-identical",
        )


if __name__ == "__main__":
    unittest.main()

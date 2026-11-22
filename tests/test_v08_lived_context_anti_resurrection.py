#!/usr/bin/env python3
"""Round B2b: anti-resurrection TTLs, pre-miss epistemic masking, consensus gate.

Coverage:
* disk-fallback ``emotional_tension`` rows (spaces / open favor / kept notes) are
  TTL-filtered against the caller's committed ``now_iso``; expired short moods,
  favors and longing notes never re-enter lived context while the ledger on disk
  stays byte-identical;
* ``lingering_threads`` honour ``active_now is False`` and compare ``expires_at``
  to the committed clock, not a fresh OS ``datetime.now()``;
* pre-miss ``stance_containment_view`` subject projections hide the moved ground
  truth (``impression_stale`` stripped, believed place projected) so World A and
  World B produce identical lived-context lines; the partner view keeps the God
  view; only a witnessed miss may surface afterwards, still without the new place;
* ``shared_idioms`` mutual-consensus framing / ``+3`` boost is reserved for
  ``confirmed`` rows; ``proposed`` rows render as a one-sided proposal with zero
  boost; unilateral ``divergent_notices`` keep per-actor attribution.

Run (WSL):
``/tmp/rc104-venv/bin/python -B -m unittest tests.test_v08_lived_context_anti_resurrection -v``
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chiyo_bundle import lived_context_bridge as bridge  # noqa: E402
from components.phone import stance_containment_view as stance  # noqa: E402

T0 = "2026-11-16T10:00:00Z"
T_MINUS_3H = "2026-11-16T07:00:00Z"
T_MINUS_10M = "2026-11-16T09:50:00Z"
T_PLUS_2H = "2026-11-16T12:00:00Z"
T_MINUS_4D = "2026-11-12T09:00:00Z"


def _cfg(root: Path) -> dict[str, Any]:
    return {
        "day_root": str(root),
        "memory": True,
        "memory_tool_policy": "restricted",
        "phone": {"enabled": True, "private_artifacts": True},
    }


def _snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    snap: dict[str, tuple[int, int, str]] = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root)).replace("\\", "/")
        st = path.lstat()
        if path.is_file() and not path.is_symlink():
            snap[rel] = (
                int(st.st_size),
                int(st.st_mtime_ns),
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        else:
            snap[rel] = (0, int(st.st_mtime_ns), "dir")
    return snap


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _now(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


class _DiskRootCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-anti-resurrection-")
        self.root = Path(self._tmp.name)
        (self.root / "phone").mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _render(self, incoming: str = "", now_iso: str = T0) -> str:
        return bridge.render_lived_companion_context(
            self.root,
            _cfg(self.root),
            incoming,
            ephemeral_hint="",
            now_iso=now_iso,
        )


class DiskFallbackTTLTest(_DiskRootCase):
    def test_01_expired_short_space_never_resurrects(self) -> None:
        _write_json(
            self.root / "phone" / "emotional_tension.json",
            {
                "quarrel_domain": {
                    "spaces_by_incident": {
                        "inc-1": {
                            "incident_ref": "inc-1",
                            "space_style": "SHORT",
                            "declared_at": T_MINUS_3H,
                            "spoken_aloud": True,
                        }
                    }
                }
            },
        )
        before = _snapshot(self.root)
        for _ in range(3):
            self.assertNotIn("独处空间", self._render())
        self.assertEqual(_snapshot(self.root), before, "renders must stay zero-write")

        # A genuinely live SHORT space still surfaces.
        _write_json(
            self.root / "phone" / "emotional_tension.json",
            {
                "quarrel_domain": {
                    "spaces_by_incident": {
                        "inc-2": {
                            "incident_ref": "inc-2",
                            "space_style": "SHORT",
                            "declared_at": T_MINUS_10M,
                            "spoken_aloud": True,
                        }
                    }
                }
            },
        )
        self.assertIn("独处空间", self._render())

    def test_02_until_sleep_space_without_rows_and_expiry(self) -> None:
        # Legacy projection shape (in_space=True, no rows) keeps its honest cue.
        _write_json(
            self.root / "phone" / "emotional_tension.json",
            {"quarrel": {"in_space": True}},
        )
        self.assertIn("独处空间", self._render())

        # in_space=True but every declared row has expired -> silent, no ghost.
        _write_json(
            self.root / "phone" / "emotional_tension.json",
            {
                "quarrel": {
                    "in_space": True,
                    "spaces_by_incident": {
                        "inc-old": {
                            "space_style": "SHORT",
                            "declared_at": T_MINUS_3H,
                        }
                    },
                }
            },
        )
        self.assertNotIn("独处空间", self._render())

        # UNTIL_SLEEP has no elapsed-time expiry and keeps showing.
        _write_json(
            self.root / "phone" / "emotional_tension.json",
            {
                "quarrel_domain": {
                    "spaces_by_incident": {
                        "inc-sleep": {
                            "space_style": "UNTIL_SLEEP",
                            "declared_at": T_MINUS_3H,
                        }
                    }
                }
            },
        )
        self.assertIn("独处空间", self._render())

    def test_03_expired_open_favor_never_resurrects(self) -> None:
        _write_json(
            self.root / "phone" / "emotional_tension.json",
            {
                "asking_domain": {
                    "open_favor": {
                        "favor_id": "fav-old",
                        "favor_kind": "COMPANY",
                        "summary": "顺路带杯热茶",
                        "status": "OPEN",
                        "asked_at": T_MINUS_3H,
                        "expires_at": "2026-11-16T09:00:00Z",
                    }
                }
            },
        )
        before = _snapshot(self.root)
        for _ in range(3):
            self.assertNotIn("顺手小忙", self._render())
        self.assertEqual(_snapshot(self.root), before)

        _write_json(
            self.root / "phone" / "emotional_tension.json",
            {
                "asking_domain": {
                    "open_favor": {
                        "favor_id": "fav-live",
                        "favor_kind": "COMPANY",
                        "summary": "顺路带杯热茶",
                        "status": "OPEN",
                        "asked_at": T_MINUS_10M,
                        "expires_at": T_PLUS_2H,
                    }
                }
            },
        )
        self.assertIn("顺手小忙", self._render())

    def test_04_expired_kept_notes_never_resurrect(self) -> None:
        _write_json(
            self.root / "phone" / "emotional_tension.json",
            {
                "missing_domain": {
                    "kept_notes": [
                        {
                            "note_id": "note-old",
                            "source_ref": "legacy:old",
                            "kind": "MISSING",
                            "summary": "很久以前留的那句话",
                            "created_at": T_MINUS_4D,
                        },
                        {
                            "note_id": "note-live",
                            "source_ref": "legacy:live",
                            "kind": "MISSING",
                            "summary": "今早留下的那句想念",
                            "created_at": T_MINUS_10M,
                        },
                    ]
                }
            },
        )
        before = _snapshot(self.root)
        rendered = self._render()
        self.assertIn("今早留下的那句想念", rendered)
        self.assertNotIn("很久以前留的那句话", rendered)
        self.assertEqual(_snapshot(self.root), before)

        # Only expired notes -> nothing at all.
        _write_json(
            self.root / "phone" / "emotional_tension.json",
            {
                "missing_domain": {
                    "kept_notes": [
                        {
                            "note_id": "note-old",
                            "kind": "MISSING",
                            "summary": "很久以前留的那句话",
                            "created_at": T_MINUS_4D,
                        }
                    ]
                }
            },
        )
        self.assertNotIn("想念留话", self._render())


class LingeringThreadClockTest(_DiskRootCase):
    def _doc(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {"lingering_threads": rows}

    def test_01_active_now_false_is_never_injected(self) -> None:
        _write_json(
            self.root / "phone" / "v08_emergent_echoes.json",
            self._doc(
                [
                    {
                        "thread_id": "thread-dead",
                        "summary_cn": "已经翻篇的话",
                        "status": "open",
                        "visibility": "shared",
                        "expires_at": "2026-06-01T00:00:00Z",
                        "active_now": False,
                    }
                ]
            ),
        )
        self.assertNotIn("挂着的一缕余绪", self._render(now_iso="2026-01-01T00:00:00Z"))

    def test_02_expires_at_uses_committed_clock_not_os_clock(self) -> None:
        # OS now (2026-11-16) is already past this expiry, but the committed
        # simulation clock (2026-01-01) is not -> it must still be live.
        _write_json(
            self.root / "phone" / "v08_emergent_echoes.json",
            self._doc(
                [
                    {
                        "thread_id": "thread-live-by-committed",
                        "summary_cn": "那封还没寄出的信",
                        "status": "open",
                        "visibility": "shared",
                        "expires_at": "2026-06-01T00:00:00Z",
                        "active_now": True,
                    }
                ]
            ),
        )
        before = _snapshot(self.root)
        rendered = self._render(now_iso="2026-01-01T00:00:00Z")
        self.assertIn("那封还没寄出的信", rendered)
        self.assertEqual(_snapshot(self.root), before)

        # Same row, committed clock past expiry -> excluded while staying on disk.
        late = self._render(now_iso="2026-07-01T00:00:00Z")
        self.assertNotIn("那封还没寄出的信", late)
        doc = json.loads(
            (self.root / "phone" / "v08_emergent_echoes.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(doc["lingering_threads"]), 1)

    def test_03_formatter_accepts_committed_now_dt(self) -> None:
        doc = self._doc(
            [
                {
                    "thread_id": "thread-x",
                    "summary_cn": "桌角那盆薄荷",
                    "status": "open",
                    "visibility": "shared",
                    "expires_at": "2026-06-01T00:00:00Z",
                }
            ]
        )
        live = bridge._format_v08_emergent_echoes_micro(
            doc, incoming_text="", include_private=True, now_dt=_now("2026-01-01T00:00:00Z")
        )
        self.assertIn("桌角那盆薄荷", live)
        expired = bridge._format_v08_emergent_echoes_micro(
            doc, incoming_text="", include_private=True, now_dt=_now("2026-07-01T00:00:00Z")
        )
        self.assertEqual(expired, "")


class ConsensusGateTest(unittest.TestCase):
    def _doc(self) -> dict[str, Any]:
        return {
            "shared_idioms": [
                {
                    "idiom_id": "idiom-confirmed",
                    "phrase": "把灯留一盏",
                    "meaning": "晚归也有人在等",
                    "origin": "搬进来的第一晚",
                    "status": "confirmed",
                    "confirmed_by": "partner",
                },
                {
                    "idiom_id": "idiom-proposed",
                    "phrase": "老地方见",
                    "meaning": "不用多说",
                    "origin": "上周随口提的",
                    "status": "proposed",
                    "proposed_by": "partner",
                },
            ],
            "divergent_notices": [
                {
                    "target_ref": "art:wall",
                    "target_title_cn": "墙上的画",
                    "subject_notice_cn": "",
                    "partner_notice_cn": "觉得该再添几笔",
                }
            ],
        }

    def setUp(self) -> None:
        self.now = _now(T0)
        self.doc = self._doc()

    def test_01_confirmed_idiom_gets_mutual_consensus_and_boost(self) -> None:
        text = bridge._format_v08_emergent_echoes_micro(
            self.doc,
            incoming_text="今晚还会把灯留一盏吗",
            include_private=True,
            now_dt=self.now,
        )
        self.assertIn("默契说法「把灯留一盏」", text)
        self.assertEqual(
            bridge._situation_boost("v08_echoes_line", text, "今晚还会把灯留一盏吗"), 3
        )

    def test_02_proposed_idiom_is_one_sided_with_zero_boost(self) -> None:
        text = bridge._format_v08_emergent_echoes_micro(
            self.doc,
            incoming_text="要不要老地方见",
            include_private=True,
            now_dt=self.now,
        )
        self.assertIn("提议中", text)
        self.assertIn("由对方提出", text)
        self.assertIn("尚未双方确认", text)
        self.assertNotIn("默契说法", text)
        self.assertEqual(
            bridge._situation_boost("v08_echoes_line", text, "要不要老地方见"), 0
        )

    def test_03_unilateral_divergent_notice_keeps_attribution(self) -> None:
        text = bridge._format_v08_emergent_echoes_micro(
            self.doc,
            incoming_text="墙上的画你怎么看",
            include_private=True,
            now_dt=self.now,
        )
        self.assertIn("各自看法：墙上的画", text)
        self.assertIn("不是共识", text)
        self.assertIn("还没有自己的明确看法", text)
        self.assertIn("对方：觉得该再添几笔", text)

    def test_04_rendered_context_keeps_proposal_one_sided(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v08-consensus-") as tmp:
            root = Path(tmp)
            (root / "phone").mkdir(parents=True)
            _write_json(root / "phone" / "v08_emergent_echoes.json", self.doc)
            rendered = bridge.render_lived_companion_context(
                root, _cfg(root), "要不要老地方见", ephemeral_hint="", now_iso=T0
            )
            self.assertIn("说法提议中「老地方见」", rendered)
            self.assertNotIn("默契说法「老地方见」", rendered)


class StancePremissMaskingTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-stance-mask-")
        self.root = Path(self._tmp.name)
        (self.root / "phone").mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _pick_pen_into_cup(self) -> None:
        stance.apply_stance_containment_action(
            self.root,
            {
                "action": "pick_and_put_in_container",
                "item_id": "fountain_pen",
                "container_id": "pen_cup",
            },
            now=T0,
            subject_view=True,
        )

    def _row(self, proj: dict[str, Any], kind: str, ident: str) -> dict[str, Any]:
        rows = proj["object_containment"]["containers" if kind == "c" else "items"]
        return next(row for row in rows if row[kind == "c" and "container_id" or "item_id"] == ident)

    def test_01_world_a_and_b_subject_projections_identical_pre_miss(self) -> None:
        # World B: same starting state, plus a secret partner move.
        self._pick_pen_into_cup()
        stance.apply_stance_containment_action(
            self.root,
            {
                "action": "partner_move_container",
                "container_id": "pen_cup",
                "to_place_id": "entry_stand",
            },
            now="2026-11-16T10:01:00Z",
            subject_view=False,
        )
        subject_proj = stance.read_stance_containment(
            self.root, now="2026-11-16T10:02:00Z", subject_view=True
        )
        partner_proj = stance.read_stance_containment(
            self.root, now="2026-11-16T10:02:00Z", subject_view=False
        )

        cup = self._row(subject_proj, "c", "pen_cup")
        pen = self._row(subject_proj, "i", "fountain_pen")
        # Believed location is projected; the moved ground truth is withheld.
        self.assertEqual(cup["place_id"], "study_desk")
        self.assertEqual(cup["effective_place_id"], "study_desk")
        self.assertEqual(cup["room_id"], "study_room")
        self.assertEqual(pen["place_id"], "study_desk")
        self.assertEqual(pen["effective_place_id"], "study_desk")
        self.assertNotIn("impression_stale", cup)
        self.assertNotIn("impression_stale", pen)
        self.assertFalse(cup["not_at_last_seen_place"])
        self.assertEqual(subject_proj["object_containment"]["stale_impression_count"], 0)
        cues = " ".join(subject_proj["object_containment"]["subject_cognitive_cues"])
        self.assertIn("study_desk", cues)
        self.assertNotIn("entry_stand", cues)

        # The partner keeps the God view.
        cup_partner = self._row(partner_proj, "c", "pen_cup")
        self.assertTrue(cup_partner["impression_stale"])
        self.assertEqual(cup_partner["effective_place_id"], "entry_stand")

        # A fresh World A (no move) projects the exact same subject row.
        with tempfile.TemporaryDirectory(prefix="v08-stance-world-a-") as tmp_a:
            root_a = Path(tmp_a)
            (root_a / "phone").mkdir(parents=True)
            stance.apply_stance_containment_action(
                root_a,
                {
                    "action": "pick_and_put_in_container",
                    "item_id": "fountain_pen",
                    "container_id": "pen_cup",
                },
                now=T0,
                subject_view=True,
            )
            world_a = stance.read_stance_containment(
                root_a, now="2026-11-16T10:02:00Z", subject_view=True
            )
            self.assertEqual(self._row(world_a, "c", "pen_cup"), cup)
            self.assertEqual(self._row(world_a, "i", "fountain_pen"), pen)

    def test_02_lived_context_lines_identical_pre_miss(self) -> None:
        self._pick_pen_into_cup()
        stance.apply_stance_containment_action(
            self.root,
            {
                "action": "partner_move_container",
                "container_id": "pen_cup",
                "to_place_id": "entry_stand",
            },
            now="2026-11-16T10:01:00Z",
            subject_view=False,
        )

        with tempfile.TemporaryDirectory(prefix="v08-stance-world-a-") as tmp_a:
            root_a = Path(tmp_a)
            (root_a / "phone").mkdir(parents=True)
            stance.apply_stance_containment_action(
                root_a,
                {
                    "action": "pick_and_put_in_container",
                    "item_id": "fountain_pen",
                    "container_id": "pen_cup",
                },
                now=T0,
                subject_view=True,
            )
            world_a = bridge.render_lived_companion_context(
                root_a, _cfg(root_a), "", ephemeral_hint="", now_iso="2026-11-16T10:02:00Z"
            )
            world_b = bridge.render_lived_companion_context(
                self.root, _cfg(self.root), "", ephemeral_hint="", now_iso="2026-11-16T10:02:00Z"
            )
            line_a = next(
                (ln for ln in world_a.splitlines() if "书桌共在与桌面收纳" in ln), ""
            )
            line_b = next(
                (ln for ln in world_b.splitlines() if "书桌共在与桌面收纳" in ln), ""
            )
            self.assertTrue(line_a)
            self.assertEqual(line_a, line_b)
            self.assertNotIn("entry_stand", world_b)
            self.assertNotIn("印象偏差", world_b)

    def test_03_after_witnessed_miss_no_new_place_is_revealed(self) -> None:
        self._pick_pen_into_cup()
        stance.apply_stance_containment_action(
            self.root,
            {
                "action": "partner_move_container",
                "container_id": "pen_cup",
                "to_place_id": "entry_stand",
            },
            now="2026-11-16T10:01:00Z",
            subject_view=False,
        )
        # She reaches the old spot: a witnessed miss, destination still unknown.
        stance.apply_stance_containment_action(
            self.root,
            {"action": "observe_current_room"},
            now="2026-11-16T10:03:00Z",
            subject_view=True,
        )
        proj = stance.read_stance_containment(
            self.root, now="2026-11-16T10:04:00Z", subject_view=True
        )
        pen = self._row(proj, "i", "fountain_pen")
        cup = self._row(proj, "c", "pen_cup")
        self.assertTrue(pen["not_at_last_seen_place"])
        self.assertNotIn("impression_stale", pen)
        self.assertEqual(pen["effective_place_id"], "study_desk")
        self.assertEqual(cup["effective_place_id"], "study_desk")
        # Only the moved pen/cup pair and her cues are checked: the default
        # entryway tray legitimately lives at entry_stand and is not a secret.
        self.assertNotIn(
            "entry_stand",
            json.dumps(
                {
                    "pen": pen,
                    "cup": cup,
                    "cues": proj["object_containment"]["subject_cognitive_cues"],
                },
                ensure_ascii=False,
            ),
        )

        rendered = bridge.render_lived_companion_context(
            self.root, _cfg(self.root), "", ephemeral_hint="", now_iso="2026-11-16T10:04:00Z"
        )
        self.assertIn("不在原印象位置", rendered)
        self.assertIn("未知新位置", rendered)
        self.assertNotIn("entry_stand", rendered)

    def test_04_bridge_drops_god_view_stale_flags_pre_miss(self) -> None:
        # Defense in depth: even a projection carrying the raw God-view flag must
        # not produce an "impression_stale" warning before a witnessed miss.
        stance_doc = {
            "object_containment": {
                "containers": [
                    {"container_id": "pen_cup", "label": "木质笔筒", "place_id": "study_desk"}
                ],
                "items": [
                    {
                        "item_id": "fountain_pen",
                        "label": "墨水钢笔",
                        "placement_kind": "container",
                        "parent_container_id": "pen_cup",
                        "impression_stale": True,
                        "not_at_last_seen_place": False,
                        "subject_last_seen_place": "study_desk",
                    }
                ],
            }
        }
        line = bridge._format_desk_and_containment(
            desk_http=None, stance_doc=stance_doc, now_dt=_now(T0)
        )
        self.assertTrue(line)
        self.assertNotIn("印象偏差", line)
        self.assertNotIn("entry_stand", line)


if __name__ == "__main__":
    unittest.main()

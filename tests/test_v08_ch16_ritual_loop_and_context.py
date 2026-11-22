#!/usr/bin/env python3
"""v0.8 Ch.16 §三 #18 / §四 #7：个人小习惯的零债接入与上下文静默边界。

Coverage (0 product-model calls, strictly zero-write):

* an empty state directory, or a state whose only ``personal_rituals`` row is
  ``faded_quietly``, renders 0 ritual characters and produces 0 candidates;
* a ``visibility == "private"`` practice never appears -- not even its title or
  count -- in the ``include_private=False`` lived-context projection, while a
  shared ``technique_retained`` / ``reused_variant`` renders a compact hint;
* the full lived context stays strictly ``<= 1800`` characters even with an
  oversized ritual title competing for the budget.

Run (Windows):
``python -B -m unittest tests.test_v08_ch16_ritual_loop_and_context -v``
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
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "components" / "phone"),
    str(ROOT / "tests"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from chiyo_bundle import lived_context_bridge as context_bridge  # noqa: E402
import v02_v03_daily_loop_bridge as loop_bridge  # noqa: E402

T0 = "2026-11-16T09:00:00+08:00"
STATE_SCHEMA = "phone.v08.emergent_echoes.v1"
PRIVATE_TITLE = "私密的睡前小动作"
SHARED_TECHNIQUE_TITLE = "泡茶前先把杯子烫一遍"
SHARED_VARIANT_TITLE = "写字前先把桌面抹一遍"


def _cfg(root: Path) -> dict[str, Any]:
    return {
        "day_root": str(root),
        "memory": True,
        "memory_tool_policy": "restricted",
        "phone": {"enabled": True, "private_artifacts": True},
    }


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


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _now(text: str = T0) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _ritual(
    ritual_id: str,
    *,
    status: str,
    visibility: str = "shared",
    title_cn: str = SHARED_TECHNIQUE_TITLE,
) -> dict[str, Any]:
    return {
        "ritual_id": ritual_id,
        "title_cn": title_cn,
        "origin_trace_cn": "那天手边正好有热水，顺手烫了一下，茶确实更稳",
        "context_scope_cn": "晚上在家写东西时",
        "variant_note_cn": "",
        "linked_genealogy_id": "",
        "status": status,
        "visibility": visibility,
        "auto_declared_by_count": False,
        "omission_debt": 0,
        "milestone_consideration_open": status == "milestone_option",
        "created_at": T0,
        "updated_at": T0,
    }


class V08RitualLoopAndContextTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-ch16-ritual-")
        self.root = Path(self._tmp.name)
        (self.root / "phone").mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- helpers ---------------------------------------------------------
    def _state(self, rituals: list[dict[str, Any]] | None = None) -> None:
        _write_json(
            self.root / "phone" / "v08_emergent_echoes.json",
            {
                "schema_version": STATE_SCHEMA,
                "revision": 1,
                "updated_at": T0,
                "next_seq": 100,
                "personal_rituals": list(rituals or []),
                "future_self_questions": [],
                "shared_idioms": [],
                "memory_reinterpretations": [],
                "style_and_interest_sparks": [],
                "divergent_notices": [],
                "operation_log": {},
            },
        )

    def _render(self, incoming: str = "") -> str:
        return context_bridge.render_lived_companion_context(
            self.root,
            _cfg(self.root),
            incoming,
            ephemeral_hint="",
            now_iso=T0,
        )

    def _collect(self) -> dict[str, Any]:
        return loop_bridge.collect_v02_v03_loop_candidates(self.root, now_iso=T0)

    @staticmethod
    def _ritual_refs(collected: dict[str, Any]) -> list[str]:
        refs = []
        for desc in (collected.get("descriptors") or {}).values():
            if not isinstance(desc, dict):
                continue
            source_ref = str(desc.get("source_ref") or "")
            if source_ref.startswith("v08_echo:ritual:"):
                refs.append(source_ref)
        return refs

    @staticmethod
    def _format(
        doc: Any,
        *,
        include_private: bool,
        incoming: str = "",
    ) -> str:
        return context_bridge._format_v08_emergent_echoes_micro(
            doc,
            incoming_text=incoming,
            include_private=include_private,
            now_dt=_now(),
        )

    # -- 1: empty / faded stays 0-char silent, zero-write, zero candidate --
    def test_01_empty_or_faded_is_silent_zero_write_zero_candidate(self) -> None:
        before = _snapshot(self.root)
        self.assertEqual(self._format({}, include_private=False), "")
        self.assertEqual(self._format({}, include_private=True), "")
        rendered_empty = self._render()
        self.assertNotIn("小习惯淡了", rendered_empty)
        self.assertNotIn("顺手惯例", rendered_empty)
        collected_empty = self._collect()
        self.assertEqual(self._ritual_refs(collected_empty), [])
        self.assertEqual(_snapshot(self.root), before, "empty render must be zero-write")

        self._state([_ritual("rit_faded", status="faded_quietly")])
        before = _snapshot(self.root)
        faded_doc = {
            "personal_rituals": [
                _ritual("rit_faded", status="faded_quietly"),
            ]
        }
        self.assertEqual(self._format(faded_doc, include_private=False), "")
        rendered_faded = self._render()
        self.assertNotIn("小习惯淡了", rendered_faded)
        self.assertNotIn("顺手惯例", rendered_faded)
        self.assertEqual(self._ritual_refs(self._collect()), [])
        self.assertEqual(_snapshot(self.root), before, "faded render must be zero-write")

    # -- 2: private never leaks; shared rituals render compactly ----------
    def test_02_private_is_invisible_and_shared_rituals_render(self) -> None:
        private_only = {
            "personal_rituals": [
                _ritual(
                    "rit_private_technique",
                    status="technique_retained",
                    visibility="private",
                    title_cn=PRIVATE_TITLE,
                ),
                _ritual(
                    "rit_private_variant",
                    status="reused_variant",
                    visibility="private",
                    title_cn=PRIVATE_TITLE + "变体",
                ),
            ]
        }
        out_private = self._format(private_only, include_private=False)
        self.assertEqual(out_private, "")
        self.assertNotIn(PRIVATE_TITLE, out_private)

        mixed_technique = {
            "personal_rituals": [
                _ritual(
                    "rit_private_technique",
                    status="technique_retained",
                    visibility="private",
                    title_cn=PRIVATE_TITLE,
                ),
                _ritual(
                    "rit_shared_technique",
                    status="technique_retained",
                    visibility="shared",
                    title_cn=SHARED_TECHNIQUE_TITLE,
                ),
            ]
        }
        out_technique = self._format(mixed_technique, include_private=False)
        self.assertIn("小习惯淡了·做法仍在", out_technique)
        self.assertIn(SHARED_TECHNIQUE_TITLE, out_technique)
        self.assertNotIn(PRIVATE_TITLE, out_technique)

        mixed_variant = {
            "personal_rituals": [
                _ritual(
                    "rit_private_variant",
                    status="reused_variant",
                    visibility="private",
                    title_cn=PRIVATE_TITLE + "变体",
                ),
                _ritual(
                    "rit_shared_variant",
                    status="reused_variant",
                    visibility="shared",
                    title_cn=SHARED_VARIANT_TITLE,
                ),
            ]
        }
        out_variant = self._format(mixed_variant, include_private=False)
        self.assertIn("手边顺手惯例", out_variant)
        self.assertIn(SHARED_VARIANT_TITLE, out_variant)
        self.assertNotIn(PRIVATE_TITLE, out_variant)

        milestone = {
            "personal_rituals": [
                _ritual(
                    "rit_shared_milestone",
                    status="milestone_option",
                    visibility="shared",
                    title_cn=SHARED_VARIANT_TITLE,
                )
            ]
        }
        self.assertIn("手边顺手惯例", self._format(milestone, include_private=False))

    # -- 3: the whole lived context stays within 1800 characters ---------
    def test_03_total_context_stays_within_1800(self) -> None:
        long_title = "写东西前先把桌面顺序排一遍再点一盏小灯" * 4
        self._state([
            _ritual(
                "rit_long_technique",
                status="technique_retained",
                visibility="shared",
                title_cn=long_title,
            ),
            _ritual(
                "rit_long_variant",
                status="reused_variant",
                visibility="shared",
                title_cn=long_title,
            ),
        ])
        breakdown = context_bridge.measure_context_budget_breakdown(
            self.root,
            _cfg(self.root),
            "",
            ephemeral_hint="",
            now_iso=T0,
        )
        self.assertLessEqual(
            breakdown["total_chars"], context_bridge.MAX_LIVED_CONTEXT_CHARS
        )
        self.assertTrue(breakdown["within_budget"])
        rendered = breakdown["rendered_text"]
        self.assertIn("小习惯淡了·做法仍在", rendered)
        self.assertNotIn(long_title, rendered)
        # The hint's title is compacted to a bounded slice, never the raw long title.
        self.assertLessEqual(len(rendered), context_bridge.MAX_LIVED_CONTEXT_CHARS)


if __name__ == "__main__":
    unittest.main()

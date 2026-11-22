#!/usr/bin/env python3
"""Ch.23：局部重新认识，不整段翻盘；假说与真实页码的边界。

Coverage:
* a second interpretation of the *same* episode keeps the locked
  ``original_fact_cn`` and appends a revision -- the old reservation
  (``preserved_reservation_cn``) and the newly noticed facet
  (``appreciation_facet_cn``) coexist instead of flipping the whole episode into
  pure praise;
* ``hypothesis_status`` only accepts ``open_question`` /
  ``revised_by_observation`` / ``confirmed``; an unknown value is a 400
  ``INVALID_FIELDS`` at both the view gate and the engine;
* a page is recorded only when the caller supplies a real one: with no
  ``exact_page_ref`` the row stays ``exact_page_known is False`` and
  ``exact_page_ref == ""`` -- never inferred from the title or the excerpt;
* before ``shared_with_partner=True`` the partner projection omits the row
  entirely; after sharing it shows only the shared view, while the subject keeps
  every earlier private reading in ``revisions``;
* zero-write reads and ``llm_calls == 0`` throughout.

Run: ``python -B -m unittest tests.test_v08_ch23_partial_reinterpretation -v``.
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
T2 = "2026-11-18T09:00:00+08:00"


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


def _memory_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": "record_memory_reinterpretation",
        "episode_ref": "book-clay-01",
        "episode_title_cn": "《窑火笔记》",
        "original_fact_cn": "这本书讲的是窑变，可我前半本读得很不耐烦",
        "prior_view_cn": "觉得作者絮叨，翻了几页就搁下了",
        "new_view_cn": "还是觉得絮叨，暂时不想继续读",
        "trigger_summary_cn": "整理书架时又把它抽出来",
        "shared_with_partner": False,
    }
    payload.update(overrides)
    return payload


class Ch23PartialReinterpretationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-ch23-")
        self.root = Path(self._tmp.name)
        (self.root / "phone").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- helpers ---------------------------------------------------------
    def _mutate(self, payload: dict[str, Any], *, now_iso: str = T0) -> dict[str, Any]:
        projected, _status = engine.apply_emergent_echoes_mutation(
            self.root, payload, actor_side="subject", now_iso=now_iso
        )
        return projected

    def _project(self, *, subject: bool = True) -> dict[str, Any]:
        return engine.read_emergent_echoes_zero_write(
            self.root, has_subject_capability=subject, now_iso=T0
        )

    def _row(self, *, subject: bool = True) -> dict[str, Any]:
        rows = self._project(subject=subject)["memory_reinterpretations"]
        for row in rows:
            if row.get("episode_ref") == "book-clay-01":
                return row
        raise AssertionError("book-clay-01 row missing from projection")

    # -- partial reinterpretation, never a whole-sale flip -----------------
    def test_partial_reinterpretation_keeps_reservation_and_facet(self) -> None:
        first = self._mutate(_memory_payload())["memory_reinterpretations"][0]
        self.assertEqual(first["original_fact_cn"], _memory_payload()["original_fact_cn"])
        self.assertEqual(first["preserved_reservation_cn"], "")
        self.assertEqual(first["appreciation_facet_cn"], "")
        self.assertEqual(first["hypothesis_status"], "")

        second = self._mutate(_memory_payload(
            original_fact_cn="这条完全不同的“事实”绝不允许覆写",
            prior_view_cn="这回翻到第三章",
            new_view_cn="第三章讲窑变缩尺那段其实有意思",
            trigger_summary_cn="雨天无事，随手翻到第三章",
            preserved_reservation_cn="前半本絮叨还是不太喜欢",
            appreciation_facet_cn="第三章讲窑变缩尺的那段其实很有意思",
            hypothesis_status="revised_by_observation",
        ), now_iso=T1)["memory_reinterpretations"][0]

        # The locked first fact stays untouched; the rest of the episode is not
        # rewritten into pure praise.
        self.assertEqual(second["reinterpretation_id"], first["reinterpretation_id"])
        self.assertEqual(second["original_fact_cn"], first["original_fact_cn"])
        self.assertEqual(second["preserved_reservation_cn"], "前半本絮叨还是不太喜欢")
        self.assertEqual(
            second["appreciation_facet_cn"], "第三章讲窑变缩尺的那段其实很有意思"
        )
        self.assertEqual(second["hypothesis_status"], "revised_by_observation")
        self.assertEqual(second["current_view_cn"], "第三章讲窑变缩尺那段其实有意思")
        self.assertEqual(len(second["revisions"]), 2)

        latest = second["revisions"][-1]
        self.assertEqual(latest["preserved_reservation_cn"], "前半本絮叨还是不太喜欢")
        self.assertEqual(latest["appreciation_facet_cn"], "第三章讲窑变缩尺的那段其实很有意思")
        self.assertEqual(latest["hypothesis_status"], "revised_by_observation")
        self.assertEqual(latest["recorded_at"], T1)
        # The earlier reading survives in the revision log.
        self.assertEqual(second["revisions"][0]["new_view_cn"], "还是觉得絮叨，暂时不想继续读")

        # Ch.23: an open question is a legitimate, non-final state.
        open_question = self._mutate(_memory_payload(
            new_view_cn="也许还有几章值得再翻翻",
            hypothesis_status="open_question",
        ), now_iso=T2)["memory_reinterpretations"][0]
        self.assertEqual(open_question["hypothesis_status"], "open_question")
        # Omitted fields keep the previously recorded reservation / facet.
        self.assertEqual(open_question["preserved_reservation_cn"], "前半本絮叨还是不太喜欢")
        self.assertEqual(
            open_question["appreciation_facet_cn"], "第三章讲窑变缩尺的那段其实很有意思"
        )
        self.assertEqual(open_question["original_fact_cn"], first["original_fact_cn"])

    def test_invalid_hypothesis_status_is_refused(self) -> None:
        with self.assertRaises(engine.V08ValidationError) as ctx:
            self._mutate(_memory_payload(hypothesis_status="totally_converted"))
        self.assertEqual(ctx.exception.reason_code, "INVALID_FIELDS")

        code, body = view.mutate_v08_emergent_echoes_view(
            self.root,
            _memory_payload(hypothesis_status="totally_converted"),
            actor_side="subject",
            now_iso=T0,
        )
        self.assertEqual(code, 400)
        self.assertEqual(body["reason_code"], "INVALID_FIELDS")
        self.assertEqual(body["llm_calls"], 0)

    # -- page references are never fabricated ------------------------------
    def test_no_page_is_ever_fabricated(self) -> None:
        row = self._mutate(_memory_payload())["memory_reinterpretations"][0]
        self.assertIs(row["exact_page_known"], False)
        self.assertEqual(row["exact_page_ref"], "")
        self.assertIs(row["revisions"][0]["exact_page_known"], False)
        self.assertEqual(row["revisions"][0]["exact_page_ref"], "")

        updated = self._mutate(_memory_payload(
            new_view_cn="第三章那段其实有意思",
            appreciation_facet_cn="第三章讲窑变缩尺的那段其实很有意思",
            hypothesis_status="revised_by_observation",
        ), now_iso=T1)["memory_reinterpretations"][0]
        self.assertIs(updated["exact_page_known"], False)
        self.assertEqual(updated["exact_page_ref"], "")

        # Only a caller-supplied real page is recorded.
        paged = self._mutate(_memory_payload(
            new_view_cn="翻到那一页又看了一遍",
            exact_page_ref="p.12",
        ), now_iso=T2)["memory_reinterpretations"][0]
        self.assertIs(paged["exact_page_known"], True)
        self.assertEqual(paged["exact_page_ref"], "p.12")
        self.assertEqual(paged["revisions"][-1]["exact_page_ref"], "p.12")

        # A malformed page is refused rather than guessed.
        with self.assertRaises(engine.V08ValidationError) as ctx:
            self._mutate(_memory_payload(exact_page_ref="大概在中间"))
        self.assertEqual(ctx.exception.reason_code, "INVALID_FIELDS")

    # -- partner gate: private readings stay private until shared ----------
    def test_partner_only_sees_the_shared_view(self) -> None:
        self._mutate(_memory_payload())
        self._mutate(_memory_payload(
            new_view_cn="第三章讲窑变缩尺那段其实有意思",
            preserved_reservation_cn="前半本絮叨还是不太喜欢",
            appreciation_facet_cn="第三章讲窑变缩尺的那段其实很有意思",
            hypothesis_status="revised_by_observation",
        ), now_iso=T1)

        self.assertEqual(self._project(subject=False)["memory_reinterpretations"], [])
        subject_row = self._row(subject=True)
        self.assertFalse(subject_row["shared_with_partner"])
        self.assertEqual(len(subject_row["revisions"]), 2)

        shared = self._mutate(_memory_payload(
            new_view_cn="第三章讲窑变缩尺那段其实有意思",
            shared_with_partner=True,
        ), now_iso=T2)["memory_reinterpretations"][0]
        self.assertTrue(shared["shared_with_partner"])

        partner_rows = self._project(subject=False)["memory_reinterpretations"]
        self.assertEqual(len(partner_rows), 1)
        partner_row = partner_rows[0]
        self.assertEqual(partner_row["episode_ref"], "book-clay-01")
        self.assertEqual(partner_row["current_view_cn"], "第三章讲窑变缩尺那段其实有意思")
        self.assertEqual(
            partner_row["preserved_reservation_cn"], "前半本絮叨还是不太喜欢"
        )
        self.assertEqual(
            partner_row["appreciation_facet_cn"], "第三章讲窑变缩尺的那段其实很有意思"
        )
        # The subject retains the private earlier readings in the revision log.
        subject_row = self._row(subject=True)
        self.assertEqual(subject_row["revisions"][0]["new_view_cn"], "还是觉得絮叨，暂时不想继续读")
        self.assertEqual(len(subject_row["revisions"]), 3)

    # -- zero write / zero model -------------------------------------------
    def test_reads_are_zero_write_and_never_call_a_model(self) -> None:
        self._mutate(_memory_payload())
        self._mutate(_memory_payload(
            new_view_cn="第三章讲窑变缩尺那段其实有意思",
            appreciation_facet_cn="第三章讲窑变缩尺的那段其实很有意思",
        ), now_iso=T1)

        before = _snapshot(self.root)
        subject = self._project(subject=True)
        partner = self._project(subject=False)
        view.read_v08_emergent_echoes_view(self.root, has_subject_capability=True, now_iso=T0)
        engine.match_situational_echoes_zero_write(
            self.root, incoming_text="窑变缩尺", include_private=True
        )
        after = _snapshot(self.root)

        self.assertEqual(before, after, "reads must stay byte- and mtime-identical")
        self.assertEqual(subject["llm_calls"], 0)
        self.assertEqual(partner["llm_calls"], 0)

        row = subject["memory_reinterpretations"][0]
        self.assertIs(row["exact_page_known"], False)
        self.assertEqual(row["exact_page_ref"], "")


if __name__ == "__main__":
    unittest.main()

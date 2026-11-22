#!/usr/bin/env python3
"""v0.8 Ch.50 shared notebook / Ch.53 surprise timing / Ch.50 §五 distinct notes.

Covers the daily-loop and lived-context seams added for:

* A partner line left on the shared notebook becomes at most ONE optional,
  zero-debt cue; no "waited N days" arithmetic.  Once the subject writes back,
  the cue disappears.  Settlement appends the subject's reply through the
  elastic-play view; a reply with no text soft-lands with zero writes.
* A subject-side ``prepared_waiting`` surprise yields at most ONE spoiler-free
  timing cue.  The partner projection, the partner-lived context and every
  candidate descriptor never carry the surprise title/content.
* Mutually shared ``distinct_annotation_pairs`` render side by side with no
  comprehension/affinity score.  A ``kept_both_unreplaced`` divergence wakes
  only when the incoming words name its work.  ``private_two_phrasings``
  (including ``imagined_reply_cn``) and unshared annotations never enter the
  partner context, and the whole context stays within 1800 chars.

Run (native Python)::

    python -B -m unittest tests.test_v08_ch50_loop_and_context -v
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _p in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Bind the shared transaction guard before the daily-loop bridge is imported (the
# bridge installs a no-op ``fcntl`` shim on native Windows; the guard must pick its
# real ``msvcrt`` fallback first).
from components.life.scripts import state_tx_guard as _state_tx_guard  # noqa: E402,F401

from chiyo_bundle import lived_context_bridge as bridge  # noqa: E402
from components.life.scripts import v02_v03_daily_loop_bridge as loop_bridge  # noqa: E402
from components.life.scripts import v04_elastic_play_learning as ep_engine  # noqa: E402
from components.phone import v04_elastic_play_view as ep_view  # noqa: E402

T0 = "2026-11-16T20:00:00+08:00"
T1 = "2026-11-16T20:10:00+08:00"
T2 = "2026-11-16T20:20:00+08:00"

SHARED_DOMAIN = "v04.elastic.shared_notebook"
SURPRISE_DOMAIN = "v04.elastic.prepared_surprise"

SURPRISE_TITLE = "夹在书里的银杏书签"
SURPRISE_CONTENT = "等你不忙的时候再看"
PRIVATE_SECRET = "只有我知道的那个页脚小标记"
IMAGINED_REPLY = "TA 大概会回一句：好，你慢慢来。"


def _snap(root: Path) -> dict[str, tuple[int, int, str]]:
    if not root.exists():
        return {}
    out: dict[str, tuple[int, int, str]] = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root)).replace("\\", "/")
        stat = path.lstat()
        if path.is_file() and not path.is_symlink():
            out[rel] = (
                int(stat.st_size),
                int(stat.st_mtime_ns),
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        else:
            out[rel] = (0, int(stat.st_mtime_ns), "dir")
    return out


def _private_cfg(root: Path) -> dict[str, Any]:
    return {
        "day_root": str(root),
        "memory": True,
        "memory_tool_policy": "restricted",
        "phone": {"enabled": True, "private_artifacts": True},
    }


def _partner_cfg(root: Path) -> dict[str, Any]:
    """Restricted partner default: private artifacts stay off the disk path."""
    return {
        "day_root": str(root),
        "memory": True,
        "memory_tool_policy": "restricted",
        "phone": {"enabled": False},
    }


def _descriptors(collected: dict[str, Any], domain: str) -> list[dict[str, Any]]:
    return [
        dict(desc)
        for desc in (collected.get("descriptors") or {}).values()
        if isinstance(desc, dict) and desc.get("clue_domain") == domain
    ]


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="v08-ch50-loop-"))
        self.root = self.tmp / "day"
        self.root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- helpers -------------------------------------------------------------
    def _mutate(
        self,
        payload: dict[str, Any],
        *,
        actor_side: str = "subject",
        now_iso: str = T0,
    ):
        return ep_view.mutate_v04_elastic_play_view(
            self.root, payload, actor_side=actor_side, now_iso=now_iso
        )

    def _read(self, *, has_subject_capability: bool):
        return ep_view.read_v04_elastic_play_view(
            self.root, has_subject_capability=has_subject_capability
        )

    def _collect(self):
        return loop_bridge.collect_v02_v03_loop_candidates(self.root, now_iso=T1)

    def _append_notebook(self, text: str, *, actor_side: str, now_iso: str):
        code, body = self._mutate(
            {"action": "append_shared_notebook", "text": text},
            actor_side=actor_side,
            now_iso=now_iso,
        )
        self.assertEqual(code, 200, body)
        return body


class CalmDailyLoopTest(_Base):
    """A calm / empty day offers nothing and the read path never writes."""

    def test_01_empty_day_yields_zero_candidates_and_zero_writes(self) -> None:
        before = _snap(self.root)
        collected = self._collect()
        self.assertEqual(_snap(self.root), before, "collect must be strict zero-write")
        self.assertTrue(collected["zero_write"])
        self.assertEqual(collected["llm_calls"], 0)
        self.assertEqual(collected["candidates"], [], "a calm empty day has no candidates")
        self.assertEqual(_descriptors(collected, SHARED_DOMAIN), [])
        self.assertEqual(_descriptors(collected, SURPRISE_DOMAIN), [])

    def test_02_initialized_empty_elastic_state_stays_calm(self) -> None:
        phone = self.root / "phone"
        phone.mkdir(parents=True, exist_ok=True)
        (phone / "v04_elastic_play.json").write_text(
            json.dumps(
                ep_engine.default_elastic_play_state(now_iso=T0),
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        before = _snap(self.root)
        collected = self._collect()
        self.assertEqual(_snap(self.root), before, "collect must be strict zero-write")
        self.assertEqual(_descriptors(collected, SHARED_DOMAIN), [])
        self.assertEqual(_descriptors(collected, SURPRISE_DOMAIN), [])
        self.assertEqual(collected["candidates"], [])


class SharedNotebookCueTest(_Base):
    """Partner line -> one zero-debt cue; subject reply clears it."""

    def test_01_partner_line_emits_one_zero_debt_cue_without_day_counting(self) -> None:
        self._append_notebook(
            "今天在窗边看到一只灰雀，停在晾衣绳上。", actor_side="partner", now_iso=T0
        )
        before = _snap(self.root)
        collected = self._collect()
        self.assertEqual(_snap(self.root), before, "collect must be strict zero-write")

        rows = _descriptors(collected, SHARED_DOMAIN)
        self.assertEqual(len(rows), 1, "at most one shared-notebook cue")
        desc = rows[0]
        self.assertEqual(desc["action_family"], "elastic_shared_notebook")
        self.assertEqual(desc["source_ref"], "v04_elastic_shared_notebook:latest_partner_line")
        self.assertIs(desc["zero_debt"], True)
        self.assertIs(desc["optional"], True)
        self.assertEqual(desc["title_hint"], "看看共享小本子上对方留的新一句")
        # No "they waited N days" arithmetic anywhere in the cue.
        hint = desc["title_hint"]
        for token in ("天", "等", "欠", "多久", "days", "waited"):
            self.assertNotIn(token, hint)
        dump = json.dumps(desc, ensure_ascii=False)
        self.assertNotIn("等了", dump)
        self.assertNotIn("天数", dump)

    def test_02_subject_reply_makes_the_cue_disappear(self) -> None:
        self._append_notebook("窗边那只灰雀，你看见了吗？", actor_side="partner", now_iso=T0)
        self.assertEqual(len(_descriptors(self._collect(), SHARED_DOMAIN)), 1)

        self._append_notebook("我也看到了，停在晾衣绳上。", actor_side="subject", now_iso=T1)
        collected = self._collect()
        self.assertEqual(
            _descriptors(collected, SHARED_DOMAIN),
            [],
            "once the subject writes back there is nothing left to answer",
        )

    def test_03_settlement_appends_reply_and_missing_text_soft_lands(self) -> None:
        self._append_notebook("窗边那只灰雀，你看见了吗？", actor_side="partner", now_iso=T0)
        collected = self._collect()
        desc = _descriptors(collected, SHARED_DOMAIN)[0]
        event = loop_bridge.v02_v03_round_event_for_selected_candidate(
            collected, desc["source_ref"]
        )
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event["action_kind"], "ELASTIC_SHARED_NOTEBOOK")

        # No text -> gentle soft landing, zero writes (never a fabricated reply).
        before = _snap(self.root)
        soft = loop_bridge.settle_v02_v03_round_consequences(self.root, event, now_iso=T1)
        self.assertTrue(soft["soft_landed"])
        self.assertFalse(soft["settled"])
        self.assertEqual(soft["optimistic_writes"], 0)
        self.assertEqual(_snap(self.root), before, "a text-less reply must not write")

        reply_event = dict(event)
        reply_event["notebook_text"] = "我也看到了，停在晾衣绳上。"
        settled = loop_bridge.settle_v02_v03_round_consequences(
            self.root, reply_event, now_iso=T1
        )
        self.assertTrue(settled["settled"], settled)
        self.assertIn("v04_elastic_play", settled["slices_updated"])

        _code, body = self._read(has_subject_capability=True)
        entries = body["data"]["shared_notebook"]["entries"]
        self.assertEqual(entries[-1]["author"], "subject")
        self.assertEqual(entries[-1]["text"], "我也看到了，停在晾衣绳上。")
        self.assertEqual(_descriptors(self._collect(), SHARED_DOMAIN), [])

    def test_04_quiet_notebook_emits_nothing(self) -> None:
        self._append_notebook("我先记一句在这儿。", actor_side="subject", now_iso=T0)
        self.assertEqual(_descriptors(self._collect(), SHARED_DOMAIN), [])


class PreparedSurpriseCueTest(_Base):
    """Subject-side readiness -> spoiler-free timing cue; partner stays blind."""

    def _prepare_surprise(self) -> None:
        code, body = self._mutate(
            {
                "action": "prepare_or_offer_surprise",
                "stage": "prepare",
                "surprise_id": "surp-ginkgo",
                "kind": "pressed_leaf_card",
                "title": SURPRISE_TITLE,
                "content": SURPRISE_CONTENT,
            },
            actor_side="subject",
            now_iso=T0,
        )
        self.assertEqual(code, 200, body)

    def test_01_cue_is_spoiler_free_and_partner_blind(self) -> None:
        self._prepare_surprise()
        before = _snap(self.root)
        collected = self._collect()
        self.assertEqual(_snap(self.root), before, "collect must be strict zero-write")

        rows = _descriptors(collected, SURPRISE_DOMAIN)
        self.assertEqual(len(rows), 1, "at most one surprise-timing cue")
        desc = rows[0]
        self.assertEqual(desc["action_family"], "elastic_offer_surprise")
        self.assertEqual(desc["source_ref"], "v04_elastic_prepared_surprise:waiting")
        self.assertIs(desc["zero_debt"], True)
        self.assertIs(desc["optional"], True)
        self.assertEqual(desc["title_hint"], "心里那件小惊喜准备好拿出来了吗（不急）")
        descriptor_dump = json.dumps(desc, ensure_ascii=False)
        self.assertNotIn(SURPRISE_TITLE, descriptor_dump)
        self.assertNotIn(SURPRISE_CONTENT, descriptor_dump)
        self.assertNotIn("surp-ginkgo", descriptor_dump)

        # Partner projection hides the waiting surprise entirely.
        _code, partner_body = self._read(has_subject_capability=False)
        partner_data = partner_body["data"]
        self.assertEqual(partner_data["prepared_surprises"], [])
        partner_dump = json.dumps(partner_data, ensure_ascii=False)
        self.assertNotIn(SURPRISE_TITLE, partner_dump)
        self.assertNotIn(SURPRISE_CONTENT, partner_dump)

        # Partner lived context (include_private=False) says not one word.
        partner_line = bridge._format_v04_neighborhood_and_elastic(
            v04_nb=None,
            v04_ep=partner_data,
            include_private=False,
            incoming_text="",
        )
        self.assertNotIn(SURPRISE_TITLE, partner_line or "")
        self.assertNotIn(SURPRISE_CONTENT, partner_line or "")

        partner_context = bridge.render_lived_companion_context(
            self.root,
            _partner_cfg(self.root),
            "",
            ephemeral_hint="",
            now_iso=T1,
        )
        self.assertNotIn(SURPRISE_TITLE, partner_context)
        self.assertNotIn(SURPRISE_CONTENT, partner_context)
        self.assertLessEqual(len(partner_context), bridge.MAX_LIVED_CONTEXT_CHARS)

    def test_02_offering_settles_and_clears_the_waiting_state(self) -> None:
        self._prepare_surprise()
        collected = self._collect()
        desc = _descriptors(collected, SURPRISE_DOMAIN)[0]
        event = loop_bridge.v02_v03_round_event_for_selected_candidate(
            collected, desc["source_ref"]
        )
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event["action_kind"], "ELASTIC_OFFER_SURPRISE")

        settled = loop_bridge.settle_v02_v03_round_consequences(self.root, event, now_iso=T2)
        self.assertTrue(settled["settled"], settled)
        self.assertIn("v04_elastic_play", settled["slices_updated"])

        readiness = ep_engine.project_surprise_readiness_zero_write(self.root)
        self.assertIs(readiness["has_prepared_waiting"], False)
        self.assertIs(readiness["has_offered_unopened"], True)
        self.assertEqual(_descriptors(self._collect(), SURPRISE_DOMAIN), [])

    def test_03_offer_without_waiting_surprise_soft_lands(self) -> None:
        event = {
            "actor_side": "subject",
            "effect_state": "APPLIED",
            "action_kind": "ELASTIC_OFFER_SURPRISE",
            "action_family": "elastic_offer_surprise",
            "source_ref": "v04_elastic_prepared_surprise:waiting",
            "receipt_ref": "v04:test-offer",
            "operation_key": "v02v03_daily_test_offer",
        }
        result = loop_bridge.settle_v02_v03_round_consequences(self.root, event, now_iso=T2)
        self.assertTrue(result["soft_landed"])
        self.assertFalse(result["settled"])
        self.assertEqual(result["optimistic_writes"], 0)
        # The view's transaction guard may create a lock file, but no business state
        # and no consequence ledger row may appear for a refused offer.
        self.assertFalse((self.root / "phone" / "v04_elastic_play.json").exists())
        self.assertFalse(
            (self.root / "phone" / "v02_v03_consequence_ledger.json").exists()
        )


class DistinctNotesContextTest(_Base):
    """Side-by-side attention without scores; strict private isolation."""

    def _share(
        self,
        *,
        actor_side: str,
        detail: str,
        page: int = 42,
        shared_with_partner: bool = True,
    ) -> None:
        code, body = self._mutate(
            {
                "action": "share_distinct_annotation",
                "material_title": "海堤漫步录",
                "page": page,
                "noticed_detail_cn": detail,
                "shared_with_partner": shared_with_partner,
            },
            actor_side=actor_side,
            now_iso=T0,
        )
        self.assertEqual(code, 200, body)

    def test_01_pairs_render_without_scores_and_private_rows_stay_out(self) -> None:
        self._share(actor_side="subject", detail="潮水退去后留在堤缝里的一层白盐")
        self._share(actor_side="partner", detail="结尾那句没说完的话")
        self._share(
            actor_side="subject", detail=PRIVATE_SECRET, shared_with_partner=False
        )
        code, body = self._mutate(
            {
                "action": "save_two_phrasings",
                "phrasing_a": "我今天想先把作品写完。",
                "phrasing_b": "今天让我先把手上的东西收个尾，好吗？",
                "chosen": "undecided",
                "imagined_reply_cn": IMAGINED_REPLY,
            },
            actor_side="subject",
            now_iso=T1,
        )
        self.assertEqual(code, 200, body)

        _code, partner_body = self._read(has_subject_capability=False)
        partner_data = partner_body["data"]
        self.assertEqual(len(partner_data["distinct_annotation_pairs"]), 1)
        self.assertEqual(partner_data["private_two_phrasings"], [])
        partner_dump = json.dumps(partner_data, ensure_ascii=False)
        self.assertNotIn(PRIVATE_SECRET, partner_dump)
        self.assertNotIn("imagined_reply_cn", partner_dump)
        self.assertNotIn(IMAGINED_REPLY, partner_dump)

        line = bridge._format_v04_neighborhood_and_elastic(
            v04_nb=None,
            v04_ep=partner_data,
            include_private=False,
            incoming_text="",
        )
        self.assertIsNotNone(line)
        assert line is not None
        self.assertIn("同读《海堤漫步录》第42页", line)
        self.assertIn("TA留意「结尾那句没说完的话」", line)
        self.assertIn("你留意「潮水退去后留在堤缝里的一层白盐」", line)
        # No comprehension / affinity scoring, and no private leak.
        for banned in ("理解", "契合", "评分", "契合度", "comprehension", "score"):
            self.assertNotIn(banned, line)
        self.assertNotIn(PRIVATE_SECRET, line)
        self.assertNotIn(IMAGINED_REPLY, line)
        self.assertLessEqual(len(line), bridge.MAX_LIVED_CONTEXT_CHARS)

        partner_context = bridge.render_lived_companion_context(
            self.root,
            _partner_cfg(self.root),
            "",
            ephemeral_hint="",
            now_iso=T1,
        )
        self.assertNotIn(PRIVATE_SECRET, partner_context)
        self.assertNotIn(IMAGINED_REPLY, partner_context)
        self.assertLessEqual(len(partner_context), bridge.MAX_LIVED_CONTEXT_CHARS)

        private_context = bridge.render_lived_companion_context(
            self.root,
            _private_cfg(self.root),
            "",
            ephemeral_hint="",
            now_iso=T1,
        )
        self.assertLessEqual(len(private_context), bridge.MAX_LIVED_CONTEXT_CHARS)

    def test_02_same_spot_resonance_is_noted_without_scoring(self) -> None:
        self._share(actor_side="subject", detail="两个人都停在结尾那句留白上", page=7)
        self._share(actor_side="partner", detail="两个人都停在结尾那句留白上", page=7)
        _code, partner_body = self._read(has_subject_capability=False)
        line = bridge._format_v04_neighborhood_and_elastic(
            v04_nb=None,
            v04_ep=partner_body["data"],
            include_private=False,
            incoming_text="",
        )
        self.assertIsNotNone(line)
        assert line is not None
        self.assertIn("(撞见同一句)", line)
        self.assertNotIn("理解", line)
        self.assertNotIn("契合", line)

    def test_03_kept_divergence_wakes_only_when_its_work_is_named(self) -> None:
        code, body = self._mutate(
            {
                "action": "propose_or_respond_experiment",
                "exp_op": "propose",
                "experiment_id": "exp-seawall",
                "title": "新版结尾",
                "target_work_ref": "work:seawall-final",
            },
            actor_side="subject",
            now_iso=T0,
        )
        self.assertEqual(code, 200, body)
        code, body = self._mutate(
            {
                "action": "propose_or_respond_experiment",
                "exp_op": "pause_keep_both",
                "experiment_id": "exp-seawall",
                "divergence_memo_cn": "原版更含蓄，改版更清楚，先不替换",
            },
            actor_side="subject",
            now_iso=T1,
        )
        self.assertEqual(code, 200, body)

        _code, partner_body = self._read(has_subject_capability=False)
        v04_ep = partner_body["data"]

        silent = bridge._format_v04_neighborhood_and_elastic(
            v04_nb=None, v04_ep=v04_ep, include_private=False, incoming_text="今天吃什么"
        )
        self.assertTrue(silent is None or "保留分歧" not in silent)

        by_ref = bridge._format_v04_neighborhood_and_elastic(
            v04_nb=None,
            v04_ep=v04_ep,
            include_private=False,
            incoming_text="我们把 work:seawall-final 再翻出来看看",
        )
        self.assertIsNotNone(by_ref)
        assert by_ref is not None
        self.assertIn("保留分歧[新版结尾]", by_ref)
        self.assertIn("原版更含蓄，改版更清楚，先不替换", by_ref)

        by_title = bridge._format_v04_neighborhood_and_elastic(
            v04_nb=None,
            v04_ep=v04_ep,
            include_private=False,
            incoming_text="新版结尾那件事，今天想再聊聊",
        )
        self.assertIsNotNone(by_title)
        assert by_title is not None
        self.assertIn("保留分歧[新版结尾]", by_title)
        self.assertLessEqual(len(by_title), bridge.MAX_LIVED_CONTEXT_CHARS)


if __name__ == "__main__":
    unittest.main()

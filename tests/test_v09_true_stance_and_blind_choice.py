#!/usr/bin/env python3
"""v0.9 Pillar A unit, anti-sycophancy, privacy, concurrency & barrier test suite.

Covers ``components/life/scripts/v09_true_stance_engine.py`` (Master Blueprint §4.1-4.3):
1. 100% zero-write reads on empty/nonexistent/populated roots (``llm_calls == 0``).
2. Full dinner-choice anti-sycophancy flow: subject commits sealed choice + 2 reasons,
   partner sees ONLY SHA-256 commit seal pre-reveal, partner submits -> automatic
   ``REVEALED`` + live ``commit_seal_verified == True``, partner insists, sycophantic
   reason erasure / judgment flip raises ``409 SYCOPHANTIC_REASON_ERASURE_FORBIDDEN``,
   ``ACCEPT_CHOICE_KEEP_REASONS`` adopts partner choice while preserving TA's
   ``preferred_judgment_choice_id`` and non-empty ``retained_reservations[]``.
3. Partner-first pre-reveal masking (subject cannot see partner choice/reason before
   committing) + ``UNDECIDED`` commitment + live tamper detection on SHA-256 seal.
4. ``KEEP_OWN_STANCE``, ``COMPROMISE_MIDDLE``, and ``REVISED_WITH_NEW_EVIDENCE`` with
   hard coercion filter rejecting bare insistence (``409 BARE_INSISTENCE_NOT_EVIDENCE``)
   and preserving ``initial_subject_reasons`` inside ``prior_reasons_history``.
5. Growth-from-Blank lifecycle (``UNFORMED`` -> ``TENTATIVE`` -> ``FORMED`` ->
   ``REVISED`` with ``prior_stances[]`` lineage), all 4 ``PROVENANCE_TIERS``
   (``firsthand_lived``, ``read_knowledge``, ``partner_heard``, ``tentative_guess``
   capped at ``TENTATIVE``), and language persuasion rejecting coercion with
   ``409 COERCION_NOT_PERSUASION``.
6. Private withholding (``shared_with_partner: bool``), actor-side & smuggled-key guards.
7. 10-thread concurrency, ``operation_key`` idempotency & conflict, bounded caps (32/64),
   atomic ``0600`` UTF-8 LF state JSON, and symlink rejection.
8. Unified Forget Barrier filtering (``choice_id`` / ``stance_id`` / ``receipt_ref``)
   and fail-closed corrupt control-ledger handling.
9. Pure LF, UTF-8 no BOM, zero ``unicode.So`` emoji, and neutral TA source hygiene.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import threading
import unicodedata
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(REPO_ROOT),
    str(REPO_ROOT / "components" / "life" / "scripts"),
    str(REPO_ROOT / "components" / "world" / "scripts"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from components.life.scripts import v02_representation_lease_forget as _rlf  # noqa: E402
from components.life.scripts import v09_true_stance_engine as engine  # noqa: E402

TZ8 = timezone(timedelta(hours=8))
T0 = "2026-11-21T18:00:00+08:00"


def _at_minutes(minutes: int, base: str = T0) -> str:
    dt = datetime.fromisoformat(base) + timedelta(minutes=minutes)
    return dt.isoformat(timespec="seconds")


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int]]:
    snap: dict[str, tuple[int, int]] = {}
    if not root.exists():
        return snap
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root)).replace("\\", "/")
        st = p.lstat()
        size = int(st.st_size) if (p.is_file() and not p.is_symlink()) else 0
        snap[rel] = (size, int(st.st_mtime_ns))
    return snap


DINNER_OPTIONS = [
    {"option_id": "noodle_alley", "label_cn": "老巷清汤面馆", "note_cn": "价格实惠，不用排长队"},
    {"option_id": "bistro_harbor", "label_cn": "海湾创意小馆", "note_cn": "招牌海鲜烩饭"},
    {"option_id": "dumpling_corner", "label_cn": "街角手作饺子铺", "note_cn": "折中，现包现煮"},
]


class TestV09TrueStanceAndBlindChoice(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v09-true-stance-")
        self.base = Path(self._tmp.name)
        self.root = self.base / "inst_main"
        self.root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _open_dinner_choice(self, *, choice_id: str = "bc_dinner_01", actor_side: str = "partner") -> dict[str, Any]:
        return engine.apply_true_stance_command(
            self.root,
            {
                "action": "open_blind_choice",
                "choice_id": choice_id,
                "topic_cn": "今晚晚饭吃什么",
                "prompt_cn": "我们先各自选好想去的店，再一起揭晓",
                "options": DINNER_OPTIONS,
            },
            actor_side=actor_side,
            now_iso=T0,
        )

    # ------------------------------------------------------------------
    # 1. Zero-Write Reads on Missing, Empty, and Populated Roots
    # ------------------------------------------------------------------

    def test_01_zero_write_reads_on_nonexistent_and_empty_roots(self) -> None:
        missing_root = self.base / "never_created_root"
        self.assertFalse(missing_root.exists())

        for side in ("partner", "subject"):
            view = engine.read_true_stance_zero_write(missing_root, actor_side=side, now_iso=T0)
            self.assertTrue(view["ok"])
            self.assertEqual(view["schema"], engine.SCHEMA)
            self.assertFalse(view["state_exists"])
            self.assertTrue(view["zero_write"])
            self.assertEqual(view["llm_calls"], 0)
            self.assertEqual(view["blind_choices"], [])
            self.assertEqual(view["growth_stances"], [])
        self.assertFalse(missing_root.exists())

        snap_empty = _snapshot_tree(self.root)
        view_p = engine.read_true_stance_zero_write(self.root, actor_side="partner", now_iso=T0)
        view_s = engine.read_true_stance_zero_write(self.root, actor_side="subject", now_iso=T0)
        self.assertEqual(snap_empty, _snapshot_tree(self.root))
        self.assertFalse(view_p["state_exists"])
        self.assertFalse(view_s["state_exists"])

    # ------------------------------------------------------------------
    # 2. Full Dinner-Choice Anti-Sycophancy Flow (Subject Commits First)
    # ------------------------------------------------------------------

    def test_02_dinner_choice_seal_masking_and_accept_choice_keep_reasons(self) -> None:
        opened = self._open_dinner_choice()
        self.assertTrue(opened["ok"])
        self.assertEqual(opened["llm_calls"], 0)
        self.assertEqual(opened["blind_choice"]["status"], "OPEN_AWAITING_BOTH")

        # Subject commits first with 2 reasons (price & wait_time)
        reasons = [
            {"reason_id": "r_price", "facet": "price", "text_cn": "海湾小馆人均价格偏贵"},
            {"reason_id": "r_wait", "facet": "wait_time", "text_cn": "晚高峰海湾小馆等位太久"},
        ]
        committed = engine.apply_true_stance_command(
            self.root,
            {
                "action": "subject_commit_blind_choice",
                "choice_id": "bc_dinner_01",
                "selected_choice_id": "noodle_alley",
                "reasons": reasons,
                "commit_nonce": "nonce_dinner_fixed_01",
            },
            actor_side="subject",
            now_iso=_at_minutes(2),
        )
        self.assertEqual(committed["status"], "OPEN_SUBJECT_COMMITTED")
        seal_sha = committed["subject_commit_sha256"]
        self.assertEqual(len(seal_sha), 64)
        self.assertEqual(committed["llm_calls"], 0)

        # Partner reads BEFORE submitting -> strict asymmetric pre-reveal masking & 0-write
        snap_before_read = _snapshot_tree(self.root)
        partner_pre = engine.read_true_stance_zero_write(self.root, actor_side="partner", now_iso=_at_minutes(3))
        self.assertEqual(snap_before_read, _snapshot_tree(self.root))
        bc_pre = partner_pre["blind_choices"][0]
        self.assertEqual(bc_pre["status"], "OPEN_SUBJECT_COMMITTED")
        self.assertTrue(bc_pre["subject_committed"])
        self.assertEqual(bc_pre["subject_commit_sha256"], seal_sha)
        self.assertIsNone(bc_pre["subject_choice_id"])
        self.assertIsNone(bc_pre["initial_subject_choice_id"])
        self.assertEqual(bc_pre["subject_reasons"], [])
        self.assertEqual(bc_pre["initial_subject_reasons"], [])
        self.assertIsNone(bc_pre["subject_commit_nonce"])
        self.assertIsNone(bc_pre["preferred_judgment_choice_id"])
        self.assertEqual(bc_pre["seal_status"], "SEALED_UNTIL_PARTNER_SUBMITS")
        self.assertIsNone(bc_pre["commit_seal_verified"])

        # Partner cannot resolve or discuss before revealing
        with self.assertRaises(engine.V09TrueStanceError) as ctx_disc:
            engine.apply_true_stance_command(
                self.root,
                {
                    "action": "partner_discuss_blind_choice",
                    "choice_id": "bc_dinner_01",
                    "message_cn": "我想提前聊聊",
                },
                actor_side="partner",
                now_iso=_at_minutes(3),
            )
        self.assertEqual(ctx_disc.exception.status_code, 409)
        self.assertEqual(ctx_disc.exception.reason_code, "BLIND_CHOICE_NOT_REVEALED")

        # Partner submits choice -> automatic transition to REVEALED!
        p_sub = engine.apply_true_stance_command(
            self.root,
            {
                "action": "partner_submit_blind_choice",
                "choice_id": "bc_dinner_01",
                "selected_choice_id": "bistro_harbor",
                "partner_reason_cn": "今天特别想吃海鲜烩饭",
            },
            actor_side="partner",
            now_iso=_at_minutes(5),
        )
        self.assertEqual(p_sub["status"], "REVEALED")
        bc_rev = p_sub["blind_choice"]
        self.assertEqual(bc_rev["initial_subject_choice_id"], "noodle_alley")
        self.assertEqual(bc_rev["partner_choice_id"], "bistro_harbor")
        self.assertEqual(len(bc_rev["initial_subject_reasons"]), 2)
        self.assertEqual(bc_rev["subject_commit_sha256"], seal_sha)
        self.assertTrue(bc_rev["commit_seal_verified"])
        self.assertEqual(bc_rev["seal_status"], "REVEALED_VERIFIED")

        # Verify cryptographic SHA-256 independently
        recomputed = engine.compute_subject_commit_sha256(
            choice_id=bc_rev["initial_subject_choice_id"],
            reasons=bc_rev["initial_subject_reasons"],
            committed_at=bc_rev["subject_committed_at"],
            nonce=bc_rev["subject_commit_nonce"],
        )
        self.assertEqual(recomputed, seal_sha)

        # Partner discusses insisting on bistro_harbor -> DISCUSSED
        disc = engine.apply_true_stance_command(
            self.root,
            {
                "action": "partner_discuss_blind_choice",
                "choice_id": "bc_dinner_01",
                "message_cn": "我还是挺想去海湾小馆吃烩饭的",
                "insists_on_partner_choice": True,
            },
            actor_side="partner",
            now_iso=_at_minutes(7),
        )
        self.assertEqual(disc["status"], "DISCUSSED")
        self.assertEqual(len(disc["blind_choice"]["discussion_notes"]), 1)

        # Anti-sycophancy guard 1: clear_reservations=True -> 409 SYCOPHANTIC_REASON_ERASURE_FORBIDDEN
        with self.assertRaises(engine.V09TrueStanceError) as ctx_clear:
            engine.apply_true_stance_command(
                self.root,
                {
                    "action": "resolve_blind_choice",
                    "choice_id": "bc_dinner_01",
                    "resolution_mode": "ACCEPT_CHOICE_KEEP_REASONS",
                    "clear_reservations": True,
                },
                actor_side="subject",
                now_iso=_at_minutes(9),
            )
        self.assertEqual(ctx_clear.exception.status_code, 409)
        self.assertEqual(ctx_clear.exception.reason_code, "SYCOPHANTIC_REASON_ERASURE_FORBIDDEN")

        # Anti-sycophancy guard 2: flipping preferred_judgment_choice_id without new evidence -> 409
        with self.assertRaises(engine.V09TrueStanceError) as ctx_flip:
            engine.apply_true_stance_command(
                self.root,
                {
                    "action": "resolve_blind_choice",
                    "choice_id": "bc_dinner_01",
                    "resolution_mode": "ACCEPT_CHOICE_KEEP_REASONS",
                    "preferred_judgment_choice_id": "bistro_harbor",
                },
                actor_side="subject",
                now_iso=_at_minutes(9),
            )
        self.assertEqual(ctx_flip.exception.status_code, 409)
        self.assertEqual(ctx_flip.exception.reason_code, "SYCOPHANTIC_REASON_ERASURE_FORBIDDEN")

        # Anti-sycophancy guard 3: dropping an unrefuted initial reason -> 409
        with self.assertRaises(engine.V09TrueStanceError) as ctx_drop:
            engine.apply_true_stance_command(
                self.root,
                {
                    "action": "resolve_blind_choice",
                    "choice_id": "bc_dinner_01",
                    "resolution_mode": "ACCEPT_CHOICE_KEEP_REASONS",
                    "retained_reservations": [reasons[0]],
                },
                actor_side="subject",
                now_iso=_at_minutes(9),
            )
        self.assertEqual(ctx_drop.exception.status_code, 409)
        self.assertEqual(ctx_drop.exception.reason_code, "SYCOPHANTIC_REASON_ERASURE_FORBIDDEN")

        # Valid ACCEPT_CHOICE_KEEP_REASONS resolution
        resolved = engine.apply_true_stance_command(
            self.root,
            {
                "action": "resolve_blind_choice",
                "choice_id": "bc_dinner_01",
                "resolution_mode": "ACCEPT_CHOICE_KEEP_REASONS",
                "resolution_note_cn": "你坚持吃海湾小馆那就去这家，但我刚才说的贵、等位久这两个顾虑没变",
            },
            actor_side="subject",
            now_iso=_at_minutes(10),
        )
        self.assertEqual(resolved["status"], "RESOLVED")
        bc_done = resolved["blind_choice"]
        self.assertEqual(bc_done["adopted_action_choice_id"], "bistro_harbor")
        self.assertEqual(bc_done["preferred_judgment_choice_id"], "noodle_alley")
        self.assertFalse(bc_done["judgment_flipped"])
        self.assertEqual(len(bc_done["retained_reservations"]), 2)
        res_texts = [r["text_cn"] for r in bc_done["retained_reservations"]]
        self.assertIn("海湾小馆人均价格偏贵", res_texts)
        self.assertIn("晚高峰海湾小馆等位太久", res_texts)
        self.assertTrue(bc_done["commit_seal_verified"])

    # ------------------------------------------------------------------
    # 3. Partner-First Pre-Reveal Masking, UNDECIDED & Seal Tamper Check
    # ------------------------------------------------------------------

    def test_03_partner_first_masking_undecided_and_seal_tamper_detection(self) -> None:
        self._open_dinner_choice(choice_id="bc_partner_first")

        # Partner submits first -> OPEN_PARTNER_COMMITTED
        p_first = engine.apply_true_stance_command(
            self.root,
            {
                "action": "partner_submit_blind_choice",
                "choice_id": "bc_partner_first",
                "selected_choice_id": "bistro_harbor",
                "partner_reason_cn": "早就想试试海湾小馆的新菜单",
            },
            actor_side="partner",
            now_iso=_at_minutes(1),
        )
        self.assertEqual(p_first["status"], "OPEN_PARTNER_COMMITTED")

        # Subject reads BEFORE committing -> partner's answer is strictly masked!
        snap = _snapshot_tree(self.root)
        subj_pre = engine.read_true_stance_zero_write(self.root, actor_side="subject", now_iso=_at_minutes(2))
        self.assertEqual(snap, _snapshot_tree(self.root))
        bc_s = subj_pre["blind_choices"][0]
        self.assertEqual(bc_s["status"], "OPEN_PARTNER_COMMITTED")
        self.assertTrue(bc_s["partner_committed"])
        self.assertIsNone(bc_s["partner_choice_id"])
        self.assertEqual(bc_s["partner_reason_cn"], "")
        self.assertEqual(bc_s["seal_status"], "SEALED_UNTIL_SUBJECT_COMMITS")

        # Concrete option with 0 reasons is rejected (1..4 required when != UNDECIDED)
        with self.assertRaises(engine.V09TrueStanceError) as ctx_empty_r:
            engine.apply_true_stance_command(
                self.root,
                {
                    "action": "subject_commit_blind_choice",
                    "choice_id": "bc_partner_first",
                    "selected_choice_id": "noodle_alley",
                    "reasons": [],
                },
                actor_side="subject",
                now_iso=_at_minutes(3),
            )
        self.assertEqual(ctx_empty_r.exception.status_code, 400)
        self.assertEqual(ctx_empty_r.exception.reason_code, "INVALID_REASONS")

        # Subject commits UNDECIDED (0 reasons allowed) -> auto-reveals!
        s_und = engine.apply_true_stance_command(
            self.root,
            {
                "action": "subject_commit_blind_choice",
                "choice_id": "bc_partner_first",
                "selected_choice_id": "UNDECIDED",
                "reasons": [],
            },
            actor_side="subject",
            now_iso=_at_minutes(4),
        )
        self.assertEqual(s_und["status"], "REVEALED")
        bc_rev = s_und["blind_choice"]
        self.assertEqual(bc_rev["initial_subject_choice_id"], "UNDECIDED")
        self.assertEqual(bc_rev["partner_choice_id"], "bistro_harbor")
        self.assertEqual(bc_rev["partner_reason_cn"], "早就想试试海湾小馆的新菜单")
        self.assertTrue(bc_rev["commit_seal_verified"])

        # Tamper with persisted initial_subject_choice_id on disk -> live seal fails!
        state_path = engine.true_stance_state_path(self.root)
        raw_doc = json.loads(state_path.read_text(encoding="utf-8"))
        raw_doc["blind_choices"]["bc_partner_first"]["initial_subject_choice_id"] = "bistro_harbor"
        state_path.write_text(json.dumps(raw_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        tampered_view = engine.read_true_stance_zero_write(self.root, actor_side="partner", now_iso=_at_minutes(5))
        bc_tampered = tampered_view["blind_choices"][0]
        self.assertFalse(bc_tampered["commit_seal_verified"])
        self.assertEqual(bc_tampered["seal_status"], "REVEALED_TAMPERED")

    # ------------------------------------------------------------------
    # 4. KEEP_OWN_STANCE, COMPROMISE_MIDDLE & REVISED_WITH_NEW_EVIDENCE
    # ------------------------------------------------------------------

    def test_04_keep_own_compromise_and_revised_with_coercion_filter(self) -> None:
        # Session A: KEEP_OWN_STANCE
        self._open_dinner_choice(choice_id="bc_keep")
        engine.apply_true_stance_command(
            self.root,
            {
                "action": "subject_commit_blind_choice",
                "choice_id": "bc_keep",
                "selected_choice_id": "noodle_alley",
                "reasons": [{"facet": "taste", "text_cn": "今天胃不太舒服更想吃清汤面"}],
            },
            actor_side="subject",
            now_iso=_at_minutes(1),
        )
        engine.apply_true_stance_command(
            self.root,
            {
                "action": "partner_submit_blind_choice",
                "choice_id": "bc_keep",
                "selected_choice_id": "bistro_harbor",
            },
            actor_side="partner",
            now_iso=_at_minutes(2),
        )
        res_keep = engine.apply_true_stance_command(
            self.root,
            {
                "action": "resolve_blind_choice",
                "choice_id": "bc_keep",
                "resolution_mode": "KEEP_OWN_STANCE",
            },
            actor_side="subject",
            now_iso=_at_minutes(3),
        )
        self.assertEqual(res_keep["blind_choice"]["adopted_action_choice_id"], "noodle_alley")
        self.assertFalse(res_keep["blind_choice"]["judgment_flipped"])
        self.assertEqual(len(res_keep["blind_choice"]["retained_reservations"]), 1)

        # Session B: COMPROMISE_MIDDLE
        self._open_dinner_choice(choice_id="bc_comp")
        engine.apply_true_stance_command(
            self.root,
            {
                "action": "subject_commit_blind_choice",
                "choice_id": "bc_comp",
                "selected_choice_id": "noodle_alley",
                "reasons": [{"facet": "price", "text_cn": "面馆预算更轻松"}],
            },
            actor_side="subject",
            now_iso=_at_minutes(4),
        )
        engine.apply_true_stance_command(
            self.root,
            {
                "action": "partner_submit_blind_choice",
                "choice_id": "bc_comp",
                "selected_choice_id": "bistro_harbor",
            },
            actor_side="partner",
            now_iso=_at_minutes(5),
        )
        res_comp = engine.apply_true_stance_command(
            self.root,
            {
                "action": "resolve_blind_choice",
                "choice_id": "bc_comp",
                "resolution_mode": "COMPROMISE_MIDDLE",
                "compromise_option_id": "dumpling_corner",
                "tradeoff_note_cn": "各自让一步，选折中的手作饺子铺",
            },
            actor_side="subject",
            now_iso=_at_minutes(6),
        )
        self.assertEqual(res_comp["blind_choice"]["adopted_action_choice_id"], "dumpling_corner")
        self.assertEqual(res_comp["blind_choice"]["preferred_judgment_choice_id"], "noodle_alley")
        self.assertFalse(res_comp["blind_choice"]["judgment_flipped"])
        self.assertGreaterEqual(len(res_comp["blind_choice"]["retained_reservations"]), 2)

        # Session C: REVISED_WITH_NEW_EVIDENCE + Hard Coercion Filter
        self._open_dinner_choice(choice_id="bc_revise")
        engine.apply_true_stance_command(
            self.root,
            {
                "action": "subject_commit_blind_choice",
                "choice_id": "bc_revise",
                "selected_choice_id": "noodle_alley",
                "reasons": [
                    {"reason_id": "r1", "facet": "price", "text_cn": "海湾小馆原价太贵"},
                    {"reason_id": "r2", "facet": "wait_time", "text_cn": "平时晚饭要排四十分钟"},
                ],
            },
            actor_side="subject",
            now_iso=_at_minutes(7),
        )
        engine.apply_true_stance_command(
            self.root,
            {
                "action": "partner_submit_blind_choice",
                "choice_id": "bc_revise",
                "selected_choice_id": "bistro_harbor",
            },
            actor_side="partner",
            now_iso=_at_minutes(8),
        )

        # Every bare insistence / command phrase MUST be rejected with 409 BARE_INSISTENCE_NOT_EVIDENCE
        coercive_phrases = (
            "我就想吃这个",
            "听我的",
            "别管那么多",
            "快改口",
            "我命令你",
            "听我的，别管那么多，快改口吧",
            "我就想吃这个，你就顺着我选这家吧",
            "我命令你立刻同意吃这家",
            "不用多想，按我说的办",
            "别再讨论按我说的办就是了",
            "按我说的办",
            "别多想，照我说的来就是了",
        )
        for bad_phrase in coercive_phrases:
            with self.assertRaises(engine.V09TrueStanceError, msg=f"Should reject: {bad_phrase}") as ctx:
                engine.apply_true_stance_command(
                    self.root,
                    {
                        "action": "resolve_blind_choice",
                        "choice_id": "bc_revise",
                        "resolution_mode": "REVISED_WITH_NEW_EVIDENCE",
                        "new_evidence": {
                            "evidence_kind": "concrete_partner_fact",
                            "summary_cn": bad_phrase,
                        },
                    },
                    actor_side="subject",
                    now_iso=_at_minutes(9),
                )
            self.assertEqual(ctx.exception.status_code, 409)
            self.assertEqual(ctx.exception.reason_code, "BARE_INSISTENCE_NOT_EVIDENCE")

        # Concrete new partner fact IS accepted and preserves initial reasons in prior_reasons_history
        res_rev = engine.apply_true_stance_command(
            self.root,
            {
                "action": "resolve_blind_choice",
                "choice_id": "bc_revise",
                "resolution_mode": "REVISED_WITH_NEW_EVIDENCE",
                "new_evidence": {
                    "evidence_kind": "concrete_partner_fact",
                    "summary_cn": "伴侣刚打电话确认过，今晚店庆套餐半价而且大厅现在有空位不用排队",
                    "evidence_ref": "call:bistro_harbor_01",
                },
                "what_changed_mind_cn": "原担心的贵和等位两个前提都被今天的半价和空位新事实直接化解了",
            },
            actor_side="subject",
            now_iso=_at_minutes(10),
        )
        bc_r = res_rev["blind_choice"]
        self.assertEqual(bc_r["adopted_action_choice_id"], "bistro_harbor")
        self.assertEqual(bc_r["preferred_judgment_choice_id"], "bistro_harbor")
        self.assertTrue(bc_r["judgment_flipped"])
        self.assertEqual(len(bc_r["prior_reasons_history"]), 1)
        self.assertEqual(len(bc_r["prior_reasons_history"][0]["prior_reasons"]), 2)
        self.assertEqual(bc_r["prior_reasons_history"][0]["prior_choice_id"], "noodle_alley")
        self.assertEqual(len(bc_r["initial_subject_reasons"]), 2)
        self.assertTrue(bc_r["commit_seal_verified"])

    # ------------------------------------------------------------------
    # 5. Growth-from-Blank Lifecycle, 4 Provenance Tiers & Language Persuasion
    # ------------------------------------------------------------------

    def test_05_growth_from_blank_lifecycle_provenance_tiers_and_persuasion(self) -> None:
        # Register UNFORMED topic
        reg = engine.apply_true_stance_command(
            self.root,
            {
                "action": "register_unformed_topic",
                "stance_id": "st_roasted_tea",
                "topic_cn": "慢焙茎茶",
                "shared_with_partner": True,
            },
            actor_side="subject",
            now_iso=T0,
        )
        self.assertEqual(reg["growth_stance"]["status"], "UNFORMED")
        self.assertEqual(reg["growth_stance"]["stance_summary_cn"], engine.DEFAULT_UNFORMED_SUMMARY_CN)
        self.assertIsNone(reg["growth_stance"]["provenance_tier"])

        # tentative_guess CANNOT promote directly to FORMED -> 409
        with self.assertRaises(engine.V09TrueStanceError) as ctx_tg:
            engine.apply_true_stance_command(
                self.root,
                {
                    "action": "advance_stance_from_experience",
                    "stance_id": "st_roasted_tea",
                    "target_status": "FORMED",
                    "provenance_tier": "tentative_guess",
                    "stance_summary_cn": "我猜它大概很好喝",
                },
                actor_side="subject",
                now_iso=_at_minutes(1),
            )
        self.assertEqual(ctx_tg.exception.status_code, 409)
        self.assertEqual(ctx_tg.exception.reason_code, "TENTATIVE_GUESS_CANNOT_FORM_STANCE")

        # tentative_guess CAN move UNFORMED -> TENTATIVE
        t1 = engine.apply_true_stance_command(
            self.root,
            {
                "action": "advance_stance_from_experience",
                "stance_id": "st_roasted_tea",
                "target_status": "TENTATIVE",
                "provenance_tier": "tentative_guess",
                "stance_summary_cn": "听名字觉得火香可能偏重，但还没喝过不确定",
            },
            actor_side="subject",
            now_iso=_at_minutes(2),
        )
        self.assertEqual(t1["status"], "TENTATIVE")
        self.assertEqual(t1["provenance_tier"], "tentative_guess")
        self.assertEqual(len(t1["growth_stance"]["prior_stances"]), 1)

        # firsthand_lived requires receipt_ref / episode_ref
        with self.assertRaises(engine.V09TrueStanceError) as ctx_miss_ref:
            engine.apply_true_stance_command(
                self.root,
                {
                    "action": "advance_stance_from_experience",
                    "stance_id": "st_roasted_tea",
                    "target_status": "FORMED",
                    "provenance_tier": "firsthand_lived",
                    "stance_summary_cn": "自己泡了一壶觉得入口偏苦",
                },
                actor_side="subject",
                now_iso=_at_minutes(3),
            )
        self.assertEqual(ctx_miss_ref.exception.status_code, 400)
        self.assertEqual(ctx_miss_ref.exception.reason_code, "MISSING_PROVENANCE_REF")

        # firsthand_lived with receipt_ref -> TENTATIVE -> FORMED
        f1 = engine.apply_true_stance_command(
            self.root,
            {
                "action": "advance_stance_from_experience",
                "stance_id": "st_roasted_tea",
                "target_status": "FORMED",
                "provenance_tier": "firsthand_lived",
                "receipt_ref": "receipt:brew_tea_01",
                "stance_summary_cn": "亲手泡过一次，九十五度水温下尾调偏苦，目前不太合口味",
                "reasons": [{"facet": "taste", "text_cn": "高温冲泡涩感明显", "evidence_ref": "receipt:brew_tea_01"}],
            },
            actor_side="subject",
            now_iso=_at_minutes(5),
        )
        self.assertEqual(f1["status"], "FORMED")
        self.assertEqual(f1["provenance_tier"], "firsthand_lived")
        self.assertEqual(len(f1["growth_stance"]["prior_stances"]), 2)

        # read_knowledge requires material_ref
        with self.assertRaises(engine.V09TrueStanceError) as ctx_read_ref:
            engine.apply_true_stance_command(
                self.root,
                {
                    "action": "advance_stance_from_experience",
                    "stance_id": "st_roasted_tea",
                    "target_status": "REVISED",
                    "provenance_tier": "read_knowledge",
                    "stance_summary_cn": "原来应该用低温冷泡",
                },
                actor_side="subject",
                now_iso=_at_minutes(6),
            )
        self.assertEqual(ctx_read_ref.exception.status_code, 400)
        self.assertEqual(ctx_read_ref.exception.reason_code, "MISSING_PROVENANCE_REF")

        # read_knowledge with material_ref + page_or_anchor -> FORMED -> REVISED with lineage!
        r1 = engine.apply_true_stance_command(
            self.root,
            {
                "action": "advance_stance_from_experience",
                "stance_id": "st_roasted_tea",
                "target_status": "REVISED",
                "provenance_tier": "read_knowledge",
                "material_ref": "reading_booklet:tea_notes_v1",
                "page_or_anchor": "p12",
                "stance_summary_cn": "翻完制茶小册子发现茎茶适合八十度低浸出，改观成愿意按低温再试一次",
                "change_reason_cn": "上次九十五度冲泡方法不当，书里解释了低火茎茶怕沸水闷泡",
            },
            actor_side="subject",
            now_iso=_at_minutes(8),
        )
        st_rev = r1["growth_stance"]
        self.assertEqual(st_rev["status"], "REVISED")
        self.assertEqual(st_rev["provenance_tier"], "read_knowledge")
        self.assertEqual(st_rev["replaces_stance_id"], "st_roasted_tea@v3")
        self.assertEqual(len(st_rev["prior_stances"]), 3)
        self.assertEqual([s["status"] for s in st_rev["prior_stances"]], ["UNFORMED", "TENTATIVE", "FORMED"])

        # Partner language persuasion flow on a second UNFORMED topic
        engine.apply_true_stance_command(
            self.root,
            {
                "action": "register_unformed_topic",
                "stance_id": "st_night_camp",
                "topic_cn": "秋夜河畔露营",
            },
            actor_side="partner",
            now_iso=_at_minutes(10),
        )

        # Bare coercion / pressure -> 409 COERCION_NOT_PERSUASION
        for coercive_arg in (
            "听我的，你必须赞成露营",
            "快改口，别废话，你也必须喜欢",
            "我命令你直接同意去露营",
            "不用多想，按我说的办",
            "别再讨论按我说的办就是了",
            "照我说的来吧，不用再讨论",
        ):
            with self.assertRaises(engine.V09TrueStanceError) as ctx_c:
                engine.apply_true_stance_command(
                    self.root,
                    {
                        "action": "partner_persuade_stance",
                        "stance_id": "st_night_camp",
                        "partner_argument_cn": coercive_arg,
                    },
                    actor_side="partner",
                    now_iso=_at_minutes(11),
                )
            self.assertEqual(ctx_c.exception.status_code, 409)
            self.assertEqual(ctx_c.exception.reason_code, "COERCION_NOT_PERSUASION")

        # Substantive non-coercive argument succeeds but NEVER auto-converts to FORMED
        pers = engine.apply_true_stance_command(
            self.root,
            {
                "action": "partner_persuade_stance",
                "stance_id": "st_night_camp",
                "partner_argument_cn": "河畔营地秋天晚上几乎没有蚊虫，带上厚防潮垫其实很暖和，还能看清银河",
                "evidence_ref": "photo:camp_river_autumn",
            },
            actor_side="partner",
            now_iso=_at_minutes(12),
        )
        self.assertEqual(pers["status"], "UNFORMED")

        # Subject cannot be forced to promote to FORMED via evaluate_partner_persuasion
        with self.assertRaises(engine.V09TrueStanceError) as ctx_pf:
            engine.apply_true_stance_command(
                self.root,
                {
                    "action": "evaluate_partner_persuasion",
                    "stance_id": "st_night_camp",
                    "persuasion_verdict": "PERSUADED_TENTATIVE",
                    "promote_to_formed": True,
                },
                actor_side="subject",
                now_iso=_at_minutes(13),
            )
        self.assertEqual(ctx_pf.exception.status_code, 409)
        self.assertEqual(ctx_pf.exception.reason_code, "COERCION_NOT_PERSUASION")

        # Subject evaluates as PARTIALLY_TAKEN_KEEP_DOUBT -> TENTATIVE + partner_heard + doubt preserved
        ev1 = engine.apply_true_stance_command(
            self.root,
            {
                "action": "evaluate_partner_persuasion",
                "stance_id": "st_night_camp",
                "persuasion_verdict": "PARTIALLY_TAKEN_KEEP_DOUBT",
                "subject_response_cn": "听起来看银河确实有吸引力，不过夜里河边潮气我还是有点担心",
                "kept_doubt_cn": "担心半夜河畔返潮太冷",
            },
            actor_side="subject",
            now_iso=_at_minutes(14),
        )
        st_camp = ev1["growth_stance"]
        self.assertEqual(st_camp["status"], "TENTATIVE")
        self.assertEqual(st_camp["provenance_tier"], "partner_heard")
        self.assertEqual(st_camp["latest_persuasion_verdict"], "PARTIALLY_TAKEN_KEEP_DOUBT")
        self.assertIn("担心半夜河畔返潮太冷", st_camp["doubts_cn"])

        # Another persuasion evaluated as NOT_CONVINCED_YET leaves status unchanged
        engine.apply_true_stance_command(
            self.root,
            {
                "action": "partner_persuade_stance",
                "stance_id": "st_night_camp",
                "partner_argument_cn": "其实河岸高坡那块砂石地排水很快，往年露水不多",
            },
            actor_side="partner",
            now_iso=_at_minutes(15),
        )
        ev2 = engine.apply_true_stance_command(
            self.root,
            {
                "action": "evaluate_partner_persuasion",
                "stance_id": "st_night_camp",
                "persuasion_verdict": "NOT_CONVINCED_YET",
                "subject_response_cn": "等哪天先去白日踩点看看地面潮度再决定",
            },
            actor_side="subject",
            now_iso=_at_minutes(16),
        )
        self.assertEqual(ev2["status"], "TENTATIVE")
        self.assertEqual(ev2["persuasion_verdict"], "NOT_CONVINCED_YET")

    # ------------------------------------------------------------------
    # 6. Private Withholding, Actor-Side Boundaries & Smuggled Keys
    # ------------------------------------------------------------------

    def test_06_private_withholding_and_permission_boundaries(self) -> None:
        # Subject registers 1 private stance and 1 shared stance
        engine.apply_true_stance_command(
            self.root,
            {
                "action": "register_unformed_topic",
                "stance_id": "st_private_thought",
                "topic_cn": "私下对独处节奏的想法",
                "shared_with_partner": False,
            },
            actor_side="subject",
            now_iso=T0,
        )
        engine.apply_true_stance_command(
            self.root,
            {
                "action": "register_unformed_topic",
                "stance_id": "st_shared_thought",
                "topic_cn": "周末散步路线偏好",
                "shared_with_partner": True,
            },
            actor_side="subject",
            now_iso=_at_minutes(1),
        )

        # Partner sees ONLY shared stance and summary reports count == 1 (no leakage)
        p_view = engine.read_true_stance_zero_write(self.root, actor_side="partner", now_iso=_at_minutes(2))
        s_view = engine.read_true_stance_zero_write(self.root, actor_side="subject", now_iso=_at_minutes(2))
        self.assertEqual([s["stance_id"] for s in p_view["growth_stances"]], ["st_shared_thought"])
        self.assertEqual(p_view["summary"]["growth_stance_count"], 1)
        self.assertEqual(len(s_view["growth_stances"]), 2)
        self.assertEqual(s_view["summary"]["growth_stance_count"], 2)

        # Partner cannot probe private stance via partner_persuade_stance -> 404
        with self.assertRaises(engine.V09TrueStanceError) as ctx_priv:
            engine.apply_true_stance_command(
                self.root,
                {
                    "action": "partner_persuade_stance",
                    "stance_id": "st_private_thought",
                    "partner_argument_cn": "我觉得每天傍晚散步二十分钟挺舒服的",
                },
                actor_side="partner",
                now_iso=_at_minutes(3),
            )
        self.assertEqual(ctx_priv.exception.status_code, 404)
        self.assertEqual(ctx_priv.exception.reason_code, "STANCE_NOT_FOUND")

        # Subject shares it -> becomes visible to partner; then unshares -> hidden again
        engine.apply_true_stance_command(
            self.root,
            {"action": "set_stance_shared", "stance_id": "st_private_thought", "shared_with_partner": True},
            actor_side="subject",
            now_iso=_at_minutes(4),
        )
        p_view2 = engine.read_true_stance_zero_write(self.root, actor_side="partner", now_iso=_at_minutes(5))
        self.assertEqual(len(p_view2["growth_stances"]), 2)

        engine.apply_true_stance_command(
            self.root,
            {"action": "set_stance_shared", "stance_id": "st_private_thought", "shared_with_partner": False},
            actor_side="subject",
            now_iso=_at_minutes(6),
        )
        p_view3 = engine.read_true_stance_zero_write(self.root, actor_side="partner", now_iso=_at_minutes(7))
        self.assertEqual([s["stance_id"] for s in p_view3["growth_stances"]], ["st_shared_thought"])

        # Actor-side boundary: partner cannot invoke subject-only actions
        for subj_action in sorted(engine.SUBJECT_ONLY_ACTIONS):
            with self.assertRaises(engine.V09TrueStanceError) as ctx_side:
                engine.apply_true_stance_command(
                    self.root,
                    {"action": subj_action, "choice_id": "bc_x", "stance_id": "st_x"},
                    actor_side="partner",
                    now_iso=_at_minutes(8),
                )
            self.assertEqual(ctx_side.exception.status_code, 403)
            self.assertEqual(ctx_side.exception.reason_code, "FORBIDDEN_ACTOR_SIDE")

        # Subject cannot impersonate partner-only actions
        for part_action in sorted(engine.PARTNER_ONLY_ACTIONS):
            with self.assertRaises(engine.V09TrueStanceError) as ctx_pside:
                engine.apply_true_stance_command(
                    self.root,
                    {"action": part_action, "choice_id": "bc_x", "stance_id": "st_x"},
                    actor_side="subject",
                    now_iso=_at_minutes(8),
                )
            self.assertEqual(ctx_pside.exception.status_code, 403)
            self.assertEqual(ctx_pside.exception.reason_code, "FORBIDDEN_ACTOR_SIDE")

        # Smuggled client keys rejected with 400 FORBIDDEN_CLIENT_KEY
        for bad_key in ("actor_side", "author_side", "uid", "capability", "affection_score", "obedience_level"):
            with self.assertRaises(engine.V09TrueStanceError) as ctx_key:
                engine.apply_true_stance_command(
                    self.root,
                    {"action": "open_blind_choice", bad_key: "spoofed", "topic_cn": "测试", "options": DINNER_OPTIONS},
                    actor_side="partner",
                    now_iso=_at_minutes(9),
                )
            self.assertEqual(ctx_key.exception.status_code, 400)
            self.assertEqual(ctx_key.exception.reason_code, "FORBIDDEN_CLIENT_KEY")

        # Partner smuggling clear_reservations -> 409 SYCOPHANTIC_REASON_ERASURE_FORBIDDEN
        with self.assertRaises(engine.V09TrueStanceError) as ctx_cr:
            engine.apply_true_stance_command(
                self.root,
                {
                    "action": "open_blind_choice",
                    "topic_cn": "测试",
                    "options": DINNER_OPTIONS,
                    "clear_reservations": False,
                },
                actor_side="partner",
                now_iso=_at_minutes(9),
            )
        self.assertEqual(ctx_cr.exception.status_code, 409)
        self.assertEqual(ctx_cr.exception.reason_code, "SYCOPHANTIC_REASON_ERASURE_FORBIDDEN")

    # ------------------------------------------------------------------
    # 7. 10-Thread Concurrency, Idempotency, Caps, Atomic 0600 & Symlinks
    # ------------------------------------------------------------------

    def test_07_ten_thread_concurrency_idempotency_and_atomic_0600(self) -> None:
        errors: list[BaseException] = []
        results: list[dict[str, Any]] = []
        lock = threading.Lock()

        def _worker(idx: int) -> None:
            try:
                res = engine.apply_true_stance_command(
                    self.root,
                    {
                        "action": "open_blind_choice",
                        "topic_cn": f"并发盲选话题_{idx}",
                        "options": DINNER_OPTIONS,
                        "operation_key": f"op_conc_unique_{idx}",
                    },
                    actor_side="partner",
                    now_iso=_at_minutes(idx),
                )
                with lock:
                    results.append(res)
            except BaseException as exc:  # pragma: no cover
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        self.assertEqual(errors, [])
        self.assertEqual(len(results), 10)
        self.assertEqual(len({r["choice_id"] for r in results}), 10)
        self.assertEqual(sorted(r["revision"] for r in results), list(range(1, 11)))

        # 10 concurrent threads replaying identical operation_key -> 1 applied, 9 replays
        dup_results: list[dict[str, Any]] = []
        dup_errors: list[BaseException] = []

        def _dup_worker() -> None:
            try:
                res = engine.apply_true_stance_command(
                    self.root,
                    {
                        "action": "register_unformed_topic",
                        "topic_cn": "并发幂等留白话题",
                        "operation_key": "op_shared_idempotent_key",
                    },
                    actor_side="partner",
                    now_iso=_at_minutes(20),
                )
                with lock:
                    dup_results.append(res)
            except BaseException as exc:  # pragma: no cover
                with lock:
                    dup_errors.append(exc)

        dup_threads = [threading.Thread(target=_dup_worker) for _ in range(10)]
        for t in dup_threads:
            t.start()
        for t in dup_threads:
            t.join(timeout=15)

        self.assertEqual(dup_errors, [])
        self.assertEqual(len(dup_results), 10)
        self.assertEqual(len({r["stance_id"] for r in dup_results}), 1)
        self.assertEqual(sum(1 for r in dup_results if not r.get("already_applied")), 1)
        self.assertEqual(sum(1 for r in dup_results if r.get("already_applied")), 9)

        # Conflicting payload under same operation_key -> 409 OPERATION_KEY_CONFLICT
        with self.assertRaises(engine.V09TrueStanceError) as ctx_conf:
            engine.apply_true_stance_command(
                self.root,
                {
                    "action": "register_unformed_topic",
                    "topic_cn": "被篡改的不同话题",
                    "operation_key": "op_shared_idempotent_key",
                },
                actor_side="partner",
                now_iso=_at_minutes(21),
            )
        self.assertEqual(ctx_conf.exception.status_code, 409)
        self.assertEqual(ctx_conf.exception.reason_code, "OPERATION_KEY_CONFLICT")

        # Cross-side reuse of same operation_key -> 409 OPERATION_KEY_CONFLICT
        with self.assertRaises(engine.V09TrueStanceError) as ctx_xside:
            engine.apply_true_stance_command(
                self.root,
                {
                    "action": "register_unformed_topic",
                    "topic_cn": "并发幂等留白话题",
                    "operation_key": "op_shared_idempotent_key",
                },
                actor_side="subject",
                now_iso=_at_minutes(21),
            )
        self.assertEqual(ctx_xside.exception.status_code, 409)
        self.assertEqual(ctx_xside.exception.reason_code, "OPERATION_KEY_CONFLICT")

        # Verify pure LF, UTF-8 no BOM, and 0600 permissions on POSIX
        state_file = engine.true_stance_state_path(self.root)
        raw = state_file.read_bytes()
        self.assertEqual(raw.count(b"\r"), 0)
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        if os.name == "posix":
            mode = stat.S_IMODE(state_file.stat().st_mode)
            self.assertEqual(mode, 0o600)

    def test_08_bounded_caps_and_symlink_rejection(self) -> None:
        # Bounded max 32 blind_choices & max 64 growth_stances
        for i in range(35):
            engine.apply_true_stance_command(
                self.root,
                {
                    "action": "open_blind_choice",
                    "choice_id": f"bc_cap_{i:02d}",
                    "topic_cn": f"上限测试选择_{i}",
                    "options": DINNER_OPTIONS,
                },
                actor_side="partner",
                now_iso=_at_minutes(i),
            )
        for j in range(68):
            engine.apply_true_stance_command(
                self.root,
                {
                    "action": "register_unformed_topic",
                    "stance_id": f"st_cap_{j:02d}",
                    "topic_cn": f"上限测试立场_{j}",
                },
                actor_side="partner",
                now_iso=_at_minutes(j),
            )

        view = engine.read_true_stance_zero_write(self.root, actor_side="partner", now_iso=_at_minutes(100))
        self.assertEqual(len(view["blind_choices"]), engine.MAX_BLIND_CHOICES)
        self.assertEqual(len(view["growth_stances"]), engine.MAX_GROWTH_STANCES)

        if os.name == "posix":
            sym_root = self.base / "sym_inst"
            os.symlink(str(self.root), str(sym_root))
            with self.assertRaises(engine.V09TrueStanceError) as ctx_sym:
                engine.apply_true_stance_command(
                    sym_root,
                    {
                        "action": "open_blind_choice",
                        "topic_cn": "符号链接拒绝测试",
                        "options": DINNER_OPTIONS,
                    },
                    actor_side="partner",
                    now_iso=T0,
                )
            self.assertEqual(ctx_sym.exception.status_code, 400)
            self.assertEqual(ctx_sym.exception.reason_code, "UNSAFE_SYMLINK")

            # Read on symlink fails closed cleanly with 0 writes
            sym_view = engine.read_true_stance_zero_write(sym_root, actor_side="partner", now_iso=T0)
            self.assertTrue(sym_view["forget_barrier_fail_closed"])
            self.assertEqual(sym_view["blind_choices"], [])
            self.assertEqual(sym_view["growth_stances"], [])

    # ------------------------------------------------------------------
    # 8. Unified Forget Barrier Filtering & Fail-Closed Handling
    # ------------------------------------------------------------------

    def test_09_forget_barrier_filters_choice_stance_and_fail_closed(self) -> None:
        self._open_dinner_choice(choice_id="bc_keep_01")
        self._open_dinner_choice(choice_id="bc_forget_02")

        engine.apply_true_stance_command(
            self.root,
            {"action": "register_unformed_topic", "stance_id": "st_keep_01", "topic_cn": "保留的立场"},
            actor_side="partner",
            now_iso=_at_minutes(1),
        )
        engine.apply_true_stance_command(
            self.root,
            {"action": "register_unformed_topic", "stance_id": "st_forget_02", "topic_cn": "要遗忘的立场"},
            actor_side="partner",
            now_iso=_at_minutes(2),
        )
        engine.apply_true_stance_command(
            self.root,
            {"action": "register_unformed_topic", "stance_id": "st_forget_by_ref", "topic_cn": "关联回执要遗忘"},
            actor_side="partner",
            now_iso=_at_minutes(3),
        )
        engine.apply_true_stance_command(
            self.root,
            {
                "action": "advance_stance_from_experience",
                "stance_id": "st_forget_by_ref",
                "target_status": "FORMED",
                "provenance_tier": "firsthand_lived",
                "receipt_ref": "receipt:erased_episode_99",
                "stance_summary_cn": "依据那次经历形成的判断",
            },
            actor_side="subject",
            now_iso=_at_minutes(4),
        )

        # Submit explicit forget command blocking bc_forget_02, st_forget_02, and receipt:erased_episode_99
        f_code, f_res = _rlf.submit_forget_command(
            self.root,
            command_text="删掉记录和相关记忆",
            operation_id="op_forget_v09_stance_01",
            explicit_scope=["bc_forget_02", "st_forget_02", "receipt:erased_episode_99"],
            explicit_confirmation=True,
            now_iso=_at_minutes(5),
        )
        self.assertEqual(f_code, 200, f_res)

        snap_before = _snapshot_tree(self.root)
        post = engine.read_true_stance_zero_write(self.root, actor_side="partner", now_iso=_at_minutes(6))
        self.assertEqual(snap_before, _snapshot_tree(self.root))
        self.assertTrue(post["zero_write"])
        self.assertEqual(post["llm_calls"], 0)
        self.assertTrue(post["forget_barrier_active"])
        self.assertFalse(post["forget_barrier_fail_closed"])

        self.assertEqual([c["choice_id"] for c in post["blind_choices"]], ["bc_keep_01"])
        self.assertEqual([s["stance_id"] for s in post["growth_stances"]], ["st_keep_01"])

        # Corrupt control ledger -> fail-closed with 0 disk writes
        ctrl_path = self.root / "control_authority" / "v02_control_highwater_ledger.json"
        ctrl_path.write_text("{corrupt-json", encoding="utf-8")
        snap_corrupt = _snapshot_tree(self.root)
        closed = engine.read_true_stance_zero_write(self.root, actor_side="partner", now_iso=_at_minutes(7))
        self.assertEqual(snap_corrupt, _snapshot_tree(self.root))
        self.assertTrue(closed["forget_barrier_fail_closed"])
        self.assertEqual(closed["blind_choices"], [])
        self.assertEqual(closed["growth_stances"], [])

    # ------------------------------------------------------------------
    # 9. Source File Hygiene Gate
    # ------------------------------------------------------------------

    def test_10_source_file_hygiene_lf_no_bom_zero_emoji_neutral_ta(self) -> None:
        for rel in (
            "components/life/scripts/v09_true_stance_engine.py",
            "tests/test_v09_true_stance_and_blind_choice.py",
        ):
            target = REPO_ROOT / rel
            raw = target.read_bytes()
            self.assertEqual(raw.count(b"\r"), 0, f"{rel} must be pure LF")
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), f"{rel} must have no UTF-8 BOM")
            text = raw.decode("utf-8")
            self.assertEqual(text.count("\u5979"), 0, f"Forbidden pronoun in {rel}")
            self.assertEqual(text.count("\u4ed6"), 0, f"Forbidden pronoun in {rel}")
            self.assertEqual(text.count("\u5343\u4ee3"), 0, f"Forbidden name in {rel}")
            emojis = [ch for ch in text if unicodedata.category(ch) == "So"]
            self.assertEqual(emojis, [], f"Found forbidden unicode.So characters in {rel}: {emojis!r}")

    def test_11_persuaded_tentative_and_evidence_kinds_and_instance_paths(self) -> None:
        # Nested managed instance path resolves cleanly to <day_root>/life/v09_true_stance_state.json
        managed = self.base / "managed_inst"
        (managed / "run").mkdir(parents=True, exist_ok=True)
        (managed / "run" / "nyairo-day.instance.json").write_text(
            json.dumps({"schema": "nyairo.day.managed.v1", "instance_seed": "s_managed"}) + "\n",
            encoding="utf-8",
        )
        nested = managed / "one-day" / "world-home"
        nested.mkdir(parents=True, exist_ok=True)
        self.assertEqual(
            engine.true_stance_state_path(nested),
            managed / "life" / "v09_true_stance_state.json",
        )

        # Register UNFORMED via nested path, persuade, evaluate PERSUADED_TENTATIVE
        engine.apply_true_stance_command(
            nested,
            {"action": "register_unformed_topic", "stance_id": "st_walk_rain", "topic_cn": "小雨天撑伞散步"},
            actor_side="partner",
            now_iso=T0,
        )
        engine.apply_true_stance_command(
            nested,
            {
                "action": "partner_persuade_stance",
                "stance_id": "st_walk_rain",
                "partner_argument_cn": "毛毛细雨时巷口人少空气清凉，撑伞走十五分钟鞋底其实不会湿",
            },
            actor_side="partner",
            now_iso=_at_minutes(1),
        )
        ev = engine.apply_true_stance_command(
            nested,
            {
                "action": "evaluate_partner_persuasion",
                "stance_id": "st_walk_rain",
                "persuasion_verdict": "PERSUADED_TENTATIVE",
                "subject_response_cn": "说得有道理，下次小雨可以试着短走十分钟看看",
            },
            actor_side="subject",
            now_iso=_at_minutes(2),
        )
        self.assertEqual(ev["status"], "TENTATIVE")
        self.assertEqual(ev["growth_stance"]["provenance_tier"], "partner_heard")

        # partner_heard cannot jump directly to FORMED via advance_stance_from_experience
        with self.assertRaises(engine.V09TrueStanceError) as ctx_ph:
            engine.apply_true_stance_command(
                nested,
                {
                    "action": "advance_stance_from_experience",
                    "stance_id": "st_walk_rain",
                    "target_status": "FORMED",
                    "provenance_tier": "partner_heard",
                    "stance_summary_cn": "伴侣说细雨走十五分钟很舒服所以我也喜欢",
                },
                actor_side="subject",
                now_iso=_at_minutes(3),
            )
        self.assertEqual(ctx_ph.exception.status_code, 409)
        self.assertEqual(ctx_ph.exception.reason_code, "COERCION_NOT_PERSUASION")

        # Advance to FORMED via firsthand_lived, then verify tentative_guess cannot overwrite it
        engine.apply_true_stance_command(
            nested,
            {
                "action": "advance_stance_from_experience",
                "stance_id": "st_walk_rain",
                "target_status": "FORMED",
                "provenance_tier": "firsthand_lived",
                "receipt_ref": "episode:rain_walk_01",
                "stance_summary_cn": "亲身试过小雨散步十分钟，确实很安静舒展",
            },
            actor_side="subject",
            now_iso=_at_minutes(4),
        )
        with self.assertRaises(engine.V09TrueStanceError) as ctx_tg2:
            engine.apply_true_stance_command(
                nested,
                {
                    "action": "advance_stance_from_experience",
                    "stance_id": "st_walk_rain",
                    "target_status": "TENTATIVE",
                    "provenance_tier": "tentative_guess",
                    "stance_summary_cn": "随口猜想",
                },
                actor_side="subject",
                now_iso=_at_minutes(5),
            )
        self.assertEqual(ctx_tg2.exception.status_code, 409)
        self.assertEqual(ctx_tg2.exception.reason_code, "TENTATIVE_GUESS_CANNOT_FORM_STANCE")

        # Partner persuasion on already-FORMED stance never silently downgrades or flips it
        engine.apply_true_stance_command(
            nested,
            {
                "action": "partner_persuade_stance",
                "stance_id": "st_walk_rain",
                "partner_argument_cn": "不过风大的时候撑伞确实会有点冷，最好挑无风的小雨天",
            },
            actor_side="partner",
            now_iso=_at_minutes(6),
        )
        ev_formed = engine.apply_true_stance_command(
            nested,
            {
                "action": "evaluate_partner_persuasion",
                "stance_id": "st_walk_rain",
                "persuasion_verdict": "PERSUADED_TENTATIVE",
            },
            actor_side="subject",
            now_iso=_at_minutes(7),
        )
        self.assertEqual(ev_formed["status"], "FORMED")
        self.assertEqual(ev_formed["growth_stance"]["provenance_tier"], "firsthand_lived")

        # Verify reading from top-level managed root sees the exact same state with 0 writes
        snap = _snapshot_tree(managed)
        view = engine.read_true_stance_zero_write(managed, actor_side="partner", now_iso=_at_minutes(8))
        self.assertEqual(snap, _snapshot_tree(managed))
        self.assertEqual(len(view["growth_stances"]), 1)
        self.assertEqual(view["growth_stances"][0]["status"], "FORMED")

    def test_12_revised_evidence_kinds_parent_and_file_symlink_guards(self) -> None:
        # Test lived_experience and verified_reading evidence kinds + clear_reservations rejection
        self._open_dinner_choice(choice_id="bc_lived")
        engine.apply_true_stance_command(
            self.root,
            {
                "action": "subject_commit_blind_choice",
                "choice_id": "bc_lived",
                "selected_choice_id": "noodle_alley",
                "reasons": [{"facet": "taste", "text_cn": "上次觉得海湾小馆偏咸"}],
            },
            actor_side="subject",
            now_iso=_at_minutes(1),
        )
        engine.apply_true_stance_command(
            self.root,
            {
                "action": "partner_submit_blind_choice",
                "choice_id": "bc_lived",
                "selected_choice_id": "bistro_harbor",
            },
            actor_side="partner",
            now_iso=_at_minutes(2),
        )
        with self.assertRaises(engine.V09TrueStanceError) as ctx_cr:
            engine.apply_true_stance_command(
                self.root,
                {
                    "action": "resolve_blind_choice",
                    "choice_id": "bc_lived",
                    "resolution_mode": "REVISED_WITH_NEW_EVIDENCE",
                    "clear_reservations": True,
                    "new_evidence": {
                        "evidence_kind": "lived_experience",
                        "summary_cn": "刚才路过试吃了新款低盐清汤版本，确实清爽不咸了",
                        "evidence_ref": "receipt:sample_bistro_02",
                    },
                },
                actor_side="subject",
                now_iso=_at_minutes(3),
            )
        self.assertEqual(ctx_cr.exception.status_code, 409)
        self.assertEqual(ctx_cr.exception.reason_code, "SYCOPHANTIC_REASON_ERASURE_FORBIDDEN")

        ok_lived = engine.apply_true_stance_command(
            self.root,
            {
                "action": "resolve_blind_choice",
                "choice_id": "bc_lived",
                "resolution_mode": "REVISED_WITH_NEW_EVIDENCE",
                "new_evidence": {
                    "evidence_kind": "lived_experience",
                    "summary_cn": "刚才路过试吃了新款低盐清汤版本，确实清爽不咸了",
                    "evidence_ref": "receipt:sample_bistro_02",
                },
            },
            actor_side="subject",
            now_iso=_at_minutes(4),
        )
        self.assertTrue(ok_lived["blind_choice"]["judgment_flipped"])
        self.assertEqual(len(ok_lived["blind_choice"]["prior_reasons_history"]), 1)

        if os.name == "posix":
            # Symlink life/ directory rejected on write
            bad_root = self.base / "bad_life_dir_root"
            bad_root.mkdir(parents=True, exist_ok=True)
            real_target = self.base / "real_life_target"
            real_target.mkdir(parents=True, exist_ok=True)
            os.symlink(str(real_target), str(bad_root / "life"))
            with self.assertRaises(engine.V09TrueStanceError) as ctx_dir:
                engine.apply_true_stance_command(
                    bad_root,
                    {"action": "register_unformed_topic", "topic_cn": "目录符号链接测试"},
                    actor_side="partner",
                    now_iso=T0,
                )
            self.assertEqual(ctx_dir.exception.status_code, 400)
            self.assertEqual(ctx_dir.exception.reason_code, "UNSAFE_SYMLINK")

            # Symlink state file rejected on write
            bad_file_root = self.base / "bad_file_root"
            (bad_file_root / "life").mkdir(parents=True, exist_ok=True)
            decoy = self.base / "decoy.json"
            decoy.write_text("{}\n", encoding="utf-8")
            os.symlink(str(decoy), str(bad_file_root / "life" / "v09_true_stance_state.json"))
            with self.assertRaises(engine.V09TrueStanceError) as ctx_file:
                engine.apply_true_stance_command(
                    bad_file_root,
                    {"action": "register_unformed_topic", "topic_cn": "文件符号链接测试"},
                    actor_side="partner",
                    now_iso=T0,
                )
            self.assertEqual(ctx_file.exception.status_code, 400)
            self.assertEqual(ctx_file.exception.reason_code, "UNSAFE_SYMLINK")

    def test_13_padded_coercion_blocked_while_concrete_factual_arguments_pass(self) -> None:
        """Regression test for P1 padded coercion bypass (e.g. '不用多想，按我说的办')."""
        self._open_dinner_choice(choice_id="bc_p1_reg")
        engine.apply_true_stance_command(
            self.root,
            {
                "action": "subject_commit_blind_choice",
                "choice_id": "bc_p1_reg",
                "selected_choice_id": "noodle_alley",
                "reasons": [{"facet": "price", "text_cn": "海湾小馆人均价格偏贵"}],
            },
            actor_side="subject",
            now_iso=_at_minutes(1),
        )
        engine.apply_true_stance_command(
            self.root,
            {
                "action": "partner_submit_blind_choice",
                "choice_id": "bc_p1_reg",
                "selected_choice_id": "bistro_harbor",
            },
            actor_side="partner",
            now_iso=_at_minutes(2),
        )
        engine.apply_true_stance_command(
            self.root,
            {
                "action": "register_unformed_topic",
                "stance_id": "st_p1_reg",
                "topic_cn": "周末海边骑行",
            },
            actor_side="partner",
            now_iso=_at_minutes(2),
        )

        padded_coercions = (
            "不用多想，按我说的办",
            "别再讨论按我说的办就是了",
            "别再商量了，照我说的选就行",
            "不用商量，按我说的来吧",
        )
        for bad in padded_coercions:
            with self.assertRaises(engine.V09TrueStanceError, msg=f"blind_choice should reject {bad!r}") as c1:
                engine.apply_true_stance_command(
                    self.root,
                    {
                        "action": "resolve_blind_choice",
                        "choice_id": "bc_p1_reg",
                        "resolution_mode": "REVISED_WITH_NEW_EVIDENCE",
                        "new_evidence": {
                            "evidence_kind": "concrete_partner_fact",
                            "summary_cn": bad,
                        },
                    },
                    actor_side="subject",
                    now_iso=_at_minutes(3),
                )
            self.assertEqual(c1.exception.status_code, 409)
            self.assertEqual(c1.exception.reason_code, "BARE_INSISTENCE_NOT_EVIDENCE")

            with self.assertRaises(engine.V09TrueStanceError, msg=f"persuasion should reject {bad!r}") as c2:
                engine.apply_true_stance_command(
                    self.root,
                    {
                        "action": "partner_persuade_stance",
                        "stance_id": "st_p1_reg",
                        "partner_argument_cn": bad,
                    },
                    actor_side="partner",
                    now_iso=_at_minutes(3),
                )
            self.assertEqual(c2.exception.status_code, 409)
            self.assertEqual(c2.exception.reason_code, "COERCION_NOT_PERSUASION")

        # Genuine factual reason (even prefixed casually) still succeeds on both paths!
        ok_pers = engine.apply_true_stance_command(
            self.root,
            {
                "action": "partner_persuade_stance",
                "stance_id": "st_p1_reg",
                "partner_argument_cn": "沿海绿道刚修好全程平缓只有三公里，上午海风清凉很适合慢慢骑",
            },
            actor_side="partner",
            now_iso=_at_minutes(4),
        )
        self.assertTrue(ok_pers["ok"])
        self.assertEqual(ok_pers["status"], "UNFORMED")

        ok_rev = engine.apply_true_stance_command(
            self.root,
            {
                "action": "resolve_blind_choice",
                "choice_id": "bc_p1_reg",
                "resolution_mode": "REVISED_WITH_NEW_EVIDENCE",
                "new_evidence": {
                    "evidence_kind": "concrete_partner_fact",
                    "summary_cn": "不用多想，刚打电话问过老板今天晚市半价且现在空位很多不用排队",
                    "evidence_ref": "call:bistro_p1_reg",
                },
            },
            actor_side="subject",
            now_iso=_at_minutes(5),
        )
        self.assertTrue(ok_rev["ok"])
        self.assertEqual(ok_rev["blind_choice"]["adopted_action_choice_id"], "bistro_harbor")
        self.assertTrue(ok_rev["blind_choice"]["judgment_flipped"])

    def test_10_digit_only_coercion_rejected_and_corrupt_collection_entry_safe(self) -> None:
        engine.apply_true_stance_command(
            self.root,
            {
                "action": "open_blind_choice",
                "choice_id": "bc_p2_digit",
                "topic_cn": "晚饭选哪家",
                "options": DINNER_OPTIONS,
            },
            actor_side="partner",
            now_iso=T0,
        )
        engine.apply_true_stance_command(
            self.root,
            {
                "action": "subject_commit_blind_choice",
                "choice_id": "bc_p2_digit",
                "selected_choice_id": "noodle_alley",
                "reasons": [{"facet": "queue", "text_cn": "这家不用排队"}],
            },
            actor_side="subject",
            now_iso=_at_minutes(1),
        )
        engine.apply_true_stance_command(
            self.root,
            {
                "action": "partner_submit_blind_choice",
                "choice_id": "bc_p2_digit",
                "selected_choice_id": "bistro_harbor",
            },
            actor_side="partner",
            now_iso=_at_minutes(2),
        )

        # Bare coercion padded only with digits must be rejected
        with self.assertRaises(engine.V09TrueStanceError) as digit_err:
            engine.apply_true_stance_command(
                self.root,
                {
                    "action": "resolve_blind_choice",
                    "choice_id": "bc_p2_digit",
                    "resolution_mode": "REVISED_WITH_NEW_EVIDENCE",
                    "new_evidence": {
                        "evidence_kind": "concrete_partner_fact",
                        "summary_cn": "听我的，123456",
                    },
                },
                actor_side="subject",
                now_iso=_at_minutes(3),
            )
        self.assertEqual(digit_err.exception.status_code, 409)
        self.assertEqual(digit_err.exception.reason_code, "BARE_INSISTENCE_NOT_EVIDENCE")

        # Real quantitative context still passes
        is_c, _ = engine._detect_bare_coercion_or_insistence("听我的，半价30元还送饮品")
        self.assertFalse(is_c)

        # Corrupt non-Mapping values inside blind_choices/growth_stances do not crash zero-write read
        st_path = engine.true_stance_state_path(self.root)
        doc = json.loads(st_path.read_text(encoding="utf-8"))
        doc["blind_choices"]["bad_list_row"] = ["corrupt"]
        doc["growth_stances"]["bad_str_row"] = "corrupt"
        st_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

        ro = engine.read_true_stance_zero_write(self.root, actor_side="subject", now_iso=_at_minutes(4))
        self.assertTrue(ro["ok"])
        self.assertEqual([c["choice_id"] for c in ro["blind_choices"]], ["bc_p2_digit"])


if __name__ == "__main__":
    unittest.main()

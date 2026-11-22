#!/usr/bin/env python3
"""Unit tests for the shared scoped-content-record substrate (wave E1a).

Coverage:
* envelope normalization: side / visibility validation, canonical source refs,
  timestamps copied verbatim;
* all four visibility classes plus author ownership and raw-source exposure;
* frozen original-fact immutability (identical re-submit ok, mutation refused);
* the three gates: WITHDRAWN, RECEIVED_UNOPENED / sealed, private ownership;
* lifecycle: consider_after dormancy, expires_at expiry at the exact boundary,
  active_now=False, and byte-identical rows across repeated reads;
* concern ranking filters authorization first, so WITHDRAWN / sealed / private
  rows never appear even on an exact keyword match.

Run (WSL):
``/tmp/rc104-venv/bin/python -B -m unittest tests.test_scoped_content_record``
"""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from scoped_content_record import (  # noqa: E402
    FROZEN_FACT_IMMUTABLE,
    check_read_and_attention_gate,
    enforce_frozen_fact_immutability,
    normalize_content_envelope,
    rank_authorized_by_concern,
)

NOW = "2026-11-16T12:00:00Z"
BEFORE = "2026-11-16T11:00:00Z"
AFTER = "2026-11-16T13:00:00Z"


class NormalizeEnvelopeTests(unittest.TestCase):
    def test_defaults_are_private_and_timestamps_stay_verbatim(self) -> None:
        envelope = normalize_content_envelope(
            author_side="subject",
            created_at="2026-11-16T09:30:00+08:00",
            consider_after="2026-11-17T00:00:00+08:00",
            expires_at="2026-11-20T00:00:00Z",
        )
        self.assertEqual(envelope["author_side"], "subject")
        self.assertEqual(envelope["visibility"], "private")
        self.assertEqual(envelope["created_at"], "2026-11-16T09:30:00+08:00")
        self.assertEqual(envelope["consider_after"], "2026-11-17T00:00:00+08:00")
        self.assertEqual(envelope["expires_at"], "2026-11-20T00:00:00Z")
        self.assertFalse(envelope["sealed"])
        self.assertFalse(envelope["withdrawn"])
        self.assertIsNone(envelope["frozen_fact"])

    def test_source_refs_are_sorted_deduplicated_and_trimmed(self) -> None:
        envelope = normalize_content_envelope(
            author_side="partner",
            source_refs=("b", " a ", "b", "", "a"),
        )
        self.assertEqual(envelope["source_refs"], ("a", "b"))
        # JSON-serializable as promised.
        json.dumps(envelope)

    def test_invalid_side_and_visibility_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "INVALID_AUTHOR_SIDE"):
            normalize_content_envelope(author_side="shared")
        with self.assertRaisesRegex(ValueError, "INVALID_VISIBILITY"):
            normalize_content_envelope(author_side="subject", visibility="public")

    def test_all_four_visibility_classes_accepted(self) -> None:
        for visibility in ("private", "shared", "shared_excerpt", "unconfirmed_idiom"):
            envelope = normalize_content_envelope(
                author_side="subject", visibility=visibility
            )
            self.assertEqual(envelope["visibility"], visibility)


class VisibilityGateTests(unittest.TestCase):
    def test_private_only_owner_reads(self) -> None:
        row = {"author_side": "subject", "visibility": "private"}
        owner = check_read_and_attention_gate(row, actor_side="subject", now_iso=NOW)
        peer = check_read_and_attention_gate(row, actor_side="partner", now_iso=NOW)

        self.assertTrue(owner["allowed_read"])
        self.assertTrue(owner["allowed_body"])
        self.assertTrue(owner["allowed_attention"])
        self.assertTrue(owner["expose_raw_sources"])
        self.assertEqual(owner["reason"], "PRIVATE_AUTHOR")

        self.assertFalse(peer["allowed_read"])
        self.assertFalse(peer["allowed_body"])
        self.assertFalse(peer["allowed_attention"])
        self.assertFalse(peer["expose_raw_sources"])
        self.assertEqual(peer["reason"], "PRIVATE_OTHER_SIDE")

    def test_shared_readable_by_both_but_raw_sources_only_for_author(self) -> None:
        row = {"author_side": "partner", "visibility": "shared"}
        subject = check_read_and_attention_gate(row, actor_side="subject", now_iso=NOW)
        partner = check_read_and_attention_gate(row, actor_side="partner", now_iso=NOW)

        for gate in (subject, partner):
            self.assertTrue(gate["allowed_read"])
            self.assertTrue(gate["allowed_body"])
            self.assertTrue(gate["allowed_attention"])
            self.assertEqual(gate["reason"], "SHARED")
        self.assertFalse(subject["expose_raw_sources"])
        self.assertTrue(partner["expose_raw_sources"])

    def test_shared_excerpt_hides_raw_sources_from_peer(self) -> None:
        row = {"author_side": "subject", "visibility": "shared_excerpt"}
        peer = check_read_and_attention_gate(row, actor_side="partner", now_iso=NOW)
        owner = check_read_and_attention_gate(row, actor_side="subject", now_iso=NOW)

        self.assertTrue(peer["allowed_read"])
        self.assertTrue(peer["allowed_body"])
        self.assertTrue(peer["allowed_attention"])
        self.assertEqual(peer["reason"], "SHARED_EXCERPT")
        self.assertFalse(peer["expose_raw_sources"])
        self.assertTrue(owner["expose_raw_sources"])

    def test_unconfirmed_idiom_is_readable_as_a_proposal(self) -> None:
        row = {"author_side": "partner", "visibility": "unconfirmed_idiom"}
        for actor in ("subject", "partner"):
            gate = check_read_and_attention_gate(row, actor_side=actor, now_iso=NOW)
            self.assertTrue(gate["allowed_read"])
            self.assertTrue(gate["allowed_body"])
            self.assertTrue(gate["allowed_attention"])
            self.assertEqual(gate["reason"], "UNCONFIRMED_IDIOM")
            self.assertEqual(gate["expose_raw_sources"], actor == "partner")

    def test_legacy_shared_with_partner_flag_maps_to_visibility(self) -> None:
        shared = {"author_side": "subject", "shared_with_partner": True}
        private = {"author_side": "subject", "shared_with_partner": False}

        self.assertTrue(
            check_read_and_attention_gate(shared, actor_side="partner", now_iso=NOW)[
                "allowed_read"
            ]
        )
        self.assertFalse(
            check_read_and_attention_gate(private, actor_side="partner", now_iso=NOW)[
                "allowed_read"
            ]
        )

    def test_envelope_drives_gate(self) -> None:
        envelope = normalize_content_envelope(
            author_side="partner",
            visibility="shared",
            expires_at="2026-11-20T00:00:00Z",
        )
        row = {"content_envelope": envelope}
        gate = check_read_and_attention_gate(row, actor_side="subject", now_iso=NOW)
        self.assertTrue(gate["allowed_read"])
        self.assertTrue(gate["allowed_attention"])
        self.assertFalse(gate["expose_raw_sources"])

    def test_field_name_overrides(self) -> None:
        row = {
            "creator_side": "partner",
            "scope": "private",
            "STATE": "WITHDRAWN",
        }
        gate = check_read_and_attention_gate(
            row,
            actor_side="partner",
            now_iso=NOW,
            author_field="creator_side",
            visibility_field="scope",
            status_field="STATE",
        )
        self.assertTrue(gate["withdrawn"])
        self.assertFalse(gate["allowed_read"])


class FrozenFactTests(unittest.TestCase):
    def test_identical_resubmission_is_allowed(self) -> None:
        existing = {"original_fact_cn": "她第一次来店里只喝了半杯。"}
        candidate = {"original_fact_cn": "她第一次来店里只喝了半杯。"}
        self.assertIsNone(enforce_frozen_fact_immutability(existing, candidate))

    def test_whitespace_trimmed_match_is_allowed(self) -> None:
        existing = {"original_fact_cn": "原始事实"}
        candidate = {"original_fact_cn": "  原始事实  "}
        self.assertIsNone(enforce_frozen_fact_immutability(existing, candidate))

    def test_mutation_raises(self) -> None:
        existing = {"original_fact_cn": "原始事实"}
        candidate = {"original_fact_cn": "被改写的事实"}
        with self.assertRaises(ValueError) as caught:
            enforce_frozen_fact_immutability(existing, candidate)
        self.assertEqual(str(caught.exception), FROZEN_FACT_IMMUTABLE)

    def test_missing_or_empty_candidate_is_append_only(self) -> None:
        existing = {"original_fact_cn": "原始事实"}
        self.assertIsNone(enforce_frozen_fact_immutability(existing, {}))
        self.assertIsNone(
            enforce_frozen_fact_immutability(existing, {"original_fact_cn": "   "})
        )

    def test_empty_existing_accepts_first_fact(self) -> None:
        self.assertIsNone(
            enforce_frozen_fact_immutability(
                {"original_fact_cn": ""}, {"original_fact_cn": "第一次记录"}
            )
        )

    def test_custom_fact_key(self) -> None:
        existing = {"prior_impressions": "旧印象"}
        with self.assertRaisesRegex(ValueError, FROZEN_FACT_IMMUTABLE):
            enforce_frozen_fact_immutability(
                existing,
                {"prior_impressions": "新印象"},
                fact_key="prior_impressions",
            )


class WithdrawnAndSealedTests(unittest.TestCase):
    def test_withdrawn_hides_everything(self) -> None:
        row = {
            "author_side": "partner",
            "visibility": "shared",
            "withdrawn": True,
            "text_cn": "任何正文",
        }
        gate = check_read_and_attention_gate(row, actor_side="subject", now_iso=NOW)
        self.assertTrue(gate["withdrawn"])
        self.assertFalse(gate["allowed_read"])
        self.assertFalse(gate["allowed_body"])
        self.assertFalse(gate["allowed_attention"])
        self.assertFalse(gate["expose_raw_sources"])
        self.assertEqual(gate["reason"], "WITHDRAWN")

    def test_status_withdrawn_hides_everything(self) -> None:
        row = {"author_side": "partner", "visibility": "shared", "status": "WITHDRAWN"}
        gate = check_read_and_attention_gate(row, actor_side="subject", now_iso=NOW)
        self.assertEqual(gate["reason"], "WITHDRAWN")
        self.assertFalse(gate["allowed_read"])

    def test_received_unopened_exposes_metadata_only(self) -> None:
        row = {"author_side": "partner", "status": "RECEIVED_UNOPENED"}
        gate = check_read_and_attention_gate(row, actor_side="subject", now_iso=NOW)
        self.assertTrue(gate["sealed"])
        self.assertTrue(gate["allowed_read"])
        self.assertFalse(gate["allowed_body"])
        self.assertFalse(gate["allowed_attention"])
        self.assertFalse(gate["expose_raw_sources"])
        self.assertEqual(gate["reason"], "SEALED_UNOPENED")

    def test_explicit_sealed_and_envelope_sealed(self) -> None:
        explicit = {"author_side": "subject", "sealed": True}
        envelope_row = {
            "content_envelope": normalize_content_envelope(
                author_side="subject", sealed=True, visibility="shared"
            )
        }
        for row in (explicit, envelope_row):
            gate = check_read_and_attention_gate(row, actor_side="subject", now_iso=NOW)
            self.assertTrue(gate["sealed"])
            self.assertFalse(gate["allowed_body"])
            self.assertFalse(gate["allowed_attention"])


class LifecycleGateTests(unittest.TestCase):
    def test_consider_after_is_dormant_before_and_active_after(self) -> None:
        row = {
            "author_side": "partner",
            "visibility": "shared",
            "consider_after": "2026-11-16T12:30:00Z",
        }
        dormant = check_read_and_attention_gate(row, actor_side="subject", now_iso=BEFORE)
        active = check_read_and_attention_gate(row, actor_side="subject", now_iso=AFTER)

        self.assertTrue(dormant["dormant"])
        self.assertFalse(dormant["allowed_attention"])
        self.assertTrue(dormant["allowed_read"])
        self.assertTrue(dormant["allowed_body"])
        self.assertEqual(dormant["reason"], "DORMANT")
        self.assertFalse(dormant["overdue"])
        self.assertEqual(dormant["debt_score"], 0)

        self.assertFalse(active["dormant"])
        self.assertTrue(active["allowed_attention"])
        self.assertFalse(active["overdue"])
        self.assertEqual(active["debt_score"], 0)

    def test_surface_after_alias(self) -> None:
        row = {
            "author_side": "partner",
            "visibility": "shared",
            "surface_after": "2026-11-16T12:30:00Z",
        }
        gate = check_read_and_attention_gate(row, actor_side="subject", now_iso=BEFORE)
        self.assertTrue(gate["dormant"])
        self.assertFalse(gate["allowed_attention"])

    def test_expires_at_is_strict_and_not_renewed(self) -> None:
        row = {
            "author_side": "partner",
            "visibility": "shared",
            "expires_at": "2026-11-16T12:00:00Z",
        }
        before = check_read_and_attention_gate(
            row, actor_side="subject", now_iso="2026-11-16T11:59:59Z"
        )
        boundary = check_read_and_attention_gate(
            row, actor_side="subject", now_iso="2026-11-16T12:00:00Z"
        )
        after = check_read_and_attention_gate(
            row, actor_side="subject", now_iso="2026-11-16T12:00:01Z"
        )

        self.assertFalse(before["expired"])
        self.assertTrue(before["allowed_attention"])
        self.assertTrue(boundary["expired"])
        self.assertFalse(boundary["allowed_attention"])
        self.assertTrue(after["expired"])
        self.assertFalse(after["allowed_attention"])
        self.assertEqual(after["reason"], "EXPIRED")

    def test_active_now_false_is_expired(self) -> None:
        row = {"author_side": "partner", "visibility": "shared", "active_now": False}
        gate = check_read_and_attention_gate(row, actor_side="subject", now_iso=NOW)
        self.assertTrue(gate["expired"])
        self.assertFalse(gate["allowed_attention"])
        self.assertEqual(gate["reason"], "INACTIVE")
        self.assertFalse(gate["overdue"])
        self.assertEqual(gate["debt_score"], 0)

    def test_repeated_reads_never_mutate_or_renew(self) -> None:
        row: dict[str, Any] = {
            "author_side": "partner",
            "visibility": "shared",
            "consider_after": "2026-11-16T11:30:00Z",
            "expires_at": "2026-11-16T12:00:00Z",
            "text_cn": "正文",
        }
        snapshot = copy.deepcopy(row)
        serialized = json.dumps(row, sort_keys=True, ensure_ascii=False)

        for now in (BEFORE, NOW, AFTER, "2030-01-01T00:00:00Z"):
            for _ in range(3):
                check_read_and_attention_gate(row, actor_side="subject", now_iso=now)

        self.assertEqual(row, snapshot)
        self.assertEqual(json.dumps(row, sort_keys=True, ensure_ascii=False), serialized)
        self.assertEqual(row["expires_at"], "2026-11-16T12:00:00Z")
        self.assertEqual(row["consider_after"], "2026-11-16T11:30:00Z")


class ConcernRankingTests(unittest.TestCase):
    CONCERN = "咖啡的香气让她想起写作"

    @staticmethod
    def _shared(text: str, **extra: Any) -> dict[str, Any]:
        row = {"author_side": "partner", "visibility": "shared", "text_cn": text}
        row.update(extra)
        return row

    def test_withdrawn_never_ranks_even_on_exact_match(self) -> None:
        row = self._shared(self.CONCERN, withdrawn=True)
        self.assertEqual(
            rank_authorized_by_concern(
                [row], concern_text=self.CONCERN, actor_side="subject", now_iso=NOW
            ),
            [],
        )

    def test_received_unopened_never_ranks_and_body_is_not_read(self) -> None:
        sealed = {
            "author_side": "partner",
            "status": "RECEIVED_UNOPENED",
            "text_cn": self.CONCERN,
            "marker": "sealed",
        }
        open_row = self._shared(self.CONCERN, marker="open")
        read_markers: list[str] = []

        def text_fn(row: Any) -> str:
            read_markers.append(str(row.get("marker")))
            return str(row.get("text_cn") or "")

        hits = rank_authorized_by_concern(
            [sealed, open_row],
            concern_text=self.CONCERN,
            actor_side="subject",
            now_iso=NOW,
            text_fn=text_fn,
        )
        self.assertEqual([hit["marker"] for hit in hits], ["open"])
        self.assertNotIn("sealed", read_markers)

    def test_private_peer_row_never_ranks_even_on_exact_match(self) -> None:
        private = {
            "author_side": "partner",
            "visibility": "private",
            "text_cn": self.CONCERN,
        }
        shared = self._shared("只分享这句")
        hits = rank_authorized_by_concern(
            [private, shared],
            concern_text=self.CONCERN,
            actor_side="subject",
            now_iso=NOW,
        )
        self.assertEqual(hits, [])

    def test_private_author_row_also_never_ranks(self) -> None:
        private = {
            "author_side": "subject",
            "visibility": "private",
            "text_cn": self.CONCERN,
        }
        hits = rank_authorized_by_concern(
            [private], concern_text=self.CONCERN, actor_side="subject", now_iso=NOW
        )
        self.assertEqual(hits, [])

    def test_dormant_and_expired_rows_never_rank(self) -> None:
        dormant = self._shared(self.CONCERN, consider_after="2026-11-16T12:30:00Z")
        expired = self._shared(self.CONCERN, expires_at="2026-11-16T12:00:00Z")
        hits = rank_authorized_by_concern(
            [dormant, expired],
            concern_text=self.CONCERN,
            actor_side="subject",
            now_iso=NOW,
        )
        self.assertEqual(hits, [])

    def test_matching_shared_rows_rank_by_score_and_keep_input_order_on_ties(self) -> None:
        strong = self._shared(self.CONCERN, marker="strong")
        weak = self._shared("咖啡", marker="weak")
        tie_a = self._shared("咖啡香气", marker="tie_a")
        tie_b = self._shared("咖啡香气", marker="tie_b")
        unrelated = self._shared("天气不错", marker="unrelated")

        hits = rank_authorized_by_concern(
            [weak, tie_a, tie_b, unrelated, strong],
            concern_text=self.CONCERN,
            actor_side="subject",
            now_iso=NOW,
        )
        markers = [hit["marker"] for hit in hits]
        self.assertEqual(markers[0], "strong")
        self.assertNotIn("unrelated", markers)
        self.assertLess(markers.index("tie_a"), markers.index("tie_b"))
        for hit in hits:
            self.assertGreater(hit["_concern_score"], 0)

    def test_limit_is_respected_and_inputs_are_copied(self) -> None:
        rows = [self._shared(self.CONCERN, marker=str(i)) for i in range(6)]
        snapshot = copy.deepcopy(rows)
        hits = rank_authorized_by_concern(
            rows,
            concern_text=self.CONCERN,
            actor_side="subject",
            now_iso=NOW,
            limit=3,
        )
        self.assertEqual(len(hits), 3)
        self.assertEqual(rows, snapshot)
        self.assertNotIn("_concern_score", rows[0])

    def test_unconfirmed_idiom_ranks_for_both_sides(self) -> None:
        row = {
            "author_side": "partner",
            "visibility": "unconfirmed_idiom",
            "idiom_cn": self.CONCERN,
        }
        for actor in ("subject", "partner"):
            hits = rank_authorized_by_concern(
                [row], concern_text=self.CONCERN, actor_side=actor, now_iso=NOW
            )
            self.assertEqual(len(hits), 1)

    def test_empty_concern_or_zero_limit_returns_empty(self) -> None:
        row = self._shared(self.CONCERN)
        self.assertEqual(
            rank_authorized_by_concern([row], concern_text="  ", actor_side="subject"),
            [],
        )
        self.assertEqual(
            rank_authorized_by_concern(
                [row], concern_text=self.CONCERN, actor_side="subject", limit=0
            ),
            [],
        )

    def test_custom_text_fn_is_used(self) -> None:
        row = self._shared("mismatch", extra_note="自定义字段命中")
        hits = rank_authorized_by_concern(
            [row],
            concern_text="自定义字段",
            actor_side="subject",
            now_iso=NOW,
            text_fn=lambda item: str(item.get("extra_note") or ""),
        )
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["extra_note"], "自定义字段命中")


class ZeroLlmTests(unittest.TestCase):
    def test_module_imports_no_model_client(self) -> None:
        import scoped_content_record as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        self.assertNotIn("llm", source.replace("llm_calls", ""))
        self.assertNotIn("requests", source)
        self.assertNotIn("urllib", source)


if __name__ == "__main__":
    unittest.main()

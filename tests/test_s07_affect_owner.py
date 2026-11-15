#!/usr/bin/env python3
"""S07-AFFECT-OWNER-1 tests (added 2026-11-14).

Covers the minimal S07 affect owner (``components.life.scripts.s07_affect_owner``)
and its read-face hookup into ``short_appraisal_view``:

1. three closed record kinds (mood_shift / affect_pulse / drive), single-owner
   attribution, closed vocabulary
2. bounded quantized intensity (integer hundredths of [0,1]; floats refused)
3. mandatory structured source_refs
4. deterministic decay: linear (integer milli-per-hour) and exponential
   (integer half-life halving), zero float, zero RNG, replay-stable
5. natural expiry with zero tombstones (expired/faded records vanish from the
   read face; counts only)
6. forbidden vocabulary (intimacy_score / mood_delta smuggling, tone filters,
   body facts) refused; no model surface
7. UNKNOWN safety: no reference instant decides nothing; unresolvable subject
   attributes nothing
8. short_appraisal_view integration: the pinned public signature is unchanged,
   ``protocol.s07_owner`` stays None (the appraisal face engages no owner), the
   ``affect`` block carries the owner pointer, and with no records the view
   behaves exactly as before

Run:  python -m unittest tests.test_s07_affect_owner -v
"""

from __future__ import annotations

import ast
import inspect
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from components.life.scripts import (  # noqa: E402
    s07_affect_owner as s07,
    short_appraisal_view as sav,
)

SUBJECT = "chiyo.global"
NOW = "2026-11-14T10:00:00+00:00"
OBS = "2026-11-14T08:00:00+00:00"
EXP = "2026-11-14T20:00:00+00:00"


def _record(**overrides: object) -> dict:
    base: dict = {
        "record_id": "affect:1",
        "kind": "mood_shift",
        "intensity": 60,
        "source_refs": ["world:read:1"],
        "observed_at": OBS,
        "expires_at": EXP,
        "decay_per_hour_milli": 0,
    }
    base.update(overrides)
    return base


class S07AffectOwnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _view(self, records, now=NOW, subject=SUBJECT):
        return s07.build_s07_affect_view(home=self.home, subject_id=subject, now=now, records=records)

    # ------------------------------------------------------------------
    # typed kinds + owner attribution
    # ------------------------------------------------------------------

    def test_01_three_closed_kinds_and_owner_attribution(self) -> None:
        self.assertEqual(
            s07.AFFECT_RECORD_KINDS, ("mood_shift", "affect_pulse", "drive")
        )
        records = [
            s07.mint_affect_record(
                record_id=f"affect:{i}",
                kind=kind,
                subject_id=SUBJECT,
                intensity=40 + i,
                source_refs=["world:read:1"],
                observed_at=OBS,
                expires_at=EXP,
            )
            for i, kind in enumerate(s07.AFFECT_RECORD_KINDS)
        ]
        view = self._view(records)
        self.assertTrue(view["identity_bound"])
        self.assertEqual(view["owner_module"], "s07_affect_owner.v1")
        self.assertEqual(
            sorted(view["live_record_refs"]), ["affect:0", "affect:1", "affect:2"]
        )
        self.assertTrue(all(r["subject_id"] == SUBJECT for r in view["live_records"]))

        # kinds outside the closed vocabulary are refused
        bad_kind = self._view([_record(kind="vibe")])
        self.assertFalse(bad_kind["has_live_affect"])
        self.assertEqual(
            bad_kind["invalid_records"][0]["reason_code"], s07.REASON_AFFECT_KIND_INVALID
        )

        # another subject's record is never re-attributed
        foreign = self._view([_record(subject_id="someone.else")])
        self.assertFalse(foreign["has_live_affect"])
        self.assertEqual(
            foreign["invalid_records"][0]["reason_code"], s07.REASON_AFFECT_SUBJECT_MISMATCH
        )

    # ------------------------------------------------------------------
    # bounded quantized intensity
    # ------------------------------------------------------------------

    def test_02_intensity_is_bounded_and_quantized(self) -> None:
        for bad in (0.5, -1, 101, True, "60", None):
            with self.assertRaises(s07.S07AffectOwnerError):
                s07.mint_affect_record(
                    record_id="affect:bad",
                    kind="drive",
                    subject_id=SUBJECT,
                    intensity=bad,
                    source_refs=["world:read:1"],
                    observed_at=OBS,
                    expires_at=EXP,
                )
        # boundaries accepted
        for edge in (0, 100):
            rec = s07.mint_affect_record(
                record_id=f"affect:edge{edge}",
                kind="affect_pulse",
                subject_id=SUBJECT,
                intensity=edge,
                source_refs=["world:read:1"],
                observed_at=OBS,
                expires_at=EXP,
            )
            self.assertEqual(rec["intensity"], edge)

        # a raw record carrying a float intensity is refused by the projection too
        raw = _record(intensity=62.5)
        item = s07.classify_affect_record(raw, subject_id=SUBJECT, reference=datetime.now(timezone.utc))
        self.assertEqual(item["reason_code"], s07.REASON_AFFECT_INTENSITY_INVALID)

        # intensities are never aggregated across records
        view = self._view([_record(record_id="affect:a"), _record(record_id="affect:b")])
        self.assertEqual(view["intensity_aggregation"], "none")
        self.assertEqual(view["protocol"]["intensity_aggregation"], "none")

    # ------------------------------------------------------------------
    # mandatory sources
    # ------------------------------------------------------------------

    def test_03_source_refs_are_mandatory(self) -> None:
        for bad_refs in (None, [], [""], ["not a ref with spaces"], [123]):
            with self.assertRaises(s07.S07AffectOwnerError):
                s07.mint_affect_record(
                    record_id="affect:s",
                    kind="mood_shift",
                    subject_id=SUBJECT,
                    intensity=30,
                    source_refs=bad_refs,
                    observed_at=OBS,
                    expires_at=EXP,
                )
        raw = _record(source_refs=[])
        item = s07.classify_affect_record(raw, subject_id=SUBJECT, reference=datetime.now(timezone.utc))
        self.assertEqual(item["reason_code"], s07.REASON_AFFECT_SOURCE_REFS_MISSING)

    # ------------------------------------------------------------------
    # deterministic decay (linear + exponential), zero float, zero RNG
    # ------------------------------------------------------------------

    def test_04_linear_decay_is_exact_integer_math(self) -> None:
        long_exp = (datetime.fromisoformat(OBS) + timedelta(hours=48)).isoformat()
        rec = s07.mint_affect_record(
            record_id="affect:lin",
            kind="mood_shift",
            subject_id=SUBJECT,
            intensity=60,
            source_refs=["world:read:1"],
            observed_at=OBS,
            expires_at=long_exp,
            decay_per_hour_milli=5000,  # 5 hundredths of [0,1] per hour
        )
        self.assertEqual(rec["decay_mode"], "linear")
        self.assertEqual(rec["decay_per_hour_milli"], 5000)

        # +2h: 60 - 2*5 = 50
        t2 = (datetime.fromisoformat(OBS) + timedelta(hours=2)).isoformat()
        item = s07.classify_affect_record(rec, subject_id=SUBJECT, reference=datetime.fromisoformat(t2))
        self.assertEqual(item["status"], "LIVE")
        self.assertEqual(item["effective_intensity"], 50)

        # same inputs -> same outputs (replay determinism)
        item_again = s07.classify_affect_record(rec, subject_id=SUBJECT, reference=datetime.fromisoformat(t2))
        self.assertEqual(item_again["effective_intensity"], item["effective_intensity"])

        # +13h: fully faded (60 - 65 <= 0), still inside the expiry window
        t13 = (datetime.fromisoformat(OBS) + timedelta(hours=13)).isoformat()
        item13 = s07.classify_affect_record(rec, subject_id=SUBJECT, reference=datetime.fromisoformat(t13))
        self.assertEqual(item13["status"], "FADED")
        self.assertEqual(item13["effective_intensity"], 0)

        # intensity 0 record: faded immediately at any later reference
        zero = s07.mint_affect_record(
            record_id="affect:zero",
            kind="affect_pulse",
            subject_id=SUBJECT,
            intensity=0,
            source_refs=["world:read:1"],
            observed_at=OBS,
            expires_at=EXP,
        )
        item0 = s07.classify_affect_record(zero, subject_id=SUBJECT, reference=datetime.fromisoformat(NOW))
        self.assertEqual(item0["status"], "FADED")

    def test_05_exponential_decay_halves_per_half_life(self) -> None:
        long_exp = (datetime.fromisoformat(OBS) + timedelta(days=100)).isoformat()
        rec = s07.mint_affect_record(
            record_id="affect:exp",
            kind="drive",
            subject_id=SUBJECT,
            intensity=64,
            source_refs=["world:read:1"],
            observed_at=OBS,
            expires_at=long_exp,
            decay_mode="exponential",
            decay_half_life_seconds=3600,
        )
        # +1h -> 32, +2h -> 16, +3h -> 8 (integer halving)
        for hours, expected in ((1, 32), (2, 16), (3, 8)):
            t = (datetime.fromisoformat(OBS) + timedelta(hours=hours)).isoformat()
            item = s07.classify_affect_record(rec, subject_id=SUBJECT, reference=datetime.fromisoformat(t))
            self.assertEqual(item["effective_intensity"], expected, f"at +{hours}h")
        # beyond the closed 64-shift bound: zero
        t99 = (datetime.fromisoformat(OBS) + timedelta(days=30)).isoformat()
        item99 = s07.classify_affect_record(rec, subject_id=SUBJECT, reference=datetime.fromisoformat(t99))
        self.assertEqual(item99["status"], "FADED")

        # mixing decay params is refused
        with self.assertRaises(s07.S07AffectOwnerError):
            s07.mint_affect_record(
                record_id="affect:mix",
                kind="drive",
                subject_id=SUBJECT,
                intensity=10,
                source_refs=["world:read:1"],
                observed_at=OBS,
                expires_at=EXP,
                decay_mode="exponential",
                decay_half_life_seconds=3600,
                decay_per_hour_milli=100,
            )
        with self.assertRaises(s07.S07AffectOwnerError):
            s07.mint_affect_record(
                record_id="affect:exp0",
                kind="drive",
                subject_id=SUBJECT,
                intensity=10,
                source_refs=["world:read:1"],
                observed_at=OBS,
                expires_at=EXP,
                decay_mode="exponential",
                decay_half_life_seconds=0,
            )

    # ------------------------------------------------------------------
    # natural expiry, zero tombstones
    # ------------------------------------------------------------------

    def test_06_expiry_is_mandatory_and_vanishes_silently(self) -> None:
        with self.assertRaises(s07.S07AffectOwnerError):
            s07.mint_affect_record(
                record_id="affect:noexp",
                kind="mood_shift",
                subject_id=SUBJECT,
                intensity=20,
                source_refs=["world:read:1"],
                observed_at=OBS,
                expires_at=None,
            )
        # inverted window refused
        with self.assertRaises(s07.S07AffectOwnerError):
            s07.mint_affect_record(
                record_id="affect:inv",
                kind="mood_shift",
                subject_id=SUBJECT,
                intensity=20,
                source_refs=["world:read:1"],
                observed_at=EXP,
                expires_at=OBS,
            )

        live = self._view([_record()])
        self.assertTrue(live["has_live_affect"])
        self.assertEqual(live["expired_count"], 0)

        # after expiry the record is unreadable: counts only, no tombstone payload
        after = (datetime.fromisoformat(EXP) + timedelta(minutes=1)).isoformat()
        view = self._view([_record()], now=after)
        self.assertFalse(view["has_live_affect"])
        self.assertEqual(view["expired_count"], 1)
        self.assertEqual(view["records"], [])
        self.assertEqual(view["live_records"], [])
        self.assertTrue(view["zero_tombstone"])
        self.assertTrue(view["protocol"]["zero_tombstone_on_expiry"])

    # ------------------------------------------------------------------
    # forbidden vocabulary & no-model surface
    # ------------------------------------------------------------------

    def test_07_forbidden_vocabulary_and_no_model(self) -> None:
        # the mint path cannot produce smuggled keys at all (closed parameters)
        rec = s07.mint_affect_record(
            record_id="affect:clean",
            kind="affect_pulse",
            subject_id=SUBJECT,
            intensity=10,
            source_refs=["world:read:1"],
            observed_at=OBS,
            expires_at=EXP,
        )
        self.assertNotIn("intimacy_score", rec)
        self.assertNotIn("mood_delta", rec)

        # raw records carrying smuggled / tone / body keys are refused per reason
        for key, expected_reason in (
            ("intimacy_score", s07.REASON_AFFECT_SMUGGLING_REFUSED),
            ("mood_delta", s07.REASON_AFFECT_SMUGGLING_REFUSED),
            ("affection_score", s07.REASON_AFFECT_SMUGGLING_REFUSED),
            ("tone", s07.REASON_AFFECT_TONE_INFERENCE_REFUSED),
            ("transcript", s07.REASON_AFFECT_TONE_INFERENCE_REFUSED),
            ("fatigue", s07.REASON_AFFECT_BODY_FACT_REFUSED),
        ):
            item = s07.classify_affect_record(
                _record(**{key: 1}), subject_id=SUBJECT, reference=datetime.now(timezone.utc)
            )
            self.assertEqual(item["reason_code"], expected_reason, key)
            self.assertIn(key, item["refused_fields"])

        # the forbidden vocabulary is declared and closed
        self.assertIn("intimacy_score", s07.FORBIDDEN_AFFECT_FIELDS)
        self.assertIn("mood_delta", s07.FORBIDDEN_AFFECT_FIELDS)

        # no model surface anywhere in the module
        module_path = REPO_ROOT / "components" / "life" / "scripts" / "s07_affect_owner.py"
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertTrue(
            imported.isdisjoint({"random", "secrets", "openai", "anthropic", "httpx", "requests"}),
            f"forbidden imports: {imported & {'random', 'secrets', 'openai', 'anthropic', 'httpx', 'requests'}}",
        )
        view = self._view([_record()])
        self.assertEqual(view["protocol"]["llm_calls"], 0)
        self.assertFalse(view["protocol"]["model_called"])

    # ------------------------------------------------------------------
    # UNKNOWN safety
    # ------------------------------------------------------------------

    def test_08_unknown_safety_no_reference_and_unbound_subject(self) -> None:
        # without a reference instant nothing is decided and nothing is live
        undecided = self._view([_record()], now=None)
        self.assertFalse(undecided["has_live_affect"])
        self.assertIsNone(undecided["observed_at"])
        self.assertEqual(
            undecided["invalid_records"][0]["reason_code"], s07.REASON_AFFECT_NO_REFERENCE_INSTANT
        )

        # a non-mapping record is refused, not guessed
        junk = self._view(["not-a-record"])
        self.assertEqual(
            junk["invalid_records"][0]["reason_code"], s07.REASON_AFFECT_NOT_MAPPING
        )

        # undeclared uncertainty reads as UNKNOWN, never as invented confidence
        item = s07.classify_affect_record(_record(), subject_id=SUBJECT, reference=datetime.fromisoformat(NOW))
        self.assertEqual(item["uncertainty_source"], "UNKNOWN")
        declared = s07.classify_affect_record(
            _record(uncertainty="fading_fast"), subject_id=SUBJECT, reference=datetime.fromisoformat(NOW)
        )
        self.assertEqual(declared["uncertainty_source"], "declared")

        # an illegal subject is a caller fault: raised, never guessed into a view
        with self.assertRaises(s07.S07AffectOwnerError):
            self._view([_record()], subject="someone.else")

    # ------------------------------------------------------------------
    # short_appraisal_view hookup (frozen public signature, owner pointer)
    # ------------------------------------------------------------------

    def test_09_short_appraisal_view_backward_compatible_and_owner_pointer(self) -> None:
        # the pinned public signature is unchanged
        parameters = inspect.signature(sav.build_short_appraisal_view).parameters
        self.assertEqual(
            set(parameters),
            {"home", "subject_id", "now", "appraisals", "corrections", "lost_source_refs"},
        )

        # no records: behaviour identical, affect block carries only the pointer
        plain = sav.build_short_appraisal_view(home=self.home, subject_id=SUBJECT, now=NOW)
        self.assertEqual(plain["valid_appraisal_refs"], [])
        self.assertEqual(plain["empty_reason_code"], sav.REASON_NO_APPRAISAL_INPUT)
        self.assertIsNone(plain["protocol"]["s07_owner"])
        self.assertFalse(plain["affect"]["engaged"])
        self.assertFalse(plain["affect"]["has_live_affect"])
        self.assertEqual(plain["affect"]["owner_module"], "s07_affect_owner.v1")
        self.assertEqual(plain["affect"]["record_kinds"], ["mood_shift", "affect_pulse", "drive"])

        # with records (owner version): reached via ShortAppraisalView
        rec = s07.mint_affect_record(
            record_id="affect:hook",
            kind="mood_shift",
            subject_id=SUBJECT,
            intensity=55,
            source_refs=["world:read:9"],
            observed_at=OBS,
            expires_at=EXP,
            decay_per_hour_milli=0,
        )
        projector = sav.ShortAppraisalView(home=self.home, subject_id=SUBJECT)
        hooked = projector.build(now=NOW, affect_records=[rec])
        self.assertTrue(hooked["affect"]["engaged"])
        self.assertTrue(hooked["affect"]["has_live_affect"])
        self.assertEqual(hooked["affect"]["live_record_refs"], ["affect:hook"])
        self.assertEqual(hooked["affect"]["live_records"][0]["effective_intensity"], 55)
        # the appraisal face itself is untouched
        self.assertEqual(hooked["valid_appraisal_refs"], [])
        self.assertIsNone(hooked["protocol"]["s07_owner"])

        # per-instance fixed records also work
        fixed = sav.ShortAppraisalView(home=self.home, subject_id=SUBJECT, affect_records=[rec])
        self.assertTrue(fixed.build(now=NOW)["affect"]["has_live_affect"])
        self.assertFalse(
            fixed.build(now=NOW, affect_records=[])["affect"]["has_live_affect"]
        )

        # expired affect records surface as counts only through the hookup
        expired = s07.mint_affect_record(
            record_id="affect:gone",
            kind="drive",
            subject_id=SUBJECT,
            intensity=90,
            source_refs=["world:read:2"],
            observed_at="2026-11-13T08:00:00+00:00",
            expires_at="2026-11-13T09:00:00+00:00",
        )
        gone = sav.ShortAppraisalView(home=self.home, subject_id=SUBJECT).build(
            now=NOW, affect_records=[expired]
        )
        self.assertFalse(gone["affect"]["has_live_affect"])
        self.assertEqual(gone["affect"]["expired_count"], 1)
        self.assertEqual(gone["affect"]["live_records"], [])

    # ------------------------------------------------------------------
    # no store, no writes
    # ------------------------------------------------------------------

    def test_10_owner_keeps_no_store_and_writes_nothing(self) -> None:
        rec = s07.mint_affect_record(
            record_id="affect:pure",
            kind="mood_shift",
            subject_id=SUBJECT,
            intensity=25,
            source_refs=["world:read:1"],
            observed_at=OBS,
            expires_at=EXP,
        )
        before = sorted(p.name for p in self.home.rglob("*")) if self.home.exists() else []
        self._view([rec])
        after = sorted(p.name for p in self.home.rglob("*")) if self.home.exists() else []
        self.assertEqual(before, after)
        view = self._view([rec])
        self.assertEqual(view["reads_performed"], 0)
        self.assertEqual(view["writes_performed"], 0)
        self.assertIsNone(view["protocol"]["persistent_store"])
        self.assertTrue(view["protocol"]["rebuildable"])


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""AFFECT/SHORT-APPRAISAL-1 tests: the bounded, sourced short-appraisal projection.

This module has no owner to read (there is no S07 affect owner in the tree), so
the tests are about the *protocol*: sources, a declared validity window,
corrections, lost sources, no stacking, no tone inference, no stable fact, no
store and no Candidate.  Nothing here claims a live model, a real deployment or
a psychological engine.

Run:  python3 tests/test_short_appraisal_view.py
"""

from __future__ import annotations

import inspect
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIFE_SCRIPTS = ROOT / "components" / "life" / "scripts"
sys.path[:0] = [str(ROOT), str(LIFE_SCRIPTS)]

import body_capacity_source as bcs  # noqa: E402
import short_appraisal_view as sav  # noqa: E402

#: The session's native fixture root (unique per work package).  Temporary dirs go
#: under it when it exists; nothing pre-existing is ever removed.
NATIVE_FIXTURE_ROOT = Path("/tmp/nyaw05r.jdK3q")

SUBJECT = "chiyo.global"
NOW = "2026-10-16T06:00:00+00:00"


def _fixture_dir(prefix: str) -> tempfile.TemporaryDirectory:
    base = NATIVE_FIXTURE_ROOT if NATIVE_FIXTURE_ROOT.is_dir() else None
    return tempfile.TemporaryDirectory(prefix=prefix, dir=str(base) if base else None)


class AppraisalTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._dirs: list[tempfile.TemporaryDirectory] = []

    def tearDown(self) -> None:
        for handle in self._dirs:
            handle.cleanup()

    def make_home(self) -> Path:
        handle = _fixture_dir("a-appraisal-home-")
        self._dirs.append(handle)
        return Path(handle.name)

    def appraisal(self, ref="appraisal:1", **overrides):
        item = {
            "appraisal_ref": ref,
            "source_refs": ["world:read:1"],
            "observed_at": "2026-10-16T05:00:00+00:00",
            "valid_until": "2026-10-16T07:00:00+00:00",
        }
        item.update(overrides)
        return item

    def build(self, appraisals=(), *, now=NOW, corrections=(), lost_source_refs=(), home=None):
        return sav.build_short_appraisal_view(
            home=self.make_home() if home is None else home,
            now=now,
            appraisals=appraisals,
            corrections=corrections,
            lost_source_refs=lost_source_refs,
        )

    def item(self, view, index=0):
        return view["appraisals"][index]


# ---------------------------------------------------------------------------
# 1. the one valid shape
# ---------------------------------------------------------------------------


class ValidShape(AppraisalTestCase):
    def test_a_sourced_bounded_appraisal_is_valid(self):
        view = self.build([self.appraisal(uncertainty="LOW", declared_scope="this reading")])

        item = self.item(view)
        self.assertEqual(view["schema_version"], sav.SCHEMA_SHORT_APPRAISAL_VIEW)
        self.assertEqual(item["status"], sav.APPRAISAL_STATUS_VALID)
        self.assertEqual(item["reason_code"], sav.REASON_OK_SOURCED_APPRAISAL)
        self.assertEqual(item["source_refs"], ["world:read:1"])
        self.assertEqual(item["subject_id"], SUBJECT)
        self.assertEqual(item["observed_at"], "2026-10-16T05:00:00+00:00")
        self.assertEqual(item["valid_until"], "2026-10-16T07:00:00+00:00")
        self.assertEqual(item["uncertainty"], "LOW")
        self.assertEqual(item["uncertainty_source"], "declared")
        self.assertEqual(item["declared_scope"], "this reading")
        self.assertEqual(view["valid_appraisal_refs"], ["appraisal:1"])
        self.assertTrue(view["has_valid_appraisal"])
        self.assertIsNone(view["empty_reason_code"])
        self.assertTrue(view["identity_bound"])
        self.assertEqual(view["subject_id"], SUBJECT)

    def test_an_undeclared_uncertainty_is_unknown_not_invented(self):
        view = self.build([self.appraisal()])

        self.assertEqual(self.item(view)["uncertainty"], None)
        self.assertEqual(self.item(view)["uncertainty_source"], "UNKNOWN")

    def test_the_same_input_rebuilds_the_same_view(self):
        source = sav.ShortAppraisalView(home=self.make_home())
        appraisals = [self.appraisal()]

        first = source.build(now=NOW, appraisals=appraisals)
        second = source.build(now=NOW, appraisals=appraisals)

        self.assertEqual(first, second)
        self.assertEqual(source.last_view, second)
        self.assertTrue(first["protocol"]["rebuildable"])
        self.assertIsNone(first["protocol"]["persistent_store"])
        self.assertFalse(first["protocol"]["canonical"])


# ---------------------------------------------------------------------------
# 2. no valid appraisal after loss, expiry, correction or missing structure
# ---------------------------------------------------------------------------


class NoValidAppraisal(AppraisalTestCase):
    def test_expiry_leaves_no_valid_appraisal(self):
        view = self.build([self.appraisal()], now="2026-10-16T07:00:00+00:00")

        self.assertEqual(self.item(view)["status"], sav.APPRAISAL_STATUS_INVALID)
        self.assertEqual(self.item(view)["reason_code"], sav.REASON_APPRAISAL_EXPIRED)
        self.assertEqual(view["valid_appraisal_refs"], [])
        self.assertFalse(view["has_valid_appraisal"])
        self.assertEqual(view["empty_reason_code"], sav.REASON_APPRAISAL_EXPIRED)

    def test_without_a_reference_instant_there_is_no_valid_appraisal(self):
        view = self.build([self.appraisal()], now=None)

        self.assertEqual(
            self.item(view)["reason_code"], sav.REASON_APPRAISAL_NO_REFERENCE_INSTANT
        )
        self.assertFalse(view["has_valid_appraisal"])
        self.assertIsNone(view["observed_at"])

    def test_no_input_at_all_is_an_explicit_empty(self):
        view = self.build([])

        self.assertEqual(view["appraisals"], [])
        self.assertFalse(view["has_valid_appraisal"])
        self.assertEqual(view["empty_reason_code"], sav.REASON_NO_APPRAISAL_INPUT)

    def test_the_bounded_window_is_required(self):
        cases = {
            "valid_until": sav.REASON_APPRAISAL_VALID_UNTIL_MISSING,
            "valid_until_invalid": sav.REASON_APPRAISAL_VALID_UNTIL_INVALID,
            "validity_inverted": sav.REASON_APPRAISAL_VALIDITY_INVERTED,
            "observed_at": sav.REASON_APPRAISAL_OBSERVED_AT_MISSING,
            "observed_at_invalid": sav.REASON_APPRAISAL_OBSERVED_AT_INVALID,
            "source_refs": sav.REASON_APPRAISAL_SOURCE_REFS_MISSING,
            "appraisal_ref": sav.REASON_APPRAISAL_REF_MISSING,
        }
        broken = {
            "valid_until": self.appraisal(valid_until=None),
            "valid_until_invalid": self.appraisal(valid_until="tomorrow-ish"),
            "validity_inverted": self.appraisal(valid_until="2026-10-16T04:00:00+00:00"),
            "observed_at": self.appraisal(observed_at=None),
            "observed_at_invalid": self.appraisal(observed_at="yesterday"),
            "source_refs": self.appraisal(source_refs=[]),
            "appraisal_ref": self.appraisal(appraisal_ref=None),
        }

        for name, raw in broken.items():
            view = self.build([raw])
            self.assertEqual(self.item(view)["reason_code"], cases[name], name)
            self.assertFalse(view["has_valid_appraisal"], name)

    def test_a_non_mapping_and_a_duplicate_ref_are_refused(self):
        view = self.build([self.appraisal(), "not-a-mapping", self.appraisal()])

        self.assertEqual(self.item(view, 0)["reason_code"], sav.REASON_OK_SOURCED_APPRAISAL)
        self.assertEqual(self.item(view, 1)["reason_code"], sav.REASON_APPRAISAL_NOT_MAPPING)
        self.assertEqual(self.item(view, 2)["reason_code"], sav.REASON_APPRAISAL_DUPLICATE_REF)
        self.assertEqual(view["valid_appraisal_refs"], ["appraisal:1"])

    def test_an_observation_from_the_future_is_refused(self):
        view = self.build(
            [self.appraisal(observed_at="2026-10-16T09:00:00+00:00",
                            valid_until="2026-10-16T10:00:00+00:00")]
        )

        self.assertEqual(self.item(view)["reason_code"], sav.REASON_APPRAISAL_OBSERVED_AT_AHEAD)
        self.assertFalse(view["has_valid_appraisal"])

    def test_a_lost_source_leaves_no_valid_appraisal(self):
        view = self.build(
            [self.appraisal(), self.appraisal("appraisal:2", source_refs=["world:read:2"])],
            lost_source_refs=["world:read:1"],
        )

        self.assertEqual(self.item(view, 0)["reason_code"], sav.REASON_APPRAISAL_SOURCE_LOST)
        self.assertEqual(self.item(view, 0)["lost_source_refs"], ["world:read:1"])
        self.assertEqual(self.item(view, 1)["status"], sav.APPRAISAL_STATUS_VALID)
        self.assertEqual(view["valid_appraisal_refs"], ["appraisal:2"])
        self.assertEqual(view["lost_source_refs"], ["world:read:1"])

    def test_a_valid_correction_leaves_no_valid_appraisal(self):
        view = self.build(
            [self.appraisal()],
            corrections=[{"correction_ref": "correction:1",
                          "corrects_refs": ["appraisal:1"],
                          "source_ref": "world:read:2",
                          "observed_at": "2026-10-16T05:30:00+00:00"}],
        )

        self.assertEqual(self.item(view)["reason_code"], sav.REASON_APPRAISAL_CORRECTED)
        self.assertFalse(view["has_valid_appraisal"])
        self.assertEqual(view["source_revisions"]["corrections"], ["correction:1"])
        self.assertEqual(view["invalid_corrections"], [])

    def test_a_correction_of_a_source_also_invalidates_the_appraisal(self):
        view = self.build(
            [self.appraisal()],
            corrections=[{"correction_ref": "correction:1", "corrects_refs": ["world:read:1"]}],
        )

        self.assertEqual(self.item(view)["reason_code"], sav.REASON_APPRAISAL_CORRECTED)
        self.assertEqual(self.item(view)["corrected_source_refs"], ["world:read:1"])

    def test_an_anonymous_correction_cannot_erase_anything(self):
        view = self.build(
            [self.appraisal()],
            corrections=[
                {"corrects_refs": ["appraisal:1"]},
                {"correction_ref": "correction:2"},
                "not-a-mapping",
            ],
        )

        self.assertEqual(self.item(view)["status"], sav.APPRAISAL_STATUS_VALID)
        reasons = [entry["reason_code"] for entry in view["invalid_corrections"]]
        self.assertEqual(reasons, [sav.REASON_CORRECTION_REF_MISSING,
                                   sav.REASON_CORRECTION_TARGETS_MISSING,
                                   sav.REASON_CORRECTION_REF_MISSING])
        self.assertEqual(view["corrections"], [])

    def test_an_appraisal_may_declare_its_own_correction(self):
        view = self.build([self.appraisal(correction_refs=["correction:9"])])

        self.assertEqual(self.item(view)["reason_code"], sav.REASON_APPRAISAL_CORRECTED)
        self.assertEqual(self.item(view)["correction_refs"], ["correction:9"])


# ---------------------------------------------------------------------------
# 3. repeated sources do not stack
# ---------------------------------------------------------------------------


class NoStacking(AppraisalTestCase):
    def test_the_same_source_is_counted_once_with_one_intensity(self):
        view = self.build([
            self.appraisal("appraisal:1", intensity="mild"),
            self.appraisal("appraisal:2", source_refs=["world:read:1", "world:read:1"],
                           intensity="mild"),
        ])

        self.assertEqual(view["source_intensity"], {"world:read:1": "mild"})
        self.assertEqual(view["conflicting_source_refs"], [])
        self.assertEqual(view["intensity_aggregation"], "none")
        self.assertEqual(self.item(view, 1)["source_refs"], ["world:read:1"])
        self.assertEqual(self.item(view, 1)["duplicate_source_refs"], ["world:read:1"])
        # two appraisals over one source do not add up to anything
        self.assertNotEqual(view["source_intensity"]["world:read:1"], "mildmild")

    def test_conflicting_intensity_is_withheld_not_averaged(self):
        view = self.build([
            self.appraisal("appraisal:1", intensity="mild"),
            self.appraisal("appraisal:2", intensity="strong"),
        ])

        self.assertEqual(view["source_intensity"], {})
        self.assertEqual(view["conflicting_source_refs"], ["world:read:1"])
        self.assertEqual(view["intensity_aggregation"], "none")
        # the appraisals themselves are still valid: only the magnitude is withheld
        self.assertEqual(view["valid_appraisal_refs"], ["appraisal:1", "appraisal:2"])


# ---------------------------------------------------------------------------
# 4. no tone inference and no self-signed stable fact
# ---------------------------------------------------------------------------


class NoInference(AppraisalTestCase):
    def test_tone_words_cannot_become_an_appraisal(self):
        for field in ("tone", "sentiment", "mood", "emotion", "affect", "feeling",
                      "text", "message", "transcript", "utterance", "reply", "words"):
            view = self.build([self.appraisal(**{field: "she sounded pleased"})])
            self.assertEqual(
                self.item(view)["reason_code"], sav.REASON_APPRAISAL_TONE_INFERENCE_REFUSED, field
            )
            self.assertEqual(self.item(view)["refused_fields"], [field], field)
            self.assertFalse(view["has_valid_appraisal"], field)

    def test_an_item_with_only_tone_and_no_source_is_refused_either_way(self):
        view = self.build([{"appraisal_ref": "appraisal:tone", "tone": "warm",
                            "observed_at": "2026-10-16T05:00:00+00:00",
                            "valid_until": "2026-10-16T07:00:00+00:00"}])

        self.assertEqual(
            self.item(view)["reason_code"], sav.REASON_APPRAISAL_TONE_INFERENCE_REFUSED
        )
        self.assertFalse(view["has_valid_appraisal"])

    def test_stable_facts_and_body_facts_are_refused(self):
        for field in ("stable_emotion", "relationship", "preference", "personality",
                      "self_belief", "identity"):
            view = self.build([self.appraisal(**{field: "settled"})])
            self.assertEqual(
                self.item(view)["reason_code"], sav.REASON_APPRAISAL_STABLE_FACT_REFUSED, field
            )
        for field in ("body_capacity_state", "body_capacity_ref", "fatigue", "health"):
            view = self.build([self.appraisal(**{field: "LIMITED"})])
            self.assertEqual(
                self.item(view)["reason_code"], sav.REASON_APPRAISAL_BODY_FACT_REFUSED, field
            )

    def test_the_view_never_signs_a_stable_fact_or_a_body_fact(self):
        view = self.build([self.appraisal(uncertainty="LOW", intensity="mild")])

        protocol = view["protocol"]
        self.assertFalse(protocol["stable_emotion_asserted"])
        self.assertFalse(protocol["relationship_fact_asserted"])
        self.assertFalse(protocol["preference_fact_asserted"])
        self.assertFalse(protocol["body_fact_asserted"])
        self.assertFalse(protocol["tone_inference_used"])
        self.assertIsNone(protocol["s07_owner"])
        self.assertEqual(protocol["kind"], "experimental_rebuildable_projection")
        # an item carries no field that could hold a stable emotion, a
        # relationship fact, a preference or a body fact
        self.assertEqual(
            set(self.item(view)),
            {"appraisal_ref", "subject_id", "source_refs", "duplicate_source_refs",
             "observed_at", "valid_until", "declared_scope", "uncertainty",
             "uncertainty_source", "intensity", "correction_refs", "corrected_source_refs",
             "lost_source_refs", "status", "reason_code"},
        )


# ---------------------------------------------------------------------------
# 5. identity, home and caller faults
# ---------------------------------------------------------------------------


class IdentityAndFaults(AppraisalTestCase):
    def test_an_appraisal_for_another_subject_is_refused(self):
        view = self.build([self.appraisal(subject_id="someone.else")])

        self.assertEqual(self.item(view)["reason_code"], sav.REASON_APPRAISAL_SUBJECT_MISMATCH)
        self.assertFalse(view["has_valid_appraisal"])

    def test_the_canonical_subject_is_accepted(self):
        view = self.build([self.appraisal(subject_id=SUBJECT)])

        self.assertEqual(self.item(view)["subject_id"], SUBJECT)
        self.assertEqual(self.item(view)["status"], sav.APPRAISAL_STATUS_VALID)

    def test_an_illegal_subject_or_home_is_a_caller_fault(self):
        home = self.make_home()

        with self.assertRaises(sav.ShortAppraisalViewError):
            sav.build_short_appraisal_view(home=home, subject_id="someone.else")
        with self.assertRaises(sav.ShortAppraisalViewError):
            sav.build_short_appraisal_view(home=home, now="not-a-time")
        with self.assertRaises(sav.ShortAppraisalViewError):
            sav.ShortAppraisalView(home=home, subject_id="someone.else")
        with self.assertRaises(sav.ShortAppraisalViewError):
            sav.ShortAppraisalView(home="/nonexistent/instance-home")
        with self.assertRaises(sav.ShortAppraisalViewError):
            sav.valid_appraisals({"nope": 1})

    def test_an_unusable_home_binds_nothing(self):
        view = sav.build_short_appraisal_view(
            home="/nonexistent/instance-home", now=NOW, appraisals=[self.appraisal()]
        )

        self.assertFalse(view["identity_bound"])
        self.assertIsNone(view["home_ref"])
        self.assertEqual(view["empty_reason_code"], bcs.REASON_HOME_UNBOUND)
        self.assertEqual(view["appraisals"], [])

    def test_an_unavailable_subject_owner_binds_nothing(self):
        saved = sys.modules.get("activity_continuity")
        try:
            sys.modules["activity_continuity"] = None
            view = sav.build_short_appraisal_view(
                home=self.make_home(), now=NOW, appraisals=[self.appraisal()]
            )
        finally:
            if saved is None:
                sys.modules.pop("activity_continuity", None)
            else:
                sys.modules["activity_continuity"] = saved

        self.assertFalse(view["identity_bound"])
        self.assertEqual(
            view["empty_reason_code"], bcs.REASON_SUBJECT_OWNER_UNAVAILABLE
        )
        self.assertFalse(view["has_valid_appraisal"])

    def test_valid_appraisals_returns_only_the_living_items(self):
        view = self.build([
            self.appraisal("appraisal:1"),
            self.appraisal("appraisal:2", valid_until="2026-10-16T05:30:00+00:00"),
        ])

        items = sav.valid_appraisals(view)

        self.assertEqual([item["appraisal_ref"] for item in items], ["appraisal:1"])
        self.assertEqual(view["invalid_appraisals"],
                         [{"appraisal_ref": "appraisal:2",
                           "reason_code": sav.REASON_APPRAISAL_EXPIRED}])


# ---------------------------------------------------------------------------
# 6. zero authority: no store, no Candidate, no writes, no budget
# ---------------------------------------------------------------------------


class ZeroAuthority(AppraisalTestCase):
    def test_the_view_claims_no_authority(self):
        view = self.build([self.appraisal()])

        self.assertTrue(view["read_only"])
        self.assertEqual(view["reads_performed"], 0)
        self.assertEqual(view["writes_performed"], 0)
        self.assertEqual(view["mutations_attempted"], 0)
        self.assertEqual(view["candidates_emitted"], 0)
        self.assertEqual(view["actions_authorized"], 0)

    def test_the_module_has_no_store_or_authority_surface(self):
        # the docstring is prose (it names what the module must never do)
        code = inspect.getsource(sav).split('"""', 2)[2]
        for forbidden in ("open(", "write_text", "write_bytes", "json.dump", "sqlite3",
                          "shutil", "os.remove", "os.replace", "mkdir", "os.makedirs",
                          "import agency_decision_ag1", "import candidate_sources_ag0",
                          "from agency_decision_ag1", "from candidate_sources_ag0"):
            self.assertNotIn(forbidden, code, forbidden)
        for name in dir(sav):
            if name.startswith("_"):
                continue
            self.assertNotRegex(
                name.lower(),
                r"adopt|submit|authoriz|authoris|dispatch|persist|store|save|write",
                f"public name {name!r} looks like a store/authority verb",
            )
        parameters = inspect.signature(sav.build_short_appraisal_view).parameters
        self.assertEqual(
            set(parameters),
            {"home", "subject_id", "now", "appraisals", "corrections", "lost_source_refs"},
        )

    def test_a_build_writes_nothing_to_the_home(self):
        home = self.make_home()
        (home / "state").mkdir()
        (home / "state" / "life.json").write_text('{"revision": 3}', encoding="utf-8")
        before = sorted(
            (str(path.relative_to(home)), path.stat().st_size) for path in home.rglob("*")
        )

        for _ in range(3):
            view = self.build([self.appraisal()], home=home)

        after = sorted(
            (str(path.relative_to(home)), path.stat().st_size) for path in home.rglob("*")
        )
        self.assertEqual(after, before)
        self.assertEqual(view["writes_performed"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

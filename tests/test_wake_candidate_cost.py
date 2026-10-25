#!/usr/bin/env python3
"""Wake cost: round-scoped owner-read memo, candidate equivalence, honest stats.

The memo is tested at the transport hook it actually changes (``LifeArtifactPort._call``)
with a fake owner; the candidate equivalence test runs the real
``ArtifactContinuationSource`` + ``CandidateMaterializer`` on both the raw and the
memoized port.  The stats tests build a synthetic *record* root (no store, no service)
and check the counting rules that matter: rule-resolved rounds are 0 model calls, the
ledger is not mixed with wake rows, movement is never invented as zero, and a repeated
read-surface counter is not reported as the unique record count.

No model, no network, no Life Supply service, 0 product calls.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"), str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import artifact_continuation_source as acs  # noqa: E402
import candidate_sources_ag0 as ag0  # noqa: E402
import wake_behavior_stats as stats  # noqa: E402
from life_artifact_port import ArtifactOwnerRefused, LifeArtifactPort  # noqa: E402
from wake_candidate_cost import (  # noqa: E402
    RoundCachedArtifactPort,
    maybe_round_cached_port,
)

SUBJECT = "chiyo"
OBSERVED_AT = "2026-10-24T09:00:00+08:00"
SOURCE_KIND = acs.SOURCE_PERSONAL_OPPORTUNITY
#: Distinctive bodies: if either ever shows up in a candidate/validation payload, the
#: "the prompt never carries document text" claim is false.
SECRET_OLD_BODY = "PRIVATE-OLD-BODY-MUST-NOT-REACH-A-PROMPT"
SECRET_NEW_BODY = "PRIVATE-NEW-BODY-FROM-THE-OPERATOR"


# ---------------------------------------------------------------------------
# a fake owner behind the real port transport
# ---------------------------------------------------------------------------


class FakeOwner(LifeArtifactPort):
    """A real ``LifeArtifactPort`` whose ``_call`` answers from memory."""

    def __init__(self) -> None:
        super().__init__("/tmp/fake-life-supply.sock", SUBJECT)
        self.rows: dict[str, dict] = {
            "artifact:a1": {"artifact_id": "artifact:a1", "title": "A", "lifecycle": "ACTIVE",
                            "revision": 1, "content": "alpha"},
            "artifact:a2": {"artifact_id": "artifact:a2", "title": "B", "lifecycle": "ACTIVE",
                            "revision": 2, "content": "beta"},
        }
        self.heads: dict[str, dict] = {
            "artifact:a1": {"version_id": "ver-a1-1", "version_number": 1},
            "artifact:a2": {"version_id": "ver-a2-2", "version_number": 2},
        }
        self.missing: set[str] = set()
        #: The owner's tombstone: the object stays in storage but no read serves it any more.
        self.tombstoned: set[str] = set()
        self.requests: list[dict] = []

    def _call(self, request):
        self.call_count += 1
        self.requests.append(dict(request))
        op = request.get("op")
        if op == "list_artifacts":
            return {"status": "OK", "artifacts": [
                {key: value for key, value in row.items() if key != "content"}
                for artifact_id, row in self.rows.items()
                if artifact_id not in self.tombstoned]}
        if op == "read_artifact":
            artifact_id = str(request.get("artifact_id"))
            if artifact_id in self.missing:
                raise ArtifactOwnerRefused("NOT_FOUND", "missing", "")
            if artifact_id in self.tombstoned:
                raise ArtifactOwnerRefused("NOT_FOUND", "tombstoned", "")
            row = self.rows[artifact_id]
            return {"status": "OK", "artifact": dict(row),
                    "head": dict(self.heads[artifact_id]), "content": row["content"]}
        if op == "read_head":
            artifact_id = str(request.get("artifact_id"))
            if artifact_id in self.missing or artifact_id in self.tombstoned:
                return {"status": "NOT_FOUND"}
            return {"status": "OK", "head": dict(self.heads[artifact_id])}
        if op == "commit_version":
            artifact_id = str(request.get("artifact_id"))
            number = int(self.heads[artifact_id]["version_number"]) + 1
            self.heads[artifact_id] = {"version_id": "ver-%s-%d" % (artifact_id[-2:], number),
                                       "version_number": number}
            self.rows[artifact_id] = {**self.rows[artifact_id], "revision": number,
                                      "content": str(request.get("content") or "")}
            return {"status": "OK", "version_id": self.heads[artifact_id]["version_id"],
                    "version_number": number, "effect": "APPLIED"}
        if op == "archive_artifact":
            artifact_id = str(request.get("artifact_id"))
            self.rows[artifact_id] = {**self.rows[artifact_id], "lifecycle": "ARCHIVED"}
            return {"status": "OK", "effect": "APPLIED"}
        if op == "tombstone":
            artifact_id = str(request.get("artifact_id"))
            self.tombstoned.add(artifact_id)
            return {"status": "OK", "effect": "APPLIED"}
        return {"status": "OK"}


def _materializer(port: LifeArtifactPort):
    source = acs.ArtifactContinuationSource(port, subject_id=SUBJECT)
    registry = ag0.CandidateSourceRegistry({SOURCE_KIND: source})
    capability = ag0.CandidateProjectionGuard.issue_capability(
        caller_module="ag0_test_harness")
    return source, ag0.CandidateMaterializer(registry, capability=capability,
                                             record_audit=False)


def _semantic_view(cset, materializer) -> list[dict]:
    candidates = [materializer._candidates_by_id[ref] for ref in cset.candidate_refs]
    return [
        {
            "candidate_id": candidate.candidate_id,
            "source_kind": candidate.source_kind,
            "source_ref": candidate.source_ref,
            "source_revision": candidate.source_revision,
            "target_refs": list(candidate.target_refs),
            "availability_status": candidate.availability_status,
        }
        for candidate in sorted(candidates, key=lambda item: item.candidate_id)
    ]


class RoundReadMemoTest(unittest.TestCase):
    def test_same_artifact_in_one_round_is_read_once_and_different_artifacts_do_not_merge(self):
        owner = FakeOwner()
        port = RoundCachedArtifactPort(owner)
        port.begin_round(OBSERVED_AT)

        first = port.read_artifact("artifact:a1")
        second = port.read_artifact("artifact:a1")
        self.assertEqual(first, second)
        self.assertEqual(owner.call_count, 1)
        self.assertEqual(port.call_count, 1)
        self.assertEqual(port.cache_hits, 1)
        self.assertGreater(port.bytes_avoided, 0)

        other = port.read_artifact("artifact:a2")
        self.assertNotEqual(first["head"], other["head"])
        self.assertEqual(owner.call_count, 2)

    def test_write_invalidates_and_a_new_round_starts_empty(self):
        owner = FakeOwner()
        port = RoundCachedArtifactPort(owner)
        port.begin_round(OBSERVED_AT)
        self.assertEqual(port.read_head("artifact:a1")["version_id"], "ver-a1-1")

        port._call({"op": "commit_version", "artifact_id": "artifact:a1",
                    "expected_head": "ver-a1-1", "content": "changed",
                    "grant_id": "grant", "operation_key": "op:test"})
        self.assertEqual(port.read_head("artifact:a1")["version_id"], "ver-a1-2")
        self.assertEqual(owner.call_count, 3)
        self.assertGreaterEqual(port.write_invalidations, 1)

        port.end_round()
        port.begin_round(OBSERVED_AT)  # deliberately the same token
        self.assertEqual(port.read_head("artifact:a1")["version_id"], "ver-a1-2")
        self.assertEqual(owner.call_count, 4, "a new round must not reuse the old memo")

    def test_revocation_write_invalidates_a_cached_list(self):
        owner = FakeOwner()
        port = RoundCachedArtifactPort(owner)
        port.begin_round(OBSERVED_AT)
        before = {row["artifact_id"]: row["lifecycle"] for row in port.list_artifacts()}
        self.assertEqual(before["artifact:a2"], "ACTIVE")

        port._call({"op": "archive_artifact", "artifact_id": "artifact:a2"})
        after = {row["artifact_id"]: row["lifecycle"] for row in port.list_artifacts()}
        self.assertEqual(after["artifact:a2"], "ARCHIVED",
                         "a revocation-like write must invalidate the cached list")

    def test_failures_are_not_remembered(self):
        owner = FakeOwner()
        owner.missing.add("artifact:a1")
        port = RoundCachedArtifactPort(owner)
        port.begin_round(OBSERVED_AT)
        with self.assertRaises(ArtifactOwnerRefused):
            port.read_artifact("artifact:a1")
        owner.missing.clear()
        self.assertEqual(port.read_artifact("artifact:a1")["head"]["version_id"], "ver-a1-1")
        self.assertEqual(owner.call_count, 2)

    def test_invalidate_forces_a_fresh_read_after_a_wait(self):
        owner = FakeOwner()
        port = RoundCachedArtifactPort(owner)
        port.begin_round(OBSERVED_AT)
        port.read_head("artifact:a1")
        port.invalidate()
        port.read_head("artifact:a1")
        self.assertEqual(owner.call_count, 2)
        self.assertEqual(port.cache_hits, 0)

    def test_candidate_set_is_identical_with_and_without_the_memo(self):
        raw_owner = FakeOwner()
        cached_owner = FakeOwner()
        _raw_source, raw_materializer = _materializer(raw_owner)
        cached_port = RoundCachedArtifactPort(cached_owner)
        _cached_source, cached_materializer = _materializer(cached_port)

        raw_set = raw_materializer.build_candidate_set(observed_at=OBSERVED_AT)
        cached_port.begin_round(OBSERVED_AT)
        cached_set = cached_materializer.build_candidate_set(observed_at=OBSERVED_AT)
        cached_port.end_round()

        self.assertEqual(_semantic_view(raw_set, raw_materializer),
                         _semantic_view(cached_set, cached_materializer))
        self.assertEqual(len(raw_set.candidate_refs), 2)
        self.assertLess(cached_owner.call_count, raw_owner.call_count,
                        "the memoized round must make fewer owner calls")

    def test_duck_typed_ports_are_left_alone(self):
        class Narrow:
            pass

        narrow = Narrow()
        self.assertIs(maybe_round_cached_port(narrow), narrow)
        real = FakeOwner()
        wrapped = maybe_round_cached_port(real)
        self.assertIsInstance(wrapped, RoundCachedArtifactPort)
        self.assertIs(maybe_round_cached_port(wrapped), wrapped)


# ---------------------------------------------------------------------------
# the formal round boundary: a use-time re-check must ask the owner again
# ---------------------------------------------------------------------------


class FormalRecheckTest(unittest.TestCase):
    """The memo may answer within one round's snapshot; it must never answer a re-check.

    The concrete window these tests pin down: the first read of a wake is the wake's view,
    and an external archive / tombstone / commit after it is *not* in that view.  What must
    hold is that the formal use-time check -- the pre-adoption candidate freshness check and
    the reading pre-commit re-check -- drops the memo and observes the owner's current state,
    so nothing is adopted or written on the strength of the older answer.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="wake-cost-pause-")
        self.pause_root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _cycle(self, port):
        import autonomous_life_cycle as alc

        cycle = alc.AutonomousLifeCycle.__new__(alc.AutonomousLifeCycle)
        cycle.artifact_port = port
        cycle.pause_state_root = self.pause_root
        cycle._business_clock = None
        return cycle

    @staticmethod
    def _candidate_for(materializer, artifact_id: str):
        for candidate in materializer._candidates_by_id.values():
            if artifact_id in str(candidate.source_ref):
                return candidate
        raise AssertionError("no candidate for %s" % artifact_id)

    def test_pre_adoption_boundary_makes_an_external_archive_visible(self):
        owner = FakeOwner()
        port = RoundCachedArtifactPort(owner)
        _source, materializer = _materializer(port)
        reader = ag0.CandidateReadService(materializer)
        port.begin_round(OBSERVED_AT)
        try:
            materializer.build_candidate_set(observed_at=OBSERVED_AT)
            candidate = self._candidate_for(materializer, "artifact:a1")
            # An *independent* operator archives the work: this is the owner's own write,
            # not the cycle's port and not a manual cache invalidation.
            owner._call({"op": "archive_artifact", "artifact_id": "artifact:a1"})
            # Before the formal boundary the round's snapshot still answers.  That is the
            # documented one-wake window -- it is not a permission to write, because the
            # checks below are what every adoption and every step goes through.
            before = reader.validate_candidate(candidate.candidate_id, observed_at=OBSERVED_AT)
            self.assertTrue(before["valid"])
            self._cycle(port)._pause_gate_hook(None)
            after = reader.validate_candidate(candidate.candidate_id, observed_at=OBSERVED_AT)
        finally:
            port.end_round()
        self.assertFalse(after["valid"])
        self.assertEqual(after["reason_code"], "ARTIFACT_NOT_CONTINUABLE")
        self.assertEqual(owner.rows["artifact:a1"]["lifecycle"], "ARCHIVED")
        self.assertEqual(owner.heads["artifact:a1"]["version_number"], 1,
                         "the freshness check must not create a version")
        self.assertNotIn("commit_version", [r.get("op") for r in owner.requests])

    def test_pre_adoption_boundary_makes_an_external_commit_visible(self):
        owner = FakeOwner()
        owner.rows["artifact:a1"]["content"] = SECRET_OLD_BODY
        port = RoundCachedArtifactPort(owner)
        _source, materializer = _materializer(port)
        reader = ag0.CandidateReadService(materializer)
        port.begin_round(OBSERVED_AT)
        try:
            materializer.build_candidate_set(observed_at=OBSERVED_AT)
            candidate = self._candidate_for(materializer, "artifact:a1")
            owner._call({"op": "commit_version", "artifact_id": "artifact:a1",
                         "content": SECRET_NEW_BODY})
            self._cycle(port)._pause_gate_hook(None)
            after = reader.validate_candidate(candidate.candidate_id, observed_at=OBSERVED_AT)
        finally:
            port.end_round()
        self.assertFalse(after["valid"])
        self.assertEqual(after["reason_code"], "ARTIFACT_HEAD_ADVANCED")
        self.assertEqual(owner.heads["artifact:a1"]["version_number"], 2)
        # The selected candidate's "use-time" revision is the operator's new head, and the
        # decision payload carries refs/enums only -- neither body ever reaches a prompt.
        import agency_cognition_adapter as aca

        prompt_view = aca.ProductionAgencyCognitionAdapter._compact_candidate(
            after["candidate"])
        rendered = json.dumps(prompt_view, ensure_ascii=False)
        self.assertNotIn(SECRET_OLD_BODY, rendered)
        self.assertNotIn(SECRET_NEW_BODY, rendered)
        # Exactly the operator's one commit: the freshness check itself wrote nothing.
        self.assertEqual([r.get("op") for r in owner.requests].count("commit_version"), 1)

    def test_the_boundary_stops_serving_a_cached_tombstoned_body(self):
        owner = FakeOwner()
        owner.rows["artifact:a1"]["content"] = SECRET_OLD_BODY
        port = RoundCachedArtifactPort(owner)
        port.begin_round(OBSERVED_AT)
        try:
            self.assertEqual(port.read_artifact("artifact:a1")["content"], SECRET_OLD_BODY)
            owner._call({"op": "tombstone", "artifact_id": "artifact:a1"})
            # Still inside the same wake and before any re-check: the memo answers with the
            # wake's view (this is the window, stated rather than denied).
            snapshot_answer = port.read_artifact("artifact:a1")
            self.assertEqual(snapshot_answer["content"], SECRET_OLD_BODY)
            self._cycle(port)._pause_gate_hook(None)
            with self.assertRaises(ArtifactOwnerRefused) as caught:
                port.read_artifact("artifact:a1")
        finally:
            port.end_round()
        self.assertEqual(caught.exception.status, "NOT_FOUND")
        self.assertNotIn(SECRET_OLD_BODY, str(caught.exception))


class DirectEvaluationRoundTest(unittest.TestCase):
    """``evaluate_once`` itself owns the round, not only ``_finish`` or the runner."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="wake-cost-round-")
        self.pause_root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _cycle(self, port):
        import autonomous_life_cycle as alc

        cycle = alc.AutonomousLifeCycle.__new__(alc.AutonomousLifeCycle)
        cycle.artifact_port = port
        cycle.pause_state_root = self.pause_root
        cycle._business_clock = None
        return cycle

    def test_a_raising_round_still_ends_the_memo(self):
        owner = FakeOwner()
        port = RoundCachedArtifactPort(owner)
        cycle = self._cycle(port)

        def failing_round(**kwargs):
            port.begin_round(kwargs["observed_at"])
            port.read_head("artifact:a1")
            raise RuntimeError("decision provider failed")

        cycle._evaluate_once_round = failing_round
        with self.assertRaises(RuntimeError):
            cycle.evaluate_once(trigger_ref="evt:round:1", observed_at=OBSERVED_AT)
        self.assertFalse(port.stats()["active"])
        # Inactive means the next read is a real owner call, not the failed round's answer.
        port.read_head("artifact:a1")
        self.assertEqual(owner.call_count, 2)

    def test_an_empty_round_still_ends_the_memo(self):
        owner = FakeOwner()
        port = RoundCachedArtifactPort(owner)
        cycle = self._cycle(port)

        def empty_round(**kwargs):
            port.begin_round(kwargs["observed_at"])
            port.read_head("artifact:a1")
            return {"outcome": "NO_MATERIAL", "llm_calls": 0}

        cycle._evaluate_once_round = empty_round
        out = cycle.evaluate_once(trigger_ref="evt:round:2", observed_at=OBSERVED_AT)
        self.assertEqual(out["outcome"], "NO_MATERIAL")
        self.assertFalse(port.stats()["active"])

    def test_two_rounds_with_the_same_instant_read_the_owners_again(self):
        owner = FakeOwner()
        port = RoundCachedArtifactPort(owner)
        cycle = self._cycle(port)
        seen: list[str] = []

        def one_round(**kwargs):
            port.begin_round(kwargs["observed_at"])
            seen.append(port.read_head("artifact:a1")["version_id"])
            return {"outcome": "QUIET", "llm_calls": 0}

        cycle._evaluate_once_round = one_round
        cycle.evaluate_once(trigger_ref="evt:round:3", observed_at=OBSERVED_AT)
        owner._call({"op": "commit_version", "artifact_id": "artifact:a1", "content": "moved"})
        # The second wake deliberately carries the *same* observed instant.
        cycle.evaluate_once(trigger_ref="evt:round:4", observed_at=OBSERVED_AT)
        self.assertEqual(seen, ["ver-a1-1", "ver-a1-2"])


# ---------------------------------------------------------------------------
# stats: synthetic record roots
# ---------------------------------------------------------------------------


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
                    encoding="utf-8")


class BehaviorStatsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="wake-cost-stats-")
        self.root = Path(self._tmp.name)
        self.state = self.root / "one-day" / "life" / "state"
        self.world = self.root / "one-day" / "world-home" / "data" / "world"
        _write_json(self.root / "fastday.json", {
            "historical_requests": 32, "request_total_limit": 300,
            "max_new_requests": 20, "run_id": "run:test"})
        _write_json(self.state / "fastday" / "experiment_budget.json", {
            "kind": "fastday_experiment_request_cap", "historical_requests": 32,
            "reserved_calls": 1, "max_new_requests": 20,
            "authorized_total_limit": 300})
        _write_json(self.state / "fastday" / "fastday_clock.json", {
            "domain_id": "fastday:test", "rate": 12.0, "logical_seconds": 21600.0,
            "state": "STOPPED"})
        _write_json(self.state / "resident_summary.json", {
            "ticks": 2, "counters": {"wakes": 327, "journey_arrivals": 292},
            "time_domain": {"domain_id": "fastday:test", "rate": 12.0,
                            "logical_elapsed_seconds": 21600, "state": "RUNNING"}})

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_movement_union_is_deduped_and_partial_is_not_zero(self):
        _write_json(self.world / "world_travel_delivery.json", {"travels": [
            {"route_id": "a", "status": "ARRIVED", "started_at": "2026-10-24T00:00:00Z",
             "arrived_at": "2026-10-24T00:01:00Z", "route_duration_seconds": 60},
            {"route_id": "b", "status": "ARRIVED", "started_at": "2026-10-24T00:01:00Z",
             "arrived_at": "2026-10-24T00:03:00Z", "route_duration_seconds": 120},
            {"route_id": "c", "status": "ARRIVED", "started_at": "2026-10-24T00:04:00Z",
             "arrived_at": "2026-10-24T00:08:00Z", "route_duration_seconds": 240},
        ]})
        result = stats.collect(self.root)
        movement = result["movement"]
        self.assertEqual(movement["status"], "COMPLETE")
        self.assertEqual(movement["movement_seconds"], 420.0)
        self.assertEqual(movement["intervals_counted"], 2, "touching travels merge")
        self.assertAlmostEqual(movement["movement_share_of_logical_day"], 420.0 / 21600.0, 6)
        self.assertEqual(movement["total_activity_share"], "NOT_MEASURABLE")

        _write_json(self.world / "world_travel_delivery.json", {"travels": [
            {"route_id": "a", "status": "ARRIVED", "started_at": "2026-10-24T00:00:00Z",
             "arrived_at": "2026-10-24T00:01:00Z"},
            {"route_id": "broken", "status": "ARRIVED", "started_at": "2026-10-24T00:04:00Z",
             "arrived_at": None},
        ]})
        partial = stats.collect(self.root)["movement"]
        self.assertEqual(partial["status"], "PARTIAL")
        self.assertIsNone(partial["movement_seconds"], "a partial total is not a number")
        self.assertEqual(partial["unknown_intervals"], 1)
        self.assertEqual(partial["known_movement_seconds"], 60.0)

    def test_missing_world_records_are_unavailable_not_zero(self):
        movement = stats.collect(self.root)["movement"]
        self.assertEqual(movement["status"], "UNAVAILABLE")
        self.assertIsNone(movement["movement_seconds"])
        self.assertIsNone(movement["movement_share_of_logical_day"])

    def test_rule_resolved_rounds_are_zero_model_and_ledger_is_not_mixed(self):
        _write_json(self.state / "life_request_budget.json", {
            "run_id": "run:test", "days": {"2026-10-24": 1},
            "tokens": [{"kind": "decision"}],
            "windows": {"run:test": {"calls": 1}}})
        _write_jsonl(self.state / "autonomous_wakes.jsonl", [
            {"status_class": "silence", "llm_calls": 0, "outcome": None},
            {"status_class": "choice", "llm_calls": 1, "outcome": "NO_ACTION",
             "decision": "NO_ACTION"}])
        _write_jsonl(self.state / "agency_decision_journal.jsonl", [
            {"payload": {"event_type": "DECISION_ATTEMPT_OPENED", "route": "RULE_RESOLVED"}},
            {"payload": {"event_type": "DECISION_ATTEMPT_OPENED",
                         "route": "COGNITIVE_REQUIRED"}},
            {"payload": {"event_type": "DECISION_COMMITTED", "decision": "NO_ACTION",
                         "decision_route": "COGNITIVE_REQUIRED",
                         "decision_id": "dec:1"}},
            {"payload": {"event_type": "DECISION_COMMITTED", "decision": "NO_ACTION",
                         "decision_route": "RULE_RESOLVED",
                         "decision_id": "dec:2"}},
        ])
        result = stats.collect(self.root)
        decisions = result["decisions"]
        self.assertEqual(decisions["model_decision_rounds"], 1)
        self.assertEqual(decisions["rule_resolved_rounds"], 1)
        self.assertEqual(decisions["no_action_model_chosen"], 1)
        self.assertEqual(decisions["no_action_rule_resolved"], 1)
        self.assertEqual(decisions["ledger"]["reserved"], 1)
        self.assertEqual(decisions["ledger"]["by_kind"], {"decision": 1})
        self.assertEqual(result["wakes"]["llm_rounds_from_wakes"], 1)

    def test_repeated_source_revision_is_flagged_without_calling_it_a_duplicate(self):
        base = {"event_type": "MATERIALIZED", "source_kind": "WORLD_OPPORTUNITY",
                "source_ref": "wopp:pose:home:r1:standing", "source_revision": 1,
                "materialized_at": "2026-10-24T09:00:00+08:00",
                "reason_code": "SOURCE_VALIDATED"}
        _write_jsonl(self.state / "candidate_audit_journal.jsonl", [
            {**base, "candidate_id": "cand:1", "availability_status": "OPEN"},
            {**base, "candidate_id": "cand:2", "availability_status": "OPEN"},
        ])
        candidates = stats.collect(self.root)["candidates"]
        self.assertEqual(candidates["materialized"], 2)
        self.assertEqual(candidates["unique_materialized_candidates"], 2)
        self.assertEqual(candidates["duplicate_candidate_id_materializations"], 0)
        self.assertEqual(len(candidates["same_source_revision_reappearances"]), 1)
        self.assertIn("not called a duplicate semantic candidate",
                      candidates["same_source_revision_note"])
        self.assertEqual(candidates["candidates_per_round"], [2])

    def test_read_surface_wake_counter_is_not_the_unique_count(self):
        _write_jsonl(self.state / "autonomous_wakes.jsonl", [
            {"status_class": "silence", "llm_calls": 0},
            {"status_class": "acted", "llm_calls": 1}])
        result = stats.collect(self.root)
        self.assertEqual(result["wakes"]["records"], 2)
        self.assertEqual(result["wakes"]["read_surface"]["counter_wakes"], 327)
        codes = {item["code"] for item in result["warnings"]}
        self.assertIn("READ_SURFACE_WAKE_COUNT", codes)
        self.assertIn("ARRIVAL_PROJECTION_REPEATED", codes)


if __name__ == "__main__":
    unittest.main()

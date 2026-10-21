#!/usr/bin/env python3
"""v0.4 stage A: her own "later", the park/switch/resume chain, and multi-project choice.

What this file pins down (and nothing else)
-------------------------------------------
* **pure -- the future-items domain**: a "later" is really saved with a clock she named, the same
  intent twice is one item, a due item can be cancelled and rescheduled, and a proposal outside
  the bounds is refused by name instead of being clamped.
* **pure -- the park spec**: the wait she chooses for "set this down until then" is a real
  ``time:`` LR-4 condition that releases the foreground (so something else can happen) and
  schedules the owner's own agenda -- no deadline quota, no auto-resume.
* **pure -- project options**: an option exists only when her own material (a question she wrote)
  or a due item supports one, a duplicate title is not offered, and the gateway refuses by name
  when B's owner service is not wired.
* **the loop** (POSIX only -- the life modules import ``fcntl``): one whole chain with an offline
  decision double -- propose and save, come due, become a project option, be selected, have the
  owner create the work, plan the first real step on it, then be cancelled.  Plus multi-project
  routing (a second work can win instead of the one bound at start-up) and the named refusal for
  a work the owner no longer calls resumable.

No model, no network, no Supply socket, no Wallet.  The owner doubles below are described as
doubles, and the "decision double" only returns what a committed Decision record would carry.

Fixtures live under ``/tmp/nyairo-v04-stage-a-fixtures`` and are **kept** (no TemporaryDirectory,
no unlink): a failing run must leave its state on disk.

Run (POSIX host)::

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v tests.test_v04_autonomous_day_stage
"""
from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIFE_SCRIPTS = ROOT / "components" / "life" / "scripts"
sys.path[:0] = [str(ROOT), str(LIFE_SCRIPTS)]

import life_future_items as lfi  # noqa: E402
import life_project_options as lpo  # noqa: E402
import waiting_ref_vocab  # noqa: E402

POSIX_ONLY = unittest.skipUnless(os.name == "posix", "life modules import fcntl (POSIX only)")

TS0 = "2026-11-02T09:00:00+00:00"
TS_DUE = "2026-11-02T09:40:00+00:00"
TS_LATER = "2026-11-02T11:00:00+00:00"

A1 = "artifact:11111111111111111111111111111111"
A2 = "artifact:22222222222222222222222222222222"
A3 = "artifact:33333333333333333333333333333333"
H1 = "artifact-version:aaaa"
H2 = "artifact-version:bbbb"
H3 = "artifact-version:cccc"

FIXTURE_ROOT = Path(os.environ.get("NYAIRO_STAGE_A_FIXTURE_ROOT")
                    or "/tmp/nyairo-v04-stage-a-fixtures")


def fixture_dir(name: str) -> Path:
    """A per-run, per-case directory under the kept fixture root (never cleaned up)."""

    path = FIXTURE_ROOT / f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-{name}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def new_store(name: str) -> tuple[lfi.FutureItems, Path]:
    root = fixture_dir(name)
    return lfi.FutureItems(root, subject_id="subject:test"), root


# ---------------------------------------------------------------------------------------------
# pure: her own "later"
# ---------------------------------------------------------------------------------------------


class FutureItemsStoreTest(unittest.TestCase):
    def test_a_later_item_is_saved_with_the_clock_she_named_and_is_not_due_yet(self):
        store, root = new_store("save")
        result = store.propose(title="  回头把第二章那段改一遍 ", in_minutes=30, now=TS0)
        self.assertTrue(result["created"])
        item = result["item"]
        self.assertTrue(item["item_ref"].startswith("fitem:"))
        self.assertEqual(item["status"], lfi.STATUS_OPEN)
        self.assertEqual(item["title"], "回头把第二章那段改一遍")
        # 09:00 + 30min, in the store's own canonical spelling
        self.assertEqual(item["due_at"], "2026-11-02T09:30:00+00:00")
        # The real file is on disk -- this is a save, not a report.
        self.assertTrue((root / lfi.STATE_FILENAME).exists())
        self.assertEqual(store.due(TS0), [])
        self.assertEqual([row.item_ref for row in store.due(TS_DUE)], [item["item_ref"]])

    def test_the_same_intent_twice_is_one_item_and_a_changed_intent_is_a_second(self):
        store, _root = new_store("idempotent")
        first = store.propose(title="回头再想想", in_minutes=60, now=TS0)
        again = store.propose(title="回头再想想", in_minutes=60, now=TS0)
        self.assertEqual(first["item"]["item_ref"], again["item"]["item_ref"])
        self.assertFalse(again["created"])
        later = store.propose(title="回头再想想", in_minutes=90, now=TS0)
        self.assertTrue(later["created"])
        self.assertNotEqual(later["item"]["item_ref"], first["item"]["item_ref"])
        self.assertEqual(len(store.open_items()), 2)

    def test_a_due_item_can_be_cancelled_and_then_stops_being_due(self):
        store, _root = new_store("cancel")
        item = store.propose(title="把一个疑问写成东西", in_minutes=10, now=TS0)["item"]
        cancelled = store.cancel(item["item_ref"], now=TS_DUE)
        self.assertEqual(cancelled["status"], lfi.STATUS_CANCELLED)
        self.assertEqual(store.due(TS_LATER), [])
        with self.assertRaises(lfi.FutureItemError) as ctx:
            store.cancel(item["item_ref"], now=TS_LATER)
        self.assertEqual(ctx.exception.reason_code, "FUTURE_ITEM_NOT_OPEN")

    def test_a_due_item_can_be_rescheduled_and_keeps_standing(self):
        store, _root = new_store("reschedule")
        item = store.propose(title="晚点再看这页", in_minutes=10, now=TS0)["item"]
        moved = store.reschedule(item["item_ref"], in_minutes=120, now=TS_DUE)
        self.assertEqual(moved["status"], lfi.STATUS_OPEN)
        self.assertEqual(moved["due_at"], "2026-11-02T11:40:00+00:00")
        self.assertEqual(moved["revision"], int(item["revision"]) + 1)
        self.assertEqual(store.due(TS_DUE), [])

    def test_a_proposal_outside_the_bounds_is_refused_by_name(self):
        store, _root = new_store("bounds")
        for kwargs, code in (
            ({"in_minutes": 0}, "FUTURE_ITEM_DELAY_OUT_OF_BOUNDS"),
            ({"in_minutes": lfi.MAX_DELAY_MINUTES + 1}, "FUTURE_ITEM_DELAY_OUT_OF_BOUNDS"),
            ({"due_at": "not-a-time"}, "FUTURE_ITEM_DUE_AT_INVALID"),
            ({"in_minutes": 5, "due_at": TS_DUE}, "FUTURE_ITEM_DUE_TIME_AMBIGUOUS"),
            ({"in_minutes": None}, "FUTURE_ITEM_DUE_TIME_AMBIGUOUS"),
        ):
            with self.assertRaises(lfi.FutureItemError) as ctx:
                store.propose(title="某件事", now=TS0, **kwargs)
            self.assertEqual(ctx.exception.reason_code, code, kwargs)
        with self.assertRaises(lfi.FutureItemError):
            store.propose(title="   ", in_minutes=10, now=TS0)
        self.assertEqual(store.open_items(), [])

    def test_actions_are_bounded_to_her_own_items(self):
        with self.assertRaises(lfi.FutureItemError) as ctx:
            lfi.validate_actions([{"item_ref": "fitem:abc", "op": "delete"}])
        self.assertEqual(ctx.exception.reason_code, "FUTURE_ITEM_ACTION_UNSUPPORTED")
        with self.assertRaises(lfi.FutureItemError):
            lfi.validate_actions([{"item_ref": "fitem:a", "op": "cancel"}] * 4)
        # a bare id is spelled into *her* namespace, so this can never reach another owner
        self.assertEqual(lfi.validate_actions([{"item_ref": "abc", "op": "cancel"}])[0]["item_ref"],
                         "fitem:abc")


class ParkSpecTest(unittest.TestCase):
    def test_the_park_is_a_time_wait_that_releases_the_foreground(self):
        spec = lfi.park_wait_spec(due_at="2026-11-02T10:00:00+00:00", item_ref="fitem:abc")
        self.assertEqual(spec["condition_kind"], "TIME_AT_OR_AFTER")
        self.assertEqual(spec["parameters"]["target_time"], "2026-11-02T10:00:00+00:00")
        # Releasing the foreground is what makes "take up something else meanwhile" legal.
        self.assertIs(spec["release_foreground"], True)
        self.assertEqual(spec["schedule_agenda"]["trigger_kind"], "TIME_AT")
        self.assertEqual(spec["schedule_agenda"]["due_at"], "2026-11-02T10:00:00+00:00")
        # The reference is in the waiting-domain vocabulary LR-2 validates against.
        parsed = waiting_ref_vocab.parse_waiting_ref(spec["waiting_on_ref"])
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed[0], waiting_ref_vocab.WaitingRefKind.TIME)


# ---------------------------------------------------------------------------------------------
# pure: project options + the owner gateway
# ---------------------------------------------------------------------------------------------


class ProjectOptionsTest(unittest.TestCase):
    def test_an_option_comes_only_from_real_material_and_skips_a_title_she_already_has(self):
        questions = [{"marker_id": "unclear:aa", "source_id": "book-1",
                      "source_revision": "artifact-version:1",
                      "note": "为什么她在这里停下来？"}]
        options = lpo.build_options(questions=questions)
        self.assertEqual(len(options), 1, options)
        option = options[0]
        self.assertEqual(option["kind"], lpo.KIND_QUESTION)
        self.assertEqual(option["seed_text"], "为什么她在这里停下来？")
        self.assertTrue(option["option_ref"].startswith("popt:"))
        self.assertIn("pnote:unclear-aa", option["source_refs"])
        # ... and the same question is not offered again once a project carries that title.
        same_title = [{"artifact_id": A1, "title": option["title"], "lifecycle": "ACTIVE"}]
        self.assertEqual(lpo.build_options(questions=questions, existing_projects=same_title), [])
        # Nothing real, nothing offered -- no option is invented to fill a quota.
        self.assertEqual(lpo.build_options(questions=[{"note": "   "}]), [])

    def test_a_due_item_becomes_an_option_with_its_own_ref_as_basis(self):
        due = [{"item_ref": "fitem:abc", "title": "把这个念头做起来", "due_at": TS_DUE,
                "source_refs": ["fitem:abc"]}]
        options = lpo.build_options(due_items=due)
        self.assertEqual(len(options), 1)
        self.assertEqual(options[0]["kind"], lpo.KIND_FUTURE_ITEM)
        self.assertEqual(options[0]["basis_ref"], "fitem:abc")
        self.assertEqual(options[0]["seed_text"], "把这个念头做起来")

    def test_the_gateway_refuses_by_name_when_no_owner_is_wired(self):
        gateway = lpo.resolve_gateway(None, environment={}, workspace_id="workspace:test")
        self.assertFalse(gateway.available())
        self.assertEqual(gateway.availability()["reason_code"], lpo.REASON_OWNER_UNAVAILABLE)
        self.assertEqual(gateway.list_projects(), [])
        with self.assertRaises(lpo.ProjectOptionError) as ctx:
            gateway.create_project(title="t", seed_text="s", source_refs=[], option_ref="popt:x")
        self.assertEqual(ctx.exception.reason_code, lpo.REASON_OWNER_UNAVAILABLE)


# ---------------------------------------------------------------------------------------------
# doubles (owner stand-ins, described as such)
# ---------------------------------------------------------------------------------------------


class _FakePort:
    """A fake ManagedArtifact read/write port.  It is a stand-in, and it says so."""

    def __init__(self, artifacts):
        self.artifacts = {row["artifact_id"]: dict(row) for row in artifacts}
        self.commits = []

    def available(self):
        return True

    def list_artifacts(self):
        return [dict(row) for row in self.artifacts.values()]

    def read_head(self, artifact_id):
        row = self.artifacts[artifact_id]
        return {"version_id": row["head_version_id"], "version_number": row["version_number"]}

    def resume_point(self, artifact_id):
        from life_artifact_port import ArtifactResumePoint
        row = self.artifacts[artifact_id]
        return ArtifactResumePoint(artifact_id=artifact_id, title=row["title"],
                                   head_version_id=row["head_version_id"],
                                   version_number=row["version_number"],
                                   content=row.get("content", "一段"),
                                   lifecycle=row["lifecycle"], revision=row["revision"])

    def commit_version(self, **kwargs):
        self.commits.append(dict(kwargs))
        raise AssertionError("the direct-port path must not run in this stage test")

    def add_artifact(self, artifact_id, *, title, head, version_number=1):
        self.artifacts[artifact_id] = {"artifact_id": artifact_id, "title": title,
                                       "head_version_id": head, "version_number": version_number,
                                       "lifecycle": "ACTIVE", "revision": 1, "content": "一段"}


class _FakeProjectOwner:
    """B's owner service, as a double: it *does* create, and records exactly what it was asked."""

    def __init__(self, port):
        self.port = port
        self.calls = []
        self.created = []
        self.projects = []

    def list_projects(self):
        return {"status": "OK", "projects": [dict(row) for row in self.projects]}

    def create_project(self, *, title, seed_text, source_refs, operation_key, workspace_id,
                       subject_id=None):
        self.calls.append({"title": title, "seed_text": seed_text,
                           "source_refs": list(source_refs), "operation_key": operation_key,
                           "workspace_id": workspace_id, "subject_id": subject_id})
        artifact_id = f"artifact:{'9' * 8}{len(self.calls):024d}"
        head = f"artifact-version:{'9' * 4}{len(self.calls):04d}"
        self.port.add_artifact(artifact_id, title=title, head=head)
        self.projects.append({"artifact_id": artifact_id, "title": title,
                             "lifecycle": "ACTIVE", "revision": 1,
                             "head_version_id": head})
        self.created.append(artifact_id)
        return {"status": "OK", "artifact_id": artifact_id, "version_id": head,
                "version_number": 1, "created": True, "operation_key": operation_key}


class _FakeActivityRead:
    def __init__(self):
        self.activity = None

    def list_open_activities(self):
        return [self.activity] if self.activity else []

    def get_activity(self, activity_id):
        return self.activity


class _FakeCandidateRead:
    def __init__(self):
        self.rows = {}

    def register(self, records):
        for record in records:
            self.rows[record.candidate_id] = record.to_dict() if hasattr(record, "to_dict") else dict(record)

    def get_candidate(self, candidate_id):
        return self.rows.get(str(candidate_id))


class _FakeRegistry:
    def __init__(self):
        self.adapters = []

    def register_adapter(self, adapter, **_kwargs):
        self.adapters.append(adapter)

    def list_adapters(self):
        return list(self.adapters)


class _FakeRuntime:
    """Just enough owner surface for one round.  Every member is a read or a double."""

    def __init__(self, state_root):
        self.state_root = str(state_root)
        self.activity_read = _FakeActivityRead()
        self.candidate_registry = _FakeRegistry()
        self.candidate_read = _FakeCandidateRead()
        self.action_read = None
        self.condition_store = None
        self.waiting_coordinator = None
        #: v0.4: AR-0 is "wired" as a sentinel, so the round plans real AR-0 steps.
        self.artifact_action_adapter = object()
        self.coordinator = None

    def canonical_state(self):
        return {"foreground_activity_ref": None}


class _Decision:
    """What a committed Decision record would carry (the offline decision double)."""

    def __init__(self, decision, *, selected=None, later_item=None, actions=(),
                 target_activity_ref=None, decision_id="dec:stage-a"):
        self.decision = decision
        self.decision_id = decision_id
        self.reason_codes = ["STAGE_A"]
        self.short_rationale = "stage A"
        self.selected_candidate_ref = selected
        self.target_activity_ref = target_activity_ref
        self.later_item_proposal = later_item
        self.future_item_actions = tuple(actions)


class _ScriptedCoordinator:
    """AG-1+AG-2 as a script: it calls the same specs a real round would, and reports a Decision."""

    def __init__(self, script):
        self.script = list(script)
        self.kwargs = None
        self.built = {}

    def run_decision_and_adoption_cycle(self, **kwargs):
        self.kwargs = dict(kwargs)
        record = self.script.pop(0) if self.script else _Decision("NO_ACTION")
        built = {}
        spec = kwargs.get("adoption_spec")
        if callable(spec) and record.decision in {"START", "RESUME"}:
            built["adoption_spec"] = spec(record)
        step = kwargs.get("step_spec_override")
        if callable(step) and record.decision in {"START", "CONTINUE"}:
            built["step_spec"] = step(record)
        self.built = built
        return {
            "correlation_id": "corr:stage-a",
            "decision_result": {"decision_record": record, "decision_route": "COGNITIVE_REQUIRED"},
            "adoption_result": {"adoption_record": _Adoption(record)},
            "integration_record": None,
        }


class _Adoption:
    def __init__(self, record):
        self.adoption_status = "APPLIED"
        self.rejection_reason_code = None
        self.decision_kind = record.decision


# ---------------------------------------------------------------------------------------------
# the loop: propose -> due -> option -> select -> create -> cancel
# ---------------------------------------------------------------------------------------------


@POSIX_ONLY
class StageADayChainTest(unittest.TestCase):
    def _cycle(self, name, *, artifacts=(A1,), questions=None):
        import autonomous_life_cycle as alc

        root = fixture_dir(name)
        port = _FakePort([{"artifact_id": artifact, "title": f"作品{artifact[-2:]}",
                           "head_version_id": head, "version_number": 1,
                           "lifecycle": "ACTIVE", "revision": 1, "content": "一段"}
                          for artifact, head in zip(artifacts, (H1, H2, H3))])
        runtime = _FakeRuntime(root)
        store = lfi.FutureItems(root, subject_id="subject:test")
        owner = _FakeProjectOwner(port)
        gateway = lpo.ProjectOwnerGateway(owner, subject_id="subject:test",
                                          workspace_id="workspace:test")
        cycle = alc.AutonomousLifeCycle(
            runtime, artifact_port=port, subject_id="subject:test",
            workspace_id="workspace:test", artifact_grant_id="grant:stage-a", enabled=True,
            env={"CHIYA_TEST": "1"}, pause_state_root=root, continuity_state_root=root,
            future_items=store, project_owner=gateway,
            project_questions_provider=(lambda: list(questions or [])))

        runtime.coordinator = _ScriptedCoordinator([])
        return cycle, runtime, port, store, owner

    def test_a_gateway_resolves_b_owner_module_without_editing_either_side(self):
        """The cross-module seam: A's resolver + B's ``private_project_service`` factories."""

        try:
            import private_project_service as pps  # B's module (stage B)
        except Exception as exc:  # pragma: no cover - B not delivered in this tree
            self.skipTest(f"stage B owner service not present: {exc}")
        gateway = lpo.resolve_gateway(pps, environment={}, workspace_id="workspace:test")
        # An environment that names no Supply socket is *not* a usable owner: the factory refuses
        # and A reports the named refusal instead of building a local stand-in library of works.
        self.assertFalse(gateway.available())
        self.assertEqual(gateway.availability()["reason_code"], lpo.REASON_OWNER_UNAVAILABLE)
        self.assertEqual(gateway.list_projects(), [])

    def test_a_whole_chain_through_later_due_option_creation_and_cancel(self):
        cycle, runtime, port, store, owner = self._cycle("chain")
        # 1. a round that only defers -- and says what she will do later
        runtime.coordinator.script = [_Decision(
            "DEFER",
            later_item=lfi.validate_proposal({"title": "把这个疑问写成一个东西",
                                              "in_minutes": 30}))]
        first = cycle.evaluate_once(trigger_ref="evt:stage-a1", observed_at=TS0)
        writes = first.get("future_item_writes") or []
        self.assertEqual(len(writes), 1, first)
        self.assertEqual(writes[0]["status"], "APPLIED")
        self.assertTrue(writes[0]["created"])
        saved = store.open_items()
        self.assertEqual(len(saved), 1)
        item_ref = saved[0].item_ref
        self.assertEqual(writes[0]["item_ref"], item_ref)
        self.assertEqual(saved[0].due_at, "2026-11-02T09:30:00+00:00")
        self.assertEqual(saved[0].origin, lfi.ORIGIN_MODEL_PROPOSAL)

        # 2. when its time comes it is *material*: it becomes a bounded project option
        due_now = cycle.due_future_items(TS_DUE)
        self.assertEqual([row["item_ref"] for row in due_now], [item_ref])
        self.assertEqual([row.item_ref for row in store.due(TS_DUE)], [item_ref])
        options = cycle.project_options(now=TS_DUE, due_items=due_now)
        self.assertEqual(len(options), 1, options)
        self.assertEqual(options[0]["basis_ref"], item_ref)
        self.assertEqual(options[0]["seed_text"], "把这个疑问写成一个东西")

        # 3. she chooses it; the owner really creates the work and the step is planned on it
        records, _snap, _meta = cycle.artifact_source.materialize(observed_at=TS_DUE)
        option_candidates = [row for row in records if row.source_ref.startswith("popt:")]
        self.assertEqual(len(option_candidates), 1, [r.source_ref for r in records])
        runtime.candidate_read.register(records)
        chosen = option_candidates[0]
        runtime.coordinator.script = [_Decision(
            "START", selected=chosen.candidate_id, target_activity_ref="actv:new")]
        second = cycle.evaluate_once(trigger_ref="evt:stage-a2", observed_at=TS_DUE)
        self.assertEqual(len(owner.created), 1, second)
        created_id = owner.created[0]
        self.assertEqual(owner.calls[0]["title"], options[0]["title"])
        self.assertEqual(owner.calls[0]["workspace_id"], "workspace:test")
        self.assertTrue(owner.calls[0]["operation_key"].startswith("project:"))
        self.assertEqual(second["project_created"]["artifact_id"], created_id)
        spec = runtime.coordinator.built["adoption_spec"]
        self.assertIn(created_id, spec["target_refs"])
        step = runtime.coordinator.built["step_spec"]
        self.assertEqual(step["payload_spec"]["artifact_id"], created_id)
        # The step's own target keeps the source's ``artifact:`` wrapper on the owner's id -- the
        # same spelling every other work step in this tree uses.
        self.assertEqual(step["target_ref"], f"artifact:{created_id}")
        self.assertEqual(second["step_work_target"], f"artifact:{created_id}")

        # 4. she can also drop it: cancelling is her write, and the item stops being due
        runtime.coordinator.script = [_Decision(
            "NO_ACTION", actions=[{"item_ref": item_ref, "op": "cancel"}])]
        third = cycle.evaluate_once(trigger_ref="evt:stage-a3", observed_at=TS_LATER)
        write = third["future_item_writes"][0]
        self.assertEqual(write["status"], "APPLIED")
        self.assertEqual(write["item_status"], lfi.STATUS_CANCELLED)
        self.assertEqual(store.due(TS_LATER), [])

    def test_a_second_work_can_be_continued_instead_of_the_bound_one(self):
        cycle, runtime, _port, _store, _owner = self._cycle("multi", artifacts=(A1, A2))
        records, _snap, _meta = cycle.artifact_source.materialize(observed_at=TS0)
        artifact_candidates = [row for row in records if row.source_ref.startswith("artifact:")]
        self.assertEqual(len(artifact_candidates), 2, [r.source_ref for r in records])
        runtime.candidate_read.register(records)
        # The source keeps the owner's own spelling, so the id carries one extra ``artifact:``.
        second = [row for row in artifact_candidates if row.source_ref.endswith(A2)][0]
        decision = _Decision("START", selected=second.candidate_id)
        point = cycle.round_point_for(decision)
        self.assertIsNotNone(point)
        self.assertEqual(point.artifact_id, A2)
        spec = cycle._work_adoption_spec_for_decision(decision, fallback=None)
        self.assertIn(A2, spec["target_refs"])
        self.assertNotIn(A1, spec["target_refs"])
        step = cycle._work_step_spec_for_decision(decision, fallback=None, observed_at=TS0)
        self.assertEqual(step["payload_spec"]["artifact_id"], A2)
        self.assertEqual(step["payload_spec"]["expected_head"], H2)

    def test_a_work_the_owner_no_longer_calls_resumable_is_refused_by_name(self):
        cycle, runtime, port, _store, _owner = self._cycle("closed", artifacts=(A1,))
        port.artifacts[A3] = {"artifact_id": A3, "title": "收尾了", "head_version_id": H3,
                              "version_number": 1, "lifecycle": "ARCHIVED", "revision": 2,
                              "content": "一段"}
        runtime.candidate_read.rows["cand:closed"] = {"candidate_id": "cand:closed",
                                                     "source_ref": A3, "target_refs": [A3]}
        decision = _Decision("START", selected="cand:closed")
        with self.assertRaises(Exception) as ctx:
            cycle._work_adoption_spec_for_decision(decision, fallback=None)
        self.assertIn("WORK_NOT_CONTINUABLE", str(ctx.exception))

    def test_nothing_to_do_is_quiet_and_spends_no_request(self):
        import autonomous_life_cycle as alc

        cycle, runtime, port, _store, _owner = self._cycle("quiet")
        for artifact_id in list(port.artifacts):
            port.artifacts[artifact_id]["lifecycle"] = "ARCHIVED"
        material = cycle.materials()
        self.assertFalse(material["has_material"], material)
        self.assertEqual(material["future_items"]["due_count"], 0)
        self.assertEqual(material["project_options"], [])
        out = cycle.evaluate_once(trigger_ref="evt:quiet", observed_at=TS0)
        self.assertEqual(out["outcome"], alc.OUTCOME_NO_MATERIAL)
        self.assertEqual(out["llm_calls"], 0)
        self.assertIsNone(runtime.coordinator.kwargs)

    def test_a_newly_due_item_is_a_new_situation_and_reopens_the_question(self):
        cycle, _runtime, _port, store, _owner = self._cycle("situation")
        before = dict(cycle._situation_parts(material={"foreground_activity": None},
                                             readiness=cycle.step_readiness(act=False)))
        store.propose(title="回头再看这页", in_minutes=10, now=TS0)
        cycle._round_future_items = cycle.due_future_items(TS_DUE)
        after = dict(cycle._situation_parts(material={"foreground_activity": None},
                                            readiness=cycle.step_readiness(act=False)))
        self.assertEqual(before["due_items"], [])
        self.assertEqual(len(after["due_items"]), 1)
        self.assertNotEqual(before, after)

# ---------------------------------------------------------------------------------------------
# integration: the real owners (Supply + AG-1 + AG-2), with only the two model calls scripted
# ---------------------------------------------------------------------------------------------

try:  # the world fixture base is pure python; it is only *used* on POSIX below
    from components.worldgen.tests._setup import WorldGenTestCase as _WorldGenBase
except Exception:  # pragma: no cover - fixture tree absent
    _WorldGenBase = unittest.TestCase


class _StageACognition:
    """The two model calls, scripted: a decision double that only picks from what AG-1 offered.

    It is *not* a coordinator and it does not build a Decision: it answers exactly like the
    production adapter would (a raw JSON object), so real AG-1 validates it, real AG-2 adopts it,
    and real Supply writes the result.  The user's product provider is never called.
    """

    model_id = "stub:stage-a-real"
    model_version = "stage-a-real-v1"

    def __init__(self, policy, *, seed_artifact, park_minutes=2):
        self.policy = policy
        self.seed_artifact = str(seed_artifact)
        self.park_minutes = int(park_minutes)
        self.call_count = 0
        self.requests: list = []

    @staticmethod
    def _pick(candidates, predicate):
        for candidate in candidates:
            if predicate(candidate):
                return candidate
        return None

    def invoke(self, request, *, schema_retry_index: int = 0):
        import agency_decision_ag1 as ag1

        self.call_count += 1
        self.requests.append(request)
        candidates = [dict(c) for c in (getattr(request, "candidates", []) or [])]
        allowed = [str(code) for code in (getattr(request, "allowed_reason_codes", []) or [])]
        reason = allowed[:1] or ["NO_LEGAL_CANDIDATE"]

        def legal(candidate, word):
            return word in (candidate.get("legal_decisions") or [])

        def is_closeout(candidate):
            return any(str(ref).startswith("closeout:")
                       for ref in (candidate.get("target_refs") or []))

        def is_seed(candidate):
            # The same object is offered twice (keep working / wrap it up); continuing is the one
            # this stage is about, and archiving the seed here would end the chain before it starts.
            if is_closeout(candidate):
                return False
            if self.seed_artifact in str(candidate.get("source_ref") or ""):
                return True
            return any(self.seed_artifact in str(ref)
                       for ref in (candidate.get("target_refs") or []))

        raw: dict = {"decision": "NO_ACTION", "selected_candidate_id": None,
                     "reason_codes": reason, "short_rationale": "stub: nothing to do",
                     "deferred_candidate_ids": []}
        if self.policy == "defer_propose":
            raw = {"decision": "DEFER", "selected_candidate_id": None, "reason_codes": reason,
                   "short_rationale": "stub: later, with a time",
                   "deferred_candidate_ids": [c.get("candidate_id") for c in candidates],
                   "later_item": {"title": "回头把这个念头写成一个东西", "in_minutes": 1}}
        elif self.policy == "start_seed":
            chosen = self._pick(candidates, lambda c: is_seed(c) and legal(c, "START"))
            if chosen is not None:
                raw = {"decision": "START", "selected_candidate_id": chosen["candidate_id"],
                       "reason_codes": reason, "short_rationale": "stub: start the seed work",
                       "deferred_candidate_ids": []}
        elif self.policy == "start_project":
            chosen = self._pick(
                candidates,
                lambda c: str(c.get("source_ref") or "").startswith("popt:")
                and legal(c, "START"))
            if chosen is not None:
                raw = {"decision": "START", "selected_candidate_id": chosen["candidate_id"],
                       "reason_codes": reason, "short_rationale": "stub: start a new project",
                       "deferred_candidate_ids": []}
        elif self.policy == "park":
            chosen = self._pick(
                candidates,
                lambda c: c.get("candidate_kind") == "CONTINUE_CURRENT" and legal(c, "WAIT"))
            if chosen is not None:
                raw = {"decision": "WAIT", "selected_candidate_id": chosen["candidate_id"],
                       "reason_codes": reason,
                       "short_rationale": "stub: set this down, come back later",
                       "deferred_candidate_ids": [],
                       "later_item": {"title": "回头接着写这一段",
                                      "in_minutes": self.park_minutes,
                                      "park_current": True}}
        elif self.policy == "resume":
            chosen = self._pick(
                candidates,
                lambda c: c.get("candidate_kind") == "RESUME_ACTIVITY" and legal(c, "RESUME"))
            if chosen is not None:
                raw = {"decision": "RESUME", "selected_candidate_id": chosen["candidate_id"],
                       "reason_codes": reason, "short_rationale": "stub: come back to it",
                       "deferred_candidate_ids": []}
        return ag1.CognitiveDecisionResponse(
            model_run_ref="model_run:stage-a:%d" % self.call_count,
            model_id=self.model_id, model_version=self.model_version,
            raw_output=raw, latency_ms=1)


@POSIX_ONLY
class StageARealOwnerChainTest(_WorldGenBase):
    """One whole stage-A experience on the real owners, with the two model calls scripted.

    Real: Life Supply (its own Unix socket, governance + artifact store), AG-0 candidate set,
    AG-1 hard constraints + decision validation, AG-2 adoption, LR-2/LR-4 commands, AR-0 writes.
    Scripted: the decision answer and the content writer.  No credential is read and no real
    model is called.
    """

    def setUp(self) -> None:
        super().setUp()
        import tests.test_one_day_life as fx

        self._fx = fx
        #: Runtimes created by this test.  They are closed in ``tearDown`` *before* the fixture base
        #: removes the instance tree, which is the order production uses (flush, then go away).
        self._runtimes: list = []
        self.services = fx.Services(Path(self.temp_dir(prefix="stagea-svc-")))
        self.addCleanup(self.services.stop)

    def tearDown(self) -> None:
        for runtime in getattr(self, "_runtimes", []):
            try:
                runtime.close()
            except Exception:  # a close failure must not hide the test result
                pass
        self._runtimes = []
        super().tearDown()

    def _chain(self, policy, *, park_minutes=2):
        import os
        from types import SimpleNamespace
        from unittest import mock

        import autonomous_life_assembly as assembly
        import life_artifact_port as lap
        import life_integration_ag2 as ag2
        import life_runtime_production as lrp
        import managed_artifact_action_adapter as maaa
        import one_day_life as odl
        from components.worldgen.instance import init_instance
        from components.worldgen.tests._setup import make_valid_docs

        package_dir = self.temp_package(make_valid_docs(seed=929711, theme="coastal_town"),
                                        prefix="stagea-pkg-")
        world_home = Path(self.temp_dir(prefix="stagea-world-")) / "world-home"
        init_instance(package_dir, world_home)
        instance = Path(self.temp_dir(prefix="stagea-inst-")) / "instance"
        with mock.patch.dict(os.environ, self._fx.ENV_GATES):
            prepared = odl.prepare(instance=instance, supply_socket=self.services.supply_path,
                                   governance_socket=self.services.gov_path,
                                   subject="test:stage-a", title="Stage A fixture",
                                   world_package=package_dir)
        self.assertTrue(prepared.get("ok"), prepared)
        data = odl.read_instance(instance)
        env = odl.instance_environment(instance)
        # The project owner seam resolves B's service from *these* names (the same ones the
        # production assembly passes), so this also proves the wiring, not just the module.
        env.update({
            "NYAIRO_LIFE_SUPPLY_SOCKET": str(self.services.supply_path),
            "NYAIRO_LIFE_SUBJECT": str(data["subject_id"]),
            "NYAIRO_LIFE_WORKSPACE_ID": str(data["workspace_id"]),
            "NYAIRO_ARTIFACT_GRANT": str(data["artifact_grant_id"]),
        })
        cognition = _StageACognition(policy, seed_artifact=data["artifact_id"],
                                     park_minutes=park_minutes)
        writer = self._fx.StubWriter()
        port = lap.LifeArtifactPort(str(self.services.supply_path), data["subject_id"])
        adapter = maaa.SupplyManagedArtifactActionAdapter(
            port, grant_id=data["artifact_grant_id"], workspace_id=data["workspace_id"])
        switches = ag2.LifeRuntimeKillSwitches(
            life_runtime_enabled=True, agency_enabled=True, action_execution_enabled=True,
            proactive_enabled=False)
        runtime = lrp.build_production_life_runtime(
            odl.life_state_root(instance), kill_switches=switches, cognition_adapter=cognition,
            install_root=instance / "life", artifact_action_adapter=adapter)
        assembled = assembly.assemble_autonomous_life(
            runtime, environment=env, state_root=odl.life_state_root(instance),
            writer=writer, enabled=True)
        self.assertEqual(assembled["state"], assembly.READY, assembled)
        self._runtimes.append(runtime)
        return SimpleNamespace(runtime=runtime, assembled=assembled, cycle=assembled["cycle"],
                               cognition=cognition, writer=writer, env=env, data=data,
                               port=port, instance=instance)

    def _head(self, data, artifact_id):
        import one_day_life as odl

        answer = odl.supply_call(self.services.supply_path,
                                 {"op": "read_head", "subject": data["subject_id"],
                                  "artifact_id": artifact_id})
        return answer.get("head") or {}

    def _activity(self, fixture, activity_ref):
        return fixture.runtime.activity_read.get_activity(activity_ref)

    def test_the_stage_chain_parks_the_running_activity_and_comes_back_by_explicit_resume(self):
        """提案保存 → 真实 WAIT(时间条件 + 排程 + 释放前台) → 到点 eligibility → 显式 RESUME。"""

        fixture = self._chain("start_seed")
        first = fixture.cycle.evaluate_once(trigger_ref="evt:stage-a:1", act=True)
        self.assertEqual(first.get("outcome"), "ACTED", first)
        activity_ref = first.get("activity_ref")
        self.assertTrue(activity_ref, first)
        seed_head = self._head(fixture.data, fixture.data["artifact_id"])
        self.assertEqual(self._activity(fixture, activity_ref)["status"], "ACTIVE")

        # 2. "set this down until a time I name" -- the time is in the Decision's own proposal.
        fixture.cognition.policy = "park"
        parked = fixture.cycle.evaluate_once(trigger_ref="evt:stage-a:2", act=True)
        self.assertTrue(parked["park"]["available"], parked)
        self.assertEqual(parked["wait_basis"]["source"], "PARK_UNTIL_CHOSEN_TIME")
        self.assertEqual(self._activity(fixture, activity_ref)["status"], "WAITING")
        # The *owner* released the foreground (that is what makes doing something else legal).
        self.assertIsNone(fixture.runtime.canonical_state().get("foreground_activity_ref"))
        conditions = fixture.runtime.condition_store.load_state().get("conditions") or {}
        mine = [row for row in conditions.values()
                if row.get("activity_id") == activity_ref and row.get("status") == "PENDING"]
        self.assertEqual(len(mine), 1, conditions)
        self.assertEqual(mine[0]["condition_kind"], "TIME_AT_OR_AFTER")
        self.assertTrue(str(mine[0]["waiting_on_ref"]).startswith("time:"))
        agenda = [row for row in (fixture.runtime.waiting_coordinator.list_agenda_items()) or []
                  if row.get("activity_id") == activity_ref]
        self.assertEqual(len(agenda), 1, agenda)
        self.assertEqual(agenda[0]["trigger_kind"], "TIME_AT")
        # Two owners, two spellings of one instant (LR-4 stores it in its own local form).
        self.assertTrue(
            _same_instant(agenda[0]["due_at"], mine[0]["parameters"]["target_time"]),
            (agenda[0]["due_at"], mine[0]["parameters"]["target_time"]))
        saved = fixture.cycle.future_items.open_items()
        self.assertEqual(len(saved), 1, saved)
        self.assertEqual(saved[0].origin, "PARK_CURRENT_ACTIVITY")
        self.assertEqual(saved[0].activity_ref, activity_ref)
        # The store spells the instant its own way; it is the *same* instant the owner scheduled.
        self.assertTrue(_same_instant(saved[0].due_at, mine[0]["parameters"]["target_time"]),
                        (saved[0].due_at, mine[0]["parameters"]["target_time"]))

        # 3. while that one sits, something else is allowed: the *owner* really released the
        #    foreground, so an item of her own that has come due becomes a project she can start.
        from datetime import datetime, timedelta

        due_at = mine[0]["parameters"]["target_time"]
        park_moment = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
        meanwhile_due = (park_moment - timedelta(seconds=120)).isoformat(timespec="seconds")
        meanwhile = (park_moment - timedelta(seconds=60)).isoformat(timespec="seconds")
        asked = fixture.cycle.future_items.propose(
            title="趁这会儿把那个念头写成一篇", due_at=meanwhile_due,
            now=(park_moment - timedelta(minutes=3)).isoformat(timespec="seconds"))
        self.assertTrue((asked.get("item") or {}).get("item_ref"), asked)
        fixture.cognition.policy = "start_project"
        other = fixture.cycle.evaluate_once(trigger_ref="evt:stage-a:2b", observed_at=meanwhile,
                                           act=True)
        options = other.get("project_options") or []
        self.assertTrue(options, other)
        created = other.get("project_created") or {}
        self.assertTrue(created.get("artifact_id"), other)
        self.assertIn(created.get("title"), [row.get("title") for row in options])
        # The parked one was not disturbed: something else ran while it sat, untouched.
        self.assertEqual(self._activity(fixture, activity_ref)["status"], "WAITING")
        self.assertNotEqual(fixture.runtime.canonical_state().get("foreground_activity_ref"),
                            activity_ref)
        self.assertEqual(self._head(fixture.data, fixture.data["artifact_id"]), seed_head)

        # 4. the clock reaches it: the owner's own due scan produces an eligibility, nothing else.
        after_due = (park_moment + timedelta(seconds=90)).isoformat(timespec="seconds")
        maintenance = fixture.cycle.maintain_waiting(observed_at=after_due)
        self.assertFalse(maintenance.get("auto_resumed"), maintenance)
        open_eligible = [row for row in fixture.runtime.waiting_coordinator.list_eligibilities()
                         if row.get("activity_id") == activity_ref
                         and row.get("status") == "OPEN"]
        self.assertEqual(len(open_eligible), 1, maintenance)
        self.assertEqual(self._activity(fixture, activity_ref)["status"], "WAITING")

        # 5. the newer thing is set down too (nothing forces it) -- which only frees the
        #    foreground; it does not fetch anything back by itself.
        fixture.cognition.policy = "park"
        set_down = fixture.cycle.evaluate_once(trigger_ref="evt:stage-a:2c", observed_at=after_due,
                                               act=True)
        self.assertEqual(set_down.get("decision"), "WAIT", set_down)
        self.assertIsNone(fixture.runtime.canonical_state().get("foreground_activity_ref"))
        self.assertEqual(self._activity(fixture, activity_ref)["status"], "WAITING")

        # 6. she comes back by an explicit RESUME (her own choice, validated by AG-1/AG-2).
        resume_at = (park_moment + timedelta(seconds=150)).isoformat(timespec="seconds")
        fixture.cognition.policy = "resume"
        back = fixture.cycle.evaluate_once(trigger_ref="evt:stage-a:3", observed_at=resume_at,
                                           act=True)
        self.assertEqual(back.get("decision"), "RESUME", back)
        self.assertEqual(back.get("adoption_status"), "APPLIED", back)
        self.assertEqual(self._activity(fixture, activity_ref)["status"], "ACTIVE")
        self.assertEqual(fixture.runtime.canonical_state().get("foreground_activity_ref"),
                         activity_ref)
        # Coming back *continues the work*: exactly one more version, on the same Activity (a
        # resume is not a second Activity, and neither park wrote anything).
        new_head = self._head(fixture.data, fixture.data["artifact_id"])
        self.assertNotEqual(new_head, seed_head)
        self.assertEqual(int(new_head["version_number"]), int(seed_head["version_number"]) + 1)

    def test_a_new_project_is_created_by_b_owner_service_and_a_second_work_can_be_chosen(self):
        """提案 → 到期 → 项目选项 → 选中 → B service 真实创建 → 真实写一版；两份作品都在。"""

        fixture = self._chain("defer_propose")
        first = fixture.cycle.evaluate_once(trigger_ref="evt:stage-a:1", act=True)
        self.assertEqual(first.get("outcome"), "DEFERRED", first)
        saved = fixture.cycle.future_items.open_items()
        self.assertEqual(len(saved), 1, first)
        item = saved[0]
        self.assertEqual(item.origin, "MODEL_PROPOSAL")
        seed_head = self._head(fixture.data, fixture.data["artifact_id"])

        # 2. its time comes: the due item becomes a bounded project option (still only an option).
        from datetime import datetime, timedelta

        after_due = (datetime.fromisoformat(item.due_at.replace("Z", "+00:00"))
                     + timedelta(seconds=5)).isoformat(timespec="seconds")
        fixture.cognition.policy = "start_project"
        second = fixture.cycle.evaluate_once(trigger_ref="evt:stage-a:2", observed_at=after_due,
                                             act=True)
        options = second.get("project_options") or []
        self.assertEqual(len(options), 1, second)
        self.assertEqual(options[0]["basis_ref"], item.item_ref)
        created = second.get("project_created") or {}
        self.assertTrue(created.get("artifact_id"), second)
        project_id = created["artifact_id"]
        self.assertNotEqual(project_id, fixture.data["artifact_id"])
        # The real Supply owner holds it: her seed text is version 1 ...
        read = fixture.port.read_artifact(project_id)
        self.assertEqual(read["artifact"]["title"], options[0]["title"])
        self.assertIn("回头把这个念头写成一个东西", read.get("content") or "")
        self.assertEqual(second.get("step_work_target"), f"artifact:{project_id}")
        self.assertEqual(self._activity(fixture, second["activity_ref"])["status"], "ACTIVE")
        # ... and the round's own step continued *that* work (version 2, written through AR-0 and
        # the owner), not the seed this instance had bound at start-up.
        receipt = second.get("step_receipt") or {}
        self.assertEqual(receipt.get("artifact_id"), project_id)
        self.assertEqual(int(receipt.get("version_number") or 0), 2)
        project_head = self._head(fixture.data, project_id)
        self.assertEqual(int(project_head["version_number"]), 2)
        self.assertIn("替身内容生成", project_head.get("content") or "")
        self.assertEqual(self._head(fixture.data, fixture.data["artifact_id"]), seed_head)

if __name__ == "__main__":  # pragma: no cover
    unittest.main()


def _same_instant(left, right):
    """Two owner spellings of one instant (``+00:00`` vs ``+08:00``) are the same instant."""

    from datetime import datetime

    def parse(value):
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))

    try:
        return parse(left) == parse(right)
    except (TypeError, ValueError):
        return False

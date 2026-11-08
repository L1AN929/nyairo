#!/usr/bin/env python3
"""v0.6 ambient background: enabled World facts reach the Life prompt as background only.

This is the small, focused check for the narrowing approved on 2026-11-02: existing,
World-projected ambient facts may enter the Life world background, but they must not
become a candidate, an offer, a permission or a task, and the feature-off shape must stay
exactly the old one (no extra field, no extra read).

Three blocks:

1. ``AmbientViewTests`` -- ``life_live_context_view`` keeps the exact public event id and
   summary, caps the count at the World's own per-day bound, drops every other field, and
   leaves the off/absent shape untouched.
2. ``ProviderAmbientReadTests`` -- ``WorldMoveOpportunityProvider`` reads the official
   World projection only when the feature is enabled, binds it to the same clock/package,
   switches travel realization off for that read, and reports a failed read by name.
3. ``AmbientPromptTests`` -- the real ``ProductionAgencyCognitionAdapter.build_prompt``
   carries the same background; with the feature absent the prompt world block has no
   ``ambient`` key at all.

No product model, no network, no real world package: the World projection is patched.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT),
              str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_live_context_view as llcv  # noqa: E402
import world_move_opportunity_source as wmos  # noqa: E402

EVENT_ID = "ambient-2026-11-02-home_window_sound"
SUMMARY = "窗外能听到轮船的汽笛声"


def _event(event_id=EVENT_ID, summary=SUMMARY, **extra):
    row = {
        "event_id": event_id,
        "event_type": "home_window_sound",
        "location_id": "home",
        "local_date": "2026-11-02",
        "valid_from": "2026-11-02T00:00:00+09:00",
        "valid_until": "2026-11-03T00:00:00+09:00",
        "payload": {"summary": summary},
    }
    row.update(extra)
    return row


def _contract(payload):
    class _Provider:
        def daily_life_context(self, limit=8):
            return payload

    return _Provider()


def _base_context(**extra):
    payload = {
        "status": "available",
        "observed_at": "2026-11-02T22:00:00+09:00",
        "location": {"place_id": "home", "name": "家"},
        "body": {},
        "nearby": [],
        "opportunities": [],
        "unknowns": [],
    }
    payload.update(extra)
    return payload


# ---------------------------------------------------------------------------------------
# 1. the bounded view
# ---------------------------------------------------------------------------------------


class AmbientViewTests(unittest.TestCase):
    def test_feature_off_keeps_the_old_shape(self):
        context = llcv.read_live_context(_contract(_base_context()))
        self.assertNotIn("ambient", context)
        self.assertEqual(context["source"], llcv.SOURCE_CONTRACT)

    def test_enabled_but_empty_is_a_named_empty_background(self):
        context = llcv.read_live_context(_contract(_base_context(
            ambient={"status": "available",
                     "source": wmos.AMBIENT_CONTEXT_SOURCE, "events": []})))
        self.assertEqual(context["ambient"]["status"], "available")
        self.assertNotIn("events", context["ambient"])

    def test_event_id_and_summary_are_exact_and_bounded(self):
        # The view re-bounds the provider's already-flattened rows: event id + summary are
        # top-level there, and anything else must be dropped.
        events = []
        for index in range(4):
            events.append({
                "event_id": EVENT_ID if index == 0 else "ambient-x-%d" % index,
                "summary": SUMMARY if index == 0 else "第%d件事" % index,
                "event_type": "home_window_sound", "location_id": "home",
                "extra_authority": "task",
            })
        context = llcv.bound_for_prompt({"ambient": {
            "status": "available", "source": wmos.AMBIENT_CONTEXT_SOURCE,
            "events": events}})
        rows = context["ambient"]["events"]
        self.assertEqual(len(rows), llcv.MAX_AMBIENT_EVENTS)
        self.assertEqual(rows[0]["event_id"], EVENT_ID)
        self.assertEqual(rows[0]["summary"], SUMMARY)
        self.assertNotIn("payload", rows[0])
        self.assertNotIn("extra_authority", rows[0])

    def test_unavailable_read_keeps_its_reason_not_a_fabricated_event(self):
        context = llcv.bound_for_prompt({"ambient": {
            "status": "unavailable", "reason_code": "AMBIENT_READ_FAILED", "events": []}})
        self.assertEqual(context["ambient"]["status"], "unavailable")
        self.assertEqual(context["ambient"]["reason_code"], "AMBIENT_READ_FAILED")
        self.assertNotIn("events", context["ambient"])


# ---------------------------------------------------------------------------------------
# 2. the provider read
# ---------------------------------------------------------------------------------------


class ProviderAmbientReadTests(unittest.TestCase):
    def setUp(self):
        import world_living_skeleton as wls
        import world_runtime_data as wrd

        self._wls = wls
        self._wrd = wrd
        self._original_observe = wls.observe_world
        self._original_package_root = wrd.package_root
        self._calls = []
        wrd.package_root = lambda environment=None: "/tmp/ambient-package"

    def tearDown(self):
        self._wls.observe_world = self._original_observe
        self._wrd.package_root = self._original_package_root

    def _install(self, observation):
        def _observe(*args, **kwargs):
            self._calls.append(dict(kwargs))
            if isinstance(observation, Exception):
                raise observation
            return observation

        self._wls.observe_world = _observe

    def _provider(self, ambient):
        provider = wmos.WorldMoveOpportunityProvider(
            client=None, action_intent=None,
            environment={"CHIYO_WORLD_AMBIENT_EVENTS_ENABLED": ambient,
                         "CHIYO_WORLD_PACKAGE": "/tmp/ambient-package"},
            world_home="/tmp/ambient-world")
        provider.last_observation = {
            "location": {"place_id": "home", "area_id": "bedroom"},
            "scene_id": "scene:home", "visible_objects": []}
        return provider

    def test_enabled_reads_the_official_projection_without_travel_writes(self):
        self._install({"status": "available",
                       "local_time": "2026-11-02T22:00:00+09:00",
                       "ambient_events": [_event()]})
        provider = self._provider("1")
        context = provider.daily_life_context()
        self.assertEqual(context["ambient"]["status"], "available")
        self.assertEqual(context["ambient"]["events"][0]["event_id"], EVENT_ID)
        self.assertEqual(context["ambient"]["events"][0]["summary"], SUMMARY)
        self.assertEqual(len(self._calls), 1)
        # Travel realization is written off for this read only: Life never writes World state.
        self.assertEqual(
            self._calls[0]["environment"][wmos.AMBIENT_READ_TRAVEL_ENV], "0")
        self.assertNotIn(wmos.AMBIENT_READ_TRAVEL_ENV, provider.environment)

    def test_disabled_performs_no_extra_read_and_keeps_the_old_shape(self):
        self._install({"status": "available", "ambient_events": [_event()]})
        context = self._provider("0").daily_life_context()
        self.assertNotIn("ambient", context)
        self.assertEqual(self._calls, [])

    def test_read_failure_is_named_not_fabricated(self):
        self._install(RuntimeError("world read failed"))
        context = self._provider("1").daily_life_context()
        self.assertEqual(context["ambient"]["status"], "unavailable")
        self.assertEqual(context["ambient"]["reason_code"], "AMBIENT_READ_FAILED")
        self.assertEqual(context["ambient"]["events"], [])


# ---------------------------------------------------------------------------------------
# 3. the real prompt
# ---------------------------------------------------------------------------------------


def _request(world):
    return SimpleNamespace(
        opportunity_id="opp:ambient-1", snapshot_fingerprint="fp:ambient-1",
        policy_version="1", legal_decision_enums=["START", "NO_ACTION"],
        allowed_reason_codes=["OK"], current_activity_summary=None, capacity_summary=None,
        observed_fact_keys=["world_here_place_home"], prior_experiences=[],
        situation_context={"schema_version": "chiyo.life.decision_situation.v1",
                           "world": world},
        candidates=[{"candidate_id": "cand:stay", "candidate_kind": "WORLD",
                     "source_kind": "WORLD_OPPORTUNITY",
                     "source_ref": "world:opp:stay",
                     "target_refs": ["stay:home:rest:300"],
                     "legal_decisions": ["START", "NO_ACTION"]}],
        max_output_tokens=512,
    )


class AmbientPromptTests(unittest.TestCase):
    def _world(self, ambient=None):
        world = {"status": "observed", "source": llcv.SOURCE_CONTRACT,
                 "location": {"place_id": "home", "name": "家"},
                 "body": {}, "nearby": [], "opportunities": [], "unknowns": []}
        if ambient is not None:
            world["ambient"] = ambient
        return world

    def test_prompt_background_is_exact_when_on_and_absent_when_off(self):
        from agency_cognition_adapter import ProductionAgencyCognitionAdapter

        adapter = ProductionAgencyCognitionAdapter(client=None)
        world = self._world({"status": "available",
                             "source": wmos.AMBIENT_CONTEXT_SOURCE,
                             "events": [{"event_id": EVENT_ID, "summary": SUMMARY}]})
        text, payload = adapter.build_prompt(_request(world))
        events = payload["situation_context"]["world"]["ambient"]["events"]
        self.assertEqual(events[0]["event_id"], EVENT_ID)
        self.assertEqual(events[0]["summary"], SUMMARY)
        self.assertIn(EVENT_ID, text)
        self.assertIn(SUMMARY, text)

        _text, off_payload = adapter.build_prompt(_request(self._world()))
        self.assertNotIn("ambient", off_payload["situation_context"]["world"])


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""I-r3: the product really wires M0 into the decision -- configured, and not.

``world-daily-i-result.md`` §4 claimed ``assemble_autonomous_life()`` never passed
``experience_provider``.  That is true only of the *test* base's ``assemble()``; the product path
does wire it:

* ``autonomous_life_resident._memory_ports()`` builds the admission consumer and the bounded
  experience view from the configured Memory database, and returns ``(None, None, None)`` when
  none is configured;
* ``assemble_autonomous_life(..., experience_provider=...)`` hands that value to
  ``AutonomousLifeCycle(...)``;
* ``AutonomousLifeCycle.consideration_context()`` is exactly what a round passes to AG-1 as
  ``prior_experiences`` (``autonomous_life_cycle.py`` ``prior_experiences=consideration``).

This module tests the two ends of that chain for real -- the resident's own port builder and the
cycle's own consideration -- with the real M0 owner on disk:

1. Memory configured: an admitted World consequence is what ``consideration_context()`` carries
   into the request, in the model-facing ``prompt_view`` shape;
2. Memory not configured: the ports are ``(None, None, None)`` and the request's
   ``prior_experiences`` is empty (the empty arm of the same control).

No M0 write is faked, no ref is invented, and the fixture directories are kept.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "native"),
              str(ROOT / "components" / "native" / "src"),
              str(ROOT / "components" / "native" / "memory_runtime_v1"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import autonomous_life_cycle as alc  # noqa: E402
import autonomous_life_resident as resident  # noqa: E402
from test_life_continuity_inputs import MemoryFixture  # noqa: E402
from test_world_daily_life_world_memory_text import (  # noqa: E402
    CONSUMED_FACT,
    _fixture_dir,
    world_handoff,
)

SUBJECT = "chiyo"  # the Memory owner the resident resolves by default


def _cycle(experience_provider) -> "alc.AutonomousLifeCycle":
    """A real cycle object, built the way the assembly builds it (same keyword)."""

    state_root = Path(tempfile.mkdtemp(prefix="ir3-wiring-state-",
                                      dir=str(Path("/tmp/nyaw05r.jdK3q"))))
    return alc.AutonomousLifeCycle(SimpleNamespace(state_root=state_root),
                                   experience_provider=experience_provider,
                                   enabled=True, env={})


def _resident_ports(environment: dict) -> tuple:
    """``AutonomousLifeResident._memory_ports`` with only the environment it really reads."""

    holder = SimpleNamespace(env=dict(environment), last_error=None)
    return resident.AutonomousLifeResident._memory_ports(holder)


class ExperienceWiringTest(unittest.TestCase):
    """One chain, two configurations: what the decision request really carries."""

    def test_a_configured_memory_is_what_the_decision_request_carries(self):
        memory = MemoryFixture(_fixture_dir("ir3-wiring-"))
        self.assertEqual(memory.admit(world_handoff()).get("disposition"), "ACCEPT")
        consumer, provider, port = _resident_ports({
            "CHIYO_LIFE_MEMORY_DB": str(memory.evidence_db),
            "CHIYO_LIFE_MEMORY_CONTROLS_DB": str(memory.controls_db),
            "NYA02_MEMORY_SUBJECT": SUBJECT,
        })
        # the resident built the real admission consumer and the real read view
        self.assertIsNotNone(consumer, "no consumer: the product would refuse every handoff")
        self.assertTrue(consumer.enabled)
        self.assertIsNotNone(consumer.port)
        self.assertIsNotNone(provider)
        self.assertIsNotNone(port)
        carried = _cycle(provider).consideration_context()
        world_rows = [item for item in carried if CONSUMED_FACT in str(item.get("text"))]
        self.assertTrue(world_rows, carried)
        row = world_rows[0]
        # the model-facing shape the resident's lambda hands over: no arbiter-only columns
        self.assertEqual(set(row), {"experience_id", "occurred_at", "text", "activity_ref",
                                    "artifact_ref", "version_ref", "owner_result"}, row)
        self.assertIsNone(row["artifact_ref"])
        self.assertTrue(str(row["owner_result"]).startswith("rset:"))

    def test_no_memory_configured_leaves_the_request_without_experiences(self):
        self.assertEqual(_resident_ports({}), (None, None, None))
        self.assertEqual(_cycle(None).consideration_context(), [])
        # and a provider that fails is bounded the same way: no exception, no invented history
        def broken():
            raise RuntimeError("M0 read failed")

        self.assertEqual(_cycle(broken).consideration_context(), [])


if __name__ == "__main__":
    unittest.main()

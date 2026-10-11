#!/usr/bin/env python3
"""B8-6A: her own committed version must not wake her again.

The defect the soak found
-------------------------
`ArtifactChangeFeed` already knew how to be told about a head *we* wrote -- `note_own_write()` --
but nothing in the live path ever called it.  So the feed's cursor only moved when it polled, and
the sequence was: poll (remember V1) -> she commits V2 -> poll (V2 != V1) -> a wake about her own
work.  One autonomous write became the next autonomous wake, stopped only by the daily budget.

What this checks
----------------
1. she commits V2, the feed observes V2, and no wake is produced for it;
2. another legal writer's V3 produces exactly one wake;
3. repeated polling adds no further wake;
4. a restart on the same state root does not re-wake for the same version;
5. the LR-4 resume-eligibility feed still wakes (the fix must not be a blanket mute);
6. the loop no longer needs the budget to stop it -- the own write produces no event at all.

A negative control runs the same sequence *without* the recorder, and must produce the wake: that
is what makes this a fix rather than a coincidence.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent            # components/
LIFE_SCRIPTS = ROOT / "life" / "scripts"
NATIVE = ROOT / "native"
for _path in (str(LIFE_SCRIPTS), str(NATIVE), str(NATIVE / "src"),
              str(NATIVE / "memory_runtime_v1"), str(ROOT.parent / "chiyo_bundle")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

FAILURES: list[str] = []


def check(condition: bool, label: str, detail: object = "") -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} :: {detail}")
        FAILURES.append(f"{label} :: {detail}")


class _Owner:
    """A stand-in artifact owner: one artifact whose head only moves when a writer moves it."""

    def __init__(self, artifact_id: str, version_id: str) -> None:
        self.artifact_id = artifact_id
        self.version_id = version_id

    def list_artifacts(self):
        return [{"artifact_id": self.artifact_id, "lifecycle": "ACTIVE", "revision": 1}]

    def read_head(self, artifact_id):
        return {"version_id": self.version_id, "version_number": 1, "created_at": "2026-10-11T00:00:00Z"}

    def write(self, version_id: str) -> None:
        """A commit by whoever this owner is speaking to."""
        self.version_id = version_id


def main() -> int:
    import autonomous_life_events as events
    import tempfile

    root = Path(tempfile.mkdtemp(prefix="b86a-feed-"))
    artifact = "artifact:soak-work"
    owner = _Owner(artifact, "v1")
    feed = events.ArtifactChangeFeed(owner, root, subject_id="chiyo-soak")

    print("[1] her own commit is observed, and wakes nobody")
    check(feed.poll() == [], "the first sighting is remembered, not announced")
    feed.note_own_write(artifact, "v2")     # what the cycle now does when the owner confirms
    owner.write("v2")                       # the owner really holds V2
    check(feed.poll() == [], "the version we committed is not an event to ourselves",
          "own write woke us")

    print("[2] another writer's version is exactly one event")
    owner.write("v3")
    first = feed.poll()
    check([e["kind"] for e in first] == [events.EVENT_ARTIFACT_CHANGED],
          "an external commit produces one artifact event", first)
    check(len(first) == 1, "and only one", first)

    print("[3] repeated polling adds nothing")
    check(feed.poll() == [] and feed.poll() == [],
          "polling again with no new head opens no window")

    print("[4] a restart does not re-wake for the same version")
    restarted = events.ArtifactChangeFeed(owner, root, subject_id="chiyo-soak")
    check(restarted.poll() == [],
          "a new process on the same state root sees the current head as already known")
    owner.write("v4")
    check(len(restarted.poll()) == 1, "and still notices the next real change")

    print("[5] the LR-4 resume-eligibility feed still wakes")
    import autonomous_life_events as ev
    eligibility = {"eligibility_id": "elig:1", "status": "OPEN", "opened_at": "2026-10-11T00:00:00Z"}

    class _Coordinator:
        def list_eligibilities(self, only_open=False):
            return [dict(eligibility)]

    resume = ev.ResumeEligibilityFeed(_Coordinator(), root)
    opened = resume.poll()
    check([e["kind"] for e in opened] == [ev.EVENT_RESUME_ELIGIBILITY_OPENED],
          "a newly OPEN eligibility is still one event", opened)
    check(resume.poll() == [], "and the same one does not fire twice")

    print("[6] the loop no longer depends on the budget to stop")
    owner2 = _Owner("artifact:other", "v1")
    silent = events.ArtifactChangeFeed(owner2, root, subject_id="chiyo-soak")
    silent.poll()
    silent.note_own_write("artifact:other", "v2")
    owner2.write("v2")
    check(silent.poll() == [],
          "after an own write there is no event left to drive the next round")

    print("[7] negative control: without the recorder the same sequence wakes")
    owner3 = _Owner("artifact:control", "v1")
    control = events.ArtifactChangeFeed(owner3, root, subject_id="chiyo-soak")
    control.poll()
    owner3.write("v2")                      # no note_own_write: the old behaviour
    woke = control.poll()
    check([e["kind"] for e in woke] == [events.EVENT_ARTIFACT_CHANGED],
          "the wake really was caused by the missing recorder, not by chance", woke)

    print("[8] the live wiring exists (drift guard)")
    assembly = (LIFE_SCRIPTS / "autonomous_life_assembly.py").read_text(encoding="utf-8")
    cycle = (LIFE_SCRIPTS / "autonomous_life_cycle.py").read_text(encoding="utf-8")
    check("cycle.own_write_recorder = artifact_feed.note_own_write" in assembly,
          "the assembly hands the feed's own-write recorder to the cycle")
    check("self.own_write_recorder(str(artifact_id or \"\"), str(version_id))" in cycle,
          "the cycle calls it with the version the owner confirmed")

    print()
    if FAILURES:
        print(f"B8_6A_CHECK: FAIL ({len(FAILURES)} checks)")
        for item in FAILURES:
            print("  -", item)
        return 1
    print("B8_6A_CHECK: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

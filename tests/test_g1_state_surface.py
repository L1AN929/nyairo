"""G1 状态面：`one_day_life status` 的 Activity 与阻塞取证必须来自权威。

本文件只验证读侧：

* 前台 Activity 取自 ``canonical_life_state.json``（权威），不是 resident summary；
  summary 里没有 ``last_tick``，所以旧实现的 WAITING/BLOCKED 分支根本不可达。
* 最近阻塞窗口取自 ``resident_journal.jsonl`` 的有界尾部；末尾的 NOT_DUE/QUIET 静默
  既不是动作也不是恢复。
* 权威缺失/损坏/不可读时，basis 具名未核实，不输出「no open Activity」，status 不创建
  任何文件、目录或锁。

边界：这里的 fixture 是按真实落盘形状手写的**文件替身**（真实的 ``CanonicalActivityStore``
与 ``AutonomousLifeResident`` 写的 key/状态词），不是真实 owner、不是真实模型、不构成 G1
任何场景通过。所有 fixture 只在本测试新建的唯一临时根里，跑完保留不清理。

需要 POSIX（fcntl/AF_UNIX 的既有模块在导入期就要它们）；原生 Windows 自动跳过。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"), str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge"),
              str(ROOT / "components" / "supply")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import one_day_life as odl

posix_only = unittest.skipIf(
    os.name == "nt", "需要 POSIX 文件锁与 AF_UNIX（在 WSL/Linux 上运行）"
)

CANONICAL_SCHEMA = "chiyo.life.canonical_state.v1"
ACTIVITY_REF = "actv_g1state0000000000000001"


def _new_root(prefix: str = "g1-state-surface-") -> Path:
    """A fresh unique temp root.  Kept on disk on purpose: fixture evidence is not cleaned."""

    return Path(tempfile.mkdtemp(prefix=prefix))


def _write_instance(root: Path) -> None:
    payload = {"schema_version": odl.SCHEMA_VERSION, "kind": odl.KIND,
               "subject_id": "subject:g1state-test", "instance": str(root)}
    (root / "instance.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                        encoding="utf-8")


def _state_root(root: Path) -> Path:
    return root / "life" / "state"


def _canonical_document(*, ref: str | None, status: str | None = None,
                        revision: int = 15) -> dict:
    activities: dict[str, dict] = {}
    if ref is not None and status is not None:
        activities[ref] = {"activity_id": ref, "status": status,
                           "activity_kind": "GO_TO_OBSERVED_PLACE",
                           "occurred_at": "2026-10-22T22:11:02+08:00"}
    return {"schema_version": CANONICAL_SCHEMA, "subject_id": "subject:g1state-test",
            "revision": revision, "foreground_activity_ref": ref,
            "last_transition_ref": "trn_g1state", "last_transition_hash": "h",
            "activities": activities}


def _resident_summary() -> dict:
    """The real summary shape: ``last_tick_at`` exists, ``last_tick`` does not."""

    return {"kind": "autonomous_life_resident", "state_root": "fixture",
            "running": False, "ticks": 334,
            "last_tick_at": "2026-10-22T15:51:34+00:00", "poll_seconds": 180,
            "counters": {}}


def _window(*, tick: int, at: str, status_class: str, status: str,
            detail: str = "", outcome: str | None = None) -> dict:
    return {"at": at, "tick": tick,
            "due": {"at": at, "kind": "NOT_DUE", "status": status,
                    "status_class": status_class, "detail": detail,
                    "outcome": outcome, "llm_calls": 0,
                    "extra": {"waiting": {}}}}


def _not_due(*, tick: int = 334, at: str = "2026-10-22T15:51:34+00:00") -> dict:
    return _window(tick=tick, at=at, status_class="silence", status="NOT_DUE",
                   detail="nothing is due yet")


def _prepare(*, canonical: dict | None, summary: dict | None = None,
             journal: list[dict] | None = None) -> tuple[Path, Path]:
    root = _new_root()
    _write_instance(root)
    state = _state_root(root)
    state.mkdir(parents=True)
    if canonical is not None:
        (state / odl.CANONICAL_ACTIVITY_STATE_FILENAME).write_text(
            json.dumps(canonical, ensure_ascii=False, indent=1, sort_keys=True),
            encoding="utf-8")
    (state / "resident_summary.json").write_text(
        json.dumps(summary if summary is not None else _resident_summary(),
                   ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8")
    if journal is not None:
        (state / "resident_journal.jsonl").write_text(
            "\n".join(json.dumps(rec, ensure_ascii=False, sort_keys=True) for rec in journal) + "\n",
            encoding="utf-8")
    return root, state


def _snapshot(root: Path) -> list[tuple[str, int, int, bool]]:
    out: list[tuple[str, int, int, bool]] = []
    for base, dirs, files in os.walk(root):
        dirs.sort()
        files.sort()
        for name in dirs + files:
            path = Path(base) / name
            stat = path.lstat()
            out.append((str(path.relative_to(root)), stat.st_size, stat.st_mtime_ns,
                        path.is_dir()))
    return sorted(out)


@posix_only
class G1StateSurfaceTest(unittest.TestCase):
    def test_a_paused_foreground_activity_is_never_reported_as_idle(self):
        root, state = _prepare(
            canonical=_canonical_document(ref=ACTIVITY_REF, status="PAUSED"),
            journal=[_not_due()])
        before = _snapshot(root)

        view = odl.status(root)

        state_view = view["state"]
        self.assertEqual(state_view["state_class"], odl.STATE_WAITING)
        self.assertNotEqual(state_view["state_class"], odl.STATE_IDLE)
        self.assertIn(ACTIVITY_REF, state_view["basis"])
        self.assertIn("PAUSED", state_view["basis"])
        self.assertIn("canonical revision 15", state_view["basis"])
        self.assertNotIn("no open Activity", state_view["basis"])
        self.assertEqual(state_view["source"], "canonical_life_state.json")
        self.assertEqual(_snapshot(root), before)

    def test_an_active_or_waiting_foreground_is_reported_with_its_own_status(self):
        for activity_status in ("ACTIVE", "WAITING"):
            with self.subTest(activity_status=activity_status):
                root, _ = _prepare(
                    canonical=_canonical_document(ref=ACTIVITY_REF, status=activity_status),
                    journal=[_not_due()])
                state_view = odl.status(root)["state"]
                self.assertEqual(state_view["state_class"], odl.STATE_WAITING)
                self.assertIn(ACTIVITY_REF, state_view["basis"])
                self.assertIn(activity_status, state_view["basis"])
                self.assertNotIn("no open Activity", state_view["basis"])

    def test_a_read_authority_with_no_foreground_is_a_legitimate_idle(self):
        root, _ = _prepare(
            canonical=_canonical_document(ref=None, revision=4),
            journal=[_not_due()])
        state_view = odl.status(root)["state"]
        self.assertEqual(state_view["state_class"], odl.STATE_IDLE)
        self.assertIn("canonical revision 4", state_view["basis"])
        self.assertIn("names no foreground Activity", state_view["basis"])
        self.assertIn("silent windows only", state_view["basis"])
        self.assertNotIn("no open Activity", state_view["basis"])
        self.assertEqual(state_view["source"],
                         "canonical_life_state.json + resident_journal.jsonl")

    def test_missing_corrupt_or_unreadable_authority_is_named_unknown(self):
        cases = ("absent", "corrupt", "unreadable")
        for case in cases:
            with self.subTest(case=case):
                root, _ = _prepare(canonical=None, journal=[_not_due()])
                state = _state_root(root)
                path = state / odl.CANONICAL_ACTIVITY_STATE_FILENAME
                if case == "corrupt":
                    path.write_text("{not json", encoding="utf-8")
                elif case == "unreadable":
                    # A directory where the authority file belongs: every reader gets OSError,
                    # including root, so this is a real unreadable shape rather than a chmod.
                    path.mkdir()
                before = _snapshot(root)

                view = odl.status(root)

                state_view = view["state"]
                expected_code = {"absent": "CANONICAL_STATE_ABSENT",
                                 "corrupt": "CANONICAL_STATE_CORRUPT",
                                 "unreadable": "CANONICAL_STATE_UNREADABLE"}[case]
                self.assertIn(expected_code, state_view["basis"])
                self.assertIn("does not claim that no Activity is open", state_view["basis"])
                self.assertNotIn("no open Activity", state_view["basis"])
                self.assertEqual(state_view["state_class"], odl.STATE_IDLE)
                read = odl.activity_view(state)
                self.assertEqual(read["status"], "UNVERIFIED")
                self.assertEqual(read["reason_code"], expected_code)
                # Nothing created: no run/, no lock, no store, no new file at all.
                self.assertEqual(_snapshot(root), before)
                self.assertFalse((state / "run").exists())
                self.assertFalse((state / "run" / "writer.lock").exists())

    def test_the_last_blocked_window_is_read_from_the_journal_not_from_last_tick(self):
        blocked = _window(tick=332, at="2026-10-22T15:49:32+00:00", status_class="blocked",
                          status="BLOCKED", detail="budget refused the wake")
        root, _ = _prepare(canonical=_canonical_document(ref=None, revision=4),
                           journal=[blocked, _not_due(tick=333, at="2026-10-22T15:50:33+00:00"),
                                    _not_due(tick=334, at="2026-10-22T15:51:34+00:00")])
        state_view = odl.status(root)["state"]
        self.assertEqual(state_view["state_class"], odl.STATE_BLOCKED)
        self.assertIn("2026-10-22T15:49:32+00:00", state_view["basis"])
        self.assertIn("budget refused the wake", state_view["basis"])
        self.assertEqual(state_view["source"], "resident_journal.jsonl")
        # The trailing silence is not reported as a recovery.
        self.assertNotIn("recovered", state_view["basis"])

        acted = _window(tick=335, at="2026-10-22T16:00:00+00:00", status_class="acted",
                        status="ACTED", outcome="ACTED")
        root2, _ = _prepare(canonical=_canonical_document(ref=None, revision=4),
                            journal=[blocked, acted, _not_due(tick=336)])
        after_real_action = odl.status(root2)["state"]
        self.assertEqual(after_real_action["state_class"], odl.STATE_IDLE)
        self.assertIn("ended as acted", after_real_action["basis"])
        self.assertNotIn("blocked", after_real_action["basis"])

    def test_existing_precedence_and_two_argument_callers_are_unchanged(self):
        root, _ = _prepare(canonical=_canonical_document(ref=ACTIVITY_REF, status="PAUSED"),
                           journal=[_not_due()])
        view = odl.status(root)
        for key in ("instance", "checked_at", "read_only", "subject_id", "life_state_root",
                    "resident", "pause", "control", "work", "state", "room", "reading",
                    "private_items", "situation", "continuity"):
            self.assertIn(key, view)
        for key in ("state_class", "basis", "source"):
            self.assertIn(key, view["state"])

        paused = odl.classify_state({}, {"paused": True, "reason_code": "operator"})
        self.assertEqual(paused["state_class"], odl.STATE_PAUSED)
        paused_running = odl.classify_state({"kind": "x", "running": True},
                                            {"paused": True, "reason_code": "operator"})
        self.assertEqual(paused_running["state_class"], odl.STATE_PAUSED)
        stopped = odl.classify_state({}, {"paused": False})
        self.assertEqual(stopped["state_class"], odl.STATE_STOPPED)
        stopped_absent = odl.classify_state({"kind": "x", "present": False}, {"paused": False})
        self.assertEqual(stopped_absent["state_class"], odl.STATE_STOPPED)
        running = odl.classify_state({"kind": "x", "running": True}, {"paused": False})
        self.assertEqual(running["state_class"], odl.STATE_RUNNING)

        # A two-argument caller supplied no reads: the basis says exactly that, and it does not
        # claim there is no open Activity.
        legacy = odl.classify_state(_resident_summary(), {"paused": False})
        self.assertEqual(legacy["state_class"], odl.STATE_IDLE)
        self.assertIn("was not read by this caller", legacy["basis"])
        self.assertNotIn("no open Activity", legacy["basis"])


if __name__ == "__main__":
    unittest.main()

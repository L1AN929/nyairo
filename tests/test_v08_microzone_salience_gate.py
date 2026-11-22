#!/usr/bin/env python3
"""Worker 8C: the microzone Background-Silence / Change-Salience gate.

v3.0 Ch.33 §五 and v0.4 Ch.41 §六 say a calm, comfortable, quiet indoor microzone is
background noise -- it must inject **zero** characters into the lived context.  A
phrase appears only when the room genuinely changed how it feels to sit there: hard
glare / dim light at the active spot, rain splashing in, a cold draft, a real
(warm food/drink) aroma cue, or a preserved work breakpoint the caller is allowed to
see.  A private breakpoint never leaks under a restricted (partner) projection.

The suite is deliberately resilient: Worker 8A lands ``v08_microzone_ecology``
concurrently, so the engine is never imported eagerly.  The gate/format/hint/dispatch
invariants always run against in-memory mappings and a stub engine.

Run (WSL): ``python -B -m unittest tests.test_v08_microzone_salience_gate -v``.
"""

from __future__ import annotations

import contextlib
import hashlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "components" / "phone"),
    str(ROOT / "tests"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import v02_v03_daily_loop_bridge as bridge  # noqa: E402
from chiyo_bundle import lived_context_bridge as lcb  # noqa: E402
from chiyo_bundle import phone_tool  # noqa: E402

T0 = "2026-11-16T09:00:00+08:00"
MICROZONE_WORDS = ("刺眼", "有点暗", "雨点", "冷风", "飘着", "还没收尾")


def _cfg(day_root: Path, *, private: bool = True) -> dict:
    return {
        "day_root": str(day_root),
        "memory": True,
        "memory_tool_policy": "restricted",
        "phone": {"enabled": True, "private_artifacts": private},
    }


def _doc(microzone, traces=None):
    return {
        "perspective": "subject",
        "active_traces": traces or [],
        "routines": [],
        "matter_hints": [],
        "microzone_ecology": microzone,
    }


class _StubEcology(types.ModuleType):
    """Stand-in for Worker 8A's ``v08_microzone_ecology`` engine."""

    def __init__(self, view):
        super().__init__("components.world.scripts.v08_microzone_ecology")
        self._view = dict(view or {})
        self.read_calls = 0
        self.adjust_calls: list[dict] = []

    def read_microzone_ecology_view(self, root, **kwargs):  # noqa: D401 - trivial
        self.read_calls += 1
        return dict(self._view)

    def adjust_microzone_comfort(self, root, fields, *, actor_side, now_iso):
        self.adjust_calls.append(
            {"fields": dict(fields), "actor_side": actor_side, "now_iso": now_iso}
        )
        return {"status": "applied", "already_applied": False}


@contextlib.contextmanager
def _install_stub(stub):
    """Register the stub under every import path the bridge tries.

    ``from components.world.scripts import v08_microzone_ecology`` resolves a package
    *attribute* once set, so merely patching ``sys.modules`` would let a later test
    reuse a stale stub.  Patching the package attribute (``create=True``) restores it
    correctly on exit.
    """

    with contextlib.ExitStack() as stack:
        stack.enter_context(
            mock.patch.dict(
                sys.modules,
                {
                    "components.world.scripts.v08_microzone_ecology": stub,
                    "v08_microzone_ecology": stub,
                },
            )
        )
        for pkg_name in ("components.world.scripts", "components"):
            try:
                pkg = __import__(pkg_name, fromlist=["v08_microzone_ecology"])
                stack.enter_context(
                    mock.patch.object(
                        pkg, "v08_microzone_ecology", stub, create=True
                    )
                )
            except Exception:  # noqa: BLE001 - package layout may differ; name patch still works
                pass
        yield stub


def _snapshot(root: Path) -> dict:
    snap = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root)).replace("\\", "/")
        if path.is_file() and not path.is_symlink():
            stat = path.lstat()
            snap[rel] = (
                int(stat.st_size),
                int(stat.st_mtime_ns),
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        else:
            snap[rel] = ("dir",)
    return snap


class BackgroundSilenceTest(unittest.TestCase):
    """A quiet, comfortable, calm microzone emits exactly 0 characters."""

    def test_quiet_formatter_is_empty(self):
        quiet = {
            "is_quiet_background": True,
            "spot_comfort": "comfortable",
            "spot_mood": "calm",
            "light_quality": "comfortable",
            "draft_state": "none",
            "aroma_cue_cn": "",
        }
        self.assertEqual(
            lcb._format_microzone_salience_snippet(quiet, include_private=True), ""
        )
        self.assertEqual(
            lcb._format_v08_living_traces_micro(_doc(quiet), include_private=True), ""
        )

    def test_quiet_build_context_is_byte_identical_to_absent(self):
        tmp = tempfile.TemporaryDirectory(prefix="v08mz-quiet-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "phone").mkdir()
        (root / "world").mkdir()

        stub = _StubEcology(
            {
                "is_quiet_background": True,
                "spot_comfort": "comfortable",
                "spot_mood": "calm",
                "light_quality": "comfortable",
                "draft_state": "none",
                "aroma_cue_cn": "",
            }
        )
        out_absent = lcb.build_lived_turn_context(root, _cfg(root))

        (root / "world" / "v08_microzone_state.json").write_text("{}", encoding="utf-8")
        before = _snapshot(root)
        with _install_stub(stub):
            out_quiet = lcb.build_lived_turn_context(root, _cfg(root))
        self.assertEqual(_snapshot(root), before, "build_lived_turn_context must be zero-write")
        # 0 extra characters injected: identical to having no microzone at all.
        self.assertEqual(out_quiet, out_absent)
        for word in MICROZONE_WORDS:
            self.assertNotIn(word, out_quiet)


class SalientCueTest(unittest.TestCase):
    """Only genuinely salient conditions produce a concise phrase."""

    def test_glare_cue(self):
        mz = {"light_quality": "glare", "spot_comfort": "comfortable", "spot_mood": "calm"}
        self.assertIn("刺眼", lcb._format_microzone_salience_snippet(mz, include_private=True))

    def test_dim_cue(self):
        mz = {"light_quality": "dim"}
        self.assertIn("暗", lcb._format_microzone_salience_snippet(mz, include_private=True))

    def test_rain_splash_cue(self):
        mz = {"draft_state": "rain_splash"}
        self.assertIn("雨点", lcb._format_microzone_salience_snippet(mz, include_private=True))

    def test_chilly_draft_cue(self):
        mz = {"draft_state": "chilly_draft"}
        self.assertIn("冷风", lcb._format_microzone_salience_snippet(mz, include_private=True))

    def test_real_aroma_cue(self):
        mz = {"aroma_cue_cn": "刚煮好的热汤"}
        self.assertIn("热汤", lcb._format_microzone_salience_snippet(mz, include_private=True))

    def test_no_signal_even_when_not_flagged_quiet(self):
        # Without any real signal there is still nothing to say.
        self.assertEqual(
            lcb._format_microzone_salience_snippet(
                {"spot_comfort": "comfortable", "spot_mood": "calm"},
                include_private=True,
            ),
            "",
        )

    def test_glare_build_context_within_budget(self):
        tmp = tempfile.TemporaryDirectory(prefix="v08mz-glare-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "phone").mkdir()
        (root / "world").mkdir()
        (root / "world" / "v08_microzone_state.json").write_text("{}", encoding="utf-8")
        stub = _StubEcology(
            {"active_spot_id": "window_side", "light_quality": "glare", "draft_state": "none"}
        )
        with _install_stub(stub):
            rendered = lcb.build_lived_turn_context(root, _cfg(root))
        self.assertLessEqual(len(rendered), 1800)
        self.assertIn("刺眼", rendered)


class PrivateBreakpointTest(unittest.TestCase):
    """A private work breakpoint is withheld under a restricted projection."""

    def _mz(self, visibility="private"):
        return {
            "light_quality": "comfortable",
            "spot_comfort": "comfortable",
            "spot_mood": "calm",
            "continuity_preserved": True,
            "preserved_breakpoint_cn": "第三节的收尾",
            "visibility": visibility,
        }

    def test_private_breakpoint_withheld_without_permission(self):
        snippet = lcb._format_microzone_salience_snippet(self._mz(), include_private=False)
        self.assertNotIn("第三节", snippet)
        self.assertEqual(snippet, "")

    def test_private_breakpoint_visible_with_permission(self):
        snippet = lcb._format_microzone_salience_snippet(self._mz(), include_private=True)
        self.assertIn("第三节", snippet)

    def test_shared_breakpoint_visible_even_restricted(self):
        snippet = lcb._format_microzone_salience_snippet(
            self._mz(visibility="shared"), include_private=False
        )
        self.assertIn("第三节", snippet)

    def test_private_breakpoint_never_reaches_restricted_build(self):
        tmp = tempfile.TemporaryDirectory(prefix="v08mz-priv-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "phone").mkdir()
        (root / "world").mkdir()
        (root / "world" / "v08_microzone_state.json").write_text("{}", encoding="utf-8")
        stub = _StubEcology(self._mz())
        with _install_stub(stub):
            restricted = lcb.build_lived_turn_context(root, _cfg(root, private=False))
        self.assertNotIn("第三节", restricted)


class PhoneHintTest(unittest.TestCase):
    def test_surface_key_count_is_frozen(self):
        self.assertIn("v08_living_traces", phone_tool.LIFE_UPDATE_SURFACES)
        self.assertEqual(len(phone_tool.LIFE_UPDATE_SURFACES), 37)

    def test_hint_names_adjust_microzone_comfort(self):
        hint = phone_tool._surface_supported_hint("v08_living_traces")
        self.assertIn("adjust_microzone_comfort", hint["subject_actions"])
        self.assertEqual(
            hint["core_fields"]["adjust_microzone_comfort"],
            [
                "window_state",
                "lamp_on",
                "lamp_angle",
                "target_spot_id",
                "posture_cn",
                "preserve_work_ref",
                "preserve_breakpoint_cn",
            ],
        )

    def test_existing_actions_stay_backwards_compatible(self):
        hint = phone_tool._surface_supported_hint("v08_living_traces")
        for action in (
            "execute_universal_verb",
            "apply_living_trace",
            "clear_living_trace",
            "save_personal_routine",
            "run_personal_routine",
            "retire_personal_routine",
            "compose_open_act",
            "respond_proposed_use",
            "preview_placement",
        ):
            self.assertIn(action, hint["subject_actions"])
        self.assertEqual(hint["core_fields"]["compose_open_act"], ["intent_cn", "steps"])


class DailyLoopDispatchTest(unittest.TestCase):
    """The bridge maps and settles a microzone-comfort shift with zero LLM calls."""

    def test_event_descriptor_maps_to_adjust_microzone(self):
        event = bridge._v02v03_event_from_descriptor(
            "cand:v08_microzone_shift",
            {
                "source_ref": "v08_microzone:shift_from_glare_or_rain",
                "action_family": "adjust_microzone_comfort",
                "target_spot_id": "desk_inner",
                "shift_reason": "glare",
                "preserve_work_ref": "work:essay_3",
                "preserve_breakpoint_cn": "第三段",
            },
        )
        self.assertIsNotNone(event)
        self.assertEqual(event["action_kind"], "ADJUST_MICROZONE")
        self.assertEqual(event["target_spot_id"], "desk_inner")
        self.assertEqual(event["preserve_work_ref"], "work:essay_3")
        self.assertIn(
            "ADJUST_MICROZONE", bridge._V02_DAILY_SETTLE_HANDLERS
        )
        self.assertIs(
            bridge._V02_DAILY_SETTLE_HANDLERS["ADJUST_MICROZONE"],
            bridge._settle_v08_microzone_adjust,
        )

    def test_settle_when_engine_present(self):
        stub = _StubEcology({})
        event = {
            "action_kind": "ADJUST_MICROZONE",
            "operation_key": "op-mz-1",
            "target_spot_id": "desk_inner",
            "shift_reason": "rain_splash",
            "preserve_work_ref": "work:essay_3",
            "preserve_breakpoint_cn": "第三段",
            "decision_id": "dec-mz-1",
        }
        slices: list[str] = []
        details: dict = {}
        inst_root = Path(tempfile.mkdtemp(prefix="v08mz-inst-"))
        life_root = Path(tempfile.mkdtemp(prefix="v08mz-life-"))
        with mock.patch.object(bridge, "_microzone_ecology_module", lambda: stub):
            out = bridge._settle_v08_microzone_adjust(
                inst_root,
                life_root,
                event,
                now_iso=T0,
                slices_updated=slices,
                details=details,
            )
        self.assertIsNone(out)
        self.assertEqual(len(stub.adjust_calls), 1)
        call = stub.adjust_calls[0]
        self.assertEqual(call["actor_side"], "subject")
        self.assertEqual(call["fields"]["target_spot_id"], "desk_inner")
        self.assertEqual(call["fields"]["preserve_work_ref"], "work:essay_3")
        self.assertTrue(call["fields"]["operation_key"].startswith("op-mz-1"))
        self.assertIn("world.v08_microzone_state", slices)
        self.assertEqual(details["v08_microzone_adjust"]["status"], "applied")

    def test_settle_refuses_cleanly_without_engine(self):
        event = {"operation_key": "op-mz-2", "target_spot_id": "desk_inner"}
        with mock.patch.object(bridge, "_microzone_ecology_module", lambda: None):
            out = bridge._settle_v08_microzone_adjust(
                Path(tempfile.mkdtemp(prefix="v08mz-inst-")),
                Path(tempfile.mkdtemp(prefix="v08mz-life-")),
                event,
                now_iso=T0,
                slices_updated=[],
                details={},
            )
        self.assertIsNotNone(out)
        self.assertEqual(out["status"], "REFUSED")
        self.assertEqual(out["reason_code"], "V08_MICROZONE_UNAVAILABLE")
        self.assertEqual(out["llm_calls"], 0)


class MicrozoneCandidateHookTest(unittest.TestCase):
    """The daily-loop scan offers a shift only at a genuinely salient window."""

    def _collect_with_view(self, view):
        tmp = tempfile.TemporaryDirectory(prefix="v08mz-scan-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "phone").mkdir()
        (root / "world").mkdir()
        (root / "world" / "v08_microzone_state.json").write_text("{}", encoding="utf-8")
        with mock.patch.object(
            bridge, "_microzone_ecology_module", lambda: object()
        ), mock.patch.object(
            bridge, "_read_microzone_view_zero_write", lambda inst_root: dict(view)
        ):
            return bridge.collect_v02_v03_loop_candidates(
                root, now_iso="2026-11-16T14:00:00+08:00"
            )

    def _microzone_descs(self, collected):
        descs = collected.get("descriptors") or {}
        return [
            (cid, desc)
            for cid, desc in descs.items()
            if str(desc.get("source_ref") or "").startswith("v08_microzone:")
        ]

    def test_glare_at_window_emits_shift_candidate(self):
        collected = self._collect_with_view(
            {
                "active_spot_id": "window_side",
                "light_quality": "glare",
                "draft_state": "calm",
                "preserved_work_ref": "work:e3",
                "preserved_breakpoint_cn": "第三段",
            }
        )
        rows = self._microzone_descs(collected)
        self.assertEqual(len(rows), 1)
        _cid, desc = rows[0]
        self.assertEqual(desc["action_family"], "adjust_microzone_comfort")
        self.assertEqual(desc["target_spot_id"], "desk_inner")
        self.assertEqual(desc["availability_basis"], "V08_MICROZONE_SALIENT_CONDITION")
        self.assertEqual(desc["preserve_work_ref"], "work:e3")
        self.assertEqual(desc["preserve_breakpoint_cn"], "第三段")
        self.assertFalse(desc["requires_outing"])
        # It round-trips to a real ADJUST_MICROZONE event.
        event = bridge._v02v03_event_from_descriptor(_cid, desc)
        self.assertEqual(event["action_kind"], "ADJUST_MICROZONE")

    def test_rain_splash_at_window_emits_shift_candidate(self):
        collected = self._collect_with_view(
            {"active_spot_id": "window_side", "draft_state": "rain_splash"}
        )
        rows = self._microzone_descs(collected)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1]["shift_reason"], "rain_splash")

    def test_quiet_window_emits_nothing(self):
        collected = self._collect_with_view(
            {"active_spot_id": "window_side", "is_quiet_background": True}
        )
        self.assertEqual(self._microzone_descs(collected), [])

    def test_glare_away_from_window_emits_nothing(self):
        collected = self._collect_with_view(
            {"active_spot_id": "desk_inner", "light_quality": "glare"}
        )
        self.assertEqual(self._microzone_descs(collected), [])


class RealEngineCompatibilityTest(unittest.TestCase):
    """If Worker 8A has landed, the real engine accepts the bridge's payload shape."""

    def _real_module(self):
        return bridge._microzone_ecology_module()

    def test_real_engine_settles_bridge_payload(self):
        mod = self._real_module()
        if mod is None:
            self.skipTest("v08_microzone_ecology not importable yet")
        root = Path(tempfile.mkdtemp(prefix="v08mz-real-"))
        slices: list[str] = []
        details: dict = {}
        event = {
            "operation_key": "op-real-mz",
            "target_spot_id": "desk_inner",
            "shift_reason": "glare",
            "preserve_work_ref": "work:essay_3",
            "preserve_breakpoint_cn": "第三段",
        }
        # No stub: exercise the actual engine end to end.
        out = bridge._settle_v08_microzone_adjust(
            root,
            root,
            event,
            now_iso=T0,
            slices_updated=slices,
            details=details,
        )
        self.assertIsNone(out, f"real engine refused: {out}")
        self.assertIn("world.v08_microzone_state", slices)
        self.assertEqual(details["v08_microzone_adjust"]["status"], "applied")


if __name__ == "__main__":
    unittest.main()

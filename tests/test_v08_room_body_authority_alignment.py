#!/usr/bin/env python3
"""Round A2 — room/window & body-sensation authority alignment.

Covers the two split-brain projections converged onto their upstream owners:

* ``v08_microzone_ecology`` reads the phone's ``phone/somatic_comfort.json``
  window/zone declaration and the World substrate's measured ``pose``
  (``data/world/body_substrate.json``) as upstream truth, while every read
  stays 100% zero-write and never resets persisted clocks/TTLs.
* ``v08_composable_body`` qualitative sensation/hint copy passes the
  ``body_observation._assert_raw_free`` firewall and keeps ``created_at`` /
  ``expires_at`` and the private default-off / wake-only boundaries intact.

Run (WSL):
``/tmp/rc104-venv/bin/python -B -m unittest tests.test_v08_room_body_authority_alignment -v``
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "tests"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from components.world.scripts import body_observation  # noqa: E402
from components.world.scripts import v08_composable_body as body_engine  # noqa: E402
from components.world.scripts import v08_microzone_ecology as microzone  # noqa: E402

T0 = "2026-10-16T09:00:00+08:00"
T1 = "2026-10-16T10:00:00+08:00"
T2 = "2026-10-16T11:00:00+08:00"
TZ = timezone(timedelta(hours=8))


def _at(minutes: int, base: str = T0) -> str:
    return (datetime.fromisoformat(base) + timedelta(minutes=minutes)).isoformat(
        timespec="seconds"
    )


def _snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    snap: dict[str, tuple[int, int, str]] = {}
    if not root.exists():
        return snap
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root)).replace("\\", "/")
        stat = path.lstat()
        if path.is_file() and not path.is_symlink():
            snap[rel] = (
                int(stat.st_size),
                int(stat.st_mtime_ns),
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        else:
            snap[rel] = (0, int(stat.st_mtime_ns), "dir")
    return snap


def _write_somatic(
    root: Path,
    *,
    window_state: str | None,
    window_updated_at: str | None = T0,
    zone: str | None = None,
) -> None:
    path = root / "phone" / "somatic_comfort.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "revision": 1,
                "updated_at": window_updated_at,
                "window": {
                    "state": window_state,
                    "updated_at": window_updated_at,
                },
                "local_position": {
                    "zone": zone,
                    "bound_place_id": "home" if zone else None,
                    "bound_area_id": "home_area" if zone else None,
                    "declared_at": window_updated_at if zone else None,
                },
                "garment": {"garment_id": None, "state": None, "dryness": None},
                "care": {"drink": None, "cushion": None},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_pose(root: Path, pose: str) -> None:
    path = root / "data" / "world" / "body_substrate.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "world.body.substrate.v1.3",
                "kind": "world_body_substrate",
                "revision": 4,
                "world_id": "shiomi_city",
                "body_id": "chiyo_body",
                "world_entity_id": "chiyo_body",
                "pose": pose,
                "orientation": "south",
                "placements": {},
                "reservations": {},
                "consumptions": {},
                "fixtures": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_microzone_state(root: Path, **overrides) -> None:
    path = microzone.state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "schema_version": 1,
        "revision": 3,
        "created_at": "2026-10-16T08:00:00+08:00",
        "updated_at": T1,
        "window_state": "closed",
        "lamp_on": False,
        "lamp_angle": "direct",
        "active_spot_id": "desk_inner",
        "posture_cn": "正常坐着",
        "preserved_work_ref": None,
        "preserved_breakpoint_cn": None,
        "continuity_preserved": False,
        "doorstep_sound_cue_cn": None,
        "doorstep_sound_at": None,
        "visibility": "private",
        "operations": {},
        # Unknown-to-the-reader clocks: a zero-write read must never touch them.
        "drying": {"until": T2, "sunlight_expires_at": T2},
        "sunlight_expires_at": T2,
    }
    doc.update(overrides)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


class MicrozoneUpstreamAuthorityTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-room-authority-")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_01_fresh_root_reflects_phone_window_and_world_pose_zero_write(self) -> None:
        _write_somatic(self.root, window_state="open", window_updated_at=T0,
                       zone="window_side")
        _write_pose(self.root, "standing")
        before = _snapshot(self.root)

        view = microzone.read_microzone_ecology_view(self.root, now_iso=T0)

        self.assertEqual(view["window_state"], "open")
        self.assertEqual(view["active_spot_id"], "window_side")
        self.assertEqual(view["posture_cn"], "站着")
        self.assertEqual(view["active_conditions"]["draft_state"], "breeze")
        self.assertEqual(_snapshot(self.root), before)
        self.assertFalse(microzone.state_path(self.root).exists())

    def test_02_missing_upstream_keeps_the_honest_defaults(self) -> None:
        view = microzone.read_microzone_ecology_view(self.root, now_iso=T0)
        self.assertEqual(view["window_state"], "closed")
        self.assertEqual(view["active_spot_id"], "desk_inner")
        self.assertEqual(view["posture_cn"], "正常坐着")
        self.assertFalse((self.root / "world").exists())
        self.assertFalse((self.root / "phone").exists())

    def test_03_newer_local_window_wins_and_clocks_are_never_reset(self) -> None:
        _write_microzone_state(self.root, window_state="closed", updated_at=T1)
        _write_somatic(self.root, window_state="open", window_updated_at=T0)
        _write_pose(self.root, "lying")
        before = _snapshot(self.root)

        view = microzone.read_microzone_ecology_view(self.root, now_iso=T0)

        # Local state is newer than the phone's window declaration.
        self.assertEqual(view["window_state"], "closed")
        # Posture only falls back to the World pose when the local state is absent.
        self.assertEqual(view["posture_cn"], "正常坐着")
        self.assertEqual(_snapshot(self.root), before)
        persisted = json.loads(microzone.state_path(self.root).read_text(encoding="utf-8"))
        self.assertEqual(persisted["drying"], {"until": T2, "sunlight_expires_at": T2})
        self.assertEqual(persisted["sunlight_expires_at"], T2)
        self.assertEqual(persisted["updated_at"], T1)

    def test_04_timestamped_upstream_window_newer_than_local_wins(self) -> None:
        _write_microzone_state(self.root, window_state="closed", updated_at=T1)
        _write_somatic(self.root, window_state="ajar", window_updated_at=T2)
        before = _snapshot(self.root)

        view = microzone.read_microzone_ecology_view(self.root, now_iso=T2)

        self.assertEqual(view["window_state"], "ajar")
        self.assertEqual(_snapshot(self.root), before)
        persisted = json.loads(microzone.state_path(self.root).read_text(encoding="utf-8"))
        self.assertEqual(persisted["window_state"], "closed")
        self.assertEqual(persisted["updated_at"], T1)

    def test_05_adjust_syncs_upstream_window_without_claiming_it(self) -> None:
        _write_somatic(self.root, window_state="open", window_updated_at=T0,
                       zone="window_side")
        _write_pose(self.root, "seated")

        receipt = microzone.adjust_microzone_comfort(
            self.root, {"lamp_on": True}, now_iso=T1
        )

        self.assertEqual(receipt["applied_fields"], ["lamp_on"])
        self.assertEqual(receipt["microzone_ecology"]["window_state"], "open")
        self.assertEqual(receipt["microzone_ecology"]["active_spot_id"], "window_side")
        persisted = json.loads(microzone.state_path(self.root).read_text(encoding="utf-8"))
        self.assertEqual(persisted["window_state"], "open")
        self.assertNotIn("window_state", receipt["applied_fields"])


class ComposableBodyQualitativeGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-body-guard-")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    @staticmethod
    def _state(root: Path) -> dict:
        return json.loads(body_engine.state_path(root).read_text(encoding="utf-8"))

    def test_01_sensation_and_hint_pass_raw_free_with_stable_timestamps(self) -> None:
        body_engine.configure_body_pack(
            self.root,
            {
                "pack_id": "menstrual_cycle",
                "enabled": True,
                "anchor_iso": T0,
                "discomfort_profile": "mild_cramps",
            },
            now_iso=T0,
        )
        body_engine.share_body_care_hint_once(
            self.root, {"hint_cn": "想喝点热的", "ttl_minutes": 60}, now_iso=T0
        )
        stored = self._state(self.root)
        hint_before = dict(stored["shared_care_hint"])
        before = _snapshot(self.root)

        first = body_engine.read_composable_body_zero_write(
            self.root, has_subject_capability=True, now_iso=_at(10)
        )
        second = body_engine.read_composable_body_zero_write(
            self.root, has_subject_capability=True, now_iso=_at(20)
        )

        self.assertEqual(
            first["current_feeling_cn"],
            "有轻微腹部不适，仍可按节奏继续手头事或暂歇",
        )
        self.assertEqual(first["current_feeling_cn"], second["current_feeling_cn"])
        body_observation._assert_raw_free(
            {"current_feeling_cn": first["current_feeling_cn"]},
            label="current_feeling_cn",
        )
        self.assertIsNotNone(first["shared_care_hint"])
        body_observation._assert_raw_free(
            {"hint_cn": first["shared_care_hint"]["text_cn"]}, label="hint_cn"
        )
        after = self._state(self.root)
        self.assertEqual(after["shared_care_hint"], hint_before)
        self.assertEqual(after["packs"]["menstrual_cycle"]["anchor_iso"], T0)
        self.assertEqual(_snapshot(self.root), before)

    def test_02_private_pack_default_off_and_wake_only(self) -> None:
        fresh = body_engine.read_composable_body_zero_write(
            self.root, has_subject_capability=True, now_iso=T0
        )
        self.assertFalse(fresh["nocturnal_emission"]["enabled"])
        self.assertEqual(fresh["nocturnal_emission"]["garment_care_note_cn"], "")
        self.assertFalse(fresh["pending_garment_care"])
        self.assertEqual(fresh["nocturnal_emission"]["sleep_activities"], [])

        with self.assertRaises(body_engine.V08ComposableBodyError) as caught:
            body_engine.bind_sleep_activity(
                self.root,
                {
                    "sleep_activity_id": "sleep-1",
                    "started_at_iso": T0,
                    "planned_wake_iso": _at(480),
                },
                now_iso=T0,
            )
        self.assertEqual(caught.exception.reason_code, "PACK_DISABLED")

        body_engine.configure_body_pack(
            self.root,
            {"pack_id": "nocturnal_emission", "enabled": True,
             "probability_permille": 500},
            now_iso=T0,
        )
        body_engine.bind_sleep_activity(
            self.root,
            {
                "sleep_activity_id": "sleep-1",
                "started_at_iso": T0,
                "planned_wake_iso": _at(480),
            },
            now_iso=T0,
        )
        body_engine.settle_sleep_wake(
            self.root,
            {"sleep_activity_id": "sleep-1", "woke_at_iso": _at(20)},
            now_iso=_at(20),
        )
        before = _snapshot(self.root)
        early = body_engine.read_composable_body_zero_write(
            self.root, has_subject_capability=True, now_iso=_at(20)
        )
        blob = json.dumps(early, ensure_ascii=False)
        self.assertNotIn("hidden_scheduled_at_iso", blob)
        self.assertNotIn("sampled_occurred", blob)
        self.assertFalse(early["pending_garment_care"])
        self.assertEqual(_snapshot(self.root), before)

    def test_03_hint_smuggling_raw_telemetry_is_refused(self) -> None:
        with self.assertRaises(body_engine.V08ComposableBodyError) as caught:
            body_engine.share_body_care_hint_once(
                self.root,
                {"hint_cn": '今天 "comfort" 很高'},
                now_iso=T0,
            )
        self.assertEqual(caught.exception.reason_code, "INVALID_FIELDS")
        self.assertFalse(body_engine.state_path(self.root).exists())


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""v0.4 Ch.51 composable optional body packs — deterministic, zero-LLM.

Coverage:
* both packs default OFF, pronoun-decoupled and independent of adult intimacy;
* menstrual clock projection (cycle index, day, active/pre window, supply flag,
  neutral feeling copy), supply use idempotency, disable-keeps-history;
* nocturnal deterministic sampling, hidden-before-realisation, early-wake
  cancellation, single realized event, garment-care resolution;
* explicit confirmed history deletion, voluntary expiring care hint, ordinary
  partner care with zero relationship score;
* the real HTTP surface: zero-write GET, partner stripping, subject-only 403,
  identity/score refusal, CSRF boundary, ``llm_calls == 0``;
* person-neutral copy (no hard-coded pronoun/name).

Run (WSL): ``python -m unittest tests.test_v08_composable_body -v``.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "tests"),
    str(ROOT / "scripts"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from components.world.scripts import v08_composable_body as engine  # noqa: E402
from components.phone import v08_composable_body_view as view  # noqa: E402

T_ANCHOR = "2026-11-01T09:00:00+08:00"
TZ = timezone(timedelta(hours=8))


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _add_days(days: int, base: str = T_ANCHOR) -> str:
    return _iso(datetime.fromisoformat(base) + timedelta(days=days))


def _add_minutes(minutes: int, base: str = T_ANCHOR) -> str:
    return _iso(datetime.fromisoformat(base) + timedelta(minutes=minutes))


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


def _state(root: Path) -> dict[str, Any]:
    return json.loads(engine.state_path(root).read_text(encoding="utf-8"))


def _sample(salt: str, sleep_activity_id: str) -> int:
    digest = hashlib.sha256(f"{salt}|{sleep_activity_id}".encode("utf-8")).hexdigest()
    return int(digest, 16) % 1000


class V08ComposableBodyEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-body-")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- zero-write & defaults ------------------------------------------------

    def test_00_zero_write_reads_never_create_body_dir(self) -> None:
        fresh = self.root / "fresh"
        fresh.mkdir()
        before = _snapshot(fresh)
        subject = engine.read_composable_body_zero_write(
            fresh, has_subject_capability=True, now_iso=T_ANCHOR
        )
        partner = engine.read_composable_body_zero_write(
            fresh, has_subject_capability=False, now_iso=T_ANCHOR
        )
        alias = engine.project_composable_body_zero_write(
            fresh, has_subject_capability=True, now_iso=T_ANCHOR
        )
        self.assertFalse((fresh / "body").exists())
        self.assertEqual(before, _snapshot(fresh))
        self.assertEqual(subject["perspective"], "subject")
        self.assertEqual(partner["perspective"], "partner")
        self.assertEqual(alias["schema"], engine.VIEW_SCHEMA)

    def test_01_defaults_off_decoupled_and_neutral(self) -> None:
        out = engine.read_composable_body_zero_write(
            self.root, has_subject_capability=True, now_iso=T_ANCHOR
        )
        self.assertTrue(out["pronoun_decoupled"])
        self.assertTrue(out["independent_of_adult_intimacy"])
        self.assertEqual(out["packs"]["menstrual_cycle"]["enabled"], False)
        self.assertEqual(out["packs"]["nocturnal_emission"]["enabled"], False)
        self.assertTrue(out["packs"]["nocturnal_emission"]["no_absence_penalty"])
        self.assertFalse(out["packs"]["nocturnal_emission"]["generates_dream_content"])
        self.assertEqual(out["current_feeling_cn"], "")
        self.assertFalse(out["needs_supply_change"])
        self.assertEqual(out["character"], {"display_name": "TA", "pronouns": "TA"})
        blob = json.dumps(out, ensure_ascii=False)
        self.assertNotIn("她", blob)
        self.assertNotIn("千代", blob)

    def test_02_character_pack_pronouns_follow_pack(self) -> None:
        (self.root / "phone").mkdir(parents=True, exist_ok=True)
        (self.root / "phone" / "character_pack_state.json").write_text(
            json.dumps({"pack": {"display_name": "阿岚", "pronouns": "他"}}),
            encoding="utf-8",
        )
        out = engine.read_composable_body_zero_write(
            self.root, has_subject_capability=True, now_iso=T_ANCHOR
        )
        self.assertEqual(out["character"]["pronouns"], "他")
        # Pack state never changes enablement: both still default OFF.
        self.assertFalse(out["packs"]["menstrual_cycle"]["enabled"])
        self.assertFalse(out["packs"]["nocturnal_emission"]["enabled"])

    # -- menstrual ------------------------------------------------------------

    def test_03_menstrual_configure_validation(self) -> None:
        with self.assertRaises(engine.V08ComposableBodyError) as ctx:
            engine.configure_body_pack(
                self.root,
                {"action": "configure_body_pack", "pack_id": "menstrual_cycle",
                 "enabled": True},
                now_iso=T_ANCHOR,
            )
        self.assertEqual(ctx.exception.reason_code, "INVALID_FIELDS")
        with self.assertRaises(engine.V08ComposableBodyError) as ctx:
            engine.configure_body_pack(
                self.root,
                {"action": "configure_body_pack", "pack_id": "menstrual_cycle",
                 "enabled": True, "anchor_iso": T_ANCHOR,
                 "cycle_length_days": 10},
                now_iso=T_ANCHOR,
            )
        self.assertEqual(ctx.exception.reason_code, "INVALID_FIELDS")
        with self.assertRaises(engine.V08ComposableBodyError) as ctx:
            engine.configure_body_pack(
                self.root,
                {"action": "configure_body_pack", "pack_id": "menstrual_cycle",
                 "enabled": True, "anchor_iso": T_ANCHOR,
                 "discomfort_profile": "very_bad_mood"},
                now_iso=T_ANCHOR,
            )
        self.assertEqual(ctx.exception.reason_code, "INVALID_FIELDS")
        with self.assertRaises(engine.V08ComposableBodyError) as ctx:
            engine.configure_body_pack(
                self.root,
                {"action": "configure_body_pack", "pack_id": "menstrual_cycle",
                 "enabled": True, "anchor_iso": "not-a-date"},
                now_iso=T_ANCHOR,
            )
        self.assertEqual(ctx.exception.reason_code, "INVALID_FIELDS")
        with self.assertRaises(engine.V08ComposableBodyError) as ctx:
            engine.configure_body_pack(
                self.root,
                {"action": "configure_body_pack", "pack_id": "made_up_pack",
                 "enabled": True},
                now_iso=T_ANCHOR,
            )
        self.assertEqual(ctx.exception.reason_code, "PACK_UNKNOWN")

    def test_04_menstrual_clock_projection(self) -> None:
        engine.configure_body_pack(
            self.root,
            {"action": "configure_body_pack", "pack_id": "menstrual_cycle",
             "enabled": True, "anchor_iso": T_ANCHOR,
             "cycle_length_days": 28, "active_days": 5,
             "discomfort_profile": "mild_cramps", "pre_symptom_enabled": True},
            now_iso=T_ANCHOR,
        )
        day1 = engine.read_composable_body_zero_write(
            self.root, has_subject_capability=True, now_iso=_add_days(0)
        )["menstrual_cycle"]
        self.assertEqual(day1["days_since_anchor"], 0)
        self.assertEqual(day1["cycle_index"], 0)
        self.assertEqual(day1["day_in_cycle"], 1)
        self.assertTrue(day1["in_active_window"])
        self.assertTrue(day1["needs_supply_change"])
        self.assertEqual(day1["current_feeling_cn"], "有轻微腹部不适，仍可按节奏继续手头事或暂歇")

        day7 = engine.read_composable_body_zero_write(
            self.root, has_subject_capability=True, now_iso=_add_days(6)
        )["menstrual_cycle"]
        self.assertEqual(day7["day_in_cycle"], 7)
        self.assertFalse(day7["in_active_window"])
        self.assertFalse(day7["needs_supply_change"])
        self.assertEqual(day7["current_feeling_cn"], "")

        day28 = engine.read_composable_body_zero_write(
            self.root, has_subject_capability=True, now_iso=_add_days(27)
        )["menstrual_cycle"]
        self.assertEqual(day28["day_in_cycle"], 28)
        self.assertTrue(day28["in_pre_window"])

        next_cycle = engine.read_composable_body_zero_write(
            self.root, has_subject_capability=True, now_iso=_add_days(28)
        )["menstrual_cycle"]
        self.assertEqual(next_cycle["cycle_index"], 1)
        self.assertEqual(next_cycle["day_in_cycle"], 1)
        self.assertTrue(next_cycle["needs_supply_change"])

    def test_05_use_supply_clears_flag_and_replays_idempotently(self) -> None:
        engine.configure_body_pack(
            self.root,
            {"action": "configure_body_pack", "pack_id": "menstrual_cycle",
             "enabled": True, "anchor_iso": T_ANCHOR},
            now_iso=T_ANCHOR,
        )
        # Reading / talking never consumes supplies.
        for _ in range(3):
            out = engine.read_composable_body_zero_write(
                self.root, has_subject_capability=True, now_iso=_add_days(0)
            )
            self.assertTrue(out["needs_supply_change"])

        first = engine.use_cycle_supply(
            self.root,
            {"action": "use_cycle_supply", "operation_key": "supply-1",
             "note_cn": "换好新的"},
            now_iso=_add_days(0),
        )
        self.assertTrue(first["supply_used"])
        self.assertFalse(first["needs_supply_change"])
        out = engine.read_composable_body_zero_write(
            self.root, has_subject_capability=True, now_iso=_add_days(0)
        )
        self.assertFalse(out["needs_supply_change"])

        replay = engine.use_cycle_supply(
            self.root,
            {"action": "use_cycle_supply", "operation_key": "supply-1",
             "note_cn": "换好新的"},
            now_iso=_add_days(0),
        )
        self.assertTrue(replay.get("already_applied"))
        self.assertEqual(len(_state(self.root)["packs"]["menstrual_cycle"]["supply_events"]), 1)

    def test_06_disable_keeps_history_and_clears_sensation(self) -> None:
        engine.configure_body_pack(
            self.root,
            {"action": "configure_body_pack", "pack_id": "menstrual_cycle",
             "enabled": True, "anchor_iso": T_ANCHOR},
            now_iso=T_ANCHOR,
        )
        engine.use_cycle_supply(
            self.root, {"action": "use_cycle_supply"}, now_iso=T_ANCHOR
        )
        receipt = engine.configure_body_pack(
            self.root,
            {"action": "configure_body_pack", "pack_id": "menstrual_cycle",
             "enabled": False},
            now_iso=_add_days(0),
        )
        self.assertFalse(receipt["history_deleted"])
        out = engine.read_composable_body_zero_write(
            self.root, has_subject_capability=True, now_iso=_add_days(0)
        )
        self.assertFalse(out["packs"]["menstrual_cycle"]["enabled"])
        self.assertFalse(out["needs_supply_change"])
        self.assertEqual(out["current_feeling_cn"], "")
        stored = _state(self.root)["packs"]["menstrual_cycle"]
        self.assertEqual(len(stored["supply_events"]), 1)

    def test_07_clear_private_history_requires_confirm(self) -> None:
        engine.configure_body_pack(
            self.root,
            {"action": "configure_body_pack", "pack_id": "menstrual_cycle",
             "enabled": True, "anchor_iso": T_ANCHOR},
            now_iso=T_ANCHOR,
        )
        engine.use_cycle_supply(
            self.root, {"action": "use_cycle_supply"}, now_iso=T_ANCHOR
        )
        with self.assertRaises(engine.V08ComposableBodyError) as ctx:
            engine.clear_private_body_history(
                self.root, {"action": "clear_private_body_history"}, now_iso=T_ANCHOR
            )
        self.assertEqual(ctx.exception.reason_code, "CONFIRMATION_REQUIRED")
        receipt = engine.clear_private_body_history(
            self.root,
            {"action": "clear_private_body_history", "confirm": True},
            now_iso=T_ANCHOR,
        )
        self.assertTrue(receipt["history_deleted"])
        packs = _state(self.root)["packs"]
        self.assertEqual(packs["menstrual_cycle"]["supply_events"], [])
        self.assertTrue(packs["menstrual_cycle"]["enabled"])

    # -- nocturnal ------------------------------------------------------------

    def test_08_bind_rejected_when_pack_disabled(self) -> None:
        with self.assertRaises(engine.V08ComposableBodyError) as ctx:
            engine.bind_sleep_activity(
                self.root,
                {"action": "bind_sleep_activity", "sleep_activity_id": "s1",
                 "started_at_iso": T_ANCHOR, "planned_wake_iso": _add_minutes(480)},
                now_iso=T_ANCHOR,
            )
        self.assertEqual(ctx.exception.reason_code, "PACK_DISABLED")
        self.assertEqual(ctx.exception.status_code, 409)

    def test_09_nocturnal_sampling_hidden_and_settlement(self) -> None:
        engine.configure_body_pack(
            self.root,
            {"action": "configure_body_pack", "pack_id": "nocturnal_emission",
             "enabled": True, "probability_permille": 500},
            now_iso=T_ANCHOR,
        )
        started = _add_minutes(0)
        planned = _add_minutes(480)
        for idx in range(1, 6):
            engine.bind_sleep_activity(
                self.root,
                {"action": "bind_sleep_activity", "sleep_activity_id": f"sleep-{idx}",
                 "started_at_iso": started, "planned_wake_iso": planned},
                now_iso=started,
            )
        salt = _state(self.root)["instance_salt"]
        activities = _state(self.root)["packs"]["nocturnal_emission"]["sleep_activities"]
        hit = next(sid for sid in activities if _sample(salt, sid) < 500)
        miss = next(sid for sid in activities if _sample(salt, sid) >= 500)
        self.assertTrue(activities[hit]["sampled_occurred"])
        self.assertFalse(activities[miss]["sampled_occurred"])

        # Before realisation the hidden moment/sample is never exposed.
        before = engine.read_composable_body_zero_write(
            self.root, has_subject_capability=True, now_iso=_add_minutes(30)
        )
        blob = json.dumps(before, ensure_ascii=False)
        self.assertNotIn("hidden_scheduled_at_iso", blob)
        self.assertNotIn("sampled_occurred", blob)

        # Early wake cancels cleanly with zero consequence.
        early = engine.settle_sleep_wake(
            self.root,
            {"action": "settle_sleep_wake", "sleep_activity_id": hit,
             "woke_at_iso": _add_minutes(20)},
            now_iso=_add_minutes(20),
        )
        self.assertTrue(early["cancelled_by_early_wake"])
        self.assertFalse(early["occurred"])
        self.assertFalse(engine.read_composable_body_zero_write(
            self.root, has_subject_capability=True, now_iso=_add_minutes(20)
        )["pending_garment_care"])

        # Bind a fresh realised activity (same deterministic id may be cancelled).
        engine.bind_sleep_activity(
            self.root,
            {"action": "bind_sleep_activity", "sleep_activity_id": "sleep-real",
             "started_at_iso": started, "planned_wake_iso": planned},
            now_iso=started,
        )
        salt = _state(self.root)["instance_salt"]
        if _sample(salt, "sleep-real") >= 500:
            # Swap in any other id that samples true, keeping the run deterministic.
            candidate = next(
                f"real-{i}" for i in range(50) if _sample(salt, f"real-{i}") < 500
            )
            engine.bind_sleep_activity(
                self.root,
                {"action": "bind_sleep_activity", "sleep_activity_id": candidate,
                 "started_at_iso": started, "planned_wake_iso": planned},
                now_iso=started,
            )
            active_id = candidate
        else:
            active_id = "sleep-real"

        settled = engine.settle_sleep_wake(
            self.root,
            {"action": "settle_sleep_wake", "sleep_activity_id": active_id,
             "woke_at_iso": _add_minutes(500)},
            now_iso=_add_minutes(500),
        )
        self.assertTrue(settled["occurred"])
        self.assertTrue(settled["pending_garment_care"])
        self.assertEqual(
            _state(self.root)["packs"]["nocturnal_emission"]["garment_care_note_cn"],
            "醒来留意到贴身衣物需要清洗或更换",
        )
        again = engine.settle_sleep_wake(
            self.root,
            {"action": "settle_sleep_wake", "sleep_activity_id": active_id,
             "woke_at_iso": _add_minutes(520)},
            now_iso=_add_minutes(520),
        )
        self.assertTrue(again.get("already_applied"))
        self.assertEqual(
            len(_state(self.root)["packs"]["nocturnal_emission"]["events"]), 1
        )

        # Sampling is never re-rolled on re-submit.
        reroll = engine.bind_sleep_activity(
            self.root,
            {"action": "bind_sleep_activity", "sleep_activity_id": active_id,
             "started_at_iso": planned, "planned_wake_iso": _add_minutes(960)},
            now_iso=started,
        )
        self.assertTrue(reroll.get("already_applied"))
        ledger = _state(self.root)["packs"]["nocturnal_emission"]["sleep_activities"][active_id]
        self.assertEqual(ledger["started_at_iso"], started)

        resolved = engine.resolve_garment_care(
            self.root,
            {"action": "resolve_garment_care", "mode": "wash_garment"},
            now_iso=_add_minutes(530),
        )
        self.assertFalse(resolved["pending_garment_care"])
        with self.assertRaises(engine.V08ComposableBodyError) as ctx:
            engine.resolve_garment_care(
                self.root,
                {"action": "resolve_garment_care", "mode": "change_garment"},
                now_iso=_add_minutes(540),
            )
        self.assertEqual(ctx.exception.reason_code, "NO_PENDING_GARMENT_CARE")

    # -- care hints, partner care, identity ----------------------------------

    def test_10_partner_view_strips_private_body_surfaces(self) -> None:
        engine.configure_body_pack(
            self.root,
            {"action": "configure_body_pack", "pack_id": "menstrual_cycle",
             "enabled": True, "anchor_iso": T_ANCHOR},
            now_iso=T_ANCHOR,
        )
        engine.share_body_care_hint_once(
            self.root,
            {"action": "share_body_care_hint_once", "hint_cn": "想喝点热的",
             "ttl_minutes": 60},
            now_iso=T_ANCHOR,
        )
        engine.offer_ordinary_care(
            self.root,
            {"action": "offer_ordinary_care", "care_kind": "warm_drink",
             "note_cn": "放在桌角"},
            now_iso=T_ANCHOR,
        )
        partner = engine.read_composable_body_zero_write(
            self.root, has_subject_capability=False, now_iso=_add_minutes(10)
        )
        for forbidden in (
            "packs", "menstrual_cycle", "nocturnal_emission", "sleep_activities",
            "pending_garment_care", "needs_supply_change", "current_feeling_cn",
        ):
            self.assertNotIn(forbidden, partner)
        self.assertEqual(partner["shared_care_hint"]["text_cn"], "想喝点热的")
        self.assertEqual(len(partner["recent_partner_care"]), 1)
        self.assertEqual(partner["recent_partner_care"][0]["care_kind"], "warm_drink")
        self.assertNotIn("intimacy_score", json.dumps(partner, ensure_ascii=False))

        expired = engine.read_composable_body_zero_write(
            self.root, has_subject_capability=False, now_iso=_add_minutes(61)
        )
        self.assertIsNone(expired["shared_care_hint"])

    def test_11_recent_partner_care_is_trimmed(self) -> None:
        for idx in range(15):
            engine.offer_ordinary_care(
                self.root,
                {"action": "offer_ordinary_care", "care_kind": "quiet_space",
                 "note_cn": f"第{idx}次"},
                now_iso=_add_minutes(idx),
            )
        partner = engine.read_composable_body_zero_write(
            self.root, has_subject_capability=False, now_iso=_add_minutes(30)
        )
        self.assertEqual(len(partner["recent_partner_care"]), engine.MAX_PARTNER_CARE_VIEW)
        self.assertEqual(partner["recent_partner_care"][-1]["note_cn"], "第14次")

    def test_12_identity_and_operation_conflict(self) -> None:
        with self.assertRaises(engine.V08ComposableBodyError) as ctx:
            engine.configure_body_pack(
                self.root,
                {"action": "configure_body_pack", "pack_id": "menstrual_cycle",
                 "enabled": True, "anchor_iso": T_ANCHOR, "uid": "root"},
                now_iso=T_ANCHOR,
            )
        self.assertEqual(ctx.exception.reason_code, "CLIENT_IDENTITY_REFUSED")

        engine.configure_body_pack(
            self.root,
            {"action": "configure_body_pack", "pack_id": "menstrual_cycle",
             "enabled": True, "anchor_iso": T_ANCHOR, "operation_key": "k1"},
            now_iso=T_ANCHOR,
        )
        with self.assertRaises(engine.V08ComposableBodyError) as ctx:
            engine.share_body_care_hint_once(
                self.root,
                {"action": "share_body_care_hint_once", "hint_cn": "喝点热的",
                 "operation_key": "k1"},
                now_iso=T_ANCHOR,
            )
        self.assertEqual(ctx.exception.reason_code, "OPERATION_KEY_CONFLICT")

    def test_14_disable_nocturnal_stops_care_but_keeps_events(self) -> None:
        engine.configure_body_pack(
            self.root,
            {"action": "configure_body_pack", "pack_id": "nocturnal_emission",
             "enabled": True, "probability_permille": 500},
            now_iso=T_ANCHOR,
        )
        started = _add_minutes(0)
        planned = _add_minutes(480)
        engine.bind_sleep_activity(
            self.root,
            {"action": "bind_sleep_activity", "sleep_activity_id": "n1",
             "started_at_iso": started, "planned_wake_iso": planned},
            now_iso=started,
        )
        salt = _state(self.root)["instance_salt"]
        ledger = _state(self.root)["packs"]["nocturnal_emission"]["sleep_activities"]["n1"]
        if not ledger["sampled_occurred"]:
            for i in range(50):
                candidate = f"n-{i}"
                if _sample(salt, candidate) < 500:
                    engine.bind_sleep_activity(
                        self.root,
                        {"action": "bind_sleep_activity", "sleep_activity_id": candidate,
                         "started_at_iso": started, "planned_wake_iso": planned},
                        now_iso=started,
                    )
                    active_id = candidate
                    break
            else:  # pragma: no cover - with 500/1000 over 50 ids this is unreachable
                self.fail("no sampled activity")
        else:
            active_id = "n1"
        engine.settle_sleep_wake(
            self.root,
            {"action": "settle_sleep_wake", "sleep_activity_id": active_id,
             "woke_at_iso": _add_minutes(500)},
            now_iso=_add_minutes(500),
        )
        self.assertTrue(
            engine.read_composable_body_zero_write(
                self.root, has_subject_capability=True, now_iso=_add_minutes(500)
            )["pending_garment_care"]
        )
        receipt = engine.configure_body_pack(
            self.root,
            {"action": "configure_body_pack", "pack_id": "nocturnal_emission",
             "enabled": False},
            now_iso=_add_minutes(510),
        )
        self.assertFalse(receipt["history_deleted"])
        out = engine.read_composable_body_zero_write(
            self.root, has_subject_capability=True, now_iso=_add_minutes(510)
        )
        self.assertFalse(out["packs"]["nocturnal_emission"]["enabled"])
        self.assertFalse(out["pending_garment_care"])
        self.assertEqual(out["garment_care_note_cn"], "")
        self.assertEqual(
            len(_state(self.root)["packs"]["nocturnal_emission"]["events"]), 1
        )

    def test_13_no_hardcoded_pronouns_in_engine_source(self) -> None:
        source = Path(engine.__file__).read_text(encoding="utf-8")
        self.assertNotIn("她", source)
        self.assertNotIn("千代", source)


class V08ComposableBodyViewTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-body-view-")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_01_partner_subject_action_is_403(self) -> None:
        code, body = view.mutate_v08_composable_body_view(
            self.root,
            {"action": "configure_body_pack", "pack_id": "menstrual_cycle",
             "enabled": True, "anchor_iso": T_ANCHOR},
            actor_side="partner",
            now_iso=T_ANCHOR,
        )
        self.assertEqual(code, 403, body)
        self.assertEqual(body["reason_code"], "FORBIDDEN_PARTNER_ACTION")
        self.assertEqual(body["llm_calls"], 0)

    def test_02_identity_and_unknown_fields_refused(self) -> None:
        code, body = view.mutate_v08_composable_body_view(
            self.root,
            {"action": "offer_ordinary_care", "care_kind": "warm_pad",
             "favorability": 99},
            actor_side="partner",
            now_iso=T_ANCHOR,
        )
        self.assertEqual(code, 409, body)
        self.assertEqual(body["reason_code"], "CLIENT_IDENTITY_REFUSED")
        code, body = view.mutate_v08_composable_body_view(
            self.root,
            {"action": "offer_ordinary_care", "care_kind": "warm_pad", "score": 1},
            actor_side="partner", now_iso=T_ANCHOR,
        )
        self.assertEqual(code, 400, body)
        self.assertEqual(body["reason_code"], "INVALID_FIELDS")

    def test_03_subject_configure_and_zero_write_read(self) -> None:
        code, body = view.mutate_v08_composable_body_view(
            self.root,
            {"action": "configure_body_pack", "pack_id": "menstrual_cycle",
             "enabled": True, "anchor_iso": T_ANCHOR},
            actor_side="subject",
            now_iso=T_ANCHOR,
        )
        self.assertEqual(code, 200, body)
        self.assertEqual(body["perspective"], "subject")
        before = _snapshot(self.root)
        code, read = view.read_v08_composable_body_view(
            self.root, has_subject_capability=True, now_iso=_add_days(0)
        )
        self.assertEqual(code, 200)
        self.assertTrue(read["ok"])
        self.assertEqual(read["data"]["perspective"], "subject")
        self.assertTrue(read["data"]["menstrual_cycle"]["enabled"])
        self.assertEqual(before, _snapshot(self.root), "GET is zero-write")

        code, partner = view.read_v08_composable_body_view(
            self.root, has_subject_capability=False, now_iso=_add_days(0)
        )
        self.assertEqual(code, 200)
        self.assertEqual(partner["data"]["perspective"], "partner")
        self.assertNotIn("packs", partner["data"])


POSIX_ONLY = unittest.skipUnless(
    hasattr(os, "getuid") and os.name == "posix",
    "Requires a POSIX loopback server (run inside WSL)",
)


@POSIX_ONLY
class V08ComposableBodyHTTPTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import autonomy_rhythm_fixture as fixture
        import test_v02_phone_http_surfaces as v02http
        from components.phone import capability as phone_capability

        cls.fixture = fixture
        cls._saved_env = dict(os.environ)
        os.environ["CHIYO_WORLD_LOCAL_OWNER_PHONE"] = "chiyo"
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="v08-body-http-"))
        cls.server_root = cls.temp_dir / "instance"
        cls.capability = phone_capability.Capability(
            token=v02http.CAP_TOKEN,
            subject="chiyo",
            endpoint="http://127.0.0.1:1",
            issued_at="2026-11-16T12:00:00+08:00",
        )
        cls.stack = fixture.build_stack(cls.server_root, port=0, capability=cls.capability)
        fixture.serve_stack(cls.stack)
        cls.base_url = cls.stack.base
        cls.CAP = v02http.CAP_TOKEN

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixture.close_stack(cls.stack)
        import shutil
        shutil.rmtree(cls.temp_dir, ignore_errors=True)
        os.environ.clear()
        os.environ.update(cls._saved_env)

    def setUp(self) -> None:
        (self.stack.root / "body" / "v08_composable_body_pack.json").unlink(missing_ok=True)
        self.stack.service._world_clock = lambda: datetime(
            2026, 11, 16, 14, 30, tzinfo=timezone(timedelta(hours=8))
        )

    def _http(self, method: str, path: str, body: dict[str, Any] | None = None, *,
              csrf: bool = False, capability: str | None = None) -> tuple[int, dict[str, Any]]:
        import urllib.error
        import urllib.request

        raw = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.base_url + path, data=raw, method=method)
        if csrf:
            req.add_header("X-CSRF-Token", self.stack.csrf_token)
        if capability is not None:
            req.add_header("X-Phone-Capability", capability)
        if raw is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_01_get_zero_write_guards_and_projection(self) -> None:
        path = "/api/phone/v08/composable-body"
        before = _snapshot(Path(self.stack.root))
        code_p, res_p = self._http("GET", path)
        self.assertEqual(code_p, 200, res_p)
        data = res_p.get("data") or {}
        self.assertEqual(data.get("schema"), "nyairo.v08.composable_body.v1")
        self.assertEqual(data.get("perspective"), "partner")
        self.assertEqual(data.get("llm_calls"), 0)
        self.assertNotIn("packs", data)

        code_s, res_s = self._http("GET", path, capability=self.CAP)
        self.assertEqual(code_s, 200, res_s)
        subject = res_s.get("data") or {}
        self.assertEqual(subject.get("perspective"), "subject")
        self.assertTrue(subject.get("pronoun_decoupled"))
        self.assertFalse(subject["packs"]["menstrual_cycle"]["enabled"])
        self.assertFalse(subject["packs"]["nocturnal_emission"]["enabled"])

        self.assertEqual(self._http("GET", path + "?x=1")[0], 400)
        self.assertEqual(self._http("GET", path, capability="forged")[0], 403)
        after = _snapshot(Path(self.stack.root))
        self.assertEqual(before, after, "GET must be 100% zero-write")

    def test_02_post_permissions_privacy_and_flow(self) -> None:
        path = "/api/phone/v08/composable-body"
        enable = {
            "action": "configure_body_pack",
            "pack_id": "menstrual_cycle",
            "enabled": True,
            "anchor_iso": "2026-11-16T09:00:00+08:00",
        }
        # CSRF boundary and partner-door refusal.
        self.assertEqual(self._http("POST", path, enable)[0], 403)
        self.assertEqual(self._http("POST", path + "?x=1", enable, csrf=True)[0], 400)
        c_forbid, res_forbid = self._http("POST", path, enable, csrf=True)
        self.assertEqual(c_forbid, 403, res_forbid)
        self.assertEqual(res_forbid.get("reason_code"), "FORBIDDEN_PARTNER_ACTION")

        # Identity / score refusal.
        c_uid, res_uid = self._http(
            "POST", path, {**enable, "uid": "root"}, csrf=True, capability=self.CAP
        )
        self.assertEqual(c_uid, 409, res_uid)
        self.assertTrue(
            str(res_uid.get("reason_code")).startswith("CLIENT_IDENTITY_REFUSED")
        )

        # Subject enables cleanly.
        c_enable, res_enable = self._http(
            "POST", path, enable, csrf=True, capability=self.CAP
        )
        self.assertEqual(c_enable, 200, res_enable)
        self.assertTrue((res_enable.get("data") or {}).get("enabled"))

        # Partner offers ordinary care (allowed on the partner door).
        c_care, res_care = self._http(
            "POST", path,
            {"action": "offer_ordinary_care", "care_kind": "warm_drink",
             "note_cn": "放在手边"},
            csrf=True,
        )
        self.assertEqual(c_care, 200, res_care)
        self.assertTrue((res_care.get("data") or {}).get("relationship_score_unchanged"))

        # Subject shares one expiring hint.
        c_hint, res_hint = self._http(
            "POST", path,
            {"action": "share_body_care_hint_once", "hint_cn": "想先安静一会",
             "ttl_minutes": 30},
            csrf=True, capability=self.CAP,
        )
        self.assertEqual(c_hint, 200, res_hint)

        _, partner_view = self._http("GET", path)
        partner_data = partner_view.get("data") or {}
        self.assertEqual(partner_data["shared_care_hint"]["text_cn"], "想先安静一会")
        self.assertEqual(len(partner_data["recent_partner_care"]), 1)
        self.assertNotIn("current_feeling_cn", json.dumps(partner_view, ensure_ascii=False))
        self.assertNotIn("menstrual_cycle", json.dumps(partner_view, ensure_ascii=False))

        # Partner cannot use supplies.
        c_use, res_use = self._http(
            "POST", path, {"action": "use_cycle_supply"}, csrf=True
        )
        self.assertEqual(c_use, 403, res_use)


if __name__ == "__main__":
    unittest.main(verbosity=2)

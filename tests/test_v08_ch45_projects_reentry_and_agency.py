#!/usr/bin/env python3
"""v0.4 Ch.45 §二–§七: autonomous mini-projects, wake re-entry, capability awareness.

Covers the lightweight completion inside the existing elastic-play engine, the
Daily Loop bridge, the lived-context bridge and the phone tool surface.  All of
it is zero-write on read, zero paid LLM calls and adds no HTTP endpoint.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "components" / "life" / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:  # normal import
    import plugins.chiyo as chiyo_plugin  # noqa: E402
except ModuleNotFoundError:  # an earlier test may have mocked sys.modules["plugins"]
    import importlib.util as _importlib_util

    _plugin_spec = _importlib_util.spec_from_file_location(
        "chiyo_plugin_isolated", ROOT / "plugins" / "chiyo" / "__init__.py"
    )
    assert _plugin_spec is not None and _plugin_spec.loader is not None
    chiyo_plugin = _importlib_util.module_from_spec(_plugin_spec)
    _plugin_spec.loader.exec_module(chiyo_plugin)  # type: ignore[union-attr]
from chiyo_bundle import lived_context_bridge as lcb  # noqa: E402
from chiyo_bundle import phone_tool  # noqa: E402
from components.life.scripts import v02_v03_daily_loop_bridge as bridge  # noqa: E402
from components.life.scripts import v04_elastic_play_learning as ep_engine  # noqa: E402
from components.phone import v04_elastic_play_view as ep_view  # noqa: E402

TZ = timezone(timedelta(hours=8))
NOW = "2026-11-16T09:00:00+08:00"


def _snap(root: Path) -> dict[str, tuple[int, int, str]]:
    if not root.exists():
        return {}
    out: dict[str, tuple[int, int, str]] = {}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        st = p.lstat()
        if p.is_file() and not p.is_symlink():
            out[rel] = (int(st.st_size), int(st.st_mtime_ns), hashlib.sha256(p.read_bytes()).hexdigest())
        else:
            out[rel] = (0, int(st.st_mtime_ns), "dir")
    return out


def _write_json(path: Path, doc: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


class Ch45Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="v08-ch45-"))
        self.day_root = self.tmp / "day"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mutate(self, payload: dict, *, actor: str = "subject"):
        return ep_view.mutate_v04_elastic_play_view(
            self.day_root, payload, actor_side=actor, now_iso=NOW
        )

    def _start_project(self, **overrides) -> dict:
        payload = {
            "action": "curate_personal_project",
            "op": "start",
            "purpose_cn": "把河边长椅坐过的几个午后记成小册",
            "medium_kind": "place_booklet",
        }
        payload.update(overrides)
        code, body = self._mutate(payload)
        self.assertEqual(code, 200, body)
        return body["data"]["personal_projects"][0]


class PlaceBookletTruthGateTest(Ch45Base):
    def test_visited_gate_blank_unvisited_pause_and_partner_privacy(self) -> None:
        # Real persisted visit evidence for the riverside bench.
        _write_json(
            self.day_root / "world" / "v04_neighborhood_state.json",
            {
                "places": {
                    "riverside_bench": {
                        "place_id": "riverside_bench",
                        "knowledge_status": "visited",
                        "last_visited_at": "2026-11-15T16:00:00+08:00",
                    },
                    "forest_trail": {
                        "place_id": "forest_trail",
                        "knowledge_status": "unknown",
                    },
                }
            },
        )

        project = self._start_project()
        project_id = project["project_id"]
        self.assertEqual(project["status"], "active")
        self.assertEqual(project["confidence_penalty"], 0)
        self.assertFalse(project["forced_reflection"])

        # Unvisited outdoor place claimed as firsthand -> named 409.
        code, body = self._mutate({
            "action": "curate_personal_project",
            "op": "add_field_note",
            "project_id": project_id,
            "place_id": "forest_trail",
            "entry_status": "visited_noted",
            "note_cn": "林子边的风很安静",
        })
        self.assertEqual(code, 409)
        self.assertEqual(body["reason_code"], "UNVISITED_PLACE_MUST_STAY_BLANK")

        # The same spot is allowed as an honestly blank/unvisited page.
        code, body = self._mutate({
            "action": "curate_personal_project",
            "op": "add_field_note",
            "project_id": project_id,
            "place_id": "forest_trail",
            "entry_status": "blank_unvisited",
            "note_cn": "还没去过，先空着这一页",
        })
        self.assertEqual(code, 200, body)

        # A genuinely visited place accepts firsthand notes + a private raw note.
        code, body = self._mutate({
            "action": "curate_personal_project",
            "op": "add_field_note",
            "project_id": project_id,
            "place_id": "riverside_bench",
            "entry_status": "visited_noted",
            "note_cn": "下午四点的水面反光很慢",
            "private_raw_note_cn": "这一页只给自己看：其实有点困",
        })
        self.assertEqual(code, 200, body)

        # An unrelated second project with no shared page.
        self._start_project(
            project_id="private-map-margin",
            purpose_cn="未公开的地图边角",
            medium_kind="map_margin_note",
        )

        # Guilt-free shelving: keeps unfinished pages, no penalty, no reminders.
        code, body = self._mutate({
            "action": "curate_personal_project",
            "op": "pause_not_feeling_it",
            "project_id": project_id,
        })
        self.assertEqual(code, 200, body)
        paused = next(
            p for p in body["data"]["personal_projects"] if p["project_id"] == project_id
        )
        self.assertEqual(paused["status"], "resting_not_feeling_it")
        self.assertEqual(paused["confidence_penalty"], 0)
        self.assertFalse(paused["forced_reflection"])
        self.assertTrue(paused["reminder_suppressed"])
        self.assertEqual(len(paused["entries"]), 2, "unfinished pages must be preserved")

        # Daily Loop: a resting project never emits a reminder/continuation cue.
        collected = bridge.collect_v02_v03_loop_candidates(self.day_root, now_iso=NOW)
        project_candidates = [
            desc for desc in collected["descriptors"].values()
            if desc.get("clue_domain") == "v04.elastic.personal_project"
        ]
        cue_refs = [desc.get("source_ref") for desc in project_candidates]
        self.assertNotIn(f"v04_personal_project:{project_id}", cue_refs)
        self.assertLessEqual(len(project_candidates), 1, "at most one optional project cue")

        # Resume, then share exactly one page.
        code, body = self._mutate({
            "action": "curate_personal_project",
            "op": "resume",
            "project_id": project_id,
        })
        self.assertEqual(code, 200, body)
        code, body = self._mutate({
            "action": "curate_personal_project",
            "op": "share_page",
            "project_id": project_id,
            "entry_index": 1,
        })
        self.assertEqual(code, 200, body)

        code, body = self._mutate({
            "action": "curate_personal_project",
            "op": "share_page",
            "project_id": project_id,
            "entry_index": 99,
        })
        self.assertEqual(code, 404)
        self.assertEqual(body["reason_code"], "PAGE_NOT_FOUND")

        # Partner projection: only the shared entry, private raw note withheld,
        # and the unshared project entirely invisible.
        status, partner_env = ep_view.read_v04_elastic_play_view(
            self.day_root, has_subject_capability=False, now_iso=NOW
        )
        self.assertEqual(status, 200)
        partner_projects = partner_env["data"]["personal_projects"]
        self.assertEqual(len(partner_projects), 1)
        self.assertEqual(partner_projects[0]["project_id"], project_id)
        self.assertEqual(len(partner_projects[0]["entries"]), 1)
        shared_entry = partner_projects[0]["entries"][0]
        self.assertTrue(shared_entry["shared"])
        self.assertEqual(shared_entry["private_raw_note_cn"], "")
        self.assertTrue(shared_entry.get("raw_note_withheld"))

        # Subject still sees both projects and the private draft text.
        status, subject_env = ep_view.read_v04_elastic_play_view(
            self.day_root, has_subject_capability=True, now_iso=NOW
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(subject_env["data"]["personal_projects"]), 2)
        subject_project = next(
            p for p in subject_env["data"]["personal_projects"] if p["project_id"] == project_id
        )
        self.assertEqual(len(subject_project["entries"]), 2)
        self.assertIn("只给自己看", subject_project["entries"][1]["private_raw_note_cn"])


class MediaHonestyTest(Ch45Base):
    def test_sound_card_claim_and_sketch_never_create_world_facts(self) -> None:
        code, body = self._mutate({
            "action": "curate_personal_project",
            "op": "start",
            "purpose_cn": "把旧书店的翻页声记成声卡",
            "medium_kind": "sound_card",
            "claimed_real_audio_recording": True,
        })
        self.assertEqual(code, 409)
        self.assertEqual(body["reason_code"], "UNSUPPORTED_MEDIA_CLAIM")

        project = self._start_project(
            purpose_cn="旧书店翻页声（文字声景来源）",
            medium_kind="sound_card",
        )
        code, body = self._mutate({
            "action": "curate_personal_project",
            "op": "add_field_note",
            "project_id": project["project_id"],
            "note_cn": "木地板轻响、纸页摩擦、远处有人合上书",
            "heard_sources": ["旧书店二楼", "木窗边"],
            "claimed_real_audio_recording": True,
        })
        self.assertEqual(code, 409)
        self.assertEqual(body["reason_code"], "UNSUPPORTED_MEDIA_CLAIM")

        world_before = _snap(self.day_root / "world")
        code, body = self._mutate({
            "action": "curate_personal_project",
            "op": "start",
            "purpose_cn": "画窗台上的空花瓶",
            "medium_kind": "sketch_note",
        })
        self.assertEqual(code, 200, body)
        sketch = body["data"]["personal_projects"][0]
        self.assertEqual(sketch["medium_kind"], "sketch_note")
        self.assertTrue(sketch["artistic_expression_only"])
        self.assertFalse(sketch["world_fact_authority"])
        self.assertEqual(world_before, _snap(self.day_root / "world"))
        for rel in ("world/v08_universal_matter.json", "world/v08_matter_space.json"):
            self.assertFalse((self.day_root / rel).exists(), "a sketch must not spawn world matter")


class WakeReentryTest(Ch45Base):
    def test_room_and_body_first_resting_excluded_and_zero_write(self) -> None:
        _write_json(
            self.day_root / "world" / "v08_matter_space.json",
            {
                "matter_objects": {
                    "sketch_sheet_01": {
                        "object_id": "sketch_sheet_01",
                        "label_cn": "昨晚没画完的参考画纸",
                        "item_kind": "paper",
                        "parent_id": "home_desk",
                    },
                    "ceramic_cup_01": {
                        "object_id": "ceramic_cup_01",
                        "label_cn": "白瓷杯",
                        "item_kind": "vessel",
                        "parent_id": "home_desk",
                    },
                }
            },
        )
        _write_json(
            self.day_root / "phone" / "somatic_comfort.json",
            {
                "window": {"state": "ajar"},
                "local_position": {"zone": "desk_side", "place_id": "home"},
            },
        )
        _write_json(
            self.day_root / "phone" / "v04_elastic_play.json",
            {
                "schema": ep_engine.STATE_SCHEMA,
                "revision": 3,
                "personal_projects": [
                    {
                        "project_id": "bench-booklet",
                        "purpose_cn": "河边长椅小册",
                        "medium_kind": "place_booklet",
                        "next_step_cn": "补一页昨天傍晚的光",
                        "status": "active",
                        "confidence_penalty": 0,
                        "forced_reflection": False,
                        "entries": [],
                    },
                    {
                        "project_id": "resting-map",
                        "purpose_cn": "暂时不想画的地图边角",
                        "medium_kind": "map_margin_note",
                        "status": "resting_not_feeling_it",
                        "confidence_penalty": 0,
                        "forced_reflection": False,
                        "entries": [],
                    },
                ],
            },
        )

        before = _snap(self.tmp)
        out = bridge.project_wake_and_interruption_reentry_zero_write(
            self.day_root, now_iso=NOW, sleep_just_ended=True, host_interrupted=True
        )
        after = _snap(self.tmp)
        self.assertEqual(before, after, "wake re-entry must be 100% zero-write")

        self.assertTrue(out["room_and_uncollected_first"])
        labels = " ".join(row["label_cn"] for row in out["room_and_uncollected_first"])
        self.assertIn("参考画纸", labels)
        self.assertIn("白瓷杯", labels)
        self.assertIn("桌边", out["body_comfort_note"])

        kinds = [thread["kind"] for thread in out["resumable_threads"]]
        refs = " ".join(str(thread.get("ref") or "") for thread in out["resumable_threads"])
        self.assertIn("personal_project", kinds)
        self.assertIn("bench-booklet", refs)
        self.assertNotIn("resting-map", refs, "shelved projects must not nag on wake")
        self.assertTrue(out["active_project_hint"])
        self.assertFalse(out["force_midnight_summary"])
        self.assertEqual(out["guilt_debt"], 0)
        self.assertFalse(out["forced_resume"])

        ordered_keys = list(out.keys())
        self.assertLess(
            ordered_keys.index("room_and_uncollected_first"),
            ordered_keys.index("body_comfort_note"),
        )
        self.assertLess(
            ordered_keys.index("body_comfort_note"),
            ordered_keys.index("resumable_threads"),
        )
        self.assertLess(
            ordered_keys.index("resumable_threads"),
            ordered_keys.index("active_project_hint"),
        )

        # A completely missing root stays clean and is never created.
        missing = self.tmp / "not-here"
        missing_before = _snap(self.tmp)
        empty = bridge.project_wake_and_interruption_reentry_zero_write(missing, now_iso=NOW)
        self.assertEqual(missing_before, _snap(self.tmp))
        self.assertEqual(empty["room_and_uncollected_first"], [])
        self.assertEqual(empty["body_comfort_note"], "")
        self.assertEqual(empty["resumable_threads"], [])
        self.assertEqual(empty["active_project_hint"], "")


class SituationalCapabilityTest(Ch45Base):
    def _write_cooldowns(self, rows: dict) -> None:
        _write_json(
            self.day_root / "life" / "v02_v03_intent_cooldowns.json",
            {"schema": bridge.MICRO_INTENT_SCHEMA, "revision": len(rows), "cooldowns": rows},
        )

    def test_scoped_failure_never_generalizes_and_silence_gate(self) -> None:
        # Quiet empty root -> no cues, silent, zero-write.
        missing = self.tmp / "empty-root"
        empty = bridge.project_situational_capability_awareness_zero_write(missing, now_iso=NOW)
        self.assertTrue(empty["silent"])
        self.assertEqual(empty["can_do_here"], [])
        self.assertEqual(empty["cannot_do_here"], [])
        self.assertEqual(empty["scoped_failure_notes"], [])
        self.assertFalse(missing.exists())

        _write_json(
            self.day_root / "world" / "v08_matter_space.json",
            {
                "matter_objects": {
                    "pen_01": {"object_id": "pen_01", "label_cn": "常用勾线笔", "item_kind": "tool", "parent_id": "home_desk"},
                    "paper_01": {"object_id": "paper_01", "label_cn": "一叠纸", "item_kind": "paper", "parent_id": "home_desk"},
                }
            },
        )
        expires = (datetime.fromisoformat(NOW) + timedelta(hours=3)).isoformat()
        self._write_cooldowns({
            "micro:read": {
                "intent_key": "micro:read",
                "category": "reading_page",
                "zone_id": "home",
                "reason_code": "VERIFY_FAILED",
                "recorded_at": NOW,
                "expires_at": expires,
            },
            "micro:craft": {
                "intent_key": "micro:craft",
                "category": "craft_sizing",
                "zone_id": "home",
                "reason_code": "UNSUPPORTED_CAPABILITY",
                "recorded_at": NOW,
                "expires_at": expires,
            },
        })

        before = _snap(self.tmp)
        aware = bridge.project_situational_capability_awareness_zero_write(self.day_root, now_iso=NOW)
        self.assertEqual(before, _snap(self.tmp), "capability awareness must be zero-write")

        self.assertFalse(aware["silent"])
        self.assertTrue(any(row["item_kind"] == "tool" for row in aware["can_do_here"]))
        self.assertFalse(aware["generalized"])
        self.assertEqual(aware["confidence_penalty"], 0)
        notes = " ".join(row["scoped_note_cn"] for row in aware["scoped_failure_notes"])
        self.assertIn("不等于书不存在", notes)
        self.assertIn("不代表手头别的事都不行", notes)
        for row in aware["scoped_failure_notes"]:
            self.assertFalse(row["generalized"])
            self.assertFalse(row["applies_to_other_tasks"])

        # Expired cooldowns contribute nothing (scoped failure naturally lifts).
        expired = (datetime.fromisoformat(NOW) - timedelta(hours=1)).isoformat()
        self._write_cooldowns({
            "micro:old": {
                "intent_key": "micro:old",
                "category": "reading_page",
                "zone_id": "home",
                "reason_code": "VERIFY_FAILED",
                "recorded_at": expired,
                "expires_at": expired,
            }
        })
        lifted = bridge.project_situational_capability_awareness_zero_write(self.day_root, now_iso=NOW)
        self.assertEqual(lifted["scoped_failure_notes"], [])

        # Lived-context: calm/quiet adds zero extra chars; a live cooldown scopes one line.
        calm_root = self.tmp / "calm-empty"
        quiet_line = lcb._format_v04_project_reentry_micro(
            v04_ep=None, root=calm_root, include_private=True,
            now_dt=datetime.fromisoformat(NOW),
        )
        self.assertIsNone(quiet_line)
        self.assertFalse(calm_root.exists(), "quiet formatting must not create a dir")

        expires = (datetime.fromisoformat(NOW) + timedelta(hours=2)).isoformat()
        self._write_cooldowns({
            "micro:read": {
                "intent_key": "micro:read",
                "category": "reading_page",
                "zone_id": "home",
                "reason_code": "VERIFY_FAILED",
                "recorded_at": NOW,
                "expires_at": expires,
            }
        })
        live_line = lcb._format_v04_project_reentry_micro(
            v04_ep=None, root=self.day_root, include_private=True,
            now_dt=datetime.fromisoformat(NOW),
        )
        self.assertIsNotNone(live_line)
        self.assertIn("不等于书不存在", live_line)
        self.assertLessEqual(len(live_line), 420)


class GroundedAlternativeAndAdminEditTest(Ch45Base):
    def test_grounded_alternative_and_admin_edit_consent_separation(self) -> None:
        code, body = self._mutate({
            "action": "propose_grounded_alternative",
            "target_suggestion_cn": "现在去院子里浇水",
            "alternative_cn": "先把这张声卡写完，十分钟后再去浇水",
            "grounded_basis_cn": "刚坐下手还是凉的，先写两行更容易接上",
        })
        self.assertEqual(code, 200, body)
        alts = body["data"]["grounded_alternatives"]
        self.assertEqual(len(alts), 1)
        self.assertEqual(alts[0]["alternative_cn"], "先把这张声卡写完，十分钟后再去浇水")

        code, body = self._mutate({
            "action": "propose_grounded_alternative",
            "target_suggestion_cn": "去散步",
            "alternative_cn": "晚点再走",
            "grounded_basis_cn": "现在想先坐一会儿",
        }, actor="partner")
        self.assertEqual(code, 403)
        self.assertEqual(body["reason_code"], "FORBIDDEN_ACTOR_SIDE")

        # Partner may not forge the subject's opinion about an admin edit.
        code, body = self._mutate({
            "action": "note_admin_world_change",
            "change_summary_cn": "世界编辑器把书桌挪到了窗边",
            "subject_opinion_cn": "她同意这样改",
        }, actor="partner")
        self.assertEqual(code, 403)
        self.assertEqual(body["reason_code"], "FORBIDDEN_ACTOR_SIDE")

        code, body = self._mutate({
            "action": "note_admin_world_change",
            "change_summary_cn": "世界编辑器把书桌挪到了窗边",
        }, actor="partner")
        self.assertEqual(code, 200, body)
        edit = body["data"]["admin_world_edits"][0]
        self.assertEqual(edit["recorded_by"], "partner")
        self.assertEqual(edit["recorded_as"], "admin_world_edit")
        self.assertFalse(edit["subject_prior_consent_fabricated"])
        self.assertEqual(edit["subject_opinion_cn"], "")

        # Only the subject may add her own opinion afterwards.
        code, body = self._mutate({
            "action": "note_admin_world_change",
            "change_summary_cn": "世界编辑器给窗台加了一盆薄荷",
            "subject_opinion_cn": "这盆先留着，我还挺喜欢的",
        })
        self.assertEqual(code, 200, body)
        edits = body["data"]["admin_world_edits"]
        self.assertEqual(edits[0]["recorded_by"], "subject")
        self.assertIn("挺喜欢", edits[0]["subject_opinion_cn"])
        self.assertFalse(edits[0]["subject_prior_consent_fabricated"])

        status, partner_env = ep_view.read_v04_elastic_play_view(
            self.day_root, has_subject_capability=False, now_iso=NOW
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(partner_env["data"]["grounded_alternatives"]), 1)
        self.assertEqual(len(partner_env["data"]["admin_world_edits"]), 2)


class GlobalContractTest(Ch45Base):
    def test_zero_endpoint_zero_llm_and_frozen_contracts(self) -> None:
        self.assertEqual(len(phone_tool.LIFE_UPDATE_SURFACES), 37)
        self.assertLessEqual(len(chiyo_plugin._PHONE_AND_WORLD_GUIDANCE), 1200)
        self.assertEqual(lcb.MAX_LIVED_CONTEXT_CHARS, 1800)

        for action in (
            "curate_personal_project",
            "propose_grounded_alternative",
            "note_admin_world_change",
        ):
            self.assertIn(action, ep_view.SUBJECT_ALLOWED_ACTIONS)
            self.assertIn(action, ep_view.ACTION_ALLOWED_FIELDS)
        self.assertIn("note_admin_world_change", ep_view.PARTNER_ALLOWED_ACTIONS)
        self.assertNotIn("curate_personal_project", ep_view.PARTNER_ALLOWED_ACTIONS)

        hints = phone_tool._SURFACE_SUPPORTED_HINTS["v04_elastic_play"]["subject_actions"]
        self.assertIn("curate_personal_project", hints)
        self.assertIn("propose_grounded_alternative", hints)
        self.assertIn("note_admin_world_change", hints)

        status, env = ep_view.read_v04_elastic_play_view(
            self.day_root, has_subject_capability=False, now_iso=NOW
        )
        self.assertEqual(status, 200)
        self.assertEqual(env["data"]["llm_calls"], 0)
        self.assertEqual(env["data"]["personal_projects"], [])

        wake = bridge.project_wake_and_interruption_reentry_zero_write(self.day_root, now_iso=NOW)
        aware = bridge.project_situational_capability_awareness_zero_write(self.day_root, now_iso=NOW)
        self.assertEqual(wake["llm_calls"], 0)
        self.assertEqual(aware["llm_calls"], 0)


if __name__ == "__main__":
    unittest.main()

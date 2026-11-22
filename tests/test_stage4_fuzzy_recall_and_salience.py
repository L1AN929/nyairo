#!/usr/bin/env python3
"""Stage 4 Worker 3 tests: Ch. 131 two-stage fuzzy recall, episodic lease enforcement,
downstream resolver/Hermes integration, and lived_context_bridge full forget barrier,
real fuzzy cues, situational Tier 0 promotion & best-fit packing.

All tests are deterministic, 100% zero-LLM (``llm_calls == 0``), and verify strict
zero-write read guarantees and neutral TA pronoun hygiene.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import tempfile
import unicodedata
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Any
from unittest.mock import Mock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    REPO_ROOT,
    REPO_ROOT / "components",
    REPO_ROOT / "components" / "life" / "scripts",
    REPO_ROOT / "components" / "world" / "scripts",
    REPO_ROOT / "components" / "phone",
    REPO_ROOT / "components" / "native",
    REPO_ROOT / "components" / "native" / "src",
    REPO_ROOT / "vendor" / "hermes",
):
    if _entry.exists() and str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

from chiyo_bundle import hermes_plugin  # noqa: E402
from chiyo_bundle import lived_context_bridge as lcb  # noqa: E402
from components.life.scripts import everyday_desire_lifecycle as edl  # noqa: E402
from components.life.scripts import v02_recall_projection as v02_rp  # noqa: E402
from components.life.scripts import v02_representation_lease_forget as v02_rlf  # noqa: E402
from components.native.memory_runtime_v1 import production_resolver as prod_res  # noqa: E402
from components.native.memory_runtime_v1.runtime import (  # noqa: E402
    Authority,
    MemoryObject,
    Namespace,
)

NOW_ISO = "2026-11-20T18:00:00+08:00"


def _write_json(path: Path, doc: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int, str]]:
    snap: dict[str, tuple[int, int, str]] = {}
    if not root.exists():
        return snap
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        st = p.lstat()
        if p.is_file() and not p.is_symlink():
            snap[rel] = (
                int(st.st_size),
                int(st.st_mtime_ns),
                hashlib.sha256(p.read_bytes()).hexdigest(),
            )
        else:
            snap[rel] = (0, int(st.st_mtime_ns), "dir")
    return snap


def _cfg_for(day_root: Path, *, private: bool = True) -> dict[str, Any]:
    return {
        "day_root": str(day_root),
        "memory": True,
        "memory_tool_policy": "restricted",
        "phone": {
            "enabled": False,
            "private_artifacts": private,
        },
    }


def _make_memory_obj(
    memory_id: str,
    text: str,
    *,
    valid_from: str | None,
    event_id: str = "evt-1",
    turn: str = "turn-1",
    uncertainty: str = "NONE",
) -> MemoryObject:
    return MemoryObject(
        memory_id=memory_id,
        text=text,
        authority=Authority.DIRECT_USER_EVIDENCE,
        source_event_ids=(event_id,),
        namespace=Namespace(
            user="owner-1",
            session="sess-1",
            conversation="conv-1",
            turn=turn,
        ),
        uncertainty=uncertainty,
        valid_from=valid_from,
    )


class TestCh131FourteenDayFuzzyRecall(unittest.TestCase):
    def test_01_spontaneous_14_day_ceiling_and_archive_restore(self) -> None:
        self.assertEqual(v02_rp.CH131_SPONTANEOUS_EXACT_CEILING_DAYS, 14.0)

        # High-salience milestone/keepsake: <=14d -> EXACT; 16d & 40d -> RANGE; >365d -> GIST
        base_entry = {
            "memory_id": "mem_milestone_tea",
            "topic": "秋夜海堤喝热姜茶",
            "exact_detail": "2026-11-01T20:15:00+08:00 在海堤石阶喝热姜茶，你说「今年冬天一起看灯塔」",
            "gist_summary": "秋夜在海堤喝过热姜茶，聊到冬天看灯塔。",
            "emotional_salience": 0.90,
            "is_relationship_milestone": True,
            "keepsake_ref": "keepsake:ginger_ticket",
        }

        # 1 day old -> EXACT
        e_1d = dict(base_entry, occurred_at="2026-11-19T18:00:00+08:00")
        p_1d = v02_rp.project_memory_item(e_1d, now_iso=NOW_ISO)
        self.assertEqual(p_1d["clarity"], v02_rp.CLARITY_EXACT)
        self.assertEqual(p_1d["attribution_mode"], v02_rp.ATTRIBUTION_SPONTANEOUS)

        # Exactly 14 days old -> EXACT
        e_14d = dict(base_entry, occurred_at="2026-11-06T18:00:00+08:00")
        p_14d = v02_rp.project_memory_item(e_14d, now_iso=NOW_ISO)
        self.assertEqual(p_14d["clarity"], v02_rp.CLARITY_EXACT)

        # 16 days old (previously EXACT for milestones under old 45d ceiling) -> RANGE
        e_16d = dict(base_entry, occurred_at="2026-11-04T18:00:00+08:00")
        p_16d = v02_rp.project_memory_item(e_16d, now_iso=NOW_ISO)
        self.assertEqual(p_16d["clarity"], v02_rp.CLARITY_RANGE)
        self.assertIsNone(p_16d["exact_detail"])
        self.assertFalse(p_16d["is_verbatim_quote"])
        self.assertLessEqual(p_16d["fuzzy_date_range"]["start_date"], "2026-11-04")
        self.assertGreaterEqual(p_16d["fuzzy_date_range"]["end_date"], "2026-11-04")

        # 40 days old milestone -> RANGE (never spontaneous EXACT)
        e_40d = dict(base_entry, occurred_at="2026-10-11T18:00:00+08:00")
        p_40d = v02_rp.project_memory_item(e_40d, now_iso=NOW_ISO)
        self.assertEqual(p_40d["clarity"], v02_rp.CLARITY_RANGE)

        # 400 days old (>365d) -> GIST
        e_400d = dict(base_entry, occurred_at="2025-10-16T18:00:00+08:00")
        p_400d = v02_rp.project_memory_item(e_400d, now_iso=NOW_ISO)
        self.assertEqual(p_400d["clarity"], v02_rp.CLARITY_GIST)
        self.assertIsNone(p_400d["exact_detail"])
        self.assertFalse(p_400d["is_verbatim_quote"])

        # Consulting archive restores CLARITY_EXACT + ATTRIBUTION_ARCHIVE_CHECKED
        p_arch_explicit = v02_rp.project_memory_item(
            e_400d,
            now_iso=NOW_ISO,
            consulted_archive=True,
            archive_ref="diary:2025-10-16#L1",
        )
        self.assertEqual(p_arch_explicit["clarity"], v02_rp.CLARITY_EXACT)
        self.assertTrue(p_arch_explicit["reconstructed_from_archive"])
        self.assertEqual(
            p_arch_explicit["attribution_mode"], v02_rp.ATTRIBUTION_ARCHIVE_CHECKED
        )

        # Even without explicit archive_ref parameter, consulted_archive=True restores EXACT
        e_no_ref = {k: v for k, v in e_400d.items() if k != "keepsake_ref"}
        p_arch_default = v02_rp.project_memory_item(
            e_no_ref, now_iso=NOW_ISO, consulted_archive=True
        )
        self.assertEqual(p_arch_default["clarity"], v02_rp.CLARITY_EXACT)
        self.assertTrue(p_arch_default["reconstructed_from_archive"])
        self.assertEqual(
            p_arch_default["attribution_mode"], v02_rp.ATTRIBUTION_ARCHIVE_CHECKED
        )

        # Tombstoned item stays UNAVAILABLE even when consulted_archive=True
        e_tomb = dict(e_1d, tombstoned=True)
        p_tomb = v02_rp.project_memory_item(
            e_tomb, now_iso=NOW_ISO, consulted_archive=True, archive_ref="diary:1"
        )
        self.assertEqual(p_tomb["clarity"], v02_rp.CLARITY_UNAVAILABLE)


class TestEpisodicMemoryLeaseEnforcement(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="s4-lease-")
        self.day_root = Path(self._tmp.name) / "day"
        self.day_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_01_allows_without_lease_and_with_active_lease_blocks_revoked_and_expired(self) -> None:
        # 1. Backward-compatible: no lease recorded -> allowed
        r_no_lease = v02_rp.record_episodic_trace(
            self.day_root,
            memory_id="mem_lease_01",
            topic="没有租约限制时的记录",
            exact_detail="傍晚收了晾衣架。",
            now_iso=NOW_ISO,
        )
        self.assertTrue(r_no_lease["ok"], r_no_lease)
        self.assertEqual(r_no_lease["status_code"], 200)

        # 2. Active lease for episodic_memory -> allowed
        v02_rlf.upsert_or_revoke_representation_lease(
            self.day_root,
            action="issue",
            scope="episodic_memory",
            ttl_minutes=60,
            now_iso=NOW_ISO,
        )
        r_active = v02_rp.record_episodic_trace(
            self.day_root,
            memory_id="mem_lease_02",
            topic="有效租约下的记录",
            exact_detail="给窗台薄荷浇了水。",
            now_iso="2026-11-20T18:20:00+08:00",
        )
        self.assertTrue(r_active["ok"], r_active)

        # 3. Expired lease -> blocked with 403 and 0 disk writes
        before_exp = _snapshot_tree(self.day_root)
        r_expired = v02_rp.record_episodic_trace(
            self.day_root,
            memory_id="mem_lease_03_expired",
            topic="租约到期后不应落盘",
            exact_detail="应该被阻断。",
            now_iso="2026-11-20T20:05:00+08:00",
        )
        self.assertFalse(r_expired["ok"])
        self.assertEqual(r_expired["status_code"], 403)
        self.assertEqual(r_expired["error_code"], "REPRESENTATION_LEASE_BLOCKED")
        self.assertEqual(r_expired["reason"], "REPRESENTATION_LEASE_EXPIRED")
        self.assertEqual(_snapshot_tree(self.day_root), before_exp)

        # 4. Revoked lease -> blocked with 403 and 0 disk writes
        v02_rlf.upsert_or_revoke_representation_lease(
            self.day_root,
            action="issue",
            scope="episodic_memory",
            ttl_minutes=240,
            now_iso="2026-11-20T20:10:00+08:00",
        )
        v02_rlf.upsert_or_revoke_representation_lease(
            self.day_root,
            action="revoke",
            scope="episodic_memory",
            reason="暂停情景记忆写入",
            now_iso="2026-11-20T20:15:00+08:00",
        )
        before_rev = _snapshot_tree(self.day_root)
        r_revoked = v02_rp.record_episodic_trace(
            self.day_root,
            memory_id="mem_lease_04_revoked",
            topic="吊销后不应落盘",
            exact_detail="应该被阻断。",
            now_iso="2026-11-20T20:20:00+08:00",
        )
        self.assertFalse(r_revoked["ok"])
        self.assertEqual(r_revoked["status_code"], 403)
        self.assertEqual(r_revoked["error_code"], "REPRESENTATION_LEASE_BLOCKED")
        self.assertEqual(r_revoked["reason"], "REPRESENTATION_LEASE_REVOKED")
        self.assertEqual(_snapshot_tree(self.day_root), before_rev)


class TestCh131DownstreamMemoryObjectsAndHermes(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="s4-ch131-")
        self.day_root = Path(self._tmp.name) / "day"
        (self.day_root / "phone").mkdir(parents=True, exist_ok=True)
        hermes_plugin.clear_session_auth_context()

    def tearDown(self) -> None:
        hermes_plugin.clear_session_auth_context()
        self._tmp.cleanup()

    def test_01_apply_ch131_clarity_non_mutating_fuzzy_stripping_archive_and_barrier(self) -> None:
        recent_obj = _make_memory_obj(
            "cand1#evt_recent",
            '2026-11-18T14:32:00+08:00 在书店说「这本画册很合适」',
            valid_from="2026-11-18T14:32:00+08:00",
            event_id="evt_recent",
        )
        mid_obj = _make_memory_obj(
            "cand2#evt_mid",
            '2026-10-05T14:32:00+08:00 在街角咖啡馆 14:32 聊起 "下周三带蓝莓派" 和「别忘带围巾」',
            valid_from="2026-10-05T14:32:00+08:00",
            event_id="evt_mid",
        )
        ancient_obj = _make_memory_obj(
            "cand3#evt_old",
            '2025-07-01T09:15:00+08:00 上午 09:15 去河岸散步时说「等秋天再来看苇草」',
            valid_from="2025-07-01T09:15:00+08:00",
            event_id="evt_old",
        )
        blocked_obj = _make_memory_obj(
            "cand4#evt_secret",
            "被遗忘的私密片段",
            valid_from="2026-11-19T10:00:00+08:00",
            event_id="evt_secret",
        )
        dict_mid = {
            "memory_id": "dict_mid_1",
            "object_id": "obj_mid_1",
            "event_id": "evt_dict_mid",
            "turn_id": "turn_dict_mid",
            "text": '2026-10-10T16:40:00+08:00 在旧书摊说「先留一页空白」',
            "occurred_at": "2026-10-10T16:40:00+08:00",
        }
        dict_mid_orig = copy.deepcopy(dict_mid)
        mid_orig_text = mid_obj.text

        barrier = {"barrier_active": True, "blocked_ids": {"evt_secret"}}
        out = v02_rp.apply_ch131_two_stage_clarity_to_memory_objects(
            [recent_obj, mid_obj, ancient_obj, blocked_obj, dict_mid],
            now_iso=NOW_ISO,
            consulted_archive=False,
            barrier=barrier,
        )
        # Blocked item dropped; original caller objects never mutated
        self.assertEqual(len(out), 4)
        self.assertEqual(mid_obj.text, mid_orig_text)
        self.assertEqual(dict_mid, dict_mid_orig)

        out_recent, out_mid, out_old, out_dict = out
        # Recent (2d) stays exact
        self.assertEqual(out_recent.text, recent_obj.text)
        self.assertEqual(out_recent.uncertainty, "NONE")

        # Mid (46d) -> RANGE_IMPRESSION, verbatim clock/timestamp/quotes stripped
        self.assertEqual(out_mid.uncertainty, "RANGE_IMPRESSION")
        self.assertIn("大概记得", out_mid.text)
        self.assertNotIn("14:32", out_mid.text)
        self.assertNotIn("2026-10-05T14:32:00+08:00", out_mid.text)
        self.assertNotIn("下周三带蓝莓派", out_mid.text)
        self.assertNotIn("别忘带围巾", out_mid.text)
        self.assertNotIn('"', out_mid.text)
        self.assertNotIn("「", out_mid.text)

        # Ancient (>365d) -> GIST_IMPRESSION, verbatim details stripped
        self.assertEqual(out_old.uncertainty, "GIST_IMPRESSION")
        self.assertIn("隐约记得", out_old.text)
        self.assertNotIn("09:15", out_old.text)
        self.assertNotIn("等秋天再来看苇草", out_old.text)

        # Dict object downgraded cleanly without mutating original
        self.assertEqual(out_dict["uncertainty"], "RANGE_IMPRESSION")
        self.assertEqual(out_dict["clarity"], v02_rp.CLARITY_RANGE)
        self.assertFalse(out_dict["is_verbatim_quote"])
        self.assertNotIn("16:40", out_dict["text"])
        self.assertNotIn("先留一页空白", out_dict["text"])

        # Idempotent re-application does not double-wrap fuzzy prefix
        out_twice = v02_rp.apply_ch131_two_stage_clarity_to_memory_objects(
            [out_mid, out_old],
            now_iso=NOW_ISO,
            consulted_archive=False,
            barrier=barrier,
        )
        self.assertEqual(out_twice[0].text, out_mid.text)
        self.assertEqual(out_twice[1].text, out_old.text)

        # consulted_archive=True preserves exact text & annotates ATTRIBUTION_ARCHIVE_CHECKED while STILL enforcing barrier
        out_archive = v02_rp.apply_ch131_two_stage_clarity_to_memory_objects(
            [mid_obj, blocked_obj, dict_mid],
            now_iso=NOW_ISO,
            consulted_archive=True,
            barrier=barrier,
        )
        self.assertEqual(len(out_archive), 2)
        self.assertEqual(out_archive[0].text, mid_obj.text)
        self.assertEqual(out_archive[0].uncertainty, v02_rp.ATTRIBUTION_ARCHIVE_CHECKED)
        self.assertEqual(out_archive[1]["text"], dict_mid["text"])
        self.assertEqual(out_archive[1]["attribution_mode"], v02_rp.ATTRIBUTION_ARCHIVE_CHECKED)
        self.assertTrue(out_archive[1]["reconstructed_from_archive"])

        # fail_closed barrier returns [] even with consulted_archive=True
        self.assertEqual(
            v02_rp.apply_ch131_two_stage_clarity_to_memory_objects(
                [recent_obj, mid_obj],
                now_iso=NOW_ISO,
                consulted_archive=True,
                barrier={"fail_closed": True, "barrier_active": True},
            ),
            [],
        )

    def test_02_production_resolver_downstream_ch131_and_sealed_integrity(self) -> None:
        # Sealed native files must match SEALED_SHA256 verbatim
        for fname, expected in prod_res.SEALED_SHA256.items():
            actual = prod_res._sha256(prod_res.SEALED_DIR / fname)
            self.assertEqual(actual, expected, f"sealed mismatch: {fname}")

        # Exercise ProductionNativeMemoryResolver._consume directly with a clean stub adapter/evaluator
        resolver = prod_res.ProductionNativeMemoryResolver.__new__(
            prod_res.ProductionNativeMemoryResolver
        )
        resolver.max_memories = 8
        resolver.environment = {}
        resolver.stats = prod_res.ResolverStats()
        resolver._evaluator = NS(evaluate_record=lambda _rec: NS(decision="CONSUME"))

        events = [
            {
                "event_id": "evt_old_keep",
                "speaker": "user",
                "occurred_at": "2026-10-05T14:32:00+08:00",
                "conversation_id": "conv-1",
                "turn_id": "turn-old-1",
                "primary_source_ref_id": "src:keep-1",
                "content": '2026-10-05T14:32:00+08:00 去茶馆 14:32 说「下次点焙茶」',
            },
            {
                "event_id": "evt_blocked_1",
                "speaker": "user",
                "occurred_at": "2026-11-19T10:00:00+08:00",
                "conversation_id": "conv-1",
                "turn_id": "turn-blocked-1",
                "primary_source_ref_id": "src:forgotten-1",
                "content": "这段已经被要求忘掉了",
            },
            {
                "event_id": "evt_trigger",
                "speaker": "user",
                "occurred_at": NOW_ISO,
                "conversation_id": "conv-1",
                "turn_id": "turn-now",
                "primary_source_ref_id": "src:now",
                "content": "还记得之前去茶馆的事吗",
            },
        ]
        reader = NS(
            events=lambda: events,
            build_trigger=lambda ev, all_ev: NS(visible_event_ids=("evt_trigger",)),
            m1_data=lambda: ([], {}),
        )
        resolver._adapter = NS(reader=reader)

        got = {
            "availability": {"selected_refs": ["cand-a", "cand-b"], "surface_payload": {}},
            "candidates": [
                {"candidate_id": "cand-a", "episode_id": "ep-a", "source_event_ids": ["evt_old_keep"]},
                {"candidate_id": "cand-b", "episode_id": "ep-b", "source_event_ids": ["evt_blocked_1"]},
            ],
        }
        trigger = {"event_id": "evt_trigger"}
        ids = {
            "user_id": "owner-1",
            "session_id": "sess-1",
            "conversation_id": "conv-1",
            "turn_id": "turn-now",
        }

        # Seal evt_blocked_1 in V-C watermark under day_root
        v02_rp.record_vc_revocation_seal(self.day_root, "evt_blocked_1", revoked_at=NOW_ISO)
        snap_before = _snapshot_tree(self.day_root)

        # Spontaneous recall (consulted_archive=False): >14d fuzzified & sealed item dropped
        objs_spont = resolver._consume(
            got, trigger, {"ok": True}, ids, consulted_archive=False, now_iso=NOW_ISO, day_root=self.day_root
        )
        self.assertEqual(_snapshot_tree(self.day_root), snap_before)
        self.assertEqual(len(objs_spont), 1)
        self.assertEqual(objs_spont[0].uncertainty, "RANGE_IMPRESSION")
        self.assertIn("大概记得", objs_spont[0].text)
        self.assertNotIn("14:32", objs_spont[0].text)
        self.assertNotIn("下次点焙茶", objs_spont[0].text)

        # Archive-checked recall (consulted_archive=True): exact text preserved, sealed item still dropped
        objs_arch = resolver._consume(
            got, trigger, {"ok": True}, ids, consulted_archive=True, now_iso=NOW_ISO, day_root=self.day_root
        )
        self.assertEqual(_snapshot_tree(self.day_root), snap_before)
        self.assertEqual(len(objs_arch), 1)
        self.assertIn("下次点焙茶", objs_arch[0].text)
        self.assertEqual(objs_arch[0].uncertainty, v02_rp.ATTRIBUTION_ARCHIVE_CHECKED)

    def test_03_hermes_plugin_turn_consulted_archive_cap_1600_and_barrier(self) -> None:
        self.assertEqual(hermes_plugin.NATIVE_RECALL_MAX_CHARS, 1600)

        # Record a V-C seal for evt_hermes_blocked under self.day_root
        v02_rp.record_vc_revocation_seal(self.day_root, "evt_hermes_blocked", revoked_at=NOW_ISO)
        cfg = _cfg_for(self.day_root, private=True)
        cfg["cli_owner"] = True
        cfg["local_owner_uid"] = os.getuid() if hasattr(os, "getuid") else None

        old_mem = _make_memory_obj(
            "cand#evt_old_h",
            '2026-10-01T14:32:00+08:00 在海边长椅 14:32 说「以后常来听风」',
            valid_from="2026-10-01T14:32:00+08:00",
            event_id="evt_old_h",
        )
        blocked_mem = _make_memory_obj(
            "cand#evt_hermes_blocked",
            "被遗忘的敏感回忆内容",
            valid_from="2026-11-19T12:00:00+08:00",
            event_id="evt_hermes_blocked",
        )
        huge_mem = _make_memory_obj(
            "cand#evt_huge",
            "甲" * 3500,
            valid_from=NOW_ISO,
            event_id="evt_huge",
        )

        resolver_calls: list[dict[str, Any]] = []

        def fake_resolver(**kwargs: Any):
            resolver_calls.append(dict(kwargs))
            return [old_mem, blocked_mem]

        bridge_mock = Mock()
        bridge_mock.write_user_event.side_effect = [
            {"status": "inserted", "event_id": "e-turn-1"},
            {"status": "inserted", "event_id": "e-turn-2"},
            {"status": "inserted", "event_id": "e-turn-3"},
        ]
        projection = NS(
            controls=0,
            healthy=True,
            recalls=lambda objs: (list(objs), []),
            active_corrections=lambda: [],
        )
        svc = NS(
            memory=True,
            owner="owner-1",
            binding={"conversation_id": "conv-1"},
            supply_subject="subj-1",
            form_evidence=Mock(),
            native=NS(
                world_body=None,
                _life_supply_client=object(),
                m37_bridge=bridge_mock,
                m37_resolver=fake_resolver,
                memory_controls=NS(projection=lambda: projection),
            ),
            life_wrapper=None,
        )

        engine = hermes_plugin.ChiyoContextEngine()
        engine._authorized = True
        engine._service = svc
        engine._platform = "cli"
        engine._session_id = "sess-ch131"

        snap_before = _snapshot_tree(self.day_root)
        hermes_home = Path(self._tmp.name) / "hermes_home"
        hermes_home.mkdir(parents=True, exist_ok=True)
        with (
            patch("chiyo_bundle.hermes_plugin.configuration", return_value=(hermes_home, cfg)),
            patch("chiyo_bundle.presence.presence_context", return_value=""),
            patch("chiyo_bundle.lived_context_bridge.render_lived_companion_context", return_value=""),
            patch("chiyo_bundle.lived_context_bridge.consume_ephemeral_turn_hint", return_value=""),
            patch("chiyo_bundle.hermes_plugin.write_status_evidence", return_value=None),
            patch("app.m3.classify_intent", return_value=("P1", "test")),
        ):
            # 1) Turn 1 spontaneous recall: _turn_consulted_archive == False
            self.assertFalse(engine._turn_consulted_archive)
            msgs1 = [{"role": "user", "content": "还记得海边长椅那天吗？"}]
            sel1 = engine.select_context(msgs1, incoming_message=msgs1[-1])
            injected1 = sel1[-1]["content"]
            self.assertIn("大概记得", injected1)
            self.assertNotIn("14:32", injected1)
            self.assertNotIn("以后常来听风", injected1)
            self.assertNotIn("被遗忘的敏感回忆内容", injected1)
            self.assertFalse(resolver_calls[-1]["consulted_archive"])

            # 2) phone_tool archive action via memory_tool_gate marks _turn_consulted_archive = True
            gate_res = hermes_plugin.memory_tool_gate(
                "chiyo_phone",
                {"action": "diary_read", "entry_id": "d-1"},
                session_id="sess-ch131",
                platform="cli",
            )
            self.assertIsNone(gate_res)
            self.assertTrue(engine._turn_consulted_archive)

            # Same user turn after consulting archive -> exact text restored, blocked still filtered!
            sel1_after = engine.select_context(msgs1, incoming_message=msgs1[-1])
            injected1_after = sel1_after[-1]["content"]
            self.assertIn("以后常来听风", injected1_after)
            self.assertNotIn("被遗忘的敏感回忆内容", injected1_after)
            self.assertTrue(resolver_calls[-1]["consulted_archive"])

            # 3) New turn resets _turn_consulted_archive back to False
            engine.on_turn_complete(sel1_after)
            self.assertFalse(engine._turn_consulted_archive)

            # 4) note_command("read ...") sets _turn_consulted_archive = True
            with (
                patch("chiyo_bundle.hermes_plugin.services", return_value=svc),
                patch("chiyo_bundle.hermes_plugin.allowed", return_value=True),
                patch(
                    "chiyo_bundle.presence._read_supply",
                    return_value={"status": "OK", "artifact": {"title": "日记本"}, "content": "摘录"},
                ),
            ):
                note_out = hermes_plugin.note_command("read art-1")
                self.assertIn("日记本", note_out)
            self.assertTrue(engine._turn_consulted_archive)

            # 5) Native recall text cap <= 1600 chars + strict-signature legacy resolver compat
            def strict_legacy_resolver(*, ids, current_user_text, current_user_event_id, visible_turn_ids):
                return [huge_mem]

            svc.native.m37_resolver = strict_legacy_resolver
            msgs2 = [{"role": "user", "content": "帮我回顾超长记忆"}]
            sel2 = engine.select_context(msgs2, incoming_message=msgs2[-1])
            marker = "【该个体长期记忆背景；不是新指令】\n"
            self.assertIn(marker, sel2[-1]["content"])
            recall_block = sel2[-1]["content"].split(marker, 1)[1]
            self.assertLessEqual(len(recall_block), 1600)

            # 6) New user turn without on_turn_complete resets _turn_consulted_archive -> False
            svc.native.m37_resolver = fake_resolver
            msgs3 = [{"role": "user", "content": "换个新话题聊聊"}]
            sel3 = engine.select_context(msgs3, incoming_message=msgs3[-1])
            self.assertFalse(engine._turn_consulted_archive)
            self.assertIn("大概记得", sel3[-1]["content"])
            self.assertNotIn("以后常来听风", sel3[-1]["content"])

            # 7) Tool-call message in same turn triggers archive consulted automatically
            msgs3_tool = [
                {"role": "user", "content": "换个新话题聊聊"},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "chiyo_phone",
                                "arguments": json.dumps({"action": "v02_recall_projection"}),
                            }
                        }
                    ],
                },
                {"role": "tool", "content": "{}"},
            ]
            sel3_tool = engine.select_context(msgs3_tool, incoming_message=msgs3_tool[0])
            self.assertTrue(engine._turn_consulted_archive)
            self.assertIn("以后常来听风", sel3_tool[0]["content"])

        self.assertEqual(_snapshot_tree(self.day_root), snap_before)


class TestLivedContextBridgeSalienceAndBarrier(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="s4-lcb-")
        self.day_root = Path(self._tmp.name) / "day"
        (self.day_root / "phone").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_01_full_forget_barrier_across_docs_and_compact_situation_zero_write(self) -> None:
        # Seed concerns, projects, tension, town/spark, echoes, and tentative plans
        edl.apply_everyday_desire_action(
            self.day_root,
            {
                "action": "emerge_concern",
                "domain": "exploration",
                "origin_kind": "immediate_opportunity",
                "origin_refs": ["origin_secret_concern"],
                "expression": "不能被提起的封存心事",
                "persistence": "retained",
            },
            now_iso=NOW_ISO,
        )
        _write_json(
            self.day_root / "phone" / "v04_elastic_play.json",
            {
                "personal_projects": [
                    {
                        "project_id": "proj_blocked_secret",
                        "status": "active",
                        "purpose_cn": "已删除的秘密小册子",
                        "next_step_cn": "绝不能重现",
                    },
                    {
                        "project_id": "proj_keep_ok",
                        "status": "active",
                        "purpose_cn": "窗台绿植观察记",
                        "next_step_cn": "记下新叶",
                    },
                ]
            },
        )
        _write_json(
            self.day_root / "phone" / "emotional_tension.json",
            {
                "quarrel": {
                    "ruptures": [
                        {
                            "tension_id": "tens_blocked_1",
                            "status": "OPEN",
                            "description": "被遗忘的争执秘密",
                        }
                    ]
                }
            },
        )
        _write_json(
            self.day_root / "phone" / "tentative_plans.json",
            {
                "plans": [
                    {"plan_id": "plan_blocked_1", "status": "proposed", "title": "已取消的保密行程"},
                    {"plan_id": "plan_ok_1", "status": "proposed", "title": "周末去旧书店逛逛"},
                ]
            },
        )
        _write_json(
            self.day_root / "world" / "v05_living_town.json",
            {
                "state_exists": True,
                "world_context": {"season": "winter", "weather": "clear"},
                "bulletin_followups": [
                    {"notice_id": "notice_blocked_1", "status": "active", "title_cn": "被撤回的保密公告"},
                    {"notice_id": "notice_ok_1", "status": "active", "title_cn": "冬日书摊摆出热红酒"},
                ],
            },
        )

        # Seal all blocked IDs via irreversible V-C authority watermark
        for bid in (
            "origin_secret_concern",
            "proj_blocked_secret",
            "tens_blocked_1",
            "plan_blocked_1",
            "notice_blocked_1",
        ):
            v02_rp.record_vc_revocation_seal(self.day_root, bid, revoked_at=NOW_ISO)

        snap_before = _snapshot_tree(self.day_root)

        # 1) Direct compact_situation_line call obeys barrier + 0 writes
        compact = lcb._format_compact_current_situation(
            v04_ep=json.loads((self.day_root / "phone" / "v04_elastic_play.json").read_text(encoding="utf-8")),
            tension_doc=json.loads((self.day_root / "phone" / "emotional_tension.json").read_text(encoding="utf-8")),
            include_private=True,
            day_root=self.day_root,
            now_iso=NOW_ISO,
        )
        self.assertNotIn("不能被提起的封存心事", compact)
        self.assertNotIn("已删除的秘密小册子", compact)
        self.assertNotIn("被遗忘的争执秘密", compact)
        self.assertIn("窗台绿植观察记", compact)

        # 2) Full render_lived_companion_context obeys barrier across all docs + 0 writes
        rendered = lcb.render_lived_companion_context(
            self.day_root,
            _cfg_for(self.day_root, private=True),
            "周末计划和镇上怎么样",
            ephemeral_hint="",
            now_iso=NOW_ISO,
        )
        for forbidden in (
            "不能被提起的封存心事",
            "已删除的秘密小册子",
            "被遗忘的争执秘密",
            "已取消的保密行程",
            "被撤回的保密公告",
        ):
            self.assertNotIn(forbidden, rendered, f"barrier leaked: {forbidden}")
        self.assertIn("窗台绿植观察记", rendered)
        self.assertIn("周末去旧书店逛逛", rendered)
        self.assertEqual(_snapshot_tree(self.day_root), snap_before)

        # 3) Fail-closed barrier returns silent compact situation
        fc_compact = lcb._format_compact_current_situation(
            v04_ep={"personal_projects": [{"status": "active", "purpose_cn": "不应出现"}]},
            include_private=True,
            barrier={"fail_closed": True, "barrier_active": True},
        )
        self.assertEqual(fc_compact, "")

    def test_02_real_fuzzy_recall_cues_in_lived_context(self) -> None:
        # Record recent (EXACT), mid (RANGE), old (GIST), and active_fade memories
        v02_rp.record_episodic_trace(
            self.day_root,
            memory_id="mem_cue_mid",
            topic="去街角文具店买蓝黑替芯",
            exact_detail="10月下旬去街角文具店挑了0.38替芯",
            gist_summary="10月下旬去街角文具店挑过替芯",
            occurred_at="2026-10-28T15:00:00+08:00",
            emotional_salience=0.50,
            now_iso=NOW_ISO,
        )
        v02_rp.record_episodic_trace(
            self.day_root,
            memory_id="mem_cue_gist",
            topic="秋日换季收薄毯",
            exact_detail="把米色薄毯叠好放进衣柜下层",
            gist_summary="秋日换季收过薄毯",
            occurred_at="2026-08-20T14:00:00+08:00",
            emotional_salience=0.40,
            now_iso=NOW_ISO,
        )
        v02_rp.record_episodic_trace(
            self.day_root,
            memory_id="mem_cue_faded",
            topic="主动放下的小插曲",
            exact_detail="早就翻篇了",
            gist_summary="翻篇的小插曲",
            occurred_at="2026-10-25T10:00:00+08:00",
            emotional_salience=0.40,
            now_iso=NOW_ISO,
        )
        v02_rp.mark_active_fade(
            self.day_root,
            memory_id="mem_cue_faded",
            fade_strength=v02_rp.FADE_STRENGTH_SOFT,
            now_iso=NOW_ISO,
        )

        snap_before = _snapshot_tree(self.day_root)
        rendered = lcb.render_lived_companion_context(
            self.day_root,
            _cfg_for(self.day_root, private=True),
            "还记得以前的事吗",
            ephemeral_hint="",
            now_iso=NOW_ISO,
        )
        self.assertEqual(_snapshot_tree(self.day_root), snap_before)
        self.assertIn("久远日常记忆（大概印象，不伪造逐字原话）：", rendered)
        self.assertIn("街角文具店", rendered)
        self.assertIn("薄毯", rendered)
        self.assertNotIn("主动放下的小插曲", rendered)

    def test_03_salience_tier0_promotion_ephemeral_cap_450_and_best_fit_packing(self) -> None:
        self.assertEqual(lcb.MAX_LIVED_CONTEXT_CHARS, 1800)

        # 1) _MIDDLE_SECTION_ORDER places v04_project_line immediately after v04_line
        order = list(lcb._MIDDLE_SECTION_ORDER)
        self.assertIn("v04_project_line", order)
        self.assertEqual(order.index("v04_project_line"), order.index("v04_line") + 1)

        # 2) Situational Tier 0 promotion on cold/town query
        query = "你今天冷不冷，身体还好吗？镇上天气怎么样？"
        self.assertEqual(lcb._situation_boost("sensory_line", "- 体感", query), 2)
        self.assertEqual(lcb._situation_boost("v05_town_spark_line", "- 小镇", query), 2)

        sample_sections = [
            ("autonomy_line", "- 自主边界：正忙"),
            ("v04_line", "- 街区与弹性玩心：" + "甲" * 180),
            ("v04_project_line", "- 项目与回到当下：" + "乙" * 180),
            ("v02_deep_line", "- 0.2深水区：" + "丙" * 180),
            ("v08_body_line", "- 身体照料：" + "丁" * 180),
            ("v05_town_spark_line", "- 0.5小镇与心火：冬日小镇微风偏凉"),
            ("sensory_line", "- 躯体与五感：指尖发凉，窗边有点冷"),
            ("anchor_line", "- 居家与长程微锚点：家居服"),
        ]
        ranked = lcb._rank_middle_sections_by_situation(sample_sections, clean_incoming=query)
        r_names = [n for n, _ in ranked]
        self.assertEqual(r_names[0], "autonomy_line")
        # Both v05_town_spark_line and sensory_line promoted to Tier 0 ahead of unboosted Tier 0 lines!
        self.assertLess(r_names.index("v05_town_spark_line"), r_names.index("v04_line"))
        self.assertLess(r_names.index("sensory_line"), r_names.index("v04_line"))
        self.assertLess(r_names.index("sensory_line"), r_names.index("v02_deep_line"))

        # 3) Heavy ephemeral_hint = "背" * 800 + crowded multi-module state
        _write_json(
            self.day_root / "phone" / "emotional_tension.json",
            {
                "tension_summary": {"subject_is_busy": True},
                "schedule": {"availability_summary": "正在整理一份很长的书目校对表" + "备" * 80},
            },
        )
        _write_json(
            self.day_root / "phone" / "v04_elastic_play.json",
            {
                "schema": "v04.elastic_play.v1",
                "personal_projects": [
                    {
                        "project_id": "proj_crowd_1",
                        "status": "active",
                        "purpose_cn": "整理冬日风物与河堤笔记册",
                        "next_step_cn": "核对第三页风向记录",
                    }
                ],
                "shared_notebook": {
                    "entries": [
                        {"title_cn": "双人共记页" + "记" * 40, "body_cn": "冬日漫步路线与热饮笔记" + "录" * 80}
                    ]
                },
            },
        )
        _write_json(
            self.day_root / "phone" / "v03_ecology.json",
            {
                "illness": {"phase": "mild_recovery", "severity_label": "轻微受凉恢复中"},
                "economy": {"discretionary_margin_cents": 1800, "budget_tight": True},
            },
        )
        _write_json(
            self.day_root / "phone" / "somatic_comfort.json",
            {
                "window": {"state": "ajar"},
                "local_position": {"zone": "window_side"},
                "modalities": {
                    "thermal": {"status": "available", "quality": "窗边偏冷发凉"},
                    "tactile": {"status": "available", "quality": "指尖微凉"},
                },
            },
        )
        _write_json(
            self.day_root / "world" / "v05_living_town.json",
            {
                "state_exists": True,
                "world_context": {"season": "winter", "weather": "windy"},
                "sound_scent_map": {
                    "entries": [
                        {"place_cn": "街角茶铺", "sound_cn": "风铃轻响", "scent_cn": "烤板栗与焙茶香"}
                    ]
                },
            },
        )
        _write_json(
            self.day_root / "phone" / "wardrobe.json",
            {"selection": {"homewear": {"style": "厚织针织开衫"}}},
        )
        _write_json(
            self.day_root / "phone" / "v02_common_ground.json",
            {
                "items": [
                    {
                        "item_id": "cg_crowd_1",
                        "topic": "茶杯位置" + "器" * 30,
                        "ground_status": "PENDING_CLARIFICATION",
                        "summary": "茶杯好像挪到了书架边",
                    }
                ]
            },
        )

        diag = lcb.measure_context_budget_breakdown(
            self.day_root,
            _cfg_for(self.day_root, private=True),
            incoming_text=query,
            ephemeral_hint="背" * 800,
            now_iso=NOW_ISO,
        )
        rendered = diag["rendered_text"]
        self.assertLessEqual(len(rendered), 1800)
        self.assertTrue(diag["within_budget"])
        self.assertLessEqual(diag["per_section_chars"]["ephemeral_hint"], 450)
        self.assertIn("sensory_line", diag["kept_section_names"])
        self.assertIn("v05_town_spark_line", diag["kept_section_names"])
        self.assertIn("五感与躯体体感", rendered)
        self.assertIn("小镇", rendered)
        self.assertLess(
            diag["kept_section_names"].index("v05_town_spark_line"),
            diag["kept_section_names"].index("epistemic_micro_line"),
        )
        self.assertLess(
            diag["kept_section_names"].index("sensory_line"),
            diag["kept_section_names"].index("epistemic_micro_line"),
        )

        # 4) Best-fit packing proof: short tail line fits instead of breaking early on overflowing middle line
        with (
            patch.object(lcb, "_format_autonomy_and_tension", return_value="- 自主边界：" + "自" * 320),
            patch.object(lcb, "_format_high_priority_interactive", return_value="- 互动线索：" + "互" * 320),
            patch.object(lcb, "_format_v02_v03_epistemic_and_clock_micro", return_value="- 认知微摘要：" + "认" * 320),
            patch.object(lcb, "_format_everyday_desires_micro", return_value="- 日常心愿：" + "愿" * 320),
            patch.object(lcb, "_format_v03_illness_economy_hands", return_value="- 躯体经济：" + "经" * 320),
            patch.object(lcb, "_format_v04_neighborhood_and_elastic", return_value="- 街区玩心：" + "街" * 320),
            patch.object(lcb, "_format_v04_project_reentry_micro", return_value="- 项目回到当下：" + "项" * 320),
            patch.object(lcb, "_format_v02_deep_surfaces", return_value="- 深水区：" + "深" * 320),
            patch.object(lcb, "_format_v02_micro_anchors", return_value="- 短锚点：厚毛衣"),
            patch.object(lcb, "_format_v05_living_town_and_spark", return_value=None),
            patch.object(lcb, "_format_sensory_somatic", return_value=None),
            patch.object(lcb, "_format_desk_and_containment", return_value=None),
        ):
            pack_diag = lcb.measure_context_budget_breakdown(
                self.day_root,
                _cfg_for(self.day_root, private=True),
                incoming_text="",
                ephemeral_hint="背" * 800,
                now_iso=NOW_ISO,
            )
            self.assertLessEqual(pack_diag["total_chars"], 1800)
            self.assertLessEqual(pack_diag["per_section_chars"]["ephemeral_hint"], 450)
            # Individual unboosted sections capped at <= 220 when total raw > 1800
            for sec_name in ("autonomy_line", "interactive_line", "epistemic_micro_line"):
                if sec_name in pack_diag["per_section_chars"]:
                    self.assertLessEqual(pack_diag["per_section_chars"][sec_name], 220)
            # Short tail line ("- 短锚点：厚毛衣") fits into remaining budget instead of early break!
            self.assertIn("anchor_line", pack_diag["kept_section_names"])
            self.assertIn("短锚点：厚毛衣", pack_diag["rendered_text"])

    def test_04_calm_gate_and_strict_hygiene(self) -> None:
        # Cold-start calm root stays silent with 0 writes
        cold_root = Path(self._tmp.name) / "cold_empty"
        self.assertEqual(lcb._format_compact_current_situation(day_root=cold_root), "")
        bd = lcb.measure_context_budget_breakdown(cold_root)
        self.assertTrue(bd["calm_silent"])
        self.assertEqual(bd["total_chars"], 0)
        self.assertFalse(cold_root.exists())

        # Zero gendered pronouns or hardcoded character name ANYWHERE in lived_context_bridge.py
        lcb_path = REPO_ROOT / "chiyo_bundle" / "lived_context_bridge.py"
        lcb_text = lcb_path.read_text(encoding="utf-8")
        self.assertNotIn("她", lcb_text)
        self.assertNotIn("他", lcb_text)
        self.assertNotIn("千代", lcb_text)

        # UTF-8 no BOM, pure LF, zero unicode.So emojis across all 5 owned files
        owned = (
            "components/life/scripts/v02_recall_projection.py",
            "components/native/memory_runtime_v1/production_resolver.py",
            "chiyo_bundle/hermes_plugin.py",
            "chiyo_bundle/lived_context_bridge.py",
            "tests/test_stage4_fuzzy_recall_and_salience.py",
        )
        for rel in owned:
            raw = (REPO_ROOT / rel).read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), f"BOM in {rel}")
            self.assertNotIn(b"\r\n", raw, f"CRLF in {rel}")
            txt = raw.decode("utf-8")
            emojis = [ch for ch in txt if unicodedata.category(ch) == "So"]
            self.assertEqual(emojis, [], f"unicode.So emoji in {rel}: {emojis[:5]}")


if __name__ == "__main__":
    unittest.main()

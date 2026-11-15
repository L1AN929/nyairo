#!/usr/bin/env python3
"""Unit & integration tests for 0.3 Clock/Evaporation + Owner Stance Lineage & Nested World Containment.

Covers:
1. ``components.life.scripts.v03_clock_evaporation``:
   - Pure GET zero-write on absent state
   - Three-clock projection & monotonic guard (``advance_projection``)
   - 6h damp outerwear drying (``damp -> dry``)
   - 30m hot drink cooling (``warm -> tepid -> room_temp``)
   - 72h missing-partner note fading naturally with zero absence debt
   - ``EVAPORABLE`` TTL silent evaporation (zero tombstones, zero audit backlog)
   - ``409 EVAPORABLE_FORBIDDEN_FOR_ACCOUNTABLE_STATE`` for wallet/money, object placement/containment, and intimacy consent
   - Discrete ``UNKNOWN`` actions across multi-day offline gaps NEVER guessed into APPLIED/FAILED
   - ``cognitive_backfill_forbidden == True`` & ``causal_ticks`` untouched across offline gap
   - Meaningless idle / halfway abandon with zero growth score delta & zero productivity penalty
   - ``sync_v02_slices`` deterministic sync from committed world facts
2. ``components.life.scripts.life_attention_stance_containment`` + ``components.phone.stance_containment_view``:
   - Pure GET zero-write across both ``life/`` and ``phone/`` directories + neutral ``TA`` default
   - Mounted reading material / Supply revision auto-probe & mismatch guard (ADR-047)
   - Closed ``facet`` enum, ``fix_stance_typo`` CAS, ``revise_stance`` ``SUPERSEDED`` lineage
   - ``mark_source_updated`` marking ``SOURCE_REVISION_STALE`` while preserving original text
   - ``revoke_stance_source`` marking ``SOURCE_WITHDRAWN``, purging ``text``/``cached_excerpt``/``private_note``, recording ``withdrawn_sources`` tombstones, preventing resurrection on rebuild/replay
   - Nested container topology (``parent_container_id``) & ``open_state`` (``CONTAINER_LOCKED`` / ``CONTAINER_CLOSED``) (ADR-048)
   - ``pick_and_put_in_container`` non-atomic partial completion leaving item in ``hand_slot`` on disk
   - ``partner_move_container`` spatial impression lag until ``observe_current_room``
   - Bidirectional ledger synchronization between ``life/attention_stance_containment.json`` and ``phone/stance_containment.json``
   - Frozen anchor SHA256 verification
"""

from __future__ import annotations

import ast
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from components.life.scripts import (  # noqa: E402
    life_attention_stance_containment as owner_sc,
    v03_clock_evaporation as clock_evap,
)
from components.phone import stance_containment_view as phone_sc  # noqa: E402

FROZEN_ASSEMBLY_SHA256 = "5f51c9e82a417d9354d08608841d3472fc5a869e6bd3c9d9a7ebed0b15397f7a"
FROZEN_SHARING_SHA256 = "0becf7df4d9b34881817ff6f1defdcc15929eacbd6183d4c8a38c5a048d46038"
FORBIDDEN_IMPORTS = {"random", "secrets", "openai", "anthropic", "httpx", "requests"}


class V03ClockEvaporationStanceWorldTest(unittest.TestCase):
    """Comprehensive deterministic tests (>= 24 cases) for 0.3 Clock/Evaporation + Stance/World substrates."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ------------------------------------------------------------------
    # Frozen Anchors & Static Purity Guards
    # ------------------------------------------------------------------

    def test_01_frozen_life_anchors_sha256_intact(self) -> None:
        assembly_path = REPO_ROOT / "components" / "life" / "scripts" / "autonomous_life_assembly.py"
        sharing_path = REPO_ROOT / "components" / "life" / "scripts" / "life_sharing_service.py"
        self.assertEqual(
            hashlib.sha256(assembly_path.read_bytes()).hexdigest(),
            FROZEN_ASSEMBLY_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(sharing_path.read_bytes()).hexdigest(),
            FROZEN_SHARING_SHA256,
        )

    def test_02_ast_zero_llm_and_zero_random_imports_across_all_three_modules(self) -> None:
        module_paths = (
            REPO_ROOT / "components" / "life" / "scripts" / "v03_clock_evaporation.py",
            REPO_ROOT / "components" / "life" / "scripts" / "life_attention_stance_containment.py",
            REPO_ROOT / "components" / "phone" / "stance_containment_view.py",
        )
        for mod_path in module_paths:
            tree = ast.parse(mod_path.read_text(encoding="utf-8"), filename=str(mod_path))
            imported: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imported.add(alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".")[0])
            self.assertTrue(
                imported.isdisjoint(FORBIDDEN_IMPORTS),
                f"{mod_path.name} imported forbidden modules: {imported & FORBIDDEN_IMPORTS}",
            )

    # ------------------------------------------------------------------
    # Part A: v03_clock_evaporation Tests
    # ------------------------------------------------------------------

    def test_03_clock_view_read_is_100_percent_zero_write(self) -> None:
        status_p, env_p = clock_evap.read_v03_clock_view(
            self.root, has_subject_capability=False, now_iso="2026-04-06T09:00:00+09:00"
        )
        status_s, env_s = clock_evap.read_v03_clock_view(
            self.root, has_subject_capability=True, now_iso="2026-04-06T09:05:00+09:00"
        )
        self.assertEqual(status_p, 200)
        self.assertEqual(status_s, 200)
        self.assertTrue(env_p["ok"])
        self.assertEqual(env_p["data"]["schema"], "phone.v03.clock.v1")
        self.assertEqual(env_p["data"]["state_schema"], "life.v03.clock_evaporation.v1")
        self.assertEqual(env_p["data"]["llm_calls"], 0)
        self.assertFalse(clock_evap.v03_clock_state_path(self.root).exists())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_04_three_clocks_advance_projection_and_monotonic_guard(self) -> None:
        st1, env1 = clock_evap.mutate_v03_clock_view(
            self.root,
            {"action": "advance_projection", "sync_v02": False},
            actor_side="system",
            now_iso="2026-04-06T10:00:00+09:00",
        )
        self.assertEqual(st1, 200)
        self.assertEqual(env1["data"]["clocks"]["world_clock"]["last_committed_at"], "2026-04-06T10:00:00+09:00")
        self.assertEqual(env1["data"]["clocks"]["world_clock"]["ambient_date"], "2026-04-06")

        # Moving clock backwards must reject with 409 WORLD_CLOCK_MONOTONIC_VIOLATION
        st_back, env_back = clock_evap.mutate_v03_clock_view(
            self.root,
            {"action": "advance_projection", "sync_v02": False},
            actor_side="system",
            now_iso="2026-04-06T09:59:00+09:00",
        )
        self.assertEqual(st_back, 409)
        self.assertEqual(env_back["reason_code"], "WORLD_CLOCK_MONOTONIC_VIOLATION")

        # Cross-day advance switches ambient_date deterministically
        st2, env2 = clock_evap.mutate_v03_clock_view(
            self.root,
            {"action": "advance_projection", "sync_v02": False},
            actor_side="system",
            now_iso="2026-04-07T08:30:00+09:00",
        )
        self.assertEqual(st2, 200)
        self.assertTrue(env2["data"]["action_receipt"]["ambient_date_switched"])
        self.assertEqual(env2["data"]["clocks"]["world_clock"]["ambient_date"], "2026-04-07")

    def test_04b_utc_z_crossing_beijing_midnight_switches_ambient_date(self) -> None:
        st1, env1 = clock_evap.mutate_v03_clock_view(
            self.root,
            {"action": "advance_projection", "sync_v02": False},
            actor_side="system",
            now_iso="2026-11-17T02:15:00+08:00",
        )
        self.assertEqual(st1, 200)
        self.assertEqual(env1["data"]["clocks"]["world_clock"]["ambient_date"], "2026-11-17")

        # 2026-11-17T16:30:00Z == 2026-11-18T00:30:00+08:00 (crossed +08:00 midnight)
        st2, env2 = clock_evap.mutate_v03_clock_view(
            self.root,
            {"action": "advance_projection", "sync_v02": False},
            actor_side="system",
            now_iso="2026-11-17T16:30:00Z",
        )
        self.assertEqual(st2, 200)
        self.assertTrue(env2["data"]["action_receipt"]["ambient_date_switched"])
        self.assertEqual(env2["data"]["clocks"]["world_clock"]["ambient_date"], "2026-11-18")
        self.assertEqual(env2["data"]["clocks"]["wall_clock"]["local_date"], "2026-11-18")

    def test_05_slow_var_damp_outerwear_dries_at_6_hours(self) -> None:
        st, _ = clock_evap.mutate_v03_clock_view(
            self.root,
            {
                "action": "declare_slow_variable",
                "var_kind": "damp_outerwear",
                "var_id": "coat_rain",
                "label": "淋雨风衣",
            },
            actor_side="subject",
            now_iso="2026-04-06T12:00:00+09:00",
        )
        self.assertEqual(st, 200)

        # At +5h59m: still damp on zero-write read
        _, r_damp = clock_evap.read_v03_clock_view(
            self.root, has_subject_capability=True, now_iso="2026-04-06T17:59:00+09:00"
        )
        coat_damp = r_damp["data"]["slow_variables"]["outerwear_drying"][0]
        self.assertEqual(coat_damp["projected_dryness"], "damp")
        self.assertFalse(coat_damp["settled_dry"])

        # At +6h00m (21600s): dry on read and settled on advance_projection
        _, r_dry = clock_evap.read_v03_clock_view(
            self.root, has_subject_capability=True, now_iso="2026-04-06T18:00:00+09:00"
        )
        coat_dry = r_dry["data"]["slow_variables"]["outerwear_drying"][0]
        self.assertEqual(coat_dry["projected_dryness"], "dry")
        self.assertTrue(coat_dry["settled_dry"])

        st_adv, env_adv = clock_evap.mutate_v03_clock_view(
            self.root,
            {"action": "advance_projection", "sync_v02": False},
            actor_side="system",
            now_iso="2026-04-06T18:00:00+09:00",
        )
        self.assertEqual(st_adv, 200)
        self.assertGreaterEqual(env_adv["data"]["action_receipt"]["settled_slow_vars"], 1)

    def test_06_slow_var_hot_drink_cools_at_30_minutes(self) -> None:
        st, _ = clock_evap.mutate_v03_clock_view(
            self.root,
            {
                "action": "declare_slow_variable",
                "var_kind": "desk_drink",
                "var_id": "mug_tea",
                "label": "桌面热红茶",
            },
            actor_side="subject",
            now_iso="2026-04-06T14:00:00+09:00",
        )
        self.assertEqual(st, 200)

        # +10m -> warm
        _, r10 = clock_evap.read_v03_clock_view(self.root, now_iso="2026-04-06T14:10:00+09:00")
        self.assertEqual(r10["data"]["slow_variables"]["drinks_and_dishes"][0]["projected_temperature"], "warm")
        self.assertFalse(r10["data"]["slow_variables"]["drinks_and_dishes"][0]["is_cooled"])

        # +20m -> tepid
        _, r20 = clock_evap.read_v03_clock_view(self.root, now_iso="2026-04-06T14:20:00+09:00")
        self.assertEqual(r20["data"]["slow_variables"]["drinks_and_dishes"][0]["projected_temperature"], "tepid")

        # +30m -> room_temp (is_cooled == True)
        _, r30 = clock_evap.read_v03_clock_view(self.root, now_iso="2026-04-06T14:30:00+09:00")
        drink30 = r30["data"]["slow_variables"]["drinks_and_dishes"][0]
        self.assertEqual(drink30["projected_temperature"], "room_temp")
        self.assertTrue(drink30["is_cooled"])

    def test_07_slow_var_missing_note_fades_at_72_hours_with_zero_absence_debt(self) -> None:
        st, _ = clock_evap.mutate_v03_clock_view(
            self.root,
            {
                "action": "declare_slow_variable",
                "var_kind": "missing_note",
                "var_id": "miss_01",
                "label": "安静想念便签",
            },
            actor_side="subject",
            now_iso="2026-04-06T10:00:00+09:00",
        )
        self.assertEqual(st, 200)

        # +71h: still active, private summary hidden from partner
        _, r_partner = clock_evap.read_v03_clock_view(
            self.root, has_subject_capability=False, now_iso="2026-04-09T09:00:00+09:00"
        )
        mp_71 = r_partner["data"]["slow_variables"]["missing_partner_notes"]
        self.assertEqual(mp_71["active_count"], 1)
        self.assertTrue(mp_71["no_absence_debt"])
        self.assertNotIn("summary", mp_71["items"][0])

        # +72h: naturally faded, zero absence debt, pruned cleanly on advance_projection
        _, r_72 = clock_evap.read_v03_clock_view(
            self.root, has_subject_capability=True, now_iso="2026-04-09T10:00:00+09:00"
        )
        mp_72 = r_72["data"]["slow_variables"]["missing_partner_notes"]
        self.assertEqual(mp_72["active_count"], 0)
        self.assertEqual(mp_72["naturally_faded_count"], 1)
        self.assertTrue(mp_72["no_absence_debt"])

        st_adv, env_adv = clock_evap.mutate_v03_clock_view(
            self.root,
            {"action": "advance_projection", "sync_v02": False},
            actor_side="system",
            now_iso="2026-04-09T10:00:00+09:00",
        )
        self.assertEqual(st_adv, 200)
        self.assertEqual(env_adv["data"]["action_receipt"]["faded_missing_notes"], 1)
        self.assertEqual(
            env_adv["data"]["slow_variables"]["missing_partner_notes"]["items"], []
        )

    def test_08_evaporable_ttl_silent_evaporation_zero_tombstone_and_zero_audit_backlog(self) -> None:
        st, env = clock_evap.mutate_v03_clock_view(
            self.root,
            {
                "action": "record_evaporable",
                "domain_kind": "fleeting_thought",
                "text": "窗外云影晃了一下",
                "ttl_seconds": 600,
            },
            actor_side="subject",
            now_iso="2026-04-06T11:00:00+09:00",
        )
        self.assertEqual(st, 200)
        self.assertEqual(env["data"]["accountability_partitions"]["evaporable_pool"]["active_count"], 1)

        # After 600s TTL: vanishes silently on GET without disk write
        _, r_exp = clock_evap.read_v03_clock_view(
            self.root, has_subject_capability=True, now_iso="2026-04-06T11:10:00+09:00"
        )
        part = r_exp["data"]["accountability_partitions"]
        self.assertEqual(part["evaporable_pool"]["active_count"], 0)
        self.assertEqual(part["evaporation_guarantees"]["tombstone_count"], 0)
        self.assertEqual(part["evaporation_guarantees"]["audit_backlog_count"], 0)

        # On advance_projection: physically removed from disk with zero tombstones
        st_adv, env_adv = clock_evap.mutate_v03_clock_view(
            self.root,
            {"action": "advance_projection", "sync_v02": False},
            actor_side="system",
            now_iso="2026-04-06T11:15:00+09:00",
        )
        self.assertEqual(st_adv, 200)
        self.assertEqual(env_adv["data"]["action_receipt"]["evaporated_count"], 1)
        disk_raw = json.loads(clock_evap.v03_clock_state_path(self.root).read_text(encoding="utf-8"))
        self.assertEqual(disk_raw["evaporable_pool"], [])
        self.assertNotIn("deleted_evaporable_ids", disk_raw)
        self.assertNotIn("evaporable_tombstones", disk_raw)

    def test_09_accountable_domains_forbidden_in_evaporable_returns_409(self) -> None:
        for bad_domain in ("wallet", "object_placement", "intimacy_consent", "mutual_consent"):
            st, env = clock_evap.mutate_v03_clock_view(
                self.root,
                {
                    "action": "record_evaporable",
                    "domain_kind": bad_domain,
                    "text": "试图把有责状态塞进可蒸发池",
                    "ttl_seconds": 300,
                },
                actor_side="subject",
                now_iso="2026-04-06T12:00:00+09:00",
            )
            self.assertEqual(st, 409, f"Expected 409 for {bad_domain}")
            self.assertEqual(env["reason_code"], "EVAPORABLE_FORBIDDEN_FOR_ACCOUNTABLE_STATE")

        # Accountable payload keys (wallet / containment / consent) also trigger 409
        for bad_key, bad_val in (
            ("amount_jpy", 1200),
            ("parent_container_id", "desk_tray"),
            ("consent_id", "c_01"),
        ):
            st_k, env_k = clock_evap.mutate_v03_clock_view(
                self.root,
                {
                    "action": "record_evaporable",
                    "domain_kind": "fleeting_thought",
                    "text": "夹带问责字段",
                    bad_key: bad_val,
                },
                actor_side="subject",
                now_iso="2026-04-06T12:01:00+09:00",
            )
            self.assertEqual(st_k, 409)
            self.assertEqual(env_k["reason_code"], "EVAPORABLE_FORBIDDEN_FOR_ACCOUNTABLE_STATE")

    def test_10_unknown_discrete_actions_never_guessed_across_long_offline_gap(self) -> None:
        st_reg, _ = clock_evap.mutate_v03_clock_view(
            self.root,
            {
                "action": "register_unknown_action",
                "action_id": "act_kettle_01",
                "domain": "kitchen",
                "summary": "断线前提交的烧水动作",
            },
            actor_side="subject",
            now_iso="2026-04-06T08:00:00+09:00",
        )
        self.assertEqual(st_reg, 200)

        # Advance 5 days offline: action MUST stay UNKNOWN and never guessed
        st_adv, env_adv = clock_evap.mutate_v03_clock_view(
            self.root,
            {"action": "advance_projection", "sync_v02": False},
            actor_side="system",
            now_iso="2026-04-11T08:00:00+09:00",
        )
        self.assertEqual(st_adv, 200)
        self.assertEqual(env_adv["data"]["action_receipt"]["unknown_actions_guessed"], 0)
        pending = env_adv["data"]["iron_laws"]["pending_unknown_actions"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["effect_state"], "UNKNOWN")
        self.assertFalse(pending[0]["guessed_by_clock"])

        # Explicit receipt resolves it cleanly
        st_res, env_res = clock_evap.mutate_v03_clock_view(
            self.root,
            {
                "action": "resolve_unknown_action",
                "action_id": "act_kettle_01",
                "resolved_outcome": "FAILED",
                "receipt_ref": "rcpt_kettle_timeout_01",
            },
            actor_side="system",
            now_iso="2026-04-11T08:01:00+09:00",
        )
        self.assertEqual(st_res, 200)
        self.assertEqual(env_res["data"]["iron_laws"]["pending_unknown_actions"], [])

    def test_11_cognitive_backfill_forbidden_across_offline_gap(self) -> None:
        clock_evap.mutate_v03_clock_view(
            self.root,
            {"action": "advance_projection", "sync_v02": False},
            actor_side="system",
            now_iso="2026-04-06T09:00:00+09:00",
        )
        st_adv, env_adv = clock_evap.mutate_v03_clock_view(
            self.root,
            {"action": "advance_projection", "sync_v02": False},
            actor_side="system",
            now_iso="2026-04-10T21:00:00+09:00",
        )
        self.assertEqual(st_adv, 200)
        cc = env_adv["data"]["clocks"]["cognitive_clock"]
        self.assertTrue(cc["cognitive_backfill_forbidden"])
        self.assertEqual(cc["fabricated_downtime_episodes"], 0)
        self.assertEqual(cc["causal_ticks"], 0)
        self.assertEqual(env_adv["data"]["action_receipt"]["cognitive_backfill_count"], 0)

    def test_12_meaningless_idle_and_halfway_abandon_zero_growth_penalty(self) -> None:
        # Partner cannot impersonate subject idle
        st_403, env_403 = clock_evap.mutate_v03_clock_view(
            self.root,
            {"action": "start_meaningless_idle", "idle_kind": "daydream_window"},
            actor_side="partner",
            now_iso="2026-04-06T15:00:00+09:00",
        )
        self.assertEqual(st_403, 403)
        self.assertEqual(env_403["reason_code"], "SUBJECT_CAPABILITY_REQUIRED")

        st_start, env_start = clock_evap.mutate_v03_clock_view(
            self.root,
            {
                "action": "start_meaningless_idle",
                "idle_kind": "halfway_abandon_tidy",
                "quiet_note": "理了一半书桌，看着窗外发会儿呆",
            },
            actor_side="subject",
            now_iso="2026-04-06T15:00:00+09:00",
        )
        self.assertEqual(st_start, 200)
        self.assertEqual(env_start["data"]["meaningless_idle"]["active"]["growth_score_delta"], 0)

        st_end, env_end = clock_evap.mutate_v03_clock_view(
            self.root,
            {
                "action": "end_meaningless_idle",
                "abandoned_halfway": True,
                "let_evaporate": True,
                "ttl_seconds": 300,
            },
            actor_side="subject",
            now_iso="2026-04-06T15:12:00+09:00",
        )
        self.assertEqual(st_end, 200)
        rcpt = env_end["data"]["action_receipt"]
        self.assertTrue(rcpt["abandoned_halfway"])
        self.assertEqual(rcpt["growth_score_delta"], 0)
        self.assertEqual(rcpt["productivity_penalty"], 0)
        self.assertFalse(rcpt["justification_required"])
        self.assertFalse(rcpt["forced_summary_generated"])

    def test_13_sync_v02_slices_from_committed_world_facts(self) -> None:
        # Commit a real room containment action so room furnishings exist on disk
        phone_sc.apply_stance_containment_action(
            self.root,
            {
                "action": "pick_and_put_in_container",
                "item_id": "fountain_pen",
                "container_id": "pen_cup",
            },
            now="2026-04-06T13:00:00-05:00",
            subject_view=True,
        )
        world_dir = self.root / "world"
        world_dir.mkdir(parents=True, exist_ok=True)
        (world_dir / "world_state.json").write_text(
            json.dumps(
                {
                    "location": {
                        "place_id": "bookstore_corner",
                        "arrived_at": "2026-04-06T13:20:00-05:00",
                    },
                    "visited_places": [
                        {
                            "place_id": "riverside_bench",
                            "visited_at": "2026-04-06T14:10:00-05:00",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        st, env = clock_evap.mutate_v03_clock_view(
            self.root,
            {"action": "sync_v02_slices", "refresh_reunion_baseline": True},
            actor_side="system",
            now_iso="2026-04-06T16:00:00-05:00",
        )
        self.assertEqual(st, 200)
        sync_rep = env["data"]["action_receipt"]["v02_sync"]
        self.assertEqual(sync_rep["llm_calls"], 0)
        self.assertGreaterEqual(sync_rep["bench_visits_logged"], 1)
        self.assertTrue(sync_rep["reunion_baseline_updated"])
        self.assertTrue((self.root / "phone" / "outing_bench_bookstore.json").exists())
        self.assertTrue((self.root / "phone" / "reunion_anchor.json").exists())
        self.assertTrue((self.root / "phone" / "longterm_room_habits.json").exists())

    # ------------------------------------------------------------------
    # Part B: life_attention_stance_containment + stance_containment_view
    # ------------------------------------------------------------------

    def test_14_both_owner_and_phone_reads_are_100_percent_zero_write_and_neutral_ta(self) -> None:
        o_partner = owner_sc.read_attention_stance_containment(
            self.root, now="2026-04-06T10:00:00+09:00", subject_view=False
        )
        o_subject = owner_sc.read_attention_stance_containment(
            self.root, now="2026-04-06T10:00:00+09:00", subject_view=True
        )
        p_partner = phone_sc.read_stance_containment(
            self.root, now="2026-04-06T10:00:00+09:00", subject_view=False
        )
        self.assertEqual(o_partner["schema"], "life.v03.attention_stance_containment.v1")
        self.assertEqual(o_partner["character"]["display_name"], "TA")
        self.assertEqual(o_subject["llm_calls"], 0)
        self.assertEqual(p_partner["character"]["display_name"], "TA")
        self.assertFalse((self.root / "life").exists())
        self.assertFalse((self.root / "phone").exists())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_15_reading_material_supply_revision_probe_and_mismatch_guard(self) -> None:
        supply_dir = self.root / "supply"
        supply_dir.mkdir(parents=True, exist_ok=True)
        (supply_dir / "reading_materials.json").write_text(
            json.dumps(
                {
                    "materials": [
                        {
                            "scope_ref": "book:craft_notes",
                            "source_revision": "ed-2026-r3",
                            "title": "器物与手艺笔记",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        # Auto-probes source_revision="ed-2026-r3" when omitted!
        res = owner_sc.apply_attention_stance_containment_action(
            self.root,
            {
                "action": "propose_stance",
                "scope_ref": "book:craft_notes",
                "page_or_anchor": "p.18",
                "facet": "method_fit",
                "text": "这章讲工具顺手度比花哨参数更重要，很合胃口。",
                "cached_excerpt": "工具顺手先于堆砌规格。",
                "shared_with_partner": True,
            },
            now="2026-04-06T11:00:00+09:00",
            subject_view=True,
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["stance"]["source_revision"], "ed-2026-r3")

        # Passing mismatched source_revision against mounted Supply raises 409
        with self.assertRaises(owner_sc.LifeStanceContainmentConflictError) as ctx:
            owner_sc.apply_attention_stance_containment_action(
                self.root,
                {
                    "action": "propose_stance",
                    "scope_ref": "book:craft_notes",
                    "source_revision": "ed-2024-old",
                    "page_or_anchor": "p.19",
                    "facet": "content",
                    "text": "旧版锚点测试",
                },
                now="2026-04-06T11:05:00+09:00",
                subject_view=True,
            )
        self.assertEqual(ctx.exception.reason_code, "STANCE_SOURCE_REVISION_MISMATCH")

    def test_16_stance_closed_facets_typo_cas_and_superseded_lineage(self) -> None:
        with self.assertRaises(owner_sc.LifeStanceContainmentValidationError) as bad_f:
            owner_sc.apply_attention_stance_containment_action(
                self.root,
                {
                    "action": "propose_stance",
                    "scope_ref": "essay:autumn",
                    "source_revision": "v1",
                    "page_or_anchor": "sec-1",
                    "facet": "invalid_facet",
                    "text": "不合法维度",
                },
                now="2026-04-06T12:00:00+09:00",
                subject_view=True,
            )
        self.assertEqual(bad_f.exception.reason_code, "STANCE_FACET_INVALID")

        created = owner_sc.apply_attention_stance_containment_action(
            self.root,
            {
                "action": "propose_stance",
                "scope_ref": "essay:autumn",
                "source_revision": "v1",
                "page_or_anchor": "sec-1",
                "facet": "appearance",
                "text": "封面留白很耐看（错字版）",
                "shared_with_partner": True,
            },
            now="2026-04-06T12:01:00+09:00",
            subject_view=True,
        )
        sid_v1 = created["stance"]["stance_id"]

        # Typo fix keeps same stance_id, increments text_version, and checks expected_version CAS
        typo = owner_sc.apply_attention_stance_containment_action(
            self.root,
            {
                "action": "fix_stance_typo",
                "stance_id": sid_v1,
                "text": "封面留白很耐看。",
                "expected_version": 1,
            },
            now="2026-04-06T12:02:00+09:00",
            subject_view=True,
        )
        self.assertEqual(typo["stance"]["stance_id"], sid_v1)
        self.assertEqual(typo["stance"]["text_version"], 2)

        with self.assertRaises(owner_sc.LifeStanceContainmentConflictError) as cas_err:
            owner_sc.apply_attention_stance_containment_action(
                self.root,
                {
                    "action": "fix_stance_typo",
                    "stance_id": sid_v1,
                    "text": "过期版本号并发写",
                    "expected_version": 1,
                },
                now="2026-04-06T12:03:00+09:00",
                subject_view=True,
            )
        self.assertEqual(cas_err.exception.reason_code, "STANCE_VERSION_CONFLICT")

        # Substantive revision creates new stance_id and marks old SUPERSEDED without erasing old text
        rev = owner_sc.apply_attention_stance_containment_action(
            self.root,
            {
                "action": "revise_stance",
                "replaces_stance_id": sid_v1,
                "text": "拿到实物后发现纸张纹理比留白更出彩。",
                "shared_with_partner": True,
            },
            now="2026-04-06T12:10:00+09:00",
            subject_view=True,
        )
        sid_v2 = rev["stance"]["stance_id"]
        self.assertNotEqual(sid_v1, sid_v2)
        self.assertEqual(rev["stance"]["replaces_stance_id"], sid_v1)

        proj = owner_sc.read_attention_stance_containment(
            self.root, now="2026-04-06T12:11:00+09:00", subject_view=True
        )
        self.assertEqual([s["stance_id"] for s in proj["stance_revisions"]["current_stances"]], [sid_v2])
        hist_by_id = {s["stance_id"]: s for s in proj["stance_revisions"]["lineage_history"]}
        self.assertEqual(hist_by_id[sid_v1]["status"], "SUPERSEDED")
        self.assertEqual(hist_by_id[sid_v1]["superseded_by"], sid_v2)
        self.assertEqual(hist_by_id[sid_v1]["text"], "封面留白很耐看。")

    def test_17_mark_source_updated_marks_stale_and_preserves_original_words(self) -> None:
        c = owner_sc.apply_attention_stance_containment_action(
            self.root,
            {
                "action": "propose_stance",
                "scope_ref": "doc:guide",
                "source_revision": "rev-1",
                "page_or_anchor": "ch2",
                "facet": "content",
                "text": "第二章的例子写得很清楚。",
                "shared_with_partner": True,
            },
            now="2026-04-06T13:00:00+09:00",
            subject_view=True,
        )
        sid = c["stance"]["stance_id"]

        upd = owner_sc.apply_attention_stance_containment_action(
            self.root,
            {
                "action": "mark_source_updated",
                "scope_ref": "doc:guide",
                "new_source_revision": "rev-2",
                "note": "作者重写了第二章示例",
            },
            now="2026-04-06T14:00:00+09:00",
            subject_view=False,
        )
        self.assertEqual(upd["stale_stance_ids"], [sid])

        proj = owner_sc.read_attention_stance_containment(
            self.root, now="2026-04-06T14:01:00+09:00", subject_view=True
        )
        self.assertEqual(proj["stance_revisions"]["current_stances"], [])
        stale = proj["stance_revisions"]["stale_stances"]
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0]["status"], "SOURCE_REVISION_STALE")
        self.assertEqual(stale[0]["text"], "第二章的例子写得很清楚。")

    def test_18_revoke_stance_source_purges_excerpts_writes_tombstone_and_blocks_resurrection(self) -> None:
        prop_payload = {
            "action": "propose_stance",
            "scope_ref": "zine:secret",
            "source_revision": "v1",
            "page_or_anchor": "p.3",
            "facet": "content",
            "text": "这段摘录很有意思",
            "cached_excerpt": "原文缓存摘录片段",
            "private_note": "私密批注",
            "shared_with_partner": True,
            "operation_key": "op_propose_zine_01",
        }
        owner_sc.apply_attention_stance_containment_action(
            self.root,
            prop_payload,
            now="2026-04-06T15:00:00+09:00",
            subject_view=True,
        )

        revoked = owner_sc.apply_attention_stance_containment_action(
            self.root,
            {
                "action": "revoke_stance_source",
                "scope_ref": "zine:secret",
                "reason": "来源已撤回授权",
                "operation_key": "op_revoke_zine_01",
            },
            now="2026-04-06T15:10:00+09:00",
            subject_view=False,
        )
        self.assertEqual(revoked["withdrawn_count"], 1)

        # Verify disk purge + withdrawn_sources tombstone
        raw_disk = json.loads(
            owner_sc.attention_stance_containment_state_path(self.root).read_text(encoding="utf-8")
        )
        st_disk = raw_disk["stance_domain"]["stances"][0]
        self.assertEqual(st_disk["status"], "SOURCE_WITHDRAWN")
        self.assertIsNone(st_disk["text"])
        self.assertIsNone(st_disk["cached_excerpt"])
        self.assertIsNone(st_disk["private_note"])
        self.assertIn("zine:secret", raw_disk["stance_domain"]["withdrawn_sources"])

        # Idempotent replay of revoke_stance_source succeeds cleanly without resurrection
        replay = owner_sc.apply_attention_stance_containment_action(
            self.root,
            {
                "action": "revoke_stance_source",
                "scope_ref": "zine:secret",
                "reason": "来源已撤回授权",
                "operation_key": "op_revoke_zine_01",
            },
            now="2026-04-06T15:11:00+09:00",
            subject_view=False,
        )
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["status"], "already_applied")

        # Replaying old propose_stance operation_key on withdrawn source fails 409 (never resurrects)
        with self.assertRaises(owner_sc.LifeStanceContainmentConflictError) as old_op_err:
            owner_sc.apply_attention_stance_containment_action(
                self.root,
                prop_payload,
                now="2026-04-06T15:11:30+09:00",
                subject_view=True,
            )
        self.assertEqual(old_op_err.exception.reason_code, "STANCE_SOURCE_WITHDRAWN")

        rebuilt = owner_sc.apply_attention_stance_containment_action(
            self.root,
            {"action": "rebuild_stance_index"},
            now="2026-04-06T15:12:00+09:00",
            subject_view=True,
        )
        self.assertEqual(rebuilt["rebuilt_active_stance_ids"], [])

        # Attempting new stance on withdrawn scope is rejected with STANCE_SOURCE_WITHDRAWN
        with self.assertRaises(owner_sc.LifeStanceContainmentConflictError) as ctx:
            owner_sc.apply_attention_stance_containment_action(
                self.root,
                {
                    "action": "propose_stance",
                    "scope_ref": "zine:secret",
                    "source_revision": "v2",
                    "page_or_anchor": "p.1",
                    "facet": "content",
                    "text": "试图复活已撤回来源",
                },
                now="2026-04-06T15:15:00+09:00",
                subject_view=True,
            )
        self.assertEqual(ctx.exception.reason_code, "STANCE_SOURCE_WITHDRAWN")

    def test_19_container_open_state_locked_and_closed_block_direct_access(self) -> None:
        phone_sc.apply_stance_containment_action(
            self.root,
            {
                "action": "register_container",
                "container_id": "wooden_lockbox",
                "label": "带锁木盒",
                "place_id": "study_desk",
                "capacity": 3,
                "open_state": "open",
            },
            now="2026-04-06T16:00:00+09:00",
            subject_view=True,
        )
        # Put memo_clip inside while open
        phone_sc.apply_stance_containment_action(
            self.root,
            {
                "action": "pick_and_put_in_container",
                "item_id": "memo_clip",
                "container_id": "wooden_lockbox",
            },
            now="2026-04-06T16:01:00+09:00",
            subject_view=True,
        )

        # Close container -> take_from_container fails with CONTAINER_CLOSED
        phone_sc.apply_stance_containment_action(
            self.root,
            {
                "action": "set_container_open_state",
                "container_id": "wooden_lockbox",
                "open_state": "closed",
            },
            now="2026-04-06T16:02:00+09:00",
            subject_view=True,
        )
        with self.assertRaises(phone_sc.StanceContainmentConflictError) as closed_err:
            phone_sc.apply_stance_containment_action(
                self.root,
                {
                    "action": "take_from_container",
                    "item_id": "memo_clip",
                    "container_id": "wooden_lockbox",
                },
                now="2026-04-06T16:03:00+09:00",
                subject_view=True,
            )
        self.assertEqual(closed_err.exception.reason_code, "CONTAINER_CLOSED")

        # Lock container -> take_from_container fails with CONTAINER_LOCKED
        phone_sc.apply_stance_containment_action(
            self.root,
            {
                "action": "set_container_open_state",
                "container_id": "wooden_lockbox",
                "open_state": "locked",
            },
            now="2026-04-06T16:04:00+09:00",
            subject_view=True,
        )
        with self.assertRaises(phone_sc.StanceContainmentConflictError) as lock_err:
            phone_sc.apply_stance_containment_action(
                self.root,
                {
                    "action": "take_from_container",
                    "item_id": "memo_clip",
                    "container_id": "wooden_lockbox",
                },
                now="2026-04-06T16:05:00+09:00",
                subject_view=True,
            )
        self.assertEqual(lock_err.exception.reason_code, "CONTAINER_LOCKED")

    def test_20_nested_container_hierarchy_and_ancestor_lock_propagation(self) -> None:
        # Outer cabinet box on study_desk -> inner velvet_pouch nested inside outer_box
        owner_sc.apply_attention_stance_containment_action(
            self.root,
            {
                "action": "register_container",
                "container_id": "outer_box",
                "label": "外层收纳箱",
                "place_id": "study_desk",
                "capacity": 3,
                "open_state": "open",
            },
            now="2026-04-06T16:10:00+09:00",
            subject_view=True,
        )
        owner_sc.apply_attention_stance_containment_action(
            self.root,
            {
                "action": "register_container",
                "container_id": "inner_pouch",
                "label": "内层绒布袋",
                "parent_container_id": "outer_box",
                "capacity": 2,
                "open_state": "open_top",
            },
            now="2026-04-06T16:11:00+09:00",
            subject_view=True,
        )
        # Put gel_pen into inner_pouch while outer_box is open
        put_res = owner_sc.apply_attention_stance_containment_action(
            self.root,
            {
                "action": "pick_and_put_in_container",
                "item_id": "gel_pen",
                "container_id": "inner_pouch",
            },
            now="2026-04-06T16:12:00+09:00",
            subject_view=True,
        )
        self.assertTrue(put_res["ok"])

        # Lock outer_box -> inner_pouch becomes inaccessible with CONTAINER_LOCKED!
        owner_sc.apply_attention_stance_containment_action(
            self.root,
            {
                "action": "set_container_open_state",
                "container_id": "outer_box",
                "open_state": "locked",
            },
            now="2026-04-06T16:13:00+09:00",
            subject_view=True,
        )
        with self.assertRaises(owner_sc.LifeStanceContainmentConflictError) as anc_err:
            owner_sc.apply_attention_stance_containment_action(
                self.root,
                {
                    "action": "take_from_container",
                    "item_id": "gel_pen",
                    "container_id": "inner_pouch",
                },
                now="2026-04-06T16:14:00+09:00",
                subject_view=True,
            )
        self.assertEqual(anc_err.exception.reason_code, "CONTAINER_LOCKED")

        # Cycle guard: nesting outer_box inside inner_pouch after unlocking is rejected
        owner_sc.apply_attention_stance_containment_action(
            self.root,
            {
                "action": "set_container_open_state",
                "container_id": "outer_box",
                "open_state": "open",
            },
            now="2026-04-06T16:15:00+09:00",
            subject_view=True,
        )
        with self.assertRaises(owner_sc.LifeStanceContainmentConflictError) as cyc_err:
            owner_sc.apply_attention_stance_containment_action(
                self.root,
                {
                    "action": "nest_container",
                    "container_id": "outer_box",
                    "parent_container_id": "inner_pouch",
                },
                now="2026-04-06T16:16:00+09:00",
                subject_view=True,
            )
        self.assertEqual(cyc_err.exception.reason_code, "CONTAINER_NESTING_CYCLE")

    def test_21_pick_and_put_partial_completion_on_locked_container_keeps_item_in_hand_on_disk(self) -> None:
        phone_sc.apply_stance_containment_action(
            self.root,
            {
                "action": "register_container",
                "container_id": "locked_case",
                "label": "上锁首饰盒",
                "place_id": "study_desk",
                "capacity": 2,
                "open_state": "locked",
            },
            now="2026-04-06T17:00:00+09:00",
            subject_view=True,
        )

        partial = phone_sc.apply_stance_containment_action(
            self.root,
            {
                "action": "pick_and_put_in_container",
                "item_id": "fountain_pen",
                "container_id": "locked_case",
                "hand_slot": "left_hand",
            },
            now="2026-04-06T17:01:00+09:00",
            subject_view=True,
        )
        self.assertFalse(partial["ok"])
        self.assertEqual(partial["status"], "partial_completed")
        self.assertTrue(partial["partial_completion"])
        self.assertTrue(partial["partial_completed"])
        self.assertFalse(partial["rolled_back"])
        self.assertEqual(partial["reason_code"], "CONTAINER_LOCKED")
        self.assertEqual(partial["held_in_hand"], "left_hand")

        # Verify persisted on disk in BOTH phone/ and life/ owner ledgers!
        owner_proj = owner_sc.read_attention_stance_containment(
            self.root, now="2026-04-06T17:02:00+09:00", subject_view=True
        )
        self.assertEqual(owner_proj["object_containment"]["hand_slots"]["left_hand"], "fountain_pen")
        pen_row = next(
            it for it in owner_proj["object_containment"]["items"] if it["item_id"] == "fountain_pen"
        )
        self.assertEqual(pen_row["placement_kind"], "hand")
        self.assertEqual(pen_row["held_in_hand"], "left_hand")

    def test_22_partner_move_nested_container_keeps_impression_stale_until_observe_room(self) -> None:
        # Create nested pouch inside desk_tray and put sketch_pencil inside nested pouch
        phone_sc.apply_stance_containment_action(
            self.root,
            {
                "action": "register_container",
                "container_id": "sub_box",
                "label": "笔袋小盒",
                "parent_container_id": "desk_tray",
                "capacity": 2,
                "open_state": "open_top",
            },
            now="2026-04-06T18:00:00+09:00",
            subject_view=True,
        )
        phone_sc.apply_stance_containment_action(
            self.root,
            {
                "action": "pick_and_put_in_container",
                "item_id": "sketch_pencil",
                "container_id": "sub_box",
            },
            now="2026-04-06T18:01:00+09:00",
            subject_view=True,
        )
        # Subject leaves study_room to outside
        phone_sc.apply_stance_containment_action(
            self.root,
            {"action": "set_subject_location", "place_id": "outside", "present": False},
            now="2026-04-06T18:05:00+09:00",
            subject_view=True,
        )

        # Partner moves outer desk_tray to dining_table while subject is absent
        mv = phone_sc.apply_stance_containment_action(
            self.root,
            {
                "action": "partner_move_container",
                "container_id": "desk_tray",
                "to_place_id": "dining_table",
                "subject_witnessed": False,
            },
            now="2026-04-06T18:10:00+09:00",
            subject_view=False,
        )
        self.assertFalse(mv["subject_impression_updated"])
        self.assertIn("sub_box", mv["moved_child_container_ids"])
        self.assertIn("sketch_pencil", mv["moved_child_item_ids"])

        # Physical place is dining_table, but subject_last_seen_place stays study_desk (impression_stale=True)
        proj_stale = phone_sc.read_stance_containment(
            self.root, now="2026-04-06T18:11:00+09:00", subject_view=True
        )
        pen_stale = next(
            it for it in proj_stale["object_containment"]["items"] if it["item_id"] == "sketch_pencil"
        )
        self.assertEqual(pen_stale["place_id"], "dining_table")
        self.assertEqual(pen_stale["subject_last_seen_place"], "study_desk")
        self.assertTrue(pen_stale["impression_stale"])

        # Subject enters kitchen_dining and observes room -> impression refreshes to dining_table!
        phone_sc.apply_stance_containment_action(
            self.root,
            {"action": "observe_current_room", "place_id": "dining_table"},
            now="2026-04-06T18:20:00+09:00",
            subject_view=True,
        )
        proj_fresh = phone_sc.read_stance_containment(
            self.root, now="2026-04-06T18:21:00+09:00", subject_view=True
        )
        pen_fresh = next(
            it for it in proj_fresh["object_containment"]["items"] if it["item_id"] == "sketch_pencil"
        )
        self.assertEqual(pen_fresh["subject_last_seen_place"], "dining_table")
        self.assertFalse(pen_fresh["impression_stale"])

    def test_23_bidirectional_sync_owner_mutation_visible_in_phone_view_zero_write(self) -> None:
        # Mutate directly on owner substrate (without creating phone/stance_containment.json)
        owner_sc.apply_attention_stance_containment_action(
            self.root,
            {
                "action": "propose_stance",
                "scope_ref": "album:rainy_walk",
                "source_revision": "r1",
                "page_or_anchor": "track-2",
                "facet": "content",
                "text": "第二首的雨声采样很自然。",
                "shared_with_partner": True,
            },
            now="2026-04-06T19:00:00+09:00",
            subject_view=True,
        )
        self.assertFalse(phone_sc.stance_containment_state_path(self.root).exists())

        # Pure read via phone_sc sees the owner stance without writing phone/stance_containment.json!
        p_view = phone_sc.read_stance_containment(
            self.root, now="2026-04-06T19:01:00+09:00", subject_view=False
        )
        self.assertFalse(phone_sc.stance_containment_state_path(self.root).exists())
        self.assertEqual(p_view["stance_revisions"]["current_count"], 1)
        self.assertEqual(
            p_view["stance_revisions"]["current_stances"][0]["text"],
            "第二首的雨声采样很自然。",
        )

    def test_24_workbench_reading_material_discovery_and_non_empty_nested_container_remove_guard(self) -> None:
        phone_dir = self.root / "phone"
        phone_dir.mkdir(parents=True, exist_ok=True)
        (phone_dir / "reading_workbench.json").write_text(
            json.dumps(
                {
                    "scope_ref": "paper:topology_01",
                    "source_revision": "draft-7",
                    "title": "空间拓扑手稿",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        discovered = owner_sc.discover_reading_materials(self.root)
        self.assertIn("paper:topology_01", discovered)
        self.assertEqual(discovered["paper:topology_01"]["source_revision"], "draft-7")

        # Register nested container inside desk_tray, then verify desk_tray cannot be removed while non-empty
        owner_sc.apply_attention_stance_containment_action(
            self.root,
            {
                "action": "register_container",
                "container_id": "mini_tin",
                "label": "小铁盒",
                "parent_container_id": "desk_tray",
                "capacity": 1,
            },
            now="2026-04-06T20:00:00+09:00",
            subject_view=True,
        )
        with self.assertRaises(owner_sc.LifeStanceContainmentConflictError) as rm_err:
            owner_sc.apply_attention_stance_containment_action(
                self.root,
                {"action": "remove_container", "container_id": "desk_tray"},
                now="2026-04-06T20:01:00+09:00",
                subject_view=True,
            )
        self.assertEqual(rm_err.exception.reason_code, "CONTAINER_NOT_EMPTY")


if __name__ == "__main__":
    unittest.main()

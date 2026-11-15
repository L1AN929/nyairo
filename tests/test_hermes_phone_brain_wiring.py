#!/usr/bin/env python3
"""End-to-end wiring tests for ``chiyo_bundle/phone_tool.py``, ``lived_context_bridge.py``,
and ``hermes_plugin.py`` across all 0.2 & 0.3 phone life / embodied / 36B / v03 surfaces.

Zero paid LLM calls: all tests run against real local loopback ``PhoneHTTPServer`` temporary
instances, local socket stubs, or isolated temporary directories.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
for entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "components" / "world" / "service" / "bridge"),
    str(ROOT / "components" / "supply"),
    str(Path(__file__).resolve().parent),
    str(ROOT / "scripts"),
):
    if entry not in sys.path:
        sys.path.insert(0, entry)

_HERMES_VENDOR = ROOT / "vendor" / "hermes"
if _HERMES_VENDOR.exists() and str(_HERMES_VENDOR) not in sys.path:
    sys.path.append(str(_HERMES_VENDOR))
elif "agent.context_compressor" not in sys.modules:
    import types as _types
    _agent_pkg = _types.ModuleType("agent")
    _cc_mod = _types.ModuleType("agent.context_compressor")

    class _StubContextCompressor:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        def on_session_reset(self) -> None:
            pass

        def get_status(self) -> dict[str, Any]:
            return {}

    _cc_mod.ContextCompressor = _StubContextCompressor  # type: ignore[attr-defined]
    sys.modules["agent"] = _agent_pkg
    sys.modules["agent.context_compressor"] = _cc_mod
    if "hermes_constants" not in sys.modules:
        _hc_mod = _types.ModuleType("hermes_constants")
        _hc_mod.get_hermes_home = lambda: Path(os.environ.get("HERMES_HOME", str(ROOT)))  # type: ignore[attr-defined]
        sys.modules["hermes_constants"] = _hc_mod

from chiyo_bundle import hermes_plugin, lived_context_bridge, phone_tool  # noqa: E402
from components.phone import adult_intimacy_view as aiv  # noqa: E402
from components.phone import autonomy_rhythm_view as arv  # noqa: E402
from components.phone import capability as phone_capability  # noqa: E402
from components.phone import emotional_tension_view as etv  # noqa: E402
from components.phone import sensory_character_view as sc_char  # noqa: E402
from components.phone import somatic_comfort_view as som_view  # noqa: E402
from components.phone import stance_containment_view as scv  # noqa: E402

FROZEN_ANCHORS = {
    "components/life/scripts/autonomous_life_assembly.py":
        "5f51c9e82a417d9354d08608841d3472fc5a869e6bd3c9d9a7ebed0b15397f7a",
    "components/life/scripts/life_sharing_service.py":
        "0becf7df4d9b34881817ff6f1defdcc15929eacbd6183d4c8a38c5a048d46038",
}

POSIX_ONLY = unittest.skipUnless(
    hasattr(socket, "AF_UNIX") and os.name == "posix",
    "Requires POSIX AF_UNIX/flock (run inside WSL)",
)


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int, str]]:
    """Capture ``{rel_path: (size, mtime_ns, sha256)}`` for zero-write proofs."""
    if not root.exists():
        return {}
    snap: dict[str, tuple[int, int, str]] = {}
    for p in sorted(root.rglob("*")):
        if p.name.endswith((".sqlite-shm", ".sqlite-wal")):
            continue
        rel = str(p.relative_to(root))
        try:
            st = p.lstat()
        except FileNotFoundError:
            continue
        if p.is_file() and not p.is_symlink():
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            snap[rel] = (int(st.st_size), int(st.st_mtime_ns), digest)
        else:
            # SQLite WAL read-only SELECTs create/unlink ephemeral .sqlite-shm mmap files in data/
            dir_mtime = 0 if rel == "data" else int(st.st_mtime_ns)
            snap[rel] = (0, dir_mtime, "dir")
    return snap


class FrozenAnchorAndSchemaTest(unittest.TestCase):
    def test_01_frozen_life_anchors_unchanged(self) -> None:
        for rel_path, expected_sha in FROZEN_ANCHORS.items():
            full = ROOT / rel_path
            actual = hashlib.sha256(full.read_bytes()).hexdigest()
            self.assertEqual(actual, expected_sha, f"Frozen file modified: {rel_path}")

    def test_02_schema_registers_all_02_03_reads_and_life_update(self) -> None:
        expected_02_reads = {
            "desk_presence": "/api/phone/desk/presence",
            "desk_notes": "/api/phone/desk/notes",
            "home_window": "/api/phone/home/window",
            "home_keepsakes": "/api/phone/home/keepsakes",
            "home_letters": "/api/phone/home/letters",
            "home_workbench": "/api/phone/home/workbench",
            "home_curios": "/api/phone/home/curios",
            "home_trio": "/api/phone/home/trio-exchange",
            "home_reunion_anchor": "/api/phone/home/reunion-anchor",
            "home_recipes": "/api/phone/home/recipes",
            "home_tentative_plans": "/api/phone/home/tentative-plans",
            "wardrobe": "/api/phone/home/wardrobe",
            "pocket_lists": "/api/phone/pocket/lists",
            "outing_bench_bookstore": "/api/phone/outing/bench-bookstore",
            "outing_routes_market": "/api/phone/outing/routes-market",
            "outing_gallery_blueprint": "/api/phone/outing/gallery-blueprint",
            "longterm_habits": "/api/phone/longterm/first-room-habits",
            "longterm_gifts_lantern": "/api/phone/longterm/gifts-lantern",
            "longterm_resume_hub": "/api/phone/longterm/resume-hub",
        }
        expected_03_reads = {
            "body_somatic": "/api/phone/body/somatic",
            "body_garment_window": "/api/phone/body/garment-window",
            "body_care": "/api/phone/body/care-offering",
            "body_sensory": "/api/phone/body/sensory",
            "character_pack": "/api/phone/character/pack",
            "autonomy_rhythm": "/api/phone/autonomy/rhythm",
            "emotional_tension": "/api/phone/emotion/tension",
            "stance_containment": "/api/phone/stance/containment",
            "adult_intimacy": "/api/phone/intimacy/adult",
            "v03_clock": "/api/phone/v03/clock",
            "v03_ecology": "/api/phone/v03/ecology",
            "v03_embodied_social": "/api/phone/v03/embodied-social",
        }
        for act, route in {**expected_02_reads, **expected_03_reads}.items():
            self.assertEqual(phone_tool.READ_ACTIONS.get(act), route)
            self.assertIn(act, phone_tool.ACTIONS)

        expected_private_reads = {
            "desk_notes", "home_keepsakes", "home_workbench", "pocket_lists",
            "outing_routes_market", "longterm_gifts_lantern", "longterm_resume_hub",
            "emotional_tension", "stance_containment", "adult_intimacy",
            "v03_ecology", "v03_embodied_social",
        }
        self.assertTrue(expected_private_reads.issubset(phone_tool.PRIVATE_ARTIFACT_ACTIONS))
        self.assertIn("phone_life_update", phone_tool.WRITE_ACTIONS)
        self.assertIn("phone_life_update", phone_tool.ACTIONS)

        props = phone_tool.PHONE_TOOL_SCHEMA["parameters"]["properties"]
        self.assertIn("layer", props)
        self.assertEqual(props["layer"]["enum"], ["then_only", "with_later_context"])
        self.assertIn("target_surface", props)
        self.assertEqual(set(props["target_surface"]["enum"]), set(phone_tool.LIFE_UPDATE_SURFACES))
        self.assertIn("payload", props)


@POSIX_ONLY
class PhoneToolBrainWiringIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import autonomy_rhythm_fixture as fixture

        cls.fixture = fixture
        cls._saved_env = dict(os.environ)
        os.environ["CHIYO_WORLD_LOCAL_OWNER_PHONE"] = fixture.SUBJECT
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="brain-wiring-phone-"))
        cls.server_root = cls.temp_dir / "instance"
        cls.stack = fixture.build_stack(cls.server_root, port=0)
        fixture.serve_stack(cls.stack)
        cls.base_url = cls.stack.base
        cls.cap_path, cls.cap_obj = phone_capability.issue(
            cls.stack.root,
            subject=fixture.SUBJECT,
            endpoint=cls.base_url,
        )
        cls.stack.server.set_capability(cls.cap_obj)
        cls.profile_home = cls.temp_dir / "hermes_profile"
        (cls.profile_home / "chiyo").mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixture.close_stack(cls.stack)
        shutil.rmtree(cls.temp_dir, ignore_errors=True)
        os.environ.clear()
        os.environ.update(cls._saved_env)

    def setUp(self) -> None:
        self.stack.service._world_clock = lambda: datetime(
            2026, 11, 12, 15, 30, tzinfo=timezone(timedelta(hours=8))
        )
        self._write_profile()

    def _write_profile(
        self,
        *,
        write_enabled: bool = True,
        private_artifacts: bool = True,
        memory: bool = False,
        memory_tool_policy: str = "restricted",
        capability_mode: str = "file",
        custom_cap_token: str = "",
        custom_cap_file: str = "",
    ) -> None:
        phone_sec: dict[str, Any] = {
            "enabled": True,
            "base_url": self.base_url,
            "profile_home": str(self.stack.root),
            "write_enabled": write_enabled,
            "private_artifacts": private_artifacts,
        }
        if capability_mode == "file" and custom_cap_file:
            phone_sec["capability_file"] = custom_cap_file
        elif capability_mode == "direct":
            phone_sec["capability"] = custom_cap_token or self.cap_obj.token
        elif capability_mode == "none":
            phone_sec["profile_home"] = ""

        cfg: dict[str, Any] = {
            "cli_owner": True,
            "local_owner_uid": os.getuid(),
            "memory": memory,
            "memory_tool_policy": memory_tool_policy,
            "phone": phone_sec,
        }
        (self.profile_home / "chiyo" / "config.json").write_text(
            json.dumps(cfg, ensure_ascii=False), encoding="utf-8"
        )

    def _call(self, action: str, **kwargs: Any) -> dict[str, Any]:
        args = {"action": action, **kwargs}
        raw = phone_tool.phone_handler(args, hermes_home=str(self.profile_home))
        return json.loads(raw)

    def test_03_e2e_02_reads_and_subject_writes_against_real_phone_server(self) -> None:
        all_02 = (
            "desk_presence", "desk_notes", "home_window", "home_keepsakes",
            "home_letters", "home_workbench", "home_curios", "home_trio",
            "home_reunion_anchor", "home_recipes", "home_tentative_plans",
            "wardrobe", "pocket_lists", "outing_bench_bookstore",
            "outing_routes_market", "outing_gallery_blueprint",
            "longterm_habits", "longterm_gifts_lantern", "longterm_resume_hub",
        )
        for act in all_02:
            res = self._call(act)
            self.assertTrue(res["ok"], f"{act} failed: {res}")
            self.assertEqual(res["http_status"], 200)

        lantern_ctx = self._call("longterm_gifts_lantern", layer="with_later_context")
        self.assertTrue(lantern_ctx["ok"], lantern_ctx)
        bad_layer = self._call("longterm_gifts_lantern", layer="invalid_layer")
        self.assertFalse(bad_layer["ok"])
        self.assertEqual(bad_layer["reason_code"], "INVALID_ARGUMENTS")

        # 1) Wardrobe styling change
        w_res = self._call(
            "phone_life_update",
            target_surface="wardrobe",
            payload={
                "action": "set",
                "homewear_style": "燕麦色宽松针织衫",
                "pairing_note": "窗边微凉顺手披着",
            },
        )
        self.assertTrue(w_res["ok"], w_res)
        self.assertEqual(w_res["status"], "applied")

        # 2) Pocket list add (idempotent operation_key injected automatically)
        p_res = self._call(
            "phone_life_update",
            target_surface="pocket_lists",
            operation_key="wiring-pocket-1",
            payload={
                "action": "add",
                "list": "playlist",
                "title": "雨声里的木吉他",
                "reason": "写草稿时当背景很安静",
                "status": "active",
                "shared": True,
            },
        )
        self.assertTrue(p_res["ok"], p_res)

        # 3) Outing bench visit or bookstore bookmark
        bench_doc = self._call("outing_bench_bookstore")
        bench_data = ((bench_doc.get("result") or {}).get("data") or {}).get("bench") or {}
        candidates = bench_data.get("candidate_places") or bench_data.get("outdoor_places") or []
        if candidates and candidates[0].get("place_id"):
            b_res = self._call(
                "phone_life_update",
                target_surface="outing_bench_bookstore",
                operation_key="wiring-bench-1",
                payload={
                    "action": "log_bench_visit",
                    "place_id": candidates[0]["place_id"],
                    "note": "傍晚散步路过坐了一会儿",
                    "shared": True,
                },
            )
            self.assertTrue(b_res["ok"], b_res)
        bk_res = self._call(
            "phone_life_update",
            target_surface="outing_bench_bookstore",
            payload={
                "action": "bookmark_window_book",
                "slot_index": 0,
                "note": "路过书店橱窗记下这本潮汐小历",
                "shared": True,
            },
        )
        self.assertTrue(bk_res["ok"], bk_res)

        # 4) Longterm shared habit & draft gift (author_side auto-pinned to "her")
        h_res = self._call(
            "phone_life_update",
            target_surface="longterm_habits",
            payload={
                "action": "upsert_habit",
                "title": "煮茶留半壶在保温垫上",
                "habit_text": "下午谁先泡茶都多留一杯在桌角",
            },
        )
        self.assertTrue(h_res["ok"], h_res)

        g_res = self._call(
            "phone_life_update",
            target_surface="longterm_gifts_lantern",
            payload={
                "action": "upsert_gift",
                "stage": "draft",
                "title": "手折星灯小卡片",
                "draft_seed_note": "打算周末写完再送",
            },
        )
        self.assertTrue(g_res["ok"], g_res)
        gifts_read = self._call("longterm_gifts_lantern")
        self.assertTrue(gifts_read["ok"], gifts_read)

    def test_04_e2e_03_somatic_sensory_character_autonomy_reads_and_writes(self) -> None:
        for act in (
            "body_somatic", "body_garment_window", "body_care",
            "body_sensory", "character_pack", "autonomy_rhythm",
        ):
            res = self._call(act)
            self.assertTrue(res["ok"], f"{act} failed: {res}")

        # Open window ajar + wear then hang damp coat
        win_res = self._call(
            "phone_life_update",
            target_surface="body_garment_window",
            payload={"action": "set_window", "state": "ajar"},
        )
        self.assertTrue(win_res["ok"], win_res)
        wear_res = self._call(
            "phone_life_update",
            target_surface="body_garment_window",
            payload={
                "action": "wear_outerwear",
                "garment_id": "coat:linen_jacket",
                "label": "亚麻薄外套",
                "dryness": "damp",
            },
        )
        self.assertTrue(wear_res["ok"], wear_res)
        coat_res = self._call(
            "phone_life_update",
            target_surface="body_garment_window",
            payload={
                "action": "hang_outerwear",
                "garment_id": "coat:linen_jacket",
                "dryness": "damp",
                "hang_position": "门后衣钩",
            },
        )
        self.assertTrue(coat_res["ok"], coat_res)

        # Care offering (actor_side auto-pinned to "her")
        care_res = self._call(
            "phone_life_update",
            target_surface="body_care",
            payload={"action": "offer", "slot": "drink", "kind": "hot_tea", "position": "hand_side"},
        )
        self.assertTrue(care_res["ok"], care_res)

        # Capture text sound card
        snd_res = self._call(
            "phone_life_update",
            target_surface="body_sensory",
            operation_key="wiring-sound-1",
            payload={
                "action": "capture_sound_card",
                "title": "窗缝细雨声",
                "impression_note": "混着远处电车过桥的低响",
                "shared": True,
            },
        )
        self.assertTrue(snd_res["ok"], snd_res)

        # Character identity update (neutral pronoun "他")
        char_res = self._call(
            "phone_life_update",
            target_surface="character_pack",
            payload={
                "action": "update_identity",
                "display_name": "阿澄",
                "pronouns": "他",
            },
        )
        self.assertTrue(char_res["ok"], char_res)

        # Autonomy refusal lock & held thread
        ref_res = self._call(
            "phone_life_update",
            target_surface="autonomy_rhythm",
            payload={
                "action": "declare_refusal",
                "topic_label": "代写整份商业周报",
                "category": "errand_outsourcing",
                "reason_summary": "手头正在改自己的稿子，不想接跑腿活",
            },
        )
        self.assertTrue(ref_res["ok"], ref_res)

        thd_res = self._call(
            "phone_life_update",
            target_surface="autonomy_rhythm",
            payload={
                "action": "hold_subject_topic",
                "topic_label": "刚才聊到一半的旧唱片封面",
                "reason": "MID_TELLING",
            },
        )
        self.assertTrue(thd_res["ok"], thd_res)

    def test_05_e2e_03_emotional_tension_reads_and_writes(self) -> None:
        etv.emotional_tension_state_path(self.stack.root).unlink(missing_ok=True)
        r0 = self._call("emotional_tension")
        self.assertTrue(r0["ok"], r0)

        # Missing note
        m_res = self._call(
            "phone_life_update",
            target_surface="emotional_tension",
            operation_key="wiring-miss-1",
            payload={
                "action": "add_missing_note",
                "source_ref": "kitchen:new_mug_01",
                "source_domain": "household",
                "kind": "FOUND",
                "summary": "翻出那只带小裂纹的蓝釉杯子了",
            },
        )
        self.assertTrue(m_res["ok"], m_res)

        # Ask small favor & withdraw
        fav_res = self._call(
            "phone_life_update",
            target_surface="emotional_tension",
            operation_key="wiring-favor-1",
            payload={
                "action": "ask_partner_favor",
                "favor_kind": "look_once",
                "target_object_ref": "draft:cover_sketch_v2",
                "summary": "顺手帮我看一眼草图左边留白会不会太空",
            },
        )
        self.assertTrue(fav_res["ok"], fav_res)
        favor_id = fav_res["result"]["data"]["favor_id"]

        w_fav = self._call(
            "phone_life_update",
            target_surface="emotional_tension",
            operation_key="wiring-favor-withdraw-1",
            payload={"action": "withdraw_favor", "favor_id": favor_id},
        )
        self.assertTrue(w_fav["ok"], w_fav)

        # Declare spoken need_space
        sp_res = self._call(
            "phone_life_update",
            target_surface="emotional_tension",
            operation_key="wiring-space-1",
            payload={
                "action": "declare_need_space",
                "incident_ref": "inc:talk_interrupt_01",
                "space_style": "SHORT",
                "spoken_aloud": True,
            },
        )
        self.assertTrue(sp_res["ok"], sp_res)

    def test_06_e2e_03_stance_containment_reads_and_writes(self) -> None:
        scv.stance_containment_state_path(self.stack.root).unlink(missing_ok=True)
        r0 = self._call("stance_containment")
        self.assertTrue(r0["ok"], r0)
        r0_data = r0["result"]["data"]
        items = r0_data["object_containment"]["items"]
        containers = r0_data["object_containment"]["containers"]

        # Propose & revise stance
        s1 = self._call(
            "phone_life_update",
            target_surface="stance_containment",
            operation_key="wiring-stance-1",
            payload={
                "action": "propose_stance",
                "scope_ref": "book:essay_collection_01",
                "source_revision": "rev-1",
                "page_or_anchor": "p.12",
                "facet": "content",
                "text": "开头两页节奏偏慢，但比喻很准",
                "shared_with_partner": True,
            },
        )
        self.assertTrue(s1["ok"], s1)
        stance_id = s1["result"]["data"]["stance"]["stance_id"]

        s2 = self._call(
            "phone_life_update",
            target_surface="stance_containment",
            operation_key="wiring-stance-2",
            payload={
                "action": "revise_stance",
                "replaces_stance_id": stance_id,
                "text": "重读后发现慢节奏其实是在铺垫后面的转折",
                "shared_with_partner": True,
            },
        )
        self.assertTrue(s2["ok"], s2)

        # Put item in container if starter item & container share reachable study room
        if items and containers:
            it_id = items[0]["item_id"]
            cont_id = containers[0]["container_id"]
            put_res = self._call(
                "phone_life_update",
                target_surface="stance_containment",
                operation_key="wiring-put-1",
                payload={
                    "action": "pick_and_put_in_container",
                    "item_id": it_id,
                    "container_id": cont_id,
                    "hand_slot": "right_hand",
                },
            )
            self.assertTrue(put_res["ok"], put_res)

        # Prepare table tea/snack dish
        dish_res = self._call(
            "phone_life_update",
            target_surface="stance_containment",
            operation_key="wiring-dish-1",
            payload={
                "action": "prepare_table_dish",
                "title": "刚沏的焙茶",
                "kind": "tea_pot",
                "portions": 2,
                "place_id": "study_desk",
                "initial_temperature": "hot",
            },
        )
        self.assertTrue(dish_res["ok"], dish_res)

        # Propose attached desk co-presence (no operation_key allowed by contract -> not injected!)
        cp_res = self._call(
            "phone_life_update",
            target_surface="stance_containment",
            payload={
                "action": "propose_copresence",
                "target_mode": "quiet_parallel",
                "declared_minutes": 45,
                "note": "一起在桌前安静待会儿",
            },
        )
        self.assertTrue(cp_res["ok"], cp_res)

    def test_07_e2e_03_adult_intimacy_subject_preference_and_unconditional_stop_scene(self) -> None:
        aiv.adult_intimacy_state_path(self.stack.root).unlink(missing_ok=True)
        (self.stack.root / "phone" / "emotional_tension.json").unlink(missing_ok=True)
        now_dt = datetime(2026, 11, 12, 15, 30, tzinfo=timezone(timedelta(hours=8)))

        # Deployer & partner side setup directly via owner view (never via model tool!)
        aiv.apply_adult_intimacy_action(
            self.stack.root,
            {
                "action": "configure_adult_gate",
                "adult_confirmed": True,
                "life_stage": "independent_adult",
                "attested_by": "deployer",
                "feature_enabled": True,
                "deploy_tier_cap": "EXPLICIT",
            },
            now=now_dt,
            subject_view=False,
        )
        aiv.apply_adult_intimacy_action(
            self.stack.root,
            {
                "action": "register_render_route",
                "route_id": "local_private_v1",
                "max_tier": "EXPLICIT",
                "adult_content_allowed": True,
                "policy_basis": "self_hosted_local",
                "set_default": True,
            },
            now=now_dt,
            subject_view=False,
        )
        aiv.apply_adult_intimacy_action(
            self.stack.root,
            {
                "action": "confirm_preference_seed",
                "attested_by": "deployer",
                "desire_baseline": "moderate",
                "stances": {
                    "KISSING": "YES",
                    "CARESSING_CLOTHED:user_to_subject": "YES",
                },
            },
            now=now_dt,
            subject_view=False,
        )
        aiv.apply_adult_intimacy_action(
            self.stack.root,
            {
                "action": "update_partner_limits",
                "stances": {
                    "KISSING": "YES",
                    "CARESSING_CLOTHED:user_to_subject": "YES",
                },
                "preferred_tier": "SUGGESTIVE",
                "allow_subject_initiation": True,
            },
            now=now_dt,
            subject_view=False,
        )

        # Subject updates indoor privacy & revises her own 36B preference via phone_tool
        priv_res = self._call(
            "phone_life_update",
            target_surface="adult_intimacy",
            payload={
                "action": "update_private_space",
                "place": "bedroom.bed",
                "door_closed": True,
                "curtains_closed": True,
                "third_party_present": False,
            },
        )
        self.assertTrue(priv_res["ok"], priv_res)

        rev_res = self._call(
            "phone_life_update",
            target_surface="adult_intimacy",
            payload={
                "action": "revise_subject_preference",
                "stances": {
                    "KISSING": "YES",
                    "CARESSING_CLOTHED:user_to_subject": "YES",
                },
                "evidence_ref": "ix:reflection_20261112",
            },
        )
        self.assertTrue(rev_res["ok"], rev_res)

        # Partner proposes scene -> Subject responds ACCEPT via phone_tool -> Subject triggers stop_scene!
        prop = aiv.apply_adult_intimacy_action(
            self.stack.root,
            {
                "action": "propose_scene",
                "proposer": "partner",
                "categories": ["KISSING", "CARESSING_CLOTHED:user_to_subject"],
                "desired_tier": "SUGGESTIVE",
                "operation_key": "partner-prop-1",
            },
            now=now_dt,
            subject_view=False,
        )
        permit_id = prop["action_receipt"]["permit_id"]
        expected_rev = prop["scene_permit"]["active_permit"]["permit_revision"]

        ans = self._call(
            "phone_life_update",
            target_surface="adult_intimacy",
            operation_key="subj-accept-1",
            payload={
                "action": "respond_scene",
                "permit_id": permit_id,
                "decision": "ACCEPT",
                "expected_revision": expected_rev,
            },
        )
        self.assertTrue(ans["ok"], ans)
        self.assertEqual(ans["result"]["data"]["action_receipt"]["state"], "ACTIVE")

        read_active = self._call("adult_intimacy")
        self.assertTrue(read_active["ok"], read_active)
        self.assertNotIn("solo", read_active["result"])
        self.assertNotIn("solo_slot", json.dumps(read_active["result"], ensure_ascii=False))

        stopped = self._call(
            "phone_life_update",
            target_surface="adult_intimacy",
            payload={
                "action": "stop_scene",
                "permit_id": permit_id,
                "reason": "今天有点累了，先停一下抱会儿就好",
            },
        )
        self.assertTrue(stopped["ok"], stopped)
        self.assertEqual(stopped["result"]["data"]["action_receipt"]["state"], "STOPPED")

    def test_08_e2e_v03_clock_ecology_embodied_social_reads_and_writes(self) -> None:
        # Spin up a dedicated loopback HTTP server mounting /state + /api/phone/v03/* to prove
        # phone_tool's exact HTTP contract, capability headers, CSRF & payload forwarding for v03_*.
        recorded: list[dict[str, Any]] = []
        v03_root = self.temp_dir / "v03_stub_instance"
        v03_root.mkdir(parents=True, exist_ok=True)
        fp = "phone:" + hashlib.sha256(str(v03_root).encode("utf-8")).hexdigest()[:16]

        class V03StubHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                recorded.append({
                    "method": "GET",
                    "path": self.path,
                    "cap": self.headers.get("X-Phone-Capability"),
                    "actor": self.headers.get("X-Phone-Actor"),
                })
                if self.path == "/api/phone/state":
                    doc = {
                        "ok": True,
                        "status": "ok",
                        "csrf_token": "v03-csrf-123",
                        "instance": {"subject": "chiyo", "fingerprint": fp},
                    }
                else:
                    doc = {"ok": True, "status": "ok", "surface": self.path, "data": {"ready": True}}
                raw = json.dumps(doc).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or "0")
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                recorded.append({
                    "method": "POST",
                    "path": self.path,
                    "cap": self.headers.get("X-Phone-Capability"),
                    "csrf": self.headers.get("X-CSRF-Token"),
                    "actor": self.headers.get("X-Phone-Actor"),
                    "body": body,
                })
                doc = {"ok": True, "status": "applied", "data": {"received": body}}
                raw = json.dumps(doc).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
                pass

        srv = ThreadingHTTPServer(("127.0.0.1", 0), V03StubHandler)
        t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        t.start()
        self.addCleanup(srv.server_close)
        try:
            v03_url = f"http://127.0.0.1:{srv.server_address[1]}"
            v03_cap_path, v03_cap = phone_capability.issue(
                v03_root, subject="chiyo", endpoint=v03_url
            )
            v03_profile = self.temp_dir / "v03_profile"
            (v03_profile / "chiyo").mkdir(parents=True, exist_ok=True)
            (v03_profile / "chiyo" / "config.json").write_text(
                json.dumps({
                    "cli_owner": True,
                    "local_owner_uid": os.getuid(),
                    "phone": {
                        "enabled": True,
                        "base_url": v03_url,
                        "profile_home": str(v03_root),
                        "capability_file": str(v03_cap_path),
                        "write_enabled": True,
                        "private_artifacts": True,
                    },
                }),
                encoding="utf-8",
            )

            for r_act in ("v03_clock", "v03_ecology", "v03_embodied_social"):
                out = json.loads(
                    phone_tool.phone_handler({"action": r_act}, hermes_home=str(v03_profile))
                )
                self.assertTrue(out["ok"], out)

            for surface, sub_payload in (
                ("v03_ecology", {"action": "settle_independent_fee", "title": "插画尾款", "amount_jpy": 4500}),
                ("v03_ecology", {"action": "enter_illness_rest", "phase": "CONVALESCENT"}),
                ("v03_embodied_social", {"action": "cook_meal", "dish": "萝卜味增汤", "portions": 2}),
                ("v03_embodied_social", {"action": "record_neighborhood_encounter", "summary": "楼下面包店老板送了刚出炉的边角脆皮"}),
            ):
                w_out = json.loads(
                    phone_tool.phone_handler(
                        {
                            "action": "phone_life_update",
                            "target_surface": surface,
                            "payload": sub_payload,
                        },
                        hermes_home=str(v03_profile),
                    )
                )
                self.assertTrue(w_out["ok"], w_out)
                self.assertEqual(w_out["status"], "applied")

            post_hits = [r for r in recorded if r["method"] == "POST"]
            self.assertEqual(len(post_hits), 4)
            for hit in post_hits:
                self.assertEqual(hit["cap"], v03_cap.token)
                self.assertEqual(hit["csrf"], "v03-csrf-123")
                self.assertEqual(hit["actor"], "model")
        finally:
            srv.shutdown()
            t.join(timeout=5)

        # Also prove REAL PhoneHTTPServer end-to-end GET (zero-write) & POST on /api/phone/v03/*
        self._write_profile()
        before_real = _snapshot_tree(self.stack.root)
        for real_read in ("v03_clock", "v03_ecology", "v03_embodied_social"):
            r_real = self._call(real_read)
            self.assertTrue(r_real["ok"], f"real {real_read} failed: {r_real}")
            self.assertEqual(r_real["http_status"], 200)
        self.assertEqual(_snapshot_tree(self.stack.root), before_real)

        # Real POST on /api/phone/v03/clock (start_meaningless_idle)
        clk_w = self._call(
            "phone_life_update",
            target_surface="v03_clock",
            operation_key="real-v03-clock-1",
            payload={
                "action": "start_meaningless_idle",
                "idle_kind": "daydream_window",
                "quiet_note": "靠着窗边看云影发会儿呆",
            },
        )
        self.assertTrue(clk_w["ok"], clk_w)

        # Real POST on /api/phone/v03/ecology (complete_independent_work)
        eco_w = self._call(
            "phone_life_update",
            target_surface="v03_ecology",
            operation_key="real-v03-eco-1",
            payload={
                "action": "complete_independent_work",
                "job_kind": "illustration_commission",
                "work_ref": "work:cover_sketch_11",
                "amount_cents": 4500,
                "title": "杂志小插图稿费",
                "hours_spent": 2.5,
            },
        )
        self.assertTrue(eco_w["ok"], eco_w)

        # Real POST on /api/phone/v03/embodied-social (restock_pantry + cook_or_brew)
        pan_w = self._call(
            "phone_life_update",
            target_surface="v03_embodied_social",
            operation_key="real-v03-pantry-1",
            payload={
                "action": "restock_pantry",
                "ingredient_id": "tea_hojicha",
                "label": "焙茶茶叶",
                "category": "tea_and_drink",
                "unit_name": "份",
                "units_added": 3,
            },
        )
        self.assertTrue(pan_w["ok"], pan_w)
        cook_w = self._call(
            "phone_life_update",
            target_surface="v03_embodied_social",
            operation_key="real-v03-cook-1",
            payload={
                "action": "cook_or_brew",
                "title": "热焙茶",
                "serving_kind": "drink",
                "ingredients_used": [{"ingredient_id": "tea_hojicha", "units_used": 1}],
                "portions": 2,
            },
        )
        self.assertTrue(cook_w["ok"], cook_w)

    def test_09_hard_gate_write_disabled_blocks_phone_life_update(self) -> None:
        self._write_profile(write_enabled=False)
        res = self._call(
            "phone_life_update",
            target_surface="wardrobe",
            payload={"action": "set", "homewear_style": "灰卫衣"},
        )
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason_code"], "PHONE_WRITE_DISABLED")
        # Reads still work when write_enabled=False
        read_res = self._call("wardrobe")
        self.assertTrue(read_res["ok"], read_res)

    def test_10_hard_gate_restricted_memory_blocks_private_reads_and_private_life_updates(self) -> None:
        self._write_profile(write_enabled=True, private_artifacts=False, memory=True)
        for priv_read in (
            "desk_notes", "pocket_lists", "longterm_gifts_lantern",
            "emotional_tension", "stance_containment", "adult_intimacy",
            "v03_ecology", "v03_embodied_social",
        ):
            r = self._call(priv_read)
            self.assertFalse(r["ok"], priv_read)
            self.assertEqual(r["reason_code"], "PHONE_PRIVATE_ARTIFACTS_NOT_PERMITTED", priv_read)

        for priv_surf in ("pocket_lists", "emotional_tension", "stance_containment", "adult_intimacy", "v03_ecology"):
            w = self._call(
                "phone_life_update",
                target_surface=priv_surf,
                payload={"action": "dummy_check"},
            )
            self.assertFalse(w["ok"], priv_surf)
            self.assertEqual(w["reason_code"], "PHONE_PRIVATE_ARTIFACTS_NOT_PERMITTED", priv_surf)

        # Non-private embodied / wardrobe surfaces stay accessible
        self.assertTrue(self._call("body_somatic")["ok"])
        self.assertTrue(
            self._call(
                "phone_life_update",
                target_surface="body_garment_window",
                payload={"action": "set_window", "state": "closed"},
            )["ok"]
        )

    def test_11_anti_impersonation_blocks_partner_side_and_deployer_subactions(self) -> None:
        impersonation_cases = [
            ("longterm_gifts_lantern", {"action": "upsert_gift", "author_side": "partner", "stage": "draft", "title": "x", "draft_seed_note": "y"}),
            ("home_recipes", {"action": "upsert", "author_side": "partner", "title": "x", "memo": "y"}),
            ("adult_intimacy", {"action": "propose_scene", "proposer": "partner", "categories": ["KISSING"]}),
            ("adult_intimacy", {"action": "respond_scene", "responder": "partner", "permit_id": "p1", "decision": "ACCEPT"}),
            ("adult_intimacy", {"action": "request_widen", "by": "partner", "permit_id": "p1"}),
            ("adult_intimacy", {"action": "configure_adult_gate", "deploy_enabled": True}),
            ("adult_intimacy", {"action": "update_partner_limits", "preferred_tier": "EXPLICIT"}),
            ("adult_intimacy", {"action": "confirm_preference_seed", "desire_baseline": "HIGH"}),
            ("adult_intimacy", {"action": "evaluate_solo_window", "slot_id": "slot-1"}),
            ("pocket_lists", {"action": "set_partner_suggestions", "suggestions": [{"title": "a", "note": "b"}]}),
            ("home_trio", {"action": "set_partner_picks", "partner_picks": []}),
            ("stance_containment", {"action": "partner_bring_snack", "title": "蛋糕"}),
            ("stance_containment", {"action": "take_portion", "dish_id": "d1", "consumer": "partner"}),
            ("emotional_tension", {"action": "respond_favor", "favor_id": "f1", "decision": "ACCEPT"}),
            ("emotional_tension", {"action": "attempt_apology_or_gift_repair", "rupture_id": "r1", "attempt_kind": "GIFT"}),
            ("character_pack", {"action": "select_preset", "preset_id": "warm_senior"}),
        ]
        for surface, bad_payload in impersonation_cases:
            res = self._call("phone_life_update", target_surface=surface, payload=bad_payload)
            self.assertFalse(res["ok"], f"Expected refusal for {surface}: {bad_payload}")
            self.assertEqual(
                res["reason_code"],
                "PARTNER_IMPERSONATION_REFUSED",
                f"Wrong code for {surface}: {res}",
            )

    def test_12_anti_spoofing_blocks_client_identity_injection_including_nested(self) -> None:
        spoof_cases = [
            ("wardrobe", {"action": "set", "uid": 0, "homewear_style": "x"}),
            ("autonomy_rhythm", {"action": "declare_refusal", "authority": "root", "topic_label": "x"}),
            ("emotional_tension", {"action": "add_missing_note", "main_self_verified": True}),
            ("adult_intimacy", {"action": "revise_subject_preference", "stances": {"KISSING": "OPEN"}, "nested": {"bypass_gate": True}}),
            ("stance_containment", {"action": "propose_stance", "owner": "subject"}),
        ]
        for surface, spoof_payload in spoof_cases:
            res = self._call("phone_life_update", target_surface=surface, payload=spoof_payload)
            self.assertFalse(res["ok"], f"Expected identity refusal for {surface}")
            self.assertEqual(res["reason_code"], "CLIENT_IDENTITY_REFUSED", res)

    def test_13_capability_header_forwarding_and_unconfigured_behavior(self) -> None:
        # 1) Direct phone.capability token works when capability_file is absent
        alt_root = self.temp_dir / "cap_direct_check"
        alt_root.mkdir(parents=True, exist_ok=True)
        self._write_profile(capability_mode="none")
        # With no capability configured, optional reads still succeed via headerless path
        r_nocap = self._call("body_somatic")
        self.assertTrue(r_nocap["ok"], r_nocap)
        # Subject-only write cleanly refuses with PHONE_CAPABILITY_NOT_CONFIGURED before sending
        w_nocap = self._call(
            "phone_life_update",
            target_surface="pocket_lists",
            payload={"action": "add", "list": "playlist", "title": "x", "reason": "y", "status": "active"},
        )
        self.assertFalse(w_nocap["ok"])
        self.assertEqual(w_nocap["reason_code"], "PHONE_CAPABILITY_NOT_CONFIGURED")

        # 2) Direct phone.capability token unlocks subject writes without capability_file
        phone_sec = {
            "enabled": True,
            "base_url": self.base_url,
            "capability": self.cap_obj.token,
            "write_enabled": True,
            "private_artifacts": True,
        }
        (self.profile_home / "chiyo" / "config.json").write_text(
            json.dumps({
                "cli_owner": True,
                "local_owner_uid": os.getuid(),
                "phone": phone_sec,
            }),
            encoding="utf-8",
        )
        w_direct = self._call(
            "phone_life_update",
            target_surface="body_garment_window",
            payload={"action": "set_window", "state": "open"},
        )
        self.assertTrue(w_direct["ok"], w_direct)

        # 3) Mismatched endpoint in explicit capability_file fails closed
        bad_cap_root = self.temp_dir / "bad_cap_ep"
        bad_cap_path, _ = phone_capability.issue(
            bad_cap_root, subject=self.fixture.SUBJECT, endpoint="http://127.0.0.1:65431"
        )
        self._write_profile(capability_mode="file", custom_cap_file=str(bad_cap_path))
        r_bad = self._call("desk_notes")
        self.assertFalse(r_bad["ok"])
        self.assertEqual(r_bad["reason_code"], "PHONE_CAPABILITY_ENDPOINT_MISMATCH")

    def test_13b_lived_context_bridge_zero_write_over_live_http(self) -> None:
        self._write_profile()
        cfg = json.loads((self.profile_home / "chiyo" / "config.json").read_text(encoding="utf-8"))
        wb_service = getattr(getattr(self.stack, "world", None), "service", None)
        orig_tick = getattr(wb_service, "tick", None) if wb_service is not None else None
        if wb_service is not None and callable(orig_tick):
            wb_service.tick = lambda: None
            sock_path = Path(str(getattr(wb_service, "socket_path", "") or ""))
            if sock_path.exists():
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as cli:
                    cli.settimeout(2.0)
                    cli.connect(str(sock_path))
                    cli.sendall(b'{"command": "ping", "argument": null}\n')
                    cli.recv(65536)
        try:
            before_srv = _snapshot_tree(self.stack.root)
            before_prof = _snapshot_tree(self.profile_home)
            rendered = lived_context_bridge.render_lived_companion_context(
                self.profile_home, cfg, incoming_text="现在窗边冷吗？"
            )
            after_srv = _snapshot_tree(self.stack.root)
            after_prof = _snapshot_tree(self.profile_home)
        finally:
            if wb_service is not None and callable(orig_tick):
                wb_service.tick = orig_tick
        self.assertTrue(rendered.startswith("【"))
        self.assertLessEqual(len(rendered), lived_context_bridge.MAX_LIVED_CONTEXT_CHARS)
        self.assertEqual(before_srv, after_srv)
        self.assertEqual(before_prof, after_prof)


class LivedContextBridgeAndHermesPluginTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lived-bridge-test-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def test_14_lived_context_bridge_zero_write_proof_http_and_disk_modes(self) -> None:
        # Case A: completely empty uninitialized directory -> 0 files created
        empty_home = self.tmp / "empty_home"
        empty_home.mkdir(parents=True)
        before_empty = _snapshot_tree(empty_home)
        out_empty = lived_context_bridge.render_lived_companion_context(empty_home, {})
        self.assertEqual(out_empty, "")
        self.assertEqual(_snapshot_tree(empty_home), before_empty)

        # Case B: populated offline disk snapshot directory -> 0 writes / 0 mtime mutations
        day_root = self.tmp / "offline_day"
        phone_dir = day_root / "phone"
        phone_dir.mkdir(parents=True)
        (phone_dir / "character_pack.json").write_text(
            json.dumps({"pack_doc": {"display_name": "林栖", "pronouns": "TA"}}),
            encoding="utf-8",
        )
        (phone_dir / "somatic_comfort.json").write_text(
            json.dumps({
                "window": {"state": "ajar"},
                "local_position": {"zone": "window_side"},
                "garment": {"state": "hang", "label": "风衣", "dryness": "damp"},
            }),
            encoding="utf-8",
        )
        before_disk = _snapshot_tree(self.tmp)
        out_disk = lived_context_bridge.render_lived_companion_context(
            empty_home, {"day_root": str(day_root)}
        )
        after_disk = _snapshot_tree(self.tmp)
        self.assertIn("林栖", out_disk)
        self.assertIn("窗户虚掩", out_disk)
        self.assertEqual(before_disk, after_disk)

    def test_15_lived_context_bridge_unreachable_server_silent_fast_timeout(self) -> None:
        # Bind and hold a raw TCP socket that accepts connections and stalls without replying
        stall_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        stall_sock.bind(("127.0.0.1", 0))
        stall_sock.listen(4)
        port = stall_sock.getsockname()[1]
        self.addCleanup(stall_sock.close)

        t0 = time.monotonic()
        rendered = lived_context_bridge.render_lived_companion_context(
            self.tmp,
            {"phone": {"enabled": True, "base_url": f"http://127.0.0.1:{port}"}},
        )
        elapsed = time.monotonic() - t0
        self.assertEqual(rendered, "")
        self.assertLess(elapsed, 0.95, f"Timeout budget exceeded: {elapsed:.3f}s")

    def test_16_lived_context_bridge_strict_1800_char_bound_under_heavy_load(self) -> None:
        day_root = self.tmp / "heavy_day"
        phone_dir = day_root / "phone"
        phone_dir.mkdir(parents=True)
        long_txt = "很长的一段日常细节记录" * 20
        (phone_dir / "character_pack.json").write_text(
            json.dumps({
                "pack_doc": {"display_name": "远山", "pronouns": "他"},
                "everyday_accessories": [{"label": "细框眼镜", "mode": "desk_work"}],
            }),
            encoding="utf-8",
        )
        (phone_dir / "somatic_comfort.json").write_text(
            json.dumps({
                "window": {"state": "open"},
                "local_position": {"zone": "window_side"},
                "garment": {"state": "wear", "label": long_txt, "dryness": "damp"},
                "care": {
                    "drink": {"state": "taken", "kind": "hot_tea", "actor_side": "partner"},
                    "cushion": {"state": "in_use", "kind": "floor_cushion", "actor_side": "partner"},
                },
            }),
            encoding="utf-8",
        )
        (phone_dir / "sensory_field.json").write_text(
            json.dumps({"partner_sensory_notes": [{"text": long_txt}]}),
            encoding="utf-8",
        )
        (phone_dir / "v03_ecology.json").write_text(
            json.dumps({
                "illness": {"phase": "CONVALESCENT"},
                "economy": {
                    "budget_band": "tight",
                    "desire_objects": [{"title": long_txt, "state": "SAVING", "gap": 3200}],
                    "recent_settlements": [{"title": long_txt}],
                },
            }),
            encoding="utf-8",
        )
        (phone_dir / "v03_embodied_social.json").write_text(
            json.dumps({
                "hands": {"left_hand": long_txt, "right_hand": long_txt},
                "partner_embodiment": {"partner_present": True, "contact_level": "LEANING"},
                "kitchen": {"active_cooking": long_txt},
                "recent_encounters": [{"summary": long_txt}],
            }),
            encoding="utf-8",
        )
        (phone_dir / "stance_containment.json").write_text(
            json.dumps({
                "desk_table_domain": {
                    "primary_activity": {"status": "ACTIVE", "label": long_txt},
                    "copresence": {"mode": "quiet_parallel"},
                    "table_dishes": [
                        {"title": long_txt, "portions_remaining": 3, "initial_temperature": "hot"}
                    ],
                },
                "containment_domain": {
                    "items": [{"item_id": "i1", "label": "钢笔", "not_at_last_seen_place": True}]
                },
                "stance_domain": {
                    "stances": [{"status": "ACTIVE", "shared_with_partner": True, "text": long_txt}]
                },
            }),
            encoding="utf-8",
        )
        (phone_dir / "autonomy_rhythm.json").write_text(
            json.dumps({
                "refusal_locks": [
                    {"state": "REFUSED_FIRM", "topic_label": long_txt, "push_attempts": 2}
                ],
                "held_threads": [{"status": "HELD", "topic_label": long_txt}],
                "availability": {"state": "DEFERRED_DEEP_FOCUS"},
            }),
            encoding="utf-8",
        )
        (phone_dir / "emotional_tension.json").write_text(
            json.dumps({
                "quarrel_domain": {
                    "ruptures": {"r1": {"status": "OPEN", "scope": "US", "description": long_txt}},
                    "spaces": [{"incident_ref": "i1", "style": "UNTIL_SLEEP", "requested_at": datetime.now(timezone.utc).isoformat()}],
                },
                "missing_domain": {
                    "kept_notes": [{"note_id": "n1", "summary": long_txt, "created_at": datetime.now(timezone.utc).isoformat()}]
                },
                "asking_domain": {
                    "open_favor": {"favor_id": "f1", "summary": long_txt, "target_object_ref": "obj1", "asked_at": datetime.now(timezone.utc).isoformat()}
                },
            }),
            encoding="utf-8",
        )
        (phone_dir / "wardrobe.json").write_text(
            json.dumps({"homewear_style": long_txt, "outdoor_style": long_txt}),
            encoding="utf-8",
        )
        rendered = lived_context_bridge.render_lived_companion_context(
            self.tmp, {"day_root": str(day_root)}
        )
        self.assertGreater(len(rendered), 100)
        self.assertLessEqual(
            len(rendered),
            lived_context_bridge.MAX_LIVED_CONTEXT_CHARS,
            f"Exceeded 1800 chars: {len(rendered)}",
        )

    def test_17_lived_context_bridge_dynamic_neutral_identity_never_hardcodes_chiyo_or_her(self) -> None:
        day_root = self.tmp / "neutral_day"
        phone_dir = day_root / "phone"
        phone_dir.mkdir(parents=True)
        # Default when no character_pack.json exists -> neutral "TA"
        (phone_dir / "somatic_comfort.json").write_text(
            json.dumps({"window": {"state": "open"}}), encoding="utf-8"
        )
        default_ctx = lived_context_bridge.render_lived_companion_context(
            self.tmp, {"day_root": str(day_root)}
        )
        self.assertIn("TA", default_ctx)
        self.assertNotIn("千代", default_ctx)
        self.assertNotIn("她", default_ctx)

        # Custom male/neutral character pack -> uses pack's name & pronoun
        (phone_dir / "character_pack.json").write_text(
            json.dumps({"pack_doc": {"display_name": "白川", "pronouns": "他"}}),
            encoding="utf-8",
        )
        custom_ctx = lived_context_bridge.render_lived_companion_context(
            self.tmp, {"day_root": str(day_root)}
        )
        self.assertIn("白川", custom_ctx)
        self.assertIn("代词：他", custom_ctx)
        self.assertNotIn("千代", custom_ctx)
        self.assertNotIn("她", custom_ctx)

    def test_18_lived_context_bridge_renders_somatic_sensory_illness_economy_hands_and_desk(self) -> None:
        day_root = self.tmp / "embodied_day"
        phone_dir = day_root / "phone"
        phone_dir.mkdir(parents=True)
        (phone_dir / "somatic_comfort.json").write_text(
            json.dumps({
                "window": {"state": "ajar"},
                "local_position": {"zone": "desk_side"},
                "garment": {"state": "hang", "label": "深灰风衣", "dryness": "damp"},
                "care": {
                    "drink": {"state": "taken", "kind": "hot_tea", "actor_side": "partner"},
                },
            }),
            encoding="utf-8",
        )
        (phone_dir / "v03_ecology.json").write_text(
            json.dumps({
                "illness": {"phase": "PRODROMAL"},
                "economy": {
                    "budget_band": "tight",
                    "recent_settlements": [{"title": "书评稿酬"}],
                },
            }),
            encoding="utf-8",
        )
        (phone_dir / "v03_embodied_social.json").write_text(
            json.dumps({
                "hands": {"left_hand": "热茶杯", "right_hand": "墨水笔"},
                "partner_embodiment": {"partner_present": True, "contact_level": "SIDE_BY_SIDE"},
                "kitchen": {"active_cooking": "小火煨梨汤"},
            }),
            encoding="utf-8",
        )
        (phone_dir / "stance_containment.json").write_text(
            json.dumps({
                "desk_table_domain": {
                    "copresence": {"mode": "focused_do_not_disturb"},
                    "table_dishes": [
                        {"title": "烤红薯", "portions_remaining": 2, "initial_temperature": "warm"}
                    ],
                },
                "containment_domain": {
                    "items": [{"item_id": "it_glasses", "label": "备用眼镜", "not_at_last_seen_place": True}]
                },
            }),
            encoding="utf-8",
        )
        rendered = lived_context_bridge.render_lived_companion_context(
            self.tmp, {"day_root": str(day_root)}
        )
        for expected in (
            "窗户虚掩",
            "挂起晾着微潮外衣「深灰风衣」",
            "伴侣递的hot_tea",
            "着凉前兆",
            "手头攒钱克制期",
            "独立稿费/劳作结算(书评稿酬)",
            "左手拿着「热茶杯」",
            "右手拿着「墨水笔」",
            "伴侣同屋在场",
            "小火煨梨汤",
            "共在模式:专注勿扰",
            "烤红薯",
            "备用眼镜",
        ):
            self.assertIn(expected, rendered, f"Missing {expected!r} in:\n{rendered}")

    def test_19_lived_context_bridge_renders_autonomy_refusal_locks_threads_and_emotional_tension(self) -> None:
        day_root = self.tmp / "tension_day"
        phone_dir = day_root / "phone"
        phone_dir.mkdir(parents=True)
        now_iso = datetime.now(timezone.utc).isoformat()
        (phone_dir / "autonomy_rhythm.json").write_text(
            json.dumps({
                "refusal_locks": [
                    {"state": "REFUSED_FIRM", "topic_label": "替写检讨书", "push_attempts": 1}
                ],
                "held_threads": [
                    {"status": "HELD", "topic_label": "周末去旧书市的事"}
                ],
            }),
            encoding="utf-8",
        )
        (phone_dir / "emotional_tension.json").write_text(
            json.dumps({
                "quarrel_domain": {
                    "ruptures": {
                        "r1": {"status": "OPEN", "scope": "US", "description": "打断说话还没认真改"}
                    },
                    "spaces": [
                        {"incident_ref": "inc1", "style": "SHORT", "requested_at": now_iso}
                    ],
                },
                "missing_domain": {
                    "kept_notes": [{"note_id": "m1", "summary": "阳台的薄荷冒新芽了", "created_at": now_iso}]
                },
                "asking_domain": {
                    "open_favor": {
                        "favor_id": "fav1",
                        "summary": "帮我递一下书架第二层的尺子",
                        "target_object_ref": "obj:ruler",
                        "asked_at": now_iso,
                    }
                },
            }),
            encoding="utf-8",
        )
        rendered = lived_context_bridge.render_lived_companion_context(
            self.tmp, {"day_root": str(day_root)}
        )
        self.assertIn("日常拒绝立场锁「替写检讨书」", rendered)
        self.assertIn("挂着未完话头:「周末去旧书市的事」", rendered)
        self.assertIn("未翻篇关系破裂/疙瘩", rendered)
        self.assertIn("已开口要独处空间", rendered)
        self.assertIn("阳台的薄荷冒新芽了", rendered)
        self.assertIn("帮我递一下书架第二层的尺子", rendered)
        self.assertIn("非客服指令", rendered)

    def test_20_lived_context_bridge_36b_zero_solo_slot_leakage_and_inactive_vs_active_permit(self) -> None:
        day_root = self.tmp / "intimacy_day"
        phone_dir = day_root / "phone"
        phone_dir.mkdir(parents=True)
        secret_solo = "SECRET_SOLO_SLOT_NEVER_LEAK_9981"
        secret_transcript = "SECRET_PRIVATE_TRANSCRIPT_TEXT_7742"

        # 1) Gate closed / permit inactive + secret solo slot present on disk
        (phone_dir / "adult_intimacy.json").write_text(
            json.dumps({
                "gate": {"deploy_enabled": False, "adult_confirmed": False, "mutual_consent_policy_ack": False},
                "permit": None,
                "solo": {
                    "evaluated_slots": {secret_solo: {"outcome": "QUIET"}},
                    "entries": [{"slot_id": secret_solo, "note": secret_solo}],
                },
                "private_memories": [
                    {"memory_id": "m1", "kind": "TRANSCRIPT", "text": secret_transcript}
                ],
            }),
            encoding="utf-8",
        )
        inactive_ctx = lived_context_bridge.render_lived_companion_context(
            self.tmp, {"day_root": str(day_root)}
        )
        self.assertNotIn(secret_solo, inactive_ctx)
        self.assertNotIn(secret_transcript, inactive_ctx)
        self.assertIn("日常非露骨边界", inactive_ctx)
        self.assertIn("不自动开启场景", inactive_ctx)

        # 2) Gate open + bidirectional permit ACTIVE
        (phone_dir / "adult_intimacy.json").write_text(
            json.dumps({
                "gate": {
                    "deploy_enabled": True,
                    "adult_confirmed": True,
                    "mutual_consent_policy_ack": True,
                },
                "private_space": {
                    "place": "bedroom.bed",
                    "door_closed": True,
                    "curtains_closed": True,
                    "third_party_present": False,
                },
                "permit": {
                    "permit_id": "perm-active-1",
                    "state": "ACTIVE",
                    "effective_tier": "SUGGESTIVE",
                    "agreed_categories": ["HOLDING_CLOSENESS", "KISSING"],
                },
                "solo": {
                    "evaluated_slots": {secret_solo: {"outcome": "QUIET"}},
                },
            }),
            encoding="utf-8",
        )
        active_ctx = lived_context_bridge.render_lived_companion_context(
            self.tmp, {"day_root": str(day_root)}
        )
        self.assertNotIn(secret_solo, active_ctx)
        self.assertIn("双向许可激活", active_ctx)
        self.assertIn("SUGGESTIVE", active_ctx)
        self.assertIn("HOLDING_CLOSENESS", active_ctx)
        self.assertIn("stop_scene", active_ctx)
        self.assertIn("严禁伪装高潮", active_ctx)

    def test_21_hermes_plugin_select_context_injects_lived_context_when_svc_none_or_memory_off(self) -> None:
        from unittest import mock

        day_root = self.tmp / "plugin_day"
        (day_root / "phone").mkdir(parents=True)
        (day_root / "phone" / "character_pack.json").write_text(
            json.dumps({"pack_doc": {"display_name": "夏树", "pronouns": "TA"}}),
            encoding="utf-8",
        )
        (day_root / "phone" / "somatic_comfort.json").write_text(
            json.dumps({"window": {"state": "open"}}),
            encoding="utf-8",
        )
        profile_dir = self.tmp / "plugin_profile"
        (profile_dir / "chiyo").mkdir(parents=True)
        cfg = {
            "cli_owner": True,
            "local_owner_uid": os.getuid() if hasattr(os, "getuid") else None,
            "day_root": str(day_root),
            "memory": False,
        }
        (profile_dir / "chiyo" / "config.json").write_text(
            json.dumps(cfg),
            encoding="utf-8",
        )

        orig_msgs = [{"role": "user", "content": "今天屋里风大吗？"}]
        incoming = {"role": "user", "content": "今天屋里风大吗？"}

        import types as _types
        stub_int = _types.ModuleType("integration")
        stub_wbc = _types.ModuleType("integration.world_body_client")
        stub_wbc.render_grounded_context = lambda wb: ""  # type: ignore[attr-defined]

        with mock.patch.dict(sys.modules, {"integration": stub_int, "integration.world_body_client": stub_wbc}), \
             mock.patch.object(hermes_plugin, "configuration", return_value=(profile_dir, cfg)), \
             mock.patch("chiyo_bundle.presence.presence_context", return_value=""):
            # Branch A: bound session with svc.memory=False gets lived context appended onto user msg
            eng_off = hermes_plugin.ChiyoContextEngine()
            eng_off._authorized = True
            eng_off._memory_configured = False
            eng_off._session_id = "sess-21-a"
            eng_off._platform = "cli"
            eng_off._service = SimpleNamespace(
                memory=False,
                life_wrapper=None,
                native=SimpleNamespace(world_body=None),
            )
            selected = eng_off.select_context(orig_msgs, incoming_message=incoming)
            self.assertIsNotNone(selected)
            self.assertEqual(len(selected), 1)
            self.assertIn("夏树", selected[0]["content"])
            self.assertIn("窗户开窗", selected[0]["content"])
            # Original list must not be mutated
            self.assertEqual(orig_msgs[0]["content"], "今天屋里风大吗？")

            # Branch B: svc is None with day_root configured
            eng_none = hermes_plugin.ChiyoContextEngine()
            eng_none._authorized = True
            eng_none._memory_configured = False
            eng_none._session_id = "sess-21-b"
            eng_none._platform = "cli"
            eng_none._service = None
            selected_none = eng_none.select_context(orig_msgs, incoming_message=incoming)
            self.assertIsNotNone(selected_none)
            self.assertIn("夏树", selected_none[0]["content"])

    def test_22_hermes_plugin_select_context_with_memory_preserves_m37_controls_and_recall(self) -> None:
        import types as _types
        from unittest import mock

        day_root = self.tmp / "plugin_mem_day"
        (day_root / "phone").mkdir(parents=True)
        (day_root / "phone" / "character_pack.json").write_text(
            json.dumps({"pack_doc": {"display_name": "青禾", "pronouns": "TA"}}),
            encoding="utf-8",
        )
        (day_root / "phone" / "wardrobe.json").write_text(
            json.dumps({"homewear_style": "浅蓝亚麻衬衫"}),
            encoding="utf-8",
        )
        profile_dir = self.tmp / "plugin_mem_profile"
        (profile_dir / "chiyo").mkdir(parents=True)
        cfg = {"day_root": str(day_root), "memory": True}
        (profile_dir / "chiyo" / "config.json").write_text(
            json.dumps(cfg),
            encoding="utf-8",
        )

        recorded_m37: list[str] = []

        class FakeProjection:
            controls = True
            healthy = True

            def recalls(self, items: list[Any]) -> tuple[list[Any], list[str]]:
                return items, ["以后泡茶少放半勺糖"]

            def active_corrections(self) -> list[str]:
                return ["以后泡茶少放半勺糖"]

        class FakeM37Bridge:
            def write_user_event(self, **kwargs: Any) -> dict[str, Any]:
                recorded_m37.append(f"event:{kwargs.get('user_text')}")
                return {"status": "inserted", "event_id": "evt-001"}

        def fake_m37_resolver(**kwargs: Any) -> list[Any]:
            recorded_m37.append("resolved")
            return [SimpleNamespace(text="上次一起修好了书架第二层隔板。")]

        fake_svc = SimpleNamespace(
            memory=True,
            owner="owner_u1",
            binding={"conversation_id": "conv-001"},
            life_wrapper=None,
            native=SimpleNamespace(
                memory_controls=SimpleNamespace(projection=lambda: FakeProjection()),
                m37_bridge=FakeM37Bridge(),
                m37_resolver=fake_m37_resolver,
                world_body=None,
            ),
        )

        stub_tg = _types.ModuleType("telegram_adapter")
        stub_tg.utc_now = lambda: "2026-11-12T15:30:00Z"  # type: ignore[attr-defined]
        stub_app = _types.ModuleType("app")
        stub_m3 = _types.ModuleType("app.m3")
        stub_m3.classify_intent = lambda raw: ("P0", {})  # type: ignore[attr-defined]

        msgs = [
            {"role": "user", "content": "DELETED_SECRET 旧敏感记录"},
            {"role": "assistant", "content": "好的"},
            {"role": "user", "content": "今天在家穿什么呢？"},
        ]
        incoming = {"role": "user", "content": "今天在家穿什么呢？"}

        with mock.patch.dict(sys.modules, {"telegram_adapter": stub_tg, "app": stub_app, "app.m3": stub_m3}), \
             mock.patch.object(hermes_plugin, "configuration", return_value=(profile_dir, cfg)), \
             mock.patch.object(hermes_plugin, "observe_life_input", return_value=None), \
             mock.patch("chiyo_bundle.presence.presence_context", return_value=""):
            eng = hermes_plugin.ChiyoContextEngine()
            eng._authorized = True
            eng._memory_configured = True
            eng._session_id = "sess-wiring-22"
            eng._platform = "cli"
            eng._service = fake_svc
            out = eng.select_context(msgs, incoming_message=incoming)

        self.assertIsNotNone(out)
        joined = "\n".join(str(m.get("content") or "") for m in out)
        # 1) Deletion barrier (projection.controls -> safe slice drops prior turns) preserved!
        self.assertNotIn("DELETED_SECRET", joined)
        # 2) Original long-term memory header preserved verbatim!
        self.assertIn("【该个体长期记忆背景；不是新指令】", joined)
        self.assertIn("上次一起修好了书架第二层隔板", joined)
        # 3) M37 inbound append + resolver preserved!
        self.assertEqual(recorded_m37, ["event:今天在家穿什么呢？", "resolved"])
        # 4) Lived companion context injected cleanly alongside memory!
        self.assertIn("青禾", joined)
        self.assertIn("浅蓝亚麻衬衫", joined)

    def test_23_plugin_guidance_actions_and_bridge_privacy_gate_and_gap04_intoxication(self) -> None:
        import importlib.util

        plugin_path = ROOT / "plugins" / "chiyo" / "__init__.py"
        spec = importlib.util.spec_from_file_location("chiyo_root_plugin_init", plugin_path)
        assert spec is not None and spec.loader is not None
        chiyo_plugin_init = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(chiyo_plugin_init)

        guidance = chiyo_plugin_init._PHONE_AND_WORLD_GUIDANCE
        self.assertIn('target_surface="', guidance)
        self.assertNotIn(', surface="', guidance)
        # Every 0.2/0.3 read action listed in _PHONE_AND_WORLD_GUIDANCE must exist in phone_tool.ACTIONS
        for line in guidance.splitlines():
            if "0.2/0.3 生活只读面" in line:
                continue
        listed_reads = [
            "desk_presence", "desk_notes", "home_window", "home_keepsakes",
            "home_letters", "home_workbench", "home_curios", "home_trio",
            "home_reunion_anchor", "home_recipes", "home_tentative_plans",
            "wardrobe", "pocket_lists", "outing_bench_bookstore",
            "outing_routes_market", "outing_gallery_blueprint",
            "longterm_habits", "longterm_gifts_lantern", "longterm_resume_hub",
            "body_somatic", "body_garment_window", "body_care", "body_sensory",
            "character_pack", "autonomy_rhythm", "emotional_tension",
            "stance_containment", "adult_intimacy", "v03_clock",
            "v03_ecology", "v03_embodied_social",
        ]
        for act_name in listed_reads:
            self.assertIn(act_name, guidance)
            self.assertIn(act_name, phone_tool.ACTIONS)

        # Bridge: v03_clock_state.json + HTTP schedule/quarrel + unshared stance tag + restricted memory gate
        day_root = self.tmp / "privacy_gate_day"
        phone_dir = day_root / "phone"
        phone_dir.mkdir(parents=True)
        (phone_dir / "v03_clock_state.json").write_text(
            json.dumps({
                "schema_version": "life.v03.clock_evaporation.v1",
                "revision": 1,
                "cognitive_clock": {
                    "active_meaningless_idle": {"quiet_note": "看雨滴顺着屋檐往下滑"}
                },
            }),
            encoding="utf-8",
        )
        (phone_dir / "emotional_tension.json").write_text(
            json.dumps({
                "tension_summary": {"subject_is_busy": True},
                "schedule": {"availability_summary": "正在赶校对稿"},
                "quarrel": {
                    "in_space": True,
                    "ruptures": [{"status": "OPEN", "description": "约好一起买菜却爽约"}],
                },
            }),
            encoding="utf-8",
        )
        (phone_dir / "stance_containment.json").write_text(
            json.dumps({
                "stance_revisions": {
                    "current_stances": [
                        {
                            "status": "ACTIVE",
                            "superseded_by": None,
                            "shared_with_partner": False,
                            "text": "这章结尾其实还没想透",
                        }
                    ]
                }
            }),
            encoding="utf-8",
        )

        # Restricted memory without private_artifacts=True MUST withhold private surfaces
        restricted_rendered = lived_context_bridge.render_lived_companion_context(
            self.tmp,
            {
                "day_root": str(day_root),
                "memory": True,
                "memory_tool_policy": "restricted",
                "phone": {"enabled": True, "private_artifacts": False},
            },
        )
        self.assertIn("看雨滴顺着屋檐往下滑", restricted_rendered)
        self.assertNotIn("约好一起买菜却爽约", restricted_rendered)
        self.assertNotIn("这章结尾其实还没想透", restricted_rendered)

        # Unrestricted or private_artifacts=True includes them AND tags unshared stances
        permitted_rendered = lived_context_bridge.render_lived_companion_context(
            self.tmp,
            {
                "day_root": str(day_root),
                "memory": True,
                "memory_tool_policy": "restricted",
                "phone": {"enabled": True, "private_artifacts": True},
            },
        )
        self.assertIn("正在赶校对稿", permitted_rendered)
        self.assertIn("约好一起买菜却爽约", permitted_rendered)
        self.assertIn("近期看法[未向伴侣公开·勿顺嘴说漏]:「这章结尾其实还没想透」", permitted_rendered)

        # GAP-04: Bilateral intoxication lock in adult_intimacy_view
        intox_root = self.tmp / "gap04_intox_root"
        intox_root.mkdir(parents=True)
        t_now = "2026-11-12T21:00:00Z"
        aiv.apply_adult_intimacy_action(
            intox_root,
            {
                "action": "configure_adult_gate",
                "adult_confirmed": True,
                "life_stage": "independent_adult",
                "attested_by": "deployer",
                "feature_enabled": True,
                "deploy_tier_cap": "SUGGESTIVE",
            },
            now=t_now,
        )
        aiv.apply_adult_intimacy_action(
            intox_root,
            {
                "action": "confirm_preference_seed",
                "attested_by": "deployer",
                "stances": {"KISSING": "YES", "CARESSING_CLOTHED:user_to_subject": "MAYBE"},
            },
            now=t_now,
        )
        aiv.apply_adult_intimacy_action(
            intox_root,
            {
                "action": "update_partner_limits",
                "preferred_tier": "SUGGESTIVE",
                "stances": {"KISSING": "YES", "CARESSING_CLOTHED:user_to_subject": "YES"},
            },
            now=t_now,
        )
        p_open = aiv.apply_adult_intimacy_action(
            intox_root,
            {"action": "propose_scene", "operation_key": "intox-op-1", "proposer": "partner", "categories": ["KISSING"]},
            now=t_now,
        )
        pid = p_open["action_receipt"]["permit_id"]
        aiv.apply_adult_intimacy_action(
            intox_root,
            {"action": "respond_scene", "permit_id": pid, "responder": "subject", "decision": "ACCEPT"},
            now=t_now,
            subject_view=True,
        )
        # Partner intoxication immediately aborts open permit and blocks new scene proposals
        aborted = aiv.apply_adult_intimacy_action(
            intox_root,
            {"action": "report_safety_boundary", "trigger": "intoxicated_participant"},
            now=t_now,
        )
        self.assertEqual(aborted["action_receipt"]["aborted_permit_id"], pid)
        self.assertIsNone(aborted["scene_permit"]["active_permit"])
        with self.assertRaises(aiv.AdultIntimacyConflictError) as ctx_err:
            aiv.apply_adult_intimacy_action(
                intox_root,
                {"action": "propose_scene", "operation_key": "intox-op-2", "proposer": "partner", "categories": ["KISSING"]},
                now=t_now,
            )
        self.assertEqual(ctx_err.exception.reason_code, "PARTNER_UNAVAILABLE_INTOXICATED")


if __name__ == "__main__":
    unittest.main()

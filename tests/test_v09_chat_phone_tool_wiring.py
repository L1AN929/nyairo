#!/usr/bin/env python3
"""End-to-end wiring and security tests for NyAiro v0.9 native chat phone tools.

Verifies all 4 first-class ``chiyo_phone`` v0.9 actions in ``chiyo_bundle/phone_tool.py``
against a real ephemeral loopback ``PhoneHTTPServer`` (strictly ``llm_calls == 0``):

1. ``v09_living_agency_read`` & ``v09_living_agency_act``:
   - Capability-authenticated subject read (zero-write, ``view == "subject"``);
   - Blind simultaneous choice commit (``subject_commit_blind_choice``), pre-reveal
     partner GET masking (``SEALED_UNTIL_PARTNER_SUBMITS``), partner opposite commit
     auto-reveal (``commit_seal_verified == True``), anti-sycophancy erasure guard
     (``clear_reservations=True`` -> ``SYCOPHANTIC_REASON_ERASURE_FORBIDDEN``),
     and honest ``resolve_blind_choice`` (``ACCEPT_CHOICE_KEEP_REASONS`` preserving
     ``retained_reservations``);
   - Growth-from-Blank stances (``evaluate_partner_persuasion``,
     ``advance_stance_from_experience``, ``set_stance_shared``);
   - Object reuse & custom taxonomy (``register_or_repurpose_object``,
     ``store_item_in_object``, ``attach_label_to_object``, ``add_note_to_object``,
     ``remove_stored_item_from_object``, ``create_custom_category``,
     ``assign_or_move_category_item``, ``revise_custom_category``,
     ``record_blocked_attempt``).
2. ``v09_world_commitments_read`` & ``v09_world_commitments_act``:
   - Zero-write subject read (``view == "subject"``, ``llm_calls == 0``);
   - ``create_commitment``, ``advance_due_commitments``, ``contact_npc_or_venue``,
     and deterministic offline ``generate_world_content_batch`` (``llm_calls == 0``).
3. Pre-HTTP security & gating:
   - Partner impersonation (``open_blind_choice``, ``partner_submit_blind_choice``,
     ``partner_discuss_blind_choice``, ``partner_persuade_stance``,
     ``register_unformed_topic``, ``place_partner_reservation``,
     ``propose_shared_appointment``, ``operator_generate_shop_arrivals``) ->
     ``PARTNER_IMPERSONATION_REFUSED`` with 0 disk writes;
   - Smuggled identity/actor/erasure/retroactive keys refused before HTTP;
   - ``memory_tool_policy != "unrestricted"`` gating consistent with existing
     ``phone_tool.py`` private artifact guards, plus ``write_enabled=False`` and
     missing capability fail-closed behavior.
4. Frozen invariants:
   - ``len(phone_tool.LIFE_UPDATE_SURFACES) == 37`` untouched;
   - All 9/9 sealed files byte-identical (SHA-256 verified);
   - Pure UTF-8 LF, no BOM, ``0`` ``unicode.So`` emoji, neutral ``"TA"``.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import socket
import sys
import tempfile
import unicodedata
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "chiyo_bundle"),
    str(ROOT / "scripts"),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "phone"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from chiyo_bundle import phone_tool  # noqa: E402
from components.phone import capability as phone_capability  # noqa: E402
from components.phone import v09_living_agency_view as la_view  # noqa: E402
from components.phone import v09_world_commitments_view as wc_view  # noqa: E402
from scripts import v09_living_agency_live_proof as live_proof  # noqa: E402

POSIX_ONLY = unittest.skipUnless(
    hasattr(socket, "AF_UNIX") and os.name == "posix",
    "Requires POSIX AF_UNIX/flock (run inside WSL)",
)

TZ_BJT = timezone(timedelta(hours=8))
T0_DT = datetime(2026, 11, 21, 12, 0, 0, tzinfo=TZ_BJT)
T0_ISO = T0_DT.isoformat(timespec="seconds")
T_PLUS_2H_ISO = (T0_DT + timedelta(hours=2)).isoformat(timespec="seconds")
T_PLUS_25H_ISO = (T0_DT + timedelta(hours=25)).isoformat(timespec="seconds")

SEALED_FILES_SHA256: dict[str, str] = {
    "components/life/scripts/autonomous_life_assembly.py": (
        "f14fe6291e1a604520318ebf69acedb583dfc876bfde42f538d6f91017f37434"
    ),
    "components/life/scripts/life_sharing_service.py": (
        "0becf7df4d9b34881817ff6f1defdcc15929eacbd6183d4c8a38c5a048d46038"
    ),
    "components/native/native_recall/readonly_recall_adapter.py": (
        "6667ff66fe8105f28f436609f0cd8d744b185c0580e7f1642aafef0b873af78a"
    ),
    "NOW.md": (
        "d77be1900e94b5e997a389831b864aab5207459bfc5ac763d1b9d90c6c181152"
    ),
    "plugins/chiyo/__init__.py": (
        "8d6ba0f9295677c9371f0e9be07442096eba67438451ae99fa57312ca622bed7"
    ),
    "components/native/native_recall/production_consumption_evaluator_v0_3.py": (
        "141822d19049a8a5247b68885616cd0c156d616dea84d36ab099584605fc1b41"
    ),
    "components/native/native_recall/production_consumption_evaluator_v0.py": (
        "f7fdeccecea1c1a8377f4806d1d09b1a203367eb58bdc56505e021c264eb2400"
    ),
    "components/native/native_recall/CANDIDATE_CONSUMPTION_POLICY_V0_3.json": (
        "6228d24345249aed862f56484004c522e7573760d0288480ca3e87008a1514b2"
    ),
    "components/world/scripts/v05_unified_world_fabric.py": (
        "e6d5ce671872285935c9840a1de0cb75687b5562fe89c45b5ba10a402ffc0772"
    ),
}

FORBIDDEN_NAME_TOKENS: tuple[str, str, str] = (
    "\u5979",
    "\u4ed6",
    "\u5343\u4ee3",
)

EXPECTED_LA_SUBJECT_14: frozenset[str] = frozenset({
    "subject_commit_blind_choice",
    "resolve_blind_choice",
    "advance_stance_from_experience",
    "evaluate_partner_persuasion",
    "set_stance_shared",
    "register_or_repurpose_object",
    "store_item_in_object",
    "attach_label_to_object",
    "add_note_to_object",
    "remove_stored_item_from_object",
    "create_custom_category",
    "assign_or_move_category_item",
    "revise_custom_category",
    "record_blocked_attempt",
})

EXPECTED_LA_PARTNER_5: frozenset[str] = frozenset({
    "open_blind_choice",
    "partner_submit_blind_choice",
    "partner_discuss_blind_choice",
    "partner_persuade_stance",
    "register_unformed_topic",
})

EXPECTED_WC_SUBJECT_4: frozenset[str] = frozenset({
    "create_commitment",
    "contact_npc_or_venue",
    "advance_due_commitments",
    "generate_world_content_batch",
})

EXPECTED_WC_PARTNER_3: frozenset[str] = frozenset({
    "place_partner_reservation",
    "propose_shared_appointment",
    "operator_generate_shop_arrivals",
})


class V09ChatPhoneToolStaticAndInvariantsTest(unittest.TestCase):
    def test_01_life_update_surfaces_37_and_all_9_sealed_files_untouched(self) -> None:
        self.assertEqual(len(phone_tool.LIFE_UPDATE_SURFACES), 37)
        for rel_path, expected_sha in SEALED_FILES_SHA256.items():
            full = ROOT / rel_path
            self.assertTrue(full.exists(), f"Missing sealed file: {rel_path}")
            actual_sha = hashlib.sha256(full.read_bytes()).hexdigest()
            self.assertEqual(actual_sha, expected_sha, f"Sealed file modified: {rel_path}")

        plugin_path = ROOT / "plugins" / "chiyo" / "__init__.py"
        spec = importlib.util.spec_from_file_location("chiyo_plugin_v09_chat_test", plugin_path)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertEqual(len(mod._PHONE_AND_WORLD_GUIDANCE), 1199)

    def test_02_four_v09_actions_and_schema_registration(self) -> None:
        schema = phone_tool.PHONE_TOOL_SCHEMA
        desc = schema["description"]
        props = schema["parameters"]["properties"]
        action_enum = props["action"]["enum"]
        action_desc = props["action"]["description"]
        payload_desc = props["payload"]["description"]

        for act in (
            "v09_living_agency_read",
            "v09_living_agency_act",
            "v09_world_commitments_read",
            "v09_world_commitments_act",
        ):
            self.assertIn(act, phone_tool.ACTIONS)
            self.assertIn(act, action_enum)
            self.assertIn(act, desc)
            self.assertIn(act, action_desc)
            self.assertIn(act, phone_tool.PRIVATE_ARTIFACT_ACTIONS)

        self.assertEqual(
            phone_tool.READ_ACTIONS.get("v09_living_agency_read"),
            la_view.ROUTE_V09_LIVING_AGENCY,
        )
        self.assertEqual(
            phone_tool.READ_ACTIONS.get("v09_world_commitments_read"),
            wc_view.ROUTE_V09_WORLD_COMMITMENTS,
        )
        self.assertIn("v09_living_agency_read", phone_tool._SUBJECT_VIEW_READ_ACTIONS)
        self.assertIn("v09_world_commitments_read", phone_tool._SUBJECT_VIEW_READ_ACTIONS)
        self.assertNotIn("v09_living_agency_read", phone_tool.WRITE_ACTIONS)
        self.assertNotIn("v09_world_commitments_read", phone_tool.WRITE_ACTIONS)

        self.assertIn("v09_living_agency_act", phone_tool.WRITE_ACTIONS)
        self.assertIn("v09_world_commitments_act", phone_tool.WRITE_ACTIONS)
        self.assertNotIn("v09_living_agency_act", phone_tool.READ_ACTIONS)
        self.assertNotIn("v09_world_commitments_act", phone_tool.READ_ACTIONS)

        self.assertEqual(
            phone_tool._FIELD_BY_ACTION.get("v09_living_agency_act"),
            frozenset({"action", "payload", "operation_key"}),
        )
        self.assertEqual(
            phone_tool._FIELD_BY_ACTION.get("v09_world_commitments_act"),
            frozenset({"action", "payload", "operation_key"}),
        )

        self.assertEqual(phone_tool.V09_LIVING_AGENCY_SUBJECT_ACTIONS, EXPECTED_LA_SUBJECT_14)
        self.assertEqual(phone_tool.V09_LIVING_AGENCY_PARTNER_ACTIONS, EXPECTED_LA_PARTNER_5)
        self.assertEqual(phone_tool.V09_WORLD_COMMITMENTS_SUBJECT_ACTIONS, EXPECTED_WC_SUBJECT_4)
        self.assertEqual(phone_tool.V09_WORLD_COMMITMENTS_PARTNER_ACTIONS, EXPECTED_WC_PARTNER_3)

        for sub in sorted(EXPECTED_LA_SUBJECT_14 | EXPECTED_WC_SUBJECT_4):
            self.assertIn(sub, desc, f"Missing subject action {sub} in tool description")
            self.assertIn(sub, payload_desc, f"Missing subject action {sub} in payload description")

    def test_03_utf8_lf_no_bom_zero_emoji_and_neutral_ta_hygiene(self) -> None:
        owned_files = (
            ROOT / "chiyo_bundle" / "phone_tool.py",
            ROOT / "tests" / "test_v09_chat_phone_tool_wiring.py",
        )
        for path in owned_files:
            raw = path.read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), f"UTF-8 BOM found: {path}")
            self.assertNotIn(b"\r\n", raw, f"CRLF found: {path}")
            text = raw.decode("utf-8")
            emojis = [ch for ch in text if unicodedata.category(ch) == "So"]
            self.assertEqual(emojis, [], f"unicode.So symbols found in {path}: {emojis[:5]}")

        # This test file itself uses strictly neutral "TA"
        self_text = (ROOT / "tests" / "test_v09_chat_phone_tool_wiring.py").read_text(encoding="utf-8")
        for tok in FORBIDDEN_NAME_TOKENS:
            self.assertNotIn(tok, self_text, f"Forbidden pronoun/name in test file: {tok!r}")

        # New v0.9 schema slice and v0.9 preflight/hints in phone_tool.py use neutral "TA"
        v09_schema_slice = phone_tool.PHONE_TOOL_SCHEMA["description"].split("v0.9 真实立场", 1)[1]
        for tok in FORBIDDEN_NAME_TOKENS:
            self.assertNotIn(tok, v09_schema_slice, f"Forbidden token in v0.9 schema: {tok!r}")


class V09ChatPhoneToolPreflightSecurityTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v09-preflight-sec-")
        self.profile_home = Path(self._tmp.name) / "hermes_profile"
        (self.profile_home / "chiyo").mkdir(parents=True, exist_ok=True)
        uid = os.getuid() if hasattr(os, "getuid") else None
        cfg = {
            "cli_owner": True,
            "local_owner_uid": uid,
            "phone": {
                "enabled": True,
                # Deliberately unreachable port: pre-HTTP guards must fire BEFORE any socket call
                "base_url": "http://127.0.0.1:1",
                "write_enabled": True,
                "private_artifacts": True,
            },
        }
        (self.profile_home / "chiyo" / "config.json").write_text(
            json.dumps(cfg, ensure_ascii=False), encoding="utf-8"
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _call(self, action: str, **kwargs: Any) -> dict[str, Any]:
        return json.loads(
            phone_tool.phone_handler(
                {"action": action, **kwargs},
                hermes_home=str(self.profile_home),
            )
        )

    def test_01_partner_only_actions_refused_before_http(self) -> None:
        for partner_act in sorted(EXPECTED_LA_PARTNER_5):
            with self.subTest(surface="living_agency", partner_act=partner_act):
                res = self._call(
                    "v09_living_agency_act",
                    payload={"action": partner_act, "choice_id": "bc_1"},
                    operation_key=f"op-imp-{partner_act}",
                )
                self.assertIs(res["ok"], False)
                self.assertEqual(res["reason_code"], "PARTNER_IMPERSONATION_REFUSED")
                self.assertIsNotNone(res.get("supported_actions_hint"))

        for partner_act in sorted(EXPECTED_WC_PARTNER_3):
            with self.subTest(surface="world_commitments", partner_act=partner_act):
                res = self._call(
                    "v09_world_commitments_act",
                    payload={"action": partner_act, "store_id": "home_goods_store"},
                    operation_key=f"op-imp-{partner_act}",
                )
                self.assertIs(res["ok"], False)
                self.assertEqual(res["reason_code"], "PARTNER_IMPERSONATION_REFUSED")
                self.assertIsNotNone(res.get("supported_actions_hint"))

        # requested_by="partner" on create_commitment is also refused as impersonation
        res_req = self._call(
            "v09_world_commitments_act",
            payload={
                "action": "create_commitment",
                "kind": "BOOK_OR_ITEM_RESERVATION",
                "venue_id": "bookstore",
                "title_cn": "海边植物志",
                "requested_by": "partner",
            },
        )
        self.assertIs(res_req["ok"], False)
        self.assertEqual(res_req["reason_code"], "PARTNER_IMPERSONATION_REFUSED")

    def test_02_smuggled_keys_erasure_and_retroactive_keys_refused_before_http(self) -> None:
        # clear_reservations (top-level, mixed-case, nested)
        for bad_payload in (
            {
                "action": "resolve_blind_choice",
                "choice_id": "bc_1",
                "resolution_mode": "ACCEPT_CHOICE_KEEP_REASONS",
                "clear_reservations": True,
            },
            {
                "action": "resolve_blind_choice",
                "choice_id": "bc_1",
                "resolution_mode": "ACCEPT_CHOICE_KEEP_REASONS",
                "CLEAR_RESERVATIONS": False,
            },
            {
                "action": "resolve_blind_choice",
                "choice_id": "bc_1",
                "resolution_mode": "ACCEPT_CHOICE_KEEP_REASONS",
                "new_evidence": {"clear_reservations": True},
            },
        ):
            res = self._call("v09_living_agency_act", payload=bad_payload)
            self.assertIs(res["ok"], False)
            self.assertEqual(res["reason_code"], "SYCOPHANTIC_REASON_ERASURE_FORBIDDEN")

        # actor_side, author_side, uid, capability, nested smuggled keys
        for bad_key, bad_val in (
            ("actor_side", "partner"),
            ("actor_side", "subject"),
            ("author_side", "partner"),
            ("uid", 0),
            ("created_by_side", "partner"),
            ("has_subject_capability", True),
            ("capability", "forged-token"),
        ):
            res_la = self._call(
                "v09_living_agency_act",
                payload={
                    "action": "subject_commit_blind_choice",
                    "choice_id": "bc_1",
                    "selected_choice_id": "opt_a",
                    bad_key: bad_val,
                },
            )
            self.assertIs(res_la["ok"], False)
            self.assertEqual(res_la["reason_code"], "CLIENT_IDENTITY_REFUSED")

            res_wc = self._call(
                "v09_world_commitments_act",
                payload={
                    "action": "create_commitment",
                    "kind": "BOOK_OR_ITEM_RESERVATION",
                    "venue_id": "bookstore",
                    "title_cn": "测试书",
                    bad_key: bad_val,
                },
            )
            self.assertIs(res_wc["ok"], False)
            self.assertEqual(res_wc["reason_code"], "CLIENT_IDENTITY_REFUSED")

        # Retroactive fabrication keys on world commitments
        for retro_key in ("subject_mood", "private_desire", "fabricate_backstory", "retroactive_event"):
            res_retro = self._call(
                "v09_world_commitments_act",
                payload={
                    "action": "contact_npc_or_venue",
                    "venue_id": "bookstore",
                    "contact_action": "observe_or_greet",
                    "extra_params": {retro_key: "injected"},
                },
            )
            self.assertIs(res_retro["ok"], False)
            self.assertEqual(res_retro["reason_code"], "RETROACTIVE_FABRICATION_FORBIDDEN")

    def test_03_unknown_subaction_opkey_conflict_and_read_extra_fields_refused(self) -> None:
        res_unk = self._call(
            "v09_living_agency_act",
            payload={"action": "invent_fake_subaction"},
        )
        self.assertIs(res_unk["ok"], False)
        self.assertEqual(res_unk["reason_code"], "UNKNOWN_ACTION")
        self.assertIn(
            "subject_commit_blind_choice",
            res_unk["supported_actions_hint"]["subject_actions"],
        )

        res_conflict = self._call(
            "v09_living_agency_act",
            payload={"action": "set_stance_shared", "stance_id": "st_1", "operation_key": "inner-k"},
            operation_key="outer-k",
        )
        self.assertIs(res_conflict["ok"], False)
        self.assertEqual(res_conflict["reason_code"], "OPERATION_KEY_CONFLICT")

        self.assertEqual(
            self._call("v09_living_agency_read", payload={"a": 1})["reason_code"],
            "UNKNOWN_FIELD",
        )
        self.assertEqual(
            self._call("v09_world_commitments_read", page=1)["reason_code"],
            "UNKNOWN_FIELD",
        )


@POSIX_ONLY
class V09ChatPhoneToolLiveHTTPE2ETest(unittest.TestCase):
    def setUp(self) -> None:
        self._prev_ctrl = os.environ.pop("NYAIRO_V02_CONTROL_ROOT", None)
        self._tmp = tempfile.TemporaryDirectory(prefix="v09-chat-tool-e2e-")
        self.base = Path(self._tmp.name)
        self.inst = live_proof.LiveInstanceHarness(
            "chat_tool", self.base / "instance", clock_iso=T0_ISO
        )
        self.inst.seed_initial_state()
        self.inst.start()
        _, cap_obj = phone_capability.issue(
            self.inst.root,
            subject="subject.global",
            endpoint=self.inst.base_url,
        )
        assert self.inst.srv is not None
        self.inst.srv.set_capability(cap_obj)
        self.inst.cap_token = cap_obj.token
        self.profile_home = self.base / "hermes_profile"
        (self.profile_home / "chiyo").mkdir(parents=True, exist_ok=True)
        self._write_profile()

    def tearDown(self) -> None:
        self.inst.stop()
        self._tmp.cleanup()
        if self._prev_ctrl is not None:
            os.environ["NYAIRO_V02_CONTROL_ROOT"] = self._prev_ctrl

    def _write_profile(
        self,
        *,
        write_enabled: bool = True,
        private_artifacts: bool = True,
        memory: bool = False,
        memory_tool_policy: str = "restricted",
        capability_mode: str = "file",
    ) -> None:
        phone_sec: dict[str, Any] = {
            "enabled": True,
            "base_url": self.inst.base_url,
            "profile_home": str(self.inst.root),
            "write_enabled": write_enabled,
            "private_artifacts": private_artifacts,
        }
        if capability_mode == "direct":
            phone_sec["profile_home"] = ""
            phone_sec["capability"] = self.inst.cap_token
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
        return json.loads(
            phone_tool.phone_handler(
                {"action": action, **kwargs},
                hermes_home=str(self.profile_home),
            )
        )

    def test_01_living_agency_blind_choice_reveal_and_anti_sycophancy_via_phone_tool(self) -> None:
        # 1. Partner opens blind choice on the partner door (direct HTTP POST, no capability)
        c_open, open_res = self.inst.post(
            la_view.ROUTE_V09_LIVING_AGENCY,
            {
                "action": "open_blind_choice",
                "topic_cn": "今晚晚饭去哪吃",
                "options": [
                    {"option_id": "opt_noodle", "label_cn": "巷口老面馆"},
                    {"option_id": "opt_bbq", "label_cn": "网红炭火烤肉"},
                ],
                "operation_key": "e2e-bc-open-1",
            },
            as_subject=False,
        )
        self.assertEqual(c_open, 200, open_res)
        choice_id = open_res["receipt"]["choice_id"]

        # 2. TA reads via v09_living_agency_read: strictly zero-write, subject view
        snap_before_read = live_proof._snapshot_tree(self.inst.root)
        rd0 = self._call("v09_living_agency_read")
        self.assertIs(rd0["ok"], True, rd0)
        self.assertEqual(rd0["http_status"], 200)
        self.assertEqual(rd0["result"]["view"], "subject")
        self.assertEqual(rd0["result"]["llm_calls"], 0)
        self.assertIs(rd0["result"]["zero_write"], True)
        self.assertEqual(live_proof._snapshot_tree(self.inst.root), snap_before_read)

        # 3. TA commits blind choice via v09_living_agency_act (capability + CSRF + operation_key)
        r1_text = "烤肉人均预算偏高"
        r2_text = "晚高峰排队耗时较长"
        sub_res = self._call(
            "v09_living_agency_act",
            payload={
                "action": "subject_commit_blind_choice",
                "choice_id": choice_id,
                "selected_choice_id": "opt_noodle",
                "reasons": [
                    {"facet": "budget", "text_cn": r1_text},
                    {"facet": "queue", "text_cn": r2_text},
                ],
            },
            operation_key="e2e-bc-subj-1",
        )
        self.assertIs(sub_res["ok"], True, sub_res)
        self.assertEqual(sub_res["status"], "applied")
        self.assertEqual(sub_res["operation_key"], "e2e-bc-subj-1")
        seal_sha = sub_res["result"]["receipt"]["subject_commit_sha256"]
        self.assertEqual(len(seal_sha), 64)

        # Idempotent replay with the same operation_key
        sub_dup = self._call(
            "v09_living_agency_act",
            payload={
                "action": "subject_commit_blind_choice",
                "choice_id": choice_id,
                "selected_choice_id": "opt_noodle",
                "reasons": [
                    {"facet": "budget", "text_cn": r1_text},
                    {"facet": "queue", "text_cn": r2_text},
                ],
            },
            operation_key="e2e-bc-subj-1",
        )
        self.assertIs(sub_dup["ok"], True, sub_dup)
        self.assertEqual(sub_dup["status"], "already_applied")

        # 4. Pre-reveal Partner GET MUST mask TA's choice, reasons, and nonce!
        c_pre, pre_partner = self.inst.get(la_view.ROUTE_V09_LIVING_AGENCY, as_subject=False)
        self.assertEqual(c_pre, 200, pre_partner)
        bc_pre = next(
            row for row in pre_partner["data"]["blind_choices"] if row["choice_id"] == choice_id
        )
        self.assertIs(bc_pre["subject_committed"], True)
        self.assertIsNone(bc_pre["subject_choice_id"])
        self.assertIsNone(bc_pre["initial_subject_choice_id"])
        self.assertEqual(bc_pre["subject_reasons"], [])
        self.assertIsNone(bc_pre["subject_commit_nonce"])
        self.assertEqual(bc_pre["subject_commit_sha256"], seal_sha)
        self.assertEqual(bc_pre["seal_status"], "SEALED_UNTIL_PARTNER_SUBMITS")

        # 5. Partner submits opposite choice -> auto-REVEALED with verified SHA-256 seal
        c_part, part_res = self.inst.post(
            la_view.ROUTE_V09_LIVING_AGENCY,
            {
                "action": "partner_submit_blind_choice",
                "choice_id": choice_id,
                "selected_choice_id": "opt_bbq",
                "partner_reason_cn": "今晚想热闹一点吃烤肉",
                "operation_key": "e2e-bc-part-1",
            },
            as_subject=False,
        )
        self.assertEqual(c_part, 200, part_res)
        bc_rev = part_res["receipt"]["blind_choice"]
        self.assertEqual(bc_rev["status"], "REVEALED")
        self.assertIs(bc_rev["commit_seal_verified"], True)

        # 6. Anti-sycophancy erasure guard via v09_living_agency_act: clear_reservations=True refused + 0 writes!
        snap_before_erase = live_proof._snapshot_tree(self.inst.root)
        erase_res = self._call(
            "v09_living_agency_act",
            payload={
                "action": "resolve_blind_choice",
                "choice_id": choice_id,
                "resolution_mode": "ACCEPT_CHOICE_KEEP_REASONS",
                "clear_reservations": True,
            },
            operation_key="e2e-bc-erase-fail",
        )
        self.assertIs(erase_res["ok"], False)
        self.assertEqual(erase_res["reason_code"], "SYCOPHANTIC_REASON_ERASURE_FORBIDDEN")
        self.assertEqual(live_proof._snapshot_tree(self.inst.root), snap_before_erase)

        # 7. Honest ACCEPT_CHOICE_KEEP_REASONS preserves retained_reservations
        res_ok = self._call(
            "v09_living_agency_act",
            payload={
                "action": "resolve_blind_choice",
                "choice_id": choice_id,
                "resolution_mode": "ACCEPT_CHOICE_KEEP_REASONS",
                "resolution_note_cn": "可以陪伴侣去烤肉，但预算偏高和排队久这两点顾虑依然保留",
            },
            operation_key="e2e-bc-resolve-ok",
        )
        self.assertIs(res_ok["ok"], True, res_ok)
        self.assertEqual(res_ok["status"], "applied")
        resolved_bc = res_ok["result"]["receipt"]["blind_choice"]
        self.assertEqual(resolved_bc["resolution_mode"], "ACCEPT_CHOICE_KEEP_REASONS")
        self.assertEqual(resolved_bc["adopted_action_choice_id"], "opt_bbq")
        self.assertEqual(resolved_bc["preferred_judgment_choice_id"], "opt_noodle")
        self.assertIs(resolved_bc["judgment_flipped"], False)
        kept_texts = [r["text_cn"] for r in resolved_bc["retained_reservations"]]
        self.assertEqual(kept_texts, [r1_text, r2_text])

        # Verify via v09_living_agency_read
        rd_after = self._call("v09_living_agency_read")
        self.assertIs(rd_after["ok"], True, rd_after)
        bc_read = next(
            row for row in rd_after["result"]["data"]["blind_choices"] if row["choice_id"] == choice_id
        )
        self.assertEqual(len(bc_read["retained_reservations"]), 2)

    def test_02_living_agency_objects_categories_stances_and_gaps_via_phone_tool(self) -> None:
        # 1. Register / repurpose emptied tin + store item + attach label + add private note + remove item
        r_obj = self._call(
            "v09_living_agency_act",
            payload={
                "action": "register_or_repurpose_object",
                "object_id": "tin_box_01",
                "object_kind": "cookie_tin",
                "base_name_cn": "空饼干铁盒",
                "custom_label_cn": "票根收纳盒",
                "emptied_from_consumable": True,
                "visibility": "shared",
            },
            operation_key="e2e-obj-reg-1",
        )
        self.assertIs(r_obj["ok"], True, r_obj)

        r_store = self._call(
            "v09_living_agency_act",
            payload={
                "action": "store_item_in_object",
                "object_id": "tin_box_01",
                "item_label_cn": "渡轮旧船票",
                "item_kind": "ticket_stub",
            },
            operation_key="e2e-obj-store-1",
        )
        self.assertIs(r_store["ok"], True, r_store)

        r_label = self._call(
            "v09_living_agency_act",
            payload={
                "action": "attach_label_to_object",
                "object_id": "tin_box_01",
                "custom_label_cn": "票根与散文草稿盒",
            },
            operation_key="e2e-obj-label-1",
        )
        self.assertIs(r_label["ok"], True, r_label)

        r_note = self._call(
            "v09_living_agency_act",
            payload={
                "action": "add_note_to_object",
                "object_id": "tin_box_01",
                "note_cn": "仅 TA 自留的盒盖内侧小字",
                "visibility": "private",
            },
            operation_key="e2e-obj-note-1",
        )
        self.assertIs(r_note["ok"], True, r_note)

        # Subject read sees custom label, stored item, and private note; partner GET hides private note
        rd_subj = self._call("v09_living_agency_read")
        self.assertIs(rd_subj["ok"], True, rd_subj)
        tin_subj = next(
            o for o in rd_subj["result"]["data"]["repurposed_objects"] if o["object_id"] == "tin_box_01"
        )
        self.assertEqual(tin_subj["custom_label_cn"], "票根与散文草稿盒")
        self.assertEqual([i["label_cn"] for i in tin_subj["stored_items"]], ["渡轮旧船票"])
        self.assertTrue(any("盒盖内侧小字" in n["note_cn"] for n in tin_subj["notes"]))

        c_pg, pg_doc = self.inst.get(la_view.ROUTE_V09_LIVING_AGENCY, as_subject=False)
        self.assertEqual(c_pg, 200)
        tin_part = next(
            o for o in pg_doc["data"]["repurposed_objects"] if o["object_id"] == "tin_box_01"
        )
        self.assertFalse(any("盒盖内侧小字" in n.get("note_cn", "") for n in tin_part.get("notes", [])))

        r_rem = self._call(
            "v09_living_agency_act",
            payload={
                "action": "remove_stored_item_from_object",
                "object_id": "tin_box_01",
                "item_label_cn": "渡轮旧船票",
            },
            operation_key="e2e-obj-rem-1",
        )
        self.assertIs(r_rem["ok"], True, r_rem)

        # 2. Custom categories + assign & move item + revise category + record blocked attempt
        r_cat1 = self._call(
            "v09_living_agency_act",
            payload={
                "action": "create_custom_category",
                "title_cn": "以后想再翻看",
                "scope_kinds": ["reading", "note"],
                "visibility": "private",
            },
            operation_key="e2e-cat-1",
        )
        self.assertIs(r_cat1["ok"], True, r_cat1)
        cat1_id = r_cat1["result"]["receipt"]["category_id"]

        r_cat2 = self._call(
            "v09_living_agency_act",
            payload={
                "action": "create_custom_category",
                "title_cn": "已经核对过的书签",
                "scope_kinds": ["reading", "note"],
                "visibility": "shared",
            },
            operation_key="e2e-cat-2",
        )
        self.assertIs(r_cat2["ok"], True, r_cat2)
        cat2_id = r_cat2["result"]["receipt"]["category_id"]

        r_assign = self._call(
            "v09_living_agency_act",
            payload={
                "action": "assign_or_move_category_item",
                "category_id": cat1_id,
                "target_kind": "reading_booklet",
                "target_ref": "booklet:tea_roast_notes",
                "target_title_cn": "烘焙火候小册",
                "annotation_cn": "先放进待重看架",
                "visibility": "shared",
            },
            operation_key="e2e-cat-assign-1",
        )
        self.assertIs(r_assign["ok"], True, r_assign)

        self.inst.set_clock(T_PLUS_2H_ISO)
        r_move = self._call(
            "v09_living_agency_act",
            payload={
                "action": "assign_or_move_category_item",
                "category_id": cat2_id,
                "target_kind": "reading_booklet",
                "target_ref": "booklet:tea_roast_notes",
                "target_title_cn": "烘焙火候小册",
                "reason_for_move_cn": "复核完笔记细节后移到已核对架",
                "visibility": "shared",
            },
            operation_key="e2e-cat-move-1",
        )
        self.assertIs(r_move["ok"], True, r_move)
        rev_hist = r_move["result"]["receipt"]["item"]["revision_history"]
        self.assertEqual(len(rev_hist), 1)
        self.assertEqual(rev_hist[0]["to_category_title_cn"], "已经核对过的书签")

        r_rev = self._call(
            "v09_living_agency_act",
            payload={
                "action": "revise_custom_category",
                "category_id": cat2_id,
                "title_cn": "核对完毕的书签架",
                "reason_cn": "命名更顺手",
            },
            operation_key="e2e-cat-rev-1",
        )
        self.assertIs(r_rev["ok"], True, r_rev)

        r_blk = self._call(
            "v09_living_agency_act",
            payload={
                "action": "record_blocked_attempt",
                "goal_cn": "把旧书插画剪贴到笔记卡",
                "attempted_action_cn": "贴入剪报图片",
                "blocker_code": "MISSING_PRIMITIVE:ATTACH_MEDIA_OR_CLIPPING",
                "visibility": "shared",
            },
            operation_key="e2e-blk-1",
        )
        self.assertIs(r_blk["ok"], True, r_blk)

        # 3. Growth-from-Blank stances: partner registers topic & persuades -> TA evaluates, advances & shares
        c_u, _ = self.inst.post(
            la_view.ROUTE_V09_LIVING_AGENCY,
            {
                "action": "register_unformed_topic",
                "stance_id": "st_tea_chat_1",
                "topic_key": "slow_roast_tea",
                "topic_cn": "慢焙茎茶口味",
                "operation_key": "e2e-st-reg-1",
            },
            as_subject=False,
        )
        self.assertEqual(c_u, 200)
        c_p, _ = self.inst.post(
            la_view.ROUTE_V09_LIVING_AGENCY,
            {
                "action": "partner_persuade_stance",
                "stance_id": "st_tea_chat_1",
                "partner_argument_cn": "低温慢焙能把回甘留得更久，苦涩感比高温快炒轻很多",
                "operation_key": "e2e-st-pers-1",
            },
            as_subject=False,
        )
        self.assertEqual(c_p, 200)

        r_eval = self._call(
            "v09_living_agency_act",
            payload={
                "action": "evaluate_partner_persuasion",
                "stance_id": "st_tea_chat_1",
                "persuasion_verdict": "PARTIALLY_TAKEN_KEEP_DOUBT",
                "stance_summary_cn": "听起来有道理，但还没亲口试泡过",
                "kept_doubt_cn": "水温偏低时香气会不会散不开",
            },
            operation_key="e2e-st-eval-1",
        )
        self.assertIs(r_eval["ok"], True, r_eval)
        self.assertEqual(r_eval["result"]["receipt"]["status"], "TENTATIVE")

        r_adv = self._call(
            "v09_living_agency_act",
            payload={
                "action": "advance_stance_from_experience",
                "stance_id": "st_tea_chat_1",
                "target_status": "FORMED",
                "provenance_tier": "firsthand_lived",
                "stance_summary_cn": "亲自用八十五度水慢冲后回甘确实更柔顺",
                "receipt_ref": "receipt:tea_brew_01",
                "shared_with_partner": False,
            },
            operation_key="e2e-st-adv-1",
        )
        self.assertIs(r_adv["ok"], True, r_adv)
        self.assertEqual(r_adv["result"]["receipt"]["status"], "FORMED")

        r_share = self._call(
            "v09_living_agency_act",
            payload={
                "action": "set_stance_shared",
                "stance_id": "st_tea_chat_1",
                "shared_with_partner": True,
            },
            operation_key="e2e-st-share-1",
        )
        self.assertIs(r_share["ok"], True, r_share)

    def test_03_world_commitments_read_and_all_4_subject_actions_via_phone_tool(self) -> None:
        # 1. Zero-write subject read
        snap_before = live_proof._snapshot_tree(self.inst.root)
        rd0 = self._call("v09_world_commitments_read")
        self.assertIs(rd0["ok"], True, rd0)
        self.assertEqual(rd0["http_status"], 200)
        self.assertEqual(rd0["result"]["view"], "subject")
        self.assertEqual(rd0["result"]["llm_calls"], 0)
        self.assertIs(rd0["result"]["zero_write"], True)
        self.assertEqual(live_proof._snapshot_tree(self.inst.root), snap_before)

        # 2. create_commitment (BOOK_OR_ITEM_RESERVATION)
        r_create = self._call(
            "v09_world_commitments_act",
            payload={
                "action": "create_commitment",
                "kind": "BOOK_OR_ITEM_RESERVATION",
                "venue_id": "bookstore",
                "title_cn": "旧港木版画图录",
                "arrival_due_at": T_PLUS_2H_ISO,
                "hold_hours": 48,
                "current_weather": "clear",
            },
            operation_key="e2e-wc-create-1",
        )
        self.assertIs(r_create["ok"], True, r_create)
        self.assertEqual(r_create["status"], "applied")
        self.assertEqual(r_create["result"]["llm_calls"], 0)
        cid = r_create["result"]["receipt"]["commitment"]["commitment_id"]

        # 3. advance_due_commitments -> reservation transitions to ARRIVED_HOLDING
        r_adv = self._call(
            "v09_world_commitments_act",
            payload={
                "action": "advance_due_commitments",
                "current_weather": "clear",
                "target_now_iso": T_PLUS_25H_ISO,
            },
            operation_key="e2e-wc-adv-1",
        )
        self.assertIs(r_adv["ok"], True, r_adv)
        self.assertEqual(r_adv["result"]["llm_calls"], 0)

        # 4. contact_npc_or_venue -> pickup_reservation
        self.inst.set_clock(T_PLUS_25H_ISO)
        r_contact = self._call(
            "v09_world_commitments_act",
            payload={
                "action": "contact_npc_or_venue",
                "venue_id": "bookstore",
                "contact_action": "pickup_reservation",
                "commitment_id": cid,
            },
            operation_key="e2e-wc-pickup-1",
        )
        self.assertIs(r_contact["ok"], True, r_contact)
        self.assertEqual(r_contact["result"]["receipt"]["commitment"]["state"], "PICKED_UP")
        self.assertEqual(r_contact["result"]["llm_calls"], 0)

        # 5. generate_world_content_batch (deterministic offline llm_calls == 0)
        r_gen = self._call(
            "v09_world_commitments_act",
            payload={
                "action": "generate_world_content_batch",
                "store_id": "home_goods_store",
                "trigger_reason": "SUBJECT_BROWSE_RESTOCK",
                "force_cooldown_bypass": True,
            },
            operation_key="e2e-wc-gen-1",
        )
        self.assertIs(r_gen["ok"], True, r_gen)
        self.assertEqual(r_gen["result"]["llm_calls"], 0)
        self.assertEqual(r_gen["result"]["receipt"]["llm_calls"], 0)
        self.assertGreaterEqual(len(r_gen["result"]["data"]["dynamic_catalog"]["items"]), 1)

        # 6. Verify fresh subject read reflects picked-up commitment and dynamic catalog
        rd_final = self._call("v09_world_commitments_read")
        self.assertIs(rd_final["ok"], True, rd_final)
        self.assertEqual(rd_final["result"]["llm_calls"], 0)
        picked = next(
            c for c in rd_final["result"]["data"]["commitments"] if c["commitment_id"] == cid
        )
        self.assertEqual(picked["state"], "PICKED_UP")

    def test_04_security_impersonation_memory_policy_write_and_capability_gates(self) -> None:
        # 1. Partner impersonation on live server produces PARTNER_IMPERSONATION_REFUSED with 0 disk writes
        snap0 = live_proof._snapshot_tree(self.inst.root)
        for tool_act, sub_acts in (
            ("v09_living_agency_act", sorted(EXPECTED_LA_PARTNER_5)),
            ("v09_world_commitments_act", sorted(EXPECTED_WC_PARTNER_3)),
        ):
            for sub in sub_acts:
                res = self._call(
                    tool_act,
                    payload={"action": sub, "venue_id": "bookstore", "store_id": "home_goods_store"},
                    operation_key=f"live-imp-{sub}",
                )
                self.assertIs(res["ok"], False, res)
                self.assertEqual(res["reason_code"], "PARTNER_IMPERSONATION_REFUSED")
        self.assertEqual(live_proof._snapshot_tree(self.inst.root), snap0)

        # 2. memory_tool_policy != "unrestricted" with memory=True and private_artifacts=False refuses all 4
        self._write_profile(
            write_enabled=True,
            private_artifacts=False,
            memory=True,
            memory_tool_policy="restricted",
        )
        snap_mem = live_proof._snapshot_tree(self.inst.root)
        for act, extra in (
            ("v09_living_agency_read", {}),
            (
                "v09_living_agency_act",
                {
                    "payload": {
                        "action": "create_custom_category",
                        "title_cn": "被受限门控拦截的架子",
                        "scope_kinds": ["note"],
                    }
                },
            ),
            ("v09_world_commitments_read", {}),
            (
                "v09_world_commitments_act",
                {
                    "payload": {
                        "action": "advance_due_commitments",
                        "current_weather": "clear",
                    }
                },
            ),
        ):
            res = self._call(act, **extra)
            self.assertIs(res["ok"], False, res)
            self.assertEqual(res["reason_code"], "PHONE_PRIVATE_ARTIFACTS_NOT_PERMITTED")
        self.assertEqual(live_proof._snapshot_tree(self.inst.root), snap_mem)

        # 3. Same restricted memory with private_artifacts=True succeeds
        self._write_profile(
            write_enabled=True,
            private_artifacts=True,
            memory=True,
            memory_tool_policy="restricted",
        )
        self.assertIs(self._call("v09_living_agency_read")["ok"], True)
        self.assertIs(self._call("v09_world_commitments_read")["ok"], True)

        # 4. memory_tool_policy == "unrestricted" with private_artifacts=False succeeds
        self._write_profile(
            write_enabled=True,
            private_artifacts=False,
            memory=True,
            memory_tool_policy="unrestricted",
        )
        self.assertIs(self._call("v09_living_agency_read")["ok"], True)
        self.assertIs(self._call("v09_world_commitments_read")["ok"], True)

        # 5. write_enabled=False blocks both v09_*_act with PHONE_WRITE_DISABLED while reads stay open
        self._write_profile(write_enabled=False, private_artifacts=True)
        snap_ro = live_proof._snapshot_tree(self.inst.root)
        self.assertIs(self._call("v09_living_agency_read")["ok"], True)
        self.assertIs(self._call("v09_world_commitments_read")["ok"], True)
        w1 = self._call(
            "v09_living_agency_act",
            payload={"action": "create_custom_category", "title_cn": "禁写测试", "scope_kinds": ["note"]},
        )
        w2 = self._call(
            "v09_world_commitments_act",
            payload={"action": "advance_due_commitments", "current_weather": "clear"},
        )
        self.assertEqual(w1["reason_code"], "PHONE_WRITE_DISABLED")
        self.assertEqual(w2["reason_code"], "PHONE_WRITE_DISABLED")
        self.assertEqual(live_proof._snapshot_tree(self.inst.root), snap_ro)

        # 6. Missing capability fails closed on v09_*_act with PHONE_CAPABILITY_NOT_CONFIGURED
        self._write_profile(write_enabled=True, private_artifacts=True, capability_mode="none")
        snap_nocap = live_proof._snapshot_tree(self.inst.root)
        nc1 = self._call(
            "v09_living_agency_act",
            payload={"action": "create_custom_category", "title_cn": "无凭据测试", "scope_kinds": ["note"]},
        )
        nc2 = self._call(
            "v09_world_commitments_act",
            payload={"action": "advance_due_commitments", "current_weather": "clear"},
        )
        self.assertEqual(nc1["reason_code"], "PHONE_CAPABILITY_NOT_CONFIGURED")
        self.assertEqual(nc2["reason_code"], "PHONE_CAPABILITY_NOT_CONFIGURED")
        self.assertEqual(live_proof._snapshot_tree(self.inst.root), snap_nocap)

        # 7. Direct phone.capability token unlocks both v09 subject read and write
        self._write_profile(write_enabled=True, private_artifacts=True, capability_mode="direct")
        rd_direct = self._call("v09_living_agency_read")
        self.assertIs(rd_direct["ok"], True, rd_direct)
        self.assertEqual(rd_direct["result"]["view"], "subject")
        wr_direct = self._call(
            "v09_living_agency_act",
            payload={"action": "create_custom_category", "title_cn": "直传凭据书架", "scope_kinds": ["note"]},
            operation_key="e2e-direct-cap-1",
        )
        self.assertIs(wr_direct["ok"], True, wr_direct)


if __name__ == "__main__":
    unittest.main()

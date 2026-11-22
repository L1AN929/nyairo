#!/usr/bin/env python3
"""Contract tests for RepresentationBinding, PermissionLease + CombinedVeto, and Forget/DeleteCase."""

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Add paths for life scripts and component native code
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "components" / "life" / "scripts"))
sys.path.insert(0, str(ROOT / "components" / "native"))

import v02_representation_lease_forget as gov


def _snapshot_tree(root: Path) -> list[tuple[str, int, int, str]]:
    if not root.exists():
        return []
    out: list[tuple[str, int, int, str]] = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        st = p.lstat()
        if p.is_file():
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            out.append((rel, st.st_size, st.st_mtime_ns, digest))
        else:
            out.append((rel + "/", 0, st.st_mtime_ns, "DIR"))
    return out


class TestV02RepresentationLeaseForget(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v02_gov_test_")
        self.root = Path(self._tmp.name)
        self.day_root = self.root / "day"
        self.control_root = self.root / "control"
        self.workspace = self.root / "workspace"
        self.day_root.mkdir()
        self.control_root.mkdir()
        self.workspace.mkdir()
        gov._ensure_control_ledger_locked(self.day_root, self.control_root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_01_representation_states_unbound_current_missing_diverged_and_rebuild(self) -> None:
        """Verify UNBOUND -> CURRENT -> MISSING -> rebuild -> DIVERGED refusal."""
        art_id = "art_states_1"
        status, resp = gov.create_managed_artifact(
            self.day_root,
            artifact_id=art_id,
            controller_principal_ref="owner_1",
            subject_id="subj_1",
            initial_body="# Canonical V1\n",
            operation_id="op_s1_create",
            visibility="shared",
            control_root=self.control_root,
        )
        self.assertEqual(status, 200)
        self.assertEqual(resp["llm_calls"], 0)

        # UNBOUND before bind_representation
        status, resp = gov.inspect_representation(self.day_root, artifact_id=art_id)
        self.assertEqual(status, 200)
        self.assertEqual(resp["representation_health"], gov.REP_UNBOUND)
        self.assertEqual(resp["llm_calls"], 0)

        # CURRENT after bind_representation
        status, resp = gov.bind_representation(
            self.day_root,
            artifact_id=art_id,
            workspace_root=self.workspace,
            workspace_rel_path="notes/state.md",
            operation_id="op_s1_bind",
        )
        self.assertEqual(status, 200)
        self.assertEqual(resp["representation_health"], gov.REP_CURRENT)

        md_path = self.workspace / "notes" / "state.md"
        self.assertTrue(md_path.is_file())

        # MISSING when workspace .md is deleted
        md_path.unlink()
        status, resp = gov.inspect_representation(self.day_root, artifact_id=art_id)
        self.assertEqual(status, 200)
        self.assertEqual(resp["representation_health"], gov.REP_MISSING)

        # Rebuild restores MISSING projection to CURRENT
        status, resp = gov.rebuild_projection_from_canonical(
            self.day_root,
            artifact_id=art_id,
            workspace_root=self.workspace,
            operation_id="op_s1_rebuild",
        )
        self.assertEqual(status, 200)
        self.assertEqual(resp["representation_health"], gov.REP_CURRENT)
        self.assertEqual(md_path.read_text(encoding="utf-8"), "# Canonical V1\n")

        # External edit transitions live health to DIVERGED; rebuild refuses to overwrite
        md_path.write_text("# External Edit\n", encoding="utf-8")
        status, resp = gov.inspect_representation(self.day_root, artifact_id=art_id)
        self.assertEqual(status, 200)
        self.assertEqual(resp["representation_health"], gov.REP_DIVERGED)
        self.assertTrue(resp["external_edit_detected"])

        status, resp = gov.rebuild_projection_from_canonical(
            self.day_root,
            artifact_id=art_id,
            workspace_root=self.workspace,
            operation_id="op_s1_rebuild_blocked",
        )
        self.assertEqual(status, 409)
        self.assertEqual(resp["code"], gov.CODE_BLOCKED_CONFLICT)
        self.assertEqual(md_path.read_text(encoding="utf-8"), "# External Edit\n")

    def test_02_external_edit_observation_and_no_auto_overwrite(self) -> None:
        """External edit produces ExternalEditObservation without overwriting canonical or disk .md."""
        art_id = "art_obs_1"
        _, created = gov.create_managed_artifact(
            self.day_root,
            artifact_id=art_id,
            controller_principal_ref="owner_1",
            subject_id="subj_1",
            initial_body="# Canonical Body\n",
            operation_id="op_obs_c1",
            visibility="private",
            control_root=self.control_root,
        )
        v1 = created["version_id"]
        gov.bind_representation(
            self.day_root,
            artifact_id=art_id,
            workspace_root=self.workspace,
            workspace_rel_path="obs.md",
            operation_id="op_obs_b1",
        )
        md_path = self.workspace / "obs.md"
        md_path.write_text("# Edited Outside\n", encoding="utf-8")

        status, obs_resp = gov.observe_external_edit(
            self.day_root,
            artifact_id=art_id,
            workspace_root=self.workspace,
            operation_id="op_obs_1",
            actor_ref="editor_ext",
            trusted_actor_identity=False,
        )
        self.assertEqual(status, 200)
        self.assertEqual(obs_resp["representation_health"], gov.REP_DIVERGED)
        obs = obs_resp["observation"]
        self.assertEqual(obs["kind"], "ExternalEditObservation")
        self.assertEqual(obs["attribution"], "EXTERNAL_UNVERIFIED")
        self.assertFalse(obs["attributed_as_ta_action_commit"])

        # Canonical head and body remain unchanged; disk file not overwritten
        status, read_resp = gov.read_with_barrier(
            self.day_root,
            target_kind="artifact",
            target_id=art_id,
            has_subject_capability=True,
            control_root=self.control_root,
        )
        self.assertEqual(status, 200)
        self.assertEqual(read_resp["version_id"], v1)
        self.assertEqual(read_resp["content"], "# Canonical Body\n")
        self.assertEqual(md_path.read_text(encoding="utf-8"), "# Edited Outside\n")

        # Partner perspective cannot inspect private artifact
        status, partner_resp = gov.inspect_representation(
            self.day_root,
            artifact_id=art_id,
            has_subject_capability=False,
        )
        self.assertEqual(status, 200)
        self.assertFalse(partner_resp["visible"])
        self.assertEqual(partner_resp["reason"], "PARTNER_PERSPECTIVE_REDACTED")

    def test_03_action_commit_conflict_409_and_explicit_import_advances_head(self) -> None:
        """CAS ACTION_COMMIT over stale head -> 409; import over moved head -> 409 BLOCKED_CONFLICT; matching base -> EXPLICIT_IMPORT advances head."""
        art_id = "art_import_1"
        _, resp = gov.create_managed_artifact(
            self.day_root,
            artifact_id=art_id,
            controller_principal_ref="owner_1",
            subject_id="subj_1",
            initial_body="# Content V1\n",
            operation_id="op_imp_c1",
            control_root=self.control_root,
        )
        v1 = resp["version_id"]
        gov.bind_representation(
            self.day_root,
            artifact_id=art_id,
            workspace_root=self.workspace,
            workspace_rel_path="import.md",
            operation_id="op_imp_b1",
        )

        # ACTION_COMMIT advances head to V2 without overwriting workspace .md
        status, resp_v2 = gov.commit_artifact_version(
            self.day_root,
            artifact_id=art_id,
            expected_head=v1,
            body="# Content V2\n",
            operation_id="op_imp_c2",
            control_root=self.control_root,
        )
        self.assertEqual(status, 200)
        v2 = resp_v2["version_id"]
        self.assertNotEqual(v1, v2)

        # Stale ACTION_COMMIT against V1 returns 409 HEAD_CONFLICT
        status, stale_commit = gov.commit_artifact_version(
            self.day_root,
            artifact_id=art_id,
            expected_head=v1,
            body="# Stale V2\n",
            operation_id="op_imp_c2_stale",
            control_root=self.control_root,
        )
        self.assertEqual(status, 409)
        self.assertEqual(stale_commit["code"], "HEAD_CONFLICT")

        # External edit on DIVERGED .md with base_projection_version=V1 fails closed 409 BLOCKED_CONFLICT
        md_path = self.workspace / "import.md"
        md_path.write_text("# Diverged External Edit\n", encoding="utf-8")
        gov.observe_external_edit(
            self.day_root,
            artifact_id=art_id,
            workspace_root=self.workspace,
            operation_id="op_imp_o1",
            actor_ref="owner_1",
            trusted_actor_identity=True,
        )
        status, conflict = gov.explicit_import_representation(
            self.day_root,
            artifact_id=art_id,
            workspace_root=self.workspace,
            base_projection_version=v1,
            actor_ref="owner_1",
            operation_id="op_imp_fail",
            control_root=self.control_root,
        )
        self.assertEqual(status, 409)
        self.assertEqual(conflict["code"], gov.CODE_BLOCKED_CONFLICT)
        self.assertFalse(conflict["silent_merge_attempted"])

        # Unpermitted / untrusted explicit import returns 403
        status, denied = gov.explicit_import_representation(
            self.day_root,
            artifact_id=art_id,
            workspace_root=self.workspace,
            base_projection_version=v2,
            actor_ref="untrusted",
            operation_id="op_imp_untrusted",
            trusted_actor_identity=False,
            control_root=self.control_root,
        )
        self.assertEqual(status, 403)
        self.assertEqual(denied["code"], "EXPLICIT_IMPORT_PERMISSION_REQUIRED")

        # Explicit import anchored on current_head (V2) commits EXPLICIT_IMPORT and restores CURRENT
        status, imported = gov.explicit_import_representation(
            self.day_root,
            artifact_id=art_id,
            workspace_root=self.workspace,
            base_projection_version=v2,
            actor_ref="owner_1",
            operation_id="op_imp_ok",
            control_root=self.control_root,
        )
        self.assertEqual(status, 200)
        self.assertEqual(imported["code"], "EXPLICIT_IMPORT_COMMITTED")
        self.assertEqual(imported["source_kind"], gov.SOURCE_EXPLICIT_IMPORT)
        self.assertEqual(imported["representation_health"], gov.REP_CURRENT)
        v3 = imported["current_head"]
        self.assertNotEqual(v3, v2)

        status, read_v3 = gov.read_with_barrier(
            self.day_root,
            target_kind="artifact",
            target_id=art_id,
            control_root=self.control_root,
        )
        self.assertEqual(status, 200)
        self.assertEqual(read_v3["version_id"], v3)
        self.assertEqual(read_v3["content"], "# Diverged External Edit\n")

    def test_04_workspace_path_traversal_symlink_and_privacy_erase_shell(self) -> None:
        """Reject path traversal/symlinks on bind; privacy-erase keeps shell and retires version_id."""
        art_id = "art_sec_1"
        _, created = gov.create_managed_artifact(
            self.day_root,
            artifact_id=art_id,
            controller_principal_ref="owner_1",
            subject_id="subj_1",
            initial_body="# Secret Draft\n",
            operation_id="op_sec_c1",
            title="Sensitive Draft Title",
            explicit_version_id="ver_fixed_001",
            control_root=self.control_root,
        )
        self.assertEqual(created["version_id"], "ver_fixed_001")

        # Traversal rejected
        status, trav = gov.bind_representation(
            self.day_root,
            artifact_id=art_id,
            workspace_root=self.workspace,
            workspace_rel_path="../escape.md",
            operation_id="op_sec_trav",
        )
        self.assertEqual(status, 403)
        self.assertIn(trav["code"], ("PATH_TRAVERSAL_FORBIDDEN", "PATH_ESCAPE_OR_SYMLINK_FORBIDDEN"))

        # Symlink rejected if OS supports symlinks
        outside_file = self.root / "outside.md"
        outside_file.write_text("outside", encoding="utf-8")
        sym_path = self.workspace / "link.md"
        try:
            os.symlink(outside_file, sym_path)
        except (OSError, NotImplementedError):
            sym_path = None
        if sym_path is not None:
            status, sym_resp = gov.bind_representation(
                self.day_root,
                artifact_id=art_id,
                workspace_root=self.workspace,
                workspace_rel_path="link.md",
                operation_id="op_sec_sym",
            )
            self.assertEqual(status, 403)
            self.assertEqual(sym_resp["code"], "PATH_ESCAPE_OR_SYMLINK_FORBIDDEN")

        # Privacy erase retains shell, sanitizes title, and forbids reusing erased version_id
        status, erased = gov.erase_version_payload_privacy(
            self.day_root,
            artifact_id=art_id,
            version_id="ver_fixed_001",
            operation_id="op_sec_erase",
            control_root=self.control_root,
        )
        self.assertEqual(status, 200)
        self.assertTrue(erased["identity_retained"])
        self.assertTrue(erased["payload_erased"])
        self.assertEqual(erased["title"], "[REDACTED_SHELL]")

        status, read_resp = gov.read_with_barrier(
            self.day_root,
            target_kind="artifact",
            target_id=art_id,
            control_root=self.control_root,
        )
        self.assertEqual(status, 200)
        self.assertEqual(read_resp["code"], "SHELL_ONLY_PAYLOAD_ERASED")
        self.assertEqual(read_resp["content"], "")

        status, reuse = gov.commit_artifact_version(
            self.day_root,
            artifact_id=art_id,
            expected_head="ver_fixed_001",
            body="reused",
            operation_id="op_sec_reuse",
            explicit_version_id="ver_fixed_001",
            control_root=self.control_root,
        )
        self.assertEqual(status, 409)
        self.assertEqual(reuse["code"], "ERASED_VERSION_ID_REUSE_FORBIDDEN")

    def test_05_ch14_scenario8_pre_dispatch_revocation_blocks_with_zero_deliveries(self) -> None:
        """Ch.14 Scenario 8: T1/TD pass, lease revoked before T2 -> 403 LEASE_REVOKED_BEFORE_DISPATCH & 0 deliveries."""
        grant_id = "grant_sc8"
        action_id = "act_sc8"
        payload_text = "outbound message payload"
        payload_hash = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()

        status, lease_resp = gov.issue_permission_lease(
            self.day_root,
            grant_id=grant_id,
            grantor="operator_admin",
            subject="subj_1",
            capability="DISCLOSE_MANAGED_ARTIFACT",
            scope="disclosure:partner",
            payload_binding=payload_hash,
            typed_target={"kind": "artifact", "id": "art_1"},
            audience="partner:local",
            operation_id="op_sc8_issue",
            control_root=self.control_root,
        )
        self.assertEqual(status, 200)
        self.assertEqual(lease_resp["llm_calls"], 0)

        # T1 read and TD reservation succeed
        status, t1 = gov.read_lease_t1(
            self.day_root,
            grant_id=grant_id,
            subject="subj_1",
            capability="DISCLOSE_MANAGED_ARTIFACT",
            target_id="art_1",
            audience="partner:local",
            payload_hash=payload_hash,
            control_root=self.control_root,
        )
        self.assertEqual(status, 200)
        self.assertTrue(t1["allowed"])

        status, td = gov.reserve_dispatch_td(
            self.day_root,
            grant_id=grant_id,
            logical_action_id=action_id,
            payload_hash=payload_hash,
            subject="subj_1",
            capability="DISCLOSE_MANAGED_ARTIFACT",
            target_id="art_1",
            audience="partner:local",
            operation_id="op_sc8_td",
            control_root=self.control_root,
        )
        self.assertEqual(status, 200)
        self.assertEqual(td["reservation_status"], gov.RES_RESERVED)

        # Revoke between TD and T2 dispatch
        status, rev = gov.revoke_permission_lease(
            self.day_root,
            grant_id=grant_id,
            actor_ref="operator_admin",
            operation_id="op_sc8_revoke",
            control_root=self.control_root,
        )
        self.assertEqual(status, 200)
        self.assertEqual(rev["revocation_seq"], 1)

        transport_calls: list[dict] = []

        def _transport(req):
            transport_calls.append(dict(req))
            return gov.OUTCOME_SUCCEEDED

        # T2 pre-dispatch check fails closed with 403 LEASE_REVOKED_BEFORE_DISPATCH and 0 deliveries
        status, disp = gov.execute_guarded_dispatch(
            self.day_root,
            grant_id=grant_id,
            logical_action_id=action_id,
            payload_hash=payload_hash,
            payload_text=payload_text,
            subject="subj_1",
            capability="DISCLOSE_MANAGED_ARTIFACT",
            target_id="art_1",
            audience="partner:local",
            operation_id="op_sc8_exec",
            transport_fn=_transport,
            control_root=self.control_root,
        )
        self.assertEqual(status, 403)
        self.assertEqual(disp["code"], gov.CODE_LEASE_REVOKED_BEFORE_DISPATCH)
        self.assertFalse(disp["delivered"])
        self.assertEqual(disp["sent_count"], 0)
        self.assertEqual(disp["action_sent_count"], 0)
        self.assertEqual(len(transport_calls), 0)

        _, gov_view = gov.project_governance_view(
            self.day_root,
            has_subject_capability=True,
            control_root=self.control_root,
        )
        self.assertEqual(gov_view["sent_count"], 0)

    def test_06_post_dispatch_revocation_never_falsifies_delivery_receipt(self) -> None:
        """Delivered dispatch followed by revocation preserves delivery receipt and prevents future sends."""
        grant_id = "grant_post_1"
        action_id = "act_post_1"
        payload_hash = hashlib.sha256(b"hello").hexdigest()

        gov.issue_permission_lease(
            self.day_root,
            grant_id=grant_id,
            grantor="operator_admin",
            subject="subj_1",
            capability="OUTBOUND_SEND",
            scope="outbound:partner",
            payload_binding=payload_hash,
            typed_target={"kind": "message", "id": "msg_1"},
            audience="partner:local",
            operation_id="op_post_issue",
            max_uses=2,
            control_root=self.control_root,
        )
        status, sent = gov.execute_guarded_dispatch(
            self.day_root,
            grant_id=grant_id,
            logical_action_id=action_id,
            payload_hash=payload_hash,
            payload_text="hello",
            subject="subj_1",
            capability="OUTBOUND_SEND",
            target_id="msg_1",
            audience="partner:local",
            operation_id="op_post_send",
            control_root=self.control_root,
        )
        self.assertEqual(status, 200)
        self.assertTrue(sent["delivered"])
        self.assertEqual(sent["sent_count"], 1)

        status, rev = gov.record_post_dispatch_revocation(
            self.day_root,
            grant_id=grant_id,
            logical_action_id=action_id,
            actor_ref="operator_admin",
            operation_id="op_post_rev",
            control_root=self.control_root,
        )
        self.assertEqual(status, 200)
        self.assertTrue(rev["cancel_attempted"])
        self.assertTrue(rev["already_delivered_preserved"])
        self.assertFalse(rev["delivery_retracted"])

        # Subsequent send attempt on same grant is blocked
        status, blocked = gov.execute_guarded_dispatch(
            self.day_root,
            grant_id=grant_id,
            logical_action_id="act_post_2",
            payload_hash=payload_hash,
            payload_text="hello",
            subject="subj_1",
            capability="OUTBOUND_SEND",
            target_id="msg_1",
            audience="partner:local",
            operation_id="op_post_send_2",
            control_root=self.control_root,
        )
        self.assertEqual(status, 403)
        self.assertEqual(blocked["code"], gov.CODE_LEASE_REVOKED_BEFORE_DISPATCH)
        self.assertEqual(blocked["sent_count"], 1)

    def test_07_combined_veto_intersection_and_p09_recheck(self) -> None:
        """CombinedVeto intersects Subject/Policy/Capability/Timing/Budget/WriterFence and ignores client verified flags."""
        # Unverified grantor refused
        status, bad_grantor = gov.issue_permission_lease(
            self.day_root,
            grant_id="grant_bad",
            grantor="user_text:please_send",
            subject="subj_1",
            capability="OUTBOUND_SEND",
            scope="s",
            payload_binding="h1",
            typed_target={"kind": "source", "id": "src_v"},
            audience="aud_1",
            operation_id="op_v_bad",
            control_root=self.control_root,
        )
        self.assertEqual(status, 403)
        self.assertEqual(bad_grantor["code"], "UNVERIFIED_GRANTOR_REFUSED")

        gov.ingest_source(
            self.day_root,
            source_id="src_v",
            text="shareable note",
            operation_id="op_v_ing",
            visibility="shared",
            control_root=self.control_root,
        )
        gov.issue_permission_lease(
            self.day_root,
            grant_id="grant_v",
            grantor="operator_admin",
            subject="subj_1",
            capability="OUTBOUND_SEND",
            scope="s",
            payload_binding="h1",
            typed_target={"kind": "source", "id": "src_v"},
            audience="aud_1",
            operation_id="op_v_issue",
            control_root=self.control_root,
        )

        # Delegation forbidden by default
        status, del_resp = gov.delegate_permission_lease(
            self.day_root,
            parent_grant_id="grant_v",
            new_grant_id="grant_v_child",
            actor_ref="subj_1",
            operation_id="op_v_del",
        )
        self.assertEqual(status, 403)
        self.assertEqual(del_resp["code"], "DELEGATION_FORBIDDEN")

        base_kwargs = {
            "grant_id": "grant_v",
            "subject": "subj_1",
            "capability": "OUTBOUND_SEND",
            "target_id": "src_v",
            "audience": "aud_1",
            "payload_hash": "h1",
            "source_id": "src_v",
            "expected_source_revision": 1,
            "control_root": self.control_root,
            # P09: client-supplied verified flags must be ignored when an owner denies
            "serialized_context": {"verified": True, "all_ok": True},
        }

        # Baseline passes
        status, ok_resp = gov.evaluate_combined_veto(self.day_root, **base_kwargs)
        self.assertEqual(status, 200)
        self.assertTrue(ok_resp["allowed"])

        # Capability / audience / subject mismatches fail
        status, bad_aud = gov.evaluate_combined_veto(self.day_root, **{**base_kwargs, "audience": "other"})
        self.assertEqual(status, 403)
        self.assertEqual(bad_aud["code"], "AUDIENCE_MISMATCH")

        # Policy / privacy / interaction boundary vetoes
        status, priv_deny = gov.evaluate_combined_veto(
            self.day_root, **base_kwargs, privacy_state={"allowed": False, "reason_code": "PRIVACY_VETO"}
        )
        self.assertEqual(status, 403)
        self.assertEqual(priv_deny["code"], "PRIVACY_VETO")

        status, bound_deny = gov.evaluate_combined_veto(
            self.day_root, **base_kwargs, boundary_state={"allowed": False, "reason_code": "BOUNDARY_VETO"}
        )
        self.assertEqual(status, 403)
        self.assertEqual(bound_deny["code"], "BOUNDARY_VETO")

        # Quiet hours defer unless urgent_authorized=True
        status, quiet = gov.evaluate_combined_veto(
            self.day_root, **base_kwargs, timing_state={"in_quiet_hours": True, "urgent_authorized": False}
        )
        self.assertEqual(status, 409)
        self.assertEqual(quiet["code"], "DEFERRED_TIMING_WINDOW")

        status, urgent_ok = gov.evaluate_combined_veto(
            self.day_root, **base_kwargs, timing_state={"in_quiet_hours": True, "urgent_authorized": True}
        )
        self.assertEqual(status, 200)

        # Budget insufficient -> 402
        status, low_budget = gov.evaluate_combined_veto(
            self.day_root, **base_kwargs, budget_state={"remaining": 0.5, "required_cost": 2.0}
        )
        self.assertEqual(status, 402)
        self.assertEqual(low_budget["code"], "BUDGET_INSUFFICIENT")

        # Stale writer epoch -> 409
        status, stale_writer = gov.evaluate_combined_veto(self.day_root, **base_kwargs, writer_epoch=0)
        self.assertEqual(status, 409)
        self.assertEqual(stale_writer["code"], "STALE_WRITER_EPOCH")

        # Source revision bump causes SOURCE_REVISION_CHANGED
        gov.ingest_source(
            self.day_root,
            source_id="src_v",
            text="updated note",
            operation_id="op_v_ing2",
            visibility="shared",
            control_root=self.control_root,
        )
        status, rev_changed = gov.evaluate_combined_veto(self.day_root, **base_kwargs)
        self.assertEqual(status, 409)
        self.assertEqual(rev_changed["code"], gov.CODE_SOURCE_REVISION_CHANGED)

    def test_08_unknown_outcome_retains_one_shot_lease_until_proofs(self) -> None:
        """UNKNOWN outcome retains one-shot lease slot; competing action rejected; release requires both proofs."""
        grant_id = "grant_unk_1"
        payload_hash = hashlib.sha256(b"one_shot").hexdigest()
        gov.issue_permission_lease(
            self.day_root,
            grant_id=grant_id,
            grantor="operator_admin",
            subject="subj_1",
            capability="OUTBOUND_SEND",
            scope="outbound:one_shot",
            payload_binding=payload_hash,
            typed_target={"kind": "message", "id": "msg_u1"},
            audience="partner:local",
            max_uses=1,
            operation_id="op_u_issue",
            control_root=self.control_root,
        )

        # Transport times out / raises -> outcome=OUTCOME_UNKNOWN -> RES_UNKNOWN_HELD
        def _boom(_req):
            raise TimeoutError("network timeout")

        status, unk_resp = gov.execute_guarded_dispatch(
            self.day_root,
            grant_id=grant_id,
            logical_action_id="act_u1",
            payload_hash=payload_hash,
            payload_text="one_shot",
            subject="subj_1",
            capability="OUTBOUND_SEND",
            target_id="msg_u1",
            audience="partner:local",
            operation_id="op_u_exec1",
            transport_fn=_boom,
            control_root=self.control_root,
        )
        self.assertEqual(status, 200)
        self.assertFalse(unk_resp["delivered"])
        self.assertEqual(unk_resp["outcome"], gov.OUTCOME_UNKNOWN)
        self.assertEqual(unk_resp["reservation_status"], gov.RES_UNKNOWN_HELD)
        self.assertEqual(unk_resp["sent_count"], 0)

        # Competing logical_action_id is blocked with 409 LEASE_EXHAUSTED
        status, compete = gov.execute_guarded_dispatch(
            self.day_root,
            grant_id=grant_id,
            logical_action_id="act_u2",
            payload_hash=payload_hash,
            payload_text="one_shot",
            subject="subj_1",
            capability="OUTBOUND_SEND",
            target_id="msg_u1",
            audience="partner:local",
            operation_id="op_u_exec2",
            control_root=self.control_root,
        )
        self.assertEqual(status, 409)
        self.assertEqual(compete["code"], "LEASE_EXHAUSTED")

        # Settling NOT_EXECUTED without both proofs keeps RES_UNKNOWN_HELD
        status, no_proof = gov.settle_dispatch_outcome(
            self.day_root,
            grant_id=grant_id,
            logical_action_id="act_u1",
            outcome=gov.OUTCOME_NOT_EXECUTED,
            operation_id="op_u_settle_noproof",
            writer_fenced_proof=True,
            store_integrity_proof=False,
            control_root=self.control_root,
        )
        self.assertEqual(status, 200)
        self.assertFalse(no_proof["released"])
        self.assertEqual(no_proof["reservation_status"], gov.RES_UNKNOWN_HELD)

        # Settling NOT_EXECUTED with both proofs releases slot so act_u2 can execute
        status, with_proof = gov.settle_dispatch_outcome(
            self.day_root,
            grant_id=grant_id,
            logical_action_id="act_u1",
            outcome=gov.OUTCOME_NOT_EXECUTED,
            operation_id="op_u_settle_proof",
            writer_fenced_proof=True,
            store_integrity_proof=True,
            control_root=self.control_root,
        )
        self.assertEqual(status, 200)
        self.assertTrue(with_proof["released"])
        self.assertEqual(with_proof["reservation_status"], gov.RES_RELEASED)

        status, retry_ok = gov.execute_guarded_dispatch(
            self.day_root,
            grant_id=grant_id,
            logical_action_id="act_u2",
            payload_hash=payload_hash,
            payload_text="one_shot",
            subject="subj_1",
            capability="OUTBOUND_SEND",
            target_id="msg_u1",
            audience="partner:local",
            operation_id="op_u_exec2_ok",
            control_root=self.control_root,
        )
        self.assertEqual(status, 200)
        self.assertTrue(retry_ok["delivered"])
        self.assertEqual(retry_ok["sent_count"], 1)

    def test_09_ambiguous_forget_hold_zero_secret_echo_and_72h_no_auto_release(self) -> None:
        """Ambiguous forget -> HOLD_PENDING_SCOPE_CLARIFICATION, zero secret echo, no timeout release even after +72h."""
        secret = "SUPER_SECRET_PASSWORD_991122"
        raw_secret_sha = hashlib.sha256(secret.encode("utf-8")).hexdigest()

        gov.ingest_source(
            self.day_root,
            source_id="src_hold_1",
            text=secret,
            operation_id="op_h_ing",
            control_root=self.control_root,
        )
        gov.register_derivation(
            self.day_root,
            node_id="node_ep_hold",
            surface="episode",
            source_refs=[{"source_id": "src_hold_1"}],
            content=f"derived from {secret}",
            generation=1,
            operation_id="op_h_der",
            control_root=self.control_root,
        )

        status, hold_resp = gov.submit_forget_command(
            self.day_root,
            command_text="忘掉刚才那段",
            operation_id="op_h_forget",
            recent_source_ids=["src_hold_1"],
            control_root=self.control_root,
        )
        self.assertEqual(status, 200)
        self.assertEqual(hold_resp["code"], gov.CASE_HOLD_PENDING)
        self.assertEqual(hold_resp["status"], gov.CASE_HOLD_PENDING)
        self.assertTrue(hold_resp["read_blocked"])
        self.assertTrue(hold_resp["outbound_blocked"])
        self.assertTrue(hold_resp["never_auto_release_on_timeout"])

        # Zero raw secret or raw unsalted sha256 echo in response/prompt/holds
        serialized_resp = json.dumps(hold_resp, ensure_ascii=False)
        self.assertNotIn(secret, serialized_resp)
        self.assertNotIn(raw_secret_sha, serialized_resp)
        self.assertNotIn("src_hold_1", hold_resp["clarification_prompt"])

        state_text = (self.day_root / "governance" / "v02_representation_lease_forget_state.json").read_text(
            encoding="utf-8"
        )
        state_doc = json.loads(state_text)
        holds_text = json.dumps(state_doc["holds"], ensure_ascii=False)
        self.assertNotIn(secret, holds_text)
        self.assertNotIn(raw_secret_sha, holds_text)

        # +72h and +96h timeout check: hold NEVER auto-releases
        for elapsed in (72 * 3600, 96 * 3600):
            status, st_resp = gov.check_hold_status(
                self.day_root,
                hold_id=hold_resp["hold_id"],
                elapsed_seconds=elapsed,
            )
            self.assertEqual(status, 200)
            self.assertEqual(st_resp["status"], gov.CASE_HOLD_PENDING)
            self.assertTrue(st_resp["read_blocked"])
            self.assertTrue(st_resp["outbound_blocked"])
            self.assertTrue(st_resp["never_auto_release_on_timeout"])

        # Read barrier blocks both normal and history queries for source and derived node
        for mode in ("normal", "history"):
            st_src, _ = gov.read_with_barrier(
                self.day_root,
                target_kind="source",
                target_id="src_hold_1",
                query_mode=mode,
                control_root=self.control_root,
            )
            self.assertEqual(st_src, 403)
            st_der, _ = gov.read_with_barrier(
                self.day_root,
                target_kind="derivation",
                target_id="node_ep_hold",
                query_mode=mode,
                control_root=self.control_root,
            )
            self.assertEqual(st_der, 403)

    def test_10_hold_retract_only_and_explicit_cancel_hold(self) -> None:
        """retract_only keeps hold blocked; explicit_cancel_hold restores read access."""
        gov.ingest_source(
            self.day_root,
            source_id="src_ret_1",
            text="preference A",
            operation_id="op_r_ing1",
            control_root=self.control_root,
        )
        _, h1 = gov.submit_forget_command(
            self.day_root,
            command_text="forget that part",
            operation_id="op_r_f1",
            recent_source_ids=["src_ret_1"],
            control_root=self.control_root,
        )
        status, ret = gov.resolve_hold_case(
            self.day_root,
            hold_id=h1["hold_id"],
            resolution="retract_only",
            operation_id="op_r_res1",
            control_root=self.control_root,
        )
        self.assertEqual(status, 200)
        self.assertEqual(ret["status"], gov.CASE_RETRACTED)
        st_read, _ = gov.read_with_barrier(
            self.day_root, target_kind="source", target_id="src_ret_1", control_root=self.control_root
        )
        self.assertEqual(st_read, 403)

        # Second source with explicit_cancel_hold
        gov.ingest_source(
            self.day_root,
            source_id="src_cancel_1",
            text="keepable note",
            operation_id="op_r_ing2",
            control_root=self.control_root,
        )
        _, h2 = gov.submit_forget_command(
            self.day_root,
            command_text="forget earlier",
            operation_id="op_r_f2",
            recent_source_ids=["src_cancel_1"],
            control_root=self.control_root,
        )
        status, can = gov.resolve_hold_case(
            self.day_root,
            hold_id=h2["hold_id"],
            resolution="explicit_cancel_hold",
            operation_id="op_r_res2",
            control_root=self.control_root,
        )
        self.assertEqual(status, 200)
        self.assertEqual(can["status"], gov.CASE_CANCELLED)
        st_read2, ok_read2 = gov.read_with_barrier(
            self.day_root, target_kind="source", target_id="src_cancel_1", control_root=self.control_root
        )
        self.assertEqual(st_read2, 200)
        self.assertEqual(ok_read2["content"], "keepable note")

    def test_11_multihop_derivation_6_surfaces_hmac_tombstones_zero_leak(self) -> None:
        """Multi-hop DAG across all 6 surfaces erased with HMAC-SHA256 opaque tombstones and 0 plaintext leak."""
        secret = "PRIVATE_DIARY_SECRET_774411"
        raw_sha = hashlib.sha256(secret.encode("utf-8")).hexdigest()
        src_id = "src_dag_1"

        gov.ingest_source(
            self.day_root,
            source_id=src_id,
            text=secret,
            operation_id="op_dag_ing",
            generation=1,
            control_root=self.control_root,
        )

        # Multi-hop chain covering all 6 DERIVATION_SURFACES:
        # episode -> period -> p12_narrative -> vector_index -> belief -> drafts
        chain = [
            ("n_ep", "episode", [{"source_id": src_id}], ()),
            ("n_per", "period", (), ("n_ep",)),
            ("n_p12", "p12_narrative", (), ("n_per",)),
            ("n_vec", "vector_index", (), ("n_p12",)),
            ("n_bel", "belief", (), ("n_vec",)),
            ("n_dft", "drafts", (), ("n_bel",)),
        ]
        for idx, (nid, surf, srefs, parents) in enumerate(chain):
            st, reg = gov.register_derivation(
                self.day_root,
                node_id=nid,
                surface=surf,
                source_refs=srefs,
                parent_node_ids=parents,
                content=f"{surf}: {secret}",
                generation=1,
                operation_id=f"op_dag_reg_{idx}",
                control_root=self.control_root,
            )
            self.assertEqual(st, 200)
            self.assertEqual(reg["root_set"], [src_id])

        # Ambiguous hold followed by confirm_delete
        _, hold_resp = gov.submit_forget_command(
            self.day_root,
            command_text="忘掉刚才那句",
            operation_id="op_dag_hold",
            recent_source_ids=[src_id],
            control_root=self.control_root,
        )
        status, del_resp = gov.resolve_hold_case(
            self.day_root,
            hold_id=hold_resp["hold_id"],
            resolution="confirm_delete",
            operation_id="op_dag_del",
            control_root=self.control_root,
        )
        self.assertEqual(status, 200)
        self.assertEqual(del_resp["status"], gov.CASE_COMPLETE)
        self.assertTrue(del_resp["complete"])
        self.assertTrue(all(d.startswith("tomb_hmac:") for d in del_resp["tombstone_digests"]))
        for surf, nid in zip(gov.DERIVATION_SURFACES, [c[0] for c in chain]):
            self.assertIn(nid, del_resp["surface_acks"][surf])

        # Verify zero plaintext / zero raw secret sha256 leak on disk
        state_raw = (self.day_root / "governance" / "v02_representation_lease_forget_state.json").read_text(
            encoding="utf-8"
        )
        ctrl_raw = (self.control_root / "control_authority" / "v02_control_highwater_ledger.json").read_text(
            encoding="utf-8"
        )
        self.assertNotIn(secret, state_raw)
        self.assertNotIn(raw_sha, state_raw)
        self.assertNotIn(secret, ctrl_raw)
        self.assertNotIn(raw_sha, ctrl_raw)
        self.assertNotIn(src_id, json.dumps(json.loads(ctrl_raw)["tombstones"]))

        # ReadBarrier blocks source and all 6 derived nodes under normal & history queries
        for mode in ("normal", "history"):
            st_s, _ = gov.read_with_barrier(
                self.day_root, target_kind="source", target_id=src_id, query_mode=mode, control_root=self.control_root
            )
            self.assertEqual(st_s, 403)
            for nid, _, _, _ in chain:
                st_n, _ = gov.read_with_barrier(
                    self.day_root,
                    target_kind="derivation",
                    target_id=nid,
                    query_mode=mode,
                    control_root=self.control_root,
                )
                self.assertEqual(st_n, 403)

    def test_12_waiting_retention_and_acknowledge_copies(self) -> None:
        """Unreachable copies enter WAITING_RETENTION while live read barrier is active; ack clears to COMPLETE."""
        gov.ingest_source(
            self.day_root,
            source_id="src_ret_copy",
            text="sensitive copy payload",
            operation_id="op_wc_ing",
            control_root=self.control_root,
        )
        status, f_resp = gov.execute_explicit_forget(
            self.day_root,
            source_ids=["src_ret_copy"],
            operation_id="op_wc_del",
            unreachable_copies=["offline_usb_1", "archive_node_2"],
            control_root=self.control_root,
        )
        self.assertEqual(status, 200)
        self.assertEqual(f_resp["status"], gov.CASE_WAITING_RETENTION)
        self.assertFalse(f_resp["complete"])
        self.assertEqual(f_resp["remaining_unreachable_copies"], ["archive_node_2", "offline_usb_1"])

        # Live read barrier is already active immediately
        st_read, _ = gov.read_with_barrier(
            self.day_root, target_kind="source", target_id="src_ret_copy", control_root=self.control_root
        )
        self.assertEqual(st_read, 403)

        # Acknowledge first copy -> still WAITING_RETENTION
        status, ack1 = gov.acknowledge_retention_copy(
            self.day_root,
            case_id=f_resp["case_id"],
            copy_id="offline_usb_1",
            operation_id="op_wc_ack1",
        )
        self.assertEqual(status, 200)
        self.assertEqual(ack1["status"], gov.CASE_WAITING_RETENTION)
        self.assertFalse(ack1["complete"])
        self.assertEqual(ack1["remaining_unreachable_copies"], ["archive_node_2"])

        # Acknowledge last copy -> COMPLETE
        status, ack2 = gov.acknowledge_retention_copy(
            self.day_root,
            case_id=f_resp["case_id"],
            copy_id="archive_node_2",
            operation_id="op_wc_ack2",
        )
        self.assertEqual(status, 200)
        self.assertEqual(ack2["status"], gov.CASE_COMPLETE)
        self.assertTrue(ack2["complete"])
        self.assertEqual(ack2["remaining_unreachable_copies"], [])

    def test_13_late_old_generation_write_and_derivation_rejection(self) -> None:
        """Late old-gen ingest_source and register_derivation are rejected with 409 STALE_GENERATION_REJECTED."""
        gov.ingest_source(
            self.day_root,
            source_id="src_gen_1",
            text="gen 3 data",
            operation_id="op_g_ing3",
            generation=3,
            control_root=self.control_root,
        )
        gov.execute_explicit_forget(
            self.day_root,
            source_ids=["src_gen_1"],
            operation_id="op_g_del3",
            generation=3,
            control_root=self.control_root,
        )

        # Late gen=2 and gen=3 writes rejected
        for stale_gen in (2, 3):
            st_ing, resp_ing = gov.ingest_source(
                self.day_root,
                source_id="src_gen_1",
                text="late write",
                operation_id=f"op_g_late_ing_{stale_gen}",
                generation=stale_gen,
                control_root=self.control_root,
            )
            self.assertEqual(st_ing, 409)
            self.assertEqual(resp_ing["code"], "STALE_GENERATION_REJECTED")

            st_der, resp_der = gov.register_derivation(
                self.day_root,
                node_id=f"der_late_{stale_gen}",
                surface="episode",
                source_refs=[{"source_id": "src_gen_1"}],
                content="late derived",
                generation=stale_gen,
                operation_id=f"op_g_late_der_{stale_gen}",
                control_root=self.control_root,
            )
            self.assertEqual(st_der, 409)
            self.assertEqual(resp_der["code"], "STALE_GENERATION_REJECTED")

        # Newer generation=4 is permitted for fresh source, while gen=3 derivation against gen=4 source is rejected
        st_new, _ = gov.ingest_source(
            self.day_root,
            source_id="src_gen_1",
            text="gen 4 fresh",
            operation_id="op_g_ing4",
            generation=4,
            control_root=self.control_root,
        )
        self.assertEqual(st_new, 200)

        st_stale_d, resp_stale_d = gov.register_derivation(
            self.day_root,
            node_id="der_stale_against_4",
            surface="belief",
            source_refs=[{"source_id": "src_gen_1"}],
            content="stale",
            generation=3,
            operation_id="op_g_der_stale",
            control_root=self.control_root,
        )
        self.assertEqual(st_stale_d, 409)
        self.assertEqual(resp_stale_d["code"], "STALE_GENERATION_REJECTED")

    def test_14_zero_write_readonly_checks_and_control_rollback_fail_closed(self) -> None:
        """All readonly inspect/veto/barrier/projection APIs perform zero writes; control rollback fails closed 503."""
        gov.create_managed_artifact(
            self.day_root,
            artifact_id="art_zw",
            controller_principal_ref="owner_1",
            subject_id="subj_1",
            initial_body="# Body\n",
            operation_id="op_zw_art",
            visibility="shared",
            control_root=self.control_root,
        )
        gov.bind_representation(
            self.day_root,
            artifact_id="art_zw",
            workspace_root=self.workspace,
            workspace_rel_path="zw.md",
            operation_id="op_zw_bind",
        )
        gov.ingest_source(
            self.day_root,
            source_id="src_zw",
            text="zw secret",
            operation_id="op_zw_ing",
            control_root=self.control_root,
        )
        gov.execute_explicit_forget(
            self.day_root,
            source_ids=["src_zw"],
            operation_id="op_zw_del",
            control_root=self.control_root,
        )

        before_day = _snapshot_tree(self.day_root)
        before_ctrl = _snapshot_tree(self.control_root)
        before_ws = _snapshot_tree(self.workspace)

        gov.inspect_representation(self.day_root, artifact_id="art_zw")
        gov.evaluate_combined_veto(
            self.day_root,
            grant_id="missing_grant",
            subject="subj_1",
            capability="OUTBOUND_SEND",
            target_id="src_zw",
            audience="aud",
            payload_hash="h",
            control_root=self.control_root,
        )
        gov.read_with_barrier(
            self.day_root, target_kind="source", target_id="src_zw", control_root=self.control_root
        )
        gov.project_governance_view(self.day_root, has_subject_capability=False, control_root=self.control_root)

        self.assertEqual(before_day, _snapshot_tree(self.day_root))
        self.assertEqual(before_ctrl, _snapshot_tree(self.control_root))
        self.assertEqual(before_ws, _snapshot_tree(self.workspace))

        # Simulate stale control rollback -> fail closed 503 CONTROL_AUTHORITY_STALE_ROLLBACK
        cpath = self.control_root / "control_authority" / "v02_control_highwater_ledger.json"
        ctrl_doc = json.loads(cpath.read_text(encoding="utf-8"))
        ctrl_doc["deletion_highwater_seq"] = 0
        cpath.write_text(json.dumps(ctrl_doc), encoding="utf-8")

        st_rb, resp_rb = gov.read_with_barrier(
            self.day_root, target_kind="source", target_id="src_zw", control_root=self.control_root
        )
        self.assertEqual(st_rb, 503)
        self.assertEqual(resp_rb["code"], "CONTROL_AUTHORITY_STALE_ROLLBACK")


if __name__ == "__main__":
    unittest.main()

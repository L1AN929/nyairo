#!/usr/bin/env python3
"""External stop: durable intent, production gate, terminal and credential separation.

What is real here
-----------------
* ``ActivityAuthorityLease`` / ``CanonicalActivityStore`` / ``ActivityCommandService`` are the
  real LR-2 owners on an isolated root; every transition goes through the real admission
  issuer and an opaque ``ACTIVITY_CONSEQUENCE`` capability minted by the composition root;
* the Decision/Adoption read ports are the only stand-ins (START needs them); STOP_INTENT and
  CANCEL use the consequence capability and re-read the live Activity;
* the phone credential module is exercised with real files (POSIX only for ``issue``, since it
  fails closed without a verified private mode).  Tokens are never printed.

The end-to-end real chain (Supply/runtime/HTTP/AG-2) is ``scripts/activity_external_stop_demo.py``.

Run:
    PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -v tests.test_activity_external_stop
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
LIFE_SCRIPTS = ROOT / "components" / "life" / "scripts"
NATIVE = ROOT / "components" / "native"
sys.path[:0] = [
    str(ROOT),
    str(LIFE_SCRIPTS),
    str(NATIVE),
    str(NATIVE / "src"),
    str(NATIVE / "memory_runtime_v1"),
    str(ROOT / "tests" / "fixtures"),
]

import activity_admission as adm  # noqa: E402
import activity_continuity as ac  # noqa: E402
import activity_external_stop as aes  # noqa: E402
import activity_interruption as ai  # noqa: E402
import candidate_sources_ag0 as ag0  # noqa: E402
from components.phone import operator_stop as phone_operator_stop  # noqa: E402

POSIX_ONLY = unittest.skipUnless(os.name == "posix", "life modules import fcntl (POSIX only)")

DECISION_REF = "dec:stopfixture"
TS = "2026-10-28T00:00:00+00:00"
SUBJECT = ac.GLOBAL_SUBJECT_ID


class _DecisionReader:
    def get_decision(self, decision_id: str):
        if decision_id != DECISION_REF:
            return None
        return {"decision_id": decision_id, "revision": 1,
                "snapshot_fingerprint": "fp:%s" % decision_id}


class _ActionRead:
    """Minimal AR-0 read face: only the Activity/status projection the stop seam asks for."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def list_actions(self, *, status: str | None = None):
        return [dict(row) for row in self.rows
                if status is None or row.get("status") == status]


class _AdoptionReader:
    def __init__(self) -> None:
        self.grants: dict[str, tuple[str, str]] = {}


    def grant(self, command_id: str, mutation_kind: str, activity_ref: str) -> None:
        self.grants[command_id] = (mutation_kind, activity_ref)

    def get_adoption_by_id(self, adoption_id: str):
        command_id = adoption_id.split(":", 1)[1] if ":" in adoption_id else ""
        granted = self.grants.get(command_id)
        if granted is None:
            return None
        mutation_kind, activity_ref = granted
        return {
            "adoption_id": adoption_id,
            "decision_ref": DECISION_REF,
            "decision_revision": 1,
            "adoption_status": "APPLIED",
            "revision": 1,
            "authorized_mutation_kind": mutation_kind,
            "authorized_command_id": command_id,
            "created_activity_ref": activity_ref,
            "target_activity_ref": activity_ref,
            "authorized_activity_ref": activity_ref,
        }


@POSIX_ONLY
class ExternalStopOwnerTest(unittest.TestCase):
    """Real LR-2 owner: intent, replay, revision, terminal, gate, release."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="external-stop-owner-"))
        self.adoptions = _AdoptionReader()
        issuer = adm.build_admission_issuer(
            _DecisionReader(), self.adoptions, runtime_epoch="external-stop-test")
        self.issuer = issuer
        self.lease = ac.ActivityAuthorityLease(self.root)
        self.lease.acquire()
        self.store = ac.CanonicalActivityStore(
            self.root, namespace=ac.NAMESPACE_ISOLATED_TEST, _admission_issuer=issuer)
        self.capability = self.lease.issue_capability(namespace=ac.NAMESPACE_ISOLATED_TEST)
        self.commands = ac.ActivityCommandService(
            store=self.store, lease=self.lease, capability=self.capability)
        self.capability_consequence = issuer.build_consequence_capability(
            allowed_kinds=("STOP_INTENT", "CANCEL"),
            owner_label="local_operator_external_stop",
            evidence_verifier=self._verify)
        self.read = ac.ActivityReadService(self.store)
        self.interruption = ai.InterruptionCoordinator(
            activity_store=self.store, read_service=self.read)
        self.actions = _ActionRead()
        self.stop = aes.ExternalStopService(
            activity_command=self.commands,
            activity_read=self.read,
            activity_store=self.store,
            stop_capability=self.capability_consequence,
            now_iso=lambda: TS,
            subject_id=SUBJECT,
            interruption=self.interruption,
            action_read=self.actions,
        )

    def _verify(self, transition, canonical_state=None):
        payload = transition.get("external_stop")
        kind = transition.get("transition_type")
        # The owner passes its own live state inside the store lock; reading the store again
        # here would deadlock.  Same shape as ``activity_admission._canonical_activity``.
        act = None
        if isinstance(canonical_state, dict):
            act = (canonical_state.get("activities") or {}).get(
                str(transition.get("activity_id")))
        if act is None:
            raise adm.ConsequenceRejected("UNKNOWN_ACTIVITY")
        act = dict(act)
        if kind == "CANCEL":
            stop = act.get("external_stop") or {}
            if stop.get("state") != "PENDING":
                raise adm.ConsequenceRejected("NOT_PENDING")
            if str(transition.get("proposal_ref")) != str(stop.get("intent_id")):
                raise adm.ConsequenceRejected("INTENT_MISMATCH")
            return {"verified": True, "owner": "local_operator_external_stop"}
        validated = ac.validate_external_stop_payload(payload, activity=act, subject_id=SUBJECT)
        return {"verified": True, "owner": "local_operator_external_stop",
                "intent_id": validated["intent_id"]}

    def tearDown(self) -> None:
        self.lease.release()

    # -- helpers ---------------------------------------------------------------
    def _rev(self) -> int:
        state, _ = self.store.load_verified_state_and_journal()
        return int(state["revision"])

    def _activity(self, activity_id: str) -> dict:
        return dict(self.read.get_activity(activity_id) or {})

    def _start(self, activity_id: str, command_id: str = "stop-start") -> dict:
        self.adoptions.grant(command_id, "START", activity_id)
        result = self.commands.start_activity(
            activity_kind="READ_ARTIFACT", title="外部叫停夹具",
            origin_ref="artifact:%s" % ("a" * 32),
            source_refs=["artifact:%s" % ("a" * 32)],
            expected_revision=self._rev(), idempotency_key=command_id,
            decision_ref=DECISION_REF, adoption_ref="adp:%s" % command_id,
            activity_id=activity_id)
        return dict(result.activity)

    def _request(self, activity_id: str, *, key: str, reason: str = "OPERATOR_REQUEST",
                 revision: int | None = None) -> aes.ExternalStopRequest:
        act = self._activity(activity_id)
        return aes.build_request(
            activity_id=activity_id,
            expected_revision=int(act["revision"]) if revision is None else revision,
            operation_key=key, reason_code=reason, subject_id=SUBJECT,
            epoch="epoch-test", requested_at=TS)

    # -- cases -----------------------------------------------------------------
    def test_intent_is_durable_and_leaves_status_alone(self):
        act_id = "actv_stop0000000001"
        before = self._start(act_id)
        accepted = self.stop.accept(self._request(act_id, key="k1"))
        self.assertEqual(accepted["status"], aes.STATUS_ACCEPTED)
        after = self._activity(act_id)
        self.assertEqual(after["status"], "ACTIVE")
        self.assertEqual(after["checkpoint_ref"], before["checkpoint_ref"])
        self.assertEqual(after["external_stop"]["state"], "PENDING")
        self.assertEqual(after["external_stop"]["authority"], "LOCAL_OPERATOR")
        self.assertEqual(after["schema_version"], ac.SCHEMA_ACTIVITY_INSTANCE_V3)
        self.assertEqual(self._rev(), before["revision"] + 1)

    def test_same_payload_replays_and_tamper_is_refused(self):
        act_id = "actv_stop0000000002"
        self._start(act_id)
        request = self._request(act_id, key="k2")
        self.stop.accept(request)
        replay = self.stop.accept(request)
        self.assertEqual(replay["status"], aes.STATUS_REPLAY)
        replaced = self._request(act_id, key="k2", reason="SAFETY_HOLD")
        with self.assertRaises(aes.ExternalStopRefused) as caught:
            self.stop.accept(replaced)
        self.assertIn("OPERATION_KEY", caught.exception.reason_code)

    def test_stale_revision_is_refused(self):
        act_id = "actv_stop0000000003"
        act = self._start(act_id)
        stale = self._request(act_id, key="k3", revision=int(act["revision"]) - 1)
        with self.assertRaises(aes.ExternalStopRefused) as caught:
            self.stop.accept(stale)
        self.assertEqual(caught.exception.reason_code, aes.REFUSED_REVISION_STALE)
        self.assertNotIn("external_stop", self._activity(act_id))

    def test_terminal_activity_is_refused(self):
        act_id = "actv_stop0000000004"
        self._start(act_id)
        self.stop.handle(self._request(act_id, key="k4"))
        self.assertEqual(self._activity(act_id)["status"], "CANCELLED")
        with self.assertRaises(aes.ExternalStopRefused) as caught:
            self.stop.accept(self._request(act_id, key="k4b"))
        self.assertEqual(caught.exception.reason_code, aes.REFUSED_TERMINAL)

    def test_cancel_releases_foreground_and_keeps_checkpoint(self):
        act_id = "actv_stop0000000005"
        before = self._start(act_id)
        result = self.stop.handle(self._request(act_id, key="k5"))
        self.assertEqual(result["status"], aes.STATUS_CANCELLED)
        after = self._activity(act_id)
        self.assertEqual(after["status"], "CANCELLED")
        self.assertEqual(after["terminal_reason_code"], "CANCELLED_BY_EXTERNAL_OPERATOR")
        self.assertEqual(after["checkpoint_ref"], before["checkpoint_ref"])
        self.assertEqual(after["external_stop"]["state"], "FINALIZED")
        view = self.read.get_current_life_view()
        self.assertIn(view.get("foreground_activity_ref"), (None, ""))

    def test_other_activity_is_not_stopped(self):
        a_id, b_id = "actv_stop0000000006", "actv_stop0000000007"
        self._start(a_id, "stop-start-a")
        self.stop.handle(self._request(a_id, key="k6"))
        self.adoptions.grant("stop-start-b", "START", b_id)
        self.commands.start_activity(
            activity_kind="READ_ARTIFACT", title="另一件",
            origin_ref="artifact:%s" % ("b" * 32),
            source_refs=["artifact:%s" % ("b" * 32)],
            expected_revision=self._rev(), idempotency_key="stop-start-b",
            decision_ref=DECISION_REF, adoption_ref="adp:stop-start-b", activity_id=b_id)
        self.assertEqual(self._activity(b_id)["status"], "ACTIVE")
        self.assertNotIn("external_stop", self._activity(b_id))

    def test_production_gate_refuses_a_pending_stop(self):
        """The real AG-2 method the submit path calls, over the real owner record."""

        import life_integration_ag2 as ag2

        act_id = "actv_stop0000000008"
        self._start(act_id)
        gate = ag2.DecisionActionBridge.require_no_pending_external_stop
        bound = SimpleNamespace(activity_read=self.read)
        gate(bound, act_id, stage="before_submit")  # no intent yet -> allowed
        self.stop.accept(self._request(act_id, key="k8"))
        with self.assertRaises(ag2.ActivityExternalStopPending) as caught:
            gate(bound, act_id, stage="before_submit")
        self.assertEqual(caught.exception.stage, "before_submit")
        self.assertEqual(caught.exception.activity_id, act_id)
        # After the terminal, the same gate refuses for the terminal reason.
        self.stop.finalize(self._request(act_id, key="k8"))
        with self.assertRaises(Exception):
            gate(bound, act_id, stage="before_submit")


    def test_atomic_region_needs_a_real_owner_boundary(self):
        """PENDING until the owner records the boundary; never a caller-side flag write."""

        act_id = "actv_stop0000000009"
        self._start(act_id)
        self.interruption.enter_atomic_region(
            activity_id=act_id, atomic_region_ref="atomic_reg:test:1",
            max_duration_seconds=60)
        request = self._request(act_id, key="k9")
        pending = self.stop.handle(request)
        self.assertEqual(pending["status"], aes.STATUS_PENDING)
        self.assertEqual(pending["boundary"]["basis"], "ATOMIC_REGION_ACTIVE")
        self.assertEqual(self._activity(act_id)["status"], "ACTIVE")
        live = int(self._activity(act_id)["revision"])
        with self.assertRaises(ac.ContractViolationError):
            self.interruption.exit_atomic_region(
                activity_id=act_id, atomic_region_ref="atomic_reg:test:1",
                boundary_ref="atomic_exit:test:1", expected_activity_revision=live + 1)
        self.assertEqual(self._activity(act_id)["status"], "ACTIVE")
        closed = self.interruption.exit_atomic_region(
            activity_id=act_id, atomic_region_ref="atomic_reg:test:1",
            boundary_ref="atomic_exit:test:1", expected_activity_revision=live)
        self.assertFalse(closed["active"])
        self.assertEqual(closed["boundary_ref"], "atomic_exit:test:1")
        done = self.stop.finalize(request)
        self.assertEqual(done["status"], aes.STATUS_CANCELLED)
        self.assertEqual(self._activity(act_id)["status"], "CANCELLED")

    def test_safe_boundary_without_owner_boundary_stays_pending(self):
        act_id = "actv_stop0000000010"
        self._start(act_id)
        self.interruption.configure_activity_attention(
            activity_id=act_id, attention_occupancy="FOCUSED",
            interruptibility="SAFE_BOUNDARY")
        result = self.stop.handle(self._request(act_id, key="k10"))
        self.assertEqual(result["status"], aes.STATUS_PENDING)
        self.assertEqual(result["boundary"]["basis"],
                         "SAFE_BOUNDARY_NOT_CONFIRMED_BY_OWNER")
        self.assertEqual(self._activity(act_id)["status"], "ACTIVE")

    def test_safe_boundary_is_confirmed_by_the_owner_on_a_real_repeat(self):
        """First request PENDING; the *same* accepted operation re-checks live owner facts."""

        act_id = "actv_stop0000000012"
        self._start(act_id)
        self.interruption.configure_activity_attention(
            activity_id=act_id, attention_occupancy="FOCUSED",
            interruptibility="SAFE_BOUNDARY")
        request = self._request(act_id, key="k12")
        first = self.stop.handle(request)
        self.assertEqual(first["status"], aes.STATUS_PENDING)
        self.assertEqual(self._activity(act_id)["status"], "ACTIVE")
        applied = self.interruption.interruption_store.load_state().get(
            "applied_boundaries") or {}
        self.assertFalse([row for row in applied.values()
                          if row.get("activity_id") == act_id])
        # No caller-supplied "safe": the owner writes its own boundary record.
        again = self.stop.handle(request)
        self.assertEqual(again["status"], aes.STATUS_CANCELLED)
        self.assertEqual(again["boundary"], {"safe": True,
                                            "basis": "SAFE_BOUNDARY_CONFIRMED_BY_OWNER"})
        self.assertEqual(self._activity(act_id)["status"], "CANCELLED")
        self.assertEqual(self._activity(act_id)["external_stop"]["state"], "FINALIZED")
        applied = self.interruption.interruption_store.load_state().get(
            "applied_boundaries") or {}
        records = [row for row in applied.values() if row.get("activity_id") == act_id]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["source"], "EXTERNAL_STOP_OPERATOR_BOUNDARY")
        self.assertEqual(records[0]["authority"], "LOCAL_OPERATOR")

    def test_safe_boundary_repeat_waits_for_an_in_flight_action(self):
        """An Action past dispatch is not recalled; the stop stays PENDING until it settles."""

        act_id = "actv_stop0000000013"
        self._start(act_id)
        self.interruption.configure_activity_attention(
            activity_id=act_id, attention_occupancy="FOCUSED",
            interruptibility="SAFE_BOUNDARY")
        self.actions.rows = [{"action_id": "actn_stop_demo01", "activity_ref": act_id,
                              "status": "UNKNOWN"}]
        request = self._request(act_id, key="k13")
        self.assertEqual(self.stop.handle(request)["status"], aes.STATUS_PENDING)
        waiting = self.stop.handle(request)
        self.assertEqual(waiting["status"], aes.STATUS_PENDING)
        self.assertEqual(waiting["boundary"]["basis"], "IN_FLIGHT_ACTION_UNRESOLVED")
        self.assertEqual(self._activity(act_id)["status"], "ACTIVE")
        # The owner's own reconciliation settles it; only then does a repeat confirm.
        self.actions.rows[0]["status"] = "SUCCEEDED"
        done = self.stop.handle(request)
        self.assertEqual(done["status"], aes.STATUS_CANCELLED)

    def test_safe_boundary_repeat_without_an_action_ledger_never_confirms(self):
        act_id = "actv_stop0000000014"
        self._start(act_id)
        self.interruption.configure_activity_attention(
            activity_id=act_id, attention_occupancy="FOCUSED",
            interruptibility="SAFE_BOUNDARY")
        blind = aes.ExternalStopService(
            activity_command=self.commands, activity_read=self.read,
            activity_store=self.store, stop_capability=self.capability_consequence,
            now_iso=lambda: TS, subject_id=SUBJECT, interruption=self.interruption)
        request = self._request(act_id, key="k14")
        self.assertEqual(blind.handle(request)["status"], aes.STATUS_PENDING)
        again = blind.handle(request)
        self.assertEqual(again["status"], aes.STATUS_PENDING)
        self.assertEqual(again["boundary"]["basis"], "ACTION_LEDGER_UNAVAILABLE")
        self.assertEqual(self._activity(act_id)["status"], "ACTIVE")

    def test_replay_survives_a_later_revision(self):
        act_id = "actv_stop0000000011"
        self._start(act_id)
        request = self._request(act_id, key="k11")
        self.stop.handle(request)
        # The caller re-sends with the *current* revision; identity is the operation key.
        again = aes.build_request(
            activity_id=act_id, expected_revision=int(self._activity(act_id)["revision"]),
            operation_key="k11", reason_code="OPERATOR_REQUEST", subject_id=SUBJECT,
            epoch="epoch-test", requested_at=TS)
        self.assertEqual(self.stop.accept(again)["status"], aes.STATUS_REPLAY)


class ExternalStopRequestTest(unittest.TestCase):
    def test_subject_and_authority_come_from_the_server(self):
        request = aes.build_request(
            activity_id="actv_x", expected_revision=2, operation_key="ops:1",
            reason_code="USER_REQUEST", subject_id="chiyo.global", epoch="e1")
        payload = request.payload()
        self.assertEqual(payload["authority"], "LOCAL_OPERATOR")
        self.assertEqual(payload["subject_id"], "chiyo.global")
        self.assertTrue(payload["intent_id"].startswith("opstop:"))
        # A retry of the same key derives the same intent.
        again = aes.build_request(
            activity_id="actv_x", expected_revision=2, operation_key="ops:1",
            reason_code="USER_REQUEST", subject_id="chiyo.global", epoch="e1")
        self.assertEqual(again.intent_id, request.intent_id)

    def test_invalid_requests_are_named_refusals(self):
        for kwargs, code in (
            ({"activity_id": "", "expected_revision": 1, "operation_key": "k",
              "reason_code": "OPERATOR_REQUEST", "subject_id": SUBJECT, "epoch": "e"},
             "ACTIVITY_ID_REQUIRED"),
            ({"activity_id": "actv_x", "expected_revision": -1, "operation_key": "k",
              "reason_code": "OPERATOR_REQUEST", "subject_id": SUBJECT, "epoch": "e"},
             "EXPECTED_REVISION_REQUIRED"),
            ({"activity_id": "actv_x", "expected_revision": 1, "operation_key": "bad key!",
              "reason_code": "OPERATOR_REQUEST", "subject_id": SUBJECT, "epoch": "e"},
             "OPERATION_KEY_INVALID"),
            ({"activity_id": "actv_x", "expected_revision": 1, "operation_key": "k",
              "reason_code": "BECAUSE", "subject_id": SUBJECT, "epoch": "e"},
             aes.REFUSED_UNKNOWN_REASON),
            ({"activity_id": "actv_x", "expected_revision": 1, "operation_key": "k",
              "reason_code": "OPERATOR_REQUEST", "subject_id": "", "epoch": "e"},
             aes.REFUSED_SUBJECT),
        ):
            with self.assertRaises(aes.ExternalStopRefused) as caught:
                aes.build_request(**kwargs)
            self.assertEqual(caught.exception.reason_code, code)

    def test_payload_refuses_smuggled_identity(self):
        activity = {"activity_id": "actv_x", "revision": 3, "status": "ACTIVE",
                    "subject_id": SUBJECT}
        base = aes.build_request(
            activity_id="actv_x", expected_revision=3, operation_key="ops:2",
            reason_code="OPERATOR_REQUEST", subject_id=SUBJECT, epoch="e").payload()
        ac.validate_external_stop_payload(base, activity=activity, subject_id=SUBJECT)
        for key in ("actor", "uid", "system", "operator", "token", "safe"):
            with self.assertRaises(ac.ContractViolationError):
                ac.validate_external_stop_payload(
                    {**base, key: "x"}, activity=activity, subject_id=SUBJECT)
        with self.assertRaises(Exception):
            ac.validate_external_stop_payload(
                {**base, "subject_id": "someone.else"}, activity=activity, subject_id=SUBJECT)


class StopControlChannelTest(unittest.TestCase):
    TOKEN = "t" * 48

    def _submit(self, channel, request, instance):
        return channel.submit(request, key=aes.mailbox_key(self.TOKEN),
                              instance=instance)

    def test_request_result_roundtrip(self):
        root = Path(tempfile.mkdtemp(prefix="external-stop-mailbox-"))
        channel = aes.StopControlChannel(root)
        request = aes.build_request(
            activity_id="actv_mail", expected_revision=4, operation_key="ops:mail:1",
            reason_code="OPERATOR_REQUEST", subject_id=SUBJECT, epoch="e")
        path = self._submit(channel, request, root)
        listed = channel.list_requests()
        self.assertEqual(len(listed), 1)
        restored = aes.request_from_mailbox(listed[0])
        self.assertEqual(restored.intent_id, request.intent_id)
        self.assertEqual(restored.activity_id, "actv_mail")
        # The file says who wrote it and carries a MAC; the token itself is never in it.
        self.assertEqual(listed[0]["issuer"], aes.MAILBOX_ISSUER)
        self.assertEqual(len(listed[0]["mac"]), 64)
        self.assertNotIn(self.TOKEN, json.dumps(listed[0]))
        channel.write_result(path, {"status": "PENDING", "intent_id": request.intent_id})
        result = channel.read_result("ops:mail:1")
        self.assertEqual(result["status"], "PENDING")
        self.assertNotIn("token", result)
        channel.clear(path)
        self.assertEqual(channel.list_requests(), [])

    def test_a_request_cannot_be_queued_without_the_serve_key(self):
        root = Path(tempfile.mkdtemp(prefix="external-stop-mailbox-"))
        channel = aes.StopControlChannel(root)
        request = aes.build_request(
            activity_id="actv_mail", expected_revision=4, operation_key="ops:mail:2",
            reason_code="OPERATOR_REQUEST", subject_id=SUBJECT, epoch="e")
        with self.assertRaises(aes.ExternalStopRefused):
            channel.submit(request, key=b"", instance=root)
        self.assertEqual(channel.list_requests(), [])


class MailboxAuthorityTest(unittest.TestCase):
    """A queued file is not authority: only the current serve's signed request is."""

    TOKEN = "k" * 48

    def _credential(self, **overrides):
        values = {"token": self.TOKEN, "subject": "test:phone",
                  "life_subject": SUBJECT, "instance": "/tmp/instance-a",
                  "endpoint": "http://127.0.0.1:3818", "epoch": "e" * 32,
                  "issued_at": "2026-10-28T00:00:00Z"}
        values.update(overrides)
        return SimpleNamespace(**values)

    def _payload(self, credential, **overrides):
        payload = {
            "schema_version": aes.SCHEMA_STOP_REQUEST, "issuer": aes.MAILBOX_ISSUER,
            "instance": credential.instance, "activity_id": "actv_mail",
            "expected_revision": 4, "operation_key": "ops:auth:1",
            "reason_code": "OPERATOR_REQUEST", "subject_id": credential.life_subject,
            "epoch": credential.epoch, "requested_at": "2026-10-28T00:00:00Z",
            "intent_id": "opstop:0123456789abcdef", "boundary_mode": "IMMEDIATE",
            "unresolved_action_refs": [],
        }
        payload["mac"] = aes.mailbox_mac(payload, aes.mailbox_key(self.TOKEN))
        payload.update(overrides)
        return payload

    def test_signed_request_is_accepted(self):
        credential = self._credential()
        aes.verify_mailbox_request(self._payload(credential), credential=credential)

    def test_each_claim_is_checked(self):
        credential = self._credential()
        cases = [
            ({"mac": "0" * 64}, aes.REFUSED_MAILBOX_FORGED),
            ({"issuer": "SOMEBODY_ELSE"}, aes.REFUSED_MAILBOX_ISSUER),
            ({"instance": "/tmp/instance-b"}, aes.REFUSED_MAILBOX_INSTANCE),
            ({"epoch": "f" * 32}, aes.REFUSED_MAILBOX_EPOCH),
            ({"subject_id": "someone.else"}, aes.REFUSED_SUBJECT),
        ]
        for overrides, code in cases:
            with self.assertRaises(aes.ExternalStopRefused) as caught:
                aes.verify_mailbox_request(self._payload(credential, **overrides),
                                           credential=credential)
            self.assertEqual(caught.exception.reason_code, code, overrides)

    def test_a_correctly_signed_old_epoch_request_is_still_refused(self):
        old = self._credential(epoch="a" * 32)
        current = self._credential(epoch="b" * 32)
        payload = self._payload(old)  # signed with the right key, wrong serve
        aes.verify_mailbox_request(payload, credential=old)
        with self.assertRaises(aes.ExternalStopRefused) as caught:
            aes.verify_mailbox_request(payload, credential=current)
        self.assertEqual(caught.exception.reason_code, aes.REFUSED_MAILBOX_EPOCH)


class OperatorEndpointTest(unittest.TestCase):
    def test_only_a_bare_loopback_endpoint_is_accepted(self):
        for good in ("http://127.0.0.1:3818", "http://localhost:3818/",
                     "http://[::1]:3818"):
            phone_operator_stop.normalize_loopback_endpoint(good)
        for bad, code in (
            ("http://user:pass@127.0.0.1:3818", "ENDPOINT_USERINFO_REFUSED"),
            ("http://127.0.0.1:3818/api", "ENDPOINT_PATH_REFUSED"),
            ("http://127.0.0.1:3818?x=1", "ENDPOINT_QUERY_REFUSED"),
            ("http://10.0.0.5:3818", "ENDPOINT_NOT_LOOPBACK"),
            ("https://127.0.0.1:3818", "ENDPOINT_SCHEME_NOT_LOOPBACK_HTTP"),
            ("http://127.0.0.1", "ENDPOINT_PORT_REQUIRED"),
        ):
            with self.assertRaises(Exception) as caught:
                phone_operator_stop.normalize_loopback_endpoint(bad)
            self.assertEqual(getattr(caught.exception, "reason_code", ""), code, bad)


class PhoneMailboxTest(unittest.TestCase):
    """A phone process without the runtime can only mail the request -- never fake a stop."""

    def test_request_is_mailed_and_reported_pending(self):
        from components.phone import service as phone_service

        instance = Path(tempfile.mkdtemp(prefix="external-stop-phone-"))
        state_root = instance / "life" / "state"
        state_root.mkdir(parents=True)
        (state_root / "canonical_life_state.json").write_text(json.dumps({
            "schema_version": "chiyo.life.canonical_state.v1",
            "subject_id": SUBJECT,
            "revision": 5,
            "foreground_activity_ref": "actv_mailbox",
            "activities": {"actv_mailbox": {
                "activity_id": "actv_mailbox", "schema_version": "chiyo.life.activity.v1",
                "revision": 3, "status": "ACTIVE", "subject_id": SUBJECT}},
        }), encoding="utf-8")
        service = phone_service.PhoneService(
            {"root": str(instance), "subject": "test:phone", "world_home": str(instance),
             "supply_socket": str(instance / "s.sock"),
             "private_items_artifact_id": "", "artifact_grant_id": "g",
             "workspace_id": "w", "environment": {}},
            life_state_root=state_root)
        credential = SimpleNamespace(token="p" * 48, life_subject=SUBJECT)
        result = service.request_activity_stop(
            activity_id="actv_mailbox", expected_revision=3,
            operation_key="mail:1", reason_code="OPERATOR_REQUEST", epoch="epoch-x",
            stop_credential=credential)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], aes.STATUS_PENDING)
        self.assertEqual(result["data"]["reason_code"], "RUNTIME_MAILBOXED")
        self.assertFalse(result["data"]["accepted_as_stop"])
        self.assertTrue(result["data"]["mailboxed"])
        mailed = aes.StopControlChannel(state_root).list_requests()
        self.assertEqual(len(mailed), 1)
        self.assertEqual(mailed[0]["activity_id"], "actv_mailbox")
        self.assertEqual(len(mailed[0]["mac"]), 64)
        self.assertNotIn(credential.token, json.dumps(mailed[0]))
        # Without a credential nothing is written at all.
        other = service.request_activity_stop(
            activity_id="actv_mailbox", expected_revision=3,
            operation_key="mail:2", reason_code="OPERATOR_REQUEST", epoch="epoch-x")
        self.assertFalse(other["ok"])
        self.assertEqual(len(aes.StopControlChannel(state_root).list_requests()), 1)
        # The read face never exposes a body or a token.
        state = service.activity_stop_state("actv_mailbox")
        self.assertTrue(state["ok"])
        self.assertNotIn("token", json.dumps(state).lower())
        # Nothing is accepted yet: the read face says QUEUED (a weaker fact) rather than
        # borrowing the owner's PENDING or pretending the Activity is stopped.
        self.assertEqual(state["data"]["stop_state"], "QUEUED")
        self.assertIsNotNone(state["data"]["queued_request"])
        self.assertIsNone(state["data"]["intent_id"])


class CancelledHandoffDeliveryTest(unittest.TestCase):
    """The CANCELLED kind reaches the production Memory consumer, not just the outbox."""

    def test_cancelled_kind_is_accepted_once_by_the_real_memory_consumer(self):
        try:
            from life_experience_memory_consumer import MemoryAdmissionExperienceConsumer
            from memory_owner_handoff import MemoryOwnerHandoff
        except Exception as exc:  # the M0 port imports fcntl on POSIX only
            self.skipTest("real Memory consumer unavailable: %s" % type(exc).__name__)

        class _Port:
            subject = "chiyo"

            def __init__(self) -> None:
                self.seen: set[str] = set()
                self.events: list = []

            def append_outcome(self, event):
                self.events.append(event)
                source_ref = str(event.source_refs[0]["id"])
                if source_ref in self.seen:
                    return {"status": "duplicate", "event_id": "ev:cancelled"}
                self.seen.add(source_ref)
                return {"status": "inserted", "event_id": "ev:cancelled"}

        port = _Port()
        # The CANCELLED handoff is issued and persisted by the instance's own AG-2 owner;
        # the consumer is bound to that owner's read port (the same read the production
        # runtime performs at assembly), not to a dict kept here.
        owner = MemoryOwnerHandoff(Path(tempfile.mkdtemp(prefix="external-stop-ag2-owner-")))
        self.addCleanup(owner.close)
        owner.register_activity("actv_stop_x", revision=2, activity_kind="PERSONAL_WORK")
        owner.open_integration(
            integration_id="lint:cancelled0000001",
            correlation_id="corr:cancelled",
            decision_ref="dec:stopfixture", adoption_ref="adp:stopfixture")
        issued = owner.emit(
            integration_id="lint:cancelled0000001", activity_id="actv_stop_x",
            kind="CANCELLED", occurred_at=TS)
        event = issued["record"]
        self.assertTrue(event, "the AG-2 owner persisted the CANCELLED handoff")
        consumer = MemoryAdmissionExperienceConsumer(
            port=port, enabled=True, handoff_verifier=owner.verifier)
        first = consumer.receive_experience_handoff(dict(event))
        again = consumer.receive_experience_handoff(dict(event))
        self.assertEqual(first["disposition"], "ACCEPT")
        self.assertEqual(again["disposition"], "DUPLICATE")
        self.assertEqual(len(port.events), 2)
        # The row names the termination kind and the Activity; nothing private is invented.
        refs = {ref["kind"]: ref["id"] for ref in port.events[0].source_refs}
        self.assertEqual(refs["life_outcome"], event["handoff_id"])
        self.assertEqual(refs["activity"], "actv_stop_x")
        self.assertEqual(refs["outcome_kind"], "CANCELLED")


class CandidateWithholdingTest(unittest.TestCase):
    def _source(self):
        return ag0.CurrentActivityCandidateSource.__new__(ag0.CurrentActivityCandidateSource)

    def test_pending_stop_withholds_the_continue_candidate(self):
        source = self._source()
        row = {"activity_id": "actv_x", "status": "ACTIVE", "subject_id": SUBJECT,
               "external_stop": {"state": "PENDING", "intent_id": "opstop:1"}}
        ok, reason = source.validate_source(row, observed_at=TS)
        self.assertFalse(ok)
        self.assertEqual(reason, "ACTIVITY_EXTERNAL_STOP_PENDING")

    def test_no_stop_keeps_the_old_behaviour(self):
        source = self._source()
        row = {"activity_id": "actv_x", "status": "ACTIVE", "subject_id": SUBJECT}
        ok, reason = source.validate_source(row, observed_at=TS)
        self.assertTrue(ok)
        self.assertIsNone(reason)


@POSIX_ONLY
class OperatorCredentialTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="external-stop-cred-"))

    def test_issue_read_match_and_rotate(self):
        path, credential = phone_operator_stop.issue(
            self.root, subject="test:external-stop",
            endpoint="http://127.0.0.1:3818")
        self.assertEqual(path.name, "operator-stop.json")
        restored = phone_operator_stop.read_credential(path)
        self.assertTrue(phone_operator_stop.matches(restored, credential.token))
        self.assertFalse(phone_operator_stop.matches(restored, "x" * 48))
        self.assertFalse(phone_operator_stop.matches(restored, credential.token, epoch="other"))
        self.assertNotIn(credential.token, repr(credential))
        phone_operator_stop.discard(self.root, credential)
        _, rotated = phone_operator_stop.issue(
            self.root, subject="test:external-stop", endpoint="http://127.0.0.1:3818")
        self.assertNotEqual(rotated.token, credential.token)
        self.assertFalse(phone_operator_stop.matches(rotated, credential.token))

    def test_non_loopback_and_repository_roots_are_refused(self):
        with self.assertRaises(phone_operator_stop.OperatorStopUnavailable):
            phone_operator_stop.issue(self.root, subject="s",
                                      endpoint="http://10.0.0.5:3818")
        with self.assertRaises(phone_operator_stop.OperatorStopUnavailable):
            phone_operator_stop.issue(ROOT / "components" / "phone", subject="s",
                                      endpoint="http://127.0.0.1:3818")

    def test_diary_and_operator_credentials_do_not_cross_read(self):
        from components.phone import capability as phone_capability

        diary_path, _diary = phone_capability.issue(
            self.root, subject="s", endpoint="http://127.0.0.1:3818")
        operator_path, _operator = phone_operator_stop.issue(
            self.root, subject="s", endpoint="http://127.0.0.1:3818")
        # Each reader refuses the other module's file by kind, not by luck.
        with self.assertRaises(phone_operator_stop.OperatorStopInvalid):
            phone_operator_stop.read_credential(diary_path)
        with self.assertRaises(phone_capability.CapabilityInvalid):
            phone_capability.read_capability(operator_path)


if __name__ == "__main__":
    unittest.main()

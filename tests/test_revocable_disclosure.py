"""Targeted tests for the narrow revocable disclosure permit.

Only the pieces this change owns: the structured scope, the owner's exact lookup, revocation
semantics, and the "write grant is not a disclosure grant" separation.  The full real-owner /
formal-assembly chain lives in ``scripts/revocable_disclosure_demo.py``.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"), str(ROOT / "components" / "supply")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from lifesupply.governance.authority import (  # noqa: E402
    ALLOW,
    CAPABILITY_MISMATCH,
    DISCLOSE_CAPABILITY,
    DISCLOSURE_AUTHORITY_SCOPE,
    DISCLOSURE_TARGET_LOCAL_OWNER,
    REVOKED,
    GovernanceAuthority,
    disclosure_scope,
)
from lifesupply.service.life_supply import (  # noqa: E402
    LifeSupplyService,
    claims_share_envelope,
    parse_share_envelope,
    parse_share_send_envelope,
)

SUBJECT = "test:disclosure"


def _envelope(state: str, *, source: str = "artifact:abc", version: str = "artifactversion:v1",
              body: str = "private body that must not leak") -> str:
    return "\n".join([
        "nyairo.share.v1",
        "state=%s" % state,
        "source_artifact_id=%s" % source,
        "source_version_id=%s" % version,
        "audience=local:owner",
        "channel=local_inbox",
        "subject_id=%s" % SUBJECT,
        "text:",
        body,
    ])


class DisclosureScopeTest(unittest.TestCase):
    def test_scope_is_structured_and_exact(self):
        scope = disclosure_scope("artifact:abc", "artifactversion:v1")
        self.assertTrue(scope.startswith("disclosure:"))
        payload = json.loads(scope[len("disclosure:"):])
        self.assertEqual(payload, {"artifact_id": "artifact:abc",
                                   "version_id": "artifactversion:v1"})
        self.assertNotEqual(scope, disclosure_scope("artifact:abc", "artifactversion:v2"))

    def test_only_a_delivered_envelope_is_a_disclosure(self):
        self.assertIsNone(parse_share_send_envelope(_envelope("DRAFT")))
        self.assertIsNone(parse_share_send_envelope("not an envelope"))
        binding = parse_share_send_envelope(_envelope("LOCAL_DELIVERED"))
        self.assertEqual(binding["source_version_id"], "artifactversion:v1")
        self.assertEqual(binding["audience"], "local:owner")
        # the body never enters the parsed header
        self.assertNotIn("private body", json.dumps(binding))

    def test_envelope_grammar_matches_the_canonical_parser(self):
        # the recognised vocabulary is parsed, an unknown state or duplicate header is not
        self.assertEqual(parse_share_envelope(_envelope("DEFERRED"))["state"], "DEFERRED")
        self.assertIsNone(parse_share_envelope(_envelope("SOMETHING_ELSE")))
        duplicated = _envelope("LOCAL_DELIVERED").replace(
            "state=LOCAL_DELIVERED", "state=LOCAL_DELIVERED\nstate=LOCAL_DELIVERED")
        self.assertIsNone(parse_share_envelope(duplicated))
        # a text that only *claims* the schema is distinguishable from ordinary text
        self.assertTrue(claims_share_envelope(duplicated))
        self.assertFalse(claims_share_envelope(_envelope("LOCAL_DELIVERED").replace(
            "nyairo.share.v1\n", "", 1)))

    def test_the_legacy_server_gate_uses_the_same_grammar(self):
        # The legacy entry has no operator port, so it refuses a delivery *and* a malformed
        # look-alike; ordinary text and a plain draft stay allowed.
        from lifesupply.service.server import _is_local_share_delivery

        self.assertTrue(_is_local_share_delivery(_envelope("LOCAL_DELIVERED")))
        self.assertTrue(_is_local_share_delivery(_envelope("LOCAL_DELIVERED").replace(
            "state=LOCAL_DELIVERED", "state=LOCAL_DELIVERED\nstate=LOCAL_DELIVERED", 1)))
        self.assertFalse(_is_local_share_delivery(_envelope("DRAFT")))
        self.assertFalse(_is_local_share_delivery("just a note, not a share"))


class OwnerPermitTest(unittest.TestCase):
    def setUp(self):
        # Kept on purpose: this suite must not delete anything (the parent's rule for the whole
        # feature).  ``mkdtemp`` leaves the directory behind; there is no cleanup hook.
        self.root = Path(tempfile.mkdtemp(prefix="revocable-disclosure-test-"))
        self.gov = GovernanceAuthority(self.root / "disclosure.sqlite")

    def _issue(self, capability: str = DISCLOSE_CAPABILITY, *, target: str = DISCLOSURE_TARGET_LOCAL_OWNER,
                scope: str | None = None, operation_id: str = "op:issue") -> str:
        answer = self.gov.issue_grant(
            grantor="HOST_OPERATOR", bootstrap_authorized=True, subject=SUBJECT,
            capability=capability, scope=scope or disclosure_scope("artifact:abc", "artifactversion:v1"),
            target=target, authority_scope=DISCLOSURE_AUTHORITY_SCOPE,
            use_mode="ONE_SHOT", operation_id=operation_id)
        self.assertEqual(answer.status, ALLOW, answer.detail)
        return str(answer.grant_id)

    def test_exact_lookup_then_revoke(self):
        grant_id = self._issue()
        rows = self.gov.lookup_grants_exact(
            subject=SUBJECT, capability=DISCLOSE_CAPABILITY,
            scope=disclosure_scope("artifact:abc", "artifactversion:v1"),
            target=DISCLOSURE_TARGET_LOCAL_OWNER)
        self.assertEqual([row["grant_id"] for row in rows], [grant_id])
        revoked = self.gov.revoke_grant(grant_id, expected_revision=1, operation_id="op:revoke")
        self.assertEqual(revoked.status, REVOKED)
        decision = self.gov.validate_grant(
            grant_id, subject=SUBJECT, capability=DISCLOSE_CAPABILITY,
            scope=disclosure_scope("artifact:abc", "artifactversion:v1"),
            target=DISCLOSURE_TARGET_LOCAL_OWNER)
        self.assertEqual(decision.status, REVOKED)
        # the revoked row is still found by the exact binding, which is why a send can say
        # DISCLOSURE_REVOKED instead of "no permit exists".
        rows = self.gov.lookup_grants_exact(
            subject=SUBJECT, capability=DISCLOSE_CAPABILITY,
            scope=disclosure_scope("artifact:abc", "artifactversion:v1"),
            target=DISCLOSURE_TARGET_LOCAL_OWNER)
        self.assertEqual([row["revocation_state"] for row in rows], ["REVOKED"])

    def test_a_write_grant_cannot_validate_as_disclosure(self):
        write_grant = self._issue(capability="COMMIT_MANAGED_ARTIFACT",
                                  scope="artifact:personal", operation_id="op:write")
        decision = self.gov.validate_grant(
            write_grant, subject=SUBJECT, capability=DISCLOSE_CAPABILITY,
            scope=disclosure_scope("artifact:abc", "artifactversion:v1"),
            target=DISCLOSURE_TARGET_LOCAL_OWNER)
        self.assertEqual(decision.status, CAPABILITY_MISMATCH)

    def test_scope_does_not_cover_another_version(self):
        grant_id = self._issue()
        decision = self.gov.validate_grant(
            grant_id, subject=SUBJECT, capability=DISCLOSE_CAPABILITY,
            scope=disclosure_scope("artifact:abc", "artifactversion:v2"),
            target=DISCLOSURE_TARGET_LOCAL_OWNER)
        self.assertNotEqual(decision.status, ALLOW)
        rows = self.gov.lookup_grants_exact(
            subject=SUBJECT, capability=DISCLOSE_CAPABILITY,
            scope=disclosure_scope("artifact:abc", "artifactversion:v2"),
            target=DISCLOSURE_TARGET_LOCAL_OWNER)
        self.assertEqual(rows, [])


class AdmissionTransactionTest(unittest.TestCase):
    def setUp(self):
        # Same no-delete rule as above: the service root is kept.
        self.root = Path(tempfile.mkdtemp(prefix="revocable-disclosure-uow-"))
        self.service = LifeSupplyService(self.root / "data")

    def tearDown(self):
        self.service.close()

    def test_permit_read_joins_the_transaction_that_writes_the_delivery(self):
        # The disclosure lookup and the permit reserve must run on the same coordinated
        # transaction as the artifact write, otherwise a revoke could land between "validate"
        # and "reserve" and still let the delivery through.  ``_connection`` joins the ambient
        # unit of work instead of opening its own connection; assert that identity directly.
        with self.service.db_store.unit_of_work(
                command="COMMIT_MARKDOWN", operation_id="op:uow-probe",
                subject_id=SUBJECT) as uow:
            with self.service.gov_store._connection() as con:
                self.assertIs(getattr(con, "raw", None), uow.con)
            uow.rollback()


if __name__ == "__main__":
    unittest.main()

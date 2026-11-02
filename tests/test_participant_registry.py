"""F-140: the instance participant registry -- identity, endpoints, explicit event links.

The tests use the **real owners** (A's ``LocalContactInbox`` and the real ``PhoneService``
facade); only the operator's clicks are scripted.  Fixture roots live under ``/tmp`` and are
kept on purpose: no ``TemporaryDirectory``, no ``rmtree``, no ``unlink`` of data.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

if os.name != "posix":  # pragma: no cover - the operator entry is a POSIX-only write surface
    raise unittest.SkipTest("F-140 operator writes require POSIX uid/mode checks")

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_local_contact as lc  # noqa: E402
import participant_registry as pr  # noqa: E402
from components.phone import contact as phone_contact  # noqa: E402
from components.phone import service as phone_service  # noqa: E402

SUBJECT = "chiyo-f140"
HOME = "/tmp/f140-home"


def _root() -> Path:
    return Path(tempfile.mkdtemp(prefix="f140-participant-test-"))


def _registry(root: Path, *, subject: str = SUBJECT, inbox=None,
              busy_timeout_ms: int = 2000) -> pr.ParticipantRegistry:
    return pr.ParticipantRegistry(root, subject_id=subject, inbox=inbox,
                                  busy_timeout_ms=busy_timeout_ms)


def _inbox(root: Path, *, subject: str = SUBJECT) -> lc.LocalContactInbox:
    return lc.LocalContactInbox(root, subject_id=subject, home_ref=HOME)


def _event(root: Path, text: str = "hello") -> str:
    answer = _inbox(root).submit({"event_kind": lc.KIND_MESSAGE, "text": text})
    assert answer.get("ok"), answer
    return str(answer["event_ref"])


class ParticipantIdentityTest(unittest.TestCase):
    def test_same_display_name_is_two_people_and_ids_are_opaque(self) -> None:
        root = _root()
        registry = _registry(root)
        first = registry.create(display_name="小林", aliases=["Kobayashi"],
                                operation_key="op:one")
        second = registry.create(display_name="小林", operation_key="op:two")
        self.assertEqual(first["status"], "applied", first)
        self.assertEqual(second["status"], "applied", second)
        one = first["data"]["participant"]
        two = second["data"]["participant"]
        self.assertNotEqual(one["participant_id"], two["participant_id"])
        self.assertTrue(one["participant_id"].startswith("participant:"))
        self.assertNotIn("小林", one["participant_id"])
        self.assertEqual(one["aliases"], ["Kobayashi"])
        self.assertEqual(one["created_by"], "operator:cli")
        self.assertEqual(one["source"], "operator:cli")
        self.assertFalse(one["is_authority"])
        self.assertFalse(one["inherits_grants"])
        listed = registry.list_participants()
        self.assertTrue(listed["ok"])
        self.assertEqual(listed["data"]["count"], 2)
        self.assertEqual(len(listed["data"]["participants"]), 2)

    def test_operation_key_replays_and_refuses_a_different_payload(self) -> None:
        root = _root()
        registry = _registry(root)
        first = registry.create(display_name="A", operation_key="op:same")
        again = registry.create(display_name="A", operation_key="op:same")
        self.assertEqual(again["status"], "already_applied")
        self.assertTrue(again["replayed"])
        self.assertEqual(again["data"]["participant"]["participant_id"],
                         first["data"]["participant"]["participant_id"])
        conflict = registry.create(display_name="B", operation_key="op:same")
        self.assertEqual(conflict["status"], "rejected")
        self.assertEqual(conflict["reason_code"], "PARTICIPANT_OPERATION_KEY_CONFLICT")
        self.assertEqual(registry.list_participants()["data"]["count"], 1)

    def test_caller_cannot_claim_system_or_trusted_source(self) -> None:
        root = _root()
        registry = _registry(root)
        bad_source = registry.create(display_name="A", operation_key="op:a",
                                     source="system:trusted")
        self.assertEqual(bad_source["reason_code"], "PARTICIPANT_SOURCE_REFUSED")
        bad_who = registry.create(display_name="A", operation_key="op:b",
                                  created_by="model")
        self.assertEqual(bad_who["reason_code"], "PARTICIPANT_CREATED_BY_REFUSED")
        self.assertEqual(registry.list_participants()["data"]["count"], 0)

    def test_bad_names_and_ids_are_named_refusals(self) -> None:
        root = _root()
        registry = _registry(root)
        empty = registry.create(display_name="  ", operation_key="op:empty")
        self.assertEqual(empty["reason_code"], "PARTICIPANT_DISPLAY_NAME_REQUIRED")
        too_long = registry.create(display_name="x" * 65, operation_key="op:long")
        self.assertEqual(too_long["reason_code"], "PARTICIPANT_DISPLAY_NAME_TOO_LONG")
        bad_key = registry.create(display_name="A", operation_key="no spaces allowed")
        self.assertEqual(bad_key["reason_code"], "PARTICIPANT_OPERATION_KEY_INVALID")
        unknown = registry.get_participant("participant:" + "0" * 24)
        self.assertEqual(unknown["status"], "absent")
        malformed = registry.get_participant("../../etc/passwd")
        self.assertEqual(malformed["reason_code"], "PARTICIPANT_ID_REQUIRED")


class ParticipantEndpointTest(unittest.TestCase):
    def test_same_person_two_channels_and_conflicting_endpoint_refused(self) -> None:
        root = _root()
        registry = _registry(root)
        one = registry.create(display_name="A", operation_key="op:a")["data"]["participant"]
        two = registry.create(display_name="B", operation_key="op:b")["data"]["participant"]
        first = registry.bind_endpoint(participant_id=one["participant_id"],
                                       provider="local_inbox", account_key="desk-1",
                                       operation_key="op:e1")
        second = registry.bind_endpoint(participant_id=one["participant_id"],
                                        provider="telegram", account_key="42",
                                        operation_key="op:e2")
        replay = registry.bind_endpoint(participant_id=one["participant_id"],
                                        provider="local_inbox", account_key="desk-1",
                                        operation_key="op:e1")
        self.assertEqual(first["status"], "applied")
        self.assertEqual(second["status"], "applied")
        self.assertEqual(replay["status"], "already_applied")
        self.assertNotEqual(first["data"]["endpoint"]["endpoint_id"],
                            second["data"]["endpoint"]["endpoint_id"])
        conflict = registry.bind_endpoint(participant_id=two["participant_id"],
                                          provider="local_inbox", account_key="desk-1",
                                          operation_key="op:e3")
        self.assertEqual(conflict["status"], "conflict")
        self.assertEqual(conflict["reason_code"], "PARTICIPANT_ENDPOINT_ALREADY_BOUND")
        detail = registry.get_participant(one["participant_id"])["data"]["participant"]
        self.assertEqual(detail["endpoint_count"], 2)
        self.assertEqual({item["provider"] for item in detail["endpoints"]},
                         {"local_inbox", "telegram"})

    def test_unknown_participant_binding_does_not_create_a_ledger(self) -> None:
        root = _root()
        registry = _registry(root)
        answer = registry.bind_endpoint(participant_id="participant:" + "0" * 24,
                                        provider="local_inbox", account_key="desk-1",
                                        operation_key="op:e1")
        self.assertEqual(answer["reason_code"], "PARTICIPANT_NOT_FOUND")
        self.assertFalse(registry.path.exists())


class ParticipantEventLinkTest(unittest.TestCase):
    def test_unlinked_arrival_is_unresolved_then_explicitly_linked(self) -> None:
        root = _root()
        event_ref = _event(root)
        registry = _registry(root, inbox=_inbox(root))
        person = registry.create(display_name="A", operation_key="op:a")["data"]["participant"]
        before = registry.resolve_event(event_ref)
        self.assertEqual(before["status"], "UNRESOLVED")
        self.assertIsNone(before["data"]["participant"])
        linked = registry.link_event(event_ref=event_ref,
                                     participant_id=person["participant_id"],
                                     operation_key="op:link")
        self.assertEqual(linked["status"], "applied", linked)
        after = registry.resolve_event(event_ref)
        self.assertEqual(after["status"], "LINKED")
        self.assertEqual(after["data"]["participant"]["participant_id"],
                         person["participant_id"])
        self.assertNotIn("body", json.dumps(after["data"]))
        replay = registry.link_event(event_ref=event_ref,
                                     participant_id=person["participant_id"],
                                     operation_key="op:link")
        self.assertEqual(replay["status"], "already_applied")

    def test_unknown_event_and_wrong_instance_are_refused_without_writes(self) -> None:
        root = _root()
        _event(root)
        registry = _registry(root, inbox=_inbox(root))
        person = registry.create(display_name="A", operation_key="op:a")["data"]["participant"]
        missing = registry.link_event(event_ref="contact_evt:" + "0" * 16,
                                      participant_id=person["participant_id"],
                                      operation_key="op:missing")
        self.assertEqual(missing["status"], "absent")
        self.assertEqual(missing["reason_code"], "PARTICIPANT_EVENT_NOT_FOUND")
        detail = registry.get_participant(person["participant_id"])["data"]["participant"]
        self.assertEqual(detail["linked_event_count"], 0)

    def test_inbox_bound_to_another_subject_cannot_verify_a_link(self) -> None:
        root = _root()
        event_ref = _event(root)
        person = _registry(root).create(display_name="A", operation_key="op:a")
        pid = person["data"]["participant"]["participant_id"]
        # The ledger knows the person, but the bound inbox believes it is another instance:
        # the event cannot be verified, so the link is refused and nothing is written.
        registry = _registry(root, inbox=_inbox(root, subject="another-instance"))
        answer = registry.link_event(event_ref=event_ref, participant_id=pid,
                                     operation_key="op:link")
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason_code"], "CONTACT_STATE_SUBJECT_MISMATCH")
        self.assertEqual(registry.get_participant(pid)["data"]["participant"]
                         ["linked_event_count"], 0)

    def test_event_from_another_root_is_not_found_here(self) -> None:
        here = _root()
        elsewhere = _root()
        foreign_ref = _event(elsewhere, "from another instance")
        registry = _registry(here, inbox=_inbox(here))
        person = registry.create(display_name="A", operation_key="op:a")["data"]["participant"]
        answer = registry.link_event(event_ref=foreign_ref,
                                     participant_id=person["participant_id"],
                                     operation_key="op:link")
        self.assertEqual(answer["status"], "absent")
        self.assertEqual(answer["reason_code"], "PARTICIPANT_EVENT_NOT_FOUND")

    def test_one_event_cannot_be_two_people(self) -> None:
        root = _root()
        event_ref = _event(root)
        registry = _registry(root, inbox=_inbox(root))
        one = registry.create(display_name="A", operation_key="op:a")["data"]["participant"]
        two = registry.create(display_name="B", operation_key="op:b")["data"]["participant"]
        first = registry.link_event(event_ref=event_ref,
                                    participant_id=one["participant_id"],
                                    operation_key="op:l1")
        self.assertEqual(first["status"], "applied")
        second = registry.link_event(event_ref=event_ref,
                                     participant_id=two["participant_id"],
                                     operation_key="op:l2")
        self.assertEqual(second["status"], "conflict")
        self.assertEqual(second["reason_code"], "PARTICIPANT_EVENT_LINK_CONFLICT")
        self.assertEqual(registry.resolve_event(event_ref)["data"]["participant"]
                         ["participant_id"], one["participant_id"])

    def test_link_with_an_endpoint_of_another_participant_is_refused(self) -> None:
        root = _root()
        event_ref = _event(root)
        registry = _registry(root, inbox=_inbox(root))
        one = registry.create(display_name="A", operation_key="op:a")["data"]["participant"]
        two = registry.create(display_name="B", operation_key="op:b")["data"]["participant"]
        endpoint = registry.bind_endpoint(participant_id=two["participant_id"],
                                          provider="local_inbox", account_key="desk-2",
                                          operation_key="op:e")["data"]["endpoint"]
        answer = registry.link_event(event_ref=event_ref,
                                     participant_id=one["participant_id"],
                                     endpoint_id=endpoint["endpoint_id"],
                                     operation_key="op:l")
        self.assertEqual(answer["status"], "conflict")
        self.assertEqual(answer["reason_code"], "PARTICIPANT_ENDPOINT_MISMATCH")


class ParticipantPersistenceTest(unittest.TestCase):
    def test_missing_ledger_is_absent_and_reads_never_create_it(self) -> None:
        root = _root()
        registry = _registry(root)
        listed = registry.list_participants()
        self.assertTrue(listed["ok"])
        self.assertEqual(listed["data"]["ledger_state"], "ABSENT")
        self.assertEqual(listed["data"]["participants"], [])
        self.assertEqual(registry.summary()["data"]["ledger_state"], "ABSENT")
        self.assertEqual(registry.resolve_event("contact_evt:" + "0" * 16)["status"],
                         "UNRESOLVED")
        self.assertFalse(registry.path.exists())

    def test_restart_same_root_keeps_ids_aliases_endpoints_and_links(self) -> None:
        root = _root()
        event_ref = _event(root)
        registry = _registry(root, inbox=_inbox(root))
        person = registry.create(display_name="A", aliases=["Ali"], operation_key="op:a")
        pid = person["data"]["participant"]["participant_id"]
        registry.bind_endpoint(participant_id=pid, provider="local_inbox",
                               account_key="desk-1", operation_key="op:e")
        registry.link_event(event_ref=event_ref, participant_id=pid, operation_key="op:l")
        reopened = _registry(root)
        detail = reopened.get_participant(pid)
        self.assertTrue(detail["ok"], detail)
        self.assertEqual(detail["data"]["participant"]["display_name"], "A")
        self.assertEqual(detail["data"]["participant"]["aliases"], ["Ali"])
        self.assertEqual(detail["data"]["participant"]["endpoint_count"], 1)
        self.assertEqual(detail["data"]["participant"]["linked_event_refs"], [event_ref])
        self.assertEqual(reopened.list_participants()["data"]["count"], 1)

    def test_foreign_or_corrupt_ledger_is_named_not_empty(self) -> None:
        root = _root()
        _registry(root).create(display_name="A", operation_key="op:a")
        foreign = _registry(root, subject="another-instance")
        answer = foreign.list_participants()
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason_code"], "PARTICIPANT_INSTANCE_MISMATCH")

        corrupt = _root()
        (corrupt / pr.DB_FILENAME).write_text("not a sqlite database", encoding="utf-8")
        broken = _registry(corrupt).list_participants()
        self.assertFalse(broken["ok"])
        self.assertEqual(broken["reason_code"], "PARTICIPANT_STATE_CORRUPT")

    def test_second_writer_is_serialised_or_refused(self) -> None:
        root = _root()
        registry = _registry(root, busy_timeout_ms=100)
        registry.create(display_name="A", operation_key="op:a")
        holder = sqlite3.connect(str(registry.path), isolation_level=None)
        try:
            holder.execute("BEGIN IMMEDIATE")
            refused = registry.create(display_name="B", operation_key="op:b")
        finally:
            holder.execute("ROLLBACK")
            holder.close()
        self.assertFalse(refused["ok"])
        self.assertIn(refused["reason_code"],
                      {"PARTICIPANT_DB_BUSY", "PARTICIPANT_STATE_UNREADABLE"})
        self.assertEqual(registry.list_participants()["data"]["count"], 1)


class ParticipantPhoneReadTest(unittest.TestCase):
    def _service(self, root: Path):
        instance = {
            "root": str(root),
            "world_home": str(root / "world"),
            "supply_socket": str(root / "missing.sock"),
            "private_items_artifact_id": "artifact:items",
            "artifact_grant_id": "grant:x",
            "workspace_id": "workspace:x",
            "subject": SUBJECT,
            "contact_home_ref": HOME,
        }
        inbox = _inbox(root)
        return phone_service.PhoneService(
            instance,
            environment={},
            contact=phone_contact.PhoneContact(state_root=root, subject=SUBJECT,
                                               home_ref=HOME, inbox=inbox),
            participants=_registry(root, inbox=inbox),
            life_state_root=root,
        ), inbox

    def test_phone_list_get_and_event_resolution(self) -> None:
        root = _root()
        service, inbox = self._service(root)
        submitted = inbox.submit({"event_kind": lc.KIND_MESSAGE, "text": "hello"})
        event_ref = str(submitted["event_ref"])
        registry = _registry(root, inbox=inbox)
        person = registry.create(display_name="小林", aliases=["Kobayashi"],
                                 operation_key="op:a")["data"]["participant"]
        pid = person["participant_id"]

        listed = service.participants()
        self.assertTrue(listed["ok"], listed)
        self.assertEqual(listed["data"]["count"], 1)
        self.assertNotIn("body", json.dumps(listed["data"]).lower())

        registry.bind_endpoint(participant_id=pid, provider="telegram", account_key="42",
                               operation_key="op:endpoint")
        detail = service.participant(pid)
        self.assertTrue(detail["ok"], detail)
        self.assertEqual(detail["data"]["participant"]["aliases"], ["Kobayashi"])
        self.assertEqual(detail["data"]["participant"]["endpoints"][0]["provider"], "telegram")
        self.assertNotIn("account_key",
                         detail["data"]["participant"]["endpoints"][0])
        self.assertEqual(service.participant("participant:" + "0" * 24)["status"],
                         "not_found")

        unresolved = service.contact_event(event_ref)
        self.assertTrue(unresolved["ok"], unresolved)
        self.assertEqual(unresolved["data"]["participant"]["resolution"], "UNRESOLVED")

        registry.link_event(event_ref=event_ref, participant_id=pid,
                            operation_key="op:link")
        linked = service.contact_event(event_ref)
        self.assertEqual(linked["data"]["participant"]["resolution"], "LINKED")
        self.assertEqual(linked["data"]["participant"]["participant"]["participant_id"], pid)
        self.assertFalse(linked["data"]["participant"]["private_text_included"])

    def test_phone_read_surface_has_no_write_route(self) -> None:
        from components.phone import server as phone_server
        from chiyo_bundle import phone_tool

        self.assertIn("participants", phone_tool.READ_ACTIONS)
        self.assertIn("participant", phone_tool.ACTIONS)
        self.assertIn("participants", phone_tool.ACTIONS)
        self.assertNotIn("participant_create", phone_tool.ACTIONS)
        self.assertNotIn("participant", phone_tool.WRITE_ACTIONS)
        handler = phone_server.PhoneRequestHandler
        self.assertTrue(hasattr(handler, "_get_participants"))
        self.assertTrue(hasattr(handler, "_get_participant"))
        self.assertFalse(hasattr(handler, "_post_participant"))


CLI = ROOT / "scripts" / "participant_registry_cli.py"
STATE_DIRS = ("one-day", "life", "state")


class ParticipantInstanceEntryTest(unittest.TestCase):
    """``--instance`` derives the state root, subject and home; the caller cannot claim them.

    These tests go through the real CLI process (not the owner class), because the binding is
    the entry's own job.  Roots stay under ``/tmp`` on purpose.
    """

    def _root(self, *, create_state: bool = True) -> Path:
        root = Path(tempfile.mkdtemp(prefix="f140-instance-"))
        if create_state:
            state = root.joinpath(*STATE_DIRS)
            state.mkdir(parents=True)
            os.chmod(state, 0o700)
        phone_service.write_instance(root, self._instance(root))
        return root

    @staticmethod
    def _state(root: Path) -> Path:
        return root.joinpath(*STATE_DIRS)

    @staticmethod
    def _instance(root: Path) -> dict:
        return {
            "schema_version": phone_service.SCHEMA_INSTANCE,
            "kind": phone_service.KIND_INSTANCE,
            "root": str(root),
            "subject": SUBJECT,
            "world_home": str(root / "world"),
            "supply_socket": str(root / "missing.sock"),
            "private_items_artifact_id": "artifact:items",
            "artifact_grant_id": "grant:x",
            "workspace_id": "workspace:x",
            "contact_home_ref": HOME,
        }

    def _cli(self, *args: str) -> tuple[int, dict]:
        completed = subprocess.run([sys.executable, str(CLI), *args], cwd=str(ROOT),
                                   capture_output=True, text=True,
                                   env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        return int(completed.returncode), json.loads(completed.stdout.strip().splitlines()[-1])

    def test_instance_entry_derives_subject_and_state_root(self) -> None:
        root = self._root()
        state = self._state(root)
        lc.LocalContactInbox(state, subject_id=SUBJECT, home_ref=HOME).submit(
            {"event_kind": lc.KIND_MESSAGE, "text": "hello", "operation_key": "arrival:one"})
        code, answer = self._cli("--instance", str(root), "create", "--display-name", "A",
                                 "--operation-key", "op:a")
        self.assertEqual(code, 0, answer)
        self.assertEqual(answer["status"], "applied")
        self.assertTrue((state / pr.DB_FILENAME).is_file())
        self.assertEqual(os.stat(state / pr.DB_FILENAME).st_mode & 0o777, 0o600)
        code, binding = self._cli("--instance", str(root), "binding")
        self.assertEqual(code, 0, binding)
        block = binding["data"]["binding"]
        self.assertEqual(block["state_root"], str(state))
        self.assertEqual(block["subject"], SUBJECT)
        self.assertEqual(block["home_ref"], HOME)
        self.assertEqual(block["instance_kind"], "phone_instance")

    def test_caller_facts_that_contradict_the_instance_are_refused(self) -> None:
        root = self._root()
        state = self._state(root)
        foreign = self._root()
        code, wrong_subject = self._cli("--instance", str(root), "--subject", "someone-else",
                                        "create", "--display-name", "A",
                                        "--operation-key", "op:a")
        self.assertEqual(code, 2)
        self.assertEqual(wrong_subject["reason_code"],
                         "PARTICIPANT_INSTANCE_ARGUMENT_MISMATCH")
        code, wrong_root = self._cli("--instance", str(root), "--state-root",
                                     str(self._state(foreign)), "create",
                                     "--display-name", "A", "--operation-key", "op:b")
        self.assertEqual(code, 2)
        self.assertEqual(wrong_root["reason_code"], "PARTICIPANT_INSTANCE_ARGUMENT_MISMATCH")
        self.assertFalse((state / pr.DB_FILENAME).exists())

    def test_inbox_of_another_subject_blocks_the_first_ledger(self) -> None:
        root = self._root()
        lc.LocalContactInbox(self._state(root), subject_id="another-instance",
                             home_ref=HOME).submit({"event_kind": lc.KIND_MESSAGE,
                                                    "text": "x",
                                                    "operation_key": "arrival:x"})
        code, answer = self._cli("--instance", str(root), "create", "--display-name", "A",
                                 "--operation-key", "op:a")
        self.assertEqual(code, 3)
        self.assertEqual(answer["reason_code"], "PARTICIPANT_INSTANCE_BINDING_MISMATCH")
        self.assertFalse((self._state(root) / pr.DB_FILENAME).exists())

    def test_symlinked_and_shared_writable_roots_are_refused(self) -> None:
        root = self._root(create_state=False)
        (root / "one-day" / "life").mkdir(parents=True)
        target = self._root()
        os.symlink(self._state(target), self._state(root))
        code, answer = self._cli("--instance", str(root), "create", "--display-name", "A",
                                 "--operation-key", "op:a")
        self.assertEqual(code, 3)
        self.assertEqual(answer["reason_code"], "PARTICIPANT_STATE_ROOT_SYMLINK")
        self.assertFalse((self._state(target) / pr.DB_FILENAME).exists())

        unsafe = self._root()
        os.chmod(self._state(unsafe), 0o777)
        code, answer = self._cli("--instance", str(unsafe), "create", "--display-name", "A",
                                 "--operation-key", "op:b")
        self.assertEqual(code, 3)
        self.assertEqual(answer["reason_code"], "PARTICIPANT_STATE_ROOT_UNSAFE_MODE")
        self.assertFalse((self._state(unsafe) / pr.DB_FILENAME).exists())

    def test_compatibility_form_needs_existing_evidence_and_matching_subject(self) -> None:
        bare = Path(tempfile.mkdtemp(prefix="f140-bare-"))
        code, answer = self._cli("--state-root", str(bare), "--subject", SUBJECT, "create",
                                 "--display-name", "A", "--operation-key", "op:a")
        self.assertEqual(code, 2)
        self.assertEqual(answer["reason_code"], "PARTICIPANT_INSTANCE_UNVERIFIABLE")
        self.assertFalse((bare / pr.DB_FILENAME).exists())

        root = self._root()
        state = self._state(root)
        lc.LocalContactInbox(state, subject_id=SUBJECT, home_ref=HOME).submit(
            {"event_kind": lc.KIND_MESSAGE, "text": "hello", "operation_key": "arrival:one"})
        code, allowed = self._cli("--state-root", str(state), "--subject", SUBJECT, "create",
                                  "--display-name", "A", "--operation-key", "op:a")
        self.assertEqual(code, 0, allowed)
        code, mismatched = self._cli("--state-root", str(state), "--subject", "someone-else",
                                     "create", "--display-name", "B",
                                     "--operation-key", "op:b")
        self.assertEqual(code, 2)  # a write against another subject's ledger is a refusal
        self.assertEqual(mismatched["reason_code"], "PARTICIPANT_INSTANCE_MISMATCH")


if __name__ == "__main__":
    unittest.main(verbosity=2)

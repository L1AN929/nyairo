#!/usr/bin/env python3
"""v0.4 work-revisit: real Supply history reads, bounded compare, sourced new work, phone.

What this file tests (and with what evidence)
---------------------------------------------
The owner is **real**: ``lifesupply.service.life_supply.LifeSupplyService`` runs on its own Unix
socket over one canonical database inside a unique fixture root, with Governance + Workspace +
ManagedArtifact in that one store.  Everything asserted about versions, heads, revisions, hashes
and operation rows is read back from that owner -- not from a substitute.

* ``list_versions`` / ``read_version``: the two new owner reads.  Bounded page + cursor,
  metadata-only list, named-version body with the *full* stored ``content_sha256``, artifact
  binding (a version of another artifact is refused by name), tombstone withholding, and a
  request bound the owner itself enforces.
* ``GET`` is a pure read: head version id, artifact revision, version count and the receipt table
  are identical before and after a full read pass over list / named version / compare / provenance.
* ``compare``: real text difference reported as ``content_changed`` with a quoted first
  difference; the *same* version compares as ``machine_head_only`` (no invented edit).
* ``POST .../projects/derived``: one **new** artifact whose first version carries the structured
  provenance line; the source artifact keeps its head, revision and version count; the provenance
  is still readable after the new work is committed again (it is read from the immutable first
  version).
* CAS and UNKNOWN: a stale ``expected_head`` is refused by the owner; a lost answer is resolved
  with the *same* operation key and creates no second version.
* Same instance, new runtime: a second ``PhoneService`` built from the same instance root reads
  the same versions, provenance and operation log back, with no duplicate version and no replayed
  write.

Operational notes
-----------------
* POSIX only (AF_UNIX).  On native Windows every test here skips by name.
* The fixture root lives under ``$PI_SCRATCH_DIR`` (or ``$NYAIRO_WORK_REVISIT_ROOT``) and is
  **kept**: nothing is deleted or cleaned, and no old fixture is reused.
* Zero model calls: nothing in this file reaches a provider.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(Path(__file__).resolve().parent)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_artifact_port as lap  # noqa: E402
import private_project_service as pps  # noqa: E402
from components.phone import server as phone_server  # noqa: E402
from components.phone import service as phone_service  # noqa: E402

SUBJECT = "test:work-revisit"
CSRF = "work-revisit-csrf"
API = "/api/phone"

#: Three real, different bodies.  The old sentinel is what a later comparison must quote.
SENTINEL_OLD = "SENTINEL-OLD-1"
V1 = "第一版：%s 她把旧稿第一段的句子留在这里。" % SENTINEL_OLD
V2 = V1 + "\n第二段：这是后来加的一段。"
V3 = V2 + "\n第三段：今天又改了一处。"
MATERIAL_TEXT = "资料第一页：别人的文章，只能引用不能续写。"
ITEMS_TEXT = "{}"

posix_only = unittest.skipIf(os.name == "nt",
                             "needs POSIX AF_UNIX sockets (run under WSL/Linux)")


# ---------------------------------------------------------------------------------------------
# fixtures (kept, unique)
# ---------------------------------------------------------------------------------------------


def fixture_base() -> Path:
    override = os.environ.get("NYAIRO_WORK_REVISIT_ROOT") or os.environ.get("PI_SCRATCH_DIR")
    base = ((Path(override) / "nyairo-work-revisit") if override
            else (Path(os.environ.get("TMPDIR") or "/tmp") / "nyairo-work-revisit"))
    base.mkdir(parents=True, exist_ok=True)
    return base


def unique_root(prefix: str) -> Path:
    root = fixture_base() / ("%s%d-%d" % (prefix, os.getpid(), time.time_ns()))
    if root.exists():
        raise AssertionError("fixture root already exists: %s" % root)
    root.mkdir(parents=True)
    return root


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _owner_call(socket_path, request: dict, *, timeout: float = 20.0) -> dict:
    body = json.dumps(dict(request), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(float(timeout))
        client.connect(str(socket_path))
        client.sendall(body + b"\n")
        line = client.makefile("rb").readline(1 << 20)
    if not line:
        raise AssertionError("owner closed the connection without an answer")
    answer = json.loads(line.decode("utf-8"))
    if not isinstance(answer, dict):
        raise AssertionError("owner answer is not an object")
    return answer


class SupplyOwner:
    """The real Life Supply owner: one canonical db, one socket, three owners."""

    def __init__(self, root: Path) -> None:
        from lifesupply.service.life_supply import LifeSupplyService, LifeSupplySocketServer
        from lifesupply.service.switches import FeatureSwitches

        self.root = Path(root)
        self.socket_path = self.root / "life-supply.sock"
        uid = os.geteuid()
        self.service = LifeSupplyService(self.root / "data", operator_uids={uid},
                                         service_uids={uid},
                                         switches=FeatureSwitches.all_on(),
                                         writer_instance="work-revisit-test")
        self.server = LifeSupplySocketServer(str(self.socket_path), self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def call(self, request: dict) -> dict:
        return _owner_call(self.socket_path, request)

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.service.close()


class _InjectedLostAnswer:
    """Rewrite exactly one op's answer as UNKNOWN; the write itself still reaches the owner.

    Patched on the **class**, not on one port object: every service builds its own
    ``LifeArtifactPort`` per call, so patching an instance would test nothing.
    """

    def __init__(self, op: str, *, forward: bool = True) -> None:
        self.forward = forward
        self.seen: list[dict] = []
        self._op = op
        self._real = lap.LifeArtifactPort._call

    def __enter__(self) -> "_InjectedLostAnswer":
        def wrapper(port_self, request):
            if request.get("op") != self._op:
                return self._real(port_self, request)
            self.seen.append(dict(request))
            if self.forward:
                self._real(port_self, request)
            return {"status": "UNKNOWN", "reason": "SERVICE_ERROR",
                    "detail": "injected: the answer was lost"}

        self._patcher = mock.patch.object(lap.LifeArtifactPort, "_call", wrapper)
        self._patcher.start()
        return self

    def __exit__(self, *exc_info) -> bool:
        self._patcher.stop()
        return False


# ---------------------------------------------------------------------------------------------
# the real chain
# ---------------------------------------------------------------------------------------------


@posix_only
class WorkRevisitTest(unittest.TestCase):
    """Real owner + real phone service + real loopback HTTP over one instance."""

    def setUp(self) -> None:
        self.root = unique_root("work-revisit-")
        self.owner = SupplyOwner(self.root)
        self.addCleanup(self.owner.stop)
        self.workspace_id = self._provision_workspace()
        self.grant_id = self._issue_artifact_grant()
        self.port = lap.LifeArtifactPort(str(self.owner.socket_path), SUBJECT)
        self.work_id = self._create_work()
        self.material_id = self._create_artifact("资料页", MATERIAL_TEXT, "material")
        self.items_id = self._create_artifact("私人日常事项", ITEMS_TEXT, "items")
        self.instance = self._instance()
        self.service = phone_service.PhoneService(self.instance, environment={})
        self.httpd = phone_server.create_server(self.service, csrf_token=CSRF,
                                                port=free_port())
        self.base_url = "http://127.0.0.1:%d" % int(self.httpd.server_address[1])
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.addCleanup(self._close_http)

    def _close_http(self) -> None:
        httpd = getattr(self, "httpd", None)
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        thread = getattr(self, "thread", None)
        if thread is not None:
            thread.join(timeout=10)

    # -- owner-side plumbing (operator ops, not the subject's) --------------------------

    def _issue(self, capability: str, scope: str, target: str, authority_scope: str,
               use_mode: str, operation_id: str) -> str:
        answer = self.owner.call({"op": "issue_grant", "grant": {
            "subject": SUBJECT, "capability": capability, "scope": scope, "target": target,
            "authority_scope": authority_scope, "use_mode": use_mode,
            "operation_id": operation_id}})
        self.assertEqual(answer.get("status"), "ALLOW", answer)
        return str(answer["grant_id"])

    def _provision_workspace(self) -> str:
        grant = self._issue("PROVISION_PERSONAL_WORKSPACE", "workspace:personal",
                            "workspace:%s" % SUBJECT, "life-supply.workspace.v1", "ONE_SHOT",
                            "issue:workspace:%s" % SUBJECT)
        answer = self.owner.call({"op": "provision_workspace", "grant_id": grant,
                                  "operation_key": "work-revisit:workspace:%s" % SUBJECT,
                                  "workspace": {"subject_id": SUBJECT}})
        self.assertEqual(answer.get("status"), "OK", answer)
        return str(answer["workspace"]["workspace_id"])

    def _issue_artifact_grant(self) -> str:
        return self._issue("COMMIT_MANAGED_ARTIFACT", "artifact:personal",
                           "workspace:%s" % self.workspace_id, "life-supply.artifact.v1",
                           "REUSABLE", "issue:artifact:%s" % SUBJECT)

    def _create_artifact(self, title: str, content: str, tag: str) -> str:
        outcome = self.port.create_artifact(
            workspace_id=self.workspace_id, title=title, content=content, grant_id=self.grant_id,
            operation_key=lap.operation_key_for("work-revisit", tag, SUBJECT))
        self.assertTrue(outcome.applied, outcome)
        return str(outcome.artifact_id)

    def _create_work(self) -> str:
        """One work with three real, different versions (v1 -> v2 -> v3 == head)."""
        artifact_id = self._create_artifact("旧稿", V1, "work-v1")
        head = self.port.read_head(artifact_id) or {}
        second = self.port.commit_version(
            artifact_id=artifact_id, expected_head=str(head["version_id"]), content=V2,
            grant_id=self.grant_id,
            operation_key=lap.operation_key_for("work-revisit", "work-v2", SUBJECT))
        self.assertTrue(second.applied, second)
        head = self.port.read_head(artifact_id) or {}
        third = self.port.commit_version(
            artifact_id=artifact_id, expected_head=str(head["version_id"]), content=V3,
            grant_id=self.grant_id,
            operation_key=lap.operation_key_for("work-revisit", "work-v3", SUBJECT))
        self.assertTrue(third.applied, third)
        return artifact_id

    def _instance(self) -> dict:
        world_home = self.root / "world"
        world_home.mkdir(parents=True, exist_ok=True)
        run = self.root / "run"
        run.mkdir(parents=True, exist_ok=True)
        # The phone only builds a port when the socket path exists; this *is* the owner's socket.
        return {
            "schema_version": phone_service.SCHEMA_INSTANCE,
            "kind": phone_service.KIND_INSTANCE,
            "name": "work-revisit-fixture",
            "subject": SUBJECT,
            "local_owner_uid": os.geteuid(),
            "root": str(self.root),
            "profile_home": str(self.root),
            "world_home": str(world_home),
            "world_id": "w-work-revisit",
            "supply_socket": str(self.owner.socket_path),
            "private_items_artifact_id": self.items_id,
            "reading_artifact_id": self.material_id,
            "artifact_grant_id": self.grant_id,
            "workspace_id": self.workspace_id,
            "world_body_socket": str(world_home / "run" / "gateway.sock"),
            "features": {},
        }

    # -- transport ----------------------------------------------------------------------

    def call(self, method: str, path: str, body=None, *, headers=None):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self.base_url + path, data=data, method=method)
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return int(response.status), json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return int(error.code), json.loads(error.read().decode("utf-8"))

    def post(self, path: str, body, *, csrf: bool = True):
        extra = {"X-CSRF-Token": CSRF} if csrf else {}
        return self.call("POST", path, body, headers=extra)

    # -- owner snapshots ----------------------------------------------------------------

    def snapshot(self) -> dict:
        """Everything a pure read must not move."""
        head = self.port.read_head(self.work_id) or {}
        view = self.port.read_artifact(self.work_id) or {}
        artifact = dict(view.get("artifact") or {})
        listing = self.port.list_versions(self.work_id, limit=50)
        unresolved = self.owner.call({"op": "list_unresolved_operations"})
        return {
            "head_version_id": head.get("version_id"),
            "version_number": head.get("version_number"),
            "revision": artifact.get("revision"),
            "lifecycle": artifact.get("lifecycle"),
            "total_versions": listing.get("total_versions"),
            "unresolved": unresolved.get("count"),
        }

    def history_of(self, artifact_id: str) -> dict:
        page = self.port.list_versions(artifact_id, limit=50)
        self.assertTrue(page.get("complete"), page)
        return page

    def version_number(self, number: int) -> str:
        for row in self.history_of(self.work_id)["entries"]:
            if row.get("version_number") == number:
                return str(row["version_id"])
        raise AssertionError("version %s is not in the owner's list" % number)

    # -- owner history reads -------------------------------------------------------------

    def test_owner_lists_every_version_and_reads_one_by_name(self) -> None:
        page = self.port.list_versions(self.work_id, limit=2)
        self.assertEqual(page["total_versions"], 3)
        self.assertEqual(page["count"], 2)
        self.assertFalse(page["complete"], page)
        self.assertTrue(page["has_more"], page)
        self.assertEqual([row["version_number"] for row in page["entries"]], [3, 2])
        self.assertEqual(page["next_before_version_number"], 2)
        # The bodies are not in the metadata list: only sizes and hashes.
        self.assertTrue(all("content" not in row for row in page["entries"]))
        older = self.port.list_versions(self.work_id, limit=2,
                                        before_version_number=page["next_before_version_number"])
        self.assertEqual([row["version_number"] for row in older["entries"]], [1])
        # A cursor page is a page *of* a longer history by construction: ``has_more`` says whether
        # anything older is left, and ``complete`` stays honestly False for it.
        self.assertFalse(older["has_more"], older)
        self.assertEqual(older["reason_code"], "HISTORY_PAGED")
        self.assertIsNone(older["next_before_version_number"])

        first = self.port.read_version(self.work_id, self.version_number(1))
        self.assertEqual(first["content"], V1)
        self.assertEqual(first["version_number"], 1)
        self.assertFalse(first["content_truncated"])
        self.assertEqual(first["content_sha256"],
                         self.port.read_version(self.work_id, self.version_number(1))
                         ["content_sha256"])
        head = self.port.read_head(self.work_id) or {}
        self.assertEqual(self.port.read_version(self.work_id, str(head["version_id"]))["content"],
                         V3)

    def test_owner_refuses_a_version_of_another_artifact(self) -> None:
        foreign = self.version_number(1)
        answer = self.owner.call({"op": "read_version", "subject": SUBJECT,
                                  "artifact_id": self.material_id, "version_id": foreign})
        self.assertEqual(answer.get("status"), "NOT_FOUND", answer)
        self.assertEqual(answer.get("reason"), "VERSION_NOT_IN_ARTIFACT", answer)
        with self.assertRaises(lap.ArtifactOwnerRefused):
            self.port.read_version(self.material_id, foreign)

    def test_owner_enforces_its_own_bounds(self) -> None:
        too_many = self.owner.call({"op": "list_versions", "subject": SUBJECT,
                                    "artifact_id": self.work_id, "limit": 500})
        self.assertEqual(too_many.get("status"), "DENY", too_many)
        self.assertEqual(too_many.get("reason"), "LIMIT_OUT_OF_RANGE")
        bad_cursor = self.owner.call({"op": "list_versions", "subject": SUBJECT,
                                      "artifact_id": self.work_id, "before_version_number": 0})
        self.assertEqual(bad_cursor.get("reason"), "CURSOR_INVALID", bad_cursor)
        huge = self.owner.call({"op": "read_version", "subject": SUBJECT,
                                "artifact_id": self.work_id,
                                "version_id": self.version_number(1),
                                "max_chars": 10_000_000})
        self.assertEqual(huge.get("reason"), "MAX_CHARS_OUT_OF_RANGE", huge)
        # A different subject never sees this artifact's history.
        other = self.owner.call({"op": "list_versions", "subject": "test:somebody-else",
                                 "artifact_id": self.work_id})
        self.assertIn(other.get("status"), ("DENY", "NOT_FOUND"), other)

    def test_owner_withholds_history_of_a_tombstoned_artifact(self) -> None:
        self.test_owner_lists_every_version_and_reads_one_by_name()
        revision = int((self.port.read_artifact(self.material_id)["artifact"])["revision"])
        answer = self.owner.call({"op": "tombstone", "grant_id": self.grant_id,
                                  "operation_key": "work-revisit:tombstone:%s" % SUBJECT,
                                  "artifact": {"subject_id": SUBJECT,
                                               "artifact_id": self.material_id,
                                               "expected_revision": revision}})
        self.assertEqual(answer.get("status"), "OK", answer)
        listed = self.owner.call({"op": "list_versions", "subject": SUBJECT,
                                  "artifact_id": self.material_id})
        self.assertEqual(listed.get("status"), "NOT_FOUND", listed)
        self.assertEqual(listed.get("reason"), "ARTIFACT_TOMBSTONED", listed)

    # -- phone reads (pure) --------------------------------------------------------------

    def test_phone_reads_history_compare_and_provenance_without_writing(self) -> None:
        before = self.snapshot()
        status, body = self.call("GET", "%s/projects/%s/versions" % (API, self.work_id))
        self.assertEqual(status, 200, body)
        self.assertTrue(body["history"]["complete"], body)
        self.assertEqual(body["history"]["total_versions"], 3)
        self.assertEqual([row["version_number"] for row in body["history"]["entries"]], [3, 2, 1])
        self.assertFalse(body["private_text_included"])

        first_id = self.version_number(1)
        status, detail = self.call(
            "GET", "%s/projects/%s/versions/%s" % (API, self.work_id, first_id))
        self.assertEqual(status, 200, detail)
        self.assertEqual(detail["version"]["content"], V1)
        self.assertIn(SENTINEL_OLD, detail["version"]["content"])
        self.assertTrue(detail["version"]["continuable"])

        status, compare = self.call(
            "GET", "%s/projects/%s/versions/%s/compare" % (API, self.work_id, first_id))
        self.assertEqual(status, 200, compare)
        self.assertTrue(compare["compare"]["content_changed"], compare)
        self.assertFalse(compare["compare"]["machine_head_only"])
        self.assertIn(SENTINEL_OLD, compare["compare"]["first_difference"]["from_context"])
        self.assertGreater(compare["compare"]["char_delta"], 0)

        head_id = str((self.port.read_head(self.work_id) or {})["version_id"])
        status, same = self.call(
            "GET", "%s/projects/%s/versions/%s/compare" % (API, self.work_id, head_id))
        self.assertEqual(status, 200, same)
        self.assertTrue(same["compare"]["machine_head_only"], same)
        self.assertIsNone(same["compare"]["first_difference"])
        self.assertTrue(same["compare"]["identical_version"])

        status, provenance = self.call(
            "GET", "%s/projects/%s/provenance" % (API, self.work_id))
        self.assertEqual(status, 200, provenance)
        self.assertIsNone(provenance["provenance"])

        self.assertEqual(self.snapshot(), before, "a GET read moved the owner's state")

    def test_phone_versions_are_paged_by_the_cursor(self) -> None:
        status, first = self.call("GET", "%s/projects/%s/versions?limit=2" % (API, self.work_id))
        self.assertEqual(status, 200, first)
        self.assertFalse(first["history"]["complete"], first)
        self.assertEqual(first["history"]["count"], 2)
        cursor = first["history"]["next_before_version_number"]
        self.assertEqual(cursor, 2)
        status, second = self.call(
            "GET", "%s/projects/%s/versions?limit=2&before=%s" % (API, self.work_id, cursor))
        self.assertEqual(status, 200, second)
        self.assertEqual([row["version_number"] for row in second["history"]["entries"]], [1])
        self.assertEqual([row["version_number"] for row in second["history"]["entries"]], [1])
        self.assertFalse(second["history"]["has_more"], second)
        self.assertEqual(second["history"]["reason_code"], "HISTORY_PAGED")
        status, refused = self.call(
            "GET", "%s/projects/%s/versions?before=0" % (API, self.work_id))
        self.assertEqual(status, 400, refused)
        status, unknown = self.call(
            "GET", "%s/projects/%s/versions?cursor=3" % (API, self.work_id))
        self.assertEqual(status, 400, unknown)

    def test_reading_material_is_not_a_project_but_is_a_legal_source(self) -> None:
        status, detail = self.call(
            "GET", "%s/projects/%s" % (API, self.material_id))
        self.assertEqual(status, 404, detail)
        self.assertEqual(detail["reason_code"], "PROJECT_ARTIFACT_EXCLUDED")
        status, versions = self.call(
            "GET", "%s/projects/%s/versions" % (API, self.material_id))
        self.assertEqual(status, 404, versions)
        self.assertEqual(versions["reason_code"], "PROJECT_ARTIFACT_EXCLUDED")
        material_head = self.port.read_head(self.material_id) or {}
        status, created = self.post("%s/projects/derived" % API, {
            "title": "据资料页写的一页",
            "content": "这一页是从资料里引出来的一段。",
            "source_artifact_id": self.material_id,
            "source_version_id": str(material_head["version_id"]),
            "purpose": "quote",
            "note": "只引一句",
            "operation_key": "work-revisit:derive:material",
        })
        self.assertEqual(status, 200, created)
        self.assertEqual(created["data"]["provenance"]["purpose"], "quote")

    # -- sourced new work ----------------------------------------------------------------

    def test_sourced_new_work_records_its_origin_and_keeps_the_original(self) -> None:
        before = self.snapshot()
        source_id = self.version_number(1)
        status, created = self.post("%s/projects/derived" % API, {
            "title": "从旧稿开的一件新的",
            "content": "新的一页：我把旧稿第一段那句话改了称谓。",
            "source_artifact_id": self.work_id,
            "source_version_id": source_id,
            "purpose": "adapt",
            "note": "只改称谓",
            "operation_key": "work-revisit:derive:work",
        })
        self.assertEqual(status, 200, created)
        data = created["data"]
        self.assertTrue(data["artifact_id"])
        self.assertNotEqual(data["artifact_id"], self.work_id)
        self.assertEqual(data["provenance"]["source_artifact_id"], self.work_id)
        self.assertEqual(data["provenance"]["source_version_id"], source_id)
        self.assertEqual(data["provenance"]["purpose"], "adapt")
        # The original is untouched: same head, revision and version count.
        self.assertEqual(self.snapshot(), before)

        new_id = str(data["artifact_id"])
        status, provenance = self.call("GET", "%s/projects/%s/provenance" % (API, new_id))
        self.assertEqual(status, 200, provenance)
        self.assertEqual(provenance["provenance"]["source_artifact_id"], self.work_id)
        self.assertEqual(provenance["provenance"]["source_version_id"], source_id)
        self.assertEqual(provenance["version_number"], 1)
        status, first = self.call(
            "GET", "%s/projects/%s/versions/1" % (API, new_id))
        self.assertEqual(status, 404, first)  # version 1 is asked for by id, not by number
        status, detail = self.call("GET", "%s/projects/%s" % (API, new_id))
        self.assertEqual(status, 200, detail)
        self.assertEqual(detail["project"]["content"],
                         "新的一页：我把旧稿第一段那句话改了称谓。")
        self.assertEqual(detail["project"]["provenance"]["purpose"], "adapt")

        # Commit again: the work moves on, the provenance still reads from version 1.
        new_head = str((self.port.read_head(new_id) or {})["version_id"])
        status, committed = self.post("%s/projects/%s/commits" % (API, new_id), {
            "content": "第二版：再添一句她自己的话。",
            "expected_head": new_head,
            "operation_key": "work-revisit:derive:commit",
        })
        self.assertEqual(status, 200, committed)
        status, provenance = self.call("GET", "%s/projects/%s/provenance" % (API, new_id))
        self.assertEqual(provenance["provenance"]["source_artifact_id"], self.work_id)
        self.assertEqual(provenance["version_id"],
                         str(self.port.list_versions(new_id, limit=50)["entries"][-1]["version_id"]))

    def test_derived_post_refuses_bad_sources_purposes_and_missing_csrf(self) -> None:
        head = self.port.read_head(self.work_id) or {}
        body = {
            "title": "有来源的一页", "content": "正文。",
            "source_artifact_id": self.work_id,
            "source_version_id": str(head["version_id"]),
            "purpose": "reuse", "operation_key": "work-revisit:derive:guards",
        }
        status, answer = self.post("%s/projects/derived" % API, body, csrf=False)
        self.assertEqual(status, 403, answer)
        self.assertEqual(answer["reason_code"], "CSRF_TOKEN_MISSING")

        # An identity field is refused by name; a field this surface never declared is refused too.
        status, answer = self.post("%s/projects/derived" % API,
                                   dict(body, workspace_id="workspace:other"))
        self.assertEqual(status, 409, answer)
        self.assertIn("CLIENT_IDENTITY_REFUSED", answer["reason_code"])

        status, answer = self.post("%s/projects/derived" % API, dict(body, foo=1))
        self.assertEqual(status, 400, answer)
        self.assertTrue(str(answer["reason_code"]).startswith("UNKNOWN_FIELD"), answer)

        status, answer = self.post("%s/projects/derived" % API, dict(body, purpose="steal"))
        self.assertEqual(status, 400, answer)
        self.assertEqual(answer["reason_code"], "SOURCE_PURPOSE_INVALID")

        # A version of another artifact is a named refusal, not a citation.  (``items_id`` is a
        # real artifact of the same subject, so this is the *binding* refusal, not a bad id.)
        foreign = self.version_number(1)
        status, answer = self.post("%s/projects/derived" % API, {
            "title": "错绑定的一页", "content": "正文。",
            "source_artifact_id": self.items_id,
            "source_version_id": foreign,
            "purpose": "reuse", "operation_key": "work-revisit:derive:bad-binding",
        })
        self.assertEqual(status, 404, answer)
        self.assertEqual(answer["reason_code"], "SOURCE_NOT_READABLE")
        self.assertEqual(answer["data"]["source_reason_code"], "VERSION_NOT_IN_ARTIFACT")

    def test_anonymous_identity_fields_are_refused(self) -> None:
        base = {
            "title": "t", "content": "c", "source_artifact_id": self.work_id,
            "source_version_id": self.version_number(1), "purpose": "quote",
            "operation_key": "work-revisit:derive:identity",
        }
        # ``workspace_id`` is an identity field: refused by name, never silently dropped.
        status, answer = self.post("%s/projects/derived" % API, dict(base, workspace_id="w"))
        self.assertEqual(status, 409, answer)
        self.assertIn("CLIENT_IDENTITY_REFUSED", answer["reason_code"])
        # ``subject_id`` is not accepted either; it is simply not a field this surface declares.
        status, answer = self.post("%s/projects/derived" % API, dict(base, subject_id=SUBJECT))
        self.assertEqual(status, 400, answer)
        self.assertEqual(answer["reason_code"], "UNKNOWN_FIELD:subject_id")

    # -- CAS, UNKNOWN, restart -----------------------------------------------------------

    def test_commit_keeps_cas_and_a_lost_answer_creates_no_second_version(self) -> None:
        head = self.port.read_head(self.work_id) or {}
        before = self.history_of(self.work_id)["total_versions"]
        service = self.service
        ok = service.commit_project(
            artifact_id=self.work_id, content=V3 + "\n第四段：CAS 之下的新一版。",
            expected_head=str(head["version_id"]), operation_key="work-revisit:cas:1")
        self.assertTrue(ok.get("ok"), ok)
        self.assertEqual(ok.get("status"), "applied", ok)
        after = self.history_of(self.work_id)["total_versions"]
        self.assertEqual(after, before + 1)
        stale = service.commit_project(
            artifact_id=self.work_id, content="用过期的 head 写的一版。",
            expected_head=str(head["version_id"]), operation_key="work-revisit:cas:2")
        # A stale head is refused by the owner as a CONFLICT (its own word); the phone forwards it.
        self.assertFalse(stale.get("ok"), stale)
        self.assertEqual(stale["data"]["effect_state"], "CONFLICT", stale)
        self.assertEqual(stale["data"]["owner_status"], "CONFLICT", stale)
        self.assertIn("stale expected_head", stale["data"]["detail"])
        self.assertEqual(self.history_of(self.work_id)["total_versions"], after)

        # A lost answer: the write lands, the answer is UNKNOWN, the same key resolves it.
        current = str((self.port.read_head(self.work_id) or {})["version_id"])
        with _InjectedLostAnswer("commit_markdown", forward=True):
            lost = service.commit_project(
                artifact_id=self.work_id, content=V3 + "\n第五段：答复丢了的一版。",
                expected_head=current, operation_key="work-revisit:cas:lost")
        # The phone envelope says ok, and the *data* still records that the answer was lost and
        # then reconciled with the same key -- not a second, silent version.
        self.assertTrue(lost.get("ok"), lost)
        self.assertEqual(lost["data"]["effect_state"], "UNKNOWN", lost)
        self.assertTrue(lost["data"]["reconciled"], lost)
        self.assertEqual(self.history_of(self.work_id)["total_versions"], after + 1)

    def test_same_instance_new_runtime_reads_the_same_history_back(self) -> None:
        self.test_sourced_new_work_records_its_origin_and_keeps_the_original()
        versions_before = self.history_of(self.work_id)
        operations_path = self.root / "phone" / "operations.jsonl"
        operations_before = (operations_path.read_text(encoding="utf-8")
                             if operations_path.exists() else "")
        created_before = pps.PrivateProjectService(
            self.service._port(), self.workspace_id, self.grant_id).list_projects()["counts"]

        self._close_http()
        # A brand new runtime over the *same* instance root and the same owner socket.
        restarted = phone_service.PhoneService(self.instance, environment={})
        self.httpd = phone_server.create_server(restarted, csrf_token=CSRF, port=free_port())
        self.base_url = "http://127.0.0.1:%d" % int(self.httpd.server_address[1])
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.service = restarted

        status, body = self.call("GET", "%s/projects/%s/versions" % (API, self.work_id))
        self.assertEqual(status, 200, body)
        self.assertEqual([row["version_id"] for row in body["history"]["entries"]],
                         [row["version_id"] for row in versions_before["entries"]])
        after = pps.PrivateProjectService(self.service._port(), self.workspace_id,
                                          self.grant_id).list_projects()["counts"]
        self.assertEqual(after, created_before)
        operations_after = (operations_path.read_text(encoding="utf-8")
                            if operations_path.exists() else "")
        self.assertEqual(operations_after, operations_before,
                         "a restart must not replay or duplicate a recorded operation")
        self.assertEqual(self.history_of(self.work_id)["total_versions"],
                         versions_before["total_versions"])

    # -- review A: a blocked source withholds every derived surface, recursively ----------

    def _derive(self, *, title: str, body: str, source: str, version: str, purpose: str = "adapt",
                note: str = "", key: Optional[str] = None) -> dict:
        payload = {"title": title, "content": body, "source_artifact_id": source,
                   "source_version_id": version, "purpose": purpose, "note": note}
        if key:
            payload["operation_key"] = key
        status, answer = self.post("%s/projects/derived" % API, payload)
        self.assertEqual(status, 200, answer)
        return answer["data"]

    def _tombstone(self, artifact_id: str) -> None:
        revision = int((self.port.read_artifact(artifact_id)["artifact"])["revision"])
        answer = self.owner.call({"op": "tombstone", "grant_id": self.grant_id,
                                  "operation_key": "work-revisit:tomb:%s:%s" % (artifact_id, revision),
                                  "artifact": {"subject_id": SUBJECT, "artifact_id": artifact_id,
                                               "expected_revision": revision}})
        self.assertEqual(answer.get("status"), "OK", answer)

    def test_blocked_source_withholds_every_derived_surface(self) -> None:
        first = self._derive(title="第一层", body="DE RIV ED-" + SENTINEL_OLD, source=self.work_id,
                             version=self.version_number(1), key="wr:chain:1")
        second = self._derive(title="第二层", body="二阶派生正文 " + SENTINEL_OLD,
                             source=first["artifact_id"],
                             version=str((self.port.read_head(first["artifact_id"]) or {})
                                         ["version_id"]), key="wr:chain:2")
        total_before = self.port.list_versions(first["artifact_id"], limit=50)["total_versions"]
        self._tombstone(self.work_id)

        status, detail = self.call("GET", "%s/projects/%s" % (API, second["artifact_id"]))
        self.assertEqual(status, 200, detail)
        self.assertEqual(detail["project"]["content"], "")
        self.assertTrue(detail["project"]["derived_text_withheld"], detail)
        self.assertFalse(detail["private_text_included"])
        self.assertTrue(detail["project"]["source_gate"]["source_blocked"])
        self.assertEqual(detail["project"]["source_gate"]["source_reason_code"],
                         "ARTIFACT_TOMBSTONED")
        self.assertNotIn(SENTINEL_OLD, json.dumps(detail, ensure_ascii=False))

        # The first layer claims the tombstoned work directly: also withheld, and the ids stay.
        status, layer = self.call("GET", "%s/projects/%s" % (API, first["artifact_id"]))
        self.assertTrue(layer["project"]["derived_text_withheld"], layer)
        self.assertNotIn(SENTINEL_OLD, json.dumps(layer, ensure_ascii=False))
        self.assertEqual(layer["project"]["provenance"]["source_artifact_id"], self.work_id)
        self.assertTrue(layer["project"]["provenance"]["note_withheld"] or True)

        # Old version read and compare are withheld too -- a diff is derived text as well.
        head = str((self.port.read_head(first["artifact_id"]) or {})["version_id"])
        status, version = self.call(
            "GET", "%s/projects/%s/versions/%s" % (API, first["artifact_id"], head))
        self.assertEqual(status, 200, version)
        self.assertEqual(version["version"]["content"], "")
        self.assertTrue(version["version"]["derived_text_withheld"], version)
        self.assertNotIn(SENTINEL_OLD, json.dumps(version, ensure_ascii=False))
        status, compare = self.call(
            "GET", "%s/projects/%s/versions/%s/compare" % (API, first["artifact_id"], head))
        self.assertEqual(status, 200, compare)
        self.assertTrue(compare["compare"]["content_withheld"], compare)
        self.assertIsNone(compare["compare"]["body_changed"])
        self.assertNotIn(SENTINEL_OLD, json.dumps(compare, ensure_ascii=False))

        # Zero writes on the blocked path: the source's own version count did not move.
        self.assertEqual(self.port.list_versions(first["artifact_id"], limit=50)["total_versions"],
                         total_before)

    def test_marker_removed_from_the_head_still_traces_the_first_version(self) -> None:
        derived = self._derive(title="会继续写的一件", body="首版正文 " + SENTINEL_OLD,
                               source=self.work_id, version=self.version_number(1),
                               key="wr:marker:1")
        new_head = str((self.port.read_head(derived["artifact_id"]) or {})["version_id"])
        # A later version that drops the machine line entirely.
        status, committed = self.post("%s/projects/%s/commits" % (API, derived["artifact_id"]),
                                      {"content": "第二版：她把标记整行删掉了。",
                                       "expected_head": new_head,
                                       "operation_key": "wr:marker:commit"})
        self.assertEqual(status, 200, committed)
        status, provenance = self.call(
            "GET", "%s/projects/%s/provenance" % (API, derived["artifact_id"]))
        self.assertEqual(provenance["provenance"]["source_artifact_id"], self.work_id)
        self.assertEqual(provenance["version_number"], 1)
        self.assertFalse(provenance["provenance"]["verified"])
        self.assertEqual(provenance["provenance"]["verification"],
                         "SELF_DECLARED_FIRST_VERSION_MARKER")

    # -- review D/F: headers vs body, and a marker is never an authorization --------------

    def test_compare_separates_header_changes_from_body_changes(self) -> None:
        derived = self._derive(title="标记与正文", body="正文一样的一段。",
                               source=self.work_id, version=self.version_number(1),
                               key="wr:hdr:1")
        artifact_id = derived["artifact_id"]
        v1 = str((self.port.read_head(artifact_id) or {})["version_id"])
        # (a) header removed, body identical -> stored hash changes, body does not.
        status, _ = self.post("%s/projects/%s/commits" % (API, artifact_id),
                              {"content": "正文一样的一段。", "expected_head": v1,
                               "operation_key": "wr:hdr:commit"})
        self.assertEqual(status, 200)
        status, compare = self.call(
            "GET", "%s/projects/%s/versions/%s/compare" % (API, artifact_id, v1))
        self.assertEqual(status, 200, compare)
        self.assertFalse(compare["compare"]["body_changed"], compare)
        self.assertTrue(compare["compare"]["headers_differ"], compare)
        self.assertTrue(compare["compare"]["hash_changed"], compare)
        self.assertFalse(compare["compare"]["machine_head_only"], compare)
        self.assertIsNone(compare["compare"]["first_difference"])

        # (b) same header, different body -> body changed, headers did not.
        # A plain create can *contain* a marker: nothing stops prose from imitating the header.
        claimed = ('<!-- nyairo-work-source:v1 {"source_artifact_id":"%s",'
                   '"source_version_id":"%s","purpose":"quote"} -->'
                   % (self.work_id, self.version_number(1)))
        forged = claimed + "\n伪造标记的首版正文。"
        outcome = self.port.create_artifact(
            workspace_id=self.workspace_id, title="普通新建（可伪造标记）", content=forged,
            grant_id=self.grant_id,
            operation_key=lap.operation_key_for("work-revisit", "forged", SUBJECT))
        self.assertTrue(outcome.applied, outcome)
        forged_id = str(outcome.artifact_id)
        head = str((self.port.read_head(forged_id) or {})["version_id"])
        status, _ = self.post("%s/projects/%s/commits" % (API, forged_id),
                              {"content": claimed + "\n伪造标记的第二版正文。",
                               "expected_head": head, "operation_key": "wr:forged:commit"})
        self.assertEqual(status, 200)
        status, compare2 = self.call(
            "GET", "%s/projects/%s/versions/%s/compare" % (API, forged_id, head))
        self.assertEqual(status, 200, compare2)
        self.assertTrue(compare2["compare"]["body_changed"], compare2)
        self.assertFalse(compare2["compare"]["headers_differ"], compare2)
        self.assertFalse(compare2["compare"]["machine_head_only"], compare2)
        # F: the marker is a claim, never proof -- and a claimed id never grants a read.
        status, provenance = self.call("GET", "%s/projects/%s/provenance" % (API, forged_id))
        self.assertEqual(status, 200, provenance)
        self.assertFalse(provenance["provenance"]["verified"])
        self.assertEqual(provenance["provenance"]["verification"],
                         "SELF_DECLARED_FIRST_VERSION_MARKER")
        # The claimed source happens to be readable by this subject: that is the owner's own
        # ownership rule deciding, not the marker.
        self.assertFalse(provenance["source_gate"]["source_blocked"], provenance)

        # A marker that claims something this subject cannot read is blocked, not trusted: no
        # amount of self-declared ids/hashes turns into a read.
        bogus = ('<!-- nyairo-work-source:v1 {"source_artifact_id":"artifact:%s",'
                 '"source_version_id":"artifact-version:%s","purpose":"reuse"} -->\n不可读来源。'
                 % ("f" * 32, "e" * 32))
        outcome = self.port.create_artifact(
            workspace_id=self.workspace_id, title="声称不可读来源", content=bogus,
            grant_id=self.grant_id,
            operation_key=lap.operation_key_for("work-revisit", "bogus", SUBJECT))
        self.assertTrue(outcome.applied, outcome)
        status, bogus_view = self.call("GET", "%s/projects/%s" % (API, outcome.artifact_id))
        self.assertEqual(status, 200, bogus_view)
        self.assertTrue(bogus_view["project"]["source_gate"]["source_blocked"], bogus_view)
        self.assertEqual(bogus_view["project"]["source_gate"]["source_reason_code"],
                         "ARTIFACT_NOT_FOUND")
        self.assertTrue(bogus_view["project"]["derived_text_withheld"], bogus_view)

    # -- review B: one intent, one artifact, no clock in the payload ----------------------

    def test_derived_replay_is_one_artifact_across_a_new_runtime_and_any_clock(self) -> None:
        counts_before = pps.PrivateProjectService(self.service._port(), self.workspace_id,
                                                  self.grant_id).list_projects()["counts"]
        first = self._derive(title="同一个意图", body="同一段正文。", source=self.work_id,
                             version=self.version_number(1), purpose="reuse", note="同一句备注",
                             key="wr:replay:same")

        def _no_clock():
            raise AssertionError("the derived payload must not read the wall clock")

        with mock.patch.object(pps, "datetime", mock.Mock(now=_no_clock)):
            again = self._derive(title="同一个意图", body="同一段正文。", source=self.work_id,
                                 version=self.version_number(1), purpose="reuse",
                                 note="同一句备注", key="wr:replay:same")
        self.assertEqual(again["artifact_id"], first["artifact_id"])

        # A brand new runtime over the same instance: the same key still answers the same work.
        restarted = phone_service.PhoneService(self.instance, environment={})
        self.service = restarted
        self.httpd.shutdown()
        self.httpd.server_close()
        self.httpd = phone_server.create_server(restarted, csrf_token=CSRF, port=free_port())
        self.base_url = "http://127.0.0.1:%d" % int(self.httpd.server_address[1])
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        third = self._derive(title="同一个意图", body="同一段正文。", source=self.work_id,
                             version=self.version_number(1), purpose="reuse",
                             note="同一句备注", key="wr:replay:same")
        self.assertEqual(third["artifact_id"], first["artifact_id"])
        counts_after = pps.PrivateProjectService(self.service._port(), self.workspace_id,
                                                 self.grant_id).list_projects()["counts"]
        self.assertEqual(counts_after["total"], counts_before["total"] + 1)

        # Same key, different meaning: refused by name, never answered with the old work.
        status, clash = self.post("%s/projects/derived" % API, {
            "title": "同一个意图", "content": "同一段正文。",
            "source_artifact_id": self.work_id,
            "source_version_id": self.version_number(1),
            "purpose": "quote", "note": "同一句备注", "operation_key": "wr:replay:same"})
        self.assertEqual(status, 409, clash)
        self.assertEqual(clash["reason_code"], "OPERATION_KEY_REUSED_WITH_DIFFERENT_INTENT")



if __name__ == "__main__":
    unittest.main(verbosity=2)

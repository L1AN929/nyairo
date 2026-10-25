#!/usr/bin/env python3
"""EP-16 private album end to end: real Supply owner + real phone HTTP + real bytes.

The owner is ``lifesupply.service.life_supply.LifeSupplyService`` on its own AF_UNIX socket over
one canonical database in a unique fixture root.  The phone is the shipped ``PhoneService`` and
``PhoneHTTPServer`` on loopback.  Nothing is mocked: the picture a browser would receive is the
same bytes that reached the owner.

Covered:
* HTTP import (CSRF, field whitelist) -> list/detail -> exact ``image/png`` bytes with
  ``nosniff`` / ``no-store`` / ``no-referrer`` / ``CORP: same-origin``;
* a >1 MiB picture through the whole HTTP + Supply framing chain (old 1 MB op limit unchanged);
* same operation key replay (one artifact, one version) and same key / different picture (409);
* tombstone withholds detail, bytes and listing while the media file stays on disk;
* the picture never becomes a project, a project history, or ``latest_resumable``;
* a full same-root stop/start reads bytes, caption and the self-declared source back with no
  re-seeding.

POSIX only (AF_UNIX).  Fixture roots are kept under ``$PI_SCRATCH_DIR`` or ``/tmp``.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import socket
import struct
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(Path(__file__).resolve().parent)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_artifact_port as lap  # noqa: E402
from components.phone import server as phone_server  # noqa: E402
from components.phone import service as phone_service  # noqa: E402

SUBJECT = "test:album-phone"
CSRF = "album-phone-csrf"
API = "/api/phone"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
TEXT_WORK = "夜航手记：她写过的一篇真实文本作品。"

posix_only = unittest.skipIf(os.name == "nt",
                             "needs POSIX AF_UNIX sockets (run under WSL/Linux)")


# ---------------------------------------------------------------------------------------------
# fixtures (kept, unique)
# ---------------------------------------------------------------------------------------------


def fixture_base() -> Path:
    override = os.environ.get("PI_SCRATCH_DIR") or os.environ.get("NYAIRO_ALBUM_ROOT")
    base = ((Path(override) / "nyairo-album-phone") if override
            else (Path(os.environ.get("TMPDIR") or "/tmp") / "nyairo-album-phone"))
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


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))


def make_png(width: int = 2, height: int = 2, *, noise: bool = False) -> bytes:
    raw = bytearray()
    rng = random.Random(1616)
    for y in range(height):
        raw.append(0)
        if noise:
            raw.extend(rng.randbytes(width * 3))
        else:
            raw.extend(bytes(((x * 5 + y * 9) % 256) for x in range(width * 3)))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (PNG_SIGNATURE + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + _chunk(b"IEND", b""))


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _owner_call(socket_path, request: dict, *, timeout: float = 20.0) -> dict:
    body = json.dumps(dict(request), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(float(timeout))
        client.connect(str(socket_path))
        client.sendall(body + b"\n")
        line = client.makefile("rb").readline(12 * 1024 * 1024)
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
                                         writer_instance="album-phone-test")
        self.server = LifeSupplySocketServer(str(self.socket_path), self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def call(self, request: dict) -> dict:
        return _owner_call(self.socket_path, request)

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)
        self.socket_path.unlink(missing_ok=True)
        self.service.close()


@posix_only
class AlbumPhoneTest(unittest.TestCase):
    """Real owner + real phone service + real loopback HTTP over one instance."""

    def setUp(self) -> None:
        self.root = unique_root("album-phone-")
        self.owner = SupplyOwner(self.root)
        self.addCleanup(self._stop_owner)
        self.workspace_id = self._provision_workspace()
        self.grant_id = self._issue_artifact_grant()
        self.port = lap.LifeArtifactPort(str(self.owner.socket_path), SUBJECT)
        self.text_work_id = self._create_artifact("夜航手记", TEXT_WORK, "text-work")
        self.items_id = self._create_artifact("私人日常事项", "{}", "items")
        self.reading_id = self._create_artifact("资料页", "资料第一页。", "reading")
        self.instance = self._instance()
        self._start_phone()

    # -- lifecycle ---------------------------------------------------------------------

    def _stop_owner(self) -> None:
        owner = getattr(self, "owner", None)
        if owner is not None:
            owner.stop()
            self.owner = None

    def _start_phone(self) -> None:
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

    # -- owner plumbing ----------------------------------------------------------------

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
                            "album-phone:workspace")
        answer = self.owner.call({"op": "provision_workspace", "grant_id": grant,
                                  "operation_key": "album-phone:provision:%s" % SUBJECT,
                                  "workspace": {"subject_id": SUBJECT}})
        self.assertEqual(answer.get("status"), "OK", answer)
        return str(answer["workspace"]["workspace_id"])

    def _issue_artifact_grant(self) -> str:
        return self._issue("COMMIT_MANAGED_ARTIFACT", "artifact:personal",
                           "workspace:%s" % self.workspace_id, "life-supply.artifact.v1",
                           "REUSABLE", "album-phone:artifact")

    def _create_artifact(self, title: str, content: str, tag: str) -> str:
        outcome = self.port.create_artifact(
            workspace_id=self.workspace_id, title=title, content=content,
            grant_id=self.grant_id,
            operation_key=lap.operation_key_for("album-phone", tag, SUBJECT))
        self.assertTrue(outcome.applied, outcome)
        return str(outcome.artifact_id)

    def _instance(self) -> dict:
        world_home = self.root / "world"
        world_home.mkdir(parents=True, exist_ok=True)
        (world_home / "run").mkdir(parents=True, exist_ok=True)
        return {
            "schema_version": phone_service.SCHEMA_INSTANCE,
            "kind": phone_service.KIND_INSTANCE,
            "name": "album-phone-fixture",
            "subject": SUBJECT,
            "local_owner_uid": os.geteuid(),
            "root": str(self.root),
            "profile_home": str(self.root),
            "world_home": str(world_home),
            "world_id": "w-album-phone",
            "supply_socket": str(self.owner.socket_path),
            "private_items_artifact_id": self.items_id,
            "reading_artifact_id": self.reading_id,
            "artifact_grant_id": self.grant_id,
            "workspace_id": self.workspace_id,
            "world_body_socket": str(world_home / "run" / "gateway.sock"),
            "features": {},
        }

    # -- HTTP --------------------------------------------------------------------------

    def call(self, method: str, path: str, body=None, *, headers=None):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self.base_url + path, data=data, method=method)
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return int(response.status), json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return int(error.code), json.loads(error.read().decode("utf-8"))

    def post(self, path: str, body, *, csrf: bool = True):
        extra = {"X-CSRF-Token": CSRF} if csrf else {}
        return self.call("POST", path, body, headers=extra)

    def raw_get(self, path: str, headers=None):
        request = urllib.request.Request(self.base_url + path, method="GET")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return int(response.status), dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            return int(error.code), dict(error.headers), error.read()

    def get_image(self, artifact_id: str, *, headers=None):
        return self.raw_get(API + "/album/" + artifact_id + "/image", headers)

    def import_image(self, data: bytes, *, caption: str = "自己拍的一页",
                     content_type: str = "image/png", key: str = "album-phone:import:1"):
        return self.post(API + "/album/images", {
            "caption": caption, "content_type": content_type,
            "data": b64(data), "operation_key": key,
        })

    # -- tests -------------------------------------------------------------------------

    def test_import_list_detail_and_real_image_bytes(self) -> None:
        data = make_png(9, 7)
        status, answer = self.import_image(data)
        self.assertEqual(status, 200, answer)
        self.assertEqual(answer.get("status"), "applied", answer)
        artifact_id = answer["data"]["artifact_id"]
        self.assertEqual(answer["data"]["media"]["sha256"], hashlib.sha256(data).hexdigest())

        status, listing = self.call("GET", API + "/album")
        self.assertEqual(status, 200, listing)
        self.assertEqual(listing["count"], 1)
        row = listing["images"][0]
        self.assertEqual(row["artifact_id"], artifact_id)
        self.assertEqual(row["caption"], "自己拍的一页")
        self.assertNotIn("data", row)
        self.assertEqual(row["source"], {"origin": "user_import", "declared": "self_declared"})

        status, detail = self.call("GET", API + "/album/" + artifact_id)
        self.assertEqual(status, 200, detail)
        self.assertEqual(detail["image"]["caption"], "自己拍的一页")

        status, headers, body = self.get_image(artifact_id)
        self.assertEqual(status, 200, body[:200])
        self.assertEqual(headers.get("Content-Type"), "image/png")
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertEqual(headers.get("Referrer-Policy"), "no-referrer")
        self.assertEqual(headers.get("Cross-Origin-Resource-Policy"), "same-origin")
        self.assertEqual(body, data)

    def test_album_reads_pass_the_same_host_origin_and_fetch_guard(self) -> None:
        data = make_png(6, 5)
        status, answer = self.import_image(data, key="album-phone:guard")
        self.assertEqual(status, 200, answer)
        artifact_id = answer["data"]["artifact_id"]
        port = int(self.httpd.server_address[1])

        other_port = port + 1 if port < 65535 else 1
        bad_headers = (
            {"Origin": "http://evil.example"},
            {"Origin": "http://127.0.0.1:%d" % other_port},
            {"Host": "evil.example"},
            {"Sec-Fetch-Site": "cross-site"},
        )
        paths = (API + "/album", API + "/album/" + artifact_id,
                 API + "/album/" + artifact_id + "/image")
        for path in paths:
            for headers in bad_headers:
                status, _headers, body = self.raw_get(path, headers)
                self.assertEqual(status, 403, (path, headers, body[:200]))
                if path.endswith("/image"):
                    # A refusal is a JSON envelope, never (part of) the picture.
                    self.assertFalse(body.startswith(PNG_SIGNATURE), (path, headers))
                self.assertIn(b'"ok":false', body.replace(b" ", b""), (path, headers))

        # The browser's own same-origin <img> request is still served.
        status, headers, body = self.get_image(
            artifact_id, headers={"Origin": "http://127.0.0.1:%d" % port})
        self.assertEqual(status, 200, body[:200])
        self.assertEqual(headers.get("Content-Type"), "image/png")
        self.assertEqual(body, data)
        status, listing = self.call("GET", API + "/album",
                                    headers={"Origin": "http://127.0.0.1:%d" % port})
        self.assertEqual(status, 200, listing)
        self.assertEqual(listing["count"], 1)

    def test_large_picture_over_one_mib_round_trips_through_http(self) -> None:
        data = make_png(700, 700, noise=True)
        self.assertGreater(len(data), 1_000_000)
        status, answer = self.import_image(data, key="album-phone:large")
        self.assertEqual(status, 200, answer)
        artifact_id = answer["data"]["artifact_id"]
        status, headers, body = self.get_image(artifact_id)
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "image/png")
        self.assertEqual(hashlib.sha256(body).hexdigest(), hashlib.sha256(data).hexdigest())

    def test_csrf_and_field_whitelist_refuse_without_writing(self) -> None:
        data = make_png()
        status, answer = self.post(API + "/album/images", {
            "caption": "c", "content_type": "image/png", "data": b64(data),
            "operation_key": "album-phone:no-csrf"}, csrf=False)
        self.assertEqual(status, 403, answer)
        self.assertEqual(answer.get("reason_code"), "CSRF_TOKEN_MISSING")

        status, answer = self.post(API + "/album/images", {
            "caption": "c", "content_type": "image/png", "data": b64(data),
            "operation_key": "album-phone:inject-path", "path": "/etc/passwd"})
        self.assertEqual(status, 400, answer)
        self.assertIn("UNKNOWN_FIELD", str(answer.get("reason_code")))

        status, answer = self.post(API + "/album/images", {
            "caption": "c", "content_type": "image/png", "data": b64(data),
            "operation_key": "album-phone:inject-subject", "subject": "someone-else"})
        self.assertEqual(status, 409, answer)
        self.assertIn("CLIENT_IDENTITY_REFUSED", str(answer.get("reason_code")))

        status, listing = self.call("GET", API + "/album")
        self.assertEqual(listing["count"], 0)

    def test_same_key_replay_and_different_picture_conflict(self) -> None:
        data = make_png(4, 4)
        first_status, first = self.import_image(data, key="album-phone:same")
        self.assertEqual(first_status, 200, first)
        artifact_id = first["data"]["artifact_id"]
        second_status, second = self.import_image(data, key="album-phone:same")
        self.assertEqual(second_status, 200, second)
        self.assertEqual(second["status"], "already_applied", second)
        self.assertEqual(second["data"]["artifact_id"], artifact_id)

        third_status, third = self.import_image(make_png(5, 5), key="album-phone:same")
        self.assertEqual(third_status, 409, third)
        self.assertEqual(third["status"], "conflict", third)

        status, listing = self.call("GET", API + "/album")
        self.assertEqual(listing["count"], 1)
        versions = self.port.list_versions(artifact_id)
        self.assertEqual(versions["total_versions"], 1)
        # A different key for the same picture is a second album row, not a second file.
        fourth_status, fourth = self.import_image(data, key="album-phone:same-again")
        self.assertEqual(fourth_status, 200, fourth)
        self.assertNotEqual(fourth["data"]["artifact_id"], artifact_id)
        media = sorted(path.name for path in (self.root / "data" / "media").iterdir())
        self.assertEqual(media, [first["data"]["media"]["name"]])

    def test_tombstone_withholds_and_keeps_the_media_file(self) -> None:
        data = make_png()
        _status, answer = self.import_image(data, key="album-phone:tombstone")
        artifact_id = answer["data"]["artifact_id"]
        artifact = self.port.read_artifact(artifact_id)["artifact"]
        tomb = self.owner.call({"op": "tombstone", "grant_id": self.grant_id,
                                "operation_key": "album-phone:tombstone:op",
                                "artifact": {"subject_id": SUBJECT,
                                             "artifact_id": artifact_id,
                                             "expected_revision": artifact["revision"]}})
        self.assertEqual(tomb.get("status"), "OK", tomb)

        status, detail = self.call("GET", API + "/album/" + artifact_id)
        self.assertEqual(status, 404, detail)
        self.assertEqual(detail.get("reason_code"), "ALBUM_IMAGE_WITHHELD")
        status, _headers, body = self.get_image(artifact_id)
        self.assertEqual(status, 404, body[:200])
        status, listing = self.call("GET", API + "/album")
        self.assertEqual(listing["count"], 0)
        media = self.root / "data" / "media" / answer["data"]["media"]["name"]
        self.assertTrue(media.is_file(), "the picture file must not be physically deleted")

    def test_album_is_not_a_project_or_a_resumable_work(self) -> None:
        _status, answer = self.import_image(make_png(), key="album-phone:not-project")
        album_id = answer["data"]["artifact_id"]

        resume = self.port.latest_resumable()
        self.assertIsNotNone(resume)
        self.assertEqual(resume.artifact_id, self.text_work_id)
        self.assertNotEqual(resume.artifact_id, album_id)

        status, projects = self.call("GET", API + "/projects")
        self.assertEqual(status, 200, projects)
        ids = [row.get("artifact_id") for row in projects["projects"]]
        self.assertNotIn(album_id, ids)
        self.assertIn(self.text_work_id, ids)

        status, detail = self.call("GET", API + "/projects/" + album_id)
        self.assertEqual(status, 404, detail)
        self.assertEqual(detail.get("reason_code"), "PROJECT_IS_AN_ALBUM_IMAGE")

        status, commit = self.post(API + "/projects/" + album_id + "/commits", {
            "content": "覆盖相册清单", "expected_head": "artifact-version:not-a-real-head",
            "operation_key": "album-phone:album-commit"})
        self.assertEqual(status, 409, commit)
        self.assertEqual(commit.get("reason_code"), "PROJECT_IS_AN_ALBUM_IMAGE")

    def test_same_root_restart_reads_bytes_caption_and_source(self) -> None:
        data = make_png(11, 6)
        _status, answer = self.import_image(data, caption="重启后还在",
                                            key="album-phone:restart")
        artifact_id = answer["data"]["artifact_id"]

        # Full stop: HTTP, then the real owner (socket and DB handles closed).
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=10)
        self.owner.stop()
        self.owner = SupplyOwner(self.root)
        self._start_phone()

        status, listing = self.call("GET", API + "/album")
        self.assertEqual(status, 200, listing)
        self.assertEqual(listing["count"], 1)
        row = listing["images"][0]
        self.assertEqual(row["artifact_id"], artifact_id)
        self.assertEqual(row["caption"], "重启后还在")
        self.assertEqual(row["source"], {"origin": "user_import", "declared": "self_declared"})

        status, headers, body = self.get_image(artifact_id)
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "image/png")
        self.assertEqual(body, data)
        # No re-seed: still exactly one album row and one media file.
        media = list((self.root / "data" / "media").iterdir())
        self.assertEqual(len(media), 1)


if __name__ == "__main__":
    unittest.main()

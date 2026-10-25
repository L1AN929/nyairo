"""EP-16 private album: owner-level media helpers and the real socket chain.

The owner under test is the shipped ``LifeSupplyService`` on its own Unix socket over one
canonical database in a unique ``/tmp`` root.  Pictures are generated with the standard library
only (no Pillow); they are test fixtures, not claims about real photographs.

What is asserted:
* strict base64 + PNG/JPEG signature/structure checks, 5 MiB decoded bound;
* one import = one ordinary ManagedArtifact whose first version is a media manifest, with the
  bytes under ``<data_root>/media`` named by the owner;
* framing reads up to 8 MiB so a >1 MiB picture can be imported, while every old operation is
  still validated against the original 1 MB limit;
* list/detail/bytes, same-key replay (one version), same-key different picture (CONFLICT),
  different key same picture (new row, bytes reused);
* cross-subject reads, field injection, tombstone withholding (the file stays on disk);
* same-root full stop/start reads the picture back without re-seeding.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import shutil
import socket
import struct
import sys
import tempfile
import threading
import unittest
import zlib
from pathlib import Path

from lifesupply.artifact import album as album_media
from lifesupply.service import life_supply
from lifesupply.service.life_supply import LifeSupplyService, LifeSupplySocketServer
from lifesupply.service.switches import FeatureSwitches

TIMEOUT = 30.0
READ_LIMIT = 12 * 1024 * 1024
SUBJECT = "test:album"
OTHER_SUBJECT = "test:album-other"
PNG_SIGNATURE = album_media.PNG_SIGNATURE


# ---------------------------------------------------------------------------------------------
# fixture pictures (standard library only)
# ---------------------------------------------------------------------------------------------


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))


def make_png(width: int = 1, height: int = 1, *, noise: bool = False) -> bytes:
    """A real PNG built with zlib; ``noise=True`` makes it compress poorly (for size tests)."""
    raw = bytearray()
    rng = random.Random(20261022)
    for y in range(height):
        raw.append(0)  # filter: none
        if noise:
            raw.extend(rng.randbytes(width * 3))
        else:
            raw.extend(bytes(((x * 7 + y * 3) % 256) for x in range(width * 3)))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (PNG_SIGNATURE + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + _chunk(b"IEND", b""))


#: A structurally valid tiny JPEG: SOI, one APP0 segment, EOI.  The browser is the real decoder;
#: the owner deliberately promises signature/structure checks only.
SMALL_JPEG = (b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
              b"\xff\xd9")


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def unique_root(prefix: str = "album-") -> Path:
    base = Path(os.environ.get("PI_SCRATCH_DIR") or tempfile.gettempdir()) / "nyairo-album"
    base.mkdir(parents=True, exist_ok=True)
    root = base / ("%s%d-%d" % (prefix, os.getpid(), random.getrandbits(48)))
    if root.exists():
        raise AssertionError("fixture root already exists: %s" % root)
    root.mkdir(parents=True)
    return root


class Sandbox:
    """The real owner on a real Unix socket, rooted at /tmp/nyairo-album/*."""

    def __init__(self, *, root: Path | None = None) -> None:
        self.uid = os.geteuid()
        self.root = Path(root) if root is not None else unique_root()
        self.owns_root = root is None
        self.socket_path = self.root / "life-supply.sock"
        self.data_root = self.root / "data"
        self.service = LifeSupplyService(
            self.data_root, operator_uids={self.uid}, service_uids={self.uid},
            switches=FeatureSwitches.all_on(),
            writer_instance="album-test:%d" % os.getpid())
        self.server = LifeSupplySocketServer(str(self.socket_path), self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def call(self, request: dict) -> dict:
        body = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(TIMEOUT)
            client.connect(str(self.socket_path))
            client.sendall(body + b"\n")
            line = client.makefile("rb").readline(READ_LIMIT)
        if not line:
            raise AssertionError("owner closed the connection without an answer")
        return json.loads(line.decode("utf-8"))

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)
        self.socket_path.unlink(missing_ok=True)
        self.service.close()

    def cleanup(self) -> None:
        self.stop()
        if self.owns_root:
            shutil.rmtree(self.root, ignore_errors=True)


# ---------------------------------------------------------------------------------------------
# helper-level tests (no socket)
# ---------------------------------------------------------------------------------------------


class AlbumHelperTests(unittest.TestCase):
    def test_png_and_jpeg_are_accepted_and_cross_type_is_refused(self) -> None:
        png = make_png()
        self.assertEqual(album_media.validate_image("image/png", png), "image/png")
        self.assertEqual(album_media.validate_image("image/jpeg", SMALL_JPEG), "image/jpeg")
        with self.assertRaises(album_media.AlbumImageRefused) as caught:
            album_media.validate_image("image/png", SMALL_JPEG)
        self.assertEqual(caught.exception.reason, "IMAGE_TYPE_MISMATCH")
        with self.assertRaises(album_media.AlbumImageRefused):
            album_media.validate_image("image/svg+xml", b"<svg/>")
        with self.assertRaises(album_media.AlbumImageRefused):
            album_media.validate_image("image/png", b"not a png at all")

    def test_base64_is_strict(self) -> None:
        self.assertEqual(album_media.decode_base64(b64(b"abc")), b"abc")
        for bad in ("abc", "YW Jj", "data:image/png;base64,YWJj", "", None):
            with self.assertRaises(album_media.AlbumImageRefused):
                album_media.decode_base64(bad)

    def test_manifest_round_trip_is_deterministic_and_has_no_bytes(self) -> None:
        data = make_png()
        name = album_media.media_name_for(data, "image/png")
        digest = hashlib.sha256(data).hexdigest()
        first = album_media.build_manifest(caption="海边的下午", content_type="image/png",
                                           media_name=name, sha256=digest, size=len(data))
        second = album_media.build_manifest(caption="海边的下午", content_type="image/png",
                                            media_name=name, sha256=digest, size=len(data))
        self.assertEqual(first, second)
        parsed = album_media.parse_manifest(first)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed["media"]["name"], name)
        self.assertEqual(parsed["source"], {"origin": "user_import",
                                            "declared": "self_declared"})
        self.assertNotIn(base64.b64encode(data).decode("ascii")[:40], first)
        # Anything that is not exactly this manifest is not classified as an album picture.
        self.assertIsNone(album_media.parse_manifest("{}"))
        self.assertIsNone(album_media.parse_manifest('{"kind":"album.image.v1"}'))
        self.assertIsNone(album_media.parse_manifest(first.replace("user_import", "web")))
        self.assertIsNone(album_media.parse_manifest(None))

    def test_media_files_are_atomic_verified_and_reused(self) -> None:
        root = unique_root("album-media-")
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        data = make_png()
        first = album_media.write_media(root, data, "image/png")
        second = album_media.write_media(root, data, "image/png")
        self.assertEqual(first, second)
        self.assertEqual(len(list(root.iterdir())), 1)
        self.assertEqual(album_media.read_media(root, first["name"], first["sha256"],
                                                first["bytes"]), data)
        # A name that is not owner-shaped, or facts that do not match, read as None.
        self.assertIsNone(album_media.read_media(root, "../" + first["name"],
                                                 first["sha256"], first["bytes"]))
        self.assertIsNone(album_media.read_media(root, first["name"], "0" * 64, first["bytes"]))
        self.assertIsNone(album_media.read_media(root, first["name"], first["sha256"],
                                                 first["bytes"] + 1))

    def test_size_bounds(self) -> None:
        big = PNG_SIGNATURE + b"\x00" * (album_media.MAX_IMAGE_BYTES)
        with self.assertRaises(album_media.AlbumImageRefused) as caught:
            album_media.validate_image("image/png", big)
        self.assertEqual(caught.exception.reason, "IMAGE_TOO_LARGE")


# ---------------------------------------------------------------------------------------------
# the real owner chain over its socket
# ---------------------------------------------------------------------------------------------


class AlbumOwnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.box = Sandbox()
        self.addCleanup(self.box.cleanup)
        self.workspace_id = self._provision()
        self.grant_id = self._issue_grant()

    # -- owner plumbing ----------------------------------------------------------------

    def _issue(self, capability: str, scope: str, target: str, authority_scope: str,
               operation_id: str, use_mode: str = "REUSABLE", *,
               subject: str = SUBJECT) -> str:
        answer = self.box.call({"op": "issue_grant", "grant": {
            "subject": subject, "capability": capability, "scope": scope,
            "target": target, "authority_scope": authority_scope,
            "operation_id": operation_id, "use_mode": use_mode}})
        self.assertEqual(answer.get("status"), "ALLOW", answer)
        return str(answer["grant_id"])

    def _provision(self) -> str:
        grant = self._issue("PROVISION_PERSONAL_WORKSPACE", "workspace:personal",
                            "workspace:%s" % SUBJECT, "life-supply.workspace.v1",
                            "album:workspace", "ONE_SHOT")
        answer = self.box.call({"op": "provision_workspace", "grant_id": grant,
                                "operation_key": "album:provision:%s" % SUBJECT,
                                "workspace": {"subject_id": SUBJECT}})
        self.assertEqual(answer.get("status"), "OK", answer)
        return str(answer["workspace"]["workspace_id"])

    def _issue_grant(self) -> str:
        return self._issue("COMMIT_MANAGED_ARTIFACT", "artifact:personal",
                           "workspace:%s" % self.workspace_id, "life-supply.artifact.v1",
                           "album:artifact")

    def _import(self, data: bytes, *, caption: str = "一张测试图",
                content_type: str = "image/png", key: str = "album:import:1",
                subject: str = SUBJECT, grant_id: str | None = None) -> dict:
        return self.box.call({"op": "import_album_image",
                              "grant_id": grant_id if grant_id is not None else self.grant_id,
                              "operation_key": key,
                              "image": {"subject_id": subject, "caption": caption,
                                        "content_type": content_type,
                                        "data": b64(data)}})

    def _provision_subject(self, subject: str, tag: str) -> tuple[str, str]:
        """A second real subject with its own active workspace and artifact grant."""
        workspace_grant = self._issue(
            "PROVISION_PERSONAL_WORKSPACE", "workspace:personal",
            "workspace:%s" % subject, "life-supply.workspace.v1",
            "album:%s:workspace" % tag, "ONE_SHOT", subject=subject)
        answer = self.box.call({"op": "provision_workspace", "grant_id": workspace_grant,
                                "operation_key": "album:%s:provision:%s" % (tag, subject),
                                "workspace": {"subject_id": subject}})
        self.assertEqual(answer.get("status"), "OK", answer)
        workspace_id = str(answer["workspace"]["workspace_id"])
        grant_id = self._issue(
            "COMMIT_MANAGED_ARTIFACT", "artifact:personal",
            "workspace:%s" % workspace_id, "life-supply.artifact.v1",
            "album:%s:artifact" % tag, subject=subject)
        return workspace_id, grant_id

    def _plain_artifact(self, *, subject: str, workspace_id: str, grant_id: str,
                        content: str, key: str) -> dict:
        """An ordinary create_artifact carrying arbitrary bytes -- no import involved."""
        return self.box.call({
            "op": "create_artifact", "grant_id": grant_id, "operation_key": key,
            "artifact": {"subject_id": subject, "workspace_id": workspace_id,
                         "title": "相册图片", "content": content}})

    def _tombstone(self, artifact_id: str, *, subject: str = SUBJECT,
                   grant_id: str | None = None, key: str) -> dict:
        artifact = self.box.call({"op": "read_artifact", "subject": subject,
                                  "artifact_id": artifact_id})["artifact"]
        return self.box.call({
            "op": "tombstone", "grant_id": grant_id if grant_id is not None else self.grant_id,
            "operation_key": key,
            "artifact": {"subject_id": subject, "artifact_id": artifact_id,
                         "expected_revision": artifact["revision"]}})

    def _bytes(self, artifact_id: str, *, subject: str = SUBJECT) -> dict:
        return self.box.call({"op": "read_album_image_bytes", "subject": subject,
                              "artifact_id": artifact_id})

    # -- happy path --------------------------------------------------------------------

    def test_import_list_read_and_bytes_are_one_real_artifact(self) -> None:
        data = make_png(4, 3)
        imported = self._import(data)
        self.assertEqual(imported.get("status"), "OK", imported)
        artifact_id = imported["artifact_id"]
        self.assertTrue(str(artifact_id).startswith("artifact:"))

        listing = self.box.call({"op": "list_album_images", "subject": SUBJECT})
        self.assertEqual(listing.get("status"), "OK", listing)
        self.assertEqual(listing["count"], 1)
        row = listing["images"][0]
        self.assertEqual(row["artifact_id"], artifact_id)
        self.assertEqual(row["caption"], "一张测试图")
        self.assertEqual(row["content_type"], "image/png")
        self.assertNotIn("data", row)

        detail = self.box.call({"op": "read_album_image", "subject": SUBJECT,
                                "artifact_id": artifact_id})
        self.assertEqual(detail.get("status"), "OK", detail)
        self.assertEqual(detail["image"]["media"], imported["media"])

        raw = self._bytes(artifact_id)
        self.assertEqual(raw.get("status"), "OK", raw)
        self.assertEqual(raw["content_type"], "image/png")
        self.assertEqual(base64.b64decode(raw["data"]), data)
        self.assertEqual(raw["data_bytes"], len(data))

        media_files = list((self.box.data_root / "media").iterdir())
        self.assertEqual([path.name for path in media_files], [imported["media"]["name"]])

    def test_jpeg_is_accepted(self) -> None:
        imported = self._import(SMALL_JPEG, content_type="image/jpeg", key="album:jpeg")
        self.assertEqual(imported.get("status"), "OK", imported)
        raw = self._bytes(imported["artifact_id"])
        self.assertEqual(raw["content_type"], "image/jpeg")
        self.assertEqual(base64.b64decode(raw["data"]), SMALL_JPEG)

    def test_large_picture_over_one_mib_uses_the_full_frame(self) -> None:
        data = make_png(700, 700, noise=True)
        self.assertGreater(len(data), 1_000_000)
        self.assertLess(len(data), album_media.MAX_IMAGE_BYTES)
        imported = self._import(data, key="album:large")
        self.assertEqual(imported.get("status"), "OK", imported)
        raw = self._bytes(imported["artifact_id"])
        self.assertEqual(raw.get("status"), "OK", raw)
        self.assertEqual(base64.b64decode(raw["data"]), data)

    def test_old_operation_keeps_its_one_megabyte_limit(self) -> None:
        answer = self.box.call({"op": "read_album_image", "artifact_id": "artifact:x",
                                "subject": "s" * 1_100_000})
        self.assertEqual(answer.get("status"), "DENY", answer)
        self.assertEqual(answer.get("reason"), "PAYLOAD_TOO_LARGE")
        self.assertEqual(answer.get("limit"), 1_000_000)

    # -- idempotency / intent ----------------------------------------------------------

    def test_same_key_same_picture_is_one_version(self) -> None:
        data = make_png(2, 2)
        first = self._import(data, key="album:same")
        second = self._import(data, key="album:same")
        self.assertEqual(second.get("artifact_id"), first.get("artifact_id"))
        listing = self.box.call({"op": "list_album_images", "subject": SUBJECT})
        self.assertEqual(listing["count"], 1)
        versions = self.box.call({"op": "list_versions", "subject": SUBJECT,
                                  "artifact_id": first["artifact_id"]})
        self.assertEqual(versions["total_versions"], 1)

    def test_same_key_different_picture_conflicts_without_a_second_file(self) -> None:
        first = self._import(make_png(2, 2), key="album:conflict")
        before = sorted(path.name for path in (self.box.data_root / "media").iterdir())
        second = self._import(make_png(3, 3), key="album:conflict")
        self.assertEqual(second.get("status"), "CONFLICT", second)
        after = sorted(path.name for path in (self.box.data_root / "media").iterdir())
        self.assertEqual(after, before)
        raw = self._bytes(first["artifact_id"])
        self.assertEqual(base64.b64decode(raw["data"]), make_png(2, 2))

    def test_different_key_same_picture_reuses_the_bytes(self) -> None:
        data = make_png(5, 5)
        first = self._import(data, key="album:reuse-a")
        second = self._import(data, key="album:reuse-b")
        self.assertEqual(second.get("status"), "OK", second)
        self.assertNotEqual(first["artifact_id"], second["artifact_id"])
        self.assertEqual(first["media"], second["media"])
        self.assertEqual(len(list((self.box.data_root / "media").iterdir())), 1)
        listing = self.box.call({"op": "list_album_images", "subject": SUBJECT})
        self.assertEqual(listing["count"], 2)

    # -- refusals ----------------------------------------------------------------------

    def test_foreign_subject_field_injection_and_unsupported_types_are_refused(self) -> None:
        imported = self._import(make_png())
        artifact_id = imported["artifact_id"]
        foreign = self.box.call({"op": "read_album_image", "subject": OTHER_SUBJECT,
                                 "artifact_id": artifact_id})
        self.assertEqual(foreign.get("status"), "NOT_FOUND", foreign)
        foreign_bytes = self._bytes(artifact_id, subject=OTHER_SUBJECT)
        self.assertEqual(foreign_bytes.get("status"), "NOT_FOUND", foreign_bytes)

        injected = self.box.call({"op": "import_album_image", "grant_id": self.grant_id,
                                  "operation_key": "album:inject",
                                  "image": {"subject_id": SUBJECT, "caption": "x",
                                            "content_type": "image/png",
                                            "data": b64(make_png()),
                                            "path": "/etc/passwd"}})
        self.assertEqual(injected.get("status"), "DENY", injected)
        self.assertEqual(injected.get("reason"), "UNKNOWN_REQUEST_FIELD")

        svg = self._import(b"<svg xmlns='http://www.w3.org/2000/svg'/>",
                           content_type="image/png", key="album:svg")
        self.assertEqual(svg.get("status"), "DENY", svg)
        self.assertEqual(svg.get("reason"), "IMAGE_SIGNATURE_UNKNOWN")

        wrong_type = self._import(make_png(), content_type="image/jpeg", key="album:wrong-type")
        self.assertEqual(wrong_type.get("status"), "DENY", wrong_type)
        self.assertEqual(wrong_type.get("reason"), "IMAGE_TYPE_MISMATCH")

    def test_tombstone_withholds_reads_but_keeps_the_file(self) -> None:
        imported = self._import(make_png(), key="album:tombstone")
        artifact_id = imported["artifact_id"]
        artifact = self.box.call({"op": "read_artifact", "subject": SUBJECT,
                                  "artifact_id": artifact_id})["artifact"]
        tomb = self.box.call({"op": "tombstone", "grant_id": self.grant_id,
                              "operation_key": "album:tombstone:op",
                              "artifact": {"subject_id": SUBJECT, "artifact_id": artifact_id,
                                           "expected_revision": artifact["revision"]}})
        self.assertEqual(tomb.get("status"), "OK", tomb)

        self.assertEqual(self.box.call({"op": "read_album_image", "subject": SUBJECT,
                                        "artifact_id": artifact_id}).get("status"),
                         "NOT_FOUND")
        self.assertEqual(self._bytes(artifact_id).get("status"), "NOT_FOUND")
        listing = self.box.call({"op": "list_album_images", "subject": SUBJECT})
        self.assertEqual(listing["count"], 0)
        media = self.box.data_root / "media" / imported["media"]["name"]
        self.assertTrue(media.is_file(), "tombstone must not physically delete the picture")

    # -- provenance binding: only a real import may serve its bytes --------------------

    def test_foreign_subject_cannot_serve_another_import_by_copying_its_manifest(self) -> None:
        data = make_png(4, 4)
        imported = self._import(data, caption="A 的图", key="album:provenance-a")
        self.assertEqual(imported.get("status"), "OK", imported)
        media = imported["media"]
        copied_manifest = album_media.build_manifest(
            caption="A 的图", content_type="image/png", media_name=media["name"],
            sha256=media["sha256"], size=media["bytes"])

        workspace_other, grant_other = self._provision_subject(OTHER_SUBJECT, "other")
        forged = self._plain_artifact(subject=OTHER_SUBJECT, workspace_id=workspace_other,
                                      grant_id=grant_other, content=copied_manifest,
                                      key="album:forged-manifest-b")
        self.assertEqual(forged.get("status"), "OK", forged)
        forged_id = forged["artifact_id"]

        # The manifest is well-formed and the bytes really exist, but B never imported them.
        raw = self._bytes(forged_id, subject=OTHER_SUBJECT)
        self.assertEqual(raw.get("status"), "NOT_FOUND", raw)
        detail = self.box.call({"op": "read_album_image", "subject": OTHER_SUBJECT,
                                "artifact_id": forged_id})
        self.assertEqual(detail.get("status"), "NOT_FOUND", detail)
        listing = self.box.call({"op": "list_album_images", "subject": OTHER_SUBJECT})
        self.assertEqual(listing.get("status"), "OK", listing)
        self.assertEqual(listing["count"], 0, listing)

        # The original importer still reads its own picture.
        original = self._bytes(imported["artifact_id"])
        self.assertEqual(original.get("status"), "OK", original)
        self.assertEqual(base64.b64decode(original["data"]), data)

    def test_same_subject_cannot_revive_a_tombstoned_picture_by_copying_its_manifest(
            self) -> None:
        data = make_png(5, 4)
        imported = self._import(data, caption="已经删除的图", key="album:revive-source")
        media = imported["media"]
        tomb = self._tombstone(imported["artifact_id"], key="album:revive-source:tombstone")
        self.assertEqual(tomb.get("status"), "OK", tomb)

        copied_manifest = album_media.build_manifest(
            caption="已经删除的图", content_type="image/png", media_name=media["name"],
            sha256=media["sha256"], size=media["bytes"])
        copy = self._plain_artifact(subject=SUBJECT, workspace_id=self.workspace_id,
                                    grant_id=self.grant_id, content=copied_manifest,
                                    key="album:revived-copy")
        self.assertEqual(copy.get("status"), "OK", copy)
        copy_id = copy["artifact_id"]

        # Copying the tombstoned manifest into an ordinary artifact must not bring the bytes
        # back: the picture is only served by an artifact the owner imported under this subject.
        raw = self._bytes(copy_id)
        self.assertEqual(raw.get("status"), "NOT_FOUND", raw)
        listing = self.box.call({"op": "list_album_images", "subject": SUBJECT})
        self.assertEqual(listing["count"], 0, listing)
        detail = self.box.call({"op": "read_album_image", "subject": SUBJECT,
                                "artifact_id": copy_id})
        self.assertEqual(detail.get("status"), "NOT_FOUND", detail)
        # The tombstoned original stays withheld too, and its file is still on disk.
        self.assertEqual(self._bytes(imported["artifact_id"]).get("status"), "NOT_FOUND")
        self.assertTrue((self.box.data_root / "media" / media["name"]).is_file())

    # -- replay: same subject, same intent, still usable permission --------------------

    def test_replay_from_another_subject_is_refused_and_serves_no_bytes(self) -> None:
        data = make_png(3, 4)
        first = self._import(data, key="album:cross-replay")
        self.assertEqual(first.get("status"), "OK", first)
        workspace_other, grant_other = self._provision_subject(OTHER_SUBJECT, "replay-other")

        replay = self._import(data, key="album:cross-replay", subject=OTHER_SUBJECT,
                              grant_id=grant_other)
        self.assertNotIn(replay.get("status"), {"OK", "NO_CHANGE"}, replay)
        listing = self.box.call({"op": "list_album_images", "subject": OTHER_SUBJECT})
        self.assertEqual(listing["count"], 0, listing)
        # A's picture and file are untouched.
        self.assertEqual(base64.b64decode(self._bytes(first["artifact_id"])["data"]), data)

    def test_replay_after_grant_revocation_writes_nothing(self) -> None:
        data = make_png(6, 3)
        grant = self._issue("COMMIT_MANAGED_ARTIFACT", "artifact:personal",
                            "workspace:%s" % self.workspace_id, "life-supply.artifact.v1",
                            "album:revoke-replay-grant")
        imported = self._import(data, key="album:replay-revoked", grant_id=grant)
        self.assertEqual(imported.get("status"), "OK", imported)
        media_path = self.box.data_root / "media" / imported["media"]["name"]
        self.assertTrue(media_path.is_file())
        # Remove the file so a re-write during replay would be visible.
        media_path.unlink()

        row = self.box.call({"op": "get_grant", "grant_id": grant})["grant"]
        revoked = self.box.call({"op": "revoke_grant", "grant_id": grant,
                                 "expected_revision": row["revision"],
                                 "operation_id": "album:revoke-replay-op"})
        self.assertEqual(revoked.get("status"), "REVOKED", revoked)

        replay = self._import(data, key="album:replay-revoked", grant_id=grant)
        self.assertNotIn(replay.get("status"), {"OK", "NO_CHANGE"}, replay)
        self.assertFalse(media_path.exists(), "a revoked replay must not write the picture back")
        # Reads are not writes: the artifact is still active, so the honest answer is that its
        # bytes are unavailable rather than a revived picture.
        missing = self._bytes(imported["artifact_id"])
        self.assertEqual(missing.get("status"), "UNKNOWN", missing)
        self.assertEqual(missing.get("reason"), "ALBUM_MEDIA_UNAVAILABLE", missing)

    def test_replay_after_tombstone_does_not_restore_the_media_file(self) -> None:
        data = make_png(7, 2)
        imported = self._import(data, key="album:replay-tombstoned")
        self.assertEqual(imported.get("status"), "OK", imported)
        tomb = self._tombstone(imported["artifact_id"], key="album:replay-tombstoned:op")
        self.assertEqual(tomb.get("status"), "OK", tomb)
        media_path = self.box.data_root / "media" / imported["media"]["name"]
        media_path.unlink()

        replay = self._import(data, key="album:replay-tombstoned")
        self.assertNotIn(replay.get("status"), {"OK", "NO_CHANGE"}, replay)
        self.assertFalse(media_path.exists(), "a tombstoned replay must not restore the file")
        listing = self.box.call({"op": "list_album_images", "subject": SUBJECT})
        self.assertEqual(listing["count"], 0, listing)

    # -- restart -----------------------------------------------------------------------

    def test_same_root_restart_reads_the_picture_back(self) -> None:
        data = make_png(6, 4)
        imported = self._import(data, caption="重启后仍在", key="album:restart")
        artifact_id = imported["artifact_id"]
        root = self.box.root
        self.box.stop()

        restarted = Sandbox(root=root)
        self.addCleanup(restarted.cleanup)
        raw = restarted.call({"op": "read_album_image_bytes", "subject": SUBJECT,
                              "artifact_id": artifact_id})
        self.assertEqual(raw.get("status"), "OK", raw)
        self.assertEqual(base64.b64decode(raw["data"]), data)
        detail = restarted.call({"op": "read_album_image", "subject": SUBJECT,
                                 "artifact_id": artifact_id})
        self.assertEqual(detail["image"]["caption"], "重启后仍在")
        listing = restarted.call({"op": "list_album_images", "subject": SUBJECT})
        self.assertEqual(listing["count"], 1)


if __name__ == "__main__":
    unittest.main()

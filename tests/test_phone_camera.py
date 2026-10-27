#!/usr/bin/env python3
"""Phone camera: local generation -> the existing album import, one key one picture.

The picture runtime is replaced by a counting fake (this test never touches Stable Diffusion).
Everything after generation is real: the Life Supply owner on its own AF_UNIX socket, the real
album import, the real manifest and media file.  Covered:

* one capture saves exactly one album artifact and labels it ``local_generated``;
* the original operation key reads the same picture back and does **not** generate again;
* the same key with a different scene is refused before any generation;
* a busy runtime refuses by name and saves nothing.

POSIX only (AF_UNIX).  Fixture roots are kept under ``$PI_SCRATCH_DIR`` or ``/tmp``.
"""
from __future__ import annotations

import os
import struct
import sys
import threading
import time
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_artifact_port as lap  # noqa: E402
from components.phone import service as phone_service  # noqa: E402
from components.phone.camera import (  # noqa: E402
    CameraRefused,
    SceneTranslator,
    _clean_english_prompt,
)

SUBJECT = "test:phone-camera"
ARTIFACT_GRANT = "phone-camera-test:artifact-grant"
WORKSPACE_KEY = "phone-camera-test:workspace"

posix_only = unittest.skipIf(os.name == "nt",
                             "needs POSIX AF_UNIX sockets (run under WSL/Linux)")


def fixture_base() -> Path:
    override = os.environ.get("PI_SCRATCH_DIR")
    base = ((Path(override) / "nyairo-phone-camera-test") if override
            else (Path(os.environ.get("TMPDIR") or "/tmp") / "nyairo-phone-camera-test"))
    base.mkdir(parents=True, exist_ok=True)
    return base


def unique_root() -> Path:
    root = fixture_base() / ("run-%d-%d" % (os.getpid(), time.time_ns()))
    root.mkdir(parents=True)
    return root


def make_png() -> bytes:
    raw = b"\x00" + bytes((x * 7) % 256 for x in range(3 * 2))
    ihdr = struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


class CountingLab:
    """One deterministic picture, and the exact prompt the runtime was really asked for."""

    def __init__(self) -> None:
        self.calls = 0
        self.prompts: list[str] = []

    def generate(self, scene: str) -> tuple[bytes, str]:
        self.calls += 1
        self.prompts.append(scene)
        return make_png(), "image/png"


class BusyLab:
    def generate(self, scene: str) -> tuple[bytes, str]:
        raise CameraRefused("CAMERA_BUSY", "another generation is already running")


class FakeTranslator:
    """A deterministic converter; ``calls`` proves the key replay never re-translates."""

    PROMPT = "windowsill, white porcelain cup, steam, grey-blue sky"

    def __init__(self, error: Exception | None = None) -> None:
        self.calls = 0
        self.error = error
        self.scenes: list[str] = []

    def prompt_for(self, scene: str) -> dict:
        self.calls += 1
        self.scenes.append(scene)
        if self.error is not None:
            raise self.error
        return {"prompt": self.PROMPT, "translated": True, "source": "test_fake"}


class SupplyOwner:
    def __init__(self, root: Path) -> None:
        from lifesupply.service.life_supply import LifeSupplyService, LifeSupplySocketServer
        from lifesupply.service.switches import FeatureSwitches

        self.socket_path = root / "supply.sock"
        uid = os.geteuid()
        self.service = LifeSupplyService(root / "data", operator_uids={uid}, service_uids={uid},
                                         switches=FeatureSwitches.all_on(),
                                         writer_instance="phone-camera-test")
        self.server = LifeSupplySocketServer(str(self.socket_path), self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def call(self, request: dict) -> dict:
        import json
        import socket

        body = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(20)
            client.connect(str(self.socket_path))
            client.sendall(body + b"\n")
            line = client.makefile("rb").readline(12 * 1024 * 1024)
        return json.loads(line.decode("utf-8"))

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)
        self.service.close()


def _grant(owner: SupplyOwner, capability: str, scope: str, target: str,
           authority_scope: str, use_mode: str, operation_id: str) -> str:
    answer = owner.call({"op": "issue_grant", "grant": {
        "subject": SUBJECT, "capability": capability, "scope": scope, "target": target,
        "authority_scope": authority_scope, "use_mode": use_mode,
        "operation_id": operation_id}})
    if answer.get("status") != "ALLOW":
        raise AssertionError("grant refused: %r" % (answer,))
    return str(answer["grant_id"])


def build_service(root: Path, owner: SupplyOwner) -> phone_service.PhoneService:
    from private_daily_items import create_document_artifact

    workspace_grant = _grant(owner, "PROVISION_PERSONAL_WORKSPACE", "workspace:personal",
                             "workspace:%s" % SUBJECT, "life-supply.workspace.v1",
                             "ONE_SHOT", WORKSPACE_KEY)
    provisioned = owner.call({"op": "provision_workspace", "grant_id": workspace_grant,
                              "operation_key": WORKSPACE_KEY,
                              "workspace": {"subject_id": SUBJECT}})
    if provisioned.get("status") != "OK":
        raise AssertionError("workspace provisioning failed: %r" % (provisioned,))
    workspace_id = str(provisioned["workspace"]["workspace_id"])
    grant_id = _grant(owner, "COMMIT_MANAGED_ARTIFACT", "artifact:personal",
                      "workspace:%s" % workspace_id, "life-supply.artifact.v1",
                      "REUSABLE", ARTIFACT_GRANT)
    port = lap.LifeArtifactPort(str(owner.socket_path), SUBJECT)
    items_id, _outcome = create_document_artifact(
        port, workspace_id=workspace_id, title="她的私人日常事项", grant_id=grant_id)
    world_home = root / "world"
    (world_home / "run").mkdir(parents=True, exist_ok=True)
    instance = {
        "schema_version": phone_service.SCHEMA_INSTANCE,
        "kind": phone_service.KIND_INSTANCE,
        "name": "phone-camera-test",
        "subject": SUBJECT,
        "local_owner_uid": os.geteuid(),
        "root": str(root),
        "profile_home": str(root),
        "world_home": str(world_home),
        "world_id": "w-phone-camera-test",
        "supply_socket": str(owner.socket_path),
        "private_items_artifact_id": items_id,
        "artifact_grant_id": grant_id,
        "workspace_id": workspace_id,
        "features": {},
    }
    phone_service.write_instance(root, instance)
    return phone_service.PhoneService(instance, environment={})


@posix_only
class CameraCaptureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = unique_root()
        self.owner = SupplyOwner(self.root)
        self.service = build_service(self.root, self.owner)
        self.translator = FakeTranslator()
        self.service._camera_translator = self.translator

    def tearDown(self) -> None:
        self.owner.stop()

    def test_one_capture_is_saved_and_relabelled_as_local_generation(self) -> None:
        lab = CountingLab()
        self.service._camera_generator = lab
        first = self.service.camera_capture(scene="窗台与白瓷杯", caption="窗台上的热气",
                                            operation_key="camera:first")
        self.assertTrue(first.get("ok"), first)
        self.assertEqual(first.get("status"), phone_service.STATUS_OK)
        artifact_id = str(first["data"]["artifact_id"])
        self.assertTrue(first["data"]["generated"])
        self.assertTrue(first["data"]["generated_locally"])
        self.assertEqual(lab.calls, 1)

        # The checkpoint got the English tags, not the Chinese original.
        self.assertEqual(lab.prompts, [FakeTranslator.PROMPT])
        self.assertEqual(self.translator.scenes, ["窗台与白瓷杯"])
        self.assertEqual(first["data"]["scene"], "窗台与白瓷杯")
        self.assertEqual(first["data"]["generation_prompt"], FakeTranslator.PROMPT)

        listing = self.service.album_list()
        self.assertTrue(listing.get("ok"), listing)
        self.assertEqual(listing.get("count"), 1)
        row = listing["images"][0]
        self.assertEqual(row["artifact_id"], artifact_id)
        self.assertEqual(row["caption"], "窗台上的热气")
        self.assertTrue(row["generated_locally"])
        self.assertEqual(row["source_label"], "local_generated")

        detail = self.service.album_detail(artifact_id)
        self.assertTrue(detail.get("ok"), detail)
        image = detail["image"]
        self.assertTrue(image["generated_locally"])
        self.assertEqual(image["scene"], "窗台与白瓷杯")
        self.assertEqual(image["generation_prompt"], FakeTranslator.PROMPT)
        self.assertEqual(image["translation"]["source"], "test_fake")
        self.assertTrue(image["translation"]["translated"])

    def test_original_key_reads_back_without_generating_again(self) -> None:
        lab = CountingLab()
        self.service._camera_generator = lab
        first = self.service.camera_capture(scene="窗台与白瓷杯", caption="窗台上的热气",
                                            operation_key="camera:first")
        artifact_id = str(first["data"]["artifact_id"])

        replay = self.service.camera_capture(scene="窗台与白瓷杯", caption="窗台上的热气",
                                             operation_key="camera:first")
        self.assertTrue(replay.get("ok"), replay)
        self.assertEqual(replay.get("status"), phone_service.STATUS_ALREADY)
        self.assertEqual(replay["data"]["artifact_id"], artifact_id)
        self.assertFalse(replay["data"]["generated"])
        self.assertTrue(replay["data"]["reused"])
        self.assertEqual(lab.calls, 1)
        # A reused key never asks the converter again, and still reports the stored texts.
        self.assertEqual(self.translator.calls, 1)
        self.assertEqual(replay["data"]["scene"], "窗台与白瓷杯")
        self.assertEqual(replay["data"]["generation_prompt"], FakeTranslator.PROMPT)
        self.assertEqual(self.service.album_list().get("count"), 1)

        other = self.service.camera_capture(scene="另一个无关的画面", caption="窗台上的热气",
                                            operation_key="camera:first")
        self.assertFalse(other.get("ok"))
        self.assertEqual(other.get("reason_code"), "CAMERA_OPERATION_KEY_REUSED")
        self.assertEqual(lab.calls, 1)

    def test_busy_runtime_refuses_and_saves_nothing(self) -> None:
        self.service._camera_generator = BusyLab()
        answer = self.service.camera_capture(scene="窗台与白瓷杯", caption="窗台上的热气",
                                             operation_key="camera:busy")
        self.assertFalse(answer.get("ok"))
        self.assertEqual(answer.get("reason_code"), "CAMERA_BUSY")
        self.assertEqual(self.service.album_list().get("count"), 0)

    def test_failed_translation_generates_and_saves_nothing(self) -> None:
        self.service._camera_translator = FakeTranslator(
            CameraRefused("CAMERA_TRANSLATION_UNAVAILABLE", "answer_is_not_english"))
        lab = CountingLab()
        self.service._camera_generator = lab
        answer = self.service.camera_capture(scene="窗台与白瓷杯", caption="窗台上的热气",
                                             operation_key="camera:no-translation")
        self.assertFalse(answer.get("ok"))
        self.assertEqual(answer.get("reason_code"), "CAMERA_TRANSLATION_UNAVAILABLE")
        self.assertEqual(lab.calls, 0)
        self.assertEqual(self.service.album_list().get("count"), 0)

    def test_busy_translator_refuses_before_any_generation(self) -> None:
        self.service._camera_translator = FakeTranslator(
            CameraRefused("CAMERA_TRANSLATOR_BUSY", "translator_is_processing"))
        lab = CountingLab()
        self.service._camera_generator = lab
        answer = self.service.camera_capture(scene="窗台与白瓷杯", caption="窗台上的热气",
                                             operation_key="camera:translator-busy")
        self.assertFalse(answer.get("ok"))
        self.assertEqual(answer.get("status"), phone_service.STATUS_REJECTED)
        self.assertEqual(answer.get("reason_code"), "CAMERA_TRANSLATOR_BUSY")
        self.assertEqual(lab.calls, 0)
        self.assertEqual(self.service.album_list().get("count"), 0)

    def test_caption_and_scene_are_bounded(self) -> None:
        self.service._camera_generator = CountingLab()
        empty = self.service.camera_capture(scene="  ", caption="窗台上的热气",
                                            operation_key="camera:empty")
        self.assertEqual(empty.get("reason_code"), "CAMERA_SCENE_INVALID")
        long_caption = self.service.camera_capture(scene="窗台", caption="热" * 201,
                                                   operation_key="camera:long")
        self.assertEqual(long_caption.get("reason_code"), "CAMERA_CAPTION_INVALID")


class StubTranslator(SceneTranslator):
    """The real translator with its two HTTP calls replaced (no network, no socket)."""

    def __init__(self, *, slots: object = None, chat: object = None) -> None:
        super().__init__("http://127.0.0.1:18190")
        self.slots = slots
        self.chat = chat
        self.paths: list[tuple[str, str]] = []

    def _request(self, method: str, path: str, payload=None, *, timeout=None):  # type: ignore[override]
        self.paths.append((method, path))
        return self.slots if path == "/slots" else self.chat


class SceneTranslatorTest(unittest.TestCase):
    """Converter rules only: no owner, no picture runtime, no network."""

    def test_english_scene_is_used_directly_without_a_request(self) -> None:
        translator = StubTranslator(slots=[], chat={"choices": []})
        result = translator.prompt_for("  rainy windowsill, one white cup  ")
        self.assertEqual(result["prompt"], "rainy windowsill, one white cup")
        self.assertFalse(result["translated"])
        self.assertEqual(result["source"], "caller_english")
        self.assertEqual(translator.paths, [])

    def test_chinese_scene_is_converted_through_one_request(self) -> None:
        translator = StubTranslator(
            slots=[{"is_processing": False}],
            chat={"choices": [{"message": {"content": '  "a windowsill, a white cup"  '}}]})
        result = translator.prompt_for("雨后的窗台，一只白瓷杯")
        self.assertEqual(result["prompt"], "a windowsill, a white cup")
        self.assertTrue(result["translated"])
        self.assertEqual(result["source"], "local_llama_cpp")
        self.assertEqual(translator.paths,
                         [("GET", "/slots"), ("POST", "/v1/chat/completions")])

    def test_truncated_answer_is_refused(self) -> None:
        translator = StubTranslator(
            slots=[{"is_processing": False}],
            chat={"choices": [{"finish_reason": "length",
                               "message": {"content": "a windowsill, a white cup"}}]})
        with self.assertRaises(CameraRefused) as caught:
            translator.prompt_for("雨后的窗台")
        self.assertEqual(caught.exception.reason_code, "CAMERA_TRANSLATION_UNAVAILABLE")
        self.assertEqual(caught.exception.detail, "translator_answer_truncated")

    def test_busy_translator_is_refused_without_a_conversion_request(self) -> None:
        translator = StubTranslator(slots=[{"is_processing": True}], chat={"choices": []})
        with self.assertRaises(CameraRefused) as caught:
            translator.prompt_for("雨后的窗台")
        self.assertEqual(caught.exception.reason_code, "CAMERA_TRANSLATOR_BUSY")
        self.assertEqual(translator.paths, [("GET", "/slots")])

    def test_chinese_or_chatty_answer_is_refused(self) -> None:
        for content in ("雨后的窗台，一只白瓷杯", "", "I'm sorry, I cannot do that."):
            with self.subTest(content=content):
                with self.assertRaises(CameraRefused) as caught:
                    _clean_english_prompt(content)
                self.assertEqual(caught.exception.reason_code,
                                 "CAMERA_TRANSLATION_UNAVAILABLE")

    def test_labels_and_code_fences_are_stripped(self) -> None:
        self.assertEqual(_clean_english_prompt("```\nPrompt: a white cup, steam\n```"),
                         "a white cup, steam")


if __name__ == "__main__":
    unittest.main()

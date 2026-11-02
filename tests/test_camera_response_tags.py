#!/usr/bin/env python3
"""Camera response budget + repeated technical tags (offline, no real model or GPU).

Two defects this pins down:

* The client used one short read timeout for every action, so a camera capture -- which
  legitimately spends up to 90 s translating and 300 s generating on the phone side -- was
  abandoned by the caller after 10 s.  The phone kept working, saved the picture, and then hit
  ``BrokenPipe`` writing an answer nobody was waiting for.  The checks here drive the
  **production** ``phone_handler`` and the real ``PhoneHTTPServer`` over a real loopback socket
  with a *delayed* answer (the translator and the picture runtime are stubs; the delay stands in
  for model/GPU time and is scaled down so the test stays fast).
* The local converter can repeat one identical comma-separated tag many times.  Exact duplicate
  tags are collapsed in order; tags that differ at all stay.  Truncated, over-limit and
  non-English answers are still refused *before* any de-duplication.

No request ever reaches a real 7860/18190 runtime, and no product model is called.
"""
from __future__ import annotations

import json
import os
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "supply")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import chiyo_bundle.phone_tool as phone_tool  # noqa: E402
from components.phone import server as phone_server  # noqa: E402
from components.phone import service as phone_service  # noqa: E402
from components.phone.camera import (  # noqa: E402
    CameraRefused,
    SceneTranslator,
    _clean_english_prompt,
)

#: Scaled-down budgets: the shape of the fix, not real model/GPU latency.  The camera budget is
#: several times the default, so a 0.8 s answer proves which one the camera call actually used.
FAST_DEFAULT = 0.35
FAST_CAMERA = 3.0
DELAY = 0.8


def _uid():
    return os.getuid() if hasattr(os, "getuid") else None


class StubService:
    """Only what the production camera handler and the two probes call.

    ``camera_capture`` sleeps to stand in for local conversion + generation; nothing is saved.
    """

    def __init__(self, profile_home: str) -> None:
        self.profile_home = profile_home
        self.calls: list[dict] = []
        self.recorded: list[dict] = []
        self.finished = threading.Event()

    # -- /api/phone/state -------------------------------------------------------------
    def state(self) -> dict:
        return {
            "ok": True,
            "status": "ok",
            "instance": {"fingerprint": phone_tool._profile_fingerprint(self.profile_home)},
        }

    # -- /api/phone/wallet (the "other action keeps the short budget" control) --------
    def wallet(self) -> dict:
        time.sleep(DELAY)
        return {"ok": True, "status": "ok", "data": {"currency": "JPY"}}

    # -- /api/phone/camera ------------------------------------------------------------
    def camera_capture(self, *, scene: str, caption: str, operation_key: str) -> dict:
        self.calls.append({"scene": scene, "caption": caption, "operation_key": operation_key})
        time.sleep(DELAY)
        self.finished.set()
        return {
            "ok": True,
            "status": "applied",
            "data": {
                "artifact_id": "artifact:test-camera",
                "caption": caption,
                "operation_key": operation_key,
                "generated": True,
                "reused": False,
            },
        }

    def record_operation(self, *, actor: str, action: str, operation_key: str,
                         result: dict) -> None:
        self.recorded.append({"actor": actor, "action": action, "operation_key": operation_key})


class _ServerFixture:
    """One real loopback ``PhoneHTTPServer`` on an ephemeral port, in a thread."""

    def __init__(self) -> None:
        self.token = "c" * 64
        self.tmp = Path(tempfile.mkdtemp(prefix="nyairo-camera-tags-"))
        (self.tmp / "chiyo").mkdir(parents=True, exist_ok=True)
        self.stub = StubService(str(self.tmp))
        # A real ``PhoneService`` (the production server refuses anything else), with only the
        # three owner-facing entry points replaced by the delay stubs.  No socket, model or GPU.
        instance = {
            "schema_version": phone_service.SCHEMA_INSTANCE,
            "kind": phone_service.KIND_INSTANCE,
            "name": "phone-camera-tags-test",
            "subject": "test:camera-tags",
            "root": str(self.tmp),
            "profile_home": str(self.tmp),
            "world_home": str(self.tmp / "world"),
            "world_id": "w-camera-tags-test",
            "supply_socket": str(self.tmp / "supply.sock"),
            "private_items_artifact_id": "artifact:test-items",
            "artifact_grant_id": "grant:test",
            "workspace_id": "workspace:test",
            "features": {},
        }
        self.service = phone_service.PhoneService(instance, environment={})
        self.service.state = self.stub.state
        self.service.wallet = self.stub.wallet
        self.service.camera_capture = self.stub.camera_capture
        self.server = phone_server.create_server(self.service, csrf_token=self.token)
        self.port = int(self.server.server_address[1])
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        config = {
            "cli_owner": True,
            "local_owner_uid": _uid(),
            "phone": {
                "enabled": True,
                "write_enabled": True,
                "base_url": "http://127.0.0.1:%d" % self.port,
                "profile_home": str(self.tmp),
            },
        }
        (self.tmp / "chiyo" / "config.json").write_text(
            json.dumps(config), encoding="utf-8")

    def camera(self, key: str = "camera-tags-test-1") -> dict:
        answer = phone_tool.phone_handler(
            {"action": "camera", "scene": "一只白色茶杯放在木桌上。",
             "caption": "桌上的一只白杯", "operation_key": key},
            hermes_home=str(self.tmp))
        return json.loads(answer)

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)
        # The fixture root stays on disk whether the case passes or fails: it only holds a stub
        # config, and deleting it would erase what a failing run left behind.  The path itself is
        # not private, so printing it is how the retained root gets found afterwards.
        print("camera_response_tags: kept fixture root %s (uid=%s)" % (self.tmp, _uid()))


class CameraClientBudgetTest(unittest.TestCase):
    """The production handler over a real delayed HTTP answer."""

    def setUp(self) -> None:
        self.saved = (phone_tool.TIMEOUT_SECONDS, phone_tool.CAMERA_TIMEOUT_SECONDS)
        phone_tool.TIMEOUT_SECONDS = FAST_DEFAULT
        phone_tool.CAMERA_TIMEOUT_SECONDS = FAST_CAMERA
        self.fixture = _ServerFixture()

    def tearDown(self) -> None:
        self.fixture.close()
        phone_tool.TIMEOUT_SECONDS, phone_tool.CAMERA_TIMEOUT_SECONDS = self.saved

    def test_camera_waits_for_the_phone_instead_of_the_short_read_timeout(self) -> None:
        # The answer arrives after FAST_DEFAULT has already elapsed: only the camera budget
        # (and the real handler pipeline) can bring it back.
        started = time.monotonic()
        answer = self.fixture.camera()
        elapsed = time.monotonic() - started
        self.assertTrue(answer.get("ok"), answer)
        self.assertEqual(answer.get("status"), "applied", answer)
        self.assertGreaterEqual(elapsed, DELAY)
        self.assertEqual(len(self.fixture.stub.calls), 1)
        self.assertEqual(self.fixture.stub.calls[0]["operation_key"], "camera-tags-test-1")

    def test_other_actions_keep_the_short_default_budget(self) -> None:
        # Same server, same 0.8 s delay, but a read action: it must still fail fast rather than
        # inherit the camera's longer wait.
        started = time.monotonic()
        answer = json.loads(phone_tool.phone_handler({"action": "wallet"},
                                                     hermes_home=str(self.fixture.tmp)))
        elapsed = time.monotonic() - started
        self.assertFalse(answer.get("ok"), answer)
        self.assertEqual(answer.get("reason_code"), "PHONE_UNREACHABLE", answer)
        self.assertLess(elapsed, DELAY)


class ClientDisconnectTest(unittest.TestCase):
    """A caller that hangs up mid-capture ends quietly, without a second 500."""

    def setUp(self) -> None:
        self.fixture = _ServerFixture()
        self.errors: list = []
        # ``handle_error`` is what socketserver calls for anything that escapes the handler; a
        # second (500) write to a dead socket would land here.
        self.fixture.server.handle_error = lambda *args: self.errors.append(args)

    def tearDown(self) -> None:
        self.fixture.close()

    def test_disconnected_camera_caller_does_not_become_an_internal_error(self) -> None:
        body = json.dumps({"scene": "一只白色茶杯放在木桌上。", "caption": "桌上的一只白杯",
                           "operation_key": "camera-tags-disconnect"}).encode("utf-8")
        request = (
            b"POST /api/phone/camera HTTP/1.1\r\n"
            b"Host: 127.0.0.1:%d\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: %d\r\n"
            b"X-CSRF-Token: %s\r\n"
            b"Connection: close\r\n\r\n" % (self.fixture.port, len(body),
                                            self.fixture.token.encode("ascii"))) + body
        with socket.create_connection(("127.0.0.1", self.fixture.port), timeout=5) as peer:
            peer.sendall(request)
            # Controlled fault injection: RST on close, so the server's write really fails.
            peer.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        self.assertTrue(self.fixture.stub.finished.wait(timeout=10),
                        "the handler never reached the capture step")
        time.sleep(1.0)  # the failed write happens right after the capture returns
        self.assertEqual(self.errors, [], "a dead caller was reported as an internal error")
        # The server is still healthy for the next caller.
        answer = self.fixture.camera("camera-tags-after-disconnect")
        self.assertTrue(answer.get("ok"), answer)


class RepeatedTagTest(unittest.TestCase):
    """Only exact duplicates of an already-valid tag line are collapsed."""

    def test_exact_duplicates_collapse_keeping_first_order(self) -> None:
        cleaned = _clean_english_prompt(
            "white cup, wooden table, white cup, teacup, wooden table, white cup")
        self.assertEqual(cleaned, "white cup, wooden table, teacup")

    def test_different_counts_and_colours_are_never_merged(self) -> None:
        cleaned = _clean_english_prompt(
            "two red apples, three red apples, one green apple, two red apples")
        self.assertEqual(cleaned, "two red apples, three red apples, one green apple")

    def test_over_limit_answer_is_refused_before_deduplication(self) -> None:
        repeated = "white cup, " * 60  # far past 400 characters, but only one distinct tag
        self.assertGreater(len(repeated), 400)
        with self.assertRaises(CameraRefused) as caught:
            _clean_english_prompt(repeated)
        self.assertEqual(caught.exception.reason_code, "CAMERA_TRANSLATION_UNAVAILABLE")
        self.assertIn("translator_answer_too_long", str(caught.exception))

    def test_non_english_answer_is_still_refused(self) -> None:
        with self.assertRaises(CameraRefused) as caught:
            _clean_english_prompt("白色的杯子, white cup")
        self.assertIn("translator_answer_not_english", str(caught.exception))

    def test_truncated_answer_is_refused_even_if_it_would_dedupe(self) -> None:
        translator = SceneTranslator(environment={})
        translator._refuse_if_busy = lambda: None
        translator._request = lambda *args, **kwargs: {
            "choices": [{"finish_reason": "length",
                         "message": {"content": "white cup, white cup, white cup"}}]}

        with self.assertRaises(CameraRefused) as caught:
            translator.prompt_for("一只白色茶杯")
        self.assertIn("translator_answer_truncated", str(caught.exception))

    def test_accepted_answer_keeps_its_provenance(self) -> None:
        translator = SceneTranslator(environment={})
        translator._refuse_if_busy = lambda: None
        translator._request = lambda *args, **kwargs: {
            "choices": [{"finish_reason": "stop",
                         "message": {"content": "white cup, wooden table, white cup"}}]}
        result = translator.prompt_for("一只白色茶杯放在木桌上")
        self.assertEqual(result["prompt"], "white cup, wooden table")
        self.assertTrue(result["translated"])
        self.assertEqual(result["source"], "local_llama_cpp")


if __name__ == "__main__":
    unittest.main(verbosity=2)

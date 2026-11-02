#!/usr/bin/env python3
"""The camera chain fixture: refusals and the single-attempt guard, with no inference at all.

These tests never translate, never generate a picture, never start HTTP and never open a
fastday/day/old camera root.  They cover the parts this fixture itself owns:

* ``serve`` refuses a non-empty (or already-instanced) root instead of reusing it;
* the one-attempt record is exclusive: a second ``verify`` on the same root is refused;
* ``readback`` refuses a root whose verify never finished;
* POSIX-only: ``_prepare`` writes a real instance over a real Supply socket and the album
  starts empty (the same provisioning order ``serve`` uses).
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"), str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from components.phone import service as phone_service  # noqa: E402
import phone_camera_chain_once as chain  # noqa: E402

posix_only = unittest.skipIf(os.name == "nt",
                             "needs POSIX AF_UNIX sockets (run under WSL/Linux)")


def _scratch() -> Path:
    override = os.environ.get("PI_SCRATCH_DIR")
    base = ((Path(override) / "nyairo-camera-chain-test") if override
            else (Path(os.environ.get("TMPDIR") or "/tmp") / "nyairo-camera-chain-test"))
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="run-", dir=str(base)))


def _fake_instance(root: Path) -> dict:
    return {
        "schema_version": phone_service.SCHEMA_INSTANCE,
        "kind": phone_service.KIND_INSTANCE,
        "name": "camera-chain-once-test",
        "subject": chain.SUBJECT,
        "root": str(root),
        "profile_home": str(root),
        "world_home": str(root / "world"),
        "supply_socket": str(root / "supply.sock"),
        "private_items_artifact_id": "artifact:test-items",
        "artifact_grant_id": "grant:test",
        "workspace_id": "workspace:test",
        "features": {},
    }


def _write_profile(root: Path) -> None:
    (root / "profile" / "chiyo").mkdir(parents=True, exist_ok=True)
    (root / "profile" / "chiyo" / "config.json").write_text(json.dumps({
        "cli_owner": True,
        "local_owner_uid": os.geteuid() if hasattr(os, "geteuid") else 0,
        "phone": {"enabled": True, "base_url": "http://127.0.0.1:9",
                  "profile_home": str(root), "write_enabled": True,
                  "private_artifacts": True},
    }), encoding="utf-8")


def _run(argv: list[str]) -> tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = chain.main(argv)
    return code, buffer.getvalue()


class ServeRefusalTest(unittest.TestCase):
    def test_refuses_a_non_empty_root(self) -> None:
        root = _scratch()
        (root / "already-here.txt").write_text("x", encoding="utf-8")
        code, out = _run(["serve", "--root", str(root), "--port", "3843"])
        self.assertEqual(code, 2)
        self.assertIn("reason_code=ROOT_NOT_EMPTY", out)

    def test_refuses_a_root_that_already_has_an_instance(self) -> None:
        root = _scratch()
        phone_service.write_instance(root, _fake_instance(root))
        code, out = _run(["serve", "--root", str(root), "--port", "3843"])
        self.assertEqual(code, 2)
        self.assertIn("ROOT_NOT_EMPTY", out)


class OneAttemptGuardTest(unittest.TestCase):
    def test_claim_is_exclusive(self) -> None:
        root = _scratch()
        path = chain._claim_attempt(root)
        state = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "started")
        with self.assertRaises(chain.ChainRefused) as caught:
            chain._claim_attempt(root)
        self.assertEqual(caught.exception.reason_code, "CHAIN_VERIFY_ALREADY_ATTEMPTED")

    def test_verify_refuses_a_missing_root(self) -> None:
        missing = _scratch() / "nope"
        code, out = _run(["verify", "--root", str(missing)])
        self.assertEqual(code, 2)
        self.assertIn("reason_code=ROOT_MISSING", out)


class VerifyGuardTest(unittest.TestCase):
    def test_refuses_a_non_real_translation(self) -> None:
        """A caller-English or reused answer is not the chain this fixture proves."""

        root = _scratch()
        phone_service.write_instance(root, _fake_instance(root))
        _write_profile(root)
        answer = {"ok": True, "status": "applied", "result": {"data": {
            "artifact_id": "artifact:test-photo",
            "generated": True,
            "generated_locally": True,
            "generation_prompt": "white cup on a wooden table",
            "translation": {"translated": False, "source": "caller_english"},
            "media": {"sha256": "0" * 64, "bytes": 1},
        }}}
        original = chain._tool
        chain._tool = lambda profile, action, **fields: answer  # noqa: ARG005
        try:
            code, out = _run(["verify", "--root", str(root)])
        finally:
            chain._tool = original
        self.assertEqual(code, 1)
        self.assertIn("reason_code=TRANSLATION_NOT_REAL", out)
        state = json.loads((root / chain.STATE_NAME).read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "failed")
        # The attempt is burned: a second verify must not run the camera again.
        code, out = _run(["verify", "--root", str(root)])
        self.assertEqual(code, 2)
        self.assertIn("CHAIN_VERIFY_ALREADY_ATTEMPTED", out)


class ReadbackRefusalTest(unittest.TestCase):
    def test_readback_refuses_an_unfinished_attempt(self) -> None:
        root = _scratch()
        phone_service.write_instance(root, _fake_instance(root))
        _write_profile(root)
        chain._claim_attempt(root)  # status=started only
        code, out = _run(["readback", "--root", str(root)])
        self.assertEqual(code, 2)
        self.assertIn("reason_code=STATE_NOT_FINISHED", out)

    def test_readback_refuses_a_root_without_a_record(self) -> None:
        root = _scratch()
        phone_service.write_instance(root, _fake_instance(root))
        _write_profile(root)
        code, out = _run(["readback", "--root", str(root)])
        self.assertEqual(code, 2)
        self.assertIn("reason_code=STATE_MISSING", out)


@posix_only
class PrepareSmokeTest(unittest.TestCase):
    def test_prepare_creates_one_instance_with_an_empty_album(self) -> None:
        root = _scratch()
        owner, instance = chain._prepare(root)
        try:
            loaded = phone_service.load_instance(root)
            self.assertEqual(loaded["subject"], chain.SUBJECT)
            self.assertTrue(Path(loaded["supply_socket"]).exists())
            self.assertTrue(loaded["private_items_artifact_id"])
            service = phone_service.PhoneService(instance, environment={})
            listing = service.album_list()
            self.assertIs(listing.get("ok"), True)
            self.assertEqual(listing.get("count"), 0)
        finally:
            owner.stop()
        with self.assertRaises(chain.ChainRefused) as caught:
            chain._check_root_for_serve(root)
        self.assertEqual(caught.exception.reason_code, "ROOT_NOT_EMPTY")


if __name__ == "__main__":
    unittest.main()

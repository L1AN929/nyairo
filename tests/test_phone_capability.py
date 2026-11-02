#!/usr/bin/env python3
"""私人日记本地能力凭据的最小直接测试。

只测凭据模块自己的边界（0600、原子替换、symlink、受控不安全存储、锁、丢弃），以及
``chiyo_phone`` 在发请求前的 subject/endpoint 校验。真实 serve + HTTP + 日记读写由
``scripts/private_diary_authority_demo.py`` 端到端演示，不在这里复制一套大夹具。

不打印 token 或文件内容；断言只在内存里比较。
"""
from __future__ import annotations

import os
import stat
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from chiyo_bundle import phone_tool  # noqa: E402
from components.phone import capability as phone_capability  # noqa: E402

posix_only = unittest.skipIf(os.name == "nt", "需要 POSIX 权限位/flock（在 WSL/Linux 上运行）")


@posix_only
class CapabilityFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="phone-capability-"))
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        # Only this test's own temporary root; no user data and no live service.
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def _issue(self, *, subject: str = "chiyo",
               endpoint: str = "http://127.0.0.1:39001") -> tuple[Path, object]:
        return phone_capability.issue(self.root, subject=subject, endpoint=endpoint)

    def test_issue_writes_a_private_rotating_credential(self) -> None:
        first_path, first = self._issue()
        self.assertEqual(first_path, phone_capability.capability_path(self.root))
        self.assertTrue(first_path.exists())
        self.assertEqual(stat.S_IMODE(first_path.stat().st_mode) & 0o077, 0)
        self.assertGreaterEqual(len(first.token), phone_capability.MIN_TOKEN_CHARS)
        self.assertNotIn(first.token, repr(first))
        self.assertNotIn(first.token, str(first))

        read_back = phone_capability.read_capability(first_path)
        self.assertEqual(read_back.token, first.token)
        self.assertEqual(read_back.subject, "chiyo")
        self.assertEqual(read_back.endpoint, "http://127.0.0.1:39001")

        _second_path, second = self._issue()
        self.assertNotEqual(second.token, first.token)
        self.assertTrue(phone_capability.matches(second, second.token))
        self.assertFalse(phone_capability.matches(first, second.token))
        # A non-ASCII header is a refusal, not a 500 from compare_digest.
        self.assertFalse(phone_capability.matches(second, "不是token"))

    def test_symlink_target_is_refused(self) -> None:
        path, _capability = self._issue()
        link = self.root / "run" / "linked-capability.json"
        os.symlink(path, link)
        with self.assertRaises(phone_capability.CapabilityInvalid) as caught:
            phone_capability.read_capability(link)
        self.assertEqual(caught.exception.reason_code, "CAPABILITY_PATH_IS_SYMLINK")

    def test_repository_root_is_refused(self) -> None:
        with self.assertRaises(phone_capability.CapabilityUnavailable) as caught:
            phone_capability.issue(ROOT, subject="chiyo",
                                   endpoint="http://127.0.0.1:39001")
        self.assertEqual(caught.exception.reason_code, "ROOT_INSIDE_REPOSITORY")

    def test_insecure_storage_leaves_no_secret(self) -> None:
        with self.assertRaises(phone_capability.CapabilityUnavailable) as caught:
            phone_capability.issue(
                self.root, subject="chiyo", endpoint="http://127.0.0.1:39001",
                private_mode_check=lambda _path: False)
        self.assertEqual(caught.exception.reason_code, "STORAGE_NOT_PRIVATE")
        self.assertFalse(phone_capability.capability_path(self.root).exists())
        self.assertEqual(
            list((self.root / "run").glob(".phone-capability.*.tmp")), [])

    def test_one_rotating_writer_per_root(self) -> None:
        first = phone_capability.acquire_lock(self.root)
        self.assertIsNotNone(first)
        second = phone_capability.acquire_lock(self.root)
        self.assertIsNone(second)
        first.release()
        third = phone_capability.acquire_lock(self.root)
        self.assertIsNotNone(third)
        third.release()

    def test_discard_only_removes_this_serves_credential(self) -> None:
        path, current = self._issue()
        _same_path, other = self._issue()
        self.assertFalse(phone_capability.discard(self.root, current))
        self.assertTrue(path.exists())
        self.assertTrue(phone_capability.discard(self.root, other))
        self.assertFalse(path.exists())


@posix_only
class ToolPreSendGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="phone-capability-tool-"))
        self.addCleanup(self._cleanup)
        self.path, self.capability = phone_capability.issue(
            self.root, subject="chiyo", endpoint="http://127.0.0.1:39002")

    def _cleanup(self) -> None:
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def _gate(self, *, base_url: str = "http://127.0.0.1:39002",
              path: Path | None = None, subject: str = "chiyo") -> str:
        settings = {"base_url": base_url, "capability_file": str(path or self.path)}
        try:
            phone_tool._private_capability(  # noqa: SLF001 - the pre-send gate under test
                settings, {"instance": {"subject": subject}})
        except phone_tool.PhoneToolRefused as exc:
            return str(exc.reason_code)
        return "TOKEN_READY"

    def test_correct_binding_reads_the_token(self) -> None:
        self.assertEqual(self._gate(), "TOKEN_READY")

    def test_endpoint_mismatch_is_refused_before_any_request(self) -> None:
        self.assertEqual(self._gate(base_url="http://127.0.0.1:39999"),
                         "PHONE_CAPABILITY_ENDPOINT_MISMATCH")

    def test_subject_mismatch_is_refused_before_any_request(self) -> None:
        self.assertEqual(self._gate(subject="someone-else"),
                         "PHONE_CAPABILITY_SUBJECT_MISMATCH")

    def test_missing_file_fails_closed(self) -> None:
        self.assertEqual(self._gate(path=self.root / "run" / "missing.json"),
                         "PHONE_CAPABILITY_FILE_UNREADABLE")

    def test_redirect_is_not_followed_with_a_credential(self) -> None:
        hits: list[dict] = []

        class Trap(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - stdlib signature
                hits.append({key.lower(): value for key, value in self.headers.items()})
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, fmt: str, *args) -> None:  # noqa: A003
                pass

        trap = ThreadingHTTPServer(("127.0.0.1", 0), Trap)
        target = "http://127.0.0.1:%d/api/phone/diary/x" % int(trap.server_address[1])

        class Redirect(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - stdlib signature
                self.send_response(302)
                self.send_header("Location", target)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, fmt: str, *args) -> None:  # noqa: A003
                pass

        redirect = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
        threads = [threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02},
                                    daemon=True) for server in (trap, redirect)]
        for thread in threads:
            thread.start()
        self.addCleanup(trap.server_close)
        self.addCleanup(redirect.server_close)
        try:
            code, _doc = phone_tool._request(  # noqa: SLF001 - transport under test
                {"base_url": "http://127.0.0.1:%d" % int(redirect.server_address[1])},
                "GET", "/api/phone/diary/x", actor="model", capability="unit-test-token")
            self.assertEqual(code, 302)
            self.assertEqual(hits, [])
        finally:
            for server in (trap, redirect):
                server.shutdown()
            for thread in threads:
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()

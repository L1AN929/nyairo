from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_module_from_path(name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(name, file_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class HermesOriginAuthorityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import ast
        import re
        import types
        import urllib.parse

        ws_src = (REPO_ROOT / "vendor" / "hermes" / "hermes_cli" / "web_server.py").read_text(encoding="utf-8")
        ws_tree = ast.parse(ws_src)
        ws_ns: dict = {
            "os": os,
            "re": re,
            "urllib": urllib,
            "_LOOPBACK_HOST_VALUES": frozenset({"127.0.0.1", "localhost", "::1"}),
        }
        wanted_ws = {"_host_header_hostname", "_is_accepted_host", "_origin_matches_host"}
        for node in ws_tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in wanted_ws:
                mod_ast = ast.Module(body=[node], type_ignores=[])
                exec(compile(mod_ast, "web_server.py", "exec"), ws_ns)

        chat_src = (REPO_ROOT / "vendor" / "hermes" / "hermes_cli" / "web_server_chat.py").read_text(encoding="utf-8")
        chat_tree = ast.parse(chat_src)
        fake_app = types.SimpleNamespace(
            state=types.SimpleNamespace(bound_host="127.0.0.1", trusted_public_hosts=frozenset())
        )
        fake_pkg = types.ModuleType("hermes_cli")
        fake_ws_mod = types.ModuleType("hermes_cli.web_server")
        fake_ws_mod._is_accepted_host = ws_ns["_is_accepted_host"]
        fake_ws_mod._origin_matches_host = ws_ns["_origin_matches_host"]
        fake_ws_mod.app = fake_app
        fake_pkg.web_server = fake_ws_mod

        chat_ns: dict = {"Optional": object, "urllib": urllib}
        for node in chat_tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "_ws_host_origin_reason":
                mod_ast = ast.Module(body=[node], type_ignores=[])
                exec(compile(mod_ast, "web_server_chat.py", "exec"), chat_ns)

        cls.ws_ns = ws_ns
        cls.chat_ns = chat_ns
        cls.fake_pkg = fake_pkg
        cls.fake_ws_mod = fake_ws_mod

    def test_origin_authority_matching_blocks_cross_port_localhost(self):
        fn = self.ws_ns["_origin_matches_host"]
        self.assertTrue(fn("http://127.0.0.1:9119", "127.0.0.1:9119"))
        self.assertTrue(fn("http://localhost:9119", "localhost:9119"))
        self.assertTrue(fn("http://[::1]:9119", "[::1]:9119"))
        self.assertFalse(fn("http://127.0.0.1:8080", "127.0.0.1:9119"))
        self.assertFalse(fn("http://localhost:9119", "127.0.0.1:9119"))
        self.assertFalse(fn("https://127.0.0.1", "127.0.0.1:9119"))
        with mock.patch.dict(os.environ, {"HERMES_WEB_DEV": "1"}, clear=False):
            self.assertTrue(fn("http://localhost:5173", "127.0.0.1:9119"))
            self.assertFalse(fn("http://localhost:8080", "127.0.0.1:9119"))

    def test_websocket_origin_guard_enforces_exact_port(self):
        import types

        reason_fn = self.chat_ns["_ws_host_origin_reason"]
        with mock.patch.dict(
            sys.modules,
            {"hermes_cli": self.fake_pkg, "hermes_cli.web_server": self.fake_ws_mod},
        ):
            ok_ws = types.SimpleNamespace(headers={"host": "127.0.0.1:9119", "origin": "http://127.0.0.1:9119"})
            self.assertIsNone(reason_fn(ok_ws))

            cross_port_ws = types.SimpleNamespace(headers={"host": "127.0.0.1:9119", "origin": "http://127.0.0.1:8080"})
            reason = reason_fn(cross_port_ws)
            self.assertIsNotNone(reason)
            self.assertTrue(reason.startswith("origin_mismatch"))

            rebind_ws = types.SimpleNamespace(headers={"host": "evil.example.com:9119", "origin": "http://evil.example.com:9119"})
            reason2 = reason_fn(rebind_ws)
            self.assertIsNotNone(reason2)
            self.assertTrue(reason2.startswith("host_mismatch"))


class WorldGenPreviewXssHardeningTest(unittest.TestCase):
    def test_render_preview_escapes_script_breakout_and_html_injections(self):
        from components.worldgen.preview import render_preview

        src_pkg = REPO_ROOT / "examples" / "worlds" / "inobe"
        with tempfile.TemporaryDirectory() as tmp:
            pkg_copy = Path(tmp) / "world_xss"
            shutil.copytree(src_pkg, pkg_copy)

            world_json = json.loads((pkg_copy / "world.json").read_text(encoding="utf-8"))
            world_json["name"] = "Inobe </script><script>window.__xss=1</script>\u2028\u2029"
            (pkg_copy / "world.json").write_text(
                json.dumps(world_json, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            content_json = json.loads((pkg_copy / "content.json").read_text(encoding="utf-8"))
            first_entry = next(iter(content_json["entries"].values()))
            first_entry["intro"] = '<img src=x onerror="alert(1)"> & </script>'
            (pkg_copy / "content.json").write_text(
                json.dumps(content_json, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            manifest_path = pkg_copy / "MANIFEST.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for fname in list(manifest["files"].keys()):
                manifest["files"][fname] = hashlib.sha256((pkg_copy / fname).read_bytes()).hexdigest()
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            out_html = render_preview(pkg_copy, output_path=Path(tmp) / "out.html")
            rendered = out_html.read_text(encoding="utf-8")

            self.assertNotIn("</script><script>window.__xss=1", rendered)
            self.assertNotIn('<img src=x onerror="alert(1)">', rendered)
            self.assertIn(r"\u003c/script\u003e\u003cscript\u003ewindow.__xss=1\u003c/script\u003e", rendered)
            self.assertNotIn("\u2028", rendered)
            self.assertNotIn("\u2029", rendered)
            self.assertIn("function esc(v)", rendered)


class WorldEditorConfinementTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        editor_dir = str(REPO_ROOT / "components" / "world-editor")
        if editor_dir not in sys.path:
            sys.path.insert(0, editor_dir)
        cls.editor_server = _load_module_from_path(
            "world_editor_server_sec_test",
            REPO_ROOT / "components" / "world-editor" / "server.py",
        )

    def test_confine_editor_dir_allows_repo_and_temp_but_blocks_system_paths(self):
        in_repo = self.editor_server._confine_editor_dir(
            str(REPO_ROOT / "examples" / "worlds" / "inobe"),
            "package_dir",
        )
        self.assertTrue(str(in_repo).startswith(str(REPO_ROOT.resolve())))

        with tempfile.TemporaryDirectory() as tmp:
            in_tmp = self.editor_server._confine_editor_dir(tmp, "package_dir")
            self.assertEqual(in_tmp, Path(tmp).resolve())

        outside = Path("C:/Windows/System32") if os.name == "nt" else Path("/etc")
        with self.assertRaises(self.editor_server.WorldError) as ctx:
            self.editor_server._confine_editor_dir(str(outside), "package_dir")
        self.assertEqual(ctx.exception.status, 403)
        self.assertEqual(ctx.exception.code, "PATH_FORBIDDEN")

        traversal = str(REPO_ROOT / ".." / "..")
        with self.assertRaises(self.editor_server.WorldError) as ctx2:
            self.editor_server._confine_editor_dir(traversal, "original_dir")
        self.assertEqual(ctx2.exception.status, 403)

    def test_sec_fetch_site_rejects_same_site_cross_port(self):
        import threading
        import urllib.error
        import urllib.request

        with tempfile.TemporaryDirectory() as tmp:
            srv = self.editor_server.create_server(Path(tmp), port=0)
            t = threading.Thread(target=srv.serve_forever, daemon=True)
            t.start()
            try:
                url = f"http://127.0.0.1:{srv.server_port}/api/bootstrap"
                req_bad = urllib.request.Request(url, headers={"Sec-Fetch-Site": "same-site"})
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(req_bad, timeout=5)
                self.assertEqual(ctx.exception.code, 403)
                ctx.exception.close()

                req_ok = urllib.request.Request(url, headers={"Sec-Fetch-Site": "same-origin"})
                with urllib.request.urlopen(req_ok, timeout=5) as resp:
                    self.assertEqual(resp.status, 200)
            finally:
                srv.shutdown()
                srv.server_close()


class CameraRuntimeAndScannerHardeningTest(unittest.TestCase):
    def test_camera_runtime_has_no_workstation_paths(self):
        cam_path = REPO_ROOT / "scripts" / "camera_local_runtime.py"
        text = cam_path.read_text(encoding="utf-8")
        self.assertNotIn("novel-rewrite", text)
        self.assertNotIn("RUANJIAN", text)

        cam_mod = _load_module_from_path("camera_local_runtime_sec_test", cam_path)
        with self.assertRaises(SystemExit) as ctx:
            cam_mod._check([("NYAIRO_LLAMA_SERVER_BIN", Path())])
        self.assertIn("NYAIRO_LLAMA_SERVER_BIN", str(ctx.exception))

    def test_public_scanner_catches_hashed_infra_tokens(self):
        scanner = _load_module_from_path(
            "scan_public_source_sec_test",
            REPO_ROOT / "scripts" / "scan_public_source.py",
        )
        self.assertGreaterEqual(len(scanner.BLOCKED_HOST_TOKEN_SHA256), 4)

        sentinel_host = "internal-gw.example.invalid"
        sentinel_digest = hashlib.sha256(sentinel_host.encode("utf-8")).hexdigest()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "leak.txt").write_text(
                f"endpoint = https://sub-gw.{sentinel_host}/v1/chat\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "-C", str(root), "add", "leak.txt"], check=True)

            with mock.patch.object(
                scanner,
                "BLOCKED_HOST_TOKEN_SHA256",
                frozenset({sentinel_digest}),
            ):
                report = scanner.scan(root)
                self.assertEqual(report["status"], "FAIL")
                self.assertTrue(
                    any(m["pattern"] == "redacted_infra_host" for m in report["unapproved_matches"])
                )


if __name__ == "__main__":
    unittest.main()

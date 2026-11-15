#!/usr/bin/env python3
"""Loopback-only, dependency-free application server for the world editor."""
from __future__ import annotations
import argparse
import json
import mimetypes
import os
import sqlite3
import sys
import tempfile
import threading
import uuid
import webbrowser
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from model import WorldError, THEMES, cover_svg, fail, validate_package, export_package, templates, is_open, start_minute
from resources import import_asset
from store import Store
from assets import ASSETS

ROOT = Path(__file__).resolve().parent
MAX_BODY = 18_000_000


def _allowed_editor_roots(extra_roots=()):
    roots = [
        ROOT.parents[1].resolve(),
        Path(tempfile.gettempdir()).resolve(),
        (Path.home() / ".nyairo").resolve(),
        (Path.home() / ".nyairo-day").resolve(),
        (Path.home() / ".nyairo-world-editor").resolve(),
    ]
    hermes_home = os.environ.get("HERMES_HOME", "").strip()
    if hermes_home:
        roots.append(Path(hermes_home).expanduser().resolve())
    for item in os.environ.get("NYAIRO_EDITOR_ALLOWED_ROOTS", "").split(os.pathsep):
        if item.strip():
            roots.append(Path(item.strip()).expanduser().resolve())
    for extra in extra_roots:
        if extra is not None:
            roots.append(Path(extra).resolve())
    return roots


def _confine_editor_dir(raw: str, label: str, extra_roots=()) -> Path:
    candidate = Path(raw.strip()).expanduser()
    if not candidate.is_absolute():
        fail(f"{label}需要是绝对路径。", "PATH_FORBIDDEN", 403)
    if ".." in candidate.parts:
        fail(f"{label}不允许包含上级目录跳转（..）。", "PATH_FORBIDDEN", 403)
    resolved = candidate.resolve()
    for allowed in _allowed_editor_roots(extra_roots):
        if resolved == allowed or allowed in resolved.parents:
            return resolved
    fail(
        f"{label}超出允许的工作目录范围（仅允许仓库目录、个人实例目录、编辑器数据目录或 NYAIRO_EDITOR_ALLOWED_ROOTS）。",
        "PATH_FORBIDDEN",
        403,
    )


def nyairo_export(data, *, extra_roots=()):
    """「导出 NyAiro 世界包」（v0.2 桥接）：以原始世界包为基础回写编辑器修改。

    编辑器 v3 文件不包含 NPC、环境事件、世界规则等完整世界事实，因此回写
    必须（且只能）以原始 WorldGen 世界包为基础做差量合并；原始包不可用时
    明确拒绝，不假装 v3 包含全部世界信息。输出永远是新目录，原始包不被覆盖。
    """
    original_raw = data.get("original_dir")
    if not isinstance(original_raw, str) or not original_raw.strip():
        fail("需要提供原始 WorldGen 世界包目录（original_dir）。")
    original = _confine_editor_dir(original_raw, "原始世界包目录", extra_roots=extra_roots)
    if not (original / "world.json").is_file() or not (original / "MANIFEST.json").is_file():
        fail(
            "指定目录不是有效的 WorldGen 世界包（缺少 world.json / MANIFEST.json）。"
            "编辑器 v3 文件不包含 NPC、环境事件等完整世界信息，无法凭空回写；"
            "请提供生成该场景时使用的原始世界包目录。",
            "ORIGINAL_REQUIRED",
        )
    repo_root = ROOT.parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    try:
        from components.worldgen.editor_bridge import import_from_editor
    except ImportError:
        fail(
            "未找到 components/worldgen 桥接模块；请在本仓库目录结构内运行编辑器。",
            "BRIDGE_MISSING",
        )
    try:
        return import_from_editor(original, data.get("package"))
    except ValueError as exc:
        # EditorBridgeError / WorldPackageError 等都按业务失败回传给界面。
        fail(str(exc))


def nyairo_perception(data, *, extra_roots=()):
    """「AI 视角预览」（v0.4）：查看某位置通过感知适配器能获得的世界信息。

    三层信息明确区分：世界包静态数据 / 该位置允许观察的信息 /
    需要运行时状态的信息。提供隔离实例目录时读取真实 world_state
    （只读），并在结果中标注来源；编辑器演示结果不冒充 World/Body 观察回执。
    """
    repo_root = ROOT.parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    try:
        from components.worldgen.perception import PerceptionError, observe_local
    except ImportError:
        fail(
            "未找到 components/worldgen 感知模块；请在本仓库目录结构内运行编辑器。",
            "BRIDGE_MISSING",
        )
    package_raw = data.get("package_dir") or data.get("original_dir")
    if not isinstance(package_raw, str) or not package_raw.strip():
        fail("需要提供 WorldGen 世界包目录（package_dir）。")
    package_dir = _confine_editor_dir(package_raw, "世界包目录", extra_roots=extra_roots)
    if not (package_dir / "world.json").is_file():
        fail("指定目录不是有效的 WorldGen 世界包（缺少 world.json）。")
    room = data.get("room") or {}
    place_id = data.get("place_id") or room.get("id")
    if not isinstance(place_id, str) or not place_id.strip():
        fail("需要提供位置（place_id 或选中区域）。")
    instance_raw = data.get("instance_home")
    resolved_instance = (
        _confine_editor_dir(instance_raw, "实例目录", extra_roots=extra_roots)
        if isinstance(instance_raw, str) and instance_raw.strip()
        else None
    )
    try:
        return observe_local(
            package_dir,
            place_id=place_id.strip(),
            area_id=data.get("area_id"),
            instance_home=resolved_instance,
        )
    except ValueError as exc:
        fail(str(exc))


def nyairo_interior(data):
    """「AI 生成室内布局」（v0.3）：为选中区域生成室内空间与家具提案。

    程序负责全部几何与合法性；GLM（可选）只提供语义建议。返回的
    ``space``/``objects`` 是编辑器可直接应用的 v3 数据；是否采纳由用户确认，
    应用后走编辑器既有的撤销与保存机制。
    """
    repo_root = ROOT.parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    try:
        from components.worldgen import interior as interior_mod
    except ImportError:
        fail(
            "未找到 components/worldgen 室内生成模块；请在本仓库目录结构内运行编辑器。",
            "BRIDGE_MISSING",
        )

    room = data.get("room")
    if not isinstance(room, dict) or not isinstance(room.get("id"), str) or not room["id"].strip():
        fail("需要提供选中的区域（room.id）。")
    room_id = room["id"].strip()

    building = data.get("building")
    if building in (None, "", "auto"):
        pseudo_location = {
            "location_id": room_id,
            "kind": str(room.get("kind") or ""),
            "name": str(room.get("name") or ""),
        }
        building = interior_mod.infer_building(pseudo_location) or "hall"
    if building not in interior_mod.BUILDING_LABELS:
        fail(
            f"未知建筑类型 {building!r}；支持 {sorted(interior_mod.BUILDING_LABELS)}",
            "UNKNOWN_BUILDING",
        )

    seed = data.get("seed")
    if seed in (None, ""):
        import time as _time

        seed = int(_time.time() * 1000) % 1_000_000_000
    if isinstance(seed, str):
        try:
            seed = int(seed)
        except ValueError:
            fail("随机种子需要是整数。", "INVALID_SEED")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2**31 - 1:
        fail("随机种子需要是 0..2147483647 的整数。", "INVALID_SEED")

    use_glm = bool(data.get("glm"))
    design = None
    design_source = "procedural"
    warnings: list[str] = []
    if use_glm:
        try:
            from components.worldgen.providers import glm

            design, usage = glm.design_interior(
                building_label=interior_mod.BUILDING_LABELS[building],
                location_name=str(room.get("name") or room_id),
                theme_desc=str(data.get("theme_desc") or "一座安静的小镇"),
            )
            design_source = "glm"
        except Exception as exc:  # GLM 失败回退程序化
            warnings.append(f"GLM 语义设计失败，回退程序化布局（{type(exc).__name__}: {exc}）")

    try:
        result = interior_mod.generate_interior(
            building=building,
            seed=seed,
            location_id=room_id,
            design=design,
        )
    except ValueError as exc:
        fail(str(exc))

    import hashlib as _hashlib

    slug = _hashlib.sha1(f"{building}|{seed}|{room_id}".encode("utf-8")).hexdigest()[:5]
    objects = []
    for index, item in enumerate(result["furniture"], start=1):
        object_id = f"ai{slug}-{item['kind']}-{index:02d}"
        objects.append(
            {
                "id": object_id,
                "name": item["label"],
                "description": item["note"],
                "kind": item["kind"],
                "room_id": room_id,
                "x": item["x"],
                "y": item["y"],
                "actions": item["actions"],
                "portable": item["portable"],
                "capacity": item["capacity"],
                "servings": item["servings"],
                "visual": item["visual"],
                "behavior": item["behavior"],
                "asset_id": None,
                "interaction": item["interaction"],
                "initial_status": item["initial_status"],
            }
        )
    return {
        "status": "generated",
        "room_id": room_id,
        "building": building,
        "building_label": interior_mod.BUILDING_LABELS[building],
        "seed": seed,
        "design_source": design_source,
        "space": result["space"],
        "objects": objects,
        "passability": result["passability"],
        "design_applied": result.get("design_applied", []),
        "warnings": warnings,
    }


def parse_json(value):
    def unique(pairs):
        result = {}
        for key, val in pairs:
            if key in result:
                fail("JSON 包含重复字段。")
            result[key] = val
        return result
    try:
        return json.loads(value, object_pairs_hook=unique, parse_constant=lambda _: fail("不支持非有限数值。"))
    except (ValueError, UnicodeDecodeError, RecursionError):
        fail("文件或请求不是有效的 UTF-8 JSON。", "INVALID_JSON", 400)


class Handler(BaseHTTPRequestHandler):
    server_version = "NyairoWorldEditor/0.4"

    def __init__(self, *args, store=None, **kwargs):
        self.store = store
        super().__init__(*args, **kwargs)

    def log_message(self, fmt, *args):
        # Deliberately do not log content, resident names, query parameters, or secrets.
        print(f"HTTP {self.command} {args[1] if len(args) > 1 else ''}", flush=True)

    STATIC_ASSETS = {
        "/": "index.html",
        "/index.html": "index.html",
        "/app.js": "app.js",
        "/style.css": "style.css",
        "/editor-state.mjs": "editor-state.mjs",
        "/layout.mjs": "layout.mjs",
        "/space.mjs": "space.mjs",
    }

    def guard(self, mutation=False, *, static_asset=False):
        allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        if self.headers.get("Host") not in allowed:
            fail("仅允许通过本机地址访问编辑器。", "HOST", 403)
        origin = self.headers.get("Origin")
        if origin and origin not in {"http://" + x for x in allowed}:
            fail("来源不被允许。", "ORIGIN", 403)
        allowed_sites = (
            (None, "", "same-origin", "same-site", "none")
            if (static_asset and not mutation)
            else (None, "", "same-origin", "none")
        )
        if self.headers.get("Sec-Fetch-Site") not in allowed_sites:
            fail("不允许跨源访问。", "ORIGIN", 403)
        if mutation and self.headers.get("X-Nyairo-Editor") != "1":
            fail("操作需要编辑器请求标记。", "CSRF", 403)

    def send(self, status, body, content_type="application/json; charset=utf-8", filename=None, *, head_only=False):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self' http://127.0.0.1:* http://localhost:*; base-uri 'none'; form-action 'self'")
        try:
            self.end_headers()
            if not head_only:
                self.wfile.write(body)
        except ConnectionError:
            # A cancelled download or closed tab does not undo a committed write.
            pass

    def route(self, mutation=False, *, head_only=False):
        try:
            path = urlparse(self.path).path
            is_static = (not mutation) and (path in self.STATIC_ASSETS)
            self.guard(mutation, static_asset=is_static)
            parts = path.strip("/").split("/")
            if mutation:
                if not self.headers.get("Content-Type", "").startswith("application/json"):
                    fail("请求需要使用 application/json。", "MEDIA_TYPE", 415)
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    fail("请求长度无效。", "INVALID", 400)
                if not 0 < length <= MAX_BODY:
                    fail("请求超过 18 MB，或内容为空。世界文件上限 8 MB。", "TOO_LARGE", 413)
                data = parse_json(self.rfile.read(length))
                if not isinstance(data, dict):
                    fail("请求需要是 JSON 对象。")
                if path == "/api/assets/import":
                    return self.send(200, import_asset(data))
                if path == "/api/validate":
                    p, warnings = validate_package(data.get("package"))
                    return self.send(200, {"package": p, "warnings": warnings})
                if path == "/api/rules/preview":
                    p, _ = validate_package(data.get("package"))
                    from model import integer
                    minute = integer(data.get("minute"), "测试时间分钟", 0, 1439)
                    return self.send(200, {"minute": minute, "rooms": [{"id": r["id"], "open": is_open(r, minute)} for r in p["rooms"]]})
                if path == "/api/draft":
                    return self.send(200, self.store.save_draft(data.get("package"), data.get("revision")))
                if path == "/api/recovery":
                    return self.send(200, self.store.recovery_write(data))
                if path == "/api/trials":
                    return self.send(201, self.store.create_trial(data.get("package")))
                if path == "/api/saves/import":
                    return self.send(201, self.store.create_trial(save=data.get("save")))
                if path == "/api/exports":
                    if data.get("kind") == "world":
                        content = export_package(data.get("package"))
                        filename = content["meta"]["id"] + ".nyairo-world.json"
                    elif data.get("kind") == "save":
                        content = self.store.export_save(data.get("trial_id"))
                        filename = content["package"]["meta"]["id"] + ".nyairo-save.json"
                    else:
                        fail("不支持的导出类型。")
                    token = uuid.uuid4().hex
                    payload = json.dumps(content, ensure_ascii=False, indent=2).encode("utf-8")
                    # Always leave a local copy even if a browser cancels its download.
                    export_dir = self.store.path.parent / "exports"
                    export_dir.mkdir(parents=True, exist_ok=True)
                    local_name = token[:8] + "-" + filename
                    with (export_dir / local_name).open("xb") as out:
                        out.write(payload)
                    cover_file = None
                    if data.get("kind") == "world":
                        cover_file = local_name.replace('.nyairo-world.json', '.cover.svg')
                        with (export_dir / cover_file).open("xb") as out:
                            out.write(cover_svg(content))
                    with self.server.exports_lock:
                        while len(self.server.exports) >= 20:
                            self.server.exports.pop(next(iter(self.server.exports)))
                        self.server.exports[token] = (filename, payload)
                    return self.send(201, {"url": "/api/exports/" + token, "filename": filename, "local_file": "exports/" + local_name, "cover_file": "exports/" + cover_file if cover_file else None})
                if path == "/api/nyairo/export":
                    active_extra = (self.store.path.parent, getattr(self.server, "active_world_parent", None))
                    return self.send(200, nyairo_export(data, extra_roots=active_extra))
                if path == "/api/nyairo/interior":
                    return self.send(200, nyairo_interior(data))
                if path == "/api/nyairo/perception":
                    active_extra = (self.store.path.parent, getattr(self.server, "active_world_parent", None))
                    return self.send(200, nyairo_perception(data, extra_roots=active_extra))
                if len(parts) == 4 and parts[:2] == ["api", "trials"] and parts[3] == "actions":
                    return self.send(200, self.store.execute(parts[2], data.get("execution_id"), data.get("revision"), data.get("command")))
            else:
                if path == "/api/bootstrap":
                    active_info = getattr(self.server, "active_world", None)
                    payload_boot = {
                        "draft": self.store.draft(),
                        "trials": self.store.trials(),
                        "workspace_id": self.store.workspace_id(),
                        "themes": THEMES,
                        "actions": templates(),
                        "assets": ASSETS,
                        "recoveries": self.store.recoveries(),
                    }
                    if active_info is not None:
                        payload_boot["active_world_dir"] = active_info["package_dir"]
                        payload_boot["active_world_id"] = active_info["world_id"]
                    return self.send(200, payload_boot)
                if path == "/api/active-world":
                    active_info = getattr(self.server, "active_world", None)
                    if active_info is None:
                        fail("当前未绑定活跃世界包目录。", "NOT_FOUND", 404)
                    refreshed = _load_active_world_package(active_info["package_dir"]) or active_info
                    return self.send(200, refreshed)
                if path == "/api/example":
                    return self.send(200, validate_package(parse_json((ROOT / "examples" / "cafe.nyairo-world.json").read_bytes()))[0])
                if len(parts) == 3 and parts[:2] == ["api", "trials"]:
                    return self.send(200, self.store.trial(parts[2]))
                if len(parts) == 4 and parts[:2] == ["api", "trials"] and parts[3] == "save":
                    return self.send(200, self.store.export_save(parts[2]))
                if len(parts) == 3 and parts[:2] == ["api", "exports"]:
                    with self.server.exports_lock:
                        export = self.server.exports.get(parts[2])
                    if not export:
                        fail("导出文件已过期，请重新点击导出。", "NOT_FOUND", 404)
                    return self.send(200, export[1], filename=export[0])
                # Fixed static allowlist prevents traversal and accidental data disclosure.
                if path in self.STATIC_ASSETS:
                    filename = ROOT / "web" / self.STATIC_ASSETS[path]
                    media = {".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".html": "text/html"}[filename.suffix]
                    return self.send(200, filename.read_bytes(), media + "; charset=utf-8", head_only=head_only)
            fail("页面或接口不存在。", "NOT_FOUND", 404)
        except WorldError as e:
            self.send(e.status, {"error": str(e), "code": e.code}, head_only=head_only)
        except (TypeError, KeyError, OverflowError, RecursionError):
            self.send(422, {"error": "内容含有无效的字段类型或结构，请检查导入文件。", "code": "INVALID"}, head_only=head_only)
        except sqlite3.Error:
            self.send(503, {"error": "本地存储暂时不可用，请确认数据目录可写后重试。", "code": "STORAGE"}, head_only=head_only)
        except OSError:
            self.send(503, {"error": "文件无法读取或保存，请确认数据目录有空间且可写后重试。", "code": "FILESYSTEM"}, head_only=head_only)

    def do_GET(self):
        self.route()

    def do_HEAD(self):
        self.route(head_only=True)

    def do_POST(self):
        self.route(True)


def _load_active_world_package(world_package_dir):
    """Convert a valid WorldGen package directory (world.json + topology.json) into an editor v3 package."""
    if world_package_dir is None or not str(world_package_dir).strip():
        return None
    try:
        pkg_dir = Path(str(world_package_dir).strip()).expanduser().resolve()
    except OSError:
        return None
    if not pkg_dir.is_dir() or not (pkg_dir / "world.json").is_file() or not (pkg_dir / "topology.json").is_file():
        return None
    repo_root = ROOT.parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    try:
        from components.worldgen.editor_bridge import load as load_worldgen, to_editor_world
        wg_pkg = load_worldgen(pkg_dir, verify=(pkg_dir / "MANIFEST.json").is_file())
        ed_world = to_editor_world(wg_pkg)
        bridge_warnings = list(ed_world.pop("_bridge_warnings", []))
        validated, val_warnings = validate_package(ed_world)
        return {
            "package": validated,
            "package_dir": str(pkg_dir),
            "world_id": wg_pkg.world_id,
            "warnings": bridge_warnings + list(val_warnings),
        }
    except Exception as exc:
        print(f"Warning: failed to load active world package {pkg_dir}: {exc}", file=sys.stderr)
        return None


def create_server(data_dir, port=0, example_file="cafe.nyairo-world.json", world_package_dir=None):
    cafe_example = parse_json((ROOT / "examples" / example_file).read_bytes())
    active_world = _load_active_world_package(world_package_dir)
    initial_example = active_world["package"] if active_world is not None else cafe_example
    store = Store(Path(data_dir) / "editor.sqlite3", initial_example)
    if active_world is not None:
        cur = store.draft()
        cafe_id = (cafe_example.get("meta") or {}).get("id")
        cur_id = ((cur.get("package") or {}).get("meta") or {}).get("id")
        if cur.get("revision") == 0 and cafe_id and cur_id == cafe_id and cur_id != (active_world["package"].get("meta") or {}).get("id"):
            from model import canonical
            with store.connection() as db:
                db.execute(
                    "UPDATE draft SET package=? WHERE id=1 AND revision=0",
                    (canonical(active_world["package"]),),
                )
    server = ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, store=store))
    server.daemon_threads = True
    server.exports = {}
    server.exports_lock = threading.Lock()
    server.active_world = active_world
    server.active_world_parent = Path(active_world["package_dir"]).parent if active_world is not None else None
    return server


def main():
    parser = argparse.ArgumentParser(description="nyairo world editor — local creator tool")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--world-package", type=Path, default=None, help="Active WorldGen package directory")
    parser.add_argument("--open", action="store_true", help="Open the editor in your browser")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("port must be 0–65535")
    env_wp = os.environ.get("NYAIRO_EDITOR_WORLD_PACKAGE", "").strip()
    wp_dir = args.world_package if args.world_package is not None else (Path(env_wp) if env_wp else None)
    try:
        server = create_server(args.data_dir.resolve(), args.port, world_package_dir=wp_dir)
    except OSError as e:
        print(f"Cannot start editor: {e}. Try another port with --port 8767.", file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"nyairo world editor: {url}\nData: {args.data_dir.resolve()}\nCtrl+C to stop. No AI model is called.", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

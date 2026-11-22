"""End-to-end verification for Hermes hot-reconnect, world_move auto-hydration, and 37/37 phone_life_update surfaces.

Runs strictly inside an isolated ``/tmp/nyhb_*`` sandbox (never touching ``/root/.nyairo-day`` or
``/root/.nyairo-rc1``) with zero paid LLM calls, and cleans up the sandbox on exit.
"""
from __future__ import annotations

import atexit
import hashlib
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
HERMES_ROOT = REPO_ROOT / "vendor" / "hermes"
for _p in (str(REPO_ROOT), str(HERMES_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

FROZEN_ANCHORS: dict[str, tuple[Path, str]] = {
    "autonomous_life_assembly.py": (
        REPO_ROOT / "components" / "life" / "scripts" / "autonomous_life_assembly.py",
        "f14fe6291e1a604520318ebf69acedb583dfc876bfde42f538d6f91017f37434",
    ),
    "life_sharing_service.py": (
        REPO_ROOT / "components" / "life" / "scripts" / "life_sharing_service.py",
        "0becf7df4d9b34881817ff6f1defdcc15929eacbd6183d4c8a38c5a048d46038",
    ),
}


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _http_json(
    base_url: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    csrf: str | None = None,
    capability: str | None = None,
    timeout: float = 10.0,
) -> tuple[int, dict[str, Any]]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(base_url + path, data=data, method=method)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if csrf:
        req.add_header("X-CSRF-Token", csrf)
    if capability:
        req.add_header("Authorization", f"Bearer {capability}")
        req.add_header("X-Phone-Capability", capability)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status), json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return int(exc.code), json.loads(raw)
        except Exception:
            return int(exc.code), {"ok": False, "raw": raw[:300]}


class _MockPluginContext:
    def __init__(self) -> None:
        self.llm = None
        self.context_engine = None
        self.prompt_sections: dict[str, Any] = {}
        self.commands: dict[str, Any] = {}
        self.hooks: dict[str, list[Any]] = {}
        self.tools: dict[str, dict[str, Any]] = {}
        self.unload_callbacks: list[Any] = []

    def register_context_engine(self, engine: Any) -> None:
        self.context_engine = engine

    def register_system_prompt_section(self, name: str, fn: Any, **kwargs: Any) -> None:
        self.prompt_sections[name] = {"fn": fn, "kwargs": kwargs}

    def register_command(self, name: str, handler: Any, **kwargs: Any) -> None:
        self.commands[name] = {"handler": handler, "kwargs": kwargs}

    def register_hook(self, event: str, handler: Any) -> None:
        self.hooks.setdefault(event, []).append(handler)

    def register_tool(self, *, name: str, toolset: str, schema: Any, handler: Any, **kwargs: Any) -> None:
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler, **kwargs}

    def on_unload(self, fn: Any) -> None:
        self.unload_callbacks.append(fn)


_ACTIVE_SANDBOXES: list[tuple[Path, Path]] = []


def _cleanup_sandbox(sandbox_root: Path, day_root: Path) -> None:
    nyairo_day = REPO_ROOT / "scripts" / "nyairo_day.py"
    if day_root.exists() and nyairo_day.is_file():
        try:
            subprocess.run(
                [sys.executable, str(nyairo_day), "stop", "--root", str(day_root), "--json"],
                capture_output=True,
                text=True,
                timeout=35,
            )
        except Exception:
            pass
    if sandbox_root.exists():
        shutil.rmtree(sandbox_root, ignore_errors=True)


def _atexit_cleanup() -> None:
    while _ACTIVE_SANDBOXES:
        sb, dr = _ACTIVE_SANDBOXES.pop()
        _cleanup_sandbox(sb, dr)


atexit.register(_atexit_cleanup)


@unittest.skipUnless(os.name == "posix", "nyairo_day Unix domain sockets require POSIX/Linux")
class HermesHotReconnectAndSurfacesE2ETest(unittest.TestCase):
    sandbox_root: Path
    day_root: Path
    profile_root: Path
    mem_auth_file: Path
    port: int
    base_url: str
    old_hermes_home: str | None

    @classmethod
    def setUpClass(cls) -> None:
        cls.sandbox_root = Path(tempfile.mkdtemp(prefix="nyhb_"))
        cls.day_root = cls.sandbox_root / "day"
        cls.profile_root = cls.sandbox_root / "profile"
        cls.mem_auth_file = cls.sandbox_root / "memory-authority" / "authority.sqlite3"
        cls.mem_auth_file.parent.mkdir(parents=True, exist_ok=True)
        cls.profile_root.mkdir(parents=True, exist_ok=True)
        _ACTIVE_SANDBOXES.append((cls.sandbox_root, cls.day_root))

        cls.port = _pick_free_port()
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        cls.old_hermes_home = os.environ.get("HERMES_HOME")
        os.environ["HERMES_HOME"] = str(cls.profile_root)

        cls._start_nyairo_day()
        cls._write_stale_socket_config()

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            from chiyo_bundle import hermes_plugin
            hermes_plugin.close_services()
        except Exception:
            pass
        if cls.old_hermes_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = cls.old_hermes_home
        _cleanup_sandbox(cls.sandbox_root, cls.day_root)
        if (cls.sandbox_root, cls.day_root) in _ACTIVE_SANDBOXES:
            _ACTIVE_SANDBOXES.remove((cls.sandbox_root, cls.day_root))

    @classmethod
    def _start_nyairo_day(cls) -> dict[str, Any]:
        cmd = [
            sys.executable,
            str(REPO_ROOT / "scripts" / "nyairo_day.py"),
            "start",
            "--root",
            str(cls.day_root),
            "--port",
            str(cls.port),
            "--memory-authority",
            str(cls.mem_auth_file),
            "--world-package",
            "nagiura",
            "--lived-needs",
            "--room-living",
            "--world-actions",
            "--v03-clock-interval",
            "0",
            "--json",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            raise RuntimeError(f"nyairo_day start failed ({proc.returncode}): {proc.stderr or proc.stdout}")
        lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip().startswith("{")]
        return json.loads(lines[-1]) if lines else {}

    @classmethod
    def _stop_nyairo_day(cls) -> None:
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "nyairo_day.py"), "stop", "--root", str(cls.day_root), "--json"],
            capture_output=True,
            text=True,
            timeout=45,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"nyairo_day stop failed ({proc.returncode}): {proc.stderr or proc.stdout}")

    @classmethod
    def _write_stale_socket_config(cls, *, extra_overrides: dict[str, Any] | None = None) -> Path:
        chiyo_dir = cls.profile_root / "chiyo"
        chiyo_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        cap_file = cls.day_root / "run" / "phone-capability.json"
        cfg: dict[str, Any] = {
            "owner": "local-test-owner",
            "cli_owner": True,
            "local_owner_uid": os.getuid(),
            "life": True,
            "memory": True,
            "memory_tool_policy": "restricted",
            # Deliberately bogus/stale paths simulating an un-rewritten config after nyairo_day restart:
            "life_supply_socket": str(cls.day_root / "run" / "supply-serve-old-999.sock"),
            "world_body_socket": str(cls.day_root / "run" / "world-body-old-999.sock"),
            "life_supply_artifact_grant": "grant-stale-old-999",
            "phone": {
                "enabled": True,
                "base_url": cls.base_url,
                "profile_home": str(cls.day_root),
                "capability_file": str(cap_file),
                "write_enabled": True,
                "private_artifacts": True,
            },
        }
        if extra_overrides:
            cfg.update(extra_overrides)
        cfg_path = chiyo_dir / "config.json"
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return cfg_path

    def _load_plugin_module(self) -> Any:
        spec = importlib.util.spec_from_file_location(
            "nyairo_chiyo_plugin_e2e", str(REPO_ROOT / "plugins" / "chiyo" / "__init__.py")
        )
        self.assertIsNotNone(spec)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_01_stale_config_socket_hot_discovery_and_live_restart_reconnect(self) -> None:
        from chiyo_bundle import hermes_plugin, presence

        hermes_plugin.close_services()
        self._write_stale_socket_config()

        inst_1 = json.loads((self.day_root / "instance.json").read_text(encoding="utf-8"))
        sock_1 = str(inst_1["supply_socket"])
        wb_sock_1 = str(inst_1["world_body_socket"])
        self.assertTrue(os.path.exists(sock_1), f"live supply_socket missing: {sock_1}")
        self.assertFalse(os.path.exists(str(self.day_root / "run" / "supply-serve-old-999.sock")))

        # 1) Stale socket in chiyo/config.json -> hot discovery via instance.json succeeds!
        svc = hermes_plugin.services()
        self.assertIsNotNone(svc)
        assert svc is not None
        self.assertEqual(str(svc.native._life_supply_client.socket_path), str(Path(sock_1).resolve()))
        self.assertEqual(str(svc.native.world_body.socket_path), str(Path(wb_sock_1).resolve()))

        status_1 = hermes_plugin.status_command()
        self.assertIn("Life Supply：READY", status_1, status_1)
        self.assertIn("世界身体：READY", status_1, status_1)

        sup_view_1 = presence.supply_view(svc)
        self.assertIsNotNone(sup_view_1)
        assert sup_view_1 is not None
        self.assertEqual(sup_view_1.get("state"), "READY", sup_view_1)

        note_save_1 = hermes_plugin.note_command("热发现笔记 | 旧配置死路径自动走 instance.json")
        self.assertIn("已保存到你的独立 Workspace。文档 ID：", note_save_1, note_save_1)
        art_id_1 = note_save_1.split("文档 ID：", 1)[1].split("；", 1)[0].strip()
        self.assertTrue(art_id_1, f"failed to parse artifact_id from {note_save_1}")

        note_read_1 = hermes_plugin.note_command(f"read {art_id_1}")
        self.assertIn("旧配置死路径自动走 instance.json", note_read_1, note_read_1)

        # 2) Restart nyairo_day -> new PID, new supply_socket, old socket dead; DO NOT restart Hermes!
        self._stop_nyairo_day()
        time.sleep(0.2)
        self._start_nyairo_day()

        inst_2 = json.loads((self.day_root / "instance.json").read_text(encoding="utf-8"))
        sock_2 = str(inst_2["supply_socket"])
        wb_sock_2 = str(inst_2["world_body_socket"])
        self.assertNotEqual(sock_2, sock_1)
        # On Linux SIGTERM, the old Unix socket inode may remain on disk while refusing connections!
        with self.assertRaises((FileNotFoundError, ConnectionRefusedError, OSError)):
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe_sock:
                probe_sock.settimeout(0.5)
                probe_sock.connect(sock_1)
        self.assertTrue(os.path.exists(sock_2), f"new socket should exist: {sock_2}")

        # Also verify `resolve_live_endpoints` detects that `sock_1` (even if its dead .sock file
        # still exists on disk) was superseded by `sock_2` in `<day_root>/instance.json`!
        from chiyo_bundle.live_endpoints import resolve_live_endpoints
        cfg_with_dead_sock1 = json.loads((self.profile_root / "chiyo" / "config.json").read_text(encoding="utf-8"))
        cfg_with_dead_sock1["life_supply_socket"] = sock_1
        ep_after = resolve_live_endpoints(cfg_with_dead_sock1)
        self.assertEqual(str(Path(ep_after["supply_socket"]).resolve()), str(Path(sock_2).resolve()))

        # Verify pre-existing cached `svc` transparently hot-reconnects on direct RPC calls
        # BEFORE even calling `hermes_plugin.services()` again!
        sup_view_2 = presence.supply_view(svc)
        self.assertIsNotNone(sup_view_2)
        assert sup_view_2 is not None
        self.assertEqual(sup_view_2.get("state"), "READY", sup_view_2)
        self.assertEqual(str(svc.native._life_supply_client.socket_path), str(Path(sock_2).resolve()))

        snap_2 = presence.status_snapshot(svc)
        self.assertEqual(snap_2["world_body"]["state"], "READY", snap_2)
        self.assertEqual(str(svc.native.world_body.socket_path), str(Path(wb_sock_2).resolve()))

        # Verify hermes_plugin commands continue seamlessly without touching chiyo/config.json
        status_2 = hermes_plugin.status_command()
        self.assertIn("Life Supply：READY", status_2, status_2)
        self.assertIn("世界身体：READY", status_2, status_2)

        note_read_after_restart = hermes_plugin.note_command(f"read {art_id_1}")
        self.assertIn("旧配置死路径自动走 instance.json", note_read_after_restart, note_read_after_restart)

        note_save_2 = hermes_plugin.note_command("重启后笔记 | 新 PID 透明热重连成功")
        self.assertIn("已保存到你的独立 Workspace。文档 ID：", note_save_2, note_save_2)

    def test_02_all_28_phone_life_update_surfaces_normalization_hints_and_safe_receipts(self) -> None:
        from chiyo_bundle import companion_extras, phone_tool

        self._write_stale_socket_config()

        def call_phone(args: dict[str, Any]) -> dict[str, Any]:
            raw = phone_tool.phone_handler(args, hermes_home=str(self.profile_root), platform="cli")
            return json.loads(raw)

        # Create a work project via HTTP so `home_workbench` `set_tray` has a real artifact_id
        st_code, st_doc = _http_json(self.base_url, "GET", "/api/phone/state")
        self.assertEqual(st_code, 200)
        csrf = str(st_doc["csrf_token"])
        p_code, p_doc = _http_json(
            self.base_url,
            "POST",
            "/api/phone/projects",
            {"title": "海堤水彩小稿", "content": "第一版：灰蓝海雾。", "operation_key": "hb_proj_01"},
            csrf=csrf,
        )
        self.assertIn(p_code, (200, 201), p_doc)
        p_data = p_doc.get("data") if isinstance(p_doc.get("data"), dict) else p_doc
        proj_obj = p_data.get("project") if isinstance(p_data.get("project"), dict) else p_data
        proj_id = str(proj_obj.get("artifact_id") or "")
        self.assertTrue(proj_id, f"expected project artifact_id: {p_doc}")

        # Discover a real walk candidate place from outing_bench_bookstore
        ob_doc = ((call_phone({"action": "outing_bench_bookstore"}).get("result") or {}).get("data") or {})
        bench_candidates = (ob_doc.get("bench") or {}).get("candidate_places") or []
        walk_place_id = "park"
        if isinstance(bench_candidates, list) and bench_candidates:
            first_bp = bench_candidates[0]
            walk_place_id = (
                str(first_bp.get("place_id") or first_bp.get("location_id") or "park")
                if isinstance(first_bp, dict)
                else str(first_bp)
            )

        # Check `body_care` with `desk_side` when no desk is declared: returns honest refusal + supported_actions_hint!
        desk_check = call_phone(
            {
                "action": "phone_life_update",
                "target_surface": "body_care",
                "payload": {"action": "offer", "slot": "drink", "kind": "hot_tea", "position": "desk_side"},
                "operation_key": "hb_desk_check_01",
            }
        )
        if not desk_check.get("ok"):
            self.assertEqual(desk_check.get("reason_code"), "NO_DECLARED_DESK_SURFACE", desk_check)
            self.assertIn("supported_actions_hint", desk_check, desk_check)
            self.assertIn("offer", desk_check["supported_actions_hint"]["subject_actions"])

        secret_eco_note = "SECRET_ECO_NOTE_DO_NOT_LEAK_771"
        secret_stance_note = "SECRET_STANCE_NOTE_DO_NOT_LEAK_772"
        secret_intimacy_text = "SECRET_INTIMACY_BOUNDARY_DO_NOT_LEAK_773"

        surface_payloads: dict[str, dict[str, Any]] = {
            "desk_tomorrow": {"action": "propose", "title": "明天整理晒干的亚麻布", "due_in_minutes": 180},
            # Exercise `op` -> `action` gentle normalization:
            "home_keepsakes": {"op": "clear_lockscreen"},
            "home_workbench": {"action": "set_tray", "work_artifact_id": proj_id, "note": "睡前再改两句"},
            "home_trio": {"action": "set_her_picks", "her_picks": []},
            "home_reunion_anchor": {"action": "capture"},
            "home_recipes": {"action": "upsert", "title": "热蜂蜜蛋奶", "memo": "小火慢温不沸腾"},
            "home_tentative_plans": {"action": "propose", "title": "周末去防波堤散步", "window_note": "周六傍晚"},
            "wardrobe": {"action": "set", "homewear_style": "米白针织开衫", "blanket_season": "spring_autumn"},
            "pocket_lists": {"action": "add", "list": "want_to_try", "title": "海雾焙茶拿铁", "note": "热饮微糖", "shared": True},
            "outing_bench_bookstore": {"action": "bookmark_window_book", "slot_index": 0, "shared": True, "note": "想翻一下目录"},
            "outing_routes_market": {"action": "save_walk_route", "title": "傍晚散步线", "place_ids": [walk_place_id], "shared": True},
            "outing_gallery_blueprint": {
                "action": "upsert_blueprint",
                "title": "阳台香草小架",
                "sketch_note": "双层木格放薄荷与迷迭香",
                "intended_use": "日常浇水与采叶",
            },
            "longterm_habits": {"action": "upsert_habit", "title": "睡前留小夜灯", "habit_text": "玄关留一盏暖光"},
            "longterm_gifts_lantern": {
                "action": "upsert_gift",
                "stage": "draft",
                "title": "手裁海风书签",
                "draft_seed_note": "用浅蓝卡纸配棉线",
            },
            "body_garment_window": {"action": "set_window", "state": "ajar"},
            "body_care": {"action": "offer", "slot": "drink", "kind": "hot_tea", "position": "hand_side"},
            "body_sensory": {"action": "capture_sound_card", "title": "屋檐细雨", "impression_note": "雨点敲在木栏杆上", "shared": True},
            "character_pack": {"action": "update_identity", "pronouns": "她"},
            "autonomy_rhythm": {
                "action": "declare_refusal",
                "topic_label": "代写工作周报",
                "category": "errand_outsourcing",
                "reason_summary": "这是你自己的工作，我不替写",
            },
            "emotional_tension": {
                "action": "add_missing_note",
                "source_ref": "craft:bookmark_01",
                "source_domain": "craft",
                "kind": "MADE",
                "summary": "裁好了两张浅蓝书签想给你看",
            },
            # Exercise `anchor` -> `page_or_anchor` + `note` -> `private_note` normalization:
            "stance_containment": {
                "action": "propose_stance",
                "scope_ref": "work:seawall_sketch",
                "anchor": "p.1",
                "source_revision": "rev_1",
                "facet": "appearance",
                "text": "雨天的海面留一点铅白比纯灰更透气",
                "shared_with_partner": False,
                "note": secret_stance_note,
            },
            "adult_intimacy": {
                "action": "record_private_memory",
                "kind": "BOUNDARY",
                "text": secret_intimacy_text,
            },
            # Exercise `category="window_glance"` -> `domain_kind="ambient_glance"` & `summary` -> `text`:
            "v03_clock": {
                "action": "record_evaporable",
                "category": "window_glance",
                "summary": "瞥见海鸥停在栏杆上",
                "ttl_seconds": 1200,
            },
            # Exercise `job_kind="illustration"` -> `"illustration_commission"` & `note` -> `private_note`:
            "v03_ecology": {
                "action": "complete_independent_work",
                "job_kind": "illustration",
                "work_ref": "job:book_spot_01",
                "amount_cents": 3500,
                "title": "地方志小插图",
                "hours_spent": 2.0,
                "note": secret_eco_note,
            },
            "v03_embodied_social": {
                "action": "restock_pantry",
                "ingredient_id": "eggs",
                "units_added": 4,
            },
            # Exercise `action="water_plant"` -> `action="water_or_move_plant", operation="water"`:
            "v04_neighborhood": {
                "action": "water_plant",
            },
            "v04_builder_trylive": {
                "action": "audit_routes_or_rehearse",
            },
            "v04_elastic_play": {
                "action": "set_day_direction",
                "direction": "今天想在窗边慢慢画完海堤草稿",
            },
            "v02_agency_depth": {
                "action": "record_choice_episode",
                "domain": "reading",
                "chosen": "海边植物图鉴",
                "options": ["海边植物图鉴", "旧街侦探小说"],
                "choice_mode": "spontaneous",
                "shared": True,
            },
            "v02_crossmodal": {
                "action": "try_color_swatches",
                "swatches": ["#346CB0", "#F2E8D5"],
                "note_cn": "试试信纸边角配色",
            },
            "v02_runtime_migration": {
                "action": "record_carrier_check",
                "model_id": "carrier-next-v2",
                "carrier_name": "desktop_local",
                "preview_available": True,
            },
            "v02_recall_projection": {
                "action": "record_episodic_trace",
                "memory_id": "mem_sea_walk_01",
                "topic": "防波堤散步",
                "exact_detail": "傍晚六点在防波堤第三盏灯柱下捡到半枚白贝壳",
                "gist_summary": "傍晚在海堤散步捡了枚贝壳",
                "emotional_salience": 0.65,
            },
            "v02_common_ground": {
                "action": "record_ground_item",
                "item_id": "cg_bookshop_sat",
                "topic": "周六旧书市约定",
                "summary": "周六下午三点在坡道口旧书市碰头",
                "ground_status": "SHARED_WITH_PARTNER",
            },
            # v0.8：对真实可达物件留一张会自然淡去的动态贴纸（主体侧，需本地 capability）。
            "v08_living_traces": {
                "action": "apply_living_trace",
                "object_id": "home_window",
                "gesture_cn": "伸手擦了擦窗玻璃一角",
                "state_sticker_cn": "窗玻璃上被擦出巴掌大一块透亮",
                "fade_mode": "timed",
                "ttl_minutes": 60,
            },
            # v3.0：只看过其中几页的有限视野，其余页严格扣留。
            "v03_encounters": {
                "action": "record_first_glance",
                "target_ref": "reading:seawall_plants",
                "target_title_cn": "海边植物图鉴",
                "seen_pages": [1, 2, 3],
                "total_pages": 12,
                "current_impression_cn": "只翻了几页，图注比正文更耐看",
                "shared": True,
            },
            # v0.8：只有彼此懂的说法（双方都能提出/确认，不强迫照演）。
            "v08_emergent_echoes": {
                "action": "propose_shared_idiom",
                "phrase": "窗边时间",
                "meaning_cn": "各自忙完手头事，在窗边坐一会儿的默契时刻",
                "origin_summary_cn": "周末傍晚一起看海雾时自然叫起来的",
            },
            # v0.8：只留一次的普通身体照料提示（主体侧，需本地 capability）。
            "v08_composable_body": {
                "action": "share_body_care_hint_once",
                "hint_cn": "今晚想早点歇一会儿，不用特意多问",
                "ttl_minutes": 120,
            },
        }

        self.assertEqual(set(surface_payloads.keys()), set(phone_tool.LIFE_UPDATE_SURFACES.keys()))

        failures: dict[str, Any] = {}
        for idx, (surf, pl) in enumerate(sorted(surface_payloads.items()), start=1):
            res = call_phone(
                {
                    "action": "phone_life_update",
                    "target_surface": surf,
                    "payload": pl,
                    "operation_key": f"hb_life_{idx:02d}_{surf}",
                }
            )
            if not (res.get("ok") is True and res.get("http_status") == 200 and res.get("status") in ("applied", "already_applied")):
                failures[surf] = res

        self.assertEqual(failures, {}, f"Expected 37/37 surfaces applied, failures: {json.dumps(failures, ensure_ascii=False)}")

        # Verify self-healing `supported_actions_hint` on bad/incomplete payloads (and zero fact fabrication!)
        err_missing_action = call_phone(
            {"action": "phone_life_update", "target_surface": "wardrobe", "payload": {"homewear_style": "衬衫"}}
        )
        self.assertFalse(err_missing_action["ok"])
        self.assertEqual(err_missing_action["reason_code"], "INVALID_PAYLOAD")
        self.assertIn("set", err_missing_action["supported_actions_hint"]["subject_actions"])

        err_missing_anchor = call_phone(
            {
                "action": "phone_life_update",
                "target_surface": "stance_containment",
                "payload": {
                    "action": "propose_stance",
                    "scope_ref": "work:seawall_sketch",
                    "source_revision": "rev_1",
                    "facet": "appearance",
                    "text": "缺锚点时不捏造默认锚点",
                },
            }
        )
        self.assertFalse(err_missing_anchor["ok"], err_missing_anchor)
        self.assertEqual(err_missing_anchor["http_status"], 400)
        self.assertIn("supported_actions_hint", err_missing_anchor)
        self.assertIn("page_or_anchor", err_missing_anchor["supported_actions_hint"]["core_fields"]["propose_stance"])

        err_unknown_sub = call_phone(
            {
                "action": "phone_life_update",
                "target_surface": "v04_neighborhood",
                "payload": {"action": "totally_nonexistent_subaction"},
            }
        )
        self.assertFalse(err_unknown_sub["ok"], err_unknown_sub)
        self.assertEqual(err_unknown_sub["http_status"], 400)
        self.assertIn("water_or_move_plant", err_unknown_sub["supported_actions_hint"]["subject_actions"])

        # Verify chat action receipts recorded for all 31 applied `phone_life_update` calls AND zero private leakage!
        all_receipts = companion_extras.list_action_receipts(self.profile_root, limit=80)
        life_receipts = [r for r in all_receipts if r.get("action") == "phone_life_update"]
        self.assertGreaterEqual(len(life_receipts), 31, f"Expected >=31 life_update receipts, got {len(life_receipts)}")

        receipts_raw_text = companion_extras.action_receipts_path(self.profile_root).read_text(encoding="utf-8")
        for forbidden_substr in (
            secret_eco_note,
            secret_stance_note,
            secret_intimacy_text,
            "record_private_memory",
            "solo",
        ):
            self.assertNotIn(forbidden_substr, receipts_raw_text)

    def test_03_world_move_live_out_of_the_box_and_explicit_disable_gate(self) -> None:
        from chiyo_bundle import companion_extras, phone_tool, world_move_tool

        self._write_stale_socket_config()
        plugin_mod = self._load_plugin_module()
        ctx = _MockPluginContext()
        plugin_mod.register(ctx)

        guidance = plugin_mod._world_move_guidance()
        max_chars = int(ctx.prompt_sections["chiyo.world-move"]["kwargs"].get("max_chars") or 1200)
        self.assertLessEqual(len(plugin_mod._PHONE_AND_WORLD_GUIDANCE), 1200)
        self.assertLessEqual(len(guidance), max_chars)
        self.assertIn("world_move", guidance)
        self.assertIn("phone_life_update", guidance)

        # Clean minimal environment: ONLY HERMES_HOME (no CHIYO_WORLD_* or WORLD_BODY_* env vars!)
        clean_env = {"HERMES_HOME": str(self.profile_root)}

        phone_tool.phone_handler(
            {
                "action": "world_action",
                "world_action": "POSE",
                "params": {"pose": "standing"},
                "operation_key": "hb_stand_before_move",
            },
            hermes_home=str(self.profile_root),
        )

        target_loc = "厨房"
        move_raw = world_move_tool.move_handler({"location": target_loc}, environment=clean_env)
        move_doc = json.loads(move_raw)
        if move_doc.get("status") == "already_there":
            target_loc = "卧室"
            move_raw = world_move_tool.move_handler({"location": target_loc}, environment=clean_env)
            move_doc = json.loads(move_raw)

        self.assertTrue(move_doc.get("ok"), f"world_move failed under clean env: {move_doc}")
        self.assertIn(move_doc.get("status"), ("arrived", "started"), move_doc)

        # Confirm the canonical world-body owner in nyairo_day recorded the real move/travel!
        world_view = json.loads(
            phone_tool.phone_handler({"action": "world"}, hermes_home=str(self.profile_root))
        )
        self.assertTrue(world_view.get("ok"), world_view)
        world_data = (world_view.get("result") or {}).get("data") or {}
        if move_doc.get("status") == "started":
            travel_obj = world_data.get("travel") or {}
            self.assertEqual(travel_obj.get("destination_location_id"), move_doc.get("destination_location_id"))

        receipts = companion_extras.list_action_receipts(self.profile_root, limit=10)
        self.assertTrue(
            any(r.get("tool") == "world_move" for r in receipts),
            f"expected world_move receipt in {receipts}",
        )

        # Verify explicit `"world_move": false` disable gate is strictly respected
        self._write_stale_socket_config(extra_overrides={"world_move": False})
        try:
            disabled_guidance = plugin_mod._world_move_guidance()
            self.assertNotIn("world_move(location)", disabled_guidance)

            disabled_raw = world_move_tool.move_handler({"location": "玄关"}, environment=clean_env)
            disabled_doc = json.loads(disabled_raw)
            self.assertFalse(disabled_doc.get("ok"), disabled_doc)
            self.assertEqual(disabled_doc.get("status"), "disabled", disabled_doc)
            self.assertEqual(disabled_doc.get("reason_code"), "FEATURE_DISABLED", disabled_doc)
        finally:
            self._write_stale_socket_config()

    def test_04_frozen_anchors_and_zero_llm_calls(self) -> None:
        for name, (fpath, expected_sha) in FROZEN_ANCHORS.items():
            self.assertEqual(_sha256(fpath), expected_sha, f"Frozen anchor mutated: {name}")

        code, living_doc = _http_json(self.base_url, "GET", "/api/phone/living")
        self.assertEqual(code, 200)
        living_data = living_doc.get("data") or {}
        req_ledger = living_data.get("requests") or living_data.get("request_ledger") or {}
        used = req_ledger.get("used", 0) if isinstance(req_ledger, dict) else 0
        self.assertEqual(used, 0, f"Expected 0 paid LLM calls, got {used}")


if __name__ == "__main__":
    unittest.main()

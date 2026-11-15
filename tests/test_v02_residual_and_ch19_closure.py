#!/usr/bin/env python3
"""0.2 全书残余闭环与第 19 章 5 处 P0 代码缺陷综合验收单测（Windows 原生可跑）。

覆盖交付范围（严格对应 15 文件边界）：
1. AG-0/AG-1 自主循环桥（根治孤岛）：
   - ``AutonomousLifeResident.tick()`` 先调用 ``_prepare_v02_v03_pre_tick_candidates`` 准备候选，
     再跑 ``runner_tick``，最后调用 ``_run_v02_v03_bridge_hook`` 推进 ``v02_agency_depth_engine``
     曝光饱和度，并在失败复盘时联动 ``PersonalPracticeService`` 写入 ``TENTATIVE`` 方法草稿；
   - ``AutonomousLifeCycle._extra_opportunities`` 延迟安全拉取 ``v02_v03_daily_loop_bridge``
     候选并生成完整 ``B1-B10`` 机会漏斗统计摘要。
2. 第 19 章 5 处 P0 代码缺陷：
   - ``autonomous_life_cycle.py`` 保留非 ``artifact:`` 前缀的明日惦记/生活待办；
   - ``autonomous_life_cycle.py`` 从预算策略/配置读取 ``max_rounds_per_day / max_cognition_calls_per_day``；
   - ``life_request_budget.py`` 按本地业务时区（默认 ``+08:00``）跨日切分并兼容显式 UTC；
   - ``life_co_presence.py`` 无时区 naive ISO 字符串按本地业务时区（``+08:00``）解析；
   - ``agency_cognition_worker.py`` 动态解析 ``event_kind / cognition_intent / request_status``。
3. ``v02_runtime_governance_migration.py``、``living.py`` 与 ``autonomous_life_events.py`` 联动：
   - 兼容读取真实 ``phone/workbench_tray.json`` 并回退兼容 ``phone/workbench_curio.json``；
   - ``living.py`` 窗口上限提升至 ``3600s / 12次``（桌面第 3 档 ``1800s | 10次`` 合法通过）；
   - ``DORMANT_NO_COGNITION`` / ``LOW_BUDGET_CONSERVATION`` 联动同步 ``living.py`` 暂停/收紧窗口；
   - ``SemanticWakeCoalescer`` 挂入 ``EventFeedPoller`` 事件唤醒去重入口；
   - ``GAP-12`` ``STALLED`` 公式 ``runnable_count > 0 and not held_by_budget and tick_age_s > 225``；
   - 迁居旅行箱 ``suitcase`` 真实读取沉淀方法、作品清单、``character_pack.json`` 与共同信物。
4. ``RecallProjection``、``CommonGround`` 与跨模态/窗边/衣橱桥接：
   - ``v02_recall_projection.py``：``EXACT / RANGE / GIST / UNAVAILABLE`` 四档清晰度与主动淡忘 vs 自然衰减；
   - ``v02_common_ground.py``：三态共同认知账本与 ``UNSEEN_MOVE`` 摸空 -> 寻找/提问 -> 更新认知闭环；
   - ``v02_crossmodal_perception.py``：联动 ``PhoneReading`` 写入 ``unclear`` 批注，联动 ``PrivateProjectService`` 与托盘绑定作品；
   - ``window_keepsake_view.py``：只读联动 ``v04_neighborhood_living`` 真实天气与窗台绿植干湿状态；
   - ``wardrobe_pocket_view.py``：补齐标准可穿戴 ``catalog_id`` 映射。
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import struct
import sys
import tempfile
import types
from typing import Any, Mapping, Optional
import unittest
from unittest import mock
import zoneinfo
import zlib

# ---------------------------------------------------------------------------
# Windows 测试环境兼容垫片（仅在当前测试进程缺少 POSIX fcntl / tzdata 时注入）
# ---------------------------------------------------------------------------
if "fcntl" not in sys.modules:
    try:
        import fcntl as _real_fcntl  # type: ignore[import-not-found]  # noqa: F401
    except ImportError:
        _fcntl_shim = types.ModuleType("fcntl")
        _fcntl_shim.LOCK_SH = 1  # type: ignore[attr-defined]
        _fcntl_shim.LOCK_EX = 2  # type: ignore[attr-defined]
        _fcntl_shim.LOCK_NB = 4  # type: ignore[attr-defined]
        _fcntl_shim.LOCK_UN = 8  # type: ignore[attr-defined]
        _fcntl_shim.flock = lambda fd, op: None  # type: ignore[attr-defined]
        _fcntl_shim.lockf = lambda fd, op, *args: None  # type: ignore[attr-defined]
        sys.modules["fcntl"] = _fcntl_shim

_ORIG_ZONEINFO = zoneinfo.ZoneInfo
try:
    _ORIG_ZONEINFO("Asia/Shanghai")
except Exception:
    _TZ_FALLBACKS = {
        "Asia/Shanghai": timezone(timedelta(hours=8), name="Asia/Shanghai"),
        "UTC": timezone.utc,
        "Etc/UTC": timezone.utc,
    }

    def _compat_zoneinfo(key: str) -> Any:
        try:
            return _ORIG_ZONEINFO(key)
        except Exception:
            if key in _TZ_FALLBACKS:
                return _TZ_FALLBACKS[key]
            raise

    zoneinfo.ZoneInfo = _compat_zoneinfo  # type: ignore[misc,assignment]

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "components" / "world" / "service" / "bridge"),
    str(ROOT / "components" / "supply"),
    str(ROOT / "scripts"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import agency_cognition_worker as acw  # noqa: E402
import autonomous_life_assembly as ala  # noqa: E402
import autonomous_life_cycle as alc  # noqa: E402
import autonomous_life_events as ale  # noqa: E402
import autonomous_life_resident as alr  # noqa: E402
from components.phone import living as phone_living  # noqa: E402
from components.phone import wardrobe_pocket_view as wpv  # noqa: E402
from components.phone import window_keepsake_view as wkv  # noqa: E402
import life_artifact_port as lap  # noqa: E402
import life_co_presence as lcp  # noqa: E402
import life_request_budget as lrb  # noqa: E402
import personal_practice_service as pps  # noqa: E402
import phone_reading as pr  # noqa: E402
import private_daily_items as pdi  # noqa: E402
import private_project_service as prj  # noqa: E402
import v02_agency_depth_engine as v02_ade  # noqa: E402
import v02_common_ground as v02_cg  # noqa: E402
import v02_crossmodal_perception as v02_cm  # noqa: E402
import v02_recall_projection as v02_rp  # noqa: E402
import v02_runtime_governance_migration as v02_rgm  # noqa: E402
import v04_neighborhood_living as v04_nb  # noqa: E402

FROZEN_ANCHORS: dict[str, str] = {
    "components/life/scripts/autonomous_life_assembly.py":
        "5f51c9e82a417d9354d08608841d3472fc5a869e6bd3c9d9a7ebed0b15397f7a",
    "components/life/scripts/life_sharing_service.py":
        "0becf7df4d9b34881817ff6f1defdcc15929eacbd6183d4c8a38c5a048d46038",
    "components/native/native_recall/readonly_recall_adapter.py":
        "6667ff66fe8105f28f436609f0cd8d744b185c0580e7f1642aafef0b873af78a",
}

BOUNDARY_15_FILES: tuple[str, ...] = (
    "components/life/scripts/v02_recall_projection.py",
    "components/life/scripts/v02_common_ground.py",
    "components/life/scripts/v02_agency_depth_engine.py",
    "components/life/scripts/v02_crossmodal_perception.py",
    "components/life/scripts/v02_runtime_governance_migration.py",
    "components/phone/living.py",
    "components/phone/window_keepsake_view.py",
    "components/phone/wardrobe_pocket_view.py",
    "components/life/scripts/agency_cognition_worker.py",
    "components/life/scripts/life_co_presence.py",
    "components/life/scripts/autonomous_life_cycle.py",
    "components/life/scripts/autonomous_life_resident.py",
    "components/life/scripts/life_request_budget.py",
    "components/life/scripts/autonomous_life_events.py",
    "tests/test_v02_residual_and_ch19_closure.py",
)

V02_NEUTRAL_MODULES: tuple[str, ...] = (
    "components/life/scripts/v02_recall_projection.py",
    "components/life/scripts/v02_common_ground.py",
    "components/life/scripts/v02_agency_depth_engine.py",
    "components/life/scripts/v02_crossmodal_perception.py",
    "components/life/scripts/v02_runtime_governance_migration.py",
)


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int, str]]:
    if not root.exists():
        return {}
    snap: dict[str, tuple[int, int, str]] = {}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root)).replace("\\", "/")
        st = p.lstat()
        if p.is_file() and not p.is_symlink():
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            snap[rel] = (int(st.st_size), int(st.st_mtime_ns), digest)
        else:
            snap[rel] = (0, int(st.st_mtime_ns), "dir")
    return snap


def _make_solid_png_bytes(rgb: tuple[int, int, int], width: int = 8, height: int = 8) -> bytes:
    def _chunk(ctype: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(ctype + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + ctype + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes(rgb) * width
    raw = row * height
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(raw))
        + _chunk(b"IEND", b"")
    )


class InMemoryArtifactPort(lap.LifeArtifactPort):
    """继承自 ``LifeArtifactPort`` 的纯内存 owner 端口，支持 Windows 下直连真实服务层测试。"""

    def __init__(self, subject_id: str = "subject:v02-test") -> None:
        super().__init__("/virtual/life-supply.sock", subject_id)
        self.artifacts: dict[str, dict[str, Any]] = {}
        self.versions: dict[str, list[dict[str, Any]]] = {}
        self.operations: dict[str, dict[str, Any]] = {}
        self._serial = 0

    def seed_artifact(
        self,
        *,
        artifact_id: str,
        title: str,
        content: str,
        workspace_id: str = "ws:v02",
        lifecycle: str = "ACTIVE",
    ) -> str:
        tail = artifact_id.split(":", 1)[-1]
        version_id = f"version-{tail}-1"
        self.artifacts[artifact_id] = {
            "artifact_id": artifact_id,
            "subject_id": self.subject_id,
            "workspace_id": workspace_id,
            "title": title,
            "lifecycle": lifecycle,
            "head_version_id": version_id,
            "created_at": 1762992000.0,
            "updated_at": 1762992000.0,
            "revision": 1,
        }
        self.versions[artifact_id] = [
            {
                "version_id": version_id,
                "version_number": 1,
                "parent_version_id": None,
                "content": content,
                "created_at": 1762992000.0,
                "operation_key": f"seed:{artifact_id}",
            }
        ]
        return version_id

    def _head(self, artifact_id: str) -> Optional[dict[str, Any]]:
        row = self.artifacts.get(artifact_id)
        if not row or not row.get("head_version_id"):
            return None
        for ver in self.versions.get(artifact_id, []):
            if ver["version_id"] == row["head_version_id"]:
                return ver
        return None

    def list_artifacts(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.artifacts.values()]

    def list_versions(self, artifact_id: str) -> list[dict[str, Any]]:
        return [dict(ver) for ver in reversed(self.versions.get(artifact_id, []))]

    def read_artifact(self, artifact_id: str) -> dict[str, Any]:
        row = self.artifacts.get(artifact_id)
        if row is None:
            raise lap.ArtifactOwnerRefused("NOT_FOUND", "ARTIFACT_NOT_FOUND", "missing artifact")
        head = self._head(artifact_id)
        return {
            "artifact": dict(row),
            "head": dict(head) if head else None,
            "content": None if head is None else head["content"],
        }

    def read_head(self, artifact_id: str) -> Optional[dict[str, Any]]:
        head = self._head(artifact_id)
        return dict(head) if head else None

    def create_artifact(
        self,
        *,
        workspace_id: str,
        title: str,
        content: str,
        grant_id: str,
        operation_key: str,
        source_refs: Any = None,
    ) -> lap.ArtifactOutcome:
        if operation_key in self.operations:
            saved = self.operations[operation_key]
            return lap.ArtifactOutcome(
                effect_state=lap.EffectState.APPLIED,
                status="OK",
                artifact_id=saved["artifact_id"],
                version_id=saved["version_id"],
                version_number=1,
                head_version_id=saved["version_id"],
                version_created=False,
                operation_key=operation_key,
            )
        self._serial += 1
        artifact_id = f"artifact:{self._serial:032x}"
        version_id = self.seed_artifact(
            artifact_id=artifact_id,
            title=title,
            content=content,
            workspace_id=workspace_id,
        )
        self.operations[operation_key] = {
            "artifact_id": artifact_id,
            "version_id": version_id,
            "version_number": 1,
            "operation_kind": "CREATE_ARTIFACT",
            "result_json": {"artifact_id": artifact_id, "version_id": version_id, "version_number": 1},
        }
        return lap.ArtifactOutcome(
            effect_state=lap.EffectState.APPLIED,
            status="OK",
            artifact_id=artifact_id,
            version_id=version_id,
            version_number=1,
            head_version_id=version_id,
            version_created=True,
            operation_key=operation_key,
        )

    def commit_version(
        self,
        *,
        artifact_id: str,
        expected_head: Optional[str],
        content: str,
        grant_id: str,
        operation_key: str,
        source_refs: Any = None,
    ) -> lap.ArtifactOutcome:
        row = self.artifacts.get(artifact_id)
        if row is None:
            return lap.ArtifactOutcome(
                effect_state=lap.EffectState.NOT_FOUND,
                status="NOT_FOUND",
                reason="ARTIFACT_NOT_FOUND",
                operation_key=operation_key,
            )
        if operation_key in self.operations:
            saved = self.operations[operation_key]
            return lap.ArtifactOutcome(
                effect_state=lap.EffectState.APPLIED,
                status="OK",
                artifact_id=artifact_id,
                version_id=saved["version_id"],
                version_number=saved["version_number"],
                head_version_id=row.get("head_version_id"),
                parent_version_id=saved.get("parent_version_id"),
                version_created=False,
                operation_key=operation_key,
            )
        cur_head = row.get("head_version_id")
        if expected_head is not None and expected_head != cur_head:
            return lap.ArtifactOutcome(
                effect_state=lap.EffectState.CONFLICT,
                status="CONFLICT",
                detail="stale expected_head",
                operation_key=operation_key,
            )
        vers = self.versions[artifact_id]
        next_num = len(vers) + 1
        tail = artifact_id.split(":", 1)[-1]
        version_id = f"version-{tail}-{next_num}"
        vers.append(
            {
                "version_id": version_id,
                "version_number": next_num,
                "parent_version_id": cur_head,
                "content": content,
                "created_at": 1762992100.0 + next_num,
                "operation_key": operation_key,
            }
        )
        row["head_version_id"] = version_id
        row["revision"] = int(row.get("revision") or 1) + 1
        self.operations[operation_key] = {
            "artifact_id": artifact_id,
            "version_id": version_id,
            "version_number": next_num,
            "parent_version_id": cur_head,
            "operation_kind": "COMMIT_MARKDOWN",
            "result_json": {
                "artifact_id": artifact_id,
                "version_id": version_id,
                "version_number": next_num,
                "parent_version_id": cur_head,
            },
        }
        return lap.ArtifactOutcome(
            effect_state=lap.EffectState.APPLIED,
            status="OK",
            artifact_id=artifact_id,
            version_id=version_id,
            version_number=next_num,
            head_version_id=version_id,
            parent_version_id=cur_head,
            version_created=True,
            operation_key=operation_key,
        )

    def operation_row(self, operation_key: str) -> Optional[dict[str, Any]]:
        saved = self.operations.get(str(operation_key))
        return dict(saved) if isinstance(saved, dict) else None

    def resolve_operation(self, operation_key: str) -> lap.ArtifactOutcome:
        saved = self.operations.get(str(operation_key))
        if not saved:
            return lap.ArtifactOutcome(
                effect_state=lap.EffectState.NO_CHANGE,
                status="NOT_FOUND",
                reason="OPERATION_NEVER_LANDED",
                operation_key=operation_key,
            )
        return lap.ArtifactOutcome(
            effect_state=lap.EffectState.APPLIED,
            status="KNOWN",
            artifact_id=saved.get("artifact_id"),
            version_id=saved.get("version_id"),
            version_number=saved.get("version_number"),
            operation_key=operation_key,
        )


class UnboundMemoryStub:
    """最小只读 Memory 接口替身（无外部数据库依赖）。"""

    def status(self) -> dict[str, Any]:
        return {
            "ok": True,
            "status": "READY",
            "reason": None,
            "detail": None,
            "data": {"binding_status": "READY", "recall_ready": True, "owner": "subject:v02-test"},
        }

    def list_experiences(self, *, query: str = "", limit: int = 20, since: Any = None) -> dict[str, Any]:
        return {
            "ok": True,
            "status": "READY",
            "reason": None,
            "detail": None,
            "data": {"experiences": [], "count": 0, "binding_status": "READY"},
        }

    def read_experience(self, experience_id: str) -> dict[str, Any]:
        return {"ok": False, "status": "NOT_FOUND", "reason": "NOT_FOUND", "detail": None, "data": {}}


class DummyRuntimeStub:
    """供 ``AutonomousLifeCycle`` 单元测试使用的轻量 runtime 句柄。"""

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.candidate_read = mock.MagicMock()


# ===========================================================================
# 0. 冻结防线、LF 换行与角色中性检查
# ===========================================================================


class Test00FrozenAnchorsAndCodeHygiene(unittest.TestCase):
    def test_01_frozen_files_sha256_untouched(self) -> None:
        for rel_path, expected_sha256 in FROZEN_ANCHORS.items():
            full_path = ROOT / rel_path
            self.assertTrue(full_path.is_file(), f"Missing frozen file: {rel_path}")
            actual = hashlib.sha256(full_path.read_bytes()).hexdigest()
            self.assertEqual(
                actual,
                expected_sha256,
                f"FROZEN FILE VIOLATION on {rel_path}: expected {expected_sha256}, got {actual}",
            )

    def test_02_all_15_boundary_files_pure_lf_newlines(self) -> None:
        for rel_path in BOUNDARY_15_FILES:
            full_path = ROOT / rel_path
            self.assertTrue(full_path.is_file(), f"Expected boundary file missing: {rel_path}")
            raw = full_path.read_bytes()
            self.assertNotIn(b"\r\n", raw, f"CRLF detected in {rel_path}")
            self.assertNotIn(b"\r", raw, f"CR detected in {rel_path}")

    def test_03_v02_modules_character_neutrality(self) -> None:
        for rel_path in V02_NEUTRAL_MODULES:
            text = (ROOT / rel_path).read_text(encoding="utf-8")
            self.assertNotIn("千代", text, f"Hardcoded persona token found in {rel_path}")


# ===========================================================================
# 1. 必做交付项 #1：AG-0/AG-1 自主循环桥（Resident 前后钩子 + Cycle B1-B10 漏斗）
# ===========================================================================


class Test01Deliverable1AG0AG1BridgeAndFunnel(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.day_root = Path(self._tmp.name) / "day_instance"
        (self.day_root / "phone").mkdir(parents=True, exist_ok=True)
        self.state_root = self.day_root / "life" / "state"
        self.state_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _build_lightweight_resident(
        self,
        *,
        cycle_obj: Any,
        practice_service: Any = None,
    ) -> Any:
        res = alr.AutonomousLifeResident.__new__(alr.AutonomousLifeResident)
        res.state_root = self.state_root
        res.env = {}
        res.act = True
        res.enable_v02_v03_bridge = True
        res.practice_service = practice_service
        res._clock = lambda: datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)
        res._business_clock = None
        res.ticks = 0
        res.last_tick = None
        res.last_error = None
        res.last_v02_v03_bridge = None
        res.message_lane = None
        res.counters = {
            "wakes": 0,
            "by_class": {},
            "outcomes": {},
            "check_windows": 0,
            "event_wakes": 0,
            "real_events": 0,
            "windows_with_material": 0,
            "no_action": 0,
            "defer": 0,
            "paused_windows": 0,
            "blocked_reasons": {},
            "actions": 0,
            "owner_confirmed_versions": 0,
            "owner_version_refs": {},
            "owner_version_unknown": 0,
            "unknown_settlements": 0,
            "pending_unknown_last": 0,
            "pending_unknown_max": 0,
            "reconciliations": 0,
            "journey_arrivals": 0,
            "journey_arrival_refusals": 0,
            "model_calls": 0,
        }
        runner_mock = mock.MagicMock()
        runner_mock.reconcile_pending.return_value = []
        runner_mock.pending_unknown_actions.return_value = []
        res.runtime = types.SimpleNamespace(activity_stop=None)
        res.assembled = {
            "state": ala.READY,
            "cycle": cycle_obj,
            "runner": runner_mock,
            "practice_service": practice_service,
        }
        res.settle_journey = lambda: {"ok": True, "arrived": False}
        res._journal = lambda rec: None
        res._update_process_record = lambda close=False: None
        res._write_summary = lambda payload: None
        res.summary = lambda: {"ok": True}
        return res

    def test_01_resident_tick_prepares_before_runner_and_advances_satiation_after(self) -> None:
        # 种子写入明日便签与就绪孵化线索，确保 v02_v03_daily_loop_bridge 产出真实候选
        (self.day_root / "phone" / "desk_tomorrow_note.json").write_text(
            json.dumps(
                {
                    "status": "OPEN",
                    "note_id": "mint_note_01",
                    "title": "明早检查阳台薄荷扦插水培根系",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        v02_ade.apply_agency_depth_mutation(
            self.day_root,
            {
                "action": "manage_open_incubation",
                "inc_op": "open",
                "topic": "阳台薄荷扦插换盆时机",
                "clue_text": "根系已经长满透明水培杯",
                "shared": True,
                "operation_key": "inc_open_001",
            },
            actor_side="subject",
            now_iso="2026-11-13T10:00:00+08:00",
        )
        v02_ade.apply_agency_depth_mutation(
            self.day_root,
            {
                "action": "manage_open_incubation",
                "inc_op": "add_evidence",
                "topic": "阳台薄荷扦插换盆时机",
                "clue_text": "新买到了透气陶粒和泥炭土",
                "evidence_ref": "receipt:soil-mix-01",
                "shared": True,
                "operation_key": "inc_recheck_001",
            },
            actor_side="subject",
            now_iso="2026-11-13T11:00:00+08:00",
        )

        cycle_stub = types.SimpleNamespace(
            _prepared_v02_v03_collected=None,
            _v02_v03_day_root=None,
        )
        call_order: list[str] = []
        observed_inside_runner: dict[str, Any] = {}

        res = self._build_lightweight_resident(cycle_obj=cycle_stub)

        def _fake_runner_tick(assembled: Mapping[str, Any], *, act: bool = True) -> dict[str, Any]:
            call_order.append("runner_tick")
            # 断言 runner_tick 执行瞬间，_prepare_v02_v03_pre_tick_candidates 已经把候选挂载在 cycle 上！
            observed_inside_runner["prepared"] = getattr(
                assembled["cycle"], "_prepared_v02_v03_collected", None
            )
            return {
                "events": [],
                "feed_errors": [],
                "due": {
                    "at": "2026-11-13T12:00:00+00:00",
                    "kind": "CHECK_WINDOW",
                    "status": "ACTED",
                    "status_class": "ACTED",
                    "outcome": "ACTED",
                    "acted": True,
                    "llm_calls": 0,
                    "extra": {
                        "domain": "horticulture_craft",
                        "activity_title": "薄荷水培转土培记录",
                        "satiation_intensity": 0.45,
                    },
                },
            }

        orig_prepare = res._prepare_v02_v03_pre_tick_candidates
        orig_post = res._run_v02_v03_bridge_hook

        def _tracked_prepare(**kwargs: Any) -> Any:
            call_order.append("pre_prepare")
            return orig_prepare(**kwargs)

        def _tracked_post(*args: Any, **kwargs: Any) -> Any:
            call_order.append("post_bridge")
            return orig_post(*args, **kwargs)

        res._prepare_v02_v03_pre_tick_candidates = _tracked_prepare
        res._run_v02_v03_bridge_hook = _tracked_post

        with mock.patch.object(ala, "runner_tick", side_effect=_fake_runner_tick):
            tick_rec = res.tick()

        self.assertEqual(call_order, ["pre_prepare", "runner_tick", "post_bridge"])
        self.assertIsInstance(observed_inside_runner["prepared"], dict)
        self.assertGreaterEqual(len(observed_inside_runner["prepared"].get("candidates") or []), 1)

        bridge_out = tick_rec.get("v02_v03_bridge") or {}
        self.assertTrue(bridge_out.get("prepared_before_tick"))
        fb = bridge_out.get("post_tick_feedback") or {}
        self.assertTrue(fb.get("satiation_recorded"))
        self.assertEqual(fb.get("satiation_domain"), "horticulture_craft")
        self.assertGreater(float(fb.get("satiation_score") or 0.0), 0.4)

    def test_02_post_tick_failure_creates_tentative_method_in_personal_practice_service(self) -> None:
        port = InMemoryArtifactPort()
        proj_svc = prj.PrivateProjectService(port, "ws:v02", "grant:v02")
        practice_svc = pps.PersonalPracticeService(proj_svc, UnboundMemoryStub())

        # 空闲轮次：100% 零写入
        snap_before = _snapshot_tree(self.day_root)
        idle_fb = v02_ade.record_post_tick_execution_feedback(
            self.day_root,
            tick_record={"due": {"status": "NOT_DUE", "outcome": "NO_ACTION", "acted": False}},
            practice_service=practice_svc,
            now_iso="2026-11-13T12:10:00+08:00",
        )
        self.assertFalse(idle_fb["satiation_recorded"])
        self.assertFalse(idle_fb["tentative_method_created"])
        self.assertEqual(_snapshot_tree(self.day_root), snap_before)

        # 失败复盘轮次：自动写 process_mistake 后悔调整 + PersonalPracticeService TENTATIVE 方法草稿
        cycle_stub = types.SimpleNamespace()
        res = self._build_lightweight_resident(cycle_obj=cycle_stub, practice_service=practice_svc)

        def _failed_runner_tick(assembled: Mapping[str, Any], *, act: bool = True) -> dict[str, Any]:
            return {
                "events": [],
                "feed_errors": [],
                "due": {
                    "at": "2026-11-13T12:20:00+00:00",
                    "kind": "CHECK_WINDOW",
                    "status": "STEP_FAILED",
                    "status_class": "BLOCKED",
                    "outcome": "STEP_FAILED",
                    "acted": False,
                    "llm_calls": 0,
                    "failure_review": {
                        "kind": "process_mistake",
                        "summary": "未先核对书架木蜡油干透时间就叠放画册导致轻微粘连",
                        "method_adjustment_cn": "木蜡油擦涂后先用指节轻触背阴角确认干爽再上架",
                        "scope": "木器养护与书架整理",
                        "method_title": "木蜡油干透核验小法",
                    },
                },
            }

        with mock.patch.object(ala, "runner_tick", side_effect=_failed_runner_tick):
            rec = res.tick()

        fb = (rec.get("v02_v03_bridge") or {}).get("post_tick_feedback") or {}
        self.assertTrue(fb.get("regret_recorded"))
        self.assertTrue(fb.get("tentative_method_created"))
        self.assertEqual(fb.get("tentative_method_state"), "TENTATIVE")
        self.assertTrue(str(fb.get("tentative_method_artifact_id") or "").startswith("artifact:"))

        listed = practice_svc.list_methods()
        self.assertTrue(listed["ok"])
        methods = listed["data"]["projects"]
        self.assertEqual(len(methods), 1)
        self.assertEqual(methods[0]["method_state"], "TENTATIVE")
        self.assertIn("木蜡油", methods[0]["title"])

    def test_03_cycle_extra_opportunities_pulls_v02_v03_candidates_and_b1_b10_funnel(self) -> None:
        (self.day_root / "phone" / "desk_tomorrow_note.json").write_text(
            json.dumps(
                {
                    "status": "OPEN",
                    "note_id": "brew_note_01",
                    "title": "手冲水温与浅烘酸质对比记录",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        v02_ade.apply_agency_depth_mutation(
            self.day_root,
            {
                "action": "manage_open_incubation",
                "inc_op": "open",
                "topic": "手冲水温与浅烘酸质对比",
                "clue_text": "92度时中后段偏涩",
                "shared": True,
                "operation_key": "inc_brew_01",
            },
            actor_side="subject",
            now_iso="2026-11-13T09:00:00+08:00",
        )
        v02_ade.apply_agency_depth_mutation(
            self.day_root,
            {
                "action": "manage_open_incubation",
                "inc_op": "add_evidence",
                "topic": "手冲水温与浅烘酸质对比",
                "clue_text": "改用89度分段注水回甘更干净",
                "evidence_ref": "note:brew-trial-89c",
                "shared": True,
                "operation_key": "inc_brew_02",
            },
            actor_side="subject",
            now_iso="2026-11-13T10:00:00+08:00",
        )

        cycle = alc.AutonomousLifeCycle(DummyRuntimeStub(self.state_root), enabled=True, env={})
        cycle._v02_v03_day_root = self.day_root
        cycle._round_future_items = []
        cycle._round_project_options = []

        extras = cycle._extra_opportunities()
        v02_items = [e for e in extras if str(e.get("source_ref") or "").startswith("fitem:v02v03-")]
        self.assertGreaterEqual(len(v02_items), 1)

        funnel = cycle.opportunity_funnel_summary()
        self.assertEqual(funnel["schema"], "chiyo.life.opportunity_funnel_b1_b10.v1")
        for key in (
            "B1_local_opportunities",
            "B2_handy_and_bounded_budget",
            "B3_self_built_scaffolding",
            "B4_maintenance_and_care",
            "B5_practice_method_transfer",
            "B6_social_opportunities",
            "B7_co_presence_and_resume",
            "B8_expiry_and_quiet_health",
            "B9_historical_retirement_and_archive",
            "B10_causal_attribution_funnel",
        ):
            self.assertIn(key, funnel)
        self.assertGreaterEqual(funnel["B1_local_opportunities"]["v02_v03_bridge_count"], 1)
        self.assertGreaterEqual(
            funnel["B10_causal_attribution_funnel"]["stages"]["admitted_candidates"], 1
        )


# ===========================================================================
# 2. 必做交付项 #2：修复第 19 章指出的 5 处 P0 代码缺陷
# ===========================================================================


class Test02Chapter19FiveP0Defects(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_01_p0_1a_cycle_preserves_non_artifact_tomorrow_notes_and_todos(self) -> None:
        state_root = self.root / "life" / "state"
        state_root.mkdir(parents=True, exist_ok=True)
        cycle = alc.AutonomousLifeCycle(DummyRuntimeStub(state_root), enabled=True, env={})
        cycle._round_project_options = []
        cycle._round_future_items = [
            {
                "item_ref": "fitem:tomorrow-water-fern",
                "title": "明早给窗台铁线蕨喷雾补水",
                "target_refs": ["home_task:windowsill_fern"],
                "due_at": "2026-11-14T08:30:00+08:00",
                "revision": 2,
            },
            {
                "item_ref": "fitem:tomorrow-plain-note",
                "title": "记得问问伴侣周末想不想吃栗子焖饭",
                "target_refs": [],
                "due_at": "2026-11-14T10:00:00+08:00",
                "revision": 1,
            },
            {
                "item_ref": "fitem:artifact-backed",
                "title": "续写散文草稿",
                "target_refs": ["artifact:00000000000000000000000000000001"],
                "due_at": "2026-11-14T11:00:00+08:00",
                "revision": 3,
            },
        ]

        extras = cycle._extra_opportunities()
        by_ref = {row["source_ref"]: row for row in extras}
        self.assertIn("fitem:tomorrow-water-fern", by_ref)
        self.assertEqual(by_ref["fitem:tomorrow-water-fern"]["kind"], "TOMORROW_NOTE_DUE")
        self.assertTrue(by_ref["fitem:tomorrow-water-fern"]["non_artifact_todo"])
        self.assertEqual(
            by_ref["fitem:tomorrow-water-fern"]["target_refs"], ["home_task:windowsill_fern"]
        )

        self.assertIn("fitem:tomorrow-plain-note", by_ref)
        self.assertEqual(by_ref["fitem:tomorrow-plain-note"]["kind"], "TOMORROW_NOTE_DUE")
        self.assertEqual(
            by_ref["fitem:tomorrow-plain-note"]["target_refs"], ["fitem:tomorrow-plain-note"]
        )

        self.assertIn("fitem:artifact-backed", by_ref)
        self.assertEqual(by_ref["fitem:artifact-backed"]["kind"], "FUTURE_ITEM_DUE")

    def test_02_p0_1b_cycle_reads_rounds_and_cognition_caps_from_policy_and_governance(self) -> None:
        # 1) 缺省回退兼容 4 rounds / 12 calls
        s1 = self.root / "s1" / "life" / "state"
        s1.mkdir(parents=True)
        c_default = alc.AutonomousLifeCycle(DummyRuntimeStub(s1), enabled=True, env={})
        self.assertEqual(c_default.budget.max_rounds_per_day, 4)
        self.assertEqual(c_default.budget.max_cognition_calls_per_day, 12)

        # 2) 从持久化 life_budget_policy.json 读取自定义配额
        s2 = self.root / "s2" / "life" / "state"
        s2.mkdir(parents=True)
        (s2 / "life_budget_policy.json").write_text(
            json.dumps({"max_rounds_per_day": 9, "max_cognition_calls_per_day": 28}),
            encoding="utf-8",
        )
        c_policy = alc.AutonomousLifeCycle(DummyRuntimeStub(s2), enabled=True, env={})
        self.assertEqual(c_policy.budget.max_rounds_per_day, 9)
        self.assertEqual(c_policy.budget.max_cognition_calls_per_day, 28)

        # 3) 治理休眠档自动收口为 0 / 0
        d3 = self.root / "s3"
        s3 = d3 / "life" / "state"
        s3.mkdir(parents=True)
        v02_rgm.apply_runtime_migration_mutation(
            d3,
            {"action": "set_budget_mode", "mode": "DORMANT_NO_COGNITION"},
            actor_side="subject",
            now_iso="2026-11-13T10:00:00+08:00",
        )
        c_dormant = alc.AutonomousLifeCycle(DummyRuntimeStub(s3), enabled=True, env={})
        self.assertEqual(c_dormant.budget.max_rounds_per_day, 0)
        self.assertEqual(c_dormant.budget.max_cognition_calls_per_day, 0)

    def test_03_p0_2_life_request_budget_rolls_over_at_local_business_midnight_plus08(self) -> None:
        broot = self.root / "budget_local"
        # 2026-11-11 23:30+08:00 == 2026-11-11 15:30Z
        t_before_midnight = datetime(2026, 11, 11, 15, 30, 0, tzinfo=timezone.utc)
        # 2026-11-12 00:10+08:00 == 2026-11-11 16:10Z（UTC 仍是 11 日，但本地 +08 已跨入 12 日！）
        t_after_midnight = datetime(2026, 11, 11, 16, 10, 0, tzinfo=timezone.utc)

        budget = lrb.LifeRequestBudget(
            broot,
            utc_day_max=2,
            run_window_max=5,
        )
        r1 = budget.reserve(now=t_before_midnight)
        self.assertTrue(bool(r1.get("reservation")))
        self.assertEqual(r1["utc_day_calls"], 1)
        self.assertEqual(r1["business_day"], "2026-11-11")

        r2 = budget.reserve(now=t_before_midnight)
        self.assertTrue(bool(r2.get("reservation")))
        self.assertEqual(r2["utc_day_calls"], 2)
        exhausted = budget.check(now=t_before_midnight)
        self.assertFalse(exhausted["allowed"])

        # 推进到本地 00:10+08:00：跨本地自然日后立即清零当日计数并重新放行！
        chk_new_day = budget.check(now=t_after_midnight)
        self.assertTrue(chk_new_day["allowed"])
        self.assertEqual(chk_new_day["business_day"], "2026-11-12")
        self.assertEqual(chk_new_day["utc_day_calls"], 0)

        r_new = budget.reserve(now=t_after_midnight)
        self.assertTrue(bool(r_new.get("reservation")))
        self.assertEqual(r_new["business_day"], "2026-11-12")
        self.assertEqual(r_new["utc_day_calls"], 1)

        # 兼容显式传 business_tz=timezone.utc 的旧行为
        broot_utc = self.root / "budget_utc"
        budget_utc = lrb.LifeRequestBudget(
            broot_utc,
            utc_day_max=2,
            run_window_max=5,
            business_tz=timezone.utc,
        )
        budget_utc.reserve(now=t_before_midnight)
        chk_utc = budget_utc.check(now=t_after_midnight)
        self.assertEqual(chk_utc["business_day"], "2026-11-11")
        self.assertEqual(chk_utc["utc_day_calls"], 1)

    def test_04_p0_3_life_co_presence_parses_naive_iso_as_local_business_tz_not_utc(self) -> None:
        parsed = lcp._instant("2026-11-12T09:00:00")
        expected_utc = datetime(2026, 11, 12, 1, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(parsed, expected_utc)
        # 与显式 +08:00 完全等价，绝不再偏 8 小时
        self.assertEqual(parsed, lcp._instant("2026-11-12T09:00:00+08:00"))

    def test_05_p0_4_agency_cognition_worker_maps_real_intent_and_request_status(self) -> None:
        ingested_events: list[Any] = []
        current_candidates: dict[str, dict[str, Any]] = {}
        current_cset_refs: list[str] = []

        fake_runtime = types.SimpleNamespace(
            user_request_source=types.SimpleNamespace(
                ingest_typed_event=lambda ev: ingested_events.append(ev)
            ),
            candidate_materializer=types.SimpleNamespace(
                build_candidate_set=lambda observed_at: types.SimpleNamespace(
                    candidate_set_id="cset:test",
                    candidate_refs=list(current_cset_refs),
                )
            ),
            candidate_read=types.SimpleNamespace(
                get_candidate=lambda ref: dict(current_candidates.get(ref) or {})
            ),
        )
        harness = acw.CognitionShadowHarness(
            runtime=fake_runtime,
            install_root=self.root / "shadow_install",
            adapter_factory=lambda obs: object(),
            enabled=True,
        )
        harness._ensure_shadow_service = lambda: object()  # type: ignore[method-assign]
        harness._ensure_worker = lambda: None  # type: ignore[method-assign]

        # 1) 明确请求 / 澄清提问 -> EXPLICIT_USER_REQUEST + OPEN + CLARIFICATION_REQUEST
        current_cset_refs[:] = ["cand:req1"]
        current_candidates["cand:req1"] = {
            "candidate_id": "cand:req1",
            "source_refs": ["ureq:gw:1"],
            "source_kind": "USER_REQUEST",
        }
        out1 = harness.observe_turn(
            session_hash="sess01",
            turn_hash="turn01",
            platform="phone",
            observed_at="2026-11-13T18:00:00+08:00",
            turn_context={
                "cognition_intent": "CLARIFICATION_REQUEST",
                "epistemic_state": "PENDING_CLARIFICATION",
                "target_refs": ["item:blue_fountain_pen"],
            },
        )
        self.assertTrue(out1["enqueued"])
        self.assertEqual(out1["event_kind"], "EXPLICIT_USER_REQUEST")
        self.assertEqual(out1["request_status"], "OPEN")
        self.assertEqual(out1["cognition_intent"], "CLARIFICATION_REQUEST")
        self.assertEqual(ingested_events[-1].event_kind, "EXPLICIT_USER_REQUEST")
        self.assertEqual(ingested_events[-1].request_status, "OPEN")
        self.assertEqual(harness._queue[-1]["cognition_intent"], "CLARIFICATION_REQUEST")

        # 2) 取消请求 -> REQUEST_CANCELLATION + CANCELLED
        current_cset_refs[:] = ["cand:cancel1"]
        current_candidates["cand:cancel1"] = {
            "candidate_id": "cand:cancel1",
            "source_refs": ["ureq:gw:2"],
            "source_kind": "USER_REQUEST",
        }
        out2 = harness.observe_turn(
            session_hash="sess01",
            turn_hash="turn02",
            platform="phone",
            observed_at="2026-11-13T18:01:00+08:00",
            event_kind="REQUEST_CANCELLATION",
            cancelled_by_ref="turn:cancel-pen",
        )
        self.assertTrue(out2["enqueued"])
        self.assertEqual(out2["event_kind"], "REQUEST_CANCELLATION")
        self.assertEqual(out2["request_status"], "CANCELLED")
        self.assertEqual(out2["cognition_intent"], "REQUEST_CANCELLATION")
        self.assertEqual(ingested_events[-1].event_kind, "REQUEST_CANCELLATION")
        self.assertEqual(ingested_events[-1].request_status, "CANCELLED")

        # 3) 普通闲聊缺省回退：无特殊上下文且无候选时保持 ORDINARY_COMMUNICATION + NONE 兼容
        current_cset_refs[:] = []
        out3 = harness.observe_turn(
            session_hash="sess01",
            turn_hash="turn03",
            platform="phone",
            observed_at="2026-11-13T18:02:00+08:00",
        )
        self.assertEqual(out3["event_kind"], "ORDINARY_COMMUNICATION")
        self.assertEqual(out3["request_status"], "NONE")
        self.assertEqual(out3["cognition_intent"], "ORDINARY_COMMUNICATION")
        self.assertEqual(ingested_events[-1].event_kind, "ORDINARY_COMMUNICATION")
        self.assertEqual(ingested_events[-1].request_status, "NONE")


# ===========================================================================
# 3. 必做交付项 #3：v02_runtime_governance_migration + living.py + events 联动
# ===========================================================================


class Test03RuntimeGovernanceMigrationLivingAndEvents(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.day_root = Path(self._tmp.name) / "inst"
        (self.day_root / "phone").mkdir(parents=True, exist_ok=True)
        (self.day_root / "life" / "state").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_01_workbench_tray_json_primary_and_workbench_curio_fallback(self) -> None:
        # 真实 phone/workbench_tray.json 优先识别
        tray_path = self.day_root / "phone" / "workbench_tray.json"
        tray_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "tray": {
                        "work": {
                            "artifact_id": "artifact:tray0001",
                            "title": "雨夜木刻版画草图",
                            "baseline_version_id": "ver-tray-1",
                            "shared": False,
                        }
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        view, _ = v02_rgm.apply_runtime_migration_mutation(
            self.day_root,
            {
                "action": "coalesce_probe",
                "family": "ambient_light",
                "summary_cn": "窗外路灯亮起",
            },
            actor_side="subject",
            now_iso="2026-11-13T19:00:00+08:00",
        )
        desk_surf = view["source_freshness"]["surfaces"]["desk"]
        self.assertTrue(desk_surf["source_exists"])
        self.assertEqual(desk_surf["source_path"], "phone/workbench_tray.json")
        audits = view["workspace_audits"]
        self.assertGreaterEqual(len(audits), 1)
        admitted_titles = [c["title"] for c in audits[-1]["admitted_candidates"]]
        self.assertIn("雨夜木刻版画草图", admitted_titles)

        # 回退兼容旧文件名 phone/workbench_curio.json
        alt_root = Path(self._tmp.name) / "inst_curio_fallback"
        (alt_root / "phone").mkdir(parents=True, exist_ok=True)
        (alt_root / "phone" / "workbench_curio.json").write_text(
            json.dumps({"drafts": [{"id": "d1", "title": "旧托盘兼容速写", "shared": False}]}),
            encoding="utf-8",
        )
        alt_view, _ = v02_rgm.apply_runtime_migration_mutation(
            alt_root,
            {
                "action": "coalesce_probe",
                "family": "ambient_light",
                "summary_cn": "夜雨微声",
            },
            actor_side="subject",
            now_iso="2026-11-13T19:05:00+08:00",
        )
        alt_desk = alt_view["source_freshness"]["surfaces"]["desk"]
        self.assertTrue(alt_desk["source_exists"])
        self.assertEqual(alt_desk["source_path"], "phone/workbench_curio.json")
        alt_titles = [c["title"] for c in alt_view["workspace_audits"][-1]["admitted_candidates"]]
        self.assertIn("旧托盘兼容速写", alt_titles)

    def test_02_living_window_bounds_3600s_12reqs_and_governance_mode_sync(self) -> None:
        self.assertEqual(phone_living.MAX_WINDOW_SECONDS, 3600)
        self.assertEqual(phone_living.MAX_WINDOW_REQUESTS, 12)

        sup = phone_living.Supervisor(self.day_root, environment={})

        # 桌面端第 3 档 1800s | 10次 不再报 DURATION_OUT_OF_RANGE / REQUEST_CAP_OUT_OF_RANGE
        fake_budget_ok = {
            "remaining": 12,
            "ledger": {"state": "absent"},
            "governance_paused": False,
            "effective_max_window_seconds": 3600,
            "effective_max_window_requests": 12,
        }
        fake_proc = mock.MagicMock()
        fake_proc.pid = 43210
        fake_proc.poll.return_value = 0
        with mock.patch.object(phone_living, "window_status", return_value=fake_budget_ok), \
             mock.patch.object(phone_living.subprocess, "Popen", return_value=fake_proc):
            ans = sup.start_window(duration_seconds=1800, max_new_requests=10)
        self.assertTrue(ans["ok"], f"Expected 1800s/10req to succeed, got {ans}")
        self.assertEqual(ans["status"], "STARTED")

        # 切换到 DORMANT_NO_COGNITION：自动同步 living.py 暂停并阻断新开窗口
        v02_rgm.apply_runtime_migration_mutation(
            self.day_root,
            {"action": "set_budget_mode", "mode": "DORMANT_NO_COGNITION", "reason_cn": "夜间休眠托管"},
            actor_side="subject",
            now_iso="2026-11-13T23:00:00+08:00",
        )
        dormant_ans = sup.start_window(duration_seconds=120, max_new_requests=1)
        self.assertFalse(dormant_ans["ok"])
        self.assertEqual(dormant_ans["reason"], "DORMANT_NO_COGNITION_PAUSED")

        # 切换到 LOW_BUDGET_CONSERVATION：自动收紧窗口上限为 300s / 2次
        v02_rgm.apply_runtime_migration_mutation(
            self.day_root,
            {"action": "set_budget_mode", "mode": "LOW_BUDGET_CONSERVATION", "budget_scale_pct": 15},
            actor_side="subject",
            now_iso="2026-11-13T23:10:00+08:00",
        )
        over_low_sec = sup.start_window(duration_seconds=1800, max_new_requests=1)
        self.assertFalse(over_low_sec["ok"])
        self.assertEqual(over_low_sec["reason"], "DURATION_OUT_OF_RANGE")
        over_low_req = sup.start_window(duration_seconds=180, max_new_requests=5)
        self.assertFalse(over_low_req["ok"])
        self.assertEqual(over_low_req["reason"], "REQUEST_CAP_OUT_OF_RANGE")

    def test_03_event_feed_poller_wires_semantic_wake_coalescer(self) -> None:
        class StaticBatchFeed:
            def __init__(self, batch: list[dict[str, Any]]) -> None:
                self._batch = batch

            def poll(self) -> list[dict[str, Any]]:
                items, self._batch = self._batch, []
                return items

        burst = [
            {"kind": ale.EVENT_ARTIFACT_CHANGED, "ref": "artifact:doc1:v2", "detail": "edit 1"},
            {"kind": ale.EVENT_ARTIFACT_CHANGED, "ref": "artifact:doc1:v3", "detail": "edit 2"},
            {"kind": ale.EVENT_ARTIFACT_CHANGED, "ref": "artifact:doc1:v4", "detail": "edit 3"},
            {"kind": ale.EVENT_RESUME_ELIGIBILITY_OPENED, "ref": "elig:user-reply", "detail": "partner replied"},
        ]
        poller = ale.EventFeedPoller(
            [StaticBatchFeed(burst)],
            day_root=self.day_root,
            coalesce_window_seconds=600,
        )
        emitted = poller.poll(now_iso="2026-11-13T20:00:00+08:00")
        self.assertEqual(poller.last_errors, [])
        # 3 条同家族 ARTIFACT_CHANGED 合并为 1 条有效后台唤醒 + 1 条控制通道零延迟直通 = 共 2 条
        self.assertEqual(len(emitted), 2)
        kinds = [e["kind"] for e in emitted]
        self.assertEqual(kinds, [ale.EVENT_ARTIFACT_CHANGED, ale.EVENT_RESUME_ELIGIBILITY_OPENED])
        self.assertEqual(len(poller.last_coalesced_events), 2)
        self.assertEqual(len(poller.last_routing_decisions), 4)

    def test_04_gap12_stalled_formula_never_misreports_held_by_budget(self) -> None:
        self.assertFalse(
            v02_rgm.evaluate_gap12_stalled(runnable_count=80, held_by_budget=True, tick_age_s=900.0)
        )
        self.assertFalse(
            v02_rgm.evaluate_gap12_stalled(runnable_count=5, held_by_budget=False, tick_age_s=225.0)
        )
        self.assertTrue(
            v02_rgm.evaluate_gap12_stalled(runnable_count=5, held_by_budget=False, tick_age_s=226.0)
        )

        # HELD_BY_BUDGET 且队列积压 >= 50、tick_age_s = 600s -> 绝不误报 STALLED_OR_DEGRADED！
        budget_held_root = Path(self._tmp.name) / "inst_held_by_budget"
        budget_held_root.mkdir(parents=True)
        (budget_held_root / "ready.json").write_text(
            json.dumps(
                {
                    "ready": True,
                    "status": "HELD_BY_BUDGET",
                    "held_by_budget": True,
                    "queue_backlog": 65,
                    "runnable_count": 65,
                    "tick_age_s": 600,
                    "oldest_unacked_seconds": 1200,
                }
            ),
            encoding="utf-8",
        )
        held_proj = v02_rgm.project_v02_runtime_migration(
            budget_held_root, has_subject_capability=True, now_iso="2026-11-13T15:00:00+08:00"
        )
        self.assertNotEqual(
            held_proj["runtime_mode"]["health_state"], v02_rgm.HEALTH_STALLED_OR_DEGRADED
        )
        self.assertTrue(held_proj["runtime_mode"]["instance_signals"]["held_by_budget"])
        self.assertFalse(held_proj["runtime_mode"]["instance_signals"]["gap12_stalled"])

        # 未被预算挂起且 runnable_count > 0、tick_age_s = 300 > 225 -> 准确报 STALLED_OR_DEGRADED！
        stalled_root = Path(self._tmp.name) / "inst_real_stalled"
        stalled_root.mkdir(parents=True)
        (stalled_root / "ready.json").write_text(
            json.dumps(
                {
                    "ready": True,
                    "status": "RUNNING",
                    "held_by_budget": False,
                    "runnable_count": 3,
                    "tick_age_s": 300,
                }
            ),
            encoding="utf-8",
        )
        stalled_proj = v02_rgm.project_v02_runtime_migration(
            stalled_root, has_subject_capability=True, now_iso="2026-11-13T15:00:00+08:00"
        )
        self.assertEqual(
            stalled_proj["runtime_mode"]["health_state"], v02_rgm.HEALTH_STALLED_OR_DEGRADED
        )
        self.assertTrue(stalled_proj["runtime_mode"]["instance_signals"]["gap12_stalled"])

    def test_05_suitcase_reads_real_practice_methods_works_character_pack_and_keepsakes(self) -> None:
        # 1) 真实 Supply SQLite 写入 1 条 PersonalPracticeService 方法 + 1 件私人作品
        db_dir = self.day_root / "supply" / "data"
        db_dir.mkdir(parents=True, exist_ok=True)
        db_path = db_dir / "life-supply.sqlite3"
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute(
                """CREATE TABLE managed_artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    title TEXT,
                    lifecycle TEXT,
                    head_version_id TEXT,
                    updated_at REAL
                )"""
            )
            conn.execute(
                """CREATE TABLE artifact_versions (
                    version_id TEXT PRIMARY KEY,
                    artifact_id TEXT,
                    version_number INTEGER,
                    content TEXT
                )"""
            )
            method_marker = (
                '<!-- nyairo-practice:v1 {"instruction":"先试涂小块留白再铺底色",'
                '"kind":"method","method_state":"TRIED","scope":"水彩风景",'
                '"title":"留白试色法","uncertainty":"阴雨天干速待测"} -->\n\n正文记录'
            )
            conn.execute(
                "INSERT INTO managed_artifacts VALUES (?, ?, 'ACTIVE', ?, 1762995000.0)",
                ("artifact:method0001", "留白试色法", "ver-m-1"),
            )
            conn.execute(
                "INSERT INTO artifact_versions VALUES (?, ?, 1, ?)",
                ("ver-m-1", "artifact:method0001", method_marker),
            )
            conn.execute(
                "INSERT INTO managed_artifacts VALUES (?, ?, 'ACTIVE', ?, 1762996000.0)",
                ("artifact:work0001", "秋日银杏书签手稿", "ver-w-2"),
            )
            conn.execute(
                "INSERT INTO artifact_versions VALUES (?, ?, 2, ?)",
                ("ver-w-2", "artifact:work0001", "银杏叶脉与金粉勾边第二稿。"),
            )
            conn.commit()
        finally:
            conn.close()

        # 2) 真实 character_pack.json 与共同信物
        (self.day_root / "character_pack.json").write_text(
            json.dumps(
                {
                    "pack_id": "pack:autumn-studio",
                    "display_name": "秋日画室角色包",
                    "version": "1.2.0",
                    "traits": ["安静观察", "偏爱手作"],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (self.day_root / "phone" / "window_keepsake.json").write_text(
            json.dumps(
                {
                    "keepsakes": [
                        {"id": "ks_ticket", "title": "海边电车纪念票根", "shared": True}
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        proj = v02_rgm.project_v02_runtime_migration(
            self.day_root, has_subject_capability=True, now_iso="2026-11-13T16:00:00+08:00"
        )
        suit = proj["migration_suitcase"]
        real_src = suit["real_sources"]
        self.assertTrue(real_src["has_real_sources"])
        self.assertEqual(real_src["character_pack"]["pack_id"], "pack:autumn-studio")
        method_titles = [m["title"] for m in real_src["practice_methods"]]
        self.assertIn("留白试色法", method_titles)
        work_titles = [w["title"] for w in real_src["private_works"]]
        self.assertIn("秋日银杏书签手稿", work_titles)
        ks_titles = [k["title_cn"] for k in real_src["shared_keepsakes"]]
        self.assertTrue(any("海边电车纪念票根" in t for t in ks_titles))

        carried_blob = " ".join(item["title_cn"] for item in suit["carried_items"])
        self.assertIn("秋日画室角色包", carried_blob)
        self.assertIn("留白试色法", carried_blob)
        self.assertIn("秋日银杏书签手稿", carried_blob)
        self.assertIn("海边电车纪念票根", carried_blob)


# ===========================================================================
# 4. 必做交付项 #4：RecallProjection、CommonGround、跨模态、窗边与衣橱真实桥接
# ===========================================================================


class Test04RecallCommonGroundCrossmodalWindowWardrobe(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.day_root = Path(self._tmp.name) / "inst"
        (self.day_root / "phone").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_01_recall_projection_four_clarity_levels_active_fade_archive_and_zero_write(self) -> None:
        empty_root = Path(self._tmp.name) / "empty_recall"
        snap0 = _snapshot_tree(empty_root)
        empty_view = v02_rp.read_v02_recall_projection_view(
            empty_root, now_iso="2026-11-13T18:00:00+08:00"
        )
        self.assertEqual(empty_view["clarity_counts"]["EXACT"], 0)
        self.assertEqual(_snapshot_tree(empty_root), snap0)

        now_iso = "2026-11-13T18:00:00+08:00"
        # 1) 近期记忆 -> EXACT
        r_exact = v02_rp.record_episodic_trace(
            self.day_root,
            memory_id="mem_recent",
            topic="傍晚煮热苹果肉桂茶",
            exact_detail="六点十分切了半颗红富士配一根肉桂棒小火慢煮。",
            occurred_at="2026-11-12T18:10:00+08:00",
            emotional_salience=0.55,
            now_iso=now_iso,
        )
        self.assertEqual(r_exact["projection"]["clarity"], v02_rp.CLARITY_EXACT)

        # 2) 两周前普通日常 -> RANGE（带模糊日期区间且包含真实发生日）
        r_range = v02_rp.record_episodic_trace(
            self.day_root,
            memory_id="mem_mid",
            topic="去街角文具店买替芯",
            exact_detail="买了两支 0.38 蓝黑替芯。",
            occurred_at="2026-10-28T15:00:00+08:00",
            emotional_salience=0.45,
            now_iso=now_iso,
        )
        self.assertEqual(r_range["projection"]["clarity"], v02_rp.CLARITY_RANGE)
        f_range = r_range["projection"]["fuzzy_date_range"]
        self.assertLessEqual(f_range["start_date"], "2026-10-28")
        self.assertGreaterEqual(f_range["end_date"], "2026-10-28")
        self.assertFalse(r_range["projection"]["is_verbatim_quote"])

        # 3) 两个月前日常琐事 -> GIST（只剩大概印象，绝不伪造逐字原话）
        r_gist = v02_rp.record_episodic_trace(
            self.day_root,
            memory_id="mem_old_gist",
            topic="整理过季薄毯",
            exact_detail="把米色华夫格薄毯折好放进了衣柜下层。",
            gist_summary="秋天换季时收拾过一回薄毯子。",
            occurred_at="2026-09-01T14:00:00+08:00",
            emotional_salience=0.40,
            now_iso=now_iso,
        )
        self.assertEqual(r_gist["projection"]["clarity"], v02_rp.CLARITY_GIST)
        self.assertIsNone(r_gist["projection"]["exact_detail"])
        self.assertFalse(r_gist["projection"]["is_verbatim_quote"])

        # 翻档案核对 != 一直记得
        checked_proj = v02_rp.project_memory_item(
            r_gist["memory"],
            now_iso=now_iso,
            consulted_archive=True,
            archive_ref="diary:2026-09-01#L3",
        )
        self.assertEqual(checked_proj["clarity"], v02_rp.CLARITY_EXACT)
        self.assertTrue(checked_proj["reconstructed_from_archive"])
        self.assertEqual(
            checked_proj["attribution_mode"], v02_rp.ATTRIBUTION_ARCHIVE_CHECKED
        )

        # 4) 一年前极低显著度鸡毛蒜皮 -> 自然衰减 UNAVAILABLE
        r_unavail = v02_rp.record_episodic_trace(
            self.day_root,
            memory_id="mem_ancient_trivial",
            topic="去年某天午后擦过一次窗框",
            exact_detail="随手拿纸巾擦了窗框灰尘。",
            occurred_at="2025-05-01T13:00:00+08:00",
            emotional_salience=0.10,
            now_iso=now_iso,
        )
        self.assertEqual(r_unavail["projection"]["clarity"], v02_rp.CLARITY_UNAVAILABLE)

        # 5) 主动淡忘（soft_let_go vs deep_let_go）：立刻阻断主动提及，软放下退为 GIST，深放下直接 UNAVAILABLE
        fade_soft = v02_rp.mark_active_fade(
            self.day_root,
            memory_id="mem_recent",
            fade_strength=v02_rp.FADE_STRENGTH_SOFT,
            now_iso=now_iso,
        )
        self.assertEqual(fade_soft["direct_projection"]["decay_mode"], v02_rp.DECAY_ACTIVE_FADE)
        self.assertEqual(fade_soft["direct_projection"]["clarity"], v02_rp.CLARITY_GIST)
        self.assertFalse(fade_soft["direct_projection"]["allow_proactive_mention"])
        self.assertEqual(
            fade_soft["proactive_projection"]["clarity"], v02_rp.CLARITY_UNAVAILABLE
        )

        fade_deep = v02_rp.mark_active_fade(
            self.day_root,
            memory_id="mem_recent",
            fade_strength=v02_rp.FADE_STRENGTH_DEEP,
            now_iso=now_iso,
        )
        self.assertEqual(fade_deep["direct_projection"]["clarity"], v02_rp.CLARITY_UNAVAILABLE)

        # GET 读投影零磁盘写入核验
        snap_before_read = _snapshot_tree(self.day_root)
        _ = v02_rp.read_v02_recall_projection_view(self.day_root, now_iso=now_iso)
        _ = v02_rp.summarize_recall_for_cognition(self.day_root, now_iso=now_iso)
        self.assertEqual(_snapshot_tree(self.day_root), snap_before_read)

    def test_02_common_ground_three_states_receipt_chain_and_unseen_move_loop(self) -> None:
        now_iso = "2026-11-13T18:30:00+08:00"
        # 1) 私下未分享事项：伴侣视角严格零泄露
        v02_cg.record_ground_item(
            self.day_root,
            item_id="priv_gift_sketch",
            topic="悄悄准备的围巾配色草稿",
            summary="打算挑燕麦色配深灰织一条围巾。",
            ground_status=v02_cg.PRIVATE_UNSHARED,
            now_iso=now_iso,
        )
        # 2) 需要达成一致的计划：SENT -> READ 仍为 PENDING_CLARIFICATION，AGREED 才进 SHARED_WITH_PARTNER
        plan_res = v02_cg.record_ground_item(
            self.day_root,
            item_id="plan_weekend_market",
            topic="周六早市采购",
            summary="周六九点一起去河岸早市买板栗",
            requires_agreement=True,
            delivery_state=v02_cg.DELIVERY_SENT,
            now_iso=now_iso,
        )
        self.assertEqual(plan_res["item"]["ground_status"], v02_cg.PENDING_CLARIFICATION)
        after_read = v02_cg.advance_communication_receipt(
            self.day_root,
            item_id="plan_weekend_market",
            delivery_state=v02_cg.DELIVERY_READ,
            now_iso=now_iso,
        )
        self.assertEqual(after_read["item"]["ground_status"], v02_cg.PENDING_CLARIFICATION)
        after_agreed = v02_cg.advance_communication_receipt(
            self.day_root,
            item_id="plan_weekend_market",
            delivery_state=v02_cg.DELIVERY_AGREED,
            now_iso=now_iso,
        )
        self.assertEqual(after_agreed["item"]["ground_status"], v02_cg.SHARED_WITH_PARTNER)

        # 改期后旧确认立即过期退回 PENDING_CLARIFICATION
        revised = v02_cg.revise_shared_plan(
            self.day_root,
            item_id="plan_weekend_market",
            revised_summary="周六预报小雨，改到周日上午十点去早市",
            now_iso=now_iso,
        )
        self.assertEqual(revised["item"]["ground_status"], v02_cg.PENDING_CLARIFICATION)
        self.assertTrue(revised["item"]["agreement_expired"])

        # 3) UNSEEN_MOVE 完整闭环：初始放书桌 -> 伴侣趁不在场挪到茶水柜抽屉 -> 主体按旧印象去书桌摸空 -> 附近没找着生成提问 -> 伴侣告知后更新认知
        v02_cg.register_object_belief(
            self.day_root,
            object_id="brass_scissors",
            object_label="黄铜小剪刀",
            spot="书桌笔筒旁",
            shared_with_partner=True,
            now_iso=now_iso,
        )
        move_res = v02_cg.record_partner_unseen_move(
            self.day_root,
            object_id="brass_scissors",
            new_spot="玄关收纳柜第二格",
            witnessed_by_subject=False,
            now_iso=now_iso,
        )
        self.assertEqual(move_res["event_kind"], v02_cg.EVENT_UNSEEN_MOVE)
        self.assertEqual(move_res["object_belief"]["subject_believed_spot"], "书桌笔筒旁")
        self.assertEqual(move_res["object_belief"]["actual_spot"], "玄关收纳柜第二格")

        # 主体读侧绝不剧透 actual_spot，且伴侣读侧绝不泄露 PRIVATE_UNSHARED
        subj_view_before = v02_cg.read_v02_common_ground_view(
            self.day_root, viewer="subject", now_iso=now_iso
        )
        self.assertNotIn("actual_spot", subj_view_before["object_beliefs"][0])
        partner_view = v02_cg.read_v02_common_ground_view(
            self.day_root, viewer="partner", now_iso=now_iso
        )
        partner_ids = [i["item_id"] for i in partner_view["items"]]
        self.assertNotIn("priv_gift_sketch", partner_ids)
        self.assertEqual(partner_view["private_withheld_count"], 1)

        # 第一步按旧印象去「书桌笔筒旁」伸手 -> 真实摸空！
        fetch_miss = v02_cg.attempt_use_or_fetch_object(
            self.day_root, object_id="brass_scissors", now_iso=now_iso
        )
        self.assertEqual(fetch_miss["outcome"], v02_cg.OUTCOME_MISSED_AT_EXPECTED)
        self.assertEqual(fetch_miss["checked_spot"], "书桌笔筒旁")
        self.assertTrue(fetch_miss["should_search_or_ask"])
        self.assertNotIn("actual_spot", fetch_miss["object_belief"])

        # 顺手在附近书架找了一圈没找到 -> 生成自然开口问伴侣提示
        nearby_miss = v02_cg.search_nearby_spots(
            self.day_root,
            object_id="brass_scissors",
            candidate_spots=["书桌抽屉", "窗台笔盒"],
            now_iso=now_iso,
        )
        self.assertEqual(nearby_miss["outcome"], v02_cg.OUTCOME_STILL_MISSING_ASK)
        self.assertTrue(nearby_miss["should_ask_partner"])
        self.assertIn("黄铜小剪刀", nearby_miss["natural_question_prompt"])

        # 伴侣回答新位置后 -> 更新信念为「玄关收纳柜第二格」并收口为 SHARED_WITH_PARTNER
        resolved = v02_cg.resolve_unseen_move_with_partner(
            self.day_root,
            object_id="brass_scissors",
            partner_told_spot="玄关收纳柜第二格",
            now_iso=now_iso,
        )
        self.assertEqual(resolved["outcome"], v02_cg.OUTCOME_CLARIFIED_BY_PARTNER)
        self.assertEqual(resolved["updated_believed_spot"], "玄关收纳柜第二格")
        self.assertEqual(resolved["ground_item"]["ground_status"], v02_cg.SHARED_WITH_PARTNER)

        # 再次去拿 -> 直接在更新后的位置拿到
        fetch_hit = v02_cg.attempt_use_or_fetch_object(
            self.day_root, object_id="brass_scissors", now_iso=now_iso
        )
        self.assertEqual(fetch_hit["outcome"], v02_cg.OUTCOME_FOUND_AT_EXPECTED)

        # GET 零磁盘写入核验
        snap_cg = _snapshot_tree(self.day_root)
        _ = v02_cg.read_v02_common_ground_view(self.day_root, viewer="subject", now_iso=now_iso)
        _ = v02_cg.summarize_common_ground_for_cognition(self.day_root, now_iso=now_iso)
        self.assertEqual(_snapshot_tree(self.day_root), snap_cg)

    def test_03_crossmodal_bridges_phone_reading_unclear_and_private_project_service(self) -> None:
        port = InMemoryArtifactPort()
        read_aid = "artifact:000000000000000000000000000000a1"
        pdi_aid = "artifact:000000000000000000000000000000a2"
        port.seed_artifact(
            artifact_id=read_aid,
            title="色彩构成讲义",
            content="第一页：明度对比实验%%PAGE%%第二页：冷暖纯度推移与空间层次",
        )
        port.seed_artifact(
            artifact_id=pdi_aid,
            title="私人日常事项",
            content=pdi.encode_document(
                pdi._empty_document("subject:v02-test", pdi_aid, "ws:v02")
            ),
        )
        reading_facade = pr.PhoneReading(
            port,
            reading_artifact_id=read_aid,
            private_items_artifact_id=pdi_aid,
            workspace_id="ws:v02",
            grant_id="grant:v02",
        )
        proj_svc = prj.PrivateProjectService(
            port,
            "ws:v02",
            "grant:v02",
            excluded_artifact_ids=(read_aid, pdi_aid),
        )
        created_work = proj_svc.create_project(
            title="暮色海港水彩习作",
            content="初稿记录：天际线留白与暖橙倒影。",
            operation_key="create_harbor_work_01",
        )
        self.assertTrue(created_work["ok"])
        work_id = created_work["artifact_id"]

        # 准备一个空的规范 workbench_tray.json 以验证 apply_palette_to_work 自动挂载托盘
        tray_file = self.day_root / "phone" / "workbench_tray.json"
        tray_file.write_text(
            json.dumps({"schema_version": 1, "revision": 0, "updated_at": None, "tray": {"work": None}}),
            encoding="utf-8",
        )

        # 1) mark_reading_uncertain -> 自动联动 PhoneReading.annotate("unclear", ...)
        mark_proj, _ = v02_cm.apply_crossmodal_mutation(
            self.day_root,
            {
                "action": "mark_reading_uncertain",
                "book_title": "色彩构成讲义",
                "page": 2,
                "state": "seen_not_understood",
                "passage_hint": "冷暖纯度推移在暗部交界处怎么避免显脏？",
                "share_with_room": False,
                "operation_key": "cm_read_unclear_01",
            },
            actor_side="subject",
            now_iso="2026-11-13T20:00:00+08:00",
            phone_reading=reading_facade,
        )
        marks = mark_proj["reading_uncertainty_marks"]
        self.assertEqual(len(marks), 1)
        self.assertTrue(marks[0].get("phone_reading_synced"), f"Sync failed: {marks[0]}")
        self.assertEqual(marks[0].get("phone_reading_annotation_kind"), "unclear")

        mat_after = reading_facade.material(2)
        self.assertTrue(mat_after["ok"])
        anns = mat_after["data"]["annotations"]
        self.assertTrue(any(a.get("kind") == "unclear" for a in anns))

        # 2) 提取色板并 apply_palette_to_work -> 真实核验绑定 PrivateProjectService 作品并同步托盘
        png_b64 = base64.b64encode(_make_solid_png_bytes((232, 122, 61))).decode("ascii")
        pal_proj, _ = v02_cm.apply_crossmodal_mutation(
            self.day_root,
            {
                "action": "analyze_image_palette",
                "title": "傍晚码头暖橙霞光",
                "image_base64": png_b64,
                "share_with_room": True,
                "operation_key": "cm_pal_01",
            },
            actor_side="subject",
            now_iso="2026-11-13T20:05:00+08:00",
        )
        study_id = pal_proj["palette_studies"][0]["study_id"]

        app_proj, _ = v02_cm.apply_crossmodal_mutation(
            self.day_root,
            {
                "action": "apply_palette_to_work",
                "study_id": study_id,
                "work_id": work_id,
                "work_title": "暮色海港水彩习作",
                "usage_note_cn": "把码头暖橙色块铺在水面高光边缘",
                "share_with_room": True,
                "operation_key": "cm_apply_work_01",
            },
            actor_side="subject",
            now_iso="2026-11-13T20:10:00+08:00",
            project_service=proj_svc,
        )
        apps = app_proj["work_palette_applications"]
        self.assertEqual(len(apps), 1)
        self.assertTrue(apps[0]["work_verified"])
        self.assertEqual(apps[0]["work_binding_source"], "private_project_service")
        self.assertTrue(apps[0]["tray_linked"])
        tray_doc = json.loads(tray_file.read_text(encoding="utf-8"))
        self.assertEqual(tray_doc["tray"]["work"]["artifact_id"], work_id)

    def test_04_window_keepsake_view_links_v04_weather_and_plant_wet_dry_readonly(self) -> None:
        nb_state = v04_nb.default_state()
        nb_state["environment"]["weather"] = "drizzle"
        nb_state["environment"]["airflow"] = "strong"
        nb_state["environment"]["season"] = "autumn"
        nb_state["environment"]["updated_at"] = "2026-11-13T17:00:00+08:00"
        nb_state["potted_plant"]["plant_id"] = "windowsill_mint"
        nb_state["potted_plant"]["name"] = "窗台小薄荷"
        nb_state["potted_plant"]["place_id"] = "home"
        nb_state["potted_plant"]["hydration"] = 18.5
        nb_state["potted_plant"]["segment_start_hydration"] = 18.5
        nb_state["potted_plant"]["stage"] = "leafy"
        v04_nb.save_state_atomic(self.day_root, nb_state)

        world_home_stub = {
            "ok": True,
            "data": {
                "location": {"status": "available", "place_id": "home", "area_id": "desk_window"},
                "local_environment": {
                    "status": "available",
                    "area": {"area_id": "desk_window"},
                    "furniture": [
                        {"object_id": "windowsill_mint", "name": "窗台小薄荷", "kind": "plant"}
                    ],
                    "light": {"status": "available", "power": "on", "band": "soft_daylight"},
                },
                "world": {"local_time": "2026-11-13T17:05:00+08:00"},
            },
        }

        snap_before = _snapshot_tree(self.day_root)
        view = wkv.window_block(
            world_result=world_home_stub,
            home_observation={"status": "unavailable"},
            soundscape=wkv.soundscape_unavailable("NONE"),
            day_root=self.day_root,
            now_iso="2026-11-13T17:05:00+08:00",
        )
        # 100% 零磁盘写入
        self.assertEqual(_snapshot_tree(self.day_root), snap_before)

        weather = view["weather"]
        self.assertEqual(weather["status"], "available")
        self.assertEqual(weather["source"], "v04_neighborhood_living")
        self.assertTrue(weather["is_weather_authority"])
        self.assertEqual(weather["condition"], "drizzle")
        self.assertEqual(weather["airflow"], "strong")

        w_plant = view["windowsill_plant"]
        self.assertTrue(w_plant["available"])
        self.assertEqual(w_plant["wet_dry_state"], "dry")
        self.assertTrue(w_plant["needs_watering"])
        self.assertEqual(w_plant["hydration"], 18.5)

        plants = view["plants"]
        self.assertEqual(len(plants), 1)
        self.assertEqual(plants[0]["moisture"]["wet_dry_state"], "dry")
        self.assertTrue(plants[0]["moisture"]["needs_watering"])

    def test_05_wardrobe_pocket_view_maps_standard_wearable_catalog_ids(self) -> None:
        subject_id = "subject:v02-test"
        wearable_pairs = (
            ("homewear", "inst_knit_set", "homewear_knit_set"),
            ("homewear", "inst_cardigan", "homewear_soft_cardigan"),
            ("outdoor", "inst_coat", "outdoor_coat_dark"),
            ("outdoor", "inst_jacket", "outdoor_light_jacket"),
            ("hairband", "inst_hairband", "hairband_linen_wrap"),
            ("hairclip", "inst_clip", "hairclip_wooden_pin"),
        )
        economy_state = {
            "items": [
                {
                    "item_instance_id": inst_id,
                    "catalog_item_id": cat_id,
                    "owner": subject_id,
                    "disposition": "OWNED",
                }
                for _slot, inst_id, cat_id in wearable_pairs
            ]
            + [
                {
                    "item_instance_id": "inst_snack",
                    "catalog_item_id": "virtual_snack_wafer",
                    "owner": subject_id,
                    "disposition": "OWNED",
                }
            ]
        }

        # 全部穿戴槽位单品均通过校验并返回精确 catalog_id
        for slot, inst_id, cat_id in wearable_pairs:
            chk = wpv.owned_item_check(
                economy_state, subject=subject_id, slot=slot, item_instance_id=inst_id
            )
            self.assertEqual(chk["status"], "verified")
            self.assertEqual(chk["catalog_id"], cat_id)

        # 零食绝不误标为可穿戴单品
        snack_chk = wpv.owned_item_check(
            economy_state, subject=subject_id, slot="homewear", item_instance_id="inst_snack"
        )
        self.assertEqual(snack_chk["status"], "rejected")
        self.assertEqual(snack_chk["reason_code"], "ITEM_CATEGORY_NOT_APPLICABLE")

        # wardrobe_block 输出完整槽位 catalog_id 与 wearable_catalog_ids 词表
        w_state, _ = wpv.apply_wardrobe_set(
            wpv.empty_wardrobe_state(),
            {
                "homewear_style": "居家软绒开衫",
                "outdoor_style": "轻便防风短外套",
                "hairband_style": "棉麻宽发带",
            },
            "2026-11-13T18:00:00+08:00",
        )
        related = wpv.related_owned_items(economy_state, subject=subject_id)
        block = wpv.wardrobe_block(
            state=w_state,
            state_reason=None,
            checks={},
            related=related,
            subject_view=True,
        )
        self.assertEqual(block["outfit"]["homewear"]["catalog_id"], "homewear_soft_cardigan")
        self.assertEqual(block["outfit"]["outdoor"]["catalog_id"], "outdoor_light_jacket")
        self.assertEqual(block["accessories"]["hairband"]["catalog_id"], "hairband_linen_wrap")
        self.assertIsNone(block["accessories"]["hairclip"]["catalog_id"])
        self.assertIn("homewear_soft_cardigan", block["wearable_catalog_ids"]["homewear"])
        self.assertIn("outdoor_light_jacket", block["wearable_catalog_ids"]["outdoor"])
        self.assertEqual(related["count"], 6)


if __name__ == "__main__":
    unittest.main()

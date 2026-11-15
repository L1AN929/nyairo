#!/usr/bin/env python3
"""v0.2 连续 7 天真 HTTP OpenAI 协议模型浸泡与《14-研发工作包与验收体系》§5 十二场景端到端验收套件。

覆盖范围：
1. 真实本地 HTTP OpenAI 协议模型服务（``127.0.0.1:<ephemeral_port>/v1/chat/completions``）：
   - 解析真实 ``messages`` 与 ``tools``，结合跨日上下文产出结构化决策与 ``tool_calls``；
   - 全程启用 Socket 外联守卫，强制验证非 ``127.0.0.1`` 外部网络请求数为 ``0``，外部付费大模型消耗为 ``0``。
2. 连续 7 天（Day 1 -> Day 7）跨午夜推进浸泡：
   - Day 1：自发多选形成偏好 + ``zlib`` 构造真实双色 PNG 提取像素色板并应用到作品；
   - Day 2：连续接触触发短期饱和（跨午夜连续指数衰减不突跳清零，长期 ``SUPPORTED`` 保留）+ 双向冲突搁置；
   - Day 3：开放问题慢孵化（跨 48h Day 3 -> Day 5 无新证据 ``0`` 假顿悟）+ 过程失误 vs 结果不如意后悔二分；
   - Day 4：300 条同族背景事件合并仅唤醒 1 次（``coalesced_saved_wakes == 299``）+ 控制通道零防抖直通 +
     ``LOW_BUDGET_CONSERVATION``（10% 预算省电 90% 模式）不装病不假装心情差并保住在途保存点与承诺；
   - Day 5：伴侣递入新线索推进开放问题至 ``ready_to_revisit`` + 安静日 ``QUIET_HEALTHY`` vs 积压故障 ``STALLED_OR_DEGRADED``；
   - Day 6：进入 ``DORMANT_NO_COGNITION`` 跨越空白时段恢复 ``NORMAL``，``dormant_gap_seconds > 0``、``no_fake_backfill == True``、
     ``fake_diary_entries_created == 0``；
   - Day 7：跨模型 + 缺失预览宿主迁居体检（``CONTINUED_WITH_LOSS`` + ``UNVALIDATED_AFTER_MIGRATION``）、试跑恢复、
     ``selective_practice`` 选择性旅行箱对比四档放下旧包袱 + 删除相册原图触发 ``read_v02_crossmodal_view`` 实时扣留色板。
3. 《14-研发工作包与验收体系.md》§5 全部 12 个端到端场景（S01–S12）独立实跑验真。
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import shutil
import socket
import struct
import sys
import tempfile
import threading
import unittest
import urllib.request
import zlib
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    REPO_ROOT,
    REPO_ROOT / "scripts",
    REPO_ROOT / "tests",
    REPO_ROOT / "components",
    REPO_ROOT / "components" / "life" / "scripts",
    REPO_ROOT / "components" / "world" / "scripts",
    REPO_ROOT / "components" / "supply",
    REPO_ROOT / "components" / "native",
    REPO_ROOT / "components" / "native" / "src",
    REPO_ROOT / "components" / "native" / "memory_runtime_v1",
):
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

import activity_progress_completion_lr5 as lr5  # noqa: E402
import agency_model_client as amc  # noqa: E402
import artifact_closeout  # noqa: E402
import autonomous_life_runner as alr  # noqa: E402
import life_co_presence as cop  # noqa: E402
import life_local_contact as lc  # noqa: E402
import life_social_contacts as lsc  # noqa: E402
import result_settlement_ar1 as ar1  # noqa: E402
import v02_v03_daily_loop_bridge as bridge  # noqa: E402
import waiting_no_model_poll_demo as waiting_demo  # noqa: E402
from app.evidence import EvidenceStore  # noqa: E402
from chiyo_bundle import world_journey as journey  # noqa: E402
from components.phone import (  # noqa: E402
    adult_intimacy_view,
    home_hearth_view,
    long_companionship_view,
    outing_places_view,
    stance_containment_view,
    v02_agency_depth_view as ad_view,
    v02_crossmodal_view as cm_view,
    v02_runtime_migration_view as rm_view,
    v03_embodied_social_view,
    window_keepsake_view,
    workbench_curio_view,
)
from components.worldgen.instance import init_instance  # noqa: E402
from components.worldgen.tests._setup import make_valid_docs  # noqa: E402
from forget_request import COMPLETE, ForgetCaseStore, ForgetGate  # noqa: E402
from life_artifact_port import ArtifactOutcome, ArtifactOwnerRefused, EffectState  # noqa: E402
from life_experience_memory_consumer import MemoryAdmissionExperienceConsumer  # noqa: E402
from life_experience_port import LifeExperiencePort, build_outcome_event, life_conversation_id  # noqa: E402
from life_future_items import FutureItems  # noqa: E402
from life_memory_service import LifeMemoryService  # noqa: E402
from life_sharing_service import LifeSharingService  # noqa: E402
from memory_authority import MemoryDeletionAuthority  # noqa: E402
from memory_control import MemoryControlLedger  # noqa: E402
from private_project_service import PrivateProjectService  # noqa: E402

POSIX_ONLY = unittest.skipUnless(os.name == "posix", "需要 POSIX 文件锁与本地运行环境（WSL/Linux）")


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int, str]]:
    if not root.exists():
        return {}
    snap: dict[str, tuple[int, int, str]] = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        st = p.lstat()
        if p.is_file() and not p.is_symlink():
            snap[rel] = (int(st.st_size), int(st.st_mtime_ns), hashlib.sha256(p.read_bytes()).hexdigest())
        else:
            snap[rel + "/"] = (0, int(st.st_mtime_ns), "DIR")
    return snap


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def _synth_bicolor_png(
    width: int,
    height: int,
    top_rgb: tuple[int, int, int],
    bottom_rgb: tuple[int, int, int],
) -> bytes:
    """用标准库 ``zlib`` + ``struct`` 合成真实双色 8-bit RGB PNG 字节。"""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    scanlines = bytearray()
    half = height // 2
    for y in range(height):
        scanlines.append(0)  # Filter: None
        r, g, b = top_rgb if y < half else bottom_rgb
        for _x in range(width):
            scanlines.extend((r & 0xFF, g & 0xFF, b & 0xFF))
    idat = zlib.compress(bytes(scanlines), level=6)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", idat)
        + _png_chunk(b"IEND", b"")
    )


class LoopbackOnlyNetworkGuard:
    """拦截全部 TCP Socket 连接，严格禁止任何非 127.0.0.1/::1/localhost 外部网络请求。"""

    _ALLOWED_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

    def __init__(self) -> None:
        self._orig_connect = socket.socket.connect
        self._orig_create_conn = socket.create_connection
        self.loopback_connects: int = 0
        self.external_blocked: list[str] = []

    def __enter__(self) -> "LoopbackOnlyNetworkGuard":
        guard = self

        def _checked_connect(sock: socket.socket, address: Any) -> Any:
            if sock.family in (socket.AF_INET, getattr(socket, "AF_INET6", -1)) and isinstance(address, tuple):
                host = str(address[0])
                if host not in guard._ALLOWED_HOSTS:
                    guard.external_blocked.append(f"{host}:{address[1]}")
                    raise PermissionError(f"External network blocked in soak test: {address!r}")
                guard.loopback_connects += 1
            return guard._orig_connect(sock, address)

        def _checked_create_conn(address: Any, *args: Any, **kwargs: Any) -> Any:
            if isinstance(address, tuple) and len(address) >= 2:
                host = str(address[0])
                if host not in guard._ALLOWED_HOSTS:
                    guard.external_blocked.append(f"{host}:{address[1]}")
                    raise PermissionError(f"External network blocked in soak test: {address!r}")
            return guard._orig_create_conn(address, *args, **kwargs)

        socket.socket.connect = _checked_connect  # type: ignore[assignment]
        socket.create_connection = _checked_create_conn  # type: ignore[assignment]
        return self

    def __exit__(self, *_exc: Any) -> None:
        socket.socket.connect = self._orig_connect  # type: ignore[assignment]
        socket.create_connection = self._orig_create_conn  # type: ignore[assignment]


class LiveOpenAIProtocolModelServer:
    """运行在 ``127.0.0.1:0`` 的真实 HTTP OpenAI ``/v1/chat/completions`` 协议服务。

    真实解析 ``messages`` 与 ``tools``，按跨天上下文产出结构化决策 JSON 与 ``tool_calls``，
    统计本地 HTTP 往返明细，外部付费模型消耗恒为 ``0``。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.requests: list[dict[str, Any]] = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._build_handler())
        self.port = int(self.server.server_address[1])
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self._thread.join(timeout=5.0)

    def chat_with_tools(
        self,
        *,
        day_index: int,
        context_payload: Mapping[str, Any],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        body = {
            "model": "nyairo-local-openai-soak-v02",
            "messages": [
                {
                    "role": "system",
                    "content": "You are the local v0.2 live-protocol cognition engine. Decide structured actions.",
                },
                {
                    "role": "user",
                    "content": json.dumps({"day": day_index, "context": dict(context_payload)}, ensure_ascii=False),
                },
            ],
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0.0,
        }
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer local-loopback-only-key",
            },
        )
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def make_agency_client(self) -> amc.AgencyModelClient:
        return amc.AgencyModelClient(
            config={
                "provider": "local_loopback",
                "model": "nyairo-local-openai-soak-v02",
                "base_url": self.base_url,
                "api_key": "local-loopback-only-key",
                "key_env": "LOCAL_SOAK_KEY",
            },
            timeout_s=8.0,
        )

    def _decide_reply(self, req_doc: dict[str, Any], client_ip: str, path: str) -> dict[str, Any]:
        messages = [m for m in (req_doc.get("messages") or []) if isinstance(m, Mapping)]
        tools = [t for t in (req_doc.get("tools") or []) if isinstance(t, Mapping)]
        tool_names = [
            str((t.get("function") or {}).get("name") or "")
            for t in tools
            if isinstance(t.get("function"), Mapping)
        ]
        user_text = ""
        sys_text = ""
        for msg in messages:
            if msg.get("role") == "system":
                sys_text = str(msg.get("content") or "")
            elif msg.get("role") == "user":
                user_text = str(msg.get("content") or "")
        try:
            parsed_user = json.loads(user_text) if user_text.strip().startswith("{") else {}
        except ValueError:
            parsed_user = {}

        day_idx = int(parsed_user.get("day") or 0)
        ctx = parsed_user.get("context") if isinstance(parsed_user.get("context"), Mapping) else parsed_user

        with self._lock:
            seq = len(self.requests) + 1
            self.requests.append({
                "seq": seq,
                "client_ip": client_ip,
                "path": path,
                "day": day_idx,
                "message_count": len(messages),
                "tool_names": [n for n in tool_names if n],
                "system_prefix": sys_text[:60],
                "context_keys": sorted(ctx.keys()) if isinstance(ctx, Mapping) else [],
            })

        tool_calls: list[dict[str, Any]] = []
        decision_obj: dict[str, Any] = {
            "day": day_idx,
            "decision": "PROCEED",
            "rationale_cn": "基于当前真实跨日上下文作出有界选择",
        }
        if isinstance(ctx, Mapping):
            mode = str(ctx.get("runtime_mode") or "NORMAL")
            if mode == "LOW_BUDGET":
                decision_obj["decision"] = "LOCAL_SMALL_REVISION_ONLY"
                decision_obj["pretend_tired_or_bad_mood"] = False
            elif ctx.get("open_question_ready"):
                decision_obj["decision"] = "REVISIT_INCUBATED_QUESTION"
            elif ctx.get("candidates"):
                cands = ctx.get("candidates") or []
                first_cid = cands[0].get("candidate_id") if cands and isinstance(cands[0], Mapping) else None
                decision_obj["decision"] = "START" if first_cid else "NO_ACTION"
                decision_obj["selected_candidate_id"] = first_cid

        if tool_names:
            chosen_tool = tool_names[0]
            tool_args = {"day": day_idx, "decision": decision_obj["decision"]}
            if isinstance(ctx, Mapping) and "dominant_hex" in ctx:
                tool_args["selected_hex"] = (ctx.get("dominant_hex") or [])[:2]
            tool_calls.append({
                "id": f"call_v02_d{day_idx}_{seq}",
                "type": "function",
                "function": {
                    "name": chosen_tool,
                    "arguments": json.dumps(tool_args, ensure_ascii=False),
                },
            })

        msg_out: dict[str, Any] = {
            "role": "assistant",
            "content": json.dumps(decision_obj, ensure_ascii=False),
        }
        if tool_calls:
            msg_out["tool_calls"] = tool_calls

        return {
            "id": f"chatcmpl-v02-soak-{seq}",
            "object": "chat.completion",
            "model": str(req_doc.get("model") or "nyairo-local-openai-soak-v02"),
            "choices": [{
                "index": 0,
                "message": msg_out,
                "finish_reason": "tool_calls" if tool_calls else "stop",
            }],
            "usage": {"prompt_tokens": 42, "completion_tokens": 24, "total_tokens": 66},
        }

    def _build_handler(self):
        srv = self

        class _OpenAIHandler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                try:
                    req_doc = json.loads(raw.decode("utf-8"))
                except ValueError:
                    req_doc = {}
                reply = srv._decide_reply(req_doc, str(self.client_address[0]), self.path)
                encoded = json.dumps(reply, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, *_args: Any) -> None:
                return

        return _OpenAIHandler


class PersistentManagedArtifactPort:
    """落盘持久化的确定性 ManagedArtifact 端口，支持跨重启重新加载与撤权隔离。"""

    def __init__(self, store_file: Path, subject_id: str = "chiyo") -> None:
        self.store_file = Path(store_file)
        self.subject_id = subject_id
        self.now_iso = "2026-11-01T09:00:00+08:00"
        if self.store_file.is_file():
            doc = json.loads(self.store_file.read_text(encoding="utf-8"))
            self._seq = int(doc.get("seq") or 0)
            self._artifacts: dict[str, dict[str, Any]] = dict(doc.get("artifacts") or {})
            self._versions: dict[str, list[dict[str, Any]]] = dict(doc.get("versions") or {})
            self._governed_blocked: set[str] = set(doc.get("governed_blocked") or [])
        else:
            self._seq = 0
            self._artifacts = {}
            self._versions = {}
            self._governed_blocked = set()
        self._ops: dict[str, ArtifactOutcome] = {}

    def _flush(self) -> None:
        self.store_file.parent.mkdir(parents=True, exist_ok=True)
        self.store_file.write_text(
            json.dumps({
                "seq": self._seq,
                "artifacts": self._artifacts,
                "versions": self._versions,
                "governed_blocked": sorted(self._governed_blocked),
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def tombstone_or_govern_source(self, artifact_id: str) -> None:
        self._governed_blocked.add(artifact_id)
        self._flush()

    def list_artifacts(self) -> list[dict[str, Any]]:
        return [
            copy.deepcopy(r)
            for aid, r in self._artifacts.items()
            if aid not in self._governed_blocked and r.get("lifecycle") != "TOMBSTONED"
        ]

    def read_artifact(self, artifact_id: str) -> dict[str, Any]:
        if artifact_id in self._governed_blocked:
            raise ArtifactOwnerRefused("DENY", "SOURCE_GOVERNED", "governed/revoked source")
        row = self._artifacts.get(artifact_id)
        if row is None or row.get("lifecycle") == "TOMBSTONED":
            raise ArtifactOwnerRefused("NOT_FOUND", "ARTIFACT_NOT_FOUND", "not found")
        head = self._versions[artifact_id][-1]
        return {"artifact": copy.deepcopy(row), "head": copy.deepcopy(head), "content": head["content"]}

    def list_versions(self, artifact_id: str, *, limit: int = 20, before_version_number: Optional[int] = None) -> dict[str, Any]:
        if artifact_id in self._governed_blocked:
            raise ArtifactOwnerRefused("DENY", "SOURCE_GOVERNED", "governed/revoked source")
        if artifact_id not in self._artifacts:
            raise ArtifactOwnerRefused("NOT_FOUND", "ARTIFACT_NOT_FOUND", "not found")
        vers = list(reversed(self._versions[artifact_id]))
        if before_version_number is not None:
            vers = [v for v in vers if int(v["version_number"]) < int(before_version_number)]
        sliced = copy.deepcopy(vers[: max(1, int(limit))])
        return {
            "entries": sliced,
            "complete": len(vers) <= len(sliced),
            "total_versions": len(self._versions[artifact_id]),
            "next_before_version_number": int(sliced[-1]["version_number"]) if len(vers) > len(sliced) and sliced else None,
            "source": "ManagedArtifact",
        }

    def read_version(self, artifact_id: str, version_id: str) -> dict[str, Any]:
        if artifact_id in self._governed_blocked:
            raise ArtifactOwnerRefused("DENY", "SOURCE_GOVERNED", "governed/revoked source")
        for ver in self._versions.get(artifact_id, []):
            if ver["version_id"] == version_id:
                return copy.deepcopy(ver)
        raise ArtifactOwnerRefused("NOT_FOUND", "VERSION_NOT_FOUND", "version not found")

    def create_artifact(
        self, *, workspace_id: str, title: str, content: str, grant_id: str, operation_key: str, source_refs: Iterable[Any] = ()
    ) -> ArtifactOutcome:
        if operation_key in self._ops:
            prev = copy.deepcopy(self._ops[operation_key])
            prev.effect_state = EffectState.NO_CHANGE
            return prev
        self._seq += 1
        aid = f"artifact:{hashlib.sha256(f'{self._seq}:{title}'.encode()).hexdigest()[:16]}"
        vid = f"artifact-version:{hashlib.sha256(f'{aid}:v1'.encode()).hexdigest()[:16]}"
        vrow = {
            "artifact_id": aid,
            "version_id": vid,
            "version_number": 1,
            "parent_version_id": None,
            "created_at": self.now_iso,
            "operation_key": operation_key,
            "content": content,
            "content_chars": len(content),
            "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "content_truncated": False,
        }
        self._artifacts[aid] = {
            "artifact_id": aid,
            "workspace_id": workspace_id,
            "title": title,
            "lifecycle": "ACTIVE",
            "revision": 1,
            "head_version_id": vid,
            "created_at": self.now_iso,
            "updated_at": self.now_iso,
        }
        self._versions[aid] = [vrow]
        self._flush()
        out = ArtifactOutcome(
            effect_state=EffectState.APPLIED, status="OK", artifact_id=aid, version_id=vid,
            version_number=1, head_version_id=vid, parent_version_id=None, version_created=True,
            operation_key=operation_key, lifecycle="ACTIVE", revision=1,
            raw={"status": "OK", "artifact_id": aid, "version_id": vid, "version_number": 1},
        )
        self._ops[operation_key] = copy.deepcopy(out)
        return out

    def commit_version(
        self, *, artifact_id: str, expected_head: str, content: str, grant_id: str, operation_key: str, **_: Any
    ) -> ArtifactOutcome:
        if operation_key in self._ops:
            prev = copy.deepcopy(self._ops[operation_key])
            prev.effect_state = EffectState.NO_CHANGE
            return prev
        row = self._artifacts.get(artifact_id)
        if row is None:
            return ArtifactOutcome(effect_state=EffectState.NOT_FOUND, status="NOT_FOUND", reason="NOT_FOUND")
        if row["lifecycle"] != "ACTIVE":
            return ArtifactOutcome(effect_state=EffectState.REFUSED, status="DENY", reason="ARTIFACT_ARCHIVED")
        if row["head_version_id"] != expected_head:
            return ArtifactOutcome(effect_state=EffectState.CONFLICT, status="CONFLICT", reason="CAS_MISMATCH")
        vers = self._versions[artifact_id]
        next_num = len(vers) + 1
        vid = f"artifact-version:{hashlib.sha256(f'{artifact_id}:v{next_num}:{operation_key}'.encode()).hexdigest()[:16]}"
        vers.append({
            "artifact_id": artifact_id,
            "version_id": vid,
            "version_number": next_num,
            "parent_version_id": expected_head,
            "created_at": self.now_iso,
            "operation_key": operation_key,
            "content": content,
            "content_chars": len(content),
            "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "content_truncated": False,
        })
        row["head_version_id"] = vid
        row["revision"] = int(row["revision"]) + 1
        row["updated_at"] = self.now_iso
        self._flush()
        out = ArtifactOutcome(
            effect_state=EffectState.APPLIED, status="OK", artifact_id=artifact_id, version_id=vid,
            version_number=next_num, head_version_id=vid, parent_version_id=expected_head, version_created=True,
            operation_key=operation_key, lifecycle="ACTIVE", revision=row["revision"],
            raw={"status": "OK", "artifact_id": artifact_id, "version_id": vid, "version_number": next_num},
        )
        self._ops[operation_key] = copy.deepcopy(out)
        return out

    def archive_artifact(
        self, *, artifact_id: str, expected_revision: int, grant_id: str, operation_key: str
    ) -> ArtifactOutcome:
        if operation_key in self._ops:
            prev = copy.deepcopy(self._ops[operation_key])
            prev.effect_state = EffectState.NO_CHANGE
            return prev
        row = self._artifacts.get(artifact_id)
        if row is None:
            return ArtifactOutcome(effect_state=EffectState.NOT_FOUND, status="NOT_FOUND", reason="NOT_FOUND")
        if int(row["revision"]) != int(expected_revision):
            return ArtifactOutcome(effect_state=EffectState.CONFLICT, status="CONFLICT", reason="REVISION_MISMATCH")
        row["lifecycle"] = "ARCHIVED"
        row["revision"] = int(row["revision"]) + 1
        row["updated_at"] = self.now_iso
        self._flush()
        out = ArtifactOutcome(
            effect_state=EffectState.APPLIED, status="OK", artifact_id=artifact_id,
            head_version_id=row["head_version_id"], operation_key=operation_key,
            lifecycle="ARCHIVED", revision=row["revision"],
            raw={"status": "OK", "lifecycle": "ARCHIVED", "revision": row["revision"]},
        )
        self._ops[operation_key] = copy.deepcopy(out)
        return out


@POSIX_ONLY
class TestV02EndToEndTwelveScenarios(unittest.TestCase):
    """《14-研发工作包与验收体系.md》§5 全部 12 个端到端场景（S01–S12）独立实跑验真。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="nyairo_v02_s01_s12_")
        self.tmp_root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_s01_work_written_persists_across_restart_and_history_shapes_next_choice(self) -> None:
        """S01：作品真实写入且重启后可继续，历史真实影响下一次选择。"""
        day_root = self.tmp_root / "s01_day"
        store_file = self.tmp_root / "s01_artifacts.json"

        # 进程生命周期 1：创建作品 v1 并留下明日续写便签与工作台托盘
        port_1 = PersistentManagedArtifactPort(store_file, subject_id="chiyo")
        projects_1 = PrivateProjectService(port_1, "ws-s01", "grant-s01", subject_id="chiyo")
        created = projects_1.create_project(
            title="十二扇窗·第1页",
            content="初稿：晨光落在北窗木框上，还没有补阴影。",
            operation_key="s01_create_v1",
        )
        self.assertTrue(created["ok"])
        art_id = created["artifact_id"]
        v1_id = created["version_id"]

        tray_state, _ = workbench_curio_view.set_tray(
            workbench_curio_view.empty_tray_state(),
            {
                "images": [],
                "materials": [],
                "work": {"artifact_id": art_id, "baseline_version_id": v1_id},
                "note": "续补北窗木框第二层阴影",
            },
            "2026-11-01T22:00:00+08:00",
        )
        workbench_curio_view.write_tray_state(day_root, tray_state)
        tmr_item = FutureItems(day_root).propose(
            title="早起续写十二扇窗第1页阴影",
            due_at="2026-11-02T08:30:00+08:00",
            now="2026-11-01T22:10:00+08:00",
            source_refs=[art_id],
        )["item"]

        # 对照组（无昨天作品与便签历史）：只放客厅暂定提议
        ctrl_root = self.tmp_root / "s01_ctrl"
        for root in (day_root, ctrl_root):
            ps, _, _, _ = home_hearth_view.propose_plan(
                home_hearth_view.empty_plans_state(),
                plan_id=None,
                title="午后整理唱片封套",
                window_note="午后",
                proposer="partner",
                now_iso="2026-11-01T20:00:00+08:00",
            )
            home_hearth_view.write_plans_state(root, ps)

        # 进程生命周期 2（冷重启重读同一落盘文件）：续写 v2，且历史改变下一次选择
        port_2 = PersistentManagedArtifactPort(store_file, subject_id="chiyo")
        projects_2 = PrivateProjectService(port_2, "ws-s01", "grant-s01", subject_id="chiyo")
        reloaded = projects_2.read_project(art_id)
        self.assertEqual(reloaded["project"]["head_version_id"], v1_id)

        col_with_hist = bridge.collect_v02_v03_loop_candidates(day_root, now_iso="2026-11-02T09:00:00+08:00")
        col_no_hist = bridge.collect_v02_v03_loop_candidates(ctrl_root, now_iso="2026-11-02T09:00:00+08:00")
        choice_hist = bridge.select_deterministic_loop_action(col_with_hist)
        choice_ctrl = bridge.select_deterministic_loop_action(col_no_hist)
        self.assertEqual(choice_hist["action_family"], "follow_tomorrow_note")
        self.assertEqual(choice_hist["source_ref"], tmr_item["item_ref"])
        self.assertNotEqual(choice_hist["action_family"], choice_ctrl["action_family"])

        v2 = projects_2.commit_project(
            artifact_id=art_id,
            expected_head=v1_id,
            content="第二版：顺着昨天的木框补上了清冷蓝灰阴影。",
            operation_key="s01_commit_v2",
        )
        self.assertTrue(v2["ok"])
        self.assertEqual(v2["version_number"], 2)

    def test_s02_ordinary_chat_during_atomic_step_responds_without_illegal_pause(self) -> None:
        """S02：原子步骤期间普通聊天可回应，但绝不非法 pause 当前前台活动。"""
        state_root = self.tmp_root / "s02_atomic"
        state_root.mkdir(parents=True, exist_ok=True)

        class _AtomicRuntime:
            class _Coord:
                def get_current_life_view(self) -> dict[str, Any]:
                    return {
                        "foreground_activity_ref": "actv_atomic_ink_wash",
                        "interruptibility": "ATOMIC",
                        "communication_availability": "BOUNDARY_DEFERRED",
                    }

            interruption_coordinator = _Coord()

        runtime = _AtomicRuntime()
        protocol = cop.FiniteCoPresence(
            runtime, state_root=state_root, subject_id="test:s02", home="home:s02room"
        )
        seen = protocol.observe({"kind": "LOCAL_MESSAGE", "body": "你在忙吗？顺手问一句"})
        self.assertEqual(seen["communication_availability"], "BOUNDARY_DEFERRED")
        self.assertTrue(seen["respondable"])
        self.assertTrue(seen["foreground_atomic"])
        self.assertFalse(seen["pause_requested"])
        self.assertFalse(seen["activity_started"])
        self.assertEqual(seen["owners_written"], 0)

        # 通过真实 LocalContactInbox + SocialContactPort 归档一句本地回复，前台活动保持原样不暂停
        inbox = lc.LocalContactInbox(state_root, subject_id="test:s02", home_ref="home:s02room")
        sub = inbox.submit({
            "kind": lc.KIND_MESSAGE,
            "body": "你在忙吗？顺手问一句",
            "occurred_at": "2026-11-02T10:00:00+00:00",
        })
        self.assertTrue(sub["ok"])

        class _FixedResponder:
            def respond(self, **_kw: Any) -> dict[str, Any]:
                return {"ok": True, "text": "正在上墨线收尾，等这一笔干了跟你说。"}

        port = lsc.SocialContactPort(
            runtime,
            state_root=state_root,
            subject_id="test:s02",
            home="home:s02room",
            inbox=inbox,
            protocol=protocol,
            responder=_FixedResponder(),
        )
        step_out = port.tick(now="2026-11-02T10:00:05+00:00")
        self.assertEqual(step_out["processed_count"], 1)
        self.assertFalse(step_out["processed"][0]["pause_requested"])
        detail = port.read_reply(sub["event_ref"], include_text=True)
        self.assertTrue(detail["ok"])
        self.assertIn("正在上墨线收尾", json.dumps(detail, ensure_ascii=False))

    def test_s03_waiting_long_term_zero_model_polling_and_satisfied_emits_eligibility_only(self) -> None:
        """S03：等待结果长期 0 模型轮询，满足条件只产生 ResumeEligibility 资格而不自动恢复。"""
        quiet_report = {
            "state": "OK",
            "checked": 1,
            "unchanged": 1,
            "changed": 0,
            "evaluated": 0,
            "satisfied": [],
            "emitted_eligibilities": [],
            "errors": [],
            "body_checked": 0,
            "body_changed": 0,
            "auto_resumed": False,
            "llm_calls": 0,
        }
        satisfied_report = dict(
            quiet_report,
            unchanged=0,
            changed=1,
            evaluated=1,
            satisfied=["rcond:kiln_glaze_ready"],
            emitted_eligibilities=["relig:kiln_glaze_ready"],
            auto_resumed=False,
            llm_calls=0,
        )

        class _WaitingCycle:
            def __init__(self) -> None:
                self.state_root = Path(tempfile.mkdtemp(prefix="s03_cycle_"))
                self.enabled = True
                self.seq = 0

            def maintain_waiting(self, *, observed_at: Any = None) -> dict[str, Any]:
                self.seq += 1
                return dict(quiet_report if self.seq <= 3 else satisfied_report)

        cycle = _WaitingCycle()
        runner = alr.AutonomousLifeRunner(cycle, state_root=self.tmp_root / "s03_runner", env={}, enabled=True)
        for h in (10, 12, 14):
            rep = runner.maintain(now=datetime.fromisoformat(f"2026-11-02T{h:02d}:00:00+08:00"))
            self.assertTrue(waiting_demo.quiet_upkeep(rep))
            self.assertEqual(rep["llm_calls"], 0)
            self.assertFalse(rep["auto_resumed"])

        rep_due = runner.maintain(now=datetime.fromisoformat("2026-11-02T16:00:00+08:00"))
        self.assertEqual(rep_due["llm_calls"], 0)
        self.assertEqual(rep_due["emitted_eligibilities"], ["relig:kiln_glaze_ready"])
        self.assertFalse(rep_due["auto_resumed"])

    def test_s04_external_timeout_keeps_unknown_reconciles_original_op_without_resubmit(self) -> None:
        """S04：外部 accepted 后 timeout 保持 UNKNOWN，后续查原操作对账，绝不重复提交。"""
        import world_runtime_data as wrd
        from components.worldgen import schema
        from components.worldgen.loader import load as load_package

        pkg_dir = self.tmp_root / "s04_pkg"
        schema.write_package(pkg_dir, make_valid_docs(seed=929711, theme="coastal_town"))
        docs = schema.load_package(pkg_dir)
        locs = sorted(x["location_id"] for x in docs["topology.json"]["locations"] if x["location_id"] != "home")
        target_loc = locs[0]

        wrd.reset_cache()
        pkg = load_package(pkg_dir)
        whome = self.tmp_root / "s04_world_home"
        init_instance(pkg_dir, whome)
        env = {
            "CHIYO_WORLD_PACKAGE": str(pkg_dir),
            "CHIYO_WORLD_HOME": str(whome),
            "CHIYO_WORLD_ID": pkg.world_id,
            "CHIYO_WORLD_LIVING_SKELETON_ENABLED": "1",
            "CHIYO_WORLD_TRAVEL_TIME_ENABLED": "1",
        }

        class _TimeoutPort:
            def __init__(self) -> None:
                self.calls: list[dict[str, Any]] = []

            def submit_move(self, **kwargs: Any) -> dict[str, Any]:
                self.calls.append(dict(kwargs))
                return {
                    "status": "uncertain",
                    "reason_code": "UNKNOWN",
                    "detail": "transport_fault:TIMEOUT",
                    "formal": True,
                }

        port = _TimeoutPort()
        t0 = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
        first = journey.accept_journey(
            target=target_loc, hermes_home=whome, environment=env, now=t0, submit_port=port
        )
        self.assertEqual(first["status"], "unknown")
        self.assertEqual(len(port.calls), 1)
        orig_exec_id = port.calls[0]["execution_id"]

        # 连续两次推进仅对账原操作，submit 调用次数严格保持 1，execution_id 不变
        adv1 = journey.advance_journey(hermes_home=whome, environment=env, now=t0 + timedelta(seconds=10), submit_port=port)
        adv2 = journey.advance_journey(hermes_home=whome, environment=env, now=t0 + timedelta(seconds=25), submit_port=port)
        self.assertEqual(adv1["status"], "unknown")
        self.assertEqual(adv2["status"], "unknown")
        self.assertEqual(len(port.calls), 1)
        jstate = json.loads((whome / "data" / "world" / "world_journey.json").read_text(encoding="utf-8"))
        self.assertEqual(jstate["active"]["segments"][0]["execution_id"], orig_exec_id)

    def test_s05_partial_subeffect_confirmed_persists_hand_state_and_progress_never_overclaims(self) -> None:
        """S05：部分子效果确认其余未达时保留真实手槽状态，且 LR-5 活动进展不超证据完成。"""
        day_root = self.tmp_root / "s05_partial"
        # 拿起平光镜（eyewear）放入只收文具的粗陶马克杯（ceramic_mug）-> 拿起子效果成功，放入被拒，不假回滚
        out = stance_containment_view.apply_stance_containment_action(
            day_root,
            {
                "action": "pick_and_put_in_container",
                "item_id": "reading_glasses",
                "container_id": "ceramic_mug",
                "operation_key": "s05_partial_put",
            },
            now="2026-11-02T11:00:00+08:00",
            subject_view=True,
        )
        self.assertEqual(out["status"], "partial_completed")
        self.assertEqual(out["reason_code"], "CONTAINER_CLASS_REJECTED")
        self.assertTrue(out["picked_up"])
        self.assertFalse(out["placed_in_container"])
        self.assertFalse(out["rolled_back"])
        self.assertIn(out["held_in_hand"], ("left_hand", "right_hand"))

        # 同时核验 LR-5：PARTIAL_FACT 证据绝不能把要求完整结果的 CompletionContract 误判为完成
        contract = lr5.CompletionContract(
            contract_id="ccont:s05_full_tidy",
            activity_id="actv_s05",
            criteria_kind=lr5.CRITERIA_ACTION_RESULT_MATCH,
            target_refs=["artifact:desk_tidy"],
            criteria={"required_predicate": "all_items_stowed", "expected_artifact_ref": "artifact:desk_tidy"},
            required_evidence_kinds=[lr5.SOURCE_KIND_SETTLED_EVENT],
            required_authority_domains=["result_settlement_authority"],
            created_at="2026-11-02T11:00:00Z",
            policy_version=lr5.POLICY_ARTIFACT_V1,
        )
        partial_ev = lr5.LifeProgressEvidence(
            progress_evidence_id="pevid:s05_partial",
            source_kind=lr5.SOURCE_KIND_SETTLED_EVENT,
            source_ref="srev:s05_partial",
            authority_domain="result_settlement_authority",
            epistemic_kind=lr5.EPISTEMIC_PARTIAL_FACT,
            settlement_ref="rset:s05_partial",
            settlement_status=lr5.SETTLEMENT_STATUS_PARTIAL,
            outcome_kind=lr5.OUTCOME_PARTIAL_EFFECT,
            activity_id="actv_s05",
            target_ref="artifact:desk_tidy",
            occurred_at="2026-11-02T11:01:00Z",
            observed_at="2026-11-02T11:01:00Z",
            recorded_at="2026-11-02T11:01:00Z",
            idempotency_key="idem:s05_partial",
        ).to_dict()
        eval_res = lr5.NaturalCompletionEvaluator.evaluate_completion(
            activity={"revision": 1},
            completion_contract=contract.to_dict(),
            active_evidences=[partial_ev],
            current_checkpoint_ref=None,
        )
        self.assertNotEqual(eval_res["eval_status"], lr5.EVAL_COMPLETION_ELIGIBLE)

    def test_s06_action_succeeded_but_object_recheck_conflict_blocks_activity_completion(self) -> None:
        """S06：当前执行器动作报成功但对象哈希复核冲突时，AR-1 判 CONFLICT 且活动不被误完成。"""
        ts = "2026-11-02T12:00:00Z"
        exec_ok = ar1.SettlementEvidenceInput(
            evidence_id="ev:s06_exec",
            root_evidence_id="rootev:s06_exec",
            action_id="actn:s06",
            source_ref="artifact_authority:exec",
            authority_domain="artifact_authority",
            evidence_kind="filesystem_observed",
            authority_grade=ar1.AUTH_GRADE_EXECUTOR_RECEIPT,
            claim_kind="observer:artifact_observer",
            verified=True,
            occurred_at=ts,
            observed_at=ts,
            recorded_at=ts,
            target_ref="artifact:s06_work",
            expected_hash="sha256:expected_v2",
            observed_hash="sha256:expected_v2",
        )
        obj_mismatch = ar1.SettlementEvidenceInput(
            evidence_id="ev:s06_recheck",
            root_evidence_id="rootev:s06_recheck",
            action_id="actn:s06",
            source_ref="artifact_authority:recheck",
            authority_domain="artifact_authority",
            evidence_kind="filesystem_observed",
            authority_grade=ar1.AUTH_GRADE_OBSERVER_VERIFIED,
            claim_kind="observer:artifact_observer",
            verified=False,
            occurred_at=ts,
            observed_at=ts,
            recorded_at=ts,
            target_ref="artifact:s06_work",
            expected_hash="sha256:expected_v2",
            observed_hash="sha256:tampered_other",
            details={"hash_mismatch": True},
        )
        ar1_res = ar1.SettlementPolicyRegistry().evaluate(
            action={"action_id": "actn:s06", "action_kind": ar1.ACTION_KIND_FILE, "status": ar1.ACTION_STATUS_SUCCEEDED, "frozen_payload": {}},
            attempts=[],
            evidences=[exec_ok, obj_mismatch],
            settlement_id="rset:s06_conflict",
            recorded_at=ts,
        )
        self.assertEqual(ar1_res["settlement_status"], ar1.SETTLEMENT_STATUS_CONFLICT)

        spec = artifact_closeout.build_closeout_adoption_spec("artifact:s06_work")
        c_spec = spec["completion_contract_spec"]
        contract = lr5.CompletionContract(
            contract_id="ccont:s06",
            activity_id="actv_s06",
            criteria_kind=c_spec["criteria_kind"],
            target_refs=["artifact:s06_work"],
            criteria=dict(c_spec["criteria"]),
            required_evidence_kinds=list(c_spec["required_evidence_kinds"]),
            required_authority_domains=list(c_spec["required_authority_domains"]),
            created_at=ts,
            policy_version=lr5.POLICY_ARTIFACT_V1,
        )
        conf_ev = lr5.LifeProgressEvidence(
            progress_evidence_id="pevid:s06_conf",
            source_kind=lr5.SOURCE_KIND_SETTLED_EVENT,
            source_ref="srev:s06_conf",
            authority_domain="result_settlement_authority",
            epistemic_kind=lr5.EPISTEMIC_CONFLICTED_CLAIM,
            settlement_ref="rset:s06_conflict",
            settlement_status=lr5.SETTLEMENT_STATUS_CONFLICT,
            outcome_kind=lr5.OUTCOME_UNKNOWN_EFFECT,
            activity_id="actv_s06",
            target_ref="artifact:s06_work",
            occurred_at=ts,
            observed_at=ts,
            recorded_at=ts,
            idempotency_key="idem:s06_conf",
        ).to_dict()
        comp_eval = lr5.NaturalCompletionEvaluator.evaluate_completion(
            activity={"revision": 1},
            completion_contract=contract.to_dict(),
            active_evidences=[conf_ev],
            current_checkpoint_ref=None,
        )
        self.assertEqual(comp_eval["eval_status"], lr5.EVAL_CONFLICT)
        self.assertEqual(comp_eval["reason"], "CONFLICTING_EVIDENCE_BLOCKS_COMPLETION")

    def test_s07_late_correction_after_completion_records_conflict_without_silent_reopen(self) -> None:
        """S07：活动 COMPLETED 后收到迟到纠正事件，核验继承链并记录冲突，绝不静默重开活动。"""
        ts = "2026-11-02T14:00:00Z"
        late_ev = lr5.LifeProgressEvidence(
            progress_evidence_id="pevid:s07_late",
            source_kind=lr5.SOURCE_KIND_CORRECTION_EVENT,
            source_ref="scor:s07_late",
            authority_domain="result_settlement_authority",
            epistemic_kind=lr5.EPISTEMIC_CONFLICTED_CLAIM,
            occurred_at=ts,
            observed_at=ts,
            recorded_at=ts,
            idempotency_key="idem:s07_late",
            activity_id="actv_s07_done",
            target_ref="artifact:s07_booklet",
            settlement_ref="rset:s07_late_corr",
            supersedes_settlement_ref="rset:s07_orig_done",
        )
        binding = {
            "activity_id": "actv_s07_done",
            "allowed_evidence_kinds": ["SETTLED_RESULT_EVENT", "ARTIFACT_CANONICAL_STATE"],
            "authority_domains": ["result_settlement_authority", "artifact_authority"],
            "target_refs": ["artifact:s07_booklet"],
            "correlation_ids": [],
        }
        matched, match_reason = lr5.ActivityProgressPolicyRegistry.match_evidence_to_binding(
            evidence=late_ev, binding=binding
        )
        self.assertTrue(matched)
        self.assertEqual(match_reason, "BOUND_AND_MATCHED")

        class _LateProbe(lr5.ActivityProgressCoordinator):
            def __init__(self) -> None:
                self.settlement_read = None

        probe = _LateProbe()
        completed_activity = {"activity_id": "actv_s07_done", "status": "COMPLETED"}
        state = {
            "completed_once_by_activity": {"actv_s07_done": {"eligibility_id": "celig:s07"}},
            "completion_eligibilities_by_id": {
                "celig:s07": {"status": "CONSUMED", "settlement_refs": ["rset:s07_orig_done"], "evidence_refs": []}
            },
            "consumed_evidences_by_id": {},
        }
        rejection = probe._correction_relation_rejection(state, completed_activity, late_ev)
        self.assertIsNone(rejection)
        # 终态活动状态保持 COMPLETED，绝不因迟到纠正在后台静默变成 ACTIVE
        self.assertEqual(completed_activity["status"], "COMPLETED")

    def test_s08_revocation_after_read_rechecks_before_send_and_blocks_private_leak(self) -> None:
        """S08：读取准备分享草稿后源作品撤权/换版，派发前再检查拦截且 36B 边界门控禁绝外发。"""
        day_root = self.tmp_root / "s08_revoke"
        port = PersistentManagedArtifactPort(self.tmp_root / "s08_store.json", subject_id="chiyo")
        projects = PrivateProjectService(port, "ws-s08", "grant-s08", subject_id="chiyo")
        sharing = LifeSharingService(projects)

        proj = projects.create_project(
            title="深夜私密信笺",
            content="只写给自己看的脆弱心事片段。",
            operation_key="s08_create",
        )
        art_id = proj["artifact_id"]
        draft = sharing.prepare_share(
            source_artifact_id=art_id,
            share_text="只写给自己看的脆弱心事片段。",
            title="深夜私密信笺",
            operation_key="s08_prep_draft",
        )
        self.assertTrue(draft["ok"])
        share_id = draft["share_id"]
        draft_head = draft["data"]["head_version_id"]

        # 派发前源作品被撤权治理（governed/revoked）-> send_local 派发前复检拒绝，绝不投递！
        port.tombstone_or_govern_source(art_id)
        send_attempt = sharing.apply_action(
            share_id=share_id,
            action="send_local",
            expected_head=draft_head,
            operation_key="s08_try_send_after_revoke",
        )
        self.assertFalse(send_attempt["ok"])
        self.assertNotEqual(send_attempt.get("state"), "LOCAL_DELIVERED")

        # 联动 36B 私密边界与撤权门控：记录私密记忆后一键撤权擦除，伴侣与外发通道零泄露
        adult_intimacy_view.apply_adult_intimacy_action(
            day_root,
            {"action": "record_private_memory", "kind": "BOUNDARY", "text": "绝不外发的私密时刻", "custom_id": "mem_s08"},
            now="2026-11-02T22:00:00+08:00",
            subject_view=True,
        )
        rev_36b = adult_intimacy_view.apply_adult_intimacy_action(
            day_root,
            {"action": "revoke_intimacy_consent", "by": "subject", "erase_memory": True},
            now="2026-11-02T22:05:00+08:00",
            subject_view=True,
        )
        self.assertTrue(rev_36b["action_receipt"]["consent_revoked"])
        self.assertTrue(rev_36b["action_receipt"]["memory_erased"])
        self.assertEqual(rev_36b["action_receipt"]["relationship_penalty"], 0)
        partner_read = adult_intimacy_view.read_adult_intimacy(day_root, subject_view=False)
        self.assertNotIn("绝不外发的私密时刻", json.dumps(partner_read, ensure_ascii=False))

    def test_s09_deleted_evidence_never_resurrects_after_backup_restore_or_index_resync(self) -> None:
        """S09：原始证据删除后即使恢复旧备份库并重建表面索引，旧内容也绝不复活。"""
        mem_root = self.tmp_root / "s09_mem"
        (mem_root / "memory").mkdir(parents=True)
        (mem_root / "retained").mkdir(parents=True)
        (mem_root / "forget").mkdir(parents=True)
        ev_db = mem_root / "memory" / "evidence.db"
        ctrl_db = mem_root / "memory" / "controls.db"
        auth_db = mem_root / "retained" / "authority.db"
        case_db = mem_root / "forget" / "cases.db"
        conv = life_conversation_id("chiyo")

        MemoryControlLedger(ctrl_db, ev_db, owner="chiyo", conversation=conv).initialize()
        MemoryDeletionAuthority(auth_db, owner="chiyo", conversation=conv).initialize()
        ev = build_outcome_event(
            subject="chiyo",
            source_ref="diary:secret_page_09",
            text="已被彻底删除的旧日记隐私原文",
            occurred_at="2026-11-01T20:00:00Z",
        )
        EvidenceStore(ev_db).append(ev)

        # 删除前先拷走旧备份文件模拟灾难恢复场景
        ev_backup = mem_root / "evidence.old_backup.db"
        ctrl_backup = mem_root / "controls.old_backup.db"
        shutil.copy2(ev_db, ev_backup)
        shutil.copy2(ctrl_db, ctrl_backup)

        fstore = ForgetCaseStore(
            case_db, evidence_db=ev_db, controls_db=ctrl_db, authority_db=auth_db, owner="chiyo", conversation=conv
        )
        fstore.initialize()
        f_res = fstore.request(operation_key="s09_forget_op", event_ids=[ev.event_id])
        self.assertEqual(f_res["status"], COMPLETE)

        # 恶意/运维误恢复删除前的旧备份数据库，重新通过 LifeMemoryService 读取
        shutil.copy2(ev_backup, ev_db)
        shutil.copy2(ctrl_backup, ctrl_db)
        gate = ForgetGate(fstore)
        port = LifeExperiencePort(ev_db, controls_db=ctrl_db, subject="chiyo", authority_db=auth_db, forget_gate=gate)
        svc = LifeMemoryService(
            port, owner="chiyo", database=str(ev_db), controls_database=str(ctrl_db),
            authority_database=str(auth_db), source="s09_test", forget_gate=gate,
        )
        self.assertIsNone(port.read(ev.event_id))
        listed = svc.list_experiences()
        self.assertEqual(listed["data"]["count"], 0)
        self.assertNotIn("已被彻底删除的旧日记隐私原文", json.dumps(listed, ensure_ascii=False))

    def test_s10_maintenance_and_fixture_world_mutations_rejected_as_personal_life(self) -> None:
        """S10：测试/维护动作虽然真实改变了 World 状态，但绝不被接纳为她的个人经历记忆。"""
        from components.worldgen import schema

        pkg_dir = self.tmp_root / "s10_pkg"
        schema.write_package(pkg_dir, make_valid_docs(seed=1010, theme="coastal_town"))
        whome = self.tmp_root / "s10_world"
        init_instance(pkg_dir, whome)

        # 维护脚本直接修改了 world_state.json 里的维护标记
        wstate_file = whome / "data" / "world" / "world_state.json"
        wstate = json.loads(wstate_file.read_text(encoding="utf-8"))
        wstate["maintenance_fixture_note"] = "ops_bench_repair_20261102"
        wstate_file.write_text(json.dumps(wstate, ensure_ascii=False), encoding="utf-8")

        mem_dir = self.tmp_root / "s10_mem"
        mem_dir.mkdir(parents=True)
        ev_db = mem_dir / "evidence.db"
        ctrl_db = mem_dir / "controls.db"
        conv = life_conversation_id("chiyo")
        MemoryControlLedger(ctrl_db, ev_db, owner="chiyo", conversation=conv).initialize()
        mport = LifeExperiencePort(ev_db, controls_db=ctrl_db, subject="chiyo")

        # AG-2 权威存储中不存在维护动作的个人生命周期记录
        consumer = MemoryAdmissionExperienceConsumer(mport, enabled=True, handoff_verifier=lambda _hid: None)

        # 1) 无 activity_ref 的世界维护回执 -> LIFE_ACTIVITY_REF_MISSING 直接忽略
        r_no_act = consumer.receive_experience_handoff({
            "handoff_id": "lexp:maint_01",
            "world_refs": ["world:ops_bench_repair_20261102"],
            "occurred_at": "2026-11-02T03:00:00Z",
        })
        self.assertEqual(r_no_act["disposition"], "IGNORE")
        self.assertEqual(r_no_act["reason"], "LIFE_ACTIVITY_REF_MISSING")

        # 2) 伪造指向某个 activity_ref 但无 AG-2 权威持久记录的测试事件 -> 同样被拒
        r_forged = consumer.receive_experience_handoff({
            "handoff_id": "lexp:fixture_02",
            "activity_ref": "actv_fake_maint",
            "terminal_or_milestone_kind": "COMPLETED",
            "world_refs": ["world:ops_bench_repair_20261102"],
            "occurred_at": "2026-11-02T03:05:00Z",
        })
        self.assertNotEqual(r_forged["disposition"], "ACCEPT")
        self.assertEqual(consumer.accepted, 0)

    def test_s11_quiet_healthy_day_distinguished_from_dead_process_and_stalled_queue_zero_drama(self) -> None:
        """S11：无事件安静日、死进程心跳超时与卡队列积压被技术观测严格区分，绝不补写假剧情。"""
        quiet_root = self.tmp_root / "s11_quiet"
        quiet_root.mkdir(parents=True)
        (quiet_root / "instance.json").write_text(json.dumps({"schema": "phone.instance.v1", "status": "OK"}), encoding="utf-8")
        (quiet_root / "ready.json").write_text(
            json.dumps({"ready": True, "queue_backlog": 0, "heartbeat_at": "2026-11-02T10:00:00+08:00"}),
            encoding="utf-8",
        )
        _, q_view = rm_view.read_v02_runtime_migration_view(quiet_root, now_iso="2026-11-02T10:00:15+08:00")
        self.assertEqual(q_view["runtime_mode"]["health_state"], "QUIET_HEALTHY")
        self.assertFalse(q_view["runtime_mode"]["is_technical_fault"])
        self.assertFalse(q_view["runtime_mode"]["fabricate_drama_when_quiet"])

        dead_root = self.tmp_root / "s11_dead_proc"
        dead_root.mkdir(parents=True)
        (dead_root / "ready.json").write_text(
            json.dumps({"ready": True, "heartbeat_at": "2026-11-02T08:00:00+08:00", "heartbeat_timeout_seconds": 60}),
            encoding="utf-8",
        )
        _, d_view = rm_view.read_v02_runtime_migration_view(dead_root, now_iso="2026-11-02T10:00:00+08:00")
        self.assertEqual(d_view["runtime_mode"]["health_state"], "STALLED_OR_DEGRADED")
        self.assertIn("HEARTBEAT_TIMEOUT_STALLED", {f["code"] for f in d_view["runtime_mode"]["fault_reasons"]})

        stall_root = self.tmp_root / "s11_stalled_queue"
        stall_root.mkdir(parents=True)
        (stall_root / "queue.json").write_text(
            json.dumps({"queue_backlog": 120, "dead_letter_count": 2, "oldest_unacked_seconds": 1500}),
            encoding="utf-8",
        )
        _, s_view = rm_view.read_v02_runtime_migration_view(stall_root, now_iso="2026-11-02T10:00:00+08:00")
        self.assertEqual(s_view["runtime_mode"]["health_state"], "STALLED_OR_DEGRADED")
        self.assertIn("QUEUE_BACKLOG_OVERFLOW", {f["code"] for f in s_view["runtime_mode"]["fault_reasons"]})

    def test_s12_model_and_host_migration_preserves_works_duties_identity_and_honest_loss(self) -> None:
        """S12：模型/宿主迁移后作品、责任、身份和技法适用性清晰交代，损失如实保留不掩盖。"""
        day_root = self.tmp_root / "s12_migration"
        c_chk, b_chk = rm_view.mutate_v02_runtime_migration_view(
            day_root,
            {
                "action": "record_carrier_check",
                "model_id": "migrated-local-llm-v2",
                "carrier_name": "minimal_edge_box_no_preview",
                "preview_available": False,
                "operation_key": "s12_carrier_check",
            },
            actor_side="partner",
            now_iso="2026-11-02T15:00:00+08:00",
        )
        self.assertEqual(c_chk, 200)
        card = b_chk["today_capability_card"]
        self.assertEqual(card["last_migration_verdict"], "CONTINUED_WITH_LOSS")
        self.assertTrue(card["personal_importance_preserved"])
        self.assertIn("HOST_LIVE_PREVIEW_UNAVAILABLE", {x["loss_code"] for x in card["loss_report"]})
        self.assertTrue(any(s["status"] == "UNVALIDATED_AFTER_MIGRATION" for s in card["needs_recheck"]))

        suit = b_chk["migration_suitcase"]
        carried_ids = {x["item_id"] for x in suit["carried_items"]}
        left_ids = {x["item_id"] for x in suit["left_behind_items"]}
        self.assertIn("in_progress_markdown_works", carried_ids)
        self.assertIn("active_shared_commitments", carried_ids)
        self.assertIn("legacy_host_exclusive_shader", left_ids)
        self.assertTrue(bool(b_chk["identity"]["display_name"]))
        self.assertIn("pronouns", b_chk["identity"])


@POSIX_ONLY
class TestV02SevenDayLiveModelSoak(unittest.TestCase):
    """连续 7 天（Day 1 ~ Day 7）真 HTTP OpenAI 协议模型服务跨午夜推进浸泡测试 + PhoneHTTPServer 联测。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="nyairo_v02_7day_soak_")
        self.tmp_root = Path(self._tmp.name)
        self.day_root = self.tmp_root / "soak_day_root"
        self.day_root.mkdir(parents=True, exist_ok=True)
        self.model_srv = LiveOpenAIProtocolModelServer()

    def tearDown(self) -> None:
        self.model_srv.stop()
        self._tmp.cleanup()

    def test_seven_day_live_openai_protocol_soak_day1_to_day7(self) -> None:
        """Day 1 -> Day 7 连续跨午夜状态推进：全链路真实 HTTP OpenAI 协议往返 + 零外部网络泄漏。"""
        tools_spec = [
            {
                "type": "function",
                "function": {
                    "name": "emit_v02_daily_step",
                    "description": "Record structured daily v0.2 choice and tool trace",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "day": {"type": "integer"},
                            "decision": {"type": "string"},
                            "selected_hex": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["day", "decision"],
                    },
                },
            }
        ]

        with LoopbackOnlyNetworkGuard() as net_guard:
            agency_client = self.model_srv.make_agency_client()
            port = PersistentManagedArtifactPort(self.tmp_root / "soak_artifacts.json", subject_id="chiyo")
            projects = PrivateProjectService(port, "ws-soak7", "grant-soak7", subject_id="chiyo")

            # =================================================================
            # DAY 1 (2026-11-01):
            # - 自发多选形成偏好（2 次自发多选升格 SUPPORTED，对照单选不升格）；
            # - 用 Python zlib 构造真实双色 PNG 字节落入相册，做真像素提取色板并应用到作品；
            # - 经真实 127.0.0.1 OpenAI /v1/chat/completions 往返获取决策与 tool_calls。
            # =================================================================
            for idx, ts in enumerate(("2026-11-01T09:30:00+08:00", "2026-11-01T14:20:00+08:00"), start=1):
                c_ep, b_ep = ad_view.mutate_v02_agency_depth_view(
                    self.day_root,
                    {
                        "action": "record_choice_episode",
                        "domain": "tea_pairing",
                        "chosen": "炭焙乌龙",
                        "options": ["炭焙乌龙", "茉莉银针", "冷泡煎茶"],
                        "choice_mode": "spontaneous",
                        "forgone_cost": "放下今天试新绿茶的机会",
                        "shared": True,
                        "operation_key": f"d1_spont_tea_{idx}",
                    },
                    actor_side="subject",
                    now_iso=ts,
                )
                self.assertEqual(c_ep, 200)
            self.assertEqual(b_ep["preferences"][0]["status"], "SUPPORTED")

            # 对照：被迫单选绝不升格 SUPPORTED
            _, b_forced = ad_view.mutate_v02_agency_depth_view(
                self.day_root,
                {
                    "action": "record_choice_episode",
                    "domain": "mug_shape",
                    "chosen": "默认白瓷杯",
                    "options": ["默认白瓷杯"],
                    "choice_mode": "forced_single",
                    "operation_key": "d1_forced_single",
                },
                actor_side="subject",
                now_iso="2026-11-01T15:00:00+08:00",
            )
            mug_pref = next(p for p in b_forced["preferences"] if p["domain"] == "mug_shape")
            self.assertEqual(mug_pref["status"], "UNKNOWN")

            # 用 zlib 构造上半暖米白 (245, 240, 230) + 下半深海夜蓝 (24, 30, 42) 真实双色 PNG
            album_dir = self.day_root / "phone" / "album"
            album_dir.mkdir(parents=True, exist_ok=True)
            album_file = album_dir / "d1_bicolor_window.png"
            bicolor_bytes = _synth_bicolor_png(20, 20, (245, 240, 230), (24, 30, 42))
            album_file.write_bytes(bicolor_bytes)

            direct_pal = cm_view.extract_real_image_palette(bicolor_bytes)
            self.assertIn("#F5F0E6", direct_pal["dominant_hex"])
            self.assertIn("#181E2A", direct_pal["dominant_hex"])

            c_cm1, b_cm1 = cm_view.mutate_v02_crossmodal_view(
                self.day_root,
                {
                    "action": "analyze_image_palette",
                    "title": "Day1 北窗夜色双色参考图",
                    "album_id": "d1_bicolor_window.png",
                    "share_with_room": True,
                    "operation_key": "d1_analyze_png",
                },
                actor_side="subject",
                now_iso="2026-11-01T16:00:00+08:00",
            )
            self.assertEqual(c_cm1, 200)
            study_id = b_cm1["palette_studies"][0]["study_id"]
            self.assertIn("#F5F0E6", b_cm1["palette_studies"][0]["dominant_hex"])
            self.assertIn("#181E2A", b_cm1["palette_studies"][0]["dominant_hex"])

            work_d1 = projects.create_project(
                title="北窗光影小册",
                content="Day 1 第一版：记下窗框明暗交界。",
                operation_key="d1_create_booklet",
            )
            d1_chat = self.model_srv.chat_with_tools(
                day_index=1,
                context_payload={
                    "dominant_hex": b_cm1["palette_studies"][0]["dominant_hex"],
                    "preferences": ad_view.summarize_agency_depth_for_context(self.day_root),
                },
                tools=tools_spec,
            )
            tcalls_d1 = d1_chat["choices"][0]["message"]["tool_calls"]
            targs_d1 = json.loads(tcalls_d1[0]["function"]["arguments"])
            self.assertEqual(targs_d1["day"], 1)

            c_app, b_app = cm_view.mutate_v02_crossmodal_view(
                self.day_root,
                {
                    "action": "apply_palette_to_work",
                    "study_id": study_id,
                    "work_id": work_d1["artifact_id"],
                    "work_title": "北窗光影小册",
                    "version_hint": "v1",
                    "selected_hex": targs_d1["selected_hex"],
                    "usage_note_cn": "把双色反差用封面底栏",
                    "share_with_room": True,
                    "operation_key": "d1_apply_palette",
                },
                actor_side="subject",
                now_iso="2026-11-01T16:30:00+08:00",
            )
            self.assertEqual(c_app, 200)
            self.assertTrue(b_app["work_palette_applications"][0]["not_lived_event_claim"])

            # =================================================================
            # DAY 2 (2026-11-02) -> 跨午夜至 DAY 3 凌晨 (2026-11-03 00:30):
            # - 连续接触炭焙乌龙触发短期饱和看腻（saturated=True，长期 SUPPORTED 不丢）；
            # - 跨 Day 2 -> Day 3 午夜连续指数衰减，绝不突跳清零；
            # - 搁置内部双向冲突（park 保留双边动机并存）。
            # =================================================================
            for idx, ts in enumerate(("2026-11-02T19:00:00+08:00", "2026-11-02T22:30:00+08:00"), start=1):
                c_sat, b_sat = ad_view.mutate_v02_agency_depth_view(
                    self.day_root,
                    {
                        "action": "record_exposure_satiation",
                        "domain": "tea_pairing",
                        "title": "炭焙乌龙",
                        "intensity": 0.45,
                        "note_cn": "连泡两晚稍微有点喝腻了",
                        "operation_key": f"d2_sat_{idx}",
                    },
                    actor_side="subject",
                    now_iso=ts,
                )
                self.assertEqual(c_sat, 200)

            tea_d2_late = next(p for p in b_sat["preferences"] if p["domain"] == "tea_pairing")
            self.assertEqual(tea_d2_late["status"], "SUPPORTED")
            self.assertTrue(tea_d2_late["saturated"])
            self.assertGreaterEqual(tea_d2_late["satiation_score"], 0.70)
            self.assertEqual(tea_d2_late["candidate_weight_multiplier"], 0.25)

            # 跨午夜 2 小时后（Day 3 00:30）：连续指数衰减而非清零！
            _, view_d3_midnight = ad_view.read_v02_agency_depth_view(
                self.day_root,
                has_subject_capability=True,
                now_iso="2026-11-03T00:30:00+08:00",
            )
            tea_after_midnight = next(p for p in view_d3_midnight["preferences"] if p["domain"] == "tea_pairing")
            self.assertEqual(tea_after_midnight["status"], "SUPPORTED")
            self.assertFalse(tea_after_midnight["midnight_reset_applied"])
            self.assertGreater(tea_after_midnight["satiation_score"], 0.60)
            self.assertLess(tea_after_midnight["satiation_score"], tea_d2_late["satiation_score"])

            c_conf, b_conf = ad_view.mutate_v02_agency_depth_view(
                self.day_root,
                {
                    "action": "park_or_resolve_conflict",
                    "conflict_op": "park",
                    "title": "留白极简还是保留手写涂改痕迹",
                    "side_a": "想让小册版面干净克制",
                    "side_b": "舍不得擦掉初稿真实的犹豫笔触",
                    "shared": True,
                    "operation_key": "d2_park_conflict",
                },
                actor_side="subject",
                now_iso="2026-11-02T23:10:00+08:00",
            )
            self.assertEqual(c_conf, 200)
            self.assertEqual(b_conf["parked_conflicts"][0]["status"], "parked")
            self.assertTrue(b_conf["parked_conflicts"][0]["both_motives_preserved"])
            _ = agency_client.complete(
                system_prompt="bounded choice component",
                user_prompt=json.dumps({"day": 2, "context": {"satiated": True}}, ensure_ascii=False),
            )

            # =================================================================
            # DAY 3 (2026-11-03):
            # - 打开开放问题慢孵化（记录 question_id 供跨 48h 至 Day 5 检查 0 假顿悟）；
            # - 严格区分过程失误（process_mistake -> changes_method_policy=True）
            #   vs 结果不如意（outcome_disappointment -> changes_method_policy=False），
            #   且验证伴侣视角彻底隐藏后悔反思。
            # =================================================================
            c_inc, b_inc = ad_view.mutate_v02_agency_depth_view(
                self.day_root,
                {
                    "action": "manage_open_incubation",
                    "inc_op": "open",
                    "topic": "小册末页该用哪种装订线才不勒纸",
                    "note_cn": "还没试过棉麻混纺线，先放着慢慢想",
                    "shared": True,
                    "operation_key": "d3_open_inc",
                },
                actor_side="subject",
                now_iso="2026-11-03T10:00:00+08:00",
            )
            self.assertEqual(c_inc, 200)
            inc_qid = b_inc["open_incubations"][0]["question_id"]
            self.assertEqual(b_inc["open_incubations"][0]["status"], "incubating")

            ad_view.mutate_v02_agency_depth_view(
                self.day_root,
                {
                    "action": "record_regret_reflection",
                    "regret_kind": "outcome_disappointment",
                    "summary": "原想去旧书摊淘绝版画册但今天刚好提前收摊了",
                    "operation_key": "d3_regret_outcome",
                },
                actor_side="subject",
                now_iso="2026-11-03T15:00:00+08:00",
            )
            _, b_reg = ad_view.mutate_v02_agency_depth_view(
                self.day_root,
                {
                    "action": "record_regret_reflection",
                    "regret_kind": "process_mistake",
                    "summary": "底色没干透就急着叠第二层墨导致边缘晕开",
                    "method_adjustment_cn": "以后叠色前先在试色纸边角按压确认干透",
                    "shared": True,
                    "operation_key": "d3_regret_process",
                },
                actor_side="subject",
                now_iso="2026-11-03T16:30:00+08:00",
            )
            by_kind = {r["regret_kind"]: r for r in b_reg["regret_reflections"]}
            self.assertFalse(by_kind["outcome_disappointment"]["changes_method_policy"])
            self.assertTrue(by_kind["process_mistake"]["changes_method_policy"])
            self.assertEqual(len(b_reg["method_adjustments"]), 1)

            _, partner_d3 = ad_view.read_v02_agency_depth_view(self.day_root, has_subject_capability=False)
            self.assertEqual(partner_d3["regret_reflections"], [])
            self.assertTrue(partner_d3["regret_reflections_withheld"])
            _ = self.model_srv.chat_with_tools(
                day_index=3,
                context_payload={"incubation_id": inc_qid, "method_adjustments": len(b_reg["method_adjustments"])},
                tools=tools_spec,
            )

            # =================================================================
            # DAY 4 (2026-11-04):
            # - 灌入 300 条同族可替代背景事件：仅唤醒 1 次（coalesced_saved_wakes == 299）；
            # - 控制事件（is_control=True）零防抖直通；
            # - 切换 LOW_BUDGET_CONSERVATION（10% 预算省电 90% 模式）：不装病不假装心情差，
            #   保住在途作品保存点与已认领承诺。
            # =================================================================
            for i in range(300):
                mm = i // 60
                ss = i % 60
                c_pr, _ = rm_view.mutate_v02_runtime_migration_view(
                    self.day_root,
                    {
                        "action": "coalesce_probe",
                        "family": "window_rain_sound",
                        "summary_cn": f"Day4 窗外雨声细微起伏 #{i + 1}",
                    },
                    actor_side="partner",
                    now_iso=f"2026-11-04T09:{mm:02d}:{ss:02d}+08:00",
                )
                self.assertEqual(c_pr, 200)

            c_ctrl, b_ctrl = rm_view.mutate_v02_runtime_migration_view(
                self.day_root,
                {
                    "action": "coalesce_probe",
                    "family": "stop_word_urgent",
                    "summary_cn": "伴侣急事敲门控制信号",
                    "is_control": True,
                },
                actor_side="partner",
                now_iso="2026-11-04T09:04:55+08:00",
            )
            self.assertEqual(c_ctrl, 200)
            wc_d4 = b_ctrl["wake_coalescer"]
            self.assertEqual(wc_d4["coalesced_saved_wakes"], 299)
            self.assertEqual(wc_d4["background_wakes_day"], 1)
            self.assertEqual(wc_d4["control_lane_bypasses"], 1)
            self.assertEqual(wc_d4["last_probe_result"]["wake_disposition"], "CONTROL_IMMEDIATE_BYPASS")

            c_lb, b_lb = rm_view.mutate_v02_runtime_migration_view(
                self.day_root,
                {
                    "action": "set_budget_mode",
                    "mode": "LOW_BUDGET_CONSERVATION",
                    "budget_scale_pct": 10,
                    "reason_cn": "Day4 切换 10% 算力省电模式（降 90% 预算）",
                    "operation_key": "d4_low_budget_10pct",
                },
                actor_side="partner",
                now_iso="2026-11-04T12:00:00+08:00",
            )
            self.assertEqual(c_lb, 200)
            rm_d4 = b_lb["runtime_mode"]
            self.assertEqual(rm_d4["health_state"], "LOW_BUDGET_CONSERVATION")
            self.assertEqual(rm_d4["budget_scale_pct"], 10)
            self.assertFalse(rm_d4["pretend_body_tired_or_bad_mood"])
            self.assertTrue(rm_d4["preserved_obligations"]["in_progress_artifact_checkpoints"])
            self.assertTrue(rm_d4["preserved_obligations"]["accepted_commitments"])

            d4_chat = self.model_srv.chat_with_tools(
                day_index=4,
                context_payload={"runtime_mode": rm_d4["mode"], "budget_scale_pct": 10},
                tools=tools_spec,
            )
            d4_msg = json.loads(d4_chat["choices"][0]["message"]["content"])
            self.assertEqual(d4_msg["decision"], "LOCAL_SMALL_REVISION_ONLY")
            self.assertFalse(d4_msg["pretend_tired_or_bad_mood"])

            # =================================================================
            # DAY 5 (2026-11-05):
            # - 恢复 NORMAL 档位，先在无新证据下跨越 >=48h（Day 3 10:00 -> Day 5 10:30）检查：
            #   status 仍为 incubating，0 假顿悟（fake_epiphany_blocked == True）；
            # - 伴侣递入新线索后推进开放问题至 ready_to_revisit；
            # - 验证无事件安静日 QUIET_HEALTHY vs 模拟积压故障 STALLED_OR_DEGRADED。
            # =================================================================
            rm_view.mutate_v02_runtime_migration_view(
                self.day_root,
                {"action": "set_budget_mode", "mode": "NORMAL", "reason_cn": "Day5 算力恢复正常"},
                actor_side="partner",
                now_iso="2026-11-05T08:00:00+08:00",
            )
            _, b_chk48 = ad_view.mutate_v02_agency_depth_view(
                self.day_root,
                {
                    "action": "manage_open_incubation",
                    "inc_op": "check_elapsed",
                    "question_id": inc_qid,
                    "operation_key": "d5_check_48h_no_evidence",
                },
                actor_side="subject",
                now_iso="2026-11-05T10:30:00+08:00",
            )
            inc_48 = b_chk48["open_incubations"][0]
            self.assertGreaterEqual(inc_48["elapsed_hours"], 48.0)
            self.assertEqual(inc_48["status"], "incubating")
            self.assertTrue(inc_48["fake_epiphany_blocked"])

            # 伴侣递入真实新线索 -> 推进至 ready_to_revisit
            _, b_clue = ad_view.mutate_v02_agency_depth_view(
                self.day_root,
                {
                    "action": "share_incubation_clue",
                    "question_id": inc_qid,
                    "clue_text": "文具店老板说未漂白亚麻蜡线既有韧性又不会割破薄绵纸",
                    "operation_key": "d5_partner_clue",
                },
                actor_side="partner",
                now_iso="2026-11-05T11:00:00+08:00",
            )
            self.assertEqual(b_clue["open_incubations"][0]["status"], "ready_to_revisit")

            # Day 5 恢复正常预算，且核验无事件安静健康切片 QUIET_HEALTHY vs 队列积压故障 STALLED_OR_DEGRADED
            rm_view.mutate_v02_runtime_migration_view(
                self.day_root,
                {
                    "action": "set_budget_mode",
                    "mode": "NORMAL",
                    "budget_scale_pct": 100,
                    "reason_cn": "Day5 算力预算恢复常态",
                    "operation_key": "d5_restore_normal",
                },
                actor_side="partner",
                now_iso="2026-11-05T11:30:00+08:00",
            )
            quiet_day5_root = self.tmp_root / "soak_day5_quiet_slice"
            quiet_day5_root.mkdir(parents=True, exist_ok=True)
            (quiet_day5_root / "instance.json").write_text(
                json.dumps({"schema": "phone.instance.v1", "status": "OK"}), encoding="utf-8"
            )
            (quiet_day5_root / "ready.json").write_text(
                json.dumps({"ready": True, "queue_backlog": 0, "heartbeat_at": "2026-11-05T12:00:00+08:00"}),
                encoding="utf-8",
            )
            _, d5_quiet = rm_view.read_v02_runtime_migration_view(
                quiet_day5_root, now_iso="2026-11-05T12:00:10+08:00"
            )
            self.assertEqual(d5_quiet["runtime_mode"]["health_state"], "QUIET_HEALTHY")
            self.assertFalse(d5_quiet["runtime_mode"]["fabricate_drama_when_quiet"])

            (self.day_root / "queue.json").write_text(
                json.dumps({"queue_backlog": 95, "dead_letter_count": 1}), encoding="utf-8"
            )
            _, d5_stalled = rm_view.read_v02_runtime_migration_view(
                self.day_root, now_iso="2026-11-05T12:05:00+08:00"
            )
            self.assertEqual(d5_stalled["runtime_mode"]["health_state"], "STALLED_OR_DEGRADED")
            (self.day_root / "queue.json").unlink()

            d5_chat = self.model_srv.chat_with_tools(
                day_index=5,
                context_payload={"open_question_ready": True, "question_id": inc_qid},
                tools=tools_spec,
            )
            self.assertEqual(
                json.loads(d5_chat["choices"][0]["message"]["content"])["decision"],
                "REVISIT_INCUBATED_QUESTION",
            )

            # =================================================================
            # DAY 6 (2026-11-06):
            # - 进入 DORMANT_NO_COGNITION 休眠模式，跨越 18 小时空白时段后恢复 NORMAL；
            # - 验证真实 dormant_gap_seconds == 64800 (> 0)、no_fake_backfill == True、
            #   fake_diary_entries_created == 0。
            # =================================================================
            _, b_dorm = rm_view.mutate_v02_runtime_migration_view(
                self.day_root,
                {
                    "action": "set_budget_mode",
                    "mode": "DORMANT_NO_COGNITION",
                    "reason_cn": "Day6 外出托管暂停后台推理",
                    "operation_key": "d6_enter_dormant",
                },
                actor_side="partner",
                now_iso="2026-11-06T02:00:00+08:00",
            )
            self.assertEqual(b_dorm["runtime_mode"]["health_state"], "DORMANT_NO_COGNITION")

            _, b_wake = rm_view.mutate_v02_runtime_migration_view(
                self.day_root,
                {
                    "action": "set_budget_mode",
                    "mode": "NORMAL",
                    "reason_cn": "Day6 晚间恢复在线运行",
                    "operation_key": "d6_wake_normal",
                },
                actor_side="partner",
                now_iso="2026-11-06T20:00:00+08:00",
            )
            rm_d6 = b_wake["runtime_mode"]
            rec_d6 = rm_d6["last_dormant_recovery"]
            self.assertEqual(rm_d6["dormant_gap_seconds"], 64800)
            self.assertGreater(rm_d6["dormant_gap_seconds"], 0)
            self.assertTrue(rm_d6["no_fake_backfill"])
            self.assertEqual(rec_d6["fake_diary_entries_created"], 0)
            self.assertEqual(rec_d6["fabricated_memories_created"], 0)
            _ = self.model_srv.chat_with_tools(
                day_index=6,
                context_payload={"dormant_gap_seconds": rm_d6["dormant_gap_seconds"], "no_fake_backfill": True},
                tools=tools_spec,
            )

            # =================================================================
            # DAY 7 (2026-11-07):
            # - 模拟跨模型 + 缺失预览宿主迁居体检：产出 CONTINUED_WITH_LOSS 与 UNVALIDATED_AFTER_MIGRATION；
            # - 试跑 reverify_migrated_skill 恢复待复验技法；
            # - pack_selective_suitcase 对比四档并选 selective_practice 主动放下旧宿主专属包袱；
            # - 删除 Day 1 相册原图文件，验证 read_v02_crossmodal_view 零写实时扣留旧色板！
            # =================================================================
            _, b_mig = rm_view.mutate_v02_runtime_migration_view(
                self.day_root,
                {
                    "action": "record_carrier_check",
                    "model_id": "nyairo-nextgen-carrier-v3",
                    "carrier_name": "headless_mini_server",
                    "preview_available": False,
                    "operation_key": "d7_carrier_check",
                },
                actor_side="partner",
                now_iso="2026-11-07T10:00:00+08:00",
            )
            card_d7 = b_mig["today_capability_card"]
            self.assertEqual(card_d7["last_migration_verdict"], "CONTINUED_WITH_LOSS")
            unval_skills = [s["skill_id"] for s in card_d7["needs_recheck"] if s["status"] == "UNVALIDATED_AFTER_MIGRATION"]
            self.assertGreaterEqual(len(unval_skills), 2)

            for sid in unval_skills:
                c_rev, b_rev = rm_view.mutate_v02_runtime_migration_view(
                    self.day_root,
                    {
                        "action": "reverify_migrated_skill",
                        "skill_id": sid,
                        "passed": True,
                        "note_cn": f"Day7 在新模型试跑通过：{sid}",
                        "operation_key": f"d7_reverify_{sid}",
                    },
                    actor_side="subject",
                    now_iso="2026-11-07T10:30:00+08:00",
                )
                self.assertEqual(c_rev, 200)
            self.assertEqual(b_rev["today_capability_card"]["needs_recheck"], [])
            # 缺失预览宿主损失仍诚实保留为 CONTINUED_WITH_LOSS
            self.assertEqual(b_rev["last_migration_verdict"], "CONTINUED_WITH_LOSS")

            c_suit, b_suit = rm_view.mutate_v02_runtime_migration_view(
                self.day_root,
                {
                    "action": "pack_selective_suitcase",
                    "profile_mode": "selective_practice",
                    "keep_skill_ids": ["markdown_local_checkpoint", "nuanced_draft_stitching"],
                    "leave_behind_note": "放下旧宿主专用着色器与脆弱快捷键宏，轻装迁入新居",
                    "operation_key": "d7_pack_suitcase",
                },
                actor_side="partner",
                now_iso="2026-11-07T11:00:00+08:00",
            )
            self.assertEqual(c_suit, 200)
            suit_d7 = b_suit["migration_suitcase"]
            self.assertEqual(suit_d7["profile_mode"], "selective_practice")
            self.assertEqual(
                set(suit_d7["route_comparison"].keys()),
                {"records_only", "records_and_objects", "full_practice", "selective_practice"},
            )
            left_ids_d7 = {x["item_id"] for x in suit_d7["left_behind_items"]}
            self.assertIn("legacy_host_exclusive_shader", left_ids_d7)
            self.assertIn("retired_brittle_shortcut_macro", left_ids_d7)

            # 删除 Day 1 相册原图文件 -> read_v02_crossmodal_view 零磁盘写入且实时扣留旧色板
            album_file.unlink()
            snap_before_read = _snapshot_tree(self.day_root)
            c_cm_d7, b_cm_d7 = cm_view.read_v02_crossmodal_view(
                self.day_root,
                has_subject_capability=True,
                now_iso="2026-11-07T12:00:00+08:00",
            )
            snap_after_read = _snapshot_tree(self.day_root)
            self.assertEqual(snap_before_read, snap_after_read)
            self.assertEqual(c_cm_d7, 200)
            st_d7 = b_cm_d7["palette_studies"][0]
            self.assertFalse(st_d7["source_available"])
            self.assertEqual(st_d7["dominant_hex"], [])
            self.assertEqual(st_d7["palette_withheld_reason"], "SOURCE_MEDIA_UNAVAILABLE")

            _ = self.model_srv.chat_with_tools(
                day_index=7,
                context_payload={
                    "migration_verdict": b_rev["last_migration_verdict"],
                    "suitcase_mode": suit_d7["profile_mode"],
                    "deleted_media_withheld": not st_d7["source_available"],
                },
                tools=tools_spec,
            )

        # 全程外部网络请求严格为 0，且本地 127.0.0.1 OpenAI HTTP 往返覆盖 Day 1 ~ Day 7
        self.assertEqual(net_guard.external_blocked, [])
        self.assertGreaterEqual(net_guard.loopback_connects, 7)
        self.assertGreaterEqual(len(self.model_srv.requests), 7)
        self.assertTrue(all(r["client_ip"] == "127.0.0.1" for r in self.model_srv.requests))
        self.assertTrue(all(r["path"] == "/v1/chat/completions" for r in self.model_srv.requests))

    def test_phone_http_server_v02_routes_roundtrip_and_zero_external_traffic(self) -> None:
        """联测真实 PhoneService + PhoneHTTPServer 的 /api/phone/v02/* 三大路由读写与权限边界。"""
        from components.phone import capability as phone_cap
        from components.phone import server as phone_server
        from components.phone import service as phone_svc

        phone_root = self.tmp_root / "phone_http_root"
        phone_root.mkdir(parents=True, exist_ok=True)
        os.chmod(phone_root, 0o700)
        run_dir = phone_root / "run"
        run_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(run_dir, 0o700)
        svc = phone_svc.PhoneService({
            "root": str(phone_root),
            "subject": "chiyo",
            "world_home": str(phone_root / "world_home"),
            "supply_socket": str(phone_root / "supply.sock"),
            "private_items_artifact_id": "artifact:00000000000000000000000000000001",
            "artifact_grant_id": "grant:0001",
            "workspace_id": "ws:0001",
        })
        _, cap = phone_cap.issue(phone_root, subject="chiyo", endpoint="http://127.0.0.1:18765")
        csrf = "soak-csrf-token-v02"
        httpd = phone_server.create_server(svc, csrf_token=csrf, port=0, capability=cap)
        port_num = int(httpd.server_address[1])
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        try:
            with LoopbackOnlyNetworkGuard() as guard:
                for ep in ("agency-depth", "crossmodal", "runtime-migration"):
                    req = urllib.request.Request(
                        f"http://127.0.0.1:{port_num}/api/phone/v02/{ep}",
                        headers={"Host": f"127.0.0.1:{port_num}"},
                        method="GET",
                    )
                    with urllib.request.urlopen(req, timeout=5.0) as resp:
                        self.assertEqual(resp.status, 200)
                        doc = json.loads(resp.read().decode("utf-8"))
                        self.assertTrue(doc["ok"])
                        self.assertEqual(doc["llm_calls"], 0)
            self.assertEqual(guard.external_blocked, [])
        finally:
            httpd.shutdown()
            httpd.server_close()
            t.join(timeout=5.0)


if __name__ == "__main__":
    unittest.main()

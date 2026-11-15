#!/usr/bin/env python3
"""v0.4 手机服务（P）：真实 Supply 私人事项 + W 的 PhoneShopping 的本地 owner 面。

它是什么
--------
一部**本机**手机的运行时。它不拥有任何真相：

==========================  ==================================================
笔记/收藏/阅读元数据        Life Supply ``ManagedArtifact``（``PrivateDailyItems``）
阅读正文/停点/页注          C 的 ``PhoneReading``（同一 Supply artifact + ``PrivateDailyItems``）
私人项目（多份作品）        Life Supply ``ManagedArtifact``（经 ``private_project_service``）
钱包/虚拟店/物品/消耗        ``world_property_economy`` + ``world_body_substrate``
                            + Resolver（全部经 W 的 ``PhoneShopping``）
实体店/交付/布置/灯         同上一个 owner（``PhoneShopping.store`` / ``store_purchase`` /
                            世界动词 ``ARRANGE`` / ``TOGGLE_FIXTURE`` / ``TOILET``）；P 只透传
操作来源（用户/模型）        ``<root>/phone/operations.jsonl``（只追加的窄日志）
==========================  ==================================================

它不是什么
----------
* 不是新的 Store：没有本地平行 JSON 库，笔记与阅读永远走 ``life_artifact_port``。
* 不是手机 authority：钱、产权、位置、消耗回执、幂等全部由既有 owner 决定；本模块只
  编排、只透传，从不"直接改 state"、从不让客户端传 uid/价格/余额当成身份。
* 不是 mock：所有依赖不可用时如实 ``unavailable``，绝不返回一个看起来成功的假结果。

绑定
----
``instance.json`` 里写明 ``supply_socket`` / ``private_items_artifact_id`` /
``artifact_grant_id`` / ``workspace_id`` / ``world_home`` / ``world_body_socket``。手机只读
写这一个 artifact、只拿一个 world home 的账；``latest_resumable`` 永不使用。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import socket
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
_LIFE_SCRIPTS = REPO_ROOT / "components" / "life" / "scripts"
_WORLD_SCRIPTS = REPO_ROOT / "components" / "world" / "scripts"
for _entry in (str(REPO_ROOT), str(_LIFE_SCRIPTS), str(_WORLD_SCRIPTS)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

INSTANCE_FILENAME = "instance.json"
SCHEMA_INSTANCE = "phone.instance.v1"
KIND_INSTANCE = "phone_local_instance"

APPS: tuple[str, ...] = ("notes", "diary", "references", "reading", "wallet", "shop", "store",
                         "inventory", "body", "world", "projects", "shares", "contact",
                         "shared_practice")

#: The World actions a local owner may ask for.  The first five are the frozen v0.4 set
#: (``docs/engineering/v04-world-body-contract.md`` §3.1); ``ARRANGE`` and ``TOGGLE_FIXTURE`` are
#: this round's room verbs, and ``TOILET`` is the optional lived-needs facility verb
#: (``2026-10-27-body-lived-needs``), exactly as C's ``PhoneWorld.VERBS`` / ``PARAM_SPEC``
#: name them.  P never renames an action:
#: the owner's own ``actions.verbs``, its parameter table and its reason codes stay the
#: authority, and P translates none of them.
WORLD_ACTIONS: tuple[str, ...] = ("MOVE", "PICK_UP", "PLACE", "POSE", "REST",
                                  "ARRANGE", "TOGGLE_FIXTURE", "TOILET")

#: The light switch is an absolute state, never a blind toggle.
FIXTURE_STATES: frozenset[str] = frozenset({"on", "off"})

#: The nine snapshot blocks C publishes (``v04-world-body-contract.md`` §2).  P only
#: summarizes them; a missing block is reported as missing, never as an empty success.
WORLD_SNAPSHOT_BLOCKS: tuple[str, ...] = (
    "world", "location", "local_environment", "destinations", "travel", "objects", "body",
    "actions", "recent_executions",
)

#: The body block fields P copies verbatim (same contract, §2.7).  P never derives a signal,
#: a band or any value of its own -- and never a raw float.
BODY_VIEW_FIELDS: tuple[str, ...] = (
    "pose", "orientation", "pose_source", "recovery", "signals", "signals_status",
    "signals_source", "bands_only", "raw_values_exposed", "rest_evidence",
    # Optional virtual lived-need bands; absent/None for every instance without
    # an explicit profile.
    "needs", "note",
)

STATUS_OK = "applied"
STATUS_ALREADY = "already_applied"
STATUS_REJECTED = "rejected"
STATUS_UNKNOWN = "unknown"
STATUS_UNAVAILABLE = "unavailable"
STATUS_DISABLED = "disabled"
STATUS_NOT_FOUND = "not_found"

#: ``GET /api/phone/today``：她今天已经发生的事，纯读。
#: 日期按 +08:00 分界——与 Activity 记录自己的写入时区（``activity_compat.BJT``）同一日界，
#: 免得一份记录按 UTC 另算一天。业务时钟存在时以它的逻辑时刻为准，缺失/损坏才退回系统时钟，
#: 并在回答里具名说明，绝不把「读不到」说成「今天什么都没做」。
TODAY_SCHEMA = "phone.today.v1"
TODAY_TIMEZONE = "+08:00"
TODAY_TZ = timezone(timedelta(hours=8))
#: 一次读取最多看这么多件作品；超出的部分如实标 truncated，不假装她今天只写了这么多。
TODAY_MAX_WORKS = 50
#: 每件作品只看最近一页版本元数据（今天产生的版本几乎总在最近页）。
TODAY_VERSION_PAGE = 20
CANONICAL_ACTIVITY_FILENAME = "canonical_life_state.json"
BUSINESS_CLOCK_DIRNAME = "fastday"
BUSINESS_CLOCK_FILENAME = "fastday_clock.json"

MAX_OPERATION_KEY = 128
MAX_TITLE = 80
MAX_BODY = 4000
#: 一条日记的上限与 ``private_daily_items.MAX_DIARY_CHARS`` 同值；超长拒绝，绝不截断。
MAX_DIARY_BODY = 8000
MAX_SOURCE_ID = 200
MAX_SOURCE_REVISION = 120
MAX_LABEL = 60

#: The reading workbench's bounds (C's ``rc1-reading-contract.md``).  P pre-checks only what it
#: can see: the page split and the per-page limit are C's owner rules, and its reason code wins.
MAX_READING_CONTENT_BYTES = 24000
MAX_READING_PAGE = 64
MAX_VERSION_ID = 128
#: One page annotation.  C measures these in UTF-8 bytes (``PhoneReading._text``), and the contract
#: names ``title ≤200`` / ``body|note ≤8000``; P uses the same unit so it never accepts something
#: the owner will then refuse.
MAX_READING_TITLE_BYTES = 200
MAX_READING_ANNOTATION_BYTES = 8000

#: The private-project workbench (B's ``private_project_service``, on the same ManagedArtifact
#: owner).  P pre-checks only what it can see; the owner's own bounds and reason codes win.
MAX_PROJECT_TITLE = 120
MAX_PROJECT_CONTENT = 20000
MAX_PROJECT_ID = 200
#: v0.4 work-revisit: one page of version metadata, and the three purposes an old version may be
#: taken for.  The bound matches the service's own ``MAX_HISTORY``; the phone never asks for more.
MAX_PROJECT_HISTORY_LIMIT = 50
MAX_SOURCE_NOTE = 120
PROJECT_SOURCE_PURPOSES = ("quote", "adapt", "reuse")

#: 普通项目列表要标出每一件的类型（project / material / research）。类型只来自工件第一行的
#: 标记，所以每件最多读一次；超过这个行数就不再逐件读，类型诚实地留成 ``unknown``。
MAX_PROJECT_TYPE_SCAN = 50

#: 资料与研究的边界（B 的 ``research_workspace_service``）。P 自己也做一遍可见的预检，
#: 但最终以服务/owner 的答复为准。
MAX_MATERIAL_TITLE = 120
MAX_MATERIAL_TEXT = 19000
MAX_MATERIAL_ORIGIN = 300
MAX_RESEARCH_QUESTION = 500
MAX_RESEARCH_SCOPE = 400
MAX_RESEARCH_UNCERTAINTY = 400
MAX_RESEARCH_CONTENT = 19000
MAX_RESEARCH_SOURCES = 12
MAX_RESEARCH_LIMIT = 50
MAX_MATERIAL_LIMIT = 50
MAX_MATERIAL_QUERY = 120
MATERIAL_SOURCE_KINDS: tuple[str, ...] = ("user_supplied", "fictional")

#: 经历检索与个人实践工作台的边界。P 只做可见的预检；A/B 自己的上限与原因码为准。
MAX_MEMORY_EXPERIENCES = 20
MAX_MEMORY_QUERY = 120
#: 一份实践文档里的引用最多几条（B 的 hard limit 是 12；这里只是同一件事的预检）。
MAX_PRACTICE_REFS = 12
MAX_PRACTICE_QUESTION = 500
MAX_PRACTICE_TITLE = 120
MAX_PRACTICE_INSTRUCTION = 2000
MAX_PRACTICE_SCOPE = 400
MAX_PRACTICE_UNCERTAINTY = 400
MAX_PRACTICE_CONTENT = 18000
MAX_PRACTICE_TRIAL_DETAIL = 600
PRACTICE_LIST_LIMIT = 12
#: 做法状态与试用结果的封闭词表（B 的契约；P 不发明第五个状态）。
PRACTICE_METHOD_STATES: tuple[str, ...] = ("TENTATIVE", "TRIAL", "CONTESTED", "RETIRED")
PRACTICE_TRIAL_OUTCOMES: tuple[str, ...] = ("HELPED", "NOT_HELPED", "UNKNOWN")
#: 实例文件里可能记录 Memory persona 的字段名（只在 env 没有时作为兜底读一次）。
MEMORY_OWNER_FIELD = "memory_owner"

#: Declared request origin.  A *declaration* by the local client, recorded, never an authority.
ACTOR_USER = "user"
ACTOR_MODEL = "model"
ACTORS = (ACTOR_USER, ACTOR_MODEL)


def profile_fingerprint(value: Path | str) -> str:
    """A stable, non-reversible name for one profile home (never the path itself).

    The phone tool carries the *expected* phone instance root; the server only publishes this
    fingerprint, so "is this my phone?" can be checked without leaking an absolute path to the
    browser UI.
    """

    return "phone:" + hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]


class PhoneServiceError(RuntimeError):
    """The phone service itself cannot serve this instance.  Never a fake success."""


class PhoneUnavailable(PhoneServiceError):
    """One dependency (Supply / World / economy) is not reachable right now."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        super().__init__("%s:%s" % (reason_code, detail)[:200])
        self.reason_code = str(reason_code)
        self.detail = str(detail)[:200]


# ---------------------------------------------------------------------------------------------
# envelopes
# ---------------------------------------------------------------------------------------------


def envelope(
    ok: bool,
    status: str,
    data: Optional[Mapping[str, Any]] = None,
    reason_code: Optional[str] = None,
) -> dict[str, Any]:
    """The one shape every endpoint returns (see docs/engineering/v04-phone-api-contract.md)."""

    payload: dict[str, Any] = {"ok": bool(ok), "status": str(status)}
    if reason_code:
        payload["reason_code"] = str(reason_code)
    payload["data"] = dict(data or {})
    return payload


def unavailable(reason_code: str, **data: Any) -> dict[str, Any]:
    return envelope(False, STATUS_UNAVAILABLE, data, reason_code)


def rejected(reason_code: str, **data: Any) -> dict[str, Any]:
    return envelope(False, STATUS_REJECTED, data, reason_code)


def today_moment(value: Any) -> Optional[datetime]:
    """把一条记录里的时间戳读成 ``+08:00`` 的时刻；读不懂就是 ``None``，从不猜。

    两个 owner 的写法不一样：Activity 记录是 ISO 字符串，作品版本是 epoch 秒。两种都认，
    无时区的 ISO 按 UTC 解释（那是存储方的约定），最后统一换算到她的日界。
    """

    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).astimezone(TODAY_TZ)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(TODAY_TZ)


def today_iso(value: Any) -> Optional[str]:
    moment = today_moment(value)
    return moment.isoformat(timespec="seconds") if moment is not None else None


def _int_or_none(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return int(value)


def today_business_day(state_root: Path | str) -> dict[str, Any]:
    """今天这个「业务日」由什么时钟决定——答案自带来源，读不到时钟不静默混钟。

    业务时钟（``fastday/fastday_clock.json``）是压缩日里唯一的时间权威；兼容嵌套
    ``<day_root>/one-day/life/state`` 与顶层 ``<day_root>/fastday/fastday_clock.json``。
    它不存在或损坏时，才退回系统时钟，并保留具名原因。两种情况都不写任何文件。
    """

    p = Path(str(state_root))
    candidates = [p / BUSINESS_CLOCK_DIRNAME / BUSINESS_CLOCK_FILENAME]
    if p.name == "state" and p.parent.name == "life" and p.parent.parent.name == "one-day":
        candidates.append(p.parents[2] / BUSINESS_CLOCK_DIRNAME / BUSINESS_CLOCK_FILENAME)
    reason: Optional[str] = None
    payload: Any = None
    for idx, path in enumerate(candidates):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            reason = None
            break
        except FileNotFoundError:
            if idx == len(candidates) - 1 and reason is None:
                reason = "BUSINESS_CLOCK_ABSENT"
        except (OSError, ValueError):
            reason = "BUSINESS_CLOCK_UNREADABLE"
            break
    if isinstance(payload, Mapping):
        moment = (today_moment(payload.get("logical_checkpoint"))
                  or today_moment(payload.get("logical_anchor"))
                  or today_moment(payload.get("logical_at"))
                  or today_moment(payload.get("now_iso"))
                  or today_moment(payload.get("current_iso"))
                  or today_moment(payload.get("now")))
        if moment is not None:
            return {"source": "business_clock", "reason_code": None,
                    "logical_at": moment.isoformat(timespec="seconds"),
                    "day": moment.date().isoformat(),
                    "run_id": payload.get("run_id"), "domain_id": payload.get("domain_id")}
        reason = "BUSINESS_CLOCK_CORRUPT"
    moment = datetime.now(tz=TODAY_TZ).replace(microsecond=0)
    return {"source": "system_clock", "reason_code": reason,
            "logical_at": moment.isoformat(timespec="seconds"),
            "day": moment.date().isoformat(), "run_id": None, "domain_id": None}


#: The room blocks C may publish on its own World snapshot.  P never derives one of them: a block
#: that is absent is ``NOT_REPORTED``, and a count is only ever the length of a list C published.
ROOM_BLOCK_NAMES: tuple[str, ...] = ("deliveries", "owned_furniture", "surfaces")
LIGHT_BLOCK_NAME = "light"


def block_count(block: Mapping[str, Any]) -> Optional[int]:
    """How many rows C published in one room block, or ``None`` when it published no list.

    The shape belongs to C, so no shape is invented here: a block that carries no list is simply
    not counted, and the phone can never show "nothing on the way" for a block the owner never
    spoke about.
    """

    for key in ("items", "entries", "rows", "records"):
        value = block.get(key)
        if isinstance(value, list):
            return len(value)
    lists = [value for value in block.values() if isinstance(value, list)]
    if len(lists) == 1:
        return len(lists[0])
    return None


def _room_entry(block: Mapping[str, Any]) -> dict[str, Any]:
    entry: dict[str, Any] = {"status": block.get("status")}
    if block.get("reason_code"):
        entry["reason_code"] = str(block["reason_code"])
    count = block_count(block)
    if count is not None:
        entry["count"] = count
    return entry


def memo_reading_anchor(row: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """A memo's optional reading anchor, exactly as the private owner wrote it.

    The anchor is ``{source_id, source_revision, page}`` (``rc1-reading-contract.md`` §6): the ids
    are Supply's own protocol strings and P never rewrites, prefixes or truncates them.  A memo
    that predates the workbench has no anchor at all, so this returns ``None`` and the note keeps
    its old shape.  A malformed anchor is *not* repaired and *not* raised on: a GET must not fail
    because one row is odd, and inventing ids is worse than omitting them.
    """

    anchor = row.get("reading_anchor")
    if not isinstance(anchor, Mapping):
        return None
    source_id = anchor.get("source_id")
    source_revision = anchor.get("source_revision")
    page = anchor.get("page")
    if not isinstance(source_id, str) or not source_id.strip():
        return None
    if not isinstance(source_revision, str) or not source_revision.strip():
        return None
    if isinstance(page, bool) or not isinstance(page, int) or int(page) < 1:
        return None
    return {"source_id": source_id, "source_revision": source_revision, "page": int(page)}


# ---------------------------------------------------------------------------------------------
# instance
# ---------------------------------------------------------------------------------------------


def canonical_life_subject(state_root: Path | str) -> str:
    """The Life subject this state root belongs to, or ``""`` when it is not readable.

    Callers use it to bind the operator-stop credential to the same scope the stop route
    really acts on.  A missing/broken file yields an empty string -- never a guess.
    """

    path = Path(state_root) / "canonical_life_state.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("subject_id") or "").strip()


def instance_path(root: Path | str) -> Path:
    return Path(root) / INSTANCE_FILENAME


def load_instance(root: Path | str) -> dict[str, Any]:
    """Read one phone instance file.  Anything else on disk is not guessed at."""

    path = instance_path(root)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PhoneServiceError("INSTANCE_UNREADABLE:%s" % exc.__class__.__name__) from exc
    except ValueError as exc:
        raise PhoneServiceError("INSTANCE_NOT_JSON") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_INSTANCE:
        raise PhoneServiceError("INSTANCE_SCHEMA_UNKNOWN")
    if raw.get("kind") != KIND_INSTANCE:
        raise PhoneServiceError("INSTANCE_KIND_UNKNOWN")
    for key in ("root", "world_home", "supply_socket", "private_items_artifact_id",
                "artifact_grant_id", "workspace_id", "subject"):
        if not str(raw.get(key) or "").strip():
            raise PhoneServiceError("INSTANCE_FIELD_MISSING:%s" % key)
    if Path(str(raw["root"])).resolve() != Path(root).resolve():
        raise PhoneServiceError("INSTANCE_ROOT_MISMATCH")
    return raw


def write_instance(root: Path | str, payload: Mapping[str, Any]) -> Path:
    path = instance_path(root)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, indent=2),
                   encoding="utf-8")
    os.replace(tmp, path)
    return path


# ---------------------------------------------------------------------------------------------
# narrow transports (read-only probes; never start anything)
# ---------------------------------------------------------------------------------------------


def _one_line(socket_path: Path | str, request: Mapping[str, Any], *, timeout: float = 5.0
              ) -> dict[str, Any]:
    body = json.dumps(dict(request), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(float(timeout))
        client.connect(str(socket_path))
        client.sendall(body + b"\n")
        line = client.makefile("rb").readline(1 << 20)
    if not line:
        raise PhoneUnavailable("EMPTY_OWNER_ANSWER")
    answer = json.loads(line.decode("utf-8"))
    if not isinstance(answer, dict):
        raise PhoneUnavailable("MALFORMED_OWNER_ANSWER")
    return answer


def supply_ready(socket_path: Path | str, *, timeout: float = 5.0) -> bool:
    """A functional read probe.  Missing socket is ``False``, never an exception."""

    if not Path(str(socket_path)).exists():
        return False
    try:
        answer = _one_line(socket_path, {"op": "health"}, timeout=timeout)
    except (OSError, ValueError, PhoneUnavailable):
        return False
    return str(answer.get("status")) == "OK"


def gateway_ready(socket_path: Path | str, *, timeout: float = 5.0) -> bool:
    if not Path(str(socket_path)).exists():
        return False
    try:
        answer = _one_line(socket_path, {"command": "get_snapshot", "argument": None},
                           timeout=timeout)
    except (OSError, ValueError, PhoneUnavailable):
        return False
    return bool(answer.get("ok"))


def wait_for_socket(socket_path: Path | str, *, timeout: float = 30.0, probe: str = "supply"
                    ) -> bool:
    """Wait for a child service's socket.  Bounded; never starts the service itself."""

    check = supply_ready if probe == "supply" else gateway_ready
    deadline = time.monotonic() + float(timeout)
    while time.monotonic() < deadline:
        if check(socket_path):
            return True
        time.sleep(0.05)
    return False


# ---------------------------------------------------------------------------------------------
# the service
# ---------------------------------------------------------------------------------------------


class PhoneService:
    """One phone instance's read/write surface.  No background threads, no caching of truth."""

    def __init__(
        self,
        instance: Mapping[str, Any],
        *,
        environment: Optional[Mapping[str, str]] = None,
        feature_overrides: Optional[Mapping[str, str]] = None,
        clock: Optional[Any] = None,
        #: Explicit business clock for the World/economy/reading facades (compressed-day
        #: demos).  ``None`` keeps each facade's own wall-clock default; when given it must
        #: return a timezone-aware ``datetime``.  This is the same moment the World child was
        #: started with, so the phone and the owner cannot disagree about opening hours.
        world_clock: Optional[Any] = None,
        #: The subject C's own World/economy facades verify.  It defaults to the Supply subject
        #: (production has one identity); an isolated fixture whose World owner was initialized
        #: under its own subject can name it here without changing the private-items subject.
        world_subject: Optional[str] = None,
        shopping: Optional[Any] = None,
        world: Optional[Any] = None,
        #: Injected reading workbench (B's ``ReadingWorkspaceService``); ``None`` = build it here
        #: from this instance's own port + reading artifact id.
        reading: Optional[Any] = None,
        sharing: Optional[Any] = None,
        contact: Optional[Any] = None,
        #: Injected participant registry (F-140, tests / an outer composition).  ``None`` =
        #: build the instance's own registry over its Life state root and subject.  It is a
        #: **read-only** surface here: creation, endpoint binding and event linking are local
        #: operator CLI acts, never phone HTTP or model actions.
        participants: Optional[Any] = None,
        #: Injected shared-practice owner (A's ``shared_practice_service``).  ``None`` = build it
        #: here from this instance's own bindings (A's factory, ``actor="user"``).
        shared_practice: Optional[Any] = None,
        #: The artifact port this instance already speaks (tests / an outer composition that
        #: opened Supply once).  ``None`` = build it from the instance's own supply socket.
        #: This is a **port**, never a substitute service: A's shared-practice owner is still
        #: built here, by A's own factory, with ``actor="user"``.
        project_port: Optional[Any] = None,
        #: 2026-10-28: the live Life runtime's external-stop service when phone and resident
        #: share one process (the demo).  ``None`` is the normal production case: the phone
        #: mails the request to the resident's own Life state root instead of writing the
        #: Activity owner itself -- it holds no lease and must never pretend it did.
        activity_stop: Optional[Any] = None,
        #: 2026-10-28: when the caller already knows the Life state root (a same-process
        #: composition, an isolated demo), bind it explicitly instead of letting the phone
        #: infer a nested layout it does not have.  ``None`` keeps the existing inference.
        life_state_root: Optional[Path | str] = None,
    ) -> None:
        self.instance = dict(instance)
        self.root = Path(str(self.instance["root"]))
        self.subject = str(self.instance["subject"])
        #: World/economy owner identity.  Only the C facades use it; the Supply/private-items
        #: subject above stays exactly as given.
        self.world_subject = str(world_subject or self.subject)
        self.world_home = Path(str(self.instance["world_home"]))
        self.supply_socket = str(self.instance["supply_socket"])
        self.private_items_artifact_id = str(self.instance["private_items_artifact_id"])
        self.artifact_grant_id = str(self.instance["artifact_grant_id"])
        self.workspace_id = str(self.instance["workspace_id"])
        # The reading workbench lives in its own managed artifact, bound once by ``prepare-demo``
        # (``demo.py:_instance_payload``).  A root that never got one stays honestly unbound:
        # nothing is created here, and no demo text is invented to fill the gap.
        self.reading_artifact_id = str(self.instance.get("reading_artifact_id") or "")
        self.world_body_socket = (str(self.instance.get("world_body_socket") or "")
                                  or str(self.world_home / "run" / "gateway.sock"))
        features = {str(k): str(v) for k, v in (self.instance.get("features") or {}).items()}
        if feature_overrides is not None:
            # One ``serve`` switch (``scripts/phone.py serve --with-room-living``) must beat the
            # switches an old root recorded -- the World/Body child it starts gets the same
            # values.  Memory only: the instance file keeps its recorded features.
            features.update({str(k): str(v) for k, v in feature_overrides.items()})
        base = dict(os.environ if environment is None else environment)
        base.update(features)
        self.environment = base
        self._clock = clock or (lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        self._world_clock = world_clock
        self._shopping = shopping
        self._world = world
        self._reading = reading
        #: Injected share facade (tests / an outer composition).  ``None`` = build it here from
        #: this instance's own port, subject, workspace and grant (never from process-wide env).
        self._sharing = sharing
        #: Injected visit facade (tests / an outer composition).  ``None`` = build it here from
        #: this instance's own Life state root, subject and home (never from a client value).
        self._contact = contact
        #: F-140 participant identity (A's ``ParticipantRegistry``).  Identity only: no message
        #: body, no grant, no capability.  Read-only from the phone's side.
        self._participants = participants
        #: Injected shared-practice owner (A's ``shared_practice_service``).  ``None`` = A has not
        #: published it into this composition: the surface then answers a named
        #: ``SHARED_PRACTICE_UNBOUND`` instead of inventing a second work/chat store.
        self._shared_practice_owner = shared_practice
        self._shared_practice: Any = None
        self._project_port = project_port
        self._shared_practice_error = ""
        #: 2026-10-28 external stop.  When present it is the *real* runtime service in this
        #: process; otherwise stop requests go through the Life-state-root mailbox.
        self._activity_stop = activity_stop
        self._life_state_root_override = (
            Path(life_state_root).expanduser() if life_state_root is not None else None)

    def _stop_state_root(self) -> Optional[Path]:
        """The Life state root this stop route reads: explicit first, inferred otherwise."""

        if self._life_state_root_override is not None:
            return (self._life_state_root_override
                    if self._life_state_root_override.is_dir() else None)
        return self._contact_state_root()

    # -- binding -------------------------------------------------------------------------

    @property
    def profile_home(self) -> str:
        return str(self.instance.get("profile_home") or self.root)

    def read_only(self) -> bool:
        return False

    def _journal_path(self) -> Path:
        return self.root / "phone" / "operations.jsonl"

    def record_operation(self, *, actor: str, action: str, operation_key: str,
                         result: Mapping[str, Any]) -> dict[str, Any]:
        """Append one attribution row.  Narrow, append-only, never a second source of truth."""

        row = {
            "at": self._clock(),
            "actor": actor if actor in ACTORS else ACTOR_USER,
            "action": str(action),
            "operation_key": str(operation_key),
            "status": str(result.get("status")),
            "reason_code": result.get("reason_code") or None,
            "ok": bool(result.get("ok")),
        }
        path = self._journal_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError:
            # Attribution is evidence, not truth: failing to write it must not fake a result.
            return {"recorded": False}
        return {"recorded": True}

    # -- Supply (notes / references / reading) --------------------------------------------

    def _port(self):
        """The artifact port this instance already speaks.

        ``project_port`` is the port **object** an outer composition already opened over the same
        owner (tests, a POSIX Supply, a long-lived parent).  It is never a fake service: whatever
        reads through it still goes to the same ``ManagedArtifact`` store.  When it is absent the
        port is built from this instance's own supply socket -- never from a request field.
        """

        if self._project_port is not None:
            return self._project_port
        from life_artifact_port import LifeArtifactPort

        if not Path(self.supply_socket).exists():
            raise PhoneUnavailable("SUPPLY_UNAVAILABLE")
        return LifeArtifactPort(self.supply_socket, self.subject)

    def _items(self):
        from private_daily_items import PrivateDailyItems

        return PrivateDailyItems(self._port(), self.private_items_artifact_id,
                                 workspace_id=self.workspace_id,
                                 grant_id=self.artifact_grant_id)

    def _head(self) -> tuple[Optional[str], Optional[int]]:
        view = self._port().read_artifact(self.private_items_artifact_id)
        head = view.get("head") or {}
        return head.get("version_id"), head.get("version_number")

    def notes(self) -> dict[str, Any]:
        """The local owner opening her own notes: the only read that carries memo bodies.

        A memo written from the reading screen also carries its optional page anchor
        (``{source_id, source_revision, page}``).  The ids stay C's protocol strings verbatim, and
        a note without an anchor keeps exactly the old shape -- the anchor is metadata, the body
        is the only private text here.
        """

        from private_daily_items import ITEM_MEMO

        try:
            items = self._items()
            head, version_number = self._head()
            rows = []
            for row in items.items(ITEM_MEMO):
                item_id = str(row.get("item_id"))
                body = ""
                try:
                    body = items.full_text(item_id)
                except Exception:
                    body = ""
                note: dict[str, Any] = {
                    "id": item_id,
                    "title": str(row.get("title") or ""),
                    "body": body,
                    "revision": row.get("revisions"),
                    "revisions": row.get("revisions"),
                    "created_at": row.get("created_at"),
                    "updated_at": row.get("updated_at"),
                    "observed_object_ref": row.get("observed_object_ref"),
                }
                anchor = memo_reading_anchor(row)
                if anchor is not None:
                    note["reading_anchor"] = anchor
                rows.append(note)
        except PhoneUnavailable as exc:
            return unavailable(exc.reason_code)
        except Exception as exc:  # owner refused / unavailable / version mismatch
            return _supply_failure(exc)
        return {"ok": True, "status": STATUS_OK, "head": head, "version_number": version_number,
                "artifact_id": self.private_items_artifact_id, "items": rows,
                "private_text_included": True}

    def create_note(self, *, title: str, body: str, operation_key: str,
                    expected_head: Optional[str] = None,
                    note_id: Optional[str] = None) -> dict[str, Any]:
        from private_daily_items import ITEM_MEMO

        try:
            items = self._items()
            head, _number = self._head()
            if expected_head and head and str(expected_head) != str(head):
                return rejected("NOTE_HEAD_CONFLICT", head=head,
                                expected_head=str(expected_head))
            # No title fallback: a create without an explicit note_id is always a NEW memo, even
            # if another memo already has the same title.  Guessing "the one with the same title"
            # would silently overwrite a note the operator never named.
            target = str(note_id or "").strip()
            if target:
                try:
                    stored = items.item(target)
                except Exception:
                    return rejected("NOTE_NOT_FOUND", note_id=target)
                if str(stored.get("item_type") or "") != ITEM_MEMO:
                    return rejected("NOTE_ID_NOT_A_MEMO", note_id=target)
                current_title = str(stored.get("title") or "")
                if current_title != title:
                    # The owner contract (PrivateDailyItems.revise_memo) only rewrites the body.
                    # Pretending a rename happened would be a silent lie, so refuse instead.
                    return rejected("NOTE_TITLE_CHANGE_NOT_SUPPORTED", note_id=target,
                                    current_title=current_title, requested_title=title)
                outcome = items.revise_memo(target, body=body, operation_key=operation_key)
                item_id = target
            else:
                # One intent (one operation_key) = one memo id: replaying the same key cannot
                # mint a second note, while a *different* key with the same title/body really is
                # a second, independent note.
                memo_id = "memo:" + hashlib.sha256(
                    ("phone|note|" + str(operation_key)).encode("utf-8")).hexdigest()[:16]
                outcome = items.add_memo(title=title, body=body, memo_id=memo_id,
                                         operation_key=operation_key)
                item_id = outcome.item_id or memo_id
        except PhoneUnavailable as exc:
            return unavailable(exc.reason_code)
        except Exception as exc:
            return _supply_failure(exc)
        fresh_head, version_number = self._head()
        status = STATUS_OK if outcome.applied else STATUS_ALREADY
        result = {"ok": True, "status": status,
                  "data": {"id": item_id, "operation_key": operation_key, "head": fresh_head,
                           "version_number": version_number,
                           "owner_status": outcome.owner_status,
                           "effect_state": outcome.effect_state}}
        if outcome.detail:
            result["data"]["detail"] = outcome.detail
        return result

    # -- diary (her own words; metadata by default, text only explicitly) -----------------

    def diary_list(self) -> dict[str, Any]:
        """The diary index: write times, lengths and a count.  **No body text, no preview.**

        The operator's ordinary read is this list.  A diary entry is a different item type from
        a memo, so the notes screen (which asks for ``ITEM_MEMO`` only) cannot carry it.
        """
        from private_daily_items import ITEM_DIARY

        try:
            items = self._items()
            rows = list(enumerate(items.items(ITEM_DIARY)))
        except PhoneUnavailable as exc:
            return unavailable(exc.reason_code)
        except Exception as exc:
            return _supply_failure(exc)
        # Newest first.  The timestamp is the real write time; the insertion index is only a
        # tie-break for two entries written within the same clock tick.
        rows.sort(key=lambda pair: (str(pair[1].get("created_at") or ""), pair[0]), reverse=True)
        entries: list[dict[str, Any]] = []
        dates: list[str] = []
        for _index, row in rows:
            created_at = str(row.get("created_at") or "")
            entry = {
                "id": str(row.get("item_id") or ""),
                "created_at": created_at or None,
                "chars": int(row.get("chars") or 0),
                "revisions": int(row.get("revisions") or 0),
                "has_text": bool(row.get("has_text")),
            }
            entries.append(entry)
            day = created_at[:10]
            if day and day not in dates:
                dates.append(day)
        return {
            "ok": True,
            "status": STATUS_OK,
            "count": len(entries),
            "entries": entries,
            "dates": dates,
            "latest_at": entries[0]["created_at"] if entries else None,
            "artifact_id": self.private_items_artifact_id,
            "private_text_included": False,
        }

    def diary_read(self, entry_id: str, *, include_text: bool) -> dict[str, Any]:
        """One diary entry.  The body is returned only when the caller asked for text.

        ``include_text=False`` is the operator's default: the entry's id, time and length are
        real facts, while the body is withheld by name (not shown as an empty quote).  Only the
        subject's own tool path asks for text, and the HTTP layer decides that from the existing
        actor declaration -- never from a request-body ``role`` field.
        """
        from private_daily_items import ITEM_DIARY, PrivateItemsContractRefused

        try:
            items = self._items()
            try:
                item = items.item(entry_id)
            except PrivateItemsContractRefused:
                return envelope(False, STATUS_NOT_FOUND, {}, "DIARY_ENTRY_NOT_FOUND")
            if str(item.get("item_type") or "") != ITEM_DIARY:
                # The diary id space is not a second read path for memos or reading annotations.
                return envelope(False, STATUS_NOT_FOUND, {}, "DIARY_ENTRY_NOT_FOUND")
            meta = {
                "id": str(item.get("item_id") or ""),
                "created_at": item.get("created_at"),
                "chars": int(item.get("chars") or 0),
                "revisions": int(item.get("revisions") or 0),
            }
            if not include_text:
                return {
                    "ok": True,
                    "status": STATUS_OK,
                    "entry": meta,
                    "body_withheld": True,
                    "reason_code": "DIARY_BODY_WITHHELD",
                    "private_text_included": False,
                }
            body = items.full_text(entry_id)
        except PhoneUnavailable as exc:
            return unavailable(exc.reason_code)
        except Exception as exc:
            return _supply_failure(exc)
        entry = dict(meta)
        entry["body"] = body
        return {
            "ok": True,
            "status": STATUS_OK,
            "entry": entry,
            "body_withheld": False,
            "private_text_included": True,
        }

    def diary_write(self, *, body: str, operation_key: str) -> dict[str, Any]:
        """Write one diary entry as her own text.  No model call, no summary, no Memory write.

        One operation key = one entry id, so a retry with the original key lands on the same
        entry instead of writing the same day twice.  Two different keys stay two entries even
        when the words are identical -- writing the same sentence on another day is not a retry.
        """
        from private_daily_items import PrivateItemsContractRefused

        try:
            items = self._items()
            entry_id = "diary:" + hashlib.sha256(
                ("phone|diary|" + str(operation_key)).encode("utf-8")).hexdigest()[:16]
            outcome = items.add_diary(body=body, diary_id=entry_id,
                                      operation_key=operation_key, now=self._clock())
            item = items.item(entry_id)
        except PrivateItemsContractRefused as exc:
            # 同一个 key 换了文字，或正文越界：这是明确拒绝，不是"已保存"。
            code = str(exc).split(":", 1)[0] or "DIARY_WRITE_REFUSED"
            return rejected(code.upper())
        except PhoneUnavailable as exc:
            return unavailable(exc.reason_code)
        except Exception as exc:
            return _supply_failure(exc)
        fresh_head, version_number = self._head()
        status = STATUS_OK if outcome.applied else STATUS_ALREADY
        result = {
            "ok": True,
            "status": status,
            "data": {
                "id": entry_id,
                "created_at": item.get("created_at"),
                "chars": int(item.get("chars") or 0),
                "head": fresh_head,
                "version_number": version_number,
                "owner_status": outcome.owner_status,
                "effect_state": outcome.effect_state,
            },
        }
        if outcome.detail:
            result["data"]["detail"] = outcome.detail
        return result

    def references(self) -> dict[str, Any]:
        from private_daily_items import ITEM_SAVED_REFERENCE

        try:
            items = self._items()
            rows = [{
                "id": str(row.get("item_id")),
                "label": str(row.get("label") or ""),
                "source_id": str(row.get("source_id") or ""),
                "source_revision": str(row.get("source_revision") or ""),
                "read": bool(row.get("read")),
                "content_fetched": bool(row.get("content_fetched")),
                "saved_at": row.get("saved_at"),
            } for row in items.items(ITEM_SAVED_REFERENCE)]
        except PhoneUnavailable as exc:
            return unavailable(exc.reason_code)
        except Exception as exc:
            return _supply_failure(exc)
        return {"ok": True, "status": STATUS_OK, "items": rows,
                "artifact_id": self.private_items_artifact_id}

    def save_reference(self, *, source_id: str, source_revision: str, label: str,
                       operation_key: str) -> dict[str, Any]:
        try:
            items = self._items()
            outcome = items.save_reference(
                source_id=source_id, source_revision=source_revision, label=label,
                # The receipt records *this operator save command*, never fetched content.
                receipt_ref="operator:phone-reference:%s" % operation_key,
                operation_key=operation_key)
        except PhoneUnavailable as exc:
            return unavailable(exc.reason_code)
        except Exception as exc:
            return _supply_failure(exc)
        status = STATUS_OK if outcome.applied else STATUS_ALREADY
        return {"ok": True, "status": status,
                "data": {"id": outcome.item_id, "operation_key": operation_key,
                         "effect_state": outcome.effect_state,
                         "read": False, "content_fetched": False}}

    def reading(self) -> dict[str, Any]:
        from private_daily_items import ITEM_READING_POSITION, ITEM_SOURCE_ACQUISITION

        try:
            items = self._items()
            rows: list[dict[str, Any]] = []
            for row in items.items(ITEM_SOURCE_ACQUISITION):
                rows.append({
                    "id": str(row.get("item_id")), "kind": "acquisition",
                    "source_id": str(row.get("source_id") or ""),
                    "source_revision": str(row.get("source_revision") or ""),
                    "label": str(row.get("source_id") or ""),
                    "position": None, "understanding": "UNKNOWN",
                    "available": bool(row.get("available", True)),
                    "needs_reread": False, "at": row.get("acquired_at"),
                })
            for row in items.items(ITEM_READING_POSITION):
                rows.append({
                    "id": str(row.get("item_id")), "kind": "position",
                    "source_id": str(row.get("source_id") or ""),
                    "source_revision": str(row.get("source_revision") or ""),
                    "label": str(row.get("source_id") or ""),
                    "position": row.get("position"),
                    "acquisition_id": row.get("acquisition_id"),
                    "understanding": "UNKNOWN",
                    "needs_read": None,
                    "needs_reread": bool(row.get("needs_reread")),
                    "needs_reread_reason": row.get("needs_reread_reason") or "",
                    "at": row.get("set_at"),
                })
        except PhoneUnavailable as exc:
            return unavailable(exc.reason_code)
        except Exception as exc:
            return _supply_failure(exc)
        # This legacy metadata list never carries a body: it may only say whether the workbench
        # has a bound material.  The text itself lives behind ``GET .../reading/material``.
        return {"ok": True, "status": STATUS_OK, "understanding": "UNKNOWN", "items": rows,
                "material_available": bool(self.reading_artifact_id),
                "source_id": self.reading_source_id()}

    # -- reading workbench (C's PhoneReading owns every reading fact) ----------------------

    def reading_source_id(self) -> Optional[str]:
        """C's own ``artifact:<id>`` rule, or ``None`` when this root has no material bound."""

        if not self.reading_artifact_id:
            return None
        return "artifact:" + self.reading_artifact_id

    def _reading_instance(self):
        """C's ``PhoneReading`` facade for this same instance; nothing is created here.

        The facade is given C's own port object: it already knows the owner identity, so P never
        re-derives one, and a missing socket stays ``SUPPLY_UNAVAILABLE`` (503) instead of being
        papered over with an empty material.
        """

        if not self.reading_artifact_id:
            raise PhoneUnavailable("READING_MATERIAL_UNAVAILABLE")
        if self._reading is not None:
            return self._reading
        try:
            import phone_reading as module
        except Exception as exc:  # C's write set; a broken import is not an empty success
            raise PhoneUnavailable("LIFE_PHONE_READING_UNAVAILABLE",
                                   type(exc).__name__) from exc
        factory = getattr(module, "PhoneReading", None)
        if factory is None:
            raise PhoneUnavailable("LIFE_PHONE_READING_UNAVAILABLE", "NO_FACADE")
        reading_clock = None
        if self._world_clock is not None:
            reading_clock = lambda: self._world_clock().isoformat()
        self._reading = factory(
            self._port(),
            reading_artifact_id=self.reading_artifact_id,
            private_items_artifact_id=self.private_items_artifact_id,
            workspace_id=self.workspace_id,
            grant_id=self.artifact_grant_id,
            clock=reading_clock,
        )
        return self._reading

    def _reading_call(self, method: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Forward one reading call.  C's envelope (status, reason_code, data) passes verbatim."""

        try:
            facade = self._reading_instance()
            handler = getattr(facade, method, None)
            if handler is None:
                # C's facade is the only owner of these facts.  A method it does not implement
                # yet is "not implemented", never an empty page and never a guessed bookmark.
                return unavailable("PHONE_FACADE_UNIMPLEMENTED", method=method)
            answer = handler(*args, **kwargs)
        except PhoneUnavailable as exc:
            # An unbound root answers in C's own vocabulary and shape: "no material" is
            # ``READING_MATERIAL_UNAVAILABLE`` with the honest empty material, never page 1.
            if exc.reason_code == "READING_MATERIAL_UNAVAILABLE":
                return unavailable(exc.reason_code, available=False, content_available=False,
                                   artifact_id=None, private_head=None, bookmark=None,
                                   annotations=[], understanding="UNKNOWN")
            return unavailable(exc.reason_code)
        except Exception as exc:
            if type(exc).__name__ == "PhoneReadingError":
                # C says a misused facade is a 500, not a pretend-503: let it reach the handler
                # that answers PHONE_INTERNAL_ERROR instead of inventing an owner state.
                raise
            return unavailable("PHONE_READING_ADAPTER_ERROR")
        if not isinstance(answer, dict):
            return unavailable("PHONE_READING_ADAPTER_ERROR")
        # The facade says ok/status itself; a refusal is never upgraded to a success here.
        return answer

    def reading_material(self, page: Optional[int] = None) -> dict[str, Any]:
        """``PhoneReading.material(page=None)``: the real text of one page, read-only."""

        return self._reading_call("material", page)

    def reading_import(self, content: str, expected_revision: str,
                       operation_key: str) -> dict[str, Any]:
        """``PhoneReading.replace_material``: replace the *bound* artifact's text, CAS on head."""

        return self._reading_call("replace_material", content, expected_revision, operation_key)

    def reading_position(self, page: int, source_revision: str, expected_head: str,
                         operation_key: str) -> dict[str, Any]:
        """``PhoneReading.save_position``: one explicit operator stop, owner-committed."""

        return self._reading_call("save_position", page, source_revision, expected_head,
                                  operation_key)

    def reading_annotate(self, kind: str, page: int, source_revision: str, expected_head: str,
                         operation_key: str, *, title: Optional[str] = None,
                         body: Optional[str] = None,
                         note: Optional[str] = None) -> dict[str, Any]:
        """``PhoneReading.annotate``: a note or an "unclear" mark on a page already acquired.

        P does not pretend the two kinds are the same thing, and it never turns an annotation
        into understanding: ``understanding`` stays C's word (always ``UNKNOWN``).
        """

        return self._reading_call("annotate", kind, page, source_revision, expected_head,
                                  operation_key, title=title, body=body, note=note)

    def reading_operation(self, operation_key: str) -> dict[str, Any]:
        """Read-only reconciliation of one reading operation key (never a new key)."""

        return self._reading_call("operation", str(operation_key))

    # -- World shopping facade (W owns every economic fact) ------------------------------

    def _shopping_instance(self):
        if self._shopping is not None:
            return self._shopping
        try:
            import world_phone_shopping as wps
        except Exception as exc:  # the facade is W's write set; a broken import is not a success
            raise PhoneUnavailable("WORLD_PHONE_SHOPPING_UNAVAILABLE",
                                   type(exc).__name__) from exc
        factory = getattr(wps, "PhoneShopping", None)
        if factory is None:
            raise PhoneUnavailable("WORLD_PHONE_SHOPPING_UNAVAILABLE", "NO_FACADE")
        self._shopping = factory(
            self.world_home,
            self.environment,
            local_owner_uid=os.getuid(),
            subject=self.world_subject,
            gateway_socket=self.world_body_socket,
            clock=self._world_clock,
        )
        return self._shopping

    def _facade_call(self, method: str, *args: Any) -> dict[str, Any]:
        try:
            shopping = self._shopping_instance()
            handler = getattr(shopping, method, None)
            if handler is None:
                # C's facade is the only owner of this fact.  A facade that does not implement the
                # method yet is "not implemented", never an empty success and never a guess.
                return unavailable("PHONE_FACADE_UNIMPLEMENTED", method=method)
            answer = handler(*args)
        except PhoneUnavailable as exc:
            return unavailable(exc.reason_code)
        except Exception:
            return unavailable("PHONE_WORLD_ADAPTER_ERROR")
        if not isinstance(answer, dict):
            return unavailable("PHONE_WORLD_ADAPTER_ERROR")
        # The facade says ok/status itself; never upgrade a refusal.
        return answer

    def wallet(self) -> dict[str, Any]:
        return self._facade_call("wallet")

    def shop(self) -> dict[str, Any]:
        return self._facade_call("shop")

    def store(self) -> dict[str, Any]:
        """``PhoneShopping.store()``: the physical shop's shelf and opening state, read-only.

        C owns every fact here (channel, availability, catalogue).  A GET never delivers,
        materializes, migrates or pays: it is the same pure read the owner exposes.
        """

        return self._facade_call("store")

    def store_purchase(self, catalog_item_id: str, operation_key: str) -> dict[str, Any]:
        """``PhoneShopping.store_purchase()``: one counter purchase under one operation key.

        P does not price it, does not decide delivery and does not mark anything as arrived: the
        key is the owner's idempotency handle and a success is *not* an arrival.
        """

        return self._facade_call("store_purchase", catalog_item_id, operation_key)

    def inventory(self) -> dict[str, Any]:
        return self._facade_call("inventory")

    def purchase(self, catalog_item_id: str, operation_key: str) -> dict[str, Any]:
        return self._facade_call("purchase", catalog_item_id, operation_key)

    def use(self, item_instance_id: str, operation_key: str) -> dict[str, Any]:
        return self._facade_call("use", item_instance_id, operation_key)

    def operation(self, operation_key: str) -> dict[str, Any]:
        return self._facade_call("operation", operation_key)

    # -- World / body facade (C owns every world and body fact) ---------------------------

    def _world_instance(self):
        """C's ``PhoneWorld`` for this same instance; nothing is started or guessed here."""
        if self._world is not None:
            return self._world
        try:
            import world_phone_world as wpw
        except Exception as exc:  # the facade is not installed: unavailable, never a fake world
            raise PhoneUnavailable("WORLD_PHONE_WORLD_UNAVAILABLE",
                                   type(exc).__name__) from exc
        factory = getattr(wpw, "PhoneWorld", None)
        if factory is None:
            raise PhoneUnavailable("WORLD_PHONE_WORLD_UNAVAILABLE", "NO_FACADE")
        self._world = factory(
            self.world_home,
            self.environment,
            local_owner_uid=os.getuid(),
            subject=self.world_subject,
            gateway_socket=self.world_body_socket,
            clock=self._world_clock,
        )
        return self._world

    def _world_call(self, method: str, *args: Any) -> dict[str, Any]:
        """Pass C's envelope through verbatim.  P reshapes no world/body field."""
        try:
            facade = self._world_instance()
            answer = getattr(facade, method)(*args)
        except PhoneUnavailable as exc:
            return unavailable(exc.reason_code)
        except Exception:
            return unavailable("PHONE_WORLD_ADAPTER_ERROR")
        if not isinstance(answer, dict):
            return unavailable("PHONE_WORLD_ADAPTER_ERROR")
        return answer

    def world(self) -> dict[str, Any]:
        """``PhoneWorld.snapshot()``: read-only; a GET never writes, settles or migrates."""
        return self._world_call("snapshot")

    def world_action(self, action: str, params: Mapping[str, Any],
                     operation_key: str) -> dict[str, Any]:
        """``PhoneWorld.act()``: the only world write path (C's Resolver owns the outcome)."""
        return self._world_call("act", str(action), dict(params), str(operation_key))

    def world_operation(self, operation_key: str) -> dict[str, Any]:
        """Read-only reconciliation of one World operation key (never a new key)."""
        return self._world_call("operation", str(operation_key))

    # -- body view (C's safe projection; P computes no body value) ------------------------

    def body(self) -> dict[str, Any]:
        """The body view is C's own safe projection: ``snapshot().data.body``, verbatim.

        P computes no body value: pose / orientation / recovery / signals / rest evidence are
        passed through.  ``status`` here is C's own body status (``available|partial|
        unavailable``); only when the facade cannot answer at all does this fall back to the
        gateway's own ``get_snapshot`` (pose + location, no body projection), and a missing
        socket stays ``unavailable``, never a faked reading.
        """

        return self._body_from_world(self.world())

    def _body_from_world(self, world_result: Mapping[str, Any]) -> dict[str, Any]:
        payload = world_result.get("data") if isinstance(world_result.get("data"), Mapping) else {}
        block = payload.get("body") if isinstance(payload.get("body"), Mapping) else {}
        if block:
            status = str(block.get("status") or STATUS_UNAVAILABLE)
            data = {key: block.get(key) for key in BODY_VIEW_FIELDS if key in block}
            view: dict[str, Any] = {"ok": status == "available", "status": status,
                                    "data": data, "source": "world_phone_world.snapshot"}
            reason = block.get("reason_code") or world_result.get("reason_code")
            if reason:
                view["reason_code"] = str(reason)
            return view
        if world_result.get("ok"):
            # The facade answered but carries no body block: that is a refusal, not a reading.
            return unavailable(str(world_result.get("reason_code") or "BODY_VIEW_UNAVAILABLE"))
        return self._body_from_gateway()

    def _body_from_gateway(self) -> dict[str, Any]:
        path = Path(self.world_body_socket)
        if not path.exists():
            return unavailable("BODY_VIEW_NOT_BOUND")
        try:
            answer = _one_line(path, {"command": "get_snapshot", "argument": None},
                               timeout=2.0)
        except (OSError, ValueError, PhoneUnavailable):
            return unavailable("BODY_VIEW_UNAVAILABLE")
        if not answer.get("ok") or not isinstance(answer.get("result"), Mapping):
            return unavailable("BODY_VIEW_UNAVAILABLE")
        view = dict(answer["result"] or {})
        location = view.get("location") if isinstance(view.get("location"), Mapping) else {}
        data = {
            "pose": view.get("pose"),
            "orientation": view.get("orientation"),
            "location": {k: location.get(k) for k in ("place_id", "area_id")},
            "world_revision": view.get("world_revision"),
            "source": "world_body_gateway.get_snapshot",
        }
        if isinstance(view.get("lived_needs"), Mapping):
            data["needs"] = dict(view["lived_needs"])
        return envelope(True, STATUS_OK, data)

    def world_summary(self, result: Mapping[str, Any]) -> dict[str, Any]:
        """A summary built only from C's own blocks: nothing is filled in when it is missing."""
        payload = result.get("data") if isinstance(result.get("data"), Mapping) else {}

        def block(name: str) -> Mapping[str, Any]:
            value = payload.get(name)
            return value if isinstance(value, Mapping) else {}

        world = block("world")
        location = block("location")
        travel = block("travel")
        objects = block("objects")
        body = block("body")
        actions = block("actions")
        here = objects.get("here") if isinstance(objects.get("here"), list) else None
        held = objects.get("held") if isinstance(objects.get("held"), list) else None
        verbs: list[dict[str, Any]] = []
        for row in actions.get("verbs") or []:
            if not isinstance(row, Mapping):
                continue
            verbs.append({"verb": row.get("verb"), "available": row.get("available"),
                          "blocked_reason": row.get("blocked_reason")})
        return {
            "world_id": world.get("world_id"),
            "revision": world.get("revision"),
            "location": {"node": location.get("node"),
                         "place_id": location.get("place_id"),
                         "area_id": location.get("area_id"),
                         "name": location.get("name")},
            "travel": {"status": travel.get("status"),
                       "remaining_seconds": travel.get("remaining_seconds"),
                       "progress_band": travel.get("progress_band")},
            "objects": {"here_count": None if here is None else len(here),
                        "held_count": None if held is None else len(held)},
            "body": {"status": body.get("status"), "pose": body.get("pose")},
            "verbs": verbs,
            "blocks": {name: {"status": block(name).get("status"),
                              "reason_code": block(name).get("reason_code")}
                       for name in WORLD_SNAPSHOT_BLOCKS},
            "room": self._room_summary(payload),
            "source": "world_phone_world.snapshot",
        }

    def _room_summary(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """C's room blocks: presence, C's own status, and only real list lengths.

        ``deliveries`` / ``owned_furniture`` / ``surfaces`` are published *inside*
        ``data.objects``, and the light inside ``data.local_environment``
        (``docs/engineering/rc1-room-living-contract.md`` §3.1–§3.4).  P reads them exactly
        there: a top-level lookup would report every block as ``NOT_REPORTED`` even though
        the owner published it.  A block that is genuinely absent is still ``NOT_REPORTED``,
        and an unknown light is never "off".
        """

        objects = payload.get("objects")
        objects = objects if isinstance(objects, Mapping) else {}
        room: dict[str, Any] = {}
        for name in ROOM_BLOCK_NAMES:
            block = objects.get(name)
            room[name] = (_room_entry(block) if isinstance(block, Mapping)
                          else {"status": None, "reason_code": "NOT_REPORTED"})
        environment = payload.get("local_environment")
        light = environment.get(LIGHT_BLOCK_NAME) if isinstance(environment, Mapping) else None
        room[LIGHT_BLOCK_NAME] = (dict(light) if isinstance(light, Mapping)
                                  else {"status": None, "reason_code": "NOT_REPORTED"})
        room["source"] = "world_phone_world.snapshot"
        return room

    @staticmethod
    def _body_entry(view: Mapping[str, Any]) -> dict[str, Any]:
        """The ``/state`` body tile: C's own status verbatim, in the phone app vocabulary."""
        data = dict(view.get("data") or {})
        status = str(view.get("status") or "")
        if status not in ("available", "partial", "unavailable"):
            status = ("partial" if data.get("source") == "world_body_gateway.get_snapshot"
                      else "unavailable")
        data["body_status"] = status
        entry: dict[str, Any] = {"status": "available" if status == "available"
                                 else "unavailable", "data": data}
        reason = view.get("reason_code")
        if reason:
            entry["reason_code"] = str(reason)
        return entry


    # -- private projects (B's owner service; P forwards, it decides nothing) -------------

    def _projects_service(self):
        """B's ``PrivateProjectService`` over this instance's own port and grant.

        The reading material and the private-items document are named as **non-projects** here:
        they are a material and a private bookkeeping document, not works.  Nothing is created,
        migrated or rebound by reading this: an artifact that was there before this screen existed
        is listed exactly as the owner reports it.
        """
        from private_project_service import PrivateProjectService, default_excluded_artifact_ids

        if not self.workspace_id or not self.artifact_grant_id:
            raise PhoneUnavailable("PROJECTS_UNBOUND")
        excluded = default_excluded_artifact_ids(self.reading_artifact_id,
                                                 self.private_items_artifact_id)
        return PrivateProjectService(self._port(), self.workspace_id, self.artifact_grant_id,
                                     excluded_artifact_ids=excluded)

    def projects(self) -> dict[str, Any]:
        """``GET``: her projects, exactly as the owner lists them (no text in this view).

        Every row also carries ``artifact_type`` / ``continuable``: a *material* or a *research*
        artifact is a real artifact of hers, but it is **not** a work the project screen may
        continue -- the type comes from the first line of the artifact's content and nothing else
        (``private_project_service`` is not modified, no new exclusion is written anywhere).
        """
        try:
            service = self._projects_service()
            answer = service.list_projects()
        except PhoneUnavailable as exc:
            return unavailable(exc.reason_code)
        except Exception as exc:
            return unavailable("PROJECTS_UNAVAILABLE", error_type=type(exc).__name__)
        if not answer.get("ok"):
            return _project_failure(answer, "PROJECTS_UNAVAILABLE")
        rows = [dict(row) for row in (answer.get("projects") or []) if isinstance(row, Mapping)]
        self._annotate_project_types(service, rows)
        return {
            "ok": True,
            "status": STATUS_OK,
            "projects": rows,
            "counts": dict(answer.get("counts") or {}),
            "excluded_artifact_ids": list(answer.get("excluded_artifact_ids") or []),
            "truncated": bool(answer.get("truncated")),
            "source": answer.get("source"),
            "private_text_included": False,
        }

    def project(self, artifact_id: str) -> dict[str, Any]:
        """``GET`` one project: her text, the owner's head and the version history it can report.

        The detail row is typed exactly like the list row (``artifact_type`` / ``continuable``), so
        the screen can say "this is a material / a research, not a work to continue" instead of
        offering an edit box that would overwrite a marker.
        """
        try:
            answer = self._projects_service().read_project(artifact_id)
        except PhoneUnavailable as exc:
            return unavailable(exc.reason_code)
        except Exception as exc:
            return unavailable("PROJECTS_UNAVAILABLE", error_type=type(exc).__name__)
        if not answer.get("ok"):
            return _project_failure(answer, "PROJECT_UNAVAILABLE")
        project = dict(answer.get("project") or {})
        artifact_type = self._content_kind(project.get("content"))
        project["artifact_type"] = artifact_type
        project["continuable"] = artifact_type == "project"
        if artifact_type in ("review", "method"):
            # 项目面看得见"这是她的回顾 / 做法"，但不替实践面把正文送出来：那一份的正文与
            # 依赖它的推断只有实践面（会逐条核对证据）才能发。
            project["content"] = ""
            project["content_available"] = False
            project["practice_surface"] = "/api/phone/practice/%s/%s" % (
                "reviews" if artifact_type == "review" else "methods",
                str(answer.get("artifact_id") or artifact_id or ""))
        if artifact_type == "album":
            # 相册清单是 owner 的媒体引用，不是可续写的正文；只把这一件指回相册面。
            project["content"] = ""
            project["content_available"] = False
            project["album_surface"] = "/api/phone/album/%s" % str(
                answer.get("artifact_id") or artifact_id or "")
        return {
            "ok": True,
            "status": STATUS_OK,
            "artifact_id": answer.get("artifact_id"),
            "project": project,
            "history": dict(answer.get("history") or {}),
            "workspace_id": answer.get("workspace_id"),
            "private_text_included": (artifact_type not in ("review", "method")
                                      and not bool(project.get("derived_text_withheld"))),
            "content_withheld": bool(project.get("derived_text_withheld")),
        }

    # -- v0.4 work-revisit: her old versions, one compare, one sourced new work -----------

    def project_versions(self, artifact_id: str, *, limit: int = 20,
                         before_version_number: Optional[int] = None) -> dict[str, Any]:
        """``GET`` one page of version *metadata* for one project.  Pure read, no body."""
        try:
            answer = self._projects_service().read_project_history(
                artifact_id, limit=limit, before_version_number=before_version_number)
        except PhoneUnavailable as exc:
            return unavailable(exc.reason_code)
        except Exception as exc:
            return unavailable("PROJECTS_UNAVAILABLE", error_type=type(exc).__name__)
        if not answer.get("ok"):
            return _project_failure(answer, "PROJECT_VERSIONS_UNAVAILABLE")
        return {
            "ok": True,
            "status": STATUS_OK,
            "artifact_id": answer.get("artifact_id"),
            "history": {
                "complete": bool(answer.get("complete")),
                "reason_code": answer.get("reason_code") or "",
                "entries": [dict(row) for row in (answer.get("entries") or [])
                            if isinstance(row, Mapping)],
                "count": answer.get("count"),
                "total_versions": answer.get("total_versions"),
                "has_more": bool(answer.get("has_more")),
                "next_before_version_number": answer.get("next_before_version_number"),
                "source": answer.get("source"),
            },
            "private_text_included": False,
        }

    def project_version(self, artifact_id: str, version_id: str) -> dict[str, Any]:
        """``GET`` one named old version: her text as it was, with the owner's own hashes."""
        try:
            answer = self._projects_service().read_project_version(artifact_id, version_id)
        except PhoneUnavailable as exc:
            return unavailable(exc.reason_code)
        except Exception as exc:
            return unavailable("PROJECTS_UNAVAILABLE", error_type=type(exc).__name__)
        if not answer.get("ok"):
            return _project_failure(answer, "PROJECT_VERSION_UNAVAILABLE")
        data = {key: answer.get(key) for key in (
            "artifact_id", "version_id", "version_number", "parent_version_id", "created_at",
            "content_chars", "returned_chars", "content_sha256", "content_truncated",
            "lifecycle", "continuable")}
        data["content"] = answer.get("content") or ""
        data["provenance"] = answer.get("provenance")
        data["source_gate"] = answer.get("source_gate")
        data["derived_text_withheld"] = bool(answer.get("derived_text_withheld"))
        return {"ok": True, "status": STATUS_OK, "artifact_id": answer.get("artifact_id"),
                "version": data,
                "private_text_included": not bool(answer.get("derived_text_withheld")),
                "content_withheld": bool(answer.get("derived_text_withheld"))}

    def project_compare(self, artifact_id: str, version_id: str) -> dict[str, Any]:
        """``GET`` the bounded difference between one old version and the current head."""
        try:
            answer = self._projects_service().compare_project_versions(artifact_id, version_id)
        except PhoneUnavailable as exc:
            return unavailable(exc.reason_code)
        except Exception as exc:
            return unavailable("PROJECTS_UNAVAILABLE", error_type=type(exc).__name__)
        if not answer.get("ok"):
            return _project_failure(answer, "PROJECT_COMPARE_UNAVAILABLE")
        data = dict(answer)
        data.pop("ok", None)
        data["source"] = answer.get("source")
        return {"ok": True, "status": STATUS_OK, "artifact_id": answer.get("artifact_id"),
                "compare": data, "private_text_included": True}
    def project_provenance(self, artifact_id: str) -> dict[str, Any]:
        """``GET`` where a sourced work came from (read from its immutable first version)."""
        try:
            answer = self._projects_service().read_project_provenance(artifact_id)
        except PhoneUnavailable as exc:
            return unavailable(exc.reason_code)
        except Exception as exc:
            return unavailable("PROJECTS_UNAVAILABLE", error_type=type(exc).__name__)
        if not answer.get("ok"):
            return _project_failure(answer, "PROJECT_PROVENANCE_UNAVAILABLE")
        return {"ok": True, "status": STATUS_OK, "artifact_id": answer.get("artifact_id"),
                "version_id": answer.get("version_id"),
                "version_number": answer.get("version_number"),
                "provenance": answer.get("provenance"),
                "source_gate": answer.get("source_gate"),
                "reason_code": answer.get("reason_code") or "",
                "private_text_included": False}

    def work_context(self, artifact_id: str) -> dict[str, Any]:
        """``GET .../projects/<id>/context``: 同一件作品的有界只读上下文。

        它把已经存在的四个 owner 读口按同一件作品聚合一次：作品 head 与旧版/来历
        （``private_project_service`` 的 Supply 读）、真实结果（A 的 Memory 可回忆经历，
        只保留 ``artifact_ref`` 就是这件作品的）、证据指回这些结果的做法与最近一次显式评价
        （B 的实践工作台，trial 的版本是方法自己的版本）、以及来源就是这件作品的本地草稿
        （B 的分享 owner，列表不带正文）。关联只认 owner ref，交集为空就不列；读不到是具名
        失败，不是空。它不写任何东西，也不发送任何草稿。
        """

        target = str(artifact_id or "").strip()
        if not target:
            return unavailable("WORK_ARTIFACT_ID_REQUIRED")
        try:
            projects = self._projects_service()
        except PhoneUnavailable as exc:
            return unavailable(exc.reason_code)
        except Exception as exc:
            return unavailable("PROJECTS_UNAVAILABLE", error_type=type(exc).__name__)
        try:
            from life_practice_context import PracticeSeam
            from life_work_context import WorkContextReader

            practice = PracticeSeam(self._practice_service())
            try:
                sharing = self._sharing_service()
            except Exception:
                sharing = None
            context = WorkContextReader(
                projects=projects, practice=practice, sharing=sharing,
                memory=self._memory_service()).read(target)
        except Exception as exc:
            return unavailable("WORK_CONTEXT_UNAVAILABLE", error_type=type(exc).__name__)
        if not isinstance(context, Mapping):
            return unavailable("WORK_CONTEXT_ANSWER_INVALID")
        return {
            "ok": True,
            "status": STATUS_OK,
            "artifact_id": context.get("artifact_id") or target,
            "context": dict(context),
            # 只投影有界摘录与方法说明，没有任何完整正文；草稿列表也不带正文。
            "private_text_included": False,
            "bounded_excerpt": True,
            "local_only": True,
            "delivered_to_user": False,
        }

    # -- 「今天」：她已经发生的事（纯读） -------------------------------------------------

    def _today_state_root(self) -> Optional[Path]:
        """这个手机实例自己的 Life 状态根；没有嵌套实例就是 ``None``（不猜目录）。"""

        from . import life as phone_life

        try:
            root = phone_life.phone_root(self.root)
            nested = phone_life.read_nested_life(root)
            if nested is None:
                return None
            return phone_life.life_state_root(root, nested)
        except Exception:
            return None

    def _today_activities(self, state_root: Path, day: str
                          ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """当天开始的 Activity，从权威自己的 ``canonical_life_state.json`` 读。

        这份文件保留每一条 Activity（终态的也在，只清前台指针），所以「今天做过的」就是
        按每条自己的 ``started_at`` 落在当天的那几条。读不到就返回具名原因 + 空列表，
        绝不把「读不到」报成「没做事」。``status`` 就是结果（ACTIVE/PAUSED/WAITING/
        BLOCKED/COMPLETED/FAILED/ABANDONED），终态与 BLOCKED 另带 owner 自己的原因码。
        """

        path = Path(str(state_root)) / CANONICAL_ACTIVITY_FILENAME
        source: dict[str, Any] = {"state": "ok", "source": CANONICAL_ACTIVITY_FILENAME,
                                  "reason_code": None, "detail": None}
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            source.update({"state": "unavailable", "reason_code": "CANONICAL_STATE_ABSENT"})
            return [], source
        except OSError as exc:
            source.update({"state": "unavailable", "reason_code": "CANONICAL_STATE_UNREADABLE",
                           "detail": type(exc).__name__})
            return [], source
        try:
            data: Any = json.loads(raw)
        except ValueError:
            source.update({"state": "unavailable", "reason_code": "CANONICAL_STATE_CORRUPT"})
            return [], source
        activities = data.get("activities") if isinstance(data, Mapping) else None
        if not isinstance(activities, Mapping):
            source.update({"state": "unavailable", "reason_code": "CANONICAL_STATE_CORRUPT",
                           "detail": "activities"})
            return [], source
        rows: list[dict[str, Any]] = []
        for ref, act in activities.items():
            if not isinstance(act, Mapping):
                continue
            started = today_moment(act.get("started_at"))
            if started is None or started.date().isoformat() != day:
                continue
            status = str(act.get("status") or "")
            blocked_reason = act.get("blocked_reason_code")
            rows.append({
                "ref": str(ref),
                "title": str(act.get("title") or ""),
                "kind": str(act.get("activity_kind") or ""),
                "status": status,
                "started_at": started.isoformat(timespec="seconds"),
                "ended_at": today_iso(act.get("ended_at")),
                "terminal_reason_code": act.get("terminal_reason_code"),
                # 2026-10-28: FAILED/BLOCKED keep the owner's own reason code and only
                # structured refs; the private body never enters this projection.
                "status_reason_code": act.get("terminal_reason_code") or blocked_reason,
                "blocked_reason_code": blocked_reason,
                "blocked_on_ref": act.get("blocked_on_ref"),
                "failure_assessment_ref": act.get("failure_assessment_ref"),
            })
        rows.sort(key=lambda row: (str(row.get("started_at") or ""), str(row.get("ref") or "")))
        # Read-only recovery projection: whether an OPEN eligibility / PENDING condition
        # exists for this Activity, and the one entry the owner facts support.  It never
        # invents a timer or a resume, and an unreadable store stays visibly unknown.
        try:
            from . import own_day_read as _odr
            facts, recovery_source = _odr.activity_recovery_projection(state_root)
            for row in rows:
                row["recovery"] = _odr.recovery_row(row, facts)
            source["recovery"] = recovery_source
        except Exception as exc:  # a projection failure must not hide the Activity list
            source["recovery"] = {"state": "unavailable",
                                  "reason_code": "RECOVERY_PROJECTION_FAILED",
                                  "detail": type(exc).__name__}
        source["count"] = len(rows)
        source["activities_without_started_at"] = sum(
            1 for act in activities.values()
            if isinstance(act, Mapping) and today_moment(act.get("started_at")) is None)
        return rows, source

    def _today_work_versions(self, day: str
                             ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """当天 owner 记录的版本：作品名 + 版本号，逐件经 ``private_project_service`` 读。

        作品清单本身就是 owner 的（阅读材料、私人事项、相册已在清单里排除）；每件只看最近
        一页版本元数据。owner 不可用时这一段如实标不可用，活动的部分照常返回——两段互不冒充。
        """

        source: dict[str, Any] = {"state": "ok", "reason_code": None, "detail": None,
                                  "truncated": False, "unreadable_works": []}
        try:
            service = self._projects_service()
            listed = service.list_projects(limit=TODAY_MAX_WORKS + 1)
        except PhoneUnavailable as exc:
            source.update({"state": "unavailable", "reason_code": exc.reason_code})
            return [], source
        except Exception as exc:
            source.update({"state": "unavailable", "reason_code": "PROJECTS_UNAVAILABLE",
                           "detail": type(exc).__name__})
            return [], source
        if not listed.get("ok"):
            source.update({"state": "unavailable",
                           "reason_code": str(listed.get("reason_code")
                                              or "PROJECTS_UNAVAILABLE")})
            return [], source
        projects = [dict(row) for row in (listed.get("projects") or [])
                    if isinstance(row, Mapping)]
        rows: list[dict[str, Any]] = []
        for project in projects[:TODAY_MAX_WORKS]:
            artifact_id = str(project.get("artifact_id") or "")
            if not artifact_id:
                continue
            try:
                history = service.read_project_history(artifact_id, limit=TODAY_VERSION_PAGE)
            except Exception as exc:  # 一件读不到不影响其它件，也不当成「今天没写」
                source["unreadable_works"].append(
                    {"artifact_id": artifact_id, "detail": type(exc).__name__})
                continue
            if not history.get("ok"):
                source["unreadable_works"].append(
                    {"artifact_id": artifact_id,
                     "reason_code": str(history.get("reason_code") or "PROJECT_VERSIONS_UNAVAILABLE")})
                continue
            for entry in history.get("entries") or []:
                if not isinstance(entry, Mapping):
                    continue
                created = today_moment(entry.get("created_at"))
                if created is None or created.date().isoformat() != day:
                    continue
                rows.append({
                    "artifact_id": artifact_id,
                    "title": str(project.get("title") or ""),
                    "version_id": str(entry.get("version_id") or ""),
                    "version_number": entry.get("version_number"),
                    "created_at": created.isoformat(timespec="seconds"),
                })
        rows.sort(key=lambda row: (str(row.get("created_at") or ""),
                                   str(row.get("artifact_id") or "")))
        source["truncated"] = bool(listed.get("truncated")) or len(projects) > TODAY_MAX_WORKS
        source["count"] = len(rows)
        return rows, source

    def today(self) -> dict[str, Any]:
        """``GET /api/phone/today``：她今天（业务时钟当天）已经发生的事。纯读，零写。

        两段：当天开始的 Activity（开始时间 / 做了什么 / 结果），以及当天产生的作品版本
        （作品名 + 版本号）。什么都没做就是两个空列表——不编一条凑数；状态根读不到则是
        具名的 unavailable，而不是一个看起来像「她今天很安静」的成功回答。
        """

        state_root = self._today_state_root()
        if state_root is None:
            return unavailable("TODAY_LIFE_STATE_UNBOUND",
                               note="没有可读的 Life 实例；不把读不到说成她今天什么都没做")
        clock = today_business_day(state_root)
        day = str(clock.get("day") or "")
        activities, activity_source = self._today_activities(state_root, day)
        works, work_source = self._today_work_versions(day)
        if activity_source.get("state") != "ok":
            # 「她今天做了什么」的核心权威读不到：这里必须说不可用，不能回一个空列表
            # 让她看起来安静了一天。作品段照常附带，但它替代不了活动权威。
            return unavailable(str(activity_source.get("reason_code") or "TODAY_UNAVAILABLE"),
                               day=day, timezone=TODAY_TIMEZONE, clock=clock,
                               works=works, sources={"activities": activity_source,
                                                     "works": work_source},
                               note="活动权威读不到；这不等于她今天什么都没做")
        return {
            "ok": True,
            "status": STATUS_OK,
            "data": {
                "schema": TODAY_SCHEMA,
                "day": day,
                # 分日依据：一条 Activity 落在哪天，看它自己记录的 started_at。
                "day_basis": "activity_started_at",
                "timezone": TODAY_TIMEZONE,
                "clock": clock,
                "activities": activities,
                "activity_count": len(activities),
                "works": works,
                "work_count": len(works),
                "sources": {"activities": activity_source, "works": work_source},
                "read_only": True,
            },
        }

    def today_arrangements(self) -> dict[str, Any]:
        """``GET /api/phone/today/arrangements``：今天自选计划、外部义务与可证执行状态。

        纯读：只读 ``life_day_plan`` 两域、canonical Activity 和既有 Action/Decision/owner
        回执读口。它不启动服务、不调用模型、不写任何结果 store；读不到来源就具名不可用，
        不把「读不到」变成「今天没有安排」。
        """

        from . import own_day_read

        state_root = self._today_state_root()
        if state_root is None:
            return unavailable("TODAY_LIFE_STATE_UNBOUND",
                               note="没有可读的 Life 实例；不把读不到说成今天没有安排")
        clock = today_business_day(state_root)
        return own_day_read.arrangements_envelope(
            state_root=state_root, subject_id=self.subject, clock=clock)

    def decision_review(self, *, decision_id: Optional[str] = None,
                        limit: int = 10) -> dict[str, Any]:
        """``GET /api/phone/decisions/review``：决定的有限摘要与 Adoption 状态，纯读。

        只读 AG-1 的不可变 DecisionRecord/处置表与 AG-2 自己的 Adoption 记录。
        rationale 正文在手机读面始终扣留；实时适用性复核需要正式 AG-2 准入读口，手机侧
        不复制规则，明确报 NOT_EVALUATED。不启动服务、不调用模型、不写任何状态。
        """

        from . import own_day_read

        state_root = self._today_state_root()
        if state_root is None:
            return unavailable("TODAY_LIFE_STATE_UNBOUND",
                               note="没有可读的 Life 实例；不把读不到说成没有决定")
        return own_day_read.decision_review_envelope(
            state_root=state_root, decision_id=decision_id, limit=limit)

    def day_timeline(self, *, subject_view: bool = False) -> dict[str, Any]:
        """``GET /api/phone/today/timeline``：她今天有记录的事，按来源合并成只读时间线。

        纯读：只读既有的 Activity 权威、日计划、World execution ledger、日记/相册/钱包
        facade。它不新建 store、不写日志、不启动服务、不调用模型。`subject_view` 只控制
        「骨架之外是否给出原入口引用」；正文/caption/图片字节永远不进入聚合读面。
        """

        from . import day_timeline

        state_root = self._today_state_root()
        if state_root is not None:
            clock = today_business_day(state_root)
        else:
            moment = datetime.now(tz=TODAY_TZ).replace(microsecond=0)
            clock = {"source": "system_clock", "reason_code": "LIFE_STATE_UNBOUND",
                     "logical_at": moment.isoformat(timespec="seconds"),
                     "day": moment.date().isoformat(), "run_id": None, "domain_id": None}
        return day_timeline.timeline_envelope(
            service=self, state_root=state_root, subject_id=self.subject, clock=clock,
            subject_view=bool(subject_view), world_home=Path(self.world_home),
            world_clock_injected=self._world_clock is not None)

    def day_story(self, *, day: Optional[str] = None) -> dict[str, Any]:
        """``GET /api/phone/today/story``：统一因果生活故事线（100% 零写入纯读，零大模型调用）。"""

        from . import day_story_view

        return day_story_view.read_today_story_zero_write(self.root, day=day)

    # -- 双人书桌：四态木牌 / 未发短笺 / 明日便签（只读聚合 + 一个显式写口） ---------------

    def _desk_state_root(self) -> Optional[Path]:
        """The same nested Life root the 'today' reads use; ``None`` means unbound, not idle."""

        return self._today_state_root()

    def _canonical_activity_doc(self) -> tuple[Optional[dict[str, Any]], str]:
        """Read-only canonical Activity document.  Never written, never guessed."""

        root = self._desk_state_root()
        if root is None:
            return None, "LIFE_STATE_UNBOUND"
        path = Path(root) / CANONICAL_ACTIVITY_FILENAME
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None, "CANONICAL_STATE_ABSENT"
        except (OSError, ValueError):
            return None, "CANONICAL_STATE_UNREADABLE"
        if not isinstance(payload, dict) or not isinstance(payload.get("activities"), dict):
            return None, "CANONICAL_STATE_MALFORMED"
        return payload, ""

    def _desk_foreground(self, doc: Optional[Mapping[str, Any]],
                         reason: str) -> dict[str, Any]:
        """The one canonical foreground pointer, as the owner records it."""

        if doc is None:
            return {"available": False, "reason_code": reason, "foreground": None}
        ref = doc.get("foreground_activity_ref")
        if not ref:
            return {"available": True, "reason_code": None, "foreground": None}
        activity = (doc.get("activities") or {}).get(str(ref))
        if not isinstance(activity, Mapping):
            return {"available": False, "reason_code": "FOREGROUND_REF_MISSING_ACTIVITY",
                    "foreground": None}
        return {
            "available": True,
            "reason_code": None,
            "foreground": {
                "ref": str(ref),
                "status": str(activity.get("status") or ""),
                "kind": str(activity.get("activity_kind") or ""),
                "interruptibility": str(activity.get("interruptibility") or "") or None,
            },
            #: The raw owner record, so the current-work projection can read the same
            #: single-valued target binding the Life cycle reads.  Never sent as-is.
            "activity": dict(activity),
        }

    def _desk_current_work(self, canonical: Mapping[str, Any]
                           ) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
        """The work the **canonical foreground** is provably on -- not the start-up binding.

        ``instance.work_artifact_id`` only records what the instance started with; after a
        new work is opened the foreground Activity is the authority.  The target comes from the
        Activity's own single-valued binding (``origin_ref``/``checkpoint_ref``/``target_ref``)
        or one unambiguous artifact among its refs -- never the first ``subject_refs`` item.
        Reading material, the private-items document, non-text artifacts and share drafts are
        not "the work on the desk".  When nothing is provable the answer is a named
        unavailable, and the start-up binding is reported separately as ``bound_work`` with
        ``is_current=false``.
        """

        from . import desk_presence_view as desk

        bound_work: Optional[dict[str, Any]] = None
        bound_id = str(self.instance.get("work_artifact_id") or "").strip()
        if bound_id:
            bound_work = {
                "artifact_id": bound_id,
                "available": True,
                "is_current": False,
                "source": "instance.work_artifact_id",
                "note": "启动绑定；不是当前前台作品，current_work 只认 canonical 前台的具名 target",
            }

        def not_current(reason: str, artifact_id: Optional[str] = None, **extra: Any
                        ) -> dict[str, Any]:
            answer: dict[str, Any] = {"available": False, "reason_code": reason,
                                      "artifact_id": artifact_id}
            answer.update(extra)
            return answer

        if not canonical.get("available"):
            return not_current(str(canonical.get("reason_code")
                                   or "CANONICAL_STATE_UNAVAILABLE")), bound_work
        foreground = canonical.get("foreground") \
            if isinstance(canonical.get("foreground"), Mapping) else None
        if foreground is None:
            return not_current("NO_FOREGROUND_ACTIVITY"), bound_work
        if str(foreground.get("status") or "") != "ACTIVE":
            return not_current("FOREGROUND_NOT_ACTIVE"), bound_work
        activity = canonical.get("activity") if isinstance(canonical.get("activity"),
                                                           Mapping) else {}
        identity, reason = desk.foreground_work_identity(activity)
        if identity is None:
            return not_current(str(reason or "FOREGROUND_TARGET_UNKNOWN")), bound_work

        # The reading material and the private-items document are artifacts but not works.
        excluded: set[str] = set()
        try:
            from life_step_readiness import canonical_target_ref

            for value in (self.reading_artifact_id, self.private_items_artifact_id):
                canonical_ref = canonical_target_ref(value) if value else None
                if canonical_ref:
                    excluded.add(canonical_ref)
        except Exception:
            pass
        if identity in excluded:
            return not_current("FOREGROUND_TARGET_NOT_A_WORK", identity), bound_work

        try:
            project = self.project(identity)
        except Exception:
            return not_current("WORK_NOT_READABLE", identity), bound_work
        if not project.get("ok"):
            return not_current(str(project.get("reason_code") or "WORK_NOT_READABLE"),
                               identity), bound_work
        row = project.get("project") if isinstance(project.get("project"), Mapping) else {}
        if row.get("continuable") is False:
            return not_current("FOREGROUND_TARGET_NOT_TEXT_WORK", identity,
                               artifact_type=row.get("artifact_type")), bound_work
        title = str(row.get("title") or "")
        try:
            from life_sharing_service import is_share_title

            if title and is_share_title(title):
                return not_current("FOREGROUND_TARGET_IS_SHARE_DRAFT", identity), bound_work
        except Exception:
            pass
        return desk.work_block(project), bound_work

    def desk_presence(self) -> dict[str, Any]:
        """``GET /api/phone/desk/presence``: door sign + desk objects + comm availability.

        Pure read: World snapshot, canonical Activity, the reading workbench and one project
        head are all read-only.  It starts no service, calls no model and writes no file.  An
        unreadable source is named instead of being turned into "at home, open".
        """

        from . import desk_presence_view as desk

        world_result = self.world()
        world_ok = bool(world_result.get("ok"))
        payload = world_result.get("data") if isinstance(world_result.get("data"),
                                                        Mapping) else {}
        location = payload.get("location") if isinstance(payload.get("location"),
                                                          Mapping) else {}
        body = payload.get("body") if isinstance(payload.get("body"), Mapping) else {}

        doc, reason = self._canonical_activity_doc()
        canonical = self._desk_foreground(doc, reason)
        door = desk.classify_door(location=location, world_ok=world_ok,
                                  world_reason=str(world_result.get("reason_code") or ""),
                                  canonical=canonical, body=body)
        comm = desk.communication_block(door=door, canonical=canonical)

        try:
            reading = desk.reading_block(self.reading_material())
        except Exception:
            reading = {"available": False, "reason_code": "READING_MATERIAL_UNAVAILABLE"}

        work, bound_work = self._desk_current_work(canonical)

        return envelope(True, STATUS_OK, {
            "schema": desk.PRESENCE_SCHEMA,
            "read_only": True,
            "door_sign": door["door_sign"],
            "door_status": door["door_status"],
            "door_reason_code": door["door_reason_code"],
            "evidence": door["evidence"],
            "desk": {"current_work": work, "bound_work": bound_work, "reading": reading},
            "interruptibility": comm["interruptibility"],
            "communication_availability": comm["communication_availability"],
            # 没有真实伴侣在场的依据：不把用户在线/刚 GET 过/共同书桌当成有人在。
            "partner_presence": {
                "value": None,
                "status": "NOT_RECORDED",
                "reason_code": "NO_PARTNER_EVIDENCE",
                "note": "没有真实伴侣在场的记录；桌面名称与请求本身不算在场",
            },
            "private_text_included": False,
            "local_only": True,
        })

    def desk_notes(self, *, subject_view: bool = False) -> dict[str, Any]:
        """``GET /api/phone/desk/notes``: retained short notes + next-day items.

        Default (partner/operator) view keeps every body/title withheld.  ``subject_view`` is
        only reached after this serve's local capability matched; it then returns the bounded
        draft text and the tomorrow item titles that already exist.  A wrong credential must
        fail before this method is called.
        """

        from . import desk_presence_view as desk

        listing = self.shares(states=list(desk.RETAINED_SHARE_STATES), limit=None)
        rows, share_counts = desk.retained_share_rows(listing)
        share_reason = None if listing.get("ok") else str(listing.get("reason_code") or "")
        if subject_view:
            for row in rows:
                share_id = str(row.get("share_id") or "")
                detail = self.share(share_id) if share_id else {}
                if detail.get("ok"):
                    row.update(desk.share_detail_private(detail))
                    row["body_withheld"] = False
                    row["text_withheld"] = not bool(row.get("text"))
                else:
                    row["detail_reason_code"] = str(detail.get("reason_code") or "")

        root = self._desk_state_root()
        clock = today_business_day(root) if root is not None else {
            "source": "system_clock", "reason_code": "LIFE_STATE_UNBOUND",
            "logical_at": datetime.now(tz=TODAY_TZ).replace(microsecond=0).isoformat(),
            "day": datetime.now(tz=TODAY_TZ).date().isoformat(),
            "run_id": None, "domain_id": None,
        }
        next_day = desk.next_business_day(clock)
        tomorrow: dict[str, Any] = {
            "items": [], "count": 0, "local_day": next_day["local_day"],
            "clock": clock, "basis": next_day["basis"],
            "timezone_offset": next_day["timezone_offset"],
        }
        if root is None:
            tomorrow.update({"available": False, "reason_code": "LIFE_STATE_UNBOUND"})
        else:
            try:
                from life_future_items import FutureItemError, FutureItems

                store = FutureItems(root, subject_id=self.subject)
                due = [item.to_dict() for item in store.open_items()
                       if desk.falls_on_local_day(item.due_at,
                                                  local_day=next_day["local_day"],
                                                  offset=next_day["timezone_offset"])]
                due.sort(key=lambda item: (str(item.get("due_at") or ""),
                                           str(item.get("item_ref") or "")))
                tomorrow.update({
                    "available": True,
                    "items": [desk.tomorrow_row(item, with_title=bool(subject_view))
                              for item in due[:20]],
                    "count": len(due),
                })
            except FutureItemError as exc:
                tomorrow.update({"available": False, "reason_code": exc.reason_code})
            except Exception:
                tomorrow.update({"available": False, "reason_code": "FUTURE_ITEMS_UNAVAILABLE"})

        text_included = bool(subject_view and (
            any(row.get("text") for row in rows)
            or any("title" in item for item in tomorrow["items"])))
        return envelope(True, STATUS_OK, {
            "schema": desk.NOTES_SCHEMA,
            "read_only": True,
            "view": "subject" if subject_view else "partner_default",
            "shares": {
                "retained": rows,
                "counts": share_counts["counts"],
                "retained_count": share_counts["retained_count"],
                "truncated": share_counts["truncated"],
                "source": "LifeSharingService",
                "unavailable_reason_code": share_reason or None,
            },
            "tomorrow": tomorrow,
            # 没有现成的「允许伴侣读未发正文」授权机制；默认不宣称支持。
            "partner_preview_available": False,
            "partner_preview_reason_code": desk.PARTNER_PREVIEW_REASON,
            "private_text_included": text_included,
            "local_only": True,
            "note": "未发短笺/明日便签正文默认不公开；只有本次 serve 的本地 capability 有效时"
                    "才返回自己视角的明文。已投递分享只在原 inbox 按既有引用呈现。",
        })

    def desk_tomorrow_write(self, *, action: str, title: Optional[str] = None,
                            item_ref: Optional[str] = None, due_at: Optional[str] = None,
                            due_in_minutes: Optional[int] = None,
                            reason: Optional[str] = None) -> dict[str, Any]:
        """One explicit operator/subject write on the existing ``FutureItems`` owner.

        No new store, no timer, no automatic reschedule/completion/send.  The caller (the phone
        server) has already required CSRF and this serve's local capability; this method only
        forwards the explicit intent and re-reads the same object afterwards.
        """

        root = self._desk_state_root()
        if root is None:
            return unavailable("LIFE_STATE_UNBOUND")
        from life_future_items import FutureItemError, FutureItems

        try:
            store = FutureItems(root, subject_id=self.subject)
            if action == "propose":
                outcome = store.propose(title=title, due_at=due_at,
                                        in_minutes=due_in_minutes)
                item = outcome.get("item") or {}
                read_back = store.get(item.get("item_ref")) if item.get("item_ref") else None
                record = read_back.to_dict() if read_back is not None else item
                return envelope(True, STATUS_OK if outcome.get("created")
                                else STATUS_ALREADY,
                                {"action": "propose", "item_ref": record.get("item_ref"),
                                 "created": bool(outcome.get("created")), "item": record,
                                 "read_back": read_back is not None})
            if action == "cancel":
                store.cancel(item_ref, reason=(reason or "CANCELLED_BY_SUBJECT"))
            elif action == "reschedule":
                store.reschedule(item_ref, due_at=due_at, in_minutes=due_in_minutes,
                                 reason=(reason or "RESCHEDULED"))
            else:
                return rejected("DESK_ACTION_UNSUPPORTED", action=action)
            read_back = store.get(item_ref)
            if read_back is None:
                return rejected("FUTURE_ITEM_NOT_FOUND", item_ref=item_ref)
            return envelope(True, STATUS_OK, {
                "action": action, "item_ref": read_back.item_ref,
                "item": read_back.to_dict(), "read_back": True,
            })
        except FutureItemError as exc:
            return rejected(exc.reason_code, detail=exc.detail)
        except Exception as exc:
            return rejected("DESK_FUTURE_WRITE_FAILED", error_type=type(exc).__name__)

    # -- 窗边景致 / 冰箱贴与自选锁屏 / 分日慢写的信 ---------------------------------------

    def _keepsake_state_path(self) -> Path:
        from . import window_keepsake_view as view

        return view.state_path(self.root)

    def _keepsake_state(self) -> tuple[dict[str, Any], Optional[str]]:
        from . import window_keepsake_view as view

        return view.read_state(self.root)

    def _keepsake_now_iso(self) -> str:
        """The explicit-selection clock: the same business clock the rest of the phone uses."""

        root = self._desk_state_root()
        clock = today_business_day(root) if root is not None else None
        moment = today_moment((clock or {}).get("logical_at")) if clock else None
        if moment is None:
            moment = datetime.now(tz=timezone.utc)
        return moment.isoformat(timespec="seconds")

    def _home_soundscape(self, world_result: Mapping[str, Any]) -> dict[str, Any]:
        """Today's home ambient **template** events; no weather authority is invented."""

        from . import window_keepsake_view as view

        try:
            import world_ambient_events as ambient
        except Exception:
            return view.soundscape_unavailable("AMBIENT_MODULE_UNAVAILABLE")
        if not ambient.feature_enabled(self.environment):
            return view.soundscape_unavailable("FEATURE_DISABLED")
        data = world_result.get("data") if isinstance(world_result.get("data"), Mapping) else {}
        location = data.get("location") if isinstance(data.get("location"), Mapping) else {}
        if not world_result.get("ok"):
            return view.soundscape_unavailable(
                str(world_result.get("reason_code") or "WORLD_UNAVAILABLE"))
        if location.get("status") != "available":
            return view.soundscape_unavailable(
                str(location.get("reason_code") or "LOCATION_UNAVAILABLE"))
        if str(location.get("place_id") or "") != "home":
            return view.soundscape_unavailable(view.NOT_AT_HOME, at_home=False)
        world = data.get("world") if isinstance(data.get("world"), Mapping) else {}
        day_text = world.get("local_date")
        local_time = world.get("local_time")
        day = view.parse_instant(day_text)
        now = view.parse_instant(local_time)
        if day is None or now is None:
            return view.soundscape_unavailable("WORLD_LOCAL_CLOCK_UNAVAILABLE")
        try:
            events = ambient.active_ambient_events(
                day.date(), now, relevant_location_ids={"home"},
                environment=self.environment)
        except Exception as exc:
            return view.soundscape_unavailable("AMBIENT_EVENTS_UNAVAILABLE",
                                               error_type=type(exc).__name__)
        return view.soundscape_available(
            events, local_date=day_text, local_time=local_time,
            clock_source=("injected_world_clock" if self._world_clock is not None
                          else "world_snapshot"))

    def home_window(self) -> dict[str, Any]:
        """``GET /api/phone/home/window``: plants / light / personal space / soundscape."""

        from . import window_keepsake_view as view

        world_result = self.world()
        try:
            from world_home_space import observe_home

            observation = observe_home(hermes_home=self.world_home, world_state=None,
                                       environment=self.environment)
        except Exception as exc:
            observation = {"status": "unavailable",
                           "reason_code": "HOME_SPACE_UNAVAILABLE",
                           "error_type": type(exc).__name__}
        return envelope(True, STATUS_OK, view.window_block(
            world_result=world_result, home_observation=observation,
            soundscape=self._home_soundscape(world_result)))

    def _lockscreen_status(self, album_artifact_id: str,
                           *, subject_view: bool) -> dict[str, Any]:
        detail = self.album_detail(album_artifact_id)
        if not detail.get("ok"):
            return {"status": "withheld", "reason_code": str(
                detail.get("reason_code") or "LOCKSCREEN_IMAGE_UNAVAILABLE"),
                "album_artifact_id": album_artifact_id}
        row = detail.get("image") if isinstance(detail.get("image"), Mapping) else {}
        out: dict[str, Any] = {
            "status": "available",
            "reason_code": None,
            "album_artifact_id": album_artifact_id,
            "content_type": row.get("content_type"),
        }
        if subject_view:
            out["caption"] = row.get("caption")
        return out

    def _verify_lockscreen_image(self, album_artifact_id: str) -> dict[str, Any]:
        """Only a real, currently readable picture from *her* album may be the lockscreen."""

        detail = self.album_detail(album_artifact_id)
        if not detail.get("ok"):
            return {"ok": False, "reason_code": str(
                detail.get("reason_code") or "LOCKSCREEN_IMAGE_UNAVAILABLE")}
        row = detail.get("image") if isinstance(detail.get("image"), Mapping) else {}
        if row.get("content_type") not in ("image/png", "image/jpeg") or not row.get("media"):
            return {"ok": False, "reason_code": "LOCKSCREEN_IMAGE_INVALID"}
        # The owner's byte read verifies size + SHA-256 on the real media path.  The bytes are
        # dropped immediately: only the reference is stored.
        image = self.album_image(album_artifact_id)
        if not image.get("ok"):
            return {"ok": False, "reason_code": str(
                image.get("reason_code") or "LOCKSCREEN_IMAGE_BYTES_UNAVAILABLE")}
        return {"ok": True}

    def _verify_pin_share(self, share_id: str) -> dict[str, Any]:
        """Fridge magnets are only delivered local shares with a readable current source."""

        detail = self.share(share_id)
        if not detail.get("ok"):
            return {"ok": False, "reason_code": str(
                detail.get("reason_code") or "PINNED_SHARE_NOT_FOUND")}
        data = detail.get("data") if isinstance(detail.get("data"), Mapping) else {}
        share = data.get("share") if isinstance(data.get("share"), Mapping) else {}
        receipt = data.get("receipt") if isinstance(data.get("receipt"), Mapping) else {}
        if str(share.get("state") or "") != "LOCAL_DELIVERED" \
                or not data.get("delivered") or not receipt:
            return {"ok": False, "reason_code": "PINNED_SHARE_NOT_DELIVERED"}
        if receipt.get("local_only") is False or receipt.get("external_channel_sent") is True:
            return {"ok": False, "reason_code": "PINNED_SHARE_NOT_LOCAL"}
        if receipt.get("audience") not in (None, "", "local:owner") \
                or receipt.get("channel") not in (None, "", "local_inbox"):
            return {"ok": False, "reason_code": "PINNED_SHARE_NOT_LOCAL"}
        if not data.get("source_readable") or share.get("text_withheld"):
            return {"ok": False, "reason_code": "PINNED_SHARE_SOURCE_NOT_READABLE"}
        return {"ok": True}

    def _pinned_share_status(self, share_id: str, pinned_at: Any) -> dict[str, Any]:
        detail = self.share(share_id)
        base = {"share_id": share_id, "pinned_at": pinned_at,
                "status": "withheld", "text_withheld": True}
        if not detail.get("ok"):
            base["reason_code"] = str(detail.get("reason_code") or "PINNED_SHARE_NOT_FOUND")
            return base
        data = detail.get("data") if isinstance(detail.get("data"), Mapping) else {}
        share = data.get("share") if isinstance(data.get("share"), Mapping) else {}
        receipt = data.get("receipt") if isinstance(data.get("receipt"), Mapping) else {}
        base["state"] = share.get("state")
        base["delivered_at"] = receipt.get("delivered_at") or share.get("updated_at")
        if str(share.get("state") or "") != "LOCAL_DELIVERED" or not data.get("delivered") \
                or not receipt:
            base["reason_code"] = "PINNED_SHARE_NOT_DELIVERED"
            return base
        if receipt.get("local_only") is False or receipt.get("external_channel_sent") is True:
            base["reason_code"] = "PINNED_SHARE_NOT_LOCAL"
            return base
        if not data.get("source_readable") or share.get("text_withheld"):
            base["reason_code"] = "PINNED_SHARE_SOURCE_NOT_READABLE"
            return base
        base.update({
            "status": "available",
            "reason_code": None,
            "text": share.get("text") or "",
            "text_chars": share.get("text_chars"),
            "text_withheld": False,
        })
        return base

    def home_keepsakes(self, *, subject_view: bool = False) -> dict[str, Any]:
        """``GET /api/phone/home/keepsakes``: the explicit lockscreen + fridge pins, re-read."""

        from . import window_keepsake_view as view

        state, state_reason = self._keepsake_state()
        lockscreen_ref = str(((state.get("lockscreen") or {}).get("album_artifact_id")) or "")
        lockscreen = self._lockscreen_status(lockscreen_ref, subject_view=subject_view) \
            if lockscreen_ref else None
        pins = [self._pinned_share_status(str(row.get("share_id") or ""), row.get("pinned_at"))
                for row in (state.get("pins") or []) if row.get("share_id")]
        return envelope(True, STATUS_OK, view.keepsakes_block(
            state=state, state_reason=state_reason, lockscreen=lockscreen, pins=pins,
            subject_view=bool(subject_view)))

    def home_keepsake_action(self, *, action: str,
                             album_artifact_id: Optional[str] = None,
                             share_id: Optional[str] = None) -> dict[str, Any]:
        """One explicit select / clear / pin / unpin.  Same intent never bumps revision."""

        from . import window_keepsake_view as view

        if action not in ("select_lockscreen", "clear_lockscreen", "pin_share", "unpin_share"):
            return rejected("KEEPSAKE_ACTION_UNSUPPORTED", action=action)
        state, state_reason = self._keepsake_state()
        if state_reason:
            return rejected(state_reason)
        now_iso = self._keepsake_now_iso()
        reference: Optional[str] = None
        if action == "select_lockscreen":
            if not album_artifact_id:
                return rejected("LOCKSCREEN_IMAGE_REQUIRED")
            checked = self._verify_lockscreen_image(album_artifact_id)
            if not checked.get("ok"):
                return rejected(str(checked.get("reason_code") or "LOCKSCREEN_IMAGE_INVALID"))
            reference = album_artifact_id
            new_state, changed = view.select_lockscreen(state, album_artifact_id, now_iso)
        elif action == "clear_lockscreen":
            new_state, changed = view.clear_lockscreen(state, now_iso)
        elif action == "pin_share":
            if not share_id:
                return rejected("SHARE_ID_REQUIRED")
            checked = self._verify_pin_share(share_id)
            if not checked.get("ok"):
                return rejected(str(checked.get("reason_code") or "PINNED_SHARE_NOT_DELIVERED"))
            reference = share_id
            new_state, changed = view.pin_share(state, share_id, now_iso)
        else:
            if not share_id:
                return rejected("SHARE_ID_REQUIRED")
            reference = share_id
            new_state, changed = view.unpin_share(state, share_id, now_iso)
        if not changed:
            return envelope(True, STATUS_ALREADY, {
                "action": action, "changed": False,
                "revision": state.get("revision"),
                "updated_at": state.get("updated_at")})
        written = view.write_state(self.root, new_state)
        data = {"action": action, "changed": True,
                "revision": written.get("revision"),
                "updated_at": written.get("updated_at")}
        if action == "select_lockscreen":
            data["album_artifact_id"] = reference
        elif action in ("pin_share", "unpin_share"):
            data["share_id"] = reference
        return envelope(True, STATUS_OK, data)

    def _letters_clock(self) -> dict[str, Any]:
        root = self._desk_state_root()
        if root is not None:
            return today_business_day(root)
        moment = datetime.now(tz=TODAY_TZ).replace(microsecond=0)
        return {"source": "system_clock", "reason_code": "LIFE_STATE_UNBOUND",
                "logical_at": moment.isoformat(timespec="seconds"),
                "day": moment.date().isoformat(), "run_id": None, "domain_id": None}

    def home_letters(self, *, subject_view: bool = False) -> dict[str, Any]:
        """``GET /api/phone/home/letters``: a cross-day share/source-version projection.

        There is no letter store: each row is one existing share whose pinned source version's
        own version history spans at least two owner-timestamp local days.  Versions newer than
        the pinned version are excluded, and the postmark says plainly that an owner wall-clock
        timestamp is not a provable compressed-business-day stamp.
        """

        from . import window_keepsake_view as view

        listing = self.shares(limit=view.MAX_SHARE_SCAN)
        if not listing.get("ok"):
            return unavailable(str(listing.get("reason_code") or "SHARES_UNAVAILABLE"))
        data = listing.get("data") if isinstance(listing.get("data"), Mapping) else {}
        shares = [row for row in (data.get("shares") or []) if isinstance(row, Mapping)]
        clock = self._letters_clock()
        offset = view.clock_offset(clock)
        rows: list[dict[str, Any]] = []
        unprovable = 0
        try:
            projects = self._projects_service()
        except Exception:
            projects = None
        for row in shares:
            state = str(row.get("state") or "")
            if state not in view.LETTER_SHARE_STATES:
                continue
            source = row.get("source") if isinstance(row.get("source"), Mapping) else {}
            source_id = str(source.get("artifact_id") or "")
            version_id = str(source.get("version_id") or "")
            if not source_id or not version_id:
                continue
            history: dict[str, Any] = {}
            if projects is not None:
                try:
                    history = projects.read_project_history(source_id, limit=view.MAX_HISTORY)
                except Exception:
                    history = {}
            entries = [item for item in (history.get("entries") or [])
                       if isinstance(item, Mapping)]
            bound_number = next(
                (_int_or_none(item.get("version_number")) for item in entries
                 if str(item.get("version_id") or "") == version_id
                 and _int_or_none(item.get("version_number")) is not None), None)
            if bound_number is None:
                bound = self.project_version(source_id, version_id)
                bound_row = bound.get("version") if isinstance(bound.get("version"), Mapping) else {}
                bound_number = _int_or_none(bound_row.get("version_number"))
            if bound_number is None or not history:
                rows.append(view.letter_row(
                    share_row=row,
                    timeline={"date_basis": "owner_timestamp_localized",
                              "timezone_offset": offset, "writing_days": 0,
                              "versions_considered": 0,
                              "versions_after_cutoff_excluded": 0,
                              "undated_versions": 0, "unbounded_versions": 0},
                    history_complete=False,
                    history_reason=str(history.get("reason_code") or "SOURCE_VERSION_UNBOUNDED"),
                    text=None, delivered_at=None, status="unknown",
                    reason_code="SOURCE_VERSION_UNBOUNDED"))
                continue
            timeline = view.writing_days(entries, cutoff_version_number=bound_number,
                                         offset=offset)
            if timeline.get("writing_days", 0) < 2:
                if timeline.get("undated_versions") or timeline.get("unbounded_versions"):
                    # 可能是跨日作品，但时间戳/版本界不可证明：计数，不冒充「不是信」。
                    unprovable += 1
                continue
            text: Optional[str] = None
            delivered_at = None
            reason_code = None
            status = "delivered" if state == view.LOCAL_DELIVERED else state.lower()
            if row.get("text_withheld") is True:
                reason_code = str(row.get("source_reason_code") or "SOURCE_NOT_READABLE")
            if state == view.LOCAL_DELIVERED or subject_view:
                detail = self.share(str(row.get("share_id") or ""))
                if detail.get("ok"):
                    detail_data = detail.get("data") if isinstance(detail.get("data"),
                                                                   Mapping) else {}
                    share = detail_data.get("share") if isinstance(detail_data.get("share"),
                                                                  Mapping) else {}
                    receipt = detail_data.get("receipt") if isinstance(
                        detail_data.get("receipt"), Mapping) else {}
                    delivered_at = receipt.get("delivered_at")
                    if not detail_data.get("source_readable") or share.get("text_withheld"):
                        reason_code = str(detail_data.get("withheld_reason_code")
                                          or "SOURCE_NOT_READABLE")
                    elif isinstance(share.get("text"), str):
                        text = share.get("text")
                else:
                    reason_code = str(detail.get("reason_code") or "SHARE_DETAIL_UNAVAILABLE")
            if reason_code:
                status = "withheld" if text is None else status
            rows.append(view.letter_row(
                share_row=row, timeline=timeline,
                history_complete=bool(history.get("complete")),
                history_reason=str(history.get("reason_code") or ""),
                text=text, delivered_at=delivered_at, status=status,
                reason_code=reason_code))
        return envelope(True, STATUS_OK, view.letters_block(
            rows=rows, offset=offset, clock=clock, subject_view=bool(subject_view),
            scanned=len(shares), truncated=bool(data.get("truncated")),
            unprovable=unprovable))

    # -- 创作托盘 / 小物履历 / 三样喜欢之物 -----------------------------------------------

    def _workbench_state(self) -> tuple[dict[str, Any], Optional[str]]:
        from . import workbench_curio_view as view

        return view.read_tray_state(self.root)

    def _trio_state(self) -> tuple[dict[str, Any], Optional[str]]:
        from . import workbench_curio_view as view

        return view.read_trio_state(self.root)

    def _tray_image_status(self, album_artifact_id: str) -> dict[str, Any]:
        detail = self.album_detail(album_artifact_id)
        if not detail.get("ok"):
            return {"status": "withheld", "reason_code": str(
                detail.get("reason_code") or "TRAY_IMAGE_UNAVAILABLE"), "caption": None,
                "content_type": None}
        row = detail.get("image") if isinstance(detail.get("image"), Mapping) else {}
        if row.get("content_type") not in ("image/png", "image/jpeg") or not row.get("media"):
            return {"status": "withheld", "reason_code": "TRAY_IMAGE_INVALID",
                    "caption": None, "content_type": row.get("content_type")}
        return {"status": "available", "reason_code": None,
                "caption": row.get("caption"), "content_type": row.get("content_type")}

    def _tray_material_status(self, artifact_id: str,
                              baseline_source_revision: Any) -> dict[str, Any]:
        answer = self.material(artifact_id)
        row = {"artifact_id": artifact_id,
               "baseline_source_revision": baseline_source_revision,
               "current_source_revision": None, "title": None}
        if not answer.get("ok"):
            row.update({"status": "UNAVAILABLE",
                        "reason_code": str(answer.get("reason_code") or "MATERIAL_UNAVAILABLE")})
            return row
        data = answer.get("data") if isinstance(answer.get("data"), Mapping) else {}
        material = data.get("material") if isinstance(data.get("material"), Mapping) else {}
        row["title"] = material.get("title")
        current = str(material.get("source_revision") or "")
        row["current_source_revision"] = current or None
        if str(material.get("availability") or "") != "AVAILABLE":
            row.update({"status": "UNAVAILABLE",
                        "reason_code": "MATERIAL_" + str(material.get("availability")
                                                         or "UNAVAILABLE")})
            return row
        row.update({"status": "CURRENT" if current == str(baseline_source_revision or "")
                    else "STALE", "reason_code": None if current == str(
                        baseline_source_revision or "") else "MATERIAL_SOURCE_CHANGED"})
        return row

    def _tray_work_status(self, artifact_id: str, baseline_version_id: Any,
                          baseline_version_number: Any) -> dict[str, Any]:
        from . import workbench_curio_view as view

        answer = self.project(artifact_id)
        row = {"artifact_id": artifact_id,
               "baseline_version_id": baseline_version_id,
               "baseline_version_number": baseline_version_number,
               "current_head_version_id": None, "current_version_number": None,
               "head_advanced": "unknown", "title": None}
        if not answer.get("ok"):
            row.update({"status": "withheld",
                        "reason_code": str(answer.get("reason_code") or "WORK_UNAVAILABLE")})
            return row
        project = answer.get("project") if isinstance(answer.get("project"), Mapping) else {}
        row["title"] = project.get("title")
        if project.get("artifact_type") != "project":
            row.update({"status": "withheld", "reason_code": "NOT_A_PRIVATE_WORK"})
            return row
        title = str(project.get("title") or "")
        try:
            from life_sharing_service import is_share_title

            if title and is_share_title(title):
                row.update({"status": "withheld", "reason_code": "WORK_IS_SHARE_ENVELOPE"})
                return row
        except Exception:
            pass
        head = str(project.get("head_version_id") or "")
        number = project.get("version_number")
        row.update({"status": "available", "reason_code": None,
                    "current_head_version_id": head or None,
                    "current_version_number": number,
                    "head_advanced": view.head_advanced(
                        baseline_version_id=baseline_version_id,
                        baseline_version_number=baseline_version_number,
                        head_version_id=head, head_version_number=number,
                        history_entries=self._work_history_entries(artifact_id),
                        history_complete=False)})
        return row

    def _work_history_entries(self, artifact_id: str) -> list[Mapping[str, Any]]:
        from . import workbench_curio_view as view

        history = self.project_versions(artifact_id, limit=view.MAX_HISTORY)
        if not history.get("ok"):
            return []
        block = history.get("history") if isinstance(history.get("history"), Mapping) else {}
        return [row for row in (block.get("entries") or []) if isinstance(row, Mapping)]

    def home_workbench(self, *, subject_view: bool = False) -> dict[str, Any]:
        """``GET /api/phone/home/workbench``: the explicit tray, re-read from real owners."""

        from . import workbench_curio_view as view

        state, state_reason = self._workbench_state()
        tray = state.get("tray") if isinstance(state.get("tray"), Mapping) else {}
        images = []
        for row in tray.get("images") or []:
            ref = str(row.get("album_artifact_id") or "")
            if not ref:
                continue
            status = self._tray_image_status(ref)
            images.append({"album_artifact_id": ref, "added_at": row.get("added_at"), **status})
        materials = []
        for row in tray.get("materials") or []:
            ref = str(row.get("artifact_id") or "")
            if not ref:
                continue
            status = self._tray_material_status(ref, row.get("baseline_source_revision"))
            materials.append({"added_at": row.get("added_at"), **status})
        work = None
        stored_work = tray.get("work") if isinstance(tray.get("work"), Mapping) else None
        if stored_work and stored_work.get("artifact_id"):
            work = self._tray_work_status(
                str(stored_work.get("artifact_id")),
                stored_work.get("baseline_version_id"),
                stored_work.get("baseline_version_number"))
            work["added_at"] = stored_work.get("added_at")
        return envelope(True, STATUS_OK, view.workbench_block(
            state=state, state_reason=state_reason, subject_view=bool(subject_view),
            images=images, materials=materials, work=work))

    def _verify_tray_image(self, album_artifact_id: str) -> dict[str, Any]:
        return self._verify_lockscreen_image(album_artifact_id)

    def _verify_tray_material(self, artifact_id: str) -> dict[str, Any]:
        answer = self.material(artifact_id)
        if not answer.get("ok"):
            return {"ok": False, "reason_code": str(
                answer.get("reason_code") or "MATERIAL_UNAVAILABLE")}
        data = answer.get("data") if isinstance(answer.get("data"), Mapping) else {}
        material = data.get("material") if isinstance(data.get("material"), Mapping) else {}
        if str(material.get("availability") or "") != "AVAILABLE":
            return {"ok": False, "reason_code": "MATERIAL_NOT_AVAILABLE"}
        revision = str(material.get("source_revision") or "")
        if not revision:
            return {"ok": False, "reason_code": "MATERIAL_REVISION_UNAVAILABLE"}
        return {"ok": True, "baseline_source_revision": revision}

    def _verify_tray_work(self, artifact_id: str) -> dict[str, Any]:
        answer = self.project(artifact_id)
        if not answer.get("ok"):
            return {"ok": False, "reason_code": str(
                answer.get("reason_code") or "WORK_UNAVAILABLE")}
        project = answer.get("project") if isinstance(answer.get("project"), Mapping) else {}
        if project.get("artifact_type") != "project":
            return {"ok": False, "reason_code": "NOT_A_PRIVATE_WORK"}
        title = str(project.get("title") or "")
        try:
            from life_sharing_service import is_share_title

            if title and is_share_title(title):
                return {"ok": False, "reason_code": "WORK_IS_SHARE_ENVELOPE"}
        except Exception:
            pass
        head = str(project.get("head_version_id") or "")
        if not head:
            return {"ok": False, "reason_code": "WORK_VERSION_UNAVAILABLE"}
        return {"ok": True, "baseline_version_id": head,
                "baseline_version_number": project.get("version_number")}

    def home_workbench_action(self, *, action: str,
                              album_artifact_ids: Optional[list[Any]] = None,
                              material_ids: Optional[list[Any]] = None,
                              work_artifact_id: Optional[str] = None,
                              note: Optional[str] = None) -> dict[str, Any]:
        """One explicit ``set_tray`` / ``clear_tray``.  Invalid refs mean zero writes."""

        from . import workbench_curio_view as view

        if action not in ("set_tray", "clear_tray"):
            return rejected("TRAY_ACTION_UNSUPPORTED", action=action)
        state, state_reason = self._workbench_state()
        if state_reason:
            return rejected(state_reason)
        now_iso = self._keepsake_now_iso()
        if action == "clear_tray":
            new_state, changed = view.clear_tray(state, now_iso)
            return self._tray_write_result(state, new_state, changed, "clear_tray")

        images = self._tray_id_list(album_artifact_ids, view.MAX_TRAY_IMAGES,
                                    "TRAY_IMAGES_INVALID")
        materials = self._tray_id_list(material_ids, view.MAX_TRAY_MATERIALS,
                                       "TRAY_MATERIALS_INVALID")
        work_id = None
        if work_artifact_id not in (None, ""):
            if not isinstance(work_artifact_id, str) or not work_artifact_id.strip():
                return rejected("TRAY_WORK_INVALID")
            work_id = work_artifact_id.strip()
        if note is not None and not isinstance(note, str):
            return rejected("TRAY_NOTE_INVALID")
        clean_note = note.strip() if isinstance(note, str) else None
        if clean_note is not None and (len(clean_note) > view.MAX_TRAY_NOTE
                                       or "\x00" in clean_note or "\r" in clean_note):
            return rejected("TRAY_NOTE_INVALID")
        if not clean_note:
            clean_note = None
        if images is None:
            return rejected("TRAY_IMAGES_INVALID")
        if materials is None:
            return rejected("TRAY_MATERIALS_INVALID")

        tray = state.get("tray") if isinstance(state.get("tray"), Mapping) else {}
        stored_images = {str(row.get("album_artifact_id")): dict(row)
                         for row in (tray.get("images") or [])
                         if row.get("album_artifact_id")}
        stored_materials = {str(row.get("artifact_id")): dict(row)
                            for row in (tray.get("materials") or [])
                            if row.get("artifact_id")}
        stored_work = dict(tray["work"]) if isinstance(tray.get("work"), Mapping) else None

        # Same intent never bumps revision and never re-baselines a work that moved on.
        if (tuple(images) == tuple(stored_images)
                and tuple(materials) == tuple(stored_materials)
                and work_id == (stored_work or {}).get("artifact_id")
                and clean_note == tray.get("note")):
            return envelope(True, STATUS_ALREADY, {
                "action": "set_tray", "changed": False,
                "revision": state.get("revision"),
                "updated_at": state.get("updated_at")})

        image_rows = []
        for ref in images:
            if ref in stored_images:
                image_rows.append(stored_images[ref])
                continue
            checked = self._verify_tray_image(ref)
            if not checked.get("ok"):
                return rejected(str(checked.get("reason_code") or "TRAY_IMAGE_INVALID"),
                                artifact_id=ref)
            image_rows.append({"album_artifact_id": ref, "added_at": now_iso})
        material_rows = []
        for ref in materials:
            if ref in stored_materials:
                material_rows.append(stored_materials[ref])
                continue
            checked = self._verify_tray_material(ref)
            if not checked.get("ok"):
                return rejected(str(checked.get("reason_code") or "TRAY_MATERIAL_INVALID"),
                                artifact_id=ref)
            material_rows.append({"artifact_id": ref,
                                  "baseline_source_revision":
                                      checked.get("baseline_source_revision"),
                                  "added_at": now_iso})
        if work_id is None:
            work_row = None
        elif stored_work and stored_work.get("artifact_id") == work_id:
            work_row = stored_work
        else:
            checked = self._verify_tray_work(work_id)
            if not checked.get("ok"):
                return rejected(str(checked.get("reason_code") or "TRAY_WORK_INVALID"),
                                artifact_id=work_id)
            work_row = {"artifact_id": work_id,
                        "baseline_version_id": checked.get("baseline_version_id"),
                        "baseline_version_number": checked.get("baseline_version_number"),
                        "added_at": now_iso}
        target = {"images": image_rows, "materials": material_rows,
                  "work": work_row, "note": clean_note}
        new_state, changed = view.set_tray(state, target, now_iso)
        return self._tray_write_result(state, new_state, changed, "set_tray")

    def _tray_write_result(self, state: Mapping[str, Any], new_state: Mapping[str, Any],
                           changed: bool, action: str) -> dict[str, Any]:
        from . import workbench_curio_view as view

        if not changed:
            return envelope(True, STATUS_ALREADY, {
                "action": action, "changed": False,
                "revision": state.get("revision"),
                "updated_at": state.get("updated_at")})
        written = view.write_tray_state(self.root, new_state)
        tray = written.get("tray") or {}
        return envelope(True, STATUS_OK, {
            "action": action, "changed": True,
            "revision": written.get("revision"), "updated_at": written.get("updated_at"),
            "counts": {"images": len(tray.get("images") or []),
                       "materials": len(tray.get("materials") or []),
                       "works": 1 if tray.get("work") else 0}})

    @staticmethod
    def _tray_id_list(value: Any, maximum: int,
                      reason: str) -> Optional[list[str]]:
        if value is None:
            return []
        if not isinstance(value, list) or len(value) > maximum:
            return None
        out: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                return None
            ref = item.strip()
            if ref in out:
                return None
            out.append(ref)
        return out

    def home_curios(self) -> dict[str, Any]:
        """``GET /api/phone/home/curios``: owned instances joined to their real positions.

        Pure read: the economy document and the substrate are loaded read-only.  This method
        never ticks, materializes, ensures or migrates anything.
        """

        from . import workbench_curio_view as view

        try:
            import world_body_substrate as substrate
            import world_property_economy as economy
        except Exception:
            return unavailable("CURIOS_OWNER_UNAVAILABLE")
        try:
            state = economy.load_state(self.world_home)
        except Exception:
            return unavailable("ECONOMY_STATE_ERROR")
        if state is None:
            return envelope(True, STATUS_OK, view.curios_block(
                items=[], reason_code="GENESIS_NOT_INITIALIZED",
                source_note="World 产权 economy 尚未初始化；不造任何物件。"))
        live: Any = None
        substrate_unavailable = False
        try:
            live = substrate.SubstrateStore(
                substrate.substrate_path(self.world_home)).load()
        except Exception:
            live = None
            substrate_unavailable = True
        placements = (live.get("placements") if isinstance(live, Mapping) else None) or {}
        deliveries = {str(row.get("item_instance_id")): dict(row)
                      for row in (state.get("deliveries") or [])
                      if isinstance(row, Mapping) and row.get("item_instance_id")}
        catalog = {entry["catalog_item_id"]: entry.get("name") for entry in economy.CATALOG}
        rows = []
        for item in state.get("items") or []:
            if not isinstance(item, Mapping):
                continue
            item_id = str(item.get("item_instance_id") or "")
            if not item_id:
                continue
            rows.append(view.curio_row(
                item=item, name=catalog.get(item.get("catalog_item_id")),
                placement=placements.get(item_id), delivery=deliveries.get(item_id),
                substrate_unavailable=substrate_unavailable))
        return envelope(True, STATUS_OK, view.curios_block(
            items=rows,
            source_note=("SUBSTRATE_UNAVAILABLE" if substrate_unavailable else None)))

    @staticmethod
    def _trio_reason(code: Any) -> Optional[str]:
        return {
            "PINNED_SHARE_NOT_DELIVERED": "SHARE_NOT_DELIVERED",
            "PINNED_SHARE_NOT_LOCAL": "SHARE_NOT_LOCAL",
            "PINNED_SHARE_SOURCE_NOT_READABLE": "SHARE_SOURCE_NOT_READABLE",
            "PINNED_SHARE_NOT_FOUND": "SHARE_NOT_FOUND",
        }.get(str(code or ""), str(code) if code else None)

    def home_trio_exchange(self) -> dict[str, Any]:
        """``GET /api/phone/home/trio-exchange``: her delivered picks + partner short cards."""

        from . import workbench_curio_view as view

        state, state_reason = self._trio_state()
        her_rows = []
        for row in state.get("her_picks") or []:
            share_id = str(row.get("share_id") or "")
            if not share_id:
                continue
            base = self._pinned_share_status(share_id, row.get("picked_at"))
            her_rows.append(view.her_pick_row(
                share_id=share_id, picked_at=row.get("picked_at"),
                status=str(base.get("status") or "withheld"),
                reason_code=self._trio_reason(base.get("reason_code")),
                text=base.get("text"), text_chars=base.get("text_chars"),
                delivered_at=base.get("delivered_at"), state=base.get("state")))
        partner_rows = [view.partner_pick_row(row) for row in state.get("partner_picks") or []]
        return envelope(True, STATUS_OK, view.trio_block(
            state=state, state_reason=state_reason, her_rows=her_rows,
            partner_rows=partner_rows))

    def home_trio_exchange_action(self, *, action: str,
                                  her_picks: Optional[list[Any]] = None,
                                  partner_picks: Optional[list[Any]] = None) -> dict[str, Any]:
        """One explicit ``set_her_picks`` / ``set_partner_picks``.

        Her picks must be real ``LOCAL_DELIVERED`` shares with a readable current source; no
        validation of a partner card touches Memory / Belief / experience / cognition.
        """

        from . import workbench_curio_view as view

        if action not in ("set_her_picks", "set_partner_picks"):
            return rejected("TRIO_ACTION_UNSUPPORTED", action=action)
        state, state_reason = self._trio_state()
        if state_reason:
            return rejected(state_reason)
        now_iso = self._keepsake_now_iso()
        if action == "set_her_picks":
            ids: list[str] = []
            if her_picks is not None:
                if not isinstance(her_picks, list) or len(her_picks) > view.MAX_TRIO_HER:
                    return rejected("TRIO_HER_PICKS_INVALID")
                for item in her_picks:
                    if not isinstance(item, str) or not item.strip():
                        return rejected("TRIO_HER_PICKS_INVALID")
                    ref = item.strip()
                    if ref in ids:
                        return rejected("TRIO_HER_PICKS_INVALID")
                    ids.append(ref)
            stored_picked = {str(row.get("share_id")): row.get("picked_at")
                             for row in (state.get("her_picks") or [])}
            picks = []
            for ref in ids:
                checked = self._verify_pin_share(ref)
                if not checked.get("ok"):
                    return rejected(self._trio_reason(checked.get("reason_code"))
                                    or "SHARE_NOT_DELIVERED", share_id=ref)
                picks.append({"share_id": ref,
                              "picked_at": stored_picked.get(ref) or now_iso})
            new_state, changed = view.set_her_picks(state, picks, now_iso)
            return self._trio_write_result(state, new_state, changed, action)

        items = partner_picks if partner_picks is not None else []
        if not isinstance(items, list) or len(items) > view.MAX_TRIO_PARTNER:
            return rejected("TRIO_PARTNER_PICKS_INVALID")
        stored_added = {(str(row.get("title") or ""), str(row.get("description") or "")):
                        row.get("added_at")
                        for row in (state.get("partner_picks") or [])}
        rows = []
        for item in items:
            if not isinstance(item, Mapping) or set(item) != {"title", "description"}:
                return rejected("TRIO_PARTNER_PICKS_INVALID")
            title = item.get("title")
            description = item.get("description")
            if not isinstance(title, str) or not title.strip() \
                    or len(title.strip()) > view.MAX_PARTNER_TITLE:
                return rejected("TRIO_PARTNER_TITLE_INVALID")
            if not isinstance(description, str) \
                    or len(description.strip()) > view.MAX_PARTNER_DESCRIPTION:
                return rejected("TRIO_PARTNER_DESCRIPTION_INVALID")
            key = (title.strip(), description.strip())
            rows.append({"title": key[0], "description": key[1],
                         "partner_contributed": True,
                         "added_at": stored_added.get(key) or now_iso})
        new_state, changed = view.set_partner_picks(state, rows, now_iso)
        return self._trio_write_result(state, new_state, changed, action)

    def _trio_write_result(self, state: Mapping[str, Any], new_state: Mapping[str, Any],
                           changed: bool, action: str) -> dict[str, Any]:
        from . import workbench_curio_view as view

        if not changed:
            return envelope(True, STATUS_ALREADY, {
                "action": action, "changed": False,
                "revision": state.get("revision"),
                "updated_at": state.get("updated_at")})
        written = view.write_trio_state(self.root, new_state)
        return envelope(True, STATUS_OK, {
            "action": action, "changed": True,
            "revision": written.get("revision"), "updated_at": written.get("updated_at"),
            "her_picks": len(written.get("her_picks") or []),
            "partner_picks": len(written.get("partner_picks") or []),
            "partner_contributed": bool(written.get("partner_picks"))})

    # -- 衣柜梳妆 / 歌单收藏想试小列表 / 作品旧版安全并排 -----------------------------------

    def _wardrobe_economy(self) -> tuple[Optional[Mapping[str, Any]], Optional[str]]:
        """The real economy document, read-only; missing is named, never filled in."""

        from . import wardrobe_pocket_view as view

        try:
            import world_property_economy as economy
        except Exception:
            return None, view.ECONOMY_UNAVAILABLE
        try:
            return economy.load_state(self.world_home), None
        except Exception:
            return None, view.ECONOMY_STATE_ERROR

    def home_wardrobe(self, *, subject_view: bool = False) -> dict[str, Any]:
        """``GET /api/phone/home/wardrobe``: explicit styling choices + real ownership checks."""

        from . import wardrobe_pocket_view as view

        state, state_reason = view.read_wardrobe_state(self.root)
        economy_state, economy_reason = self._wardrobe_economy()
        checks: dict[str, dict[str, Any]] = {}
        for slot in view.SLOT_NAMES:
            item_id = str(((state.get("selection") or {}).get(slot) or {})
                          .get("item_instance_id") or "")
            if not item_id:
                continue
            if economy_reason is not None:
                checks[slot] = {"status": "unavailable", "reason_code": economy_reason,
                                "slot": slot, "catalog_item_id": None,
                                "item_instance_id": item_id}
                continue
            checks[slot] = view.owned_item_check(
                economy_state, subject=self.world_subject, slot=slot,
                item_instance_id=item_id)
        if economy_reason is not None:
            related = {"status": "unavailable", "reason_code": economy_reason,
                       "items": [], "count": 0}
        else:
            related = view.related_owned_items(economy_state, subject=self.world_subject)
        return envelope(True, STATUS_OK, view.wardrobe_block(
            state=state, state_reason=state_reason, checks=checks, related=related,
            subject_view=bool(subject_view)))

    def home_wardrobe_action(self, *, action: str, values: Optional[Mapping[str, Any]] = None,
                             fields: Optional[list[Any]] = None) -> dict[str, Any]:
        """One explicit ``set`` / ``clear`` of the local styling selection."""

        from . import wardrobe_pocket_view as view

        state, state_reason = view.read_wardrobe_state(self.root)
        if state_reason:
            return rejected(state_reason)
        now_iso = self._keepsake_now_iso()
        if action == "set":
            chosen = dict(values or {})
            if not chosen:
                return rejected("WARDROBE_FIELDS_REQUIRED")
            economy_state, economy_reason = self._wardrobe_economy()
            for field in view.SLOT_FIELDS:
                if not field.endswith("_item_instance_id") or field not in chosen:
                    continue
                slot = field[: -len("_item_instance_id")]
                if economy_reason is not None:
                    return rejected(economy_reason)
                checked = view.owned_item_check(
                    economy_state, subject=self.world_subject, slot=slot,
                    item_instance_id=str(chosen[field]))
                if checked.get("status") != "verified":
                    return rejected(str(checked.get("reason_code") or "ITEM_NOT_OWNED"),
                                    slot=slot)
            try:
                new_state, changed = view.apply_wardrobe_set(state, chosen, now_iso)
            except view.PocketStateError as exc:
                return rejected(exc.reason_code)
        elif action == "clear":
            try:
                new_state, changed = view.apply_wardrobe_clear(state, list(fields or []),
                                                               now_iso)
            except view.PocketStateError as exc:
                return rejected(exc.reason_code)
        else:
            return rejected("WARDROBE_ACTION_UNSUPPORTED", action=action)
        if not changed:
            return envelope(True, STATUS_ALREADY, {
                "action": action, "changed": False,
                "revision": state.get("revision"),
                "updated_at": state.get("updated_at")})
        written = view.write_wardrobe_state(self.root, new_state)
        return envelope(True, STATUS_OK, {
            "action": action, "changed": True,
            "revision": written.get("revision"),
            "updated_at": written.get("updated_at")})

    def pocket_lists(self, *, subject_view: bool = False) -> dict[str, Any]:
        """``GET /api/phone/pocket/lists``: shared-by-default cards + partner suggestions."""

        from . import wardrobe_pocket_view as view

        state, state_reason = view.read_pocket_state(self.root)
        return envelope(True, STATUS_OK, view.pocket_block(
            state=state, state_reason=state_reason, subject_view=bool(subject_view)))

    def pocket_lists_action(self, *, action: str, kind: Optional[str] = None,
                            entry_id: Optional[str] = None,
                            values: Optional[Mapping[str, Any]] = None,
                            status: Optional[str] = None, shared: Optional[bool] = None,
                            operation_key: Optional[str] = None,
                            suggestions: Optional[list[Any]] = None) -> dict[str, Any]:
        """One explicit add / update / remove / partner suggestion replacement."""

        from . import wardrobe_pocket_view as view

        state, state_reason = view.read_pocket_state(self.root)
        if state_reason:
            return rejected(state_reason)
        now_iso = self._keepsake_now_iso()
        chosen = dict(values or {})
        title = chosen.pop("title", None)
        try:
            if action == "add":
                if kind not in view.LIST_KINDS:
                    return rejected("POCKET_LIST_INVALID")
                if not isinstance(operation_key, str) or not operation_key:
                    return rejected("POCKET_OPERATION_KEY_REQUIRED")
                new_state, changed = view.pocket_add(
                    state, kind=kind, entry_id=view.new_entry_id(kind), title=title,
                    values=chosen, status=status, shared=shared,
                    operation_key=operation_key, now_iso=now_iso)
            elif action == "update":
                if kind not in view.LIST_KINDS:
                    return rejected("POCKET_LIST_INVALID")
                if not isinstance(entry_id, str) or not entry_id:
                    return rejected("POCKET_ENTRY_ID_REQUIRED")
                new_state, changed = view.pocket_update(
                    state, kind=kind, entry_id=entry_id, values=chosen, status=status,
                    shared=shared, now_iso=now_iso)
            elif action == "remove":
                if kind not in view.LIST_KINDS:
                    return rejected("POCKET_LIST_INVALID")
                if not isinstance(entry_id, str) or not entry_id:
                    return rejected("POCKET_ENTRY_ID_REQUIRED")
                new_state, changed = view.pocket_remove(
                    state, kind=kind, entry_id=entry_id, now_iso=now_iso)
            elif action == "set_partner_suggestions":
                new_state, changed = view.pocket_set_partner_suggestions(
                    state, list(suggestions or []), now_iso)
            else:
                return rejected("POCKET_ACTION_UNSUPPORTED", action=action)
        except view.PocketStateError as exc:
            return rejected(exc.reason_code)
        if not changed:
            return envelope(True, STATUS_ALREADY, {
                "action": action, "changed": False,
                "revision": state.get("revision"),
                "updated_at": state.get("updated_at")})
        written = view.write_pocket_state(self.root, new_state)
        return envelope(True, STATUS_OK, {
            "action": action, "changed": True,
            "revision": written.get("revision"),
            "updated_at": written.get("updated_at"),
            "counts": {name: len(rows) for name, rows in written["lists"].items()},
            "partner_suggestion_count": len(written["partner_suggestions"])})

    def _pocket_delivered_share_for_version(self, artifact_id: str,
                                            version_id: str) -> dict[str, Any]:
        """The one *exactly* version-bound local delivery, re-verified at read time."""

        from . import wardrobe_pocket_view as view

        listing = self.shares(limit=view.MAX_SHARE_SCAN)
        if not listing.get("ok"):
            return {"ok": False,
                    "reason_code": str(listing.get("reason_code")
                                       or view.SHARES_UNAVAILABLE)}
        data = listing.get("data") if isinstance(listing.get("data"), Mapping) else {}
        for row in data.get("shares") or ():
            if not isinstance(row, Mapping):
                continue
            if str(row.get("state") or "") != "LOCAL_DELIVERED" or not row.get("delivered"):
                continue
            source = row.get("source") if isinstance(row.get("source"), Mapping) else {}
            if str(source.get("artifact_id") or "") != artifact_id \
                    or str(source.get("version_id") or "") != version_id:
                # A delivery of another version never stands in for this one.
                continue
            share_id = str(row.get("share_id") or "")
            detail = self.share(share_id) if share_id else {}
            if not detail.get("ok"):
                continue
            body = detail.get("data") if isinstance(detail.get("data"), Mapping) else {}
            share = body.get("share") if isinstance(body.get("share"), Mapping) else {}
            receipt = body.get("receipt") if isinstance(body.get("receipt"), Mapping) else {}
            if not body.get("delivered") or not receipt:
                continue
            if receipt.get("local_only") is False \
                    or receipt.get("external_channel_sent") is True:
                continue
            if receipt.get("audience") not in (None, "", "local:owner") \
                    or receipt.get("channel") not in (None, "", "local_inbox"):
                continue
            if not body.get("source_readable") or share.get("text_withheld"):
                continue
            text = share.get("text")
            if not isinstance(text, str):
                continue
            return {"ok": True, "share_id": share_id, "text": text,
                    "delivered_at": receipt.get("delivered_at") or share.get("updated_at")}
        return {"ok": False, "reason_code": view.VERSION_NOT_DELIVERED}

    def pocket_work_revisit(self, *, artifact_id: str, left_version_id: str,
                            right_version_id: str, subject_view: bool = False
                            ) -> dict[str, Any]:
        """``GET /api/phone/pocket/work-revisit``: two real versions, safe bounded summaries."""

        from . import wardrobe_pocket_view as view

        if left_version_id == right_version_id:
            return rejected("VERSIONS_NOT_DISTINCT")
        project_answer = self.project(artifact_id)
        if not project_answer.get("ok"):
            return _project_failure(project_answer, "PROJECT_UNAVAILABLE")
        project = project_answer.get("project") \
            if isinstance(project_answer.get("project"), Mapping) else {}
        if str(project.get("artifact_type") or "") != "project":
            return rejected(view.NOT_A_PROJECT, artifact_id=artifact_id)
        try:
            from life_sharing_service import is_share_title
        except Exception:
            return unavailable("SHARE_GUARD_UNAVAILABLE")
        if is_share_title(project.get("title")):
            # A share envelope is a derived delivery, not a work to compare; reading it through
            # this surface would bypass the share disclosure boundary.
            return rejected("SHARE_ENVELOPE_NOT_A_PROJECT", artifact_id=artifact_id)
        lifecycle = str(project.get("lifecycle") or "")
        basis = view.OWNER_EXCERPT_BASIS if subject_view else view.DELIVERED_SHARE_BASIS

        versions: dict[str, dict[str, Any]] = {}
        withheld_content: dict[str, bool] = {}
        for side, wanted in (("left", left_version_id), ("right", right_version_id)):
            read = self.project_version(artifact_id, wanted)
            if not read.get("ok"):
                reason = str(read.get("reason_code") or "PROJECT_VERSION_UNAVAILABLE")
                if reason == view.VERSION_NOT_IN_ARTIFACT:
                    return rejected(reason, artifact_id=artifact_id)
                return _project_failure(read, "PROJECT_VERSION_UNAVAILABLE")
            version = read.get("version") if isinstance(read.get("version"), Mapping) else {}
            if str(version.get("version_id") or "") != wanted \
                    or str(read.get("artifact_id") or "") != artifact_id:
                return rejected(view.VERSION_NOT_IN_ARTIFACT, artifact_id=artifact_id)
            versions[side] = dict(version)
            withheld_content[side] = bool(
                read.get("content_withheld") or version.get("derived_text_withheld"))

        if lifecycle != "ACTIVE":
            sides = {side: view.withheld_side(
                version_id=wanted, version_number=versions[side].get("version_number"),
                reason_code=view.ARCHIVED_SOURCE, excerpt_basis=basis)
                for side, wanted in (("left", left_version_id),
                                     ("right", right_version_id))}
            return envelope(True, STATUS_OK, view.work_revisit_block(
                artifact_id=artifact_id, subject_view=bool(subject_view),
                left=sides["left"], right=sides["right"],
                artifact_status="withheld", artifact_reason=view.ARCHIVED_SOURCE))

        sides: dict[str, dict[str, Any]] = {}
        for side, wanted in (("left", left_version_id), ("right", right_version_id)):
            version = versions[side]
            number = version.get("version_number")
            if subject_view:
                if str(version.get("lifecycle") or "ACTIVE") != "ACTIVE":
                    sides[side] = view.withheld_side(
                        version_id=wanted, version_number=number,
                        reason_code=view.ARCHIVED_SOURCE, excerpt_basis=basis)
                elif withheld_content.get(side):
                    sides[side] = view.withheld_side(
                        version_id=wanted, version_number=number,
                        reason_code=view.SOURCE_NOT_READABLE, excerpt_basis=basis)
                else:
                    sides[side] = view.owner_version_side(
                        version=version, version_id=wanted,
                        lifecycle=str(version.get("lifecycle") or "ACTIVE"))
            else:
                delivered = self._pocket_delivered_share_for_version(artifact_id, wanted)
                if delivered.get("ok"):
                    sides[side] = view.delivered_share_side(
                        version_id=wanted, version_number=number,
                        share_id=str(delivered.get("share_id") or ""),
                        text=str(delivered.get("text") or ""),
                        delivered_at=delivered.get("delivered_at"))
                else:
                    sides[side] = view.withheld_side(
                        version_id=wanted, version_number=number,
                        reason_code=str(delivered.get("reason_code")
                                        or view.VERSION_NOT_DELIVERED),
                        excerpt_basis=basis)
        return envelope(True, STATUS_OK, view.work_revisit_block(
            artifact_id=artifact_id, subject_view=bool(subject_view),
            left=sides["left"], right=sides["right"]))

    # -- 居家收官：重逢锚点 / 夜茶菜谱 / 待定小约定 ---------------------------------------

    def _home_clock(self) -> dict[str, Any]:
        """The same business clock the rest of the phone uses; unreadable is named, not guessed."""

        root = self._desk_state_root()
        if root is not None:
            return today_business_day(root)
        moment = datetime.now(tz=TODAY_TZ).replace(microsecond=0)
        return {"source": "system_clock", "reason_code": "LIFE_STATE_UNBOUND",
                "logical_at": moment.isoformat(timespec="seconds"),
                "day": moment.date().isoformat(), "run_id": None, "domain_id": None}

    @staticmethod
    def _clock_offset(clock: Mapping[str, Any]) -> Optional[str]:
        value = clock.get("logical_at") if isinstance(clock, Mapping) else None
        try:
            moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.isoformat(timespec="seconds")[-6:]

    def _reunion_location(self, world_result: Mapping[str, Any]) -> dict[str, Any]:
        data = world_result.get("data") if isinstance(world_result.get("data"), Mapping) else {}
        location = data.get("location") if isinstance(data.get("location"), Mapping) else {}
        if not world_result.get("ok"):
            return {"status": "unavailable",
                    "reason_code": str(world_result.get("reason_code") or "WORLD_UNAVAILABLE")}
        if location.get("status") != "available":
            return {"status": str(location.get("status") or "unavailable"),
                    "reason_code": str(location.get("reason_code") or "LOCATION_UNAVAILABLE")}
        place = str(location.get("place_id") or "")
        return {"status": "available", "place_id": place or None,
                "area_id": location.get("area_id") or None,
                "home": bool(location.get("home") or place == "home"),
                "reason_code": None}

    def _reunion_reading(self) -> dict[str, Any]:
        """The bounded reading identity + a *saved* stop; no body and no page note."""

        answer = self.reading_material()
        if not answer.get("ok"):
            return {"status": "unavailable",
                    "reason_code": str(answer.get("reason_code") or "READING_UNAVAILABLE")}
        data = answer.get("data") if isinstance(answer.get("data"), Mapping) else {}
        bookmark = data.get("bookmark") if isinstance(data.get("bookmark"), Mapping) else None
        page: Optional[int] = None
        page_reason: Optional[str] = None
        if isinstance(bookmark, Mapping):
            raw_page = bookmark.get("page")
            if isinstance(raw_page, int) and not isinstance(raw_page, bool) and raw_page > 0:
                if not bookmark.get("needs_reread"):
                    page = raw_page
                else:
                    page_reason = "READING_POSITION_STALE"
            else:
                page_reason = "READING_POSITION_UNKNOWN"
        else:
            page_reason = "READING_POSITION_UNKNOWN"
        return {"status": "available",
                "artifact_id": data.get("artifact_id"),
                "source_revision": data.get("source_revision"),
                "version_number": data.get("version_number"),
                "page": page,
                "pages": data.get("pages"),
                "reason_code": page_reason}

    def _reunion_delivered(self) -> dict[str, Any]:
        """Only real ``LOCAL_DELIVERED`` shares enter the stable set; drafts never do."""

        from . import home_hearth_view as view

        listing = self.shares(limit=view.MAX_ANCHOR_SHARES)
        if not listing.get("ok"):
            return {"status": "unavailable",
                    "reason_code": str(listing.get("reason_code") or "SHARES_UNAVAILABLE")}
        data = listing.get("data") if isinstance(listing.get("data"), Mapping) else {}
        rows = [row for row in (data.get("shares") or []) if isinstance(row, Mapping)]
        ids = []
        for row in rows:
            if str(row.get("state") or "") != "LOCAL_DELIVERED":
                continue
            share_id = str(row.get("share_id") or "")
            if share_id and share_id not in ids:
                ids.append(share_id)
        return {"status": "available",
                "share_ids": ids[:view.MAX_ANCHOR_SHARES],
                "count": len(ids),
                "truncated": bool(data.get("truncated")
                                  or len(rows) >= view.MAX_ANCHOR_SHARES),
                "reason_code": None}

    def _reunion_items(self) -> dict[str, Any]:
        """Owned instances joined to their real position; buying is not delivery/placement."""

        from . import home_hearth_view as view

        curios = self.home_curios()
        if not curios.get("ok"):
            return {"status": "unavailable",
                    "reason_code": str(curios.get("reason_code") or "ITEMS_UNAVAILABLE")}
        data = curios.get("data") if isinstance(curios.get("data"), Mapping) else {}
        if data.get("reason_code"):
            # Genesis-not-initialised is a missing source, not "she owns zero things".
            return {"status": "unavailable",
                    "reason_code": str(data.get("reason_code"))}
        rows = []
        for row in (data.get("items") or []):
            if not isinstance(row, Mapping):
                continue
            item_id = str(row.get("item_instance_id") or "")
            if not item_id:
                continue
            rows.append({"item_instance_id": item_id,
                         "name": row.get("name"),
                         "placement": row.get("position") or {}})
        return {"status": "available",
                "items": rows[:view.MAX_ANCHOR_ITEMS],
                "count": len(rows),
                "truncated": len(rows) > view.MAX_ANCHOR_ITEMS,
                "reason_code": None}

    def _reunion_snapshot(self) -> dict[str, Any]:
        clock = self._home_clock()
        location = self._reunion_location(self.world())
        reading = self._reunion_reading()
        delivered = self._reunion_delivered()
        items = self._reunion_items()
        complete = all(block.get("status") == "available"
                       for block in (location, reading, delivered, items))
        return {"clock": clock, "clock_offset": self._clock_offset(clock),
                "location": location, "reading": reading,
                "delivered": delivered, "items": items, "complete": bool(complete)}

    @staticmethod
    def _home_now_iso(clock: Mapping[str, Any]) -> str:
        value = clock.get("logical_at") if isinstance(clock, Mapping) else None
        return str(value) if isinstance(value, str) and value.strip() else \
            datetime.now(tz=TODAY_TZ).replace(microsecond=0).isoformat(timespec="seconds")

    def home_reunion_anchor(self) -> dict[str, Any]:
        """``GET /api/phone/home/reunion-anchor``: the stored safe snapshot vs real owners."""

        from . import home_hearth_view as view

        state, state_reason = view.read_reunion_state(self.root)
        snapshot = self._reunion_snapshot()
        return envelope(True, STATUS_OK, view.reunion_block(
            state=state, state_reason=state_reason, snapshot=snapshot,
            now_iso=self._home_now_iso(snapshot["clock"])))

    def home_reunion_capture(self) -> dict[str, Any]:
        """``POST /api/phone/home/reunion-anchor``: one explicit safe capture.

        The client supplies nothing but ``action``: the snapshot is read here from the real
        owners.  Re-capturing the same disclosed state never bumps the revision.
        """

        from . import home_hearth_view as view

        state, state_reason = view.read_reunion_state(self.root)
        if state_reason:
            return rejected(state_reason)
        snapshot = self._reunion_snapshot()
        now_iso = self._home_now_iso(snapshot["clock"])
        new_state, changed = view.capture_anchor(state, snapshot, now_iso)
        if not changed:
            return envelope(True, STATUS_ALREADY, {
                "action": "capture", "changed": False,
                "revision": state.get("revision"),
                "updated_at": state.get("updated_at"),
                "has_anchor": bool(state.get("anchor"))})
        written = view.write_reunion_state(self.root, new_state)
        anchor = written.get("anchor") or {}
        return envelope(True, STATUS_OK, {
            "action": "capture", "changed": True,
            "revision": written.get("revision"),
            "updated_at": written.get("updated_at"),
            "has_anchor": True,
            "captured_at_local": anchor.get("captured_at_local"),
            "complete": bool(anchor.get("complete"))})

    def _recipe_references(self) -> dict[str, Any]:
        """A bounded read of real purchases/consumptions; never evidence of cooking."""

        from . import home_hearth_view as view

        try:
            import world_property_economy as economy

            consumable_ids = {str(entry.get("catalog_item_id") or "")
                              for entry in economy.phone_catalog_entries()}
        except Exception:
            consumable_ids = set()
        if not consumable_ids:
            return {"status": "unavailable",
                    "reason_code": "CONSUMABLE_CATALOG_UNAVAILABLE",
                    "items": [], "count": 0}
        answer = self.inventory()
        if not answer.get("ok"):
            return {"status": "unavailable",
                    "reason_code": str(answer.get("reason_code") or "INVENTORY_UNAVAILABLE"),
                    "items": [], "count": 0}
        data = answer.get("data") if isinstance(answer.get("data"), Mapping) else {}
        rows = []
        matched = 0
        for entry in (data.get("items") or []):
            if not isinstance(entry, Mapping):
                continue
            catalog_id = str(entry.get("catalog_item_id") or "")
            if catalog_id not in consumable_ids:
                continue
            matched += 1
            consumed = bool(entry.get("consumed"))
            rows.append({
                "item_instance_id": entry.get("item_instance_id"),
                "name": entry.get("name"),
                "catalog_item_id": catalog_id,
                "purchase": {
                    "status": "available",
                    "delivery_status": entry.get("delivery_status"),
                    "materialization": entry.get("materialization"),
                    "location": entry.get("location"),
                },
                "consumption": (
                    {"status": "consumed", "receipt_id": entry.get("receipt_id")}
                    if consumed else
                    {"status": "not_consumed", "receipt_id": None}),
                "source": "world_phone_shopping.inventory",
            })
        return {
            "status": "available", "reason_code": None,
            "items": rows[:view.MAX_RECIPE_REFERENCE],
            "count": matched,
            "truncated": matched > view.MAX_RECIPE_REFERENCE,
            "consumption_read_surface": "world_phone_shopping.inventory",
            "not_evidence_of_cooking": view.NOT_EVIDENCE_OF_COOKING,
            "consumption_claimed": view.CONSUMPTION_CLAIMED,
        }

    def home_recipes(self) -> dict[str, Any]:
        """``GET /api/phone/home/recipes``: explicit shared recipe cards + real references."""

        from . import home_hearth_view as view

        state, state_reason = view.read_recipes_state(self.root)
        return envelope(True, STATUS_OK, view.recipes_block(
            state=state, state_reason=state_reason,
            references=self._recipe_references()))

    def home_recipe_upsert(self, *, recipe_id: Optional[str], title: Any, memo: Any,
                           author_side: str) -> dict[str, Any]:
        """One explicit create/update of a shared recipe card; the author side is immutable."""

        from . import home_hearth_view as view

        state, state_reason = view.read_recipes_state(self.root)
        if state_reason:
            return rejected(state_reason)
        now_iso = self._home_now_iso(self._home_clock())
        new_state, changed, card, error = view.upsert_recipe(
            state, recipe_id=recipe_id, title=title, memo=memo,
            author_side=author_side, now_iso=now_iso)
        if error:
            return rejected(error)
        target_id = str((card or {}).get("recipe_id") or recipe_id or "")
        if not changed:
            return envelope(True, STATUS_ALREADY, {
                "action": "upsert", "changed": False,
                "revision": state.get("revision"),
                "updated_at": state.get("updated_at"),
                "recipe_id": target_id,
                "recipe": card})
        written = view.write_recipes_state(self.root, new_state)
        updated = next((row for row in written.get("recipes") or []
                        if row.get("recipe_id") == target_id), None)
        return envelope(True, STATUS_OK, {
            "action": "upsert", "changed": True,
            "revision": written.get("revision"),
            "updated_at": written.get("updated_at"),
            "recipe_id": target_id, "recipe": updated})

    def _arrangements_summary(self, subject_view: bool) -> dict[str, Any]:
        """Safe arrangement facts only: counts/status/day by default, titles with her capability."""

        answer = self.today_arrangements()
        if not answer.get("ok"):
            # An unreadable arrangement source is a named unknown, never "nothing today".
            return {"status": "unknown",
                    "reason_code": str(answer.get("reason_code") or "ARRANGEMENTS_UNAVAILABLE"),
                    "day": None, "plan_count": None, "obligation_count": None,
                    "execution_status": None, "truncated": None}
        data = answer.get("data") if isinstance(answer.get("data"), Mapping) else {}
        plan = data.get("plan") if isinstance(data.get("plan"), Mapping) else {}
        obligations = data.get("obligations") if isinstance(data.get("obligations"),
                                                              Mapping) else {}
        sources = data.get("sources") if isinstance(data.get("sources"), Mapping) else {}
        execution = sources.get("execution") if isinstance(sources.get("execution"),
                                                            Mapping) else {}
        summary: dict[str, Any] = {
            "status": "available", "reason_code": None,
            "day": data.get("day"),
            "plan_count": _int_or_none(plan.get("count")),
            "obligation_count": _int_or_none(obligations.get("count")),
            "execution_status": execution.get("state"),
            "truncated": bool(obligations.get("truncated")),
        }
        if subject_view:
            entries = [row for row in (plan.get("entries") or [])
                       if isinstance(row, Mapping)]
            summary["plans_detail"] = [
                {"title": row.get("title"), "date": row.get("date"),
                 "execution": (row.get("execution") or {}).get("status")
                 if isinstance(row.get("execution"), Mapping) else None}
                for row in entries[:10]]
            summary["plan_titles"] = [str(row.get("title")) for row in entries[:10]
                                      if row.get("title")]
        return summary

    def home_tentative_plans(self, *, subject_view: bool = False) -> dict[str, Any]:
        """``GET /api/phone/home/tentative-plans``: today's safe summary + local tentative list."""

        from . import home_hearth_view as view

        state, state_reason = view.read_plans_state(self.root)
        return envelope(True, STATUS_OK, view.plans_block(
            state=state, state_reason=state_reason,
            arrangements=self._arrangements_summary(bool(subject_view)),
            subject_view=bool(subject_view)))

    def home_tentative_plan_action(self, *, action: str, actor_is_her: bool,
                                   plan_id: Optional[str] = None, title: Any = None,
                                   window_note: Any = None,
                                   proposer: Optional[str] = None) -> dict[str, Any]:
        """One explicit propose/reschedule/drop; nothing here reaches core arrangements."""

        from . import home_hearth_view as view

        if action not in ("propose", "reschedule", "drop"):
            return rejected("PLAN_ACTION_UNSUPPORTED", action=action)
        state, state_reason = view.read_plans_state(self.root)
        if state_reason:
            return rejected(state_reason)
        now_iso = self._home_now_iso(self._home_clock())
        if action == "propose":
            if proposer not in view.PLAN_PROPOSERS:
                return rejected("PLAN_PROPOSER_INVALID")
            if proposer == "her" and not actor_is_her:
                return rejected("PLAN_PROPOSER_FORBIDDEN")
            new_state, changed, card, error = view.propose_plan(
                state, plan_id=plan_id, title=title, window_note=window_note,
                proposer=proposer, now_iso=now_iso)
        else:
            existing = view.find_plan(state, plan_id or "") if plan_id else None
            if existing is None:
                return rejected("PLAN_NOT_FOUND")
            if existing.get("proposer") == "her" and not actor_is_her:
                return rejected("PLAN_FORBIDDEN")
            if action == "reschedule":
                new_state, changed, card, error = view.reschedule_plan(
                    state, plan_id=plan_id or "", window_note=window_note, now_iso=now_iso)
            else:
                new_state, changed, card, error = view.drop_plan(
                    state, plan_id=plan_id or "", now_iso=now_iso)
        if error:
            return rejected(error)
        target_id = str((card or {}).get("plan_id") or plan_id or "")
        if not changed:
            return envelope(True, STATUS_ALREADY, {
                "action": action, "changed": False,
                "revision": state.get("revision"),
                "updated_at": state.get("updated_at"),
                "plan_id": target_id,
                "plan": view.plan_row(card) if isinstance(card, Mapping) else None})
        written = view.write_plans_state(self.root, new_state)
        updated = next((row for row in written.get("plans") or []
                        if row.get("plan_id") == target_id), None)
        return envelope(True, STATUS_OK, {
            "action": action, "changed": True,
            "revision": written.get("revision"),
            "updated_at": written.get("updated_at"),
            "plan_id": target_id,
            "plan": view.plan_row(updated) if isinstance(updated, Mapping) else None})

    # -- 房间外日常：长椅书店 / 散步路线节日集市 / 可重访作品展与共同新地点草图 ------------

    def _outing_world_context(self) -> dict[str, Any]:
        """Pure read of real World outdoor places and local business date (zero write/tick)."""

        from . import outing_places_view as view

        clock = self._home_clock()
        now_iso = self._home_now_iso(clock)
        fallback_date = str(clock.get("day")
                            or datetime.now(tz=TODAY_TZ).date().isoformat())

        world_result = self.world()
        world_ok = bool(world_result.get("ok"))
        data = world_result.get("data") if isinstance(world_result.get("data"), Mapping) else {}
        world_meta = data.get("world") if isinstance(data.get("world"), Mapping) else {}
        raw_local_date = world_meta.get("local_date")
        if (world_ok and isinstance(raw_local_date, str)
                and re.match(r"^\d{4}-\d{2}-\d{2}$", raw_local_date.strip())):
            local_date = raw_local_date.strip()
        else:
            local_date = fallback_date

        if not world_ok or str(world_meta.get("status") or "") != "available":
            reason = str(world_result.get("reason_code")
                         or world_meta.get("reason_code")
                         or "WORLD_UNAVAILABLE")
            return {
                "available": False,
                "reason_code": reason,
                "local_date": local_date,
                "now_iso": now_iso,
                "places": view.classify_world_places(()),
            }

        raw_places: list[dict[str, Any]] = []
        seen_ids: dict[str, dict[str, Any]] = {}
        home_place_id = "home"

        location_block = data.get("location") \
            if isinstance(data.get("location"), Mapping) else {}
        current_pid = str(location_block.get("place_id") or "").strip()
        if current_pid and not bool(location_block.get("home")):
            entry = {
                "place_id": current_pid,
                "name": location_block.get("place_name") or current_pid,
                "kind": str(location_block.get("kind") or ""),
            }
            raw_places.append(entry)
            seen_ids[current_pid] = entry

        destinations = data.get("destinations") \
            if isinstance(data.get("destinations"), Mapping) else {}
        for item in (destinations.get("places") or ()):
            if not isinstance(item, Mapping):
                continue
            pid = str(item.get("place_id") or item.get("location_id") or "").strip()
            if not pid or pid in seen_ids:
                continue
            entry = dict(item)
            entry["place_id"] = pid
            raw_places.append(entry)
            seen_ids[pid] = entry

        try:
            import world_runtime_data as wrd

            wdata = wrd.active_world(self.environment)
            home_place_id = str(getattr(wdata, "home_location_id", None) or "home")
            locations = getattr(wdata, "locations", None)
            if isinstance(locations, Mapping):
                for loc_id, loc_meta in locations.items():
                    if not isinstance(loc_meta, Mapping):
                        continue
                    pid = str(loc_meta.get("location_id") or loc_id or "").strip()
                    if not pid:
                        continue
                    if pid in seen_ids:
                        seen_ids[pid].setdefault("kind", loc_meta.get("kind") or "")
                        seen_ids[pid].setdefault("zone_id", loc_meta.get("zone_id") or "")
                        seen_ids[pid].setdefault("description",
                                                 loc_meta.get("description") or "")
                        if not seen_ids[pid].get("name") or seen_ids[pid]["name"] == pid:
                            seen_ids[pid]["name"] = loc_meta.get("name") or pid
                    else:
                        entry = dict(loc_meta)
                        entry["place_id"] = pid
                        raw_places.append(entry)
                        seen_ids[pid] = entry
        except Exception:
            pass

        return {
            "available": True,
            "reason_code": None,
            "local_date": local_date,
            "now_iso": now_iso,
            "places": view.classify_world_places(raw_places, home_place_id=home_place_id),
        }

    def _outing_wallet_summary(self) -> dict[str, Any]:
        """Pure read of real virtual-currency balance from `world_property_economy.load_state`."""

        from . import outing_places_view as view

        try:
            import world_property_economy as economy
        except Exception:
            return {"wallet_status": "unavailable", "balance_jpy": None,
                    "reason_code": view.ECONOMY_UNAVAILABLE}
        try:
            doc = economy.load_state(self.world_home)
        except Exception as exc:
            code = getattr(exc, "reason_code", None) or view.ECONOMY_STATE_ERROR
            return {"wallet_status": "unavailable", "balance_jpy": None,
                    "reason_code": str(code)}
        if doc is None:
            return {"wallet_status": "unavailable", "balance_jpy": None,
                    "reason_code": view.GENESIS_NOT_INITIALIZED}
        try:
            balance = int(economy._balance(doc))
        except Exception:
            return {"wallet_status": "unavailable", "balance_jpy": None,
                    "reason_code": view.ECONOMY_STATE_ERROR}
        return {"wallet_status": "available", "balance_jpy": balance, "reason_code": None}

    def outing_bench_bookstore(self, *, subject_view: bool = False) -> dict[str, Any]:
        """``GET /api/phone/outing/bench-bookstore``: riverside bench trace + bookstore window."""

        from . import outing_places_view as view

        state, state_reason = view.read_bench_bookstore_state(self.root)
        world_ctx = self._outing_world_context()
        return envelope(True, STATUS_OK, view.bench_bookstore_block(
            state=state, state_reason=state_reason,
            world_places=world_ctx["places"],
            world_available=bool(world_ctx["available"]),
            world_reason=world_ctx["reason_code"],
            local_date=world_ctx["local_date"],
            subject_view=bool(subject_view)))

    def outing_bench_bookstore_action(
        self,
        *,
        action: str,
        place_id: Optional[str] = None,
        note: Optional[str] = None,
        note_supplied: bool = False,
        shared: Optional[bool] = None,
        operation_key: Optional[str] = None,
        visit_id: Optional[str] = None,
        slot_index: Optional[int] = None,
    ) -> dict[str, Any]:
        """``POST /api/phone/outing/bench-bookstore``: explicit bench/window mutations."""

        from . import outing_places_view as view

        state, state_reason = view.read_bench_bookstore_state(self.root)
        if state_reason:
            return rejected(state_reason)
        world_ctx = self._outing_world_context()
        now_iso = world_ctx["now_iso"]
        local_date = world_ctx["local_date"]

        try:
            if action == "log_bench_visit":
                if not world_ctx["available"]:
                    return unavailable(str(world_ctx["reason_code"] or "WORLD_UNAVAILABLE"))
                valid_ids = set((world_ctx["places"].get("places_by_id") or {}).keys())
                new_state, item, changed = view.apply_log_bench_visit(
                    state, place_id=str(place_id or ""), valid_place_ids=valid_ids,
                    operation_key=str(operation_key or ""), note=note, shared=shared,
                    now_iso=now_iso)
                detail: dict[str, Any] = {"visit_id": item["visit_id"], "visit": item}
            elif action == "set_bench_visibility":
                new_state, item, changed = view.apply_set_bench_visibility(
                    state, visit_id=str(visit_id or ""), shared=bool(shared), now_iso=now_iso)
                detail = {"visit_id": item["visit_id"], "shared": item["shared"]}
            elif action == "bookmark_window_book":
                new_state, item, changed = view.apply_bookmark_window_book(
                    state, slot_index=int(slot_index) if slot_index is not None else -1,
                    local_date=local_date, shared=shared, note=note,
                    note_supplied=bool(note_supplied), now_iso=now_iso)
                detail = {"bookmark_id": item["bookmark_id"], "bookmark": item}
            elif action == "set_partner_window_note":
                new_state, item, changed = view.apply_set_partner_window_note(
                    state, slot_index=int(slot_index) if slot_index is not None else -1,
                    local_date=local_date, note=str(note or ""), now_iso=now_iso)
                detail = {"partner_window_note": item}
            elif action == "clear_partner_window_note":
                new_state, changed = view.apply_clear_partner_window_note(
                    state, now_iso=now_iso)
                detail = {"partner_window_note": None}
            else:
                return rejected("BENCH_BOOKSTORE_ACTION_UNSUPPORTED", action=action)
        except view.OutingStateError as exc:
            return rejected(exc.reason_code)

        if not changed:
            return envelope(True, STATUS_ALREADY, {
                "action": action, "changed": False,
                "revision": state.get("revision"),
                "updated_at": state.get("updated_at"),
                **detail,
            })
        written = view.write_bench_bookstore_state(self.root, new_state)
        return envelope(True, STATUS_OK, {
            "action": action, "changed": True,
            "revision": written.get("revision"),
            "updated_at": written.get("updated_at"),
            **detail,
        })

    def outing_routes_market(self, *, subject_view: bool = False) -> dict[str, Any]:
        """``GET /api/phone/outing/routes-market``: walk routes/photos + virtual festival market."""

        from . import outing_places_view as view

        state, state_reason = view.read_routes_market_state(self.root)
        world_ctx = self._outing_world_context()
        album_readability: dict[str, bool] = {}
        for route in state.get("routes") or ():
            if not isinstance(route, Mapping):
                continue
            if not subject_view and not route.get("shared"):
                continue
            album_id = route.get("album_artifact_id")
            if isinstance(album_id, str) and album_id and album_id not in album_readability:
                detail = self.album_detail(album_id)
                image = detail.get("image") if isinstance(detail.get("image"), Mapping) else {}
                album_readability[album_id] = bool(
                    detail.get("ok") and str(image.get("lifecycle") or "ACTIVE") == "ACTIVE")
        wallet = self._outing_wallet_summary()
        return envelope(True, STATUS_OK, view.routes_market_block(
            state=state, state_reason=state_reason,
            world_places=world_ctx["places"],
            world_available=bool(world_ctx["available"]),
            world_reason=world_ctx["reason_code"],
            album_readability=album_readability,
            wallet=wallet,
            local_date=world_ctx["local_date"],
            subject_view=bool(subject_view)))

    def outing_routes_market_action(
        self,
        *,
        action: str,
        route_id: Optional[str] = None,
        title: Optional[str] = None,
        place_ids: Optional[list[Any]] = None,
        album_artifact_id: Optional[str] = None,
        album_supplied: bool = False,
        caption: Optional[str] = None,
        caption_supplied: bool = False,
        shared: Optional[bool] = None,
        stall_code: Optional[str] = None,
        memo: Optional[str] = None,
        budget_cap_jpy: Optional[int] = None,
        budget_supplied: bool = False,
        tip: Optional[str] = None,
    ) -> dict[str, Any]:
        """``POST /api/phone/outing/routes-market``: explicit route/market-memo mutations."""

        from . import outing_places_view as view

        state, state_reason = view.read_routes_market_state(self.root)
        if state_reason:
            return rejected(state_reason)
        world_ctx = self._outing_world_context()
        now_iso = world_ctx["now_iso"]

        try:
            if action == "save_walk_route":
                if not world_ctx["available"]:
                    return unavailable(str(world_ctx["reason_code"] or "WORLD_UNAVAILABLE"))
                if album_artifact_id is not None:
                    detail_check = self.album_detail(album_artifact_id)
                    image = (detail_check.get("image")
                             if isinstance(detail_check.get("image"), Mapping) else {})
                    if not detail_check.get("ok"):
                        if str(detail_check.get("reason_code") or "") == "SUPPLY_UNAVAILABLE":
                            return unavailable("SUPPLY_UNAVAILABLE")
                        return rejected("ALBUM_IMAGE_NOT_FOUND",
                                        album_artifact_id=album_artifact_id)
                    if str(image.get("lifecycle") or "ACTIVE") != "ACTIVE":
                        return rejected("ALBUM_IMAGE_NOT_FOUND",
                                        album_artifact_id=album_artifact_id)
                valid_ids = set((world_ctx["places"].get("places_by_id") or {}).keys())
                new_state, item, changed = view.apply_save_walk_route(
                    state, route_id=route_id, title=str(title or ""),
                    place_ids=place_ids if isinstance(place_ids, list) else [],
                    valid_place_ids=valid_ids,
                    album_artifact_id=album_artifact_id, album_supplied=bool(album_supplied),
                    caption=caption, caption_supplied=bool(caption_supplied),
                    shared=shared, now_iso=now_iso)
                detail = {"route_id": item["route_id"], "route": item}
            elif action == "remove_walk_route":
                new_state, changed = view.apply_remove_walk_route(
                    state, route_id=str(route_id or ""), now_iso=now_iso)
                detail = {"route_id": str(route_id or "")}
            elif action == "note_market_wish":
                new_state, item, changed = view.apply_note_market_wish(
                    state, stall_code=str(stall_code or ""), memo=str(memo or ""),
                    budget_cap_jpy=budget_cap_jpy, budget_supplied=bool(budget_supplied),
                    shared=shared, now_iso=now_iso)
                detail = {"wish_id": item["wish_id"], "wish": item}
            elif action == "set_partner_market_tip":
                new_state, item, changed = view.apply_set_partner_market_tip(
                    state, stall_code=str(stall_code or ""), tip=str(tip or ""),
                    now_iso=now_iso)
                detail = {"partner_market_tip": item}
            elif action == "clear_partner_market_tip":
                new_state, changed = view.apply_clear_partner_market_tip(
                    state, now_iso=now_iso)
                detail = {"partner_market_tip": None}
            else:
                return rejected("ROUTES_MARKET_ACTION_UNSUPPORTED", action=action)
        except view.OutingStateError as exc:
            return rejected(exc.reason_code)

        if not changed:
            return envelope(True, STATUS_ALREADY, {
                "action": action, "changed": False,
                "revision": state.get("revision"),
                "updated_at": state.get("updated_at"),
                **detail,
            })
        written = view.write_routes_market_state(self.root, new_state)
        return envelope(True, STATUS_OK, {
            "action": action, "changed": True,
            "revision": written.get("revision"),
            "updated_at": written.get("updated_at"),
            **detail,
        })

    def _outing_project_exhibit(self, exhibit: Mapping[str, Any], *,
                                subject_view: bool) -> dict[str, Any]:
        """Re-verify one stored gallery exhibit against `PrivateProjectService` & share inbox."""

        from . import outing_places_view as view

        artifact_id = str(exhibit.get("artifact_id") or "")
        version_id = str(exhibit.get("version_id") or "")

        project_answer = self.project(artifact_id)
        if not project_answer.get("ok"):
            reason = str(project_answer.get("reason_code") or view.SOURCE_NOT_READABLE)
            if reason.startswith("PROJECT_IS_"):
                reason = view.NOT_A_PROJECT
            return view.format_exhibit_projection(
                exhibit, subject_view=subject_view, source_status="withheld",
                reason_code=reason)

        project = project_answer.get("project") \
            if isinstance(project_answer.get("project"), Mapping) else {}
        if str(project.get("artifact_type") or "") != "project":
            return view.format_exhibit_projection(
                exhibit, subject_view=subject_view, source_status="withheld",
                reason_code=view.NOT_A_PROJECT)

        try:
            from life_sharing_service import is_share_title
        except Exception:
            return view.format_exhibit_projection(
                exhibit, subject_view=subject_view, source_status="withheld",
                reason_code="SHARE_GUARD_UNAVAILABLE")
        if is_share_title(project.get("title")):
            return view.format_exhibit_projection(
                exhibit, subject_view=subject_view, source_status="withheld",
                reason_code=view.SHARE_ENVELOPE_NOT_A_PROJECT)

        ver_read = self.project_version(artifact_id, version_id)
        version = ver_read.get("version") \
            if (ver_read.get("ok") and isinstance(ver_read.get("version"), Mapping)) else {}
        version_number = version.get("version_number")

        if str(project.get("lifecycle") or "") != "ACTIVE":
            return view.format_exhibit_projection(
                exhibit, subject_view=subject_view, source_status="withheld",
                reason_code=view.ARCHIVED_SOURCE, version_number=version_number)

        if not ver_read.get("ok"):
            reason = str(ver_read.get("reason_code") or view.VERSION_UNREADABLE)
            return view.format_exhibit_projection(
                exhibit, subject_view=subject_view, source_status="withheld",
                reason_code=reason)
        if (str(version.get("version_id") or "") != version_id
                or str(ver_read.get("artifact_id") or "") != artifact_id):
            return view.format_exhibit_projection(
                exhibit, subject_view=subject_view, source_status="withheld",
                reason_code=view.VERSION_NOT_IN_ARTIFACT)
        if str(version.get("lifecycle") or "ACTIVE") != "ACTIVE":
            return view.format_exhibit_projection(
                exhibit, subject_view=subject_view, source_status="withheld",
                reason_code=view.ARCHIVED_SOURCE, version_number=version_number)

        if subject_view:
            if ver_read.get("content_withheld") or version.get("derived_text_withheld"):
                return view.format_exhibit_projection(
                    exhibit, subject_view=True, source_status="withheld",
                    reason_code=view.SOURCE_NOT_READABLE, version_number=version_number)
            return view.format_exhibit_projection(
                exhibit, subject_view=True, source_status="available",
                title=str(project.get("title") or ""),
                version_number=version_number,
                owner_content=str(version.get("content") or ""))

        delivered = self._pocket_delivered_share_for_version(artifact_id, version_id)
        if not delivered.get("ok"):
            return view.format_exhibit_projection(
                exhibit, subject_view=False, source_status="withheld",
                reason_code=str(delivered.get("reason_code") or view.VERSION_NOT_DELIVERED),
                version_number=version_number)
        return view.format_exhibit_projection(
            exhibit, subject_view=False, source_status="available",
            title=str(project.get("title") or ""),
            version_number=version_number,
            delivered_share_id=str(delivered.get("share_id") or ""),
            delivered_text=str(delivered.get("text") or ""),
            delivered_at=delivered.get("delivered_at"))

    def outing_gallery_blueprint(self, *, subject_view: bool = False) -> dict[str, Any]:
        """``GET /api/phone/outing/gallery-blueprint``: mini exhibition + co-drawn blueprints."""

        from . import outing_places_view as view

        state, state_reason = view.read_gallery_blueprint_state(self.root)
        projected = [self._outing_project_exhibit(item, subject_view=bool(subject_view))
                     for item in (state.get("exhibits") or ()) if isinstance(item, Mapping)]
        return envelope(True, STATUS_OK, view.gallery_blueprint_block(
            state=state, state_reason=state_reason,
            exhibits_projected=projected,
            subject_view=bool(subject_view)))

    def outing_gallery_blueprint_action(
        self,
        *,
        action: str,
        artifact_id: Optional[str] = None,
        version_id: Optional[str] = None,
        wall_caption: Optional[str] = None,
        caption_supplied: bool = False,
        exhibit_id: Optional[str] = None,
        blueprint_id: Optional[str] = None,
        title: Optional[str] = None,
        sketch_note: Optional[str] = None,
        intended_use: Optional[str] = None,
        author_side: Optional[str] = None,
    ) -> dict[str, Any]:
        """``POST /api/phone/outing/gallery-blueprint``: curate exhibit / co-draw blueprint."""

        from . import outing_places_view as view

        state, state_reason = view.read_gallery_blueprint_state(self.root)
        if state_reason:
            return rejected(state_reason)
        now_iso = self._home_now_iso(self._home_clock())

        try:
            if action == "curate_exhibit":
                target_artifact = str(artifact_id or "")
                target_version = str(version_id or "")
                project_answer = self.project(target_artifact)
                if not project_answer.get("ok"):
                    reason = str(project_answer.get("reason_code") or "")
                    if (project_answer.get("status") == STATUS_UNAVAILABLE
                            and not reason.startswith("PROJECT_IS_")):
                        return _project_failure(project_answer, "PROJECT_UNAVAILABLE")
                    return rejected(view.NOT_A_PROJECT, artifact_id=target_artifact)
                project = project_answer.get("project") \
                    if isinstance(project_answer.get("project"), Mapping) else {}
                if str(project.get("artifact_type") or "") != "project":
                    return rejected(view.NOT_A_PROJECT, artifact_id=target_artifact)
                try:
                    from life_sharing_service import is_share_title
                except Exception:
                    return unavailable("SHARE_GUARD_UNAVAILABLE")
                if is_share_title(project.get("title")):
                    return rejected(view.SHARE_ENVELOPE_NOT_A_PROJECT,
                                    artifact_id=target_artifact)
                if str(project.get("lifecycle") or "") != "ACTIVE":
                    return rejected(view.ARCHIVED_SOURCE, artifact_id=target_artifact)

                ver_read = self.project_version(target_artifact, target_version)
                if not ver_read.get("ok"):
                    reason = str(ver_read.get("reason_code") or "PROJECT_VERSION_UNAVAILABLE")
                    if (reason in (view.VERSION_NOT_IN_ARTIFACT, "VERSION_NOT_FOUND")
                            or ver_read.get("status") == STATUS_NOT_FOUND):
                        return rejected(view.VERSION_NOT_IN_ARTIFACT,
                                        artifact_id=target_artifact)
                    return _project_failure(ver_read, "PROJECT_VERSION_UNAVAILABLE")
                version = ver_read.get("version") \
                    if isinstance(ver_read.get("version"), Mapping) else {}
                if (str(version.get("version_id") or "") != target_version
                        or str(ver_read.get("artifact_id") or "") != target_artifact):
                    return rejected(view.VERSION_NOT_IN_ARTIFACT, artifact_id=target_artifact)
                if str(version.get("lifecycle") or "ACTIVE") != "ACTIVE":
                    return rejected(view.ARCHIVED_SOURCE, artifact_id=target_artifact)

                new_state, item, changed = view.apply_curate_exhibit(
                    state, artifact_id=target_artifact, version_id=target_version,
                    wall_caption=wall_caption, caption_supplied=bool(caption_supplied),
                    now_iso=now_iso)
                detail: dict[str, Any] = {"exhibit_id": item["exhibit_id"], "exhibit": item}
            elif action == "remove_exhibit":
                new_state, changed = view.apply_remove_exhibit(
                    state, exhibit_id=str(exhibit_id or ""), now_iso=now_iso)
                detail = {"exhibit_id": str(exhibit_id or "")}
            elif action == "upsert_blueprint":
                new_state, item, changed = view.apply_upsert_blueprint(
                    state, blueprint_id=blueprint_id, title=str(title or ""),
                    sketch_note=str(sketch_note or ""), intended_use=str(intended_use or ""),
                    author_side=str(author_side or ""), now_iso=now_iso)
                detail = {"blueprint_id": item["blueprint_id"], "blueprint": item}
            elif action == "archive_blueprint":
                new_state, item, changed = view.apply_archive_blueprint(
                    state, blueprint_id=str(blueprint_id or ""),
                    author_side=str(author_side or ""), now_iso=now_iso)
                detail = {"blueprint_id": item["blueprint_id"], "blueprint": item}
            else:
                return rejected("GALLERY_BLUEPRINT_ACTION_UNSUPPORTED", action=action)
        except view.OutingStateError as exc:
            return rejected(exc.reason_code)

        if not changed:
            return envelope(True, STATUS_ALREADY, {
                "action": action, "changed": False,
                "revision": state.get("revision"),
                "updated_at": state.get("updated_at"),
                **detail,
            })
        written = view.write_gallery_blueprint_state(self.root, new_state)
        return envelope(True, STATUS_OK, {
            "action": action, "changed": True,
            "revision": written.get("revision"),
            "updated_at": written.get("updated_at"),
            **detail,
        })

    # -- 长程陪伴六项日常细节：首日房间/盆栽手账/共同习惯册/礼物轨迹/双层灯光/一键续接 --------

    def _longterm_home_room_context(self) -> dict[str, Any]:
        """Gather live room snapshot facts and real home potted plants (pure zero-write)."""

        plants_by_id: dict[str, dict[str, Any]] = {}
        furniture_count = 0

        hw = self.home_window()
        hw_data = hw.get("data") if (hw.get("ok") and isinstance(hw.get("data"), Mapping)) else {}
        plants_raw = hw_data.get("plants")
        plant_rows: list[Any] = []
        if isinstance(plants_raw, list):
            plant_rows.extend(plants_raw)
        elif isinstance(plants_raw, Mapping) and isinstance(plants_raw.get("items"), list):
            plant_rows.extend(plants_raw["items"])
        for row in plant_rows:
            if not isinstance(row, Mapping):
                continue
            pid = str(row.get("object_id") or row.get("plant_id") or "").strip()
            if not pid:
                continue
            plants_by_id[pid] = {
                "plant_id": pid,
                "name": str(row.get("name") or pid),
                "area_id": row.get("area_id"),
            }

        try:
            from world_runtime_data import active_world

            pkg_dir = Path(active_world(self.environment).package_dir)
            home_ids = {"home"}
            loc_path = pkg_dir / "locations.json"
            if loc_path.is_file():
                loc_doc = json.loads(loc_path.read_text(encoding="utf-8"))
                for loc in (loc_doc.get("locations") or ()):
                    if isinstance(loc, Mapping) and loc.get("home") is True:
                        lid = str(loc.get("location_id") or "").strip()
                        if lid:
                            home_ids.add(lid)
            obj_path = pkg_dir / "objects.json"
            if obj_path.is_file():
                obj_doc = json.loads(obj_path.read_text(encoding="utf-8"))
                for obj in (obj_doc.get("objects") or ()):
                    if not isinstance(obj, Mapping):
                        continue
                    if str(obj.get("location_id") or "") not in home_ids:
                        continue
                    oid = str(obj.get("object_id") or "").strip()
                    kind = str(obj.get("kind") or "").strip().lower()
                    if kind == "plant":
                        if oid and oid not in plants_by_id:
                            plants_by_id[oid] = {
                                "plant_id": oid,
                                "name": str(obj.get("name") or oid),
                                "area_id": obj.get("area_id"),
                            }
                    else:
                        furniture_count += 1
        except Exception:
            pass

        loc_block = self._reunion_location(self.world())
        location = (
            str(loc_block.get("place_id") or "home")
            if loc_block.get("status") == "available" else "home"
        )

        items_block = self._reunion_items()
        item_rows = items_block.get("items") if items_block.get("status") == "available" else ()
        item_count = (
            int(items_block.get("count") or 0)
            if items_block.get("status") == "available" else 0
        )
        item_names: list[str] = []
        for row in (item_rows or ()):
            if isinstance(row, Mapping):
                nm = str(row.get("name") or "").strip()
                if nm and nm not in item_names:
                    item_names.append(nm)

        plant_names: list[str] = []
        for pinfo in plants_by_id.values():
            pnm = str(pinfo.get("name") or "").strip()
            if pnm and pnm not in plant_names:
                plant_names.append(pnm)

        reading_block = self._reunion_reading()
        raw_page = reading_block.get("page") if reading_block.get("status") == "available" else None
        reading_page = raw_page if (isinstance(raw_page, int) and not isinstance(raw_page, bool)) else None

        delivered_block = self._reunion_delivered()
        delivered_count = (
            int(delivered_block.get("count") or 0)
            if delivered_block.get("status") == "available" else 0
        )

        return {
            "snapshot": {
                "location": location,
                "furniture_count": furniture_count,
                "item_count": item_count,
                "item_names": item_names,
                "plant_names": plant_names,
                "reading_page": reading_page,
                "delivered_share_count": delivered_count,
            },
            "plants_by_id": plants_by_id,
        }

    def _longterm_album_is_active(self, album_id: str) -> bool:
        detail = self.album_detail(album_id)
        image = detail.get("image") if isinstance(detail.get("image"), Mapping) else {}
        return bool(detail.get("ok") and str(image.get("lifecycle") or "ACTIVE") == "ACTIVE")

    def _longterm_delivered_project_ids(self) -> set[str]:
        listing = self.shares(limit=50)
        if not listing.get("ok"):
            return set()
        data = listing.get("data") if isinstance(listing.get("data"), Mapping) else {}
        delivered_ids: set[str] = set()
        for row in (data.get("shares") or ()):
            if not isinstance(row, Mapping) or str(row.get("state") or "") != "LOCAL_DELIVERED":
                continue
            source = row.get("source") if isinstance(row.get("source"), Mapping) else {}
            aid = str(source.get("artifact_id") or "").strip()
            if aid:
                delivered_ids.add(aid)
        return delivered_ids

    def _longterm_inspect_project_version(
        self,
        artifact_id: str,
        version_id: str,
        *,
        include_owner_text: bool,
    ) -> dict[str, Any]:
        from . import long_companionship_view as view

        project_answer = self.project(artifact_id)
        if not project_answer.get("ok"):
            return {
                "readable": False,
                "lifecycle": None,
                "reason_code": str(project_answer.get("reason_code") or view.SOURCE_NOT_READABLE),
            }
        project = (
            project_answer.get("project")
            if isinstance(project_answer.get("project"), Mapping) else {}
        )
        if str(project.get("artifact_type") or "") != "project":
            return {
                "readable": False,
                "lifecycle": str(project.get("lifecycle") or ""),
                "reason_code": "NOT_A_PROJECT",
            }
        try:
            from life_sharing_service import is_share_title
            if is_share_title(project.get("title")):
                return {
                    "readable": False,
                    "lifecycle": str(project.get("lifecycle") or ""),
                    "reason_code": "SHARE_ENVELOPE_NOT_A_PROJECT",
                }
        except Exception:
            return {
                "readable": False,
                "lifecycle": None,
                "reason_code": "SHARE_GUARD_UNAVAILABLE",
            }

        lifecycle = str(project.get("lifecycle") or "")
        if lifecycle != "ACTIVE":
            return {
                "readable": False,
                "lifecycle": lifecycle or "ARCHIVED",
                "reason_code": view.ARCHIVED_SOURCE,
            }

        ver_read = self.project_version(artifact_id, version_id)
        if not ver_read.get("ok"):
            reason = str(ver_read.get("reason_code") or view.VERSION_UNREADABLE)
            if reason == "VERSION_NOT_FOUND" or ver_read.get("status") == STATUS_NOT_FOUND:
                reason = view.VERSION_NOT_IN_ARTIFACT
            return {
                "readable": False,
                "lifecycle": lifecycle,
                "reason_code": reason,
            }
        version = ver_read.get("version") if isinstance(ver_read.get("version"), Mapping) else {}
        if (str(version.get("version_id") or "") != version_id
                or str(ver_read.get("artifact_id") or "") != artifact_id):
            return {
                "readable": False,
                "lifecycle": lifecycle,
                "reason_code": view.VERSION_NOT_IN_ARTIFACT,
            }
        if str(version.get("lifecycle") or "ACTIVE") != "ACTIVE":
            return {
                "readable": False,
                "lifecycle": "ARCHIVED",
                "reason_code": view.ARCHIVED_SOURCE,
            }

        delivered_res = self._pocket_delivered_share_for_version(artifact_id, version_id)
        delivered = bool(delivered_res.get("ok"))
        owner_excerpt = ""
        if include_owner_text and not ver_read.get("content_withheld"):
            owner_excerpt = str(version.get("content") or "")

        return {
            "readable": True,
            "lifecycle": "ACTIVE",
            "reason_code": None,
            "title": str(project.get("title") or ""),
            "version_number": version.get("version_number"),
            "version_created_at": version.get("created_at") or project.get("updated_at"),
            "owner_excerpt": owner_excerpt,
            "delivered": delivered,
            "share_id": delivered_res.get("share_id") if delivered else None,
            "delivered_title": delivered_res.get("title") if delivered else None,
            "delivered_excerpt": str(delivered_res.get("text") or "") if delivered else None,
            "delivered_at": delivered_res.get("delivered_at") if delivered else None,
        }

    def _longterm_scan_derived_lineage(self) -> list[dict[str, Any]]:
        """Scan `PrivateProjectService` for real `derived` works with v1 provenance markers."""

        out: list[dict[str, Any]] = []
        try:
            from life_sharing_service import is_share_title
            projects = self._projects_service()
            listing = projects.list_projects(include_archived=True)
        except Exception:
            return out
        if not isinstance(listing, Mapping) or not listing.get("ok"):
            return out

        for row in (listing.get("projects") or ()):
            if not isinstance(row, Mapping):
                continue
            art_id = str(row.get("artifact_id") or "").strip()
            head_ver = str(row.get("head_version_id") or "").strip()
            if not art_id or not head_ver:
                continue
            if is_share_title(row.get("title")):
                continue
            try:
                prov_res = projects.read_project_provenance(art_id)
            except Exception:
                continue
            if not isinstance(prov_res, Mapping) or not prov_res.get("ok"):
                continue
            prov = prov_res.get("provenance")
            if not isinstance(prov, Mapping):
                continue
            src_art = str(prov.get("source_artifact_id") or "").strip()
            src_ver = str(prov.get("source_version_id") or "").strip()
            if not src_art or not src_ver:
                continue
            out.append({
                "derived_artifact_id": art_id,
                "derived_version_id": head_ver,
                "derived_lifecycle": str(row.get("lifecycle") or ""),
                "source_artifact_id": src_art,
                "source_version_id": src_ver,
                "purpose": prov.get("purpose"),
                "taken_at": prov.get("taken_at") or row.get("created_at"),
                "provenance_note": None if prov.get("note_withheld") else prov.get("note"),
            })
        return out

    def longterm_first_room_habits(self, *, subject_view: bool = False) -> dict[str, Any]:
        """``GET /api/phone/longterm/first-room-habits``: Day-1 room delta + plant sketch + habits."""

        from . import long_companionship_view as view

        state, state_reason = view.read_room_habits_state(self.root)
        room_ctx = self._longterm_home_room_context()

        album_readability: dict[str, bool] = {}
        first_room = state.get("first_room")
        if isinstance(first_room, Mapping):
            aid = first_room.get("album_artifact_id")
            if isinstance(aid, str) and aid:
                album_readability[aid] = self._longterm_album_is_active(aid)

        delivered_project_ids = self._longterm_delivered_project_ids()
        project_targets: dict[str, dict[str, Any]] = {}
        for link in (state.get("plant_sketch_links") or ()):
            if not isinstance(link, Mapping):
                continue
            if not subject_view and not link.get("shared"):
                continue
            tkind = str(link.get("target_kind") or "")
            tid = str(link.get("target_artifact_id") or "")
            if not tid:
                continue
            if tkind == "album":
                if tid not in album_readability:
                    album_readability[tid] = self._longterm_album_is_active(tid)
            elif tkind == "project" and tid not in project_targets:
                proj_ans = self.project(tid)
                proj = proj_ans.get("project") if isinstance(proj_ans.get("project"), Mapping) else {}
                active_proj = bool(
                    proj_ans.get("ok")
                    and str(proj.get("artifact_type") or "") == "project"
                    and str(proj.get("lifecycle") or "") == "ACTIVE"
                )
                project_targets[tid] = {
                    "active": active_proj,
                    "delivered": active_proj and (tid in delivered_project_ids),
                }

        return envelope(True, STATUS_OK, view.first_room_habits_block(
            state=state,
            state_reason=state_reason,
            live_room=room_ctx["snapshot"],
            home_plants_by_id=room_ctx["plants_by_id"],
            album_readability=album_readability,
            project_targets=project_targets,
            subject_view=bool(subject_view),
        ))

    def longterm_first_room_habits_action(
        self,
        *,
        action: str,
        album_artifact_id: Optional[str] = None,
        note: Optional[str] = None,
        plant_id: Optional[str] = None,
        target_kind: Optional[str] = None,
        target_artifact_id: Optional[str] = None,
        observation_note: Optional[str] = None,
        shared: Optional[bool] = None,
        link_id: Optional[str] = None,
        habit_id: Optional[str] = None,
        title: Optional[str] = None,
        habit_text: Optional[str] = None,
        revised_reason: Optional[str] = None,
        retired_reason: Optional[str] = None,
        author_side: Optional[str] = None,
    ) -> dict[str, Any]:
        """``POST /api/phone/longterm/first-room-habits``: explicit mutations."""

        from . import long_companionship_view as view

        state, state_reason = view.read_room_habits_state(self.root)
        if state_reason:
            return rejected(state_reason)
        now_iso = self._home_now_iso(self._home_clock())

        try:
            if action == "capture_first_room":
                existing_first = state.get("first_room")
                already_captured = (
                    isinstance(existing_first, Mapping) and existing_first.get("captured") is True
                )
                if not already_captured and album_artifact_id is not None:
                    if not self._longterm_album_is_active(album_artifact_id):
                        return rejected("FIRST_ROOM_ALBUM_NOT_FOUND",
                                        album_artifact_id=album_artifact_id)
                room_ctx = self._longterm_home_room_context()
                new_state, item, changed = view.apply_capture_first_room(
                    state,
                    snapshot=room_ctx["snapshot"],
                    album_artifact_id=album_artifact_id,
                    note=note,
                    now_iso=now_iso,
                )
                detail: dict[str, Any] = {"first_room": item}
            elif action == "link_plant_sketch":
                room_ctx = self._longterm_home_room_context()
                plants_by_id = room_ctx["plants_by_id"]
                clean_pid = str(plant_id or "").strip()
                if clean_pid not in plants_by_id:
                    return rejected("PLANT_NOT_IN_HOME", plant_id=clean_pid)
                clean_kind = str(target_kind or "").strip()
                clean_target = str(target_artifact_id or "").strip()
                if clean_kind == "album":
                    if not self._longterm_album_is_active(clean_target):
                        return rejected("SKETCH_TARGET_NOT_FOUND",
                                        target_artifact_id=clean_target)
                elif clean_kind == "project":
                    proj_ans = self.project(clean_target)
                    proj = (
                        proj_ans.get("project")
                        if isinstance(proj_ans.get("project"), Mapping) else {}
                    )
                    try:
                        from life_sharing_service import is_share_title
                        share_titled = is_share_title(proj.get("title"))
                    except Exception:
                        return unavailable("SHARE_GUARD_UNAVAILABLE")
                    if (not proj_ans.get("ok")
                            or share_titled
                            or str(proj.get("artifact_type") or "") != "project"
                            or str(proj.get("lifecycle") or "") != "ACTIVE"):
                        return rejected("SKETCH_TARGET_NOT_FOUND",
                                        target_artifact_id=clean_target)
                else:
                    return rejected("SKETCH_TARGET_KIND_INVALID")

                new_state, item, changed = view.apply_link_plant_sketch(
                    state,
                    plant_id=clean_pid,
                    plant_name=str(plants_by_id[clean_pid].get("name") or clean_pid),
                    valid_plant_ids=set(plants_by_id.keys()),
                    target_kind=clean_kind,
                    target_artifact_id=clean_target,
                    observation_note=str(observation_note or ""),
                    shared=False if shared is None else bool(shared),
                    now_iso=now_iso,
                )
                detail = {"link_id": item["link_id"], "link": item}
            elif action == "remove_plant_sketch":
                new_state, changed = view.apply_remove_plant_sketch(
                    state, link_id=str(link_id or ""), now_iso=now_iso)
                detail = {"link_id": str(link_id or "")}
            elif action == "upsert_habit":
                new_state, item, changed = view.apply_upsert_habit(
                    state,
                    habit_id=habit_id,
                    title=str(title or ""),
                    habit_text=str(habit_text or ""),
                    revised_reason=revised_reason,
                    author_side=str(author_side or ""),
                    now_iso=now_iso,
                )
                detail = {"habit_id": item["habit_id"], "habit": item}
            elif action == "retire_habit":
                new_state, item, changed = view.apply_retire_habit(
                    state,
                    habit_id=str(habit_id or ""),
                    retired_reason=retired_reason,
                    author_side=str(author_side or ""),
                    now_iso=now_iso,
                )
                detail = {"habit_id": item["habit_id"], "habit": item}
            else:
                return rejected("FIRST_ROOM_HABITS_ACTION_UNSUPPORTED", action=action)
        except view.LongtermStateError as exc:
            return rejected(exc.reason_code)

        if not changed:
            return envelope(True, STATUS_ALREADY, {
                "action": action,
                "changed": False,
                "already_applied": True,
                "revision": state.get("revision"),
                "updated_at": state.get("updated_at"),
                **detail,
            })
        written = view.write_room_habits_state(self.root, new_state)
        return envelope(True, STATUS_OK, {
            "action": action,
            "changed": True,
            "already_applied": False,
            "revision": written.get("revision"),
            "updated_at": written.get("updated_at"),
            **detail,
        })

    def longterm_gifts_lantern(
        self,
        *,
        layer: str = "then_only",
        subject_view: bool = False,
    ) -> dict[str, Any]:
        """``GET /api/phone/longterm/gifts-lantern``: shared gifts + dual-layer memory lantern."""

        from . import long_companionship_view as view

        state, state_reason = view.read_gifts_lantern_state(self.root)
        derived_lineage = self._longterm_scan_derived_lineage()

        pairs_to_inspect: set[tuple[str, str]] = set()
        for gift in (state.get("shared_gifts") or ()):
            if not isinstance(gift, Mapping):
                continue
            if not subject_view and gift.get("author_side") == "her" and gift.get("stage") != "gifted":
                continue
            aid = gift.get("artifact_id")
            vid = gift.get("version_id")
            if isinstance(aid, str) and aid and isinstance(vid, str) and vid:
                pairs_to_inspect.add((aid, vid))

        for remake in derived_lineage:
            s_aid = str(remake.get("source_artifact_id") or "")
            s_vid = str(remake.get("source_version_id") or "")
            if s_aid and s_vid:
                pairs_to_inspect.add((s_aid, s_vid))
            if layer == "with_later_context":
                d_aid = str(remake.get("derived_artifact_id") or "")
                d_vid = str(remake.get("derived_version_id") or "")
                if d_aid and d_vid:
                    pairs_to_inspect.add((d_aid, d_vid))

        for refl in (state.get("later_reflections") or ()):
            if not isinstance(refl, Mapping):
                continue
            if not subject_view and not refl.get("shared"):
                continue
            s_aid = str(refl.get("source_artifact_id") or "")
            s_vid = str(refl.get("source_version_id") or "")
            if s_aid and s_vid:
                pairs_to_inspect.add((s_aid, s_vid))

        inspections: dict[tuple[str, str], dict[str, Any]] = {}
        for aid, vid in pairs_to_inspect:
            inspections[(aid, vid)] = self._longterm_inspect_project_version(
                aid, vid, include_owner_text=bool(subject_view))

        return envelope(True, STATUS_OK, view.gifts_lantern_block(
            state=state,
            state_reason=state_reason,
            layer=layer,
            version_inspections=inspections,
            derived_lineage=derived_lineage,
            subject_view=bool(subject_view),
        ))

    def longterm_gifts_lantern_action(
        self,
        *,
        action: str,
        gift_id: Optional[str] = None,
        author_side: Optional[str] = None,
        stage: Optional[str] = None,
        title: Optional[str] = None,
        draft_seed_note: Optional[str] = None,
        finished_note: Optional[str] = None,
        finished_note_supplied: bool = False,
        artifact_id: Optional[str] = None,
        version_id: Optional[str] = None,
        link_supplied: bool = False,
        source_artifact_id: Optional[str] = None,
        source_version_id: Optional[str] = None,
        reflection_note: Optional[str] = None,
        shared: Optional[bool] = None,
    ) -> dict[str, Any]:
        """``POST /api/phone/longterm/gifts-lantern``: explicit gift & reflection mutations."""

        from . import long_companionship_view as view

        state, state_reason = view.read_gifts_lantern_state(self.root)
        if state_reason:
            return rejected(state_reason)
        now_iso = self._home_now_iso(self._home_clock())

        try:
            if action == "upsert_gift":
                if str(author_side or "") == "her" and artifact_id is not None and version_id is not None:
                    insp = self._longterm_inspect_project_version(
                        artifact_id, version_id, include_owner_text=False)
                    if not insp.get("readable"):
                        return rejected(str(insp.get("reason_code") or "GIFT_WORK_NOT_FOUND"),
                                        artifact_id=artifact_id)
                new_state, item, changed = view.apply_upsert_gift(
                    state,
                    gift_id=gift_id,
                    author_side=str(author_side or ""),
                    stage=str(stage or ""),
                    title=str(title or ""),
                    draft_seed_note=str(draft_seed_note or ""),
                    finished_note=finished_note,
                    artifact_id=artifact_id,
                    version_id=version_id,
                    finished_note_supplied=bool(finished_note_supplied),
                    link_supplied=bool(link_supplied),
                    now_iso=now_iso,
                )
                detail: dict[str, Any] = {"gift_id": item["gift_id"], "gift": item}
            elif action == "send_gift":
                new_state, item, changed = view.apply_send_gift(
                    state,
                    gift_id=str(gift_id or ""),
                    author_side=str(author_side or ""),
                    finished_note=finished_note,
                    now_iso=now_iso,
                )
                detail = {"gift_id": item["gift_id"], "gift": item}
            elif action == "record_later_reflection":
                src_art = str(source_artifact_id or "")
                src_ver = str(source_version_id or "")
                insp = self._longterm_inspect_project_version(
                    src_art, src_ver, include_owner_text=False)
                if not insp.get("readable"):
                    return rejected(str(insp.get("reason_code") or "SOURCE_WORK_NOT_FOUND"),
                                    source_artifact_id=src_art)
                new_state, item, changed = view.apply_record_later_reflection(
                    state,
                    source_artifact_id=src_art,
                    source_version_id=src_ver,
                    reflection_note=str(reflection_note or ""),
                    shared=False if shared is None else bool(shared),
                    now_iso=now_iso,
                )
                detail = {"reflection_id": item["reflection_id"], "reflection": item}
            else:
                return rejected("GIFTS_LANTERN_ACTION_UNSUPPORTED", action=action)
        except view.LongtermStateError as exc:
            return rejected(exc.reason_code)

        if not changed:
            return envelope(True, STATUS_ALREADY, {
                "action": action,
                "changed": False,
                "already_applied": True,
                "revision": state.get("revision"),
                "updated_at": state.get("updated_at"),
                **detail,
            })
        written = view.write_gifts_lantern_state(self.root, new_state)
        return envelope(True, STATUS_OK, {
            "action": action,
            "changed": True,
            "already_applied": False,
            "revision": written.get("revision"),
            "updated_at": written.get("updated_at"),
            **detail,
        })

    def runtime_recovery_review(self) -> dict[str, Any]:
        """Pure zero-write read of `runtime_recovery_review.review(...)`, or safe fallback if unbound."""

        candidates = [self.root / "one-day", self.root]
        one_day_root: Optional[Path] = None
        for cand in candidates:
            inst_file = cand / "instance.json"
            if not inst_file.is_file() or inst_file.is_symlink():
                continue
            try:
                doc = json.loads(inst_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(doc, Mapping) and doc.get("schema") == "life.one_day.v1":
                one_day_root = cand
                break

        if one_day_root is None:
            return {
                "status": "UNBOUND",
                "recovery_class": None,
                "backup_restore_status": "ONE_DAY_UNBOUND",
                "forget_barrier_status": "ENFORCED_NO_RAW_EXPORT",
                "reason_code": "ONE_DAY_INSTANCE_UNBOUND",
                "read_only": True,
            }

        try:
            import runtime_recovery_review as rrr

            sock = self.supply_socket if Path(self.supply_socket).exists() else None
            report = rrr.review(one_day_root, supply_socket=sock, subject=self.subject)
        except Exception:
            return {
                "status": "UNAVAILABLE",
                "recovery_class": None,
                "backup_restore_status": "REVIEW_UNAVAILABLE",
                "forget_barrier_status": "UNKNOWN",
                "reason_code": "RECOVERY_REVIEW_FAILED",
                "read_only": True,
            }

        gates = report.get("gates") if isinstance(report.get("gates"), Mapping) else {}
        backup_gate = gates.get("backup_restore") if isinstance(gates.get("backup_restore"), Mapping) else {}
        forget_gate = gates.get("forget_barrier") if isinstance(gates.get("forget_barrier"), Mapping) else {}
        backup_status = str(backup_gate.get("status") or "UNKNOWN")
        forget_status = str(
            forget_gate.get("status")
            or ("ENFORCED_BY_BACKUP_GATE" if backup_status == "PASS" else "NOT_RECORDED")
        )
        rec_class = str(report.get("class") or "UNKNOWN")
        return {
            "status": "OK" if report.get("ok") else rec_class,
            "recovery_class": rec_class,
            "backup_restore_status": backup_status,
            "forget_barrier_status": forget_status,
            "reason_code": None,
            "read_only": True,
        }

    def longterm_resume_hub(self, *, subject_view: bool = False) -> dict[str, Any]:
        """``GET /api/phone/longterm/resume-hub``: pure zero-write resume & closed/recovery overview."""

        from . import long_companionship_view as view

        active_projects: list[dict[str, Any]] = []
        archived_projects: list[dict[str, Any]] = []
        try:
            from life_sharing_service import is_share_title
            projects_svc = self._projects_service()
            listing = projects_svc.list_projects(include_archived=True)
            if isinstance(listing, Mapping) and listing.get("ok"):
                for row in (listing.get("projects") or ()):
                    if not isinstance(row, Mapping):
                        continue
                    if is_share_title(row.get("title")):
                        continue
                    aid = str(row.get("artifact_id") or "").strip()
                    if not aid:
                        continue
                    # Verify project artifact_type (exclude research/material artifacts)
                    p_detail = self.project(aid)
                    p_obj = (
                        p_detail.get("project")
                        if (p_detail.get("ok") and isinstance(p_detail.get("project"), Mapping))
                        else row
                    )
                    if p_detail.get("ok") and str(p_obj.get("artifact_type") or "") != "project":
                        continue
                    lifecycle = str(row.get("lifecycle") or "")
                    normalized = {
                        "artifact_id": aid,
                        "title": str(row.get("title") or ""),
                        "head_version_id": row.get("head_version_id"),
                        "updated_at": row.get("updated_at") or row.get("created_at"),
                    }
                    if lifecycle == "ACTIVE":
                        active_projects.append(normalized)
                    elif lifecycle == "ARCHIVED":
                        archived_projects.append(normalized)
        except Exception:
            pass

        active_projects.sort(key=lambda r: str(r.get("updated_at") or ""), reverse=True)
        archived_projects.sort(key=lambda r: str(r.get("updated_at") or ""), reverse=True)
        active_id_set = {r["artifact_id"] for r in active_projects}

        latest_topic_share: Optional[dict[str, Any]] = None
        latest_delivered_project_share: Optional[dict[str, Any]] = None
        shares_ans = self.shares(limit=50)
        if shares_ans.get("ok"):
            sdata = shares_ans.get("data") if isinstance(shares_ans.get("data"), Mapping) else {}
            delivered_rows = [
                row for row in (sdata.get("shares") or ())
                if isinstance(row, Mapping) and str(row.get("state") or "") == "LOCAL_DELIVERED"
            ]
            delivered_rows.sort(
                key=lambda r: str(r.get("delivered_at") or r.get("updated_at") or ""),
                reverse=True,
            )
            for row in delivered_rows:
                sid = str(row.get("share_id") or "")
                if not sid:
                    continue
                detail = self.share(sid)
                ddata = detail.get("data") if isinstance(detail.get("data"), Mapping) else {}
                sh = ddata.get("share") if isinstance(ddata.get("share"), Mapping) else row
                stitle = str(sh.get("title") or row.get("title") or "").strip() or None
                updated = sh.get("delivered_at") or row.get("updated_at") or row.get("created_at")
                if latest_topic_share is None:
                    latest_topic_share = {
                        "share_id": sid,
                        "title": stitle,
                        "updated_at": updated,
                    }
                src = row.get("source") if isinstance(row.get("source"), Mapping) else {}
                src_aid = str(src.get("artifact_id") or "").strip()
                src_vid = str(src.get("version_id") or "").strip()
                if (latest_delivered_project_share is None
                        and src_aid
                        and src_aid in active_id_set):
                    latest_delivered_project_share = {
                        "share_id": sid,
                        "title": stitle,
                        "artifact_id": src_aid,
                        "version_id": src_vid or None,
                        "delivered_at": updated,
                    }
                if latest_topic_share is not None and latest_delivered_project_share is not None:
                    break

        reading_ans = self.reading_material()
        rdata = (
            reading_ans.get("data")
            if (reading_ans.get("ok") and isinstance(reading_ans.get("data"), Mapping)) else {}
        )
        total_pages = rdata.get("pages")
        has_book = bool(
            reading_ans.get("ok")
            and isinstance(total_pages, int)
            and not isinstance(total_pages, bool)
            and total_pages > 0
        )
        bmark = rdata.get("bookmark") if isinstance(rdata.get("bookmark"), Mapping) else {}
        cur_page = bmark.get("page")
        if not isinstance(cur_page, int) or isinstance(cur_page, bool) or cur_page <= 0:
            cur_page = 1 if has_book else None
        reading_title: Optional[str] = None
        if subject_view and has_book:
            page_text = rdata.get("page_text")
            if not isinstance(page_text, str) or not page_text.strip():
                p1 = self.reading_material(page=1)
                p1_data = p1.get("data") if isinstance(p1.get("data"), Mapping) else {}
                page_text = p1_data.get("page_text")
            if isinstance(page_text, str) and page_text.strip():
                first_line = page_text.strip().splitlines()[0].strip()
                reading_title = first_line[:60] if first_line else None

        reading_bookmark = {
            "has_book": has_book,
            "current_page": cur_page,
            "total_pages": total_pages if has_book else None,
            "updated_at": bmark.get("updated_at") or rdata.get("updated_at"),
            "title": reading_title,
        }

        recovery_summary = self.runtime_recovery_review()
        return envelope(True, STATUS_OK, view.resume_hub_block(
            active_projects=active_projects,
            archived_projects=archived_projects,
            latest_delivered_project_share=latest_delivered_project_share,
            reading_bookmark=reading_bookmark,
            latest_topic_share=latest_topic_share,
            recovery_summary=recovery_summary,
            subject_view=bool(subject_view),
        ))

    # -- 开源角色包与可组合身体 / 多通道感官场与文字声景卡 ---------------------------------

    @staticmethod
    def _already(data: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
        """Standard idempotent replay envelope (``status == 'already_applied'``)."""

        return envelope(True, STATUS_ALREADY, data)

    @staticmethod
    def _bad_request(reason_code: str, **data: Any) -> dict[str, Any]:
        out = rejected(reason_code, **data)
        out["http_status"] = 400
        return out

    def character_pack(self, *, subject_view: bool = False) -> dict[str, Any]:
        """``GET /api/phone/character/pack``: pure zero-write character pack & body summary."""

        from . import sensory_character_view as view

        state, state_reason = view.read_character_pack_state(self.root)
        return envelope(
            True,
            STATUS_OK,
            view.project_character_pack(
                state,
                state_reason=state_reason,
                subject_view=bool(subject_view),
            ),
        )

    def character_pack_action(
        self,
        *,
        action: str,
        payload: Optional[Mapping[str, Any]] = None,
        subject_view: bool = False,
    ) -> dict[str, Any]:
        """``POST /api/phone/character/pack``: explicit preset / custom pack / identity / physiology / export / attest."""

        from . import sensory_character_view as view

        body = dict(payload or {})
        state, state_reason = view.read_character_pack_state(self.root)
        if state_reason:
            return rejected(state_reason)
        now_iso = self._somatic_now_iso()

        try:
            if action == "export_pack":
                inc_seed = body.get("include_sexual_seed", True)
                if not isinstance(inc_seed, bool):
                    return self._bad_request("INCLUDE_SEXUAL_SEED_INVALID")
                exported = view.apply_export_pack(state, include_sexual_seed=inc_seed)
                return envelope(True, STATUS_OK, exported)
            if action == "select_preset":
                if "preset_id" not in body:
                    return self._bad_request("PRESET_ID_REQUIRED")
                new_state = view.apply_select_preset(
                    state, preset_id=body.get("preset_id"), now_iso=now_iso
                )
            elif action == "apply_custom_pack":
                if "pack" not in body:
                    return self._bad_request("PACK_REQUIRED")
                new_state = view.apply_custom_pack(
                    state, pack=body.get("pack"), now_iso=now_iso
                )
            elif action == "import_pack_json":
                if "pack_json" in body:
                    raw_pj = body.get("pack_json")
                    if isinstance(raw_pj, str):
                        if not raw_pj.strip():
                            return self._bad_request("PACK_JSON_REQUIRED")
                        try:
                            parsed_pack = json.loads(raw_pj)
                        except ValueError:
                            return self._bad_request("PACK_JSON_INVALID")
                    elif isinstance(raw_pj, Mapping):
                        parsed_pack = raw_pj
                    else:
                        return self._bad_request("PACK_JSON_INVALID")
                elif "pack" in body:
                    parsed_pack = body.get("pack")
                else:
                    return self._bad_request("PACK_JSON_REQUIRED")
                new_state = view.apply_custom_pack(
                    state, pack=parsed_pack, now_iso=now_iso
                )
            elif action == "attest_character_pack":
                if "adult_confirmed" in body and body.get("adult_confirmed") is not True:
                    return self._bad_request("ADULT_CONFIRMED_REQUIRED")
                if "fictional_confirmed" in body and body.get("fictional_confirmed") is not True:
                    return self._bad_request("FICTIONAL_CONFIRMED_REQUIRED")
                active_doc = state.get("pack_doc") if isinstance(state.get("pack_doc"), Mapping) else {}
                if body.get("pack_id") is not None:
                    if not isinstance(body.get("pack_id"), str) or body["pack_id"].strip() != str(active_doc.get("pack_id") or ""):
                        return self._bad_request("PACK_ID_MISMATCH")
                if body.get("version") is not None:
                    v_raw = body.get("version")
                    if isinstance(v_raw, bool) or not isinstance(v_raw, (int, str)) or str(v_raw).strip() != str(active_doc.get("version") or ""):
                        return self._bad_request("PACK_VERSION_MISMATCH")
                if body.get("life_stage") is not None:
                    if body.get("life_stage") not in ("adult", "university_student", "working_adult", "adult_unspecified", "independent_adult"):
                        return self._bad_request("ADULT_LIFE_STAGE_REQUIRED")
                if body.get("declared_age") is not None:
                    age_val = body.get("declared_age")
                    if isinstance(age_val, bool) or not isinstance(age_val, int) or age_val < 18:
                        return self._bad_request("DECLARED_AGE_MUST_BE_ADULT")
                if body.get("note") is not None:
                    if not isinstance(body.get("note"), str) or not body["note"].strip() or len(body["note"].strip()) > 240:
                        return self._bad_request("NOTE_INVALID")
                new_state = view.apply_attest_character_pack(
                    state,
                    now_iso=now_iso,
                    attested_by=body.get("attested_by", "deployer"),
                    pack_sha256=body.get("pack_sha256"),
                )
                if isinstance(body.get("note"), str) and body["note"].strip() and isinstance(new_state.get("attestation"), dict):
                    new_state["attestation"]["note"] = body["note"].strip()
            elif action == "update_identity":
                new_state = view.apply_update_identity(
                    state, fields_present=body, now_iso=now_iso
                )
            elif action == "set_optional_physiology":
                if "optional_processes" not in body:
                    return self._bad_request("OPTIONAL_PROCESS_INVALID")
                new_state = view.apply_set_optional_physiology(
                    state,
                    optional_processes=body.get("optional_processes"),
                    now_iso=now_iso,
                )
            else:
                return self._bad_request("CHARACTER_PACK_ACTION_INVALID", action=action)
        except view.CharacterPackValidationError as exc:
            return self._bad_request(exc.reason_code)

        try:
            written = view.write_character_pack_state(self.root, new_state)
        except ValueError as exc:
            return rejected(str(exc) or "CHARACTER_PACK_WRITE_FAILED")

        projected = view.project_character_pack(
            written, state_reason=None, subject_view=bool(subject_view)
        )
        res_data: dict[str, Any] = {
            "action": action,
            "changed": True,
            "revision": written.get("revision"),
            "updated_at": written.get("updated_at"),
            "active_pack": projected["active_pack"],
            "exportable_pack": projected.get("exportable_pack"),
            "attestation": projected.get("attestation"),
            "adult_gate_preview": projected["adult_gate_preview"],
            "boundaries": projected["boundaries"],
        }
        att = written.get("attestation")
        if action == "attest_character_pack" and isinstance(att, Mapping):
            res_data["pack_sha256"] = att.get("pack_sha256")
            res_data["pack_ref"] = att.get("pack_ref")
            res_data["attested_by"] = att.get("attested_by")
            res_data["attested_at"] = att.get("attested_at")
        return envelope(True, STATUS_OK, res_data)

    def _sensory_projection(self, *, subject_view: bool = False) -> tuple[
        dict[str, Any], dict[str, Any], Optional[str]
    ]:
        from . import sensory_character_view as view

        world_result = self.world()
        body_view = self._body_from_world(world_result)
        somatic_state, somatic_reason = self._somatic_state()
        character_state, _char_reason = view.read_character_pack_state(self.root)
        cards_state, cards_reason = view.read_sensory_cards_state(self.root)
        now_iso = self._somatic_now_iso()
        projected = view.derive_sensory_field(
            world_result=world_result,
            body_view=body_view,
            somatic_state=somatic_state,
            somatic_reason=somatic_reason,
            character_state=character_state,
            cards_state=cards_state,
            cards_reason=cards_reason,
            now_iso=now_iso,
            subject_view=bool(subject_view),
            environment=self.environment,
        )
        return projected, cards_state, cards_reason

    def body_sensory(self, *, subject_view: bool = False) -> dict[str, Any]:
        """``GET /api/phone/body/sensory``: pure zero-write 5-channel sensory field & cards."""

        projected, _cards_state, _cards_reason = self._sensory_projection(
            subject_view=bool(subject_view)
        )
        return envelope(True, STATUS_OK, projected)

    def body_sensory_action(
        self,
        *,
        action: str,
        payload: Optional[Mapping[str, Any]] = None,
        subject_view: bool = False,
    ) -> dict[str, Any]:
        """``POST /api/phone/body/sensory``: sound cards & partner sensory notes."""

        from . import sensory_character_view as view

        body = dict(payload or {})
        cards_state, cards_reason = view.read_sensory_cards_state(self.root)
        if cards_reason:
            return rejected(cards_reason)
        now_iso = self._somatic_now_iso()

        try:
            if action == "capture_sound_card":
                if "shared" in body and not isinstance(body.get("shared"), bool):
                    return self._bad_request("SHARED_FLAG_INVALID")
                if "title" in body and body.get("title") is None:
                    return self._bad_request("CARD_TITLE_INVALID")
                if "impression_note" in body and body.get("impression_note") is None:
                    return self._bad_request("IMPRESSION_NOTE_INVALID")
                if "operation_key" in body and body.get("operation_key") is None:
                    return self._bad_request("OPERATION_KEY_INVALID")
                projected, _cs, _cr = self._sensory_projection(subject_view=True)
                new_state, card, already_applied, conflict = view.apply_capture_sound_card(
                    cards_state,
                    sensory_projection=projected,
                    title=body.get("title") if "title" in body else None,
                    impression_note=(
                        body.get("impression_note") if "impression_note" in body else None
                    ),
                    shared=body.get("shared", False),
                    operation_key=(
                        body.get("operation_key") if "operation_key" in body else None
                    ),
                    now_iso=now_iso,
                )
                if conflict:
                    return rejected(conflict)
                if already_applied:
                    return self._already({
                        "action": action,
                        "changed": False,
                        "replayed": True,
                        "revision": cards_state.get("revision"),
                        "updated_at": cards_state.get("updated_at"),
                        "sound_card": card,
                    })
                written = view.write_sensory_cards_state(self.root, new_state)
                return envelope(True, STATUS_OK, {
                    "action": action,
                    "changed": True,
                    "revision": written.get("revision"),
                    "updated_at": written.get("updated_at"),
                    "sound_card": card,
                })

            if action == "set_sound_card_share":
                if "card_id" not in body:
                    return self._bad_request("CARD_ID_INVALID")
                if "shared" not in body or not isinstance(body.get("shared"), bool):
                    return self._bad_request("SHARED_FLAG_INVALID")
                new_state, card, changed = view.apply_set_sound_card_share(
                    cards_state,
                    card_id=body.get("card_id"),
                    shared=body.get("shared"),
                    now_iso=now_iso,
                )
                if card is None:
                    return envelope(False, STATUS_NOT_FOUND, {}, "SOUND_CARD_NOT_FOUND")
                if not changed:
                    return self._already({
                        "action": action,
                        "changed": False,
                        "revision": cards_state.get("revision"),
                        "updated_at": cards_state.get("updated_at"),
                        "sound_card": card,
                    })
                written = view.write_sensory_cards_state(self.root, new_state)
                return envelope(True, STATUS_OK, {
                    "action": action,
                    "changed": True,
                    "revision": written.get("revision"),
                    "updated_at": written.get("updated_at"),
                    "sound_card": card,
                })

            if action == "remove_sound_card":
                if "card_id" not in body:
                    return self._bad_request("CARD_ID_INVALID")
                new_state, removed = view.apply_remove_sound_card(
                    cards_state, card_id=body.get("card_id"), now_iso=now_iso
                )
                if removed is None:
                    return envelope(False, STATUS_NOT_FOUND, {}, "SOUND_CARD_NOT_FOUND")
                written = view.write_sensory_cards_state(self.root, new_state)
                return envelope(True, STATUS_OK, {
                    "action": action,
                    "changed": True,
                    "revision": written.get("revision"),
                    "updated_at": written.get("updated_at"),
                    "removed_card_id": removed.get("card_id"),
                })

            if action == "leave_sensory_note":
                if "text" not in body:
                    return self._bad_request("SENSORY_NOTE_TEXT_INVALID")
                new_state, note, conflict = view.apply_leave_sensory_note(
                    cards_state, text=body.get("text"), now_iso=now_iso
                )
                if conflict:
                    return rejected(conflict)
                written = view.write_sensory_cards_state(self.root, new_state)
                return envelope(True, STATUS_OK, {
                    "action": action,
                    "changed": True,
                    "revision": written.get("revision"),
                    "updated_at": written.get("updated_at"),
                    "note": note,
                })

            if action == "remove_sensory_note":
                if "note_id" not in body:
                    return self._bad_request("SENSORY_NOTE_ID_INVALID")
                new_state, removed_note = view.apply_remove_sensory_note(
                    cards_state, note_id=body.get("note_id"), now_iso=now_iso
                )
                if removed_note is None:
                    return envelope(False, STATUS_NOT_FOUND, {}, "SENSORY_NOTE_NOT_FOUND")
                written = view.write_sensory_cards_state(self.root, new_state)
                return envelope(True, STATUS_OK, {
                    "action": action,
                    "changed": True,
                    "revision": written.get("revision"),
                    "updated_at": written.get("updated_at"),
                    "removed_note_id": removed_note.get("note_id"),
                })

            if action == "clear_sensory_notes":
                new_state, cleared_count, changed = view.apply_clear_sensory_notes(
                    cards_state, now_iso=now_iso
                )
                if not changed:
                    return self._already({
                        "action": action,
                        "changed": False,
                        "cleared_count": 0,
                        "revision": cards_state.get("revision"),
                        "updated_at": cards_state.get("updated_at"),
                    })
                written = view.write_sensory_cards_state(self.root, new_state)
                return envelope(True, STATUS_OK, {
                    "action": action,
                    "changed": True,
                    "cleared_count": cleared_count,
                    "revision": written.get("revision"),
                    "updated_at": written.get("updated_at"),
                })

            return self._bad_request("BODY_SENSORY_ACTION_INVALID", action=action)
        except view.CharacterPackValidationError as exc:
            return self._bad_request(exc.reason_code)
        except ValueError as exc:
            return rejected(str(exc) or "SENSORY_CARDS_WRITE_FAILED")

    # -- 主体日常自主拒绝边界 / 挂着的话题余温 / 主动改画风 / 真实异步延迟回信 ------------

    def _autonomy_live_context(self) -> dict[str, Any]:
        from . import sensory_character_view as char_view

        world_result = self.world()
        body_view = self._body_from_world(world_result)
        somatic_state, somatic_reason = self._somatic_state()
        character_state, _char_reason = char_view.read_character_pack_state(self.root)
        cards_state, cards_reason = char_view.read_sensory_cards_state(self.root)
        desk_result = self.desk_presence()
        now_iso = self._somatic_now_iso()
        sensory_projection = char_view.derive_sensory_field(
            world_result=world_result,
            body_view=body_view,
            somatic_state=somatic_state,
            somatic_reason=somatic_reason,
            character_state=character_state,
            cards_state=cards_state,
            cards_reason=cards_reason,
            now_iso=now_iso,
            subject_view=False,
            environment=self.environment,
        )
        return {
            "character_state": character_state,
            "world_result": world_result,
            "body_view": body_view,
            "desk_result": desk_result,
            "somatic_state": somatic_state,
            "sensory_projection": sensory_projection,
            "now_iso": now_iso,
        }

    def autonomy_rhythm(self, *, subject_view: bool = False) -> dict[str, Any]:
        """``GET /api/phone/autonomy/rhythm``: pure zero-write projection of the 4 autonomy pillars."""

        from . import autonomy_rhythm_view as view

        state, state_reason = view.read_autonomy_rhythm_state(self.root)
        ctx = self._autonomy_live_context()
        projected = view.project_autonomy_rhythm(
            state,
            state_reason=state_reason,
            subject_view=bool(subject_view),
            character_state=ctx["character_state"],
            world_result=ctx["world_result"],
            body_view=ctx["body_view"],
            desk_result=ctx["desk_result"],
            somatic_state=ctx["somatic_state"],
            sensory_projection=ctx["sensory_projection"],
            now_iso=ctx["now_iso"],
        )
        return envelope(True, STATUS_OK, projected)

    def autonomy_rhythm_action(
        self,
        *,
        action: str,
        payload: Optional[Mapping[str, Any]] = None,
        subject_view: bool = False,
    ) -> dict[str, Any]:
        """``POST /api/phone/autonomy/rhythm``: explicit refusal, thread, pivot & async rhythm actions."""

        from . import autonomy_rhythm_view as view

        body = dict(payload or {})
        state, state_reason = view.read_autonomy_rhythm_state(self.root)
        if state_reason:
            return rejected(state_reason)
        ctx = self._autonomy_live_context()

        try:
            new_state, extra = view.apply_autonomy_action(
                state,
                action=action,
                payload=body,
                subject_view=bool(subject_view),
                character_state=ctx["character_state"],
                world_result=ctx["world_result"],
                body_view=ctx["body_view"],
                desk_result=ctx["desk_result"],
                somatic_state=ctx["somatic_state"],
                sensory_projection=ctx["sensory_projection"],
                now_iso=ctx["now_iso"],
            )
        except view.AutonomyRhythmValidationError as exc:
            return self._bad_request(exc.reason_code)
        except view.AutonomyRhythmNotFoundError as exc:
            return envelope(False, STATUS_NOT_FOUND, {}, exc.reason_code)
        except view.AutonomyRhythmConflictError as exc:
            return rejected(exc.reason_code)

        try:
            written = view.write_autonomy_rhythm_state(self.root, new_state)
        except ValueError as exc:
            return rejected(str(exc) or "AUTONOMY_RHYTHM_WRITE_FAILED")

        projected = view.project_autonomy_rhythm(
            written,
            state_reason=None,
            subject_view=bool(subject_view),
            character_state=ctx["character_state"],
            world_result=ctx["world_result"],
            body_view=ctx["body_view"],
            desk_result=ctx["desk_result"],
            somatic_state=ctx["somatic_state"],
            sensory_projection=ctx["sensory_projection"],
            now_iso=ctx["now_iso"],
        )
        out_data = dict(projected)
        out_data["action"] = action
        out_data["changed"] = True
        out_data.update(extra)
        return envelope(True, STATUS_OK, out_data)

    # -- 0.3 情绪脾气与日常张力 / 看法修订与物件共在 / 欲望与 36B 成年亲密 ---------------

    def emotional_tension(self, *, subject_view: bool = False) -> dict[str, Any]:
        """``GET /api/phone/emotion/tension``: pure zero-write 5-domain tension projection."""

        from . import emotional_tension_view as view

        now_iso = self._somatic_now_iso()
        try:
            projected = view.read_emotional_tension(
                self.root, now=now_iso, subject_view=bool(subject_view)
            )
        except view.EmotionalTensionValidationError as exc:
            return self._bad_request(exc.reason_code)
        except view.EmotionalTensionConflictError as exc:
            return rejected(exc.reason_code)
        return envelope(True, STATUS_OK, projected)

    def emotional_tension_action(
        self,
        *,
        payload: Optional[Mapping[str, Any]] = None,
        subject_view: bool = False,
    ) -> dict[str, Any]:
        """``POST /api/phone/emotion/tension``: explicit schedule, quarrel, missing, low-mood & favor actions."""

        from . import emotional_tension_view as view

        now_iso = self._somatic_now_iso()
        try:
            out = view.apply_emotional_tension_action(
                self.root,
                dict(payload or {}),
                now=now_iso,
                subject_view=bool(subject_view),
            )
        except view.EmotionalTensionValidationError as exc:
            return self._bad_request(exc.reason_code)
        except view.EmotionalTensionPermissionError as exc:
            return {
                "ok": False,
                "status": STATUS_REJECTED,
                "reason_code": exc.reason_code,
                "http_status": 403,
                "data": {},
            }
        except view.EmotionalTensionNotFoundError as exc:
            return envelope(False, STATUS_NOT_FOUND, {}, exc.reason_code)
        except view.EmotionalTensionConflictError as exc:
            return rejected(exc.reason_code)

        status_out = STATUS_ALREADY if out.get("status") == STATUS_ALREADY else STATUS_OK
        return envelope(True, status_out, out, out.get("reason_code"))

    def stance_containment(self, *, subject_view: bool = False) -> dict[str, Any]:
        """``GET /api/phone/stance/containment``: pure zero-write stance, containment & shared desk/table projection."""

        from . import stance_containment_view as view

        now_iso = self._somatic_now_iso()
        try:
            projected = view.read_stance_containment(
                self.root, now=now_iso, subject_view=bool(subject_view)
            )
        except view.StanceContainmentValidationError as exc:
            return self._bad_request(exc.reason_code)
        except view.StanceContainmentConflictError as exc:
            return rejected(exc.reason_code)
        return envelope(True, STATUS_OK, projected)

    def stance_containment_action(
        self,
        *,
        payload: Optional[Mapping[str, Any]] = None,
        subject_view: bool = False,
    ) -> dict[str, Any]:
        """``POST /api/phone/stance/containment``: explicit stance lineage, container & shared desk/table actions."""

        from . import stance_containment_view as view

        now_iso = self._somatic_now_iso()
        try:
            out = view.apply_stance_containment_action(
                self.root,
                dict(payload or {}),
                now=now_iso,
                subject_view=bool(subject_view),
            )
        except view.StanceContainmentValidationError as exc:
            return self._bad_request(exc.reason_code)
        except view.StanceContainmentPermissionError as exc:
            return {
                "ok": False,
                "status": STATUS_REJECTED,
                "reason_code": exc.reason_code,
                "http_status": 403,
                "data": {},
            }
        except view.StanceContainmentNotFoundError as exc:
            return envelope(False, STATUS_NOT_FOUND, {}, exc.reason_code)
        except view.StanceContainmentConflictError as exc:
            return rejected(exc.reason_code)

        status_out = STATUS_ALREADY if out.get("status") == STATUS_ALREADY else STATUS_OK
        return envelope(True, status_out, out)

    def adult_intimacy(self, *, subject_view: bool = False) -> dict[str, Any]:
        """``GET /api/phone/intimacy/adult``: pure zero-write desire decay & 36B adult intimacy projection."""

        from . import adult_intimacy_view as view

        now_iso = self._somatic_now_iso()
        try:
            projected = view.read_adult_intimacy(
                self.root, now=now_iso, subject_view=bool(subject_view)
            )
        except view.AdultIntimacyValidationError as exc:
            return self._bad_request(exc.reason_code)
        except view.AdultIntimacyConflictError as exc:
            return rejected(exc.reason_code)
        return envelope(True, STATUS_OK, projected)

    def adult_intimacy_action(
        self,
        *,
        payload: Optional[Mapping[str, Any]] = None,
        subject_view: bool = False,
    ) -> dict[str, Any]:
        """``POST /api/phone/intimacy/adult``: explicit 36B gate, body, consent, stop & private memory actions."""

        from . import adult_intimacy_view as view

        now_iso = self._somatic_now_iso()
        try:
            out = view.apply_adult_intimacy_action(
                self.root,
                dict(payload or {}),
                now=now_iso,
                subject_view=bool(subject_view),
            )
        except view.AdultIntimacyValidationError as exc:
            return self._bad_request(exc.reason_code)
        except view.AdultIntimacyPermissionError as exc:
            return {
                "ok": False,
                "status": STATUS_REJECTED,
                "reason_code": exc.reason_code,
                "http_status": 403,
                "data": {},
            }
        except view.AdultIntimacyNotFoundError as exc:
            return envelope(False, STATUS_NOT_FOUND, {}, exc.reason_code)
        except view.AdultIntimacyConflictError as exc:
            return rejected(exc.reason_code)

        status_out = STATUS_ALREADY if out.get("status") == STATUS_ALREADY else STATUS_OK
        return envelope(True, status_out, out)

    # -- 0.3 / v3.0 三时钟蒸发 / 经济独立与生病自愈生态 / 厨房双手接触与邻里生态 ------------

    @staticmethod
    def _load_v03_clock_module() -> Any:
        import sys as _sys

        life_scripts = Path(__file__).resolve().parents[1] / "life" / "scripts"
        if str(life_scripts) not in _sys.path:
            _sys.path.insert(0, str(life_scripts))
        from components.life.scripts import v03_clock_evaporation as clock_mod

        return clock_mod

    def v03_clock(self, *, subject_view: bool = False) -> tuple[int, dict[str, Any]]:
        """``GET /api/phone/v03/clock``: pure zero-write three-clock & evaporation projection."""

        clock_mod = self._load_v03_clock_module()
        code, body = clock_mod.read_v03_clock_view(
            self.root,
            has_subject_capability=bool(subject_view),
            now_iso=self._somatic_now_iso(),
        )
        out = dict(body)
        if code == 200 and out.get("ok") is True:
            out["status"] = STATUS_OK
        return int(code), out

    def v03_clock_action(
        self,
        *,
        payload: Optional[Mapping[str, Any]] = None,
        subject_view: bool = False,
    ) -> tuple[int, dict[str, Any]]:
        """``POST /api/phone/v03/clock``: zero-LLM clock projection advance, evaporable & idle actions."""

        clock_mod = self._load_v03_clock_module()
        actor_side = "subject" if subject_view else "partner"
        code, body = clock_mod.mutate_v03_clock_view(
            self.root,
            dict(payload or {}),
            actor_side=actor_side,
            now_iso=self._somatic_now_iso(),
        )
        return int(code), dict(body)

    def v03_ecology(self, *, subject_view: bool = False) -> tuple[int, dict[str, Any]]:
        """``GET /api/phone/v03/ecology``: pure zero-write economy, illness & reality-bridge projection."""

        from . import v03_ecology_view as view

        code, body = view.read_v03_ecology_view(
            self.root,
            has_subject_capability=bool(subject_view),
            now_iso=self._somatic_now_iso(),
        )
        return int(code), dict(body)

    def v03_ecology_action(
        self,
        *,
        payload: Optional[Mapping[str, Any]] = None,
        subject_view: bool = False,
    ) -> tuple[int, dict[str, Any]]:
        """``POST /api/phone/v03/ecology``: independent economy, illness self-healing & outbound gate."""

        from . import v03_ecology_view as view

        actor_side = "subject" if subject_view else "partner"
        code, body = view.mutate_v03_ecology_view(
            self.root,
            dict(payload or {}),
            actor_side=actor_side,
            now_iso=self._somatic_now_iso(),
        )
        out = dict(body)
        if code == 200 and out.get("ok") is True:
            if out.get("command_status") == "DUPLICATE":
                out["status"] = STATUS_ALREADY
            elif out.get("status") == "ok":
                out["status"] = "applied"
        return int(code), out

    def v03_embodied_social(self, *, subject_view: bool = False) -> tuple[int, dict[str, Any]]:
        """``GET /api/phone/v03/embodied-social``: pure zero-write kitchen, contact, NPC & style projection."""

        from . import v03_embodied_social_view as view

        code, body = view.read_v03_embodied_social_view(
            self.root,
            has_subject_capability=bool(subject_view),
            now_iso=self._somatic_now_iso(),
        )
        out = dict(body)
        if code == 200 and out.get("ok") is True:
            out["status"] = STATUS_OK
        return int(code), out

    def v03_embodied_social_action(
        self,
        *,
        payload: Optional[Mapping[str, Any]] = None,
        subject_view: bool = False,
    ) -> tuple[int, dict[str, Any]]:
        """``POST /api/phone/v03/embodied-social``: kitchen conservation, two-hand contact, NPC & style."""

        from . import v03_embodied_social_view as view

        actor_side = "subject" if subject_view else "partner"
        code, body = view.mutate_v03_embodied_social_view(
            self.root,
            dict(payload or {}),
            actor_side=actor_side,
            now_iso=self._somatic_now_iso(),
        )
        return int(code), dict(body)

    # -- v0.4 街区院子手作公共生活 / 世界建设诊断与试住沙盒 / 弹性方向共同试验与玩心 ------

    def v04_neighborhood(self, *, subject_view: bool = False) -> tuple[int, dict[str, Any]]:
        """``GET /api/phone/v04/neighborhood``: pure zero-write neighborhood, courtyard, craft & shelf projection."""

        from . import v04_neighborhood_view as view

        code, body = view.read_v04_neighborhood_view(
            self.root,
            has_subject_capability=bool(subject_view),
            now_iso=self._somatic_now_iso(),
        )
        out = dict(body)
        if code == 200 and out.get("ok") is True:
            out["status"] = STATUS_OK
        return int(code), out

    def v04_neighborhood_action(
        self,
        *,
        payload: Optional[Mapping[str, Any]] = None,
        subject_view: bool = False,
    ) -> tuple[int, dict[str, Any]]:
        """``POST /api/phone/v04/neighborhood``: local weather, journey, courtyard, notices, crafts & shelf."""

        from . import v04_neighborhood_view as view

        actor_side = "subject" if subject_view else "partner"
        code, body = view.mutate_v04_neighborhood_view(
            self.root,
            dict(payload or {}),
            actor_side=actor_side,
            now_iso=self._somatic_now_iso(),
        )
        out = dict(body)
        if code == 200 and out.get("ok") is True:
            if out.get("command_status") == "DUPLICATE" or out.get("status") == "already_applied":
                out["status"] = STATUS_ALREADY
            elif out.get("status") in ("ok", None, ""):
                out["status"] = "applied"
        return int(code), out

    def v04_builder_trylive(self, *, subject_view: bool = False) -> tuple[int, dict[str, Any]]:
        """``GET /api/phone/v04/builder-trylive``: pure zero-write world diagnostics & sandbox projection."""

        from . import v04_builder_trylive_view as view

        code, body = view.read_v04_builder_trylive_view(
            self.root,
            has_subject_capability=bool(subject_view),
            now_iso=self._somatic_now_iso(),
        )
        out = dict(body)
        if code == 200 and out.get("ok") is True:
            out["status"] = STATUS_OK
        return int(code), out

    def v04_builder_trylive_action(
        self,
        *,
        payload: Optional[Mapping[str, Any]] = None,
        subject_view: bool = False,
    ) -> tuple[int, dict[str, Any]]:
        """``POST /api/phone/v04/builder-trylive``: isolated route audit, prose gap check, 1h try-live & preview."""

        from . import v04_builder_trylive_view as view

        actor_side = "subject" if subject_view else "partner"
        code, body = view.mutate_v04_builder_trylive_view(
            self.root,
            dict(payload or {}),
            actor_side=actor_side,
            now_iso=self._somatic_now_iso(),
        )
        out = dict(body)
        if code == 200 and out.get("ok") is True:
            if out.get("command_status") == "DUPLICATE" or out.get("status") == "already_applied":
                out["status"] = STATUS_ALREADY
            elif out.get("status") in ("ok", None, ""):
                out["status"] = "applied"
        return int(code), out

    def v04_elastic_play(self, *, subject_view: bool = False) -> tuple[int, dict[str, Any]]:
        """``GET /api/phone/v04/elastic-play``: pure zero-write elastic direction, shared learning & playfulness."""

        from . import v04_elastic_play_view as view

        code, body = view.read_v04_elastic_play_view(
            self.root,
            has_subject_capability=bool(subject_view),
            now_iso=self._somatic_now_iso(),
        )
        out = dict(body)
        if code == 200 and out.get("ok") is True:
            out["status"] = STATUS_OK
        return int(code), out

    def v04_elastic_play_action(
        self,
        *,
        payload: Optional[Mapping[str, Any]] = None,
        subject_view: bool = False,
    ) -> tuple[int, dict[str, Any]]:
        """``POST /api/phone/v04/elastic-play``: day direction, shared notebook, experiment, game & surprise."""

        from . import v04_elastic_play_view as view

        actor_side = "subject" if subject_view else "partner"
        code, body = view.mutate_v04_elastic_play_view(
            self.root,
            dict(payload or {}),
            actor_side=actor_side,
            now_iso=self._somatic_now_iso(),
        )
        out = dict(body)
        if code == 200 and out.get("ok") is True:
            if out.get("command_status") == "DUPLICATE" or out.get("status") == "already_applied":
                out["status"] = STATUS_ALREADY
            elif out.get("status") in ("ok", None, ""):
                out["status"] = "applied"
        return int(code), out

    def v02_agency_depth(self, *, subject_view: bool = False) -> tuple[int, dict[str, Any]]:
        """``GET /api/phone/v02/agency-depth``: pure zero-write agency depth projection."""

        from . import v02_agency_depth_view as view

        code, body = view.read_v02_agency_depth_view(
            self.root,
            has_subject_capability=bool(subject_view),
            now_iso=self._somatic_now_iso(),
        )
        out = dict(body)
        if code == 200 and out.get("ok") is True:
            out["status"] = STATUS_OK
        return int(code), out

    def v02_agency_depth_action(
        self,
        *,
        payload: Optional[Mapping[str, Any]] = None,
        subject_view: bool = False,
    ) -> tuple[int, dict[str, Any]]:
        """``POST /api/phone/v02/agency-depth``: agency depth mutations with actor_side & idempotency."""

        from . import v02_agency_depth_view as view

        actor_side = "subject" if subject_view else "partner"
        code, body = view.mutate_v02_agency_depth_view(
            self.root,
            dict(payload or {}),
            actor_side=actor_side,
            now_iso=self._somatic_now_iso(),
        )
        out = dict(body)
        if code == 200 and out.get("ok") is True:
            if out.get("command_status") == "DUPLICATE" or out.get("status") == "already_applied":
                out["status"] = STATUS_ALREADY
            elif out.get("status") in ("ok", None, ""):
                out["status"] = "applied"
        return int(code), out

    def v02_crossmodal(self, *, subject_view: bool = False) -> tuple[int, dict[str, Any]]:
        """``GET /api/phone/v02/crossmodal``: pure zero-write crossmodal palette, trial pad & reading marks."""

        from . import v02_crossmodal_view as view

        code, body = view.read_v02_crossmodal_view(
            self.root,
            has_subject_capability=bool(subject_view),
            now_iso=self._somatic_now_iso(),
        )
        out = dict(body)
        if code == 200 and out.get("ok") is True:
            out["status"] = STATUS_OK
        return int(code), out

    def v02_crossmodal_action(
        self,
        *,
        payload: Optional[Mapping[str, Any]] = None,
        subject_view: bool = False,
    ) -> tuple[int, dict[str, Any]]:
        """``POST /api/phone/v02/crossmodal``: image palette analysis, color trial swatches & reading marks."""

        from . import v02_crossmodal_view as view

        actor_side = "subject" if subject_view else "partner"
        code, body = view.mutate_v02_crossmodal_view(
            self.root,
            dict(payload or {}),
            actor_side=actor_side,
            now_iso=self._somatic_now_iso(),
        )
        out = dict(body)
        if code == 200 and out.get("ok") is True:
            if out.get("command_status") == "DUPLICATE" or out.get("status") == "already_applied":
                out["status"] = STATUS_ALREADY
            elif out.get("status") in ("ok", None, ""):
                out["status"] = "applied"
        return int(code), out

    def v02_runtime_migration(self, *, subject_view: bool = False) -> tuple[int, dict[str, Any]]:
        """``GET /api/phone/v02/runtime-migration``: pure zero-write runtime governance & migration projection."""

        from . import v02_runtime_migration_view as view

        code, body = view.read_v02_runtime_migration_view(
            self.root,
            has_subject_capability=bool(subject_view),
            now_iso=self._somatic_now_iso(),
        )
        out = dict(body)
        if code == 200 and out.get("ok") is True:
            out["status"] = STATUS_OK
        return int(code), out

    def v02_runtime_migration_action(
        self,
        *,
        payload: Optional[Mapping[str, Any]] = None,
        subject_view: bool = False,
    ) -> tuple[int, dict[str, Any]]:
        """``POST /api/phone/v02/runtime-migration``: budget mode, carrier check, suitcase & skill reverify."""

        from . import v02_runtime_migration_view as view

        actor_side = "subject" if subject_view else "partner"
        code, body = view.mutate_v02_runtime_migration_view(
            self.root,
            dict(payload or {}),
            actor_side=actor_side,
            now_iso=self._somatic_now_iso(),
        )
        out = dict(body)
        if code == 200 and out.get("ok") is True:
            if out.get("command_status") == "DUPLICATE" or out.get("status") == "already_applied":
                out["status"] = STATUS_ALREADY
            elif out.get("status") in ("ok", None, ""):
                out["status"] = "applied"
        return int(code), out

    def v02_recall_projection(self, *, subject_view: bool = False) -> tuple[int, dict[str, Any]]:
        """``GET /api/phone/v02/recall-projection``: pure zero-write 4-level recall projection."""

        from components.life.scripts import v02_recall_projection as rp

        now_iso = self._somatic_now_iso()
        raw_view = rp.read_v02_recall_projection_view(self.root, now_iso=now_iso)
        cognition = rp.summarize_recall_for_cognition(self.root, now_iso=now_iso)
        persp = "subject" if subject_view else "partner"
        data_block: dict[str, Any] = {
            "schema": rp.SCHEMA_VERSION,
            "endpoint": "/api/phone/v02/recall-projection",
            "perspective": persp,
            "revision": len(raw_view.get("direct_recall_items") or []),
            "state_exists": (Path(self.root) / rp.RECALL_STATE_REL_PATH).is_file(),
            "llm_calls": 0,
            **raw_view,
            "cognition_summary": cognition,
        }
        return 200, {
            "ok": True,
            "status": STATUS_OK,
            "command_status": "READ",
            "schema": rp.SCHEMA_VERSION,
            "endpoint": "/api/phone/v02/recall-projection",
            "perspective": persp,
            "llm_calls": 0,
            "data": data_block,
            **raw_view,
        }

    def v02_recall_projection_action(
        self,
        *,
        payload: Optional[Mapping[str, Any]] = None,
        subject_view: bool = False,
    ) -> tuple[int, dict[str, Any]]:
        """``POST /api/phone/v02/recall-projection``: episodic trace, active fade, rehearsal & tombstone."""

        from components.life.scripts import v02_recall_projection as rp

        body = dict(payload or {})
        action = str(body.get("action") or "").strip()
        if not action:
            return 400, {
                "ok": False,
                "status": STATUS_REJECTED,
                "reason_code": "ACTION_REQUIRED",
                "error_code": "ACTION_REQUIRED",
                "llm_calls": 0,
                "data": {},
            }
        if action in ("mark_active_fade", "record_rehearsal") and not subject_view:
            return 403, {
                "ok": False,
                "status": STATUS_REJECTED,
                "reason_code": "FORBIDDEN_ACTOR_SIDE",
                "error_code": "FORBIDDEN_ACTOR_SIDE",
                "llm_calls": 0,
                "data": {},
            }
        res = rp.mutate_v02_recall_projection(self.root, body, now_iso=self._somatic_now_iso())
        code = int(res.get("status_code") or (200 if res.get("ok") else 400))
        if not res.get("ok"):
            err = str(res.get("error_code") or res.get("reason_code") or "INVALID_FIELDS")
            return code, {
                "ok": False,
                "status": STATUS_REJECTED,
                "reason_code": err,
                "error_code": err,
                "llm_calls": 0,
                "data": {},
            }
        data_out = {k: v for k, v in res.items() if k not in ("ok", "status_code")}
        data_out["action"] = action
        data_out["perspective"] = "subject" if subject_view else "partner"
        data_out["llm_calls"] = 0
        return code, {
            "ok": True,
            "status": STATUS_OK,
            "command_status": "APPLIED",
            "schema": rp.SCHEMA_VERSION,
            "endpoint": "/api/phone/v02/recall-projection",
            "llm_calls": 0,
            "data": data_out,
            **data_out,
        }

    def v02_common_ground(self, *, subject_view: bool = False) -> tuple[int, dict[str, Any]]:
        """``GET /api/phone/v02/common-ground``: pure zero-write common ground & unseen-move projection."""

        from components.life.scripts import v02_common_ground as cg

        viewer = "subject" if subject_view else "partner"
        now_iso = self._somatic_now_iso()
        raw_view = cg.read_v02_common_ground_view(self.root, viewer=viewer, now_iso=now_iso)
        data_block: dict[str, Any] = {
            "schema": cg.SCHEMA_VERSION,
            "endpoint": "/api/phone/v02/common-ground",
            "perspective": viewer,
            "revision": len(raw_view.get("items") or []),
            "state_exists": (Path(self.root) / cg.COMMON_GROUND_REL_PATH).is_file(),
            "llm_calls": 0,
            **raw_view,
        }
        if subject_view:
            data_block["cognition_summary"] = cg.summarize_common_ground_for_cognition(
                self.root, now_iso=now_iso
            )
        return 200, {
            "ok": True,
            "status": STATUS_OK,
            "command_status": "READ",
            "schema": cg.SCHEMA_VERSION,
            "endpoint": "/api/phone/v02/common-ground",
            "perspective": viewer,
            "llm_calls": 0,
            "data": data_block,
            **raw_view,
        }

    def v02_common_ground_action(
        self,
        *,
        payload: Optional[Mapping[str, Any]] = None,
        subject_view: bool = False,
    ) -> tuple[int, dict[str, Any]]:
        """``POST /api/phone/v02/common-ground``: ground ledger, receipt state & unseen-move inquiry."""

        from components.life.scripts import v02_common_ground as cg

        body = dict(payload or {})
        action = str(body.get("action") or "").strip()
        if not action:
            return 400, {
                "ok": False,
                "status": STATUS_REJECTED,
                "reason_code": "ACTION_REQUIRED",
                "error_code": "ACTION_REQUIRED",
                "llm_calls": 0,
                "data": {},
            }
        if action in ("attempt_use_or_fetch_object", "search_nearby_spots") and not subject_view:
            return 403, {
                "ok": False,
                "status": STATUS_REJECTED,
                "reason_code": "FORBIDDEN_ACTOR_SIDE",
                "error_code": "FORBIDDEN_ACTOR_SIDE",
                "llm_calls": 0,
                "data": {},
            }
        res = cg.mutate_v02_common_ground(self.root, body, now_iso=self._somatic_now_iso())
        code = int(res.get("status_code") or (200 if res.get("ok") else 400))
        if not res.get("ok"):
            err = str(res.get("error_code") or res.get("reason_code") or "INVALID_FIELDS")
            return code, {
                "ok": False,
                "status": STATUS_REJECTED,
                "reason_code": err,
                "error_code": err,
                "llm_calls": 0,
                "data": {},
            }
        data_out = {k: v for k, v in res.items() if k not in ("ok", "status_code")}
        data_out["action"] = action
        data_out["perspective"] = "subject" if subject_view else "partner"
        data_out["llm_calls"] = 0
        return code, {
            "ok": True,
            "status": STATUS_OK,
            "command_status": "APPLIED",
            "schema": cg.SCHEMA_VERSION,
            "endpoint": "/api/phone/v02/common-ground",
            "llm_calls": 0,
            "data": data_out,
            **data_out,
        }

    # -- 具身冷暖 / 窗扇与外衣 / 桌边照顾（纯投影 + 一段显式本地仿真） ------------------

    def _somatic_state(self) -> tuple[dict[str, Any], Optional[str]]:
        from . import somatic_comfort_view as view

        return view.read_state(self.root)

    def _somatic_now_iso(self) -> str:
        """The same business clock the rest of the phone uses; never a second clock."""

        return self._home_now_iso(self._home_clock())

    @staticmethod
    def _somatic_place(world_result: Mapping[str, Any]) -> tuple[Optional[str],
                                                                 Optional[str],
                                                                 Optional[str]]:
        from . import somatic_comfort_view as view

        world = view.world_context(world_result)
        if world.get("status") != "available":
            return None, None, str(world.get("reason_code") or view.LOCATION_UNREADABLE)
        if world.get("travelling"):
            return None, None, view.LOCATION_UNSTABLE
        place = world.get("place_id")
        area = world.get("area_id")
        if not place or not area:
            return None, None, view.LOCATION_UNREADABLE
        return str(place), str(area), None

    def body_somatic(self) -> dict[str, Any]:
        """``GET /api/phone/body/somatic``: bounded four-modality projection, pure read."""

        from . import somatic_comfort_view as view

        world_result = self.world()
        body_view = self._body_from_world(world_result)
        state, state_reason = self._somatic_state()
        return envelope(True, STATUS_OK, view.somatic_block(
            world_result=world_result, body_view=body_view, state=state,
            state_reason=state_reason, now_iso=self._somatic_now_iso(),
            soundscape=self._home_soundscape(world_result)))

    def body_garment_window(self) -> dict[str, Any]:
        """``GET /api/phone/body/garment-window``: window / local position / outerwear."""

        from . import somatic_comfort_view as view

        world_result = self.world()
        state, state_reason = self._somatic_state()
        return envelope(True, STATUS_OK, view.garment_window_block(
            world_result=world_result, state=state, state_reason=state_reason,
            now_iso=self._somatic_now_iso()))

    def body_garment_window_action(self, *, action: str, state_value: Any = None,
                                   zone: Any = None, garment_id: Any = None,
                                   label: Any = None, dryness: Any = None,
                                   hang_position: Any = None) -> dict[str, Any]:
        """One explicit local condition.  Her window and her garment, never a partner's."""

        from . import somatic_comfort_view as view

        current, state_reason = self._somatic_state()
        if state_reason:
            return rejected(state_reason)
        now_iso = self._somatic_now_iso()
        if action == "set_window":
            new_state, changed, error = view.set_window(current, state_value, now_iso)
        elif action == "set_local_position":
            place, area, why = self._somatic_place(self.world())
            if why:
                return rejected(why)
            new_state, changed, error = view.set_local_position(
                current, zone=zone, place_id=place, area_id=area, now_iso=now_iso)
        elif action == "wear_outerwear":
            new_state, changed, error = view.wear_outerwear(
                current, garment_id=garment_id, label=label, dryness=dryness,
                now_iso=now_iso)
        elif action == "hang_outerwear":
            new_state, changed, error = view.hang_outerwear(
                current, garment_id=garment_id, hang_position=hang_position,
                dryness=dryness, now_iso=now_iso)
        else:
            return rejected("GARMENT_WINDOW_ACTION_UNSUPPORTED", action=action)
        if error:
            return rejected(error)
        if not changed:
            return envelope(True, STATUS_ALREADY, {
                "action": action, "changed": False,
                "revision": current.get("revision"),
                "updated_at": current.get("updated_at")})
        written = view.write_state(self.root, new_state)
        data = {"action": action, "changed": True,
                "revision": written.get("revision"),
                "updated_at": written.get("updated_at")}
        if action == "set_window":
            data["window"] = written.get("window")
        elif action == "set_local_position":
            data["local_position"] = written.get("local_position")
        else:
            data["garment"] = view.derive_garment(written, now_iso)
        return envelope(True, STATUS_OK, data)

    def _somatic_bound_drink_item(self, place_id: str, area_id: str) -> dict[str, Any]:
        """A real, currently usable thirst-relieving instance at the bound room.

        Selection is server-side only: the client never names an item, a path or a
        capability.  A bought snack with no declared thirst effect is not a drink.
        """

        answer = self.inventory()
        if not answer.get("ok"):
            return {"item_instance_id": None,
                    "reason_code": str(answer.get("reason_code")
                                       or "INVENTORY_UNAVAILABLE")}
        try:
            import world_consumable_contract as contract

            facts = contract.economy_facts(self.world_home)
            economy = facts.get("economy") if facts.get("status") == "present" else None
        except Exception:
            contract = None
            economy = None
        data = answer.get("data") if isinstance(answer.get("data"), Mapping) else {}
        expected = "room:%s/%s" % (place_id, area_id)
        for entry in (data.get("items") or []):
            if not isinstance(entry, Mapping) or entry.get("consumed"):
                continue
            if entry.get("usable") is not True:
                continue
            if str(entry.get("location") or "") != expected:
                continue
            item_id = str(entry.get("item_instance_id") or "")
            if not item_id or contract is None:
                continue
            try:
                declaration = contract.declaration_of(item_id, self.world_home,
                                                      economy=economy)
            except Exception:
                continue
            effects = declaration.get("lived_need_effects") \
                if isinstance(declaration, Mapping) else None
            thirst = effects.get("thirst") if isinstance(effects, Mapping) else None
            if not (isinstance(thirst, (int, float)) and not isinstance(thirst, bool)
                    and thirst > 0):
                continue
            return {"item_instance_id": item_id,
                    "label": entry.get("name"),
                    "reason_code": None}
        return {"item_instance_id": None,
                "reason_code": "NO_THIRST_RELIEVING_CONSUMABLE"}

    @staticmethod
    def _somatic_position_supported(world_result: Mapping[str, Any],
                                    position: str) -> tuple[bool, Optional[str]]:
        """``hand_side`` is always local; ``desk_side`` needs a real declared table here."""

        if position == "hand_side":
            return True, None
        data = world_result.get("data") if isinstance(world_result.get("data"),
                                                      Mapping) else {}
        environment = data.get("local_environment") \
            if isinstance(data.get("local_environment"), Mapping) else {}
        furniture = environment.get("furniture") \
            if isinstance(environment.get("furniture"), list) else []
        if any(isinstance(row, Mapping) and str(row.get("kind") or "") == "table"
               for row in furniture):
            return True, None
        objects = data.get("objects") if isinstance(data.get("objects"), Mapping) else {}
        surfaces = objects.get("surfaces") if isinstance(objects.get("surfaces"),
                                                          Mapping) else {}
        for row in (surfaces.get("here") or []):
            if isinstance(row, Mapping) and str(row.get("surface_kind") or "") in (
                    "desk_surface", "table_surface"):
                return True, None
        return False, "NO_DECLARED_DESK_SURFACE"

    def body_care_offering(self) -> dict[str, Any]:
        """``GET /api/phone/body/care-offering``: one drink slot + one cushion slot."""

        from . import somatic_comfort_view as view

        world_result = self.world()
        body_view = self._body_from_world(world_result)
        state, state_reason = self._somatic_state()
        return envelope(True, STATUS_OK, view.care_offering_block(
            world_result=world_result, body_view=body_view, state=state,
            state_reason=state_reason, now_iso=self._somatic_now_iso()))

    def body_care_action(self, *, action: str, slot: str, actor_side: Optional[str] = None,
                         kind: Optional[str] = None, position: Optional[str] = None
                         ) -> dict[str, Any]:
        """Explicit offer / take / drink / use / stow / withdraw on the local slots.

        Only ``drink`` writes outside this instance: it forwards the already legal
        ``PhoneShopping.use`` (the existing owner CONSUME channel).  Everything else is
        this instance's own bounded simulation JSON.
        """

        from . import somatic_comfort_view as view

        current, state_reason = self._somatic_state()
        if state_reason:
            return rejected(state_reason)
        now_iso = self._somatic_now_iso()
        world_result = self.world()
        place, area, why = self._somatic_place(world_result)
        if action in ("offer", "take", "drink", "use"):
            if why:
                return rejected(why)
        if action in ("take", "drink") and slot != "drink":
            return rejected("CARE_SLOT_ACTION_MISMATCH", action=action, slot=slot)
        if action == "use" and slot != "cushion":
            return rejected("CARE_SLOT_ACTION_MISMATCH", action=action, slot=slot)
        if action == "offer":
            supported, why_position = self._somatic_position_supported(
                world_result, str(position))
            if not supported:
                return rejected(why_position or "CARE_POSITION_UNSUPPORTED",
                                position=position)
            bound = None
            if slot == "drink":
                bound = self._somatic_bound_drink_item(place, area)
                if bound.get("item_instance_id") is None:
                    bound = None
            new_state, changed, error, offer = view.offer_care(
                current, slot=slot, kind=kind, actor_side=actor_side,
                position=position, place_id=place, area_id=area, now_iso=now_iso,
                bound_item=bound)
        elif action == "take":
            new_state, changed, error, offer = view.take_drink(
                current, place_id=place, area_id=area, now_iso=now_iso)
        elif action == "use":
            body_view = self._body_from_world(world_result)
            pose = view.body_context(body_view).get("pose")
            new_state, changed, error, offer = view.use_cushion(
                current, pose=pose, place_id=place, area_id=area, now_iso=now_iso)
        elif action == "stow":
            new_state, changed, error, offer = view.stow_care(
                current, slot=slot, now_iso=now_iso)
        elif action == "withdraw":
            new_state, changed, error, offer = view.withdraw_care(
                current, slot=slot, now_iso=now_iso)
        elif action == "drink":
            return self._somatic_drink(current, now_iso=now_iso, place=place, area=area)
        else:
            return rejected("CARE_ACTION_UNSUPPORTED", action=action)
        if error:
            return rejected(error)
        if not changed:
            return envelope(True, STATUS_ALREADY, {
                "action": action, "slot": slot, "changed": False,
                "revision": current.get("revision"),
                "updated_at": current.get("updated_at"),
                "offer": offer})
        written = view.write_state(self.root, new_state)
        return envelope(True, STATUS_OK, {
            "action": action, "slot": slot, "changed": True,
            "revision": written.get("revision"),
            "updated_at": written.get("updated_at"),
            "offer": offer})

    def _somatic_drink(self, current: Mapping[str, Any], *, now_iso: str,
                       place: str, area: str) -> dict[str, Any]:
        from . import somatic_comfort_view as view

        row = (current.get("care") or {}).get("drink")
        if not isinstance(row, Mapping):
            return rejected("NO_DRINK_OFFER")
        if row.get("state") == "consumed":
            # Replaying the same action never drinks twice: the owner already decided.
            return envelope(True, STATUS_ALREADY, {
                "action": "drink", "slot": "drink", "changed": False,
                "consumed": True,
                "bound_item_instance_id": row.get("bound_item_instance_id"),
                "receipt_ref": row.get("receipt_ref"),
                "operation_key": row.get("drink_operation_key"),
                "replayed": True})
        if row.get("state") != "taken":
            return rejected("DRINK_REQUIRES_TAKE_FIRST", state=row.get("state"))
        item_id = str(row.get("bound_item_instance_id") or "")
        if not item_id:
            # No real thirst-relieving instance was bound: never reduce thirst locally,
            # never invent a receipt, never sign a new authority.
            return rejected("DRINK_UNSUPPORTED",
                            detail="NO_THIRST_RELIEVING_CONSUMABLE",
                            consumed=False)
        key = "somatic-drink:%s" % str(row.get("offer_id") or "")
        answer = self.use(item_id, key)
        status = str(answer.get("status") or "")
        data = answer.get("data") if isinstance(answer.get("data"), Mapping) else {}
        if answer.get("ok") and status in (STATUS_OK, STATUS_ALREADY):
            receipt = data.get("receipt") if isinstance(data.get("receipt"),
                                                         Mapping) else {}
            receipt_ref = receipt.get("receipt_id") or data.get("receipt_id")
            new_state, changed = view.mark_drink_result(
                current, outcome="consumed", receipt_ref=receipt_ref,
                operation_key=key, now_iso=now_iso)
            if changed:
                view.write_state(self.root, new_state)
            return envelope(True, STATUS_OK if status == STATUS_OK else STATUS_ALREADY, {
                "action": "drink", "slot": "drink",
                "changed": bool(changed), "consumed": True,
                "bound_item_instance_id": item_id,
                "operation_key": key,
                "receipt": dict(receipt) or None,
                "applied_to_body": data.get("applied_to_body"),
                "source": "world_phone_shopping.use"})
        if status == STATUS_UNKNOWN or answer.get("status") == STATUS_UNKNOWN:
            # Never mark consumed while the owner is unknown.  The local slot stays
            # "taken"; replaying this same action reuses the same key to reconcile.
            return envelope(False, STATUS_UNKNOWN, {
                "action": "drink", "slot": "drink", "consumed": False,
                "bound_item_instance_id": item_id, "operation_key": key,
                "note": "owner outcome unknown; replay the same action to reconcile, "
                        "never a new key"}, answer.get("reason_code"))
        return rejected(str(answer.get("reason_code") or "DRINK_REFUSED"),
                        consumed=False, owner_status=status,
                        bound_item_instance_id=item_id)

    # -- S03 current claims ----------------------------------------------------------------

    def _s03_claims(self):
        """The instance's own claim aggregate.  No graph, no cache, no second store."""

        path = str(self.instance.get("s03_claims_db") or "").strip()
        if not path:
            raise PhoneUnavailable("S03_CLAIMS_UNBOUND")
        from s03_claims import S03ClaimService, S03ClaimsError

        subject = str(self.instance.get("s03_claims_subject") or self.subject)
        try:
            return S03ClaimService(path, subject=subject)
        except S03ClaimsError as exc:
            raise PhoneUnavailable("S03_CLAIMS_UNAVAILABLE",
                                   type(exc).__name__) from exc

    def claims(self, *, limit: int = 8) -> dict[str, Any]:
        """``GET /api/phone/claims``: bounded current claims, observed at the phone clock.

        Pure read: no World read, no model call, no Memory write.  Each row names its
        scope/context/status/source kind; conflicts stay side by side.
        """

        try:
            view = self._s03_claims().current_view(now=self._clock(), limit=limit)
        except PhoneUnavailable as exc:
            return unavailable(exc.reason_code)
        except Exception:
            return unavailable("S03_CLAIMS_READ_FAILED")
        return envelope(True, STATUS_OK, view)

    def claim_slot(self, slot_id: str) -> dict[str, Any]:
        """``GET /api/phone/claims/<slot>``: one slot's current state and revision chain."""

        from s03_claims import S03ClaimsRefused, S03ClaimsError

        try:
            service = self._s03_claims()
            slot = service.slot_view(slot_id=slot_id, now=self._clock())
            if slot.get("reason_code") == "NO_SLOT":
                # A slot that was never asked is UNKNOWN, not a refused read: there is
                # no history to return and inventing one would be a second store.
                history = {"ok": True, "slot_id": slot_id, "claims": [],
                           "reason_code": "NO_SLOT", "read_only": True}
            else:
                history = service.history(slot_id=slot_id, now=self._clock())
        except S03ClaimsRefused as exc:
            return rejected(exc.reason_code)
        except PhoneUnavailable as exc:
            return unavailable(exc.reason_code)
        except S03ClaimsError:
            return unavailable("S03_CLAIMS_READ_FAILED")
        return envelope(True, STATUS_OK, {"slot": slot, "history": history})

    def claim_observe(self, operation_key: str) -> dict[str, Any]:
        """``POST /api/phone/claims/observations``: one formal World read -> evidence.

        The payload carries only the operation key.  The observation itself is taken
        here, from the World owner's own snapshot facade; a client cannot submit a
        "world fact", a role, an authority or an arbitrary world id.
        """

        from s03_claims import S03ClaimsRefused

        try:
            service = self._s03_claims()
            world = self._world_instance()
            snapshot = world.snapshot()
        except PhoneUnavailable as exc:
            return unavailable(exc.reason_code)
        except Exception:
            return unavailable("S03_WORLD_OBSERVATION_UNAVAILABLE")
        try:
            result = service.observe_world(snapshot, now=self._clock(),
                                           operation_key=operation_key)
        except S03ClaimsRefused as exc:
            return rejected(exc.reason_code)
        except Exception:
            return unavailable("S03_CLAIMS_WRITE_FAILED")
        status = STATUS_ALREADY if str(result.get("status")) == "ALREADY_APPLIED" \
            else STATUS_OK
        return envelope(True, status, result)

    def create_derived_project(self, *, title: str, content: str, source_artifact_id: str,
                               source_version_id: str, purpose: str, note: str = "",
                               operation_key: str) -> dict[str, Any]:
        """``POST`` one **new** work that records the old artifact/version it was taken from."""
        try:
            answer = self._projects_service().create_derived_project(
                title=title, content=content, source_artifact_id=source_artifact_id,
                source_version_id=source_version_id, purpose=purpose, note=note,
                operation_key=operation_key)
        except PhoneUnavailable as exc:
            return unavailable(exc.reason_code)
        except Exception as exc:
            return unavailable("PROJECTS_UNAVAILABLE", error_type=type(exc).__name__)
        mapped = _project_answer(answer, "PROJECT_DERIVE_REFUSED")
        if isinstance(mapped.get("data"), dict):
            if answer.get("provenance") is not None:
                mapped["data"]["provenance"] = answer.get("provenance")
            if answer.get("source_reason_code"):
                mapped["data"]["source_reason_code"] = answer.get("source_reason_code")
        return mapped

    def create_project(self, *, title: str, content: str,
                       operation_key: str) -> dict[str, Any]:
        """One explicit new project.  P sends title+text; the owner mints the artifact."""
        try:
            answer = self._projects_service().create_project(
                title=title, content=content, operation_key=operation_key)
        except PhoneUnavailable as exc:
            return unavailable(exc.reason_code)
        except Exception as exc:
            return unavailable("PROJECTS_UNAVAILABLE", error_type=type(exc).__name__)
        return _project_answer(answer, "PROJECT_CREATE_REFUSED")

    def commit_project(self, *, artifact_id: str, content: str, expected_head: str,
                       operation_key: str) -> dict[str, Any]:
        """One new version of one project, under the caller's own CAS head.

        A *material* or a *research* artifact is refused here by name: committing to one from the
        project screen would rewrite its first-line marker (and so silently turn a material into a
        work, or drop a research's citations).  Those two have their own screens and their own
        owner calls.
        """
        guard = self._continuable_guard(artifact_id)
        if guard is not None:
            return guard
        try:
            answer = self._projects_service().commit_project(
                artifact_id=artifact_id, content=content, expected_head=expected_head,
                operation_key=operation_key)
        except PhoneUnavailable as exc:
            return unavailable(exc.reason_code)
        except Exception as exc:
            return unavailable("PROJECTS_UNAVAILABLE", error_type=type(exc).__name__)
        return _project_answer(answer, "PROJECT_COMMIT_REFUSED")

    def _content_kind(self, content: Any) -> str:
        """First-line marker -> which kind of document this is (``unknown`` on doubt).

        ``review`` / ``method`` are B's practice markers and are checked first: a project screen
        must never be able to rewrite one (that would silently drop the marker's metadata).
        """
        try:
            from personal_practice_service import practice_kind
        except Exception:
            practice_kind = None  # type: ignore[assignment]
        if callable(practice_kind):
            try:
                found = practice_kind(content)
            except Exception:
                return "unknown"
            if found in ("review", "method"):
                return found
        try:
            from .album import is_album_manifest
        except Exception:  # a broken import is not a project
            is_album_manifest = None  # type: ignore[assignment]
        if callable(is_album_manifest):
            try:
                if is_album_manifest(content):
                    return "album"
            except Exception:
                return "unknown"
        try:
            from research_workspace_service import is_material_content, is_research_content
        except Exception:  # a broken import is not a project
            return "unknown"
        try:
            if is_research_content(content):
                return "research"
            if is_material_content(content):
                return "material"
        except Exception:
            return "unknown"
        return "project"

    def _annotate_project_types(self, service: Any, rows: list[dict[str, Any]]) -> None:
        """Type each listed row from the artifact's own content (bounded, read-only)."""
        for index, row in enumerate(rows):
            artifact_type = "unknown"
            artifact_id = str(row.get("artifact_id") or "")
            if artifact_id and index < MAX_PROJECT_TYPE_SCAN:
                try:
                    view = service.port.read_artifact(artifact_id)
                    artifact_type = self._content_kind(view.get("content"))
                except Exception:
                    artifact_type = "unknown"
            row["artifact_type"] = artifact_type
            row["continuable"] = artifact_type == "project"

    def _continuable_guard(self, artifact_id: str) -> Optional[dict[str, Any]]:
        """Refuse a project write that would overwrite a material / research / practice marker."""
        try:
            service = self._projects_service()
            view = service.port.read_artifact(artifact_id)
        except Exception:
            # Cannot tell: let the owner answer for itself (it still enforces CAS and bounds).
            return None
        artifact_type = self._content_kind(view.get("content"))
        if artifact_type == "project":
            return None
        if artifact_type == "research":
            return rejected("PROJECT_IS_A_RESEARCH", artifact_id=artifact_id,
                            detail="研究作品请在研究工作台里修改；这里写会改写它的标记。")
        if artifact_type == "material":
            return rejected("PROJECT_IS_A_MATERIAL", artifact_id=artifact_id,
                            detail="资料不是可续写作品；导入过的文本不会被覆盖。")
        if artifact_type in ("review", "method"):
            return rejected("PROJECT_IS_A_PRACTICE_DOCUMENT", artifact_id=artifact_id,
                            artifact_type=artifact_type,
                            detail="回顾/做法要在实践工作台里改；这里写会改写它的第一行标记。")
        if artifact_type == "album":
            return rejected("PROJECT_IS_AN_ALBUM_IMAGE", artifact_id=artifact_id,
                            detail="相册图片不是可续写作品；它只在相册里查看。")
        return None

    def close_project(self, *, artifact_id: str, expected_revision: int,
                      operation_key: str) -> dict[str, Any]:
        """Close (archive) one project.  Archiving is the owner's verb; nothing is deleted."""
        try:
            answer = self._projects_service().close_project(
                artifact_id=artifact_id, expected_revision=expected_revision,
                operation_key=operation_key)
        except PhoneUnavailable as exc:
            return unavailable(exc.reason_code)
        except Exception as exc:
            return unavailable("PROJECTS_UNAVAILABLE", error_type=type(exc).__name__)
        return _project_answer(answer, "PROJECT_CLOSE_REFUSED")

    # -- EP-16: the private album (narrow owner reads + one explicit import) --------------

    def _album(self):
        """The album transport over this instance's own Supply socket and grant."""
        from .album import PhoneAlbum

        if not Path(self.supply_socket).exists():
            raise PhoneUnavailable("SUPPLY_UNAVAILABLE")
        if not self.artifact_grant_id:
            raise PhoneUnavailable("ALBUM_UNBOUND")
        return PhoneAlbum(self.supply_socket, self.subject, grant_id=self.artifact_grant_id)

    def _album_row(self, raw: Any) -> Optional[dict[str, Any]]:
        """One owner album row, narrowed to the facts the picture surface may show."""
        if not isinstance(raw, Mapping):
            return None
        artifact_id = raw.get("artifact_id")
        content_type = raw.get("content_type")
        media = raw.get("media")
        if not isinstance(artifact_id, str) or not artifact_id:
            return None
        if content_type not in ("image/png", "image/jpeg"):
            return None
        if not isinstance(media, Mapping):
            return None
        name = media.get("name")
        digest = media.get("sha256")
        size = media.get("bytes")
        if not isinstance(name, str) or not isinstance(digest, str):
            return None
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            return None
        created = raw.get("created_at")
        created = float(created) if isinstance(created, (int, float)) and not isinstance(created, bool) else None
        iso = raw.get("created_at_iso")
        return {
            "artifact_id": artifact_id,
            "caption": raw.get("caption") if isinstance(raw.get("caption"), str) else "",
            "content_type": content_type,
            "media": {"name": name, "sha256": digest, "bytes": size},
            "source": {"origin": "user_import", "declared": "self_declared"},
            "lifecycle": raw.get("lifecycle") if isinstance(raw.get("lifecycle"), str) else "",
            "created_at": created,
            "created_at_iso": iso if isinstance(iso, str) else None,
            "head_version_id": raw.get("head_version_id") if isinstance(raw.get("head_version_id"), str) else None,
            "version_number": raw.get("version_number") if isinstance(raw.get("version_number"), int) else None,
        }

    def album_list(self) -> dict[str, Any]:
        """``GET``: the pictures this subject imported, newest first.  No bytes, no paths."""
        try:
            answer = self._album().list_images()
        except PhoneUnavailable as exc:
            return unavailable(exc.reason_code)
        except Exception as exc:
            return unavailable("ALBUM_UNAVAILABLE", error_type=type(exc).__name__)
        if answer.get("status") != "OK":
            return _album_failure(answer, "ALBUM_UNAVAILABLE")
        rows = answer.get("images")
        images = [row for row in (self._album_row(entry) for entry in (rows or []))
                  if row is not None]
        marks = self._camera_records()
        for image in images:
            generated = marks.get(image["artifact_id"])
            image["generated_locally"] = generated is not None
            image["source_label"] = "local_generated" if generated is not None else "user_import"
        return {
            "ok": True,
            "status": STATUS_OK,
            "images": images,
            "count": len(images),
            "bounded": bool(answer.get("bounded")),
            "source": "ManagedArtifact",
            "private_media_included": False,
        }

    def album_detail(self, artifact_id: str) -> dict[str, Any]:
        """``GET`` one picture's caption/source facts.  No bytes in this view."""
        try:
            answer = self._album().read_image(artifact_id)
        except PhoneUnavailable as exc:
            return unavailable(exc.reason_code)
        except Exception as exc:
            return unavailable("ALBUM_UNAVAILABLE", error_type=type(exc).__name__)
        if answer.get("status") != "OK":
            return _album_failure(answer, "ALBUM_IMAGE_UNAVAILABLE")
        row = self._album_row(answer.get("image"))
        if row is None:
            return unavailable("ALBUM_ANSWER_INVALID")
        generated = self._camera_records().get(row["artifact_id"])
        row["generated_locally"] = generated is not None
        row["source_label"] = "local_generated" if generated is not None else "user_import"
        if generated is not None:
            # Her original words and the exact English prompt that was actually sent, so the
            # album detail can show both.  Old captures without them stay None -- never invented.
            scene = generated.get("scene")
            prompt = generated.get("generation_prompt")
            translation = generated.get("translation")
            row["scene"] = scene if isinstance(scene, str) else None
            row["generation_prompt"] = prompt if isinstance(prompt, str) else None
            row["translation"] = (dict(translation) if isinstance(translation, Mapping)
                                  else None)
        return {"ok": True, "status": STATUS_OK, "artifact_id": row["artifact_id"],
                "image": row, "private_media_included": False}

    def album_image(self, artifact_id: str) -> dict[str, Any]:
        """``GET`` one picture's real bytes, verified against the owner's own manifest."""
        try:
            answer = self._album().read_image_bytes(artifact_id)
        except PhoneUnavailable as exc:
            return unavailable(exc.reason_code)
        except Exception as exc:
            return unavailable("ALBUM_UNAVAILABLE", error_type=type(exc).__name__)
        if answer.get("status") != "OK":
            return _album_failure(answer, "ALBUM_IMAGE_UNAVAILABLE")
        row = self._album_row(answer.get("image"))
        content_type = answer.get("content_type")
        data_text = answer.get("data")
        if row is None or content_type != row["content_type"] or not isinstance(data_text, str):
            return unavailable("ALBUM_ANSWER_INVALID")
        try:
            data = base64.b64decode(data_text.encode("ascii"), validate=True)
        except Exception:
            return unavailable("ALBUM_ANSWER_INVALID")
        if (len(data) != row["media"]["bytes"]
                or hashlib.sha256(data).hexdigest() != row["media"]["sha256"]):
            return unavailable("ALBUM_ANSWER_INVALID")
        return {"ok": True, "status": STATUS_OK, "artifact_id": row["artifact_id"],
                "content_type": content_type, "data": data, "image": row,
                "private_media_included": True}

    def album_import(self, *, caption: str, content_type: str, data: str,
                     operation_key: str) -> dict[str, Any]:
        """One explicit import.  P sends the user's caption and picture; the owner decides."""
        try:
            answer = self._album().import_image(
                caption=caption, content_type=content_type, data=data,
                operation_key=operation_key)
        except PhoneUnavailable as exc:
            return unavailable(exc.reason_code)
        except Exception as exc:
            return unavailable("ALBUM_UNAVAILABLE", error_type=type(exc).__name__)
        return _album_write_answer(answer, "ALBUM_IMPORT_REFUSED")

    # -- the local camera: one generated picture, then the *existing* album import ---------

    def _picture_lab(self):
        """The one loopback picture runtime, built from this instance's own environment."""
        lab = getattr(self, "_camera_generator", None)
        if lab is None:
            from .camera import LocalPictureLab

            lab = LocalPictureLab(environment=self.environment)
            self._camera_generator = lab
        return lab

    def _scene_translator(self):
        """The one local Chinese->English tag converter, built from this instance's environment.

        It is a technical converter, not her cognition: it only renders the scene she already
        gave, and a failure is a named refusal -- never a silent fallback to the Chinese text.
        """
        translator = getattr(self, "_camera_translator", None)
        if translator is None:
            from .camera import SceneTranslator

            translator = SceneTranslator(environment=self.environment)
            self._camera_translator = translator
        return translator

    def _camera_index_path(self) -> Path:
        return self.root / "phone" / "camera.jsonl"

    def _camera_rows(self) -> list[dict[str, Any]]:
        """The phone's own append-only capture index.  It is attribution, never a second owner.

        A missing or unreadable file is an empty index, not a fabrication: the pictures still
        live in the Supply owner and are still listed from there.
        """
        try:
            lines = self._camera_index_path().read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        rows: list[dict[str, Any]] = []
        for line in lines:
            try:
                row = json.loads(line)
            except (TypeError, ValueError):
                continue
            if isinstance(row, dict) and isinstance(row.get("key"), str):
                rows.append(row)
        return rows

    def _camera_records(self) -> dict[str, dict[str, Any]]:
        """``artifact_id -> the most recent capture record`` for source labelling."""
        records: dict[str, dict[str, Any]] = {}
        for row in self._camera_rows():
            artifact_id = row.get("artifact_id")
            if isinstance(artifact_id, str) and artifact_id:
                records[artifact_id] = row
        return records

    def _camera_record(self, key: str) -> Optional[dict[str, Any]]:
        found: Optional[dict[str, Any]] = None
        for row in self._camera_rows():
            if row.get("key") == key:
                found = row
        return found

    def _camera_record_append(self, row: Mapping[str, Any]) -> bool:
        path = self._camera_index_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
        except OSError:
            return False
        return True

    def camera_capture(self, *, scene: str, caption: str,
                       operation_key: str) -> dict[str, Any]:
        """One local picture from her scene text, saved through the existing album import.

        The operation key is the phone's idempotency handle for the *whole* capture: before any
        generation the existing index is consulted, so an ordinary retry with the original key
        never asks the GPU for a second expensive picture.  The album import gets a derived key,
        keeping camera captures apart from explicit imports of the caller's own files.
        """
        from .camera import CameraRefused, MAX_CAPTION_CHARS, MAX_SCENE_CHARS

        text = scene if isinstance(scene, str) else ""
        text = text.strip()
        words = caption if isinstance(caption, str) else ""
        words = words.strip()
        key = str(operation_key)
        if not text or len(text) > MAX_SCENE_CHARS or "\x00" in text:
            return rejected("CAMERA_SCENE_INVALID")
        if not words or len(words) > MAX_CAPTION_CHARS or "\x00" in words or "\r" in words:
            return rejected("CAMERA_CAPTION_INVALID")

        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        existing = self._camera_record(key)
        if existing is not None:
            if existing.get("scene_sha256") != digest or existing.get("caption") != words:
                return rejected("CAMERA_OPERATION_KEY_REUSED")
            artifact_id = str(existing.get("artifact_id") or "")
            detail = self.album_detail(artifact_id) if artifact_id else unavailable(
                "CAMERA_PHOTO_UNREADABLE")
            if detail.get("ok") is not True:
                return unavailable("CAMERA_PHOTO_NO_LONGER_READABLE", artifact_id=artifact_id)
            stored_scene = existing.get("scene")
            stored_prompt = existing.get("generation_prompt")
            stored_translation = existing.get("translation")
            return envelope(True, STATUS_ALREADY, {
                "artifact_id": artifact_id,
                "caption": words,
                "operation_key": key,
                "album_operation_key": existing.get("album_operation_key"),
                "generated_locally": True,
                "generated": False,
                "reused": True,
                "scene": stored_scene if isinstance(stored_scene, str) else None,
                "generation_prompt": (stored_prompt if isinstance(stored_prompt, str) else None),
                "translation": (dict(stored_translation)
                                if isinstance(stored_translation, Mapping) else None),
                "image": detail.get("image"),
            })

        # Her Chinese scene is rendered into the tag-style English this checkpoint follows.  This
        # is a converter, not a second author: the original text is kept and a failed conversion
        # refuses the capture (no picture, nothing saved) instead of feeding Chinese to the GPU.
        try:
            translation = self._scene_translator().prompt_for(text)
        except CameraRefused as exc:
            hard = exc.reason_code in {
                "CAMERA_TRANSLATOR_BUSY", "CAMERA_TRANSLATOR_URL_INVALID",
                "CAMERA_SCENE_INVALID",
            }
            answer = {"detail": exc.detail} if exc.detail else {}
            return (rejected(exc.reason_code, **answer) if hard
                    else unavailable(exc.reason_code, **answer))
        except Exception as exc:
            return unavailable("CAMERA_TRANSLATION_UNAVAILABLE",
                               error_type=type(exc).__name__)
        if not isinstance(translation, Mapping):
            return unavailable("CAMERA_TRANSLATION_UNAVAILABLE",
                               detail="translator_answer_invalid")
        prompt = str(translation.get("prompt") or "")
        if not prompt:
            return unavailable("CAMERA_TRANSLATION_UNAVAILABLE", detail="empty_prompt")

        try:
            data, content_type = self._picture_lab().generate(prompt)
        except CameraRefused as exc:
            hard = exc.reason_code in {
                "CAMERA_BUSY", "CAMERA_SCENE_INVALID", "CAMERA_URL_MISSING",
                "CAMERA_URL_NOT_LOOPBACK", "CAMERA_URL_INVALID", "CAMERA_REDIRECT_REFUSED",
            }
            answer = {"detail": exc.detail} if exc.detail else {}
            return (rejected(exc.reason_code, **answer) if hard
                    else unavailable(exc.reason_code, **answer))
        except Exception as exc:
            return unavailable("CAMERA_GENERATION_FAILED", error_type=type(exc).__name__)

        album_key = "camera:" + hashlib.sha256(
            ("phone|camera|" + key).encode("utf-8")).hexdigest()[:16]
        imported = self.album_import(caption=words, content_type=content_type,
                                     data=base64.b64encode(data).decode("ascii"),
                                     operation_key=album_key)
        if imported.get("ok") is not True:
            # The picture was generated, but it was not saved.  Say so; never claim a photo.
            return imported
        payload = imported.get("data") if isinstance(imported.get("data"), Mapping) else {}
        artifact_id = str(payload.get("artifact_id") or "")
        recorded = self._camera_record_append({
            "at": self._clock(),
            "key": key,
            "scene_sha256": digest,
            "scene": text,
            "generation_prompt": prompt,
            "translation": {"translated": bool(translation.get("translated")),
                            "source": str(translation.get("source") or "")},
            "caption": words,
            "artifact_id": artifact_id,
            "album_operation_key": album_key,
            "content_type": content_type,
            "media": payload.get("media"),
        })
        return envelope(True, STATUS_OK, {
            "artifact_id": artifact_id,
            "caption": words,
            "scene": text,
            "generation_prompt": prompt,
            "translation": {"translated": bool(translation.get("translated")),
                            "source": str(translation.get("source") or "")},
            "content_type": content_type,
            "media": payload.get("media"),
            "version_id": payload.get("version_id"),
            "operation_key": key,
            "album_operation_key": album_key,
            "generated_locally": True,
            "generated": True,
            "reused": False,
            "index_recorded": recorded,
        })

    # -- shares (B's LifeSharingService; P forwards, it decides nothing) -------------------

    def _sharing_service(self):
        """B's ``LifeSharingService`` over **this** instance's port, subject, workspace, grant.

        The binding comes from the instance the ``PhoneService`` was constructed with -- never from
        a process-global environment variable, so two phones on one machine can never borrow each
        other's subject or grant.  Nothing is created or migrated here: every share is one
        artifact on the same ``ManagedArtifact`` owner, and ``LifeSharingService`` keeps no store.

        The private-items document is named as a non-source (it is her bookkeeping document, not a
        work); the reading material is **not** excluded -- it is one of her real artifacts, so it
        stays selectable as a source of its own kind.
        """

        from private_project_service import PrivateProjectService, default_excluded_artifact_ids

        from . import sharing as phone_sharing

        if self._sharing is not None:
            return self._sharing
        if not self.workspace_id or not self.artifact_grant_id:
            raise PhoneUnavailable("SHARES_UNBOUND")
        projects = PrivateProjectService(
            self._port(), self.workspace_id, self.artifact_grant_id,
            subject_id=self.subject,
            excluded_artifact_ids=default_excluded_artifact_ids(self.private_items_artifact_id))
        self._sharing = phone_sharing.PhoneSharing(
            projects, subject=self.subject, workspace_id=self.workspace_id,
            reading_artifact_id=self.reading_artifact_id)
        return self._sharing

    def _sharing_call(self, method: str, **kwargs: Any) -> dict[str, Any]:
        """One call on the share facade; ``unavailable`` is the only thing P adds."""

        try:
            answer = getattr(self._sharing_service(), method)(**kwargs)
        except PhoneUnavailable as exc:
            return unavailable(exc.reason_code)
        except Exception as exc:  # owner refused to build / an unexpected shape
            return unavailable("SHARES_UNAVAILABLE", error_type=type(exc).__name__)
        if not isinstance(answer, Mapping):
            return unavailable("SHARES_UNAVAILABLE")
        return dict(answer)

    def shares(self, states: Optional[list[str]] = None,
               limit: Optional[int] = None) -> dict[str, Any]:
        """``GET``: every share this instance holds -- **without** the share text."""

        return self._sharing_call("listing", states=states, limit=limit)

    def share(self, share_id: str) -> dict[str, Any]:
        """``GET`` one share: state, source currency, receipt, and its bounded text if readable."""

        return self._sharing_call("detail", share_id=share_id)

    def share_sources(self) -> dict[str, Any]:
        """``GET`` which of her artifacts may be a source now (readable projects + reading)."""

        return self._sharing_call("sources")

    def share_prepare(self, *, source_artifact_id: str, share_text: Optional[str] = None,
                      excerpt_chars: Optional[int] = None, title: Optional[str] = None,
                      expires_after_s: Optional[int] = None,
                      operation_key: Optional[str] = None) -> dict[str, Any]:
        """``POST``: one explicit draft.  Audience and channel are P's fixed constants."""

        return self._sharing_call(
            "prepare", source_artifact_id=source_artifact_id, share_text=share_text,
            excerpt_chars=excerpt_chars, title=title, expires_after_s=expires_after_s,
            operation_key=operation_key)

    def share_action(self, *, share_id: str, action: str, expected_head: str,
                     operation_key: Optional[str] = None,
                     defer_condition: Optional[str] = None,
                     opportunity_ref: Optional[str] = None,
                     reason: Optional[str] = None) -> dict[str, Any]:
        """``POST``: one explicit ``send_local`` / ``defer`` / ``drop`` under her own CAS head."""

        return self._sharing_call(
            "act", share_id=share_id, action=action, expected_head=expected_head,
            operation_key=operation_key, defer_condition=defer_condition,
            opportunity_ref=opportunity_ref, reason=reason)

    def share_operation(self, operation_key: str) -> dict[str, Any]:
        """``GET`` reconcile one **original** share key: what the phone recorded, nothing more.

        A share has no owner-side operation ledger to ask (B derives its keys from the intent and
        the owner's own ``resolve_operation`` is used *inside* ``apply_action``), so this is a pure
        read of two real things: the phone's append-only attribution journal
        (``phone/operations.jsonl``) for that exact key, and every share's **current** state.  It
        never writes, never settles a clock and never mints a second key.
        """

        records: list[dict[str, Any]] = []
        path = self._journal_path()
        if path.exists():
            try:
                with path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            row = json.loads(line)
                        except ValueError:
                            continue
                        if not isinstance(row, Mapping):
                            continue
                        if str(row.get("operation_key")) != str(operation_key):
                            continue
                        records.append({
                            "at": str(row.get("at") or "")[:40],
                            "actor": str(row.get("actor") or "")[:20],
                            "action": str(row.get("action") or "")[:60],
                            "status": str(row.get("status") or "")[:30],
                            "reason_code": str(row.get("reason_code") or "")[:64],
                            "ok": bool(row.get("ok")),
                        })
            except OSError:
                records = []
        listing = self.shares()
        shares: list[dict[str, Any]] = []
        payload = listing.get("data") if isinstance(listing.get("data"), Mapping) else {}
        for row in payload.get("shares") or ():
            if not isinstance(row, Mapping):
                continue
            shares.append({"share_id": str(row.get("share_id") or "")[:200],
                           "state": str(row.get("state") or "")[:30],
                           "head_version_id": str(row.get("head_version_id") or "")[:200],
                           "delivered": bool(row.get("delivered")),
                           "sendable": bool(row.get("sendable"))})
        return {
            "ok": True,
            "status": "ok",
            "data": {
                "operation_key": str(operation_key),
                "records": records[-20:],
                "recorded": bool(records),
                "shares": shares[:100],
                "shares_ok": bool(listing.get("ok")),
                "shares_reason_code": str(listing.get("reason_code") or "")[:64],
                # 对账是读，不是重发：这句话每次都说清楚。
                "note": "只读对账：不会用新键重发，也不会改写这条分享",
                "local_only": True,
            },
        }

    # -- external stop (local operator; the Activity owner is still LR-2) -------------------

    def _life_canonical_state(self) -> tuple[Optional[dict[str, Any]], str]:
        """Read-only view of the Life canonical state file.  Never written, never guessed."""

        state_root = self._stop_state_root()
        if state_root is None:
            return None, "LIFE_STATE_ROOT_UNBOUND"
        path = Path(state_root) / "canonical_life_state.json"
        if path.is_symlink() or not path.is_file():
            return None, "CANONICAL_STATE_MISSING"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None, "CANONICAL_STATE_UNREADABLE"
        if not isinstance(payload, dict) or not isinstance(payload.get("activities"), dict):
            return None, "CANONICAL_STATE_MALFORMED"
        return payload, ""

    def _owner_stop_truth(self, activity_id: Any) -> tuple[str, str]:
        """``(activity status, stop state)`` straight from the canonical owner record."""

        state, _reason = self._life_canonical_state()
        if state is None:
            return "", ""
        act = (state.get("activities") or {}).get(str(activity_id or ""))
        if not isinstance(act, dict):
            return "", ""
        stop = act.get("external_stop") or {}
        stop_state = str(stop.get("state") or "") if isinstance(stop, dict) else ""
        return str(act.get("status") or ""), stop_state

    def _queued_stop_request(self, activity_id: str) -> Optional[dict[str, Any]]:
        """A mailbox request that the owner has not accepted yet, or ``None``.

        The read face must not call this "stopped": it is a *queued* request waiting for the
        resident.  Only entries that name this Activity and still sit in the mailbox count.
        """

        import activity_external_stop as aes

        state_root = self._stop_state_root()
        if state_root is None:
            return None
        try:
            channel = aes.StopControlChannel(state_root)
            for payload in channel.list_requests():
                if str(payload.get("activity_id") or "") != str(activity_id or ""):
                    continue
                return {
                    "operation_key": payload.get("operation_key"),
                    "intent_id": payload.get("intent_id"),
                    "reason_code": payload.get("reason_code"),
                    "requested_at": payload.get("requested_at"),
                    "epoch": payload.get("epoch"),
                }
        except Exception:
            return None
        return None

    def activity_stop_state(self, activity_id: str) -> dict[str, Any]:
        """The stop/terminal state of one Activity -- no body, no token, no private text."""

        state, reason = self._life_canonical_state()
        if state is None:
            return unavailable(reason)
        act = (state.get("activities") or {}).get(str(activity_id))
        if not isinstance(act, dict):
            return rejected("ACTIVITY_NOT_FOUND", activity_id=str(activity_id))
        stop = dict(act.get("external_stop") or {})
        queued = None if stop else self._queued_stop_request(str(activity_id))
        return envelope(True, "OK", {
            # ``stop_state`` is the owner's word only.  ``QUEUED`` is a separate, weaker fact:
            # a request is on disk and no owner has accepted it (never read as PENDING).
            "stop_state": stop.get("state") or ("QUEUED" if queued else None),
            "queued_request": queued,
            "activity_id": act.get("activity_id"),
            "activity_status": act.get("status"),
            "activity_revision": act.get("revision"),
            "intent_id": stop.get("intent_id"),
            "reason_code": stop.get("reason_code"),
            "requested_at": stop.get("requested_at"),
            "accepted_at": stop.get("accepted_at"),
            "finalized_at": stop.get("finalized_at"),
            "boundary_mode": stop.get("boundary_mode"),
            "unresolved_action_refs": list(stop.get("unresolved_action_refs") or []),
            "terminal_reason_code": act.get("terminal_reason_code"),
            "checkpoint_ref": act.get("checkpoint_ref"),
            "last_action_ref": act.get("last_action_ref"),
            "read_only": True,
        })

    def request_activity_stop(self, *, activity_id: Any, expected_revision: Any,
                              operation_key: Any, reason_code: Any,
                              epoch: Optional[str],
                              stop_credential: Optional[Any] = None) -> dict[str, Any]:
        """One local-operator stop request.  Authentication already happened in the handler.

        With a live runtime in this process the owner answers directly.  Otherwise the request
        is written to the Life state root mailbox *signed with this serve's credential* and the
        answer is an explicit ``PENDING`` that names ``RUNTIME_MAILBOXED`` -- never a fabricated
        CANCELLED.  Without a credential nothing is written: the mailbox is not authority.
        """

        import activity_external_stop as aes

        if stop_credential is None:
            return unavailable("OPERATOR_STOP_UNAVAILABLE")
        state, reason = self._life_canonical_state()
        if state is None:
            return unavailable(reason)
        act = (state.get("activities") or {}).get(str(activity_id or ""))
        if not isinstance(act, dict):
            return rejected("ACTIVITY_NOT_FOUND", activity_id=str(activity_id or ""))
        subject_id = str(state.get("subject_id") or "")
        if not subject_id:
            return unavailable("CANONICAL_SUBJECT_MISSING")
        # The credential names the Life subject it may stop; a phone wired to a different
        # canonical state cannot stop here just because it holds a valid token.
        life_subject = str(getattr(stop_credential, "life_subject", "") or "")
        if not life_subject or life_subject != subject_id:
            return rejected("SUBJECT_MISMATCH")
        try:
            request = aes.build_request(
                activity_id=activity_id,
                expected_revision=expected_revision,
                operation_key=operation_key,
                reason_code=reason_code,
                subject_id=subject_id,
                epoch=epoch,
                boundary_mode="IMMEDIATE",
            )
        except aes.ExternalStopRefused as exc:
            return rejected(exc.reason_code)
        if self._activity_stop is not None:
            try:
                result = self._activity_stop.handle(request)
            except aes.ExternalStopRefused as exc:
                return rejected(exc.reason_code, detail=exc.detail)
            return envelope(result.get("status") not in {
                aes.STATUS_REFUSED, "REFUSED"}, str(result.get("status") or "UNKNOWN"),
                {**result, "mailboxed": False})
        state_root = self._stop_state_root()
        if state_root is None:
            return unavailable("LIFE_STATE_ROOT_UNBOUND")
        instance_root = aes.instance_root_for_state_root(state_root)
        if instance_root is None:
            return unavailable("INSTANCE_ROOT_UNRESOLVED")
        channel = aes.StopControlChannel(state_root)
        try:
            channel.submit(request, key=aes.mailbox_key(
                getattr(stop_credential, "token", "")), instance=instance_root)
        except aes.ExternalStopRefused as exc:
            return unavailable(exc.reason_code)
        # Whatever the resident wrote back is only a *report*.  The status this route returns
        # is re-derived from the canonical Activity, so a forged result file cannot make the
        # phone announce a CANCELLED the owner never committed.
        owner_status, owner_stop = self._owner_stop_truth(request.activity_id)
        settled = channel.read_result(request.operation_key)
        if isinstance(settled, dict) and (
                str(settled.get("operation_key") or "") not in ("", request.operation_key)
                or str(settled.get("intent_id") or "") not in ("", request.intent_id)):
            settled = None  # a result for a different operation is not this answer
        base = {
            "activity_id": request.activity_id,
            "intent_id": request.intent_id,
            "operation_key": request.operation_key,
            "mailboxed": True,
            "owner_activity_status": owner_status,
            "owner_stop_state": owner_stop,
            "result_report": settled,
        }
        if owner_status == "CANCELLED":
            return envelope(True, aes.STATUS_CANCELLED, {**base, "accepted_as_stop": True})
        if owner_stop == "PENDING":
            return envelope(True, aes.STATUS_PENDING, {
                **base, "accepted_as_stop": True,
                "reason_code": "STOP_INTENT_PENDING",
                "detail": ("the owner accepted the durable intent; it blocks further dispatch "
                           "and becomes CANCELLED at the next safe boundary")})
        if isinstance(settled, dict) and settled.get("status") == aes.STATUS_REFUSED:
            return envelope(False, aes.STATUS_REFUSED, {
                **base, "accepted_as_stop": False,
                "reason_code": str(settled.get("reason_code") or "STOP_REFUSED"),
                "detail": str(settled.get("detail") or "")[:200]})
        return envelope(True, aes.STATUS_PENDING, {
            **base,
            "accepted_as_stop": False,
            "reason_code": "RUNTIME_MAILBOXED",
            "detail": ("the request is durable on disk and not yet accepted by the owner; the "
                       "live resident applies it at its own boundary -- this is not a stop yet"),
        })

    # -- local visits / co-presence (A's ``LocalContactInbox``; P only delivers) -----------

    def _contact_state_root(self) -> Optional[Path]:
        """This instance's own Life state root, or ``None`` when it never had one.

        Two real sources, in this order: the environment the instance's own Life binding maps
        (``phone_life.map_life_environment`` -- recorded ``NYA02_RESIDENT_STATE`` first), then the
        very path ``one_day_life.prepare`` would have written for this root.  Nothing is created
        here: a root that does not exist stays honestly unbound.
        """

        # F-140: an explicitly bound Life state root (same-process composition, isolated demo)
        # wins over inference.  It is the same root the caller already gave for the stop route;
        # nothing is created if the directory does not exist.
        if self._life_state_root_override is not None:
            return (self._life_state_root_override
                    if self._life_state_root_override.is_dir() else None)
        from . import life as phone_life

        try:
            nested = phone_life.read_nested_life(self.root)
        except Exception:
            nested = None
        try:
            environment = phone_life.map_life_environment(self.instance, nested)
        except Exception:
            environment = dict(self.environment)
        recorded = str(environment.get("NYA02_RESIDENT_STATE") or "").strip()
        if recorded:
            return Path(recorded)
        try:
            return Path(phone_life.life_state_root(self.root, nested))
        except Exception:
            return None

    def _contact_service(self):
        """P's visit facade over **this** instance's state root, subject and home.

        The binding comes from the instance the ``PhoneService`` was constructed with -- never
        from a process-global environment variable and never from the request.  Nothing is
        created, migrated or started here: A's inbox is a bounded transport ledger, and the
        joint-time session record is B's own ``co_presence.json``, which P only reads.
        """

        from . import contact as phone_contact

        if self._contact is not None:
            return self._contact
        state_root = self._contact_state_root()
        if state_root is None:
            raise PhoneUnavailable("CONTACT_STATE_ROOT_UNBOUND")
        try:
            nested = None
            from . import life as phone_life
            nested = phone_life.read_nested_life(self.root)
            environment = phone_life.map_life_environment(self.instance, nested)
        except Exception:
            environment = dict(self.environment)
        home, home_source = phone_contact.home_ref_for(environment, self.instance)
        if not home:
            raise PhoneUnavailable("CONTACT_HOME_UNBOUND")
        self._contact = phone_contact.PhoneContact(
            state_root=state_root, subject=self.subject, home_ref=home,
            home_source=home_source,
            clock=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        return self._contact

    def _contact_call(self, method: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """One call on the visit facade; ``unavailable`` is the only thing P adds."""

        try:
            answer = getattr(self._contact_service(), method)(*args, **kwargs)
        except PhoneUnavailable as exc:
            return unavailable(exc.reason_code)
        except Exception as exc:  # owner refused to build / an unexpected shape
            return unavailable("CONTACT_UNAVAILABLE", error_type=type(exc).__name__)
        if not isinstance(answer, Mapping):
            return unavailable("CONTACT_UNAVAILABLE")
        return dict(answer)

    def contact(self) -> dict[str, Any]:
        """``GET /contact``: counts, bounded rows, the read-only joint-time session."""

        return self._contact_call("summary")

    def contact_events(self, limit: Optional[int] = None) -> dict[str, Any]:
        """``GET /contact/events``: bounded transport rows -- no body text."""

        return self._contact_call("listing", limit=limit)

    def contact_event(self, event_ref: str, *, include_body: bool = False) -> dict[str, Any]:
        """``GET /contact/events/<ref>``: one event; the text only on explicit ask.

        F-140: the answer also carries the participant **resolution** (``LINKED`` /
        ``UNRESOLVED`` / ``UNREADABLE``).  That is a read-only join over the instance's own
        participant registry; the inbox event itself is unchanged, and an arrival nobody linked
        stays honestly unresolved instead of being attributed to the instance subject.
        """

        answer = self._contact_call("event", event_ref, include_body=include_body)
        if isinstance(answer, dict) and answer.get("ok") is True \
                and isinstance(answer.get("data"), dict):
            answer["data"]["participant"] = self.participant_for_event(event_ref)
        return answer

    # -- participants (F-140: A's ``ParticipantRegistry``; identity only, read-only here) ---

    def _participant_registry(self):
        """A's registry over **this** instance's Life state root and subject.

        Nothing is created here: a fresh instance simply has no database yet, and every read
        says ``ledger_state=ABSENT`` instead of inventing people.  The phone never writes this
        owner -- creation / endpoint binding / event linking are local operator CLI acts.
        """

        if self._participants is not None:
            return self._participants
        state_root = self._contact_state_root()
        if state_root is None:
            raise PhoneUnavailable("PARTICIPANTS_STATE_ROOT_UNBOUND")
        from participant_registry import ParticipantRegistry

        self._participants = ParticipantRegistry(state_root, subject_id=self.subject)
        return self._participants

    def _participant_call(self, method: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """One call on the registry; ``unavailable`` is the only thing P adds."""

        try:
            answer = getattr(self._participant_registry(), method)(*args, **kwargs)
        except PhoneUnavailable as exc:
            return unavailable(exc.reason_code)
        except Exception as exc:  # a broken owner is never "nobody is known"
            return unavailable("PARTICIPANTS_UNAVAILABLE", error_type=type(exc).__name__)
        if not isinstance(answer, Mapping):
            return unavailable("PARTICIPANTS_UNAVAILABLE")
        return dict(answer)

    @staticmethod
    def _participant_envelope(answer: Mapping[str, Any]) -> dict[str, Any]:
        status = str(answer.get("status") or "")
        reason = str(answer.get("reason_code") or "")
        if answer.get("ok"):
            out = dict(answer)
            out.setdefault("reason_code", "")
            return out
        if status in ("absent", "not_found") or reason == "PARTICIPANT_NOT_FOUND":
            return envelope(False, STATUS_NOT_FOUND, dict(answer.get("data") or {}),
                            reason or "PARTICIPANT_NOT_FOUND")
        if status in ("rejected", "conflict"):
            return rejected(reason or "PARTICIPANTS_REQUEST_INVALID")
        return unavailable(reason or "PARTICIPANTS_UNAVAILABLE")

    def participants(self, limit: Optional[int] = None) -> dict[str, Any]:
        """``GET /participants``: the bounded identity list.  Pure read, no message body."""

        return self._participant_envelope(self._participant_call("list_participants",
                                                                 limit=limit))

    def participant(self, participant_id: str) -> dict[str, Any]:
        """``GET /participants/<id>``: one participant, its endpoints and linked event refs.

        The endpoint's raw ``account_key`` is operator-bound identity material, not something a
        phone reader (or her model-side tool) needs: it is redacted here, while the provider and
        the opaque endpoint id stay visible so the binding is still auditable.
        """

        answer = self._participant_envelope(self._participant_call("get_participant",
                                                                   participant_id))
        data = answer.get("data") if isinstance(answer.get("data"), Mapping) else None
        participant = data.get("participant") if isinstance(data, Mapping) else None
        if isinstance(participant, dict):
            endpoints = participant.get("endpoints")
            if isinstance(endpoints, list):
                for endpoint in endpoints:
                    if isinstance(endpoint, dict) and "account_key" in endpoint:
                        endpoint.pop("account_key", None)
                        endpoint["account_key_redacted"] = True
        return answer

    def participant_for_event(self, event_ref: str) -> dict[str, Any]:
        """Resolve one arrival's attribution.  Never raises; unknown stays ``UNREADABLE``."""

        answer = self._participant_call("resolve_event", event_ref)
        payload = answer.get("data") if isinstance(answer.get("data"), Mapping) else {}
        if not answer.get("ok"):
            return {
                "status": "UNREADABLE",
                "resolution": "UNREADABLE",
                "reason_code": str(answer.get("reason_code") or "PARTICIPANTS_UNAVAILABLE"),
                "participant": None,
                "endpoint": None,
                "link_source": "",
                "linked_at": "",
                "private_text_included": False,
                "note": "参与者账本读不出来：这条来话属于谁仍然未知，不是「没有关联」",
            }
        participant = payload.get("participant") if isinstance(
            payload.get("participant"), Mapping) else None
        endpoint = payload.get("endpoint") if isinstance(
            payload.get("endpoint"), Mapping) else None
        link = payload.get("link") if isinstance(payload.get("link"), Mapping) else {}
        resolution = str(payload.get("resolution") or answer.get("status") or "UNRESOLVED")
        return {
            "status": resolution,
            "resolution": resolution,
            "reason_code": "",
            "participant": ({
                "participant_id": str(participant.get("participant_id") or ""),
                "display_name": str(participant.get("display_name") or ""),
                "aliases": [str(item) for item in (participant.get("aliases") or [])],
            } if participant else None),
            "endpoint": ({
                "endpoint_id": str(endpoint.get("endpoint_id") or ""),
                "provider": str(endpoint.get("provider") or ""),
            } if endpoint else None),
            "link_source": str(link.get("link_source") or ""),
            "linked_at": str(link.get("linked_at") or ""),
            "ledger_state": str(payload.get("ledger_state") or ""),
            "private_text_included": False,
            "note": ("这条来话已由本机 operator 显式关联到一位参与者"
                     if resolution == "LINKED" else
                     "这条来话还没有被显式关联：它不属于任何人，不是本实例主体，也不按名字猜"),
        }

    def contact_operation(self, operation_key: str) -> dict[str, Any]:
        """``GET /contact/operations/<key>``: reconcile one original key.  Pure read."""

        return self._contact_call("operation", operation_key)

    def contact_submit(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """``POST /contact/events``: deliver one local arrival.  Never an acceptance."""

        return self._contact_call("submit", payload)

    # -- shared practice (A's ``shared_practice_service``; P only forwards user intent) ------

    def _shared_practice_owner_from_instance(self):
        """A's ``SharedPracticeService`` built from **this instance's own bindings**, actor=user.

        This is the production wiring (``serve`` / a real instance).  It uses A's own published
        factory and the env names ``private_project_service`` already speaks -- no guessed
        constructor, no second truth source, and no process-global env: the values come from the
        instance payload this ``PhoneService`` was constructed with, plus the instance's own
        supply socket (or the port an outer composition already opened over the same owner).
        A half-wired instance yields ``None`` here, and the surface then answers a named
        ``SHARED_PRACTICE_UNBOUND`` instead of inventing a practice store.
        """

        from private_project_service import (ENV_EXCLUDED_NAMES, ENV_GRANT_NAMES, ENV_SOCKET_NAMES,
                                             ENV_SUBJECT_NAMES, ENV_WORKSPACE_NAMES,
                                             default_excluded_artifact_ids)
        from shared_practice_service import CALLER_USER, shared_practice_service_from_env

        if not self.workspace_id or not self.artifact_grant_id or not self.subject:
            self._shared_practice_error = "SHARED_PRACTICE_BINDINGS_MISSING"
            return None
        # A rereads explicitly selected reading material for governance. Excluding it here
        # would redact every derived reply; only the private-items ledger is never a source.
        excluded = default_excluded_artifact_ids(self.private_items_artifact_id)
        env = {ENV_WORKSPACE_NAMES[0]: self.workspace_id,
               ENV_GRANT_NAMES[0]: self.artifact_grant_id,
               ENV_SUBJECT_NAMES[0]: self.subject,
               ENV_SOCKET_NAMES[0]: self.supply_socket}
        if excluded:
            env[ENV_EXCLUDED_NAMES[0]] = ",".join(excluded)
        try:
            port = self._port()
        except PhoneUnavailable:
            port = None
        try:
            owner = shared_practice_service_from_env(port=port, env=env, actor=CALLER_USER)
        except Exception as exc:  # a broken owner is named, never guessed around
            self._shared_practice_error = "SHARED_PRACTICE_BIND_FAILED:" + type(exc).__name__
            return None
        if owner is None:
            self._shared_practice_error = "SHARED_PRACTICE_OWNER_UNBOUND"
        return owner

    def _shared_practice_service(self):
        """P's shared-practice facade over **this** instance's own subject and workspace.

        The owner is either injected (tests) or built here by A's factory with ``actor="user"``
        (production).  The **transport** -- the instance's own ``LocalContactInbox`` -- is passed
        in as a callable too: the user's own words and the explicit end travel there, so a normal
        Life runner can process them; P never writes the user's contribution into A and never
        ends the practice on its own.
        """

        from . import practice_shared as phone_practice

        if self._shared_practice is not None:
            return self._shared_practice
        owner = self._shared_practice_owner
        if owner is None:
            owner = self._shared_practice_owner_from_instance()
        self._shared_practice = phone_practice.PhoneSharedPractice(
            subject=self.subject, workspace_id=self.workspace_id, service=owner,
            transport=self._contact_service, transport_error=self._shared_practice_error,
            clock=self._clock)
        return self._shared_practice

    def _shared_practice_call(self, method: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """One call on the shared-practice facade; ``unavailable`` is the only thing P adds."""

        try:
            answer = getattr(self._shared_practice_service(), method)(*args, **kwargs)
        except PhoneUnavailable as exc:
            return unavailable(exc.reason_code)
        except Exception as exc:  # a broken owner is never "nothing happened"
            return unavailable("SHARED_PRACTICE_UNAVAILABLE", error_type=type(exc).__name__)
        if not isinstance(answer, Mapping):
            return unavailable("SHARED_PRACTICE_UNAVAILABLE")
        return dict(answer)

    def shared_practices(self) -> dict[str, Any]:
        """``GET /shared-practice``: what exists now.  Pure read."""

        return self._shared_practice_call("summary")

    def shared_practice(self, practice_id: str) -> dict[str, Any]:
        """``GET /shared-practice/works/<id>``: material, rounds, contributions, versions."""

        return self._shared_practice_call("work", practice_id)

    def shared_practice_operation(self, operation_key: str) -> dict[str, Any]:
        """``GET /shared-practice/operations/<key>``: reconcile one original key.  Pure read."""

        return self._shared_practice_call("operation", operation_key)

    def shared_practice_open(self, *, source_artifact_id: str, source_version_id: str,
                            title: str, text: str, operation_key: str) -> dict[str, Any]:
        """``POST /shared-practice``: the user picks one explicitly shared material.

        ``text`` is the user's own first turn: A's ``open_practice`` needs an opening text and a
        title, so the phone asks for both instead of inventing them.  ``source_version_id`` is
        only the version the person actually saw -- P forwards it so the facade can report
        ``source_version_moved`` when the owner recorded a different one.
        """

        return self._shared_practice_call("open_practice", source_artifact_id=source_artifact_id,
                                   source_version_id=source_version_id, title=title, text=text,
                                   operation_key=operation_key)

    def shared_practice_act(self, *, practice_id: str, action: str, expected_head: str,
                            operation_key: str, text: Optional[str] = None,
                            reason: Optional[str] = None) -> dict[str, Any]:
        """``POST .../actions``: one **user** intent (submit / modify / pause / end)."""

        return self._shared_practice_call("act", practice_id=practice_id, action=action,
                                   expected_head=expected_head, operation_key=operation_key,
                                   text=text, reason=reason)
    # -- materials / research (B's research_workspace_service; P forwards, decides nothing) --

    def _research_service(self):
        """B's ``ResearchWorkspaceService`` over this instance's own project service.

        It is built fresh per call (no cached truth, no second store): the same port, the same
        grant, the same workspace as the project screen, plus the reading material and the
        private-items document named so they are never mistaken for a work.
        """
        from research_workspace_service import ResearchWorkspaceService

        if not self.workspace_id or not self.artifact_grant_id:
            raise PhoneUnavailable("RESEARCH_UNBOUND")
        return ResearchWorkspaceService(
            self._projects_service(),
            reading_artifact_id=self.reading_artifact_id,
            private_items_artifact_id=self.private_items_artifact_id,
        )

    def _research_call(self, method: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Forward one research-workspace call; its envelope (status/data) passes verbatim."""
        try:
            handler = getattr(self._research_service(), method)
            answer = handler(*args, **kwargs)
        except PhoneUnavailable as exc:
            return unavailable(exc.reason_code)
        except Exception as exc:
            name = type(exc).__name__
            if name in ("ResearchContractRefused", "ProjectContractRefused"):
                # A shape P's own pre-flight should have caught: refuse by name, never send it.
                return rejected("RESEARCH_REQUEST_INVALID", detail=str(exc)[:200])
            return unavailable("RESEARCH_UNAVAILABLE", error_type=name)
        if not isinstance(answer, dict):
            return unavailable("RESEARCH_UNAVAILABLE", error_type="NON_ENVELOPE")
        out = dict(answer)
        out["ok"] = bool(out.get("ok"))
        out["status"] = str(out.get("status") or STATUS_UNAVAILABLE)
        out["data"] = dict(out.get("data") or {})
        return out

    def materials(self, query: str = "", limit: Optional[int] = None) -> dict[str, Any]:
        """``GET`` the material library: list + search.  Pure read, no text in the rows."""
        kwargs: dict[str, Any] = {"query": query}
        if limit is not None:
            kwargs["limit"] = int(limit)
        return self._research_call("list_materials", **kwargs)

    def material(self, artifact_id: str,
                 expected_revision: Optional[Any] = None) -> dict[str, Any]:
        """``GET`` one material with its real text and the version it belongs to."""
        return self._research_call("read_material", artifact_id,
                                   expected_revision=expected_revision)

    def import_material(self, *, title: str, text: str, source_kind: str, origin: str,
                        operation_key: str) -> dict[str, Any]:
        """Import one explicitly supplied text as a new material artifact (never an overwrite)."""
        return self._research_call("import_material", title=title, text=text,
                                   source_kind=source_kind, origin=origin,
                                   operation_key=operation_key)

    def research_list(self, limit: Optional[int] = None) -> dict[str, Any]:
        """``GET`` the research list (bounded, current head only, no body)."""
        kwargs: dict[str, Any] = {}
        if limit is not None:
            kwargs["limit"] = int(limit)
        return self._research_call("list_research", **kwargs)

    def research_read(self, artifact_id: str) -> dict[str, Any]:
        """``GET`` one research: content, citations and each source's real version status."""
        return self._research_call("read_research", artifact_id)

    def research_create(self, *, question: str, source_refs: Any, title: Optional[str] = None,
                        scope: str = "", uncertainty: Optional[str] = None,
                        operation_key: str) -> dict[str, Any]:
        """Create one research from a question plus explicitly chosen material sources."""
        kwargs: dict[str, Any] = {"question": question, "source_refs": source_refs,
                                  "scope": scope, "operation_key": operation_key}
        if title is not None:
            kwargs["title"] = title
        if uncertainty is not None:
            kwargs["uncertainty"] = uncertainty
        return self._research_call("create_research", **kwargs)

    def research_update(self, *, artifact_id: str, content: str, expected_head: str,
                        operation_key: str, scope: Optional[str] = None,
                        uncertainty: Optional[str] = None) -> dict[str, Any]:
        """Continue one research under the caller's own CAS head; the marker is rebuilt."""
        kwargs: dict[str, Any] = {"artifact_id": artifact_id, "content": content,
                                  "expected_head": expected_head, "operation_key": operation_key}
        if scope is not None:
            kwargs["scope"] = scope
        if uncertainty is not None:
            kwargs["uncertainty"] = uncertainty
        return self._research_call("update_research", **kwargs)

    def research_close(self, *, artifact_id: str, expected_revision: int,
                       operation_key: str) -> dict[str, Any]:
        """Close one research with the owner's ``archive`` verb: nothing is deleted."""
        return self._research_call("close_research", artifact_id=artifact_id,
                                   expected_revision=int(expected_revision),
                                   operation_key=operation_key)

    # -- Memory 与个人实践（A 的 LifeMemoryService + B 的 personal_practice_service） -------

    def _effective_memory_env(self) -> dict[str, str]:
        """Return effective Memory environment, merging nested ``one-day/instance.json`` read-only when unbound."""
        from . import life as phone_life

        env: dict[str, str] = dict(self.environment)
        try:
            db_names = tuple(phone_life.memory_env_names().get("database") or ())
        except Exception:
            db_names = ()
        has_db = any(str(env.get(name) or "").strip() for name in db_names)
        if not has_db:
            try:
                nested = phone_life.read_nested_life(self.root)
                nested_env = nested.get("environment") if isinstance(nested, Mapping) else None
                if isinstance(nested_env, Mapping):
                    for k, v in nested_env.items():
                        if isinstance(k, str) and v is not None and not str(env.get(k) or "").strip():
                            env[k] = str(v)
            except Exception:
                pass
        return env

    def memory_owner(self) -> str:
        """Which Memory persona this instance reads: the instance/env config, never a client.

        The order is: the exact owner env name the shipped resident reads, then a field the
        instance file recorded, then ``phone.life.MEMORY_DEFAULT_OWNER``.  A request body can
        never reach this method -- the phone surface does not accept ``owner`` at all.
        """
        from . import life as phone_life

        eff_env = self._effective_memory_env()
        names: tuple[str, ...] = ()
        try:
            names = tuple(phone_life.memory_env_names().get("owner") or ())
        except Exception:
            names = ()
        for name in names:
            value = str(eff_env.get(name) or "").strip()
            if value:
                return value
        recorded = str(self.instance.get(MEMORY_OWNER_FIELD) or "").strip()
        if recorded:
            return recorded
        return str(getattr(phone_life, "MEMORY_DEFAULT_OWNER", "") or "chiyo")

    def _memory_service(self):
        """A's Memory service for this instance (possibly UNBOUND), or ``None`` if not installed.

        This never opens a database and never builds an ``EvidenceStore``: it hands the
        environment to A's own factory, which answers a service (bound or not) or nothing.
        """
        try:
            from life_memory_service import memory_service_from_env
        except Exception:
            return None
        try:
            return memory_service_from_env(self._effective_memory_env())
        except Exception:
            return None

    def memory(self) -> dict[str, Any]:
        """``GET``: the Memory binding status (never an initialisation, never a write)."""
        service = self._memory_service()
        if service is None:
            return {"ok": False, "status": STATUS_UNAVAILABLE,
                    "reason_code": "MEMORY_SERVICE_UNAVAILABLE",
                    "data": {"installed": False, "binding_status": "UNBOUND",
                             "recall_ready": False, "owner": self.memory_owner(),
                             "enable_available": True}}
        status_fn = getattr(service, "status", None)
        if not callable(status_fn):
            return unavailable("MEMORY_STATUS_UNAVAILABLE",
                               installed=True, owner=self.memory_owner())
        try:
            answer = status_fn()
        except Exception as exc:
            return unavailable("MEMORY_STATUS_FAILED", error_type=type(exc).__name__)
        if not isinstance(answer, Mapping):
            return unavailable("MEMORY_STATUS_INVALID")
        data = dict(answer.get("data") or {})
        # A's own row carries ``binding_status`` / ``recall_ready`` / ``owner``; a missing owner is
        # filled with *this instance's* configured one, never with a client-supplied string.
        data["owner"] = data.get("owner") or self.memory_owner()
        data.setdefault("binding_status", str(answer.get("status") or ""))
        data["installed"] = True
        data["enable_available"] = True
        data["binding_status"] = data.get("binding_status") or "UNBOUND"
        if answer.get("ok"):
            return {"ok": True, "status": "ok", "data": data}
        reason = str(answer.get("reason") or answer.get("reason_code") or "")
        detail = answer.get("detail")
        if isinstance(detail, str) and detail:
            data["detail"] = detail[:200]
        return {"ok": False, "status": STATUS_UNAVAILABLE,
                "reason_code": (reason or "MEMORY_UNAVAILABLE").upper()[:64],
                "data": data}

    def memory_experiences(self, *, query: str = "",
                           limit: Optional[int] = None) -> dict[str, Any]:
        """``GET``: real experiences A can still recall, as a bounded, related search."""
        service = self._memory_service()
        if service is None:
            return unavailable("MEMORY_SERVICE_UNAVAILABLE")
        lister = getattr(service, "list_experiences", None)
        if not callable(lister):
            return unavailable("MEMORY_LIST_UNAVAILABLE")
        cap = MAX_MEMORY_EXPERIENCES if limit is None else max(1, min(int(limit),
                                                                   MAX_MEMORY_EXPERIENCES))
        text = query if isinstance(query, str) else ""
        try:
            answer = lister(query=text.strip(), limit=cap)
        except Exception as exc:
            return unavailable("MEMORY_QUERY_FAILED", error_type=type(exc).__name__)
        if not isinstance(answer, Mapping) or not answer.get("ok"):
            return _memory_failure(answer, "MEMORY_EXPERIENCES_UNAVAILABLE")
        data = dict(answer.get("data") or {})
        rows = [dict(row) for row in (data.get("experiences") or []) if isinstance(row, Mapping)]
        return {"ok": True, "status": "ok",
                "data": {"experiences": rows[:cap], "count": len(rows[:cap]),
                         "query": text.strip(), "limit": cap,
                         "binding_status": str(data.get("binding_status") or ""),
                         "owner": data.get("owner") or self.memory_owner(),
                         "note": "经历只来自已确认的结果；这里是她能召回的那部分。"}}

    def memory_experience(self, experience_id: str) -> dict[str, Any]:
        """``GET`` one experience: A's own governance decides whether it is still recallable."""
        service = self._memory_service()
        if service is None:
            return unavailable("MEMORY_SERVICE_UNAVAILABLE")
        reader = getattr(service, "read_experience", None)
        if not callable(reader):
            return unavailable("MEMORY_READ_UNAVAILABLE")
        try:
            answer = reader(experience_id)
        except Exception as exc:
            return unavailable("MEMORY_READ_FAILED", error_type=type(exc).__name__)
        if not isinstance(answer, Mapping) or not answer.get("ok"):
            return _memory_failure(answer, "MEMORY_EXPERIENCE_UNAVAILABLE",
                                   experience_id=experience_id)
        data = dict(answer.get("data") or {})
        data["owner"] = data.get("owner") or self.memory_owner()
        return {"ok": True, "status": "ok", "data": data}

    def enable_memory(self, *, operation_key: str) -> dict[str, Any]:
        """``POST``: explicitly enable this instance's own Memory (A's owner initialises it).

        Only an explicit POST reaches this: a GET/preflight never creates a store.  The owner is
        this instance's configured one -- the request cannot name an owner, a database or a path.
        """
        import inspect
        from . import life as phone_life

        enabler = getattr(phone_life, "enable_instance_memory", None)
        if not callable(enabler):
            return unavailable("MEMORY_ENABLE_UNAVAILABLE",
                               detail="这一版还没有显式启用入口（A 的 enable_instance_memory）。")
        call_kwargs: dict[str, Any] = {
            "owner": self.memory_owner(),
            "operation_key": operation_key,
        }
        try:
            eff_env = self._effective_memory_env()
            env_names = phone_life.memory_env_names()
            bound_keys = tuple(env_names.get("database") or ()) + tuple(env_names.get("authority") or ())
            is_bound = any(str(eff_env.get(k) or "").strip() for k in bound_keys)
            if not is_bound and "authority_path" in inspect.signature(enabler).parameters:
                resolved_auth: Optional[Path] = None
                managed_file = self.root / "nyairo-day-managed.json"
                try:
                    if not managed_file.is_symlink() and managed_file.is_file():
                        mdoc = json.loads(managed_file.read_text(encoding="utf-8"))
                        raw_ma = str((mdoc or {}).get("memory_authority") or "").strip() if isinstance(mdoc, Mapping) else ""
                        if raw_ma:
                            resolved_auth = Path(raw_ma).expanduser()
                except Exception:
                    resolved_auth = None
                if resolved_auth is None:
                    resolved_auth = self.root.parent / f"{self.root.name}-memory-authority.sqlite"
                call_kwargs["authority_path"] = resolved_auth
        except Exception:
            pass
        try:
            answer = enabler(self.root, **call_kwargs)
        except Exception as exc:
            return unavailable("MEMORY_ENABLE_FAILED", error_type=type(exc).__name__)
        if not isinstance(answer, Mapping):
            return unavailable("MEMORY_ENABLE_INVALID")
        # A's ``enable_instance_memory`` answers a *report*, not an envelope: it carries the
        # binding, the backup it wrote and ``restart_required``.  Everything else is reported
        # verbatim; nothing is restarted here and no second call is made.
        nested = answer.get("data") if isinstance(answer.get("data"), Mapping) else None
        data = dict(nested) if nested is not None else {
            key: value for key, value in dict(answer).items()
            if key not in ("ok", "status", "reason", "detail", "exit_code")}
        data["restart_required"] = bool(data.get("restart_required"))
        data["service_started"] = bool(data.get("service_started"))
        data["processes_started"] = int(data.get("processes_started") or 0)
        data["owner"] = answer.get("owner") or self.memory_owner()
        data["enable_status"] = str(answer.get("status") or "")
        reason = str(answer.get("reason") or answer.get("reason_code") or "")
        detail = answer.get("detail")
        if isinstance(detail, str) and detail:
            data["detail"] = detail[:200]
        if answer.get("ok"):
            return {"ok": True, "status": "ok", "data": data}
        status = STATUS_UNAVAILABLE if str(answer.get("status") or "") in ("UNAVAILABLE",
                                                                          "UNKNOWN") \
            else STATUS_REJECTED
        return {"ok": False, "status": status,
                "reason_code": (reason or "MEMORY_ENABLE_REFUSED").upper()[:64],
                "data": data}

    # -- 个人实践：回顾 / 做法（B 的 personal_practice_service；P 只转发） --------------------

    def _practice_service(self):
        """B's ``PersonalPracticeService`` over this instance's owner and Memory.

        The Memory service is rebuilt per call (no cached truth): a review's evidence is checked
        against what A can read *now*, not against what it could read when the screen opened.
        """
        from personal_practice_service import PersonalPracticeService

        if not self.workspace_id or not self.artifact_grant_id:
            raise PhoneUnavailable("PRACTICE_UNBOUND")
        return PersonalPracticeService(self._projects_service(),
                                       memory_service=self._memory_service())

    def _practice_call(self, method: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Forward one practice-workspace call; its envelope (status/data) passes verbatim."""
        try:
            handler = getattr(self._practice_service(), method, None)
            if not callable(handler):
                return unavailable("PRACTICE_METHOD_MISSING", method=method)
            answer = handler(*args, **kwargs)
        except PhoneUnavailable as exc:
            return unavailable(exc.reason_code)
        except Exception as exc:
            return unavailable("PRACTICE_UNAVAILABLE", error_type=type(exc).__name__)
        if not isinstance(answer, Mapping):
            return unavailable("PRACTICE_ANSWER_INVALID", method=method)
        return dict(answer)

    def practice(self) -> dict[str, Any]:
        """``GET``: the workbench's two lists plus the Memory status, in one guarded read."""
        reviews = self._practice_call("list_reviews", limit=PRACTICE_LIST_LIMIT)
        methods = self._practice_call("list_methods", limit=PRACTICE_LIST_LIMIT)
        memory = self.memory()
        review_data = dict(reviews.get("data") or {})
        method_data = dict(methods.get("data") or {})
        ok = bool(reviews.get("ok")) and bool(methods.get("ok"))
        data: dict[str, Any] = {
            "reviews": list(review_data.get("projects") or []),
            "methods": list(method_data.get("projects") or []),
            "counts": {"reviews": len(review_data.get("projects") or []),
                       "methods": len(method_data.get("projects") or [])},
            "review_status": str(reviews.get("status") or ""),
            "method_status": str(methods.get("status") or ""),
            "review_truncated": bool(review_data.get("truncated")),
            "method_truncated": bool(method_data.get("truncated")),
            "memory": dict(memory.get("data") or {}),
            "memory_status": str(memory.get("status") or ""),
            "memory_bound": bool((memory.get("data") or {}).get("binding_status")
                                 and (memory.get("data") or {}).get("binding_status") != "UNBOUND"),
            "method_states": list(PRACTICE_METHOD_STATES),
            "trial_outcomes": list(PRACTICE_TRIAL_OUTCOMES),
            "not_verified_skill": True,
            "not_memory": True,
        }
        if ok:
            return {"ok": True, "status": "ok", "data": data}
        reason = str(reviews.get("reason_code") or methods.get("reason_code")
                     or "PRACTICE_UNAVAILABLE")
        data["review_reason_code"] = str(reviews.get("reason_code") or "")
        data["method_reason_code"] = str(methods.get("reason_code") or "")
        return {"ok": False, "status": STATUS_UNAVAILABLE, "reason_code": reason[:64],
                "data": data}

    def practice_review(self, artifact_id: str) -> dict[str, Any]:
        """``GET`` one review: body, head, and every cited experience's current readability."""
        return self._practice_call("read_review", artifact_id)

    def create_practice_review(self, *, question: str, title: str, experience_ids: list[str],
                               operation_key: str) -> dict[str, Any]:
        """``POST`` one review.  Every cited experience is read through A before anything lands."""
        return self._practice_call("create_review", question=question, title=title,
                                   experience_ids=experience_ids, operation_key=operation_key)

    def update_practice_document(self, *, artifact_id: str, content: str, expected_head: str,
                                 operation_key: str) -> dict[str, Any]:
        """``POST`` the next body of one review/method under the caller's CAS head."""
        return self._practice_call("update_document", artifact_id, content=content,
                                   expected_head=expected_head, operation_key=operation_key)

    def practice_methods(self, *, query: str = "",
                         limit: Optional[int] = None) -> dict[str, Any]:
        """``GET`` the method drafts (no bodies)."""
        cap = PRACTICE_LIST_LIMIT if limit is None else max(1, int(limit))
        return self._practice_call("list_methods", query=query, limit=cap)

    def practice_method(self, artifact_id: str) -> dict[str, Any]:
        """``GET`` one method: instruction, scope, unknown, state and all evidence checks."""
        return self._practice_call("read_method", artifact_id)

    def create_practice_method(self, *, title: str, instruction: str, scope: str,
                               uncertainty: str, experience_ids: list[str],
                               review_refs: list[str], operation_key: str) -> dict[str, Any]:
        """``POST`` one TENTATIVE method draft (never a verified Skill, never a persona trait)."""
        return self._practice_call("create_method", title=title, instruction=instruction,
                                   scope=scope, uncertainty=uncertainty,
                                   experience_ids=experience_ids, review_refs=review_refs,
                                   operation_key=operation_key)

    def record_practice_trial(self, *, artifact_id: str, outcome: str, detail: str,
                              experience_ids: list[str], expected_head: str,
                              operation_key: str) -> dict[str, Any]:
        """``POST`` one trial evaluation: HELPED/NOT_HELPED need readable evidence, else UNKNOWN."""
        return self._practice_call("record_trial", artifact_id, outcome=outcome, detail=detail,
                                   experience_ids=experience_ids,
                                   expected_head=expected_head, operation_key=operation_key)

    def revise_practice_method(self, *, artifact_id: str, instruction: str, scope: str,
                               uncertainty: str, expected_head: str,
                               operation_key: str) -> dict[str, Any]:
        """``POST`` a revision: the old version stays, the state goes back to TENTATIVE."""
        return self._practice_call("revise_method", artifact_id, instruction=instruction,
                                   scope=scope, uncertainty=uncertainty,
                                   expected_head=expected_head, operation_key=operation_key)

    def retire_practice_method(self, *, artifact_id: str, expected_head: str,
                               operation_key: str) -> dict[str, Any]:
        """``POST`` a retirement: logical RETIRED, the document and its counterexamples stay."""
        return self._practice_call("retire_method", artifact_id, expected_head=expected_head,
                                   operation_key=operation_key)


    # -- /state --------------------------------------------------------------------------

    def state(self) -> dict[str, Any]:
        apps: dict[str, Any] = {}

        def summarise(name: str, result: Mapping[str, Any]) -> dict[str, Any]:
            status = str(result.get("status") or "")
            if result.get("ok"):
                entry: dict[str, Any] = {"status": "available"}
            elif status == STATUS_DISABLED:
                entry = {"status": "disabled"}
            else:
                entry = {"status": "unavailable"}
            if result.get("reason_code"):
                entry["reason_code"] = str(result["reason_code"])
            data: dict[str, Any] = {}
            payload = result.get("data") if isinstance(result.get("data"), Mapping) else {}
            items = result.get("items")
            if isinstance(items, list):
                data["item_count"] = len(items)
            for key in ("currency", "balance", "purchase_available"):
                if key in payload:
                    data[key] = payload[key]
            catalog = payload.get("catalog")
            if isinstance(catalog, list):
                data["catalog_count"] = len(catalog)
            for key in ("store_id", "channel"):
                if isinstance(payload.get(key), str):
                    data[key] = payload[key]
            availability = payload.get("availability")
            if isinstance(availability, Mapping):
                # C's own opening state, verbatim: "OPEN 10:00-20:00" and "closed" are both the
                # owner's words, and the phone never turns a missing one into "open".
                data["availability"] = dict(availability)
            data.setdefault("app", name)
            entry["data"] = data
            return entry

        supply_up = Path(self.supply_socket).exists()
        items_up = bool(self.private_items_artifact_id) and supply_up
        notes = self.notes() if items_up else unavailable("SUPPLY_UNAVAILABLE")
        references = self.references() if items_up else unavailable("SUPPLY_UNAVAILABLE")
        reading = self.reading() if items_up else unavailable("SUPPLY_UNAVAILABLE")
        apps["notes"] = summarise("notes", notes)
        # 日记在普通状态里只报数量/日期：正文不在这一层，也不做摘要或派生标题。
        diary = self.diary_list() if items_up else unavailable("SUPPLY_UNAVAILABLE")
        diary_entry = summarise("diary", diary)
        if diary.get("ok"):
            diary_entry["data"]["item_count"] = int(diary.get("count") or 0)
            diary_entry["data"]["latest_at"] = diary.get("latest_at")
            diary_entry["data"]["dates"] = list(diary.get("dates") or [])
            diary_entry["data"]["private_text_included"] = False
        apps["diary"] = diary_entry
        apps["references"] = summarise("references", references)
        apps["reading"] = summarise("reading", reading)
        apps["wallet"] = summarise("wallet", self.wallet())
        apps["shop"] = summarise("shop", self.shop())
        apps["store"] = summarise("store", self.store())
        apps["inventory"] = summarise("inventory", self.inventory())
        # One facade call feeds both the world app and the upgraded body app: the body block
        # is C's own projection, and /state never computes a second one.
        world_view = self.world()
        world_entry = summarise("world", world_view)
        world_entry["data"].update(self.world_summary(world_view))
        world_entry["data"].setdefault("app", "world")
        apps["world"] = world_entry
        apps["body"] = self._body_entry(self._body_from_world(world_view))
        # Projects are read last: one owner list call, and a missing socket answers its own
        # "unavailable" instead of an empty list (no projects is a real answer, a dead owner is not).
        if supply_up and self.workspace_id and self.artifact_grant_id:
            apps["projects"] = summarise("projects", self.projects())
        else:
            apps["projects"] = summarise("projects", unavailable("SUPPLY_UNAVAILABLE"))
        # Shares are the last summary: one owner read, **no text** (the list never carries it), and
        # an unbound/dead owner keeps its own honest status instead of an empty inbox.
        if supply_up and self.workspace_id and self.artifact_grant_id:
            share_view = self.shares()
        else:
            share_view = unavailable("SUPPLY_UNAVAILABLE")
        share_entry = summarise("shares", share_view)
        share_payload = (share_view.get("data")
                         if isinstance(share_view.get("data"), Mapping) else {})
        for key in ("counts", "inbox", "withheld_count", "local_only", "external_send", "note"):
            if key in share_payload:
                share_entry["data"][key] = share_payload[key]
        apps["shares"] = share_entry
        # Local visits are the last summary: A's own counts + the bounded transport rows + B's
        # read-only session record.  No body text, and an unbound state root keeps its own
        # honest "unavailable" instead of pretending an empty inbox.
        contact_view = self.contact()
        contact_entry = summarise("contact", contact_view)
        contact_payload = (contact_view.get("data")
                           if isinstance(contact_view.get("data"), Mapping) else {})
        for key in ("counts", "by_kind", "event_count", "capacity", "ledger_state",
                    "session", "home_bound", "home_source", "local_only", "note"):
            if key in contact_payload:
                contact_entry["data"][key] = contact_payload[key]
        events = contact_payload.get("events")
        if isinstance(events, list):
            contact_entry["data"]["event_count"] = contact_payload.get("event_count") \
                if isinstance(contact_payload.get("event_count"), int) else len(events)
        apps["contact"] = contact_entry
        # Shared practice is the last summary: one pure read of A's owner.  Unbound (A has not
        # published the interface into this composition) keeps its own honest status instead of
        # an empty practice list, and the entry always says it never writes a subject side.
        practice_view = self.shared_practices()
        practice_entry = summarise("shared_practice", practice_view)
        practice_payload = (practice_view.get("data")
                            if isinstance(practice_view.get("data"), Mapping) else {})
        for key in ("count", "truncated", "private_text_included", "local_only",
                    "delivered_to_user", "note"):
            if key in practice_payload:
                practice_entry["data"][key] = practice_payload[key]
        service_block = practice_payload.get("service")
        if isinstance(service_block, Mapping):
            practice_entry["data"]["service_bound"] = bool(service_block.get("bound"))
            practice_entry["data"]["owner"] = str(service_block.get("owner") or "")
            practice_entry["data"]["actions"] = list(service_block.get("actions") or [])
        apps["shared_practice"] = practice_entry
        return {
            "ok": True,
            "read_only": self.read_only(),
            "instance": {
                "name": str(self.instance.get("name") or self.root.name),
                "subject": self.subject,
                "mode": str(self.instance.get("mode") or "local_owner"),
                "fingerprint": profile_fingerprint(self.profile_home),
            },
            "apps": apps,
        }


def _project_failure(answer: Mapping[str, Any], default_reason: str) -> dict[str, Any]:
    """Map one *read* refusal from the project owner onto the phone envelope.

    The owner's own reason code is kept; only the status vocabulary is translated, because a GET
    has no "applied/rejected" of its own.  ``not_found`` stays 404, everything else 503.
    """
    status = str(answer.get("status") or "")
    reason = str(answer.get("reason_code") or "") or default_reason
    data: dict[str, Any] = {}
    if answer.get("artifact_id"):
        data["artifact_id"] = str(answer["artifact_id"])
    if answer.get("detail"):
        data["detail"] = str(answer["detail"])[:200]
    if status == STATUS_NOT_FOUND:
        return {"ok": False, "status": STATUS_NOT_FOUND, "reason_code": reason.upper()[:64],
                "data": data}
    return {"ok": False, "status": STATUS_UNAVAILABLE, "reason_code": reason.upper()[:64],
            "data": data}


def _album_failure(answer: Mapping[str, Any], default_reason: str) -> dict[str, Any]:
    """Map one owner album *read* refusal onto the phone envelope, keeping its reason."""
    status = str(answer.get("status") or "")
    reason = str(answer.get("reason") or "") or default_reason
    data: dict[str, Any] = {}
    if answer.get("detail"):
        data["detail"] = str(answer["detail"])[:200]
    if status == "NOT_FOUND":
        return {"ok": False, "status": STATUS_NOT_FOUND,
                "reason_code": reason.upper()[:64], "data": data}
    if status == "UNKNOWN":
        return {"ok": False, "status": STATUS_UNKNOWN,
                "reason_code": reason.upper()[:64], "data": data}
    if status in ("DENY", "BLOCKED"):
        return rejected(reason.upper()[:64], **data)
    return unavailable(reason.upper()[:64], **data)


def _album_write_answer(answer: Mapping[str, Any], default_reason: str) -> dict[str, Any]:
    """Map one owner album *import* answer onto the phone envelope."""
    status = str(answer.get("status") or "")
    reason = str(answer.get("reason") or "") or default_reason
    data: dict[str, Any] = {}
    for key in ("artifact_id", "version_id", "version_number", "caption", "content_type",
                "media", "operation_key", "lifecycle"):
        if answer.get(key) is not None:
            data[key] = answer[key]
    if answer.get("detail"):
        data["detail"] = str(answer["detail"])[:200]
    if status == "OK":
        return envelope(True, STATUS_OK, data)
    if status == "NO_CHANGE":
        return envelope(True, STATUS_ALREADY, data)
    if status == "CONFLICT":
        return {"ok": False, "status": "conflict",
                "reason_code": reason.upper()[:64], "data": data}
    if status == "NOT_FOUND":
        return {"ok": False, "status": STATUS_NOT_FOUND,
                "reason_code": reason.upper()[:64], "data": data}
    if status == "UNKNOWN":
        return {"ok": False, "status": STATUS_UNKNOWN,
                "reason_code": reason.upper()[:64], "data": data}
    return rejected(reason.upper()[:64], **data)


def _project_answer(answer: Mapping[str, Any], default_reason: str) -> dict[str, Any]:
    """Map one project *write* answer onto the phone envelope, keeping the owner's reason."""
    status = str(answer.get("status") or "")
    reason = str(answer.get("reason_code") or "") or default_reason
    data: dict[str, Any] = {}
    for key in ("artifact_id", "version_id", "version_number", "head_version_id",
                "parent_version_id", "operation_key", "effect_state", "owner_status",
                "lifecycle", "revision"):
        if answer.get(key) is not None:
            data[key] = answer[key]
    if answer.get("detail"):
        data["detail"] = str(answer["detail"])[:200]
    if answer.get("reconciled"):
        data["reconciled"] = True
    if answer.get("retryable") is not None:
        data["retryable"] = bool(answer["retryable"])
    if status == "applied":
        return envelope(True, STATUS_OK, data)
    if status == "already_applied":
        return envelope(True, STATUS_ALREADY, data)
    if status == "conflict":
        return rejected(reason.upper()[:64], **data)
    if status == "not_found":
        return {"ok": False, "status": STATUS_NOT_FOUND, "reason_code": reason.upper()[:64],
                "data": data}
    if status == "unknown":
        return {"ok": False, "status": STATUS_UNKNOWN, "reason_code": reason.upper()[:64],
                "data": data}
    if status == "rejected":
        return rejected(reason.upper()[:64], **data)
    return {"ok": False, "status": STATUS_UNAVAILABLE, "reason_code": reason.upper()[:64],
            "data": data}


def _supply_failure(exc: Exception) -> dict[str, Any]:
    """Map a Supply adapter refusal onto the one envelope, keeping the owner's own reason."""

    lookups = (
        ("PrivateItemsConflict", "NOTE_HEAD_CONFLICT", 409, STATUS_REJECTED),
        ("PrivateItemsOwnerRefused", None, 409, STATUS_REJECTED),
        ("PrivateItemsContractRefused", None, 400, STATUS_REJECTED),
        ("PrivateItemsVersionMismatch", "ARTIFACT_VERSION_MISMATCH", 409, STATUS_REJECTED),
        ("PrivateItemsUnresolved", "SUPPLY_RESULT_UNKNOWN", 202, STATUS_UNKNOWN),
        ("PrivateItemsSourceUnavailable", "SOURCE_UNAVAILABLE", 409, STATUS_REJECTED),
        ("LifeArtifactUnavailable", "SUPPLY_UNAVAILABLE", 503, STATUS_UNAVAILABLE),
        ("ArtifactOwnerRefused", None, 503, STATUS_UNAVAILABLE),
    )
    name = type(exc).__name__
    for candidate, reason, _code, status in lookups:
        if name == candidate:
            owner_reason = getattr(exc, "reason", "") or ""
            detail = getattr(exc, "detail", "") or str(exc)
            code = reason or (str(owner_reason).split(":")[-1] or "SUPPLY_REFUSED")
            return {"ok": False, "status": status, "reason_code": code.upper()[:64],
                    "data": {"detail": str(detail)[:200]}}
    return {"ok": False, "status": STATUS_UNAVAILABLE,
            "reason_code": "SUPPLY_UNAVAILABLE", "data": {"error_type": name}}


def _memory_failure(answer: Any, default_reason: str, **extra: Any) -> dict[str, Any]:
    """Map A's Memory refusal onto the phone envelope.  A's own reason code is kept verbatim."""
    data: dict[str, Any] = {}
    if isinstance(answer, Mapping):
        payload = answer.get("data")
        if isinstance(payload, Mapping):
            data.update({key: value for key, value in dict(payload).items()
                         if key != "experiences"})
        detail = answer.get("detail")
        if isinstance(detail, str) and detail:
            data["detail"] = detail[:200]
        reason = str(answer.get("reason") or answer.get("reason_code")
                     or answer.get("status") or "")
    else:
        reason = ""
    data.update(extra)
    reason_code = (reason or default_reason).upper()[:64]
    return {"ok": False, "status": STATUS_UNAVAILABLE, "reason_code": reason_code,
            "data": data}

__all__ = [
    "ACTORS",
    "ACTOR_MODEL",
    "ACTOR_USER",
    "APPS",
    "BODY_VIEW_FIELDS",
    "FIXTURE_STATES",
    "INSTANCE_FILENAME",
    "KIND_INSTANCE",
    "LIGHT_BLOCK_NAME",
    "MATERIAL_SOURCE_KINDS",
    "MAX_BODY",
    "MAX_DIARY_BODY",
    "MAX_LABEL",
    "MAX_MATERIAL_LIMIT",
    "MAX_MATERIAL_ORIGIN",
    "MAX_MATERIAL_QUERY",
    "MAX_MATERIAL_TEXT",
    "MAX_MATERIAL_TITLE",
    "MAX_MEMORY_EXPERIENCES",
    "MAX_MEMORY_QUERY",
    "MAX_PRACTICE_CONTENT",
    "MAX_PRACTICE_INSTRUCTION",
    "MAX_PRACTICE_QUESTION",
    "MAX_PRACTICE_REFS",
    "MAX_PRACTICE_SCOPE",
    "MAX_PRACTICE_TITLE",
    "MAX_PRACTICE_TRIAL_DETAIL",
    "MAX_PRACTICE_UNCERTAINTY",
    "MEMORY_OWNER_FIELD",
    "PRACTICE_LIST_LIMIT",
    "PRACTICE_METHOD_STATES",
    "PRACTICE_TRIAL_OUTCOMES",
    "MAX_RESEARCH_CONTENT",
    "MAX_RESEARCH_LIMIT",
    "MAX_RESEARCH_QUESTION",
    "MAX_RESEARCH_SCOPE",
    "MAX_RESEARCH_SOURCES",
    "MAX_RESEARCH_UNCERTAINTY",
    "MAX_PROJECT_TYPE_SCAN",
    "MAX_LABEL",
    "MAX_OPERATION_KEY",
    "MAX_PROJECT_CONTENT",
    "MAX_PROJECT_ID",
    "MAX_PROJECT_HISTORY_LIMIT",
    "MAX_SOURCE_NOTE",
    "PROJECT_SOURCE_PURPOSES",
    "MAX_PROJECT_TITLE",
    "MAX_READING_ANNOTATION_BYTES",
    "MAX_READING_CONTENT_BYTES",
    "MAX_READING_PAGE",
    "MAX_READING_TITLE_BYTES",
    "MAX_SOURCE_ID",
    "MAX_SOURCE_REVISION",
    "MAX_TITLE",
    "MAX_VERSION_ID",
    "PhoneService",
    "PhoneServiceError",
    "PhoneUnavailable",
    "SCHEMA_INSTANCE",
    "ROOM_BLOCK_NAMES",
    "WORLD_ACTIONS",
    "WORLD_SNAPSHOT_BLOCKS",
    "block_count",
    "envelope",
    "gateway_ready",
    "instance_path",
    "load_instance",
    "memo_reading_anchor",
    "profile_fingerprint",
    "rejected",
    "supply_ready",
    "unavailable",
    "wait_for_socket",
    "write_instance",
]

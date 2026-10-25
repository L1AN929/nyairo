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
                            世界动词 ``ARRANGE`` / ``TOGGLE_FIXTURE``）；P 只透传
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
import socket
import sys
import time
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

APPS: tuple[str, ...] = ("notes", "references", "reading", "wallet", "shop", "store",
                         "inventory", "body", "world", "projects", "shares", "contact",
                         "shared_practice")

#: The World actions a local owner may ask for.  The first five are the frozen v0.4 set
#: (``docs/engineering/v04-world-body-contract.md`` §3.1); ``ARRANGE`` and ``TOGGLE_FIXTURE`` are
#: this round's room verbs, exactly as C's ``PhoneWorld.VERBS`` / ``PARAM_SPEC`` name them.
#: P never renames an action: the owner's own ``actions.verbs``, its parameter table and its
#: reason codes stay the authority, and P translates none of them.
WORLD_ACTIONS: tuple[str, ...] = ("MOVE", "PICK_UP", "PLACE", "POSE", "REST",
                                  "ARRANGE", "TOGGLE_FIXTURE")

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
    "signals_source", "bands_only", "raw_values_exposed", "rest_evidence", "note",
)

STATUS_OK = "applied"
STATUS_ALREADY = "already_applied"
STATUS_REJECTED = "rejected"
STATUS_UNKNOWN = "unknown"
STATUS_UNAVAILABLE = "unavailable"
STATUS_DISABLED = "disabled"
STATUS_NOT_FOUND = "not_found"

MAX_OPERATION_KEY = 128
MAX_TITLE = 80
MAX_BODY = 4000
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
        shopping: Optional[Any] = None,
        world: Optional[Any] = None,
        #: Injected reading workbench (B's ``ReadingWorkspaceService``); ``None`` = build it here
        #: from this instance's own port + reading artifact id.
        reading: Optional[Any] = None,
        sharing: Optional[Any] = None,
        contact: Optional[Any] = None,
        #: Injected shared-practice owner (A's ``shared_practice_service``).  ``None`` = build it
        #: here from this instance's own bindings (A's factory, ``actor="user"``).
        shared_practice: Optional[Any] = None,
        #: The artifact port this instance already speaks (tests / an outer composition that
        #: opened Supply once).  ``None`` = build it from the instance's own supply socket.
        #: This is a **port**, never a substitute service: A's shared-practice owner is still
        #: built here, by A's own factory, with ``actor="user"``.
        project_port: Optional[Any] = None,
    ) -> None:
        self.instance = dict(instance)
        self.root = Path(str(self.instance["root"]))
        self.subject = str(self.instance["subject"])
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
        self._shopping = shopping
        self._world = world
        self._reading = reading
        #: Injected share facade (tests / an outer composition).  ``None`` = build it here from
        #: this instance's own port, subject, workspace and grant (never from process-wide env).
        self._sharing = sharing
        #: Injected visit facade (tests / an outer composition).  ``None`` = build it here from
        #: this instance's own Life state root, subject and home (never from a client value).
        self._contact = contact
        #: Injected shared-practice owner (A's ``shared_practice_service``).  ``None`` = A has not
        #: published it into this composition: the surface then answers a named
        #: ``SHARED_PRACTICE_UNBOUND`` instead of inventing a second work/chat store.
        self._shared_practice_owner = shared_practice
        self._shared_practice: Any = None
        self._project_port = project_port
        self._shared_practice_error = ""

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
        self._reading = factory(
            self._port(),
            reading_artifact_id=self.reading_artifact_id,
            private_items_artifact_id=self.private_items_artifact_id,
            workspace_id=self.workspace_id,
            grant_id=self.artifact_grant_id,
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
            subject=self.subject,
            gateway_socket=self.world_body_socket,
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
            subject=self.subject,
            gateway_socket=self.world_body_socket,
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
        return envelope(True, STATUS_OK, {
            "pose": view.get("pose"),
            "orientation": view.get("orientation"),
            "location": {k: location.get(k) for k in ("place_id", "area_id")},
            "world_revision": view.get("world_revision"),
            "source": "world_body_gateway.get_snapshot",
        })

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

    # -- local visits / co-presence (A's ``LocalContactInbox``; P only delivers) -----------

    def _contact_state_root(self) -> Optional[Path]:
        """This instance's own Life state root, or ``None`` when it never had one.

        Two real sources, in this order: the environment the instance's own Life binding maps
        (``phone_life.map_life_environment`` -- recorded ``NYA02_RESIDENT_STATE`` first), then the
        very path ``one_day_life.prepare`` would have written for this root.  Nothing is created
        here: a root that does not exist stays honestly unbound.
        """

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
        """``GET /contact/events/<ref>``: one event; the text only on explicit ask."""

        return self._contact_call("event", event_ref, include_body=include_body)

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

    def memory_owner(self) -> str:
        """Which Memory persona this instance reads: the instance/env config, never a client.

        The order is: the exact owner env name the shipped resident reads, then a field the
        instance file recorded, then ``phone.life.MEMORY_DEFAULT_OWNER``.  A request body can
        never reach this method -- the phone surface does not accept ``owner`` at all.
        """
        from . import life as phone_life

        names: tuple[str, ...] = ()
        try:
            names = tuple(phone_life.memory_env_names().get("owner") or ())
        except Exception:
            names = ()
        for name in names:
            value = str(self.environment.get(name) or "").strip()
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
            return memory_service_from_env(dict(self.environment))
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
        from . import life as phone_life

        enabler = getattr(phone_life, "enable_instance_memory", None)
        if not callable(enabler):
            return unavailable("MEMORY_ENABLE_UNAVAILABLE",
                               detail="这一版还没有显式启用入口（A 的 enable_instance_memory）。")
        try:
            answer = enabler(self.root, owner=self.memory_owner(), operation_key=operation_key)
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

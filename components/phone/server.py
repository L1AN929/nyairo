#!/usr/bin/env python3
"""v0.4 手机 HTTP 面：loopback-only、单实例、一个 JSON 信封。

这里的每一条闸门都是 **fail closed**：

* 只监听 ``127.0.0.1``；``Host`` 必须是 ``127.0.0.1`` / ``localhost``（可带端口）；
* ``Origin``（若有）必须与 ``Host`` 同源；``Sec-Fetch-Site``（若有）只能是
  ``same-origin`` / ``none``；
* 每个 ``POST`` 都要 ``X-CSRF-Token``，值与 ``GET /api/phone/state`` 里的
  ``csrf_token`` 一致（常量时间比较）；
* 请求字段白名单；客户端带来的 ``uid``/``authority``/``main_self_verified``/``home``/
  ``price``/``balance``/``owner`` 一律不当身份（直接拒）；
* 静态文件只有固定三个名字，``profile``/仓库根**不可能**被当成静态目录；
* ``POST /api/phone/world/actions`` 只收 ``{action, params, operation_key}``，``params`` 逐动词
  白名单（``MOVE/PICK_UP/PLACE/POSE/REST`` + 本轮的 ``ARRANGE``/``TOGGLE_FIXTURE``）；
  ``uid``/``home``/``authority``/``elapsed``/价格/身体值出现在 params 里同样是
  ``CLIENT_IDENTITY_REFUSED``；
* ``GET /api/phone/store`` 是只读的实体店投影（到店/营业/目录都是 owner 的话），
  ``POST /api/phone/store/purchases`` 只收 ``{catalog_item_id, operation_key}``：价格、余额、
  交付状态、owner 一律不接受，``applied`` 不等于「已到货」，GET 更不会配送或物化。
* 阅读工作台只经 C 的 ``PhoneReading``：``GET /api/phone/reading/material?page=N`` 只返回该页真实
  正文，绝不记录、绝不写盘、绝不创建材料；``POST .../reading/material``、``.../reading/positions``、
  ``.../reading/annotations`` 只收正文/页码/来源版本/owner head/kind 白名单/幂等键，
  ``GET .../reading/operations/<key>`` 按**原 key** 对账（未知就保留原 key，不换键重试）。
  客户端不能声明 artifact id、路径、URL、authority、title 或理解；正文只出现在这一条私人读取面上，
  不进 ``/state``、不进旧 ``GET /api/phone/reading`` 的元数据列表。
* 私人项目（B 的 ``private_project_service``）只暴露它的五个面：``GET /api/phone/projects``（列表，
  不含正文）、``GET /api/phone/projects/<artifact_id>``（正文 + head + 版本历史）、
  ``POST /api/phone/projects``（``{title, content, operation_key}``）、
  ``POST .../projects/<id>/commits``（``{content, expected_head, operation_key}``）与
  ``POST .../projects/<id>/close``（``{expected_revision, operation_key}``，归档不是删除）。
  GET 是纯读（宿主/来源闸门，零写）；写要 CSRF、请求体白名单，CAS 由 owner 判定。
  客户端永远不在 body 里声明 artifact id（id 只在路径里），也不声明 workspace/grant。

信封与 HTTP 码的映射见 ``docs/engineering/v04-phone-api-contract.md`` §5；世界/身体/房间字段与
动词参数只按 C 的契约（``docs/engineering/v04-world-body-contract.md`` +
``docs/engineering/rc1-room-living-contract.md``）转发，本层不重算任何 owner 事实。
"""
from __future__ import annotations

import hmac
import json
import logging
import re
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import parse_qsl, urlsplit

from . import service as phone_service
from . import living as phone_living
from . import sharing as phone_sharing
from . import contact as phone_contact
from . import practice_shared as phone_practice

WEB_DIR = Path(__file__).resolve().parent / "web"

#: The one API prefix this surface serves.  Nothing else under the host is routed here.
API_PREFIX = "/api/phone"

MAX_BODY_BYTES = 64 * 1024
#: One album import may carry ~5 MiB of picture bytes (~7 MiB of base64 JSON).  This wider body
#: belongs to ``POST /api/phone/album/images`` alone; every other POST keeps MAX_BODY_BYTES.
MAX_ALBUM_BODY_BYTES = 8 * 1024 * 1024
MAX_ALBUM_CAPTION = 200
#: The local supervisor's run controls.  The client only names a bound and a pause intent; the
#: shared feature cap, the ledger baseline and every refusal stay in ``living``/the supervisor.
LIVING_WINDOW_FIELDS = frozenset({"duration_seconds", "max_new_requests"})
LIVING_PAUSE_FIELDS = frozenset({"paused"})
MAX_OPERATION_KEY_LEN = phone_service.MAX_OPERATION_KEY

STATIC_FILES: dict[str, tuple[str, str]] = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}

ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost"})
ALLOWED_FETCH_SITES = frozenset({"same-origin", "none"})

#: Request-body keys that would try to smuggle identity or owner facts.  Never accepted.
IDENTITY_FIELDS = frozenset({
    "uid", "euid", "gid", "authority", "authorities", "main_self_verified", "confirm_apply",
    "home", "hermes_home", "world_home", "profile", "profile_home", "owner", "subject",
    "price", "balance", "current_balance", "currency", "binding", "source", "actor",
    "verified_main_self", "grant_id", "workspace_id", "artifact_id",
})

NOTES_FIELDS = frozenset({"title", "body", "operation_key", "expected_head", "note_id"})
REFERENCE_FIELDS = frozenset({"source_id", "source_revision", "label", "operation_key"})
PURCHASE_FIELDS = frozenset({"catalog_item_id", "operation_key"})
USE_FIELDS = frozenset({"item_instance_id", "operation_key"})
#: ``POST /api/phone/store/purchases``: the counter purchase of one physical-store item.  The
#: client names the catalogue row and its own idempotency key; it never names a price, a balance,
#: a delivery state or an owner.
STORE_PURCHASE_FIELDS = frozenset({"catalog_item_id", "operation_key"})

#: The reading workbench's request shapes (``docs/engineering/rc1-reading-contract.md``, C's).
#: The client only ever names *its own* intent: the text it explicitly supplies, the page, the
#: source revision it read and the owner head it observed.  Never an artifact id, a path, a URL,
#: an authority or an understanding.
READING_MATERIAL_FIELDS = frozenset({"content", "expected_revision", "operation_key"})
READING_POSITION_FIELDS = frozenset({"page", "source_revision", "expected_head", "operation_key"})
#: ``annotate`` is whitelisted *per kind*: a ``note`` carries title+body, an ``unclear`` mark
#: carries a note.  The union below is only the outer fence; ``_reading_annotation`` pins each
#: kind exactly, so one kind can never smuggle the other's fields into the owner.
READING_ANNOTATION_FIELDS = frozenset({
    "kind", "page", "source_revision", "expected_head", "operation_key", "title", "body", "note",
})
READING_ANNOTATION_KINDS = ("note", "unclear")
#: The only query parameter the reading material GET accepts, and it is a page number.
READING_QUERY_FIELDS = frozenset({"page"})

#: ``/api/phone/projects`` -- her private projects (B's ``private_project_service``).  The client
#: names a title, the text, the head it read, the artifact revision it read, and its own
#: idempotency key; it never names an artifact id in a body (the id is the path) and never a
#: workspace, a grant, a price or an authority.
PROJECT_CREATE_FIELDS = frozenset({"title", "content", "operation_key"})
PROJECT_COMMIT_FIELDS = frozenset({"content", "expected_head", "operation_key"})
PROJECT_CLOSE_FIELDS = frozenset({"expected_revision", "operation_key"})
#: ``POST /api/phone/projects/derived`` -- one **new** work taken from one pinned old version.
#: ``purpose`` is the only vocabulary this surface adds (quote / adapt / reuse); the source ids are
#: owner ids, matched exactly, and ``note`` is a bounded one-line remark.
PROJECT_DERIVE_FIELDS = frozenset({"title", "content", "source_artifact_id", "source_version_id",
                                   "purpose", "note", "operation_key"})
PROJECT_VERSIONS_QUERY_FIELDS = frozenset({"limit", "before"})

#: ``/api/phone/album`` -- her private imported pictures.  The import body names a caption, the
#: declared image type and the picture bytes as plain base64; it never names a path, a URL, a
#: file name, an owner, an authority or a subject.
ALBUM_IMPORT_FIELDS = frozenset({"caption", "content_type", "data", "operation_key"})
ALBUM_CONTENT_TYPES = frozenset({"image/png", "image/jpeg"})

#: ``/api/phone/shares`` -- the share surface (B's ``LifeSharingService``, forwarded by P).  The
#: client names the source it saw in ``GET /shares/sources``, its own explicit short text (or an
#: explicit excerpt length), an optional title / expiry window and its own idempotency key.  It
#: never names an audience or a channel: those are P's fixed ``local:owner`` / ``local_inbox``.
SHARE_PREPARE_FIELDS = frozenset({"source_artifact_id", "share_text", "excerpt_chars", "title",
                                  "expires_after_s", "operation_key"})
#: One explicit action.  ``action`` is one of ``send_local`` / ``defer`` / ``drop``; ``expire`` is
#: the owner's own fact (a read-side derivation) and is **not** a phone write.
SHARE_ACTION_FIELDS = frozenset({"action", "expected_head", "operation_key", "defer_condition",
                                 "opportunity_ref", "reason"})
#: The only two query parameters the share list accepts (``states`` may repeat).
SHARE_QUERY_FIELDS = frozenset({"states", "limit"})
SHARE_ACTIONS = phone_sharing.PHONE_SHARE_ACTIONS
#: Anything that would try to address this share somewhere other than the local inbox.  A client
#: that sends one is refused by name (409), never silently ignored.
SHARE_ADDRESSING_FIELDS = frozenset({
    "audience", "audiences", "channel", "channels", "recipient", "recipients", "to", "cc", "bcc",
    "email", "mail", "url", "uri", "endpoint", "webhook", "telegram", "chat_id", "username",
    "account", "address", "transport", "provider", "smtp", "sms", "notify", "send_to", "external",
    "account", "address", "transport", "provider", "smtp", "sms", "notify", "send_to", "external",
})

#: ``/api/phone/contact`` -- the local visit surface (A's ``LocalContactInbox``, delivered by P).
#: The client names *its own* arrival: the kind, its own text, an explicit scope (invitations
#: only), the invitation an end closes, and its own idempotency key.  It never names a subject,
#: a home, a state root, a path, a URL, a channel or an outcome.
CONTACT_SUBMIT_FIELDS = frozenset({
    "event_kind", "text", "scope", "expires_after_s", "duration_seconds", "requires_engagement",
    "atomic", "invitation_id", "explicit_end", "reason", "operation_key",
})
#: The only query parameter the contact list accepts.
CONTACT_QUERY_FIELDS = frozenset({"limit"})
#: Fields that would try to decide *whose* inbox this arrives in, or how it is delivered.  A
#: client that sends one is refused by name (409) rather than silently ignored: a silently
#: dropped ``home``/``channel`` looks like the arrival went somewhere it never went.
CONTACT_REFUSED_FIELDS = frozenset({
    "subject", "subject_id", "home", "home_ref", "state_root", "life_state_root", "root",
    "path", "file", "file_path", "url", "uri", "endpoint", "channel", "channels", "audience",
    "recipient", "to", "transport", "provider", "source", "source_path", "db", "database",
    "uid", "euid", "owner", "grant_id", "workspace_id", "artifact_id",
})
#: The three kinds a client may ask for; A's names, verbatim (no translation layer).
CONTACT_KINDS = phone_contact.KIND_TEXT

#: ``/api/phone/shared-practice`` -- shared practice over A's ``shared_practice_service``.  The
#: client names **its own** intent only: one material it saw in the owner's own source list, the
#: text *the user* wrote, the head it read, and its own idempotency key.  It never names a
#: subject, a contributor, a state, an outcome she made, a home, a path, a URL or a channel.
#: ``/api/phone/shared-practice`` -- shared practice over A's ``shared_practice_service``.  The
#: client names **its own** intent only: one material it saw in the owner's own source list, the
#: title it saw, the text *the user* wrote, the version it read, and its own idempotency key.  It
#: never names a subject, a contributor, a state, an outcome she made, a home, a path, a URL or a
#: channel.  ``text`` is the user's **first turn** (A's ``open_practice`` needs an opening text).
SHARED_PRACTICE_OPEN_FIELDS = frozenset({"source_artifact_id", "source_version_id", "title",
                                        "text", "operation_key"})
#: One explicit user action: ``action`` is one of ``phone_practice.PHONE_ACTIONS``
#: (``submit`` / ``modify`` / ``pause`` / ``end``); ``text`` is only meaningful for
#: ``submit`` / ``modify``, ``reason`` only for ``pause`` / ``end``.  ``resume`` / ``reopen``
#: are hers: A's ``ACTOR_WRITES`` gives them to the Life side only, so they are refused here.
SHARED_PRACTICE_ACTION_FIELDS = frozenset({"action", "expected_head", "operation_key", "text",
                                          "reason"})
#: Fields that would try to decide *who* contributed, *who* accepted, or where this lives.  A
#: client that sends one is refused by name (409) rather than silently ignored: a silently
#: dropped ``subject``/``contribution`` looks like her side was written.
SHARED_PRACTICE_REFUSED_FIELDS = frozenset({
    "subject", "subject_id", "by", "contributor", "author", "role", "side",
    "life", "life_text", "subject_text", "life_contribution", "subject_contribution",
    "accept", "accepted", "decision", "outcome", "result", "state", "status",
    "home", "home_ref", "state_root", "life_state_root", "root", "path", "file", "file_path",
    "url", "uri", "endpoint", "channel", "channels", "audience", "recipient", "to", "transport",
    "provider", "source", "source_path", "db", "database", "uid", "euid", "owner", "grant_id",
    "workspace_id", "artifact_id", "private_text", "work_text", "contributions", "versions",
})
CONTACT_KINDS = phone_contact.KIND_TEXT

#: ``/api/phone/materials`` -- the material library (B's ``research_workspace_service``).  The
#: client names its own text, the declared source kind, the declared origin and its own
#: idempotency key; it never names an artifact id (import always mints a new one) and never a
#: path, a URL, a workspace or an authority.
MATERIAL_IMPORT_FIELDS = frozenset({"title", "text", "source_kind", "origin", "operation_key"})
#: The only two query parameters the material list accepts.
MATERIAL_QUERY_FIELDS = frozenset({"query", "limit"})
#: ``/api/phone/research`` -- one research work.  ``source_refs`` is the *only* nested place an
#: artifact id may appear: the client chooses materials it saw in the library, and the service
#: verifies every one of them against the owner before anything is created.
RESEARCH_CREATE_FIELDS = frozenset({"question", "source_refs", "title", "scope", "uncertainty",
                                    "operation_key"})
RESEARCH_UPDATE_FIELDS = frozenset({"content", "expected_head", "operation_key", "scope",
                                    "uncertainty"})
RESEARCH_CLOSE_FIELDS = frozenset({"expected_revision", "operation_key"})
RESEARCH_SOURCE_FIELDS = frozenset({"artifact_id", "source_revision"})
#: The only query parameter the research list accepts.
RESEARCH_QUERY_FIELDS = frozenset({"limit"})

#: ``/api/phone/memory`` -- A's Memory service.  ``GET`` is a pure read (status / experiences);
#: ``POST .../memory/enable`` only names its own idempotency key: the owner, the database and the
#: controls are this instance's configuration, never a request field.
MEMORY_ENABLE_FIELDS = frozenset({"operation_key"})
#: The only query parameters the experience search accepts.
MEMORY_QUERY_FIELDS = frozenset({"query", "limit"})

#: ``/api/phone/practice`` -- B's personal practice workspace (reviews + methods).  The client
#: names its own text, the experience/review refs it saw, the head it read and its own key; it
#: never names a workspace, a grant, an owner, a state or an outcome it made up.
PRACTICE_REVIEW_CREATE_FIELDS = frozenset({"question", "title", "experience_ids",
                                          "operation_key"})
#: The body continuation of one review/method: the text plus the head read before editing.
PRACTICE_DOCUMENT_FIELDS = frozenset({"content", "expected_head", "operation_key"})
PRACTICE_METHOD_CREATE_FIELDS = frozenset({"title", "instruction", "scope", "uncertainty",
                                          "experience_ids", "review_refs", "operation_key"})
#: One trial evaluation.  ``method_state`` is *not* accepted: only the service decides it.
PRACTICE_TRIAL_FIELDS = frozenset({"outcome", "detail", "experience_ids", "expected_head",
                                  "operation_key"})
PRACTICE_REVISE_FIELDS = frozenset({"instruction", "scope", "uncertainty", "expected_head",
                                   "operation_key"})
PRACTICE_RETIRE_FIELDS = frozenset({"expected_head", "operation_key"})
#: The only two query parameters the method list accepts.
PRACTICE_QUERY_FIELDS = frozenset({"query", "limit"})

#: ``POST /api/phone/world/actions`` accepts exactly these three top-level keys.
WORLD_FIELDS = frozenset({"action", "params", "operation_key"})

#: The per-verb parameter table.  The first five are verbatim from C's
#: ``docs/engineering/v04-world-body-contract.md`` §3.1; the room verbs are this round's
#: (``docs/engineering/rc1-room-living-contract.md``, C's).  P is a pre-flight only: the same
#: rules are enforced again by ``PhoneWorld.act`` / the Resolver, and the owner's word wins.
WORLD_ACTION_FIELDS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "MOVE": (frozenset({"destination_location_id"}), frozenset({"destination_location_id"})),
    "PICK_UP": (frozenset({"object_id", "slot_id"}), frozenset({"object_id", "slot_id"})),
    "PLACE": (frozenset({"object_id", "target"}), frozenset({"object_id", "target"})),
    "POSE": (frozenset({"pose", "orientation"}), frozenset({"pose"})),
    "REST": (frozenset({"state", "mode"}), frozenset({"state"})),
}

#: The room verbs, verbatim from C's ``PhoneWorld.VERBS`` / ``PARAM_SPEC``:
#: ``ARRANGE`` takes one owned instance (``object_id``) plus its ``target``;
#: ``TOGGLE_FIXTURE`` takes ``object_id`` plus the absolute ``state``.  P is a pre-flight only:
#: the owner re-checks both, and its word (and reason code) wins.
ROOM_WORLD_ACTION_FIELDS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "ARRANGE": (frozenset({"object_id", "target"}), frozenset({"object_id", "target"})),
    "TOGGLE_FIXTURE": (frozenset({"object_id", "state"}), frozenset({"object_id", "state"})),
}
WORLD_ACTION_FIELDS.update(ROOM_WORLD_ACTION_FIELDS)
#: An owned piece of furniture is arranged into a room or onto a declared surface.  A container,
#: a hand or transit are other verbs and other owners.
ARRANGE_TARGET_KINDS = frozenset({"room", "surface"})
FIXTURE_STATES = phone_service.FIXTURE_STATES

#: Values that are only ever the *world's* to state.  A client that sends one is refused
#: with ``CLIENT_IDENTITY_REFUSED`` (409) instead of being silently ignored.
WORLD_FORBIDDEN_PARAM_KEYS = frozenset({
    "uid", "euid", "gid", "authority", "authorities", "main_self_verified", "home",
    "hermes_home", "world_home", "profile", "profile_home", "owner", "subject", "binding",
    "elapsed", "elapsed_seconds", "elapsed_ms", "duration_seconds", "seconds", "rest_seconds",
    "fatigue", "activation", "comfort", "sensory_load", "tension", "warmth", "health", "energy",
    "body_values", "bands", "signals",
    # A price, a balance or a delivery state is the economy owner's word, never a client's.
    "price", "price_jpy", "amount", "cost", "balance", "currency", "delivery_state",
    "delivery_seconds", "elapsed_hours",
})

HAND_SLOTS = frozenset({"left_hand", "right_hand"})
POSES = frozenset({"standing", "seated", "lying", "transitioning"})
ORIENTATIONS = frozenset({"north", "south", "east", "west", "up", "down"})
REST_STATES = frozenset({"start", "stop"})
REST_MODES = frozenset({"rest", "sleep_like"})

#: Placement targets are validated structurally here (exact key set per kind) and
#: semantically by ``world_body_substrate.validate_placement_target`` inside the owner.
WORLD_TARGET_FIELDS: dict[str, frozenset[str]] = {
    "room": frozenset({"kind", "location_id", "area_id"}),
    "surface": frozenset({"kind", "location_id", "surface_id"}),
    "container": frozenset({"kind", "location_id", "container_id"}),
    "hand": frozenset({"kind", "slot_id"}),
    "in_transit": frozenset({"kind", "from_location_id", "to_location_id"}),
}

MAX_WORLD_DESTINATION = 96
MAX_WORLD_OBJECT_ID = 128
MAX_WORLD_PLACEMENT_ID = 128


_OPERATION_PATH = re.compile(r"^/api/phone/operations/([A-Za-z0-9._:\-]{1,128})$")
_WORLD_OPERATION_PATH = re.compile(r"^/api/phone/world/operations/([A-Za-z0-9._:\-]{1,128})$")
#: ``GET /api/phone/reading/operations/<key>``: the *original* reading key, looked up as-is.
#: An unknown key stays that key; there is no "retry with a fresh key" path here.
_READING_OPERATION_PATH = re.compile(r"^/api/phone/reading/operations/([A-Za-z0-9._:\-]{1,128})$")
#: ``GET /api/phone/projects/<artifact_id>``.  The id is the owner's own protocol string
#: (``artifact:<hex>``); it is matched, never rewritten, and it is the *only* place a project id
#: appears in a request (never a body field).
_PROJECT_PATH = re.compile(r"^/api/phone/projects/([A-Za-z0-9._:\-]{1,200})$")
_PROJECT_COMMITS_PATH = re.compile(r"^/api/phone/projects/([A-Za-z0-9._:\-]{1,200})/commits$")
_PROJECT_CLOSE_PATH = re.compile(r"^/api/phone/projects/([A-Za-z0-9._:\-]{1,200})/close$")
#: v0.4 work-revisit: one project's version list, one named old version, that version's compare
#: against today's head, and where a sourced work came from.  All four are pure GET reads; the
#: compare path is matched before the plain version path so it is never read as a version id.
_PROJECT_VERSIONS_PATH = re.compile(r"^/api/phone/projects/([A-Za-z0-9._:\-]{1,200})/versions$")
_PROJECT_VERSION_COMPARE_PATH = re.compile(
    r"^/api/phone/projects/([A-Za-z0-9._:\-]{1,200})/versions/([A-Za-z0-9._:\-]{1,200})/compare$")
_PROJECT_VERSION_PATH = re.compile(
    r"^/api/phone/projects/([A-Za-z0-9._:\-]{1,200})/versions/([A-Za-z0-9._:\-]{1,200})$")
_PROJECT_PROVENANCE_PATH = re.compile(
    r"^/api/phone/projects/([A-Za-z0-9._:\-]{1,200})/provenance$")
#: ``/api/phone/album/<artifact_id>`` is one imported picture; ``/image`` is its real bytes.
#: The image path is matched first so it can never be read as an artifact id.  The id is the
#: owner's own protocol string, matched and never rewritten.
_ALBUM_IMAGE_PATH = re.compile(r"^/api/phone/album/([A-Za-z0-9._:\-]{1,200})/image$")
_ALBUM_PATH = re.compile(r"^/api/phone/album/([A-Za-z0-9._:\-]{1,200})$")
LOG = logging.getLogger("chiyo.phone.http")

#: ``GET /api/phone/materials/<artifact_id>``: a material id, matched exactly as the owner
#: spells it (``artifact:<hex>``).  It is the only place a material id appears in a request.
_MATERIAL_PATH = re.compile(r"^/api/phone/materials/([A-Za-z0-9._:\-]{1,200})$")
#: One research work.  ``/updates`` and ``/close`` are their own paths; the bare path is a read.
_RESEARCH_PATH = re.compile(r"^/api/phone/research/([A-Za-z0-9._:\-]{1,200})$")
_RESEARCH_UPDATES_PATH = re.compile(r"^/api/phone/research/([A-Za-z0-9._:\-]{1,200})/updates$")
_RESEARCH_CLOSE_PATH = re.compile(r"^/api/phone/research/([A-Za-z0-9._:\-]{1,200})/close$")

#: ``GET /api/phone/memory/experiences/<experience_id>``: A's own experience id, matched as the
#: owner spells it.  It is the only place an experience id appears in a path.
_MEMORY_EXPERIENCE_PATH = re.compile(r"^/api/phone/memory/experiences/([A-Za-z0-9._:\-]{1,200})$")
#: One review / one method.  ``/documents``, ``/trials``, ``/revisions`` and ``/retire`` are their
#: own paths; the bare path is a read.  A practice artifact id is never a body field.
_PRACTICE_REVIEW_PATH = re.compile(r"^/api/phone/practice/reviews/([A-Za-z0-9._:\-]{1,200})$")
_PRACTICE_REVIEW_DOCUMENTS_PATH = re.compile(
    r"^/api/phone/practice/reviews/([A-Za-z0-9._:\-]{1,200})/documents$")
_PRACTICE_METHOD_PATH = re.compile(r"^/api/phone/practice/methods/([A-Za-z0-9._:\-]{1,200})$")
_PRACTICE_METHOD_TRIALS_PATH = re.compile(
    r"^/api/phone/practice/methods/([A-Za-z0-9._:\-]{1,200})/trials$")
_PRACTICE_METHOD_REVISIONS_PATH = re.compile(
    r"^/api/phone/practice/methods/([A-Za-z0-9._:\-]{1,200})/revisions$")
_PRACTICE_METHOD_RETIRE_PATH = re.compile(
    r"^/api/phone/practice/methods/([A-Za-z0-9._:\-]{1,200})/retire$")

#: ``GET /api/phone/shares`` is the list, ``GET .../shares/sources`` the selectable sources and
#: ``GET .../shares/operations/<key>`` the pure-read reconciliation of one original key.  A share
#: id in a path is B's own protocol string (``artifact:<hex>``): matched, never rewritten.
_SHARE_PATH = re.compile(r"^/api/phone/shares/([A-Za-z0-9._:\-]{1,200})$")
_SHARE_ACTIONS_PATH = re.compile(r"^/api/phone/shares/([A-Za-z0-9._:\-]{1,200})/actions$")
_SHARE_OPERATION_PATH = re.compile(r"^/api/phone/shares/operations/([A-Za-z0-9._:\-]{1,128})$")
#: ``/api/phone/contact`` -- the local visit / co-presence entry.  ``/events`` is the bounded
#: transport list, ``/events/<event_ref>`` one event, ``/operations/<key>`` the pure-read
#: reconciliation of one original key.  An event ref is A's own protocol string
#: (``contact_evt:<hex>``): matched, never rewritten, and never a body field.
_CONTACT_PATH = re.compile(r"^/api/phone/contact/events/([A-Za-z0-9._:\-]{1,200})$")
_CONTACT_OPERATION_PATH = re.compile(r"^/api/phone/contact/operations/([A-Za-z0-9._:\-]{1,128})$")
#: ``/api/phone/shared-practice`` -- shared practice over A's ``shared_practice_service``.
#: ``/works/<id>`` is one practice (its id is A's protocol string, matched never rewritten),
#: ``/works/<id>/actions`` the one explicit **user** action, ``/operations/<key>`` the pure-read
#: reconciliation of one original key.  A practice id is never a body field, and no path here
#: accepts a file, a URL, a channel or a contributor.
_SHARED_PRACTICE_WORK_PATH = re.compile(
    r"^/api/phone/shared-practice/works/([A-Za-z0-9._:\-]{1,200})$")
_SHARED_PRACTICE_ACTIONS_PATH = re.compile(
    r"^/api/phone/shared-practice/works/([A-Za-z0-9._:\-]{1,200})/actions$")
_SHARED_PRACTICE_OPERATION_PATH = re.compile(
    r"^/api/phone/shared-practice/operations/([A-Za-z0-9._:\-]{1,128})$")
#: The only HTTP codes this surface may ever answer with: the existing 200/202/404/409/503
#: conventions, the 400 for a request that cannot be a request at all, and 413 for an oversized
#: body.  A facade's ``http_status`` hint is honoured inside this set only.
ALLOWED_HTTP_STATUS: frozenset[int] = frozenset({200, 202, 400, 404, 409, 413, 503})


class PhoneRequestError(ValueError):

    """A request this surface refuses before touching any owner."""

    def __init__(self, reason_code: str, status: int = 400) -> None:
        super().__init__(reason_code)
        self.reason_code = str(reason_code)
        self.status = int(status)


def http_status_for(status: str, result: Optional[Mapping[str, Any]] = None) -> int:
    """The one mapping from envelope status to HTTP code.

    C's facades publish an optional ``http_status`` hint (``phone_reading.envelope``).  P honours
    it only inside this surface's frozen set **and** only when it cannot flip the meaning of the
    answer: a success stays 200/202, a refusal stays an error.  Anything else falls back to the
    status map, so no owner can talk P into answering 200 for a refusal (or 409 for a success).
    """

    mapped = {
        phone_service.STATUS_OK: 200,
        phone_service.STATUS_ALREADY: 200,
        phone_service.STATUS_REJECTED: 409,
        phone_service.STATUS_DISABLED: 409,
        phone_service.STATUS_UNAVAILABLE: 503,
        phone_service.STATUS_UNKNOWN: 202,
        phone_service.STATUS_NOT_FOUND: 404,
        # B's research-workspace service speaks the project service's vocabulary too: ``ok`` is
        # its pure-read success, ``conflict`` is a stale CAS / version precondition (409).
        "ok": 200,
        "conflict": 409,
        # B's share service keeps its own word for "the source moved since the draft": it is a
        # definite refusal (nothing was written), 409 like a CAS conflict, and it is *not* folded
        # into "conflict" -- the screen has to say "来源换版" and not "版本冲突".
        phone_sharing.STATUS_STALE_SOURCE: 409,
    }.get(str(status), 200)
    hint = result.get("http_status") if isinstance(result, Mapping) else None
    if isinstance(hint, int) and not isinstance(hint, bool) and hint in ALLOWED_HTTP_STATUS:
        if bool(result.get("ok")) and hint in (200, 202):
            return int(hint)
        if not result.get("ok") and hint in (400, 404, 409, 413, 503):
            return int(hint)
    return mapped


def _failure(reason_code: str) -> dict[str, Any]:
    return {"ok": False, "status": phone_service.STATUS_REJECTED,
            "reason_code": str(reason_code), "data": {}}


class PhoneHTTPServer(ThreadingHTTPServer):
    """One bound loopback server; the service and the CSRF secret live here, not globally."""

    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address: tuple[str, int], *, service: phone_service.PhoneService,
                 csrf_token: str, log_path: Optional[Path] = None,
                 supervisor: Optional[Any] = None) -> None:
        if not isinstance(service, phone_service.PhoneService):
            raise TypeError("PhoneHTTPServer needs a PhoneService")
        if not isinstance(csrf_token, str) or not csrf_token:
            raise ValueError("csrf_token required")
        self.service = service
        self.csrf_token = csrf_token
        self.log_path = Path(log_path) if log_path else None
        #: The optional local supervisor.  A plain ``phone serve`` has none: the living view is
        #: still readable, but the run controls answer ``SUPERVISOR_UNAVAILABLE`` instead of
        #: starting a model window from a surface that never offered one.
        self.supervisor = supervisor
        super().__init__(address, PhoneRequestHandler)


class PhoneRequestHandler(BaseHTTPRequestHandler):
    server_version = "chiyo-phone/0.4"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # -- plumbing ------------------------------------------------------------------------

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003 - stdlib signature
        """Never log headers, tokens or bodies: method, path and code only."""

        LOG.info("%s %s", self.command, self.path.split("?", 1)[0])

    def _send(self, code: int, payload: Mapping[str, Any]) -> None:
        body = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(int(code))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_image(self, content_type: str, body: bytes) -> None:
        """One real picture response.  Same-origin only; never cached, never sniffed."""
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_static(self, name: str, content_type: str) -> None:
        path = WEB_DIR / name
        if name not in {value[0] for value in STATIC_FILES.values()}:
            self._send(404, _failure("NOT_FOUND"))
            return
        try:
            body = path.read_bytes()
        except OSError:
            self._send(404, _failure("STATIC_FILE_MISSING"))
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    # -- guards --------------------------------------------------------------------------

    def _host_allowed(self) -> bool:
        raw = str(self.headers.get("Host") or "").strip().lower()
        if not raw:
            return False
        host = raw
        if host.startswith("["):  # IPv6 literal - never allowed here
            return False
        if ":" in host:
            host, _, port = host.partition(":")
            if not port.isdigit():
                return False
        return host in ALLOWED_HOSTS

    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        raw = str(origin).strip()
        parts = urlsplit(raw)
        if parts.scheme != "http" or parts.path not in ("", "/") or parts.query:
            return False
        host = (parts.hostname or "").lower()
        if host not in ALLOWED_HOSTS:
            return False
        expected_port = self.server.server_address[1]
        if parts.port is not None and int(parts.port) != int(expected_port):
            return False
        return True

    def _fetch_site_allowed(self) -> bool:
        value = self.headers.get("Sec-Fetch-Site")
        if value is None:
            return True
        return str(value).strip().lower() in ALLOWED_FETCH_SITES

    def _guard_common(self) -> Optional[tuple[int, dict[str, Any]]]:
        if not self._host_allowed():
            return 403, _failure("HOST_NOT_ALLOWED")
        if not self._origin_allowed():
            return 403, _failure("ORIGIN_NOT_ALLOWED")
        if not self._fetch_site_allowed():
            return 403, _failure("CROSS_SITE_REFUSED")
        return None

    def _guard_mutation(self) -> Optional[tuple[int, dict[str, Any]]]:
        guard = self._guard_common()
        if guard is not None:
            return guard
        token = self.headers.get("X-CSRF-Token")
        if token is None or not str(token).strip():
            return 403, _failure("CSRF_TOKEN_MISSING")
        if not hmac.compare_digest(str(token).strip(), str(self.server.csrf_token)):
            return 403, _failure("CSRF_TOKEN_INVALID")
        return None

    def _read_body(self, fields: frozenset[str],
                   refused: frozenset[str] = frozenset(),
                   max_bytes: Optional[int] = None) -> dict[str, Any]:
        content_type = str(self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type != "application/json":
            raise PhoneRequestError("CONTENT_TYPE_REQUIRED")
        raw_length = self.headers.get("Content-Length")
        if raw_length is None or not str(raw_length).strip().isdigit():
            raise PhoneRequestError("CONTENT_LENGTH_REQUIRED")
        length = int(str(raw_length).strip())
        if length <= 0:
            raise PhoneRequestError("BODY_REQUIRED")
        limit = MAX_BODY_BYTES if max_bytes is None else int(max_bytes)
        if length > limit:
            raise PhoneRequestError("BODY_TOO_LARGE", 413)
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise PhoneRequestError("BODY_NOT_JSON") from None
        if not isinstance(payload, dict):
            raise PhoneRequestError("BODY_NOT_AN_OBJECT")
        identifiers = sorted(set(payload) & IDENTITY_FIELDS)
        if identifiers:
            # The phone layer drops these; the owner never sees them as identity either.
            raise PhoneRequestError("CLIENT_IDENTITY_REFUSED:%s" % ",".join(identifiers), 409)
        addressing = sorted(set(payload) & set(refused))
        if addressing:
            # "Send this to X" is not a field this surface accepts: refused by name, never dropped
            # silently (silently ignoring it would look like the share went somewhere).
            raise PhoneRequestError("SHARE_ADDRESSING_REFUSED:%s" % ",".join(addressing), 409)
        unknown = sorted(set(payload) - set(fields))
        if unknown:
            raise PhoneRequestError("UNKNOWN_FIELD:%s" % ",".join(unknown), 400)
        return payload

    def _actor(self) -> str:
        declared = str(self.headers.get("X-Phone-Actor") or "").strip().lower()
        if not declared:
            return phone_service.ACTOR_USER
        if declared not in phone_service.ACTORS:
            # Not a hard failure: an unknown declaration is recorded as the plain user.
            return phone_service.ACTOR_USER
        return declared

    # -- routing -------------------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - stdlib signature
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802 - stdlib signature
        self._dispatch("POST")

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib signature
        self._dispatch("HEAD")

    def _dispatch(self, method: str) -> None:
        path = urlsplit(self.path).path
        if path in STATIC_FILES:
            if method not in ("GET", "HEAD"):
                self._send(405, _failure("METHOD_NOT_ALLOWED"))
                return
            guard = self._guard_common()
            if guard is not None:
                self._send(*guard)
                return
            self._send_static(*STATIC_FILES[path])
            return
        if not path.startswith(API_PREFIX):
            self._send(404, _failure("NOT_FOUND"))
            return
        try:
            self._dispatch_api(method, path)
        except PhoneRequestError as exc:
            self._send(exc.status, _failure(exc.reason_code))
        except Exception:  # never echo the exception text, path or token
            LOG.warning("phone.api.error path=%s", path)
            self._send(500, {"ok": False, "status": "error",
                             "reason_code": "PHONE_INTERNAL_ERROR", "data": {}})

    def _dispatch_api(self, method: str, path: str) -> None:
        service: phone_service.PhoneService = self.server.service
        operations = _OPERATION_PATH.match(path)
        world_operation = _WORLD_OPERATION_PATH.match(path)
        reading_operation = _READING_OPERATION_PATH.match(path)
        if method == "GET":
            if path == f"{API_PREFIX}/state":
                self._get_state(service)
                return
            # The phone's "now": a read-only projection rebuilt from the resident's own records
            # and the persisted ledger.  Opening the page never starts a service or a model.
            if path == f"{API_PREFIX}/living":
                self._get_living(service)
                return
            if path == f"{API_PREFIX}/notes":
                self._send(200, service.notes())
                return
            if path == f"{API_PREFIX}/references":
                self._send(200, service.references())
                return
            if path == f"{API_PREFIX}/reading":
                self._send(200, service.reading())
                return
            # The workbench's real text: one page, read-only, never recorded and never written.
            if path == f"{API_PREFIX}/reading/material":
                self._get_reading_material(service)
                return
            if reading_operation:
                self._send_or_map(service.reading_operation(reading_operation.group(1)))
                return
            if path == f"{API_PREFIX}/wallet":
                self._send_or_map(service.wallet())
                return
            if path == f"{API_PREFIX}/shop":
                self._send_or_map(service.shop())
                return
            # The physical shop's shelf and its opening state.  Same posture as the World
            # snapshot: a guarded pure read that never delivers, materializes or pays.
            if path == f"{API_PREFIX}/store":
                self._get_store(service)
                return
            if path == f"{API_PREFIX}/inventory":
                self._send_or_map(service.inventory())
                return
            # The World snapshot is read-only; it is still host/origin guarded because it
            # carries where she is and what her body is doing.
            if path == f"{API_PREFIX}/world":
                self._get_world(service)
                return
            # Her private album: the list carries captions/owner media metadata (no bytes); the
            # detail carries no bytes either; only ``/image`` serves the real picture.  The image
            # path is matched before the bare id path.
            if path == f"{API_PREFIX}/album":
                self._get_album_list(service)
                return
            if _ALBUM_IMAGE_PATH.match(path):
                self._get_album_image(service, _ALBUM_IMAGE_PATH.match(path).group(1))
                return
            if _ALBUM_PATH.match(path):
                self._get_album_detail(service, _ALBUM_PATH.match(path).group(1))
                return
            # Her own projects: a guarded pure read (the list carries no text; the detail does).
            if path == f"{API_PREFIX}/projects":
                self._get_projects(service)
                return
            if _PROJECT_PATH.match(path):
                self._get_project(service, _PROJECT_PATH.match(path).group(1))
            # v0.4 work-revisit: her old versions (metadata page), one named old version, its
            # bounded compare against today's head, and a sourced work's provenance.  All pure
            # reads under the same host/source guard as the rest of this surface.
            if _PROJECT_VERSIONS_PATH.match(path):
                self._get_project_versions(service, _PROJECT_VERSIONS_PATH.match(path).group(1))
                return
            if _PROJECT_VERSION_COMPARE_PATH.match(path):
                matched = _PROJECT_VERSION_COMPARE_PATH.match(path)
                self._get_project_version_compare(service, matched.group(1), matched.group(2))
                return
            if _PROJECT_VERSION_PATH.match(path):
                matched = _PROJECT_VERSION_PATH.match(path)
                self._get_project_version(service, matched.group(1), matched.group(2))
                return
            if _PROJECT_PROVENANCE_PATH.match(path):
                self._get_project_provenance(service, _PROJECT_PROVENANCE_PATH.match(path).group(1))
                return
                return
            # The material library and the research workbench (B's research workspace service):
            # guarded pure reads; the list carries no text, the detail does.
            if path == f"{API_PREFIX}/materials":
                self._get_materials(service)
                return
            if _MATERIAL_PATH.match(path):
                self._get_material(service, _MATERIAL_PATH.match(path).group(1))
                return
            if path == f"{API_PREFIX}/research":
                self._get_research(service)
                return
            if _RESEARCH_PATH.match(path):
                self._get_research_item(service, _RESEARCH_PATH.match(path).group(1))
                return
            # Memory (A's LifeMemoryService): guarded pure reads, never an initialisation.  The
            # detail is A's own envelope; P never derives an experience or a binding state.
            if path == f"{API_PREFIX}/memory":
                self._get_memory(service)
                return
            if path == f"{API_PREFIX}/memory/experiences":
                self._get_memory_experiences(service)
                return
            if _MEMORY_EXPERIENCE_PATH.match(path):
                self._get_memory_experience(service, _MEMORY_EXPERIENCE_PATH.match(path).group(1))
                return
            # The practice workbench (B's personal_practice_service): guarded pure reads.  The
            # list carries no bodies; the detail does (unless governance withholds them).
            if path == f"{API_PREFIX}/practice":
                self._get_practice(service)
                return
            if path == f"{API_PREFIX}/practice/methods":
                self._get_practice_methods(service)
                return
            if _PRACTICE_REVIEW_PATH.match(path):
                self._get_practice_review(service, _PRACTICE_REVIEW_PATH.match(path).group(1))
                return
            if _PRACTICE_METHOD_PATH.match(path):
                self._get_practice_method(service, _PRACTICE_METHOD_PATH.match(path).group(1))
                return
            # Her shares (B's LifeSharingService): two guarded pure reads and one pure-read
            # reconciliation.  ``/shares/sources`` is matched before the id pattern.
            if path == f"{API_PREFIX}/shares":
                self._get_shares(service)
                return
            if path == f"{API_PREFIX}/shares/sources":
                self._get_share_sources(service)
                return
            if _SHARE_OPERATION_PATH.match(path):
                self._get_share_operation(
                    service, _SHARE_OPERATION_PATH.match(path).group(1))
                return
            if _SHARE_PATH.match(path):
                self._get_share(service, _SHARE_PATH.match(path).group(1))
                return
            # Her local visits (A's ``LocalContactInbox``, delivered by P): guarded pure reads.
            # ``/contact/events`` matches before the event-id pattern; ``/operations/<key>`` is
            # the pure-read reconciliation of one original key.
            if path == f"{API_PREFIX}/contact":
                self._get_contact(service)
                return
            if path == f"{API_PREFIX}/contact/events":
                self._get_contact_events(service)
                return
            if _CONTACT_OPERATION_PATH.match(path):
                self._get_contact_operation(
                    service, _CONTACT_OPERATION_PATH.match(path).group(1))
                return
            if _CONTACT_PATH.match(path):
                self._get_contact_event(service, _CONTACT_PATH.match(path).group(1))
                return
            # Shared practice (A's ``shared_practice_service``): two guarded pure reads and one
            # pure-read reconciliation.  ``/works/<id>`` is matched before any future sub-path,
            # and ``/operations/<key>`` is the *original* key, looked up as-is.
            if path == f"{API_PREFIX}/shared-practice":
                self._get_shared_practices(service)
                return
            if _SHARED_PRACTICE_OPERATION_PATH.match(path):
                self._get_shared_practice_operation(
                    service, _SHARED_PRACTICE_OPERATION_PATH.match(path).group(1))
                return
            if _SHARED_PRACTICE_WORK_PATH.match(path):
                self._get_shared_practice_work(
                    service, _SHARED_PRACTICE_WORK_PATH.match(path).group(1))
                return
            if world_operation:
                key = world_operation.group(1)
                self._send_or_map(service.world_operation(key))
                return
            if operations:
                key = operations.group(1)
                self._send_or_map(service.operation(key))
                return
            self._send(404, _failure("NOT_FOUND"))
            return
        if method == "POST":
            if path == f"{API_PREFIX}/notes":
                self._post_note(service)
                return
            if path == f"{API_PREFIX}/references":
                self._post_reference(service)
                return
            if path == f"{API_PREFIX}/purchases":
                self._post_purchase(service)
                return
            if path == f"{API_PREFIX}/store/purchases":
                self._post_store_purchase(service)
                return
            if path == f"{API_PREFIX}/items/use":
                self._post_use(service)
                return
            if path == f"{API_PREFIX}/world/actions":
                self._post_world_action(service)
                return
            if path == f"{API_PREFIX}/reading/material":
                self._post_reading_material(service)
                return
            if path == f"{API_PREFIX}/reading/positions":
                self._post_reading_position(service)
                return
            if path == f"{API_PREFIX}/reading/annotations":
                self._post_reading_annotation(service)
                return
            # Her private album: exactly one explicit import.  There is no rename, no delete and
            # no second write path; the picture bytes reach only the Supply owner.
            if path == f"{API_PREFIX}/album/images":
                self._post_album_import(service)
                return
            # Her own projects: explicit title/text, one CAS head, one archived project.
            if path == f"{API_PREFIX}/projects":
                self._post_project_create(service)
                return
            # v0.4 work-revisit: **one new work** with a recorded source.  It is a create, not a
            # fork or a restore: the source artifact gets no new version out of it.
            if path == f"{API_PREFIX}/projects/derived":
                self._post_project_derived(service)
                return
            if _PROJECT_COMMITS_PATH.match(path):
                self._post_project_commit(service, _PROJECT_COMMITS_PATH.match(path).group(1))
                return
            if _PROJECT_CLOSE_PATH.match(path):
                self._post_project_close(service, _PROJECT_CLOSE_PATH.match(path).group(1))
                return
            # Materials and research: one import, one create, one CAS continuation, one close.
            if path == f"{API_PREFIX}/materials":
                self._post_material_import(service)
                return
            if path == f"{API_PREFIX}/research":
                self._post_research_create(service)
                return
            if _RESEARCH_UPDATES_PATH.match(path):
                self._post_research_update(service, _RESEARCH_UPDATES_PATH.match(path).group(1))
                return
            if _RESEARCH_CLOSE_PATH.match(path):
                self._post_research_close(service, _RESEARCH_CLOSE_PATH.match(path).group(1))
                return
            # Memory: one **explicit** enable.  GET never does this, and the answer's
            # ``restart_required`` is only reported -- no service is restarted from here.
            if path == f"{API_PREFIX}/memory/enable":
                self._post_memory_enable(service)
                return
            # The practice workbench: one create, one document, one trial, one revision, one
            # retire.  There is no delete path: retirement is a logical state in the document.
            if path == f"{API_PREFIX}/practice/reviews":
                self._post_practice_review_create(service)
                return
            if _PRACTICE_REVIEW_DOCUMENTS_PATH.match(path):
                self._post_practice_document(
                    service, _PRACTICE_REVIEW_DOCUMENTS_PATH.match(path).group(1))
                return
            if path == f"{API_PREFIX}/practice/methods":
                self._post_practice_method_create(service)
                return
            if _PRACTICE_METHOD_TRIALS_PATH.match(path):
                self._post_practice_trial(
                    service, _PRACTICE_METHOD_TRIALS_PATH.match(path).group(1))
                return
            if _PRACTICE_METHOD_REVISIONS_PATH.match(path):
                self._post_practice_revision(
                    service, _PRACTICE_METHOD_REVISIONS_PATH.match(path).group(1))
                return
            if _PRACTICE_METHOD_RETIRE_PATH.match(path):
                self._post_practice_retire(
                    service, _PRACTICE_METHOD_RETIRE_PATH.match(path).group(1))
                return
            # Shares: one explicit draft, one explicit action on one draft.  No delete path: a
            # dropped/expired share stays a terminal fact, and a delivered one is never re-sent.
            if path == f"{API_PREFIX}/shares":
                self._post_share_prepare(service)
                return
            # Shared practice: one open (pick a material) and one explicit **user** action.  There
            # is no path here that writes her side: ``submit_life`` / ``accept`` are not this
            # surface's vocabulary, and a payload that tries to name them is refused by name.
            if path == f"{API_PREFIX}/shared-practice":
                self._post_shared_practice_open(service)
                return
            if _SHARED_PRACTICE_ACTIONS_PATH.match(path):
                self._post_shared_practice_action(
                    service, _SHARED_PRACTICE_ACTIONS_PATH.match(path).group(1))
                return
            if _SHARE_ACTIONS_PATH.match(path):
                self._post_share_action(service, _SHARE_ACTIONS_PATH.match(path).group(1))
                return
            # Local visits: exactly one write -- deliver one arrival.  The phone never claims
            # it, never records its outcome and never starts a second Life writer; whether she
            # engages is decided by her own protocol after this transport row lands.
            if path == f"{API_PREFIX}/contact/events":
                self._post_contact_event(service)
                return
            # The local supervisor's explicit run controls.  Without a supervisor attached (a
            # plain ``phone serve``) they refuse by name; they never start a model themselves.
            if path == f"{API_PREFIX}/living/window":
                self._post_living_window(service)
                return
            if path == f"{API_PREFIX}/living/pause":
                self._post_living_pause(service)
                return
            if path == f"{API_PREFIX}/living/stop":
                self._post_living_stop(service)
                return
            self._send(404, _failure("NOT_FOUND"))
            return
            self._send(404, _failure("NOT_FOUND"))
            return
        self._send(405, _failure("METHOD_NOT_ALLOWED"))

    # -- shares (B's LifeSharingService owns every fact; every GET here is a pure read) ----

    def _share_query(self) -> tuple[Optional[list[str]], int]:
        """``?states=DRAFT,DEFERRED&limit=N``: two keys, each at most once, each validated.

        An unknown key, a repeated key, an unknown state name or an out-of-range limit is refused
        before the owner is asked: a silent fallback would look like "she has no drafts".
        """

        raw = urlsplit(self.path).query
        if not raw:
            return None, phone_sharing.MAX_SHARES
        pairs = parse_qsl(raw, keep_blank_values=True)
        keys = [key for key, _value in pairs]
        unknown = sorted(set(keys) - SHARE_QUERY_FIELDS)
        if unknown:
            raise PhoneRequestError("UNKNOWN_QUERY:%s" % ",".join(unknown))
        states: list[str] = []
        limit = phone_sharing.MAX_SHARES
        seen_limit = False
        for key, value in pairs:
            if key == "limit":
                if seen_limit:
                    raise PhoneRequestError("QUERY_REPEATED:limit")
                seen_limit = True
                parsed = _limit_value(value, phone_sharing.MAX_SHARES)
                limit = phone_sharing.MAX_SHARES if parsed is None else int(parsed)
                continue
            for entry in str(value).split(","):
                text = entry.strip().upper()
                if not text:
                    continue
                if text not in phone_sharing.SHARE_STATES:
                    raise PhoneRequestError("SHARE_STATE_INVALID")
                if text not in states:
                    states.append(text)
        return (states or None), limit

    def _get_shares(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/shares``: her drafts / deferred / inbox -- and no share text."""

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        states, limit = self._share_query()
        self._send_or_map(service.shares(states, limit))

    def _get_share(self, service: phone_service.PhoneService, share_id: str) -> None:
        """``GET /api/phone/shares/<id>``: state, source currency, receipt, bounded text."""

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        self._send_or_map(service.share(_share_id(share_id)))

    def _get_share_sources(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/shares/sources``: what may be a source now (owner-checked)."""

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        self._send_or_map(service.share_sources())

    def _get_share_operation(self, service: phone_service.PhoneService, key: str) -> None:
        """``GET /api/phone/shares/operations/<key>``: pure-read reconciliation, same key."""

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        self._send_or_map(service.share_operation(_operation_key(key)))

    def _post_share_prepare(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/shares``: one explicit draft.  Never a whole private material."""

        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        payload = self._read_body(SHARE_PREPARE_FIELDS, SHARE_ADDRESSING_FIELDS)
        source = _share_source_id(payload.get("source_artifact_id"))
        has_text = "share_text" in payload
        has_excerpt = "excerpt_chars" in payload
        if has_text and has_excerpt:
            raise PhoneRequestError("SHARE_TEXT_EXCLUSIVE")
        if not has_text and not has_excerpt:
            # 阶段单：绝不允许「把整篇私人材料当分享文本」的默认行为。
            raise PhoneRequestError("SHARE_TEXT_REQUIRED")
        text = _share_text(payload.get("share_text")) if has_text else None
        excerpt = _excerpt_chars(payload.get("excerpt_chars")) if has_excerpt else None
        title = _share_title(payload.get("title"))
        expiry = _expires_after_s(payload.get("expires_after_s"))
        key = _operation_key(payload.get("operation_key"))
        result = service.share_prepare(source_artifact_id=source, share_text=text,
                                       excerpt_chars=excerpt, title=title,
                                       expires_after_s=expiry, operation_key=key)
        service.record_operation(actor=self._actor(), action="share:prepare",
                                 operation_key=key, result=result)
        self._send_or_map(result)

    def _post_share_action(self, service: phone_service.PhoneService,
                           share_id: str) -> None:
        """``POST /api/phone/shares/<id>/actions``: send_local / defer / drop under her CAS head."""

        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        payload = self._read_body(SHARE_ACTION_FIELDS, SHARE_ADDRESSING_FIELDS)
        action = _share_action(payload.get("action"))
        expected_head = _clean_text(payload.get("expected_head"),
                                    phone_service.MAX_VERSION_ID, "EXPECTED_HEAD_INVALID")
        condition = _share_optional_line(payload.get("defer_condition"),
                                         phone_sharing.MAX_CONDITION_CHARS,
                                         "DEFER_CONDITION_INVALID")
        opportunity = _share_optional_line(payload.get("opportunity_ref"),
                                           phone_sharing.MAX_CONDITION_CHARS,
                                           "OPPORTUNITY_REF_INVALID")
        reason = _share_optional_line(payload.get("reason"), phone_sharing.MAX_REASON_CHARS,
                                      "REASON_INVALID")
        if action == "defer":
            if not condition and not opportunity:
                raise PhoneRequestError("DEFER_CONDITION_REQUIRED")
        else:
            # 「延后到什么机会」不属于发送/放弃：给了就是客户端的 bug，不静默丢掉。
            for field, value in (("defer_condition", condition), ("opportunity_ref", opportunity)):
                if value:
                    raise PhoneRequestError("UNKNOWN_FIELD:%s" % field)
        key = _operation_key(payload.get("operation_key"))
        result = service.share_action(share_id=_share_id(share_id), action=action,
                                      expected_head=expected_head, operation_key=key,
                                      defer_condition=condition or None,
                                      opportunity_ref=opportunity or None,
                                      reason=reason or None)
        service.record_operation(actor=self._actor(), action="share:" + action,
                                 operation_key=key, result=result)
        self._send_or_map(result)

    def _send_or_map(self, result: Mapping[str, Any]) -> None:
        self._send(http_status_for(str(result.get("status")), result), result)

    # -- local visits / co-presence (A's LocalContactInbox; P only delivers) --------------

    def _contact_limit_query(self) -> Optional[int]:
        """``?limit=N``: the one query parameter the visit list accepts.

        An unknown key, a repeated key or a value that is not a small positive integer is
        refused before the owner is asked: a silent fallback to "everything" would look like a
        complete answer.
        """

        raw = urlsplit(self.path).query
        if not raw:
            return None
        pairs = parse_qsl(raw, keep_blank_values=True)
        keys = [key for key, _value in pairs]
        unknown = sorted(set(keys) - CONTACT_QUERY_FIELDS)
        if unknown:
            raise PhoneRequestError("UNKNOWN_QUERY:%s" % ",".join(unknown))
        if keys.count("limit") > 1:
            raise PhoneRequestError("QUERY_REPEATED:limit")
        return _limit_value(pairs[0][1] if pairs else None, phone_contact.MAX_CONTACT_LIST)

    def _get_contact(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/contact``: counts, bounded rows and B's read-only session.  Zero writes."""

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        self._send_or_map(service.contact())

    def _get_contact_events(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/contact/events``: the bounded transport rows -- no body text."""

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        limit = self._contact_limit_query()
        self._send_or_map(service.contact_events(limit))

    def _get_contact_event(self, service: phone_service.PhoneService, event_ref: str) -> None:
        """``GET /api/phone/contact/events/<ref>``: one arrival, with its bounded text.

        The detail is the only projection that carries the text, and it says so
        (``body_included`` / ``private_text_included``).  There is no query parameter: an
        arrival's identity is its path, not a client-chosen field.
        """

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        self._send_or_map(service.contact_event(_contact_event_ref(event_ref),
                                                include_body=True))

    def _get_contact_operation(self, service: phone_service.PhoneService, key: str) -> None:
        """``GET /api/phone/contact/operations/<key>``: pure-read reconciliation, same key."""

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        self._send_or_map(service.contact_operation(_operation_key(key)))

    def _post_contact_event(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/contact/events``: deliver **one** local arrival.

        The body is rebuilt field by field from the client's own intent: the kind, its own
        text, an explicit scope (invitations only), the invitation an end closes, and its own
        idempotency key.  Subject / home / state root / channel / path / URL are refused by
        name (409) -- never dropped silently.  ``queued`` is the strongest thing this surface
        may say: the answer carries ``accepted=false``, ``pause_requested=false`` and
        ``activity_started=false`` because *this* layer never accepts for her.
        """

        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        payload = self._read_body(CONTACT_SUBMIT_FIELDS, CONTACT_REFUSED_FIELDS)
        built = _contact_payload(payload)
        key = _operation_key(payload.get("operation_key"))
        built["operation_key"] = key
        result = service.contact_submit(built)
        service.record_operation(actor=self._actor(), action="contact:" + built["event_kind"],
                                 operation_key=key, result=result)
        self._send_or_map(result)

    # -- shared practice (A's shared_practice_service; P forwards user intent only) -------

    def _get_shared_practices(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/shared-practice``: what exists now.  Zero writes, no private text."""

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        self._send_or_map(service.shared_practices())

    def _get_shared_practice_work(self, service: phone_service.PhoneService,
                                  practice_id: str) -> None:
        """``GET .../shared-practice/works/<id>``: material, both sides' turns, work versions."""

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        self._send_or_map(service.shared_practice(_shared_practice_id(practice_id)))

    def _get_shared_practice_operation(self, service: phone_service.PhoneService,
                                       key: str) -> None:
        """``GET .../shared-practice/operations/<key>``: pure-read reconciliation, same key."""

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        self._send_or_map(service.shared_practice_operation(_operation_key(key)))

    def _post_shared_practice_open(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/shared-practice``: the user picks **one** explicitly shared material
        and writes the first turn.

        The material is chosen from the owner's own current source list (an artifact id plus the
        version the user actually saw); it is never a path, a URL, a database or a private text.
        ``title`` and ``text`` are exactly what A's ``open_practice`` requires, so the phone asks
        the user for them instead of inventing them.  The phone writes nothing about her side
        here: opening a practice is the user's request.
        """

        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        payload = self._read_body(SHARED_PRACTICE_OPEN_FIELDS, SHARED_PRACTICE_REFUSED_FIELDS)
        source = _practice_source_id(payload.get("source_artifact_id"))
        version = _practice_text(payload.get("source_version_id"), 200,
                                 "SHARED_PRACTICE_SOURCE_VERSION_INVALID")
        title = _practice_text(payload.get("title"), phone_practice.MAX_TITLE,
                               "SHARED_PRACTICE_TITLE_REQUIRED")
        text = _practice_body(payload.get("text"))
        key = _operation_key(payload.get("operation_key"))
        result = service.shared_practice_open(source_artifact_id=source,
                                             source_version_id=version, title=title, text=text,
                                             operation_key=key)
        service.record_operation(actor=self._actor(), action="shared-practice:open",
                                 operation_key=key, result=result)
        self._send_or_map(result)

    def _post_shared_practice_action(self, service: phone_service.PhoneService,
                                     practice_id: str) -> None:
        """``POST .../shared-practice/works/<id>/actions``: one explicit **user** intent.

        ``action`` is one of ``submit`` / ``modify`` / ``pause`` / ``end``.  Text is the
        *user's own* words; it is never her reply.  ``resume`` / ``reopen`` are hers (A's
        ``ACTOR_WRITES`` gives them to the Life side only), so they are refused by name here
        instead of being forwarded.  Anything that would try to write her side (``subject`` /
        ``contributor`` / ``accept`` / ``outcome`` / ``state``) is refused by name (409), so a
        silently dropped field can never look like her contribution landed.
        """

        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        payload = self._read_body(SHARED_PRACTICE_ACTION_FIELDS, SHARED_PRACTICE_REFUSED_FIELDS)
        raw_action = payload.get("action")
        action = (" ".join(str(raw_action or "").split()).lower())[:24]
        if action in ("resume", "reopen"):
            raise PhoneRequestError("SHARED_PRACTICE_ACTION_UNSUPPORTED")
        if action not in phone_practice.PHONE_ACTIONS:
            raise PhoneRequestError("SHARED_PRACTICE_ACTION_UNSUPPORTED")
        # ``expected_head`` is the CAS head of the artifact; only a direct artifact state change
        # (pause) needs it now.  A submit/end travels through the inbox, so a client that read no
        # head is not refused for it -- and a client that sends one still has it validated.
        head = ""
        if payload.get("expected_head") not in (None, ""):
            head = _practice_text(payload.get("expected_head"), phone_service.MAX_VERSION_ID,
                                  "SHARED_PRACTICE_HEAD_REQUIRED")
        if action == "pause" and not head:
            raise PhoneRequestError("SHARED_PRACTICE_HEAD_REQUIRED")
        key = _operation_key(payload.get("operation_key"))
        text = None
        if action in ("submit", "modify"):
            text = _practice_body(payload.get("text"))
        elif payload.get("text") not in (None, ""):
            # 暂停/结束不带正文：给了就是客户端的 bug，不静默丢掉。
            raise PhoneRequestError("UNKNOWN_FIELD:text")
        reason = None
        if action in ("pause", "end"):
            if payload.get("reason") not in (None, ""):
                reason = _practice_text(payload.get("reason"), 64,
                                        "SHARED_PRACTICE_REASON_INVALID")
        elif payload.get("reason") not in (None, ""):
            raise PhoneRequestError("UNKNOWN_FIELD:reason")
        result = service.shared_practice_act(practice_id=_shared_practice_id(practice_id),
                                            action=action, expected_head=head,
                                            operation_key=key, text=text, reason=reason)
        service.record_operation(actor=self._actor(), action="shared-practice:" + action,
                                 operation_key=key, result=result)
        self._send_or_map(result)

    def _get_state(self, service: phone_service.PhoneService) -> None:
        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        payload = dict(service.state())
        payload["csrf_token"] = str(self.server.csrf_token)
        self._send(200, payload)

    def _get_living(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/living``: the real "now", read-only.

        It reads the resident summary, wake journal tail, coverage ledger and the persisted
        request ledger only.  It starts nothing and calls no model, so a page refresh can never
        turn a historical ACTIVE record into "she is doing this right now".
        """

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        view = phone_living.living_view(
            service.root, service=service,
            supervisor=getattr(self.server, "supervisor", None), live=True)
        self._send(200, {"ok": True, "status": "ok", "data": view})

    def _living_supervisor(self) -> Optional[Any]:
        return getattr(self.server, "supervisor", None)

    def _living_envelope(self, result: Mapping[str, Any], *, ok_status: str) -> dict[str, Any]:
        if result.get("ok"):
            return phone_service.envelope(True, ok_status, dict(result))
        reason = str(result.get("reason") or result.get("reason_code")
                     or "LIVING_CONTROL_REFUSED")
        return phone_service.envelope(False, phone_service.STATUS_REJECTED,
                                      {key: value for key, value in dict(result).items()
                                       if key not in {"ok", "status", "reason"}},
                                      reason)

    def _post_living_window(self, service: phone_service.PhoneService) -> None:
        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        supervisor = self._living_supervisor()
        if supervisor is None:
            self._send(409, _failure("SUPERVISOR_UNAVAILABLE"))
            return
        payload = self._read_body(LIVING_WINDOW_FIELDS)
        result = supervisor.start_window(
            duration_seconds=payload.get("duration_seconds"),
            max_new_requests=payload.get("max_new_requests"))
        service.record_operation(actor=self._actor(), action="living:window",
                                 operation_key="living-window:%d" % int(time.time()),
                                 result=result)
        self._send_or_map(self._living_envelope(result, ok_status="applied"))

    def _post_living_pause(self, service: phone_service.PhoneService) -> None:
        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        supervisor = self._living_supervisor()
        if supervisor is None:
            self._send(409, _failure("SUPERVISOR_UNAVAILABLE"))
            return
        payload = self._read_body(LIVING_PAUSE_FIELDS)
        paused = payload.get("paused")
        if not isinstance(paused, bool):
            raise PhoneRequestError("PAUSED_MUST_BE_BOOLEAN")
        result = supervisor.set_paused(paused)
        service.record_operation(actor=self._actor(),
                                 action="living:pause" if paused else "living:resume",
                                 operation_key="living-pause:%d" % int(time.time()),
                                 result=result)
        self._send_or_map(self._living_envelope(result, ok_status="applied"))

    def _post_living_stop(self, service: phone_service.PhoneService) -> None:
        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        supervisor = self._living_supervisor()
        if supervisor is None:
            self._send(409, _failure("SUPERVISOR_UNAVAILABLE"))
            return
        # Consume a declared body even though nothing is read from it: leaving bytes in the
        # stream would corrupt the next keep-alive request.
        length = self.headers.get("Content-Length")
        if length is not None and str(length).strip().isdigit() and int(str(length)) > 0:
            self._read_body(frozenset())
        result = supervisor.stop_window()
        service.record_operation(actor=self._actor(), action="living:stop",
                                 operation_key="living-stop:%d" % int(time.time()),
                                 result=result)
        self._send_or_map(self._living_envelope(result, ok_status="applied"))

    def _get_world(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/world`` is a pure read: no init, no settlement, no write."""
        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        self._send_or_map(service.world())

    def _get_store(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/store`` is a pure read of C's own shop projection.

        It never delivers, materializes, migrates or pays: the shelf, the opening state and the
        budget the owner reports are exactly what a GET returns.
        """

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        self._send_or_map(service.store())

    def _reading_page_query(self) -> Optional[int]:
        """The one query parameter the material GET accepts: a strict page number.

        Anything else is refused before the owner is asked: an unknown key, a repeated ``page``,
        a blank value, a sign, a leading zero, a float or a boolean word.  A silent fallback to
        page 1 would look like "you are back at the beginning" and hide a client bug.
        """

        raw = urlsplit(self.path).query
        if not raw:
            return None
        pairs = parse_qsl(raw, keep_blank_values=True)
        keys = [key for key, _value in pairs]
        unknown = sorted(set(keys) - READING_QUERY_FIELDS)
        if unknown:
            raise PhoneRequestError("UNKNOWN_QUERY:%s" % ",".join(unknown))
        if keys.count("page") > 1:
            raise PhoneRequestError("QUERY_REPEATED:page")
        value = str(pairs[0][1]) if pairs else ""
        if not re.match(r"^[1-9][0-9]{0,2}$", value):
            raise PhoneRequestError("PAGE_INVALID")
        page = int(value)
        if page > phone_service.MAX_READING_PAGE:
            raise PhoneRequestError("PAGE_OUT_OF_RANGE")
        return page

    def _get_reading_material(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/reading/material?page=N`` — the page's real text, pure read.

        It never records a position, an acquisition or a note, never writes a file and never
        creates a material: the bookmark is only ever moved by an explicit POST.
        """

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        page = self._reading_page_query()
        self._send_or_map(service.reading_material(page))

    # -- mutations -----------------------------------------------------------------------

    def _post_note(self, service: phone_service.PhoneService) -> None:
        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        payload = self._read_body(NOTES_FIELDS)
        title = _clean_text(payload.get("title"), phone_service.MAX_TITLE, "TITLE_INVALID")
        body = _clean_body(payload.get("body"), phone_service.MAX_BODY)
        key = _operation_key(payload.get("operation_key"))
        expected_head = payload.get("expected_head")
        if expected_head is not None and not isinstance(expected_head, str):
            raise PhoneRequestError("EXPECTED_HEAD_INVALID")
        note_id = payload.get("note_id")
        if note_id is not None and not isinstance(note_id, str):
            raise PhoneRequestError("NOTE_ID_INVALID")
        result = service.create_note(title=title, body=body, operation_key=key,
                                     expected_head=expected_head, note_id=note_id)
        service.record_operation(actor=self._actor(), action="note", operation_key=key,
                                 result=result)
        self._send_or_map(result)

    def _post_reference(self, service: phone_service.PhoneService) -> None:
        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        payload = self._read_body(REFERENCE_FIELDS)
        source_id = _clean_text(payload.get("source_id"), phone_service.MAX_SOURCE_ID,
                               "SOURCE_ID_INVALID")
        revision = _clean_text(payload.get("source_revision"),
                              phone_service.MAX_SOURCE_REVISION, "SOURCE_REVISION_INVALID")
        label = _clean_text(payload.get("label"), phone_service.MAX_LABEL, "LABEL_INVALID")
        key = _operation_key(payload.get("operation_key"))
        result = service.save_reference(source_id=source_id, source_revision=revision,
                                        label=label, operation_key=key)
        service.record_operation(actor=self._actor(), action="reference", operation_key=key,
                                 result=result)
        self._send_or_map(result)

    def _post_purchase(self, service: phone_service.PhoneService) -> None:
        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        payload = self._read_body(PURCHASE_FIELDS)
        catalog_item_id = _clean_text(payload.get("catalog_item_id"), 80,
                                      "CATALOG_ITEM_INVALID")
        key = _operation_key(payload.get("operation_key"))
        result = service.purchase(catalog_item_id, key)
        service.record_operation(actor=self._actor(), action="purchase", operation_key=key,
                                 result=result)
        self._send_or_map(result)

    def _post_store_purchase(self, service: phone_service.PhoneService) -> None:
        """One counter purchase: the catalogue row plus the client's own idempotency key.

        P prices nothing and promises no arrival.  ``applied`` means the owner committed the
        purchase (and, for furniture, started its real delivery); whether the piece is already at
        home is a later fact the snapshot tells.
        """

        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        payload = self._read_body(STORE_PURCHASE_FIELDS)
        catalog_item_id = _clean_text(payload.get("catalog_item_id"), 80,
                                      "CATALOG_ITEM_INVALID")
        key = _operation_key(payload.get("operation_key"))
        result = service.store_purchase(catalog_item_id, key)
        service.record_operation(actor=self._actor(), action="store_purchase",
                                 operation_key=key, result=result)
        self._send_or_map(result)

    def _post_use(self, service: phone_service.PhoneService) -> None:
        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        payload = self._read_body(USE_FIELDS)
        item_instance_id = _clean_text(payload.get("item_instance_id"), 128,
                                      "ITEM_INSTANCE_INVALID")
        key = _operation_key(payload.get("operation_key"))
        result = service.use(item_instance_id, key)
        service.record_operation(actor=self._actor(), action="use", operation_key=key,
                                 result=result)
        self._send_or_map(result)

    def _post_world_action(self, service: phone_service.PhoneService) -> None:
        """One explicit world command.  P validates the shape; C owns the outcome."""
        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        payload = self._read_body(WORLD_FIELDS)
        raw_action = payload.get("action")
        action = str(raw_action).strip().upper() if isinstance(raw_action, str) else ""
        if action not in phone_service.WORLD_ACTIONS:
            raise PhoneRequestError("UNKNOWN_WORLD_ACTION")
        params = _world_params(action, payload.get("params"))
        key = _operation_key(payload.get("operation_key"))
        result = service.world_action(action, params, key)
        service.record_operation(actor=self._actor(), action="world:" + action.lower(),
                                 operation_key=key, result=result)
        self._send_or_map(result)

    # -- reading workbench (C's PhoneReading owns every reading fact) ---------------------

    def _post_reading_material(self, service: phone_service.PhoneService) -> None:
        """One explicit operator text, for the artifact this instance is already bound to.

        The client names neither an artifact, nor a path, nor a URL, nor a title: the owner
        decides the new revision and keeps the old text as an older Supply version.
        """

        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        payload = self._read_body(READING_MATERIAL_FIELDS)
        content = _clean_reading_content(payload.get("content"))
        expected_revision = _clean_text(payload.get("expected_revision"),
                                        phone_service.MAX_VERSION_ID,
                                        "EXPECTED_REVISION_INVALID")
        key = _operation_key(payload.get("operation_key"))
        result = service.reading_import(content, expected_revision, key)
        service.record_operation(actor=self._actor(), action="reading_import",
                                 operation_key=key, result=result)
        self._send_or_map(result)

    def _post_reading_position(self, service: phone_service.PhoneService) -> None:
        """One explicit stop: the owner really reads that page, then commits the bookmark.

        ``page`` is the page the operator is on — going *back* to an earlier page is legitimate,
        and the highest page is never used to guess where she stopped.
        """

        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        payload = self._read_body(READING_POSITION_FIELDS)
        page = _reading_page(payload.get("page"))
        source_revision = _clean_text(payload.get("source_revision"),
                                      phone_service.MAX_SOURCE_REVISION,
                                      "SOURCE_REVISION_INVALID")
        expected_head = _clean_text(payload.get("expected_head"), phone_service.MAX_VERSION_ID,
                                    "EXPECTED_HEAD_INVALID")
        key = _operation_key(payload.get("operation_key"))
        result = service.reading_position(page, source_revision, expected_head, key)
        service.record_operation(actor=self._actor(), action="reading_position",
                                 operation_key=key, result=result)
        self._send_or_map(result)

    def _post_reading_annotation(self, service: phone_service.PhoneService) -> None:
        """A private note or an honest "unclear" mark on a page already read (whitelist per kind)."""

        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        payload = self._read_body(READING_ANNOTATION_FIELDS)
        kind = payload.get("kind")
        if not isinstance(kind, str) or kind not in READING_ANNOTATION_KINDS:
            raise PhoneRequestError("ANNOTATION_KIND_INVALID")
        page = _reading_page(payload.get("page"))
        source_revision = _clean_text(payload.get("source_revision"),
                                      phone_service.MAX_SOURCE_REVISION,
                                      "SOURCE_REVISION_INVALID")
        expected_head = _clean_text(payload.get("expected_head"), phone_service.MAX_VERSION_ID,
                                    "EXPECTED_HEAD_INVALID")
        key = _operation_key(payload.get("operation_key"))
        if kind == "note":
            # A note is a memo: it has a title and a body.  Its own ``note`` key does not exist,
            # and an extra one is a client bug, not something to silently drop.
            if "note" in payload:
                raise PhoneRequestError("UNKNOWN_FIELD:note")
            if "title" not in payload:
                raise PhoneRequestError("ANNOTATION_TITLE_REQUIRED")
            if "body" not in payload:
                raise PhoneRequestError("ANNOTATION_BODY_REQUIRED")
            title = _clean_annotation(payload.get("title"),
                                      phone_service.MAX_READING_TITLE_BYTES,
                                      "ANNOTATION_TITLE_INVALID")
            body = _clean_annotation(payload.get("body"),
                                     phone_service.MAX_READING_ANNOTATION_BYTES,
                                     "ANNOTATION_BODY_INVALID")
            result = service.reading_annotate(kind, page, source_revision, expected_head, key,
                                              title=title, body=body)
        else:
            # An "unclear" mark is its own item type: it never carries a title or a body, and it
            # never turns "I did not understand this" into a memo about something else.
            for field in ("title", "body"):
                if field in payload:
                    raise PhoneRequestError("UNKNOWN_FIELD:%s" % field)
            if "note" not in payload:
                raise PhoneRequestError("ANNOTATION_NOTE_REQUIRED")
            note = _clean_annotation(payload.get("note"),
                                     phone_service.MAX_READING_ANNOTATION_BYTES,
                                     "ANNOTATION_NOTE_INVALID")
            result = service.reading_annotate(kind, page, source_revision, expected_head, key,
                                              note=note)
        service.record_operation(actor=self._actor(), action="reading_annotate",
                                 operation_key=key, result=result)
        self._send_or_map(result)

    # -- her private projects (B's owner service decides every fact) ----------------------

    # -- private album (EP-16: one import, real bytes out) --------------------------------

    def _get_album_list(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/album``: guarded pure read of her own imported pictures."""
        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        self._send_or_map(service.album_list())

    def _get_album_detail(self, service: phone_service.PhoneService,
                          artifact_id: str) -> None:
        """``GET /api/phone/album/<id>``: guarded pure read of one picture's own words."""
        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        self._send_or_map(service.album_detail(artifact_id))

    def _get_album_image(self, service: phone_service.PhoneService, artifact_id: str) -> None:
        """``GET /api/phone/album/<id>/image``: the picture's real bytes, never an envelope."""
        guard = self._guard_common()
        if guard is not None:
            # Even a refusal is a shape-checked JSON envelope, never image bytes.
            self._send(*guard)
            return
        result = service.album_image(artifact_id)
        body = result.get("data") if isinstance(result, dict) else None
        content_type = result.get("content_type") if isinstance(result, dict) else None
        if (isinstance(result, dict) and result.get("ok") is True
                and isinstance(body, (bytes, bytearray))
                and content_type in ALBUM_CONTENT_TYPES):
            self._send_image(str(content_type), bytes(body))
            return
        if isinstance(result, dict) and result.get("ok") is True:
            # A positive answer with no usable bytes is not a picture: never send an empty image.
            self._send(502, _failure("ALBUM_ANSWER_INVALID"))
            return
        self._send_or_map(result)

    def _post_album_import(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/album/images``: one explicit user import.

        The body is the user's caption, the declared type and plain base64 bytes.  No path, URL,
        file name, owner, authority or subject is accepted; ``IDENTITY_FIELDS`` refuses those by
        name before the owner is called.
        """
        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        payload = self._read_body(ALBUM_IMPORT_FIELDS, max_bytes=MAX_ALBUM_BODY_BYTES)
        caption = payload.get("caption")
        if not isinstance(caption, str):
            raise PhoneRequestError("CAPTION_REQUIRED")
        caption = caption.strip()
        if len(caption) > MAX_ALBUM_CAPTION:
            raise PhoneRequestError("CAPTION_TOO_LONG")
        if any(ch in caption for ch in ("\x00", "\r")):
            raise PhoneRequestError("CAPTION_INVALID")
        content_type = payload.get("content_type")
        if not isinstance(content_type, str) or content_type not in ALBUM_CONTENT_TYPES:
            raise PhoneRequestError("IMAGE_CONTENT_TYPE_UNSUPPORTED", 415)
        data = payload.get("data")
        if not isinstance(data, str) or not data:
            raise PhoneRequestError("IMAGE_DATA_REQUIRED")
        # 5 MiB decoded is at most ~6.7 MiB of base64; refuse a longer text before the owner sees
        # it (the owner enforces the decoded bound again).
        if len(data) > 7 * 1024 * 1024 + 16:
            raise PhoneRequestError("IMAGE_TOO_LARGE", 413)
        key = _operation_key(payload.get("operation_key"))
        result = service.album_import(caption=caption, content_type=str(content_type),
                                      data=data, operation_key=key)
        service.record_operation(actor=self._actor(), action="album:import",
                                 operation_key=key, result=result)
        self._send_or_map(result)

    def _get_projects(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/projects``: a guarded pure read.  It never creates or migrates one."""
        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        self._send_or_map(service.projects())

    def _get_project(self, service: phone_service.PhoneService, artifact_id: str) -> None:
        """``GET /api/phone/projects/<id>``: her text, the owner's head and the history it reports."""
        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        self._send_or_map(service.project(_project_id(artifact_id)))

    def _post_project_create(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/projects``: one explicit new project (title + first text).

        The client supplies the title and the text itself -- there is no "create the first
        proposal" path here, because a proposal is a choice and the choice has to be made.
        """
        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        payload = self._read_body(PROJECT_CREATE_FIELDS)
        title = _clean_text(payload.get("title"), phone_service.MAX_PROJECT_TITLE,
                            "PROJECT_TITLE_INVALID")
        content = _clean_project_content(payload.get("content"))
        key = _operation_key(payload.get("operation_key"))
        result = service.create_project(title=title, content=content, operation_key=key)
        service.record_operation(actor=self._actor(), action="project_create",
                                 operation_key=key, result=result)
        self._send_or_map(result)

    def _post_project_commit(self, service: phone_service.PhoneService,
                             artifact_id: str) -> None:
        """``POST /api/phone/projects/<id>/commits``: the next version, under the head you read."""
        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        payload = self._read_body(PROJECT_COMMIT_FIELDS)
        content = _clean_project_content(payload.get("content"))
        expected_head = _clean_text(payload.get("expected_head"),
                                    phone_service.MAX_VERSION_ID, "EXPECTED_HEAD_INVALID")
        key = _operation_key(payload.get("operation_key"))
        result = service.commit_project(artifact_id=_project_id(artifact_id), content=content,
                                        expected_head=expected_head, operation_key=key)
        service.record_operation(actor=self._actor(), action="project_commit",
                                 operation_key=key, result=result)
        self._send_or_map(result)

    def _post_project_close(self, service: phone_service.PhoneService,
                            artifact_id: str) -> None:
        """``POST /api/phone/projects/<id>/close``: archive it (the owner's verb, never a delete)."""
        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        payload = self._read_body(PROJECT_CLOSE_FIELDS)
        expected = payload.get("expected_revision")
        if isinstance(expected, bool) or not isinstance(expected, int) or expected < 1:
            raise PhoneRequestError("EXPECTED_REVISION_INVALID")
        key = _operation_key(payload.get("operation_key"))
        result = service.close_project(artifact_id=_project_id(artifact_id),
                                       expected_revision=int(expected), operation_key=key)
        service.record_operation(actor=self._actor(), action="project_close",
                                 operation_key=key, result=result)
        self._send_or_map(result)

    # -- v0.4 work-revisit: old versions, compare, sourced new work -----------------------

    def _project_versions_query(self) -> tuple[Optional[int], Optional[int]]:
        """``?limit=N&before=M`` for one history page: two strict keys, each at most once."""
        raw = urlsplit(self.path).query
        if not raw:
            return None, None
        pairs = parse_qsl(raw, keep_blank_values=True)
        seen: dict[str, str] = {}
        for key, value in pairs:
            if key not in PROJECT_VERSIONS_QUERY_FIELDS:
                raise PhoneRequestError("UNKNOWN_QUERY:%s" % key)
            if key in seen:
                raise PhoneRequestError("QUERY_REPEATED:%s" % key)
            seen[key] = value
        limit = _limit_value(seen.get("limit"), phone_service.MAX_PROJECT_HISTORY_LIMIT)
        before: Optional[int] = None
        if "before" in seen:
            text = str(seen.get("before") or "")
            if not re.match(r"^[1-9][0-9]{0,5}$", text):
                raise PhoneRequestError("CURSOR_INVALID")
            before = int(text)
        return limit, before

    def _get_project_versions(self, service: phone_service.PhoneService,
                              artifact_id: str) -> None:
        """``GET /api/phone/projects/<id>/versions``: a guarded pure read of version metadata."""
        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        limit, before = self._project_versions_query()
        kwargs: dict[str, Any] = {}
        if limit is not None:
            kwargs["limit"] = limit
        if before is not None:
            kwargs["before_version_number"] = before
        self._send_or_map(service.project_versions(_project_id(artifact_id), **kwargs))

    def _get_project_version(self, service: phone_service.PhoneService, artifact_id: str,
                             version_id: str) -> None:
        """``GET .../versions/<version_id>``: that old text exactly as the owner stored it."""
        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        self._send_or_map(service.project_version(_project_id(artifact_id),
                                                  _version_path_id(version_id)))

    def _get_project_version_compare(self, service: phone_service.PhoneService,
                                     artifact_id: str, version_id: str) -> None:
        """``GET .../versions/<version_id>/compare``: that version against today's head."""
        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        self._send_or_map(service.project_compare(_project_id(artifact_id),
                                                  _version_path_id(version_id)))

    def _get_project_provenance(self, service: phone_service.PhoneService,
                                artifact_id: str) -> None:
        """``GET .../provenance``: where a sourced work came from (its immutable first version)."""
        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        self._send_or_map(service.project_provenance(_project_id(artifact_id)))

    def _post_project_derived(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/projects/derived``: one **new** work with a recorded source.

        The body names the source artifact and the *pinned* version, the purpose (quote / adapt /
        reuse) and the new work's own title and text.  Nothing here is derived by the phone: the
        service re-reads that version from the owner before writing the provenance.
        """
        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        payload = self._read_body(PROJECT_DERIVE_FIELDS)
        title = _clean_text(payload.get("title"), phone_service.MAX_PROJECT_TITLE,
                            "PROJECT_TITLE_INVALID")
        content = _clean_project_content(payload.get("content"))
        source_artifact = _owner_path_id(payload.get("source_artifact_id"),
                                         "SOURCE_ARTIFACT_ID_INVALID")
        source_version = _version_path_id(payload.get("source_version_id"))
        purpose = _clean_text(payload.get("purpose"), 16, "SOURCE_PURPOSE_INVALID").lower()
        if purpose not in phone_service.PROJECT_SOURCE_PURPOSES:
            raise PhoneRequestError("SOURCE_PURPOSE_INVALID")
        note = _clean_optional_text(payload.get("note"), phone_service.MAX_SOURCE_NOTE,
                                    "SOURCE_NOTE_INVALID")
        key = _operation_key(payload.get("operation_key"))
        result = service.create_derived_project(
            title=title, content=content, source_artifact_id=source_artifact,
            source_version_id=source_version, purpose=purpose, note=note, operation_key=key)
        service.record_operation(actor=self._actor(), action="project_derive",
                                 operation_key=key, result=result)
        self._send_or_map(result)

    # -- material library / research workbench (B's service owns every fact) --------------

    def _material_query(self) -> tuple[str, Optional[int]]:
        """``?query=...&limit=N`` for the material list: two keys, each at most once."""
        raw = urlsplit(self.path).query
        if not raw:
            return "", None
        pairs = parse_qsl(raw, keep_blank_values=True)
        keys = [key for key, _value in pairs]
        unknown = sorted(set(keys) - MATERIAL_QUERY_FIELDS)
        if unknown:
            raise PhoneRequestError("UNKNOWN_QUERY:%s" % ",".join(unknown))
        seen: dict[str, str] = {}
        for key, value in pairs:
            if key in seen:
                raise PhoneRequestError("QUERY_REPEATED:%s" % key)
            seen[key] = value
        query = seen.get("query", "")
        if len(query) > phone_service.MAX_MATERIAL_QUERY:
            raise PhoneRequestError("QUERY_TOO_LONG")
        if any(ch in query for ch in ("\x00", "\r", "\n")):
            raise PhoneRequestError("QUERY_INVALID")
        return query.strip(), _limit_value(seen.get("limit"), phone_service.MAX_MATERIAL_LIMIT)

    def _research_limit_query(self) -> Optional[int]:
        """``?limit=N`` for the research list: one key, at most once."""
        raw = urlsplit(self.path).query
        if not raw:
            return None
        pairs = parse_qsl(raw, keep_blank_values=True)
        keys = [key for key, _value in pairs]
        unknown = sorted(set(keys) - RESEARCH_QUERY_FIELDS)
        if unknown:
            raise PhoneRequestError("UNKNOWN_QUERY:%s" % ",".join(unknown))
        if keys.count("limit") > 1:
            raise PhoneRequestError("QUERY_REPEATED:limit")
        return _limit_value(pairs[0][1] if pairs else None,
                            phone_service.MAX_RESEARCH_LIMIT)

    def _get_materials(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/materials``: the library list, a guarded pure read."""
        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        query, limit = self._material_query()
        self._send_or_map(service.materials(query, limit))

    def _get_material(self, service: phone_service.PhoneService, artifact_id: str) -> None:
        """``GET /api/phone/materials/<id>``: the real text plus the version it belongs to."""
        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        self._send_or_map(service.material(_material_id(artifact_id)))

    def _get_research(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/research``: her research works (bounded, no body)."""
        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        self._send_or_map(service.research_list(self._research_limit_query()))

    def _get_research_item(self, service: phone_service.PhoneService,
                           artifact_id: str) -> None:
        """``GET /api/phone/research/<id>``: content, citations and each source's version check."""
        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        self._send_or_map(service.research_read(_research_id(artifact_id)))

    def _post_material_import(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/materials``: one explicitly supplied text, one new artifact.

        Nothing is fetched: the text, the declared ``source_kind`` and the declared ``origin`` are
        the operator's own.  A replay of the same key answers ``already_applied`` with the same
        artifact instead of importing a second copy.
        """
        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        payload = self._read_body(MATERIAL_IMPORT_FIELDS)
        title = _clean_text(payload.get("title"), phone_service.MAX_MATERIAL_TITLE,
                            "MATERIAL_TITLE_INVALID")
        text = _clean_material_text(payload.get("text"))
        source_kind = _clean_source_kind(payload.get("source_kind"))
        origin = _clean_declaration(payload.get("origin"), phone_service.MAX_MATERIAL_ORIGIN,
                                    "ORIGIN_INVALID")
        key = _operation_key(payload.get("operation_key"))
        result = service.import_material(title=title, text=text, source_kind=source_kind,
                                         origin=origin, operation_key=key)
        service.record_operation(actor=self._actor(), action="material_import",
                                 operation_key=key, result=result)
        self._send_or_map(result)

    def _post_research_create(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/research``: a question plus explicitly chosen material sources."""
        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        payload = self._read_body(RESEARCH_CREATE_FIELDS)
        question = _clean_text(payload.get("question"), phone_service.MAX_RESEARCH_QUESTION,
                               "RESEARCH_QUESTION_INVALID")
        sources = _research_sources(payload.get("source_refs"))
        title = None
        if payload.get("title") is not None:
            title = _clean_text(payload.get("title"), phone_service.MAX_MATERIAL_TITLE,
                                "RESEARCH_TITLE_INVALID")
        scope = _clean_optional_text(payload.get("scope"), phone_service.MAX_RESEARCH_SCOPE,
                                     "RESEARCH_SCOPE_INVALID")
        uncertainty = _clean_optional_text(payload.get("uncertainty"),
                                           phone_service.MAX_RESEARCH_UNCERTAINTY,
                                           "RESEARCH_UNCERTAINTY_INVALID")
        key = _operation_key(payload.get("operation_key"))
        result = service.research_create(question=question, source_refs=sources, title=title,
                                         scope=scope, uncertainty=uncertainty,
                                         operation_key=key)
        service.record_operation(actor=self._actor(), action="research_create",
                                 operation_key=key, result=result)
        self._send_or_map(result)


    def _post_research_update(self, service: phone_service.PhoneService,
                              artifact_id: str) -> None:
        """``POST /api/phone/research/<id>/updates``: the next version, under the head you read.

        The client sends the body it wants; the marker (question + citations) is rebuilt by the
        service from the owner's own copy, so a writer can neither drop nor rewrite it.
        """
        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        payload = self._read_body(RESEARCH_UPDATE_FIELDS)
        content = _clean_research_content(payload.get("content"))
        expected_head = _clean_text(payload.get("expected_head"),
                                    phone_service.MAX_VERSION_ID, "EXPECTED_HEAD_INVALID")
        scope = None
        if payload.get("scope") is not None:
            scope = _clean_optional_text(payload.get("scope"),
                                         phone_service.MAX_RESEARCH_SCOPE,
                                         "RESEARCH_SCOPE_INVALID")
        uncertainty = None
        if payload.get("uncertainty") is not None:
            uncertainty = _clean_optional_text(payload.get("uncertainty"),
                                               phone_service.MAX_RESEARCH_UNCERTAINTY,
                                               "RESEARCH_UNCERTAINTY_INVALID")
        key = _operation_key(payload.get("operation_key"))
        result = service.research_update(artifact_id=_research_id(artifact_id), content=content,
                                         expected_head=expected_head, operation_key=key,
                                         scope=scope, uncertainty=uncertainty)
        service.record_operation(actor=self._actor(), action="research_update",
                                 operation_key=key, result=result)
        self._send_or_map(result)

    def _post_research_close(self, service: phone_service.PhoneService,
                             artifact_id: str) -> None:
        """``POST /api/phone/research/<id>/close``: archive it (never delete)."""
        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        payload = self._read_body(RESEARCH_CLOSE_FIELDS)
        expected = payload.get("expected_revision")
        if isinstance(expected, bool) or not isinstance(expected, int) or expected < 1:
            raise PhoneRequestError("EXPECTED_REVISION_INVALID")
        key = _operation_key(payload.get("operation_key"))
        result = service.research_close(artifact_id=_research_id(artifact_id),
                                        expected_revision=int(expected), operation_key=key)
        service.record_operation(actor=self._actor(), action="research_close",
                                 operation_key=key, result=result)
        self._send_or_map(result)

    # -- Memory 与个人实践（A 的 Memory 服务、B 的实践服务；P 只转发，不重算） ------------

    def _memory_query(self) -> tuple[str, Optional[int]]:
        """``?query=...&limit=N`` for the experience search: two keys, each at most once."""
        raw = urlsplit(self.path).query
        if not raw:
            return "", None
        pairs = parse_qsl(raw, keep_blank_values=True)
        keys = [key for key, _value in pairs]
        unknown = sorted(set(keys) - MEMORY_QUERY_FIELDS)
        if unknown:
            raise PhoneRequestError("UNKNOWN_QUERY:%s" % ",".join(unknown))
        seen: dict[str, str] = {}
        for key, value in pairs:
            if key in seen:
                raise PhoneRequestError("QUERY_REPEATED:%s" % key)
            seen[key] = value
        query = seen.get("query", "")
        if len(query) > phone_service.MAX_MEMORY_QUERY:
            raise PhoneRequestError("QUERY_TOO_LONG")
        if any(ch in query for ch in ("\x00", "\r", "\n")):
            raise PhoneRequestError("QUERY_INVALID")
        return query.strip(), _limit_value(seen.get("limit"),
                                          phone_service.MAX_MEMORY_EXPERIENCES)

    def _practice_query(self) -> tuple[str, Optional[int]]:
        """``?query=...&limit=N`` for the method list: two keys, each at most once."""
        raw = urlsplit(self.path).query
        if not raw:
            return "", None
        pairs = parse_qsl(raw, keep_blank_values=True)
        keys = [key for key, _value in pairs]
        unknown = sorted(set(keys) - PRACTICE_QUERY_FIELDS)
        if unknown:
            raise PhoneRequestError("UNKNOWN_QUERY:%s" % ",".join(unknown))
        seen: dict[str, str] = {}
        for key, value in pairs:
            if key in seen:
                raise PhoneRequestError("QUERY_REPEATED:%s" % key)
            seen[key] = value
        query = seen.get("query", "")
        if len(query) > phone_service.MAX_MEMORY_QUERY:
            raise PhoneRequestError("QUERY_TOO_LONG")
        if any(ch in query for ch in ("\x00", "\r", "\n")):
            raise PhoneRequestError("QUERY_INVALID")
        return query.strip(), _limit_value(seen.get("limit"),
                                           phone_service.PRACTICE_LIST_LIMIT)

    def _get_memory(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/memory``: A's own binding status.  A pure read; no store is built."""
        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        self._send_or_map(service.memory())

    def _get_memory_experiences(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/memory/experiences``: what A can still recall, related to a query."""
        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        query, limit = self._memory_query()
        self._send_or_map(service.memory_experiences(query=query, limit=limit))

    def _get_memory_experience(self, service: phone_service.PhoneService,
                               experience_id: str) -> None:
        """``GET /api/phone/memory/experiences/<id>``: one experience, A's governance deciding."""
        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        self._send_or_map(service.memory_experience(_experience_id(experience_id)))

    def _post_memory_enable(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/memory/enable``: the one explicit enable.  Never a restart."""
        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        payload = self._read_body(MEMORY_ENABLE_FIELDS)
        key = _operation_key(payload.get("operation_key"))
        result = service.enable_memory(operation_key=key)
        service.record_operation(actor=self._actor(), action="memory_enable",
                                 operation_key=key, result=result)
        self._send_or_map(result)

    def _get_practice(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/practice``: reviews + methods + Memory status, one guarded read."""
        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        self._send_or_map(service.practice())

    def _get_practice_methods(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/practice/methods``: the method drafts (bounded, no bodies)."""
        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        query, limit = self._practice_query()
        self._send_or_map(service.practice_methods(query=query, limit=limit))

    def _get_practice_review(self, service: phone_service.PhoneService,
                             artifact_id: str) -> None:
        """``GET /api/phone/practice/reviews/<id>``: body, head and every evidence check."""
        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        self._send_or_map(service.practice_review(_practice_id(artifact_id)))

    def _get_practice_method(self, service: phone_service.PhoneService,
                             artifact_id: str) -> None:
        """``GET /api/phone/practice/methods/<id>``: instruction, scope, state, evidence."""
        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        self._send_or_map(service.practice_method(_practice_id(artifact_id)))

    def _post_practice_review_create(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/practice/reviews``: one review over experiences she actually had."""
        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        payload = self._read_body(PRACTICE_REVIEW_CREATE_FIELDS)
        question = _clean_text(payload.get("question"), phone_service.MAX_PRACTICE_QUESTION,
                               "PRACTICE_QUESTION_INVALID")
        title = _clean_optional_text(payload.get("title"), phone_service.MAX_PRACTICE_TITLE,
                                     "PRACTICE_TITLE_INVALID")
        ids = _ref_list(payload.get("experience_ids"), phone_service.MAX_PRACTICE_REFS,
                        "EXPERIENCE_IDS_INVALID")
        key = _operation_key(payload.get("operation_key"))
        result = service.create_practice_review(question=question, title=title,
                                                experience_ids=ids, operation_key=key)
        service.record_operation(actor=self._actor(), action="practice_review_create",
                                 operation_key=key, result=result)
        self._send_or_map(result)

    def _post_practice_document(self, service: phone_service.PhoneService,
                                artifact_id: str) -> None:
        """``POST .../documents``: the next body of one review/method, under the head you read."""
        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        payload = self._read_body(PRACTICE_DOCUMENT_FIELDS)
        content = _clean_practice_content(payload.get("content"))
        expected_head = _clean_text(payload.get("expected_head"),
                                    phone_service.MAX_VERSION_ID, "EXPECTED_HEAD_INVALID")
        key = _operation_key(payload.get("operation_key"))
        result = service.update_practice_document(artifact_id=_practice_id(artifact_id),
                                                  content=content,
                                                  expected_head=expected_head,
                                                  operation_key=key)
        service.record_operation(actor=self._actor(), action="practice_document",
                                 operation_key=key, result=result)
        self._send_or_map(result)

    def _post_practice_method_create(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/practice/methods``: one TENTATIVE method draft (never a Skill)."""
        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        payload = self._read_body(PRACTICE_METHOD_CREATE_FIELDS)
        title = _clean_text(payload.get("title"), phone_service.MAX_PRACTICE_TITLE,
                            "PRACTICE_TITLE_INVALID")
        instruction = _clean_text(payload.get("instruction"),
                                  phone_service.MAX_PRACTICE_INSTRUCTION,
                                  "PRACTICE_INSTRUCTION_INVALID")
        scope = _clean_optional_text(payload.get("scope"), phone_service.MAX_PRACTICE_SCOPE,
                                     "PRACTICE_SCOPE_INVALID")
        uncertainty = _clean_optional_text(payload.get("uncertainty"),
                                           phone_service.MAX_PRACTICE_UNCERTAINTY,
                                           "PRACTICE_UNCERTAINTY_INVALID")
        ids = _ref_list(payload.get("experience_ids"), phone_service.MAX_PRACTICE_REFS,
                        "EXPERIENCE_IDS_INVALID")
        refs = _ref_list(payload.get("review_refs"), phone_service.MAX_PRACTICE_REFS,
                         "REVIEW_REFS_INVALID")
        key = _operation_key(payload.get("operation_key"))
        result = service.create_practice_method(title=title, instruction=instruction,
                                                scope=scope, uncertainty=uncertainty,
                                                experience_ids=ids, review_refs=refs,
                                                operation_key=key)
        service.record_operation(actor=self._actor(), action="practice_method_create",
                                 operation_key=key, result=result)
        self._send_or_map(result)

    def _post_practice_trial(self, service: phone_service.PhoneService,
                             artifact_id: str) -> None:
        """``POST .../trials``: one evaluation.  HELPED/NOT_HELPED need real evidence."""
        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        payload = self._read_body(PRACTICE_TRIAL_FIELDS)
        outcome = _practice_outcome(payload.get("outcome"))
        detail = _clean_optional_text(payload.get("detail"),
                                      phone_service.MAX_PRACTICE_TRIAL_DETAIL,
                                      "PRACTICE_TRIAL_DETAIL_INVALID")
        ids = _ref_list(payload.get("experience_ids"), phone_service.MAX_PRACTICE_REFS,
                        "EXPERIENCE_IDS_INVALID")
        expected_head = _clean_text(payload.get("expected_head"),
                                    phone_service.MAX_VERSION_ID, "EXPECTED_HEAD_INVALID")
        key = _operation_key(payload.get("operation_key"))
        result = service.record_practice_trial(artifact_id=_practice_id(artifact_id),
                                               outcome=outcome, detail=detail,
                                               experience_ids=ids,
                                               expected_head=expected_head,
                                               operation_key=key)
        service.record_operation(actor=self._actor(), action="practice_trial",
                                 operation_key=key, result=result)
        self._send_or_map(result)

    def _post_practice_revision(self, service: phone_service.PhoneService,
                                artifact_id: str) -> None:
        """``POST .../revisions``: a revision keeps the old version and returns to TENTATIVE."""
        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        payload = self._read_body(PRACTICE_REVISE_FIELDS)
        instruction = _clean_text(payload.get("instruction"),
                                  phone_service.MAX_PRACTICE_INSTRUCTION,
                                  "PRACTICE_INSTRUCTION_INVALID")
        scope = _clean_optional_text(payload.get("scope"), phone_service.MAX_PRACTICE_SCOPE,
                                     "PRACTICE_SCOPE_INVALID")
        uncertainty = _clean_optional_text(payload.get("uncertainty"),
                                           phone_service.MAX_PRACTICE_UNCERTAINTY,
                                           "PRACTICE_UNCERTAINTY_INVALID")
        expected_head = _clean_text(payload.get("expected_head"),
                                    phone_service.MAX_VERSION_ID, "EXPECTED_HEAD_INVALID")
        key = _operation_key(payload.get("operation_key"))
        result = service.revise_practice_method(artifact_id=_practice_id(artifact_id),
                                                instruction=instruction, scope=scope,
                                                uncertainty=uncertainty,
                                                expected_head=expected_head,
                                                operation_key=key)
        service.record_operation(actor=self._actor(), action="practice_revise",
                                 operation_key=key, result=result)
        self._send_or_map(result)

    def _post_practice_retire(self, service: phone_service.PhoneService,
                              artifact_id: str) -> None:
        """``POST .../retire``: a logical retirement.  Nothing is deleted, no version is lost."""
        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        payload = self._read_body(PRACTICE_RETIRE_FIELDS)
        expected_head = _clean_text(payload.get("expected_head"),
                                    phone_service.MAX_VERSION_ID, "EXPECTED_HEAD_INVALID")
        key = _operation_key(payload.get("operation_key"))
        result = service.retire_practice_method(artifact_id=_practice_id(artifact_id),
                                                expected_head=expected_head,
                                                operation_key=key)
        service.record_operation(actor=self._actor(), action="practice_retire",
                                 operation_key=key, result=result)
        self._send_or_map(result)

def _owner_path_id(value: Any, reason: str) -> str:
    """One owner artifact id exactly as the owner spells it (``artifact:<hex>``): no rewriting."""
    if not isinstance(value, str):
        raise PhoneRequestError(reason)
    text = value.strip()
    if not text or len(text) > phone_service.MAX_PROJECT_ID:
        raise PhoneRequestError(reason)
    if any(ch in text for ch in ("\x00", "\r", "\n", "/", "?")):
        raise PhoneRequestError(reason)
    return text


def _material_id(value: Any) -> str:
    return _owner_path_id(value, "MATERIAL_ID_INVALID")


def _research_id(value: Any) -> str:
    return _owner_path_id(value, "RESEARCH_ID_INVALID")


def _version_path_id(value: Any) -> str:
    """One owner version id (``artifact-version:<hex>``) exactly as the owner spells it."""
    if not isinstance(value, str):
        raise PhoneRequestError("VERSION_ID_INVALID")
    text = value.strip()
    if not text or len(text) > phone_service.MAX_VERSION_ID:
        raise PhoneRequestError("VERSION_ID_INVALID")
    if any(ch in text for ch in ("\x00", "\r", "\n", "/", "?")):
        raise PhoneRequestError("VERSION_ID_INVALID")
    return text


def _limit_value(value: Any, maximum: int) -> Optional[int]:
    """One strict positive ``limit`` query value: no signs, no floats, no booleans."""
    if value is None:
        return None
    text = str(value)
    if not re.match(r"^[1-9][0-9]{0,2}$", text):
        raise PhoneRequestError("LIMIT_INVALID")
    limit = int(text)
    if limit > int(maximum):
        raise PhoneRequestError("LIMIT_OUT_OF_RANGE")
    return limit


def _clean_material_text(value: Any) -> str:
    """The imported text: 1..19000 characters, no NUL, newlines normalised.  Nothing else."""
    if not isinstance(value, str):
        raise PhoneRequestError("MATERIAL_TEXT_INVALID")
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip() or "\x00" in text:
        raise PhoneRequestError("MATERIAL_TEXT_INVALID")
    if len(text) > phone_service.MAX_MATERIAL_TEXT:
        raise PhoneRequestError("MATERIAL_TEXT_INVALID")
    return text


def _clean_source_kind(value: Any) -> str:
    """Only the two declared kinds: the operator's own text, or explicitly fiction."""
    if not isinstance(value, str):
        raise PhoneRequestError("SOURCE_KIND_INVALID")
    text = value.strip().lower()
    if text not in phone_service.MATERIAL_SOURCE_KINDS:
        raise PhoneRequestError("SOURCE_KIND_INVALID")
    return text


def _clean_declaration(value: Any, maximum: int, reason: str) -> str:
    """A declared origin: one non-blank line.  A declaration, never a verified fact."""
    if not isinstance(value, str):
        raise PhoneRequestError(reason)
    text = value.strip()
    if not text or len(text) > int(maximum):
        raise PhoneRequestError(reason)
    if any(ch in text for ch in ("\x00", "\r", "\n")):
        raise PhoneRequestError(reason)
    return text


def _clean_optional_text(value: Any, maximum: int, reason: str) -> str:
    """An optional bounded field (scope / uncertainty): empty is a real value, NUL is not."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise PhoneRequestError(reason)
    text = value.strip()
    if "\x00" in text or len(text) > int(maximum):
        raise PhoneRequestError(reason)
    return text


def _clean_research_content(value: Any) -> str:
    """The body the operator wants to commit: non-blank, bounded, no NUL."""
    if not isinstance(value, str):
        raise PhoneRequestError("RESEARCH_CONTENT_INVALID")
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip() or "\x00" in text:
        raise PhoneRequestError("RESEARCH_CONTENT_INVALID")
    if len(text) > phone_service.MAX_RESEARCH_CONTENT:
        raise PhoneRequestError("RESEARCH_CONTENT_INVALID")
    return text


def _research_sources(value: Any) -> list[dict[str, str]]:
    """The material references the operator chose: objects with exactly two string fields.

    This is the only nested place an artifact id may appear.  P checks the *shape*; whether each
    reference is really a readable material with that revision is the service's check against the
    owner, and its answer (CURRENT / STALE / UNAVAILABLE) is the truth.
    """
    if not isinstance(value, list):
        raise PhoneRequestError("SOURCE_REFS_INVALID")
    if not value:
        raise PhoneRequestError("SOURCE_REFS_REQUIRED")
    if len(value) > phone_service.MAX_RESEARCH_SOURCES:
        raise PhoneRequestError("SOURCE_REFS_TOO_MANY")
    rows: list[dict[str, str]] = []
    seen: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            raise PhoneRequestError("SOURCE_REFS_INVALID")
        unknown = sorted(set(item) - RESEARCH_SOURCE_FIELDS)
        if unknown:
            raise PhoneRequestError("UNKNOWN_FIELD:source_refs.%s" % ",".join(unknown))
        artifact_id = _owner_path_id(item.get("artifact_id"), "SOURCE_REFS_INVALID")
        revision = item.get("source_revision")
        if revision is None:
            revision_text = ""
        elif isinstance(revision, str):
            revision_text = revision.strip()
        else:
            raise PhoneRequestError("SOURCE_REFS_INVALID")
        if len(revision_text) > phone_service.MAX_SOURCE_REVISION:
            raise PhoneRequestError("SOURCE_REFS_INVALID")
        if artifact_id in seen:
            continue
        seen.append(artifact_id)
        rows.append({"artifact_id": artifact_id, "source_revision": revision_text})
    if not rows:
        raise PhoneRequestError("SOURCE_REFS_INVALID")
    return rows


def _project_id(value: Any) -> str:
    """One project id exactly as the owner spells it (``artifact:<hex>``): no rewriting."""
    if not isinstance(value, str):
        raise PhoneRequestError("PROJECT_ID_INVALID")
    text = value.strip()
    if not text or len(text) > phone_service.MAX_PROJECT_ID:
        raise PhoneRequestError("PROJECT_ID_INVALID")
    if any(ch in text for ch in ("\x00", "\r", "\n", "/", "?")):
        raise PhoneRequestError("PROJECT_ID_INVALID")
    return text


def _clean_project_content(value: Any) -> str:
    """The project's text: non-blank, no NUL, within P's declared bound for one version."""
    if not isinstance(value, str):
        raise PhoneRequestError("PROJECT_CONTENT_INVALID")
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip() or "\x00" in text:
        raise PhoneRequestError("PROJECT_CONTENT_INVALID")
    if len(text) > phone_service.MAX_PROJECT_CONTENT:
        raise PhoneRequestError("PROJECT_CONTENT_INVALID")
    return text

# ---------------------------------------------------------------------------------------------
# share request values (shape only; B's LifeSharingService owns every semantic)
# ---------------------------------------------------------------------------------------------


def _share_id(value: Any) -> str:
    """One share id exactly as the owner spells it (``artifact:<hex>``): matched, not rewritten."""

    return _owner_path_id(value, "SHARE_ID_INVALID")


def _share_source_id(value: Any) -> str:
    """The source artifact the client picked from ``GET /shares/sources``: an owner id, no path."""

    if not isinstance(value, str):
        raise PhoneRequestError("SOURCE_ID_INVALID")
    text = value.strip()
    if not text or len(text) > phone_sharing.MAX_SOURCE_ID:
        raise PhoneRequestError("SOURCE_ID_INVALID")
    # ``artifact:<hex>`` 是 owner 自己的协议串：只有一个冒号，前缀必须是 ``artifact``。
    # 裸十六进制 display ref 也接受（``PrivateProjectService`` 同样两种都认）。路径、URL、
    # 数据库、邮箱一律在这里挡住 —— 它们不是「她的某件作品」。
    forbidden = ("\x00", "\r", "\n", "/", "\\", "?", "@", " ", "\t")
    if any(ch in text for ch in forbidden):
        raise PhoneRequestError("SOURCE_ID_INVALID")
    if ":" in text:
        prefix, _, rest = text.partition(":")
        if prefix != "artifact" or ":" in rest or not rest.strip():
            raise PhoneRequestError("SOURCE_ID_INVALID")
        return text
    return text


def _share_text(value: Any) -> str:
    """The explicit share text: 1..MAX_SHARE_TEXT_CHARS, no NUL, newlines normalised."""

    if not isinstance(value, str):
        raise PhoneRequestError("SHARE_TEXT_INVALID")
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip() or "\x00" in text:
        raise PhoneRequestError("SHARE_TEXT_INVALID")
    if len(text) > phone_sharing.MAX_SHARE_TEXT_CHARS:
        raise PhoneRequestError("SHARE_TEXT_INVALID")
    return text


def _excerpt_chars(value: Any) -> int:
    """An explicit excerpt length: a strict integer inside B's own bound."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise PhoneRequestError("EXCERPT_CHARS_INVALID")
    if value < 1 or value > phone_sharing.MAX_EXCERPT_CHARS:
        raise PhoneRequestError("EXCERPT_CHARS_INVALID")
    return int(value)


def _share_title(value: Any) -> Optional[str]:
    """An optional one-line title for the draft (the owner falls back to the source title)."""

    if value is None:
        return None
    text = _clean_text(value, phone_sharing.MAX_TITLE_CHARS, "SHARE_TITLE_INVALID")
    return text


def _expires_after_s(value: Any) -> Optional[int]:
    """An optional draft expiry window, inside B's own MIN/MAX."""

    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise PhoneRequestError("EXPIRES_AFTER_INVALID")
    if value < phone_sharing.MIN_EXPIRY_S or value > phone_sharing.MAX_EXPIRY_S:
        raise PhoneRequestError("EXPIRES_AFTER_INVALID")
    return int(value)


def _share_action(value: Any) -> str:
    """Only the three actions P may ask for: ``send_local`` / ``defer`` / ``drop``."""

    if not isinstance(value, str):
        raise PhoneRequestError("SHARE_ACTION_INVALID")
    text = value.strip()
    if text not in SHARE_ACTIONS:
        raise PhoneRequestError("SHARE_ACTION_INVALID")
    return text


def _share_optional_line(value: Any, maximum: int, reason: str) -> str:
    """An optional one-line field (a defer condition, an opportunity ref, a reason)."""

    if value is None:
        return ""
    if not isinstance(value, str):
        raise PhoneRequestError(reason)
    text = " ".join(value.split())
    if len(text) > int(maximum) or "\x00" in text:
        raise PhoneRequestError(reason)
    return text


#: One id inside a nested reference list: the owner's own protocol spelling, never a path.
_REF_ID_RE = re.compile(r"^[A-Za-z0-9._:\-]{1,200}$")


def _practice_id(value: Any) -> str:
    """One review/method id exactly as the owner spells it: no rewriting, no prefixing."""
    if not isinstance(value, str):
        raise PhoneRequestError("PRACTICE_ID_INVALID")
    text = value.strip()
    if not text or len(text) > phone_service.MAX_PROJECT_ID:
        raise PhoneRequestError("PRACTICE_ID_INVALID")
    if any(ch in text for ch in ("\x00", "\r", "\n", "/", "?")):
        raise PhoneRequestError("PRACTICE_ID_INVALID")
    return text


def _experience_id(value: Any) -> str:
    """One Memory experience id, matched as A spells it."""
    if not isinstance(value, str):
        raise PhoneRequestError("EXPERIENCE_ID_INVALID")
    text = value.strip()
    if not text or len(text) > 200 or not _REF_ID_RE.match(text):
        raise PhoneRequestError("EXPERIENCE_ID_INVALID")
    return text


def _ref_list(value: Any, maximum: int, reason: str) -> list[str]:
    """A nested list of owner ids (experiences / reviews): strings only, bounded, de-duplicated.

    This is the only nested place an id may appear in a practice request.  Whether each id is
    really readable is decided by A's Memory service and by the service's own review read --
    never by the fact that a client named it.
    """
    if value is None:
        return []
    if not isinstance(value, list):
        raise PhoneRequestError(reason)
    if len(value) > int(maximum):
        raise PhoneRequestError(reason)
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise PhoneRequestError(reason)
        text = item.strip()
        if not text or not _REF_ID_RE.match(text):
            raise PhoneRequestError(reason)
        if text not in out:
            out.append(text)
    return out


def _practice_outcome(value: Any) -> str:
    """Only the three declared trial outcomes; ``method_state`` is never a client's word."""
    if not isinstance(value, str):
        raise PhoneRequestError("PRACTICE_OUTCOME_INVALID")
    text = value.strip().upper()
    if text not in phone_service.PRACTICE_TRIAL_OUTCOMES:
        raise PhoneRequestError("PRACTICE_OUTCOME_INVALID")
    return text


def _clean_practice_content(value: Any) -> str:
    """The next body of one review/method: non-blank, no NUL, within the practice bound."""
    if not isinstance(value, str):
        raise PhoneRequestError("PRACTICE_CONTENT_INVALID")
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip() or "\x00" in text:
        raise PhoneRequestError("PRACTICE_CONTENT_INVALID")
    if len(text) > phone_service.MAX_PRACTICE_CONTENT:
        raise PhoneRequestError("PRACTICE_CONTENT_INVALID")
    return text


def _reading_page(value: Any) -> int:
    """A page is a strict positive integer: no bool, no float, no numeric string."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise PhoneRequestError("PAGE_INVALID")
    if value < 1:
        raise PhoneRequestError("PAGE_INVALID")
    if value > phone_service.MAX_READING_PAGE:
        raise PhoneRequestError("PAGE_OUT_OF_RANGE")
    return int(value)


def _clean_reading_content(value: Any) -> str:
    """The operator's own text: 1..24000 UTF-8 bytes, one non-blank body, no NUL.

    The per-page split and the 12000-byte page limit stay the owner's business; P only refuses
    what cannot be a page at all.  ``%%PAGE%%`` is the only separator, and text without one is a
    single page — P never invents a split of its own.
    """

    if not isinstance(value, str):
        raise PhoneRequestError("READING_CONTENT_INVALID")
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip() or "\x00" in text:
        raise PhoneRequestError("READING_CONTENT_INVALID")
    size = len(text.encode("utf-8"))
    if size < 1 or size > phone_service.MAX_READING_CONTENT_BYTES:
        raise PhoneRequestError("READING_CONTENT_INVALID")
    return text


def _clean_annotation(value: Any, maximum_bytes: int, reason: str) -> str:
    """One page annotation's text: non-blank, no NUL, within C's own UTF-8 byte bound.

    C measures these in bytes (``PhoneReading._text``), so P does too: a value P accepts is never
    then refused by the owner for a size it already declared.
    """

    if not isinstance(value, str):
        raise PhoneRequestError(reason)
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip() or "\x00" in text:
        raise PhoneRequestError(reason)
    if len(text.encode("utf-8")) > int(maximum_bytes):
        raise PhoneRequestError(reason)
    return text


def _clean_text(value: Any, maximum: int, reason: str) -> str:
    if not isinstance(value, str):
        raise PhoneRequestError(reason)
    text = value.strip()
    if not text or len(text) > maximum:
        raise PhoneRequestError(reason)
    if any(ch in text for ch in ("\x00", "\r")):
        raise PhoneRequestError(reason)
    return text


def _clean_body(value: Any, maximum: int) -> str:
    if not isinstance(value, str):
        raise PhoneRequestError("BODY_INVALID")
    text = value.replace("\r\n", "\n")
    if not text.strip() or len(text) > maximum:
        raise PhoneRequestError("BODY_INVALID")
    if "\x00" in text:
        raise PhoneRequestError("BODY_INVALID")
    return text


def _operation_key(value: Any) -> str:
    if not isinstance(value, str):
        raise PhoneRequestError("OPERATION_KEY_REQUIRED")
    key = value.strip()
    if not key or len(key) > MAX_OPERATION_KEY_LEN:
        raise PhoneRequestError("OPERATION_KEY_INVALID")
    if not re.match(r"^[A-Za-z0-9._:\-]+$", key):
        raise PhoneRequestError("OPERATION_KEY_INVALID")
    return key


# ---------------------------------------------------------------------------------------------
# shared practice: the user's own intent shapes (A owns every meaning)
# ---------------------------------------------------------------------------------------------


def _shared_practice_id(value: Any) -> str:
    """One practice id exactly as the owner spells it: matched, never rewritten."""

    if not isinstance(value, str):
        raise PhoneRequestError("SHARED_PRACTICE_ID_INVALID")
    text = value.strip()
    if not text or len(text) > phone_practice.MAX_PRACTICE_ID:
        raise PhoneRequestError("SHARED_PRACTICE_ID_INVALID")
    if any(ch in text for ch in ("\x00", "\r", "\n", "/", "?", " ")):
        raise PhoneRequestError("SHARED_PRACTICE_ID_INVALID")
    return text


def _practice_source_id(value: Any) -> str:
    """One material id from the owner's own source list: an id, never a path or a URL."""

    if not isinstance(value, str):
        raise PhoneRequestError("SHARED_PRACTICE_SOURCE_REQUIRED")
    text = value.strip()
    if not text or len(text) > phone_practice.MAX_SOURCE_ID:
        raise PhoneRequestError("SHARED_PRACTICE_SOURCE_INVALID")
    if any(ch in text for ch in ("\x00", "\r", "\n", "/", "?", " ", "\\")):
        raise PhoneRequestError("SHARED_PRACTICE_SOURCE_INVALID")
    if "://" in text:
        raise PhoneRequestError("SHARED_PRACTICE_SOURCE_INVALID")
    return text


def _practice_text(value: Any, maximum: int, reason: str) -> str:
    """One bounded, single-line protocol value the client declares (a version, a head, a reason)."""

    if not isinstance(value, str):
        raise PhoneRequestError(reason)
    text = value.strip()
    if not text or len(text) > int(maximum):
        raise PhoneRequestError(reason)
    if any(ch in text for ch in ("\x00", "\r", "\n")):
        raise PhoneRequestError(reason)
    return text


def _practice_body(value: Any) -> str:
    """The user's own text: 1..MAX_BODY characters, newlines normalised, nothing else.

    This is the *only* free-form string this surface accepts, and it is always the user's own
    words.  Her reply is never a payload field: it is written by B's writer inside the normal
    Life chain, and the phone never sends it.
    """

    if not isinstance(value, str):
        raise PhoneRequestError("SHARED_PRACTICE_TEXT_REQUIRED")
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip() or "\x00" in text:
        raise PhoneRequestError("SHARED_PRACTICE_TEXT_INVALID")
    if len(text) > phone_service.MAX_BODY:
        raise PhoneRequestError("SHARED_PRACTICE_TEXT_INVALID")
    return text

# ---------------------------------------------------------------------------------------------
# local visits: the one request shape (A owns every meaning)
# ---------------------------------------------------------------------------------------------


def _contact_event_ref(value: Any) -> str:
    """One transport event ref exactly as A spells it (``contact_evt:<hex>``): no rewriting."""

    if not isinstance(value, str):
        raise PhoneRequestError("CONTACT_EVENT_REF_INVALID")
    text = value.strip()
    if not text or len(text) > 200:
        raise PhoneRequestError("CONTACT_EVENT_REF_INVALID")
    if any(ch in text for ch in ("\x00", "\r", "\n", "/", "?", " ")):
        raise PhoneRequestError("CONTACT_EVENT_REF_INVALID")
    return text


def _contact_time(value: Any, reason: str) -> str:
    """One optional ISO-8601 instant the client *declares* (when it heard it).  Never a length."""

    if value is None:
        return ""
    if not isinstance(value, str) or not value.strip():
        raise PhoneRequestError(reason)
    text = value.strip()
    if len(text) > 40 or any(ch in text for ch in ("\x00", "\r", "\n", " ")):
        raise PhoneRequestError(reason)
    return text


def _contact_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Rebuild one arrival from the client's own intent.  Everything else is P's own.

    The three kinds are A's names (no translation).  An ordinary message may **not** carry a
    scope or an end; an invitation must carry an explicit scope; an end must say which
    invitation it closes and must be explicit or carry completion evidence.  A deferral, an
    acceptance and a decline are **not** fields here: those are her answers, recorded by the
    consumer that owns the decision -- not something a phone click may write.
    """

    raw_kind = payload.get("event_kind")
    kind = str(raw_kind).strip().upper() if isinstance(raw_kind, str) else ""
    if kind not in CONTACT_KINDS:
        raise PhoneRequestError("CONTACT_EVENT_KIND_UNSUPPORTED")
    built: dict[str, Any] = {"event_kind": kind}
    text = payload.get("text")
    if text is not None:
        if not isinstance(text, str):
            raise PhoneRequestError("CONTACT_TEXT_INVALID")
        normalised = text.replace("\r\n", "\n").replace("\r", "\n")
        if len(normalised) > phone_contact.MAX_CONTACT_MESSAGE or "\x00" in normalised:
            raise PhoneRequestError("CONTACT_TEXT_INVALID")
        if normalised.strip():
            built["text"] = normalised
    if kind == phone_contact.KIND_MESSAGE_PHONE:
        # An ordinary message is an observation: it may not claim a scope, an expiry, an
        # engagement requirement or an end.  Each one is refused by name.
        for field in ("scope", "expires_after_s", "duration_seconds", "requires_engagement",
                      "atomic", "invitation_id", "explicit_end", "reason"):
            if payload.get(field) not in (None, "", False):
                raise PhoneRequestError("CONTACT_FIELD_NOT_ALLOWED_FOR_KIND:%s" % field)
        return built
    if kind == phone_contact.KIND_INVITATION_PHONE:
        scope = payload.get("scope")
        if not isinstance(scope, str) or not scope.strip():
            raise PhoneRequestError("CONTACT_SCOPE_REQUIRED")
        if len(" ".join(scope.split())) > phone_contact.MAX_CONTACT_SCOPE or "\x00" in scope:
            raise PhoneRequestError("CONTACT_SCOPE_INVALID")
        built["scope"] = " ".join(scope.split())
        for field in ("explicit_end", "reason"):
            if payload.get(field) not in (None, "", False):
                raise PhoneRequestError("CONTACT_FIELD_NOT_ALLOWED_FOR_KIND:%s" % field)
        expires = payload.get("expires_after_s")
        if expires is not None:
            if isinstance(expires, bool) or not isinstance(expires, int):
                raise PhoneRequestError("CONTACT_EXPIRES_AFTER_INVALID")
            if expires < 1 or expires > phone_contact.MAX_CONTACT_SECONDS:
                raise PhoneRequestError("CONTACT_EXPIRES_AFTER_INVALID")
            # The client says "how long this invitation is good for"; A records the instant.
            import datetime as _dt
            moment = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=int(expires))
            built["expires_at"] = moment.isoformat(timespec="seconds")
        duration = payload.get("duration_seconds")
        if duration is not None:
            if isinstance(duration, bool) or not isinstance(duration, int):
                raise PhoneRequestError("CONTACT_DURATION_INVALID")
            if duration < 0 or duration > phone_contact.MAX_CONTACT_SECONDS:
                raise PhoneRequestError("CONTACT_DURATION_INVALID")
            built["duration_seconds"] = int(duration)
        for field in ("requires_engagement", "atomic"):
            if field in payload:
                if not isinstance(payload[field], bool):
                    raise PhoneRequestError("CONTACT_%s_INVALID" % field.upper())
                built[field] = bool(payload[field])
        invitation_id = payload.get("invitation_id")
        if invitation_id not in (None, ""):
            raise PhoneRequestError("CONTACT_INVITATION_ID_NOT_CLIENT_CHOSEN")
        reason = payload.get("reason")
        if reason not in (None, ""):
            raise PhoneRequestError("CONTACT_FIELD_NOT_ALLOWED_FOR_KIND:reason")
        return built
    # An end: it closes one named invitation.  A scope / an engagement claim belongs to the
    # invitation, not here.
    for field in ("scope", "expires_after_s", "duration_seconds", "requires_engagement",
                  "atomic"):
        if payload.get(field) not in (None, "", False):
            raise PhoneRequestError("CONTACT_FIELD_NOT_ALLOWED_FOR_KIND:%s" % field)
    invitation_id = payload.get("invitation_id")
    if not isinstance(invitation_id, str) or not invitation_id.strip():
        raise PhoneRequestError("CONTACT_INVITATION_ID_REQUIRED")
    invitation = invitation_id.strip()
    if len(invitation) > 200 or any(ch in invitation for ch in ("\x00", "\r", "\n", " ")):
        raise PhoneRequestError("CONTACT_INVITATION_ID_INVALID")
    built["invitation_id"] = invitation
    explicit = payload.get("explicit_end")
    if explicit is not None:
        if not isinstance(explicit, bool):
            raise PhoneRequestError("CONTACT_EXPLICIT_END_INVALID")
        built["explicit_end"] = bool(explicit)
    reason = payload.get("reason")
    if reason not in (None, ""):
        if not isinstance(reason, str):
            raise PhoneRequestError("CONTACT_REASON_INVALID")
        cleaned = " ".join(reason.split())
        if not cleaned or len(cleaned) > 64 or "\x00" in cleaned:
            raise PhoneRequestError("CONTACT_REASON_INVALID")
        built["reason"] = cleaned
    if built.get("explicit_end") is not True:
        # There is no completion-evidence field on this surface: a phone click either ends the
        # session explicitly or does not claim to end it at all.
        raise PhoneRequestError("CONTACT_END_NEEDS_EXPLICIT_END")
    if text is not None and built.get("text"):
        raise PhoneRequestError("CONTACT_FIELD_NOT_ALLOWED_FOR_KIND:text")
    return built


# ---------------------------------------------------------------------------------------------
# world action pre-flight (shape only; C owns every semantic)
# ---------------------------------------------------------------------------------------------


def _world_choice(value: Any, allowed: frozenset[str], reason: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise PhoneRequestError(reason)
    return value


def _world_target(value: Any, *, kinds: Optional[frozenset[str]] = None) -> dict[str, Any]:
    """A placement target with exactly the keys its kind allows (no client extras)."""

    if not isinstance(value, dict):
        raise PhoneRequestError("TARGET_INVALID")
    forbidden = sorted(set(value) & WORLD_FORBIDDEN_PARAM_KEYS)
    if forbidden:
        raise PhoneRequestError("CLIENT_IDENTITY_REFUSED:%s" % ",".join(forbidden), 409)
    kind = value.get("kind")
    if not isinstance(kind, str) or kind not in WORLD_TARGET_FIELDS:
        raise PhoneRequestError("TARGET_INVALID")
    if kinds is not None and kind not in kinds:
        # Moving one of her own pieces of furniture goes to a room or onto a declared surface;
        # a container / a hand / transit belong to other verbs and other owners.
        raise PhoneRequestError("TARGET_KIND_NOT_ALLOWED:%s" % kind)
    expected = WORLD_TARGET_FIELDS[kind]
    if set(value) != set(expected):
        raise PhoneRequestError("TARGET_INVALID")
    target: dict[str, Any] = {"kind": kind}
    for field in sorted(expected - {"kind"}):
        if field == "slot_id":
            target[field] = _world_choice(value[field], HAND_SLOTS, "TARGET_INVALID")
        else:
            target[field] = _clean_text(value[field], MAX_WORLD_PLACEMENT_ID, "TARGET_INVALID")
    return target


def _world_params(action: str, value: Any) -> dict[str, Any]:
    """Validate one verb's params before the owner sees them (contract §3.1).

    ``ARRANGE`` / ``TOGGLE_FIXTURE`` follow C's own ``PARAM_SPEC`` exactly (``object_id`` +
    ``target`` / ``object_id`` + absolute ``state``).  P checks the shape only; the owner's word
    and reason code win.  Identity, elapsed time, price and body values are refused here for
    every verb.
    """

    if not isinstance(value, dict):
        raise PhoneRequestError("PARAMS_REQUIRED")
    forbidden = sorted(set(value) & WORLD_FORBIDDEN_PARAM_KEYS)
    if forbidden:
        # uid/home/authority/elapsed/body values are never an input, and never ignored.
        raise PhoneRequestError("CLIENT_IDENTITY_REFUSED:%s" % ",".join(forbidden), 409)
    allowed, required = WORLD_ACTION_FIELDS[action]
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise PhoneRequestError("UNKNOWN_FIELD:%s" % ",".join(unknown))
    missing = sorted(set(required) - set(value))
    if missing:
        raise PhoneRequestError("PARAM_REQUIRED:%s" % ",".join(missing))
    if action == "ARRANGE":
        return {
            "object_id": _clean_text(value["object_id"], MAX_WORLD_OBJECT_ID,
                                     "OBJECT_ID_INVALID"),
            "target": _world_target(value["target"], kinds=ARRANGE_TARGET_KINDS),
        }
    if action == "TOGGLE_FIXTURE":
        return {
            "object_id": _clean_text(value["object_id"], MAX_WORLD_OBJECT_ID,
                                     "OBJECT_ID_INVALID"),
            "state": _world_choice(value["state"], FIXTURE_STATES, "FIXTURE_STATE_INVALID"),
        }
    if action == "MOVE":
        return {"destination_location_id": _clean_text(
            value["destination_location_id"], MAX_WORLD_DESTINATION, "DESTINATION_INVALID")}
    if action == "PICK_UP":
        return {
            "object_id": _clean_text(value["object_id"], MAX_WORLD_OBJECT_ID,
                                     "OBJECT_ID_INVALID"),
            "slot_id": _world_choice(value["slot_id"], HAND_SLOTS, "SLOT_INVALID"),
        }
    if action == "PLACE":
        return {
            "object_id": _clean_text(value["object_id"], MAX_WORLD_OBJECT_ID,
                                     "OBJECT_ID_INVALID"),
            "target": _world_target(value["target"]),
        }
    if action == "POSE":
        params: dict[str, Any] = {
            "pose": _world_choice(value["pose"], POSES, "POSE_INVALID")}
        if "orientation" in value:
            params["orientation"] = _world_choice(value["orientation"], ORIENTATIONS,
                                                  "ORIENTATION_INVALID")
        return params
    # REST: state is the command, mode only ever belongs to a start.
    state = _world_choice(value["state"], REST_STATES, "REST_STATE_INVALID")
    rest: dict[str, Any] = {"state": state}
    if "mode" in value:
        if state != "start":
            raise PhoneRequestError("MODE_REQUIRES_START")
        rest["mode"] = _world_choice(value["mode"], REST_MODES, "REST_MODE_INVALID")
    return rest


def create_server(service: phone_service.PhoneService, *, csrf_token: str, port: int = 0,
                  log_path: Optional[Path] = None,
                  supervisor: Optional[Any] = None) -> PhoneHTTPServer:
    """Bind one loopback server.  The host is not a caller choice."""

    if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
        raise ValueError("port must be an integer in 0..65535")
    return PhoneHTTPServer(("127.0.0.1", int(port)), service=service,
                           csrf_token=csrf_token, log_path=log_path, supervisor=supervisor)


__all__ = [
    "ALLOWED_FETCH_SITES",
    "ALLOWED_HOSTS",
    "API_PREFIX",
    "ARRANGE_TARGET_KINDS",
    "FIXTURE_STATES",
    "HAND_SLOTS",
    "IDENTITY_FIELDS",
    "MAX_BODY_BYTES",
    "MAX_WORLD_DESTINATION",
    "MAX_WORLD_OBJECT_ID",
    "MAX_WORLD_PLACEMENT_ID",
    "ORIENTATIONS",
    "POSES",
    "MATERIAL_IMPORT_FIELDS",
    "MATERIAL_QUERY_FIELDS",
    "PROJECT_CLOSE_FIELDS",
    "RESEARCH_CLOSE_FIELDS",
    "RESEARCH_CREATE_FIELDS",
    "RESEARCH_QUERY_FIELDS",
    "RESEARCH_SOURCE_FIELDS",
    "RESEARCH_UPDATE_FIELDS",
    "PROJECT_CREATE_FIELDS",
    "PROJECT_COMMIT_FIELDS",
    "PhoneHTTPServer",
    "PhoneRequestError",
    "PhoneRequestHandler",
    "READING_ANNOTATION_FIELDS",
    "READING_ANNOTATION_KINDS",
    "READING_MATERIAL_FIELDS",
    "READING_POSITION_FIELDS",
    "READING_QUERY_FIELDS",
    "REST_MODES",
    "REST_STATES",
    "ROOM_WORLD_ACTION_FIELDS",
    "STATIC_FILES",
    "STORE_PURCHASE_FIELDS",
    "SHARE_ACTION_FIELDS",
    "SHARE_ACTIONS",
    "SHARE_ADDRESSING_FIELDS",
    "SHARE_PREPARE_FIELDS",
    "SHARE_QUERY_FIELDS",
    "WEB_DIR",
    "WORLD_ACTION_FIELDS",
    "WORLD_FIELDS",
    "WORLD_FORBIDDEN_PARAM_KEYS",
    "WORLD_TARGET_FIELDS",
    "create_server",
    "http_status_for",
]

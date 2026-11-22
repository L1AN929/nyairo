#!/usr/bin/env python3
"""v0.4 手机 HTTP 面：loopback-only、单实例、一个 JSON 信封。

这里的每一条闸门都是 **fail closed**：

* 只监听 ``127.0.0.1``；``Host`` 必须是 ``127.0.0.1`` / ``localhost``（可带端口）；
* ``Origin``（若有）必须与 ``Host`` 同源；``Sec-Fetch-Site``（若有）只能是
  ``same-origin`` / ``none``；
* 每个 ``POST`` 都要 ``X-CSRF-Token``，值与 ``GET /api/phone/state`` 里的
  ``csrf_token`` 一致（常量时间比较）；
* 日记正文读取（``X-Phone-Actor: model``）与主体日记写入还要**本次 serve 的本地能力凭据**
  （``Authorization: Bearer`` 或 ``X-Phone-Capability``，常量时间比较）；缺失/错误/本 serve
  不可用时 403，不降级成空正文，也不写任何条目。operator 的元数据读取不需要凭据；
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

``GET /api/phone/today`` 是纯读：按业务时钟当天（缺失才退回系统时钟，并具名）列出当天开始的
Activity（开始时间 / 做了什么 / 结果）和当天产生的作品版本（作品名 + 版本号）；活动权威读不到
时回具名 ``unavailable``，不回一个看起来「她今天什么都没做」的空成功。

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
from typing import Any, Callable, Mapping, Optional
from urllib.parse import parse_qsl, urlsplit

from . import capability as phone_capability
from . import operator_stop as phone_operator_stop
from . import service as phone_service
from . import living as phone_living
from . import sharing as phone_sharing
from . import contact as phone_contact
from . import outing_places_view as outing_view
from . import practice_shared as phone_practice

WEB_DIR = Path(__file__).resolve().parent / "web"

#: The one API prefix this surface serves.  Nothing else under the host is routed here.
API_PREFIX = "/api/phone"

#: v0.8 general object affordances, living traces and personal habit routines.
ROUTE_V08_LIVING_TRACES = "/api/phone/v08/living-traces"

#: v0.4 Ch.51 composable optional body process packs.
ROUTE_V08_COMPOSABLE_BODY = "/api/phone/v08/composable-body"

#: v3.0 finite glance / shared material / shopkeeper hearsay surface.
ROUTE_V03_ENCOUNTERS = "/api/phone/v03/encounters"

#: v0.4 Ch.48/50 + v3.0 Ch.24 emergent echoes (questions, idioms, reinterpretations, sparks, notices).
ROUTE_V08_EMERGENT_ECHOES = "/api/phone/v08/emergent-echoes"

#: v0.9 Pillars A + D (blind choices, growth stances, repurposed objects, custom taxonomy, capability gaps).
ROUTE_V09_LIVING_AGENCY = "/api/phone/v09/living-agency"

#: v0.9 Pillars B + C (dynamic catalog, persistent NPCs, durable commitments & venue notices).
ROUTE_V09_WORLD_COMMITMENTS = "/api/phone/v09/world-commitments"

#: Stage 3 unified pollable outbound outbox (proactive outreach + ready deferred replies).
ROUTE_OUTBOUND_PENDING = "/api/phone/outbound/pending"
ROUTE_OUTBOUND_ACK = "/api/phone/outbound/ack"

MAX_BODY_BYTES = 64 * 1024
#: One album import may carry ~5 MiB of picture bytes (~7 MiB of base64 JSON).  This wider body
#: belongs to ``POST /api/phone/album/images`` alone; every other POST keeps MAX_BODY_BYTES.
MAX_ALBUM_BODY_BYTES = 8 * 1024 * 1024
MAX_ALBUM_CAPTION = 200
#: The local supervisor's run controls.  The client only names a bound and a pause intent; the
#: shared feature cap, the ledger baseline and every refusal stay in ``living``/the supervisor.
LIVING_WINDOW_FIELDS = frozenset({"duration_seconds", "max_new_requests"})
LIVING_PAUSE_FIELDS = frozenset({"paused", "operation_key"})
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
#: ``POST /api/phone/control/activity/stop`` -- the local operator names only which Activity,
#: which revision it saw, its own stable operation key and a closed reason code.  Subject,
#: epoch and authority come from the server side; an ``actor``/``operator``/``system`` field
#: is refused outright (it is in ``IDENTITY_FIELDS``).
OPERATOR_STOP_FIELDS = frozenset({
    "activity_id", "expected_revision", "operation_key", "reason_code",
})
OPERATOR_STOP_REASONS = frozenset({
    "OPERATOR_REQUEST", "USER_REQUEST", "SAFETY_HOLD", "SERVICE_SHUTDOWN",
})
#: ``POST /api/phone/diary``: one entry of her own words.  The operation key is required: a diary
#: write is an explicit act, and the original key is what makes a retry land on the same entry.
DIARY_WRITE_FIELDS = frozenset({"body", "operation_key"})
REFERENCE_FIELDS = frozenset({"source_id", "source_revision", "label", "operation_key"})
PURCHASE_FIELDS = frozenset({"catalog_item_id", "operation_key"})
USE_FIELDS = frozenset({"item_instance_id", "operation_key"})
#: ``POST /api/phone/store/purchases``: the counter purchase of one physical-store item.  The
#: client names the catalogue row and its own idempotency key; it never names a price, a balance,
#: a delivery state or an owner.
STORE_PURCHASE_FIELDS = frozenset({"catalog_item_id", "item_name", "operation_key"})

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
#: ``POST /api/phone/camera`` -- one local picture from her own scene text.  The client names the
#: scene it wants, the caption she wrote and its own idempotency key; never a picture, a path, a
#: URL, a model, a runtime address or bytes.
CAMERA_CAPTURE_FIELDS = frozenset({"scene", "caption", "operation_key"})
MAX_CAMERA_SCENE = 800

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

#: ``/api/phone/desk/*`` -- the two-person desk projection.  ``presence`` and ``notes`` are
#: pure reads.  ``POST /api/phone/desk/tomorrow`` is the one explicit write: it forwards a
#: propose/cancel/reschedule to the existing ``FutureItems`` owner, after CSRF and this
#: serve's local capability; it creates no second store and no autonomous decision.
DESK_TOMORROW_FIELDS = frozenset({"action", "title", "item_ref", "due_at", "due_in_minutes",
                                  "reason"})
DESK_ACTIONS = frozenset({"propose", "cancel", "reschedule"})

#: ``/api/phone/home/*`` -- 窗边景致、冰箱贴与自选锁屏、分日慢写的信。两个 GET 是纯读聚合；
#: ``POST /api/phone/home/keepsakes`` 只写实例自己的显式选择 JSON：先 CSRF，再本次 serve 的
#: 本地 capability；引用会被 owner 现读核验，但正文/图片/路径永远不进这个请求体。
WINDOW_KEEPSAKE_FIELDS = frozenset({"action", "album_artifact_id", "share_id"})
WINDOW_KEEPSAKE_ACTIONS = frozenset({
    "select_lockscreen", "clear_lockscreen", "pin_share", "unpin_share",
})
#: 「顺手带一张图/一段话进来」不是这个面的输入。客户端若发这些字段就按名字拒绝（409），
#: 而不是静默丢掉——静默丢掉会看起来像选择已经生效。
WINDOW_KEEPSAKE_REFUSED_FIELDS = frozenset({
    "caption", "text", "title", "summary", "media", "bytes", "data", "sha256", "hash",
    "path", "file", "file_path", "url", "uri", "endpoint", "body", "content",
    "audience", "channel", "recipient", "to", "transport", "provider", "source",
})
SLOW_LETTER_SMUGGLED_FIELDS = frozenset({
    "author_side", "actor_side", "uid", "euid", "gid", "owner", "subject", "actor",
    "authority", "authorities", "main_self_verified", "confirm_apply", "score",
    "attachment_score", "omission_debt", "cools_until", "drafted_at", "sent_at",
    "deliver_after",
})

#: ``/api/phone/home/workbench`` -- the explicit creation tray.  ``GET`` re-reads every
#: reference; ``POST`` writes only the instance's own reference JSON after CSRF + capability.
#: The client may not send a caption/body/version: the baseline version is always read by the
#: server from the real owner at insertion time.
WORKBENCH_ACTIONS = frozenset({"set_tray", "clear_tray"})
WORKBENCH_FIELDS = frozenset({"action", "album_artifact_ids", "material_ids",
                              "work_artifact_id", "note"})
WORKBENCH_REFUSED_FIELDS = WINDOW_KEEPSAKE_REFUSED_FIELDS | frozenset({
    "version", "version_id", "head_version_id", "baseline", "baseline_version_id",
    "source_revision", "artifact_type",
})

#: ``/api/phone/home/trio-exchange`` -- her delivered picks and the partner's short cards.
#: ``set_her_picks`` needs CSRF + capability; ``set_partner_picks`` needs CSRF only (a
#: capability header that *is* presented must still verify and never downgrades silently).
TRIO_ACTIONS = frozenset({"set_her_picks", "set_partner_picks"})
TRIO_FIELDS = frozenset({"action", "her_picks", "partner_picks"})

#: ``/api/phone/home/wardrobe`` -- explicit styling choices only.  ``GET`` re-reads the real
#: economy for any named owned instance; ``POST`` writes only this instance's own selection
#: JSON after CSRF + capability.  A wardrobe choice is not ownership, location or wear state.
WARDROBE_ACTIONS = frozenset({"set", "clear"})
WARDROBE_SET_FIELDS = frozenset({
    "homewear_style", "homewear_item_instance_id", "outdoor_style",
    "outdoor_item_instance_id", "hairband_style", "hairband_item_instance_id",
    "hairclip_style", "hairclip_item_instance_id", "pairing_note", "blanket_season",
    "cushion_season",
})
WARDROBE_FIELDS = WARDROBE_SET_FIELDS | frozenset({"action", "fields"})
WARDROBE_REFUSED_FIELDS = frozenset({
    "path", "file", "file_path", "url", "uri", "endpoint", "media", "bytes", "data",
    "owner", "actor", "subject", "uid", "world_id", "place_id", "area_id", "location",
    "shared", "revision", "worn", "equipped", "apply_to_world", "change_world",
})

#: ``/api/phone/pocket/lists`` -- bounded personal cards + up to three partner suggestions.
#: Personal add/update/remove need CSRF + capability; the partner replacement needs CSRF only
#: (a capability that *is* presented must still verify).  Nothing here calls reading, a model,
#: Memory or FutureItems.
POCKET_ACTIONS = frozenset({"add", "update", "remove", "set_partner_suggestions"})
POCKET_FIELDS = frozenset({
    "action", "list", "entry_id", "title", "reason", "expectation", "verdict", "note",
    "status", "shared", "operation_key", "suggestions",
})
POCKET_REFUSED_FIELDS = frozenset({
    "path", "file", "file_path", "url", "uri", "endpoint", "media", "bytes", "data",
    "owner", "actor", "subject", "uid", "revision", "created_at", "updated_at",
    "partner_contributed", "private_count", "total", "count", "entries",
})
#: The partner suggestion replacement: each card carries only a short title and note.
POCKET_SUGGESTION_FIELDS = frozenset({"title", "note"})
#: The finite status vocabulary *per list*.  A status that belongs to another list (or to no
#: list at all) is a 400 request error, never silently coerced.
POCKET_LIST_STATUSES = {
    "playlist": frozenset({"active", "resting"}),
    "favorites": frozenset({"saved_unread", "reviewed"}),
    "want_to_try": frozenset({"active", "paused", "dropped", "done"}),
}

#: ``/api/phone/pocket/work-revisit`` -- exactly these three query keys, each once, no default.
WORK_REVISIT_QUERY_FIELDS = frozenset({"artifact_id", "left_version_id", "right_version_id"})

#: ``/api/phone/home/reunion-anchor`` -- one explicit safe capture.  The client names only the
#: action: every fact in the snapshot is read here from the real owners, never submitted.
REUNION_FIELDS = frozenset({"action"})
REUNION_ACTIONS = frozenset({"capture"})

#: ``/api/phone/home/recipes`` -- the two-person shared recipe cards.  ``author_side`` selects
#: the write gate (her = CSRF + this serve's capability; partner = CSRF only, and a capability
#: that *is* presented must still verify).  It never grants her identity.
RECIPE_FIELDS = frozenset({"action", "recipe_id", "title", "memo", "author_side"})
RECIPE_ACTIONS = frozenset({"upsert"})

#: ``/api/phone/home/tentative-plans`` -- propose / reschedule / drop only.  There is no generic
#: status write, no ACCEPTED and no import into core arrangements, Activity or FutureItems.
PLAN_ACTIONS = frozenset({"propose", "reschedule", "drop"})
PLAN_FIELDS = frozenset({"action", "plan_id", "title", "window_note", "proposer"})

#: ``/api/phone/outing/*`` -- 房间外六项生活细节：长椅重访与书店橱窗、散步路线与虚拟预算集市、
#: 可重访作品展与共同新地点草图。所有动作均按精确允许键存在性校验，跨动作空字段一律 400。
OUTING_BENCH_BOOKSTORE_ACTIONS = frozenset({
    "log_bench_visit", "set_bench_visibility", "bookmark_window_book",
    "set_partner_window_note", "clear_partner_window_note",
})
OUTING_BENCH_BOOKSTORE_FIELDS = frozenset({
    "action", "place_id", "note", "shared", "operation_key", "visit_id", "slot_index",
})
OUTING_ROUTES_MARKET_ACTIONS = frozenset({
    "save_walk_route", "remove_walk_route", "note_market_wish",
    "set_partner_market_tip", "clear_partner_market_tip",
})
OUTING_ROUTES_MARKET_FIELDS = frozenset({
    "action", "route_id", "title", "place_ids", "album_artifact_id", "caption", "shared",
    "stall_code", "memo", "budget_cap_jpy", "tip",
})
OUTING_GALLERY_BLUEPRINT_ACTIONS = frozenset({
    "curate_exhibit", "remove_exhibit", "upsert_blueprint", "archive_blueprint",
})
OUTING_GALLERY_BLUEPRINT_FIELDS = frozenset({
    "action", "artifact_id", "version_id", "wall_caption", "exhibit_id",
    "blueprint_id", "title", "sketch_note", "intended_use", "author_side",
})

#: ``/api/phone/longterm/*`` -- 长程陪伴六项生活细节：首日房间与盆栽手账/共同习惯册、
#: 共同礼物轨迹与回忆空间双层灯光、一键续接与归档/恢复只读总览。所有动作均按精确键集校验。
LONGTERM_FIRST_ROOM_HABITS_ACTIONS = frozenset({
    "capture_first_room", "link_plant_sketch", "remove_plant_sketch",
    "upsert_habit", "retire_habit",
})
LONGTERM_FIRST_ROOM_HABITS_FIELDS = frozenset({
    "action", "album_artifact_id", "note",
    "plant_id", "target_kind", "target_artifact_id", "observation_note", "shared", "link_id",
    "habit_id", "title", "habit_text", "revised_reason", "retired_reason", "author_side",
})
LONGTERM_GIFTS_LANTERN_ACTIONS = frozenset({
    "upsert_gift", "send_gift", "record_later_reflection",
})
LONGTERM_GIFTS_LANTERN_FIELDS = frozenset({
    "action", "gift_id", "author_side", "stage", "title", "draft_seed_note",
    "finished_note", "artifact_id", "version_id",
    "source_artifact_id", "source_version_id", "reflection_note", "shared",
})
LONGTERM_LANTERN_LAYERS = frozenset({"then_only", "with_later_context"})

#: ``/api/phone/character/pack`` & ``/api/phone/body/sensory`` -- 开源角色包合同、可组合身体与
#: 五路环境感官场 + 文字声景卡。按精确动作键集白名单校验，任何多余或空串可选键一律 400。
CHARACTER_PACK_ACTIONS = frozenset({
    "select_preset",
    "apply_custom_pack",
    "update_identity",
    "set_optional_physiology",
    "export_pack",
    "import_pack_json",
    "attest_character_pack",
})
CHARACTER_PACK_FIELDS = frozenset({
    "action", "preset_id", "pack",
    "display_name", "pronouns", "subject_calls_user", "user_calls_subject",
    "sensory_preferences", "everyday_accessories",
    "optional_processes",
    "pack_json", "attested_by", "pack_id", "version",
    "adult_confirmed", "life_stage", "declared_age", "fictional_confirmed",
    "include_sexual_seed", "pack_sha256", "note", "owner",
})
BODY_SENSORY_ACTIONS = frozenset({
    "capture_sound_card", "set_sound_card_share", "remove_sound_card",
    "leave_sensory_note", "remove_sensory_note", "clear_sensory_notes",
})
BODY_SENSORY_FIELDS = frozenset({
    "action", "title", "impression_note", "shared", "operation_key",
    "card_id", "text", "note_id",
})

#: ``/api/phone/autonomy/rhythm`` -- 主体日常自主拒绝边界、挂着的话题余温、主动走神改画风与
#: 真实异步延迟回信。GET 纯读零写；POST 先校验 CSRF，主体动作必须带 X-Phone-Capability。
AUTONOMY_RHYTHM_ACTIONS = frozenset({
    "evaluate_turn",
    "declare_refusal",
    "release_refusal",
    "record_turn_context",
    "hold_subject_topic",
    "return_to_thread",
    "clear_session_threads",
    "wave_off_retracted",
    "trigger_spontaneous_pivot",
    "set_phone_aside",
    "urgent_knock",
    "claim_ready_messages",
    "clear_delivered_messages",
    "update_preferences",
})
AUTONOMY_RHYTHM_SUBJECT_ACTIONS = frozenset({
    "declare_refusal",
    "release_refusal",
    "hold_subject_topic",
    "return_to_thread",
    "trigger_spontaneous_pivot",
    "set_phone_aside",
})
AUTONOMY_RHYTHM_FIELDS = frozenset({
    "action",
    "user_text", "urgent", "session_id",
    "topic_label", "category", "reason_summary", "private_note", "lock_id",
    "reason", "owner", "thread_id", "turn_id", "hold_thread",
    "custom_prompt",
    "mode", "minutes", "note",
    "msg_id", "msg_ids",
    "pace_mode", "away_delay_minutes", "anti_errand_firmness",
    "thread_hold_min", "spontaneous_pivot_enabled",
    "pivot_cooldown_min", "spontaneous_pivot_cooldown_min",
})

#: ``/api/phone/body/*`` -- 具身冷暖/坐姿投影、窗扇与外衣、桌边温饮/靠垫。三个 GET 都是
#: 纯读聚合；两个 POST 只写实例自己的局部仿真 JSON（先 CSRF；本人动作再要本次 serve 的
#: capability）。窗/衣物/照顾都只记显式条件，不复制 Body/世界位置，不建第二动作账本。
SOMATIC_WINDOW_STATES = frozenset({"open", "ajar", "closed"})
SOMATIC_LOCAL_ZONES = frozenset({"window_side", "desk_side", "elsewhere"})
SOMATIC_DRYNESS = frozenset({"dry", "damp", "unknown"})
SOMATIC_GARMENT_WINDOW_ACTIONS = frozenset({
    "set_window", "set_local_position", "wear_outerwear", "hang_outerwear",
})
SOMATIC_GARMENT_WINDOW_FIELDS = frozenset({
    "action", "state", "zone", "garment_id", "label", "dryness", "hang_position",
})
SOMATIC_CARE_ACTIONS = frozenset({"offer", "take", "drink", "use", "stow", "withdraw"})
SOMATIC_CARE_FIELDS = frozenset({"action", "slot", "kind", "actor_side", "position"})
SOMATIC_CARE_SLOTS = frozenset({"drink", "cushion"})
SOMATIC_CARE_KINDS = {"drink": frozenset({"warm_water", "hot_tea"}),
                      "cushion": frozenset({"floor_cushion"})}
SOMATIC_CARE_ACTORS = frozenset({"her", "partner"})
SOMATIC_CARE_POSITIONS = frozenset({"hand_side", "desk_side"})
#: A client may not name the real instance, its path/URL, its temperature or a body value:
#: those are the owner's or this module's own facts, never request fields.
SOMATIC_REFUSED_FIELDS = frozenset({
    "path", "file", "file_path", "url", "uri", "endpoint", "media", "bytes", "data",
    "item_instance_id", "object_id", "object", "slot_id", "container_id", "catalog_item_id",
    "temperature", "temp", "warmth", "cooling_seconds", "elapsed", "elapsed_seconds",
    "seconds", "duration", "body", "pose", "needs", "thirst", "hunger", "toilet",
    "place_id", "area_id", "location", "location_id", "revision", "operation_key",
})
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

#: ``/api/phone/decisions/review`` -- S04's read-only decision projection.  ``decision_id``
#: 单查一条有限摘要；缺省时只列出最近的骨架。``limit`` 只影响骨架列表长度。
DECISION_REVIEW_QUERY_FIELDS = frozenset({"decision_id", "limit"})

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
    # Optional lived-needs facility use: exactly one facility id the World declaration names.
    # Nothing else (no elapsed time, no body value, no effect) is ever a client's input.
    "TOILET": (frozenset({"facility_id"}), frozenset({"facility_id"})),
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
#: v0.5 same-work: one bounded, read-only aggregation of the same work's head/old versions,
#: provenance, owner-confirmed results, evidence-linked methods and local drafts.  Read-only and
#: host/origin guarded like every other private-artifact read; it sends and writes nothing.
_PROJECT_CONTEXT_PATH = re.compile(
    r"^/api/phone/projects/([A-Za-z0-9._:\-]{1,200})/context$")
#: ``/api/phone/album/<artifact_id>`` is one imported picture; ``/image`` is its real bytes.
#: The image path is matched first so it can never be read as an artifact id.  The id is the
#: owner's own protocol string, matched and never rewritten.
_ALBUM_IMAGE_PATH = re.compile(r"^/api/phone/album/([A-Za-z0-9._:\-]{1,200})/image$")
_ALBUM_PATH = re.compile(r"^/api/phone/album/([A-Za-z0-9._:\-]{1,200})$")
#: ``/api/phone/diary/<entry_id>``: one diary entry.  The id is the owner's own diary id
#: (``diary:<hex>``), matched and never rewritten.  The body is only included when the caller
#: is the subject's own tool path (``X-Phone-Actor: model``); the default operator read gets
#: metadata with the body withheld by name.
_DIARY_PATH = re.compile(r"^/api/phone/diary/([A-Za-z0-9._:\-]{1,200})$")
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
#: ``/api/phone/participants`` -- F-140's identity read surface (A's ``ParticipantRegistry``).
#: Two pure reads only: the bounded list and one participant.  There is deliberately **no**
#: POST here: creation / endpoint binding / event linking are local operator CLI acts.
_PARTICIPANT_PATH = re.compile(r"^/api/phone/participants/([A-Za-z0-9._:\-]{1,200})$")
#: Same bound as the owner (``participant_registry.MAX_LIST``); repeated here so this layer
#: refuses an oversized query before the owner is asked.
MAX_PARTICIPANTS_LIST = 50
PARTICIPANT_QUERY_FIELDS = frozenset({"limit"})
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
#: ``/api/phone/claims`` -- S03's bounded current-claim read.  ``/claims/<slot_id>`` is
#: one exact slot (the id is S03's own opaque string, matched never rewritten);
#: ``/claims/observations`` is the one write: it takes an operation key only, and the
#: phone itself performs a formal World read before recording evidence.
_CLAIM_SLOT_PATH = re.compile(r"^/api/phone/claims/([A-Za-z0-9._:\-]{1,200})$")
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
    request_queue_size = 128

    def __init__(self, address: tuple[str, int], *, service: phone_service.PhoneService,
                 csrf_token: str, log_path: Optional[Path] = None,
                 supervisor: Optional[Any] = None, reuse_address: bool = False,
                 capability: Optional[phone_capability.Capability] = None,
                 operator_stop: Optional[Any] = None) -> None:
        if not isinstance(service, phone_service.PhoneService):
            raise TypeError("PhoneHTTPServer needs a PhoneService")
        if not isinstance(csrf_token, str) or not csrf_token:
            raise ValueError("csrf_token required")
        # 默认仍是不复用（旧行为）；只有用户入口在“停止后立刻重新打开同一端口”时显式打开。
        # SO_REUSEADDR 在 Linux 上只放过 TIME_WAIT，不会让第二个活着的 listener 抢同一端口。
        self.allow_reuse_address = bool(reuse_address)
        self.service = service
        self.csrf_token = csrf_token
        #: This serve's local capability credential (private diary body / subject write).  It is
        #: minted after the real endpoint is bound; ``None`` means the private diary fails closed.
        self.capability: Optional[phone_capability.Capability] = capability
        #: 2026-10-28: the *separate* local-operator credential for the external-stop control
        #: route.  It is never the diary credential and it grants nothing else.
        self.operator_stop: Optional[Any] = operator_stop
        self.log_path = Path(log_path) if log_path else None
        #: The optional local supervisor.  A plain ``phone serve`` has none: the living view is
        #: still readable, but the run controls answer ``SUPERVISOR_UNAVAILABLE`` instead of
        #: starting a model window from a surface that never offered one.
        self.supervisor = supervisor
        super().__init__(address, PhoneRequestHandler)

    def set_capability(self, capability: Optional[phone_capability.Capability]) -> None:
        """Install the credential issued for this bound endpoint (called once, before serving)."""

        self.capability = capability

    def set_operator_stop(self, credential: Optional[Any]) -> None:
        """Install this serve's local-operator stop credential (never the diary one)."""

        self.operator_stop = credential


#: A peer that left before its answer was written.  There is nobody left to receive an error
#: body, so the request ends quietly: a second (500) write would only raise again and turn a
#: closed client into a logged internal error.
_CLIENT_GONE_ERRORS = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)


class PhoneRequestHandler(BaseHTTPRequestHandler):
    server_version = "chiyo-phone/0.4"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # -- plumbing ------------------------------------------------------------------------

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003 - stdlib signature
        """Never log headers, tokens or bodies: method, path and code only."""

        LOG.info("%s %s", self.command, self.path.split("?", 1)[0])

    def _write_response(self, emit: Any) -> bool:
        """Emit one response; a disconnected peer is a quiet end, not an internal error.

        Only socket-level disconnects are swallowed here.  Anything raised by the handler body
        itself still reaches ``_dispatch``'s error path, so a real failure stays visible.
        """

        if getattr(self, "_responded", False):
            LOG.warning("phone.duplicate_response_suppressed path=%s", self.path.split("?", 1)[0])
            self.close_connection = True
            return False
        if not getattr(self, "_body_consumed", True):
            raw_len = self.headers.get("Content-Length")
            if raw_len is None or str(raw_len).strip() != "0":
                self.close_connection = True
        self._responded = True
        try:
            emit()
        except _CLIENT_GONE_ERRORS:
            self.close_connection = True
            return False
        return True

    def _send(self, code: int, payload: Mapping[str, Any]) -> None:
        body = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True).encode("utf-8")

        def emit() -> None:
            self.send_response(int(code))
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if self.close_connection:
                self.send_header("Connection", "close")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        self._write_response(emit)

    def _send_image(self, content_type: str, body: bytes) -> None:
        """One real picture response.  Same-origin only; never cached, never sniffed."""

        def emit() -> None:
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

        self._write_response(emit)

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

        def emit() -> None:
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

        self._write_response(emit)

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
                   max_bytes: Optional[int] = None,
                   *,
                   allow_identity_fields: frozenset[str] = frozenset()) -> dict[str, Any]:
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
        self._body_consumed = True
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise PhoneRequestError("BODY_NOT_JSON") from None
        if not isinstance(payload, dict):
            raise PhoneRequestError("BODY_NOT_AN_OBJECT")
        identifiers = sorted((set(payload) & IDENTITY_FIELDS) - set(allow_identity_fields))
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

    # -- local capability (private diary only) -------------------------------------------

    def _presented_capability(self) -> tuple[Optional[str], Optional[str]]:
        """Extract one bearer credential.  Two differing headers are refused, never merged."""

        authorization = str(self.headers.get("Authorization") or "").strip()
        header = str(self.headers.get("X-Phone-Capability") or "").strip()
        values: list[str] = []
        if authorization:
            scheme, _, value = authorization.partition(" ")
            if scheme.strip().lower() != "bearer" or not value.strip():
                return None, "CAPABILITY_MALFORMED"
            values.append(value.strip())
        if header:
            values.append(header)
        if not values:
            return None, "CAPABILITY_REQUIRED"
        if len(set(values)) > 1:
            return None, "CAPABILITY_INVALID"
        return values[0], None

    def _capability_refusal(self) -> Optional[tuple[int, dict[str, Any]]]:
        """The one gate for the private diary.  Missing / wrong / unavailable never degrade."""

        capability = getattr(self.server, "capability", None)
        if capability is None:
            return 403, _failure("CAPABILITY_UNAVAILABLE")
        presented, reason = self._presented_capability()
        if reason is not None:
            return 403, _failure(reason)
        if not phone_capability.matches(capability, presented):
            return 403, _failure("CAPABILITY_INVALID")
        return None

    # -- local operator stop (a *separate* credential; never the diary one) ---------------

    def _presented_operator_stop(self) -> tuple[Optional[str], Optional[str]]:
        authorization = str(self.headers.get("Authorization") or "").strip()
        header = str(self.headers.get("X-Phone-Operator-Stop") or "").strip()
        values: list[str] = []
        if authorization:
            scheme, _, value = authorization.partition(" ")
            if scheme.strip().lower() != "bearer" or not value.strip():
                return None, "OPERATOR_STOP_CAPABILITY_MALFORMED"
            values.append(value.strip())
        if header:
            values.append(header)
        if not values:
            return None, "OPERATOR_STOP_REQUIRED"
        if len(set(values)) > 1:
            return None, "OPERATOR_STOP_INVALID"
        return values[0], None

    def _operator_stop_refusal(self) -> Optional[tuple[int, dict[str, Any]]]:
        """Authentication happens before the body is parsed and before any idempotency read."""

        credential = getattr(self.server, "operator_stop", None)
        if credential is None:
            return 403, _failure("OPERATOR_STOP_UNAVAILABLE")
        presented, reason = self._presented_operator_stop()
        if reason is not None:
            return 403, _failure(reason)
        if not phone_operator_stop.matches(credential, presented):
            return 403, _failure("OPERATOR_STOP_INVALID")
        return None

    def _get_activity_stop_state(self, service: phone_service.PhoneService) -> None:
        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        refusal = self._operator_stop_refusal()
        if refusal is not None:
            self._send(*refusal)
            return
        query = parse_qsl(urlsplit(self.path).query, keep_blank_values=True)
        keys = [key for key, _value in query]
        if sorted(keys) != ["activity_id"] or len(query) != 1:
            self._send(400, _failure("UNKNOWN_QUERY"))
            return
        activity_id = str(query[0][1] or "").strip()
        if not activity_id:
            self._send(400, _failure("ACTIVITY_ID_REQUIRED"))
            return
        result = service.activity_stop_state(activity_id)
        self._send(200 if result.get("ok") else http_status_for(
            str(result.get("status") or ""), result), result)

    def _post_activity_stop(self, service: phone_service.PhoneService) -> None:
        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        refusal = self._operator_stop_refusal()
        if refusal is not None:
            self._send(*refusal)
            return
        # The credential is already checked; only now is the body read and the operation key
        # considered.  A wrong token can never be turned into a replay of an earlier request.
        payload = self._read_body(OPERATOR_STOP_FIELDS)
        reason = str(payload.get("reason_code") or "").strip().upper()
        if reason not in OPERATOR_STOP_REASONS:
            self._send(409, _failure("UNKNOWN_REASON_CODE"))
            return
        revision = payload.get("expected_revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            self._send(400, _failure("EXPECTED_REVISION_INVALID"))
            return
        credential = self.server.operator_stop
        result = service.request_activity_stop(
            activity_id=payload.get("activity_id"),
            expected_revision=revision,
            operation_key=payload.get("operation_key"),
            reason_code=reason,
            epoch=str(getattr(credential, "epoch", "") or ""),
            # The credential itself travels with the request: the mailbox signature is
            # derived from it, and the Life subject it may stop is verified before anything
            # is written.  The token never leaves this call.
            stop_credential=credential,
        )
        self._send(200 if result.get("ok") else http_status_for(
            str(result.get("status") or ""), result), result)

    # -- routing -------------------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - stdlib signature
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802 - stdlib signature
        self._dispatch("POST")

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib signature
        self._dispatch("HEAD")

    def _dispatch(self, method: str) -> None:
        self._responded = False
        self._body_consumed = (method != "POST")
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
        except _CLIENT_GONE_ERRORS:
            # The caller hung up while this request was being handled (a long camera capture is
            # the realistic case).  There is no one to answer; writing a 500 would just fail again
            # and be misreported as an internal error.
            self.close_connection = True
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
            # 2026-10-28: the external-stop read surface.  Same loopback guards, plus the
            # *separate* operator credential; it answers stop/terminal facts only.
            if path == f"{API_PREFIX}/control/activity/stop/state":
                self._get_activity_stop_state(service)
                return
            if path == f"{API_PREFIX}/state":
                self._get_state(service)
                return
            # The phone's "now": a read-only projection rebuilt from the resident's own records
            # and the persisted ledger.  Opening the page never starts a service or a model.
            if path == f"{API_PREFIX}/living":
                self._get_living(service)
                return
            # What she has actually done today (the business clock's day), plus today's work
            # versions.  A pure read over the Activity authority and the project owner: it
            # writes nothing and never turns "unreadable" into "nothing happened".
            if path == f"{API_PREFIX}/today":
                self._get_today(service)
                return
            # 今天她自己排的事、外部义务与可证执行状态。同一套 host/origin 守护；纯读，
            # 不启动服务、不写结果 store。``/today`` 的既有形状与行为完全不变。
            if path == f"{API_PREFIX}/today/arrangements":
                self._get_today_arrangements(service)
                return
            # 她今天有记录的事：把已有的 Activity / 日计划 / World 动作 / 日记 / 相册 /
            # 钱包交易合成一条只读时间线。不新建 store、不补空白、不复制正文；
            # operator 默认只有骨架，她自己的工具带 actor 声明才拿到原入口引用。
            if path == f"{API_PREFIX}/today/timeline":
                self._get_day_timeline(service)
                return
            if path == f"{API_PREFIX}/today/story":
                self._get_today_story(service)
                return
            # S03: her current claims -- observed / reported / inferred, each with its
            # scope, time and status; conflicts, stale and unknown stay distinct.  A pure
            # read: no World fetch, no model, no Memory write, no action permission.
            # S04 F-049/F-050: 一条已提交决定的有限摘要、已记录的未选/延期项，以及
            # Decision 与 Adoption 的分开状态。纯读；rationale 正文在手机读面扣留，
            # 实时适用性明确 NOT_EVALUATED（不复制 AG-2 准入规则）。
            if path == f"{API_PREFIX}/decisions/review":
                self._get_decision_review(service)
                return
            if path == f"{API_PREFIX}/claims":
                self._get_claims(service)
                return
            if _CLAIM_SLOT_PATH.match(path):
                self._get_claim_slot(service, _CLAIM_SLOT_PATH.match(path).group(1))
                return
            if path == f"{API_PREFIX}/notes":
                self._send(200, service.notes())
                return
            # Her diary index: write times and lengths only.  One entry's body is a separate
            # read below, and only the subject's own tool path asks for text.
            if path == f"{API_PREFIX}/diary":
                self._get_diary_list(service)
                return
            if _DIARY_PATH.match(path):
                self._get_diary_entry(service, _DIARY_PATH.match(path).group(1))
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
            # 具身冷暖/干湿/坐姿/触觉：真实 World/Body 只读投影，加本实例显式的窗/衣物/
            # 桌边条件；没有证据的模态就是 unknown/无信号，不为了凑四类编事实。
            if path == f"{API_PREFIX}/body/somatic":
                self._get_body_somatic(service)
                return
            # 窗扇开关、局部位置与外衣穿挂：只读投影；写口只在 POST，且只属于本人。
            if path == f"{API_PREFIX}/body/garment-window":
                self._get_body_garment_window(service)
                return
            # 桌边温饮/靠垫：一个饮品槽 + 一个靠垫槽；GET 按 elapsed 派生冷却与支撑。
            if path == f"{API_PREFIX}/body/care-offering":
                self._get_body_care_offering(service)
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
            if _PROJECT_CONTEXT_PATH.match(path):
                self._get_project_context(service, _PROJECT_CONTEXT_PATH.match(path).group(1))
                return
            if _PROJECT_PATH.match(path):
                self._get_project(service, _PROJECT_PATH.match(path).group(1))
                return
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
            # 双人书桌：四态木牌与桌面物件、留存短笺与明日便签。两条都是纯读；默认视角
            # 不带任何正文/私人标题，只有本次 serve 的本地 capability 有效时才是自己视角。
            if path == f"{API_PREFIX}/desk/presence":
                self._get_desk_presence(service)
                return
            if path == f"{API_PREFIX}/desk/notes":
                self._get_desk_notes(service)
                return
            # 窗边景致 / 冰箱贴与自选锁屏 / 分日慢写的信。三个 GET 都只读：真实 World
            # 家具/灯光、世界家 personal space、当天 ambient 模板声景、相册/分享/
            # 作品版本都由原 owner 现读；没有气象权威就不编天气。
            if path == f"{API_PREFIX}/home/window":
                self._get_home_window(service)
                return
            if path == f"{API_PREFIX}/home/keepsakes":
                self._get_home_keepsakes(service)
                return
            if path == f"{API_PREFIX}/home/letters":
                self._get_home_letters(service)
                return
            # 创作托盘 / 小物履历 / 三样喜欢之物。三个 GET 都是纯读：托盘引用现读
            # album/material/project owner；小物履历只读 economy + substrate；交换匣现读
            # 已投递分享正文并受 source 治理约束。
            if path == f"{API_PREFIX}/home/workbench":
                self._get_home_workbench(service)
                return
            if path == f"{API_PREFIX}/home/curios":
                self._get_home_curios(service)
                return
            if path == f"{API_PREFIX}/home/trio-exchange":
                self._get_home_trio_exchange(service)
                return
            # 衣柜梳妆 / 歌单收藏想试小列表 / 作品旧版安全并排。衣柜与列表默认只给安全视图，
            # 本人 capability 才给私人备注与全量；旧版并排只读两个真实版本，绝不写。
            if path == f"{API_PREFIX}/home/wardrobe":
                self._get_home_wardrobe(service)
                return
            if path == f"{API_PREFIX}/pocket/lists":
                self._get_pocket_lists(service)
                return
            if path == f"{API_PREFIX}/pocket/work-revisit":
                self._get_pocket_work_revisit(service)
                return
            # 居家收官：重逢锚点 / 夜茶菜谱 / 待定小约定。三个 GET 都纯读；锚点 GET 只比对
            # 真实 owner 事实，菜谱参考区只读真实购买/消费，暂定项与正式今日安排分列。
            if path == f"{API_PREFIX}/home/reunion-anchor":
                self._get_home_reunion_anchor(service)
                return
            if path == f"{API_PREFIX}/home/recipes":
                self._get_home_recipes(service)
                return
            if path == f"{API_PREFIX}/home/tentative-plans":
                self._get_home_tentative_plans(service)
                return
            # 房间外六项生活细节：三个 GET 均纯读零写；默认 partner_default 安全视图，显式错误凭据 403。
            if path == f"{API_PREFIX}/outing/bench-bookstore":
                self._get_outing_bench_bookstore(service)
                return
            if path == f"{API_PREFIX}/outing/routes-market":
                self._get_outing_routes_market(service)
                return
            if path == f"{API_PREFIX}/outing/gallery-blueprint":
                self._get_outing_gallery_blueprint(service)
                return
            # 长程陪伴六项生活细节：三个 GET 均纯读零写；默认 partner_default 安全视图，显式错误凭据 403。
            if path == f"{API_PREFIX}/longterm/first-room-habits":
                self._get_longterm_first_room_habits(service)
                return
            if path == f"{API_PREFIX}/longterm/gifts-lantern":
                self._get_longterm_gifts_lantern(service)
                return
            if path == f"{API_PREFIX}/longterm/resume-hub":
                self._get_longterm_resume_hub(service)
                return
            # 开源角色包与多通道感官场：两个 GET 均 100% 纯读零写；默认安全视图，错误凭据 403。
            if path == f"{API_PREFIX}/character/pack":
                self._get_character_pack(service)
                return
            if path == f"{API_PREFIX}/body/sensory":
                self._get_body_sensory(service)
                return
            if path == f"{API_PREFIX}/autonomy/rhythm":
                self._get_autonomy_rhythm(service)
                return
            # 0.3 情绪脾气与日常张力 / 看法修订与物件共在 / 欲望自然消退与 36B 成年亲密：三个 GET 均 100% 纯读零写。
            if path == f"{API_PREFIX}/emotion/tension":
                self._get_emotional_tension(service)
                return
            if path == f"{API_PREFIX}/stance/containment":
                self._get_stance_containment(service)
                return
            if path == f"{API_PREFIX}/intimacy/adult":
                self._get_adult_intimacy(service)
                return
            if path == f"{API_PREFIX}/v03/clock":
                self._get_v03_clock(service)
                return
            if path == f"{API_PREFIX}/v03/ecology":
                self._get_v03_ecology(service)
                return
            if path == f"{API_PREFIX}/body/health":
                self._get_body_health(service)
                return
            if path == f"{API_PREFIX}/v03/embodied-social":
                self._get_v03_embodied_social(service)
                return
            if path == f"{API_PREFIX}/v04/neighborhood":
                self._get_v04_neighborhood(service)
                return
            if path == f"{API_PREFIX}/v04/builder-trylive":
                self._get_v04_builder_trylive(service)
                return
            if path == f"{API_PREFIX}/v04/elastic-play":
                self._get_v04_elastic_play(service)
                return
            if path == f"{API_PREFIX}/v02/agency-depth":
                self._get_v02_agency_depth(service)
                return
            if path == f"{API_PREFIX}/v02/crossmodal":
                self._get_v02_crossmodal(service)
                return
            if path == f"{API_PREFIX}/v02/runtime-migration":
                self._get_v02_runtime_migration(service)
                return
            if path == f"{API_PREFIX}/v02/honest-ux":
                self._get_v02_honest_ux(service)
                return
            if path == f"{API_PREFIX}/v02/recall-projection":
                self._get_v02_recall_projection(service)
                return
            if path == f"{API_PREFIX}/v02/common-ground":
                self._get_v02_common_ground(service)
                return
            if path == f"{API_PREFIX}/v02/ecology-growth":
                self._get_v02_ecology_growth(service)
                return
            if path == f"{API_PREFIX}/v02/world-lineage":
                self._get_v02_world_lineage(service)
                return
            if path == f"{API_PREFIX}/v05/living-town":
                self._get_v05_living_town(service)
                return
            if path == f"{API_PREFIX}/v05/inner-spark":
                self._get_v05_inner_spark(service)
                return
            if path == f"{API_PREFIX}/v05/home-craft":
                self._get_v05_home_craft(service)
                return
            if path == ROUTE_V08_LIVING_TRACES:
                self._get_v08_living_traces(service)
                return
            if path == ROUTE_V08_COMPOSABLE_BODY:
                self._get_v08_composable_body(service)
                return
            if path == ROUTE_V03_ENCOUNTERS:
                self._get_v03_encounters(service)
                return
            if path == ROUTE_V08_EMERGENT_ECHOES:
                self._get_v08_emergent_echoes(service)
                return
            if path == ROUTE_V09_LIVING_AGENCY:
                self._get_v09_living_agency(service)
                return
            if path == ROUTE_V09_WORLD_COMMITMENTS:
                self._get_v09_world_commitments(service)
                return
            if path == ROUTE_OUTBOUND_PENDING:
                self._get_outbound_pending(service)
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
            # F-140 participant identity: two guarded pure reads.  The list carries names /
            # aliases / counts (no message body); the detail adds explicit endpoint and event
            # links.  There is no write route on this surface, so a model cannot create, bind or
            # send through it.
            if path == f"{API_PREFIX}/participants":
                self._get_participants(service)
                return
            if _PARTICIPANT_PATH.match(path):
                self._get_participant(service, _PARTICIPANT_PATH.match(path).group(1))
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
            # 窗扇/外衣：只属于本人，先 CSRF 再本地凭据；不开放伴侣替她调整。
            if path == f"{API_PREFIX}/body/garment-window":
                self._post_body_garment_window(service)
                return
            # 桌边照顾：伴侣可 offer（CSRF），本人动作要 CSRF + capability；
            # 伴侣只能撤回自己尚未被动过的 offer。
            if path == f"{API_PREFIX}/body/care-offering":
                self._post_body_care_offering(service)
                return
            # 明日便签的唯一写口：显式操作者意图，走现有 FutureItems；先 CSRF，再本地凭据。
            if path == f"{API_PREFIX}/desk/tomorrow":
                self._post_desk_tomorrow(service)
                return
            # 冰箱贴/锁屏的唯一写口：只记显式选择引用，不复制正文或图片。
            if path == f"{API_PREFIX}/home/keepsakes":
                self._post_home_keepsakes(service)
                return
            if path == f"{API_PREFIX}/home/letters":
                self._post_home_letters(service)
                return
            # 创作托盘：显式放入/清空引用，只写实例自己的 JSON。
            if path == f"{API_PREFIX}/home/workbench":
                self._post_home_workbench(service)
                return
            # 交换匣：her 选择需 CSRF + capability；partner 短卡只需 CSRF。
            if path == f"{API_PREFIX}/home/trio-exchange":
                self._post_home_trio_exchange(service)
                return
            # 衣柜：本人 CSRF + capability；小列表：个人卡片 CSRF + capability，伴侣建议只
            # 需 CSRF（显式携带的 capability 仍要验证）。
            if path == f"{API_PREFIX}/home/wardrobe":
                self._post_home_wardrobe(service)
                return
            if path == f"{API_PREFIX}/pocket/lists":
                self._post_pocket_lists(service)
                return
            # 居家收官：锚点 capture 只需 CSRF（不接收客户端时间/快照）；菜谱作者侧只选择
            # 验证策略；暂定项没有任何核心安排写口。
            if path == f"{API_PREFIX}/home/reunion-anchor":
                self._post_home_reunion_anchor(service)
                return
            if path == f"{API_PREFIX}/home/recipes":
                self._post_home_recipes(service)
                return
            if path == f"{API_PREFIX}/home/tentative-plans":
                self._post_home_tentative_plans(service)
                return
            # 房间外写口：本人动作需 CSRF + capability；伴侣动作需 CSRF（显式携带凭据仍校验，错凭据 403）。
            if path == f"{API_PREFIX}/outing/bench-bookstore":
                self._post_outing_bench_bookstore(service)
                return
            if path == f"{API_PREFIX}/outing/routes-market":
                self._post_outing_routes_market(service)
                return
            if path == f"{API_PREFIX}/outing/gallery-blueprint":
                self._post_outing_gallery_blueprint(service)
                return
            # 长程陪伴写口：本人动作需 CSRF + capability；伴侣动作需 CSRF（显式携带凭据仍校验，错凭据 403）。
            if path == f"{API_PREFIX}/longterm/first-room-habits":
                self._post_longterm_first_room_habits(service)
                return
            if path == f"{API_PREFIX}/longterm/gifts-lantern":
                self._post_longterm_gifts_lantern(service)
                return
            # 开源角色包与多通道感官场写口：可选生理体验与文字声景卡写操作必须带 capability（否则 403）；
            # 预设切换/角色包导入/基础称呼偏好与伴侣感官小贴士走 CSRF，但显式携带错误凭据一律 403。
            if path == f"{API_PREFIX}/character/pack":
                self._post_character_pack(service)
                return
            if path == f"{API_PREFIX}/body/sensory":
                self._post_body_sensory(service)
                return
            if path == f"{API_PREFIX}/autonomy/rhythm":
                self._post_autonomy_rhythm(service)
                return
            if path == f"{API_PREFIX}/emotion/tension":
                self._post_emotional_tension(service)
                return
            if path == f"{API_PREFIX}/stance/containment":
                self._post_stance_containment(service)
                return
            if path == f"{API_PREFIX}/intimacy/adult":
                self._post_adult_intimacy(service)
                return
            if path == f"{API_PREFIX}/v03/clock":
                self._post_v03_clock(service)
                return
            if path == f"{API_PREFIX}/v03/ecology":
                self._post_v03_ecology(service)
                return
            if path == f"{API_PREFIX}/v03/embodied-social":
                self._post_v03_embodied_social(service)
                return
            if path == f"{API_PREFIX}/v04/neighborhood":
                self._post_v04_neighborhood(service)
                return
            if path == f"{API_PREFIX}/v04/builder-trylive":
                self._post_v04_builder_trylive(service)
                return
            if path == f"{API_PREFIX}/v04/elastic-play":
                self._post_v04_elastic_play(service)
                return
            if path == f"{API_PREFIX}/v02/agency-depth":
                self._post_v02_agency_depth(service)
                return
            if path == f"{API_PREFIX}/v02/crossmodal":
                self._post_v02_crossmodal(service)
                return
            if path == f"{API_PREFIX}/v02/runtime-migration":
                self._post_v02_runtime_migration(service)
                return
            if path == f"{API_PREFIX}/v02/honest-ux":
                self._post_v02_honest_ux(service)
                return
            if path == f"{API_PREFIX}/v02/recall-projection":
                self._post_v02_recall_projection(service)
                return
            if path == f"{API_PREFIX}/v02/common-ground":
                self._post_v02_common_ground(service)
                return
            if path == f"{API_PREFIX}/v02/ecology-growth":
                self._post_v02_ecology_growth(service)
                return
            if path == f"{API_PREFIX}/v02/world-lineage":
                self._post_v02_world_lineage(service)
                return
            if path == f"{API_PREFIX}/v05/living-town":
                self._post_v05_living_town(service)
                return
            if path == f"{API_PREFIX}/v05/inner-spark":
                self._post_v05_inner_spark(service)
                return
            if path == f"{API_PREFIX}/v05/home-craft":
                self._post_v05_home_craft(service)
                return
            if path == ROUTE_V08_LIVING_TRACES:
                self._post_v08_living_traces(service)
                return
            if path == ROUTE_V08_COMPOSABLE_BODY:
                self._post_v08_composable_body(service)
                return
            if path == ROUTE_V03_ENCOUNTERS:
                self._post_v03_encounters(service)
                return
            if path == ROUTE_V08_EMERGENT_ECHOES:
                self._post_v08_emergent_echoes(service)
                return
            if path == ROUTE_V09_LIVING_AGENCY:
                self._post_v09_living_agency(service)
                return
            if path == ROUTE_V09_WORLD_COMMITMENTS:
                self._post_v09_world_commitments(service)
                return
            if path == ROUTE_OUTBOUND_ACK:
                self._post_outbound_ack(service)
                return
            if path == f"{API_PREFIX}/control/activity/stop":
                self._post_activity_stop(service)
                return
            if path == f"{API_PREFIX}/notes":
                self._post_note(service)
                return
            if path == f"{API_PREFIX}/diary":
                self._post_diary(service)
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
            # S03: one formal observation.  The body carries an operation key only; the
            # observation itself comes from the World owner's snapshot, never from the client.
            if path == f"{API_PREFIX}/claims/observations":
                self._post_claim_observe(service)
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
            # The phone camera: one local generation from her scene text, saved through the same
            # album import.  No model cognition, no automatic capture, no second storage.
            if path == f"{API_PREFIX}/camera":
                self._post_camera(service)
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

    # -- 双人书桌 -------------------------------------------------------------------------

    def _desk_subject_view(self) -> tuple[bool, Optional[tuple[int, dict[str, Any]]]]:
        """Absent credential = default partner/operator view; wrong credential never degrades.

        ``X-Phone-Actor`` alone is not authority here (same rule as the private diary).  A
        credential that *is* presented must match this serve, otherwise the answer is a 403 --
        never a quiet fallback to the private view or to the body-less one.
        """

        capability = getattr(self.server, "capability", None)
        presented, reason = self._presented_capability()
        if reason == "CAPABILITY_REQUIRED":
            return False, None
        if reason is not None:
            return False, (403, _failure(reason))
        if capability is None:
            return False, (403, _failure("CAPABILITY_UNAVAILABLE"))
        if not phone_capability.matches(capability, presented):
            return False, (403, _failure("CAPABILITY_INVALID"))
        return True, None

    def _get_desk_presence(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/desk/presence``: door sign + desk objects, pure read."""

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        self._send_or_map(service.desk_presence())

    def _get_desk_notes(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/desk/notes``: retained short notes + tomorrow items, pure read."""

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        subject_view, refusal = self._desk_subject_view()
        if refusal is not None:
            self._send(*refusal)
            return
        self._send_or_map(service.desk_notes(subject_view=subject_view))

    def _post_desk_tomorrow(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/desk/tomorrow``: one explicit tomorrow-item write."""

        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        refusal = self._capability_refusal()
        if refusal is not None:
            self._send(*refusal)
            return
        payload = self._read_body(DESK_TOMORROW_FIELDS)
        action = _clean_text(payload.get("action"), 20, "DESK_ACTION_REQUIRED").lower()
        if action not in DESK_ACTIONS:
            raise PhoneRequestError("DESK_ACTION_UNSUPPORTED")
        allowed = {
            "propose": frozenset({"action", "title", "due_at", "due_in_minutes"}),
            "cancel": frozenset({"action", "item_ref", "reason"}),
            "reschedule": frozenset({"action", "item_ref", "due_at", "due_in_minutes",
                                     "reason"}),
        }[action]
        extra = sorted(key for key in payload if key not in allowed
                       and payload.get(key) not in (None, ""))
        if extra:
            raise PhoneRequestError("UNKNOWN_FIELD:%s" % ",".join(extra))
        title = None
        item_ref = None
        due_at = None
        minutes = None
        reason = None
        if action == "propose":
            title = _clean_text(payload.get("title"), 120, "FUTURE_ITEM_TITLE_INVALID")
        else:
            item_ref = _clean_text(payload.get("item_ref"), 200, "ITEM_REF_INVALID")
        if action in ("propose", "reschedule"):
            due_at, minutes = _desk_due_fields(payload)
        if payload.get("reason") not in (None, ""):
            reason = _clean_text(payload.get("reason"), 120, "REASON_INVALID")
        result = service.desk_tomorrow_write(
            action=action, title=title, item_ref=item_ref, due_at=due_at,
            due_in_minutes=minutes, reason=reason)
        self._send_or_map(result)

    # -- 具身冷暖 / 窗扇与外衣 / 桌边照顾 ---------------------------------------------

    def _get_body_somatic(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/body/somatic``: bounded four-modality read, zero writes."""

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        refusal = self._optional_capability_refusal()
        if refusal is not None:
            self._send(*refusal)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        self._send_or_map(service.body_somatic())

    def _get_body_garment_window(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/body/garment-window``: local window/garment conditions."""

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        refusal = self._optional_capability_refusal()
        if refusal is not None:
            self._send(*refusal)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        self._send_or_map(service.body_garment_window())

    def _get_body_care_offering(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/body/care-offering``: the two local slots, owner re-read."""

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        refusal = self._optional_capability_refusal()
        if refusal is not None:
            self._send(*refusal)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        self._send_or_map(service.body_care_offering())

    def _post_body_garment_window(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/body/garment-window``: her own window/position/outerwear."""

        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        refusal = self._capability_refusal()
        if refusal is not None:
            self._send(*refusal)
            return
        payload = self._read_body(SOMATIC_GARMENT_WINDOW_FIELDS, SOMATIC_REFUSED_FIELDS)
        action = _clean_text(payload.get("action"), 40,
                             "GARMENT_WINDOW_ACTION_REQUIRED").lower()
        if action not in SOMATIC_GARMENT_WINDOW_ACTIONS:
            raise PhoneRequestError("GARMENT_WINDOW_ACTION_UNSUPPORTED")
        allowed = {
            "set_window": frozenset({"action", "state"}),
            "set_local_position": frozenset({"action", "zone"}),
            "wear_outerwear": frozenset({"action", "garment_id", "label", "dryness"}),
            "hang_outerwear": frozenset({"action", "garment_id", "hang_position",
                                         "dryness"}),
        }[action]
        # Presence, not truthiness: ``state: ""`` / ``zone: null`` / ``label: []`` still
        # names an intent this action never performs.
        extra = sorted(set(payload) - allowed)
        if extra:
            raise PhoneRequestError("UNKNOWN_FIELD:%s" % ",".join(extra))
        if action == "set_window":
            state_value = _world_choice(payload.get("state"), SOMATIC_WINDOW_STATES,
                                        "WINDOW_STATE_INVALID")
            result = service.body_garment_window_action(action=action,
                                                        state_value=state_value)
        elif action == "set_local_position":
            zone = _world_choice(payload.get("zone"), SOMATIC_LOCAL_ZONES,
                                 "LOCAL_ZONE_INVALID")
            result = service.body_garment_window_action(action=action, zone=zone)
        elif action == "wear_outerwear":
            garment_id = _optional_local_id(payload.get("garment_id"),
                                            "GARMENT_ID_INVALID")
            if garment_id is None:
                raise PhoneRequestError("GARMENT_ID_REQUIRED")
            label = _somatic_optional_label(payload.get("label"))
            dryness = _somatic_optional_choice(payload.get("dryness"), SOMATIC_DRYNESS,
                                               "GARMENT_DRYNESS_INVALID")
            result = service.body_garment_window_action(
                action=action, garment_id=garment_id, label=label, dryness=dryness)
        else:
            garment_id = _optional_local_id(payload.get("garment_id"),
                                            "GARMENT_ID_INVALID")
            if garment_id is None:
                raise PhoneRequestError("GARMENT_ID_REQUIRED")
            hang_position = _optional_local_id(payload.get("hang_position"),
                                               "HANG_POSITION_INVALID")
            if hang_position is None:
                raise PhoneRequestError("HANG_POSITION_REQUIRED")
            dryness = _somatic_optional_choice(payload.get("dryness"), SOMATIC_DRYNESS,
                                               "GARMENT_DRYNESS_INVALID")
            result = service.body_garment_window_action(
                action=action, garment_id=garment_id, hang_position=hang_position,
                dryness=dryness)
        self._send_or_map(result)

    def _post_body_care_offering(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/body/care-offering``: offer / take / drink / use / stow / withdraw."""

        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        payload = self._read_body(SOMATIC_CARE_FIELDS, SOMATIC_REFUSED_FIELDS)
        action = _clean_text(payload.get("action"), 40, "CARE_ACTION_REQUIRED").lower()
        if action not in SOMATIC_CARE_ACTIONS:
            raise PhoneRequestError("CARE_ACTION_UNSUPPORTED")
        slot = _world_choice(payload.get("slot"), SOMATIC_CARE_SLOTS, "CARE_SLOT_INVALID")
        allowed = (frozenset({"action", "slot", "kind", "actor_side", "position"})
                   if action == "offer" else frozenset({"action", "slot"}))
        extra = sorted(set(payload) - allowed)
        if extra:
            raise PhoneRequestError("UNKNOWN_FIELD:%s" % ",".join(extra))
        if action == "offer":
            actor_side = _world_choice(payload.get("actor_side"), SOMATIC_CARE_ACTORS,
                                       "CARE_ACTOR_INVALID")
            kind = _world_choice(payload.get("kind"), SOMATIC_CARE_KINDS[slot],
                                 "CARE_KIND_INVALID")
            position = _world_choice(payload.get("position"), SOMATIC_CARE_POSITIONS,
                                     "CARE_POSITION_INVALID")
            if actor_side == "her":
                refusal = self._capability_refusal()
            else:
                refusal = self._partner_gate_passed()
            if refusal is not None:
                self._send(*refusal)
                return
            result = service.body_care_action(action=action, slot=slot,
                                              actor_side=actor_side, kind=kind,
                                              position=position)
        else:
            if action == "withdraw":
                # A partner withdraws their own untouched offer; a presented credential
                # must still verify rather than silently downgrade.
                refusal = self._partner_gate_passed()
            else:
                # take / drink / use / stow are hers: CSRF + this serve's capability.
                refusal = self._capability_refusal()
            if refusal is not None:
                self._send(*refusal)
                return
            result = service.body_care_action(action=action, slot=slot)
        self._send_or_map(result)

    # -- 窗边景致 / 冰箱贴与自选锁屏 / 分日慢写的信 -----------------------------------

    def _get_home_window(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/home/window``: real home plants/light/personal space, pure read."""

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        self._send_or_map(service.home_window())

    def _get_home_keepsakes(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/home/keepsakes``: lockscreen/pins, owner re-read, pure read."""

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        subject_view, refusal = self._desk_subject_view()
        if refusal is not None:
            self._send(*refusal)
            return
        self._send_or_map(service.home_keepsakes(subject_view=subject_view))

    def _get_home_letters(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/home/letters``: cross-day share/source projection, pure read."""

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        subject_view, refusal = self._desk_subject_view()
        if refusal is not None:
            self._send(*refusal)
            return
        self._send_or_map(service.home_letters(subject_view=subject_view))

    def _read_raw_json_object(self, *, max_bytes: int = MAX_BODY_BYTES) -> dict[str, Any]:
        content_type = str(self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type != "application/json":
            raise PhoneRequestError("CONTENT_TYPE_REQUIRED")
        raw_length = self.headers.get("Content-Length")
        if raw_length is None or not str(raw_length).strip().isdigit():
            raise PhoneRequestError("CONTENT_LENGTH_REQUIRED")
        length = int(str(raw_length).strip())
        if length <= 0:
            raise PhoneRequestError("BODY_REQUIRED")
        if length > int(max_bytes):
            raise PhoneRequestError("BODY_TOO_LARGE", 413)
        raw = self.rfile.read(length)
        self._body_consumed = True
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise PhoneRequestError("BODY_NOT_JSON") from None
        if not isinstance(payload, dict):
            raise PhoneRequestError("BODY_NOT_AN_OBJECT")
        return payload

    def _dispatch_slow_letter_draft_action(
        self,
        service: phone_service.PhoneService,
        payload: Mapping[str, Any],
        *,
        actor_side: str,
    ) -> None:
        from . import window_keepsake_view as wk_view

        smuggled = sorted(set(payload) & SLOW_LETTER_SMUGGLED_FIELDS)
        if smuggled:
            raise PhoneRequestError("CLIENT_FIELD_FORBIDDEN:%s" % ",".join(smuggled), 400)
        action = _clean_text(payload.get("action"), 40, "SLOW_LETTER_ACTION_REQUIRED").lower()
        if action not in wk_view.SLOW_LETTER_DRAFT_ACTIONS:
            raise PhoneRequestError("SLOW_LETTER_ACTION_UNSUPPORTED", 400)
        allowed = wk_view.ACTION_ALLOWED_FIELDS[action]
        extra = sorted(set(payload) - allowed)
        if extra:
            raise PhoneRequestError("UNKNOWN_FIELD:%s" % ",".join(extra), 400)
        for key, val in payload.items():
            if isinstance(val, (dict, list)):
                raise PhoneRequestError("INVALID_FIELD_TYPE:%s" % key, 400)
        self._send_or_map(service.home_slow_letter_draft_action(
            action=action,
            actor_side=actor_side,
            payload=payload,
        ))

    def _post_home_letters(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/home/letters``: 72h cooling slow-letter draft lifecycle."""

        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        subject_view, cap_refusal = self._desk_subject_view()
        if cap_refusal is not None:
            self._send(*cap_refusal)
            return
        payload = self._read_raw_json_object()
        actor_side = "subject" if subject_view else "partner"
        self._dispatch_slow_letter_draft_action(service, payload, actor_side=actor_side)

    def _post_home_keepsakes(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/home/keepsakes``: select/clear/pin/unpin + slow-letter draft actions."""

        from . import window_keepsake_view as wk_view

        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        subject_view, cap_refusal = self._desk_subject_view()
        if cap_refusal is not None:
            self._send(*cap_refusal)
            return
        payload = self._read_raw_json_object()
        raw_action = payload.get("action")
        if (
            isinstance(raw_action, str)
            and raw_action.strip().lower() in wk_view.SLOW_LETTER_DRAFT_ACTIONS
        ):
            actor_side = "subject" if subject_view else "partner"
            self._dispatch_slow_letter_draft_action(service, payload, actor_side=actor_side)
            return
        if not subject_view:
            refusal = self._capability_refusal()
            if refusal is not None:
                self._send(*refusal)
                return
        identifiers = sorted(set(payload) & IDENTITY_FIELDS)
        if identifiers:
            raise PhoneRequestError("CLIENT_IDENTITY_REFUSED:%s" % ",".join(identifiers), 409)
        addressing = sorted(set(payload) & set(WINDOW_KEEPSAKE_REFUSED_FIELDS))
        if addressing:
            raise PhoneRequestError("SHARE_ADDRESSING_REFUSED:%s" % ",".join(addressing), 409)
        unknown = sorted(set(payload) - set(WINDOW_KEEPSAKE_FIELDS))
        if unknown:
            raise PhoneRequestError("UNKNOWN_FIELD:%s" % ",".join(unknown), 400)
        action = _clean_text(payload.get("action"), 40, "KEEPSAKE_ACTION_REQUIRED").lower()
        if action not in WINDOW_KEEPSAKE_ACTIONS:
            raise PhoneRequestError("KEEPSAKE_ACTION_UNSUPPORTED")
        allowed = {
            "select_lockscreen": frozenset({"action", "album_artifact_id"}),
            "clear_lockscreen": frozenset({"action"}),
            "pin_share": frozenset({"action", "share_id"}),
            "unpin_share": frozenset({"action", "share_id"}),
        }[action]
        extra = sorted(set(payload) - allowed)
        if extra:
            raise PhoneRequestError("UNKNOWN_FIELD:%s" % ",".join(extra))
        album_artifact_id = None
        share_id = None
        if action == "select_lockscreen":
            album_artifact_id = _owner_path_id(payload.get("album_artifact_id"),
                                               "LOCKSCREEN_IMAGE_INVALID")
        elif action in ("pin_share", "unpin_share"):
            share_id = _share_id(payload.get("share_id"))
        self._send_or_map(service.home_keepsake_action(
            action=action, album_artifact_id=album_artifact_id, share_id=share_id))

    def _get_home_workbench(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/home/workbench``: the tray, re-read from real owners."""

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        subject_view, refusal = self._desk_subject_view()
        if refusal is not None:
            self._send(*refusal)
            return
        self._send_or_map(service.home_workbench(subject_view=subject_view))

    def _get_home_curios(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/home/curios``: owned instances + real positions, pure read."""

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        self._send_or_map(service.home_curios())

    def _get_home_trio_exchange(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/home/trio-exchange``: delivered picks + partner cards, pure read."""

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        self._send_or_map(service.home_trio_exchange())

    def _post_home_workbench(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/home/workbench``: one explicit set/clear of tray references."""

        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        refusal = self._capability_refusal()
        if refusal is not None:
            self._send(*refusal)
            return
        payload = self._read_body(WORKBENCH_FIELDS, WORKBENCH_REFUSED_FIELDS)
        action = _clean_text(payload.get("action"), 40, "TRAY_ACTION_REQUIRED").lower()
        if action not in WORKBENCH_ACTIONS:
            raise PhoneRequestError("TRAY_ACTION_UNSUPPORTED")
        allowed = {
            "set_tray": frozenset({"action", "album_artifact_ids", "material_ids",
                                   "work_artifact_id", "note"}),
            "clear_tray": frozenset({"action"}),
        }[action]
        # A field this action does not take is refused by presence, not by truthiness:
        # ``[]`` / ``null`` / ``""`` still name an intent the action never performs.
        extra = sorted(set(payload) - allowed)
        if extra:
            raise PhoneRequestError("UNKNOWN_FIELD:%s" % ",".join(extra))
        result = service.home_workbench_action(
            action=action,
            album_artifact_ids=payload.get("album_artifact_ids"),
            material_ids=payload.get("material_ids"),
            work_artifact_id=payload.get("work_artifact_id"),
            note=payload.get("note"))
        self._send_or_map(result)

    def _post_home_trio_exchange(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/home/trio-exchange``: her picks (CSRF+cap) / partner cards (CSRF)."""

        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        payload = self._read_body(TRIO_FIELDS)
        action = _clean_text(payload.get("action"), 40, "TRIO_ACTION_REQUIRED").lower()
        if action not in TRIO_ACTIONS:
            raise PhoneRequestError("TRIO_ACTION_UNSUPPORTED")
        if action == "set_her_picks":
            refusal = self._capability_refusal()
            if refusal is not None:
                self._send(*refusal)
                return
            # Presence, not truthiness: ``partner_picks: []`` / ``null`` / ``""`` carried by a
            # her-pick action is still a field that action does not take.
            if "partner_picks" in payload:
                raise PhoneRequestError("UNKNOWN_FIELD:partner_picks")
            result = service.home_trio_exchange_action(
                action=action, her_picks=payload.get("her_picks"))
        else:
            # Partner cards need only CSRF.  A credential that *is* presented must still be
            # this serve's, otherwise the request is refused instead of silently downgraded.
            _presented, reason = self._presented_capability()
            if reason != "CAPABILITY_REQUIRED":
                refusal = self._capability_refusal()
                if refusal is not None:
                    self._send(*refusal)
                    return
            if "her_picks" in payload:
                raise PhoneRequestError("UNKNOWN_FIELD:her_picks")
            result = service.home_trio_exchange_action(
                action=action, partner_picks=payload.get("partner_picks"))
        self._send_or_map(result)

    # -- 衣柜梳妆 / 歌单收藏想试小列表 / 作品旧版安全并排 ---------------------------------

    def _get_home_wardrobe(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/home/wardrobe``: styling choices + real ownership checks."""

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        subject_view, refusal = self._desk_subject_view()
        if refusal is not None:
            self._send(*refusal)
            return
        self._send_or_map(service.home_wardrobe(subject_view=subject_view))

    def _post_home_wardrobe(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/home/wardrobe``: one explicit set/clear of her selection."""

        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        refusal = self._capability_refusal()
        if refusal is not None:
            self._send(*refusal)
            return
        payload = self._read_body(WARDROBE_FIELDS, WARDROBE_REFUSED_FIELDS)
        action = _clean_text(payload.get("action"), 40, "WARDROBE_ACTION_REQUIRED").lower()
        if action not in WARDROBE_ACTIONS:
            raise PhoneRequestError("WARDROBE_ACTION_UNSUPPORTED")
        if action == "set":
            allowed = frozenset({"action"}) | WARDROBE_SET_FIELDS
            # Presence, not truthiness: ``fields`` carried by a set action is not a field it takes.
            extra = sorted(set(payload) - allowed)
            if extra:
                raise PhoneRequestError("UNKNOWN_FIELD:%s" % ",".join(extra))
            values: dict[str, Any] = {}
            if "homewear_style" in payload:
                values["homewear_style"] = _clean_text(
                    payload.get("homewear_style"), 120, "WARDROBE_STYLE_REQUIRED")
            if "outdoor_style" in payload:
                values["outdoor_style"] = _clean_text(
                    payload.get("outdoor_style"), 120, "WARDROBE_STYLE_REQUIRED")
            if "hairband_style" in payload:
                values["hairband_style"] = _clean_text(
                    payload.get("hairband_style"), 120, "WARDROBE_STYLE_REQUIRED")
            if "hairclip_style" in payload:
                values["hairclip_style"] = _clean_text(
                    payload.get("hairclip_style"), 120, "WARDROBE_STYLE_REQUIRED")
            for field in ("homewear_item_instance_id", "outdoor_item_instance_id",
                          "hairband_item_instance_id", "hairclip_item_instance_id"):
                if field in payload:
                    item_id = _optional_local_id(payload.get(field),
                                                 "WARDROBE_ITEM_ID_INVALID")
                    if item_id is None:
                        raise PhoneRequestError("WARDROBE_ITEM_ID_INVALID")
                    values[field] = item_id
            if "pairing_note" in payload:
                values["pairing_note"] = _clean_text(
                    payload.get("pairing_note"), 120, "WARDROBE_NOTE_REQUIRED")
            for field in ("blanket_season", "cushion_season"):
                if field in payload:
                    values[field] = _world_choice(
                        payload.get(field), frozenset({"summer", "spring_autumn", "winter"}),
                        "WARDROBE_SEASON_INVALID")
            if not values:
                raise PhoneRequestError("WARDROBE_FIELDS_REQUIRED")
            self._send_or_map(service.home_wardrobe_action(action=action, values=values))
            return
        extra = sorted(set(payload) - {"action", "fields"})
        if extra:
            raise PhoneRequestError("UNKNOWN_FIELD:%s" % ",".join(extra))
        fields = payload.get("fields")
        if not isinstance(fields, list) or not fields:
            raise PhoneRequestError("WARDROBE_CLEAR_FIELDS_REQUIRED")
        cleaned: list[str] = []
        for field in fields:
            if not isinstance(field, str) or field not in WARDROBE_SET_FIELDS:
                raise PhoneRequestError("WARDROBE_CLEAR_FIELD_INVALID")
            if field in cleaned:
                raise PhoneRequestError("WARDROBE_CLEAR_FIELD_DUPLICATED")
            cleaned.append(field)
        self._send_or_map(service.home_wardrobe_action(action=action, fields=cleaned))

    def _get_pocket_lists(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/pocket/lists``: shared cards + partner suggestions, pure read."""

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        subject_view, refusal = self._desk_subject_view()
        if refusal is not None:
            self._send(*refusal)
            return
        self._send_or_map(service.pocket_lists(subject_view=subject_view))

    def _pocket_suggestion_rows(self, raw: Any) -> list[dict[str, Any]]:
        if not isinstance(raw, list):
            raise PhoneRequestError("POCKET_SUGGESTIONS_INVALID")
        if len(raw) > 3:
            raise PhoneRequestError("POCKET_PARTNER_TOO_MANY")
        rows: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                raise PhoneRequestError("POCKET_SUGGESTIONS_INVALID")
            extra = sorted(set(item) - POCKET_SUGGESTION_FIELDS)
            if extra:
                raise PhoneRequestError("UNKNOWN_SUGGESTION_FIELD:%s" % ",".join(extra))
            title = _clean_text(item.get("title"), 60, "POCKET_PARTNER_TITLE_REQUIRED")
            note = None
            if "note" in item:
                note = _clean_text(item.get("note"), 120, "POCKET_PARTNER_NOTE_INVALID")
            rows.append({"title": title, "note": note})
        return rows

    def _post_pocket_lists(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/pocket/lists``: personal cards (CSRF+cap) / partner cards (CSRF)."""

        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        payload = self._read_body(POCKET_FIELDS, POCKET_REFUSED_FIELDS)
        action = _clean_text(payload.get("action"), 40, "POCKET_ACTION_REQUIRED").lower()
        if action not in POCKET_ACTIONS:
            raise PhoneRequestError("POCKET_ACTION_UNSUPPORTED")
        if action == "set_partner_suggestions":
            # Partner write: CSRF only, but a presented credential must still verify.
            _presented, reason = self._presented_capability()
            if reason != "CAPABILITY_REQUIRED":
                refusal = self._capability_refusal()
                if refusal is not None:
                    self._send(*refusal)
                    return
            # A partner card is not a way to name her list, an entry, a shared flag or a
            # personal field; every field other than action+suggestions is refused by presence
            # even when empty/null.
            extra = sorted(set(payload) - {"action", "suggestions"})
            if extra:
                raise PhoneRequestError("UNKNOWN_FIELD:%s" % ",".join(extra))
            if "suggestions" not in payload:
                raise PhoneRequestError("POCKET_SUGGESTIONS_REQUIRED")
            rows = self._pocket_suggestion_rows(payload.get("suggestions"))
            self._send_or_map(service.pocket_lists_action(
                action=action, suggestions=rows))
            return

        refusal = self._capability_refusal()
        if refusal is not None:
            self._send(*refusal)
            return
        if "suggestions" in payload:
            raise PhoneRequestError("UNKNOWN_FIELD:suggestions")
        kind = _world_choice(payload.get("list"), frozenset({"playlist", "favorites",
                                                             "want_to_try"}),
                             "POCKET_LIST_INVALID")
        status = None
        if "status" in payload:
            status = _clean_text(payload.get("status"), 40, "POCKET_STATUS_REQUIRED")
            if status not in POCKET_LIST_STATUSES[kind]:
                raise PhoneRequestError("POCKET_STATUS_NOT_FOR_LIST")
        shared = None
        if "shared" in payload:
            shared = _strict_boolean(payload.get("shared"), "POCKET_SHARED_INVALID")
        if action == "add":
            allowed = frozenset({"action", "list", "title", "status", "shared",
                                 "operation_key"})
            if kind == "playlist":
                allowed = allowed | {"reason"}
            elif kind == "favorites":
                allowed = allowed | {"expectation", "verdict"}
            else:
                allowed = allowed | {"note"}
            extra = sorted(set(payload) - allowed)
            if extra:
                raise PhoneRequestError("UNKNOWN_FIELD:%s" % ",".join(extra))
            values: dict[str, Any] = {
                "title": _clean_text(payload.get("title"), 80, "POCKET_TITLE_REQUIRED")}
            for field in ("reason", "expectation", "verdict", "note"):
                if field in payload:
                    values[field] = _clean_text(payload.get(field), 120,
                                                "POCKET_%s_INVALID" % field.upper())
            key = _clean_text(payload.get("operation_key"), 120,
                              "POCKET_OPERATION_KEY_REQUIRED")
            self._send_or_map(service.pocket_lists_action(
                action=action, kind=kind, values=values, status=status, shared=shared,
                operation_key=key))
            return
        if action == "update":
            entry_id = _pocket_entry_id(payload.get("entry_id"))
            allowed = frozenset({"action", "list", "entry_id", "title", "status", "shared"})
            if kind == "playlist":
                allowed = allowed | {"reason"}
            elif kind == "favorites":
                allowed = allowed | {"expectation", "verdict"}
            else:
                allowed = allowed | {"note"}
            extra = sorted(set(payload) - allowed)
            if extra:
                raise PhoneRequestError("UNKNOWN_FIELD:%s" % ",".join(extra))
            values = {}
            if "title" in payload:
                values["title"] = _clean_text(payload.get("title"), 80,
                                              "POCKET_TITLE_REQUIRED")
            for field in ("reason", "expectation", "verdict", "note"):
                if field in payload:
                    values[field] = _clean_text(payload.get(field), 120,
                                                "POCKET_%s_INVALID" % field.upper())
            self._send_or_map(service.pocket_lists_action(
                action=action, kind=kind, entry_id=entry_id, values=values,
                status=status, shared=shared))
            return
        entry_id = _pocket_entry_id(payload.get("entry_id"))
        extra = sorted(set(payload) - {"action", "list", "entry_id"})
        if extra:
            raise PhoneRequestError("UNKNOWN_FIELD:%s" % ",".join(extra))
        self._send_or_map(service.pocket_lists_action(
            action=action, kind=kind, entry_id=entry_id))

    def _work_revisit_query(self) -> tuple[str, str, str]:
        """Exactly three named ids, each once; no default and no "latest work" guess."""

        pairs = parse_qsl(urlsplit(self.path).query, keep_blank_values=True)
        keys = [key for key, _value in pairs]
        unknown = sorted(set(keys) - WORK_REVISIT_QUERY_FIELDS)
        if unknown:
            raise PhoneRequestError("UNKNOWN_QUERY:%s" % ",".join(unknown))
        values: dict[str, str] = {}
        for key, value in pairs:
            if key in values:
                raise PhoneRequestError("QUERY_REPEATED:%s" % key)
            text = str(value).strip()
            if not text:
                raise PhoneRequestError("WORK_REVISIT_QUERY_REQUIRED:%s" % key)
            values[key] = text
        missing = sorted(WORK_REVISIT_QUERY_FIELDS - set(values))
        if missing:
            raise PhoneRequestError("WORK_REVISIT_QUERY_REQUIRED:%s" % ",".join(missing))
        return values["artifact_id"], values["left_version_id"], values["right_version_id"]

    def _get_pocket_work_revisit(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/pocket/work-revisit``: two real versions, bounded summaries."""

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        artifact_id, left_version_id, right_version_id = self._work_revisit_query()
        artifact_id = _owner_path_id(artifact_id, "ARTIFACT_ID_INVALID")
        left_version_id = _version_path_id(left_version_id)
        right_version_id = _version_path_id(right_version_id)
        if left_version_id == right_version_id:
            raise PhoneRequestError("VERSIONS_NOT_DISTINCT")
        subject_view, refusal = self._desk_subject_view()
        if refusal is not None:
            self._send(*refusal)
            return
        self._send_or_map(service.pocket_work_revisit(
            artifact_id=artifact_id, left_version_id=left_version_id,
            right_version_id=right_version_id, subject_view=subject_view))

    # -- 居家收官：重逢锚点 / 夜茶菜谱 / 待定小约定 -------------------------------------

    def _get_home_reunion_anchor(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/home/reunion-anchor``: stored anchor vs current owners, pure read."""

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        self._send_or_map(service.home_reunion_anchor())

    def _get_home_recipes(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/home/recipes``: shared cards + a bounded owner reference area."""

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        self._send_or_map(service.home_recipes())

    def _get_home_tentative_plans(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/home/tentative-plans``: safe arrangement summary + local plans."""

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        subject_view, refusal = self._desk_subject_view()
        if refusal is not None:
            self._send(*refusal)
            return
        self._send_or_map(service.home_tentative_plans(subject_view=subject_view))

    def _post_home_reunion_anchor(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/home/reunion-anchor``: one explicit safe capture (CSRF only)."""

        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        # CSRF-only still refuses a credential that is presented but is not this serve's.
        refusal = self._partner_gate_passed()
        if refusal is not None:
            self._send(*refusal)
            return
        payload = self._read_body(REUNION_FIELDS)
        action = _clean_text(payload.get("action"), 40, "REUNION_ACTION_REQUIRED").lower()
        if action not in REUNION_ACTIONS:
            raise PhoneRequestError("REUNION_ACTION_UNSUPPORTED")
        extra = sorted(set(payload) - {"action"})
        if extra:
            raise PhoneRequestError("UNKNOWN_FIELD:%s" % ",".join(extra))
        self._send_or_map(service.home_reunion_capture())

    def _optional_capability_refusal(self) -> Optional[tuple[int, dict[str, Any]]]:
        """A read that keeps a safe default view may omit the credential.

        An absent credential keeps that default path; a malformed / wrong / unavailable one
        is a 403 and never a quiet downgrade to it.  The same constant-time check the write
        doors use.
        """

        presented, reason = self._presented_capability()
        if reason == "CAPABILITY_REQUIRED":
            return None
        return self._capability_refusal()

    def _partner_gate_passed(self) -> Optional[tuple[int, dict[str, Any]]]:
        """Partner writes need only CSRF.  A capability that *is* presented must verify.

        An absent credential is the partner path; a malformed / wrong / unavailable one is a
        403 and never a quiet downgrade to "partner".
        """

        return self._optional_capability_refusal()

    def _post_home_recipes(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/home/recipes``: one explicit create/update of a shared card."""

        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        payload = self._read_body(RECIPE_FIELDS)
        action = _clean_text(payload.get("action"), 40, "RECIPE_ACTION_REQUIRED").lower()
        if action not in RECIPE_ACTIONS:
            raise PhoneRequestError("RECIPE_ACTION_UNSUPPORTED")
        extra = sorted(set(payload) - {"action", "recipe_id", "title", "memo", "author_side"})
        if extra:
            raise PhoneRequestError("UNKNOWN_FIELD:%s" % ",".join(extra))
        author_side = _clean_text(payload.get("author_side"), 20,
                                  "RECIPE_AUTHOR_REQUIRED").lower()
        if author_side not in ("her", "partner"):
            raise PhoneRequestError("RECIPE_AUTHOR_INVALID")
        if author_side == "her":
            refusal = self._capability_refusal()
        else:
            refusal = self._partner_gate_passed()
        if refusal is not None:
            self._send(*refusal)
            return
        recipe_id = _optional_local_id(payload.get("recipe_id"), "RECIPE_ID_INVALID")
        title = _clean_text(payload.get("title"), 40, "RECIPE_TITLE_INVALID")
        memo = _clean_text(payload.get("memo"), 240, "RECIPE_MEMO_INVALID")
        self._send_or_map(service.home_recipe_upsert(
            recipe_id=recipe_id, title=title, memo=memo, author_side=author_side))

    def _post_home_tentative_plans(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/home/tentative-plans``: propose / reschedule / drop."""

        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        payload = self._read_body(PLAN_FIELDS)
        action = _clean_text(payload.get("action"), 40, "PLAN_ACTION_REQUIRED").lower()
        if action not in PLAN_ACTIONS:
            raise PhoneRequestError("PLAN_ACTION_UNSUPPORTED")
        allowed = {
            "propose": {"action", "plan_id", "title", "window_note", "proposer"},
            "reschedule": {"action", "plan_id", "window_note"},
            "drop": {"action", "plan_id"},
        }[action]
        extra = sorted(set(payload) - allowed)
        if extra:
            raise PhoneRequestError("UNKNOWN_FIELD:%s" % ",".join(extra))
        plan_id = _optional_local_id(payload.get("plan_id"), "PLAN_ID_INVALID")
        if action == "propose":
            proposer = _clean_text(payload.get("proposer"), 20,
                                   "PLAN_PROPOSER_REQUIRED").lower()
            if proposer not in ("her", "partner"):
                raise PhoneRequestError("PLAN_PROPOSER_INVALID")
            if proposer == "her":
                refusal = self._capability_refusal()
            else:
                refusal = self._partner_gate_passed()
            if refusal is not None:
                self._send(*refusal)
                return
            title = _clean_text(payload.get("title"), 60, "PLAN_TITLE_INVALID")
            window_note = None
            if payload.get("window_note") is not None:
                if not isinstance(payload.get("window_note"), str):
                    raise PhoneRequestError("PLAN_WINDOW_INVALID")
                window_note = payload["window_note"].strip()
                if len(window_note) > 40 or any(ch in window_note for ch in ("\x00", "\r")):
                    raise PhoneRequestError("PLAN_WINDOW_INVALID")
            result = service.home_tentative_plan_action(
                action="propose", actor_is_her=(proposer == "her"),
                plan_id=plan_id, title=title, window_note=window_note,
                proposer=proposer)
            self._send_or_map(result)
            return
        if plan_id is None:
            raise PhoneRequestError("PLAN_ID_REQUIRED")
        actor_is_her = False
        presented, reason = self._presented_capability()
        if reason not in (None, "CAPABILITY_REQUIRED"):
            self._send(403, _failure(reason))
            return
        if reason is None:
            capability = getattr(self.server, "capability", None)
            if capability is None:
                self._send(403, _failure("CAPABILITY_UNAVAILABLE"))
                return
            if not phone_capability.matches(capability, presented):
                self._send(403, _failure("CAPABILITY_INVALID"))
                return
            actor_is_her = True
        window_note = None
        if action == "reschedule":
            window_note = _clean_text(payload.get("window_note"), 40, "PLAN_WINDOW_INVALID")
        self._send_or_map(service.home_tentative_plan_action(
            action=action, actor_is_her=actor_is_her, plan_id=plan_id,
            window_note=window_note))

    # -- 房间外日常：长椅书店 / 散步路线节日集市 / 可重访作品展与共同新地点草图 ----------

    def _get_outing_bench_bookstore(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/outing/bench-bookstore``: pure read, 403 on bad capability."""

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        subject_view, refusal = self._desk_subject_view()
        if refusal is not None:
            self._send(*refusal)
            return
        self._send_or_map(service.outing_bench_bookstore(subject_view=subject_view))

    def _post_outing_bench_bookstore(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/outing/bench-bookstore``: bench trace & bookstore window."""

        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        # Any explicitly presented capability must verify first, even on partner actions.
        opt_refusal = self._optional_capability_refusal()
        if opt_refusal is not None:
            self._send(*opt_refusal)
            return
        payload = self._read_body(OUTING_BENCH_BOOKSTORE_FIELDS)
        action = _clean_text(payload.get("action"), 40,
                             "BENCH_BOOKSTORE_ACTION_REQUIRED").lower()
        if action not in OUTING_BENCH_BOOKSTORE_ACTIONS:
            raise PhoneRequestError("BENCH_BOOKSTORE_ACTION_UNSUPPORTED")

        allowed_by_action = {
            "log_bench_visit": {"action", "place_id", "note", "shared", "operation_key"},
            "set_bench_visibility": {"action", "visit_id", "shared"},
            "bookmark_window_book": {"action", "slot_index", "shared", "note"},
            "set_partner_window_note": {"action", "slot_index", "note"},
            "clear_partner_window_note": {"action"},
        }
        if action in ("log_bench_visit", "set_bench_visibility", "bookmark_window_book"):
            refusal = self._capability_refusal()
            if refusal is not None:
                self._send(*refusal)
                return

        extra = sorted(set(payload) - allowed_by_action[action])
        if extra:
            raise PhoneRequestError("UNKNOWN_FIELD:%s" % ",".join(extra))

        if action == "log_bench_visit":
            place_id = _outing_place_id(payload.get("place_id"))
            key = _operation_key(payload.get("operation_key"))
            note = (_outing_optional_note(payload.get("note"), 120, "BENCH_NOTE_INVALID")
                    if "note" in payload else None)
            shared = (_strict_boolean(payload.get("shared"), "BENCH_SHARED_INVALID")
                      if "shared" in payload else None)
            self._send_or_map(service.outing_bench_bookstore_action(
                action=action, place_id=place_id, note=note, shared=shared,
                operation_key=key))
            return

        if action == "set_bench_visibility":
            visit_id = _outing_required_local_id(payload.get("visit_id"),
                                                 "BENCH_VISIT_ID_INVALID")
            shared = _strict_boolean(payload.get("shared"), "BENCH_SHARED_INVALID")
            self._send_or_map(service.outing_bench_bookstore_action(
                action=action, visit_id=visit_id, shared=shared))
            return

        if action == "bookmark_window_book":
            slot_index = _outing_slot_index(payload.get("slot_index"))
            shared = (_strict_boolean(payload.get("shared"), "WINDOW_SHARED_INVALID")
                      if "shared" in payload else None)
            note_supplied = "note" in payload
            note = (_outing_optional_note(payload.get("note"), 120, "WINDOW_NOTE_INVALID")
                    if note_supplied else None)
            self._send_or_map(service.outing_bench_bookstore_action(
                action=action, slot_index=slot_index, shared=shared,
                note=note, note_supplied=note_supplied))
            return

        if action == "set_partner_window_note":
            slot_index = _outing_slot_index(payload.get("slot_index"))
            note = _clean_text(payload.get("note"), 120, "WINDOW_NOTE_INVALID")
            self._send_or_map(service.outing_bench_bookstore_action(
                action=action, slot_index=slot_index, note=note))
            return

        self._send_or_map(service.outing_bench_bookstore_action(action=action))

    def _get_outing_routes_market(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/outing/routes-market``: pure read, 403 on bad capability."""

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        subject_view, refusal = self._desk_subject_view()
        if refusal is not None:
            self._send(*refusal)
            return
        self._send_or_map(service.outing_routes_market(subject_view=subject_view))

    def _post_outing_routes_market(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/outing/routes-market``: walk routes & festival market memos."""

        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        opt_refusal = self._optional_capability_refusal()
        if opt_refusal is not None:
            self._send(*opt_refusal)
            return
        payload = self._read_body(OUTING_ROUTES_MARKET_FIELDS)
        action = _clean_text(payload.get("action"), 40,
                             "ROUTES_MARKET_ACTION_REQUIRED").lower()
        if action not in OUTING_ROUTES_MARKET_ACTIONS:
            raise PhoneRequestError("ROUTES_MARKET_ACTION_UNSUPPORTED")

        allowed_by_action = {
            "save_walk_route": {
                "action", "route_id", "title", "place_ids",
                "album_artifact_id", "caption", "shared",
            },
            "remove_walk_route": {"action", "route_id"},
            "note_market_wish": {
                "action", "stall_code", "memo", "budget_cap_jpy", "shared",
            },
            "set_partner_market_tip": {"action", "stall_code", "tip"},
            "clear_partner_market_tip": {"action"},
        }
        if action in ("save_walk_route", "remove_walk_route", "note_market_wish"):
            refusal = self._capability_refusal()
            if refusal is not None:
                self._send(*refusal)
                return

        extra = sorted(set(payload) - allowed_by_action[action])
        if extra:
            raise PhoneRequestError("UNKNOWN_FIELD:%s" % ",".join(extra))

        if action == "save_walk_route":
            route_id = _optional_local_id(payload.get("route_id"), "ROUTE_ID_INVALID")
            title = _clean_text(payload.get("title"), 60, "ROUTE_TITLE_INVALID")
            place_ids = _outing_place_list(payload.get("place_ids"))
            album_supplied = "album_artifact_id" in payload
            raw_album = payload.get("album_artifact_id")
            if raw_album in (None, ""):
                if raw_album is not None and not isinstance(raw_album, str):
                    raise PhoneRequestError("ALBUM_ID_INVALID")
                album_id = None
            else:
                album_id = _owner_path_id(raw_album, "ALBUM_ID_INVALID")
            caption_supplied = "caption" in payload
            caption = (_outing_optional_note(payload.get("caption"), 120,
                                             "ROUTE_CAPTION_INVALID")
                       if caption_supplied else None)
            shared = (_strict_boolean(payload.get("shared"), "ROUTE_SHARED_INVALID")
                      if "shared" in payload else None)
            self._send_or_map(service.outing_routes_market_action(
                action=action, route_id=route_id, title=title, place_ids=place_ids,
                album_artifact_id=album_id, album_supplied=album_supplied,
                caption=caption, caption_supplied=caption_supplied, shared=shared))
            return

        if action == "remove_walk_route":
            route_id = _outing_required_local_id(payload.get("route_id"), "ROUTE_ID_INVALID")
            self._send_or_map(service.outing_routes_market_action(
                action=action, route_id=route_id))
            return

        if action == "note_market_wish":
            stall_code = _world_choice(payload.get("stall_code"), outing_view.STALL_CODES,
                                       "MARKET_STALL_INVALID")
            memo = _clean_text(payload.get("memo"), 120, "MARKET_MEMO_INVALID")
            budget_supplied = "budget_cap_jpy" in payload
            budget_cap = (_outing_budget_cap(payload.get("budget_cap_jpy"))
                          if budget_supplied else None)
            shared = (_strict_boolean(payload.get("shared"), "MARKET_SHARED_INVALID")
                      if "shared" in payload else None)
            self._send_or_map(service.outing_routes_market_action(
                action=action, stall_code=stall_code, memo=memo,
                budget_cap_jpy=budget_cap, budget_supplied=budget_supplied,
                shared=shared))
            return

        if action == "set_partner_market_tip":
            stall_code = _world_choice(payload.get("stall_code"), outing_view.STALL_CODES,
                                       "MARKET_STALL_INVALID")
            tip = _clean_text(payload.get("tip"), 120, "MARKET_TIP_INVALID")
            self._send_or_map(service.outing_routes_market_action(
                action=action, stall_code=stall_code, tip=tip))
            return

        self._send_or_map(service.outing_routes_market_action(action=action))

    def _get_outing_gallery_blueprint(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/outing/gallery-blueprint``: pure read, 403 on bad capability."""

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        subject_view, refusal = self._desk_subject_view()
        if refusal is not None:
            self._send(*refusal)
            return
        self._send_or_map(service.outing_gallery_blueprint(subject_view=subject_view))

    def _post_outing_gallery_blueprint(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/outing/gallery-blueprint``: gallery curating & co-blueprints."""

        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        opt_refusal = self._optional_capability_refusal()
        if opt_refusal is not None:
            self._send(*opt_refusal)
            return
        payload = self._read_body(OUTING_GALLERY_BLUEPRINT_FIELDS,
                                  allow_identity_fields=frozenset({"artifact_id"}))
        action = _clean_text(payload.get("action"), 40,
                             "GALLERY_BLUEPRINT_ACTION_REQUIRED").lower()
        if action not in OUTING_GALLERY_BLUEPRINT_ACTIONS:
            raise PhoneRequestError("GALLERY_BLUEPRINT_ACTION_UNSUPPORTED")

        allowed_by_action = {
            "curate_exhibit": {"action", "artifact_id", "version_id", "wall_caption"},
            "remove_exhibit": {"action", "exhibit_id"},
            "upsert_blueprint": {
                "action", "blueprint_id", "title", "sketch_note",
                "intended_use", "author_side",
            },
            "archive_blueprint": {"action", "blueprint_id", "author_side"},
        }
        if action in ("curate_exhibit", "remove_exhibit"):
            refusal = self._capability_refusal()
            if refusal is not None:
                self._send(*refusal)
                return

        extra = sorted(set(payload) - allowed_by_action[action])
        if extra:
            raise PhoneRequestError("UNKNOWN_FIELD:%s" % ",".join(extra))

        if action == "curate_exhibit":
            artifact_id = _owner_path_id(payload.get("artifact_id"), "ARTIFACT_ID_INVALID")
            version_id = _version_path_id(payload.get("version_id"))
            caption_supplied = "wall_caption" in payload
            wall_caption = (_outing_optional_note(payload.get("wall_caption"), 120,
                                                  "WALL_CAPTION_INVALID")
                            if caption_supplied else None)
            self._send_or_map(service.outing_gallery_blueprint_action(
                action=action, artifact_id=artifact_id, version_id=version_id,
                wall_caption=wall_caption, caption_supplied=caption_supplied))
            return

        if action == "remove_exhibit":
            exhibit_id = _outing_required_local_id(payload.get("exhibit_id"),
                                                   "EXHIBIT_ID_INVALID")
            self._send_or_map(service.outing_gallery_blueprint_action(
                action=action, exhibit_id=exhibit_id))
            return

        author_side = _clean_text(payload.get("author_side"), 20,
                                  "BLUEPRINT_AUTHOR_REQUIRED").lower()
        if author_side not in ("her", "partner"):
            raise PhoneRequestError("BLUEPRINT_AUTHOR_INVALID")
        if author_side == "her":
            refusal = self._capability_refusal()
            if refusal is not None:
                self._send(*refusal)
                return

        if action == "upsert_blueprint":
            blueprint_id = _optional_local_id(payload.get("blueprint_id"),
                                              "BLUEPRINT_ID_INVALID")
            title = _clean_text(payload.get("title"), 40, "BLUEPRINT_TITLE_INVALID")
            sketch_note = _clean_text(payload.get("sketch_note"), 160,
                                      "BLUEPRINT_SKETCH_INVALID")
            intended_use = _clean_text(payload.get("intended_use"), 120,
                                       "BLUEPRINT_USE_INVALID")
            self._send_or_map(service.outing_gallery_blueprint_action(
                action=action, blueprint_id=blueprint_id, title=title,
                sketch_note=sketch_note, intended_use=intended_use,
                author_side=author_side))
            return

        blueprint_id = _outing_required_local_id(payload.get("blueprint_id"),
                                                 "BLUEPRINT_ID_INVALID")
        self._send_or_map(service.outing_gallery_blueprint_action(
            action=action, blueprint_id=blueprint_id, author_side=author_side))

    # -- 长程陪伴日常：首日房间/盆栽手账/共同习惯册/共同礼物/双层灯光/一键续接 ----------

    def _get_longterm_first_room_habits(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/longterm/first-room-habits``: pure read, 403 on bad capability."""

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        subject_view, refusal = self._desk_subject_view()
        if refusal is not None:
            self._send(*refusal)
            return
        self._send_or_map(service.longterm_first_room_habits(subject_view=subject_view))

    def _post_longterm_first_room_habits(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/longterm/first-room-habits``: Day-1 room / plant sketch / habits."""

        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        opt_refusal = self._optional_capability_refusal()
        if opt_refusal is not None:
            self._send(*opt_refusal)
            return
        payload = self._read_body(LONGTERM_FIRST_ROOM_HABITS_FIELDS)
        action = _clean_text(payload.get("action"), 40,
                             "FIRST_ROOM_HABITS_ACTION_REQUIRED").lower()
        if action not in LONGTERM_FIRST_ROOM_HABITS_ACTIONS:
            raise PhoneRequestError("FIRST_ROOM_HABITS_ACTION_UNSUPPORTED")

        allowed_by_action = {
            "capture_first_room": {"action", "album_artifact_id", "note"},
            "link_plant_sketch": {
                "action", "plant_id", "target_kind", "target_artifact_id",
                "observation_note", "shared",
            },
            "remove_plant_sketch": {"action", "link_id"},
            "upsert_habit": {
                "action", "habit_id", "title", "habit_text",
                "revised_reason", "author_side",
            },
            "retire_habit": {"action", "habit_id", "retired_reason", "author_side"},
        }
        if action in ("capture_first_room", "link_plant_sketch", "remove_plant_sketch"):
            refusal = self._capability_refusal()
            if refusal is not None:
                self._send(*refusal)
                return

        extra = sorted(set(payload) - allowed_by_action[action])
        if extra:
            raise PhoneRequestError("UNKNOWN_FIELD:%s" % ",".join(extra))

        if action == "capture_first_room":
            album_id = (
                _owner_path_id(payload.get("album_artifact_id"), "FIRST_ROOM_ALBUM_ID_INVALID")
                if payload.get("album_artifact_id") is not None else None
            )
            note = (
                _outing_optional_note(payload.get("note"), 160, "FIRST_ROOM_NOTE_INVALID")
                if "note" in payload else None
            )
            self._send_or_map(service.longterm_first_room_habits_action(
                action=action, album_artifact_id=album_id, note=note))
            return

        if action == "link_plant_sketch":
            plant_id = _outing_required_local_id(payload.get("plant_id"), "PLANT_ID_INVALID")
            target_kind = _clean_text(payload.get("target_kind"), 20,
                                      "SKETCH_TARGET_KIND_REQUIRED").lower()
            if target_kind not in ("project", "album"):
                raise PhoneRequestError("SKETCH_TARGET_KIND_INVALID")
            target_artifact_id = _owner_path_id(payload.get("target_artifact_id"),
                                                "SKETCH_TARGET_ID_INVALID")
            observation_note = _clean_text(payload.get("observation_note"), 160,
                                           "SKETCH_OBSERVATION_INVALID")
            shared = (
                _strict_boolean(payload.get("shared"), "SKETCH_SHARED_INVALID")
                if "shared" in payload else False
            )
            self._send_or_map(service.longterm_first_room_habits_action(
                action=action, plant_id=plant_id, target_kind=target_kind,
                target_artifact_id=target_artifact_id,
                observation_note=observation_note, shared=shared))
            return

        if action == "remove_plant_sketch":
            link_id = _outing_required_local_id(payload.get("link_id"), "SKETCH_LINK_ID_INVALID")
            self._send_or_map(service.longterm_first_room_habits_action(
                action=action, link_id=link_id))
            return

        author_side = _clean_text(payload.get("author_side"), 20,
                                  "HABIT_AUTHOR_REQUIRED").lower()
        if author_side not in ("her", "partner"):
            raise PhoneRequestError("HABIT_AUTHOR_INVALID")
        if author_side == "her":
            refusal = self._capability_refusal()
            if refusal is not None:
                self._send(*refusal)
                return

        if action == "upsert_habit":
            habit_id = _optional_local_id(payload.get("habit_id"), "HABIT_ID_INVALID")
            title = _clean_text(payload.get("title"), 60, "HABIT_TITLE_INVALID")
            habit_text = _clean_text(payload.get("habit_text"), 200, "HABIT_TEXT_INVALID")
            revised_reason = (
                _outing_optional_note(payload.get("revised_reason"), 160, "HABIT_REASON_INVALID")
                if "revised_reason" in payload else None
            )
            self._send_or_map(service.longterm_first_room_habits_action(
                action=action, habit_id=habit_id, title=title, habit_text=habit_text,
                revised_reason=revised_reason, author_side=author_side))
            return

        habit_id = _outing_required_local_id(payload.get("habit_id"), "HABIT_ID_INVALID")
        retired_reason = (
            _outing_optional_note(payload.get("retired_reason"), 160, "HABIT_REASON_INVALID")
            if "retired_reason" in payload else None
        )
        self._send_or_map(service.longterm_first_room_habits_action(
            action=action, habit_id=habit_id, retired_reason=retired_reason,
            author_side=author_side))

    def _get_longterm_gifts_lantern(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/longterm/gifts-lantern``: optional `?layer=then_only|with_later_context`."""

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        raw_query = urlsplit(self.path).query
        layer = "then_only"
        if raw_query:
            pairs = parse_qsl(raw_query, keep_blank_values=True, strict_parsing=False)
            if not pairs:
                raise PhoneRequestError("UNKNOWN_QUERY")
            seen_layer = False
            for key, val in pairs:
                if key != "layer":
                    raise PhoneRequestError("UNKNOWN_QUERY")
                if seen_layer:
                    raise PhoneRequestError("DUPLICATE_QUERY:layer")
                seen_layer = True
                if val not in LONGTERM_LANTERN_LAYERS:
                    raise PhoneRequestError("LANTERN_LAYER_INVALID")
                layer = val
        subject_view, refusal = self._desk_subject_view()
        if refusal is not None:
            self._send(*refusal)
            return
        self._send_or_map(service.longterm_gifts_lantern(
            layer=layer, subject_view=subject_view))

    def _post_longterm_gifts_lantern(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/longterm/gifts-lantern``: gift draft->gifted & later reflections."""

        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        opt_refusal = self._optional_capability_refusal()
        if opt_refusal is not None:
            self._send(*opt_refusal)
            return
        payload = self._read_body(LONGTERM_GIFTS_LANTERN_FIELDS,
                                  allow_identity_fields=frozenset({"artifact_id"}))
        action = _clean_text(payload.get("action"), 40,
                             "GIFTS_LANTERN_ACTION_REQUIRED").lower()
        if action not in LONGTERM_GIFTS_LANTERN_ACTIONS:
            raise PhoneRequestError("GIFTS_LANTERN_ACTION_UNSUPPORTED")

        allowed_by_action = {
            "upsert_gift": {
                "action", "gift_id", "author_side", "stage", "title",
                "draft_seed_note", "finished_note", "artifact_id", "version_id",
            },
            "send_gift": {"action", "gift_id", "author_side", "finished_note"},
            "record_later_reflection": {
                "action", "source_artifact_id", "source_version_id",
                "reflection_note", "shared",
            },
        }
        if action == "record_later_reflection":
            refusal = self._capability_refusal()
            if refusal is not None:
                self._send(*refusal)
                return

        extra = sorted(set(payload) - allowed_by_action[action])
        if extra:
            raise PhoneRequestError("UNKNOWN_FIELD:%s" % ",".join(extra))

        if action == "record_later_reflection":
            source_artifact_id = _owner_path_id(
                payload.get("source_artifact_id"), "REFLECTION_SOURCE_ARTIFACT_INVALID")
            source_version_id = _version_path_id(payload.get("source_version_id"))
            reflection_note = _clean_text(payload.get("reflection_note"), 240,
                                          "REFLECTION_NOTE_INVALID")
            shared = (
                _strict_boolean(payload.get("shared"), "REFLECTION_SHARED_INVALID")
                if "shared" in payload else False
            )
            self._send_or_map(service.longterm_gifts_lantern_action(
                action=action, source_artifact_id=source_artifact_id,
                source_version_id=source_version_id,
                reflection_note=reflection_note, shared=shared))
            return

        author_side = _clean_text(payload.get("author_side"), 20,
                                  "GIFT_AUTHOR_REQUIRED").lower()
        if author_side not in ("her", "partner"):
            raise PhoneRequestError("GIFT_AUTHOR_INVALID")
        if author_side == "her":
            refusal = self._capability_refusal()
            if refusal is not None:
                self._send(*refusal)
                return

        if action == "upsert_gift":
            gift_id = _optional_local_id(payload.get("gift_id"), "GIFT_ID_INVALID")
            stage = _clean_text(payload.get("stage"), 20, "GIFT_STAGE_REQUIRED").lower()
            if stage not in ("draft", "wrapped"):
                raise PhoneRequestError("GIFT_STAGE_INVALID")
            title = _clean_text(payload.get("title"), 60, "GIFT_TITLE_INVALID")
            draft_seed_note = _clean_text(payload.get("draft_seed_note"), 200,
                                          "GIFT_DRAFT_SEED_INVALID")
            finished_supplied = "finished_note" in payload
            finished_note = (
                _outing_optional_note(payload.get("finished_note"), 200, "GIFT_FINISHED_NOTE_INVALID")
                if finished_supplied else None
            )
            link_supplied = ("artifact_id" in payload) or ("version_id" in payload)
            art_raw = payload.get("artifact_id")
            ver_raw = payload.get("version_id")
            if (art_raw is None) != (ver_raw is None):
                raise PhoneRequestError("GIFT_WORK_LINK_INCOMPLETE")
            artifact_id = (
                _owner_path_id(art_raw, "GIFT_ARTIFACT_ID_INVALID")
                if art_raw is not None else None
            )
            version_id = _version_path_id(ver_raw) if ver_raw is not None else None
            self._send_or_map(service.longterm_gifts_lantern_action(
                action=action, gift_id=gift_id, author_side=author_side, stage=stage,
                title=title, draft_seed_note=draft_seed_note,
                finished_note=finished_note, finished_note_supplied=finished_supplied,
                artifact_id=artifact_id, version_id=version_id, link_supplied=link_supplied))
            return

        gift_id = _outing_required_local_id(payload.get("gift_id"), "GIFT_ID_INVALID")
        finished_note = (
            _outing_optional_note(payload.get("finished_note"), 200, "GIFT_FINISHED_NOTE_INVALID")
            if "finished_note" in payload else None
        )
        self._send_or_map(service.longterm_gifts_lantern_action(
            action=action, gift_id=gift_id, author_side=author_side,
            finished_note=finished_note))

    def _get_longterm_resume_hub(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/longterm/resume-hub``: pure zero-write resume & closed/recovery hub."""

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        subject_view, refusal = self._desk_subject_view()
        if refusal is not None:
            self._send(*refusal)
            return
        self._send_or_map(service.longterm_resume_hub(subject_view=subject_view))

    def _get_character_pack(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/character/pack``: pure zero-write character pack & body summary."""

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        subject_view, refusal = self._desk_subject_view()
        if refusal is not None:
            self._send(*refusal)
            return
        self._send_or_map(service.character_pack(subject_view=subject_view))

    def _post_character_pack(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/character/pack``: preset / custom pack / identity / physiology."""

        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        opt_refusal = self._optional_capability_refusal()
        if opt_refusal is not None:
            self._send(*opt_refusal)
            return
        payload = self._read_body(
            CHARACTER_PACK_FIELDS,
            max_bytes=65536,
            allow_identity_fields=frozenset({"owner"}),
        )
        action = _clean_text(payload.get("action"), 40, "CHARACTER_PACK_ACTION_REQUIRED").lower()
        if action not in CHARACTER_PACK_ACTIONS:
            raise PhoneRequestError("CHARACTER_PACK_ACTION_INVALID")

        if action == "set_optional_physiology":
            refusal = self._capability_refusal()
            if refusal is not None:
                self._send(*refusal)
                return

        allowed_by_action: dict[str, set[str]] = {
            "select_preset": {"action", "preset_id"},
            "apply_custom_pack": {"action", "pack"},
            "import_pack_json": {"action", "pack_json", "pack"},
            "export_pack": {"action", "include_sexual_seed"},
            "attest_character_pack": {
                "action",
                "attested_by",
                "pack_id",
                "version",
                "adult_confirmed",
                "life_stage",
                "declared_age",
                "fictional_confirmed",
                "pack_sha256",
                "note",
                "owner",
            },
            "update_identity": {
                "action", "display_name", "pronouns",
                "subject_calls_user", "user_calls_subject",
                "sensory_preferences", "everyday_accessories",
            },
            "set_optional_physiology": {"action", "optional_processes"},
        }
        extra = sorted(set(payload) - allowed_by_action[action])
        if extra:
            raise PhoneRequestError("UNKNOWN_FIELD:%s" % ",".join(extra))

        subject_view = self._capability_refusal() is None
        self._send_or_map(service.character_pack_action(
            action=action, payload=payload, subject_view=subject_view))

    def _get_body_sensory(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/body/sensory``: pure zero-write 5-channel sensory field & cards."""

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        subject_view, refusal = self._desk_subject_view()
        if refusal is not None:
            self._send(*refusal)
            return
        self._send_or_map(service.body_sensory(subject_view=subject_view))

    def _post_body_sensory(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/body/sensory``: sound cards & partner sensory notes."""

        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        opt_refusal = self._optional_capability_refusal()
        if opt_refusal is not None:
            self._send(*opt_refusal)
            return
        payload = self._read_body(BODY_SENSORY_FIELDS)
        action = _clean_text(payload.get("action"), 40, "BODY_SENSORY_ACTION_REQUIRED").lower()
        if action not in BODY_SENSORY_ACTIONS:
            raise PhoneRequestError("BODY_SENSORY_ACTION_INVALID")

        if action in ("capture_sound_card", "set_sound_card_share", "remove_sound_card"):
            refusal = self._capability_refusal()
            if refusal is not None:
                self._send(*refusal)
                return

        allowed_by_action: dict[str, set[str]] = {
            "capture_sound_card": {
                "action", "title", "impression_note", "shared", "operation_key",
            },
            "set_sound_card_share": {"action", "card_id", "shared"},
            "remove_sound_card": {"action", "card_id"},
            "leave_sensory_note": {"action", "text"},
            "remove_sensory_note": {"action", "note_id"},
            "clear_sensory_notes": {"action"},
        }
        extra = sorted(set(payload) - allowed_by_action[action])
        if extra:
            raise PhoneRequestError("UNKNOWN_FIELD:%s" % ",".join(extra))

        subject_view = self._capability_refusal() is None
        self._send_or_map(service.body_sensory_action(
            action=action, payload=payload, subject_view=subject_view))

    def _get_autonomy_rhythm(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/autonomy/rhythm``: pure zero-write autonomy & reply rhythm projection."""

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        subject_view, refusal = self._desk_subject_view()
        if refusal is not None:
            self._send(*refusal)
            return
        self._send_or_map(service.autonomy_rhythm(subject_view=subject_view))

    def _post_autonomy_rhythm(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/autonomy/rhythm``: explicit refusal, thread, pivot & async rhythm actions."""

        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        opt_refusal = self._optional_capability_refusal()
        if opt_refusal is not None:
            self._send(*opt_refusal)
            return
        payload = self._read_body(
            AUTONOMY_RHYTHM_FIELDS,
            allow_identity_fields=frozenset({"owner"}),
        )
        action = _clean_text(
            payload.get("action"), 48, "AUTONOMY_RHYTHM_ACTION_REQUIRED"
        ).lower()
        if action not in AUTONOMY_RHYTHM_ACTIONS:
            raise PhoneRequestError("AUTONOMY_RHYTHM_ACTION_INVALID")

        if action in AUTONOMY_RHYTHM_SUBJECT_ACTIONS:
            refusal = self._capability_refusal()
            if refusal is not None:
                self._send(*refusal)
                return

        allowed_by_action: dict[str, set[str]] = {
            "evaluate_turn": {"action", "user_text", "urgent", "session_id"},
            "declare_refusal": {
                "action", "topic_label", "category", "reason_summary", "private_note",
            },
            "release_refusal": {"action", "lock_id"},
            "record_turn_context": {
                "action", "topic_label", "reason", "owner", "private_note",
                "turn_id", "session_id", "hold_thread",
            },
            "hold_subject_topic": {
                "action", "topic_label", "reason", "owner", "private_note",
            },
            "return_to_thread": {"action", "thread_id"},
            "clear_session_threads": {"action", "session_id", "reason"},
            "wave_off_retracted": {"action", "thread_id"},
            "trigger_spontaneous_pivot": {"action", "custom_prompt"},
            "set_phone_aside": {"action", "mode", "minutes", "note", "private_note"},
            "urgent_knock": {"action", "msg_id", "reason"},
            "claim_ready_messages": {"action"},
            "clear_delivered_messages": {"action", "msg_ids"},
            "update_preferences": {
                "action",
                "pace_mode",
                "away_delay_minutes",
                "anti_errand_firmness",
                "thread_hold_min",
                "spontaneous_pivot_enabled",
                "pivot_cooldown_min",
                "spontaneous_pivot_cooldown_min",
            },
        }
        extra = sorted(set(payload) - allowed_by_action[action])
        if extra:
            raise PhoneRequestError("UNKNOWN_FIELD:%s" % ",".join(extra))

        subject_view = self._capability_refusal() is None
        self._send_or_map(service.autonomy_rhythm_action(
            action=action, payload=payload, subject_view=subject_view))

    # -- 0.3 情绪脾气与日常张力 / 看法修订与物件共在 / 欲望与 36B 成年亲密 -----------------

    def _read_slice_json_body(
        self,
        *,
        allow_identity_fields: frozenset[str] = frozenset(),
        max_bytes: int = MAX_BODY_BYTES,
        require_non_empty: bool = False,
    ) -> dict[str, Any]:
        """The one strict JSON object reader for every slice route.

        Content-Type/Length guards, ``BODY_TOO_LARGE`` (413), strict dict
        parsing and the identity fence (``CLIENT_IDENTITY_REFUSED`` / 409) are
        identical everywhere.  ``require_non_empty`` keeps the older image/echo
        doors' "an empty object is not a command" refusal unchanged.
        """

        content_type = str(self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type != "application/json":
            raise PhoneRequestError("CONTENT_TYPE_REQUIRED")
        raw_length = self.headers.get("Content-Length")
        if raw_length is None or not str(raw_length).strip().isdigit():
            raise PhoneRequestError("CONTENT_LENGTH_REQUIRED")
        length = int(str(raw_length).strip())
        if length <= 0:
            raise PhoneRequestError("BODY_REQUIRED")
        if length > int(max_bytes):
            raise PhoneRequestError("BODY_TOO_LARGE", 413)
        raw = self.rfile.read(length)
        self._body_consumed = True
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise PhoneRequestError("BODY_NOT_JSON") from None
        if not isinstance(payload, dict):
            raise PhoneRequestError("BODY_NOT_AN_OBJECT")
        if require_non_empty and not payload:
            raise PhoneRequestError("BODY_REQUIRED")
        identifiers = sorted((set(payload) & IDENTITY_FIELDS) - set(allow_identity_fields))
        if identifiers:
            raise PhoneRequestError("CLIENT_IDENTITY_REFUSED:%s" % ",".join(identifiers), 409)
        return payload

    def _read_v03_json_body(
        self,
        *,
        allow_identity_fields: frozenset[str] = frozenset(),
        max_bytes: int = MAX_BODY_BYTES,
    ) -> dict[str, Any]:
        """Compatibility alias for the canonical slice reader (empty object allowed)."""

        return self._read_slice_json_body(
            allow_identity_fields=allow_identity_fields, max_bytes=max_bytes
        )

    def _serve_slice_get(
        self,
        read: Callable[..., Any],
        *,
        desk: bool = True,
        deliver: str = "tuple",
    ) -> None:
        """Run the common slice GET sequence and send exactly one answer.

        ``desk=True`` resolves the optional subject capability through
        ``_desk_subject_view`` (absent credential = partner view, a wrong one is
        a 403); ``desk=False`` is the credential-free body-health door.
        ``deliver="tuple"`` sends ``(code, body)``; ``deliver="result"`` sends a
        single envelope through the one status map.
        """

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        if desk:
            subject_view, refusal = self._desk_subject_view()
            if refusal is not None:
                self._send(*refusal)
                return
            result = read(subject_view=subject_view)
        else:
            result = read()
        if deliver == "result":
            self._send_or_map(result)
            return
        code, body = result
        self._send(code, body)

    def _serve_slice_post(
        self,
        mutate: Callable[..., Any],
        *,
        allow_identity_fields: frozenset[str] = frozenset(),
        max_bytes: int = MAX_BODY_BYTES,
        require_non_empty: bool = False,
        deliver: str = "tuple",
    ) -> None:
        """Run the common slice POST sequence and send exactly one answer.

        CSRF first, then the optional capability (absent = partner door, a wrong
        one is a 403, never a downgrade), then the strict JSON body.  The same
        verification decides ``subject_view``.  ``deliver="v03_result"`` routes
        an envelope result through ``_send_v03_result`` (403 hint preserved).
        """

        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        opt_refusal = self._optional_capability_refusal()
        if opt_refusal is not None:
            self._send(*opt_refusal)
            return
        payload = self._read_slice_json_body(
            allow_identity_fields=allow_identity_fields,
            max_bytes=max_bytes,
            require_non_empty=require_non_empty,
        )
        subject_view = self._capability_refusal() is None
        result = mutate(payload=payload, subject_view=subject_view)
        if deliver == "v03_result":
            self._send_v03_result(result)
            return
        code, body = result
        self._send(code, body)

    def _send_v03_result(self, result: Mapping[str, Any]) -> None:
        if not result.get("ok") and result.get("http_status") == 403:
            self._send(403, _failure(str(result.get("reason_code") or "FORBIDDEN")))
            return
        self._send_or_map(result)

    def _get_emotional_tension(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/emotion/tension``: pure zero-write 5-domain tension projection."""

        self._serve_slice_get(service.emotional_tension, deliver="result")

    def _post_emotional_tension(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/emotion/tension``: schedule, quarrel, missing, low-mood & favor actions."""

        self._serve_slice_post(
            service.emotional_tension_action,
            allow_identity_fields=frozenset({"actor"}),
            deliver="v03_result",
        )

    def _get_stance_containment(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/stance/containment``: pure zero-write stance, containment & desk/table projection."""

        self._serve_slice_get(service.stance_containment, deliver="result")

    def _post_stance_containment(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/stance/containment``: stance lineage, container & shared desk/table actions."""

        self._serve_slice_post(
            service.stance_containment_action,
            allow_identity_fields=frozenset({"actor"}),
            deliver="v03_result",
        )

    def _get_adult_intimacy(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/intimacy/adult``: pure zero-write desire decay & 36B adult intimacy projection."""

        self._serve_slice_get(service.adult_intimacy, deliver="result")

    def _post_adult_intimacy(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/intimacy/adult``: 36B adult gate, consent, stop & private memory actions."""

        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        opt_refusal = self._optional_capability_refusal()
        if opt_refusal is not None:
            self._send(*opt_refusal)
            return
        payload = self._read_v03_json_body(
            allow_identity_fields=frozenset({"source", "price", "balance", "currency"})
        )
        raw_action = str(payload.get("action") or "").strip()
        subject_only_actions = {
            "revise_subject_preference",
            "evaluate_solo_window",
            "record_private_memory",
            "update_body_context",
        }
        by_actions = {
            "request_widen",
            "answer_widen",
            "pause_scene",
            "confirm_resume",
            "narrow_scene",
            "stop_scene",
            "erase_private_memory",
            "revoke_intimacy_consent",
        }

        def _norm_role(val: Any) -> str:
            return val.strip().lower() if isinstance(val, str) else ""

        refusal = self._capability_refusal()
        acts_as_subject = (
            raw_action in subject_only_actions
            or _norm_role(payload.get("by")) == "subject"
            or _norm_role(payload.get("proposer")) == "subject"
            or _norm_role(payload.get("responder")) == "subject"
            or _norm_role(payload.get("speaker")) == "subject"
        )
        if refusal is not None:
            if acts_as_subject:
                self._send(*refusal)
                return
            if raw_action in by_actions and (
                payload.get("by") is None
                or (isinstance(payload.get("by"), str) and not payload.get("by").strip())
            ):
                payload = {**payload, "by": "partner"}
        subject_view = refusal is None
        self._send_v03_result(
            service.adult_intimacy_action(payload=payload, subject_view=subject_view)
        )

    def _get_v03_clock(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/v03/clock``: pure zero-write three-clock & memory evaporation projection."""

        self._serve_slice_get(service.v03_clock)

    def _post_v03_clock(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/v03/clock``: zero-LLM clock advance, evaporable pool & meaningless idle."""

        self._serve_slice_post(
            service.v03_clock_action,
            allow_identity_fields=frozenset({"grant_id", "actor", "source"}),
        )

    def _get_body_health(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/body/health``: pure zero-write health tier and capacity."""

        self._serve_slice_get(service.body_health, desk=False)

    def _get_v03_ecology(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/v03/ecology``: pure zero-write economy, illness & reality-bridge projection."""

        self._serve_slice_get(service.v03_ecology)

    def _post_v03_ecology(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/v03/ecology``: independent economy, illness self-healing & outbound gate."""

        self._serve_slice_post(
            service.v03_ecology_action,
            allow_identity_fields=frozenset(
                {"source", "actor", "price", "balance", "currency"}
            ),
        )

    def _get_v03_embodied_social(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/v03/embodied-social``: pure zero-write kitchen, contact, NPC & style projection."""

        self._serve_slice_get(service.v03_embodied_social)

    def _post_v03_embodied_social(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/v03/embodied-social``: kitchen conservation, two-hand contact, NPC & style."""

        self._serve_slice_post(
            service.v03_embodied_social_action,
            allow_identity_fields=frozenset({"actor", "source"}),
        )

    def _get_v04_neighborhood(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/v04/neighborhood``: pure zero-write neighborhood, courtyard, craft & shelf projection."""

        self._serve_slice_get(service.v04_neighborhood)

    def _post_v04_neighborhood(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/v04/neighborhood``: local weather, journey, courtyard, notices, crafts & shelf."""

        self._serve_slice_post(service.v04_neighborhood_action)

    def _get_v04_builder_trylive(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/v04/builder-trylive``: pure zero-write world diagnostics & try-live sandbox projection."""

        self._serve_slice_get(service.v04_builder_trylive)

    def _post_v04_builder_trylive(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/v04/builder-trylive``: isolated route audit, prose gap check, 1h try-live & preview."""

        self._serve_slice_post(service.v04_builder_trylive_action)

    def _get_v04_elastic_play(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/v04/elastic-play``: pure zero-write elastic direction, shared learning & playfulness."""

        self._serve_slice_get(service.v04_elastic_play)

    def _post_v04_elastic_play(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/v04/elastic-play``: day direction, shared notebook, experiment, game & surprise."""

        self._serve_slice_post(service.v04_elastic_play_action)

    def _read_v02_crossmodal_json_body(
        self,
        *,
        max_bytes: int = 6 * 1024 * 1024,
        allow_identity_fields: frozenset[str] = frozenset(),
    ) -> dict[str, Any]:
        """Compatibility alias for the canonical reader (empty object refused)."""

        return self._read_slice_json_body(
            allow_identity_fields=allow_identity_fields,
            max_bytes=max_bytes,
            require_non_empty=True,
        )

    def _get_v02_agency_depth(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/v02/agency-depth``: pure zero-write agency depth projection."""

        self._serve_slice_get(service.v02_agency_depth)

    def _post_v02_agency_depth(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/v02/agency-depth``: agency depth mutations with actor_side & idempotency."""

        self._serve_slice_post(service.v02_agency_depth_action)

    def _get_v02_crossmodal(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/v02/crossmodal``: pure zero-write crossmodal palette, trial pad & reading marks."""

        self._serve_slice_get(service.v02_crossmodal)

    def _post_v02_crossmodal(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/v02/crossmodal``: image palette analysis, color trial swatches & reading marks."""

        self._serve_slice_post(
            service.v02_crossmodal_action,
            allow_identity_fields=frozenset({"artifact_id"}),
            max_bytes=6 * 1024 * 1024,
            require_non_empty=True,
        )

    def _get_v02_runtime_migration(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/v02/runtime-migration``: pure zero-write runtime governance & migration projection."""

        self._serve_slice_get(service.v02_runtime_migration)

    def _post_v02_runtime_migration(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/v02/runtime-migration``: budget mode, carrier check, suitcase & skill reverify."""

        self._serve_slice_post(
            service.v02_runtime_migration_action,
            allow_identity_fields=frozenset({"artifact_id"}),
            max_bytes=4 * 1024 * 1024,
        )

    def _get_v02_honest_ux(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/v02/honest-ux``: pure zero-write honest UX & boundary projection."""

        self._serve_slice_get(service.v02_honest_ux)

    def _post_v02_honest_ux(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/v02/honest-ux``: partner boundary & subject-only honest UX actions."""

        self._serve_slice_post(
            service.v02_honest_ux_action,
            allow_identity_fields=frozenset({"artifact_id", "actor", "source", "owner"}),
            require_non_empty=True,
        )

    def _get_v02_recall_projection(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/v02/recall-projection``: pure zero-write 4-level recall projection."""

        self._serve_slice_get(service.v02_recall_projection)

    def _post_v02_recall_projection(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/v02/recall-projection``: episodic trace, active fade, rehearsal & tombstone."""

        self._serve_slice_post(
            service.v02_recall_projection_action,
            allow_identity_fields=frozenset({"actor", "source", "owner"}),
            require_non_empty=True,
        )

    def _get_v02_common_ground(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/v02/common-ground``: pure zero-write common ground & unseen-move projection."""

        self._serve_slice_get(service.v02_common_ground)

    def _post_v02_common_ground(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/v02/common-ground``: ground ledger, receipt state & unseen-move inquiry."""

        self._serve_slice_post(
            service.v02_common_ground_action,
            allow_identity_fields=frozenset({"actor", "source", "owner"}),
            require_non_empty=True,
        )

    def _get_v02_ecology_growth(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/v02/ecology-growth``: pure zero-write midscale ecology & growth projection."""

        def _read(*, subject_view: bool = False) -> tuple[int, dict[str, Any]]:
            try:
                from . import v02_midscale_ecology_view as view
            except ImportError:
                return 503, {
                    "ok": False,
                    "status": "unavailable",
                    "command_status": "REJECTED",
                    "endpoint": f"{API_PREFIX}/v02/ecology-growth",
                    "reason_code": "V02_ECOLOGY_GROWTH_UNAVAILABLE",
                    "error_code": "V02_ECOLOGY_GROWTH_UNAVAILABLE",
                    "detail": "v02_midscale_ecology_view module is not available",
                    "llm_calls": 0,
                    "data": {},
                }
            return service._read_slice_view(
                view.read_v02_ecology_growth_view,
                subject_view=subject_view,
            )

        self._serve_slice_get(_read)

    def _post_v02_ecology_growth(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/v02/ecology-growth``: midscale ecology & growth mutations with CSRF & capability."""

        def _mutate(
            *,
            payload: Optional[Mapping[str, Any]] = None,
            subject_view: bool = False,
        ) -> tuple[int, dict[str, Any]]:
            try:
                from . import v02_midscale_ecology_view as view
            except ImportError:
                return 503, {
                    "ok": False,
                    "status": "unavailable",
                    "command_status": "REJECTED",
                    "endpoint": f"{API_PREFIX}/v02/ecology-growth",
                    "reason_code": "V02_ECOLOGY_GROWTH_UNAVAILABLE",
                    "error_code": "V02_ECOLOGY_GROWTH_UNAVAILABLE",
                    "detail": "v02_midscale_ecology_view module is not available",
                    "llm_calls": 0,
                    "data": {},
                }
            return service._mutate_slice_view(
                view.mutate_v02_ecology_growth_view,
                payload,
                subject_view=subject_view,
            )

        self._serve_slice_post(
            _mutate,
            allow_identity_fields=frozenset({"artifact_id"}),
            require_non_empty=True,
        )

    def _get_v02_world_lineage(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/v02/world-lineage``: pure zero-write world news, carrier & lineage projection."""

        from . import v02_news_carrier_lineage_view as view

        self._serve_slice_get(
            lambda *, subject_view=False: service._read_slice_view(
                view.read_v02_world_lineage_view,
                subject_view=subject_view,
            )
        )

    def _post_v02_world_lineage(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/v02/world-lineage``: world news layering, carrier adaptation & lineage mutations."""

        from . import v02_news_carrier_lineage_view as view

        self._serve_slice_post(
            lambda *, payload=None, subject_view=False: service._mutate_slice_view(
                view.mutate_v02_world_lineage_view,
                payload,
                subject_view=subject_view,
            ),
            require_non_empty=True,
        )

    def _get_v05_living_town(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/v05/living-town``: pure zero-write living town, regulars & dual-map projection."""

        from . import v05_living_town_view as view

        self._serve_slice_get(
            lambda *, subject_view=False: service._read_slice_view(
                view.read_v05_living_town_view,
                subject_view=subject_view,
            )
        )

    def _post_v05_living_town(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/v05/living-town``: living town, third-space, neighbor favors & dual-map mutations."""

        from . import v05_living_town_view as view

        self._serve_slice_post(
            lambda *, payload=None, subject_view=False: service._mutate_slice_view(
                view.mutate_v05_living_town_view,
                payload,
                subject_view=subject_view,
            ),
            require_non_empty=True,
        )

    def _get_v05_inner_spark(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/v05/inner-spark``: pure zero-write open desires, morning dreams, foreshadow & frost."""

        from . import v05_dreams_desires_view as view

        self._serve_slice_get(
            lambda *, subject_view=False: service._read_slice_view(
                view.read_v05_inner_spark_view,
                subject_view=subject_view,
            )
        )

    def _post_v05_inner_spark(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/v05/inner-spark``: open desires, morning dreams, tucked page notes & window frost."""

        from . import v05_dreams_desires_view as view

        self._serve_slice_post(
            lambda *, payload=None, subject_view=False: service._mutate_slice_view(
                view.mutate_v05_inner_spark_view,
                payload,
                subject_view=subject_view,
            ),
            require_non_empty=True,
        )

    def _get_v05_home_craft(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/v05/home-craft``: pure zero-write patina, arrangement, moving carton & postcards."""

        from . import v05_home_craft_view as view

        self._serve_slice_get(
            lambda *, subject_view=False: service._read_slice_view(
                view.read_v05_home_craft_view,
                subject_view=subject_view,
            )
        )

    def _post_v05_home_craft(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/v05/home-craft``: patina, arrangement compromise, carton unpack & PNG postcards."""

        from . import v05_home_craft_view as view

        self._serve_slice_post(
            lambda *, payload=None, subject_view=False: service._mutate_slice_view(
                view.mutate_v05_home_craft_view,
                payload,
                subject_view=subject_view,
            ),
            max_bytes=512 * 1024,
            require_non_empty=True,
        )

    def _get_v08_living_traces(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/v08/living-traces``: pure zero-write traces, objects & routines."""

        self._serve_slice_get(service.v08_living_traces)

    def _post_v08_living_traces(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/v08/living-traces``: universal verbs, living traces & routines."""

        self._serve_slice_post(
            service.v08_living_traces_action, require_non_empty=True
        )

    def _get_v08_composable_body(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/v08/composable-body``: pure zero-write optional body packs."""

        self._serve_slice_get(service.v08_composable_body)

    def _post_v08_composable_body(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/v08/composable-body``: optional body pack actions."""

        self._serve_slice_post(
            service.v08_composable_body_action, require_non_empty=True
        )

    def _get_v03_encounters(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/v03/encounters``: pure zero-write finite glance & shared-material projection."""

        self._serve_slice_get(service.v03_encounters)

    def _post_v03_encounters(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/v03/encounters``: finite glance, shared materials & hearsay boundary."""

        self._serve_slice_post(
            service.v03_encounters_action, require_non_empty=True
        )

    def _get_v08_emergent_echoes(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/v08/emergent-echoes``: pure zero-write emergent echoes projection."""

        self._serve_slice_get(service.v08_emergent_echoes)

    def _post_v08_emergent_echoes(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/v08/emergent-echoes``: questions, idioms, reinterpretations & sparks."""

        self._serve_slice_post(
            service.v08_emergent_echoes_action, require_non_empty=True
        )

    def _get_v09_living_agency(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/v09/living-agency``: pure zero-write Pillars A + D projection."""

        self._serve_slice_get(service.v09_living_agency)

    def _post_v09_living_agency(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/v09/living-agency``: blind choices, stances, objects & taxonomy."""

        self._serve_slice_post(
            service.v09_living_agency_action, require_non_empty=True
        )

    def _get_v09_world_commitments(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/v09/world-commitments``: pure zero-write Pillars B + C projection."""

        self._serve_slice_get(service.v09_world_commitments)

    def _post_v09_world_commitments(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/v09/world-commitments``: dynamic catalog, NPC commitments & contact."""

        self._serve_slice_post(
            service.v09_world_commitments_action, require_non_empty=True
        )

    def _get_outbound_pending(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/outbound/pending``: 100% zero-write unified pollable outbox."""

        self._serve_slice_get(service.outbound_pending)

    def _post_outbound_ack(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/outbound/ack``: CSRF-gated idempotent transport delivery receipt."""

        self._serve_slice_post(
            service.outbound_ack_action, require_non_empty=True
        )

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

    # -- participants (F-140: A's ParticipantRegistry; every read here is pure) -----------

    def _participants_limit(self) -> Optional[int]:
        """``?limit=N``: the only query key the identity list accepts."""

        raw = urlsplit(self.path).query
        if not raw:
            return None
        pairs = parse_qsl(raw, keep_blank_values=True)
        unknown = sorted({key for key, _value in pairs} - PARTICIPANT_QUERY_FIELDS)
        if unknown:
            raise PhoneRequestError("UNKNOWN_QUERY:%s" % ",".join(unknown))
        if [key for key, _value in pairs].count("limit") > 1:
            raise PhoneRequestError("QUERY_REPEATED:limit")
        return _limit_value(pairs[0][1] if pairs else None, MAX_PARTICIPANTS_LIST)

    def _get_participants(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/participants``: bounded identity rows -- no message body."""

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        self._send_or_map(service.participants(self._participants_limit()))

    def _get_participant(self, service: phone_service.PhoneService,
                         participant_id: str) -> None:
        """``GET /api/phone/participants/<id>``: one participant and its explicit links."""

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        self._send_or_map(service.participant(participant_id))

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

    def _get_today(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/today``: 她今天已经发生的活动与作品版本，只读。

        与 ``/living`` 同一套 host/origin 守护：这一段同样是她的私人事实。读取只碰
        ``canonical_life_state.json``、业务时钟文件和既有项目 owner，不启动任何服务。
        """

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        self._send_or_map(service.today())

    def _get_today_arrangements(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/today/arrangements``: 计划/义务/执行状态，只读。

        与 ``/today`` 同一套 host/origin 守护：计划标题、义务标题与 Activity 标题都是
        她的私人事实。读取只碰既有 Life 文档、canonical 权威与 owner 读口；不启动任何服务，
        也不写结果表。它只接受一个路径，多一个 query 就是对客户端 bug 的静默猜测，直接拒绝。
        """

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        self._send_or_map(service.today_arrangements())

    def _get_decision_review(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/decisions/review``: 决定的有限摘要与 Adoption 状态，只读。

        ``?decision_id=`` 单查一条；缺省只列最近骨架。与 ``/today`` 同一套 host/origin
        守护：决定里的来源、候选和理由都是她的私人事实。rationale 正文不进入手机读面。
        """

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        decision_id, limit = self._decision_review_query()
        self._send_or_map(service.decision_review(decision_id=decision_id, limit=limit))

    def _decision_review_query(self) -> tuple[Optional[str], int]:
        """``?decision_id=...&limit=N``：两个键各最多一次；未知键直接拒绝。"""

        raw = urlsplit(self.path).query
        if not raw:
            return None, 10
        pairs = parse_qsl(raw, keep_blank_values=True)
        unknown = sorted({key for key, _value in pairs} - DECISION_REVIEW_QUERY_FIELDS)
        if unknown:
            raise PhoneRequestError("UNKNOWN_QUERY:%s" % ",".join(unknown))
        seen: dict[str, str] = {}
        for key, value in pairs:
            if key in seen:
                raise PhoneRequestError("QUERY_REPEATED:%s" % key)
            seen[key] = value
        decision_id = (seen.get("decision_id") or "").strip() or None
        if decision_id is not None:
            if len(decision_id) > 128 or any(ch in decision_id for ch in ("\x00", "\r", "\n")):
                raise PhoneRequestError("DECISION_ID_INVALID")
        limit = _limit_value(seen.get("limit"), phone_service.MAX_MATERIAL_LIMIT)
        return decision_id, int(limit or 10)

    def _get_day_timeline(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/today/timeline``: 她今天有记录的事，只读。

        和 ``/today`` 一样走 host/origin 守护；日期只由实例自己的业务时钟决定，调用方
        不能写入发生时间。`X-Phone-Actor` 沿用既有声明：缺失/未知 = operator，只有
        ``model`` 才拿到原入口 ``detail_ref``；两种视图都不在聚合里复制日记正文或相册说明。
        """

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        self._send_or_map(service.day_timeline(
            subject_view=self._actor() == phone_service.ACTOR_MODEL))

    def _get_today_story(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/today/story``: 统一因果生活故事线（100% 零写纯读）。"""

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        opt_refusal = self._optional_capability_refusal()
        if opt_refusal is not None:
            self._send(*opt_refusal)
            return
        raw_query = urlsplit(self.path).query
        day_param: Optional[str] = None
        if raw_query:
            pairs = parse_qsl(raw_query, keep_blank_values=True)
            unknown = sorted({k for k, _v in pairs} - {"day"})
            if unknown:
                raise PhoneRequestError("UNKNOWN_QUERY:%s" % ",".join(unknown))
            if len(pairs) > 1:
                raise PhoneRequestError("QUERY_REPEATED:day")
            cand_day = str(pairs[0][1] or "").strip()
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", cand_day):
                raise PhoneRequestError("STORY_DAY_INVALID")
            day_param = cand_day
        self._send_or_map(service.day_story(day=day_param))

    def _get_claims(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/claims``: bounded current claims; host/origin guarded.

        A claim read carries where she was last observed and what sources said, so it
        uses the same guard as the rest of her private surface.  It never writes.
        """

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        self._send_or_map(service.claims())

    def _get_claim_slot(self, service: phone_service.PhoneService, slot_id: str) -> None:
        """``GET /api/phone/claims/<slot_id>``: one slot's current state and history."""

        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        if urlsplit(self.path).query:
            raise PhoneRequestError("UNKNOWN_QUERY")
        self._send_or_map(service.claim_slot(slot_id))

    def _post_claim_observe(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/claims/observations``: one formal World read -> evidence.

        The request names no content, world id, role or authority: only the operation
        key.  Everything observed is taken from the World owner's own snapshot here.
        """

        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        payload = self._read_body(frozenset({"operation_key"}))
        key = _operation_key(payload.get("operation_key"))
        result = service.claim_observe(key)
        service.record_operation(actor=self._actor(), action="claims:observe",
                                 operation_key=key, result=result)
        self._send_or_map(result)

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
            self._read_body(frozenset({"operation_key"}))
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

    def _get_diary_list(self, service: phone_service.PhoneService) -> None:
        """``GET /api/phone/diary``: the index only -- times, lengths and a count."""
        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        self._send(200, service.diary_list())

    def _get_diary_entry(self, service: phone_service.PhoneService, entry_id: str) -> None:
        """``GET /api/phone/diary/<id>``: one entry, body only with this serve's credential.

        ``X-Phone-Actor`` remains an intent declaration, not an identity.  An operator read
        (missing / non-model actor) gets metadata with ``body_withheld: true``.  A declared
        model read must additionally present the local capability credential; missing, wrong
        or unavailable credentials are refused instead of degrading to a successful empty body.
        """
        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        if self._actor() == phone_service.ACTOR_MODEL:
            refusal = self._capability_refusal()
            if refusal is not None:
                self._send(*refusal)
                return
            include_text = True
        else:
            include_text = False
        self._send_or_map(service.diary_read(entry_id, include_text=include_text))

    def _post_diary(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/diary``: one entry of her own words, with her own real time.

        Writing her own diary is a subject write, not an operator write: it requires this
        serve's local capability in addition to the existing CSRF door.  A missing / wrong
        credential is refused before the body is parsed or any owner is touched, so it creates
        zero entries and zero versions.
        """
        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        refusal = self._capability_refusal()
        if refusal is not None:
            self._send(*refusal)
            return
        payload = self._read_body(DIARY_WRITE_FIELDS)
        body = _clean_body(payload.get("body"), phone_service.MAX_DIARY_BODY)
        key = _operation_key(payload.get("operation_key"))
        result = service.diary_write(body=body, operation_key=key)
        service.record_operation(actor=self._actor(), action="diary", operation_key=key,
                                 result=result)
        self._send_or_map(result)

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
        if "item_name" in payload and payload.get("item_name") is not None:
            item_name = _clean_text(payload.get("item_name"), 40, "ITEM_NAME_INVALID")
            result = service.store_purchase(catalog_item_id, key, item_name=item_name)
        else:
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

    def _post_camera(self, service: phone_service.PhoneService) -> None:
        """``POST /api/phone/camera``: one local picture from her scene text.

        This is the only camera write.  The runtime address, the model and the generation
        parameters are the phone's own; the body can name neither.  The picture is saved by the
        existing album import -- a camera capture is not a second media store.
        """
        guard = self._guard_mutation()
        if guard is not None:
            self._send(*guard)
            return
        payload = self._read_body(CAMERA_CAPTURE_FIELDS)
        scene = _clean_body(payload.get("scene"), MAX_CAMERA_SCENE)
        caption = payload.get("caption")
        if not isinstance(caption, str):
            raise PhoneRequestError("CAPTION_REQUIRED")
        caption = caption.strip()
        if len(caption) > MAX_ALBUM_CAPTION:
            raise PhoneRequestError("CAPTION_TOO_LONG")
        if any(ch in caption for ch in ("\x00", "\r")):
            raise PhoneRequestError("CAPTION_INVALID")
        key = _operation_key(payload.get("operation_key"))
        result = service.camera_capture(scene=scene, caption=caption, operation_key=key)
        service.record_operation(actor=self._actor(), action="camera", operation_key=key,
                                 result=result)
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

    def _get_project_context(self, service: phone_service.PhoneService,
                             artifact_id: str) -> None:
        """``GET .../context``: the same work's bounded, read-only context.

        Head and old versions, where a sourced work came from, the owner-confirmed results whose
        ``artifact_ref`` is this work, the methods whose evidence really points back at those
        results (with their latest explicit verdict), and the local drafts sourced from it.
        Association is by owner ref only.  Nothing here writes, sends or authorises, and the list
        answer carries no draft text.
        """
        guard = self._guard_common()
        if guard is not None:
            self._send(*guard)
            return
        self._send_or_map(service.work_context(artifact_id))

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


def _desk_due_fields(payload: Mapping[str, Any]) -> tuple[Optional[str], Optional[int]]:
    """Exactly one of ``due_at`` / ``due_in_minutes``; the owner keeps the final word."""

    due_at = payload.get("due_at")
    minutes = payload.get("due_in_minutes")
    has_due = due_at is not None and str(due_at).strip() != ""
    has_min = minutes is not None and str(minutes).strip() != ""
    if has_due == has_min:
        raise PhoneRequestError("DESK_DUE_TIME_AMBIGUOUS")
    if has_due:
        return _clean_text(due_at, 80, "DUE_AT_INVALID"), None
    if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes < 1:
        raise PhoneRequestError("DUE_IN_MINUTES_INVALID")
    return None, int(minutes)


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


def _optional_local_id(value: Any, reason: str) -> Optional[str]:
    """An optional local card id (recipe/plan).  ``None``/``""`` mean "derive it"; a path,
    a URL or a non-string is refused by name."""

    if value is None:
        return None
    if not isinstance(value, str):
        raise PhoneRequestError(reason)
    text = value.strip()
    if not text:
        return None
    if len(text) > 80 or any(ch in text for ch in ("\x00", "\r", "\n", "/", "\\", "?", "#")):
        raise PhoneRequestError(reason)
    return text


def _outing_required_local_id(value: Any, reason: str) -> str:
    """One required local card/visit/route/exhibit/blueprint id."""

    if not isinstance(value, str):
        raise PhoneRequestError(reason)
    text = value.strip()
    if not text or len(text) > 80 or not re.match(r"^[A-Za-z0-9._:\-]+$", text):
        raise PhoneRequestError(reason)
    return text


def _outing_optional_note(value: Any, maximum: int, reason: str) -> Optional[str]:
    """An optional bounded single-line note; `None`/`""` normalize to `None`."""

    if value is None:
        return None
    if not isinstance(value, str):
        raise PhoneRequestError(reason)
    if any(ch in value for ch in ("\x00", "\r")):
        raise PhoneRequestError(reason)
    stripped = value.strip()
    if not stripped:
        return None
    if len(stripped) > maximum:
        raise PhoneRequestError(reason)
    return " ".join(stripped.split())[:maximum]


def _outing_place_id(value: Any) -> str:
    """One World location identifier shape check before owner/world lookup."""

    if not isinstance(value, str):
        raise PhoneRequestError("PLACE_ID_REQUIRED")
    text = value.strip()
    if not text or len(text) > MAX_WORLD_DESTINATION:
        raise PhoneRequestError("PLACE_ID_INVALID")
    if any(ch in text for ch in ("\x00", "\r", "\n", "/", "\\", "?", "#", " ")):
        raise PhoneRequestError("PLACE_ID_INVALID")
    return text


def _outing_place_list(value: Any) -> list[str]:
    """Non-empty list of 1..6 distinct place_ids for a walk route."""

    if not isinstance(value, list) or not 1 <= len(value) <= outing_view.MAX_ROUTE_STOPS:
        raise PhoneRequestError("ROUTE_PLACES_INVALID")
    places: list[str] = []
    for raw in value:
        if not isinstance(raw, str) or not raw.strip():
            raise PhoneRequestError("ROUTE_PLACES_INVALID")
        pid = raw.strip()
        if len(pid) > MAX_WORLD_DESTINATION or any(
                ch in pid for ch in ("\x00", "\r", "\n", "/", "\\", "?", "#", " ")):
            raise PhoneRequestError("ROUTE_PLACES_INVALID")
        if pid in places:
            raise PhoneRequestError("ROUTE_PLACES_DUPLICATED")
        places.append(pid)
    return places


def _outing_slot_index(value: Any) -> int:
    """Strict integer `0..2` for featured bookstore window slots."""

    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2:
        raise PhoneRequestError("WINDOW_SLOT_INVALID")
    return int(value)


def _outing_budget_cap(value: Any) -> Optional[int]:
    """Optional integer >= 0 for virtual festival market budget memo."""

    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise PhoneRequestError("MARKET_BUDGET_CAP_INVALID")
    if not 0 <= value <= 10_000_000:
        raise PhoneRequestError("MARKET_BUDGET_CAP_INVALID")
    return int(value)


def _somatic_optional_label(value: Any) -> Optional[str]:
    """A short local garment label; empty/absent means "no label", never a path."""

    if value is None:
        return None
    if not isinstance(value, str):
        raise PhoneRequestError("GARMENT_LABEL_INVALID")
    text = value.strip()
    if not text:
        return None
    if len(text) > 40 or any(ch in text for ch in ("\x00", "\r", "\n")):
        raise PhoneRequestError("GARMENT_LABEL_INVALID")
    return text


def _somatic_optional_choice(value: Any, allowed: frozenset[str],
                             reason: str) -> Optional[str]:
    """An optional finite vocabulary value; absent stays ``None``, a wrong value is 400."""

    if value is None:
        return None
    if not isinstance(value, str) or value not in allowed:
        raise PhoneRequestError(reason)
    return value


def _strict_boolean(value: Any, reason: str) -> bool:
    """A JSON boolean, exactly.  ``"true"``/1/null are refusals, never coerced."""

    if not isinstance(value, bool):
        raise PhoneRequestError(reason)
    return value


def _pocket_entry_id(value: Any) -> str:
    """One stable local card id (no path, URL or owner identity)."""

    if not isinstance(value, str):
        raise PhoneRequestError("POCKET_ENTRY_ID_INVALID")
    text = value.strip()
    if not text or len(text) > 80 or not re.match(r"^[A-Za-z0-9_-]+$", text):
        raise PhoneRequestError("POCKET_ENTRY_ID_INVALID")
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
    ``target`` / ``object_id`` + absolute ``state``), and ``TOILET`` takes exactly one
    declared ``facility_id``.  P checks the shape only; the owner's word and reason code win.
    Identity, elapsed time, price and body values are refused here for every verb.
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
    if action == "TOILET":
        # Only the declared facility id travels.  Whether that facility exists, is at the
        # current place and is open is the World owner's verdict, never P's.
        return {"facility_id": _clean_text(value["facility_id"], MAX_WORLD_OBJECT_ID,
                                           "FACILITY_ID_INVALID")}
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
                  supervisor: Optional[Any] = None,
                  reuse_address: bool = False,
                  capability: Optional[phone_capability.Capability] = None,
                  operator_stop: Optional[Any] = None) -> PhoneHTTPServer:
    """Bind one loopback server.  The host is not a caller choice."""

    if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
        raise ValueError("port must be an integer in 0..65535")
    return PhoneHTTPServer(("127.0.0.1", int(port)), service=service,
                           csrf_token=csrf_token, log_path=log_path, supervisor=supervisor,
                           reuse_address=bool(reuse_address), capability=capability,
                           operator_stop=operator_stop)


__all__ = [
    "ALLOWED_FETCH_SITES",
    "ALLOWED_HOSTS",
    "API_PREFIX",
    "ARRANGE_TARGET_KINDS",
    "DECISION_REVIEW_QUERY_FIELDS",
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
    "ROUTE_V09_LIVING_AGENCY",
    "ROUTE_V09_WORLD_COMMITMENTS",
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

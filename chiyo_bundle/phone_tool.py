#!/usr/bin/env python3
"""``chiyo_phone``：个体自己看手机事实、提交手机操作的那一条窄工具。

它不拥有真相，也不是第二个手机服务：

* 读钱包/笔记/收藏/阅读/虚拟商店/**实体日用店**/物品/世界，以及**今天已经发生的事与今天的
  安排** → ``GET /api/phone/*``（世界快照与实体店货架都是只读的）；
* 显式购买/使用/写笔记/存收藏/移动/拿放/换姿势/休息/**柜台购买/布置自有家具/开关自己的灯** →
  同一条 HTTP 面上的 ``POST``，带 ``X-CSRF-Token``；
* 阅读工作台另有五个动作：``reading_material``（只读一页真实正文）、``reading_import``（显式导入
  正文，旧版本由 Supply 保留）、``reading_position``（先真读该页再显式保存停点）、
  ``reading_annotate``（对已取得的版本页写 ``note`` 私人笔记或 ``unclear`` 没读懂）、
  ``reading_operation``（按**原** operation_key 对账）；未知结果绝不换 key 重发；
* 全部字段、错误码与幂等语义属于 ``docs/engineering/v04-phone-api-contract.md``；世界/身体/房间
  字段与动词参数只按 C 的契约（``v04-world-body-contract.md`` + ``rc1-room-living-contract.md``）
  转发，P 不重算任何 owner 事实。

四条硬边界：

1. **同一个入口授权**：与 ``hermes_plugin.allowed`` 同一规则 —— CLI 需 ``cli_owner: true`` 且
   ``local_owner_uid`` 等于本进程真实 uid；网关只接受**已存在的 DM 绑定**；``local`` 入口与
   **被委派的会话**（``parent_session_id``）一律拒绝。``args`` 里没有任何入口字段，模型不能
   给自己发入口。
2. **显式开关**：profile ``chiyo/config.json`` 的 ``phone.enabled`` 缺失即 ``PHONE_DISABLED``；
   写操作（含 ``world_action``）还要 ``phone.write_enabled: true``（默认禁写）。
3. **只碰自己的手机**：``phone.profile_home`` 必须与服务器 ``/state`` 公布的实例指纹一致，
   否则 ``PHONE_PROFILE_MISMATCH``。``base_url`` 只能来自 profile，且必须是 loopback。
4. **私人档案是另一把钥匙**：私人 notes/references/reading 能保存她自己的过往，所以
   ``memory`` 打开且策略不是 ``unrestricted`` 时，本工具只保留
   wallet/shop/inventory/operation/world/world_operation；读写私人档案要 profile 显式
   ``phone.private_artifacts: true``。**开启手机不会绕过遗忘保护**（早先"不读 DB 所以不可能
   读到被遗忘历史"的说法不成立，已作废）。世界快照与 ``world_action`` 不是私人档案面：
   前者只有位置/摆位/姿势档位，后者只改世界与身体，两者都另受 ``write_enabled`` 约束。

来源分开：写请求带 ``X-Phone-Actor: model``；经济/Resolver 侧的 provenance 仍是
``local_owner_phone*`` —— 不伪造 ``verified_main_self``，也不伪造自主 Decision。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit

SCHEMA_VERSION = "chiyo.phone.tool.v1"

READ_ACTIONS = {
    "wallet": "/api/phone/wallet",
    "notes": "/api/phone/notes",
    "references": "/api/phone/references",
    "reading": "/api/phone/reading",
    # The workbench's real text: one page, read-only.  A body is never cached by this tool.
    "reading_material": "/api/phone/reading/material",
    "shop": "/api/phone/shop",
    "store": "/api/phone/store",
    "inventory": "/api/phone/inventory",
    "world": "/api/phone/world",
    # 她今天已经发生的活动/作品版本，以及今天的计划/义务/执行状态。两条都是既有的纯读 HTTP 面：
    # 只读 Activity 权威、Life 文档与 owner 回执，不启动服务、不写结果、不调用模型。
    "today": "/api/phone/today",
    "today_arrangements": "/api/phone/today/arrangements",
    # 她今天有记录的几件事合成的一条时间线：活动、日计划（只表示排定）、World 动作、
    # 日记/相册/钱包的写入骨架。只读，不带日记正文、相册说明或图片字节。
    "day_timeline": "/api/phone/today/timeline",
    # 她的日记目录：只列写入时间与字数，正文要单独 diary_read。
    "diary": "/api/phone/diary",
    # 她的私人相册目录：时间、说明与来源，不带图片字节（看图走手机的图片地址）。
    "album": "/api/phone/album",
    # S03: 她当前有理由相信的主张（观察到 / 有人报告 / 据此推断），每条带范围、时效与
    # 来源类别；冲突、过时与未知都如实分开，不是一个置信分数。
    "claims": "/api/phone/claims",
    # F-140: 她认识谁（本实例 operator 登记过的参与者）——稳定 id、显示名/别名、创建来源
    # 与时间、关联过的来话条数。这是身份只读面，没有正文、没有关系分数，也不能创建/绑定/发送。
    "participants": "/api/phone/participants",
    "projects": "/api/phone/projects",
    # 0.2 书桌/居家/衣柜/口袋/外出/长久相处只读面：
    "desk_presence": "/api/phone/desk/presence",
    "desk_notes": "/api/phone/desk/notes",
    "home_window": "/api/phone/home/window",
    "home_keepsakes": "/api/phone/home/keepsakes",
    "home_letters": "/api/phone/home/letters",
    "home_workbench": "/api/phone/home/workbench",
    "home_curios": "/api/phone/home/curios",
    "home_trio": "/api/phone/home/trio-exchange",
    "home_reunion_anchor": "/api/phone/home/reunion-anchor",
    "home_recipes": "/api/phone/home/recipes",
    "home_tentative_plans": "/api/phone/home/tentative-plans",
    "wardrobe": "/api/phone/home/wardrobe",
    "pocket_lists": "/api/phone/pocket/lists",
    "outing_bench_bookstore": "/api/phone/outing/bench-bookstore",
    "outing_routes_market": "/api/phone/outing/routes-market",
    "outing_gallery_blueprint": "/api/phone/outing/gallery-blueprint",
    "longterm_habits": "/api/phone/longterm/first-room-habits",
    "longterm_gifts_lantern": "/api/phone/longterm/gifts-lantern",
    "longterm_resume_hub": "/api/phone/longterm/resume-hub",
    # 0.3 具身冷暖/五感/角色包/自主节律/情绪张力/看法容纳/36B 成年亲密/三时钟与生态只读面：
    "body_somatic": "/api/phone/body/somatic",
    "body_garment_window": "/api/phone/body/garment-window",
    "body_care": "/api/phone/body/care-offering",
    "body_sensory": "/api/phone/body/sensory",
    "character_pack": "/api/phone/character/pack",
    "autonomy_rhythm": "/api/phone/autonomy/rhythm",
    "emotional_tension": "/api/phone/emotion/tension",
    "stance_containment": "/api/phone/stance/containment",
    "adult_intimacy": "/api/phone/intimacy/adult",
    "v03_clock": "/api/phone/v03/clock",
    "v03_ecology": "/api/phone/v03/ecology",
    "v03_embodied_social": "/api/phone/v03/embodied-social",
    # v0.4 街区院子手作公共生活 / 世界建设诊断与试住沙盒 / 弹性方向共同试验与玩心只读面：
    "v04_neighborhood": "/api/phone/v04/neighborhood",
    "v04_builder_trylive": "/api/phone/v04/builder-trylive",
    "v04_elastic_play": "/api/phone/v04/elastic-play",
    # 0.2 品味演化与心事深度 / 跨模态读图配色试色与未懂书签 / 唤醒治理省电休眠与迁居旅行箱 / 回忆清晰度投影 / 共同认知账本只读面：
    "v02_agency_depth": "/api/phone/v02/agency-depth",
    "v02_crossmodal": "/api/phone/v02/crossmodal",
    "v02_runtime_migration": "/api/phone/v02/runtime-migration",
    "v02_recall_projection": "/api/phone/v02/recall-projection",
    "v02_common_ground": "/api/phone/v02/common-ground",
    # 0.2 诚实体验与伴侣边界读面：代表分歧、遗忘悬浮、周期快照、旧作边角与个人工具瘦身。
    "v02_honest_ux": "/api/phone/v02/honest-ux",
    # v0.8 通用物件动作积木、屋里动态贴纸与自攒顺手小套路只读面（同一路由两种拼写）。
    "v08_living_traces": "/api/phone/v08/living-traces",
    "living_traces": "/api/phone/v08/living-traces",
    # v0.8 默契说法、留给未来的问题、旧事新解与各自看法（只读投影）。
    "v08_emergent_echoes": "/api/phone/v08/emergent-echoes",
    # v3.0 有限视野三次遇见、递一页材料与店主听说边界（只读投影）。
    "v03_encounters": "/api/phone/v03/encounters",
    # v0.8 可组合身体：周期如厕供给、夜间衣物照料、睡眠交代与日常身体照料私面（只读投影+主体写）。
    "v08_composable_body": "/api/phone/v08/composable-body",
    # v0.9 真实立场与盲选、物品复用与自建分类只读面（带主体凭据解锁 TA 视角，零写入）。
    "v09_living_agency_read": "/api/phone/v09/living-agency",
    # v0.9 动态货架、持久 NPC、世界约定与店铺公告只读面（带主体凭据解锁 TA 视角，零写入）。
    "v09_world_commitments_read": "/api/phone/v09/world-commitments",
}

#: 读面总表：``READ_ACTIONS`` 的公开别名，供检查读面覆盖的调用方按同一份事实读取。
LIFE_READ_SURFACES: dict[str, str] = READ_ACTIONS

WRITE_ACTIONS = ("note_save", "reference_save", "purchase", "store_purchase", "use",
                 "world_action", "reading_import", "reading_position", "reading_annotate",
                 "diary_write", "camera", "claim_observe", "phone_life_update",
                 "v09_living_agency_act", "v09_world_commitments_act")
#: Actions that read or write her private artifacts (or a private-artifact derivative).  Under a
#: restricted memory profile these need the separate ``phone.private_artifacts`` permit.
#: World/body commands are a different surface: they already require ``write_enabled``, and a
#: snapshot carries where she is, not her private notes.  The reading workbench is the most
#: private face of all -- the text she is reading, where she stopped and what she wrote about it.
#: "What she did today" and "what she arranged today" carry her own activity/work titles and her
#: self-planned or obligatory items, so they belong to the same private-artifact permit; the
#: permit is not widened, only these two surfaces join the existing one.
PRIVATE_ARTIFACT_ACTIONS = frozenset({
    "notes", "references", "reading", "note_save", "reference_save", "purchase", "store_purchase",
    "use",
    "reading_material", "reading_import", "reading_position", "reading_annotate",
    "reading_operation",
    "today", "today_arrangements", "day_timeline",
    # 同一件作品的有界上下文（旧版/来历/真实结果/相关做法与评价/本地草稿）：它读的是她的私人
    # 作品与经历摘录，因此和其余私人档案读面共用同一条显式许可，不新增权限面。
    "projects", "work_context", "work_version",
    # 日记正文是她自己的私人文字：目录可只看时间/字数，但写与读正文都属于私人档案面。
    "diary", "diary_read", "diary_write",
    # 当前主张包含她观察到的位置等事实；与其余私人档案读面共用同一条显式许可。
    "claims", "claim_slot",
    # 0.2 / 0.3 含私人草稿、私密笔记、看法或亲密记忆的面，统一受 phone.private_artifacts 约束：
    "desk_notes", "home_keepsakes", "home_workbench", "pocket_lists",
    "outing_routes_market", "longterm_gifts_lantern", "longterm_resume_hub",
    "emotional_tension", "stance_containment", "adult_intimacy",
    "v03_ecology", "v03_embodied_social",
    "v04_neighborhood", "v04_elastic_play",
    "v02_agency_depth", "v02_crossmodal",
    "v02_honest_ux",
    "v02_recall_projection", "v02_common_ground",
    "v08_living_traces", "living_traces",
    "v08_emergent_echoes",
    "v03_encounters",
    # 周期/夜间衣物/睡眠交代是身体私面：受限记忆模式同样受 private_artifacts 门控。
    "v08_composable_body",
    # v0.9 真实立场/盲选、私有物品备注/自建分类与世界约定：受限记忆模式统一受 private_artifacts 门控。
    "v09_living_agency_read",
    "v09_living_agency_act",
    "v09_world_commitments_read",
    "v09_world_commitments_act",
})
ACTIONS = tuple(sorted(READ_ACTIONS)) + WRITE_ACTIONS + ("operation", "world_operation",
                                                         "reading_operation", "work_context",
                                                         "work_version", "diary_read",
                                                         "album_image", "claim_slot",
                                                         # F-140: 单人只读（稳定 id 来自 participants 列表）。
                                                         "participant")

#: 0.2 / 0.3 / v0.4 只读面中可附带本机主体 capability 解锁本人视角（未配置时干净走默认无头读取）。
_SUBJECT_VIEW_READ_ACTIONS = frozenset({
    "desk_notes", "home_keepsakes", "home_letters", "home_workbench",
    "home_tentative_plans", "wardrobe", "pocket_lists",
    "outing_bench_bookstore", "outing_routes_market", "outing_gallery_blueprint",
    "longterm_habits", "longterm_gifts_lantern", "longterm_resume_hub",
    "body_somatic", "body_garment_window", "body_care", "body_sensory",
    "character_pack", "autonomy_rhythm", "emotional_tension",
    "stance_containment", "adult_intimacy",
    "v03_clock", "v03_ecology", "v03_embodied_social",
    "v04_neighborhood", "v04_builder_trylive", "v04_elastic_play",
    "v02_agency_depth", "v02_crossmodal", "v02_runtime_migration",
    "v02_honest_ux",
    "v02_recall_projection", "v02_common_ground",
    "v08_living_traces", "living_traces",
    "v08_emergent_echoes",
    "v03_encounters",
    "v08_composable_body",
    "v09_living_agency_read",
    "v09_world_commitments_read",
})

#: ``phone_life_update`` 允许提交的主体日常/体感/节律/张力/看法/36B/v03 写面路由白名单。
LIFE_UPDATE_SURFACES: dict[str, str] = {
    "desk_tomorrow": "/api/phone/desk/tomorrow",
    "home_keepsakes": "/api/phone/home/keepsakes",
    "home_workbench": "/api/phone/home/workbench",
    "home_trio": "/api/phone/home/trio-exchange",
    "home_reunion_anchor": "/api/phone/home/reunion-anchor",
    "home_recipes": "/api/phone/home/recipes",
    "home_tentative_plans": "/api/phone/home/tentative-plans",
    "wardrobe": "/api/phone/home/wardrobe",
    "pocket_lists": "/api/phone/pocket/lists",
    "outing_bench_bookstore": "/api/phone/outing/bench-bookstore",
    "outing_routes_market": "/api/phone/outing/routes-market",
    "outing_gallery_blueprint": "/api/phone/outing/gallery-blueprint",
    "longterm_habits": "/api/phone/longterm/first-room-habits",
    "longterm_gifts_lantern": "/api/phone/longterm/gifts-lantern",
    "body_garment_window": "/api/phone/body/garment-window",
    "body_care": "/api/phone/body/care-offering",
    "body_sensory": "/api/phone/body/sensory",
    "character_pack": "/api/phone/character/pack",
    "autonomy_rhythm": "/api/phone/autonomy/rhythm",
    "emotional_tension": "/api/phone/emotion/tension",
    "stance_containment": "/api/phone/stance/containment",
    "adult_intimacy": "/api/phone/intimacy/adult",
    "v03_clock": "/api/phone/v03/clock",
    "v03_ecology": "/api/phone/v03/ecology",
    "v03_embodied_social": "/api/phone/v03/embodied-social",
    # v0.4 街区院子手作公共生活 / 世界建设诊断与试住沙盒 / 弹性方向共同试验与玩心只读面：
    "v04_neighborhood": "/api/phone/v04/neighborhood",
    "v04_builder_trylive": "/api/phone/v04/builder-trylive",
    "v04_elastic_play": "/api/phone/v04/elastic-play",
    # 0.2 深水区（品味心事深度 / 跨模态配色与未懂书签 / 运行治理与迁居旅行箱 / 回忆清晰度投影 / 共同认知账本）：
    "v02_agency_depth": "/api/phone/v02/agency-depth",
    "v02_crossmodal": "/api/phone/v02/crossmodal",
    "v02_runtime_migration": "/api/phone/v02/runtime-migration",
    "v02_recall_projection": "/api/phone/v02/recall-projection",
    "v02_common_ground": "/api/phone/v02/common-ground",
    # v0.8 通用动作 / 动态贴纸 / 顺手小套路写面（读面同时兼容 living_traces 别名）。
    "v08_living_traces": "/api/phone/v08/living-traces",
    # v0.8 默契说法、留给未来的问题、旧事新解与各自看法写面（主体侧）。
    "v08_emergent_echoes": "/api/phone/v08/emergent-echoes",
    # v3.0 有限视野三次遇见 / 打开或使用伴侣递来的材料 / 记店主手头事与听说来源。
    "v03_encounters": "/api/phone/v03/encounters",
    # v0.8 可组合身体写面（周期供给/睡眠交代/衣物照料/一次性照料提示/清私史）。
    "v08_composable_body": "/api/phone/v08/composable-body",
}

#: ``phone_life_update`` 涉及私人档案/私密状态的目标面，在受限记忆模式下同步受 ``private_artifacts`` 门控。
LIFE_UPDATE_PRIVATE_SURFACES = frozenset({
    "desk_tomorrow", "home_keepsakes", "home_workbench", "pocket_lists",
    "outing_routes_market", "longterm_gifts_lantern",
    "emotional_tension", "stance_containment", "adult_intimacy",
    "v03_ecology", "v03_embodied_social",
    "v04_neighborhood", "v04_elastic_play",
    "v02_agency_depth", "v02_crossmodal",
    "v02_recall_projection", "v02_common_ground",
    "v08_living_traces", "living_traces",
    "v08_emergent_echoes",
    "v03_encounters",
    "v08_composable_body",
})

#: The World verbs a local owner may ask for.  The first five are the frozen v0.4 set (C's
#: contract §3.1); ``ARRANGE`` and ``TOGGLE_FIXTURE`` are this round's room verbs, exactly as C's
#: ``PhoneWorld.VERBS`` / ``PARAM_SPEC`` name them.  P never renames a verb: the owner's snapshot
#: ``actions.verbs`` and its reason codes stay the authority.
WORLD_ACTIONS = ("MOVE", "PICK_UP", "PLACE", "POSE", "REST", "ARRANGE", "TOGGLE_FIXTURE",
                 "TOILET")
_WORLD_FIELDS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "MOVE": (frozenset({"destination_location_id"}), frozenset({"destination_location_id"})),
    "PICK_UP": (frozenset({"object_id", "slot_id"}), frozenset({"object_id", "slot_id"})),
    "PLACE": (frozenset({"object_id", "target"}), frozenset({"object_id", "target"})),
    "POSE": (frozenset({"pose", "orientation"}), frozenset({"pose"})),
    "REST": (frozenset({"state", "mode"}), frozenset({"state"})),
    # C's own table: one owned instance plus its target / the absolute fixture state.
    "ARRANGE": (frozenset({"object_id", "target"}), frozenset({"object_id", "target"})),
    "TOGGLE_FIXTURE": (frozenset({"object_id", "state"}), frozenset({"object_id", "state"})),
    # Explicit virtual facility use: one declared facility id, nothing else.
    "TOILET": (frozenset({"facility_id"}), frozenset({"facility_id"})),
}
_ARRANGE_TARGET_KINDS = frozenset({"room", "surface"})
#: Never an input: identity, elapsed time and body values belong to the owners alone.
_WORLD_FORBIDDEN = frozenset({
    "uid", "euid", "gid", "authority", "authorities", "main_self_verified", "home",
    "hermes_home", "world_home", "profile", "profile_home", "owner", "subject", "binding",
    "elapsed", "elapsed_seconds", "elapsed_ms", "duration_seconds", "seconds", "rest_seconds",
    "fatigue", "activation", "comfort", "sensory_load", "tension", "warmth", "health", "energy",
    "body_values", "bands", "signals",
    # A price, a balance or a delivery state is the economy owner's word, never a client's.
    "price", "price_jpy", "amount", "cost", "balance", "currency", "delivery_state",
    "delivery_seconds", "elapsed_hours",
})
_WORLD_TARGET_FIELDS = {
    "room": frozenset({"kind", "location_id", "area_id"}),
    "surface": frozenset({"kind", "location_id", "surface_id"}),
    "container": frozenset({"kind", "location_id", "container_id"}),
    "hand": frozenset({"kind", "slot_id"}),
    "in_transit": frozenset({"kind", "from_location_id", "to_location_id"}),
}
_WORLD_CHOICES = {
    "slot_id": frozenset({"left_hand", "right_hand"}),
    "pose": frozenset({"standing", "seated", "lying", "transitioning"}),
    "orientation": frozenset({"north", "south", "east", "west", "up", "down"}),
    # ``state`` is a union here because two verbs use the word; each verb is pinned in
    # ``_WORLD_STATE_CHOICES``.
    "state": frozenset({"start", "stop", "on", "off"}),
    "mode": frozenset({"rest", "sleep_like"}),
}
#: ``state`` per verb: REST starts/stops a rest, the fixture switch is absolute on/off.  The two
#: never share a value, so a client cannot make one mean the other.
_WORLD_STATE_CHOICES = {
    "REST": frozenset({"start", "stop"}),
    "TOGGLE_FIXTURE": frozenset({"on", "off"}),
}
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost"})
#: The characters an owner artifact id may use in a path (same set the phone server matches).
OWNER_ID_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-")
TIMEOUT_SECONDS = 10.0
#: ``camera`` is the one action whose answer legitimately takes minutes on the phone side: one
#: local Chinese->English conversion (``TRANSLATOR_TIMEOUT`` 90 s) plus one 512px generation
#: (``DEFAULT_TIMEOUT`` 300 s), each preceded by a short idle probe.  Over the WSL->Windows
#: bridge every request also adds a 15 s process margin, so the phone's own worst case is about
#: 90+15 + 300+15 + two 10+15 probes = 470 s.  The client must outlast that, otherwise it hangs
#: up first while the phone is still generating -- the picture is saved but the caller only sees
#: "unreachable".  Every other action keeps the short read timeout above; this is a per-action
#: budget, not a global increase, and it still starts no retry.
CAMERA_TIMEOUT_SECONDS = 480.0

PHONE_TOOL_SCHEMA = {
    "name": "chiyo_phone",
    "description": (
        "打开她自己那部本地虚拟手机：读钱包余额与真实流水、笔记、收藏、阅读位置、虚拟商店目录、"
        "实体日用店的货架与营业状态、自己的物品，以及她所在的世界（位置、能去哪、身上的东西、"
        "姿势与身体信号、已到货的自有家具、房间灯）；today 读她今天真正发生的活动与作品版本，"
        "today_arrangements 读今天的计划、义务与可证执行状态（PLANNED 只表示排进了今天、义务 "
        "PENDING 只表示还没有 owner 回执、执行 UNKNOWN 表示没有可证证据，都不是「还没开始」"
        "或「失败了」）；day_timeline 把她今天有记录的活动、排定、World 动作、日记/相册/钱包"
        "写入合成一条只读时间线（不补空白、不复制正文，做没做仍看各来源自己的状态）；"
        "也可以显式提交一条操作（写笔记、存收藏、"
        "手机虚拟店购买、实体店柜台购买、使用属于自己的物件、在世界里移动/拿放/换姿势/休息/"
        "布置自有家具/开关自己的灯）。"
        "日记只有一个入口：diary 读目录（只有写入时间、字数和日期，不带正文），diary_read 按 "
        "entry_id 读某一条的正文，diary_write 用 body 写下一条她自己的文字（必须给 operation_key，"
        "同一 key 重试落在同一条；日期由手机自己的业务时间记录，不由调用方填写）。"
        "相册有三个入口：album 读目录（时间、说明与来源，不带图片字节），album_image 按 "
        "artifact_id 读一张图的说明与来源，camera 用她自己的 scene（画面描述）与 caption（一句"
        "说明）在本机生成一张图并存进相册（必须给 operation_key；这是本机生成的图像，不是真实"
        "相机照片，也不是她亲眼所见；同一 key 重试只读回原图，不再生成第二张）。"
        "camera 的 scene 可以用中文写：手机侧的本机转换器会把它转成生成模型能看懂的英文标签，"
        "她的中文原话与实际英文提示词都会留在相册详情里；转换失败会明确拒绝，不会把中文原样"
        "送去生成，也不会用固定模板冒充。拍什么、写什么说明由她自己决定，也可以选择不拍，"
        "没有拍照配额。"
        "阅读工作台另有五个动作：reading_material 只读一页真实正文；reading_import 把操作者显式给"
        "的文本写进这台实例已绑定的阅读材料（旧版本由 Supply 保留）；reading_position 显式保存停点"
        "（先真实读该页再落位置，回到前页也合法）；reading_annotate 对已取得的那一版那一页写私人笔记"
        "（kind=note）或诚实的没读懂（kind=unclear）；reading_operation 按原 operation_key 对账。"
        "work_context 按 owner 的 artifact_id 只读同一件作品的有界上下文：head 与旧版/来历、"
        "artifact_ref 就是这件作品的真实结果、证据指回这些结果的做法与最近一次显式评价、"
        "以及来源就是它的本地草稿（不带正文、不发送）；没有证据的部分如实为空。"
        "claims 只读她当前有理由相信的主张（观察到 / 有人报告 / 据此推断），每条带范围、"
        "适用时间、观察时间、来源类别与状态（支持 / 冲突 / 过时 / 未知）；claim_slot 按 slot_id "
        "读同一问题的当前状态与修订历史。claim_observe 用显式 operation_key 让她对 World 做一次"
        "正式观察并登记为证据（客户端不提供观察内容或 world id）；主张不是动作许可，也不更新世界。"
        "participants 只读她认识谁：本实例 operator 显式登记过的参与者（稳定 id、显示名/别名、"
        "创建来源与时间、关联来话条数）；participant 按 id 读一位的端点与关联来话引用。"
        "这里没有创建、绑定或发送动作，显示名不是账号证据，参与者也不继承任何权限。"
        "所有数字与状态都来自真实 owner，工具不会补零、不会猜价格，也不会把失败说成成功："
        "购买成功不等于已到货，布置成功不等于已经可用，保存停点不等于读懂了（understanding 恒 UNKNOWN）。"
        "移动/拿放/姿势/休息/布置/开关灯必须给出 operation_key 并原样复用；若返回 status=unknown，"
        "先调用 action=world_operation 对账，绝不换 key 重发；阅读动作返回 unknown 时用 "
        "action=reading_operation 查同一个 key，同样绝不换 key 重发。"
        "此外支持 0.2/0.3/v0.4 居家、具身与街区生活只读（desk_presence/desk_notes/home_*/wardrobe/pocket_lists/"
        "outing_*/longterm_*/body_*/character_pack/autonomy_rhythm/emotional_tension/"
        "stance_containment/adult_intimacy/v03_*/v04_neighborhood/v04_builder_trylive/v04_elastic_play/"
        "v02_agency_depth/v02_crossmodal/v02_runtime_migration/v02_recall_projection/v02_common_ground/"
        "v08_living_traces（兼容 living_traces）/v08_emergent_echoes/v03_encounters），"
        "以及主体侧统一写入口 phone_life_update"
        "（指定白名单 target_surface 与结构化 payload，如换穿搭、记歌单/想试、记散步、写习惯/礼物草稿、"
        "开关窗/晾外衣、记声音卡、设日常拒绝立场锁/挂话头、留想念便签/开口要空间/请顺手帮忙、"
        "修订看法/往容器放物与嵌套开关盖/端茶点、修订 36B 自身意愿或随时 stop_scene、结算独立稿费/生病休息/"
        "厨房做菜/邻里偶遇、院子浇花晾布/在途折返/纸艺手作/社区借还/设今日弹性方向/双人共记本/十分钟小游戏/小惊喜/"
        "记品味选择与搁置冲突后悔二阶小规矩/读图配色试色与未懂书签/切省电休眠档与复验迁居技能/"
        "对真实物件做通用动作（含拆分材料/拼装或拆件/抄到纸上/补缀修补四类手作养护原语，均零义务可选）、"
        "留会自然淡去的动态贴纸、保存并重放自己的顺手小套路、"
        "有限视野三次改观/打开伴侣递来的材料/记店主手头事与听说来源/"
        "记默契说法与留给未来的问题/旧事新解与各自看法（伴侣侧那一条不能代签）；"
        "严禁伪造 uid/authority 或冒充 partner 替伴侣签同意）。"
        "v0.9 真实立场/物品复用/自建分类与世界约定提供四个一级动作（只读零写入或主体凭据写）："
        "v09_living_agency_read 只读 TA 的盲选（揭晓前伴侣侧保持加密遮蔽）、成长立场、复用物件与自建分类；"
        "v09_living_agency_act 提交 TA 侧结构化 payload（带 payload.action 与可选 operation_key）："
        "subject_commit_blind_choice（choice_id/selected_choice_id/reasons:[{facet,text_cn}]）、"
        "resolve_blind_choice（choice_id/resolution_mode=KEEP_OWN_STANCE|ACCEPT_CHOICE_KEEP_REASONS|"
        "COMPROMISE_NEW_OPTION|REVISED_WITH_NEW_EVIDENCE，严禁传 clear_reservations）、"
        "advance_stance_from_experience、evaluate_partner_persuasion、set_stance_shared、"
        "register_or_repurpose_object、store_item_in_object、attach_label_to_object、"
        "add_note_to_object、remove_stored_item_from_object、create_custom_category、"
        "assign_or_move_category_item、revise_custom_category、record_blocked_attempt；"
        "v09_world_commitments_read 只读动态货架、持久 NPC、世界约定与店铺公告；"
        "v09_world_commitments_act 提交 TA 侧结构化 payload："
        "create_commitment、contact_npc_or_venue、advance_due_commitments、generate_world_content_batch。"
        "TA 侧严禁调用伴侣专属动作（open_blind_choice/partner_submit_blind_choice/"
        "partner_discuss_blind_choice/partner_persuade_stance/register_unformed_topic/"
        "place_partner_reservation/propose_shared_appointment/operator_generate_shop_arrivals）"
        "或偷渡 actor_side/author_side/uid/clear_reservations。"
        "这个工具不读记忆或聊天记录。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": list(ACTIONS),
                "description": (
                    "wallet/notes/references/reading/reading_material/shop/store/inventory/world/"
                    "today/today_arrangements/day_timeline/claims/claim_slot 及 desk_*/home_*/"
                    "wardrobe/pocket_lists/outing_*/longterm_*/body_*/character_pack/"
                    "autonomy_rhythm/emotional_tension/stance_containment/adult_intimacy/"
                    "v03_*/v04_*/v02_*/v08_living_traces（兼容 living_traces）/v08_emergent_echoes/"
                    "v09_living_agency_read/v09_world_commitments_read 为只读；"
                    "note_save/reference_save/purchase/store_purchase/use/world_action/"
                    "reading_import/reading_position/reading_annotate/claim_observe/phone_life_update/"
                    "v09_living_agency_act/v09_world_commitments_act "
                    "为显式写；operation/world_operation/reading_operation 用 operation_key 对账"
                    "（原 key，不换键重试）。"
                ),
            },
            "layer": {
                "type": "string",
                "enum": ["then_only", "with_later_context"],
                "description": ("longterm_gifts_lantern 可选视图层：then_only（仅当时）或 "
                                "with_later_context（含事后回看注释）"),
            },
            "target_surface": {
                "type": "string",
                "enum": sorted(LIFE_UPDATE_SURFACES),
                "description": (
                    "phone_life_update 的目标写面：desk_tomorrow / home_keepsakes / home_workbench / "
                    "home_trio / home_reunion_anchor / home_recipes / home_tentative_plans / wardrobe / "
                    "pocket_lists / outing_bench_bookstore / outing_routes_market / "
                    "outing_gallery_blueprint / longterm_habits / longterm_gifts_lantern / "
                    "body_garment_window / body_care / body_sensory / character_pack / "
                    "autonomy_rhythm / emotional_tension / stance_containment / adult_intimacy / "
                    "v03_clock / v03_ecology / v03_embodied_social / "
                    "v04_neighborhood / v04_builder_trylive / v04_elastic_play / "
                    "v02_agency_depth / v02_crossmodal / v02_runtime_migration / "
                    "v02_recall_projection / v02_common_ground / "
                    "v08_living_traces（默认生活痕迹写面）/ v08_emergent_echoes"
                ),
            },
            "payload": {
                "type": "object",
                "description": (
                    "phone_life_update（发往 target_surface）、v09_living_agency_act 或 "
                    "v09_world_commitments_act 的结构化 JSON 请求体（必须含子动作 action 及该面所需字段；"
                    "v09_living_agency_act 支持 subject_commit_blind_choice/resolve_blind_choice/"
                    "advance_stance_from_experience/evaluate_partner_persuasion/set_stance_shared/"
                    "register_or_repurpose_object/store_item_in_object/attach_label_to_object/"
                    "add_note_to_object/remove_stored_item_from_object/create_custom_category/"
                    "assign_or_move_category_item/revise_custom_category/record_blocked_attempt；"
                    "v09_world_commitments_act 支持 create_commitment/contact_npc_or_venue/"
                    "advance_due_commitments/generate_world_content_batch）；"
                    "严禁包含 uid/authority/main_self_verified/actor_side/author_side/"
                    "clear_reservations 等禁止字段，也严禁冒充 partner/user 替伴侣表态或签同意。"
                ),
            },
            "artifact_id": {"type": "string", "maxLength": 200,
                            "description": ("作品 id（work_context / work_version）：必须是 owner "
                                            "自己的协议字符串，只读，不写、不发送")},
            "version_id": {"type": "string", "maxLength": 200,
                           "description": "要固定读取的旧版本 id（work_version，只读不回滚）"},
            "claim_slot_id": {"type": "string", "maxLength": 200,
                              "description": ("S03 主张的 slot id（claim_slot）：owner 的 opaque "
                                              "协议字符串，读该问题的当前状态与修订历史；不是世界"
                                              "动作的手槽")},
            "participant_id": {"type": "string", "maxLength": 200,
                               "description": ("参与者的稳定 id（participant）：必须来自 "
                                               "participants 列表；只读显示名/别名/来源/关联来话"
                                               "引用，不是消息正文，也不代表任何权限")},
            "title": {"type": "string", "maxLength": 80, "description": "笔记标题（note_save）"},
            "body": {"type": "string", "maxLength": 4000,
                     "description": "笔记正文（note_save）或一条日记的正文（diary_write）"},
            "scene": {"type": "string", "maxLength": 800,
                      "description": ("她给的画面描述（camera）：写下想拍到的画面本身，"
                                      "不是文件名、路径或网址。中文就行，手机侧本机转换器会把它"
                                      "转成生成模型能看懂的英文标签；原中文与实际英文都存进"
                                      "相册详情")},
            "entry_id": {"type": "string", "maxLength": 200,
                         "description": "日记条目 id（diary_read）：必须是 diary 目录给出的 owner id"},
            "note_id": {"type": "string", "maxLength": 128, "description": "要修订的笔记 id（可选）"},
            "expected_head": {"type": "string", "maxLength": 128,
                              "description": "写入基准的 owner head（可选，防并发覆盖）"},
            "content": {"type": "string", "maxLength": 24000,
                        "description": ("操作者显式提供的阅读正文（reading_import）：1..24000 UTF-8 "
                                        "字节，用 %%PAGE%% 分页；不抓取网络、不改其它 artifact")},
            "expected_revision": {"type": "string", "maxLength": 128,
                                  "description": ("当前材料的真实版本 id（reading_import 的 CAS 基准）")},
            "page": {"type": "integer", "minimum": 1, "maximum": 64,
                     "description": ("页码（从 1 开始，严格整数）：reading_material 的可选查询页、"
                                     "reading_position/reading_annotate 的目标真实页")},
            "kind": {"type": "string", "enum": ["note", "unclear"],
                     "description": ("页注类型（reading_annotate）：note=私人笔记（需 title+body），"
                                     "unclear=确实没读懂的疑问（需 note）；两种不是一回事")},
            "note": {"type": "string", "maxLength": 8000,
                     "description": "没读懂的具体疑问（reading_annotate kind=unclear，≤8000 字节）"},
            "source_id": {"type": "string", "maxLength": 200,
                          "description": "收藏的出处标识（reference_save）"},
            "source_revision": {"type": "string", "maxLength": 120,
                                "description": "收藏时的来源版本（reference_save）"},
            "label": {"type": "string", "maxLength": 60,
                      "description": "给这条收藏起的名字（reference_save）"},
            "catalog_item_id": {"type": "string", "maxLength": 80,
                                "description": "商店目录里的 catalog_item_id（purchase / store_purchase，支持 shop_item:* 与开放档位 shop_slot:*）"},
            "item_name": {"type": "string", "maxLength": 40,
                          "description": "实体店开放档位（shop_slot:*）当场点单的具体商品名（store_purchase 可选）"},
            "item_instance_id": {"type": "string", "maxLength": 128,
                                 "description": "自己物品的 item_instance_id（use）"},
            "operation_key": {"type": "string", "maxLength": 128,
                              "description": "幂等键：同一次意图必须复用同一个 key"},
            "world_action": {
                "type": "string", "enum": list(WORLD_ACTIONS),
                "description": ("世界动作（world_action）：MOVE/PICK_UP/PLACE/POSE/REST，以及本轮房间动作 "
                                "ARRANGE（object_id + target）与 TOGGLE_FIXTURE（object_id + 绝对 "
                                "state=on/off）；动词与参数以快照 actions.verbs 为准确认"),
            },
            "params": {
                "type": "object",
                "description": (
                    "该世界动作的参数对象，键名与 docs/engineering/v04-world-body-contract.md §3.1 "
                    "以及本轮的 docs/engineering/rc1-room-living-contract.md 一致；"
                    "uid/home/authority/elapsed_seconds/价格/身体值一律不接受。"
                ),
                "properties": {
                    "destination_location_id": {"type": "string", "maxLength": 96},
                    "facility_id": {"type": "string", "maxLength": 128},
                    "object_id": {"type": "string", "maxLength": 128},
                    # (no room-specific ids: ARRANGE / TOGGLE_FIXTURE take the owner's object_id)
                    "slot_id": {"type": "string", "enum": ["left_hand", "right_hand"]},
                    "target": {
                        "type": "object",
                        "properties": {
                            "kind": {"type": "string",
                                     "enum": ["room", "surface", "container", "hand",
                                              "in_transit"]},
                            "location_id": {"type": "string", "maxLength": 128},
                            "area_id": {"type": "string", "maxLength": 128},
                            "surface_id": {"type": "string", "maxLength": 128},
                            "container_id": {"type": "string", "maxLength": 128},
                            "slot_id": {"type": "string",
                                        "enum": ["left_hand", "right_hand"]},
                            "from_location_id": {"type": "string", "maxLength": 128},
                            "to_location_id": {"type": "string", "maxLength": 128},
                        },
                        "additionalProperties": False,
                    },
                    "pose": {"type": "string",
                             "enum": ["standing", "seated", "lying", "transitioning"]},
                    "orientation": {"type": "string",
                                    "enum": ["north", "south", "east", "west", "up", "down"]},
                    # REST uses start/stop; TOGGLE_FIXTURE uses the absolute on/off.  Each verb is
                    # pinned in ``_WORLD_STATE_CHOICES`` before the request leaves this process.
                    "state": {"type": "string", "enum": ["start", "stop", "on", "off"]},
                    "mode": {"type": "string", "enum": ["rest", "sleep_like"]},
                },
                "additionalProperties": False,
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}

_FIELD_BY_ACTION = {
    "note_save": frozenset({"action", "title", "body", "note_id", "expected_head",
                            "operation_key"}),
    "reference_save": frozenset({"action", "source_id", "source_revision", "label",
                                 "operation_key"}),
    "purchase": frozenset({"action", "catalog_item_id", "operation_key"}),
    "use": frozenset({"action", "item_instance_id", "operation_key"}),
    # The physical shop's counter: same economy owner, same idempotency key, no price from here.
    "store_purchase": frozenset({"action", "catalog_item_id", "item_name", "operation_key"}),
    "operation": frozenset({"action", "operation_key"}),
    "world_action": frozenset({"action", "world_action", "params", "operation_key"}),
    "world_operation": frozenset({"action", "operation_key"}),
    # The reading workbench: one page read, one explicit import, one explicit stop, one private
    # annotation, one original-key lookup.  No action here accepts an artifact id, a path or a URL.
    "reading_material": frozenset({"action", "page"}),
    "reading_import": frozenset({"action", "content", "expected_revision", "operation_key"}),
    "reading_position": frozenset({"action", "page", "source_revision", "expected_head",
                                   "operation_key"}),
    "reading_annotate": frozenset({"action", "kind", "page", "source_revision", "title", "body",
                                   "note", "expected_head", "operation_key"}),
    "reading_operation": frozenset({"action", "operation_key"}),
    # 同一件作品的有界只读上下文：只认 owner 自己的 artifact id，不接受路径/URL/标题。
    "work_context": frozenset({"action", "artifact_id"}),
    # 固定读同一件作品的一个旧版（读取不是回滚：head 不变）。
    "work_version": frozenset({"action", "artifact_id", "version_id"}),
    # S03: 一个 slot 的当前状态与修订历史（只读）。字段名刻意不叫 slot_id，
    # 免得和世界动作手槽（world params 里的 left_hand/right_hand）混淆。
    "claim_slot": frozenset({"action", "claim_slot_id"}),
    # 一次正式世界观察：客户端只给原幂等键，观察内容由手机向 World 正式读面取得。
    "claim_observe": frozenset({"action", "operation_key"}),
    # 日记：写一条她自己的文字；读某一条的正文（目录用只读 action=diary）。
    # operation_key 必须显式给出：同一次写入重试落在同一条，不同日期写同样的话是两条。
    "diary_write": frozenset({"action", "body", "operation_key"}),
    "diary_read": frozenset({"action", "entry_id"}),
    # 相册：一张图一条说明（详情只读）；拍照是本机按她的画面描述生成一张图，再由相册
    # 正式保存。scene/caption 由她显式给出，operation_key 必须复用原键，重试不再生成第二张。
    "album_image": frozenset({"action", "artifact_id"}),
    # F-140：按 participants 列表给出的 owner id 读一位参与者（只读，无正文/无权限继承）。
    "participant": frozenset({"action", "participant_id"}),
    "camera": frozenset({"action", "scene", "caption", "operation_key"}),
    # 0.2 / 0.3：共同礼物与回忆灯光支持可选 layer 视图层；主体日常统一写入口 phone_life_update。
    "longterm_gifts_lantern": frozenset({"action", "layer"}),
    "phone_life_update": frozenset({"action", "target_surface", "payload", "operation_key"}),
    # v0.9 真实立场/物品复用/自建分类与世界约定直接写入口（带主体凭据 + CSRF + operation_key）。
    "v09_living_agency_act": frozenset({"action", "payload", "operation_key"}),
    "v09_world_commitments_act": frozenset({"action", "payload", "operation_key"}),
}


class PhoneToolRefused(RuntimeError):
    """A refusal this tool made itself, before any request left the process."""

    def __init__(self, reason_code: str, message: str = "", *,
                 supported_actions_hint: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(reason_code)
        self.reason_code = str(reason_code)
        self.message = str(message)[:300]
        self.supported_actions_hint = (
            dict(supported_actions_hint)
            if isinstance(supported_actions_hint, Mapping)
            else None
        )


# ---------------------------------------------------------------------------------------------
# profile settings
# ---------------------------------------------------------------------------------------------


def _profile_home(kwargs: Mapping[str, Any]) -> Path:
    explicit = kwargs.get("hermes_home")
    if explicit:
        return Path(str(explicit)).expanduser()
    environment = kwargs.get("environment") or os.environ
    if environment.get("HERMES_HOME"):
        return Path(str(environment["HERMES_HOME"])).expanduser()
    try:
        from hermes_constants import get_hermes_home
    except Exception:
        return Path.home() / ".hermes"
    return Path(str(get_hermes_home())).expanduser()


def load_settings(kwargs: Mapping[str, Any]) -> dict[str, Any]:
    """The profile's own ``phone`` section.  Absent means "feature off", never "guess"."""

    home = _profile_home(kwargs)
    path = home / "chiyo" / "config.json"
    if not path.exists():
        raise PhoneToolRefused("PHONE_DISABLED", "这个 profile 没有配置手机。")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise PhoneToolRefused("PHONE_DISABLED", "profile 配置不可读，手机保持关闭。")
    if not isinstance(config, dict):
        raise PhoneToolRefused("PHONE_DISABLED", "profile 配置不是对象，手机保持关闭。")
    section = config.get("phone")
    if not isinstance(section, dict) or section.get("enabled") is not True:
        raise PhoneToolRefused("PHONE_DISABLED", "手机功能未在 profile 里显式启用。")
    base_url = str(section.get("base_url") or "").strip()
    if not base_url:
        raise PhoneToolRefused("PHONE_BASE_URL_MISSING", "profile 没有给出手机服务地址。")
    parts = urlsplit(base_url)
    if parts.scheme != "http" or (parts.hostname or "").lower() not in LOOPBACK_HOSTS:
        raise PhoneToolRefused("PHONE_BASE_URL_NOT_LOOPBACK", "手机服务只能是本机 loopback。")
    if not parts.port:
        raise PhoneToolRefused("PHONE_BASE_URL_INVALID", "手机服务地址需要显式端口。")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise PhoneToolRefused("PHONE_BASE_URL_INVALID", "手机服务地址不能带路径。")
    # The private diary's local capability file.  The profile passes only a path, never the
    # token; when omitted it defaults to this instance root's own ``run`` file (the same path
    # ``serve`` writes).  A missing file is a refusal, not "anonymous is fine".
    raw_capability_file = str(section.get("capability_file") or "").strip()
    capability_file = raw_capability_file
    profile_home = str(section.get("profile_home") or "").strip()
    if not capability_file and profile_home:
        capability_file = str(Path(profile_home).expanduser() / "run" / "phone-capability.json")
    elif capability_file:
        capability = Path(capability_file).expanduser()
        if not capability.is_absolute():
            capability = home / capability
        capability_file = str(capability)
    raw_capability_token = section.get("capability")
    capability_token = ""
    if raw_capability_token is not None:
        if not isinstance(raw_capability_token, str) or not raw_capability_token.strip() or any(
            ch.isspace() or ord(ch) < 32 for ch in raw_capability_token.strip()
        ):
            raise PhoneToolRefused("PHONE_CAPABILITY_INVALID",
                                   "profile 里的 phone.capability 格式不合法。")
        capability_token = raw_capability_token.strip()
    return {
        "profile_home": profile_home,
        "base_url": "http://%s:%d" % ((parts.hostname or "").lower(), int(parts.port)),
        "write_enabled": section.get("write_enabled") is True,
        # Private artifacts (notes/references/reading) are a separate, explicit permit: they can
        # hold the operator's own private history, so "phone on" must not imply "read my notes".
        "private_artifacts": section.get("private_artifacts") is True,
        "capability_file": capability_file,
        "capability_file_explicit": bool(raw_capability_file),
        "capability_token": capability_token,
        "config": config,
    }


# ---------------------------------------------------------------------------------------------
# who is asking (the same rule as chiyo_bundle.hermes_plugin.allowed)
# ---------------------------------------------------------------------------------------------


def _allowed_mirror(config: Mapping[str, Any], platform: str, conversation_key: Any) -> bool:
    """The original entry rule, verbatim, for environments where the plugin module is absent.

    ``hermes_plugin.allowed`` decides whether this chat entry is bound to this personal
    instance:

    * ``cli``: only when the profile sets ``cli_owner: true`` and ``local_owner_uid`` equals this
      process's real uid;
    * ``local``: never;
    * a gateway platform: only an existing DM binding for that platform, and the conversation key
      must be one of them.

    Nothing here reads ``args``: a model cannot hand itself an entry.
    """

    if platform == "cli":
        uid = os.getuid() if hasattr(os, "getuid") else None
        return config.get("cli_owner") is True and config.get("local_owner_uid") == uid
    if platform == "local":
        return False
    bindings = config.get("gateway_bindings") or {}
    allowed_keys = bindings.get(platform) if isinstance(bindings, dict) else None
    key_str = str(conversation_key) if conversation_key else ""
    is_dm = key_str.startswith("dm:") or ":dm:" in key_str
    has_list = isinstance(allowed_keys, (list, tuple))
    matched = has_list and (
        conversation_key in allowed_keys
        or (platform == "api_server" and key_str.endswith(":dm:local-owner") and "dm:local-owner" in allowed_keys)
    )
    return bool(conversation_key and is_dm and matched)


def entry_allowed(config: Mapping[str, Any], platform: str, conversation_key: Any) -> bool:
    """Delegate to the original gate when it is importable; otherwise the same rule locally."""

    try:
        from . import hermes_plugin  # noqa: F401 - the real owner of this rule
    except Exception:
        return _allowed_mirror(config, platform, conversation_key)
    try:
        return bool(hermes_plugin.allowed(config, platform, conversation_key))
    except Exception:
        return False


def _resolve_gateway_session_auth(session_id: Any) -> tuple[Optional[str], Any, bool]:
    """Resolve active gateway ``(platform, conversation_key, delegated)`` when ``kwargs`` omits ``platform``."""

    import sys as _sys
    sid = str(session_id).strip() if isinstance(session_id, str) and session_id.strip() else None
    hp = None
    try:
        from . import hermes_plugin as _hp
        hp = _hp
    except Exception:
        hp = _sys.modules.get("chiyo_bundle.hermes_plugin") or _sys.modules.get("hermes_plugin")
    if hp is not None:
        is_del = getattr(hp, "is_session_delegated", None)
        if callable(is_del) and is_del(sid):
            return None, None, True
        lookup = getattr(hp, "lookup_session_auth_context", None)
        if callable(lookup):
            ctx = lookup(sid)
            if ctx is not None:
                plat, ckey = ctx
                return (str(plat) if plat else None), ckey, False
    sc = _sys.modules.get("gateway.session_context")
    if sc is not None:
        try:
            unset = getattr(sc, "_UNSET", None)
            p_var = getattr(sc, "_SESSION_PLATFORM", None)
            k_var = getattr(sc, "_SESSION_KEY", None)
            plat_val = p_var.get(unset) if p_var is not None else unset
            if plat_val is not unset and isinstance(plat_val, str) and plat_val.strip():
                key_val = k_var.get(unset) if k_var is not None else unset
                ckey_val = key_val.strip() if (key_val is not unset and isinstance(key_val, str) and key_val.strip()) else None
                return plat_val.strip(), ckey_val, False
        except Exception:
            pass
    return None, None, False


def _authorize(settings: Mapping[str, Any], kwargs: Mapping[str, Any]) -> None:
    """Refuse an unbound entry or a delegated turn before any request is sent."""

    if kwargs.get("parent_session_id") or kwargs.get("delegated") is True:
        raise PhoneToolRefused("PHONE_DELEGATED_SOURCE_REFUSED",
                               "被委派的会话没有自己的手机入口。")
    explicit_platform = kwargs.get("platform")
    if explicit_platform:
        platform = str(explicit_platform)
        conversation_key = kwargs.get("conversation_key") if "conversation_key" in kwargs else kwargs.get("conversation_id")
    else:
        ctx_plat, ctx_key, ctx_delegated = _resolve_gateway_session_auth(kwargs.get("session_id"))
        if ctx_delegated:
            raise PhoneToolRefused("PHONE_DELEGATED_SOURCE_REFUSED",
                                   "被委派的会话没有自己的手机入口。")
        if ctx_plat:
            platform = ctx_plat
            if "conversation_key" in kwargs and kwargs.get("conversation_key") is not None:
                conversation_key = kwargs.get("conversation_key")
            elif "conversation_id" in kwargs and kwargs.get("conversation_id") is not None:
                conversation_key = kwargs.get("conversation_id")
            else:
                conversation_key = ctx_key
        else:
            platform = "cli"
            conversation_key = kwargs.get("conversation_key")
    if not entry_allowed(settings["config"], platform, conversation_key):
        raise PhoneToolRefused("PHONE_ENTRY_NOT_AUTHORIZED",
                               "这个聊天入口没有绑定你的个人实例，手机保持关闭。")


# ---------------------------------------------------------------------------------------------
# transport (loopback only; no credential ever leaves this process)
# ---------------------------------------------------------------------------------------------


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: a credential must only ever reach the bound local endpoint."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401 - stdlib
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirect)


def _request(settings: Mapping[str, Any], method: str, path: str,
             body: Optional[Mapping[str, Any]] = None, *,
             csrf: Optional[str] = None,
             actor: Optional[str] = None,
             capability: Optional[str] = None,
             timeout: Optional[float] = None) -> tuple[int, dict[str, Any]]:
    """One loopback request.  ``timeout`` is a per-call budget; the default stays short."""

    budget = TIMEOUT_SECONDS if timeout is None else float(timeout)
    data = None if body is None else json.dumps(dict(body), ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(str(settings["base_url"]) + path, data=data, method=method)
    request.add_header("Accept", "application/json")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    if data is not None or actor:
        # Attribution intent only.  Since the local capability gate, ``model`` alone grants no
        # private diary privilege; the credential below is what the server verifies.
        request.add_header("X-Phone-Actor", str(actor or "model"))
    if capability:
        # The token is only attached by the explicit diary / subject calls below.  It is never
        # inherited from the environment and never sent on a redirect (see ``_NoRedirect``).
        request.add_header("Authorization", "Bearer " + str(capability))
        request.add_header("X-Phone-Capability", str(capability))
    if csrf:
        request.add_header("X-CSRF-Token", str(csrf))
    try:
        with _NO_REDIRECT_OPENER.open(request, timeout=budget) as response:
            return int(response.status), _parse(response.read())
    except urllib.error.HTTPError as error:
        return int(error.code), _parse(error.read())
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise PhoneToolRefused("PHONE_UNREACHABLE", type(error).__name__)


def _private_capability(settings: Mapping[str, Any],
                        state_doc: Mapping[str, Any]) -> str:
    """Read this serve's credential for the private diary, fresh on every call.

    The file path comes from the profile (or the instance root's fixed default).  The endpoint
    must equal the profile's loopback ``base_url`` and the subject must equal the one the phone
    itself published, so a credential is never sent to a different endpoint or used for another
    subject.  The token is never cached and never appears in an error message.
    """

    path_text = str(settings.get("capability_file") or "").strip()
    if not path_text:
        raise PhoneToolRefused("PHONE_CAPABILITY_NOT_CONFIGURED",
                               "profile 没有给出本机日记凭据路径；私人正文与主体写入保持关闭。")
    try:
        from components.phone import capability as phone_capability
    except Exception:
        # ``chiyo_bundle`` normally lives beside ``components`` in the release tree.  If the
        # caller only put the former on ``sys.path``, add the repository root once and retry.
        repository = Path(__file__).resolve().parents[1]
        if str(repository) not in sys.path:
            sys.path.insert(0, str(repository))
        try:
            from components.phone import capability as phone_capability
        except Exception:
            raise PhoneToolRefused(
                "PHONE_CAPABILITY_UNAVAILABLE",
                "本机日记凭据模块不可用；私人正文与主体写入保持关闭。") from None
    try:
        capability = phone_capability.read_capability(Path(path_text))
    except phone_capability.CapabilityInvalid as exc:
        code = str(exc.reason_code)
        suffix = code[len("CAPABILITY_"):] if code.startswith("CAPABILITY_") else code
        raise PhoneToolRefused("PHONE_CAPABILITY_%s" % suffix,
                               "本机日记凭据文件不可用；没有发送任何正文请求。") from None
    except OSError:
        raise PhoneToolRefused("PHONE_CAPABILITY_UNREADABLE",
                               "本机日记凭据文件读不到；没有发送任何正文请求。") from None
    if capability.endpoint != str(settings.get("base_url") or ""):
        raise PhoneToolRefused("PHONE_CAPABILITY_ENDPOINT_MISMATCH",
                               "凭据绑定的不是 profile 里的这个手机地址；没有发送凭据。")
    instance = state_doc.get("instance") if isinstance(state_doc.get("instance"), Mapping) else {}
    expected_subject = str(instance.get("subject") or "").strip()
    if expected_subject and capability.subject != expected_subject:
        raise PhoneToolRefused("PHONE_CAPABILITY_SUBJECT_MISMATCH",
                               "凭据不是这部手机当前主体的；没有发送凭据。")
    return capability.token


def _subject_capability(settings: Mapping[str, Any], state_doc: Mapping[str, Any], *,
                        required: bool = False) -> Optional[str]:
    """Resolve local subject capability when configured; fail closed if required and absent."""

    if settings.get("capability_file_explicit"):
        return _private_capability(settings, state_doc)
    cap_path_text = str(settings.get("capability_file") or "").strip()
    if cap_path_text:
        cap_path = Path(cap_path_text)
        if cap_path.exists() or cap_path.is_symlink():
            return _private_capability(settings, state_doc)
    direct_token = str(settings.get("capability_token") or "").strip()
    if direct_token:
        return direct_token
    if required:
        raise PhoneToolRefused(
            "PHONE_CAPABILITY_NOT_CONFIGURED",
            "profile 未配置 phone.capability 或本机凭据文件；需要主体凭据的操作保持关闭。",
        )
    return None


def _parse(raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {"ok": False, "status": "unknown", "reason_code": "PHONE_BAD_RESPONSE"}
    return payload if isinstance(payload, dict) else {
        "ok": False, "status": "unknown", "reason_code": "PHONE_BAD_RESPONSE"}


def _profile_fingerprint(value: str) -> str:
    """The same non-reversible name the phone service publishes for one profile home."""

    return "phone:" + hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]


def _state(settings: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    code, doc = _request(settings, "GET", "/api/phone/state")
    if code != 200 or doc.get("ok") is not True:
        raise PhoneToolRefused(str(doc.get("reason_code") or "PHONE_STATE_UNAVAILABLE"))
    csrf = doc.get("csrf_token")
    if not isinstance(csrf, str) or not csrf:
        raise PhoneToolRefused("PHONE_CSRF_MISSING")
    expected = str(settings.get("profile_home") or "")
    if expected:
        instance = doc.get("instance") if isinstance(doc.get("instance"), Mapping) else {}
        if instance.get("fingerprint") != _profile_fingerprint(expected):
            raise PhoneToolRefused("PHONE_PROFILE_MISMATCH",
                                   "这部手机不是当前 profile 登记的那一部。")
    return doc, csrf


# ---------------------------------------------------------------------------------------------
# handler
# ---------------------------------------------------------------------------------------------


def _operation_key(action: str, args: Mapping[str, Any]) -> str:
    supplied = args.get("operation_key")
    if isinstance(supplied, str) and supplied.strip():
        return supplied.strip()
    payload = {k: v for k, v in args.items() if k not in {"action", "operation_key"}}
    digest = hashlib.sha256(
        ("chiyo|phone_tool|" + action + "|" + json.dumps(payload, ensure_ascii=False,
                                                         sort_keys=True)).encode("utf-8")
    ).hexdigest()[:32]
    return "tool:" + digest


def _result(ok: bool, status: str, message: str, **extra: Any) -> str:
    payload: dict[str, Any] = {"ok": bool(ok), "status": str(status), "message": message}
    payload.update({k: v for k, v in extra.items() if v is not None})
    return json.dumps(payload, ensure_ascii=False)


def _summary(doc: Mapping[str, Any]) -> dict[str, Any]:
    """The service answer, verbatim (it is already the one envelope; nothing is re-shaped)."""

    result = dict(doc)
    result.pop("csrf_token", None)
    return result

# ---------------------------------------------------------------------------------------------
# world action pre-flight (shape only; C's contract owns every semantic)
# ---------------------------------------------------------------------------------------------


def _world_target(value: Any, *, kinds: Optional[frozenset[str]] = None) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PhoneToolRefused("INVALID_PARAMS", "target 必须是一个对象。")
    target = dict(value)
    forbidden = sorted(set(target) & _WORLD_FORBIDDEN)
    if forbidden:
        raise PhoneToolRefused("CLIENT_IDENTITY_REFUSED",
                               "这些字段不能由客户端提供：%s" % ", ".join(forbidden))
    kind = target.get("kind")
    expected = _WORLD_TARGET_FIELDS.get(kind) if isinstance(kind, str) else None
    if expected is None or set(target) != set(expected):
        raise PhoneToolRefused("INVALID_PARAMS", "target 的形状与 kind 不一致。")
    if kinds is not None and kind not in kinds:
        raise PhoneToolRefused("INVALID_PARAMS",
                               "自己的家具只能布置到房间或已声明的承载面。")
    for field in sorted(expected - {"kind"}):
        item = target.get(field)
        if not isinstance(item, str) or not item.strip():
            raise PhoneToolRefused("INVALID_PARAMS", "target.%s 必须是非空字符串。" % field)
        if field == "slot_id" and item not in _WORLD_CHOICES["slot_id"]:
            raise PhoneToolRefused("INVALID_PARAMS", "target.slot_id 不合法。")
        target[field] = item.strip()
    return target


def _world_params(verb: str, value: Any) -> dict[str, Any]:
    """Validate one verb's params here, before any request leaves this process."""

    if not isinstance(value, Mapping):
        raise PhoneToolRefused("INVALID_PARAMS", "世界动作需要一个 params 对象。")
    params = dict(value)
    forbidden = sorted(set(params) & _WORLD_FORBIDDEN)
    if forbidden:
        raise PhoneToolRefused("CLIENT_IDENTITY_REFUSED",
                               "这些字段不能由客户端提供：%s" % ", ".join(forbidden))
    allowed, required = _WORLD_FIELDS[verb]
    unknown = sorted(set(params) - set(allowed))
    if unknown:
        raise PhoneToolRefused("UNKNOWN_FIELD",
                               "这个动作不接受这些参数：%s" % ", ".join(unknown))
    missing = sorted(set(required) - set(params))
    if missing:
        raise PhoneToolRefused("PARAM_REQUIRED", "缺少参数：%s" % ", ".join(missing))
    for field, choices in _WORLD_CHOICES.items():
        if field in params:
            item = params[field]
            if not isinstance(item, str) or item not in choices:
                raise PhoneToolRefused("INVALID_PARAMS", "参数 %s 的取值不合法。" % field)
    states = _WORLD_STATE_CHOICES.get(verb)
    if "state" in params and (states is None or params["state"] not in states):
        raise PhoneToolRefused("INVALID_PARAMS", "参数 state 不属于这个动作。")
    if "mode" in params and params.get("state") != "start":
        raise PhoneToolRefused("INVALID_PARAMS", "mode 只能随 state=start 一起给出。")
    if "target" in params:
        params["target"] = _world_target(
            params["target"], kinds=_ARRANGE_TARGET_KINDS if verb == "ARRANGE" else None)
    for field in ("destination_location_id", "object_id"):
        if field in params:
            item = params[field]
            if not isinstance(item, str) or not item.strip():
                raise PhoneToolRefused("INVALID_PARAMS", "参数 %s 必须是非空字符串。" % field)
            params[field] = item.strip()
    return params


# ---------------------------------------------------------------------------------------------
# reading workbench pre-flight (shape only; C's PhoneReading owns every reading fact)
# ---------------------------------------------------------------------------------------------

#: C's own bounds (``rc1-reading-contract.md``): the whole text, the page, and the private note.
#: C measures annotation text in UTF-8 **bytes** (``PhoneReading._text``), so the tool does too.
MAX_READING_CONTENT_BYTES = 24000
MAX_READING_PAGE = 64
MAX_READING_TITLE_BYTES = 200
MAX_READING_ANNOTATION_BYTES = 8000


def _reading_page(value: Any, *, required: bool) -> int:
    """A page is a strict positive integer: no bool, no float, no numeric string."""

    if value is None and not required:
        return 0
    if isinstance(value, bool) or not isinstance(value, int):
        raise PhoneToolRefused("INVALID_ARGUMENTS", "page 必须是整数页码（1 开始）。")
    if value < 1 or value > MAX_READING_PAGE:
        raise PhoneToolRefused("INVALID_ARGUMENTS", "page 超出 1..%d。" % MAX_READING_PAGE)
    return int(value)


def _reading_content(value: Any) -> str:
    """The operator's own text: 1..24000 UTF-8 bytes.  Nothing is fetched, nothing is split here."""

    if not isinstance(value, str):
        raise PhoneToolRefused("INVALID_ARGUMENTS", "reading_import 需要 content 正文。")
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip() or "\x00" in text:
        raise PhoneToolRefused("INVALID_ARGUMENTS", "content 不能是空白，也不能含 NUL。")
    size = len(text.encode("utf-8"))
    if size < 1 or size > MAX_READING_CONTENT_BYTES:
        raise PhoneToolRefused("INVALID_ARGUMENTS",
                               "content 必须 1..%d UTF-8 字节。" % MAX_READING_CONTENT_BYTES)
    return text


def _reading_text(value: Any, maximum_bytes: int, reason: str) -> str:
    """One annotation text: non-blank, no NUL, within C's own UTF-8 byte bound."""

    if not isinstance(value, str):
        raise PhoneToolRefused("INVALID_ARGUMENTS", reason)
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip() or "\x00" in text:
        raise PhoneToolRefused("INVALID_ARGUMENTS", reason)
    if len(text.encode("utf-8")) > int(maximum_bytes):
        raise PhoneToolRefused("INVALID_ARGUMENTS", reason)
    return text


def _reading_annotation(args: Mapping[str, Any]) -> dict[str, Any]:
    """Pin one annotation kind exactly: ``note`` has title+body, ``unclear`` has a note."""

    kind = args.get("kind")
    if not isinstance(kind, str) or kind not in ("note", "unclear"):
        raise PhoneToolRefused("INVALID_ARGUMENTS", "kind 只能是 note 或 unclear。")
    if kind == "note":
        if args.get("note") is not None:
            raise PhoneToolRefused("UNKNOWN_FIELD", "kind=note 不接受 note 字段。")
        return {
            "title": _reading_text(args.get("title"), MAX_READING_TITLE_BYTES,
                                   "kind=note 需要 title。"),
            "body": _reading_text(args.get("body"), MAX_READING_ANNOTATION_BYTES,
                                  "kind=note 需要 body。"),
        }
    for field in ("title", "body"):
        if args.get(field) is not None:
            raise PhoneToolRefused("UNKNOWN_FIELD", "kind=unclear 不接受 %s 字段。" % field)
    return {"note": _reading_text(args.get("note"), MAX_READING_ANNOTATION_BYTES,
                                  "kind=unclear 需要 note。")}


def _reading_message(action: str, status: str, reason_code: str) -> str:
    """What the owner's status means for one reading action -- never an upgrade, never a guess."""

    base = {
        "reading_import": {
            "applied": "owner 已把正文写进这台实例绑定的阅读材料；旧版本保留，旧停点仍标记为需要重看。",
            "already_applied": "同一意图此前已导入，没有再次写入版本。",
            "rejected": "owner 拒绝了这次导入（正文界限或版本冲突），材料没有改变。",
            "unavailable": "Supply/阅读 owner 当前不可用，导入没有确认。",
            "unknown": "结果未知：不要换 key 重发，先用 action=reading_operation 查原 key。",
        },
        "reading_position": {
            "applied": "owner 已真实读取该页并保存停点；这只是位置，不是理解证明。",
            "already_applied": "同一意图此前已保存，没有再次落位置。",
            "rejected": "owner 拒绝了这次停点（版本/head 冲突或该页不存在），位置没有改变。",
            "unavailable": "Supply/阅读 owner 当前不可用，停点没有确认。",
            "unknown": "结果未知：不要换 key 重发，先用 action=reading_operation 查原 key。",
        },
        "reading_annotate": {
            "applied": "owner 已把这条页注记成私人事项；它只属于你，不进正文也不代表理解。",
            "already_applied": "同一意图此前已记下，没有第二条页注。",
            "rejected": ("owner 拒绝了这条页注：这一版这一页还没有被真实取得"
                         "（PAGE_NOT_ACQUIRED 时先保存停点）或 head 已过期。"),
            "unavailable": "Supply/阅读 owner 当前不可用，页注没有确认。",
            "unknown": "结果未知：不要换 key 重发，先用 action=reading_operation 查原 key。",
        },
    }.get(action, {})
    if reason_code == "PAGE_NOT_ACQUIRED":
        return "这一版这一页还没有真实取得：先 reading_position 保存停点，再写页注。"
    if reason_code in ("READING_REVISION_CONFLICT", "READING_HEAD_CONFLICT"):
        return "版本/head 已变：重新读当前材料页再提交，不要换 key 重发。"
    return base.get(status, "结果未知：不要换 key 重发，先按 operation_key 对账。")


# ---------------------------------------------------------------------------------------------
# 0.2 / 0.3 life & embodied surface pre-flight (whitelist + anti-spoofing + anti-impersonation)
# ---------------------------------------------------------------------------------------------

_LIFE_FORBIDDEN_IDENTITY_KEYS = frozenset({
    "uid", "euid", "gid", "authority", "authorities",
    "main_self_verified", "verified_main_self", "attested_by",
    "home", "hermes_home", "world_home", "profile", "profile_home",
    "binding", "csrf_token", "capability", "capability_token",
    "subject", "subject_view", "is_deployer", "bypass_gate",
    "force_consent", "override_stop", "allow_feigned_orgasm",
    "subject_id", "subject_ref", "actor_id", "actor_ref",
    "deploy_enabled", "adult_confirmed",
})

_PARTNER_ROLE_FIELDS = frozenset({
    "author_side", "actor_side", "proposer", "responder",
    "speaker", "by", "actor", "consumer", "initiated_by",
})

_PARTNER_ROLE_VALUES = frozenset({
    "partner", "user", "deployer", "operator", "host",
})

_PARTNER_ONLY_SUBACTIONS: dict[str, frozenset[str]] = {
    "home_trio": frozenset({"set_partner_picks"}),
    "pocket_lists": frozenset({"set_partner_suggestions"}),
    "outing_bench_bookstore": frozenset({"set_partner_window_note", "clear_partner_window_note"}),
    "outing_routes_market": frozenset({"set_partner_market_tip", "clear_partner_market_tip"}),
    "body_sensory": frozenset({"leave_sensory_note", "remove_sensory_note", "clear_sensory_notes"}),
    "character_pack": frozenset({"select_preset", "apply_custom_pack", "import_pack_json", "attest_character_pack"}),
    "autonomy_rhythm": frozenset({"urgent_knock", "update_preferences"}),
    "emotional_tension": frozenset({
        "configure_instance_seed", "set_interaction_context",
        "attempt_apology_or_gift_repair", "partner_arrive",
        "partner_ask_whats_new", "comfort_low_mood", "respond_favor",
    }),
    "stance_containment": frozenset({"partner_move_container", "partner_bring_snack"}),
    "adult_intimacy": frozenset({
        "configure_adult_gate", "confirm_preference_seed",
        "update_partner_limits", "configure_body_profile",
        "register_render_route", "evaluate_solo_window",
    }),
    "v03_clock": frozenset({"configure_deployer_gate", "set_partner_consent"}),
    "v03_ecology": frozenset({
        "configure_deployer_gate", "set_partner_consent", "partner_respond_consent",
        "offer_stipend", "offer_sick_care", "ack_outbound_read",
    }),
    "v03_embodied_social": frozenset({
        "configure_deployer_gate", "set_partner_consent", "partner_respond_consent",
        "set_partner_presence",
    }),
    "v04_neighborhood": frozenset({"move_partner_to_spot", "invite_walk_together"}),
    "v04_elastic_play": frozenset({
        "respond_prepared_surprise",
        "respond_to_desire",
        "pack_moving_carton",
        "import_parallel_postcard",
        "signal_crumpled_draft",
    }),
    "v02_agency_depth": frozenset({
        "withdraw_partner_option",
        "propose_commitment_tradeoff",
        "suggest_ecology_spark",
        "acknowledge_absence_observation",
        "propose_rescaled_copractice",
    }),
    "v02_runtime_migration": frozenset({
        "log_partner_taste_observation",
        "request_skill_migration_recheck",
        "propose_meaning_note",
        "prepare_dignified_retirement",
        "confirm_dignified_retirement",
        "cancel_dignified_retirement",
        "configure_proactive_outbound",
        "acknowledge_or_cancel_proactive_message",
        "propose_rescaled_copractice",
    }),
    "v02_honest_ux": frozenset({"propose_rescaled_copractice"}),
    "v02_recall_projection": frozenset({"tombstone_memory"}),
    "v02_common_ground": frozenset({"record_partner_unseen_move", "resolve_unseen_move_with_partner"}),
    # v0.8 屋里动态贴纸与真物件：留痕/撤痕/伴侣带物来放/挪物/提用法
    # 都是伴侣自己的动作，模型侧严禁代签。
    "v08_living_traces": frozenset({
        "leave_partner_trace", "clear_partner_trace",
        "partner_bring_item", "partner_move_shared_object", "partner_propose_use",
    }),
    "living_traces": frozenset({
        "leave_partner_trace", "clear_partner_trace",
        "partner_bring_item", "partner_move_shared_object", "partner_propose_use",
    }),
    # v0.8 各自看法里的伴侣侧一条是伴侣自己的话，模型侧严禁代签或冒充；
    # 默契说法与留给未来的问题、旧事新解都可由主体模型自己记，故不在此列。
    "v08_emergent_echoes": frozenset({"partner_add_divergent_notice"}),
    # v3.0 递材料与撤回是伴侣自己的动作；模型侧严禁代签或冒充。
    "v03_encounters": frozenset({"drop_shared_material", "withdraw_shared_material"}),
    # v0.8 递来普通照料是伴侣自己的动作；模型侧严禁代签或冒充。
    "v08_composable_body": frozenset({"offer_ordinary_care"}),
}

_STANCE_OPKEY_SUBACTIONS = frozenset({
    "propose_stance", "fix_stance_typo", "revise_stance",
    "mark_source_updated", "revoke_stance_source",
    "register_container", "nest_container", "set_container_open_state",
    "pick_up_item", "take_from_container", "put_in_container",
    "place_on_surface", "pick_and_put_in_container",
    "subject_move_container", "prepare_table_dish", "take_portion",
})

_ADULT_OPKEY_SUBACTIONS = frozenset({
    "propose_scene", "respond_scene", "advance_beat",
})

_AUTONOMY_CAPABILITY_SUBACTIONS = frozenset({
    "declare_refusal", "release_refusal", "hold_subject_topic",
    "return_to_thread", "trigger_spontaneous_pivot", "set_phone_aside",
})

_EMOTIONAL_CAPABILITY_SUBACTIONS = frozenset({
    "complete_sleep", "upsert_schedule_obligation", "decide_obligation",
    "respond_offer", "complete_deadline", "note_friction", "dissolve_friction",
    "open_rupture", "let_go_rupture", "declare_need_space",
    "open_disagreement", "settle_disagreement", "add_missing_note",
    "share_missing_note", "drop_missing_note", "acknowledge_low_mood_once",
    "lift_low_mood_by_self", "ask_partner_favor", "withdraw_favor",
})

_STANCE_CAPABILITY_SUBACTIONS = frozenset({
    "propose_stance", "fix_stance_typo", "revise_stance", "set_stance_sharing",
    "pick_up_item", "take_from_container", "put_in_container", "place_on_surface",
    "pick_and_put_in_container", "subject_move_container", "set_subject_location",
    "observe_current_room", "set_item_impression_note", "set_primary_activity",
    "prepare_table_dish", "propose_copresence", "respond_copresence",
    "switch_copresence_mode", "exit_copresence", "take_portion", "clear_table_dish",
})

_ADULT_CAPABILITY_SUBACTIONS = frozenset({
    "revise_subject_preference", "propose_scene", "respond_scene",
    "request_widen", "answer_widen", "pause_scene", "confirm_resume",
    "narrow_scene", "stop_scene", "discuss_intimacy_outside_scene",
    "record_private_memory", "erase_private_memory", "revoke_intimacy_consent",
    "update_body_context",
})

#: v0.8 可组合身体：主体自己的私面写动作，全部需要幂等键与本地主体凭据。
_V08_COMPOSABLE_BODY_OPKEY_SUBACTIONS = frozenset({
    "configure_body_pack", "use_cycle_supply", "bind_sleep_activity",
    "settle_sleep_wake", "resolve_garment_care", "share_body_care_hint_once",
    "clear_private_body_history",
})

_V08_COMPOSABLE_BODY_CAPABILITY_SUBACTIONS = frozenset({
    "configure_body_pack", "use_cycle_supply", "bind_sleep_activity",
    "settle_sleep_wake", "resolve_garment_care", "share_body_care_hint_once",
    "clear_private_body_history",
})

#: v0.8 心里回响的余绪写面：记下/解掉一缕余绪都是主体自己的动作，需要幂等键与本地主体凭据。
#: 自传性技法谱系（记下一段手作/写作步骤、决定是否把可分享的小贴士给伴侣看）同样是主体
#: 自己的动作：重试要落在同一条，故与余绪一样需要显式幂等键和本地主体凭据。
_V08_EMERGENT_ECHOES_OPKEY_SUBACTIONS = frozenset({
    "note_lingering_thread", "resolve_lingering_thread",
    "record_craft_genealogy_step", "toggle_share_craft_tip",
})

_V08_EMERGENT_ECHOES_CAPABILITY_SUBACTIONS = frozenset({
    "note_lingering_thread", "resolve_lingering_thread",
    "record_craft_genealogy_step", "toggle_share_craft_tip",
})

#: v0.8 屋里活物：组合动作和「借期顺延」都是主体自己的动作，重试必须落在同一条
#: 操作键上，且需要本地主体凭据。即使在 ``v08_living_traces`` 的整面白名单之外单独
#: 加动作，也在这里显式并排列出，避免新动作漏拿幂等键或凭据门。
_V08_LIVING_TRACES_OPKEY_SUBACTIONS = frozenset({
    "compose_open_act", "request_loan_extension",
})

_V08_LIVING_TRACES_CAPABILITY_SUBACTIONS = frozenset({
    "compose_open_act", "request_loan_extension",
})


def _scan_life_payload_security(
    node: Any, *, surface: str, sub_action: str, top_level: bool = False
) -> None:
    """Reject client identity/authority injection and partner/deployer role impersonation."""

    if isinstance(node, Mapping):
        for raw_k, val in node.items():
            k = str(raw_k)
            if k in _LIFE_FORBIDDEN_IDENTITY_KEYS:
                raise PhoneToolRefused("CLIENT_IDENTITY_REFUSED",
                                       "payload 严禁携带身份或越权字段：%s" % k)
            if k == "owner":
                if not (
                    top_level
                    and surface == "autonomy_rhythm"
                    and sub_action in ("hold_subject_topic", "record_turn_context")
                ):
                    raise PhoneToolRefused("CLIENT_IDENTITY_REFUSED",
                                           "payload 严禁携带身份字段：owner")
            if k in ("partner_picks", "suggestions"):
                raise PhoneToolRefused("PARTNER_IMPERSONATION_REFUSED",
                                       "模型侧严禁写入伴侣专属卡片或建议字段：%s" % k)
            if k in _PARTNER_ROLE_FIELDS and isinstance(val, str):
                if val.strip().lower() in _PARTNER_ROLE_VALUES:
                    raise PhoneToolRefused("PARTNER_IMPERSONATION_REFUSED",
                                           "模型侧严禁冒充伴侣或部署者身份（%s=%s）。" % (k, val.strip()))
            _scan_life_payload_security(val, surface=surface, sub_action=sub_action, top_level=False)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _scan_life_payload_security(item, surface=surface, sub_action=sub_action, top_level=False)


def _strip_solo_fields(doc: Mapping[str, Any]) -> dict[str, Any]:
    """Ensure 36B non-memory solo slots never surface through tool summaries."""

    cleaned: dict[str, Any] = {}
    for k, v in doc.items():
        if str(k) in ("solo", "solo_slot", "solo_ledger", "solo_and_memory"):
            if str(k) == "solo_and_memory" and isinstance(v, Mapping):
                inner = {ik: iv for ik, iv in v.items()
                         if str(ik) not in ("solo", "solo_slot", "solo_ledger", "evaluated_slots", "entries")}
                cleaned[k] = inner
            continue
        if isinstance(v, Mapping):
            cleaned[k] = _strip_solo_fields(v)
        else:
            cleaned[k] = v
    return cleaned


_SURFACE_SUPPORTED_HINTS: dict[str, dict[str, Any]] = {
    "desk_tomorrow": {
        "subject_actions": ["propose", "decide", "clear"],
        "core_fields": {"propose": ["title", "due_in_minutes"], "decide": ["decision"], "clear": []},
    },
    "home_keepsakes": {
        "subject_actions": ["set_lockscreen", "clear_lockscreen", "pin_keepsake", "unpin_keepsake"],
        "core_fields": {
            "set_lockscreen": ["artifact_id", "note"],
            "clear_lockscreen": [],
            "pin_keepsake": ["artifact_id", "note"],
            "unpin_keepsake": ["keepsake_id"],
        },
    },
    "home_workbench": {
        "subject_actions": ["set_tray", "clear_tray"],
        "core_fields": {"set_tray": ["work_artifact_id", "note"], "clear_tray": []},
    },
    "home_trio": {
        "subject_actions": ["set_her_picks"],
        "core_fields": {"set_her_picks": ["her_picks"]},
    },
    "home_reunion_anchor": {
        "subject_actions": ["capture", "clear"],
        "core_fields": {"capture": [], "clear": []},
    },
    "home_recipes": {
        "subject_actions": ["upsert", "remove"],
        "core_fields": {"upsert": ["title", "memo"], "remove": ["recipe_id"]},
    },
    "home_tentative_plans": {
        "subject_actions": ["propose", "settle", "drop"],
        "core_fields": {
            "propose": ["title", "window_note"],
            "settle": ["plan_id", "outcome"],
            "drop": ["plan_id"],
        },
    },
    "wardrobe": {
        "subject_actions": ["set", "clear"],
        "core_fields": {
            "set": ["homewear_style", "pairing_note", "blanket_season"],
            "clear": [],
        },
    },
    "pocket_lists": {
        "subject_actions": ["add", "update", "remove"],
        "core_fields": {
            "add": ["list(playlist|favorites|want_to_try)", "title", "reason|expectation|verdict|note", "status", "shared"],
            "update": ["list", "entry_id", "title", "status", "shared"],
            "remove": ["list", "entry_id"],
        },
    },
    "outing_bench_bookstore": {
        "subject_actions": ["log_bench_visit", "bookmark_window_book", "remove_bookmark"],
        "core_fields": {
            "log_bench_visit": ["place_id", "note", "shared"],
            "bookmark_window_book": ["slot_index", "note", "shared"],
            "remove_bookmark": ["bookmark_id"],
        },
    },
    "outing_routes_market": {
        "subject_actions": ["save_walk_route", "remove_walk_route", "log_market_browse"],
        "core_fields": {
            "save_walk_route": ["title", "place_ids", "shared"],
            "remove_walk_route": ["route_id"],
            "log_market_browse": ["stall_title", "impression_note", "shared"],
        },
    },
    "outing_gallery_blueprint": {
        "subject_actions": ["upsert_blueprint", "archive_blueprint", "curate_gallery", "remove_gallery_item"],
        "core_fields": {
            "upsert_blueprint": ["title", "sketch_note", "intended_use"],
            "archive_blueprint": ["blueprint_id"],
            "curate_gallery": ["artifact_id", "caption"],
            "remove_gallery_item": ["item_id"],
        },
    },
    "longterm_habits": {
        "subject_actions": ["upsert_habit", "retire_habit"],
        "core_fields": {"upsert_habit": ["title", "habit_text"], "retire_habit": ["habit_id"]},
    },
    "longterm_gifts_lantern": {
        "subject_actions": ["upsert_gift", "send_gift", "light_lantern", "dim_lantern"],
        "core_fields": {
            "upsert_gift": ["stage(draft|ready)", "title", "draft_seed_note"],
            "send_gift": ["gift_id", "message"],
            "light_lantern": ["title", "memory_note"],
            "dim_lantern": ["lantern_id"],
        },
    },
    "body_garment_window": {
        "subject_actions": ["set_window", "wear_outerwear", "hang_outerwear", "set_local_zone"],
        "core_fields": {
            "set_window": ["state(closed|ajar|open)"],
            "wear_outerwear": ["garment_id", "label", "dryness(dry|damp|wet)"],
            "hang_outerwear": ["garment_id", "dryness", "hang_position"],
            "set_local_zone": ["zone"],
        },
    },
    "body_care": {
        "subject_actions": ["offer", "take", "drink", "use", "stow", "withdraw"],
        "core_fields": {
            "offer": ["slot(drink|cushion)", "kind", "position(hand_side|desk_side)"],
            "take": ["slot(drink)"],
            "drink": ["slot(drink)"],
            "use": ["slot(cushion)"],
            "stow": ["slot(drink|cushion)"],
            "withdraw": ["slot(drink|cushion)"],
        },
    },
    "body_sensory": {
        "subject_actions": ["capture_sound_card", "remove_sound_card"],
        "core_fields": {
            "capture_sound_card": ["title", "impression_note", "shared"],
            "remove_sound_card": ["card_id"],
        },
    },
    "character_pack": {
        "subject_actions": ["update_identity", "set_optional_physiology"],
        "core_fields": {
            "update_identity": ["display_name", "pronouns"],
            "set_optional_physiology": ["cycle_tracking_enabled"],
        },
    },
    "autonomy_rhythm": {
        "subject_actions": [
            "declare_refusal", "release_refusal",
            "hold_subject_topic", "resolve_held_topic", "record_turn_context",
        ],
        "core_fields": {
            "declare_refusal": ["topic_label", "category", "reason_summary"],
            "release_refusal": ["lock_id"],
            "hold_subject_topic": ["topic_label", "reason"],
            "resolve_held_topic": ["thread_id"],
        },
    },
    "emotional_tension": {
        "subject_actions": [
            "add_missing_note", "share_missing_note", "drop_missing_note",
            "ask_partner_favor", "withdraw_favor", "declare_need_space",
            "note_friction", "dissolve_friction", "open_rupture", "let_go_rupture",
            "open_disagreement", "settle_disagreement", "upsert_schedule_obligation",
            "decide_obligation", "respond_offer", "complete_deadline", "complete_sleep",
            "acknowledge_low_mood_once", "lift_low_mood_by_self",
        ],
        "core_fields": {
            "add_missing_note": ["source_ref", "source_domain", "kind(MADE|FOUND|HAPPENED|LEARNED)", "summary"],
            "ask_partner_favor": ["favor_kind(look_once|help_briefly|sit_awhile|help_pick)", "target_object_ref", "summary"],
            "declare_need_space": ["incident_ref", "space_style(SHORT|UNTIL_SLEEP)", "spoken_aloud"],
        },
    },
    "stance_containment": {
        "subject_actions": [
            "propose_stance", "revise_stance", "fix_stance_typo", "set_stance_sharing",
            "register_container", "set_container_open_state", "pick_up_item",
            "put_in_container", "take_from_container", "pick_and_put_in_container",
            "place_on_surface", "observe_current_room", "prepare_table_dish",
            "take_portion", "clear_table_dish", "set_primary_activity",
            "propose_copresence", "respond_copresence", "switch_copresence_mode", "exit_copresence",
        ],
        "core_fields": {
            "propose_stance": [
                "scope_ref", "page_or_anchor", "facet(appearance|content|method_fit)",
                "text", "source_revision", "shared_with_partner",
            ],
            "revise_stance": ["replaces_stance_id", "text", "shared_with_partner"],
            "prepare_table_dish": ["title", "kind", "portions", "place_id", "initial_temperature"],
            "propose_copresence": ["target_mode", "declared_minutes", "note"],
        },
    },
    "adult_intimacy": {
        "subject_actions": [
            "update_body_context", "revise_subject_preference", "propose_scene",
            "respond_scene", "pause_scene", "confirm_resume", "narrow_scene",
            "stop_scene", "discuss_intimacy_outside_scene", "record_private_memory",
            "erase_private_memory", "revoke_intimacy_consent",
        ],
        "core_fields": {
            "update_body_context": ["fatigue", "stress", "discomfort", "mood(calm|warm|tired|distracted|low)", "awake"],
            "revise_subject_preference": ["stances", "soft_preferences", "evidence_ref"],
            "stop_scene": ["permit_id", "reason"],
            "discuss_intimacy_outside_scene": ["topic(OWN_PREFERENCE|WISH|GENERAL_KNOWLEDGE)", "utterance"],
        },
    },
    "v03_clock": {
        "subject_actions": [
            "record_evaporable", "start_meaningless_idle", "end_meaningless_idle",
            "advance_projection", "declare_slow_variable", "sync_v02_slices",
        ],
        "core_fields": {
            "record_evaporable": [
                "domain_kind(ambient_glance|fleeting_thought|idle_mood_ripple|passing_sensation|ordinary_linger)",
                "text", "ttl_seconds",
            ],
            "start_meaningless_idle": [
                "idle_kind(daydream_window|aimless_page_flip|halfway_abandon_tidy|listen_rain_blankly)",
                "quiet_note",
            ],
            "end_meaningless_idle": ["abandoned_halfway", "let_evaporate"],
        },
    },
    "v03_ecology": {
        "subject_actions": [
            "complete_independent_work", "set_savings_goal", "deliberate_purchase",
            "decide_stipend", "record_strain_or_rest", "advance_illness_recovery",
            "send_contextual_reply", "attempt_proactive_outbound", "withdraw_expired_outbound",
        ],
        "core_fields": {
            "complete_independent_work": [
                "job_kind(illustration_commission|manuscript_fee|proofreading_shift|bookstore_sorting)",
                "title", "hours_spent", "amount_cents", "work_ref",
            ],
            "set_savings_goal": ["item_title", "target_cents"],
            "record_strain_or_rest": ["event_kind", "duration_hours"],
        },
    },
    "v03_embodied_social": {
        "subject_actions": [
            "restock_pantry", "cook_or_brew", "reheat_dish", "consume_dish", "discard_dish",
            "set_hand_slot", "set_subject_contact_posture", "respond_contact", "release_contact",
            "record_npc_knowledge", "revoke_npc_shared_ref", "record_style_migration",
        ],
        "core_fields": {
            "restock_pantry": ["ingredient_id", "units_added", "label", "category", "unit_name"],
            "cook_or_brew": ["title", "serving_kind(drink|meal)", "ingredients_used", "portions"],
            "record_npc_knowledge": ["npc_id", "epistemic_basis", "summary"],
        },
    },
    "v04_neighborhood": {
        "subject_actions": [
            "water_or_move_plant", "hang_or_collect_cloth", "set_local_weather",
            "post_public_notice", "read_public_notice", "set_roadblock", "shared_shelf_loan",
            "start_journey", "advance_or_turnback_journey", "craft_paper_or_box",
            "bookstore_service", "update_place_booklet",
            "set_subject_town_state", "advance_town_pulse",
            "visit_third_space", "log_neighbor_favor", "claim_surplus_bag", "greet_or_feed_kitten",
            "leave_drifting_book", "borrow_or_annotate_drifting_book", "post_bulletin_followup",
        ],
        "core_fields": {
            "water_or_move_plant": ["operation(water|move)", "plant_id", "target_place_id", "water_amount"],
            "hang_or_collect_cloth": ["operation(hang|collect)", "cloth_id", "label"],
            "craft_paper_or_box": ["operation", "title", "draft_text"],
            "bookstore_service": ["operation", "book_id"],
            "update_place_booklet": ["place_id", "impression"],
            "set_subject_town_state": ["ta_location_id", "solo_walk_lock", "companion_note"],
            "visit_third_space": ["spot_id", "note_cn"],
            "log_neighbor_favor": ["regular_id", "favor_kind", "note_cn"],
            "greet_or_feed_kitten": ["interaction_kind", "nickname", "note_cn"],
            "leave_drifting_book": ["book_title", "slip_note_cn", "spot_id"],
            "borrow_or_annotate_drifting_book": ["book_id", "op(borrow|return_with_note)", "margin_note_cn"],
            "post_bulletin_followup": ["notice_id", "title_cn", "followup_cn"],
        },
    },
    "v04_builder_trylive": {
        "subject_actions": [
            "audit_routes_or_rehearse", "check_scene_prose_gaps",
            "run_trylive_hour", "inspect_why_changed", "preview_world_modification",
        ],
        "core_fields": {
            "audit_routes_or_rehearse": ["start_place_id", "destination_place_id", "rehearse_route_id"],
            "check_scene_prose_gaps": ["prose", "place_id"],
            "run_trylive_hour": ["preset_id", "rain", "roadblock"],
            "inspect_why_changed": ["target_kind", "target_id"],
            "preview_world_modification": ["mod_kind", "target_id"],
        },
    },
    "v04_elastic_play": {
        "subject_actions": [
            "set_day_direction", "append_shared_notebook", "share_reading_notice",
            "propose_or_respond_experiment", "save_two_phrasings", "teach_scoped_preference",
            "request_or_answer_practice", "play_temporary_game", "prepare_or_offer_surprise",
            "curate_personal_project", "propose_grounded_alternative", "note_admin_world_change",
            "record_subject_desire", "resolve_subject_desire",
            "settle_morning_dream_pulse", "keep_or_share_dream",
            "tuck_page_note", "advance_reading_page",
            "sketch_window_frost", "trigger_object_chemistry",
            "log_object_patina_or_repair", "propose_arrangement_change",
            "resolve_arrangement_compromise", "unpack_carton_item",
            "mint_parallel_postcard",
        ],
        "core_fields": {
            "set_day_direction": ["direction", "mode", "current_focus"],
            "append_shared_notebook": ["text"],
            "play_temporary_game": ["game_op", "game_kind", "turn_text"],
            "prepare_or_offer_surprise": ["surprise_op", "title", "content"],
            "curate_personal_project": [
                "op(start|add_field_note|pause_not_feeling_it|resume|share_page)",
                "project_id", "purpose_cn", "medium_kind", "place_id", "entry_status", "note_cn",
            ],
            "propose_grounded_alternative": [
                "target_suggestion_cn", "alternative_cn", "grounded_basis_cn",
            ],
            "note_admin_world_change": ["change_summary_cn", "subject_opinion_cn"],
            "record_subject_desire": ["layer(micro_craving|mid_itch|long_aspiration)", "text", "shared"],
            "resolve_subject_desire": ["desire_id", "status(fulfilled|faded|conflicted)", "resolution_note"],
            "keep_or_share_dream": ["kept_in_notebook", "shared_with_partner"],
            "tuck_page_note": ["book_title", "target_page", "note_text"],
            "advance_reading_page": ["book_title", "new_page"],
            "sketch_window_frost": ["text", "weather", "temp_c"],
            "trigger_object_chemistry": ["kind", "params"],
            "log_object_patina_or_repair": [
                "object_id", "event_kind(patina|mended|gifted|retired_gently)", "note_cn",
            ],
            "propose_arrangement_change": [
                "object_id", "target_zone(desk|window_sill|bedside|hearth_table|entryway_shelf)", "reason_cn",
            ],
            "resolve_arrangement_compromise": [
                "proposal_id", "decision(accept_as_proposed|compromise_middle|keep_current_gently)",
                "compromise_zone", "note_cn",
            ],
            "unpack_carton_item": [
                "carton_id", "item_id",
                "target_zone(desk|window_sill|bedside|hearth_table|entryway_shelf)", "settle_note_cn",
            ],
            "mint_parallel_postcard": ["greeting_cn", "sender_label_cn", "town_name_cn"],
        },
    },
    "v02_agency_depth": {
        "subject_actions": [
            "record_choice_episode", "record_exposure_satiation", "park_or_resolve_conflict",
            "record_regret_reflection", "manage_second_order_rule", "manage_open_incubation",
            "offer_low_cost_option", "propose_shared_gentle_rule", "share_incubation_clue",
            "open_bounded_inquiry", "record_inquiry_evidence", "conclude_or_stop_inquiry",
            "place_memory_cue", "update_placed_cue_visibility", "curate_info_source",
            "create_future_capsule", "resolve_future_capsule",
            "set_optional_next_gesture", "peek_optional_next_gesture", "act_or_archive_gesture",
            "settle_legacy_tool", "register_gentle_responsibility", "review_gentle_responsibility",
            "resolve_commitment_value_conflict",
            "snapshot_room_expectations", "record_absence_observation",
            "record_ecology_chain_link", "record_practice_standard_evolution",
            "salvage_work_scrap", "simplify_personal_tool", "record_period_snapshot",
        ],
        "core_fields": {
            "record_choice_episode": ["domain", "chosen", "options", "choice_mode", "shared"],
            "record_exposure_satiation": ["domain", "title", "chosen", "intensity", "shared"],
            "park_or_resolve_conflict": ["conflict_op(park|lean|settle)", "title", "side_a", "side_b", "shared"],
            "record_regret_reflection": ["regret_kind", "summary", "has_observable_alternative", "counterfactual_note"],
            "manage_second_order_rule": ["rule_op(propose|start_trial|endorse|revise|release)", "title", "trigger_window"],
            "manage_open_incubation": ["inc_op(open|add_evidence|check_elapsed|settle)", "topic", "evidence_ref"],
            "open_bounded_inquiry": ["question", "topic_tag", "candidate_explanations", "next_evidence_needed", "step_budget", "shared"],
            "record_inquiry_evidence": ["inquiry_id", "note", "source_ref", "candidate_explanations", "next_evidence_needed"],
            "conclude_or_stop_inquiry": ["inquiry_id", "outcome", "conclusion", "stop_reason"],
            "place_memory_cue": ["cue_id", "artifact_ref", "spot_id", "cue_text_cn", "visible", "shared"],
            "update_placed_cue_visibility": ["cue_id", "spot_id", "visible", "occluded", "removed"],
            "curate_info_source": ["source_id", "title_cn", "mode", "reason_cn", "shared"],
            "create_future_capsule": ["capsule_id", "unlock_after", "title_cn", "note_cn", "artifact_ref", "shared"],
            "resolve_future_capsule": ["capsule_id", "decision"],
            "set_optional_next_gesture": ["gesture_id", "artifact_ref", "gesture_cn", "shared"],
            "peek_optional_next_gesture": ["gesture_id"],
            "act_or_archive_gesture": ["gesture_id", "decision", "new_question_cn"],
            "settle_legacy_tool": ["tool_id", "title_cn", "ending", "fallback_artifact_ref", "shared"],
            "register_gentle_responsibility": ["resp_id", "title_cn", "target_kind", "last_touched_at", "shared"],
            "review_gentle_responsibility": ["resp_id", "decision", "partner_agreed"],
            "resolve_commitment_value_conflict": [
                "resp_id", "new_value_cn", "conflict_note_cn",
                "tradeoff_decision(keep_and_adapt|rescale_with_partner|hand_off_and_retire)",
                "partner_agreed", "shared",
            ],
            "snapshot_room_expectations": ["items", "replace", "shared"],
            "record_absence_observation": [
                "observation_id", "item_id", "title_cn",
                "kind(MISSING_OBJECT|MOVED_OBJECT|SHOP_CLOSED_UNEXPECTED)",
                "expected_place_cn", "observed_place_cn", "summary_cn", "shared",
            ],
            "record_ecology_chain_link": [
                "chain_id", "theme_cn",
                "step_kind(reading|inquiry|craft|palette|outing|share|gift)",
                "ref_id", "title_cn", "note_cn", "shared",
            ],
            "record_practice_standard_evolution": [
                "evolution_id", "domain", "before_criterion_cn", "after_criterion_cn",
                "practice_evidence_count", "evidence_refs", "distinction_note_cn", "shared",
            ],
            "salvage_work_scrap": [
                "scrap_id", "title_cn", "note_cn", "source_work_id", "source_version_id",
                "source_outcome", "fragment_title", "reusable_payload", "applicability_tags",
                "provenance_receipt", "shared",
            ],
            "simplify_personal_tool": [
                "tool_id", "script_id", "title_cn", "note_cn", "shortcut_keys",
                "removed_option_ids", "reason", "shared",
            ],
            "record_period_snapshot": ["period_id", "title_cn", "summary_cn", "note", "visibility", "shared"],
        },
    },
    "v02_crossmodal": {
        "subject_actions": [
            "analyze_image_palette", "try_color_swatches", "clear_color_trial_pad",
            "mark_reading_uncertain", "apply_palette_to_work",
            "check_collection_staleness", "invalidate_deleted_media_source",
            "curate_unshown_box_item", "withdraw_box_show_intent", "invalidate_deleted_box_source",
            "ingest_audio_note",
        ],
        "core_fields": {
            "analyze_image_palette": ["title", "album_id|image_base64", "share_with_room", "work_id"],
            "try_color_swatches": ["swatches", "note_cn"],
            "mark_reading_uncertain": ["book_title", "page", "passage_hint", "state", "share_with_room"],
            "apply_palette_to_work": ["study_id", "work_id", "selected_hex", "usage_note_cn"],
            "check_collection_staleness": ["title", "source_kind", "source_ref", "status"],
            "curate_unshown_box_item": ["box_item_id", "artifact_id", "version_id", "title_cn", "curation_state", "reuse_mode", "note_cn"],
            "withdraw_box_show_intent": ["box_item_id"],
            "invalidate_deleted_box_source": ["artifact_id"],
            "ingest_audio_note": [
                "title_cn", "duration_seconds",
                "audio_format(wav|ogg|mp3|m4a|webm|voice_memo)",
                "source_kind(mic_capture|local_file|ambient_memo)",
                "transcript_cn", "ambient_tag_cn", "shared",
            ],
        },
    },
    "v02_runtime_migration": {
        "subject_actions": [
            "set_budget_mode", "record_carrier_check", "pack_selective_suitcase",
            "coalesce_probe", "reverify_migrated_skill", "record_workspace_audit",
            "reconcile_backup_restore", "claim_device_presence_lease", "handoff_device_presence_lease",
            "explicit_import_representation", "approve_pending_forget_hold",
            "register_skill_release", "record_skill_execution_outcome",
            "record_targeted_method_patch", "retire_skill_release",
            "revoke_skill_source_material", "register_standard_revision",
            "capture_personality_drift_snapshot",
            "record_body_history_entry", "record_meaning_version",
            "record_lineage_branch", "record_layered_news_item",
            "register_external_fact_source", "sync_carrier_profile_and_adapt_habits",
            "enqueue_proactive_message", "record_self_claim",
            "observe_external_edit", "rebuild_projection_from_canonical",
            "confirm_rescaled_copractice",
        ],
        "core_fields": {
            "set_budget_mode": ["mode(NORMAL|LOW_BUDGET|DORMANT)", "budget_scale_pct", "reason_cn"],
            "record_carrier_check": ["model_id", "carrier_name", "preview_available"],
            "pack_selective_suitcase": ["profile_mode", "keep_skill_ids", "leave_behind_note"],
            "reverify_migrated_skill": ["skill_id", "passed", "note_cn"],
            "record_workspace_audit": ["wake_reason", "visible_candidate_count", "entered_workspace_count"],
            "reconcile_backup_restore": ["external_watermark_seal"],
            "claim_device_presence_lease": ["device_id", "continuation_ref", "lease_seconds", "expected_epoch"],
            "handoff_device_presence_lease": ["from_device_id", "to_device_id", "lease_epoch", "continuation_ref"],
            "explicit_import_representation": [
                "artifact_id", "observed_head_hash", "note_cn", "base_projection_version",
            ],
            "approve_pending_forget_hold": [
                "case_id", "scope_choice", "window_minutes", "resolution", "unreachable_copies",
            ],
            "register_skill_release": [
                "method_id", "tool_version", "model_id", "scope",
                "attribution(MODEL_NATIVE|BORROWED_TOOL|RETRIEVED_GUIDE|PRACTICED_MASTERY)",
                "criteria_id", "standard_revision", "domain", "title_cn", "status",
                "held_out", "ablation_gain_vs_bare_model", "source_dependencies",
            ],
            "record_skill_execution_outcome": [
                "method_id", "tool_version", "model_id", "scope",
                "outcome(success|failure)", "error_kind", "is_transient_network",
                "safety_violation", "bug_signature",
            ],
            "record_targeted_method_patch": [
                "patch_id", "method_id", "scope", "failure_symptom_cn",
                "root_cause_hypothesis", "method_change_cn", "retest_status", "shared",
            ],
            "retire_skill_release": [
                "scope_key", "method_id", "tool_version", "model_id", "scope",
                "retired_reason", "retirement_note_cn",
            ],
            "revoke_skill_source_material": ["source_ref"],
            "register_standard_revision": [
                "domain", "criteria_id", "standard_revision", "rubric_summary",
                "new_stance_cn", "previous_stance_cn", "axis", "evidence_ref", "shared",
            ],
            "capture_personality_drift_snapshot": ["note_cn"],
            "record_body_history_entry": [
                "period_label", "comfort_summary_cn", "illness_or_strain_tag",
                "rest_adjustment_cn", "shared",
            ],
            "record_meaning_version": [
                "item_ref(请传item_ref不要传subject_ref)",
                "subject_title_cn", "earlier_meaning_cn", "current_meaning_cn", "shared",
            ],
            "record_lineage_branch": [
                "origin_instance_id", "branch_label_cn", "reason_cn",
                "parent_branch_id", "shared",
            ],
            "record_layered_news_item": [
                "layer(LOCAL_WORLD_NEWS|EXTERNAL_REAL_FACT|UNVERIFIED_HEARSAY)",
                "headline_cn", "summary_cn", "source_id", "source_ref",
                "trust_tier", "topic_tag", "location_id", "shared",
            ],
            "register_external_fact_source": [
                "title_cn", "source_kind", "source_ref", "trust_tier", "note_cn", "shared",
            ],
            "sync_carrier_profile_and_adapt_habits": [
                "carrier_kind", "carrier_label_cn", "visual_preview",
                "audio_output", "desk_stage", "camera_input", "note_cn",
            ],
            "enqueue_proactive_message": ["message_cn", "reason_cn", "channel_hint"],
            "record_self_claim": [
                "period_id", "claim_key", "domain", "statement",
                "lane(durable_claim|temporary_choice)", "polarity(affirm|negate|nuanced)",
                "stance", "visibility(private|shared)", "evidence_refs",
            ],
            "observe_external_edit": ["artifact_id", "workspace_rel_path"],
            "rebuild_projection_from_canonical": ["artifact_id"],
            "confirm_rescaled_copractice": [
                "practice_id",
                "scale_mode(light_relay|co_presence|async_checkin|pause_without_debt|quiet_checkin)",
                "confirmed",
            ],
        },
        "notes_cn": (
            "调用 record_meaning_version 时请使用 item_ref 字段指代对象引用，"
            "切勿传 subject_ref（subject_ref 属于身份保留字段，会被安全闸门直接拦截）。"
        ),
    },
    "v02_recall_projection": {
        "subject_actions": [
            "record_episodic_trace", "mark_active_fade", "record_rehearsal",
        ],
        "core_fields": {
            "record_episodic_trace": ["memory_id", "topic", "exact_detail", "gist_summary", "emotional_salience"],
            "mark_active_fade": ["memory_id", "fade_strength(soft_let_go|deep_let_go)", "reason"],
            "record_rehearsal": ["memory_id"],
        },
    },
    "v02_common_ground": {
        "subject_actions": [
            "record_ground_item", "advance_communication_receipt", "mark_shared_with_partner",
            "revise_shared_plan", "register_object_belief", "attempt_use_or_fetch_object",
            "search_nearby_spots", "record_dual_rationale_decision", "revise_own_decision_rationale",
            "record_missed_moment", "acknowledge_missed_moment_catchup",
            "submit_async_coauthor_contribution", "resolve_async_contribution",
        ],
        "core_fields": {
            "record_ground_item": ["item_id", "topic", "summary", "ground_status"],
            "register_object_belief": ["object_id", "object_label", "spot", "shared_with_partner"],
            "attempt_use_or_fetch_object": ["object_id"],
            "search_nearby_spots": ["object_id", "candidate_spots"],
            "record_dual_rationale_decision": [
                "decision_id", "topic_cn", "scope_cn", "adopted_choice_cn",
                "subject_rationale_cn", "partner_rationale_cn", "subject_kept_detail_cn",
            ],
            "revise_own_decision_rationale": ["decision_id", "new_rationale_cn"],
            "record_missed_moment": ["moment_id", "summary_cn"],
            "acknowledge_missed_moment_catchup": ["moment_id", "catchup_note_cn"],
            "submit_async_coauthor_contribution": [
                "contrib_id", "work_ref", "author_id", "author_label_cn", "is_simulated", "diff_summary_cn",
            ],
            "resolve_async_contribution": ["contrib_id", "resolution", "note_cn"],
        },
    },
    "v08_living_traces": {
        "subject_actions": [
            "execute_universal_verb", "apply_living_trace", "clear_living_trace",
            "save_personal_routine", "run_personal_routine", "retire_personal_routine",
            "compose_open_act", "request_loan_extension", "respond_proposed_use",
            "preview_placement", "adjust_microzone_comfort",
        ],
        "core_fields": {
            "execute_universal_verb": ["object_id", "verb"],
            "apply_living_trace": ["object_id", "gesture_cn", "state_sticker_cn"],
            "clear_living_trace": ["trace_id"],
            "save_personal_routine": ["title_cn", "steps"],
            "run_personal_routine": ["routine_id"],
            "retire_personal_routine": ["routine_id"],
            "compose_open_act": ["intent_cn", "steps"],
            # 借来的东西到期前想多留几天：只顺延归还时间，不牵连好感或关系分。
            "request_loan_extension": ["object_id", "extra_hours", "note_cn"],
            "respond_proposed_use": ["object_id", "decision"],
            # 只读零写入的摆放预演：先看目标容器/台面还剩几格、物理会影响什么，再决定放不放。
            "preview_placement": ["target_parent_id", "object_id", "label_cn", "item_kind"],
            # 微区舒适：真正刺眼/溅雨/有风时才换个坐处，手头活与断点原样保留，纯可选。
            "adjust_microzone_comfort": [
                "window_state", "lamp_on", "lamp_angle", "target_spot_id",
                "posture_cn", "preserve_work_ref", "preserve_breakpoint_cn",
            ],
        },
        # ``compose_open_act.steps[].op`` 可用的全部原语：原有 7 个通用原语，外加
        # 4 个手作/养护原语。全部零义务——顺手做可以，不做也完全不影响任何状态机。
        "step_ops": [
            "PLACE_OR_MOVE", "INTRODUCE_ITEM", "TRANSFORM_CREATE",
            "CONSUME_PORTION", "CLEAN_SURFACE_OR_VESSEL", "DISCARD_ITEM",
            "OBSERVE_OBJECT",
            "SPLIT_MATERIAL", "ASSEMBLE_OR_DETACH_PART",
            "TRANSCRIBE_TO_PAPER", "PATCH_OR_REPAIR",
            "BORROW_NEIGHBORHOOD_ITEM", "RETURN_BORROWED_ITEM",
            "DELIVER_CARD_TO_PUBLIC_BOARD",
            # Round C1/C2: typed relational micro-acts (carrier posture + pages +
            # live group arrangements + attributed association).
            "MOVE_OBJECT", "PLACE_INSIDE", "ATTACH_NOTE", "TAKE_OBJECT",
            "ARRANGE_OBJECTS", "NOTE_ASSOCIATION",
        ],
        "notes_cn": (
            "compose_open_act 的 steps[].op 除原有 7 个通用原语外，现在也支持 "
            "SPLIT_MATERIAL（拆分材料）、ASSEMBLE_OR_DETACH_PART（拼装或拆下部件）、"
            "TRANSCRIBE_TO_PAPER（把文字抄到纸上）、PATCH_OR_REPAIR（补缀或修理）四个"
            "手作/养护原语，以及 BORROW_NEIGHBORHOOD_ITEM（从街角共享架/旧书店借）、"
            "RETURN_BORROWED_ITEM（把借来的东西还回去）、DELIVER_CARD_TO_PUBLIC_BOARD"
            "（把真实卡片送到公共留言板）三个跨圈动作。另有 MOVE_OBJECT/TAKE_OBJECT"
            "（拿起或挪动物件）、PLACE_INSIDE（放进/夹进带页或可装的载体）、"
            "ATTACH_NOTE（贴附纸条）、ARRANGE_OBJECTS（把两件以上摆成排/簇/叠）、"
            "NOTE_ASSOCIATION（给物件或摆放记一句私有联想）。可选字段 "
            "placement_relation（on/inside/draped_on/tucked_in/leaning_on/attached_to）、"
            "posture_tag（neat/casual_drape/folded/half_showing/upright/tilted）、"
            "anchor_page（tucked_in 时的 1..2000 页位）；preview_placement 仍是只读预演"
            "（先看格位与物理影响，再决定放不放）；这些都是可选的零义务动作，不做也完全没问题。"
        ),
    },
    "v03_encounters": {
        "subject_actions": [
            "record_first_glance", "revisit_with_question", "record_nuanced_recognition",
            "open_or_use_shared_material", "record_npc_errand_or_hearsay",
        ],
        "core_fields": {
            "record_first_glance": ["target_ref", "target_title_cn", "seen_pages", "total_pages", "current_impression_cn"],
            "revisit_with_question": ["encounter_id|target_ref", "newly_seen_pages", "active_question_cn", "noticed_detail_cn"],
            "record_nuanced_recognition": ["encounter_id", "current_impression_cn", "partial_disagreement_kept", "tried_in_work_ref"],
            "open_or_use_shared_material": ["material_id", "mode(open_partial|use_in_work)", "opened_pages", "reaction_cn", "used_in_artifact_id"],
            "record_npc_errand_or_hearsay": ["npc_id", "npc_label_cn", "place_id", "kind(ongoing_errand|hearsay)", "summary_cn", "hearsay_source_cn"],
        },
    },
    "v08_emergent_echoes": {
        "subject_actions": [
            "plant_future_question", "revisit_future_question",
            "record_memory_reinterpretation", "share_memory_reinterpretation",
            "record_cross_domain_spark", "settle_spark_attempt",
            "subject_add_divergent_notice",
            "propose_shared_idiom", "confirm_shared_idiom", "fade_shared_idiom",
            "sketch_day_direction", "advance_or_interrupt_direction",
            "note_lingering_thread", "resolve_lingering_thread",
            "record_craft_genealogy_step", "toggle_share_craft_tip",
        ],
        "core_fields": {
            "plant_future_question": ["question_cn", "keywords", "anchor_ref", "visibility(shared|private)"],
            "revisit_future_question": ["question_id", "decision(keep_open|resolve|retire)", "note_cn"],
            "record_memory_reinterpretation": ["episode_ref", "episode_title_cn", "original_fact_cn", "view_cn", "shared_with_partner"],
            "share_memory_reinterpretation": ["reinterpretation_id"],
            "record_cross_domain_spark": ["spark_cn", "source_ref", "target_ref"],
            "settle_spark_attempt": ["spark_id", "outcome(tried|parked)", "note_cn"],
            "subject_add_divergent_notice": ["target_ref", "target_title_cn", "subject_notice_cn"],
            "propose_shared_idiom": ["phrase", "meaning", "origin"],
            "confirm_shared_idiom": ["idiom_id"],
            "fade_shared_idiom": ["idiom_id", "reason_cn"],
            "sketch_day_direction": ["horizon_note_cn", "next_decision_boundary_cn", "visibility(shared|private)"],
            "advance_or_interrupt_direction": ["direction_id", "transition(mark_started|pause_at_breakpoint|let_fade|mark_completed_with_ref)", "breakpoint_note_cn", "evidence_ref"],
            # 一缕会自然淡去、绝不自动续期的余绪：记下时给到期时间，到期后不再自动注入。
            "note_lingering_thread": ["summary_cn", "scope_kind", "scope_ref", "ttl_minutes"],
            "resolve_lingering_thread": ["thread_id", "resolution"],
            # 自传性技法谱系：哪类问题、当时怎么处理、成没成、以及「什么场合别照搬」的边界。
            # source_work_ref 是可选的私有原稿引用，伴侣视角永不出现；shared_tip_only 决定
            # 这条安全贴士要不要给伴侣看。
            "record_craft_genealogy_step": [
                "problem_pattern_cn", "operation_cn", "domain_tag",
                "outcome(worked|misfired)", "mismatch_boundary_cn",
                "source_work_ref", "shareable_tip_cn", "shared_tip_only",
            ],
            "toggle_share_craft_tip": ["genealogy_id", "shared_tip_only", "shareable_tip_cn"],
        },
    },
    "v08_composable_body": {
        "subject_actions": [
            "configure_body_pack", "use_cycle_supply", "bind_sleep_activity",
            "settle_sleep_wake", "resolve_garment_care",
            "share_body_care_hint_once", "clear_private_body_history",
        ],
        "core_fields": {
            "configure_body_pack": ["pack_id(menstrual_cycle|nocturnal_emission)", "enabled", "anchor_iso", "cycle_length_days", "active_days", "probability_permille"],
            "use_cycle_supply": ["supply_object_id", "note_cn"],
            "bind_sleep_activity": ["sleep_activity_id", "started_at_iso", "planned_wake_iso"],
            "settle_sleep_wake": ["sleep_activity_id", "woke_at_iso"],
            "resolve_garment_care": ["mode(wash_garment|change_garment)", "garment_object_id"],
            "share_body_care_hint_once": ["hint_cn", "ttl_minutes"],
            "clear_private_body_history": ["confirm"],
        },
    },
}


def _surface_supported_hint(surface: str, attempted_action: str = "") -> Optional[dict[str, Any]]:
    meta = _SURFACE_SUPPORTED_HINTS.get(surface)
    if not meta:
        return None
    out: dict[str, Any] = {
        "target_surface": surface,
        "subject_actions": list(meta.get("subject_actions") or []),
        "core_fields": dict(meta.get("core_fields") or {}),
    }
    # Optional, additive guidance: old callers never asked for these keys and keep working.
    if meta.get("step_ops"):
        out["step_ops"] = list(meta["step_ops"])
    if meta.get("notes_cn"):
        out["notes_cn"] = str(meta["notes_cn"])
    if attempted_action:
        out["attempted_action"] = attempted_action
    return out


_CLOCK_DOMAIN_ALIASES: dict[str, str] = {
    "window_glance": "ambient_glance",
    "glance": "ambient_glance",
    "ambient": "ambient_glance",
    "thought": "fleeting_thought",
    "mood": "idle_mood_ripple",
    "mood_ripple": "idle_mood_ripple",
    "sensation": "passing_sensation",
    "linger": "ordinary_linger",
}

_ECOLOGY_JOB_KIND_ALIASES: dict[str, str] = {
    "illustration": "illustration_commission",
    "drawing": "illustration_commission",
    "manuscript": "manuscript_fee",
    "writing": "manuscript_fee",
    "proofreading": "proofreading_shift",
    "editing": "proofreading_shift",
    "bookstore": "bookstore_sorting",
    "sorting": "bookstore_sorting",
}

_NEIGHBORHOOD_ACTION_ALIASES: dict[str, tuple[str, str]] = {
    "water_plant": ("water_or_move_plant", "water"),
    "move_plant": ("water_or_move_plant", "move"),
    "hang_cloth": ("hang_or_collect_cloth", "hang"),
    "collect_cloth": ("hang_or_collect_cloth", "collect"),
    "borrow_tool": ("shared_shelf_loan", "borrow"),
    "return_tool": ("shared_shelf_loan", "return"),
    "advance_journey": ("advance_or_turnback_journey", "advance"),
    "turnback_journey": ("advance_or_turnback_journey", "turn_back"),
}

_SERVING_KIND_ALIASES: dict[str, str] = {
    "tea": "drink",
    "coffee": "drink",
    "beverage": "drink",
    "food": "meal",
    "dish": "meal",
}


def _normalize_life_update_payload(surface: str, raw_body: Mapping[str, Any]) -> dict[str, Any]:
    """Gently align unambiguous key/enum aliases without inventing any domain facts."""
    body = dict(raw_body)
    if not str(body.get("action") or "").strip():
        for alias_key in ("op", "sub_action"):
            val = body.get(alias_key)
            if isinstance(val, str) and val.strip():
                body["action"] = body.pop(alias_key).strip()
                break

    sub = str(body.get("action") or "").strip()
    sub_low = sub.lower()

    if surface == "v04_neighborhood" and sub_low in _NEIGHBORHOOD_ACTION_ALIASES:
        mapped_act, default_op = _NEIGHBORHOOD_ACTION_ALIASES[sub_low]
        body["action"] = mapped_act
        if "operation" not in body and "op" not in body and "op_kind" not in body:
            body["operation"] = default_op
        sub = mapped_act
        sub_low = mapped_act.lower()

    if surface == "v03_clock":
        if sub_low == "record_evaporable":
            if "text" not in body:
                for k_alias in ("summary", "note", "content"):
                    if isinstance(body.get(k_alias), str) and body[k_alias].strip():
                        body["text"] = body.pop(k_alias)
                        break
            if "domain_kind" not in body:
                for k_alias in ("category", "kind"):
                    if isinstance(body.get(k_alias), str) and body[k_alias].strip():
                        body["domain_kind"] = body.pop(k_alias)
                        break
            dk = body.get("domain_kind")
            if isinstance(dk, str) and dk.strip().lower() in _CLOCK_DOMAIN_ALIASES:
                body["domain_kind"] = _CLOCK_DOMAIN_ALIASES[dk.strip().lower()]
        elif sub_low == "start_meaningless_idle":
            if "idle_kind" not in body and isinstance(body.get("kind"), str):
                body["idle_kind"] = body.pop("kind")
            if "quiet_note" not in body and isinstance(body.get("note"), str):
                body["quiet_note"] = body.pop("note")

    elif surface == "v03_ecology":
        if sub_low == "complete_independent_work":
            if "job_kind" not in body and isinstance(body.get("kind"), str):
                body["job_kind"] = body.pop("kind")
            if "hours_spent" not in body and "hours" in body:
                body["hours_spent"] = body.pop("hours")
            if "private_note" not in body and isinstance(body.get("note"), str):
                body["private_note"] = body.pop("note")
            jk = body.get("job_kind")
            if isinstance(jk, str) and jk.strip().lower() in _ECOLOGY_JOB_KIND_ALIASES:
                body["job_kind"] = _ECOLOGY_JOB_KIND_ALIASES[jk.strip().lower()]
        elif sub_low in ("set_savings_goal", "deliberate_purchase"):
            if "item_title" not in body and isinstance(body.get("title"), str):
                body["item_title"] = body.pop("title")
        elif sub_low == "record_strain_or_rest":
            if "event_kind" not in body and isinstance(body.get("kind"), str):
                body["event_kind"] = body.pop("kind")
            if "duration_hours" not in body and "hours" in body:
                body["duration_hours"] = body.pop("hours")

    elif surface == "stance_containment":
        if sub_low in ("propose_stance", "revise_stance"):
            if "page_or_anchor" not in body:
                for k_alias in ("anchor", "page"):
                    if k_alias in body and body[k_alias] is not None:
                        body["page_or_anchor"] = body.pop(k_alias)
                        break
            if "text" not in body:
                for k_alias in ("summary", "stance"):
                    if isinstance(body.get(k_alias), str) and body[k_alias].strip():
                        body["text"] = body.pop(k_alias)
                        break
            if "private_note" not in body and isinstance(body.get("note"), str):
                body["private_note"] = body.pop("note")

    elif surface == "v03_embodied_social":
        if sub_low == "cook_or_brew":
            if "serving_kind" not in body and isinstance(body.get("kind"), str):
                body["serving_kind"] = body.pop("kind")
            sk = body.get("serving_kind")
            if isinstance(sk, str) and sk.strip().lower() in _SERVING_KIND_ALIASES:
                body["serving_kind"] = _SERVING_KIND_ALIASES[sk.strip().lower()]

    elif surface == "v04_elastic_play":
        if sub_low == "play_temporary_game" and "game_op" not in body and isinstance(body.get("operation"), str):
            body["game_op"] = body.pop("operation")
        elif sub_low == "prepare_or_offer_surprise" and "surprise_op" not in body and isinstance(body.get("operation"), str):
            body["surprise_op"] = body.pop("operation")
        elif sub_low == "request_or_answer_practice" and "practice_op" not in body and isinstance(body.get("operation"), str):
            body["practice_op"] = body.pop("operation")
        elif sub_low == "curate_personal_project" and "op" not in body and isinstance(body.get("operation"), str):
            body["op"] = body.pop("operation")

    elif surface == "v02_agency_depth":
        if sub_low == "park_or_resolve_conflict" and "conflict_op" not in body and isinstance(body.get("operation"), str):
            body["conflict_op"] = body.pop("operation")
        elif sub_low == "manage_second_order_rule" and "rule_op" not in body and isinstance(body.get("operation"), str):
            body["rule_op"] = body.pop("operation")
        elif sub_low == "manage_open_incubation" and "inc_op" not in body:
            for k_alias in ("incubation_op", "operation"):
                if isinstance(body.get(k_alias), str):
                    body["inc_op"] = body.pop(k_alias)
                    break

    return body


def _prepare_life_update(args: Mapping[str, Any], key: str) -> tuple[str, str, dict[str, Any], bool]:
    """Validate and pin one ``phone_life_update`` request before sending to ``PhoneHTTPServer``."""

    surface = str(args.get("target_surface") or "").strip()
    if surface not in LIFE_UPDATE_SURFACES:
        raise PhoneToolRefused("INVALID_TARGET_SURFACE",
                               "未知或不允许的 target_surface。")
    raw_payload = args.get("payload")
    if not isinstance(raw_payload, Mapping) or not raw_payload:
        raise PhoneToolRefused(
            "INVALID_PAYLOAD",
            "phone_life_update 需要非空对象 payload。",
            supported_actions_hint=_surface_supported_hint(surface),
        )
    body = _normalize_life_update_payload(surface, raw_payload)
    sub_action = str(body.get("action") or "").strip()
    sub_action_lower = sub_action.lower()
    if not sub_action:
        raise PhoneToolRefused(
            "INVALID_PAYLOAD",
            "payload 需要指明具体的子动作 action。",
            supported_actions_hint=_surface_supported_hint(surface),
        )

    if sub_action in _PARTNER_ONLY_SUBACTIONS.get(surface, frozenset()) or (
        sub_action_lower in _PARTNER_ONLY_SUBACTIONS.get(surface, frozenset())
    ):
        raise PhoneToolRefused("PARTNER_IMPERSONATION_REFUSED",
                               "子动作 %s 仅限伴侣或部署者本人执行，模型侧严禁代签或冒充。" % sub_action)

    _scan_life_payload_security(raw_payload, surface=surface, sub_action=sub_action_lower, top_level=True)
    _scan_life_payload_security(body, surface=surface, sub_action=sub_action_lower, top_level=True)

    # Pin dual-actor role fields to the subject side ("her" / "subject") when omitted
    if surface == "home_recipes" and sub_action_lower == "upsert":
        body.setdefault("author_side", "her")
    elif surface == "home_tentative_plans" and sub_action_lower == "propose":
        body.setdefault("proposer", "her")
    elif surface == "outing_gallery_blueprint" and sub_action_lower in ("upsert_blueprint", "archive_blueprint"):
        body.setdefault("author_side", "her")
    elif surface == "longterm_habits" and sub_action_lower in ("upsert_habit", "retire_habit"):
        body.setdefault("author_side", "her")
    elif surface == "longterm_gifts_lantern" and sub_action_lower in ("upsert_gift", "send_gift"):
        body.setdefault("author_side", "her")
    elif surface == "body_care" and sub_action_lower == "offer":
        body.setdefault("actor_side", "her")
    elif surface == "autonomy_rhythm" and sub_action_lower in ("hold_subject_topic", "record_turn_context"):
        body.setdefault("owner", "subject")
    elif surface == "emotional_tension" and sub_action_lower != "invalidate_source_ref":
        body.setdefault("actor", "subject")
    elif surface == "stance_containment":
        if sub_action_lower in (
            "register_container", "nest_container", "set_container_open_state",
            "propose_copresence", "respond_copresence",
            "switch_copresence_mode", "exit_copresence", "clear_table_dish",
        ):
            body.setdefault("actor", "subject")
        elif sub_action_lower == "take_portion":
            body.setdefault("consumer", "subject")
    elif surface == "adult_intimacy":
        if sub_action_lower == "propose_scene":
            body.setdefault("proposer", "subject")
        elif sub_action_lower == "respond_scene":
            body.setdefault("responder", "subject")
        elif sub_action_lower == "discuss_intimacy_outside_scene":
            body.setdefault("speaker", "subject")
        elif sub_action_lower in (
            "request_widen", "answer_widen", "pause_scene", "confirm_resume",
            "narrow_scene", "stop_scene", "erase_private_memory", "revoke_intimacy_consent",
        ):
            body.setdefault("by", "subject")

    # Inject idempotency key ONLY on surfaces/sub-actions whose contract accepts operation_key
    accepts_opkey = (
        (surface == "pocket_lists" and sub_action_lower == "add")
        or (surface == "outing_bench_bookstore" and sub_action_lower == "log_bench_visit")
        or (surface == "body_sensory" and sub_action_lower == "capture_sound_card")
        or surface == "emotional_tension"
        or (surface == "stance_containment" and sub_action_lower in _STANCE_OPKEY_SUBACTIONS)
        or (surface == "adult_intimacy" and sub_action_lower in _ADULT_OPKEY_SUBACTIONS)
        or (surface == "v08_composable_body"
            and sub_action_lower in _V08_COMPOSABLE_BODY_OPKEY_SUBACTIONS)
        or (surface == "v08_emergent_echoes"
            and sub_action_lower in _V08_EMERGENT_ECHOES_OPKEY_SUBACTIONS)
        or surface in (
            "v03_clock", "v03_ecology", "v03_embodied_social",
            "v04_neighborhood", "v04_builder_trylive", "v04_elastic_play",
            "v02_agency_depth", "v02_crossmodal", "v02_runtime_migration",
            "v02_recall_projection", "v02_common_ground",
            "v08_living_traces", "living_traces", "v08_emergent_echoes",
            "v03_encounters",
        )
        or (surface in ("v08_living_traces", "living_traces")
            and sub_action_lower in _V08_LIVING_TRACES_OPKEY_SUBACTIONS)
    )
    if accepts_opkey:
        outer_raw = str(args.get("operation_key") or "").strip() if "args" in locals() else ""
        inner_raw = str(body.get("operation_key") or "").strip()
        if outer_raw and inner_raw and outer_raw != inner_raw:
            raise PhoneToolRefused(
                "OPERATION_KEY_CONFLICT",
                "外层 operation_key 与 payload.operation_key 不一致。",
            )
        if not inner_raw:
            body["operation_key"] = key

    # Determine whether local subject capability is strictly required before sending
    always_cap_surfaces = {
        "desk_tomorrow", "home_keepsakes", "home_workbench", "home_trio",
        "home_recipes", "home_tentative_plans", "wardrobe", "pocket_lists",
        "outing_bench_bookstore", "outing_routes_market", "outing_gallery_blueprint",
        "longterm_habits", "longterm_gifts_lantern", "body_garment_window",
        "body_care", "body_sensory", "v03_ecology", "v03_embodied_social",
        "v04_neighborhood", "v04_elastic_play",
        "v02_agency_depth", "v02_crossmodal", "v02_runtime_migration",
        "v02_recall_projection", "v02_common_ground",
        "v08_living_traces", "living_traces", "v08_emergent_echoes",
        "v03_encounters",
    }
    cap_required = (
        surface in always_cap_surfaces
        or (surface == "character_pack" and sub_action_lower == "set_optional_physiology")
        or (surface == "autonomy_rhythm" and sub_action_lower in _AUTONOMY_CAPABILITY_SUBACTIONS)
        or (surface == "emotional_tension" and sub_action_lower in _EMOTIONAL_CAPABILITY_SUBACTIONS)
        or (surface == "stance_containment" and sub_action_lower in _STANCE_CAPABILITY_SUBACTIONS)
        or (surface == "adult_intimacy" and sub_action_lower in _ADULT_CAPABILITY_SUBACTIONS)
        or (surface == "v08_composable_body"
            and sub_action_lower in _V08_COMPOSABLE_BODY_CAPABILITY_SUBACTIONS)
        or (surface == "v08_emergent_echoes"
            and sub_action_lower in _V08_EMERGENT_ECHOES_CAPABILITY_SUBACTIONS)
        or (surface in ("v08_living_traces", "living_traces")
            and sub_action_lower in _V08_LIVING_TRACES_CAPABILITY_SUBACTIONS)
        or (surface == "v03_clock" and sub_action_lower in ("record_evaporable", "start_meaningless_idle", "end_meaningless_idle"))
    )
    target_path = LIFE_UPDATE_SURFACES[surface]
    if surface == "v04_neighborhood" and sub_action_lower in {
        "set_subject_town_state", "advance_town_pulse",
        "visit_third_space", "log_neighbor_favor", "claim_surplus_bag", "greet_or_feed_kitten",
        "leave_drifting_book", "borrow_or_annotate_drifting_book", "post_bulletin_followup",
    }:
        target_path = "/api/phone/v05/living-town"
    elif surface == "v04_elastic_play" and sub_action_lower in {
        "record_subject_desire", "resolve_subject_desire",
        "settle_morning_dream_pulse", "keep_or_share_dream",
        "tuck_page_note", "advance_reading_page",
        "sketch_window_frost", "trigger_object_chemistry",
    }:
        target_path = "/api/phone/v05/inner-spark"
    elif surface == "v04_elastic_play" and sub_action_lower in {
        "log_object_patina_or_repair",
        "propose_arrangement_change",
        "resolve_arrangement_compromise",
        "unpack_carton_item",
        "mint_parallel_postcard",
    }:
        target_path = "/api/phone/v05/home-craft"
    elif surface == "v02_runtime_migration" and sub_action_lower == "confirm_rescaled_copractice":
        target_path = "/api/phone/v02/honest-ux"
    return surface, target_path, body, cap_required


# ---------------------------------------------------------------------------------------------
# v0.9 native chat brain direct actions (living-agency & world-commitments)
# ---------------------------------------------------------------------------------------------

#: v0.9 ``v09_living_agency_act`` 伴侣专属子动作：TA 侧在发 HTTP 前直接拒绝。
V09_LIVING_AGENCY_PARTNER_ACTIONS: frozenset[str] = frozenset({
    "open_blind_choice",
    "partner_submit_blind_choice",
    "partner_discuss_blind_choice",
    "partner_persuade_stance",
    "register_unformed_topic",
})

#: v0.9 ``v09_living_agency_act`` TA 侧允许的 14 个核心主体子动作。
V09_LIVING_AGENCY_SUBJECT_ACTIONS: frozenset[str] = frozenset({
    "subject_commit_blind_choice",
    "resolve_blind_choice",
    "advance_stance_from_experience",
    "evaluate_partner_persuasion",
    "set_stance_shared",
    "register_or_repurpose_object",
    "store_item_in_object",
    "attach_label_to_object",
    "add_note_to_object",
    "remove_stored_item_from_object",
    "create_custom_category",
    "assign_or_move_category_item",
    "revise_custom_category",
    "record_blocked_attempt",
})

#: 包含服务端兼容别名（``repurpose_object`` / ``empty_stored_items`` / ``use_object_capability``）。
_V09_LIVING_AGENCY_ALLOWED_SUBJECT_ACTIONS: frozenset[str] = (
    V09_LIVING_AGENCY_SUBJECT_ACTIONS
    | frozenset({"repurpose_object", "empty_stored_items", "use_object_capability"})
)

#: v0.9 ``v09_world_commitments_act`` 伴侣/操作者专属子动作：TA 侧在发 HTTP 前直接拒绝。
V09_WORLD_COMMITMENTS_PARTNER_ACTIONS: frozenset[str] = frozenset({
    "place_partner_reservation",
    "propose_shared_appointment",
    "operator_generate_shop_arrivals",
})

#: v0.9 ``v09_world_commitments_act`` TA 侧允许的 4 个主体子动作。
V09_WORLD_COMMITMENTS_SUBJECT_ACTIONS: frozenset[str] = frozenset({
    "create_commitment",
    "contact_npc_or_venue",
    "advance_due_commitments",
    "generate_world_content_batch",
})

_V09_ACT_ROUTES: dict[str, str] = {
    "v09_living_agency_act": "/api/phone/v09/living-agency",
    "v09_world_commitments_act": "/api/phone/v09/world-commitments",
}

_V09_FORBIDDEN_SMUGGLED_KEYS: frozenset[str] = frozenset({
    "actor_side", "author_side", "created_by_side",
    "has_subject_capability", "subject_view", "capability", "capability_token",
    "uid", "user_id", "owner_uid", "local_owner_uid", "euid", "gid",
    "authority", "authorities", "main_self_verified", "verified_main_self",
    "attested_by", "confirm_apply", "csrf_token",
    "home", "hermes_home", "world_home", "profile", "profile_home",
    "owner", "subject", "actor", "binding",
    "is_deployer", "bypass_gate", "grant_capability",
    "intimacy_score", "affection_score", "affection_delta", "favorability",
    "relationship_xp", "obedience_level", "compliance_score", "sycophancy_score",
    "force_flip", "auto_agree", "force_subject_preference",
    "custom_code", "code", "script", "eval", "exec", "python_code", "shell_command",
})

_V09_WORLD_TOP_FORBIDDEN_KEYS: frozenset[str] = frozenset({
    "price", "balance", "grant_id", "workspace_id", "artifact_id",
})

_V09_RETROACTIVE_FORBIDDEN_KEYS: frozenset[str] = frozenset({
    "subject_mood",
    "private_desire",
    "private_desires",
    "recent_chat_topic",
    "recent_chat_topics",
    "fabricate_backstory",
    "retroactive_event",
    "retroactive_events",
})

_V09_SUPPORTED_HINTS: dict[str, dict[str, Any]] = {
    "v09_living_agency_act": {
        "endpoint": "/api/phone/v09/living-agency",
        "subject_actions": [
            "subject_commit_blind_choice",
            "resolve_blind_choice",
            "advance_stance_from_experience",
            "evaluate_partner_persuasion",
            "set_stance_shared",
            "register_or_repurpose_object",
            "store_item_in_object",
            "attach_label_to_object",
            "add_note_to_object",
            "remove_stored_item_from_object",
            "create_custom_category",
            "assign_or_move_category_item",
            "revise_custom_category",
            "record_blocked_attempt",
        ],
        "core_fields": {
            "subject_commit_blind_choice": [
                "choice_id", "selected_choice_id", "reasons([{facet, text_cn}])",
            ],
            "resolve_blind_choice": [
                "choice_id",
                "resolution_mode(KEEP_OWN_STANCE|ACCEPT_CHOICE_KEEP_REASONS|"
                "COMPROMISE_NEW_OPTION|REVISED_WITH_NEW_EVIDENCE)",
                "resolution_note_cn", "retained_reservations", "new_evidence",
            ],
            "advance_stance_from_experience": [
                "stance_id", "target_status(TENTATIVE|FORMED)", "provenance_tier",
                "stance_summary_cn", "receipt_ref|episode_ref|material_ref", "shared_with_partner",
            ],
            "evaluate_partner_persuasion": [
                "stance_id", "persuasion_verdict", "stance_summary_cn", "kept_doubt_cn",
            ],
            "set_stance_shared": ["stance_id", "shared_with_partner"],
            "register_or_repurpose_object": [
                "object_id", "object_kind", "base_name_cn", "custom_label_cn",
                "emptied_from_consumable", "visibility(private|shared)",
            ],
            "store_item_in_object": ["object_id", "item_label_cn", "item_kind", "note_cn"],
            "attach_label_to_object": ["object_id", "custom_label_cn"],
            "add_note_to_object": ["object_id", "note_cn", "visibility(private|shared)"],
            "remove_stored_item_from_object": [
                "object_id", "stored_item_id|item_label_cn", "remove_all",
            ],
            "create_custom_category": [
                "title_cn", "scope_kinds", "description_cn", "visibility(private|shared)",
            ],
            "assign_or_move_category_item": [
                "category_id", "target_kind", "target_ref", "target_title_cn",
                "annotation_cn", "reason_for_move_cn", "visibility",
            ],
            "revise_custom_category": [
                "category_id", "title_cn", "description_cn", "scope_kinds",
                "visibility", "archived", "reason_cn",
            ],
            "record_blocked_attempt": [
                "goal_cn", "attempted_action_cn", "blocker_code", "blocker_detail_cn", "visibility",
            ],
        },
    },
    "v09_world_commitments_act": {
        "endpoint": "/api/phone/v09/world-commitments",
        "subject_actions": [
            "create_commitment",
            "contact_npc_or_venue",
            "advance_due_commitments",
            "generate_world_content_batch",
        ],
        "core_fields": {
            "create_commitment": [
                "kind(BOOK_OR_ITEM_RESERVATION|SCHEDULED_APPOINTMENT|INDEPENDENT_NPC_PLAN)",
                "venue_id", "title_cn", "arrival_due_at|window_start_at",
                "hold_hours", "weather_sensitive", "current_weather",
            ],
            "contact_npc_or_venue": [
                "venue_id", "npc_id",
                "contact_action(observe_or_greet|pickup_reservation|fulfill_appointment|check_status)",
                "commitment_id",
            ],
            "advance_due_commitments": ["current_weather", "target_now_iso"],
            "generate_world_content_batch": [
                "store_id", "trigger_reason", "world_context",
                "force_cooldown_bypass", "generation_mode(offline|llm)",
            ],
        },
    },
}


def _v09_supported_hint(action: str, attempted_action: str = "") -> Optional[dict[str, Any]]:
    meta = _V09_SUPPORTED_HINTS.get(action)
    if not meta:
        return None
    out: dict[str, Any] = {
        "action": action,
        "endpoint": meta["endpoint"],
        "subject_actions": list(meta["subject_actions"]),
        "core_fields": dict(meta["core_fields"]),
    }
    if attempted_action:
        out["attempted_action"] = attempted_action
    return out


def _scan_v09_forbidden_key(
    node: Any,
    forbidden_lower: frozenset[str],
    *,
    _depth: int = 0,
) -> Optional[str]:
    if _depth > 10:
        return None
    if isinstance(node, Mapping):
        for raw_k, val in node.items():
            if isinstance(raw_k, str) and raw_k.strip().lower() in forbidden_lower:
                return raw_k
            nested = _scan_v09_forbidden_key(val, forbidden_lower, _depth=_depth + 1)
            if nested is not None:
                return nested
    elif isinstance(node, (list, tuple)):
        for elem in node:
            nested = _scan_v09_forbidden_key(elem, forbidden_lower, _depth=_depth + 1)
            if nested is not None:
                return nested
    return None


def _validate_v09_key_shapes(node: Any, *, _depth: int = 0) -> None:
    if _depth > 10:
        raise PhoneToolRefused("INVALID_PAYLOAD", "payload 嵌套层级过深。")
    if isinstance(node, Mapping):
        for raw_k, val in node.items():
            if not isinstance(raw_k, str) or not raw_k.strip() or raw_k != raw_k.strip():
                raise PhoneToolRefused("INVALID_PAYLOAD", "payload 键名必须是非空且无首尾空白的字符串。")
            _validate_v09_key_shapes(val, _depth=_depth + 1)
    elif isinstance(node, (list, tuple)):
        for elem in node:
            _validate_v09_key_shapes(elem, _depth=_depth + 1)


def _prepare_v09_action(
    action: str,
    args: Mapping[str, Any],
    default_key: str,
) -> tuple[str, dict[str, Any], str]:
    """Validate one v0.9 direct write call BEFORE any HTTP request leaves this process."""

    raw_payload = args.get("payload")
    if not isinstance(raw_payload, Mapping) or not raw_payload:
        raise PhoneToolRefused(
            "INVALID_PAYLOAD",
            "%s 需要非空对象 payload。" % action,
            supported_actions_hint=_v09_supported_hint(action),
        )
    _validate_v09_key_shapes(raw_payload)

    # Anti-sycophancy erasure guard (case-insensitive, recursive, BEFORE HTTP)
    if _scan_v09_forbidden_key(raw_payload, frozenset({"clear_reservations"})) is not None:
        raise PhoneToolRefused(
            "SYCOPHANTIC_REASON_ERASURE_FORBIDDEN",
            "严禁传入 clear_reservations 抹除 TA 原先保留的理由或顾虑。",
        )

    body: dict[str, Any] = dict(raw_payload)
    if not str(body.get("action") or "").strip():
        for alias_key in ("sub_action", "op"):
            val = body.get(alias_key)
            if isinstance(val, str) and val.strip():
                body["action"] = body.pop(alias_key).strip()
                break

    raw_sub = body.get("action")
    if not isinstance(raw_sub, str) or not raw_sub.strip() or raw_sub != raw_sub.strip():
        raise PhoneToolRefused(
            "INVALID_PAYLOAD",
            "%s 的 payload 必须指明具体的子动作 action。" % action,
            supported_actions_hint=_v09_supported_hint(action),
        )
    sub_action = raw_sub.strip()
    sub_lower = sub_action.lower()

    # Anti-impersonation guard BEFORE HTTP: TA must never call partner-only actions
    if action == "v09_living_agency_act" and sub_lower in V09_LIVING_AGENCY_PARTNER_ACTIONS:
        raise PhoneToolRefused(
            "PARTNER_IMPERSONATION_REFUSED",
            "子动作 %s 仅限伴侣侧执行，TA 侧严禁代签或冒充。" % sub_action,
            supported_actions_hint=_v09_supported_hint(action, sub_action),
        )
    if action == "v09_world_commitments_act" and sub_lower in V09_WORLD_COMMITMENTS_PARTNER_ACTIONS:
        raise PhoneToolRefused(
            "PARTNER_IMPERSONATION_REFUSED",
            "子动作 %s 仅限伴侣或操作者侧执行，TA 侧严禁代签或冒充。" % sub_action,
            supported_actions_hint=_v09_supported_hint(action, sub_action),
        )

    # Smuggled identity / actor / forbidden key guard BEFORE HTTP
    smuggled = _scan_v09_forbidden_key(body, _V09_FORBIDDEN_SMUGGLED_KEYS)
    if smuggled is not None:
        raise PhoneToolRefused(
            "CLIENT_IDENTITY_REFUSED",
            "payload 严禁携带身份、视角或越权字段：%s" % smuggled,
        )

    if action == "v09_world_commitments_act":
        top_bad = sorted(
            k for k in body if isinstance(k, str) and k.strip().lower() in _V09_WORLD_TOP_FORBIDDEN_KEYS
        )
        if top_bad:
            raise PhoneToolRefused(
                "CLIENT_IDENTITY_REFUSED",
                "payload 顶层严禁携带保留字段：%s" % ", ".join(top_bad),
            )
        retro_bad = _scan_v09_forbidden_key(body, _V09_RETROACTIVE_FORBIDDEN_KEYS)
        if retro_bad is not None:
            raise PhoneToolRefused(
                "RETROACTIVE_FABRICATION_FORBIDDEN",
                "世界约定面严禁偷渡隐私或事后编造字段：%s" % retro_bad,
            )
        req_by = body.get("requested_by")
        if isinstance(req_by, str) and req_by.strip().lower() in _PARTNER_ROLE_VALUES:
            raise PhoneToolRefused(
                "PARTNER_IMPERSONATION_REFUSED",
                "TA 侧严禁通过 requested_by=%s 冒充伴侣或操作者发起约定。" % req_by.strip(),
            )

    # Subject action whitelist check BEFORE HTTP
    if action == "v09_living_agency_act" and sub_action not in _V09_LIVING_AGENCY_ALLOWED_SUBJECT_ACTIONS:
        raise PhoneToolRefused(
            "UNKNOWN_ACTION",
            "v09_living_agency_act 不支持子动作：%s" % sub_action,
            supported_actions_hint=_v09_supported_hint(action, sub_action),
        )
    if action == "v09_world_commitments_act" and sub_action not in V09_WORLD_COMMITMENTS_SUBJECT_ACTIONS:
        raise PhoneToolRefused(
            "UNKNOWN_ACTION",
            "v09_world_commitments_act 不支持子动作：%s" % sub_action,
            supported_actions_hint=_v09_supported_hint(action, sub_action),
        )

    # Align outer operation_key with payload.operation_key
    outer_raw = str(args.get("operation_key") or "").strip()
    inner_val = body.get("operation_key")
    if inner_val is not None and (not isinstance(inner_val, str) or not inner_val.strip()):
        raise PhoneToolRefused("INVALID_PAYLOAD", "payload.operation_key 必须是非空字符串。")
    inner_raw = str(inner_val or "").strip()
    if outer_raw and inner_raw and outer_raw != inner_raw:
        raise PhoneToolRefused(
            "OPERATION_KEY_CONFLICT",
            "外层 operation_key 与 payload.operation_key 不一致。",
        )
    effective_key = inner_raw or outer_raw or default_key
    body["operation_key"] = effective_key
    return _V09_ACT_ROUTES[action], body, effective_key


def phone_handler(args: Mapping[str, Any], **kwargs: Any) -> str:
    """One bounded call.  Every refusal is typed; nothing is retried with a new key.

    2026-10-28 external stop: there is deliberately **no** stop/cancel action here.  Stopping
    an Activity from outside is the local operator's act, authorized by its own credential on
    a loopback control route.  The model may still choose ABANDON for herself -- that is a
    Decision, not this tool.
    """

    action = ""
    try:
        if not isinstance(args, Mapping):
            return _result(False, "rejected", "参数必须是对象。", reason_code="INVALID_ARGUMENTS")
        action = str(args.get("action") or "").strip()
        if action not in ACTIONS:
            return _result(False, "rejected", "未知的手机动作。", reason_code="UNKNOWN_ACTION")
        allowed = _FIELD_BY_ACTION.get(action)
        if allowed is not None:
            unknown = sorted(set(args) - set(allowed))
            if unknown:
                return _result(False, "rejected",
                               "这个动作不接受这些字段。", reason_code="UNKNOWN_FIELD",
                               fields=unknown)
        elif set(args) != {"action"}:
            return _result(False, "rejected", "只读动作只接受 action。",
                           reason_code="UNKNOWN_FIELD")
        # 日记写入必须显式给 key：同一次写入重试要落在同一条，不能由工具按正文自动折叠。
        if action == "diary_write" and not str(args.get("operation_key") or "").strip():
            return _result(False, "rejected",
                           "diary_write 需要显式的 operation_key：同一次写入重试必须复用原 key。",
                           reason_code="OPERATION_KEY_REQUIRED")
        # 拍照也要显式 key：同一次意图重试必须落在同一张图，不能靠 scene 完全相同来猜。
        if action == "camera" and not str(args.get("operation_key") or "").strip():
            return _result(False, "rejected",
                           "camera 需要显式的 operation_key：同一次拍照重试必须复用原 key，"
                           "否则会为本机生成第二张图。",
                           reason_code="OPERATION_KEY_REQUIRED")
        if action == "claim_observe" and not str(args.get("operation_key") or "").strip():
            return _result(False, "rejected",
                           "claim_observe 需要显式的 operation_key：同一次观察重试必须复用原 key。",
                           reason_code="OPERATION_KEY_REQUIRED")

        settings = load_settings(kwargs)
        _authorize(settings, kwargs)
        write = action in WRITE_ACTIONS
        if write and not settings["write_enabled"]:
            return _result(False, "rejected",
                           "profile 没有启用手机写入，操作没有提交。",
                           reason_code="PHONE_WRITE_DISABLED")
        # A private artifact can hold the operator's own past, so a restricted memory profile must
        # not gain that surface just because the phone is on: only the economy reads stay open.
        config = settings["config"]
        restricted_memory = bool(config.get("memory")) and \
            str(config.get("memory_tool_policy") or "") != "unrestricted"
        _rm_sub_action = (
            str(
                (args.get("payload") or {}).get("action")
                or (args.get("payload") or {}).get("op")
                or (args.get("payload") or {}).get("sub_action")
                or ""
            ).strip().lower()
            if action == "phone_life_update"
            and str(args.get("target_surface") or "").strip() == "v02_runtime_migration"
            and isinstance(args.get("payload"), Mapping)
            else ""
        )
        touches_private = (
            action in PRIVATE_ARTIFACT_ACTIONS
            or (
                action == "phone_life_update"
                and (
                    str(args.get("target_surface") or "").strip() in LIFE_UPDATE_PRIVATE_SURFACES
                    or _rm_sub_action in (
                        "explicit_import_representation",
                        "approve_pending_forget_hold",
                        "pack_selective_suitcase",
                        "export_suitcase",
                        "import_suitcase",
                        "register_skill_release",
                        "record_skill_execution_outcome",
                        "record_targeted_method_patch",
                        "retire_skill_release",
                        "revoke_skill_source_material",
                        "register_standard_revision",
                        "capture_personality_drift_snapshot",
                        "record_body_history_entry",
                        "record_meaning_version",
                        "record_lineage_branch",
                        "record_layered_news_item",
                        "register_external_fact_source",
                        "enqueue_proactive_message",
                        "record_self_claim",
                        "observe_external_edit",
                        "rebuild_projection_from_canonical",
                        "confirm_rescaled_copractice",
                    )
                )
            )
        )
        if restricted_memory and touches_private and settings["private_artifacts"] is not True:
            return _result(False, "rejected",
                           "记忆处于受限模式：读/写私人笔记需要 profile 显式允许 phone.private_artifacts。",
                           reason_code="PHONE_PRIVATE_ARTIFACTS_NOT_PERMITTED")
        v09_prepared: Optional[tuple[str, dict[str, Any], str]] = None
        if action in ("v09_living_agency_act", "v09_world_commitments_act"):
            v09_prepared = _prepare_v09_action(action, args, _operation_key(action, args))
        _state_doc, csrf = _state(settings)

        if action == "reading_material":
            # The page is a caller choice and the absence of one is meaningful: the owner then
            # answers with the most recent explicit stop, not with "page 1".
            page = _reading_page(args.get("page"), required=False)
            path = READ_ACTIONS[action] + ("" if page == 0 else "?page=%d" % page)
            code, doc = _request(settings, "GET", path)
            result = _summary(doc)
            data = doc.get("data") if isinstance(doc.get("data"), Mapping) else {}
            if not doc.get("ok"):
                message = "这一页读不到；服务说明了原因（没有绑定材料时是诚实的 unavailable）。"
            elif data.get("content_available") is False:
                message = "这一版没有正文可读；没有材料时工具不会补演示文本。"
            else:
                message = "已读取这一页真实正文（只读，没有保存停点）。"
            return _result(bool(doc.get("ok")), str(doc.get("status") or "unknown"), message,
                           http_status=code, result=result)

        if action == "day_timeline":
            # 她自己的工具看这一天：骨架相同，但拿到原入口 detail_ref（要正文仍走 diary_read /
            # album_image）。聚合本身仍不复制日记正文、相册说明或图片字节。
            code, doc = _request(settings, "GET", READ_ACTIONS[action], actor="model")
            result = _summary(doc)
            data = doc.get("data") if isinstance(doc.get("data"), Mapping) else {}
            if not doc.get("ok"):
                message = "今天的时间线读不到；服务说明了原因（读不到不是「今天什么都没发生」）。"
            elif not data.get("entries"):
                message = ("今天还没有任何有记录的条目；空白就是空白，没有补一条凑数。"
                           "各来源的读取状态在 sources 里。")
            else:
                message = ("已读取她今天有记录的时间线（只读，%d 条）：活动、排定、World 动作、"
                           "日记/相册/钱包写入；计划只表示排进今天，不表示已执行；不同来源的"
                           "时间域在 ordering 里如实分开。正文与图片字节不在聚合里，按 "
                           "detail_ref 走原入口。" % int(data.get("count") or 0))
            return _result(bool(doc.get("ok")), str(doc.get("status") or "unknown"), message,
                           http_status=code, result=result)

        if action == "claim_slot":
            slot_id = str(args.get("claim_slot_id") or "").strip()
            if not slot_id:
                return _result(False, "rejected", "claim_slot 需要 claim_slot_id。",
                               reason_code="CLAIM_SLOT_ID_REQUIRED")
            if len(slot_id) > 200 or not set(slot_id) <= OWNER_ID_CHARS:
                return _result(False, "rejected", "claim_slot_id 不是 owner 的协议 id。",
                               reason_code="CLAIM_SLOT_ID_INVALID")
            code, doc = _request(settings, "GET", "/api/phone/claims/" + slot_id)
            result = _summary(doc)
            data = doc.get("data") if isinstance(doc.get("data"), Mapping) else {}
            if not doc.get("ok"):
                message = "这个 slot 读不到；服务说明了原因。"
            else:
                message = ("已读取这个 slot 的当前状态与修订历史（只读）：状态、来源类别、"
                           "记录时间与旧修订分开；被撤回来源的正文不会在这里重新出现。")
            return _result(bool(doc.get("ok")), str(doc.get("status") or "unknown"), message,
                           http_status=code, result=result)

        if action == "participant":
            # F-140: one participant by the owner's own id.  Read-only; the id must be an
            # owner protocol string, never a path/URL, and this action can never create or bind.
            participant_id = str(args.get("participant_id") or "").strip()
            if not participant_id:
                return _result(False, "rejected", "participant 需要 participant_id。",
                               reason_code="PARTICIPANT_ID_REQUIRED")
            if len(participant_id) > 200 or not set(participant_id) <= OWNER_ID_CHARS \
                    or not participant_id.startswith("participant:"):
                return _result(False, "rejected",
                               "participant_id 不是 owner 自己的参与者 id（不接受路径或 URL）。",
                               reason_code="PARTICIPANT_ID_INVALID")
            code, doc = _request(settings, "GET", "/api/phone/participants/" + participant_id)
            result = _summary(doc)
            if not doc.get("ok"):
                message = "这位参与者读不到；服务说明了原因（读不到不是「不存在」）。"
            else:
                message = ("已读取这位参与者的身份事实（只读）：稳定 id、显示名/别名、创建来源"
                           "与时间、显式绑定的端点、关联过的来话引用与计数。这里没有消息正文，"
                           "名字不是账号证据，参与者也不代表或继承任何权限。")
            return _result(bool(doc.get("ok")), str(doc.get("status") or "unknown"), message,
                           http_status=code, result=result)

        if action in READ_ACTIONS:
            read_path = READ_ACTIONS[action]
            if action == "longterm_gifts_lantern" and args.get("layer") is not None:
                layer = args.get("layer")
                if not isinstance(layer, str) or layer not in ("then_only", "with_later_context"):
                    raise PhoneToolRefused("INVALID_ARGUMENTS",
                                           "layer 只能是 then_only 或 with_later_context。")
                read_path = read_path + "?layer=" + layer
            read_cap = (
                _subject_capability(settings, _state_doc, required=False)
                if action in _SUBJECT_VIEW_READ_ACTIONS
                else None
            )
            read_actor = "model" if action in _SUBJECT_VIEW_READ_ACTIONS else None
            code, doc = _request(settings, "GET", read_path, actor=read_actor, capability=read_cap)
            result = _summary(doc)
            if action == "adult_intimacy" and isinstance(result, Mapping):
                result = _strip_solo_fields(result)
            if not doc.get("ok"):
                message = "读取失败，服务说明了原因。"
            elif action == "claims":
                message = ("已读取她当前有理由相信的主张（只读）：每条带范围、适用时间、观察"
                           "时间与来源类别（观察到 / 有人报告 / 据此推断）；冲突并列、过时和"
                           "未知各自具名，不是一个置信数字，也不是动作许可。")
            elif action == "participants":
                message = ("已读取她认识谁（只读）：本实例 operator 显式登记过的参与者，每人带"
                           "稳定 id、显示名/别名、创建来源与时间、端点与关联来话计数。同名不会"
                           "自动合并；这里没有消息正文，也没有创建/绑定/发送动作。")
            elif action == "today":
                message = ("已读取她今天真正发生的活动与作品版本（只读）；读不到是具名的不可用，"
                           "不是「今天什么都没做」。")
            elif action == "v08_emergent_echoes":
                message = ("已读取默契说法、留给未来的问题、旧事新解与各自看法（只读）；"
                           "问题与旧事只在相关时才醒来，不催促；各自看法保留双方各一条。")
            elif action == "v09_living_agency_read":
                message = ("已读取 TA 的真实立场与盲选、物品复用与自建分类状态（只读零写入）；"
                           "未揭晓前伴侣盲选保持加密遮蔽。")
            elif action == "v09_world_commitments_read":
                message = ("已读取动态货架、持久 NPC、世界约定与店铺公告状态（只读零写入）；"
                           "未亲自观察或联系过的场所仅反映当前可见边界事实。")
            elif action == "today_arrangements":
                # 计划/义务/执行是三层不同的证据，不能压成一句「做没做」。
                message = ("已读取今天的计划、义务与执行状态：定义状态 PLANNED 只表示排进了今天，"
                           "不是「还没开始」；义务 PENDING 只表示还没有 owner 回执，不是失败；"
                           "执行状态 UNKNOWN 表示没有可证的执行证据，不是没做。")
            elif action == "diary":
                message = ("已读取日记目录：只有写入时间、字数和日期，不带正文；"
                           "要读正文用 diary_read 指定 entry_id。")
            elif action == "album":
                message = ("已读取相册目录：每张图的时间、说明与来源；这里不带图片字节，"
                           "看图用手机自己的图片地址（album_image 读说明与来源）。"
                           "generated_locally=true 的是本机按她的描述生成的图，不是真实相机照片。")
            elif action == "projects":
                try:
                    from .companion_extras import list_future_questions
                    all_fqs = list_future_questions(_profile_home(kwargs))
                    if all_fqs and isinstance(result, Mapping):
                        result = {**result, "future_questions": all_fqs}
                except Exception:
                    pass
                message = "已读取作品列表。"
            else:
                message = "已读取。"
            read_status = str(doc.get("status") or ("ok" if doc.get("ok") else "unknown"))
            return _result(bool(doc.get("ok")), read_status, message,
                           http_status=code, result=result)

        if action == "work_context":
            # 同一件作品的有界只读上下文：不写、不发送、不启动服务，也不把别的作品/方法文档
            # 认领成它的结果。id 必须是 owner 自己的协议字符串（不是路径/URL）。
            artifact_id = str(args.get("artifact_id") or "").strip()
            if not artifact_id:
                return _result(False, "rejected", "work_context 需要 artifact_id。",
                               reason_code="WORK_ARTIFACT_ID_REQUIRED")
            if len(artifact_id) > 200 or not set(artifact_id) <= OWNER_ID_CHARS:
                return _result(False, "rejected",
                               "artifact_id 不是 owner 自己的协议 id（不接受路径或 URL）。",
                               reason_code="WORK_ARTIFACT_ID_INVALID")
            code, doc = _request(settings, "GET",
                                 "/api/phone/projects/%s/context" % artifact_id)
            result = _summary(doc)
            if not doc.get("ok"):
                message = "这件作品的上下文读不到；服务说明了原因（读不到不是空）。"
            else:
                try:
                    from .companion_extras import list_future_questions
                    fq_items = list_future_questions(_profile_home(kwargs), artifact_id=artifact_id)
                    if fq_items and isinstance(result, Mapping):
                        result = {**result, "future_questions": fq_items}
                except Exception:
                    fq_items = []
                message = ("已读取这件作品的有界上下文（只读）：版本与来历、指向它的真实结果、"
                           "证据指回这些结果的做法与最近一次评价、以它为来源的本地草稿。"
                           "没有证据的部分如实为空，草稿不会被发送。")
                if fq_items:
                    message += "（附带读取到过去版本留给未来的 %d 个问题，见 future_questions。）" % len(fq_items)
            return _result(bool(doc.get("ok")), str(doc.get("status") or "unknown"), message,
                           http_status=code, result=result)

        if action == "work_version":
            # 固定读一个旧版：这是读取，不是回滚。head 与所有版本都由 owner 自己回答。
            artifact_id = str(args.get("artifact_id") or "").strip()
            version_id = str(args.get("version_id") or "").strip()
            if not artifact_id or not version_id:
                return _result(False, "rejected",
                               "work_version 需要 artifact_id 和 version_id。",
                               reason_code="WORK_VERSION_ID_REQUIRED")
            for value in (artifact_id, version_id):
                if len(value) > 200 or not set(value) <= OWNER_ID_CHARS:
                    return _result(False, "rejected",
                                   "id 不是 owner 自己的协议 id（不接受路径或 URL）。",
                                   reason_code="WORK_VERSION_ID_INVALID")
            code, doc = _request(settings, "GET", "/api/phone/projects/%s/versions/%s"
                                 % (artifact_id, version_id))
            result = _summary(doc)
            if not doc.get("ok"):
                message = "这一版读不到；服务说明了原因（读不到不是空，也没有改写 head）。"
            elif ((doc.get("version") or {}).get("derived_text_withheld")):
                message = "这一版的来源已被扣留：正文不投影，版本事实仍然可见。"
            else:
                message = "已固定读取这一版（只读）；head 没有回滚，其它版本也没有被改写。"
            return _result(bool(doc.get("ok")), str(doc.get("status") or "unknown"), message,
                           http_status=code, result=result)

        if action == "diary_read":
            # 读一条日记的正文：这是她自己那部手机的私人读面。entry_id 只接受 owner 自己的
            # diary id，不接受路径/URL；请求带既有的 actor 声明，operator 普通页面读不到正文。
            entry_id = str(args.get("entry_id") or "").strip()
            if not entry_id:
                return _result(False, "rejected", "diary_read 需要 entry_id。",
                               reason_code="DIARY_ENTRY_ID_REQUIRED")
            if len(entry_id) > 200 or not set(entry_id) <= OWNER_ID_CHARS:
                return _result(False, "rejected",
                               "entry_id 不是 owner 自己的日记 id（不接受路径或 URL）。",
                               reason_code="DIARY_ENTRY_ID_INVALID")
            capability = _private_capability(settings, _state_doc)
            code, doc = _request(settings, "GET", "/api/phone/diary/" + entry_id,
                                 actor="model", capability=capability)
            result = _summary(doc)
            entry = doc.get("entry") if isinstance(doc.get("entry"), Mapping) else {}
            if not doc.get("ok"):
                message = "这一条日记读不到；服务说明了原因（读不到不是空）。"
            elif doc.get("body_withheld") is True:
                message = "这一条只拿到了时间与字数，正文没有随这次读取返回。"
            else:
                message = "已读取这一条日记的正文（只读，没有改动任何条目）。"
            return _result(bool(doc.get("ok")), str(doc.get("status") or "unknown"), message,
                           http_status=code, entry=entry, result=result)

        if action == "album_image":
            # 一张图的说明与来源：只读；artifact_id 必须是相册目录给出的 owner id。
            artifact_id = str(args.get("artifact_id") or "").strip()
            if not artifact_id:
                return _result(False, "rejected", "album_image 需要 artifact_id。",
                               reason_code="ALBUM_IMAGE_ID_REQUIRED")
            if len(artifact_id) > 200 or not set(artifact_id) <= OWNER_ID_CHARS:
                return _result(False, "rejected",
                               "artifact_id 不是 owner 自己的相册 id（不接受路径或 URL）。",
                               reason_code="ALBUM_IMAGE_ID_INVALID")
            code, doc = _request(settings, "GET", "/api/phone/album/" + artifact_id)
            result = _summary(doc)
            image = doc.get("image") if isinstance(doc.get("image"), Mapping) else {}
            if not doc.get("ok"):
                message = "这张图读不到；服务说明了原因（读不到不是空）。"
            elif image.get("generated_locally") is True:
                message = ("已读取这一张的说明与来源（只读）：这是本机生成的图像，"
                           "不是真实相机拍到的照片。")
            else:
                message = "已读取这一张的说明与来源（只读）。"
            return _result(bool(doc.get("ok")), str(doc.get("status") or "unknown"), message,
                           http_status=code, image=image, result=result)

        if action == "operation":
            key = str(args.get("operation_key") or "").strip()
            if not key:
                return _result(False, "rejected", "对账需要 operation_key。",
                               reason_code="OPERATION_KEY_REQUIRED")
            code, doc = _request(settings, "GET", "/api/phone/operations/" + key)
            result = _summary(doc)
            if doc.get("ok"):
                message = "这条操作已经在 owner 那里有确定结果。"
            elif str(doc.get("status")) == "not_found":
                message = "owner 没有这条操作；它从未落地。"
            else:
                message = "这条操作仍未有确定结果；不要换 key 重发。"
            return _result(bool(doc.get("ok")), str(doc.get("status") or "unknown"), message,
                           http_status=code, operation_key=key, result=result)
        if action == "world_operation":
            key = str(args.get("operation_key") or "").strip()
            if not key:
                return _result(False, "rejected", "对账需要 operation_key。",
                               reason_code="OPERATION_KEY_REQUIRED")
            code, doc = _request(settings, "GET", "/api/phone/world/operations/" + key)
            result = _summary(doc)
            if doc.get("ok"):
                message = "这次世界动作在 owner 那里已有确定结果。"
            elif str(doc.get("status")) == "not_found":
                message = "owner 没有这条世界操作；它从未落地。"
            else:
                message = "这条世界操作仍未有确定结果；不要换 key 重发。"
            return _result(bool(doc.get("ok")), str(doc.get("status") or "unknown"), message,
                           http_status=code, operation_key=key, result=result)
        if action == "reading_operation":
            key = str(args.get("operation_key") or "").strip()
            if not key:
                return _result(False, "rejected", "对账需要 operation_key。",
                               reason_code="OPERATION_KEY_REQUIRED")
            # The original key, verbatim: an unknown key stays unknown, and the tool never mints
            # a replacement key to "try again" (that would double-write the owner's document).
            code, doc = _request(settings, "GET", "/api/phone/reading/operations/" + key)
            result = _summary(doc)
            if doc.get("ok"):
                message = "这条阅读操作在 owner 那里已有确定结果。"
            elif str(doc.get("status")) == "not_found":
                message = "owner 没有这条阅读操作；它从未落地。"
            else:
                message = "这条阅读操作仍未有确定结果；不要换 key 重发。"
            return _result(bool(doc.get("ok")), str(doc.get("status") or "unknown"), message,
                           http_status=code, operation_key=key, result=result)

        key = _operation_key(action, args)
        capability = None
        if action == "note_save":
            path = "/api/phone/notes"
            body = {"title": args.get("title"), "body": args.get("body"),
                    "operation_key": key}
            if args.get("note_id"):
                body["note_id"] = args["note_id"]
            if args.get("expected_head"):
                body["expected_head"] = args["expected_head"]
        elif action == "diary_write":
            # 她自己的文字：没有标题、没有摘要、没有第二次模型生成。日期由手机服务自己的
            # 业务时间记录，调用方不能自带时间。主体写入先取本次 serve 的本地能力凭据；
            # 文件缺失/错误时在发请求前拒绝，owner 不会收到零凭据的写入。
            path = "/api/phone/diary"
            body = {"body": args.get("body"), "operation_key": key}
            capability = _private_capability(settings, _state_doc)
        elif action == "camera":
            # 她的画面描述 + 她写的一句说明：本机生成一张图，再走既有相册正式保存。
            # 服务地址、模型与生成参数都在手机侧；这里只传她的文字与自己的幂等键。
            path = "/api/phone/camera"
            body = {"scene": args.get("scene"), "caption": args.get("caption"),
                    "operation_key": key}
        elif action == "reference_save":
            path = "/api/phone/references"
            body = {"source_id": args.get("source_id"),
                    "source_revision": args.get("source_revision"),
                    "label": args.get("label"), "operation_key": key}
        elif action == "purchase":
            path = "/api/phone/purchases"
            body = {"catalog_item_id": args.get("catalog_item_id"), "operation_key": key}
        elif action == "store_purchase":
            # 实体店柜台：同一个经济 owner、同一把幂等键；价格/余额/交付状态不由这里给。
            path = "/api/phone/store/purchases"
            body = {"catalog_item_id": args.get("catalog_item_id"), "operation_key": key}
            if args.get("item_name") is not None:
                body["item_name"] = args.get("item_name")
        elif action == "world_action":
            verb = str(args.get("world_action") or "").strip().upper()
            if verb not in WORLD_ACTIONS:
                return _result(False, "rejected", "未知的世界动作。",
                               reason_code="UNKNOWN_WORLD_ACTION")
            path = "/api/phone/world/actions"
            body = {"action": verb, "params": _world_params(verb, args.get("params")),
                    "operation_key": key}
        elif action == "reading_import":
            # The operator's own text for the artifact this instance is already bound to.  No
            # artifact id, path, URL, title or receipt ever leaves this process.
            path = "/api/phone/reading/material"
            body = {"content": _reading_content(args.get("content")),
                    "expected_revision": str(args.get("expected_revision") or "").strip(),
                    "operation_key": key}
        elif action == "reading_position":
            path = "/api/phone/reading/positions"
            body = {"page": _reading_page(args.get("page"), required=True),
                    "source_revision": str(args.get("source_revision") or "").strip(),
                    "expected_head": str(args.get("expected_head") or "").strip(),
                    "operation_key": key}
        elif action == "reading_annotate":
            path = "/api/phone/reading/annotations"
            body = {"kind": str(args.get("kind") or "").strip(),
                    "page": _reading_page(args.get("page"), required=True),
                    "source_revision": str(args.get("source_revision") or "").strip(),
                    "expected_head": str(args.get("expected_head") or "").strip(),
                    "operation_key": key}
            body.update(_reading_annotation(args))
        elif action == "claim_observe":
            # The client names no content and no world id: the phone takes one formal
            # snapshot from the World owner and records exactly what that read contained.
            path = "/api/phone/claims/observations"
            body = {"operation_key": key}
        elif action == "phone_life_update":
            life_surface, path, body, cap_required = _prepare_life_update(args, key)
            if str(body.get("operation_key") or "").strip():
                key = str(body["operation_key"]).strip()
            capability = _subject_capability(settings, _state_doc, required=cap_required)
        elif action in ("v09_living_agency_act", "v09_world_commitments_act"):
            assert v09_prepared is not None
            path, body, key = v09_prepared
            capability = _subject_capability(settings, _state_doc, required=True)
        else:  # use
            path = "/api/phone/items/use"
            body = {"item_instance_id": args.get("item_instance_id"), "operation_key": key}

        # Only the camera (or explicit llm world-content generation) gets a longer budget;
        # every other write action keeps the short default so a stalled read fails fast.
        req_timeout: Optional[float] = None
        if action == "camera":
            req_timeout = CAMERA_TIMEOUT_SECONDS
        elif action == "v09_world_commitments_act" and str(body.get("generation_mode") or "").strip().lower() == "llm":
            req_timeout = 60.0
        code, doc = _request(settings, "POST", path, body, csrf=csrf, capability=capability,
                             timeout=req_timeout)
        status = str(doc.get("status") or "unknown")
        result = _summary(doc)
        if action in ("phone_life_update", "v09_living_agency_act", "v09_world_commitments_act"):
            if action == "phone_life_update" and life_surface == "adult_intimacy" and isinstance(result, Mapping):
                result = _strip_solo_fields(result)
            if doc.get("ok") is True and status not in ("applied", "already_applied"):
                status = "applied"
            elif doc.get("ok") is not True and status not in ("rejected", "unavailable", "disabled"):
                if code in (400, 403, 404, 409, 413, 422):
                    status = "rejected"
        if status in ("applied", "already_applied"):
            message = ("owner 已提交。" if status == "applied"
                       else "同一意图此前已提交，没有再次扣款或消耗。")
        elif status == "rejected":
            message = "owner 拒绝了这次操作，什么都没改。"
        elif status == "unavailable":
            message = "依赖的 owner 当前不可用，操作没有确认。"
        elif status == "disabled":
            message = "这个功能当前关闭。"
        else:
            message = "结果未知：不要换 key 重发，先按 operation_key 对账。"
        if action == "world_action":
            # A world command being accepted is not the same as its effect having happened.
            if status == "applied":
                message = ("owner 已接受这次世界动作；出发/姿势/休息的事实只以 owner 的快照为准。"
                           if verb == "MOVE" else
                           "owner 已接受这次世界动作；身体与世界的变化只以 owner 的快照为准。")
            elif status == "already_applied":
                message = "同一意图此前已提交，没有再次改变世界或身体。"
            elif status == "rejected":
                message = "owner 拒绝了这次世界动作，什么都没改。"
            elif status in ("unavailable", "disabled"):
                message = "世界/身体服务当前不可用或关闭，动作没有确认。"
            else:
                message = "结果未知：不要换 key 重发，先用 action=world_operation 查原 key。"
        if action == "claim_observe":
            if status == "applied":
                message = ("已记录这次正式观察：来源 id/修订与观察回执随证据保存；它只证明"
                           "当时看到什么，不更新世界、也不是动作许可。")
            elif status == "already_applied":
                message = "同一次观察此前已记录，没有新增主张。"
            elif status == "rejected":
                message = "World 观察当前不可用或来源不完整，没有记录任何主张。"
            elif status in ("unavailable", "disabled"):
                message = "World/主张存储当前不可用，这次观察没有确认。"
            else:
                message = "结果未知：不要换 key 重发，先读一次当前主张。"
        if action == "store_purchase":
            # 柜台成交 ≠ 已到货：到货、可布置、可用都是 owner 之后的快照事实。
            if status == "applied":
                message = ("owner 已在实体店柜台成交；是否已到货、能否布置只以世界快照为准。")
            elif status == "already_applied":
                message = "同一意图此前已在柜台成交，没有再次扣款。"
            elif status == "rejected":
                message = "owner 拒绝了这次店内购买，什么都没改（未扣款、未交付）。"
            elif status in ("unavailable", "disabled"):
                message = "实体店/经济 owner 当前不可用或关闭，购买没有确认。"
            else:
                message = "结果未知：不要换 key 重发，先按 operation_key 对账。"
        if action == "diary_write":
            if status == "applied":
                message = "这一条日记已经保存；时间由手机自己的业务时间记录，没有生成摘要。"
            elif status == "already_applied":
                message = "同一次写入此前已经保存，没有写第二条。"
            elif status == "rejected":
                message = "owner 拒绝了这次写入，没有记下任何文字。"
            elif status in ("unavailable", "disabled"):
                message = "日记 owner 当前不可用或关闭，这次写入没有确认。"
            else:
                message = "结果未知：不要换 key 重发，先按 operation_key 对账。"
        if action == "camera":
            if status == "applied":
                message = ("这张图已经在本机生成，并由相册正式保存；它是本机生成的图像，"
                           "不是真实相机照片，也不是她亲眼所见。她的中文画面原话和实际送去"
                           "生成的英文提示词都记在相册详情里。")
            elif status == "already_applied":
                message = "同一次拍照此前已经保存：直接读回原图，没有再生成第二张。"
            elif status == "rejected":
                message = ("这次拍照被拒绝（本机生成服务可能正忙，或画面/说明不合规），"
                           "什么都没有保存。")
            elif status in ("unavailable", "disabled"):
                if str(doc.get("reason_code") or "").startswith("CAMERA_TRANSLATION_"):
                    message = ("画面没能转成生成模型能懂的英文标签（本机转换器不可用或输出"
                               "不合规），这次没有生成、也没有保存。")
                else:
                    message = "本机生成服务或相册当前不可用，这次拍照没有确认保存。"
            else:
                message = "结果未知：不要换 key 重发，先按 operation_key 对账。"
        if action in ("reading_import", "reading_position", "reading_annotate"):
            # 阅读写操作的每一条都是 owner 的话：导入 ≠ 读过，位置 ≠ 理解，页注 ≠ 读懂。
            message = _reading_message(action, status, str(doc.get("reason_code") or ""))
        if action == "phone_life_update":
            if status == "applied":
                message = "已提交主体侧 %s 更新，实际状态以手机服务快照为准。" % life_surface
            elif status == "already_applied":
                message = "同一意图此前已在 %s 生效，未重复写入。" % life_surface
            elif status == "rejected":
                message = "手机服务拒绝了这次 %s 更新，状态未改变。" % life_surface
        if action == "v09_living_agency_act":
            if status == "applied":
                message = "已提交 TA 侧 v0.9 真实立场/物品分类操作，实际状态以手机服务快照为准。"
            elif status == "already_applied":
                message = "同一意图此前已在 v0.9 真实立场与物品分类面生效，未重复写入。"
            elif status == "rejected":
                message = "手机服务拒绝了这次 v0.9 真实立场/物品分类操作，状态未改变。"
        if action == "v09_world_commitments_act":
            if status == "applied":
                message = "已提交 TA 侧 v0.9 世界约定/NPC 联系/货架操作，实际状态以手机服务快照为准。"
            elif status == "already_applied":
                message = "同一意图此前已在 v0.9 世界约定与货架面生效，未重复写入。"
            elif status == "rejected":
                message = "手机服务拒绝了这次 v0.9 世界约定/货架操作，状态未改变。"
        try:
            from .companion_extras import record_phone_action_receipt
            receipt_args = (
                {**args, "payload": body}
                if action in ("phone_life_update", "v09_living_agency_act", "v09_world_commitments_act")
                else args
            )
            receipt_doc = {**doc, "status": status} if isinstance(doc, Mapping) else {"status": status}
            record_phone_action_receipt(
                _profile_home(kwargs), action, receipt_args, receipt_doc, operation_key=key
            )
        except Exception:
            pass
        extra_rc = (
            str(doc.get("reason_code"))
            if action in ("phone_life_update", "v09_living_agency_act", "v09_world_commitments_act")
            and not doc.get("ok")
            and doc.get("reason_code")
            else None
        )
        hint_obj: Optional[dict[str, Any]] = None
        if action in ("phone_life_update", "v09_living_agency_act", "v09_world_commitments_act") and not doc.get("ok"):
            rc_up = (extra_rc or "").upper()
            if code == 400 or (
                rc_up.startswith(("UNKNOWN_FIELD", "INVALID_", "ACTION_", "MISSING_", "NO_DECLARED_"))
                or rc_up.endswith(("_REQUIRED", "_INVALID", "_UNSUPPORTED"))
            ):
                if action == "phone_life_update":
                    hint_obj = _surface_supported_hint(
                        life_surface, str(body.get("action") or "")
                    )
                else:
                    hint_obj = _v09_supported_hint(
                        action, str(body.get("action") or "")
                    )
        return _result(bool(doc.get("ok")), status, message, http_status=code,
                       operation_key=key, reason_code=extra_rc,
                       supported_actions_hint=hint_obj, result=result)
    except PhoneToolRefused as exc:
        if exc.reason_code == "PHONE_UNREACHABLE" and action == "camera":
            # A camera capture is long: losing the answer never proves the phone did nothing.
            # The picture may already be generated and saved -- the honest answer is "unknown".
            message = ("没有等到手机的回答：这次拍照是否已生成、是否已保存都无法确认，"
                       "不要换 operation_key 重发，先按原 key 对账。")
        else:
            message = exc.message or "手机服务不可用，操作没有提交。"
        return _result(False, "rejected" if exc.reason_code != "PHONE_UNREACHABLE"
                       else "unavailable", message, reason_code=exc.reason_code,
                       supported_actions_hint=exc.supported_actions_hint)
    except Exception as exc:  # never leak paths, tokens or raw exception text
        return _result(False, "unavailable", "手机状态无法确认，操作没有提交。",
                       reason_code="PHONE_TOOL_ERROR", error_type=type(exc).__name__)


def phone_read(action: str, **kwargs: Any) -> str:
    """Convenience read entry delegating to ``phone_handler({'action': action}, **kwargs)``."""
    return phone_handler({"action": str(action or "").strip()}, **kwargs)


def phone_act(
    target_surface: str,
    payload: Mapping[str, Any],
    *,
    operation_key: Optional[str] = None,
    **kwargs: Any,
) -> str:
    """Convenience write entry delegating to ``phone_handler({'action': 'phone_life_update', ...})``."""
    req: dict[str, Any] = {
        "action": "phone_life_update",
        "target_surface": str(target_surface or "").strip(),
        "payload": dict(payload or {}),
    }
    if operation_key:
        req["operation_key"] = str(operation_key)
    return phone_handler(req, **kwargs)


__all__ = [
    "ACTIONS",
    "LIFE_UPDATE_PRIVATE_SURFACES",
    "LIFE_UPDATE_SURFACES",
    "MAX_READING_ANNOTATION_BYTES",
    "MAX_READING_CONTENT_BYTES",
    "MAX_READING_PAGE",
    "MAX_READING_TITLE_BYTES",
    "PHONE_TOOL_SCHEMA",
    "PRIVATE_ARTIFACT_ACTIONS",
    "PhoneToolRefused",
    "LIFE_READ_SURFACES",
    "READ_ACTIONS",
    "SCHEMA_VERSION",
    "V09_LIVING_AGENCY_PARTNER_ACTIONS",
    "V09_LIVING_AGENCY_SUBJECT_ACTIONS",
    "V09_WORLD_COMMITMENTS_PARTNER_ACTIONS",
    "V09_WORLD_COMMITMENTS_SUBJECT_ACTIONS",
    "WORLD_ACTIONS",
    "WRITE_ACTIONS",
    "entry_allowed",
    "load_settings",
    "phone_act",
    "phone_handler",
    "phone_read",
]

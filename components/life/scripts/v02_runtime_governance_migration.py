#!/usr/bin/env python3
"""0.2 语义事件合并唤醒、省电休眠承接与迁居选择性旅行箱领域引擎（确定性零大模型）。

职责边界：
1. 语义事件合并唤醒与背压路由器（Wake/Runtime/Budget + F-168, F-170, F-174, F-187）：
   - 严格区分控制通道（``is_control=True``，零防抖直通、不受后台唤醒配额阻塞）与可替代环境/日常机会事件
     （按 ``dedup_family`` 在防抖窗口内合并，只产生 1 次有效唤醒，如实累加 ``coalesced_saved_wakes``）；
   - 硬配额门控：后台认知默认上限 ``hourly_wake_cap=6``、``daily_wake_cap=24``，前台与控制通道独立保障；
   - 每次有效唤醒记录归因 ``wake_reason`` 与工作区候选裁剪审计 ``workspace_audits``，并在伴侣视角自动脱敏
     未公开私人草稿标题；
   - 07 章 §二 WakeTicket 持久唤醒票：每次归因同步落票（``wake_tickets`` 有界列表，随状态文件原子
     落盘、重启载入、容量截断），同 coalesce_key 合并折进既有票（``folded_count``+1），control/
     foreground 旁路票照记并标注 ``bypass``，背压延期记 open 票；runtime-migration 读面暴露票清单
     与 folded 计数、``est_tokens`` 合计，供断电恢复后对账。
2. 安静无事 vs 进程卡死严格区分 + 预算骤降 90% 省电活法 + 休眠零补写（F-169, F-171, F-173, F-191 & #35/#36）：
   - 结合 ``<day_root>`` 真实实例健康信号（``instance.json``、``ready.json``、队列积压）与当前运行模式产出
     ``QUIET_HEALTHY`` / ``ACTIVE_HEALTHY`` / ``LOW_BUDGET_CONSERVATION`` / ``DORMANT_NO_COGNITION`` /
     ``STALLED_OR_DEGRADED``；
   - 低预算省电档暂停漫游探查与新开大坑，保留在途作品保存点、已认领承诺与删除/对账责任，只做局部小修订，
     绝不把省预算伪装成身体疲惫或心情不好；
   - 休眠档记录 ``dormant_since``，恢复回正常档时精确计算 ``dormant_gap_seconds`` 并显式标记
     ``no_fake_backfill=True``，严禁补造休眠期间的假日记或假经历。
3. 换模型/换载体能力体检、「今天还能做什么」生活卡、选择性旅行箱与事实面新鲜度
   （F-139, F-143, F-147, F-192, F-193 & §14.5, §14.7, #34）。
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

try:
    from components.life.scripts import state_tx_guard as _tx
except ImportError:
    _LIFE_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
    if _LIFE_SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, _LIFE_SCRIPTS_DIR)
    import state_tx_guard as _tx  # type: ignore[no-redef]

try:
    from components.life.scripts import v02_recall_projection as _recall_proj
except ImportError:
    _LIFE_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
    if _LIFE_SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, _LIFE_SCRIPTS_DIR)
    import v02_recall_projection as _recall_proj  # type: ignore[no-redef]

VC_AUTHORITY_SCHEMA = _recall_proj.VC_AUTHORITY_SCHEMA
VC_AUTHORITY_WATERMARK_REL_PATH = _recall_proj.VC_AUTHORITY_WATERMARK_REL_PATH
compute_vc_item_digest = _recall_proj.compute_vc_item_digest
read_vc_authority_watermark_readonly = _recall_proj.read_vc_authority_watermark_readonly
record_vc_revocation_seal = _recall_proj.record_vc_revocation_seal

VIEW_SCHEMA = "nyairo.v02.runtime_migration.v1"
ENDPOINT_PATH = "/api/phone/v02/runtime-migration"

STATE_DIRNAME = "phone"
STATE_FILENAME = "v02_runtime_migration.json"
RUNTIME_GOVERNANCE_FILENAME = "v02_runtime_governance.json"
RUNTIME_GOVERNANCE_SCHEMA = "nyairo.v02.runtime_governance.v1"
STATE_SCHEMA_VERSION = 1

DEVICE_PRESENCE_SCHEMA = "nyairo.v02.device_presence.v1"
DEFAULT_DEVICE_LEASE_SECONDS = 900
MAX_DEVICE_LEASE_SECONDS = 86400
MAX_DEVICE_HANDOFF_HISTORY = 16

CHATLESS_LIFE_REVIEW_SCHEMA = "nyairo.v02.chatless_life_review.v1"
_VC_DIGEST_HEX_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_VC_SAFE_SCOPE_RE = re.compile(r"[^A-Za-z0-9_.:-]")

DEFAULT_TZ = timezone(timedelta(hours=8))

DEFAULT_HOURLY_WAKE_CAP = 6
DEFAULT_DAILY_WAKE_CAP = 24
DEFAULT_COALESCE_WINDOW_SECONDS = 300

MAX_WAKE_ATTRIBUTIONS = 32
MAX_WORKSPACE_AUDITS = 24
MAX_WAKE_TIMESTAMPS = 256
MAX_OPERATION_LEDGER = 128

# ---------------------------------------------------------------------------------
# Ch.16 §7 一年衰败与维护：22 类监测矩阵（v2.0 一年期漂移监测，不用生命总分）。
# 只读统计既有实证文件的存在性/新鲜度，零写盘、零大模型；22 类严格分属四大域。
# ---------------------------------------------------------------------------------
DECAY_MONITOR_SCHEMA = "nyairo.v02.annual_decay_matrix.v1"
DECAY_MONITOR_GROUP_MEMORY = "memory_recall"
DECAY_MONITOR_GROUP_RELATIONSHIP = "relationship_common_ground"
DECAY_MONITOR_GROUP_AGENCY = "agency_habits_projects"
DECAY_MONITOR_GROUP_WORLD = "world_rhythm_somatic"
DECAY_MONITOR_GROUPS: tuple[str, ...] = (
    DECAY_MONITOR_GROUP_MEMORY,
    DECAY_MONITOR_GROUP_RELATIONSHIP,
    DECAY_MONITOR_GROUP_AGENCY,
    DECAY_MONITOR_GROUP_WORLD,
)

DECAY_STATUS_ACTIVE = "ACTIVE"
DECAY_STATUS_DORMANT = "DORMANT"
DECAY_STATUS_NEEDS_REANCHOR = "NEEDS_REANCHOR"
DECAY_STATUS_ABSENT_BASELINE = "ABSENT_BASELINE"
DECAY_MONITOR_STATUSES: tuple[str, ...] = (
    DECAY_STATUS_ACTIVE,
    DECAY_STATUS_DORMANT,
    DECAY_STATUS_NEEDS_REANCHOR,
    DECAY_STATUS_ABSENT_BASELINE,
)

#: 每个域一项一行的监测登记：来源文件、计数键、半衰期、底线权重、漂移区间与重锚定策略。
#: 22 项总数固定（6 + 5 + 6 + 5），缺少或重复都会在投影的 total/group_counts 里显形。
DECAY_MONITOR_CATEGORIES_22: tuple[dict[str, Any], ...] = (
    # 1) memory_recall (6)
    {
        "category": "episodic_traces", "group": DECAY_MONITOR_GROUP_MEMORY,
        "label_cn": "情景记忆痕迹", "sources": ("memory/v02_recall_projection_state.json",),
        "count_key": "memories", "half_life_days": 180, "floor_weight": 0.15,
        "drift_band": (0.0, 0.40), "re_anchor_policy": "REHEARSE_OR_ARCHIVE_CONFIRM",
    },
    {
        "category": "autobiographical_anchors", "group": DECAY_MONITOR_GROUP_MEMORY,
        "label_cn": "自传体锚点", "sources": ("memory/v02_recall_projection_state.json",),
        "count_key": "memories", "half_life_days": 365, "floor_weight": 0.20,
        "drift_band": (0.0, 0.25), "re_anchor_policy": "RE_ANCHOR_FROM_MILESTONES",
    },
    {
        "category": "sensory_impressions", "group": DECAY_MONITOR_GROUP_MEMORY,
        "label_cn": "感官印象", "sources": ("phone/v02_crossmodal.json",),
        "count_key": None, "half_life_days": 210, "floor_weight": 0.10,
        "drift_band": (0.0, 0.50), "re_anchor_policy": "OBSERVE_ANEW",
    },
    {
        "category": "promise_commitments", "group": DECAY_MONITOR_GROUP_MEMORY,
        "label_cn": "承诺与约定", "sources": ("phone/reunion_anchor.json", "phone/home_recipes.json"),
        "count_key": None, "half_life_days": 120, "floor_weight": 0.30,
        "drift_band": (0.0, 0.30), "re_anchor_policy": "RECONFIRM_WITH_PARTNER",
    },
    {
        "category": "seasonal_milestones", "group": DECAY_MONITOR_GROUP_MEMORY,
        "label_cn": "季节里程碑", "sources": ("memory/v02_common_ground_state.json",),
        "count_key": "items", "half_life_days": 365, "floor_weight": 0.25,
        "drift_band": (0.0, 0.20), "re_anchor_policy": "MARK_NEXT_SEASON",
    },
    {
        "category": "active_faded_items", "group": DECAY_MONITOR_GROUP_MEMORY,
        "label_cn": "主动淡忘项", "sources": ("memory/v02_recall_projection_state.json",),
        "count_key": "memories", "half_life_days": 90, "floor_weight": 0.00,
        "drift_band": (0.0, 0.60), "re_anchor_policy": "ALLOW_TO_FADE",
    },
    # 2) relationship_common_ground (5)
    {
        "category": "shared_vocabulary", "group": DECAY_MONITOR_GROUP_RELATIONSHIP,
        "label_cn": "共同语汇", "sources": ("memory/v02_common_ground_state.json",),
        "count_key": "items", "half_life_days": 300, "floor_weight": 0.20,
        "drift_band": (0.0, 0.25), "re_anchor_policy": "REUSE_IN_DIALOGUE",
    },
    {
        "category": "agreed_boundaries", "group": DECAY_MONITOR_GROUP_RELATIONSHIP,
        "label_cn": "已达成边界", "sources": ("phone/reunion_anchor.json", "phone/tentative_plans.json"),
        "count_key": None, "half_life_days": 365, "floor_weight": 0.35,
        "drift_band": (0.0, 0.15), "re_anchor_policy": "RESTATE_AGREEMENT",
    },
    {
        "category": "pending_clarifications", "group": DECAY_MONITOR_GROUP_RELATIONSHIP,
        "label_cn": "待澄清事项", "sources": ("memory/v02_common_ground_state.json",),
        "count_key": "items", "half_life_days": 21, "floor_weight": 0.05,
        "drift_band": (0.0, 0.70), "re_anchor_policy": "RAISE_NATURAL_QUESTION",
    },
    {
        "category": "gift_lantern_entries", "group": DECAY_MONITOR_GROUP_RELATIONSHIP,
        "label_cn": "信物灯盏条目", "sources": ("phone/longterm_gifts_lantern.json",),
        "count_key": None, "half_life_days": 400, "floor_weight": 0.25,
        "drift_band": (0.0, 0.20), "re_anchor_policy": "REVISIT_LANTERN",
    },
    {
        "category": "co_presence_habits", "group": DECAY_MONITOR_GROUP_RELATIONSHIP,
        "label_cn": "共在习惯", "sources": ("phone/autonomy_rhythm.json",),
        "count_key": None, "half_life_days": 180, "floor_weight": 0.20,
        "drift_band": (0.0, 0.30), "re_anchor_policy": "RENEGOTIATE_RHYTHM",
    },
    # 3) agency_habits_projects (6)
    {
        "category": "preference_satiation", "group": DECAY_MONITOR_GROUP_AGENCY,
        "label_cn": "偏好饱和", "sources": ("state/v02_agency_depth_state.json",),
        "count_key": "preferences", "half_life_days": 30, "floor_weight": 0.05,
        "drift_band": (0.0, 0.50), "re_anchor_policy": "ROTATE_NOVELTY",
    },
    {
        "category": "open_inquiries", "group": DECAY_MONITOR_GROUP_AGENCY,
        "label_cn": "有界探究", "sources": ("state/v02_agency_depth_state.json",),
        "count_key": "inquiries", "half_life_days": 60, "floor_weight": 0.10,
        "drift_band": (0.0, 0.40), "re_anchor_policy": "ADD_EVIDENCE_OR_STOP",
    },
    {
        "category": "tentative_plans", "group": DECAY_MONITOR_GROUP_AGENCY,
        "label_cn": "待定小约定", "sources": ("phone/tentative_plans.json",),
        "count_key": "plans", "half_life_days": 30, "floor_weight": 0.05,
        "drift_band": (0.0, 0.50), "re_anchor_policy": "CONFIRM_OR_RELEASE",
    },
    {
        "category": "tomorrow_notes", "group": DECAY_MONITOR_GROUP_AGENCY,
        "label_cn": "明日便签", "sources": ("phone/desk_tomorrow_note.json",),
        "count_key": None, "half_life_days": 7, "floor_weight": 0.00,
        "drift_band": (0.0, 0.80), "re_anchor_policy": "FOLLOW_OR_DROP",
    },
    {
        "category": "workbench_drafts", "group": DECAY_MONITOR_GROUP_AGENCY,
        "label_cn": "工作台草稿", "sources": ("phone/workbench_tray.json", "phone/workbench_curio.json"),
        "count_key": None, "half_life_days": 90, "floor_weight": 0.20,
        "drift_band": (0.0, 0.35), "re_anchor_policy": "SAVE_CHECKPOINT",
    },
    {
        "category": "second_order_rules", "group": DECAY_MONITOR_GROUP_AGENCY,
        "label_cn": "二阶小规矩", "sources": ("state/v02_agency_depth_state.json",),
        "count_key": "second_order_rules", "half_life_days": 240, "floor_weight": 0.20,
        "drift_band": (0.0, 0.30), "re_anchor_policy": "REVIEW_AND_REVISE",
    },
    # 4) world_rhythm_somatic (5)
    {
        "category": "courtyard_plant_care", "group": DECAY_MONITOR_GROUP_WORLD,
        "label_cn": "院子盆栽照料", "sources": ("world/v04_neighborhood_state.json",),
        "count_key": None, "half_life_days": 14, "floor_weight": 0.10,
        "drift_band": (0.0, 0.50), "re_anchor_policy": "WATER_OR_MOVE",
    },
    {
        "category": "neighborhood_familiarity", "group": DECAY_MONITOR_GROUP_WORLD,
        "label_cn": "街区熟悉度", "sources": ("world/v04_neighborhood_state.json",),
        "count_key": None, "half_life_days": 180, "floor_weight": 0.15,
        "drift_band": (0.0, 0.35), "re_anchor_policy": "WALK_ROUTE_REVISIT",
    },
    {
        "category": "illness_recovery_memory", "group": DECAY_MONITOR_GROUP_WORLD,
        "label_cn": "病后恢复记忆", "sources": ("phone/v03_ecology.json", "phone/v03_ecology_state.json"),
        "count_key": None, "half_life_days": 60, "floor_weight": 0.10,
        "drift_band": (0.0, 0.40), "re_anchor_policy": "REST_AND_RECORD",
    },
    {
        "category": "somatic_comfort_baseline", "group": DECAY_MONITOR_GROUP_WORLD,
        "label_cn": "身体舒适基线", "sources": ("phone/somatic_comfort.json",),
        "count_key": None, "half_life_days": 120, "floor_weight": 0.20,
        "drift_band": (0.0, 0.30), "re_anchor_policy": "RECHECK_WARMTH",
    },
    {
        "category": "economic_spending_rhythm", "group": DECAY_MONITOR_GROUP_WORLD,
        "label_cn": "收支节奏", "sources": ("phone/v03_ecology.json", "phone/v03_ecology_state.json"),
        "count_key": None, "half_life_days": 90, "floor_weight": 0.15,
        "drift_band": (0.0, 0.35), "re_anchor_policy": "REVIEW_LEDGER",
    },
)

# ---------------------------------------------------------------------------------
# 07 章 §二 WakeTicket：持久唤醒票（07-宏观驱动引擎与事件循环 §二）。
# 票账与既有 operation_ledger 同风格：state 内有界列表，随 _save_state_atomic 原子落盘，
# 重启由 _load_state_readonly 载入（容量截断防膨胀），断电恢复后经 runtime-migration 读面
# 对账（folded 计数、est_tokens 合计）。
# ---------------------------------------------------------------------------------
WAKE_TICKET_SCHEMA = "nyairo.v02.wake_ticket.v1"
MAX_WAKE_TICKETS = 128
#: 每张票留痕的合并明细条数上限（folded_count 计数本身不受限，永远如实累加）。
MAX_TICKET_FOLD_TRACE = 16
#: 无票 est 时的保守常数：07 章 §四 tokens_max=50_000 / calls_max=24 → 约 2083/次，取 2000，
#: 24 次全按保守常数计仍低于日 token 预算（调用上限先绑定）。
DEFAULT_WAKE_TICKET_EST_TOKENS = 2000

WAKE_TICKET_STATUS_OPEN = "open"
WAKE_TICKET_STATUS_FIRED = "fired"
WAKE_TICKET_STATUS_FOLDED_INTO = "folded_into"
WAKE_TICKET_STATUS_EXPIRED = "expired"
WAKE_TICKET_STATUSES = (
    WAKE_TICKET_STATUS_OPEN,
    WAKE_TICKET_STATUS_FIRED,
    WAKE_TICKET_STATUS_FOLDED_INTO,
    WAKE_TICKET_STATUS_EXPIRED,
)

#: 07 章 §二 priority：0 安全/事实纠错 · 1 断点与到期 · 2 身体节律 · 3 发散思考/环境机会。
#: 本路由只有通道语义：control=0，foreground_chat=1，background 环境机会=3。
WAKE_TICKET_PRIORITY_CONTROL = 0
WAKE_TICKET_PRIORITY_FOREGROUND = 1
WAKE_TICKET_PRIORITY_BACKGROUND = 3

WAKE_TICKET_BYPASS_CONTROL = "control"
WAKE_TICKET_BYPASS_FOREGROUND = "foreground_chat"
WAKE_TICKET_FOLD_KIND_COALESCE = "coalesce"
WAKE_TICKET_FOLD_KIND_QUOTA = "quota_backpressure"

HEALTH_QUIET_HEALTHY = "QUIET_HEALTHY"
HEALTH_ACTIVE_HEALTHY = "ACTIVE_HEALTHY"
HEALTH_LOW_BUDGET_CONSERVATION = "LOW_BUDGET_CONSERVATION"
HEALTH_DORMANT_NO_COGNITION = "DORMANT_NO_COGNITION"
HEALTH_STALLED_OR_DEGRADED = "STALLED_OR_DEGRADED"
HEALTH_STATES = (
    HEALTH_QUIET_HEALTHY,
    HEALTH_ACTIVE_HEALTHY,
    HEALTH_LOW_BUDGET_CONSERVATION,
    HEALTH_DORMANT_NO_COGNITION,
    HEALTH_STALLED_OR_DEGRADED,
)

MODE_NORMAL = "NORMAL"
MODE_LOW_BUDGET = "LOW_BUDGET"
MODE_DORMANT = "DORMANT"

RETIREMENT_STATUS_ACTIVE = "ACTIVE"
RETIREMENT_STATUS_PENDING = "PENDING_CONFIRMATION"
RETIREMENT_STATUS_SEALED = "RETIRED_ARCHIVE_SEALED"
RETIREMENT_STATUSES: tuple[str, ...] = (
    RETIREMENT_STATUS_ACTIVE,
    RETIREMENT_STATUS_PENDING,
    RETIREMENT_STATUS_SEALED,
)
RETIREMENT_CONFIRMATION_PHRASE_CN = "确认温和封存归档"

PROACTIVE_DEFAULT_QUIET_START_HM = "22:30"
PROACTIVE_DEFAULT_QUIET_END_HM = "07:30"
PROACTIVE_DEFAULT_DAILY_CAP = 2
PROACTIVE_MIN_DAILY_CAP = 1
PROACTIVE_MAX_DAILY_CAP = 6
PROACTIVE_DEFAULT_COOLDOWN_MINUTES = 180
PROACTIVE_MIN_COOLDOWN_MINUTES = 30
PROACTIVE_MAX_COOLDOWN_MINUTES = 1440
PROACTIVE_MAX_PENDING_QUEUE = 12
PROACTIVE_MAX_RECENT_LOG = 24
_HM_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

BUDGET_MODE_ALIASES: dict[str, str] = {
    "NORMAL": MODE_NORMAL,
    "LOW_BUDGET": MODE_LOW_BUDGET,
    "CONSERVATION": MODE_LOW_BUDGET,
    "LOW_BUDGET_CONSERVATION": MODE_LOW_BUDGET,
    "DORMANT": MODE_DORMANT,
    "DORMANT_NO_COGNITION": MODE_DORMANT,
}

SUITCASE_RECORDS_ONLY = "records_only"
SUITCASE_RECORDS_AND_OBJECTS = "records_and_objects"
SUITCASE_FULL_PRACTICE = "full_practice"
SUITCASE_SELECTIVE_PRACTICE = "selective_practice"
SUITCASE_PROFILE_MODES = (
    SUITCASE_RECORDS_ONLY,
    SUITCASE_RECORDS_AND_OBJECTS,
    SUITCASE_FULL_PRACTICE,
    SUITCASE_SELECTIVE_PRACTICE,
)

SUITCASE_BUNDLE_SCHEMA = "nyairo.suitcase.bundle.v1"
SUITCASE_BUNDLE_VERSION = "0.8.0"
MIGRATION_LOSS_MANIFEST_SCHEMA = "nyairo.suitcase.loss_manifest.g4.v1"

SUITCASE_RECORDS_ONLY_SLICES: tuple[str, ...] = (
    "desk_tomorrow_note.json",
    "home_hearth.json",
    "v02_common_ground.json",
    "v02_recall_projection.json",
    "window_keepsakes.json",
    "longterm_gifts_lantern.json",
)
SUITCASE_RECORDS_AND_OBJECTS_EXTRA_SLICES: tuple[str, ...] = (
    "workbench_curio.json",
    "outing_bench_bookstore.json",
    "v02_crossmodal.json",
    "wardrobe_pocket.json",
    "v04_neighborhood.json",
    "v04_elastic_play.json",
    "v03_epistemic_encounters.json",
)
SUITCASE_PRACTICE_EXTRA_SLICES: tuple[str, ...] = (
    "longterm_room_habits.json",
    "v02_open_self_rules.json",
    "autonomy_rhythm.json",
    "somatic_comfort.json",
    "sensory_environment.json",
    "emotional_tension.json",
    "v03_ecology.json",
    "everyday_desires.json",
    "v08_emergent_echoes.json",
    "v08_composable_body.json",
)
SUITCASE_ALLOWED_SLICE_FILES: tuple[str, ...] = (
    SUITCASE_RECORDS_ONLY_SLICES
    + SUITCASE_RECORDS_AND_OBJECTS_EXTRA_SLICES
    + SUITCASE_PRACTICE_EXTRA_SLICES
)
SUITCASE_ALLOWED_SLICE_SET: frozenset[str] = frozenset(SUITCASE_ALLOWED_SLICE_FILES)

SUITCASE_ALLOWED_WORLD_FILES: tuple[str, ...] = (
    "v08_matter_space.json",
    "v08_open_affordance.json",
    "v08_microzone_ecology.json",
    "v04_neighborhood_state.json",
)
SUITCASE_ALLOWED_WORLD_FILE_SET: frozenset[str] = frozenset(SUITCASE_ALLOWED_WORLD_FILES)

SUITCASE_WORLD_SLICE_META: dict[str, str] = {
    "v08_matter_space.json": "通用物质空间、嵌套容器包与私密抽屉物理态",
    "v08_open_affordance.json": "通用物件交互痕迹与自建顺手小流程",
    "v08_microzone_ecology.json": "室内外微气候落座点、窗扇灯角与气味条件",
    "v04_neighborhood_state.json": "三环街区院落、盆栽耐受与手作借还状态",
}

SUITCASE_ALLOWED_WORLD_PACKAGE_FILES: tuple[str, ...] = (
    "manifest.json",
    "locations.json",
    "routes.json",
    "rules.json",
    "objects.json",
    "scene.json",
    "content.json",
)
SUITCASE_ALLOWED_WORLD_PACKAGE_FILE_SET: frozenset[str] = frozenset(
    SUITCASE_ALLOWED_WORLD_PACKAGE_FILES
)
SUITCASE_WORLD_PACKAGE_MAX_TOTAL_BYTES = 2 * 1024 * 1024

SUITCASE_SLICE_META: dict[str, tuple[str, str]] = {
    "desk_tomorrow_note.json": ("书桌明日便签与留存短笺", "records_only"),
    "home_hearth.json": ("居家炉火、重逢锚点与小约定", "records_only"),
    "v02_common_ground.json": ("双方共同认知与待澄清账本", "records_only"),
    "v02_recall_projection.json": ("四层回忆清晰度与遗忘墓碑水位", "records_only"),
    "window_keepsakes.json": ("窗边景致、自选锁屏与冰箱贴留念", "records_only"),
    "longterm_gifts_lantern.json": ("共同礼物轨迹与回忆双层灯盏", "records_only"),
    "workbench_curio.json": ("工作台创作托盘与小物履历", "records_and_objects"),
    "outing_bench_bookstore.json": ("外出长椅足迹与书店橱窗书签", "records_and_objects"),
    "v02_crossmodal.json": ("跨模态配色小样、试色板与读书标记", "records_and_objects"),
    "wardrobe_pocket.json": ("衣柜穿搭与口袋心愿小清单", "records_and_objects"),
    "v04_neighborhood.json": ("街区小院、手作与周边风物", "records_and_objects"),
    "v04_elastic_play.json": ("弹性玩耍探索与造物手艺谱系", "records_and_objects"),
    "v03_epistemic_encounters.json": ("有限视野三次遇见、递交材料与听说边界", "records_and_objects"),
    "longterm_room_habits.json": ("房间日常起居小习惯", "practice"),
    "v02_open_self_rules.json": ("自主生活小原则与自我约定", "practice"),
    "autonomy_rhythm.json": ("独处与相伴节奏偏好", "practice"),
    "somatic_comfort.json": ("身体冷暖、窗扇与桌边照拂习惯", "practice"),
    "sensory_environment.json": ("多通道环境感官微调偏好", "practice"),
    "emotional_tension.json": ("日常情绪张力与缓和节奏", "practice"),
    "v03_ecology.json": ("独立收支账本与生病调养习惯", "practice"),
    "everyday_desires.json": ("日常小心愿自然消长状态", "practice"),
    "v08_emergent_echoes.json": ("涌现回响、共同暗语与零债日常习惯", "practice"),
    "v08_composable_body.json": ("可组合身体节律包与日常照拂提示", "practice"),
}

FORBIDDEN_SUITCASE_SECRET_KEYS: frozenset[str] = frozenset({
    "api_key",
    "apikey",
    "authorization",
    "secret_key",
    "secret",
    "capability_token",
    "access_token",
    "refresh_token",
    "private_key",
    "password",
    "passwd",
    "bearer_token",
    "csrf_token",
    "auth_token",
    "client_secret",
    "webhook_secret",
    "session_token",
    "session_permit",
    "session_permits",
    "active_permit",
    "permit_token",
    "intimacy_permit",
    "widen_permit",
    # 2026-11-14 审计项6a：补齐裸键名黑名单。
    # "token" / "authorization" 为裸通用凭据键名；已核对仓库全部白名单切片写入方
    # （components/life/scripts 与 components/phone 各 view/bridge），无任何合法切片
    # 内容使用这两个裸键名；capability/operator_stop/life_request_budget 的 "token"
    # 均存于非切片文件，不受影响。api_key / secret 此前已在黑名单内。
    "token",
})

_HOST_FS_PATH_RE = re.compile(
    r"(?:"
    r"(?:^|(?<=[\s\"'=]))/(?:root|mnt|home|Users|tmp|var|opt|etc|private|srv)(?:/[^\s\"']*)?"
    r"|\b[A-Za-z]:[\\/][^\s\"']*"
    r"|\bfile:///[^\s\"']+"
    r"|\\\\[A-Za-z0-9_.-]+\\[^\s\"']*"
    r")"
)
_SECRET_VALUE_PREFIX_RE = re.compile(
    r"^(?:sk-[A-Za-z0-9_-]{12,}|Bearer\s+[A-Za-z0-9._~+/-]{12,}|ghp_[A-Za-z0-9]{16,})$"
)
# 2026-11-14 审计项6b：值级脱敏放宽——自由文本中内嵌的长令牌子串也要被打成红色占位符。
# _SECRET_VALUE_PREFIX_RE 只在整串恰好是令牌时命中（^$ 全串锚定），内嵌在句子里的
# 令牌会漏网。此正则仅供 _redact_host_paths_in_str 的脱敏调用点使用（已全仓核对：
# _SECRET_VALUE_PREFIX_RE / 本正则均无其他调用点），不会影响非脱敏场景。
_SECRET_VALUE_EMBEDDED_RE = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{16,}|Bearer\s+[A-Za-z0-9._~+/-]{16,}|ghp_[A-Za-z0-9]{16,})"
)

VERDICT_CONTINUED = "CONTINUED"
VERDICT_CONTINUED_WITH_LOSS = "CONTINUED_WITH_LOSS"
VERDICT_UNVERIFIED = "UNVERIFIED"
MIGRATION_VERDICTS = (
    VERDICT_CONTINUED,
    VERDICT_CONTINUED_WITH_LOSS,
    VERDICT_UNVERIFIED,
)

SKILL_STATUS_VALIDATED = "VALIDATED"
SKILL_STATUS_UNVALIDATED = "UNVALIDATED_AFTER_MIGRATION"
SKILL_STATUS_PENDING_REVERIFY = "PENDING_REVERIFY"
SKILL_STATUS_SUSPENDED_LOSS = "SUSPENDED_AFTER_TRIAL_LOSS"

REDACTED_PRIVATE_DRAFT_LABEL = "[未公开私人草稿]"

DEFAULT_CARRIER_FINGERPRINT: dict[str, Any] = {
    "model_id": "nyairo-default-carrier-v1",
    "carrier_name": "local_phone_host",
    "preview_available": True,
}

FORBIDDEN_FOREIGN_AND_IDENTITY_KEYS: frozenset[str] = frozenset({
    "actor_side",
    "has_subject_capability",
    "subject_id",
    "user_id",
    "uid",
    "owner_id",
    "writer_epoch",
    "is_admin",
    "bypass_quota",
    "force_wake",
    "mood_delta",
    "affection_score",
    "intimacy_score",
    "fatigue_override",
    "fake_backfill",
    "pretend_tired",
    "roleplay_fatigue",
})

QUEUE_BACKLOG_FAULT_THRESHOLD = 50
PENDING_REQUIRED_FAULT_THRESHOLD = 1000
STALE_UNACKED_SECONDS_THRESHOLD = 900


class V02RuntimeMigrationError(RuntimeError):
    """Structured domain error for v0.2 runtime governance & migration."""

    def __init__(
        self,
        status_code: int,
        reason_code: str,
        message: str = "",
        *,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(message or reason_code)
        self.status_code = int(status_code)
        self.reason_code = str(reason_code)
        self.message = str(message or reason_code)
        self.details: dict[str, Any] = dict(details) if details else {}


class V02ValidationError(V02RuntimeMigrationError):
    def __init__(
        self,
        reason_code: str,
        message: str = "",
        *,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(400, reason_code, message, details=details)


class V02PermissionError(V02RuntimeMigrationError):
    def __init__(
        self,
        reason_code: str,
        message: str = "",
        *,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(403, reason_code, message, details=details)


class V02ConflictError(V02RuntimeMigrationError):
    def __init__(
        self,
        reason_code: str,
        message: str = "",
        *,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(409, reason_code, message, details=details)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _resolve_now(now_iso: Optional[str]) -> tuple[datetime, str]:
    if now_iso is not None:
        parsed = _parse_iso(now_iso)
        if parsed is None:
            raise V02ValidationError("INVALID_FIELDS", f"Invalid ISO timestamp: {now_iso!r}")
        return parsed, parsed.isoformat(timespec="seconds")
    now_dt = datetime.now(DEFAULT_TZ).replace(microsecond=0)
    return now_dt, now_dt.isoformat(timespec="seconds")


def _mtime_iso(path: Path) -> Optional[str]:
    try:
        st = path.stat()
    except OSError:
        return None
    return datetime.fromtimestamp(st.st_mtime, tz=DEFAULT_TZ).isoformat(timespec="seconds")


def _read_json_safe(path: Path) -> tuple[bool, bool, dict[str, Any]]:
    """Return ``(exists, valid_json_object, mapping_payload)`` with zero writes."""
    if not path.is_file():
        return False, True, {}
    try:
        raw = path.read_text(encoding="utf-8")
        parsed = json.loads(raw)
    except (OSError, ValueError):
        return True, False, {}
    if not isinstance(parsed, Mapping):
        return True, False, {}
    return True, True, dict(parsed)


def _state_file_path(day_root: Path | str) -> Path:
    return Path(day_root) / STATE_DIRNAME / STATE_FILENAME


def _read_character_identity(day_root: Path) -> dict[str, str]:
    exists, ok, payload = _read_json_safe(day_root / STATE_DIRNAME / "character_pack_state.json")
    if exists and ok:
        pack = payload.get("active_pack") if isinstance(payload.get("active_pack"), Mapping) else payload
        if isinstance(pack, Mapping):
            name = str(pack.get("display_name") or "").strip() or "TA"
            pronouns = str(pack.get("pronouns") or "").strip() or "TA"
            return {"display_name": name, "pronouns": pronouns}
    return {"display_name": "TA", "pronouns": "TA"}


def _default_skills_catalog() -> dict[str, dict[str, Any]]:
    return {
        "markdown_local_checkpoint": {
            "skill_id": "markdown_local_checkpoint",
            "title_cn": "本地文稿断点保存与续写",
            "plain_desc_cn": "顺着上一次留下的书签和保存点继续改一小段文稿",
            "category": "universal_tool",
            "model_sensitive": False,
            "requires_preview": False,
            "status": SKILL_STATUS_VALIDATED,
            "personal_importance": "日常写东西最离不开的基础保存习惯",
            "importance_preserved": True,
            "verified_model_id": DEFAULT_CARRIER_FINGERPRINT["model_id"],
            "last_trial_note_cn": "",
        },
        "shared_promise_and_delete_ledger": {
            "skill_id": "shared_promise_and_delete_ledger",
            "title_cn": "已答应约定与撤权删除对账",
            "plain_desc_cn": "记住双方仍有效的约定，撤权或删除请求随时对账落实",
            "category": "governance_contract",
            "model_sensitive": False,
            "requires_preview": False,
            "status": SKILL_STATUS_VALIDATED,
            "personal_importance": "对共同相处负责的底线记录",
            "importance_preserved": True,
            "verified_model_id": DEFAULT_CARRIER_FINGERPRINT["model_id"],
            "last_trial_note_cn": "",
        },
        "nuanced_draft_stitching": {
            "skill_id": "nuanced_draft_stitching",
            "title_cn": "双版本长草稿差异拼合技法",
            "plain_desc_cn": "比对两版草稿的细微语气差别，挑出合适段落接在一起",
            "category": "personal_technique",
            "model_sensitive": True,
            "requires_preview": False,
            "status": SKILL_STATUS_VALIDATED,
            "personal_importance": "打磨私人长文稿时很看重的顺手做法",
            "importance_preserved": True,
            "verified_model_id": DEFAULT_CARRIER_FINGERPRINT["model_id"],
            "last_trial_note_cn": "",
        },
        "window_sketch_tone_tuning": {
            "skill_id": "window_sketch_tone_tuning",
            "title_cn": "窗边观察小册子分镜细调",
            "plain_desc_cn": "把零散观察笔记按节奏排成耐看的一页小册子",
            "category": "personal_technique",
            "model_sensitive": True,
            "requires_preview": False,
            "status": SKILL_STATUS_VALIDATED,
            "personal_importance": "整理日常观察收藏时的个人习惯",
            "importance_preserved": True,
            "verified_model_id": DEFAULT_CARRIER_FINGERPRINT["model_id"],
            "last_trial_note_cn": "",
        },
        "interactive_layout_live_preview": {
            "skill_id": "interactive_layout_live_preview",
            "title_cn": "交互小卡片实时预览排布",
            "plain_desc_cn": "边改卡片边在宿主预览窗确认实际排版效果",
            "category": "carrier_coupled_practice",
            "model_sensitive": False,
            "requires_preview": True,
            "status": SKILL_STATUS_VALIDATED,
            "personal_importance": "做可互动小作品时的辅助确认手段",
            "importance_preserved": True,
            "verified_model_id": DEFAULT_CARRIER_FINGERPRINT["model_id"],
            "last_trial_note_cn": "",
        },
    }


def _default_retirement_state() -> dict[str, Any]:
    return {
        "status": RETIREMENT_STATUS_ACTIVE,
        "prepared_at": None,
        "sealed_at": None,
        "reason_cn": "",
        "farewell_note_cn": "",
        "confirmation_phrase": None,
        "archive_manifest": None,
        "prepared_by": None,
        "sealed_by": None,
        "destructive_wipe_performed": False,
    }


def _default_proactive_outbound_state() -> dict[str, Any]:
    return {
        "enabled": False,
        "quiet_start_hm": PROACTIVE_DEFAULT_QUIET_START_HM,
        "quiet_end_hm": PROACTIVE_DEFAULT_QUIET_END_HM,
        "daily_cap": PROACTIVE_DEFAULT_DAILY_CAP,
        "min_cooldown_minutes": PROACTIVE_DEFAULT_COOLDOWN_MINUTES,
        "sent_today_count": 0,
        "sent_today_date": None,
        "last_sent_at": None,
        "next_msg_seq": 1,
        "pending_queue": [],
        "recent_log": [],
    }


def build_default_state() -> dict[str, Any]:
    return {
        "schema": VIEW_SCHEMA,
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": 0,
        "updated_at": None,
        "mode_config": {
            "mode": MODE_NORMAL,
            "budget_scale_pct": 100,
            "reason_cn": "",
            "dormant_since": None,
            "last_dormant_gap_seconds": 0,
            "last_dormant_recovery": None,
            "mode_changed_at": None,
        },
        "wake_router": {
            "hourly_wake_cap": DEFAULT_HOURLY_WAKE_CAP,
            "daily_wake_cap": DEFAULT_DAILY_WAKE_CAP,
            "coalesce_window_seconds": DEFAULT_COALESCE_WINDOW_SECONDS,
            "effective_wakes_total": 0,
            "control_wakes_total": 0,
            "control_lane_bypasses": 0,
            "foreground_chat_bypasses": 0,
            "coalesced_saved_wakes": 0,
            "quota_blocked_count": 0,
            "background_wake_timestamps": [],
            "active_family_windows": {},
            "recent_wake_attributions": [],
            "wake_tickets": [],
            "last_probe_result": None,
        },
        "carrier_migration": {
            "carrier_fingerprint": copy.deepcopy(DEFAULT_CARRIER_FINGERPRINT),
            "previous_carrier_fingerprint": None,
            "check_count": 0,
            "last_checked_at": None,
            "last_migration_verdict": VERDICT_CONTINUED,
            "skills": _default_skills_catalog(),
            "loss_report": [],
        },
        "suitcase_state": {
            "profile_mode": SUITCASE_SELECTIVE_PRACTICE,
            "keep_skill_ids": None,
            "leave_behind_note": "",
            "packed_at": None,
            "migrated_delivery_records": [],
        },
        "device_presence": {
            "active_device_id": None,
            "lease_epoch": 0,
            "leased_at": None,
            "lease_expires_at": None,
            "lease_seconds": DEFAULT_DEVICE_LEASE_SECONDS,
            "continuation_ref": None,
            "last_handoff": None,
            "handoff_history": [],
        },
        "retirement": _default_retirement_state(),
        "proactive_outbound": _default_proactive_outbound_state(),
        "workspace_audits": [],
        "operation_ledger": {},
    }


def _load_state_readonly(day_root: Path) -> tuple[bool, dict[str, Any]]:
    """Load persisted state without touching disk. Returns ``(state_exists, state_dict)``."""
    path = _state_file_path(day_root)
    exists, ok, raw = _read_json_safe(path)
    base = build_default_state()
    if not exists:
        return False, base
    if not ok:
        base["_corrupt_state_file"] = True
        return True, base

    base["revision"] = int(raw.get("revision") or 0)
    base["updated_at"] = raw.get("updated_at")
    for section in (
        "mode_config",
        "wake_router",
        "carrier_migration",
        "suitcase_state",
        "device_presence",
        "retirement",
        "proactive_outbound",
    ):
        src_sec = raw.get(section)
        if isinstance(src_sec, Mapping):
            if section == "carrier_migration":
                merged = copy.deepcopy(base[section])
                for k, v in src_sec.items():
                    if k == "skills" and isinstance(v, Mapping):
                        for sk_id, sk_val in v.items():
                            if isinstance(sk_val, Mapping):
                                merged["skills"][str(sk_id)] = dict(sk_val)
                    else:
                        merged[k] = copy.deepcopy(v)
                base["carrier_migration"] = merged
            else:
                merged = copy.deepcopy(base[section])
                merged.update(copy.deepcopy(dict(src_sec)))
                if section == "wake_router":
                    # 07 章 §二：重启载入近期票，容量截断防膨胀；过期裁决带时钟发生在
                    # 写路径与投影（载入路径没有 now，不猜测）。
                    raw_tickets = merged.get("wake_tickets")
                    if isinstance(raw_tickets, list):
                        merged["wake_tickets"] = [
                            dict(t) for t in raw_tickets[-MAX_WAKE_TICKETS:]
                            if isinstance(t, Mapping)
                        ]
                    else:
                        merged["wake_tickets"] = []
                elif section == "device_presence":
                    raw_hist = merged.get("handoff_history")
                    if isinstance(raw_hist, list):
                        merged["handoff_history"] = [
                            dict(h) for h in raw_hist[-MAX_DEVICE_HANDOFF_HISTORY:]
                            if isinstance(h, Mapping)
                        ]
                    else:
                        merged["handoff_history"] = []
                elif section == "proactive_outbound":
                    raw_pq = merged.get("pending_queue")
                    merged["pending_queue"] = [
                        dict(item) for item in (raw_pq[-PROACTIVE_MAX_PENDING_QUEUE:] if isinstance(raw_pq, list) else [])
                        if isinstance(item, Mapping)
                    ]
                    raw_rl = merged.get("recent_log")
                    merged["recent_log"] = [
                        dict(item) for item in (raw_rl[-PROACTIVE_MAX_RECENT_LOG:] if isinstance(raw_rl, list) else [])
                        if isinstance(item, Mapping)
                    ]
                base[section] = merged

    if isinstance(raw.get("workspace_audits"), list):
        base["workspace_audits"] = copy.deepcopy(raw["workspace_audits"][-MAX_WORKSPACE_AUDITS:])
    if isinstance(raw.get("operation_ledger"), Mapping):
        base["operation_ledger"] = copy.deepcopy(dict(raw["operation_ledger"]))
    return True, base


def _save_state_atomic(day_root: Path, state: Mapping[str, Any]) -> None:
    target = _state_file_path(day_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    clean_state = {k: v for k, v in state.items() if not str(k).startswith("_")}
    encoded = (json.dumps(clean_state, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(
        prefix=".v02_runtime_migration.",
        suffix=".tmp",
        dir=str(target.parent),
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
        os.replace(tmp_name, target)
    except Exception:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _effective_wake_caps(mode: str, budget_scale_pct: int, base_hourly: int, base_daily: int) -> tuple[int, int]:
    if mode == MODE_DORMANT:
        return 0, 0
    if mode == MODE_LOW_BUDGET or budget_scale_pct <= 25:
        eff_h = max(1, int(math.ceil(base_hourly * (budget_scale_pct / 100.0))))
        eff_d = max(2, int(math.ceil(base_daily * (budget_scale_pct / 100.0))))
        return min(base_hourly, eff_h), min(base_daily, eff_d)
    return int(base_hourly), int(base_daily)


def _count_recent_background_wakes(timestamps: Sequence[str], now_dt: datetime) -> tuple[int, int]:
    hour_cutoff = now_dt - timedelta(seconds=3600)
    local_now = now_dt.astimezone(DEFAULT_TZ)
    hour_count = 0
    day_count = 0
    for ts in timestamps:
        dt = _parse_iso(ts)
        if dt is None:
            continue
        if hour_cutoff <= dt <= now_dt + timedelta(seconds=1):
            hour_count += 1
        if dt.astimezone(DEFAULT_TZ).date() == local_now.date():
            day_count += 1
    return hour_count, day_count


class SemanticWakeCoalescer:
    """In-memory / state-backed semantic wake coalescing & backpressure router."""

    def __init__(
        self,
        *,
        hourly_wake_cap: int = DEFAULT_HOURLY_WAKE_CAP,
        daily_wake_cap: int = DEFAULT_DAILY_WAKE_CAP,
        coalesce_window_seconds: int = DEFAULT_COALESCE_WINDOW_SECONDS,
    ) -> None:
        self._state = build_default_state()
        self._state["wake_router"]["hourly_wake_cap"] = int(hourly_wake_cap)
        self._state["wake_router"]["daily_wake_cap"] = int(daily_wake_cap)
        self._state["wake_router"]["coalesce_window_seconds"] = int(coalesce_window_seconds)

    @property
    def state(self) -> dict[str, Any]:
        return self._state

    def route_event(
        self,
        *,
        family: str,
        summary_cn: str,
        is_control: bool = False,
        lane: str = "background",
        now_iso: Optional[str] = None,
    ) -> dict[str, Any]:
        now_dt, resolved_iso = _resolve_now(now_iso)
        return _route_wake_event_into_state(
            self._state,
            family=family,
            summary_cn=summary_cn,
            is_control=is_control,
            lane=lane,
            now_dt=now_dt,
            now_iso=resolved_iso,
        )

    def route_batch(
        self,
        events: Sequence[Mapping[str, Any]],
        *,
        now_iso: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for ev in events:
            ev_now = ev.get("now_iso", now_iso)
            results.append(
                self.route_event(
                    family=str(ev.get("family") or "ambient_generic"),
                    summary_cn=str(ev.get("summary_cn") or "环境微弱动静"),
                    is_control=bool(ev.get("is_control", False)),
                    lane=str(ev.get("lane") or "background"),
                    now_iso=ev_now,
                )
            )
        return results


def _discover_private_draft_candidates_readonly(day_root: Optional[Path]) -> list[dict[str, Any]]:
    """Read optional local workbench/desk hints (workbench_tray.json + fallback workbench_curio.json) with zero writes."""
    if day_root is None:
        return []
    candidates: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for rel_name in ("workbench_tray.json", "workbench_curio.json", "private_drafts.json"):
        exists, ok, data = _read_json_safe(day_root / STATE_DIRNAME / rel_name)
        if not exists or not ok:
            continue
        tray_obj = data.get("tray") if isinstance(data.get("tray"), Mapping) else None
        if isinstance(tray_obj, Mapping):
            work_ref = tray_obj.get("work")
            if isinstance(work_ref, Mapping) and not bool(work_ref.get("shared", False)):
                w_id = str(work_ref.get("artifact_id") or work_ref.get("work_id") or work_ref.get("id") or "tray_work").strip()
                w_title = str(work_ref.get("title") or w_id).strip()
                if w_title and w_id not in seen_ids:
                    seen_ids.add(w_id)
                    candidates.append({
                        "candidate_id": w_id,
                        "title": w_title,
                        "source": rel_name,
                        "visibility": "private_draft",
                        "is_private_draft": True,
                    })
        items = data.get("items") or data.get("drafts") or data.get("tray_items")
        if isinstance(items, list):
            for idx, item in enumerate(items[:3]):
                if isinstance(item, Mapping) and not bool(item.get("shared", False)):
                    title = str(item.get("title") or "").strip()
                    cid = str(item.get("id") or item.get("artifact_id") or f"local_priv_{idx}").strip()
                    if title and cid not in seen_ids:
                        seen_ids.add(cid)
                        candidates.append({
                            "candidate_id": cid,
                            "title": title,
                            "source": rel_name,
                            "visibility": "private_draft",
                            "is_private_draft": True,
                        })
    return candidates


def _new_wake_ticket(
    *,
    ticket_seq: int,
    coalesce_key: str,
    priority: int,
    status: str,
    now_iso: str,
    est_tokens: Optional[int] = None,
    lease_until: Optional[str] = None,
    bypass: Optional[str] = None,
    summary_cn: str = "",
) -> dict[str, Any]:
    """07 章 §二 WakeTicket：ticket_id/priority/coalesce_key/folded/est_tokens/lease/status."""
    safe_est = est_tokens if isinstance(est_tokens, int) and not isinstance(est_tokens, bool) \
        and est_tokens >= 0 else DEFAULT_WAKE_TICKET_EST_TOKENS
    return {
        "schema": WAKE_TICKET_SCHEMA,
        "ticket_id": f"wt-{ticket_seq:04d}",
        "priority": int(priority),
        "coalesce_key": str(coalesce_key),
        "status": status if status in WAKE_TICKET_STATUSES else WAKE_TICKET_STATUS_OPEN,
        "created_at": now_iso,
        "lease_until": lease_until,
        "fired_at": now_iso if status == WAKE_TICKET_STATUS_FIRED else None,
        "est_tokens": int(safe_est),
        "folded_count": 0,
        "fold_trace": [],
        "bypass": bypass,
        "summary_cn": str(summary_cn or "")[:200],
    }


def _wake_ticket_fold_target(
    tickets: list[dict[str, Any]],
    coalesce_key: str,
    now_dt: datetime,
) -> Optional[dict[str, Any]]:
    """Newest same-key ticket whose fold lease is still valid (open or already fired)."""
    for ticket in reversed(tickets):
        if not isinstance(ticket, dict) or str(ticket.get("coalesce_key")) != str(coalesce_key):
            continue
        if str(ticket.get("status")) not in (WAKE_TICKET_STATUS_OPEN, WAKE_TICKET_STATUS_FIRED):
            continue
        lease_dt = _parse_iso(ticket.get("lease_until"))
        if lease_dt is not None and now_dt < lease_dt:
            return ticket
    return None


def _record_wake_ticket_fold(
    ticket: dict[str, Any],
    *,
    fold_kind: str,
    now_iso: str,
    summary_cn: str = "",
) -> None:
    ticket["folded_count"] = int(ticket.get("folded_count") or 0) + 1
    trace = ticket.setdefault("fold_trace", [])
    trace.append({
        "fold_kind": str(fold_kind),
        "at": now_iso,
        "summary_cn": str(summary_cn or "")[:200],
    })
    ticket["fold_trace"] = trace[-MAX_TICKET_FOLD_TRACE:]


def _finalize_expired_wake_tickets_at(
    tickets: list[dict[str, Any]],
    now_dt: datetime,
) -> int:
    finalized = 0
    for ticket in tickets:
        if not isinstance(ticket, dict) or str(ticket.get("status")) != WAKE_TICKET_STATUS_OPEN:
            continue
        lease_dt = _parse_iso(ticket.get("lease_until"))
        if lease_dt is not None and now_dt >= lease_dt:
            ticket["status"] = WAKE_TICKET_STATUS_EXPIRED
            ticket["expired_at"] = lease_dt.isoformat(timespec="seconds")
            finalized += 1
    return finalized


def _trim_wake_tickets(tickets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return tickets[-MAX_WAKE_TICKETS:]


def _wake_tickets_summary(tickets: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts = {status: 0 for status in WAKE_TICKET_STATUSES}
    folded_total = 0
    est_total = 0
    for ticket in tickets:
        status = str(ticket.get("status") or "")
        if status in counts:
            counts[status] += 1
        folded_total += int(ticket.get("folded_count") or 0)
        est_total += int(ticket.get("est_tokens") or 0)
    return {
        "total": len(tickets),
        "by_status": counts,
        "folded_total": folded_total,
        "est_tokens_total": est_total,
    }


def _append_wake_ticket(
    router: dict[str, Any],
    *,
    coalesce_key: str,
    priority: int,
    status: str,
    now_dt: datetime,
    now_iso: str,
    est_tokens: Optional[int] = None,
    lease_until: Optional[str] = None,
    bypass: Optional[str] = None,
    summary_cn: str = "",
) -> dict[str, Any]:
    """Create one wake ticket in the state's bounded ledger (07 章 §二 持久票账)."""
    seq = int(router.get("wake_ticket_seq") or 0) + 1
    router["wake_ticket_seq"] = seq
    tickets = router.setdefault("wake_tickets", [])
    _finalize_expired_wake_tickets_at(tickets, now_dt)
    ticket = _new_wake_ticket(
        ticket_seq=seq,
        coalesce_key=coalesce_key,
        priority=priority,
        status=status,
        now_iso=now_iso,
        est_tokens=est_tokens,
        lease_until=lease_until,
        bypass=bypass,
        summary_cn=summary_cn,
    )
    tickets.append(ticket)
    router["wake_tickets"] = _trim_wake_tickets(tickets)
    return ticket


def _route_wake_event_into_state(
    state: dict[str, Any],
    *,
    family: str,
    summary_cn: str,
    is_control: bool,
    lane: str = "background",
    now_dt: datetime,
    now_iso: str,
    day_root: Optional[Path] = None,
    est_tokens: Optional[int] = None,
) -> dict[str, Any]:
    router = state["wake_router"]
    mode_cfg = state["mode_config"]
    mode = str(mode_cfg.get("mode") or MODE_NORMAL)
    budget_pct = int(mode_cfg.get("budget_scale_pct") or 100)

    clean_family = family.strip()
    clean_summary = summary_cn.strip()
    window_sec = int(router.get("coalesce_window_seconds") or DEFAULT_COALESCE_WINDOW_SECONDS)
    base_hourly = int(router.get("hourly_wake_cap") or DEFAULT_HOURLY_WAKE_CAP)
    base_daily = int(router.get("daily_wake_cap") or DEFAULT_DAILY_WAKE_CAP)
    eff_hourly, eff_daily = _effective_wake_caps(mode, budget_pct, base_hourly, base_daily)

    active_windows: dict[str, dict[str, Any]] = router.setdefault("active_family_windows", {})
    expired_keys: list[str] = []
    for fam_key, win in active_windows.items():
        exp_dt = _parse_iso(win.get("expires_at"))
        if exp_dt is not None and now_dt >= exp_dt:
            expired_keys.append(fam_key)
    for fam_key in expired_keys:
        active_windows.pop(fam_key, None)

    if is_control or lane == "control":
        router["control_lane_bypasses"] = int(router.get("control_lane_bypasses") or 0) + 1
        router["control_wakes_total"] = int(router.get("control_wakes_total") or 0) + 1
        router["effective_wakes_total"] = int(router.get("effective_wakes_total") or 0) + 1
        wake_seq = int(router["effective_wakes_total"])
        wake_id = f"wake-ctrl-{wake_seq:04d}"
        wake_reason = f"control_immediate:{clean_family}"

        attr_entry = {
            "wake_id": wake_id,
            "occurred_at": now_iso,
            "lane": "control",
            "is_control": True,
            "family": clean_family,
            "wake_reason": wake_reason,
            "summary_cn": clean_summary,
            "budget_mode": mode,
            "debounced": False,
            "llm_calls": 0,
        }
        router.setdefault("recent_wake_attributions", []).append(attr_entry)
        router["recent_wake_attributions"] = router["recent_wake_attributions"][-MAX_WAKE_ATTRIBUTIONS:]

        # 07 章 §二：control 旁路票照记，标注 bypass（零防抖直通，无合并窗租约）。
        _append_wake_ticket(
            router,
            coalesce_key=clean_family,
            priority=WAKE_TICKET_PRIORITY_CONTROL,
            status=WAKE_TICKET_STATUS_FIRED,
            now_dt=now_dt,
            now_iso=now_iso,
            est_tokens=est_tokens,
            bypass=WAKE_TICKET_BYPASS_CONTROL,
            summary_cn=clean_summary,
        )

        _append_auto_workspace_audit(
            state,
            wake_reason=wake_reason,
            family=clean_family,
            summary_cn=clean_summary,
            is_control=True,
            now_iso=now_iso,
            day_root=day_root,
        )

        probe_result = {
            "family": clean_family,
            "summary_cn": clean_summary,
            "is_control": True,
            "lane": "control",
            "effective_wake": True,
            "coalesced": False,
            "debounced": False,
            "quota_blocked": False,
            "wake_disposition": "CONTROL_IMMEDIATE_BYPASS",
            "wake_reason": wake_reason,
            "wake_id": wake_id,
            "occurred_at": now_iso,
        }
        router["last_probe_result"] = probe_result
        return probe_result

    if lane == "foreground_chat":
        router["foreground_chat_bypasses"] = int(router.get("foreground_chat_bypasses") or 0) + 1
        router["effective_wakes_total"] = int(router.get("effective_wakes_total") or 0) + 1
        wake_seq = int(router["effective_wakes_total"])
        wake_id = f"wake-fg-{wake_seq:04d}"
        wake_reason = f"foreground_chat:{clean_family}"
        attr_entry = {
            "wake_id": wake_id,
            "occurred_at": now_iso,
            "lane": "foreground_chat",
            "is_control": False,
            "family": clean_family,
            "wake_reason": wake_reason,
            "summary_cn": clean_summary,
            "budget_mode": mode,
            "debounced": False,
            "llm_calls": 0,
        }
        router.setdefault("recent_wake_attributions", []).append(attr_entry)
        router["recent_wake_attributions"] = router["recent_wake_attributions"][-MAX_WAKE_ATTRIBUTIONS:]
        # 07 章 §二：foreground 旁路票照记，标注 bypass。
        _append_wake_ticket(
            router,
            coalesce_key=clean_family,
            priority=WAKE_TICKET_PRIORITY_FOREGROUND,
            status=WAKE_TICKET_STATUS_FIRED,
            now_dt=now_dt,
            now_iso=now_iso,
            est_tokens=est_tokens,
            bypass=WAKE_TICKET_BYPASS_FOREGROUND,
            summary_cn=clean_summary,
        )
        probe_result = {
            "family": clean_family,
            "summary_cn": clean_summary,
            "is_control": False,
            "lane": "foreground_chat",
            "effective_wake": True,
            "coalesced": False,
            "debounced": False,
            "quota_blocked": False,
            "wake_disposition": "FOREGROUND_GUARANTEED",
            "wake_reason": wake_reason,
            "wake_id": wake_id,
            "occurred_at": now_iso,
        }
        router["last_probe_result"] = probe_result
        return probe_result

    if mode == MODE_DORMANT:
        probe_result = {
            "family": clean_family,
            "summary_cn": clean_summary,
            "is_control": False,
            "lane": "background",
            "effective_wake": False,
            "coalesced": False,
            "debounced": True,
            "quota_blocked": False,
            "wake_disposition": "SUPPRESSED_BY_DORMANT_MODE",
            "wake_reason": None,
            "wake_id": None,
            "occurred_at": now_iso,
        }
        router["last_probe_result"] = probe_result
        return probe_result

    if clean_family.startswith("hidden_") or clean_family == "heartbeat":
        probe_result = {
            "family": clean_family,
            "summary_cn": clean_summary,
            "is_control": False,
            "lane": "background",
            "effective_wake": False,
            "coalesced": False,
            "debounced": True,
            "quota_blocked": False,
            "wake_disposition": "ISOLATED_NON_COGNITIVE_SIGNAL",
            "wake_reason": None,
            "wake_id": None,
            "occurred_at": now_iso,
        }
        router["last_probe_result"] = probe_result
        return probe_result

    existing_win = active_windows.get(clean_family)
    if existing_win is not None:
        opened_dt = _parse_iso(existing_win.get("opened_at"))
        exp_dt = _parse_iso(existing_win.get("expires_at"))
        if exp_dt is not None and (opened_dt is None or now_dt >= opened_dt) and now_dt < exp_dt:
            router["coalesced_saved_wakes"] = int(router.get("coalesced_saved_wakes") or 0) + 1
            existing_win["coalesced_count"] = int(existing_win.get("coalesced_count") or 0) + 1
            existing_win["latest_summary_cn"] = clean_summary
            existing_win["last_merged_at"] = now_iso
            # 07 章 §二：同 coalesce_key 合并 -> 折进既有票（folded+1），不新建票。
            fold_target = _wake_ticket_fold_target(
                router.setdefault("wake_tickets", []), clean_family, now_dt)
            if fold_target is not None:
                _record_wake_ticket_fold(
                    fold_target,
                    fold_kind=WAKE_TICKET_FOLD_KIND_COALESCE,
                    now_iso=now_iso,
                    summary_cn=clean_summary,
                )
            probe_result = {
                "family": clean_family,
                "summary_cn": clean_summary,
                "is_control": False,
                "lane": "background",
                "effective_wake": False,
                "coalesced": True,
                "debounced": True,
                "quota_blocked": False,
                "wake_disposition": "COALESCED_SAME_FAMILY",
                "wake_reason": existing_win.get("wake_reason"),
                "wake_id": existing_win.get("wake_id"),
                "occurred_at": now_iso,
            }
            router["last_probe_result"] = probe_result
            return probe_result

    bg_timestamps: list[str] = router.setdefault("background_wake_timestamps", [])
    wakes_hour, wakes_day = _count_recent_background_wakes(bg_timestamps, now_dt)
    if wakes_hour >= eff_hourly or wakes_day >= eff_daily:
        router["quota_blocked_count"] = int(router.get("quota_blocked_count") or 0) + 1
        # 07 章 §二：背压超限不直接丢弃——同类票可折则折（folded+1）；无同类票则把这次
        # 延期本身记成一张 open 延期票（带合并租约），断电恢复后对账可见"延期未消费"。
        deferred_ticket: Optional[dict[str, Any]] = _wake_ticket_fold_target(
            router.setdefault("wake_tickets", []), clean_family, now_dt)
        if deferred_ticket is not None:
            _record_wake_ticket_fold(
                deferred_ticket,
                fold_kind=WAKE_TICKET_FOLD_KIND_QUOTA,
                now_iso=now_iso,
                summary_cn=clean_summary,
            )
        else:
            deferred_ticket = _append_wake_ticket(
                router,
                coalesce_key=clean_family,
                priority=WAKE_TICKET_PRIORITY_BACKGROUND,
                status=WAKE_TICKET_STATUS_OPEN,
                now_dt=now_dt,
                now_iso=now_iso,
                est_tokens=est_tokens,
                lease_until=(now_dt + timedelta(seconds=window_sec)).isoformat(timespec="seconds"),
                summary_cn=clean_summary,
            )
        probe_result = {
            "family": clean_family,
            "summary_cn": clean_summary,
            "is_control": False,
            "lane": "background",
            "effective_wake": False,
            "coalesced": False,
            "debounced": True,
            "quota_blocked": True,
            "wake_disposition": "QUOTA_BACKPRESSURE_DEFERRED",
            "wake_reason": None,
            "wake_id": None,
            "occurred_at": now_iso,
        }
        router["last_probe_result"] = probe_result
        return probe_result

    router["effective_wakes_total"] = int(router.get("effective_wakes_total") or 0) + 1
    wake_seq = int(router["effective_wakes_total"])
    wake_id = f"wake-bg-{wake_seq:04d}"
    wake_reason = f"semantic_opportunity:{clean_family}"
    expires_iso = (now_dt + timedelta(seconds=window_sec)).isoformat(timespec="seconds")

    bg_timestamps.append(now_iso)
    router["background_wake_timestamps"] = bg_timestamps[-MAX_WAKE_TIMESTAMPS:]

    active_windows[clean_family] = {
        "family": clean_family,
        "wake_id": wake_id,
        "wake_reason": wake_reason,
        "opened_at": now_iso,
        "expires_at": expires_iso,
        "latest_summary_cn": clean_summary,
        "last_merged_at": now_iso,
        "coalesced_count": 0,
    }

    attr_entry = {
        "wake_id": wake_id,
        "occurred_at": now_iso,
        "lane": "background",
        "is_control": False,
        "family": clean_family,
        "wake_reason": wake_reason,
        "summary_cn": clean_summary,
        "budget_mode": mode,
        "debounced": True,
        "llm_calls": 0,
    }
    router.setdefault("recent_wake_attributions", []).append(attr_entry)
    router["recent_wake_attributions"] = router["recent_wake_attributions"][-MAX_WAKE_ATTRIBUTIONS:]

    # 07 章 §二：有效后台唤醒落一张已消费票（fired），合并租约 = 语义合并窗；
    # 窗内同类事件折进这张票（folded+1），窗外即新事实新票。
    _append_wake_ticket(
        router,
        coalesce_key=clean_family,
        priority=WAKE_TICKET_PRIORITY_BACKGROUND,
        status=WAKE_TICKET_STATUS_FIRED,
        now_dt=now_dt,
        now_iso=now_iso,
        est_tokens=est_tokens,
        lease_until=expires_iso,
        summary_cn=clean_summary,
    )

    _append_auto_workspace_audit(
        state,
        wake_reason=wake_reason,
        family=clean_family,
        summary_cn=clean_summary,
        is_control=False,
        now_iso=now_iso,
        day_root=day_root,
    )

    probe_result = {
        "family": clean_family,
        "summary_cn": clean_summary,
        "is_control": False,
        "lane": "background",
        "effective_wake": True,
        "coalesced": False,
        "debounced": True,
        "quota_blocked": False,
        "wake_disposition": "EFFECTIVE_BACKGROUND_WAKE",
        "wake_reason": wake_reason,
        "wake_id": wake_id,
        "occurred_at": now_iso,
    }
    router["last_probe_result"] = probe_result
    return probe_result


def _append_auto_workspace_audit(
    state: dict[str, Any],
    *,
    wake_reason: str,
    family: str,
    summary_cn: str,
    is_control: bool,
    now_iso: str,
    day_root: Optional[Path] = None,
) -> None:
    mode_cfg = state["mode_config"]
    mode = str(mode_cfg.get("mode") or MODE_NORMAL)
    budget_pct = int(mode_cfg.get("budget_scale_pct") or 100)
    audits: list[dict[str, Any]] = state.setdefault("workspace_audits", [])
    audit_seq = len(audits) + 1

    admitted: list[dict[str, Any]] = [
        {
            "candidate_id": f"cand-{family}-{audit_seq}",
            "source": "control_lane" if is_control else "coalesced_world_signal",
            "title": summary_cn,
            "visibility": "shared",
            "is_private_draft": False,
        }
    ]
    trimmed: list[dict[str, Any]] = []

    local_privs = _discover_private_draft_candidates_readonly(day_root)
    if local_privs:
        if mode == MODE_LOW_BUDGET or budget_pct <= 25:
            first_priv = dict(local_privs[0])
            admitted.append(first_priv)
            for extra in local_privs[1:]:
                trimmed.append({
                    "candidate_id": extra["candidate_id"],
                    "source": extra["source"],
                    "title": extra["title"],
                    "visibility": "private_draft",
                    "is_private_draft": True,
                    "reason_code": "LOW_BUDGET_LOCAL_SCOPE_ONLY",
                    "reason_cn": "省电低预算节奏下仅保留当前断点手稿，暂不铺开其余支线草稿",
                })
        else:
            for priv in local_privs[:2]:
                admitted.append(dict(priv))

    if not is_control and (mode == MODE_LOW_BUDGET or budget_pct <= 25):
        trimmed.append({
            "candidate_id": f"cand-roam-{audit_seq}",
            "source": "ambient_roaming",
            "title": "外出漫游探查与新开大项目候选",
            "visibility": "shared",
            "is_private_draft": False,
            "reason_code": "PAUSED_BY_LOW_BUDGET_CONSERVATION",
            "reason_cn": "预算缩减期间暂停漫游探查与新开大坑，只保留在途局部小修订",
        })

    visible_total = len(admitted) + len(trimmed)
    audit_entry = {
        "audit_id": f"wsa-{audit_seq:04d}",
        "recorded_at": now_iso,
        "wake_reason": wake_reason,
        "budget_mode": mode,
        "budget_scale_pct": budget_pct,
        "visible_candidate_count": visible_total,
        "entered_workspace_count": len(admitted),
        "trimmed_count": len(trimmed),
        "selection_rule": (
            "control_immediate_priority"
            if is_control
            else ("low_budget_checkpoint_first" if (mode == MODE_LOW_BUDGET or budget_pct <= 25) else "semantic_dedup_bounded_workspace")
        ),
        "admitted_candidates": admitted,
        "trimmed_reasons": trimmed,
    }
    audits.append(audit_entry)
    state["workspace_audits"] = audits[-MAX_WORKSPACE_AUDITS:]


def _redact_workspace_audits_for_perspective(
    audits: Sequence[Mapping[str, Any]],
    *,
    has_subject_capability: bool,
) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    for raw_audit in audits:
        entry = copy.deepcopy(dict(raw_audit))
        redacted_count = 0

        if not has_subject_capability:
            priv_titles = entry.pop("private_draft_titles", None)
            if isinstance(priv_titles, list):
                redacted_count += len(priv_titles)

            for list_key in ("admitted_candidates", "trimmed_reasons", "trimmed_candidates"):
                items = entry.get(list_key)
                if not isinstance(items, list):
                    continue
                sanitized_items: list[dict[str, Any]] = []
                for item in items:
                    if not isinstance(item, Mapping):
                        sanitized_items.append({"title": str(item), "title_redacted": False})
                        continue
                    copied = dict(item)
                    is_priv = bool(
                        copied.get("is_private_draft")
                        or copied.get("visibility") in ("private", "private_draft")
                        or (copied.get("shared") is False)
                    )
                    if is_priv:
                        copied["title"] = REDACTED_PRIVATE_DRAFT_LABEL
                        copied["title_redacted"] = True
                        copied.pop("private_title", None)
                        copied.pop("raw_draft_title", None)
                        redacted_count += 1
                    else:
                        copied["title_redacted"] = False
                    sanitized_items.append(copied)
                entry[list_key] = sanitized_items

            entry["private_draft_redacted_count"] = redacted_count
        else:
            entry["private_draft_redacted_count"] = 0

        projected.append(entry)
    return projected


GAP12_STALLED_TICK_AGE_SECONDS = 225
BUDGET_HOLD_STATUS_TOKENS = frozenset({
    "HELD_BY_BUDGET",
    "BUDGET_HELD",
    "BUDGET_EXHAUSTED",
    "REQUEST_BUDGET_UTC_DAY_LIMIT",
    "REQUEST_BUDGET_RUN_WINDOW_LIMIT",
})


def evaluate_gap12_stalled(*, runnable_count: int, held_by_budget: bool, tick_age_s: float) -> bool:
    """GAP-12 authoritative stall predicate: runnable_count > 0 and not held_by_budget and tick_age_s > 225."""
    return bool(
        int(runnable_count) > 0
        and (not bool(held_by_budget))
        and float(tick_age_s) > float(GAP12_STALLED_TICK_AGE_SECONDS)
    )


def _extract_budget_hold_flag(data: Mapping[str, Any]) -> bool:
    if data.get("held_by_budget") is True or data.get("budget_held") is True:
        return True
    for field in ("status", "state", "hold_reason", "reason_code", "reason"):
        token = str(data.get(field) or "").strip().upper()
        if token in BUDGET_HOLD_STATUS_TOKENS:
            return True
    return False


def _extract_tick_age_seconds(data: Mapping[str, Any], *, now_dt: datetime) -> Optional[float]:
    for num_key in ("tick_age_s", "tick_age_seconds"):
        raw_val = data.get(num_key)
        if isinstance(raw_val, (int, float)) and not isinstance(raw_val, bool):
            return max(0.0, float(raw_val))
    for ts_key in ("last_tick_at", "heartbeat_at"):
        ts_dt = _parse_iso(data.get(ts_key))
        if ts_dt is not None:
            return max(0.0, (now_dt - ts_dt).total_seconds())
    return None


def _coerce_signal_int(raw: Any) -> int:
    """Coerce an externally injected health-signal value to int; bool or non-numeric garbage → 0."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return 0
    try:
        return int(raw)
    except (ValueError, OverflowError):
        return 0


def inspect_day_root_health_signals(day_root: Path, *, now_dt: datetime) -> dict[str, Any]:
    """Read-only inspection of ``instance.json``, ``ready.json``, and queue backlog."""
    fault_reasons: list[dict[str, str]] = []
    signals: dict[str, Any] = {
        "instance_present": False,
        "ready_present": False,
        "queue_backlog_count": 0,
        "runnable_count": 0,
        "held_by_budget": False,
        "tick_age_s": 0.0,
        "gap12_stalled": False,
        "pending_required_count": 0,
        "dead_letter_count": 0,
        "oldest_unacked_seconds": 0,
        "resident_ticks_recorded": 0,
    }

    held_by_budget = False
    explicit_runnable: Optional[int] = None
    explicit_tick_age: Optional[float] = None

    inst_exists, inst_ok, inst_data = _read_json_safe(day_root / "instance.json")
    signals["instance_present"] = inst_exists
    if inst_exists:
        if not inst_ok:
            fault_reasons.append({
                "code": "CORRUPT_INSTANCE_JSON",
                "reason_cn": "实例绑定文件 instance.json 损坏或无法解析",
            })
        else:
            if _extract_budget_hold_flag(inst_data):
                held_by_budget = True
            rc_inst = inst_data.get("runnable_count")
            if isinstance(rc_inst, int) and not isinstance(rc_inst, bool):
                explicit_runnable = max(explicit_runnable or 0, rc_inst)
            ta_inst = _extract_tick_age_seconds(inst_data, now_dt=now_dt)
            if ta_inst is not None:
                explicit_tick_age = ta_inst if explicit_tick_age is None else max(explicit_tick_age, ta_inst)
            inst_status = str(inst_data.get("status") or inst_data.get("health") or "").strip().upper()
            if inst_status not in BUDGET_HOLD_STATUS_TOKENS and (
                inst_status in {"STALLED", "DEGRADED", "FAILED", "ERROR", "DEAD", "CORRUPTED"}
                or inst_data.get("stalled") is True
            ):
                fault_reasons.append({
                    "code": f"INSTANCE_STATUS_{inst_status or 'STALLED'}",
                    "reason_cn": str(inst_data.get("error") or inst_data.get("reason") or "实例状态报告异常或卡死"),
                })

    ready_exists, ready_ok, ready_data = _read_json_safe(day_root / "ready.json")
    signals["ready_present"] = ready_exists
    pending_heartbeat_fault: Optional[dict[str, str]] = None
    if ready_exists:
        if not ready_ok:
            fault_reasons.append({
                "code": "CORRUPT_READY_JSON",
                "reason_cn": "就绪探针文件 ready.json 损坏或无法解析",
            })
        else:
            if _extract_budget_hold_flag(ready_data):
                held_by_budget = True
            rc_ready = ready_data.get("runnable_count")
            if isinstance(rc_ready, int) and not isinstance(rc_ready, bool):
                explicit_runnable = max(explicit_runnable or 0, rc_ready)
            ta_ready = _extract_tick_age_seconds(ready_data, now_dt=now_dt)
            if ta_ready is not None:
                explicit_tick_age = ta_ready if explicit_tick_age is None else max(explicit_tick_age, ta_ready)
            ready_status = str(ready_data.get("status") or ready_data.get("state") or "").strip().upper()
            if ready_status not in BUDGET_HOLD_STATUS_TOKENS and (
                ready_data.get("ready") is False
                or ready_data.get("stalled") is True
                or ready_data.get("heartbeat_stale") is True
                or ready_status in {"STALLED", "DEGRADED", "FAILED", "ERROR", "UNHEALTHY", "DEAD", "TIMEOUT"}
            ):
                fault_reasons.append({
                    "code": f"READY_UNHEALTHY_{ready_status or 'NOT_READY'}",
                    "reason_cn": str(ready_data.get("error") or ready_data.get("reason") or "宿主就绪心跳报告未就绪或卡住"),
                })
            if ready_data.get("expected_running") is True and ready_data.get("worker_alive") is False:
                fault_reasons.append({
                    "code": "WORKER_PROCESS_DEAD",
                    "reason_cn": "后台工作进程在预期运行期间意外退出",
                })
            hb_timeout = ready_data.get("heartbeat_timeout_seconds")
            hb_at = _parse_iso(ready_data.get("heartbeat_at"))
            if isinstance(hb_timeout, (int, float)) and hb_timeout > 0 and hb_at is not None:
                age_sec = (now_dt - hb_at).total_seconds()
                if age_sec > float(hb_timeout):
                    pending_heartbeat_fault = {
                        "code": "HEARTBEAT_TIMEOUT_STALLED",
                        "reason_cn": f"心跳已超时 {int(age_sec)} 秒（上限 {int(hb_timeout)} 秒），进程疑似卡死",
                    }

    backlog_candidates = (
        day_root / "queue.json",
        day_root / STATE_DIRNAME / "queue_state.json",
        day_root / "one-day" / "outbox_backlog.json",
        day_root / "outbox.json",
        day_root / "one-day" / "resident_summary.json",
    )
    max_backlog = _coerce_signal_int(ready_data.get("queue_backlog") or ready_data.get("backlog_count")) if ready_ok else 0
    max_pending_req = _coerce_signal_int(ready_data.get("pending_required_count") or ready_data.get("pending_required")) if ready_ok else 0
    max_dead_letter = _coerce_signal_int(ready_data.get("dead_letter_count")) if ready_ok else 0
    max_unacked_age = _coerce_signal_int(ready_data.get("oldest_unacked_seconds")) if ready_ok else 0

    for q_path in backlog_candidates:
        q_exists, q_ok, q_data = _read_json_safe(q_path)
        if not q_exists:
            continue
        if not q_ok:
            fault_reasons.append({
                "code": f"CORRUPT_QUEUE_SIGNAL_{q_path.name.upper().replace('.', '_')}",
                "reason_cn": f"队列状态文件 {q_path.name} 损坏，无法确认消费进度",
            })
            continue
        if _extract_budget_hold_flag(q_data):
            held_by_budget = True
        rc_q = q_data.get("runnable_count")
        if isinstance(rc_q, int) and not isinstance(rc_q, bool):
            explicit_runnable = max(explicit_runnable or 0, rc_q)
        ta_q = _extract_tick_age_seconds(q_data, now_dt=now_dt)
        if ta_q is not None:
            explicit_tick_age = ta_q if explicit_tick_age is None else max(explicit_tick_age, ta_q)
        if q_path.name == "resident_summary.json":
            try:
                signals["resident_ticks_recorded"] = max(
                    signals["resident_ticks_recorded"],
                    int(q_data.get("acted_ticks") or q_data.get("active_turns") or 0),
                )
            except (TypeError, ValueError):
                pass
        q_status = str(q_data.get("status") or q_data.get("health") or "").strip().upper()
        if q_status not in BUDGET_HOLD_STATUS_TOKENS and (
            q_status in {"STALLED", "DEGRADED", "BLOCKED", "DEADLOCKED", "ERROR"}
            or q_data.get("stalled") is True
        ):
            fault_reasons.append({
                "code": f"QUEUE_STATE_{q_status or 'STALLED'}",
                "reason_cn": str(q_data.get("reason") or f"{q_path.name} 标记队列消费停滞"),
            })
        for key_name in ("queue_backlog", "backlog_count", "pending_count", "unprocessed_count"):
            val = q_data.get(key_name)
            if isinstance(val, int) and not isinstance(val, bool):
                max_backlog = max(max_backlog, val)
        for key_name in ("pending_required_count", "pending_required"):
            val = q_data.get(key_name)
            if isinstance(val, int) and not isinstance(val, bool):
                max_pending_req = max(max_pending_req, val)
        dl = q_data.get("dead_letter_count")
        if isinstance(dl, int) and not isinstance(dl, bool):
            max_dead_letter = max(max_dead_letter, dl)
        ua = q_data.get("oldest_unacked_seconds")
        if isinstance(ua, (int, float)) and not isinstance(ua, bool):
            max_unacked_age = max(max_unacked_age, int(ua))

    if pending_heartbeat_fault is not None and not held_by_budget:
        fault_reasons.append(pending_heartbeat_fault)

    runnable_count = explicit_runnable if explicit_runnable is not None else max_backlog
    if explicit_tick_age is not None:
        tick_age_s = round(explicit_tick_age, 3)
        tick_age_known = True
    elif max_unacked_age > 0:
        tick_age_s = float(max_unacked_age)
        tick_age_known = True
    else:
        tick_age_s = 0.0
        tick_age_known = False

    gap12_stalled = evaluate_gap12_stalled(
        runnable_count=runnable_count,
        held_by_budget=held_by_budget,
        tick_age_s=tick_age_s,
    )

    signals["queue_backlog_count"] = max_backlog
    signals["runnable_count"] = runnable_count
    signals["held_by_budget"] = held_by_budget
    signals["tick_age_s"] = tick_age_s
    signals["gap12_stalled"] = gap12_stalled
    signals["pending_required_count"] = max_pending_req
    signals["dead_letter_count"] = max_dead_letter
    signals["oldest_unacked_seconds"] = max_unacked_age

    # GAP-12: when held_by_budget is True, queue accumulation (>= 50) or unacked wait is budget hold, NEVER STALLED
    if not held_by_budget:
        if gap12_stalled:
            fault_reasons.append({
                "code": "GAP12_RUNNABLE_TICK_STALLED",
                "reason_cn": (
                    f"存在 {runnable_count} 项可执行工作且未被预算挂起，距上次有效推进已达 "
                    f"{int(tick_age_s)} 秒（超过 {GAP12_STALLED_TICK_AGE_SECONDS} 秒阈值），判定调度卡滞"
                ),
            })
        if max_backlog >= QUEUE_BACKLOG_FAULT_THRESHOLD and (
            not tick_age_known or tick_age_s > GAP12_STALLED_TICK_AGE_SECONDS
        ):
            fault_reasons.append({
                "code": "QUEUE_BACKLOG_OVERFLOW",
                "reason_cn": f"待处理队列积压 {max_backlog} 条（超过健康阈值 {QUEUE_BACKLOG_FAULT_THRESHOLD}），消费卡顿",
            })
        if max_unacked_age >= STALE_UNACKED_SECONDS_THRESHOLD:
            fault_reasons.append({
                "code": "UNACKED_MESSAGE_STALE_15M",
                "reason_cn": f"最早未确认任务已滞留 {max_unacked_age} 秒（超过 15 分钟），通道退化",
            })

    if max_pending_req >= PENDING_REQUIRED_FAULT_THRESHOLD and not held_by_budget:
        fault_reasons.append({
            "code": "PENDING_OBLIGATION_OVERFLOW",
            "reason_cn": f"必需结算事项积压 {max_pending_req} 条，暂停新增自主副作用",
        })
    if max_dead_letter > 0:
        fault_reasons.append({
            "code": "QUEUE_DEAD_LETTER_PRESENT",
            "reason_cn": f"存在 {max_dead_letter} 条死信异常事件未排清",
        })

    return {
        "is_faulted": len(fault_reasons) > 0,
        "fault_reasons": fault_reasons,
        "signals": signals,
    }


def _project_runtime_mode(
    state: Mapping[str, Any],
    *,
    day_root: Path,
    now_dt: datetime,
    now_iso: str,
) -> dict[str, Any]:
    mode_cfg = state["mode_config"]
    router = state["wake_router"]
    mode = str(mode_cfg.get("mode") or MODE_NORMAL)
    budget_pct = int(mode_cfg.get("budget_scale_pct") or 100)
    ret_sec = state.get("retirement") if isinstance(state.get("retirement"), Mapping) else {}
    if str(ret_sec.get("status") or "") == RETIREMENT_STATUS_SEALED:
        mode = MODE_DORMANT
        budget_pct = 0
    reason_cn = str(mode_cfg.get("reason_cn") or "")
    dormant_since = mode_cfg.get("dormant_since")
    last_gap_sec = int(mode_cfg.get("last_dormant_gap_seconds") or 0)
    last_recovery = copy.deepcopy(mode_cfg.get("last_dormant_recovery"))

    health_inspect = inspect_day_root_health_signals(day_root, now_dt=now_dt)
    fault_reasons = list(health_inspect["fault_reasons"])
    if state.get("_corrupt_state_file"):
        fault_reasons.append({
            "code": "CORRUPT_RUNTIME_MIGRATION_STATE",
            "reason_cn": "运行治理状态文件损坏，需核验后再恢复自主写操作",
        })

    bg_timestamps = router.get("background_wake_timestamps") or []
    wakes_hour, wakes_day = _count_recent_background_wakes(bg_timestamps, now_dt)
    control_wakes = int(router.get("control_wakes_total") or 0)
    resident_active = int(health_inspect["signals"].get("resident_ticks_recorded") or 0)

    if mode == MODE_DORMANT and isinstance(dormant_since, str):
        d_dt = _parse_iso(dormant_since)
        current_dormant_gap = max(0, int((now_dt - d_dt).total_seconds())) if d_dt is not None else 0
    else:
        current_dormant_gap = last_gap_sec

    is_low_budget = mode == MODE_LOW_BUDGET or (mode == MODE_NORMAL and budget_pct <= 25)
    configured_pct = budget_pct
    if mode != MODE_DORMANT:
        try:
            from components.life.scripts import v02_autobiography_skill_provenance as _asp

            asp_budget = _asp.project_budget_survival(day_root, now_iso=now_iso)
            asp_pct_raw = asp_budget.get("budget_scale_pct") if isinstance(asp_budget, Mapping) else None
            if (
                isinstance(asp_pct_raw, int)
                and not isinstance(asp_pct_raw, bool)
                and asp_pct_raw < budget_pct
            ):
                budget_pct = max(0, asp_pct_raw)
                if mode == MODE_NORMAL and (
                    budget_pct <= 25
                    or str(asp_budget.get("mode") or "").upper() != _asp.BUDGET_MODE_NORMAL
                ):
                    is_low_budget = True
        except Exception:
            pass

    if fault_reasons:
        health_state = HEALTH_STALLED_OR_DEGRADED
        summary_cn = (
            "检测到后台通道积压或实例异常："
            + "；".join(r["reason_cn"] for r in fault_reasons[:3])
            + "。如实报告技术故障，不伪装成安静休息。"
        )
    elif mode == MODE_DORMANT:
        health_state = HEALTH_DORMANT_NO_COGNITION
        summary_cn = (
            "当前处于休眠停摆模式：仅保留数据存储与重开条件，期间不产生认知经历，"
            "醒来后先核对时间与环境变化，绝不补写休眠期间的假日记。"
        )
    elif is_low_budget:
        health_state = HEALTH_LOW_BUDGET_CONSERVATION
        summary_cn = (
            f"当前处于省电低预算节奏（预算 {budget_pct}%）：暂停漫游探查与新开大坑，"
            "保留在途作品保存点、已认领承诺与删除/对账责任，只做局部小修订；"
            "这是算力供给收紧，不是身体累了或心情不好。"
        )
    elif wakes_day == 0 and control_wakes == 0 and resident_active == 0:
        health_state = HEALTH_QUIET_HEALTHY
        summary_cn = "今天很安静：队列与实例健康正常，0 条待办事件、0 次模型调用，平平常常，不硬编剧情。"
    else:
        health_state = HEALTH_ACTIVE_HEALTHY
        summary_cn = "日常运行顺畅，唤醒配额与队列均在健康水位内。"

    paused_activities: list[str] = []
    allowed_work_scope = "normal_bounded_daily_life"
    if health_state == HEALTH_LOW_BUDGET_CONSERVATION:
        paused_activities = [
            "ambient_roaming_exploration",
            "new_large_project_initiation",
            "broad_open_ended_digression",
        ]
        allowed_work_scope = "local_small_revision_only"
    elif health_state == HEALTH_DORMANT_NO_COGNITION:
        paused_activities = [
            "all_background_cognition",
            "ambient_roaming_exploration",
            "new_large_project_initiation",
        ]
        allowed_work_scope = "storage_and_reopen_gate_only"
    elif health_state == HEALTH_STALLED_OR_DEGRADED:
        paused_activities = [
            "autonomous_side_effect_writes",
            "ambient_roaming_exploration",
        ]
        allowed_work_scope = "control_and_reconciliation_only"

    effective_mode = (
        MODE_LOW_BUDGET
        if (mode == MODE_NORMAL and is_low_budget)
        else mode
    )
    return {
        "mode": effective_mode,
        "budget_scale_pct": 0 if mode == MODE_DORMANT else budget_pct,
        "configured_budget_scale_pct": 0 if mode == MODE_DORMANT and configured_pct == 100 else configured_pct,
        "health_state": health_state,
        "summary_cn": summary_cn,
        "reason_cn": reason_cn,
        "observed_at": now_iso,
        "dormant_since": dormant_since if mode == MODE_DORMANT else None,
        "dormant_gap_seconds": current_dormant_gap,
        "no_fake_backfill": True,
        "fake_diary_backfill_count": 0,
        "last_dormant_recovery": last_recovery,
        "reopen_conditions": [
            "explicit_set_budget_mode_normal",
            "control_lane_urgent_signal",
            "supply_budget_restored",
        ],
        "paused_activities": paused_activities,
        "allowed_work_scope": allowed_work_scope,
        "preserved_obligations": {
            "in_progress_artifact_checkpoints": True,
            "accepted_commitments": True,
            "deletion_and_reconciliation_duties": True,
            "existing_local_macros_usable": True,
        },
        "pretend_body_tired_or_bad_mood": False,
        "fake_equal_intimacy_template": False,
        "fabricate_drama_when_quiet": False,
        "is_technical_fault": bool(fault_reasons),
        "disguised_as_quiet": False,
        "fault_reasons": fault_reasons,
        "instance_signals": health_inspect["signals"],
        "effective_wakes_today": wakes_day,
        "effective_wakes_last_hour": wakes_hour,
        "llm_calls": 0,
    }


def _compute_migration_verdict(carrier_sec: Mapping[str, Any]) -> str:
    fp = carrier_sec.get("carrier_fingerprint") or {}
    preview_ok = bool(fp.get("preview_available", True))
    skills = carrier_sec.get("skills") or {}
    has_loss = not preview_ok
    has_unvalidated = False
    for sk in skills.values():
        if not isinstance(sk, Mapping):
            continue
        st = str(sk.get("status") or SKILL_STATUS_VALIDATED)
        if st == SKILL_STATUS_SUSPENDED_LOSS:
            has_loss = True
        elif st in (SKILL_STATUS_UNVALIDATED, SKILL_STATUS_PENDING_REVERIFY):
            has_unvalidated = True
    if has_loss:
        return VERDICT_CONTINUED_WITH_LOSS
    if has_unvalidated:
        return VERDICT_UNVERIFIED
    return VERDICT_CONTINUED


def _project_today_capability_card(
    state: Mapping[str, Any],
    runtime_mode_proj: Mapping[str, Any],
) -> dict[str, Any]:
    carrier_sec = state["carrier_migration"]
    fp = copy.deepcopy(carrier_sec.get("carrier_fingerprint") or DEFAULT_CARRIER_FINGERPRINT)
    preview_ok = bool(fp.get("preview_available", True))
    skills = carrier_sec.get("skills") or {}
    verdict = _compute_migration_verdict(carrier_sec)
    health_state = str(runtime_mode_proj.get("health_state") or HEALTH_QUIET_HEALTHY)

    reliable_now: list[dict[str, Any]] = []
    needs_recheck: list[dict[str, Any]] = []
    paused_by_budget_or_carrier: list[dict[str, Any]] = []

    for sk_id, raw_sk in skills.items():
        if not isinstance(raw_sk, Mapping):
            continue
        item_base = {
            "skill_id": str(sk_id),
            "title_cn": str(raw_sk.get("title_cn") or sk_id),
            "plain_desc_cn": str(raw_sk.get("plain_desc_cn") or ""),
            "status": str(raw_sk.get("status") or SKILL_STATUS_VALIDATED),
            "personal_importance": str(raw_sk.get("personal_importance") or ""),
            "importance_preserved": True,
        }
        requires_prev = bool(raw_sk.get("requires_preview", False))
        st = item_base["status"]

        if health_state == HEALTH_DORMANT_NO_COGNITION and raw_sk.get("category") not in ("governance_contract",):
            paused_by_budget_or_carrier.append({
                **item_base,
                "pause_source": "dormant_mode",
                "reason_cn": "休眠期间暂停日常创作与探查，仅保留存储与撤权删除对账底线",
            })
            continue

        if requires_prev and not preview_ok:
            paused_by_budget_or_carrier.append({
                **item_base,
                "pause_source": "carrier_missing_preview",
                "reason_cn": "新宿主暂缺实时预览窗，改用纯文本核对或等宿主补齐预览后再开",
            })
            continue

        if st in (SKILL_STATUS_UNVALIDATED, SKILL_STATUS_PENDING_REVERIFY):
            needs_recheck.append({
                **item_base,
                "reason_cn": "换了新模型底座，这件事依然重要，但旧顺手技法要先拿小样试跑一次再当稳妥能力用",
            })
        elif st == SKILL_STATUS_SUSPENDED_LOSS:
            paused_by_budget_or_carrier.append({
                **item_base,
                "pause_source": "migration_trial_loss",
                "reason_cn": "新载体试跑未达原有效果，诚实挂起，不靠硬编故事掩盖",
            })
        else:
            reliable_now.append({
                **item_base,
                "reason_cn": "当前载体与预算下可直接稳妥使用",
            })

    if health_state == HEALTH_LOW_BUDGET_CONSERVATION:
        paused_by_budget_or_carrier.append({
            "skill_id": "ambient_roaming_and_large_work",
            "title_cn": "远途漫游探查与新开大型作品",
            "plain_desc_cn": "主动逛街探新店、开启多章节长篇新坑",
            "status": "PAUSED_BY_BUDGET",
            "personal_importance": "留待预算宽裕时再展开的日常余量",
            "importance_preserved": True,
            "pause_source": "low_budget_conservation",
            "reason_cn": "预算缩减到省电档，先把余量留给在途作品局部小修订和已答应的约定",
        })

    if health_state == HEALTH_STALLED_OR_DEGRADED:
        headline_cn = "后台队列有积压或异常，先保住对账和撤权通道，等故障排清再接着忙手头作品。"
    elif health_state == HEALTH_DORMANT_NO_COGNITION:
        headline_cn = "现在是休眠保存状态：房间作品和重开条件都留着，不跑日常思考，醒来再接着过。"
    elif not preview_ok and needs_recheck:
        headline_cn = "旧文稿保存和约定仍能做，但新宿主少了一种实时预览，且几项个人细活要先试跑一次。"
    elif not preview_ok:
        headline_cn = "旧文稿续写和整理办法仍能做，但新宿主少了一种实时预览，排版类小活要先改用纯文本核对。"
    elif needs_recheck:
        headline_cn = "换了新模型底座，基础读写和约定照常，几项顺手的个人技法需要先试一次再算数。"
    elif health_state == HEALTH_LOW_BUDGET_CONSERVATION:
        headline_cn = "今天走省电小步节奏：在途作品保存点和答应的事都在，只做局部小修改，不开新大坑。"
    else:
        headline_cn = "今天文稿断点续写、桌面整理、预览排布和答应过的事都能照常稳妥做。"

    return {
        "headline_cn": headline_cn,
        "reliable_now": reliable_now,
        "needs_recheck": needs_recheck,
        "paused_by_budget_or_carrier": paused_by_budget_or_carrier,
        "last_migration_verdict": verdict,
        "carrier_fingerprint": fp,
        "previous_carrier_fingerprint": copy.deepcopy(carrier_sec.get("previous_carrier_fingerprint")),
        "personal_importance_preserved": True,
        "loss_report": copy.deepcopy(carrier_sec.get("loss_report") or []),
    }


def _summarize_phone_slice_doc(
    slice_file: str,
    doc: Mapping[str, Any],
    *,
    source_rel: Optional[str] = None,
) -> dict[str, Any]:
    """Build a compact read-only summary for one present portable phone slice file."""
    label_cn, tier = SUITCASE_SLICE_META.get(slice_file, (slice_file, "records_and_objects"))
    if slice_file == "v08_composable_body.json":
        return {
            "slice_file": slice_file,
            "tier": tier,
            "entry_count": 0,
            "title_cn": f"{label_cn}（按需受控便携切片）",
            "source": source_rel or f"{STATE_DIRNAME}/{slice_file}",
        }
    entry_count = 0
    for key in (
        "items",
        "notes",
        "tomorrow_items",
        "memories",
        "tombstones",
        "bench_visits",
        "window_bookmarks",
        "palette_studies",
        "collection_items",
        "habits",
        "rules",
        "self_rules",
        "desires",
        "recipes",
        "plans",
        "pins",
        "shared_gifts",
        "later_reflections",
        "encounters",
        "shared_materials",
        "npc_errands_and_hearsay",
        "play_sessions",
        "craft_genealogy",
        "future_self_questions",
        "shared_idioms",
        "memory_reinterpretations",
        "style_and_interest_sparks",
        "divergent_notices",
        "day_directions",
        "personal_rituals",
    ):
        val = doc.get(key)
        if isinstance(val, (list, Mapping)):
            entry_count += len(val)
    if entry_count == 0:
        entry_count = sum(
            1
            for k, v in doc.items()
            if k not in ("schema", "schema_version", "revision", "updated_at", "operation_ledger")
            and v not in (None, "", [], {})
        )
    detail_suffix = f"（已记录 {entry_count} 项）" if entry_count > 0 else "（已初始化）"
    return {
        "slice_file": slice_file,
        "tier": tier,
        "entry_count": entry_count,
        "title_cn": f"{label_cn}{detail_suffix}",
        "source": source_rel or f"{STATE_DIRNAME}/{slice_file}",
    }


def _discover_real_suitcase_assets_readonly(
    day_root: Optional[Path],
    *,
    practice_service: Any = None,
    project_service: Any = None,
) -> dict[str, Any]:
    """Read real PersonalPracticeService methods, PrivateProjectService works, character_pack.json, keepsakes, phone slices & world assets (zero writes)."""
    result: dict[str, Any] = {
        "has_real_sources": False,
        "character_pack": None,
        "practice_methods": [],
        "private_works": [],
        "shared_keepsakes": [],
        "phone_slices": [],
        "world_slices": [],
        "world_package_files": [],
        "world_package_oversized": False,
        "tombstone_count": 0,
    }
    if day_root is None and practice_service is None and project_service is None:
        return result

    seen_method_ids: set[str] = set()
    seen_work_ids: set[str] = set()

    # 1. Explicit service instances if wired
    if practice_service is not None and hasattr(practice_service, "list_methods"):
        try:
            ans = practice_service.list_methods(limit=16)
            if isinstance(ans, Mapping) and ans.get("ok"):
                ans_data = ans.get("data") if isinstance(ans.get("data"), Mapping) else {}
                method_rows = (
                    ans_data.get("methods")
                    or ans_data.get("projects")
                    or ans.get("methods")
                    or ans.get("projects")
                    or ()
                )
                for row in method_rows:
                    if not isinstance(row, Mapping):
                        continue
                    aid = str(row.get("artifact_id") or "").strip()
                    if not aid or aid in seen_method_ids:
                        continue
                    seen_method_ids.add(aid)
                    result["practice_methods"].append({
                        "artifact_id": aid,
                        "title": str(row.get("title") or aid).strip(),
                        "method_state": str(row.get("method_state") or "TENTATIVE").strip().upper(),
                        "scope": str(row.get("scope") or "").strip(),
                        "source": "PersonalPracticeService",
                    })
        except Exception:
            pass

    if project_service is not None and hasattr(project_service, "list_projects"):
        try:
            ans = project_service.list_projects(include_archived=True, limit=16)
            if isinstance(ans, Mapping) and ans.get("ok"):
                proj_data = ans.get("data") if isinstance(ans.get("data"), Mapping) else {}
                proj_rows = ans.get("projects") or proj_data.get("projects") or ()
                for row in proj_rows:
                    if not isinstance(row, Mapping):
                        continue
                    aid = str(row.get("artifact_id") or "").strip()
                    if not aid or aid in seen_method_ids or aid in seen_work_ids:
                        continue
                    # Skip practice documents if practice_service wasn't passed separately
                    is_prac = False
                    if hasattr(project_service, "read_project"):
                        try:
                            detail = project_service.read_project(aid, include_history=False)
                            proj_obj = (detail or {}).get("project") if isinstance(detail, Mapping) else {}
                            raw_txt = str((proj_obj or {}).get("content") or "")
                            if "nyairo-practice:v1" in raw_txt[:240]:
                                is_prac = True
                        except Exception:
                            is_prac = False
                    if is_prac:
                        continue
                    seen_work_ids.add(aid)
                    result["private_works"].append({
                        "artifact_id": aid,
                        "title": str(row.get("title") or aid).strip(),
                        "lifecycle": str(row.get("lifecycle") or row.get("status") or "ACTIVE").strip().upper(),
                        "head_version_id": row.get("head_version_id"),
                        "source": "PrivateProjectService",
                    })
        except Exception:
            pass

    if day_root is None:
        result["has_real_sources"] = bool(result["practice_methods"] or result["private_works"])
        return result

    # 2. Read-only Supply SQLite scan on day_root (PersonalPracticeService + PrivateProjectService owner store)
    db_candidates = (
        day_root / "data" / "life-supply.sqlite3",
        day_root / "supply" / "data" / "life-supply.sqlite3",
        day_root / "supply" / "life-supply.sqlite3",
        day_root / "life-supply.sqlite3",
        day_root / "state" / "life-supply.sqlite3",
        day_root / "supply" / "supply.db",
        day_root / "one-day" / "supply.db",
        day_root / "supply.db",
        day_root / STATE_DIRNAME / "supply.db",
        day_root / "artifacts.db",
    )
    for db_path in db_candidates:
        try:
            if db_path.is_symlink() or not db_path.is_file():
                continue
        except OSError:
            continue
        try:
            import sqlite3
            from personal_practice_service import parse_practice_content
            from private_project_service import is_album_manifest

            uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=0.5)
            conn.row_factory = sqlite3.Row
            try:
                tables = {
                    str(r[0])
                    for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                }
                art_table = (
                    "managed_artifacts"
                    if "managed_artifacts" in tables
                    else ("artifacts" if "artifacts" in tables else "")
                )
                if not art_table:
                    continue
                if "artifact_versions" in tables:
                    rows = conn.execute(
                        f"""
                        SELECT a.artifact_id, a.title, a.lifecycle, a.head_version_id, v.content
                        FROM {art_table} a
                        LEFT JOIN artifact_versions v ON v.version_id = a.head_version_id
                        ORDER BY a.rowid DESC
                        LIMIT 32
                        """
                    ).fetchall()
                else:
                    rows = conn.execute(
                        f"""
                        SELECT a.artifact_id, a.title, a.lifecycle, a.head_version_id, '' AS content
                        FROM {art_table} a
                        ORDER BY a.rowid DESC
                        LIMIT 32
                        """
                    ).fetchall()
            finally:
                conn.close()

            for row in rows:
                aid = str(row["artifact_id"] or "").strip()
                if not aid:
                    continue
                title = str(row["title"] or aid).strip()
                lifecycle = str(row["lifecycle"] or "ACTIVE").strip().upper()
                content = row["content"] if isinstance(row["content"], str) else ""
                parsed_prac = parse_practice_content(content)
                if parsed_prac.get("marker_present") or parsed_prac.get("is_practice"):
                    if parsed_prac.get("kind") == "method" and aid not in seen_method_ids:
                        meta = parsed_prac.get("metadata") if isinstance(parsed_prac.get("metadata"), Mapping) else {}
                        seen_method_ids.add(aid)
                        result["practice_methods"].append({
                            "artifact_id": aid,
                            "title": str(parsed_prac.get("title") or meta.get("title") or title).strip(),
                            "method_state": str(
                                parsed_prac.get("method_state") or meta.get("method_state") or "TENTATIVE"
                            ).strip().upper(),
                            "scope": str(parsed_prac.get("scope") or meta.get("scope") or "").strip(),
                            "source": "PersonalPracticeService",
                        })
                    continue
                if is_album_manifest(content):
                    continue
                if aid not in seen_work_ids and aid not in seen_method_ids:
                    seen_work_ids.add(aid)
                    result["private_works"].append({
                        "artifact_id": aid,
                        "title": title,
                        "lifecycle": lifecycle,
                        "head_version_id": row["head_version_id"],
                        "source": "PrivateProjectService",
                    })
        except Exception:
            continue

    # Also inspect workbench_tray.json work reference if not already listed
    ex_tray, ok_tray, tray_doc = _read_json_safe(day_root / STATE_DIRNAME / "workbench_tray.json")
    if ex_tray and ok_tray:
        tray_obj = tray_doc.get("tray") if isinstance(tray_doc.get("tray"), Mapping) else tray_doc
        work_ref = tray_obj.get("work") if isinstance(tray_obj, Mapping) else None
        if isinstance(work_ref, Mapping):
            w_id = str(work_ref.get("artifact_id") or work_ref.get("work_id") or "").strip()
            if w_id and w_id not in seen_work_ids and w_id not in seen_method_ids:
                seen_work_ids.add(w_id)
                result["private_works"].append({
                    "artifact_id": w_id,
                    "title": str(work_ref.get("title") or w_id).strip(),
                    "lifecycle": "ACTIVE",
                    "head_version_id": work_ref.get("base_version_id") or work_ref.get("baseline_version_id"),
                    "source": "workbench_tray.json",
                })

    # 3. Read-only character_pack.json (with fallback to character_pack_state.json)
    pack_candidates = (
        day_root / "character_pack.json",
        day_root / STATE_DIRNAME / "character_pack.json",
        day_root / "config" / "character_pack.json",
        day_root / STATE_DIRNAME / "character_pack_state.json",
    )
    for p_cand in pack_candidates:
        ex_p, ok_p, p_data = _read_json_safe(p_cand)
        if not ex_p or not ok_p:
            continue
        pack_body = p_data.get("active_pack") if isinstance(p_data.get("active_pack"), Mapping) else p_data
        if isinstance(pack_body, Mapping) and pack_body:
            pack_id = str(
                pack_body.get("pack_id")
                or pack_body.get("character_id")
                or pack_body.get("id")
                or "character_pack"
            ).strip()
            disp_name = str(
                pack_body.get("display_name")
                or pack_body.get("name")
                or "角色"
            ).strip()
            result["character_pack"] = {
                "pack_id": pack_id,
                "display_name": disp_name,
                "pronouns": str(pack_body.get("pronouns") or "TA").strip(),
                "version": str(pack_body.get("version") or pack_body.get("schema_version") or "v1").strip(),
                "summary": str(pack_body.get("summary") or pack_body.get("temperament_note") or "").strip(),
                "source_path": str(p_cand.relative_to(day_root)).replace("\\", "/"),
            }
            break

    # 4. Read-only shared keepsakes summary (window_keepsakes.json + window_keepsake.json)
    for wk_name in ("window_keepsakes.json", "window_keepsake.json"):
        ex_wk, ok_wk, wk_data = _read_json_safe(day_root / STATE_DIRNAME / wk_name)
        if not ex_wk or not ok_wk:
            continue
        wk_src = f"{STATE_DIRNAME}/{wk_name}"
        lockscreen_obj = wk_data.get("lockscreen")
        if isinstance(lockscreen_obj, Mapping) and str(lockscreen_obj.get("album_artifact_id") or "").strip():
            aid_ls = str(lockscreen_obj.get("album_artifact_id")).strip()
            result["shared_keepsakes"].append({
                "item_id": f"keepsake:lockscreen:{aid_ls}",
                "title_cn": f"窗边自选锁屏留影（{aid_ls}）",
                "source": wk_src,
            })
        pins_list = wk_data.get("pins")
        if isinstance(pins_list, list):
            for idx, pin in enumerate(pins_list[:4]):
                if isinstance(pin, Mapping) and str(pin.get("share_id") or "").strip():
                    sid = str(pin.get("share_id")).strip()
                    result["shared_keepsakes"].append({
                        "item_id": f"keepsake:pin:{sid}",
                        "title_cn": f"冰箱贴分享纪念（{sid}）",
                        "source": wk_src,
                    })
        ks_obj = wk_data.get("keepsake") if isinstance(wk_data.get("keepsake"), Mapping) else wk_data
        note_obj = ks_obj.get("partner_note") if isinstance(ks_obj, Mapping) else None
        items_list = (
            ks_obj.get("items")
            if isinstance(ks_obj, Mapping) and isinstance(ks_obj.get("items"), list)
            else (wk_data.get("keepsakes") if isinstance(wk_data.get("keepsakes"), list) else None)
        )
        if isinstance(note_obj, Mapping) and str(note_obj.get("text") or note_obj.get("title") or "").strip():
            title_txt = str(note_obj.get("title") or note_obj.get("text") or "窗台共同信物便签").strip()[:36]
            result["shared_keepsakes"].append({
                "item_id": "keepsake:window_partner_note",
                "title_cn": f"窗台共同便签：{title_txt}",
                "source": wk_src,
            })
        if isinstance(items_list, list):
            for idx, k_item in enumerate(items_list[:4]):
                if isinstance(k_item, Mapping):
                    kid = str(k_item.get("item_instance_id") or k_item.get("id") or f"wk_{idx}").strip()
                    kname = str(k_item.get("name") or k_item.get("title") or kid).strip()
                    result["shared_keepsakes"].append({
                        "item_id": f"keepsake:{kid}",
                        "title_cn": f"共同信物：{kname}",
                        "source": wk_src,
                    })

    ex_tr, ok_tr, tr_data = _read_json_safe(day_root / STATE_DIRNAME / "trio_exchange.json")
    if ex_tr and ok_tr:
        her_picks = tr_data.get("her_picks") if isinstance(tr_data.get("her_picks"), list) else []
        partner_picks = tr_data.get("partner_picks") if isinstance(tr_data.get("partner_picks"), list) else []
        if her_picks or partner_picks:
            result["shared_keepsakes"].append({
                "item_id": "keepsake:trio_exchange_picks",
                "title_cn": f"互选心爱小物记录（主体 {len(her_picks)} 件 / 伴侣 {len(partner_picks)} 件）",
                "source": "phone/trio_exchange.json",
            })

    ex_ra, ok_ra, ra_data = _read_json_safe(day_root / STATE_DIRNAME / "reunion_anchor.json")
    if ex_ra and ok_ra and ra_data:
        anchor_label = str(ra_data.get("anchor_note") or ra_data.get("place_name") or "重逢锚点记录").strip()[:36]
        result["shared_keepsakes"].append({
            "item_id": "keepsake:reunion_anchor",
            "title_cn": f"共同归处信物锚点：{anchor_label}",
            "source": "phone/reunion_anchor.json",
        })

    ex_cg, ok_cg, cg_data = _read_json_safe(day_root / "memory" / "v02_common_ground_state.json")
    if ex_cg and ok_cg and isinstance(cg_data.get("items"), Mapping):
        shared_cnt = sum(
            1
            for item in cg_data["items"].values()
            if isinstance(item, Mapping) and item.get("ground_status") == "SHARED_WITH_PARTNER"
        )
        if shared_cnt > 0:
            result["shared_keepsakes"].append({
                "item_id": "keepsake:common_ground_shared",
                "title_cn": f"已确认共同认知与约定（共 {shared_cnt} 项）",
                "source": "memory/v02_common_ground_state.json",
            })

    # 5. Read-only scan of real portable phone slices (including canonical companion paths)
    for slice_file in SUITCASE_ALLOWED_SLICE_FILES:
        sl_doc, sl_src = _read_slice_or_companion_readonly_with_source(day_root, slice_file)
        if sl_doc is not None:
            result["phone_slices"].append(
                _summarize_phone_slice_doc(slice_file, sl_doc, source_rel=sl_src)
            )

    # 6. Read-only scan of real <day_root>/world/ (or world-home/world/) v0.8/v0.4 world slices
    for w_file in SUITCASE_ALLOWED_WORLD_FILES:
        w_doc, w_src = _read_world_slice_readonly_with_source(day_root, w_file)
        if w_doc is not None:
            w_label = SUITCASE_WORLD_SLICE_META.get(w_file, w_file)
            result["world_slices"].append({
                "world_file": w_file,
                "title_cn": f"{w_label}（已初始化）",
                "source": w_src or f"world/{w_file}",
            })

    # 7. Read-only scan of custom <day_root>/world-package/ (<= 2 MB, non-symlink)
    wp_files_meta, wp_oversized = _inspect_world_package_readonly(day_root)
    result["world_package_files"] = wp_files_meta
    result["world_package_oversized"] = wp_oversized

    # 8. Read-only tombstone watermark count
    ts_map = _extract_tombstone_ledger_readonly(day_root, now_iso="1970-01-01T00:00:00+00:00")
    result["tombstone_count"] = len(ts_map)

    result["has_real_sources"] = bool(
        result["character_pack"]
        or result["practice_methods"]
        or result["private_works"]
        or result["shared_keepsakes"]
        or result["phone_slices"]
        or result["world_slices"]
        or result["world_package_files"]
    )
    return result


def _build_suitcase_for_route(
    route_mode: str,
    *,
    skills: Mapping[str, Any],
    keep_skill_ids: Optional[Sequence[str]],
    real_assets: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    assets = dict(real_assets or {})
    core_records: list[dict[str, Any]] = [
        {
            "item_id": "verified_history_and_provenance",
            "kind": "record",
            "title_cn": "有来源的经历账本与删除撤权屏障水位",
            "reason_cn": "保证搬家后不丢因果来源，也不让已删内容复活",
        },
    ]
    char_pack = assets.get("character_pack")
    if isinstance(char_pack, Mapping):
        core_records.append({
            "item_id": f"character_pack:{char_pack.get('pack_id') or 'active'}",
            "kind": "character_pack",
            "title_cn": f"角色设定包摘要（{char_pack.get('display_name') or '主体'} · {char_pack.get('version') or 'v1'}）",
            "reason_cn": "从真实 character_pack 读取主体身份与基础设定，迁居不换人",
            "source": str(char_pack.get("source_path") or "character_pack.json"),
        })

    real_works = list(assets.get("private_works") or [])
    works_title_cn = (
        f"在途与已完成的作品手稿（共 {len(real_works)} 部真实作品）"
        if real_works
        else "在途与已完成的作品手稿及断点书签"
    )
    core_objects_and_commitments: list[dict[str, Any]] = [
        {
            "item_id": "in_progress_markdown_works",
            "kind": "artifact",
            "title_cn": works_title_cn,
            "reason_cn": "搬去新地方还能顺着原来的保存点继续改",
        },
        {
            "item_id": "active_shared_commitments",
            "kind": "commitment",
            "title_cn": "双方仍有效的共同约定与待办责任",
            "reason_cn": "换了环境也不赖账或遗忘答应过的事",
        },
    ]
    for work in real_works:
        if not isinstance(work, Mapping):
            continue
        core_objects_and_commitments.append({
            "item_id": str(work.get("artifact_id")),
            "kind": "private_project_work",
            "title_cn": f"个人作品：《{work.get('title')}》（{work.get('lifecycle')}）",
            "reason_cn": "来自 PrivateProjectService 的真实作品与断点版本，随身带去新工作台",
            "source": str(work.get("source") or "PrivateProjectService"),
        })

    for ks in assets.get("shared_keepsakes") or ():
        if not isinstance(ks, Mapping):
            continue
        core_objects_and_commitments.append({
            "item_id": str(ks.get("item_id")),
            "kind": "shared_keepsake",
            "title_cn": str(ks.get("title_cn")),
            "reason_cn": "双方真实留下的共同信物与关系锚点摘要",
            "source": str(ks.get("source") or "shared_keepsakes"),
        })

    universal_tools = [
        {
            "item_id": "markdown_local_checkpoint",
            "kind": "universal_tool",
            "title_cn": "本地文稿断点保存小工具",
            "reason_cn": "纯确定性通用工具，不绑特定模型与宿主特效",
        },
    ]

    optional_personal_skills: list[dict[str, Any]] = []
    for sk_id in ("nuanced_draft_stitching", "window_sketch_tone_tuning", "interactive_layout_live_preview"):
        sk = skills.get(sk_id)
        if not isinstance(sk, Mapping):
            continue
        optional_personal_skills.append({
            "item_id": sk_id,
            "kind": "personal_practice",
            "title_cn": str(sk.get("title_cn") or sk_id),
            "skill_status": str(sk.get("status") or SKILL_STATUS_VALIDATED),
            "reason_cn": "经日常实践沉淀的个人顺手做法（迁后按需试跑核对）",
        })

    legacy_high_debt_items: list[dict[str, Any]] = [
        {
            "item_id": "legacy_host_exclusive_shader",
            "kind": "host_coupled_effect",
            "title_cn": "依赖旧宿主的难迁专属特效配置",
            "reason_cn": "强行搬运会背上沉重兼容债、挤占新生活余量，主动留在旧归档里",
        },
        {
            "item_id": "retired_brittle_shortcut_macro",
            "kind": "retired_method",
            "title_cn": "已失配的过时快捷键旧脚本",
            "reason_cn": "旧环境下早已少用，主动舍弃以保持新工作台清爽",
        },
    ]

    default_real_keep_ids: set[str] = set()
    for method in assets.get("practice_methods") or ():
        if not isinstance(method, Mapping):
            continue
        mid = str(method.get("artifact_id") or "").strip()
        mtitle = str(method.get("title") or mid).strip()
        mstate = str(method.get("method_state") or "TENTATIVE").strip().upper()
        if not mid:
            continue
        if mstate in {"RETIRED", "CONTESTED"}:
            legacy_high_debt_items.append({
                "item_id": mid,
                "kind": "retired_method",
                "title_cn": f"已退居旧档做法：{mtitle}（{mstate}）",
                "reason_cn": "PersonalPracticeService 中已退役或存疑的做法，留存旧档不塞进新箱子",
                "source": "PersonalPracticeService",
            })
        else:
            optional_personal_skills.append({
                "item_id": mid,
                "kind": "personal_practice",
                "title_cn": f"沉淀做法：{mtitle}（{mstate}）",
                "skill_status": mstate,
                "reason_cn": "来自 PersonalPracticeService 的真实个人方法记录",
                "source": "PersonalPracticeService",
            })
            if mstate in {"SETTLED", "TRIED", "ACTIVE", "TENTATIVE"}:
                default_real_keep_ids.add(mid)

    for sl_summary in assets.get("phone_slices") or ():
        if not isinstance(sl_summary, Mapping):
            continue
        sl_file = str(sl_summary.get("slice_file") or "").strip()
        if not sl_file:
            continue
        sl_tier = str(sl_summary.get("tier") or "records_and_objects").strip()
        sl_item_id = f"phone_slice:{sl_file}"
        sl_title = str(sl_summary.get("title_cn") or sl_file)
        sl_source = str(sl_summary.get("source") or f"{STATE_DIRNAME}/{sl_file}")
        if sl_tier == "records_only":
            core_records.append({
                "item_id": sl_item_id,
                "kind": "phone_slice_record",
                "slice_file": sl_file,
                "title_cn": sl_title,
                "reason_cn": "来自本地真实记录切片，保证迁居后便签、炉火与记忆墓碑水位完整延续",
                "source": sl_source,
            })
        elif sl_tier == "records_and_objects":
            core_objects_and_commitments.append({
                "item_id": sl_item_id,
                "kind": "phone_slice_object",
                "slice_file": sl_file,
                "title_cn": sl_title,
                "reason_cn": "来自本地真实物件与足迹切片，把书桌小物、书店书签与试色记录一并带走",
                "source": sl_source,
            })
        else:
            optional_personal_skills.append({
                "item_id": sl_item_id,
                "kind": "phone_slice_practice",
                "slice_file": sl_file,
                "title_cn": sl_title,
                "skill_status": SKILL_STATUS_VALIDATED,
                "reason_cn": "来自本地真实日常习惯与节奏切片，可在迁居时按需选择保留或从简重起",
                "source": sl_source,
            })
            default_real_keep_ids.add(sl_item_id)

    for w_summary in assets.get("world_slices") or ():
        if not isinstance(w_summary, Mapping):
            continue
        w_file = str(w_summary.get("world_file") or "").strip()
        if not w_file:
            continue
        core_objects_and_commitments.append({
            "item_id": f"world_slice:{w_file}",
            "kind": "world_slice_object",
            "world_file": w_file,
            "title_cn": str(w_summary.get("title_cn") or w_file),
            "reason_cn": "来自本地真实空间物质、生活痕迹与微气候切片，迁入新环境后延续房间器物现场",
            "source": str(w_summary.get("source") or f"world/{w_file}"),
        })

    wp_files_list = [
        str(x.get("package_file") or "").strip()
        for x in (assets.get("world_package_files") or ())
        if isinstance(x, Mapping) and str(x.get("package_file") or "").strip()
    ]
    if wp_files_list:
        core_objects_and_commitments.append({
            "item_id": "custom_world_package:world-package",
            "kind": "custom_world_package",
            "package_files": wp_files_list,
            "title_cn": f"自建世界包目录（共 {len(wp_files_list)} 份声明文件）",
            "reason_cn": "来自本地 world-package 自定义街区、店铺售卖规格与物件物质档声明，整体无损迁走",
            "source": "world-package",
        })

    non_portable_external_leases = [
        {
            "item_id": "old_community_exclusive_doorplate",
            "kind": "external_facility_lease",
            "title_cn": "旧公共空间门牌与第三方独占许可",
            "reason_cn": "公共设施与当地资格留在原处继续被别人正常使用，不强行私有化迁走",
        },
    ]

    if route_mode == SUITCASE_RECORDS_ONLY:
        carried = list(core_records)
        left_behind = (
            list(core_objects_and_commitments)
            + list(universal_tools)
            + optional_personal_skills
            + legacy_high_debt_items
            + non_portable_external_leases
        )
        debt_level = "MINIMAL"
        summary_cn = "记录包：只带历史与对账记录，作品物件与顺手工具全部留原处，去了新家要从零搭工作台。"
    elif route_mode == SUITCASE_RECORDS_AND_OBJECTS:
        carried = list(core_records) + list(core_objects_and_commitments)
        left_behind = (
            list(universal_tools)
            + optional_personal_skills
            + legacy_high_debt_items
            + non_portable_external_leases
        )
        debt_level = "LOW"
        summary_cn = "记录物件包：带走历史记录、作品手稿和有效约定，但不带工具与个人技法，需要重新磨合手感。"
    elif route_mode == SUITCASE_FULL_PRACTICE:
        carried = (
            list(core_records)
            + list(core_objects_and_commitments)
            + list(universal_tools)
            + optional_personal_skills
            + legacy_high_debt_items
        )
        left_behind = list(non_portable_external_leases)
        debt_level = "HIGH"
        summary_cn = "全量实践包：连难迁的旧特效和过时脚本也一并塞进箱子，兼容维护债高，容易拖累新生活。"
    else:
        keep_set = (
            set(keep_skill_ids)
            if keep_skill_ids is not None
            else ({"nuanced_draft_stitching", "window_sketch_tone_tuning"} | default_real_keep_ids)
        )
        chosen_skills: list[dict[str, Any]] = []
        dropped_skills: list[dict[str, Any]] = []
        for sk_item in optional_personal_skills:
            sid = sk_item["item_id"]
            if sid in keep_set:
                chosen_skills.append(sk_item)
            else:
                dropped_copy = dict(sk_item)
                dropped_copy["reason_cn"] = "本次选择性整理主动留在旧处，轻装迁居减少磨合负担"
                dropped_skills.append(dropped_copy)

        carried = (
            list(core_records)
            + list(core_objects_and_commitments)
            + list(universal_tools)
            + chosen_skills
        )
        left_behind = dropped_skills + legacy_high_debt_items + non_portable_external_leases
        debt_level = "LOW_BALANCED"
        summary_cn = "选择性实践包（推荐）：带走作品手稿、通用小工具、有效约定与精选技法，主动放下失效旧法和独占设施，少背兼容债。"

    return {
        "profile_mode": route_mode,
        "recommended": route_mode == SUITCASE_SELECTIVE_PRACTICE,
        "compatibility_debt_level": debt_level,
        "summary_cn": summary_cn,
        "carried_count": len(carried),
        "left_behind_count": len(left_behind),
        "carried_items": carried,
        "left_behind_items": left_behind,
    }


def _project_migration_suitcase(
    state: Mapping[str, Any],
    *,
    day_root: Optional[Path] = None,
    practice_service: Any = None,
    project_service: Any = None,
) -> dict[str, Any]:
    suit_cfg = state["suitcase_state"]
    skills = state["carrier_migration"].get("skills") or {}
    active_mode = str(suit_cfg.get("profile_mode") or SUITCASE_SELECTIVE_PRACTICE)
    if active_mode not in SUITCASE_PROFILE_MODES:
        active_mode = SUITCASE_SELECTIVE_PRACTICE
    keep_ids = suit_cfg.get("keep_skill_ids")
    leave_note = str(suit_cfg.get("leave_behind_note") or "")
    real_assets = _discover_real_suitcase_assets_readonly(
        day_root,
        practice_service=practice_service,
        project_service=project_service,
    )

    route_comparison: dict[str, dict[str, Any]] = {}
    for m in SUITCASE_PROFILE_MODES:
        route_comparison[m] = _build_suitcase_for_route(
            m,
            skills=skills,
            keep_skill_ids=keep_ids if m == active_mode else None,
            real_assets=real_assets,
        )

    active_pack = _build_suitcase_for_route(
        active_mode,
        skills=skills,
        keep_skill_ids=keep_ids,
        real_assets=real_assets,
    )

    selected_for_active = set(
        _slices_for_effective_mode(
            active_mode,
            requested_mode=active_mode,
            keep_skill_ids=keep_ids if isinstance(keep_ids, list) else None,
        )
    )
    present_phone_slice_files = [
        str(s.get("slice_file"))
        for s in (real_assets.get("phone_slices") or ())
        if isinstance(s, Mapping) and str(s.get("slice_file") or "") in selected_for_active
    ]
    present_world_slice_files = (
        [
            str(w.get("world_file"))
            for w in (real_assets.get("world_slices") or ())
            if isinstance(w, Mapping) and str(w.get("world_file") or "")
        ]
        if active_mode != SUITCASE_RECORDS_ONLY
        else []
    )
    present_wp_files = (
        [
            str(p.get("package_file"))
            for p in (real_assets.get("world_package_files") or ())
            if isinstance(p, Mapping) and str(p.get("package_file") or "")
        ]
        if active_mode != SUITCASE_RECORDS_ONLY
        else []
    )
    preview_loss_manifest = _build_migration_loss_manifest(
        profile_mode=active_mode,
        effective_mode=active_mode,
        has_phone_slices=bool(present_phone_slice_files),
        phone_slice_count=len(present_phone_slice_files),
        has_character_pack=bool(real_assets.get("character_pack")),
        world_slice_names=present_world_slice_files,
        world_package_files=present_wp_files,
        tombstone_count=int(real_assets.get("tombstone_count") or 0),
        carrier_sec=state.get("carrier_migration") or {},
        world_package_oversized=bool(real_assets.get("world_package_oversized", False)),
    )

    return {
        "profile_mode": active_mode,
        "recommended_mode": SUITCASE_SELECTIVE_PRACTICE,
        "not_full_raw_dump_export": True,
        "keep_skill_ids": list(keep_ids) if isinstance(keep_ids, list) else None,
        "leave_behind_note": leave_note,
        "packed_at": suit_cfg.get("packed_at"),
        "migrated_delivery_records": copy.deepcopy(suit_cfg.get("migrated_delivery_records") or []),
        "compatibility_debt_level": active_pack["compatibility_debt_level"],
        "summary_cn": active_pack["summary_cn"],
        "carried_items": active_pack["carried_items"],
        "left_behind_items": active_pack["left_behind_items"],
        "route_comparison": route_comparison,
        "real_sources": real_assets,
        "migration_loss_manifest": preview_loss_manifest,
    }


def collect_source_freshness(
    day_root: Path,
    *,
    now_iso: str,
    state_exists: bool,
    state: Mapping[str, Any],
) -> dict[str, Any]:
    """Read-only summary of household fact surfaces (room, desk, sensory, rhythm, migration_check)."""
    surface_specs = (
        (
            "room",
            "房间",
            "S01_World/home_hearth",
            (
                Path(STATE_DIRNAME) / "reunion_anchor.json",
                Path("world_snapshot.json"),
                Path(STATE_DIRNAME) / "home_recipes.json",
                Path("instance.json"),
            ),
            ("captured_at", "observed_at", "updated_at"),
        ),
        (
            "desk",
            "书桌",
            "S01_S09_Desk/desk_presence",
            (
                Path(STATE_DIRNAME) / "desk_tomorrow_note.json",
                Path(STATE_DIRNAME) / "workbench_tray.json",
                Path(STATE_DIRNAME) / "workbench_curio.json",
                Path(STATE_DIRNAME) / "tentative_plans.json",
            ),
            ("updated_at", "observed_at", "recorded_at"),
        ),
        (
            "sensory",
            "感知",
            "S02_S03_Sensory/sensory_field",
            (
                Path(STATE_DIRNAME) / "sensory_cards.json",
                Path(STATE_DIRNAME) / "somatic_comfort.json",
                Path(STATE_DIRNAME) / "character_pack_state.json",
            ),
            ("updated_at", "observed_at", "recorded_at"),
        ),
        (
            "rhythm",
            "节奏",
            "S09_S14_Rhythm/autonomy_rhythm",
            (
                Path(STATE_DIRNAME) / "autonomy_rhythm.json",
                Path("one-day") / "resident_summary.json",
                Path("ready.json"),
            ),
            ("updated_at", "observed_at", "heartbeat_at"),
        ),
    )

    surfaces: dict[str, dict[str, Any]] = {}
    for surf_id, label_cn, owner_tag, rel_candidates, ts_keys in surface_specs:
        chosen_rel = str(rel_candidates[0]).replace("\\", "/")
        found_exists = False
        synced_at: Optional[str] = None
        for rel_p in rel_candidates:
            full_p = day_root / rel_p
            ex, ok, data = _read_json_safe(full_p)
            if ex:
                found_exists = True
                chosen_rel = str(rel_p).replace("\\", "/")
                if ok:
                    for tk in ts_keys:
                        val = data.get(tk)
                        if isinstance(val, str) and _parse_iso(val) is not None:
                            synced_at = val
                            break
                if synced_at is None:
                    synced_at = _mtime_iso(full_p)
                break

        surfaces[surf_id] = {
            "surface_id": surf_id,
            "label_cn": label_cn,
            "source_owner": owner_tag,
            "source_path": chosen_rel,
            "source_exists": found_exists,
            "last_synced_at": synced_at,
            "freshness_status": "SYNCED" if found_exists else "DEFAULT_UNMATERIALIZED",
        }

    mig_rel = f"{STATE_DIRNAME}/{STATE_FILENAME}"
    mig_synced = (
        str(state.get("carrier_migration", {}).get("last_checked_at") or state.get("updated_at") or "")
        or (_mtime_iso(day_root / STATE_DIRNAME / STATE_FILENAME) if state_exists else None)
    )
    surfaces["migration_check"] = {
        "surface_id": "migration_check",
        "label_cn": "迁居体检",
        "source_owner": "S12_S13_S14_RuntimeMigration/v02_runtime_migration",
        "source_path": mig_rel,
        "source_exists": bool(state_exists),
        "last_synced_at": mig_synced if mig_synced else None,
        "freshness_status": "SYNCED" if state_exists else "DEFAULT_UNMATERIALIZED",
    }

    out: dict[str, Any] = {
        "checked_at": now_iso,
        "surfaces": surfaces,
    }
    for k, v in surfaces.items():
        out[k] = copy.deepcopy(v)
    return out


def _decay_count_entries(doc: Any, count_key: Optional[str]) -> int:
    if not count_key or not isinstance(doc, Mapping):
        return 0
    val = doc.get(count_key)
    if isinstance(val, (list, Mapping)):
        return len(val)
    return 0


def _decay_snapshot(
    root: Path,
    sources: Sequence[str],
) -> tuple[bool, Optional[datetime], Optional[str], dict[str, dict[str, Any]]]:
    """Read presence/freshness/counts from real files with zero writes."""
    present = False
    latest_dt: Optional[datetime] = None
    latest_iso: Optional[str] = None
    docs: dict[str, dict[str, Any]] = {}
    for rel in sources:
        path = root / rel
        try:
            if path.is_symlink() or not path.is_file():
                continue
        except OSError:
            continue
        present = True
        _exists, ok, doc = _read_json_safe(path)
        m_iso = _mtime_iso(path)
        m_dt = _parse_iso(m_iso)
        if m_dt is not None and (latest_dt is None or m_dt > latest_dt):
            latest_dt = m_dt
            latest_iso = m_iso
        if ok:
            docs[rel] = doc
    return present, latest_dt, latest_iso, docs


def project_annual_decay_matrix_22(
    day_root: Path | str,
    *,
    horizon_days: int = 365,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Ch.16 §7：22 类一年期衰败监测矩阵的只读投影（零写盘、零大模型）。

    仅统计既有实证文件的存在性、最近修改时间与条目数，据此为每一类计算半衰期、
    底线权重、漂移区间、重锚定策略与当前观测状态（``ACTIVE`` / ``DORMANT`` /
    ``NEEDS_REANCHOR`` / ``ABSENT_BASELINE``）。没有任何基线文件的类别诚实记为
    ``ABSENT_BASELINE``，绝不虚构观测。
    """
    root = Path(day_root)
    now_dt, resolved_iso = _resolve_now(now_iso)
    horizon = max(1, int(horizon_days))
    categories: list[dict[str, Any]] = []
    group_counts = {group: 0 for group in DECAY_MONITOR_GROUPS}
    status_counts = {status: 0 for status in DECAY_MONITOR_STATUSES}

    for spec in DECAY_MONITOR_CATEGORIES_22:
        group = str(spec["group"])
        present, latest_dt, latest_iso, docs = _decay_snapshot(root, spec["sources"])
        entry_count = 0
        count_key = spec.get("count_key")
        if count_key:
            for doc in docs.values():
                entry_count += _decay_count_entries(doc, count_key)

        half_life = max(1, int(spec["half_life_days"]))
        age_days: Optional[float] = None
        if present and latest_dt is not None:
            age_days = round(max(0.0, (now_dt - latest_dt).total_seconds() / 86400.0), 3)

        if not present:
            status = DECAY_STATUS_ABSENT_BASELINE
            retained_weight = 0.0
        else:
            if age_days is None:
                status = DECAY_STATUS_ACTIVE
                retained_weight = 1.0
            else:
                if age_days > horizon:
                    status = DECAY_STATUS_NEEDS_REANCHOR
                elif age_days > horizon * 0.5:
                    status = DECAY_STATUS_DORMANT
                else:
                    status = DECAY_STATUS_ACTIVE
                retained_weight = round(0.5 ** (age_days / half_life), 4)

        group_counts[group] = group_counts.get(group, 0) + 1
        status_counts[status] = status_counts.get(status, 0) + 1
        drift_lo, drift_hi = spec["drift_band"]
        categories.append({
            "category": str(spec["category"]),
            "group": group,
            "label_cn": str(spec["label_cn"]),
            "status": status,
            "present": bool(present),
            "entry_count": int(entry_count),
            "last_observed_at": latest_iso,
            "age_days": age_days,
            "half_life_days": half_life,
            "floor_weight": float(spec["floor_weight"]),
            "drift_band": [float(drift_lo), float(drift_hi)],
            "retained_weight": retained_weight,
            "re_anchor_policy": str(spec["re_anchor_policy"]),
            "sources": list(spec["sources"]),
        })

    groups = [
        {
            "group": group,
            "count": group_counts.get(group, 0),
            "categories": [
                row["category"] for row in categories if row["group"] == group
            ],
        }
        for group in DECAY_MONITOR_GROUPS
    ]
    return {
        "schema": DECAY_MONITOR_SCHEMA,
        "observed_at": resolved_iso,
        "horizon_days": horizon,
        "total_categories": len(categories),
        "group_counts": group_counts,
        "groups": groups,
        "status_counts": status_counts,
        "categories": categories,
        "zero_write": True,
        "llm_calls": 0,
    }


def project_v02_runtime_migration(
    day_root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
    practice_service: Any = None,
    project_service: Any = None,
) -> dict[str, Any]:
    """100% zero-disk-write read projection."""
    root = Path(day_root)
    now_dt, resolved_iso = _resolve_now(now_iso)
    state_exists, state = _load_state_readonly(root)
    return _build_projected_payload(
        root,
        state=state,
        state_exists=state_exists,
        has_subject_capability=has_subject_capability,
        now_dt=now_dt,
        now_iso=resolved_iso,
        practice_service=practice_service,
        project_service=project_service,
    )


def _build_projected_payload(
    day_root: Path,
    *,
    state: Mapping[str, Any],
    state_exists: bool,
    has_subject_capability: bool,
    now_dt: datetime,
    now_iso: str,
    practice_service: Any = None,
    project_service: Any = None,
) -> dict[str, Any]:
    identity = _read_character_identity(day_root)
    runtime_mode_proj = _project_runtime_mode(state, day_root=day_root, now_dt=now_dt, now_iso=now_iso)
    cap_card_proj = _project_today_capability_card(state, runtime_mode_proj)
    suitcase_proj = _project_migration_suitcase(
        state,
        day_root=day_root,
        practice_service=practice_service,
        project_service=project_service,
    )
    freshness_proj = collect_source_freshness(
        day_root,
        now_iso=now_iso,
        state_exists=state_exists,
        state=state,
    )

    router = state["wake_router"]
    mode = str(runtime_mode_proj["mode"])
    cfg_pct = int(runtime_mode_proj["configured_budget_scale_pct"])
    base_h = int(router.get("hourly_wake_cap") or DEFAULT_HOURLY_WAKE_CAP)
    base_d = int(router.get("daily_wake_cap") or DEFAULT_DAILY_WAKE_CAP)
    eff_h, eff_d = _effective_wake_caps(mode, cfg_pct, base_h, base_d)
    wakes_h, wakes_d = _count_recent_background_wakes(
        router.get("background_wake_timestamps") or [],
        now_dt,
    )

    active_wins_proj: dict[str, dict[str, Any]] = {}
    for fam, win in (router.get("active_family_windows") or {}).items():
        if not isinstance(win, Mapping):
            continue
        exp_dt = _parse_iso(win.get("expires_at"))
        if exp_dt is None or now_dt < exp_dt:
            active_wins_proj[str(fam)] = copy.deepcopy(dict(win))

    # 07 章 §二：只读对账投影——近期票清单（folded 计数、est_tokens 合计）。
    # lease 已过的 open 票在此按当前时刻就地定期为 expired（仅内存副本，零写入），
    # 断电恢复后第一眼就能看出哪些延期票从未被消费。
    tickets_proj = [
        dict(t) for t in (router.get("wake_tickets") or [])[-MAX_WAKE_TICKETS:]
        if isinstance(t, Mapping)
    ]
    _finalize_expired_wake_tickets_at(tickets_proj, now_dt)
    wake_tickets_summary_proj = _wake_tickets_summary(tickets_proj)

    wake_coalescer_proj = {
        "hourly_wake_cap": base_h,
        "daily_wake_cap": base_d,
        "effective_hourly_wake_cap": eff_h,
        "effective_daily_wake_cap": eff_d,
        "coalesce_window_seconds": int(router.get("coalesce_window_seconds") or DEFAULT_COALESCE_WINDOW_SECONDS),
        "background_wakes_hour": wakes_h,
        "background_wakes_day": wakes_d,
        "effective_wakes_total": int(router.get("effective_wakes_total") or 0),
        "control_wakes_total": int(router.get("control_wakes_total") or 0),
        "control_lane_bypasses": int(router.get("control_lane_bypasses") or 0),
        "foreground_chat_bypasses": int(router.get("foreground_chat_bypasses") or 0),
        "coalesced_saved_wakes": int(router.get("coalesced_saved_wakes") or 0),
        "quota_blocked_count": int(router.get("quota_blocked_count") or 0),
        "foreground_and_control_guaranteed": True,
        "active_family_windows": active_wins_proj,
        "recent_wake_attributions": copy.deepcopy(router.get("recent_wake_attributions") or []),
        "wake_tickets": tickets_proj,
        "wake_tickets_summary": wake_tickets_summary_proj,
        "last_probe_result": copy.deepcopy(router.get("last_probe_result")),
    }

    audits_proj = _redact_workspace_audits_for_perspective(
        state.get("workspace_audits") or [],
        has_subject_capability=has_subject_capability,
    )
    vc_status_proj = _project_vc_authority_status(day_root)
    device_presence_proj = _project_device_presence(state, now_dt=now_dt)
    retirement_status_proj = _project_retirement_status(state)
    proactive_outbound_proj = _project_proactive_outbound(state, now_dt=now_dt)
    chatless_review_proj = project_chatless_life_review(
        day_root,
        has_subject_capability=has_subject_capability,
        now_iso=now_iso,
    )

    return {
        "schema": VIEW_SCHEMA,
        "endpoint": ENDPOINT_PATH,
        "state_exists": bool(state_exists),
        "perspective": "her_private" if has_subject_capability else "partner_default",
        "revision": int(state.get("revision") or 0),
        "observed_at": now_iso,
        "identity": identity,
        "runtime_mode": runtime_mode_proj,
        "wake_coalescer": wake_coalescer_proj,
        "today_capability_card": cap_card_proj,
        "last_migration_verdict": cap_card_proj["last_migration_verdict"],
        "migration_suitcase": suitcase_proj,
        "migration_loss_manifest": copy.deepcopy(suitcase_proj["migration_loss_manifest"]),
        "source_freshness": freshness_proj,
        "workspace_audits": audits_proj,
        "annual_decay_matrix_22": project_annual_decay_matrix_22(
            day_root, now_iso=now_iso
        ),
        "vc_authority_status": vc_status_proj,
        "device_presence": device_presence_proj,
        "retirement_status": retirement_status_proj,
        "proactive_outbound": proactive_outbound_proj,
        "chatless_life_review": chatless_review_proj,
        "llm_calls": 0,
    }


def _canonical_payload_fingerprint(payload: Mapping[str, Any], *, actor_side: str) -> str:
    filtered = {
        k: v
        for k, v in sorted(payload.items())
        if k != "operation_key"
    }
    blob = json.dumps({"actor_side": actor_side, "payload": filtered}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _build_duplicate_projection(
    root: Path,
    *,
    state: Mapping[str, Any],
    state_exists: bool,
    action: str,
    actor_side: str,
    payload: Mapping[str, Any],
    ledger_entry: Mapping[str, Any],
    now_dt: datetime,
    now_iso: str,
) -> dict[str, Any]:
    projected = _build_projected_payload(
        root,
        state=state,
        state_exists=state_exists,
        has_subject_capability=(actor_side == "subject"),
        now_dt=now_dt,
        now_iso=now_iso,
    )
    cached_extra = ledger_entry.get("response_extra")
    if isinstance(cached_extra, Mapping):
        for ek, ev in cached_extra.items():
            projected[ek] = copy.deepcopy(ev)
    elif action == "export_suitcase":
        exp_bundle, exp_summary = _build_export_suitcase_bundle(
            root, state=state, payload=payload, now_iso=now_iso
        )
        projected["suitcase_bundle"] = exp_bundle
        projected["summary"] = exp_summary
        projected["migration_loss_manifest"] = copy.deepcopy(exp_summary["migration_loss_manifest"])
    return projected


def _run_state_only_action(
    root: Path,
    *,
    state: dict[str, Any],
    action: str,
    payload: Mapping[str, Any],
    now_dt: datetime,
    now_iso: str,
    actor_side: str = "partner",
) -> Optional[dict[str, Any]]:
    if action == "set_budget_mode":
        _apply_set_budget_mode(state, payload, now_dt=now_dt, now_iso=now_iso)
    elif action == "record_carrier_check":
        _apply_record_carrier_check(state, payload, now_iso=now_iso)
    elif action == "pack_selective_suitcase":
        _apply_pack_selective_suitcase(state, payload, now_iso=now_iso, day_root=root)
    elif action == "coalesce_probe":
        _route_wake_event_into_state(
            state,
            family=str(payload["family"]),
            summary_cn=str(payload["summary_cn"]),
            is_control=bool(payload.get("is_control", False)),
            lane="control" if bool(payload.get("is_control", False)) else "background",
            now_dt=now_dt,
            now_iso=now_iso,
            day_root=root,
        )
    elif action == "reverify_migrated_skill":
        _apply_reverify_migrated_skill(state, payload, now_iso=now_iso)
    elif action == "record_workspace_audit":
        _apply_record_workspace_audit(state, payload, now_iso=now_iso)
    elif action == "claim_device_presence_lease":
        _apply_claim_device_presence_lease(state, payload, now_dt=now_dt, now_iso=now_iso)
    elif action == "handoff_device_presence_lease":
        _apply_handoff_device_presence_lease(state, payload, now_dt=now_dt, now_iso=now_iso)
    elif action == "prepare_dignified_retirement":
        return _apply_prepare_dignified_retirement(
            root, state, payload, actor_side=actor_side, now_iso=now_iso
        )
    elif action == "cancel_dignified_retirement":
        return _apply_cancel_dignified_retirement(state)
    elif action == "confirm_dignified_retirement":
        return _apply_confirm_dignified_retirement(
            root, state, payload, actor_side=actor_side, now_dt=now_dt, now_iso=now_iso
        )
    elif action == "configure_proactive_outbound":
        return _apply_configure_proactive_outbound(
            state, payload, actor_side=actor_side, now_dt=now_dt, day_root=root, now_iso=now_iso
        )
    elif action == "enqueue_proactive_message":
        return _apply_enqueue_proactive_message(
            state, payload, actor_side=actor_side, now_dt=now_dt, now_iso=now_iso, day_root=root
        )
    elif action == "acknowledge_or_cancel_proactive_message":
        return _apply_acknowledge_or_cancel_proactive_message(
            state, payload, actor_side=actor_side, now_iso=now_iso, day_root=root
        )
    else:
        raise V02ValidationError("INVALID_FIELDS", f"Unsupported action: {action!r}")
    return None


def _on_carrier_model_switched(
    root: Path,
    *,
    from_model_id: str,
    to_model_id: str,
    actor_side: str,
    now_iso: str,
) -> None:
    """Post-commit hook on real model switch: bump existing skill provenance & open preference recheck pack."""
    if not from_model_id or not to_model_id or from_model_id == to_model_id:
        return

    # 1. Only bump skill provenance if state/v02_autobiography_skill_provenance.json already exists
    #    and has active releases registered under from_model_id.
    prov_path = root / "state" / "v02_autobiography_skill_provenance.json"
    if prov_path.is_file() and not prov_path.is_symlink():
        try:
            try:
                from components.life.scripts import v02_autobiography_skill_provenance as _asp
            except ImportError:
                import v02_autobiography_skill_provenance as _asp  # type: ignore[no-redef]

            _p_exists, p_state = _asp._load_state_readonly(root)
            if _p_exists:
                releases = (p_state.get("skills") or {}).get("releases") or {}
                tool_versions = sorted({
                    str(rec.get("tool_version") or "")
                    for rec in releases.values()
                    if isinstance(rec, Mapping)
                    and rec.get("model_id") == from_model_id
                    and rec.get("status") != _asp.SKILL_STATUS_RETIRED
                    and str(rec.get("tool_version") or "").strip()
                })
                for tv in tool_versions:
                    _asp.bump_carrier_or_tool_version(
                        root,
                        {
                            "from_model_id": from_model_id,
                            "to_model_id": to_model_id,
                            "from_tool_version": tv,
                            "to_tool_version": tv,
                        },
                        now_iso=now_iso,
                    )
        except Exception:
            pass

    # 2. Auto-create a Cross-Model Preference Recheck Pack in v02_personality_drift_recheck.json.
    try:
        try:
            from components.life.scripts import v02_personality_drift_and_model_recheck as _drift
        except ImportError:
            import v02_personality_drift_and_model_recheck as _drift  # type: ignore[no-redef]

        _drift.create_model_preference_recheck_pack(
            root,
            {
                "from_model_id": from_model_id,
                "to_model_id": to_model_id,
                "note_cn": f"模型从 {from_model_id} 切换为 {to_model_id} 后自动生成偏好复核包",
            },
            actor_side=actor_side,
            now_iso=now_iso,
        )
    except Exception:
        pass


def apply_runtime_migration_mutation(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str,
    now_iso: Optional[str] = None,
) -> tuple[dict[str, Any], str]:
    """Apply validated command, persist atomically, and return ``(projected, command_status)``."""
    root = Path(day_root)
    now_dt, resolved_iso = _resolve_now(now_iso)
    action = str(payload["action"]).strip()
    if action == "reconcile_backup_restore" and actor_side != "subject":
        raise V02PermissionError(
            "FORBIDDEN_ACTOR_SIDE",
            f"Action {action!r} is forbidden for actor_side={actor_side!r}",
        )

    op_key_raw = payload.get("operation_key")
    op_key = op_key_raw.strip() if isinstance(op_key_raw, str) and op_key_raw.strip() else None
    payload_fp = _canonical_payload_fingerprint(payload, actor_side=actor_side)

    # Read-only pre-flight before touching filesystem/lockfile so rejected 400/403/409 remain zero-write
    pre_exists, pre_state = _load_state_readonly(root)
    pre_ledger: Mapping[str, Any] = pre_state.get("operation_ledger") or {}
    if op_key is not None and op_key in pre_ledger:
        prev_pre = pre_ledger[op_key]
        if prev_pre.get("payload_sha256") != payload_fp:
            raise V02ConflictError(
                "OPERATION_KEY_CONFLICT",
                f"operation_key {op_key!r} already committed with different payload",
            )
        return (
            _build_duplicate_projection(
                root,
                state=pre_state,
                state_exists=pre_exists,
                action=action,
                actor_side=actor_side,
                payload=payload,
                ledger_entry=prev_pre,
                now_dt=now_dt,
                now_iso=resolved_iso,
            ),
            "DUPLICATE",
        )

    if action == "export_suitcase":
        _build_export_suitcase_bundle(root, state=copy.deepcopy(pre_state), payload=payload, now_iso=resolved_iso)
    elif action == "import_suitcase":
        _apply_import_suitcase(
            root, state=copy.deepcopy(pre_state), payload=payload, now_iso=resolved_iso, dry_run=True
        )
    elif action == "reconcile_backup_restore":
        if root.is_symlink():
            raise V02ValidationError("SYMLINK_TARGET_FORBIDDEN", "day_root cannot be a symlink")
        _normalize_external_watermark_seal(
            payload.get("external_watermark_seal"), default_now_iso=resolved_iso
        )
        _assert_not_symlink_chain(
            root, tuple(VC_AUTHORITY_WATERMARK_REL_PATH), label="/".join(VC_AUTHORITY_WATERMARK_REL_PATH)
        )
        _assert_not_symlink_chain(
            root, (STATE_DIRNAME, RUNTIME_GOVERNANCE_FILENAME), label=f"{STATE_DIRNAME}/{RUNTIME_GOVERNANCE_FILENAME}"
        )
    else:
        _run_state_only_action(
            root,
            state=copy.deepcopy(pre_state),
            action=action,
            payload=payload,
            now_dt=now_dt,
            now_iso=resolved_iso,
            actor_side=actor_side,
        )

    with _tx.state_file_lock(_state_file_path(root)):
        state_exists, state = _load_state_readonly(root)
        ledger: dict[str, dict[str, Any]] = state.setdefault("operation_ledger", {})

        if op_key is not None and op_key in ledger:
            prev = ledger[op_key]
            if prev.get("payload_sha256") != payload_fp:
                raise V02ConflictError(
                    "OPERATION_KEY_CONFLICT",
                    f"operation_key {op_key!r} already committed with different payload",
                )
            return (
                _build_duplicate_projection(
                    root,
                    state=state,
                    state_exists=state_exists,
                    action=action,
                    actor_side=actor_side,
                    payload=payload,
                    ledger_entry=prev,
                    now_dt=now_dt,
                    now_iso=resolved_iso,
                ),
                "DUPLICATE",
            )

        extra_projection_fields: dict[str, Any] = {}
        carrier_prev_model_id: Optional[str] = None
        carrier_new_model_id: Optional[str] = None

        if action == "export_suitcase":
            exp_bundle, exp_summary = _apply_export_suitcase(
                root, state=state, payload=payload, now_iso=resolved_iso
            )
            extra_projection_fields["suitcase_bundle"] = exp_bundle
            extra_projection_fields["summary"] = exp_summary
            extra_projection_fields["migration_loss_manifest"] = copy.deepcopy(
                exp_summary["migration_loss_manifest"]
            )
        elif action == "import_suitcase":
            imp_receipt = _apply_import_suitcase(
                root, state=state, payload=payload, now_iso=resolved_iso
            )
            extra_projection_fields["import_receipt"] = imp_receipt
            extra_projection_fields["migration_loss_manifest"] = copy.deepcopy(
                imp_receipt["migration_loss_manifest"]
            )
        elif action == "reconcile_backup_restore":
            rec_receipt = _apply_reconcile_backup_restore_vc_barrier(
                root,
                external_watermark_seal=payload.get("external_watermark_seal"),
                now_iso=resolved_iso,
            )
            extra_projection_fields["vc_reconcile_receipt"] = rec_receipt
        else:
            if action == "record_carrier_check":
                prev_fp_before = (
                    (state.get("carrier_migration") or {}).get("carrier_fingerprint")
                    or DEFAULT_CARRIER_FINGERPRINT
                )
                carrier_prev_model_id = str(prev_fp_before.get("model_id") or "")
            state_receipt = _run_state_only_action(
                root,
                state=state,
                action=action,
                payload=payload,
                now_dt=now_dt,
                now_iso=resolved_iso,
                actor_side=actor_side,
            )
            if isinstance(state_receipt, Mapping):
                extra_projection_fields["action_receipt"] = copy.deepcopy(dict(state_receipt))
            if action == "record_carrier_check":
                new_fp_after = (state.get("carrier_migration") or {}).get("carrier_fingerprint") or {}
                carrier_new_model_id = str(new_fp_after.get("model_id") or "")

        state["revision"] = int(state.get("revision") or 0) + 1
        state["updated_at"] = resolved_iso

        if op_key is not None:
            ledger_entry: dict[str, Any] = {
                "action": action,
                "actor_side": actor_side,
                "payload_sha256": payload_fp,
                "recorded_at": resolved_iso,
                "revision": state["revision"],
            }
            if "import_receipt" in extra_projection_fields:
                ledger_entry["response_extra"] = {
                    "import_receipt": copy.deepcopy(extra_projection_fields["import_receipt"]),
                    "migration_loss_manifest": copy.deepcopy(
                        extra_projection_fields["migration_loss_manifest"]
                    ),
                }
            elif "vc_reconcile_receipt" in extra_projection_fields:
                ledger_entry["response_extra"] = {
                    "vc_reconcile_receipt": copy.deepcopy(
                        extra_projection_fields["vc_reconcile_receipt"]
                    ),
                }
            elif "action_receipt" in extra_projection_fields:
                ledger_entry["response_extra"] = {
                    "action_receipt": copy.deepcopy(extra_projection_fields["action_receipt"]),
                }
            ledger[op_key] = ledger_entry
            if len(ledger) > MAX_OPERATION_LEDGER:
                oldest_keys = list(ledger.keys())[: len(ledger) - MAX_OPERATION_LEDGER]
                for ok_del in oldest_keys:
                    ledger.pop(ok_del, None)

        _save_state_atomic(root, state)

    if (
        action == "record_carrier_check"
        and carrier_prev_model_id
        and carrier_new_model_id
        and carrier_prev_model_id != carrier_new_model_id
    ):
        _on_carrier_model_switched(
            root,
            from_model_id=carrier_prev_model_id,
            to_model_id=carrier_new_model_id,
            actor_side=actor_side,
            now_iso=resolved_iso,
        )

    if action in ("set_budget_mode", "confirm_dignified_retirement"):
        try:
            from components.phone import living as phone_living

            mc = state["mode_config"]
            phone_living.sync_governance_mode(
                root,
                str(mc.get("mode") or MODE_NORMAL),
                budget_scale_pct=int(mc.get("budget_scale_pct") or 100),
                reason=str(mc.get("reason_cn") or ""),
            )
        except Exception:
            pass

    projected = _build_projected_payload(
        root,
        state=state,
        state_exists=True,
        has_subject_capability=(actor_side == "subject"),
        now_dt=now_dt,
        now_iso=resolved_iso,
    )
    for ek, ev in extra_projection_fields.items():
        projected[ek] = ev
    return projected, "APPLIED"


def _strict_non_bool_int(raw: Any, *, field_name: str, minimum: int = 0, maximum: Optional[int] = None) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise V02ValidationError("INVALID_FIELDS", f"{field_name} must be an integer")
    if raw < minimum:
        raise V02ValidationError("INVALID_FIELDS", f"{field_name} must be >= {minimum}")
    if maximum is not None and raw > maximum:
        raise V02ValidationError("INVALID_FIELDS", f"{field_name} must be <= {maximum}")
    return raw


def _default_device_presence_state() -> dict[str, Any]:
    return {
        "schema": DEVICE_PRESENCE_SCHEMA,
        "lease_epoch": 0,
        "active_device_id": None,
        "continuation_ref": None,
        "lease_seconds": DEFAULT_DEVICE_LEASE_SECONDS,
        "leased_at": None,
        "expires_at": None,
        "last_handoff_at": None,
        "last_handoff_from": None,
        "last_handoff_to": None,
        "handoff_history": [],
    }


def _project_device_presence(state: Mapping[str, Any], *, now_dt: datetime) -> dict[str, Any]:
    raw_dp = state.get("device_presence")
    dp = dict(raw_dp) if isinstance(raw_dp, Mapping) else _default_device_presence_state()
    epoch = max(0, int(dp.get("lease_epoch") or 0))
    active_dev_raw = dp.get("active_device_id")
    active_dev = active_dev_raw.strip() if isinstance(active_dev_raw, str) and active_dev_raw.strip() else None
    cont_raw = dp.get("continuation_ref")
    cont_ref = cont_raw.strip() if isinstance(cont_raw, str) and cont_raw.strip() else None
    lease_seconds = max(1, int(dp.get("lease_seconds") or DEFAULT_DEVICE_LEASE_SECONDS))
    leased_at_raw = dp.get("leased_at")
    leased_at = leased_at_raw.strip() if isinstance(leased_at_raw, str) and leased_at_raw.strip() else None
    expires_at_raw = dp.get("expires_at")
    expires_at = expires_at_raw.strip() if isinstance(expires_at_raw, str) and expires_at_raw.strip() else None

    expires_dt = _parse_iso(expires_at)
    remaining_seconds = 0
    lease_active = False
    if active_dev is not None:
        if expires_dt is None:
            lease_active = True
        else:
            delta_s = int((expires_dt - now_dt).total_seconds())
            if delta_s > 0:
                lease_active = True
                remaining_seconds = delta_s

    history_raw = dp.get("handoff_history")
    history = [
        dict(entry)
        for entry in (history_raw if isinstance(history_raw, list) else [])[-MAX_DEVICE_HANDOFF_HISTORY:]
        if isinstance(entry, Mapping)
    ]

    return {
        "schema": DEVICE_PRESENCE_SCHEMA,
        "lease_epoch": epoch,
        "active_device_id": active_dev,
        "continuation_ref": cont_ref,
        "lease_seconds": lease_seconds,
        "leased_at": leased_at,
        "expires_at": expires_at,
        "lease_active": lease_active,
        "lease_expired": bool(active_dev is not None and not lease_active),
        "remaining_seconds": remaining_seconds,
        "last_handoff_at": dp.get("last_handoff_at"),
        "last_handoff_from": dp.get("last_handoff_from"),
        "last_handoff_to": dp.get("last_handoff_to"),
        "handoff_history": history,
        "epoch_guard_enforced": True,
        "llm_calls": 0,
    }


def _apply_claim_device_presence_lease(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    *,
    now_dt: datetime,
    now_iso: str,
) -> None:
    raw_dev = payload.get("device_id")
    if not isinstance(raw_dev, str) or not raw_dev.strip() or len(raw_dev.strip()) > 128:
        raise V02ValidationError("INVALID_FIELDS", "device_id must be a non-empty string (<=128 chars)")
    device_id = raw_dev.strip()

    raw_ls = payload.get("lease_seconds")
    if raw_ls is None:
        lease_seconds = DEFAULT_DEVICE_LEASE_SECONDS
    else:
        lease_seconds = _strict_non_bool_int(
            raw_ls, field_name="lease_seconds", minimum=1, maximum=MAX_DEVICE_LEASE_SECONDS
        )

    dp = state.get("device_presence")
    if not isinstance(dp, dict):
        dp = _default_device_presence_state()
        state["device_presence"] = dp

    active_epoch = max(0, int(dp.get("lease_epoch") or 0))
    if "expected_epoch" in payload and payload.get("expected_epoch") is not None:
        expected_epoch = _strict_non_bool_int(payload.get("expected_epoch"), field_name="expected_epoch", minimum=0)
        if expected_epoch < active_epoch:
            raise V02ConflictError(
                "STALE_DEVICE_EPOCH",
                f"expected_epoch={expected_epoch} is older than active lease_epoch={active_epoch}",
                details={"expected_epoch": expected_epoch, "active_lease_epoch": active_epoch},
            )
        if expected_epoch > active_epoch:
            raise V02ConflictError(
                "DEVICE_EPOCH_MISMATCH",
                f"expected_epoch={expected_epoch} does not match active lease_epoch={active_epoch}",
                details={"expected_epoch": expected_epoch, "active_lease_epoch": active_epoch},
            )

    prev_device = dp.get("active_device_id")
    if "continuation_ref" in payload:
        raw_cont = payload.get("continuation_ref")
        if raw_cont is None:
            continuation_ref = None
        elif isinstance(raw_cont, str):
            continuation_ref = raw_cont.strip()[:240] or None
        else:
            raise V02ValidationError("INVALID_FIELDS", "continuation_ref must be a string or null")
    else:
        prev_cont = dp.get("continuation_ref")
        continuation_ref = prev_cont.strip()[:240] if isinstance(prev_cont, str) and prev_cont.strip() else None

    new_epoch = active_epoch + 1
    expires_iso = (now_dt + timedelta(seconds=lease_seconds)).isoformat(timespec="seconds")
    dp["schema"] = DEVICE_PRESENCE_SCHEMA
    dp["lease_epoch"] = new_epoch
    dp["active_device_id"] = device_id
    dp["continuation_ref"] = continuation_ref
    dp["lease_seconds"] = lease_seconds
    dp["leased_at"] = now_iso
    dp["expires_at"] = expires_iso

    history = list(dp.get("handoff_history") or [])
    history.append(
        {
            "event": "claim",
            "from_device_id": prev_device,
            "to_device_id": device_id,
            "lease_epoch": new_epoch,
            "continuation_ref": continuation_ref,
            "at": now_iso,
        }
    )
    dp["handoff_history"] = history[-MAX_DEVICE_HANDOFF_HISTORY:]


def _apply_handoff_device_presence_lease(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    *,
    now_dt: datetime,
    now_iso: str,
) -> None:
    raw_from = payload.get("from_device_id")
    raw_to = payload.get("to_device_id")
    if not isinstance(raw_from, str) or not raw_from.strip() or len(raw_from.strip()) > 128:
        raise V02ValidationError("INVALID_FIELDS", "from_device_id must be a non-empty string")
    if not isinstance(raw_to, str) or not raw_to.strip() or len(raw_to.strip()) > 128:
        raise V02ValidationError("INVALID_FIELDS", "to_device_id must be a non-empty string")
    from_device_id = raw_from.strip()
    to_device_id = raw_to.strip()

    if "lease_epoch" not in payload or payload.get("lease_epoch") is None:
        raise V02ValidationError("INVALID_FIELDS", "lease_epoch is required for handoff_device_presence_lease")
    req_epoch = _strict_non_bool_int(payload.get("lease_epoch"), field_name="lease_epoch", minimum=0)

    dp = state.get("device_presence")
    if not isinstance(dp, dict):
        dp = _default_device_presence_state()
        state["device_presence"] = dp

    active_epoch = max(0, int(dp.get("lease_epoch") or 0))
    if req_epoch < active_epoch:
        raise V02ConflictError(
            "STALE_DEVICE_EPOCH",
            f"lease_epoch={req_epoch} is older than active lease_epoch={active_epoch}",
            details={"lease_epoch": req_epoch, "active_lease_epoch": active_epoch},
        )
    if req_epoch > active_epoch:
        raise V02ConflictError(
            "DEVICE_EPOCH_MISMATCH",
            f"lease_epoch={req_epoch} does not match active lease_epoch={active_epoch}",
            details={"lease_epoch": req_epoch, "active_lease_epoch": active_epoch},
        )

    active_device_raw = dp.get("active_device_id")
    active_device = active_device_raw.strip() if isinstance(active_device_raw, str) and active_device_raw.strip() else None
    if active_device is None:
        raise V02ConflictError(
            "NO_ACTIVE_DEVICE_LEASE",
            "Cannot handoff device presence before an active lease is claimed",
        )
    if active_device != from_device_id:
        raise V02ConflictError(
            "DEVICE_LEASE_OWNER_MISMATCH",
            f"Active lease belongs to {active_device!r}, not {from_device_id!r}",
            details={"active_device_id": active_device, "from_device_id": from_device_id},
        )

    raw_ls = payload.get("lease_seconds")
    if raw_ls is None:
        lease_seconds = max(1, int(dp.get("lease_seconds") or DEFAULT_DEVICE_LEASE_SECONDS))
    else:
        lease_seconds = _strict_non_bool_int(
            raw_ls, field_name="lease_seconds", minimum=1, maximum=MAX_DEVICE_LEASE_SECONDS
        )

    if "continuation_ref" in payload:
        raw_cont = payload.get("continuation_ref")
        if raw_cont is None:
            continuation_ref = None
        elif isinstance(raw_cont, str):
            continuation_ref = raw_cont.strip()[:240] or None
        else:
            raise V02ValidationError("INVALID_FIELDS", "continuation_ref must be a string or null")
    else:
        prev_cont = dp.get("continuation_ref")
        continuation_ref = prev_cont.strip()[:240] if isinstance(prev_cont, str) and prev_cont.strip() else None

    new_epoch = active_epoch + 1
    expires_iso = (now_dt + timedelta(seconds=lease_seconds)).isoformat(timespec="seconds")
    dp["schema"] = DEVICE_PRESENCE_SCHEMA
    dp["lease_epoch"] = new_epoch
    dp["active_device_id"] = to_device_id
    dp["continuation_ref"] = continuation_ref
    dp["lease_seconds"] = lease_seconds
    dp["leased_at"] = now_iso
    dp["expires_at"] = expires_iso
    dp["last_handoff_at"] = now_iso
    dp["last_handoff_from"] = from_device_id
    dp["last_handoff_to"] = to_device_id

    history = list(dp.get("handoff_history") or [])
    history.append(
        {
            "event": "handoff",
            "from_device_id": from_device_id,
            "to_device_id": to_device_id,
            "lease_epoch": new_epoch,
            "continuation_ref": continuation_ref,
            "at": now_iso,
        }
    )
    dp["handoff_history"] = history[-MAX_DEVICE_HANDOFF_HISTORY:]


def _project_vc_authority_status(day_root: Path) -> dict[str, Any]:
    """Read-only summary of G0 V-C anti-resurrection watermark (zero writes, zero plaintext)."""
    wm_rel_parts = tuple(VC_AUTHORITY_WATERMARK_REL_PATH)
    wm = read_vc_authority_watermark_readonly(day_root)
    ext_root_raw = str(os.environ.get("NYAIRO_V02_CONTROL_ROOT") or "").strip()
    candidate_roots = [Path(ext_root_raw), day_root] if ext_root_raw else [day_root]
    wm_exists = any(
        _is_safe_nonsymlink_rel(cand_root, wm_rel_parts)
        and cand_root.joinpath(*wm_rel_parts).is_file()
        for cand_root in candidate_roots
    )
    sealed = wm.get("sealed_digests")
    sealed_count = len(sealed) if isinstance(sealed, Mapping) else 0
    return {
        "schema": VC_AUTHORITY_SCHEMA,
        "watermark_rel_path": "/".join(wm_rel_parts),
        "watermark_exists": wm_exists,
        "authority_generation": int(wm.get("authority_generation") or 0),
        "restore_epoch": int(wm.get("restore_epoch") or 0),
        "sealed_digest_count": sealed_count,
        "enforced": True,
        "plaintext_stored": False,
        "llm_calls": 0,
    }


def _runtime_governance_candidate_paths(day_root: Path) -> tuple[Path, ...]:
    return (
        day_root / STATE_DIRNAME / RUNTIME_GOVERNANCE_FILENAME,
        day_root / "governance" / RUNTIME_GOVERNANCE_FILENAME,
        day_root / RUNTIME_GOVERNANCE_FILENAME,
    )


def _read_governance_tombstones_readonly(day_root: Path, *, now_iso: str) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for cand in _runtime_governance_candidate_paths(day_root):
        try:
            if cand.is_symlink():
                continue
        except OSError:
            continue
        ex, ok, doc = _read_json_safe(cand)
        if not ex or not ok or not isinstance(doc, Mapping):
            continue
        for k in ("tombstones", "tombstone_ledger", "revocations"):
            if k in doc:
                merged.update(_normalize_tombstone_entries_from_any(doc.get(k), default_now_iso=now_iso))
    return merged


def _read_controls_db_revoked_ids_readonly(day_root: Path) -> dict[str, str]:
    """Read revoked/deleted target event IDs from SQLite controls.db in strict read-only URI mode."""
    revoked: dict[str, str] = {}
    for rel in (("memory", "controls.db"), ("controls.db",)):
        if not _is_safe_nonsymlink_rel(day_root, rel):
            continue
        db_path = day_root.joinpath(*rel)
        if not db_path.is_file():
            continue
        conn: Optional[sqlite3.Connection] = None
        try:
            conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
            cur = conn.cursor()
            tables = {
                str(r[0])
                for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                if r and r[0]
            }
            if "memory_controls" in tables:
                cols = {
                    str(r[1])
                    for r in cur.execute("PRAGMA table_info(memory_controls)").fetchall()
                    if len(r) > 1
                }
                if {"kind", "target_event"}.issubset(cols):
                    sel = "SELECT target_event, created_at FROM memory_controls WHERE UPPER(COALESCE(kind,''))='DELETE'" if "created_at" in cols else "SELECT target_event, '' FROM memory_controls WHERE UPPER(COALESCE(kind,''))='DELETE'"
                    for tgt, ts in cur.execute(sel).fetchall():
                        clean = str(tgt or "").strip()
                        if clean:
                            revoked.setdefault(clean, str(ts or "").strip())
            if "memory_erasures" in tables:
                cols = {
                    str(r[1])
                    for r in cur.execute("PRAGMA table_info(memory_erasures)").fetchall()
                    if len(r) > 1
                }
                if "event_ids_json" in cols:
                    sel = "SELECT event_ids_json, created_at FROM memory_erasures" if "created_at" in cols else "SELECT event_ids_json, '' FROM memory_erasures"
                    for raw_json, ts in cur.execute(sel).fetchall():
                        try:
                            parsed = json.loads(str(raw_json or "[]"))
                        except ValueError:
                            continue
                        if isinstance(parsed, list):
                            for eid in parsed:
                                clean = str(eid or "").strip()
                                if clean:
                                    revoked.setdefault(clean, str(ts or "").strip())
        except sqlite3.Error:
            pass
        finally:
            if conn is not None:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
    return revoked


def _sync_runtime_governance_files(
    day_root: Path,
    *,
    tombstones_map: Mapping[str, Mapping[str, Any]],
    authority_generation: int,
    restore_epoch: int,
    now_iso: str,
) -> list[str]:
    """Atomically sync tombstones and V-C epoch metadata into v02_runtime_governance.json files."""
    primary_rel = (STATE_DIRNAME, RUNTIME_GOVERNANCE_FILENAME)
    _assert_not_symlink_chain(day_root, primary_rel, label="/".join(primary_rel))
    primary_path = day_root.joinpath(*primary_rel)
    candidates: list[tuple[tuple[str, ...], Path]] = [(primary_rel, primary_path)]
    for extra_rel in (("governance", RUNTIME_GOVERNANCE_FILENAME), (RUNTIME_GOVERNANCE_FILENAME,)):
        p = day_root.joinpath(*extra_rel)
        if _is_safe_nonsymlink_rel(day_root, extra_rel) and p.is_file():
            candidates.append((extra_rel, p))

    synced: list[str] = []
    with _tx.state_file_lock(primary_path):
        for rel_parts, target_path in candidates:
            _assert_not_symlink_chain(day_root, rel_parts, label="/".join(rel_parts))
            ex, ok, existing = _read_json_safe(target_path)
            doc = copy.deepcopy(existing) if (ex and ok and isinstance(existing, Mapping)) else {
                "schema": RUNTIME_GOVERNANCE_SCHEMA,
                "tombstones": {},
            }
            if not doc.get("schema"):
                doc["schema"] = RUNTIME_GOVERNANCE_SCHEMA
            existing_ts = _normalize_tombstone_entries_from_any(
                doc.get("tombstones"), default_now_iso=now_iso
            )
            for mid, entry in tombstones_map.items():
                if not isinstance(mid, str) or not mid.strip():
                    continue
                norm_mid = mid.strip()
                existing_ts[norm_mid] = {
                    "memory_id": norm_mid,
                    "vc_digest": str(entry.get("vc_digest") or compute_vc_item_digest(norm_mid)),
                    "reason": str(entry.get("reason") or "user_revoked").strip()[:120],
                    "tombstoned_at": str(entry.get("tombstoned_at") or now_iso).strip()[:40],
                    "tombstoned": True,
                }
            doc["tombstones"] = existing_ts
            doc["vc_authority_generation"] = int(authority_generation)
            doc["vc_restore_epoch"] = int(restore_epoch)
            doc["updated_at"] = now_iso
            _tx.atomic_write_json(target_path, doc)
            synced.append("/".join(rel_parts))
    return synced


def record_revocation_tombstone(
    day_root: Path | str,
    item_id: str,
    *,
    reason: str = "user_revoked",
    scope: str = "governance",
    now_iso: Optional[str] = None,
    now: Optional[str] = None,
) -> dict[str, Any]:
    """Record a governance revocation tombstone and persist its irreversible V-C watermark seal."""
    root = Path(day_root)
    if root.is_symlink():
        raise V02ValidationError("SYMLINK_TARGET_FORBIDDEN", "day_root cannot be a symlink")
    if not isinstance(item_id, str) or not item_id.strip():
        raise V02ValidationError("INVALID_FIELDS", "item_id must be a non-empty string")
    clean_id = item_id.strip()
    clean_reason = str(reason or "user_revoked").strip()[:120] or "user_revoked"
    eff_now = now_iso if now_iso is not None else now
    _, resolved_iso = _resolve_now(eff_now)

    seal_receipt = record_vc_revocation_seal(
        root,
        clean_id,
        revoked_at=resolved_iso,
        scope=scope,
    )

    gov_entry = {
        clean_id: {
            "memory_id": clean_id,
            "vc_digest": seal_receipt["digest"],
            "reason": clean_reason,
            "tombstoned_at": resolved_iso,
            "tombstoned": True,
        }
    }
    synced_paths = _sync_runtime_governance_files(
        root,
        tombstones_map=gov_entry,
        authority_generation=int(seal_receipt["authority_generation"]),
        restore_epoch=int(seal_receipt["restore_epoch"]),
        now_iso=resolved_iso,
    )
    return {
        "schema": RUNTIME_GOVERNANCE_SCHEMA,
        "status": "TOMBSTONED",
        "item_id": clean_id,
        "vc_digest": seal_receipt["digest"],
        "authority_generation": int(seal_receipt["authority_generation"]),
        "restore_epoch": int(seal_receipt["restore_epoch"]),
        "tombstoned_at": resolved_iso,
        "reason": clean_reason,
        "scope": seal_receipt["scope"],
        "watermark_rel_path": "/".join(VC_AUTHORITY_WATERMARK_REL_PATH),
        "synced_governance_paths": synced_paths,
        "llm_calls": 0,
    }


def _normalize_external_watermark_seal(
    raw_seal: Any,
    *,
    default_now_iso: str,
) -> tuple[dict[str, dict[str, Any]], int, int]:
    if raw_seal is None:
        return {}, 0, 0
    if not isinstance(raw_seal, Mapping):
        raise V02ValidationError("INVALID_FIELDS", "external_watermark_seal must be an object")

    ext_gen = _strict_non_bool_int(
        raw_seal.get("authority_generation", 0), field_name="authority_generation", minimum=0
    )
    ext_restore_epoch = _strict_non_bool_int(
        raw_seal.get("restore_epoch", 0), field_name="restore_epoch", minimum=0
    )

    raw_digests = raw_seal.get("sealed_digests")
    if raw_digests is None and "revoked_digests" in raw_seal:
        raw_digests = raw_seal.get("revoked_digests")

    out_digests: dict[str, dict[str, Any]] = {}
    if raw_digests is None:
        return out_digests, ext_gen, ext_restore_epoch

    if isinstance(raw_digests, Mapping):
        for raw_hex, meta in raw_digests.items():
            hex_str = str(raw_hex or "").strip().lower()
            if not _VC_DIGEST_HEX_RE.match(hex_str):
                raise V02ValidationError(
                    "INVALID_FIELDS", "external_watermark_seal sealed_digests keys must be 64-hex SHA-256 digests"
                )
            rev_at = default_now_iso
            sc = "memory"
            if isinstance(meta, Mapping):
                if isinstance(meta.get("revoked_at"), str) and meta["revoked_at"].strip():
                    rev_at = meta["revoked_at"].strip()[:40]
                if isinstance(meta.get("scope"), str) and meta["scope"].strip():
                    sc = _VC_SAFE_SCOPE_RE.sub("_", meta["scope"].strip())[:32] or "memory"
            out_digests[hex_str] = {"revoked_at": rev_at, "scope": sc}
    elif isinstance(raw_digests, list):
        for elem in raw_digests:
            if isinstance(elem, str):
                hex_str = elem.strip().lower()
                rev_at = default_now_iso
                sc = "memory"
            elif isinstance(elem, Mapping):
                hex_str = str(elem.get("digest") or elem.get("sha256") or "").strip().lower()
                rev_at = str(elem.get("revoked_at") or default_now_iso).strip()[:40]
                sc = _VC_SAFE_SCOPE_RE.sub("_", str(elem.get("scope") or "memory").strip())[:32] or "memory"
            else:
                raise V02ValidationError("INVALID_FIELDS", "Invalid external_watermark_seal digest entry")
            if not _VC_DIGEST_HEX_RE.match(hex_str):
                raise V02ValidationError(
                    "INVALID_FIELDS", "external_watermark_seal digests must be valid 64-hex SHA-256 strings"
                )
            out_digests[hex_str] = {"revoked_at": rev_at, "scope": sc}
    else:
        raise V02ValidationError("INVALID_FIELDS", "external_watermark_seal sealed_digests must be an object or list")

    return out_digests, ext_gen, ext_restore_epoch


def _collect_vc_scrub_targets(day_root: Path) -> list[tuple[tuple[str, ...], bool]]:
    """Ordered list of ``(rel_parts, allow_list_root)`` files subject to V-C anti-resurrection scrubbing."""
    seen: set[tuple[str, ...]] = set()
    targets: list[tuple[tuple[str, ...], bool]] = []

    def _add(rel_parts: tuple[str, ...], allow_list: bool = False) -> None:
        if rel_parts not in seen:
            seen.add(rel_parts)
            targets.append((rel_parts, allow_list))

    _add((STATE_DIRNAME, "v02_recall_projection.json"))
    _add(("memory", "v02_recall_projection_state.json"))
    _add(("memory", "v02_common_ground_state.json"))
    _add(("state", "v02_agency_depth_state.json"))
    _add((STATE_DIRNAME, "v02_agency_depth_state.json"))
    _add((STATE_DIRNAME, "v02_agency_depth.json"))
    _add((STATE_DIRNAME, "workbench_curio.json"))
    _add((STATE_DIRNAME, "workbench_tray.json"))
    _add((STATE_DIRNAME, "desk_tomorrow_note.json"))
    _add((STATE_DIRNAME, "longterm_gifts_lantern.json"))
    _add((STATE_DIRNAME, "outing_bench_bookstore.json"))
    _add((STATE_DIRNAME, "v02_crossmodal.json"))
    _add((STATE_DIRNAME, "character_pack_state.json"))

    for sname in SUITCASE_ALLOWED_SLICE_FILES:
        _add((STATE_DIRNAME, sname))
        for comp_rel in _SUITCASE_COMPANION_FALLBACKS.get(sname, ()):
            _add(tuple(comp_rel))
        for mirror_rel in _SUITCASE_CANONICAL_RESTORE_MIRRORS.get(sname, ()):
            _add(tuple(mirror_rel))

    for wname in SUITCASE_ALLOWED_WORLD_FILES:
        _add(("world", wname))
        for w_rel in _WORLD_SLICE_READ_CANDIDATES.get(wname, ()):
            _add(tuple(w_rel))
    world_dir = day_root / "world"
    if _is_safe_nonsymlink_rel(day_root, ("world",)) and world_dir.is_dir():
        try:
            for child in sorted(world_dir.iterdir(), key=lambda p: p.name):
                if child.suffix.lower() == ".json" and not child.is_symlink() and child.is_file():
                    _add(("world", child.name))
        except OSError:
            pass

    for pname in SUITCASE_ALLOWED_WORLD_PACKAGE_FILES:
        _add(("world-package", pname), allow_list=True)
    wp_dir = day_root / "world-package"
    if _is_safe_nonsymlink_rel(day_root, ("world-package",)) and wp_dir.is_dir():
        try:
            for child in sorted(wp_dir.iterdir(), key=lambda p: p.name):
                if child.suffix.lower() == ".json" and not child.is_symlink() and child.is_file():
                    _add(("world-package", child.name), allow_list=True)
        except OSError:
            pass

    return targets


def _recompute_recall_projection_doc_after_scrub(
    doc: dict[str, Any],
    *,
    merged_tombstones: Mapping[str, Mapping[str, Any]],
    now_iso: str,
) -> dict[str, Any]:
    existing_ts = _normalize_tombstone_entries_from_any(doc.get("tombstones"), default_now_iso=now_iso)
    for mid, entry in merged_tombstones.items():
        if isinstance(mid, str) and mid.strip():
            norm_mid = mid.strip()
            existing_ts[norm_mid] = {
                "memory_id": norm_mid,
                "reason": str(entry.get("reason") or "vc_barrier_reconciled").strip()[:120],
                "tombstoned_at": str(entry.get("tombstoned_at") or now_iso).strip()[:40],
                "tombstoned": True,
            }
    if existing_ts:
        doc["tombstones"] = existing_ts
    mems = doc.get("memories")
    if isinstance(mems, Mapping) and isinstance(doc.get("clarity_counts"), dict):
        counts = {"CRYSTAL": 0, "WARM": 0, "FADING": 0, "ARCHIVED": 0, "TOMBSTONED": len(existing_ts)}
        for m in mems.values():
            if isinstance(m, Mapping):
                tier = str(m.get("clarity_tier") or "WARM").upper()
                if tier in counts:
                    counts[tier] += 1
        doc["clarity_counts"] = counts
    doc["updated_at"] = now_iso
    return doc


def _apply_reconcile_backup_restore_vc_barrier(
    day_root: Path,
    *,
    external_watermark_seal: Any = None,
    now_iso: str,
) -> dict[str, Any]:
    if day_root.is_symlink():
        raise V02ValidationError("SYMLINK_TARGET_FORBIDDEN", "day_root cannot be a symlink")

    ext_digests, ext_gen, ext_restore_epoch = _normalize_external_watermark_seal(
        external_watermark_seal, default_now_iso=now_iso
    )

    wm_rel_parts = tuple(VC_AUTHORITY_WATERMARK_REL_PATH)
    _assert_not_symlink_chain(day_root, wm_rel_parts, label="/".join(wm_rel_parts))
    wm_path = day_root.joinpath(*wm_rel_parts)

    # Read-only gather of tombstones from local recall projection, local governance, and controls.db
    local_rp_ts = _extract_tombstone_ledger_readonly(day_root, now_iso=now_iso)
    local_gov_ts = _read_governance_tombstones_readonly(day_root, now_iso=now_iso)
    controls_db_revoked = _read_controls_db_revoked_ids_readonly(day_root)

    with _tx.state_file_lock(wm_path):
        local_wm = read_vc_authority_watermark_readonly(day_root)
        local_digests: dict[str, dict[str, Any]] = {}
        raw_local_sd = local_wm.get("sealed_digests")
        if isinstance(raw_local_sd, Mapping):
            for hx, meta in raw_local_sd.items():
                if _VC_DIGEST_HEX_RE.match(str(hx)):
                    m_map = meta if isinstance(meta, Mapping) else {}
                    hx_low = str(hx).lower()
                    local_digests[hx_low] = {
                        "digest": hx_low,
                        "revoked_at": str(m_map.get("revoked_at") or now_iso)[:40],
                        "scope": str(m_map.get("scope") or "memory")[:32],
                    }

        merged_digests: dict[str, dict[str, Any]] = dict(local_digests)
        for hx, meta in ext_digests.items():
            if hx not in merged_digests:
                merged_digests[hx] = {
                    "digest": hx,
                    "revoked_at": str(meta.get("revoked_at") or now_iso)[:40],
                    "scope": str(meta.get("scope") or "memory")[:32],
                }

        for tid, ts_entry in list(local_rp_ts.items()) + list(local_gov_ts.items()):
            hx = compute_vc_item_digest(tid)
            if hx not in merged_digests:
                merged_digests[hx] = {
                    "digest": hx,
                    "revoked_at": str(ts_entry.get("tombstoned_at") or now_iso)[:40],
                    "scope": "memory",
                }
        controls_db_digests = 0
        for cid, c_ts in controls_db_revoked.items():
            hx = compute_vc_item_digest(cid)
            if hx not in merged_digests:
                merged_digests[hx] = {
                    "digest": hx,
                    "revoked_at": str(c_ts or now_iso)[:40],
                    "scope": "controls_db",
                }
                controls_db_digests += 1

        local_gen = max(0, int(local_wm.get("authority_generation") or 0))
        local_restore_epoch = max(0, int(local_wm.get("restore_epoch") or 0))
        added_beyond_local = len(merged_digests) > len(local_digests)
        added_beyond_ext = ext_gen > 0 and len(merged_digests) > len(ext_digests)
        new_gen = max(local_gen, ext_gen)
        if (added_beyond_local and new_gen <= local_gen) or (added_beyond_ext and new_gen <= ext_gen):
            new_gen += 1
        new_restore_epoch = max(local_restore_epoch, ext_restore_epoch) + 1
        for entry_meta in merged_digests.values():
            entry_meta.setdefault("authority_generation", int(new_gen))

        wm_doc = {
            "schema": VC_AUTHORITY_SCHEMA,
            "authority_generation": int(new_gen),
            "restore_epoch": int(new_restore_epoch),
            "sealed_digests": merged_digests,
            "plaintext_stored": False,
            "updated_at": now_iso,
        }
        _tx.atomic_write_json(wm_path, wm_doc, sort_keys=True)

    sealed_digest_set = frozenset(merged_digests.keys())
    known_dead_ids: set[str] = (
        set(local_rp_ts.keys())
        | set(local_gov_ts.keys())
        | set(controls_db_revoked.keys())
    )
    discovered_dead_ids: set[str] = set()
    scrubbed_files: list[str] = []

    for rel_parts, allow_list in _collect_vc_scrub_targets(day_root):
        if not _is_safe_nonsymlink_rel(day_root, rel_parts):
            continue
        target_path = day_root.joinpath(*rel_parts)
        if not target_path.is_file():
            continue
        with _tx.state_file_lock(target_path):
            if allow_list:
                ex, ok, doc = _read_json_doc_or_list_safe(target_path)
                if not ex or not ok or not isinstance(doc, (Mapping, list)):
                    continue
                scrubbed = _scrub_tombstoned_ids_from_tree(
                    doc,
                    known_dead_ids,
                    vc_sealed_digests=sealed_digest_set,
                    matched_ids_out=discovered_dead_ids,
                )
                if scrubbed != doc and isinstance(scrubbed, (Mapping, list)):
                    _atomic_write_json_any(target_path, scrubbed)
                    scrubbed_files.append("/".join(rel_parts))
            else:
                ex, ok, doc = _read_json_safe(target_path)
                if not ex or not ok or not isinstance(doc, Mapping):
                    continue
                scrubbed = _scrub_tombstoned_ids_from_tree(
                    doc,
                    known_dead_ids,
                    vc_sealed_digests=sealed_digest_set,
                    matched_ids_out=discovered_dead_ids,
                )
                if scrubbed != doc and isinstance(scrubbed, dict):
                    _atomic_write_json_doc(target_path, scrubbed)
                    scrubbed_files.append("/".join(rel_parts))

    all_merged_tombstones: dict[str, dict[str, Any]] = {}
    all_merged_tombstones.update(local_rp_ts)
    all_merged_tombstones.update(local_gov_ts)
    for cid, c_ts in controls_db_revoked.items():
        all_merged_tombstones.setdefault(
            cid,
            {
                "memory_id": cid,
                "vc_digest": compute_vc_item_digest(cid),
                "reason": "controls_db_delete_reconciled",
                "tombstoned_at": c_ts or now_iso,
                "tombstoned": True,
            },
        )
    for did in sorted(discovered_dead_ids):
        if did not in all_merged_tombstones:
            hx = compute_vc_item_digest(did)
            rev_at = merged_digests.get(hx, {}).get("revoked_at") or now_iso
            all_merged_tombstones[did] = {
                "memory_id": did,
                "vc_digest": hx,
                "reason": "vc_barrier_reconciled",
                "tombstoned_at": rev_at,
                "tombstoned": True,
            }

    # Sync discovered/known tombstones back into recall projection state files if they exist
    if all_merged_tombstones:
        for rp_rel in (
            (STATE_DIRNAME, "v02_recall_projection.json"),
            ("memory", "v02_recall_projection_state.json"),
        ):
            if not _is_safe_nonsymlink_rel(day_root, rp_rel):
                continue
            rp_path = day_root.joinpath(*rp_rel)
            if not rp_path.is_file():
                continue
            with _tx.state_file_lock(rp_path):
                ex_r, ok_r, doc_r = _read_json_safe(rp_path)
                if ex_r and ok_r and isinstance(doc_r, Mapping):
                    updated_rp = _recompute_recall_projection_doc_after_scrub(
                        dict(doc_r),
                        merged_tombstones=all_merged_tombstones,
                        now_iso=now_iso,
                    )
                    _tx.atomic_write_json(rp_path, updated_rp)
                    rel_str = "/".join(rp_rel)
                    if rel_str not in scrubbed_files:
                        scrubbed_files.append(rel_str)

    synced_gov = _sync_runtime_governance_files(
        day_root,
        tombstones_map=all_merged_tombstones,
        authority_generation=int(new_gen),
        restore_epoch=int(new_restore_epoch),
        now_iso=now_iso,
    )

    return {
        "schema": "nyairo.v02.vc_reconcile_receipt.v1",
        "status": "RECONCILED",
        "authority_generation": int(new_gen),
        "restore_epoch": int(new_restore_epoch),
        "sealed_digest_count": len(merged_digests),
        "merged_external_digest_count": len(ext_digests),
        "controls_db_digests_discovered": controls_db_digests,
        "stripped_item_count": len(discovered_dead_ids),
        "scrubbed_files": scrubbed_files,
        "synced_governance_paths": synced_gov,
        "reconciled_at": now_iso,
        "llm_calls": 0,
    }


def reconcile_backup_restore_vc_barrier(
    day_root: Path | str,
    *,
    external_watermark_seal: Any = None,
    operation_key: Optional[str] = None,
    now: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Enforce G0 V-C anti-resurrection barrier across backup rollbacks (deterministic, zero LLM calls)."""
    root = Path(day_root)
    eff_now = now_iso if now_iso is not None else now
    _, resolved_iso = _resolve_now(eff_now)

    if operation_key is not None:
        if not isinstance(operation_key, str) or not operation_key.strip():
            raise V02ValidationError("INVALID_FIELDS", "operation_key must be a non-empty string when provided")
        mut_payload: dict[str, Any] = {
            "action": "reconcile_backup_restore",
            "operation_key": operation_key.strip(),
        }
        if external_watermark_seal is not None:
            mut_payload["external_watermark_seal"] = external_watermark_seal
        projected, op_status = apply_runtime_migration_mutation(
            root,
            mut_payload,
            actor_side="subject",
            now_iso=resolved_iso,
        )
        receipt = copy.deepcopy(projected.get("vc_reconcile_receipt") or {})
        if op_status == "REPLAYED":
            receipt["status"] = "REPLAYED"
        return receipt

    return _apply_reconcile_backup_restore_vc_barrier(
        root,
        external_watermark_seal=external_watermark_seal,
        now_iso=resolved_iso,
    )


def project_chatless_life_review(
    day_root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Ch.15 §10.I 'Delete Chat Window and Look' non-verbal life review (100% zero writes, zero LLM calls)."""
    root = Path(day_root)
    _, resolved_iso = _resolve_now(now_iso)

    wm_doc = read_vc_authority_watermark_readonly(root)
    raw_sealed = wm_doc.get("sealed_digests")
    vc_digests = frozenset(str(k).lower() for k in raw_sealed.keys()) if isinstance(raw_sealed, Mapping) else frozenset()
    tombstones = _extract_tombstone_ledger_readonly(root, now_iso=resolved_iso)
    tombstones.update(_read_governance_tombstones_readonly(root, now_iso=resolved_iso))
    dead_ids = frozenset(tombstones.keys())

    def _is_dead(raw_id: Any) -> bool:
        if not isinstance(raw_id, str):
            return False
        cid = raw_id.strip()
        if not cid:
            return False
        if cid in dead_ids:
            return True
        if vc_digests and compute_vc_item_digest(cid) in vc_digests:
            return True
        return False

    def _read_rel(rel_parts: tuple[str, ...]) -> Optional[dict[str, Any]]:
        if not _is_safe_nonsymlink_rel(root, rel_parts):
            return None
        ex, ok, doc = _read_json_safe(root.joinpath(*rel_parts))
        if ex and ok and isinstance(doc, Mapping):
            return dict(doc)
        return None

    def _pick_first_id(entry: Mapping[str, Any], keys: tuple[str, ...], fallback: str = "") -> str:
        for k in keys:
            val = entry.get(k)
            if isinstance(val, str) and val.strip():
                return val.strip()
        return fallback

    def _entry_is_public(entry: Mapping[str, Any]) -> bool:
        delivery = str(entry.get("delivery_state") or entry.get("status") or "").strip().upper()
        stage = str(entry.get("stage") or "").strip().lower()
        vis = str(entry.get("visibility") or "").strip().lower()
        return bool(
            entry.get("shared") is True
            or entry.get("share_with_room") is True
            or entry.get("public") is True
            or delivery == "LOCAL_DELIVERED"
            or stage == "gifted"
            or vis in {"shared", "public"}
        )

    # 1. works_and_versions (workbench_curio.json / workbench_tray.json / longterm_gifts_lantern.json)
    works_items: list[dict[str, Any]] = []
    seen_work_keys: set[str] = set()
    for wb_rel in ((STATE_DIRNAME, "workbench_curio.json"), (STATE_DIRNAME, "workbench_tray.json")):
        wb_doc = _read_rel(wb_rel)
        if not isinstance(wb_doc, Mapping):
            continue
        candidates: list[Mapping[str, Any]] = []
        tray = wb_doc.get("tray")
        if isinstance(tray, Mapping) and isinstance(tray.get("work"), Mapping):
            w_copy = dict(tray["work"])
            if "note" in tray and "note" not in w_copy:
                w_copy["note"] = tray.get("note")
            candidates.append(w_copy)
        for list_key in ("works", "versions", "items", "curios"):
            raw_seq = wb_doc.get(list_key)
            if isinstance(raw_seq, list):
                for el in raw_seq:
                    if isinstance(el, Mapping):
                        candidates.append(el)
            elif isinstance(raw_seq, Mapping):
                for dk, dv in raw_seq.items():
                    if isinstance(dv, Mapping):
                        merged_el = dict(dv)
                        merged_el.setdefault("work_id", str(dk))
                        candidates.append(merged_el)
        if _pick_first_id(wb_doc, ("work_id", "artifact_id", "version_id", "id")):
            candidates.append(wb_doc)
        for item in candidates:
            wid = _pick_first_id(item, ("work_id", "artifact_id", "version_id", "baseline_version_id", "id"))
            if _is_dead(wid):
                continue
            dedup_key = f"wb:{wid}:{item.get('version_id') or item.get('baseline_version_id') or ''}"
            if dedup_key in seen_work_keys:
                continue
            is_public = _entry_is_public(item)
            if not has_subject_capability and not is_public:
                continue
            seen_work_keys.add(dedup_key)
            rec: dict[str, Any] = {
                "source": "/".join(wb_rel),
                "work_id": wid,
                "artifact_id": item.get("artifact_id") or wid,
                "version_id": item.get("version_id") or item.get("baseline_version_id"),
                "version_number": item.get("version_number") or item.get("baseline_version_number"),
                "title": str(item.get("title") or item.get("name") or "").strip(),
                "delivery_state": str(item.get("delivery_state") or item.get("status") or ("LOCAL_DELIVERED" if is_public else "DRAFT")).strip(),
                "shared": is_public,
            }
            if has_subject_capability and isinstance(item.get("note"), str) and item["note"].strip():
                rec["note"] = item["note"].strip()
            works_items.append(rec)

    reflection_items: list[dict[str, Any]] = []
    gifts_doc = _read_rel((STATE_DIRNAME, "longterm_gifts_lantern.json"))
    if isinstance(gifts_doc, Mapping):
        for g in gifts_doc.get("shared_gifts") or []:
            if not isinstance(g, Mapping):
                continue
            gid = _pick_first_id(g, ("gift_id", "artifact_id", "id"))
            art_id = _pick_first_id(g, ("artifact_id",))
            if _is_dead(gid) or _is_dead(art_id):
                continue
            is_public = _entry_is_public(g)
            if not has_subject_capability and not is_public:
                continue
            rec = {
                "source": f"{STATE_DIRNAME}/longterm_gifts_lantern.json",
                "gift_id": gid,
                "artifact_id": g.get("artifact_id"),
                "version_id": g.get("version_id"),
                "stage": str(g.get("stage") or "").strip(),
                "title": str(g.get("title") or "").strip(),
                "delivery_state": str(g.get("delivery_state") or ("LOCAL_DELIVERED" if is_public else "DRAFT")).strip(),
                "gifted_at": g.get("gifted_at"),
                "shared": is_public,
            }
            if has_subject_capability:
                if isinstance(g.get("finished_note"), str) and g["finished_note"].strip():
                    rec["finished_note"] = g["finished_note"].strip()
                if isinstance(g.get("draft_seed_note"), str) and g["draft_seed_note"].strip():
                    rec["draft_seed_note"] = g["draft_seed_note"].strip()
            works_items.append(rec)
        for rf in gifts_doc.get("later_reflections") or []:
            if not isinstance(rf, Mapping):
                continue
            rf_id = _pick_first_id(rf, ("reflection_id", "source_artifact_id", "id"))
            if _is_dead(rf_id) or _is_dead(rf.get("source_artifact_id")):
                continue
            is_public = _entry_is_public(rf)
            if not has_subject_capability and not is_public:
                continue
            rf_rec: dict[str, Any] = {
                "reflection_id": rf_id,
                "source_artifact_id": rf.get("source_artifact_id"),
                "source_version_id": rf.get("source_version_id"),
                "shared": is_public,
            }
            if has_subject_capability or is_public:
                note_txt = str(rf.get("reflection_note") or rf.get("note") or "").strip()
                if note_txt:
                    rf_rec["note"] = note_txt
            reflection_items.append(rf_rec)

    works_and_versions = {
        "item_count": len(works_items),
        "delivered_count": sum(1 for it in works_items if it.get("shared") or it.get("delivery_state") == "LOCAL_DELIVERED"),
        "items": works_items,
        "later_reflections": reflection_items,
    }

    # 2. room_layout_traces (world/v08_matter_space.json)
    matter_doc, _ = _read_world_slice_readonly_with_source(root, "v08_matter_space.json")
    zones_out: list[dict[str, Any]] = []
    objects_out: list[dict[str, Any]] = []
    acts_out: list[dict[str, Any]] = []
    arrangements_out: list[dict[str, Any]] = []
    if isinstance(matter_doc, Mapping):
        raw_objs = matter_doc.get("matter_objects")
        if raw_objs is None:
            raw_objs = matter_doc.get("objects")
        obj_list: list[Mapping[str, Any]] = []
        if isinstance(raw_objs, Mapping):
            for ok_id, ov in raw_objs.items():
                if isinstance(ov, Mapping):
                    oc = dict(ov)
                    oc.setdefault("object_id", str(ok_id))
                    obj_list.append(oc)
        elif isinstance(raw_objs, list):
            obj_list = [dict(o) for o in raw_objs if isinstance(o, Mapping)]

        def _is_private_zone(zone_id: str, vis: str = "") -> bool:
            zl = zone_id.strip().lower()
            vl = vis.strip().lower()
            return zl == "subject_private_drawer" or vl in {"private", "subject_only", "her_private"}

        zone_counts: dict[str, int] = {}
        for obj in obj_list:
            oid = _pick_first_id(obj, ("object_id", "item_id", "id"))
            if _is_dead(oid):
                continue
            ozone = str(obj.get("zone") or obj.get("zone_id") or obj.get("location") or "").strip()
            ovis = str(obj.get("visibility") or "").strip()
            if ozone:
                zone_counts[ozone] = zone_counts.get(ozone, 0) + 1
            if not has_subject_capability and _is_private_zone(ozone, ovis):
                continue
            objects_out.append(
                {
                    "object_id": oid,
                    "label": str(obj.get("label") or obj.get("name") or oid).strip(),
                    "zone": ozone,
                    "state": str(obj.get("state") or obj.get("condition") or "").strip(),
                }
            )

        raw_zones = matter_doc.get("zones")
        zone_entries: list[tuple[str, Mapping[str, Any]]] = []
        if isinstance(raw_zones, Mapping):
            for zk, zv in raw_zones.items():
                if isinstance(zv, Mapping):
                    zone_entries.append((str(zk), zv))
        elif isinstance(raw_zones, list):
            for zv in raw_zones:
                if isinstance(zv, Mapping):
                    zk = _pick_first_id(zv, ("zone_id", "id", "name"))
                    if zk:
                        zone_entries.append((zk, zv))

        seen_zones: set[str] = set()
        for zid, zmeta in zone_entries:
            seen_zones.add(zid)
            zvis = str(zmeta.get("visibility") or "").strip()
            if _is_dead(zid):
                continue
            if not has_subject_capability and _is_private_zone(zid, zvis):
                zones_out.append(
                    {
                        "zone_id": zid,
                        "visibility": "PRIVATE",
                        "redacted": True,
                        "item_count": None,
                    }
                )
            else:
                z_items = zmeta.get("items") or zmeta.get("objects")
                z_cnt = len(z_items) if isinstance(z_items, list) else zone_counts.get(zid, 0)
                zones_out.append(
                    {
                        "zone_id": zid,
                        "label": str(zmeta.get("label") or zid).strip(),
                        "visibility": "PRIVATE" if _is_private_zone(zid, zvis) else "PUBLIC",
                        "item_count": int(z_cnt),
                    }
                )
        for ozone, ocnt in sorted(zone_counts.items()):
            if ozone in seen_zones or _is_dead(ozone):
                continue
            if not has_subject_capability and _is_private_zone(ozone):
                zones_out.append(
                    {
                        "zone_id": ozone,
                        "visibility": "PRIVATE",
                        "redacted": True,
                        "item_count": None,
                    }
                )
            else:
                zones_out.append(
                    {
                        "zone_id": ozone,
                        "label": ozone,
                        "visibility": "PRIVATE" if _is_private_zone(ozone) else "PUBLIC",
                        "item_count": int(ocnt),
                    }
                )

        for act in matter_doc.get("recent_acts") or []:
            if not isinstance(act, Mapping):
                continue
            aid = _pick_first_id(act, ("act_id", "object_id", "id"))
            if _is_dead(aid) or _is_dead(act.get("object_id")):
                continue
            azone = str(act.get("zone") or act.get("zone_id") or act.get("target_zone") or "").strip()
            avis = str(act.get("visibility") or "").strip()
            if not has_subject_capability and _is_private_zone(azone, avis):
                continue
            acts_out.append(
                {
                    "act_id": aid,
                    "action": str(act.get("action") or act.get("act_type") or "").strip(),
                    "zone": azone,
                    "object_id": act.get("object_id"),
                    "at": act.get("at") or act.get("recorded_at"),
                }
            )

        for arr in matter_doc.get("active_arrangements") or []:
            if not isinstance(arr, Mapping):
                continue
            arr_id = _pick_first_id(arr, ("arrangement_id", "id", "object_id"))
            if _is_dead(arr_id):
                continue
            azone = str(arr.get("zone") or arr.get("zone_id") or "").strip()
            avis = str(arr.get("visibility") or "").strip()
            if not has_subject_capability and _is_private_zone(azone, avis):
                continue
            arrangements_out.append(
                {
                    "arrangement_id": arr_id,
                    "zone": azone,
                    "summary": str(arr.get("summary") or arr.get("label") or "").strip(),
                }
            )

    room_layout_traces = {
        "zones": zones_out,
        "object_count": len(objects_out),
        "objects": objects_out,
        "recent_acts": acts_out,
        "active_arrangements": arrangements_out,
    }

    # 3. reading_bookmarks (outing_bench_bookstore.json / v02_crossmodal.json)
    bookmarks_out: list[dict[str, Any]] = []
    crossmodal_out: list[dict[str, Any]] = []
    bookstore_doc = _read_rel((STATE_DIRNAME, "outing_bench_bookstore.json"))
    if isinstance(bookstore_doc, Mapping):
        for bm in bookstore_doc.get("window_bookmarks") or bookstore_doc.get("bookmarks") or []:
            if not isinstance(bm, Mapping):
                continue
            bid = _pick_first_id(bm, ("bookmark_id", "id"))
            if _is_dead(bid):
                continue
            is_public = _entry_is_public(bm)
            if not has_subject_capability and not is_public:
                continue
            bm_rec: dict[str, Any] = {
                "bookmark_id": bid,
                "slot_index": bm.get("slot_index"),
                "book_code": bm.get("book_code"),
                "title": str(bm.get("title") or "").strip(),
                "shared": is_public,
                "updated_at": bm.get("updated_at") or bm.get("saved_at"),
            }
            if has_subject_capability or is_public:
                if isinstance(bm.get("note"), str) and bm["note"].strip():
                    bm_rec["note"] = bm["note"].strip()
            bookmarks_out.append(bm_rec)

    crossmodal_doc = _read_rel((STATE_DIRNAME, "v02_crossmodal.json"))
    if isinstance(crossmodal_doc, Mapping):
        for seq_key in ("reading_uncertainty_marks", "collection_items", "bookmarks"):
            seq = crossmodal_doc.get(seq_key)
            entries: list[Mapping[str, Any]] = []
            if isinstance(seq, list):
                entries = [dict(x) for x in seq if isinstance(x, Mapping)]
            elif isinstance(seq, Mapping):
                for xk, xv in seq.items():
                    if isinstance(xv, Mapping):
                        xc = dict(xv)
                        xc.setdefault("mark_id", str(xk))
                        entries.append(xc)
            for item in entries:
                mid = _pick_first_id(item, ("mark_id", "bookmark_id", "item_id", "id"))
                if _is_dead(mid):
                    continue
                is_public = _entry_is_public(item)
                if not has_subject_capability and not is_public:
                    continue
                cm_rec: dict[str, Any] = {
                    "mark_id": mid,
                    "source_kind": seq_key,
                    "title": str(item.get("title") or item.get("topic") or "").strip(),
                    "shared": is_public,
                }
                if has_subject_capability or is_public:
                    excerpt = str(item.get("note") or item.get("excerpt") or "").strip()
                    if excerpt:
                        cm_rec["note"] = excerpt
                crossmodal_out.append(cm_rec)

    reading_bookmarks = {
        "bookmark_count": len(bookmarks_out),
        "bookmarks": bookmarks_out,
        "crossmodal_mark_count": len(crossmodal_out),
        "crossmodal_marks": crossmodal_out,
    }

    # 4. methods_and_endings (v02_agency_depth.json / state/v02_agency_depth_state.json)
    endings_out: list[dict[str, Any]] = []
    methods_out: list[dict[str, Any]] = []
    seen_endings: set[str] = set()
    seen_methods: set[str] = set()
    for ag_rel in (
        (STATE_DIRNAME, "v02_agency_depth.json"),
        (STATE_DIRNAME, "v02_agency_depth_state.json"),
        ("state", "v02_agency_depth_state.json"),
    ):
        ag_doc = _read_rel(ag_rel)
        if not isinstance(ag_doc, Mapping):
            continue
        for ek in ("legacy_tool_endings",):
            raw_end = ag_doc.get(ek)
            end_list: list[Mapping[str, Any]] = []
            if isinstance(raw_end, list):
                end_list = [dict(x) for x in raw_end if isinstance(x, Mapping)]
            elif isinstance(raw_end, Mapping):
                for xk, xv in raw_end.items():
                    if isinstance(xv, Mapping):
                        xc = dict(xv)
                        xc.setdefault("ending_id", str(xk))
                        end_list.append(xc)
            for item in end_list:
                eid = _pick_first_id(item, ("ending_id", "tool_id", "method_id", "id"))
                if _is_dead(eid):
                    continue
                is_public = _entry_is_public(item)
                if not has_subject_capability and not is_public:
                    continue
                if eid and eid in seen_endings:
                    continue
                if eid:
                    seen_endings.add(eid)
                rec = {
                    "ending_id": eid,
                    "title": str(item.get("title") or item.get("label") or item.get("summary") or "").strip(),
                    "status": str(item.get("status") or "ended").strip(),
                    "shared": is_public,
                }
                endings_out.append(rec)

        for mk in ("validated_methods", "method_adjustments"):
            raw_m = ag_doc.get(mk)
            m_list: list[Mapping[str, Any]] = []
            if isinstance(raw_m, list):
                m_list = [dict(x) for x in raw_m if isinstance(x, Mapping)]
            elif isinstance(raw_m, Mapping):
                for xk, xv in raw_m.items():
                    if isinstance(xv, Mapping):
                        xc = dict(xv)
                        xc.setdefault("method_id", str(xk))
                        m_list.append(xc)
            for item in m_list:
                mid = _pick_first_id(item, ("method_id", "adjustment_id", "rule_id", "id"))
                if _is_dead(mid):
                    continue
                is_public = _entry_is_public(item)
                status_str = str(item.get("status") or "").strip().lower()
                is_validated = bool(item.get("validated") is True or status_str in {"validated", "active", "verified"})
                if mk == "method_adjustments" and not is_validated and not is_public:
                    continue
                if not has_subject_capability and not is_public:
                    continue
                if mid and mid in seen_methods:
                    continue
                if mid:
                    seen_methods.add(mid)
                methods_out.append(
                    {
                        "method_id": mid,
                        "title": str(item.get("title") or item.get("label") or item.get("method") or "").strip(),
                        "validated": is_validated or True,
                        "shared": is_public,
                    }
                )

    methods_and_endings = {
        "ending_count": len(endings_out),
        "method_count": len(methods_out),
        "legacy_tool_endings": endings_out,
        "validated_methods": methods_out,
    }

    # 5. closed_and_waiting_items (desk_tomorrow_note.json)
    closed_count = 0
    waiting_count = 0
    private_redacted_count = 0
    closed_items_out: list[dict[str, Any]] = []
    waiting_items_out: list[dict[str, Any]] = []
    desk_doc = _read_rel((STATE_DIRNAME, "desk_tomorrow_note.json"))
    if isinstance(desk_doc, Mapping):
        raw_notes: list[tuple[str, Mapping[str, Any]]] = []
        for lkey, def_kind in (
            ("closed_items", "closed"),
            ("waiting_items", "waiting"),
            ("tomorrow_items", "waiting"),
            ("notes", ""),
            ("items", ""),
            ("shared_notes", "waiting"),
            ("retained_notes", "waiting"),
        ):
            seq = desk_doc.get(lkey)
            if isinstance(seq, list):
                for el in seq:
                    if isinstance(el, Mapping):
                        raw_notes.append((def_kind, el))
            elif isinstance(seq, Mapping):
                for nk, nv in seq.items():
                    if isinstance(nv, Mapping):
                        nc = dict(nv)
                        nc.setdefault("note_id", str(nk))
                        raw_notes.append((def_kind, nc))
        if _pick_first_id(desk_doc, ("note_id", "item_id", "id")) and not raw_notes:
            raw_notes.append(("", desk_doc))

        seen_note_ids: set[str] = set()
        for def_kind, note_entry in raw_notes:
            nid = _pick_first_id(note_entry, ("note_id", "item_id", "id"))
            if _is_dead(nid):
                continue
            if nid and nid in seen_note_ids:
                continue
            if nid:
                seen_note_ids.add(nid)
            st = str(note_entry.get("status") or note_entry.get("state") or def_kind or "waiting").strip().lower()
            is_closed = st in {"closed", "done", "completed", "resolved", "archived"}
            if is_closed:
                closed_count += 1
            else:
                waiting_count += 1
            is_public = _entry_is_public(note_entry)
            if not has_subject_capability and not is_public:
                private_redacted_count += 1
                continue
            rec = {
                "note_id": nid,
                "title": str(note_entry.get("title") or note_entry.get("label") or note_entry.get("text") or "").strip(),
                "status": "closed" if is_closed else "waiting",
                "shared": is_public,
            }
            if is_closed:
                closed_items_out.append(rec)
            else:
                waiting_items_out.append(rec)

    closed_and_waiting_items = {
        "closed_count": closed_count,
        "waiting_count": waiting_count,
        "private_redacted_count": private_redacted_count if not has_subject_capability else 0,
        "closed_items": closed_items_out,
        "waiting_items": waiting_items_out,
    }

    # 6. verified_deliveries (v02_common_ground.json / memory/v02_common_ground_state.json)
    deliveries_out: list[dict[str, Any]] = []
    seen_cg: set[str] = set()
    shared_cg_count = 0
    for cg_rel in ((STATE_DIRNAME, "v02_common_ground.json"), ("memory", "v02_common_ground_state.json")):
        cg_doc = _read_rel(cg_rel)
        if not isinstance(cg_doc, Mapping):
            continue
        raw_cg = cg_doc.get("items") or cg_doc.get("common_ground") or cg_doc.get("entries")
        cg_entries: list[Mapping[str, Any]] = []
        if isinstance(raw_cg, Mapping):
            for gk, gv in raw_cg.items():
                if isinstance(gv, Mapping):
                    gc = dict(gv)
                    gc.setdefault("ground_id", str(gk))
                    cg_entries.append(gc)
        elif isinstance(raw_cg, list):
            cg_entries = [dict(g) for g in raw_cg if isinstance(g, Mapping)]
        for g in cg_entries:
            gid = _pick_first_id(g, ("ground_id", "item_id", "id"))
            if _is_dead(gid) or _is_dead(g.get("source_memory_id")):
                continue
            if gid and gid in seen_cg:
                continue
            if gid:
                seen_cg.add(gid)
            gstatus = str(g.get("ground_status") or "").strip().upper()
            is_shared = gstatus == "SHARED_WITH_PARTNER"
            if is_shared:
                shared_cg_count += 1
            if not has_subject_capability and not is_shared:
                continue
            deliveries_out.append(
                {
                    "ground_id": gid,
                    "ground_status": gstatus or ("SHARED_WITH_PARTNER" if is_shared else "PRIVATE_UNSHARED"),
                    "delivery_state": str(g.get("delivery_state") or ("LOCAL_DELIVERED" if is_shared else "")).strip(),
                    "topic": str(g.get("topic") or g.get("title") or g.get("summary") or "").strip(),
                    "verified_at": g.get("verified_at") or g.get("shared_at") or g.get("updated_at"),
                }
            )

    verified_deliveries = {
        "delivery_count": len(deliveries_out),
        "shared_count": shared_cg_count,
        "items": deliveries_out,
    }

    return {
        "schema": CHATLESS_LIFE_REVIEW_SCHEMA,
        "perspective": "her_private" if has_subject_capability else "partner_default",
        "observed_at": resolved_iso,
        "chat_window_hidden": True,
        "dialogue_ignored": True,
        "nonverbal_evidence_only": True,
        "works_and_versions": works_and_versions,
        "room_layout_traces": room_layout_traces,
        "reading_bookmarks": reading_bookmarks,
        "methods_and_endings": methods_and_endings,
        "closed_and_waiting_items": closed_and_waiting_items,
        "verified_deliveries": verified_deliveries,
        "llm_calls": 0,
    }


def _apply_set_budget_mode(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    *,
    now_dt: datetime,
    now_iso: str,
    _allow_sealed_transition: bool = False,
) -> None:
    if not _allow_sealed_transition:
        ret_sec = state.get("retirement") if isinstance(state.get("retirement"), Mapping) else {}
        if str(ret_sec.get("status") or "") == RETIREMENT_STATUS_SEALED:
            raise V02ConflictError(
                "ALREADY_SEALED",
                "Cannot change budget mode after dignified retirement archive is sealed",
            )
    raw_mode = str(payload["mode"]).strip().upper()
    if raw_mode not in BUDGET_MODE_ALIASES:
        raise V02ValidationError("INVALID_MODE", f"Unsupported budget mode: {payload['mode']!r}")
    canonical_mode = BUDGET_MODE_ALIASES[raw_mode]

    raw_pct = payload.get("budget_scale_pct")
    if raw_pct is not None:
        if isinstance(raw_pct, bool) or not isinstance(raw_pct, int) or not (1 <= raw_pct <= 100):
            raise V02ValidationError("INVALID_FIELDS", "budget_scale_pct must be int in 1..100")
        pct = 0 if canonical_mode == MODE_DORMANT else int(raw_pct)
    else:
        pct = 0 if canonical_mode == MODE_DORMANT else (10 if canonical_mode == MODE_LOW_BUDGET else 100)

    reason_cn = str(payload.get("reason_cn") or "").strip()
    mode_cfg = state["mode_config"]
    prev_mode = str(mode_cfg.get("mode") or MODE_NORMAL)
    prev_dormant_since = mode_cfg.get("dormant_since")

    if canonical_mode == MODE_DORMANT:
        if prev_mode != MODE_DORMANT or not prev_dormant_since:
            mode_cfg["dormant_since"] = now_iso
    else:
        if prev_mode == MODE_DORMANT and isinstance(prev_dormant_since, str):
            d_dt = _parse_iso(prev_dormant_since)
            gap_sec = max(0, int((now_dt - d_dt).total_seconds())) if d_dt is not None else 0
            mode_cfg["last_dormant_gap_seconds"] = gap_sec
            mode_cfg["last_dormant_recovery"] = {
                "dormant_since": prev_dormant_since,
                "woke_at": now_iso,
                "dormant_gap_seconds": gap_sec,
                "no_fake_backfill": True,
                "fake_diary_entries_created": 0,
                "fabricated_memories_created": 0,
                "orientation_cn": (
                    f"已从休眠恢复（真实跨越 {gap_sec} 秒），期间无认知经历，"
                    "不补写任何假日记，先看当前环境与时间再接着安排。"
                ),
            }
            mode_cfg["dormant_since"] = None

    mode_cfg["mode"] = canonical_mode
    mode_cfg["budget_scale_pct"] = pct
    mode_cfg["reason_cn"] = reason_cn
    mode_cfg["mode_changed_at"] = now_iso


def _apply_record_carrier_check(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    *,
    now_iso: str,
) -> None:
    carrier_sec = state["carrier_migration"]
    prev_fp = copy.deepcopy(carrier_sec.get("carrier_fingerprint") or DEFAULT_CARRIER_FINGERPRINT)

    new_model_id = str(payload["model_id"]).strip()
    raw_carrier = payload.get("carrier_name")
    new_carrier_name = (
        raw_carrier.strip()
        if isinstance(raw_carrier, str) and raw_carrier.strip()
        else str(prev_fp.get("carrier_name") or DEFAULT_CARRIER_FINGERPRINT["carrier_name"])
    )
    if "preview_available" in payload and payload["preview_available"] is not None:
        new_preview = bool(payload["preview_available"])
    else:
        new_preview = bool(prev_fp.get("preview_available", True))

    model_changed = new_model_id != str(prev_fp.get("model_id") or "")
    carrier_changed = new_carrier_name != str(prev_fp.get("carrier_name") or "")
    preview_changed = new_preview != bool(prev_fp.get("preview_available", True))

    skills = carrier_sec.setdefault("skills", _default_skills_catalog())
    if model_changed or carrier_changed:
        for sk in skills.values():
            if not isinstance(sk, dict):
                continue
            sk["importance_preserved"] = True
            if bool(sk.get("model_sensitive", False)) and model_changed:
                sk["status"] = SKILL_STATUS_UNVALIDATED

    loss_report: list[dict[str, Any]] = list(carrier_sec.get("loss_report") or [])
    loss_report = [entry for entry in loss_report if entry.get("loss_code") != "HOST_LIVE_PREVIEW_UNAVAILABLE"]
    if not new_preview:
        loss_report.append({
            "loss_code": "HOST_LIVE_PREVIEW_UNAVAILABLE",
            "recorded_at": now_iso,
            "affected_skill_ids": ["interactive_layout_live_preview"],
            "summary_cn": "当前宿主缺少实时预览窗能力，交互排布需先改用纯文本核对，不虚构预览已完成",
        })

    carrier_sec["previous_carrier_fingerprint"] = prev_fp if (model_changed or carrier_changed or preview_changed) else carrier_sec.get("previous_carrier_fingerprint")
    carrier_sec["carrier_fingerprint"] = {
        "model_id": new_model_id,
        "carrier_name": new_carrier_name,
        "preview_available": new_preview,
    }
    carrier_sec["check_count"] = int(carrier_sec.get("check_count") or 0) + 1
    carrier_sec["last_checked_at"] = now_iso
    carrier_sec["loss_report"] = loss_report
    carrier_sec["last_migration_verdict"] = _compute_migration_verdict(carrier_sec)


def _apply_reverify_migrated_skill(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    *,
    now_iso: str,
) -> None:
    carrier_sec = state["carrier_migration"]
    skills = carrier_sec.setdefault("skills", _default_skills_catalog())
    target_id = str(payload["skill_id"]).strip()
    passed_flag = payload.get("passed", payload.get("trial_passed", True))
    passed = bool(passed_flag)
    note_cn = str(payload.get("note_cn") or "").strip()
    cur_model = str((carrier_sec.get("carrier_fingerprint") or {}).get("model_id") or "")

    if target_id == "all":
        targets = [
            sk
            for sk in skills.values()
            if isinstance(sk, dict) and sk.get("status") in (SKILL_STATUS_UNVALIDATED, SKILL_STATUS_PENDING_REVERIFY)
        ]
    else:
        if target_id not in skills or not isinstance(skills[target_id], dict):
            raise V02ValidationError("INVALID_FIELDS", f"Unknown skill_id: {target_id!r}")
        targets = [skills[target_id]]

    loss_report: list[dict[str, Any]] = list(carrier_sec.get("loss_report") or [])
    for sk in targets:
        sid = str(sk.get("skill_id") or target_id)
        sk["importance_preserved"] = True
        sk["last_trial_note_cn"] = note_cn
        loss_report = [e for e in loss_report if e.get("skill_id") != sid]
        if passed:
            sk["status"] = SKILL_STATUS_VALIDATED
            sk["verified_model_id"] = cur_model
        else:
            sk["status"] = SKILL_STATUS_SUSPENDED_LOSS
            loss_report.append({
                "loss_code": "SKILL_TRIAL_RECHECK_FAILED",
                "skill_id": sid,
                "recorded_at": now_iso,
                "summary_cn": note_cn or f"个人技法「{sk.get('title_cn', sid)}」在新载体试跑未达原有水准，诚实挂起并保留损失记录",
            })

    carrier_sec["loss_report"] = loss_report
    carrier_sec["last_migration_verdict"] = _compute_migration_verdict(carrier_sec)


def _apply_pack_selective_suitcase(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    *,
    now_iso: str,
    day_root: Optional[Path] = None,
) -> None:
    raw_mode = str(payload["profile_mode"]).strip()
    if raw_mode not in SUITCASE_PROFILE_MODES:
        raise V02ValidationError("INVALID_MODE", f"Unsupported suitcase profile_mode: {raw_mode!r}")

    skills = state["carrier_migration"].get("skills") or {}
    real_assets = _discover_real_suitcase_assets_readonly(day_root)
    real_method_ids = {
        str(m.get("artifact_id") or "").strip()
        for m in (real_assets.get("practice_methods") or ())
        if isinstance(m, Mapping) and str(m.get("artifact_id") or "").strip()
    }
    real_slice_ids = (
        {
            f"phone_slice:{s.get('slice_file')}"
            for s in (real_assets.get("phone_slices") or ())
            if isinstance(s, Mapping) and str(s.get("slice_file") or "").strip()
        }
        | set(SUITCASE_ALLOWED_SLICE_SET)
        | {f"world_slice:{w}" for w in SUITCASE_ALLOWED_WORLD_FILES}
        | set(SUITCASE_ALLOWED_WORLD_FILE_SET)
    )
    keep_ids_raw = payload.get("keep_skill_ids")
    keep_ids: Optional[list[str]] = None
    if keep_ids_raw is not None:
        if not isinstance(keep_ids_raw, list):
            raise V02ValidationError("INVALID_FIELDS", "keep_skill_ids must be a list of skill IDs")
        cleaned: list[str] = []
        for item in keep_ids_raw:
            if not isinstance(item, str) or not item.strip():
                raise V02ValidationError("INVALID_FIELDS", "keep_skill_ids items must be non-empty strings")
            sid = item.strip()
            if sid not in skills and sid not in real_method_ids and sid not in real_slice_ids:
                raise V02ValidationError("INVALID_FIELDS", f"Unknown skill_id in keep_skill_ids: {sid!r}")
            if sid not in cleaned:
                cleaned.append(sid)
        keep_ids = cleaned

    leave_note = str(payload.get("leave_behind_note") or "").strip()
    suit = state["suitcase_state"]
    suit["profile_mode"] = raw_mode
    suit["keep_skill_ids"] = keep_ids
    suit["leave_behind_note"] = leave_note
    suit["packed_at"] = now_iso


def _normalize_audit_candidate(item: Any, *, idx: int, default_private: bool = False) -> dict[str, Any]:
    if isinstance(item, str):
        text = item.strip()
        if not text:
            raise V02ValidationError("INVALID_FIELDS", "Candidate title cannot be blank")
        return {
            "candidate_id": f"cand-{idx + 1}",
            "source": "workspace_candidate",
            "title": text,
            "visibility": "private_draft" if default_private else "shared",
            "is_private_draft": bool(default_private),
        }
    if isinstance(item, Mapping):
        title = str(item.get("title") or "").strip()
        if not title:
            raise V02ValidationError("INVALID_FIELDS", "Candidate dict must have non-empty title")
        vis = str(item.get("visibility") or ("private_draft" if default_private else "shared")).strip()
        is_priv = bool(
            item.get("is_private_draft", default_private)
            or vis in ("private", "private_draft")
            or (item.get("shared") is False)
        )
        out: dict[str, Any] = {
            "candidate_id": str(item.get("candidate_id") or f"cand-{idx + 1}").strip(),
            "source": str(item.get("source") or "workspace_candidate").strip(),
            "title": title,
            "visibility": "private_draft" if is_priv else vis,
            "is_private_draft": is_priv,
        }
        if "reason_code" in item and item["reason_code"] is not None:
            out["reason_code"] = str(item["reason_code"]).strip()
        if "reason_cn" in item and item["reason_cn"] is not None:
            out["reason_cn"] = str(item["reason_cn"]).strip()
        return out
    raise V02ValidationError("INVALID_FIELDS", "Invalid candidate entry type")


def _apply_record_workspace_audit(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    *,
    now_iso: str,
) -> None:
    wake_reason = str(payload["wake_reason"]).strip()
    mode_cfg = state["mode_config"]
    audits: list[dict[str, Any]] = state.setdefault("workspace_audits", [])
    audit_seq = len(audits) + 1

    admitted_raw = payload.get("admitted_candidates") or []
    trimmed_raw = payload.get("trimmed_reasons") if "trimmed_reasons" in payload else (payload.get("trimmed_candidates") or [])
    priv_titles_raw = payload.get("private_draft_titles") or []

    admitted_list = [_normalize_audit_candidate(x, idx=i) for i, x in enumerate(admitted_raw)]
    for j, p_title in enumerate(priv_titles_raw):
        if not isinstance(p_title, str) or not p_title.strip():
            raise V02ValidationError("INVALID_FIELDS", "private_draft_titles items must be non-empty strings")
        admitted_list.append(
            _normalize_audit_candidate(p_title, idx=len(admitted_list) + j, default_private=True)
        )

    trimmed_list: list[dict[str, Any]] = []
    for k, t_item in enumerate(trimmed_raw):
        norm = _normalize_audit_candidate(t_item, idx=k)
        norm.setdefault("reason_code", "WORKSPACE_SLOT_TRIMMED")
        norm.setdefault("reason_cn", "受当轮工作区容量或预算约束未进入焦点")
        trimmed_list.append(norm)

    entered_cnt = payload.get("entered_workspace_count")
    entered_val = len(admitted_list) if entered_cnt is None else int(entered_cnt)

    visible_cnt = payload.get("visible_candidate_count")
    if visible_cnt is None:
        visible_val = max(entered_val + len(trimmed_list), len(admitted_list) + len(trimmed_list))
    else:
        visible_val = int(visible_cnt)

    if entered_val < 0 or visible_val < 0 or entered_val > visible_val:
        raise V02ValidationError(
            "INVALID_FIELDS",
            "visible_candidate_count and entered_workspace_count must satisfy 0 <= entered <= visible",
        )

    entry: dict[str, Any] = {
        "audit_id": f"wsa-{audit_seq:04d}",
        "recorded_at": now_iso,
        "wake_reason": wake_reason,
        "budget_mode": str(mode_cfg.get("mode") or MODE_NORMAL),
        "budget_scale_pct": int(mode_cfg.get("budget_scale_pct") or 100),
        "visible_candidate_count": visible_val,
        "entered_workspace_count": entered_val,
        "trimmed_count": max(0, visible_val - entered_val),
        "selection_rule": str(payload.get("selection_rule") or "bounded_workspace_admission").strip(),
        "admitted_candidates": admitted_list,
        "trimmed_reasons": trimmed_list,
    }
    if priv_titles_raw:
        entry["private_draft_titles"] = [str(t).strip() for t in priv_titles_raw]

    audits.append(entry)
    state["workspace_audits"] = audits[-MAX_WORKSPACE_AUDITS:]


_SUITCASE_COMPANION_FALLBACKS: dict[str, tuple[tuple[str, ...], ...]] = {
    "v02_common_ground.json": (("memory", "v02_common_ground_state.json"),),
    "v02_recall_projection.json": (("memory", "v02_recall_projection_state.json"),),
    "window_keepsakes.json": (("phone", "window_keepsake.json"),),
    "v04_neighborhood.json": (
        ("phone", "v04_neighborhood_state.json"),
        ("world", "v04_neighborhood_state.json"),
    ),
    "v03_epistemic_encounters.json": (("phone", "v03_encounters.json"),),
    "v02_open_self_rules.json": (
        ("phone", "v02_agency_depth_state.json"),
        ("state", "v02_agency_depth_state.json"),
    ),
    "everyday_desires.json": (("life", "everyday_desires.json"),),
    "v08_composable_body.json": (("body", "v08_composable_body_pack.json"),),
}

_SUITCASE_CANONICAL_RESTORE_MIRRORS: dict[str, tuple[tuple[str, ...], ...]] = {
    "v03_epistemic_encounters.json": (("phone", "v03_encounters.json"),),
    "v08_composable_body.json": (("body", "v08_composable_body_pack.json"),),
}

_WORLD_SLICE_READ_CANDIDATES: dict[str, tuple[tuple[str, ...], ...]] = {
    "v08_matter_space.json": (
        ("world", "v08_matter_space.json"),
        ("world-home", "world", "v08_matter_space.json"),
    ),
    "v08_open_affordance.json": (
        ("world", "v08_open_affordance.json"),
        ("world-home", "world", "v08_open_affordance.json"),
        ("world", "v08_living_traces.json"),
        ("world-home", "world", "v08_living_traces.json"),
        ("phone", "v08_living_traces.json"),
        ("world-home", "phone", "v08_living_traces.json"),
    ),
    "v08_microzone_ecology.json": (
        ("world", "v08_microzone_ecology.json"),
        ("world-home", "world", "v08_microzone_ecology.json"),
        ("world", "v08_microzone_state.json"),
        ("world-home", "world", "v08_microzone_state.json"),
    ),
    "v04_neighborhood_state.json": (
        ("world", "v04_neighborhood_state.json"),
        ("world-home", "world", "v04_neighborhood_state.json"),
    ),
}

_TOMBSTONE_MATCH_ID_KEYS: tuple[str, ...] = (
    "id",
    "memory_id",
    "item_id",
    "entry_id",
    "note_id",
    "visit_id",
    "study_id",
    "desire_id",
    "ground_id",
    "source_memory_id",
    "object_id",
    "trace_id",
    "routine_id",
    "act_id",
    "inscription_id",
    "question_id",
    "idiom_id",
    "reinterpretation_id",
    "spark_id",
    "notice_id",
    "direction_id",
    "ritual_id",
    "encounter_id",
    "material_id",
    "errand_id",
    "play_id",
    "genealogy_id",
    "artifact_id",
    "gift_id",
    "reflection_id",
    "share_id",
    "item_instance_id",
    "bookmark_id",
    "work_id",
    "method_id",
    "ending_id",
    "adjustment_id",
    "rule_id",
    "preference_id",
    "mark_id",
    "event_id",
    "source_artifact_id",
    "arrangement_id",
)


def _canonicalize_json_value(val: Any) -> Any:
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        if isinstance(val, float) and val.is_integer():
            return int(val)
        return val
    if isinstance(val, Mapping):
        return {str(k): _canonicalize_json_value(v) for k, v in val.items()}
    if isinstance(val, list):
        return [_canonicalize_json_value(elem) for elem in val]
    return val


def compute_suitcase_bundle_sha256(bundle: Mapping[str, Any]) -> str:
    """Compute canonical SHA-256 over a suitcase bundle dict (excluding ``bundle_sha256``)."""
    body = {str(k): _canonicalize_json_value(v) for k, v in bundle.items() if str(k) != "bundle_sha256"}
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _is_secret_or_permit_key(key: str) -> bool:
    low = key.strip().lower()
    if not low:
        return False
    if low in FORBIDDEN_SUITCASE_SECRET_KEYS:
        return True
    if low.endswith((
        "_api_key",
        "_apikey",
        "_secret_key",
        "_private_key",
        "_capability_token",
        "_access_token",
        "_refresh_token",
        "_bearer_token",
        "_csrf_token",
        "_auth_token",
        "_client_secret",
        "_session_permit",
        "_permit_token",
    )):
        return True
    if "adult_intimacy" in low and ("permit" in low or "session" in low or "token" in low):
        return True
    return False


def _is_host_path_binding_key(key: str) -> bool:
    low = key.strip().lower()
    return low in {
        "host_path",
        "root_path",
        "db_path",
        "sqlite_path",
        "socket_path",
        "pid_file",
        "workspace_root",
        "day_root",
        "hermes_home",
        "world_home",
    }


def _redact_host_paths_in_str(
    text: str,
    *,
    source_label: str,
    field_path: str,
    stripped_bindings: list[str],
) -> str:
    if not text:
        return text
    if _SECRET_VALUE_PREFIX_RE.match(text.strip()):
        marker = f"{source_label}:{field_path}:secret_value"
        if marker not in stripped_bindings:
            stripped_bindings.append(marker)
        return "[STRIPPED_SECRET]"
    # 2026-11-14 审计项6b：整串锚定未命中时，再按子串打掉自由文本中内嵌的长令牌，
    # 替换为红色占位符；命中后仍继续走宿主路径清洗，同一字符串两类泄漏一并处理。
    redacted_text = _SECRET_VALUE_EMBEDDED_RE.sub("[REDACTED_SECRET]", text)
    if redacted_text != text:
        marker = f"{source_label}:{field_path}:embedded_secret_value"
        if marker not in stripped_bindings:
            stripped_bindings.append(marker)
        text = redacted_text
    if _HOST_FS_PATH_RE.search(text):
        marker = f"{source_label}:{field_path}:host_path"
        if marker not in stripped_bindings:
            stripped_bindings.append(marker)
        return _HOST_FS_PATH_RE.sub("[STRIPPED_HOST_PATH]", text)
    return text


def _sanitize_suitcase_tree(
    node: Any,
    *,
    source_label: str,
    field_path: str = "$",
    stripped_bindings: list[str],
    strip_pack_host_sections: bool = False,
    depth: int = 0,
) -> Any:
    if depth > 24:
        return None
    if isinstance(node, Mapping):
        cleaned: dict[str, Any] = {}
        for raw_k, raw_v in node.items():
            k = str(raw_k)
            low_k = k.strip().lower()
            child_path = f"{field_path}.{k}"
            if _is_secret_or_permit_key(k):
                marker = f"{source_label}:{child_path}:secret_or_permit"
                if marker not in stripped_bindings:
                    stripped_bindings.append(marker)
                continue
            if strip_pack_host_sections and low_k in ("deploy_only", "user_only", "host_bindings"):
                marker = f"{source_label}:{child_path}:host_binding_section"
                if marker not in stripped_bindings:
                    stripped_bindings.append(marker)
                continue
            if _is_host_path_binding_key(k) and isinstance(raw_v, str) and raw_v.strip():
                marker = f"{source_label}:{child_path}:host_binding_key"
                if marker not in stripped_bindings:
                    stripped_bindings.append(marker)
                continue
            cleaned[k] = _sanitize_suitcase_tree(
                raw_v,
                source_label=source_label,
                field_path=child_path,
                stripped_bindings=stripped_bindings,
                strip_pack_host_sections=strip_pack_host_sections,
                depth=depth + 1,
            )
        return cleaned
    if isinstance(node, list):
        return [
            _sanitize_suitcase_tree(
                elem,
                source_label=source_label,
                field_path=f"{field_path}[{idx}]",
                stripped_bindings=stripped_bindings,
                strip_pack_host_sections=strip_pack_host_sections,
                depth=depth + 1,
            )
            for idx, elem in enumerate(node)
        ]
    if isinstance(node, str):
        return _redact_host_paths_in_str(
            node,
            source_label=source_label,
            field_path=field_path,
            stripped_bindings=stripped_bindings,
        )
    if isinstance(node, (int, float, bool)) or node is None:
        return node
    return str(node)


def _scrub_tombstoned_ids_from_tree(
    node: Any,
    tombstoned_ids: frozenset[str] | set[str],
    *,
    vc_sealed_digests: Optional[frozenset[str] | set[str]] = None,
    matched_ids_out: Optional[set[str]] = None,
    in_tombstone_container: bool = False,
    depth: int = 0,
) -> Any:
    if (not tombstoned_ids and not vc_sealed_digests) or depth > 24:
        return node
    if in_tombstone_container:
        return copy.deepcopy(node)

    def _is_dead(raw_candidate: Any) -> bool:
        if not isinstance(raw_candidate, str):
            return False
        cand = raw_candidate.strip()
        if not cand:
            return False
        if cand in tombstoned_ids:
            if matched_ids_out is not None:
                matched_ids_out.add(cand)
            return True
        if vc_sealed_digests and compute_vc_item_digest(cand) in vc_sealed_digests:
            if matched_ids_out is not None:
                matched_ids_out.add(cand)
            return True
        return False

    if isinstance(node, Mapping):
        out: dict[str, Any] = {}
        for raw_k, raw_v in node.items():
            k = str(raw_k)
            if _is_dead(k):
                continue
            if k in ("work", "active_work", "featured_item") and isinstance(raw_v, Mapping):
                matched_nested = any(_is_dead(raw_v.get(id_k)) for id_k in _TOMBSTONE_MATCH_ID_KEYS)
                if matched_nested:
                    out[k] = None
                    continue
            is_ts_container = k in ("tombstones", "tombstone_ledger")
            out[k] = _scrub_tombstoned_ids_from_tree(
                raw_v,
                tombstoned_ids,
                vc_sealed_digests=vc_sealed_digests,
                matched_ids_out=matched_ids_out,
                in_tombstone_container=is_ts_container,
                depth=depth + 1,
            )
        return out
    if isinstance(node, list):
        out_list: list[Any] = []
        for elem in node:
            if _is_dead(elem):
                continue
            if isinstance(elem, Mapping):
                matched_dead = any(_is_dead(elem.get(id_key)) for id_key in _TOMBSTONE_MATCH_ID_KEYS)
                if matched_dead:
                    continue
            out_list.append(
                _scrub_tombstoned_ids_from_tree(
                    elem,
                    tombstoned_ids,
                    vc_sealed_digests=vc_sealed_digests,
                    matched_ids_out=matched_ids_out,
                    in_tombstone_container=False,
                    depth=depth + 1,
                )
            )
        return out_list
    return node


def _normalize_tombstone_entries_from_any(raw_ts: Any, *, default_now_iso: str) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    if isinstance(raw_ts, Mapping):
        for raw_id, raw_entry in raw_ts.items():
            mid = str(raw_id or "").strip()
            if isinstance(raw_entry, Mapping):
                mid = str(raw_entry.get("memory_id") or raw_entry.get("id") or mid).strip()
                if not mid:
                    continue
                merged[mid] = {
                    "memory_id": mid,
                    "reason": str(raw_entry.get("reason") or "user_recalled_or_deleted").strip()[:120],
                    "tombstoned_at": str(raw_entry.get("tombstoned_at") or default_now_iso).strip()[:40],
                    "tombstoned": True,
                }
            elif mid:
                merged[mid] = {
                    "memory_id": mid,
                    "reason": "user_recalled_or_deleted",
                    "tombstoned_at": default_now_iso,
                    "tombstoned": True,
                }
    elif isinstance(raw_ts, list):
        for raw_entry in raw_ts:
            if isinstance(raw_entry, str) and raw_entry.strip():
                mid = raw_entry.strip()
                merged[mid] = {
                    "memory_id": mid,
                    "reason": "user_recalled_or_deleted",
                    "tombstoned_at": default_now_iso,
                    "tombstoned": True,
                }
            elif isinstance(raw_entry, Mapping):
                mid = str(raw_entry.get("memory_id") or raw_entry.get("id") or "").strip()
                if not mid:
                    continue
                merged[mid] = {
                    "memory_id": mid,
                    "reason": str(raw_entry.get("reason") or "user_recalled_or_deleted").strip()[:120],
                    "tombstoned_at": str(raw_entry.get("tombstoned_at") or default_now_iso).strip()[:40],
                    "tombstoned": True,
                }
    return merged


def _read_vc_sealed_digest_set_readonly(day_root: Path) -> frozenset[str]:
    wm = read_vc_authority_watermark_readonly(day_root)
    raw = wm.get("sealed_digests")
    if not isinstance(raw, Mapping):
        return frozenset()
    return frozenset(str(k).strip().lower() for k in raw.keys() if str(k).strip())


def _extract_tombstone_ledger_readonly(day_root: Path, *, now_iso: str) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    candidates = (
        day_root / STATE_DIRNAME / "v02_recall_projection.json",
        day_root / "memory" / "v02_recall_projection_state.json",
    )
    for cand in candidates:
        try:
            if cand.is_symlink():
                continue
        except OSError:
            continue
        ex, ok, doc = _read_json_safe(cand)
        if not ex or not ok or not isinstance(doc, Mapping):
            continue
        for src_key in ("tombstones", "tombstone_ledger"):
            if src_key in doc:
                merged.update(_normalize_tombstone_entries_from_any(doc.get(src_key), default_now_iso=now_iso))
    merged.update(_read_governance_tombstones_readonly(day_root, now_iso=now_iso))
    return merged


def _slices_for_effective_mode(
    effective_mode: str,
    *,
    requested_mode: str,
    keep_skill_ids: Optional[Sequence[str]] = None,
) -> tuple[str, ...]:
    if effective_mode == SUITCASE_RECORDS_ONLY:
        return SUITCASE_RECORDS_ONLY_SLICES
    if effective_mode == SUITCASE_RECORDS_AND_OBJECTS:
        return SUITCASE_RECORDS_ONLY_SLICES + SUITCASE_RECORDS_AND_OBJECTS_EXTRA_SLICES
    base = SUITCASE_RECORDS_ONLY_SLICES + SUITCASE_RECORDS_AND_OBJECTS_EXTRA_SLICES
    if requested_mode == SUITCASE_SELECTIVE_PRACTICE and keep_skill_ids is not None:
        explicit_practice_slices: list[str] = []
        has_any_slice_selector = False
        for raw_sid in keep_skill_ids:
            sid = str(raw_sid or "").strip()
            if sid.startswith("phone_slice:"):
                has_any_slice_selector = True
                fname = sid.split("phone_slice:", 1)[1].strip()
                if fname in SUITCASE_PRACTICE_EXTRA_SLICES and fname not in explicit_practice_slices:
                    explicit_practice_slices.append(fname)
            elif sid in SUITCASE_PRACTICE_EXTRA_SLICES:
                has_any_slice_selector = True
                if sid not in explicit_practice_slices:
                    explicit_practice_slices.append(sid)
        if has_any_slice_selector:
            return base + tuple(explicit_practice_slices)
    return SUITCASE_ALLOWED_SLICE_FILES


def _is_safe_nonsymlink_rel(day_root: Path, rel_parts: Sequence[str]) -> bool:
    cur = day_root
    try:
        if cur.exists() and cur.is_symlink():
            return False
        for part in rel_parts:
            cur = cur / part
            if cur.is_symlink():
                return False
    except OSError:
        return False
    return True


def _read_json_doc_or_list_safe(path: Path) -> tuple[bool, bool, Any]:
    """Return ``(exists, valid_json_container, payload)`` accepting dict or list (zero writes)."""
    if not path.is_file():
        return False, True, None
    try:
        raw = path.read_text(encoding="utf-8")
        parsed = json.loads(raw)
    except (OSError, ValueError):
        return True, False, None
    if isinstance(parsed, Mapping):
        return True, True, dict(parsed)
    if isinstance(parsed, list):
        return True, True, list(parsed)
    return True, False, None


def _doc_freshness_rank(path: Path, doc: Mapping[str, Any]) -> tuple[int, int]:
    rev_raw = doc.get("revision")
    rev = int(rev_raw) if isinstance(rev_raw, int) and not isinstance(rev_raw, bool) else 0
    try:
        mtime_ns = int(path.stat().st_mtime_ns)
    except OSError:
        mtime_ns = 0
    return (rev, mtime_ns)


def _read_slice_or_companion_readonly_with_source(
    day_root: Path, slice_file: str
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    primary_rel: tuple[str, ...] = (STATE_DIRNAME, slice_file)
    companions = _SUITCASE_COMPANION_FALLBACKS.get(slice_file, ())

    if slice_file in _SUITCASE_CANONICAL_RESTORE_MIRRORS:
        best_doc: Optional[dict[str, Any]] = None
        best_src: Optional[str] = None
        best_rank: tuple[int, int] = (-1, -1)
        for rel_parts in (primary_rel, *companions):
            if not _is_safe_nonsymlink_rel(day_root, rel_parts):
                continue
            cand = day_root.joinpath(*rel_parts)
            ex, ok, doc = _read_json_safe(cand)
            if ex and ok and isinstance(doc, Mapping):
                rank = _doc_freshness_rank(cand, doc)
                if best_doc is None or rank >= best_rank:
                    best_doc = copy.deepcopy(dict(doc))
                    best_src = "/".join(rel_parts)
                    best_rank = rank
        return best_doc, best_src

    if _is_safe_nonsymlink_rel(day_root, primary_rel):
        primary = day_root.joinpath(*primary_rel)
        ex, ok, doc = _read_json_safe(primary)
        if ex and ok and isinstance(doc, Mapping):
            return copy.deepcopy(dict(doc)), "/".join(primary_rel)
    for rel_parts in companions:
        if not _is_safe_nonsymlink_rel(day_root, rel_parts):
            continue
        comp = day_root.joinpath(*rel_parts)
        ex_c, ok_c, doc_c = _read_json_safe(comp)
        if ex_c and ok_c and isinstance(doc_c, Mapping):
            return copy.deepcopy(dict(doc_c)), "/".join(rel_parts)
    return None, None


def _read_slice_or_companion_readonly(day_root: Path, slice_file: str) -> Optional[dict[str, Any]]:
    doc, _ = _read_slice_or_companion_readonly_with_source(day_root, slice_file)
    return doc


def _read_world_slice_readonly_with_source(
    day_root: Path, world_file: str
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    candidates = _WORLD_SLICE_READ_CANDIDATES.get(
        world_file,
        (("world", world_file), ("world-home", "world", world_file)),
    )
    world_tier_cands = [c for c in candidates if "world" in c]
    fallback_cands = [c for c in candidates if "world" not in c]

    for group in (world_tier_cands, fallback_cands):
        best_doc: Optional[dict[str, Any]] = None
        best_src: Optional[str] = None
        best_rank: tuple[int, int] = (-1, -1)
        for rel_parts in group:
            if not _is_safe_nonsymlink_rel(day_root, rel_parts):
                continue
            cand = day_root.joinpath(*rel_parts)
            ex, ok, doc = _read_json_safe(cand)
            if ex and ok and isinstance(doc, Mapping):
                rank = _doc_freshness_rank(cand, doc)
                if best_doc is None or rank >= best_rank:
                    best_doc = copy.deepcopy(dict(doc))
                    best_src = "/".join(rel_parts)
                    best_rank = rank
        if best_doc is not None:
            return best_doc, best_src
    return None, None


def _read_world_package_readonly(
    day_root: Path,
    *,
    stripped_bindings: Optional[list[str]] = None,
) -> tuple[dict[str, Any], bool]:
    wp_rel_candidates: tuple[tuple[str, ...], ...] = (
        ("world-package",),
        ("world-home", "world-package"),
    )
    chosen_dir: Optional[Path] = None
    chosen_prefix = "world-package"
    for rel_parts in wp_rel_candidates:
        cur = day_root
        has_symlink = False
        try:
            if cur.exists() and cur.is_symlink():
                has_symlink = True
            else:
                for seg in rel_parts:
                    cur = cur / seg
                    if cur.is_symlink():
                        has_symlink = True
                        break
        except OSError:
            continue
        if has_symlink:
            if stripped_bindings is not None:
                marker = f"{'/'.join(rel_parts)}:symlink_skipped"
                if marker not in stripped_bindings:
                    stripped_bindings.append(marker)
            continue
        try:
            if cur.is_dir():
                chosen_dir = cur
                chosen_prefix = "/".join(rel_parts)
                break
        except OSError:
            continue

    if chosen_dir is None:
        return {}, False

    total_bytes = 0
    raw_files: dict[str, Any] = {}
    for fname in SUITCASE_ALLOWED_WORLD_PACKAGE_FILES:
        fpath = chosen_dir / fname
        try_names = (fname, "MANIFEST.json") if fname == "manifest.json" else (fname,)
        resolved_file: Optional[Path] = None
        for cand_name in try_names:
            cand_p = chosen_dir / cand_name
            try:
                if cand_p.is_symlink():
                    if stripped_bindings is not None:
                        marker = f"{chosen_prefix}/{cand_name}:symlink_skipped"
                        if marker not in stripped_bindings:
                            stripped_bindings.append(marker)
                    continue
                if cand_p.is_file():
                    resolved_file = cand_p
                    break
            except OSError:
                continue
        if resolved_file is None:
            continue
        try:
            fsize = int(resolved_file.stat().st_size)
        except OSError:
            continue
        total_bytes += fsize
        if total_bytes > SUITCASE_WORLD_PACKAGE_MAX_TOTAL_BYTES:
            if stripped_bindings is not None:
                marker = f"{chosen_prefix}:exceeds_2mb_limit"
                if marker not in stripped_bindings:
                    stripped_bindings.append(marker)
            return {}, True
        ex_f, ok_f, parsed_f = _read_json_doc_or_list_safe(resolved_file)
        if ex_f and ok_f and parsed_f is not None:
            raw_files[fname] = copy.deepcopy(parsed_f)

    return raw_files, False


def _inspect_world_package_readonly(day_root: Path) -> tuple[list[dict[str, Any]], bool]:
    docs, oversized = _read_world_package_readonly(day_root, stripped_bindings=None)
    if oversized:
        return [], True
    rows = [
        {
            "package_file": fname,
            "source": f"world-package/{fname}",
        }
        for fname in SUITCASE_ALLOWED_WORLD_PACKAGE_FILES
        if fname in docs
    ]
    return rows, False


def _build_migration_loss_manifest(
    *,
    profile_mode: str,
    effective_mode: str,
    has_phone_slices: bool,
    phone_slice_count: int,
    has_character_pack: bool,
    world_slice_names: Sequence[str],
    world_package_files: Sequence[str],
    tombstone_count: int,
    carrier_sec: Mapping[str, Any],
    world_package_oversized: bool = False,
) -> dict[str, Any]:
    """Build deterministic G4 ``migration_loss_manifest`` (Ch.14 G4 / EP-18)."""
    carried_domains: list[str] = []
    carried_domains_detail: list[dict[str, Any]] = []

    if has_phone_slices:
        carried_domains.append("phone_slices")
        carried_domains_detail.append({
            "domain": "phone_slices",
            "label_cn": "便携日常切片",
            "count": int(phone_slice_count),
            "summary_cn": f"已纳入 {int(phone_slice_count)} 份便携日常与生活记录切片",
        })
    if has_character_pack:
        carried_domains.append("character_pack")
        carried_domains_detail.append({
            "domain": "character_pack",
            "label_cn": "角色设定包",
            "count": 1,
            "summary_cn": "已纳入剥离宿主绑定后的角色基础设定包",
        })
    if world_slice_names:
        carried_domains.append("world_matter_and_traces")
        carried_domains_detail.append({
            "domain": "world_matter_and_traces",
            "label_cn": "空间物质状态与生活痕迹",
            "count": len(world_slice_names),
            "files": list(world_slice_names),
            "summary_cn": f"已纳入 {len(world_slice_names)} 份空间器物、交互痕迹与微气候切片",
        })
    if world_package_files:
        carried_domains.append("custom_world_package")
        carried_domains_detail.append({
            "domain": "custom_world_package",
            "label_cn": "自定义世界包",
            "count": len(world_package_files),
            "files": list(world_package_files),
            "summary_cn": f"已纳入 {len(world_package_files)} 份自定义世界包声明文件（含售卖规格与物质档）",
        })
    carried_domains.append("tombstone_watermarks")
    carried_domains_detail.append({
        "domain": "tombstone_watermarks",
        "label_cn": "遗忘与撤回墓碑水位",
        "count": int(tombstone_count),
        "summary_cn": f"强制随包携带墓碑防复活水位（当前共 {int(tombstone_count)} 条墓碑记录）",
    })

    intentionally_left_behind: list[dict[str, Any]] = [
        {
            "domain": "host_credentials_and_api_keys",
            "title_cn": "宿主凭据、API 密钥与访问令牌",
            "reason_cn": "绑定原宿主运行环境的安全密钥、连接串与本机绝对路径，跨机携带会造成泄漏或失配，一律剥离。",
        },
        {
            "domain": "active_36b_permits_and_solo_partition",
            "title_cn": "36B 活跃亲密许可与独处私密分区账本",
            "reason_cn": "严格执行 36B 隐私与同意红线：即时许可不可跨环境延续，adult_intimacy 与 solo 私密分区账本永不随包导出。",
        },
        {
            "domain": "transient_sockets_and_locks",
            "title_cn": "瞬态套接字、文件锁与运行期队列句柄",
            "reason_cn": "仅对原机器当次进程有效的瞬态运行状态，迁入新环境后按需重建。",
        },
    ]

    if effective_mode == SUITCASE_RECORDS_ONLY:
        intentionally_left_behind.append({
            "domain": "world_objects",
            "title_cn": "空间物质器物、交互痕迹、微气候与自定义世界包",
            "reason_cn": "当前装箱档位为仅记录模式（records_only），空间器物切片与自定义世界包按约定留在原处。",
        })
    if effective_mode in (SUITCASE_RECORDS_ONLY, SUITCASE_RECORDS_AND_OBJECTS):
        intentionally_left_behind.append({
            "domain": "practice_habits",
            "title_cn": "个人起居习惯、涌现回响与可组合身体节律",
            "reason_cn": (
                "当前装箱档位为仅记录模式（records_only），个人日常习惯与身体节律切片主动留在原处，去新环境从简重起。"
                if effective_mode == SUITCASE_RECORDS_ONLY
                else "当前装箱档位不携带习惯层（records_and_objects 或 keep_practice=False），个人习惯与节律留在原处。"
            ),
        })
    if world_package_oversized and effective_mode != SUITCASE_RECORDS_ONLY:
        intentionally_left_behind.append({
            "domain": "oversized_custom_world_package",
            "title_cn": "超限自定义世界包（超过 2 MB 便携上限）",
            "reason_cn": "自定义世界包 JSON 总体积超过 2 MB 便携上限，未内联塞入便携旅行箱。",
        })

    capability_recheck_required: list[dict[str, Any]] = []
    seen_recheck_ids: set[str] = set()
    skills_map = carrier_sec.get("skills") if isinstance(carrier_sec.get("skills"), Mapping) else {}
    fp = carrier_sec.get("carrier_fingerprint") if isinstance(carrier_sec.get("carrier_fingerprint"), Mapping) else {}
    preview_ok = bool(fp.get("preview_available", True))

    for raw_sid, raw_sk in skills_map.items():
        if not isinstance(raw_sk, Mapping):
            continue
        sid = str(raw_sid)
        sk_status = str(raw_sk.get("status") or SKILL_STATUS_VALIDATED)
        requires_prev = bool(raw_sk.get("requires_preview", False))
        if requires_prev and not preview_ok:
            seen_recheck_ids.add(sid)
            capability_recheck_required.append({
                "item_id": sid,
                "skill_id": sid,
                "title_cn": str(raw_sk.get("title_cn") or sid),
                "status": "HOST_LIVE_PREVIEW_UNAVAILABLE",
                "reason_cn": "目标宿主缺少实时预览窗能力，需改用纯文本核对或在补齐预览能力后重新核验。",
            })
        elif sk_status in (
            SKILL_STATUS_UNVALIDATED,
            SKILL_STATUS_PENDING_REVERIFY,
            SKILL_STATUS_SUSPENDED_LOSS,
        ):
            seen_recheck_ids.add(sid)
            capability_recheck_required.append({
                "item_id": sid,
                "skill_id": sid,
                "title_cn": str(raw_sk.get("title_cn") or sid),
                "status": sk_status,
                "reason_cn": "目标模型或宿主环境变更后，需通过小样试跑重新核验该技法是否仍保持原有表现。",
            })

    for loss_item in carrier_sec.get("loss_report") or ():
        if not isinstance(loss_item, Mapping):
            continue
        lcode = str(loss_item.get("loss_code") or "").strip()
        lid = str(loss_item.get("skill_id") or lcode).strip()
        if lid and lid not in seen_recheck_ids:
            seen_recheck_ids.add(lid)
            capability_recheck_required.append({
                "item_id": lid,
                "skill_id": lid,
                "title_cn": str(loss_item.get("summary_cn") or lid)[:60],
                "status": str(loss_item.get("status") or lcode or SKILL_STATUS_PENDING_REVERIFY),
                "reason_cn": str(loss_item.get("summary_cn") or "迁入目标载体后需重新核对可用性。"),
            })

    return {
        "schema": MIGRATION_LOSS_MANIFEST_SCHEMA,
        "profile_mode": profile_mode,
        "effective_profile_mode": effective_mode,
        "carried_domains": carried_domains,
        "carried_domains_detail": carried_domains_detail,
        "intentionally_left_behind": intentionally_left_behind,
        "intentionally_left_behind_domains": [
            row["domain"] for row in intentionally_left_behind
        ],
        "capability_recheck_required": capability_recheck_required,
        "tombstone_anti_resurrection_enforced": True,
    }


def _build_export_suitcase_bundle(
    day_root: Path,
    *,
    state: Mapping[str, Any],
    payload: Mapping[str, Any],
    now_iso: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    raw_mode = str(payload.get("profile_mode") or state.get("suitcase_state", {}).get("profile_mode") or SUITCASE_SELECTIVE_PRACTICE).strip()
    if raw_mode not in SUITCASE_PROFILE_MODES:
        raise V02ValidationError("INVALID_MODE", f"Unsupported suitcase profile_mode: {raw_mode!r}")

    if "keep_practice" in payload and payload["keep_practice"] is not None:
        if not isinstance(payload["keep_practice"], bool):
            raise V02ValidationError("INVALID_FIELDS", "keep_practice must be a boolean")
        keep_practice = bool(payload["keep_practice"])
    else:
        keep_practice = True

    effective_mode = (
        SUITCASE_RECORDS_AND_OBJECTS
        if (raw_mode in (SUITCASE_FULL_PRACTICE, SUITCASE_SELECTIVE_PRACTICE) and not keep_practice)
        else raw_mode
    )

    keep_skill_ids = payload.get("keep_skill_ids")
    selected_slice_files = _slices_for_effective_mode(
        effective_mode,
        requested_mode=raw_mode,
        keep_skill_ids=keep_skill_ids if isinstance(keep_skill_ids, list) else None,
    )

    stripped_host_bindings: list[str] = []

    adult_file = day_root / STATE_DIRNAME / "adult_intimacy.json"
    try:
        if adult_file.exists() or adult_file.is_symlink():
            stripped_host_bindings.append("phone/adult_intimacy.json:session_permits")
    except OSError:
        pass
    for solo_rel in (
        (STATE_DIRNAME, "solo_partition.json"),
        (STATE_DIRNAME, "adult_solo.json"),
        ("solo",),
    ):
        solo_p = day_root.joinpath(*solo_rel)
        try:
            if solo_p.exists() or solo_p.is_symlink():
                stripped_host_bindings.append(f"{'/'.join(solo_rel)}:36b_solo_excluded")
        except OSError:
            pass

    tombstones_map = _extract_tombstone_ledger_readonly(day_root, now_iso=now_iso)
    vc_digests = _read_vc_sealed_digest_set_readonly(day_root)
    tombstone_ids = frozenset(tombstones_map.keys())
    tombstone_ledger = [tombstones_map[k] for k in sorted(tombstones_map.keys())]

    sanitized_character_pack: Optional[dict[str, Any]] = None
    for pack_rel in (
        (STATE_DIRNAME, "character_pack_state.json"),
        ("character_pack.json",),
        (STATE_DIRNAME, "character_pack.json"),
    ):
        if not _is_safe_nonsymlink_rel(day_root, pack_rel):
            continue
        pack_path = day_root.joinpath(*pack_rel)
        ex_p, ok_p, p_doc = _read_json_safe(pack_path)
        if ex_p and ok_p and isinstance(p_doc, Mapping) and p_doc:
            cleaned_pack = _sanitize_suitcase_tree(
                p_doc,
                source_label="/".join(pack_rel),
                stripped_bindings=stripped_host_bindings,
                strip_pack_host_sections=True,
            )
            cleaned_pack = _scrub_tombstoned_ids_from_tree(
                cleaned_pack, tombstone_ids, vc_sealed_digests=vc_digests
            )
            if isinstance(cleaned_pack, dict):
                sanitized_character_pack = cleaned_pack
                break

    exported_slices: dict[str, dict[str, Any]] = {}
    for slice_file in selected_slice_files:
        raw_doc = _read_slice_or_companion_readonly(day_root, slice_file)
        if raw_doc is None:
            continue
        cleaned_doc = _sanitize_suitcase_tree(
            raw_doc,
            source_label=f"phone/{slice_file}",
            stripped_bindings=stripped_host_bindings,
            strip_pack_host_sections=False,
        )
        cleaned_doc = _scrub_tombstoned_ids_from_tree(
            cleaned_doc, tombstone_ids, vc_sealed_digests=vc_digests
        )
        if isinstance(cleaned_doc, dict):
            if slice_file == "v02_recall_projection.json" and tombstones_map:
                existing_ts = _normalize_tombstone_entries_from_any(
                    cleaned_doc.get("tombstones"), default_now_iso=now_iso
                )
                existing_ts.update(copy.deepcopy(tombstones_map))
                cleaned_doc["tombstones"] = existing_ts
            exported_slices[slice_file] = cleaned_doc

    if "v02_recall_projection.json" in selected_slice_files and tombstones_map and "v02_recall_projection.json" not in exported_slices:
        exported_slices["v02_recall_projection.json"] = {
            "schema": "nyairo.v02.recall_projection.v1",
            "memories": {},
            "tombstones": copy.deepcopy(tombstones_map),
            "updated_at": now_iso,
        }

    exported_world_slices: dict[str, dict[str, Any]] = {}
    exported_world_package: dict[str, Any] = {}
    wp_oversized = False
    if effective_mode != SUITCASE_RECORDS_ONLY:
        for w_file in SUITCASE_ALLOWED_WORLD_FILES:
            raw_w_doc, _ = _read_world_slice_readonly_with_source(day_root, w_file)
            if raw_w_doc is None:
                continue
            cleaned_w_doc = _sanitize_suitcase_tree(
                raw_w_doc,
                source_label=f"world/{w_file}",
                stripped_bindings=stripped_host_bindings,
                strip_pack_host_sections=False,
            )
            cleaned_w_doc = _scrub_tombstoned_ids_from_tree(
                cleaned_w_doc, tombstone_ids, vc_sealed_digests=vc_digests
            )
            if isinstance(cleaned_w_doc, dict):
                exported_world_slices[w_file] = cleaned_w_doc

        raw_wp_docs, wp_oversized = _read_world_package_readonly(
            day_root, stripped_bindings=stripped_host_bindings
        )
        for wp_fname, raw_wp_doc in raw_wp_docs.items():
            cleaned_wp = _sanitize_suitcase_tree(
                raw_wp_doc,
                source_label=f"world-package/{wp_fname}",
                stripped_bindings=stripped_host_bindings,
                strip_pack_host_sections=False,
            )
            cleaned_wp = _scrub_tombstoned_ids_from_tree(
                cleaned_wp, tombstone_ids, vc_sealed_digests=vc_digests
            )
            if isinstance(cleaned_wp, (dict, list)):
                exported_world_package[wp_fname] = cleaned_wp

    included_items: list[str] = []
    if sanitized_character_pack is not None:
        included_items.append("character_pack_state.json")
    included_items.extend(exported_slices.keys())
    included_items.extend(f"world/{w}" for w in exported_world_slices.keys())
    included_items.extend(f"world-package/{p}" for p in exported_world_package.keys())

    loss_manifest = _build_migration_loss_manifest(
        profile_mode=raw_mode,
        effective_mode=effective_mode,
        has_phone_slices=bool(exported_slices),
        phone_slice_count=len(exported_slices),
        has_character_pack=(sanitized_character_pack is not None),
        world_slice_names=list(exported_world_slices.keys()),
        world_package_files=list(exported_world_package.keys()),
        tombstone_count=len(tombstone_ledger),
        carrier_sec=state.get("carrier_migration") or {},
        world_package_oversized=wp_oversized,
    )

    bundle: dict[str, Any] = {
        "schema": SUITCASE_BUNDLE_SCHEMA,
        "bundle_version": SUITCASE_BUNDLE_VERSION,
        "exported_at": now_iso,
        "profile_mode": raw_mode,
        "effective_profile_mode": effective_mode,
        "keep_practice": keep_practice,
        "character_pack": sanitized_character_pack,
        "slices": exported_slices,
        "tombstone_ledger": tombstone_ledger,
        "included_items": included_items,
        "stripped_host_bindings": stripped_host_bindings,
        "migration_loss_manifest": loss_manifest,
    }
    if exported_world_slices:
        bundle["world_slices"] = exported_world_slices
    if exported_world_package:
        bundle["world_package"] = exported_world_package
    bundle["bundle_sha256"] = compute_suitcase_bundle_sha256(bundle)

    summary = {
        "profile_mode": raw_mode,
        "effective_profile_mode": effective_mode,
        "keep_practice": keep_practice,
        "slice_count": len(exported_slices),
        "world_slice_count": len(exported_world_slices),
        "world_package_file_count": len(exported_world_package),
        "has_world_package": bool(exported_world_package),
        "included_count": len(included_items),
        "tombstone_count": len(tombstone_ledger),
        "stripped_count": len(stripped_host_bindings),
        "has_character_pack": sanitized_character_pack is not None,
        "migration_loss_manifest": copy.deepcopy(loss_manifest),
        "bundle_sha256": bundle["bundle_sha256"],
        "summary_cn": (
            f"已按「{effective_mode}」打包便携旅行箱：包含 {len(exported_slices)} 份生活切片"
            f"{f'、{len(exported_world_slices)} 份空间物质切片' if exported_world_slices else ''}"
            f"{f'、{len(exported_world_package)} 份自建世界包文件' if exported_world_package else ''}"
            f"{'与角色设定包' if sanitized_character_pack is not None else ''}，"
            f"携带 {len(tombstone_ledger)} 条遗忘墓碑水位，剥离 {len(stripped_host_bindings)} 项宿主绑定或敏感凭据。"
        ),
    }
    return bundle, summary


def _apply_export_suitcase(
    day_root: Path,
    *,
    state: dict[str, Any],
    payload: Mapping[str, Any],
    now_iso: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    bundle, summary = _build_export_suitcase_bundle(
        day_root,
        state=state,
        payload=payload,
        now_iso=now_iso,
    )
    suit = state["suitcase_state"]
    suit["profile_mode"] = bundle["profile_mode"]
    suit["packed_at"] = now_iso
    if "leave_behind_note" in payload and payload["leave_behind_note"] is not None:
        suit["leave_behind_note"] = str(payload["leave_behind_note"]).strip()
    return bundle, summary


def _assert_no_secret_keys_recursive(node: Any, *, depth: int = 0) -> None:
    if depth > 24:
        raise V02ValidationError("INVALID_FIELDS", "Suitcase bundle nesting depth exceeds limit")
    if isinstance(node, Mapping):
        for raw_k, raw_v in node.items():
            if not isinstance(raw_k, str):
                raise V02ValidationError("INVALID_FIELDS", "Suitcase bundle keys must be strings")
            if _is_secret_or_permit_key(raw_k):
                raise V02ValidationError(
                    "SUITCASE_SECRET_KEY_FORBIDDEN",
                    f"Forbidden secret or permit key in suitcase bundle: {raw_k!r}",
                )
            _assert_no_secret_keys_recursive(raw_v, depth=depth + 1)
    elif isinstance(node, list):
        for elem in node:
            _assert_no_secret_keys_recursive(elem, depth=depth + 1)


def _validate_allowlisted_filename(
    raw_name: Any,
    *,
    allowed_set: frozenset[str],
    kind_label: str,
) -> str:
    if not isinstance(raw_name, str) or not raw_name.strip():
        raise V02ValidationError("FORBIDDEN_SLICE_FILENAME", f"{kind_label} filename must be a non-empty string")
    fname = raw_name.strip()
    if (
        "/" in fname
        or "\\" in fname
        or ".." in fname
        or ":" in fname
        or "\x00" in fname
        or fname != Path(fname).name
    ):
        raise V02ValidationError(
            "FORBIDDEN_SLICE_FILENAME",
            f"Path traversal or separator forbidden in {kind_label} filename: {raw_name!r}",
        )
    if fname not in allowed_set:
        raise V02ValidationError(
            "FORBIDDEN_SLICE_FILENAME",
            f"{kind_label} filename is not in portable suitcase allowlist: {fname!r}",
        )
    return fname


def _validate_slice_filename_allowlist(slice_name: Any) -> str:
    return _validate_allowlisted_filename(
        slice_name,
        allowed_set=SUITCASE_ALLOWED_SLICE_SET,
        kind_label="Slice",
    )


def _validate_world_slice_filename_allowlist(world_name: Any) -> str:
    return _validate_allowlisted_filename(
        world_name,
        allowed_set=SUITCASE_ALLOWED_WORLD_FILE_SET,
        kind_label="World slice",
    )


def _validate_world_package_filename_allowlist(pkg_name: Any) -> str:
    return _validate_allowlisted_filename(
        pkg_name,
        allowed_set=SUITCASE_ALLOWED_WORLD_PACKAGE_FILE_SET,
        kind_label="World package",
    )


def _assert_not_symlink_chain(day_root: Path, rel_parts: Sequence[str], *, label: str) -> None:
    if day_root.is_symlink():
        raise V02ValidationError("SYMLINK_TARGET_FORBIDDEN", "day_root cannot be a symlink")
    cur = day_root
    for seg in rel_parts:
        cur = cur / seg
        if cur.is_symlink():
            raise V02ValidationError("SYMLINK_TARGET_FORBIDDEN", f"Refusing symlink target: {label}")


def _atomic_write_json_any(target_path: Path, payload_obj: Any) -> None:
    if target_path.is_symlink():
        raise V02ValidationError("SYMLINK_TARGET_FORBIDDEN", f"Refusing to write symlink: {target_path.name}")
    parent = target_path.parent
    if parent.is_symlink():
        raise V02ValidationError("SYMLINK_TARGET_FORBIDDEN", "Refusing to write inside symlinked directory")
    parent.mkdir(parents=True, exist_ok=True)
    serializable = dict(payload_obj) if isinstance(payload_obj, Mapping) else list(payload_obj)
    encoded = (json.dumps(serializable, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target_path.stem}.",
        suffix=".tmp",
        dir=str(parent),
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target_path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _atomic_write_json_doc(target_path: Path, doc: Mapping[str, Any]) -> None:
    _atomic_write_json_any(target_path, dict(doc))


def _scrub_existing_json_file_if_present(
    day_root: Path,
    rel_parts: Sequence[str],
    tombstone_ids: frozenset[str],
    *,
    allow_list_root: bool = False,
    vc_sealed_digests: Optional[frozenset[str]] = None,
) -> None:
    if (not tombstone_ids and not vc_sealed_digests) or not _is_safe_nonsymlink_rel(day_root, rel_parts):
        return
    fpath = day_root.joinpath(*rel_parts)
    if allow_list_root:
        ex, ok, doc = _read_json_doc_or_list_safe(fpath)
        if ex and ok and isinstance(doc, (Mapping, list)):
            scrubbed = _scrub_tombstoned_ids_from_tree(
                doc, tombstone_ids, vc_sealed_digests=vc_sealed_digests
            )
            if scrubbed != doc and isinstance(scrubbed, (Mapping, list)):
                _atomic_write_json_any(fpath, scrubbed)
    else:
        ex, ok, doc = _read_json_safe(fpath)
        if ex and ok and isinstance(doc, Mapping):
            scrubbed = _scrub_tombstoned_ids_from_tree(
                doc, tombstone_ids, vc_sealed_digests=vc_sealed_digests
            )
            if scrubbed != doc and isinstance(scrubbed, Mapping):
                _atomic_write_json_doc(fpath, scrubbed)


def _apply_import_suitcase(
    day_root: Path,
    *,
    state: dict[str, Any],
    payload: Mapping[str, Any],
    now_iso: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    raw_bundle = payload.get("bundle")
    if not isinstance(raw_bundle, Mapping):
        raise V02ValidationError("INVALID_FIELDS", "bundle must be a JSON object")

    schema_val = str(raw_bundle.get("schema") or "").strip()
    if schema_val != SUITCASE_BUNDLE_SCHEMA:
        raise V02ValidationError(
            "INVALID_SUITCASE_SCHEMA",
            f"Expected schema {SUITCASE_BUNDLE_SCHEMA!r}, got {schema_val!r}",
        )

    raw_slices = raw_bundle.get("slices")
    if raw_slices is None:
        raw_slices = {}
    if not isinstance(raw_slices, Mapping):
        raise V02ValidationError("INVALID_FIELDS", "bundle.slices must be a JSON object")

    validated_slice_names: list[str] = []
    for raw_sname, raw_sdoc in raw_slices.items():
        clean_sname = _validate_slice_filename_allowlist(raw_sname)
        if not isinstance(raw_sdoc, Mapping):
            raise V02ValidationError("INVALID_FIELDS", f"Slice {clean_sname!r} payload must be a JSON object")
        validated_slice_names.append(clean_sname)

    raw_world_slices = raw_bundle.get("world_slices")
    if raw_world_slices is None:
        raw_world_slices = {}
    if not isinstance(raw_world_slices, Mapping):
        raise V02ValidationError("INVALID_FIELDS", "bundle.world_slices must be a JSON object")

    validated_world_names: list[str] = []
    for raw_wname, raw_wdoc in raw_world_slices.items():
        clean_wname = _validate_world_slice_filename_allowlist(raw_wname)
        if not isinstance(raw_wdoc, Mapping):
            raise V02ValidationError("INVALID_FIELDS", f"World slice {clean_wname!r} payload must be a JSON object")
        validated_world_names.append(clean_wname)

    raw_world_package = raw_bundle.get("world_package")
    if raw_world_package is None:
        raw_world_package = {}
    if not isinstance(raw_world_package, Mapping):
        raise V02ValidationError("INVALID_FIELDS", "bundle.world_package must be a JSON object")

    validated_wp_names: list[str] = []
    wp_encoded_bytes = 0
    for raw_pname, raw_pdoc in raw_world_package.items():
        clean_pname = _validate_world_package_filename_allowlist(raw_pname)
        if not isinstance(raw_pdoc, (Mapping, list)):
            raise V02ValidationError(
                "INVALID_FIELDS",
                f"World package {clean_pname!r} payload must be a JSON object or array",
            )
        wp_encoded_bytes += len(json.dumps(raw_pdoc, ensure_ascii=False).encode("utf-8"))
        if wp_encoded_bytes > SUITCASE_WORLD_PACKAGE_MAX_TOTAL_BYTES:
            raise V02ValidationError(
                "WORLD_PACKAGE_TOO_LARGE",
                "bundle.world_package exceeds 2 MB portable size limit",
            )
        validated_wp_names.append(clean_pname)

    raw_char_pack = raw_bundle.get("character_pack")
    if raw_char_pack is not None and not isinstance(raw_char_pack, Mapping):
        raise V02ValidationError("INVALID_FIELDS", "bundle.character_pack must be a JSON object or null")

    _assert_no_secret_keys_recursive(raw_bundle)

    declared_sha = raw_bundle.get("bundle_sha256")
    if not isinstance(declared_sha, str) or len(declared_sha.strip()) != 64:
        raise V02ValidationError("SUITCASE_CHECKSUM_MISMATCH", "Missing or malformed bundle_sha256")
    computed_sha = compute_suitcase_bundle_sha256(raw_bundle)
    if declared_sha.strip().lower() != computed_sha.lower():
        raise V02ValidationError(
            "SUITCASE_CHECKSUM_MISMATCH",
            "Suitcase bundle_sha256 does not match canonical bundle contents",
        )

    # Pre-flight symlink verification across all target paths before mutating disk
    if day_root.is_symlink():
        raise V02ValidationError("SYMLINK_TARGET_FORBIDDEN", "day_root cannot be a symlink")
    _assert_not_symlink_chain(day_root, (STATE_DIRNAME,), label="phone")
    phone_dir = day_root / STATE_DIRNAME
    for sname in validated_slice_names:
        _assert_not_symlink_chain(day_root, (STATE_DIRNAME, sname), label=f"phone/{sname}")
        for mirror_rel in _SUITCASE_CANONICAL_RESTORE_MIRRORS.get(sname, ()):
            _assert_not_symlink_chain(day_root, mirror_rel, label="/".join(mirror_rel))
    _assert_not_symlink_chain(day_root, (STATE_DIRNAME, "character_pack_state.json"), label="phone/character_pack_state.json")

    world_dir = day_root / "world"
    if validated_world_names:
        _assert_not_symlink_chain(day_root, ("world",), label="world")
        for wname in validated_world_names:
            _assert_not_symlink_chain(day_root, ("world", wname), label=f"world/{wname}")
            if wname == "v08_microzone_ecology.json":
                _assert_not_symlink_chain(day_root, ("world", "v08_microzone_state.json"), label="world/v08_microzone_state.json")
            elif wname == "v08_open_affordance.json":
                _assert_not_symlink_chain(day_root, (STATE_DIRNAME, "v08_living_traces.json"), label="phone/v08_living_traces.json")

    wp_dir = day_root / "world-package"
    if validated_wp_names:
        _assert_not_symlink_chain(day_root, ("world-package",), label="world-package")
        for pname in validated_wp_names:
            _assert_not_symlink_chain(day_root, ("world-package", pname), label=f"world-package/{pname}")

    incoming_ts = _normalize_tombstone_entries_from_any(
        raw_bundle.get("tombstone_ledger"), default_now_iso=now_iso
    )
    incoming_rp_slice = raw_slices.get("v02_recall_projection.json")
    if isinstance(incoming_rp_slice, Mapping) and "tombstones" in incoming_rp_slice:
        incoming_ts.update(
            _normalize_tombstone_entries_from_any(
                incoming_rp_slice.get("tombstones"), default_now_iso=now_iso
            )
        )
    local_ts = _extract_tombstone_ledger_readonly(day_root, now_iso=now_iso)
    vc_digests = _read_vc_sealed_digest_set_readonly(day_root)

    merged_tombstones: dict[str, dict[str, Any]] = {}
    merged_tombstones.update(local_ts)
    merged_tombstones.update(incoming_ts)
    all_tombstone_ids = frozenset(merged_tombstones.keys())
    replayed_tombstone_ids = sorted(incoming_ts.keys())
    vc_matched_on_import: set[str] = set()

    if merged_tombstones:
        _assert_not_symlink_chain(day_root, (STATE_DIRNAME, "v02_recall_projection.json"), label="phone/v02_recall_projection.json")

    if dry_run:
        return {}

    dummy_stripped: list[str] = []
    slices_to_write: dict[str, dict[str, Any]] = {}
    for sname in validated_slice_names:
        sanitized_doc = _sanitize_suitcase_tree(
            raw_slices[sname],
            source_label=f"phone/{sname}",
            stripped_bindings=dummy_stripped,
            strip_pack_host_sections=False,
        )
        scrubbed_doc = _scrub_tombstoned_ids_from_tree(
            sanitized_doc,
            all_tombstone_ids,
            vc_sealed_digests=vc_digests,
            matched_ids_out=vc_matched_on_import,
        )
        if isinstance(scrubbed_doc, dict):
            slices_to_write[sname] = scrubbed_doc

    world_slices_to_write: dict[str, dict[str, Any]] = {}
    for wname in validated_world_names:
        sanitized_w = _sanitize_suitcase_tree(
            raw_world_slices[wname],
            source_label=f"world/{wname}",
            stripped_bindings=dummy_stripped,
            strip_pack_host_sections=False,
        )
        scrubbed_w = _scrub_tombstoned_ids_from_tree(
            sanitized_w,
            all_tombstone_ids,
            vc_sealed_digests=vc_digests,
            matched_ids_out=vc_matched_on_import,
        )
        if isinstance(scrubbed_w, dict):
            world_slices_to_write[wname] = scrubbed_w

    wp_files_to_write: dict[str, Any] = {}
    for pname in validated_wp_names:
        sanitized_p = _sanitize_suitcase_tree(
            raw_world_package[pname],
            source_label=f"world-package/{pname}",
            stripped_bindings=dummy_stripped,
            strip_pack_host_sections=False,
        )
        scrubbed_p = _scrub_tombstoned_ids_from_tree(
            sanitized_p,
            all_tombstone_ids,
            vc_sealed_digests=vc_digests,
            matched_ids_out=vc_matched_on_import,
        )
        if isinstance(scrubbed_p, (dict, list)):
            wp_files_to_write[pname] = scrubbed_p

    for did in sorted(vc_matched_on_import):
        if did not in merged_tombstones:
            merged_tombstones[did] = {
                "memory_id": did,
                "reason": "vc_barrier_reconciled",
                "tombstoned_at": now_iso,
                "tombstoned": True,
            }
    all_tombstone_ids = frozenset(merged_tombstones.keys())

    # Always replay merged tombstones into v02_recall_projection.json when present or non-empty
    if "v02_recall_projection.json" in slices_to_write or merged_tombstones:
        base_rp = slices_to_write.get("v02_recall_projection.json")
        if base_rp is None:
            ex_rp, ok_rp, disk_rp = _read_json_safe(phone_dir / "v02_recall_projection.json")
            base_rp = copy.deepcopy(dict(disk_rp)) if (ex_rp and ok_rp and isinstance(disk_rp, Mapping)) else {
                "schema": "nyairo.v02.recall_projection.v1",
                "memories": {},
            }
            base_rp = _scrub_tombstoned_ids_from_tree(
                base_rp, all_tombstone_ids, vc_sealed_digests=vc_digests
            )
        base_rp["tombstones"] = copy.deepcopy(merged_tombstones)
        if isinstance(base_rp.get("clarity_counts"), dict):
            base_rp["clarity_counts"]["TOMBSTONED"] = len(merged_tombstones)
        base_rp["updated_at"] = now_iso
        slices_to_write["v02_recall_projection.json"] = base_rp

    restored_slices: list[str] = []
    for sname in SUITCASE_ALLOWED_SLICE_FILES:
        if sname in slices_to_write:
            _atomic_write_json_doc(phone_dir / sname, slices_to_write[sname])
            for mirror_rel in _SUITCASE_CANONICAL_RESTORE_MIRRORS.get(sname, ()):
                _atomic_write_json_doc(day_root.joinpath(*mirror_rel), slices_to_write[sname])
            restored_slices.append(sname)
        elif all_tombstone_ids or vc_digests:
            # Scrub any existing allowlisted phone slice and its canonical companion on disk
            _scrub_existing_json_file_if_present(
                day_root, (STATE_DIRNAME, sname), all_tombstone_ids, vc_sealed_digests=vc_digests
            )
            for comp_rel in _SUITCASE_COMPANION_FALLBACKS.get(sname, ()):
                _scrub_existing_json_file_if_present(
                    day_root, comp_rel, all_tombstone_ids, vc_sealed_digests=vc_digests
                )

    restored_world_slices: list[str] = []
    for wname in SUITCASE_ALLOWED_WORLD_FILES:
        if wname in world_slices_to_write:
            wdoc = world_slices_to_write[wname]
            _atomic_write_json_doc(world_dir / wname, wdoc)
            if wname == "v08_microzone_ecology.json":
                _atomic_write_json_doc(world_dir / "v08_microzone_state.json", wdoc)
            elif (
                wname == "v08_open_affordance.json"
                and str(wdoc.get("schema_version") or "") == "world.v08.open_affordance.v1"
            ):
                _atomic_write_json_doc(phone_dir / "v08_living_traces.json", wdoc)
            restored_world_slices.append(wname)
        elif all_tombstone_ids or vc_digests:
            for w_rel in _WORLD_SLICE_READ_CANDIDATES.get(wname, (("world", wname),)):
                _scrub_existing_json_file_if_present(
                    day_root, w_rel, all_tombstone_ids, vc_sealed_digests=vc_digests
                )

    restored_world_package_files: list[str] = []
    for pname in SUITCASE_ALLOWED_WORLD_PACKAGE_FILES:
        if pname in wp_files_to_write:
            _atomic_write_json_any(wp_dir / pname, wp_files_to_write[pname])
            restored_world_package_files.append(pname)
        elif all_tombstone_ids or vc_digests:
            _scrub_existing_json_file_if_present(
                day_root,
                ("world-package", pname),
                all_tombstone_ids,
                allow_list_root=True,
                vc_sealed_digests=vc_digests,
            )

    # Sync tombstones into memory/v02_recall_projection_state.json if present
    mem_rp_path = day_root / "memory" / "v02_recall_projection_state.json"
    if (merged_tombstones or vc_digests) and _is_safe_nonsymlink_rel(day_root, ("memory", "v02_recall_projection_state.json")):
        ex_m, ok_m, doc_m = _read_json_safe(mem_rp_path)
        if ex_m and ok_m and isinstance(doc_m, Mapping):
            scrubbed_m = _scrub_tombstoned_ids_from_tree(
                doc_m, all_tombstone_ids, vc_sealed_digests=vc_digests
            )
            if isinstance(scrubbed_m, dict):
                scrubbed_m["tombstones"] = copy.deepcopy(merged_tombstones)
                scrubbed_m["updated_at"] = now_iso
                _atomic_write_json_doc(mem_rp_path, scrubbed_m)

    restored_char_pack = False
    if isinstance(raw_char_pack, Mapping) and raw_char_pack:
        sanitized_pack = _sanitize_suitcase_tree(
            raw_char_pack,
            source_label="phone/character_pack_state.json",
            stripped_bindings=dummy_stripped,
            strip_pack_host_sections=True,
        )
        sanitized_pack = _scrub_tombstoned_ids_from_tree(
            sanitized_pack, all_tombstone_ids, vc_sealed_digests=vc_digests
        )
        if isinstance(sanitized_pack, dict):
            _atomic_write_json_doc(phone_dir / "character_pack_state.json", sanitized_pack)
            restored_slices.append("character_pack_state.json")
            restored_char_pack = True

    raw_target_model = payload.get("target_model_id")
    raw_target_carrier = payload.get("target_carrier_mode")
    target_model_id = raw_target_model.strip() if isinstance(raw_target_model, str) and raw_target_model.strip() else ""
    target_carrier_mode = raw_target_carrier.strip() if isinstance(raw_target_carrier, str) and raw_target_carrier.strip() else ""

    carrier_sec = state["carrier_migration"]
    if target_model_id or target_carrier_mode:
        prev_fp = copy.deepcopy(carrier_sec.get("carrier_fingerprint") or DEFAULT_CARRIER_FINGERPRINT)
        eff_model_id = target_model_id or str(prev_fp.get("model_id") or DEFAULT_CARRIER_FINGERPRINT["model_id"])
        eff_carrier_name = target_carrier_mode or str(prev_fp.get("carrier_name") or DEFAULT_CARRIER_FINGERPRINT["carrier_name"])
        carrier_low = target_carrier_mode.lower()
        if carrier_low in {"headless", "cli", "minimal", "text_only", "no_preview", "headless_host"}:
            eff_preview = False
        else:
            eff_preview = bool(prev_fp.get("preview_available", True))

        model_or_carrier_changed = (
            eff_model_id != str(prev_fp.get("model_id") or "")
            or eff_carrier_name != str(prev_fp.get("carrier_name") or "")
        )
        _apply_record_carrier_check(
            state,
            {
                "model_id": eff_model_id,
                "carrier_name": eff_carrier_name,
                "preview_available": eff_preview,
            },
            now_iso=now_iso,
        )
        skills = carrier_sec.setdefault("skills", _default_skills_catalog())
        loss_report = [
            entry
            for entry in (carrier_sec.get("loss_report") or [])
            if isinstance(entry, Mapping) and entry.get("loss_code") != "MODEL_SENSITIVE_PENDING_REVERIFY"
        ]
        if model_or_carrier_changed:
            for sid, sk in skills.items():
                if not isinstance(sk, dict):
                    continue
                if bool(sk.get("model_sensitive", False)):
                    sk["status"] = SKILL_STATUS_PENDING_REVERIFY
                    loss_report.append({
                        "loss_code": "MODEL_SENSITIVE_PENDING_REVERIFY",
                        "skill_id": str(sid),
                        "status": SKILL_STATUS_PENDING_REVERIFY,
                        "recorded_at": now_iso,
                        "summary_cn": (
                            f"便携旅行箱迁入目标环境（{eff_model_id} / {eff_carrier_name}），"
                            f"个人技法「{sk.get('title_cn', sid)}」标记为待重新试跑核对"
                        ),
                    })
        carrier_sec["loss_report"] = loss_report
        carrier_sec["last_migration_verdict"] = _compute_migration_verdict(carrier_sec)

    carrier_sec["loss_report"] = [
        entry
        for entry in (carrier_sec.get("loss_report") or [])
        if isinstance(entry, Mapping) and entry.get("loss_code") != "LEGACY_DELIVERY_MEANING_DOWNGRADED"
    ]
    raw_legacy_delivery = raw_bundle.get("legacy_delivery_records")
    migrated_delivery_records: list[dict[str, Any]] = []
    if raw_legacy_delivery is not None:
        if isinstance(raw_legacy_delivery, (str, bytes)) or not isinstance(raw_legacy_delivery, Sequence):
            raise V02ValidationError("INVALID_FIELDS", "bundle.legacy_delivery_records must be a JSON array")
        for rec in raw_legacy_delivery:
            if not isinstance(rec, Mapping):
                raise V02ValidationError("INVALID_FIELDS", "Each legacy_delivery_records item must be an object")
            msg_id = str(rec.get("msg_id") or "").strip()
            src_ver = str(rec.get("meaning_version") or "unknown").strip() or "unknown"
            raw_status = str(rec.get("status") or "UNKNOWN").strip().upper() or "UNKNOWN"
            verified_read = (src_ver == "delivery.v2" and bool(rec.get("verified_read_receipt")))
            downgraded_status = raw_status if verified_read else "UNKNOWN"
            migrated_delivery_records.append({
                "msg_id": msg_id,
                "source_meaning_version": src_ver,
                "original_status": raw_status,
                "downgraded_status": downgraded_status,
                "treated_as_read": bool(verified_read),
                "meaning_downgraded": not verified_read,
                "migrated_at": now_iso,
            })
        if any(item["meaning_downgraded"] for item in migrated_delivery_records):
            carrier_sec["loss_report"].append({
                "loss_code": "LEGACY_DELIVERY_MEANING_DOWNGRADED",
                "count": sum(1 for item in migrated_delivery_records if item["meaning_downgraded"]),
                "downgraded_to": "UNKNOWN",
                "recorded_at": now_iso,
                "summary_cn": "旧语义版本投递回执已降级为 UNKNOWN，绝不按已读处理",
            })

    skills_now = carrier_sec.get("skills") or {}
    pending_reverify_count = sum(
        1
        for sk in skills_now.values()
        if isinstance(sk, Mapping) and sk.get("status") in (SKILL_STATUS_UNVALIDATED, SKILL_STATUS_PENDING_REVERIFY)
    )
    final_loss_report = copy.deepcopy(carrier_sec.get("loss_report") or [])

    suit = state["suitcase_state"]
    suit["migrated_delivery_records"] = copy.deepcopy(migrated_delivery_records)
    raw_imp_profile = str(raw_bundle.get("profile_mode") or "").strip()
    imp_mode = str(raw_bundle.get("effective_profile_mode") or raw_imp_profile or SUITCASE_SELECTIVE_PRACTICE).strip()
    if imp_mode in SUITCASE_PROFILE_MODES:
        suit["profile_mode"] = imp_mode
    else:
        imp_mode = str(suit.get("profile_mode") or SUITCASE_SELECTIVE_PRACTICE)
    declared_profile = raw_imp_profile if raw_imp_profile in SUITCASE_PROFILE_MODES else imp_mode

    restored_phone_slice_only = [s for s in restored_slices if s != "character_pack_state.json"]
    import_loss_manifest = _build_migration_loss_manifest(
        profile_mode=declared_profile,
        effective_mode=imp_mode,
        has_phone_slices=bool(restored_phone_slice_only),
        phone_slice_count=len(restored_phone_slice_only),
        has_character_pack=restored_char_pack,
        world_slice_names=restored_world_slices,
        world_package_files=restored_world_package_files,
        tombstone_count=len(merged_tombstones),
        carrier_sec=carrier_sec,
        world_package_oversized=False,
    )

    return {
        "restored_slices": restored_slices,
        "restored_world_slices": restored_world_slices,
        "restored_world_package_files": restored_world_package_files,
        "replayed_tombstones": replayed_tombstone_ids,
        "migrated_delivery_records": migrated_delivery_records,
        "pending_reverify_count": int(pending_reverify_count),
        "loss_report": final_loss_report,
        "migration_loss_manifest": import_loss_manifest,
    }


# ---------------------------------------------------------------------------
# F-148: Two-Step Dignified Permanent Retirement (Zero Destructive Wipe)
# ---------------------------------------------------------------------------


def _count_collection_entries(doc: Mapping[str, Any], keys: Sequence[str]) -> int:
    total = 0
    for key in keys:
        val = doc.get(key)
        if isinstance(val, Mapping):
            total += len(val)
        elif isinstance(val, list):
            total += len(val)
    return total


def _build_retirement_archive_manifest_readonly(
    day_root: Path,
    state: Mapping[str, Any],
    *,
    now_iso: str,
) -> dict[str, Any]:
    """Compute a 100% read-only summary of memories/keepsakes/skills before sealing (zero deletes)."""
    memories_count = 0
    for rel in (
        ("phone", "v02_recall_projection.json"),
        ("memory", "v02_recall_projection_state.json"),
    ):
        ex, ok, doc = _read_json_safe(day_root.joinpath(*rel))
        if ex and ok and doc:
            memories_count = max(
                memories_count,
                _count_collection_entries(doc, ("memories", "items", "direct_recall_items")),
            )

    keepsakes_count = 0
    ex_ks, ok_ks, doc_ks = _read_json_safe(day_root / "phone" / "window_keepsakes.json")
    if ex_ks and ok_ks and doc_ks:
        keepsakes_count += _count_collection_entries(doc_ks, ("pins", "keepsakes", "slow_letter_drafts"))
    ex_gl, ok_gl, doc_gl = _read_json_safe(day_root / "phone" / "longterm_gifts_lantern.json")
    if ex_gl and ok_gl and doc_gl:
        keepsakes_count += _count_collection_entries(doc_gl, ("shared_gifts", "later_reflections"))

    carrier_skills = (state.get("carrier_migration") or {}).get("skills")
    skills_count = len(carrier_skills) if isinstance(carrier_skills, Mapping) else 0
    ex_asp, ok_asp, doc_asp = _read_json_safe(
        day_root / "state" / "v02_autobiography_skill_provenance.json"
    )
    if ex_asp and ok_asp and doc_asp:
        sk_sec = doc_asp.get("skills")
        if isinstance(sk_sec, Mapping) and isinstance(sk_sec.get("releases"), Mapping):
            skills_count += len(sk_sec["releases"])

    audio_notes_count = 0
    ex_cm, ok_cm, doc_cm = _read_json_safe(day_root / "phone" / "v02_crossmodal.json")
    if ex_cm and ok_cm and doc_cm and isinstance(doc_cm.get("audio_notes"), list):
        audio_notes_count = len(doc_cm["audio_notes"])

    phone_slice_files_count = 0
    phone_dir = day_root / "phone"
    if not phone_dir.is_symlink() and phone_dir.is_dir():
        try:
            phone_slice_files_count = sum(
                1 for p in phone_dir.iterdir() if p.suffix == ".json" and p.is_file() and not p.is_symlink()
            )
        except OSError:
            phone_slice_files_count = 0

    return {
        "memories_count": int(memories_count),
        "keepsakes_count": int(keepsakes_count),
        "skills_count": int(skills_count),
        "audio_notes_count": int(audio_notes_count),
        "phone_slice_files_count": int(phone_slice_files_count),
        "preserved_read_only": True,
        "destructive_wipe_performed": False,
        "computed_at": now_iso,
    }


def _project_retirement_status(state: Mapping[str, Any]) -> dict[str, Any]:
    raw_ret = state.get("retirement")
    ret = dict(raw_ret) if isinstance(raw_ret, Mapping) else _default_retirement_state()
    status = str(ret.get("status") or RETIREMENT_STATUS_ACTIVE)
    if status not in RETIREMENT_STATUSES:
        status = RETIREMENT_STATUS_ACTIVE
    return {
        "status": status,
        "prepared_at": ret.get("prepared_at"),
        "sealed_at": ret.get("sealed_at"),
        "reason_cn": str(ret.get("reason_cn") or ""),
        "farewell_note_cn": str(ret.get("farewell_note_cn") or ""),
        "confirmation_phrase": ret.get("confirmation_phrase"),
        "archive_manifest": copy.deepcopy(ret.get("archive_manifest")),
        "destructive_wipe_performed": False,
    }


def _apply_prepare_dignified_retirement(
    day_root: Path,
    state: dict[str, Any],
    payload: Mapping[str, Any],
    *,
    actor_side: str,
    now_iso: str,
) -> dict[str, Any]:
    ret = state.setdefault("retirement", _default_retirement_state())
    if str(ret.get("status") or "") == RETIREMENT_STATUS_SEALED:
        raise V02ConflictError("ALREADY_SEALED", "Instance is already retired and archive-sealed")

    raw_reason = payload.get("reason_cn")
    if raw_reason is not None and not isinstance(raw_reason, str):
        raise V02ValidationError("INVALID_FIELDS", "reason_cn must be a string")
    reason_cn = str(raw_reason or "").strip()[:200]

    raw_farewell = payload.get("farewell_note_cn")
    if raw_farewell is not None and not isinstance(raw_farewell, str):
        raise V02ValidationError("INVALID_FIELDS", "farewell_note_cn must be a string")
    farewell_note_cn = str(raw_farewell or "").strip()[:400]

    manifest = _build_retirement_archive_manifest_readonly(day_root, state, now_iso=now_iso)
    ret["status"] = RETIREMENT_STATUS_PENDING
    ret["prepared_at"] = now_iso
    ret["sealed_at"] = None
    ret["reason_cn"] = reason_cn
    ret["farewell_note_cn"] = farewell_note_cn
    ret["confirmation_phrase"] = RETIREMENT_CONFIRMATION_PHRASE_CN
    ret["archive_manifest"] = manifest
    ret["prepared_by"] = actor_side
    ret["destructive_wipe_performed"] = False
    return _project_retirement_status(state)


def _apply_cancel_dignified_retirement(state: dict[str, Any]) -> dict[str, Any]:
    ret = state.setdefault("retirement", _default_retirement_state())
    if str(ret.get("status") or "") == RETIREMENT_STATUS_SEALED:
        raise V02ConflictError("ALREADY_SEALED", "Cannot cancel an already sealed retirement archive")
    state["retirement"] = _default_retirement_state()
    return _project_retirement_status(state)


def _apply_confirm_dignified_retirement(
    day_root: Path,
    state: dict[str, Any],
    payload: Mapping[str, Any],
    *,
    actor_side: str,
    now_dt: datetime,
    now_iso: str,
) -> dict[str, Any]:
    ret = state.setdefault("retirement", _default_retirement_state())
    cur_status = str(ret.get("status") or RETIREMENT_STATUS_ACTIVE)
    if cur_status == RETIREMENT_STATUS_SEALED:
        raise V02ConflictError("ALREADY_SEALED", "Instance is already retired and archive-sealed")
    if cur_status != RETIREMENT_STATUS_PENDING:
        raise V02ConflictError(
            "RETIREMENT_NOT_PREPARED",
            "Must call prepare_dignified_retirement before confirming dignified retirement",
        )

    raw_phrase = payload.get("confirmation_phrase")
    expected_phrase = str(ret.get("confirmation_phrase") or RETIREMENT_CONFIRMATION_PHRASE_CN)
    if not isinstance(raw_phrase, str) or raw_phrase.strip() != expected_phrase:
        raise V02ValidationError(
            "CONFIRMATION_PHRASE_MISMATCH",
            "Confirmation phrase does not match the prepared dignified retirement phrase",
        )

    raw_farewell = payload.get("farewell_note_cn")
    if raw_farewell is not None:
        if not isinstance(raw_farewell, str):
            raise V02ValidationError("INVALID_FIELDS", "farewell_note_cn must be a string")
        cleaned_farewell = raw_farewell.strip()[:400]
        if cleaned_farewell:
            ret["farewell_note_cn"] = cleaned_farewell

    manifest = _build_retirement_archive_manifest_readonly(day_root, state, now_iso=now_iso)
    ret["status"] = RETIREMENT_STATUS_SEALED
    ret["sealed_at"] = now_iso
    ret["sealed_by"] = actor_side
    ret["archive_manifest"] = manifest
    ret["destructive_wipe_performed"] = False

    _apply_set_budget_mode(
        state,
        {
            "mode": "DORMANT_NO_COGNITION",
            "reason_cn": "已温和只读封存归档，暂停自主认知，全部纪念与记忆完好保留",
        },
        now_dt=now_dt,
        now_iso=now_iso,
        _allow_sealed_transition=True,
    )
    return _project_retirement_status(state)


# ---------------------------------------------------------------------------
# F-113: Opt-In Proactive Outbound Queue & Anti-Harassment Gate
# ---------------------------------------------------------------------------


def _parse_hm_minutes(raw: Any, field_name: str) -> tuple[str, int]:
    if not isinstance(raw, str):
        raise V02ValidationError("INVALID_FIELDS", f"{field_name} must be HH:MM string")
    trimmed = raw.strip()
    m = _HM_TIME_RE.match(trimmed)
    if not m:
        raise V02ValidationError("INVALID_FIELDS", f"{field_name} must be valid 24h HH:MM, got {raw!r}")
    hours = int(m.group(1))
    mins = int(m.group(2))
    return f"{hours:02d}:{mins:02d}", hours * 60 + mins


def _is_inside_quiet_hours_east8(
    now_dt: datetime,
    quiet_start_hm: str,
    quiet_end_hm: str,
) -> bool:
    _, start_m = _parse_hm_minutes(quiet_start_hm, "quiet_start_hm")
    _, end_m = _parse_hm_minutes(quiet_end_hm, "quiet_end_hm")
    if start_m == end_m:
        return False
    local_dt = now_dt.astimezone(DEFAULT_TZ)
    cur_m = local_dt.hour * 60 + local_dt.minute
    if start_m < end_m:
        return start_m <= cur_m < end_m
    return cur_m >= start_m or cur_m < end_m


def _project_proactive_outbound(state: Mapping[str, Any], *, now_dt: datetime) -> dict[str, Any]:
    raw_po = state.get("proactive_outbound")
    po = dict(raw_po) if isinstance(raw_po, Mapping) else _default_proactive_outbound_state()
    local_today = now_dt.astimezone(DEFAULT_TZ).date().isoformat()
    sent_today = (
        int(po.get("sent_today_count") or 0)
        if po.get("sent_today_date") == local_today
        else 0
    )
    return {
        "enabled": bool(po.get("enabled", False)),
        "quiet_start_hm": str(po.get("quiet_start_hm") or PROACTIVE_DEFAULT_QUIET_START_HM),
        "quiet_end_hm": str(po.get("quiet_end_hm") or PROACTIVE_DEFAULT_QUIET_END_HM),
        "daily_cap": int(po.get("daily_cap") or PROACTIVE_DEFAULT_DAILY_CAP),
        "min_cooldown_minutes": int(po.get("min_cooldown_minutes") or PROACTIVE_DEFAULT_COOLDOWN_MINUTES),
        "sent_today_count": sent_today,
        "last_sent_at": po.get("last_sent_at"),
        "pending_queue": copy.deepcopy(list(po.get("pending_queue") or [])[-PROACTIVE_MAX_PENDING_QUEUE:]),
        "recent_log": copy.deepcopy(list(po.get("recent_log") or [])[-PROACTIVE_MAX_RECENT_LOG:]),
    }


def _load_v03_outbound_bridge() -> Any:
    try:
        from components.world.scripts import v03_economy_illness_bridge as _v03_eco
        return _v03_eco
    except ImportError:  # pragma: no cover
        _world_scripts = str(Path(__file__).resolve().parents[2] / "world" / "scripts")
        if _world_scripts not in sys.path:
            sys.path.insert(0, _world_scripts)
        try:
            import v03_economy_illness_bridge as _v03_eco  # type: ignore[no-redef]
            return _v03_eco
        except ImportError:
            return None


def _map_f113_channel_to_v03(channel_hint: str, v03_eco: Any) -> str:
    clean = str(channel_hint or "").strip()
    if clean in getattr(v03_eco, "PROACTIVE_CHANNELS", ()):
        return clean
    if clean.lower() in ("cross_channel", "ping", "wechat", "telegram"):
        return "cross_channel_ping"
    return "desktop_note"


def _apply_configure_proactive_outbound(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    *,
    actor_side: str,
    now_dt: datetime,
    day_root: Optional[Path] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    if actor_side != "partner":
        raise V02PermissionError(
            "FORBIDDEN_ACTOR_SIDE",
            "Only partner can configure proactive outbound opt-in settings",
        )
    po = state.setdefault("proactive_outbound", _default_proactive_outbound_state())

    if "enabled" in payload:
        if not isinstance(payload["enabled"], bool):
            raise V02ValidationError("INVALID_FIELDS", "enabled must be a boolean")
        po["enabled"] = payload["enabled"]
    if "quiet_start_hm" in payload and payload["quiet_start_hm"] is not None:
        norm_s, _ = _parse_hm_minutes(payload["quiet_start_hm"], "quiet_start_hm")
        po["quiet_start_hm"] = norm_s
    if "quiet_end_hm" in payload and payload["quiet_end_hm"] is not None:
        norm_e, _ = _parse_hm_minutes(payload["quiet_end_hm"], "quiet_end_hm")
        po["quiet_end_hm"] = norm_e
    if "daily_cap" in payload and payload["daily_cap"] is not None:
        po["daily_cap"] = _strict_non_bool_int(
            payload["daily_cap"],
            field_name="daily_cap",
            minimum=PROACTIVE_MIN_DAILY_CAP,
            maximum=PROACTIVE_MAX_DAILY_CAP,
        )
    if "min_cooldown_minutes" in payload and payload["min_cooldown_minutes"] is not None:
        po["min_cooldown_minutes"] = _strict_non_bool_int(
            payload["min_cooldown_minutes"],
            field_name="min_cooldown_minutes",
            minimum=PROACTIVE_MIN_COOLDOWN_MINUTES,
            maximum=PROACTIVE_MAX_COOLDOWN_MINUTES,
        )

    # When v03_ecology.json is not yet initialized and partner explicitly sets
    # min_cooldown_minutes in F-113, initialize the unified outbound policy
    # with partner's explicit F-113 preferences while preserving existing v03
    # 48h policy untouched whenever v03_ecology.json already exists.
    if (
        day_root is not None
        and "min_cooldown_minutes" in payload
        and payload.get("min_cooldown_minutes") is not None
    ):
        v03_eco = _load_v03_outbound_bridge()
        if v03_eco is not None:
            eco_path = v03_eco.ecology_state_path(day_root)
            if not eco_path.exists():
                try:
                    v03_eco.apply_v03_ecology_command(
                        day_root,
                        {
                            "action": "configure_outbound_quota",
                            "daily_proactive_quota": int(po["daily_cap"]),
                            "quiet_start_hhmm": str(po["quiet_start_hm"]),
                            "quiet_end_hhmm": str(po["quiet_end_hm"]),
                            "min_outbound_interval_hours": int(po["min_cooldown_minutes"]) // 60,
                        },
                        actor_side="partner",
                        now_iso=now_iso or now_dt.isoformat(timespec="seconds"),
                    )
                except Exception:
                    pass

    return _project_proactive_outbound(state, now_dt=now_dt)


def _apply_enqueue_proactive_message(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    *,
    actor_side: str,
    now_dt: datetime,
    now_iso: str,
    day_root: Optional[Path] = None,
) -> dict[str, Any]:
    if actor_side != "subject":
        raise V02PermissionError(
            "FORBIDDEN_ACTOR_SIDE",
            "Only subject can enqueue proactive outbound messages",
        )

    raw_msg = payload.get("message_cn")
    if not isinstance(raw_msg, str) or not raw_msg.strip() or len(raw_msg.strip()) > 280:
        raise V02ValidationError("INVALID_FIELDS", "message_cn must be a non-empty string (<=280 chars)")
    message_cn = raw_msg.strip()

    raw_reason = payload.get("reason_cn")
    if raw_reason is not None and not isinstance(raw_reason, str):
        raise V02ValidationError("INVALID_FIELDS", "reason_cn must be a string")
    reason_cn = str(raw_reason or "").strip()[:160]

    raw_channel = payload.get("channel_hint")
    if raw_channel is not None and not isinstance(raw_channel, str):
        raise V02ValidationError("INVALID_FIELDS", "channel_hint must be a string")
    channel_hint = str(raw_channel or "").strip()[:40] or "pocket_phone"

    # Strict anti-harassment gate check order:
    # (a) not RETIRED_ARCHIVE_SEALED (409 INSTANCE_RETIRED)
    ret_status = str((state.get("retirement") or {}).get("status") or RETIREMENT_STATUS_ACTIVE)
    if ret_status == RETIREMENT_STATUS_SEALED:
        raise V02ConflictError("INSTANCE_RETIRED", "Instance is retired and archive-sealed")

    po = state.setdefault("proactive_outbound", _default_proactive_outbound_state())

    # (b) enabled is True (403 PROACTIVE_DISABLED)
    if not bool(po.get("enabled", False)):
        raise V02PermissionError("PROACTIVE_DISABLED", "Proactive outbound is disabled by partner")

    # (c) runtime mode not DORMANT_NO_COGNITION (409 DORMANT_BLOCKED)
    cur_mode = str((state.get("mode_config") or {}).get("mode") or MODE_NORMAL)
    if cur_mode == MODE_DORMANT:
        raise V02ConflictError("DORMANT_BLOCKED", "Runtime mode is DORMANT_NO_COGNITION")

    # (d) current East-8 HH:MM not inside quiet hours (409 QUIET_HOURS_ACTIVE)
    q_start = str(po.get("quiet_start_hm") or PROACTIVE_DEFAULT_QUIET_START_HM)
    q_end = str(po.get("quiet_end_hm") or PROACTIVE_DEFAULT_QUIET_END_HM)
    if _is_inside_quiet_hours_east8(now_dt, q_start, q_end):
        raise V02ConflictError(
            "QUIET_HOURS_ACTIVE",
            f"Current East-8 time is inside quiet hours ({q_start}-{q_end})",
        )

    # (e) sent_today_count < daily_cap (429 DAILY_CAP_REACHED)
    local_today = now_dt.astimezone(DEFAULT_TZ).date().isoformat()
    sent_today = (
        int(po.get("sent_today_count") or 0)
        if po.get("sent_today_date") == local_today
        else 0
    )
    daily_cap = int(po.get("daily_cap") or PROACTIVE_DEFAULT_DAILY_CAP)
    if sent_today >= daily_cap:
        raise V02RuntimeMigrationError(
            429,
            "DAILY_CAP_REACHED",
            f"Daily proactive outbound cap reached ({sent_today}/{daily_cap})",
        )

    # (f) elapsed >= min_cooldown_minutes (429 COOLDOWN_ACTIVE)
    last_sent_dt = _parse_iso(po.get("last_sent_at"))
    min_cd = int(po.get("min_cooldown_minutes") or PROACTIVE_DEFAULT_COOLDOWN_MINUTES)
    if last_sent_dt is not None:
        elapsed_sec = (now_dt - last_sent_dt).total_seconds()
        if elapsed_sec < float(min_cd * 60):
            raise V02RuntimeMigrationError(
                429,
                "COOLDOWN_ACTIVE",
                f"Proactive outbound cooldown active (min {min_cd} minutes)",
            )

    seq = int(po.get("next_msg_seq") or 1)
    msg_id = f"pmsg_{seq:04d}"

    # Stage 3 Bridge: enforce unified 7-step anti-harassment gate and enqueue
    # into v03_economy_illness_bridge with delivery_mode="honest_outbox".
    v03_outbound_id: Optional[str] = None
    v03_outbox_status: Optional[str] = None
    v03_channel: Optional[str] = None
    if day_root is not None:
        v03_eco = _load_v03_outbound_bridge()
        if v03_eco is not None:
            v03_channel = _map_f113_channel_to_v03(channel_hint, v03_eco)
            raw_op = payload.get("operation_key")
            if isinstance(raw_op, str) and raw_op.strip():
                safe_op = re.sub(r"[^A-Za-z0-9._:\-]+", "_", raw_op.strip()).strip("_")
                bridge_op_key = f"f113_{safe_op}"[:120]
            else:
                safe_ts = re.sub(r"[^A-Za-z0-9._:\-]+", "_", now_iso)
                bridge_op_key = f"f113_{msg_id}_{safe_ts}"[:120]
            try:
                v03_res = v03_eco.attempt_proactive_outbound(
                    day_root,
                    text=message_cn,
                    channel=v03_channel,
                    reason_kind="genuine_life_share",
                    delivery_mode="honest_outbox",
                    operation_key=bridge_op_key,
                    now_iso=now_iso,
                )
            except v03_eco.V03EcologyError as exc:
                raise V02ConflictError(
                    exc.reason_code,
                    f"Blocked by unified 7-step proactive gate: {exc.reason_code}",
                    details=exc.details,
                ) from exc
            rcpt = v03_res.get("receipt") if isinstance(v03_res.get("receipt"), Mapping) else {}
            v03_outbound_id = str(rcpt.get("outbound_id") or "") or None
            v03_outbox_status = str(rcpt.get("outbound_status") or "APPROVED_FOR_DELIVERY")

    po["next_msg_seq"] = seq + 1
    item: dict[str, Any] = {
        "message_id": msg_id,
        "message_cn": message_cn,
        "reason_cn": reason_cn,
        "channel_hint": channel_hint,
        "status": "QUEUED",
        "delivery_mode": "honest_outbox",
        "created_at": now_iso,
    }
    if v03_outbound_id is not None:
        item["v03_outbound_id"] = v03_outbound_id
        item["v03_channel"] = v03_channel
        item["outbox_status"] = v03_outbox_status or "APPROVED_FOR_DELIVERY"
    queue = list(po.get("pending_queue") or [])
    queue.append(item)
    po["pending_queue"] = queue[-PROACTIVE_MAX_PENDING_QUEUE:]
    po["sent_today_date"] = local_today
    po["sent_today_count"] = sent_today + 1
    po["last_sent_at"] = now_iso

    rlog = list(po.get("recent_log") or [])
    rlog.append(copy.deepcopy(item))
    po["recent_log"] = rlog[-PROACTIVE_MAX_RECENT_LOG:]
    return copy.deepcopy(item)


def _apply_acknowledge_or_cancel_proactive_message(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    *,
    actor_side: str,
    now_iso: str,
    day_root: Optional[Path] = None,
) -> dict[str, Any]:
    if actor_side != "partner":
        raise V02PermissionError(
            "FORBIDDEN_ACTOR_SIDE",
            "Only partner can acknowledge or cancel proactive outbound messages",
        )
    raw_mid = payload.get("message_id")
    if not isinstance(raw_mid, str) or not raw_mid.strip():
        raise V02ValidationError("INVALID_FIELDS", "message_id must be a non-empty string")
    message_id = raw_mid.strip()

    raw_dec = payload.get("decision")
    if not isinstance(raw_dec, str) or raw_dec.strip().lower() not in ("acknowledge", "cancel"):
        raise V02ValidationError("INVALID_FIELDS", "decision must be 'acknowledge' or 'cancel'")
    decision = raw_dec.strip().lower()

    po = state.setdefault("proactive_outbound", _default_proactive_outbound_state())
    queue = list(po.get("pending_queue") or [])
    target: Optional[dict[str, Any]] = None
    for entry in queue:
        if isinstance(entry, dict) and entry.get("message_id") == message_id:
            target = entry
            break
    if target is None:
        raise V02RuntimeMigrationError(
            404,
            "PROACTIVE_MESSAGE_NOT_FOUND",
            f"Proactive message not found: {message_id!r}",
        )
    if str(target.get("status") or "") != "QUEUED":
        raise V02ConflictError(
            "MESSAGE_ALREADY_DECIDED",
            f"Proactive message {message_id!r} is already {target.get('status')!r}",
        )

    new_status = "ACKNOWLEDGED" if decision == "acknowledge" else "CANCELLED"
    target["status"] = new_status
    target["decided_at"] = now_iso
    target["decided_by"] = "partner"
    po["pending_queue"] = queue[-PROACTIVE_MAX_PENDING_QUEUE:]

    if day_root is not None and target.get("v03_outbound_id"):
        v03_eco = _load_v03_outbound_bridge()
        if v03_eco is not None:
            out_id = str(target["v03_outbound_id"])
            try:
                if decision == "acknowledge":
                    v03_eco.ack_proactive_outbound_delivery(
                        day_root,
                        outbound_id=out_id,
                        action="ack_read",
                        operation_key=f"f113_ack_{message_id}"[:120],
                        now_iso=now_iso,
                    )
                    target["outbox_status"] = "READ"
                else:
                    v03_eco.apply_v03_ecology_command(
                        day_root,
                        {
                            "action": "withdraw_pending_outbound",
                            "outbound_id": out_id,
                            "operation_key": f"f113_cxl_{message_id}"[:120],
                        },
                        actor_side="subject",
                        now_iso=now_iso,
                    )
                    target["outbox_status"] = "WITHDRAWN"
            except Exception:
                pass

    rlog = list(po.get("recent_log") or [])
    updated_log = False
    for log_item in rlog:
        if isinstance(log_item, dict) and log_item.get("message_id") == message_id:
            log_item["status"] = new_status
            log_item["decided_at"] = now_iso
            log_item["decided_by"] = "partner"
            if "outbox_status" in target:
                log_item["outbox_status"] = target["outbox_status"]
            updated_log = True
    if not updated_log:
        rlog.append(copy.deepcopy(target))
    po["recent_log"] = rlog[-PROACTIVE_MAX_RECENT_LOG:]
    return copy.deepcopy(target)


def build_runtime_migration_context_summary(
    day_root: Path | str,
    *,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """100% zero-write helper for chat background & daily loop candidate bridge."""
    proj = project_v02_runtime_migration(
        day_root,
        has_subject_capability=False,
        now_iso=now_iso,
    )
    rm = proj["runtime_mode"]
    card = proj["today_capability_card"]
    health = str(rm["health_state"])
    unvalidated_ids = [str(x["skill_id"]) for x in card["needs_recheck"]]
    paused_ids = [str(x["skill_id"]) for x in card["paused_by_budget_or_carrier"]]

    cognition_allowed = health not in (HEALTH_DORMANT_NO_COGNITION, HEALTH_STALLED_OR_DEGRADED)
    roaming_paused = health in (
        HEALTH_LOW_BUDGET_CONSERVATION,
        HEALTH_DORMANT_NO_COGNITION,
        HEALTH_STALLED_OR_DEGRADED,
    )

    hints: list[str] = []
    if health == HEALTH_LOW_BUDGET_CONSERVATION:
        hints.append(
            f"当前处于省电低预算档（{rm['configured_budget_scale_pct']}%）：只做在途作品局部小修改并守住已有约定，绝不把省算力说成身体累了或心情不好。"
        )
    elif health == HEALTH_DORMANT_NO_COGNITION:
        hints.append("当前处于休眠保存状态：暂停日常认知活动，醒来后不补写休眠期间的假日记。")
    elif health == HEALTH_STALLED_OR_DEGRADED:
        hints.append("当前后台通道存在积压或技术异常：如实说明故障状态，不伪装成安静发呆。")
    if int(rm.get("dormant_gap_seconds") or 0) > 0 and rm.get("last_dormant_recovery") is not None:
        hints.append(
            f"刚跨过 {int(rm['dormant_gap_seconds'])} 秒休眠空白恢复运行：严禁补造休眠期间的假经历或假日记。"
        )
    if unvalidated_ids or paused_ids:
        hints.append(card["headline_cn"])

    return {
        "schema": VIEW_SCHEMA,
        "state_exists": proj["state_exists"],
        "mode": rm["mode"],
        "budget_scale_pct": rm["budget_scale_pct"],
        "health_state": health,
        "cognition_allowed": cognition_allowed,
        "roaming_and_large_projects_paused": roaming_paused,
        "pretend_body_tired_or_bad_mood": False,
        "no_fake_backfill": True,
        "dormant_gap_seconds": int(rm["dormant_gap_seconds"]),
        "last_migration_verdict": card["last_migration_verdict"],
        "unvalidated_skill_ids": unvalidated_ids,
        "paused_capability_ids": paused_ids,
        "retirement_status": proj["retirement_status"]["status"],
        "retired_sealed": proj["retirement_status"]["status"] == RETIREMENT_STATUS_SEALED,
        "proactive_outbound_enabled": bool(proj["proactive_outbound"]["enabled"]),
        "headline_cn": card["headline_cn"],
        "context_hint_cn": " ".join(hints).strip(),
        "llm_calls": 0,
    }


__all__ = [
    "BUDGET_MODE_ALIASES",
    "CHATLESS_LIFE_REVIEW_SCHEMA",
    "DEFAULT_CARRIER_FINGERPRINT",
    "DEFAULT_COALESCE_WINDOW_SECONDS",
    "DEFAULT_DAILY_WAKE_CAP",
    "DEFAULT_DEVICE_LEASE_SECONDS",
    "DEFAULT_HOURLY_WAKE_CAP",
    "DEFAULT_WAKE_TICKET_EST_TOKENS",
    "DECAY_MONITOR_CATEGORIES_22",
    "DECAY_MONITOR_GROUPS",
    "DECAY_MONITOR_SCHEMA",
    "DECAY_MONITOR_STATUSES",
    "DECAY_STATUS_ABSENT_BASELINE",
    "DECAY_STATUS_ACTIVE",
    "DECAY_STATUS_DORMANT",
    "DECAY_STATUS_NEEDS_REANCHOR",
    "DEVICE_PRESENCE_SCHEMA",
    "ENDPOINT_PATH",
    "FORBIDDEN_FOREIGN_AND_IDENTITY_KEYS",
    "FORBIDDEN_SUITCASE_SECRET_KEYS",
    "HEALTH_ACTIVE_HEALTHY",
    "HEALTH_DORMANT_NO_COGNITION",
    "HEALTH_LOW_BUDGET_CONSERVATION",
    "HEALTH_QUIET_HEALTHY",
    "HEALTH_STALLED_OR_DEGRADED",
    "HEALTH_STATES",
    "MAX_DEVICE_HANDOFF_HISTORY",
    "MAX_DEVICE_LEASE_SECONDS",
    "MIGRATION_LOSS_MANIFEST_SCHEMA",
    "MIGRATION_VERDICTS",
    "MODE_DORMANT",
    "MODE_LOW_BUDGET",
    "MODE_NORMAL",
    "PROACTIVE_DEFAULT_COOLDOWN_MINUTES",
    "PROACTIVE_DEFAULT_DAILY_CAP",
    "PROACTIVE_DEFAULT_QUIET_END_HM",
    "PROACTIVE_DEFAULT_QUIET_START_HM",
    "PROACTIVE_MAX_COOLDOWN_MINUTES",
    "PROACTIVE_MAX_DAILY_CAP",
    "PROACTIVE_MAX_PENDING_QUEUE",
    "PROACTIVE_MIN_COOLDOWN_MINUTES",
    "PROACTIVE_MIN_DAILY_CAP",
    "REDACTED_PRIVATE_DRAFT_LABEL",
    "RETIREMENT_CONFIRMATION_PHRASE_CN",
    "RETIREMENT_STATUSES",
    "RETIREMENT_STATUS_ACTIVE",
    "RETIREMENT_STATUS_PENDING",
    "RETIREMENT_STATUS_SEALED",
    "RUNTIME_GOVERNANCE_FILENAME",
    "RUNTIME_GOVERNANCE_SCHEMA",
    "VC_AUTHORITY_SCHEMA",
    "VC_AUTHORITY_WATERMARK_REL_PATH",
    "WAKE_TICKET_SCHEMA",
    "WAKE_TICKET_STATUSES",
    "WAKE_TICKET_STATUS_EXPIRED",
    "WAKE_TICKET_STATUS_FIRED",
    "WAKE_TICKET_STATUS_FOLDED_INTO",
    "WAKE_TICKET_STATUS_OPEN",
    "SKILL_STATUS_PENDING_REVERIFY",
    "SKILL_STATUS_SUSPENDED_LOSS",
    "SKILL_STATUS_UNVALIDATED",
    "SKILL_STATUS_VALIDATED",
    "STATE_DIRNAME",
    "STATE_FILENAME",
    "SUITCASE_ALLOWED_SLICE_FILES",
    "SUITCASE_ALLOWED_WORLD_FILES",
    "SUITCASE_ALLOWED_WORLD_PACKAGE_FILES",
    "SUITCASE_BUNDLE_SCHEMA",
    "SUITCASE_BUNDLE_VERSION",
    "SUITCASE_FULL_PRACTICE",
    "SUITCASE_PRACTICE_EXTRA_SLICES",
    "SUITCASE_PROFILE_MODES",
    "SUITCASE_RECORDS_AND_OBJECTS",
    "SUITCASE_RECORDS_AND_OBJECTS_EXTRA_SLICES",
    "SUITCASE_RECORDS_ONLY",
    "SUITCASE_RECORDS_ONLY_SLICES",
    "SUITCASE_SELECTIVE_PRACTICE",
    "SUITCASE_SLICE_META",
    "SUITCASE_WORLD_PACKAGE_MAX_TOTAL_BYTES",
    "SUITCASE_WORLD_SLICE_META",
    "SemanticWakeCoalescer",
    "V02ConflictError",
    "V02PermissionError",
    "V02RuntimeMigrationError",
    "V02ValidationError",
    "VERDICT_CONTINUED",
    "VERDICT_CONTINUED_WITH_LOSS",
    "VERDICT_UNVERIFIED",
    "VIEW_SCHEMA",
    "apply_runtime_migration_mutation",
    "build_default_state",
    "build_runtime_migration_context_summary",
    "collect_source_freshness",
    "compute_suitcase_bundle_sha256",
    "compute_vc_item_digest",
    "inspect_day_root_health_signals",
    "project_annual_decay_matrix_22",
    "project_chatless_life_review",
    "project_v02_runtime_migration",
    "read_vc_authority_watermark_readonly",
    "reconcile_backup_restore_vc_barrier",
    "record_revocation_tombstone",
    "record_vc_revocation_seal",
]

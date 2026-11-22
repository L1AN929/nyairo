#!/usr/bin/env python3
"""B6-5 / I: the observation -> observed-opportunity translation for real World actions.

AG-0 already declares ``CANDIDATE_KIND_CONSIDER_WORLD_OPPORTUNITY`` and already
owns ``WorldOpportunityCandidateSource``.  That source encodes one rule and one
rule only -- "World knows X != Chiyo knows X": a candidate may only come from a
World fact she actually perceived, carrying a perception reference and a
world-revision reference, and scanning the global World database to invent one is
explicitly forbidden.

What did not exist is the *translation*: nothing in the tree ever turned a real
gateway observation into those records (both composition roots construct the
source empty, and ``record_observed_opportunity`` had no caller).  This module is
that translation and nothing else.

It is deliberately dull:

* it reads one bounded observation plus the World's own availability truth;
* it emits one opportunity per action the World itself currently offers -- so the
  opportunity set and the choosable set are the same set, and an action the World
  does not offer cannot appear here;
* it never ranks, scores, recommends or picks.  AG-1 decides; this only reports
  what was observed.

Verbs translated (I adds the second one)
----------------------------------------
* ``MOVE`` -- one opportunity per reachable destination the selector-visible
  action space offers; the observed target is ``place:<location_id>``.
* ``CONSUME`` -- one opportunity per object the World gateway's own
  ``get_available_actions`` reports as consumable *right now* (F: that list is
  already the owner's verdict, computed from the live placement, the World's
  consumable declaration, the current place's opening state and the pose).  The
  observed target is ``object:<object_id>``.  This module never decides whether
  something is edible; it only reports what the owner already exposed.
* ``POSE`` / ``REST`` (W) -- the *body* verbs, which need two parameters each and
  therefore travel as one encoded target (``life_body_action_targets``):
  ``bodyact:pose:<pose>:<orientation>`` and ``bodyact:rest:<start|stop>:rest``.
  The pose list is the owner's own ``params.pose`` enumeration, the orientation is
  the one the canonical snapshot reports, and the current posture is excluded --
  the Resolver would answer ``already_in_pose`` and a no-op is not an opportunity.
  REST is offered only in the direction the owner itself says is available
  (``start_available`` / ``stop_available``); this module never decides that she
  "needs" to rest, and it never offers ``sleep_like``.
* ``PICK_UP`` / ``PLACE`` (v0.4 B) -- the *hand* verbs, which need two parameters
  each (``object_id`` + ``slot_id``, or ``object_id`` + a whole placement target) and
  therefore travel as one encoded target (``life_daily_action_targets``):
  ``dailyact:pick_up:<slot>:<object>`` /
  ``dailyact:place:<room|surface>:<...>:<object>``.  The object set is the owner's own
  ``PICK_UP`` candidate list (live placement + declared capability + pose + current
  place), the slots are the free hands it lists, and the destinations are the room she
  is really standing in plus the surfaces the owner itself projects for that same
  place -- nothing here invents a container, a surface or a second-hand slot.

The W catalog (``world_daily_opportunities.location_opportunity_catalog``) is read
as *facts about the places* -- walk seconds, opening state, whether she really sees
them -- and is attached to the richer, non-AG-0 view this module keeps for reports.
It is never a grant (every catalog entry is ``action_grant: false``) and never a
candidate list of its own: a place the World does not offer to move to still cannot
appear here.

Bounded observations only: the raw world revision travels inside the structured
references the World already uses for exactly-once bookkeeping, and the
selector-facing projection stays the one ``action_intent`` defines.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import unquote

import candidate_sources_ag0 as ag0
import life_body_action_targets as body_actions
import life_daily_action_targets as daily_actions

try:  # 18 章 §二 Day2：抑制理由码的唯一权威在世界经济 owner 那里。
    from world_property_economy import ALREADY_OWNED_SUPPRESSED
except ImportError:  # pragma: no cover - 世界脚本目录不在 sys.path 时按字面量比对
    ALREADY_OWNED_SUPPRESSED = "ALREADY_OWNED_SUPPRESSED"

#: How long one observation's opportunities stay valid.  Short on purpose: a
#: stale "the street is reachable" is exactly what requirement 5 forbids acting on.
DEFAULT_VALIDITY_SECONDS = 300

#: How many MOVE destinations one observation may put in front of AG-1.  The cognition
#: request itself is bounded at 16 candidates, and a hub street can honestly have more than a
#: dozen neighbours; without a bound the whole round fails with CANDIDATE_EXPLOSION before any
#: choice exists.  This is a *bound*, not a ranking: the owner's own selector order is kept.
#:
#: 0.7: the bound is seven, and an adjacent **street** the owner lists is reserved inside it
#: (see ``observed_move_opportunities``).  Six was measured against a hub whose other
#: candidates already fill the remaining budget; reserving the transit streets without giving
#: back a slot pushed the round to 17.  Seven is the largest total that still leaves the
#: default observation inside AG-1's sixteen, and the street reservation is what keeps a
#: whole sub-graph (e.g. the coast behind street_3) from being permanently unreachable.
MAX_MOVE_OPPORTUNITIES = 7

#: 0.7：默认街区里必须始终留在移动名额内的浏览型地点。花店的唯一可执行停留是
#: ``BROWSE_STORE``（只能逛），它是本人点名要保留的街区功能；经济店则由它自己的
#: 营业投影单独保留。名单只决定"哪些地点不会被上限截掉"，不排序、不增加候选总数。
RESERVED_BROWSE_PLACES = ("flower_shop",)

#: The verbs this translation understands as *World owner* verbs with a single parameter.
#: POSE and REST need two parameters each, so they travel as one encoded ``bodyact:`` target
#: instead (``life_body_action_targets``): the AR-0 payload keeps its single-target shape.
MOVE_VERB = "MOVE"
CONSUME_VERB = "CONSUME"
POSE_VERB = body_actions.POSE_VERB
REST_VERB = body_actions.REST_VERB
TOILET_VERB = body_actions.TOILET_VERB
#: v0.4 B: the *hand* verbs.  They need two parameters each (``object_id`` + ``slot_id``;
#: ``object_id`` + a whole placement target), so they travel as one encoded ``dailyact:``
#: target too (``life_daily_action_targets``) and the AR-0 payload shape is untouched.
PICK_UP_VERB = daily_actions.PICK_UP_VERB
PLACE_VERB = daily_actions.PLACE_VERB
#: v0.4 room-living: the two furniture/fixture verbs.  Their whole parameter set also
#: travels as one encoded ``dailyact:`` target, so they are translated exactly like the
#: hand verbs -- only the owner verb differs.  Both are offered only from the owner's own
#: availability verdict (owned+delivered+placed furniture / a declared fixture that is
#: really in this area); nothing here decides what may be arranged or toggled.
ARRANGE_VERB = daily_actions.ARRANGE_VERB
TOGGLE_FIXTURE_VERB = daily_actions.TOGGLE_FIXTURE_VERB
#: Bounds for the two new categories.  They are *offer* bounds inside the one round, not a
#: ranking and not a default: home has two declared areas and (today) one lamp, so a real
#: round emits one ARRANGE and, only where the fixture is present, one TOGGLE.  The cap
#: keeps room-living from ever pushing AG-1's sixteen-candidate budget out of reach while
#: still leaving the fixups of an ordinary home visible.
MAX_ARRANGE_OPPORTUNITIES = 4
MAX_TOGGLE_FIXTURE_OPPORTUNITIES = 4

#: Total World opportunities one observation may emit.  AG-1 hard-fails the whole round at
#: more than sixteen *allowed* candidates, and the other Life sources (a continuable artifact,
#: its version, a closeout, the current-activity continuation) already fill the rest of that
#: budget.  Twelve is the largest World count that still leaves those four guaranteed slots.
#: When a home with furniture and a lamp crosses it, the *tail* of the largest flexible
#: categories yields first (stays, then body poses, then moves, then the room-living pair) --
#: every category keeps at least one entry, so nothing is silently deleted and the model is
#: never handed an empty class of action.
MAX_WORLD_OPPORTUNITIES = 12
#: 0.7: 唯一一条**店内购买**目标。它不是 World 动词，也不是桥/网关的动词：价格、余额、
#: 营业/在店判断和记账都属于既有 ``world_property_economy`` 经济 owner。Life 这一侧只把
#: owner 当场给出的可买商品翻译成候选，target 里只装商品目录 id，绝不装价格或产权。
PURCHASE_VERB = "PURCHASE"
PURCHASE_REF_PREFIX = "buy:"
#: I-r1: a non-productive stay is *not* a World verb.  No owner executes "stand here": the
#: World owner's part is only the observation that she really is in this place, plus the
#: opening/affordance facts the W catalog already projects.  ``STAY`` is therefore a
#: Life-side descriptor, and the ``stay:`` namespace below is what the Life core reads back
#: to know it must record an internal checkpoint instead of submitting a World verb.
STAY_VERB = "STAY"
STAY_REF_PREFIX = "stay:"
#: How long one stay lasts, by the affordance the place itself offers.  Fixed here -- not
#: derived from the model, a feeling, or the round budget -- so the same place + affordance
#: always declares the same stay, and the completion contract can name it in advance.
STAY_SECONDS_BY_AFFORDANCE = {
    "SIT": 300,
    "SIT_AT_CAFE": 300,
    "REST": 300,
    # 0.7 C: 家具边的短停留；时长只是世界包声明的建议值，缺省时用这里的固定值。
    "DESK_PAUSE": 300,
    "WINDOW_PAUSE": 240,
    "TEA_PAUSE": 300,
    "READ_HERE": 600,
    "READ": 600,
    "SELF_STUDY": 600,
    "LOOK_AROUND": 180,
    "WALK": 240,
    "USE_PHONE": 240,
    "BROWSE_BOOKS": 300,
    # 模板 coastal_town 的便利店/鱼市/杂货店/书店都用这个名字说「逛逛」；
    # 漏在这里就不会生成停留候选（2026-11-07 起不再退回 QUIET_STOP）。
    "BROWSE_STORE": 300,
    "PRAY": 180,
    # 「什么都不干」的三档：短，是因为发呆本来没有任务感，十分钟的呆滞不是发呆是加班。
    "DAZE": 180,
    "POTTER": 180,
    "PEOPLE_WATCH": 180,
}

#: The affinity order a stay prefers when a place offers several: a stationary, non-productive
#: option first (a bench, a reading table, a quiet pause), and walking last -- walking is not
#: staying, so it is only used when the place offers nothing else this table knows.
#:
#: ``IDLE_STAY_AFFORDANCES`` sit at the end *on purpose*: they are the fallback flavour of a
#: place, never its headline.  Being last is also why they may not be picked by this order at
#: all (see ``_stay_descriptors``) -- an order alone would starve them forever, so they travel
#: as their own candidate on a rotating turn instead of competing for the one slot above.
STAY_DESCRIPTOR_PREFERENCE = (
    "SIT", "SIT_AT_CAFE", "REST", "READ_HERE", "READ", "SELF_STUDY", "USE_PHONE",
    # 0.7 C: 家中家具边的自然小事。都只是「在某个家具边待一会儿」这一种既有
    # ``stay_at_place`` 能力，标题不冒充整理/泡茶等没有端口的效果。
    "DESK_PAUSE", "WINDOW_PAUSE", "TEA_PAUSE",
    "BROWSE_BOOKS", "BROWSE_STORE", "PRAY", "LOOK_AROUND", "WALK",
    "DAZE", "POTTER", "PEOPLE_WATCH",
)
#: The "nothing is being produced here" affordances: sitting and drifting off, pottering about
#: at home, standing on a corner watching people.  They have no completion condition, no
#: artifact and no owner verb -- which is the whole point of them.
IDLE_STAY_AFFORDANCES = ("DAZE", "POTTER", "PEOPLE_WATCH")
#: How long one turn of the idle rotation lasts.  An hour, on the observation's own clock: long
#: enough that the same quiet stretch keeps the same offer (so a re-observation does not reshuffle
#: the candidate set), short enough that a whole day is not stuck on one flavour of doing nothing.
STAY_IDLE_ROTATION_SECONDS = 3600
#: 旧版"没有已知 affordance 就待一会儿"的兜底描述符。2026-11-07 起**不再自动产生**：
#: 没有可用执行定义时，只能明确报告，不能替她选一个 QUIET_STOP 伪装成功。
STAY_DESCRIPTOR_QUIET = "QUIET_STOP"
STAY_QUIET_SECONDS = 180


class StayDefinitionError(Exception):
    """一条显式活动定义不成立。

    ``code`` 是稳定、安全的固定错误码；``path`` 是可选的安全结构定位
    （``rules.json/location_rules/<合法id>/affordances/<index>/<合同字段>``）。
    二者都不含 description/title 值、异常正文或主机绝对路径。
    """

    def __init__(self, code: str, path: Optional[str] = None) -> None:
        super().__init__(code if not path else "%s: %s" % (code, path))
        self.code = code
        self.path = path


#: 地点 ID 合法字符与 schema.``ID_RE`` 同一约束；不合法时路径里不回显它。
_DEFINITION_PATH_PLACEHOLDER = "*"


def _safe_location_component(location_id: Any) -> Optional[str]:
    """合法地点 ID 才能进定位路径；否则返回 ``None``（不直接回显任意键）。"""

    text = str(location_id or "")
    if not text:
        return None
    try:
        from components.worldgen import schema as worldgen_schema
    except ImportError:
        import sys
        from pathlib import Path

        root = str(Path(__file__).resolve().parents[3])
        if root not in sys.path:
            sys.path.insert(0, root)
        from components.worldgen import schema as worldgen_schema
    if len(text) > worldgen_schema.MAX_LOCATION_ID_LENGTH:
        return None
    if not worldgen_schema.ID_RE.fullmatch(text):
        return None
    return text


def _definition_path(
    location_id: Any, index: Any, field: Any
) -> str:
    """``rules.json/location_rules/...`` 形式的安全定位（闭集字段 + 严格整数下标）。"""

    activity_definition = _activity_definition_module()
    safe_field = (
        str(field)
        if str(field) in activity_definition.DEFINITION_FIELDS
        else activity_definition.FIELD_EXECUTION
    )
    safe_place = _safe_location_component(location_id) or _DEFINITION_PATH_PLACEHOLDER
    return "rules.json/location_rules/%s/affordances/%d/%s" % (
        safe_place, int(index), safe_field)


def _activity_definition_module() -> Any:
    """世界包侧的活动定义合同；Life 只消费，不另建第二份解释。"""

    try:
        from components.worldgen import activity_definition
    except ImportError:
        import sys
        from pathlib import Path

        root = str(Path(__file__).resolve().parents[3])
        if root not in sys.path:
            sys.path.insert(0, root)
        from components.worldgen import activity_definition
    return activity_definition


#: 允许经 catalog 读取转发到调用方的定义错误码（严格闭集）。
_DEFINITION_ERROR_CODES = frozenset({
    "UNSUPPORTED_ACTIVITY_DEFINITION",
    "INVALID_ACTIVITY_EXECUTION",
    "INCOMPLETE_ACTIVITY_EXECUTION",
})
#: 安全结构定位的地点段：合法字符闭集，不含分隔符或任意输入值。
_DEFINITION_PLACE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _safe_definition_error(
    code: Any, path: Any
) -> Optional[tuple[str, Optional[str]]]:
    """把 catalog 里的定义错误收敛成 ``(固定code, 安全path)``；否则返回 None。

    ``code`` 必须在闭集内；``path`` 必须是
    ``rules.json/location_rules/<合法地点>/affordances/<整数下标>/<合同字段>``，
    否则只保留 code、不传播路径字符串。
    """

    if code not in _DEFINITION_ERROR_CODES:
        return None
    safe_path: Optional[str] = None
    if isinstance(path, str):
        parts = path.split("/")
        if (
            len(parts) == 6
            and parts[0] == "rules.json"
            and parts[1] == "location_rules"
            and _DEFINITION_PLACE_RE.fullmatch(parts[2])
            and parts[3] == "affordances"
            and parts[4].isdigit()
            and str(int(parts[4])) == parts[4]
            and parts[5] in _activity_definition_module().DEFINITION_FIELDS
        ):
            safe_path = path
    return str(code), safe_path
#: How each idle descriptor is said out loud in a candidate label.  A place name goes in front
#: ("在临海的住居发会儿呆"), so these are the verb phrase only.  A descriptor with no phrase here
#: keeps the neutral "在X待一会儿" label.
STAY_IDLE_PHRASES = {
    "DAZE": "发会儿呆",
    "POTTER": "随便转转",
    "PEOPLE_WATCH": "站着看看人",
}

#: Target namespaces.  The prefix is what the Life core reads back to decide *which* owner
#: verb an adopted opportunity implies -- never the model, and never a guess.
PLACE_REF_PREFIX = "place:"
OBJECT_REF_PREFIX = "object:"
#: W: the body verbs' namespace.  The whole parameter set lives inside it, so the same
#: "read the target back" rule applies to a posture and a rest command.
BODY_ACTION_REF_PREFIX = body_actions.BODY_ACTION_REF_PREFIX
#: v0.4 B: the hand verbs' namespace.  Like ``bodyact:``, the whole parameter set lives
#: inside the ref, so reading the target back is the only way anything learns what was
#: picked up or where it was put down.
DAILY_ACTION_REF_PREFIX = daily_actions.DAILY_ACTION_REF_PREFIX
#: The destination kinds this seam can name (the owner refuses ``container``/``in_transit``
#: destinations for V1 ``PLACE``), re-exported from the encoding module so a caller never
#: has to import two modules to compare a destination.
PLACEMENT_ROOM = daily_actions.PLACEMENT_ROOM
PLACEMENT_SURFACE = daily_actions.PLACEMENT_SURFACE

#: The owner's own load model for one hand movement (``body_consequence``: ``PICK_UP`` /
#: ``PLACE`` are a ~2 s manipulation, deliberately not an activity duration).  Copied so a
#: report can name a number without importing a World module on every observation; the
#: owner's value is authoritative and this copy must move with it.
HAND_MANIPULATION_SECONDS = 2.0
#: 一次柜台购买本身是一次瞬时 owner 记账（不是一段旅程）。沿用同一次手部操作的
#: 2 秒负荷声明，只用于显示"这件事有多长"；真正的时间事实是经济 owner 的交付状态。
PURCHASE_SECONDS = HAND_MANIPULATION_SECONDS
#: The owner's own posture duration (``body_consequence.MOVEMENT_SECONDS_BY_ACTION["POSE"]``).
POSTURE_SECONDS = 5.0
#: Where a ``duration_seconds`` number came from, so an assumed/owner value never looks
#: like a measurement.
DURATION_SOURCE_OWNER = "body_consequence"
DURATION_SOURCE_ROUTE = "world_package_route"


def _revision_of(observation: Mapping[str, Any]) -> Any:
    return observation.get("world_revision")


def _place_of(observation: Mapping[str, Any]) -> Optional[str]:
    location = observation.get("location") or {}
    place = location.get("place_id")
    return str(place) if place else None


def _validity_window(observed_at: str, valid_for_seconds: int) -> tuple[str, str]:
    valid_from = ag0._require_iso(observed_at, "observed_at")
    valid_until = ag0._require_iso(
        _plus_seconds(valid_from, valid_for_seconds), "valid_until"
    )
    return (valid_from, valid_until)


def observed_move_opportunities(
    observation: Mapping[str, Any],
    *,
    action_space: Mapping[str, Any],
    occurred_at: str,
    observed_at: str,
    valid_for_seconds: int = DEFAULT_VALIDITY_SECONDS,
    place_kinds: Optional[Mapping[str, Any]] = None,
    reserved_destinations: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """One opportunity per MOVE destination the observation supports.

    ``action_space`` must be the *selector-visible* projection (what a decision
    is actually allowed to choose from), not the raw gateway list: the point of
    the seam is that she can only consider what she could also pick.

    ``place_kinds`` is the W catalog's own ``{location_id: kind}`` map.  It is used
    for one structural rule only: a **street** the owner lists is the graph's transit
    node, so it is never counted against ``MAX_MOVE_OPPORTUNITIES`` and every adjacent
    street the owner offers stays in the round.  Without it the world's own order is
    kept and the first N are used (the old behaviour); the venue cap itself is
    unchanged, so a hub still cannot explode the decision's candidate set.

    ``reserved_destinations`` (0.7) is the caller's own short list of places that must
    survive the bound because a real owner action exists there right now -- today the
    one physical store that is open and sells economy goods.  It is still only a
    *reservation inside* ``MAX_MOVE_OPPORTUNITIES`` (never an extra candidate, never a
    ranking), and a destination the owner's action space does not offer is ignored.
    """

    place = _place_of(observation)
    revision = _revision_of(observation)
    world_id = observation.get("world_id")
    if place is None or revision is None:
        return []

    valid_from, valid_until = _validity_window(observed_at, valid_for_seconds)
    occurred = ag0._require_iso(occurred_at, "occurred_at")
    reserved = {str(item) for item in (reserved_destinations or []) if str(item)}

    # Emit in the owner's own order, tagging which rows are adjacent streets.  The
    # order is what the final list keeps; the cap is a *selection*, not a ranking.
    rows: list[tuple[int, dict[str, Any], bool]] = []
    for index, candidate in enumerate(action_space.get("candidates") or []):
        if not isinstance(candidate, Mapping):
            continue
        if candidate.get("verb") != MOVE_VERB:
            continue
        params = candidate.get("params") or {}
        destination = str(params.get("destination_location_id") or "").strip()
        if not destination:
            continue
        is_street = str((place_kinds or {}).get(destination) or "") == "street"
        rows.append((index, {
            "opportunity_id": "wopp:move:%s:r%s:%s" % (place, revision, destination),
            "perception_ref": "percept:gateway-snapshot:%s:r%s" % (place, revision),
            "world_revision_ref": "wrev:%s:r%s" % (world_id or "world", revision),
            "target_refs": ["place:%s" % destination],
            "valid_from": valid_from,
            "valid_until": valid_until,
            "occurred_at": occurred,
            "observed_at": valid_from,
            "observed": True,
            "status": "AVAILABLE",
            "revision": 1,
            "subject_id": ag0.GLOBAL_SUBJECT_ID,
            "location_ref": "place:%s" % place,
        }, is_street, destination in reserved))

    reserved_rows = [row for row in rows if row[2] or row[3]]
    venues = [row for row in rows if not (row[2] or row[3])]
    # The transit streets (and the one open economy store) come first, then the owner's
    # own venue order fills the rest.  The *total* stays at the bound, so the reservation
    # never enlarges the offer.
    room = MAX_MOVE_OPPORTUNITIES - len(reserved_rows)
    kept = ((reserved_rows + venues[:room]) if room > 0
            else reserved_rows[:MAX_MOVE_OPPORTUNITIES])
    kept.sort(key=lambda row: row[0])  # the owner's own order survives the selection
    return [row[1] for row in kept]


def observed_consume_opportunities(
    observation: Mapping[str, Any],
    *,
    available_actions: Optional[Mapping[str, Any]],
    occurred_at: str,
    observed_at: str,
    valid_for_seconds: int = DEFAULT_VALIDITY_SECONDS,
) -> list[dict[str, Any]]:
    """One opportunity per object the World itself says is consumable *now*.

    ``available_actions`` is the gateway's own ``get_available_actions`` payload.
    Its ``CONSUME`` entry carries the owner's availability verdict and the
    candidates it computed from the live substrate -- so a refused or unavailable
    entry produces no opportunity here, and nothing is inferred from a scene
    description.  An absent entry (an older gateway) is likewise no opportunity:
    "not exposed" is not "maybe available".
    """

    place = _place_of(observation)
    revision = _revision_of(observation)
    world_id = observation.get("world_id")
    if place is None or revision is None or not isinstance(available_actions, Mapping):
        return []

    valid_from, valid_until = _validity_window(observed_at, valid_for_seconds)
    occurred = ag0._require_iso(occurred_at, "occurred_at")

    opportunities: list[dict[str, Any]] = []
    for entry in available_actions.get("actions") or []:
        if not isinstance(entry, Mapping) or entry.get("verb") != CONSUME_VERB:
            continue
        if entry.get("available") is not True:
            continue
        for offered in entry.get("candidates") or []:
            if not isinstance(offered, Mapping):
                continue
            object_id = str(offered.get("object_id") or "").strip()
            if not object_id:
                continue
            opportunities.append({
                "opportunity_id": "wopp:consume:%s:r%s:%s" % (place, revision, object_id),
                "perception_ref": "percept:gateway-available-actions:%s:r%s"
                                  % (place, revision),
                "world_revision_ref": "wrev:%s:r%s" % (world_id or "world", revision),
                "target_refs": ["%s%s" % (OBJECT_REF_PREFIX, object_id)],
                "valid_from": valid_from,
                "valid_until": valid_until,
                "occurred_at": occurred,
                "observed_at": valid_from,
                "observed": True,
                "status": "AVAILABLE",
                "revision": 1,
                "subject_id": ag0.GLOBAL_SUBJECT_ID,
                "location_ref": "place:%s" % place,
            })
    return opportunities


def observed_body_action_opportunities(
    observation: Mapping[str, Any],
    *,
    available_actions: Optional[Mapping[str, Any]],
    occurred_at: str,
    observed_at: str,
    valid_for_seconds: int = DEFAULT_VALIDITY_SECONDS,
) -> list[dict[str, Any]]:
    """One opportunity per *body* action the World owner itself says is available now.

    Two owner verbs, two parameters each, one encoded target
    (``life_body_action_targets``):

    * ``POSE`` -- one opportunity per pose the owner's own ``params.pose`` enumeration
      offers, carrying the orientation the canonical snapshot reports (a posture change
      keeps facing).  The posture she is already in is skipped: the Resolver answers
      ``already_in_pose`` and a no-op is not something she can be invited to choose.
      With no snapshot orientation there is no POSE opportunity at all -- an invented
      orientation is exactly the second parameter this module must not make up.
    * ``REST`` -- one opportunity per direction the owner marks available
      (``start_available`` / ``stop_available``), in ``rest`` mode.  When the owner
      additionally reports ``sleep_like_available`` (start available **and** lying
      **and** a declared rest surface really here), a second ``start`` opportunity is
      emitted with ``sleep_like``; the ordinary ``rest`` start stays.  Neither one is
      a decision that she needs rest -- only the owner's availability verdict speaks.

    Failure is closed everywhere: an absent/refused entry, a pose list this encoding
    cannot name, or a snapshot that cannot answer produces no opportunity.  Nothing
    here decides that she is tired, that she should sit down, or that rest is due --
    the owner's availability verdict is the only source.
    """

    place = _place_of(observation)
    revision = _revision_of(observation)
    world_id = observation.get("world_id")
    if place is None or revision is None or not isinstance(available_actions, Mapping):
        return []

    valid_from, valid_until = _validity_window(observed_at, valid_for_seconds)
    occurred = ag0._require_iso(occurred_at, "occurred_at")
    entries: dict[str, Mapping[str, Any]] = {}
    for entry in available_actions.get("actions") or []:
        if isinstance(entry, Mapping) and entry.get("verb"):
            entries[str(entry["verb"])] = entry

    def _emit(target_ref: str, *, verb: str, suffix: str,
              perception_ref: str) -> dict[str, Any]:
        return {
            "opportunity_id": "wopp:%s:%s:r%s:%s" % (verb.lower(), place, revision, suffix),
            "perception_ref": perception_ref,
            "world_revision_ref": "wrev:%s:r%s" % (world_id or "world", revision),
            "target_refs": [target_ref],
            "valid_from": valid_from,
            "valid_until": valid_until,
            "occurred_at": occurred,
            "observed_at": valid_from,
            "observed": True,
            "status": "AVAILABLE",
            "revision": 1,
            "subject_id": ag0.GLOBAL_SUBJECT_ID,
            "location_ref": "place:%s" % place,
        }

    opportunities: list[dict[str, Any]] = []
    current_pose = str(observation.get("pose") or "").strip()
    current_orientation = str(observation.get("orientation") or "").strip()

    pose_entry = entries.get(POSE_VERB) or {}
    if pose_entry.get("available") is True:
        declared = pose_entry.get("params") or {}
        poses = [str(item or "").strip() for item in (declared.get("pose") or [])]
        orientations = [str(item or "").strip()
                        for item in (declared.get("orientation") or [])]
        if current_orientation and current_orientation in orientations:
            for offered in poses:
                if not offered or offered not in body_actions.POSES:
                    continue
                if offered == current_pose:
                    # Same posture and same orientation: the owner's own no-op.
                    continue
                opportunities.append(_emit(
                    body_actions.encode_pose_target(offered, current_orientation),
                    verb=POSE_VERB, suffix=offered,
                    perception_ref="percept:gateway-snapshot:%s:r%s" % (place, revision),
                ))

    rest_entry = entries.get(REST_VERB) or {}
    for state, availability_key in (("start", "start_available"),
                                    ("stop", "stop_available")):
        if rest_entry.get(availability_key) is not True:
            continue
        opportunities.append(_emit(
            body_actions.encode_rest_target(state, "rest"),
            verb=REST_VERB, suffix=state,
            perception_ref="percept:gateway-available-actions:%s:r%s" % (place, revision),
        ))
        # The night mode is a second, *narrower* owner verdict: the gateway says
        # ``sleep_like_available`` only when a start is really available **and** she is
        # already lying **and** a declared rest surface is placed here.  It never means
        # "she should sleep"; it means the owner would accept this exact command now.
        if state == "start" and rest_entry.get("sleep_like_available") is True:
            opportunities.append(_emit(
                body_actions.encode_rest_target("start", "sleep_like"),
                verb=REST_VERB, suffix="start_sleep_like",
                perception_ref=("percept:gateway-available-actions:%s:r%s"
                                % (place, revision)),
            ))
    return opportunities


def observed_toilet_opportunities(
    observation: Mapping[str, Any],
    *,
    available_actions: Optional[Mapping[str, Any]],
    occurred_at: str,
    observed_at: str,
    valid_for_seconds: int = DEFAULT_VALIDITY_SECONDS,
) -> list[dict[str, Any]]:
    """One opportunity per declared virtual facility the owner exposes *now*.

    The entry exists only for an instance with an explicit lived-needs profile
    (the gateway's own ``TOILET`` verdict).  This module never decides that she
    needs it, which facility exists, or that using it is a good idea.
    """

    place = _place_of(observation)
    revision = _revision_of(observation)
    world_id = observation.get("world_id")
    if place is None or revision is None or not isinstance(available_actions, Mapping):
        return []
    entry = next((row for row in available_actions.get("actions") or []
                  if isinstance(row, Mapping) and row.get("verb") == TOILET_VERB), None)
    if not entry or entry.get("available") is not True:
        return []

    valid_from, valid_until = _validity_window(observed_at, valid_for_seconds)
    occurred = ag0._require_iso(occurred_at, "occurred_at")
    opportunities: list[dict[str, Any]] = []
    for offered in entry.get("candidates") or []:
        if not isinstance(offered, Mapping):
            continue
        facility_id = str(offered.get("facility_id") or "").strip()
        if not facility_id:
            continue
        try:
            target = body_actions.encode_toilet_target(facility_id)
        except body_actions.BodyActionTargetError:
            continue
        suffix = hashlib.sha256(facility_id.encode("utf-8")).hexdigest()[:24]
        opportunities.append({
            "opportunity_id": "wopp:toilet:%s:r%s:%s" % (place, revision, suffix),
            "perception_ref": "percept:gateway-available-actions:%s:r%s" % (place, revision),
            "world_revision_ref": "wrev:%s:r%s" % (world_id or "world", revision),
            "target_refs": [target],
            "valid_from": valid_from,
            "valid_until": valid_until,
            "occurred_at": occurred,
            "observed_at": valid_from,
            "observed": True,
            "status": "AVAILABLE",
            "revision": 1,
            "subject_id": ag0.GLOBAL_SUBJECT_ID,
            "location_ref": "place:%s" % place,
        })
    return opportunities


#: The id charset AG-0's ``ObservedWorldOpportunity`` accepts after the ``wopp:`` prefix
#: (``activity_continuity._STRUCTURED_REF_RE``).  The ``dailyact:`` encoding spells plain
#: fields with percent quoting; a ``%`` is not a legal ref character, so the opportunity id
#: is built from the *decoded* spelling (or a hash when that is not survivable).
_OPPORTUNITY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,160}$")


def _daily_opportunity_suffix(target: str) -> str:
    """A deterministic, structured-ref-safe spelling of a ``dailyact:`` target.

    The target's own field encoding is untouched (that is ``life_daily_action_targets``'
    contract).  Only the *opportunity id* is normalized: room/pick-up targets already
    survive verbatim, while a legacy percent-quoted field is decoded.  Anything still
    outside AG-0's id charset is replaced by a stable hash of the exact target, so a real
    owner fact is never dropped merely because its id is exotic.
    """

    suffix = str(target)[len(DAILY_ACTION_REF_PREFIX):]
    decoded = unquote(suffix)
    if _OPPORTUNITY_ID_RE.fullmatch(decoded):
        return decoded
    return "h:" + hashlib.sha256(suffix.encode("utf-8")).hexdigest()[:32]


def observed_daily_action_opportunities(
    observation: Mapping[str, Any],
    *,
    available_actions: Optional[Mapping[str, Any]],
    occurred_at: str,
    observed_at: str,
    valid_for_seconds: int = DEFAULT_VALIDITY_SECONDS,
    destinations: Optional[list[Mapping[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """One opportunity per *hand* action the World owner itself says is possible now.

    Two owner verbs, one encoded target each (``life_daily_action_targets``):

    * ``PICK_UP`` -- one opportunity per object in the gateway's own ``PICK_UP``
      candidates, carrying exactly one of the free hand slots the owner listed for it.
      The object set is the owner's verdict (live placement + ``takeable`` capability +
      pose + current place), so nothing here decides what may be lifted.
    * ``PLACE`` -- one opportunity per object she is *really* holding (the canonical
      snapshot's own ``hands`` mapping) times one destination from ``destinations``.  The
      destination list is the caller's bounded read of real places to put something down
      (the room she is standing in, plus any surface the owner itself projects for that
      same place); an empty or absent list means the current room and nothing else.  No
      container is ever guessed: the owner refuses container destinations in V1, and this
      encoding cannot spell one.

    Failure is closed everywhere: an absent/refused entry, no candidates, no free slot, no
    held object, or a parameter set this encoding refuses produces no opportunity.  Nothing
    here ranks, scores, recommends or picks -- AG-1 decides, and the Resolver re-reads
    hands, pose, capability and destination at commit time.
    """

    place = _place_of(observation)
    revision = _revision_of(observation)
    world_id = observation.get("world_id")
    if place is None or revision is None or not isinstance(available_actions, Mapping):
        return []

    valid_from, valid_until = _validity_window(observed_at, valid_for_seconds)
    occurred = ag0._require_iso(occurred_at, "occurred_at")
    entries: dict[str, Mapping[str, Any]] = {}
    for entry in available_actions.get("actions") or []:
        if isinstance(entry, Mapping) and entry.get("verb"):
            entries[str(entry["verb"])] = entry

    def _emit(verb: str, target: str, *, perception_ref: str) -> dict[str, Any]:
        return {
            "opportunity_id": "wopp:%s:%s:r%s:%s"
                              % (verb.lower(), place, revision,
                                 _daily_opportunity_suffix(target)),
            "perception_ref": perception_ref,
            "world_revision_ref": "wrev:%s:r%s" % (world_id or "world", revision),
            "target_refs": [target],
            "valid_from": valid_from,
            "valid_until": valid_until,
            "occurred_at": occurred,
            "observed_at": valid_from,
            "observed": True,
            "status": "AVAILABLE",
            "revision": 1,
            "subject_id": ag0.GLOBAL_SUBJECT_ID,
            "location_ref": "place:%s" % place,
        }

    opportunities: list[dict[str, Any]] = []

    pick_up = entries.get(PICK_UP_VERB) or {}
    if pick_up.get("available") is True:
        for offered in pick_up.get("candidates") or []:
            if not isinstance(offered, Mapping):
                continue
            object_id = str(offered.get("object_id") or "").strip()
            if not object_id:
                continue
            offered_slots = {str(slot or "").strip()
                             for slot in (offered.get("slots") or [])}
            slot = next((candidate for candidate in daily_actions.HAND_SLOTS
                         if candidate in offered_slots), None)
            if slot is None:
                # Every hand the owner offered is outside its own vocabulary: there is no
                # honest slot to name, so there is no target to build.
                continue
            try:
                target = daily_actions.encode_daily_action_target(
                    PICK_UP_VERB, {"object_id": object_id, "slot_id": slot})
            except daily_actions.DailyActionTargetError:
                continue
            opportunities.append(_emit(
                PICK_UP_VERB, target,
                perception_ref="percept:gateway-available-actions:%s:r%s"
                               % (place, revision)))

    place_entry = entries.get(PLACE_VERB) or {}
    if place_entry.get("available") is True:
        held = _held_objects(observation)
        chosen = _daily_destinations(observation, destinations)
        for object_id in held:
            for destination in chosen:
                try:
                    target = daily_actions.encode_daily_action_target(
                        PLACE_VERB, {"object_id": object_id, "target": destination})
                except daily_actions.DailyActionTargetError:
                    continue
                opportunities.append(_emit(
                    PLACE_VERB, target,
                    perception_ref="percept:gateway-snapshot:%s:r%s" % (place, revision)))
    return opportunities


def _room_living_emit(verb: str, target: str, *, place: str, revision: Any,
                      world_id: Any, observed_at: str, occurred_at: str,
                      valid_from: str, valid_until: str,
                      perception_ref: str) -> dict[str, Any]:
    """One world opportunity row for an encoded ``dailyact:`` room-living target."""

    return {
        "opportunity_id": "wopp:%s:%s:r%s:%s"
                          % (verb.lower(), place, revision,
                             _daily_opportunity_suffix(target)),
        "perception_ref": perception_ref,
        "world_revision_ref": "wrev:%s:r%s" % (world_id or "world", revision),
        "target_refs": [target],
        "valid_from": valid_from,
        "valid_until": valid_until,
        "occurred_at": occurred_at,
        "observed_at": valid_from,
        "observed": True,
        "status": "AVAILABLE",
        "revision": 1,
        "subject_id": ag0.GLOBAL_SUBJECT_ID,
        "location_ref": "place:%s" % place,
    }


def observed_arrange_opportunities(
    observation: Mapping[str, Any],
    *,
    available_actions: Optional[Mapping[str, Any]],
    occurred_at: str,
    observed_at: str,
    valid_for_seconds: int = DEFAULT_VALIDITY_SECONDS,
) -> list[dict[str, Any]]:
    """One opportunity per legal room/surface destination for furniture she is WITH.

    ``available_actions`` is the gateway's own verdict: an ARRANGE candidate is an
    instance that is owned + delivered + relocatable and **already placed at home**.
    The destination list is the owner's own ``rooms`` (declared home areas) plus the
    instance's own declared surfaces -- never a place this module invents.

    An ARRANGE is only executable where the furniture really is, so a candidate is only
    offered while the body is in the same place as the placement (the Resolver would
    answer ``NOT_HOME``/``FURNITURE_NOT_HOME`` otherwise).  The current destination is
    skipped: a same-target arrange is the owner's own no-op, not an invitation to choose.
    """

    place = _place_of(observation)
    revision = _revision_of(observation)
    world_id = observation.get("world_id")
    if place is None or revision is None or not isinstance(available_actions, Mapping):
        return []

    entry = next((row for row in available_actions.get("actions") or []
                  if isinstance(row, Mapping) and row.get("verb") == ARRANGE_VERB), None)
    if not entry or entry.get("available") is not True:
        return []

    valid_from, valid_until = _validity_window(observed_at, valid_for_seconds)
    occurred = ag0._require_iso(occurred_at, "occurred_at")
    rooms = [
        {"location_id": str(room.get("location_id") or "").strip(),
         "area_id": str(room.get("area_id") or "").strip()}
        for room in entry.get("rooms") or []
        if isinstance(room, Mapping)
    ]
    opportunities: list[dict[str, Any]] = []
    for offered in entry.get("candidates") or []:
        if not isinstance(offered, Mapping):
            continue
        object_id = str(offered.get("object_id") or "").strip()
        if not object_id:
            continue
        placement = offered.get("placement")
        if not isinstance(placement, Mapping):
            continue
        if str(placement.get("location_id") or "") != place:
            # She is not where the furniture is: arranging it from here is not allowed.
            continue
        current_area = str(placement.get("area_id") or "")
        current_surface = str(placement.get("surface_id") or "")
        current_kind = str(placement.get("kind") or "")
        destinations: list[dict[str, Any]] = []
        for room in rooms:
            if room["location_id"] != place or not room["area_id"]:
                continue
            if room["area_id"] == current_area:
                continue
            destinations.append({"kind": daily_actions.PLACEMENT_ROOM,
                                 "location_id": place, "area_id": room["area_id"]})
        for surface in offered.get("surfaces") or []:
            surface_id = str(surface or "").strip()
            if not surface_id:
                continue
            if current_kind == daily_actions.PLACEMENT_SURFACE and surface_id == current_surface:
                continue
            destinations.append({"kind": daily_actions.PLACEMENT_SURFACE,
                                 "location_id": place, "surface_id": surface_id})
        for destination in destinations:
            try:
                target = daily_actions.encode_daily_action_target(
                    ARRANGE_VERB, {"object_id": object_id, "target": destination})
            except daily_actions.DailyActionTargetError:
                continue
            opportunities.append(_room_living_emit(
                ARRANGE_VERB, target, place=place, revision=revision, world_id=world_id,
                observed_at=observed_at, occurred_at=occurred,
                valid_from=valid_from, valid_until=valid_until,
                perception_ref="percept:gateway-available-actions:%s:r%s"
                               % (place, revision)))
            if len(opportunities) >= MAX_ARRANGE_OPPORTUNITIES:
                return opportunities
    return opportunities


def observed_toggle_fixture_opportunities(
    observation: Mapping[str, Any],
    *,
    available_actions: Optional[Mapping[str, Any]],
    occurred_at: str,
    observed_at: str,
    valid_for_seconds: int = DEFAULT_VALIDITY_SECONDS,
) -> list[dict[str, Any]]:
    """One opportunity per declared fixture the owner offers in this area.

    The offered state is the **absolute opposite** of the recorded power, so the round can
    never invite a blind toggle or a same-state write.  An *unknown* power is not assumed
    to be ``off``: the offer is the definite ``off`` command (the owner records a real
    ``off`` and only then does ``on`` become the opposite); a second round then offers
    ``on``.  Nothing here decides that a light should be on.
    """

    place = _place_of(observation)
    revision = _revision_of(observation)
    world_id = observation.get("world_id")
    if place is None or revision is None or not isinstance(available_actions, Mapping):
        return []

    entry = next((row for row in available_actions.get("actions") or []
                  if isinstance(row, Mapping) and row.get("verb") == TOGGLE_FIXTURE_VERB), None)
    if not entry or entry.get("available") is not True:
        return []

    valid_from, valid_until = _validity_window(observed_at, valid_for_seconds)
    occurred = ag0._require_iso(occurred_at, "occurred_at")
    opportunities: list[dict[str, Any]] = []
    for offered in entry.get("candidates") or []:
        if not isinstance(offered, Mapping):
            continue
        object_id = str(offered.get("object_id") or "").strip()
        if not object_id:
            continue
        power = offered.get("power")
        power = power.strip().lower() if isinstance(power, str) else None
        # The opposite of a *known* power; an unknown power is commanded to the definite
        # ``off`` (never folded into "it is off" -- the owner records a real off), and only
        # then does ``on`` become the opposite.
        wanted = "on" if power == "off" else "off"
        try:
            target = daily_actions.encode_daily_action_target(
                TOGGLE_FIXTURE_VERB, {"object_id": object_id, "state": wanted})
        except daily_actions.DailyActionTargetError:
            continue
        opportunities.append(_room_living_emit(
            TOGGLE_FIXTURE_VERB, target, place=place, revision=revision, world_id=world_id,
            observed_at=observed_at, occurred_at=occurred,
            valid_from=valid_from, valid_until=valid_until,
            perception_ref="percept:gateway-available-actions:%s:r%s" % (place, revision)))
        if len(opportunities) >= MAX_TOGGLE_FIXTURE_OPPORTUNITIES:
            return opportunities
    return opportunities


def _bound_world_opportunities(groups: list[list[dict[str, Any]]]) -> list[list[dict[str, Any]]]:
    """Trim the tail of the largest flexible categories until the World offer fits.

    ``groups`` is ``[move, consume, stay, body, toilet, daily, arrange, toggle, purchase]``.
    Only the tail of a group is ever dropped, and never a group's last entry, so each class of
    action stays represented.  The order (stays, poses, moves, arrange, toggle) is the order in
    which a place with many declared stays yields room to the harder-to-reach offers.
    """

    total = sum(len(group) for group in groups)
    excess = total - MAX_WORLD_OPPORTUNITIES
    if excess <= 0:
        return groups
    for index in (2, 3, 5, 0, 6, 7, 8, 1):  # stay, body, daily, move, arrange, toggle, purchase, consume
        if excess <= 0:
            break
        group = groups[index]
        min_keep = (
            min(len(group), 4)
            if index == 8
            else (min(len(group), 2) if index == 1 else 1)
        )
        if len(group) <= min_keep:
            continue
        keep = max(min_keep, len(group) - excess)
        excess -= len(group) - keep
        groups[index] = group[:keep]
    return groups


def _held_objects(observation: Mapping[str, Any]) -> list[str]:
    """The objects the canonical snapshot says are in a hand, in slot order."""

    hands = observation.get("hands")
    if not isinstance(hands, Mapping):
        return []
    held: list[str] = []
    for slot in daily_actions.HAND_SLOTS:
        object_id = str(hands.get(slot) or "").strip()
        if object_id and object_id not in held:
            held.append(object_id)
    return held


def _daily_destinations(
    observation: Mapping[str, Any],
    destinations: Optional[list[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    """The real places she could put something down, deduplicated and bounded.

    The room she is standing in is derivable from the canonical snapshot itself
    (``location.place_id`` + ``location.area_id``), which is also exactly what the owner's
    own ``PLACE`` destination rule compares a room target against.  Surfaces only ever
    arrive from the owner's own projection for *this* place; a destination naming another
    place, or a shape this encoding cannot carry, is dropped rather than repaired.

    The owner's projection (``get_environment`` -> ``surfaces_here``) spells a surface
    object ``{"surface_id", "surface_kind", "location_id", ...}``: ``surface_kind`` is the
    furniture kind (``desk_surface``, ``bed_surface``, ...), while this module's placement
    encoding names the *target* kind ``surface``.  Those are two different vocabularies, so
    the presence of a real ``surface_id`` -- not a matching ``kind`` string -- is what says
    "owner-projected surface here".  Reading the furniture kind as the placement kind would
    drop every surface the owner really exposes, leaving only the coarse room destination.
    """

    location = observation.get("location") or {}
    place = str(location.get("place_id") or "").strip()
    area = str(location.get("area_id") or "").strip()
    if not place or not area:
        return []
    chosen: list[dict[str, Any]] = [
        {"kind": daily_actions.PLACEMENT_ROOM, "location_id": place, "area_id": area}]
    seen = {daily_actions.PLACEMENT_ROOM}
    for item in destinations or []:
        if not isinstance(item, Mapping):
            continue
        surface_id = str(item.get("surface_id") or "").strip()
        location_id = str(item.get("location_id") or "").strip()
        if not surface_id or location_id != place:
            continue
        key = "%s@%s" % (surface_id, location_id)
        if key in seen:
            continue
        seen.add(key)
        chosen.append({"kind": daily_actions.PLACEMENT_SURFACE,
                       "location_id": location_id, "surface_id": surface_id})
    return chosen


def stay_target(place: str, descriptor: str, seconds: int) -> str:
    """The one target spelling a stay's whole plan travels in.

    ``stay:<place>:<descriptor>:<seconds>`` -- so the Life core can rebuild the plan from the
    committed candidate, the Activity's own ``subject_refs`` and the checkpoint it recorded,
    without a second plan store and without asking a model what it meant.
    """

    return "%s%s:%s:%d" % (STAY_REF_PREFIX, str(place), str(descriptor), int(seconds))


def parse_stay_target(target: Any) -> Optional[dict[str, Any]]:
    """Split a ``stay:`` target back into its plan, or ``None`` when it is not one."""

    text = str(target or "").strip()
    if not text.startswith(STAY_REF_PREFIX):
        return None
    parts = text[len(STAY_REF_PREFIX):].split(":")
    if len(parts) != 3 or not parts[0] or not parts[1]:
        return None
    try:
        seconds = int(parts[2])
    except ValueError:
        return None
    if seconds <= 0:
        return None
    return {"place": parts[0], "descriptor": parts[1], "seconds": seconds,
            "target_ref": text}


def _stay_rotation_turn(observed_at: Any) -> int:
    """Which turn of the idle rotation this observation falls on.

    Counted in hours on the observation's *own* instant, so every caller in one round agrees,
    a quiet hour keeps one answer, and a failed read of the instant degrades to turn 0 instead
    of inventing a random one.
    """

    from datetime import datetime, timezone

    text = str(observed_at or "").strip()
    if not text:
        return 0
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return 0
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp()) // STAY_IDLE_ROTATION_SECONDS


def _stay_descriptors(
    affordances: Any, *, observed_at: Any = None, location_id: Any = None
) -> list[tuple[str, int]]:
    """The stays this place really offers: at most one for what it is *for*, one for doing nothing.

    Only affordances the place itself lists are ever used.  Two tiers, two rules:

    * the place's own working options (a reading table, a bench, a cafe seat) keep the old
      behaviour exactly -- the preference order picks one, and a place that only offers real
      work (a bookshop with ``READ_HERE``) therefore always offers that work;
    * the idle options (``IDLE_STAY_AFFORDANCES``) travel as their own candidate on a rotating
      turn, because a preference list alone would bury them forever -- they are last by design.
      One turn, one idle flavour, so the offer still has one shape per place rather than a menu
      of three ways to do nothing.

    A place that lists idle affordances *only* gets its rotating one.  A place that lists
    nothing this table knows is an *environment-only* description: it yields no stay at all,
    and this function does **not** invent a ``QUIET_STOP`` on its behalf.  An affordance that
    explicitly claims an execution mechanism must point at a supported, complete stay
    definition; otherwise this raises :class:`StayDefinitionError` instead of silently
    dropping it.  Environment declarations (no ``execution``) are never upgraded or refused.
    """

    declared = {str(item.get("affordance_id") or "").strip(): item
                for item in affordances or [] if isinstance(item, Mapping)}
    activity_definition = _activity_definition_module()
    # 逐条原始声明校验（不按去重后的 dict），重复 ID 也不能把坏定义悄悄盖掉。
    for index, item in enumerate(affordances or []):
        if not isinstance(item, Mapping):
            continue
        if not activity_definition.declares_execution(item):
            continue
        try:
            activity_definition.validate_execution(item)
        except activity_definition.ActivityDefinitionError as error:
            # 固定 code + 安全结构定位；调用方拿到的是这个异常，不是空列表。
            raise StayDefinitionError(
                error.code, _definition_path(location_id, index, error.field)
            ) from None
    offered = set(declared)
    # 旧绑定的两张表必须同时命中；只命中一张是"缺绑定"，不是环境描述。
    for affordance_id in offered:
        in_preference = affordance_id in STAY_DESCRIPTOR_PREFERENCE
        in_seconds = affordance_id in STAY_SECONDS_BY_AFFORDANCE
        if in_preference != in_seconds:
            raise StayDefinitionError("INCOMPLETE_STAY_BINDING")
    known = [affordance_id for affordance_id in STAY_DESCRIPTOR_PREFERENCE
             if affordance_id in offered and affordance_id in STAY_SECONDS_BY_AFFORDANCE]
    working = [item for item in known if item not in IDLE_STAY_AFFORDANCES]
    idle = [item for item in known if item in IDLE_STAY_AFFORDANCES]

    def seconds_for(descriptor: str) -> int:
        """本机会的时长：地点自己声明的建议值优先，缺失时回退既有固定表。"""

        suggestion = (declared.get(descriptor) or {}).get("suggested_seconds")
        if isinstance(suggestion, int) and not isinstance(suggestion, bool) and suggestion > 0:
            return suggestion
        return STAY_SECONDS_BY_AFFORDANCE[descriptor]

    chosen: list[tuple[str, int]] = []
    for item in working:
        chosen.append((item, seconds_for(item)))
    if idle:
        picked = idle[_stay_rotation_turn(observed_at) % len(idle)]
        chosen.append((picked, seconds_for(picked)))
    # 没有可执行的停留定义 = 没有停留候选。禁止自动补 QUIET_STOP / "待一会儿"。
    return chosen


def stay_definition_status(
    affordances: Any, *, observed_at: Any = None, location_id: Any = None
) -> dict[str, Any]:
    """这条地点的 affordance 列表在**定义层**是什么状态。

    ``available``：有可执行的停留定义；``environment_only``：只有环境描述，没有停留
    定义（合法，零候选）；``absent``：连环境描述都没有。显式定义不成立时返回
    ``error`` 和固定错误码——调用方不能只看到空列表而不知道原因。
    """

    try:
        descriptors = _stay_descriptors(
            affordances, observed_at=observed_at, location_id=location_id)
    except StayDefinitionError as error:
        return {"status": "error", "reason_code": error.code, "definitions": []}
    definitions = [
        {"descriptor": descriptor, "seconds": seconds}
        for descriptor, seconds in descriptors
    ]
    if definitions:
        return {"status": "available", "reason_code": None,
                "definitions": definitions}
    if any(isinstance(item, Mapping) for item in affordances or []):
        return {"status": "environment_only",
                "reason_code": "NO_EXECUTABLE_STAY_DEFINITION",
                "definitions": []}
    return {"status": "absent", "reason_code": "NO_AFFORDANCES",
            "definitions": []}


def stay_definition_status_for(
    observation: Mapping[str, Any],
    catalog: Optional[Mapping[str, Any]],
    *,
    observed_at: Any = None,
) -> dict[str, Any]:
    """同一次观察里，她所在地点在定义层的状态（与"闭店/暂不可执行"分开）。"""

    place = _place_of(observation)
    if place is None or not isinstance(catalog, Mapping):
        return {"status": "unavailable", "reason_code": "PLACE_FACT_UNAVAILABLE",
                "definitions": []}
    if str(catalog.get("status") or "") != "available":
        return {"status": "unavailable", "reason_code": "CATALOG_UNAVAILABLE",
                "definitions": []}
    entry: Mapping[str, Any] = {}
    for item in catalog.get("opportunities") or []:
        if isinstance(item, Mapping) and str(item.get("location_id")) == place:
            entry = item
            break
    if not entry:
        return {"status": "absent", "reason_code": "PLACE_NOT_IN_CATALOG",
                "definitions": []}
    # 第一层先于第二层：定义不成立时，即使地点闭店也必须报定义错误，
    # 不能被 closed 短路掩盖；合法定义在闭店时仍按第二层返回 closed。
    status = stay_definition_status(
        entry.get("affordances"), observed_at=observed_at, location_id=place)
    if str(status.get("status") or "") == "error":
        return status
    if str((entry.get("availability") or {}).get("status") or "") != "OPEN":
        return {"status": "closed", "reason_code": "PLACE_NOT_OPEN_NOW",
                "definitions": []}
    return status


def observed_stay_opportunities(
    observation: Mapping[str, Any],
    *,
    catalog: Optional[Mapping[str, Any]],
    occurred_at: str,
    observed_at: str,
    valid_for_seconds: int = DEFAULT_VALIDITY_SECONDS,
) -> list[dict[str, Any]]:
    """The non-productive stays this place really offers right now, if it is really open.

    What they are: Life-side bounded stays whose only World-side inputs are (a) the canonical
    observation that she is in this place right now and (b) the W catalog's own opening /
    affordance facts for it.  What they are not: a World verb, a resting effect, a nutrition
    fact, or a claim that something was produced.

    Usually one offer; two when the place offers both something it is *for* and something to do
    nothing in (see ``_stay_descriptors``).  Either way every descriptor comes from the place's
    own affordance list, so a place that does not declare an idle option never gets one.

    Failure is closed: an unreadable catalog (or a place with no OPEN availability fact)
    yields no stay at all, so a place whose opening state is unknown never becomes somewhere
    she is invited to settle in for ten minutes.

    Definition failure is *not* silent: an affordance that explicitly claims an execution
    mechanism but is unsupported or incomplete raises :class:`StayDefinitionError` with a
    fixed code.  A place that only describes its environment (no execution claim and no
    legacy stay binding) yields zero stays without inventing a ``QUIET_STOP``.
    """

    place = _place_of(observation)
    revision = _revision_of(observation)
    world_id = observation.get("world_id")
    if place is None or revision is None or not isinstance(catalog, Mapping):
        return []
    if str(catalog.get("status") or "") != "available":
        return []
    entry: Mapping[str, Any] = {}
    for item in catalog.get("opportunities") or []:
        if isinstance(item, Mapping) and str(item.get("location_id")) == place:
            entry = item
            break
    if not entry:
        return []
    # 第一层先于第二层：显式坏定义即使地点闭店也要报错，不能被 closed 短路。
    descriptors = _stay_descriptors(
        entry.get("affordances"), observed_at=observed_at, location_id=place)
    if str((entry.get("availability") or {}).get("status") or "") != "OPEN":
        return []

    valid_from, valid_until = _validity_window(observed_at, valid_for_seconds)
    occurred = ag0._require_iso(occurred_at, "occurred_at")
    return [{
        "opportunity_id": "wopp:stay:%s:r%s:%s" % (place, revision, descriptor),
        "perception_ref": "percept:gateway-snapshot:%s:r%s" % (place, revision),
        "world_revision_ref": "wrev:%s:r%s" % (world_id or "world", revision),
        "target_refs": [stay_target(place, descriptor, seconds)],
        "valid_from": valid_from,
        "valid_until": valid_until,
        "occurred_at": occurred,
        "observed_at": valid_from,
        "observed": True,
        "status": "AVAILABLE",
        "revision": 1,
        "subject_id": ag0.GLOBAL_SUBJECT_ID,
        "location_ref": "place:%s" % place,
    } for descriptor, seconds in descriptors]


def read_location_catalog(
    environment: Optional[Mapping[str, str]],
    now: Any,
    *,
    hermes_home: Optional[str],
) -> dict[str, Any]:
    """The W opportunity catalog, as facts about places -- or an honest absence.

    Read-only and failure tolerant by design: an unreadable catalog means "no place
    facts this round", never a guessed availability.  When the package failed to load
    because an explicit activity definition is invalid, the same fixed ``code`` and
    safe structural ``path`` are kept on the unavailable envelope; ordinary read
    failures stay reason-only, never dressed up as definition errors.  The catalog is
    never a grant; every entry it returns carries ``action_grant: false`` of its own.
    """

    try:
        import world_daily_opportunities as wdo
    except Exception as error:  # noqa: BLE001 - a missing module is not an opportunity
        return {"status": "unavailable", "reason_code": "CATALOG_MODULE_UNAVAILABLE",
                "detail": "%s: %s" % (type(error).__name__, error), "opportunities": []}
    try:
        catalog = wdo.location_opportunity_catalog(
            environment, now, hermes_home=hermes_home)
    except Exception as error:  # noqa: BLE001 - same rule: report, never invent
        return {"status": "unavailable", "reason_code": "CATALOG_READ_FAILED",
                "detail": "%s: %s" % (type(error).__name__, error), "opportunities": []}
    if not isinstance(catalog, Mapping):
        return {"status": "unavailable", "reason_code": "CATALOG_SHAPE_INVALID",
                "opportunities": []}
    result = dict(catalog)
    definition_error = None
    if str(result.get("status") or "") == "unavailable":
        definition_error = _safe_definition_error(
            result.get("code"), result.get("path"))
    # 定义错误才带 code/path；两条都先摘掉，非法或越界的值不进入调用方视野。
    result.pop("code", None)
    result.pop("path", None)
    if definition_error is not None:
        code, safe_path = definition_error
        result["code"] = code
        if safe_path is not None:
            result["path"] = safe_path
    return result


def read_store_projection(
    environment: Optional[Mapping[str, str]],
    now: Any,
    *,
    hermes_home: Optional[str],
) -> dict[str, Any]:
    """The physical counter's own read-only projection, or an honest absence.

    The economy owner answers with the store's opening state, whether she is at the
    counter, the catalogue it really sells and each item's affordability.  An unreadable
    owner means "no store facts this round" -- never an assumed shelf and never an
    assumed balance.  Nothing here writes, reserves or charges anything.
    """

    try:
        import world_property_economy as economy
    except Exception as error:  # noqa: BLE001 - a missing module is not a store fact
        return {"status": "unavailable", "reason_code": "STORE_MODULE_UNAVAILABLE",
                "detail": "%s: %s" % (type(error).__name__, error), "catalog": []}
    try:
        projection = economy.store_shop_projection(
            hermes_home=hermes_home, environment=environment, now=now)
    except Exception as error:  # noqa: BLE001 - same rule: report, never invent
        return {"status": "unavailable", "reason_code": "STORE_READ_FAILED",
                "detail": "%s: %s" % (type(error).__name__, error), "catalog": []}
    if not isinstance(projection, Mapping):
        return {"status": "unavailable", "reason_code": "STORE_SHAPE_INVALID",
                "catalog": []}
    return dict(projection)


def observed_purchase_opportunities(
    observation: Mapping[str, Any],
    *,
    projection: Optional[Mapping[str, Any]],
    occurred_at: str,
    observed_at: str,
    valid_for_seconds: int = DEFAULT_VALIDITY_SECONDS,
) -> list[dict[str, Any]]:
    """One opportunity per item the economy owner says is really buyable *here, now*.

    The facts are the owner's own: it must report the counter available (open) and
    ``purchase_available`` true, the body must really be in the store's place, and each
    item must be in the counter's catalogue and affordable.  Nothing is inferred from a
    place title: a shop that only has ``BROWSE_STORE`` yields no purchase here, and a
    closed counter or a different place yields none at all.

    The target carries the **catalogue id only**.  Price, balance and ownership are
    re-read by the owner inside the purchase itself, so nothing in a candidate can name
    a price or a transaction.
    """

    place = _place_of(observation)
    revision = _revision_of(observation)
    world_id = observation.get("world_id")
    if place is None or revision is None or not isinstance(projection, Mapping):
        return []
    if str(projection.get("status") or "") != "available":
        return []
    if projection.get("purchase_available") is not True:
        return []
    store_id = str(projection.get("store_id") or "").strip()
    if not store_id or place != store_id:
        return []

    valid_from, valid_until = _validity_window(observed_at, valid_for_seconds)
    occurred = ag0._require_iso(occurred_at, "occurred_at")
    opportunities: list[dict[str, Any]] = []
    for item in projection.get("catalog") or []:
        if not isinstance(item, Mapping):
            continue
        item_id = str(item.get("catalog_item_id") or "").strip()
        price = item.get("price")
        if not item_id:
            continue
        if isinstance(price, bool) or not isinstance(price, int) or price < 0:
            continue
        if item.get("affordable") is not True:
            # "Cannot pay for it right now" is not an offer.  The owner re-checks the
            # balance under its own lock; this only keeps an unpayable shelf off the list.
            continue
        if item.get("suppress_reason_code") == ALREADY_OWNED_SUPPRESSED:
            # 18 章 §二 Day2（A/B 闭环）：经济 owner 自己判定"已拥有同款"的固定家具
            # 不再生成购买候选——证据是 owner 投影里的持有事实（OWNED 实例计数），
            # 这里只过滤、不另算第二份所有权。
            continue
        opportunities.append({
            "opportunity_id": "wopp:buy:%s:r%s:%s" % (place, revision, item_id),
            "perception_ref": "percept:world-property-economy:%s:r%s" % (place, revision),
            "world_revision_ref": "wrev:%s:r%s" % (world_id or "world", revision),
            "target_refs": ["%s%s" % (PURCHASE_REF_PREFIX, item_id)],
            "valid_from": valid_from,
            "valid_until": valid_until,
            "occurred_at": occurred,
            "observed_at": valid_from,
            "observed": True,
            "status": "AVAILABLE",
            "revision": 1,
            "subject_id": ag0.GLOBAL_SUBJECT_ID,
            "location_ref": "place:%s" % place,
        })
    return opportunities


def opportunity_view(
    opportunities: list[dict[str, Any]],
    catalog: Optional[Mapping[str, Any]] = None,
    available_actions: Optional[Mapping[str, Any]] = None,
    purchase_facts: Optional[Mapping[str, Mapping[str, Any]]] = None,
    reason_hints: Optional[Mapping[str, str]] = None,
) -> list[dict[str, Any]]:
    """A richer, non-AG-0 view of the same opportunities, for reports and steps.

    AG-0's ``ObservedWorldOpportunity`` accepts exactly its declared fields, so the
    extra facts the Life core wants (which owner verb, how far, is the place really
    open, is the place fact uncertain, and -- for a body action -- the decoded
    parameters and the owner's own per-direction verdict) are kept *here* instead of
    being smuggled into the candidate contract.  Nothing in this view authorizes
    anything.
    """

    by_location: dict[str, Mapping[str, Any]] = {}
    for item in (catalog or {}).get("opportunities") or []:
        if isinstance(item, Mapping) and item.get("location_id"):
            by_location[str(item["location_id"])] = item

    view: list[dict[str, Any]] = []
    for item in opportunities or []:
        if not isinstance(item, Mapping):
            continue
        targets = [str(ref) for ref in (item.get("target_refs") or [])]
        target = targets[0] if targets else ""
        stay = parse_stay_target(target)
        affordance = None
        stay_seconds = None
        #: 停留的描述性标题，只从**同一地点**同一 descriptor 的 affordance 取；缺失即 None，
        #: 不跨地点拼凑，也不写进 target（target 仍只装计划本身）。
        stay_title = None
        #: 同一地点同一 descriptor 显式声明的执行机制；只有结构完整时透出，且只作
        #: 定义信息展示，不是授权。
        stay_execution = None
        body = None
        params: Optional[dict[str, Any]] = None
        pose = None
        orientation = None
        rest_state = None
        rest_mode = None
        invalid_body_reason: Optional[str] = None
        daily: Optional[dict[str, Any]] = None
        invalid_daily_reason: Optional[str] = None
        destination: Optional[dict[str, Any]] = None
        fixture_state: Optional[str] = None
        purchase: Optional[dict[str, Any]] = None
        if stay is not None:
            kind = STAY_VERB
            object_id = None
            affordance = stay["descriptor"]
            stay_seconds = stay["seconds"]
            source = "life_daily_activity_from_world_observation"
            location = stay["place"]
        elif target.startswith(BODY_ACTION_REF_PREFIX):
            # W: a body action's *whole* parameter set is inside the ref, so the view
            # reports the decoded parameters (never a re-guess) and the owner's own
            # availability for exactly this direction.
            location = str(item.get("location_ref") or "")[len(PLACE_REF_PREFIX):]
            try:
                body = body_actions.parse_body_action_target(target)
            except body_actions.BodyActionTargetError as error:
                # A malformed encoding is reported as such; it is not dropped silently
                # and it is certainly not turned into some other verb.
                kind = "INVALID_BODY_ACTION"
                object_id = None
                source = "life_body_action_targets"
                invalid_body_reason = str(error) or "BODY_ACTION_TARGET_INVALID"
            else:
                kind = str(body["verb"])
                object_id = None
                source = "world_gateway_available_actions"
                params = dict(body["params"])
                pose = params.get("pose")
                orientation = params.get("orientation")
                rest_state = params.get("state")
                rest_mode = params.get("mode")
        elif target.startswith(DAILY_ACTION_REF_PREFIX):
            # v0.4 B: a hand action's entire parameter set is inside the ref, so the view
            # reports the decoded parameters (the object, the hand slot, the destination)
            # and never re-derives any of them from another read.
            location = str(item.get("location_ref") or "")[len(PLACE_REF_PREFIX):]
            try:
                daily = daily_actions.parse_daily_action_target(target)
            except daily_actions.DailyActionTargetError as error:
                # A malformed encoding is reported, never silently dropped and never
                # turned into some other verb.
                kind = "INVALID_DAILY_ACTION"
                object_id = None
                source = "life_daily_action_targets"
                invalid_daily_reason = str(error) or "DAILY_ACTION_TARGET_INVALID"
            else:
                kind = str(daily["verb"])
                object_id = str(daily["object_id"])
                params = dict(daily["params"])
                destination = (dict(params["target"])
                               if kind in (PLACE_VERB, ARRANGE_VERB) else None)
                fixture_state = (str(params["state"])
                                 if kind == TOGGLE_FIXTURE_VERB else None)
                source = ("world_gateway_available_actions"
                          if kind in (PICK_UP_VERB, ARRANGE_VERB, TOGGLE_FIXTURE_VERB)
                          else "world_gateway_snapshot")
        elif target.startswith(OBJECT_REF_PREFIX):
            kind = CONSUME_VERB
            object_id = target[len(OBJECT_REF_PREFIX):]
            source = "world_gateway_available_actions"
            location = str(item.get("location_ref") or "")[len(PLACE_REF_PREFIX):]
        elif target.startswith(PURCHASE_REF_PREFIX):
            kind = PURCHASE_VERB
            item_id = target[len(PURCHASE_REF_PREFIX):]
            object_id = item_id
            source = "world_property_economy.store_shop_projection"
            location = str(item.get("location_ref") or "")[len(PLACE_REF_PREFIX):]
            purchase = dict((purchase_facts or {}).get(item_id) or {})
        elif target.startswith(PLACE_REF_PREFIX):
            kind = MOVE_VERB
            object_id = None
            source = "world_gateway_selector_action_space"
            location = target[len(PLACE_REF_PREFIX):]
        else:  # pragma: no cover - the emitters above only produce these
            continue
        place_fact = by_location.get(location) or {}
        availability = (place_fact.get("availability") or {}) if place_fact else {}
        reachability = (place_fact.get("reachability") or {}) if place_fact else {}
        if stay is not None:
            for declared in place_fact.get("affordances") or []:
                if not isinstance(declared, Mapping):
                    continue
                if str(declared.get("affordance_id") or "") != str(affordance):
                    continue
                raw_title = declared.get("title")
                if isinstance(raw_title, str) and raw_title.strip():
                    stay_title = " ".join(raw_title.split())[:80]
                activity_definition = _activity_definition_module()
                if activity_definition.declares_execution(declared):
                    try:
                        stay_execution = activity_definition.validate_execution(
                            declared)
                    except activity_definition.ActivityDefinitionError:
                        stay_execution = None
                break
        if body:
            blocked_reason = _body_blocked_reason(body, available_actions)
        elif daily:
            blocked_reason = _daily_blocked_reason(daily, available_actions)
        else:
            blocked_reason = invalid_body_reason or invalid_daily_reason
        completion = (daily_actions.daily_action_completion(target) if daily else None)
        view.append({
            "opportunity_id": item.get("opportunity_id"),
            "kind": kind,
            "verb": kind,
            "target_ref": target,
            "object_id": object_id,
            "affordance": affordance,
            "stay_seconds": stay_seconds,
            "title": stay_title,
            # 显式执行机制的定义信息（只有世界包写了才有）；不是执行授权。
            "execution": stay_execution,
            "location_id": location,
            "subject_id": item.get("subject_id"),
            "source": source,
            "perception_ref": item.get("perception_ref"),
            "world_revision_ref": item.get("world_revision_ref"),
            "observed": bool(item.get("observed")),
            "valid_from": item.get("valid_from"),
            "valid_until": item.get("valid_until"),
            "occurred_at": item.get("occurred_at"),
            # ``UNKNOWN`` here means the owner's own rhythm read failed; it is reported
            # as uncertainty and never upgraded to "open" or "closed".
            "availability": availability.get("status") or "NOT_READ",
            "availability_reason": availability.get("reason_code"),
            "walk_seconds": reachability.get("walk_seconds"),
            "place_observed": (place_fact.get("observed") or {}).get("status")
                              if place_fact else None,
            "action_grant": False,
            # W: body-action facts (``None`` for every other namespace, so a report can
            # read one shape and still tell the difference).
            "params": params,
            "pose": pose,
            "orientation": orientation,
            "rest_state": rest_state,
            "rest_mode": rest_mode,
            "fact_key": (body["fact_key"] if body
                         else completion["fact_key"] if completion else None),
            "expected_value": (body["expected_value"] if body
                               else completion["expected_value"] if completion else None),
            # v0.4 B: hand-action facts (``None`` for every other namespace).  The
            # destination is the *decoded* placement target, and the slot the owner's own
            # candidate offered -- never a value this module re-derived.
            "destination": destination,
            "slot_id": (params or {}).get("slot_id") if daily else None,
            # v0.4 room-living: the absolute fixture state a TOGGLE target names (``None``
            # for every other namespace), so a report reads the owner verb's own parameter.
            "fixture_state": fixture_state,
            # 0.7: the counter facts for a ``buy:`` target (name/price/currency/balance and
            # the owner's own delivery state).  ``None`` for every other namespace.
            "purchase": purchase,
            "owner_blocked_reason": blocked_reason,
        })
        hint_lookup = reason_hints if isinstance(reason_hints, Mapping) else {}
        hint_raw = (
            item.get("reason_hint_cn")
            or hint_lookup.get(str(item.get("opportunity_id") or ""))
            or hint_lookup.get(target)
        )
        if isinstance(hint_raw, str) and hint_raw.strip():
            view[-1]["reason_hint_cn"] = " ".join(hint_raw.split())[:96]
    return view


def _body_blocked_reason(
    body: Optional[Mapping[str, Any]],
    available_actions: Optional[Mapping[str, Any]],
) -> Optional[str]:
    """The owner's own reason for the *direction* this body action would take.

    REST publishes a per-direction verdict (``start_blocked_reason`` /
    ``stop_blocked_reason``) precisely so "stop what is not open" and "start when it is
    not needed" are never reported as one thing; POSE publishes a single
    ``blocked_reason``.  An entry the gateway does not expose yields ``None`` -- "no
    reason reported" is not "no reason".
    """

    if not isinstance(body, Mapping) or not isinstance(available_actions, Mapping):
        return None
    entry: Mapping[str, Any] = {}
    for candidate in available_actions.get("actions") or []:
        if isinstance(candidate, Mapping) and candidate.get("verb") == body.get("verb"):
            entry = candidate
            break
    if not entry:
        return None
    if str(body.get("verb")) == REST_VERB:
        state = str((body.get("params") or {}).get("state") or "")
        key = "stop_blocked_reason" if state == "stop" else "start_blocked_reason"
        return entry.get(key)
    return entry.get("blocked_reason")


def _daily_blocked_reason(
    daily: Optional[Mapping[str, Any]],
    available_actions: Optional[Mapping[str, Any]],
) -> Optional[str]:
    """The owner's own reason it will not take this hand action right now.

    ``PICK_UP`` publishes ``blocked_reason`` (hands occupied, pose cannot manipulate, no
    takeable object here) and so does ``PLACE`` (nothing held).  An entry the gateway does
    not expose yields ``None`` -- "no reason reported" is not "no reason".
    """

    if not isinstance(daily, Mapping) or not isinstance(available_actions, Mapping):
        return None
    for candidate in available_actions.get("actions") or []:
        if isinstance(candidate, Mapping) and candidate.get("verb") == daily.get("verb"):
            return candidate.get("blocked_reason")
    return None


#: Poses in which the owner will let a hand act at all
#: (``world_body_substrate._HANDS_USABLE_POSES``, copied: the owner re-checks it).
HANDS_USABLE_POSES = ("standing", "seated")

#: The presentation order ``daily_life_context`` groups its opportunities in: what she can
#: do with her hands right here, then somewhere to be, then something to use up, then
#: where she could go, then posture.  This is a *presentation* order, documented on every
#: payload as ``order_basis: kind_group_presentation``; it is not a ranking, not a score
#: and not a default pick, and within a group the observation's own emission order holds.
CONTEXT_KIND_ORDER = (
    CONSUME_VERB, TOILET_VERB, PICK_UP_VERB, PLACE_VERB, ARRANGE_VERB,
    TOGGLE_FIXTURE_VERB, STAY_VERB, PURCHASE_VERB, MOVE_VERB, POSE_VERB, REST_VERB,
)
#: How many unknowns one context may name (a bounded list, not a log).
MAX_CONTEXT_UNKNOWNS = 8

#: v0.6 ambient background: the World's own per-day cap is 3, and this background never
#: enlarges it.  Only current, location-relevant facts the World itself projected are kept.
MAX_AMBIENT_CONTEXT_EVENTS = 3
AMBIENT_CONTEXT_SOURCE = "world_living_skeleton.observe_world.ambient_events"
#: The World's own travel switch (``world_travel_delivery.TRAVEL_FEATURE_ENV``).  It is
#: written off only for the ambient read, so this background read never realizes an
#: arrival or writes the World state from the Life process.
AMBIENT_READ_TRAVEL_ENV = "CHIYO_WORLD_TRAVEL_TIME_ENABLED"


def _context_text(value: Any, limit: int) -> str:
    """One whitespace-collapsed, length-capped string (never a paragraph)."""

    return " ".join(str(value if value is not None else "").split())[:limit]


def _ambient_instant(value: Any) -> Any:
    """One iso instant for the same-source check; an unreadable one is UNKNOWN."""

    from datetime import datetime, timezone

    text = str(value or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _ambient_same_source_conflict(
    observation: Mapping[str, Any],
    snapshot: Any,
    events: Any,
) -> Optional[str]:
    """The named fact that contradicts this round's own snapshot, or ``None``.

    The background read is a *second* read.  It must not turn another world, another
    place or an already-expired record into "what is happening here now".  Only facts
    the two reads already report are compared; a side that did not report one is
    ``UNKNOWN`` and is never treated as a contradiction.  Nothing new is derived from
    the world package -- the World still owns the time window, the place filter and
    the event set.
    """

    if not isinstance(observation, Mapping):
        return None
    snap = snapshot if isinstance(snapshot, Mapping) else {}
    observed_world = str(observation.get("world_id") or "").strip()
    snapshot_world = str(snap.get("world_id") or "").strip()
    if observed_world and snapshot_world and observed_world != snapshot_world:
        return "AMBIENT_WORLD_MISMATCH"

    current = observation.get("current_location")
    snapshot_location = snap.get("location")
    if isinstance(current, Mapping) and isinstance(snapshot_location, Mapping):
        node = str(current.get("location_id") or "").strip()
        observed_place = node.split("/", 1)[0] if node else ""
        snapshot_place = str(snapshot_location.get("place_id") or "").strip()
        if observed_place and snapshot_place and observed_place != snapshot_place:
            return "AMBIENT_LOCATION_MISMATCH"
        # An area is only comparable when the World named an area node.  A place node
        # carries the place's *default* area, which is not the state's current area
        # (entrance vs default differ), so comparing it would invent a contradiction.
        observed_area = node.split("/", 1)[1] if "/" in node else ""
        snapshot_area = str(snapshot_location.get("area_id") or "").strip()
        if observed_area and snapshot_area and observed_area != snapshot_area:
            return "AMBIENT_LOCATION_MISMATCH"

    local_time = _ambient_instant(observation.get("local_time"))
    if local_time is not None:
        for event in events or []:
            if not isinstance(event, Mapping):
                continue
            start = _ambient_instant(event.get("valid_from"))
            end = _ambient_instant(event.get("valid_until"))
            if start is None or end is None:
                continue
            if not (start <= local_time < end):
                return "AMBIENT_EVENT_WINDOW_MISMATCH"
    return None


def _ambient_row(event: Any) -> Optional[dict[str, Any]]:
    """One World-projected ambient fact, bounded to the public fields only.

    The World owns the event id, the place, the validity window and the summary; this
    copies those facts unchanged (the summary stays *exact* so a reader can still
    recognise the same public template) and drops anything else.
    """

    if not isinstance(event, Mapping):
        return None
    event_id = _context_text(event.get("event_id"), 160)
    payload = event.get("payload")
    # The summary is copied verbatim (strip only) so it still matches the World's public
    # template exactly; it is public world text, never private material.
    summary = str(
        (payload.get("summary") if isinstance(payload, Mapping) else "") or "").strip()[:240]
    if not event_id or not summary:
        return None
    row: dict[str, Any] = {"event_id": event_id, "summary": summary}
    for key, limit in (("event_type", 64), ("location_id", 160),
                       ("local_date", 16), ("valid_from", 40), ("valid_until", 40)):
        text = _context_text(event.get(key), limit)
        if text:
            row[key] = text
    return row


def _object_labels(observation: Mapping[str, Any]) -> dict[str, str]:
    """``{object_id: label}`` from the snapshot's own label join (never a raw id)."""

    labels: dict[str, str] = {}
    for item in observation.get("visible_objects") or []:
        if isinstance(item, Mapping) and item.get("object_id"):
            labels[str(item["object_id"])] = str(item.get("label") or item["object_id"])
    return labels


def _place_names(catalog: Mapping[str, Any]) -> dict[str, str]:
    """``{location_id: name}`` for the places the World itself named this round."""

    names: dict[str, str] = {}
    for item in catalog.get("opportunities") or []:
        if isinstance(item, Mapping) and item.get("location_id") and item.get("name"):
            names[str(item["location_id"])] = str(item["name"])
    return names


def _place_facts(catalog: Mapping[str, Any], place_id: str) -> Mapping[str, Any]:
    """The catalog's own entry for one place, or ``{}`` when it has none."""

    for item in catalog.get("opportunities") or []:
        if isinstance(item, Mapping) and str(item.get("location_id")) == place_id:
            return item
    return {}


def _area_name(place_facts: Mapping[str, Any], area_id: str) -> Optional[str]:
    """The name of the indoor area she is in, when the World names it."""

    for area in place_facts.get("indoor_areas") or []:
        if isinstance(area, Mapping) and str(area.get("area_id")) == area_id:
            return str(area.get("name") or "") or None
    observed_area = (place_facts.get("observed") or {}).get("area") or {}
    if isinstance(observed_area, Mapping) and str(observed_area.get("area_id")) == area_id:
        return str(observed_area.get("name") or "") or None
    return None


def _body_context(observation: Mapping[str, Any]) -> dict[str, Any]:
    """Her own postures and hands, as the canonical snapshot reports them.

    Only her *own* facts: the pose the owner holds, the orientation it holds, which hand
    holds which object, and which hands are free.  ``hands_usable`` is the owner's own
    rule (``_HANDS_USABLE_POSES``) restated so a caller never has to guess whether a hand
    action is physically possible -- it grants nothing.
    """

    pose = str(observation.get("pose") or "").strip() or None
    orientation = str(observation.get("orientation") or "").strip() or None
    hands_raw = observation.get("hands") if isinstance(observation.get("hands"), Mapping) else {}
    # A *list* of the hands that really hold something, in the owner's own slot order: the
    # shape a prompt renders, and the only hands that can honestly be named.  An empty hand
    # is not listed at all -- ``free_slots`` says which ones those are.
    hands: list[dict[str, Any]] = []
    free_slots: list[str] = []
    holding: list[str] = []
    for slot in daily_actions.HAND_SLOTS:
        object_id = str(hands_raw.get(slot) or "").strip()
        if object_id:
            hands.append({"slot": slot, "object_id": object_id})
            holding.append(object_id)
        else:
            free_slots.append(slot)
    needs_raw = observation.get("lived_needs")
    needs = ({str(key): str(value) for key, value in needs_raw.items()}
             if isinstance(needs_raw, Mapping) else None)
    return {
        "pose": pose,
        "orientation": orientation,
        "hands": hands,
        # ``False`` when the snapshot did not say: "unknown hands" is not "empty hands".
        "hands_known": isinstance(observation.get("hands"), Mapping),
        "free_slots": free_slots,
        "holding": holding,
        # ``None`` when the pose is unknown: an unreadable posture is not "cannot hold".
        "hands_usable": (pose in HANDS_USABLE_POSES) if pose else None,
        # Optional virtual lived needs, as owner bands only.  ``None`` for every
        # instance without an explicit profile (old roots unchanged).
        "needs": needs,
        "needs_known": needs is not None,
    }


def _takeable_here(available_actions: Optional[Mapping[str, Any]]) -> set[str]:
    """The objects the owner says are takeable *and* in this place right now."""

    if not isinstance(available_actions, Mapping):
        return set()
    for entry in available_actions.get("actions") or []:
        if not isinstance(entry, Mapping) or entry.get("verb") != PICK_UP_VERB:
            continue
        if entry.get("available") is not True:
            return set()
        return {str((offered or {}).get("object_id") or "").strip()
                for offered in entry.get("candidates") or []
                if isinstance(offered, Mapping) and offered.get("object_id")}
    return set()


def _nearby_objects(
    observation: Mapping[str, Any],
    body: Mapping[str, Any],
    labels: Mapping[str, str],
    takeable_ids: set[str],
) -> list[dict[str, Any]]:
    """What is really within reach here: the held objects and the takeable ones.

    The gateway's snapshot lists *every* placement in the World without saying where it
    is, so an object she is not holding and the owner does not report as takeable-here
    cannot honestly be called nearby.  Those objects are named in ``unknowns`` instead.
    """

    placements = {str(item.get("object_id")): item
                  for item in observation.get("visible_objects") or []
                  if isinstance(item, Mapping) and item.get("object_id")}
    nearby: list[dict[str, Any]] = []
    for held in body.get("hands") or []:
        if not isinstance(held, Mapping):
            continue
        slot = str(held.get("slot") or "")
        object_id = str(held.get("object_id") or "")
        if not object_id:
            continue
        entry = placements.get(object_id) or {}
        nearby.append({
            "object_id": object_id,
            "label": labels.get(object_id) or object_id,
            "kind": entry.get("kind"),
            "in_hand": True,
            "slot_id": slot,
            "takeable": False,
        })
    for object_id in sorted(takeable_ids):
        if any(item["object_id"] == object_id for item in nearby):
            continue
        entry = placements.get(object_id) or {}
        nearby.append({
            "object_id": object_id,
            "label": labels.get(object_id) or object_id,
            "kind": entry.get("kind"),
            "in_hand": False,
            "slot_id": None,
            "takeable": True,
        })
    return nearby


def _context_order_key(entry: Mapping[str, Any]) -> int:
    verb = str(entry.get("verb") or "")
    try:
        return CONTEXT_KIND_ORDER.index(verb)
    except ValueError:
        return len(CONTEXT_KIND_ORDER)


def _duration_seconds(entry: Mapping[str, Any]) -> tuple[Optional[float], Optional[str]]:
    """How long this action takes, by the owner's own numbers, and where the number is from.

    A route duration comes from the world package's own edges, a stay's from the
    descriptor the catalog selected, a hand movement's and a posture's from the Body's own
    load model (``body_consequence``).  ``REST`` is a session, not a duration, so it
    answers ``None`` -- an unknown number, never a made-up one.
    """

    verb = str(entry.get("verb") or "")
    if verb == MOVE_VERB:
        seconds = entry.get("walk_seconds")
        if isinstance(seconds, (int, float)) and not isinstance(seconds, bool):
            return float(seconds), DURATION_SOURCE_ROUTE
        return None, None
    if verb == STAY_VERB:
        seconds = entry.get("stay_seconds")
        if isinstance(seconds, (int, float)) and not isinstance(seconds, bool):
            return float(seconds), "stay_descriptor"
        return None, None
    if verb in (PICK_UP_VERB, PLACE_VERB, CONSUME_VERB, TOILET_VERB):
        return HAND_MANIPULATION_SECONDS, DURATION_SOURCE_OWNER
    if verb == PURCHASE_VERB:
        # 柜台购买本身是一次瞬时 owner 记账；交付时窗是经济 owner 的单独事实，
        # 由 ``_context_opportunity`` 的 purchase facts 报出，不混进这段时长。
        return PURCHASE_SECONDS, DURATION_SOURCE_OWNER
    if verb == POSE_VERB:
        return POSTURE_SECONDS, DURATION_SOURCE_OWNER
    return None, None


def _hand_label(slot: Any) -> Optional[str]:
    return {"left_hand": "左手", "right_hand": "右手"}.get(str(slot or ""))


def _preparation_text(verb: str, facts: Mapping[str, Any]) -> str:
    """One short sentence of preparation facts (what is already true), never a checklist."""

    if verb == PICK_UP_VERB:
        slots = "、".join(_hand_label(slot) or str(slot)
                         for slot in facts.get("free_slots") or []) or "没有空手"
        usable = facts.get("hands_usable")
        ability = ("这个姿势能动手" if usable is True
                   else "这个姿势动不了手" if usable is False
                   else "姿势是否能动还不确定")
        return "%s；%s" % (slots, ability)
    if verb == PLACE_VERB:
        destination = facts.get("destination") or {}
        kind = str(destination.get("kind") or "")
        if kind == daily_actions.PLACEMENT_SURFACE:
            return "放的地方是一个真实存在的台面"
        if kind == daily_actions.PLACEMENT_ROOM:
            return "放的地方就是现在这间房"
        return "放的地方这一轮没有读到"
    if verb == PURCHASE_VERB:
        price = facts.get("price")
        currency = facts.get("currency") or ""
        balance = facts.get("balance")
        if price is None:
            return "价格这一轮没有读到"
        return ("这件 %s %s；现在余额 %s %s" % (price, currency, balance, currency)
                if balance is not None else "这件 %s %s" % (price, currency))
    if verb == MOVE_VERB:
        return "走过去这一趟要 %s" % facts.get("walk_seconds")
    if verb == STAY_VERB:
        return "这个地方现在%s" % (facts.get("place_availability") or "未知")
    if verb == POSE_VERB:
        return "现在的姿势是%s" % (facts.get("current_pose") or "未知")
    return ""


def _nearby_places(
    catalog: Mapping[str, Any],
    *,
    place_id: str,
    place_names: Mapping[str, str],
    limit: int,
) -> list[dict[str, Any]]:
    """The nearby *places* the World itself lists, here first, then by its own walk time.

    Only what the catalog projects: a name, whether the place is open, how long walking
    there takes, and whether she actually sees it from here.  A place the catalog does not
    list is absent rather than guessed, and sorting is by the World's own numbers -- the
    list is not a ranking of what she should do.
    """

    entries = [item for item in catalog.get("opportunities") or []
               if isinstance(item, Mapping) and item.get("location_id")]
    def _walk(item: Mapping[str, Any]) -> tuple[Any, Any, str]:
        seconds = (item.get("reachability") or {}).get("walk_seconds")
        return (seconds in (None, ""), seconds if seconds not in (None, "") else 0,
                str(item.get("location_id")))

    here = [item for item in entries if str(item.get("location_id")) == place_id]
    elsewhere = sorted(
        (item for item in entries if str(item.get("location_id")) != place_id),
        key=_walk)
    places: list[dict[str, Any]] = []
    for item in here + elsewhere:
        location_id = str(item.get("location_id"))
        availability = item.get("availability") or {}
        reachability = item.get("reachability") or {}
        places.append({
            "place_id": location_id,
            "name": place_names.get(location_id),
            "kind": item.get("kind"),
            "availability": availability.get("status"),
            "availability_reason": availability.get("reason_code"),
            "reach": reachability.get("status"),
            "walk_seconds": reachability.get("walk_seconds"),
            "observed": (item.get("observed") or {}).get("status"),
            "here": bool(item.get("here")),
        })
        if len(places) >= limit:
            break
    return places


def _preparation(
    entry: Mapping[str, Any],
    body: Mapping[str, Any],
    destination: Optional[Mapping[str, Any]],
) -> dict[str, Any]:
    """What has to be true (and already is true) for this action to go through.

    Only facts: which hands are free, whether the pose can manipulate at all, what is
    already in hand, and which real destination the target names.  It is not a checklist
    and it does not say what to do first.
    """

    verb = str(entry.get("verb") or "")
    if verb == PICK_UP_VERB:
        return {
            "free_slots": list(body.get("free_slots") or []),
            "slot_id": entry.get("slot_id"),
            "hands_usable": body.get("hands_usable"),
        }
    if verb == PLACE_VERB:
        return {
            "holding": list(body.get("holding") or []),
            "destination": dict(destination or {}),
        }
    if verb == MOVE_VERB:
        return {"walk_seconds": entry.get("walk_seconds"),
                "availability": entry.get("availability")}
    if verb == STAY_VERB:
        return {"place_availability": entry.get("availability"),
                "affordance": entry.get("affordance")}
    if verb == PURCHASE_VERB:
        purchase = entry.get("purchase") or {}
        return {"store": entry.get("location_id"),
                "price": purchase.get("price"),
                "currency": purchase.get("currency"),
                "balance": purchase.get("balance"),
                "delivery_status": purchase.get("delivery_status")}
    if verb == POSE_VERB:
        return {"current_pose": body.get("pose")}
    return {}


def _context_opportunity(
    entry: Mapping[str, Any],
    *,
    labels: Mapping[str, str],
    place_names: Mapping[str, str],
    place_id: str,
    area_name: Optional[str],
    surface_labels: Mapping[str, str],
    body: Mapping[str, Any],
) -> dict[str, Any]:
    """One opportunity as the cognition layer should see it: a name, a sentence, a fact.

    The label and the description are composed from the *already decoded* parameters and
    the owner's own names, so the model reads "把「饭团」放在卧室这间房里" instead of an
    opaque ``dailyact:place:room:...`` id.  The target is still carried verbatim: it is
    what a Decision must name, and nothing here paraphrases it.
    """

    verb = str(entry.get("verb") or "")
    params = entry.get("params") or {}
    object_id = entry.get("object_id")
    object_label = labels.get(str(object_id)) if object_id else None
    destination = entry.get("destination") or None
    purchase = entry.get("purchase") or {}
    store_name = None
    if verb == PURCHASE_VERB:
        store_name = place_names.get(str(entry.get("location_id") or "")) or None
    place_name = place_names.get(place_id) or None
    here = place_name or ("%s/%s" % (place_id, area_name) if area_name else place_id)
    slot_label = _hand_label(entry.get("slot_id") or params.get("slot_id"))
    surface_label = None
    if isinstance(destination, Mapping) and destination.get("surface_id"):
        surface_id = str(destination["surface_id"])
        surface_label = surface_labels.get(surface_id) or surface_id
    target_place_name = (place_names.get(str(entry.get("location_id")))
                         if verb == MOVE_VERB else None)

    label: Optional[str] = None
    description: Optional[str] = None
    if verb == PICK_UP_VERB:
        label = "拿起「%s」" % (object_label or object_id or "那个东西")
        description = ("「%s」就在%s；拿起后它在%s里。" % (object_label or object_id,
                                                       here, slot_label or "手"))
    elif verb == PLACE_VERB:
        if surface_label:
            label = "把「%s」放到%s上" % (object_label or object_id or "它", surface_label)
            description = "「%s」现在在手上；%s就在%s。" % (
                object_label or object_id, surface_label, here)
        else:
            label = "把「%s」放下" % (object_label or object_id or "它")
            description = "「%s」现在在手上；放下的地方就是%s这间房。" % (
                object_label or object_id, here)
    elif verb == ARRANGE_VERB:
        if surface_label:
            label = "把「%s」摆到%s上" % (object_label or object_id or "这件家具", surface_label)
            description = "「%s」是她自己已经拥有、已经到家的家具；摆到它自己的%s上。" % (
                object_label or object_id or "这件家具", surface_label)
        else:
            area_text = str((destination or {}).get("area_id") or "") or "另一处"
            label = "把「%s」挪到%s" % (object_label or object_id or "这件家具", area_text)
            description = ("「%s」是她自己已经拥有、已经到家的家具；在这个住处里换到%s。"
                           % (object_label or object_id or "这件家具", area_text))
    elif verb == TOGGLE_FIXTURE_VERB:
        fixture_state = str(entry.get("fixture_state") or params.get("state") or "")
        action_word = "打开" if fixture_state == "on" else "关掉"
        label = "%s「%s」" % (action_word, object_label or object_id or "这盏灯")
        description = ("绝对地把这盏灯的电源设为 %s（不是切换）；只有世界包声明过、"
                       "并且就在这个区域里的灯具才可这样操作。" % (fixture_state or "未知"))
    elif verb == PURCHASE_VERB:
        item_name = (purchase.get("name") or object_label or object_id or "一件东西")
        price = purchase.get("price")
        p_min = purchase.get("price_min")
        p_max = purchase.get("price_max")
        open_order = bool(purchase.get("open_order"))
        currency = purchase.get("currency") or ""
        if open_order and p_min is not None and p_max is not None:
            price_text = "%s-%s %s 自选点单" % (p_min, p_max, currency)
        else:
            price_text = ("%s %s" % (price, currency)).strip() if price is not None else "价格待定"
        store = store_name or entry.get("location_id") or "店里"
        label = "在%s买「%s」（%s）" % (store, item_name, price_text)
        balance = purchase.get("balance")
        balance_text = ("她现在的余额是 %s %s。" % (balance, currency)) if balance is not None else ""
        delivery = purchase.get("delivery_status")
        delivery_text = ("成交后交付状态：%s。" % delivery) if delivery else ""
        if open_order:
            description = (
                "在%s按%s档位自选具体商品（在 purchase_choice 写明 item_name 与估计价格）；"
                "买下后产权归她。%s%s" % (store, item_name, balance_text, delivery_text)
            )
        else:
            description = ("「%s」是%s现在真的在卖的商品，价格由经济 owner 给出；"
                           "买下后产权归她。%s%s" % (item_name, store, balance_text, delivery_text))
    elif verb == MOVE_VERB:
        label = "去%s" % (target_place_name or entry.get("location_id") or "另一个地方")
        walk = entry.get("walk_seconds")
        walk_desc = ("从这里走过去大约%s秒。" % int(walk)
                     if isinstance(walk, (int, float)) and not isinstance(walk, bool)
                     else "路程秒数这一轮没有读到。")
        reason_hint = str(entry.get("reason_hint_cn") or "").strip()
        if reason_hint:
            description = ("%s（%s）" % (reason_hint, walk_desc))[:120]
        else:
            description = walk_desc
    elif verb == CONSUME_VERB:
        label = "吃掉/喝掉「%s」（缓解饥饿或口渴）" % (object_label or object_id or "这一份")
        description = "吃掉或喝掉这一份已经属于她的食物或饮品（一次一份）；可缓解对应的虚拟饥饿或口渴需求。"
    elif verb == TOILET_VERB:
        label = "用一下这里的设施"
        description = ("使用这个世界包在此处声明过的设施；只改变虚拟身体需求，"
                       "不生成任何隐私内容，也不是人体生理主张。")
    elif verb == STAY_VERB:
        seconds = entry.get("stay_seconds")
        affordance = entry.get("affordance") or ""
        phrase = STAY_IDLE_PHRASES.get(affordance)
        seconds_text = int(seconds) if isinstance(seconds, int) else seconds
        # 发呆/转悠这类说不出口的选项，标签就用日常说法；说不出口的事没人会选。
        label = ("在%s%s" % (here, phrase)) if phrase else ("在%s待一会儿" % here)
        # 不说"什么也不做、不产出东西"：那等于在选项里替她判了这件事没用，而发呆本身就是
        # 这件事的内容。只说清楚时间有多长，剩下的由她自己填。
        description = ("就在这里待大约%s秒，这段时间空着。" % seconds_text
                       if phrase else
                       "就在这里待大约%s秒（%s）。" % (seconds_text, affordance))
    elif verb == POSE_VERB:
        label = "换个姿势（%s）" % (params.get("pose") or "")
        description = "身体的姿势换成%s，朝向仍是%s。" % (
            params.get("pose"), params.get("orientation"))
    elif verb == REST_VERB:
        state = params.get("state") or ""
        label = "开始休息" if state == "start" else "结束休息"
        description = "这是一次恢复会话的%s，不是睡眠，也不是恢复完成的证明。" % (
            "开始" if state == "start" else "结束")

    duration, duration_source = _duration_seconds(entry)
    facts = _preparation(entry, body, destination)
    out = {
        "target_ref": entry.get("target_ref"),
        "verb": verb,
        "label": label,
        "description": description,
        "duration_seconds": duration,
        "duration_source": duration_source,
        # ``availability`` is the owner's verdict on the *verb*; ``place_availability`` is
        # its verdict on the place the action happens in.  They are never merged.
        "availability": "AVAILABLE",
        "availability_reason": entry.get("owner_blocked_reason"),
        "available": True,
        "place_availability": entry.get("availability"),
        "observed": "true" if entry.get("observed") else "false",
        "observed_now": True,
        # A one-sentence preparation summary for the prompt, plus the same facts as data.
        "preparation": _preparation_text(verb, facts),
        "preparation_facts": facts,
        "object_id": object_id,
        "object_label": object_label,
        # 0.7: only a ``buy:`` target carries these; every other verb keeps ``None``.
        "purchase": dict(purchase) if purchase else None,
        "slot_id": entry.get("slot_id") or params.get("slot_id"),
        "destination": dict(destination) if isinstance(destination, Mapping) else None,
        "location_id": entry.get("location_id"),
        "place_name": target_place_name or (place_name if verb != MOVE_VERB else None),
        "fact_key": entry.get("fact_key"),
        "expected_value": entry.get("expected_value"),
        "owner_blocked_reason": entry.get("owner_blocked_reason"),
        "valid_until": entry.get("valid_until"),
        "action_grant": False,
    }
    reason_hint_cn = str(entry.get("reason_hint_cn") or "").strip()
    if reason_hint_cn:
        out["reason_hint_cn"] = reason_hint_cn[:96]
    return out


def _context_unknowns(
    observation: Mapping[str, Any],
    *,
    catalog: Mapping[str, Any],
    place_id: str,
    area_id: str,
    body: Mapping[str, Any],
    surfaces: Optional[list[dict[str, Any]]],
    surface_reason: Optional[str],
    nearby: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Everything this context honestly does *not* know, named instead of filled in.

    A far place she has not looked at, an object whose location the snapshot does not
    expose, an unreadable opening state, an owner that did not answer the surface
    question -- each is one entry with a stable ``code``, so the caller can say "不知道"
    instead of assuming "there is nothing".
    """

    unknowns: list[dict[str, Any]] = []
    if str(catalog.get("status") or "") != "available":
        unknowns.append({
            "code": "place_facts_unavailable",
            "detail": str(catalog.get("reason_code") or "the place catalog did not answer"),
            "scope": "all_places",
        })
    else:
        unseen = [str(item.get("location_id"))
                  for item in catalog.get("opportunities") or []
                  if isinstance(item, Mapping) and item.get("location_id")
                  and str(item.get("location_id")) != place_id
                  and str((item.get("observed") or {}).get("status")) != "visible_here"]
        if unseen:
            unknowns.append({
                "code": "places_not_observed",
                "detail": "these places are not seen from here; only their timetable "
                          "facts are known",
                "location_ids": sorted(unseen)[:8],
                "count": len(unseen),
            })
        here_fact = _place_facts(catalog, place_id)
        availability = str((here_fact.get("availability") or {}).get("status") or "")
        if availability in ("", "UNKNOWN"):
            unknowns.append({
                "code": "place_opening_state_unknown",
                "detail": "whether this place is open right now could not be read",
                "place_id": place_id,
            })

    known_ids = {str(item.get("object_id")) for item in nearby}
    unknown_objects = sorted(
        str(item.get("object_id"))
        for item in observation.get("visible_objects") or []
        if isinstance(item, Mapping) and item.get("object_id")
        and str(item["object_id"]) not in known_ids)
    if unknown_objects:
        unknowns.append({
            "code": "object_locations_not_exposed",
            "detail": "the body snapshot lists these objects without saying which place "
                      "they are in, so they are not treated as nearby",
            "object_count": len(unknown_objects),
        })
    if surfaces is None:
        unknowns.append({
            "code": "surface_destinations_not_exposed",
            "detail": "the World did not answer which surfaces exist here, so only the "
                      "room itself can be named as somewhere to put something down",
            "reason": surface_reason,
        })
    unknowns.append({
        "code": "container_destinations_not_available",
        "detail": "the World does not accept a container destination for a placement in "
                  "this version, so no container is offered",
    })
    if body.get("hands_usable") is False:
        unknowns.append({
            "code": "hands_not_usable_in_this_pose",
            "detail": "the current posture cannot manipulate anything",
            "pose": body.get("pose"),
        })
    if not area_id:
        unknowns.append({
            "code": "area_not_reported",
            "detail": "the snapshot did not say which indoor area the body is in",
        })
    details = unknowns[:MAX_CONTEXT_UNKNOWNS]
    # Two shapes, on purpose: short codes for the prompt (a bounded list of names), and the
    # same entries with their detail for anything that wants to explain them.
    return [str(item["code"]) for item in details], details


def _plus_seconds(iso_text: str, seconds: int) -> str:
    from datetime import datetime, timezone

    moment = datetime.fromisoformat(iso_text.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return (moment + timedelta(seconds=int(seconds))).astimezone(timezone.utc).isoformat()


class WorldMoveOpportunityProvider:
    """Read one real observation through the World gateway, then translate it.

    The read uses the same ``WorldBodyClient`` the AR-0 adapter submits through,
    so observation and action share one transport and one identity.  Anything
    unreadable yields an empty list: no observation means no opportunity, never a
    guessed one.
    """

    def __init__(
        self,
        *,
        client: Any,
        action_intent: Any,
        bridge_dir: Optional[str] = None,
        valid_for_seconds: int = DEFAULT_VALIDITY_SECONDS,
        catalog_reader: Any = None,
        store_reader: Any = None,
        environment: Optional[Mapping[str, str]] = None,
        world_home: Optional[str] = None,
        now: Any = None,
        observed_now: Any = None,
    ) -> None:
        self.client = client
        self.action_intent = action_intent
        self.bridge_dir = bridge_dir
        self.valid_for_seconds = int(valid_for_seconds)
        #: Injected for tests; ``read_location_catalog`` by default.  Called with
        #: ``(environment, now, hermes_home=...)``.
        self.catalog_reader = catalog_reader or read_location_catalog
        #: Injected for tests; ``read_store_projection`` by default.  Called with
        #: ``(environment, now, hermes_home=...)`` and never writes anything.
        self.store_reader = store_reader or read_store_projection
        self.environment = dict(environment) if environment is not None else None
        self.world_home = world_home
        self.now = now
        #: The instant *this observation* is taken at, when it must differ from the instant the
        #: schedule is read at.  ``None`` keeps them the same, which is the production shape.
        self.observed_now = observed_now
        self.last_observation: Optional[dict[str, Any]] = None
        self.last_action_space: Optional[dict[str, Any]] = None
        self.last_available_actions: Optional[dict[str, Any]] = None
        self.last_catalog: Optional[dict[str, Any]] = None
        #: 0.7: the counter's own read-only projection this round (``None`` = not read).
        self.last_store_projection: Optional[dict[str, Any]] = None
        self.last_opportunity_view: list[dict[str, Any]] = []
        #: v0.4 B: the instant this observation was taken at (ISO), and the owner's own
        #: projection of the surfaces present where she is.  Both are what
        #: ``daily_life_context`` reads back, so the context can never be assembled from a
        #: different read than the opportunities came from.
        self.last_observed_at: Optional[str] = None
        self.last_surfaces: Optional[list[dict[str, Any]]] = None
        self.last_destination_reason: Optional[str] = None
        self.last_reason: Optional[str] = None
        #: 定义层状态：available / environment_only / absent / closed / unavailable /
        #: error。显式执行定义不成立时 ``reason_code`` 是固定错误码，不是空列表。
        self.last_stay_definition_status: Optional[dict[str, Any]] = None
        #: Stage 1: zero-write life reason hints keyed by opportunity_id & target_ref.
        self.last_move_reason_hints: dict[str, str] = {}

    def __call__(self) -> list[dict[str, Any]]:
        return self.observe()

    def observe(self, *, occurred_at: Optional[str] = None) -> list[dict[str, Any]]:
        from datetime import datetime, timezone

        # 每一轮重新计算定义层状态；提前返回时保持 None，不沿用上一轮结论。
        self.last_stay_definition_status = None
        wbc = self._world_body_client()
        status = self.client.get_status()
        if status.outcome != wbc.OK or not (status.data or {}).get("ready"):
            self.last_reason = "runtime_not_ready:%s" % status.outcome
            return []
        snapshot = self.client.get_snapshot()
        if snapshot.outcome != wbc.OK or not isinstance(snapshot.data, dict):
            self.last_reason = "snapshot_unavailable:%s" % snapshot.outcome
            return []
        available = self.client.get_available_actions()
        available_actions = (
            available.data
            if available.outcome == wbc.OK and isinstance(available.data, dict)
            else None
        )
        import inspect as _inspect

        _bas_params = _inspect.signature(self.action_intent.build_action_space).parameters
        _accepts_env = (
            "environment" in _bas_params
            or any(p.kind == _inspect.Parameter.VAR_KEYWORD for p in _bas_params.values())
        )
        _bas_kwargs: dict[str, Any] = {"available_actions": available_actions}
        if _accepts_env:
            _bas_kwargs["environment"] = self.environment
        space = self.action_intent.build_action_space(snapshot.data, **_bas_kwargs)
        visible = self.action_intent.redact_action_space_for_selector(space)
        self.last_observation = dict(snapshot.data)
        self.last_action_space = visible
        self.last_available_actions = dict(available_actions) if available_actions else None
        moment = occurred_at or self._observation_moment_iso()
        # The place facts are read *before* the stay is offered: a stay is only legal where
        # from a catalog read that has not happened yet.
        self.last_catalog = self._catalog()
        # catalog 读取本身带出的定义错误：同一固定 code + 安全 path 直接报给调用方，
        # 不落成空列表，也不只在 last_* 内部字段里留下。普通读失败没有 code，照旧。
        catalog_definition_error = self._catalog_definition_error()
        if catalog_definition_error is not None:
            code, path = catalog_definition_error
            self.last_stay_definition_status = {
                "status": "error", "reason_code": code, "definitions": []}
            raise StayDefinitionError(code, path)
        # 0.7: one read-only counter projection this round.  It is used for two things
        # only -- keeping the one open economy store inside the move bound, and offering
        # the goods it really sells while she is standing at the counter.
        self.last_store_projection = self._store()
        move = observed_move_opportunities(
            snapshot.data,
            action_space=visible,
            occurred_at=moment,
            observed_at=moment,
            valid_for_seconds=self.valid_for_seconds,
            # The catalog's own kind per place: only used so an adjacent *street* is never
            # dropped by the venue cap (a street is how the rest of the graph is reached).
            place_kinds={
                str(item.get("location_id")): item.get("kind")
                for item in (self.last_catalog or {}).get("opportunities") or []
                if isinstance(item, Mapping) and item.get("location_id")
            },
            reserved_destinations=self._reserved_destinations(),
        )
        consume = observed_consume_opportunities(
            snapshot.data,
            available_actions=available_actions,
            occurred_at=moment,
            observed_at=moment,
            valid_for_seconds=self.valid_for_seconds,
        )
        # 定义层状态在候选之前就算好，因此"没有可执行定义"与"闭店/暂不可执行"
        # 不能都表现成同一个空列表；显式定义不成立时错误码也留在这里。
        self.last_stay_definition_status = stay_definition_status_for(
            snapshot.data, self.last_catalog, observed_at=moment)
        # 第一层不成立时**向调用方抛出**既有 StayDefinitionError（固定 code + 安全
        # 结构 path），不再捕获成空列表；调用方（Life cycle）已把异常记进
        # last_world_error，这是实际消费的错误通道。
        stay = observed_stay_opportunities(
            snapshot.data,
            catalog=self.last_catalog,
            occurred_at=moment,
            observed_at=moment,
            valid_for_seconds=self.valid_for_seconds,
        )
        body = observed_body_action_opportunities(
            snapshot.data,
            available_actions=available_actions,
            occurred_at=moment,
            observed_at=moment,
            valid_for_seconds=self.valid_for_seconds,
        )
        toilet = observed_toilet_opportunities(
            snapshot.data,
            available_actions=available_actions,
            occurred_at=moment,
            observed_at=moment,
            valid_for_seconds=self.valid_for_seconds,
        )
        # v0.4 B: the surfaces really present where she is, from the owner's own
        # environment projection.  ``None`` means "the owner did not answer this round",
        # which is different from "there is no surface": the context reports it as an
        # unknown rather than pretending the room is bare.
        self.last_surfaces = self._surfaces_here()
        daily = observed_daily_action_opportunities(
            snapshot.data,
            available_actions=available_actions,
            occurred_at=moment,
            observed_at=moment,
            valid_for_seconds=self.valid_for_seconds,
            destinations=self.last_surfaces or [],
        )
        self.last_destination_reason = (
            None if self.last_surfaces is not None
            else "SURFACES_NOT_EXPOSED_BY_OWNER")
        # v0.4 room-living: furniture arrangement and absolute fixture power, offered only
        # from the owner's own availability verdict and only where she really is.
        arrange = observed_arrange_opportunities(
            snapshot.data,
            available_actions=available_actions,
            occurred_at=moment,
            observed_at=moment,
            valid_for_seconds=self.valid_for_seconds,
        )
        toggle = observed_toggle_fixture_opportunities(
            snapshot.data,
            available_actions=available_actions,
            occurred_at=moment,
            observed_at=moment,
            valid_for_seconds=self.valid_for_seconds,
        )
        # 0.7: the counter's goods, only when the owner says she is really there and it is
        # really open with room to buy.  A closed shop or another place yields nothing.
        purchase = observed_purchase_opportunities(
            snapshot.data,
            projection=self.last_store_projection,
            occurred_at=moment,
            observed_at=moment,
            valid_for_seconds=self.valid_for_seconds,
        )
        self.last_observed_at = moment
        groups = _bound_world_opportunities(
            [move, consume, stay, body, toilet, daily, arrange, toggle, purchase])
        flat = [item for group in groups for item in group]
        reason_hints: dict[str, str] = {}
        try:
            day_root = self._resolve_day_root_zero_write()
            if groups and groups[0]:
                enriched_moves = enrich_move_opportunities_with_life_reasons(
                    groups[0],
                    day_root=day_root,
                    observation=snapshot.data,
                    now_iso=moment,
                )
                for row in enriched_moves:
                    hint = str(row.get("reason_hint_cn") or "").strip()
                    if not hint:
                        continue
                    oid = str(row.get("opportunity_id") or "").strip()
                    if oid:
                        reason_hints[oid] = hint
                    targets = row.get("target_refs") or ()
                    if targets:
                        reason_hints[str(targets[0])] = hint
        except Exception:  # noqa: BLE001 - enrichment is best-effort and never blocks observe()
            reason_hints = {}
        self.last_move_reason_hints = dict(reason_hints)
        self.last_opportunity_view = opportunity_view(
            flat, self.last_catalog, available_actions,
            purchase_facts=self._purchase_facts(),
            reason_hints=reason_hints)
        self.last_reason = None
        return flat

    def _resolve_day_root_zero_write(self) -> Optional[Path]:
        """Resolve enclosing instance day_root purely zero-write, or None for bare doubles."""
        raw_root = str(self.world_home or "").strip()
        if not raw_root and isinstance(self.environment, Mapping):
            raw_root = str(
                self.environment.get("CHIYO_WORLD_HOME")
                or self.environment.get("HERMES_HOME")
                or ""
            ).strip()
        if not raw_root:
            return None
        ip = _import_instance_paths()
        if ip is not None:
            try:
                return ip.resolve_instance_paths(raw_root, environment=self.environment).day_root
            except Exception:  # noqa: BLE001
                return Path(raw_root)
        return Path(raw_root)

    # -- plumbing ----------------------------------------------------------

    def _observation_moment_iso(self) -> str:
        """The instant this observation is taken at (ISO), as the caller's own clock says.

        ``observed_now`` exists because the two instants are genuinely different in a
        rehearsal that pins the schedule (so "the shop is closed" is deterministic) while the
        Life clock is what every round and every stay is measured on.  With ``observed_now``
        unset the two instants are the same, which is the production shape.
        """

        if self.observed_now is None:
            return self._moment_iso()
        value = self.observed_now() if callable(self.observed_now) else self.observed_now
        if hasattr(value, "isoformat") and not isinstance(value, str):
            return value.isoformat()
        return str(value)

    def _moment(self) -> Any:
        """The instant this observation is taken at, as a real datetime when possible.

        The W catalog takes an aware ``datetime`` (its own ``_instant`` refuses a string),
        so the same instant the opportunity ids carry is handed to it as an object, and
        only the ids use the ISO spelling.
        """
        from datetime import datetime, timezone

        if self.now is not None:
            value = self.now() if callable(self.now) else self.now
            if hasattr(value, "isoformat") and not isinstance(value, str):
                return value
            if isinstance(value, str):
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return datetime.now(timezone.utc)

    def _moment_iso(self) -> str:
        value = self._moment()
        return value.isoformat() if hasattr(value, "isoformat") else str(value)

    def _catalog(self) -> Optional[dict[str, Any]]:
        """Read the place facts.  A reader that raises is reported, never guessed."""

        moment = self._moment()
        try:
            catalog = self.catalog_reader(self.environment, moment,
                                          hermes_home=self.world_home)
        except TypeError:
            # A reader that does not take ``hermes_home`` (older test doubles).
            try:
                catalog = self.catalog_reader(self.environment, moment)
            except Exception as error:  # noqa: BLE001
                self.last_reason = "catalog_read_failed:%s" % type(error).__name__
                return None
        except Exception as error:  # noqa: BLE001
            self.last_reason = "catalog_read_failed:%s" % type(error).__name__
            return None
        return dict(catalog) if isinstance(catalog, Mapping) else None

    def _store(self) -> Optional[dict[str, Any]]:
        """Read the counter's own projection once.  A failed read is no store fact."""

        # A provider with neither a request environment nor a world home (a hand-built test
        # double) has no instance to read; never fall back to the operator's own home.
        if self.environment is None and not self.world_home:
            return None
        moment = self._moment()
        try:
            projection = self.store_reader(self.environment, moment,
                                           hermes_home=self.world_home)
        except TypeError:
            try:
                projection = self.store_reader(self.environment, moment)
            except Exception as error:  # noqa: BLE001
                self.last_reason = "store_read_failed:%s" % type(error).__name__
                return None
        except Exception as error:  # noqa: BLE001
            self.last_reason = "store_read_failed:%s" % type(error).__name__
            return None
        return dict(projection) if isinstance(projection, Mapping) else None

    def _reserved_destinations(self) -> list[str]:
        """The places that must survive the move bound because they are really open.

        Two narrow kinds: the default district's browse-only flower shop (a place the four
        named everyday places include; whether it is open is still the stay layer's own
        question), and the economy owner's own store (only while its own opening rule says it
        is open).  A closed counter, a disabled economy or an unreadable projection reserves
        no store, so the ordinary neighbourhood offer is exactly what it was.  The bound
        itself is unchanged -- these only choose *which* places stay inside it.
        """

        reserved: list[str] = list(RESERVED_BROWSE_PLACES)
        projection = self.last_store_projection or {}
        store_id = str(projection.get("store_id") or "").strip()
        availability = projection.get("availability")
        if (store_id and isinstance(availability, Mapping)
                and str(availability.get("status") or "") == "OPEN"):
            reserved.append(store_id)
        open_stores = projection.get("open_store_ids")
        if isinstance(open_stores, list):
            for sid in open_stores[:3]:
                sid_text = str(sid or "").strip()
                if sid_text and sid_text not in reserved:
                    reserved.append(sid_text)
        # v0.4 room-living: when she is away but really owns furniture placed at home, the
        # owner offers ARRANGE there.  Home must not be squeezed out of the move bound by
        # the street/store reservation, or the whole "walk back and use what you bought"
        # chain would be unreachable from the very place she bought it.  It is still only a
        # reservation *inside* the bound, and a home the owner does not offer is ignored.
        arrange_entry = next(
            (row for row in (self.last_available_actions or {}).get("actions") or []
             if isinstance(row, Mapping) and row.get("verb") == ARRANGE_VERB), None)
        if arrange_entry and arrange_entry.get("available"):
            for offered in arrange_entry.get("candidates") or []:
                placement = (offered or {}).get("placement") if isinstance(
                    offered, Mapping) else None
                home_place = str((placement or {}).get("location_id") or "").strip()
                if home_place:
                    reserved.append(home_place)
                    break
        return list(dict.fromkeys(reserved))

    def _purchase_facts(self) -> dict[str, dict[str, Any]]:
        """``{catalog_item_id: public counter facts}`` for this round's catalogue."""

        projection = self.last_store_projection or {}
        balance = projection.get("balance")
        delivery = projection.get("delivery") if isinstance(
            projection.get("delivery"), Mapping) else {}
        facts: dict[str, dict[str, Any]] = {}
        for item in projection.get("catalog") or []:
            if not isinstance(item, Mapping):
                continue
            item_id = str(item.get("catalog_item_id") or "").strip()
            if not item_id:
                continue
            facts[item_id] = {
                "catalog_item_id": item_id,
                "name": item.get("name"),
                "description": item.get("description"),
                "price": item.get("price"),
                "price_min": item.get("price_min"),
                "price_max": item.get("price_max"),
                "open_order": bool(item.get("open_order")),
                "category": item.get("category"),
                "price_tier": item.get("price_tier"),
                "currency": item.get("currency") or projection.get("currency"),
                "affordable": item.get("affordable"),
                "balance": balance,
                "store_id": projection.get("store_id"),
                "delivery_mode": delivery.get("mode"),
                "delivery_seconds": delivery.get("duration_seconds"),
            }
        return facts

    def _catalog_definition_error(self) -> Optional[tuple[str, Optional[str]]]:
        """catalog 读取带出的定义错误；只有闭集 code 才向调用方抛出。"""

        catalog = self.last_catalog
        if not isinstance(catalog, Mapping):
            return None
        if str(catalog.get("status") or "") != "unavailable":
            return None
        return _safe_definition_error(catalog.get("code"), catalog.get("path"))

    def _world_body_client(self) -> Any:
        if self.bridge_dir:
            import sys

            if self.bridge_dir not in sys.path:
                sys.path.insert(0, self.bridge_dir)
        import world_body_client as wbc

        return wbc

    # -- the bounded living context (v0.4 B) --------------------------------
    def _surfaces_here(self) -> Optional[list[dict[str, Any]]]:
        """The owner's own projection of the surfaces at this place, or ``None``.

        ``get_environment`` is a read-only gateway command that answers from the owner's
        live substrate and its own declaration, so it is the only bounded read that says
        which surfaces really exist where she is.  A client without it, or an owner that
        cannot answer, yields ``None`` -- "not exposed" is not "there is nothing here".
        """

        call = getattr(self.client, "call", None)
        if not callable(call):
            return None
        try:
            probe = call("get_environment")
        except Exception:  # noqa: BLE001 - an unreadable surface list is not a surface
            return None
        wbc = self._world_body_client()
        if getattr(probe, "outcome", None) != wbc.OK or not isinstance(probe.data, Mapping):
            return None
        surfaces = probe.data.get("surfaces")
        if not isinstance(surfaces, list):
            return None
        return [dict(item) for item in surfaces if isinstance(item, Mapping)]

    def _ambient_context(self) -> Optional[dict[str, Any]]:
        """The World's own current, location-relevant ambient facts -- or ``None`` when off.

        Only the already-enabled ambient feature is read; ``0``/unset returns ``None`` so the
        context keeps its old shape and this method performs **no** extra read.  The facts come
        from the official ``observe_world`` projection, bound to this round's own clock and
        world home, so the time/location filtering stays the World's own.  Travel realization
        is switched off for this read only: Life must never write the World state on a
        background read.  A read that fails is reported as an unknown, never filled in.
        """

        environment = self.environment
        if not environment or not self.world_home:
            return None
        env = dict(environment)
        try:
            import world_ambient_events as wae

            if not wae.feature_enabled(env):
                return None
        except Exception:  # noqa: BLE001 - no World module means no enabled feature
            return None
        try:
            import world_runtime_data as wrd

            # Bind the read to the *same* world package the instance is running; without
            # that explicit binding the World module would fall back to its built-in city.
            if wrd.package_root(env) is None:
                return None
        except Exception:  # noqa: BLE001 - an unreadable binding is not a same-world read
            return None
        read_env = dict(env)
        read_env[AMBIENT_READ_TRAVEL_ENV] = "0"
        try:
            import world_living_skeleton as wls

            observation = wls.observe_world(
                hermes_home=self.world_home, environment=read_env, now=self._moment())
        except Exception:  # noqa: BLE001 - reported as unknown, never guessed
            return {"status": "unavailable", "source": AMBIENT_CONTEXT_SOURCE,
                    "reason_code": "AMBIENT_READ_FAILED", "events": [], "total": 0}
        if (not isinstance(observation, Mapping)
                or str(observation.get("status")) != "available"):
            return {"status": "unavailable", "source": AMBIENT_CONTEXT_SOURCE,
                    "reason_code": "AMBIENT_OBSERVATION_UNAVAILABLE",
                    "events": [], "total": 0}
        raw_events = observation.get("ambient_events")
        if not isinstance(raw_events, list):
            return {"status": "unavailable", "source": AMBIENT_CONTEXT_SOURCE,
                    "reason_code": "AMBIENT_NOT_PROJECTED", "events": [], "total": 0}
        # A second read that names another world/place, or carries a record outside its
        # own validity window, is not "what is happening here now": report the conflict
        # and present no event rather than fill the prompt with a contradicting fact.
        conflict = _ambient_same_source_conflict(
            observation, self.last_observation, raw_events)
        if conflict:
            return {"status": "unavailable", "source": AMBIENT_CONTEXT_SOURCE,
                    "reason_code": conflict, "events": [], "total": 0}
        rows = [row for row in (_ambient_row(item) for item in raw_events) if row]
        return {
            "status": "available",
            "source": AMBIENT_CONTEXT_SOURCE,
            "observed_at": _context_text(observation.get("local_time"), 40) or None,
            "events": rows[:MAX_AMBIENT_CONTEXT_EVENTS],
            "total": len(rows),
            "truncated": len(rows) > MAX_AMBIENT_CONTEXT_EVENTS,
            "action_grant": False,
        }

    def daily_life_context(self, limit: int = 8) -> dict[str, Any]:
        """The live situation, as bounded *data* for the cognition layer.

        This is the "she can see where she is and what her hands could do" view: the place
        she is really in (with the name the World itself gives it), her body's own posture
        and hands, the objects the owner says are within reach here, the actions that are
        really available right now, and an explicit list of what she does **not** know.  It
        is built only from the observation already taken this round (``last_observation``
        / ``last_available_actions`` / ``last_catalog`` / ``last_surfaces`` /
        ``last_opportunity_view``), so it can never describe a different World state than
        the opportunities came from, and it reads no file, no database and no directory of
        its own.

        What it deliberately is not:

        * **not a recommendation**: there is no score, no ranking, no "best" and no default
          pick.  ``opportunities`` is grouped for presentation (hand actions first, then a
          place to be, then going somewhere, then posture) and each group keeps the
          observation's own emission order -- that grouping is documented in
          ``order_basis``, and it is not a preference;
        * **not omniscient**: a place she has not observed contributes no object and no
          interior detail, an object outside her reach is not listed as nearby, and every
          gap is named in ``unknowns`` instead of being filled in;
        * **not a script**: nothing here is a plan, a task, a time of day or a quota.

        Keys: ``status``, ``observed_at``, ``location``, ``body``, ``nearby``,
        ``opportunities``, ``unknowns`` (the required shape), plus ``reason_code``,
        ``action_grant: false``, ``limit``, ``truncated``, ``total_opportunities``,
        ``order_basis`` and ``schedule_source`` for the caller's own bookkeeping.

        v0.6: when the instance has the World ambient feature enabled, ``ambient`` carries the
        World's own current, location-relevant facts for this place and instant (bounded to the
        World's per-day cap).  It is background only -- not a candidate, an offer, a permission
        or a task.  With the feature off/unset the key is absent and nothing extra is read.
        """

        limit_value = 8
        if not isinstance(limit, bool):
            try:
                limit_value = int(limit)
            except (TypeError, ValueError):
                limit_value = 8
        limit_value = max(0, min(limit_value, 32))

        observation = self.last_observation
        if not isinstance(observation, Mapping) or not observation:
            return {
                "status": "unavailable",
                "reason_code": self.last_reason or "NO_OBSERVATION",
                "observed_at": self.last_observed_at,
                "location": None,
                "body": None,
                "nearby": [],
                "opportunities": [],
                "unknowns": ["no_observation"],
                "unknown_details": [{"code": "no_observation",
                                     "detail": "no World observation has been taken "
                                               "this round"}],
                "nearby_objects": [],
                "action_grant": False,
                "limit": limit_value,
                "truncated": False,
                "total_opportunities": 0,
                "order_basis": "kind_group_presentation",
            }

        catalog = self.last_catalog or {}
        location = observation.get("location") or {}
        place_id = str(location.get("place_id") or "")
        area_id = str(location.get("area_id") or "")
        labels = _object_labels(observation)
        place_names = _place_names(catalog)
        place_facts = _place_facts(catalog, place_id)
        place_name = place_names.get(place_id)
        area_name = _area_name(place_facts, area_id)
        surfaces = self.last_surfaces
        surface_labels = {str(item.get("surface_id")): str(item.get("label") or "")
                          for item in surfaces or []
                          if isinstance(item, Mapping) and item.get("surface_id")}

        body = _body_context(observation)
        nearby_objects = _nearby_objects(observation, body, labels,
                                         _takeable_here(self.last_available_actions))
        nearby = _nearby_places(catalog, place_id=place_id, place_names=place_names,
                                limit=limit_value)
        view = [entry for entry in (self.last_opportunity_view or [])
                if isinstance(entry, Mapping)]
        ordered = sorted(view, key=_context_order_key)
        selected = ordered[:limit_value]
        all_opportunities = [
            _context_opportunity(entry, labels=labels, place_names=place_names,
                                 place_id=place_id, area_name=area_name,
                                 surface_labels=surface_labels, body=body)
            for entry in ordered
        ]
        opportunities = all_opportunities[:len(selected)]
        candidate_labels = {
            str(item["target_ref"]): str(item["label"])
            for item in all_opportunities
            if item.get("target_ref") and item.get("label")
        }
        unknown_codes, unknown_details = _context_unknowns(
            observation, catalog=catalog, place_id=place_id, area_id=area_id, body=body,
            surfaces=surfaces, surface_reason=self.last_destination_reason,
            nearby=nearby_objects)
        context: dict[str, Any] = {
            "status": "available",
            "reason_code": None,
            "observed_at": self.last_observed_at,
            "location": {
                "place_id": place_id or None,
                "area_id": area_id or None,
                # A name only when the World itself names the place; an unreadable catalog
                # leaves ``None`` rather than a prettified id.
                "name": place_name,
                "area_name": area_name,
                "kind": place_facts.get("kind") if place_facts else None,
                "here": True,
                "indoor": bool(place_facts.get("indoor_areas")) if place_facts else None,
                "scene_id": observation.get("scene_id"),
                "local_time": catalog.get("local_time"),
                "timezone": catalog.get("timezone"),
                "availability": ((place_facts.get("availability") or {}).get("status")
                                 if place_facts else None),
                "observes": ((place_facts.get("observed") or {}).get("status")
                             if place_facts else None),
                # 这个地方自己写的文字（书里的一页、墙上的告示）。只是背景事实，
                # 不是候选、不是授权，也不代表她读过；世界没写就是 None。
                "reading": (place_facts.get("reading") if place_facts else None),
            },
            "body": body,
            # ``nearby`` is what the catalog says about nearby *places*; ``nearby_objects``
            # is what the owner says is really within reach.  They are different questions
            # and are never merged into one list.
            "nearby": nearby,
            "nearby_objects": nearby_objects,
            "opportunities": opportunities,
            "candidate_labels": candidate_labels,
            "unknowns": unknown_codes,
            "unknown_details": unknown_details,
            "action_grant": False,
            "schedule_source": catalog.get("schedule_source"),
            "limit": limit_value,
            "truncated": len(ordered) > len(selected),
            "total_opportunities": len(ordered),
            "order_basis": "kind_group_presentation",
        }
        # v0.6: an *enabled* ambient feature adds the World's own current facts for this place
        # and instant.  Off/unset adds nothing and performs no read, so the old shape (and its
        # cost) is exactly what it was.
        ambient = self._ambient_context()
        if ambient is not None:
            context["ambient"] = ambient
        return context


def _import_instance_paths() -> Any:
    try:
        from components.world.scripts import instance_paths as ip
        return ip
    except ImportError:
        repo_root = str(Path(__file__).resolve().parents[3])
        world_dir = str(Path(__file__).resolve().parents[2] / "world" / "scripts")
        for p in (repo_root, world_dir):
            if p not in sys.path:
                sys.path.insert(0, p)
        try:
            from components.world.scripts import instance_paths as ip
            return ip
        except ImportError:
            try:
                import instance_paths as ip  # type: ignore[no-redef]
                return ip
            except ImportError:
                return None


def _read_optional_json_zero_write(path: Path) -> Optional[dict[str, Any]]:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return dict(raw) if isinstance(raw, Mapping) else None


def _extract_local_hour(
    now_iso: Optional[str],
    observation: Optional[Mapping[str, Any]] = None,
) -> Optional[int]:
    candidates: list[Any] = [now_iso]
    if isinstance(observation, Mapping):
        candidates.append(observation.get("local_time"))
        candidates.append(observation.get("observed_at"))
    for raw in candidates:
        if not isinstance(raw, str) or not raw.strip():
            continue
        text = raw.strip()
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return int(dt.hour)
        except ValueError:
            match = re.search(r"(\d{1,2}):(\d{2})", text)
            if match:
                hr = int(match.group(1))
                if 0 <= hr <= 23:
                    return hr
    return None


def _build_outing_reason_helper(
    *,
    milk_empty: bool = False,
    low_tide_open: bool = False,
    passing_cart_title: Optional[str] = None,
    stuffy: bool = False,
    rain_heavy: bool = False,
) -> Optional[dict[str, Any]]:
    try:
        import v02_v03_daily_loop_bridge as _vbridge
        fn = getattr(_vbridge, "build_outing_reason", None)
        if callable(fn):
            return fn(
                milk_empty=milk_empty,
                low_tide_open=low_tide_open,
                passing_cart_title=passing_cart_title,
                stuffy=stuffy,
                rain_heavy=rain_heavy,
            )
    except Exception:  # noqa: BLE001
        pass
    return attach_v05_outing_reason(
        milk_empty=milk_empty,
        low_tide_open=low_tide_open,
        passing_cart_title=passing_cart_title,
        stuffy=stuffy,
        rain_heavy=rain_heavy,
    )


_RAIN_SUPPRESS_WEATHERS = frozenset({
    "rain",
    "storm",
    "heavy_rain",
    "thunder_blackout",
    "sea_gale",
})


def _collect_move_life_signals_zero_write(
    *,
    day_root: Optional[Path | str] = None,
    observation: Optional[Mapping[str, Any]] = None,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Read v04_neighborhood, v05_living_town, v03_economy and observation cues with 0 writes."""
    signals: dict[str, Any] = {
        "has_any_source": False,
        "weather": "clear",
        "rain_heavy": False,
        "illness_acute": False,
        "hour": _extract_local_hour(now_iso, observation),
        "milk_empty": False,
        "budget_tight": False,
        "low_tide_open": False,
        "passing_cart_title": None,
        "v04_weather_present": False,
        "v04_courtyard_care": False,
        "v04_street_notice": False,
        "v04_bookstore_cue": False,
        "v05_theme": "",
        "v05_spectacle_title": "",
        "v05_rainy_surplus_bag": False,
        "v05_bakery_cue": "",
        "v05_drifting_book_spots": set(),
        "v05_present_regular_spots": set(),
        "v05_ferry_running": False,
        "v05_telescope_clear": False,
        "v05_grove_stage": "",
        "current_place_id": _place_of(observation) if isinstance(observation, Mapping) else None,
    }

    ip = _import_instance_paths()
    paths = None
    resolved_day_root: Optional[Path] = None
    if day_root is not None:
        if ip is not None:
            try:
                paths = ip.resolve_instance_paths(day_root)
                resolved_day_root = paths.day_root
            except Exception:  # noqa: BLE001
                resolved_day_root = Path(day_root)
        else:
            resolved_day_root = Path(day_root)

    # 1. Observation cues (weather, lived_needs)
    if isinstance(observation, Mapping):
        obs_weather = observation.get("weather")
        if isinstance(obs_weather, Mapping):
            w_str = str(obs_weather.get("condition") or obs_weather.get("weather") or "").strip().lower()
            if w_str:
                signals["weather"] = w_str
                signals["has_any_source"] = True
        elif isinstance(obs_weather, str) and obs_weather.strip():
            signals["weather"] = obs_weather.strip().lower()
            signals["has_any_source"] = True
        lived_needs = observation.get("lived_needs")
        if isinstance(lived_needs, Mapping):
            for key in ("thirst", "hunger"):
                band = str(lived_needs.get(key) or "").strip().lower()
                if band in ("noticeable", "strong", "urgent", "high"):
                    signals["milk_empty"] = True
                    signals["has_any_source"] = True

    if resolved_day_root is not None:
        # 2. v04_neighborhood_state.json (zero-write)
        v04_doc = _read_optional_json_zero_write(
            resolved_day_root / "world" / "v04_neighborhood_state.json"
        )
        if isinstance(v04_doc, Mapping):
            signals["has_any_source"] = True
            env_block = v04_doc.get("environment")
            if not isinstance(env_block, Mapping):
                env_block = v04_doc.get("weather_and_ground") if isinstance(v04_doc.get("weather_and_ground"), Mapping) else {}
            w_val = str((env_block or {}).get("weather") or (env_block or {}).get("condition") or "").strip().lower()
            if w_val:
                signals["weather"] = w_val
                signals["v04_weather_present"] = True
            laundry = v04_doc.get("laundry") or v04_doc.get("drying_cloths")
            if isinstance(laundry, Mapping) and laundry:
                signals["v04_courtyard_care"] = True
            elif isinstance(laundry, list) and laundry:
                signals["v04_courtyard_care"] = True
            plants = v04_doc.get("plants") or v04_doc.get("garden")
            if isinstance(plants, Mapping) and plants:
                signals["v04_courtyard_care"] = True
            elif isinstance(plants, list) and plants:
                signals["v04_courtyard_care"] = True
            for key in ("notices", "street_notices", "board_notices", "notice_board", "street_corner"):
                val = v04_doc.get(key)
                if val:
                    signals["v04_street_notice"] = True
                    break
            for key in ("bookstore", "bookmarks", "booklets", "borrowed_books"):
                val = v04_doc.get(key)
                if val:
                    signals["v04_bookstore_cue"] = True
                    break

        # 3. v05_living_town.json (zero-write)
        town_file = resolved_day_root / "world" / "v05_living_town.json"
        town_doc = _read_optional_json_zero_write(town_file)
        if isinstance(town_doc, Mapping):
            signals["has_any_source"] = True
            cart_raw = town_doc.get("passing_cart_title") or town_doc.get("passing_cart")
            if isinstance(cart_raw, Mapping):
                cart_title = str(cart_raw.get("title_cn") or cart_raw.get("title") or "").strip()
            else:
                cart_title = str(cart_raw or "").strip()
            if cart_title:
                signals["passing_cart_title"] = cart_title[:32]
            if town_doc.get("low_tide_open") is True:
                signals["low_tide_open"] = True
            try:
                from components.world.scripts import v05_living_town_engine as _v05_town
            except ImportError:
                _v05_town = None
                try:
                    import v05_living_town_engine as _v05_town  # type: ignore[no-redef]
                except ImportError:
                    _v05_town = None
            if _v05_town is not None:
                try:
                    snap = _v05_town.read_living_town_snapshot(resolved_day_root, now_iso=now_iso)
                    wctx = snap.get("world_context") if isinstance(snap.get("world_context"), Mapping) else {}
                    theme = str(wctx.get("theme") or "").strip().lower()
                    signals["v05_theme"] = theme
                    if not signals["v04_weather_present"]:
                        tw = str(wctx.get("weather") or "").strip().lower()
                        if tw:
                            signals["weather"] = tw
                    if signals["hour"] is None and isinstance(wctx.get("hour"), int):
                        signals["hour"] = int(wctx["hour"])
                    spec = snap.get("rare_spectacle")
                    if isinstance(spec, Mapping) and spec.get("active"):
                        title = str(spec.get("title_cn") or spec.get("label_cn") or "").strip()
                        if title:
                            signals["v05_spectacle_title"] = title[:36]
                    chalk = snap.get("chalkboard_and_favors")
                    if isinstance(chalk, Mapping):
                        if chalk.get("rainy_half_price_bag_available"):
                            signals["v05_rainy_surplus_bag"] = True
                        b_extra = str(chalk.get("bakery_extra") or chalk.get("diner_special") or "").strip()
                        if b_extra:
                            signals["v05_bakery_cue"] = b_extra[:36]
                    ts = snap.get("third_spaces")
                    if isinstance(ts, Mapping):
                        pier = ts.get("river_pier")
                        if isinstance(pier, Mapping) and pier.get("ferry_running"):
                            signals["v05_ferry_running"] = True
                        overlook = ts.get("hillside_overlook")
                        if isinstance(overlook, Mapping) and overlook.get("telescope_clear"):
                            signals["v05_telescope_clear"] = True
                        grove = ts.get("blank_grove")
                        if isinstance(grove, Mapping) and grove.get("grove_stage"):
                            signals["v05_grove_stage"] = str(grove.get("grove_stage") or "").strip()[:24]
                    for bk in snap.get("drifting_bookbox") or ():
                        if isinstance(bk, Mapping):
                            sp = str(bk.get("spot_id") or "old_bookshop").strip()
                            if sp:
                                signals["v05_drifting_book_spots"].add(sp)
                    regs = snap.get("regulars")
                    reg_rows = regs.values() if isinstance(regs, Mapping) else (regs if isinstance(regs, list) else ())
                    for reg in reg_rows:
                        if isinstance(reg, Mapping) and reg.get("present_now"):
                            sp = str(reg.get("spot_id") or "").strip()
                            if sp:
                                signals["v05_present_regular_spots"].add(sp)
                except Exception:  # noqa: BLE001
                    pass

        # 4. v03_economy & phone/v03_ecology.json (zero-write)
        eco_path = (
            paths.economy_state_json
            if paths is not None
            else (resolved_day_root / "one-day" / "world-home" / "state" / "world_property_economy.json")
        )
        eco_doc = _read_optional_json_zero_write(eco_path)
        if eco_doc is None:
            for rel in (
                "one-day/world-home/state/world_property_economy.json",
                "world/state/world_property_economy.json",
                "world-home/state/world_property_economy.json",
            ):
                eco_doc = _read_optional_json_zero_write(resolved_day_root / rel)
                if eco_doc is not None:
                    break
        if isinstance(eco_doc, Mapping):
            signals["has_any_source"] = True
            if eco_doc.get("milk_empty") is True:
                signals["milk_empty"] = True
            supplies = eco_doc.get("supplies") or eco_doc.get("pantry")
            if isinstance(supplies, Mapping):
                for drink_key in ("milk", "drinks", "drink", "beverage"):
                    if drink_key in supplies:
                        try:
                            if float(supplies[drink_key]) <= 0:
                                signals["milk_empty"] = True
                        except (TypeError, ValueError):
                            if str(supplies[drink_key]).strip().lower() in ("empty", "none", "low"):
                                signals["milk_empty"] = True
            bal = eco_doc.get("available_balance_cents")
            if bal is None:
                bal = eco_doc.get("balance_cents")
            if isinstance(bal, (int, float)) and not isinstance(bal, bool):
                floor = eco_doc.get("living_floor_cents", 50000)
                try:
                    if float(bal) <= float(floor):
                        signals["budget_tight"] = True
                except (TypeError, ValueError):
                    pass

        v03_phone_eco = _read_optional_json_zero_write(
            resolved_day_root / "phone" / "v03_ecology.json"
        )
        if isinstance(v03_phone_eco, Mapping):
            signals["has_any_source"] = True
            ill = v03_phone_eco.get("illness")
            if isinstance(ill, Mapping):
                phase = str(ill.get("phase") or "").strip().lower()
                if phase in ("acute", "acute_illness"):
                    signals["illness_acute"] = True
            eco_block = v03_phone_eco.get("economy")
            if isinstance(eco_block, Mapping) and eco_block.get("budget_tight") is True:
                signals["budget_tight"] = True

    weather_norm = str(signals["weather"] or "clear").strip().lower()
    signals["weather"] = weather_norm
    signals["rain_heavy"] = weather_norm in _RAIN_SUPPRESS_WEATHERS
    hour_val = signals["hour"]
    if (
        not signals["low_tide_open"]
        and not signals["rain_heavy"]
        and signals["v05_theme"] == "coastal"
        and isinstance(hour_val, int)
        and 13 <= hour_val <= 17
    ):
        signals["low_tide_open"] = True
    return signals


def _map_place_for_reason(dest: str) -> dict[str, Any]:
    ip = _import_instance_paths()
    if ip is not None:
        try:
            return dict(ip.map_world_place_to_slices(dest))
        except Exception:  # noqa: BLE001
            pass
    low = str(dest or "").strip().lower()
    is_outdoor = any(
        tok in low for tok in ("street", "river", "sea", "pier", "park", "hill", "yard", "grove", "overlook")
    ) and not any(tok in low for tok in ("store", "shop", "book", "bakery", "cafe", "laund", "home"))
    v05_spot = "home"
    if any(tok in low for tok in ("book", "stationery", "home_goods")):
        v05_spot = "old_bookshop"
    elif any(tok in low for tok in ("laund",)):
        v05_spot = "coin_laundromat"
    elif any(tok in low for tok in ("grove",)):
        v05_spot = "blank_grove"
    elif any(tok in low for tok in ("park", "hill", "overlook")):
        v05_spot = "hillside_overlook"
    elif any(tok in low for tok in ("river", "sea", "pier", "seawall", "street_3")):
        v05_spot = "river_pier"
    elif any(tok in low for tok in ("street", "store", "bakery", "cafe", "corner")):
        v05_spot = "bakery_corner"
    return {
        "world_place_id": low or "home",
        "v04_place_id": "home",
        "v05_spot_id": v05_spot,
        "is_outdoor": is_outdoor,
    }


def enrich_move_opportunities_with_life_reasons(
    opportunities: list[dict[str, Any]],
    *,
    day_root: Optional[Path | str] = None,
    observation: Optional[Mapping[str, Any]] = None,
    now_iso: Optional[str] = None,
    recent_note_streak: int = 0,
    is_final_closeout_step: bool = False,
) -> list[dict[str, Any]]:
    """Zero-write enrichment attaching concrete ``reason_hint_cn`` cues to MOVE opportunities.

    Reads live zero-write cues from ``v04_neighborhood``, ``v05_living_town``,
    ``v03_economy`` and ``build_outing_reason`` while preserving exact
    ``opportunity_id`` and ``target_refs``.  Suppresses outdoor outing nudges
    when weather is heavy rain/storm or acute illness is active.
    """
    if not opportunities:
        return []
    signals = _collect_move_life_signals_zero_write(
        day_root=day_root,
        observation=observation,
        now_iso=now_iso,
    )
    stuffy = int(recent_note_streak or 0) >= 2 and not bool(is_final_closeout_step)
    rain_heavy = bool(signals["rain_heavy"])
    illness_acute = bool(signals["illness_acute"])
    has_cues = bool(signals["has_any_source"] or stuffy)

    enriched: list[dict[str, Any]] = []
    for raw in opportunities:
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)
        targets = [str(r) for r in (item.get("target_refs") or ())]
        if not targets or not targets[0].startswith(PLACE_REF_PREFIX):
            enriched.append(item)
            continue

        dest = targets[0][len(PLACE_REF_PREFIX):].strip()
        mapped = _map_place_for_reason(dest)
        v05_spot = str(mapped.get("v05_spot_id") or "")
        v04_place = str(mapped.get("v04_place_id") or "")
        is_outdoor = bool(mapped.get("is_outdoor"))

        if not has_cues:
            enriched.append(item)
            continue

        # Suppress outdoor outing push during rain/storm or acute illness
        if (rain_heavy or illness_acute) and is_outdoor:
            enriched.append(item)
            continue

        hint: Optional[str] = None
        if dest == "home":
            if signals["current_place_id"] and signals["current_place_id"] != "home":
                if rain_heavy:
                    hint = "外头雨势不小，顺路回住处泡杯热茶歇口气"
                elif illness_acute:
                    hint = "身体有点乏，回住处安静休息一会儿"
        elif dest in ("convenience_store", "bakery", "bakery_corner", "cafe") or v05_spot == "bakery_corner":
            if signals["milk_empty"] and not rain_heavy:
                outing = _build_outing_reason_helper(milk_empty=True, rain_heavy=False)
                if isinstance(outing, Mapping) and outing.get("label_cn"):
                    hint = str(outing["label_cn"])
                else:
                    hint = "家里饮品见底了，顺路去街口便利店捎一点回来"
            elif signals["passing_cart_title"] and not rain_heavy:
                outing = _build_outing_reason_helper(
                    passing_cart_title=str(signals["passing_cart_title"]),
                    rain_heavy=False,
                )
                if isinstance(outing, Mapping) and outing.get("label_cn"):
                    hint = str(outing["label_cn"])
            elif signals["v05_rainy_surplus_bag"] and dest in ("bakery", "bakery_corner"):
                hint = "雨夜七点后面包铺挂出了半价余粮袋，顺路拎一份回来"
            elif signals["v05_bakery_cue"] and not rain_heavy:
                hint = "拐角小黑板今天写着%s，顺路过去转十分钟" % signals["v05_bakery_cue"]
            elif signals["budget_tight"] and not rain_heavy:
                hint = "这阵子预算偏紧，顺路去街口看看今日平价补给或透透气"
            elif signals["v04_street_notice"] and not rain_heavy:
                hint = "街口告示板有了新动静，顺路下楼走一圈看看"
            elif stuffy and not rain_heavy:
                outing = _build_outing_reason_helper(stuffy=True, rain_heavy=False)
                if isinstance(outing, Mapping) and outing.get("label_cn"):
                    hint = str(outing["label_cn"])
            elif signals["v04_weather_present"] and not rain_heavy:
                hint = "外头天清气爽，去街口溜达十分钟换换空气"
        elif v05_spot == "old_bookshop" or v04_place == "bookstore":
            if "old_bookshop" in signals["v05_drifting_book_spots"] or signals["v04_bookstore_cue"]:
                hint = "旧书铺书架和漂流书箱有新动静，过去翻两页换换脑子"
            elif "old_bookshop" in signals["v05_present_regular_spots"]:
                hint = "旧书铺这会儿开门正安静，过去看会儿书歇歇眼"
            elif signals["v04_weather_present"] and not rain_heavy:
                hint = "外头天气清爽，书店这会儿开门了，去逛逛换个节奏"
            elif not rain_heavy:
                hint = "去书店安安静静翻两页书，给眼睛和脑子透透气"
        elif v05_spot == "river_pier":
            if signals["low_tide_open"] and not rain_heavy:
                outing = _build_outing_reason_helper(low_tide_open=True, rain_heavy=False)
                if isinstance(outing, Mapping) and outing.get("label_cn"):
                    hint = str(outing["label_cn"])
            elif signals["v05_spectacle_title"] and not rain_heavy:
                hint = "外头难得遇上%s，顺路去河埠看一眼吹十分钟风" % signals["v05_spectacle_title"]
            elif signals["v05_ferry_running"] and not rain_heavy:
                hint = "渡口长椅空着、渡船正按点靠岸，出去吹十分钟风"
            elif stuffy and not rain_heavy:
                outing = _build_outing_reason_helper(stuffy=True, rain_heavy=False)
                if isinstance(outing, Mapping) and outing.get("label_cn"):
                    hint = str(outing["label_cn"])
            elif not rain_heavy:
                hint = "去河边走十分钟吹吹风，听听水声换口气"
        elif v05_spot == "hillside_overlook":
            if signals["v05_spectacle_title"] and not rain_heavy:
                hint = "外头遇上%s，上坡去眺望台看一眼远处" % signals["v05_spectacle_title"]
            elif signals["v05_telescope_clear"] and not rain_heavy:
                hint = "今天视野清亮，上坡去眺望台吹风看看街区屋檐"
            elif stuffy and not rain_heavy:
                outing = _build_outing_reason_helper(stuffy=True, rain_heavy=False)
                if isinstance(outing, Mapping) and outing.get("label_cn"):
                    hint = str(outing["label_cn"])
            elif not rain_heavy:
                hint = "上坡走几步去眺望台透透气，活动一下肩背"
        elif v05_spot == "blank_grove":
            if signals["v05_grove_stage"] and not rain_heavy:
                hint = "留白空坪这会儿是「%s」，去树荫下放空十分钟" % signals["v05_grove_stage"]
            elif not rain_heavy:
                hint = "去留白空坪慢慢散步，吹吹轻风松弛一下"
        elif v05_spot == "coin_laundromat":
            hint = "自助烘衣房长凳暖烘烘的，顺路过去坐会儿换个环境"
        elif v04_place in ("yard", "porch"):
            if signals["v04_courtyard_care"] and not rain_heavy:
                hint = "院子里的晾衣和盆栽正好瞧一瞧，顺便呼吸一下外头空气"
            elif not rain_heavy:
                hint = "推门去院檐下站几分钟，晒点自然光活动一下"
        else:
            outing = _build_outing_reason_helper(
                milk_empty=bool(signals["milk_empty"]),
                low_tide_open=bool(signals["low_tide_open"]),
                passing_cart_title=signals["passing_cart_title"],
                stuffy=stuffy,
                rain_heavy=rain_heavy,
            )
            if isinstance(outing, Mapping) and outing.get("label_cn"):
                hint = str(outing["label_cn"])
            elif not rain_heavy:
                hint = "外头风正好，推门出去走十分钟换个节奏"

        if hint:
            item["reason_hint_cn"] = " ".join(str(hint).split())[:96]
        enriched.append(item)
    return enriched


def attach_v05_outing_reason(
    *,
    milk_empty: bool = False,
    low_tide_open: bool = False,
    passing_cart_title: Optional[str] = None,
    stuffy: bool = False,
    rain_heavy: bool = False,
) -> Optional[dict[str, Any]]:
    """0.5 把外出目标转成贴近当下生活氛围的自然出门由头（大雨天留屋不催出门）。"""
    if passing_cart_title and not rain_heavy:
        return {
            "action_id": "go_alley_cart",
            "kind": "outing",
            "label_cn": f"窗外刚飘来{passing_cart_title}的动静，趁这会儿下楼追上去买两块",
            "score": 0.92,
        }
    if low_tide_open and not rain_heavy:
        return {
            "action_id": "go_seawall_tide",
            "kind": "outing",
            "label_cn": "下午刚好退潮露出了旧石阶，拎包散步去海堤看看",
            "score": 0.86,
        }
    if milk_empty and not rain_heavy:
        return {
            "action_id": "go_store_milk",
            "kind": "outing",
            "label_cn": "家里牛奶喝空了，顺路去街口便利店捎一盒回来",
            "score": 0.82,
        }
    if stuffy and not rain_heavy:
        return {
            "action_id": "go_breeze_walk",
            "kind": "outing",
            "label_cn": "在屋里坐久了有点闷，推门沿着河边走十分钟吹吹风",
            "score": 0.78,
        }
    return None


__all__ = [
    "AMBIENT_CONTEXT_SOURCE",
    "ARRANGE_VERB",
    "attach_v05_outing_reason",
    "enrich_move_opportunities_with_life_reasons",
    "BODY_ACTION_REF_PREFIX",
    "CONSUME_VERB",
    "CONTEXT_KIND_ORDER",
    "DAILY_ACTION_REF_PREFIX",
    "DEFAULT_VALIDITY_SECONDS",
    "DURATION_SOURCE_OWNER",
    "DURATION_SOURCE_ROUTE",
    "HANDS_USABLE_POSES",
    "HAND_MANIPULATION_SECONDS",
    "IDLE_STAY_AFFORDANCES",
    "MAX_AMBIENT_CONTEXT_EVENTS",
    "MAX_ARRANGE_OPPORTUNITIES",
    "MAX_CONTEXT_UNKNOWNS",
    "MAX_TOGGLE_FIXTURE_OPPORTUNITIES",
    "MAX_WORLD_OPPORTUNITIES",
    "MOVE_VERB",
    "OBJECT_REF_PREFIX",
    "PICK_UP_VERB",
    "PLACEMENT_ROOM",
    "PLACEMENT_SURFACE",
    "PLACE_REF_PREFIX",
    "PLACE_VERB",
    "POSE_VERB",
    "POSTURE_SECONDS",
    "PURCHASE_REF_PREFIX",
    "PURCHASE_SECONDS",
    "PURCHASE_VERB",
    "RESERVED_BROWSE_PLACES",
    "REST_VERB",
    "STAY_DESCRIPTOR_PREFERENCE",
    "STAY_DESCRIPTOR_QUIET",
    "StayDefinitionError",
    "STAY_IDLE_PHRASES",
    "STAY_IDLE_ROTATION_SECONDS",
    "STAY_QUIET_SECONDS",
    "STAY_REF_PREFIX",
    "STAY_SECONDS_BY_AFFORDANCE",
    "STAY_VERB",
    "TOGGLE_FIXTURE_VERB",
    "TOILET_VERB",
    "WorldMoveOpportunityProvider",
    "observed_arrange_opportunities",
    "observed_body_action_opportunities",
    "observed_consume_opportunities",
    "observed_toilet_opportunities",
    "observed_daily_action_opportunities",
    "observed_move_opportunities",
    "observed_toggle_fixture_opportunities",
    "observed_purchase_opportunities",
    "observed_stay_opportunities",
    "stay_definition_status",
    "stay_definition_status_for",
    "opportunity_view",
    "parse_stay_target",
    "read_location_catalog",
    "read_store_projection",
    "stay_target",
]

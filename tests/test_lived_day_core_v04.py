#!/usr/bin/env python3
"""v0.4 主线 A（连续日常生活）：现场生活 context / prompt 容量 / 拿放路线的精准用例。

本文件是 A 的定向证据，**不继承**既有 TestCase。三块：

1. ``LiveScenePromptTest``（原生 Windows 也能跑）：B 的 ``daily_life_context`` 契约与观测回退都
   被压成有界、带名字的现场（在哪里/手上什么/能去哪+路程+营业/实际可做的准备），未知标未知；
   这份现场进的是 ``ProductionAgencyCognitionAdapter.build_prompt`` 的**真实** payload，不是报告字段。
2. ``PromptCapacityTest``（同上）：prompt 超限时**结构化裁剪**，序列化结果必须 ``json.loads`` 成功、
   candidate id / legal_decisions / target_refs 与现场保留；旧 ``text[:N] + ...(truncated)"}``
   伪 JSON 不再出现；裁剪报告只有计数。
3. ``LivedDayRouteTest``（POSIX）：``dailyact:`` 从 Agenda 决策走进 AG-1 -> AG-2 -> AR-0 的
   step/adoption 形状，完成判据来自**动作后**的一次 fresh owner 观测（命令成功 ≠ 事实）；
   cycle 的 ``decision_situation_context`` 把现场放进 ``world``。

``life_daily_action_targets`` 在 B 落地前由**契约替身**（本文件内，明确标注）提供：这是为了让 A 的
路由与完成核对可测，产品代码里没有任何第二份编码。

需要 POSIX（fcntl）的部分在原生 Windows 自动跳过。夹具根保留不删。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_live_context_view as llcv  # noqa: E402
import agency_cognition_adapter as aa  # noqa: E402
import life_daily_action_targets as daily  # noqa: E402  (B 的真实编码契约)

#: 所有夹具的唯一根：建一次，永不删除（失败也留证）。
FIXTURE_ROOT = Path(tempfile.mkdtemp(prefix="nyairov04a-livedday-"))

#: 只依赖 typing 的部分（live view / cognition adapter）原生 Windows 可跑；其余经 fcntl。
_LIFE_ERROR: str | None = None
try:
    import autonomous_life_cycle as alc  # noqa: E402
except Exception as error:  # noqa: BLE001 - 环境限制，不是产品行为
    _LIFE_ERROR = "%s: %s" % (type(error).__name__, error)

needs_life = unittest.skipIf(_LIFE_ERROR is not None,
                             "Life 模块需要 POSIX（fcntl）：%s" % (_LIFE_ERROR or ""))


def _state_root(prefix: str = "lived-day-") -> Path:
    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix=prefix, dir=str(FIXTURE_ROOT)))
    return root


# ---------------------------------------------------------------------------------------
# 0. 一份"真实形状"的世界观测与目录（测试声明的事实，不是产品推断）
# ---------------------------------------------------------------------------------------

MOMENT = "2026-11-13T09:30:00+00:00"


def _catalog(*, shop_open: bool = True) -> dict:
    return {
        "status": "available",
        "world_id": "lived_day_world",
        "local_time": MOMENT,
        "current_place_id": "home",
        "opportunities": [
            {"location_id": "home", "name": "家", "kind": "home", "here": True,
             "reachability": {"status": "here", "walk_seconds": 0},
             "availability": {"status": "OPEN"},
             "observed": {"status": "visible_here"},
             "affordances": [{"affordance_id": "desk", "description": "窗边的桌子"}]},
            {"location_id": "corner_shop", "name": "街角小店", "kind": "shop", "here": False,
             "reachability": {"status": "reachable", "walk_seconds": 240},
             "availability": {"status": "OPEN" if shop_open else "CLOSED",
                              "reason_code": None if shop_open else "OUTSIDE_OPENING_HOURS"},
             "opening_expectation": {"status": "OPEN_UNTIL_18:00"},
             "observed": {"status": "not_observed", "reason_code": "OUT_OF_VIEW"},
             "affordances": [{"affordance_id": "counter", "description": "柜台上的零钱罐"}]},
            {"location_id": "park", "name": "小公园", "kind": "outdoor", "here": False,
             "reachability": {"status": "reachable", "walk_seconds": 600},
             "availability": {"status": "OPEN"},
             "observed": {"status": "not_observed", "reason_code": "OUT_OF_VIEW"},
             "affordances": []},
        ],
    }


def _observation(*, pose: str = "standing", hands=None, revision: int = 11,
                 placement=None) -> dict:
    """She is at home; ``placement`` says where the bowl really sits in the owner's snapshot."""

    hands = {"left_hand": None, "right_hand": None} if hands is None else dict(hands)
    bowl = {"object_id": "bowl-01", "label": "饭碗", "kind": "room", "in_hand": False}
    if isinstance(placement, dict):
        bowl.update(dict(placement))
    visible = [bowl,
               {"object_id": "book-01", "label": "手边的书", "kind": "room", "in_hand": False}]
    if hands.get("left_hand"):
        for row in visible:
            if row["object_id"] == hands["left_hand"]:
                row["in_hand"] = True
    return {
        "world_id": "lived_day_world",
        "world_revision": revision,
        "location": {"place_id": "home", "area_id": "room"},
        "pose": pose,
        "orientation": "south",
        "hands": hands,
        "visible_objects": visible,
        "visible_object_count": len(visible),
    }


def _view(*, hands=None) -> list[dict]:
    return [
        {"opportunity_id": "opp:move:corner_shop", "verb": "MOVE", "kind": "MOVE",
         "target_ref": "place:corner_shop", "location_id": "corner_shop", "observed": True},
        {"opportunity_id": "opp:pick:bowl", "verb": "PICK_UP", "kind": "PICK_UP",
         "target_ref": _pick_target(), "location_id": "home", "object_id": "bowl-01",
         "observed": True},
        {"opportunity_id": "opp:place:bowl", "verb": "PLACE", "kind": "PLACE",
         "target_ref": _place_target(), "location_id": "home",
         "object_id": "bowl-01", "observed": True},
        {"opportunity_id": "opp:stay:park", "verb": "STAY", "kind": "STAY",
         "target_ref": "stay:park:quiet:600", "location_id": "park", "affordance": "长椅",
         "stay_seconds": 600, "observed": True},
        {"opportunity_id": "opp:pose", "verb": "POSE", "kind": "POSE",
         "target_ref": "bodyact:pose:seated:north", "location_id": "home",
         "params": {"pose": "seated", "orientation": "north"}, "pose": "seated",
         "orientation": "north", "observed": True},
    ]


class _ObservationProvider:
    """World seam 的替身：只做"观测一次然后被读"，不给任何建议。"""

    def __init__(self, *, observation=None, catalog=None, view=None) -> None:
        self.last_observation = dict(observation) if observation else None
        self.last_catalog = dict(catalog) if catalog else None
        self.last_opportunity_view = list(view or [])
        self.last_available_actions = None
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return []


class _ContractProvider(_ObservationProvider):
    """发布了 B 契约 ``daily_life_context(limit=...)`` 的 seam。"""

    def __init__(self, payload, **kwargs) -> None:
        super().__init__(**kwargs)
        self.payload = payload
        self.limits: list = []

    def daily_life_context(self, *, limit=8):
        self.limits.append(int(limit))
        return dict(self.payload)


# ---------------------------------------------------------------------------------------
# 1. 现场生活 context：契约优先，观测回退同样有界、带名字、未知标未知
# ---------------------------------------------------------------------------------------


class LiveSceneContractTest(unittest.TestCase):
    """B 的契约就是唯一来源；这里只压大小，不改语义。"""

    def test_the_published_contract_is_used_and_only_bounded(self):
        payload = {
            "status": "observed",
            "observed_at": MOMENT,
            "location": {"place_id": "home", "name": "家", "area_id": "room", "here": True,
                         "local_time": MOMENT},
            "body": {"pose": "standing", "orientation": "south",
                     "hands": [{"slot": "left_hand", "object_id": "bowl-01", "name": "饭碗"}],
                     "hands_known": True},
            "nearby": [{"place_id": "corner_shop", "name": "街角小店", "walk_seconds": 240,
                        "availability": "OPEN", "observed": "not_observed"}],
            "opportunities": [
                {"target_ref": _pick_target(), "verb": "PICK_UP", "label": "端起饭碗",
                 "description": "把饭碗拿在手里", "duration_seconds": 20,
                 "availability": "OPEN", "preparation": "需要空出一只手"},
                {"target_ref": "stay:park:quiet:600", "verb": "STAY", "label": "坐着待一会儿",
                 "duration_seconds": 600},
            ],
            "unknowns": ["OPENING_HOURS_NOT_READ"],
        }
        provider = _ContractProvider(payload)
        context = llcv.read_live_context(provider, limit=8)
        self.assertEqual(provider.limits, [8])
        self.assertEqual(context["source"], llcv.SOURCE_CONTRACT)
        self.assertEqual(context["location"]["name"], "家")
        self.assertEqual(context["body"]["hands"][0]["object_id"], "bowl-01")
        self.assertEqual(context["nearby"][0]["walk_seconds"], 240)
        self.assertEqual(context["opportunities"][0]["preparation"], "需要空出一只手")
        self.assertEqual(context["opportunities"][1]["duration_seconds"], 600)
        self.assertIn("OPENING_HOURS_NOT_READ", context["unknowns"])

    def test_the_contract_bound_is_enforced_here_not_only_downstream(self):
        payload = {"status": "observed",
                   "nearby": [{"place_id": "p%d" % i, "name": "地方%d" % i} for i in range(20)],
                   "opportunities": [{"target_ref": "dailyact:pick_up:o%d" % i, "verb": "PICK_UP"}
                                     for i in range(20)]}
        context = llcv.read_live_context(_ContractProvider(payload), limit=3)
        self.assertEqual(len(context["nearby"]), 3)
        self.assertEqual(len(context["opportunities"]), 3)

    def test_an_unusable_contract_answer_is_named_not_silently_empty(self):
        provider = _ObservationProvider()
        provider.daily_life_context = lambda *, limit=8: "not a mapping"
        context = llcv.read_live_context(provider)
        self.assertEqual(context["status"], llcv.STATUS_UNAVAILABLE)
        self.assertEqual(context["reason_code"], llcv.REASON_CONTRACT_SHAPE)


class LiveSceneFallbackTest(unittest.TestCase):
    """契约还没发布时的桥：同一份观测 + 同一份目录，不重观测、不猜。"""

    def setUp(self):
        self.provider = _ObservationProvider(observation=_observation(), catalog=_catalog(),
                                             view=_view())

    def test_the_place_is_named_and_the_area_and_local_time_are_kept(self):
        context = llcv.read_live_context(self.provider, limit=8, observed_at=MOMENT)
        self.assertEqual(context["source"], llcv.SOURCE_FALLBACK)
        self.assertEqual(context["location"]["place_id"], "home")
        self.assertEqual(context["location"]["name"], "家")
        self.assertEqual(context["location"]["area_id"], "room")
        self.assertEqual(context["location"]["here"], True)
        self.assertEqual(context["observed_at"], MOMENT)

    def test_hands_are_reported_with_their_names_and_empty_hands_are_empty(self):
        empty = llcv.read_live_context(self.provider, limit=8)
        self.assertEqual(empty["body"]["hands"], [])
        self.assertTrue(empty["body"]["hands_known"])
        holding = _ObservationProvider(observation=_observation(hands={"left_hand": "bowl-01"}),
                                       catalog=_catalog(), view=_view())
        context = llcv.read_live_context(holding, limit=8)
        self.assertEqual(context["body"]["hands"],
                         [{"slot": "left_hand", "object_id": "bowl-01", "name": "饭碗"}])

    def test_places_she_could_walk_to_keep_walking_time_and_the_opening_verdict(self):
        context = llcv.read_live_context(self.provider, limit=8)
        by_id = {row["place_id"]: row for row in context["nearby"]}
        self.assertEqual(by_id["corner_shop"]["name"], "街角小店")
        self.assertEqual(by_id["corner_shop"]["walk_seconds"], 240)
        self.assertEqual(by_id["corner_shop"]["availability"], "OPEN")
        self.assertEqual(by_id["corner_shop"]["open_expectation"], "OPEN_UNTIL_18:00")
        self.assertEqual(by_id["park"]["walk_seconds"], 600)
        self.assertNotIn("home", by_id, "the place she is in is not one she could walk to")

    def test_a_place_she_is_not_in_never_borrows_details_from_inside_it(self):
        context = llcv.read_live_context(self.provider, limit=8)
        shop = [row for row in context["nearby"] if row["place_id"] == "corner_shop"][0]
        self.assertEqual(shop["observed"], "not_observed")
        self.assertNotIn("premises", shop, "OUT_OF_VIEW must not become 'I saw the counter'")
        self.assertIn(llcv.UNKNOWN_OPPORTUNITY_DETAIL, context["unknowns"])

    def test_the_concrete_offers_carry_meaning_not_only_a_ref(self):
        context = llcv.read_live_context(self.provider, limit=8)
        by_ref = {row["target_ref"]: row for row in context["opportunities"]}
        self.assertEqual(by_ref[_pick_target()]["verb"], "PICK_UP")
        self.assertEqual(by_ref[_pick_target()]["label"], "饭碗")
        self.assertEqual(by_ref["stay:park:quiet:600"]["duration_seconds"], 600)
        self.assertEqual(by_ref["bodyact:pose:seated:north"]["params"],
                         "orientation=north, pose=seated")
        self.assertNotIn("place:corner_shop", by_ref, "a place is described once, not twice")

    def test_an_unread_catalog_names_the_unknown_instead_of_inventing_a_place(self):
        provider = _ObservationProvider(observation=_observation(), catalog=None, view=[])
        context = llcv.read_live_context(provider, limit=8)
        self.assertEqual(context["status"], llcv.STATUS_PARTIAL)
        self.assertEqual(context["nearby"], [])
        self.assertIn(llcv.UNKNOWN_PLACE_NAMES, context["unknowns"])

    def test_no_observation_at_all_is_a_named_absence_not_an_empty_world(self):
        context = llcv.read_live_context(_ObservationProvider(), limit=8)
        self.assertEqual(context["status"], llcv.STATUS_UNAVAILABLE)
        self.assertIn(llcv.REASON_NO_OBSERVATION, context["unknowns"])


# ---------------------------------------------------------------------------------------
# 2. prompt：现场进真实 payload；超限结构化裁剪后仍然是一份完整 JSON
# ---------------------------------------------------------------------------------------


def _request(**overrides):
    candidates = [
        {"candidate_id": "cand:work-1", "candidate_kind": "CONSIDER_PERSONAL_OPPORTUNITY",
         "source_kind": "ARTIFACT_CONTINUATION", "source_ref": "artifact:work-1",
         "target_refs": ["artifact:work-1"], "legal_decisions": ["START", "DEFER", "NO_ACTION"],
         "candidate_revision": "rev-9", "bounded_description": "继续自己那篇没写完的东西"},
        {"candidate_id": "cand:pick-bowl", "candidate_kind": "CONSIDER_WORLD_OPPORTUNITY",
         "source_kind": "WORLD_OPPORTUNITY", "source_ref": "world:opportunity:pick",
         "target_refs": [_pick_target()],
         "legal_decisions": ["START", "DEFER", "NO_ACTION"], "bounded_description": "端起饭碗"},
    ]
    base = dict(
        opportunity_id="opp:1", candidate_set_id="cset:1",
        policy_version="policy:v1",
        legal_decision_enums=["START", "CONTINUE", "PAUSE", "WAIT", "RESUME", "ABANDON",
                             "DEFER", "NO_ACTION"],
        allowed_reason_codes=["R" * 24] * 40,
        current_activity_summary={"activity_ref": "activity:work-1", "status": "ACTIVE"},
        capacity_summary={"body_capacity_state": "AVAILABLE", "resource_availability_state": "UNKNOWN"},
        observed_fact_keys=["world_here_place_home"],
        candidates=candidates,
        prior_experiences=[],
        situation_context={},
        max_output_tokens=512,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _adapter(**kwargs):
    client = SimpleNamespace(provider="stub", model="stub-model")
    return aa.ProductionAgencyCognitionAdapter(client=client, **kwargs)


def _live_situation():
    provider = _ObservationProvider(observation=_observation(hands={"left_hand": "bowl-01"}),
                                    catalog=_catalog(), view=_view())
    context = llcv.read_live_context(provider, limit=8, observed_at=MOMENT)
    return {"schema_version": "chiyo.life.decision_situation.v1",
            "world": context,
            "reading": {"state": "AVAILABLE", "material_title": "窗边的书", "page": 2,
                        "next_page": 3},
            "activity": {"activity_ref": "activity:work-1", "status": "ACTIVE",
                         "step_available": True},
            "park": {"available": True, "how": "choose WAIT and set later_item.park_current=true"},
            "later_items": [{"item_ref": "fitem:1", "title": "回来接着读", "due": False}],
            "project_options": [{"option_ref": "opt:1", "title": "想写的东西", "kind": "PROJECT",
                                 "seed_excerpt": "把窗外的街写成一段"}],
            "practice": {"methods": [{"method_ref": "m:1", "title": "先读一遍再写",
                                      "instruction": "先读，再写一段"}]},
            "now": MOMENT}


class LiveScenePromptTest(unittest.TestCase):
    def test_the_live_scene_reaches_the_real_prompt_with_names(self):
        adapter = _adapter()
        text, payload = adapter.build_prompt(_request(situation_context=_live_situation()))
        world = payload["situation_context"]["world"]
        self.assertEqual(world["location"]["name"], "家")
        self.assertEqual(world["body"]["hands"][0]["name"], "饭碗")
        self.assertEqual({row["place_id"] for row in world["nearby"]},
                         {"corner_shop", "park"})
        self.assertTrue(any(row["walk_seconds"] == 240 for row in world["nearby"]))
        self.assertTrue(any(row["verb"] == "PICK_UP" for row in world["opportunities"]))
        self.assertEqual(json.loads(text), payload)
        self.assertIn("家", text, "the place name is in the text sent, not just in a report")

    def test_the_prompt_says_this_is_one_continuous_day(self):
        prompt = aa.SYSTEM_PROMPT
        self.assertIn("situation_context.world", prompt)
        self.assertIn("one continuous day", prompt)
        self.assertIn("unknown", prompt)


class PromptCapacityTest(unittest.TestCase):
    BIG = "研究材料片段" * 200

    def _flooded(self):
        situation = _live_situation()
        situation["research"] = {
            "status": "available",
            "materials": [{"artifact_id": "art:%d" % i, "title": self.BIG,
                           "excerpt": self.BIG, "source_revision": "rev%d" % i}
                          for i in range(6)],
            "results": [{"research_id": "res:%d" % i, "summary": self.BIG} for i in range(4)],
        }
        situation["practice"] = {
            "methods": [{"method_ref": "m:%d" % i, "title": self.BIG,
                         "instruction": self.BIG, "scope": self.BIG,
                         "uncertainty": self.BIG} for i in range(3)],
            "experiences": [{"experience_id": "e:%d" % i, "excerpt": self.BIG} for i in range(4)],
        }
        situation["project_options"] = [
            {"option_ref": "opt:%d" % i, "title": self.BIG, "kind": "PROJECT",
             "seed_excerpt": self.BIG, "scope": self.BIG, "uncertainty": self.BIG,
             "materials": [{"artifact_id": "m:%d" % i, "excerpt": self.BIG}],
             "experiences": [{"experience_id": "e:%d" % i, "excerpt": self.BIG}]}
            for i in range(4)]
        situation["later_items"] = [{"item_ref": "fitem:%d" % i, "title": self.BIG}
                                    for i in range(6)]
        situation["questions"] = [{"note": self.BIG, "from": "artifact:work-1"}]
        return _request(situation_context=situation,
                        prior_experiences=[{"experience_id": "x:%d" % i, "text": self.BIG}
                                           for i in range(3)])

    def test_an_over_long_prompt_is_cut_structurally_and_still_parses(self):
        adapter = _adapter(max_prompt_chars=4000)
        text, payload = adapter.build_prompt(self._flooded())
        self.assertLessEqual(len(text), 4000)
        parsed = json.loads(text)                    # 旧实现的伪 JSON 在这里就会炸
        self.assertEqual(parsed, payload)
        self.assertNotIn("...(truncated)", text)
        self.assertIn("trimmed", adapter.last_prompt_trim)
        self.assertTrue(adapter.last_prompt_trim["trimmed"])
        self.assertFalse(adapter.last_prompt_trim["over_budget"])

    def test_the_choice_is_still_possible_after_the_trim(self):
        adapter = _adapter(max_prompt_chars=4000)
        _, payload = adapter.build_prompt(self._flooded())
        candidates = payload["candidates"]
        self.assertGreaterEqual(len(candidates), 2)
        for row in candidates:
            self.assertTrue(row.get("candidate_id"))
            self.assertTrue(row.get("target_refs"), "a candidate without its target cannot be chosen")
            self.assertTrue(row.get("legal_decisions"), "a candidate without legal verbs cannot be chosen")
        self.assertIn("START", payload["legal_decision_enums"])
        self.assertTrue(payload["observed_fact_keys"])

    def test_the_live_scene_survives_a_trim_that_drops_the_research_flood(self):
        adapter = _adapter(max_prompt_chars=4000)
        _, payload = adapter.build_prompt(self._flooded())
        world = (payload.get("situation_context") or {}).get("world") or {}
        self.assertEqual(world.get("location", {}).get("name"), "家")
        self.assertTrue(world.get("opportunities"))
        self.assertTrue(world.get("nearby"))
        self.assertNotIn("research", payload.get("situation_context") or {})

    def test_the_trim_report_carries_counts_only_never_prompt_text(self):
        adapter = _adapter(max_prompt_chars=4000)
        adapter.build_prompt(self._flooded())
        report = adapter.last_prompt_trim
        blob = json.dumps(report, ensure_ascii=False)
        self.assertNotIn(self.BIG[:20], blob)
        for row in report["dropped"]:
            self.assertEqual(set(row), {"step", "count"})
            self.assertIsInstance(row["count"], int)


# ---------------------------------------------------------------------------------------
# 3. 目标与完成事实一律来自 B 的真实契约（``life_daily_action_targets``）
# ---------------------------------------------------------------------------------------


def _pick_target() -> str:
    """PICK_UP 的 target 由 B 自己的编码器给出（A 不复制编码）。"""

    return daily.encode_daily_action_target(
        "PICK_UP", {"object_id": "bowl-01", "slot_id": "left_hand"})


def _place_target() -> str:
    """PLACE 到桌面的 target，同样来自 B 的编码器。"""

    return daily.encode_daily_action_target(
        "PLACE", {"object_id": "bowl-01",
                  "target": {"kind": "surface", "location_id": "home",
                             "surface_id": "desk-01"}})


# ---------------------------------------------------------------------------------------
# 4. cycle：现场进 situation context；dailyact 走真实 step/adoption 形状
# ---------------------------------------------------------------------------------------


class _ActivityRead:
    def __init__(self, activities=None, foreground=None):
        self.activities = {str(k): dict(v) for k, v in (activities or {}).items()}
        self.foreground = foreground

    def get_current_life_view(self):
        kind = (self.activities.get(str(self.foreground)) or {}).get("activity_kind")
        return {"foreground_activity_ref": self.foreground, "foreground_activity_kind": kind}

    def get_activity(self, activity_id):
        record = self.activities.get(str(activity_id))
        return dict(record) if record else None

    def list_open_activities(self):
        return [dict(row) for row in self.activities.values()
                if str(row.get("status") or "") in {"ACTIVE", "PAUSED", "WAITING"}]


class _CandidateRead:
    def __init__(self, candidates=None):
        self.candidates = dict(candidates or {})

    def get_candidate(self, candidate_id):
        return dict(self.candidates.get(str(candidate_id)) or {}) or None


class _ProgressPort:
    def __init__(self):
        self.evidence = []

    def ingest_progress_evidence(self, evidence):
        self.evidence.append(evidence)
        return {"progress_state": {"progress_state": "IN_PROGRESS"},
                "checkpoint_update": {"checkpoint_update_id": "cp:1", "checkpoint_ref": "cp:1"}}


class _OpportunitySource:
    def __init__(self):
        self.recorded: list[dict] = []
        self.expired: list[str] = []

    def record_observed_opportunity(self, item):
        self.recorded.append(dict(item))

    def expire_opportunity(self, opportunity_id, *, observed_at=None):
        self.expired.append(str(opportunity_id))


class _Runtime:
    """只放 cycle 真正会读的那几个口；owner 的写入全在真实 runtime 的用例里。"""

    def __init__(self, *, state_root, candidates=None, activities=None, foreground=None,
                 provider=None):
        self.state_root = Path(state_root)
        self.world_opportunity_provider = provider
        self.world_opportunity_source = _OpportunitySource()
        self.candidate_read = _CandidateRead(candidates)
        self.activity_read = _ActivityRead(activities, foreground)
        self.ag2_progress_port = _ProgressPort()
        self.world_action_adapter = object()
        self.kill_switches = SimpleNamespace(to_dict=lambda: {})
        self.artifact_action_adapter = None


def _cycle(runtime):
    return alc.AutonomousLifeCycle(runtime, enabled=True, env={},
                                   world_opportunity_provider=runtime.world_opportunity_provider)


CANDIDATE_PICK = {"candidate_id": "cand:pick-bowl",
                  "candidate_kind": "CONSIDER_WORLD_OPPORTUNITY",
                  "source_kind": "WORLD_OPPORTUNITY",
                  "source_ref": "world:opportunity:pick",
                  "target_refs": [_pick_target()],
                  "legal_decisions": ["START", "DEFER", "NO_ACTION"]}


@needs_life
class LivedDayRouteTest(unittest.TestCase):
    def setUp(self):
        self.root = _state_root()
        self.provider = _ObservationProvider(observation=_observation(), catalog=_catalog(),
                                             view=_view())
        self.runtime = _Runtime(state_root=self.root, candidates={"cand:pick-bowl": CANDIDATE_PICK},
                                provider=self.provider)
        self.cycle = _cycle(self.runtime)


    def _decision(self, candidate="cand:pick-bowl", decision="START"):
        return SimpleNamespace(decision=decision, selected_candidate_ref=candidate,
                               reason_codes=[], short_rationale="scripted")

    # -- the live scene is in the situation context the model actually gets -------------

    def test_the_cycle_puts_the_live_scene_in_the_situation_context(self):
        self.cycle._refresh_world_opportunities()
        context = self.cycle.decision_situation_context(now=MOMENT)
        world = context["world"]
        self.assertEqual(world["status"], llcv.STATUS_PARTIAL)
        self.assertEqual(world["location"]["name"], "家")
        self.assertEqual(world["location"]["place_id"], "home")
        self.assertTrue(world["body"].get("hands_known"))
        self.assertTrue(any(row["walk_seconds"] == 240 for row in world["nearby"]))
        self.assertNotIn(llcv.UNKNOWN_OPENING, world["unknowns"])

    def test_a_situation_without_an_observation_says_so_instead_of_showing_an_empty_world(self):
        quiet = _ObservationProvider()
        runtime = _Runtime(state_root=self.root, provider=quiet)
        cycle = _cycle(runtime)
        context = cycle.decision_situation_context(now=MOMENT)
        self.assertEqual(context["world"]["status"], llcv.STATUS_UNAVAILABLE)
        self.assertIn(llcv.REASON_NO_OBSERVATION, context["world"]["unknowns"])

    # -- dailyact: real step shape, real adoption contract -----------------------------

    def test_a_dailyact_candidate_is_routed_to_the_targets_own_step_builder(self):
        self.cycle._refresh_world_opportunities()
        self.assertTrue(self.cycle._candidate_is_world(CANDIDATE_PICK))
        step = self.cycle._world_step_spec(self._decision())
        self.assertEqual(step["target_ref"], _pick_target())
        self.assertEqual(step["payload_spec"]["verb"], "PICK_UP")
        self.assertEqual(step["payload_spec"]["target"], _pick_target())
        self.assertIn("idem_step", step["idempotency_key"])

    def test_the_adoption_contract_names_the_targets_own_completion_fact(self):
        spec = self.cycle._world_adoption_spec(
            [{"location_ref": "place:home", "target_refs": [_pick_target()]}],
            decision=self._decision())
        completion = spec["completion_contract_spec"]
        self.assertEqual(completion["criteria"]["fact_key"],
                         daily.daily_action_completion(_pick_target())["fact_key"])
        self.assertEqual(completion["criteria"]["expected_value"],
                         daily.daily_action_completion(_pick_target())["expected_value"])
        self.assertEqual(spec["activity_kind"], alc.DAILY_ACTION_ACTIVITY_KIND)

    # -- the fact comes from a fresh owner observation, never from the command ---------

    def _classify(self, observation, *, verb="PICK_UP", target=None):
        target = _pick_target() if target is None else target
        self.provider.last_observation = dict(observation)
        action = {"action_id": "action:1", "frozen_payload": {"verb": verb, "target": target}}
        base = {"activity_ref": "activity:daily-1", "correlation_id": "corr:1"}
        return self.cycle._classify_daily_action(base, verb=verb, action=action)

    def test_a_pick_up_counts_only_when_the_fresh_snapshot_shows_it_in_hand(self):
        result_hit = self._classify(_observation(hands={"left_hand": "bowl-01"}, revision=12))
        self.assertEqual(result_hit["outcome"], alc.OUTCOME_ACTED, result_hit)
        self.assertTrue(result_hit["daily_action"]["observation"]["re_read"])
        self.assertTrue(result_hit["daily_action"]["satisfied"])
        self.assertEqual(result_hit["daily_action"]["observed"]["object_id"], "bowl-01")
        self.assertEqual(len(self.runtime.ag2_progress_port.evidence), 1)
        evidence = self.runtime.ag2_progress_port.evidence[0]
        fact = daily.daily_action_completion(_pick_target())
        self.assertEqual(evidence.observed_facts[fact["fact_key"]], fact["expected_value"])
        self.assertEqual(evidence.authority_domain, "world_authority")

    def test_a_pick_up_the_owner_does_not_show_is_unsettled_and_writes_nothing(self):
        result = self._classify(_observation(hands={"left_hand": None}, revision=13))
        self.assertEqual(result["outcome"], alc.OUTCOME_STEP_UNSETTLED, result)
        self.assertFalse(result["daily_action"]["satisfied"])
        self.assertEqual(self.runtime.ag2_progress_port.evidence, [])

    def test_a_place_is_judged_the_other_way_round(self):
        placed = self._classify(
            _observation(hands={"left_hand": None}, revision=14,
                         placement={"kind": "surface", "surface_id": "desk-01"}),
            verb="PLACE", target=_place_target())
        self.assertEqual(placed["outcome"], alc.OUTCOME_ACTED, placed)
        still_held = self._classify(
            _observation(hands={"left_hand": "bowl-01"}, revision=15,
                         placement={"kind": "surface", "surface_id": "desk-01"}),
            verb="PLACE", target=_place_target())
        self.assertEqual(still_held["outcome"], alc.OUTCOME_STEP_UNSETTLED, still_held)

    def test_an_unreadable_world_after_the_action_is_unsettled_not_a_success(self):
        self.provider.last_observation = None
        self.cycle.world_opportunity_provider = self.provider
        action = {"action_id": "action:2",
                  "frozen_payload": {"verb": "PICK_UP", "target": _pick_target()}}
        base = {"activity_ref": "activity:daily-1", "correlation_id": "corr:2"}
        result = self.cycle._classify_daily_action(base, verb="PICK_UP", action=action)
        self.assertEqual(result["outcome"], alc.OUTCOME_STEP_UNSETTLED, result)
        self.assertFalse(result["daily_action"]["observation"]["re_read"])
        self.assertEqual(self.runtime.ag2_progress_port.evidence, [])

    def test_a_target_the_contract_refuses_is_a_named_refusal(self):
        action = {"action_id": "action:3",
                  "frozen_payload": {"verb": "PICK_UP", "target": "dailyact:fly:away"}}
        base = {"activity_ref": "activity:daily-1", "correlation_id": "corr:3"}
        result = self.cycle._classify_daily_action(base, verb="PICK_UP", action=action)
        self.assertEqual(result["outcome"], alc.OUTCOME_STEP_REFUSED, result)

    def test_a_missing_targets_module_is_named_and_nothing_is_invented(self):
        with mock.patch.dict(sys.modules, {"life_daily_action_targets": None}):
            with self.assertRaises(Exception) as caught:
                alc.daily_action_targets_module()
        self.assertIn("DAILY_ACTION_TARGETS_MODULE_UNAVAILABLE", str(caught.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)

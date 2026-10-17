#!/usr/bin/env python3
"""街区连续生活：**同一实例**在现有 ``daily_neighborhood_v1`` 上真的走过多个地点再回家。

这一段不是"一天"，也不假装是一天：它是同一份数据根、同一个 runtime、同一批真实本地 owner
（真实 Supply socket、真实 ``WorldBodyService`` + 真 ``gateway.sock``、真实 ``daily_neighborhood_v1``
包、真实 Body 后果、真实 M0/Memory 准入、真实 AG-0/AG-1/AG-2 与 Activity/LR-5/LR-4 owner）上的一段
可数 episode，只有"模型"是确定性替身，从不联网。

**只有一个注入时刻**：``Clock.now``（``INJECTED_CLOCK``）就是这一整段唯一的"当下"——每轮的
``observed_at``、每次出发与到达的耗时推进、以及每一份营业事实的读取（provider 的 catalog 时刻与
Life 时刻是同一个值）都取它。它只随本文件显式 ``advance`` 前进；世界自己的营业/到达仍由 World
owner 的事实说了算。

链（家 → 门前街 → 街口 → 公园 → 街口 → 咖啡店 → 街口 → 门前街 → 阅读点 → 门前街 → 家）：
每一段都是一次**真实单跳出发**（出发≠到达：owner 公布的 due 之前位置不变，due 之后才到达），
候选只来自 owner 此刻给出的单跳动作空间——Life 里没有"远处的可选地图"。阅读点 / 公园 / 咖啡店
各自只在到场之后才观察到店内事实；每一段的身份（``journey_id``/``segment_status``/due）都来自
owner 自己的记录。

两处**条件驱动的改变**（同一条固定替身规则，只读当轮候选与 owner 的营业事实，路由固定在替身自己
的出门计划里，不在产品里）：

  A. 街口 07:23，咖啡店 ``CLOSED``（工作日 8–18）→ 这一轮**不去**，什么都不做（``NO_ACTION``，
     前台空、0 副作用）；同一个计划推进到 17:56 之后，同一条规则读到 ``OPEN`` → 真的走过去。
  B. 咖啡店里 17:58 开始一次停留（``SIT_AT_CAFE`` 300s）；18:00 店关门之后，停留的 checkpoint 被
     **安全边界拒绝**（``WORLD_PLACE_CLOSED_AT_STAY_CHECKPOINT``，不写"她还坐在关着门的店里"），
     随后那一轮模型读到"这儿关门了"的事实，主动 ``ABANDON``：停留**提前结束**、``completed:false``、
     剩余秒数如实写成剩余——不靠篡改 elapsed 或伪造完成事实来释放前台。

公园那次停留是**自然完成**的对照：START → ONGOING（未到点不许完成）→ END（到点且世界仍在那儿，
``COMPLETION_ELIGIBLE``，前台释放）。停留是 Life 自己的内部 checkpoint，不发 World SIT/REST Action、
不写 Body 奖励、不强制产出。

出厂前那段原私人事项照旧：真实 Supply 新版本 → 合法 safe-boundary 打断（真实共处）停在保存点 →
出门 → 回家 RESUME 原资格与 checkpoint → CONTINUE 落成真实新版本。本次 World 后果（公园那次停留）
经 AG-2 既有 handoff 进真实 M0，用的是中性 World 模板，并且真的出现在下一次 DecisionRequest 的
``prior_experiences`` 里。

``UNKNOWN`` 只用一次真实故障注入：第一段出发的答复丢了（``LostAnswerResolver``），统一走
``cycle.reconcile_unknown_step``（同一 action/execution，不重发 START、不 sleep 碰绿）。

不继承任何既有 TestCase；夹具以**组合**方式复用 ``tests/test_world_daily_life_continuous.py`` 的
``EpisodeFixture`` 实例（只读 import，不改它）。所有 home 落在 ``/tmp/nyaw05r.jdK3q/nb-life-*``
（runner 把 ``shutil.rmtree`` 置 no-op），保留不删。
"""

from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge"),
              str(ROOT / "components" / "supply"), str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from test_one_day_life import posix_only  # noqa: E402
import test_world_daily_life_continuous as continuous  # noqa: E402

FIXTURE_ROOT = Path("/tmp/nyaw05r.jdK3q")
STARTABLE_KINDS = frozenset({
    "RESPOND_USER_REQUEST", "CONSIDER_WORLD_OPPORTUNITY", "FULFILL_COMMITMENT",
    "PURSUE_GOAL_STEP", "CONSIDER_PERSONAL_OPPORTUNITY",
})

#: The stroll the stand-in model intends for itself, as *hops* -- this is the model's own
#: bounded plan (a person has one before going out), not a product route: Life never sees it,
#: never stores it, and every hop is still a candidate the World owner has to offer first.
NEIGHBORHOOD_PLAN = (
    ("go", "street_front"),
    ("go", "street_corner"),
    ("go", "park"),
    ("stay", None),          # 公园长椅：自然完成的那一次
    ("go", "street_corner"),
    ("go", "cafe"),
    ("stay", None),          # 咖啡店：被"店关门了"这个事实提前结束的那一次
    ("go", "street_corner"),
    ("go", "street_front"),
    ("go", "reading_point"),
    ("go", "street_front"),
    ("go", "home"),
)


def _parse(iso_text: str) -> datetime:
    moment = datetime.fromisoformat(str(iso_text).replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


class ScriptedNeighborhoodCognition:
    """Deterministic stand-in with **one** intention (a hop plan) and one rule per round.

    It is told nothing about which candidate to click.  Every round it reads what the request
    really carries -- the candidates AG-1 offered and their per-candidate legal decisions, the
    owner's own ``observed_fact_keys`` (``world_place_<place>_availability_<STATUS>`` and
    ``world_here_place_<place>``) and the admitted experiences -- and applies one rule:

      1. if a stay is running, the only legal continuation of its round is that stay:
         ``CONTINUE`` while its place is still open, ``ABANDON`` the moment the owner says the
         place is **CLOSED** (sitting in a shut shop is not something the World says is
         happening, so she stops instead of claiming it);
      2. otherwise take the next hop of the plan -- but only if the World really offers it in
         this round's single-hop candidate set *and* does not call that place CLOSED;
      3. if the next hop is not offered or is CLOSED, do nothing at all (``NO_ACTION``): a
         closed shop is not a reason to invent a route or a second activity.

    What it read each round is recorded in ``self.reads`` so the assertions can show the read
    really drove the choice.
    """

    model_id = "stub:nb-neighborhood"
    model_version = "nb-stub-v1"

    def __init__(self, plan=NEIGHBORHOOD_PLAN) -> None:
        self.plan = tuple(plan)
        self.index = 0
        self.goal = ""
        self.call_count = 0
        self.reads: list[dict] = []
        self.offered: list[list] = []
        self.stays_taken = 0
        self.abandons = 0
        self.holds = 0

    # -- request reading ----------------------------------------------------------

    @staticmethod
    def _refs(candidate: dict) -> list[str]:
        return [str(ref) for ref in (candidate.get("target_refs") or [])]

    @staticmethod
    def _legal(candidate: dict) -> list[str]:
        return [str(item) for item in (candidate.get("legal_decisions") or [])]

    @staticmethod
    def _facts(keys) -> dict:
        found: dict = {}
        for key in keys or ():
            text = str(key)
            prefix = "world_place_"
            marker = "_availability_"
            if not text.startswith(prefix) or marker not in text:
                continue
            place, _, status = text[len(prefix):].partition(marker)
            if place and status:
                found[place] = status
        return found

    @staticmethod
    def _here(keys) -> str:
        for key in keys or ():
            text = str(key)
            if text.startswith("world_here_place_"):
                return text[len("world_here_place_"):]
        return ""

    @property
    def next_hop(self):
        return self.plan[self.index] if self.index < len(self.plan) else None

    # -- the decision -------------------------------------------------------------

    def invoke(self, request, *, schema_retry_index: int = 0):
        import agency_decision_ag1 as ag1

        self.call_count += 1
        candidates = [dict(c) for c in (getattr(request, "candidates", []) or [])]
        keys = [str(key) for key in (getattr(request, "observed_fact_keys", []) or [])]
        facts = self._facts(keys)
        here = self._here(keys)
        experiences = [dict(item) for item in
                       (getattr(request, "prior_experiences", []) or [])
                       if isinstance(item, dict)]
        allowed = [str(code) for code in (getattr(request, "allowed_reason_codes", []) or [])]
        self.offered.append([
            {"kind": c.get("candidate_kind"), "targets": self._refs(c),
             "legal": self._legal(c), "candidate_id": c.get("candidate_id")}
            for c in candidates])
        chosen, decision, reason = self._choose(candidates, facts, here)
        if chosen is not None and decision == "START" and any(
                ref.startswith("stay:") for ref in self._refs(chosen)):
            self.stays_taken += 1
        if decision == "ABANDON":
            self.abandons += 1
        if chosen is None:
            self.holds += 1
        self.reads.append({
            "here": here, "facts": dict(facts), "fact_keys": keys,
            "experiences": [dict(item) for item in experiences],
            "hop": self.next_hop,
            "offered_targets": sorted({ref for c in candidates for ref in self._refs(c)}),
            "chose": self._refs(chosen)[0] if chosen else None,
            "decision": decision, "reason": reason})
        if chosen is None:
            raw = {"decision": "NO_ACTION", "selected_candidate_id": None,
                   "reason_codes": allowed[:1], "short_rationale": reason[:200],
                   "deferred_candidate_ids": []}
        else:
            raw = {"decision": decision, "selected_candidate_id": chosen.get("candidate_id"),
                   "reason_codes": allowed[:1], "short_rationale": reason[:200],
                   "deferred_candidate_ids": []}
        return ag1.CognitiveDecisionResponse(
            model_run_ref="model_run:stub:%d" % self.call_count, model_id=self.model_id,
            model_version=self.model_version, raw_output=raw, latency_ms=1)

    def _choose(self, candidates: list[dict], facts: dict, here: str) -> tuple:
        running = next((candidate for candidate in candidates
                        if "CONTINUE" in self._legal(candidate)), None)
        if running is not None:
            status = facts.get(here) or "NOT_READ"
            if status == "CLOSED" and "ABANDON" in self._legal(running):
                return (running, "ABANDON",
                        "world_place_%s_availability_CLOSED: 不在这儿继续待着了" % here)
            return (running, "CONTINUE", "continue the stay that is running")
        startable = [candidate for candidate in candidates
                     if "START" in self._legal(candidate)
                     and candidate.get("candidate_kind") in STARTABLE_KINDS]
        hop = self.next_hop
        if hop is None:
            return (None, "", "the plan for this outing is done")
        kind, place = hop
        if kind == "go":
            status = facts.get(place, "NOT_READ")
            if status == "CLOSED":
                return (None, "", "world_place_%s_availability_CLOSED: 这一趟不去那儿" % place)
            for candidate in startable:
                if "place:%s" % place in self._refs(candidate):
                    self.index += 1
                    return (candidate, "START", "the next hop of my own plan: %s" % place)
            return (None, "", "%s is not on offer from here" % place)
        if kind == "stay":
            for candidate in startable:
                if any(ref.startswith("stay:") for ref in self._refs(candidate)):
                    self.index += 1
                    return (candidate, "START", "stay here for a while")
            return (None, "", "this place offers no stay")
        return (None, "", "nothing legal was offered")


class NeighborhoodFixture:
    """One instance, one runtime, one injected clock, one real World service (composition).

    ``tests/test_world_daily_life_continuous.EpisodeFixture`` is reused as an **object**, not as
    a base class: it carries no test of its own and the two modules must not inherit each
    other's drill.  What this class adds is the neighbourhood part: the hop-plan stand-in, one
    real single-hop travel with its owner-side evidence, one bounded stay, and the checks that
    keep the fixture directories on disk.
    """

    def __init__(self) -> None:
        self.host = continuous.EpisodeFixture("runTest")
        self.host.setUp()
        self.cognition = ScriptedNeighborhoodCognition()
        self.host.cognition = self.cognition
        self.host.runtime.decision_service.cognition_adapter = self.cognition
        self.edges: list[dict] = []

    def __getattr__(self, name):
        if name == "host":
            raise AttributeError(name)
        return getattr(self.host, name)

    def close(self) -> None:
        """Tear the instance down in the fixture's own order, but keep every directory."""
        base = getattr(self.host, "base", None)
        directories = getattr(base, "_temp_dirs", None)
        if isinstance(directories, list):
            directories.clear()   # the batch contract: fixtures are retained, not deleted
        try:
            self.host.doCleanups()
        except Exception:
            pass

    # -- one real single-hop travel ----------------------------------------------

    def travel(self, place: str) -> dict:
        """One departure toward an adjacent place, settled at the owner's own due instant.

        What is asserted here is the World's own contract, not this module's bookkeeping: a
        departure is **not** an arrival (the observed position is still the origin until the
        owner's due instant has passed), the destination the owner announces is the one asked
        for, and the edge really has a positive duration.
        """
        origin = self.host.current_place()
        self.host.provider()          # one bounded observation at this instant (no model call)
        view = next((item for item in (self.host.provider.last_opportunity_view or [])
                     if item.get("target_ref") == "place:%s" % place), {})
        walk = int(view.get("walk_seconds") or 0)
        out = self.host.round("nb:go:%s" % place, because="go:%s" % place)
        reconciled: dict = {}
        if out.get("outcome") == "STEP_UNKNOWN":
            # 同一次执行对账：不重发 START、不 sleep 碰绿，结论是 owner 自己的账本。
            reconciled = self.host.cycle.reconcile_unknown_step(
                action_id=out.get("action_id"), occurred_at=self.host.clock.iso(),
                reason="nb:go:%s" % place)
            self.host.assertTrue(reconciled.get("reconciled"), reconciled)
            self.host.assertEqual(reconciled.get("action_status"), "SUCCEEDED", reconciled)
            self.host.assertNotEqual(reconciled.get("reason_code"), "STILL_UNKNOWN", reconciled)
            journey = dict(reconciled.get("journey") or {})
        else:
            self.host.assertEqual(out.get("outcome"), "IN_TRANSIT", out)
            self.host.assertEqual(out.get("world_verb"), "MOVE", out)
            journey = dict(out.get("journey") or {})
        self.host.assertTrue(journey, out)
        destination = (journey.get("destination_location_id")
                       or journey.get("target_location_id"))
        self.host.assertEqual(destination, place, journey)
        due = str(journey.get("expected_arrival_at") or "") or None
        if journey.get("arrived"):
            # 对账当时，owner 的账本已经把这趟走完了：没有再"等到期"，位置就是目的地。
            settled = reconciled
            arrived_via = "RECONCILED"
        else:
            self.host.assertTrue(due, journey)
            # 出发 ≠ 到达：owner 公布的 due 之前，她仍在原来那个地方。
            self.host.assertEqual(self.host.current_place(), origin,
                                  "a departure must not already be an arrival")
            settled = self.host.cycle.settle_world_journey(
                observed_at=_parse(due).isoformat())
            self.host.assertTrue(settled.get("ok"), settled)
            self.host.assertTrue(settled.get("arrived"), settled)
            arrived_via = "DUE"
        self.host.assertEqual(self.host.current_place(), place, settled)
        self.host.assertGreater(walk, 0, view)   # positive-duration edge, from the owner
        self.host.clock.advance(walk, because="walk:%s" % place)
        # ``remaining_seconds`` 是 owner 自己在出发时说的"这一段还要走多久"——这正是**这一段**
        # 的正时长。owner 的 ``expected_arrival_at`` 用的是它自己的墙钟，而 ``departed_at`` 是
        # 本段唯一的注入时刻；拿这两个相减就是双时钟造假，所以不做。
        edge = {"from": origin, "to": place, "walk_seconds": walk,
                "remaining_seconds": journey.get("remaining_seconds"),
                "departed_at": out.get("observed_at"), "expected_arrival_at": due,
                "segment_status": journey.get("segment_status"),
                "arrived_via": arrived_via,
                "reconciled_unknown": bool(reconciled), "arrived": True}
        self.edges.append(edge)
        return edge

    def use(self, cognition) -> None:
        """Swap the stand-in model of this instance (the *adapter* is what the cycle calls)."""
        self.cognition = cognition
        self.host.cognition = cognition
        self.host.runtime.decision_service.cognition_adapter = cognition

    # -- stays --------------------------------------------------------------------

    def start_stay(self, trigger: str) -> dict:
        """One bounded, non-productive stay, begun the way a person begins one: by deciding."""
        started = self.host.round(trigger, because=trigger)
        self.host.assertEqual(started.get("outcome"), "ACTED", started)
        info = dict(started.get("stay") or {})
        self.host.assertEqual(info.get("phase"), "START", info)
        self.host.assertGreater(int(info.get("planned_seconds") or 0), 0, info)
        activity = str(started.get("activity_ref") or "")
        self.host.assertTrue(activity, started)
        self.host.assertEqual(self.host.foreground(), activity)
        self.host.assertEqual(self.host.activity(activity).get("status"), "ACTIVE")
        return {"round": started, "stay": info, "activity": activity}

    def finish_stay(self, handle: dict) -> dict:
        """The declared stretch really elapsed: only then may the World-end confirm it."""
        planned = int(handle["stay"]["planned_seconds"])
        half = max(1, planned // 2)
        ongoing = self.host.round("nb:stay:ongoing", advance=half, because="stay:ongoing")
        self.host.assertEqual(ongoing.get("outcome"), "ACTED", ongoing)
        self.host.assertEqual((ongoing.get("stay") or {}).get("phase"), "ONGOING", ongoing)
        self.host.assertNotEqual(ongoing.get("progress_state"), "COMPLETION_ELIGIBLE", ongoing)
        ended = self.host.round("nb:stay:end", advance=planned, because="stay:end")
        self.host.assertEqual(ended.get("outcome"), "ACTED", ended)
        info = dict(ended.get("stay") or {})
        self.host.assertEqual(info.get("phase"), "END", info)
        self.host.assertGreaterEqual(int(info.get("elapsed_seconds") or 0), planned, info)
        self.host.assertEqual(ended.get("progress_state"), "COMPLETION_ELIGIBLE", ended)
        self.host.assertIsNone(self.host.foreground())
        self.host.assertEqual(self.host.activity(handle["activity"]).get("status"), "COMPLETED")
        return ended

    # -- owner reads ---------------------------------------------------------------

    def checkpoints(self, activity: str) -> list[dict]:
        return [dict(item) for item in
                (self.host.runtime.progress_read.list_checkpoint_updates(str(activity)) or [])]

    def world_rows(self) -> list:
        """The admitted M0 rows that carry a World fact (never a document version)."""
        return [row for row in (self.host.memory.port.recent(limit=50) or [])
                if "world_fact:" in str(getattr(row, "text", "") or "")]


@posix_only
class NeighborhoodLifeTest(unittest.TestCase):
    """The drill: one short *neighbourhood* episode through the real owners."""

    def setUp(self) -> None:
        self.fixture = NeighborhoodFixture()
        self.addCleanup(self.fixture.close)

    def test_one_instance_walks_the_neighborhood_and_comes_back_to_her_own_work(self):
        field = self.fixture
        data = field.data
        work_id = data["work_artifact_id"]
        print("NB CLOCK kind=%s now=%s weekday=%s hour=%s"
              % (field.clock.kind, field.clock.base.isoformat(), field.clock.base.weekday(),
                 field.clock.base.astimezone(continuous.JST).hour))
        self.assertEqual(field.clock.kind, "INJECTED_CLOCK")
        self.assertLess(field.clock.base.weekday(), 5)
        self.assertEqual(field.clock.base.astimezone(continuous.JST).hour, 7)

        # -- 0) 没有真实材料：一个模型调用都不该发生 --------------------------------
        saved = (field.cycle.world_opportunity_provider, field.cycle.artifact_port,
                 field.cycle.reading_artifact_id)
        field.cycle.world_opportunity_provider = lambda: []
        field.cycle.artifact_port = None
        field.cycle.reading_artifact_id = None
        quiet = field.round("nb:quiet")
        self.assertEqual(quiet.get("outcome"), "NO_MATERIAL", quiet)
        self.assertEqual(quiet.get("llm_calls"), 0, quiet)
        self.assertEqual(field.cognition.call_count, 0, field.cognition.call_count)
        field.cycle.world_opportunity_provider, field.cycle.artifact_port, \
            field.cycle.reading_artifact_id = saved

        # -- 1) 出门前：一次真实作品推进（AR-0 → 真 Supply 新版本） -----------------
        head_before = field.head(work_id)["version_id"]
        field.host.runtime.decision_service.cognition_adapter = field.base._work_cognition(
            work_id, data["reading_artifact_id"])
        work_round = field.round("nb:work")
        self.assertEqual(work_round.get("outcome"), "ACTED", work_round)
        head_after_work = field.head(work_id)["version_id"]
        self.assertNotEqual(head_after_work, head_before)
        private_activity = str(work_round["activity_ref"])
        self.assertTrue(private_activity)

        # -- 2) 合法 safe-boundary 打断：私人 Activity 停在保存点，前台释放 ----------
        import life_co_presence as cop

        protocol = cop.FiniteCoPresence(
            field.runtime, state_root=field.state_root, subject_id=data["subject_id"],
            home=str(field.state_root), cycle=field.cycle)
        invitation = cop.parse_invitation({
            "event_kind": cop.KIND_INVITATION, "invitation_id": "inv:nb:1",
            "source_ref": "channel:nb-drill", "occurred_at": field.clock.iso(),
            "scope": "一起待一会儿", "requires_engagement": True})
        self.assertEqual(protocol.consider(invitation, now=field.clock.iso()).get("decision"),
                         cop.DECISION_ACCEPT)
        started = protocol.begin(invitation, now=field.clock.iso())
        self.assertEqual(started["adoption"]["adoption_status"], "APPLIED", started["adoption"])
        parked = field.activity(private_activity)
        self.assertEqual(parked.get("status"), "WAITING", parked)
        checkpoint_at_break = parked.get("checkpoint_ref")
        self.assertTrue(checkpoint_at_break, parked)
        ended = protocol.end(cop.parse_end({
            "event_kind": cop.KIND_END, "invitation_id": "inv:nb:1",
            "occurred_at": field.clock.advance(300, because="co-presence")
            .replace("+00:00", "Z"),
            "explicit_end": True}), now=field.clock.iso())
        self.assertTrue(ended.get("ok"), ended)
        eligibility = ended["new_eligibility_refs"][0]
        self.assertEqual(eligibility["status"], "OPEN", eligibility)
        self.assertEqual(eligibility["activity_id"], private_activity, eligibility)
        self.assertEqual(ended["evaluation"]["llm_calls"], 0, ended["evaluation"])
        self.assertIsNone(field.foreground(), field.runtime.canonical_state())
        print("NB INTERRUPT parked=%s checkpoint=%s eligibility=%s"
              % (parked["status"], checkpoint_at_break, eligibility["eligibility_id"]))

        # -- 3) 出门：第一段出发的答复真的丢了，用同一次执行对账 --------------------
        field.runtime.decision_service.cognition_adapter = field.cognition
        self.assertEqual(field.cognition.plan, NEIGHBORHOOD_PLAN)
        first = field.travel("street_front")
        self.assertTrue(first["reconciled_unknown"], first)
        self.assertEqual(field.lost_answer.lost, 1, field.lost_answer.lost)
        self.assertEqual(field.runtime.action_read.list_actions(status="UNKNOWN"), [])

        # -- 4) 街口 → 公园：公园那一次停留**自然完成** ---------------------------
        field.travel("street_corner")
        field.travel("park")
        self.assertEqual(field.current_place(), "park")
        park = field.start_stay("nb:park:stay")
        self.assertEqual(park["stay"].get("place"), "park", park["stay"])
        self.assertEqual(park["stay"].get("affordance"), "SIT", park["stay"])
        self.assertEqual(int(park["stay"].get("planned_seconds")), 300, park["stay"])
        finished = field.finish_stay(park)
        print("NB STAY-NATURAL place=park planned=%s elapsed=%s progress=%s"
              % (park["stay"].get("planned_seconds"),
                 (finished.get("stay") or {}).get("elapsed_seconds"),
                 finished.get("progress_state")))
        park_updates = field.checkpoints(park["activity"])
        self.assertTrue(park_updates, park_updates)
        self.assertTrue(any("checkpoint:stay:" in str(item.get("new_checkpoint_ref") or "")
                            or "world_fact:stay:" in str(item.get("new_checkpoint_ref") or "")
                            for item in park_updates), park_updates)

        # -- 5) 街口 07:23：咖啡店 CLOSED（工作日 8–18）→ 这一轮**不去** -----------
        field.travel("street_corner")
        hold = field.round("nb:cafe:closed", because="cafe:closed")
        read = field.cognition.reads[-1]
        self.assertEqual(read["facts"].get("cafe"), "CLOSED", read)
        self.assertIn("world_place_cafe_availability_CLOSED", read["fact_keys"], read)
        # 走得到（owner 的 action space 给出 MOVE），所以它的缺席不是拓扑造成的
        self.assertIn("place:cafe", read["offered_targets"], read)
        self.assertIsNone(read["chose"], read)
        self.assertEqual(hold.get("outcome"), "NO_ACTION", hold)
        self.assertEqual(hold.get("llm_calls"), 1, hold)
        self.assertIsNone(field.foreground(), field.runtime.canonical_state())
        self.assertEqual(field.current_place(), "street_corner")
        self.assertEqual(field.cognition.index, NEIGHBORHOOD_PLAN.index(("go", "cafe")),
                         field.cognition.index)
        print("NB HOLD facts=%s offered_place_cafe=%s reason=%s"
              % (json.dumps(read["facts"]), "place:cafe" in read["offered_targets"],
                 read["reason"]))

        # -- 6) 同一个计划推进到傍晚：同一条规则读到 OPEN → 真的走过去 --------------
        field.clock.advance(10 * 3600 + 33 * 60, because="afternoon")
        field.travel("cafe")
        self.assertEqual(field.current_place(), "cafe")
        open_read = field.cognition.reads[-1]
        self.assertEqual(open_read["facts"].get("cafe"), "OPEN", open_read)
        self.assertEqual(open_read["chose"], "place:cafe", open_read)
        # -- 7) 咖啡店里的停留：店关门之后主动放弃（提前结束，不冒充待满）---------
        cafe = field.start_stay("nb:cafe:stay")
        self.assertEqual(cafe["stay"].get("affordance"), "SIT_AT_CAFE", cafe["stay"])
        self.assertEqual(int(cafe["stay"].get("planned_seconds")), 300, cafe["stay"])
        # 时间推到 18:00：world 包自己写的 `close_hour=18` 到点，这一轮 owner 的事实说 CLOSED，
        # 同一条规则于是主动结束这次停留——不是等到 300s 再假装待满。
        abandoned = field.round("nb:cafe:closes", advance=180, because="cafe:closes")
        leave = dict(abandoned.get("stay") or {})
        self.assertEqual(abandoned.get("outcome"), "ABANDONED", abandoned)
        self.assertEqual(abandoned.get("decision"), "ABANDON", abandoned)
        self.assertEqual(abandoned.get("step_path"), "STAY_ENDED_BY_CHOICE", abandoned)
        self.assertEqual((abandoned.get("world_facts") or {}).get("world_place_cafe_availability_CLOSED"),
                         "CLOSED", abandoned.get("world_facts"))
        self.assertEqual(leave.get("phase"), "ENDED_EARLY", leave)
        self.assertEqual(leave.get("exit_decision"), "ABANDON", leave)
        self.assertIs(leave.get("completed"), False, leave)
        self.assertIs(leave.get("completion_claimed"), False, leave)
        elapsed = int(leave.get("elapsed_seconds") or 0)
        self.assertGreater(elapsed, 0, leave)
        self.assertLess(elapsed, int(cafe["stay"]["planned_seconds"]), leave)
        self.assertEqual(int(leave.get("remaining_seconds") or 0),
                         int(cafe["stay"]["planned_seconds"]) - elapsed, leave)
        self.assertEqual(leave.get("owner_status"), "ABANDONED", leave)
        self.assertEqual(leave.get("owner_terminal_reason_code"), "ABANDONED_BY_DECISION",
                         leave)
        self.assertTrue(leave.get("owner_ended_at"), leave)
        # 起止都来自真实事实：起点是 LR-5 记下的第一个 checkpoint，终点是这一轮的
        # observed_at；两个时刻是同一个瞬间的两种拼写，比较时刻、不比较字符串。
        self.assertIsNotNone(leave.get("started_at"), leave)
        self.assertLessEqual(
            abs((_parse(leave["started_at"])
                 - _parse(cafe["stay"]["started_at"])).total_seconds()), 1.0,
            (leave.get("started_at"), cafe["stay"].get("started_at")))
        self.assertIs(leave.get("resumable_as_stay"), False, leave)
        self.assertIsNone(field.foreground(), field.runtime.canonical_state())
        self.assertEqual(field.activity(cafe["activity"]).get("status"), "ABANDONED")
        # 提前结束**不是**待满：这条 Activity 上没有任何"到点"的 checkpoint。
        cafe_updates = field.checkpoints(cafe["activity"])
        self.assertTrue(cafe_updates, cafe_updates)
        self.assertEqual(
            [item for item in cafe_updates
             if str(item.get("new_checkpoint_ref") or "").startswith("checkpoint:stay:")], [],
            cafe_updates)
        print("NB STAY-EARLY place=cafe planned=%s elapsed=%s remaining=%s owner=%s/%s"
              % (cafe["stay"].get("planned_seconds"), elapsed,
                 leave.get("remaining_seconds"), leave.get("owner_status"),
                 leave.get("owner_terminal_reason_code")))

        # -- 8) 提前结束之后新的 MOVE 合法，旧停留不再占前台 -------------------------
        field.travel("street_corner")
        self.assertEqual(field.current_place(), "street_corner")
        self.assertIsNone(field.foreground(), field.runtime.canonical_state())
        field.travel("street_front")
        field.travel("reading_point")
        self.assertEqual(field.current_place(), "reading_point")
        # 到场这件事由一次真实观察确认：`stay:...:READ_HERE:600` 只在她真的站在店里、且
        # owner 说此刻 OPEN 时才出现（同一个地方在 07:23 的那一轮里是 CLOSED）。
        field.provider()
        observed = [str(item.get("target_ref"))
                    for item in (field.provider.last_opportunity_view or [])]
        self.assertIn("stay:reading_point:READ_HERE:600", observed, observed)
        self.assertEqual(
            str((field.provider.last_observation or {}).get("location", {}).get("place_id")),
            "reading_point")
        catalog = {str(item.get("location_id")): item
                   for item in (field.provider.last_catalog or {}).get("opportunities") or []}
        self.assertEqual((catalog["reading_point"]["availability"]).get("status"), "OPEN")
        self.assertEqual((catalog["cafe"]["availability"]).get("status"), "CLOSED")
        print("NB READING here=reading_point stays_offered=%s cafe=%s"
              % ([ref for ref in observed if ref.startswith("stay:")],
                 catalog["cafe"]["availability"].get("status")))

        # -- 9) 回家：RESUME 原保存点 → CONTINUE 落成真实新版本 --------------------
        field.travel("street_front")
        field.travel("home")
        self.assertEqual(field.current_place(), "home")
        self.assertIsNone(field.foreground(), field.runtime.canonical_state())
        requests: list[dict] = []
        field.runtime.decision_service.cognition_adapter = continuous.RecordingCognition(
            field.base._resume_cognition_for(work_id), requests)
        resumed = field.round("nb:resume")
        self.assertEqual(resumed.get("decision"), "RESUME", resumed)
        self.assertEqual(resumed.get("adoption_status"), "APPLIED", resumed)
        back = field.activity(private_activity)
        self.assertEqual(back.get("status"), "ACTIVE", back)
        self.assertEqual(back.get("checkpoint_ref"), checkpoint_at_break, back)
        self.assertEqual(resumed.get("continuity_consumed_eligibility_ref"),
                         eligibility["eligibility_id"], resumed)
        field.runtime.decision_service.cognition_adapter = field.base._continue_cognition()
        head_before_continue = field.head(work_id)["version_id"]
        continued = field.round("nb:continue")
        self.assertEqual(continued.get("outcome"), "ACTED", continued)
        self.assertNotEqual(field.head(work_id)["version_id"], head_before_continue)

        # -- 10) 本次 World 后果进真实 M0，并真的出现在下一次决策输入里 --------------
        rows = field.world_rows()
        self.assertTrue(rows, [str(getattr(row, "text", ""))[:80]
                               for row in (field.memory.port.recent(limit=10) or [])])
        stay_row = next((row for row in rows
                         if "world_fact:stay:park" in str(getattr(row, "text", "") or "")), None)
        self.assertIsNotNone(stay_row, [str(getattr(row, "text", ""))[:90] for row in rows])
        stay_text = str(getattr(stay_row, "text", "") or "")
        self.assertIsNone(stay_row.artifact_ref, stay_row)
        self.assertNotRegex(stay_text, "真实修改|新版本")
        self.assertTrue(requests, "the resume round must have made one request")
        carried = json.dumps(requests[-1].get("experiences") or [], ensure_ascii=False)
        # 被准入的那一行真的进了下一次 DecisionRequest（模型看到的那份形状）。
        self.assertIn("world_fact:stay:park:SIT:300", carried)
        self.assertIn(stay_text, carried)
        self.assertTrue([key for key in (requests[-1].get("fact_keys") or [])
                         if str(key).startswith("world_place_")], requests[-1])
        print("NB M0-WORLD row=%s owner_result=%s text=%s"
              % (stay_row.source_ref, stay_row.owner_result, stay_text[:70]))

        # -- 11) 段的身份与正时长：每条边都来自 owner 自己的单跳事实 -----------------
        self.assertEqual(len(field.edges), 10, field.edges)
        for edge in field.edges:
            self.assertGreater(edge["walk_seconds"], 0, edge)
            self.assertTrue(edge["arrived"], edge)
            self.assertIn(edge["arrived_via"], {"DUE", "RECONCILED"}, edge)
            # 到期那一刻由 owner 自己公布；对账时已经走完的那一段不再有 due。
            if edge["arrived_via"] == "DUE":
                self.assertTrue(edge["expected_arrival_at"], edge)
                # owner 自己说的"这一段还剩多少秒"就是这条边的正时长（不是和注入时钟相减）。
                self.assertGreater(int(edge["remaining_seconds"] or 0), 0, edge)
                # owner 按自己的墙钟算 remaining，向上取整到整秒，所以允许几秒的取整误差。
                self.assertLessEqual(int(edge["remaining_seconds"]), edge["walk_seconds"] + 5,
                                     edge)
        hops = [(edge["from"], edge["to"]) for edge in field.edges]
        self.assertEqual(hops, [
            ("home", "street_front"), ("street_front", "street_corner"),
            ("street_corner", "park"), ("park", "street_corner"),
            ("street_corner", "cafe"), ("cafe", "street_corner"),
            ("street_corner", "street_front"), ("street_front", "reading_point"),
            ("reading_point", "street_front"), ("street_front", "home")], hops)
        # 90+120+180+180+120+120+120+150+150+90：每一条边都是正时长，全部来自 owner 的拓扑事实。
        self.assertEqual(sum(edge["walk_seconds"] for edge in field.edges), 1320)
        print("NB EDGES %s" % json.dumps(
            [{key: edge[key] for key in ("from", "to", "walk_seconds", "segment_status")}
             for edge in field.edges], ensure_ascii=False))
        print("NB CLOCK advances=%d now=%s" % (len(field.clock.advances), field.clock.iso()))
        print("NB DONE")


if __name__ == "__main__":
    unittest.main()

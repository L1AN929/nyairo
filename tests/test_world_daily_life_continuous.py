#!/usr/bin/env python3
"""I-r2：**同一实例的一段短程连续 episode**（``INJECTED_CLOCK``）——出门前保存点 → 走到门前街
（相邻书店**闭店**，于是改主意就地待一会儿）→ 回家 → 用掉一份 → 私人保存点续步 → 本次 World
后果经 M0 进入下一次输入 → 暂停。

这不是"一天"，也不假装是一天：它是一段可数的短程连续 episode，跑在真实本地 owner 上（真实
Life Supply socket 服务、真实 ``WorldBodyService`` + 真 ``gateway.sock``、真实 World 包
``examples/worlds/daily_neighborhood_v1``、真实 Body 后果、真实 M0/Memory 准入、真实
Activity/LR-5/LR-4 owner），只有"模型"是确定性替身，从不联网、从不跑真实产品模型。

**只有一个注入时刻**：``Clock.now`` 就是这一整段唯一的"当下"——每一轮的 ``observed_at``、每一次
出行、以及每一份营业事实的读取（``WorldMoveOpportunityProvider`` 的 catalog 时刻与 Life 时刻是
同一个 ``Clock.now``）都取它。它只随本文件显式 ``advance`` 前进；世界自己的营业/到达仍由 World
owner 的事实说了算，本文件不替它宣布任何时刻。

场景（同一个实例、同一份数据根、一个 runtime）：
  0. 没有任何真实材料 → ``NO_MATERIAL``、0 模型调用；
  1. 一次真实作品推进（AR-0 → 真 Supply 新版本 → AG-2 handoff → M0 行 VERIFIED）；
  2. 一次合法 safe-boundary 打断（真实共处邀请）把私人 Activity 停在保存点，前台释放；
  3. 出门：观察同时给出"能走到的地方"和"能吃的那一份"，决策选择**不消费**直接出门，
     那一份仍在原地、没有回执、没有 Body 后果；
  4. 到了门前街再读一次 owner 的当下事实：她去不了的那家书店（``reading_point``，就在门前街
     边上）此刻 **CLOSED**。确定性模型**读到的就是这条来源事实**（AG-1 的
     ``observed_fact_keys`` 里 ``world_place_reading_point_availability_CLOSED``），于是不往
     关着的那一支走，改成就地待一会儿——一个它自己从合法候选里选出的、真的会执行的动作；
  5. 门前街的停留：一次真实 START（checkpoint 由 LR-5 自己记），提前一轮**不许**完成，声明
     时长真的到了之后才由世界当前观察确认结束，前台释放；
  6. 回家（路上暂停一次：暂停后 0 调用/0 出发，已出发段照常到达，预算身份不重置）；
  7. 在家里用掉**一份**可食物件：owner 回执 + Body 恰好一条后果（非营养）；
  8. 用 owner 自己发的 RESUME 资格回到原私人 Activity，检查点仍是打断前那一个，随后
     CONTINUE 真的落成 Supply 新版本；
  9. 本次 World 后果（用掉的那一份 / 停留）经 AG-2 既有的 experience handoff 进真实 M0，行文用
     **中性**的 World 模板（不再说"完成了一次真实修改，作品所有者确认了新版本"）；
 10. 那条被准入的行进入下一次 ``prior_experiences`` 并**真的改变一次合法选择**：同一个"还想
     吃"的意图下，读到 owner 自己的 ``world_fact:object:<id>:consumed`` 就地待着，而不是出门
     再找（作品行/空行不产生这个偏向）；随后 RESUME 回原保存点、CONTINUE 落成 Supply 新版本，
     历史行决定续写对象。

``UNKNOWN`` 的处理与产品入口同一条：同一 ``action_id`` / 同一 execution id 先对账
（``reconcile_unknown_step``），由 owner 自己的账本定胜负，再结算**原来那个** Activity；本文件
不 sleep 重试、不重新 ``START``、不直写 journey/Activity。

不继承任何既有 TestCase（避免误收旧模块的几十个用例）；夹具以组合方式复用
``tests/test_life_v1_integration.py``（只读 import，不改它）。所有 home 落在
``/tmp/nyaw05r.jdK3q/ir1-continuous-*`` 并**保留**。
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone
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

import action_intent  # noqa: E402
import action_reality_ledger as ar0  # noqa: E402
import autonomous_life_cycle as alc  # noqa: E402
import life_co_presence as cop  # noqa: E402
import one_day_life as odl  # noqa: E402
import test_life_v1_integration as donor  # noqa: E402
import world_body_substrate as wbs  # noqa: E402
import world_move_opportunity_source as wmos  # noqa: E402
from world_body_client import WorldBodyClient  # noqa: E402

WORLD_PACKAGE = ROOT / "examples" / "worlds" / "daily_neighborhood_v1"
BRIDGE_DIR = ROOT / "components" / "world" / "service" / "bridge"
FIXTURE_ROOT = Path("/tmp/nyaw05r.jdK3q")
FOOD = "i-food-riceball-01"
SUBJECT = "test:i-r1"
JST = timezone(timedelta(hours=9))

STARTABLE_KINDS = frozenset({
    "RESPOND_USER_REQUEST", "CONSIDER_WORLD_OPPORTUNITY", "FULFILL_COMMITMENT",
    "PURSUE_GOAL_STEP", "CONSIDER_PERSONAL_OPPORTUNITY",
})
#: The World owner's own consumed fact, exactly as the consumer writes it into a memory row.
_WORLD_CONSUMED = re.compile(r"world_fact:object:([A-Za-z0-9_.:+-]+?):consumed")


def episode_instant(*, after: datetime | None = None) -> datetime:
    """The **one** injected instant of this episode: the next weekday 07:00 (JST).

    Why 07:00 on a weekday is not arbitrary: the world package's own rules make the bookstore
    (``reading_point``, 10-20) and the cafe (8-18, weekdays only) closed at that hour while the
    street and home are open, so "that place is shut right now" is the owner's deterministic
    fact rather than a race with a wall clock.  The same value is handed to the World catalog
    read *and* used as the Life clock, so this episode never mixes two "now"s.
    """

    anchor = (after or datetime.now(timezone.utc)).astimezone(JST) + timedelta(minutes=10)
    day = anchor.date()
    while True:
        candidate = datetime(day.year, day.month, day.day, 7, 0, tzinfo=JST)
        if candidate.weekday() < 5 and candidate > anchor:
            return candidate
        day = day + timedelta(days=1)


class Clock:
    """The explicit INJECTED_CLOCK: one instant, only these calls move it.

    ``now`` is the single "now" of the episode: the round's ``observed_at``, the moment every
    World observation and opening fact is read at, the moment a departure starts from and the
    moment a stay is measured on.  Nothing else advances it and nothing derives it from a
    result -- so a pass cannot come from two contradictory clocks.
    """

    def __init__(self, base: datetime) -> None:
        self.base = base
        self.now = base
        self.kind = "INJECTED_CLOCK"
        self.advances: list[dict] = []

    def iso(self) -> str:
        return self.now.astimezone(timezone.utc).isoformat()

    def advance(self, seconds: int, *, because: str = "") -> str:
        self.now = self.now + timedelta(seconds=int(seconds))
        self.advances.append({"seconds": int(seconds), "at": self.iso(), "because": because})
        return self.iso()


class ScriptedWorldCognition:
    """Deterministic stand-in with **one declared intention** and one fixed rule per round.

    It is not told what to pick.  Every round it reads the request the way a model would --
    the candidates AG-1 really offered, the sourced ``observed_fact_keys`` (the World's own
    opening verdicts, e.g. ``world_place_reading_point_availability_CLOSED``), the place the
    observation says she is in (``world_here_place_<place>``), the current activity and the
    admitted experiences -- and applies the same rule to whatever it finds:

      1. a running activity is the only legal continuation -> ``CONTINUE``;
      2. with the "eat" intention, a consumed object that is really here -> ``CONSUME``;
      3. with a place intention:
         a. the wished place is offered and *open* -> go there;
         b. she is already there and it offers a stay -> take it;
         c. the wished place is reachable but the World says it is **CLOSED** -> do not go;
            wait it out here once (a stay is still a real thing to do);
         d. otherwise head for the first open place the observation offers;
      4. any other legal stay/place, else ``NO_ACTION``.

  5. and only from the **admitted history**: if a World row already names the owner's own
     ``world_fact:object:<id>:consumed`` fact, the declared appetite is answered -- she does
     not leave to look for another one.  Memory is allowed to say "that already happened";
     it is never allowed to stand in for a position, a permission, a fact about the body or
     a fact about who she is.

    The only thing the drill sets is the *intention* (``goal``), once per phase of the episode
    -- exactly the way a person's own intention is a fact about the episode, not a per-round
    instruction about which candidate to click.  What it reads each round is recorded in
    ``self.reads`` so the assertions can show the read really happened.
    """

    model_id = "stub:i-r2-world"
    model_version = "i-r2-stub-v1"

    def __init__(self) -> None:
        self.goal = ""
        self.call_count = 0
        self.stays_taken = 0
        self.offered: list[list] = []
        self.reads: list[dict] = []

    # -- request reading -----------------------------------------------------------

    @staticmethod
    def _refs(candidate: dict) -> list[str]:
        return [str(ref) for ref in (candidate.get("target_refs") or [])]

    @staticmethod
    def _legal(candidate: dict) -> list[str]:
        return [str(item) for item in (candidate.get("legal_decisions") or [])]

    @staticmethod
    def _facts(keys) -> dict:
        """``world_place_<place>_availability_<STATUS>`` keys -> ``{place: STATUS}``.

        This is the whole point of the fact channel: the model reads the World owner's own
        verdict out of the request instead of guessing it from which offers are missing.
        """

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

    @staticmethod
    def _wish(goal: str) -> str:
        return goal.split(":", 1)[1] if ":" in goal else ""

    # -- the decision --------------------------------------------------------------

    @staticmethod
    def _world_consumed(experiences) -> list[str]:
        """Object ids the admitted history's World *consumed* fact names, in order.

        What a model reads from memory is the row's text; this rule looks for the World
        owner's own structured token inside it (the consumer writes that token verbatim).
        Nothing else in a remembered sentence may move this rule.
        """

        found: list[str] = []
        for item in experiences or ():
            if not isinstance(item, dict):
                continue
            for match in _WORLD_CONSUMED.finditer(str(item.get("text") or "")):
                if match.group(1) not in found:
                    found.append(match.group(1))
        return found

    def invoke(self, request, *, schema_retry_index: int = 0):
        import agency_decision_ag1 as ag1

        self.call_count += 1
        candidates = [dict(c) for c in (getattr(request, "candidates", []) or [])]
        raw_keys = [str(key) for key in (getattr(request, "observed_fact_keys", []) or [])]
        facts = self._facts(raw_keys)
        here = self._here(raw_keys)
        experiences = [dict(item) for item in (getattr(request, "prior_experiences", []) or [])
                       if isinstance(item, dict)]
        self.offered.append([
            {"kind": c.get("candidate_kind"), "targets": self._refs(c),
             "legal": self._legal(c), "candidate_id": c.get("candidate_id"),
             "activity_ref": c.get("activity_ref")}
            for c in candidates])
        allowed = [str(code) for code in (getattr(request, "allowed_reason_codes", []) or [])]
        chosen, decision, reason = self._choose(candidates, facts, here, experiences)
        if chosen is not None and decision == "START" and any(
                ref.startswith("stay:") for ref in self._refs(chosen)):
            # her own short history: one nearby stay is enough to wait a closed place out.
            self.stays_taken += 1
        self.reads.append({
            "goal": self.goal, "facts": dict(facts), "here": here,
            "fact_keys": raw_keys,
            "experiences": [dict(item) for item in experiences],
            "world_consumed": self._world_consumed(experiences),
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

    def _choose(self, candidates: list[dict], facts: dict, here: str,
                experiences=()) -> tuple:
        """The one rule, applied to what this request really contains."""

        # 1. a running activity (a stay, a work step) is the only legal continuation.
        for candidate in candidates:
            if "CONTINUE" in self._legal(candidate):
                return (candidate, "CONTINUE", "continue the running activity")
        startable = [candidate for candidate in candidates
                     if "START" in self._legal(candidate)
                     and candidate.get("candidate_kind") in STARTABLE_KINDS]
        places = sorted([candidate for candidate in startable
                         if self._place_of(candidate)], key=self._place_of)
        stays = [candidate for candidate in startable
                 if any(ref.startswith("stay:") for ref in self._refs(candidate))]
        # 2. the declared appetite: eat something that is really here.
        if self.goal.startswith("eat:"):
            for candidate in startable:
                if any(ref.startswith("object:") for ref in self._refs(candidate)):
                    return (candidate, "START", "the declared appetite, and it is really here")
            # ... and if the admitted history already names the World owner's own *consumed*
            # fact, the appetite is answered: she does not leave to look for another one.
            answered = self._world_consumed(experiences)
            if answered and stays:
                return (stays[0], "START",
                        "world_fact:object:%s:consumed is in her admitted history; the appetite "
                        "is answered, staying here instead of going out for more"
                        % answered[0])
        wish = self._wish(self.goal)
        if wish:
            reachable = [candidate for candidate in places if self._place_of(candidate) == wish]
            if reachable and facts.get(wish, "NOT_READ") != "CLOSED":
                return (reachable[0], "START", "the wished place %s is open" % wish)
            if here == wish and stays:
                return (stays[0], "START", "already at %s, and it offers a stay" % wish)
            if reachable and self.stays_taken == 0 and stays:
                return (stays[0], "START",
                        "world_place_%s_availability_%s: not going there, waiting nearby"
                        % (wish, facts.get(wish) or "NOT_READ"))
        open_places = [candidate for candidate in places
                       if facts.get(self._place_of(candidate), "NOT_READ") != "CLOSED"]
        if open_places:
            return (open_places[0], "START", "first place that is really open")
        if stays:
            return (stays[0], "START", "a stay where she is")
        if places:
            return (places[0], "START", "the only place left on offer")
        return (None, "", "nothing legal was offered")

    def _place_of(self, candidate: dict) -> str:
        for ref in self._refs(candidate):
            if ref.startswith("place:"):
                return ref.split(":", 1)[1]
        return ""


class LostAnswerResolver:
    """The owner really departs; only this one *answer* is lost.

    This is the fault shape AR-0 already names (``TRANSPORT_ERR_AMBIGUOUS_POST_SUBMIT``): the
    World executed, the reply did not come back, so Life's action is ``UNKNOWN`` while the
    owner's own travel record really exists.  It is armed for exactly one submission and it
    never touches the ledger, the cache or the journey -- everything else is delegated.
    """

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.armed = True
        self.lost = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def resolve_action(self, **kwargs):
        out = self.inner.resolve_action(**kwargs)
        if not self.armed:
            return out
        self.armed = False
        self.lost += 1
        lost = dict(out)
        lost["transport_result"] = ar0.TRANSPORT_ERR_AMBIGUOUS_POST_SUBMIT
        lost["status"] = "uncertain"
        lost["code"] = "WORLD_ANSWER_LOST"
        lost["details"] = {"arrived": False, "reason_code": "WORLD_ANSWER_LOST"}
        return lost


class RecordingCognition:
    """Delegates to another stub and records exactly what the request carried.

    Used on the *real* resume/continue rounds (which keep their own owner-side stubs) so the
    drill can show what the next DecisionRequest was handed instead of asserting on the
    cycle's bookkeeping.  It answers nothing itself and changes nothing.
    """

    def __init__(self, inner: Any, sink: list) -> None:
        self.inner = inner
        self.sink = sink
        self.model_id = getattr(inner, "model_id", "stub:recording")
        self.model_version = getattr(inner, "model_version", "i-r2-recording")

    def invoke(self, request, *, schema_retry_index: int = 0):
        self.sink.append({
            "experiences": [dict(item) for item in
                            (getattr(request, "prior_experiences", []) or [])
                            if isinstance(item, dict)],
            "fact_keys": [str(key) for key in
                          (getattr(request, "observed_fact_keys", []) or [])]})
        return self.inner.invoke(request, schema_retry_index=schema_retry_index)


@posix_only
class EpisodeFixture(unittest.TestCase):
    """The episode fixture: one instance, one runtime, one injected clock, one World service.

    It carries no test of its own, so a targeted scenario (see
    ``tests/test_world_daily_life_plan_change.py``) can reuse the *same* owner assembly without
    inheriting this module's drill.
    """

    # -- fixture -----------------------------------------------------------------

    def setUp(self) -> None:
        # 夹具以**组合**方式复用 D 的 owner 装配（只读 import，不继承它的 TestCase）。
        self.base = donor.DScenarioBase("runTest")
        self.base.setUp()
        self.addCleanup(self._close)
        # 真实有限街区包，而不是 D 的房间场景：出门要有街、有公园、有会闭店的店。
        self.base.world.package_dir = WORLD_PACKAGE
        self.prepared = self.base.prepare_scenario(keep_fixture_world=True)
        self.data = self.prepared.data
        self.memory = self.prepared.memory
        self.world_home = Path(str(self.data["world_home"]))
        self.instance = Path(self.base.instance)
        self._declare_consumable()
        self._place_food("home")
        real_now = datetime.now(timezone.utc)
        # **一个**注入时刻：这一段 episode 的 Life 时钟与 World catalog 的读取时刻是同一个
        # ``Clock.now``（下一个工作日的 07:00 JST：书店 10 点才开、咖啡店 8 点才开，所以"那家
        # 关着"是 owner 的确定事实，不是和墙钟赛跑）。
        self.clock = Clock(episode_instant(after=real_now))
        # 真实 World 服务在实例自己的 world home 上起（实例环境里才有它的 socket）。
        self.gateway = self.base.gateway()
        self.env = self.base.instance_env()
        self.provider = wmos.WorldMoveOpportunityProvider(
            client=WorldBodyClient(environment=self.env), action_intent=action_intent,
            bridge_dir=str(BRIDGE_DIR), environment=self.env,
            world_home=str(self.world_home),
            # 营业事实与 Life 时刻取同一个时刻，没有第二个"当下"。
            now=lambda: self.clock.now,
            observed_now=lambda: self.clock.now,
            # 一次观察的"可选窗口"一分钟（provider 自己的参数；产品默认仍是 300s）：几段路之后
            # 一份旧门牌本来就不该继续和她此刻能选的东西并排竞争 AG-1 的 16 条候选上限。
            valid_for_seconds=60)
        self.cognition = ScriptedWorldCognition()
        self.runtime, self.port = self.base.runtime_with_memory(
            self.cognition, self.env, self.data, self.memory)
        self.fixture = self.base.assemble(self.runtime, self.env, observation=self.provider)
        self.cycle = self.fixture.cycle
        self.cycle.world_home = str(self.world_home)
        self.cycle.work_artifact_id = self.data["work_artifact_id"]
        self.state_root = odl.life_state_root(self.instance)
        # I-r2: 真实世界真的出发了，但这一次**答复**在路上丢了——AR-0 会看到 UNKNOWN，而 owner
        # 自己的 travel 记录是真的存在的。这正是要闭环的那种情况，只丢一次。
        self.lost_answer = LostAnswerResolver(self.runtime.world_adapter.resolver)
        self.runtime.world_adapter.resolver = self.lost_answer
        # I-r2: 真实 M0 → 决策输入的那条既有端口。这里接的就是 Memory 自己的读
        # （``memory.port.recent``），不是本文件伪造的经历；它只影响"这一轮能看到什么"。
        self.cycle.experience_provider = self.experience_rows
        self.cycle.continuity_source = self.continuity_view
    def experience_rows(self):

        """The admitted M0 rows exactly as the product hands them to a model.

        This is ``memory.port.prompt_view()`` -- the same bounded, governance-filtered view
        the resident's ``experience_provider`` lambda returns (``autonomous_life_resident``
        ``_memory_ports``).  It is the *model-facing* shape, so what this drill reads here is
        literally what a model would see: ``text``, ``activity_ref``, ``artifact_ref``,
        ``version_ref``, ``owner_result``.  Nothing is filtered, sorted or invented.
        """

        return [dict(item) for item in (self.memory.port.prompt_view() or [])
                if isinstance(item, dict)]

    def continuity_view(self):
        """C's read-only view over the real Memory/Activity/Agenda owners (same as the resident).

        It is a *read*: it can name what is unfinished and which owner-confirmed consequence is
        the newest one, and it can never authorize a move, a position or an action.
        """

        from life_continuity_inputs import ContinuityBinding, LifeContinuityInputs

        return LifeContinuityInputs(
            binding=ContinuityBinding(subject=donor.MEMORY_OWNER, home=str(self.state_root),
                                      now=self.clock.iso(),
                                      owner_refs={"activity": "activity:canonical",
                                                  "agenda": "agenda:lr4",
                                                  "artifact": "artifact:supply"}),
            past_source=self.memory.port,
            activity_source=self.runtime.activity_read,
            agenda_source=self.runtime.waiting_coordinator,
            governance_probe=lambda: {"healthy": self.memory.ledger.projection().healthy})

    def _close(self) -> None:
        for step in (self.base.tearDown, self.base.doCleanups):
            try:
                step()
            except Exception:
                pass

    # -- owner-side fixture writes (initialisation only, never "because of a result") ----

    def _declare_consumable(self) -> None:
        path = self.world_home / "state" / "world_object_capabilities.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        document = {}
        if path.exists():
            document = json.loads(path.read_text(encoding="utf-8"))
        document.setdefault("schema_version", wbs.CAPABILITIES_SCHEMA)
        document.setdefault("objects", {})
        document["consumables"] = {FOOD: {"consumable": True, "free_to_consume": True}}
        path.write_text(json.dumps(document, ensure_ascii=False, sort_keys=True),
                        encoding="utf-8")

    def _place_food(self, place: str) -> None:
        store = wbs.SubstrateStore(wbs.substrate_path(self.world_home))
        state = store.load()
        self.assertIsNotNone(state, "init_instance must have written a substrate")
        store.save(wbs.place_object(state, FOOD, {"kind": "room", "location_id": place,
                                                  "area_id": place}))

    # -- owner reads -------------------------------------------------------------

    def substrate(self) -> dict:
        return wbs.SubstrateStore(wbs.substrate_path(self.world_home)).load()

    def placement(self, object_id: str):
        return wbs.placement_of(self.substrate(), object_id)

    def receipts(self) -> dict:
        return self.substrate().get("consumptions") or {}

    def consequence_records(self) -> list:
        path = self.world_home / "data" / "body" / "consequences.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def current_place(self) -> str:
        state = json.loads((self.world_home / "data" / "world" / "world_state.json")
                           .read_text(encoding="utf-8"))
        return str(state["location"]["place_id"])

    def foreground(self):
        return self.runtime.canonical_state().get("foreground_activity_ref")

    def activity(self, ref) -> dict:
        return self.runtime.activity_read.get_activity(str(ref)) or {}

    def head(self, artifact_id: str) -> dict:
        return self.base.head(artifact_id)

    # -- one round ---------------------------------------------------------------

    def round(self, trigger: str, *, goal=None, advance: int | None = None,
              because: str = "") -> dict:
        if advance is not None:
            self.clock.advance(advance, because=because or trigger)
        if goal is not None:
            self.cognition.goal = goal
        out = self.cycle.evaluate_once(trigger_ref=trigger, observed_at=self.clock.iso(),
                                       act=True)
        read = self.cognition.reads[-1] if self.cognition.reads else {}
        print("R2 %-22s outcome=%s decision=%s chose=%s stay=%s place=%s"
              % (trigger, out.get("outcome"), out.get("decision"), read.get("chose"),
                 (out.get("stay") or {}).get("phase"), self.current_place()))
        if out.get("outcome") in {"DECISION_FAILED", "NO_ACTION"} and self.cognition.offered:
            offered = self.cognition.offered[-1]
            print("R2   offered=%d %s" % (len(offered), json.dumps(offered, ensure_ascii=False)[:1200]))
        elif self.cognition.offered:
            print("R2   offered=%d facts=%s chose=%s"
                  % (len(self.cognition.offered[-1]), json.dumps(read.get("facts")),
                     read.get("chose")))
        return out

    def move(self, place: str, *, goal: str | None = None) -> tuple[dict, dict]:
        """One single-hop departure, settled at the owner's own arrival instant.

        An ``UNKNOWN`` answer is closed by the **same** execution -- never by a second
        ``START``: ``cycle.reconcile_unknown_step`` settles the owner's own journey ledger
        against its travel record, reconciles the same ``action_id`` and settles the original
        Activity.  If the owner still has not said what happened, this fails with that verdict
        instead of retrying; nothing here sleeps its way to green.
        """

        out = self.round("drill:move:%s" % place, goal=goal, because="move:%s" % place)
        reconciled: dict = {}
        if out.get("outcome") == "STEP_UNKNOWN":
            reconciled = self.cycle.reconcile_unknown_step(
                action_id=out.get("action_id"), occurred_at=self.clock.iso(),
                reason="drill:move:%s" % place)
            print("R2 MOVE-UNKNOWN %s action=%s reconcile=%s/%s place=%s"
                  % (place, out.get("action_id"), reconciled.get("reason_code"),
                     reconciled.get("action_status"), self.current_place()))
            self.assertTrue(reconciled.get("reconciled"), reconciled)
            self.assertEqual(reconciled.get("action_status"), "SUCCEEDED", reconciled)
            self.assertNotEqual(reconciled.get("reason_code"), "STILL_UNKNOWN", reconciled)
            journey = reconciled.get("journey") or {}
            # The owner's own evidence decides which of the two honest answers this is: the
            # segment departed (in transit, arrival still ahead) or it already arrived.
            self.assertIn(journey.get("journey_status"), {"in_transit", "completed"}, reconciled)
            out = dict(out)
            out.update({"outcome": "IN_TRANSIT", "world_verb": "MOVE",
                        "reconciliation": reconciled,
                        "journey": {"destination_location_id": place,
                                    "expected_arrival_at": journey.get("expected_arrival_at"),
                                    "segment_status": journey.get("segment_status")}})
        self.assertEqual(out.get("outcome"), "IN_TRANSIT", out)
        self.assertEqual(out.get("world_verb"), "MOVE", out)
        journey = out.get("journey") or {}
        self.assertEqual(journey.get("destination_location_id"), place, journey)
        arrival = str(journey.get("expected_arrival_at") or "")
        if arrival:
            settled = self.cycle.settle_world_journey(
                observed_at=datetime.fromisoformat(arrival.replace("Z", "+00:00")).isoformat())
            self.assertTrue(settled.get("ok"), settled)
            self.assertTrue(settled.get("arrived"), settled)
        else:
            # The reconciliation already read the owner's arrival; there is nothing left to
            # settle, and claiming "still in transit" here would be the opposite error.
            settled = reconciled
        self.assertEqual(self.current_place(), place, settled)
        # 时间怎么走由这个文件自己声明：按 owner 公布的那一段步行秒数推进
        views = [item for item in (self.provider.last_opportunity_view or [])
                 if item.get("target_ref") == "place:%s" % place]
        seconds = int((views[0].get("walk_seconds") if views else 0) or 0)
        if seconds:
            self.clock.advance(seconds, because="walk:%s" % place)
        return out, settled

    # -- the drill ---------------------------------------------------------------

class DailyContinuousDrillTest(EpisodeFixture):
    """The drill itself: one short continuous episode through the real owners."""

    def test_a_short_continuous_episode_runs_through_the_real_owners(self):
        # 历史 test id 曾是 ``..._lives_the_whole_day_...``；本文件从不声称跑过"一天"，那一段
        # 现在只是这段短程连续 episode 的旧名字。
        data = self.data
        work_id = data["work_artifact_id"]
        print("R2 CLOCK kind=%s now=%s weekday=%s hour=%s"
              % (self.clock.kind, self.clock.base.isoformat(), self.clock.base.weekday(),
                 self.clock.base.astimezone(JST).hour))
        # **唯一**的注入时刻：整段 episode 的 Life 时刻与营业事实读取时刻同一个值。
        self.assertGreater(self.clock.base, datetime.now(timezone.utc) - timedelta(seconds=1))
        self.assertLess(self.clock.base.weekday(), 5)
        self.assertEqual(self.clock.base.astimezone(JST).hour, 7)
        self.assertFalse(hasattr(self.clock, "schedule"), "there is only one clock in this episode")

        # -- 0) 没有真实材料：一个模型调用都不该发生 ---------------------------------
        saved = (self.cycle.world_opportunity_provider, self.cycle.artifact_port,
                 self.cycle.reading_artifact_id)
        self.cycle.world_opportunity_provider = lambda: []
        self.cycle.artifact_port = None
        self.cycle.reading_artifact_id = None
        quiet = self.round("drill:quiet")
        self.assertEqual(quiet.get("outcome"), "NO_MATERIAL", quiet)
        self.assertEqual(quiet.get("llm_calls"), 0, quiet)
        self.assertEqual(self.cognition.call_count, 0, self.cognition.call_count)
        self.cycle.world_opportunity_provider, self.cycle.artifact_port, \
            self.cycle.reading_artifact_id = saved

        # -- 1) 一次真实作品推进：AR-0 → 真 Supply 新版本 → AG-2 handoff → M0 -------
        head_before = self.head(work_id)["version_id"]
        self.runtime.decision_service.cognition_adapter = self.base._work_cognition(
            work_id, data["reading_artifact_id"])
        work_round = self.round("drill:work")
        self.assertEqual(work_round.get("outcome"), "ACTED", work_round)
        head_after_work = self.head(work_id)["version_id"]
        self.assertNotEqual(head_after_work, head_before)
        self.assertTrue(work_round.get("owner_receipt"), work_round)
        private_activity = str(work_round["activity_ref"])
        self.assertTrue(private_activity)
        rows = self.memory.rows()
        self.assertTrue(rows, "the real owner result must reach M0")
        self.assertEqual(rows[0]["delivery_status"], "VERIFIED", rows[0])
        admitted = self.memory.port.recent(limit=20)
        self.assertTrue([r for r in admitted if r.artifact_ref == work_id],
                        [r.artifact_ref for r in admitted])
        print("R1 WORK head=%s->%s activity=%s m0_rows=%d"
              % (head_before, head_after_work, private_activity, len(rows)))

        # -- 2) 合法 safe-boundary 打断：私人 Activity 停在保存点，前台释放 ----------
        protocol = cop.FiniteCoPresence(
            self.runtime, state_root=self.state_root, subject_id=data["subject_id"],
            home=str(self.state_root), cycle=self.cycle)
        invitation = cop.parse_invitation({
            "event_kind": cop.KIND_INVITATION, "invitation_id": "inv:ir1:1",
            "source_ref": "channel:ir1-drill", "occurred_at": self.clock.iso(),
            "scope": "一起待一会儿", "requires_engagement": True})
        considered = protocol.consider(invitation, now=self.clock.iso())
        self.assertEqual(considered.get("decision"), cop.DECISION_ACCEPT, considered)
        started = protocol.begin(invitation, now=self.clock.iso())
        self.assertTrue(started.get("ok"), started)
        self.assertEqual(started["adoption"]["adoption_status"], "APPLIED", started["adoption"])
        parked = self.activity(private_activity)
        self.assertEqual(parked.get("status"), "WAITING", parked)
        checkpoint_at_break = parked.get("checkpoint_ref")
        self.assertTrue(checkpoint_at_break, parked)
        self.assertTrue(parked.get("foreground_released"), parked)
        # 单前台：共处期间前台是**共处自己**那一个 Activity，私人 Activity 已停在保存点
        self.assertNotEqual(self.foreground(), private_activity)
        self.assertIn("wait_dep:co_presence-inv:ir1:1", parked.get("source_refs") or [], parked)
        ended = protocol.end(cop.parse_end({
            "event_kind": cop.KIND_END, "invitation_id": "inv:ir1:1",
            "occurred_at": self.clock.advance(300, because="co-presence").replace("+00:00", "Z"),
            "explicit_end": True}), now=self.clock.iso())
        self.assertTrue(ended.get("ok"), ended)
        self.assertEqual(ended["joint_activity"]["status"], "COMPLETED", ended["joint_activity"])
        self.assertIsNone(self.foreground(), self.runtime.canonical_state())
        eligibility = ended["new_eligibility_refs"][0]
        self.assertEqual(eligibility["status"], "OPEN", eligibility)
        self.assertEqual(eligibility["activity_id"], private_activity, eligibility)
        self.assertEqual(ended["evaluation"]["llm_calls"], 0, ended["evaluation"])
        print("R1 INTERRUPT parked=%s checkpoint=%s eligibility=%s"
              % (parked["status"], checkpoint_at_break, eligibility["eligibility_id"]))

        # -- 3) 出门：同一份观察里既有能去的地方，也有能吃的那一份，选择**不吃** -----
        self.runtime.decision_service.cognition_adapter = self.cognition
        # 这一段 episode 自己的意图：白天去书店看会儿书。整段只声明一次，之后每一轮都是同一个
        # 规则去读请求里的事实和候选——不是调用者逐轮指定"选哪个"。
        self.cognition.goal = "read_at:reading_point"
        observe = self.provider()
        refs = [item["target_refs"][0] for item in observe]
        # 同一份观察里三类合法机会并存（家只有一条单跳边，所以竞争来自"去/吃/待"三种，
        # 而不是自造路线）：能吃的那一份、能走到的地方、能在这儿待一会儿。
        self.assertIn("object:%s" % FOOD, refs, refs)
        self.assertTrue([ref for ref in refs if ref.startswith("place:")], refs)
        self.assertTrue([ref for ref in refs if ref.startswith("stay:")], refs)
        self.assertGreaterEqual(len(refs), 3, refs)
        stay_at_home = [ref for ref in refs if ref.startswith("stay:")][0]
        self.assertTrue(stay_at_home.startswith("stay:home:"), refs)
        departure, _settled = self.move("street_front", goal="read_at:reading_point")
        # 这一段真的是 UNKNOWN → 对账 的闭环：世界真的出发了、答复丢了；同一 action/execution
        # 对账后仍是"出发"，不是被重发一次 START，也没有留下 UNKNOWN 动作。
        self.assertEqual(self.lost_answer.lost, 1, self.lost_answer.lost)
        reconciled = departure.get("reconciliation") or {}
        self.assertEqual(reconciled.get("reason_code"), "RECONCILED_SUCCEEDED", departure)
        self.assertEqual(reconciled.get("action_status"), "SUCCEEDED", reconciled)
        self.assertIn((reconciled.get("journey") or {}).get("journey_status"),
                      {"in_transit", "completed"}, reconciled)
        self.assertEqual(self.runtime.action_read.list_actions(status="UNKNOWN"), [])
        # 不消费的那一支：那一份还在、没有回执、没有 Body 后果
        self.assertIsNotNone(self.placement(FOOD), self.substrate())
        self.assertEqual(self.receipts(), {})
        self.assertEqual([item for item in self.consequence_records()
                          if FOOD in json.dumps(item, ensure_ascii=False)], [])
        self.assertEqual(self.current_place(), "street_front")
        self.assertEqual(departure.get("world_verb"), "MOVE", departure)

        # -- 4) 门前街：owner 当下的事实说边上那家书店 CLOSED → 模型读了这条事实才改主意 ----
        here = [item["target_refs"][0] for item in self.provider()]
        competing = sorted(ref for ref in here if ref.startswith("place:"))
        # 单跳边界：owner 只给出这一刻一步能走到的地方，没有为了"全目的地"造一张全局地图
        self.assertGreaterEqual(len(competing), 2, competing)
        catalog = self.provider.last_catalog or {}
        catalog_facts = {str(item.get("location_id")): item
                         for item in (catalog.get("opportunities") or [])}
        bookstore = catalog_facts.get("reading_point", {})
        # 就开在门前街边上的书店此刻关门：owner 自己的营业事实，也不是拓扑造成的缺席。
        self.assertEqual((bookstore.get("availability") or {}).get("status"), "CLOSED", bookstore)
        self.assertEqual(bookstore.get("affordances"), [], bookstore)
        # 门口仍然走得到（MOVE 由 owner 的 action space 给出，闭店不拦出发）——所以它的缺席
        # 不能归因于拓扑；模型必须从**事实**里读到"关着"。
        self.assertIn("place:reading_point", competing, competing)
        closed_key = "world_place_reading_point_availability_CLOSED"
        plan_change = self.round("drill:plan-change")
        read = self.cognition.reads[-1]
        # 1) 这条来源事实真的进了这次 DecisionRequest 的输入
        self.assertIn(closed_key, read["fact_keys"], read)
        self.assertEqual(read["facts"].get("reading_point"), "CLOSED", read)
        # 2) 模型据此没有选那条关着的支，而是选了另一个**合法且真的会执行**的分支
        self.assertIn("place:reading_point", read["offered_targets"], read)
        self.assertNotEqual(read["chose"], "place:reading_point", read)
        self.assertTrue(str(read["chose"] or "").startswith("stay:street_front:"), read)
        self.assertEqual(plan_change.get("outcome"), "ACTED", plan_change)
        stay = plan_change.get("stay") or {}
        self.assertEqual(stay.get("phase"), "START", stay)
        self.assertEqual(stay.get("place"), "street_front", stay)
        self.assertEqual(stay.get("affordance"), "LOOK_AROUND", stay)
        self.assertEqual(stay.get("planned_seconds"), 180, stay)
        self.assertEqual(stay.get("started_at"), plan_change.get("observed_at"), stay)
        stay_activity = str(plan_change.get("activity_ref"))
        self.assertTrue(stay_activity)
        self.assertEqual(self.foreground(), stay_activity)
        self.assertEqual(self.activity(stay_activity).get("status"), "ACTIVE")
        self.assertEqual(self.current_place(), "street_front")
        print("R2 PLAN-CHANGE competing=%s facts=%s chose=%s reason=%s"
              % (competing, json.dumps(read["facts"]), read["chose"], read["reason"]))
        # -- 5) 门前街的非产出停留：真实起止，提前一轮不许完成 ----------------------
        here_stays = [item["target_refs"] for item in self.provider()
                      if item["target_refs"][0].startswith("stay:")
                      and item["location_ref"] == "place:street_front"]
        self.assertEqual(here_stays, [["stay:street_front:LOOK_AROUND:180"]], here_stays)
        # 提前一轮：时间没到，完成判据不许被满足（同一个停留 Activity 的 CONTINUE）
        early = self.round("drill:stay:early", advance=120, because="stay:early")
        self.assertEqual(early.get("outcome"), "ACTED", early)
        self.assertEqual((early.get("stay") or {}).get("phase"), "ONGOING", early.get("stay"))
        self.assertNotEqual(early.get("progress_state"), "COMPLETION_ELIGIBLE", early)
        self.assertEqual(self.foreground(), stay_activity)
        # 声明时长真的到了：只有这时才由世界当前观察确认结束
        finish = self.round("drill:stay:end", advance=120, because="stay:end")
        self.assertEqual(finish.get("outcome"), "ACTED", finish)
        ended_stay = finish.get("stay") or {}
        self.assertEqual(ended_stay.get("phase"), "END", ended_stay)
        self.assertGreaterEqual(int(ended_stay.get("elapsed_seconds") or 0), 180, ended_stay)
        self.assertEqual(finish.get("progress_state"), "COMPLETION_ELIGIBLE", finish)
        print("R2 STAY-COMPLETED outcome=%s progress=%s completion=%s"
              % (finish.get("outcome"), finish.get("progress_state"),
                 finish.get("completion_status")))
        self.assertIsNone(self.foreground(), self.runtime.canonical_state())
        self.assertEqual(self.activity(stay_activity).get("status"), "COMPLETED",
                         self.activity(stay_activity))
        updates = self.runtime.progress_read.list_checkpoint_updates(stay_activity)
        self.assertTrue(updates, updates)
        # 同一个瞬间（owner 用自己的偏移写下来，只到秒）：比较时刻，不比较拼写。
        checked_at = datetime.fromisoformat(str(updates[0]["occurred_at"]).replace("Z", "+00:00"))
        started_at = datetime.fromisoformat(str(stay.get("started_at")).replace("Z", "+00:00"))
        self.assertLessEqual(abs((checked_at - started_at).total_seconds()), 1.0,
                             (updates[0], stay.get("started_at")))
        self.assertTrue(str(updates[0]["new_checkpoint_ref"]).startswith("world_fact:"), updates[0])
        print("R2 STAY start=%s elapsed=%s checkpoint=%s"
              % (stay.get("started_at"), ended_stay.get("elapsed_seconds"),
                 ended_stay.get("checkpoint_ref")))

        # -- 6) 回家，路上暂停：0 调用 / 不出发 / 已出发段照常到达 ------------------
        budget_before = dict(self.fixture.budget.view())
        run_id_before = self.env.get(odl.RUN_ID_ENV)
        calls_before = self.cognition.call_count
        # 同一个模型规则、同一个意图：书店关着、门前街已经待过一次 ⇒ 这一轮它选回家，而且这
        # 一步真的执行（真出发）。UNKNOWN 也照样走同一次对账，不重发。
        outbound = self.round("drill:home:depart")
        read = self.cognition.reads[-1]
        self.assertEqual(read.get("chose"), "place:home", read)
        if outbound.get("outcome") == "STEP_UNKNOWN":
            reconciled = self.cycle.reconcile_unknown_step(
                action_id=outbound.get("action_id"), occurred_at=self.clock.iso(),
                reason="drill:home:depart")
            print("R2 MOVE-UNKNOWN home action=%s reconcile=%s/%s"
                  % (outbound.get("action_id"), reconciled.get("reason_code"),
                     reconciled.get("action_status")))
            self.assertTrue(reconciled.get("reconciled"), reconciled)
            self.assertEqual(reconciled.get("action_status"), "SUCCEEDED", reconciled)
            outbound = dict(outbound)
            outbound.update({"outcome": "IN_TRANSIT", "world_verb": "MOVE",
                             "reconciliation": reconciled,
                             "journey": {"destination_location_id": "home",
                                         "expected_arrival_at":
                                             (reconciled.get("journey") or {})
                                             .get("expected_arrival_at")}})
        odl.set_pause(self.instance, True, reason="drill:pause")
        try:
            held = self.round("drill:paused")
            self.assertEqual(held.get("outcome"), "PAUSED_BY_OPERATOR", held)
            self.assertEqual(held.get("llm_calls"), 0, held)
            self.assertEqual(self.cognition.call_count, calls_before + 1, self.cognition.call_count)
            self.assertEqual(self.current_place(), "street_front")
            # 暂停不冻结物理：已出发的那一段按 owner 自己的到期时刻照常到达，但绝不发起新段
            due = str((outbound.get("journey") or {}).get("expected_arrival_at") or "")
            self.assertTrue(due, outbound)
            settled = self.cycle.settle_world_journey(
                observed_at=datetime.fromisoformat(due.replace("Z", "+00:00")).isoformat())
            self.assertTrue(settled.get("ok"), settled)
            self.assertTrue(settled.get("arrived"), settled)
            self.assertEqual(self.current_place(), "home", settled)
        finally:
            odl.set_pause(self.instance, False, reason="drill:pause")
        budget_after = dict(self.fixture.budget.view())
        self.assertEqual(budget_after["utc_day_calls"], budget_before["utc_day_calls"],
                         (budget_before, budget_after))
        self.assertEqual(budget_after["run_window_calls"], budget_before["run_window_calls"],
                         (budget_before, budget_after))
        self.assertEqual(budget_after["run_id"], budget_before["run_id"], budget_after)
        self.assertEqual(self.env.get(odl.RUN_ID_ENV), run_id_before)
        print("R2 PAUSE calls=%s->%s budget=%s"
              % (calls_before, self.cognition.call_count, budget_after["utc_day_calls"]))

        # -- 7) 回家后用掉一份：owner 回执 + Body 恰好一条后果 ----------------------
        self.cognition.goal = "eat:any"
        consume_round = self.round("drill:consume")
        self.assertEqual(consume_round.get("outcome"), "ACTED", consume_round)
        self.assertEqual(consume_round.get("world_verb"), "CONSUME", consume_round)
        self.assertTrue(consume_round.get("consumption_receipt_present"), consume_round)
        receipts = self.receipts()
        self.assertEqual(len(receipts), 1, receipts)
        execution_id, receipt = next(iter(receipts.items()))
        self.assertEqual(receipt["object_id"], FOOD, receipt)
        self.assertEqual(receipt["execution_id"], execution_id, receipt)
        self.assertEqual(consume_round.get("consumption_execution_id"), execution_id)
        self.assertIsNone(self.placement(FOOD), self.substrate())
        consumed = [item for item in self.consequence_records()
                    if str(item.get("execution_id")) == str(execution_id)]
        self.assertEqual(len(consumed), 1, self.consequence_records())
        blob = json.dumps(consumed[0], ensure_ascii=False).lower()
        for word in ("nutrition", "satiety", "hunger", "health", "recovery"):
            self.assertNotIn(word, blob.replace("not %s" % word, ""))
        self.assertEqual(consume_round.get("progress_state"), "COMPLETION_ELIGIBLE",
                         consume_round)
        self.assertIsNone(self.foreground(), self.runtime.canonical_state())
        print("R2 CONSUME receipt=%s execution=%s body=%d"
              % (receipt["receipt_id"], execution_id, len(consumed)))

        # -- 8) 本次 World 后果真的进了 M0，而且是一条**中性**的 World 叙述 -----------
        consume_action = str(consume_round.get("action_id") or "")
        self.assertTrue(consume_action, consume_round)
        handoff = consume_round.get("world_experience_handoff") or {}
        self.assertTrue(handoff.get("handoff_id"), consume_round)
        consume_fact = "world_fact:object:%s:consumed" % FOOD
        m0_rows = [row.to_dict() for row in self.memory.port.recent(limit=50)]
        # 挑出来的方式是这条事实本身：owner 的 consumed 事实写在行里，而不是按"最新一行"猜。
        world_rows = [row for row in m0_rows if consume_fact in str(row.get("text") or "")]
        self.assertTrue(world_rows, {"fact": consume_fact,
                                     "m0": [str(r.get("text"))[:70] for r in m0_rows[:8]]})
        world_row = world_rows[0]
        # 这一行来自本次 World 动作 + owner 回执，不是作品行、也不是测试写进去的行：
        # 动作引用是这一次消费的 AR-0 动作，结果引用是 AR-1 结算，作品引用为空。
        self.assertEqual(world_row.get("action_ref"), consume_action, world_row)
        self.assertIsNone(world_row.get("artifact_ref"), world_row)
        self.assertEqual(world_row.get("outcome_kind"), "SIGNIFICANT_PROGRESS", world_row)
        self.assertTrue(str(world_row.get("owner_result") or "").startswith("rset:"), world_row)
        # I-r3：consumer 的模板不再用作品腔去说一件世界里的后果（旧模板会说"完成了一次真实修改，
        # 作品所有者确认了新版本"），同时保留真实来源：世界事实、动作、结算回执都还在行里。
        world_text = str(world_row.get("text") or "")
        self.assertIn("世界事实 " + consume_fact, world_text)
        self.assertIn(consume_action, world_text)
        self.assertIn(str(world_row.get("owner_result")), world_text)
        for claim in ("真实修改", "新版本", "作品"):
            self.assertNotIn(claim, world_text, world_text)
        print("R2 M0-WORLD row=%s action=%s execution=%s owner_result=%s text=%s"
              % (world_row.get("source_ref"), consume_action, execution_id,
                 world_row.get("owner_result"), world_text[:80]))

        # -- 8b) 这条被准入的 World 行真的改变了下一次**合法选择** -------------------
        # 同一个意图（还想吃）、同一个实例：那一份已经用掉了（观察里不再有 object 候选）。
        # 没有这条行时，规则会出门继续找；读到 owner 自己的 consumed 事实时，就地待着。
        view_rows = self.experience_rows()
        self.assertTrue([row for row in view_rows if consume_fact in str(row.get("text") or "")],
                        [str(row.get("text"))[:70] for row in view_rows])
        # 同一次输入里还带着作品行（artifact_ref 非空）：它不含 World 事实，不能替代这一行。
        self.assertTrue([row for row in view_rows if row.get("artifact_ref") == work_id], view_rows)
        self.assertEqual(self.cognition.goal, "eat:any")
        after_consume = self.round("drill:after-consume")
        read = self.cognition.reads[-1]
        self.assertEqual(read.get("world_consumed"), [FOOD], read)
        self.assertTrue(str(read.get("chose") or "").startswith("stay:home:"), read)
        self.assertIn("place:street_front", read.get("offered_targets") or [], read)
        home_stay = after_consume.get("stay") or {}
        self.assertEqual(home_stay.get("phase"), "START", home_stay)
        planned_home = int(home_stay.get("planned_seconds") or 0)
        self.assertGreater(planned_home, 0, home_stay)
        print("R2 WORLD-IN-CHOICE chose=%s consumed=%s reason=%s"
              % (read.get("chose"), read.get("world_consumed"), read.get("reason")))
        ended_home = self.round("drill:after-consume:end", advance=planned_home + 60,
                                because="home:stay:end")
        self.assertEqual((ended_home.get("stay") or {}).get("phase"), "END", ended_home)
        self.assertIsNone(self.foreground(), self.runtime.canonical_state())


        # -- 9) 回到原私人保存点并续步：owner 资格 → 真新版本 ----------------------
        resume_requests: list[dict] = []
        self.runtime.decision_service.cognition_adapter = RecordingCognition(
            self.base._resume_cognition_for(work_id), resume_requests)
        resumed = self.round("drill:resume")
        self.assertEqual(resumed.get("decision"), "RESUME", resumed)
        self.assertEqual(resumed.get("adoption_status"), "APPLIED", resumed)
        back = self.activity(private_activity)
        self.assertEqual(back.get("status"), "ACTIVE", back)
        self.assertEqual(back.get("checkpoint_ref"), checkpoint_at_break, back)
        self.assertEqual(resumed.get("continuity_consumed_eligibility_ref"),
                         eligibility["eligibility_id"], resumed)
        # 这一次 DecisionRequest 真的带着刚才那条 World 行：不是"cycle 里记了一笔"，而是喂给
        # 决策的输入里就有它（生产形状 ``prompt_view``，没有 action_ref 字段——所以这里认的是
        # 行文里 owner 自己的事实与结算回执，而不是只认结构化字段）。紧接着的 RESUME /
        # CONTINUE 是**真执行**的 Action。
        self.assertTrue(resume_requests, "the resume round must have made one request")
        carried = json.dumps(resume_requests[-1].get("experiences") or [], ensure_ascii=False)
        self.assertIn(consume_fact, carried)
        self.assertIn(str(world_row.get("owner_result")), carried)
        # 同一次请求里，来源营业事实也在（同一条 observed_facts 通道），不是只塞了历史。
        self.assertTrue([key for key in (resume_requests[-1].get("fact_keys") or [])
                         if str(key).startswith("world_place_")], resume_requests[-1])
        print("R2 M0-INTO-REQUEST experiences=%d carries=%s"
              % (len(resume_requests[-1].get("experiences") or []), consume_fact))
        self.runtime.decision_service.cognition_adapter = self.base._continue_cognition()
        head_before_continue = self.head(work_id)["version_id"]
        continued = self.round("drill:continue")
        self.assertEqual(continued.get("outcome"), "ACTED", continued)
        head_after_continue = self.head(work_id)["version_id"]
        self.assertNotEqual(head_after_continue, head_before_continue)
        self.assertEqual(continued["artifact_selection"]["artifact_id"], work_id,
                         continued["artifact_selection"])
        print("R2 RESUME checkpoint=%s continued=%s->%s"
              % (back.get("checkpoint_ref") == checkpoint_at_break,
                 head_before_continue, head_after_continue))

        # -- 10) 历史行决定续写对象：M0 那一行说了算，弱线索不改变它 ---------------
        work_rows = [row for row in self.memory.port.recent(limit=20)
                     if row.artifact_ref == work_id]
        self.assertTrue(work_rows, "the real owner result must be admitted to M0")
        newest = work_rows[0]
        self.assertTrue(newest.owner_result, newest.to_dict())
        self.assertTrue(newest.action_ref, newest.to_dict())
        history_receipts = self.runtime.action_read.list_receipts(newest.action_ref)
        self.assertTrue(history_receipts, newest.action_ref)
        history_version = str(history_receipts[-1]["details"]["version_id"])
        head_now = self.head(work_id)["version_id"]
        view = self.cycle.read_continuity()
        self.assertEqual(view.get("past_state"), "OK", view.get("source_states"))
        step = view.get("next_step") or {}
        self.assertEqual(step.get("reason_code"), "CONTINUE_FROM_OWNER_CONFIRMED_OBJECT", step)
        self.assertEqual(step.get("continue_from_ref"), newest.owner_result, step)
        self.assertEqual(self.cycle.continuity_continue_object(), work_id)
        point, selection = self.cycle._read_resume_point()
        self.assertEqual(selection.get("selected_by"), "CONTINUITY_HISTORY", selection)
        self.assertEqual(selection.get("history_continuation_ref"), newest.owner_result,
                         selection)
        self.assertEqual(selection.get("history_version_ref"), history_version, selection)
        self.assertEqual(selection.get("expected_head_source"), "OWNER_CURRENT_HEAD", selection)
        self.assertEqual(point.artifact_id, work_id, selection)
        self.assertEqual(point.head_version_id, head_now, selection)
        # …这一行真的是历史喂进来的：再走一步，落成新版本
        self.runtime.decision_service.cognition_adapter = self.base._continue_cognition()
        before_history_step = self.head(work_id)["version_id"]
        acted = self.round("drill:history-step")
        self.assertEqual(acted.get("outcome"), "ACTED", acted)
        self.assertEqual(acted["artifact_selection"]["selected_by"], "CONTINUITY_HISTORY",
                         acted["artifact_selection"])
        self.assertNotEqual(self.head(work_id)["version_id"], before_history_step)
        # 弱线索：没有对应的真实行 / 没有对象 → 回到绑定的作品，位置与权限不变
        self.cycle.last_continuity = {
            "past_state": "OK",
            "next_step": {"reason_code": "CONTINUE_FROM_OWNER_CONFIRMED_OBJECT",
                          "item_ref": private_activity, "evidence_refs": ["lexp:no-such-row"],
                          "artifact_ref": None, "continue_from_ref": None},
            "past": [],
        }
        self.assertIsNone(self.cycle.continuity_continue_object())
        cue_point, cue_selection = self.cycle._read_resume_point()
        self.assertEqual(cue_selection.get("selected_by"), "BOUND_WORK", cue_selection)
        self.assertEqual(cue_point.artifact_id, work_id, cue_selection)
        self.assertEqual(self.cycle.pause_gate()["blocked"], False)
        print("R2 HISTORY row=%s owner_result=%s head=%s"
              % (newest.source_ref, newest.owner_result, head_now))
        print("R2 CLOCK advances=%d injected=%s" % (len(self.clock.advances), self.clock.iso()))
        print("R2 DONE")


if __name__ == "__main__":
    unittest.main()

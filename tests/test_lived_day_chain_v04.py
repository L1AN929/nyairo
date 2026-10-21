#!/usr/bin/env python3
"""v0.4 主线 A 验收：**一个实例、一条连续的日常链**（真实 owner，模型只有原始 JSON）。

这条链不是功能清单，而是"她能不能连着过"：

1. **看书看到真停点**：真实阅读材料按页读到这一版读完（`READING_EXHAUSTED`，真实停点）。
2. **明确 WAIT park**：`WAIT` + `later_item.park_current`，前台释放、Activity 变 `WAITING`
   （不是 PAUSE、不是清指针）。
3. **真实拿起**：`dailyact:PICK_UP` 走 AG-1 → AG-2 → AR-0 → World bridge → Intent Executor →
   gateway → **真实 Resolver**，基底 `hands` 真的变了；完成判据来自动作后的 fresh 观测。
4. **真实外出**：`place:` MOVE 出发（真实旅程），按 owner 自己公布的到达时刻推进测试时钟。
5. **到达 / 停留（若 owner 在那里提供停留）**：`stay:` 由 LR-5 按真实时长记 checkpoint。
6. **回家**：再一段真实旅程回到 `home`。
7. **续接**：owner 真发布了材料的下一版（真实 `commit_version`）→ LR-4 自己的 upkeep 看到 head
   变了 → `RESUME` 候选出现 → 显式 RESUME **同一条 Activity** → 再 `CONTINUE` 读新版本第 1 页。

**明示**：这是测试时钟推进（把 Life 时刻拨到 owner 公布的 `expected_arrival_at` / 停留截止），
以及一段很短的路程；**不是**真实的一天，也**不是** Gemini 自主，模型只是回放原始 JSON 的脚本。
World 侧是真服务（真 gateway.sock、真 Resolver、真基底），A 侧是真 AG-1/AG-2/AR-0/LR-5/LR-2；
唯一不是"真产品代码"的东西是决策脚本本身。

需要 POSIX（fcntl + AF_UNIX）；原生 Windows 自动跳过。夹具保留不删。
"""

from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime
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

from test_world_daily_life_continuous import (  # noqa: E402
    BRIDGE_DIR, EpisodeFixture, WORLD_PACKAGE, posix_only)
import test_life_v1_integration as donor  # noqa: E402
import life_daily_action_targets as daily  # noqa: E402
import life_wait_basis as lwb  # noqa: E402
import autonomous_life_cycle as alc  # noqa: E402
import one_day_life as odl  # noqa: E402
import world_body_substrate as wbs  # noqa: E402
import world_move_opportunity_source as wmos  # noqa: E402
from world_body_client import WorldBodyClient  # noqa: E402
from world_journey_action_adapter import (  # noqa: E402
    DELEGATED_VERBS as PRODUCTION_DELEGATED_VERBS,
    JourneyWorldResolver,
    RealJourneyWorldActionAdapter,
)

#: 她手边这一件普通东西：只在**初始化**时按 owner 的合法格式声明 + 放下（测试中不再改它）。
BOWL = "lived-bowl-01"
HOME = "home"
PLACE_OUT = "street_front"

#: AG-1 可以 START 的候选类别（与既有世界用例同一份清单）。
STARTABLE_KINDS = frozenset({
    "RESPOND_USER_REQUEST", "CONSIDER_WORLD_OPPORTUNITY", "FULFILL_COMMITMENT",
    "PURSUE_GOAL_STEP", "CONSIDER_PERSONAL_OPPORTUNITY",
})


class ChainCognition:
    """回放原始 JSON 的决策脚本：只从 AG-1 真正给的候选里挑，不伪造 id、不带业务字段。"""

    model_id = "stub:lived-day-chain"
    model_version = "lived-day-chain-v1"

    def __init__(self) -> None:
        self.intent = ""
        self.goal = ""
        self.reads: list[dict] = []
        self.offered: list[list] = []
        self.calls = 0
        self.intent = ""
        self.goal = ""
        self._round_legal: list[str] = []
    # -- helpers -----------------------------------------------------------------
    @staticmethod
    def _refs(candidate: dict) -> list[str]:
        return [str(ref) for ref in (candidate.get("target_refs") or [])]

    @staticmethod
    def _legal(candidate: dict) -> list[str]:
        return [str(item) for item in (candidate.get("legal_decisions") or [])]

    def _startable(self, candidates):
        return [c for c in candidates
                if "START" in self._legal(c)
                and c.get("candidate_kind") in STARTABLE_KINDS]

    def _with_refs(self, candidates, prefix: str):
        return [c for c in candidates
                if any(ref.startswith(prefix) for ref in self._refs(c))]

    def _pick(self, candidates, situation):
        intent = self.intent
        startable = self._startable(candidates)
        if intent == "read":
            for candidate in startable:
                if self.cycle_ref("reading") in " ".join(self._refs(candidate)):
                    return candidate, "START", "read her own material"
            return None, "", "no reading candidate was offered"
        if intent == "work":
            for candidate in startable:
                if self.cycle_ref("work") in " ".join(self._refs(candidate)):
                    return candidate, "START", "carry on with her own work"
            return None, "", "no work candidate was offered"

        if intent == "pick_up":
            for candidate in self._with_refs(startable, "dailyact:pick_up:"):
                return candidate, "START", "pick the thing up while I am here"
            return None, "", "no pick-up candidate was offered"
        if intent == "place_down":
            for candidate in self._with_refs(startable, "dailyact:place:"):
                return candidate, "START", "put it down where it belongs"
            return None, "", "no placement candidate was offered"
        if intent == "move_out":
            for candidate in startable:
                if "place:%s" % PLACE_OUT in self._refs(candidate):
                    return candidate, "START", "walk over to %s" % PLACE_OUT
            return None, "", "the destination was not offered"
        if intent == "stay":
            for candidate in self._with_refs(startable, "stay:"):
                return candidate, "START", "stay here a while"
            return None, "", "no stay was offered at this place"
        if intent == "move_home":
            for candidate in startable:
                if "place:%s" % HOME in self._refs(candidate):
                    return candidate, "START", "walk back home"
            return None, "", "home was not offered"
        if intent == "park":
            # 她的停点：这一版读完了、owner 自己也说没有下一步，而这一轮**真的**有一个等待理由
            # （等这本书的下一次发布），所以 WAIT 是合法动词。`park_current` 只在 park 真被提供
            # 时才带上（那是"放下还做得下去的事"），这里如实照做。
            if "WAIT" not in (self._round_legal or []):
                return None, "", "WAIT is not legal this round"
            return None, "WAIT", "wait on the next revision of the book"
        if intent == "resume":
            for candidate in candidates:
                if "RESUME" in self._legal(candidate):
                    return candidate, "RESUME", "come back to what I set down"
            return None, "", "no RESUME candidate was offered"
        if intent == "continue":
            for candidate in candidates:
                if "CONTINUE" in self._legal(candidate):
                    return candidate, "CONTINUE", "go on with it"
            return None, "", "no CONTINUE candidate was offered"
        return None, "", "no intent"

    # the fixture's ``round()`` prints ``self.cognition.reads[-1]``; keep that shape
    cycle_ref = staticmethod(lambda _kind: "")
    refs = {"reading": "", "work": ""}

    # -- the decision ------------------------------------------------------------
    def invoke(self, request, *, schema_retry_index: int = 0):
        import agency_decision_ag1 as ag1

        self.calls += 1
        candidates = [dict(c) for c in (getattr(request, "candidates", []) or [])]
        situation = getattr(request, "situation_context", {}) or {}
        allowed = [str(code) for code in (getattr(request, "allowed_reason_codes", []) or [])]
        self._round_legal = [str(x) for x in (getattr(request, "legal_decision_enums", []) or [])]
        self.offered.append([
            {"kind": c.get("candidate_kind"), "targets": self._refs(c),
             "legal": self._legal(c), "candidate_id": c.get("candidate_id")}
            for c in candidates])
        chosen, decision, reason = self._pick(candidates, situation)
        self.reads.append({
            "intent": self.intent, "chose": (self._refs(chosen)[0] if chosen else None),
            "decision": decision, "reason": reason,
            "legal_enums": list(self._round_legal),
            "offered_targets": sorted({ref for c in candidates for ref in self._refs(c)}),
            "world": (situation.get("world") or {}),
            "park": situation.get("park") or {},
        })
        raw: dict = {"decision": decision or "NO_ACTION",
                     "selected_candidate_id": (chosen.get("candidate_id") if chosen else None),
                     "reason_codes": allowed[:1], "short_rationale": reason[:200],
                     "deferred_candidate_ids": []}
        if decision == "WAIT":
            # the ONE thing she wants to do later.  ``park_current`` is only honest when parking a
            # *continuable* Activity is really on offer; here she is waiting on a dependency, so the
            # item is a plain note to herself and the WAIT itself names the dependency.
            item = {"title": "等这本书的下一版", "in_minutes": 30}
            if (situation.get("park") or {}).get("available"):
                item["park_current"] = True
            raw["later_item"] = item
        return ag1.CognitiveDecisionResponse(
            model_run_ref="model_run:chain:%d" % self.calls, model_id=self.model_id,
            model_version=self.model_version, raw_output=raw, latency_ms=1)


@posix_only
class LivedDayChainTest(EpisodeFixture):
    """一个实例、一条链：读→park→拿起→出门→(停留)→回家→续接。"""

    def setUp(self) -> None:
        # 组合 D 的 owner 装配（只 import，不继承它的 TestCase），与既有世界夹具同一套。
        self.base = donor.DScenarioBase("runTest")
        self.base.setUp()
        self.addCleanup(self._close)
        self.base.world.package_dir = WORLD_PACKAGE
        self.prepared = self.base.prepare_scenario(keep_fixture_world=True)
        self.data = self.prepared.data
        self.memory = self.prepared.memory
        self.world_home = Path(str(self.data["world_home"]))
        self.instance = Path(self.base.instance)
        self._declare_takeable()
        self._place_bowl(HOME)
        from test_world_daily_life_continuous import Clock, episode_instant
        self.clock = Clock(episode_instant(after=datetime.now().astimezone()))
        self.gateway = self.base.gateway()
        self.env = self.base.instance_env()
        self.provider = wmos.WorldMoveOpportunityProvider(
            client=WorldBodyClient(environment=self.env), action_intent=__import__("action_intent"),
            bridge_dir=str(BRIDGE_DIR), environment=self.env, world_home=str(self.world_home),
            now=lambda: self.clock.now, observed_now=lambda: self.clock.now,
            valid_for_seconds=60)
        self.cognition = ChainCognition()
        self.runtime, self.port = self.base.runtime_with_memory(
            self.cognition, self.env, self.data, self.memory)
        self.fixture = self.base.assemble(self.runtime, self.env, observation=self.provider)
        self.cycle = self.fixture.cycle
        self.cycle.world_home = str(self.world_home)
        self.cycle.work_artifact_id = self.data["work_artifact_id"]
        self.state_root = odl.life_state_root(self.instance)
        # 正式生产 seam，一字未替换：AR-0 适配器背后就是这个实例真实装配的 journey resolver，
        # 拿放动词必须由它自己委派给 World 的正式入口（见测试末尾对 DELEGATED_VERBS 的断言）。
        self.resolver = self.runtime.world_adapter.resolver
        self.assertIsInstance(self.runtime.world_adapter, RealJourneyWorldActionAdapter)
        # 脚本按 target 找候选时用得到的两件真东西
        self.cognition.refs = {"reading": str(self.data.get("reading_artifact_id") or ""),
                               "work": str(self.data.get("work_artifact_id") or "")}
        self.cognition.cycle_ref = lambda kind: self.cognition.refs.get(kind, "")

    # -- 初始化时的 owner 合法写入（测试中不再为了结果改它） ---------------------------
    def _declare_takeable(self) -> None:
        path = self.world_home / "state" / "world_object_capabilities.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        document = {}
        if path.exists():
            document = json.loads(path.read_text(encoding="utf-8"))
        document.setdefault("schema_version", wbs.CAPABILITIES_SCHEMA)
        document.setdefault("objects", {})[BOWL] = {"takeable": "true", "placeable": "true"}
        path.write_text(json.dumps(document, ensure_ascii=False, sort_keys=True),
                        encoding="utf-8")

    def _place_bowl(self, place: str) -> None:
        store = wbs.SubstrateStore(wbs.substrate_path(self.world_home))
        state = store.load()
        self.assertIsNotNone(state, "init_instance must have written a substrate")
        store.save(wbs.place_object(state, BOWL,
                                    {"kind": "room", "location_id": place, "area_id": place}))

    # -- one intent-driven round ----------------------------------------------------
    def turn(self, intent: str, *, trigger: str) -> dict:
        self.cognition.intent = intent
        out = self.cycle.evaluate_once(trigger_ref=trigger, observed_at=self.clock.iso(),
                                       act=True)
        read = self.cognition.reads[-1] if self.cognition.reads else {}
        print("CHAIN %-11s outcome=%-16s decision=%-9s chose=%s place=%s"
              % (intent, out.get("outcome"), out.get("decision"), read.get("chose"),
                 self.current_place()))
        if out.get("outcome") in {"NO_ACTION", "DECISION_FAILED"}:
            print("CHAIN   offered=%s" % json.dumps(read.get("offered_targets"), ensure_ascii=False))
            print("CHAIN   legal=%s" % json.dumps(read.get("legal_enums"), ensure_ascii=False))
        return out

    def _advance_to_owner_arrival(self, journey: dict) -> dict:
        """先把时钟拨到 owner 自己公布的到达时刻，再让真实旅程结算这一段。"""
        arrival = str((journey or {}).get("expected_arrival_at") or "")
        self.assertTrue(arrival, journey)
        # 显式的测试时钟：把 Life 时刻推到 owner 自己公布的那一段到达时刻（不睡真实秒）。这个
        # owner 用墙钟给这一段计时，而本链的 Life 时钟是注入的：当公布的时刻已经落在 Life 时钟
        # 之前（本链已经"在那之后"），再往前推一分钟让这一段的到期成立即可。
        target = datetime.fromisoformat(arrival.replace("Z", "+00:00"))
        seconds = int((target - self.clock.now).total_seconds())
        self.clock.advance(max(60, seconds),
                           because="walk:%s" % journey.get("destination_location_id"))
        settled = self.cycle.settle_world_journey(observed_at=self.clock.iso())
        print("CHAIN settle   arrived=%s at=%s status=%s"
              % (settled.get("arrived"), settled.get("location_id"), settled.get("journey_status")))
        return settled

    # -- the one chain ---------------------------------------------------------------
    def test_one_instance_reads_parks_picks_up_goes_out_and_resumes_the_same_activity(self):
        reading_id = self.cognition.refs["reading"]
        self.assertTrue(reading_id, "the instance must carry a real reading material")

        # 1) 读到这一版的真停点（一页一轮；第三页之后 owner 的阅读状态就是 EXHAUSTED）
        for page in (1, 2, 3):
            out = self.turn("read" if page == 1 else "continue",
                            trigger="chain:read:%d" % page)
            self.assertIn(out.get("outcome"), {"ACTED", "ALREADY_DONE"}, out)
            situation = self.cycle.private_material_situation() or {}
            print("CHAIN read page=%d state=%s next=%s"
                  % (page, situation.get("state"),
                     (situation.get("position") or {}).get("next_page")))
        # 停点：这一版真的读完了（停在第 3 页），停点绑在 owner 的那个版本上。
        stop_head = self.head(reading_id)
        self.assertTrue(stop_head.get("version_id"), stop_head)

        # 2) 明确 WAIT park：她自己选 WAIT（合法是因为这一轮真的有等待理由），前台释放、
        #    Activity 变 WAITING —— 不是 PAUSE，也不是清指针。
        out = self.turn("park", trigger="chain:park")
        self.assertIn("WAIT", self.cognition.reads[-1]["legal_enums"])
        self.assertEqual(out.get("step_readiness", {}).get("reason_code"),
                         "READING_EXHAUSTED", out.get("step_readiness"))
        self.assertNotIn("CONTINUE", self.cognition.reads[-1]["legal_enums"])
        self.assertEqual(out.get("decision"), "WAIT", out)
        self.assertEqual(out.get("outcome"), "PAUSED", out)
        exit_report = out.get("exit") or {}
        self.assertTrue(exit_report.get("applied"), exit_report)
        # owner 自己的状态是 WAITING（等她等的那个依赖），**不是** PAUSED
        self.assertEqual(exit_report.get("owner_status"), "WAITING", exit_report)
        parked_ref = out.get("activity_ref")
        self.assertTrue(parked_ref, out)
        parked = self.activity(parked_ref)
        self.assertEqual(str(parked.get("status")), "WAITING", parked)
        # 这个 owner 的事实：WAIT 之后 Activity 变 WAITING，前台指针仍指着**同一条** Activity
        # （不是被清空，也不是 PAUSED）；`exit` 报告里 owner 自己的状态才是判据。
        print("CHAIN park     foreground=%s status=%s"
              % (self.foreground(), self.activity(self.foreground() or "").get("status")))
        self.assertEqual(self.foreground(), parked_ref, "the foreground still names the parked Activity")

        # 3) 真实拿起：AG-1 → AG-2 → AR-0 → bridge → IntentExecutor → gateway → Resolver
        out = self.turn("pick_up", trigger="chain:pick_up")
        _ref = out.get("selected_candidate_ref")
        _record = self.runtime.candidate_read.get_candidate(str(_ref)) or {}
        self.assertTrue(self.cycle._candidate_is_world(_record),
                        "the committed candidate must be the World one: %s"
                        % (_record.get("target_refs"),))
        self.assertEqual(out.get("outcome"), "ACTED", out)
        self.assertEqual(out.get("world_verb"), "PICK_UP", out)
        self.assertTrue(str(out.get("daily_action", {}).get("satisfied")), out.get("daily_action"))
        self.assertTrue((out.get("daily_action") or {}).get("observation", {}).get("re_read"),
                        out.get("daily_action"))
        placement = self.placement(BOWL) or {}
        self.assertEqual(str(placement.get("kind")), "hand",
                         "the World owner's own substrate must really hold it: %s" % (placement,))
        self.assertEqual(str(placement.get("slot_id")), "left_hand", placement)
        pick_activity = out.get("activity_ref")
        self.assertTrue(pick_activity, out)
        # 步骤成功只写进度；完成仍由 LR-5 的完成契约自己判（"物件真的在手里"），不由本文件写
        self.assertEqual(str(self.activity(pick_activity).get("status")), "COMPLETED",
                         self.activity(pick_activity))
        print("CHAIN pick done progress=%s owner=%s foreground=%s"
              % (json.dumps(out.get("progress_state"), ensure_ascii=False, default=str)[:160],
                 self.activity(pick_activity).get("status"), self.foreground()))
        self.assertIsNone(self.foreground(), "the completed hand Activity frees the foreground")

        # 4) 真实放下：同一条正式 resolver 链，目标由 B 的编码器给出，落到 owner 自己的 placement
        out = self.turn("place_down", trigger="chain:place_down")
        self.assertEqual(out.get("outcome"), "ACTED", out)
        self.assertEqual(out.get("world_verb"), "PLACE", out)
        self.assertTrue(str(out.get("daily_action", {}).get("satisfied")), out.get("daily_action"))
        placed = self.placement(BOWL) or {}
        self.assertNotEqual(str(placed.get("kind")), "hand",
                            "putting it down must really leave the hand: %s" % (placed,))
        place_activity = out.get("activity_ref")
        self.assertTrue(place_activity, out)
        self.assertEqual(str(self.activity(place_activity).get("status")), "COMPLETED",
                         self.activity(place_activity))
        print("CHAIN place done placement=%s owner=%s"
              % (json.dumps(placed, ensure_ascii=False)[:200],
                 self.activity(place_activity).get("status")))

        # 5) 真实外出旅程（出发 ≠ 到达）
        out = self.turn("move_out", trigger="chain:out")
        self.assertEqual(out.get("outcome"), "IN_TRANSIT", out)
        self.assertEqual(self.current_place(), HOME, "a departure is not an arrival")
        settled = self._advance_to_owner_arrival(out.get("journey") or {})
        self.assertTrue(settled.get("arrived"), settled)
        self.assertEqual(self.current_place(), PLACE_OUT, settled)

        # 5) 到达后的停留（owner 在这里提供则真的待一会；结束仍是她自己的下一轮步骤）
        # 6) 回家（再一段真实旅程），到家后 owner 在这里提供一次真实停留
        out = self.turn("move_home", trigger="chain:home")
        self.assertEqual(out.get("outcome"), "IN_TRANSIT", out)
        self.assertEqual(self.current_place(), PLACE_OUT, "the return leg departs first too")
        settled = self._advance_to_owner_arrival(out.get("journey") or {})
        self.assertTrue(settled.get("arrived"), settled)
        self.assertEqual(self.current_place(), HOME, settled)

        stay_out = self.turn("stay", trigger="chain:stay")
        self.assertIn(stay_out.get("outcome"), {"ACTED", "ALREADY_DONE"}, stay_out)
        planned = int(((stay_out.get("stay") or {}).get("planned_seconds")) or 0)
        self.assertGreater(planned, 0, stay_out)
        self.clock.advance(planned + 5, because="stay:%s" % HOME)
        ended = self.turn("continue", trigger="chain:stay:end")
        print("CHAIN stay   planned=%s end=%s stay=%s"
              % (planned, ended.get("outcome"),
                 json.dumps(ended.get("stay") or {}, ensure_ascii=False)[:200]))
        self.assertIn(ended.get("outcome"), {"ACTED", "ALREADY_DONE"}, ended)
        self.assertEqual(str((ended.get("stay") or {}).get("phase")),
                         alc.STAY_PHASE_END, ended.get("stay"))

        # 7) owner 真发布了下一版 → LR-4 自己的 upkeep 看到 head 变了 → 显式 RESUME 并续接
        new_head = self.port.commit_version(
            artifact_id=reading_id, expected_head=stop_head.get("version_id"),
            content=("第一页（新版）：窗外的街换了颜色。\n%%PAGE%%\n"
                     "第二页（新版）：有人在楼下搬东西。\n%%PAGE%%\n"
                     "第三页（新版）：灯亮了。\n"),
            grant_id=self.data["artifact_grant_id"],
            operation_key="chain:reading:next-edition:1")
        self.assertTrue(new_head.applied, new_head.to_dict())
        upkeep = self.cycle.maintain_waiting(observed_at=self.clock.iso())
        print("CHAIN upkeep   checked=%s changed=%s satisfied=%s eligibilities=%s"
              % (upkeep.get("checked"), upkeep.get("changed"), upkeep.get("satisfied"),
                 upkeep.get("emitted_eligibilities")))
        self.assertFalse(upkeep.get("auto_resumed"), upkeep)
        self.assertEqual(upkeep.get("changed"), 1, upkeep)

        resumed = self.turn("resume", trigger="chain:resume")
        self.assertEqual(resumed.get("decision"), "RESUME", resumed)
        self.assertEqual(resumed.get("adoption_status"), "APPLIED", resumed)
        self.assertEqual(resumed.get("activity_ref"), parked_ref,
                         "she must come back to the *same* Activity, not a new one")
        self.assertEqual(str(self.activity(parked_ref).get("status")), "ACTIVE",
                         self.activity(parked_ref))
        self.assertEqual(self.foreground(), parked_ref)

        out = self.turn("continue", trigger="chain:continue")
        self.assertIn(out.get("outcome"), {"ACTED", "ALREADY_DONE"}, out)
        situation = self.cycle.private_material_situation() or {}
        position = situation.get("position") or {}
        print("CHAIN resumed  state=%s page=%s next=%s"
              % (situation.get("state"), position.get("page"), position.get("next_page")))
        self.assertIsNotNone(position.get("next_page"),
                             "continuing must really read the new revision")

        # 正式生产 seam 自己的能力（不是本文补的）：拿放动词在 journey resolver 的委派清单里，
        # 而这条链从头到尾用的就是这个类自己的默认 resolver。
        self.assertIsInstance(self.resolver, JourneyWorldResolver)
        self.assertIn("PICK_UP", PRODUCTION_DELEGATED_VERBS)
        self.assertIn("PLACE", PRODUCTION_DELEGATED_VERBS)
        self.assertEqual(self.runtime.world_adapter.resolver, self.resolver)


if __name__ == "__main__":
    unittest.main(verbosity=2)

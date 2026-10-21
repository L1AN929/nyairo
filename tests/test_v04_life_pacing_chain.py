#!/usr/bin/env python3
"""v0.4 A（有松有紧的生活）：一条同实例、正常入口的完整“放下—缓过来—重新接上”链。

这条链只走正式入口，没有任何一步用 helper 冒充整轮：

```
真实 Supply/World/Memory owner + 真实 assemble_autonomous_life
  r1  Decision START   -> 真的开始读这份材料（第 1 页）
  r2  Decision WAIT    -> owner 把同一个 Activity 置为 WAITING，**保留指针**（材料/页码/位置都不动），
                          前台被 LR-2 按 ``release_foreground`` 放掉；条件是身体那一个
                          （``wait_dep:body.<身份>.<基线>``，LR-4 里 PENDING）
  r3  Decision START   -> bodyact:pose（真实姿态，owner 自己的 snapshot 变了）
  r4  Decision START   -> bodyact:rest:start（owner 自己开恢复会话；这个 REST Activity 由 LR-5 完成，
                          不再占前台）
      owner tick       -> 用可控 clock 推进同一台 World owner，恢复档位/趋势真的变了（读回来的）
      maintain         -> 身体条件被 LR-4 判 SATISFIED，只产出一个 OPEN eligibility（零模型、不自动恢复）
  r5  Decision START   -> bodyact:rest:stop（会话由 owner 关掉；实测回 ``completed``，不是“恢复了”）
  r6  Decision RESUME  -> 同一个 Activity 被重新认领，读到**下一页**（同一材料、同一位置往后）
```

机制边界（照实声明，不冒充“真的过了几个小时”）：

* 身体负荷是**夹具在 owner 启动前**种下的（``fatigue=0.60``，见 ``PacingChainFixture``）——这是脚本
  选择，不是她自然累的；恢复是同一台 owner 在**可控 clock** 下按自己的规则算出来的，不是我们写死的。
* tick 只在测试里显式调用，interval 由 ``Clock`` 给；没有真实时间流逝，也没有“真实度过数小时”。
* 没有外发、没有真模型：决策是只回放原始 JSON 的脚本（父级用真模型时把 cognition 换掉即可）。

夹具：所有目录只建不删（继承 donor 时**跳过**它的 rmtree teardown，见 ``PacingChainBase.tearDown``）。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT),
              str(ROOT / "tests"),
              str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge"),
              str(ROOT / "components" / "native" / "integration"),
              str(ROOT / "components" / "supply")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import agency_decision_ag1 as ag1  # noqa: E402
import one_day_life as odl  # noqa: E402

POSIX_ONLY = unittest.skipUnless(os.name != "win32",
                                 "真实 World/Body socket + fcntl 需要 POSIX")

_IMPORT_ERROR: str | None = None
try:
    import body_runtime as body_runtime  # noqa: E402
    import world_body_client as wbc  # noqa: E402
    import world_body_config as wcfg  # noqa: E402
    import world_body_service as wbsvc  # noqa: E402
    import world_move_opportunity_source as wmos  # noqa: E402
    import world_runtime_data as wrd  # noqa: E402
    from world_body_read_port import WorldBodyReadPort  # noqa: E402

    import test_life_v1_integration as donor  # noqa: E402
    from test_world_daily_life_continuous import WORLD_PACKAGE, episode_instant  # noqa: E402
except Exception as error:  # noqa: BLE001
    _IMPORT_ERROR = "%s: %s" % (type(error).__name__, error)

needs_donor = unittest.skipIf(_IMPORT_ERROR is not None,
                              "donor fixture unavailable: %s" % (_IMPORT_ERROR or ""))

BRIDGE_DIR = ROOT / "components" / "world" / "service" / "bridge"

#: 夹具声明的负荷（不是她自然累的）：高于 owner 自己的 ``RECOVERY_OPEN_FATIGUE``。
SEEDED_FATIGUE = 0.60
#: 一次 owner tick 推进的秒数（可控 clock）。
TICK_SECONDS = 600


class Clock:
    """The one knob the fixture owns: the World owner's own moment."""

    def __init__(self, moment):
        self.moment = moment

    def advance(self, seconds: float) -> "Clock":
        self.moment = self.moment + timedelta(seconds=float(seconds))
        return self

    def iso(self) -> str:
        return self.moment.isoformat()


class ClockedGateway:
    """The real ``world_body_service`` with only its *moment* supplied by the fixture.

    Nothing about the owner is re-implemented: it is the production service loop bound to the
    instance's own world home, with its own resolver, ledger, lease and gateway socket.  The
    subclass below overrides exactly one method (``_moment``) so a real owner tick can be driven
    at a declared interval instead of waiting wall-clock hours.
    """

    class _Service(wbsvc.WorldBodyService):
        clock: "Clock | None" = None

        def _moment(self):  # noqa: D401 - the owner's clock, injected
            return self.clock.moment

    def __init__(self, world_home, *, world_id, environment, clock, tick_seconds=3600.0):
        self.world_home = Path(world_home)
        self.clock = clock
        self.config = wcfg.load_config(dict(environment),
                                       runtime_root=ROOT / "components" / "world",
                                       world_home=self.world_home, runtime_mode="canonical",
                                       authority="production", world_id=world_id)
        self.service = self._Service(self.config, tick_seconds=tick_seconds,
                                     instance_id="pacing-%d" % os.getpid())
        self.service.clock = clock
        self.startup_report = self.service.startup()
        self.socket_path = self.service.gateway_socket_path
        self._thread = threading.Thread(target=self.service.serve,
                                        kwargs={"max_seconds": 1800.0}, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 30.0
        while not self.socket_path.exists():
            if time.monotonic() > deadline:
                raise AssertionError("World gateway socket 没出现: %s" % self.socket_path)
            time.sleep(0.02)

    def environment(self) -> dict:
        return {"WORLD_BODY_SOCKET": str(self.socket_path),
                "WORLD_BODY_INTEGRATION_MODE": "canonical",
                "NYAIRO_WORLD_BRIDGE_DIR": str(BRIDGE_DIR)}

    def tick(self):
        return self.service.tick()

    def stop(self) -> None:
        try:
            self.service.shutdown()
        except Exception:  # noqa: BLE001 - 已关闭不是测试结论
            pass
        self._thread.join(timeout=5)


class PacingScript:
    """Raw-JSON decision script: it only names ids the real prompt really offered."""

    model_id = "stub:life-pacing"
    model_version = "life-pacing-v1"

    def __init__(self) -> None:
        self.plan: list[str] = []
        self.seen: list[dict] = []
        self.calls = 0
        self.reading_id = ""

    @staticmethod
    def _targets(candidate) -> list[str]:
        return [str(ref) for ref in (candidate.get("target_refs") or [])]

    def invoke(self, request, *, schema_retry_index: int = 0):
        self.calls += 1
        allowed = [str(code) for code in (getattr(request, "allowed_reason_codes", []) or [])]
        situation = getattr(request, "situation_context", {}) or {}
        candidates = [dict(row) for row in (getattr(request, "candidates", []) or [])]
        step = self.plan.pop(0) if self.plan else "noop"
        raw = {"decision": "NO_ACTION", "selected_candidate_id": None,
               "reason_codes": allowed[:1] or ["NO_ACTION"],
               "short_rationale": "quiet", "deferred_candidate_ids": []}

        def pick(predicate):
            for candidate in candidates:
                if predicate(candidate):
                    return candidate
            return None

        chosen = None
        if step == "read":
            chosen = pick(lambda c: "START" in (c.get("legal_decisions") or [])
                          and self.reading_id and self.reading_id in " ".join(self._targets(c)))
            if chosen:
                raw.update(decision="START", short_rationale="read a page of my own material")
        elif step == "wait":
            chosen = pick(lambda c: "WAIT" in (c.get("legal_decisions") or []))
            if chosen:
                raw.update(decision="WAIT",
                           short_rationale="my body is like this; keep my place")
        elif step == "pose":
            chosen = pick(lambda c: "START" in (c.get("legal_decisions") or [])
                          and any(ref.startswith("bodyact:pose:") for ref in self._targets(c)))
            if chosen:
                raw.update(decision="START", short_rationale="sit down for a while")
        elif step == "rest_start":
            chosen = pick(lambda c: "START" in (c.get("legal_decisions") or [])
                          and any(ref.startswith("bodyact:rest:start") for ref in self._targets(c)))
            if chosen:
                raw.update(decision="START", short_rationale="rest a while")
        elif step == "rest_stop":
            chosen = pick(lambda c: "START" in (c.get("legal_decisions") or [])
                          and any(ref.startswith("bodyact:rest:stop") for ref in self._targets(c)))
            if chosen:
                raw.update(decision="START", short_rationale="enough rest")
        elif step == "resume":
            chosen = pick(lambda c: "RESUME" in (c.get("legal_decisions") or []))
            if chosen:
                raw.update(decision="RESUME", short_rationale="back to my page")
        if chosen:
            raw["selected_candidate_id"] = chosen["candidate_id"]
        self.seen.append({
            "step": step,
            "chosen": (chosen or {}).get("candidate_id"),
            "legal": [(c.get("candidate_kind"), list(c.get("legal_decisions") or []))
                      for c in candidates],
            "breakpoint": json.loads(json.dumps(situation.get("breakpoint") or {})),
            "body": json.loads(json.dumps(situation.get("body") or {})),
        })
        return ag1.CognitiveDecisionResponse(
            model_run_ref="model_run:pacing:%d" % self.calls, model_id=self.model_id,
            model_version=self.model_version, raw_output=raw, latency_ms=1)


class PacingChainFixture:
    """One real isolated instance + one real World/Body owner with a declared load and clock.

    复用方式（父级真模型验收也走这套）：

    * ``fixture = PacingChainFixture(fatigue=0.60)``：建实例、种负荷、起 owner（都在 owner 启动**之前**
      种好，所以不碰它的运行中状态）、接真实 Supply/Memory/AG-2/runtime/assembly；
    * ``fixture.turn("read" | "wait" | "pose" | "rest_start" | "rest_stop" | "resume" | "noop")``：
      一轮正式 ``cycle.evaluate_once(act=True)``；
    * ``fixture.owner_tick(n)``：n 次真实 owner tick（每次推进 ``TICK_SECONDS``，由可控 clock 给）；
    * ``fixture.maintain()``：一次零模型 upkeep（= ``runner.maintain`` 调用的同一个方法）；
    * 读证据：``fixture.reading()`` / ``fixture.activity(ref)`` / ``fixture.foreground()`` /
      ``fixture.rest_entry()`` / ``fixture.recovery()`` / ``fixture.conditions()`` /
      ``fixture.eligibilities()``；
    * 换成真模型：``fixture.runtime.cognition_adapter = <ProductionAgencyCognitionAdapter>``
      （或构造后替换 ``fixture.script``），其余不变；预算由父级持有。
    """

    def __init__(self, *, fatigue: float = SEEDED_FATIGUE):
        self._saved_env = dict(os.environ)
        self.base = donor.DScenarioBase("runTest")
        self.base.setUp()
        self.base.world.package_dir = WORLD_PACKAGE
        prepared = self.base.prepare_scenario(keep_fixture_world=True)
        self.data = prepared.data
        self.memory = getattr(prepared, "memory", None)
        self.instance = Path(self.base.instance)
        self.world_home = Path(str(self.data["world_home"]))
        self.clock = Clock(episode_instant(after=datetime.now().astimezone()))
        #: 夹具声明的负荷（owner 启动前种下）：脚本选择，不是自然生活。
        self.seeded_fatigue = float(fatigue)
        state = body_runtime.initial_runtime(at=self.clock.moment)
        state["somatic"]["fatigue"] = self.seeded_fatigue
        body_runtime.BodyStore(self.world_home / "data" / "body").save_runtime(state)
        self.env = odl.instance_environment(self.instance)
        self.gateway = ClockedGateway(self.world_home, world_id=self.data["world_id"],
                                      environment=self.env, clock=self.clock)
        self.env.update(self.gateway.environment())
        os.environ.update(self.env)
        wrd.reset_cache()
        self.client = wbc.WorldBodyClient(environment=self.env)
        self.port = WorldBodyReadPort(self.client)
        self.provider = wmos.WorldMoveOpportunityProvider(
            client=self.client, action_intent=__import__("action_intent"),
            bridge_dir=str(BRIDGE_DIR), environment=self.env, world_home=str(self.world_home),
            now=lambda: self.clock.moment, observed_now=lambda: self.clock.moment,
            valid_for_seconds=60)
        self.script = PacingScript()
        self.script.reading_id = str(self.data.get("reading_artifact_id") or "")
        memory = self.base.memory()
        self.runtime, self.port_adapter = self.base.runtime_with_memory(
            self.script, self.env, self.data, memory)
        self.assembled = self.base.assemble(self.runtime, self.env, observation=self.provider)
        self.cycle = self.assembled.cycle
        self.cycle.world_home = str(self.world_home)
        self.cycle.work_artifact_id = self.data["work_artifact_id"]
        self.cycle.reading_artifact_id = self.script.reading_id
        self.rounds: list[dict] = []

    # -- rounds / owner ticks -----------------------------------------------------------
    def turn(self, step: str, *, label: str | None = None) -> dict:
        self.script.plan = [step]
        out = self.cycle.evaluate_once(
            trigger_ref="pacing:%s:%d" % (label or step, len(self.rounds) + 1),
            observed_at=self.clock.iso(), act=True)
        self.rounds.append(out)
        return out

    def owner_tick(self, times: int = 1) -> list:
        reports = []
        for _ in range(times):
            self.clock.advance(TICK_SECONDS)
            reports.append(self.gateway.tick())
        return reports

    def maintain(self) -> dict:
        return self.cycle.maintain_waiting(observed_at=self.clock.iso())

    # -- owner reads --------------------------------------------------------------------
    def reading(self) -> dict:
        situation = self.cycle.private_material_situation(refresh=True) or {}
        material = situation.get("material") if isinstance(
            situation.get("material"), dict) else {}
        position = situation.get("position") if isinstance(
            situation.get("position"), dict) else {}
        return {"state": situation.get("state"),
                "material_version_id": material.get("version_id"),
                "material_title": material.get("title"),
                "page": position.get("page"),
                "next_page": position.get("next_page"),
                "detail": situation.get("detail")}

    def activity(self, ref) -> dict:
        return dict(self.runtime.activity_read.get_activity(str(ref)) or {})

    def foreground(self):
        view = self.runtime.activity_read.get_current_life_view() or {}
        return view.get("foreground_activity_ref")

    def rest_entry(self) -> dict:
        actions = (self.client.get_available_actions().data or {}).get("actions") or []
        for entry in actions:
            if entry.get("verb") == "REST":
                return dict(entry)
        return {}

    def recovery(self) -> dict:
        payload = self.port.body() or {}
        data = payload.get("data") or {}
        return {"recovery": dict(data.get("recovery") or {}),
                "signals": [dict(row) for row in (data.get("signals") or [])],
                "pose": data.get("pose")}

    def conditions(self) -> dict:
        state = self.runtime.condition_store.load_state() or {}
        return {cid: {"status": row.get("status"), "kind": row.get("condition_kind"),
                      "waiting_on_ref": row.get("waiting_on_ref"),
                      "parameters": dict(row.get("parameters") or {})}
                for cid, row in (state.get("conditions") or {}).items()}

    def eligibilities(self) -> dict:
        state = self.runtime.eligibility_store.load_state() or {}
        return {eid: {"status": row.get("status"), "activity_id": row.get("activity_id"),
                      "resume_condition_ref": row.get("resume_condition_ref")}
                for eid, row in (state.get("eligibilities") or {}).items()}

    # -- resources ----------------------------------------------------------------------
    def close(self) -> None:
        for runtime in list(getattr(self.base, "_runtimes", []) or []):
            try:
                runtime.close()
            except Exception:  # noqa: BLE001 - 关闭失败不改变测试结论
                pass
        try:
            self.base._runtimes = []
            store = getattr(self.memory, "store", None)
            if store is not None and hasattr(store, "close"):
                store.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.base.services.stop()
        except Exception:  # noqa: BLE001
            pass
        self.gateway.stop()
        os.environ.clear()
        os.environ.update(self._saved_env)
        wrd.reset_cache()


@POSIX_ONLY
@needs_donor
class PacingChainBase(unittest.TestCase):
    """Donor fixture, minus its ``rmtree`` teardown：失败的运行必须把状态留在盘上。"""

    fatigue = SEEDED_FATIGUE

    def setUp(self) -> None:
        self.fx = PacingChainFixture(fatigue=self.fatigue)
        self.addCleanup(self._close)

    def _close(self) -> None:
        try:
            self.fx.close()
        except Exception:  # noqa: BLE001 - 资源关闭不是测试结论
            pass
        # 故意**不**调用 donor 的 tearDown：``WorldGenTestCase.tearDown`` 会 rmtree 掉整个实例根。
        # 这里只关自己的资源、保留夹具；没有全局 no-op unlink，也没有删任何东西。
        print("\nfixture kept: %s" % getattr(self.fx, "instance", "?"))


class PacingChainTest(PacingChainBase):
    """放下 → 缓过来 → 重新接上（一切都在正式轮里）。"""

    def test_the_whole_chain_returns_to_the_same_page_read(self):
        fx = self.fx
        # ---- r1：真的开始读这份材料 ------------------------------------------------
        first = fx.turn("read", label="read")
        self.assertEqual(first.get("outcome"), "ACTED", first.get("detail"))
        reading_activity = first.get("activity_ref")
        self.assertTrue(reading_activity)
        page_one = fx.reading()
        self.assertEqual(page_one["page"], 1)
        self.assertEqual(fx.activity(reading_activity)["status"], "ACTIVE")

        # ---- r2：正常 Decision 明确留下断点（身体条件），前台被放掉 -------------------
        wait = fx.turn("wait", label="wait")
        self.assertEqual(wait.get("decision"), "WAIT", wait.get("detail"))
        self.assertEqual(wait.get("outcome"), "PAUSED", wait.get("detail"))
        basis = wait.get("wait_basis") or {}
        self.assertEqual(basis.get("source"), "BODY_CAPACITY_RESTED", basis)
        self.assertEqual(basis.get("baseline_capacity"), "LIMITED", basis)
        self.assertEqual(basis.get("baseline_body_ref"), "chiyo_body", basis)
        self.assertTrue(basis.get("waiting_on_ref", "").startswith("wait_dep:body."), basis)
        # owner 自己把 Activity 置为 WAITING，指针（材料/条件）仍在，前台被释放。
        record = fx.activity(reading_activity)
        self.assertEqual(record["status"], "WAITING")
        self.assertEqual(record.get("waiting_on_ref"), basis["waiting_on_ref"])
        self.assertTrue(record.get("resume_condition_ref"))
        self.assertIsNone(fx.foreground())
        # 位置一点没动：同一份材料、还是停在第 1 页。
        kept = fx.reading()
        self.assertEqual(kept["material_version_id"], page_one["material_version_id"])
        self.assertEqual(kept["page"], 1)
        # LR-4 里是**一个 PENDING 的身体条件**，不是计时器。
        conditions = fx.conditions()
        self.assertEqual(len(conditions), 1, conditions)
        only = list(conditions.values())[0]
        self.assertEqual(only["status"], "PENDING")
        self.assertEqual(only["kind"], "EXTERNAL_RESULT_EVENT")
        self.assertEqual(only["waiting_on_ref"], basis["waiting_on_ref"])
        self.assertEqual(only["parameters"]["basis_source"], "BODY_CAPACITY_RESTED")
        self.assertNotIn("condition_expires_at", only["parameters"])
        # 模型看到的就是这份有界断点（不是它自己猜的私有协议）。
        seen = fx.script.seen[-1]["breakpoint"]
        self.assertTrue(seen["available"], seen)
        self.assertEqual(seen["keeps"]["activity_ref"], reading_activity)
        self.assertEqual(seen["condition"]["baseline"]["burden"],
                         [{"signal": "fatigue_noticeable", "band": "mild"}])
        self.assertTrue(seen["condition"]["releases_foreground"])

        # ---- r3：真实姿态（坐下/躺下由 owner 自己的 offer 决定） ---------------------
        pose = fx.turn("pose", label="pose")
        self.assertEqual(pose.get("outcome"), "ACTED", pose.get("detail"))
        self.assertIn((fx.rest_entry().get("current") or {}).get("pose"), ("seated", "lying"),
                      fx.rest_entry())
        self.assertIs(fx.rest_entry().get("start_available"), True, fx.rest_entry())

        # ---- r4：REST start 走正式链，且这个 Activity 由 LR-5 完成（不占前台） -------
        rest_start = fx.turn("rest_start", label="rest-start")
        self.assertEqual(rest_start.get("outcome"), "ACTED", rest_start.get("detail"))
        rest_activity = rest_start.get("activity_ref")
        self.assertTrue(rest_activity)
        self.assertEqual(fx.activity(rest_activity)["status"], "COMPLETED")
        self.assertIsNone(fx.foreground())
        start_entry = fx.rest_entry()
        self.assertEqual((start_entry.get("current") or {}).get("recovery_state"), "active")
        self.assertIs(start_entry.get("stop_available"), True, start_entry)
        before = fx.recovery()["recovery"]
        self.assertEqual(before.get("state"), "active")

        # ---- owner tick：真实恢复趋势/档位变化（同一台 owner，可控 clock） ----------
        reports = fx.owner_tick(4)
        self.assertTrue(all((report or {}).get("advance", {}).get("wrote") for report in reports),
                        reports)
        after = fx.recovery()["recovery"]
        self.assertNotEqual(after.get("progress_band"), before.get("progress_band"),
                            (before, after))
        self.assertEqual(after.get("trend"), "improving", after)

        # ---- maintain：身体条件被 LR-4 判 SATISFIED，只产出一个资格，不自动恢复 -------
        upkeep = fx.maintain()
        self.assertEqual(upkeep.get("body_checked"), 1, upkeep)
        self.assertEqual(upkeep.get("body_changed"), 1, upkeep)
        self.assertTrue(upkeep.get("satisfied"), upkeep)
        self.assertEqual(len(upkeep.get("emitted_eligibilities") or []), 1, upkeep)
        self.assertIs(upkeep.get("auto_resumed"), False)
        self.assertEqual(upkeep.get("llm_calls"), 0)
        self.assertEqual(list(fx.conditions().values())[0]["status"], "SATISFIED")
        open_eligibilities = [row for row in fx.eligibilities().values()
                              if row["status"] == "OPEN"]
        self.assertEqual(len(open_eligibilities), 1, fx.eligibilities())
        self.assertEqual(open_eligibilities[0]["activity_id"], reading_activity)
        # 资格只是"可以问"：Activity 仍然 WAITING，还停在第 1 页。
        self.assertEqual(fx.activity(reading_activity)["status"], "WAITING")
        self.assertEqual(fx.reading()["page"], 1)

        # ---- r5：REST stop 走正式链；owner 自己的词是 completed（不是"恢复了"） ------
        rest_stop = fx.turn("rest_stop", label="rest-stop")
        self.assertEqual(rest_stop.get("outcome"), "ACTED", rest_stop.get("detail"))
        stop_activity = rest_stop.get("activity_ref")
        self.assertTrue(stop_activity)
        self.assertEqual(fx.activity(stop_activity)["status"], "COMPLETED")
        self.assertIsNone(fx.foreground())
        stop_entry = fx.rest_entry()
        self.assertIn((stop_entry.get("current") or {}).get("recovery_state"),
                      ("completed", "inactive", "interrupted"), stop_entry)
        self.assertIs(stop_entry.get("stop_available"), False, stop_entry)
        self.assertEqual(fx.recovery()["recovery"].get("state"),
                         (stop_entry.get("current") or {}).get("recovery_state"))

        # ---- r6：新鲜的 Decision 自己 RESUME，同一个 Activity 读到下一页 --------------
        resume = fx.turn("resume", label="resume")
        self.assertEqual(resume.get("decision"), "RESUME", resume.get("detail"))
        self.assertEqual(resume.get("outcome"), "ACTED", resume.get("detail"))
        self.assertEqual(resume.get("activity_ref"), reading_activity)
        record = fx.activity(reading_activity)
        self.assertEqual(record["status"], "ACTIVE")
        self.assertIsNone(record.get("waiting_on_ref"))
        self.assertEqual(fx.foreground(), reading_activity)
        page_two = fx.reading()
        self.assertEqual(page_two["page"], 2, page_two)
        self.assertEqual(page_two["next_page"], 3, page_two)
        # 同一份材料（版本没换）：她读的是原来那一份的下一页，不是另一个对象。
        self.assertEqual(page_two["material_version_id"], page_one["material_version_id"])
        self.assertEqual(page_two["material_title"], page_one["material_title"])

    def test_a_breakpoint_with_no_body_change_releases_nothing(self):
        fx = self.fx
        fx.turn("read", label="read")
        fx.turn("wait", label="wait")
        reading_activity = fx.rounds[0].get("activity_ref")
        # 没有 owner tick：身体版本没变，证据没动 -> 一个事件都不该有。
        upkeep = fx.maintain()
        self.assertEqual(upkeep.get("body_checked"), 1, upkeep)
        self.assertEqual(upkeep.get("body_changed"), 0, upkeep)
        self.assertEqual(upkeep.get("unchanged"), 1, upkeep)
        self.assertEqual(upkeep.get("evaluated"), 0, upkeep)
        self.assertEqual(upkeep.get("emitted_eligibilities"), [], upkeep)
        self.assertIs(upkeep.get("auto_resumed"), False)
        self.assertEqual(list(fx.conditions().values())[0]["status"], "PENDING")
        self.assertEqual([row for row in fx.eligibilities().values()
                          if row["status"] == "OPEN"], [])
        self.assertEqual(fx.activity(reading_activity)["status"], "WAITING")
        self.assertEqual(fx.reading()["page"], 1)

    def test_a_satisfied_breakpoint_still_needs_her_own_resume(self):
        fx = self.fx
        fx.turn("read", label="read")
        fx.turn("wait", label="wait")
        fx.turn("pose", label="pose")
        fx.turn("rest_start", label="rest-start")
        fx.owner_tick(4)
        upkeep = fx.maintain()
        self.assertTrue(upkeep.get("satisfied"), upkeep)
        reading_activity = fx.rounds[0].get("activity_ref")
        # 条件满足了、资格 OPEN 了，但她这一轮选择什么都不做：那就是什么都不做。
        quiet = fx.turn("noop", label="noop")
        self.assertEqual(quiet.get("decision"), "NO_ACTION", quiet.get("detail"))
        self.assertEqual(quiet.get("outcome"), "NO_ACTION", quiet.get("detail"))
        self.assertEqual(fx.activity(reading_activity)["status"], "WAITING")
        self.assertEqual(fx.reading()["page"], 1)
        open_eligibilities = [row for row in fx.eligibilities().values()
                              if row["status"] == "OPEN"]
        self.assertEqual(len(open_eligibilities), 1, fx.eligibilities())


class QuietBodyPacingTest(PacingChainBase):
    """身体没报 LIMITED 时，一个断点都不该被提供（"没信号不是健康"，也不是等待理由）。"""

    fatigue = 0.05

    def test_a_body_that_is_not_limited_offers_no_breakpoint(self):
        fx = self.fx
        fx.turn("read", label="read")
        self.assertEqual(fx.recovery()["recovery"].get("state"), "inactive")
        round_two = fx.turn("noop", label="second")
        self.assertEqual((round_two.get("body_breakpoint") or {}).get("available"), False,
                         round_two.get("body_breakpoint"))
        self.assertEqual((round_two.get("body_breakpoint") or {}).get("why_not"),
                         "BODY_NOT_LIMITED")
        # 模型侧也看到同样的具名原因（不是"什么都没有"）。
        seen = fx.script.seen[-1]["breakpoint"]
        self.assertFalse(seen.get("available"))
        self.assertEqual(seen.get("why_not"), "BODY_NOT_LIMITED")
        # WAIT 若这一轮合法，也只能是"她自己挑一个时间"（park），绝不是身体条件断点。
        basis = round_two.get("wait_basis") or {}
        self.assertNotEqual(basis.get("source"), "BODY_CAPACITY_RESTED", basis)
        self.assertIsNone((round_two.get("body_breakpoint") or {}).get("waiting_on_ref"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

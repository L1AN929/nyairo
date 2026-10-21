"""rc1-C：主体「用自己的材料」的真实 runtime + 真实 Supply owner 链。

范围（只证明机制，不冒充自主）：
1. 同一实例：START 读第 1 页 → 同一 Activity 的 CONTINUE 读第 2 页 → 重新装配（同一 state root，
   新 runtime / 新 cycle）后继续第 3 页；
2. 阅读 CONTINUE 绝不顺手给作品提交一版（作品 head 跨阅读轮保持不变）；
3. 回前页的显式停点就是停点（不是最高页 +1）；
4. 换版从新版入口重新认领（旧版最高页不驱动新版，只报 needs_reread）；
5. 读尽：不再产生可执行的个人阅读机会，前台读尽允许 NO_ACTION，不报错、不转作品；
6. 缺 grant / 暂停 / UNKNOWN 原键：都不写、不绕、不换键；
7. 处境事实键经既有 bounded 通道进入 production prompt，且不泄正文。

明确：本文件的决策来自**脚本化替身**（``ScriptedCognition``），它只说「候选在 AG-1 眼里真的合法、
路由真的到 owner、停点真的落盘」。它不是模型，不能读作「她自己选择了读书」。
``understanding`` 恒 UNKNOWN。0 模型调用、0 网络、0 支付、0 Memory 写入。

需要 POSIX（fcntl + AF_UNIX）；原生 Windows 自动跳过。
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from test_one_day_life import (  # noqa: E402
    OneDayTestCase,
    StubCognition,
    StubObservation,
    StubWriter,
    posix_only,
)
import agency_decision_ag1 as ag1  # noqa: E402
import agency_cognition_adapter as cognition_adapter  # noqa: E402
import autonomous_life_assembly as assembly  # noqa: E402
import autonomous_life_cycle as alc  # noqa: E402
import life_artifact_port as lap  # noqa: E402
import one_day_life as odl  # noqa: E402
import private_daily_items as pdi  # noqa: E402
from managed_artifact_action_adapter import OP_COMMIT_VERSION  # noqa: E402
import subject_private_materials as spm  # noqa: E402

READING = ("第一页：房间里的桌子与窗。\n%%PAGE%%\n"
           "第二页：窗外是一条街，街角有树。\n%%PAGE%%\n"
           "第三页：天色渐暗，风从海上过来。\n")
READING_V2 = ("新版第一页：换了一份材料。\n%%PAGE%%\n"
              "新版第二页：这一页是新的。\n")
#: 正文里的一句特征串，用来证明它没有出现在 prompt / 报告里。
MATERIAL_SENTINEL = "窗外是一条街"

#: Sentinel for the test helper's "use the instance's own grant" argument.
_USE_INSTANCE_GRANT = object()


class ScriptedCognition(StubCognition):
    """脚本化决策替身：只从 AG-1 真正给出的候选里挑，按事先写死的顺序。

    每一步的取值：
    * ``READ``   —— 选「这份阅读材料」的候选：CONTINUE 合法就 CONTINUE，否则 START；
    * ``WORK``   —— 选「这份作品」的候选（反例：证明前台是作品时不会被当成阅读）；
    * ``NO_ACTION`` —— 明确不动。

    它记录这次请求本身（``requests``），所以「处境事实键真的进了 production prompt」可以在
    不花一分钱模型费的前提下被证明。**它不是模型**，不产生任何「自主选择」的证据。
    """

    def __init__(self, script, *, reading_artifact_id: str, work_artifact_id: str) -> None:
        super().__init__("artifact")
        self.script = list(script)
        self.reading_artifact_id = str(reading_artifact_id)
        self.work_artifact_id = str(work_artifact_id)
        self.requests: list[object] = []
        self.offered: list[list[dict]] = []

    def _step(self) -> str:
        index = max(0, self.call_count - 1)
        if not self.script:
            return "NO_ACTION"
        return self.script[min(index, len(self.script) - 1)]

    def _pick(self, request, wanted: str):
        """The candidate about ``wanted``; a legal CONTINUE wins over a legal START.

        When the material is already in the foreground, AG-1 legitimately offers *both* the
        running Activity (CONTINUE) and the open personal opportunity (START), and AG-2 refuses
        a second foreground Activity.  Preferring the CONTINUE keeps the scripted driver on the
        path this file is about instead of making the outcome depend on candidate order.
        """
        matches: list[tuple[dict, list[str]]] = []
        for candidate in (getattr(request, "candidates", []) or []):
            candidate = dict(candidate)
            refs = [str(ref) for ref in (candidate.get("target_refs") or [])]
            refs.append(str(candidate.get("source_ref") or ""))
            if not any(wanted in ref for ref in refs):
                continue
            legal = [str(item) for item in (candidate.get("legal_decisions") or [])]
            matches.append((candidate, legal))
        for candidate, legal in matches:
            if "CONTINUE" in legal:
                return candidate, legal
        for candidate, legal in matches:
            if "START" in legal:
                return candidate, legal
        return (matches[0] if matches else (None, []))

    def invoke(self, request, *, schema_retry_index: int = 0):
        self.call_count += 1
        self.requests.append(request)
        self.offered.append([{"kind": dict(c).get("candidate_kind"),
                              "candidate_id": dict(c).get("candidate_id"),
                              "legal": [str(x) for x in (dict(c).get("legal_decisions") or [])]}
                             for c in (getattr(request, "candidates", []) or [])])
        allowed = [str(code) for code in (getattr(request, "allowed_reason_codes", []) or [])]
        step = self._step()
        chosen, legal = (None, [])
        if step == "READ":
            chosen, legal = self._pick(request, self.reading_artifact_id)
        elif step == "WORK":
            chosen, legal = self._pick(request, self.work_artifact_id)
        if chosen is None or not ({"START", "CONTINUE"} & set(legal)):
            raw = {"decision": "NO_ACTION", "selected_candidate_id": None,
                   "reason_codes": allowed[:1], "short_rationale": "nothing legal to do now",
                   "deferred_candidate_ids": []}
        else:
            raw = {"decision": "CONTINUE" if "CONTINUE" in legal else "START",
                   "selected_candidate_id": chosen.get("candidate_id"),
                   "reason_codes": allowed[:1],
                   "short_rationale": "carry on with this one",
                   "deferred_candidate_ids": []}
        return ag1.CognitiveDecisionResponse(
            model_run_ref="model_run:stub:scripted:%d" % self.call_count,
            model_id=self.model_id, model_version=self.model_version,
            raw_output=raw, latency_ms=1)


@posix_only
class SubjectReadingCycleTest(OneDayTestCase):
    """真实 Supply/World owner + 真实 runtime + 脚本化决策替身。"""

    def _runtime(self, cognition, env, data, *, grant_id=_USE_INSTANCE_GRANT):
        """The same production runtime wiring ``CycleTest`` uses (real owner + AR-0 adapter)."""
        import life_integration_ag2 as ag2
        import life_runtime_production as lrp
        import managed_artifact_action_adapter as maaa
        from world_journey_action_adapter import (JourneyWorldResolver,
                                                 RealJourneyWorldActionAdapter)

        state_root = odl.life_state_root(self.instance)
        port = lap.LifeArtifactPort(str(self.services.supply_path), data["subject_id"])
        adapter = maaa.SupplyManagedArtifactActionAdapter(
            port,
            grant_id=(data["artifact_grant_id"] if grant_id is _USE_INSTANCE_GRANT
                      else grant_id),
            workspace_id=data["workspace_id"])
        switches = ag2.LifeRuntimeKillSwitches(
            life_runtime_enabled=True, agency_enabled=True, action_execution_enabled=True,
            proactive_enabled=False)
        runtime = lrp.build_production_life_runtime(
            state_root, kill_switches=switches, cognition_adapter=cognition,
            install_root=self.instance / "life", artifact_action_adapter=adapter,
            world_action_adapter=RealJourneyWorldActionAdapter(
                JourneyWorldResolver(environment=env)))
        # tracked so tearDown closes it while the instance tree still exists
        self._runtimes.append(runtime)
        self.addCleanup(runtime.close)
        return runtime, port

    def _reading_source_id(self, data: dict) -> str:
        """The private-items ``source_id`` for the material (one ``artifact:`` layer only)."""
        return "artifact:%s" % str(data["reading_artifact_id"]).split("artifact:", 1)[-1]

    def _prepare(self, *, content: str = READING) -> dict:
        result = self.prepare_instance(reading_title="C 阅读链材料", reading_content=content,
                                       create_private_items=True)
        self.assertTrue(result["ok"], result)
        return odl.read_instance(self.instance)

    def _fixture(self, script, *, data=None, grant_id=_USE_INSTANCE_GRANT):
        """(re)assemble a full runtime+cycle on this instance's own state root."""
        data = data or odl.read_instance(self.instance)
        env = self.instance_env()
        cognition = ScriptedCognition(script,
                                      reading_artifact_id=data["reading_artifact_id"],
                                      work_artifact_id=data["work_artifact_id"])
        runtime, port = self._runtime(cognition, env, data, grant_id=grant_id)
        assembled = assembly.assemble_autonomous_life(
            runtime, environment=env, state_root=odl.life_state_root(self.instance),
            world_provider=StubObservation(env), writer=StubWriter(), enabled=True)
        self.assertEqual(assembled["state"], assembly.READY, assembled)
        return SimpleNamespace(runtime=runtime, port=port, cycle=assembled["cycle"],
                               cognition=cognition, data=data, env=env, assembled=assembled)

    # -- owner read helpers ------------------------------------------------------------

    def _head(self, artifact_id: str) -> dict:
        data = odl.read_instance(self.instance)
        return odl.supply_call(self.services.supply_path,
                               {"op": "read_head", "subject": data["subject_id"],
                                "artifact_id": artifact_id})["head"]

    def _document(self, artifact_id: str) -> dict:
        data = odl.read_instance(self.instance)
        content = odl.supply_call(self.services.supply_path,
                                  {"op": "read_artifact", "subject": data["subject_id"],
                                   "artifact_id": artifact_id})["content"]
        return pdi.decode_document(content)

    def _positions(self, document: dict, source_id: str) -> list[dict]:
        """The document's reading positions **in the order they were written**.

        The stored JSON is serialized with sorted keys, so ``items`` alone loses the write
        order; the document's own ``order`` list is what preserves it.
        """
        items = document["items"]
        order = [item_id for item_id in (document.get("order") or []) if item_id in items]
        order += [item_id for item_id in items if item_id not in order]
        return [items[item_id] for item_id in order
                if items[item_id].get("item_type") == pdi.ITEM_READING_POSITION
                and str(items[item_id].get("source_id")) == source_id]

    def _offered_reading(self, cycle) -> list[list[str]]:
        """What the admitted PERSONAL_OPPORTUNITY source really offers right now."""
        out: list[list[str]] = []
        for adapter in cycle.runtime.candidate_registry.list_adapters():
            if getattr(adapter, "source_kind", None) != "PERSONAL_OPPORTUNITY":
                continue
            items, _snap, _counts = adapter.discover(observed_at="2026-10-16T06:00:00+00:00")
            for item in items:
                out.append([str(ref) for ref in (item.get("target_refs") or [])])
        return out

    # -- 1/2/3. START → 同一 Activity CONTINUE → 重新装配后继续 -------------------------

    def test_start_then_continue_reads_one_then_two_then_a_fresh_assembly_reads_three(self):
        data = self._prepare()
        first = self._fixture(["READ", "READ"], data=data)
        work_before = self._head(data["work_artifact_id"])["version_id"]
        doc_before = self._head(data["private_items_artifact_id"])["version_id"]

        round1 = first.cycle.evaluate_once(trigger_ref="evt:c:read1", act=True)
        self.assertEqual(round1["outcome"], "ACTED", round1)
        self.assertEqual(round1["step_path"], "READING_AR0", round1)
        self.assertEqual(round1["reading"]["page"], 1, round1.get("reading"))
        self.assertEqual(round1["reading"]["understanding"], "UNKNOWN")
        self.assertEqual(round1["reading"]["planned_from"], "NO_STOP_ON_CURRENT_REVISION")
        activity_ref = round1["activity_ref"]
        self.assertTrue(activity_ref, round1)
        work_after_round1 = self._head(data["work_artifact_id"])["version_id"]
        self.assertEqual(work_after_round1, work_before,
                         "a page read must never commit the work")
        document = self._document(data["private_items_artifact_id"])
        self.assertEqual([p["position"] for p in self._positions(
            document, self._reading_source_id(data))], [1])

        # round 2: the *same* foreground Activity, continued -- page 2, one more document version
        doc_head_1 = self._head(data["private_items_artifact_id"])["version_id"]
        round2 = first.cycle.evaluate_once(trigger_ref="evt:c:read2", act=True)
        self.assertEqual(round2["outcome"], "ACTED", round2)
        self.assertEqual(round2["reading"]["page"], 2, round2.get("reading"))
        self.assertEqual(round2["reading"]["previous_page"], 1, round2.get("reading"))
        self.assertEqual(round2["reading"]["planned_from"], "LATEST_STOP_ON_CURRENT_REVISION")
        self.assertEqual(round2["activity_ref"], activity_ref,
                         "CONTINUE must continue the same Activity, not adopt a second one")
        self.assertNotEqual(self._head(data["private_items_artifact_id"])["version_id"], doc_head_1)
        self.assertEqual(self._head(data["work_artifact_id"])["version_id"], work_before,
                         "the work head must be unchanged after a reading CONTINUE")
        # AG-1 really was offered the CONTINUE-legal activity candidate in that round
        kinds = {row["kind"] for row in first.cognition.offered[-1]}
        self.assertIn("CONTINUE_CURRENT", kinds, first.cognition.offered[-1])
        document = self._document(data["private_items_artifact_id"])
        self.assertEqual(sorted(p["position"] for p in self._positions(
            document, self._reading_source_id(data))), [1, 2])
        self.assertNotEqual(self._head(data["private_items_artifact_id"])["version_id"],
                            doc_before)

        # a fresh assembly on the same state root (new runtime, new cycle, new budget object)
        first.runtime.close()
        second = self._fixture(["READ"], data=data)
        round3 = second.cycle.evaluate_once(trigger_ref="evt:c:read3", act=True)
        self.assertEqual(round3["outcome"], "ACTED", round3)
        self.assertEqual(round3["reading"]["page"], 3, round3.get("reading"))
        self.assertEqual(round3["reading"]["previous_page"], 2, round3.get("reading"))
        self.assertEqual(round3["reading"]["pages"], 3)
        self.assertEqual(round3["activity_ref"], activity_ref,
                         "the foreground reading Activity survives a re-assembly")
        self.assertEqual(self._head(data["work_artifact_id"])["version_id"], work_before)
        document = self._document(data["private_items_artifact_id"])
        self.assertEqual(sorted(p["position"] for p in self._positions(
            document, self._reading_source_id(data))), [1, 2, 3])

    # -- 4. 读尽：不再可执行，也不允许转成作品 --------------------------------------

    def test_finished_material_is_not_an_executable_opportunity_and_never_becomes_work(self):
        data = self._prepare()
        fixture = self._fixture(["READ", "READ", "READ", "READ"], data=data)
        for index in (1, 2, 3):
            out = fixture.cycle.evaluate_once(trigger_ref="evt:c:finish%d" % index, act=True)
            self.assertEqual(out["outcome"], "ACTED", out)
            self.assertEqual(out["reading"]["page"], index, out.get("reading"))
        work_head = self._head(data["work_artifact_id"])["version_id"]
        doc_head = self._head(data["private_items_artifact_id"])["version_id"]

        situation = fixture.cycle.private_material_situation(refresh=True)
        self.assertEqual(situation["state"], spm.STATE_EXHAUSTED, situation)
        self.assertFalse(situation["available"])
        self.assertIsNone(situation["position"]["next_page"], situation["position"])
        self.assertFalse(fixture.cycle.reading_available())
        # no *executable* personal reading opportunity is manufactured every round
        offered = self._offered_reading(fixture.cycle)
        self.assertFalse(any(data["reading_artifact_id"] in ref
                             for refs in offered for ref in refs), offered)
        # ... and a CONTINUE on the still-running reading Activity is a legal stop, not an
        # error and not a work commit
        out = fixture.cycle.evaluate_once(trigger_ref="evt:c:finish4", act=True)
        self.assertEqual(out["outcome"], "NO_ACTION", out)
        self.assertEqual(out["reading_continued"], False, out)
        self.assertEqual(out["reading_state"], spm.STATE_EXHAUSTED, out)
        self.assertEqual(self._head(data["work_artifact_id"])["version_id"], work_head,
                         "an exhausted reading round must not commit the work")
        self.assertEqual(self._head(data["private_items_artifact_id"])["version_id"], doc_head,
                         "an exhausted reading round writes nothing at all")

    # -- 5. 回前页 / 换版 -----------------------------------------------------------

    def test_an_explicit_earlier_stop_is_the_stop_not_the_highest_page(self):
        data = self._prepare()
        self.assertTrue(odl.read_page(self.instance, page=3)["ok"])
        self.assertTrue(odl.read_page(self.instance, page=1)["ok"])
        fixture = self._fixture(["READ"], data=data)
        situation = fixture.cycle.private_material_situation(refresh=True)
        self.assertEqual(situation["position"]["page"], 1, situation["position"])
        self.assertEqual(situation["position"]["next_page"], 2, situation["position"])
        self.assertEqual(situation["position"]["prior_revision_pages"], [],
                         "an earlier page of the same revision is not a 'prior revision'")
        out = fixture.cycle.evaluate_once(trigger_ref="evt:c:back", act=True)
        self.assertEqual(out["outcome"], "ACTED", out)
        self.assertEqual(out["reading"]["page"], 2, out.get("reading"))
        # (the explicit stop page 1 decides; max(page)=3 must not)

    def test_a_new_material_revision_is_claimed_from_its_own_entry(self):
        data = self._prepare()
        for page in (1, 2, 3):
            self.assertTrue(odl.read_page(self.instance, page=page)["ok"])
        port = lap.LifeArtifactPort(str(self.services.supply_path), data["subject_id"])
        head = port.read_head(data["reading_artifact_id"]) or {}
        outcome = port.commit_version(
            artifact_id=data["reading_artifact_id"], expected_head=head.get("version_id"),
            content=READING_V2, grant_id=data["artifact_grant_id"],
            operation_key=lap.operation_key_for("test:material:replace", str(head.get("version_id"))))
        self.assertTrue(outcome.applied, outcome.to_dict())
        fixture = self._fixture(["READ"], data=data)
        situation = fixture.cycle.private_material_situation(refresh=True)
        self.assertEqual(situation["state"], spm.STATE_AVAILABLE, situation)
        self.assertIsNone(situation["position"]["page"], situation["position"])
        self.assertEqual(situation["position"]["next_page"], 1, situation["position"])
        self.assertTrue(situation["position"]["needs_reread"], situation["position"])
        self.assertEqual(situation["position"]["needs_reread_reason"],
                         "SOURCE_REVISION_ADVANCED", situation["position"])
        self.assertEqual(situation["position"]["prior_revision_pages"], [1, 2, 3],
                         "the old revision's pages are a *situation*, not a page to carry over")
        self.assertEqual(situation["material"]["pages"], 2)
        out = fixture.cycle.evaluate_once(trigger_ref="evt:c:v2", act=True)
        self.assertEqual(out["outcome"], "ACTED", out)
        self.assertEqual(out["reading"]["page"], 1, out.get("reading"))
        document = self._document(data["private_items_artifact_id"])
        newest = self._positions(document, self._reading_source_id(data))[-1]
        self.assertEqual(newest["source_revision"],
                         self._head(data["reading_artifact_id"])["version_id"])

    # -- 6. 缺 grant / 暂停 / UNKNOWN 原键 -----------------------------------------

    def test_a_foreign_grant_is_refused_by_the_owner_and_writes_nothing(self):
        data = self._prepare()
        # An adapter holding a grant the governance owner never issued: the *owner* refuses
        # the write.  (A ``None`` grant is not a usable negative control here -- the AR-0
        # adapter itself refuses to be constructed without a grant id, so the round could not
        # even reach the fence; the honest negative is a real call with a foreign grant.)
        foreign = "grant:not-issued-by-this-owner"
        fixture = self._fixture(["READ"], data=data, grant_id=foreign)
        doc_head = self._head(data["private_items_artifact_id"])["version_id"]
        work_head = self._head(data["work_artifact_id"])["version_id"]
        out = fixture.cycle.evaluate_once(trigger_ref="evt:c:nogrant", act=True)
        self.assertNotEqual(out["outcome"], "ACTED", out)
        self.assertIn(out["outcome"], {"STEP_REFUSED", "STEP_UNKNOWN", "ADOPTION_REJECTED"},
                      out)
        self.assertEqual(self._head(data["private_items_artifact_id"])["version_id"], doc_head,
                         "a refused write must not mint a private-items version")
        self.assertEqual(self._head(data["work_artifact_id"])["version_id"], work_head)

    def test_paused_life_considers_nothing(self):
        data = self._prepare()
        fixture = self._fixture(["READ"], data=data)
        odl.set_pause(self.instance, True, reason="test")
        try:
            doc_head = self._head(data["private_items_artifact_id"])["version_id"]
            out = fixture.cycle.evaluate_once(trigger_ref="evt:c:paused", act=True)
            self.assertEqual(out["outcome"], "PAUSED_BY_OPERATOR", out)
            self.assertEqual(fixture.cognition.call_count, 0,
                             "a paused round must not even ask for a decision")
            self.assertEqual(self._head(data["private_items_artifact_id"])["version_id"], doc_head)
        finally:
            odl.set_pause(self.instance, False, reason="test")

    def test_the_reading_step_is_planned_under_one_deterministic_operation_key(self):
        data = self._prepare()
        fixture = self._fixture(["READ"], data=data)
        first = fixture.cycle._reading_step_spec(observed_at="2026-10-16T06:00:00+00:00")
        second = fixture.cycle._reading_step_spec(observed_at="2026-10-16T06:00:00+00:00")
        self.assertEqual(first["idempotency_key"], second["idempotency_key"],
                         "one intended page read has exactly one operation key")
        self.assertTrue(str(first["idempotency_key"]).startswith("life:"))
        self.assertEqual(first["payload_spec"]["op"], OP_COMMIT_VERSION)
        self.assertEqual(first["payload_spec"]["expected_head"],
                         self._head(data["private_items_artifact_id"])["version_id"],
                         "the step is pinned to the owner's current private-items head (CAS)")
        self.assertTrue(first["payload_spec"]["grant_id"])
        # An UNKNOWN result is answered with "reconcile the original operation", never a retry.
        unknown = {"step_execution_result": {"submit_result": {"action": {
            "action_id": "action:test:unknown", "status": "UNKNOWN",
            "frozen_payload": dict(first["payload_spec"])}}}}
        base = {"correlation_id": "corr:test:unknown"}
        out = fixture.cycle._classify_ar0_step(unknown, base, "2026-10-16T06:00:00+00:00")
        self.assertEqual(out["outcome"], "STEP_UNKNOWN", out)
        self.assertIn("reconcile the original operation", out["detail"])
        self.assertEqual(out["owner_version_id"], None, out)
        self.assertEqual(self._head(data["private_items_artifact_id"])["version_id"],
                         dict(first["payload_spec"])["expected_head"])

    # -- 7. 处境事实键进入 production prompt，且不泄正文 -------------------------

    def test_the_situation_reaches_the_production_prompt_without_material_text(self):
        data = self._prepare()
        fixture = self._fixture(["READ", "READ"], data=data)
        first = fixture.cycle.evaluate_once(trigger_ref="evt:c:prompt1", act=True)
        self.assertEqual(first["outcome"], "ACTED", first)
        request = fixture.cognition.requests[-1]
        # (a) at decision time the bounded facts say exactly what the round may do: page 1 of 3
        #     is next, nothing is finished -- and the page number is *in the key*
        self.assertIn("reading_state_AVAILABLE", first["private_material_facts"], first)
        self.assertIn("reading_next_page_1", first["private_material_facts"], first)
        self.assertIn("reading_pages_3", first["private_material_facts"], first)
        # (b) the *production* prompt projection keeps them (real adapter code, no provider call)
        adapter = cognition_adapter.ProductionAgencyCognitionAdapter(
            client=SimpleNamespace(provider="stub", model="no-model-called"))
        text, payload = adapter.build_prompt(request)
        keys = payload["observed_fact_keys"]
        self.assertIn("reading_state_AVAILABLE", keys, keys)
        self.assertIn("reading_next_page_1", keys, keys)
        self.assertLessEqual(len(keys), spm.MAX_FACT_KEYS)
        for key in keys:
            self.assertTrue(key.isascii(), key)
            self.assertLessEqual(len(key), spm.MAX_FACT_KEY_CHARS, key)
        # (c) no page text, no note body, no credential field travels with it
        self.assertNotIn(MATERIAL_SENTINEL, text)
        self.assertNotIn("第一页", text)
        self.assertNotIn("source_acquisition", text)
        self.assertEqual(first["reading"]["understanding"], "UNKNOWN")

        # (d) after the progress really moved, the *next* round's facts carry the new stop:
        #     the situation is not a frozen snapshot from the first round
        second = fixture.cycle.evaluate_once(trigger_ref="evt:c:prompt2", act=True)
        self.assertEqual(second["outcome"], "ACTED", second)
        self.assertIn("reading_page_1", second["private_material_facts"], second)
        self.assertIn("reading_next_page_2", second["private_material_facts"], second)
        _, payload2 = adapter.build_prompt(fixture.cognition.requests[-1])
        self.assertIn("reading_next_page_2", payload2["observed_fact_keys"])

    # -- 8. 反例：前台是作品时不会被当成阅读 -------------------------------------

    def test_a_work_foreground_is_never_treated_as_a_reading_continuation(self):
        data = self._prepare()
        fixture = self._fixture(["WORK"], data=data)
        work_before = self._head(data["work_artifact_id"])["version_id"]
        out = fixture.cycle.evaluate_once(trigger_ref="evt:c:work", act=True)
        self.assertEqual(out["outcome"], "ACTED", out)
        self.assertNotEqual(self._head(data["work_artifact_id"])["version_id"], work_before)
        self.assertIsNone(fixture.cycle._foreground_reading_activity(),
                          "a work Activity is not a reading continuation")
        document = self._document(data["private_items_artifact_id"])
        self.assertEqual(document["items"], {},
                         "the work branch must not touch the private-items document")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""v0.4 旧作重访 · 完整 Life 写作链与两 PID 同根重启。

这条用例把既有几段证据接成一条链，且只加测试、不改产品代码：

* **真实 Supply owner**：复用 ``tests/test_v04_work_revisit_history.py`` 的 ``SupplyOwner``
  （真实 ``LifeSupplyService`` + AF_UNIX + canonical db），作品标题无关，版本全由 owner 落盘。
* **真实 Life runtime / assembly / AR-0**：复用 ``DScenarioBase`` 的真 owner、真 World home、
  真 Memory 装配；两轮写作各由一个独立子进程真正 ``evaluate_once``。
* **真实写作请求**：writer 是 ``ModelContinuationWriter``，transport 是只记录请求的
  ``RecordingClient``。``earlier_versions`` 由 cycle 自己的 ``_next_content`` 从 owner 读回并
  自动组装，测试没有手工往 context 里塞旧版正文。
* **两 PID 同根重启**：worker A 真跑一轮（owner receipt + 新版本）后置 operator pause 并
  ``close()`` 释放 lease；worker B 在同一 data root 新建 runtime/assembly/预算，先证明暂停轮
  0 请求 0 写入，再 resume 合法 CONTINUE 同一 Activity，owner 产生新版本。
* **具名拒绝与不重复**：读到别的 artifact 的版本由 owner 具名拒绝；派生作品在重启后仍带来源，
  同一 operation key 重放仍是同一件，不重复创建。

零产品模型请求：decision 是确定性替身，content client 只记录请求，产品 completion 为 0。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import textwrap
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "native" / "memory_runtime_v1"),
              str(ROOT / "components" / "native" / "integration"), str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_artifact_port as lap  # noqa: E402
import private_project_service as pps  # noqa: E402
import one_day_life as odl  # noqa: E402
from components.phone import server as phone_server  # noqa: E402
from components.phone import service as phone_service  # noqa: E402
from test_life_v1_integration import DScenarioBase  # noqa: E402
from test_one_day_life import posix_only  # noqa: E402

API = "/api/phone"
CSRF = "v04-runtime-csrf"
SENTINEL_OLD = "SENTINEL-OLD-1"

V_OLD = "第一段：%s 她在夜航手记里留下这一句。" % SENTINEL_OLD
V_PREV = V_OLD + "\n第二段：她在途中又补了一段，旧句子还在。"
V_HEAD = "第三段：靠岸之后，她把那句旧话删掉了，只留下新的一版。"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


# ---------------------------------------------------------------------------------------------
# 单个 worker：真实装配 + 真跑 evaluate_once；不是只读 probe，也不在父进程持有 runtime
# ---------------------------------------------------------------------------------------------


WORKER = textwrap.dedent(
    '''
    import json, os, sys
    sys.path[:0] = [__TESTS__, __SCRIPTS__]

    import one_day_life as odl
    import autonomous_life_assembly as assembly
    import life_artifact_port as lap
    import life_integration_ag2 as ag2
    import life_runtime_production as lrp
    import managed_artifact_action_adapter as maaa
    import model_continuation_writer as mcw
    from life_experience_port import LifeExperiencePort
    from life_experience_memory_consumer import MemoryAdmissionExperienceConsumer
    from world_journey_action_adapter import JourneyWorldResolver, RealJourneyWorldActionAdapter
    from test_one_day_life import StubCognition, StubObservation

    INSTANCE = __INSTANCE__
    PHASE = __PHASE__
    WORK = __WORK__
    SENTINEL = __SENTINEL__
    REPLY = '{"kind":"append_paragraph","text":"她接着写下新的一段。"}'

    env = odl.instance_environment(INSTANCE)
    data = odl.read_instance(INSTANCE)
    # The cycle reads the workspace from its environment; this is the instance's own real
    # workspace id (the same one the owner provisioned), not a made-up binding.
    env["CHIYO_LIFE_SUPPLY_WORKSPACE_ID"] = data["workspace_id"]
    state_root = odl.life_state_root(INSTANCE)
    supply = assembly.resolve_supply_configuration(env)
    report = {"phase": PHASE, "pid": os.getpid(), "run_id": env.get(odl.RUN_ID_ENV)}


    class RecordingClient:
        """Transport stand-in: keeps every request the real writer actually sent."""

        def __init__(inner, reply):
            inner.reply = reply
            inner.requests = []

        def complete(inner, *, system_prompt, user_prompt, timeout_s=0.0, max_output_tokens=0):
            inner.requests.append({"system_prompt": system_prompt, "user_prompt": user_prompt})

            class _Result:
                text = inner.reply

            return _Result()


    class WorkLoopback(StubCognition):
        """Deterministic branch over the legal set: CONTINUE the running work, else START it."""

        def __init__(inner):
            super().__init__("artifact")
            inner.seen = []
            inner.chosen = []

        def invoke(inner, request, *, schema_retry_index=0):
            allowed = [str(c) for c in (getattr(request, "allowed_reason_codes", []) or [])]
            candidates = [dict(c) for c in (getattr(request, "candidates", []) or [])]
            inner.seen.append([{"kind": c.get("candidate_kind"),
                                "source": c.get("source_kind"),
                                "legal": [str(x) for x in (c.get("legal_decisions") or [])],
                                "refs": [str(x) for x in (c.get("target_refs") or [])],
                                "source_ref": c.get("source_ref")}
                               for c in candidates])
            def about_work(c):
                refs = [str(x) for x in (c.get("target_refs") or [])]
                source = str(c.get("source_ref") or "")
                # A closeout offer names the same artifact but asks to close it, not to write
                # it; choosing it would abandon the running work (ABANDON_NONTERMINAL_CHOSEN).
                if any(ref.startswith("closeout:") for ref in refs):
                    return False
                if source.startswith("closeout:"):
                    return False
                return any(WORK in ref for ref in (refs + [source]))

            about_work = [c for c in candidates if about_work(c)]
            continue_legal = [c for c in about_work
                              if "CONTINUE" in [str(x) for x in (c.get("legal_decisions") or [])]]
            start_legal = [c for c in about_work
                           if "START" in [str(x) for x in (c.get("legal_decisions") or [])]]
            chosen = (continue_legal or start_legal or [None])[0]
            if chosen is None:
                raise AssertionError(
                    "no legal candidate about the work artifact in this window: %r"
                    % (inner.seen,))
            per = [str(x) for x in (chosen.get("legal_decisions") or [])]
            decision = "CONTINUE" if "CONTINUE" in per else "START"
            inner.chosen.append({"decision": decision,
                                 "candidate_id": chosen.get("candidate_id"),
                                 "kind": chosen.get("candidate_kind"),
                                 "legal": per})
            inner.call_count += 1
            import agency_decision_ag1 as ag1
            return ag1.CognitiveDecisionResponse(
                model_run_ref="model_run:stub:work-revisit", model_id=inner.model_id,
                model_version=inner.model_version,
                raw_output={"decision": decision,
                            "selected_candidate_id": chosen["candidate_id"],
                            "reason_codes": allowed[:1],
                            "short_rationale": "carry on with the running work",
                            "deferred_candidate_ids": []},
                latency_ms=1)


    def request_summaries(client):
        rows = []
        for item in client.requests:
            try:
                body = json.loads(item["user_prompt"])
            except Exception:
                body = {}
            earlier = body.get("earlier_versions") or []
            tail = str(body.get("tail_of_current_text") or "")
            rows.append({
                "earlier_versions": [{"version_id": e.get("version_id"),
                                      "version_number": e.get("version_number"),
                                      "has_sentinel": SENTINEL in str(e.get("excerpt") or "")}
                                     for e in earlier if isinstance(e, dict)],
                "tail_has_sentinel": SENTINEL in tail,
                "prompt_has_sentinel": SENTINEL in item["user_prompt"],
            })
        return rows


    cognition = WorkLoopback()
    client = RecordingClient(REPLY)
    memory_db = (env.get("NYA02_MEMORY_DB") or str(INSTANCE / "memory" / "evidence.sqlite"))
    controls = env.get("NYA02_MEMORY_CONTROLS_DB") or memory_db
    subject = env.get("NYA02_MEMORY_SUBJECT") or "chiyo"
    consumer = MemoryAdmissionExperienceConsumer(
        port=LifeExperiencePort(memory_db, controls_db=controls, subject=subject), enabled=True)

    port = lap.LifeArtifactPort(supply["supply_socket"], supply["subject_id"])
    adapter = maaa.SupplyManagedArtifactActionAdapter(
        port, grant_id=supply["artifact_grant_id"], workspace_id=data["workspace_id"])
    switches = ag2.LifeRuntimeKillSwitches(
        life_runtime_enabled=True, agency_enabled=True, action_execution_enabled=True,
        proactive_enabled=False)
    runtime = lrp.build_production_life_runtime(
        state_root, kill_switches=switches, cognition_adapter=cognition,
        install_root=str(INSTANCE), artifact_action_adapter=adapter,
        world_action_adapter=RealJourneyWorldActionAdapter(
            JourneyWorldResolver(environment=env)),
        experience_consumer=consumer)
    budget = assembly.request_budget(env, state_root)
    assembled = assembly.assemble_autonomous_life(
        runtime, environment=env, state_root=state_root, world_provider=StubObservation(env),
        writer=mcw.ModelContinuationWriter(client), enabled=True, requests=budget)


    def head():
        return odl.supply_call(supply["supply_socket"], {
            "op": "read_head", "subject": data["subject_id"],
            "artifact_id": WORK})["head"]["version_id"]


    report["assembled_state"] = assembled.get("state")
    cycle = assembled["cycle"]
    report["budget_at_start"] = budget.view()
    report["pause_at_start"] = odl.status(INSTANCE)["pause"]
    report["state_at_start"] = odl.status(INSTANCE)["state"]["state_class"]
    report["head_at_start"] = head()

    if PHASE == "first":
        out = cycle.evaluate_once(trigger_ref="work-revisit:worker1", act=True)
        report["round"] = {"outcome": out.get("outcome"),
                           "decision": out.get("decision"),
                           "adoption_status": out.get("adoption_status"),
                           "llm_calls": out.get("llm_calls"),
                           "owner_receipt": bool(out.get("owner_receipt")),
                           "owner_version_created": out.get("owner_version_created"),
                           "reason_codes": list(out.get("reason_codes") or []),
                           "detail": out.get("detail"),
                           "activity_ref": out.get("activity_ref")}
        report["cognition_calls"] = cognition.call_count
        report["head_after_round"] = head()
        report["requests"] = request_summaries(client)
        budget.reserve("decision")
        report["budget_after_reserve"] = budget.view()
        odl.set_pause(INSTANCE, True, reason="work-revisit:restart")
        report["pause_set"] = odl.status(INSTANCE)["pause"]
    else:
        paused = cycle.evaluate_once(trigger_ref="work-revisit:worker2-paused", act=True)
        report["paused_round"] = {"outcome": paused.get("outcome"),
                                  "llm_calls": paused.get("llm_calls")}
        report["cognition_calls_while_paused"] = cognition.call_count
        report["requests_after_paused"] = len(client.requests)
        report["head_while_paused"] = head()
        odl.set_pause(INSTANCE, False, reason="work-revisit:restart")
        resumed = cycle.evaluate_once(trigger_ref="work-revisit:worker2-resume", act=True)
        report["round"] = {"outcome": resumed.get("outcome"),
                           "decision": resumed.get("decision"),
                           "adoption_status": resumed.get("adoption_status"),
                           "llm_calls": resumed.get("llm_calls"),
                           "owner_receipt": bool(resumed.get("owner_receipt")),
                           "owner_version_created": resumed.get("owner_version_created"),
                           "reason_codes": list(resumed.get("reason_codes") or []),
                           "detail": resumed.get("detail"),
                           "activity_ref": resumed.get("activity_ref")}
        report["cognition_calls"] = cognition.call_count
        report["head_after_round"] = head()
        report["requests_after_resume"] = len(client.requests)
        report["requests"] = request_summaries(client)
        activity_ref = resumed.get("activity_ref")
        report["activity_after"] = (dict(runtime.activity_read.get_activity(activity_ref))
                                    if activity_ref else None)
        budget.reserve("decision")
        report["budget_after_reserve"] = budget.view()

    report["cognition_seen"] = cognition.seen
    report["cognition_chosen"] = cognition.chosen
    runtime.close()
    report["closed"] = not getattr(runtime.lease, "held", True)
    print("WORKER_REPORT " + json.dumps(report, ensure_ascii=False, default=str))
    '''
)


# ---------------------------------------------------------------------------------------------
# 场景：为了模块级运行只收本文件的新用例，DScenarioBase 只作为组合进来的夹具，不继承进测试类
# ---------------------------------------------------------------------------------------------


class _Scenario(DScenarioBase):
    """Fixture holder; never loaded as a suite (``load_tests`` below returns only the new test)."""

    def runTest(self) -> None:  # pragma: no cover - never run
        pass


def load_tests(loader, tests, pattern):  # noqa: ARG001
    """Module run selects exactly the new class, not the inherited W05 trio."""
    return unittest.TestSuite([loader.loadTestsFromTestCase(WorkRevisitRuntimeTest)])


@posix_only
class WorkRevisitRuntimeTest(unittest.TestCase):
    """Real owner + real runtime in two PIDs, one data root, real writer request body."""

    def setUp(self) -> None:
        self.scenario = _Scenario("runTest")
        self.scenario.setUp()
        self.addCleanup(self.scenario.doCleanups)
        self.prepared = self.scenario.prepare_scenario()
        self.data = self.prepared.data
        self.instance_dir = Path(self.scenario.instance)
        self.subject = self.data["subject_id"]
        self.workspace_id = self.data["workspace_id"]
        self.grant_id = self.data["artifact_grant_id"]
        self.port = lap.LifeArtifactPort(str(self.scenario.services.supply_path), self.subject)

        self.material_id = self._create_artifact("资料页", "别人的资料，只能引用。", "material")
        self.items_id = self._create_artifact("私人日常事项", "{}", "items")

        self.work_id = self.data["work_artifact_id"]
        self.old_version_id = self._commit(V_OLD, "old")
        self.previous_version_id = self._commit(V_PREV, "prev")
        self.previous_number = int(
            self.port.read_version(self.work_id, self.previous_version_id)["version_number"])
        self.head_version_id = self._commit(V_HEAD, "head")
        self.assertEqual(self.port.read_version(self.work_id, self.head_version_id)["content"],
                         V_HEAD)

        self.phone = phone_service.PhoneService(self._phone_instance(), environment={})
        self.httpd = phone_server.create_server(self.phone, csrf_token=CSRF,
                                                port=free_port())
        self.base_url = "http://127.0.0.1:%d" % int(self.httpd.server_address[1])
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.addCleanup(self._close_http)

        # A derived work that cites the sentinel-bearing previous version.  It must survive the
        # Life restart with its source and must not be created twice on replay.
        self.project_service = pps.PrivateProjectService(self.port, self.workspace_id,
                                                         self.grant_id)
        self.counts_before = self.project_service.list_projects()["counts"]
        self.derived = self._derive("work-revisit:derive:runtime")
        self.derived_id = str(self.derived["artifact_id"])
        self.counts_after_derive = self.project_service.list_projects()["counts"]
        self.assertEqual(self.counts_after_derive["total"],
                         self.counts_before["total"] + 1)

    # -- fixture plumbing ----------------------------------------------------------------

    def _phone_instance(self) -> dict:
        items_id = self.items_id
        reading_id = self.material_id
        world_home = Path(str(self.data["world_home"]))
        return {
            "schema_version": phone_service.SCHEMA_INSTANCE,
            "kind": phone_service.KIND_INSTANCE,
            "name": "work-revisit-runtime-fixture",
            "subject": self.subject,
            "local_owner_uid": os.geteuid(),
            "root": str(self.instance_dir),
            "profile_home": str(self.instance_dir),
            "world_home": str(world_home),
            "world_id": self.data.get("world_id") or "w-work-revisit-runtime",
            "supply_socket": str(self.scenario.services.supply_path),
            "private_items_artifact_id": items_id,
            "reading_artifact_id": reading_id,
            "artifact_grant_id": self.grant_id,
            "workspace_id": self.workspace_id,
            "world_body_socket": str(world_home / "run" / "gateway.sock"),
            "features": {},
        }

    def _close_http(self) -> None:
        httpd = getattr(self, "httpd", None)
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        thread = getattr(self, "thread", None)
        if thread is not None:
            thread.join(timeout=10)

    def _create_artifact(self, title: str, content: str, tag: str) -> str:
        outcome = self.port.create_artifact(
            workspace_id=self.workspace_id, title=title, content=content,
            grant_id=self.grant_id,
            operation_key=lap.operation_key_for("work-revisit-runtime", tag, self.subject))
        self.assertTrue(outcome.applied, outcome)
        return str(outcome.artifact_id)

    def _commit(self, content: str, tag: str) -> str:
        head = self.port.read_head(self.work_id) or {}
        outcome = self.port.commit_version(
            artifact_id=self.work_id, expected_head=str(head["version_id"]), content=content,
            grant_id=self.grant_id,
            operation_key=lap.operation_key_for("work-revisit-runtime", tag, self.subject))
        self.assertTrue(outcome.applied, outcome)
        return str((self.port.read_head(self.work_id) or {})["version_id"])

    def _derive(self, key: str) -> dict:
        answer = self.project_service.create_derived_project(
            title="从夜航手记开的一件", content="派生正文：她只改了称谓。",
            source_artifact_id=self.work_id, source_version_id=self.previous_version_id,
            purpose="adapt", note="只改称谓", operation_key=key)
        self.assertTrue(answer.get("ok"), answer)
        return answer

    def _worker(self, phase: str) -> dict:
        code = (WORKER
                .replace("__TESTS__", repr(str(ROOT / "tests")))
                .replace("__SCRIPTS__", repr(str(ROOT / "components" / "life" / "scripts")))
                .replace("__INSTANCE__", repr(str(self.instance_dir)))
                .replace("__PHASE__", repr(phase))
                .replace("__WORK__", repr(self.work_id))
                .replace("__SENTINEL__", repr(SENTINEL_OLD)))
        done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                              timeout=600,
                              env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(done.returncode, 0, (done.returncode, done.stdout[-1500:],
                                              done.stderr[-3000:]))
        lines = [line for line in done.stdout.splitlines()
                 if line.startswith("WORKER_REPORT ")]
        self.assertTrue(lines, done.stdout[-2000:])
        return json.loads(lines[-1].split(" ", 1)[1])

    def _get(self, path: str) -> tuple[int, dict]:
        request = urllib.request.Request(self.base_url + path, method="GET")
        request.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return int(response.status), json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return int(error.code), json.loads(error.read().decode("utf-8"))

    def _head(self) -> str:
        return str((self.port.read_head(self.work_id) or {})["version_id"])

    def _versions(self, artifact_id: str) -> int:
        return int(self.port.list_versions(artifact_id, limit=50)["total_versions"])

    # -- the chain -----------------------------------------------------------------------

    def test_runtime_restart_keeps_work_revisit_and_continues_the_activity(self) -> None:
        # Owner-side preconditions: the old sentence is only in the previous version, and the
        # real phone read/compare surface sees both the old body and the difference.
        old = self.port.read_version(self.work_id, self.previous_version_id)
        self.assertIn(SENTINEL_OLD, old["content"])
        self.assertNotIn(SENTINEL_OLD, V_HEAD)
        status, detail = self._get("%s/projects/%s/versions/%s"
                                   % (API, self.work_id, self.previous_version_id))
        self.assertEqual(status, 200, detail)
        self.assertIn(SENTINEL_OLD, detail["version"]["content"])
        status, compare = self._get("%s/projects/%s/versions/%s/compare"
                                    % (API, self.work_id, self.previous_version_id))
        self.assertEqual(status, 200, compare)
        self.assertTrue(compare["compare"]["content_changed"], compare)

        # Named refusal: a version of this work is not readable through another artifact.
        with self.assertRaises(lap.ArtifactOwnerRefused) as refused:
            self.port.read_version(self.material_id, self.previous_version_id)
        self.assertIn("VERSION_NOT_IN_ARTIFACT", str(refused.exception))

        head_before = self._head()
        derived_versions_before = self._versions(self.derived_id)

        # ---- worker A (PID A): real writing chain, then pause + release -----------------
        first = self._worker("first")
        self.assertNotEqual(first["pid"], os.getpid(), first)
        self.assertEqual(first["assembled_state"], "READY", first)
        self.assertEqual(first["round"]["outcome"], "ACTED", first)
        self.assertTrue(first["round"]["owner_receipt"], first)
        self.assertIsNot(first["round"]["owner_version_created"], False, first)
        self.assertEqual(first["round"]["decision"], "START", first)
        self.assertEqual(first["cognition_chosen"][-1]["decision"], "START", first)
        self.assertGreaterEqual(int(first["round"]["llm_calls"]), 1, first)
        self.assertTrue(first["closed"], first)
        self.assertTrue(first["pause_set"]["paused"], first["pause_set"])
        head_after_first = self._head()
        self.assertNotEqual(head_after_first, head_before, first)
        self.assertEqual(first["head_after_round"], head_after_first, first)

        # The real request, assembled by the cycle itself from the owner's previous version.
        self.assertEqual(len(first["requests"]), 1, first["requests"])
        request = first["requests"][0]
        self.assertEqual(len(request["earlier_versions"]), 1, request)
        earlier = request["earlier_versions"][0]
        self.assertEqual(earlier["version_id"], self.previous_version_id, earlier)
        self.assertEqual(int(earlier["version_number"]), self.previous_number, earlier)
        self.assertTrue(earlier["has_sentinel"], earlier)
        # The head body never carried the sentinel: what the writer saw came from the owner's
        # previous version, not from ``tail_of_current_text``.
        self.assertFalse(request["tail_has_sentinel"], request)
        self.assertTrue(request["prompt_has_sentinel"], request)

        calls_after_first = int(first["budget_after_reserve"]["utc_day_calls"])
        self.assertEqual(calls_after_first,
                         int(first["budget_at_start"]["utc_day_calls"]) + 1, first)

        # ---- the gap: no write, no replayed derivation ----------------------------------
        self.assertEqual(self._head(), head_after_first)
        self.assertEqual(self._versions(self.derived_id), derived_versions_before)
        self.assertEqual(self.project_service.list_projects()["counts"],
                         self.counts_after_derive)

        # ---- worker B (PID B): new runtime on the same root, paused round has 0 requests --
        second = self._worker("second")
        self.assertNotEqual(second["pid"], first["pid"], (second, first))
        self.assertNotEqual(second["pid"], os.getpid(), second)
        self.assertEqual(second["assembled_state"], "READY", second)
        self.assertTrue(second["closed"], second)
        self.assertEqual(second["run_id"], first["run_id"], (second, first))
        self.assertEqual(int(second["budget_at_start"]["utc_day_calls"]), calls_after_first,
                         second["budget_at_start"])
        self.assertEqual(int(second["budget_at_start"]["utc_day_max"]),
                         int(first["budget_after_reserve"]["utc_day_max"]), second)
        self.assertEqual(int(second["budget_at_start"]["run_window_max"]),
                         int(first["budget_after_reserve"]["run_window_max"]), second)
        self.assertTrue(second["pause_at_start"]["paused"], second["pause_at_start"])
        self.assertEqual(second["state_at_start"], odl.STATE_PAUSED, second)
        self.assertEqual(second["paused_round"]["outcome"], "PAUSED_BY_OPERATOR", second)
        self.assertEqual(second["paused_round"]["llm_calls"], 0, second)
        self.assertEqual(second["cognition_calls_while_paused"], 0, second)
        self.assertEqual(second["requests_after_paused"], 0, second)
        self.assertEqual(second["head_while_paused"], head_after_first, second)

        # ... and resume legitimately continues the *same* Activity into a new owner version.
        self.assertEqual(second["round"]["outcome"], "ACTED", second)
        self.assertTrue(second["round"]["owner_receipt"], second)
        self.assertEqual(second["round"]["decision"], "CONTINUE", second)
        self.assertEqual(second["cognition_chosen"][-1]["decision"], "CONTINUE", second)
        self.assertIn("CONTINUE", second["cognition_chosen"][-1]["legal"], second)
        self.assertEqual(second["round"]["activity_ref"], first["round"]["activity_ref"], second)
        self.assertTrue(str(second["round"]["detail"] or "").startswith("owner committed"),
                        second["round"])
        self.assertEqual(second["requests_after_resume"], 1, second)
        self.assertEqual(len(second["requests"]), 1, second["requests"])
        # earlier_versions holds the head from before worker A's write (version 4),
        # not head_after_first, worker A's newly written head (version 5).
        resume_earlier = second["requests"][0]["earlier_versions"]
        self.assertEqual(len(resume_earlier), 1, resume_earlier)
        head_after_first_number = int(
            self.port.read_version(self.work_id, head_after_first)["version_number"])
        self.assertEqual(int(resume_earlier[0]["version_number"]),
                         head_after_first_number - 1, resume_earlier)
        self.assertNotEqual(resume_earlier[0]["version_id"], head_after_first, resume_earlier)
        self.assertFalse(second["requests"][0]["tail_has_sentinel"], second["requests"])
        self.assertEqual(second["activity_after"]["status"], "ACTIVE", second["activity_after"])
        head_after_second = self._head()
        self.assertNotEqual(head_after_second, head_after_first, second)
        self.assertEqual(second["head_after_round"], head_after_second, second)

        # ---- after both restarts: history, compare, provenance and no duplicate ----------
        reread = self.port.read_version(self.work_id, self.previous_version_id)
        self.assertIn(SENTINEL_OLD, reread["content"])
        self.assertNotIn(SENTINEL_OLD,
                         self.port.read_version(self.work_id, head_after_second)["content"])
        status, compare_after = self._get(
            "%s/projects/%s/versions/%s/compare"
            % (API, self.work_id, self.previous_version_id))
        self.assertEqual(status, 200, compare_after)
        self.assertTrue(compare_after["compare"]["content_changed"], compare_after)
        self.assertIsNotNone(compare_after["compare"]["first_difference"], compare_after)

        status, provenance = self._get("%s/projects/%s/provenance"
                                       % (API, self.derived_id))
        self.assertEqual(status, 200, provenance)
        self.assertEqual(provenance["provenance"]["source_artifact_id"], self.work_id)
        self.assertEqual(provenance["provenance"]["source_version_id"],
                         self.previous_version_id)
        self.assertEqual(self._versions(self.derived_id), derived_versions_before)

        # The same intent, replayed after the restart, is still the same artifact.
        replayed = self._derive("work-revisit:derive:runtime")
        self.assertEqual(str(replayed["artifact_id"]), self.derived_id)
        self.assertEqual(self.project_service.list_projects()["counts"],
                         self.counts_after_derive)
        self.assertEqual(self._versions(self.derived_id), derived_versions_before)

        # Non-sensitive facts for the supervisor's independent re-run (no private prose).
        print("WORK_REVISIT_RUNTIME_OK " + json.dumps({
            "worker_pids": [first["pid"], second["pid"]],
            "run_id": first["run_id"],
            "lease_released": [first["closed"], second["closed"]],
            "activity_ref": first["round"]["activity_ref"],
            "head_before": head_before,
            "head_after_first": head_after_first,
            "head_after_second": head_after_second,
            "previous_version_id": self.previous_version_id,
            "previous_version_number": self.previous_number,
            "derived_artifact_id": self.derived_id,
            "derived_versions": derived_versions_before,
            "first_round_request_versions": request["earlier_versions"],
            "resume_round_request_versions": resume_earlier,
        }, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main(verbosity=2)

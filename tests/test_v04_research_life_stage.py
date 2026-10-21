#!/usr/bin/env python3
"""v0.4 stage A+B: one question + one real material -> one research work, and its reuse.

What this file pins down (and nothing else)
-------------------------------------------
* **pure -- the research option**: an option exists only when B's research workspace is really
  wired, it carries exactly the material hits the workspace returned (owner id + the revision the
  hit was read at), a question with no material keeps the unknown honest instead of inventing a
  source, and a moved material is a *new* option rather than a silently reused one.
* **pure -- the seam refuses honestly**: an unwired workspace answers "not wired" / nothing, never
  an empty-but-successful read; a service missing a documented method is reported UNUSABLE.
* **pure -- the content writer**: an HTML-comment header (the research marker) survives both
  ``append_paragraph`` and ``revise_tail``, the bounded research context really rides into the
  prompt, and ``no_change`` stays a legal answer that writes nothing.
* **pure -- quiet state**: the situation fingerprint moves when a material revision or a research
  head/source-check changes, and does not move for a mere clock tick (no text enters it).
* **the loop** (POSIX only -- the life modules import ``fcntl``): one whole chain on the **real**
  owners -- Life Supply on its own Unix socket, AG-1's hard constraints and raw-JSON validation,
  AG-2 adoption, AR-0's managed-artifact adapter -- where a question she wrote plus an imported
  material become a research work (created by B's ``ResearchWorkspaceService``), that work is
  written through AR-0 with a prompt that carries the source's real body text and version, and the
  next decision/writing context reuses the result.  Plus: a material that moved is STALE, an
  archived one is UNAVAILABLE, and a material is never offered as work.

Stand-ins, named as such
------------------------
* ``_ScriptedModelClient`` -- a stand-in for the product model: it answers one fixed JSON edit and
  records every ``user_prompt`` it was given.  No credential is read and no network is used.
* ``_ResearchCognition`` -- a stand-in for the decision model: it answers a raw JSON object, which
  real AG-1 then validates.  It never builds a Decision itself.
* ``_FakeResearch`` in the quiet-state test -- a stand-in for the seam's *fingerprint parts* only.

Fixtures live under ``/tmp/nyairo-v04-research-fixtures`` and are **kept** (no TemporaryDirectory,
no unlink): a failing run must leave its state on disk.

Run (POSIX host)::

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v tests.test_v04_research_life_stage
"""
from __future__ import annotations

import json
import os
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
LIFE_SCRIPTS = ROOT / "components" / "life" / "scripts"
sys.path[:0] = [str(ROOT), str(LIFE_SCRIPTS)]

import life_project_options as lpo  # noqa: E402
import research_work_context as rwc  # noqa: E402

POSIX_ONLY = unittest.skipUnless(os.name == "posix", "life modules import fcntl (POSIX only)")

TS0 = "2026-11-05T09:00:00+00:00"
TS_LATER = "2026-11-05T11:00:00+00:00"

MATERIAL_ID = "artifact:77777777777777777777777777777777"
MATERIAL_HEAD = "artifact-version:7777"
MATERIAL_TEXT = "第一章：村里的水井在旱季仍然有水，但取水要排很长的队。"

QUESTION = "为什么旱季取水要排这么长的队？"

#: The one sentence the *scripted model* really writes (via AR-0) into the research document.
#: The follow-up prompts must be able to see it -- a head-only excerpt cannot.
FINDING = "暂定发现：井水靠村北的暗渠补给，不是雨水。"

FIXTURE_ROOT = Path(os.environ.get("NYAIRO_RESEARCH_FIXTURE_ROOT")
                    or "/tmp/nyairo-v04-research-fixtures")


def fixture_dir(name: str) -> Path:
    """A per-run, per-case directory under the kept fixture root (never cleaned up)."""

    path = FIXTURE_ROOT / f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-{name}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def material_hit(*, revision: str = MATERIAL_HEAD, artifact_id: str = MATERIAL_ID) -> dict:
    return {"artifact_id": artifact_id, "title": "田野笔记", "source_revision": revision,
            "source_kind": "user_supplied", "origin": "操作者导入的文本",
            "excerpt": MATERIAL_TEXT}


def unclear_question() -> list[dict]:
    return [{"marker_id": "unclear:aa", "source_id": MATERIAL_ID,
             "source_revision": MATERIAL_HEAD, "note": QUESTION}]


# ---------------------------------------------------------------------------------------------
# pure: the research option
# ---------------------------------------------------------------------------------------------


class ResearchOptionTest(unittest.TestCase):
    def test_an_option_is_offered_only_when_the_workspace_is_wired(self):
        plain = lpo.build_options(questions=unclear_question())
        self.assertEqual([row["kind"] for row in plain], [lpo.KIND_QUESTION])
        self.assertFalse(plain[0]["research"])
        self.assertEqual(plain[0]["materials"], [])

        wired = lpo.build_options(questions=unclear_question(),
                                  research_materials=[material_hit()],
                                  research_available=True)
        self.assertEqual([row["kind"] for row in wired], [lpo.KIND_RESEARCH])
        self.assertTrue(wired[0]["research"])
        # One question is one option -- never two options for the same seed.
        self.assertEqual(len(wired), 1, wired)

    def test_the_option_carries_the_real_material_hits_and_nothing_invented(self):
        options = lpo.build_options(questions=unclear_question(),
                                    research_materials=[material_hit()],
                                    research_available=True)
        option = options[0]
        self.assertEqual(option["seed_text"], QUESTION)
        self.assertEqual([row["artifact_id"] for row in option["materials"]], [MATERIAL_ID])
        self.assertEqual([row["source_revision"] for row in option["materials"]], [MATERIAL_HEAD])
        # The provenance refs keep the owner's id and the exact revision it was read at.
        self.assertIn(f"{MATERIAL_ID}@{MATERIAL_HEAD}", option["source_refs"])
        self.assertIn("pnote:unclear-aa", option["source_refs"])
        self.assertIn("田野笔记", option["scope"])
        self.assertEqual(option["uncertainty"], lpo.DEFAULT_UNCERTAINTY)
        # A hit without an owner id is not a hit: it is dropped instead of being cited.
        dropped = lpo.build_options(
            questions=unclear_question(),
            research_materials=[{"title": "没有 id 的东西", "source_revision": MATERIAL_HEAD}],
            research_available=True)
        self.assertEqual(dropped[0]["materials"], [])

    def test_a_question_without_material_keeps_the_unknown_honest(self):
        options = lpo.build_options(questions=unclear_question(), research_materials=[],
                                    research_available=True)
        option = options[0]
        self.assertEqual(option["kind"], lpo.KIND_RESEARCH)
        self.assertEqual(option["materials"], [])
        self.assertEqual(option["source_refs"], ["pnote:unclear-aa"])
        self.assertEqual(option["uncertainty"], lpo.NO_MATERIAL_UNCERTAINTY)
        self.assertIn("没有可读资料", option["scope"])

    def test_a_moved_material_makes_a_new_option_instead_of_reusing_the_old_one(self):
        before = lpo.build_options(questions=unclear_question(),
                                   research_materials=[material_hit()], research_available=True)
        after = lpo.build_options(
            questions=unclear_question(),
            research_materials=[material_hit(revision="artifact-version:8888")],
            research_available=True)
        self.assertNotEqual(before[0]["option_ref"], after[0]["option_ref"])
        self.assertEqual(after[0]["materials"][0]["source_revision"], "artifact-version:8888")


# ---------------------------------------------------------------------------------------------
# pure: the seam answers "not wired" honestly
# ---------------------------------------------------------------------------------------------


class ResearchExcerptTest(unittest.TestCase):
    """The bounded digest a later prompt reads must be *her text*, not the marker/template head."""

    def _marker(self, question: str = QUESTION) -> str:
        payload = {"question": question, "scope": "只依据：田野笔记",
                   "source_refs": [{"artifact_id": MATERIAL_ID,
                                    "source_revision": MATERIAL_HEAD}],
                   "title": "研究：一个问题", "uncertainty": "暂定，尚未验证"}
        return "<!-- nyairo-research:v1 %s -->" % json.dumps(payload, ensure_ascii=False)

    def test_a_long_document_keeps_its_tail_finding_and_never_the_marker_alone(self):
        seam = rwc.ResearchSeam(None)
        marker = self._marker()
        # The marker alone is already longer than the bound; the finding is at the very end, where
        # AR-0 appends.  A head-only excerpt cannot contain it.
        self.assertGreater(len(marker), 240)
        body = "## 问题\n" + QUESTION + "\n\n## 暂定发现\n" + ("模板填充。" * 200) + "\n" + FINDING
        view = seam._project_view({"artifact_id": "artifact:r1", "content": marker + "\n\n" + body,
                                   "source_checks": []})
        excerpt = view["content_excerpt"]
        self.assertIn(FINDING, excerpt)
        self.assertNotIn("<!--", excerpt)
        self.assertNotIn("nyairo-research:v1", excerpt)
        self.assertLessEqual(len(excerpt), seam.excerpt_chars)
        # Both ends are represented: the body's head *and* the newest written part.
        self.assertIn("## 问题", excerpt)

    def test_a_short_document_is_its_own_text_and_plain_text_is_never_lost(self):
        seam = rwc.ResearchSeam(None)
        short = "## 问题\n" + QUESTION + "\n\n# 暂定发现\n" + FINDING
        view = seam._project_view({"artifact_id": "artifact:r2",
                                   "content": self._marker() + "\n\n" + short,
                                   "source_checks": []})
        self.assertEqual(view["content_excerpt"], short)
        plain = seam._project_view({"artifact_id": "artifact:r3", "content": "一段没有标记的正文",
                                    "source_checks": []})
        self.assertEqual(plain["content_excerpt"], "一段没有标记的正文")


class ResearchSeamWithoutWorkspaceTest(unittest.TestCase):
    def test_an_unwired_seam_reads_nothing_and_says_why(self):
        seam = rwc.ResearchSeam(None)
        self.assertFalse(seam.available())
        self.assertEqual(seam.availability()["status"], "NOT_WIRED")
        self.assertEqual(seam.materials(), [])
        self.assertEqual(seam.materials(), [])
        self.assertEqual(seam.material_ids(), [])
        self.assertEqual(seam.existing(), [])
        self.assertIsNone(seam.read_material(MATERIAL_ID))
        self.assertIsNone(seam.writing_context(MATERIAL_ID))
        self.assertEqual(seam.decision_context()["status"], "NOT_WIRED")
        self.assertEqual(seam.fingerprint_parts(),
                         {"research_materials": "", "research_works": ""})
        with self.assertRaises(rwc.ResearchSeamError) as ctx:
            seam.create_research(question=QUESTION, source_refs=[], operation_key="op:x")
        self.assertEqual(ctx.exception.reason_code, rwc.REASON_CREATE_REFUSED)

    def test_a_service_missing_a_documented_method_is_unusable_rather_than_empty(self):
        class _Half:
            def list_materials(self, **_kwargs):
                return {"ok": True, "status": "ok", "data": {"materials": []}}

        seam = rwc.ResearchSeam(_Half())
        self.assertFalse(seam.available())
        described = seam.availability()
        self.assertEqual(described["status"], "UNUSABLE")
        self.assertIn("create_research", described["missing"])
        self.assertEqual(seam.materials(), [])

    def test_an_unrelated_owner_error_is_a_named_failure_not_an_empty_list(self):
        class _Refusing:
            def list_materials(self, **_kwargs):
                return {"ok": False, "status": "unavailable", "reason_code": "SUPPLY_UNAVAILABLE"}

            def read_material(self, *_a, **_k):  # pragma: no cover - not reached
                raise AssertionError

            def create_research(self, **_kwargs):  # pragma: no cover - not reached
                raise AssertionError

            def list_research(self, **_kwargs):
                return {"ok": True, "status": "ok", "data": {"projects": []}}

            def read_research(self, _artifact_id):
                return {"ok": False, "status": "not_found", "reason_code": "RESEARCH_NOT_FOUND"}

        seam = rwc.ResearchSeam(_Refusing())
        self.assertTrue(seam.available())
        self.assertEqual(seam.materials(), [])
        self.assertIn("RESEARCH_ENVELOPE_NOT_OK", seam.last_error)
        self.assertIsNone(seam.writing_context("artifact:abc"))


# ---------------------------------------------------------------------------------------------
# pure: the content writer keeps the header and carries the research context
# ---------------------------------------------------------------------------------------------


class _ScriptedModelClient:
    """Stand-in for the product model: one fixed JSON answer, every prompt recorded."""

    def __init__(self, payload: dict | None = None) -> None:
        self.payload = payload or {"kind": "append_paragraph", "text": "补一句自己的想法。"}
        self.prompts: list[dict] = []

    def complete(self, *, system_prompt: str, user_prompt: str, timeout_s: float = 0.0,
                 max_output_tokens: int = 0):
        self.prompts.append({"system": system_prompt, "user": user_prompt})
        return SimpleNamespace(text=json.dumps(self.payload, ensure_ascii=False))


class ModelWriterHeaderTest(unittest.TestCase):
    def _point(self, content: str):
        from life_artifact_port import ArtifactResumePoint

        return ArtifactResumePoint(
            artifact_id=MATERIAL_ID, title="研究：一个问题",
            head_version_id="artifact-version:aaaa", version_number=1, content=content,
            lifecycle="ACTIVE", revision=1)

    def _writer(self, payload=None):
        from model_continuation_writer import ModelContinuationWriter

        client = _ScriptedModelClient(payload)
        return ModelContinuationWriter(client), client

    def test_the_marker_header_survives_append_and_revise_tail(self):
        header = '<!-- nyairo-research:v1 {"question":"为什么" } -->'
        writer, _client = self._writer()
        appended = writer.next_content(self._point(header), observed_at=TS0)
        self.assertTrue(appended.startswith(header), appended)
        self.assertIn("补一句自己的想法", appended)
        self.assertEqual(writer._merge("", "append_paragraph", "只有一段"), "只有一段\n")
        revised = writer._merge(f"{header}\n\n第一段\n\n第二段", "revise_tail", "改过的第二段")
        self.assertTrue(revised.startswith(header), revised)
        self.assertIn("第一段", revised)
        self.assertIn("改过的第二段", revised)
        self.assertNotIn("第二段\n", revised.replace("改过的第二段", ""))
        # ... and a header that is the *only* content is never dropped by a tail rewrite.
        only_header = writer._merge(header, "revise_tail", "新的第一段")
        self.assertTrue(only_header.startswith(header), only_header)
        self.assertIn("新的第一段", only_header)

    def test_the_bounded_research_context_rides_into_the_prompt(self):
        writer, client = self._writer()
        context = {"kind": "research_document_context",
                   "own_research": {"question": QUESTION, "scope": "只依据：田野笔记",
                                    "uncertainty": "暂定，尚未验证",
                                    "sources": [{"artifact_id": MATERIAL_ID,
                                                 "source_revision": MATERIAL_HEAD,
                                                 "availability": "CURRENT",
                                                 "text": MATERIAL_TEXT}]}}
        writer.next_content(self._point("<!-- header -->\n\n第一段"), observed_at=TS0,
                            context=context)
        payload = json.loads(client.prompts[-1]["user"])
        self.assertEqual(payload["research_context"], context)
        self.assertIn(MATERIAL_TEXT, json.dumps(payload, ensure_ascii=False))
        self.assertIn(MATERIAL_HEAD, json.dumps(payload, ensure_ascii=False))

    def test_no_change_is_legal_and_writes_nothing(self):
        from model_continuation_writer import ModelNoChange

        writer, _client = self._writer({"kind": "no_change", "text": ""})
        with self.assertRaises(ModelNoChange):
            writer.next_content(self._point("<!-- header -->\n\n第一段"), observed_at=TS0)
        self.assertEqual(writer.no_change_count, 1)
        # A no_change that carries a paragraph is a contradiction, not an edit.
        writer2, _client2 = self._writer({"kind": "no_change", "text": "硬加一段"})
        with self.assertRaises(Exception) as ctx:
            writer2.next_content(self._point("第一段"), observed_at=TS0)
        self.assertIn("MODEL_NO_CHANGE_CARRIES_TEXT", str(ctx.exception))


# ---------------------------------------------------------------------------------------------
# pure: quiet state follows real content, never the clock
# ---------------------------------------------------------------------------------------------


class _FakeResearch:
    """Stand-in for the seam's fingerprint parts (no owner is asked)."""

    def __init__(self, parts: dict) -> None:
        self.parts = dict(parts)

    def fingerprint_parts(self) -> dict:
        return dict(self.parts)


class ResearchQuietStateTest(unittest.TestCase):
    def test_a_material_revision_or_a_research_head_moves_the_fingerprint(self):
        import life_step_readiness as lsr

        self.assertIn("research_materials", lsr.SITUATION_KEYS)
        self.assertIn("research_works", lsr.SITUATION_KEYS)
        base = {"foreground_activity_ref": None, "research_materials": "m1@rev1@originA@abcd",
                "research_works": "artifact:r1@head1@readable@m1=CURRENT"}
        same = dict(base)
        moved_material = dict(base, research_materials="m1@rev2@originA@abcd")
        moved_research = dict(base, research_works="artifact:r1@head2@readable@m1=CURRENT")
        stale_check = dict(base, research_works="artifact:r1@head1@readable@m1=STALE")
        self.assertEqual(lsr.situation_fingerprint(base), lsr.situation_fingerprint(same))
        self.assertNotEqual(lsr.situation_fingerprint(base),
                            lsr.situation_fingerprint(moved_material))
        self.assertNotEqual(lsr.situation_fingerprint(base),
                            lsr.situation_fingerprint(moved_research))
        self.assertNotEqual(lsr.situation_fingerprint(base),
                            lsr.situation_fingerprint(stale_check))
        # Nothing in the parts is time-derived: a tick that changes nothing changes nothing.
        self.assertNotIn("observed_at", lsr.SITUATION_KEYS)

    def test_the_seam_builds_its_parts_from_ids_versions_and_checks_only(self):
        class _Service:
            def list_materials(self, **_kwargs):
                return {"ok": True, "status": "ok",
                        "data": {"materials": [material_hit()]}}

            def read_material(self, *_a, **_k):  # pragma: no cover - not reached
                raise AssertionError

            def create_research(self, **_kwargs):  # pragma: no cover - not reached
                raise AssertionError

            def list_research(self, **_kwargs):
                return {"ok": True, "status": "ok",
                        "data": {"projects": [{"artifact_id": "artifact:r1",
                                               "head_version_id": "artifact-version:h1"}]}}

            def read_research(self, _artifact_id):
                return {"ok": True, "status": "ok",
                        "data": {"project": {"artifact_id": "artifact:r1",
                                             "head_version_id": "artifact-version:h1",
                                             "content": "",
                                             "source_checks": [
                                                 {"artifact_id": MATERIAL_ID,
                                                  "source_revision": MATERIAL_HEAD,
                                                  "status": "STALE"}]}}}

        seam = rwc.ResearchSeam(_Service(), max_existing=2)
        parts = seam.fingerprint_parts()
        self.assertIn(MATERIAL_ID, parts["research_materials"])
        self.assertIn(MATERIAL_HEAD, parts["research_materials"])
        self.assertIn("artifact:r1", parts["research_works"])
        self.assertIn("STALE", parts["research_works"])
        self.assertNotIn(MATERIAL_TEXT, parts["research_materials"])
        self.assertNotIn(QUESTION, parts["research_works"])


# ---------------------------------------------------------------------------------------------
# the real owners (Supply + AG-1 + AG-2 + AR-0), with only the model calls scripted
# ---------------------------------------------------------------------------------------------

try:  # the world fixture base is pure python; it is only *used* on POSIX below
    from components.worldgen.tests._setup import WorldGenTestCase as _WorldGenBase
except Exception:  # pragma: no cover - fixture tree absent
    _WorldGenBase = unittest.TestCase


class _ResearchCognition:
    """The decision model, scripted: it answers a raw JSON object and nothing else.

    Real AG-1 validates it and real AG-2 adopts it; it never builds a Decision itself, and the
    product provider is never called.
    """

    model_id = "stub:research-chain"
    model_version = "research-chain-v1"

    def __init__(self) -> None:
        self.call_count = 0
        self.requests: list = []

    def invoke(self, request, *, schema_retry_index: int = 0):
        import agency_decision_ag1 as ag1

        self.call_count += 1
        self.requests.append(request)
        candidates = [dict(c) for c in (getattr(request, "candidates", []) or [])]
        allowed = [str(code) for code in (getattr(request, "allowed_reason_codes", []) or [])]
        reason = allowed[:1] or ["NO_LEGAL_CANDIDATE"]
        raw: dict = {"decision": "NO_ACTION", "selected_candidate_id": None,
                     "reason_codes": reason, "short_rationale": "stub: nothing to do",
                     "deferred_candidate_ids": []}
        chosen = None
        for candidate in candidates:
            if str(candidate.get("source_ref") or "").startswith("popt:") \
                    and "START" in (candidate.get("legal_decisions") or []):
                chosen = candidate
                break
        if chosen is not None:
            raw = {"decision": "START", "selected_candidate_id": chosen["candidate_id"],
                   "reason_codes": reason, "short_rationale": "stub: start the research",
                   "deferred_candidate_ids": []}
        return ag1.CognitiveDecisionResponse(
            model_run_ref="model_run:research:%d" % self.call_count,
            model_id=self.model_id, model_version=self.model_version,
            raw_output=raw, latency_ms=1)


@POSIX_ONLY
class ResearchLifeChainTest(_WorldGenBase):
    """One whole research experience on the real owners.

    Real: Life Supply (own Unix socket, governance + artifact store), B's research workspace,
    AG-0 candidates, AG-1 constraints/validation, AG-2 adoption, AR-0's managed-artifact adapter.
    Scripted: the decision answer, the content answer.  No credential is read.
    """

    def setUp(self) -> None:
        super().setUp()
        import tests.test_one_day_life as fx

        self._fx = fx
        self._runtimes: list = []
        self.services = fx.Services(Path(self.temp_dir(prefix="research-svc-")))
        self.addCleanup(self.services.stop)

    def tearDown(self) -> None:
        for runtime in getattr(self, "_runtimes", []):
            try:
                runtime.close()
            except Exception:  # a close failure must not hide the test result
                pass
        self._runtimes = []
        super().tearDown()

    # -- the fixture instance -----------------------------------------------------------

    def _chain(self, name: str = "chain"):
        import os
        from unittest import mock

        import autonomous_life_assembly as assembly
        import life_artifact_port as lap
        import life_integration_ag2 as ag2
        import life_runtime_production as lrp
        import managed_artifact_action_adapter as maaa
        import one_day_life as odl
        from model_continuation_writer import ModelContinuationWriter
        from components.worldgen.instance import init_instance
        from components.worldgen.tests._setup import make_valid_docs

        package_dir = self.temp_package(make_valid_docs(seed=929711, theme="coastal_town"),
                                        prefix="research-pkg-")
        world_home = Path(self.temp_dir(prefix="research-world-")) / "world-home"
        init_instance(package_dir, world_home)
        instance = Path(self.temp_dir(prefix="research-inst-")) / "instance"
        with mock.patch.dict(os.environ, self._fx.ENV_GATES):
            prepared = odl.prepare(instance=instance, supply_socket=self.services.supply_path,
                                   governance_socket=self.services.gov_path,
                                   subject="test:research", title="Research fixture",
                                   world_package=package_dir, create_private_items=True)
        self.assertTrue(prepared.get("ok"), prepared)
        data = odl.read_instance(instance)
        env = odl.instance_environment(instance)
        # The same names the production assembly passes, so the seam resolution is proven too.
        env.update({
            "NYAIRO_LIFE_SUPPLY_SOCKET": str(self.services.supply_path),
            "NYAIRO_LIFE_SUBJECT": str(data["subject_id"]),
            "NYAIRO_LIFE_WORKSPACE_ID": str(data["workspace_id"]),
            "NYAIRO_ARTIFACT_GRANT": str(data["artifact_grant_id"]),
        })
        if data.get("private_items_artifact_id"):
            env.setdefault("CHIYO_LIFE_PRIVATE_ITEMS_ARTIFACT_ID",
                           str(data["private_items_artifact_id"]))
        cognition = _ResearchCognition()
        client = _ScriptedModelClient()
        writer = ModelContinuationWriter(client, max_new_chars=300)
        port = lap.LifeArtifactPort(str(self.services.supply_path), data["subject_id"])
        adapter = maaa.SupplyManagedArtifactActionAdapter(
            port, grant_id=data["artifact_grant_id"], workspace_id=data["workspace_id"])
        switches = ag2.LifeRuntimeKillSwitches(
            life_runtime_enabled=True, agency_enabled=True, action_execution_enabled=True,
            proactive_enabled=False)
        runtime = lrp.build_production_life_runtime(
            odl.life_state_root(instance), kill_switches=switches, cognition_adapter=cognition,
            install_root=instance / "life", artifact_action_adapter=adapter)
        assembled = assembly.assemble_autonomous_life(
            runtime, environment=env, state_root=odl.life_state_root(instance),
            writer=writer, enabled=True)
        self.assertEqual(assembled["state"], assembly.READY, assembled)
        self._runtimes.append(runtime)
        return SimpleNamespace(runtime=runtime, assembled=assembled, cycle=assembled["cycle"],
                               cognition=cognition, client=client, writer=writer, env=env,
                               data=data, port=port, instance=instance, services=self.services)

    def _research_service(self, fixture):
        import research_workspace_service as rws

        service = rws.research_from_env(fixture.port, dict(fixture.env))
        self.assertIsNotNone(service, "B's research workspace did not resolve from the env")
        return service

    def _import_material(self, fixture):
        service = self._research_service(fixture)
        answer = service.import_material(
            title="田野笔记", text=MATERIAL_TEXT, source_kind="user_supplied",
            origin="操作者导入的文本（口述记录）", operation_key="op:material:1")
        self.assertTrue(answer.get("ok"), answer)
        material = answer["data"]["material"]
        return service, str(material["artifact_id"]), str(material["source_revision"])

    def _write_question(self, fixture, material_id: str, revision: str):
        import private_daily_items as pdi

        items_id = str(fixture.data["private_items_artifact_id"])
        store = pdi.PrivateDailyItems(fixture.port, items_id,
                                      workspace_id=str(fixture.data.get("workspace_id") or ""),
                                      grant_id=str(fixture.data["artifact_grant_id"]))
        result = store.mark_unclear(source_id=material_id, source_revision=revision,
                                    note=QUESTION, marker_id="unclear:research-1",
                                    operation_key="op:unclear:1")
        self.assertTrue(getattr(result, "ok", True), result)
        return store

    def _start_research(self, fixture):
        """One real round: the question + the material become a research work."""

        fixture.cognition.policy = None  # the scripted answer is the same for every round
        return fixture.cycle.evaluate_once(trigger_ref="evt:research:1", act=True)

    # -- the chain ---------------------------------------------------------------------

    def test_a_question_and_a_real_material_become_a_research_work_written_with_its_sources(self):
        import research_workspace_service as rws

        fixture = self._chain("research-chain")
        self.assertEqual(fixture.assembled["research"]["status"], "READY",
                         fixture.assembled["research"])
        self.assertTrue(fixture.cycle.research_available())

        service, material_id, revision = self._import_material(fixture)
        self._write_question(fixture, material_id, revision)

        # 1. The decision really sees the material hit with its provenance and her own question.
        options = fixture.cycle.project_options(now=TS0)
        self.assertTrue(options, "no project option was formed from the question")
        context = fixture.cycle.decision_situation_context(now=TS0)
        hits = context["research"]["material_hits"]
        self.assertEqual([row["artifact_id"] for row in hits], [material_id])
        self.assertEqual(hits[0]["source_revision"], revision)
        self.assertEqual([row["note"] for row in context.get("questions") or []], [QUESTION])
        option = [row for row in context["project_options"] if row["research"]]
        self.assertTrue(option, context["project_options"])
        self.assertEqual(option[0]["materials"][0]["artifact_id"], material_id)
        self.assertEqual(option[0]["materials"][0]["source_revision"], revision)

        # 2. She chooses it: B's workspace creates the research work on the real owner.
        #    The content answer is scripted to one *unique* sentence, so the follow-up prompts can
        #    only contain it if they really re-read what AR-0 wrote (not just the marker/template).
        fixture.client.payload = {"kind": "append_paragraph", "text": FINDING,
                                  "self_note": "stub: 写一条暂定发现"}
        out = self._start_research(fixture)
        created = out.get("project_created") or {}
        self.assertTrue(created.get("artifact_id"), out)
        self.assertTrue(created.get("research"), created)
        research_id = str(created["artifact_id"])
        # The *created* content (as the owner confirmed it), before AR-0's step writes anything.
        original = str(created.get("content") or "")
        self.assertTrue(original.startswith("<!-- "), original[:80])
        parsed = rws.parse_research_content(original)
        self.assertTrue(parsed.get("marker_present"), parsed)
        self.assertEqual(parsed.get("question"), QUESTION)
        self.assertEqual([row["artifact_id"] for row in parsed.get("source_refs") or []],
                         [material_id])
        self.assertEqual([row["source_revision"] for row in parsed.get("source_refs") or []],
                         [revision])
        self.assertTrue(parsed.get("uncertainty"), parsed)

        # 3. AR-0 wrote the next version of *that* work, and the writer's prompt really carried
        #    the source body and its version (not just a hash or a ref).
        receipt = out.get("step_receipt") or {}
        self.assertEqual(receipt.get("artifact_id"), research_id)
        self.assertEqual(int(receipt.get("version_number") or 0), 2)
        prompt = json.loads(fixture.client.prompts[-1]["user"])
        research_context = prompt["research_context"]
        own = research_context["own_research"]
        self.assertEqual(own["question"], QUESTION)
        self.assertTrue(own["scope"], own)
        self.assertEqual([row["availability"] for row in own["sources"]], ["AVAILABLE"])
        self.assertEqual([row["source_revision"] for row in own["sources"]], [revision])
        self.assertIn(MATERIAL_TEXT, own["sources"][0]["text"])
        self.assertIn(MATERIAL_TEXT, json.dumps(prompt, ensure_ascii=False))
        written = fixture.port.read_artifact(research_id).get("content") or ""
        self.assertTrue(written.startswith("<!-- "), written[:80])
        self.assertNotEqual(written, original)
        # The scripted model's own sentence really landed in the owner's document through AR-0.
        self.assertIn(FINDING, written)

        # 4. The next decision and the next ordinary work re-read the result as document context --
        #    and what they read really contains *what she wrote*, not only the marker or B's
        #    template.  A head-only excerpt (the marker alone is already > 240 chars) fails here.
        later = fixture.cycle.decision_situation_context(now=TS_LATER)
        results = later["research"]["research_results"]
        self.assertEqual([row["question"] for row in results], [QUESTION])
        research_excerpt = str(results[0]["excerpt"])
        self.assertIn(FINDING, research_excerpt)
        self.assertNotIn("<!--", research_excerpt)
        self.assertIn("CURRENT", results[0]["source_checks"])
        self.assertEqual(results[0]["scope"], own["scope"])
        self.assertEqual(results[0]["uncertainty"], parsed["uncertainty"])
        seed_context = fixture.cycle.research_writing_context(fixture.data["artifact_id"])
        self.assertIsNotNone(seed_context, "an ordinary work got no research context")
        self.assertNotIn("own_research", seed_context)
        self.assertEqual([row["question"] for row in seed_context["prior_research"]], [QUESTION])
        prior_excerpt = str(seed_context["prior_research"][0]["excerpt"])
        self.assertIn(FINDING, prior_excerpt)
        self.assertNotIn("<!--", prior_excerpt)
        self.assertEqual(seed_context["prior_research"][0]["scope"], own["scope"])
        self.assertEqual(seed_context["prior_research"][0]["uncertainty"], parsed["uncertainty"])
        # The same bounded digest is what a *writing* prompt for that research work carries.
        rewrite = fixture.cycle.research_writing_context(research_id)["own_research"]
        self.assertIn(FINDING, str(rewrite["content_excerpt"]))
        self.assertEqual(rewrite["question"], QUESTION)
        # ... and none of it is an admitted experience.
        self.assertNotIn("experience_id", json.dumps(seed_context))

    def test_a_moved_or_archived_material_is_never_called_current(self):
        import research_workspace_service as rws

        fixture = self._chain("research-stale")
        service, material_id, revision = self._import_material(fixture)
        self._write_question(fixture, material_id, revision)
        out = self._start_research(fixture)
        research_id = str((out.get("project_created") or {})["artifact_id"])
        self.assertTrue(research_id)

        # The material moves: one more owner version of the very same artifact.
        current = fixture.port.read_artifact(material_id)
        head = (current.get("head") or {}).get("version_id")
        material_content = current.get("content") or ""
        outcome = fixture.port.commit_version(
            artifact_id=material_id, expected_head=head,
            content=material_content + "\n\n后来补记：水井在雨季前清过一次。",
            grant_id=fixture.data["artifact_grant_id"], operation_key="op:material:move")
        self.assertTrue(outcome.applied, outcome)

        read = service.read_research(research_id)
        self.assertTrue(read.get("ok"), read)
        checks = read["data"]["project"]["source_checks"]
        self.assertEqual([row["status"] for row in checks], ["STALE"], checks)
        self.assertFalse(read["data"]["project"]["sources_current"])
        # A's own writing context says the same word -- and offers no text for that source.
        context = fixture.cycle.research_writing_context(research_id)
        own = context["own_research"]
        self.assertEqual([row["check"] for row in own["source_checks"]], ["STALE"])
        self.assertEqual([row["availability"] for row in own["sources"]], ["STALE"])
        self.assertEqual(own["sources"][0]["text"], "")

        # An archived material is UNAVAILABLE, never CURRENT.
        archived = fixture.port.archive_artifact(
            artifact_id=material_id,
            expected_revision=int((fixture.port.read_artifact(material_id).get("artifact")
                                   or {}).get("revision") or 1),
            grant_id=fixture.data["artifact_grant_id"], operation_key="op:material:archive")
        self.assertTrue(archived.applied, archived)
        read = service.read_research(research_id)
        checks = read["data"]["project"]["source_checks"]
        self.assertEqual([row["status"] for row in checks], ["UNAVAILABLE"], checks)
        context = fixture.cycle.research_writing_context(research_id)
        self.assertEqual([row["availability"] for row in context["own_research"]["sources"]],
                         ["UNAVAILABLE"])
        # The research document itself is untouched: its marker and question are still there.
        parsed = rws.parse_research_content(
            fixture.port.read_artifact(research_id).get("content") or "")
        self.assertEqual(parsed.get("question"), QUESTION)

    def test_a_material_is_never_offered_as_work_or_to_wrap_up(self):
        fixture = self._chain("research-exclusion")
        _service, material_id, _revision = self._import_material(fixture)
        items, _snap, _meta = fixture.cycle.artifact_source.materialize(observed_at=TS0)
        # The source keeps the owner's own id verbatim, so a candidate ref carries one extra
        # ``artifact:`` -- the identity to compare is the bare one.
        bare_material = material_id.split(":", 1)[1]
        bare_work = str(fixture.data["artifact_id"]).split(":", 1)[1]
        refs = [str(row.source_ref) for row in items]
        self.assertFalse([ref for ref in refs if bare_material in ref], refs)
        self.assertIn(material_id,
                      fixture.cycle.artifact_source.last_material_skip_reasons)
        self.assertEqual(
            fixture.cycle.artifact_source.last_material_skip_reasons[material_id],
            "RESEARCH_MATERIAL_NOT_A_WORK")
        # Her real work is still offered -- the exclusion is about material, not about work.
        self.assertTrue([ref for ref in refs if bare_work in ref], refs)
        # ... and the material is not a wrap-up target either.
        self.assertNotIn(bare_material,
                         set(fixture.cycle.artifact_source.last_closeout_targets))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

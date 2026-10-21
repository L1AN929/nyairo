"""v0.4 私人读写实践 B：真实页内容 → 有限私密请求 → 可选 note/unclear/no_change → 一个 PDI CAS。

本文件证明的是**机制**，不是「她自己读懂了」：
1. 正文只来自真实 owner 的当前页，只进那一次私密内容请求，不进报告 / facts / 日志；
2. note / unclear / no_change 各自合法；一步最多一条批注；``understanding`` 恒 UNKNOWN；
3. 取得、停点、批注在**同一个** PDI 版本（一次 AR-0 CAS）落盘；同意图同键重放不重复，
   换意图换键；
4. 未配 ``reading_response_writer`` 时保持旧的「只记取得+停点」行为，并诚实报
   ``NOT_CONFIGURED``；
5. 响应失败 = 一条都不写（连取得也不写），且不报成成功；
6. 作品 writer 的 ``no_change`` 在 AR-0 路径和直连 port 路径都是合法的提交前空操作：
   不写版本、不报 progress/experience、不自动完成 Activity、不算模型失败；
7. 暂停时根本不问，也不会有私密请求。

明确：决策来自 ``ScriptedCognition``（脚本化替身），私密答复来自 ``StubReadingResponse``，
批注纯计划在多数用例里用**记录型替身**（A 的 ``plan_page_annotation`` 尚未落地时也能证明
B 侧的合成与键绑定）。0 真实模型、0 网络、0 密钥、0 Memory 写入。
需要 POSIX（fcntl + AF_UNIX）；原生 Windows 自动跳过。
"""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

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

import autonomous_life_assembly as assembly  # noqa: E402
import life_artifact_port as lap  # noqa: E402
import model_continuation_writer as mcw  # noqa: E402
import one_day_life as odl  # noqa: E402
import private_daily_items as pdi  # noqa: E402
from test_one_day_life import StubObservation, StubWriter, posix_only  # noqa: E402
from test_subject_reading_cycle import (  # noqa: E402
    READING_V2,
    _USE_INSTANCE_GRANT,
    MATERIAL_SENTINEL,
    ScriptedCognition,
    SubjectReadingCycleTest,
)

#: 模型「写」的那条私密批注里的一句特征串：它只许出现在私密请求和私密文档里。
NOTE_BODY = "这一页让我想起窗外的树，回头想再看一眼。"
NOTE_TITLE = "关于第二页的一句话"
UNCLEAR_NOTE = "这里我没看懂，先记一个问号。"
#: 第 1 页自己的正文特征串（``MATERIAL_SENTINEL`` 在第 2 页，第 1 页的请求里不该出现）。
PAGE1_SENTINEL = "房间里的桌子与窗"


# ---------------------------------------------------------------------------------------------
# 替身：私密内容请求（A 的 ModelReadingResponse 的形状）
# ---------------------------------------------------------------------------------------------


class StubReadingResponse:
    """脚本化私密答复：返回/抛出预先写死的那一个答案，并记录请求本身。

    ``metadata=True`` 时按 A 的**真实形状**作答：把请求里的源元数据（``source_id`` /
    ``source_revision`` / ``page`` / ``observed_at`` / ``prompt_chars`` / ``page_chars`` /
    ``page_text_truncated``）一并回传 —— 真实的 ``ModelReadingResponse.respond`` 就是这么返回的，
    而 A 的纯计划只认 ``kind``/``title``/``body``/``note``，所以 B 侧必须严格筛字段。
    ``on_request`` 用来模拟「私密请求期间材料 / 文档被改动」。
    """

    def __init__(self, answer=None, *, error: BaseException | None = None,
                 metadata: bool = False, on_request=None) -> None:
        self.answer = dict(answer or {"kind": "no_change"})
        self.error = error
        self.metadata = metadata
        self.on_request = on_request
        self.calls: list[dict] = []

    def respond(self, context, *, observed_at: str):
        self.calls.append({"context": dict(context), "observed_at": observed_at})
        if self.on_request is not None:
            self.on_request(dict(context))
        if self.error is not None:
            raise self.error
        answer = dict(self.answer)
        if self.metadata:
            text = str(context.get("text") or "")
            answer.update({
                "source_id": context.get("source_id"),
                "source_revision": context.get("source_revision"),
                "page": context.get("page"),
                "observed_at": observed_at,
                "prompt_chars": len(text),
                "page_chars": len(text),
                "page_text_truncated": False,
            })
        return answer


def _stand_in_planner(document, *, artifact_id, source_id, source_revision, page, response,
                      operation_key, now):
    """A 的纯计划的**记录型替身**：按插件的既有语义造一条 memo / unclear 条目。

    它不冒充 A：真实实现由 A 交，这里只用来证明 B 侧真的把
    （已取得该页的）document / source / 版本 / 页 / 答复 / 键交给纯计划，并把返回的字节
    放进**同一个** CAS。
    """
    calls.append({"document_items": sorted((document.get("items") or {})),
                  "artifact_id": artifact_id, "source_id": source_id,
                  "source_revision": source_revision, "page": page,
                  "response": dict(response), "operation_key": operation_key, "now": now})
    doc = copy.deepcopy(dict(document))
    kind = str(response.get("kind") or "")
    item_id = None
    if kind == "note":
        item_id = "memo:standin-%s-p%s" % (str(source_revision)[:8], page)
        doc.setdefault("items", {})[item_id] = {
            "item_id": item_id, "item_type": pdi.ITEM_MEMO,
            "title": str(response.get("title") or ""),
            "body": str(response.get("body") or ""),
            "created_at": now, "updated_at": now, "revisions": [],
            "reading_anchor": {"source_id": source_id, "source_revision": source_revision,
                               "page": page},
        }
    elif kind == "unclear":
        item_id = "unclear:standin-%s-p%s" % (str(source_revision)[:8], page)
        doc.setdefault("items", {})[item_id] = {
            "item_id": item_id, "item_type": pdi.ITEM_UNCLEAR_MARKER,
            "source_id": source_id, "source_revision": source_revision, "position": page,
            "marked_at": now, "note": str(response.get("note") or ""),
        }
    else:  # no_change: 不增加任何条目
        return {"content": pdi.encode_document(doc), "changed": False, "item_id": None,
                "kind": kind, "operation_key": operation_key}
    order = doc.setdefault("order", [])
    if item_id not in order:
        order.append(item_id)
    return {"content": pdi.encode_document(doc), "changed": True, "item_id": item_id,
            "kind": kind, "operation_key": operation_key}


#: ``_stand_in_planner`` 的记录（每个用例自己清空）。
calls: list[dict] = []


class NoChangeWorkWriter:
    """作品 writer 的显式 no_change 替身（不是失败，是「没什么可写的」）。"""

    def __init__(self) -> None:
        self.calls = 0

    def next_content(self, point, *, observed_at: str) -> str:
        self.calls += 1
        raise mcw.ModelNoChange("nothing to add here")


@posix_only
class SubjectReadingPracticeCycleTest(SubjectReadingCycleTest):
    """真实 Supply owner + 真实 runtime + 脚本化决策 + 明确的内容替身。"""

    # -- 夹具 ---------------------------------------------------------------------------

    def _practice_fixture(self, script, *, response_writer=None, writer=None,
                          data=None, grant_id=_USE_INSTANCE_GRANT, with_ar0=True):
        """(重新)装配同一实例上的完整 runtime + cycle，并注入明确的私密答复替身。"""
        data = data or odl.read_instance(self.instance)
        env = self.instance_env()
        cognition = ScriptedCognition(
            script, reading_artifact_id=data["reading_artifact_id"],
            work_artifact_id=data["work_artifact_id"])
        runtime, port = self._runtime(cognition, env, data, grant_id=grant_id,
                                      with_ar0=with_ar0)
        assembled = assembly.assemble_autonomous_life(
            runtime, environment=env, state_root=odl.life_state_root(self.instance),
            world_provider=StubObservation(env), writer=writer or StubWriter(),
            reading_response_writer=response_writer, enabled=True)
        self.assertEqual(assembled["state"], assembly.READY, assembled)
        return SimpleNamespace(runtime=runtime, port=port, cycle=assembled["cycle"],
                               cognition=cognition, data=data, env=env,
                               assembled=assembled, response=response_writer,
                               writer=writer)

    def _runtime(self, cognition, env, data, *, grant_id=_USE_INSTANCE_GRANT,
                 with_ar0=True):
        """父类的生产装配，外加一个「不接 AR-0 适配器」的变体（走直连 port 路径）。"""
        if with_ar0:
            return super()._runtime(cognition, env, data, grant_id=grant_id)
        import life_integration_ag2 as ag2
        import life_runtime_production as lrp
        import life_artifact_port as lap
        from world_journey_action_adapter import (JourneyWorldResolver,
                                                 RealJourneyWorldActionAdapter)
        state_root = odl.life_state_root(self.instance)
        port = lap.LifeArtifactPort(str(self.services.supply_path), data["subject_id"])
        switches = ag2.LifeRuntimeKillSwitches(
            life_runtime_enabled=True, agency_enabled=True, action_execution_enabled=True,
            proactive_enabled=False)
        runtime = lrp.build_production_life_runtime(
            state_root, kill_switches=switches, cognition_adapter=cognition,
            install_root=self.instance / "life", artifact_action_adapter=None,
            world_action_adapter=RealJourneyWorldActionAdapter(
                JourneyWorldResolver(environment=env)))
        self._runtimes.append(runtime)
        self.addCleanup(runtime.close)
        return runtime, port

    def _annotations(self, document: dict, source_id: str) -> dict:
        items = [item for item in document["items"].values()
                 if str(item.get("item_type")) in {pdi.ITEM_MEMO, pdi.ITEM_UNCLEAR_MARKER}
                 and (str(item.get("source_id")) == source_id
                      or str((item.get("reading_anchor") or {}).get("source_id")) == source_id)]
        return {"note": [i for i in items if i.get("item_type") == pdi.ITEM_MEMO],
                "unclear": [i for i in items
                            if i.get("item_type") == pdi.ITEM_UNCLEAR_MARKER]}

    def _doc_head(self, data: dict) -> dict:
        return self._head(data["private_items_artifact_id"])

    def setUp(self) -> None:
        super().setUp()
        calls.clear()

    # -- 1. 未配答复 writer：旧行为 + NOT_CONFIGURED ------------------------------------

    def test_no_response_writer_keeps_the_old_page_read_and_reports_not_configured(self):
        data = self._prepare()
        fixture = self._practice_fixture(["READ"], data=data)
        self.assertIsNone(fixture.response)
        self.assertEqual(fixture.assembled["reading_response"]["state"], "NOT_CONFIGURED")

        out = fixture.cycle.evaluate_once(trigger_ref="evt:p:plain", act=True)
        self.assertEqual(out["outcome"], "ACTED", out)
        self.assertEqual(out["step_path"], "READING_AR0", out)
        annotation = out["reading"]["annotation"]
        self.assertEqual(annotation["state"], "NOT_CONFIGURED", annotation)
        self.assertIsNone(annotation["kind"])
        self.assertFalse(annotation["applied"])
        # The page read is still real: one acquisition and one stop, and no annotation at all.
        document = self._document(data["private_items_artifact_id"])
        annotations = self._annotations(document, self._reading_source_id(data))
        self.assertEqual((len(annotations["note"]), len(annotations["unclear"])), (0, 0))
        self.assertEqual([p["position"] for p in self._positions(
            document, self._reading_source_id(data))], [1])
        self.assertEqual(out["reading"]["understanding"], "UNKNOWN")
        self.assertEqual(calls, [], "no planner may run when no response writer is wired")
        self.assertNotIn(MATERIAL_SENTINEL, json.dumps(out, ensure_ascii=False, default=str))

    # -- 2. note：取得+停点+批注 = 同一个 PDI 版本 ---------------------------------------

    def test_a_note_response_lands_in_the_same_single_version_as_the_page_read(self):
        data = self._prepare()
        response = StubReadingResponse({"kind": "note", "title": NOTE_TITLE,
                                        "body": NOTE_BODY})
        fixture = self._practice_fixture(["READ"], response_writer=response, data=data)
        self.assertEqual(fixture.assembled["reading_response"]["state"], "READY")
        doc_head_before = self._doc_head(data)
        work_before = self._head(data["work_artifact_id"])["version_id"]

        with mock.patch.object(pdi, "plan_page_annotation", _stand_in_planner, create=True):
            first = fixture.cycle._reading_step_spec(observed_at="2026-10-25T06:00:00+00:00")
            second = fixture.cycle._reading_step_spec(observed_at="2026-10-25T06:00:00+00:00")
            self.assertEqual(first["idempotency_key"], second["idempotency_key"],
                             "one intended read+annotation has exactly one operation key")
            self.assertTrue(str(first["idempotency_key"]).startswith("life:"))
            # ... and a *different* intent on the same page is a different intended effect.
            response.answer = {"kind": "unclear", "note": UNCLEAR_NOTE}
            other = fixture.cycle._reading_step_spec(observed_at="2026-10-25T06:00:00+00:00")
            self.assertNotEqual(first["idempotency_key"], other["idempotency_key"])
            response.answer = {"kind": "note", "title": NOTE_TITLE, "body": NOTE_BODY}

            out = fixture.cycle.evaluate_once(trigger_ref="evt:p:note", act=True)

        self.assertEqual(out["outcome"], "ACTED", out)
        self.assertEqual(out["step_path"], "READING_AR0", out)
        annotation = out["reading"]["annotation"]
        self.assertEqual(annotation["state"], "APPLIED", annotation)
        self.assertEqual(annotation["kind"], "note")
        self.assertTrue(annotation["item_id"], annotation)

        # exactly one document version was minted for read + annotation
        doc_head_after = self._doc_head(data)
        self.assertNotEqual(doc_head_after["version_id"], doc_head_before["version_id"])
        self.assertEqual(doc_head_after["version_number"],
                         int(doc_head_before["version_number"]) + 1,
                         "acquisition, stop and note must share one CAS version")
        document = self._document(data["private_items_artifact_id"])
        source_id = self._reading_source_id(data)
        annotations = self._annotations(document, source_id)
        self.assertEqual(len(annotations["note"]), 1, annotations)
        self.assertEqual(len(annotations["unclear"]), 0, annotations)
        note = annotations["note"][0]
        self.assertEqual(note["body"], NOTE_BODY, "the private note is the model's own text")
        self.assertEqual(note["reading_anchor"]["page"], 1, note["reading_anchor"])
        self.assertEqual(note["reading_anchor"]["source_id"], source_id)
        self.assertEqual([p["position"] for p in self._positions(document, source_id)], [1],
                         "the acquisition and the stop are in that same version")
        self.assertEqual(document["items"][out["reading"]["acquisition_id"]]["item_type"],
                         pdi.ITEM_SOURCE_ACQUISITION)

        # The request carried the real page and the owner's own revision -- and nothing else.
        self.assertEqual(len(response.calls), 4,
                         "one request per planned step (three explicit plans + the round)")
        context = response.calls[-1]["context"]
        self.assertEqual(context["page"], 1)
        self.assertEqual(context["previous_page"], None)
        self.assertEqual(context["total_pages"], 3)
        self.assertEqual(context["source_revision"], self._head(
            data["reading_artifact_id"])["version_id"])
        # page 1's own real text is what went into the request (the sentinel on page 2 did not).
        self.assertIn(PAGE1_SENTINEL, context["text"])
        self.assertNotIn(MATERIAL_SENTINEL, context["text"])
        # the planner saw the document that already acquired this page
        self.assertIn(out["reading"]["acquisition_id"], calls[-1]["document_items"])
        self.assertEqual(calls[-1]["page"], 1)
        self.assertEqual(calls[-1]["response"]["kind"], "note")

        # The private page text / note text never travels into the round report or the facts.
        rendered = json.dumps(out, ensure_ascii=False, default=str)
        self.assertNotIn(MATERIAL_SENTINEL, rendered)
        self.assertNotIn(NOTE_BODY, rendered)
        self.assertNotIn(NOTE_TITLE, rendered)
        self.assertNotIn(NOTE_BODY, json.dumps(fixture.cognition.requests[-1].observed_facts
                                               if hasattr(fixture.cognition.requests[-1],
                                                          "observed_facts") else {},
                                               ensure_ascii=False))
        # ... and reading never becomes the work: the work head is untouched.
        self.assertEqual(self._head(data["work_artifact_id"])["version_id"], work_before)
        # The annotation is visible to the next round's reading view as a real count.
        situation = fixture.cycle.private_material_situation(refresh=True)
        self.assertEqual(situation["annotations"]["note"], 1, situation["annotations"])
        self.assertEqual(situation["understanding"], "UNKNOWN")

    # -- 3. unclear：标记 != 理解 -------------------------------------------------------

    def test_an_unclear_response_is_a_marker_and_never_becomes_understanding(self):
        data = self._prepare()
        response = StubReadingResponse({"kind": "unclear", "note": UNCLEAR_NOTE})
        fixture = self._practice_fixture(["READ"], response_writer=response, data=data)
        with mock.patch.object(pdi, "plan_page_annotation", _stand_in_planner, create=True):
            out = fixture.cycle.evaluate_once(trigger_ref="evt:p:unclear", act=True)

        self.assertEqual(out["outcome"], "ACTED", out)
        self.assertEqual(out["reading"]["annotation"]["kind"], "unclear", out["reading"])
        self.assertEqual(out["reading"]["annotation"]["state"], "APPLIED")
        document = self._document(data["private_items_artifact_id"])
        source_id = self._reading_source_id(data)
        annotations = self._annotations(document, source_id)
        self.assertEqual(len(annotations["unclear"]), 1, annotations)
        self.assertEqual(len(annotations["note"]), 0, annotations)
        self.assertEqual(annotations["unclear"][0]["position"], 1)
        self.assertEqual(annotations["unclear"][0]["note"], UNCLEAR_NOTE)
        positions = self._positions(document, source_id)
        self.assertEqual([p["position"] for p in positions], [1])
        self.assertEqual(positions[0]["understanding"], pdi.UNDERSTANDING_UNKNOWN,
                         "an unclear mark is not comprehension")
        self.assertNotIn(UNCLEAR_NOTE, json.dumps(out, ensure_ascii=False, default=str))

    # -- 4. no_change：照常保存取得与停点，不加批注 -------------------------------------

    def test_a_no_change_response_saves_the_read_and_adds_no_annotation(self):
        data = self._prepare()
        response = StubReadingResponse({"kind": "no_change"})
        fixture = self._practice_fixture(["READ"], response_writer=response, data=data)
        doc_before = self._doc_head(data)
        # No planner stand-in is installed: a no_change must not need one.
        # a no_change must not need a planner at all: nothing is annotated, so nothing is asked
        out = fixture.cycle.evaluate_once(trigger_ref="evt:p:none", act=True)

        self.assertEqual(out["outcome"], "ACTED", out)
        self.assertEqual(out["reading"]["annotation"]["state"], "NO_CHANGE",
                         out["reading"]["annotation"])
        self.assertFalse(out["reading"]["annotation"]["applied"])
        document = self._document(data["private_items_artifact_id"])
        source_id = self._reading_source_id(data)
        self.assertEqual([p["position"] for p in self._positions(document, source_id)], [1],
                         "the read is saved even when nothing was written about it")
        annotations = self._annotations(document, source_id)
        self.assertEqual((len(annotations["note"]), len(annotations["unclear"])), (0, 0))
        self.assertEqual(self._doc_head(data)["version_number"],
                         int(doc_before["version_number"]) + 1)
        self.assertEqual(calls, [], "a no_change never asks the annotator to plan nothing")
        self.assertEqual(out["reading"]["understanding"], "UNKNOWN")

    # -- 5. 响应失败：一条都不写 --------------------------------------------------------

    def test_a_failed_response_writes_nothing_at_all(self):
        data = self._prepare()
        response = StubReadingResponse({"kind": "note"}, error=RuntimeError("MODEL_SAID_NO"))
        fixture = self._practice_fixture(["READ"], response_writer=response, data=data)
        doc_before = self._doc_head(data)
        work_before = self._head(data["work_artifact_id"])["version_id"]
        with mock.patch.object(pdi, "plan_page_annotation", _stand_in_planner, create=True):
            out = fixture.cycle.evaluate_once(trigger_ref="evt:p:fail", act=True)

        self.assertNotEqual(out["outcome"], "ACTED", out)
        self.assertEqual(out["outcome"], "STEP_REFUSED", out)
        self.assertIn("reading response failed", out["detail"])
        self.assertNotIn("MODEL_SAID_NO", json.dumps(out, ensure_ascii=False, default=str),
                         "the response's own words are not carried into the report")
        self.assertEqual(self._doc_head(data)["version_id"], doc_before["version_id"],
                         "a failed response must not even commit the page read")
        self.assertEqual(self._head(data["work_artifact_id"])["version_id"], work_before)
        document = self._document(data["private_items_artifact_id"])
        self.assertEqual(document["items"], {}, "no half step: no acquisition, no note")
        self.assertEqual(len(response.calls), 1, "the request was really made")
        self.assertNotIn(MATERIAL_SENTINEL, json.dumps(out, ensure_ascii=False, default=str))

    # -- 5b. 真实响应形状 / 元数据矛盾 / 空白页 / 请求期间变动 --------------------------

    def test_a_real_response_shape_reaches_the_real_planner_in_one_version(self):
        """A 的真实答复形状（带源元数据）+ A 的真实纯计划：字段被筛净，版本只 +1。"""
        data = self._prepare()
        response = StubReadingResponse({"kind": "note", "title": NOTE_TITLE, "body": NOTE_BODY},
                                       metadata=True)
        fixture = self._practice_fixture(["READ"], response_writer=response, data=data)
        doc_before = self._doc_head(data)
        # 没有替身计划：这是 A 真实的 plan_page_annotation。
        out = fixture.cycle.evaluate_once(trigger_ref="evt:p:real-shape", act=True)

        self.assertEqual(out["outcome"], "ACTED", out)
        self.assertEqual(out["reading"]["annotation"]["state"], "APPLIED", out["reading"])
        self.assertEqual(out["reading"]["annotation"]["kind"], "note")
        document = self._document(data["private_items_artifact_id"])
        source_id = self._reading_source_id(data)
        annotations = self._annotations(document, source_id)
        self.assertEqual(len(annotations["note"]), 1, annotations)
        self.assertEqual(annotations["note"][0]["body"], NOTE_BODY)
        # 请求本身带的是真实页元数据（这正是不许整包转发给纯计划的原因）。
        self.assertEqual(response.calls[-1]["context"]["page"], 1)
        # 一个意图一个版本：合并后的文档必须写 owner 真正的下一个版本号，不是内部 +2。
        expected = int(doc_before["version_number"]) + 1
        self.assertEqual(document["owner_version_number"], expected,
                         "the merged document must carry the owner's own next version number")
        self.assertEqual(document["doc_revision"], expected)
        self.assertEqual(self._doc_head(data)["version_number"], expected)
        self.assertEqual(len(document.get("reading_operations") or {}), 1,
                         document.get("reading_operations"))
        self.assertEqual([p["position"] for p in self._positions(document, source_id)], [1])
        self.assertEqual(out["reading"]["understanding"], "UNKNOWN")
        rendered = json.dumps(out, ensure_ascii=False, default=str)
        self.assertNotIn(NOTE_BODY, rendered)
        self.assertNotIn(MATERIAL_SENTINEL, rendered)

    def test_a_response_that_contradicts_its_page_is_refused_and_writes_nothing(self):
        data = self._prepare()
        response = StubReadingResponse({"kind": "note", "title": NOTE_TITLE, "body": NOTE_BODY,
                                        "source_id": "artifact:not-this-one",
                                        "source_revision": "rev-other", "page": 9})
        fixture = self._practice_fixture(["READ"], response_writer=response, data=data)
        doc_before = self._doc_head(data)
        out = fixture.cycle.evaluate_once(trigger_ref="evt:p:contradiction", act=True)

        self.assertEqual(out["outcome"], "STEP_REFUSED", out)
        self.assertIn("contradicts the page", out["detail"])
        self.assertEqual(len(response.calls), 1, "the request really was made")
        self.assertEqual(self._doc_head(data)["version_id"], doc_before["version_id"],
                         "a contradictory answer must not write the page read either")
        self.assertEqual(self._document(data["private_items_artifact_id"])["items"], {})

    def test_a_blank_page_keeps_the_real_stop_and_skips_the_content_writer(self):
        data = self._prepare(content="   \n%%PAGE%%\n第二页：这一页有字。\n")
        response = StubReadingResponse({"kind": "note", "title": NOTE_TITLE, "body": NOTE_BODY})
        fixture = self._practice_fixture(["READ"], response_writer=response, data=data)
        out = fixture.cycle.evaluate_once(trigger_ref="evt:p:blank", act=True)

        self.assertEqual(out["outcome"], "ACTED", out)
        self.assertEqual(out["reading"]["annotation"]["state"], "SKIPPED_EMPTY",
                         out["reading"])
        self.assertFalse(out["reading"]["annotation"]["applied"])
        self.assertEqual(response.calls, [], "a blank page must not become a content request")
        document = self._document(data["private_items_artifact_id"])
        source_id = self._reading_source_id(data)
        self.assertEqual([p["position"] for p in self._positions(document, source_id)], [1],
                         "the real stop is saved even when the page has nothing to answer about")
        annotations = self._annotations(document, source_id)
        self.assertEqual((len(annotations["note"]), len(annotations["unclear"])), (0, 0))
        self.assertEqual(out["reading"]["understanding"], "UNKNOWN")

    def test_a_material_change_during_the_request_writes_nothing(self):
        data = self._prepare()
        port = lap.LifeArtifactPort(str(self.services.supply_path), data["subject_id"])

        def _replace_material(_context):
            head = port.read_head(data["reading_artifact_id"]) or {}
            port.commit_version(
                artifact_id=data["reading_artifact_id"], expected_head=head.get("version_id"),
                content=READING_V2, grant_id=data["artifact_grant_id"],
                operation_key=lap.operation_key_for("test:material:mid-request",
                                                    str(head.get("version_id"))))

        response = StubReadingResponse({"kind": "note", "title": NOTE_TITLE, "body": NOTE_BODY},
                                       on_request=_replace_material)
        fixture = self._practice_fixture(["READ"], response_writer=response, data=data)
        doc_before = self._doc_head(data)
        out = fixture.cycle.evaluate_once(trigger_ref="evt:p:material-moved", act=True)

        self.assertEqual(out["outcome"], "STEP_REFUSED", out)
        self.assertIn("material changed during the private response", out["detail"])
        self.assertEqual(self._doc_head(data)["version_id"], doc_before["version_id"])
        self.assertEqual(self._document(data["private_items_artifact_id"])["items"], {})

    def test_a_revocation_during_the_request_writes_nothing(self):
        data = self._prepare()
        self.assertTrue(odl.read_page(self.instance, page=1)["ok"])
        port = lap.LifeArtifactPort(str(self.services.supply_path), data["subject_id"])
        source_id = self._reading_source_id(data)
        revision = self._head(data["reading_artifact_id"])["version_id"]
        items = pdi.PrivateDailyItems(port, data["private_items_artifact_id"],
                                      grant_id=data["artifact_grant_id"])

        def _revoke(_context):
            items.mark_source_unavailable(source_id=source_id, source_revision=revision,
                                          reason="operator revoked mid-request")

        response = StubReadingResponse({"kind": "note", "title": NOTE_TITLE, "body": NOTE_BODY},
                                       on_request=_revoke)
        fixture = self._practice_fixture(["READ"], response_writer=response, data=data)
        before = self._document(data["private_items_artifact_id"])
        out = fixture.cycle.evaluate_once(trigger_ref="evt:p:revoked", act=True)

        self.assertEqual(out["outcome"], "STEP_REFUSED", out)
        self.assertIn("revoked during the private response", out["detail"])
        after = self._document(data["private_items_artifact_id"])
        self.assertEqual(sorted(after["items"]), sorted(before["items"]),
                         "the refused round must not add an acquisition or a note")
        self.assertEqual([p["position"] for p in self._positions(after, source_id)], [1])

    # -- 6. 暂停：不问，也不写 ----------------------------------------------------------

    def test_paused_life_never_asks_for_a_private_response(self):
        data = self._prepare()
        response = StubReadingResponse({"kind": "note", "title": NOTE_TITLE, "body": NOTE_BODY})
        fixture = self._practice_fixture(["READ"], response_writer=response, data=data)
        doc_before = self._doc_head(data)
        odl.set_pause(self.instance, True, reason="test")
        try:
            out = fixture.cycle.evaluate_once(trigger_ref="evt:p:paused", act=True)
        finally:
            odl.set_pause(self.instance, False, reason="test")

        self.assertEqual(out["outcome"], "PAUSED_BY_OPERATOR", out)
        self.assertEqual(response.calls, [], "a paused round makes no private request")
        self.assertEqual(fixture.cognition.call_count, 0)
        self.assertEqual(self._doc_head(data)["version_id"], doc_before["version_id"])

    # -- 7. 作品 no_change（AR-0 路径）：提交前就停 --------------------------------

    def test_the_work_writer_no_change_stops_before_the_owner_on_the_ar0_path(self):
        data = self._prepare()
        writer = NoChangeWorkWriter()
        fixture = self._practice_fixture(["WORK"], writer=writer, data=data)
        work_before = self._head(data["work_artifact_id"])["version_id"]
        out = fixture.cycle.evaluate_once(trigger_ref="evt:p:nochange-ar0", act=True)

        self.assertEqual(writer.calls, 1, "the writer really was asked")
        self.assertEqual(out["outcome"], "NO_ACTION", out)
        self.assertEqual(out["work_no_change"]["reason_code"], "NO_CHANGE", out)
        self.assertFalse(out["work_no_change"]["owner_called"])
        self.assertFalse(out["work_no_change"]["version_created"])
        self.assertNotEqual(out["outcome"], "STEP_REFUSED",
                            "a legal no_change is not a model failure")
        self.assertEqual(self._head(data["work_artifact_id"])["version_id"], work_before,
                         "no_change must not write a version")
        self.assertIsNone(out.get("owner_version_id"), out)
        self.assertIsNone(fixture.cycle.last_receipt, "no receipt may be minted for a no-op")
        # the Activity it was about is still ACTIVE: nothing is auto-completed
        view = fixture.runtime.activity_read.get_current_life_view()
        self.assertIsNotNone(view.get("foreground_activity_ref"), view)

    # -- 8. 作品 no_change（直连 port 路径）：同样在提交前停 ------------------------
    #
    # 本轮探针发现的边界（不是 B 本轮引入的改动）：没有 AR-0 适配器时，production runtime
    # 根本不把「续写作品」这个候选提供给决策（`cognition.offered` 里只有两个
    # PERSONAL_OPPORTUNITY），所以 `evaluate_once` 走不到直连分支。这里直接驱动那条分支
    # 自己的两个半边：`_commit_next_version` 是它唯一的 owner 入口，`_work_no_change`
    # 是它唯一的报告出口。

    def _work_point(self, data: dict):
        port = lap.LifeArtifactPort(str(self.services.supply_path), data["subject_id"])
        return port.resume_point(data["work_artifact_id"])

    @staticmethod
    def _direct_base() -> dict:
        """`_commit_next_version` derives its operation key from non-empty activity/correlation
        refs, so the direct-path cases hand it the same shape `evaluate_once` would."""
        return {"activity_ref": "activity:test-direct",
                "correlation_id": "corr:test-direct",
                "decision_id": "decision:test-direct"}

    def test_the_work_writer_no_change_stops_before_the_owner_on_the_direct_path(self):
        data = self._prepare()
        writer = NoChangeWorkWriter()
        fixture = self._practice_fixture(["WORK"], writer=writer, data=data, with_ar0=False)
        self.assertFalse(fixture.cycle._ar0_enabled(),
                         "this case is about the path that never crosses an AR-0 fence")
        work_before = self._head(data["work_artifact_id"])["version_id"]
        point = self._work_point(data)

        with self.assertRaises(mcw.ModelNoChange):
            fixture.cycle._commit_next_version(point, self._direct_base(),
                                              "2026-10-25T06:00:00+00:00")

        self.assertEqual(writer.calls, 1, "the writer really was asked")
        self.assertIsNone(fixture.cycle.last_receipt, "no receipt may be minted for a no-op")
        self.assertEqual(self._head(data["work_artifact_id"])["version_id"], work_before,
                         "no_change must not write a version on the direct path either")
        # ... and the report half is the honest no-op, not a model failure.
        report = fixture.cycle._work_no_change({}, llm_calls=1)
        self.assertEqual(report["outcome"], "NO_ACTION", report)
        self.assertEqual(report["work_no_change"]["reason_code"], "NO_CHANGE")
        self.assertFalse(report["work_no_change"]["owner_called"])
        self.assertFalse(report["work_no_change"]["version_created"])

    def test_a_real_content_failure_on_the_direct_path_is_a_refused_receipt(self):
        data = self._prepare()

        class _FailingWriter:
            """一个真实的失败（不是 no_change）：没有正文，也没有成功。"""

            def __init__(self) -> None:
                self.calls = 0

            def next_content(self, point, *, observed_at: str) -> str:
                self.calls += 1
                raise mcw.ModelContentError("MODEL_EDIT_EMPTY")

        writer = _FailingWriter()
        fixture = self._practice_fixture(["WORK"], writer=writer, data=data, with_ar0=False)
        work_before = self._head(data["work_artifact_id"])["version_id"]
        receipt = fixture.cycle._commit_next_version(self._work_point(data),
                                                    self._direct_base(),
                                                    "2026-10-25T06:00:00+00:00")

        self.assertEqual(writer.calls, 1)
        self.assertEqual(receipt.status, "CONTENT_FAILED", receipt.to_dict())
        self.assertEqual(str(getattr(receipt.effect_state, "value", receipt.effect_state)),
                         "REFUSED", receipt.to_dict())
        self.assertIsNone(receipt.version_id, receipt.to_dict())
        self.assertEqual(self._head(data["work_artifact_id"])["version_id"], work_before,
                         "a failed content request must not mint a version")


def load_tests(loader, tests, pattern):
    """只跑本类自己定义的用例（父类是复用的夹具，不是本次的验收面）。"""
    suite = unittest.TestSuite()
    for name in sorted(SubjectReadingPracticeCycleTest.__dict__):
        if name.startswith("test_"):
            suite.addTest(SubjectReadingPracticeCycleTest(name))
    return suite


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""v0.4 阶段 B：资料库 + 研究工作台服务（只测本轮新增的这一层）。

这个文件测什么
--------------
``components/life/scripts/research_workspace_service.py``：资料列表/检索/导入/详情，
研究创建/列表/详情/续写/关闭，来源的 CURRENT/STALE/UNAVAILABLE，marker 的读与写，
以及给 A 的 ``research_from_env`` / ``parse_research_content`` / ``build_research_content``。

证据分级（照实写）
------------------
* ``FakePort`` 是**内存里的 owner 替身**（复用上一阶段那份冻结语义：CAS、operation_key
  幂等、ARCHIVED 生命周期、UNKNOWN 需先对账）。这里测的是**本服务自己的契约**；真实 Supply
  （socket、Governance 预留、不可变版本）由 Supply 自己的测试与 A 的生活链负责。
* 不启动 socket、不落盘、不读凭据、不碰原实例目录、不删除任何文件。

覆盖：
资料分类（阅读材料只读、私人事项与研究不是资料）、检索、导入与幂等、读取的版本拒绝、
资料 marker 可检测、研究创建先核对来源（读不到就拒）、marker 在第一行且含四项、
正文不冒充"已读过"、续写保留 marker 与引用、CAS 缺失即拒/陈旧 head 是 conflict、
来源更新变 STALE、来源归档变 UNAVAILABLE、关闭是 archive 且正文/版本保留、
关闭后拒绝续写、UNKNOWN 先对账、环境工厂与纯 helper。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(Path(__file__).resolve().parent)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_artifact_port as lap  # noqa: E402
import private_project_service as pps  # noqa: E402
import research_workspace_service as rws  # noqa: E402
from test_v04_private_projects_stage import (  # noqa: E402
    FakePort,
    GRANT,
    ITEMS,
    READING,
    WORKSPACE,
)

PLAIN_WORK = "artifact:old-work"


def service_for(port: Any) -> rws.ResearchWorkspaceService:
    projects = pps.PrivateProjectService(port, WORKSPACE, GRANT,
                                         excluded_artifact_ids=(READING, ITEMS))
    return rws.ResearchWorkspaceService(projects, reading_artifact_id=READING,
                                        private_items_artifact_id=ITEMS)


class ResearchWorkspaceBase(unittest.TestCase):
    def setUp(self) -> None:
        self.port = FakePort()
        self.port.seed(artifact_id=READING, title="她的阅读材料",
                       content="第一页\n%%PAGE%%\n第二页")
        self.port.seed(artifact_id=ITEMS, title="她的私人日常事项", content='{"items":{}}')
        self.reading_head = self.port.seed(artifact_id=PLAIN_WORK, title="她自己的日常记录",
                                           content="旧的一页，已经在那儿了。")
        self.service = service_for(self.port)

    def import_one(self, key: str = "material:1",
                   title: str = "潮汐表（他抄的）",
                   text: str = "潮汐每天两次。\n第二行。") -> dict[str, Any]:
        return self.service.import_material(title=title, text=text,
                                            source_kind="user_supplied",
                                            origin="他手抄给我的一张纸",
                                            operation_key=key)


class MaterialLibraryTest(ResearchWorkspaceBase):
    def test_list_keeps_reading_material_read_only_and_skips_non_materials(self) -> None:
        answer = self.service.list_materials()
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["status"], "ok")
        rows = answer["data"]["materials"]
        self.assertEqual([row["artifact_id"] for row in rows], [READING])
        self.assertTrue(rows[0]["read_only"])
        self.assertEqual(rows[0]["source_kind"], "reading_material")
        self.assertEqual(rows[0]["source_revision"], "version-artifact-reading-1")
        # 私人事项与"普通作品"都不是资料；列表照实说跳过了什么。
        self.assertEqual(answer["data"]["skipped"]["private_items"], 1)
        self.assertGreaterEqual(answer["data"]["skipped"]["other"], 1)
        self.assertEqual(self.port.calls[-1][0], "read_artifact")  # 纯读，没有任何写

    def test_import_is_a_new_artifact_and_never_overwrites_the_reading_material(self) -> None:
        imported = self.import_one()
        self.assertTrue(imported["ok"])
        self.assertEqual(imported["status"], "applied")
        material = imported["data"]["material"]
        self.assertTrue(material["artifact_id"])
        self.assertNotEqual(material["artifact_id"], READING)
        self.assertEqual(material["source_kind"], "user_supplied")
        self.assertEqual(material["origin"], "他手抄给我的一张纸")
        self.assertEqual(material["source_revision"], "version-%s-1"
                         % material["artifact_id"].split(":", 1)[-1])
        # 既有阅读材料一个字都没动。
        reading = self.service.read_material(READING)
        self.assertTrue(reading["ok"])
        self.assertEqual(reading["data"]["material"]["text"], "第一页\n%%PAGE%%\n第二页")
        self.assertEqual(self.port.artifacts[READING]["head_version_id"],
                         "version-artifact-reading-1")

    def test_import_replay_reuses_the_same_artifact(self) -> None:
        first = self.import_one()
        replay = self.import_one()
        self.assertEqual(replay["data"]["material"]["artifact_id"],
                         first["data"]["material"]["artifact_id"])
        second = self.import_one(key="material:2", title="另一张纸")
        self.assertNotEqual(second["data"]["material"]["artifact_id"],
                            first["data"]["material"]["artifact_id"])
        ids = [row["artifact_id"] for row in self.service.list_materials()["data"]["materials"]]
        self.assertEqual(len(ids), 3)  # 阅读材料 + 两张纸

    def test_search_matches_title_body_and_origin(self) -> None:
        imported = self.import_one()
        target = imported["data"]["material"]["artifact_id"]
        for query in ("潮汐", "第二行", "手抄", "他抄的"):
            found = self.service.list_materials(query=query)
            self.assertEqual([row["artifact_id"] for row in found["data"]["materials"]],
                             [target], query)
        nothing = self.service.list_materials(query="完全不存在的东西")
        self.assertTrue(nothing["ok"])
        self.assertEqual(nothing["data"]["materials"], [])
        self.assertEqual(nothing["data"]["count"], 0)

    def test_read_material_returns_text_and_refuses_a_stale_expected_revision(self) -> None:
        imported = self.import_one()
        target = imported["data"]["material"]["artifact_id"]
        revision = imported["data"]["material"]["source_revision"]
        detail = self.service.read_material(target)
        self.assertTrue(detail["ok"])
        self.assertEqual(detail["data"]["material"]["text"], "潮汐每天两次。\n第二行。")
        self.assertEqual(detail["data"]["material"]["availability"], "AVAILABLE")
        stale = self.service.read_material(target, expected_revision="version-moved-on")
        self.assertFalse(stale["ok"])
        self.assertEqual(stale["status"], "conflict")
        self.assertEqual(stale["reason_code"], "MATERIAL_REVISION_MISMATCH")
        self.assertEqual(stale["data"]["source_revision"], revision)
        # 用 artifact revision 整数核对也一样。
        ok = self.service.read_material(target, expected_revision=1)
        self.assertTrue(ok["ok"])
        wrong = self.service.read_material(target, expected_revision=9)
        self.assertEqual(wrong["status"], "conflict")

    def test_research_and_plain_work_are_not_materials(self) -> None:
        research = self.service.create_research(
            question="这是什么意思", source_refs=[READING],
            operation_key="research:not-a-material")
        target = research["data"]["project"]["artifact_id"]
        answer = self.service.read_material(target)
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason_code"], "ARTIFACT_IS_A_RESEARCH")
        plain = self.service.read_material(PLAIN_WORK)
        self.assertFalse(plain["ok"])
        self.assertEqual(plain["reason_code"], "NOT_A_MATERIAL")
        ids = [row["artifact_id"] for row in self.service.list_materials()["data"]["materials"]]
        self.assertNotIn(target, ids)
        self.assertNotIn(PLAIN_WORK, ids)

    def test_import_contract_is_explicit_and_bounded(self) -> None:
        with self.assertRaises(rws.ResearchContractRefused):
            self.service.import_material(title=" ", text="有正文", source_kind="user_supplied",
                                         origin="来源", operation_key="k")
        with self.assertRaises(rws.ResearchContractRefused):
            self.service.import_material(title="标题", text="   ", source_kind="user_supplied",
                                         origin="来源", operation_key="k")
        with self.assertRaises(rws.ResearchContractRefused):
            self.service.import_material(title="标题", text="正文", source_kind="web_scrape",
                                         origin="来源", operation_key="k")
        with self.assertRaises(rws.ResearchContractRefused):
            self.service.import_material(title="标题", text="正文", source_kind="user_supplied",
                                         origin=" ", operation_key="k")
        with self.assertRaises(rws.ResearchContractRefused):
            self.service.import_material(title="标题", text="正文", source_kind="fictional",
                                         origin="来源", operation_key=" ")
        # 一次都不该落到 owner 的创建上。
        self.assertEqual([name for name, _payload in self.port.calls
                          if name == "create_artifact"], [])


class ResearchWorkflowTest(ResearchWorkspaceBase):
    def setUp(self) -> None:
        super().setUp()
        imported = self.import_one()
        self.material_id = imported["data"]["material"]["artifact_id"]
        self.material_revision = imported["data"]["material"]["source_revision"]

    def create(self, key: str = "research:1", **kwargs: Any) -> dict[str, Any]:
        refs = kwargs.pop("source_refs", [{"artifact_id": self.material_id,
                                           "source_revision": self.material_revision}])
        return self.service.create_research(question="潮汐为什么每天两次？", source_refs=refs,
                                            scope="只讨论这张手抄纸上的内容",
                                            operation_key=key, **kwargs)

    def test_create_checks_sources_and_writes_a_marker_on_the_first_line(self) -> None:
        created = self.create()
        self.assertTrue(created["ok"], created)
        self.assertEqual(created["status"], "applied")
        project = created["data"]["project"]
        self.assertEqual(project["question"], "潮汐为什么每天两次？")
        self.assertEqual(project["source_refs"][0]["artifact_id"], self.material_id)
        self.assertEqual(project["source_refs"][0]["source_revision"], self.material_revision)
        self.assertEqual(project["uncertainty"], rws.DEFAULT_UNCERTAINTY)
        self.assertTrue(project["not_memory"])
        self.assertFalse(project["stable_belief"])
        checks = project["source_checks"]
        self.assertEqual([row["status"] for row in checks], ["CURRENT"])

        content = project["content"]
        self.assertTrue(content.startswith("<!-- nyairo-research:v1 "), content[:60])
        self.assertTrue(content.splitlines()[0].endswith(" -->"))
        first = content.splitlines()[0]
        self.assertNotEqual(content.strip().splitlines()[-1][:4], "<!--")
        parsed = rws.parse_research_content(content)
        self.assertTrue(parsed["marker_present"])
        self.assertEqual(parsed["question"], "潮汐为什么每天两次？")
        self.assertEqual(parsed["scope"], "只讨论这张手抄纸上的内容")
        self.assertEqual(parsed["uncertainty"], rws.DEFAULT_UNCERTAINTY)
        self.assertEqual(len(parsed["source_refs"]), 1)
        # 正文是暂定笔记：不假装已经读过页面。
        self.assertIn("## 暂定发现", parsed["body"])
        self.assertIn("还没有", parsed["body"])
        self.assertIn("不是「已经读完」的证明", parsed["body"])
        self.assertFalse(rws.is_material_content(content))
        self.assertTrue(first.startswith("<!-- nyairo-research:v1 {"))

    def test_create_refuses_a_source_it_cannot_read(self) -> None:
        answer = self.service.create_research(
            question="读不到的来源", source_refs=[{"artifact_id": "artifact:does-not-exist",
                                                   "source_revision": "v1"}],
            operation_key="research:missing")
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["status"], "rejected")
        self.assertEqual(answer["reason_code"], "RESEARCH_SOURCE_UNAVAILABLE")
        self.assertEqual(answer["data"]["source_checks"][0]["status"], "UNAVAILABLE")
        # 没有建出任何工件。
        self.assertEqual(self.service.list_research()["data"]["projects"], [])

    def test_create_refuses_a_source_that_is_not_a_material(self) -> None:
        answer = self.service.create_research(question="作品不是资料",
                                              source_refs=[PLAIN_WORK],
                                              operation_key="research:plain")
        self.assertEqual(answer["status"], "rejected")
        self.assertEqual(answer["data"]["source_checks"][0]["reason_code"],
                         "SOURCE_NOT_A_MATERIAL")

    def test_update_preserves_marker_refs_and_needs_a_head(self) -> None:
        created = self.create()
        project = created["data"]["project"]
        target, head = project["artifact_id"], project["head_version_id"]
        with self.assertRaises(rws.ResearchContractRefused):
            self.service.update_research(target, content="新的一版", expected_head="",
                                         operation_key="research:update:no-head")
        stale = self.service.update_research(target, content="新的一版",
                                             expected_head="version-moved-on",
                                             operation_key="research:update:stale")
        self.assertEqual(stale["status"], "conflict")

        updated = self.service.update_research(
            target, content="## 暂定发现\n两次潮差大概一样。", expected_head=head,
            operation_key="research:update:1")
        self.assertTrue(updated["ok"], updated)
        self.assertEqual(updated["status"], "applied")
        self.assertEqual(updated["data"]["version_number"], 2)
        new_project = updated["data"]["project"]
        self.assertTrue(new_project["content"].startswith("<!-- nyairo-research:v1 "))
        parsed = rws.parse_research_content(new_project["content"])
        self.assertEqual(parsed["source_refs"], project["source_refs"])
        self.assertEqual(parsed["question"], project["question"])
        self.assertIn("两次潮差大概一样。", parsed["body"])

        # 连 marker 一起交上来也照样被规范化（正文照旧、引用照旧）。
        with_marker = new_project["content"] + "\n补一行。"
        again = self.service.update_research(
            target, content=with_marker, expected_head=new_project["head_version_id"],
            operation_key="research:update:2", scope="新的范围", uncertainty="仍然很初步")
        self.assertTrue(again["ok"], again)
        matched = rws.parse_research_content(again["data"]["project"]["content"])
        self.assertEqual(matched["scope"], "新的范围")
        self.assertEqual(matched["uncertainty"], "仍然很初步")
        self.assertTrue(matched["body"].rstrip().endswith("补一行。"))

    def test_source_update_marks_the_reference_stale(self) -> None:
        created = self.create()
        target = created["data"]["project"]["artifact_id"]
        self.assertTrue(self.service.read_research(target)["data"]["project"]["sources_current"])
        updated_material = rws.build_material_content(
            text="潮汐每天两次。\n他后来又补了一行。", source_kind="user_supplied",
            origin="他手抄给我的一张纸", title="潮汐表（他抄的）")
        self.port.commit_version(artifact_id=self.material_id,
                                 expected_head=self.material_revision,
                                 content=updated_material, grant_id=GRANT,
                                 operation_key="material:update:1")
        read = self.service.read_research(target)
        checks = read["data"]["project"]["source_checks"]
        self.assertEqual([row["status"] for row in checks], ["STALE"])
        self.assertFalse(read["data"]["project"]["sources_current"])
        # 旧结果没有被说成"已经重新核实"。
        self.assertNotEqual(checks[0]["current_source_revision"],
                            created["data"]["project"]["source_refs"][0]["source_revision"])

    def test_archived_material_becomes_unavailable(self) -> None:
        created = self.create()
        target = created["data"]["project"]["artifact_id"]
        self.port.archive_artifact(artifact_id=self.material_id, expected_revision=1,
                                   grant_id=GRANT, operation_key="material:archive:1")
        read = self.service.read_research(target)
        checks = read["data"]["project"]["source_checks"]
        self.assertEqual([row["status"] for row in checks], ["UNAVAILABLE"])
        self.assertEqual(checks[0]["reason_code"], "SOURCE_ARCHIVED")
        # 已撤回的来源不能再启动新研究。
        refused = self.service.create_research(
            question="再试一次", source_refs=[{"artifact_id": self.material_id,
                                               "source_revision": self.material_revision}],
            operation_key="research:again")
        self.assertEqual(refused["reason_code"], "RESEARCH_SOURCE_UNAVAILABLE")

    def test_close_archives_and_keeps_every_version(self) -> None:
        created = self.create()
        project = created["data"]["project"]
        target = project["artifact_id"]
        updated = self.service.update_research(target, content="第二版正文",
                                               expected_head=project["head_version_id"],
                                               operation_key="research:update:1")
        head = updated["data"]["project"]["head_version_id"]
        closed = self.service.close_research(target, expected_revision=2,
                                            operation_key="research:close:1")
        self.assertTrue(closed["ok"], closed)
        self.assertEqual(closed["data"]["lifecycle"], "ARCHIVED")
        self.assertTrue(closed["data"]["content_kept"])
        self.assertTrue(closed["data"]["versions_kept"])
        after = self.service.read_research(target)
        self.assertEqual(after["data"]["project"]["status"], "archived")
        self.assertEqual(after["data"]["project"]["head_version_id"], head)
        self.assertIn("第二版正文", after["data"]["project"]["content"])
        self.assertEqual(len(self.port.versions[target]), 2)  # 版本一个都没少
        # 关闭后不再续写（但内容都还在）。
        refused = self.service.update_research(target, content="再来一版",
                                               expected_head=head,
                                               operation_key="research:update:2")
        self.assertEqual(refused["status"], "rejected")
        self.assertEqual(refused["reason_code"], "RESEARCH_ARCHIVED")

    def test_list_research_lists_only_research_without_body(self) -> None:
        created = self.create()
        listed = self.service.list_research()
        rows = listed["data"]["projects"]
        self.assertEqual([row["artifact_id"] for row in rows],
                         [created["data"]["project"]["artifact_id"]])
        self.assertNotIn("content", rows[0])
        self.assertNotIn("body", rows[0])
        self.assertEqual(rows[0]["source_count"], 1)
        self.assertEqual(rows[0]["status"], "active")
        self.assertFalse(rows[0]["stable_belief"])

    def test_unknown_effect_is_reconciled_before_any_retry(self) -> None:
        self.port.unknown_next = True
        self.port.forward_on_unknown = True
        self.port.resolve_to = lap.EffectState.APPLIED
        created = self.create(key="research:unknown")
        self.assertTrue(created["ok"])
        self.assertTrue(created["data"]["reconciled"])
        self.assertIn(("resolve_operation", {"operation_key": "research:unknown"}),
                      self.port.calls)


class HelperTest(unittest.TestCase):
    def test_build_and_parse_roundtrip(self) -> None:
        content = rws.build_research_content(
            question="风从哪里来", source_refs=[{"artifact_id": "artifact:a" * 1,
                                                 "source_revision": "v1"}],
            scope="只在这一页", uncertainty="暂定", body="## 暂定发现\n还没有。",
            title="研究：风")
        self.assertTrue(content.startswith("<!-- nyairo-research:v1 {"))
        self.assertTrue(content.splitlines()[0].endswith(" -->"))
        parsed = rws.parse_research_content(content)
        self.assertTrue(parsed["marker_present"])
        self.assertEqual(parsed["question"], "风从哪里来")
        self.assertEqual(parsed["source_refs"],
                         [{"artifact_id": "artifact:a", "source_revision": "v1"}])
        self.assertEqual(parsed["scope"], "只在这一页")
        self.assertEqual(parsed["uncertainty"], "暂定")
        self.assertEqual(parsed["body"], "## 暂定发现\n还没有。")
        self.assertTrue(rws.is_research_content(content))
        self.assertFalse(rws.is_material_content(content))

    def test_parse_is_honest_about_what_is_not_a_research(self) -> None:
        parsed = rws.parse_research_content("只是一段普通正文。")
        self.assertFalse(parsed["marker_present"])
        self.assertEqual(parsed["body"], "只是一段普通正文。")
        self.assertFalse(rws.is_research_content("只是一段普通正文。"))
        broken_content = "<!-- nyairo-research:v1 {not json} -->\n\n正文"
        broken = rws.parse_research_content(broken_content)
        # 标记行在那儿但 JSON 坏了：照实说它坏了，不冒充"这不是研究"，也不当成合法研究。
        self.assertTrue(broken["marker_present"])
        self.assertEqual(broken["parse_error"], "MARKER_JSON_INVALID")
        self.assertFalse(rws.is_research_content(broken_content))

    def test_material_marker_is_public_and_detectable(self) -> None:
        content = rws.build_material_content(text="第一行\n第二行", source_kind="fictional",
                                             origin="他明确说是虚构的", title="虚构的纸")
        self.assertTrue(content.startswith("<!-- nyairo-material:v1 "))
        self.assertTrue(rws.is_material_content(content))
        self.assertFalse(rws.is_research_content(content))
        parsed = rws.parse_material_content(content)
        self.assertEqual(parsed["source_kind"], "fictional")
        self.assertEqual(parsed["origin"], "他明确说是虚构的")
        self.assertEqual(parsed["body"], "第一行\n第二行")
        self.assertIsNone(rws.parse_material_content("普通正文"))


class EnvironmentFactoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.port = FakePort()
        self.port.seed(artifact_id=READING, title="材料", content="p1")
        self.port.seed(artifact_id=ITEMS, title="事项", content="{}")
        self.env = {
            "NYAIRO_LIFE_WORKSPACE_ID": WORKSPACE,
            "NYAIRO_ARTIFACT_GRANT": GRANT,
            "NYAIRO_LIFE_SUBJECT": "chiyo",
            "NYAIRO_READING_ARTIFACT_ID": READING,
            "NYAIRO_PRIVATE_ITEMS_ARTIFACT_ID": ITEMS,
        }

    def test_env_factory_builds_a_service(self) -> None:
        built = rws.research_from_env(self.port, self.env)
        self.assertIsInstance(built, rws.ResearchWorkspaceService)
        self.assertEqual(built.reading_artifact_id, READING)
        self.assertEqual(built.private_items_artifact_id, ITEMS)
        self.assertEqual(built.workspace_id, WORKSPACE)
        self.assertEqual(built.projects.excluded_artifact_ids, tuple(sorted([ITEMS, READING])))

    def test_env_factory_returns_none_when_half_wired(self) -> None:
        self.assertIsNone(rws.research_from_env(self.port, {}))
        self.assertIsNone(rws.research_from_env(
            self.port, {"NYAIRO_LIFE_WORKSPACE_ID": WORKSPACE}))
        self.assertIsNone(rws.research_from_env(
            self.port, {"NYAIRO_LIFE_WORKSPACE_ID": WORKSPACE,
                        "NYAIRO_ARTIFACT_GRANT": GRANT}))
        self.assertIsNone(rws.research_from_env(None, {}))

    def test_service_refuses_a_missing_project_service(self) -> None:
        with self.assertRaises(rws.ResearchUnavailable):
            rws.ResearchWorkspaceService(None)
        with self.assertRaises(rws.ResearchUnavailable):
            rws.ResearchWorkspaceService(object())


if __name__ == "__main__":
    unittest.main()

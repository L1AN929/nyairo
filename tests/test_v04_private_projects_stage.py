#!/usr/bin/env python3
"""v0.4 阶段 B：私人多项目 owner 服务 + 提案（只测本轮新增的这一层）。

这个文件测什么
--------------
``components/life/scripts/private_project_service.py`` 把「她自己的作品」直接当成现有
ManagedArtifact 来用：列项目 / 读详情（含版本历史）/ 创建 / 提交新版本 / 归档，全部经一个
窄 port。``private_project_cognition.py`` 只形成**纯结构**提案，绝不写 owner。

证据分级（照实写）
------------------
* ``FakePort`` 是**内存里的 owner 替身**，按证据里冻结的语义回答（CAS、operation_key 幂等、
  ARCHIVED 生命周期、UNKNOWN 需先对账）。这里测的是**本服务自己的契约**：状态映射、CASE 检查、
  拒绝路径、给 A 的适配器形状。真实的 Supply owner（socket、Governance 预留、不可变版本）由
  Supply 自己的测试与 A 的生活链负责，本文件不启动 socket、不落盘、不碰任何实例数据。
* 不读凭据、不跑真实模型、不碰原实例目录、不删除任何文件。

覆盖：
项目/材料的分类（reading、PDI 被排除且仍可诚实拒绝）、列表不含正文、详情带 head 与历史、
历史不完整时照实说（``PORT_LACKS_LIST_VERSIONS``）、port 能列版本时 ``complete=True``、
创建必须显式标题+正文（提案不能自动变成项目）、operation_key 幂等、CAS 缺失即拒、
陈旧 head 是 conflict、归档保留正文且可再读、UNKNOWN 先对账不重发、
提案边界与 A 的 ``ProjectOwnerGateway`` 适配器。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_artifact_port as lap  # noqa: E402
import private_project_cognition as ppc  # noqa: E402
import private_project_service as pps  # noqa: E402

SUBJECT = "chiyo"
WORKSPACE = "ws-private-projects"
GRANT = "grant-artifact-1"
READING = "artifact-reading"
ITEMS = "artifact-items"


def _outcome(state: str, **kwargs: Any) -> lap.ArtifactOutcome:
    base = dict(effect_state=state, status="OK")
    base.update(kwargs)
    return lap.ArtifactOutcome(**base)


class FakePort:
    """An in-memory owner with the frozen semantics this service depends on.

    Not a claim about the real Supply service: it answers only what
    ``private_project_service`` is allowed to rely on (CAS on head, idempotency on the operation
    key, an ARCHIVED lifecycle, and an UNKNOWN that must be reconciled before any retry).
    """

    def __init__(self) -> None:
        self.subject_id = SUBJECT
        self.artifacts: dict[str, dict[str, Any]] = {}
        self.versions: dict[str, list[dict[str, Any]]] = {}
        self.operations: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._serial = 0
        self.conflict_next = False
        self.unknown_next = False
        self.resolve_to = lap.EffectState.APPLIED
        self.forward_on_unknown = False
        self.rights: bool = True

    # -- helpers -----------------------------------------------------------------------
    def _id(self, prefix: str) -> str:
        self._serial += 1
        # 真实的 ManagedArtifact id 是 ``artifact:<32 hex>``：替身也用同一形状，
        # 这样 A 的 display ref（去掉前缀的十六进制）也能被服务认回来。
        return "%s%032x" % (prefix, self._serial)

    def seed(self, *, artifact_id: str, title: str, content: str, lifecycle: str = "ACTIVE",
             revision: Optional[int] = None) -> str:
        version_id = "version-%s-1" % artifact_id.split(":", 1)[-1]
        self.artifacts[artifact_id] = {
            "artifact_id": artifact_id, "subject_id": SUBJECT, "workspace_id": WORKSPACE,
            "title": title, "lifecycle": lifecycle, "head_version_id": version_id,
            "created_at": 1760000000.0, "updated_at": 1760000100.0,
            "revision": 1 if revision is None else int(revision),
        }
        self.versions[artifact_id] = [{
            "version_id": version_id, "version_number": 1, "parent_version_id": None,
            "content": content, "created_at": 1760000000.0, "operation_key": "seed:%s" % artifact_id,
        }]
        return version_id

    def _head(self, artifact_id: str) -> Optional[dict[str, Any]]:
        row = self.artifacts.get(artifact_id)
        if not row or not row.get("head_version_id"):
            return None
        for version in self.versions.get(artifact_id, []):
            if version["version_id"] == row["head_version_id"]:
                return version
        return None

    # -- port surface ------------------------------------------------------------------
    def list_artifacts(self) -> list[dict[str, Any]]:
        self.calls.append(("list_artifacts", {}))
        return [dict(row) for row in self.artifacts.values()]

    def read_artifact(self, artifact_id: str) -> dict[str, Any]:
        self.calls.append(("read_artifact", {"artifact_id": artifact_id}))
        row = self.artifacts.get(artifact_id)
        if row is None:
            raise lap.ArtifactOwnerRefused("NOT_FOUND", "ARTIFACT_NOT_FOUND", "no such artifact")
        head = self._head(artifact_id)
        return {"artifact": dict(row), "head": dict(head) if head else None,
                "content": None if head is None else head["content"]}

    def create_artifact(self, *, workspace_id: str, title: str, content: str, grant_id: str,
                        operation_key: str) -> lap.ArtifactOutcome:
        self.calls.append(("create_artifact", {"workspace_id": workspace_id, "title": title,
                                               "content": content, "operation_key": operation_key}))
        if operation_key in self.operations:
            replay = self.operations[operation_key]
            return _outcome(lap.EffectState.APPLIED, artifact_id=replay["artifact_id"],
                            version_id=replay["version_id"], version_number=1)
        artifact_id = self._id("artifact:")
        version_id = self.seed(artifact_id=artifact_id, title=title, content=content)
        self.operations[operation_key] = {"artifact_id": artifact_id, "version_id": version_id}
        if self.unknown_next:
            self.unknown_next = False
            if not self.forward_on_unknown:
                self.artifacts.pop(artifact_id, None)
                self.versions.pop(artifact_id, None)
            return _outcome(lap.EffectState.UNKNOWN, status="UNKNOWN")
        return _outcome(lap.EffectState.APPLIED, artifact_id=artifact_id, version_id=version_id,
                        version_number=1)

    def commit_version(self, *, artifact_id: str, expected_head: Optional[str], content: str,
                       grant_id: str, operation_key: str) -> lap.ArtifactOutcome:
        self.calls.append(("commit_version", {"artifact_id": artifact_id,
                                              "expected_head": expected_head,
                                              "operation_key": operation_key}))
        row = self.artifacts.get(artifact_id)
        if row is None:
            return _outcome(lap.EffectState.NOT_FOUND, status="NOT_FOUND",
                            reason="ARTIFACT_NOT_FOUND")
        if operation_key in self.operations:
            replay = self.operations[operation_key]
            return _outcome(lap.EffectState.APPLIED, artifact_id=artifact_id,
                            version_id=replay["version_id"],
                            version_number=replay["version_number"])
        if self.conflict_next or (expected_head is not None
                                 and expected_head != row.get("head_version_id")):
            self.conflict_next = False
            return _outcome(lap.EffectState.CONFLICT, status="CONFLICT",
                            detail="stale expected_head")
        if self.unknown_next:
            # 答复丢了，而且这次真的没有落盘：owner 事后只能回答 NOT_FOUND。
            self.unknown_next = False
            return _outcome(lap.EffectState.UNKNOWN, status="UNKNOWN")
        versions = self.versions[artifact_id]
        number = len(versions) + 1
        version_id = "version-%s-%d" % (artifact_id.split(":", 1)[-1], number)
        versions.append({"version_id": version_id, "version_number": number,
                         "parent_version_id": row.get("head_version_id"), "content": content,
                         "created_at": 1760000200.0, "operation_key": operation_key})
        row["head_version_id"] = version_id
        row["revision"] = int(row.get("revision") or 1) + 1
        self.operations[operation_key] = {"artifact_id": artifact_id, "version_id": version_id,
                                          "version_number": number}
        if self.unknown_next:
            self.unknown_next = False
            return _outcome(lap.EffectState.UNKNOWN, status="UNKNOWN")
        return _outcome(lap.EffectState.APPLIED, artifact_id=artifact_id, version_id=version_id,
                        version_number=number, parent_version_id=versions[-2]["version_id"])

    def archive_artifact(self, *, artifact_id: str, expected_revision: int, grant_id: str,
                         operation_key: str) -> lap.ArtifactOutcome:
        self.calls.append(("archive_artifact", {"artifact_id": artifact_id,
                                                "expected_revision": expected_revision,
                                                "operation_key": operation_key}))
        row = self.artifacts.get(artifact_id)
        if row is None:
            return _outcome(lap.EffectState.NOT_FOUND, status="NOT_FOUND")
        if operation_key in self.operations:
            return _outcome(lap.EffectState.APPLIED, artifact_id=artifact_id,
                            lifecycle=row.get("lifecycle"), revision=row.get("revision"))
        if row.get("lifecycle") == "ARCHIVED":
            return _outcome(lap.EffectState.NO_CHANGE, status="NO_CHANGE")
        if int(expected_revision) != int(row.get("revision") or 0):
            return _outcome(lap.EffectState.CONFLICT, status="CONFLICT")
        row["lifecycle"] = "ARCHIVED"
        row["revision"] = int(row.get("revision") or 0) + 1
        self.operations[operation_key] = {"artifact_id": artifact_id, "version_id": None}
        return _outcome(lap.EffectState.APPLIED, artifact_id=artifact_id, lifecycle="ARCHIVED",
                        revision=row["revision"])

    def resolve_operation(self, operation_key: str) -> lap.ArtifactOutcome:
        self.calls.append(("resolve_operation", {"operation_key": operation_key}))
        if self.resolve_to == lap.EffectState.APPLIED:
            row = self.operations.get(operation_key) or {}
            return _outcome(lap.EffectState.APPLIED, artifact_id=row.get("artifact_id"),
                            version_id=row.get("version_id"))
        return _outcome(lap.EffectState.NO_CHANGE, status="NOT_FOUND")


class VersionListingPort(FakePort):
    """A port that also exposes the optional ``list_versions`` capability."""

    def list_versions(self, artifact_id: str) -> list[dict[str, Any]]:
        self.calls.append(("list_versions", {"artifact_id": artifact_id}))
        return [dict(row) for row in self.versions.get(artifact_id, [])]


def service_for(port: Any, **kwargs: Any) -> pps.PrivateProjectService:
    return pps.PrivateProjectService(port, WORKSPACE, GRANT,
                                     excluded_artifact_ids=(READING, ITEMS), **kwargs)


class PrivateProjectServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.port = FakePort()
        self.reading_head = self.port.seed(artifact_id=READING, title="她的阅读材料",
                                           content="第一页\n%%PAGE%%\n第二页")
        self.port.seed(artifact_id=ITEMS, title="她的私人日常事项", content="{\"items\":{}}")
        self.existing = "artifact:old-work"
        self.port.seed(artifact_id=self.existing, title="她自己的日常记录",
                       content="旧的一页，已经在那儿了。")
        self.service = service_for(self.port)

    def test_list_excludes_materials_and_keeps_old_work_visible(self) -> None:
        answer = self.service.list_projects()
        self.assertTrue(answer["ok"])
        ids = [row["artifact_id"] for row in answer["projects"]]
        self.assertEqual(ids, [self.existing])
        self.assertNotIn(READING, ids)
        self.assertNotIn(ITEMS, ids)
        self.assertEqual(answer["counts"]["total"], 1)
        self.assertEqual(answer["counts"]["active"], 1)
        self.assertEqual(answer["counts"]["excluded"], 2)
        self.assertFalse(answer["private_text_included"])
        self.assertNotIn("content", answer["projects"][0])
        # 旧数据继续可见：没有迁移、没有重建、没有给旧标题打标记。
        self.assertEqual(answer["projects"][0]["title"], "她自己的日常记录")

    def test_read_project_returns_text_head_and_honest_history(self) -> None:
        answer = self.service.read_project(self.existing)
        self.assertTrue(answer["ok"])
        project = answer["project"]
        self.assertEqual(project["content"], "旧的一页，已经在那儿了。")
        self.assertEqual(project["head_version_id"], "version-old-work-1")
        self.assertEqual(project["version_number"], 1)
        history = answer["history"]
        self.assertFalse(history["complete"])
        self.assertEqual(history["reason_code"], pps.REASON_PORT_LACKS_LIST_VERSIONS)
        self.assertEqual([row["version_id"] for row in history["entries"]],
                         ["version-old-work-1"])
        self.assertTrue(history["entries"][0]["is_head"])

    def test_reading_material_is_refused_as_a_project(self) -> None:
        answer = self.service.read_project(READING)
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["status"], pps.STATUS_NOT_FOUND)
        self.assertEqual(answer["reason_code"], pps.REASON_ARTIFACT_EXCLUDED)

    def test_history_is_complete_when_the_port_can_list_versions(self) -> None:
        port = VersionListingPort()
        port.seed(artifact_id=self.existing, title="她自己的日常记录", content="v1")
        port.commit_version(artifact_id=self.existing, expected_head="version-old-work-1",
                            content="v2", grant_id=GRANT, operation_key="seed-commit")
        service = service_for(port)
        answer = service.read_project_history(self.existing)
        self.assertTrue(answer["ok"])
        self.assertTrue(answer["complete"])
        self.assertEqual(answer["reason_code"], "")
        self.assertEqual([row["version_number"] for row in answer["entries"]], [2, 1])
        self.assertTrue(answer["entries"][0]["is_head"])

    def test_create_requires_explicit_title_and_text_and_is_idempotent(self) -> None:
        with self.assertRaises(pps.ProjectContractRefused):
            self.service.create_project(title="   ", content="有正文")
        with self.assertRaises(pps.ProjectContractRefused):
            self.service.create_project(title="有标题", content="   ")
        # 提案对象不是创建入口：只接受明确标题+正文的字符串。
        with self.assertRaises(pps.ProjectContractRefused):
            self.service.create_project(title={"title": "提案"}, content="正文")
        first = self.service.create_project(title="夜航手记", content="第一段。",
                                            operation_key="project:create:1")
        self.assertTrue(first["ok"])
        self.assertEqual(first["status"], pps.STATUS_APPLIED)
        artifact_id = first["artifact_id"]
        self.assertTrue(artifact_id)
        replay = self.service.create_project(title="夜航手记", content="第一段。",
                                             operation_key="project:create:1")
        self.assertEqual(replay["artifact_id"], artifact_id)
        listed = self.service.list_projects()
        created = [row for row in listed["projects"] if row["artifact_id"] == artifact_id]
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]["title"], "夜航手记")

    def test_commit_requires_a_head_and_surfaces_conflict(self) -> None:
        with self.assertRaises(pps.ProjectContractRefused):
            self.service.commit_project(artifact_id=self.existing, content="新的一版",
                                        expected_head=None)
        stale = self.service.commit_project(artifact_id=self.existing, content="新的一版",
                                            expected_head="version-moved-on",
                                            operation_key="project:commit:stale")
        self.assertFalse(stale["ok"])
        self.assertEqual(stale["status"], pps.STATUS_CONFLICT)
        applied = self.service.commit_project(artifact_id=self.existing, content="新的一版",
                                              expected_head="version-old-work-1",
                                              operation_key="project:commit:1")
        self.assertTrue(applied["ok"])
        self.assertEqual(applied["status"], pps.STATUS_APPLIED)
        self.assertEqual(applied["version_number"], 2)
        detail = self.service.read_project(self.existing)
        self.assertEqual(detail["project"]["content"], "新的一版")
        self.assertEqual(detail["project"]["revision"], 2)

    def test_close_archives_and_keeps_the_text_readable(self) -> None:
        closed = self.service.close_project(artifact_id=self.existing, expected_revision=1,
                                            operation_key="project:close:1")
        self.assertTrue(closed["ok"])
        self.assertEqual(closed["lifecycle"], "ARCHIVED")
        again = self.service.close_project(artifact_id=self.existing, expected_revision=2,
                                           operation_key="project:close:2")
        self.assertEqual(again["status"], pps.STATUS_ALREADY_APPLIED)
        detail = self.service.read_project(self.existing)
        self.assertEqual(detail["project"]["status"], "archived")
        self.assertEqual(detail["project"]["content"], "旧的一页，已经在那儿了。")
        listed = self.service.list_projects()
        self.assertEqual([row["status"] for row in listed["projects"]], ["archived"])
        self.assertEqual(listed["counts"]["archived"], 1)
        self.assertEqual([], self.service.list_projects(include_archived=False)["projects"])

    def test_unknown_effect_is_reconciled_before_any_retry(self) -> None:
        self.port.unknown_next = True
        self.port.forward_on_unknown = True
        self.port.resolve_to = lap.EffectState.APPLIED
        reconciled = self.service.create_project(title="被打断的一份", content="已经落了一半。",
                                                 operation_key="project:create:unknown")
        self.assertTrue(reconciled["ok"])
        self.assertTrue(reconciled["reconciled"])
        self.assertEqual(reconciled["reason_code"], pps.REASON_RECONCILED)
        self.assertIn(("resolve_operation",
                       {"operation_key": "project:create:unknown"}), self.port.calls)

        self.port.unknown_next = True
        self.port.forward_on_unknown = False
        self.port.resolve_to = lap.EffectState.NO_CHANGE
        never = self.service.commit_project(artifact_id=self.existing, content="没有落地的一版",
                                            expected_head="version-old-work-1",
                                            operation_key="project:commit:never")
        self.assertEqual(never["status"], pps.STATUS_UNKNOWN)
        self.assertTrue(never["retryable"])
        self.assertEqual(never["reason_code"], pps.REASON_OPERATION_NEVER_LANDED)
        self.assertEqual(self.service.read_project(self.existing)["project"]["revision"], 1)


class ProjectCognitionTest(unittest.TestCase):
    def seed(self) -> dict[str, Any]:
        return ppc.bounded_seed(
            materials={"state": "AVAILABLE", "title": "夜航手记", "pages": 3, "page": 2,
                       "source_id": "artifact:" + READING, "source_revision": "v1"},
            questions=[{"id": "memo:1", "page": 2, "text": "这一页说的风是什么意思"},
                       {"id": "memo:2", "page": 3, "text": "结尾为什么停下来"}],
            existing_projects=[{"artifact_id": self.existing(),
                                "title": "《夜航手记》第 2 页：写成一页"}],
        )

    @staticmethod
    def existing() -> str:
        return "artifact:old-work"

    def test_structural_proposals_are_bounded_and_never_write(self) -> None:
        seed = self.seed()
        proposals = ppc.structural_proposals(seed)
        self.assertLessEqual(len(proposals), ppc.MAX_PROPOSALS)
        for row in proposals:
            self.assertTrue(row["proposal_id"].startswith(ppc.PROPOSAL_ID_PREFIX))
            self.assertTrue(row["requires_selection"])
            self.assertTrue(row["title"])
            self.assertLessEqual(len(row["title"]), ppc.MAX_TITLE_CHARS)
            self.assertIn(row["kind"], ppc.STRUCTURAL_KINDS)
            self.assertNotIn("artifact_id", row)
        titles = [row["title"] for row in proposals]
        # 已存在的项目标题不会被重复提案。
        self.assertNotIn("《夜航手记》第 2 页：写成一页", titles)
        self.assertEqual([row["kind"] for row in proposals],
                         [ppc.KIND_QUESTION, ppc.KIND_QUESTION])

    def test_selection_is_explicit_and_yields_title_plus_text(self) -> None:
        proposals = ppc.structural_proposals(self.seed())
        with self.assertRaises(ppc.ProjectProposalError):
            ppc.select_proposal(proposals, "")
        with self.assertRaises(ppc.ProjectProposalError):
            ppc.select_proposal(proposals, "pproposal:unknown")
        chosen = ppc.select_proposal(proposals, proposals[0]["proposal_id"])
        payload = ppc.chosen_project_payload(chosen)
        self.assertEqual(payload["title"], chosen["title"])
        self.assertEqual(payload["content"], chosen["seed_text"])
        self.assertEqual(payload["proposal_id"], chosen["proposal_id"])

    def test_model_failure_is_named_and_never_faked(self) -> None:
        class BrokenClient:
            def complete(self, **_kwargs: Any) -> Any:
                raise RuntimeError("transport down")

        with self.assertRaises(ppc.ProjectProposalError):
            ppc.model_proposals(BrokenClient(), self.seed())
        fallback = ppc.form_proposals(self.seed(), client=BrokenClient())
        self.assertEqual(fallback["origin"], ppc.ORIGIN_STRUCTURAL)
        self.assertIn("PROJECT_PROPOSAL_MODEL_FAILED", fallback["model_error"])
        self.assertTrue(fallback["creates_nothing"])

        class GoodClient:
            def complete(self, **_kwargs: Any) -> Any:
                class Result:
                    text = ('{"proposals":[{"kind":"OPEN_DIRECTION","title":"写一页风",'
                            '"first_step":"先记下今天的风","seed_text":"今天的风是从海边来的"}]}')
                return Result()

        formed = ppc.form_proposals(self.seed(), client=GoodClient())
        self.assertEqual(formed["origin"], ppc.ORIGIN_MODEL)
        self.assertEqual(len(formed["proposals"]), 1)
        self.assertEqual(formed["proposals"][0]["kind"], ppc.KIND_OPEN)
        self.assertEqual(formed["proposals"][0]["origin"], ppc.ORIGIN_MODEL)


class LifeOwnerAdapterTest(unittest.TestCase):
    """A 的 ``life_project_options.ProjectOwnerGateway`` 接上 B 的服务（同一条真实链路）。"""

    def setUp(self) -> None:
        self.port = FakePort()
        self.port.seed(artifact_id=READING, title="她的阅读材料", content="第一页")
        self.service = service_for(self.port)
        self.owner = pps.LifeProjectOwner(self.service)

    def test_adapter_answers_a_gateway_shaped_list(self) -> None:
        answer = self.owner.list_projects()
        self.assertEqual(answer["status"], pps.OWNER_STATUS_OK)
        # 材料不是项目：A 的 option 列表只会看到她真正的东西。
        self.assertEqual(answer["projects"], [])
        self.assertEqual(answer["excluded_artifact_ids"], [ITEMS, READING])

    def test_gateway_creates_one_project_through_the_owner(self) -> None:
        import life_project_options as lpo

        gateway = lpo.ProjectOwnerGateway(self.owner, subject_id=SUBJECT,
                                          workspace_id=WORKSPACE)
        self.assertTrue(gateway.available())
        created = gateway.create_project(
            title="从疑问长出来的一页", seed_text="这一页说的风是什么意思",
            source_refs=["artifact:memo:1"], option_ref="popt:1")
        self.assertEqual(created["status"], "OK")
        artifact_id = created["artifact_id"]
        self.assertTrue(artifact_id)
        detail = self.service.read_project(artifact_id)
        self.assertEqual(detail["project"]["content"], "这一页说的风是什么意思")
        # 再点一次同一个 option：owner 幂等，不会多出第二件。
        again = gateway.create_project(
            title="从疑问长出来的一页", seed_text="这一页说的风是什么意思",
            source_refs=["artifact:memo:1"], option_ref="popt:1")
        self.assertEqual(again["artifact_id"], artifact_id)
        listed = self.service.list_projects()
        self.assertEqual([row["title"] for row in listed["projects"]], ["从疑问长出来的一页"])
        self.assertEqual(listed["counts"]["total"], 1)
        # A 的 display ref（去掉 ``artifact:`` 前缀的十六进制）服务也认：同一件工件。
        self.assertEqual(detail["artifact_id"], "artifact:" + artifact_id)

    def test_adapter_refuses_a_foreign_workspace(self) -> None:
        answer = self.owner.create_project(title="别的世界", seed_text="正文",
                                           workspace_id="ws-other")
        self.assertEqual(answer["status"], pps.OWNER_STATUS_REFUSED)
        self.assertEqual(answer["reason"], "WORKSPACE_MISMATCH")
        self.assertEqual(self.service.list_projects()["counts"]["total"], 0)

    def test_env_factory_refuses_a_half_wired_environment(self) -> None:
        with self.assertRaises(pps.ProjectUnavailable):
            pps.private_projects_from_env({})
        with self.assertRaises(pps.ProjectUnavailable):
            pps.private_projects_from_env({"NYAIRO_LIFE_WORKSPACE_ID": "ws-1"})


if __name__ == "__main__":
    unittest.main()

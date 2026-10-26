"""v0.5 同一件作品上下文的关联规则（纯读，假 owner 端口）。

这里只钉住 ``life_work_context.WorkContextReader`` 自己的判断：

* 结果只认 ``artifact_ref`` 与目标作品相同的经历；
* 做法只列证据（创建时的 experience_ids 或某条 trial 的 experience_ids）真的指回这些结果
  的那些；没有结果时**不**把全局做法认领给这件作品；
* 草稿只列 ``source.artifact_id`` 就是这件作品的；trial 的版本是方法自己的版本；
* 一段读失败只让那一段具名失败，不会变成空，也不影响其它段。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any, Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_work_context as lwc  # noqa: E402


WORK = "artifact:work-1"
OTHER = "artifact:work-2"
V1 = "artifact-version:v1"
V2 = "artifact-version:v2"
METHOD = "artifact:method-1"
OTHER_METHOD = "artifact:method-2"


class FakeProjects:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def read_project(self, artifact_id: str, *, include_history: bool = True) -> dict[str, Any]:
        if self.fail:
            raise OSError("owner down")
        return {"ok": True, "artifact_id": artifact_id, "project": {
            "artifact_id": artifact_id, "title": "夜航手记", "lifecycle": "ACTIVE",
            "head_version_id": V2, "version_number": 2, "content_chars": 42,
            "content_available": True, "continuable": True}}

    def read_project_history(self, artifact_id: str, *, limit: int = 20) -> dict[str, Any]:
        return {"ok": True, "entries": [
            {"version_id": V2, "version_number": 2, "created_at": "2026-11-20T09:00:00Z"},
            {"version_id": V1, "version_number": 1, "created_at": "2026-11-20T08:00:00Z"},
        ], "count": 2, "total_versions": 2}

    def read_project_provenance(self, artifact_id: str) -> dict[str, Any]:
        return {"ok": True, "provenance": None, "reason_code": "NO_FIRST_VERSION"}


class FakeMemory:
    def __init__(self, rows, *, broken: bool = False) -> None:
        self.rows = rows
        self.broken = broken

    def list_experiences(self, *, query: str = "", limit: int = 20) -> dict[str, Any]:
        if self.broken:
            return {"ok": False, "status": "unavailable",
                    "reason_code": "MEMORY_READ_FAILED", "data": {}}
        return {"ok": True, "status": "ready", "data": {"experiences": self.rows}}


class FakePractice:
    def __init__(self, methods, details) -> None:
        self._methods = methods
        self._details = details

    def methods(self, *, query: str = "", limit: Optional[int] = None,
                available_only: bool = False):
        return self._methods

    def read_method(self, artifact_id: str):
        return self._details.get(artifact_id)


class FakeSharing:
    def __init__(self, rows) -> None:
        self.rows = rows

    def list_shares(self, *, include_text: bool = False, limit: Optional[int] = None):
        return {"ok": True, "status": "ok", "data": {"shares": self.rows}}


def _result(version: str, artifact: str = WORK, experience: str = "exp-1") -> dict[str, Any]:
    return {"experience_id": experience, "occurred_at": "2026-11-20T09:00:00Z",
            "artifact_ref": artifact, "version_ref": "artifactversion:" + version,
            "owner_result": "rset:1", "outcome_kind": "ACTED", "text": "写完了。"}


def _method_row(artifact_id: str = METHOD) -> dict[str, Any]:
    return {"method_ref": "method:" + artifact_id.split(":")[-1], "artifact_id": artifact_id,
            "title": "先写结论", "instruction": "先写结论", "scope": "长文",
            "uncertainty": "", "method_state": "TRIAL", "head_version_id": "mv2",
            "trial_count": 1, "counterexample_count": 0, "available": True}


def _method_detail(experience_ids, *, version: str = "mv2") -> dict[str, Any]:
    return {"artifact_id": METHOD, "head_version_id": version, "content_available": True,
            "governance_blocked": False, "experience_ids": [],
            "trials": [{"outcome": "HELPED", "version_id": version,
                        "experience_ids": list(experience_ids)}]}


class WorkContextAssociationTest(unittest.TestCase):
    def reader(self, *, memory_rows=None, projects=None, methods=None, details=None,
               shares=None):
        return lwc.WorkContextReader(
            projects=projects if projects is not None else FakeProjects(),
            practice=FakePractice(methods or [], details or {}),
            sharing=FakeSharing(shares or []),
            memory=FakeMemory(memory_rows or []))

    def test_only_results_of_this_artifact_and_only_evidence_linked_methods(self):
        reader = self.reader(
            memory_rows=[_result(V2), _result(V1, experience="exp-1"),
                         _result(V2, artifact=OTHER, experience="exp-other")],
            methods=[_method_row(METHOD), _method_row(OTHER_METHOD)],
            details={METHOD: _method_detail(["exp-1"]),
                     OTHER_METHOD: _method_detail(["exp-other"])},
            shares=[{"share_id": "artifact:share-1", "state": "DRAFT", "source": {
                         "artifact_id": WORK, "version_id": V2}},
                    {"share_id": "artifact:share-2", "state": "DRAFT", "source": {
                         "artifact_id": OTHER, "version_id": V2}}])
        view = reader.read(WORK)
        self.assertEqual(view["state"], "OK")
        self.assertEqual(view["work"]["head_version_id"], V2)
        self.assertEqual([row["version_id"] for row in view["versions"]["entries"]],
                         [V2, V1])
        self.assertEqual({row["experience_id"] for row in view["results"]["rows"]},
                         {"exp-1"})
        self.assertTrue(all(lwc.same_artifact(row["artifact_ref"], WORK)
                            for row in view["results"]["rows"]))
        # ``exp-other`` belongs to the other work but is *not* in the scanned results of this one,
        # so the method that cites it is not listed either.
        self.assertEqual([row["artifact_id"] for row in view["methods"]["rows"]], [METHOD])
        row = view["methods"]["rows"][0]
        self.assertEqual(row["latest_evaluation"]["outcome"], "HELPED")
        self.assertIn("exp-1", row["work_result_experience_ids"])
        self.assertEqual([row["share_id"] for row in view["drafts"]["rows"]],
                         ["artifact:share-1"])
        self.assertNotIn("text", view["drafts"]["rows"][0])

    def test_no_result_means_no_global_method_is_claimed(self):
        reader = self.reader(memory_rows=[_result(V2, artifact=OTHER)],
                             methods=[_method_row(METHOD)],
                             details={METHOD: _method_detail(["exp-1"])})
        view = reader.read(WORK)
        self.assertEqual(view["results"]["rows"], [])
        self.assertEqual(view["methods"]["state"], "NO_EVIDENCE")
        self.assertEqual(view["methods"]["reason_code"],
                         "WORK_CONTEXT_NO_WORK_RESULT_EVIDENCE")
        self.assertEqual(view["methods"]["rows"], [])

    def test_a_failed_section_is_named_and_does_not_empty_the_others(self):
        reader = self.reader(projects=FakeProjects(fail=True),
                             memory_rows=[_result(V2)])
        view = reader.read(WORK)
        self.assertFalse(view["work"]["available"])
        self.assertEqual(view["work"]["reason_code"], "WORK_CONTEXT_PROJECT_READ_FAILED")
        self.assertTrue(view["results"]["rows"])

    def test_memory_refusal_is_named(self):
        reader = lwc.WorkContextReader(
            projects=FakeProjects(), practice=FakePractice([], {}), sharing=FakeSharing([]),
            memory=FakeMemory([], broken=True))
        view = reader.read(WORK)
        self.assertEqual(view["results"]["reason_code"], "MEMORY_READ_FAILED")

    def test_missing_target_is_refused_not_guessed(self):
        view = self.reader().read("")
        self.assertEqual(view["state"], "UNAVAILABLE")
        self.assertEqual(view["reason_code"], "WORK_ARTIFACT_ID_REQUIRED")


class PhoneToolWorkActionsTest(unittest.TestCase):
    def test_work_actions_are_private_reads_with_explicit_fields(self):
        import chiyo_bundle.phone_tool as tool

        for action in ("work_context", "work_version"):
            self.assertIn(action, tool.ACTIONS)
            self.assertIn(action, tool.PRIVATE_ARTIFACT_ACTIONS)
            self.assertNotIn(action, tool.WRITE_ACTIONS)
            self.assertIn(action, tool._FIELD_BY_ACTION)
        self.assertEqual(tool._FIELD_BY_ACTION["work_context"],
                         frozenset({"action", "artifact_id"}))
        self.assertEqual(tool._FIELD_BY_ACTION["work_version"],
                         frozenset({"action", "artifact_id", "version_id"}))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

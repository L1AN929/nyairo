#!/usr/bin/env python3
"""v0.4 work-revisit · 正常续改的写作请求里，真的带着从 owner 读回的旧版正文。

测什么（证据分级）
------------------
* owner 是真的：复用 ``tests/test_v04_work_revisit_history.py`` 的 ``SupplyOwner``（真实
  ``LifeSupplyService`` + AF_UNIX + 同一 canonical db）。本文件只新建两版以上的作品，不重投旧夹具。
* 摘要是真的：``PrivateProjectService.continuation_excerpt()`` 通过 ``read_version`` 读到**上一版**
  正文，带上 ``version_id`` / ``version_number`` / 存库 hash；源被 tombstone 后它**不给正文**。
* 写作请求是真的：摘要装进 context 交给真实 ``ModelContinuationWriter.next_content``，由它自己的
  ``complete(system_prompt=…, user_prompt=…)`` 发出去；测试用只记录请求的 client 抓住那份
  ``user_prompt``，断言 sentinel 出现在**实际请求体**里，而不是断言一个 helper 的返回值。
  client 是替身（合法：本段禁止产品 completion），writer/prompt/合并/限额全是产品代码。

不做的事：不建候选、不走 AG1/AG2、不发模型、不写 Activity、不删任何东西；夹具根建在短 root 下并保留。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(Path(__file__).resolve().parent)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_artifact_port as lap  # noqa: E402
import model_continuation_writer as mcw  # noqa: E402
import private_project_service as pps  # noqa: E402
import test_v04_work_revisit_history as base  # noqa: E402

SENTINEL_EARLIER = "EARLIER-VERSION-SENTINEL"
V1 = "第一版：%s 这一句只在旧版里。" % SENTINEL_EARLIER
V2 = "第二版：head 里的正文，没有那个标记。"

posix_only = unittest.skipIf(os.name == "nt", "needs POSIX AF_UNIX (run under WSL/Linux)")


class RecordingClient:
    """A transport stand-in that keeps every request the real writer actually sent."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.requests: list[dict[str, Any]] = []

    def complete(self, *, system_prompt: str, user_prompt: str, timeout_s: float = 0.0,
                 max_output_tokens: int = 0):
        self.requests.append({"system_prompt": system_prompt, "user_prompt": user_prompt})

        class _Result:
            text = self.reply

        return _Result()


@posix_only
class WriterCarriesEarlierVersionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = (Path(os.environ.get("NYAIRO_WORK_REVISIT_ROOT")
                          or os.environ.get("PI_SCRATCH_DIR") or "/tmp")
                     / ("wr-writer-%d-%d" % (os.getpid(), time.time_ns())))
        self.root.mkdir(parents=True, exist_ok=False)
        self.owner = base.SupplyOwner(self.root)
        self.addCleanup(self.owner.stop)
        self.workspace_id = self._provision_workspace()
        self.grant_id = self._issue_artifact_grant()
        self.port = lap.LifeArtifactPort(str(self.owner.socket_path), base.SUBJECT)
        self.work_id = self._seed_two_versions()
        self.service = pps.PrivateProjectService(self.port, self.workspace_id, self.grant_id)

    # -- owner plumbing (operator ops) ---------------------------------------------------
    def _issue(self, capability: str, scope: str, target: str, authority_scope: str,
               use_mode: str, operation_id: str) -> str:
        answer = self.owner.call({"op": "issue_grant", "grant": {
            "subject": base.SUBJECT, "capability": capability, "scope": scope, "target": target,
            "authority_scope": authority_scope, "use_mode": use_mode,
            "operation_id": operation_id}})
        self.assertEqual(answer.get("status"), "ALLOW", answer)
        return str(answer["grant_id"])

    def _provision_workspace(self) -> str:
        grant = self._issue("PROVISION_PERSONAL_WORKSPACE", "workspace:personal",
                            "workspace:%s" % base.SUBJECT, "life-supply.workspace.v1",
                            "ONE_SHOT", "issue:workspace:%s" % base.SUBJECT)
        answer = self.owner.call({"op": "provision_workspace", "grant_id": grant,
                                  "operation_key": "wr-writer:workspace:%s" % base.SUBJECT,
                                  "workspace": {"subject_id": base.SUBJECT}})
        self.assertEqual(answer.get("status"), "OK", answer)
        return str(answer["workspace"]["workspace_id"])

    def _issue_artifact_grant(self) -> str:
        return self._issue("COMMIT_MANAGED_ARTIFACT", "artifact:personal",
                           "workspace:%s" % self.workspace_id, "life-supply.artifact.v1",
                           "REUSABLE", "issue:artifact:writer")

    def _seed_two_versions(self) -> str:
        outcome = self.port.create_artifact(
            workspace_id=self.workspace_id, title="两版的手记", content=V1,
            grant_id=self.grant_id,
            operation_key=lap.operation_key_for("wr-writer", "v1", base.SUBJECT))
        self.assertTrue(outcome.applied, outcome)
        artifact_id = str(outcome.artifact_id)
        head = self.port.read_head(artifact_id) or {}
        second = self.port.commit_version(
            artifact_id=artifact_id, expected_head=str(head["version_id"]), content=V2,
            grant_id=self.grant_id,
            operation_key=lap.operation_key_for("wr-writer", "v2", base.SUBJECT))
        self.assertTrue(second.applied, second)
        return artifact_id

    # -- the one thing this file is about ------------------------------------------------
    def test_earlier_version_excerpt_reaches_the_actual_writer_request(self) -> None:
        excerpt = self.service.continuation_excerpt(self.work_id, max_chars=200)
        self.assertTrue(excerpt.get("ok"), excerpt)
        self.assertIn(SENTINEL_EARLIER, excerpt["excerpt"])
        self.assertEqual(excerpt["version_number"], 1)
        self.assertTrue(excerpt["version_id"])

        point = self.port.resume_point(self.work_id)
        self.assertNotIn(SENTINEL_EARLIER, point.content or "",
                         "the head must not contain the old sentence: the writer gets it only "
                         "through the pinned excerpt")
        client = RecordingClient('{"kind":"append_paragraph","text":"新加的一句。"}')
        writer = mcw.ModelContinuationWriter(client)
        written = writer.next_content(
            point, observed_at="2026-10-28T12:00:00+08:00",
            context={"earlier_versions": [excerpt]})
        self.assertTrue(written)

        self.assertEqual(len(client.requests), 1)
        request = json.loads(client.requests[0]["user_prompt"])
        self.assertIn("earlier_versions", request)
        self.assertEqual(request["earlier_versions"][0]["version_number"], 1)
        self.assertIn(SENTINEL_EARLIER, request["earlier_versions"][0]["excerpt"])
        # Real request body, not a context dict: the sentinel is inside the JSON the transport got.
        self.assertIn(SENTINEL_EARLIER, client.requests[0]["user_prompt"])
        # The head is still the head: the current text in the same request is the new version.
        self.assertIn(V2, request["tail_of_current_text"])
        self.assertNotIn(SENTINEL_EARLIER, request["tail_of_current_text"])

    def test_a_blocked_source_sends_no_old_text_to_the_writer(self) -> None:
        derived = self.service.create_derived_project(
            title="派生的一件", content="派生正文，没有标记。", source_artifact_id=self.work_id,
            source_version_id=str((self.port.read_head(self.work_id) or {})["version_id"]),
            purpose="adapt", operation_key="wr-writer:derive:1")
        self.assertTrue(derived.get("ok"), derived)
        derived_id = str(derived["artifact_id"])
        head = self.port.read_head(derived_id) or {}
        self.port.commit_version(
            artifact_id=derived_id, expected_head=str(head["version_id"]),
            content="派生第二版。", grant_id=self.grant_id,
            operation_key="wr-writer:derive:commit")
        revision = int((self.port.read_artifact(self.work_id)["artifact"])["revision"])
        answer = self.owner.call({"op": "tombstone", "grant_id": self.grant_id,
                                  "operation_key": "wr-writer:tomb:%s" % revision,
                                  "artifact": {"subject_id": base.SUBJECT,
                                               "artifact_id": self.work_id,
                                               "expected_revision": revision}})
        self.assertEqual(answer.get("status"), "OK", answer)

        excerpt = self.service.continuation_excerpt(derived_id)
        # Withholding is reported, not disguised: no body is handed out and the reason names the
        # blocked source.  ``ok`` here means "the owner was asked and answered", not "text found".
        self.assertIsNone(excerpt.get("excerpt"))
        self.assertTrue(excerpt.get("content_withheld"), excerpt)
        self.assertEqual(excerpt.get("reason_code"), "SOURCE_NOT_READABLE")

        point = self.port.resume_point(derived_id)
        client = RecordingClient('{"kind":"append_paragraph","text":"不该出现旧版。"}')
        writer = mcw.ModelContinuationWriter(client)
        context = {} if excerpt.get("excerpt") is None else {"earlier_versions": [excerpt]}
        writer.next_content(point, observed_at="2026-10-28T12:00:00+08:00", context=context)
        self.assertNotIn(SENTINEL_EARLIER, client.requests[0]["user_prompt"])


WORK_ID = "artifact:" + "a" * 32
WORKSPACE = "workspace:wr-writer"
GRANT = "grant:wr-writer"


def _point() -> lap.ArtifactResumePoint:
    return lap.ArtifactResumePoint(
        artifact_id=WORK_ID, title="两版的手记", head_version_id="artifact-version:head",
        version_number=2, content=V2, lifecycle="ACTIVE", revision=3)


class _FakeEarlierService:
    """A project-service stand-in: returns the answer it was given, records each read."""

    def __init__(self, answer: Any) -> None:
        self.answer = answer
        self.calls: list[tuple[str, int]] = []

    def continuation_excerpt(self, artifact_id: str, *, max_chars: int = 600) -> Any:
        self.calls.append((artifact_id, max_chars))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@posix_only
class CycleEarlierVersionWiringTest(unittest.TestCase):
    """The cycle is the missing link: it must put the owner-read excerpt into the real request.

    No owner and no model: the cycle is built with a stand-in port and an injected project
    service, while the writer/prompt/request body remain the product code.  The cycle itself is
    imported here rather than at module scope: it pulls in ``fcntl``-based modules, so this class
    lives under the same POSIX gate as the owner-backed tests above.
    """

    def _cycle(self, client: RecordingClient, *, workspace_id: Any = WORKSPACE,
               grant_id: Any = GRANT) -> Any:
        import autonomous_life_cycle as alc
        root = Path(tempfile.mkdtemp(prefix="earlier-cycle-"))
        self.addCleanup(shutil.rmtree, str(root), True)
        return alc.AutonomousLifeCycle(
            SimpleNamespace(state_root=root), enabled=True, env={},
            artifact_port=object(), workspace_id=workspace_id, artifact_grant_id=grant_id,
            writer=mcw.ModelContinuationWriter(client))

    def test_really_sent_request_carries_the_earlier_version(self) -> None:
        client = RecordingClient('{"kind":"append_paragraph","text":"新加的一句。"}')
        cycle = self._cycle(client)
        service = _FakeEarlierService({
            "ok": True, "excerpt": V1, "version_id": "artifact-version:old",
            "version_number": 1, "reason_code": "OK"})
        cycle._earlier_project_service = service

        written = cycle._next_content(_point(), observed_at="2026-10-28T12:00:00+08:00")
        self.assertTrue(written)
        # Exactly one bounded read, for this work, at the documented cap.
        self.assertEqual(service.calls, [(WORK_ID, 600)])

        self.assertEqual(len(client.requests), 1)
        request = json.loads(client.requests[0]["user_prompt"])
        self.assertEqual(len(request["earlier_versions"]), 1)
        self.assertEqual(request["earlier_versions"][0]["version_number"], 1)
        self.assertEqual(request["earlier_versions"][0]["version_id"], "artifact-version:old")
        # The sentinel is in the JSON the transport actually received, not merely in a dict.
        self.assertIn(SENTINEL_EARLIER, client.requests[0]["user_prompt"])
        self.assertNotIn(SENTINEL_EARLIER, request["tail_of_current_text"])

    def test_no_key_when_the_service_fails_withholds_or_is_unwired(self) -> None:
        cases = {
            "not_ok": {"ok": False, "excerpt": V1, "version_number": 1},
            "no_excerpt": {"ok": True, "excerpt": None, "version_number": 1},
            "blank_excerpt": {"ok": True, "excerpt": "   ", "version_number": 1},
            "exception": RuntimeError("owner unavailable"),
        }
        for label, answer in cases.items():
            with self.subTest(label):
                client = RecordingClient('{"kind":"append_paragraph","text":"照常写。"}')
                cycle = self._cycle(client)
                cycle._earlier_project_service = _FakeEarlierService(answer)
                written = cycle._next_content(_point(), observed_at="2026-10-28T12:00:00+08:00")
                self.assertTrue(written, "a failed earlier read must not fail the writing step")
                request = json.loads(client.requests[0]["user_prompt"])
                self.assertNotIn("earlier_versions", request)
                self.assertNotIn(SENTINEL_EARLIER, client.requests[0]["user_prompt"])

        # Unwired workspace: no read at all, no key, writing still happens.
        client = RecordingClient('{"kind":"append_paragraph","text":"没有工作区也照写。"}')
        cycle = self._cycle(client, workspace_id=None)
        service = _FakeEarlierService({"ok": True, "excerpt": V1, "version_number": 1})
        cycle._earlier_project_service = service
        cycle._next_content(_point(), observed_at="2026-10-28T12:00:00+08:00")
        self.assertEqual(service.calls, [])
        self.assertNotIn("earlier_versions", json.loads(client.requests[0]["user_prompt"]))

    def test_the_project_service_is_built_once_and_reused(self) -> None:
        client = RecordingClient('{"kind":"append_paragraph","text":"写两次。"}')
        cycle = self._cycle(client)
        built: list[tuple[str, str]] = []

        class _Service:
            def __init__(self, port: Any, workspace_id: str, grant_id: str) -> None:
                built.append((workspace_id, grant_id))

            def continuation_excerpt(self, artifact_id: str, *, max_chars: int = 600) -> dict:
                return {"ok": True, "excerpt": "旧版一句。", "version_id": "v1",
                        "version_number": 1}

        with mock.patch.object(pps, "PrivateProjectService", _Service):
            cycle._next_content(_point(), observed_at="2026-10-28T12:00:00+08:00")
            cycle._next_content(_point(), observed_at="2026-10-28T12:00:00+08:00")
        self.assertEqual(built, [(WORKSPACE, GRANT)])
        self.assertEqual(len(client.requests), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)

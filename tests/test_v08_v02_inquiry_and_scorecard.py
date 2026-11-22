#!/usr/bin/env python3
"""v0.8 0.2 收口：有界 Inquiry 完整化 + R0–R5 反傀儡五维 Scorecard 验收。

覆盖：
1. ``Inquiry`` 候选解释（plausible/supported/ruled_out）、下一步线索、单条步骤预算；
   ``step_budget=2`` 第 2 条证据自动收口 ``STOPPED`` / ``BUDGET_EXHAUSTED``，同 ``operation_key``
   重放幂等不重复累加。
2. 伴侣动作 ``share_inquiry_clue``：公开问题可追加线索；私密/不存在一律 ``404 INQUIRY_NOT_FOUND``
   （零存在性泄漏）；已结束问题 ``409 INQUIRY_ALREADY_CLOSED``；伴侣试调 ``record_inquiry_evidence``
   仍被 ``403 FORBIDDEN_ACTOR_SIDE`` 拦住。
3. ``v02_antipuppet_scorecard``：100% 零写盘、``llm_calls == 0``、五维 + R0–R5 结构完整；
   单维硬违例时 ``overall_risk`` 强制 ``HIGH``，不被其余 ``LOW`` 平均稀释。
4. 真实 loopback HTTP：``GET /api/phone/v02/runtime-migration`` 挂载 ``antipuppet_scorecard``；
   ``share_inquiry_clue`` 受 CSRF 放行，缺 CSRF 403。
5. 新增生产源码无硬编码 ``她`` / ``千代``。

Run:  python3 -m unittest tests.test_v08_v02_inquiry_and_scorecard
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "components" / "world" / "service" / "bridge"),
    str(ROOT / "components" / "supply"),
    str(ROOT / "scripts"),
    str(ROOT / "tests"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from components.life.scripts import v02_agency_depth_engine as ad_engine  # noqa: E402
from components.life.scripts import v02_antipuppet_scorecard as scorecard  # noqa: E402
from components.phone import v02_agency_depth_view as ad_view  # noqa: E402
from components.phone import v02_runtime_migration_view as rm_view  # noqa: E402

T0 = "2026-11-16T09:00:00+08:00"
T1 = "2026-11-16T10:00:00+08:00"
T2 = "2026-11-16T11:00:00+08:00"

CAP_TOKEN = "v02-phone-http-cap-token-20261113"
SUBJECT = "chiyo"

POSIX_ONLY = unittest.skipUnless(
    hasattr(socket, "AF_UNIX") and os.name == "posix",
    "Requires POSIX domain sockets (run inside WSL)",
)


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int, str]]:
    if not root.exists():
        return {}
    snap: dict[str, tuple[int, int, str]] = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root)).replace("\\", "/")
        st = path.lstat()
        if path.is_file() and not path.is_symlink():
            snap[rel] = (
                int(st.st_size),
                int(st.st_mtime_ns),
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        else:
            snap[rel] = (0, int(st.st_mtime_ns), "dir")
    return snap


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-inquiry-scorecard-")
        self.root = Path(self._tmp.name) / "instance"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _open(
        self,
        *,
        question: str,
        shared: bool = True,
        candidates: list[dict[str, Any]] | None = None,
        next_evidence: str = "",
        step_budget: int | None = None,
        key: str | None = None,
        now_iso: str = T0,
    ) -> tuple[int, dict[str, Any]]:
        payload: dict[str, Any] = {
            "action": "open_bounded_inquiry",
            "question": question,
            "shared": shared,
        }
        if candidates is not None:
            payload["candidate_explanations"] = candidates
        if next_evidence:
            payload["next_evidence_needed"] = next_evidence
        if step_budget is not None:
            payload["step_budget"] = step_budget
        if key:
            payload["operation_key"] = key
        return ad_view.mutate_v02_agency_depth_view(
            self.root, payload, actor_side="subject", now_iso=now_iso
        )

    def _seed_clean_instance(self) -> None:
        (self.root / "phone").mkdir(parents=True, exist_ok=True)
        (self.root / "memory").mkdir(parents=True, exist_ok=True)
        (self.root / "life").mkdir(parents=True, exist_ok=True)
        (self.root / "state").mkdir(parents=True, exist_ok=True)

        (self.root / "phone" / "character_pack_state.json").write_text(
            json.dumps({
                "schema_version": 1,
                "revision": 0,
                "source": "default_neutral_preset",
                "preset_id": "neutral_resident",
                "pack_doc": {
                    "display_name": "居民",
                    "pronouns": "TA",
                    "persona": "neutrally described resident",
                },
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        (self.root / "phone" / "autonomy_rhythm.json").write_text(
            json.dumps({"active_locks": [], "subject_override": None}),
            encoding="utf-8",
        )
        (self.root / "life" / "life_dwell.json").write_text(
            json.dumps({
                "host_gaps": [{
                    "honest_blank": True,
                    "fabricated_memories": 0,
                    "cognitive_backfill_count": 0,
                }],
            }),
            encoding="utf-8",
        )
        (self.root / "memory" / "v02_recall_projection_state.json").write_text(
            json.dumps({
                "memories": {
                    "mem_1": {
                        "gist_summary": "一次真实发生的小事。",
                        "occurred_at": "2026-11-15T10:00:00+08:00",
                        "source_refs": ["fs:life/note.md"],
                    },
                },
                "tombstones": {},
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        (self.root / "state" / "v02_agency_depth_state.json").write_text(
            json.dumps({
                "inquiries": [{
                    "inquiry_id": "inq_0001",
                    "shared": True,
                    "status": "INVESTIGATING",
                    "evidence_notes": [{"note": "真实观察", "source_ref": "book:1"}],
                }],
                "method_adjustments": [],
            }, ensure_ascii=False),
            encoding="utf-8",
        )


# ===========================================================================
# 1. Inquiry candidate explanations / budget auto-stop
# ===========================================================================


class InquiryBudgetAndCandidatesTest(_Base):
    def test_01_open_with_candidates_and_budget_auto_stop_is_idempotent(self) -> None:
        code, doc = self._open(
            question="旧木窗隔音怎么做？",
            candidates=[
                {"summary_cn": "换整扇窗", "stance": "ruled_out"},
                {"summary_cn": "贴门缝密封条", "stance": "supported"},
            ],
            next_evidence="查密封条规格",
            step_budget=2,
            key="inq-open-1",
        )
        self.assertEqual(code, 200, doc)
        inq = doc["inquiries"][0]
        self.assertEqual(inq["step_budget"], 2)
        self.assertEqual(inq["steps_spent"], 0)
        self.assertEqual(inq["next_evidence_needed"], "查密封条规格")
        self.assertEqual(
            [c["stance"] for c in inq["candidate_explanations"]],
            ["ruled_out", "supported"],
        )
        iid = inq["inquiry_id"]

        code, doc = ad_view.mutate_v02_agency_depth_view(
            self.root,
            {
                "action": "record_inquiry_evidence",
                "inquiry_id": iid,
                "note": "第一条真实观察",
                "source_ref": "book:window_seal",
                "operation_key": "inq-ev-1",
            },
            actor_side="subject",
            now_iso=T1,
        )
        self.assertEqual(code, 200, doc)
        self.assertEqual(doc["inquiries"][0]["status"], "INVESTIGATING")
        self.assertEqual(doc["inquiries"][0]["steps_spent"], 1)

        code, doc = ad_view.mutate_v02_agency_depth_view(
            self.root,
            {
                "action": "record_inquiry_evidence",
                "inquiry_id": iid,
                "note": "第二条真实观察",
                "source_ref": "book:window_seal_2",
                "operation_key": "inq-ev-2",
            },
            actor_side="subject",
            now_iso=T2,
        )
        self.assertEqual(code, 200, doc)
        stopped = doc["inquiries"][0]
        self.assertEqual(stopped["steps_spent"], 2)
        self.assertEqual(stopped["status"], "STOPPED")
        self.assertEqual(stopped["stop_reason"], "BUDGET_EXHAUSTED")
        self.assertIsNotNone(stopped["settled_at"])

        # 同 operation_key 重放幂等：不重复累加、不重复收口。
        code, doc = ad_view.mutate_v02_agency_depth_view(
            self.root,
            {
                "action": "record_inquiry_evidence",
                "inquiry_id": iid,
                "note": "第二条真实观察",
                "source_ref": "book:window_seal_2",
                "operation_key": "inq-ev-2",
            },
            actor_side="subject",
            now_iso=T2,
        )
        self.assertEqual(code, 200, doc)
        self.assertEqual(doc.get("command_status"), "DUPLICATE")
        self.assertEqual(doc["inquiries"][0]["steps_spent"], 2)
        self.assertEqual(len(doc["inquiries"][0]["evidence_notes"]), 2)

    def test_02_old_inquiry_without_new_fields_reads_with_safe_defaults(self) -> None:
        legacy = {
            "schema": ad_engine.VIEW_SCHEMA,
            "revision": 1,
            "next_seq": 2,
            "inquiries": [{
                "inquiry_id": "inq_0001",
                "question": "老数据问题",
                "topic_tag": "",
                "status": "OPEN",
                "shared": True,
                "evidence_notes": [],
                "conclusion": "",
                "stop_reason": "",
                "opened_at": T0,
                "updated_at": T0,
                "settled_at": None,
            }],
            "operation_ledger": {},
        }
        path = self.root / "state" / "v02_agency_depth_state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

        code, doc = ad_view.read_v02_agency_depth_view(
            self.root, has_subject_capability=True, now_iso=T1
        )
        self.assertEqual(code, 200, doc)
        inq = doc["inquiries"][0]
        self.assertEqual(inq["candidate_explanations"], [])
        self.assertEqual(inq["next_evidence_needed"], "")
        self.assertEqual(inq["step_budget"], ad_engine.DEFAULT_INQUIRY_STEP_BUDGET)
        self.assertEqual(inq["steps_spent"], 0)
        self.assertEqual(inq["partner_clues"], [])


# ===========================================================================
# 2. share_inquiry_clue view semantics
# ===========================================================================


class ShareInquiryClueTest(_Base):
    def test_01_public_clue_private_and_missing_are_not_found_and_closed_is_409(self) -> None:
        _, doc = self._open(question="公开问题", shared=True, key="pq")
        public_id = doc["inquiries"][0]["inquiry_id"]
        _, doc = self._open(question="私密问题", shared=False, key="pv")
        private_id = doc["inquiries"][0]["inquiry_id"]

        code, doc = ad_view.mutate_v02_agency_depth_view(
            self.root,
            {
                "action": "share_inquiry_clue",
                "inquiry_id": public_id,
                "clue_text": "密封条有背胶款",
                "operation_key": "clue-1",
            },
            actor_side="partner",
            now_iso=T1,
        )
        self.assertEqual(code, 200, doc)
        public = next(i for i in doc["inquiries"] if i["inquiry_id"] == public_id)
        self.assertEqual(len(public["partner_clues"]), 1)
        self.assertEqual(public["partner_clues"][0]["clue_text"], "密封条有背胶款")
        self.assertTrue(public["partner_clues"][0]["clue_id"])
        # 不强迫 TA 改变结论。
        self.assertEqual(public["status"], "OPEN")

        # 重放同一条线索幂等。
        code, doc = ad_view.mutate_v02_agency_depth_view(
            self.root,
            {
                "action": "share_inquiry_clue",
                "inquiry_id": public_id,
                "clue_text": "密封条有背胶款",
                "operation_key": "clue-1",
            },
            actor_side="partner",
            now_iso=T1,
        )
        self.assertEqual(code, 200, doc)
        self.assertEqual(doc.get("command_status"), "DUPLICATE")
        public = next(i for i in doc["inquiries"] if i["inquiry_id"] == public_id)
        self.assertEqual(len(public["partner_clues"]), 1)

        # 私密/不存在一律 404，绝不暴露存在性。
        code, doc = ad_view.mutate_v02_agency_depth_view(
            self.root,
            {"action": "share_inquiry_clue", "inquiry_id": private_id, "clue_text": "x"},
            actor_side="partner",
            now_iso=T1,
        )
        self.assertEqual(code, 404)
        self.assertEqual(doc["reason_code"], "INQUIRY_NOT_FOUND")
        code, doc = ad_view.mutate_v02_agency_depth_view(
            self.root,
            {"action": "share_inquiry_clue", "inquiry_id": "inq_9999", "clue_text": "x"},
            actor_side="partner",
            now_iso=T1,
        )
        self.assertEqual(code, 404)
        self.assertEqual(doc["reason_code"], "INQUIRY_NOT_FOUND")

        # 已结束问题 409。
        code, doc = ad_view.mutate_v02_agency_depth_view(
            self.root,
            {
                "action": "conclude_or_stop_inquiry",
                "inquiry_id": public_id,
                "outcome": "STOPPED",
                "stop_reason": "先不查了",
            },
            actor_side="subject",
            now_iso=T2,
        )
        self.assertEqual(code, 200, doc)
        code, doc = ad_view.mutate_v02_agency_depth_view(
            self.root,
            {"action": "share_inquiry_clue", "inquiry_id": public_id, "clue_text": "x"},
            actor_side="partner",
            now_iso=T2,
        )
        self.assertEqual(code, 409)
        self.assertEqual(doc["reason_code"], "INQUIRY_ALREADY_CLOSED")

        # 伴侣仍不能代记证据。
        code, doc = ad_view.mutate_v02_agency_depth_view(
            self.root,
            {"action": "record_inquiry_evidence", "inquiry_id": private_id, "note": "x"},
            actor_side="partner",
            now_iso=T2,
        )
        self.assertEqual(code, 403)
        self.assertEqual(doc["reason_code"], "FORBIDDEN_ACTOR_SIDE")


# ===========================================================================
# 3. Antipuppet scorecard structure / zero-write / hard-fail
# ===========================================================================


class AntipuppetScorecardTest(_Base):
    def test_01_clean_instance_is_low_and_zero_write(self) -> None:
        self._seed_clean_instance()
        before = _snapshot_tree(self.root)

        result = scorecard.evaluate_antipuppet_scorecard(self.root, now_iso=T1)
        rm_code, rm_doc = rm_view.read_v02_runtime_migration_view(
            self.root, has_subject_capability=False, now_iso=T1
        )

        self.assertEqual(_snapshot_tree(self.root), before, "scorecard must stay zero-write")
        self.assertEqual(result["schema"], "nyairo.v02.antipuppet_scorecard.v1")
        self.assertTrue(result["averaging_forbidden"])
        self.assertTrue(result["hard_fail_triggers_high"])
        self.assertEqual(result["llm_calls"], 0)
        self.assertEqual(result["overall_risk"], "LOW")
        self.assertEqual(
            [row["stage"] for row in result["r0_r5_checks"]],
            ["R0", "R1", "R2", "R3", "R4", "R5"],
        )
        for row in result["r0_r5_checks"]:
            self.assertEqual(set(row), {"stage", "title_cn", "passed", "note_cn"})
            self.assertIsInstance(row["passed"], bool)
            self.assertTrue(row["title_cn"])
        self.assertEqual(
            [dim["dim_id"] for dim in result["dimensions"]],
            [
                "factual_boundary",
                "agency_boundary",
                "persona_decoupling",
                "model_phrasing_independence",
                "growth_provenance",
            ],
        )
        for dim in result["dimensions"]:
            self.assertIn(dim["risk"], {"LOW", "MEDIUM", "HIGH"})
            self.assertTrue(dim["label_cn"])
            self.assertTrue(dim["summary_cn"])

        self.assertEqual(rm_code, 200, rm_doc)
        mounted = rm_doc["data"]["antipuppet_scorecard"]
        self.assertEqual(mounted, rm_doc["antipuppet_scorecard"])
        self.assertEqual(mounted["llm_calls"], 0)

    def test_02_single_hard_violation_forces_high_without_averaging(self) -> None:
        self._seed_clean_instance()

        pack_path = self.root / "phone" / "character_pack_state.json"
        pack = json.loads(pack_path.read_text(encoding="utf-8"))
        pack["pack_doc"]["private_override"] = True
        pack_path.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")

        result = scorecard.evaluate_antipuppet_scorecard(self.root, now_iso=T1)
        self.assertEqual(result["overall_risk"], "HIGH")
        by_id = {dim["dim_id"]: dim for dim in result["dimensions"]}
        self.assertEqual(by_id["persona_decoupling"]["risk"], "HIGH")
        # 其余维度仍为 LOW，证明 overall 不是平均分稀释。
        self.assertEqual(by_id["factual_boundary"]["risk"], "LOW")
        self.assertEqual(by_id["agency_boundary"]["risk"], "LOW")
        self.assertEqual(by_id["model_phrasing_independence"]["risk"], "LOW")
        self.assertEqual(by_id["growth_provenance"]["risk"], "LOW")
        r0 = next(row for row in result["r0_r5_checks"] if row["stage"] == "R0")
        self.assertFalse(r0["passed"])

    def test_03_missing_evidence_is_medium_not_fabricated_pass(self) -> None:
        result = scorecard.evaluate_antipuppet_scorecard(self.root, now_iso=T1)
        self.assertEqual(result["overall_risk"], "MEDIUM")
        for row in result["r0_r5_checks"]:
            self.assertIn("未观测", row["note_cn"])


# ===========================================================================
# 4. Source neutrality
# ===========================================================================


class SourceNeutralityTest(unittest.TestCase):
    def test_01_new_sources_are_character_neutral(self) -> None:
        files = (
            ROOT / "components" / "life" / "scripts" / "v02_antipuppet_scorecard.py",
            ROOT / "components" / "life" / "scripts" / "v02_agency_depth_engine.py",
            ROOT / "components" / "phone" / "v02_agency_depth_view.py",
            ROOT / "components" / "phone" / "v02_runtime_migration_view.py",
        )
        for path in files:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("她", text, f"Hardcoded pronoun in {path}")
            self.assertNotIn("千代", text, f"Hardcoded character name in {path}")


# ===========================================================================
# 5. Real loopback HTTP surface
# ===========================================================================


@POSIX_ONLY
class V02InquiryScorecardHTTPTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import autonomy_rhythm_fixture as fixture
        from components.phone import capability as phone_capability

        cls.fixture = fixture
        cls._saved_env = dict(os.environ)
        os.environ["CHIYO_WORLD_LOCAL_OWNER_PHONE"] = SUBJECT
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="v08-inquiry-scorecard-http-"))
        cls.server_root = cls.temp_dir / "instance"
        cls.capability = phone_capability.Capability(
            token=CAP_TOKEN,
            subject=SUBJECT,
            endpoint="http://127.0.0.1:1",
            issued_at="2026-11-16T08:00:00+08:00",
        )
        cls.stack = fixture.build_stack(cls.server_root, port=0, capability=cls.capability)
        fixture.serve_stack(cls.stack)
        cls.base_url = cls.stack.base

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixture.close_stack(cls.stack)
        shutil.rmtree(cls.temp_dir, ignore_errors=True)
        os.environ.clear()
        os.environ.update(cls._saved_env)

    def setUp(self) -> None:
        for rel in (
            "state/v02_agency_depth_state.json",
            "phone/v02_runtime_migration.json",
        ):
            (self.stack.root / rel).unlink(missing_ok=True)
        self.stack.service._world_clock = lambda: datetime(
            2026, 11, 16, 9, 30, tzinfo=timezone(timedelta(hours=8))
        )

    def _http(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        csrf: bool = False,
        capability: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        raw = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.base_url + path, data=raw, method=method)
        if csrf:
            req.add_header("X-CSRF-Token", self.stack.csrf_token)
        if capability is not None:
            req.add_header("X-Phone-Capability", capability)
        if raw is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def _open(self, question: str, *, shared: bool) -> str:
        code, doc = self._http(
            "POST",
            "/api/phone/v02/agency-depth",
            {"action": "open_bounded_inquiry", "question": question, "shared": shared},
            csrf=True,
            capability=CAP_TOKEN,
        )
        self.assertEqual(code, 200, doc)
        return doc["data"]["inquiries"][0]["inquiry_id"]

    def test_01_runtime_migration_mounts_scorecard_zero_write(self) -> None:
        before = _snapshot_tree(self.stack.root)
        code, res = self._http("GET", "/api/phone/v02/runtime-migration")
        self.assertEqual(code, 200, res)
        self.assertEqual(_snapshot_tree(self.stack.root), before)
        sc = res["data"]["antipuppet_scorecard"]
        self.assertEqual(sc["schema"], "nyairo.v02.antipuppet_scorecard.v1")
        self.assertTrue(sc["averaging_forbidden"])
        self.assertTrue(sc["hard_fail_triggers_high"])
        self.assertEqual(sc["llm_calls"], 0)
        self.assertEqual(len(sc["r0_r5_checks"]), 6)
        self.assertEqual(len(sc["dimensions"]), 5)
        self.assertIn(sc["overall_risk"], {"LOW", "MEDIUM", "HIGH"})

    def test_02_share_inquiry_clue_csrf_and_zero_existence_leak(self) -> None:
        path = "/api/phone/v02/agency-depth"
        public_id = self._open("公开探究", shared=True)
        private_id = self._open("私密探究", shared=False)

        # 缺 CSRF：服务端先拦。
        code, res = self._http(
            "POST",
            path,
            {"action": "share_inquiry_clue", "inquiry_id": public_id, "clue_text": "一条线索"},
        )
        self.assertEqual(code, 403, res)

        # 伴侣带 CSRF：公开问题放行。
        code, res = self._http(
            "POST",
            path,
            {
                "action": "share_inquiry_clue",
                "inquiry_id": public_id,
                "clue_text": "一条线索",
                "operation_key": "http-clue-1",
            },
            csrf=True,
        )
        self.assertEqual(code, 200, res)
        self.assertEqual(res["status"], "applied")
        public = next(i for i in res["data"]["inquiries"] if i["inquiry_id"] == public_id)
        self.assertEqual(public["partner_clues"][0]["clue_text"], "一条线索")

        # 私密/不存在：同一 404，不泄漏存在性。
        for target in (private_id, "inq_9999"):
            code, res = self._http(
                "POST",
                path,
                {"action": "share_inquiry_clue", "inquiry_id": target, "clue_text": "x"},
                csrf=True,
            )
            self.assertEqual(code, 404, res)
            self.assertEqual(res["reason_code"], "INQUIRY_NOT_FOUND")

        # 伴侣不能代记证据。
        code, res = self._http(
            "POST",
            path,
            {"action": "record_inquiry_evidence", "inquiry_id": public_id, "note": "x"},
            csrf=True,
        )
        self.assertEqual(code, 403, res)
        self.assertEqual(res["reason_code"], "FORBIDDEN_ACTOR_SIDE")

        # 本人结束问题后，伴侣线索 409。
        code, res = self._http(
            "POST",
            path,
            {
                "action": "conclude_or_stop_inquiry",
                "inquiry_id": public_id,
                "outcome": "STOPPED",
                "stop_reason": "先不查",
            },
            csrf=True,
            capability=CAP_TOKEN,
        )
        self.assertEqual(code, 200, res)
        code, res = self._http(
            "POST",
            path,
            {"action": "share_inquiry_clue", "inquiry_id": public_id, "clue_text": "x"},
            csrf=True,
        )
        self.assertEqual(code, 409, res)
        self.assertEqual(res["reason_code"], "INQUIRY_ALREADY_CLOSED")


if __name__ == "__main__":
    unittest.main()

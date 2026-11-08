#!/usr/bin/env python3
"""G2 A 路线唯一真实批（封闭启动适配）的离线验收。

只覆盖新接线，不重跑旧 10 段 / 27 / 18 / 旧 suite / 全库 / notes 树 / SHA：

1. 固定身份、预算、时间与模型参数；真实入口拒绝夹具替身，夹具只接 tempdir 新 scratch。
2. prepare/audit 的完整 CLI 路径：prepared/start=null/window absent/ledger=0，audit 纯读零写。
3. 自然 controller 的两个出口：无 H → NO_EFFECTIVE_HISTORY；有 H 但不可比 →
   SCENARIO_NOT_IDENTIFIABLE，且 H 的 owner 版本与正式 M0 经历版本精确对齐。
4. 显式条件 PAUSE + 时间夹具正控：采集→同轮结构门→40 影子→隔离双分支承接（RESUME），
   同一本持久 52 账本、每臂 2、第三次发送前拒、A 的写不污染 B 起点。
5. 采集/影子/承接任一阶段首次传输失败 → BATCH_ABORTED、持久停批、第二次 run 拒绝。
6. 隔离：真实子进程装配、gate off、世界/身体/Memory 写面都在本批根内；仓库默认写面零新增、
   父进程环境不变。
7. 输出只含安全字段，不含 prompt/正文/异常/headers/密钥/recent_tokens。

Decision/内容在夹具里是脚本替身；真实模型、真实 provider 采样与自然等待不在本模块。
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(REPO_ROOT), str(REPO_ROOT / "scripts"), str(REPO_ROOT / "tests"),
               str(REPO_ROOT / "components"), str(REPO_ROOT / "components" / "life" / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import g2_next_live as g2  # noqa: E402
import g2_next_offline as g2_offline  # noqa: E402
import history_choice_experiment as baseline  # noqa: E402
import history_choice_natural_v2 as v2  # noqa: E402
from g2_natural_dual_choice_probe import DualChoiceTransport  # noqa: E402
from agency_model_client import ModelCallResult  # noqa: E402


# ---------------------------------------------------------------------------------
# 公共夹具
# ---------------------------------------------------------------------------------

def _fresh_root(prefix: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix)) / "batch"


#: a 正式 profile 的绝对截止（2026-11-01T01:00）已经过期；夹具要能继续跑「窗口内」
#: 的路径，就得显式给一个未来截止。``g2.FORMAL_PROFILE`` / ``g2.DEADLINE`` 本身
#: 保持原值，身份用例仍断言历史值，真实已结束批次不受影响。
_FIXTURE_DEADLINE = datetime.now(timezone.utc) + timedelta(days=30)


def _fixture_deadline() -> datetime:
    """同一个进程内所有夹具用同一个未来截止，否则 prereg 身份比较会对不上。"""

    return _FIXTURE_DEADLINE


def _profile_for(root: Path, **overrides: Any) -> g2.LiveProfile:
    values = {"batch_root": Path(root), "deadline": _FIXTURE_DEADLINE}
    values.update(overrides)
    return g2.replace(g2.FORMAL_PROFILE, **values)


def _prepare(prefix: str) -> tuple[Path, dict[str, Any]]:
    root = _fresh_root(prefix)
    return root, g2.prepare(profile=_profile_for(root), offline_fixtures=True, root=root)


def _virtual() -> v2.VirtualTime:
    return v2.VirtualTime()


def _pause_time_hook(state: dict[str, bool]):
    """夹具时钟：PAUSE 之后把 observed_at 推到 park 时刻之后（不是自然经过）。"""

    def hook(tick: int, bundle: Any = None) -> None:
        if state.get("done") or bundle is None or tick < 3:
            return
        moment = (datetime.now(timezone.utc) + timedelta(minutes=6)).isoformat(
            timespec="milliseconds")
        try:
            bundle["cycle"].maintain_waiting(observed_at=moment)
        except Exception:
            return
        state["done"] = True

    return hook


class _FailTransport(v2.CountingTransport):
    def complete(self, *, system_prompt: str, user_prompt: str,
                 timeout_s: Optional[float] = None,
                 max_output_tokens: Optional[int] = None) -> Any:
        self.calls.append({"max_output_tokens": max_output_tokens,
                           "prompt_chars": len(user_prompt or ""), "label": self.label})
        return ModelCallResult(purpose="offline", provider=self.provider, model=self.model,
                               text="", latency_ms=1, status=200, usage_source="none",
                               failure_class="MODEL_CONNECTION")


class _AnyPickTransport(v2.CountingTransport):
    """夹具影子：对第一个可执行的候选按 RESUME→CONTINUE 作答；不改合法集。"""

    def complete(self, *, system_prompt: str, user_prompt: str,
                 timeout_s: Optional[float] = None,
                 max_output_tokens: Optional[int] = None) -> Any:
        self.calls.append({"max_output_tokens": max_output_tokens,
                           "prompt_chars": len(user_prompt or ""), "label": self.label})
        try:
            payload = json.loads(user_prompt or "{}")
        except ValueError:
            payload = {}
        for row in (payload.get("candidates") or []):
            if not isinstance(row, Mapping):
                continue
            legal = [str(code) for code in (row.get("legal_decisions") or ())]
            for decision in ("RESUME", "CONTINUE"):
                if decision in legal:
                    answer = {"decision": decision,
                              "selected_candidate_id": str(row.get("candidate_id") or ""),
                              "reason_codes": ["OPEN_ENDED_CONTINUATION"]}
                    return ModelCallResult(
                        purpose="offline", provider=self.provider, model=self.model,
                        text=json.dumps(answer, ensure_ascii=False), latency_ms=1,
                        status=200, usage_source="none")
        return ModelCallResult(
            purpose="offline", provider=self.provider, model=self.model,
            text=json.dumps({"decision": "NO_ACTION",
                             "reason_codes": ["NO_ACTION_CHOSEN"]}),
            latency_ms=1, status=200, usage_source="none")


def _run_dual(root: Path, *, hook_state: Optional[dict[str, bool]] = None,
              cognition_factory: Any = None, content_factory: Any = None) -> dict[str, Any]:
    virtual = _virtual()
    return g2.run(
        profile=_profile_for(root),
        execute_approved=True, offline_fixtures=True, root=root,
        decision_transport=DualChoiceTransport(),
        content_transport=v2.ScriptedAppendTransport(),
        cognition_factory=cognition_factory, content_factory=content_factory,
        clock=virtual.monotonic, sleep=virtual.sleep,
        total_window_seconds=3600, acquisition_window_seconds=1800,
        max_observations=8,
        fixture_hook=(_pause_time_hook(hook_state) if hook_state is not None else None))


def _public_blob(payload: Mapping[str, Any]) -> str:
    return json.dumps(g2._public_run(payload), ensure_ascii=False, default=str)


#: 仓库默认 World 写面（与 e 的既有隔离断言同一组）。
REPO_DEFAULT_PATHS = (
    REPO_ROOT / "data" / "world" / "run" / "action_lifecycle.jsonl",
    REPO_ROOT / "data" / "world" / "run" / "activity_state.json",
    REPO_ROOT / "data" / "world" / "run" / "experience_candidates.jsonl",
    REPO_ROOT / "data" / "world" / "run" / "self_maintenance_state.json",
    REPO_ROOT / "data" / "world" / "data" / "life_execution.jsonl",
)

_ENV_NAMES = ("WORLD_BODY_INTENT_MUTATION_GATE", "CHIYO_WORLD_HOME", "HERMES_HOME",
              "WORLD_ACTION_AUTH_REGISTRY_DIR", "WORLD_ACTION_LIFECYCLE_PATH",
              "NYA02_MODEL_PROVIDER", "NYA02_MODEL_BASE_URL", "NYA02_MODEL_NAME",
              "NYA02_MODEL_API_KEY")


def _bounded_snapshot(paths) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in paths:
        try:
            stat = Path(path).stat()
            out[str(path)] = {"exists": True, "size": stat.st_size,
                              "mtime_ns": stat.st_mtime_ns}
        except FileNotFoundError:
            out[str(path)] = {"exists": False}
    return out


def _snapshot_diff(before: Mapping[str, Mapping[str, Any]],
                   after: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    return {key: {"before": before[key], "after": after.get(key)}
            for key in before if before[key] != after.get(key)}


def _tree_snapshot(root: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for directory, _dirnames, filenames in os.walk(str(root)):
        for name in filenames:
            path = Path(directory) / name
            try:
                stat = path.stat()
                out[str(path)] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
            except OSError:
                out[str(path)] = {"exists": True}
    return out


# ---------------------------------------------------------------------------------
# 1. 固定身份与入口
# ---------------------------------------------------------------------------------

class IdentityAndEntryTest(unittest.TestCase):
    def test_fixed_identity_caps_and_parameters(self):
        profile = g2.FORMAL_PROFILE
        self.assertEqual(str(profile.batch_root), "/root/nyairo-g2-natural-20261031-a")
        self.assertEqual(str(profile.main_root), "/root/nyairo-g2-natural-20261031-a/run/main")
        self.assertEqual(str(profile.authority_db),
                         "/root/nyairo-g2-natural-20261031-a/run/memory-authority/authority.db")
        self.assertEqual(str(profile.branches_root),
                         "/root/nyairo-g2-natural-20261031-a/run/branches")
        self.assertEqual(profile.run_id, "run:g2-natural-20261031-a")
        self.assertEqual(profile.domain_id, "g2:natural:20261031-a")
        self.assertEqual(profile.expected_history, 185)
        self.assertEqual(profile.request_cap, 52)
        self.assertEqual((profile.acquisition_cap, profile.shadow_cap,
                          profile.continuation_cap, profile.diagnostic_cap), (8, 40, 4, 0))
        self.assertEqual(profile.arm_cap, 2)
        self.assertEqual(profile.acquisition_window_seconds, 1800)
        self.assertEqual(profile.observation_interval_seconds, 60)
        self.assertEqual(profile.max_observations, 31)
        self.assertEqual(profile.total_window_seconds, 5400)
        self.assertEqual(profile.deadline.isoformat(), "2026-11-01T01:00:00+08:00")
        params = profile.model_params()
        self.assertEqual(params["provider"], "custom")
        self.assertEqual(params["base_url"], "https://gw.example.invalid/v1")
        self.assertEqual(params["model"], "cn:deepseek-v4.1-flash")
        self.assertEqual(params["temperature"], 0.0)
        self.assertIs(params["stream"], False)
        self.assertEqual((params["decision_output_cap"], params["content_output_cap"]),
                         (4096, 2048))
        self.assertNotIn("api_key", json.dumps(params).lower())

    def test_real_path_refuses_fixture_doubles(self):
        result = g2.run(execute_approved=True, offline_fixtures=False,
                        decision_transport=v2.CountingTransport("{}"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason_code"], g2.R_FIXTURE_IN_REAL_PATH)
        result = g2.run(execute_approved=False, offline_fixtures=False)
        self.assertEqual(result["reason_code"], g2.R_EXECUTE_FLAG_MISSING)

    def test_model_parameters_must_match_and_never_echo_the_key(self):
        self.assertEqual(g2._verify_model_config({})["reason_code"],
                         g2.R_NO_MODEL_CONFIG)
        wrong = g2._verify_model_config({
            "NYA02_MODEL_PROVIDER": "custom",
            "NYA02_MODEL_BASE_URL": "https://wrong.invalid/v1",
            "NYA02_MODEL_NAME": "wrong-model",
            "NYA02_MODEL_API_KEY": "sk-must-not-leak"})
        self.assertEqual(wrong["reason_code"], g2.R_MODEL_PARAM_MISMATCH)
        blob = json.dumps(wrong, ensure_ascii=False)
        self.assertNotIn("sk-must-not-leak", blob)
        good = g2._verify_model_config({
            "NYA02_MODEL_PROVIDER": g2.MODEL_PROVIDER,
            "NYA02_MODEL_BASE_URL": g2.MODEL_BASE_URL,
            "NYA02_MODEL_NAME": g2.MODEL_NAME,
            "NYA02_MODEL_API_KEY": "sk-must-not-leak"})
        self.assertTrue(good["ok"], good)
        self.assertNotIn("sk-must-not-leak", json.dumps(good, ensure_ascii=False))

    def test_fixture_root_must_be_a_temp_scratch(self):
        formal = g2.prepare(offline_fixtures=True, root=g2.FORMAL_BATCH_ROOT)
        self.assertEqual(formal["reason_code"], g2.R_FIXTURE_ROOT_REFUSED)
        outside = Path(tempfile.mkdtemp(prefix="g2-outside-")) / "batch"
        refused = g2.prepare(offline_fixtures=False, root=outside)
        self.assertEqual(refused["reason_code"], g2.R_FIXTURE_ROOT_REFUSED)

    def test_public_output_is_whitelisted(self):
        dirty = {"ok": True, "result": "NO_EFFECTIVE_HISTORY",
                 "prompt": "SECRET_PROMPT", "completion": "SECRET_COMPLETION",
                 "recent_tokens": [1, 2], "api_key": "sk-secret",
                 "headers": {"Authorization": "secret"},
                 "nested": {"reasoning": "SECRET_REASONING"}}
        blob = json.dumps(g2._public_run(dirty), ensure_ascii=False)
        for forbidden in ("SECRET_PROMPT", "SECRET_COMPLETION", "recent_tokens",
                          "api_key", "Authorization", "SECRET_REASONING"):
            self.assertNotIn(forbidden, blob)
        self.assertIn("NO_EFFECTIVE_HISTORY", blob)

    def test_cli_real_run_is_closed_without_prepare(self):
        # 正式根不存在时，run 在读 prereg 之前就拒绝，不写任何文件。
        with contextlib.redirect_stdout(io.StringIO()):
            code = g2.main(["run", "--execute-approved"])
        self.assertEqual(code, g2.EXIT_REFUSED)


# ---------------------------------------------------------------------------------
# 2. prepare / audit 与 CLI
# ---------------------------------------------------------------------------------

class PrepareAuditTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root, cls.prepared = _prepare("g2-audit-")

    def test_prepare_layout_and_prereg(self):
        self.assertTrue(self.prepared["ok"], self.prepared)
        self.assertEqual(self.prepared["state"], "prepared")
        self.assertIsNone(self.prepared["started_at"])
        self.assertEqual(self.prepared["ledger"]["run_window_calls"], 0)
        self.assertTrue(g2.FORMAL_PROFILE.deadline.isoformat())
        prereg = g2._read_json(self.root / "experiment" / "g2-natural.json")
        self.assertEqual(prereg["run_id"], g2.RUN_ID)
        self.assertEqual(prereg["domain_id"], g2.DOMAIN_ID)
        self.assertEqual(prereg["history"], {"used": 185, "total_cap": 300, "new_cap": 52})
        self.assertEqual(prereg["caps"]["acquisition"], 8)
        self.assertEqual(prereg["caps"]["shadow"], 40)
        self.assertEqual(prereg["caps"]["continuation"], 4)
        self.assertEqual(prereg["caps"]["gate"], "off")
        self.assertTrue((self.root / "run" / "main").is_dir())
        self.assertTrue((self.root / "run" / "memory-authority" / "authority.db").exists())
        self.assertTrue((self.root / "run" / "branches").is_dir())

    def test_audit_is_pure_read_and_reports_the_expected_state(self):
        before = _tree_snapshot(self.root)
        result = g2.audit(profile=_profile_for(self.root), offline_fixtures=True, root=self.root)
        after = _tree_snapshot(self.root)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["state"], "prepared")
        self.assertIsNone(result["started_at"])
        self.assertEqual(result["window"], {"present": False})
        self.assertEqual(result["ledger"]["run_window_calls"], 0)
        self.assertEqual(result["history"], 185)
        self.assertEqual(result["caps"]["total"], 52)
        self.assertEqual(before, after, "audit wrote into the experiment root")

    def test_duplicate_prepare_and_authority_occupied(self):
        again = g2.prepare(profile=_profile_for(self.root), offline_fixtures=True, root=self.root)
        self.assertEqual(again["reason_code"], g2.R_ALREADY_PREPARED)
        occupied = _fresh_root("g2-authority-")
        (occupied / "run" / "memory-authority").mkdir(parents=True)
        (occupied / "run" / "memory-authority" / "authority.db").write_text("x")
        refused = g2.prepare(profile=_profile_for(occupied), offline_fixtures=True, root=occupied)
        self.assertEqual(refused["reason_code"], g2.R_AUTHORITY_NOT_FRESH)

    def test_wrong_identity_half_state_and_bad_ledger_fail_closed(self):
        scratch = Path(tempfile.mkdtemp(prefix="g2-audit-bad-"))
        wrong = scratch / "wrong"
        (wrong / "experiment").mkdir(parents=True)
        g2._write_json(wrong / "experiment" / "g2-natural.json", {
            "run_id": "run:someone-else", "domain_id": g2.DOMAIN_ID,
            "root": str(wrong), "deadline": g2.DEADLINE.isoformat(),
            "caps": {"expected_history": 185, "total": 52}})
        result = g2.audit(profile=_profile_for(wrong), offline_fixtures=True, root=wrong)
        self.assertEqual(result["reason_code"], g2.R_IDENTITY_MISMATCH)

        half = scratch / "half"
        (half / "experiment").mkdir(parents=True)
        g2._write_json(half / "experiment" / "g2-natural.json", {
            "run_id": g2.RUN_ID, "domain_id": g2.DOMAIN_ID, "root": str(half),
            "deadline": _fixture_deadline().isoformat(),
            "caps": {"expected_history": 185, "total": 52},
            "state_root": str(half / "run" / "main" / "state")})
        self.assertEqual(g2.audit(profile=_profile_for(half), offline_fixtures=True, root=half)["reason_code"],
                         g2.R_HALF_STATE)

        ledger_root = scratch / "ledger"
        main = ledger_root / "run" / "main"
        state = main / "state"
        state.mkdir(parents=True)
        (state / "life_request_budget.json").write_text("{not json")
        (ledger_root / "experiment").mkdir(parents=True)
        g2._write_json(ledger_root / "experiment" / "g2-natural.json", {
            "run_id": g2.RUN_ID, "domain_id": g2.DOMAIN_ID, "root": str(ledger_root),
            "deadline": _fixture_deadline().isoformat(),
            "caps": {"expected_history": 185, "total": 52},
            "state_root": str(state)})
        g2._write_json(ledger_root / "experiment" / "g2_batch.json", {
            "run_id": g2.RUN_ID, "state": "prepared", "run": None})
        self.assertEqual(g2.audit(profile=_profile_for(ledger_root), offline_fixtures=True, root=ledger_root)["reason_code"],
                         g2.R_LEDGER_UNREADABLE)

    def test_prepare_and_audit_do_not_need_model_keys(self):
        for name in _ENV_NAMES:
            if name.startswith("NYA02_MODEL_"):
                self.assertNotIn(name, os.environ)
        self.assertTrue(self.prepared["ok"])
        self.assertTrue(g2.audit(profile=_profile_for(self.root), offline_fixtures=True, root=self.root)["ok"])


class CliFixtureTest(unittest.TestCase):
    def test_cli_prepare_audit_run_and_second_run_refused(self):
        root = _fresh_root("g2-cli-")
        # CLI 没有 --deadline；只在本用例作用域内把默认 profile 换成未来截止，
        # 正式的 FORMAL_PROFILE / DEADLINE 常量保持原值。
        fixture = _profile_for(root)
        with mock.patch.object(g2, "FORMAL_PROFILE", fixture), \
                mock.patch.object(g2, "DEADLINE", _FIXTURE_DEADLINE), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(g2.main(["prepare", "--offline-fixtures", "--root", str(root)]),
                             g2.EXIT_OK)
            self.assertEqual(g2.main(["audit", "--offline-fixtures", "--root", str(root)]),
                             g2.EXIT_OK)
            self.assertEqual(g2.main(["run", "--execute-approved", "--offline-fixtures",
                                      "--root", str(root)]), g2.EXIT_OK)
            again = g2.main(["run", "--execute-approved", "--offline-fixtures",
                             "--root", str(root)])
        self.assertEqual(again, g2.EXIT_REFUSED)
        batch = g2._read_json(root / "experiment" / "g2_batch.json")
        self.assertTrue(batch.get("run", {}).get("started_at"))
        self.assertEqual(batch.get("result"), g2.RESULT_NO_H)
        self.assertTrue((root / "experiment" / "g2_window.json").exists())


# ---------------------------------------------------------------------------------
# 3. 自然 controller 的两个出口
# ---------------------------------------------------------------------------------

class NaturalExitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.quiet_root, cls.quiet_prepared = _prepare("g2-quiet-")
        quiet_virtual = _virtual()
        cls.quiet = g2.run(
            profile=_profile_for(cls.quiet_root),
            execute_approved=True, offline_fixtures=True, root=cls.quiet_root,
            clock=quiet_virtual.monotonic, sleep=quiet_virtual.sleep,
            total_window_seconds=300, acquisition_window_seconds=60, max_observations=2)

        cls.h_root, cls.h_prepared = _prepare("g2-h-")
        cls.h_run = _run_dual(cls.h_root, hook_state=None)

    def test_no_history_exit_does_not_force_requests(self):
        run = self.quiet
        self.assertTrue(run["ok"], run)
        self.assertEqual(run["result"], g2.RESULT_NO_H)
        self.assertIsNone(run["acquisition"]["h_ref"])
        self.assertLessEqual(int(run["acquisition"]["requests_used"]), 1)
        self.assertLessEqual(int(run["requests_used"]), 1)
        self.assertIn(run["acquisition"]["stop_reason"],
                      {v2.STOP_WINDOW_ELAPSED, v2.STOP_OBSERVATION_LIMIT,
                       v2.STOP_REQUEST_CAP, v2.STOP_DEADLINE_REACHED})

    def test_h_established_but_scenario_not_identifiable(self):
        run = self.h_run
        self.assertTrue(run["ok"], run)
        self.assertEqual(run["acquisition"]["result"], g2.RESULT_NOT_IDENTIFIABLE)
        self.assertEqual(run["result"], g2.RESULT_NOT_IDENTIFIABLE)
        self.assertTrue(run["acquisition"]["h_ref"])
        self.assertTrue(run["acquisition"]["h_evidence"]["version_aligned"])
        self.assertEqual(run["acquisition"]["h_evidence"]["owner_version_id"],
                         run["acquisition"]["h_evidence"]["experience_version_ref"])
        self.assertIn(run["acquisition"]["gate"],
                      {baseline.REASON_SECOND_WORK_MISSING,
                       baseline.REASON_SCENARIO_NOT_IDENTIFIABLE})
        self.assertIsNone(run["shadow"])
        self.assertLessEqual(int(run["requests_used"]), 8)

    def test_second_run_is_refused(self):
        for root in (self.quiet_root, self.h_root):
            second = g2.run(profile=_profile_for(root),
                            execute_approved=True, offline_fixtures=True, root=root)
            self.assertEqual(second["reason_code"], g2.R_ALREADY_STARTED)


# ---------------------------------------------------------------------------------
# 4. 双合法正控：40 影子 + 隔离双分支 RESUME 承接
# ---------------------------------------------------------------------------------

class FullPositiveControlTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root, cls.prepared = _prepare("g2-full-")
        cls.result = _run_dual(cls.root, hook_state={"done": False})
        cls.profile = g2.replace(g2.FORMAL_PROFILE, batch_root=cls.root)

    def test_scenario_frozen_and_h_version_aligned(self):
        run = self.result
        self.assertTrue(run["ok"], run)
        self.assertEqual(run["acquisition"]["result"], g2.RESULT_FROZEN)
        self.assertIs(run["acquisition"]["frozen"], True)
        self.assertTrue(run["acquisition"]["h_evidence"]["version_aligned"])
        self.assertGreaterEqual(run["gate"]["candidate_count"], 2)
        self.assertGreaterEqual(run["gate"]["executable_count"], 2)
        self.assertIs(run["gate"]["h_executable"], True)
        self.assertIs(run["gate"]["other_work_executable"], True)
        self.assertIs(run["gate"]["no_action_legal"], True)
        self.assertIs(run["gate"]["offline_transport"], True)

    def test_shadow_is_exactly_forty_and_uses_the_same_ledger(self):
        run = self.result
        self.assertEqual(run["shadow"]["pairs_complete"], 20)
        self.assertEqual(run["shadow"]["stop_reason"], "")
        acquisition_used = int(run["acquisition"]["requests_used"])
        shadow_used = int(run["shadow"]["requests_used"])
        self.assertGreaterEqual(acquisition_used, 1)
        self.assertEqual(shadow_used - acquisition_used, 40)
        self.assertEqual(run["requests_used"], 48)
        self.assertEqual(run["ledger"]["run_window_calls"], 48)
        self.assertEqual(run["ledger"]["run_window_max"], 52)
        self.assertEqual(run["statistics"]["pairs_complete"], 20)
        self.assertIn(run["statistics"]["conclusion"],
                      {"HISTORY_PACKAGE_CHANGED_H_CHOICE", "NO_DETECTED_DIFFERENCE"})
        self.assertIn(run["result"], {g2.RESULT_DETECTED, g2.RESULT_NO_DIFFERENCE})

    def test_both_arms_resume_the_same_frozen_target_with_two_requests(self):
        arms = self.result["continuation"]
        self.assertEqual(set(arms), {"A", "B"})
        for arm in ("A", "B"):
            row = arms[arm]
            self.assertEqual(row["status"], "RAN", row)
            self.assertEqual(row["decision"], "RESUME")
            self.assertEqual(row["used"], 2)
            self.assertIs(row["third_refused"], True)
            self.assertTrue(row["checks"]["same_frozen_artifact"])
            self.assertTrue(row["checks"]["same_frozen_activity"])
            self.assertTrue(row["checks"]["owner_version_created"])
            self.assertTrue(row["checks"]["new_owner_version"])
            self.assertTrue(row["checks"]["effect_applied"])
            self.assertTrue(row["checks"]["decision_matches_shadow_choice"])
        self.assertNotEqual(arms["A"]["version_ref"], arms["B"]["version_ref"])
        self.assertEqual(arms["A"]["activity_ref"], arms["B"]["activity_ref"])
        self.assertEqual(int(self.result["requests_used"]), 48)

    def test_branch_is_isolated_and_arm_a_does_not_pollute_b_start(self):
        branch = self.result["branch"]
        self.assertTrue(branch["ok"], branch)
        self.assertIs(branch["isolation_ok"], True)
        self.assertIn("NYA02_MEMORY_AUTHORITY_DB", branch["shared_authority"])
        self.assertIs(branch["b_start_unpolluted"], True)
        self.assertIs(branch["arm_versions_distinct"], True)
        self.assertEqual(branch["branch_head_before"], branch["branch_head_after_arm_a"])
        self.assertNotEqual(branch["main_head_before"], branch["main_head_after_arm_a"])
        branch_root = Path(str(self.result["continuation"]["B"]["root"]))
        self.assertEqual(branch_root, self.root / "run" / "branches" / "b")
        stage = g2._read_json(self.root / "experiment" / "g2_stages.json")
        self.assertEqual(stage["stages"]["acquisition"]["cap"], 8)
        self.assertEqual(stage["stages"]["shadow"]["cap"], 40)
        self.assertEqual(stage["stages"]["continuation"]["cap"], 4)

    def test_output_contains_no_private_or_unbounded_fields(self):
        blob = _public_blob(self.result)
        for forbidden in ("recent_tokens", "prompt", "completion", "reasoning",
                          "api_key", "Authorization", "headers"):
            self.assertNotIn(forbidden, blob)


# ---------------------------------------------------------------------------------
# 4b. 影子选择 → 执行目标的安全映射（纯单测）
# ---------------------------------------------------------------------------------

class ShadowMappingUnitTest(unittest.TestCase):
    H = "artifact:artifact:hhhh"
    O = "artifact:artifact:oooo"
    GATE = [
        {"candidate_id": "cand:h", "targets": [H], "legal": ["RESUME", "DEFER"]},
        {"candidate_id": "cand:o", "targets": [O], "legal": ["START"]},
    ]

    def test_no_action_is_not_executable_and_never_falls_back(self):
        shadow = {"samples": [{"pair": 1, "arm": "A", "decision": "NO_ACTION",
                               "selected_candidate_ref": None}]}
        choice = g2._shadow_choice(shadow, self.GATE, self.H, "A")
        self.assertFalse(choice["ok"])
        self.assertEqual(choice["reason_code"], g2.R_SHADOW_CHOICE_NOT_EXECUTABLE)

    def test_wrong_target_is_refused(self):
        shadow = {"samples": [{"pair": 1, "arm": "A", "decision": "RESUME",
                               "selected_candidate_ref": "cand:o"}]}
        choice = g2._shadow_choice(shadow, self.GATE, self.H, "A")
        self.assertFalse(choice["ok"])
        self.assertEqual(choice["reason_code"], g2.R_SHADOW_TARGET_MISMATCH)

    def test_resume_maps_only_when_legal_on_the_frozen_target(self):
        shadow = {"samples": [{"pair": 1, "arm": "A", "decision": "RESUME",
                               "selected_candidate_ref": "cand:h"}]}
        choice = g2._shadow_choice(shadow, self.GATE, self.H, "A")
        self.assertTrue(choice["ok"], choice)
        self.assertEqual(choice["decision"], "RESUME")
        self.assertEqual(choice["targets"], [self.H])


# ---------------------------------------------------------------------------------
# 5. 失败持久停批（采集 / 影子 / 承接三个具名口）
# ---------------------------------------------------------------------------------

class FailurePersistenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.acq_root, _ = _prepare("g2-fail-acq-")
        acq_virtual = _virtual()
        cls.acq = g2.run(
            profile=_profile_for(cls.acq_root),
            execute_approved=True, offline_fixtures=True, root=cls.acq_root,
            decision_transport=_FailTransport("{}", label="fail-acquisition"),
            content_transport=v2.ScriptedAppendTransport(),
            clock=acq_virtual.monotonic, sleep=acq_virtual.sleep,
            total_window_seconds=3600, acquisition_window_seconds=1800,
            max_observations=3)

        cls.shadow_root, _ = _prepare("g2-fail-shadow-")
        cls.shadow = _run_dual(
            cls.shadow_root, hook_state={"done": False},
            cognition_factory=lambda _stage, _arm=None: _FailTransport(
                "{}", label="fail-shadow"))

        cls.cont_root, _ = _prepare("g2-fail-cont-")

        def _factory(stage: str, _arm=None):
            if stage == "shadow":
                return _AnyPickTransport("{}", label="shadow-ok")
            return _FailTransport("{}", label="fail-continuation")

        cls.cont = _run_dual(cls.cont_root, hook_state={"done": False},
                             cognition_factory=_factory)

    def _assert_persisted_provider_failure(self, root: Path, run: Mapping[str, Any]):
        self.assertTrue(run["ok"], run)
        self.assertEqual(run["result"], g2.RESULT_ABORTED)
        self.assertEqual(run["stop"], g2.R_PROVIDER_FAILURE)
        stop_file = root / "experiment" / "g2_stop.json"
        self.assertTrue(stop_file.exists())
        self.assertEqual(g2._read_json(stop_file)["reason_code"], g2.R_PROVIDER_FAILURE)
        second = g2.run(profile=_profile_for(root),
                        execute_approved=True, offline_fixtures=True, root=root)
        self.assertEqual(second["reason_code"], g2.R_ALREADY_STARTED, second)

    def test_acquisition_failure_stops_before_any_retry(self):
        self._assert_persisted_provider_failure(self.acq_root, self.acq)
        self.assertEqual(int(self.acq["requests_used"]), 1)
        self.assertIsNone(self.acq["shadow"])
        self.assertIsNone(self.acq["continuation"])

    def test_shadow_failure_stops_the_batch_and_skips_continuation(self):
        self._assert_persisted_provider_failure(self.shadow_root, self.shadow)
        self.assertLess(self.shadow["shadow"]["pairs_complete"], 20)
        self.assertEqual(int(self.shadow["requests_used"]), 5)
        self.assertIsNone(self.shadow["continuation"])

    def test_continuation_failure_stops_before_arm_b(self):
        self._assert_persisted_provider_failure(self.cont_root, self.cont)
        arms = self.cont["continuation"]
        self.assertLessEqual(int(arms["A"]["used"]), 1)
        self.assertEqual(arms["B"]["status"], g2.R_NOT_RUN)
        self.assertEqual(arms["B"]["reason_code"], g2.R_PROVIDER_FAILURE)
        self.assertEqual(int(self.cont["requests_used"]), 45)

    def test_success_is_not_inferred_from_zero_errors(self):
        self.assertNotEqual(self.acq["result"], g2.RESULT_NO_DIFFERENCE)
        self.assertNotEqual(self.shadow["result"], g2.RESULT_NO_DIFFERENCE)
        self.assertNotEqual(self.cont["result"], g2.RESULT_NO_DIFFERENCE)


# ---------------------------------------------------------------------------------
# 6. 一本 52 账本 + 阶段/臂发送前硬门（纯账本单测）
# ---------------------------------------------------------------------------------

class BudgetLedgerUnitTest(unittest.TestCase):
    def _ledger(self, parent: Path, *, cap: int = 52):
        from life_request_budget import LifeRequestBudget

        budget = LifeRequestBudget(parent / "ledger", utc_day_max=300,
                                   run_window_max=cap, run_id=g2.RUN_ID)
        budget.ensure_window(allow_new=True)
        profile = _profile_for(parent)
        return budget, g2.LiveStageLedger(profile, budget)

    def test_8_40_4_share_one_ledger_and_the_53rd_is_refused(self):
        from life_request_budget import REASON_RUN_WINDOW_LIMIT

        with tempfile.TemporaryDirectory(prefix="g2-budget-") as tmp:
            parent = Path(tmp)
            budget, stages = self._ledger(parent)
            stages.start_stage("acquisition")
            for index in range(8):
                g2_offline._send_through_adapter(
                    budget, g2_offline._CountingSender(fail=(index == 7)))
            self.assertEqual(budget.view()["run_window_calls"], 8)
            with self.assertRaises(baseline.BatchRefused) as refused:
                stages.assert_room("acquisition", budget.view())
            self.assertEqual(refused.exception.reason_code, g2.R_STAGE_CAP)

            stages.start_stage("shadow")
            for _ in range(40):
                self.assertTrue(g2_offline._send_through_adapter(
                    budget, g2_offline._CountingSender())["sent"])
            self.assertEqual(budget.view()["run_window_calls"], 48)
            with self.assertRaises(baseline.BatchRefused) as shadow_full:
                stages.assert_room("shadow", budget.view())
            self.assertEqual(shadow_full.exception.reason_code, g2.R_STAGE_CAP)

            stages.start_stage("continuation")
            for arm in ("A", "B"):
                with baseline.arm_ceiling(budget, g2.ARM_REQUEST_CAP):
                    for _ in range(2):
                        self.assertTrue(g2_offline._send_through_adapter(
                            budget, g2_offline._CountingSender())["sent"])
                    third_sender = g2_offline._CountingSender()
                    third = g2_offline._send_through_adapter(budget, third_sender)
                    self.assertFalse(third["sent"])
                    self.assertEqual(third_sender.calls, 0)
                    self.assertEqual(third["reason_code"], REASON_RUN_WINDOW_LIMIT)
            self.assertEqual(budget.view()["run_window_calls"], 52)
            extra_sender = g2_offline._CountingSender()
            extra = g2_offline._send_through_adapter(budget, extra_sender)
            self.assertFalse(extra["sent"])
            self.assertEqual(extra_sender.calls, 0)
            self.assertEqual(extra["reason_code"], REASON_RUN_WINDOW_LIMIT)
            self.assertFalse(any(hasattr(budget, name)
                                 for name in ("release", "refund", "give_back")))

            from life_request_budget import LifeRequestBudget

            reopened = LifeRequestBudget(parent / "ledger", utc_day_max=300,
                                         run_window_max=52, run_id=g2.RUN_ID)
            window = reopened.ensure_window()
            self.assertEqual(window["state"], "existing")
            self.assertEqual(int(reopened.view()["run_window_calls"]), 52)

    def test_unused_acquisition_slots_are_not_transferred(self):
        with tempfile.TemporaryDirectory(prefix="g2-budget-unused-") as tmp:
            parent = Path(tmp)
            budget, stages = self._ledger(parent)
            stages.start_stage("acquisition")
            for _ in range(6):
                g2_offline._send_through_adapter(budget, g2_offline._CountingSender())
            stages.start_stage("shadow")
            room = stages.assert_room("shadow", budget.view())
            self.assertEqual(room["total_remaining"], 46)
            self.assertEqual(room["stage_remaining"], 40)
            stages.start_stage("continuation")
            room = stages.assert_room("continuation", budget.view(), reserving=2)
            self.assertEqual(room["total_remaining"], 46)
            self.assertEqual(room["stage_remaining"], 4)
            self.assertLess(46 + 4, g2.REQUEST_CAP)


# ---------------------------------------------------------------------------------
# 7. 真实子进程装配 + 默认写面零新增 + 父环境不变
# ---------------------------------------------------------------------------------

class IsolationSubprocessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root, cls.prepared = _prepare("g2-isolation-")
        cls.repo_before = _bounded_snapshot(REPO_DEFAULT_PATHS)
        cls.env_before = {name: os.environ.get(name) for name in _ENV_NAMES}
        cls.probe = cls._run_probe()

    @classmethod
    def _run_probe(cls) -> dict[str, Any]:
        code = textwrap.dedent("""
            import json, sys
            from pathlib import Path
            sys.path[:0] = [sys.argv[1], sys.argv[2]]
            import g2_next_live as g
            profile = g.replace(g.FORMAL_PROFILE, batch_root=Path(sys.argv[3]))
            try:
                out = g.phase_probe(profile, offline_fixtures=True)
                print(json.dumps({"ok": True, "out": out}, ensure_ascii=False, default=str))
            except Exception as exc:
                print(json.dumps({"ok": False, "kind": type(exc).__name__,
                                  "reason_code": getattr(exc, "reason_code", None)}))
        """)
        hostile = {
            "WORLD_BODY_INTENT_MUTATION_GATE": "canonical",
            "CHIYO_WORLD_HOME": str(REPO_ROOT / "data" / "world"),
            "HERMES_HOME": str(REPO_ROOT / "data" / "world"),
            "WORLD_ACTION_AUTH_REGISTRY_DIR": str(REPO_ROOT / "data" / "world" / "run"),
            "WORLD_ACTION_LIFECYCLE_PATH": str(REPO_ROOT / "data" / "world" / "run"
                                               / "action_lifecycle.jsonl"),
        }
        environment = {**os.environ, **hostile, "PYTHONDONTWRITEBYTECODE": "1"}
        done = subprocess.run(
            [sys.executable, "-B", "-c", code, str(REPO_ROOT), str(REPO_ROOT / "scripts"),
             str(cls.root)],
            capture_output=True, text=True, env=environment, timeout=600)
        if done.returncode != 0:
            raise AssertionError(done.stderr[-3000:])
        return json.loads(done.stdout.strip().splitlines()[-1])

    def test_subprocess_assembles_with_gate_off_and_all_paths_inside(self):
        self.assertIs(self.probe.get("ok"), True, self.probe)
        out = self.probe["out"]
        isolation = out["isolation"]
        self.assertIs(out["ok"], True)
        self.assertEqual(out["gate"], "off")
        self.assertEqual(isolation["gate"], "off")
        self.assertIs(isolation["ok"], True, isolation.get("blocking"))
        self.assertEqual(Path(isolation["world_home"]).resolve(),
                         (self.root / "run" / "main").resolve() / "one-day" / "world-home")
        self.assertTrue(g2._within(isolation["world_home"], self.root / "run" / "main"))
        self.assertEqual(Path(isolation["authority"]).resolve(),
                         (self.root / "run" / "memory-authority" / "authority.db").resolve())
        # hostile 父环境里的仓库 home 没有漏进子进程。
        self.assertNotIn(str(REPO_ROOT / "data" / "world"),
                         json.dumps(isolation, ensure_ascii=False))

    def test_repo_default_write_surfaces_get_zero_new_writes(self):
        after = _bounded_snapshot(REPO_DEFAULT_PATHS)
        self.assertEqual(_snapshot_diff(self.repo_before, after), {})

    def test_parent_environment_is_unchanged(self):
        for name in _ENV_NAMES:
            self.assertEqual(os.environ.get(name), self.env_before[name], name)


# ---------------------------------------------------------------------------------
# 8. 窗口/截止的持久边界（纯单测）
# ---------------------------------------------------------------------------------

class WindowAndDeadlineTest(unittest.TestCase):
    def test_window_is_written_once_and_expiry_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="g2-window-") as tmp:
            root = Path(tmp) / "batch"
            (root / "experiment").mkdir(parents=True)
            profile = _profile_for(root)
            window = g2._write_window(profile, total_window_seconds=100)
            self.assertEqual(window["run_id"], g2.RUN_ID)
            self.assertFalse(g2._deadline_passed(profile))
            with self.assertRaises(g2.LiveRefused) as refused:
                g2._write_window(profile, total_window_seconds=100)
            self.assertEqual(refused.exception.reason_code, g2.R_ALREADY_STARTED)
            # 缺窗口/坏窗口一律视为已到期。
            (root / "experiment" / "g2_window.json").unlink()
            self.assertTrue(g2._deadline_passed(profile))

    def test_tiny_total_window_is_expired_immediately(self):
        with tempfile.TemporaryDirectory(prefix="g2-window-zero-") as tmp:
            root = Path(tmp) / "batch"
            (root / "experiment").mkdir(parents=True)
            profile = _profile_for(root)
            g2._write_window(profile, total_window_seconds=0)
            self.assertTrue(g2._deadline_passed(profile))

    def test_plan_keeps_acquisition_and_total_windows_separate(self):
        plan = g2._plan_for(g2.FORMAL_PROFILE)
        self.assertEqual(plan.window_seconds, 1800)
        self.assertEqual(plan.observation_interval_seconds, 60)
        self.assertEqual(plan.max_observations, 31)
        self.assertEqual(plan.request_cap, 8)
        self.assertEqual(plan.future_shadow_requests, 40)
        self.assertEqual(plan.future_continuation_requests, 4)
        self.assertEqual(plan.arm_request_cap, 2)
        self.assertEqual(g2.FORMAL_PROFILE.total_window_seconds, 5400)

    def test_ledger_direct_read_never_creates_the_lock(self):
        from life_request_budget import lock_path

        with tempfile.TemporaryDirectory(prefix="g2-ledger-direct-") as tmp:
            state = Path(tmp) / "state"
            ledger = g2._read_ledger_direct(g2.FORMAL_PROFILE, state)
            self.assertEqual(ledger["run_window_calls"], 0)
            self.assertFalse(ledger["run_window_present"])
            self.assertFalse(lock_path(state).exists())


if __name__ == "__main__":
    unittest.main()

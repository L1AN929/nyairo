#!/usr/bin/env python3
"""e 批（世界动作临时许可的一次自然观测）的最小离线验收。

只测这一批新增的边界，不重跑 a/b/c/d 的历史套件：

1. e 是第五个封闭身份；缺省仍是 a，``BATCH_PROFILES`` 仍是 a/b/c，
   旧 ``BATCH_CHOICES`` ("a","b","c","d") 一个值都没动，CLI 只多认一个 e。
2. gate 是每批自己的固定值：e=canonical，d=显式 off，a/b/c 保持原样继承。
   父进程的 canonical 不能漏进 d；**本进程 ``os.environ`` 一个字节都不改**——
   “子进程 env 映射”这件事只在真实子进程里验证，不 mock 父环境。
3. 三处 env 映射一致：runtime env、phase 子进程 env、自有 World child env。
4. e 的 prepare 与 natural phase 是**真**标准装配（真 Supply/World/runtime），
   材料助手与 ``SubmissionInjector`` 在真实 phase 子进程里被打成 raise（证明路径根本不调用）。
5. e 经正式 AG-1→AG-2→AR-0 标准许可，至少一个真实 Action 成功并留下 owner 效果；
   d 的同一个标准 adapter 在 gate off 下仍明确 ``GATE_OFF``、零回执。
6. 错身份/缺 gate 字段的 phase 被拒；同根不重跑；cap 用满即停；摘要只认 e 根。
7. World 写面隔离：phase 子进程自己的 gate/home/registry/lifecycle 都落在新根；
   仓库默认 ``data/world/...`` 在同模块前后只做存在/大小/mtime_ns 有界比较，零新增。
8. e 的路径守卫：实例 world_home 缺失/不可读/相对/解析后落在根外（含符号链接逃逸）时，
   在起服务、发请求、写 started/window 之前明确 ``Refused``，绝不借用父环境的 home；
   phase 子进程进正式 phase 前先自查自己的实际 env 与固定映射逐项相符。

Decision 是明确标注的测试替身（只从真实候选里挑），真实入口不写死任何选择。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(REPO_ROOT), str(REPO_ROOT / "scripts"), str(REPO_ROOT / "tests"),
               str(REPO_ROOT / "components"), str(REPO_ROOT / "components" / "life" / "scripts"),
               str(REPO_ROOT / "components" / "supply"),
               str(REPO_ROOT / "components" / "world" / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import g1_real_adoption as g1  # noqa: E402
import world_observe_e_summary as summary  # noqa: E402

# e 的注册截止已过真实墙钟。本文件所有根都在 mkdtemp 下，因此显式进入本进程的离线夹具
# 参考时刻（本批截止前 1 小时）。它只改本进程内存，不写环境变量、不写任何持久字段；
# profile 常量与真实 run 语义不动，真实根带它会在写入之前被拒。需要另起解释器的子进程
# 自己显式进入同一夹具（下面脚本里那一行），不靠环境变量传播。
g1.enter_offline_fixture(g1.E_PROFILE)

GATE_ENV = "WORLD_BODY_INTENT_MUTATION_GATE"
#: 本进程启动时父 shell 的这些值（预期 UNSET）。测试只要求**前后不变**，
#: 不把开发者机器上的偶发设置当成业务失败。
_ENV_BEFORE = {name: os.environ.get(name) for name in (
    GATE_ENV, "CHIYO_WORLD_HOME", "HERMES_HOME", "WORLD_ACTION_AUTH_REGISTRY_DIR",
    "WORLD_ACTION_LIFECYCLE_PATH")}

#: 仓库默认写面（`default_path()` 在没有任何 World home/显式路径时的回退）。
REPO_DEFAULT_PATHS = (
    REPO_ROOT / "data" / "world" / "run" / "action_lifecycle.jsonl",
    REPO_ROOT / "data" / "world" / "run" / "activity_state.json",
    REPO_ROOT / "data" / "world" / "run" / "experience_candidates.jsonl",
    REPO_ROOT / "data" / "world" / "run" / "self_maintenance_state.json",
    REPO_ROOT / "data" / "world" / "data" / "life_execution.jsonl",
)


def _bounded_snapshot(paths) -> dict[str, dict[str, Any]]:
    """存在/大小/mtime_ns 三项，不算 SHA、不全树扫。"""

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


def _child_gate_probe(extra_env: Mapping[str, str]) -> dict[str, Any]:
    """真实新子进程里读 ``_phase_child_env``：不碰本测试进程的 os.environ。"""

    code = textwrap.dedent("""
        import json, sys
        sys.path[:0] = [sys.argv[1], sys.argv[2]]
        import g1_real_adoption as g1
        gate = sys.argv[3]
        out = {}
        for key, profile in (("a", g1.BATCH_PROFILES["a"]),
                             ("d", g1.D_PROFILE), ("e", g1.E_PROFILE)):
            out[key] = g1._phase_child_env(profile).get(gate)
        print(json.dumps(out))
    """)
    environment = {**os.environ, **{str(k): str(v) for k, v in extra_env.items()}}
    done = subprocess.run(
        [sys.executable, "-B", "-c", code, str(REPO_ROOT),
         str(REPO_ROOT / "scripts"), GATE_ENV],
        capture_output=True, text=True, env=environment)
    if done.returncode != 0:
        raise AssertionError(done.stderr[-2000:])
    return json.loads(done.stdout.strip().splitlines()[-1])


def _phase_report(root: Path) -> dict[str, Any]:
    return g1._read_json(g1._experiment_dir(root) / (g1.PHASE_FILENAME % g1.PHASE_NATURAL))


def _runtime_mapping(root: Path, batch_key: str) -> dict[str, str]:
    with g1._use_batch(batch_key):
        return g1._runtime_env(root, offline=True)


def _world_home(root: Path) -> Path:
    from components.phone import service as phone_service

    return Path(str(dict(phone_service.load_instance(root)).get("world_home") or ""))


def _world_file(world_home: Path, name: str) -> dict[str, Any]:
    return dict(g1._read_json(world_home / "data" / "world" / name) or {})


def _journey_state(world_home: Path) -> dict[str, Any]:
    import chiyo_bundle.world_journey as world_journey

    return dict(world_journey.load_state(world_home) or {})


class _Artifacts:
    """一个已完成自然 phase 的只读事实（全部从本根文件读，不再跑任何动作）。"""

    def __init__(self, root: Path, batch_key: str) -> None:
        self.root = root
        self.batch_key = batch_key
        self.phase = _phase_report(root)
        self.env = _runtime_mapping(root, batch_key)
        self.state_root = Path(str(self.env.get("NYA02_RESIDENT_STATE") or ""))
        self.state = dict(g1._read_json(
            self.state_root / "action_reality_state.json") or {})
        self.world_home = _world_home(root)
        isolation = self.phase.get("isolation") or {}
        lifecycle = str(isolation.get("lifecycle_path")
                        or (self.world_home / "run" / "action_lifecycle.jsonl"))
        self.lifecycle_path = Path(lifecycle)
        # 直接从本根 ledger 读有界安全记录（存在才读；不碰仓库默认路径）。
        self.lifecycle = (g1._lifecycle_safe_records(self.lifecycle_path)
                          if self.lifecycle_path.exists() else [])
        self.actions = [dict(row) for row in (self.state.get("actions") or {}).values()]
        self.proposals = dict(self.state.get("proposals") or {})
        self.attempts = dict(self.state.get("attempts") or {})
        self.receipts = dict(self.state.get("receipts") or {})
        self.body = _world_file(self.world_home, "body_substrate.json")
        self.journey = _journey_state(self.world_home) if self.world_home else {}
        self.world_state = _world_file(self.world_home, "world_state.json")

    def payload(self, action: Mapping[str, Any]) -> dict[str, Any]:
        proposal = dict(self.proposals.get(str(action.get("proposal_ref") or "")) or {})
        return dict(proposal.get("payload_data") or {})

    def attempts_for(self, action_id: str) -> list[dict[str, Any]]:
        rows = [dict(row) for row in self.attempts.values()
                if str(row.get("action_id") or "") == action_id]
        rows.sort(key=lambda row: int(row.get("attempt_no") or 0))
        return rows

    def receipts_for(self, action_id: str) -> list[dict[str, Any]]:
        return [dict(row) for row in self.receipts.values()
                if str(row.get("action_id") or "") == action_id]

    def lifecycle_for(self, execution_id: str) -> list[dict[str, Any]]:
        return [dict(row) for row in self.lifecycle
                if str(row.get("execution_id") or "") == execution_id]


class EProfileTest(unittest.TestCase):
    """e 是第五个封闭身份；旧身份/旧枚举/旧 gate 行为一个都不动。"""

    def test_e_is_the_closed_fifth_identity(self):
        e = g1.E_PROFILE
        self.assertEqual(str(e.root), "/root/nyairo-world-observe-20261031-e")
        self.assertEqual(str(e.authority_dir),
                         "/root/nyairo-world-observe-20261031-e-memory-authority")
        self.assertEqual(str(e.authority_db),
                         "/root/nyairo-world-observe-20261031-e-memory-authority/authority.db")
        self.assertEqual(e.run_id, "run:world-observe-20261031-e")
        self.assertEqual(e.domain_id, "world-observe:20261031-e")
        self.assertEqual(e.expected_history, 148)
        self.assertEqual(e.deadline.isoformat(), "2026-11-01T01:00:00+08:00")
        self.assertIs(g1._identity(), g1.BATCH_PROFILES["a"])
        self.assertIs(g1._select_batch("e"), e)
        self.assertTrue(g1._is_natural(e))
        self.assertTrue(g1._is_natural(g1.D_PROFILE))
        for key in ("a", "b", "c"):
            self.assertFalse(g1._is_natural(g1.BATCH_PROFILES[key]), key)

    def test_old_abc_and_d_identity_are_untouched(self):
        self.assertEqual(set(g1.BATCH_PROFILES), {"a", "b", "c"})
        self.assertEqual(g1.BATCH_CHOICES, ("a", "b", "c", "d"))
        self.assertEqual(g1.CLI_BATCH_CHOICES, ("a", "b", "c", "d", "e"))
        self.assertIs(g1.MODE_PROFILES["d"], g1.D_PROFILE)
        self.assertIs(g1.MODE_PROFILES["e"], g1.E_PROFILE)
        self.assertEqual({key: value.deadline.isoformat()
                          for key, value in g1.BATCH_PROFILES.items()},
                         {"a": "2026-10-30T17:00:00+08:00",
                          "b": "2026-10-30T17:00:00+08:00",
                          "c": "2026-10-31T01:00:00+08:00"})
        self.assertEqual(g1.D_PROFILE.expected_history, 141)
        self.assertEqual(str(g1.D_PROFILE.root), "/root/nyairo-pause-observe-20261030-d")

    def test_gate_is_per_batch_and_never_inherited_by_d(self):
        self.assertEqual(g1._world_intent_gate(g1.E_PROFILE), "canonical")
        self.assertEqual(g1._world_intent_gate(g1.D_PROFILE), "off")
        for key in ("a", "b", "c"):
            self.assertIsNone(g1._world_intent_gate(g1.BATCH_PROFILES[key]), key)
        # 父 shell 带着 canonical：真实子进程里构造的三份映射，d 仍必须显式 off，
        # a 原样继承，e 是自己的 canonical。这里没有 mock.patch.dict(os.environ)。
        probed = _child_gate_probe({GATE_ENV: "canonical"})
        self.assertEqual(probed["a"], "canonical")
        self.assertEqual(probed["d"], "off")
        self.assertEqual(probed["e"], "canonical")
        # 本进程环境自始至终没被改过。
        for name, value in _ENV_BEFORE.items():
            self.assertEqual(os.environ.get(name), value, name)

    def test_each_natural_root_is_refused_by_the_other(self):
        with g1._use_batch("e"):
            self.assertIsNone(g1._check_root(g1.E_PROFILE.root))
            for profile in (g1.D_PROFILE, g1.BATCH_PROFILES["a"]):
                refused = g1._check_root(profile.root)
                self.assertEqual(refused.get("reason_code"),
                                 g1.REASON_BATCH_IDENTITY_MISMATCH)
        with g1._use_batch("d"):
            refused = g1._check_root(g1.E_PROFILE.root)
        self.assertEqual(refused.get("reason_code"), g1.REASON_BATCH_IDENTITY_MISMATCH)
        self.assertEqual(refused.get("root_belongs_to"), "e")

    def test_a_prereg_with_another_deadline_is_refused(self):
        e = g1.E_PROFILE
        root = Path(tempfile.mkdtemp(prefix="g1-e-prereg-"))
        g1._write_json(g1._experiment_dir(root) / g1.PREREG_FILENAME, {
            "schema": "g1.real.prereg.v1", "batch": "e", "run_id": e.run_id,
            "domain_id": e.domain_id, "root": str(root),
            "state_root": str(root / "one-day"),
            "deadline": g1.BATCH_PROFILES["c"].deadline.isoformat()})
        with g1._use_batch("e"):
            refused = g1._verify_instance_identity(root)
        self.assertEqual(refused.get("reason_code"), g1.REASON_BATCH_IDENTITY_MISMATCH)
        self.assertEqual(refused.get("field"), "deadline")

    def _phase_refusal(self, label: str, payload: Mapping[str, object]) -> subprocess.CompletedProcess:
        root = Path(tempfile.mkdtemp(prefix="g1-e-phase-"))
        config = root / ("cfg-%s.json" % label)
        g1._write_json(config, dict(payload))
        return subprocess.run(
            [sys.executable, "-B", str(Path(g1.__file__)), "_phase",
             "--phase", g1.PHASE_NATURAL, "--config", str(config)],
            capture_output=True, text=True, env={**os.environ,
                                                 "PYTHONDONTWRITEBYTECODE": "1"})

    def test_phase_config_must_carry_the_batch_gate(self):
        e = g1.E_PROFILE
        base = {"root": str(Path(tempfile.mkdtemp(prefix="g1-e-gate-"))),
                "run_id": e.run_id, "domain_id": e.domain_id,
                "deadline": e.deadline.isoformat()}
        done = self._phase_refusal("missing", dict(base, batch="e"))
        self.assertEqual(done.returncode, g1.EXIT_REFUSED)
        self.assertIn(g1.REASON_WORLD_GATE_MISMATCH, done.stderr)
        done = self._phase_refusal("wrong", dict(base, batch="e", world_gate="off"))
        self.assertEqual(done.returncode, g1.EXIT_REFUSED)
        self.assertIn(g1.REASON_WORLD_GATE_MISMATCH, done.stderr)
        # d 不接受 canonical：父进程继承值不能把 d 的门打开。
        d = g1.D_PROFILE
        done = self._phase_refusal("d-canonical", {
            "root": str(Path(tempfile.mkdtemp(prefix="g1-d-gate-"))), "batch": "d",
            "run_id": d.run_id, "domain_id": d.domain_id,
            "deadline": d.deadline.isoformat(), "world_gate": "canonical"})
        self.assertEqual(done.returncode, g1.EXIT_REFUSED)
        self.assertIn(g1.REASON_WORLD_GATE_MISMATCH, done.stderr)

    def test_natural_phase_belongs_only_to_d_and_e(self):
        a = g1.BATCH_PROFILES["a"]
        done = self._phase_refusal("natural-under-a", {
            "root": "/tmp/x", "batch": "a", "run_id": a.run_id,
            "domain_id": a.domain_id, "deadline": a.deadline.isoformat()})
        self.assertEqual(done.returncode, g1.EXIT_REFUSED)
        self.assertIn(g1.REASON_UNKNOWN_BATCH, done.stderr)
        e = g1.E_PROFILE
        root = Path(tempfile.mkdtemp(prefix="g1-e-route-"))
        config = root / "cfg-p1-e.json"
        g1._write_json(config, {"root": "/tmp/x", "batch": "e", "run_id": e.run_id,
                                "domain_id": e.domain_id,
                                "deadline": e.deadline.isoformat(),
                                "world_gate": "canonical"})
        done = subprocess.run(
            [sys.executable, "-B", str(Path(g1.__file__)), "_phase", "--phase", "p1",
             "--config", str(config)], capture_output=True, text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(done.returncode, g1.EXIT_REFUSED)
        self.assertIn(g1.REASON_UNKNOWN_BATCH, done.stderr)


def _hostile_child_env_probe(root: Path) -> dict[str, Any]:
    """hostile 父环境也放进另子进程：本测试进程的 env 不被 patch，也不被继承污染。"""

    code = textwrap.dedent("""
        import json, sys
        sys.path[:0] = [sys.argv[1], sys.argv[2]]
        import g1_real_adoption as g1
        env = g1._phase_child_env(g1.E_PROFILE, sys.argv[3])
        keys = ["WORLD_BODY_INTENT_MUTATION_GATE", "CHIYO_WORLD_HOME", "HERMES_HOME",
                "WORLD_ACTION_AUTH_REGISTRY_DIR", "WORLD_ACTION_LIFECYCLE_PATH",
                "WORLD_ACTIVITY_STATE", "WORLD_EXPERIENCE_SPOOL",
                "WORLD_SELF_MAINTENANCE_STATE"]
        print(json.dumps({name: env.get(name) for name in keys}))
    """)
    hostile = {
        GATE_ENV: "off",
        "CHIYO_WORLD_HOME": str(REPO_ROOT / "data" / "world"),
        "HERMES_HOME": str(REPO_ROOT / "data" / "world"),
        "WORLD_ACTION_AUTH_REGISTRY_DIR": str(REPO_ROOT / "data" / "world" / "run"),
        "WORLD_ACTION_LIFECYCLE_PATH": str(REPO_ROOT / "data" / "world" / "run"
                                           / "action_lifecycle.jsonl"),
        "WORLD_ACTIVITY_STATE": str(REPO_ROOT / "data" / "world" / "run" / "activity_state.json"),
        "WORLD_EXPERIENCE_SPOOL": str(REPO_ROOT / "data" / "world" / "run"
                                      / "experience_candidates.jsonl"),
        "WORLD_SELF_MAINTENANCE_STATE": str(REPO_ROOT / "data" / "world" / "run"
                                            / "self_maintenance_state.json"),
    }
    environment = {**os.environ, **hostile}
    done = subprocess.run(
        [sys.executable, "-B", "-c", code, str(REPO_ROOT),
         str(REPO_ROOT / "scripts"), str(root)],
        capture_output=True, text=True, env=environment)
    if done.returncode != 0:
        raise AssertionError(done.stderr[-2000:])
    return json.loads(done.stdout.strip().splitlines()[-1])


def _run_guarded_phase(root: Path) -> subprocess.CompletedProcess:
    """在真实新子进程里跑一次 e 自然 phase，并把两个协作者打成 raise。

    子进程的 env 就是 ``_phase_child_env`` 构造的那一份；父进程环境不参与、也不被修改。
    """

    config = g1._experiment_dir(root) / "guarded-natural-config.json"
    # 窗口只写这一次；子进程的 loop/截止门读它，不写就没有请求。
    with g1._use_batch("e"):
        g1._write_window(root)
    g1._write_json(config, {
        "root": str(root), "batch": "e", "run_id": g1.E_PROFILE.run_id,
        "domain_id": g1.E_PROFILE.domain_id,
        "deadline": g1.E_PROFILE.deadline.isoformat(),
        "world_gate": "canonical", "offline": True,
        "capture_candidates": True, "plan": ["START", "NO_ACTION"]})
    code = textwrap.dedent("""
        import json, sys
        sys.path[:0] = [sys.argv[1], sys.argv[2]]
        import g1_real_adoption as g1

        # 本解释器不继承父进程的内存状态：显式进入同一个离线夹具参考时刻。
        g1.enter_offline_fixture(g1.E_PROFILE)

        class _NeverConstructed:
            def __init__(self, *a, **k):
                raise AssertionError("natural path constructed SubmissionInjector")

        def _boom(*a, **k):
            raise AssertionError("natural path called _provision_operator_material")

        g1.SubmissionInjector = _NeverConstructed
        g1._provision_operator_material = _boom
        with g1._use_batch("e"):
            with open(sys.argv[3], encoding="utf-8") as handle:
                g1._phase_natural(json.load(handle))
        print("GUARDED_PHASE_OK")
    """)
    environment = g1._phase_child_env(g1.E_PROFILE, root)
    return subprocess.run(
        [sys.executable, "-B", "-c", code, str(REPO_ROOT),
         str(REPO_ROOT / "scripts"), str(config)],
        capture_output=True, text=True, env=environment, timeout=900)


class ENaturalChainTest(unittest.TestCase):
    """e 的完整离线链：真 owner/runtime 在真实子进程里跑，脚本 Decision/内容，HTTP 硬拒。"""

    @classmethod
    def setUpClass(cls):
        cls.scratch = Path(tempfile.mkdtemp(prefix="g1-e-chain-"))
        cls.e = g1.E_PROFILE
        cls.repo_before = _bounded_snapshot(REPO_DEFAULT_PATHS)
        saved_material = g1._provision_operator_material

        def _boom_material(*_args, **_kwargs):
            raise AssertionError("e must never call _provision_operator_material")

        # root A：真实子进程 + 两个协作者 raise（prepare 在父进程里也打 raise）。
        cls.root_a = cls.scratch / "root-a"
        g1._provision_operator_material = _boom_material
        try:
            with g1._use_batch("e"):
                cls.prepared_a = g1.prepare(cls.root_a, expected_root=cls.root_a,
                                            offline=True)
        finally:
            g1._provision_operator_material = saved_material
        cls.guarded = _run_guarded_phase(cls.root_a)

        # root B：走父级真实入口（_run_phase_process → _phase_child_env）。
        cls.root_b = cls.scratch / "root-b"
        with g1._use_batch("e"):
            cls.prepared_b = g1.prepare(cls.root_b, expected_root=cls.root_b, offline=True)
            cls.run_b = g1.start_run(cls.root_b, expected_root=cls.root_b,
                                     execute_approved=True, offline=True,
                                     capture_candidates=True)
            cls.audit_b = g1.audit(cls.root_b, expected_root=cls.root_b)
            cls.again_b = g1.start_run(cls.root_b, expected_root=cls.root_b,
                                       execute_approved=True, offline=True)
        cls.repo_after = _bounded_snapshot(REPO_DEFAULT_PATHS)
        cls.artifacts_a = _Artifacts(cls.root_a, "e")
        cls.artifacts_b = _Artifacts(cls.root_b, "e")
        cls.summary_b = summary.build_summary(cls.root_b, offline_scratch=True)

    def test_prepare_never_provisions_material_or_a_control(self):
        prepared = self.prepared_a
        self.assertTrue(prepared.get("ok"), prepared.get("reason_code"))
        self.assertIsNone(prepared.get("material"))
        self.assertIsNone(prepared.get("control"))
        prereg = g1._read_json(g1._experiment_dir(self.root_a) / g1.PREREG_FILENAME)
        self.assertIsNone(prereg.get("operator_material"))
        self.assertIsNone(prereg.get("operator_control"))
        self.assertEqual(prereg.get("batch"), "e")
        self.assertEqual(prereg.get("deadline"), self.e.deadline.isoformat())
        self.assertEqual((prereg.get("caps") or {}).get("expected_history"), 148)
        self.assertEqual((prereg.get("caps") or {}).get("request_cap"), 20)
        self.assertEqual(prereg.get("world_intent_gate"), "canonical")
        observation = prereg.get("observation") or {}
        self.assertIs(observation.get("natural_phase"), True)
        self.assertIs(observation.get("operator_material_provisioned"), False)
        self.assertIs(observation.get("control_created"), False)
        self.assertIs(observation.get("injector_constructed"), False)
        self.assertEqual(observation.get("world_intent_gate"), "canonical")
        # e 不抄 d 的两个材料布尔。
        self.assertNotIn("booleans", observation)

    def test_the_guarded_child_phase_is_real_standard_assembly_with_canonical_gate(self):
        self.assertEqual(self.guarded.returncode, 0, self.guarded.stderr[-2000:])
        self.assertIn("GUARDED_PHASE_OK", self.guarded.stdout)
        report = self.artifacts_a.phase
        self.assertEqual(report.get("batch"), "e")
        self.assertEqual(report.get("run_id"), self.e.run_id)
        self.assertIs(report.get("natural_phase"), True)
        self.assertIs(report.get("operator_material_provisioned"), False)
        self.assertIs(report.get("control_created"), False)
        self.assertIs(report.get("injector_constructed"), False)
        self.assertNotIn("injections", report)
        self.assertNotIn("coverage", report)
        world = report.get("world") or {}
        self.assertIs(world.get("started"), True)
        self.assertIs(world.get("ready"), True)
        self.assertEqual(world.get("status"), "READY")
        self.assertEqual(world.get("gate"), "canonical")
        self.assertEqual(world.get("child_env_gate"), "canonical")
        # World child 的 env 由 demo 从 phase 子进程继承构造：registry/lifecycle 也在本根。
        run_dir = self.artifacts_a.world_home / "run"
        self.assertEqual(Path(str(world.get("child_env_world_home"))),
                         self.artifacts_a.world_home)
        self.assertEqual(Path(str(world.get("child_env_registry"))), run_dir)
        self.assertEqual(Path(str(world.get("child_env_lifecycle"))),
                         run_dir / "action_lifecycle.jsonl")
        self.assertFalse((report.get("close_world") or {}).get("socket_live_after"))
        self.assertFalse((report.get("close_supply") or {}).get("socket_live_after"))
        observation = report.get("observation") or {}
        self.assertEqual(observation.get("status"), "OBSERVED")
        self.assertTrue(any(count > 0 for count in
                            observation.get("world_candidates_per_request") or []))
        isolation = report.get("isolation") or {}
        self.assertEqual(isolation.get("gate"), "canonical")
        self.assertIs(isolation.get("world_home_is_instance"), True)
        self.assertIs(isolation.get("world_home_is_within_root"), True)
        self.assertIs(isolation.get("registry_is_within_root"), True)
        self.assertIs(isolation.get("lifecycle_is_within_root"), True)
        self.assertIs(isolation.get("lifecycle_exists"), True)

    def test_the_parent_entry_runs_one_natural_phase_and_audit_sees_the_gate(self):
        run = self.run_b
        self.assertTrue(run.get("ok"), run.get("stop_reason"))
        self.assertEqual(run.get("batch"), "e")
        self.assertEqual(run.get("run_id"), self.e.run_id)
        self.assertIsNone(run.get("p1"))
        self.assertIsNone(run.get("p2"))
        self.assertIsNotNone(run.get("natural"))
        self.assertEqual((run.get("natural") or {}).get("phase"), g1.PHASE_NATURAL)
        self.assertEqual(run.get("coverage"), {})
        self.assertIn(run.get("result"), {"OBSERVED", "NOT_COVERED"})
        config = g1._read_json(g1._experiment_dir(self.root_b)
                               / ("g1-config-%s.json" % g1.PHASE_NATURAL))
        self.assertEqual(config.get("batch"), "e")
        self.assertEqual(config.get("world_gate"), "canonical")
        audited = self.audit_b
        self.assertTrue(audited.get("ok"), audited)
        self.assertEqual(audited.get("batch"), "e")
        self.assertEqual(audited.get("world_intent_gate"), "canonical")
        self.assertEqual(list((audited.get("phases") or {})), [g1.PHASE_NATURAL])
        self.assertIn("observation", audited)
        self.assertEqual(self.again_b.get("reason_code"), g1.REASON_ALREADY_STARTED)
        ledger = run.get("ledger") or {}
        self.assertEqual(ledger.get("run_id"), self.e.run_id)
        self.assertEqual(int(ledger.get("run_window_max") or 0), 20)

    def test_the_action_really_succeeded_through_the_formal_chain(self):
        artifacts = self.artifacts_b
        self.assertEqual(len(artifacts.actions), 1, artifacts.actions)
        action = artifacts.actions[0]
        action_id = str(action.get("action_id") or "")
        self.assertEqual(action.get("status"), "SUCCEEDED", action)
        attempts = artifacts.attempts_for(action_id)
        self.assertEqual(len(attempts), 1, attempts)
        self.assertEqual(attempts[0].get("status"), "SUCCEEDED", attempts)
        receipts = artifacts.receipts_for(action_id)
        self.assertTrue(receipts, "no owner receipt for the action")
        receipt = receipts[0]
        self.assertIn(str(receipt.get("status_claim") or "").upper(),
                      {"ACCEPTED", "SUCCEEDED"})
        execution_id = str(receipt.get("provider_operation_ref") or "")
        self.assertTrue(execution_id, receipt)
        payload = artifacts.payload(action)
        self.assertTrue(payload.get("verb"), payload)
        self.assertTrue(payload.get("target"), payload)
        # 同 Action / 执行 / 目标：owner 侧 lifecycle 对同一 execution_id 已 APPLIED。
        lifecycle = artifacts.lifecycle_for(execution_id)
        applied = [row for row in lifecycle if row.get("state") == "APPLIED"]
        self.assertTrue(applied, {"execution_id": execution_id, "lifecycle": lifecycle})
        # APPLIED 是一条 transition，只有 CREATED 行带 verb；同一 execution_id 的
        # verb 必须与 AR-0 payload 相同。
        verbs = {str(row.get("verb")) for row in lifecycle if row.get("verb")}
        self.assertIn(str(payload.get("verb")), verbs,
                      {"execution_id": execution_id, "lifecycle": lifecycle})
        # World 状态里这次执行的 revision 能对上（一个 action，允许后续 tick 推进）。
        details = dict(receipt.get("details") or {})
        revision = details.get("world_revision")
        if isinstance(revision, int) and not isinstance(revision, bool):
            observed = artifacts.world_state.get("revision")
            if isinstance(observed, int):
                self.assertGreaterEqual(observed, revision)
        # 具体的 owner 效果按 verb 配对，不用 journeyChanged/receipt 非空兜底。
        verb = str(payload.get("verb") or "")
        target = str(payload.get("target") or "")
        if verb == "POSE":
            from life_body_action_targets import parse_body_action_target

            parsed = parse_body_action_target(target)
            self.assertIsNotNone(parsed, target)
            self.assertEqual(artifacts.body.get("pose"), parsed["params"]["pose"], artifacts.body)
            self.assertEqual(artifacts.body.get("orientation"),
                             parsed["params"]["orientation"], artifacts.body)
        elif verb == "MOVE":
            state = artifacts.journey or {}
            active = state.get("active") if isinstance(state.get("active"), Mapping) else None
            targets = []
            if isinstance(active, Mapping):
                targets.append(str(active.get("target_location_id") or ""))
            for row in list(state.get("history") or [])[-4:]:
                if isinstance(row, Mapping):
                    targets.append(str(row.get("target_location_id") or ""))
            self.assertIn(target, targets,
                          {"target": target, "journey": state, "in_transit": True})

    def test_the_phase_child_writes_only_inside_the_new_root(self):
        isolation = self.artifacts_b.phase.get("isolation") or {}
        self.assertEqual(isolation.get("gate"), "canonical")
        self.assertIs(isolation.get("world_home_is_instance"), True)
        self.assertIs(isolation.get("world_home_is_within_root"), True)
        self.assertIs(isolation.get("registry_is_within_root"), True)
        self.assertIs(isolation.get("lifecycle_is_within_root"), True)
        self.assertIs(isolation.get("lifecycle_exists"), True)
        lifecycle_path = Path(str(isolation.get("lifecycle_path") or ""))
        self.assertTrue(lifecycle_path.exists(), lifecycle_path)
        self.assertEqual(lifecycle_path,
                         self.artifacts_b.world_home / "run" / "action_lifecycle.jsonl")
        receipt = self.artifacts_b.receipts_for(
            str(self.artifacts_b.actions[0].get("action_id") or ""))[0]
        execution_id = str(receipt.get("provider_operation_ref") or "")
        records = self.artifacts_b.lifecycle_for(execution_id)
        self.assertTrue(any(row.get("state") == "APPLIED" for row in records), records)
        # phase 子进程报告的仓库默认路径 ≠ 本根路径（没有回退到仓库）。
        repo_default = str(isolation.get("repo_default_lifecycle_path") or "")
        self.assertNotEqual(repo_default, str(lifecycle_path))

    def test_a_hostile_parent_environment_cannot_leak_into_the_e_child(self):
        probed = _hostile_child_env_probe(self.root_b)
        instance_home = self.artifacts_b.world_home
        self.assertEqual(probed[GATE_ENV], "canonical")
        self.assertEqual(Path(probed["CHIYO_WORLD_HOME"]), instance_home)
        self.assertEqual(Path(probed["HERMES_HOME"]), instance_home)
        run_dir = instance_home / "run"
        self.assertEqual(Path(probed["WORLD_ACTION_AUTH_REGISTRY_DIR"]), run_dir)
        self.assertEqual(Path(probed["WORLD_ACTION_LIFECYCLE_PATH"]),
                         run_dir / "action_lifecycle.jsonl")
        self.assertEqual(Path(probed["WORLD_ACTIVITY_STATE"]),
                         run_dir / "activity_state.json")
        self.assertEqual(Path(probed["WORLD_EXPERIENCE_SPOOL"]),
                         run_dir / "experience_candidates.jsonl")
        self.assertEqual(Path(probed["WORLD_SELF_MAINTENANCE_STATE"]),
                         run_dir / "self_maintenance_state.json")
        # 父进程（本测试进程）环境没被这一轮 probe 改动。
        for name, value in _ENV_BEFORE.items():
            self.assertEqual(os.environ.get(name), value, name)

    def test_repo_default_world_paths_get_zero_new_writes(self):
        changed = _snapshot_diff(self.repo_before, self.repo_after)
        self.assertEqual(changed, {},
                         "repo default world paths changed during the run: %r" % (changed,))

    def test_the_summary_is_read_only_and_names_the_gate(self):
        built = self.summary_b
        self.assertTrue(built.get("ok"), built)
        self.assertEqual(built.get("batch"), "e")
        self.assertEqual(built.get("world_intent_gate"), "canonical")
        self.assertEqual((built.get("lifecycle") or {}).get("state"), "finished")
        world = built.get("world") or {}
        self.assertEqual(world.get("status"), "OBSERVED")
        self.assertIn((world.get("position") or {}).get("status"),
                      {"OBSERVED", "UNKNOWN"})
        self.assertIn((world.get("journey") or {}).get("status"),
                      {"OBSERVED", "ABSENT", "UNKNOWN"})
        self.assertIn((world.get("body") or {}).get("status"), {"OBSERVED", "UNKNOWN"})
        blob = json.dumps(built, ensure_ascii=False)
        self.assertNotIn(g1.MATERIAL_TEXT, blob)

    def test_process_environment_is_unchanged(self):
        for name, value in _ENV_BEFORE.items():
            self.assertEqual(os.environ.get(name), value, name)


def _run_d_gate_off_step(root: Path) -> dict[str, Any]:
    """d 的真实子进程：同一标准 adapter、显式 off，只走一次正式 ``evaluate_once``。

    d 的绝对截止（2026-10-31 01:00 CST）已经过去，父级 ``start_run`` 会按设计拒开窗口；
    这里用显式的固定 latch（不按截止判）只验证 **gate off 的正式拒绝**，不冒充真实 run。
    Decision 是明确标注的测试替身（只从真实 WORLD_OPPORTUNITY 候选里挑 START）。
    """

    report_path = g1._experiment_dir(root) / "d-gate-off-step.json"
    code = textwrap.dedent("""
        import json, sys
        sys.path[:0] = [sys.argv[1], sys.argv[2]]
        import g1_real_adoption as g1

        # 本解释器不继承父进程的内存状态：显式进入 d 的离线夹具参考时刻。
        g1.enter_offline_fixture(g1.D_PROFILE)

        root, report_path = sys.argv[3], sys.argv[4]

        class PickWorldStart(g1._ScriptedDecisionTransport):
            def complete(self, *, system_prompt, user_prompt, timeout_s=None,
                         max_output_tokens=None):
                self.calls.append({"max_output_tokens": max_output_tokens,
                                   "prompt_chars": len(user_prompt or "")})
                try:
                    payload = json.loads(user_prompt or "{}")
                except ValueError:
                    payload = {}
                allowed = [str(code) for code in (payload.get("allowed_reason_codes") or [])]
                rows = [row for row in (payload.get("candidates") or [])
                        if isinstance(row, dict)]
                pool = [row for row in rows
                        if str(row.get("source_kind") or "") == "WORLD_OPPORTUNITY"
                        and "START" in [str(code) for code in (row.get("legal_decisions") or [])]]
                pick = pool[0] if pool else None
                answer = {"decision": "NO_ACTION", "selected_candidate_id": None,
                          "reason_codes": allowed[:1] or ["NO_ACTION_CHOSEN"],
                          "deferred_candidate_ids": []}
                if pick is not None:
                    answer.update({"decision": "START",
                                   "selected_candidate_id": str(pick["candidate_id"])})
                return self._result(json.dumps(answer, ensure_ascii=False), "offline-decision")

        out = {}
        supply = world = bundle = None
        try:
            with g1._use_batch("d"):
                g1._write_window(root)
                supply = g1._start_supply(root, tag="test-d-gate-off")
                world = g1._start_world(root, tag="test-d-gate-off")
                out["world"] = {
                    "gate": g1._world_intent_gate(g1._identity()),
                    "child_env_gate": world.environment.get(
                        "WORLD_BODY_INTENT_MUTATION_GATE"),
                    "child_env_world_home": world.environment.get("CHIYO_WORLD_HOME")}
                env = g1._runtime_env(root, offline=True)
                out["runtime_gate"] = env.get("WORLD_BODY_INTENT_MUTATION_GATE")
                state_root = env["NYA02_RESIDENT_STATE"]
                budget = g1._budget(state_root)
                latch = g1.SendLatch(lambda: False)
                saved = g1._ScriptedDecisionTransport
                g1._ScriptedDecisionTransport = PickWorldStart
                try:
                    bundle = g1._build_runtime(
                        root, state_root=state_root, budget=budget, latch=latch,
                        env=env, offline=True, plan=["START"], world_feed=True)
                finally:
                    g1._ScriptedDecisionTransport = saved
                out.update(dict(bundle["cycle"].evaluate_once(
                    trigger_ref="test:d:world-step", act=True,
                    observed_at=g1._FixtureClock().next()) or {}))
        finally:
            if bundle is not None:
                out["close_runtime"] = g1._release_runtime(bundle)
            if world is not None:
                out["close_world"] = g1._stop_world(world)
            if supply is not None:
                out["close_supply"] = g1._stop_supply(supply)
            with g1._use_batch("d"):
                out["isolation"] = g1._isolation_refresh(g1._isolation_view(root))
        with open(report_path, "w", encoding="utf-8") as handle:
            json.dump(out, handle, ensure_ascii=False, default=str)
        print("GATE_OFF_STEP_OK")
    """)
    environment = g1._phase_child_env(g1.D_PROFILE, root)
    done = subprocess.run(
        [sys.executable, "-B", "-c", code, str(REPO_ROOT),
         str(REPO_ROOT / "scripts"), str(root), str(report_path)],
        capture_output=True, text=True, env=environment, timeout=900)
    if done.returncode != 0:
        raise AssertionError(done.stderr[-3000:])
    return dict(g1._read_json(report_path) or {})


class DGateOffRealAdapterTest(unittest.TestCase):
    """d：同一个标准 adapter，在显式 off 下仍是明确 GATE_OFF、零 World 效果。"""

    @classmethod
    def setUpClass(cls):
        cls.scratch = Path(tempfile.mkdtemp(prefix="g1-d-world-step-"))
        cls.root = cls.scratch / "root"
        cls.repo_before = _bounded_snapshot(REPO_DEFAULT_PATHS)
        with g1._use_batch("d"):
            cls.prepared = g1.prepare(cls.root, expected_root=cls.root, offline=True)
        cls.out = _run_d_gate_off_step(cls.root)
        cls.repo_after = _bounded_snapshot(REPO_DEFAULT_PATHS)
        cls.artifacts = _Artifacts(cls.root, "d")

    def test_the_step_is_real_standard_assembly_with_gate_off(self):
        self.assertTrue(self.prepared.get("ok"), self.prepared)
        out = self.out
        self.assertEqual(out.get("decision"), "START", out.get("detail"))
        self.assertTrue(out.get("activity_ref"), out)
        self.assertTrue(out.get("action_id"), out)
        self.assertEqual((out.get("world") or {}).get("gate"), "off")
        self.assertEqual((out.get("world") or {}).get("child_env_gate"), "off")
        self.assertEqual(out.get("runtime_gate"), "off")
        self.assertFalse((out.get("close_world") or {}).get("socket_live_after"))
        self.assertFalse((out.get("close_supply") or {}).get("socket_live_after"))

    def test_the_world_child_and_runtime_see_off(self):
        env = self.artifacts.env
        self.assertEqual(env.get(GATE_ENV), "off")
        isolation = self.out.get("isolation") or {}
        self.assertEqual(isolation.get("gate"), "off")
        self.assertIs(isolation.get("world_home_is_instance"), True)
        self.assertIs(isolation.get("world_home_is_within_root"), True)
        self.assertIs(isolation.get("lifecycle_is_within_root"), True)
        self.assertIs(isolation.get("lifecycle_exists"), True)

    def test_gate_off_is_a_definite_pre_submit_refusal_with_no_world_effect(self):
        out = self.out
        self.assertEqual(out.get("outcome"), "STEP_REFUSED", out.get("detail"))
        self.assertEqual(out.get("action_status"), "PREPARED")
        pre = out.get("pre_submit") or {}
        self.assertEqual(pre.get("reason_code"), "GATE_OFF")
        self.assertIs(pre.get("not_sent"), True)
        artifacts = self.artifacts
        self.assertEqual(len(artifacts.actions), 1, artifacts.actions)
        action = artifacts.actions[0]
        action_id = str(action.get("action_id") or "")
        self.assertEqual(action.get("status"), "PREPARED", action)
        attempts = artifacts.attempts_for(action_id)
        self.assertEqual(len(attempts), 1, attempts)
        self.assertEqual(attempts[0].get("status"), "PRE_SUBMIT_FAILED", attempts)
        self.assertEqual(attempts[0].get("error_class"), "GATE_OFF", attempts)
        self.assertEqual(artifacts.receipts_for(action_id), [])
        # 生命周期里只有 CREATED/ABORTED，没有 APPLIED（owner 没提交）。
        execution_id = ""
        error_ref = str(attempts[0].get("error_ref") or "")
        if error_ref.startswith("err:") and ":" in error_ref[4:]:
            execution_id = error_ref[4:].rsplit(":", 1)[0]
        records = artifacts.lifecycle_for(execution_id) if execution_id else artifacts.lifecycle
        states = {str(row.get("state") or "") for row in records}
        self.assertNotIn("APPLIED", states, records)
        self.assertIn("CREATED", states, records)
        # 仓库默认写面零新增。
        self.assertEqual(_snapshot_diff(self.repo_before, self.repo_after), {})


class EGuardTest(unittest.TestCase):
    """cap 用满即停；摘要只认 e 根。"""

    def test_the_cap_stops_the_loop(self):
        root = Path(tempfile.mkdtemp(prefix="g1-e-cap-"))
        with g1._use_batch("e"):
            g1._write_json(g1._experiment_dir(root) / g1.PREREG_FILENAME, {
                "schema": "g1.real.prereg.v1", "batch": "e",
                "run_id": g1.E_PROFILE.run_id,
                "domain_id": g1.E_PROFILE.domain_id, "root": str(root),
                "state_root": str(root / "state"),
                "deadline": g1.E_PROFILE.deadline.isoformat()})
            g1._write_window(root)
        state_root = root / "state"
        state_root.mkdir(parents=True, exist_ok=True)
        budget = g1._budget(state_root)
        budget.ensure_window(allow_new=True)
        latch = g1.SendLatch(lambda: g1._deadline_passed(root))
        self.assertEqual(g1._loop_stop(root, budget, latch), "")
        for _index in range(g1.REQUEST_CAP):
            budget.reserve("decision")
        self.assertEqual(g1._loop_stop(root, budget, latch), g1.REASON_REQUEST_CAP)

    def test_the_summary_refuses_any_other_root(self):
        other = Path(tempfile.mkdtemp(prefix="g1-e-other-"))
        refused = summary.build_summary(other)
        self.assertFalse(refused.get("ok"))
        self.assertIn(refused.get("reason_code"),
                      {g1.REASON_ROOT_NOT_PREREGISTERED, g1.REASON_BATCH_IDENTITY_MISMATCH})
        self.assertEqual(summary.main(["--root", str(other)]), summary.EXIT_REFUSED)


def _prepare_e_root(prefix: str) -> Path:
    """新建一个干净 e 实例根（走真 prepare 入口），供路径守卫负控使用。"""

    scratch = Path(tempfile.mkdtemp(prefix=prefix))
    root = scratch / "root"
    with g1._use_batch("e"):
        prepared = g1.prepare(root, expected_root=root, offline=True)
    if not prepared.get("ok"):
        raise AssertionError("prepare refused: %r" % (prepared,))
    return root


def _load_instance(root: Path) -> dict[str, Any]:
    from components.phone import service as phone_service

    return dict(phone_service.load_instance(root))


def _save_instance(root: Path, payload: Mapping[str, Any]) -> None:
    from components.phone import service as phone_service

    phone_service.write_instance(root, payload)


def _start_e(root: Path) -> dict[str, Any]:
    with g1._use_batch("e"):
        return g1.start_run(root, expected_root=root, execute_approved=True, offline=True)


class EWorldHomeGuardTest(unittest.TestCase):
    """e 的路径守卫：坏 world_home 在服务、请求、started/window 之前明确拒绝。"""

    @classmethod
    def setUpClass(cls):
        # 共享一个未篡改的合法根，只给「phase 子进程 env 自查」用，测试里不修改它。
        cls.root_ok = _prepare_e_root("g1-e-guard-ok-")

    def _assert_refused_before_window(self, root: Path, reason: str) -> None:
        refused = _start_e(root)
        self.assertFalse(refused.get("ok"), refused)
        self.assertEqual(refused.get("reason_code"), reason, refused)
        experiment = g1._experiment_dir(root)
        self.assertFalse(g1._window_path(root).exists(), "window was written")
        batch = g1._read_json(experiment / g1.BATCH_FILENAME) or {}
        self.assertFalse((batch.get("run") or {}).get("started_at"), batch)
        self.assertFalse((experiment / (g1.PHASE_FILENAME % g1.PHASE_NATURAL)).exists())

    def test_missing_or_unreadable_world_home_is_refused_before_the_window(self):
        missing = _prepare_e_root("g1-e-guard-missing-")
        payload = _load_instance(missing)
        payload.pop("world_home", None)
        _save_instance(missing, payload)
        self._assert_refused_before_window(missing, g1.REASON_WORLD_HOME_UNAVAILABLE)

        broken = _prepare_e_root("g1-e-guard-unreadable-")
        from components.phone import service as phone_service

        phone_service.instance_path(broken).write_text("{not json", encoding="utf-8")
        self._assert_refused_before_window(broken, g1.REASON_WORLD_HOME_UNAVAILABLE)

    def test_relative_world_home_is_refused(self):
        root = _prepare_e_root("g1-e-guard-relative-")
        payload = _load_instance(root)
        payload["world_home"] = "world"
        _save_instance(root, payload)
        self._assert_refused_before_window(root, g1.REASON_WORLD_HOME_UNAVAILABLE)

    def test_world_home_outside_the_root_is_refused(self):
        root = _prepare_e_root("g1-e-guard-outside-")
        outside = root.parent / "outside-world"
        outside.mkdir(parents=True, exist_ok=True)
        payload = _load_instance(root)
        payload["world_home"] = str(outside)
        _save_instance(root, payload)
        self._assert_refused_before_window(root, g1.REASON_WORLD_HOME_OUTSIDE_ROOT)

    def test_symlinked_world_home_escape_is_refused(self):
        root = _prepare_e_root("g1-e-guard-symlink-")
        outside = root.parent / "outside-world"
        outside.mkdir(parents=True, exist_ok=True)
        link = root / "escaped-world"
        try:
            os.symlink(str(outside), str(link), target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest("symlink not supported here: %r" % (exc,))
        payload = _load_instance(root)
        payload["world_home"] = str(link)
        _save_instance(root, payload)
        self._assert_refused_before_window(root, g1.REASON_WORLD_HOME_OUTSIDE_ROOT)

    def test_a_broken_home_cannot_borrow_a_hostile_parent_env(self):
        root = _prepare_e_root("g1-e-guard-hostile-")
        payload = _load_instance(root)
        payload.pop("world_home", None)
        _save_instance(root, payload)
        hostile = {
            "CHIYO_WORLD_HOME": str(REPO_ROOT / "data" / "world"),
            "HERMES_HOME": str(REPO_ROOT / "data" / "world"),
            "WORLD_ACTION_AUTH_REGISTRY_DIR": str(REPO_ROOT / "data" / "world" / "run"),
            "WORLD_ACTION_LIFECYCLE_PATH": str(REPO_ROOT / "data" / "world" / "run"
                                               / "action_lifecycle.jsonl"),
        }
        code = textwrap.dedent("""
            import json, sys
            sys.path[:0] = [sys.argv[1], sys.argv[2]]
            import g1_real_adoption as g1
            try:
                env = g1._phase_child_env(g1.E_PROFILE, sys.argv[3])
            except g1.Refused as exc:
                print(json.dumps({"refused": exc.reason_code}))
            else:
                print(json.dumps({"refused": None,
                                  "home": env.get("CHIYO_WORLD_HOME")}))
        """)
        done = subprocess.run(
            [sys.executable, "-B", "-c", code, str(REPO_ROOT),
             str(REPO_ROOT / "scripts"), str(root)],
            capture_output=True, text=True, env={**os.environ, **hostile})
        self.assertEqual(done.returncode, 0, done.stderr[-2000:])
        out = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertEqual(out.get("refused"), g1.REASON_WORLD_HOME_UNAVAILABLE)
        # 父环境里的仓库默认路径既没被借用，也没出现在结果里。
        self.assertNotIn(str(REPO_ROOT / "data" / "world"), done.stdout)

    def _phase_config(self, root: Path) -> Path:
        config = g1._experiment_dir(root) / "guard-natural-config.json"
        g1._write_json(config, {
            "root": str(root), "batch": "e", "run_id": g1.E_PROFILE.run_id,
            "domain_id": g1.E_PROFILE.domain_id,
            "deadline": g1.E_PROFILE.deadline.isoformat(),
            "world_gate": "canonical", "offline": True, "plan": ["NO_ACTION"]})
        return config

    def test_the_phase_child_refuses_a_mismatched_env_before_any_service(self):
        root = self.root_ok
        config = self._phase_config(root)
        env = g1._phase_child_env(g1.E_PROFILE, root)
        # 只有 home 错：其余六个写面仍是本实例的值，验证单项错配也会被拒。
        env["CHIYO_WORLD_HOME"] = str(root.parent / "somewhere-else")
        done = subprocess.run(
            [sys.executable, "-B", str(Path(g1.__file__)), "_phase",
             "--phase", g1.PHASE_NATURAL, "--config", str(config)],
            capture_output=True, text=True, env=env)
        self.assertEqual(done.returncode, g1.EXIT_REFUSED, done.stderr[-2000:])
        self.assertIn(g1.REASON_WORLD_ENV_MISMATCH, done.stderr)
        # 拒绝发生在 runner 之前：没有 phase 报告，也就没有起 World/Supply。
        self.assertFalse((g1._experiment_dir(root)
                          / (g1.PHASE_FILENAME % g1.PHASE_NATURAL)).exists())

    def test_the_phase_child_accepts_its_fixed_mapping(self):
        root = self.root_ok
        env = g1._phase_child_env(g1.E_PROFILE, root)
        code = textwrap.dedent("""
            import sys
            sys.path[:0] = [sys.argv[1], sys.argv[2]]
            import g1_real_adoption as g1
            g1._verify_isolated_child_env(sys.argv[3])
            print("SELF_CHECK_OK")
        """)
        done = subprocess.run(
            [sys.executable, "-B", "-c", code, str(REPO_ROOT),
             str(REPO_ROOT / "scripts"), str(root)],
            capture_output=True, text=True, env=env)
        self.assertEqual(done.returncode, 0, done.stderr[-2000:])
        self.assertIn("SELF_CHECK_OK", done.stdout)


if __name__ == "__main__":
    unittest.main()

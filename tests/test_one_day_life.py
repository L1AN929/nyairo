"""W05: 一天自主生活 —— owner 级、cycle 级与入口级验证。

施工单 §6.1 的场景对应：

* prepare/preflight/status/pause/resume/report：真实 Life Supply owner（governance + supply
  两个 Unix socket 服务，线程内、落盘）+ 真实 World home（``init_instance`` 写 world_state.json）；
* 计时出发不等于到达、观察外目标被拒、UNKNOWN 对账不重放：真实旅程引擎 + 真实 World owner；
* 两种合格选择分别驱动真实 World 移动与真实 Supply 作品版本：真实 ProductionLifeRuntime +
  真实 Supply owner + **确定性替身决策/内容**（施工单允许）；
* NO_ACTION/DEFER 零副作用、下一次选择受真实结果影响、pause/resume 边界、双 writer 拒绝、
  只读 status 零字节变化、停机不补经历、报告不提前 PASS。

替身边界（必须在回执里照写）：W05 不启 World gateway socket；测试里的"观察缝"直接读**同一份**
``world_state.json``（``world_body_gateway_api`` 也是读它），位置权威始终是真实 owner，
被替换的只是 socket 传输。真实模型一次也不调用。

需要 POSIX（fcntl + AF_UNIX）与 tz 数据库；原生 Windows 自动跳过。
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"), str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge"),
              str(ROOT / "components" / "supply")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from components.worldgen.instance import init_instance
from components.worldgen.tests._setup import WorldGenTestCase, make_valid_docs

import one_day_life as odl
import autonomous_life_control as control
import life_pause

posix_only = unittest.skipIf(
    os.name == "nt", "需要 POSIX 文件锁与 AF_UNIX（在 WSL/Linux 上运行）"
)


#: AG-1 Section 65 允许 START 的候选类别。替身模型与真模型看到的是同一份候选数据，
#: 所以替身也按这份清单过滤，而不是抓第一个候选硬塞 START（那会被决策矩阵正确拒绝）。
_STARTABLE_CANDIDATE_KINDS = frozenset({
    "RESPOND_USER_REQUEST",
    "CONSIDER_WORLD_OPPORTUNITY",
    "FULFILL_COMMITMENT",
    "PURSUE_GOAL_STEP",
    "CONSIDER_PERSONAL_OPPORTUNITY",
})
ENV_GATES = {"REAL_WORKSPACE_PROVISION": "ON", "REAL_ARTIFACT_ACTION": "ON"}


def _clean_env() -> dict:
    env = dict(os.environ)
    for name in odl.OWNED_ENV_NAMES:
        env.pop(name, None)
    for name in ("NYA02_MODEL_BASE_URL", "NYA02_MODEL_NAME", "NYA02_MODEL_API_KEY",
                 "NYA02_MODEL_PROVIDER", "NYA02_PROFILE_HOME", "CHIYO_PROFILE_HOME",
                 "CHIYO_AUTONOMOUS_LIFE_ENABLED", "CHIYO_AUTONOMOUS_CHECK_INTERVAL_MINUTES"):
        env.pop(name, None)
    return env


class Services:
    """真实 Life Supply owner（LifeSupplyService）跑在它自己的 Unix socket 上，落盘在沙盒里。

    施工单要求"真实 Life/World/Supply 持久 owner"：这里用的是 owner 模块自己的 socket 服务器
    （``LifeSupplySocketServer``），它同时承担 governance 与 supply 两面，和 Life 侧
    ``LifeArtifactPort`` 说的是同一套 op。``Api``/``UnixApiServer`` 是另一套重建的前端，
    它的 op 名与 owner 不一致（``read_artifact`` vs ``get_artifact``），所以本单不用它。
    """

    def __init__(self, root: Path) -> None:
        from lifesupply.service.life_supply import LifeSupplyService, LifeSupplySocketServer
        from lifesupply.service.switches import FeatureSwitches

        self.root = root
        uid = os.geteuid()
        self.socket_path = root / "life-supply.sock"
        self.service = LifeSupplyService(
            root / "data", operator_uids={uid}, service_uids={uid},
            switches=FeatureSwitches.all_on(),
            writer_instance="w05-test:%d" % os.getpid())
        self.server = LifeSupplySocketServer(str(self.socket_path), self.service)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def supply_path(self) -> Path:
        return self.socket_path

    @property
    def gov_path(self) -> Path:
        return self.socket_path

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.service.close()


WORLD_RUNTIME_ROOT = ROOT / "components" / "world"
WORLD_BRIDGE_DIR = ROOT / "components" / "world" / "service" / "bridge"


class WorldGateway:
    """真实 World 服务：真 socket、真 gateway surface、真 Resolver、真授权租约。

    W05R 要求计时 MOVE 走世界**自己的**正式授权入口（Intent Executor 铸一次性授权 →
    ``gateway.sock`` 消费 → Resolver 提交），所以这里的 World 不是替身：它就是
    ``world_body_service`` 自己的服务循环，绑在实例自己的 world home 上。
    ``tick_seconds`` 取很大值，让后台 tick 不会在测试窗口里顺手写世界状态。
    """

    def __init__(self, world_home, *, world_id, environment=None,
                 runtime_root=WORLD_RUNTIME_ROOT, tick_seconds: float = 3600.0) -> None:
        import world_body_config as wcfg
        import world_body_service as wbsvc

        self.world_home = Path(world_home)
        # 服务的 Resolver 用的是 ``config.environment``，所以必须把实例自己的世界包/世界家
        # 交给它 —— 否则它只看得见 world_state.json，看不见那张地点图，正式入口会把一个
        # 真实可达的地点判成 UNKNOWN_TARGET。
        self.config = wcfg.load_config(dict(environment or {}), runtime_root=runtime_root,
                                       world_home=self.world_home,
                                       runtime_mode="canonical", authority="production",
                                       world_id=world_id)
        self.service = wbsvc.WorldBodyService(self.config, tick_seconds=tick_seconds,
                                             instance_id="w05r-%d" % os.getpid())
        self.startup_report = self.service.startup()
        self.socket_path = self.service.gateway_socket_path
        self._thread = threading.Thread(
            target=self.service.serve, kwargs={"max_seconds": 1800.0}, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 10.0
        while not self.socket_path.exists():
            if time.monotonic() > deadline:
                raise AssertionError("World gateway socket never appeared: %s"
                                     % self.socket_path)
            time.sleep(0.02)

    def environment(self) -> dict:
        """调用方环境：指向这个服务自己的 socket，并指向本树的 bridge 模块。"""

        return {"WORLD_BODY_SOCKET": str(self.socket_path),
                "WORLD_BODY_INTEGRATION_MODE": "canonical",
                "NYAIRO_WORLD_BRIDGE_DIR": str(WORLD_BRIDGE_DIR)}

    def stop(self) -> None:
        try:
            self.service.shutdown()
        except Exception:
            pass
        self._thread.join(timeout=5)


class StubModel:
    """一个 loopback OpenAI 兼容替身：只回答两个 purpose，从不联网。"""

    def __init__(self, *, policy: str = "artifact") -> None:
        self.policy = policy
        self.requests: list[dict] = []
        handler = self._handler()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def base_url(self) -> str:
        return "http://127.0.0.1:%d/v1" % self.port

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def _reply(self, body: dict) -> dict:
        system = ""
        user = ""
        for message in body.get("messages") or []:
            if message.get("role") == "system":
                system = str(message.get("content") or "")
            elif message.get("role") == "user":
                user = str(message.get("content") or "")
        self.requests.append({"system": system[:80], "user_chars": len(user)})
        if "bounded choice component" in system:
            text = json.dumps(self._decision(user), ensure_ascii=False)
        else:
            # W05R 根因修正（第二个）：内容写入器的合同只接受 ``append_paragraph`` /
            # ``revise_tail``；首轮替身给的 ``append`` 会被正确地判成
            # MODEL_EDIT_KIND_REFUSED，于是 CLI 那一轮既没有内容也没有版本。
            text = json.dumps({"kind": "append_paragraph",
                               "text": "（替身模型写的一小段）",
                               "self_note": "stub"}, ensure_ascii=False)
        return {"choices": [{"message": {"role": "assistant", "content": text}}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}}

    def _decision(self, user: str) -> dict:
        try:
            prompt = json.loads(user)
        except ValueError:
            prompt = {}
        candidates = [c for c in (prompt.get("candidates") or []) if isinstance(c, dict)]
        # W05R 根因修正：AG-1 的合同要求 ``reason_codes`` 至少含一个**已注册**的码，
        # 空列表会被正确地判成 INVALID_MODEL_OUTPUT:UNKNOWN_REASON_CODE（W05 首轮
        # CLI 的 BLOCKED 就是这个）。替身必须从真模型看得到的同一份
        # ``allowed_reason_codes`` 里挑，而不是自己编一个或留空。
        allowed = [str(code) for code in (prompt.get("allowed_reason_codes") or [])]
        # W05R 根因修正（第三个）：替身也必须尊重**这次请求**给的 legal_decision_enums 与
        # 候选自己的 executable_now。真模型会看这两样；不看就会给出一个被决策矩阵正确
        # 拒绝的组合（INVALID_MODEL_OUTPUT:DECISION_CANDIDATE_INCOMPATIBLE），
        # 那测到的就不是"生活闭环"，而是"替身不守合同"。
        legal = [str(item) for item in (prompt.get("legal_decision_enums") or [])]
        # START 只对这几类候选合法（AG-1 Section 65 的同一份清单）；候选自己的
        # executable_now 也必须不是 False。替身能看到的就只有这些字段，所以它按这些字段
        # 过滤，而不是抓第一个候选硬塞 START。
        startable = [c for c in candidates
                     if c.get("executable_now") is not False
                     and c.get("candidate_kind") in _STARTABLE_CANDIDATE_KINDS]
        world = [c for c in startable
                 if c.get("candidate_kind") == "CONSIDER_WORLD_OPPORTUNITY"]
        others = [c for c in startable if c not in world]
        if self.policy == "no_action":
            return {"decision": "NO_ACTION", "selected_candidate_id": None,
                    "reason_codes": allowed[:1], "short_rationale": "nothing worth doing",
                    "deferred_candidate_ids": []}
        if self.policy == "defer":
            return {"decision": "DEFER", "selected_candidate_id": None,
                    "reason_codes": allowed[:1], "short_rationale": "later",
                    "deferred_candidate_ids": [c.get("candidate_id") for c in startable]}
        # W05R-R1 根因修正（第四个）：AG-1 在请求里**逐候选**给出 ``legal_decisions``，真模型
        # 只看这一个候选允许什么。替身以前只看整次请求的 union，于是会挑到第一个"种类看起来
        # 可启动"的候选、硬塞 START，被决策矩阵正确地判成
        # ``INVALID_MODEL_OUTPUT:DECISION_CANDIDATE_INCOMPATIBLE`` —— 那测到的是替身不守
        # 合同，不是生活闭环。这里改成：只发这个候选自己允许的决定。
        def _legal_for(candidate, *names):
            per = [str(item) for item in (candidate.get("legal_decisions") or [])]
            if per:
                return [name for name in names if name in per]
            # 请求里没有逐候选清单（旧形状）：退回整次请求的 legal_decision_enums。
            return [name for name in names if not legal or name in legal]

        order = (world or startable) if self.policy == "move" else (others or startable)
        for candidate in order:
            if _legal_for(candidate, "START"):
                return {"decision": "START",
                        "selected_candidate_id": candidate.get("candidate_id"),
                        "reason_codes": allowed[:1], "short_rationale": "stub choice",
                        "deferred_candidate_ids": []}
        # 没有候选允许 START：合法的续作/恢复仍然可以选（无副作用），仍然按合同挑。
        for candidate in candidates:
            for name in ("CONTINUE", "RESUME"):
                if _legal_for(candidate, name):
                    return {"decision": name,
                            "selected_candidate_id": candidate.get("candidate_id"),
                            "reason_codes": allowed[:1], "short_rationale": "stub continues",
                            "deferred_candidate_ids": []}
        # 没有合法可启动的事项：选一个合法且无副作用的选择，而不是硬塞 START。
        if not legal or "NO_ACTION" in legal:
            return {"decision": "NO_ACTION", "selected_candidate_id": None,
                    "reason_codes": allowed[:1], "short_rationale": "nothing legal to start",
                    "deferred_candidate_ids": []}
        return {"decision": "DEFER", "selected_candidate_id": None,
                "reason_codes": allowed[:1], "short_rationale": "deferred",
                "deferred_candidate_ids": [c.get("candidate_id") for c in startable]}

    def _handler(self):
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):  # noqa: N802 - http.server naming
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                try:
                    body = json.loads(raw.decode("utf-8"))
                except ValueError:
                    body = {}
                payload = json.dumps(stub._reply(body)).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args):  # keep the test output clean
                return

        return Handler

def _tree_snapshot(root: Path) -> dict[str, tuple]:
    """整棵树的 (大小, sha256, mtime_ns)，用来证明"只读"不是口号。"""

    import hashlib

    out: dict[str, tuple] = {}
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root))
        if path.is_dir():
            out[relative] = ("dir",)
            continue
        stat = path.stat()
        out[relative] = (stat.st_size, hashlib.sha256(path.read_bytes()).hexdigest(),
                         stat.st_mtime_ns)
    return out


def _every_30_minutes(count: int, *, start: str = "2026-10-14T00:00:00+00:00") -> list[dict]:
    """连续、无缺口的日志时间戳（每 30 分钟一条），用来单独检验报告的判读规则。"""

    from datetime import datetime, timedelta

    base = datetime.fromisoformat(start)
    return [{"at": (base + timedelta(minutes=30 * index)).isoformat(), "tick": index + 1}
            for index in range(count)]


def _current_place(world_home: Path) -> str:
    state = json.loads((world_home / "data" / "world" / "world_state.json")
                       .read_text(encoding="utf-8"))
    return str(state["location"]["place_id"])


@posix_only
class OneDayTestCase(WorldGenTestCase):
    """真实 World home + 真实 Supply owner（两个 socket 服务）的隔离实例夹具。"""

    def setUp(self) -> None:
        super().setUp()
        #: W05R-R1: runtimes created by this test, closed in ``tearDown`` (see there).
        self._runtimes: list = []
        self.env_saved = dict(os.environ)
        self.world = self._make_world()
        self.services = Services(Path(self.temp_dir(prefix="w05svc-")))
        self.addCleanup(self._teardown)
        self.instance = Path(self.temp_dir(prefix="w05inst-")) / "instance"
        # 世界变更闸门（``WORLD_BODY_INTENT_MUTATION_GATE``）是 World bridge 自己的**进程环境**
        # 开关：关闭时 action space 里根本没有 MOVE 候选。部署时由 supervisor 设（见 runbook），
        # 这里为测试进程打开它。
        self._gate = mock.patch.dict(os.environ,
                                     {"WORLD_BODY_INTENT_MUTATION_GATE": "canonical"})
        self._gate.start()
        self.addCleanup(self._gate.stop)

    def tearDown(self) -> None:
        """Close the runtimes *before* the base class deletes the instance tree.

        W05R-R1: the runtime's durable state lives under the instance root, and
        ``WorldGenTestCase.tearDown`` (``super()``) removes that root before unittest
        runs the ``addCleanup`` callbacks.  Closing here is the order production uses:
        flush while the state root exists.
        """

        for runtime in getattr(self, "_runtimes", []):
            try:
                runtime.close()
            except Exception:  # a close failure must not hide the test result
                pass
        self._runtimes = []
        super().tearDown()

    def _teardown(self) -> None:
        self.services.stop()
        os.environ.clear()
        os.environ.update(self.env_saved)
        import world_runtime_data as wrd

        wrd.reset_cache()

    def _make_world(self):
        from types import SimpleNamespace

        package_dir = self.temp_package(make_valid_docs(seed=929711, theme="coastal_town"),
                                        prefix="w05pkg-")
        home = Path(self.temp_dir(prefix="w05world-")) / "world-home"
        init_instance(package_dir, home)
        return SimpleNamespace(package_dir=package_dir, home=home)

    def prepare_instance(self, **kwargs):
        with mock.patch.dict(os.environ, ENV_GATES):
            return odl.prepare(instance=self.instance,
                               supply_socket=self.services.supply_path,
                               governance_socket=self.services.gov_path,
                               subject="test:w05", title="W05 fixture",
                               world_package=self.world.package_dir, **kwargs)

    def gateway(self) -> "WorldGateway":
        """实例自己的真实 World 服务（懒启，只启一次）。

        W05R：世界移动必须经正式授权入口，所以观察缝之外还要有一个真的
        ``gateway.sock``。这里用 ``world_body_service`` 自己的循环，绑在实例自己的
        world home 上；测试结束关掉。
        """

        existing = getattr(self, "_gateway", None)
        if existing is not None:
            return existing
        data = odl.read_instance(self.instance)
        gateway = WorldGateway(self.world_home(), world_id=data["world_id"],
                               environment=odl.instance_environment(self.instance))
        self._gateway = gateway
        self.addCleanup(gateway.stop)
        return gateway

    def instance_env(self, data=None):
        env = odl.instance_environment(self.instance)
        env.update(self.gateway().environment())
        return env

    def world_home(self) -> Path:
        """prepare 在实例内部建自己的 world home；测试一律用它，不用夹具里的那个。"""

        return Path(str(odl.read_instance(self.instance)["world_home"]))

    def _prepared_instance(self) -> dict:
        self.prepare_instance()
        return odl.read_instance(self.instance)


class PrepareAndControlTest(OneDayTestCase):
    def test_prepare_creates_a_real_instance_and_preflight_reads_it(self):
        result = self.prepare_instance()
        self.assertTrue(result["ok"], result)
        data = odl.read_instance(self.instance)
        self.assertEqual(data["subject_id"], "test:w05")
        self.assertEqual(data["seed_provided_by"], "operator")
        self.assertEqual(data["world_home"], str(self.world_home()))
        # 真实 Supply owner 侧事实：作品与 head 真的在盘上
        head = odl.supply_call(self.services.supply_path,
                               {"op": "read_head", "subject": data["subject_id"],
                                "artifact_id": data["artifact_id"]})
        self.assertEqual(head["head"]["version_id"], data["seed_version_id"])
        # 真实 World owner 侧事实：world_state.json 由 init_instance 写出，位置在 home
        state = json.loads((self.world_home() / "data" / "world" / "world_state.json")
                           .read_text(encoding="utf-8"))
        self.assertEqual(state["world_id"], data["world_id"])
        self.assertTrue(state["location"]["place_id"])

        report = odl.preflight(self.instance)
        self.assertEqual(report["checks"]["world"]["status"], "READY")
        self.assertEqual(report["checks"]["supply"]["status"], "READY")
        self.assertEqual(report["checks"]["life"]["status"], "READY")
        # 没配模型：必须是 BLOCKED，而不是"看起来能用"
        self.assertEqual(report["checks"]["model"]["status"], "BLOCKED")
        self.assertFalse(report["verified_claims"]["verified_model_response"])
        self.assertIn("model", report["blocked"])
        # 密钥永不出现：描述里只有 api_key_present 这个布尔
        self.assertNotIn("api_key", report["checks"]["model"]["description"])
        self.assertFalse(report["checks"]["model"]["description"]["api_key_present"])

    def test_prepare_refuses_an_existing_instance_without_touching_it(self):
        self.prepare_instance()
        before = _tree_snapshot(self.instance)
        with self.assertRaises(odl.OneDayError):
            self.prepare_instance()
        self.assertEqual(_tree_snapshot(self.instance), before)

    def test_pause_is_the_life_bit_only_and_status_is_read_only(self):
        self.prepare_instance()
        world_control = self.world_home() / "data" / "world" / "world_control.json"
        self.assertFalse(world_control.exists())

        paused = odl.set_pause(self.instance, True, reason="test")
        self.assertTrue(paused["pause"]["paused"])
        self.assertTrue(paused["world_control_untouched"])
        # 世界自己的暂停位一个字都没动（两个开关互不误触）
        self.assertFalse(world_control.exists())

        before = _tree_snapshot(self.instance)
        view = odl.status(self.instance)
        self.assertTrue(view["read_only"])
        self.assertTrue(view["pause"]["paused"])
        self.assertEqual(_tree_snapshot(self.instance), before)

        resumed = odl.set_pause(self.instance, False, reason="test")
        self.assertFalse(resumed["pause"]["paused"])
        self.assertFalse(world_control.exists())

    def test_pause_state_corruption_fails_closed(self):
        self.prepare_instance()
        path = life_pause.pause_path(odl.life_state_root(self.instance))
        path.write_text("{not json", encoding="utf-8")
        view = life_pause.read(odl.life_state_root(self.instance))
        self.assertEqual(view["state"], life_pause.STATE_CORRUPT)
        self.assertTrue(view["paused"])
        self.assertTrue(life_pause.gate(odl.life_state_root(self.instance))["blocked"])
        self.assertEqual(life_pause.gate(odl.life_state_root(self.instance))["reason_code"],
                         life_pause.REASON_PAUSE_STATE_CORRUPT)


class JourneyResolverTest(OneDayTestCase):
    def _resolver(self):
        import world_journey_action_adapter as wja

        env = self.instance_env()
        import world_runtime_data as wrd

        wrd.reset_cache()
        return wja.JourneyWorldResolver(environment=env), env

    def test_departure_is_committed_but_never_reported_as_arrival(self):
        import action_reality_ledger as ar0
        import world_runtime_data as wrd

        data = self._prepared_instance()
        resolver, env = self._resolver()
        world = wrd.active_world(env)
        origin = _current_place(self.world_home())
        destination = sorted(world.reachable_of(origin))[0]

        out = resolver.resolve_action(idempotency_key="idem:w05:depart", verb="MOVE",
                                      target=destination, expected_dependencies={})
        self.assertEqual(out["status"], "committed")
        self.assertEqual(out["code"], "JOURNEY_DEPARTED")
        self.assertEqual(out["transport_result"], ar0.TRANSPORT_OK_SUCCEEDED)
        self.assertIs(out["details"]["arrived"], False)
        self.assertEqual(out["details"]["location_id"], origin)
        self.assertEqual(out["details"]["destination_location_id"], destination)
        self.assertTrue(out["details"]["journey_id"])

        # 真实 owner：位置仍在家；在途记录真的存在；旅程账本真的存在
        self.assertEqual(_current_place(self.world_home()), origin)
        journey_file = self.world_home() / "data" / "world" / "world_journey.json"
        self.assertTrue(journey_file.is_file())
        stored = json.loads(journey_file.read_text(encoding="utf-8"))
        self.assertIn(stored["active"]["status"], {"pending", "in_transit"})
        self.assertEqual(stored["active"]["target_location_id"], destination)

        # 幂等：同一个 key 重放不会再提交一次
        again = resolver.resolve_action(idempotency_key="idem:w05:depart", verb="MOVE",
                                        target=destination, expected_dependencies={})
        self.assertEqual(again["execution_id"], out["execution_id"])
        self.assertEqual(json.loads(journey_file.read_text(encoding="utf-8"))["revision"],
                         stored["revision"])

        # 对账：同一 execution_id 已被 owner 提交，不需要重放
        view = resolver.reconcile_request(idempotency_key="idem:w05:depart", target=destination)
        self.assertEqual(view["outcome"], "SUCCEEDED")
        self.assertFalse(view["result"]["arrived"])
        self.assertEqual(view["result"]["segments_confirmed"], 0)
        unknown = resolver.reconcile_request(idempotency_key="idem:w05:never", target=destination)
        self.assertEqual(unknown["outcome"], "NOT_FOUND")
        # 段级查询（旅程自己的 execution_id）仍然可用，且不会凭空认领
        self.assertEqual(
            resolver.reconcile_execution("world-exec:not-a-real-execution")["outcome"],
            "NOT_FOUND")

    def test_unobserved_target_is_refused_without_touching_world_state(self):
        import world_journey_action_adapter as wja

        self.prepare_instance()
        resolver, env = self._resolver()
        state_path = self.world_home() / "data" / "world" / "world_state.json"
        before = state_path.read_bytes()
        origin = _current_place(self.world.home)

        out = resolver.resolve_action(idempotency_key="idem:w05:bad", verb="MOVE",
                                      target="place_world_never_offered",
                                      expected_dependencies={})
        self.assertEqual(out["status"], "rejected")
        self.assertEqual(out["code"], "LOCATION_NOT_REACHABLE")
        self.assertIs(out["details"]["arrived"], False)
        self.assertEqual(state_path.read_bytes(), before)
        self.assertEqual(_current_place(self.world_home()), origin)
        self.assertFalse((self.world_home() / "data" / "world" / "world_journey.json").exists())

    def test_unsupported_verb_and_missing_home_are_definite_refusals(self):
        import action_reality_ledger as ar0
        import world_journey_action_adapter as wja

        self.prepare_instance()
        resolver, _env = self._resolver()
        pose = resolver.resolve_action(idempotency_key="idem:w05:pose", verb="POSE",
                                       target="home", expected_dependencies={})
        self.assertEqual(pose["status"], "rejected")
        self.assertEqual(pose["transport_result"], ar0.TRANSPORT_ERR_DEFINITE_PRE_SUBMIT)

        blind = wja.JourneyWorldResolver(environment={})
        out = blind.resolve_action(idempotency_key="idem:w05:nohome", verb="MOVE",
                                   target="home", expected_dependencies={})
        self.assertEqual(out["code"], "WORLD_HOME_NOT_CONFIGURED")
        self.assertEqual(out["transport_result"], ar0.TRANSPORT_ERR_DEFINITE_PRE_SUBMIT)
        self.assertIsNone(blind.world_home)

class StubCognition:
    """确定性替身决策（施工单 §6.2 允许）：只从 AG-1 真正给的候选里挑。"""

    model_id = "stub:w05"
    model_version = "w05-stub-v1"

    def __init__(self, policy: str = "artifact") -> None:
        self.policy = policy
        self.call_count = 0
        self.offered: list[list] = []

    def invoke(self, request, *, schema_retry_index: int = 0):
        import agency_decision_ag1 as ag1

        self.call_count += 1
        candidates = [dict(c) for c in (getattr(request, "candidates", []) or [])]
        self.offered.append([c.get("candidate_kind") for c in candidates])
        allowed = [str(code) for code in (getattr(request, "allowed_reason_codes", []) or [])]
        world = [c for c in candidates if c.get("candidate_kind") == "CONSIDER_WORLD_OPPORTUNITY"]
        others = [c for c in candidates if c not in world]
        if self.policy == "no_action":
            raw = {"decision": "NO_ACTION", "selected_candidate_id": None,
                   "reason_codes": allowed[:1],
                   "short_rationale": "nothing worth doing", "deferred_candidate_ids": []}
        elif self.policy == "defer":
            raw = {"decision": "DEFER", "selected_candidate_id": None,
                   "reason_codes": allowed[:1], "short_rationale": "later",
                   "deferred_candidate_ids": [c.get("candidate_id") for c in candidates]}
        else:
            chosen = (world or candidates) if self.policy == "move" else (others or candidates)
            if not chosen:
                raw = {"decision": "NO_ACTION", "selected_candidate_id": None,
                       "reason_codes": allowed[:1], "short_rationale": "no candidate",
                       "deferred_candidate_ids": []}
            else:
                raw = {"decision": "START",
                       "selected_candidate_id": chosen[0].get("candidate_id"),
                       "reason_codes": allowed[:1], "short_rationale": "stub choice",
                       "deferred_candidate_ids": []}
        return ag1.CognitiveDecisionResponse(model_run_ref="model_run:stub:%d" % self.call_count,
                                             model_id=self.model_id,
                                             model_version=self.model_version,
                                             raw_output=raw, latency_ms=1)


class StubWriter:
    """确定性替身内容生成：真的写一段新文字，交给真实 Supply owner 做 CAS。"""

    def __init__(self, note: str = "（替身内容生成）") -> None:
        self.note = note
        self.call_count = 0

    def next_content(self, point, *, observed_at: str) -> str:
        self.call_count += 1
        return "%s\n\n%s %s\n" % (point.content or "", self.note, observed_at)


class StubObservation:
    """观察缝的替身：读的是**同一份** ``world_state.json``（gateway 也读它）。

    被替换的只有 socket 传输；候选集来自真实的 ``action_intent.build_action_space``，
    位置事实来自真实 World owner，从不扫描全局世界假装已见。
    """

    def __init__(self, environment) -> None:
        self.environment = dict(environment)
        self.last_observation = None
        self.last_action_space = None

    def __call__(self):
        from datetime import datetime, timezone

        import action_intent
        import world_foundation as wf
        import world_living_skeleton as wls
        import world_move_opportunity_source as wmos

        home = Path(self.environment["CHIYO_WORLD_HOME"])
        state = wf.WorldStateStore(home / "data" / "world" / "world_state.json").load() or {}
        # 真实 gateway 的 snapshot 是「位置 + 姿态 + 可见物体」；这里补上同一形状的两个非位置
        # 字段，位置事实仍然只来自 owner 自己写的 world_state.json。
        # 真实 gateway 的 snapshot 里世界修订号叫 world_revision；owner 自己写的是 revision。
        state.setdefault("world_revision", state.get("revision"))
        state = dict(state)
        state.setdefault("pose", "standing")
        state.setdefault("visible_objects", [])
        self.last_observation = dict(state)
        place = (state.get("location") or {}).get("place_id")
        catalog = {item["location_id"]: item for item in wls.location_catalog(self.environment)}
        current = catalog.get(str(place)) or {}
        offered = [{"location_id": neighbor, "label": neighbor}
                   for neighbor in (current.get("reachable") or [])]
        space = action_intent.build_action_space(
            state, available_actions={"actions": [{"verb": "MOVE", "available": True,
                                                   "candidates": offered}]})
        visible = action_intent.redact_action_space_for_selector(space)
        self.last_action_space = visible
        moment = datetime.now(timezone.utc).isoformat()
        return wmos.observed_move_opportunities(state, action_space=visible,
                                               occurred_at=moment, observed_at=moment)

class CycleTest(OneDayTestCase):
    """真实 ProductionLifeRuntime + 真实 Supply/World owner + 替身决策/内容/观察。"""

    def _runtime(self, cognition, env, data):
        import life_artifact_port as lap
        import life_integration_ag2 as ag2
        import life_runtime_production as lrp
        import managed_artifact_action_adapter as maaa
        from world_journey_action_adapter import (JourneyWorldResolver,
                                                 RealJourneyWorldActionAdapter)

        state_root = odl.life_state_root(self.instance)
        port = lap.LifeArtifactPort(str(self.services.supply_path), data["subject_id"])
        adapter = maaa.SupplyManagedArtifactActionAdapter(
            port, grant_id=data["artifact_grant_id"], workspace_id=data["workspace_id"])
        switches = ag2.LifeRuntimeKillSwitches(
            life_runtime_enabled=True, agency_enabled=True, action_execution_enabled=True,
            proactive_enabled=False)
        runtime = lrp.build_production_life_runtime(
            state_root, kill_switches=switches, cognition_adapter=cognition,
            install_root=self.instance / "life", artifact_action_adapter=adapter,
            world_action_adapter=RealJourneyWorldActionAdapter(
                JourneyWorldResolver(environment=env)))
        # W05R-R1: tracked so ``tearDown`` can close it while the instance tree still
        # exists (the base class's tearDown deletes that tree before unittest runs the
        # addCleanup callbacks, and a runtime closed after that cannot flush its state).
        self._runtimes.append(runtime)
        self.addCleanup(runtime.close)
        return runtime, port

    def _assembled(self, *, policy="artifact"):
        import autonomous_life_assembly as assembly

        data = self._prepared_instance()
        env = self.instance_env()
        cognition, writer = StubCognition(policy), StubWriter()
        runtime, port = self._runtime(cognition, env, data)
        assembled = assembly.assemble_autonomous_life(
            runtime, environment=env, state_root=odl.life_state_root(self.instance),
            world_provider=StubObservation(env), writer=writer, enabled=True)
        self.assertEqual(assembled["state"], assembly.READY, assembled)
        return SimpleNamespace(runtime=runtime, assembled=assembled, cycle=assembled["cycle"],
                               cognition=cognition, writer=writer, env=env, data=data, port=port)

    def _world_cycle(self, *, policy="move"):
        """没有作品可续时的 cycle：世界观察就是材料（去一个她真正看见的地方）。"""

        import autonomous_life_cycle as alc

        data = self._prepared_instance()
        env = self.instance_env()
        cognition, writer = StubCognition(policy), StubWriter()
        runtime, _port = self._runtime(cognition, env, data)
        cycle = alc.AutonomousLifeCycle(runtime, artifact_port=None,
                                        world_opportunity_provider=StubObservation(env),
                                        writer=writer, enabled=True, env=env)
        return SimpleNamespace(runtime=runtime, cycle=cycle, cognition=cognition,
                               writer=writer, env=env, data=data)

    def test_world_choice_departs_without_arriving_and_is_settled_by_real_time(self):
        fixture = self._world_cycle(policy="move")
        origin = _current_place(self.world_home())
        out = fixture.cycle.evaluate_once(trigger_ref="evt:test:move", act=True)
        self.assertEqual(out.get("outcome"), "IN_TRANSIT", out)
        self.assertEqual(out.get("step_path"), "WORLD_AR0")
        # 出发 ≠ 到达：位置权威仍在原处，也没有给 LR-5 任何到达观测
        self.assertEqual(_current_place(self.world_home()), origin)
        self.assertIsNone(out.get("progress_state"))
        self.assertEqual(out.get("location_id"), origin)
        self.assertEqual(out["journey"]["location_id"], origin)
        self.assertTrue(out["journey"]["journey_id"])
        self.assertIsNot(out["journey"]["arrived"], True)

        # 真实时间之前结算：不认领到达
        from datetime import datetime, timedelta

        journey_file = self.world_home() / "data" / "world" / "world_journey.json"
        stored = json.loads(journey_file.read_text(encoding="utf-8"))
        arrival = stored["active"]["segments"][0]["expected_arrival_at"]
        self.assertTrue(arrival)
        due = datetime.fromisoformat(arrival.replace("Z", "+00:00"))
        early = fixture.cycle.settle_world_journey(
            observed_at=(due - timedelta(hours=1)).isoformat())
        self.assertTrue(early["ok"])
        self.assertFalse(early["arrived"])
        self.assertEqual(_current_place(self.world_home()), origin)

        # 真正到达之后（注入到期时间）结算：位置由 owner 改到目的地，才认领到达
        late = fixture.cycle.settle_world_journey(observed_at=arrival.replace("Z", "+00:00"))
        self.assertTrue(late["ok"], late)
        self.assertEqual(late["journey_status"], "completed")
        self.assertEqual(late["observed_location_id"], stored["active"]["target_location_id"])
        self.assertTrue(late["arrived"])
        self.assertEqual(_current_place(self.world_home()),
                         stored["active"]["target_location_id"])

    def test_artifact_choice_commits_a_real_supply_version(self):
        fixture = self._assembled(policy="artifact")
        data = fixture.data
        before = odl.supply_call(self.services.supply_path,
                                 {"op": "read_head", "subject": data["subject_id"],
                                  "artifact_id": data["artifact_id"]})
        out = fixture.cycle.evaluate_once(trigger_ref="evt:test:work", act=True)
        self.assertEqual(out.get("outcome"), "ACTED", out)
        self.assertGreaterEqual(fixture.writer.call_count, 1)
        after = odl.supply_call(self.services.supply_path,
                                {"op": "read_head", "subject": data["subject_id"],
                                 "artifact_id": data["artifact_id"]})
        self.assertNotEqual(after["head"]["version_id"], before["head"]["version_id"])
        read = odl.supply_call(self.services.supply_path,
                               {"op": "read_artifact", "subject": data["subject_id"],
                                "artifact_id": data["artifact_id"]})
        self.assertIn("替身内容生成", json.dumps(read, ensure_ascii=False))

    def test_no_action_and_defer_change_nothing(self):
        for policy in ("no_action", "defer"):
            with self.subTest(policy=policy):
                self.setUp()
                try:
                    fixture = self._assembled(policy=policy)
                    data = fixture.data
                    before = odl.supply_call(
                        self.services.supply_path,
                        {"op": "read_head", "subject": data["subject_id"],
                         "artifact_id": data["artifact_id"]})
                    out = fixture.cycle.evaluate_once(trigger_ref="evt:test:none", act=True)
                    self.assertIn(out.get("outcome"), {"NO_ACTION", "DEFERRED"}, out)
                    after = odl.supply_call(
                        self.services.supply_path,
                        {"op": "read_head", "subject": data["subject_id"],
                         "artifact_id": data["artifact_id"]})
                    self.assertEqual(after["head"]["version_id"], before["head"]["version_id"])
                    self.assertEqual(fixture.writer.call_count, 0)
                    self.assertFalse((self.world_home() / "data" / "world"
                                      / "world_journey.json").exists())
                    self.assertEqual(control.list_recent_actions(str(self.instance / "life"),
                                                                 limit=5), [])
                finally:
                    self._teardown()

    def test_pause_holds_the_window_without_any_model_call(self):
        fixture = self._world_cycle(policy="move")
        odl.set_pause(self.instance, True, reason="test")
        out = fixture.cycle.evaluate_once(trigger_ref="evt:test:held", act=True)
        self.assertEqual(out.get("outcome"), "PAUSED_BY_OPERATOR", out)
        self.assertEqual(out.get("llm_calls"), 0)
        self.assertEqual(fixture.cognition.call_count, 0)
        self.assertFalse((self.world_home() / "data" / "world" / "world_journey.json").exists())
        odl.set_pause(self.instance, False, reason="test")
        out = fixture.cycle.evaluate_once(trigger_ref="evt:test:resumed", act=True)
        self.assertEqual(out.get("outcome"), "IN_TRANSIT", out)

    def test_second_writer_is_refused(self):
        import life_runtime_production as lrp

        data = self._prepared_instance()
        env = self.instance_env()
        runtime, _port = self._runtime(StubCognition("artifact"), env, data)
        import activity_continuity as ac

        self.assertTrue(runtime.lease.held)
        with self.assertRaises(ac.ActivityLockError):
            lrp.build_production_life_runtime(
                odl.life_state_root(self.instance), install_root=self.instance / "life")
        # 同一条路径的入口级表现：第二个 writer 被拒绝，而不是等到它写坏东西
        self.assertEqual(
            odl.run(self.instance, once=True, duration_seconds=1)["exit_code"],
            odl.EXIT_LEASE_HELD)

    def test_a_stopped_process_is_not_backfilled(self):
        fixture = self._assembled(policy="no_action")
        runner = fixture.assembled["runner"]
        first = runner.run_due(act=False)
        self.assertEqual(first.kind, "CHECK_WINDOW")
        self.assertNotEqual(first.status, "NOT_DUE")
        second = runner.run_due(act=False)
        self.assertEqual(second.status, "NOT_DUE")
        self.assertEqual(second.status_class, "silence")


class ReportTest(OneDayTestCase):
    def _write_records(self, rows, *, counters=None, running=False, poll=1800):
        state_root = odl.life_state_root(self.instance)
        state_root.mkdir(parents=True, exist_ok=True)
        (state_root / "resident_journal.jsonl").write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
            encoding="utf-8")
        (state_root / "resident_summary.json").write_text(json.dumps({
            "kind": "autonomous_life_resident", "running": running, "ticks": len(rows),
            "poll_seconds": poll, "started_at": rows[0]["at"] if rows else None,
            "counters": dict(counters or {}),
            "soak_24h": "SOAK_RUNNING_24H"}), encoding="utf-8")

    def test_report_never_passes_prematurely_and_counts_downtime(self):
        self.prepare_instance()
        self._write_records([{"at": "2026-10-14T00:00:00+00:00", "tick": 1},
                             {"at": "2026-10-14T03:30:00+00:00", "tick": 2}],
                            counters={"owner_confirmed_versions": 3, "actions": 3})
        report = odl.build_report(self.instance, target_seconds=86400)
        self.assertEqual(report["verdict"], odl.VERDICT_NOT_PASS)
        self.assertEqual(report["window"]["coverage_seconds"], 12600)
        self.assertEqual(report["window"]["downtime_seconds"], 12600)
        self.assertEqual(len(report["window"]["gaps"]), 1)
        self.assertEqual(report["window"]["observed_seconds"], 0)
        self.assertFalse(report["read_only"] is False)

    def test_a_full_but_idle_window_is_not_a_pass(self):
        self.prepare_instance()
        rows = _every_30_minutes(51)
        self._write_records(rows, counters={"owner_confirmed_versions": 0, "actions": 0,
                                            "no_action": 50})
        report = odl.build_report(self.instance, target_seconds=86400)
        self.assertGreaterEqual(report["window"]["observed_seconds"], 86400)
        self.assertEqual(report["verdict"], odl.VERDICT_NOT_PASS)
        self.assertIn("zero-action", report["verdict_basis"])

    def test_a_full_window_with_a_real_result_passes_and_a_stopped_run_does_not(self):
        self.prepare_instance()
        rows = _every_30_minutes(51)
        self._write_records(rows, counters={"owner_confirmed_versions": 2, "actions": 2})
        self.assertEqual(odl.build_report(self.instance,
                                          target_seconds=86400)["verdict"],
                         odl.VERDICT_PASS)
        # 同一份日志，但中间有 7 小时停机：覆盖仍超过 24h，却不算一天的生活
        base_rows = _every_30_minutes(64)
        gapped = base_rows[:20] + base_rows[33:]
        self._write_records(gapped, counters={"owner_confirmed_versions": 2, "actions": 2})
        interrupted = odl.build_report(self.instance, target_seconds=86400)
        self.assertGreater(interrupted["window"]["coverage_seconds"], 86400)
        self.assertGreater(interrupted["window"]["downtime_seconds"], 0)
        self.assertEqual(interrupted["verdict"], odl.VERDICT_INTERRUPTED)

    def test_report_on_a_silent_instance_is_insufficient_evidence(self):
        self.prepare_instance()
        report = odl.build_report(self.instance, target_seconds=86400)
        self.assertEqual(report["verdict"], odl.VERDICT_NO_EVIDENCE)
        self.assertIn("not_claimed", report)
"""W05-owner-chain：真实邻居拓扑上的**物理 owner 链**（World 层，不是 Life 自主）。

本包只证明 World owner 一侧的事：真实 ``examples/worlds/daily_neighborhood_v1`` 世界
（真 ``init_instance``）+ 真 ``WorldBodyService``（真 gateway UDS、真授权租约、真
Resolver、真 travel 记录）+ ``FormalOwnerMoveSubmitPort``（Life 侧的正式授权入口，**只读
引用**，不绕过授权、不直接调 Resolver 的 commit）。

证据形态：一个数据根上先跑若干正时长的单跳，经 ``world_journey.accept_journey`` /
``advance_journey`` 在真实拓扑上绕 10 条边
（home→street_front→street_corner→park→street_corner→cafe→street_corner→
street_front→reading_point→street_front→home）。每段都断言：

* 提交成功 = **出发，不是到达**（位置仍在段起点，owner travel 记录 ``IN_PROGRESS``）；
* **到期前**位置不变（对 owner 自己的 due 时刻做**只读**读取，不写任何账本）；
* **到期后**是一次真实到达（``realize_due_travel`` 真有落盘，位置变成段终点）；
* execution id / travel id / 对账结论始终归属同一段；完成的旅程不会重复 spawn 新提交。

两条恢复边界（各自独立、只跑一次）：

* 暂停中的在途段：World 控制位暂停（不是伪 Life operator pause）→ 已发段可以到达，但本
  旅程的下一段不出发；关掉原 service lease 后，用**真新进程/新 PID** 在**同一数据根**上以
  **新的 UDS 绝对路径**重建，原 socket 保留（不 unlink），再读原 journey / execution /
  owner ledger；resume 之后只允许合法的新段。
* 真实 owner 已提交、回执丢失：故障注入在 test 的传输层（gateway surface 上挂住回执 +
  读查询失败），同 execution 只读对账 → 认领为 ``started``，提交数恒 1、对应 travel 恰好 1；
  之后再推进只对账、不重复提交。

时钟（必须照写，不许含糊）：**正式 submit 接口内部的 owner 回执时钟不可注入** ——
``world_action_resolver`` 调 ``world_living_skeleton.apply_move_to`` 时不传 ``now``
（``world_action_resolver.py:942-947``），所以 travel 的 ``started_at/expected_arrival_at``
永远是 owner 进程的墙钟。本包的时钟纪律是：journey 侧一律显式传 aware ``now``，取值**只由
owner 公布的 ``expected_arrival_at`` 推出**（到期前 = ``due-1s`` 只读、到期后 = ``due+1s``
推进），从不真睡 1320 秒的走时，也**不宣称**\"owner 与 journey 共用同一个注入时钟\"。
多段加速后 journey 侧时钟会领先 owner 回执（见报告里的实测漂移），因此段内\"到期前\"只用
**只读**读取观察，绝不用倒退的时刻写账本。

边界（不夸称）：没有 Life 自主、没有 6 地点内景/消费、没有真模型/外网；Life 侧生活集成由
另一位 writer 负责。夹具全部落在 ``/tmp/nyaw05r.jdK3q/nb-owner-*``，**永久保留**：不
rmtree、不 unlink、不 TemporaryDirectory 清理、不删 pyc。

需要 POSIX（fcntl + AF_UNIX）；原生 Windows 自动跳过。
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "tests"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge"),
              str(ROOT / "components" / "life" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from components.worldgen.instance import init_instance  # noqa: E402

import action_intent as ai  # noqa: E402
import world_body_authority as wba  # noqa: E402
import world_body_config as wcfg  # noqa: E402
import world_body_gateway_api as gapi  # noqa: E402
import world_body_service as wbsvc  # noqa: E402
import world_living_skeleton as wls  # noqa: E402
import world_runtime_data as wrd  # noqa: E402
import world_travel_delivery as wtd  # noqa: E402
import world_body_client as wbc  # noqa: E402
from chiyo_bundle import world_control  # noqa: E402
from chiyo_bundle import world_journey as journey  # noqa: E402
from world_action_bridge_adapter import WorldBridgeResolver  # noqa: E402
from world_journey_action_adapter import FormalOwnerMoveSubmitPort  # noqa: E402

posix_only = unittest.skipIf(
    os.name == "nt", "需要 POSIX 文件锁与 AF_UNIX（在 WSL/Linux 上运行）"
)

#: 所有夹具的唯一根；永久保留（本文件里没有任何删除动作）。
FIXTURE_ROOT = Path("/tmp/nyaw05r.jdK3q")
WORLD_PACKAGE = ROOT / "examples" / "worlds" / "daily_neighborhood_v1"
RUNTIME_ROOT = ROOT / "components" / "world"
BRIDGE_DIR = RUNTIME_ROOT / "service" / "bridge"

#: 真实拓扑上的 10 条边（世界包自带 6 地点、5 条道路）。
TRIPS = (
    ("park", ("home", "street_front", "street_corner", "park")),
    ("cafe", ("park", "street_corner", "cafe")),
    ("reading_point", ("cafe", "street_corner", "street_front", "reading_point")),
    ("home", ("reading_point", "street_front", "home")),
)

#: 子进程 worker 的源码：真 WorldBodyService，跑它自己的循环。
#: ``NB_RUN_DIR`` 只重定向**服务运行时目录**（socket + 租约），世界数据根不动；这样新 worker
#: 绑新的 UDS 绝对路径，不必 unlink 上一进程留下的 socket。
WORKER_SOURCE = '''\
import json
import os
import sys
import threading
import time

REPO = os.environ["NB_REPO"]
HOME = os.environ["NB_HOME"]
RUN_DIR = os.environ["NB_RUN_DIR"]
WORLD_ID = os.environ["NB_WORLD_ID"]
TICK = float(os.environ.get("NB_TICK", "0.25"))
INSTANCE = os.environ["NB_INSTANCE"]

for entry in (REPO,
              os.path.join(REPO, "components", "world", "scripts"),
              os.path.join(REPO, "components", "world", "service"),
              os.path.join(REPO, "components", "world", "service", "bridge")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import world_body_config as wcfg
import world_body_service as wbsvc


class RunDirectory:
    """Delegate the real config, override only ``run_dir`` (sockets + lease)."""

    def __init__(self, config, run_dir):
        self._config = config
        self._run_dir = run_dir

    def __getattr__(self, name):
        return getattr(self._config, name)

    @property
    def run_dir(self):
        return self._run_dir


env = dict(os.environ)
config = wcfg.load_config(env, runtime_root=os.path.join(REPO, "components", "world"),
                          world_home=HOME, runtime_mode="canonical",
                          authority="production", world_id=WORLD_ID)
if RUN_DIR and RUN_DIR != str(config.run_dir):
    config = RunDirectory(config, __import__("pathlib").Path(RUN_DIR))

service = wbsvc.WorldBodyService(config, tick_seconds=TICK, instance_id=INSTANCE)
service.startup()
GATEWAY = str(service.gateway_socket_path)
CONTROL = str(service.socket_path)


def announce():
    deadline = time.monotonic() + 30.0
    while not os.path.exists(GATEWAY) and time.monotonic() < deadline:
        time.sleep(0.02)
    print(json.dumps({"ready": True, "pid": os.getpid(), "gateway": GATEWAY,
                      "control": CONTROL, "run_dir": str(config.run_dir),
                      "started_at": service.started_at,
                      "clock": "wall (WorldBodyService default)"}), flush=True)


threading.Thread(target=announce, daemon=True).start()
try:
    service.serve(max_seconds=900.0)
finally:
    # 只关进程/租约，**不**调 shutdown()：shutdown 会 unlink 自己绑过的 socket，
    # 而本包要求原 socket 留在盘上作为\"没有被重置\"的证据。
    released = None
    try:
        released = service.lease.release() if service.lease is not None else None
    except Exception as error:
        released = {"error": "%s: %s" % (type(error).__name__, error)}
    print(json.dumps({"stopped": True, "pid": os.getpid(),
                      "lease_released": released, "ticks": service.counters["ticks"]},
                     ensure_ascii=False), flush=True)
'''


def _fixture_root(label: str) -> Path:
    """唯一的、保留的夹具根（创建一次，永不删除）。"""

    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    root = FIXTURE_ROOT / ("nb-owner-%s-%s" % (label, uuid.uuid4().hex[:10]))
    root.mkdir(parents=True, exist_ok=False)
    return root


def _instant(text: str) -> datetime:
    return datetime.fromisoformat(str(text).replace("Z", "+00:00"))


class ChainHarness:
    """一个数据根 + 真 WorldBodyService（进程内或子进程）+ 正式提交端口。"""

    def __init__(self, label: str) -> None:
        self.root = _fixture_root(label)
        self.home = self.root / "world-home"
        init_instance(WORLD_PACKAGE, self.home)
        state = json.loads((self.home / "data" / "world" / "world_state.json")
                           .read_text("utf-8"))
        self.world_id = state["world_id"]
        (self.home / "run").mkdir(parents=True, exist_ok=True)
        self.env = {
            "CHIYO_WORLD_PACKAGE": str(WORLD_PACKAGE),
            "CHIYO_WORLD_HOME": str(self.home),
            "CHIYO_WORLD_ID": self.world_id,
            "CHIYO_WORLD_LIVING_SKELETON_ENABLED": "1",
            "CHIYO_WORLD_TRAVEL_TIME_ENABLED": "1",
            "CHIYO_WORLD_DAILY_RHYTHM_ENABLED": "0",
            "CHIYO_WORLD_AMBIENT_EVENTS_ENABLED": "0",
            "CHIYO_WORLD_SOCIAL_PRESENCE_ENABLED": "0",
            "WORLD_ACTION_AUTH_REGISTRY_DIR": str(self.home / "run"),
            "WORLD_ACTION_LIFECYCLE_PATH": str(self.home / "run" / "action_lifecycle.jsonl"),
            ai.GATE_ENV: ai.GATE_CANONICAL,
            "WORLD_BODY_INTEGRATION_MODE": "canonical",
            "NYAIRO_WORLD_BRIDGE_DIR": str(BRIDGE_DIR),
        }
        self.saved_env = dict(os.environ)
        os.environ.update(self.env)
        wrd.reset_cache()
        self.service = None
        self.thread = None
        self.workers: list[subprocess.Popen] = []

    # -- 生命周期（只停进程/线程/租约，不删任何路径） ----------------------

    def close(self) -> None:
        for proc in self.workers:
            if proc.poll() is None:
                self._terminate(proc)
        if self.service is not None:
            try:
                self.service.lease.release()
            except Exception:
                pass
            if self.thread is not None:
                self.thread.join(timeout=5)
        try:
            os.environ.clear()
            os.environ.update(self.saved_env)
        finally:
            wrd.reset_cache()

    # -- 服务 --------------------------------------------------------------

    def start_service(self, *, run_dir: Path | None = None,
                      tick_seconds: float = 3600.0, instance_id: str = "") -> Path:
        """进程内真服务（同一个 PID；子进程重启见 ``start_worker``）。"""

        config = wcfg.load_config(dict(self.env), runtime_root=RUNTIME_ROOT,
                                 world_home=self.home, runtime_mode="canonical",
                                 authority="production", world_id=self.world_id)
        if run_dir is not None:
            config = _RunDirectory(config, run_dir)
        self.service = wbsvc.WorldBodyService(
            config, tick_seconds=tick_seconds,
            instance_id=instance_id or "nb-owner-inproc-%d" % os.getpid())
        self.service.startup()
        socket_path = self.service.gateway_socket_path
        self.thread = threading.Thread(
            target=self.service.serve, kwargs={"max_seconds": 900.0}, daemon=True)
        self.thread.start()
        self._await_socket(socket_path)
        return socket_path

    def start_worker(self, *, run_dir: Path | None = None,
                     label: str = "worker") -> tuple[subprocess.Popen, dict]:
        """真子进程真新 PID；``run_dir`` 给定时绑新的 UDS 绝对路径。"""

        self.root.mkdir(parents=True, exist_ok=True)
        script = self.root / "worker.py"
        script.write_text(WORKER_SOURCE, encoding="utf-8")
        child_env = dict(os.environ)
        child_env.update(self.env)
        child_env.update({
            "NB_REPO": str(ROOT),
            "NB_HOME": str(self.home),
            "NB_RUN_DIR": str(run_dir or (self.home / "run")),
            "NB_WORLD_ID": self.world_id,
            "NB_INSTANCE": "nb-owner-%s-%s" % (label, uuid.uuid4().hex[:6]),
            "NB_TICK": "0.25",
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        proc = subprocess.Popen(
            [sys.executable, str(script)],
            cwd=str(ROOT), env=child_env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=1,
            start_new_session=True,
        )
        self.workers.append(proc)
        ready = self._await_line(proc, "ready", timeout=60.0)
        self._await_socket(Path(ready["gateway"]))
        # 夹具里的永久证据：真新进程的真 PID / socket 路径（不打印到测试输出）。
        with (self.root / "workers.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "label": label, "pid": ready["pid"], "gateway": ready["gateway"],
                "control": ready["control"], "run_dir": ready["run_dir"],
                "started_at": ready.get("started_at"),
                "clock": ready.get("clock"),
                "parent_pid": os.getpid(),
            }, ensure_ascii=False) + "\n")
        return proc, ready

    def _await_line(self, proc: subprocess.Popen, key: str, *,
                    timeout: float) -> dict:
        lines: list[dict] = []
        seen: list[str] = []
        event = threading.Event()

        def pump() -> None:
            for line in proc.stdout:  # type: ignore[union-attr]
                seen.append(line.rstrip())
                try:
                    payload = json.loads(line)
                except ValueError:
                    continue
                lines.append(payload)
                if payload.get(key):
                    event.set()

        threading.Thread(target=pump, daemon=True).start()
        if not event.wait(timeout):
            stderr = ""
            if proc.stderr is not None:
                try:
                    stderr = proc.stderr.read()[-2000:]
                except Exception:
                    stderr = "<unreadable>"
            raise AssertionError(
                "worker 没有报 %r（rc=%r）stdout=%r stderr=%r"
                % (key, proc.poll(), seen, stderr))
        return lines[-1]

    @staticmethod
    def _await_socket(path: Path, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        while not path.exists():
            if time.monotonic() > deadline:
                raise AssertionError("gateway socket 没出现: %s" % path)
            time.sleep(0.02)

    def stop_worker(self, proc: subprocess.Popen, admin_socket: Path) -> dict:
        final = self._terminate(proc, admin_socket)
        return final

    def _terminate(self, proc: subprocess.Popen,
                   admin_socket: Path | None = None) -> dict:
        """SIGTERM → 唤醒 select → 等真退出；子进程自己在 finally 里放租约。"""

        if proc.poll() is not None:
            return {"stopped": False, "pid": proc.pid, "returncode": proc.returncode}
        proc.send_signal(signal.SIGTERM)
        # serve() 的 select 只在有连接/超时时醒来，SIGTERM 只置标志位。
        for _ in range(200):
            if proc.poll() is not None:
                break
            if admin_socket is not None:
                import socket as _socket

                try:
                    with _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM) as probe:
                        probe.settimeout(0.2)
                        probe.connect(str(admin_socket))
                        probe.sendall(b'{"command": "status"}\n')
                except OSError:
                    pass
            time.sleep(0.05)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:  # pragma: no cover - 不静默吞掉
            proc.kill()
            proc.wait(timeout=30)
            raise AssertionError("worker %d 没有按 SIGTERM 退出" % proc.pid)
        self._close_pipes(proc)
        return {"stopped": True, "pid": proc.pid, "returncode": proc.returncode}

    @staticmethod
    def _close_pipes(proc: subprocess.Popen) -> None:
        """只关管道（进程已退出），不删任何盘上文件。"""

        for stream in (proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass

    # -- 端口 --------------------------------------------------------------

    def port(self, socket_path: Path, *, client: object = None) -> FormalOwnerMoveSubmitPort:
        env = dict(self.env)
        env["WORLD_BODY_SOCKET"] = str(socket_path)
        if client is None:
            bridge = WorldBridgeResolver(environment=env, bridge_dir=BRIDGE_DIR)
        else:
            bridge = WorldBridgeResolver(environment=env, bridge_dir=BRIDGE_DIR,
                                         client=client)
        return FormalOwnerMoveSubmitPort(environment=env, bridge=bridge)

    # -- 读取 --------------------------------------------------------------

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def position(self, now: datetime) -> str:
        return wls.read_move_state(hermes_home=self.home, environment=self.env,
                                   now=now)["world_location_id"]


    def raw_position(self) -> str:
        """只读盘上的世界位置，**不**触发任何到期结算（没有副作用）。"""

        state = json.loads((self.home / "data" / "world" / "world_state.json")
                           .read_text("utf-8"))
        location = state["location"]
        return wrd.active_world(self.env).state_node(location["place_id"],
                                                    location.get("area_id"))
    def travels(self) -> list[dict]:
        state = wtd.load_state(self.home, self.env)
        return [] if state is None else list(state["travels"])

    def travels_of(self, execution_id: str) -> list[dict]:
        return [item for item in self.travels()
                if item["execution_id"] == execution_id]

    def journey_state(self) -> dict:
        return journey.load_state(self.home)

    def active(self) -> dict | None:
        return self.journey_state().get("active")

    def observe(self, now: datetime) -> dict:
        return journey.observe_journey(hermes_home=self.home, environment=self.env,
                                       now=now)

    def state_bytes(self, *names: str) -> dict:
        base = self.home / "data" / "world"
        return {name: (base / name).read_bytes() if (base / name).is_file() else None
                for name in names}

    def ledger_entries(self, execution_id: str) -> list[dict]:
        path = self.home / "data" / "life_execution.jsonl"
        if not path.is_file():
            return []
        entries = []
        for line in path.read_text("utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if str(entry.get("execution_id") or "") == execution_id:
                entries.append(entry)
        return entries

    def accepted(self, target: str, *, request_key: str, port, now: datetime) -> dict:
        return journey.accept_journey(
            target=target, hermes_home=self.home, environment=self.env, now=now,
            request_key=request_key, submit_port=port)

    def advance(self, *, port, now: datetime) -> dict:
        return journey.advance_journey(
            hermes_home=self.home, environment=self.env, now=now, submit_port=port)

    def pause(self, *, paused: bool, now: datetime, reason: str) -> dict:
        with journey.journey_lock(self.home):
            return world_control.set_paused(self.home, paused=paused, now=now,
                                            reason=reason)

    def retarget_auth_registry(self, run_dir: Path) -> None:
        """让签发方与新 service 落在**同一个** run 目录。

        授权注册表是 service 的运行时状态，不是世界数据：gateway 用
        ``service.config.run_dir`` 找它（``world_body_gateway_api.py:169-174``），而签发侧
        （``intent_executor.py:242-247``）读 ``WORLD_ACTION_AUTH_REGISTRY_DIR``。新进程换了
        run 目录，签发侧就必须跟着换，否则回执会是 ``AUTHORIZATION_NOT_FOUND``。
        """

        run_dir.mkdir(parents=True, exist_ok=True)
        self.env["WORLD_ACTION_AUTH_REGISTRY_DIR"] = str(run_dir)
        os.environ["WORLD_ACTION_AUTH_REGISTRY_DIR"] = str(run_dir)


class _RunDirectory:
    """进程内版本：只覆盖 ``run_dir``（socket + 租约），世界数据根不动。"""

    def __init__(self, config: object, run_dir: Path) -> None:
        self._config = config
        self._run_dir = run_dir

    def __getattr__(self, name: str) -> object:
        return getattr(self._config, name)

    @property
    def run_dir(self) -> Path:
        return self._run_dir


@posix_only
class NeighborhoodOwnerChainTest(unittest.TestCase):
    """主链：真实拓扑 10 条边，出发 ≠ 到达，到期才真到达。"""

    def setUp(self) -> None:
        self.h = ChainHarness("chain")
        self.addCleanup(self.h.close)
        self.socket = self.h.start_service()
        self.port = self.h.port(self.socket)

    def test_ten_real_edges_depart_hold_position_then_arrive_once(self) -> None:
        walked: list[tuple[str, str]] = []
        submissions = 0
        execution_ids: list[str] = []

        for index, (target, expected_route) in enumerate(TRIPS):
            origin = self.h.position(self.h.now())
            accepted = self.h.accepted(target, request_key="chain-%d" % index,
                                       port=self.port, now=self.h.now())
            self.assertEqual(accepted["status"], "started", accepted)
            self.assertEqual(accepted["reason_code"], "TRAVEL_STARTED", accepted)
            route = (origin,) + tuple(accepted["route"] or ())
            self.assertEqual(route, expected_route, accepted)
            submissions += 1
            walked.extend(zip(route, route[1:]))

            while True:
                active = self.h.active()
                self.assertIsNotNone(active, "旅程在 %s 处丢了" % target)
                assert active is not None
                if active["status"] == "completed":
                    self.assertEqual(active["target_location_id"], target, active)
                    break
                self.assertEqual(active["status"], "in_transit", active)
                segment = active["segments"][active["current_segment"]]
                execution_id = segment["execution_id"]
                execution_ids.append(execution_id)
                destination = segment["destination_location_id"]
                origin_here = (active["origin_location_id"] if segment["index"] == 0
                               else active["route"][segment["index"] - 1])
                self.assertEqual(destination, route[segment["index"] + 1], segment)

                # ① 出发 ≠ 到达：刚提交完，位置仍在段起点，owner 记录是在途。
                self.assertEqual(segment["status"], "started", segment)
                self.assertEqual(self.h.position(self.h.now()), origin_here)
                records = self.h.travels_of(execution_id)
                self.assertEqual(len(records), 1, records)
                travel = records[0]
                self.assertEqual(travel["status"], "IN_PROGRESS", travel)
                self.assertEqual(travel["origin_location_id"], origin_here, travel)
                self.assertEqual(travel["destination_location_id"], destination, travel)
                # 旅程账本里的出发时刻/预计到达全部取自 owner 的回执。
                self.assertEqual(segment["travel_id"], travel["travel_id"])
                self.assertEqual(segment["started_at"], travel["started_at"])
                self.assertEqual(segment["expected_arrival_at"],
                                 travel["expected_arrival_at"])

                due = _instant(travel["expected_arrival_at"])

                # ② 到期前：位置不变（对 owner 公布的 due 做**只读**观察）。
                before = due - timedelta(seconds=1)
                self.assertEqual(self.h.position(before), origin_here,
                                 "到期前位置被改动了（段 %s）" % execution_id)
                projection = self.h.observe(before)
                self.assertEqual(projection["status"], "in_transit", projection)
                self.assertEqual(projection["segments_confirmed"],
                                 active["current_segment"], projection)
                self.assertEqual(projection["destination_location_id"], destination,
                                 projection)

                # ③ 到期后：真实到达（owner 自己的实时结算把位置写过去）。
                settled = self.h.advance(port=self.port, now=due + timedelta(seconds=1))
                self.assertEqual(settled["segments_confirmed"],
                                 active["current_segment"] + 1, settled)
                self.assertNotIn(settled["status"], {"blocked", "unknown"}, settled)
                self.assertEqual(self.h.raw_position(), destination,
                                 "到期后没有真实到达（段 %s）" % execution_id)
                arrived = self.h.travels_of(execution_id)
                self.assertEqual(len(arrived), 1, arrived)
                self.assertEqual(arrived[0]["status"], "ARRIVED", arrived)
                submissions = self.port.submissions

        # 主链自证：10 条真实边、10 次提交、10 条 travel、10 个不同 execution id。
        self.assertEqual(len(walked), 10, walked)
        self.assertEqual(walked, [
            ("home", "street_front"),
            ("street_front", "street_corner"),
            ("street_corner", "park"),
            ("park", "street_corner"),
            ("street_corner", "cafe"),
            ("cafe", "street_corner"),
            ("street_corner", "street_front"),
            ("street_front", "reading_point"),
            ("reading_point", "street_front"),
            ("street_front", "home"),
        ])
        self.assertEqual(self.port.submissions, 10)
        self.assertEqual(len(execution_ids), 10)
        self.assertEqual(len(set(execution_ids)), 10)
        travels = self.h.travels()
        self.assertEqual(len(travels), 10)
        self.assertEqual(sorted(item["execution_id"] for item in travels),
                         sorted(execution_ids))
        self.assertEqual(self.h.raw_position(), "home")

        # 完成的旅程不会重复 spawn：再推进仍是 completed，提交数不变。
        final = self.h.advance(port=self.port, now=self.h.now() + timedelta(seconds=5))
        self.assertEqual(final["status"], "completed", final)
        self.assertEqual(self.port.submissions, 10)
        self.assertEqual(len(self.h.travels()), 10)
        state = self.h.journey_state()
        archived = list(state.get("history") or [])
        current = state.get("active") or {}
        # 一次只允许一个旅程：前 3 段旅程已归档，最后一段是当前（已完成）的旅程。
        self.assertEqual(len(archived) + (1 if current else 0), len(TRIPS), archived)
        self.assertEqual(current.get("status"), "completed", current)
        self.assertEqual(current.get("target_location_id"), "home", current)
        for item in archived:
            self.assertEqual(item.get("status"), "completed", item)
        for execution_id in execution_ids:
            committed = [entry for entry in self.h.ledger_entries(execution_id)
                         if entry.get("status") == "committed"]
            self.assertEqual(len(committed), 1, (execution_id, committed))
            self.assertEqual(committed[0]["reason_code"], "move_departed")


@posix_only
class PausedSegmentRestartTest(unittest.TestCase):
    """在途段暂停 + 真新进程/新 socket 在同一数据根上重建。"""

    def setUp(self) -> None:
        self.h = ChainHarness("restart")
        self.addCleanup(self.h.close)

    def test_a_paused_segment_arrives_but_starts_nothing_until_resume(self) -> None:
        first_proc, first = self.h.start_worker(label="w1")
        socket_old = Path(first["gateway"])
        port = self.h.port(socket_old)

        accepted = self.h.accepted("park", request_key="restart-1", port=port,
                                   now=self.h.now())
        self.assertEqual(accepted["status"], "started", accepted)
        self.assertEqual(tuple(accepted["route"]),
                         ("street_front", "street_corner", "park"), accepted)
        active = self.h.active()
        assert active is not None
        segment = active["segments"][0]
        execution_id = segment["execution_id"]
        travel = self.h.travels_of(execution_id)[0]
        due = _instant(travel["expected_arrival_at"])
        self.assertEqual(self.h.position(self.h.now()), "home")
        self.assertEqual(port.submissions, 1)

        # World 控制位暂停（不是伪 Life operator pause）。
        self.h.pause(paused=True, now=self.h.now(), reason="owner-chain-test")
        held = self.h.advance(port=port, now=self.h.now())
        # 在途段照旧如实报告（不因为暂停而谎称"没出发"）；闸门只挡**新段**。
        self.assertEqual(held["status"], "in_transit", held)
        self.assertEqual(held["segment_status"], "started", held)
        self.assertEqual(port.submissions, 1)
        self.assertEqual(len(self.h.travels()), 1)

        # 暂停下到期前：位置仍不变；已发段仍可到达。
        self.assertEqual(self.h.position(due - timedelta(seconds=1)), "home")
        before_restart = {
            "journey": self.h.state_bytes("world_journey.json"),
            "travel": self.h.state_bytes("world_travel_delivery.json"),
            "world": self.h.state_bytes("world_state.json"),
        }
        committed_before = [entry for entry in self.h.ledger_entries(execution_id)
                            if entry.get("status") == "committed"]
        self.assertEqual(len(committed_before), 1, committed_before)
        old_socket_stat = os.lstat(socket_old)
        self.assertEqual(os.geteuid(), old_socket_stat.st_uid)

        # 关掉原 service（进程 + 租约），**不** unlink 它留下的 socket。
        final = self.h.stop_worker(first_proc, Path(first["control"]))
        self.assertEqual(final["returncode"], 0, final)
        self.assertTrue(final["stopped"], final)
        self.assertFalse(wba.lock_is_held(self.h.home / "run"),
                         "原 service 的租约没有释放")
        new_stat = os.lstat(socket_old)
        self.assertEqual((new_stat.st_dev, new_stat.st_ino),
                         (old_socket_stat.st_dev, old_socket_stat.st_ino),
                         "原 socket 被 unlink 或替换了")

        # 同数据根重建：**真新进程/新 PID + 新 UDS 绝对路径**，旧 socket 原样保留。
        second_proc, second = self.h.start_worker(
            run_dir=self.h.root / "worker2-run", label="w2")
        self.assertNotEqual(second["pid"], first["pid"])
        self.assertNotEqual(first["pid"], second_proc.pid)
        socket_new = Path(second["gateway"])
        self.assertNotEqual(socket_new, socket_old)
        self.assertEqual(Path(second["run_dir"]), self.h.root / "worker2-run")
        self.assertTrue(wba.lock_is_held(self.h.root / "worker2-run"),
                        "新进程没有持有自己的租约")
        after_restart_stat = os.lstat(socket_old)
        self.assertEqual((after_restart_stat.st_dev, after_restart_stat.st_ino),
                         (old_socket_stat.st_dev, old_socket_stat.st_ino),
                         "新进程 unlink 了旧 socket")

        # 同数据根：原 journey / execution / owner ledger 没有被重置或改写。
        self.assertEqual(self.h.state_bytes("world_journey.json")["world_journey.json"],
                         before_restart["journey"]["world_journey.json"])
        self.assertEqual(
            self.h.state_bytes("world_travel_delivery.json")["world_travel_delivery.json"],
            before_restart["travel"]["world_travel_delivery.json"])
        self.assertEqual(self.h.state_bytes("world_state.json")["world_state.json"],
                         before_restart["world"]["world_state.json"])
        recovered = self.h.active()
        assert recovered is not None
        self.assertEqual(recovered["status"], "in_transit", recovered)
        self.assertEqual(recovered["segments"][0]["execution_id"], execution_id)
        self.assertEqual(recovered["segments"][0]["travel_id"], travel["travel_id"])
        self.assertEqual([item["execution_id"] for item in self.h.travels()],
                         [execution_id])
        committed_after = [entry for entry in self.h.ledger_entries(execution_id)
                           if entry.get("status") == "committed"]
        self.assertEqual(len(committed_after), 1, committed_after)

        # 新进程上继续：暂停仍然挡住"新段"，但已发段可以到达。
        # 授权注册表是 service 运行时状态：新 run 目录 = 新注册表（见 retarget_auth_registry）。
        self.h.retarget_auth_registry(self.h.root / "worker2-run")
        port_new = self.h.port(socket_new)
        still_paused = self.h.advance(port=port_new, now=due - timedelta(seconds=1))
        self.assertEqual(still_paused["status"], "in_transit", still_paused)
        self.assertEqual(still_paused["segments_confirmed"], 0, still_paused)
        self.assertEqual(port_new.submissions, 0)
        arrival = self.h.advance(port=port_new, now=due + timedelta(seconds=1))
        self.assertEqual(arrival["segments_confirmed"], 1, arrival)
        self.assertEqual(arrival["status"], "paused", arrival)
        self.assertEqual(self.h.raw_position(), "street_front")
        self.assertEqual(port_new.submissions, 0)
        self.assertEqual(len(self.h.travels()), 1)

        # resume 之后只允许合法的新段（新 execution id、起点=刚到达的地点）。
        self.h.pause(paused=False, now=due + timedelta(seconds=3), reason="owner-chain-test")
        resumed = self.h.advance(port=port_new, now=due + timedelta(seconds=4))
        self.assertEqual(resumed["status"], "started", resumed)
        self.assertEqual(resumed["reason_code"], "TRAVEL_STARTED", resumed)
        self.assertEqual(port_new.submissions, 1)
        travels = self.h.travels()
        self.assertEqual(len(travels), 2, travels)
        fresh = [item for item in travels if item["execution_id"] != execution_id]
        self.assertEqual(len(fresh), 1, fresh)
        self.assertEqual(fresh[0]["origin_location_id"], "street_front")
        self.assertEqual(fresh[0]["destination_location_id"], "street_corner")
        self.assertEqual(fresh[0]["status"], "IN_PROGRESS")
        stored = self.h.active()
        assert stored is not None
        self.assertEqual(stored["segments"][1]["execution_id"], fresh[0]["execution_id"])
        self.assertEqual(stored["segments"][1]["travel_id"], fresh[0]["travel_id"])
        self.assertEqual(stored["segments"][1]["started_at"], fresh[0]["started_at"])
        # 到达本身没有改 execution id / travel id。
        self.assertEqual(stored["segments"][0]["execution_id"], execution_id)
        self.assertEqual(stored["segments"][0]["travel_id"], travel["travel_id"])
        self.assertEqual(stored["segments"][0]["status"], "confirmed")

        second_final = self.h.stop_worker(second_proc, Path(second["control"]))
        self.assertEqual(second_final["returncode"], 0, second_final)


@posix_only
class LostReplyAfterRealCommitTest(unittest.TestCase):
    """真实 owner 已提交、回执丢失：同 execution 只读对账，提交数恒 1。"""

    def setUp(self) -> None:
        self.h = ChainHarness("lostreply")
        self.addCleanup(self.h.close)
        self.release = threading.Event()
        self.commit_done = threading.Event()
        self.submissions_at_gateway: list[dict] = []

    # -- 故障注入（test 传输层，不绕过授权） --------------------------------

    def _lost_reply(self):
        real_submit = gapi.GatewaySurface.submit_action
        # 读查询那一路直接注入失败（不调用真实实现），回执窗口内拿不到判决。
        state = self

        def slow_submit(surface, request):
            result = real_submit(surface, request)  # 真实 Resolver 提交 + 账本 committed
            state.submissions_at_gateway.append(result)
            state.commit_done.set()
            state.release.wait(30)
            return result

        def unreadable(_surface, _execution_id):
            # 读查询也不可达：owner 的判决在盘上，但回执窗口内拿不到。
            raise TimeoutError("gateway read window closed (test transport fault)")

        return mock.patch.object(gapi.GatewaySurface, "submit_action", slow_submit), \
            mock.patch.object(gapi.GatewaySurface, "get_action_result", unreadable)

    def _release_after_commit_and_give_up(self, client,
                                          timeout: float = 60.0) -> None:
        """事件屏障：**两条事实都成立**才放行迟到的回执，两条都是有意义的信号。

        * ``client.metrics.submitted >= 1`` —— 客户端已经等满自己的 deadline（否则回执会被
          正常送达，故障窗口根本没发生）。不能用"读次数"当路标：执行器在提交前自己就会读快照。
        * ``self.commit_done`` —— owner 真的提交并落账完毕。

        只等客户端会**提前放行**：真实 Resolver 的提交在机器繁忙时要 >1s，比客户端的 0.4s 预算慢，
        于是"提交是否已发生"变成竞速（父级同进程验收里就是这样红的）。等两条事实，时序就变成有向的
        事实链，既不 sleep 碰绿，也不放宽任何断言。
        """

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.commit_done.is_set() and client.metrics.submitted >= 1:
                break
            time.sleep(0.005)
        self.release.set()

    def test_a_lost_receipt_claims_the_owner_departure_without_a_second_submit(self) -> None:
        socket_path = self.h.start_service()
        client = wbc.WorldBodyClient(environment={**self.h.env,
                                                  "WORLD_BODY_SOCKET": str(socket_path)},
                                     timeout=0.4, mutation_timeout=0.4, read_retries=0)
        port = self.h.port(socket_path, client=client)
        submit_patch, read_patch = self._lost_reply()
        watchdog = threading.Thread(
            target=self._release_after_commit_and_give_up, args=(client,), daemon=True)
        with submit_patch, read_patch:
            watchdog.start()
            accepted = self.h.accepted("street_front", request_key="lost-1",
                                       port=port, now=self.h.now())
        watchdog.join(timeout=70)
        # 有界等待"真实提交真的落账"：屏障保证回执迟到，但不假设提交有多快（机器繁忙时 >1s）。
        self.assertTrue(self.commit_done.wait(60.0), "真实 owner 提交没有发生")
        # 故障窗口必须真实存在：客户端提交恰好一次，并且是自己等满 deadline 才放弃。
        self.assertEqual(client.metrics.submitted, 1)
        self.assertGreaterEqual(client.metrics.timeouts, 1, client.metrics.as_dict())
        # 端口的归类必须是"不确定"，而不是成功、也不是拒绝。
        self.assertEqual(port.last_result["intent_status"], ai.STATUS_UNKNOWN,
                         port.last_result)

        # ① 故障窗口本身：端口只报"不确定"，而 owner 已经落账了一条出发，位置不动。
        #    顺序事实：旅程在 accept 内部只对账一次，那一刻 owner 的 travel 记录**可能还没出现**
        #    （真实 Resolver 的提交在机器繁忙时会比客户端 0.4s 预算慢）。两种中间态都合法且都
        #    非终态：unknown（还没有证据）或 started（已经认领）。终态 blocked/completed 一律不许。
        self.assertIn(accepted["status"], {"unknown", "started", "in_transit"}, accepted)
        self.assertNotIn(accepted["status"], {"blocked", "completed"}, accepted)
        execution_id = port.last_intent["execution_id"]
        records = self.h.travels_of(execution_id)
        self.assertEqual(len(records), 1, records)
        travel = records[0]
        self.assertEqual(travel["status"], "IN_PROGRESS", travel)
        self.assertEqual(travel["origin_location_id"], "home", travel)
        self.assertEqual(travel["destination_location_id"], "street_front", travel)
        self.assertEqual(self.h.position(self.h.now()), "home")

        # ② 提交恰好一次：端口 1 次、gateway 1 次、账本 attempts=1、travel 恰好 1 条。
        self.assertEqual(port.submissions, 1)
        self.assertEqual(len(self.submissions_at_gateway), 1)
        view = self.h.service.resolver.reconcile(execution_id)
        self.assertTrue(view["known"], view)
        self.assertEqual(view["verdict"], "SUCCESS", view)
        self.assertEqual(view["state"], "committed", view)
        self.assertEqual(view["attempts"], 1, view)

        # ③ 同 execution 的**只读对账**把这一发认领成 started（不是到达），身份全部来自 owner
        #    记录。对账发生在"提交已落账"之后，所以这一步不依赖提交与客户端的先后。
        claimed = self.h.advance(port=port, now=self.h.now())
        self.assertEqual(claimed["status"], "in_transit", claimed)
        self.assertEqual(claimed["segment_status"], "started", claimed)
        self.assertEqual(claimed["destination_location_id"], "street_front", claimed)
        self.assertEqual(port.submissions, 1)
        self.assertEqual(len(self.submissions_at_gateway), 1)
        active = self.h.active()
        assert active is not None
        segment = active["segments"][0]
        self.assertEqual(segment["execution_id"], execution_id)
        self.assertEqual(segment["status"], "started")
        self.assertEqual(active["status"], "in_transit")
        self.assertEqual(segment["travel_id"], travel["travel_id"])
        self.assertEqual(segment["started_at"], travel["started_at"])
        self.assertEqual(segment["expected_arrival_at"], travel["expected_arrival_at"])
        # 出发 ≠ 到达：认领不等于到达。
        self.assertEqual(self.h.position(self.h.now()), "home")
        self.assertEqual(len(self.h.travels_of(execution_id)), 1)

        # 之后再推进只对账，绝不重复提交，也不换 execution id。
        due = _instant(travel["expected_arrival_at"])
        for offset in (1, 2):
            again = self.h.advance(port=port, now=due - timedelta(seconds=offset))
            self.assertEqual(again["status"], "in_transit", again)
            self.assertEqual(port.submissions, 1)
            self.assertEqual(len(self.submissions_at_gateway), 1)
            self.assertEqual(len(self.h.travels_of(execution_id)), 1)
            stored = self.h.active()
            assert stored is not None
            self.assertEqual(stored["segments"][0]["execution_id"], execution_id)
        self.assertEqual(self.h.position(due - timedelta(seconds=1)), "home")

        # 到期后仍然只有这一条 travel，且真的到达。
        settled = self.h.advance(port=port, now=due + timedelta(seconds=1))
        self.assertEqual(settled["segments_confirmed"], 1, settled)
        self.assertEqual(self.h.travels_of(execution_id)[0]["status"], "ARRIVED")
        self.assertEqual(self.h.raw_position(), "street_front")


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)

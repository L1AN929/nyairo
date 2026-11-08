#!/usr/bin/env python3
"""World 到达已写盘、旅程却报失败：正常到达接缝的边界用例。

idle2 真实批（``run:fastday-idle2-20261106``）的同一时刻留下三份证据：

* ``world_travel_delivery.json`` 里 ``travel-3dbcc4e...`` 已是 ``ARRIVED``，
  ``arrived_at`` 为 ``00:18:34Z``；
* ``world_state.json`` 的位置已在 ``street_1``；
* ``world_journey.json`` 的活动旅程却写成 ``blocked`` /
  ``ARRIVAL_REALIZATION_FAILED``，同一时刻。

引擎自己的「故障窗口③」写明：到达结算前若同 ``execution_id`` 的 travel 记录已是
``ARRIVED`` 且位置在段终点，只能**补写账本**，不得再移动、也不得记成失败。现行
``_reconcile`` 只在 realizer 返回 ``arrived`` 时重读证据；若到达已由 World owner
的另一条结算路径（自己的 tick、观察缝里的 lazy realize、或并发的另一个推进器）先
一步落盘，本进程这次 ``realize_due_travel`` 会看到 ``active=None`` 而返回
``none``/``failed``，于是直接走 ``else`` 写终态 ``blocked``，与落盘证据矛盾。

本模块用真实 world 包、真实 **World 服务（真 ``gateway.sock`` + 真 registry）**、
真实旅程引擎和真实 travel realizer 建立一个新 MOVE：每一段都是新的 Action，经正式
``intent gate -> fresh read -> 一次性授权 -> gateway 消费 -> Resolver`` 提交；到达
引擎写的是真实 World travel 记录。随后再用 deterministic 的 interleaving 复现
「owner 已落盘、旅程这一次读不到在途记录」的窗口，断言修后只补写账本、不伪造成功、
不绕过逐段提交。票证证据取自盘上的 ``run/action_authorizations.json``（``CONSUMED``）
与 ``run/action_lifecycle.jsonl``，不是端口自己的计数器。

时钟分域：一次性许可的签发/消费/过期是**真实墙钟**（gateway 侧），旅程和 travel
的 ``now`` 也由真实 travel 记录声明；测试只读取该记录的 ``expected_arrival_at`` 来
选取结算时刻，不把逻辑测试钟塞进生产授权链，也不为时钟错位改生产或旧测试。

AF_INET/AF_INET6/DNS/出站连接硬拒，AF_UNIX 保留（gateway 通讯真实）。用例数据全部
保留在 ``mkdtemp`` 目录里，不清理。

Run:  python -B -m unittest tests.test_world_arrival_realization_boundary
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "components" / "world" / "service" / "bridge"),
    str(ROOT / "components" / "supply"),
    str(ROOT / "scripts"),
):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from components.worldgen import schema
from components.worldgen.instance import init_instance
from components.worldgen.loader import load as load_package
from components.worldgen.tests._setup import make_valid_docs

posix_only = unittest.skipIf(
    os.name == "nt", "持久旅程需要 POSIX 文件锁与 AF_UNIX（在 WSL/Linux 上运行）"
)

#: 本模块新建的 scratch 全部保留，便于事后看盘上证据。
_RETAINED: list[Path] = []

#: 正式 bridge 目录：一次性授权与 gateway 客户端都从这里装配。
_BRIDGE_DIR = ROOT / "components" / "world" / "service" / "bridge"


def _mkdtemp(prefix: str) -> Path:
    path = Path(tempfile.mkdtemp(prefix=prefix))
    _RETAINED.append(path)
    return path


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _parse_ts(text: str) -> datetime:
    return datetime.fromisoformat(str(text).replace("Z", "+00:00"))


def _clean_env() -> dict:
    env = dict(os.environ)
    for key in (
        "CHIYO_WORLD_PACKAGE",
        "CHIYO_WORLD_HOME",
        "CHIYO_WORLD_ID",
        "XINXI_DIR",
    ):
        env.pop(key, None)
    return env


class _WorldObservation:
    """观察缝替身：只读同一份 ``world_state.json``，位置权威仍是真实 owner。"""

    def __init__(self, env: dict) -> None:
        self.env = dict(env)
        self.last_observation = None
        self.last_catalog = None

    def __call__(self):
        import world_foundation as wf
        import world_living_skeleton as wls

        home = Path(self.env["CHIYO_WORLD_HOME"])
        state = wf.WorldStateStore(
            home / "data" / "world" / "world_state.json"
        ).load() or {}
        state = dict(state)
        state.setdefault("world_revision", state.get("revision"))
        self.last_observation = state
        self.last_catalog = {
            item["location_id"]: item
            for item in wls.location_catalog(self.env)
        }


class _LifeSettleHarness:
    """把真实的 ``AutonomousLifeCycle.settle_world_journey`` 绑到最小宿主上。

    只提供该函数实际读取的字段；结算本身（``advance_journey`` + World 观察 +
    completed 判定）用的仍是正式 Life 方法本体，不复制其逻辑。这里的 ``submit_port``
    是正式 gateway 端口，下一段仍然逐段走一次性授权。

    注意：这不是完整的 LR5 终态链——只借用 ``settle_world_journey`` 这一个正式方法，
    Life 模型、Activity 采用与终态归档都不在本夹具内。
    """

    def __init__(self, home: Path, env: dict, port, scratch: Path, now: datetime):
        self.world_home = str(home)
        self.env = dict(env)
        self.runtime = SimpleNamespace(
            world_action_adapter=SimpleNamespace(
                resolver=SimpleNamespace(submit_port=port)
            )
        )
        self.world_opportunity_provider = _WorldObservation(env)
        self.pause_state_root = Path(scratch) / "life-pause"
        self.last_world_error = None
        self.last_journey_settlement = None
        self.last_world_observation = None
        self.last_world_catalog = None
        self.last_error = None
        self._now = now

    def pause_gate(self):
        import life_pause

        return life_pause.gate(self.pause_state_root)

    def _now_iso(self) -> str:
        return _iso(self._now)

    def _observe_position(self):
        import autonomous_life_cycle as alc

        return alc.AutonomousLifeCycle._observe_position(self)

    def _foreground_activity_ref(self):
        return None

    def settle(self, when: datetime, *, settle_only: bool = True) -> dict:
        import autonomous_life_cycle as alc

        return alc.AutonomousLifeCycle.settle_world_journey(
            self,
            observed_at=_iso(when),
            settle_only=settle_only,
        )


@posix_only
class ArrivalRealizationBoundaryCase(unittest.TestCase):
    """正常到达接缝：盘上已到达 = 补写账本；没有确证 = 不冒成功。"""

    @classmethod
    def setUpClass(cls) -> None:
        # 复用既有离线守卫：AF_INET/AF_INET6/DNS/出站连接硬拒，AF_UNIX 保留。
        import g1_real_adoption as guard_mod

        cls._guard = guard_mod._offline_network_guard(
            _mkdtemp("arrival-boundary-guard-")
        )
        cls._guard.__enter__()
        cls._guard_selfcheck = guard_mod._offline_guard_selfcheck()
        wanted = ("inet_refused", "inet6_refused", "dns_refused",
                  "connect_refused", "unix_ok")
        if not all(cls._guard_selfcheck.get(name) for name in wanted):
            cls._guard.__exit__(None, None, None)
            raise AssertionError("offline network guard self-check failed: %r"
                                 % (cls._guard_selfcheck,))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._guard.__exit__(None, None, None)

    def setUp(self) -> None:
        import world_runtime_data as wrd

        wrd.reset_cache()
        self._gate_previous = os.environ.get("WORLD_BODY_INTENT_MUTATION_GATE")
        # Intent Executor 从进程环境读 mutation gate；正式端口不自己造门。
        os.environ["WORLD_BODY_INTENT_MUTATION_GATE"] = "canonical"

    def tearDown(self) -> None:
        import world_runtime_data as wrd

        wrd.reset_cache()
        if self._gate_previous is None:
            os.environ.pop("WORLD_BODY_INTENT_MUTATION_GATE", None)
        else:
            os.environ["WORLD_BODY_INTENT_MUTATION_GATE"] = self._gate_previous

    # -- 夹具 ---------------------------------------------------------------

    def _formal_port(self, env: dict):
        """正式提交口：只组装，不铸造授权、不写世界状态。"""

        from chiyo_bundle import journey_authorization

        return journey_authorization.default_submit_port(environment=env)

    def _start_service(self, home: Path, env: dict, world_id: str):
        """起一个真实 World 服务线程：真 ``gateway.sock`` + 真 registry。

        复用 ``scripts/journey_authorization_smoke.py`` 的同一段装配，不再新增一套
        gateway/registry 夹具。服务只监听 ``run/`` 下的 AF_UNIX socket。
        """

        import journey_authorization_smoke as smoke

        service = smoke._WorldService(home, env, world_id)
        self.addCleanup(self._stop_service, service)
        return service

    @staticmethod
    def _stop_service(service) -> None:
        try:
            service.stop()
        except Exception:
            pass

    def _world(self, prefix: str = "arrival-boundary-") -> SimpleNamespace:
        import world_runtime_data as wrd

        root = _mkdtemp(prefix)
        package_dir = root / "package"
        package_dir.mkdir()
        schema.write_package(package_dir, make_valid_docs(seed=929711,
                                                          theme="coastal_town"))
        home = root / "world-home"
        init_instance(package_dir, home)
        wrd.reset_cache()
        package = load_package(package_dir)
        env = _clean_env()
        env.update(
            {
                "CHIYO_WORLD_PACKAGE": str(package_dir),
                "CHIYO_WORLD_HOME": str(home),
                "CHIYO_WORLD_ID": package.world_id,
                "CHIYO_WORLD_LIVING_SKELETON_ENABLED": "1",
                "CHIYO_WORLD_TRAVEL_TIME_ENABLED": "1",
                "CHIYO_WORLD_DAILY_RHYTHM_ENABLED": "0",
                "CHIYO_WORLD_AMBIENT_EVENTS_ENABLED": "0",
                "CHIYO_WORLD_SOCIAL_PRESENCE_ENABLED": "0",
                "NYAIRO_WORLD_BRIDGE_DIR": str(_BRIDGE_DIR),
                "WORLD_BODY_INTEGRATION_MODE": "canonical",
                "WORLD_BODY_INTENT_MUTATION_GATE": "canonical",
            }
        )
        wrd.reset_cache()
        service = self._start_service(home, env, package.world_id)
        env = dict(env)
        env["WORLD_BODY_SOCKET"] = str(service.socket_path)
        port = self._formal_port(env)
        wrd.reset_cache()
        return SimpleNamespace(root=root, package_dir=package_dir, home=home,
                               env=env, port=port, service=service,
                               world_id=package.world_id)

    def _journey(self):
        import chiyo_bundle.world_journey as journey

        return journey

    def _wtd(self):
        import world_travel_delivery as wtd

        return wtd

    def _accept(self, world: SimpleNamespace, target: str,
                now: datetime | None = None) -> dict:
        return self._journey().accept_journey(
            target=target,
            hermes_home=world.home,
            environment=world.env,
            now=now,
            submit_port=world.port,
        )

    def _harness(self, world: SimpleNamespace, now: datetime) -> _LifeSettleHarness:
        return _LifeSettleHarness(world.home, world.env, world.port,
                                  world.root, now)

    def _state_dir(self, world: SimpleNamespace) -> Path:
        return Path(world.home) / "data" / "world"

    def _run_dir(self, world: SimpleNamespace) -> Path:
        return Path(world.home) / "run"

    def _journey_state(self, world: SimpleNamespace) -> dict:
        return json.loads(
            (self._state_dir(world) / "world_journey.json").read_text(
                encoding="utf-8"
            )
        )

    def _travels(self, world: SimpleNamespace) -> list:
        path = self._state_dir(world) / "world_travel_delivery.json"
        if not path.exists():
            return []
        return list(json.loads(path.read_text(encoding="utf-8"))["travels"])

    def _position(self, world: SimpleNamespace) -> str:
        state = json.loads(
            (self._state_dir(world) / "world_state.json").read_text(
                encoding="utf-8"
            )
        )
        return str(state["location"]["place_id"])

    def _world_revision(self, world: SimpleNamespace) -> int:
        state = json.loads(
            (self._state_dir(world) / "world_state.json").read_text(
                encoding="utf-8"
            )
        )
        return int(state["revision"])

    def _auth_records(self, world: SimpleNamespace) -> list:
        """盘上真实的一次性授权记录（gateway 消费后为 ``CONSUMED``）。"""

        path = self._run_dir(world) / "action_authorizations.json"
        if not path.exists():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        return list(payload.get("records") or [])

    def _lifecycle_bytes(self, world: SimpleNamespace) -> bytes:
        """Intent Executor 的持久生命周期回执（每个 execution 多阶段追加）。"""

        path = self._run_dir(world) / "action_lifecycle.jsonl"
        return path.read_bytes() if path.exists() else b""

    def _due(self, world: SimpleNamespace, index: int = 0) -> datetime:
        """结算时刻取自真实 travel 记录声明的到达时间，而不是逻辑测试钟。"""

        record = self._travels(world)[index]
        return _parse_ts(record["expected_arrival_at"]) + timedelta(seconds=1)

    def _assert_formal_segment(self, world: SimpleNamespace, index: int) -> dict:
        """该段确实经正式链提交：新 execution、新票、gateway 已消费。"""

        stored = self._journey_state(world)["active"]
        segment = stored["segments"][index]
        records = self._auth_records(world)
        self.assertGreaterEqual(len(records), index + 1, records)
        record = records[index]
        self.assertEqual(record["state"], "CONSUMED", record)
        self.assertEqual(record["verb"], "MOVE", record)
        self.assertEqual(record["execution_id"], segment["execution_id"], record)
        self.assertTrue(record.get("consumed_at"), record)
        intent = world.port.last_intent or {}
        self.assertEqual(intent.get("execution_id"), segment["execution_id"])
        self.assertEqual((intent.get("params") or {}).get("destination_location_id"),
                         segment["destination_location_id"])
        self.assertEqual(world.port.last_result["intent_status"], "APPLIED",
                         world.port.last_result)
        return segment

    def _settle_with_concurrent_owner(self, world: SimpleNamespace,
                                      when: datetime,
                                      *, settle_only: bool = True):
        """复现真实竞态：旅程读在途快照后，World owner 的另一条结算路径先落盘。

        生产里这件事由 World 服务的 tick / 观察缝的 lazy realize 完成；这里在
        ``_travel_index`` 的第一次读之后同步调用**真实** realizer 落盘，再把
        刚才读到的「仍在途」快照交回旅程。于是旅程随后自己的
        ``realize_due_travel`` 会真的看到 ``active=None``，与生产窗口同形。
        """

        journey = self._journey()
        wtd = self._wtd()
        real_index = journey._travel_index
        fired = {"owner_realized": False}

        def racing_index(home, env, module):
            snapshot = real_index(home, env, module)
            if not fired["owner_realized"]:
                fired["owner_realized"] = True
                wtd.realize_due_travel(hermes_home=home, environment=env, now=when)
            return snapshot

        harness = self._harness(world, when)
        with mock.patch.object(journey, "_travel_index", new=racing_index):
            result = harness.settle(when, settle_only=settle_only)
        return result, fired

    # -- 1. 正常单段到达 -----------------------------------------------------

    def test_normal_single_segment_arrival_completes(self) -> None:
        world = self._world("arrival-normal-")
        accepted = self._accept(world, "street_1")
        self.assertEqual(accepted["status"], "started", accepted)
        self.assertEqual(world.port.submissions, 1)
        self._assert_formal_segment(world, 0)

        due = self._due(world)
        result = self._harness(world, due).settle(due, settle_only=True)

        self.assertEqual(result["journey_status"], "completed", result)
        self.assertTrue(result["arrived"], result)
        self.assertEqual(result["observed_location_id"], "street_1", result)
        stored = self._journey_state(world)["active"]
        self.assertEqual(stored["status"], "completed", stored)
        self.assertEqual(stored["segments"][0]["status"], "confirmed", stored)
        travels = self._travels(world)
        self.assertEqual(len(travels), 1, travels)
        self.assertEqual(travels[0]["status"], "ARRIVED", travels)
        self.assertEqual(travels[0]["execution_id"],
                         stored["segments"][0]["execution_id"])
        self.assertEqual(self._position(world), "street_1")
        # 到达结算是补账，不得再铸第二张票。
        self.assertEqual(len(self._auth_records(world)), 1,
                         self._auth_records(world))
        self.assertEqual(world.port.submissions, 1)

    # -- 2. owner 已写到达、旅程这一次读不到在途记录（本单修的接缝）---------

    def test_owner_already_wrote_arrival_is_booked_not_blocked(self) -> None:
        world = self._world("arrival-race-")
        accepted = self._accept(world, "street_1")
        self.assertEqual(accepted["status"], "started", accepted)
        self._assert_formal_segment(world, 0)

        due = self._due(world)
        result, fired = self._settle_with_concurrent_owner(world, due)

        # World owner 的那次结算真的落盘了（修前修后都一样）。
        self.assertTrue(fired["owner_realized"], fired)
        travels = self._travels(world)
        self.assertEqual(len(travels), 1, travels)
        self.assertEqual(travels[0]["status"], "ARRIVED", travels)
        self.assertEqual(self._position(world), "street_1")

        # 修后：同一段只补写账本，不再记成终态 blocked。
        self.assertEqual(result["journey_status"], "completed", result)
        self.assertTrue(result["arrived"], result)
        self.assertEqual(result["observed_location_id"], "street_1", result)
        stored = self._journey_state(world)["active"]
        self.assertEqual(stored["status"], "completed", stored)
        self.assertEqual(stored["segments"][0]["status"], "confirmed", stored)
        self.assertEqual(stored["segments"][0]["travel_id"],
                         travels[0]["travel_id"], stored)
        self.assertEqual(len(self._travels(world)), 1,
                         "补写账本不得再铸一张 travel")
        self.assertEqual(len(self._auth_records(world)), 1,
                         "补写账本不得再消费一张票")
        self.assertEqual(world.port.submissions, 1)

    # -- 3. 同一确认重复维护：不加位移/票/receipt ---------------------------

    def test_repeat_settle_is_idempotent(self) -> None:
        world = self._world("arrival-idem-")
        self._accept(world, "street_1")
        due = self._due(world)
        first = self._harness(world, due).settle(due, settle_only=True)
        self.assertEqual(first["journey_status"], "completed", first)

        state_dir = self._state_dir(world)
        before = {
            name: (state_dir / name).read_bytes()
            for name in ("world_state.json", "world_travel_delivery.json",
                         "world_journey.json")
        }
        revision = self._world_revision(world)
        travel_count = len(self._travels(world))
        auth_before = self._auth_records(world)
        lifecycle_before = self._lifecycle_bytes(world)
        submissions = world.port.submissions

        later = due + timedelta(seconds=30)
        again = self._harness(world, later).settle(later, settle_only=True)

        self.assertEqual(again["journey_status"], "completed", again)
        self.assertTrue(again["arrived"], again)
        self.assertEqual(self._world_revision(world), revision)
        self.assertEqual(len(self._travels(world)), travel_count)
        for name, payload in before.items():
            self.assertEqual((state_dir / name).read_bytes(), payload,
                             "%s changed on a repeated confirmation" % name)
        self.assertEqual(self._auth_records(world), auth_before,
                         "重复确认不得新增/改写授权票")
        self.assertEqual(self._lifecycle_bytes(world), lifecycle_before,
                         "重复确认不得新增生命周期回执")
        self.assertEqual(world.port.submissions, submissions)

    # -- 4. 没有到达确证时绝不冒成功 ---------------------------------------

    def test_failure_without_confirming_evidence_never_fakes_arrival(self) -> None:
        # 4a. 真实落盘前拒绝：realizer 直接失败，什么都没写。
        world = self._world("arrival-prewrite-")
        self._accept(world, "street_1")
        self._assert_formal_segment(world, 0)
        due = self._due(world)

        def pre_write_failure(**_kwargs):
            return {
                "kind": "world_travel_projection",
                "status": "failed",
                "reason_code": "ARRIVAL_REALIZATION_FAILED",
                "error_type": "ControlledPreWrite",
            }

        with mock.patch.object(self._wtd(), "realize_due_travel",
                               side_effect=pre_write_failure):
            blocked = self._harness(world, due).settle(due, settle_only=True)

        self.assertEqual(blocked["journey_status"], "blocked", blocked)
        self.assertFalse(blocked["arrived"], blocked)
        self.assertEqual(blocked["reason_code"], "ARRIVAL_REALIZATION_FAILED")
        self.assertEqual(self._position(world), "home")
        self.assertEqual(self._travels(world)[0]["status"], "IN_PROGRESS")

        # 4b. 受控落盘后错误：位置被写了，但同 execution_id 的 travel 还没有
        #     ARRIVED。位置相同不能单独当作到达确证，仍不得伪成功。
        world = self._world("arrival-partial-")
        self._accept(world, "street_1")
        self._assert_formal_segment(world, 0)
        due = self._due(world)

        def post_write_without_arrival(**_kwargs):
            self._force_position(world, "street_1")
            return {
                "kind": "world_travel_projection",
                "status": "failed",
                "reason_code": "ARRIVAL_REALIZATION_FAILED",
                "error_type": "ControlledPostWrite",
            }

        with mock.patch.object(self._wtd(), "realize_due_travel",
                               side_effect=post_write_without_arrival):
            blocked = self._harness(world, due).settle(due, settle_only=True)

        self.assertEqual(self._position(world), "street_1")
        self.assertEqual(self._travels(world)[0]["status"], "IN_PROGRESS")
        self.assertEqual(blocked["journey_status"], "blocked", blocked)
        self.assertFalse(blocked["arrived"], blocked)
        self.assertEqual(self._journey_state(world)["active"]["status"], "blocked")

    def _force_position(self, world: SimpleNamespace, node: str) -> None:
        """外部写入者（WorldStateStore 的规范写入）把位置改到 node。"""

        import world_foundation as wf
        import world_runtime_data as wrd
        import world_travel_delivery as wtd

        active = wrd.active_world(world.env)
        path = wtd.world_state_path(world.home)
        store = wf.WorldStateStore(path)
        state = store.load()
        updated = json.loads(json.dumps(state))
        place = active.node_place(node)
        area = active.area_id_of(node)
        updated["revision"] = state["revision"] + 1
        updated["location"] = {"place_id": place, "area_id": area}
        updated["scene"] = {
            "scene_id": f"{place}.{area}",
            "revision": state["scene"]["revision"] + 1,
        }
        store.save(updated, expected_revision=updated["revision"])

    # -- 5. 多段旅程：补写上一段不得绕过下一段的逐一提交 --------------------

    def test_multi_segment_arrival_still_submits_the_next_segment(self) -> None:
        world = self._world("arrival-multi-")
        accepted = self._accept(world, "street_2")
        self.assertEqual(accepted["status"], "started", accepted)
        self.assertEqual(list(accepted["route"]), ["street_1", "street_2"],
                         accepted)
        self.assertEqual(world.port.submissions, 1)
        self._assert_formal_segment(world, 0)

        first_due = self._due(world, 0)
        result, fired = self._settle_with_concurrent_owner(
            world, first_due, settle_only=False
        )

        self.assertTrue(fired["owner_realized"], fired)
        # 第一段补写账本后，第二段仍是新的 Action：必须再提交一次。
        self.assertEqual(world.port.submissions, 2, result)
        self.assertIn(result["journey_status"], {"started", "in_transit"}, result)
        stored = self._journey_state(world)["active"]
        self.assertEqual(stored["segments"][0]["status"], "confirmed", stored)
        self.assertEqual(stored["segments"][1]["status"], "started", stored)
        self.assertEqual(self._position(world), "street_1",
                         "第二段不得瞬移，位置仍在中转点")
        travels = self._travels(world)
        self.assertEqual(len(travels), 2, travels)
        self.assertEqual(
            [item["status"] for item in travels], ["ARRIVED", "IN_PROGRESS"], travels
        )
        self.assertNotEqual(travels[0]["execution_id"], travels[1]["execution_id"])
        # 第二段是新的 execution、新的票，且 gateway 已经消费过。
        self._assert_formal_segment(world, 1)
        records = self._auth_records(world)
        self.assertEqual(len(records), 2, records)
        self.assertEqual([item["state"] for item in records],
                         ["CONSUMED", "CONSUMED"], records)
        self.assertNotEqual(records[0]["execution_id"], records[1]["execution_id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

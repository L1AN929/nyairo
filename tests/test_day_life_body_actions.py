#!/usr/bin/env python3
"""W: 主体姿态与真实休息 —— 编码 / 机会 / 桥接 / 真实 owner 链的精准用例。

Run（POSIX / WSL，与既有 World 用例一致）:  python3 tests/test_day_life_body_actions.py
Run（原生 Windows，只跑不依赖 World 的编码契约）:  python tests/test_day_life_body_actions.py

覆盖（14 条，精确，不 discover 继承旧套）：

1. ``bodyact:pose:<pose>:<orientation>`` / ``bodyact:rest:<start|stop>:rest`` 的往返与完整字段；
2. 不完整 / 多字段 / 未知动作 / 越界枚举一律拒绝，``sleep_like`` 本轮不可执行（窄准入）；
3. AG-2 step：单 target payload（不新增 ``params``），key 绑定全部参数 + 前置 revision；
4. 观测事实：POSE 看 owner 确认姿态，REST 只看 recovery **会话**状态（命令成功 ≠ 身体恢复）；
5. POSE 机会严格来自 ``get_available_actions`` 的 pose 枚举 + snapshot 朝向，排除当前姿态；
6. REST 机会只出 owner 说 ``start_available`` / ``stop_available`` 的方向，永不出 ``sleep_like``；
7. 视图识别 ``bodyact``：报告 verb / 解码后的 params / owner 自己的 blocked reason；
8. AR-0 窄准入 REST（POSE 原本有）、bridge 解码、journey 委托，MOVE/CONSUME 旧行为不变；
9. 真实 World service + IntentExecutor + gateway + Resolver：POSE 真链提交一次，重复同姿态不再提交；
10. 真实链上 REST 通过 intent allowlist 与参数契约、铸票、提交，停在 **owner 自己的可用性**
    （``NOT_EXPOSED``，B6E1 前的 intent 层 ``INVALID`` 已不存在）；stop 无会话另有 ``NO_OPEN_RECOVERY``；
11. 真实 owner 自己拒 REST：Body preflight ``RECOVERY_NOT_NEEDED``，gateway 曝光面 ``NOT_EXPOSED``；
12. 非法 bodyact target 在 bridge 就被拒，授权没签发、owner 没被联系；
13. intent 参数契约窄：``sleep_like`` / 缺字段 / 多字段 / 越界 state 都在 ``build_submission`` 拒；
14. ``sleep_like`` 在真实链上到不了 owner：没有 lifecycle 记录、基底与身体都没动。

不做：改 owner / 阈值 / 时钟、造恢复、删夹具（socket 与失败全留）、跑旧整套、24h 常驻。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge"),
              str(ROOT / "components" / "life" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

#: 只依赖 typing 的编码模块：原生 Windows 也能导入，所以编码契约在两边都能跑。
import life_body_action_targets as body_actions  # noqa: E402
import life_daily_action_targets as daily_actions  # noqa: E402

#: 其余 Life 模块经 ``candidate_sources_ag0`` -> ``activity_continuity`` -> ``fcntl``，
#: 原生 Windows 不可导入（与既有 Life 用例同一限制）。导入失败只跳过，不静默改行为。
_LIFE_IMPORT_ERROR: str | None = None
try:
    import action_reality_ledger as ar0  # noqa: E402
    import world_action_bridge_adapter as wba  # noqa: E402
    import world_journey_action_adapter as wja  # noqa: E402
    import world_move_opportunity_source as wmos  # noqa: E402
except Exception as error:  # noqa: BLE001 - 环境限制，不是产品行为
    _LIFE_IMPORT_ERROR = "%s: %s" % (type(error).__name__, error)

#: 真实 World service 需要 ``fcntl``/``AF_UNIX`` 与 ``os.geteuid``。
_WORLD_IMPORT_ERROR: str | None = None
try:
    from components.worldgen.instance import init_instance  # noqa: E402

    import action_intent as ai  # noqa: E402
    import world_action_resolver as war  # noqa: E402
    import world_body_config as wcfg  # noqa: E402
    import world_body_service as wbsvc  # noqa: E402
    import world_body_substrate as wbs  # noqa: E402
    import world_runtime_data as wrd  # noqa: E402
except Exception as error:  # noqa: BLE001 - 环境限制，不是产品行为
    _WORLD_IMPORT_ERROR = "%s: %s" % (type(error).__name__, error)

needs_life = unittest.skipIf(
    _LIFE_IMPORT_ERROR is not None,
    "Life 模块需要 POSIX（fcntl）：%s" % _LIFE_IMPORT_ERROR)
posix_only = unittest.skipIf(
    os.name == "nt" or _WORLD_IMPORT_ERROR is not None,
    "真实 World service 需要 POSIX（fcntl/AF_UNIX/os.geteuid）：%s"
    % (_WORLD_IMPORT_ERROR or "nt"))

#: 所有夹具的唯一根：建一次，永不删除（失败也留证）。
FIXTURE_ROOT = Path(tempfile.mkdtemp(prefix="nyaw04-bodyact-"))
WORLD_PACKAGE = ROOT / "examples" / "worlds" / "daily_neighborhood_v1"
BRIDGE_DIR = ROOT / "components" / "world" / "service" / "bridge"

PLACE_HOME = "home"


def _owner_availability(*, pose=None, pose_available=True, rest=None):
    """A gateway ``get_available_actions`` payload with only what these tests need."""

    actions: list[dict] = []
    if pose is not None:
        actions.append({
            "verb": "POSE",
            "available": pose_available,
            "params": {"pose": list(pose), "orientation": list(body_actions.ORIENTATIONS)},
        })
    if rest is not None:
        entry = {
            "verb": "REST",
            "available": bool(rest.get("start_available") or rest.get("stop_available")),
            "params": {"state": ["start", "stop"], "mode": ["rest", "sleep_like"]},
            "start_available": bool(rest.get("start_available")),
            "stop_available": bool(rest.get("stop_available")),
            "sleep_like_available": bool(rest.get("sleep_like_available")),
            "start_blocked_reason": rest.get("start_blocked_reason"),
            "stop_blocked_reason": rest.get("stop_blocked_reason"),
            "current": {"recovery_state": rest.get("recovery_state", "inactive")},
        }
        actions.append(entry)
    return {"kind": "world.body.gateway.api", "schema_version": "1", "actions": actions}


def _observation(*, pose="standing", orientation="south", revision=7):
    return {
        "world_id": "bodyact_fixture_world",
        "world_revision": revision,
        "location": {"place_id": PLACE_HOME, "area_id": PLACE_HOME},
        "pose": pose,
        "orientation": orientation,
    }


MOMENT = "2026-10-26T09:00:00+00:00"


# ---------------------------------------------------------------------------
# 1-2. 编码契约（不依赖 World/Body）
# ---------------------------------------------------------------------------


class BodyActionEncodingTests(unittest.TestCase):
    """一个 target 装下 owner 的两个参数，且只装它认得的那些。"""

    def test_pose_and_rest_round_trip_with_every_parameter(self) -> None:
        pose = body_actions.parse_body_action_target("bodyact:pose:seated:north")
        self.assertEqual(pose, {
            "verb": "POSE",
            "target": "bodyact:pose:seated:north",
            "params": {"pose": "seated", "orientation": "north"},
            "fact_key": "world_body_pose_ref",
            "expected_value": "world_fact:body:pose:seated:north",
        })
        started = body_actions.parse_body_action_target("bodyact:rest:start:rest")
        self.assertEqual(started, {
            "verb": "REST",
            "target": "bodyact:rest:start:rest",
            "params": {"state": "start", "mode": "rest"},
            "fact_key": "world_body_recovery_ref",
            "expected_value": "world_fact:body:recovery:active",
        })
        stopped = body_actions.parse_body_action_target("bodyact:rest:stop:rest")
        self.assertEqual(stopped["params"], {"state": "stop", "mode": "rest"})
        self.assertEqual(stopped["expected_value"], "world_fact:body:recovery:inactive")
        # 编码是唯一的：parse 的 target 就是原串，重新编码得到同一串。
        self.assertEqual(
            body_actions.encode_pose_target("seated", "north"), pose["target"])
        self.assertEqual(
            body_actions.encode_rest_target("start", "rest"), started["target"])

    def test_incomplete_extra_unknown_and_sleep_like_are_refused(self) -> None:
        for bad in ("bodyact:pose:seated",                       # 不完整
                    "bodyact:pose:seated:north:extra",            # 多字段
                    "bodyact:sit:seated:north",                   # 未知动作
                    "bodyact:pose:sitting:north",                 # 越界 pose
                    "bodyact:pose:seated:sideways",               # 越界朝向
                    "bodyact:rest:begin:rest",                    # 越界 state
                    "bodyact:rest:start:sleep_like",              # 本轮不准入
                    "bodyact:rest:stop:sleep_like"):              # 本轮不准入
            with self.subTest(ref=bad):
                with self.assertRaises(body_actions.BodyActionTargetError):
                    body_actions.parse_body_action_target(bad)
        # 别的命名空间不是本模块的事：返回 None，而不是拒绝别人的 target。
        for foreign in ("place:home", "object:item-1", "stay:home:SIT:300", "", None):
            self.assertIsNone(body_actions.parse_body_action_target(foreign))
        # 造不出 sleep_like 的编码，就不存在手写绕过的入口。
        self.assertEqual(body_actions.REST_MODES, ("rest",))
        self.assertIn("sleep_like", body_actions.OWNER_REST_MODES)
        with self.assertRaises(body_actions.BodyActionTargetError):
            body_actions.encode_rest_target("start", "sleep_like")


# ---------------------------------------------------------------------------
# 3-4. AG-2 step 与观测事实
# ---------------------------------------------------------------------------


@needs_life
class BodyActionStepTests(unittest.TestCase):
    """step 仍是 AG-2 的世界动作，AR-0 的 payload 仍只有一个 target。"""

    def test_step_spec_keeps_the_single_target_payload_and_binds_every_parameter(self) -> None:
        step = body_actions.build_body_action_step(
            "bodyact:pose:seated:north", place=PLACE_HOME, revision=7)
        self.assertEqual(step["step_kind"], "WORLD_ACTION")
        self.assertEqual(step["action_kind"], "WORLD_ACTION")
        self.assertEqual(step["target_ref"], "bodyact:pose:seated:north")
        # 单 target 契约：没有 params 键，参数全在 target 里。
        self.assertEqual(set(step["payload_spec"]),
                         {"verb", "target", "expected_dependencies"})
        self.assertEqual(step["payload_spec"]["verb"], "POSE")
        self.assertEqual(step["payload_spec"]["target"], "bodyact:pose:seated:north")
        self.assertEqual(step["payload_spec"]["expected_dependencies"], {})
        self.assertEqual(step["expected_effect_predicates"], ["body_pose_changed"])
        self.assertIn(":home:r7:", step["idempotency_key"])
        self.assertIn("bodyact:pose:seated:north", step["idempotency_key"])

        rest_start = body_actions.build_body_action_step(
            "bodyact:rest:start:rest", place=PLACE_HOME, revision=7)
        rest_stop = body_actions.build_body_action_step(
            "bodyact:rest:stop:rest", place=PLACE_HOME, revision=7)
        self.assertEqual(rest_start["expected_effect_predicates"],
                         ["recovery_session_started"])
        self.assertEqual(rest_stop["expected_effect_predicates"],
                         ["recovery_session_stopped"])
        # 参数不同 / 前置 revision 不同 → 不同的 key，绝不共用一个执行。
        keys = {step["idempotency_key"], rest_start["idempotency_key"],
                rest_stop["idempotency_key"],
                body_actions.build_body_action_step(
                    "bodyact:pose:seated:north", place=PLACE_HOME, revision=8
                )["idempotency_key"]}
        self.assertEqual(len(keys), 4)
        with self.assertRaises(body_actions.BodyActionTargetError):
            body_actions.build_body_action_step("place:home", place=PLACE_HOME, revision=7)

    def test_observed_fact_reads_pose_or_recovery_session_only(self) -> None:
        pose = body_actions.parse_body_action_target("bodyact:pose:seated:north")
        same = body_actions.body_action_observed_fact(
            pose, {"pose": "seated", "orientation": "north"})
        self.assertEqual(same["fact_key"], "world_body_pose_ref")
        self.assertEqual(same["value"], "world_fact:body:pose:seated:north")
        self.assertTrue(same["satisfied"])
        self.assertEqual(same["source"], "world_gateway.get_snapshot")
        other = body_actions.body_action_observed_fact(
            pose, {"pose": "seated", "orientation": "south"})
        self.assertFalse(other["satisfied"])
        # 读不到就是读不到：快照答不出来永远不算满足。
        unknown = body_actions.body_action_observed_fact(pose, {"pose": "seated"})
        self.assertIsNone(unknown["value"])
        self.assertFalse(unknown["satisfied"])

        start = body_actions.parse_body_action_target("bodyact:rest:start:rest")
        active = body_actions.body_action_observed_fact(
            start, {}, rest_availability={"current": {"recovery_state": "active"}})
        self.assertTrue(active["satisfied"])
        # 会话没开 → 事实是 inactive：命令成功也推不出身体恢复。
        inactive = body_actions.body_action_observed_fact(
            start, {}, rest_availability={"current": {"recovery_state": "inactive"}})
        self.assertFalse(inactive["satisfied"])
        self.assertEqual(inactive["value"], "world_fact:body:recovery:inactive")
        self.assertEqual(inactive["source"], "world_gateway.get_available_actions")
        silent = body_actions.body_action_observed_fact(start, {})
        self.assertIsNone(silent["value"])
        self.assertFalse(silent["satisfied"])
        # 返回值里没有任何"恢复量/阈值/健康"字段，只有会话事实。
        self.assertEqual(set(silent), {"fact_key", "expected_value", "value",
                                       "satisfied", "source", "observed"})


# ---------------------------------------------------------------------------
# 5-7. 机会与视图
# ---------------------------------------------------------------------------


@needs_life
class BodyActionOpportunityTests(unittest.TestCase):
    """机会只来自 owner 自己的 available + canonical snapshot。"""

    def test_pose_opportunities_exclude_the_current_pose_and_need_an_orientation(self) -> None:
        available = _owner_availability(pose=("standing", "seated", "lying"))
        found = wmos.observed_body_action_opportunities(
            _observation(pose="standing", orientation="south"),
            available_actions=available, occurred_at=MOMENT, observed_at=MOMENT)
        self.assertEqual([item["target_refs"] for item in found],
                         [["bodyact:pose:seated:south"], ["bodyact:pose:lying:south"]])
        for item in found:
            self.assertEqual(item["status"], "AVAILABLE")
            self.assertTrue(item["observed"])
            self.assertEqual(item["location_ref"], "place:home")
            self.assertEqual(item["world_revision_ref"], "wrev:bodyact_fixture_world:r7")
        # 快照没有朝向 → 没有 POSE 机会（第二参数不靠猜）。
        self.assertEqual(
            wmos.observed_body_action_opportunities(
                _observation(pose="standing", orientation=None),
                available_actions=available, occurred_at=MOMENT, observed_at=MOMENT), [])
        # owner 没给 POSE 条目 / 说不可用 → 没有机会。
        self.assertEqual(
            wmos.observed_body_action_opportunities(
                _observation(), available_actions={"actions": []},
                occurred_at=MOMENT, observed_at=MOMENT), [])
        self.assertEqual(
            wmos.observed_body_action_opportunities(
                _observation(),
                available_actions=_owner_availability(pose=("seated",), pose_available=False),
                occurred_at=MOMENT, observed_at=MOMENT), [])

    def test_rest_offers_only_the_available_direction_and_never_sleep_like(self) -> None:
        started = wmos.observed_body_action_opportunities(
            _observation(),
            available_actions=_owner_availability(
                rest={"start_available": True, "stop_available": False,
                      "sleep_like_available": True,
                      "start_blocked_reason": None,
                      "stop_blocked_reason": "NO_OPEN_RECOVERY"}),
            occurred_at=MOMENT, observed_at=MOMENT)
        self.assertEqual([item["target_refs"] for item in started],
                         [["bodyact:rest:start:rest"]])
        self.assertEqual(started[0]["perception_ref"],
                         "percept:gateway-available-actions:home:r7")
        stopped = wmos.observed_body_action_opportunities(
            _observation(),
            available_actions=_owner_availability(
                rest={"start_available": False, "stop_available": True,
                      "recovery_state": "active",
                      "start_blocked_reason": "RECOVERY_ALREADY_ACTIVE"}),
            occurred_at=MOMENT, observed_at=MOMENT)
        self.assertEqual([item["target_refs"] for item in stopped],
                         [["bodyact:rest:stop:rest"]])
        # 两个方向都不可用（身体不需要恢复）→ 一条 REST 机会都不出，也不生造"她该休息"。
        self.assertEqual(
            wmos.observed_body_action_opportunities(
                _observation(),
                available_actions=_owner_availability(
                    rest={"start_available": False, "stop_available": False,
                          "start_blocked_reason": "RECOVERY_NOT_NEEDED"}),
                occurred_at=MOMENT, observed_at=MOMENT), [])

    def test_view_reports_decoded_params_and_the_owner_blocked_reason(self) -> None:
        available = _owner_availability(
            pose=("seated",),
            rest={"start_available": False, "stop_available": False,
                  "start_blocked_reason": "RECOVERY_NOT_NEEDED",
                  "stop_blocked_reason": "NO_OPEN_RECOVERY"})
        opportunities = wmos.observed_body_action_opportunities(
            _observation(), available_actions=available,
            occurred_at=MOMENT, observed_at=MOMENT)
        # owner 现在说 REST 不需要，但这条记录本身是那一刻观测到的：视图照样报告它，
        # 并把 owner **当前**的按方向理由原样带出（不升级成"可做"）。
        view = wmos.opportunity_view(
            opportunities + [{
                "opportunity_id": "wopp:rest:home:r7:start",
                "perception_ref": "percept:gateway-available-actions:home:r7",
                "world_revision_ref": "wrev:bodyact_fixture_world:r7",
                "target_refs": ["bodyact:rest:start:rest"],
                "valid_from": MOMENT, "valid_until": MOMENT, "occurred_at": MOMENT,
                "observed_at": MOMENT, "observed": True, "status": "AVAILABLE",
                "revision": 1, "subject_id": "subject:global",
                "location_ref": "place:home",
            }],
            None, available)
        pose_view = next(item for item in view if item["target_ref"] == "bodyact:pose:seated:south")
        self.assertEqual(pose_view["kind"], "POSE")
        self.assertEqual(pose_view["params"], {"pose": "seated", "orientation": "south"})
        self.assertEqual(pose_view["pose"], "seated")
        self.assertEqual(pose_view["orientation"], "south")
        self.assertEqual(pose_view["fact_key"], "world_body_pose_ref")
        self.assertFalse(pose_view["action_grant"])
        rest_view = next(item for item in view if item["target_ref"] == "bodyact:rest:start:rest")
        self.assertEqual(rest_view["kind"], "REST")
        self.assertEqual(rest_view["params"], {"state": "start", "mode": "rest"})
        self.assertEqual(rest_view["rest_state"], "start")
        self.assertEqual(rest_view["rest_mode"], "rest")
        self.assertEqual(rest_view["owner_blocked_reason"], "RECOVERY_NOT_NEEDED")
        # 坏的编码照样被识别并报原因，不会被丢掉、也不会被当成别的动词。
        broken = wmos.opportunity_view([{
            "opportunity_id": "wopp:pose:home:r7:seated",
            "target_refs": ["bodyact:pose:sitting:north"],
            "location_ref": "place:home", "observed": True,
            "occurred_at": MOMENT, "valid_from": MOMENT, "valid_until": MOMENT,
            "perception_ref": "percept:gateway-snapshot:home:r7",
            "world_revision_ref": "wrev:bodyact_fixture_world:r7",
        }], None, available)
        self.assertEqual(broken[0]["kind"], "INVALID_BODY_ACTION")
        self.assertIn("pose must be one of", broken[0]["owner_blocked_reason"])


# ---------------------------------------------------------------------------
# 8. 契约层：AR-0 准入 / bridge 解码 / journey 委托
# ---------------------------------------------------------------------------


@needs_life
class BodyActionContractTests(unittest.TestCase):
    """AR-0 只窄增 REST；bridge 解码 target；journey 把 POSE/REST 委托出去。"""

    def test_ar0_admits_rest_bridge_decodes_and_journey_delegates(self) -> None:
        adapter = ar0.WorldActionAdapter(ar0.FakeWorldResolver())
        for verb, target in (("POSE", "bodyact:pose:seated:north"),
                             ("REST", "bodyact:rest:stop:rest")):
            prepared = adapter.validate_prepare({}, {"verb": verb, "target": target})
            self.assertEqual(prepared, {"verb": verb, "target": target,
                                        "expected_dependencies": {}})
        # 单 target 旧契约没变：仍要求非空 target，仍不许别的动词借道。
        with self.assertRaises(ar0.ContractViolationError):
            adapter.validate_prepare({}, {"verb": "REST", "target": "  "})
        with self.assertRaises(ar0.ContractViolationError):
            adapter.validate_prepare({}, {"verb": "ARRANGE", "target": "bodyact:pose:seated:north"})
        # 旧的单参数动词一个字没改，也没有给任何动词开一个自由 params 入口。
        self.assertEqual(wba.resolver_params("MOVE", "cafe"),
                         {"destination_location_id": "cafe"})
        self.assertEqual(wba.resolver_params("CONSUME", "item-1"), {"object_id": "item-1"})
        self.assertEqual(set(wba.VERB_PARAMETER), {"MOVE", "CONSUME"})
        # body 动词：参数从编码里解出来，解不全就拒（不会猜一个朝向）。
        self.assertEqual(wba.resolver_params("POSE", "bodyact:pose:lying:east"),
                         {"pose": "lying", "orientation": "east"})
        self.assertEqual(wba.resolver_params("REST", "bodyact:rest:start:rest"),
                         {"state": "start", "mode": "rest"})
        for verb, target in (("POSE", "seated"), ("REST", "bodyact:rest:start"),
                             ("REST", "bodyact:rest:start:sleep_like")):
            with self.subTest(verb=verb, target=target):
                with self.assertRaises(body_actions.BodyActionTargetError):
                    wba.resolver_params(verb, target)
        # journey：MOVE 仍是它自己的计时动作；CONSUME/POSE/REST 与 v0.4 的拿放一并走同一个正式链
        # 而不是被拒（拿放的动词表由 B 的 ``life_daily_action_targets`` 拥有，这里直接引用，不复制）。
        self.assertEqual(wja.SUPPORTED_VERBS, ("MOVE",))
        self.assertEqual(set(wja.DELEGATED_VERBS),
                         {"CONSUME", "POSE", "REST",
                          *daily_actions.DAILY_ACTION_VERBS})

    def test_rest_param_contract_is_narrow_at_the_submission_boundary(self) -> None:
        """admit 一个 verb 与检查它的参数是同一步：sleep_like 在 intent 层就不可执行。"""

        snapshot = {"world_revision": 3, "location": {"place_id": "home"},
                    "pose": "standing"}

        def intent(verb: str, params: dict) -> dict:
            return {"intent_id": "world-intent:test", "execution_id": "world-exec:test",
                    "intent_status": "AUTHORIZED", "verb": verb, "params": dict(params),
                    "decision_ref": None, "source_provenance": "ar0_world_bridge_adapter"}

        for state in ("start", "stop"):
            submission = ai.build_submission(
                intent("REST", {"state": state, "mode": "rest"}),
                fresh_snapshot=snapshot)
            self.assertEqual(submission["action"], "REST")
            self.assertEqual(submission["params"], {"state": state, "mode": "rest"})

        refused = (
            ("sleep_like 不准入", {"state": "start", "mode": "sleep_like"},
             "not admitted here"),
            ("缺 mode", {"state": "start"}, "incomplete"),
            ("多字段", {"state": "start", "mode": "rest", "seconds": 60}, "unknown fields"),
            ("越界 state", {"state": "begin", "mode": "rest"}, "state must be one of"),
        )
        for label, params, expected in refused:
            with self.subTest(case=label):
                with self.assertRaises(ai.ActionIntentError) as caught:
                    ai.build_submission(intent("REST", params), fresh_snapshot=snapshot)
                self.assertIn(expected, str(caught.exception))

        # 其他动词一个字没变：没有自由 params 通道，未知动词仍被 allowlist 拦。
        moved = ai.build_submission(
            intent("MOVE", {"destination_location_id": "cafe"}), fresh_snapshot=snapshot)
        self.assertEqual(moved["params"], {"destination_location_id": "cafe"})
        with self.assertRaises(ai.ActionIntentError) as caught:
            ai.build_submission(intent("ARRANGE", {"object_id": "x"}),
                                fresh_snapshot=snapshot)
        self.assertIn("production allowlist", str(caught.exception))
        self.assertEqual(ai.REST_STATES, ("start", "stop"))
        self.assertEqual(ai.REST_MODES, ("rest",))
        self.assertIn("REST", ai.EXPOSED_VERBS)


# ---------------------------------------------------------------------------
# 9-12. 真实 World service + IntentExecutor + gateway + Resolver
# ---------------------------------------------------------------------------


class _ChainHarness:
    """一个真数据根 + 真 ``WorldBodyService``（真 gateway UDS、真 Resolver）。

    只替换"谁来读盘"这一件事：世界包用 ``init_instance`` 真建，身体基底用
    ``initial_substrate`` 真写（pose=standing / orientation=south，这是夹具的初始状态，
    不是伪造的恢复需求）。
    """

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="chain-", dir=str(FIXTURE_ROOT)))
        self.home = self.root / "world-home"
        init_instance(WORLD_PACKAGE, self.home)
        state = json.loads((self.home / "data" / "world" / "world_state.json")
                           .read_text("utf-8"))
        self.world_id = state["world_id"]
        (self.home / "run").mkdir(parents=True, exist_ok=True)
        self.store = wbs.SubstrateStore(wbs.substrate_path(self.home))
        self.store.save(wbs.initial_substrate(world_id=self.world_id))
        self.env = {
            "CHIYO_WORLD_PACKAGE": str(WORLD_PACKAGE),
            "CHIYO_WORLD_HOME": str(self.home),
            "CHIYO_WORLD_ID": self.world_id,
            "CHIYO_WORLD_LIVING_SKELETON_ENABLED": "1",
            "CHIYO_WORLD_TRAVEL_TIME_ENABLED": "1",
            "CHIYO_WORLD_DAILY_RHYTHM_ENABLED": "0",
            "CHIYO_WORLD_AMBIENT_EVENTS_ENABLED": "0",
            "CHIYO_WORLD_SOCIAL_PRESENCE_ENABLED": "0",
            "CHIYO_WORLD_BODY_SUBSTRATE_ENABLED": "1",
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

    def start(self) -> Path:
        config = wcfg.load_config(dict(self.env), runtime_root=ROOT / "components" / "world",
                                  world_home=self.home, runtime_mode="canonical",
                                  authority="production", world_id=self.world_id)
        self.service = wbsvc.WorldBodyService(
            config, tick_seconds=3600.0,
            instance_id="bodyact-chain-%d" % os.getpid())
        self.service.startup()
        socket_path = self.service.gateway_socket_path
        self.thread = threading.Thread(
            target=self.service.serve, kwargs={"max_seconds": 900.0}, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 30.0
        while not socket_path.exists():
            if time.monotonic() > deadline:
                raise AssertionError("gateway socket 没出现: %s" % socket_path)
            time.sleep(0.02)
        return socket_path

    def bridge(self, socket_path: Path):
        env = dict(self.env)
        env["WORLD_BODY_SOCKET"] = str(socket_path)
        return wba.WorldBridgeResolver(environment=env, bridge_dir=BRIDGE_DIR)

    def substrate(self) -> dict:
        return self.store.load()

    def close(self) -> None:
        """只放租约、只停线程：不 ``shutdown()``、不 unlink 任何 socket。"""

        if self.service is not None and self.service.lease is not None:
            try:
                self.service.lease.release()
            except Exception:  # noqa: BLE001 - 已释放或已失效都不是测试结论
                pass
        try:
            os.environ.clear()
            os.environ.update(self.saved_env)
        finally:
            wrd.reset_cache()


@posix_only
@needs_life
class RealWorldBodyActionChainTests(unittest.TestCase):
    """真实链：Life bridge -> IntentExecutor -> gateway.sock -> Resolver。"""

    def setUp(self) -> None:
        self.h = _ChainHarness()
        self.addCleanup(self.h.close)
        self.socket = self.h.start()
        self.bridge = self.h.bridge(self.socket)

    def test_pose_reaches_the_real_owner_and_repeats_do_not_commit_twice(self) -> None:
        before = self.h.substrate()
        self.assertEqual((before["pose"], before["orientation"]), ("standing", "south"))

        result = self.bridge.resolve_action(
            idempotency_key="bodyact-test-pose-1", verb="POSE",
            target="bodyact:pose:seated:north", expected_dependencies={})
        self.assertEqual(result["status"], "committed", result)
        self.assertEqual(result["code"], "OK", result)
        after = self.h.substrate()
        self.assertEqual((after["pose"], after["orientation"]), ("seated", "north"))
        # 回读是 owner 自己的快照，不是把请求目标复述一遍。
        self.assertEqual(result["details"]["body_observed"],
                         {"pose": "seated", "orientation": "north"})
        self.assertEqual(result["details"]["observation_source"],
                         "world_gateway.get_snapshot")

        # 同一姿态再来一次：基底不再前进（owner 侧 noop）。World client 把 owner 的
        # noop 归到 SUCCESS，所以判据是**基底 revision**，不是 bridge 的标签。
        again = self.bridge.resolve_action(
            idempotency_key="bodyact-test-pose-2", verb="POSE",
            target="bodyact:pose:seated:north", expected_dependencies={})
        self.assertEqual(again["world_revision"], result["world_revision"])
        self.assertEqual(self.h.substrate()["revision"], after["revision"])
        self.assertEqual(self.h.substrate()["pose"], "seated")

    def test_rest_clears_the_intent_allowlist_and_stops_at_the_owners_availability(self) -> None:
        """REST 现在真的进了正式链：intent 层放行、铸票、提交；拒绝来自 owner 的可用性。

        B6E1 之前这里是 ``INVALID / "not in the production allowlist"``（intent 层根本不放行）。
        现在同一请求通过 allowlist 与参数契约、拿到一次性授权、被提交到 gateway，
        然后由 gateway 的曝光策略按 owner 自己的 ``available`` 回答 ``NOT_EXPOSED``。
        这条差别就是"上游断链"与"owner 说现在不需要"的分界，用 lifecycle 逐段证明。
        """

        # 真实链用的就是 bridge 这一份清单（同名副本见"已知限制"）。
        self.assertTrue(ai.__file__.replace("\\", "/").endswith(
            "components/world/service/bridge/action_intent.py"), ai.__file__)
        self.assertIn("REST", ai.ACTION_ALLOWLIST)
        self.assertEqual(ai.REST_MODES, ("rest",))

        before = self.h.substrate()
        body_before = self._body_bytes()

        started = self.bridge.resolve_action(
            idempotency_key="bodyact-test-rest-1", verb="REST",
            target="bodyact:rest:start:rest", expected_dependencies={})
        self.assertEqual(started["status"], "rejected", started)
        self.assertEqual(started["detail"], "NOT_EXPOSED", started)
        self.assertEqual(started["transport_result"],
                         ar0.TRANSPORT_ERR_DEFINITE_REJECT)
        # 旧的 intent 层阻塞确实没了：拒绝理由不再提 allowlist。
        self.assertNotIn("production allowlist", started["detail"])

        # lifecycle 逐段：CREATED → AUTHORIZED（带票与精确参数）→ SUBMITTING → SUBMITTED →
        # REJECTED(NOT_EXPOSED)。SUBMITTED 在位就是"请求真的交到了 owner 手上"的证据。
        states = self._lifecycle(started["execution_id"])
        self.assertEqual([item["state"] for item in states],
                         ["CREATED", "AUTHORIZED", "SUBMITTING", "SUBMITTED",
                          "REJECTED"], states)
        self.assertTrue(states[1]["authorization_id"].startswith("wauth:"), states[1])
        self.assertEqual(states[1]["target"], {"mode": "rest", "state": "start"})
        self.assertEqual(states[4]["failure_class"], "NOT_EXPOSED")

        # stop 同理：现在不是"清单不收"，而是 owner 说没有可停的会话。
        stopped = self.bridge.resolve_action(
            idempotency_key="bodyact-test-rest-2", verb="REST",
            target="bodyact:rest:stop:rest", expected_dependencies={})
        self.assertEqual(stopped["status"], "rejected", stopped)
        self.assertEqual(stopped["detail"], "NOT_EXPOSED", stopped)
        self.assertEqual(
            self._lifecycle(stopped["execution_id"])[1]["target"],
            {"mode": "rest", "state": "stop"})
        # owner 自己的 preflight 对"没有开会话就 stop"的回答是 NO_OPEN_RECOVERY。
        service = self.h.service
        refused_stop = service.act({
            "action": "REST",
            "execution_id": "bodyact-test-rest-stop-body",
            "actor_id": "chiyo",
            "body_id": "chiyo_body",
            "authority": war.AUTHORITY_VERIFIED_MAIN_SELF,
            "confirm_apply": True,
            "observed_dependencies": service.resolver._dependencies(),
            "params": {"state": "stop", "mode": "rest"},
        })
        self.assertEqual(refused_stop["status"], "rejected", refused_stop)
        self.assertEqual(refused_stop["reason_code"], "NO_OPEN_RECOVERY", refused_stop)
        self.assertFalse(refused_stop["applied_to_body"])
        self.assertTrue(refused_stop["body_unchanged"])

        # 零副作用：世界基底与身体运行态都没动，也没有任何东西被记成恢复。
        self.assertEqual(self.h.substrate()["revision"], before["revision"])
        self.assertEqual(self._body_bytes(), body_before)

    def test_the_owner_itself_refuses_rest_it_does_not_need(self) -> None:
        """身体不需要恢复时，owner 自己诚实拒绝——不直改 body/阈值/时钟。"""

        before = self.h.substrate()
        body_before = self._body_bytes()
        service = self.h.service

        # (a) Body preflight：服务在 Resolver 之前就拒，并说明原因。
        answer = service.act({
            "action": "REST",
            "execution_id": "bodyact-test-rest-body",
            "actor_id": "chiyo",
            "body_id": "chiyo_body",
            "authority": war.AUTHORITY_VERIFIED_MAIN_SELF,
            "confirm_apply": True,
            "observed_dependencies": service.resolver._dependencies(),
            "params": {"state": "start", "mode": "rest"},
        })
        self.assertEqual(answer["status"], "rejected", answer)
        self.assertEqual(answer["reason_code"], "RECOVERY_NOT_NEEDED", answer)
        self.assertEqual(answer["source"], "body_preflight")
        self.assertFalse(answer["applied_to_body"])
        self.assertTrue(answer["body_unchanged"])
        self.assertTrue(answer["world_unchanged"])

        # (b) 网关曝光面：REST 当前不 available，所以连 Resolver 都不该被联系。
        issued = service.gateway_surface.authorizations.issue(
            decision_id=None, intent_id="world-intent:bodyact-test-rest",
            intent_fingerprint="fp-bodyact-test-rest",
            execution_id="bodyact-test-rest-gateway", actor="chiyo", verb="REST",
            params={"state": "start", "mode": "rest"}, authority_generation=1)
        exposed = service.gateway_surface.submit_action({
            "action": "REST",
            "execution_id": "bodyact-test-rest-gateway",
            "params": {"state": "start", "mode": "rest"},
            "authorization_id": issued["authorization_id"],
            "decision_ref": None,
        })
        self.assertEqual(exposed["status"], "rejected", exposed)
        self.assertEqual(exposed["reason_code"], "NOT_EXPOSED", exposed)
        self.assertTrue(exposed["world_unchanged"])
        self.assertTrue(exposed["body_unchanged"])

        self.assertEqual(self.h.substrate()["revision"], before["revision"])
        self.assertEqual(self._body_bytes(), body_before)

    def test_an_invalid_body_target_never_reaches_the_owner(self) -> None:
        before = self.h.substrate()
        result = self.bridge.resolve_action(
            idempotency_key="bodyact-test-pose-bad", verb="POSE",
            target="bodyact:pose:sitting:north", expected_dependencies={})
        self.assertEqual(result["status"], "rejected", result)
        self.assertEqual(result["code"], "BODY_ACTION_TARGET_INVALID", result)
        self.assertEqual(result["transport_result"], ar0.TRANSPORT_ERR_DEFINITE_PRE_SUBMIT)
        self.assertIn("pose must be one of", result["detail"])
        # 没签发授权、没联系 owner、基底没动。
        lifecycle = self.h.home / "run" / "action_lifecycle.jsonl"
        if lifecycle.is_file():
            for line in lifecycle.read_text("utf-8").splitlines():
                if not line.strip():
                    continue
                entry = json.loads(line)
                self.assertNotEqual(entry.get("execution_id"), result["execution_id"])
        self.assertEqual(self.h.substrate()["revision"], before["revision"])
        self.assertEqual(self.h.substrate()["pose"], "standing")


    def test_sleep_like_never_reaches_the_owner(self) -> None:
        """sleep_like 不是"发射器没提供"：bridge 解码层就拒，连授权都不铸。

        Life 的编码（``life_body_action_targets``）与 World bridge 的 intent 参数契约
        （``action_intent.validate_verb_params``）各拒一次；这里证明真实链上第一条就把它挡在
        提交之前，所以 owner 从来没被联系，也没有任何东西被记成恢复。
        """

        before = self.h.substrate()
        body_before = self._body_bytes()
        result = self.bridge.resolve_action(
            idempotency_key="bodyact-test-sleep-like", verb="REST",
            target="bodyact:rest:start:sleep_like", expected_dependencies={})
        self.assertEqual(result["status"], "rejected", result)
        self.assertEqual(result["code"], "BODY_ACTION_TARGET_INVALID", result)
        self.assertEqual(result["transport_result"], ar0.TRANSPORT_ERR_DEFINITE_PRE_SUBMIT)
        self.assertIn("rest mode must be one of ['rest']", result["detail"])
        # 没有 lifecycle 记录 = 没有 CREATED/AUTHORIZED/SUBMITTED，owner 没被联系。
        self.assertEqual(self._lifecycle(result["execution_id"]), [])
        self.assertEqual(self.h.substrate()["revision"], before["revision"])
        self.assertEqual(self._body_bytes(), body_before)

    def _lifecycle(self, execution_id: str) -> list[dict]:
        """The owner-side intent lifecycle entries for one execution, in file order."""

        path = self.h.home / "run" / "action_lifecycle.jsonl"
        if not path.is_file():
            return []
        entries = []
        for line in path.read_text("utf-8").splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            if str(entry.get("execution_id")) == str(execution_id):
                entries.append(entry)
        return entries
    def _body_bytes(self) -> bytes:
        path = self.h.home / "data" / "body" / "runtime.json"
        return path.read_bytes() if path.is_file() else b""


if __name__ == "__main__":
    unittest.main(verbosity=2)

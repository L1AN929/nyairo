#!/usr/bin/env python3
"""v0.4 身体参与：有来源的定性身体处境 -> 明确选择姿态/休息/继续 -> 真实 owner 后果。

本文件是 A 包（身体链）的定向证据，**不继承**既有 TestCase，也不跑全库。三块：

1. ``BodyContextTests``（原生 Windows 也能跑）：``life_body_context`` 只做投影 —— 负荷信号只出
   定性档位、会话 OPEN/CLOSED 来自 World 自己的 REST 条目、``recovery.claim`` 永远是
   ``NOT_ASSERTED``、无信号/读不到是具名 UNKNOWN（不是健康），并且预算/原始值/候选一个字都不带。
2. ``BodyPromptTests``（同上）：这份身体处境进的是 ``ProductionAgencyCognitionAdapter`` 的
   **真实** payload（不是报告字段），超长 prompt 的结构化裁剪里身体块与现场一起留到最后，
   系统提示明确写了"会话不等于恢复 / 没信号不等于健康 / 没人逼她睡或吃"。
3. ``RealBodyChainTests``（POSIX，真实 World service + gateway + Resolver）：夹具声明一次
   "已经累了"的身体数据（``seed_body``，明确是脚本选择，不是自然生活），然后走真链
   ``POSE`` 坐下 -> ``REST`` start（owner 自己开门）-> 用真实读口读回身体处境（会话 OPEN、
   没有恢复保证）-> ``REST`` stop -> 会话 CLOSED 且仍然没有恢复保证；同一轮的决定处境里，
   暂停中的 Activity 与 due 的"稍后"事项仍在，供她重新认领。

夹具根只建不删（失败也留证）；不联网、不调模型、不改 owner 阈值、不删夹具。
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
from types import SimpleNamespace
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge"),
              str(ROOT / "components" / "native" / "integration")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_body_context as lbc  # noqa: E402
import body_capacity_source as body_capacity  # noqa: E402
import agency_cognition_adapter as aa  # noqa: E402

#: All fixtures live under one retained root (created once, never removed).
FIXTURE_ROOT = Path(tempfile.mkdtemp(prefix="nyairov04a-body-"))

MOMENT = "2026-11-13T09:30:00+00:00"

#: POSIX-only parts (fcntl / AF_UNIX / geteuid).
_LIFE_ERROR: str | None = None
try:
    import autonomous_life_cycle as alc  # noqa: E402
except Exception as error:  # noqa: BLE001 - environment limit, not product behaviour
    _LIFE_ERROR = "%s: %s" % (type(error).__name__, error)

_WORLD_ERROR: str | None = None
try:
    from components.worldgen.instance import init_instance  # noqa: E402

    import body_runtime as body_runtime  # noqa: E402
    import world_action_resolver as war  # noqa: E402
    import world_body_config as wcfg  # noqa: E402
    import world_body_service as wbsvc  # noqa: E402
    import world_body_substrate as wbs  # noqa: E402
    import world_runtime_data as wrd  # noqa: E402
    import world_body_client as wbc  # noqa: E402
    from world_body_read_port import WorldBodyReadPort  # noqa: E402
except Exception as error:  # noqa: BLE001
    _WORLD_ERROR = "%s: %s" % (type(error).__name__, error)

needs_life = unittest.skipIf(_LIFE_ERROR is not None,
                             "Life 模块需要 POSIX（fcntl）：%s" % (_LIFE_ERROR or ""))
posix_only = unittest.skipIf(os.name == "nt" or _WORLD_ERROR is not None,
                             "真实 World service 需要 POSIX：%s" % (_WORLD_ERROR or ""))


# ---------------------------------------------------------------------------------------
# 0. declared fixtures (scripted, on purpose -- not a claim of a naturally lived day)
# ---------------------------------------------------------------------------------------


def _body_column(*, capacity="LIMITED", evidence=None, bound=True):
    return {
        "body_capacity_state": capacity,
        "source": "world_body_read_port.get_body_signals",
        "reason_code": "OK_LIMITED_BY_BODY_SIGNAL" if capacity == "LIMITED" else "NO_BURDEN_EVIDENCE",
        "observed_at": MOMENT,
        "observed_body_id": "chiyo_body",
        "observed_revision": "bodyrev:7",
        "freshness": "FRESH" if bound else None,
        "evidence": list(evidence or []),
        "identity_bound": bound,
        "revision_bound": bound,
        "observation_time_bound": bound,
    }


def _rest_entry(*, session="active", start=False, stop=True, why="RECOVERY_ALREADY_ACTIVE",
                pose="seated", surface=True):
    return {
        "verb": "REST",
        "available": bool(start or stop),
        "params": {"state": ["start", "stop"], "mode": ["rest", "sleep_like"]},
        "start_available": start,
        "stop_available": stop,
        "sleep_like_available": False,
        "start_blocked_reason": None if start else why,
        "stop_blocked_reason": None if stop else "NO_OPEN_RECOVERY",
        "current": {"pose": pose, "recovery_state": session, "travelling": False,
                    "rest_surface": "bed_surface" if surface else None},
    }


# ---------------------------------------------------------------------------------------
# 1. the projection itself
# ---------------------------------------------------------------------------------------


class BodyContextTests(unittest.TestCase):
    def test_a_limited_body_shows_bands_a_session_and_no_recovery_claim(self):
        context = lbc.build(
            body_column=_body_column(evidence=["fatigue_high:strong", "recovery_interrupted:mild"]),
            rest=_rest_entry())
        self.assertEqual(context["status"], lbc.STATUS_OBSERVED)
        self.assertEqual(context["capacity"], body_capacity.CAPACITY_LIMITED)
        self.assertEqual(context["burden"],
                         [{"signal": "fatigue_high", "band": "strong"},
                          {"signal": "recovery_interrupted", "band": "mild"}])
        self.assertEqual(context["recovery"]["session"], lbc.RECOVERY_SESSION_OPEN)
        # 会话 ≠ 恢复：命令成功不等于身体恢复，这里是唯一的说法。
        self.assertEqual(context["recovery"]["claim"], lbc.RECOVERY_CLAIM_NOT_ASSERTED)
        self.assertEqual(context["recovery"]["progress"], lbc.RECOVERY_PROGRESS_NOT_READ)
        self.assertIs(context["rest"]["can_start"], False)
        self.assertIs(context["rest"]["can_stop"], True)
        self.assertEqual(context["rest"]["why_not"], "RECOVERY_ALREADY_ACTIVE")
        self.assertEqual(context["rest"]["pose"], "seated")
        self.assertTrue(context["rest"]["rest_surface"])
        self.assertFalse(context["limits"]["raw_values_exposed"])
        self.assertEqual(context["limits"]["reads_performed"], 0)

    def test_an_unread_body_is_unknown_and_never_healthy(self):
        context = lbc.build(body_column=None, rest=_rest_entry(session="inactive", start=True,
                                                               stop=False, why=None))
        self.assertEqual(context["status"], lbc.STATUS_UNKNOWN)
        self.assertEqual(context["capacity"], body_capacity.CAPACITY_UNKNOWN)
        self.assertEqual(context["recovery"]["session"], lbc.RECOVERY_SESSION_UNKNOWN)
        self.assertNotEqual(context["capacity"], body_capacity.CAPACITY_AVAILABLE)
        self.assertIn(lbc.REASON_BODY_COLUMN_ABSENT, context["unknowns"])

    def test_a_quiet_body_is_unknown_not_healthy_and_not_an_obligation(self):
        context = lbc.build(body_column=_body_column(capacity="UNKNOWN", evidence=[]),
                            rest=_rest_entry(session="inactive", start=False, stop=False,
                                             why="RECOVERY_NOT_NEEDED"))
        self.assertEqual(context["capacity"], body_capacity.CAPACITY_UNKNOWN)
        self.assertEqual(context["status"], lbc.STATUS_PARTIAL)
        self.assertIn(lbc.UNKNOWN_BURDEN_IS_NOT_HEALTH, context["unknowns"])
        self.assertEqual(context["rest"]["why_not"], "RECOVERY_NOT_NEEDED")
        self.assertIs(context["rest"]["can_start"], False)

    def test_a_missing_rest_entry_leaves_session_and_capability_named_unknown(self):
        context = lbc.build(body_column=_body_column(evidence=["fatigue_noticeable:mild"]),
                            rest=None)
        self.assertEqual(context["recovery"]["session"], lbc.RECOVERY_SESSION_UNKNOWN)
        self.assertIn(lbc.UNKNOWN_RECOVERY_SESSION, context["unknowns"])
        self.assertIn(lbc.UNKNOWN_REST_CAPABILITY, context["unknowns"])
        self.assertIsNone(context["rest"]["can_start"])
        # The body itself was still read, so its qualitative burden survives.
        self.assertEqual(context["burden"], [{"signal": "fatigue_noticeable", "band": "mild"}])

    def test_stopping_a_session_closes_it_without_claiming_recovery(self):
        context = lbc.build(
            body_column=_body_column(evidence=["fatigue_noticeable:mild"]),
            rest=_rest_entry(session="inactive", start=True, stop=False, why=None))
        self.assertEqual(context["recovery"]["session"], lbc.RECOVERY_SESSION_CLOSED)
        self.assertEqual(context["recovery"]["claim"], lbc.RECOVERY_CLAIM_NOT_ASSERTED)
        self.assertIs(context["rest"]["can_start"], True)
        self.assertIsNone(context["rest"]["why_not"])

    def test_thermal_signals_are_not_reported_as_easing_and_trends_are(self):
        context = lbc.build(
            body_column=_body_column(evidence=["warmth_high:strong", "cold_tendency:mild",
                                               "recovery_underway:mild"]),
            rest=_rest_entry())
        self.assertEqual(context["easing"], [{"signal": "recovery_underway", "band": "mild"}])

    def test_no_budget_no_raw_values_no_candidates_ever_ride_along(self):
        column = dict(_body_column(evidence=["fatigue_high:strong"]))
        column["resource_budget"] = {"state": "corrupt", "allowed": False}
        column["fatigue"] = 0.87
        context = lbc.build(body_column=column, rest=_rest_entry())
        blob = json.dumps(context, ensure_ascii=False)
        self.assertNotIn("budget", blob)
        self.assertNotIn("0.87", blob)
        self.assertFalse(context["not_authority"]["candidate"])
        self.assertFalse(context["not_authority"]["grant"])
        # 档位是字符串枚举，身体数值一个都不出现。
        for row in context["burden"]:
            self.assertIsInstance(row["band"], str)

    def test_an_unbound_body_view_says_so_instead_of_pretending_freshness(self):
        context = lbc.build(body_column=_body_column(evidence=[], bound=False),
                            rest=_rest_entry())
        self.assertIn(lbc.REASON_BODY_VIEW_UNBOUND, context["unknowns"])
        self.assertIsNone(context["freshness"])

    def test_a_column_without_a_capacity_state_is_named_not_guessed(self):
        context = lbc.build(body_column={"evidence": ["fatigue_high:strong"]})
        self.assertEqual(context["status"], lbc.STATUS_UNKNOWN)
        self.assertEqual(context["reason_code"], lbc.REASON_BODY_COLUMN_INVALID)


# ---------------------------------------------------------------------------------------
# 2. the prompt the real model would read
# ---------------------------------------------------------------------------------------


class _Client:
    provider = "scripted"
    model = "scripted-decision"


def _request(situation_context, *, candidates=None):
    return SimpleNamespace(
        opportunity_id="opp:body-1", snapshot_fingerprint="fp:body-1",
        policy_version="1", legal_decision_enums=["START", "NO_ACTION"],
        allowed_reason_codes=["OK"], current_activity_summary=None, capacity_summary=None,
        observed_fact_keys=["world_here_place_home"],
        prior_experiences=[],
        situation_context=situation_context,
        candidates=candidates or [{"candidate_id": "cand:rest", "candidate_kind": "WORLD",
                                   "source_kind": "WORLD_OPPORTUNITY",
                                   "source_ref": "world:opp:rest",
                                   "target_refs": ["bodyact:rest:start:rest"],
                                   "legal_decisions": ["START", "NO_ACTION"]}],
        max_output_tokens=512,
    )


class BodyPromptTests(unittest.TestCase):
    def _situation(self):
        body = lbc.build(body_column=_body_column(evidence=["fatigue_high:strong"]),
                         rest=_rest_entry())
        world = {"status": "observed",
                 "location": {"name": "家", "place_id": "home"},
                 "body": {"pose": "seated", "orientation": "north"},
                 "nearby": [{"place_id": "home", "walk_seconds": 0}],
                 "opportunities": [{"verb": "REST", "label": "歇一会儿"}],
                 "unknowns": []}
        return {"schema_version": "chiyo.life.decision_situation.v1",
                "world": world, "body": body, "now": MOMENT}

    def test_the_body_block_reaches_the_real_prompt_without_numbers(self):
        adapter = aa.ProductionAgencyCognitionAdapter(client=_Client(), model_id="scripted")
        text, payload = adapter.build_prompt(_request(self._situation()))
        body = payload["situation_context"]["body"]
        self.assertEqual(body["capacity"], "LIMITED")
        self.assertEqual(body["recovery"]["session"], "OPEN")
        self.assertEqual(body["recovery"]["claim"], "NOT_ASSERTED")
        self.assertEqual(body["burden"], [{"signal": "fatigue_high", "band": "strong"}])
        self.assertIs(body["rest"]["can_start"], False)
        self.assertEqual(body["rest"]["why_not"], "RECOVERY_ALREADY_ACTIVE")
        self.assertNotIn("fatigue\": 0", text)
        self.assertNotIn("limits", body)
        self.assertNotIn("budget", json.dumps(body, ensure_ascii=False))

    def test_a_wide_body_block_is_bounded_by_the_adapter_not_by_the_caller(self):
        situation = self._situation()
        situation["body"] = dict(situation["body"])
        situation["body"]["burden"] = [{"signal": "fatigue_high", "band": "strong"}] * 50
        situation["body"]["easing"] = [{"signal": "recovery_underway", "band": "mild"}] * 50
        adapter = aa.ProductionAgencyCognitionAdapter(client=_Client(), model_id="scripted")
        _, payload = adapter.build_prompt(_request(situation))
        body = payload["situation_context"]["body"]
        self.assertLessEqual(len(body["burden"]), 4)
        self.assertLessEqual(len(body["easing"]), 3)

    def test_the_body_survives_a_structural_trim_next_to_the_live_scene(self):
        situation = self._situation()
        situation["practice"] = {"methods": [{"method_ref": "pm:%d" % i,
                                              "title": "x" * 400,
                                              "instruction": "y" * 400} for i in range(3)]}
        situation["project_options"] = [{"option_ref": "opt:%d" % i, "title": "t" * 300,
                                         "seed_excerpt": "s" * 300} for i in range(4)]
        situation["later_items"] = [{"item_ref": "fitem:%d" % i, "title": "l" * 300}
                                    for i in range(6)]
        situation["research"] = {"materials": [{"artifact_id": "m:%d" % i,
                                                "excerpt": "r" * 400} for i in range(4)]}
        # 一个明显放不下的上限：逼裁剪走到最后一步（只留现场与身体）。
        adapter = aa.ProductionAgencyCognitionAdapter(client=_Client(), model_id="scripted",
                                                      max_prompt_chars=1200)
        text, payload = adapter.build_prompt(_request(situation))
        json.loads(text)
        kept = payload["situation_context"]
        self.assertEqual(kept["world"]["location"]["name"], "家")
        self.assertEqual(kept["body"]["recovery"]["session"], "OPEN")
        self.assertNotIn("research", kept)
        # 只掉了研究明细：身体块与现场一起留到最后（见下一条 trimmed 断言）。
        self.assertTrue(adapter.last_prompt_trim["trimmed"])
        steps = {row["step"] for row in adapter.last_prompt_trim["dropped"]}
        self.assertIn("situation_except_world_and_body", steps)

    def test_the_system_prompt_states_session_health_and_non_obligation(self):
        prompt = aa.SYSTEM_PROMPT
        self.assertIn("situation_context.body", prompt)
        self.assertIn("NOT_ASSERTED", prompt)
        self.assertIn("never obliged to sleep or eat", prompt)
        self.assertIn("not** health", prompt)


# ---------------------------------------------------------------------------------------
# 3. the cycle puts the same read in the decision situation
# ---------------------------------------------------------------------------------------


class _SituationSource:
    def __init__(self, view):
        self.view = dict(view)
        self.calls = 0

    def build(self, *, now, budget, observations):
        self.calls += 1
        return dict(self.view)


class _Runtime:
    def __init__(self, root):
        self.state_root = Path(root)
        self.world_opportunity_source = SimpleNamespace(
            record_observed_opportunity=lambda item: None,
            expire_opportunity=lambda ref, observed_at=None: None)
        self.candidate_read = None
        self.activity_read = SimpleNamespace(
            get_current_life_view=lambda: {"foreground_activity_ref": "activity:1",
                                           "foreground_activity_kind": "READ_PERSONAL_MATERIAL"},
            get_activity=lambda ref: {"activity_id": "activity:1", "status": "WAITING",
                                      "activity_kind": "READ_PERSONAL_MATERIAL"},
            list_open_activities=lambda: [])
        self.ag2_progress_port = None
        self.world_action_adapter = None
        self.artifact_action_adapter = None
        self.kill_switches = SimpleNamespace(to_dict=lambda: {})


def _situation_column(*, evidence, capacity="LIMITED"):
    """A body column in A's own situation-view shape (owner: ``life_situation_view._column``)."""

    return {
        "state": capacity,
        "ref": "bodycap:chiyo_body:bodyrev:7",
        "source": "world_body_read_port.get_body_signals",
        "reason_code": "OK_LIMITED_BY_BODY_SIGNAL" if capacity == "LIMITED" else "NO_BURDEN_EVIDENCE",
        "derived_by": "body_capacity_source.read_body_capacity",
        "owner_asserted": False,
        "freshness": "FRESH",
        "observed_at": MOMENT,
        "observed_body_id": "chiyo_body",
        "observed_revision": "bodyrev:7",
        "evidence": list(evidence),
        "identity_bound": True,
        "revision_bound": True,
        "observation_time_bound": True,
    }


def _situation_view(*, evidence, capacity="LIMITED"):
    column = _situation_column(evidence=evidence, capacity=capacity)
    return {
        "schema_version": "chiyo.life.situation_view.v1",
        "kind": "life_situation_view",
        "observed_at": MOMENT,
        "identity_bound": True,
        "body": column,
        "resource_budget": {"state": "AVAILABLE", "ref": "budget:ledger:2026-11-13",
                            "source": "life_request_budget.check", "allowed": True},
        "dimensions": {"body": capacity, "resource_budget": "AVAILABLE"},
        "limiting_dimensions": ["body"] if capacity == "LIMITED" else [],
        "unknown_dimensions": [],
    }


@needs_life
class BodySituationContextTests(unittest.TestCase):
    def _cycle(self, *, view, rest):
        root = Path(tempfile.mkdtemp(prefix="ctx-", dir=str(FIXTURE_ROOT)))
        provider = SimpleNamespace(last_available_actions={"actions": [rest]} if rest else None)
        source = _SituationSource(view)
        cycle = alc.AutonomousLifeCycle(
            _Runtime(root), enabled=True, env={},
            world_opportunity_provider=provider, situation_source=source)
        cycle._last_foreground_record = {"activity_ref": "activity:1", "status": "WAITING",
                                         "activity_kind": "READ_PERSONAL_MATERIAL"}
        return cycle, source

    def test_the_decision_reads_the_same_body_read_the_capacity_came_from(self):
        cycle, source = self._cycle(
            view=_situation_view(evidence=["fatigue_high:strong"]), rest=_rest_entry())
        situation = cycle.read_situation(MOMENT)
        kwargs = cycle.capacity_kwargs(situation)
        context = cycle.decision_situation_context(now=MOMENT)
        self.assertEqual(source.calls, 1)              # 一次身体读，决定与容量共用
        self.assertEqual(kwargs["body_capacity_state"], "LIMITED")
        body = context["body"]
        self.assertEqual(body["capacity"], "LIMITED")
        self.assertEqual(body["recovery"]["session"], "OPEN")
        self.assertEqual(body["recovery"]["claim"], "NOT_ASSERTED")
        self.assertEqual(body["source"], "world_body_read_port.get_body_signals")
        self.assertEqual(body["revision"], "bodyrev:7")

    def test_a_round_with_no_body_read_says_unknown_in_the_context(self):
        cycle, _ = self._cycle(view={"reason_code": "SITUATION_SOURCE_NOT_WIRED"}, rest=None)
        cycle.read_situation(MOMENT)
        body = cycle.decision_situation_context(now=MOMENT)["body"]
        self.assertEqual(body["status"], lbc.STATUS_UNKNOWN)
        self.assertEqual(body["capacity"], "UNKNOWN")
        self.assertNotEqual(body["recovery"]["session"], "OPEN")

    def test_the_parked_activity_and_its_later_item_stay_visible_for_reclaiming(self):
        cycle, _ = self._cycle(
            view=_situation_view(evidence=["fatigue_noticeable:mild"]),
            rest=_rest_entry(session="inactive", start=True, stop=False, why=None))
        cycle.read_situation(MOMENT)
        context = cycle.decision_situation_context(now=MOMENT)
        self.assertEqual(context["body"]["recovery"]["session"], "CLOSED")
        # 休息完了仍然没有"已恢复"的保证；她要不要接着做由她自己再认领。
        self.assertEqual(context["body"]["recovery"]["claim"], "NOT_ASSERTED")
        self.assertEqual(context["activity"]["status"], "WAITING")
        self.assertEqual(context["activity"]["activity_ref"], "activity:1")


# ---------------------------------------------------------------------------------------
# 4. the real owner chain (POSIX): POSE -> REST start -> read back -> REST stop
# ---------------------------------------------------------------------------------------

WORLD_PACKAGE = ROOT / "examples" / "worlds" / "daily_neighborhood_v1"
BRIDGE_DIR = ROOT / "components" / "world" / "service" / "bridge"


class _ChainHarness:
    """Real instance home + real ``WorldBodyService`` (real gateway UDS, real Resolver).

    Only two things are declared by the fixture, on purpose: the world package is a real
    ``init_instance`` build, and the body's fatigue is *seeded once* (``seed_body``) so the
    test can start from "she is already tired" instead of pretending a whole day passed.
    """

    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="bodychain-", dir=str(FIXTURE_ROOT)))
        self.home = self.root / "world-home"
        init_instance(WORLD_PACKAGE, self.home)
        state = json.loads((self.home / "data" / "world" / "world_state.json")
                           .read_text("utf-8"))
        self.world_id = state["world_id"]
        (self.home / "run").mkdir(parents=True, exist_ok=True)
        self.store = wbs.SubstrateStore(wbs.substrate_path(self.home))
        self.store.save(wbs.initial_substrate(world_id=self.world_id))
        self.seed_body(fatigue=0.60)
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
            "WORLD_BODY_INTEGRATION_MODE": "canonical",
            "NYAIRO_WORLD_BRIDGE_DIR": str(BRIDGE_DIR),
        }
        self.saved_env = dict(os.environ)
        os.environ.update(self.env)
        wrd.reset_cache()
        self.service = None
        self.thread = None

    def seed_body(self, *, fatigue):
        moment = datetime.now(timezone.utc)
        state = body_runtime.initial_runtime(at=moment)
        state["somatic"]["fatigue"] = float(fatigue)
        body_runtime.BodyStore(self.home / "data" / "body").save_runtime(state)
        self.seeded_at = moment

    def start(self) -> Path:
        config = wcfg.load_config(dict(self.env), runtime_root=ROOT / "components" / "world",
                                  world_home=self.home, runtime_mode="canonical",
                                  authority="production", world_id=self.world_id)
        self.service = wbsvc.WorldBodyService(
            config, tick_seconds=3600.0, instance_id="bodyctx-chain-%d" % os.getpid())
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

    def act(self, verb, params):
        return self.service.act({
            "action": verb,
            "execution_id": "bodyctx-%s-%d" % (verb.lower(), time.monotonic_ns()),
            "actor_id": "chiyo",
            "body_id": "chiyo_body",
            "authority": war.AUTHORITY_VERIFIED_MAIN_SELF,
            "confirm_apply": True,
            "observed_dependencies": self.service.resolver._dependencies(),
            "params": dict(params),
        })

    def close(self) -> None:
        if self.service is not None and self.service.lease is not None:
            try:
                self.service.lease.release()
            except Exception:  # noqa: BLE001 - 已释放不是测试结论
                pass
        try:
            os.environ.clear()
            os.environ.update(self.saved_env)
        finally:
            wrd.reset_cache()


@posix_only
@needs_life
class RealBodyChainTests(unittest.TestCase):
    def setUp(self):
        self.h = _ChainHarness()
        self.addCleanup(self.h.close)
        self.socket = self.h.start()
        self.client = wbc.WorldBodyClient(socket_path=self.socket, mode=wbc.MODE_CANONICAL,
                                          environment=self.h.env)
        self.port = WorldBodyReadPort(self.client)

    def _rest_entry(self):
        result = self.client.get_available_actions()
        self.assertTrue(result.ok, result.as_dict())
        for entry in (result.data or {}).get("actions") or []:
            if entry.get("verb") == "REST":
                return dict(entry)
        raise AssertionError("gateway 没有发布 REST 条目: %r" % (result.data,))

    def _body_context(self, rest):
        payload = self.port.body()
        self.assertEqual(str(payload.get("outcome")), "OK", payload)
        signals = (payload.get("data") or {}).get("signals") or []
        # 有信号就以 owner 自己的观测时刻为准；没有信号是"没事可报"，不是"健康"。
        now = (str(signals[0].get("occurred_at")) if signals
               else datetime.now(timezone.utc).isoformat())
        view = body_capacity.read_body_capacity(self.port, home=self.h.home, now=now)
        context = lbc.build(body_column=view, rest=rest, observed_at=now)
        return context, view, signals

    def test_rest_is_a_real_session_and_never_a_recovery_claim(self):
        sitting = self.h.act("POSE", {"pose": "seated", "orientation": "north"})
        self.assertIn(sitting.get("status"), ("applied", "committed", "noop"), sitting)

        started = self.h.act("REST", {"state": "start", "mode": "rest"})
        self.assertIn(started.get("status"), ("applied", "committed"), started)
        self.assertTrue(started.get("applied_to_body"), started)

        rest = self._rest_entry()
        self.assertIs(rest["start_available"], False)
        self.assertIs(rest["stop_available"], True)
        self.assertEqual(rest["start_blocked_reason"], "RECOVERY_ALREADY_ACTIVE")
        self.assertEqual(rest["current"]["recovery_state"], "active")

        context, view, signals = self._body_context(rest)
        self.assertEqual(context["recovery"]["session"], lbc.RECOVERY_SESSION_OPEN)
        # 真链上也没有"恢复了"这件事：只有会话开着。
        self.assertEqual(context["recovery"]["claim"], lbc.RECOVERY_CLAIM_NOT_ASSERTED)
        self.assertIn(context["capacity"], body_capacity.VALID_CAPACITY_STATES)
        self.assertNotEqual(context["capacity"], body_capacity.CAPACITY_AVAILABLE)
        burden_types = {row["signal"] for row in context["burden"]}
        self.assertTrue(burden_types <= body_capacity.BURDEN_SIGNAL_TYPES)
        # 真实读来的负荷信号确实在（种子 0.60 的疲劳在 owner 自己的阈值之上）。
        self.assertIn("fatigue_noticeable",
                      {str(s.get("signal_type")) for s in signals}, signals)
        if view["body_capacity_state"] == body_capacity.CAPACITY_LIMITED:
            self.assertTrue(context["burden"])
            self.assertEqual(context["freshness"], "FRESH")

        # stop 只带 state：owner 的参数契约只允许 state=start 时出现 mode。
        stopped = self.h.act("REST", {"state": "stop"})
        self.assertIn(stopped.get("status"), ("applied", "committed"), stopped)
        after_rest = self._rest_entry()
        # 会话结束后 owner 自己说 "completed"（或 interrupted / inactive）——都不是"恢复了"。
        self.assertIn(after_rest["current"]["recovery_state"],
                      ("inactive", "completed", "interrupted"))
        after, _, _ = self._body_context(after_rest)
        self.assertEqual(after["recovery"]["session"], lbc.RECOVERY_SESSION_CLOSED)
        self.assertEqual(after["recovery"]["claim"], lbc.RECOVERY_CLAIM_NOT_ASSERTED)

    def test_a_body_that_does_not_need_rest_is_told_so_by_the_owner(self):
        # 同一个夹具里先把"累"降到 owner 阈值之下：这不是健康推断，只是把夹具的输入说清楚。
        self.h.close()
        self.h = _ChainHarness()
        self.h.seed_body(fatigue=0.05)
        self.socket = self.h.start()
        self.client = wbc.WorldBodyClient(socket_path=self.socket, mode=wbc.MODE_CANONICAL,
                                          environment=self.h.env)
        self.port = WorldBodyReadPort(self.client)
        sitting = self.h.act("POSE", {"pose": "seated", "orientation": "north"})
        self.assertIn(sitting.get("status"), ("applied", "committed", "noop"), sitting)
        rest = self._rest_entry()
        context, _, _ = self._body_context(rest)
        if rest.get("start_available") is not True:
            self.assertEqual(rest.get("start_blocked_reason"), "RECOVERY_NOT_NEEDED", rest)
        self.assertEqual(context["recovery"]["claim"], lbc.RECOVERY_CLAIM_NOT_ASSERTED)
        self.assertNotEqual(context["capacity"], body_capacity.CAPACITY_AVAILABLE)


if __name__ == "__main__":
    unittest.main(verbosity=2)

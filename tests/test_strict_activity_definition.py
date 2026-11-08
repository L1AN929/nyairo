#!/usr/bin/env python3
"""严格活动定义层：REST / READ_HERE 显式 ``stay_at_place`` 的第一层把关。

只证明四件事（四个聚合用例）：

1. 正式模板生成世界 → schema/load/catalog → 定义解析 → Life 现有候选，
   标题/秒数/机制一致；adapter/client/script transport 真正收到的 user JSON
   里能看到同一对象定义信息，决策仍是 NO_ACTION；
2. 显式 execution 缺 mechanism/title/秒数、未知机制、不支持活动、null/类型错/
   未知键，在 generator 与 runtime 读取路径分别报固定错误码，不生候选、
   不用 QUIET_STOP 补救；
3. “普通环境描述”和“显式可执行定义”只按结构区分（同一未知 ID，有无 execution
   两种 fixture）：前者零候选不报错，后者具名错误；混合也绝不悄悄滤掉错误；
4. 已有 legacy 完整绑定继续按原准入/排序工作；闭店/不可执行仍由第二层拒绝，
   第一层通过不新发 grant、不绕 AG-0。

需要 POSIX 只因 Life cycle 链导入 fcntl；本文件不打开任何 socket。
scratch 目录用 ``mkdtemp`` 创建并保留，不删除任何东西。
"""

from __future__ import annotations

import copy
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
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge"),
              str(ROOT / "components" / "supply"), str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

if os.name != "posix":  # the real Life cycle chain imports fcntl
    raise unittest.SkipTest("strict activity definition acceptance needs a POSIX host")

import agency_cognition_adapter as aca  # noqa: E402
import candidate_sources_ag0 as ag0  # noqa: E402
import life_live_context_view as llcv  # noqa: E402
import world_body_client as wbc  # noqa: E402
import world_move_opportunity_source as wmos  # noqa: E402
from agency_model_client import AgencyModelClient, ModelCallResult  # noqa: E402
from components.worldgen import activity_definition, generator, loader, schema  # noqa: E402
from components.worldgen.generator import generate_world  # noqa: E402
from components.worldgen.instance import init_instance  # noqa: E402

JST = timezone(timedelta(hours=9))
MECHANISM = {"mechanism": "stay_at_place"}
REST_TITLE = "在窗边歇一会儿"
READ_TITLE = "在书店读一本书"


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def _catalog(places: dict[str, list[dict]], *, status: str = "OPEN") -> dict:
    return {
        "status": "available",
        "action_grant": False,
        "opportunities": [
            {
                "location_id": place,
                "availability": {"status": status, "reason_code": "WITHIN_OPEN_HOURS"},
                "affordances": [dict(item) for item in affordances],
            }
            for place, affordances in places.items()
        ],
    }


def _observation(place: str, stamp: str) -> dict:
    return {"location": {"place_id": place}, "world_revision": 1,
            "world_id": "t", "observed_at": stamp}


def _offer(catalog: dict, place: str, moment: datetime):
    stamp = _iso(moment)
    offers = wmos.observed_stay_opportunities(
        _observation(place, stamp), catalog=catalog,
        occurred_at=stamp, observed_at=stamp)
    return offers, wmos.opportunity_view(offers, catalog)


def _live_context(observation: dict, catalog: dict, view: list[dict]) -> dict:
    provider = SimpleNamespace(
        last_observation=observation,
        last_catalog=catalog,
        last_opportunity_view=view,
    )
    return llcv.read_live_context(provider, limit=8,
                                  observed_at=observation.get("observed_at"))


def _rules_doc(affordances: list[dict]) -> dict:
    return {
        "schema_version": schema.RULES_SCHEMA,
        "location_rules": {
            "home": {
                "open_hour": None,
                "close_hour": None,
                "weekdays_only": False,
                "affordances": affordances,
            }
        },
        "weather_candidates": [{"condition": "clear", "description": "晴"}],
    }


class _ScriptTransport:
    """最外层 transport 替身：记录正式 client 真正收到的 user JSON。"""

    backend = "scripted-transport"
    provider = "scripted"
    model = "scripted"
    send_split_capable = False

    def __init__(self) -> None:
        self.prompts: list[dict[str, str]] = []

    def complete(self, *, system_prompt: str, user_prompt: str,
                 timeout_s: float | None = None,
                 max_output_tokens: int | None = None) -> ModelCallResult:
        self.prompts.append({"system": system_prompt, "user": user_prompt})
        received = json.loads(user_prompt)
        allowed = [str(code) for code in (received.get("allowed_reason_codes") or [])]
        answer = {"decision": "NO_ACTION", "selected_candidate_id": None,
                  "reason_codes": allowed[:1], "short_rationale": "scripted",
                  "deferred_candidate_ids": []}
        return ModelCallResult(purpose="agency_cognition", provider=self.provider,
                               model=self.model, text=json.dumps(answer, ensure_ascii=False),
                               latency_ms=1, status=200, usage_source="none")


class _FakeClient:
    """正式 provider 的客户端替身：同样的 status/snapshot/available 形状，不开任何 socket。"""

    def __init__(self, snapshot: dict, available: dict | None = None) -> None:
        self._snapshot = snapshot
        self._available = available or {"actions": []}

    def get_status(self):
        return SimpleNamespace(outcome=wbc.OK, data={"ready": True})

    def get_snapshot(self):
        return SimpleNamespace(outcome=wbc.OK, data=dict(self._snapshot))

    def get_available_actions(self):
        return SimpleNamespace(outcome=wbc.OK, data=dict(self._available))


class _FakeIntent:
    """只提供 provider 真正会用的两个 action-space 方法。"""

    def build_action_space(self, snapshot, available_actions=None):
        return {"candidates": []}

    def redact_action_space_for_selector(self, space):
        return space


class StrictActivityDefinitionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(tempfile.mkdtemp(prefix="strict-activity-definition-"))
        cls.package = cls.root / "package"
        cls.docs = generate_world(seed=929711, theme="coastal_town")
        schema.write_package(cls.package, cls.docs)
        cls.home = cls.root / "world-home"
        init_instance(cls.package, cls.home)
        cls.environment = {
            "CHIYO_WORLD_HOME": str(cls.home),
            "HERMES_HOME": str(cls.home),
            "CHIYO_WORLD_PACKAGE": str(cls.package),
        }
        cls.moment = datetime(2026, 11, 7, 13, 0, tzinfo=JST)
        cls.stamp = _iso(cls.moment)
        cls.catalog = wmos.read_location_catalog(
            cls.environment, cls.moment, hermes_home=str(cls.home))
        cls.home_observation = _observation("home", cls.stamp)
        cls.book_observation = _observation("bookstore", cls.stamp)

    def _provider(self, place: str, catalog: dict):
        """正式 provider 入口：一次真实 observe()，catalog 由调用方给定（可未校验）。"""

        snapshot = {
            "location": {"place_id": place},
            "world_revision": 1,
            "world_id": "t",
            "observed_at": self.stamp,
        }
        return wmos.WorldMoveOpportunityProvider(
            client=_FakeClient(snapshot),
            action_intent=_FakeIntent(),
            catalog_reader=lambda environment, now, hermes_home=None: catalog,
            environment=None,
            world_home=None,
            now=lambda: self.moment,
            observed_now=lambda: self.moment,
            valid_for_seconds=300,
        )

    # -- 1. 正式生成 → 定义解析 → 现有候选 → 实际 transport ------------------

    def test_generated_definitions_reach_candidates_and_real_transport(self):
        rules = self.docs["rules.json"]["location_rules"]
        home_rest = next(item for item in rules["home"]["affordances"]
                         if item["affordance_id"] == "REST")
        book_read = next(item for item in rules["bookstore"]["affordances"]
                         if item["affordance_id"] == "READ_HERE")
        self.assertEqual(home_rest["execution"], MECHANISM)
        self.assertEqual(book_read["execution"], MECHANISM)
        self.assertEqual(home_rest["title"], REST_TITLE)
        self.assertEqual(book_read["title"], READ_TITLE)

        home_entry = next(item for item in self.catalog["opportunities"]
                          if item["location_id"] == "home")
        home_declared = next(item for item in home_entry["affordances"]
                             if item["affordance_id"] == "REST")
        self.assertEqual(home_declared["execution"], MECHANISM)
        self.assertIs(home_declared["action_grant"], False)
        book_entry = next(item for item in self.catalog["opportunities"]
                          if item["location_id"] == "bookstore")
        book_declared = next(item for item in book_entry["affordances"]
                             if item["affordance_id"] == "READ_HERE")
        self.assertEqual(book_declared["execution"], MECHANISM)

        home_offers, home_view = _offer(self.catalog, "home", self.moment)
        book_offers, book_view = _offer(self.catalog, "bookstore", self.moment)
        home_targets = [offer["target_refs"] for offer in home_offers]
        book_targets = [offer["target_refs"] for offer in book_offers]
        self.assertIn(["stay:home:REST:240"], home_targets)
        self.assertIn(["stay:bookstore:READ_HERE:600"], book_targets)
        home_rest_view = next(item for item in home_view
                              if item["target_ref"] == "stay:home:REST:240")
        book_read_view = next(item for item in book_view
                              if item["target_ref"] == "stay:bookstore:READ_HERE:600")
        self.assertEqual((home_rest_view["title"], home_rest_view["stay_seconds"],
                          home_rest_view["execution"]),
                         (REST_TITLE, 240, MECHANISM))
        self.assertEqual((book_read_view["title"], book_read_view["stay_seconds"],
                          book_read_view["execution"]),
                         (READ_TITLE, 600, MECHANISM))
        self.assertTrue(all("QUIET_STOP" not in target
                            for targets in home_targets + book_targets
                            for target in targets))

        # 现有 AG-0 候选：同一 target、同一份定义信息，没有新增授权字段。
        source = ag0.WorldOpportunityCandidateSource()
        for offer in home_offers + book_offers:
            source.record_observed_opportunity(offer)
        candidates, _snapshot, _meta = source.materialize(observed_at=self.stamp)
        chosen = [cand for cand in candidates
                  if cand.target_refs[0] in {"stay:home:REST:240",
                                             "stay:bookstore:READ_HERE:600"}]
        self.assertEqual(len(chosen), 2, chosen)
        self.assertTrue(all("action_grant" not in cand.to_dict() for cand in chosen))

        live = _live_context(self.home_observation, self.catalog,
                             home_view + book_view)
        live_rows = {row["target_ref"]: row for row in live["opportunities"]}
        self.assertEqual(live_rows["stay:home:REST:240"]["execution"], MECHANISM)
        self.assertEqual(live_rows["stay:bookstore:READ_HERE:600"]["execution"],
                         MECHANISM)
        self.assertEqual(live_rows["stay:home:REST:240"]["label"], REST_TITLE)
        self.assertEqual(live_rows["stay:bookstore:READ_HERE:600"]["label"], READ_TITLE)

        request = SimpleNamespace(
            opportunity_id="opp:strict-definition",
            candidate_set_id="cset:strict-definition",
            policy_version="policy:v1",
            legal_decision_enums=("START", "PAUSE", "ABANDON", "NO_ACTION"),
            allowed_reason_codes=("WORLD_OPPORTUNITY_OBSERVED",),
            current_activity_summary=None,
            capacity_summary={"state": "AVAILABLE"},
            observed_fact_keys=("world.location",),
            candidates=[cand.to_dict() for cand in chosen],
            prior_experiences=[],
            situation_context={"world": live},
            max_output_tokens=1024,
        )
        transport = _ScriptTransport()
        adapter = aca.ProductionAgencyCognitionAdapter(
            client=AgencyModelClient(
                config={"provider": "scripted", "model": "scripted"},
                transport=transport, purpose="agency_cognition"),
            timeout_s=5.0, max_output_tokens=512)
        response = adapter.invoke(request)
        self.assertEqual(len(transport.prompts), 1)
        sent = json.loads(transport.prompts[0]["user"])
        sent_targets = {tuple(cand.get("target_refs") or [])
                        for cand in sent.get("candidates") or []}
        self.assertIn(("stay:home:REST:240",), sent_targets)
        self.assertIn(("stay:bookstore:READ_HERE:600",), sent_targets)
        sent_rows = {row.get("target_ref"): row
                     for row in (sent.get("situation_context") or {})
                     .get("world", {}).get("opportunities") or []}
        self.assertEqual(sent_rows["stay:home:REST:240"]["execution"], MECHANISM)
        self.assertEqual(sent_rows["stay:bookstore:READ_HERE:600"]["execution"],
                         MECHANISM)
        self.assertEqual(sent_rows["stay:home:REST:240"]["duration_seconds"], 240)
        self.assertIn("NO_ACTION", sent.get("legal_decision_enums") or [])
        self.assertEqual(getattr(response, "raw_output", {}).get("decision"),
                         "NO_ACTION")

        # 正式 provider 正控：合法显式定义经真实 observe() 可读，仍不发 grant。
        provider = self._provider("home", self.catalog)
        observed = provider.observe()
        targets = [ref for item in observed for ref in (item.get("target_refs") or [])]
        self.assertIn("stay:home:REST:240", targets)
        self.assertEqual(
            provider.last_stay_definition_status.get("status"), "available")
        self.assertIsNone(provider.last_stay_definition_status.get("reason_code"))
        self.assertNotIn("QUIET_STOP", json.dumps(observed, ensure_ascii=False))

    # -- 2. 显式定义在 generator 与 runtime 两侧都报固定错误码 -----------------

    def test_explicit_execution_errors_at_generator_and_runtime(self):
        template = generator.load_theme("coastal_town")
        cases = (
            ("missing title", {"suggested_seconds": 240,
                               "execution": dict(MECHANISM)},
             activity_definition.INCOMPLETE_ACTIVITY_EXECUTION),
            ("missing seconds", {"title": REST_TITLE,
                                 "execution": dict(MECHANISM)},
             activity_definition.INCOMPLETE_ACTIVITY_EXECUTION),
            ("unsupported activity", {"title": "搬东西", "suggested_seconds": 60,
                                      "execution": dict(MECHANISM)},
             activity_definition.UNSUPPORTED_ACTIVITY_DEFINITION),
            ("unknown mechanism", {"title": REST_TITLE, "suggested_seconds": 240,
                                   "execution": {"mechanism": "fly"}},
             activity_definition.INVALID_ACTIVITY_EXECUTION),
            ("unknown key", {"title": REST_TITLE, "suggested_seconds": 240,
                             "execution": {"mechanism": "stay_at_place",
                                           "weight": 1}},
             activity_definition.INVALID_ACTIVITY_EXECUTION),
            ("null execution", {"title": REST_TITLE, "suggested_seconds": 240,
                                "execution": None},
             activity_definition.INVALID_ACTIVITY_EXECUTION),
        )
        for label, meta, code in cases:
            activity_id = "MOVE" if label == "unsupported activity" else "REST"
            broken = copy.deepcopy(template)
            broken["fixed_slots"][0]["affordances"].append([activity_id, "x", meta])
            with self.subTest(where=label):
                with mock.patch.object(generator, "load_theme", return_value=broken):
                    with self.assertRaises(schema.WorldPackageError) as caught:
                        generate_world(seed=1, theme="coastal_town")
                self.assertIn(code, str(caught.exception))
        # 直接走 schema 入口的 null/空值/错类型同样拒绝。
        for execution in (None, {}, {"mechanism": None}, "stay_at_place"):
            with self.subTest(execution=execution):
                with self.assertRaises(schema.WorldPackageError) as caught:
                    schema.validate_rules_doc(_rules_doc([
                        {"affordance_id": "REST", "description": "歇",
                         "title": REST_TITLE, "suggested_seconds": 240,
                         "execution": execution}]))
                self.assertIn(activity_definition.INVALID_ACTIVITY_EXECUTION,
                              str(caught.exception))

        # runtime 读取路径：手造目录里显式定义不成立时必须报错，不生候选。
        runtime_cases = (
            ({"affordance_id": "REST", "description": "歇", "title": REST_TITLE,
              "execution": dict(MECHANISM)},
             activity_definition.INCOMPLETE_ACTIVITY_EXECUTION),
            ({"affordance_id": "MOVE", "description": "走", "title": "走一走",
              "suggested_seconds": 60, "execution": dict(MECHANISM)},
             activity_definition.UNSUPPORTED_ACTIVITY_DEFINITION),
            ({"affordance_id": "REST", "description": "歇", "title": REST_TITLE,
              "suggested_seconds": 240,
              "execution": {"mechanism": "fly"}},
             activity_definition.INVALID_ACTIVITY_EXECUTION),
        )
        for item, code in runtime_cases:
            catalog = _catalog({"home": [item]})
            with self.subTest(runtime=code):
                with self.assertRaises(wmos.StayDefinitionError) as caught:
                    _offer(catalog, "home", self.moment)
                self.assertEqual(caught.exception.code, code)
                with self.assertRaises(wmos.StayDefinitionError):
                    wmos._stay_descriptors([item])
                status = wmos.stay_definition_status([item])
                self.assertEqual(status, {"status": "error", "reason_code": code,
                                          "definitions": []})
        # 混合：合法 legacy + 未知显式定义，错误不能被悄悄滤掉。
        mixed = [{"affordance_id": "REST", "description": "歇"},
                 {"affordance_id": "MOVE", "description": "走", "title": "走一走",
                  "suggested_seconds": 60, "execution": dict(MECHANISM)}]
        with self.assertRaises(wmos.StayDefinitionError) as caught:
            wmos._stay_descriptors(mixed)
        self.assertEqual(caught.exception.code,
                         activity_definition.UNSUPPORTED_ACTIVITY_DEFINITION)

        # --- 正式 load 入口：落盘世界包加载时就拒坏定义，并给安全结构定位 ---
        home_count = len(
            self.docs["rules.json"]["location_rules"]["home"]["affordances"])
        load_cases = (
            ({"affordance_id": "REST", "description": "歇",
              "suggested_seconds": 240, "execution": dict(MECHANISM)},
             activity_definition.INCOMPLETE_ACTIVITY_EXECUTION, "title"),
            ({"affordance_id": "REST", "description": "歇", "title": REST_TITLE,
              "suggested_seconds": 240, "execution": {"mechanism": "fly"}},
             activity_definition.INVALID_ACTIVITY_EXECUTION, "execution"),
        )
        for item, code, field in load_cases:
            package_dir = Path(tempfile.mkdtemp(
                prefix="strict-bad-package-", dir=str(self.root)))
            schema.write_package(package_dir, copy.deepcopy(self.docs))
            rules = json.loads(
                (package_dir / "rules.json").read_text(encoding="utf-8"))
            rules["location_rules"]["home"]["affordances"].append(dict(item))
            schema.write_json(package_dir / "rules.json", rules)
            schema.write_json(package_dir / "MANIFEST.json",
                              schema.build_manifest(package_dir))
            expected = ("rules.json/location_rules/home/affordances/%d/%s"
                        % (home_count, field))
            with self.subTest(load=code):
                with self.assertRaises(schema.WorldPackageError) as caught:
                    loader.load(package_dir)
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(caught.exception.path, expected)
                self.assertIn(code, str(caught.exception))
                self.assertIn(expected, str(caught.exception))
                self.assertNotIn(REST_TITLE, str(caught.exception))
                # catalog 读取入口不能把坏包静默成 available/空成功，也不能把
                # 同一条定义错误泛化成只有 WORLD_PACKAGE_UNREADABLE。
                bad_env = dict(self.environment,
                               CHIYO_WORLD_PACKAGE=str(package_dir))
                catalog = wmos.read_location_catalog(
                    bad_env, self.moment, hermes_home=str(self.home))
                self.assertEqual(catalog.get("status"), "unavailable")
                self.assertEqual(catalog.get("reason_code"),
                                 "WORLD_PACKAGE_UNREADABLE")
                self.assertEqual(catalog.get("code"), code)
                self.assertEqual(catalog.get("path"), expected)
                self.assertEqual(catalog.get("opportunities"), [])
                catalog_text = json.dumps(catalog, ensure_ascii=False)
                self.assertNotIn(REST_TITLE, catalog_text)
                self.assertNotIn(str(package_dir), catalog_text)
                self.assertNotIn("QUIET_STOP", catalog_text)

                # 正式 provider 的**默认** catalog_reader：不注入替身，同一坏包经
                # 真实 read_location_catalog 向调用方抛同一条定义错误，而不是空列表。
                provider = wmos.WorldMoveOpportunityProvider(
                    client=_FakeClient({
                        "location": {"place_id": "home"},
                        "world_revision": 1,
                        "world_id": "t",
                        "observed_at": self.stamp,
                    }),
                    action_intent=_FakeIntent(),
                    environment=bad_env,
                    world_home=str(self.home),
                    now=lambda: self.moment,
                    observed_now=lambda: self.moment,
                    valid_for_seconds=300,
                )
                self.assertIs(provider.catalog_reader, wmos.read_location_catalog)
                with self.assertRaises(wmos.StayDefinitionError) as provider_caught:
                    provider.observe()
                self.assertEqual(provider_caught.exception.code, code)
                self.assertEqual(provider_caught.exception.path, expected)
                provider_text = str(provider_caught.exception)
                self.assertNotIn(REST_TITLE, provider_text)
                self.assertNotIn(str(package_dir), provider_text)
                self.assertEqual(provider.last_opportunity_view, [])
                self.assertNotIn(
                    "QUIET_STOP",
                    json.dumps(provider.last_stay_definition_status,
                               ensure_ascii=False))

        # --- 正式 provider 入口：拿到未校验 catalog 也向调用方抛错 ---
        provider_cases = (
            # 缺 title
            ({"affordance_id": "REST", "description": "歇",
              "suggested_seconds": 240, "execution": dict(MECHANISM)},
             activity_definition.INCOMPLETE_ACTIVITY_EXECUTION, "title", "OPEN"),
            # 缺 seconds（字段路径必须精确到 suggested_seconds）
            ({"affordance_id": "REST", "description": "歇", "title": REST_TITLE,
              "execution": dict(MECHANISM)},
             activity_definition.INCOMPLETE_ACTIVITY_EXECUTION,
             "suggested_seconds", "OPEN"),
            # 未知 mechanism
            ({"affordance_id": "REST", "description": "歇", "title": REST_TITLE,
              "suggested_seconds": 240, "execution": {"mechanism": "fly"}},
             activity_definition.INVALID_ACTIVITY_EXECUTION, "execution", "OPEN"),
            # 闭店不能掩盖第一层：坏定义仍报定义错误
            ({"affordance_id": "REST", "description": "歇",
              "suggested_seconds": 240, "execution": dict(MECHANISM)},
             activity_definition.INCOMPLETE_ACTIVITY_EXECUTION, "title", "CLOSED"),
        )
        for item, code, field, status in provider_cases:
            catalog = _catalog({"home": [item]}, status=status)
            provider = self._provider("home", catalog)
            expected = ("rules.json/location_rules/home/affordances/0/%s" % field)
            with self.subTest(provider=code, status=status):
                with self.assertRaises(wmos.StayDefinitionError) as caught:
                    provider.observe()
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(caught.exception.path, expected)
                self.assertIn(code, str(caught.exception))
                self.assertIn(expected, str(caught.exception))
                self.assertNotIn(REST_TITLE, str(caught.exception))
        # 混合合法 + 坏显式定义：正式 provider 入口整体报错，不偷偷滤掉。
        mixed_catalog = _catalog({"home": [
            {"affordance_id": "REST", "description": "歇"},
            {"affordance_id": "MOVE", "description": "走", "title": "走一走",
             "suggested_seconds": 60, "execution": dict(MECHANISM)}]})
        with self.assertRaises(wmos.StayDefinitionError) as caught:
            self._provider("home", mixed_catalog).observe()
        self.assertEqual(caught.exception.code,
                         activity_definition.UNSUPPORTED_ACTIVITY_DEFINITION)
        self.assertEqual(
            caught.exception.path,
            "rules.json/location_rules/home/affordances/1/affordance_id")
        # 恶意/非法地点 key 不进定位路径、不回显。
        evil_key = "../../etc/passwd"
        evil_catalog = _catalog({evil_key: [
            {"affordance_id": "REST", "description": "歇",
             "execution": dict(MECHANISM)}]})
        with self.assertRaises(wmos.StayDefinitionError) as caught:
            self._provider(evil_key, evil_catalog).observe()
        self.assertEqual(
            caught.exception.path,
            "rules.json/location_rules/*/affordances/0/title")
        self.assertNotIn(evil_key, str(caught.exception))

    # -- 3. 环境描述 vs 显式定义：只看结构，不看标签 --------------------------

    def test_environment_description_and_execution_claim_are_structurally_different(self):
        # 同一个未知 ID：没有 execution 就是环境描述，零候选、不报错。
        environment_only = {"affordance_id": "MEDITATE", "description": "打坐",
                            "title": "打坐一会儿", "suggested_seconds": 900}
        self.assertEqual(wmos._stay_descriptors([environment_only]), [])
        catalog = _catalog({"home": [environment_only]})
        offers, view = _offer(catalog, "home", self.moment)
        self.assertEqual(offers, [])
        self.assertEqual(view, [])
        self.assertEqual(wmos.stay_definition_status([environment_only]),
                         {"status": "environment_only",
                          "reason_code": "NO_EXECUTABLE_STAY_DEFINITION",
                          "definitions": []})
        self.assertNotIn("QUIET_STOP", json.dumps(offers, ensure_ascii=False))

        # 同一个未知 ID：显式声明 execution 后就是一条不成立的可执行定义。
        claimed = {**environment_only, "execution": dict(MECHANISM)}
        with self.assertRaises(wmos.StayDefinitionError) as caught:
            wmos._stay_descriptors([claimed])
        self.assertEqual(caught.exception.code,
                         activity_definition.UNSUPPORTED_ACTIVITY_DEFINITION)

        # 合法 legacy 与普通环境描述同时存在：只给合法的那条，不因环境项报错。
        self.assertEqual(
            wmos._stay_descriptors([{"affordance_id": "REST", "description": "歇"},
                                    {"affordance_id": "WINDOW", "description": "窗"}],
                                   observed_at=self.stamp),
            [("REST", 300)])

        # 显式写了 QUIET_STOP 也不自动变成停留；不声明 execution 就没有定义。
        self.assertEqual(
            wmos._stay_descriptors([{"affordance_id": "QUIET_STOP",
                                     "description": "待着"}]), [])

        # 第一层与第二层独立（原分层次序错，已修正）：坏定义即使闭店也先报
        # 定义错误，不能被 closed 短路掩盖。
        closed_bad = _catalog({"home": [
            {"affordance_id": "REST", "description": "歇", "title": REST_TITLE,
             "execution": dict(MECHANISM)}]}, status="CLOSED")
        with self.assertRaises(wmos.StayDefinitionError) as caught:
            _offer(closed_bad, "home", self.moment)
        self.assertEqual(caught.exception.code,
                         activity_definition.INCOMPLETE_ACTIVITY_EXECUTION)
        status = wmos.stay_definition_status_for(
            self.home_observation, closed_bad, observed_at=self.stamp)
        self.assertEqual(status["status"], "error")
        self.assertEqual(status["reason_code"],
                         activity_definition.INCOMPLETE_ACTIVITY_EXECUTION)
        self.assertEqual(status["definitions"], [])

        # 合法定义闭店仍由第二层拒绝：零候选、状态 closed、不是定义错误。
        closed_legal = _catalog({"home": [
            {"affordance_id": "REST", "description": "歇"} ]}, status="CLOSED")
        closed_offers, closed_view = _offer(closed_legal, "home", self.moment)
        self.assertEqual(closed_offers, [])
        self.assertEqual(closed_view, [])
        status = wmos.stay_definition_status_for(
            self.home_observation, closed_legal, observed_at=self.stamp)
        self.assertEqual(status["status"], "closed")
        self.assertEqual(status["definitions"], [])

    # -- 4. legacy 绑定、排序与三层边界 ----------------------------------------

    def test_legacy_bindings_and_layer_boundaries_are_unchanged(self):
        # legacy 完整绑定（无 execution）继续按原秒数/偏好排序工作，同地点合法事项全部保留不饿死。
        legacy = _catalog({"home": [
            {"affordance_id": "READ_HERE", "description": "读"},
            {"affordance_id": "REST", "description": "歇"}]})
        offers, view = _offer(legacy, "home", self.moment)
        self.assertEqual([item["target_refs"] for item in offers],
                         [["stay:home:REST:300"], ["stay:home:READ_HERE:600"]])
        self.assertIsNone(view[0]["execution"])
        self.assertIsNone(view[0]["title"])

        # 显式 execution 只覆盖同地点同 descriptor 的展示与建议秒数。
        explicit = _catalog({"home": [
            {"affordance_id": "REST", "description": "歇", "title": REST_TITLE,
             "suggested_seconds": 240, "execution": dict(MECHANISM)}]})
        explicit_offers, explicit_view = _offer(explicit, "home", self.moment)
        self.assertEqual([item["target_refs"] for item in explicit_offers],
                         [["stay:home:REST:240"]])
        self.assertEqual(explicit_view[0]["execution"], MECHANISM)
        self.assertNotIn(REST_TITLE, explicit_offers[0]["target_refs"][0])

        # 第一层通过不等于授权：正式目录与视图都不发 grant，也没有 AG-0 之外的新字段。
        self.assertIs(self.catalog.get("action_grant"), False)
        home_entry = next(item for item in self.catalog["opportunities"]
                          if item["location_id"] == "home")
        self.assertTrue(all(item.get("action_grant") is False
                            for item in home_entry["affordances"]))
        self.assertIs(explicit_view[0]["action_grant"], False)
        self.assertIs(explicit_view[0]["observed"], True)

        # 第二层闭店仍拒绝；不是编译错误，也不产生任何候选。
        closed = _catalog({"home": [
            {"affordance_id": "REST", "description": "歇"}]}, status="CLOSED")
        closed_offers, closed_view = _offer(closed, "home", self.moment)
        self.assertEqual(closed_offers, [])
        self.assertEqual(closed_view, [])
        self.assertEqual(
            wmos.stay_definition_status_for(self.home_observation, closed,
                                            observed_at=self.stamp)["status"],
            "closed")

        # 正式 provider 正控（最少场景）：普通环境描述零候选且 environment_only、
        # 无 QUIET_STOP；合法定义闭店零候选、状态 closed、不报错。
        environment_only = _catalog({"home": [
            {"affordance_id": "MEDITATE", "description": "打坐"}]})
        environment_provider = self._provider("home", environment_only)
        self.assertEqual(environment_provider.observe(), [])
        self.assertEqual(
            environment_provider.last_stay_definition_status.get("status"),
            "environment_only")
        self.assertNotIn(
            "QUIET_STOP",
            json.dumps(environment_provider.last_opportunity_view,
                       ensure_ascii=False))

        closed_provider = self._provider("home", closed)
        self.assertEqual(closed_provider.observe(), [])
        self.assertEqual(
            closed_provider.last_stay_definition_status.get("status"), "closed")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""世界包 affordance 的可选 title / suggested_seconds（有限兼容扩展）。

只证明四件事：

1. 新模板给一个**既有支持**的活动声明 title/秒数后，真正 generator 生成的 scratch world
   经正式 load/validate/catalog/观测，标题与秒数来到同一地点的 Life 候选，并进入
   ``ProductionAgencyCognitionAdapter.invoke`` → 正式 ``AgencyModelClient`` → 最外层 script
   transport 真正收到的 user JSON；同一份 scratch world 再走正式 runtime/cycle 的 AG-1 →
   AG-2，创建出的 canonical Activity 标题就是配置的 title，计划仍是
   ``stay:<place>:<descriptor>:<seconds>``；
2. 旧模板/旧包缺字段时逐个字段回退，描述符、候选身份、默认标题与默认秒数不变；
   不同地点声明同名 descriptor 时元数据互不污染；
3. 显式非法值（null/空串/非整数/bool/范围外/未知键）在 schema 与模板入口被拒绝；
   带元数据的未知 affordance 不会获得新 descriptor 准入，偏好顺序不变；
   2026-11-07 新裁定替换旧兜底：没有可执行停留定义的未知 affordance 不再自动
   变成 QUIET_STOP，只作环境描述、零停留下候选（本文件仅此断言随之更新）；
4. 其它主要消费者（catalog 短视图、life_live_context_view）遇到空/部分字段不崩，
   标题只作纯文本展示，不产生新的执行授权或动作。

需要 POSIX 只因本模块导入真实 Life cycle（其模块链需要 ``fcntl``）；没有任何 socket。
scratch 目录由 ``mkdtemp`` 创建并保留，不删任何东西。
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
    raise unittest.SkipTest("affordance metadata acceptance needs a POSIX host")

import agency_cognition_adapter as aca  # noqa: E402
import autonomous_life_cycle as alc  # noqa: E402
import candidate_sources_ag0 as ag0  # noqa: E402
import life_integration_ag2 as ag2  # noqa: E402
import life_live_context_view as llcv  # noqa: E402
import life_runtime_production as lrp  # noqa: E402
import world_daily_opportunities as wdo  # noqa: E402
import world_move_opportunity_source as wmos  # noqa: E402
from agency_model_client import AgencyModelClient, ModelCallResult  # noqa: E402
from components.worldgen import generator, schema  # noqa: E402
from components.worldgen.generator import generate_world  # noqa: E402
from components.worldgen.instance import init_instance  # noqa: E402

JST = timezone(timedelta(hours=9))
TITLE = "在窗边歇一会儿"
SUGGESTED = 240


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def _catalog(places: dict[str, list[dict]]) -> dict:
    """一份形状真实的最小目录：每个地点 OPEN，affordances 原样列出。"""

    return {
        "status": "available",
        "action_grant": False,
        "opportunities": [
            {
                "location_id": place,
                "availability": {"status": "OPEN", "reason_code": "WITHIN_OPEN_HOURS"},
                "affordances": [dict(item) for item in affordances],
            }
            for place, affordances in places.items()
        ],
    }


def _offer(catalog: dict, place: str, moment: datetime):
    stamp = _iso(moment)
    observation = {"location": {"place_id": place}, "world_revision": 1, "world_id": "t"}
    offers = wmos.observed_stay_opportunities(
        observation, catalog=catalog, occurred_at=stamp, observed_at=stamp)
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
    """最外层 transport 替身：记录正式 client 真正收到的 user JSON，并返回写死的 Decision。

    ``select_target`` 为空时回答 NO_ACTION；给出 target 时只从收到的候选里挑带这个
    target 的那一条，绝不自己编 candidate_id。
    """

    backend = "scripted-transport"
    provider = "scripted"
    model = "scripted"
    send_split_capable = False

    def __init__(self, *, select_target: str | None = None) -> None:
        self.select_target = select_target
        self.prompts: list[dict[str, str]] = []
        self.calls: list[dict[str, object]] = []

    def complete(self, *, system_prompt: str, user_prompt: str,
                 timeout_s: float | None = None,
                 max_output_tokens: int | None = None) -> ModelCallResult:
        self.prompts.append({"system": system_prompt, "user": user_prompt})
        self.calls.append({"timeout_s": float(timeout_s or 0.0),
                           "max_output_tokens": int(max_output_tokens or 0)})
        received = json.loads(user_prompt)
        allowed = [str(code) for code in (received.get("allowed_reason_codes") or [])]
        chosen = None
        if self.select_target:
            for candidate in received.get("candidates") or []:
                if self.select_target in (candidate.get("target_refs") or []):
                    chosen = candidate
                    break
        if chosen is None:
            answer = {"decision": "NO_ACTION", "selected_candidate_id": None,
                      "reason_codes": allowed[:1], "short_rationale": "scripted",
                      "deferred_candidate_ids": []}
        else:
            answer = {"decision": "START",
                      "selected_candidate_id": chosen.get("candidate_id"),
                      "reason_codes": allowed[:1], "short_rationale": "scripted",
                      "deferred_candidate_ids": []}
        return ModelCallResult(purpose="agency_cognition", provider=self.provider,
                               model=self.model, text=json.dumps(answer, ensure_ascii=False),
                               latency_ms=1, status=200, usage_source="none")


class _ScratchWorldProvider:
    """把同一份生成的 scratch world 观测交给正式 cycle 的既有 seam，不做第二次合成。"""

    def __init__(self, observation: dict, catalog: dict,
                 view: list[dict], offers: list[dict]) -> None:
        self.last_observation = dict(observation)
        self.last_catalog = catalog
        self.last_opportunity_view = view
        self._offers = offers

    def __call__(self) -> list[dict]:
        return [dict(item) for item in self._offers]


class OptionalAffordanceMetadataTest(unittest.TestCase):
    """一个真实生成世界 + 合成目录的负控，四组用例都在这里。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(tempfile.mkdtemp(prefix="affordance-metadata-"))
        cls.package = cls.root / "package"
        # 真实模板（coastal_town）自己声明了 home REST 的 title/240。
        schema.write_package(cls.package, generate_world(seed=929711, theme="coastal_town"))
        cls.home = cls.root / "world-home"
        init_instance(cls.package, cls.home)
        cls.environment = {
            "CHIYO_WORLD_HOME": str(cls.home),
            "HERMES_HOME": str(cls.home),
            "CHIYO_WORLD_PACKAGE": str(cls.package),
        }
        cls.moment = datetime(2026, 11, 7, 13, 0, tzinfo=JST)
        cls.observed_at = _iso(cls.moment)
        cls.catalog = wmos.read_location_catalog(
            cls.environment, cls.moment, hermes_home=str(cls.home))
        cls.observation = {
            "location": {"place_id": "home", "area_id": "bedroom"},
            "world_revision": 1,
            "world_id": cls.catalog.get("world_id") or "coastal_town_929711",
            "observed_at": cls.observed_at,
        }
        cls.offers = wmos.observed_stay_opportunities(
            cls.observation, catalog=cls.catalog,
            occurred_at=cls.observed_at, observed_at=cls.observed_at)
        cls.view = wmos.opportunity_view(cls.offers, cls.catalog)
        cls.live = _live_context(cls.observation, cls.catalog, cls.view)

    # -- 1. 正式生成 → 正式目录/观测 → Life 候选 → adapter 实际选项 -------------

    def test_generated_world_metadata_reaches_the_real_cognition_options(self):
        self.assertEqual(self.catalog["status"], "available", self.catalog)
        self.assertEqual(self.catalog["schedule_source"], "world_package_schedule")
        home_entry = next(
            item for item in self.catalog["opportunities"]
            if item["location_id"] == "home")
        rest_declared = next(
            item for item in home_entry["affordances"]
            if item["affordance_id"] == "REST")
        self.assertEqual(rest_declared["title"], TITLE)
        self.assertEqual(rest_declared["suggested_seconds"], SUGGESTED)
        self.assertIs(rest_declared["action_grant"], False)

        # 观测层：秒数来自同一地点的声明，target 只有 place/descriptor/seconds。
        targets = [offer["target_refs"] for offer in self.offers]
        self.assertIn(["stay:home:REST:240"], targets, targets)
        rest_view = next(
            item for item in self.view if item["target_ref"] == "stay:home:REST:240")
        self.assertEqual(rest_view["title"], TITLE)
        self.assertEqual(rest_view["stay_seconds"], SUGGESTED)
        # 同一地点的 DAZE 没写 title，就不该凭空得到一个。
        idle_view = next(
            item for item in self.view if item["affordance"] == "DAZE")
        self.assertIsNone(idle_view["title"])

        # 正式 Life 候选：AG-0 的 World source 原样接收这次观测。
        source = ag0.WorldOpportunityCandidateSource()
        rest_offer = next(
            offer for offer in self.offers
            if offer["target_refs"] == ["stay:home:REST:240"])
        source.record_observed_opportunity(rest_offer)
        candidates, _snapshot, _meta = source.materialize(observed_at=self.observed_at)
        chosen = next(
            cand for cand in candidates
            if cand.target_refs == ["stay:home:REST:240"])
        self.assertEqual(chosen.candidate_kind, "CONSIDER_WORLD_OPPORTUNITY")
        # 计划一致性：cycle 自己的解析器读回同一个地点/描述符/秒数。
        plan = alc.stay_plan(chosen.target_refs[0])
        self.assertEqual(plan["place"], "home")
        self.assertEqual(plan["descriptor"], "REST")
        self.assertEqual(plan["seconds"], SUGGESTED)

        # 世界里活着的标题进入 live view 的 label，并随候选进到 adapter 实际发出的 payload。
        live_rows = self.live["opportunities"]
        live_rest = next(
            row for row in live_rows if row["target_ref"] == "stay:home:REST:240")
        self.assertEqual(live_rest["label"], TITLE)
        self.assertEqual(live_rest["duration_seconds"], SUGGESTED)

        request = SimpleNamespace(
            opportunity_id="opp:affordance-metadata",
            candidate_set_id="cset:affordance-metadata",
            policy_version="policy:v1",
            legal_decision_enums=("START", "PAUSE", "ABANDON", "NO_ACTION"),
            allowed_reason_codes=("WORLD_OPPORTUNITY_OBSERVED",),
            current_activity_summary=None,
            capacity_summary={"state": "AVAILABLE"},
            observed_fact_keys=("world.location",),
            candidates=[chosen.to_dict()],
            prior_experiences=[],
            situation_context={"world": self.live},
            max_output_tokens=1024,
        )
        # 正式 adapter → 正式 client → 最外层 script transport：断言用的是 transport
        # 真正收到的 user JSON，而不是 build_prompt 的返回值。
        transport = _ScriptTransport()
        adapter = aca.ProductionAgencyCognitionAdapter(
            client=AgencyModelClient(
                config={"provider": "scripted", "model": "scripted"},
                transport=transport, purpose="agency_cognition"),
            timeout_s=5.0, max_output_tokens=512)
        response = adapter.invoke(request)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(adapter.call_count, 1)
        sent = json.loads(transport.prompts[0]["user"])
        candidate_targets = [cand.get("target_refs")
                             for cand in sent.get("candidates") or []]
        self.assertIn(["stay:home:REST:240"], candidate_targets)
        world_rows = (sent.get("situation_context") or {}).get("world", {}).get("opportunities") or []
        self.assertTrue(any(
            row.get("label") == TITLE and row.get("duration_seconds") == SUGGESTED
            for row in world_rows), world_rows)
        self.assertIn("NO_ACTION", sent.get("legal_decision_enums") or [])
        self.assertEqual(getattr(response, "raw_output", {}).get("decision"), "NO_ACTION")

        # 第二个末端：同一份 scratch world 走正式 runtime/cycle 的 AG-1 → AG-2。
        # 只验证正式 adoption 与 canonical Activity 创建，不执行世界副作用，也不冒已完成。
        install = Path(tempfile.mkdtemp(prefix="affordance-life-", dir=str(self.root)))
        os.chmod(install, 0o700)
        state = install / "state"
        state.mkdir(mode=0o700)
        provider = _ScratchWorldProvider(
            self.observation, self.catalog, self.view, self.offers)
        activity_transport = _ScriptTransport(select_target="stay:home:REST:240")
        activity_adapter = aca.ProductionAgencyCognitionAdapter(
            client=AgencyModelClient(
                config={"provider": "scripted", "model": "scripted"},
                transport=activity_transport, purpose="agency_cognition"),
            timeout_s=5.0, max_output_tokens=512)
        runtime = lrp.build_production_life_runtime(
            state,
            kill_switches=ag2.LifeRuntimeKillSwitches(
                life_runtime_enabled=True, agency_enabled=True,
                action_execution_enabled=False, proactive_enabled=False),
            cognition_adapter=activity_adapter, install_root=install)
        try:
            cycle = alc.AutonomousLifeCycle(
                runtime, artifact_port=None, writer=None,
                world_opportunity_provider=provider, enabled=True,
                env=self.environment, world_home=str(self.home),
                max_rounds_per_day=5)
            out = cycle.evaluate_once(
                trigger_ref="evt:affordance-metadata",
                observed_at=self.observed_at, act=True)
            activity = runtime.activity_read.get_activity(out.get("activity_ref"))
        finally:
            runtime.close()
        self.assertEqual(out.get("decision"), "START", out)
        self.assertEqual(out.get("adoption_status"), "APPLIED", out)
        self.assertEqual(len(activity_transport.calls), 1)
        received = json.loads(activity_transport.prompts[0]["user"])
        self.assertTrue(any(
            "stay:home:REST:240" in (cand.get("target_refs") or [])
            for cand in received.get("candidates") or []))
        self.assertIsNotNone(activity, out)
        self.assertEqual(activity.get("title"), TITLE)
        self.assertIn("world_fact:stay:home:REST:240", activity.get("subject_refs"))
        self.assertNotIn(TITLE, json.dumps(activity.get("subject_refs"), ensure_ascii=False))
        executed = alc.stay_plan("stay:home:REST:240")
        self.assertEqual((executed["place"], executed["descriptor"], executed["seconds"]),
                         ("home", "REST", SUGGESTED))

    # -- 2. 旧包缺字段：逐字段回退；同名 descriptor 不跨地点污染 ----------------

    def test_missing_metadata_falls_back_per_field_and_never_crosses_places(self):
        moment = self.moment
        # 旧包：两个字段都缺 → 默认 title（无）/默认秒数 600。
        legacy_cat = _catalog({"legacy": [{"affordance_id": "READ_HERE",
                                           "description": "读"}]})
        legacy_offers, legacy_view = _offer(legacy_cat, "legacy", moment)
        self.assertEqual([o["target_refs"] for o in legacy_offers],
                         [["stay:legacy:READ_HERE:600"]])
        self.assertEqual(legacy_offers[0]["opportunity_id"],
                         "wopp:stay:legacy:r1:READ_HERE")
        self.assertIsNone(legacy_view[0]["title"])
        self.assertNotIn("title", legacy_cat["opportunities"][0]["affordances"][0])

        # 只有 title：秒数回退默认 300，候选身份不变。
        title_cat = _catalog({"home": [{"affordance_id": "REST", "description": "歇",
                                        "title": TITLE}]})
        title_offers, title_view = _offer(title_cat, "home", moment)
        self.assertEqual([o["target_refs"] for o in title_offers],
                         [["stay:home:REST:300"]])
        self.assertEqual(title_view[0]["title"], TITLE)

        # 只有秒数：title 保持缺失（不给未写字段造默认键）。
        seconds_cat = _catalog({"home": [{"affordance_id": "REST", "description": "歇",
                                          "suggested_seconds": 450}]})
        seconds_offers, seconds_view = _offer(seconds_cat, "home", moment)
        self.assertEqual([o["target_refs"] for o in seconds_offers],
                         [["stay:home:REST:450"]])
        self.assertIsNone(seconds_view[0]["title"])

        # 同名 descriptor 的两处地点：A 的元数据只作用于 A。
        pair_cat = _catalog({
            "place_a": [{"affordance_id": "READ_HERE", "description": "读",
                         "title": "A 的读位", "suggested_seconds": 900}],
            "place_b": [{"affordance_id": "READ_HERE", "description": "读"}],
        })
        a_offers, a_view = _offer(pair_cat, "place_a", moment)
        b_offers, b_view = _offer(pair_cat, "place_b", moment)
        self.assertEqual([o["target_refs"] for o in a_offers],
                         [["stay:place_a:READ_HERE:900"]])
        self.assertEqual([o["target_refs"] for o in b_offers],
                         [["stay:place_b:READ_HERE:600"]])
        self.assertEqual(a_view[0]["title"], "A 的读位")
        self.assertIsNone(b_view[0]["title"])
        self.assertNotIn("A 的读位", json.dumps(b_view, ensure_ascii=False))

    # -- 3. 非法值拒绝；未知 ID 不获准入；顺序不变、未知不再 quiet 兜底 ----------

    def test_invalid_values_are_rejected_and_unknown_ids_gain_nothing(self):
        base = {"affordance_id": "REST", "description": "歇"}
        invalid = [
            ({**base, "title": None}, "title"),
            ({**base, "title": ""}, "title"),
            ({**base, "title": "   "}, "title"),
            ({**base, "title": 42}, "title"),
            ({**base, "title": "x" * 81}, "title"),
            ({**base, "title": "上\n下"}, "title"),
            ({**base, "title": "上\t下"}, "title"),
            ({**base, "suggested_seconds": None}, "suggested_seconds"),
            ({**base, "suggested_seconds": True}, "suggested_seconds"),
            ({**base, "suggested_seconds": 240.5}, "suggested_seconds"),
            ({**base, "suggested_seconds": "240"}, "suggested_seconds"),
            ({**base, "suggested_seconds": 0}, "suggested_seconds"),
            ({**base, "suggested_seconds": 86401}, "suggested_seconds"),
            ({**base, "weight": 1}, "只允许"),
            ({"affordance_id": "REST"}, "affordance_id/description"),
        ]
        for affordance, fragment in invalid:
            with self.subTest(affordance=affordance):
                with self.assertRaises(schema.WorldPackageError) as caught:
                    schema.validate_rules_doc(_rules_doc([affordance]))
                self.assertIn(fragment, str(caught.exception))

        # 合法边界值仍然通过：1..86400、title 恰好 80 字符。
        accepted = schema.validate_rules_doc(_rules_doc([
            {**base, "title": "标" * 80, "suggested_seconds": 1},
            {"affordance_id": "READ", "description": "读", "suggested_seconds": 86400},
        ]))
        self.assertEqual(accepted["location_rules"]["home"]["affordances"][0]
                         ["suggested_seconds"], 1)

        # 模板正式入口：第三元素不是对象 / 含未知键都会被 generator 拒绝。
        template = generator.load_theme("coastal_town")
        for bad in (["REST", "x", ["title"]], ["REST", "x", {"weight": 1}]):
            broken = copy.deepcopy(template)
            broken["fixed_slots"][0]["affordances"].append(bad)
            with self.subTest(template_entry=bad):
                with mock.patch.object(generator, "load_theme", return_value=broken):
                    with self.assertRaises(schema.WorldPackageError):
                        generate_world(seed=1, theme="coastal_town")
        # 模板里显式非法值在生成自检时同样被拒绝（不是静默当缺失）。
        broken = copy.deepcopy(template)
        broken["fixed_slots"][0]["affordances"].append(
            ["REST", "x", {"suggested_seconds": 0}])
        with mock.patch.object(generator, "load_theme", return_value=broken):
            with self.assertRaises(schema.WorldPackageError):
                generate_world(seed=1, theme="coastal_town")

        # 带元数据的未知 ID：不获得新 descriptor，也不会挤掉原本支持的活动。
        unknown_with_meta = {"affordance_id": "MEDITATE", "description": "打坐",
                             "title": "打坐一会儿", "suggested_seconds": 900}
        known = {"affordance_id": "REST", "description": "歇"}
        self.assertEqual(
            wmos._stay_descriptors([unknown_with_meta, known]),
            [("REST", 300)])
        # 2026-11-07 新裁定：只有未知 ID、没有显式执行定义时，它是环境描述，
        # 不再自动变成 QUIET_STOP「待一会儿」；零停留下候选、也不报编译错误。
        self.assertEqual(wmos._stay_descriptors([unknown_with_meta]), [])
        # 偏好顺序不变：REST 排在 READ_HERE 前面（即使 READ_HERE 写在列表更前），且同地点的合法事项不再被饿死。
        read_with_meta = {"affordance_id": "READ_HERE", "description": "读",
                          "suggested_seconds": 900}
        self.assertEqual(
            wmos._stay_descriptors([read_with_meta, known], observed_at="2026-11-07T04:00:00Z"),
            [("REST", 300), ("READ_HERE", 900)])
        # 空闲轮换只换 flavor，不因元数据改变选择；元数据只改那一条的秒数。
        rotated = [
            wmos._stay_descriptors(
                [{"affordance_id": "DAZE", "description": "呆",
                  "suggested_seconds": 120},
                 {"affordance_id": "POTTER", "description": "转"}],
                observed_at=datetime(2026, 11, 7, hour, tzinfo=timezone.utc).isoformat())
            for hour in (0, 1, 2)
        ]
        self.assertEqual([row[0][0] for row in rotated], ["DAZE", "POTTER", "DAZE"])
        self.assertEqual(rotated[0], [("DAZE", 120)])

    # -- 4. 其它消费者：短视图兼容、部分形状、纯文本标题、无新授权 -----------------

    def test_other_consumers_tolerate_partial_fields_and_grants_do_not_change(self):
        # 真实目录：根与每个 affordance 仍然 action_grant=False。
        self.assertIs(self.catalog.get("action_grant"), False)
        home_entry = next(
            item for item in self.catalog["opportunities"]
            if item["location_id"] == "home")
        self.assertTrue(all(item.get("action_grant") is False
                            for item in home_entry["affordances"]))

        # 短视图只投影 affordance_id，遇到新字段不崩、也不会把标题当动作。
        compact = wdo.compact_projection(self.catalog)
        compact_home = next(
            item for item in compact["opportunities"] if item["location_id"] == "home")
        self.assertIn("REST", compact_home["affordance_ids"])
        self.assertNotIn("title", compact_home)
        self.assertIs(compact_home["action_grant"], False)

        # 空/部分字段形状：仅 title 时秒数回退；仅秒数时标题缺失；两者都缺时旧标签不变。
        moment = self.moment
        cat = _catalog({
            "home": [{"affordance_id": "REST", "description": "歇", "title": TITLE}],
            "reading": [{"affordance_id": "READ", "description": "读",
                         "suggested_seconds": 450}],
            "library": [{"affordance_id": "READ_HERE", "description": "读"}],
        })
        offers: list[dict] = []
        view: list[dict] = []
        for place in ("home", "reading", "library"):
            place_offers, place_view = _offer(cat, place, moment)
            offers.extend(place_offers)
            view.extend(place_view)
        live = _live_context(
            {"location": {"place_id": "home"}, "world_revision": 1,
             "observed_at": _iso(moment)},
            cat, view)
        rows = {row["target_ref"]: row for row in live["opportunities"]}
        self.assertEqual(rows["stay:home:REST:300"]["label"], TITLE)
        self.assertEqual(rows["stay:reading:READ:450"]["label"], "READ (450)")
        self.assertEqual(rows["stay:library:READ_HERE:600"]["label"],
                         "READ_HERE (600)")

        # 标题里的尖括号只是纯文本，不会被当成执行语义；payload 仍是合法 JSON。
        html_cat = _catalog({"home": [
            {"affordance_id": "REST", "description": "歇",
             "title": "<b>在窗边</b>", "suggested_seconds": 240}]})
        html_offers, html_view = _offer(html_cat, "home", moment)
        html_live = _live_context(
            {"location": {"place_id": "home"}, "world_revision": 1,
             "observed_at": _iso(moment)},
            html_cat, html_view)
        html_label = html_live["opportunities"][0]["label"]
        self.assertEqual(html_label, "<b>在窗边</b>")
        self.assertEqual(
            json.loads(json.dumps(html_live, ensure_ascii=False))["opportunities"][0]
            ["label"], "<b>在窗边</b>")
        # 目标仍只有计划本身，标题不进身份。
        self.assertEqual([o["target_refs"] for o in html_offers],
                         [["stay:home:REST:240"]])
        self.assertNotIn("在窗边", html_offers[0]["target_refs"][0])

        # 同一批候选进 adapter：原许可动作与 NO_ACTION 都还在，没有新增执行入口。
        source = ag0.WorldOpportunityCandidateSource()
        for offer in offers:
            source.record_observed_opportunity(offer)
        candidates, _snapshot, _meta = source.materialize(observed_at=_iso(moment))
        request = SimpleNamespace(
            opportunity_id="opp:partial", candidate_set_id="cset:partial",
            policy_version="policy:v1",
            legal_decision_enums=("START", "PAUSE", "ABANDON", "NO_ACTION"),
            allowed_reason_codes=("WORLD_OPPORTUNITY_OBSERVED",),
            current_activity_summary=None,
            capacity_summary={"state": "AVAILABLE"},
            observed_fact_keys=("world.location",),
            candidates=[cand.to_dict() for cand in candidates],
            prior_experiences=[],
            situation_context={"world": live},
            max_output_tokens=1024,
        )
        _text, payload = aca.ProductionAgencyCognitionAdapter(
            client=SimpleNamespace(provider="stub", model="stub-model")).build_prompt(request)
        self.assertIn("NO_ACTION", payload["legal_decision_enums"])
        self.assertIn("START", payload["legal_decision_enums"])
        for row in payload["candidates"]:
            self.assertEqual(row["target_refs"][0][:5], "stay:")
            self.assertNotIn("action_grant", row)
        self.assertEqual(
            {row["target_refs"][0] for row in payload["candidates"]},
            {"stay:home:REST:300", "stay:reading:READ:450",
             "stay:library:READ_HERE:600"})


if __name__ == "__main__":
    unittest.main()

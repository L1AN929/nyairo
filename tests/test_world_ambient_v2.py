"""世界包 ambient v2（显式版本 + 有限跨天事件）的离线定向测试。

只覆盖本格式与投影行为，不替代全库/World 全套：

* v2 显式版本正控；未知版本/缺版本明确拒绝，不猜、不降级；
* v1 文档不能携带 kind/finite 等跨天字段；v2 条目必须显式 kind；
* 有限事件必须有固定 identity、明确起止、世界日期阶段与结束原因；
* 同一身份日 1/日 2 连续、阶段可变、跨午夜窗口正确；
* 期限后（+8/+16 天、远未来）不复活，未来尚未开始不提前投影；
* 重建 World 读取上下文（清缓存）后身份稳定；
* 每日上限、地点视野与事件字段边界不变；
* 旧读端（只认 v1）遇到 v2 明确拒绝，不做静默降级；
* v2 包 16 单日 + 8 有限跨天，目录 20–30，事件不含动作/后果权威字段。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
for entry in (
    ROOT,
    ROOT / "components",
    ROOT / "components" / "world" / "scripts",
):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

PACKAGE = ROOT / "examples" / "worlds" / "daily_neighborhood_v1"
TOKYO = ZoneInfo("Asia/Tokyo")
UTC = ZoneInfo("UTC")


def _digest_tree(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _legacy_v1_reader(doc: dict) -> dict:
    """模拟“旧读端”：只认 v1，遇到 v2 明确拒绝，不自动降级。"""

    if not isinstance(doc, dict) or doc.get("schema_version") != "worldgen.ambient.v1":
        raise ValueError("旧读端只支持 worldgen.ambient.v1，拒绝未知版本")
    from worldgen import schema

    return schema.validate_ambient_doc(doc)


class AmbientV2SchemaTest(unittest.TestCase):
    """格式层：版本分派、字段严格性与有限事件约束。"""

    def setUp(self) -> None:
        from worldgen import schema

        self.schema = schema
        self.doc = json.loads((PACKAGE / "ambient.json").read_text(encoding="utf-8"))

    def _copy(self) -> dict:
        return json.loads(json.dumps(self.doc))

    def _assert_rejected(self, doc: dict, fragment: str) -> None:
        with self.assertRaises(self.schema.WorldPackageError) as ctx:
            self.schema.validate_ambient_doc(doc)
        self.assertIn(fragment, str(ctx.exception))

    # -- 版本分派 --------------------------------------------------------------

    def test_v2_document_is_accepted_with_explicit_kinds(self) -> None:
        normalized = self.schema.validate_ambient_doc(self._copy())
        self.assertEqual(normalized["schema_version"], self.schema.AMBIENT_SCHEMA_V2)
        self.assertEqual(len(normalized["templates"]), 24)
        kinds = [item["kind"] for item in normalized["templates"]]
        self.assertEqual(kinds.count("single_day"), 16)
        self.assertEqual(kinds.count("finite"), 8)

    def test_unknown_or_missing_version_is_rejected_not_downgraded(self) -> None:
        doc = self._copy()
        doc["schema_version"] = "worldgen.ambient.v9"
        self._assert_rejected(doc, "未知版本拒绝加载")
        doc = self._copy()
        del doc["schema_version"]
        self._assert_rejected(doc, "未知版本拒绝加载")

    def test_old_v1_reader_rejects_v2_without_downgrade(self) -> None:
        with self.assertRaises(ValueError):
            _legacy_v1_reader(self._copy())
        # 旧读端仍能读 v1。
        v1 = {
            "schema_version": "worldgen.ambient.v1",
            "templates": [
                {
                    "event_type": "legacy_event",
                    "location_id": "home",
                    "summary": "旧模板",
                    "start_hour": 8,
                    "end_hour": 18,
                }
            ],
        }
        self.assertEqual(_legacy_v1_reader(v1)["templates"][0]["event_type"], "legacy_event")

    # -- v1 不被新字段吞掉 ----------------------------------------------------

    def test_v1_cannot_carry_kind_or_finite(self) -> None:
        doc = {
            "schema_version": "worldgen.ambient.v1",
            "templates": [
                {
                    "kind": "finite",
                    "event_type": "legacy_event",
                    "location_id": "home",
                    "summary": "旧模板",
                    "start_hour": 8,
                    "end_hour": 18,
                    "finite": {},
                }
            ],
        }
        self._assert_rejected(doc, "event_type/location_id/summary/start_hour/end_hour")
        doc = {
            "schema_version": "worldgen.ambient.v1",
            "templates": [
                {
                    "event_type": "legacy_event",
                    "location_id": "home",
                    "summary": "旧模板",
                    "start_hour": 8,
                    "end_hour": 18,
                    "phases": [],
                    "period_days": 8,
                }
            ],
        }
        self._assert_rejected(doc, "event_type/location_id/summary/start_hour/end_hour")

    def test_v1_template_limit_is_unchanged(self) -> None:
        template = {
            "event_type": "legacy_event",
            "location_id": "home",
            "summary": "旧模板",
            "start_hour": 8,
            "end_hour": 18,
        }
        doc = {
            "schema_version": "worldgen.ambient.v1",
            "templates": [
                {**template, "event_type": f"legacy_event_{index}"}
                for index in range(17)
            ],
        }
        self._assert_rejected(doc, "数量不能超过 16")

    # -- v2 条目严格性 --------------------------------------------------------

    def test_v2_entry_requires_explicit_kind(self) -> None:
        doc = self._copy()
        entry = dict(doc["templates"][0])
        entry.pop("kind")
        doc["templates"][0] = entry
        self._assert_rejected(doc, "kind 必须是 single_day 或 finite")

    def test_v2_rejects_unknown_kind_and_mixed_fields(self) -> None:
        doc = self._copy()
        doc["templates"][0]["kind"] = "periodic"
        self._assert_rejected(doc, "kind 必须是 single_day 或 finite")
        doc = self._copy()
        doc["templates"][0]["finite"] = {
            "identity": "x",
            "starts_on": "2026-10-24",
            "ends_on": "2026-10-25",
            "end_reason": "结束",
            "phases": [],
        }
        self._assert_rejected(doc, "single_day 必须包含")

    def test_v2_template_limit(self) -> None:
        doc = self._copy()
        template = {
            "kind": "single_day",
            "event_type": "",
            "location_id": "home",
            "summary": "模板",
            "start_hour": 8,
            "end_hour": 18,
        }
        doc["templates"] = [
            {**template, "event_type": f"single_{index}"} for index in range(25)
        ]
        self._assert_rejected(doc, "数量不能超过 24")

    def test_finite_identity_must_be_unique(self) -> None:
        doc = self._copy()
        finite = next(item for item in doc["templates"] if item["kind"] == "finite")
        duplicate = json.loads(json.dumps(finite))
        duplicate["event_type"] = "finite_duplicate"
        index = next(
            index
            for index, item in enumerate(doc["templates"])
            if item["kind"] == "single_day"
        )
        doc["templates"][index] = duplicate
        self._assert_rejected(doc, "identity 重复")

    def test_finite_identity_format_is_bounded(self) -> None:
        doc = self._copy()
        finite = next(item for item in doc["templates"] if item["kind"] == "finite")
        finite["finite"]["identity"] = "bad identity"
        self._assert_rejected(doc, "finite.identity")

    def test_finite_dates_must_be_valid_and_ordered(self) -> None:
        doc = self._copy()
        finite = next(item for item in doc["templates"] if item["kind"] == "finite")
        finite["finite"]["starts_on"] = "2026-10-26"
        finite["finite"]["ends_on"] = "2026-10-25"
        self._assert_rejected(doc, "起始日期晚于结束日期")
        doc = self._copy()
        finite = next(item for item in doc["templates"] if item["kind"] == "finite")
        finite["finite"]["starts_on"] = "2026-02-30"
        self._assert_rejected(doc, "日期范围无效")

    def test_finite_phase_dates_must_be_in_range_and_ordered(self) -> None:
        doc = self._copy()
        finite = next(item for item in doc["templates"] if item["kind"] == "finite")
        finite["finite"]["phases"][0]["local_date"] = "2026-10-23"
        self._assert_rejected(doc, "超出事件起止范围")
        doc = self._copy()
        finite = next(item for item in doc["templates"] if item["kind"] == "finite")
        finite["finite"]["phases"].reverse()
        self._assert_rejected(doc, "必须按日期严格递增")
        doc = self._copy()
        finite = next(item for item in doc["templates"] if item["kind"] == "finite")
        finite["finite"]["phases"] = []
        self._assert_rejected(doc, "phases 必须是非空列表")

    def test_finite_end_reason_is_required(self) -> None:
        doc = self._copy()
        finite = next(item for item in doc["templates"] if item["kind"] == "finite")
        finite["finite"]["end_reason"] = "   "
        self._assert_rejected(doc, "end_reason 不能为空")

    def test_timezone_must_be_valid_iana(self) -> None:
        doc = self._copy()
        doc["timezone"] = "Not/AZone"
        self._assert_rejected(doc, "不是有效 IANA 时区")
        doc = self._copy()
        doc["timezone"] = "Asia/Tokyo"
        normalized = self.schema.validate_ambient_doc(doc)
        self.assertEqual(normalized["timezone"], "Asia/Tokyo")

    def test_timezone_cannot_disagree_with_world_doc(self) -> None:
        from worldgen import loader, schema

        tmp = Path(tempfile.mkdtemp(prefix="nyairo-ambient-v2-tz-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        package = tmp / "package"
        shutil.copytree(PACKAGE, package)
        doc = self._copy()
        doc["timezone"] = "UTC"
        schema.write_json(package / "ambient.json", doc)
        schema.write_json(package / "MANIFEST.json", schema.build_manifest(package))
        with self.assertRaises(schema.WorldPackageError) as ctx:
            loader.load(package)
        self.assertIn("不一致", str(ctx.exception))


class AmbientV2RuntimeTest(unittest.TestCase):
    """运行时：真 v2 包的有限跨天投影与读取稳定性。"""

    def setUp(self) -> None:
        import world_ambient_events as wae
        import world_runtime_data as wrd

        self.wae = wae
        self.wrd = wrd
        self.wrd.reset_cache()
        self.addCleanup(self.wrd.reset_cache)
        self.env = {"CHIYO_WORLD_PACKAGE": str(PACKAGE)}

    def _events(self, day: date) -> list[dict]:
        return self.wae.materialize_ambient_events(day, environment=self.env)

    def _finite_ids(self, day: date) -> set[str]:
        return {
            event["event_id"]
            for event in self._events(day)
            if event["provenance"] == self.wae.FINITE_PROVENANCE
        }

    def test_catalog_bound_and_locations(self) -> None:
        catalog = self.wae.ambient_template_catalog(self.env)
        self.assertTrue(20 <= catalog["total"] <= 30)
        self.assertEqual(catalog["total"], 24)
        self.assertEqual(catalog["categories"]["cross_day"], 8)
        self.assertEqual(catalog["max_events_per_day"], 3)
        world = self.wrd.active_world(self.env)
        for offset in range(10):
            day = date(2026, 10, 24) + timedelta(days=offset)
            events = self._events(day)
            self.assertLessEqual(len(events), catalog["max_events_per_day"])
            for event in events:
                self.assertIn(event["location_id"], world.locations)
                self.assertEqual(set(event), {
                    "event_id",
                    "world_id",
                    "local_date",
                    "location_id",
                    "event_type",
                    "payload",
                    "valid_from",
                    "valid_until",
                    "provenance",
                })
                self.assertEqual(set(event["payload"]), {"summary"})
                self.assertLessEqual(len(event["payload"]["summary"]), 240)
                self.assertEqual(event["local_date"], day.isoformat())
        # 地点视野边界：只投影 relevant 地点，不串到全城。
        moment = datetime(2026, 10, 25, 12, 0, tzinfo=TOKYO)
        filtered = self.wae.active_ambient_events(
            date(2026, 10, 25),
            moment,
            timezone_name="Asia/Tokyo",
            relevant_location_ids={"park"},
            environment=self.env,
        )
        self.assertTrue(filtered)
        for event in filtered:
            self.assertEqual(event["location_id"], "park")

    def test_finite_event_is_stable_across_days_and_reader_restart(self) -> None:
        day = date(2026, 10, 24)
        first = self._events(day)
        finite = [
            event
            for event in first
            if event["provenance"] == self.wae.FINITE_PROVENANCE
        ]
        self.assertEqual(len(finite), 1)
        event = finite[0]
        self.assertEqual(event["event_id"], "ambient-finite-street_front_drainage_20261024")

        next_day = self._events(day + timedelta(days=1))
        continued = [
            item for item in next_day if item["event_id"] == event["event_id"]
        ]
        self.assertEqual(len(continued), 1)
        self.assertNotEqual(
            continued[0]["payload"]["summary"], event["payload"]["summary"]
        )
        self.assertEqual(continued[0]["event_type"], event["event_type"])
        self.assertEqual(continued[0]["location_id"], event["location_id"])

        # 重建 World 读取上下文（模拟进程重启）后同一身份仍稳定。
        before = {item["event_id"]: item for item in self._events(day)}
        self.wrd.reset_cache()
        after = {item["event_id"]: item for item in self._events(day)}
        self.assertEqual(before, after)

    def test_finite_event_expires_and_never_revives(self) -> None:
        day = date(2026, 10, 24)
        event_id = next(iter(self._finite_ids(day)))
        for offset in (2, 8, 16, 40, 365):
            later = self._events(day + timedelta(days=offset))
            self.assertNotIn(event_id, {item["event_id"] for item in later})
        # 未来尚未开始：10-23 没有任何有限事件。
        self.assertEqual(self._finite_ids(date(2026, 10, 23)), set())

    def test_finite_active_window_and_cross_midnight(self) -> None:
        # 内建世界第一条有限事件第一阶段结束于 24 点（跨到次日 00:00）。
        self.wrd.reset_cache()
        env = {}
        day = date(2026, 10, 24)
        event = next(
            item
            for item in self.wae.materialize_ambient_events(day, environment=env)
            if item["provenance"] == self.wae.FINITE_PROVENANCE
        )
        valid_from = datetime.fromisoformat(event["valid_from"])
        valid_until = datetime.fromisoformat(event["valid_until"])
        self.assertEqual(valid_until.date(), date(2026, 10, 25))
        self.assertEqual(valid_until.hour, 0)

        def active(now: datetime) -> set[str]:
            return {
                item["event_id"]
                for item in self.wae.active_ambient_events(
                    day, now, timezone_name="Asia/Tokyo", environment=env
                )
            }

        self.assertNotIn(event["event_id"], active(valid_from - timedelta(minutes=1)))
        self.assertIn(event["event_id"], active(valid_from))
        self.assertIn(
            event["event_id"], active(valid_until - timedelta(minutes=1))
        )
        self.assertNotIn(event["event_id"], active(valid_until))
        # UTC 表达的同一时刻：窗前/窗内判断不依赖墙钟日期。
        self.assertIn(
            event["event_id"],
            active((valid_until - timedelta(minutes=1)).astimezone(UTC)),
        )

    def test_repeated_reads_do_not_write_package(self) -> None:
        before = _digest_tree(PACKAGE)
        for offset in range(5):
            self._events(date(2026, 10, 24) + timedelta(days=offset))
            self.wae.ambient_template_catalog(self.env)
        self.assertEqual(before, _digest_tree(PACKAGE))

    def test_events_carry_no_action_or_commitment_authority(self) -> None:
        forbidden = {
            "action",
            "action_grant",
            "effect",
            "reward",
            "penalty",
            "quest",
            "goal",
            "emotion_effect",
            "price",
            "inventory",
            "promise",
            "message_received",
        }
        for offset in range(10):
            day = date(2026, 10, 24) + timedelta(days=offset)
            for event in self._events(day):
                self.assertFalse(forbidden.intersection(event))
                self.assertFalse(forbidden.intersection(event["payload"]))


if __name__ == "__main__":
    unittest.main()

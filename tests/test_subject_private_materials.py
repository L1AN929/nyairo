"""rc1-C：主体私人材料的「版本敏感处境」——纯读模块，真实 Supply owner，0 模型 0 网络。

这里断言的是**只读事实**，不是行为：
* 无当前版本停点 → 从第 1 页开始；
* 最近一次**显式**停点就是停点（不是最高页）；同版本内回前页有效；
* 旧版本停点 → 不搬到新版（只报 needs_reread + 旧版本页清单）；
* 读尽 → EXHAUSTED、没有下一页，且这是正常结果而不是错误；
* 撤权 / 越界 / 空材料 / owner 不可达 / 未绑定 → 各自一个状态，绝不互相顶替，也绝不补数据；
* 事实键：ASCII、<=48 字符、<=8 个本模块自己的键，与 World 事实合并后 <=24，且不含正文。

``understanding`` 恒 ``UNKNOWN``：页码是位置，不是理解。

需要 POSIX（AF_UNIX）；原生 Windows 自动跳过。
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from test_one_day_life import OneDayTestCase, posix_only  # noqa: E402

import life_artifact_port as lap  # noqa: E402
import one_day_life as odl  # noqa: E402
import private_daily_items as pdi  # noqa: E402
import subject_private_materials as spm  # noqa: E402

READING = ("第一页：房间里的桌子与窗。\n%%PAGE%%\n"
           "第二页：窗外是一条街，街角有树。\n%%PAGE%%\n"
           "第三页：天色渐暗，风从海上过来。\n")
READING_V2 = ("新版第一页：换了一份材料。\n%%PAGE%%\n"
              "新版第二页：这一页是新的。\n")
MATERIAL_SENTINEL = "窗外是一条街"


@posix_only
class ReadingSituationTest(OneDayTestCase):
    """真实 Supply owner 上的一次只读处境读取。"""

    def _prepare(self) -> dict:
        result = self.prepare_instance(reading_title="C 处境材料", reading_content=READING,
                                       create_private_items=True)
        self.assertTrue(result["ok"], result)
        return odl.read_instance(self.instance)

    def _port(self, data: dict) -> lap.LifeArtifactPort:
        return lap.LifeArtifactPort(str(self.services.supply_path), data["subject_id"])

    def _situation(self, port, data: dict) -> dict:
        return spm.read_reading_situation(
            port, reading_artifact_id=data["reading_artifact_id"],
            private_items_artifact_id=data["private_items_artifact_id"])

    def _items(self, port, data: dict) -> pdi.PrivateDailyItems:
        return pdi.PrivateDailyItems(port, data["private_items_artifact_id"],
                                     workspace_id=data["workspace_id"],
                                     grant_id=data["artifact_grant_id"])

    def _source_id(self, data: dict) -> str:
        return "artifact:%s" % str(data["reading_artifact_id"]).split("artifact:", 1)[-1]

    def _commit_material(self, port, data: dict, content: str) -> None:
        head = port.read_head(data["reading_artifact_id"]) or {}
        outcome = port.commit_version(
            artifact_id=data["reading_artifact_id"], expected_head=head.get("version_id"),
            content=content, grant_id=data["artifact_grant_id"],
            operation_key=lap.operation_key_for("test:material", str(head.get("version_id")),
                                                str(len(content))))
        self.assertTrue(outcome.applied, outcome.to_dict())

    # -- the four different "no page" answers -----------------------------------------

    def test_an_unbound_instance_reports_reading_not_bound(self):
        data = self._prepare()
        port = self._port(data)
        nothing = spm.read_reading_situation(port)
        self.assertEqual(nothing["state"], spm.STATE_READING_NOT_BOUND)
        self.assertFalse(nothing["available"])
        self.assertEqual(nothing["understanding"], spm.UNDERSTANDING_UNKNOWN)
        # half a binding is still unbound -- the module never fills the gap by recency
        half = spm.read_reading_situation(port, reading_artifact_id=data["reading_artifact_id"])
        self.assertEqual(half["state"], spm.STATE_READING_NOT_BOUND)
        self.assertEqual(half["material"]["pages"], None)

    def test_a_fresh_material_has_no_stop_and_starts_at_page_one(self):
        data = self._prepare()
        situation = self._situation(self._port(data), data)
        self.assertEqual(situation["state"], spm.STATE_AVAILABLE, situation)
        self.assertTrue(situation["available"])
        self.assertFalse(situation["exhausted"])
        self.assertEqual(situation["material"]["pages"], 3)
        self.assertIsNone(situation["position"]["page"])
        self.assertEqual(situation["position"]["next_page"], 1)
        self.assertEqual(situation["detail"], "NO_STOP_ON_CURRENT_REVISION")
        self.assertEqual(situation["material"]["version_id"],
                         odl.supply_call(self.services.supply_path,
                                         {"op": "read_head", "subject": data["subject_id"],
                                          "artifact_id": data["reading_artifact_id"]}
                                         )["head"]["version_id"])
        self.assertEqual(situation["understanding"], "UNKNOWN")

    def test_the_latest_explicit_stop_wins_over_the_highest_page(self):
        data = self._prepare()
        self.assertTrue(odl.read_page(self.instance, page=3)["ok"])
        self.assertTrue(odl.read_page(self.instance, page=1)["ok"])
        situation = self._situation(self._port(data), data)
        self.assertEqual(situation["state"], spm.STATE_AVAILABLE, situation)
        self.assertEqual(situation["position"]["page"], 1, situation["position"])
        self.assertEqual(situation["position"]["next_page"], 2, situation["position"])
        self.assertEqual(situation["position"]["prior_revision_pages"], [],
                         "an earlier page of the same revision is not a prior revision")
        self.assertFalse(situation["position"]["needs_reread"])

    def test_an_old_revision_stop_is_a_situation_not_a_page(self):
        data = self._prepare()
        for page in (1, 2, 3):
            self.assertTrue(odl.read_page(self.instance, page=page)["ok"])
        port = self._port(data)
        self._commit_material(port, data, READING_V2)
        situation = self._situation(port, data)
        self.assertEqual(situation["state"], spm.STATE_AVAILABLE, situation)
        self.assertIsNone(situation["position"]["page"], situation["position"])
        self.assertEqual(situation["position"]["next_page"], 1, situation["position"])
        self.assertTrue(situation["position"]["needs_reread"])
        self.assertEqual(situation["position"]["needs_reread_reason"],
                         "SOURCE_REVISION_ADVANCED", situation["position"])
        self.assertEqual(situation["position"]["prior_revision_pages"], [1, 2, 3])
        self.assertEqual(situation["material"]["pages"], 2)
        self.assertEqual(situation["material"]["version_number"], 2)

    def test_a_current_stop_is_not_made_stale_by_the_previous_revisions_pages(self):
        """当前版本上的真实停点就是处境：旧版页/旧版锚点照报，但不再让停点永久过期。

        真实缺陷：只要历史旧版页还在，`needs_reread` 就恒为 `True` + `SOURCE_REVISION_ADVANCED`，
        于是「已在新版第 1 页停下」的下一轮认知收到的却是「你还没重看」的矛盾事实。
        """

        data = self._prepare()
        port = self._port(data)
        head = port.read_head(data["reading_artifact_id"]) or {}
        old_revision = str(head["version_id"])
        note = self._items(port, data).add_memo(
            title="旧版的一页", body="旧版第 1 页上想到的东西",
            reading_anchor={"source_id": self._source_id(data),
                            "source_revision": old_revision, "page": 1})
        self.assertTrue(note.applied, note.to_dict())
        for page in (1, 2, 3):
            self.assertTrue(odl.read_page(self.instance, page=page)["ok"])
        self._commit_material(port, data, READING_V2)          # 3 页 -> 2 页
        self.assertTrue(odl.read_page(self.instance, page=1)["ok"])  # 新版上的真实停点

        situation = self._situation(self._port(data), data)
        self.assertEqual(situation["state"], spm.STATE_AVAILABLE, situation)
        self.assertEqual(situation["position"]["page"], 1, situation["position"])
        self.assertEqual(situation["position"]["next_page"], 2, situation["position"])
        self.assertTrue(situation["position"]["on_current_revision"], situation["position"])
        self.assertFalse(situation["position"]["needs_reread"], situation["position"])
        self.assertEqual(situation["position"]["needs_reread_reason"], "",
                         situation["position"])
        # 历史照旧如实报告：旧版页清单与旧版锚点的笔记仍是 stale，只是不再是「当前停点过期」
        self.assertEqual(situation["position"]["prior_revision_pages"], [1, 2, 3])
        self.assertEqual(situation["annotations"]["note"], 1, situation["annotations"])
        self.assertEqual(situation["annotations"]["stale_note"], 1, situation["annotations"])
        facts = spm.reading_fact_keys(situation)
        self.assertIn("reading_needs_reread_false", facts, facts)
        self.assertIn("reading_next_page_2", facts, facts)

    def test_a_stop_that_points_at_another_sources_acquisition_is_not_a_stop(self):
        """停点必须指向**这份材料自己**的 acquisition：只比 revision 会把别家的取得当停点。

        这份文档是刻意手写的（PDI 门面 `_available_acquisition` 不接受跨 source 配对），
        代表的正是旧数据/被改过的文档形状 —— 只读处境不能相信它。
        """

        data = self._prepare()
        port = self._port(data)
        revision = str((port.read_head(data["reading_artifact_id"]) or {})["version_id"])
        doc_head = port.read_head(data["private_items_artifact_id"]) or {}
        document = pdi.decode_document(
            port.read_artifact(data["private_items_artifact_id"])["content"])
        foreign_source = "artifact:another-material"
        acquisition_id, position_id = "acquisition:rc1cforeign0", "position:rc1cforeign00"
        document["items"][acquisition_id] = {
            "item_id": acquisition_id, "item_type": pdi.ITEM_SOURCE_ACQUISITION,
            "acquisition_id": acquisition_id, "source_id": foreign_source,
            "source_revision": revision, "receipt_ref": "read:another-material",
            "grant_ref": None, "obtained": {"pages": [2]}, "excerpt": None,
            "acquired_at": "2026-10-16T06:00:00+00:00", "available": True,
            "unavailable_reason": "", "unavailable_at": None,
        }
        document["items"][position_id] = {
            "item_id": position_id, "item_type": pdi.ITEM_READING_POSITION,
            "position_id": position_id, "source_id": self._source_id(data),
            "source_revision": revision, "position": 2, "acquisition_id": acquisition_id,
            "set_at": "2026-10-16T06:00:00+00:00",
            "understanding": pdi.UNDERSTANDING_UNKNOWN,
        }
        document["order"] = list(document.get("order") or []) + [acquisition_id, position_id]
        document["owner_version_number"] = int(document.get("owner_version_number") or 0) + 1
        document["doc_revision"] = int(document.get("doc_revision") or 0) + 1
        document["updated_at"] = "2026-10-16T06:00:00+00:00"
        written = port.commit_version(
            artifact_id=data["private_items_artifact_id"],
            expected_head=doc_head.get("version_id"),
            content=pdi.encode_document(document), grant_id=data["artifact_grant_id"],
            operation_key=lap.operation_key_for(
                "test:doc:foreign-acquisition", str(doc_head.get("version_id"))))
        self.assertTrue(written.applied, written.to_dict())

        situation = self._situation(self._port(data), data)
        self.assertEqual(situation["state"], spm.STATE_AVAILABLE, situation)
        self.assertIsNone(situation["position"]["page"], situation["position"])
        self.assertEqual(situation["position"]["next_page"], 1, situation["position"])
        self.assertEqual(situation["detail"], "NO_STOP_ON_CURRENT_REVISION")
        self.assertEqual(situation["position"]["prior_revision_pages"], [])
        self.assertFalse(situation["position"]["needs_reread"], situation["position"])

    def test_a_finished_material_is_exhausted_not_an_error(self):
        data = self._prepare()
        for page in (1, 2, 3):
            self.assertTrue(odl.read_page(self.instance, page=page)["ok"])
        situation = self._situation(self._port(data), data)
        self.assertEqual(situation["state"], spm.STATE_EXHAUSTED, situation)
        self.assertTrue(situation["exhausted"])
        self.assertFalse(situation["available"])
        self.assertEqual(situation["position"]["page"], 3, situation["position"])
        self.assertIsNone(situation["position"]["next_page"],
                          "a finished material has no next page -- the last page is not re-read")

    def test_a_revoked_source_is_reported_and_not_reused(self):
        data = self._prepare()
        self.assertTrue(odl.read_page(self.instance, page=1)["ok"])
        port = self._port(data)
        head = port.read_head(data["reading_artifact_id"]) or {}
        items = self._items(port, data)
        revoked = items.mark_source_unavailable(
            source_id=self._source_id(data), source_revision=str(head["version_id"]),
            reason="operator revoked the material for this instance")
        self.assertTrue(revoked.applied, revoked.to_dict())
        situation = self._situation(port, data)
        self.assertEqual(situation["state"], spm.STATE_SOURCE_REVOKED, situation)
        self.assertFalse(situation["available"])
        self.assertIsNone(situation["position"]["next_page"], situation["position"])

    def test_an_out_of_range_stop_is_reported_and_not_repaired(self):
        data = self._prepare()
        self.assertTrue(odl.read_page(self.instance, page=1)["ok"])
        port = self._port(data)
        head = port.read_head(data["reading_artifact_id"]) or {}
        items = self._items(port, data)
        # A stop the document really holds, pointing past the material's own last page.
        saved = items.set_reading_position(source_id=self._source_id(data),
                                           source_revision=str(head["version_id"]),
                                           position=9)
        self.assertTrue(saved.applied, saved.to_dict())
        situation = self._situation(port, data)
        self.assertEqual(situation["state"], spm.STATE_POSITION_OUT_OF_RANGE, situation)
        self.assertFalse(situation["available"])
        self.assertIsNone(situation["position"]["next_page"], situation["position"])
        self.assertEqual(situation["position"]["page"], 9)

    def test_an_unreachable_owner_is_reported_not_guessed(self):
        data = self._prepare()
        broken = lap.LifeArtifactPort("/nonexistent/c-rc1/owner.sock", data["subject_id"],
                                      timeout=1.0)
        situation = self._situation(broken, data)
        self.assertEqual(situation["state"], spm.STATE_OWNER_UNAVAILABLE, situation)
        self.assertFalse(situation["available"])
        self.assertIsNone(situation["material"]["version_id"])
        self.assertIsNone(situation["position"]["next_page"])

    # -- notes: counts, never their text ---------------------------------------------

    def test_the_situation_counts_notes_and_questions_without_their_text(self):
        data = self._prepare()
        self.assertTrue(odl.read_page(self.instance, page=1)["ok"])
        port = self._port(data)
        items = self._items(port, data)
        head = port.read_head(data["reading_artifact_id"]) or {}
        revision = str(head["version_id"])
        note = items.add_memo(title="这一页", body="第 1 页上想到的东西",
                              reading_anchor={"source_id": self._source_id(data),
                                              "source_revision": revision, "page": 1})
        self.assertTrue(note.applied, note.to_dict())
        unclear = items.mark_unclear(source_id=self._source_id(data), source_revision=revision,
                                     note="这一句没读懂", position=1)
        self.assertTrue(unclear.applied, unclear.to_dict())
        situation = self._situation(port, data)
        self.assertEqual(situation["annotations"]["note"], 1, situation["annotations"])
        self.assertEqual(situation["annotations"]["unclear"], 1, situation["annotations"])
        self.assertEqual(situation["annotations"]["stale_note"], 0)
        self.assertNotIn("没读懂", str(situation), "the situation must not carry note text")
        self.assertNotIn("想到的东西", str(situation))

    # -- the fact-key projection ------------------------------------------------------

    def test_the_fact_keys_are_bounded_ascii_and_carry_the_numbers(self):
        data = self._prepare()
        self.assertTrue(odl.read_page(self.instance, page=1)["ok"])
        situation = self._situation(self._port(data), data)
        facts = spm.reading_fact_keys(situation)
        self.assertIn("reading_state_AVAILABLE", facts, facts)
        self.assertIn("reading_page_1", facts, facts)
        self.assertIn("reading_next_page_2", facts, facts)
        self.assertIn("reading_pages_3", facts, facts)
        self.assertIn("reading_exhausted_false", facts, facts)
        self.assertLessEqual(len(facts), spm.MAX_READING_FACT_KEYS)
        for key in facts:
            self.assertTrue(key.isascii(), key)
            self.assertLessEqual(len(key), spm.MAX_FACT_KEY_CHARS, key)
            self.assertTrue(key.replace("_", "").isalnum(), key)
        self.assertNotIn(MATERIAL_SENTINEL, json.dumps(facts, ensure_ascii=False))

    def test_the_merged_facts_stay_inside_the_prompt_budget(self):
        reading = {"reading_state_AVAILABLE": "AVAILABLE", "reading_next_page_2": 2}
        world = {"world_place_%d_availability_OPEN" % index: "OPEN" for index in range(40)}
        merged = spm.merge_decision_facts(world, reading)
        self.assertLessEqual(len(merged), spm.MAX_FACT_KEYS)
        # the private situation is never crowded out by a place dump ...
        self.assertIn("reading_state_AVAILABLE", merged)
        self.assertIn("reading_next_page_2", merged)
        # ... and nothing that could not survive the prompt projection is kept
        for key in merged:
            self.assertTrue(key.isascii(), key)
            self.assertLessEqual(len(key), spm.MAX_FACT_KEY_CHARS, key)
        over_long = {"x" * (spm.MAX_FACT_KEY_CHARS + 1): 1, "not-ascii-键": 2,
                     "world_ok": 3}
        self.assertEqual(sorted(spm.merge_decision_facts(over_long, {})), ["world_ok"])


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""第 46 篇 §五 8 类故障矩阵 & §六 第 1–7 天连续生活验证（v0.8 Wave 4）。

全部驱动真实 owner，零产品大模型调用：

* ``components.world.scripts.v04_neighborhood_living``（状态/分段干燥/行程/账本），
* ``components.phone.v04_neighborhood_view``（严格字段与视角闸门），
* ``v02_v03_daily_loop_bridge``（候选/后果结算/幂等账本），
* 七天线在**同一个**隔离临时实例上跑真实 ``AutonomousLifeCycle`` +
  ``AutonomousLifeResident.tick()``，认知用脚本化 0-LLM 替身。

第一部分 ``TestCh46EightFaultScenarios`` 对齐 §五 8 个故障场景；第二部分
``TestCh46SevenDayContinuousLife`` 对齐 §六 第 1–7 天连续生活。

Run:  python3 tests/test_v08_ch46_seven_day_verification.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "components" / "native" / "integration"),
    str(ROOT / "tests"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import v02_v03_daily_loop_bridge as bridge  # noqa: E402
from components.phone.service import PhoneService  # noqa: E402
from components.phone.v04_neighborhood_view import (  # noqa: E402
    mutate_v04_neighborhood_view,
    read_v04_neighborhood_view,
)
from components.world.scripts import v04_neighborhood_living as engine  # noqa: E402

T0 = "2026-10-15T09:00:00+08:00"


def _iso_add(minutes: int = 0, *, hours: float = 0.0, base: str = T0) -> str:
    delta = timedelta(minutes=minutes, hours=hours)
    return (datetime.fromisoformat(base) + delta).isoformat(timespec="seconds")


def _tree_snapshot(root: Path) -> list[tuple[str, int, int]]:
    rows: list[tuple[str, int, int]] = []
    if not root.exists():
        return rows
    for path in sorted(root.rglob("*")):
        stat = path.lstat()
        rows.append((str(path.relative_to(root)), stat.st_size, stat.st_mtime_ns))
    return rows


class _V04OwnerBase(unittest.TestCase):
    """隔离临时实例 + 真实 v0.4 owner 的最小夹具（零 product LLM）。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-ch46-")
        self.root = Path(self._tmp.name)
        (self.root / "phone").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- real owner helpers -------------------------------------------------------
    def init_neighborhood(self, *, weather: str = "clear", now_iso: str = T0) -> None:
        code, body = mutate_v04_neighborhood_view(
            self.root,
            {"action": "set_local_weather", "weather": weather, "airflow": "medium"},
            actor_side="partner",
            now_iso=now_iso,
        )
        self.assertEqual(code, 200, body)
        self.assertEqual(body["status"], "applied")

    def mutate(self, payload: dict, *, actor_side: str, now_iso: str) -> tuple[int, dict]:
        return mutate_v04_neighborhood_view(
            self.root, payload, actor_side=actor_side, now_iso=now_iso
        )

    def state(self) -> dict:
        state, exists = engine.load_state_readonly(self.root)
        self.assertTrue(exists)
        return state

    def state_bytes(self) -> bytes:
        path = engine.state_file_path(self.root)
        return path.read_bytes() if path.is_file() else b""

    def collect(self, now_iso: str) -> dict:
        return bridge.collect_v02_v03_loop_candidates(self.root, now_iso=now_iso)

    @staticmethod
    def descriptor(collected: dict, source_ref: str) -> dict:
        for desc in collected.get("descriptors", {}).values():
            if desc.get("source_ref") == source_ref:
                return desc
        raise AssertionError(f"missing candidate {source_ref!r}")

    @staticmethod
    def event_for(desc: dict, *, tag: str) -> dict:
        return {
            "actor_side": "life_resident",
            "effect_state": "APPLIED",
            "action_kind": desc.get("v04_action_kind"),
            "v04_action": desc["v04_action"],
            "v04_payload": dict(desc.get("v04_payload") or {}),
            "source_ref": desc["source_ref"],
            "receipt_ref": f"test:{tag}",
            "operation_key": f"test_{tag}",
        }

    def settle(self, event: dict, *, now_iso: str) -> dict:
        return bridge.settle_v02_v03_round_consequences(
            self.root, event, now_iso=now_iso
        )


# ---------------------------------------------------------------------------
# Part 1: 第 46 篇 §五 8 类故障场景
# ---------------------------------------------------------------------------


class TestCh46EightFaultScenarios(_V04OwnerBase):
    def test_01_carried_cloth_survives_mid_leg_roadblock(self) -> None:
        """故障 1：拿到巾后移动受阻，布巾仍在真实携带位置，绝不瞬移回家。"""
        self.init_neighborhood()
        code, body = self.mutate(
            {
                "action": "start_journey",
                "target_place": "street_corner",
                "carry_item_ids": ["wet_cloth_main"],
            },
            actor_side="subject",
            now_iso=T0,
        )
        self.assertEqual(code, 200, body)
        self.assertEqual(body["status"], "applied")
        state = self.state()
        self.assertEqual(state["drying_items"]["wet_cloth_main"]["support_id"], "hand_bag")
        self.assertIn("wet_cloth_main", state["journey"]["carried_item_ids"])
        self.assertEqual(len(state["drying_items"]), 1)

        # 行程走到街角近路中段（+3 分钟）时前方真的被封，+4 分钟推进到现场遭遇。
        code, body = self.mutate(
            {"action": "set_roadblock", "route_id": "open_shortcut", "reason": "临时施工围栏"},
            actor_side="partner",
            now_iso=_iso_add(3),
        )
        self.assertEqual(code, 200, body)
        engine.advance_v04_neighborhood_processes(self.root, now_iso=_iso_add(4))

        state = self.state()
        journey = state["journey"]
        self.assertEqual(journey["state"], "paused_at_node")
        self.assertEqual(journey["halt_reason"], "ROADBLOCK_ENCOUNTERED")
        self.assertEqual(journey["blocked_route_id"], "open_shortcut")
        self.assertIsNone(journey["current_place_id"])  # 中途停下，不是凭空到站
        self.assertGreater(journey["paused_walked_seconds"], 0.0)
        self.assertIn("open_shortcut", journey["discovered_roadblocks"])
        # 布巾仍在随身帆布袋里，随身位置与携带清单同时成立，绝不回 home_rack。
        cloth = state["drying_items"]["wet_cloth_main"]
        self.assertEqual(cloth["support_id"], "hand_bag")
        self.assertIn("wet_cloth_main", journey["carried_item_ids"])
        self.assertLess(cloth["moisture"], 75.0)  # 袋内只是极慢蒸发，没有被别的天气改写
        self.assertEqual(len(state["drying_items"]), 1)

        # 反复只读观察不会把布巾挪走。
        before = self.state_bytes()
        for _ in range(5):
            code, view = read_v04_neighborhood_view(
                self.root, has_subject_capability=True, now_iso=_iso_add(4)
            )
            self.assertEqual(code, 200)
            self.assertEqual(
                view["data"]["drying_items"]["wet_cloth_main"]["support_id"], "hand_bag"
            )
            self.assertTrue(view["data"]["journey"]["routes_catalog"]["open_shortcut"][
                "known_blocked_to_subject"
            ])
        self.assertEqual(self.state_bytes(), before)

    def test_02_hang_committed_but_response_lost_is_replayed_not_duplicated(self) -> None:
        """故障 2：挂巾已提交但响应丢失，同 operation_key 重放恢复且不生成第二条布巾。"""
        self.init_neighborhood()
        payload = {
            "action": "hang_or_collect_cloth",
            "operation": "hang",
            "item_id": "wet_cloth_main",
            "target_support": "yard_line",
            "operation_key": "fault_hang_lost_resp",
        }
        code, first = self.mutate(payload, actor_side="subject", now_iso=T0)
        self.assertEqual(code, 200, first)
        self.assertEqual(first["status"], "applied")
        self.assertFalse(first["already_applied"])
        state = self.state()
        self.assertEqual(state["drying_items"]["wet_cloth_main"]["support_id"], "yard_line")
        self.assertEqual(len(state["drying_items"]), 1)
        revision = state["revision"]
        bytes_after_first = self.state_bytes()

        # 同一 operation_key 的恢复查询：明确 already_applied，原回执逐字保留。
        code, second = self.mutate(payload, actor_side="subject", now_iso=T0)
        self.assertEqual(code, 200, second)
        self.assertEqual(second["status"], "already_applied")
        self.assertTrue(second["already_applied"])
        self.assertEqual(second["result"], first["result"])
        state = self.state()
        self.assertEqual(state["revision"], revision)
        self.assertEqual(len(state["drying_items"]), 1)
        self.assertEqual(state["drying_items"]["wet_cloth_main"]["support_id"], "yard_line")
        self.assertEqual(self.state_bytes(), bytes_after_first)

    def test_03_same_support_never_overwrites_the_first_occupant(self) -> None:
        """故障 3：同挂钩“同时占用”。

        真实引擎的 ``CLOTH_SUPPORTS`` **没有容量字段**（``porch_hook`` 不是容量 1 的槽位），
        所以物理上允许两件各自不同 item_id 的布巾同挂一处，且绝不覆盖先挂者。真正被明确拒绝
        的第二笔提交，是同一 ``operation_key`` 携带不同意图的重放（``409
        OPERATION_KEY_CONFLICT``）。这里两路都核对，不伪造引擎没有的容量语义。
        """
        self.init_neighborhood()
        code, first = self.mutate(
            {
                "action": "hang_or_collect_cloth",
                "operation": "hang",
                "item_id": "wet_cloth_main",
                "target_support": "porch_hook",
                "operation_key": "fault_hook_first",
            },
            actor_side="subject",
            now_iso=T0,
        )
        self.assertEqual(code, 200, first)
        # 第二件布巾进来（独立 operation_key）：引擎照真实语义接住，第一件原样保留。
        code, second = self.mutate(
            {
                "action": "hang_or_collect_cloth",
                "operation": "wet",
                "item_id": "wet_cloth_guest",
                "name": "客用棉巾",
                "target_support": "porch_hook",
            },
            actor_side="subject",
            now_iso=T0,
        )
        self.assertEqual(code, 200, second)
        state = self.state()
        self.assertEqual(state["drying_items"]["wet_cloth_main"]["support_id"], "porch_hook")
        self.assertEqual(state["drying_items"]["wet_cloth_main"]["moisture"], 75.0)
        self.assertEqual(state["drying_items"]["wet_cloth_guest"]["support_id"], "porch_hook")
        self.assertEqual(len(state["drying_items"]), 2)

        # 同一 operation_key + 不同意图的重放：409 冲突，先落账那笔不被覆盖。
        code, conflict = self.mutate(
            {
                "action": "hang_or_collect_cloth",
                "operation": "hang",
                "item_id": "wet_cloth_main",
                "target_support": "yard_line",
                "operation_key": "fault_hook_first",
            },
            actor_side="subject",
            now_iso=T0,
        )
        self.assertEqual(code, 409, conflict)
        self.assertEqual(conflict["reason_code"], "OPERATION_KEY_CONFLICT")
        state = self.state()
        self.assertEqual(state["drying_items"]["wet_cloth_main"]["support_id"], "porch_hook")
        self.assertEqual(state["drying_items"]["wet_cloth_main"]["moisture"], 75.0)

    def test_04_half_dried_then_rain_is_settled_piecewise(self) -> None:
        """故障 4：晾到一半下雨，按真实天气区间分段结算，绝不整段当晴天。"""
        self.init_neighborhood()
        code, body = self.mutate(
            {
                "action": "hang_or_collect_cloth",
                "operation": "hang",
                "item_id": "wet_cloth_main",
                "target_support": "yard_line",
            },
            actor_side="subject",
            now_iso=T0,
        )
        self.assertEqual(code, 200, body)
        engine.advance_v04_neighborhood_processes(self.root, now_iso=_iso_add(hours=1))
        cloth = self.state()["drying_items"]["wet_cloth_main"]
        self.assertEqual(cloth["moisture"], 47.0)  # 75 - 28（露天晴天中速风）
        self.assertGreater(cloth["moisture"], engine.DRY_SLEEP_THRESHOLD)
        self.assertFalse(cloth["sleeping_dry"])

        # 切到雨：旧区间先按晴天结算到此刻，再开新的一段。
        code, body = self.mutate(
            {"action": "set_local_weather", "weather": "rain"},
            actor_side="partner",
            now_iso=_iso_add(hours=1),
        )
        self.assertEqual(code, 200, body)
        engine.advance_v04_neighborhood_processes(self.root, now_iso=_iso_add(hours=2))
        cloth = self.state()["drying_items"]["wet_cloth_main"]
        self.assertEqual(cloth["moisture"], 92.0)  # 47 + 45（露天雨）
        self.assertEqual(engine.moisture_to_band(cloth["moisture"]), "wet")
        self.assertFalse(cloth["sleeping_dry"])
        self.assertIsNone(cloth["dried_at"])

        # 雨停放晴：再从 92 按晴天逐小时往回降，最后才真正干透。
        code, body = self.mutate(
            {"action": "set_local_weather", "weather": "clear"},
            actor_side="partner",
            now_iso=_iso_add(hours=2),
        )
        self.assertEqual(code, 200, body)
        engine.advance_v04_neighborhood_processes(self.root, now_iso=_iso_add(hours=6))
        cloth = self.state()["drying_items"]["wet_cloth_main"]
        self.assertEqual(cloth["moisture"], engine.DRY_SLEEP_THRESHOLD)
        self.assertTrue(cloth["sleeping_dry"])
        self.assertIsNotNone(cloth["dried_at"])
        self.assertGreaterEqual(int(cloth["segment_count"]), 3)

    def test_05_restart_from_disk_restores_partial_work_without_fabrication(self) -> None:
        """故障 5：服务重启后同一目录原样恢复，未执行的新行为绝不被私自补写。"""
        self.init_neighborhood()
        code, body = self.mutate(
            {
                "action": "hang_or_collect_cloth",
                "operation": "hang",
                "item_id": "wet_cloth_main",
                "target_support": "yard_line",
            },
            actor_side="subject",
            now_iso=T0,
        )
        self.assertEqual(code, 200, body)
        engine.advance_v04_neighborhood_processes(self.root, now_iso=_iso_add(hours=1))
        before = self.state()
        bytes_before = self.state_bytes()
        collected_before = {
            d["source_ref"] for d in self.collect(_iso_add(hours=1))["descriptors"].values()
        }

        # 全新的读取路径：状态逐字一致（半干进度、锚点、分段计数、修订号）。
        fresh, exists = engine.load_state_readonly(self.root)
        self.assertTrue(exists)
        self.assertEqual(fresh["revision"], before["revision"])
        self.assertEqual(fresh["drying_items"], before["drying_items"])
        self.assertEqual(fresh["journey"], before["journey"])
        self.assertFalse(fresh["drying_items"]["wet_cloth_main"]["sleeping_dry"])

        # 只读观察不会补写任何未执行的动作。
        for _ in range(3):
            read_v04_neighborhood_view(self.root, has_subject_capability=True,
                                       now_iso=_iso_add(hours=1))
        self.assertEqual(self.state_bytes(), bytes_before)
        collected_after = {
            d["source_ref"] for d in self.collect(_iso_add(hours=1))["descriptors"].values()
        }
        self.assertEqual(collected_after, collected_before)
        # 半干时既不该出现“已干可收”，也不会被补成“已收回 home_rack”。
        self.assertNotIn("v04_courtyard_collect:wet_cloth_main", collected_after)
        self.assertEqual(self.state()["drying_items"]["wet_cloth_main"]["support_id"], "yard_line")

    def test_06_twenty_map_refreshes_are_zero_write_zero_cognition(self) -> None:
        """故障 6：用户频繁刷新地图，目录/状态/修订号/模型调用全部零变化。"""
        self.init_neighborhood()
        code, body = self.mutate(
            {
                "action": "hang_or_collect_cloth",
                "operation": "hang",
                "item_id": "wet_cloth_main",
                "target_support": "yard_line",
            },
            actor_side="subject",
            now_iso=T0,
        )
        self.assertEqual(code, 200, body)
        before_tree = _tree_snapshot(self.root)
        before_rev = self.state()["revision"]
        model_calls = 0
        for i in range(20):
            subject = (i % 2) == 0
            code, view = read_v04_neighborhood_view(
                self.root, has_subject_capability=subject, now_iso=_iso_add(1)
            )
            self.assertEqual(code, 200)
            self.assertEqual(view["llm_calls"], 0)
            model_calls += int(view["llm_calls"])
            collected = self.collect(_iso_add(1))
            self.assertEqual(collected["llm_calls"], 0)
            self.assertTrue(collected["read_only"])
            model_calls += int(collected["llm_calls"])
        self.assertEqual(model_calls, 0)
        self.assertEqual(_tree_snapshot(self.root), before_tree)
        self.assertEqual(self.state()["revision"], before_rev)

    def test_07_revoked_capability_hides_private_draft_and_unshared_booklet(self) -> None:
        """故障 7：观察权限被收回后，私人草稿与未共享小册立刻隐藏，无旧缓存残留。"""
        self.init_neighborhood()
        code, body = self.mutate(
            {
                "action": "craft_paper_or_box",
                "operation": "cut_card",
                "units": 1,
                "card_id": "card_private",
                "title": "只给自己看的卡片",
                "draft_text": "这是她只想自己读到的草稿。",
            },
            actor_side="subject",
            now_iso=T0,
        )
        self.assertEqual(code, 200, body)
        code, body = self.mutate(
            {
                "action": "update_place_booklet",
                "operation": "record_entry",
                "place_id": "yard",
                "quietness_band": "quiet",
                "impression": "院里此刻只有晾衣绳轻轻碰到木长椅的声音。",
                "shared_with_partner": False,
            },
            actor_side="subject",
            now_iso=T0,
        )
        self.assertEqual(code, 200, body)

        code, subject_view = read_v04_neighborhood_view(
            self.root, has_subject_capability=True, now_iso=T0
        )
        self.assertEqual(code, 200)
        card = subject_view["data"]["craft_bench"]["cards_by_id"]["card_private"]
        self.assertEqual(card["draft_text"], "这是她只想自己读到的草稿。")
        self.assertFalse(card["draft_withheld"])
        booklet = subject_view["data"]["public_life"]["place_booklet"]
        self.assertFalse(booklet["withheld"])
        self.assertIn("yard", {e["place_id"] for e in booklet["entries"]})

        code, partner_view = read_v04_neighborhood_view(
            self.root, has_subject_capability=False, now_iso=T0
        )
        self.assertEqual(code, 200)
        card = partner_view["data"]["craft_bench"]["cards_by_id"]["card_private"]
        self.assertIsNone(card["draft_text"])
        self.assertTrue(card["draft_withheld"])
        self.assertTrue(card["withheld"])
        self.assertIsNone(card["inscribed_text"])
        booklet = partner_view["data"]["public_life"]["place_booklet"]
        self.assertTrue(booklet["withheld"])
        self.assertEqual(booklet["entries"], [])
        self.assertIsNone(booklet["title"])

        # 真实 HTTP 路由业务函数同样按视角分级，不依赖任何旧缓存。
        shim = NS(root=self.root, _somatic_now_iso=lambda: T0)
        shim.v04_neighborhood = types.MethodType(PhoneService.v04_neighborhood, shim)
        http_code, http_body = shim.v04_neighborhood(subject_view=False)
        self.assertEqual(http_code, 200)
        self.assertIsNone(
            http_body["craft_bench"]["cards_by_id"]["card_private"]["draft_text"]
        )
        self.assertTrue(http_body["public_life"]["place_booklet"]["withheld"])

    def test_08_duplicate_consequence_delivery_applies_exactly_once(self) -> None:
        """故障 8：世界已提交后重复投递，已是事实不回滚、账本与后果只生效一次。"""
        self.init_neighborhood()
        target = _iso_add(hours=1)
        event = {
            "actor_side": "life_resident",
            "effect_state": "APPLIED",
            "action_kind": "ADVANCE_CLOCK",
            "advance_clock_to_iso": target,
            "receipt_ref": "fault:clock:committed",
            "operation_key": "fault_clock_redelivery",
        }
        first = self.settle(event, now_iso=target)
        self.assertEqual(first["status"], "APPLIED", first)
        self.assertFalse(first["already_applied"])
        state_bytes = self.state_bytes()
        revision = self.state()["revision"]
        ledger_path = self.root / "phone" / bridge.CONSEQUENCE_LEDGER_FILENAME
        self.assertTrue(ledger_path.is_file())
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        self.assertEqual(len(ledger["receipts"]), 1)

        second = self.settle(event, now_iso=target)
        self.assertTrue(second["already_applied"], second)
        self.assertEqual(second["status"], "APPLIED")
        # 世界状态文件逐字未变，修订号不回滚也不二次递增。
        self.assertEqual(self.state_bytes(), state_bytes)
        self.assertEqual(self.state()["revision"], revision)
        ledger_after = json.loads(ledger_path.read_text(encoding="utf-8"))
        self.assertEqual(len(ledger_after["receipts"]), 1)
        self.assertEqual(ledger_after["revision"], ledger["revision"])
        self.assertFalse(any(p.name.startswith(".v04_neighborhood_state")
                             for p in (self.root / "world").iterdir()))


# ---------------------------------------------------------------------------
# Part 2: 第 46 篇 §六 第 1–7 天连续隔离实例（真实 cycle + resident）
# ---------------------------------------------------------------------------

try:  # pragma: no cover - platform gate only
    from test_one_day_life import OneDayTestCase as _OneDayTestCase, StubWriter, posix_only
    import agency_decision_ag1 as ag1
    import autonomous_life_assembly as assembly
    import autonomous_life_cycle as alc
    import autonomous_life_resident as resident
    import autonomous_life_runner as life_runner
    import one_day_life as odl

    _FIXTURE_ERROR = None
except Exception as _exc:  # noqa: BLE001 - native Windows has no fcntl / AF_UNIX
    _FIXTURE_ERROR = "%s: %s" % (type(_exc).__name__, _exc)
    _OneDayTestCase = unittest.TestCase
    posix_only = unittest.skipIf(True, "real owner fixture needs POSIX")

needs_fixture = unittest.skipIf(
    globals().get("_FIXTURE_ERROR") is not None,
    "real owner fixture unavailable: %s" % globals().get("_FIXTURE_ERROR", ""),
)


class _ScriptedCognition:
    """零产品模型的脚本化决策：只在被展示的候选里按前缀挑一个。"""

    def __init__(self, *, decision: str = "START", select_prefix: str = "v04_") -> None:
        self.decision = decision
        self.select_prefix = select_prefix
        self.call_count = 0

    def invoke(self, request, *, schema_retry_index: int = 0):
        self.call_count += 1
        candidates = [dict(c) for c in (getattr(request, "candidates", []) or [])]
        allowed = [str(code) for code in (getattr(request, "allowed_reason_codes", []) or [])]
        chosen = None
        for cand in candidates:
            targets = [str(ref) for ref in (cand.get("target_refs") or [])]
            if any(ref.startswith(self.select_prefix) for ref in targets):
                chosen = cand
                break
        if self.decision == "START" and chosen is not None:
            raw = {
                "decision": ag1.DECISION_START,
                "selected_candidate_id": chosen.get("candidate_id"),
                "reason_codes": allowed[:1],
                "short_rationale": "stub: 顺着街区里这件真实的小事走一小步。",
                "deferred_candidate_ids": [],
            }
        else:
            raw = {
                "decision": ag1.DECISION_NO_ACTION,
                "selected_candidate_id": None,
                "reason_codes": allowed[:1],
                "short_rationale": "stub: 今天先不动这一件。",
                "deferred_candidate_ids": [],
            }
        return ag1.CognitiveDecisionResponse(
            model_run_ref="model_run:stub:%d" % self.call_count,
            model_id="stub:v08-wave4",
            model_version="v08-wave4-stub-v1",
            raw_output=raw,
            latency_ms=1,
        )


@posix_only
@needs_fixture
class TestCh46SevenDayContinuousLife(_OneDayTestCase):
    """第 1–7 天在同一个实例根目录上连续生活（真实 cycle / resident / v0.4 owner）。"""

    def setUp(self) -> None:
        super().setUp()
        self._now = datetime.fromisoformat(T0)
        (Path(self.instance) / "phone").mkdir(parents=True, exist_ok=True)

    def _runtime(self, cognition, env, data):
        """真实 ProductionLifeRuntime + 真实 Supply/World owner（同 CycleTest 装配）。"""
        import life_artifact_port as lap
        import life_integration_ag2 as ag2
        import life_runtime_production as lrp
        import managed_artifact_action_adapter as maaa
        from world_journey_action_adapter import (
            JourneyWorldResolver, RealJourneyWorldActionAdapter,
        )

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
        self._runtimes.append(runtime)
        self.addCleanup(runtime.close)
        return runtime, port

    # -- clock & epoch helpers ----------------------------------------------------
    def _set_clock(self, now_iso: str) -> None:
        self._now = datetime.fromisoformat(now_iso)

    def _iso(self) -> str:
        return self._now.isoformat(timespec="seconds")

    def _instance_root(self) -> Path:
        return Path(self.instance)

    def _v04_state_path(self) -> Path:
        return engine.state_file_path(self._instance_root())

    def _state(self) -> dict:
        state, exists = engine.load_state_readonly(self._instance_root())
        self.assertTrue(exists)
        return state

    def _make_epoch(self, cognition) -> tuple[object, object, object]:
        """Build one real cycle/runner/resident epoch pointing at the same disk dir."""
        data = odl.read_instance(self.instance)
        env = self.instance_env()
        runtime, port = self._runtime(cognition, env, data)
        cycle = alc.AutonomousLifeCycle(
            runtime, artifact_port=port, subject_id=data["subject_id"],
            writer=StubWriter(), enabled=True, env=env,
        )
        runner_obj = life_runner.AutonomousLifeRunner(
            cycle, state_root=odl.life_state_root(self.instance), env=env,
            clock=lambda: self._now, interval_minutes=0,
        )
        ticked = resident.AutonomousLifeResident(
            state_root=odl.life_state_root(self.instance), environment=env, act=True,
            clock=lambda: self._now, sleeper=lambda _seconds: None, monotonic=lambda: 0.0,
        )
        ticked.assembled = {"state": assembly.READY, "runner": runner_obj, "cycle": cycle}
        return ticked, cycle, runtime

    def _tick(self, ticked, cognition, *, decision: str, prefix: str) -> dict:
        cognition.decision = decision
        cognition.select_prefix = prefix
        record = ticked.tick()
        bridge_view = record.get("v02_v03_bridge") or {}
        self.assertEqual(int(bridge_view.get("llm_calls") or 0), 0, bridge_view)
        for settlement in bridge_view.get("settlements") or ():
            self.assertEqual(int(settlement.get("llm_calls") or 0), 0, settlement)
        return record

    def _advance(self, *, now_iso: str) -> dict:
        return engine.advance_v04_neighborhood_processes(
            self._instance_root(), now_iso=now_iso
        )

    def _mutate(self, payload: dict, *, actor_side: str, now_iso: str) -> dict:
        code, body = mutate_v04_neighborhood_view(
            self._instance_root(), payload, actor_side=actor_side, now_iso=now_iso
        )
        self.assertEqual(code, 200, body)
        return body

    def _conserve_ok(self, state: dict) -> bool:
        bench = state["craft_bench"]
        return (
            int(bench["paper_units"]) + int(bench["scrap_units"])
            + int(bench["consumed_in_cards_units"]) + int(bench["consumed_in_box_units"])
        ) == int(bench["total_stocked_units"])

    def test_seven_consecutive_days_on_one_instance(self) -> None:
        self.prepare_instance()
        inst = self._instance_root()
        # 第 1 天清晨：真实邻居写入晴天环境（创建 v0.4 状态，含唯一一条湿布巾）。
        self._set_clock("2026-10-15T09:00:00+08:00")
        self._mutate(
            {"action": "set_local_weather", "weather": "clear", "airflow": "medium"},
            actor_side="partner", now_iso=self._iso(),
        )
        cognition = _ScriptedCognition()
        ticked, cycle, runtime = self._make_epoch(cognition)
        try:
            # ---------------- Day 1: 晴天挂出 → 干透 → 收回 home_rack -------------
            record = self._tick(
                ticked, cognition, decision="START", prefix="v04_courtyard_hang:"
            )
            due = record.get("due") or {}
            self.assertEqual(due.get("decision"), "START", record)
            self.assertEqual(due.get("status"), "ACTED", record)
            self.assertEqual(cognition.call_count, 1)
            bridge_view = record.get("v02_v03_bridge") or {}
            self.assertTrue(bridge_view.get("prepared_before_tick"), bridge_view)
            self.assertTrue(bridge_view.get("settlements"), bridge_view)
            self.assertEqual(bridge_view.get("llm_calls"), 0)
            self.assertTrue(all(s.get("status") == "APPLIED" for s in bridge_view["settlements"]))
            state = self._state()
            self.assertEqual(state["drying_items"]["wet_cloth_main"]["support_id"], "yard_line")
            self.assertEqual(len(state["drying_items"]), 1)

            # 推进晴天时段至干透，再走一轮真实自主轮次回收。
            self._set_clock("2026-10-15T17:00:00+08:00")
            self._advance(now_iso=self._iso())
            cloth = self._state()["drying_items"]["wet_cloth_main"]
            self.assertTrue(cloth["sleeping_dry"])
            self.assertLessEqual(cloth["moisture"], engine.DRY_SLEEP_THRESHOLD)
            record = self._tick(
                ticked, cognition, decision="START", prefix="v04_courtyard_collect:"
            )
            self.assertEqual((record.get("due") or {}).get("status"), "ACTED", record)
            state = self._state()
            self.assertEqual(state["drying_items"]["wet_cloth_main"]["support_id"], "home_rack")
            self.assertEqual(len(state["drying_items"]), 1)
            self.assertTrue(self._conserve_ok(state))

            # ---------------- Day 2: 旧物件延续，转雨后抢收门廊 -------------------
            self._set_clock("2026-10-16T09:00:00+08:00")
            self._mutate(
                {"action": "hang_or_collect_cloth", "operation": "wet",
                 "item_id": "wet_cloth_main", "target_support": "home_rack"},
                actor_side="subject", now_iso=self._iso(),
            )
            # Wave 3 延续的按线索 operation_key：Day 1 已为 ``v04_courtyard_hang:
            # wet_cloth_main`` 落过账；同一线索的第二次真实挂出改走带独立
            # operation_key 的正式 settle 入口（仍是真实 bridge → phone view → v0.4
            # owner），而不是会命中线索级去重的 resident 自带 key。
            collected = bridge.collect_v02_v03_loop_candidates(
                inst, now_iso=self._iso())
            hang_desc = next(
                d for d in collected["descriptors"].values()
                if d.get("source_ref") == "v04_courtyard_hang:wet_cloth_main"
            )
            settled = bridge.settle_v02_v03_round_consequences(
                inst,
                {
                    "actor_side": "life_resident",
                    "effect_state": "APPLIED",
                    "action_kind": hang_desc["v04_action_kind"],
                    "v04_action": hang_desc["v04_action"],
                    "v04_payload": dict(hang_desc["v04_payload"]),
                    "source_ref": hang_desc["source_ref"],
                    "receipt_ref": "day2:hang:independent",
                    "operation_key": "day2_hang_owned_by_owner",
                },
                now_iso=self._iso(),
            )
            self.assertEqual(settled.get("status"), "APPLIED", settled)
            self.assertEqual(int(settled.get("llm_calls") or 0), 0)
            self.assertEqual(
                self._state()["drying_items"]["wet_cloth_main"]["support_id"], "yard_line"
            )
            self._set_clock("2026-10-16T10:00:00+08:00")
            self._mutate(
                {"action": "set_local_weather", "weather": "rain"},
                actor_side="partner", now_iso=self._iso(),
            )
            record = self._tick(
                ticked, cognition, decision="START", prefix="v04_courtyard_cloth:"
            )
            self.assertEqual((record.get("due") or {}).get("status"), "ACTED", record)
            state = self._state()
            self.assertEqual(state["drying_items"]["wet_cloth_main"]["support_id"], "porch_hook")
            self.assertEqual(len(state["drying_items"]), 1)

            # ---------------- Day 3: 无人来访，只让世界时间走 -------------------
            self._set_clock("2026-10-17T09:00:00+08:00")
            self._advance(now_iso=self._iso())
            plant = self._state()["potted_plant"]
            self.assertLessEqual(plant["hydration"], 35.0)
            record = self._tick(
                ticked, cognition, decision="START", prefix="v04_porch_plant:"
            )
            self.assertEqual((record.get("due") or {}).get("status"), "ACTED", record)
            state = self._state()
            self.assertGreater(state["potted_plant"]["hydration"], plant["hydration"])
            self.assertTrue(state["potted_plant"]["last_watered_at"])
            self.assertTrue(self._conserve_ok(state))
            self.assertEqual(len(state["drying_items"]), 1)

            # ---------------- Day 4: 普通打断，暂停且允许不恢复 -----------------
            self._set_clock("2026-10-18T10:30:00+08:00")
            self._mutate(
                {"action": "start_journey", "target_place": "street_corner"},
                actor_side="subject", now_iso=self._iso(),
            )
            self._set_clock("2026-10-18T10:33:00+08:00")
            self._advance(now_iso=self._iso())
            self.assertEqual(self._state()["journey"]["state"], "in_transit")
            self._mutate(
                {"action": "advance_or_turnback_journey", "operation": "pause"},
                actor_side="subject", now_iso=self._iso(),
            )
            self._mutate(
                {"action": "update_place_booklet", "operation": "pause_booklet",
                 "note": "先停一停，等这阵打扰过去。"},
                actor_side="subject", now_iso=self._iso(),
            )
            paused = self._state()
            self.assertEqual(paused["journey"]["state"], "paused_at_node")
            self.assertEqual(paused["journey"]["halt_reason"], "USER_PAUSED")
            self.assertGreater(paused["journey"]["paused_walked_seconds"], 0.0)
            self.assertEqual(paused["public_life"]["place_booklet"]["status"], "paused")

            self._set_clock("2026-10-18T10:40:00+08:00")
            record = self._tick(ticked, cognition, decision="NO_ACTION", prefix="v04_")
            self.assertEqual((record.get("due") or {}).get("decision"), "NO_ACTION", record)
            after = self._state()
            self.assertEqual(after["journey"]["state"], "paused_at_node")
            self.assertEqual(after["journey"]["halt_reason"], "USER_PAUSED")
            self.assertEqual(after["public_life"]["place_booklet"]["status"], "paused")

            # ---------------- Day 5: 受控重启，同一磁盘目录重建新对象 -------------
            snapshot = {
                "revision": after["revision"],
                "places": {pid: p["knowledge_status"] for pid, p in after["places"].items()},
                "craft": {k: after["craft_bench"][k] for k in (
                    "paper_units", "scrap_units", "total_stocked_units",
                    "consumed_in_cards_units", "consumed_in_box_units")},
                "journey": {k: after["journey"][k] for k in (
                    "state", "halt_reason", "paused_walked_seconds", "active_leg_index")},
                "receipts": sorted(after["operation_receipts"]),
                "booklet_status": after["public_life"]["place_booklet"]["status"],
            }
            bytes_before_restart = self._v04_state_path().read_bytes()
            ticked.stop()
            runtime.close()
            self._set_clock("2026-10-19T09:00:00+08:00")
            cognition_b = _ScriptedCognition()
            ticked_b, cycle_b, runtime_b = self._make_epoch(cognition_b)
            try:
                # 仅构造新实例不得改写世界事实。
                self.assertEqual(self._v04_state_path().read_bytes(), bytes_before_restart)
                fresh = self._state()
                self.assertEqual(fresh["revision"], snapshot["revision"])
                self.assertEqual(
                    {pid: p["knowledge_status"] for pid, p in fresh["places"].items()},
                    snapshot["places"],
                )
                self.assertEqual(
                    {k: fresh["craft_bench"][k] for k in snapshot["craft"]},
                    snapshot["craft"],
                )
                self.assertEqual(
                    {k: fresh["journey"][k] for k in snapshot["journey"]},
                    snapshot["journey"],
                )
                self.assertEqual(sorted(fresh["operation_receipts"]), snapshot["receipts"])
                self.assertEqual(
                    fresh["public_life"]["place_booklet"]["status"],
                    snapshot["booklet_status"],
                )
                record = self._tick(ticked_b, cognition_b, decision="NO_ACTION", prefix="v04_")
                self.assertEqual((record.get("due") or {}).get("decision"), "NO_ACTION", record)
                self.assertEqual(self._v04_state_path().read_bytes(), bytes_before_restart)
            except Exception:
                ticked_b.stop()
                raise

            # 第 6–7 天继续沿用重启后的 Epoch B。
            state = self._state()
            # ---------------- Day 6: 封路 + 公共告示，亲历前不知情 --------------
            self._set_clock("2026-10-20T09:00:00+08:00")
            # 昨天暂停的行程先原路折返（不是 resume，也不是强行继续）。
            self._mutate(
                {"action": "advance_or_turnback_journey", "operation": "turn_back"},
                actor_side="subject", now_iso=self._iso(),
            )
            self._set_clock("2026-10-20T09:03:00+08:00")
            self._advance(now_iso=self._iso())
            self.assertIn(self._state()["journey"]["state"], ("at_place", "arrived"))

            self._mutate(
                {"action": "set_roadblock", "route_id": "open_shortcut",
                 "reason": "石板路临时围挡"},
                actor_side="partner", now_iso=self._iso(),
            )
            self._mutate(
                {"action": "post_public_notice", "title": "近路围挡通知",
                 "body": "街角近路今天上午临时围挡，请绕行长廊。",
                 "related_route_id": "open_shortcut", "roadblock_claim": "blocked",
                 "valid_hours": 48},
                actor_side="partner", now_iso=self._iso(),
            )
            code, before_view = read_v04_neighborhood_view(
                inst, has_subject_capability=True, now_iso=self._iso()
            )
            self.assertEqual(code, 200)
            self.assertFalse([
                rb for rb in before_view["data"]["roadblocks"]
                if rb.get("route_id") == "open_shortcut"
            ])
            self.assertFalse(
                before_view["data"]["journey"]["routes_catalog"]["open_shortcut"][
                    "known_blocked_to_subject"]
            )
            notice = before_view["data"]["public_life"]["public_notices"][0]
            self.assertIsNone(notice["body"])
            self.assertTrue(notice["body_withheld_until_read"])

            # 亲自走近：真实遭遇封路 → 折返。
            self._mutate(
                {"action": "start_journey", "target_place": "street_corner"},
                actor_side="subject", now_iso=self._iso(),
            )
            self._set_clock("2026-10-20T09:07:00+08:00")
            self._advance(now_iso=self._iso())
            journey = self._state()["journey"]
            self.assertEqual(journey["halt_reason"], "ROADBLOCK_ENCOUNTERED")
            self.assertEqual(journey["blocked_route_id"], "open_shortcut")
            self.assertIn("open_shortcut", journey["discovered_roadblocks"])
            self._mutate(
                {"action": "advance_or_turnback_journey", "operation": "turn_back"},
                actor_side="subject", now_iso=self._iso(),
            )
            self._set_clock("2026-10-20T09:10:00+08:00")
            self._advance(now_iso=self._iso())
            self.assertIn(self._state()["journey"]["state"], ("at_place", "arrived"))

            # 真正读一次公共告示。
            record = self._tick(
                ticked_b, cognition_b, decision="START", prefix="v04_public_notice:"
            )
            self.assertEqual((record.get("due") or {}).get("status"), "ACTED", record)
            state = self._state()
            notice_row = state["public_life"]["public_notices"][0]
            self.assertTrue(notice_row["read_by_subject"])
            code, after_notice = read_v04_neighborhood_view(
                inst, has_subject_capability=True, now_iso=self._iso()
            )
            self.assertEqual(code, 200)
            shown = after_notice["data"]["public_life"]["public_notices"][0]
            self.assertEqual(shown["body"], "街角近路今天上午临时围挡，请绕行长廊。")
            self.assertFalse(shown["body_withheld_until_read"])

            # ---------------- Day 7: 解除小册暂停，安静收尾 ---------------------
            self._set_clock("2026-10-21T09:00:00+08:00")
            self._mutate(
                {"action": "update_place_booklet", "operation": "resume_booklet"},
                actor_side="subject", now_iso=self._iso(),
            )
            record = self._tick(
                ticked_b, cognition_b, decision="START", prefix="v04_place_booklet:"
            )
            self.assertEqual((record.get("due") or {}).get("status"), "ACTED", record)
            state = self._state()
            booklet = state["public_life"]["place_booklet"]
            self.assertEqual(booklet["status"], "active")
            self.assertTrue(booklet["entries"])
            self.assertFalse(booklet["shared_with_partner"])
            code, partner_view = read_v04_neighborhood_view(
                inst, has_subject_capability=False, now_iso=self._iso()
            )
            self.assertEqual(code, 200)
            self.assertTrue(partner_view["data"]["public_life"]["place_booklet"]["withheld"])

            bytes_before_quiet = self._v04_state_path().read_bytes()
            record = self._tick(ticked_b, cognition_b, decision="NO_ACTION", prefix="v04_")
            self.assertEqual((record.get("due") or {}).get("decision"), "NO_ACTION", record)
            self.assertEqual(self._v04_state_path().read_bytes(), bytes_before_quiet)

            # 七天总账：产品模型 0 次、对象数量守恒、纸张总库存守恒。
            final = self._state()
            self.assertEqual(len(final["drying_items"]), 1)
            self.assertTrue(self._conserve_ok(final))
            self.assertEqual(len(final["public_life"]["bookstore"]["books"]), 3)
            self.assertEqual(len(final["public_life"]["shared_shelf"]["items"]), 2)
            # 认知全部走 0-LLM 脚本替身：产物模型调用 0 次（每次结算已逐条断言
            # ``llm_calls == 0``）。安静的 NO_ACTION 轮次可能根本不触发一次决策，
            # 所以只核对发生过决策的次数在六个 START 轮次与十个 tick 之间。
            self.assertGreaterEqual(cognition.call_count + cognition_b.call_count, 6)
            self.assertLessEqual(cognition.call_count + cognition_b.call_count, 10)
        finally:
            try:
                ticked.stop()
            except Exception:
                pass
            try:
                ticked_b.stop()
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()

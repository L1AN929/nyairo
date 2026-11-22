#!/usr/bin/env python3
"""v0.8 Wave 8: 0.2 / 0.3 / 0.4 七天连续常驻全栈验证（零产品大模型调用）。

这一波是纯粹的跨周期多域连续验证：在**同一个独立隔离临时实例目录**上，把 Wave 5
（0.3 全量日常候选）、Wave 6（0.2 全量 7 类日常候选）、Wave 7（P12 只读叙事 / 有界
Inquiry / Ch.16 22 类衰减矩阵 / Ch.07 §4 SlotQuota & DailyEnvelope）与真实 v0.4 街区
（院子晾晒 / 盆栽 / 告示 / 小册）串成第 1 天到第 7 天的连续日常链，核对：

* 跨模块真状态衔接：0.3 赚到的余额、0.2 的便签 / 计划 / 书签 / 植物、0.4 的晾晒与告示、
  有界探究与共同背景，都落在同一实例的**真实 owner 磁盘状态**上，互不踩踏；
* 受控重启一致：丢弃内存对象、指向同一磁盘目录重建新 ``AutonomousLifeCycle`` /
  ``AutonomousLifeResident``，Day 1–4 的事实逐字保留、零虚构补写；
* 隐私分级：P12 自传体叙事在主体视角可见多天实证事件与转折 / 母题，伴侣视角严格剥离
  未共享私密内容；
* 额度护栏：``LifeRequestBudget`` 的 ``DAYTIME_ACTIVE`` / ``EVENING_WIND_DOWN`` /
  ``NIGHT_REST`` ``SlotQuota`` 与 ``DailyEnvelope`` 账本计数准确、默认预算调用不被破坏；
* 全周期产品 ``llm_calls == 0``：认知走脚本化替身，所有真实 settle 与只读投影逐条回报 0。

两条测试路径：

* ``SevenDayOwnerTruthChainTest``：owner 级、平台无关、秒级，负责逐域精确断言与 Day 7
  只读投影；原生 Windows 亦可跑。
* ``SevenDayResidentContinuityTest``：真实 ``AutonomousLifeCycle`` + ``AutonomousLifeResident``
  （POSIX：真实 Life Supply / World owner socket），第 1–7 天在同一实例上连续 tick，
  Day 5 受控重启并消费成人亲密断点。原生 Windows 自动跳过（无 fcntl / AF_UNIX）。

Run:  python3 -m unittest tests.test_v08_v02_v03_seven_day_resident_verification
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _entry in (
    str(ROOT),
    str(ROOT / "components"),
    str(ROOT / "components" / "life" / "scripts"),
    str(ROOT / "components" / "world" / "scripts"),
    str(ROOT / "components" / "world" / "service"),
    str(ROOT / "components" / "phone"),
    str(ROOT / "components" / "native" / "integration"),
    str(ROOT / "tests"),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import v02_v03_daily_loop_bridge as bridge  # noqa: E402
import v02_common_ground as common_ground  # noqa: E402
from components.life.scripts import life_request_budget as lrb  # noqa: E402
from components.life.scripts import v02_agency_depth_engine as ad_engine  # noqa: E402
from components.life.scripts import v02_recall_projection as recall  # noqa: E402
from components.life.scripts import v02_runtime_governance_migration as rm_engine  # noqa: E402
from components.phone import adult_intimacy_view as intimacy_view  # noqa: E402
from components.phone import home_hearth_view as hearth_view  # noqa: E402
from components.phone import long_companionship_view as longterm_view  # noqa: E402
from components.phone import outing_places_view as outing_view  # noqa: E402
from components.phone import v02_agency_depth_view as ad_view  # noqa: E402
from components.phone import v02_runtime_migration_view as rm_view  # noqa: E402
from components.phone import v03_ecology_view as ecology_view  # noqa: E402
from components.phone import workbench_curio_view as workbench_view  # noqa: E402
from components.phone.v04_neighborhood_view import (  # noqa: E402
    mutate_v04_neighborhood_view,
    read_v04_neighborhood_view,
)
from components.world.scripts import v03_economy_illness_bridge as eco_engine  # noqa: E402
from components.world.scripts import v04_neighborhood_living as v04_engine  # noqa: E402

DAY1 = "2026-11-15T09:00:00+08:00"
DAY1_JOB = "2026-11-15T09:10:00+08:00"
DAY2 = "2026-11-16T09:00:00+08:00"
DAY2_LATE = "2026-11-16T09:30:00+08:00"
DAY3 = "2026-11-17T09:00:00+08:00"
DAY3_ILL = "2026-11-17T10:00:00+08:00"
DAY4 = "2026-11-18T09:00:00+08:00"
DAY5 = "2026-11-19T09:00:00+08:00"
DAY6 = "2026-11-20T09:00:00+08:00"
DAY7 = "2026-11-21T09:00:00+08:00"
INTIMACY_AT = datetime.fromisoformat(DAY4)

ECO_SCHEMA = "v03.ecology.bridge.v1"


# ---------------------------------------------------------------------------
# Shared helpers (operate on any instance root; zero product-model calls)
# ---------------------------------------------------------------------------


def _eco_doc(*, phase: str = "healthy", severity: float = 0.0) -> dict:
    return {
        "schema": ECO_SCHEMA,
        "schema_version": 1,
        "revision": 0,
        "illness": {
            "phase": phase,
            "severity": severity,
            "strain_load": 0.5 if phase != "healthy" else 0.0,
            "rest_active": False,
        },
    }


def _read_eco_state(root: Path) -> dict:
    state, reason = eco_engine.read_stored_ecology_zero_write(root)
    assert reason is None, reason
    return state


def _economy(root: Path, now_iso: str) -> dict:
    proj = eco_engine.project_v03_ecology(
        root, has_subject_capability=True, now_iso=now_iso
    )
    return dict(proj["data"]["economy"])


def _illness(root: Path, now_iso: str) -> dict:
    proj = eco_engine.project_v03_ecology(
        root, has_subject_capability=True, now_iso=now_iso
    )
    return dict(proj["data"]["illness"])


def _balance(root: Path, now_iso: str) -> int:
    return int(_economy(root, now_iso)["available_balance_cents"])


def _collect(root: Path, now_iso: str) -> dict:
    return bridge.collect_v02_v03_loop_candidates(root, now_iso=now_iso)


def _descriptor(collected: dict, source_ref: str) -> dict:
    for desc in collected.get("descriptors", {}).values():
        if desc.get("source_ref") == source_ref:
            return desc
    raise AssertionError("missing candidate %r in %s" % (
        source_ref, sorted({d.get("source_ref") for d in collected.get("descriptors", {}).values()})))


def _source_refs(collected: dict) -> set[str]:
    return {
        str(desc.get("source_ref") or "")
        for desc in collected.get("descriptors", {}).values()
    }


def _event_from(collected: dict, source_ref: str, *, decision_id: str) -> dict:
    event = bridge.v02_v03_round_event_for_selected_candidate(
        collected, source_ref, decision_id=decision_id
    )
    assert isinstance(event, dict), "no executable event for %r" % (source_ref,)
    return event


def _settle(root: Path, event: dict, now_iso: str) -> dict:
    return bridge.settle_v02_v03_round_consequences(
        root, event, now_iso=now_iso
    )


def _ledger(root: Path) -> dict:
    path = root / "phone" / bridge.CONSEQUENCE_LEDGER_FILENAME
    if not path.is_file():
        return {}
    return dict(json.loads(path.read_text("utf8")).get("receipts") or {})


def _dir_snapshot(root: Path, names) -> dict:
    snap: dict = {}
    for name in names:
        base = Path(root) / name
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            rel = str(path.relative_to(root)).replace("\\", "/")
            st = path.lstat()
            if path.is_file() and not path.is_symlink():
                snap[rel] = (
                    int(st.st_size),
                    int(st.st_mtime_ns),
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                )
            else:
                snap[rel] = (0, int(st.st_mtime_ns), "dir")
    return snap


# -- seeding helpers ---------------------------------------------------------


def _seed_living_floor(root: Path, now_iso: str, *, floor_cents: int = 5_400) -> None:
    """A tight living floor (balance 6000, floor 5400 -> discretionary 600 <= 800)
    is what motivates Day-1 independent work; after the job credit the margin opens
    back up so Day-4 affordable purchase passes the real budget gate."""
    code, body = ecology_view.mutate_v03_ecology_view(
        root,
        {
            "action": "configure_living_floor",
            "living_floor_cents": floor_cents,
            "operation_key": "seed_living_floor",
        },
        actor_side="subject",
        now_iso=now_iso,
    )
    assert code == 200 and body.get("ok"), body


def _seed_desk_note(root: Path, now_iso: str, *, note_id: str = "bedside_1") -> None:
    (root / "phone").mkdir(parents=True, exist_ok=True)
    (root / "phone" / "desk_tomorrow_note.json").write_text(
        json.dumps(
            {
                "note_id": note_id,
                "title": "明早把窗台的薄荷换个位置",
                "status": "OPEN",
                "due_at": now_iso,
                "created_at": now_iso,
            },
            ensure_ascii=False,
        ),
        encoding="utf8",
    )


def _seed_workbench(root: Path, now_iso: str) -> None:
    state = workbench_view.empty_tray_state()
    state, changed = workbench_view.set_tray(
        state,
        {
            "images": [],
            "materials": [],
            "work": {"artifact_id": "artifact:draft-1", "baseline_version_id": "ver-1"},
            "note": "纸样还没上色",
        },
        now_iso,
    )
    assert changed
    workbench_view.write_tray_state(root, state)


def _seed_plan(root: Path, now_iso: str, *, plan_id: str, title: str) -> None:
    state, reason = hearth_view.read_plans_state(root)
    if reason is not None:
        state = hearth_view.empty_plans_state()
    state, _changed, _card, err = hearth_view.propose_plan(
        state, plan_id=plan_id, title=title, window_note="", proposer="her", now_iso=now_iso
    )
    assert err is None, err
    hearth_view.write_plans_state(root, state)


def _seed_bookmark(root: Path, now_iso: str, *, book_code: str = "tide_almanac_notes") -> None:
    outing_view.write_bench_bookstore_state(
        root,
        {
            "schema_version": 1,
            "revision": 0,
            "updated_at": None,
            "bench_visits": [],
            "window_bookmarks": [
                {
                    "bookmark_id": "wbk-1",
                    "slot_index": 0,
                    "book_code": book_code,
                    "title": "潮汐与岸边小历",
                    "note": "傍晚想翻两页",
                    "shared": False,
                    "saved_at": now_iso,
                    "updated_at": now_iso,
                }
            ],
            "partner_window_note": None,
        },
    )


def _seed_route(root: Path, now_iso: str, *, route_id: str = "route-riverside-1") -> None:
    outing_view.write_routes_market_state(
        root,
        {
            "schema_version": 1,
            "revision": 0,
            "updated_at": None,
            "routes": [
                {
                    "route_id": route_id,
                    "title": "河岸慢走",
                    "place_ids": ["riverside_path", "old_bookstreet"],
                    "album_artifact_id": None,
                    "caption": None,
                    "shared": False,
                    "created_at": now_iso,
                    "updated_at": now_iso,
                }
            ],
            "market_wishes": [],
            "partner_market_tip": None,
        },
    )


def _seed_plant(root: Path, now_iso: str, *, plant_id: str = "mint-1") -> None:
    state = longterm_view.empty_room_habits_state()
    state, _link, changed = longterm_view.apply_link_plant_sketch(
        state,
        plant_id=plant_id,
        plant_name="窗台薄荷",
        target_kind="album",
        target_artifact_id="alb-plant-1",
        observation_note="叶尖带一点浅绿锯齿",
        shared=False,
        now_iso=now_iso,
    )
    assert changed
    longterm_view.write_room_habits_state(root, state)


def _seed_common_ground(root: Path, now_iso: str, *, item_id: str = "cg_umbrella") -> None:
    result = common_ground.record_ground_item(
        root,
        item_id=item_id,
        topic="伞放在哪",
        summary="昨天顺手把伞收起来了",
        ground_status=common_ground.PENDING_CLARIFICATION,
        now_iso=now_iso,
    )
    assert result.get("ok"), result


def _seed_private_common_ground(root: Path, now_iso: str) -> None:
    result = common_ground.record_ground_item(
        root,
        item_id="cg_private",
        topic="PRIVATE_GROUND_SECRET",
        summary="PRIVATE_GROUND_DETAIL",
        ground_status=common_ground.PRIVATE_UNSHARED,
        now_iso=now_iso,
    )
    assert result.get("ok"), result


def _seed_purchase_cues(root: Path) -> None:
    (root / "phone").mkdir(parents=True, exist_ok=True)
    (root / "phone" / "v03_purchase_cues.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "cue_id": "lamp_bulb",
                        "item_title": "替换的暖光灯泡",
                        "price_cents": 800,
                    },
                    {
                        "cue_id": "fancy_vase",
                        "item_title": "过于昂贵的花瓶",
                        "price_cents": 50_000,
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf8",
    )


def _open_inquiry(root: Path, now_iso: str, *, shared: bool = True) -> str:
    code, doc = ad_view.mutate_v02_agency_depth_view(
        root,
        {
            "action": "open_bounded_inquiry",
            "question": "窗边薄荷越冬要不要剪枝？",
            "topic_tag": "窗边薄荷",
            "shared": shared,
            "operation_key": "d2-inq-open",
        },
        actor_side="subject",
        now_iso=now_iso,
    )
    assert code == 200, doc
    return str(doc["inquiries"][0]["inquiry_id"])


def _conclude_inquiry(root: Path, inquiry_id: str, now_iso: str) -> dict:
    _proj, status = ad_engine.apply_agency_depth_mutation(
        root,
        {
            "action": "conclude_or_stop_inquiry",
            "inquiry_id": inquiry_id,
            "outcome": "CONCLUDED",
            "conclusion": "留老茎剪徒长枝更稳。",
            "operation_key": "d6-inq-conclude",
        },
        actor_side="subject",
        now_iso=now_iso,
    )
    assert status == "APPLIED", status
    return ad_engine.project_v02_agency_depth(
        root, has_subject_capability=True, now_iso=now_iso
    )


def _seed_v04_neighborhood(root: Path, now_iso: str, *, weather: str = "clear") -> None:
    code, body = mutate_v04_neighborhood_view(
        root,
        {"action": "set_local_weather", "weather": weather, "airflow": "medium"},
        actor_side="partner",
        now_iso=now_iso,
    )
    assert code == 200 and body.get("status") == "applied", body


def _post_v04_notice(root: Path, now_iso: str) -> str:
    code, body = mutate_v04_neighborhood_view(
        root,
        {
            "action": "post_public_notice",
            "title": "近路围挡通知",
            "body": "街角近路今天上午临时围挡，请绕行长廊。",
            "valid_hours": 48,
        },
        actor_side="partner",
        now_iso=now_iso,
    )
    assert code == 200, body
    state, exists = v04_engine.load_state_readonly(root)
    assert exists
    notices = state["public_life"]["public_notices"]
    return str(notices[-1]["notice_id"])


def _v04_state(root: Path) -> dict:
    state, exists = v04_engine.load_state_readonly(root)
    assert exists
    return state


def _inject_acute_illness(root: Path, now_iso: str) -> None:
    result = eco_engine.record_strain_or_rest(
        root,
        event_kind="caught_in_rain",
        intensity=2.0,
        unchanged_damp_clothes=True,
        operation_key="d3-acute",
        now_iso=now_iso,
    )
    assert result.get("status") in ("ok", "APPLIED", None), result
    assert _illness(root, now_iso)["capability_envelope"]["blocks_strenuous_outing"] is True


def _set_illness_healthy(root: Path, now_iso: str) -> None:
    """Fixture: mark the body recovered in place, preserving the economy ledger.

    The multi-day illness curve legitimately needs ~2 days of rest to clear the
    ``blocks_strenuous_outing`` band; the day-by-day chain models "病愈" on Day 4 by
    patching only the ``illness`` sub-document of the real ecology state while leaving
    every economy / ledger byte intact.  This is an injected test precondition, not a
    production path.
    """
    path = root / "phone" / "v03_ecology.json"
    doc = json.loads(path.read_text("utf8"))
    illness = doc.get("illness") if isinstance(doc.get("illness"), dict) else {}
    illness.update({"phase": "healthy", "severity": 0.0, "strain_load": 0.0, "rest_active": False})
    doc["illness"] = illness
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf8")
    assert _illness(root, now_iso)["capability_envelope"]["blocks_strenuous_outing"] is False


def _seed_recall_state(root: Path, now_iso: str) -> None:
    path = root / "memory" / "v02_recall_projection_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": recall.SCHEMA_VERSION,
                "memories": {
                    "mem_1": {
                        "memory_id": "mem_1",
                        "topic": "窗边薄荷",
                        "gist_summary": "秋天给窗边薄荷换了盆。",
                        "occurred_at": "2026-09-12T10:00:00+08:00",
                        "is_relationship_milestone": True,
                        "shared": True,
                    },
                    "mem_2": {
                        "memory_id": "mem_2",
                        "topic": "窗边薄荷",
                        "gist_summary": "入冬前把薄荷挪回屋里。",
                        "occurred_at": "2026-11-02T10:00:00+08:00",
                        "is_relationship_milestone": False,
                        "shared": True,
                    },
                    "mem_private": {
                        "memory_id": "mem_private",
                        "topic": "PRIVATE_MEMORY_SECRET",
                        "gist_summary": "PRIVATE_MEMORY_DETAIL",
                        "occurred_at": "2026-10-01T10:00:00+08:00",
                        "shared": False,
                    },
                },
                "tombstones": {},
                "updated_at": now_iso,
            },
            ensure_ascii=False,
        ),
        encoding="utf8",
    )


# -- adult intimacy helpers --------------------------------------------------


def _open_ready_intimacy_env(root: Path, *, now: datetime = INTIMACY_AT) -> None:
    from tests.test_adult_intimacy_view import _open_ready_env

    _open_ready_env(root, now=now)


def _clear_intimacy_acute(root: Path, *, now: datetime = INTIMACY_AT) -> None:
    """Recovery: clear the acute-illness hard block the v0.3 illness sync wrote
    into the 36B body context (acute injection creates/updates it even before the
    gate is configured).  Real owner action, no product model."""
    intimacy_view.apply_adult_intimacy_action(
        root,
        {
            "action": "update_body_context",
            "acute_illness": False,
            "discomfort": "none",
            "fatigue": 0.1,
        },
        now=now,
        subject_view=True,
    )


def _open_active_scene(root: Path, *, tag: str, now: datetime) -> str:
    proposed = intimacy_view.apply_adult_intimacy_action(
        root,
        {
            "action": "propose_scene",
            "operation_key": "%s_proposal" % tag,
            "proposer": "partner",
            "categories": ["KISSING"],
        },
        now=now,
    )
    permit_id = proposed["action_receipt"]["permit_id"]
    intimacy_view.apply_adult_intimacy_action(
        root,
        {
            "action": "respond_scene",
            "permit_id": permit_id,
            "responder": "subject",
            "decision": "ACCEPT",
        },
        now=now,
    )
    return str(permit_id)


def _permit_rec(root: Path, permit_id: str) -> dict:
    state = json.loads(
        intimacy_view.adult_intimacy_state_path(root).read_text(encoding="utf8")
    )
    return state["permits"]["records"][permit_id]


# ===========================================================================
# Part 1: owner-level seven-day truth chain (platform independent, fast)
# ===========================================================================


class SevenDayOwnerTruthChainTest(unittest.TestCase):
    """第 1–7 天的真实 owner 磁盘状态链；不需要 socket，原生 Windows 也能跑。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v08-wave8-owner-")
        self.root = Path(self._tmp.name)
        (self.root / "phone").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- Day 1: 0.3 起手 + 0.2 工作台/便笺 + SlotQuota --------------------------
    def _day1_slot_quota_ledger(self) -> None:
        budget = lrb.LifeRequestBudget(self.root / "budget", utc_day_max=24, run_window_max=1000)
        now = datetime.fromisoformat(DAY1)
        # DAYTIME_ACTIVE 配额 2：第三次必须被时段配额拦下。
        for _ in range(lrb.SLOT_QUOTAS["DAYTIME_ACTIVE"]):
            budget.record_slot_and_daily_consumption(
                slot_kind="DAYTIME_ACTIVE", category="INITIATION", now_dt=now
            )
        with self.assertRaises(lrb.RequestBudgetError) as caught:
            budget.record_slot_and_daily_consumption(
                slot_kind="DAYTIME_ACTIVE", category="INITIATION", now_dt=now
            )
        self.assertEqual(caught.exception.reason_code, lrb.REASON_SLOT_QUOTA_EXCEEDED)
        snap = budget.slot_and_daily_snapshot(now_dt=now)
        self.assertEqual(snap["slots"]["DAYTIME_ACTIVE"]["used"], 2)
        self.assertEqual(snap["slots"]["DAYTIME_ACTIVE"]["quota"], 2)

        # EVENING_WIND_DOWN 配额 1：第二次被拦。
        budget.record_slot_and_daily_consumption(
            slot_kind="EVENING_WIND_DOWN", category="REFLECTION", now_dt=now
        )
        with self.assertRaises(lrb.RequestBudgetError) as caught:
            budget.record_slot_and_daily_consumption(
                slot_kind="EVENING_WIND_DOWN", category="REFLECTION", now_dt=now
            )
        self.assertEqual(caught.exception.reason_code, lrb.REASON_SLOT_QUOTA_EXCEEDED)

        # NIGHT_REST 配额 0：任何一次都被精确拦截。
        night = budget.check_slot_and_daily_envelope(
            slot_kind="NIGHT_REST", category="REFLECTION", now_dt=now
        )
        self.assertFalse(night["allowed"])
        self.assertEqual(night["reason_code"], lrb.REASON_SLOT_QUOTA_EXCEEDED)
        self.assertEqual(night["slot_quota"], 0)

        # DailyEnvelope：日硬上限 8 与分类上限 CONTINUATION 5 各自独立。
        self.assertEqual(lrb.DAILY_ENVELOPE_CAPS["daily_hard_cap"], 8)
        self.assertEqual(lrb.DAILY_ENVELOPE_CAPS["per_category_cap"]["CONTINUATION"], 5)

    def test_seven_day_owner_chain(self) -> None:
        root = self.root

        # ------------------------------------------------------------------
        # Day 1 — 0.3 起手：真实余额、明日便笺、工作台、额度账本
        # ------------------------------------------------------------------
        _seed_living_floor(root, DAY1)
        _seed_desk_note(root, DAY1)
        _seed_workbench(root, DAY1)
        self._day1_slot_quota_ledger()

        day1 = _collect(root, DAY1)
        self.assertIn("v03_job:manuscript_fee", _source_refs(day1))
        self.assertIn("tomorrow_note:bedside_1", _source_refs(day1))
        self.assertIn("artifact:draft-1", _source_refs(day1))
        self.assertIn("v03_idle:daydream_window", _source_refs(day1))

        start_balance = _balance(root, DAY1)
        job = _settle(
            root, _event_from(day1, "v03_job:manuscript_fee", decision_id="d1-job"), DAY1_JOB
        )
        self.assertEqual(job["status"], "APPLIED", job)
        self.assertEqual(job.get("llm_calls", 0), 0)
        self.assertIn("v03_ecology.economy", job["slices_updated"])
        payout = int(_descriptor(day1, "v03_job:manuscript_fee")["payout_cents"])
        self.assertGreater(payout, 0)
        self.assertEqual(_balance(root, DAY1_JOB), start_balance + payout)

        # 同 decision_id 重放：入账与回执完全幂等，余额不再变。
        replay = _settle(
            root, _event_from(day1, "v03_job:manuscript_fee", decision_id="d1-job"), DAY1_JOB
        )
        self.assertTrue(replay.get("already_applied"), replay)
        self.assertEqual(_balance(root, DAY1_JOB), start_balance + payout)
        credits = [
            row
            for row in _read_eco_state(root)["economy"]["ledger_entries"]
            if row["entry_kind"] == "INDEPENDENT_WORK_CREDIT"
        ]
        self.assertEqual(len(credits), 1)

        note = _settle(
            root, _event_from(day1, "tomorrow_note:bedside_1", decision_id="d1-note"), DAY1_JOB
        )
        self.assertEqual(note["status"], "APPLIED", note)
        self.assertIn("desk_tomorrow_note", note["slices_updated"])
        note_doc = json.loads((root / "phone" / "desk_tomorrow_note.json").read_text("utf8"))
        self.assertEqual(note_doc["status"], "DONE")

        bench = _settle(
            root, _event_from(day1, "artifact:draft-1", decision_id="d1-bench"), DAY1_JOB
        )
        self.assertEqual(bench["status"], "APPLIED", bench)
        self.assertIn("workbench_tray", bench["slices_updated"])
        tray_state, reason = workbench_view.read_tray_state(root)
        self.assertIsNone(reason)
        self.assertIn("〔", tray_state["tray"]["note"])
        # 真实草稿身份保留，没有伪造版本。
        self.assertEqual(tray_state["tray"]["work"]["artifact_id"], "artifact:draft-1")
        self.assertEqual(tray_state["tray"]["work"]["baseline_version_id"], "ver-1")

        # ------------------------------------------------------------------
        # Day 2 — 0.2 有界探究立项 + 共同背景 + 0.4 院子晾晒
        # ------------------------------------------------------------------
        inquiry_id = _open_inquiry(root, DAY2)
        _seed_common_ground(root, DAY2)
        _seed_v04_neighborhood(root, DAY2)

        day2 = _collect(root, DAY2)
        refs2 = _source_refs(day2)
        self.assertIn("v02_inquiry:%s" % inquiry_id, refs2)
        self.assertIn("common_ground:cg_umbrella", refs2)
        self.assertIn("v04_courtyard_hang:wet_cloth_main", refs2)

        adv = _settle(
            root,
            _event_from(day2, "v02_inquiry:%s" % inquiry_id, decision_id="d2-inq"),
            DAY2,
        )
        self.assertEqual(adv["status"], "APPLIED", adv)
        self.assertIn("v02_agency_depth.inquiries", adv["slices_updated"])
        proj = ad_engine.project_v02_agency_depth(root, has_subject_capability=True, now_iso=DAY2)
        self.assertEqual(proj["inquiries"][0]["status"], "INVESTIGATING")
        self.assertEqual(len(proj["inquiries"][0]["evidence_notes"]), 1)

        cg = _settle(
            root,
            _event_from(day2, "common_ground:cg_umbrella", decision_id="d2-cg"),
            DAY2,
        )
        self.assertEqual(cg["status"], "APPLIED", cg)
        summary = common_ground.summarize_common_ground_for_cognition(root)
        self.assertEqual(summary.get("pending_clarifications"), [])

        hang = _settle(
            root,
            _event_from(day2, "v04_courtyard_hang:wet_cloth_main", decision_id="d2-hang"),
            DAY2,
        )
        self.assertEqual(hang["status"], "APPLIED", hang)
        self.assertEqual(
            _v04_state(root)["drying_items"]["wet_cloth_main"]["support_id"], "yard_line"
        )
        # 三域状态在同一实例上互不踩踏。
        self.assertEqual(proj["inquiries"][0]["status"], "INVESTIGATING")
        self.assertEqual(_v04_state(root)["drying_items"]["wet_cloth_main"]["support_id"], "yard_line")

        # ------------------------------------------------------------------
        # Day 3 — 生病照护 + 计划/远足拒止 + 发呆合法性
        # ------------------------------------------------------------------
        _seed_plan(root, DAY2, plan_id="plan-hike", title="去河岸徒步走到旧书街")
        _seed_route(root, DAY2)
        healthy = _collect(root, DAY2)
        self.assertTrue(_descriptor(healthy, "plan:plan-hike")["strenuous_outing"])
        self.assertTrue(_descriptor(healthy, "route:route-riverside-1")["strenuous_outing"])
        plan_event = _event_from(healthy, "plan:plan-hike", decision_id="d3-plan")
        route_event = _event_from(healthy, "route:route-riverside-1", decision_id="d3-route")

        # 受凉转入急症（candidate_cap=4）。当日真实候选恰为 4 项：两项照护、探究、
        # 合法发呆，正好都进了上限之内的候选池。
        _inject_acute_illness(root, DAY3)
        self.assertEqual(_illness(root, DAY3_ILL)["phase"], "acute_illness")
        ill = _collect(root, DAY3_ILL)
        self.assertIn("v03_care:bed_rest_block", _source_refs(ill))
        self.assertIn("v03_care:warm_hydration", _source_refs(ill))
        self.assertIn("v03_idle:daydream_window", _source_refs(ill))

        bed = _settle(root, _event_from(ill, "v03_care:bed_rest_block", decision_id="d3-bed"), DAY3_ILL)
        self.assertEqual(bed["status"], "APPLIED", bed)
        self.assertTrue(_illness(root, DAY3_ILL)["rest_active"])
        hyd = _settle(root, _event_from(ill, "v03_care:warm_hydration", decision_id="d3-water"), DAY3_ILL)
        self.assertEqual(hyd["status"], "APPLIED", hyd)
        self.assertIsNotNone(_illness(root, DAY3_ILL)["last_hydration_at"])

        idle = _settle(root, _event_from(ill, "v03_idle:daydream_window", decision_id="d3-idle"), DAY3_ILL)
        self.assertEqual(idle["status"], "APPLIED", idle)
        receipt = idle["details"]["meaningless_idle"]
        self.assertEqual(receipt["productivity_penalty"], 0)
        self.assertFalse(receipt["created_artifact"])
        self.assertFalse(receipt["forced_summary_generated"])

        # 同一病期：剧烈远足与强耗能计划被真实门控拒绝，零写盘。
        plans_before = (root / "phone" / "tentative_plans.json").read_text("utf8")
        routes_before = (root / "phone" / "outing_routes_market.json").read_text("utf8")
        refused_plan = _settle(root, plan_event, DAY3_ILL)
        refused_route = _settle(root, route_event, DAY3_ILL)
        self.assertEqual(refused_plan["status"], "REFUSED", refused_plan)
        self.assertEqual(refused_plan["reason_code"], "ILLNESS_BLOCKS_STRENUOUS_OUTING")
        self.assertEqual(refused_plan["optimistic_writes"], 0)
        self.assertEqual(refused_route["status"], "REFUSED", refused_route)
        self.assertEqual(refused_route["reason_code"], "ILLNESS_BLOCKS_STRENUOUS_OUTING")
        self.assertEqual(refused_route["optimistic_writes"], 0)
        self.assertEqual((root / "phone" / "tentative_plans.json").read_text("utf8"), plans_before)
        self.assertEqual((root / "phone" / "outing_routes_market.json").read_text("utf8"), routes_before)
        # 照料没有因此被打断。
        self.assertTrue(_illness(root, DAY3_ILL)["rest_active"])

        # ------------------------------------------------------------------
        # Day 4 — 病愈 + 可负担购买 / 预算拒止 + 成人亲密断点保存与降级
        # ------------------------------------------------------------------
        _set_illness_healthy(root, DAY4)
        _seed_purchase_cues(root)
        day4 = _collect(root, DAY4)
        self.assertIn("purchase_cue:lamp_bulb", _source_refs(day4))
        # 超预算的非必需冲动购买在采集阶段就被预算闸拦下。
        filtered = [
            row
            for row in day4["gating"]["filtered_candidates"]
            if row.get("source_ref") == "purchase_cue:fancy_vase"
        ]
        self.assertTrue(filtered, day4["gating"]["filtered_candidates"])

        before_buy = _balance(root, DAY4)
        bought = _settle(
            root, _event_from(day4, "purchase_cue:lamp_bulb", decision_id="d4-buy"), DAY4
        )
        self.assertEqual(bought["status"], "APPLIED", bought)
        self.assertEqual(_balance(root, DAY4), before_buy - 800)

        forged = {
            "actor_side": "life_resident",
            "effect_state": "APPLIED",
            "source_ref": "purchase_cue:fancy_vase",
            "receipt_ref": "test:wave8:overbudget",
            "decision_id": "d4-overbudget",
            "action_kind": "COMMIT_AFFORDABLE_PURCHASE",
            "item_title": "过于昂贵的花瓶",
            "price_cents": 50_000,
            "verdict": "afford_independently",
            "commit_spend": True,
            "item_id": "fancy_vase",
            "operation_key": "wave8_overbudget_purchase",
        }
        refused = _settle(root, forged, DAY4)
        self.assertEqual(refused["status"], "REFUSED", refused)
        self.assertEqual(refused["reason_code"], "INSUFFICIENT_INDEPENDENT_FUNDS")
        self.assertEqual(refused["optimistic_writes"], 0)
        self.assertGreaterEqual(_balance(root, DAY4), 0)

        # 成人亲密：保存真实 activity_ref，再 STOP 降级 -> checkpoint 可恢复。
        _open_ready_intimacy_env(root, now=INTIMACY_AT)
        _clear_intimacy_acute(root, now=INTIMACY_AT)
        permit_id = _open_active_scene(root, tag="wave8", now=INTIMACY_AT)
        self.assertFalse(_permit_rec(root, permit_id)["checkpoint"]["resumable"])
        intimacy_view.apply_adult_intimacy_action(
            root,
            {
                "action": "save_activity_checkpoint",
                "permit_id": permit_id,
                "activity_ref": "life_activity:reading_nook",
                "step_summary": "读到一半",
                "activity_title": "窗边读书",
            },
            now=INTIMACY_AT,
        )
        intimacy_view.apply_adult_intimacy_action(
            root,
            {"action": "stop_scene", "permit_id": permit_id, "by": "subject"},
            now=INTIMACY_AT + timedelta(seconds=30),
        )
        checkpoint = _permit_rec(root, permit_id)["checkpoint"]
        self.assertTrue(checkpoint["resumable"])
        self.assertEqual(checkpoint["activity_ref"], "life_activity:reading_nook")

        # ------------------------------------------------------------------
        # Day 5 — 受控重启（owner 级：无内存对象可丢）+ 断点发现与消费
        # ------------------------------------------------------------------
        # 余额、探究、晒衣进度、亲密断点全部从磁盘重新读出且逐字保真。
        self.assertEqual(_balance(root, DAY5), before_buy - 800)
        proj5 = ad_engine.project_v02_agency_depth(root, has_subject_capability=True, now_iso=DAY5)
        self.assertEqual(proj5["inquiries"][0]["status"], "INVESTIGATING")
        self.assertEqual(
            _v04_state(root)["drying_items"]["wet_cloth_main"]["support_id"], "yard_line"
        )
        self.assertTrue(_permit_rec(root, permit_id)["checkpoint"]["resumable"])

        resume_ref = "v03_intimacy_resume:%s" % permit_id
        day5 = _collect(root, DAY5)
        self.assertIn(resume_ref, _source_refs(day5))
        resume = _settle(root, _event_from(day5, resume_ref, decision_id="d5-resume"), DAY5)
        self.assertEqual(resume["status"], "APPLIED", resume)
        self.assertIn("phone.adult_intimacy", resume["slices_updated"])
        self.assertEqual(
            resume["details"]["intimacy_resumes"]["resumed_activity_ref"],
            "life_activity:reading_nook",
        )
        final_cp = _permit_rec(root, permit_id)["checkpoint"]
        self.assertTrue(final_cp["consumed"])
        self.assertFalse(final_cp["resumable"])
        # 再次采集不再重复产出该断点。
        self.assertNotIn(resume_ref, _source_refs(_collect(root, DAY5)))

        # ------------------------------------------------------------------
        # Day 6 — 0.2 收尾：计划 / 书签 / 植物手账 / 公共告示 / 探究收敛
        # ------------------------------------------------------------------
        _seed_bookmark(root, DAY6)
        _seed_plant(root, DAY6)
        notice_id = _post_v04_notice(root, DAY6)
        day6 = _collect(root, DAY6)
        refs6 = _source_refs(day6)
        self.assertIn("plan:plan-hike", refs6)
        self.assertIn("book:tide_almanac_notes", refs6)
        self.assertIn("plant:mint-1", refs6)
        self.assertIn("v04_public_notice:%s" % notice_id, refs6)

        adopted = _settle(root, _event_from(day6, "plan:plan-hike", decision_id="d6-plan"), DAY6)
        self.assertEqual(adopted["status"], "APPLIED", adopted)
        self.assertEqual(adopted["details"]["tentative_plan"]["resolution"], "ADOPTED")
        plans_state, _reason = hearth_view.read_plans_state(root)
        by_id = {row["plan_id"]: row for row in plans_state["plans"]}
        self.assertEqual(by_id["plan-hike"]["status"], "DROPPED")

        book = _settle(root, _event_from(day6, "book:tide_almanac_notes", decision_id="d6-book"), DAY6)
        self.assertEqual(book["status"], "APPLIED", book)
        plant = _settle(root, _event_from(day6, "plant:mint-1", decision_id="d6-plant"), DAY6)
        self.assertEqual(plant["status"], "APPLIED", plant)
        notice = _settle(
            root,
            _event_from(day6, "v04_public_notice:%s" % notice_id, decision_id="d6-notice"),
            DAY6,
        )
        self.assertEqual(notice["status"], "APPLIED", notice)
        self.assertTrue(_v04_state(root)["public_life"]["public_notices"][-1]["read_by_subject"])

        _conclude_inquiry(root, inquiry_id, DAY6)
        concluded = ad_engine.project_v02_agency_depth(root, has_subject_capability=True, now_iso=DAY6)
        self.assertEqual(concluded["inquiries"][0]["status"], "CONCLUDED")

        # 夜间零额度：默认预算调用不被破坏。
        night_budget = lrb.LifeRequestBudget(root / "budget", utc_day_max=24, run_window_max=1000)
        night_now = datetime.fromisoformat(DAY6.replace("09:00:00", "23:00:00"))
        night = night_budget.check_slot_and_daily_envelope(
            slot_kind="NIGHT_REST", category="REFLECTION", now_dt=night_now
        )
        self.assertFalse(night["allowed"])
        self.assertEqual(night["reason_code"], lrb.REASON_SLOT_QUOTA_EXCEEDED)
        legacy = night_budget.can_spend(now=night_now)
        self.assertTrue(legacy["allowed"])
        self.assertNotIn("slot_envelope_checked", legacy)

        # ------------------------------------------------------------------
        # Day 7 — 只读全局视角：P12 叙事 + 22 类衰减矩阵 + 隐私 + 零写入
        # ------------------------------------------------------------------
        _seed_recall_state(root, DAY7)
        _seed_private_common_ground(root, DAY7)

        before = _dir_snapshot(root, ["phone", "memory", "state", "world"])

        subject = recall.project_p12_autobiographical_narrative(
            root, has_subject_capability=True, now_iso=DAY7
        )
        partner = recall.project_p12_autobiographical_narrative(
            root, has_subject_capability=False, now_iso=DAY7
        )
        recall_view = recall.read_v02_recall_projection_view(
            root, now_iso=DAY7, has_subject_capability=True
        )
        ad_code, ad_doc = ad_view.read_v02_agency_depth_view(
            root, has_subject_capability=False, now_iso=DAY7
        )
        rm_code, rm_doc = rm_view.read_v02_runtime_migration_view(
            root, has_subject_capability=False, now_iso=DAY7
        )
        matrix = rm_engine.project_annual_decay_matrix_22(root, horizon_days=365, now_iso=DAY7)

        self.assertEqual(_dir_snapshot(root, ["phone", "memory", "state", "world"]), before)

        # P12 主体视角：多天实证事件 + 转折 + 母题。
        self.assertTrue(subject["state_exists"])
        self.assertEqual(subject["state_status"], recall.P12_STATUS_ATTESTED)
        self.assertEqual(subject["perspective"], "subject")
        self.assertGreaterEqual(len(subject["chapters"]), 2)  # 9 月 / 11 月
        self.assertTrue(subject["turning_points"])
        self.assertTrue(subject["recurring_motifs"])
        attested = set(subject["attested_source_refs"])
        self.assertIn("recall:mem_1", attested)
        self.assertIn("recall:mem_2", attested)
        self.assertIn("common_ground:cg_umbrella", attested)
        self.assertIn("agency_depth:%s" % inquiry_id, attested)
        self.assertIn("workbench:artifact:draft-1", attested)
        motifs = {row["motif"]: row["occurrences"] for row in subject["recurring_motifs"]}
        self.assertGreaterEqual(motifs.get("窗边薄荷", 0), 2)
        self.assertEqual(subject["llm_calls"], 0)

        # P12 伴侣视角：严格剥离未共享私密内容。
        partner_blob = json.dumps(partner, ensure_ascii=False)
        for secret in (
            "PRIVATE_MEMORY_SECRET",
            "PRIVATE_MEMORY_DETAIL",
            "PRIVATE_GROUND_SECRET",
            "PRIVATE_GROUND_DETAIL",
        ):
            self.assertNotIn(secret, partner_blob)
        self.assertNotIn("recall:mem_private", partner["attested_source_refs"])
        self.assertIn("recall:mem_private", subject["attested_source_refs"])
        self.assertEqual(partner["perspective"], "partner")

        # 读面挂载 P12 且零 LLM。
        self.assertIn("p12_autobiographical_narrative", recall_view)
        self.assertEqual(ad_code, 200)
        self.assertIn("p12_autobiographical_narrative", ad_doc)
        self.assertEqual(ad_doc["llm_calls"], 0)
        self.assertEqual(rm_code, 200)

        # Ch.16 22 类衰减矩阵：22 类不重不漏，反映前 6 天真实活跃状态。
        self.assertEqual(matrix["total_categories"], 22)
        self.assertTrue(matrix["zero_write"])
        self.assertEqual(matrix["llm_calls"], 0)
        names = [row["category"] for row in matrix["categories"]]
        self.assertEqual(len(names), 22)
        self.assertEqual(len(set(names)), 22)
        present = {row["category"] for row in matrix["categories"] if row["present"]}
        for expected in (
            "episodic_traces",
            "autobiographical_anchors",
            "seasonal_milestones",
            "shared_vocabulary",
            "open_inquiries",
            "tentative_plans",
            "tomorrow_notes",
            "workbench_drafts",
            "courtyard_plant_care",
            "illness_recovery_memory",
            "economic_spending_rhythm",
        ):
            self.assertIn(expected, present, "decay category %s should be present" % expected)
        self.assertGreaterEqual(len(present), 12)
        for row in matrix["categories"]:
            self.assertIn(row["status"], rm_engine.DECAY_MONITOR_STATUSES)
            self.assertGreater(row["half_life_days"], 0)
            self.assertTrue(row["re_anchor_policy"])

        # 因果账本可追溯：Day 1 的赚取与 Day 2 的晾晒都在账上。
        receipts = _ledger(root)
        self.assertTrue(receipts)

        # 七天总账：余额守恒、对象数量一致、因果账本可追溯。
        self.assertEqual(_balance(root, DAY7), start_balance + payout - 800)
        self.assertEqual(len(_v04_state(root)["drying_items"]), 1)
        self.assertEqual(
            _v04_state(root)["drying_items"]["wet_cloth_main"]["support_id"], "yard_line"
        )


# ===========================================================================
# Part 2: real cycle/resident seven-day continuity (POSIX)
# ===========================================================================

try:  # pragma: no cover - platform gate only
    from test_one_day_life import OneDayTestCase as _OneDaySeamBase, StubWriter, posix_only
    import agency_decision_ag1 as ag1
    import autonomous_life_assembly as assembly
    import autonomous_life_cycle as alc
    import autonomous_life_resident as resident
    import autonomous_life_runner as life_runner
    import one_day_life as odl

    _FIXTURE_ERROR = None
except Exception as _exc:  # noqa: BLE001 - native Windows has no fcntl / AF_UNIX
    _FIXTURE_ERROR = "%s: %s" % (type(_exc).__name__, _exc)
    _OneDaySeamBase = unittest.TestCase
    posix_only = unittest.skipIf(True, "real owner fixture needs POSIX")

needs_fixture = unittest.skipIf(
    globals().get("_FIXTURE_ERROR") is not None,
    "real owner fixture unavailable: %s" % globals().get("_FIXTURE_ERROR", ""),
)


class _SeamCognition:
    """零产品模型脚本化决策：只从被展示的候选里按前缀挑选。"""

    def __init__(self) -> None:
        self.select_prefixes: list[str] = []
        self.call_count = 0

    def invoke(self, request, *, schema_retry_index: int = 0):
        self.call_count += 1
        candidates = [dict(c) for c in (getattr(request, "candidates", []) or [])]
        allowed = [str(code) for code in (getattr(request, "allowed_reason_codes", []) or [])]
        chosen = None
        for cand in candidates:
            refs = [str(ref) for ref in (cand.get("target_refs") or [])]
            refs.append(str(cand.get("source_ref") or ""))
            if any(any(ref.startswith(p) for p in self.select_prefixes) for ref in refs):
                chosen = cand
                break
        if chosen is not None:
            raw = {
                "decision": ag1.DECISION_START,
                "selected_candidate_id": chosen.get("candidate_id"),
                "reason_codes": allowed[:1],
                "short_rationale": "stub(wave8): 顺着这件真实的日常小事走一小步。",
                "deferred_candidate_ids": [],
            }
        else:
            raw = {
                "decision": ag1.DECISION_NO_ACTION,
                "selected_candidate_id": None,
                "reason_codes": allowed[:1],
                "short_rationale": "stub(wave8): 今天先不动这一件。",
                "deferred_candidate_ids": [],
            }
        return ag1.CognitiveDecisionResponse(
            model_run_ref="model_run:stub:%d" % self.call_count,
            model_id="stub:v08-wave8",
            model_version="v08-wave8-stub-v1",
            raw_output=raw,
            latency_ms=1,
        )


@posix_only
@needs_fixture
class SevenDayResidentContinuityTest(_OneDaySeamBase):
    """第 1–7 天在同一个实例根目录上连续生活（真实 cycle / resident / 各域 owner）。"""

    def setUp(self) -> None:
        super().setUp()
        self._now = datetime.fromisoformat(DAY1)
        (Path(self.instance) / "phone").mkdir(parents=True, exist_ok=True)

    # -- real runtime / epoch construction ---------------------------------------
    def _runtime(self, cognition, env, data):
        import life_artifact_port as lap
        import life_integration_ag2 as ag2
        import life_runtime_production as lrp
        import managed_artifact_action_adapter as maaa
        from world_journey_action_adapter import (
            JourneyWorldResolver,
            RealJourneyWorldActionAdapter,
        )

        state_root = odl.life_state_root(self.instance)
        port = lap.LifeArtifactPort(str(self.services.supply_path), data["subject_id"])
        adapter = maaa.SupplyManagedArtifactActionAdapter(
            port, grant_id=data["artifact_grant_id"], workspace_id=data["workspace_id"]
        )
        switches = ag2.LifeRuntimeKillSwitches(
            life_runtime_enabled=True,
            agency_enabled=True,
            action_execution_enabled=True,
            proactive_enabled=False,
        )
        runtime = lrp.build_production_life_runtime(
            state_root,
            kill_switches=switches,
            cognition_adapter=cognition,
            install_root=self.instance / "life",
            artifact_action_adapter=adapter,
            world_action_adapter=RealJourneyWorldActionAdapter(
                JourneyWorldResolver(environment=env)
            ),
        )
        self._runtimes.append(runtime)
        self.addCleanup(runtime.close)
        return runtime, port

    def _instance_root(self) -> Path:
        return Path(self.instance)

    def _make_epoch(self, cognition):
        data = odl.read_instance(self.instance)
        env = self.instance_env()
        runtime, port = self._runtime(cognition, env, data)
        cycle = alc.AutonomousLifeCycle(
            runtime,
            artifact_port=port,
            subject_id=data["subject_id"],
            writer=StubWriter(),
            enabled=True,
            env=env,
        )
        runner_obj = life_runner.AutonomousLifeRunner(
            cycle,
            state_root=odl.life_state_root(self.instance),
            env=env,
            clock=lambda: self._now,
            interval_minutes=0,
        )
        ticked = resident.AutonomousLifeResident(
            state_root=odl.life_state_root(self.instance),
            environment=env,
            act=True,
            clock=lambda: self._now,
            sleeper=lambda _seconds: None,
            monotonic=lambda: 0.0,
        )
        ticked.assembled = {"state": assembly.READY, "runner": runner_obj, "cycle": cycle}
        return ticked, cycle, runtime

    def _tick(self, ticked, cognition, *, now_iso: str, prefixes) -> dict:
        self._now = datetime.fromisoformat(now_iso)
        cognition.select_prefixes = list(prefixes)
        record = ticked.tick()
        bridge_view = record.get("v02_v03_bridge") or {}
        self.assertEqual(int(bridge_view.get("llm_calls") or 0), 0, bridge_view)
        for settlement in bridge_view.get("settlements") or ():
            self.assertEqual(int(settlement.get("llm_calls") or 0), 0, settlement)
        return record

    def test_seven_day_continuity_with_restart_and_resume(self) -> None:
        self.prepare_instance()
        inst = self._instance_root()
        (inst / "phone").mkdir(parents=True, exist_ok=True)

        # Day 1 seed: 余额动机（储蓄目标）、明日便签、工作台、真实 v0.4 环境。
        _seed_living_floor(inst, DAY1)
        _seed_desk_note(inst, DAY1)
        _seed_workbench(inst, DAY1)
        _seed_v04_neighborhood(inst, DAY1)

        cognition = _SeamCognition()
        ticked, cycle, runtime = self._make_epoch(cognition)
        try:
            # ---------------- Day 1: 真实 cycle 赚取余额 ----------------
            start_balance = _balance(inst, DAY1_JOB)
            record = self._tick(
                ticked, cognition, now_iso=DAY1_JOB, prefixes=["v03_job:manuscript_fee"]
            )
            due = record.get("due") or {}
            self.assertEqual(due.get("decision"), "START", record)
            self.assertEqual(due.get("status"), "ACTED", record)
            self.assertEqual(cognition.call_count, 1)
            after_job = _balance(inst, DAY1_JOB)
            self.assertGreater(after_job, start_balance)
            credits = [
                row
                for row in _read_eco_state(inst)["economy"]["ledger_entries"]
                if row["entry_kind"] == "INDEPENDENT_WORK_CREDIT"
            ]
            self.assertEqual(len(credits), 1)
            payout = int(credits[0]["amount_cents"])

            # 0.2 便签与工作台：同一实例、真实 owner 落盘。
            day1 = _collect(inst, DAY1_JOB)
            note = _settle(inst, _event_from(day1, "tomorrow_note:bedside_1", decision_id="w8-d1-note"), DAY1_JOB)
            self.assertEqual(note["status"], "APPLIED", note)
            bench = _settle(inst, _event_from(day1, "artifact:draft-1", decision_id="w8-d1-bench"), DAY1_JOB)
            self.assertEqual(bench["status"], "APPLIED", bench)

            # ---------------- Day 2: 探究立项 + 共同背景 + 院子晾晒 ----------------
            inquiry_id = _open_inquiry(inst, DAY2)
            _seed_common_ground(inst, DAY2)
            record = self._tick(
                ticked, cognition, now_iso=DAY2, prefixes=["v04_courtyard_hang:"]
            )
            self.assertEqual((record.get("due") or {}).get("status"), "ACTED", record)
            self.assertEqual(
                _v04_state(inst)["drying_items"]["wet_cloth_main"]["support_id"], "yard_line"
            )
            day2 = _collect(inst, DAY2)
            adv = _settle(
                inst, _event_from(day2, "v02_inquiry:%s" % inquiry_id, decision_id="w8-d2-inq"), DAY2
            )
            self.assertEqual(adv["status"], "APPLIED", adv)
            cg = _settle(inst, _event_from(day2, "common_ground:cg_umbrella", decision_id="w8-d2-cg"), DAY2)
            self.assertEqual(cg["status"], "APPLIED", cg)

            # ---------------- Day 3: 生病照护 + 剧烈外出拒止 ----------------
            _seed_plan(inst, DAY2, plan_id="plan-hike", title="去河岸徒步走到旧书街")
            _seed_route(inst, DAY2)
            healthy = _collect(inst, DAY2)
            plan_event = _event_from(healthy, "plan:plan-hike", decision_id="w8-d3-plan")
            route_event = _event_from(healthy, "route:route-riverside-1", decision_id="w8-d3-route")
            _inject_acute_illness(inst, DAY3)

            record = self._tick(
                ticked, cognition, now_iso=DAY3_ILL, prefixes=["v03_care:bed_rest_block"]
            )
            self.assertEqual((record.get("due") or {}).get("status"), "ACTED", record)
            self.assertTrue(_illness(inst, DAY3_ILL)["rest_active"])

            refused_plan = _settle(inst, plan_event, DAY3_ILL)
            refused_route = _settle(inst, route_event, DAY3_ILL)
            self.assertEqual(refused_plan["status"], "REFUSED", refused_plan)
            self.assertEqual(refused_plan["reason_code"], "ILLNESS_BLOCKS_STRENUOUS_OUTING")
            self.assertEqual(refused_route["status"], "REFUSED", refused_route)

            # ---------------- Day 4: 病愈 + 可负担购买 + 亲密断点保存 ----------------
            _set_illness_healthy(inst, DAY4)
            _seed_purchase_cues(inst)
            before_buy = _balance(inst, DAY4)
            record = self._tick(
                ticked, cognition, now_iso=DAY4, prefixes=["purchase_cue:lamp_bulb"]
            )
            self.assertEqual((record.get("due") or {}).get("status"), "ACTED", record)
            self.assertEqual(_balance(inst, DAY4), before_buy - 800)

            _open_ready_intimacy_env(inst, now=INTIMACY_AT)
            _clear_intimacy_acute(inst, now=INTIMACY_AT)
            permit_id = _open_active_scene(inst, tag="wave8seam", now=INTIMACY_AT)
            intimacy_view.apply_adult_intimacy_action(
                inst,
                {
                    "action": "save_activity_checkpoint",
                    "permit_id": permit_id,
                    "activity_ref": "life_activity:reading_nook",
                },
                now=INTIMACY_AT,
            )
            intimacy_view.apply_adult_intimacy_action(
                inst,
                {"action": "stop_scene", "permit_id": permit_id, "by": "subject"},
                now=INTIMACY_AT + timedelta(seconds=30),
            )
            self.assertTrue(_permit_rec(inst, permit_id)["checkpoint"]["resumable"])

            # ---------------- Day 5: 受控重启 -> 断点发现与消费 ----------------
            watched = [
                inst / "phone" / "v03_ecology.json",
                inst / "phone" / "desk_tomorrow_note.json",
                inst / "phone" / "adult_intimacy.json",
                inst / "world" / "v04_neighborhood_state.json",
                inst / "state" / "v02_agency_depth_state.json",
                inst / "memory" / "v02_common_ground_state.json",
            ]
            before_restart = {
                str(p): (p.read_bytes() if p.is_file() else None) for p in watched
            }
            restart_balance = _balance(inst, DAY5)

            ticked.stop()
            runtime.close()

            cognition_b = _SeamCognition()
            ticked_b, cycle_b, runtime_b = self._make_epoch(cognition_b)
            try:
                # 仅构造新实例不得改写任何既有域事实（零虚构补写）。
                for p in watched:
                    now_bytes = p.read_bytes() if p.is_file() else None
                    self.assertEqual(now_bytes, before_restart[str(p)], str(p))

                self.assertEqual(_balance(inst, DAY5), restart_balance)
                self.assertEqual(
                    ad_engine.project_v02_agency_depth(
                        inst, has_subject_capability=True, now_iso=DAY5
                    )["inquiries"][0]["status"],
                    "INVESTIGATING",
                )
                self.assertEqual(
                    _v04_state(inst)["drying_items"]["wet_cloth_main"]["support_id"], "yard_line"
                )
                self.assertTrue(_permit_rec(inst, permit_id)["checkpoint"]["resumable"])

                resume_ref = "v03_intimacy_resume:%s" % permit_id
                record = self._tick(
                    ticked_b, cognition_b, now_iso=DAY5, prefixes=["v03_intimacy_resume:"]
                )
                self.assertEqual((record.get("due") or {}).get("status"), "ACTED", record)
                final_cp = _permit_rec(inst, permit_id)["checkpoint"]
                self.assertTrue(final_cp["consumed"])
                self.assertFalse(final_cp["resumable"])
                self.assertNotIn(resume_ref, _source_refs(_collect(inst, DAY5)))

                # ---------------- Day 6: 0.2 收尾 ----------------
                _seed_bookmark(inst, DAY6)
                _seed_plant(inst, DAY6)
                notice_id = _post_v04_notice(inst, DAY6)
                record = self._tick(ticked_b, cognition_b, now_iso=DAY6, prefixes=["plan:"])
                self.assertEqual((record.get("due") or {}).get("status"), "ACTED", record)
                plans_state, _reason = hearth_view.read_plans_state(inst)
                by_id = {row["plan_id"]: row for row in plans_state["plans"]}
                self.assertEqual(by_id["plan-hike"]["status"], "DROPPED")

                day6 = _collect(inst, DAY6)
                book = _settle(inst, _event_from(day6, "book:tide_almanac_notes", decision_id="w8-d6-book"), DAY6)
                self.assertEqual(book["status"], "APPLIED", book)
                plant = _settle(inst, _event_from(day6, "plant:mint-1", decision_id="w8-d6-plant"), DAY6)
                self.assertEqual(plant["status"], "APPLIED", plant)
                notice = _settle(
                    inst,
                    _event_from(day6, "v04_public_notice:%s" % notice_id, decision_id="w8-d6-notice"),
                    DAY6,
                )
                self.assertEqual(notice["status"], "APPLIED", notice)
                _conclude_inquiry(inst, inquiry_id, DAY6)

                # ---------------- Day 7: 静默收尾 + 只读全局视角 ----------------
                _seed_recall_state(inst, DAY7)
                _seed_private_common_ground(inst, DAY7)

                record = self._tick(ticked_b, cognition_b, now_iso=DAY7, prefixes=[])
                self.assertEqual((record.get("due") or {}).get("decision"), "NO_ACTION", record)

                before = _dir_snapshot(inst, ["phone", "memory", "state"])
                subject = recall.project_p12_autobiographical_narrative(
                    inst, has_subject_capability=True, now_iso=DAY7
                )
                partner = recall.project_p12_autobiographical_narrative(
                    inst, has_subject_capability=False, now_iso=DAY7
                )
                matrix = rm_engine.project_annual_decay_matrix_22(
                    inst, horizon_days=365, now_iso=DAY7
                )
                self.assertEqual(_dir_snapshot(inst, ["phone", "memory", "state"]), before)

                self.assertEqual(subject["perspective"], "subject")
                self.assertGreaterEqual(len(subject["chapters"]), 2)
                self.assertTrue(subject["turning_points"])
                attested = set(subject["attested_source_refs"])
                self.assertIn("common_ground:cg_umbrella", attested)
                self.assertIn("agency_depth:%s" % inquiry_id, attested)
                self.assertIn("workbench:artifact:draft-1", attested)

                partner_blob = json.dumps(partner, ensure_ascii=False)
                for secret in (
                    "PRIVATE_MEMORY_SECRET",
                    "PRIVATE_MEMORY_DETAIL",
                    "PRIVATE_GROUND_SECRET",
                    "PRIVATE_GROUND_DETAIL",
                ):
                    self.assertNotIn(secret, partner_blob)
                self.assertNotIn("recall:mem_private", partner["attested_source_refs"])
                self.assertEqual(partner["perspective"], "partner")

                self.assertEqual(matrix["total_categories"], 22)
                self.assertTrue(matrix["zero_write"])
                self.assertEqual(matrix["llm_calls"], 0)
                present = {row["category"] for row in matrix["categories"] if row["present"]}
                self.assertIn("courtyard_plant_care", present)
                self.assertIn("economic_spending_rhythm", present)
                self.assertIn("open_inquiries", present)

                # 七天总账：余额守恒（起始 + 赚取 − 购买）、对象数量一致。
                self.assertEqual(_balance(inst, DAY7), before_buy - 800)
                self.assertEqual(len(_v04_state(inst)["drying_items"]), 1)

                # 全周期产品模型 0 次（两次 epoch 的脚本替身都不是产品模型）。
                self.assertEqual(int(bridge_view_llm_calls(record)), 0)
            except Exception:
                ticked_b.stop()
                raise
        finally:
            try:
                ticked.stop()
            except Exception:
                pass


def bridge_view_llm_calls(record) -> int:
    view = record.get("v02_v03_bridge") or {}
    return int(view.get("llm_calls") or 0)


if __name__ == "__main__":
    unittest.main()

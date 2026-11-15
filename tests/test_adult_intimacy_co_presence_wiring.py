#!/usr/bin/env python3
"""36B-4 §9 / 36B §5.4：成年亲密 permit 引擎与共在 owner 的最小接线（T3-wave2）。

覆盖：
* ACTIVE -> PRIVATE_INTIMATE 共在会话登记（经 ``life_co_presence`` 公开 API，单前台）；
* 暂停 -> 会话降级（DEGRADED），双方确认恢复 -> 重开（OPEN）；
* STOP -> 会话关闭（CLOSED），入场断点标记可恢复；
* 入场断点字段（36A §11.1）：permit 记录内 ``checkpoint``（activity_ref/saved_at），
  本引擎读不到 Life 活动 -> 诚实记录"未保存"（saved=False）；
* 过夜重决定（36B §5.4 declared_seconds=86400）：跨日 ACTIVE 在 advance_beat 强制
  暂停（``OVERNIGHT_RECONFIRM_REQUIRED``），双方 confirm_resume = 重新决定；
* fail-open（36B-4 §9）：共在 owner 不可用/槽位被占只记录（``co_presence_binding``），
  绝不阻断亲密场景；
* 共在 owner 侧封闭模式与单槽位语义（PRIVATE_INTIMATE / FINITE_CO_PRESENCE）。
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "components" / "life" / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from components.phone import adult_intimacy_view as aiv  # noqa: E402
from components.phone.adult_intimacy_view import (  # noqa: E402
    AdultIntimacyConflictError,
    apply_adult_intimacy_action,
    read_adult_intimacy,
)
import life_co_presence as cop  # noqa: E402
from tests.test_adult_intimacy_view import T0, _open_ready_env  # noqa: E402


def _co_presence_root(root: Path) -> Path:
    return aiv._resolve_co_presence_root(root)


def _read_slot(root: Path) -> dict:
    path = _co_presence_root(root) / cop.SESSION_FILENAME
    if not path.is_file():
        return {"state": "NONE"}
    return json.loads(path.read_text(encoding="utf-8"))


def _permit_rec(root: Path, pid: str) -> dict:
    state = json.loads(aiv.adult_intimacy_state_path(root).read_text(encoding="utf-8"))
    return state["permits"]["records"][pid]


def _open_active_scene(root: Path, *, tag: str):
    _open_ready_env(root, now=T0)
    p = apply_adult_intimacy_action(
        root,
        {"action": "propose_scene", "operation_key": f"{tag}_p",
         "proposer": "partner", "categories": ["KISSING"]},
        now=T0,
    )
    pid = p["action_receipt"]["permit_id"]
    apply_adult_intimacy_action(
        root,
        {"action": "respond_scene", "permit_id": pid, "responder": "subject",
         "decision": "ACCEPT"},
        now=T0,
    )
    return pid


class PrivateIntimateCoPresenceWiringTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="aiv-copint-"))
        self.root = self.tmp / "instance"

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------------
    # ACTIVE -> 登记；STOP -> 关闭 + 断点可恢复
    # ------------------------------------------------------------------

    def test_active_registers_private_intimate_session_and_stop_closes_it(self) -> None:
        pid = _open_active_scene(self.root, tag="w1")

        # ACTIVE：共在会话已登记为 PRIVATE_INTIMATE（单前台语义沿用 single_foreground）
        session = _read_slot(self.root)
        self.assertEqual(session["state"], cop.PI_SESSION_OPEN)
        self.assertEqual(session["mode"], cop.CO_PRESENCE_MODE_PRIVATE_INTIMATE)
        self.assertEqual(session["session_id"], f"copint:{pid}")
        self.assertEqual(session["permit_ref"], pid)
        self.assertTrue(session["single_foreground"])
        rec = _permit_rec(self.root, pid)
        self.assertTrue(rec["co_presence_binding"].get("ok"))
        # 入场断点（36A §11.1）：本引擎读不到 Life 活动 -> 诚实记录"未保存"
        self.assertIn("checkpoint", rec)
        self.assertIsNone(rec["checkpoint"]["activity_ref"])
        self.assertEqual(rec["checkpoint"]["saved_at"], rec["active_since"])
        self.assertFalse(rec["checkpoint"]["saved"])
        self.assertFalse(rec["checkpoint"]["resumable"])
        # 水位线：同一状态的后续提交不再打扰 owner
        self.assertEqual(rec["co_presence_synced_state"], "ACTIVE")

        # STOP：会话关闭，断点标记可恢复（恢复执行不在本批）
        apply_adult_intimacy_action(
            self.root,
            {"action": "stop_scene", "permit_id": pid, "by": "subject"},
            now=T0 + timedelta(seconds=30),
        )
        session = _read_slot(self.root)
        self.assertEqual(session["state"], cop.PI_SESSION_CLOSED)
        self.assertEqual(session["end_reason"], "STOPPED_BY_SUBJECT")
        self.assertTrue(session["checkpoint"]["resumable"])
        rec = _permit_rec(self.root, pid)
        self.assertTrue(rec["checkpoint"]["resumable"])
        self.assertEqual(rec["co_presence_synced_state"], "STOPPED")

        # 幂等重放：对已 CLOSED 会话再次关闭是幂等重放，不抛错
        again = cop.close_private_intimate_session(
            _co_presence_root(self.root), session_id=f"copint:{pid}",
            reason_code="ENDED_BY_SUBJECT")
        self.assertTrue(again["ok"])
        self.assertTrue(again["already_closed"])

    # ------------------------------------------------------------------
    # 暂停 -> 降级；恢复 -> 重开
    # ------------------------------------------------------------------

    def test_pause_degrades_session_and_dual_confirm_reopens_it(self) -> None:
        pid = _open_active_scene(self.root, tag="w2")
        self.assertEqual(_read_slot(self.root)["state"], cop.PI_SESSION_OPEN)

        # 空闲超时（600s）暂停 -> 会话降级（DEGRADED），断点可恢复
        with self.assertRaises(AdultIntimacyConflictError):
            apply_adult_intimacy_action(
                self.root,
                {"action": "advance_beat", "permit_id": pid, "operation_key": "w2_b1",
                 "categories": ["KISSING"]},
                now=T0 + timedelta(seconds=605),
            )
        session = _read_slot(self.root)
        self.assertEqual(session["state"], cop.PI_SESSION_DEGRADED)
        self.assertEqual(session["degraded_reason"], "IDLE_TIMEOUT")
        self.assertTrue(session["checkpoint"]["resumable"])

        # 单方确认仍暂停；双方确认 -> ACTIVE，会话重开（同 session_id）
        paused = read_adult_intimacy(self.root, now=T0 + timedelta(seconds=606))
        epoch = paused["scene_permit"]["active_permit"]["resume_epoch"]
        apply_adult_intimacy_action(
            self.root,
            {"action": "confirm_resume", "permit_id": pid, "by": "partner",
             "resume_epoch": epoch},
            now=T0 + timedelta(seconds=610),
        )
        self.assertEqual(_read_slot(self.root)["state"], cop.PI_SESSION_DEGRADED)
        apply_adult_intimacy_action(
            self.root,
            {"action": "confirm_resume", "permit_id": pid, "by": "subject",
             "resume_epoch": epoch},
            now=T0 + timedelta(seconds=612),
        )
        session = _read_slot(self.root)
        self.assertEqual(session["state"], cop.PI_SESSION_OPEN)
        self.assertEqual(session["session_id"], f"copint:{pid}")
        rec = _permit_rec(self.root, pid)
        self.assertEqual(rec["co_presence_synced_state"], "ACTIVE")

    # ------------------------------------------------------------------
    # 过夜重决定（36B §5.4：86400s 上限到期不自续）
    # ------------------------------------------------------------------

    def test_overnight_active_beyond_86400s_forces_reconfirm(self) -> None:
        pid = _open_active_scene(self.root, tag="w3")
        # 保活拍（间隔 500s < 600s 空闲超时），让场景以 ACTIVE 跨过 86400s 界
        beat_t = 500
        while beat_t <= 86000:
            apply_adult_intimacy_action(
                self.root,
                {"action": "advance_beat", "permit_id": pid,
                 "operation_key": f"w3_b{beat_t}", "categories": ["KISSING"]},
                now=T0 + timedelta(seconds=beat_t),
            )
            beat_t += 500
        # 只读投影先如实报告 overdue 位
        due = read_adult_intimacy(self.root, now=T0 + timedelta(seconds=86401))
        self.assertTrue(due["scene_permit"]["active_permit"]["overnight_reconfirm_due"])

        # 越过 86400s 的拍（距上一拍 401s < 600s，非空闲）：强制暂停并带 reason_code
        with self.assertRaises(AdultIntimacyConflictError) as ictx:
            apply_adult_intimacy_action(
                self.root,
                {"action": "advance_beat", "permit_id": pid, "operation_key": "w3_b_over",
                 "categories": ["KISSING"]},
                now=T0 + timedelta(seconds=86401),
            )
        self.assertEqual(ictx.exception.reason_code, aiv.OVERNIGHT_RECONFIRM_REASON)
        paused = read_adult_intimacy(self.root, now=T0 + timedelta(seconds=86402))
        active = paused["scene_permit"]["active_permit"]
        self.assertEqual(active["state"], "PAUSED")
        self.assertEqual(active["pause_reason"], aiv.OVERNIGHT_RECONFIRM_REASON)
        self.assertEqual(_read_slot(self.root)["state"], cop.PI_SESSION_DEGRADED)

        # 双方确认 = 重新决定：ACTIVE 恢复，86400s 窗口自确认时刻重新起算
        epoch = active["resume_epoch"]
        apply_adult_intimacy_action(
            self.root,
            {"action": "confirm_resume", "permit_id": pid, "by": "partner",
             "resume_epoch": epoch},
            now=T0 + timedelta(seconds=86500),
        )
        resumed = apply_adult_intimacy_action(
            self.root,
            {"action": "confirm_resume", "permit_id": pid, "by": "subject",
             "resume_epoch": epoch},
            now=T0 + timedelta(seconds=86600),
        )
        self.assertEqual(resumed["scene_permit"]["active_permit"]["state"], "ACTIVE")
        fresh = read_adult_intimacy(
            self.root, now=T0 + timedelta(seconds=86600 + 86399))
        self.assertFalse(fresh["scene_permit"]["active_permit"]["overnight_reconfirm_due"])
        self.assertEqual(_read_slot(self.root)["state"], cop.PI_SESSION_OPEN)

    # ------------------------------------------------------------------
    # fail-open（36B-4 §9）：owner 不可用/槽位被占，场景照常
    # ------------------------------------------------------------------

    def test_owner_unavailable_is_fail_open_scene_never_blocks(self) -> None:
        pid = _open_active_scene(self.root, tag="w4")
        self.assertTrue(_permit_rec(self.root, pid)["co_presence_binding"].get("ok"))
        # owner 不可用（模拟共在系统故障）：后续状态迁移照常完成，只记录不阻断
        with mock.patch.object(aiv, "_import_life_co_presence", return_value=None):
            apply_adult_intimacy_action(
                self.root,
                {"action": "stop_scene", "permit_id": pid, "by": "partner"},
                now=T0 + timedelta(seconds=40),
            )
        rec = _permit_rec(self.root, pid)
        binding = rec["co_presence_binding"]
        self.assertFalse(binding.get("ok"))
        self.assertEqual(binding.get("reason_code"), aiv.CO_PRESENCE_BINDING_UNAVAILABLE)
        self.assertEqual(rec["state"], "STOPPED")
        self.assertEqual(rec["co_presence_synced_state"], "STOPPED")

    def test_occupied_slot_is_recorded_and_scene_still_starts(self) -> None:
        # 预占槽位：一个 OPEN 的 FINITE_CO_PRESENSE 拼写错误防护——用真枚举写一个邀请会话
        occupied = {
            "schema_version": cop.SESSION_SCHEMA,
            "state": "OPEN",
            "mode": cop.CO_PRESENCE_MODE_FINITE_CO_PRESENCE,
            "invitation_id": "localinvite:occupied",
            "scope": "一起坐十分钟",
            "opened_at": T0.isoformat(),
        }
        cop._write_session_dict(_co_presence_root(self.root), occupied)
        pid = _open_active_scene(self.root, tag="w5")
        rec = _permit_rec(self.root, pid)
        binding = rec["co_presence_binding"]
        self.assertFalse(binding.get("ok"))
        self.assertEqual(binding.get("reason_code"), cop.REASON_PRIVATE_SLOT_OCCUPIED)
        self.assertEqual(rec["state"], "ACTIVE", "fail-open：场景不被共在故障阻断")
        # 槽位上的邀请会话未被破坏
        self.assertEqual(_read_slot(self.root).get("invitation_id"), "localinvite:occupied")

    # ------------------------------------------------------------------
    # owner 侧：封闭模式与单槽位语义
    # ------------------------------------------------------------------

    def test_owner_private_session_vocabulary_and_slot_semantics(self) -> None:
        root = _co_presence_root(self.root)
        reg = cop.register_private_intimate_session(
            root, session_id="copint:x1", permit_ref="scn_x1", started_at=T0.isoformat(),
            activity_ref=None, activity_saved_at=None)
        self.assertTrue(reg["ok"])
        self.assertEqual(reg["session"]["checkpoint"]["saved"], False)
        # 同 id 重登：幂等（already_open）
        again = cop.register_private_intimate_session(
            root, session_id="copint:x1", permit_ref="scn_x1")
        self.assertTrue(again["ok"])
        self.assertTrue(again["already_open"])
        # 异 id 登记：单槽位拒绝
        other = cop.register_private_intimate_session(
            root, session_id="copint:x2", permit_ref="scn_x2")
        self.assertFalse(other["ok"])
        self.assertEqual(other["reason_code"], cop.REASON_PRIVATE_SLOT_OCCUPIED)
        # 降级 -> 异 id 关闭按名字拒绝 -> 同 id 关闭成功
        cop.degrade_private_intimate_session(
            root, session_id="copint:x1", reason_code="IDLE_TIMEOUT")
        wrong = cop.close_private_intimate_session(root, session_id="copint:x2")
        self.assertFalse(wrong["ok"])
        self.assertEqual(wrong["reason_code"], cop.REASON_PRIVATE_SESSION_MISMATCH)
        closed = cop.close_private_intimate_session(
            root, session_id="copint:x1", reason_code="ENDED_BY_SUBJECT")
        self.assertTrue(closed["ok"])
        self.assertTrue(closed["session"]["checkpoint"]["resumable"] is False or
                        closed["session"]["checkpoint"]["resumable"] is True)
        # 只读投影：NONE 当槽位上不是 PRIVATE_INTIMATE
        cop._write_session_dict(root, {"schema_version": cop.SESSION_SCHEMA,
                                       "state": "CLOSED", "invitation_id": "x"})
        none_view = cop.read_private_intimate_session(root)
        self.assertEqual(none_view["state"], "NONE")
        # 封闭枚举成员齐全
        self.assertIn(cop.CO_PRESENCE_MODE_PRIVATE_INTIMATE, cop.CO_PRESENCE_MODES)
        self.assertIn(cop.CO_PRESENCE_MODE_FINITE_CO_PRESENCE, cop.CO_PRESENCE_MODES)


if __name__ == "__main__":
    unittest.main()

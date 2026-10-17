"""W05-owner-unknown：模糊提交（UNCERTAIN）不得落成终态 blocked。

实证缺陷（fixture /tmp/nyaw05r.jdK3q/w05inst-fgon6ibm，未改动）：
``WorldBodyClient.submit_action`` 的 2.0s socket 超时把一次**已经提交**的 MOVE
报成 ``transport_fault:TIMEOUT`` → 端口 ``uncertain`` → ``world_journey`` 把
``owner_status != "applied"`` 直接写成终态 ``blocked``，于是 owner 账本里明明
committed/move_departed，旅程却再也推不动（blocked 是终态）。

本文件只测这条边界，正负都精确：

* 端口 uncertain + 没有任何证据 → 旅程 ``unknown``（非终态），同一次调用内不再提交；
* 后续推进只对账，**不**再次提交（submit 调用数恒为 1）；
* 同 execution_id 的 travel 记录出现 → 认领为 started（不认到达、不换身份）；
* 错 execution / 错 origin、destination → 不认领；
* 真 rejected / disabled / 构建失败 → 仍然 blocked；
* pause 之后仍然可以认领已出发的段，但不启动新段。

需要 POSIX（fcntl 文件锁）；原生 Windows 自动跳过。
"""

from __future__ import annotations

import sys
import unittest
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "tests"),
              str(ROOT / "components" / "world" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from chiyo_bundle import world_control  # noqa: E402
from chiyo_bundle import world_journey as journey  # noqa: E402
from test_world_journey import JourneyTestCase, posix_only  # noqa: E402

#: 端口在"回执丢了"时给世界引擎的唯一用词（world_journey_action_adapter:608）。
UNCERTAIN = {"status": "uncertain", "reason_code": "UNKNOWN",
             "detail": "transport_fault:TIMEOUT", "formal": True}


class JourneyPort:
    """剧本化的 submit_port：记账每一次提交，返回值由 behavior 决定。"""

    def __init__(self, behavior=None):
        self.behavior = behavior or (lambda **kwargs: dict(UNCERTAIN))
        self.calls: list[dict] = []

    @property
    def submissions(self) -> int:
        return len(self.calls)

    def submit_move(self, *, destination, origin, execution_id, hermes_home,
                    environment, now=None):
        self.calls.append({"destination": destination, "origin": origin,
                           "execution_id": execution_id, "at": now})
        return self.behavior(destination=destination, origin=origin,
                             execution_id=execution_id, hermes_home=hermes_home,
                             environment=environment, now=now)


@posix_only
class AmbiguousSubmissionTest(JourneyTestCase):
    """模糊提交的正负边界。"""

    # -- 夹具 ---------------------------------------------------------------

    def accept_with_port(self, target, port, *, world=None, now=None,
                         request_key=None) -> dict:
        world = world or self.world
        return journey.accept_journey(
            target=target, hermes_home=world.home, environment=world.env,
            now=now or self.now, request_key=request_key, submit_port=port,
        )

    def advance_with_port(self, port, *, world=None, now=None) -> dict:
        world = world or self.world
        return journey.advance_journey(
            hermes_home=world.home, environment=world.env, now=now or self.now,
            submit_port=port,
        )

    def active(self, world=None) -> dict:
        return self.journey_state(world)["active"]

    def segment_execution_id(self, index=0, world=None) -> str:
        return self.active(world)["segments"][index]["execution_id"]

    def start_matching_travel(self, *, execution_id, origin, destination,
                              world=None, now=None) -> dict:
        world = world or self.world
        started = self.wtd.start_travel(
            execution_id=execution_id, origin_location_id=origin,
            destination_location_id=destination, hermes_home=world.home,
            environment=world.env, now=now or self.now,
        )
        self.assertEqual(started["status"], "started", started)
        return started

    # -- 1. 没有证据：unknown，并且不允许重发 -------------------------------

    def test_ambiguous_submission_without_evidence_is_unknown_never_blocked(self):
        world = self.world
        s1 = world.ids["s1"]
        port = JourneyPort()
        revision_before = self.world_revision()

        result = self.accept_with_port(world.ids["s4"], port)

        # 不写终态：blocked 会让一次可能已发生的出发永久锁死。
        self.assertEqual(result["status"], "unknown", result)
        self.assertNotEqual(result["status"], "blocked")
        self.assertTrue(str(result["reason_code"]).startswith(
            "SEGMENT_SUBMIT_UNCERTAIN"), result["reason_code"])
        # 端口的 failure detail 没有被压成笼统的 UNKNOWN。
        self.assertIn("transport_fault:TIMEOUT", result["reason_code"])
        self.assertEqual(port.submissions, 1)
        self.assertEqual(len(port.calls), 1)
        # owner 侧没有被本模块写脏：位置仍在起点，没有 travel 记录。
        self.assertEqual(self.position(), "home")
        self.assertEqual(self.travels(), [])
        self.assertEqual(self.world_revision(), revision_before)

        stored = self.active()
        self.assertEqual(stored["status"], "unknown")
        self.assertEqual(stored["segments"][0]["status"], "pending")
        self.assertIsNone(stored["segments"][0]["travel_id"])
        self.assertTrue(stored["error"]["reason_code"].startswith(
            "SEGMENT_SUBMIT_UNCERTAIN"))
        self.assertEqual(stored["segments"][0]["destination_location_id"], s1)
        # 只读观察如实报告同一个非终态原因。
        observed = self.observe()
        self.assertEqual(observed["status"], "unknown")
        self.assertTrue(str(observed["reason_code"]).startswith(
            "SEGMENT_SUBMIT_UNCERTAIN"))

    def test_repeated_advances_reconcile_only_and_never_resubmit(self):
        world = self.world
        port = JourneyPort()
        self.assertEqual(self.accept_with_port(world.ids["s4"], port)["status"],
                         "unknown")
        stored_before = self.active()

        first = self.advance_with_port(port, now=self.now + timedelta(seconds=5))
        second = self.advance_with_port(port, now=self.now + timedelta(seconds=10))

        self.assertEqual(first["status"], "unknown", first)
        self.assertEqual(second["status"], "unknown", second)
        # 关键：两次推进都没有再提交，也没有换 identity。
        self.assertEqual(port.submissions, 1)
        self.assertEqual(len(self.travels()), 0)
        stored = self.active()
        self.assertEqual(stored["segments"][0]["execution_id"],
                         stored_before["segments"][0]["execution_id"])
        self.assertEqual(stored["status"], "unknown")
        self.assertEqual(self.position(), "home")

    # -- 2. 同 execution_id 的证据可以认领（不认到达） ----------------------

    def test_an_uncertain_submit_claims_the_same_execution_travel_record(self):
        world = self.world
        s1 = world.ids["s1"]
        port = JourneyPort()

        def behavior(*, destination, origin, execution_id, hermes_home,
                     environment, now=None):
            # owner 真的出发了，只是回执丢了：用同一个 execution id 起程。
            self.start_matching_travel(execution_id=execution_id, origin=origin,
                                       destination=destination, now=now)
            return dict(UNCERTAIN)

        port.behavior = behavior
        result = self.accept_with_port(world.ids["s4"], port)

        self.assertEqual(result["status"], "started", result)
        self.assertEqual(result["reason_code"], "TRAVEL_STARTED")
        self.assertEqual(result["destination_location_id"], s1)
        self.assertEqual(port.submissions, 1)
        records = self.travels()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["execution_id"], port.calls[0]["execution_id"])

        stored = self.active()
        segment = stored["segments"][0]
        # 认领 = started，且时间/身份全部来自 owner 自己的记录。
        self.assertEqual(segment["status"], "started")
        self.assertEqual(segment["travel_id"], records[0]["travel_id"])
        self.assertEqual(segment["started_at"], records[0]["started_at"])
        self.assertEqual(segment["expected_arrival_at"],
                         records[0]["expected_arrival_at"])
        self.assertEqual(stored["status"], "in_transit")
        self.assertIsNone(stored["error"])
        # 认领不是到达：位置还在起点。
        self.assertEqual(self.position(), "home")

        # 下一次推进只结算这一段；下一段是**新的** execution id，不是重发。
        settled = self.advance_with_port(port, now=self.now + timedelta(seconds=300))
        self.assertEqual(settled["segments_confirmed"], 1, settled)
        self.assertEqual(self.position(), s1)
        self.assertEqual(port.submissions, 2)
        self.assertEqual(port.calls[0]["execution_id"], records[0]["execution_id"])
        self.assertNotEqual(port.calls[1]["execution_id"],
                            port.calls[0]["execution_id"])
        self.assertEqual(port.calls[1]["execution_id"],
                         self.active()["segments"][1]["execution_id"])

    def test_a_mismatched_execution_id_is_never_claimed(self):
        world = self.world
        s1 = world.ids["s1"]
        port = JourneyPort()

        def behavior(*, destination, origin, execution_id, hermes_home,
                     environment, now=None):
            # 别人的在途：id 不同，起终点也不同。
            self.start_matching_travel(execution_id="someone-else-execution",
                                       origin=s1, destination=world.ids["s2"],
                                       now=now)
            return dict(UNCERTAIN)

        port.behavior = behavior
        result = self.accept_with_port(world.ids["s4"], port)

        self.assertEqual(result["status"], "blocked", result)
        self.assertEqual(result["reason_code"], "EXTERNAL_TRAVEL_IN_PROGRESS")
        self.assertEqual(port.submissions, 1)
        stored = self.active()
        self.assertEqual(stored["status"], "blocked")
        self.assertEqual(stored["segments"][0]["status"], "pending")
        self.assertIsNone(stored["segments"][0]["travel_id"])

    def test_a_same_id_record_with_the_wrong_origin_is_not_claimed(self):
        world = self.world
        s1, s2 = world.ids["s1"], world.ids["s2"]
        port = JourneyPort()

        def behavior(*, destination, origin, execution_id, hermes_home,
                     environment, now=None):
            # execution id 对得上，但 payload 不是这一段（错 origin/destination）。
            self.start_matching_travel(execution_id=execution_id, origin=s1,
                                       destination=s2, now=now)
            return dict(UNCERTAIN)

        port.behavior = behavior
        result = self.accept_with_port(world.ids["s4"], port)

        # 不认领、也不当成"真拒绝"：保持 unknown 等待对账证据。
        self.assertEqual(result["status"], "unknown", result)
        self.assertEqual(result["reason_code"],
                         "SEGMENT_SUBMIT_UNCERTAIN:transport_fault:TIMEOUT")
        stored = self.active()
        self.assertEqual(stored["segments"][0]["status"], "pending")
        self.assertIsNone(stored["segments"][0]["travel_id"])
        self.assertEqual(port.submissions, 1)

    # -- 3. 明确失败仍然是终态 ---------------------------------------------

    def test_an_explicit_rejection_still_blocks(self):
        world = self.world
        port = JourneyPort(lambda **kwargs: {"status": "rejected",
                                             "reason_code": "LOCATION_CLOSED"})
        result = self.accept_with_port(world.ids["s4"], port)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason_code"], "LOCATION_CLOSED")
        self.assertEqual(self.active()["status"], "blocked")
        # 终态不再命令 owner。
        again = self.advance_with_port(port, now=self.now + timedelta(seconds=30))
        self.assertEqual(again["status"], "blocked")
        self.assertEqual(port.submissions, 1)

    def test_a_disabled_or_pre_submit_failure_still_blocks(self):
        for moved in ({"status": "disabled", "reason_code": "TRAVEL_TIME_DISABLED"},
                      {"status": "unavailable",
                       "reason_code": "INTENT_BUILD_FAILED",
                       "detail": "ActionIntentError: bad shape"}):
            with self.subTest(status=moved["status"]):
                world = self.make_world(prefix="w05u-neg-")
                port = JourneyPort(lambda **kwargs: dict(moved))
                result = self.accept_with_port(world.ids["s4"], port, world=world)
                self.assertEqual(result["status"], "blocked", result)
                self.assertEqual(result["reason_code"], moved["reason_code"])
                self.assertEqual(self.active(world)["status"], "blocked")
                self.assertEqual(self.travels(world), [])
                self.assertEqual(port.submissions, 1)

    # -- 4. pause：可以认领已出发，但不启动新段 ----------------------------

    def test_a_paused_journey_claims_a_departed_segment_but_starts_nothing(self):
        world = self.world
        s1 = world.ids["s1"]
        port = JourneyPort()
        self.assertEqual(self.accept_with_port(world.ids["s4"], port)["status"],
                         "unknown")
        execution_id = self.segment_execution_id()

        # owner 的出发其实已经落账（迟到的证据），随后世界被暂停。
        self.start_matching_travel(execution_id=execution_id, origin="home",
                                   destination=s1, now=self.now)
        with journey.journey_lock(world.home):
            world_control.set_paused(world.home, paused=True, now=self.now,
                                     reason="user_pause")

        claim = self.advance_with_port(port, now=self.now + timedelta(seconds=10))
        # 认领已出发的段是允许的（不是"新出发"）。
        self.assertEqual(claim["status"], "in_transit", claim)
        self.assertEqual(claim["segment_status"], "started", claim)
        self.assertEqual(port.submissions, 1)
        self.assertEqual(self.active()["segments"][0]["status"], "started")

        # 到达后确认这一段可以，但下一段必须被 pause 挡住。
        settled = self.advance_with_port(port, now=self.now + timedelta(seconds=300))
        self.assertEqual(settled["segments_confirmed"], 1, settled)
        self.assertEqual(settled["status"], "paused", settled)
        self.assertEqual(settled["destination_location_id"], world.ids["s2"])
        self.assertEqual(port.submissions, 1)
        self.assertEqual(len(self.travels()), 1)
        self.assertEqual(self.position(), s1)

        again = self.advance_with_port(port, now=self.now + timedelta(seconds=400))
        self.assertEqual(again["status"], "paused", again)
        self.assertEqual(port.submissions, 1)
        self.assertEqual(len(self.travels()), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

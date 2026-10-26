#!/usr/bin/env python3
"""v0.4 分享走正常生活入口：真实实例装配 + 正式 ``evaluate_once`` + 原始 JSON 决策。

这条链**不**调用 ``cycle._apply_sharing_action``，也不构造 SimpleNamespace 决策：
它用组合 D 的真实实例（真实 Supply owner 的 socket、真实 AR-0/AG-2/runtime、真实
``assemble_autonomous_life``），注入一个只回放 **原始 JSON** 的 cognition 脚本（与
``test_lived_day_chain_v04.ChainCognition`` 同一做法），每一轮都走
``cycle.evaluate_once(trigger_ref=..., observed_at=..., act=True)``。

覆盖：
1. 源作品（这个实例自己的 work artifact）→ 正式 Decision 带 ``sharing.prepare`` →
   owner 造出 DRAFT（≠ 已发）→ 这一轮**不能**被报成 NO_ACTION（有副作用），也不写原作品；
2. 下一轮正式 Decision 带 ``sharing.defer``（写明 opportunity_ref）→ DEFERRED；
3. 声明的机会到了（脚本声明，不是世界自动给的）→ 正式 Decision 带 ``sharing.send_local``
   → ``LOCAL_DELIVERED``，本地投递 1 条 receipt，再读仍是同一条；
4. 分享件不会被当成普通工作续写（不在 project options / 本轮 step 目标里）；
5. 决策模型在 prompt 里真的读到草稿正文（有界）、来源版本/来源是否仍当前、以及延后条件。

夹具：组合 D 的真实实例 + 真实 Supply socket；**没有**外发、没有真实模型、没有删除夹具。
"""

from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
for entry in (str(ROOT),
              str(TESTS),
              str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import agency_cognition_adapter as aa  # noqa: E402
import agency_decision_ag1 as ag1  # noqa: E402
import autonomous_life_assembly as assembly  # noqa: E402
import life_sharing_context as lsc  # noqa: E402
import life_sharing_service as lss  # noqa: E402
import one_day_life as odl  # noqa: E402

POSIX_ONLY = unittest.skipUnless(sys.platform != "win32", "real Supply socket needs POSIX")

try:
    import test_life_v1_integration as donor  # noqa: E402
    from test_world_daily_life_continuous import (  # noqa: E402
        WORLD_PACKAGE, Clock, episode_instant,
    )
    from world_body_client import WorldBodyClient  # noqa: E402
    import world_move_opportunity_source as wmos  # noqa: E402
    from world_journey_action_adapter import (  # noqa: E402
        JourneyWorldResolver, RealJourneyWorldActionAdapter,
    )

    _IMPORT_ERROR: str | None = None
except Exception as error:  # noqa: BLE001
    _IMPORT_ERROR = "%s: %s" % (type(error).__name__, error)

needs_donor = unittest.skipIf(_IMPORT_ERROR is not None,
                              "donor fixture unavailable: %s" % (_IMPORT_ERROR or ""))

BRIDGE_DIR = ROOT / "components" / "world" / "service" / "bridge"
SHARE_TEXT = "今天把那页留白重排完了，第二段还是挤，但这样更像当天。"
SECOND_SHARE_TEXT = "这句话先留在这里，等一个安静的时候再说。"


class SourceRefusingPort:
    """The real owner port, except one **source** artifact read is refused.

    This is the documented governed-source shape (``ok=False`` + the owner's own reason
    code).  Everything else -- the share artifact itself, listings -- goes to the real port
    unchanged, so the formal round still runs against the real owner.
    """

    def __init__(self, inner, *, source_id, status: str = "rejected",
                 reason_code: str = "SOURCE_GOVERNED") -> None:
        self._inner = inner
        self._source = str(source_id or "").replace("artifact:", "")
        self._status = status
        self._reason = reason_code

    def read_project(self, artifact_id, **kwargs):
        if str(artifact_id or "").replace("artifact:", "") == self._source:
            return {"ok": False, "status": self._status, "reason_code": self._reason,
                    "detail": "the owner refused this source read"}
        return self._inner.read_project(artifact_id, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class SharingCognition:
    """Raw-JSON decision script: it reads the situation block and answers like a model would.

    It never computes an id: the share id / CAS head it names are the ones the real prompt carried.
    """

    model_id = "stub:sharing-life-chain"
    model_version = "sharing-life-chain-v1"

    def __init__(self) -> None:
        self.plan: list[str] = []
        self.reads: list[dict] = []
        self.calls = 0

    def invoke(self, request, *, schema_retry_index: int = 0):
        self.calls += 1
        allowed = [str(code) for code in (getattr(request, "allowed_reason_codes", []) or [])]
        situation = getattr(request, "situation_context", {}) or {}
        block = situation.get("sharing") or {}
        shares = list(block.get("shares") or [])
        sources = list(block.get("sources") or [])
        step = self.plan.pop(0) if self.plan else ""
        raw: dict = {"decision": "NO_ACTION", "selected_candidate_id": None,
                     "reason_codes": allowed[:1] or ["NO_ACTION"],
                     "short_rationale": "quiet round", "deferred_candidate_ids": []}
        if step in ("prepare", "prepare_second") and sources:
            # The owner's operation key covers source head + text + title, so a second draft
            # needs its own words -- replaying the first one would be refused as the same op.
            raw["sharing"] = {"op": "prepare",
                              "source_artifact_id": sources[0]["artifact_id"],
                              "share_text": (SHARE_TEXT if step == "prepare"
                                             else SECOND_SHARE_TEXT),
                              "title": ("留白重排" if step == "prepare" else "再想一句")}
        elif step == "defer" and shares:
            share = shares[0]
            raw["sharing"] = {"op": "defer", "share_id": share["share_id"],
                              "expected_head": share["head_version_id"],
                              "opportunity_ref": "quiet-evening"}
        elif step == "send_local" and shares:
            share = shares[0]
            raw["sharing"] = {"op": "send_local", "share_id": share["share_id"],
                              "expected_head": share["head_version_id"]}
        self.reads.append({"step": step, "sharing": json.loads(json.dumps(block)),
                           "raw_sharing": raw.get("sharing")})
        return ag1.CognitiveDecisionResponse(
            model_run_ref="model_run:share-chain:%d" % self.calls, model_id=self.model_id,
            model_version=self.model_version, raw_output=raw, latency_ms=1)


@POSIX_ONLY
@needs_donor
class SharingLifeChainTest(unittest.TestCase):
    """一个实例、一条链：prepare → defer → send_local（都走正式 Decision）。"""

    def setUp(self) -> None:
        self.base = donor.DScenarioBase("runTest")
        self.base.setUp()
        self.addCleanup(self._close)
        self.base.world.package_dir = WORLD_PACKAGE
        self.prepared = self.base.prepare_scenario(keep_fixture_world=True)
        self.data = self.prepared.data
        self.memory = self.prepared.memory
        self.world_home = Path(str(self.data["world_home"]))
        self.instance = Path(self.base.instance)
        self.clock = Clock(episode_instant(after=datetime.now().astimezone()))
        self.gateway = self.base.gateway()
        self.env = self.base.instance_env()
        self.provider = wmos.WorldMoveOpportunityProvider(
            client=WorldBodyClient(environment=self.env),
            action_intent=__import__("action_intent"), bridge_dir=str(BRIDGE_DIR),
            environment=self.env, world_home=str(self.world_home),
            now=lambda: self.clock.now, observed_now=lambda: self.clock.now,
            valid_for_seconds=60)
        self.cognition = SharingCognition()
        self.runtime, self.port = self.base.runtime_with_memory(
            self.cognition, self.env, self.data, self.memory)
        self.fixture = self.base.assemble(self.runtime, self.env, observation=self.provider)
        self.cycle = self.fixture.cycle
        self.cycle.world_home = str(self.world_home)
        self.cycle.work_artifact_id = self.data["work_artifact_id"]
        self.state_root = odl.life_state_root(self.instance)
        self.resolver = self.runtime.world_adapter.resolver
        self.assertIsInstance(self.runtime.world_adapter, RealJourneyWorldActionAdapter)

    def _close(self) -> None:
        try:
            self.base.doCleanups()
        except Exception:  # noqa: BLE001 - cleanup of a fixture is not a test result
            pass

    # -- one formal round ---------------------------------------------------------
    def turn(self, step: str, *, trigger: str) -> dict:
        self.cognition.plan = [step]
        return self.cycle.evaluate_once(trigger_ref=trigger, observed_at=self.clock.iso(),
                                        act=True)

    def work_head(self) -> str:
        return str(self.base.head(self.data["work_artifact_id"])["version_id"])

    def all_share_ids(self) -> list[str]:
        answer = self.fixture.assembled["cycle"].sharing_service.list_shares(
            include_text=False)
        self.assertTrue(answer["ok"], answer)
        return [row["share_id"] for row in answer["data"]["shares"]]

    # -- the one chain -------------------------------------------------------------
    def test_prepare_defer_and_one_local_delivery_through_formal_decisions(self):
        wiring = self.fixture.assembled.get("sharing") or {}
        self.assertEqual(wiring.get("status"), "READY", wiring)
        service = self.cycle.sharing_service
        self.assertIsNotNone(service, "assembly must hand the real sharing service to the cycle")

        work_before = self.work_head()
        self.assertEqual(self.all_share_ids(), [])

        # 1) 正式 Decision：源作品 → prepare。草稿 ≠ 已发，且这一轮不许报成 NO_ACTION。
        first = self.turn("prepare", trigger="share:prepare")
        self.assertEqual(first.get("decision"), ag1.DECISION_NO_ACTION, first)
        share = first.get("sharing") or {}
        self.assertTrue(share.get("ok"), share)
        self.assertEqual(share.get("state"), lss.STATE_DRAFT, share)
        self.assertFalse(share.get("delivered"))
        self.assertEqual(first.get("outcome"), "SHARE_ACTED", first)
        self.assertTrue(first.get("sharing_outcome_note"))
        share_id = share["share_id"]
        # 原作品没有被这次分享写入：分享是另一件 artifact。
        self.assertEqual(self.work_head(), work_before)
        self.assertNotEqual(share_id, self.data["work_artifact_id"])
        # 模型真的读到了草稿正文与来源版本（不是只有 id）。
        prompt_block = self.cognition.reads[-1]["sharing"] or {}
        self.assertEqual(prompt_block.get("channel"), "local_inbox")
        source_ids = {row["artifact_id"] for row in prompt_block.get("sources") or []}
        self.assertIn(self.data["work_artifact_id"], source_ids)
        # v0.5: the owner's own "this list was cut" fact survives the prompt whitelist (here the
        # list really was not cut, so the fact is an explicit False -- never silently absent).
        self.assertIs(prompt_block.get("truncated"), False)
        self.assertIs(aa._bounded_sharing(prompt_block).get("truncated"), False)

        # 2) 正式 Decision：defer（写明她在等什么）。下一轮的 prompt 里必须带着这句话。
        second = self.turn("defer", trigger="share:defer")
        deferred = second.get("sharing") or {}
        self.assertTrue(deferred.get("ok"), deferred)
        self.assertEqual(deferred.get("state"), lss.STATE_DEFERRED, deferred)
        self.assertEqual((deferred.get("deferred") or {}).get("opportunity_ref"),
                         "quiet-evening")
        self.assertEqual(second.get("outcome"), "SHARE_ACTED", second)
        self.assertEqual(self.work_head(), work_before)
        # 3) 读一眼不写：决策轮的读不为任何 share 造版本（DEFERRED 不会每 tick 被写）。
        head_before = lsc.read_draft(service, share_id)["head_version_id"]
        self.cycle.sharing_decision_context()
        self.assertEqual(lsc.read_draft(service, share_id)["head_version_id"], head_before)

        # 4) 声明的机会到了（脚本声明的机会，不是世界自动给的）→ 正式 Decision send_local。
        third = self.turn("send_local", trigger="share:send")
        # 第 3 轮（send 之前）的 prompt 必须带着 DEFERRED 状态、草稿正文、来源版本与她的等待条件。
        block = self.cognition.reads[-1]["sharing"] or {}
        row = [item for item in block.get("shares") or []
               if item.get("share_id") == share_id][0]
        self.assertEqual(row.get("state"), lss.STATE_DEFERRED)
        self.assertTrue(row.get("text_excerpt"), row)       # 草稿正文进 prompt（有界）
        self.assertIn("留白", row.get("text_excerpt"))
        self.assertTrue(row.get("source_current", {}).get("matches_recorded"), row)
        self.assertEqual((row.get("deferred") or {}).get("opportunity_ref"), "quiet-evening")
        sent = third.get("sharing") or {}
        self.assertTrue(sent.get("ok"), sent)
        self.assertEqual(sent.get("state"), lss.STATE_LOCAL_DELIVERED, sent)
        self.assertTrue(sent.get("delivered"))
        self.assertEqual(sent.get("delivery_count"), 1)
        receipt = sent.get("receipt") or {}
        self.assertTrue(receipt.get("local_only"))
        self.assertFalse(receipt.get("external_channel_sent"))
        self.assertFalse(receipt.get("external_delivery_confirmed"))
        self.assertFalse(receipt.get("read_by_audience"))
        self.assertEqual(third.get("outcome"), "SHARE_ACTED", third)
        self.assertEqual(self.work_head(), work_before)

        # 再读：还是同一条本地投递，不是第二条
        again = lsc.read_draft(service, share_id)
        self.assertEqual(again["state"], lss.STATE_LOCAL_DELIVERED)
        self.assertEqual(again["receipt"]["delivery_id"], receipt["delivery_id"])
        # 已投递的分享不再出现在"可操作"列表里（终态不重开），但它本身仍然可读。
        self.assertEqual(lsc.prompt_block(service)["shares"], [])

        # 5) 分享件不会被当成普通工作续写。
        options = self.cycle.project_options(now=self.clock.iso())
        flatten = json.dumps(options, ensure_ascii=False)
        self.assertNotIn(share_id.replace("artifact:", ""), flatten)
        self.assertNotEqual(third.get("step_work_target"), share_id)
        self.assertEqual(self.cycle.work_artifact_id, self.data["work_artifact_id"])

    def test_a_governed_source_reaches_the_formal_round_and_the_owner_refuses_the_send(self):
        """The withholding fact must travel through the *formal* round, not just a helper.

        One draft is prepared while the source is readable.  Then the recorded source becomes
        unreadable (source-only read refusal).  A full ``evaluate_once`` round then has to show
        the decision the withheld words and the owner's own reason code, and when the scripted
        decision still asks to send, the owner refuses and nothing is written.
        """
        service = self.cycle.sharing_service
        first = self.turn("prepare", trigger="share:prepare")
        share = first.get("sharing") or {}
        self.assertTrue(share.get("ok"), share)
        self.assertEqual(share.get("state"), lss.STATE_DRAFT, share)
        share_id = share["share_id"]
        head = lsc.read_draft(service, share_id)["head_version_id"]

        governed = lss.LifeSharingService(
            SourceRefusingPort(service.projects, source_id=self.data["work_artifact_id"]),
            clock=service._clock)
        self.cycle.sharing_service = governed

        refused = self.turn("send_local", trigger="share:send-governed")
        # The decision prompt itself carries the withholding (bounded, no invented excerpt).
        block = self.cognition.reads[-1]["sharing"] or {}
        row = [item for item in block.get("shares") or []
               if item.get("share_id") == share_id][0]
        self.assertIs(row.get("text_withheld"), True)
        self.assertIs(row.get("text_available"), False)
        self.assertTrue(row.get("withheld_reason_code"))
        self.assertNotEqual(row.get("withheld_reason_code"), "SHARING_SOURCE_NOT_FOUND")
        self.assertFalse(row.get("text_excerpt"))  # no words, and the flag says why
        # The adapter's prompt whitelist, fed this round's own block, keeps the withholding and
        # never turns it into an empty excerpt.
        bounded = aa._bounded_sharing(block)
        braw = [item for item in bounded.get("shares") or []
                if item.get("share_id") == share_id][0]
        self.assertIs(braw.get("text_withheld"), True)
        self.assertIs(braw.get("text_available"), False)
        self.assertNotIn("text_excerpt", braw)
        self.assertTrue(self.cognition.reads[-1]["raw_sharing"], "the script really asked to send")

        # The owner refuses; the round writes nothing and reports no delivery.
        sent = refused.get("sharing") or {}
        self.assertFalse(sent.get("ok"), sent)
        self.assertFalse(sent.get("delivered"))
        self.assertNotEqual(refused.get("outcome"), "SHARE_ACTED", refused)
        after = lsc.read_draft(service, share_id)
        self.assertEqual(after["head_version_id"], head)
        self.assertEqual(after["state"], lss.STATE_DRAFT)
        self.assertIsNone(after["receipt"])

    def test_a_refused_sharing_request_is_not_reported_as_a_side_effect(self):
        first = self.turn("prepare", trigger="share:prepare")
        self.assertEqual(first.get("outcome"), "SHARE_ACTED", first)
        share_id = (first.get("sharing") or {})["share_id"]
        head = lsc.read_draft(self.cycle.sharing_service, share_id)["head_version_id"]
        # 用过期 head 发：owner 回 conflict，这一轮不该有"已落地"的副作用。
        self.cognition.plan = []
        self.cycle.last_sharing_block = self.cycle.sharing_decision_context()
        stale_action = {"op": "send_local", "share_id": share_id,
                        "expected_head": "artifact-version:" + "0" * 32}
        refused = self.cycle._apply_sharing_action(
            type("R", (), {"sharing_action": stale_action})())
        self.assertFalse(refused.get("ok"), refused)
        self.assertFalse(refused.get("delivered"))
        self.assertNotEqual(refused.get("status"), "applied")
        self.assertEqual(lsc.read_draft(self.cycle.sharing_service, share_id)["head_version_id"],
                         head)


if __name__ == "__main__":
    unittest.main(verbosity=2)

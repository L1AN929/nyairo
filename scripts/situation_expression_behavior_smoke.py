#!/usr/bin/env python3
"""v0.4 处境到有限表达：走**正式** ``evaluate_once`` 的本地冒烟。

跑的是什么
----------
直接复用 ``tests/test_v04_sharing_life_chain.py`` 的隔离夹具：真实 Supply owner socket、
真实隔离 M0、真实 runtime、真实 ``assemble_autonomous_life``，认知用只回放**原始 JSON** 的
脚本替身（不调模型、不联网）。每一轮都走 ``cycle.evaluate_once`` → AG-1 正式校验 →
``_apply_sharing_action``，**不**直接调 ``_apply_sharing_action``，也不手写
``last_sharing_block``。

本单只看这一处：来源被治理/不可达时，扣留事实必须随本轮正式处境进入 Decision 可读视图，
且模型脚本仍请求发送时由 owner 拒、本地零增量；正常路径 prepare → defer → send_local 只产生
一条本地 receipt，并可从 owner 回读。

边界
----
* Decision 是脚本替身，但提交经正式 AG-1（与真实链同一入口）；
* “撤权/不可达”由端口只拒**来源 artifact** 的读取来模拟，不称完整真实 Memory 治理；
* 本地投递不是用户收到；任何 surface 都没有外发；不读真实实例、不输出正文/prompt。
* scratch 由夹具自己建，结束后收尾自有对象；本脚本不删夹具目录。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT),
              str(ROOT / "tests"),
              str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_sharing_context as lsc  # noqa: E402
import life_sharing_service as lss  # noqa: E402
import test_v04_sharing_life_chain as chain  # noqa: E402

CHECKS: list[dict] = []


def check(name: str, ok: bool, **evidence) -> None:
    CHECKS.append({"check": name, "ok": bool(ok), **evidence})
    if not ok:
        raise AssertionError("%s failed: %r" % (name, evidence))


def _row(block: dict, share_id: str) -> dict:
    rows = [item for item in (block.get("shares") or []) if item.get("share_id") == share_id]
    return rows[0] if rows else {}


def main() -> int:
    case = chain.SharingLifeChainTest(
        "test_prepare_defer_and_one_local_delivery_through_formal_decisions")
    case.setUp()
    try:
        wiring = case.fixture.assembled.get("sharing") or {}
        check("formal_assembly_hands_the_real_sharing_owner",
              wiring.get("status") == "READY", status=wiring.get("status"))
        service = case.cycle.sharing_service
        check("cycle_holds_that_same_owner", service is not None)

        # 1) 正式 Decision：prepare → DRAFT（不是已发），prompt 只给来源与句柄。
        first = case.turn("prepare", trigger="smoke:prepare")
        share = first.get("sharing") or {}
        check("formal_prepare_is_a_draft_not_a_delivery",
              share.get("ok") and share.get("state") == lss.STATE_DRAFT
              and not share.get("delivered") and not (share.get("receipt") or {}),
              state=share.get("state"), delivered=share.get("delivered"))
        share_id = share["share_id"]
        first_prompt = case.cognition.reads[-1]["sharing"] or {}
        check("the_prompt_offers_sources_and_no_delivery",
              first_prompt.get("channel") == "local_inbox"
              and (first_prompt.get("inbox") or {}).get("local_only") is True
              and not (first_prompt.get("inbox") or {}).get("delivered_count"),
              channel=first_prompt.get("channel"))

        # 2) 正式 Decision：defer 写明等待条件；不投递。
        second = case.turn("defer", trigger="smoke:defer")
        deferred = second.get("sharing") or {}
        check("formal_defer_delivers_nothing",
              deferred.get("ok") and deferred.get("state") == lss.STATE_DEFERRED
              and not deferred.get("delivered"),
              state=deferred.get("state"))
        before_send = lsc.read_draft(service, share_id)
        check("defer_wrote_no_receipt", before_send.get("receipt") is None)

        # 3) 正式 Decision：send_local → 一条本地 receipt，可从 owner 回读。
        third = case.turn("send_local", trigger="smoke:send")
        sent = third.get("sharing") or {}
        receipt = sent.get("receipt") or {}
        check("formal_send_local_makes_one_local_receipt",
              sent.get("ok") and sent.get("state") == lss.STATE_LOCAL_DELIVERED
              and sent.get("delivered") and sent.get("delivery_count") == 1
              and receipt.get("local_only") is True
              and receipt.get("external_channel_sent") is False
              and receipt.get("external_delivery_confirmed") is False
              and receipt.get("read_by_audience") is False,
              delivery_count=sent.get("delivery_count"))
        readback = lsc.read_draft(service, share_id)
        check("local_receipt_is_read_back_from_the_owner",
              readback.get("state") == lss.STATE_LOCAL_DELIVERED
              and (readback.get("receipt") or {}).get("delivery_id")
              == receipt.get("delivery_id"),
              delivered=readback.get("delivered"))

        # 4) 第二份草稿：来源此刻不可读 → 本轮正式 prompt 明确扣留；脚本仍请求发送 → owner 拒。
        again = case.turn("prepare_second", trigger="smoke:prepare-2")
        second_share = again.get("sharing") or {}
        check("formal_prepare_again_makes_another_draft",
              second_share.get("ok") and second_share.get("state") == lss.STATE_DRAFT,
              state=second_share.get("state"), decision=again.get("decision"),
              outcome=again.get("outcome"), reason=second_share.get("reason_code"),
              sources=len((case.cognition.reads[-1]["sharing"] or {}).get("sources") or []))
        second_id = second_share["share_id"]
        second_head = lsc.read_draft(service, second_id)["head_version_id"]

        governed = lss.LifeSharingService(
            chain.SourceRefusingPort(service.projects,
                                     source_id=case.data["work_artifact_id"]),
            clock=service._clock)
        case.cycle.sharing_service = governed
        refused = case.turn("send_local", trigger="smoke:send-governed")
        block = case.cognition.reads[-1]["sharing"] or {}
        row = _row(block, second_id)
        check("the_formal_prompt_names_the_withholding",
              row.get("text_withheld") is True and row.get("text_available") is False
              and bool(row.get("withheld_reason_code"))
              and row.get("withheld_reason_code") != "SHARING_SOURCE_NOT_FOUND"
              and not row.get("text_excerpt"),
              withheld=row.get("text_withheld"), reason=row.get("withheld_reason_code"))
        check("the_script_really_asked_to_send",
              bool(case.cognition.reads[-1]["raw_sharing"]))
        refused_share = refused.get("sharing") or {}
        after = lsc.read_draft(service, second_id)
        check("the_owner_refuses_the_withheld_send_and_nothing_is_written",
              not refused_share.get("ok") and not refused_share.get("delivered")
              and after.get("state") == lss.STATE_DRAFT
              and after.get("head_version_id") == second_head
              and after.get("receipt") is None,
              state=after.get("state"))

        # 5) 后续这个正式回合也没有给已投递的那条造第二条 receipt（终态不重开）。
        after_later = lsc.read_draft(service, share_id)
        check("a_later_formal_round_makes_no_second_receipt",
              (after_later.get("receipt") or {}).get("delivery_id")
              == receipt.get("delivery_id")
              and after_later.get("delivered") is True,
              delivery_count=sent.get("delivery_count"))

        # 6) 任何 surface 都没有外发通道。
        describe = governed.describe()
        check("no_external_channel_on_any_surface",
              describe.get("external_send") is False
              and describe.get("channels") == ["local_inbox"]
              and block.get("channel") == "local_inbox"
              and block.get("audience") == "local:owner",
              channels=describe.get("channels"))

        result = {
            "ok": all(item["ok"] for item in CHECKS),
            "checks": CHECKS,
            "checks_passed": sum(1 for item in CHECKS if item["ok"]),
            "checks_total": len(CHECKS),
            "entry": "formal cycle.evaluate_once + AG-1 (raw-JSON scripted decision)",
            "owners": {
                "ManagedArtifactStore": "real (isolated sqlite, via donor fixture socket)",
                "PrivateProjectService": "real",
                "LifeSharingService": "real",
                "AutonomousLifeCycle": "real (assemble_autonomous_life)",
                "DecisionInput": "scripted raw JSON through formal AG-1, no model, no network",
                "governed_source": "source-only read refusal port (documented shape)",
            },
            "instance": str(case.instance),
            "external_channel_sent": False,
            "delivered_to_user": False,
            "private_text_printed": False,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1
    finally:
        try:
            case.doCleanups()
        except Exception:  # noqa: BLE001 - cleanup of a fixture is not a test result
            pass


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:  # noqa: BLE001 - one honest failing exit
        print(json.dumps({"ok": False, "error": "%s: %s" % (type(error).__name__, error),
                          "checks": CHECKS}, ensure_ascii=False, indent=2))
        raise SystemExit(1)

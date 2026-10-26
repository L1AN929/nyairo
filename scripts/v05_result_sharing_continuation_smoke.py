#!/usr/bin/env python3
"""v0.5 零模型真实 owner 冒烟：真实作品结果承接到有限本地表达（换版/不可读）。

全部在本机隔离夹具内，0 产品模型、0 外网、0 旧实例、0 外发：

1. 复用 ``result_method_behavior_smoke`` 的隔离装配：真实 Life Supply owner（自有
   AF_UNIX + 落盘 canonical db）、真实 M0 Memory（EvidenceStore + controls）、真实
   ProductionLifeRuntime、B 的实践工作台与 Sharing owner、AG-0 候选、AG-1 原始 JSON
   校验、AG-2 adoption、AR-0 受管作品适配器。Decision 与内容回答是脚本替身（明示），
   不宣称真实模型自然采用。
2. 第一轮由正式 Decision 真正写出作品 A 的一个版本，并得到 owner 的结果级身份
   （``artifact:<id>@artifactversion:<head>``）与 step receipt —— 不是只读种子作品。
3. 正式 Decision 从 A 的**当前 head** prepare 一份草稿 D1：草稿是另一件 artifact，
   A 的 head 不变，草稿不是投递。
4. 正式 Decision CONTINUE 同一作品 A，真实 owner 写出下一版（v3）：D1 仍精确绑定
   原来那一版；下一轮**实际请求**里 ``source_current.stale/matches_recorded`` 如实，
   旧正文仍可读（不伪称被删）；主动 send_local 由 owner 拒 ``stale_source``，
   D1 的 head / state / receipt 一律不变。
5. 再后续正式 Decision 对当前可读的 A 新 prepare：形成**新草稿 D2**并绑定新版本，
   D1 一字未改；新 Decision send_local 只产生**一条**本地 receipt，
   ``external_channel_sent / external_delivery_confirmed / read_by_audience /
   acknowledged / understood`` 全为 false。重复发送（含 owner 层重放）不产生第二条。
6. 来源不可读故障：对**来源读**的故障端口拒绝读取后，下一轮实际请求里正文被扣留
   （``text_withheld``/``text_available``/原因码），仍发则 owner 拒且零写；
   sentinel 不在这份请求、拒绝答案或读回答案里 —— 不从旧 text/缓存/历史版本恢复。
7. 草稿 CAS 冲突与来源换版分开：Decision 读完之后，**另一个合法 owner writer** 对
   D1 做一次合法 defer，旧 ``expected_head`` 的 send 收到 ``conflict``（不是
   ``stale_source``），零新 receipt。
8. NO_ACTION 合法：不分享、不自动 drop 旧稿、不自动重试、不自动刷新来源。

Run (POSIX/WSL)::

    PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/v05_result_sharing_continuation_smoke.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
for entry in (str(ROOT), str(SCRIPT_DIR), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "world" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

#: 每轮一个自己的观察时刻：静默账本按回合状态键控，冻结时刻会让后续回合变成
#: ``SAME_STATE_ALREADY_CONSIDERED``，这个冒烟需要每轮都真的取一次 Decision。
#: 步长取 400 分钟（既有 6 小时 TTL 之外）：被拒的发送不改变任何状态，同一个状态要等
#: 既有 TTL 过完才会被重新考虑 —— 这里推进的是夹具时钟，不是静默规则。
_BASE_TIME = datetime.now().astimezone()
_ROUND_INDEX = [0]

#: 唯一进入"来源不可读"草稿的句子片段。它只用来断言扣留真的发生，绝不打印。
SENTINEL = "SENTINEL_V05_SHARE_SOURCE_WITHHELD_DO_NOT_RETURN"

D1_TEXT = "这一段来自作品刚写完的那一版，先留在这里。"
D2_TEXT = "换过一版之后，这一句才是现在想说的。"
D3_TEXT = "这一句只用来验证来源不可读时不会漏出来。" + SENTINEL

CHECKS: list[tuple[str, bool, object]] = []
TABLE: list[dict] = []
ROUNDS: list[dict] = []


def check(name: str, condition, detail=None) -> None:
    CHECKS.append((name, bool(condition), detail))
    print("CHECK %-72s %s" % (name, "PASS" if condition else "FAIL"))
    if not condition and detail is not None:
        print("  detail:", json.dumps(detail, ensure_ascii=False, default=str)[:900])


def next_instant() -> str:
    _ROUND_INDEX[0] += 1
    return (_BASE_TIME + timedelta(minutes=400 * _ROUND_INDEX[0])).isoformat()


def bare(value) -> str:
    text = str(value or "").strip()
    while text.startswith("artifact:"):
        text = text[len("artifact:"):]
    return text


def work_ref(value) -> str:
    text = bare(value)
    return "artifact:%s" % text if text else ""


def head_version(chain, artifact_id) -> str:
    point = chain.port.resume_point(artifact_id)
    return str(getattr(point, "head_version_id", "") or "")


def version(chain, artifact_id) -> int:
    point = chain.port.resume_point(artifact_id)
    return int(getattr(point, "version_number", 0) or 0)


class SourceRefusingPort:
    """真实 owner 端口，只对**来源** artifact 的读取回答拒绝。

    ``ok=False`` + owner 自己的原因码就是既有的治理/撤权形状；分享件本身、列表、
    写入全部原样落到真实 owner，所以正式回合仍然跑在真链上。
    """

    def __init__(self, inner, *, source_id, status: str = "rejected",
                 reason_code: str = "SOURCE_GOVERNED") -> None:
        self._inner = inner
        self._source = bare(source_id)
        self._status = status
        self._reason = reason_code
        self.refused_reads = 0

    def read_project(self, artifact_id, **kwargs):
        if bare(artifact_id) == self._source:
            self.refused_reads += 1
            return {"ok": False, "status": self._status, "reason_code": self._reason,
                    "detail": "the owner refused this source read"}
        return self._inner.read_project(artifact_id, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class PlanCognition:
    """按计划回答原始 JSON 的 Decision 替身；只回显本轮真实请求里给的 id / CAS head。

    每一步是一个 dict：

    * ``{"op": "prepare", "text": ..., "title": ...}``：从本轮 ``sources`` 里的
      当前作品来源 prepare 草稿；
    * ``{"op": "defer"|"send_local"|"drop", "target": <share_id>, "head": <可选>}``：
      对轮内 offered 的某份草稿行动，默认用它在本轮请求里看到的 CAS head；
    * ``{"script": {...}}``：附加原始 JSON 字段（CONTINUE 等）；
    * ``{"race": callable}``：在回答之前让另一个合法 owner writer 动一次（CAS 竞态夹具）。

    ``head`` 显式给出时（例如 ``"stale"`` 场景），就是"模型仍然用读到的旧 head 发送"。
    """

    model_id = "stub:v05-result-sharing"
    model_version = "v05-result-sharing-v1"

    def __init__(self) -> None:
        self.plan: list[dict] = []
        self.captured: list[dict] = []
        self.call_count = 0
        self.last_head_seen: dict = {}

    def invoke(self, request, *, schema_retry_index: int = 0):
        import agency_cognition_adapter as aa
        import agency_decision_ag1 as ag1

        self.call_count += 1
        allowed = [str(code) for code in (getattr(request, "allowed_reason_codes", []) or [])]
        situation = getattr(request, "situation_context", {}) or {}
        sharing = situation.get("sharing") if isinstance(situation, dict) else {}
        sharing = sharing if isinstance(sharing, dict) else {}
        self.captured.append({
            "sharing": json.loads(json.dumps(sharing, ensure_ascii=False, default=str)),
            "bounded": aa.ProductionAgencyCognitionAdapter._bounded_situation(situation),
        })
        for row in sharing.get("shares") or []:
            if isinstance(row, dict) and row.get("share_id"):
                self.last_head_seen[str(row["share_id"])] = str(row.get("head_version_id") or "")

        step = self.plan.pop(0) if self.plan else {"op": "none"}
        raw: dict = {"decision": "NO_ACTION", "selected_candidate_id": None,
                     "reason_codes": allowed[:1] or ["NO_ACTION"],
                     "short_rationale": "stub: quiet round",
                     "deferred_candidate_ids": []}
        raw.update(dict(step.get("script") or {}))
        op = str(step.get("op") or "none")
        if op == "prepare":
            kind = str(step.get("source_kind") or "current_work")
            sources = [row for row in (sharing.get("sources") or [])
                       if isinstance(row, dict) and str(row.get("kind") or "") == kind]
            if not sources:
                raise AssertionError("no %s source in this round's request: %r" % (kind, sharing))
            raw["sharing"] = {"op": "prepare",
                              "source_artifact_id": sources[0]["artifact_id"],
                              "share_text": str(step["text"]),
                              "title": str(step.get("title") or "")}
        elif op in ("defer", "send_local", "drop"):
            target = str(step["target"])
            head = step.get("head")
            if not head:
                head = self.last_head_seen.get(target, "")
            request_body = {"op": op, "share_id": target, "expected_head": head}
            if op == "defer":
                request_body["opportunity_ref"] = str(step.get("opportunity_ref")
                                                     or "quiet-evening")
            if op == "drop":
                request_body["reason"] = str(step.get("reason") or "不想说了")
            raw["sharing"] = request_body
        if callable(step.get("race")):
            # Another legitimate owner writer acts *after* the Decision saw its CAS head.
            step["race"](step)
        return ag1.CognitiveDecisionResponse(
            model_run_ref="model_run:v05-result-sharing:%d" % self.call_count,
            model_id=self.model_id, model_version=self.model_version,
            raw_output=raw, latency_ms=1)


def install_planner(chain) -> PlanCognition:
    planner = PlanCognition()
    chain.cognition.invoke = planner.invoke
    return planner


def run_round(chain, planner: PlanCognition, step: dict | None, *, trigger: str) -> dict:
    """一轮正式 ``evaluate_once``；每轮自己的观察时刻，避免静默账本吃掉后续回合。"""

    planner.plan = [step] if step else []
    return chain.cycle.evaluate_once(trigger_ref=trigger, act=True,
                                     observed_at=next_instant())


def continue_candidate(chain, artifact_id: str, *, observed_at: str):
    """本轮同一观察时刻下，owner 目标就是这个 artifact 的唯一 CONTINUE 候选。"""

    import past_candidate_bridge as pcb

    wanted = bare(artifact_id)
    candidates = chain.runtime.candidate_materializer.build_candidate_set(
        observed_at=observed_at).candidate_refs
    matches = []
    for candidate_ref in candidates:
        row = chain.runtime.candidate_read.get_candidate(str(candidate_ref)) or {}
        if str(row.get("candidate_kind") or "") != "CONTINUE_CURRENT":
            continue
        if str(row.get("source_kind") or "") != "CURRENT_ACTIVITY":
            continue
        target = pcb.work_ref_from_candidate(row, exclusions=chain.cycle._non_work_artifact_ids())
        if target and bare(target) == wanted:
            matches.append(str(candidate_ref))
    return matches[0] if len(matches) == 1 else None


def latest_sharing_block(planner: PlanCognition) -> dict:
    return (planner.captured[-1]["sharing"] if planner.captured else {}) or {}


def record_round(tag: str, out: dict, planner: PlanCognition) -> None:
    """One safe structural row per formal round: no private text, no prompt body."""

    sharing = out.get("sharing") if isinstance(out.get("sharing"), dict) else {}
    block = latest_sharing_block(planner)
    ROUNDS.append({
        "round": tag,
        "decision": str(out.get("decision"))[:64],
        "outcome": str(out.get("outcome") or "")[:48],
        "sharing_ok": sharing.get("ok"),
        "sharing_status": str(sharing.get("status") or "")[:32],
        "sharing_reason": str(sharing.get("reason_code") or "")[:48],
        "sources": [str(row.get("kind") or "") for row in (block.get("sources") or [])
                    if isinstance(row, dict)],
        "open_shares": len(block.get("shares") or []),
        "error": str(out.get("detail") or out.get("practice_error") or "")[:160],
    })


def draft_rows(planner: PlanCognition, share_id: str) -> list:
    return [row for row in (latest_sharing_block(planner).get("shares") or [])
            if isinstance(row, dict) and str(row.get("share_id") or "") == share_id]


def main() -> int:
    if os.name != "posix":
        print("SKIP: this smoke needs POSIX (fcntl + AF_UNIX); run in WSL/Linux")
        return 0

    import life_sharing_context as lsc
    import life_sharing_service as lss
    import result_method_behavior_smoke as harness

    print("fixture root:", harness.FIXTURE_ROOT)
    chain = harness.build_chain("v05-result-sharing")
    closed = False
    completed = False
    planner = None
    try:
        # -- 0) 真实作品 A：正式 Decision 写出一个版本 + owner 结果身份 + receipt --------
        # 这一段用夹具自带的脚本 cognition（它负责选候选与内容回答），随后再换成
        # 本冒烟的 PlanCognition：换的时机在真实写之后，互不遮盖。
        seed = harness.seed_method_after_first_write(chain)
        planner = install_planner(chain)
        check("the fixture round's own checks all passed",
              all(ok for _name, ok, _detail in harness._CHECKS), harness._CHECKS)
        receipt = seed.round_out.get("step_receipt") or {}
        artifact_a = str(receipt.get("artifact_id")
                         or (seed.round_out.get("project_created") or {}).get("artifact_id") or "")
        v_first = head_version(chain, artifact_a)
        handoff = seed.round_out.get("experience_handoff") or {}
        result_ref = str(handoff.get("progress_result_ref") or "")
        check("a formal Decision really wrote a work version through the owner",
              bool(artifact_a) and version(chain, artifact_a) >= 2 and bool(v_first),
              {"artifact": artifact_a, "version": version(chain, artifact_a),
               "head": v_first})
        check("the owner also minted the result identity and the step receipt",
              result_ref == "artifact:%s@artifactversion:%s" % (bare(artifact_a), v_first)
              and bool(receipt),
              {"result_ref": result_ref, "receipt_keys": sorted(receipt)})
        service = chain.cycle.sharing_service
        check("the cycle is wired to the real sharing owner",
              isinstance(service, lss.LifeSharingService), type(service).__name__)
        chain.cycle.work_artifact_id = artifact_a
        a_head_at_v2 = head_version(chain, artifact_a)

        # -- 1) 正式 Decision：从 A 的当前 head prepare D1（草稿 ≠ 投递，A 不变） --------
        out1 = run_round(chain, planner, {"op": "prepare", "text": D1_TEXT, "title": "刚写完那版"},
                         trigger="evt:v05-sharing:prepare")
        record_round("prepare-d1", out1, planner)
        first = out1.get("sharing") or {}
        d1 = str(first.get("share_id") or "")
        check("prepare creates a DRAFT through the real owner", first.get("ok") is True
              and first.get("state") == lss.STATE_DRAFT and not first.get("delivered"), first)
        owner_d1 = service.read_share(d1, include_text=False) if d1 else {}
        d1_bound = str(((owner_d1.get("data") or {}).get("share") or {}).get(
            "source", {}).get("version_id") or "")
        check("the draft really binds the owner's current source version",
              d1_bound == a_head_at_v2, {"bound": d1_bound, "head": a_head_at_v2})
        check("the draft is a different artifact and the work head did not move",
              bare(d1) != bare(artifact_a) and head_version(chain, artifact_a) == a_head_at_v2,
              {"draft": d1, "work_head": head_version(chain, artifact_a)})
        d1_head = str(first.get("head_version_id") or "")
        prompt1 = latest_sharing_block(planner)
        check("the same request also offers the real work artifact as a source",
              any(str(row.get("artifact_id") or "") == work_ref(artifact_a)
                  for row in (prompt1.get("sources") or [])), prompt1.get("sources"))

        # -- 2) 正式 Decision CONTINUE A → 真实 owner 写出下一版 ------------------------
        stamp = next_instant()
        candidate = continue_candidate(chain, artifact_a, observed_at=stamp)
        check("exactly one formal CONTINUE candidate targets the same work", bool(candidate),
              {"candidate": candidate, "artifact": artifact_a})
        chain.client.payload = {"kind": "append_paragraph",
                                "text": "下一版：把结尾收住。",
                                "self_note": "stub: write A v3"}
        planner.plan = [{"script": {"decision": "CONTINUE",
                                    "selected_candidate_id": candidate,
                                    "short_rationale": "stub: continue the same work"}}]
        out_v3 = chain.cycle.evaluate_once(trigger_ref="evt:v05-sharing:continue", act=True,
                                           observed_at=stamp)
        record_round("continue-v3", out_v3, planner)
        a_head_v3 = head_version(chain, artifact_a)
        v3_receipt = out_v3.get("step_receipt") or {}
        check("a formal CONTINUE wrote the next version of the same work",
              bare(v3_receipt.get("artifact_id")) == bare(artifact_a)
              and a_head_v3 != a_head_at_v2 and version(chain, artifact_a) >= 3,
              {"head_before": a_head_at_v2, "head_after": a_head_v3,
               "receipt_artifact": bare(v3_receipt.get("artifact_id")),
               "receipt_version": v3_receipt.get("version_number")})
        check("the draft still records the version it was prepared from",
              lsc.read_draft(service, d1)["source_current"]["matches_recorded"] is False,
              lsc.read_draft(service, d1))

        # -- 3) 下一轮实际请求 in-situ 如实报告 stale；旧正文仍可读（不伪称删除） --------
        preview = chain.cycle.sharing_decision_context()
        row = [item for item in (preview.get("shares") or [])
               if str(item.get("share_id") or "") == d1][0]
        check("the next request reports the source moved, honestly",
              row.get("source_current", {}).get("readable") is True
              and row.get("source_current", {}).get("matches_recorded") is False
              and row.get("source_current", {}).get("stale") is True
              and row.get("source_current", {}).get("reason_code") == "SOURCE_REVISION_CHANGED", row)
        check("...and the old words stay readable (not reported as deleted)",
              row.get("text_available") is True and bool(row.get("text_excerpt")),
              {"text_available": row.get("text_available"),
               "text_chars": row.get("text_chars")})
        out_stale = run_round(chain, planner,
                              {"op": "send_local", "target": d1}, trigger="evt:v05-sharing:stale-send")
        record_round("stale-send-d1", out_stale, planner)
        stale = out_stale.get("sharing") or {}
        after_stale = lsc.read_draft(service, d1)
        check("the owner refuses the stale draft itself", stale.get("ok") is False
              and stale.get("status") == lss.STATUS_STALE_SOURCE
              and not stale.get("delivered"),
              {"status": stale.get("status"), "reason_code": stale.get("reason_code")})
        check("...and zero writes: same head, same state, no receipt",
              after_stale.get("head_version_id") == d1_head
              and after_stale.get("state") == lss.STATE_DRAFT
              and after_stale.get("receipt") is None, after_stale)
        check("the stale refusal did not change the work artifact either",
              head_version(chain, artifact_a) == a_head_v3)
        stale_prompt_row = (draft_rows(planner, d1) or [{}])[0]
        check("the formal request itself carried the stale fact",
              stale_prompt_row.get("source_current", {}).get("stale") is True, stale_prompt_row)

        # -- 4) 新 Decision 对当前可读的 A 新 prepare → 新草稿绑定新版本；旧稿一字未改 ----
        out2 = run_round(chain, planner,
                         {"op": "prepare", "text": D2_TEXT, "title": "换版之后"},
                         trigger="evt:v05-sharing:re-prepare")
        record_round("prepare-d2", out2, planner)
        second = out2.get("sharing") or {}
        d2 = str(second.get("share_id") or "")
        d2_head = str(second.get("head_version_id") or "")
        owner_d2 = service.read_share(d2, include_text=False) if d2 else {}
        d2_bound = str(((owner_d2.get("data") or {}).get("share") or {}).get(
            "source", {}).get("version_id") or "")
        check("the new prepare is a separate draft bound to the new version",
              second.get("ok") is True and second.get("state") == lss.STATE_DRAFT
              and d2 and bare(d2) != bare(d1) and d2_bound == a_head_v3,
              {"share_id": d2, "bound": d2_bound, "head": a_head_v3})
        old_after = lsc.read_draft(service, d1)
        check("the old draft was not rewritten or rebound",
              old_after.get("head_version_id") == d1_head
              and old_after.get("state") == lss.STATE_DRAFT, old_after)
        check("two drafts are open, no delivery yet",
              (service.list_shares(include_text=False).get("data") or {}).get(
                  "counts", {}).get("total") == 2, service.list_shares()["data"]["counts"])
        options = json.dumps(chain.cycle.project_options(), ensure_ascii=False, default=str)
        check("a share draft never becomes a continuable work option",
              bare(d1) not in options and bare(d2) not in options, {"drafts": [d1, d2]})

        # -- 5) 新 Decision send_local → 恰好一条本地 receipt ---------------------------
        out_send = run_round(chain, planner,
                             {"op": "send_local", "target": d2}, trigger="evt:v05-sharing:send")
        record_round("send-d2", out_send, planner)
        sent = out_send.get("sharing") or {}
        receipt2 = sent.get("receipt") or {}
        owner_receipt = (service.read_share(d2).get("data") or {}).get("receipt") or {}
        check("send_local produces exactly one local receipt",
              sent.get("ok") is True and sent.get("state") == lss.STATE_LOCAL_DELIVERED
              and sent.get("delivered") is True and sent.get("delivery_count") == 1
              and bool(receipt2.get("delivery_id"))
              and str(owner_receipt.get("delivery_id") or "")
              == str(receipt2.get("delivery_id") or ""), sent)
        check("nothing external / read / acknowledged / understood is claimed",
              owner_receipt.get("local_only") is True
              and owner_receipt.get("external_channel_sent") is False
              and owner_receipt.get("external_delivery_confirmed") is False
              and owner_receipt.get("read_by_audience") is False
              and owner_receipt.get("acknowledged") is False
              and owner_receipt.get("understood") is False
              and receipt2.get("external_channel_sent") is False
              and receipt2.get("external_delivery_confirmed") is False
              and receipt2.get("read_by_audience") is False
              and receipt2.get("understood") is False, owner_receipt)
        check("the work artifact is untouched by sharing",
              head_version(chain, artifact_a) == a_head_v3)
        delivered_head = lsc.read_draft(service, d2).get("head_version_id") or ""
        replay = service.apply_action(share_id=d2, action="send_local",
                                      expected_head=delivered_head)
        inbox = service.list_shares(include_text=False)["data"]["inbox"]
        check("a repeated send is refused with the one original receipt",
              replay.get("ok") is False
              and str(replay.get("reason_code") or "") == lss.REASON_ALREADY_DELIVERED
              and str((replay.get("data") or {}).get("receipt", {}).get("delivery_id") or "")
              == str(receipt2.get("delivery_id") or "")
              and inbox.get("delivered_count") == 1,
              {"reason": replay.get("reason_code"), "inbox": inbox})
        # 同一意图的重放要用同一个时钟：信封里的 created_at 也是意图的一部分，换了时刻就是
        # 同一 key 下的不同载荷（owner 会如实回 conflict）。这里冻结一个小隔离 service 的时钟，
        # 两次调用构成一次真正的"超时重放"，只允许一个草稿。
        frozen = service._now()
        replaying = lss.LifeSharingService(service.projects, clock=lambda: frozen)
        total_before_replay = (service.list_shares(include_text=False).get("data") or {}).get(
            "counts", {}).get("total")
        first_replay = replaying.prepare_share(source_artifact_id=artifact_a,
                                               share_text="重放用的另一句。", title="重放")
        same = replaying.prepare_share(source_artifact_id=artifact_a,
                                       share_text="重放用的另一句。", title="重放")
        total_after_replay = (service.list_shares(include_text=False).get("data") or {}).get(
            "counts", {}).get("total")
        # 现有 owner 合同：显式 operation_key 的第二次 create 不会回 already_applied，
        # 而是如实拒这份重复意图。要点不是状态字面，而是**没有第二份草稿**。
        check("the same prepare intent never mints a second draft",
              total_after_replay == (total_before_replay or 0) + 1
              and str(first_replay.get("status") or "") in ("applied", "already_applied")
              and same.get("ok") is False,
              {"first": first_replay.get("status"), "second": same.get("status"),
               "reason": same.get("reason_code"),
               "totals": [total_before_replay, total_after_replay]})

        # -- 6) 来源不可读：正文扣留、仍发则拒、零写、sentinel 不出现 --------------------
        out3 = run_round(chain, planner,
                         {"op": "prepare", "text": D3_TEXT, "title": "来源不可读用例"},
                         trigger="evt:v05-sharing:prepare-3")
        record_round("prepare-d3", out3, planner)
        third = out3.get("sharing") or {}
        d3 = str(third.get("share_id") or "")
        d3_head = str(third.get("head_version_id") or "")
        check("a third draft was prepared while the source was readable",
              third.get("ok") is True and bool(d3), third)
        refusing = SourceRefusingPort(service.projects, source_id=artifact_a)
        governed = lss.LifeSharingService(refusing, clock=service._clock)
        chain.cycle.sharing_service = governed
        out_read = run_round(chain, planner, {"op": "none"},
                             trigger="evt:v05-sharing:governed-read")
        record_round("governed-read", out_read, planner)
        governed_row = (draft_rows(planner, d3) or [{}])[0]
        block_text = json.dumps(latest_sharing_block(planner), ensure_ascii=False, default=str)
        bounded_text = json.dumps(planner.captured[-1]["bounded"], ensure_ascii=False,
                                  default=str)
        check("the final request names the withholding and the owner's own reason",
              governed_row.get("text_withheld") is True
              and governed_row.get("text_available") is False
              and governed_row.get("withheld_reason_code") == "SOURCE_GOVERNED"
              and not governed_row.get("text_excerpt"), governed_row)
        check("the withheld words are not recoverable from the request",
              SENTINEL not in block_text and SENTINEL not in bounded_text)
        owner_read = governed.read_share(d3, include_text=True)
        check("the owner's own read also withholds the words",
              owner_read.get("ok") is True
              and owner_read["data"].get("text_withheld") is True
              and SENTINEL not in json.dumps(owner_read, ensure_ascii=False, default=str),
              {"state": owner_read.get("state"),
               "reason_code": owner_read.get("reason_code")})
        out_refused = run_round(chain, planner, {"op": "send_local", "target": d3},
                                trigger="evt:v05-sharing:governed-send")
        record_round("governed-send-d3", out_refused, planner)
        refused = out_refused.get("sharing") or {}
        after_refused = governed.read_share(d3, include_text=False)
        check("the owner refuses the send and names a refusal, not 'not found'",
              refused.get("ok") is False and refused.get("delivered") is False
              and str(refused.get("status") or "") == lss.STATUS_STALE_SOURCE
              and str(refused.get("reason_code") or "") not in ("", "SOURCE_NOT_FOUND")
              and SENTINEL not in json.dumps(refused, ensure_ascii=False, default=str), refused)
        check("...with zero writes and no receipt",
              after_refused["data"]["share"].get("head_version_id") == d3_head
              and after_refused["data"]["share"].get("state") == lss.STATE_DRAFT
              and after_refused["data"].get("receipt") is None, after_refused.get("status"))

        # -- 7) 草稿 CAS 冲突 ≠ 来源换版：Decision 读后另一个合法 writer 改了 head ------
        chain.cycle.sharing_service = service
        # 用当前可读来源上的 D3：来源没有换版，唯一变化是另一个合法 writer 动了草稿 head。
        d3_before_race = lsc.read_draft(service, d3)["head_version_id"]

        def competing_defer(_step, *, share_id=d3, head=d3_before_race):
            other = lss.LifeSharingService(service.projects, clock=service._clock)
            answer = other.apply_action(share_id=share_id, action="defer",
                                        expected_head=head, opportunity_ref="quiet-evening",
                                        operation_key="share:op:v05-sharing:competing-defer")
            if not answer.get("ok"):
                raise AssertionError("the competing defer did not land: %r" % (answer,))

        out_race = run_round(chain, planner,
                             {"op": "send_local", "target": d3, "head": d3_before_race,
                              "race": competing_defer},
                             trigger="evt:v05-sharing:cas-race")
        record_round("cas-race-d3", out_race, planner)
        raced = out_race.get("sharing") or {}
        after_race = lsc.read_draft(service, d3)
        raced_reason = str(raced.get("reason_code") or "")
        check("the old expected head is a CAS conflict, not a stale source",
              raced.get("ok") is False and str(raced.get("status") or "") == lss.STATUS_CONFLICT
              and bool(raced_reason) and not raced_reason.startswith("SOURCE_")
              and raced.get("source_current") is None, raced)
        check("...and the competing legitimate defer is the state that survived",
              after_race["state"] == lss.STATE_DEFERRED
              and after_race["head_version_id"] != d3_before_race
              and after_race.get("receipt") is None, after_race)

        # -- 8) NO_ACTION 合法：不分享、不自动 drop / 不自动刷新 ------------------------
        heads_before = {sid: lsc.read_draft(service, sid).get("head_version_id")
                        for sid in (d1, d2, d3)}
        out_quiet = run_round(chain, planner, {"op": "none"}, trigger="evt:v05-sharing:quiet")
        record_round("quiet", out_quiet, planner)
        heads_after = {sid: lsc.read_draft(service, sid).get("head_version_id")
                       for sid in (d1, d2, d3)}
        states_after = {sid: lsc.read_draft(service, sid).get("state") for sid in (d1, d2, d3)}
        check("a NO_ACTION round takes no sharing action at all",
              not (out_quiet.get("sharing") or {}), out_quiet.get("sharing"))
        check("nothing was auto-dropped, auto-retried or auto-refreshed",
              heads_after == heads_before
              and states_after[d1] == lss.STATE_DRAFT
              and states_after[d2] == lss.STATE_LOCAL_DELIVERED
              and states_after[d3] == lss.STATE_DEFERRED, states_after)
        quiet_row = (draft_rows(planner, d3) or [{}])[0]
        check("the next formal request shows the head the competing writer left",
              quiet_row.get("head_version_id") == after_race["head_version_id"]
              and quiet_row.get("state") == lss.STATE_DEFERRED,
              {"prompt_head": quiet_row.get("head_version_id"),
               "owner_head": after_race["head_version_id"]})

        # -- 9) 纯读不写：两次 context 不造版本，也不新增 receipt -----------------------
        before_counts = service.list_shares(include_text=False)["data"]["counts"]
        chain.cycle.sharing_decision_context()
        chain.cycle.sharing_decision_context()
        after_counts = service.list_shares(include_text=False)["data"]["counts"]
        check("read-only rounds never write a version or a receipt",
              before_counts == after_counts
              and {sid: lsc.read_draft(service, sid).get("head_version_id")
                   for sid in (d1, d2, d3)} == heads_after,
              {"before": before_counts, "after": after_counts})

        TABLE.append({
            "work_artifact": work_ref(artifact_a),
            "versions": [a_head_at_v2, a_head_v3],
            "drafts": {"d1": d1, "d2": d2, "d3": d3},
            "states": states_after,
            "receipts": {"d2": receipt2.get("delivery_id")},
            "owner_source_refusals": refusing.refused_reads,
        })
        completed = True
        return 0 if all(ok for _name, ok, _detail in CHECKS) else 1
    finally:
        try:
            if not closed:
                chain.runtime.close()
        except Exception as exc:  # noqa: BLE001 - a close failure is printed, not hidden
            print("  runtime close error:", type(exc).__name__, str(exc)[:200])
        try:
            chain.services.stop()
        except Exception as exc:  # noqa: BLE001
            print("  supply stop error:", type(exc).__name__, str(exc)[:200])
        print("\nRESULT TABLE (safe structure only)")
        for entry in TABLE:
            print(json.dumps(entry, ensure_ascii=False, default=str))
        print("\nROUNDS (decision metadata only)")
        for entry in ROUNDS:
            print(json.dumps(entry, ensure_ascii=False, default=str))
        failed = [name for name, ok, _detail in CHECKS if not ok]
        print("\nchecks: %d, failed: %d" % (len(CHECKS), len(failed)))
        for name in failed:
            print("FAILED:", name)
        if not completed:
            # An exception aborted the chain: the checks that did run are not the verdict.
            print("RESULT: ERROR (chain aborted before the end)")
        else:
            print("RESULT: %s" % ("FAIL" if failed else "PASS"))


if __name__ == "__main__":
    sys.exit(main())

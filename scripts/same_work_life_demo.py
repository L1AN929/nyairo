#!/usr/bin/env python3
"""零模型真实 owner 演示：同一件作品接着做。

它做四件事，全部在本机、0 产品模型、0 外网、0 旧实例：

1. 用真实 Supply / M0 Memory / 实践工作台装配一条隔离链，第一轮正式判断选一个真实的
   项目选项 START，写出这件作品的第 1 版（owner 回执、真实 Activity）；
2. 第二轮正式判断 CONTINUE 同一件作品，写出第 2 版，并用第一版结果作为证据记一次显式
   方法评价（HELPED → TRIAL）；
3. 第三轮的**正式**决策上下文（``decision_situation_context`` -> ``work``）真的读到：
   作品 head/旧版、artifact_ref 就是这件作品的结果、证据指回这些结果的做法与最近一次评价；
   随后同一轮 CONTINUE 写出第 3 版，仍是同一 artifact；
4. 经真实 ``phone_handler`` 读同一件作品的有界上下文，并固定读第 1 版（head 不回滚）；
   再显式准备一份绑定当前结果版本的本地草稿、延后、显式本地发送，得到一条本地回执。

Decision 与内容是脚本替身（文件里明示），不宣称真实模型自然选择；作品版本、Activity、
Memory 经历、方法评价、草稿与本地回执全部由真实 owner 写出。

Run (POSIX/WSL)::

    PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/same_work_life_demo.py
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any, Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"), str(ROOT / "tests"),
              str(ROOT / "components" / "world" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import result_method_behavior_smoke as base  # noqa: E402

TS0 = "2026-11-20T09:00:00+00:00"
METHOD_TITLE = "先写结论"
METHOD_INSTRUCTION = "先写一句结论，再补三段理由"
METHOD_SCOPE = "只在这个长文项目里"
METHOD_UNCERTAINTY = "不知道短文本是否适用"
CSRF = "same-work-demo-csrf"


def say(text: str) -> None:
    print(text)
    sys.stdout.flush()


def check(name: str, condition: Any, detail: Any = None) -> bool:
    ok = bool(condition)
    say("CHECK %-62s %s" % (name, "PASS" if ok else "FAIL"))
    if not ok and detail is not None:
        say("  detail: " + json.dumps(detail, ensure_ascii=False, default=str)[:1200])
    return ok


def _candidate(chain) -> str:
    """The CURRENT_ACTIVITY candidate of the running work Activity (or a named failure)."""

    refs = chain.runtime.candidate_materializer.build_candidate_set(
        observed_at=TS0).candidate_refs
    for ref in refs:
        payload = chain.runtime.candidate_read.get_candidate(str(ref)) or {}
        if (str(payload.get("candidate_kind") or "") == "CONTINUE_CURRENT"
                or str(payload.get("source_kind") or "") == "CURRENT_ACTIVITY"):
            return str(ref)
    raise SystemExit("no CURRENT_ACTIVITY candidate after the first write: %r" % (refs,))


def _bare(value: Any) -> str:
    text = str(value or "")
    for prefix in ("artifactversion:", "artifact-version:", "artifact:"):
        while text.startswith(prefix):
            text = text[len(prefix):]
    return text


def _receipt_artifact(out: Mapping[str, Any]) -> str:
    receipt = out.get("step_receipt") if isinstance(out.get("step_receipt"),
                                                     Mapping) else {}
    return str(receipt.get("artifact_id") or "")


def _experiences(chain) -> list[dict[str, Any]]:
    return list(chain.cycle.practice.experiences() or [])


def run(chain) -> int:
    failures: list[str] = []

    def bad(name: str, detail: Any = None) -> None:
        failures.append(name)
        check(name, False, detail)

    # ---- round 1: one real project option -> the work's first version ----------------
    material_id, revision = base.import_material(chain)
    base.write_question(chain, material_id, revision)
    base.select_option(chain, review=False)
    chain.client.payload = {"kind": "append_paragraph", "text": "第一版：先把结论写在最前面。",
                            "self_note": "stub: first version"}
    chain.cognition.script = dict(chain.cognition.script or {})
    chain.cognition.script["method_proposal"] = {
        "title": METHOD_TITLE, "instruction": METHOD_INSTRUCTION,
        "scope": METHOD_SCOPE, "uncertainty": METHOD_UNCERTAINTY}
    out1 = chain.cycle.evaluate_once(trigger_ref="evt:same-work:1", act=True)
    artifact_id = _receipt_artifact(out1)
    if not artifact_id:
        bad("round 1 really wrote a work version", out1.get("step_receipt") or out1)
        return 1
    work_ref = artifact_id if str(artifact_id).startswith("artifact:") else \
        "artifact:" + str(artifact_id)
    method = dict(out1.get("method_created") or {})
    version1 = str((out1.get("step_receipt") or {}).get("version_id") or "")
    experiences = _experiences(chain)
    if not check("round 1 created the work and a TENTATIVE method",
                 bool(method.get("method_ref")) and bool(experiences),
                 {"method": method, "experiences": experiences[:2],
                  "outcome": out1.get("outcome")}):
        return 1
    r1 = str(experiences[0]["experience_id"])

    # ---- round 2: CONTINUE the same work + one evidence-backed evaluation -----------
    cont = _candidate(chain)
    chain.cognition.script = {
        "decision": "CONTINUE", "selected_candidate_id": cont,
        "deferred_candidate_ids": [], "method_ref": method["method_ref"],
        "method_trial": {"method_ref": method["method_ref"], "outcome": "HELPED",
                         "detail": "这次先写结论，确实顺一些",
                         "experience_ids": [r1]}}
    chain.client.payload = {"kind": "append_paragraph", "text": "第二版：补上第二段理由。",
                            "self_note": "stub: second version"}
    out2 = chain.cycle.evaluate_once(trigger_ref="evt:same-work:2", act=True)
    version2 = str((out2.get("step_receipt") or {}).get("version_id") or "")
    check("round 2 continued the same work (new version, same artifact)",
          _bare(_receipt_artifact(out2)) == _bare(artifact_id)
          and version2 and version2 != version1,
          {"artifact": _receipt_artifact(out2), "expected": artifact_id,
           "version1": version1, "version2": version2, "outcome": out2.get("outcome")})
    check("the explicit trial reached the real owner as TRIAL/HELPED",
          (out2.get("method_trial") or {}).get("method_state") == "TRIAL"
          and (out2.get("method_trial") or {}).get("outcome") == "HELPED",
          out2.get("method_trial"))

    # ---- round 3: the formal decision context reads all of it, then continues it -----
    cont = _candidate(chain)
    chain.cognition.script = {"decision": "CONTINUE", "selected_candidate_id": cont,
                              "deferred_candidate_ids": [],
                              "short_rationale": "stub: continue the same work"}
    chain.client.payload = {"kind": "append_paragraph", "text": "第三版：收一下尾。",
                            "self_note": "stub: third version"}
    out3 = chain.cycle.evaluate_once(trigger_ref="evt:same-work:3", act=True)
    work = out3.get("work_context") if isinstance(out3.get("work_context"),
                                                  Mapping) else {}
    work_head = work.get("work") if isinstance(work.get("work"), Mapping) else {}
    results = work.get("results") if isinstance(work.get("results"), Mapping) else {}
    methods = work.get("methods") if isinstance(work.get("methods"), Mapping) else {}
    result_rows = [row for row in (results.get("rows") or []) if isinstance(row, Mapping)]
    method_rows = [row for row in (methods.get("rows") or []) if isinstance(row, Mapping)]
    versions = [row for row in ((work.get("versions") or {}).get("entries") or [])
                if isinstance(row, Mapping)]

    check("the formal decision context saw the work's own head",
          str(work_head.get("head_version_id") or "") == version2,
          {"head": work_head.get("head_version_id"), "expected": version2})
    check("...its old versions (the head is not a re-write of history)",
          any(str(row.get("version_id")) == version1 for row in versions)
          and len(versions) >= 2, versions)
    written = {_bare(version1), _bare(version2)}
    check("...the results whose artifact_ref is this work",
          bool(result_rows) and all(str(row.get("version_ref") or "")
                                    and _bare(row.get("version_ref")) in written
                                    for row in result_rows),
          [{"id": row.get("experience_id"), "version": row.get("version_ref"),
              "expected": sorted(written)} for row in result_rows])
    check("...the method whose evidence points back at those results",
          bool(method_rows) and method_rows[0].get("artifact_id") == method.get("artifact_id")
          and (method_rows[0].get("latest_evaluation") or {}).get("outcome") == "HELPED",
          method_rows)
    version3 = str((out3.get("step_receipt") or {}).get("version_id") or "")
    check("round 3 kept writing the same work",
          bool(_receipt_artifact(out3)) and version3 not in ("", version1, version2),
          {"artifact": _receipt_artifact(out3), "version3": version3,
           "outcome": out3.get("outcome")})

    # ---- the real phone reads the same context, and an old version stays readable ----
    from components.phone import server as phone_server
    from components.phone import service as phone_service
    from chiyo_bundle import phone_tool

    root = chain.root
    profile_home = root / "profile"
    (profile_home / "chiyo").mkdir(parents=True, exist_ok=True)
    instance = {
        "schema_version": phone_service.SCHEMA_INSTANCE,
        "kind": phone_service.KIND_INSTANCE,
        "name": "same-work-demo",
        "subject": chain.data["subject_id"],
        "local_owner_uid": os.geteuid(),
        "root": str(root),
        "profile_home": str(profile_home),
        "world_home": str(chain.data.get("world_home") or (root / "world")),
        "world_id": str(chain.data.get("world_id") or "same-work-demo"),
        "supply_socket": str(chain.services.supply_path),
        "private_items_artifact_id": chain.data.get("private_items_artifact_id"),
        "reading_artifact_id": chain.data.get("reading_artifact_id"),
        "artifact_grant_id": chain.data["artifact_grant_id"],
        "workspace_id": chain.data["workspace_id"],
        "world_body_socket": str(root / "world" / "run" / "gateway.sock"),
        "features": {},
    }
    service = phone_service.PhoneService(instance, environment=dict(chain.env))
    httpd = phone_server.create_server(service, csrf_token=CSRF, port=0)
    thread = threading.Thread(target=httpd.serve_forever,
                              kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        port = int(httpd.server_address[1])
        (profile_home / "chiyo" / "config.json").write_text(json.dumps({
            "cli_owner": True, "local_owner_uid": os.geteuid(),
            "phone": {"enabled": True, "base_url": "http://127.0.0.1:%d" % port,
                      "profile_home": str(profile_home), "private_artifacts": True},
        }, ensure_ascii=False), encoding="utf-8")

        raw = phone_tool.phone_handler(
            {"action": "work_context", "artifact_id": artifact_id},
            hermes_home=str(profile_home))
        answer = json.loads(raw)
        context = ((answer.get("result") or {}).get("context")
                   if isinstance(answer.get("result"), Mapping) else {}) or {}
        phone_results = ((context.get("results") or {}).get("rows") or [])
        phone_methods = ((context.get("methods") or {}).get("rows") or [])
        check("phone_handler read the same work's context (read-only)",
              bool(answer.get("ok")) and bool(context),
              {"answer_ok": answer.get("ok"), "status": answer.get("status"),
               "result": (answer.get("result") or {}).get("context")})
        check("...including its results and the evidence-linked method",
              bool(phone_results) and bool(phone_methods),
              {"results": len(phone_results), "methods": len(phone_methods)})

        raw = phone_tool.phone_handler(
            {"action": "work_version", "artifact_id": artifact_id, "version_id": version1},
            hermes_home=str(profile_home))
        old = json.loads(raw)
        old_body = str(((old.get("result") or {}).get("version") or {}).get("content") or "")
        check("phone_handler still reads the first version verbatim",
              bool(old.get("ok")) and "第一版" in old_body, {"answer": old})
        head_after = service.project(artifact_id)
        check("...and reading an old version did not roll the head back",
              str(((head_after.get("project") or {}).get("head_version_id") or "")) == version3,
              {"head": (head_after.get("project") or {}).get("head_version_id")})

        # ---- one local draft: prepare on the current result version, defer, send local --
        sharing = chain.cycle.sharing_service
        # The draft binds to the source's **current head**, so it is bound to the result version
        # this round just wrote -- not to some earlier version and not to a copied string.
        prepared = sharing.prepare_share(
            source_artifact_id=work_ref,
            share_text="想把这件作品的开头说给本地收件箱听。",
            operation_key="op:same-work:share:1")
        prepared_data = prepared.get("data") if isinstance(prepared.get("data"),
                                                           Mapping) else {}
        share_id = str(prepared_data.get("share_id") or prepared.get("share_id") or "")
        source_bound = str((prepared_data.get("source") or {}).get("version_id")
                           if isinstance(prepared_data.get("source"), Mapping) else "")
        check("a local draft was really prepared on the result version",
              bool(prepared.get("ok")) and bool(share_id)
              and _bare(source_bound) == _bare(version3),
              {"prepared": prepared, "source_version": source_bound,
               "expected": version3})
        if share_id:
            current = sharing.read_share(share_id, include_text=False)
            head = str(((current.get("data") or {}).get("share") or {}).get("head_version_id")
                       or "")
            deferred = sharing.apply_action(share_id=share_id, action="defer",
                                            expected_head=head,
                                            defer_condition="先把这一段放一放",
                                            operation_key="op:same-work:share:defer")
            check("the draft was explicitly deferred (still not sent)",
                  bool(deferred.get("ok"))
                  and str((deferred.get("data") or {}).get("state") or "") == "DEFERRED",
                  deferred)
            current = sharing.read_share(share_id, include_text=False)
            head = str(((current.get("data") or {}).get("share") or {}).get("head_version_id")
                       or "")
            sent = sharing.apply_action(share_id=share_id, action="send_local",
                                        expected_head=head,
                                        operation_key="op:same-work:share:send")
            data = sent.get("data") if isinstance(sent.get("data"), Mapping) else {}
            check("one explicit local send produced exactly one receipt",
                  bool(sent.get("ok")) and str(data.get("state") or "") == "LOCAL_DELIVERED"
                  and bool(data.get("receipt")) and int(data.get("delivery_count") or 0) == 1,
                  sent)
            after = service.work_context(artifact_id).get("context") or {}
            drafts = [row for row in ((after.get("drafts") or {}).get("rows") or [])
                      if isinstance(row, Mapping)]
            check("the work's context now lists that local draft, with no external send",
                  any(str(row.get("share_id")) == share_id for row in drafts),
                  {"drafts": drafts})

        head_now = service.project(artifact_id)
        head_number = ((head_now.get("project") or {}).get("version_number"))
        say("")
        say("同一件作品：%s 从第 1 版继续到第 %s 版（同一 artifact，旧版都还在）。"
            % (work_ref, head_number or "N"))
        say("后一次正式判断读到：结果 %d 条、做法 %d 条（TRIAL/HELPED），都按 owner ref 关联。"
            % (len(result_rows), len(method_rows)))
        say("本地草稿：DRAFT -> DEFERRED -> LOCAL_DELIVERED（一条本地回执，未外发）。")
        say("手机读取：head 是第 %s 版，第 1 版仍可逐字读回；head 没有回滚。"
            % (head_number or "N"))
    finally:
        try:
            httpd.shutdown()
        finally:
            httpd.server_close()
        thread.join(timeout=10)

    if failures:
        say("RESULT: FAIL (%d)" % len(failures))
        return 1
    say("RESULT: PASS")
    return 0


def main() -> int:
    if os.name != "posix":
        say("SKIP: this demo needs POSIX (fcntl + AF_UNIX); run in WSL/Linux")
        return 0
    chain = base.build_chain("same-work")
    try:
        return run(chain)
    finally:
        base.close_chain(chain)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

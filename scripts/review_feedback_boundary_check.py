#!/usr/bin/env python3
"""自动回顾候选的来源反馈边界：固定离线入口（真实 Supply/M0/正式 assembly 链）。

它做什么（全部在本机、0 产品模型、0 外网、0 旧实例）
--------------------------------------------------
1. 先用 ``unittest.TestLoader.discover`` 只圈 ``tests/test_review_feedback_boundary.py``，
   真实跑它的 11 个供给层 / owner 类型判定用例；runner 自己的 trace 收进 ``io.StringIO``，
   只按固定用例名与 PASS/FAIL 报告（不回显断言正文）。
2. 用 ``result_method_behavior_smoke`` 的隔离装配起一条**真实**链：真实 Life Supply owner
   （自己的 Unix socket，落盘）、真实 M0 Memory（EvidenceStore + controls）、真实
   ProductionLifeRuntime、B 的实践工作台、AG-0 候选、AG-1 原始 JSON 校验、AG-2 adoption、
   AR-0 受管作品适配器。决策与内容回答是脚本替身（文件里明示），不宣称真实模型自然采用。
3. 第一轮：一件普通作品写入，AG-2 让结果成为可回忆经历 E。
4. 第二轮：脚本 Decision 选中回顾选项，B 的真实 owner 建回顾 A，AR-0 真写版本 R；
   AG-2/M0 依法把这次写作结果记成经历 E2（``artifact_ref`` = A）。
5. 第三轮：正式 CONTINUE 真写回顾的下一版 R2，E3 必须精确对上这次回执的 owner 版本；
   真实 ``review_seed`` / ``build_options`` 不把 E2/E3 当种子，原普通经历 E 仍可作种子，
   E2/E3 仍是两条不同的 M0 可读结果、A/R 仍是可续写的普通作品。
6. 负控：源类型读失败 / 未知 → 本轮自动种子具名扣留，其它候选不消失；普通作品标题以
   「回顾：」开头不误杀；真正回顾换个标题也照 owner 标记分类。

命令::

    python -B scripts/review_feedback_boundary_check.py check --offline

只接受 ``check --offline``；任何真实 prepare/run/模型入口都不在此实现。
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
for entry in (str(ROOT), str(SCRIPT_DIR), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "world" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

CHECKS: list[tuple[str, bool, object]] = []


def check(name: str, condition, detail=None) -> None:
    """Record one fixed-name boolean; only the name and PASS/FAIL are ever printed.

    ``detail`` is kept in-process for debugging but never written out: a failure detail can hold
    owner payloads (prompts, bodies, experience excerpts), and this entry reports a verdict, not a
    dump.  The final summary likewise lists fixed check names only.
    """

    CHECKS.append((name, bool(condition), detail))
    print("CHECK %-72s %s" % (name, "PASS" if condition else "FAIL"))


def bare(value: Any) -> str:
    text = str(value or "").strip()
    while text.startswith("artifact:"):
        text = text[len("artifact:"):]
    return text


_VERSION_PREFIXES = ("artifactversion:", "artifact-version:", "artifact_version:")


def bare_version(value: Any) -> str:
    """One owner version id with *every* known prefix stripped, for exact comparison only.

    A Memory experience stores the owner version as ``artifactversion:artifact-version:<id>``
    (the consumer prefixes ``artifactversion:`` onto an owner ``version_id`` that already carries
    ``artifact-version:``), while the step receipt carries only ``artifact-version:<id>``.  A
    single-pass strip leaves one prefix on one side and none on the other, so the exact-version
    comparison would never match.  Strip repeatedly until stable.
    """

    text = str(value or "").strip()
    stripped = True
    while stripped:
        stripped = False
        for prefix in _VERSION_PREFIXES:
            if text.startswith(prefix):
                text = text[len(prefix):]
                stripped = True
                break
    return text


# ---------------------------------------------------------------------------------------------
# 离线网络守卫：只允许本根 AF_UNIX 正式 owner 通讯
# ---------------------------------------------------------------------------------------------


class NetworkForbidden(RuntimeError):
    """An AF_INET/AF_INET6/DNS call was attempted inside the offline phase."""


class _NetworkGuard:
    def __init__(self) -> None:
        self.attempts = 0
        self._module: Any = None
        self._real_socket: Any = None
        self._real_create_connection: Any = None
        self._real_getaddrinfo: Any = None

    def __enter__(self) -> "_NetworkGuard":
        module = socket
        guard = self
        real = module.socket

        class _GuardedSocket(real):  # type: ignore[misc, valid-type]
            def __init__(self, family: int = -1, type: int = -1, proto: int = -1,
                         fileno: Optional[int] = None) -> None:
                resolved = module.AF_INET if family in (-1, None) else family
                if resolved in (module.AF_INET, module.AF_INET6):
                    guard.attempts += 1
                    raise NetworkForbidden("offline phase forbids AF_INET/AF_INET6 sockets")
                if fileno is None:
                    super().__init__(family, type, proto)
                else:
                    super().__init__(family, type, proto, fileno)

        def _deny(*_args: Any, **_kwargs: Any) -> Any:
            guard.attempts += 1
            raise NetworkForbidden("offline phase forbids outbound network calls")

        self._module = module
        self._real_socket = real
        self._real_create_connection = module.create_connection
        self._real_getaddrinfo = module.getaddrinfo
        module.socket = _GuardedSocket
        module.create_connection = _deny
        module.getaddrinfo = _deny
        return self

    def __exit__(self, *_exc: Any) -> None:
        if self._module is None:
            return
        self._module.socket = self._real_socket
        self._module.create_connection = self._real_create_connection
        self._module.getaddrinfo = self._real_getaddrinfo


# ---------------------------------------------------------------------------------------------
# 新测试文件：只跑这一个文件
# ---------------------------------------------------------------------------------------------


def _run_unit_suite() -> None:
    """Run exactly ``tests/test_review_feedback_boundary.py`` once, here.

    Discovery is pinned to that one file (no old suite is touched).  The runner's own trace is
    captured into a ``StringIO`` and never printed -- a failing assertion's message can carry
    fixture text.  A failure is reported by the case's fixed id; the count check requires the
    whole 11 cases with zero skips.
    """

    import io
    import unittest

    stream = io.StringIO()
    suite = unittest.TestLoader().discover(
        start_dir=str(ROOT / "tests"),
        pattern="test_review_feedback_boundary.py",
        top_level_dir=str(ROOT / "tests"))
    result = unittest.TextTestRunner(stream=stream, verbosity=0).run(suite)
    ran = int(result.testsRun)
    skipped = len(result.skipped)
    failed = [case.id() for case, _ in (result.failures + result.errors)]
    check("unit: tests/test_review_feedback_boundary.py ran all 11 cases, 0 skipped",
          ran == 11 and skipped == 0 and not failed,
          {"ran": ran, "skipped": skipped, "failed": failed})
    for case_id in failed:
        check("unit failed case: %s" % case_id, False)


# ---------------------------------------------------------------------------------------------
# 纯供给层小检查
# ---------------------------------------------------------------------------------------------


def _row(experience_id: str, artifact_ref: str, excerpt: str = "一条真实经历") -> dict[str, Any]:
    return {"experience_id": experience_id,
            "occurred_at": "2026-11-03T08:00:00+00:00",
            "excerpt": excerpt,
            "artifact_ref": artifact_ref,
            "version_ref": "artifactversion:ver-1",
            "outcome_kind": "VERIFIED_RESULT"}


def supply_layer_checks() -> None:
    import life_project_options as lpo

    review = _row("exp:review-result", "artifact:art-review", "回顾写出来的结果")
    ordinary = _row("exp:ordinary", "artifact:art-work", "一次普通写作结果")

    check("supply: a review result alone yields no automatic review seed",
          lpo.review_seed([review], available=True,
                          excluded_artifact_refs=["artifact:art-review"]) is None)
    seed = lpo.review_seed([review, ordinary], available=True,
                           excluded_artifact_refs=["artifact:art-review"])
    check("supply: a review result first does not swallow the ordinary row behind it",
          bool(seed) and [r["experience_id"] for r in seed["experiences"]] == ["exp:ordinary"],
          seed)
    check("supply: an ordinary confirmed result still seeds a review",
          lpo.review_seed([ordinary], available=True,
                          excluded_artifact_refs=["artifact:art-review"]) is not None)
    check("supply: an experience with no artifact source is a legal seed",
          lpo.review_seed([_row("exp:world", "")], available=True,
                          excluded_artifact_refs=["artifact:art-review"]) is not None)
    withheld = lpo.build_options(
        questions=[{"note": "一个普通问题", "marker_id": "m1"}],
        experiences=[review], practice_available=True, review_source_verified=False)
    check("supply: an unclassified source withholds the review seed but not other options",
          not [o for o in withheld if o.get("review")]
          and bool([o for o in withheld if o.get("kind") == lpo.KIND_QUESTION]),
          withheld)


# ---------------------------------------------------------------------------------------------
# 真实链
# ---------------------------------------------------------------------------------------------


def _continue_candidate(chain, artifact_id: str):
    """The exact CONTINUE_CURRENT candidate whose owner target is this artifact (or None)."""

    import past_candidate_bridge as pcb
    from result_method_behavior_smoke import TS0

    wanted = bare(artifact_id)
    candidates = chain.runtime.candidate_materializer.build_candidate_set(
        observed_at=TS0).candidate_refs
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


def real_chain_checks(scratch: Path) -> None:
    import result_method_behavior_smoke as harness
    import life_project_options as lpo

    # The shared harness has its own ``check`` that prints a failure detail.  Route its checks
    # through this entry's fixed-name reporter, so nothing dynamic (prompt/body/excerpt) is ever
    # written out during this run; the conditions themselves are unchanged.
    harness.check = check

    chain = harness.build_chain("review-feedback")
    try:
        # -- round 1: an ordinary work gets a real owner version + experience E ------------
        seed = harness.seed_method_after_first_write(chain)
        ordinary_id = str(seed.experience_id)
        check("round 1 produced a readable ordinary experience", bool(ordinary_id), seed.round_out)

        # -- round 2: a formal Decision selects the review, owner writes A / version R -----
        harness.end_foreground(chain, tag="before-review")
        harness.select_option(chain, review=True)
        chain.client.payload = {"kind": "append_paragraph", "text": harness.REVIEW_BODY,
                                "self_note": "stub: write review"}
        out2 = chain.cycle.evaluate_once(trigger_ref="evt:review-feedback:2", act=True)
        created = dict(out2.get("review_created") or {})
        receipt = dict(out2.get("step_receipt") or {})
        artifact_a = str(created.get("artifact_id") or receipt.get("artifact_id") or "")
        version_r = str(created.get("version_id") or receipt.get("version_id") or "")
        check("round 2 created a real review artifact and wrote version R",
              bool(artifact_a) and bool(version_r) and out2.get("outcome") == "ACTED",
              {"created": created, "receipt": receipt,
               "outcome": out2.get("outcome"), "detail": out2.get("detail")})

        experiences = chain.cycle.practice.experiences()
        e2 = next((row for row in experiences
                   if artifact_a and bare(row.get("artifact_ref")) == bare(artifact_a)), None)
        e2_id = str((e2 or {}).get("experience_id") or "")
        check("round 2's writing result became a recallable experience E2 naming A",
              bool(e2_id), {"artifact": artifact_a, "experiences": experiences})

        # -- owner classification of the source artifact (existing read port) --------------
        classified = chain.cycle.practice.review_result_artifact_ids(
            [(e2 or {}).get("artifact_ref")])
        check("the owner classifies A as a review by its first-line marker",
              str(classified.get("status")) == "READY"
              and artifact_a in (classified.get("review_ids") or []), classified)

        # -- round 3 supply layer: E2 is not the seed; E still is -------------------------
        options = chain.cycle.project_options()
        review_options = [row for row in options if row.get("review")]
        seeded_ids = [str(r.get("experience_id"))
                      for row in review_options for r in (row.get("experiences") or ())]
        check("the automatic review seed does not use the review's own result E2",
              e2_id not in seeded_ids, {"review_options": review_options, "e2": e2_id})
        # Same-title dedupe can hide the *option* (A already has this exact seed title), so the
        # seed itself is checked directly over the real M0 rows and the owner's real set.
        real_seed = lpo.review_seed(
            experiences, available=True,
            excluded_artifact_refs=classified.get("review_ids") or [])
        real_seeded = [str(r.get("experience_id"))
                       for r in (real_seed or {}).get("experiences") or ()]
        check("a real ordinary experience still seeds a review; E2 does not",
              bool(real_seed) and ordinary_id in real_seeded and e2_id not in real_seeded,
              {"seed": real_seed, "ordinary": ordinary_id, "e2": e2_id})
        check("E2 is still readable from Memory (not deleted or hidden)",
              bool(chain.cycle.practice.read_experience(e2_id)), {"e2": e2_id})
        check("the supply-layer withholding is not raised for a classified source",
              not chain.cycle.last_review_seed_withheld,
              {"withheld": chain.cycle.last_review_seed_withheld})

        only_review = lpo.build_options(
            experiences=[e2] if e2 else [],
            practice_available=True,
            review_excluded_artifact_refs=classified.get("review_ids") or [])
        check("with only E2 in the pool there is no automatic review option at all",
              not [row for row in only_review if row.get("review")], only_review)

        # -- A / R stay a continuable ordinary work ---------------------------------------
        review_doc = chain.cycle.practice.read_review(artifact_a)
        head_before = int((review_doc or {}).get("version_number") or 0)
        check("the review document stays readable at the version AR-0 wrote",
              bool(review_doc) and head_before >= 2
              and review_doc.get("content_available") is not False,
              {"review_doc": review_doc, "created_version": version_r})
        check("a review is not withdrawn from the work offers (only methods are)",
              bare(artifact_a) not in
              [bare(item) for item in chain.cycle._non_work_artifact_ids()],
              {"non_work": chain.cycle._non_work_artifact_ids()})

        # -- round 3: a formal CONTINUE must really write the review's next version ---------
        # A missing candidate is a hard failure here, never a "nothing offered, so fine"
        # fallback: this entry exists to prove the formal CONTINUE path, and E3 below depends on
        # it having really executed.
        continue_ref = _continue_candidate(chain, artifact_a)
        check("round 3 offers a real CONTINUE_CURRENT candidate naming the review artifact",
              bool(continue_ref), {"artifact": artifact_a})

        out3: dict[str, Any] = {}
        receipt3: dict[str, Any] = {}
        if continue_ref:
            chain.client.payload = {"kind": "append_paragraph",
                                    "text": "回顾续写：下一段继续写。",
                                    "self_note": "stub: continue review"}
            chain.cognition.script = {"decision": "CONTINUE",
                                      "selected_candidate_id": continue_ref,
                                      "deferred_candidate_ids": []}
            out3 = chain.cycle.evaluate_once(trigger_ref="evt:review-feedback:3", act=True)
            receipt3 = dict(out3.get("step_receipt") or {})
            rd = chain.cycle.practice.read_review(artifact_a) or {}
            check("round 3's formal CONTINUE really wrote the review's next version",
                  out3.get("outcome") == "ACTED"
                  and int(rd.get("version_number") or 0) > head_before,
                  {"outcome": out3.get("outcome"), "head": rd.get("version_number"),
                   "before": head_before})
        else:
            check("round 3's formal CONTINUE really wrote the review's next version", False,
                  {"reason": "no CONTINUE_CURRENT candidate was offered for this artifact"})

        # E3 is the owner-confirmed writing result of *this* CONTINUE -- not merely "some newer
        # experience".  Its version must be the receipt's own version id.
        receipt_version = str(receipt3.get("version_id")
                              or receipt3.get("head_version_id") or "")
        now_experiences = chain.cycle.practice.experiences()
        after_continue = [
            row for row in now_experiences
            if bare(row.get("artifact_ref")) == bare(artifact_a)
            and str(row.get("experience_id")) != e2_id]
        e3 = None
        if receipt_version:
            e3 = next((row for row in after_continue
                       if bare_version(row.get("version_ref")) == bare_version(receipt_version)),
                      None)
        check("round 3's CONTINUE produced its own experience E3 naming A",
              e3 is not None,
              {"artifact": artifact_a, "count": len(after_continue)})
        check("E3's version_ref is exactly the CONTINUE receipt's owner version",
              bool(receipt_version) and e3 is not None
              and bare_version((e3 or {}).get("version_ref")) == bare_version(receipt_version),
              {"receipt_version": receipt_version})
        e3_id = str((e3 or {}).get("experience_id") or "")

        # Use the real owner classification and the real seed/option builders (not only the
        # classifier) over the real M0 rows read back just now.
        classified3 = chain.cycle.practice.review_result_artifact_ids(
            [row.get("artifact_ref") for row in now_experiences])
        review_ids3 = [str(ref) for ref in (classified3.get("review_ids") or ())]
        verified3 = str(classified3.get("status") or "") == "READY"
        check("the owner classifies every review-result source (E2/E3 included) as a review",
              verified3 and bare(artifact_a) in [bare(ref) for ref in review_ids3],
              {"status": classified3.get("status"), "review_ids": review_ids3})

        real_seed3 = lpo.review_seed(
            now_experiences, available=True,
            excluded_artifact_refs=review_ids3, source_verified=verified3)
        seeded3 = [str(row.get("experience_id"))
                   for row in (real_seed3 or {}).get("experiences") or ()]
        check("the real review seed still keeps the ordinary E and never uses E3",
              bool(real_seed3) and ordinary_id in seeded3
              and e3_id not in seeded3 and e2_id not in seeded3,
              {"seeded": seeded3, "ordinary": ordinary_id})

        real_options3 = lpo.build_options(
            experiences=now_experiences, practice_available=True,
            review_excluded_artifact_refs=review_ids3, review_source_verified=verified3)
        review_option_rows = [option for option in real_options3 if option.get("review")]
        option_experience_ids = [
            str(row.get("experience_id"))
            for option in review_option_rows
            for row in (option.get("experiences") or ())]
        check("the real review option is still built on the ordinary E and names neither E2 nor E3",
              bool(review_option_rows) and ordinary_id in option_experience_ids
              and e2_id not in option_experience_ids
              and e3_id not in option_experience_ids,
              {"option_experiences": option_experience_ids})

        # Two different confirmed results both stay in Memory: the newer one does not replace or
        # swallow the older one.
        check("E2 and E3 stay two distinct, readable results in Memory",
              bool(e2_id) and bool(e3_id) and e2_id != e3_id
              and bool(bare_version((e2 or {}).get("version_ref")))
              and bare_version((e2 or {}).get("version_ref"))
              != bare_version((e3 or {}).get("version_ref"))
              and bool(chain.cycle.practice.read_experience(e2_id))
              and bool(chain.cycle.practice.read_experience(e3_id)),
              {"e2": e2_id, "e3": e3_id})
    finally:
        harness.close_chain(chain)


# ---------------------------------------------------------------------------------------------
# entry
# ---------------------------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["check"])
    parser.add_argument("--offline", action="store_true",
                        help="required: only the offline, zero-model, zero-network check runs")
    args = parser.parse_args(argv)
    if not args.offline:
        parser.error("this entry only supports 'check --offline'")
    if os.name != "posix":
        print("SKIP: this check needs POSIX (fcntl + AF_UNIX); run in WSL/Linux")
        return 0

    # Keep the path short: an AF_UNIX socket path must stay under the ~108-byte limit, and the
    # harness appends ``<timestamp>-<pid>-<tag>/supply/life-supply.sock`` beneath this root.
    scratch = Path(tempfile.mkdtemp(prefix="rf-"))
    print("scratch root:", scratch)
    # The harness reads this at import time; keep every fixture under the scratch root.
    os.environ["NYAIRO_RESULT_METHOD_FIXTURE_ROOT"] = str(scratch)

    guard = _NetworkGuard()
    try:
        with guard:
            _run_unit_suite()
            supply_layer_checks()
            real_chain_checks(scratch)
            check("offline guard observed zero AF_INET/AF_INET6/DNS attempts",
                  guard.attempts == 0, {"attempts": guard.attempts})
    except KeyboardInterrupt:
        raise
    except BaseException as exc:  # fixed line only: never a traceback, never str(exc)
        print("ERROR:", type(exc).__name__)
        return 1

    failed = [name for name, ok, _ in CHECKS if not ok]
    print("\nchecks: %d, failed: %d" % (len(CHECKS), len(failed)))
    if failed:
        for name in failed:
            print("FAILED:", name)
        print("RESULT: FAIL")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

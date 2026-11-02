#!/usr/bin/env python3
"""离线实测：回顾标题在自身截断下是否收敛，以及收敛后同标题去重是否真的命中。

范围（只读、离线、零产品调用）

* 调用仓库现成的 ``life_project_options.review_seed`` / ``build_options``、
  ``life_experience_memory_consumer`` 的确证文本投影、``life_practice_context.PracticeSeam``
  的经历节选，以及 ``personal_practice_service`` / ``private_project_service`` 的真实 owner
  写入与读取 API。
* 经历正文、标题、问题、到期事项全部是脚本自己合成的测试文本（带「合成」字样），
  仅代表测试生成，**不**代表自然发生或 Memory 准入。
* owner 使用 ``tests/test_v04_private_projects_stage.py`` 里既有的内存 port 替身
  （它按既有契约回答 CAS / 幂等 / 生命周期），**不是** socket Supply 服务；本脚本不启动
  任何服务、不读写任何实例根、不发 HTTP、不调模型。
* 默认在 ``tempfile.mkdtemp`` 新建根里运行并保留，不 ``TemporaryDirectory``、不 ``rmtree``、
  不裸删任何文件。没有「真实实例根」参数。

三层证据分开记录：

1. ``string`` —— 只跑 ``review_seed``，经历节选直接取上一轮标题（纯字符串递推）。
2. ``build_options`` —— 经 ''确证文本 -> PracticeSeam 节选'' 的真实投影，再跑真实
   ``build_options``，观察是否仍产出 REVIEW 及其它 kind。
3. ``owner`` —— 真实 ``PersonalPracticeService``（内存 port）按既有 API 保存合成回顾，
   用真实 ``list_projects`` 读回标题，再交给 ``build_options``，确认去重用的就是 owner
   已存标题。

私有 helper（``consumer._text`` / ``consumer._refs``）只用可用的那一层做投影，
不构成一次完整运行链路的证明；脚本结论只对上述三个层次成立。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_experience_memory_consumer as lemc  # noqa: E402
import life_practice_context as lpc  # noqa: E402
import life_project_options as lpo  # noqa: E402
import personal_practice_service as pps  # noqa: E402
import private_project_service as prj  # noqa: E402
from test_v04_private_projects_stage import (  # noqa: E402
    GRANT,
    SUBJECT,
    WORKSPACE,
    FakePort,
)

#: 与 ``review_seed`` 内的固定前缀相同；这里只用于结构核对，不另立规则。
REVIEW_PREFIX = "回顾："
TITLE_LIMIT = lpo.MAX_TITLE_CHARS
DESCRIPTION = "合成结果：测试生成，不代表真实发生。"

SYNTH_SHORT_TITLE = "读了三页"          # 6 字
SYNTH_LONG_TITLE = "字" * 95            # 超过 80 字上限的合成标题
#: 正负控专用的一句短合成经历：保证产出的回顾标题离 80 字上限还有距离，
#: 这样「相近但不相等」才有机会被看见（80 字满格的标题再追加字符会被截掉）。
SYNTH_SHORT_EXPERIENCE = "合成短经历：看了一眼窗外。"


def _memory_row(index: int, text: str) -> dict[str, Any]:
    """PracticeSeam 只认这些字段；全部为脚本合成的单行经历。"""

    return {
        "experience_id": "exp:synth:%03d" % index,
        "event_id": "evt:synth:%03d" % index,
        "occurred_at": "2026-10-31T00:00:00Z",
        "text": text,
        "artifact_ref": "artifact:%032x" % index,
        "version_ref": "artifactversion:synth-%03d" % index,
        "owner_result": "合成回执",
        "outcome_kind": "WORK_VERSION_COMMITTED",
    }


class SyntheticMemory:
    """只把脚本合成的一行经历喂给 ``PracticeSeam``；不是 Memory 服务、不落盘。"""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def status(self) -> dict[str, Any]:
        return {"ok": True, "status": "READY", "reason": None, "detail": None,
                "data": {"binding_status": "READY", "recall_ready": True}}

    def list_experiences(self, *, query: str = "", limit: int = 20,
                         since: Any = None) -> dict[str, Any]:
        rows = [dict(row) for row in self.rows[:limit]]
        return {"ok": True, "status": "READY", "reason": None, "detail": None,
                "data": {"experiences": rows, "count": len(rows)}}

    def read_experience(self, experience_id: str) -> dict[str, Any]:
        for row in self.rows:
            if row["experience_id"] == experience_id:
                return {"ok": True, "status": "READY", "reason": None, "detail": None,
                        "data": {"experience": dict(row)}}
        return {"ok": False, "status": "NOT_FOUND", "reason": "MEMORY_EXPERIENCE_NOT_RECALLABLE",
                "detail": "", "data": {"experience": None}}


def confirmed_work_text(consumer: lemc.MemoryAdmissionExperienceConsumer, title: str,
                        index: int) -> str:
    """真实 ``confirmed_context`` + ``_text`` 投影：标题在句首，后面才是结果描述。

    合成事件里 ``artifact_refs`` / ``settlement_refs`` 只是为了让确证上下文通过它自己的
    锚定检查；它们不是任何真实 owner 回执。
    """

    artifact_id = "artifact:%032x" % index
    version_ref = "artifactversion:synth-%03d" % index
    event = {
        "artifact_refs": [artifact_id],
        "settlement_refs": [version_ref],
        "occurred_at": "2026-10-31T00:00:00Z",
        "owner_confirmed_context": {
            "outcome_confirmed": True,
            "artifact_id": artifact_id,
            "version_ref": version_ref,
            "title": title,
            "result_description": DESCRIPTION,
        },
    }
    confirmed, note = consumer.confirmed_context(event)
    if confirmed is None:
        raise SystemExit("合成事件未被确证上下文接收: %s" % note)
    refs = consumer._refs(event, "WORK_VERSION_COMMITTED", confirmed)
    return consumer._text(event, "WORK_VERSION_COMMITTED", refs, confirmed)


def run_string_recursion(initial_title: str, max_rounds: int = 64) -> dict[str, Any]:
    """第 1 层：只跑 ``review_seed``，节选直接取上一轮标题。"""

    rounds: list[dict[str, Any]] = []
    current = initial_title
    previous: str | None = None
    first_full: int | None = None
    for index in range(1, max_rounds + 1):
        seed = lpo.review_seed(
            [{"experience_id": "exp:synth:%03d" % index, "excerpt": current,
              "occurred_at": "2026-10-31T00:00:00Z"}],
            available=True)
        if seed is None:
            rounds.append({"round": index, "review": False})
            return {"rounds": rounds, "converged": False, "review_emitted": False}
        title = str(seed["title"])
        if first_full is None and len(title) >= TITLE_LIMIT:
            first_full = index
        rounds.append({
            "round": index,
            "title_len": len(title),
            "at_limit": len(title) == TITLE_LIMIT,
            "equals_previous": previous is not None and title == previous,
            "prefix_only": len(title) == TITLE_LIMIT and title.startswith(REVIEW_PREFIX),
        })
        if previous is not None and title == previous:
            return {"rounds": rounds, "converged": True, "rounds_to_converge": index,
                    "first_at_limit_round": first_full, "final_len": len(title)}
        previous = title
        current = title
    return {"rounds": rounds, "converged": False, "rounds_to_converge": None,
            "first_at_limit_round": first_full, "final_len": None}


def run_real_projection(initial_title: str, max_rounds: int = 64) -> dict[str, Any]:
    """第 2 层：确证文本 -> PracticeSeam 节选 -> 真实 ``build_options``。"""

    consumer = lemc.MemoryAdmissionExperienceConsumer(port=None, enabled=False, max_chars=600)
    memory = SyntheticMemory()
    seam = lpc.PracticeSeam(None, memory=memory)
    rounds: list[dict[str, Any]] = []
    title = initial_title
    previous: str | None = None
    first_full: int | None = None
    for index in range(1, max_rounds + 1):
        memory.rows = [_memory_row(index, confirmed_work_text(consumer, title, index))]
        experiences = seam.experiences(query="")
        options = lpo.build_options(experiences=experiences, practice_available=True,
                                    questions=[], due_items=[], existing_projects=[])
        review = next((row for row in options if row.get("kind") == lpo.KIND_REVIEW), None)
        kinds = [row.get("kind") for row in options]
        if review is None:
            rounds.append({"round": index, "review": False, "kinds": kinds})
            return {"rounds": rounds, "review_emitted": False, "converged": False,
                    "first_at_limit_round": first_full,
                    "excerpt_len": len(experiences[0]["excerpt"]) if experiences else 0}
        new_title = str(review.get("title") or "")
        if first_full is None and len(new_title) >= TITLE_LIMIT:
            first_full = index
        rounds.append({
            "round": index,
            "kinds": kinds,
            "title_len": len(new_title),
            "at_limit": len(new_title) == TITLE_LIMIT,
            "equals_previous": previous is not None and new_title == previous,
        })
        if previous is not None and new_title == previous:
            return {"rounds": rounds, "review_emitted": True, "converged": True,
                    "rounds_to_converge": index, "final_len": len(new_title),
                    "first_at_limit_round": first_full,
                    "excerpt_len": len(experiences[0]["excerpt"]) if experiences else 0}
        previous = new_title
        title = new_title
    return {"rounds": rounds, "review_emitted": True, "converged": False,
            "rounds_to_converge": None, "final_len": None,
            "first_at_limit_round": first_full}


def run_owner_dedupe(initial_title: str, max_rounds: int = 64) -> dict[str, Any]:
    """第 3 层：真实 owner 保存合成回顾 -> 真实 list_projects -> build_options 去重。"""

    port = FakePort()
    projects = prj.PrivateProjectService(port, WORKSPACE, GRANT, subject_id=SUBJECT)
    practice = pps.PersonalPracticeService(projects, memory_service=None)
    memory = SyntheticMemory()
    seam = lpc.PracticeSeam(practice, memory=memory)
    practice_available = seam.available()
    consumer = lemc.MemoryAdmissionExperienceConsumer(port=None, enabled=False, max_chars=600)

    questions = [{"marker_id": "pnote:synth:1",
                  "note": "合成疑问：只用于观察其它 kind 是否返回。",
                  "source_id": "artifact:synth-question"}]
    due_items = [{"item_ref": "future:synth:1",
                  "title": "合成到期事项：只用于观察其它 kind 是否返回。",
                  "due_at": "2026-10-31T00:00:00Z"}]

    records = list(projects.list_projects().get("projects") or [])
    title = initial_title
    created: list[dict[str, Any]] = []
    rounds: list[dict[str, Any]] = []
    for index in range(1, max_rounds + 1):
        memory.rows = [_memory_row(index, confirmed_work_text(consumer, title, index))]
        experiences = seam.experiences(query="")
        options = lpo.build_options(experiences=experiences,
                                    practice_available=practice_available,
                                    existing_projects=records,
                                    questions=questions, due_items=due_items)
        kinds = [row.get("kind") for row in options]
        review = next((row for row in options if row.get("kind") == lpo.KIND_REVIEW), None)
        if review is None:
            rounds.append({"round": index, "review": False, "kinds": kinds})
            return {
                "practice_available": practice_available,
                "owner_title_sourced": True,
                "created_reviews": len(created),
                "rounds_to_skip": index,
                "dedupe_hit": True,
                "kinds_when_skipped": kinds,
                "other_kinds_returned": [kind for kind in kinds
                                         if kind != lpo.KIND_REVIEW],
                "existing_project_emitted_as_option":
                    any(kind not in lpo.ALL_KINDS for kind in kinds),
                "existing_project_count": len(records),
                "rounds": rounds,
            }

        new_title = str(review.get("title") or "")
        answer = practice.create_review(question="合成回顾问题：测试生成，不是真实经历。",
                                        title=new_title)
        if not isinstance(answer, Mapping) or answer.get("ok") is not True:
            raise SystemExit("真实 owner 拒绝创建合成回顾: %s"
                             % json.dumps(answer, ensure_ascii=False)[:200])
        records = list(projects.list_projects().get("projects") or [])
        matched = [row for row in records
                   if lpo._one_line(row.get("title"), TITLE_LIMIT) == new_title]
        created.append({
            "round": index,
            "title_len": len(new_title),
            "owner_stored_title_matches": bool(matched),
            "owner_record_count": len(records),
        })
        rounds.append({"round": index, "review": True, "kinds": kinds,
                       "owner_stored_title_matches": bool(matched),
                       "title_len": len(new_title)})
        if not matched:
            raise SystemExit("owner 读回的标题与新建标题不一致")
        title = new_title
    return {"practice_available": practice_available, "owner_title_sourced": True,
            "created_reviews": len(created), "dedupe_hit": False,
            "created": created, "rounds": rounds}


def run_controls() -> dict[str, Any]:
    """最小正负控：同标题已存在应跳过；相近但不相等不应跳过。"""

    memory = SyntheticMemory()
    seam = lpc.PracticeSeam(None, memory=memory)
    memory.rows = [_memory_row(1, SYNTH_SHORT_EXPERIENCE)]
    experiences = seam.experiences(query="")
    seed = lpo.review_seed(experiences, available=True)
    if seed is None:
        raise SystemExit("合成经历没有产出回顾种子")
    wanted = str(seed["title"])

    hit = lpo.build_options(experiences=experiences, practice_available=True,
                            existing_projects=[{"title": wanted,
                                                "artifact_id": "artifact:exists"}])
    near = lpo.build_options(experiences=experiences, practice_available=True,
                             existing_projects=[{"title": wanted + "旧",
                                                 "artifact_id": "artifact:near"}])
    far = lpo.build_options(experiences=experiences, practice_available=True,
                            existing_projects=[])

    return {
        "wanted_title_len": len(wanted),
        "near_title_differs_from_wanted": (wanted + "旧") != wanted,
        "positive_existing_same_title_kinds": [row.get("kind") for row in hit],
        "positive_review_skipped": not any(row.get("kind") == lpo.KIND_REVIEW for row in hit),
        "negative_near_title_kinds": [row.get("kind") for row in near],
        "negative_review_emitted": any(row.get("kind") == lpo.KIND_REVIEW for row in near),
        "baseline_no_existing_kinds": [row.get("kind") for row in far],
        "baseline_review_emitted": any(row.get("kind") == lpo.KIND_REVIEW for row in far),
        "existing_project_emitted_as_option":
            any(row.get("kind") not in lpo.ALL_KINDS for row in hit + near + far),
    }


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="nyairo-review-title-boundary-"))
    short_string = run_string_recursion(SYNTH_SHORT_TITLE)
    long_string = run_string_recursion(SYNTH_LONG_TITLE)
    short_real = run_real_projection(SYNTH_SHORT_TITLE)
    long_real = run_real_projection(SYNTH_LONG_TITLE)
    owner = run_owner_dedupe(SYNTH_SHORT_TITLE)
    controls = run_controls()

    layers_agree = (
        short_string.get("rounds_to_converge") == short_real.get("rounds_to_converge")
        and short_string.get("final_len") == short_real.get("final_len")
        and long_string.get("rounds_to_converge") == long_real.get("rounds_to_converge")
        and long_string.get("final_len") == long_real.get("final_len")
    )
    payload = {
        "root": str(root),
        "title_limit": TITLE_LIMIT,
        "excerpt_limit": lpo.MAX_MATERIAL_EXCERPT_CHARS,
        "practice_excerpt_limit": lpc.DEFAULT_EXCERPT_CHARS,
        "synthetic_only": True,
        "owner_port": "in-memory FakePort (契约替身，非 socket Supply)",
        "layers": {
            "string": {"short": short_string, "long": long_string},
            "build_options": {"short": short_real, "long": long_real},
            "owner": owner,
            "controls": controls,
        },
        "string_and_build_options_agree_on_rounds_and_len": layers_agree,
        "conclusions": {
            "title_converges_to_a_fixed_point": bool(short_string.get("converged")
                                                     and long_string.get("converged")),
            "convergence_round_short": short_string.get("rounds_to_converge"),
            "convergence_round_long": long_string.get("rounds_to_converge"),
            "final_title_len_is_the_limit": short_string.get("final_len") == TITLE_LIMIT,
            "same_title_dedupe_hits_after_convergence": bool(owner.get("dedupe_hit")),
            "owner_stored_title_participates_in_dedupe": bool(
                owner.get("dedupe_hit") and owner.get("owner_title_sourced")),
            "other_kinds_still_returned_when_review_skipped": bool(
                owner.get("other_kinds_returned")),
            "existing_projects_are_not_emitted_as_options": not bool(
                owner.get("existing_project_emitted_as_option")
                or controls.get("existing_project_emitted_as_option")),
            "positive_control_same_title_is_deduped": bool(
                controls.get("positive_review_skipped")),
            "negative_control_near_title_is_not_deduped": bool(
                controls.get("negative_review_emitted")
                and controls.get("near_title_differs_from_wanted")),
        },
    }
    target = root / "result.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"root": str(root), "result": str(target),
                      "conclusions": payload["conclusions"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

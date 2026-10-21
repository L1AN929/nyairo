#!/usr/bin/env python3
"""v0.4 B: her own *practice workspace* -- reviews (回顾) and methods (做法).

What this module is
-------------------
One small service over the existing ``ManagedArtifact`` owner:

* **reviews** -- "what did I actually see, where did I see it, what is still unknown".
  A review cites experiences that this subject's Memory can still read back; it is an
  accountable write-up, not a belief and not a stable preference.
* **methods** -- "one concrete thing to try, under which conditions, with what is still
  unknown".  A method draft is **TENTATIVE** from the first byte: it is not a verified
  Skill, not a persona trait, and never becomes one by being tried often.  Only a recorded
  trial with *readable* evidence can move it, and a counterexample moves it to **CONTESTED**.

What it is not
--------------
* **No second library.**  A review and a method *are* ``ManagedArtifact`` rows the existing
  Supply owner already holds.  Every read and every write goes through
  ``PrivateProjectService`` (``list_artifacts`` / ``read_artifact`` / ``create_artifact`` /
  ``commit_version``), which itself only speaks ``LifeArtifactPort``.  No new table, no new
  JSON store, no second Memory, no ``AG0`` source kind.
* **Not a skill registry.**  Nothing here compiles, executes or schedules a method; a method
  is prose a Life/people turn may *read* as document context.
* **Not a Memory.**  Experiences are read through A's ``LifeMemoryService``; this module
  never opens a Memory database, never scans one, and never fabricates an experience id.
  When the Memory service is unbound, a review may still be written as an explicit
  "no experience basis" document -- it simply cannot claim a proven experience.
* **Not an exception-thrower for owner answers.**  Programming mistakes raise
  (``PracticeContractRefused`` / ``PracticeUnavailable``); what an owner answers is always
  a dict with the documented ``status`` values.

The first-line marker (the only definition in the tree)
-------------------------------------------------------
Exactly one marker name, one place to parse it, one place to build it::

    <!-- nyairo-practice:v1 {"kind":"review","title":...} -->
    <blank line>
    <the real body>

* Only the **first line** is ever inspected; a document whose first line is not this marker
  is not a practice document (``is_practice_content`` is false, ``kind`` is ``""``).
* The marker is one physical line of sorted JSON, so a body can never be mistaken for
  metadata -- and, more importantly for this round, **the metadata can never eat the real
  body**: :func:`parse_practice_content` always returns the body verbatim, and
  :meth:`PersonalPracticeService.update_document` rebuilds the marker from what is already
  stored while keeping the caller's body byte-for-byte (a caller that hands back the whole
  content has its leading marker line stripped instead of duplicated).
* The body is what AR0 may continue writing; the metadata is the service's, and a writer
  cannot change it by writing text.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

_LIFE_SCRIPTS = str(Path(__file__).resolve().parent)
if _LIFE_SCRIPTS not in sys.path:
    sys.path.insert(0, _LIFE_SCRIPTS)

from private_project_service import (  # noqa: E402
    ENV_GRANT_NAMES,
    ENV_SOCKET_NAMES,
    ENV_SUBJECT_NAMES,
    ENV_WORKSPACE_NAMES,
    MAX_CONTENT_CHARS,
    MAX_TITLE_CHARS,
    STATUS_ALREADY_APPLIED,
    STATUS_APPLIED,
    STATUS_CONFLICT,
    STATUS_NOT_FOUND,
    STATUS_OK,
    STATUS_REJECTED,
    STATUS_UNAVAILABLE,
    STATUS_UNKNOWN,
    SOURCE_OWNER,
    PrivateProjectService,
    ProjectContractRefused,
    ProjectUnavailable,
    project_operation_key,
)

# ---------------------------------------------------------------------------------------------
# 标记：全树唯一定义
# ---------------------------------------------------------------------------------------------

#: 第一行的标记名。``<!-- nyairo-practice:v1 {JSON} -->``
PRACTICE_MARKER = "nyairo-practice:v1"
MARKER_OPEN = "<!-- "
MARKER_CLOSE = " -->"
MARKER_VERSION = "v1"

#: 两类实践文档。
KIND_REVIEW = "review"
KIND_METHOD = "method"
KINDS: tuple[str, ...] = (KIND_REVIEW, KIND_METHOD)

#: 做法的四个状态。``SUPPORTED`` 不在其中：本阶段没有"已验证做法"这回事。
METHOD_STATE_TENTATIVE = "TENTATIVE"
METHOD_STATE_TRIAL = "TRIAL"
METHOD_STATE_CONTESTED = "CONTESTED"
METHOD_STATE_RETIRED = "RETIRED"
METHOD_STATES: tuple[str, ...] = (METHOD_STATE_TENTATIVE, METHOD_STATE_TRIAL,
                                 METHOD_STATE_CONTESTED, METHOD_STATE_RETIRED)

#: 试用评价的三种结果。``UNKNOWN`` 不变成功，也不算失败。
OUTCOME_HELPED = "HELPED"
OUTCOME_NOT_HELPED = "NOT_HELPED"
OUTCOME_UNKNOWN = "UNKNOWN"
TRIAL_OUTCOMES: tuple[str, ...] = (OUTCOME_HELPED, OUTCOME_NOT_HELPED, OUTCOME_UNKNOWN)

#: 证据核对的两档（"读过且仍然可召回" / "读不到、已撤回或已被治理挡下"）。
CHECK_CURRENT = "CURRENT"
CHECK_UNAVAILABLE = "UNAVAILABLE"

#: 证据类别：一条经历，或一份回顾（回顾本身也依赖经历，所以会递归传播）。
EVIDENCE_EXPERIENCE = "experience"
EVIDENCE_REVIEW = "review"

#: "这棵文档里没有已核实的经历" —— 只是陈述，不是失败。
BASIS_NONE = "none"
BASIS_EXPERIENCE = "experience"

DEFAULT_UNCERTAINTY = "暂定，尚未验证"
NO_EXPERIENCE_NOTE = "这份回顾没有引用任何经历：它是一份计划/问题，不是已证经验的记录。"
NOT_VERIFIED_SKILL_NOTE = ("做法草案不是已验证 Skill，也不是人格或稳定信念："
                           "初始 TENTATIVE，只有带回读证据的试用才能改变它的状态。")
EVIDENCE_WITHHELD_NOTE = ("有证据已经读不回来（被撤回、删除或停止召回）："
                          "依赖它的正文不再透出，只剩元数据。")

MAX_QUESTION_CHARS = 500
MAX_INSTRUCTION_CHARS = 2000
MAX_SCOPE_CHARS = 400
MAX_UNCERTAINTY_CHARS = 400
MAX_TRIAL_DETAIL_CHARS = 600
MAX_BODY_CHARS = 18000
MAX_EVIDENCE_REFS = 12
MAX_REVIEW_REFS = 8
MAX_TRIALS = 12
MAX_REVIEWS = 50
MAX_METHODS = 50
MAX_SCAN = 200
MAX_REF_DEPTH = 4
MAX_EXCERPT_CHARS = 200
DEFAULT_LIMIT = 12

#: A 的 Memory 服务方法（形状固定，缺失就是没接上，不做降级猜测）。
MEMORY_STATUS_METHOD = "status"
MEMORY_READ_METHOD = "read_experience"
MEMORY_LIST_METHOD = "list_experiences"


class PracticeError(RuntimeError):
    """本模块自己的错误基类（编程/接线错误，不是 owner 的答复）。"""


class PracticeContractRefused(PracticeError):
    """调用方给的形状本模块不接受。"""


class PracticeUnavailable(PracticeError):
    """没有可用的 ``PrivateProjectService``（owner 根本没被问过）。"""


# ---------------------------------------------------------------------------------------------
# 纯 helper：marker 的读与写
# ---------------------------------------------------------------------------------------------


def _json_line(payload: Mapping[str, Any]) -> str:
    return json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _marker_line(payload: Mapping[str, Any]) -> str:
    line = MARKER_OPEN + PRACTICE_MARKER + " " + _json_line(payload) + MARKER_CLOSE
    if "\n" in line or "\r" in line:  # json.dumps 已经转义换行；这里只是不变量
        raise PracticeContractRefused("marker line must be one physical line")
    return line


def _split_marker(content: Any) -> tuple[Optional[str], str]:
    """把 content 拆成 ``(JSON 文本 | None, 正文)``。**只认第一行**。"""
    if not isinstance(content, str):
        return None, ""
    first, separator, rest = content.partition("\n")
    line = first.strip()
    if not (line.startswith(MARKER_OPEN) and line.endswith(MARKER_CLOSE)):
        return None, content
    inner = line[len(MARKER_OPEN):-len(MARKER_CLOSE)]
    name, space, blob = inner.partition(" ")
    if not space or name.strip() != PRACTICE_MARKER or not blob.strip():
        return None, content
    body = rest if separator else ""
    # 标记行与正文之间那一个空行是分隔，不是正文的一部分（只剥一个）。
    if body.startswith("\n"):
        body = body[1:]
    return blob.strip(), body


def _clean_ids(value: Any, *, limit: int) -> list[str]:
    """一串 id：宽松接受 str/list/tuple，但绝不编造、绝不去重成一个陌生的东西。"""
    if value is None:
        return []
    if isinstance(value, str):
        items: Iterable[Any] = [value]
    elif isinstance(value, (list, tuple)):
        items = value
    else:
        raise PracticeContractRefused("id list must be a list of strings")
    out: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise PracticeContractRefused("every id must be a string")
        text = item.strip()
        if not text:
            continue
        if len(text) > 200:
            raise PracticeContractRefused("id is too long")
        if text not in out:
            out.append(text)
    if len(out) > limit:
        raise PracticeContractRefused("at most %d refs" % int(limit))
    return out


def _one_line(value: Any, name: str, *, limit: int, allow_empty: bool = False) -> str:
    if value is None and allow_empty:
        return ""
    if not isinstance(value, str):
        raise PracticeContractRefused("%s must be a string" % name)
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    if "\x00" in text:
        raise PracticeContractRefused("%s must not contain NUL" % name)
    line = " ".join(part for part in text.splitlines() if part.strip()).strip()
    if not allow_empty and not line:
        raise PracticeContractRefused("%s must not be blank" % name)
    if len(line) > int(limit):
        raise PracticeContractRefused("%s exceeds %d characters" % (name, int(limit)))
    return line


def _text(value: Any, name: str, *, limit: int, allow_empty: bool = False) -> str:
    if value is None and allow_empty:
        return ""
    if not isinstance(value, str):
        raise PracticeContractRefused("%s must be a string" % name)
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    if "\x00" in text:
        raise PracticeContractRefused("%s must not contain NUL" % name)
    if not allow_empty and not text.strip():
        raise PracticeContractRefused("%s must not be blank" % name)
    if len(text) > int(limit):
        raise PracticeContractRefused("%s exceeds %d characters" % (name, int(limit)))
    return text


def _artifact_id(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PracticeContractRefused("artifact_id is required")
    text = value.strip()
    if len(text) > 200:
        raise PracticeContractRefused("artifact_id is too long")
    return text


def _operation_key(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise PracticeContractRefused("operation_key must be a non-empty string")
    text = value.strip()
    if len(text) > 128:
        raise PracticeContractRefused("operation_key is too long")
    return text


# ---------------------------------------------------------------------------------------------
# 纯 helper：解析 / 构造（C 可以直接用，不用复制第二套 marker 定义）
# ---------------------------------------------------------------------------------------------


def parse_practice_content(content: Any) -> dict[str, Any]:
    """读一件工件的内容：**第一行**的实践标记 + 正文（不抛、不猜）。

    总是返回同一组键；不是实践文档时 ``kind`` 为 ``""``、``body`` 就是整段内容。
    元数据永远不会挤掉正文：``body`` 永远是标记行之后那段文本本身。
    """
    blob, body = _split_marker(content)
    result: dict[str, Any] = {
        "kind": "",
        "marker_present": False,
        "marker_version": "",
        "parse_error": "",
        "marker_line": "",
        "body": body if isinstance(body, str) else "",
        "title": "",
        "question": "",
        "instruction": "",
        "scope": "",
        "uncertainty": "",
        "experience_ids": [],
        "review_refs": [],
        "experience_basis": BASIS_NONE,
        "method_state": "",
        "trials": [],
        "revisions": 0,
        "scope_missing": False,
    }
    if blob is None:
        return result
    try:
        payload = json.loads(blob)
    except ValueError:
        result["marker_present"] = True
        result["marker_version"] = MARKER_VERSION
        result["parse_error"] = "MARKER_JSON_INVALID"
        return result
    if not isinstance(payload, Mapping):
        result["marker_present"] = True
        result["marker_version"] = MARKER_VERSION
        result["parse_error"] = "MARKER_NOT_AN_OBJECT"
        return result
    kind = str(payload.get("kind") or "").strip()
    result["marker_present"] = True
    result["marker_version"] = MARKER_VERSION
    result["marker_line"] = _marker_line(payload)
    if kind not in KINDS:
        result["parse_error"] = "MARKER_KIND_UNKNOWN:%s" % (kind or "missing")
        return result
    result["kind"] = kind
    result["title"] = payload.get("title") if isinstance(payload.get("title"), str) else ""
    result["question"] = (payload.get("question")
                          if isinstance(payload.get("question"), str) else "")
    result["instruction"] = (payload.get("instruction")
                             if isinstance(payload.get("instruction"), str) else "")
    result["scope"] = payload.get("scope") if isinstance(payload.get("scope"), str) else ""
    result["uncertainty"] = (payload.get("uncertainty")
                             if isinstance(payload.get("uncertainty"), str) else "")
    result["experience_ids"] = _ids_out(payload.get("experience_ids"))
    result["review_refs"] = _ids_out(payload.get("review_refs"))
    basis = str(payload.get("experience_basis") or "")
    result["experience_basis"] = BASIS_EXPERIENCE if basis == BASIS_EXPERIENCE else BASIS_NONE
    state = str(payload.get("method_state") or "")
    result["method_state"] = state if state in METHOD_STATES else ""
    result["scope_missing"] = payload.get("scope_missing") is True
    trials = payload.get("trials")
    if isinstance(trials, list):
        for item in trials:
            if not isinstance(item, Mapping):
                continue
            outcome = str(item.get("outcome") or "")
            if outcome not in TRIAL_OUTCOMES:
                continue
            result["trials"].append({
                "outcome": outcome,
                "detail": item.get("detail") if isinstance(item.get("detail"), str) else "",
                "experience_ids": _ids_out(item.get("experience_ids")),
                "version_id": (item.get("version_id")
                               if isinstance(item.get("version_id"), str) else ""),
                "evidence_usable": item.get("evidence_usable") is True,
            })
    revisions = payload.get("revisions")
    if isinstance(revisions, int) and not isinstance(revisions, bool) and revisions >= 0:
        result["revisions"] = int(revisions)
    return result


def _ids_out(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
    return out


def is_practice_content(content: Any) -> bool:
    """这件内容的第一行是不是**有效**的实践标记（A/C 可以不解析就分流）。"""
    parsed = parse_practice_content(content)
    return bool(parsed.get("marker_present")) and not parsed.get("parse_error") \
        and parsed.get("kind") in KINDS


def practice_kind(content: Any) -> str:
    """``review`` / ``method`` / ``""`` —— 只按第一行标记回答。"""
    parsed = parse_practice_content(content)
    if parsed.get("parse_error"):
        return ""
    kind = str(parsed.get("kind") or "")
    return kind if kind in KINDS else ""


def build_practice_content(metadata: Mapping[str, Any], body: str = "") -> str:
    """把元数据 + 正文拼成一件实践文档：第一行标记，然后一个空行，然后正文原样。"""
    if not isinstance(metadata, Mapping):
        raise PracticeContractRefused("metadata must be a mapping")
    kind = str(metadata.get("kind") or "")
    if kind not in KINDS:
        raise PracticeContractRefused("kind must be one of %s" % (", ".join(KINDS)))
    text = body if isinstance(body, str) else ""
    if len(text) > MAX_BODY_CHARS:
        raise PracticeContractRefused("body exceeds %d characters" % MAX_BODY_CHARS)
    payload = {key: value for key, value in dict(metadata).items() if value is not None}
    line = _marker_line(payload)
    if not text:
        return line + "\n"
    return line + "\n\n" + text


# ---------------------------------------------------------------------------------------------
# 服务
# ---------------------------------------------------------------------------------------------


class PersonalPracticeService:
    """回顾与做法：同一条 ManagedArtifact owner 上的两个视图。"""

    def __init__(self, projects: Any, memory_service: Any = None) -> None:
        if projects is None:
            raise PracticeUnavailable("PROJECT_SERVICE_REQUIRED")
        if not isinstance(projects, PrivateProjectService):
            raise PracticeUnavailable("PROJECT_SERVICE_INVALID")
        self.projects = projects
        self.port = projects.port
        self.workspace_id = projects.workspace_id
        self.grant_id = projects.grant_id
        self.subject_id = projects.subject_id
        #: A 的 ``LifeMemoryService``（或同形状替身）。``None`` = 没有接上 Memory。
        self.memory_service = memory_service

    # -- introspection -------------------------------------------------------------------

    def describe(self) -> dict[str, Any]:
        """这个服务接到了什么（不读 owner、不含任何私人文句）。"""
        return {
            "kind": "personal_practice_service",
            "source": SOURCE_OWNER,
            "subject_id": self.subject_id,
            "workspace_id": self.workspace_id,
            "grant_present": bool(self.grant_id),
            "marker": PRACTICE_MARKER,
            "kinds": list(KINDS),
            "method_states": list(METHOD_STATES),
            "trial_outcomes": list(TRIAL_OUTCOMES),
            "memory_bound": self.memory_service is not None,
            "limits": {
                "reviews": MAX_REVIEWS,
                "methods": MAX_METHODS,
                "evidence_refs": MAX_EVIDENCE_REFS,
                "review_refs": MAX_REVIEW_REFS,
                "trials": MAX_TRIALS,
                "scan": MAX_SCAN,
            },
        }

    def memory_bound(self) -> bool:
        return self.memory_service is not None

    # -- 回顾 ----------------------------------------------------------------------------

    def list_reviews(self, *, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
        """列她的回顾（不含正文），最近的在前。纯读。"""
        scanned, rows, skipped = self._scan(KIND_REVIEW)
        cap = _limit(limit, MAX_REVIEWS)
        for row in rows:
            row.pop("_haystack", None)
        rows.sort(key=lambda item: (-_sort_key(item), str(item.get("title") or "")))
        return _envelope(True, STATUS_OK, {
            "projects": rows[:cap],
            "count": len(rows[:cap]),
            "matched": len(rows),
            "truncated": len(rows) > cap,
            "scanned": scanned,
            "skipped": skipped,
            "kind": KIND_REVIEW,
            "memory_bound": self.memory_bound(),
            "note": "回顾是一份可追溯的写法，不是 Memory，也不是稳定信念。",
        })

    def read_review(self, artifact_id: str) -> dict[str, Any]:
        """一份回顾：正文、head、版本历史，以及**每条经历现在是否仍然可读**。

        治理：只要有一条被引用的经历读不回来（撤回 / 删除 / 停止召回 / 调用方无权），
        这一份的正文就不再透出（``content_available=False``，``body=""``），只剩元数据与
        证据核对结果 —— 不会从派生文档里把已被治理挡下的内容漏回来。
        """
        return self._read(KIND_REVIEW, _artifact_id(artifact_id), depth=0, visited=())

    def create_review(
        self,
        *,
        question: Any,
        title: Any = None,
        experience_ids: Any = (),
        operation_key: Any = None,
    ) -> dict[str, Any]:
        """建一份回顾：实际通过 A 读经历，读不回来就拒绝（不杜撰）。

        没有 Memory 时允许建一份**明确没有经历基础**的回顾（``experience_basis=none``）：
        它可以是一份计划或问题，但它不能声称任何已证经验。
        """
        clean_question = _one_line(question, "question", limit=MAX_QUESTION_CHARS)
        ids = _clean_ids(experience_ids, limit=MAX_EVIDENCE_REFS)
        checks = self._check_experiences(ids)
        blocked = [check for check in checks if not check.get("usable")]
        if blocked:
            return _rejected("PRACTICE_EVIDENCE_UNREADABLE", {
                "kind": KIND_REVIEW,
                "experience_checks": checks,
                "detail": "有一半以上的经历读不回来：回顾不能引用读不到的经历。",
            })
        clean_title = self._resolve_title(title, clean_question)
        metadata = {
            "kind": KIND_REVIEW,
            "title": clean_title,
            "question": clean_question,
            "experience_ids": ids,
            "experience_basis": BASIS_EXPERIENCE if ids else BASIS_NONE,
        }
        body = self._initial_review_body(clean_question, ids, checks)
        return self._create(KIND_REVIEW, clean_title, metadata, body, operation_key, {
            "question": clean_question,
            "experience_ids": ids,
            "experience_checks": checks,
            "experience_basis": metadata["experience_basis"],
            "note": "" if ids else NO_EXPERIENCE_NOTE,
        })

    # -- 做法 ----------------------------------------------------------------------------

    def list_methods(self, *, query: str = "", limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
        """列做法草案（不含正文），可按问题/标题/正文做有界确定性检索。"""
        text = query if isinstance(query, str) else ""
        needle = text.strip().lower()
        scanned, rows, skipped = self._scan(KIND_METHOD)
        if needle:
            rows = [row for row in rows if needle in str(row.get("_haystack") or "")]
        for row in rows:
            row.pop("_haystack", None)
        cap = _limit(limit, MAX_METHODS)
        rows.sort(key=lambda item: (-_sort_key(item), str(item.get("title") or "")))
        return _envelope(True, STATUS_OK, {
            "projects": rows[:cap],
            "count": len(rows[:cap]),
            "matched": len(rows),
            "query": text.strip(),
            "truncated": len(rows) > cap,
            "scanned": scanned,
            "skipped": skipped,
            "kind": KIND_METHOD,
            "memory_bound": self.memory_bound(),
            "note": NOT_VERIFIED_SKILL_NOTE,
        })

    def read_method(self, artifact_id: str) -> dict[str, Any]:
        """一条做法：做法、范围、未知、状态，以及全部证据（含被引用回顾的递归核对）。"""
        return self._read(KIND_METHOD, _artifact_id(artifact_id), depth=0, visited=())

    def create_method(
        self,
        *,
        title: Any,
        instruction: Any,
        scope: Any = "",
        uncertainty: Any = "",
        experience_ids: Any = (),
        review_refs: Any = (),
        operation_key: Any = None,
    ) -> dict[str, Any]:
        """建一条**TENTATIVE** 的做法草案：具体做法 + 条件 + 未知，绝不自动升级。"""
        clean_title = _one_line(title, "title", limit=MAX_TITLE_CHARS)
        clean_instruction = _text(instruction, "instruction", limit=MAX_INSTRUCTION_CHARS)
        clean_scope = _one_line(scope, "scope", limit=MAX_SCOPE_CHARS, allow_empty=True)
        clean_uncertainty = _one_line(uncertainty, "uncertainty",
                                      limit=MAX_UNCERTAINTY_CHARS, allow_empty=True)
        ids = _clean_ids(experience_ids, limit=MAX_EVIDENCE_REFS)
        refs = _clean_ids(review_refs, limit=MAX_REVIEW_REFS)
        experience_checks = self._check_experiences(ids)
        review_checks = self._check_reviews(refs, depth=0, visited=())
        blocked = [check for check in (experience_checks + review_checks)
                   if not check.get("usable")]
        if blocked:
            return _rejected("PRACTICE_EVIDENCE_UNREADABLE", {
                "kind": KIND_METHOD,
                "evidence_checks": experience_checks + review_checks,
                "detail": "有引用读不回来：做法不能建立在读不到的证据上。",
            })
        metadata = {
            "kind": KIND_METHOD,
            "title": clean_title,
            "instruction": clean_instruction,
            "scope": clean_scope,
            "scope_missing": not bool(clean_scope),
            "uncertainty": clean_uncertainty or DEFAULT_UNCERTAINTY,
            "method_state": METHOD_STATE_TENTATIVE,
            "experience_ids": ids,
            "review_refs": refs,
            "experience_basis": BASIS_EXPERIENCE if (ids or refs) else BASIS_NONE,
            "trials": [],
            "revisions": 0,
        }
        body = self._initial_method_body(metadata, experience_checks + review_checks)
        return self._create(KIND_METHOD, clean_title, metadata, body, operation_key, {
            "instruction": clean_instruction,
            "scope": clean_scope,
            "uncertainty": metadata["uncertainty"],
            "method_state": METHOD_STATE_TENTATIVE,
            "experience_ids": ids,
            "review_refs": refs,
            "evidence_checks": experience_checks + review_checks,
            "note": NOT_VERIFIED_SKILL_NOTE,
        })

    def record_trial(
        self,
        artifact_id: str,
        *,
        outcome: Any,
        detail: Any = "",
        experience_ids: Any = (),
        expected_head: Any,
        operation_key: Any = None,
    ) -> dict[str, Any]:
        """给一条做法记一次试用评价（CAS 新版本；评价不改原事实）。

        ``HELPED`` / ``NOT_HELPED`` 必须有**经 A 治理仍可读**的经历；没有证据只能记
        ``UNKNOWN``。``NOT_HELPED`` 会让做法变成 ``CONTESTED``（反例不被抹掉），
        ``HELPED`` 只让它成为 ``TRIAL`` —— 试用三次也换不来 ``SUPPORTED``。
        """
        target = _artifact_id(artifact_id)
        clean_outcome = self._outcome(outcome)
        clean_detail = _text(detail, "detail", limit=MAX_TRIAL_DETAIL_CHARS, allow_empty=True)
        ids = _clean_ids(experience_ids, limit=MAX_EVIDENCE_REFS)
        head = self._head(expected_head)
        current = self._require_method(target)
        if current["ok"] is not True:
            return current["answer"]
        parsed = current["parsed"]
        if parsed.get("method_state") == METHOD_STATE_RETIRED:
            return _rejected("PRACTICE_METHOD_RETIRED", {
                "artifact_id": target,
                "detail": "这条做法已经退休：保留文档，但不再接受试用或修订。",
            })
        if current["blocked"]:
            return _rejected("PRACTICE_EVIDENCE_UNAVAILABLE", {
                "artifact_id": target,
                "evidence_checks": current["checks"],
                "detail": EVIDENCE_WITHHELD_NOTE,
            })
        checks = self._check_experiences(ids)
        usable = bool(ids) and all(check.get("usable") for check in checks)
        if clean_outcome != OUTCOME_UNKNOWN and not usable:
            return _rejected("PRACTICE_EVIDENCE_REQUIRED", {
                "artifact_id": target,
                "outcome": clean_outcome,
                "experience_checks": checks,
                "detail": "没有可读证据就不能说有效或无效：只能记 UNKNOWN。",
            })
        metadata = dict(current["metadata"])
        trials = list(parsed.get("trials") or [])
        trials.append({
            "outcome": clean_outcome,
            "detail": clean_detail,
            "experience_ids": ids,
            "version_id": str(head),
            "evidence_usable": usable and clean_outcome != OUTCOME_UNKNOWN,
        })
        metadata["trials"] = _trim_trials(trials)
        metadata["method_state"] = self._state_after_trial(
            str(parsed.get("method_state") or METHOD_STATE_TENTATIVE), clean_outcome, usable)
        answer = self._commit_metadata(target, metadata, parsed.get("body") or "", head,
                                       operation_key, "trial")
        if not answer.get("ok"):
            return answer
        data = dict(answer.get("data") or {})
        data.update({
            "method_state": metadata["method_state"],
            "outcome": clean_outcome,
            "experience_checks": checks,
            "evidence_usable": usable and clean_outcome != OUTCOME_UNKNOWN,
            "trials_recorded": len(metadata["trials"]),
            "note": "记录是评价，不改原事实；UNKNOWN 不变成功。",
        })
        return _envelope(True, answer.get("status") or STATUS_APPLIED, data)

    def revise_method(
        self,
        artifact_id: str,
        *,
        instruction: Any,
        scope: Any = "",
        uncertainty: Any = "",
        expected_head: Any,
        operation_key: Any = None,
    ) -> dict[str, Any]:
        """修订做法：旧版本留在 owner 的历史里，状态**回到 TENTATIVE**，反例不抹除。"""
        target = _artifact_id(artifact_id)
        clean_instruction = _text(instruction, "instruction", limit=MAX_INSTRUCTION_CHARS)
        clean_scope = _one_line(scope, "scope", limit=MAX_SCOPE_CHARS, allow_empty=True)
        clean_uncertainty = _one_line(uncertainty, "uncertainty",
                                      limit=MAX_UNCERTAINTY_CHARS, allow_empty=True)
        head = self._head(expected_head)
        current = self._require_method(target)
        if current["ok"] is not True:
            return current["answer"]
        parsed = current["parsed"]
        if parsed.get("method_state") == METHOD_STATE_RETIRED:
            return _rejected("PRACTICE_METHOD_RETIRED", {
                "artifact_id": target,
                "detail": "退休的做法不再接受修订（文档仍然保留）。",
            })
        metadata = dict(current["metadata"])
        previous = {
            "instruction": parsed.get("instruction") or "",
            "scope": parsed.get("scope") or "",
            "uncertainty": parsed.get("uncertainty") or "",
            "method_state": parsed.get("method_state") or METHOD_STATE_TENTATIVE,
        }
        metadata.update({
            "instruction": clean_instruction,
            "scope": clean_scope,
            "scope_missing": not bool(clean_scope),
            "uncertainty": clean_uncertainty or DEFAULT_UNCERTAINTY,
            "method_state": METHOD_STATE_TENTATIVE,
            "revisions": int(parsed.get("revisions") or 0) + 1,
            "revised_from": previous,
        })
        answer = self._commit_metadata(target, metadata, parsed.get("body") or "", head,
                                       operation_key, "revise")
        if not answer.get("ok"):
            return answer
        data = dict(answer.get("data") or {})
        data.update({
            "method_state": METHOD_STATE_TENTATIVE,
            "revisions": metadata["revisions"],
            "previous": previous,
            "trials_preserved": len(parsed.get("trials") or []),
            "note": "旧版本仍在 owner 的历史里；状态回到 TENTATIVE，反例一条没少。",
        })
        return _envelope(True, answer.get("status") or STATUS_APPLIED, data)

    def retire_method(
        self,
        artifact_id: str,
        *,
        expected_head: Any,
        operation_key: Any = None,
    ) -> dict[str, Any]:
        """退休：逻辑状态 RETIRED，文档、正文、反例全部保留 —— 没有物理删除。"""
        target = _artifact_id(artifact_id)
        head = self._head(expected_head)
        current = self._require_method(target)
        if current["ok"] is not True:
            return current["answer"]
        parsed = current["parsed"]
        if parsed.get("method_state") == METHOD_STATE_RETIRED:
            return _envelope(True, STATUS_ALREADY_APPLIED, {
                "artifact_id": target,
                "method_state": METHOD_STATE_RETIRED,
                "already_retired": True,
                "note": "这条做法已经是 RETIRED：不会写第二个版本。",
            })
        metadata = dict(current["metadata"])
        metadata.update({
            "method_state": METHOD_STATE_RETIRED,
            "retired_from": parsed.get("method_state") or METHOD_STATE_TENTATIVE,
        })
        answer = self._commit_metadata(target, metadata, parsed.get("body") or "", head,
                                       operation_key, "retire")
        if not answer.get("ok"):
            return answer
        data = dict(answer.get("data") or {})
        data.update({
            "method_state": METHOD_STATE_RETIRED,
            "content_kept": True,
            "trials_preserved": len(parsed.get("trials") or []),
            "note": "退休只是状态：文档与每个版本都还在。",
        })
        return _envelope(True, answer.get("status") or STATUS_APPLIED, data)

    # -- 文档续写（AR0 / 界面） ----------------------------------------------------------

    def update_document(
        self,
        artifact_id: str,
        *,
        content: Any,
        expected_head: Any,
        operation_key: Any = None,
    ) -> dict[str, Any]:
        """给回顾或做法**续写正文**（CAS）：元数据由服务重建，正文原样保留。

        AR0 写的是一段文本，不是元数据：``content`` 里若带着第一行标记，标记被剥掉（元数据
        以已存的为准），剩下的才是正文 —— 这样 writer 既不能改写元数据，也不会把正文挤掉。
        """
        target = _artifact_id(artifact_id)
        head = self._head(expected_head)
        text = _text(content, "content", limit=MAX_BODY_CHARS, allow_empty=True)
        current = self._require_practice(target)
        if current["ok"] is not True:
            return current["answer"]
        parsed = current["parsed"]
        if parsed.get("parse_error"):
            return _rejected("PRACTICE_MARKER_INVALID", {
                "artifact_id": target,
                "detail": str(parsed.get("parse_error") or ""),
            })
        body = text
        marker_stripped = False
        if is_practice_content(text):
            _blob, stripped = _split_marker(text)
            body = stripped
            marker_stripped = True
        if not body.strip():
            return _rejected("PRACTICE_BODY_REQUIRED", {
                "artifact_id": target,
                "detail": "续写必须有正文；元数据不能被当成正文提交。",
            })
        answer = self._commit_metadata(target, dict(current["metadata"]), body, head,
                                       operation_key, "document")
        if not answer.get("ok"):
            return answer
        data = dict(answer.get("data") or {})
        data.update({
            "kind": parsed.get("kind"),
            "body_chars": len(body),
            "marker_rebuilt": True,
            "marker_stripped": marker_stripped,
            "metadata_preserved": True,
        })
        return _envelope(True, answer.get("status") or STATUS_APPLIED, data)

    # -- 内部：扫描 / 读 ----------------------------------------------------------------

    def _scan(self, kind: str) -> tuple[int, list[dict[str, Any]], dict[str, int]]:
        """扫一遍 owner 的工件，挑出这一类实践文档（一件一次 read，超出就停）。"""
        rows: list[dict[str, Any]] = []
        skipped = {"other": 0, "practice": 0, "unreadable": 0}
        scanned = 0
        try:
            listed = self.port.list_artifacts()
        except Exception as exc:
            raise PracticeUnavailable("ARTIFACT_LIST_FAILED:%s" % type(exc).__name__) from exc
        for row in listed or ():
            if not isinstance(row, Mapping) or not row.get("artifact_id"):
                continue
            if scanned >= MAX_SCAN:
                break
            scanned += 1
            artifact_id = str(row["artifact_id"])
            try:
                view = self.port.read_artifact(artifact_id)
            except Exception:
                skipped["unreadable"] += 1
                continue
            parsed = parse_practice_content(view.get("content"))
            if parsed.get("kind") not in KINDS:
                skipped["other"] += 1
                continue
            if parsed.get("kind") != kind:
                skipped["practice"] += 1
                continue
            artifact = dict(view.get("artifact") or {})
            head = dict(view.get("head") or {}) if isinstance(view.get("head"), Mapping) else {}
            lifecycle = str(artifact.get("lifecycle") or row.get("lifecycle") or "")
            # 列表行也要过同一道治理：blocked 的行不许带问句 / 指令 / 范围 / 未知
            # （它们都是从受治理的正文里推出来的），也不许被这些词检索到。
            checks = self._evidence_checks(parsed, depth=1, visited=(artifact_id,))
            blocked = any(not check.get("usable") for check in checks)
            if parsed.get("kind") == KIND_METHOD:
                row_out = self._method_row(artifact_id, artifact, head, parsed)
                if blocked:
                    row_out["instruction"] = ""
                    row_out["scope"] = ""
                    row_out["uncertainty"] = ""
                haystack = " ".join((
                    str(parsed.get("title") or ""),
                    "" if blocked else str(parsed.get("instruction") or ""),
                    "" if blocked else str(parsed.get("scope") or ""),
                    "" if blocked else str(parsed.get("uncertainty") or ""),
                    "" if blocked else str(parsed.get("body") or ""),
                ))
            else:
                row_out = self._review_row(artifact_id, artifact, head, parsed)
                if blocked:
                    row_out["question"] = ""
                haystack = " ".join((
                    str(parsed.get("title") or ""),
                    "" if blocked else str(parsed.get("question") or ""),
                    "" if blocked else str(parsed.get("body") or ""),
                ))
            row_out["_haystack"] = haystack.lower()
            row_out["governance_blocked"] = bool(blocked)
            row_out["governance_reason"] = ("PRACTICE_EVIDENCE_UNAVAILABLE" if blocked else "")
            row_out["lifecycle"] = lifecycle
            row_out["status"] = "archived" if lifecycle.upper() == "ARCHIVED" else "active"
            rows.append(row_out)
        return scanned, rows, skipped

    @staticmethod
    def _review_row(artifact_id: str, artifact: Mapping[str, Any], head: Mapping[str, Any],
                    parsed: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "artifact_id": artifact_id,
            "kind": KIND_REVIEW,
            "title": str(artifact.get("title") or parsed.get("title") or ""),
            "question": str(parsed.get("question") or ""),
            "experience_count": len(parsed.get("experience_ids") or []),
            "experience_basis": parsed.get("experience_basis") or BASIS_NONE,
            "revision": artifact.get("revision"),
            "head_version_id": head.get("version_id") or artifact.get("head_version_id"),
            "version_number": head.get("version_number"),
            "created_at": artifact.get("created_at"),
            "updated_at": artifact.get("updated_at"),
            "not_memory": True,
        }

    @staticmethod
    def _method_row(artifact_id: str, artifact: Mapping[str, Any], head: Mapping[str, Any],
                    parsed: Mapping[str, Any]) -> dict[str, Any]:
        trials = list(parsed.get("trials") or [])
        return {
            "artifact_id": artifact_id,
            "kind": KIND_METHOD,
            "title": str(artifact.get("title") or parsed.get("title") or ""),
            "instruction": str(parsed.get("instruction") or ""),
            "scope": str(parsed.get("scope") or ""),
            "uncertainty": str(parsed.get("uncertainty") or ""),
            "method_state": parsed.get("method_state") or METHOD_STATE_TENTATIVE,
            "experience_count": len(parsed.get("experience_ids") or []),
            "review_count": len(parsed.get("review_refs") or []),
            "trial_count": len(trials),
            "counterexample_count": len([row for row in trials
                                         if row.get("outcome") == OUTCOME_NOT_HELPED]),
            "revisions": int(parsed.get("revisions") or 0),
            "revision": artifact.get("revision"),
            "head_version_id": head.get("version_id") or artifact.get("head_version_id"),
            "version_number": head.get("version_number"),
            "created_at": artifact.get("created_at"),
            "updated_at": artifact.get("updated_at"),
            "verified_skill": False,
            "not_memory": True,
        }

    def _read(self, kind: str, artifact_id: str, *, depth: int,
              visited: tuple[str, ...]) -> dict[str, Any]:
        current = self._require_practice(artifact_id, expected_kind=kind)
        if current["ok"] is not True:
            return current["answer"]
        parsed = current["parsed"]
        project = dict(current["project"])
        checks = self._evidence_checks(parsed, depth=depth + 1,
                                       visited=visited + (artifact_id,))
        blocked = any(not check.get("usable") for check in checks)
        content = project.get("content")
        body = parsed.get("body") or ""
        if blocked:
            # 治理挡下：**任何嵌套字段**都不许带受治理的正文，不是只清顶层 body。
            project["content"] = ""
            project["content_available"] = False
        data: dict[str, Any] = {
            "project": project,
            "kind": kind,
            "body": "" if blocked else body,
            "content": "" if blocked else (content if isinstance(content, str) else ""),
            "content_available": not blocked,
            "head_version_id": project.get("head_version_id"),
            "revision": project.get("revision"),
            "governance": {
                "blocked": bool(blocked),
                "reason_code": "PRACTICE_EVIDENCE_UNAVAILABLE" if blocked else "",
                "detail": EVIDENCE_WITHHELD_NOTE if blocked else "",
            },
            "not_memory": True,
        }
        if kind == KIND_REVIEW:
            data.update({
                "question": "" if blocked else (parsed.get("question") or ""),
                "experience_ids": list(parsed.get("experience_ids") or []),
                "experience_basis": parsed.get("experience_basis") or BASIS_NONE,
                "experience_checks": checks,
                "note": (EVIDENCE_WITHHELD_NOTE if blocked
                         else (NO_EXPERIENCE_NOTE
                               if parsed.get("experience_basis") != BASIS_EXPERIENCE
                               else "证据按现在的治理状态逐条核对过。")),
            })
        else:
            trials = list(parsed.get("trials") or [])
            if blocked:
                # 评价的 outcome / 版本号是状态，可以留；`detail` 是依赖受治理内容的推断，不发。
                trials = [dict(row, detail="") for row in trials]
            data.update({
                "instruction": "" if blocked else (parsed.get("instruction") or ""),
                "scope": "" if blocked else (parsed.get("scope") or ""),
                "scope_missing": parsed.get("scope_missing") is True,
                "uncertainty": "" if blocked else (parsed.get("uncertainty")
                                                   or DEFAULT_UNCERTAINTY),
                "method_state": parsed.get("method_state") or METHOD_STATE_TENTATIVE,
                "experience_ids": list(parsed.get("experience_ids") or []),
                "review_refs": list(parsed.get("review_refs") or []),
                "experience_basis": parsed.get("experience_basis") or BASIS_NONE,
                "evidence_checks": checks,
                "trials": trials,
                "counterexamples": [row for row in trials
                                    if row.get("outcome") == OUTCOME_NOT_HELPED],
                "revisions": int(parsed.get("revisions") or 0),
                "verified_skill": False,
                "stable_belief": False,
                "note": (EVIDENCE_WITHHELD_NOTE if blocked else NOT_VERIFIED_SKILL_NOTE),
            })
        if blocked:
            # 元数据与核对结果照实给，正文与依赖它的推断一概不给。
            return _envelope(True, STATUS_OK, data, reason_code="PRACTICE_EVIDENCE_UNAVAILABLE")
        return _envelope(True, STATUS_OK, data)

    def _require_practice(self, artifact_id: str, *,
                          expected_kind: Optional[str] = None) -> dict[str, Any]:
        """读一件工件并解析它的标记；不是这类实践文档就返回一个诚实的拒绝。"""
        try:
            view = self.projects.port.read_artifact(artifact_id)
        except Exception as exc:
            return {"ok": False, "answer": self._owner_failure(exc, artifact_id=artifact_id)}
        artifact = dict(view.get("artifact") or {})
        if not artifact:
            return {"ok": False, "answer": _envelope(
                False, STATUS_NOT_FOUND, {"artifact_id": artifact_id},
                reason_code="PRACTICE_NOT_FOUND")}
        parsed = parse_practice_content(view.get("content"))
        if parsed.get("parse_error"):
            return {"ok": False, "answer": _rejected("PRACTICE_MARKER_INVALID", {
                "artifact_id": artifact_id,
                "detail": str(parsed.get("parse_error") or ""),
            })}
        if parsed.get("kind") not in KINDS:
            return {"ok": False, "answer": _rejected("PRACTICE_NOT_A_DOCUMENT", {
                "artifact_id": artifact_id,
                "detail": "这件工件的第一行不是实践标记，它不是回顾也不是做法。",
            })}
        if expected_kind and parsed.get("kind") != expected_kind:
            return {"ok": False, "answer": _rejected("PRACTICE_KIND_MISMATCH", {
                "artifact_id": artifact_id,
                "expected_kind": expected_kind,
                "actual_kind": parsed.get("kind"),
            })}
        head = dict(view.get("head") or {}) if isinstance(view.get("head"), Mapping) else {}
        lifecycle = str(artifact.get("lifecycle") or "")
        project = {
            "artifact_id": artifact_id,
            "title": str(artifact.get("title") or parsed.get("title") or ""),
            "lifecycle": lifecycle,
            "status": "archived" if lifecycle.upper() == "ARCHIVED" else "active",
            "revision": artifact.get("revision"),
            "head_version_id": head.get("version_id") or artifact.get("head_version_id"),
            "version_number": head.get("version_number"),
            "parent_version_id": head.get("parent_version_id"),
            "created_at": artifact.get("created_at"),
            "updated_at": artifact.get("updated_at"),
            "workspace_id": str(artifact.get("workspace_id") or ""),
            "content": view.get("content") if isinstance(view.get("content"), str) else "",
            "kind": parsed.get("kind"),
            "source": SOURCE_OWNER,
        }
        metadata = self._metadata_from(parsed)
        return {"ok": True, "parsed": parsed, "project": project, "metadata": metadata}

    def _require_method(self, artifact_id: str) -> dict[str, Any]:
        current = self._require_practice(artifact_id, expected_kind=KIND_METHOD)
        if current["ok"] is not True:
            return current
        checks = self._evidence_checks(current["parsed"], depth=1, visited=(artifact_id,))
        current["checks"] = checks
        current["blocked"] = any(not check.get("usable") for check in checks)
        return current

    @staticmethod
    def _metadata_from(parsed: Mapping[str, Any]) -> dict[str, Any]:
        """把解析结果还原成可以写回标记的元数据（只保留已知字段）。"""
        kind = str(parsed.get("kind") or "")
        metadata: dict[str, Any] = {
            "kind": kind,
            "title": str(parsed.get("title") or ""),
            "experience_ids": list(parsed.get("experience_ids") or []),
            "experience_basis": parsed.get("experience_basis") or BASIS_NONE,
        }
        if kind == KIND_REVIEW:
            metadata["question"] = str(parsed.get("question") or "")
        else:
            metadata.update({
                "instruction": str(parsed.get("instruction") or ""),
                "scope": str(parsed.get("scope") or ""),
                "scope_missing": parsed.get("scope_missing") is True,
                "uncertainty": (str(parsed.get("uncertainty") or "")
                                or DEFAULT_UNCERTAINTY),
                "method_state": parsed.get("method_state") or METHOD_STATE_TENTATIVE,
                "review_refs": list(parsed.get("review_refs") or []),
                "trials": list(parsed.get("trials") or []),
                "revisions": int(parsed.get("revisions") or 0),
            })
        return metadata

    # -- 内部：证据 ----------------------------------------------------------------------

    def _check_experiences(self, ids: Iterable[str]) -> list[dict[str, Any]]:
        return [self._check_experience(experience_id) for experience_id in ids]

    def _check_experience(self, experience_id: str) -> dict[str, Any]:
        """一条经历现在还算不算证据（只问 A 的 Memory，绝不自己读库）。"""
        check: dict[str, Any] = {
            "kind": EVIDENCE_EXPERIENCE,
            "experience_id": experience_id,
            "status": CHECK_UNAVAILABLE,
            "usable": False,
            "reason_code": "",
            "detail": "",
            "owner": "",
            "occurred_at": "",
            "outcome_kind": "",
            "excerpt": "",
        }
        if self.memory_service is None:
            check["reason_code"] = "PRACTICE_MEMORY_UNBOUND"
            check["detail"] = "这个实例没有接上 Memory：读不到经历，只能记 UNKNOWN。"
            return check
        reader = getattr(self.memory_service, MEMORY_READ_METHOD, None)
        if not callable(reader):
            check["reason_code"] = "PRACTICE_MEMORY_READER_MISSING"
            check["detail"] = "Memory 服务没有 read_experience。"
            return check
        try:
            answer = reader(experience_id)
        except Exception as exc:
            check["reason_code"] = "PRACTICE_MEMORY_ERROR"
            check["detail"] = type(exc).__name__
            return check
        if not isinstance(answer, Mapping):
            check["reason_code"] = "PRACTICE_MEMORY_ANSWER_INVALID"
            check["detail"] = "Memory 没有回一个 dict。"
            return check
        data = answer.get("data") if isinstance(answer.get("data"), Mapping) else {}
        experience = data.get("experience") if isinstance(data.get("experience"), Mapping) else {}
        if not answer.get("ok") or not experience:
            check["reason_code"] = str(answer.get("reason") or answer.get("reason_code")
                                       or answer.get("status") or "EXPERIENCE_UNREADABLE")
            detail = answer.get("detail")
            check["detail"] = str(detail)[:200] if isinstance(detail, str) else ""
            return check
        found_id = experience.get("experience_id")
        if isinstance(found_id, str) and found_id and found_id != experience_id:
            check["reason_code"] = "EXPERIENCE_ID_MISMATCH"
            check["detail"] = "Memory 回的是一条别的经历。"
            return check
        text = experience.get("text")
        check.update({
            "status": CHECK_CURRENT,
            "usable": True,
            "reason_code": "",
            "detail": "",
            "owner": str(experience.get("owner") or data.get("owner") or ""),
            "occurred_at": str(experience.get("occurred_at") or ""),
            "outcome_kind": str(experience.get("outcome_kind") or ""),
            "excerpt": _excerpt(text),
        })
        return check

    def _check_reviews(self, ids: Iterable[str], *, depth: int,
                      visited: tuple[str, ...]) -> list[dict[str, Any]]:
        return [self._check_review(review_id, depth=depth, visited=visited) for review_id in ids]

    def _check_review(self, review_id: str, *, depth: int,
                      visited: tuple[str, ...]) -> dict[str, Any]:
        """一份被引用的回顾：真的读它，并把它自己的治理状态**递归**传上来。"""
        check: dict[str, Any] = {
            "kind": EVIDENCE_REVIEW,
            "artifact_id": review_id,
            "status": CHECK_UNAVAILABLE,
            "usable": False,
            "reason_code": "",
            "detail": "",
            "title": "",
            "head_version_id": "",
            "experience_count": 0,
        }
        if depth >= MAX_REF_DEPTH:
            check["reason_code"] = "PRACTICE_REF_DEPTH"
            check["detail"] = "引用嵌套太深，不再往下读。"
            return check
        if review_id in visited:
            check["reason_code"] = "PRACTICE_REF_CYCLE"
            check["detail"] = "引用绕回了自己，不再往下读。"
            return check
        answer = self.read_review(review_id) if depth > 0 else self._read(
            KIND_REVIEW, review_id, depth=depth, visited=visited)
        if not isinstance(answer, Mapping) or not answer.get("ok"):
            check["reason_code"] = str((answer or {}).get("reason_code")
                                       or (answer or {}).get("status")
                                       or "PRACTICE_REVIEW_UNREADABLE")
            detail = (answer or {}).get("detail")
            check["detail"] = str(detail)[:200] if isinstance(detail, str) else ""
            return check
        data = answer.get("data") if isinstance(answer.get("data"), Mapping) else {}
        project = data.get("project") if isinstance(data.get("project"), Mapping) else {}
        governance = data.get("governance") if isinstance(data.get("governance"), Mapping) else {}
        check["title"] = str(project.get("title") or "")
        check["head_version_id"] = str(data.get("head_version_id") or "")
        check["experience_count"] = len(data.get("experience_ids") or [])
        if governance.get("blocked"):
            # 递归传播：被引用的回顾自己都读不出正文，做法也就没有可用的证据。
            check["reason_code"] = "PRACTICE_EVIDENCE_UNAVAILABLE"
            check["detail"] = "被引用的回顾已经因为证据读不回来而不再透出正文。"
            return check
        check["status"] = CHECK_CURRENT
        check["usable"] = True
        return check

    def _evidence_checks(self, parsed: Mapping[str, Any], *, depth: int,
                         visited: tuple[str, ...]) -> list[dict[str, Any]]:
        checks = self._check_experiences(parsed.get("experience_ids") or [])
        if parsed.get("kind") == KIND_METHOD:
            checks.extend(self._check_reviews(parsed.get("review_refs") or [],
                                             depth=depth, visited=visited))
        return checks

    # -- 内部：写 ------------------------------------------------------------------------

    def _create(self, kind: str, title: str, metadata: Mapping[str, Any], body: str,
                operation_key: Any, extra: Mapping[str, Any]) -> dict[str, Any]:
        clean_content = build_practice_content(metadata, body)
        key = _operation_key(operation_key) or project_operation_key(
            "practice", kind, self.subject_id, self.workspace_id, title, clean_content)
        answer = self.projects.create_project(title=title, content=clean_content,
                                              operation_key=key)
        project_row = self._row_from_answer(kind, answer, metadata)
        data = dict(extra)
        data.update(self._effect_data(answer, key))
        if project_row:
            data["project"] = project_row
        return self._write_envelope(answer, data, default_reason="PRACTICE_CREATE_REFUSED")

    def _commit_metadata(self, artifact_id: str, metadata: Mapping[str, Any], body: str,
                         expected_head: str, operation_key: Any,
                         action: str) -> dict[str, Any]:
        clean_content = build_practice_content(metadata, body)
        key = _operation_key(operation_key) or project_operation_key(
            "practice", action, artifact_id, expected_head, clean_content)
        answer = self.projects.commit_project(artifact_id=artifact_id,
                                              content=clean_content,
                                              expected_head=expected_head,
                                              operation_key=key)
        return answer if isinstance(answer, Mapping) else {
            "ok": False, "status": STATUS_UNAVAILABLE, "reason_code": "PRACTICE_ANSWER_INVALID",
            "data": {"artifact_id": artifact_id}}

    @staticmethod
    def _row_from_answer(kind: str, answer: Mapping[str, Any],
                         metadata: Mapping[str, Any]) -> dict[str, Any]:
        artifact_id = answer.get("artifact_id")
        if not isinstance(artifact_id, str) or not artifact_id:
            return {}
        row: dict[str, Any] = {
            "artifact_id": artifact_id,
            "kind": kind,
            "title": str(metadata.get("title") or ""),
            "revision": answer.get("revision"),
            "head_version_id": answer.get("version_id") or answer.get("head_version_id"),
            "version_number": answer.get("version_number"),
            "status": "active",
            "lifecycle": answer.get("lifecycle") or "ACTIVE",
            "source": SOURCE_OWNER,
            "not_memory": True,
        }
        if kind == KIND_REVIEW:
            row["question"] = str(metadata.get("question") or "")
            row["experience_count"] = len(metadata.get("experience_ids") or [])
            row["experience_basis"] = metadata.get("experience_basis") or BASIS_NONE
        else:
            row["instruction"] = str(metadata.get("instruction") or "")
            row["scope"] = str(metadata.get("scope") or "")
            row["uncertainty"] = str(metadata.get("uncertainty") or "")
            row["method_state"] = metadata.get("method_state") or METHOD_STATE_TENTATIVE
            row["experience_count"] = len(metadata.get("experience_ids") or [])
            row["review_count"] = len(metadata.get("review_refs") or [])
            row["trial_count"] = len(metadata.get("trials") or [])
            row["verified_skill"] = False
        return row

    @staticmethod
    def _effect_data(answer: Mapping[str, Any], key: str) -> dict[str, Any]:
        data: dict[str, Any] = {"operation_key": key}
        for name in ("artifact_id", "version_id", "version_number", "head_version_id",
                     "parent_version_id", "effect_state", "owner_status", "lifecycle",
                     "revision"):
            if answer.get(name) is not None:
                data[name] = answer[name]
        # After an applied write the version the owner just made *is* the head; say so instead of
        # leaving the caller without a head to write under.
        if data.get("head_version_id") is None and data.get("version_id"):
            data["head_version_id"] = data["version_id"]
        if answer.get("detail"):
            data["detail"] = str(answer["detail"])[:200]
        if answer.get("reconciled"):
            data["reconciled"] = True
        if answer.get("retryable") is not None:
            data["retryable"] = bool(answer["retryable"])
        return data

    @staticmethod
    def _write_envelope(answer: Mapping[str, Any], data: dict[str, Any],
                        *, default_reason: str) -> dict[str, Any]:
        """把项目服务的答复映射成一个信封（owner 的原因码原样保留）。"""
        status = str(answer.get("status") or "")
        reason = str(answer.get("reason_code") or "") or default_reason
        if answer.get("ok") and status in (STATUS_APPLIED, STATUS_ALREADY_APPLIED):
            return _envelope(True, status, data)
        if status == STATUS_CONFLICT:
            return _envelope(False, STATUS_CONFLICT, data, reason_code=reason.upper()[:64])
        if status == STATUS_NOT_FOUND:
            return _envelope(False, STATUS_NOT_FOUND, data, reason_code=reason.upper()[:64])
        if status == STATUS_UNKNOWN:
            return _envelope(False, STATUS_UNKNOWN, data, reason_code=reason.upper()[:64])
        if status == STATUS_REJECTED:
            return _envelope(False, STATUS_REJECTED, data, reason_code=reason.upper()[:64])
        return _envelope(False, STATUS_UNAVAILABLE, data, reason_code=reason.upper()[:64])

    # -- 内部：各种小东西 ----------------------------------------------------------------

    def _resolve_title(self, title: Any, fallback: str) -> str:
        if isinstance(title, str) and title.strip():
            return _one_line(title, "title", limit=MAX_TITLE_CHARS)
        return _one_line(fallback, "title", limit=MAX_TITLE_CHARS)

    @staticmethod
    def _head(value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise PracticeContractRefused(
                "expected_head is required: pass the head_version_id you read before writing")
        text = value.strip()
        if len(text) > 200:
            raise PracticeContractRefused("expected_head is too long")
        return text

    @staticmethod
    def _outcome(value: Any) -> str:
        text = value.strip().upper() if isinstance(value, str) else ""
        if text not in TRIAL_OUTCOMES:
            raise PracticeContractRefused("outcome must be one of %s"
                                          % (", ".join(TRIAL_OUTCOMES)))
        return text

    @staticmethod
    def _state_after_trial(current: str, outcome: str, usable: bool) -> str:
        """试用结果 → 状态。HELPED 只到 TRIAL；UNKNOWN 什么也不改。"""
        if not usable or outcome == OUTCOME_UNKNOWN:
            return current if current in METHOD_STATES else METHOD_STATE_TENTATIVE
        if outcome == OUTCOME_NOT_HELPED:
            return METHOD_STATE_CONTESTED
        if outcome == OUTCOME_HELPED:
            return METHOD_STATE_TRIAL
        return current

    @staticmethod
    def _initial_review_body(question: str, ids: list[str],
                             checks: list[dict[str, Any]]) -> str:
        lines = ["## 当时看到的问题", question, "", "## 引用的经历"]
        if not ids:
            lines.append("- （没有引用任何经历：这是一份计划，不是已证记录。）")
        else:
            for check in checks:
                when = check.get("occurred_at") or "时间未提供"
                excerpt = check.get("excerpt") or "（服务没有返回正文节选）"
                lines.append("- %s · %s · %s" % (check.get("experience_id"), when, excerpt))
        lines.extend(["", "## 我还不知道的", "- （待补：哪些地方仍然没有证据。）"])
        return "\n".join(lines)

    @staticmethod
    def _initial_method_body(metadata: Mapping[str, Any],
                             checks: list[dict[str, Any]]) -> str:
        lines = [
            "## 做法",
            str(metadata.get("instruction") or ""),
            "",
            "## 适用条件",
            str(metadata.get("scope") or "") or "（还没有划定范围：先当它只在这一次成立。）",
            "",
            "## 我还不知道的",
            str(metadata.get("uncertainty") or DEFAULT_UNCERTAINTY),
            "",
            "## 依据",
        ]
        if not checks:
            lines.append("- （没有引用经历或回顾：这是一张草案。）")
        else:
            for check in checks:
                if check.get("kind") == EVIDENCE_REVIEW:
                    lines.append("- 回顾 %s（%s）" % (check.get("artifact_id"),
                                                    check.get("title") or "无标题"))
                else:
                    lines.append("- 经历 %s · %s" % (check.get("experience_id"),
                                                     check.get("excerpt") or "（无节选）"))
        return "\n".join(lines)

    @staticmethod
    def _owner_failure(exc: Exception, *, artifact_id: str = "") -> dict[str, Any]:
        """把 owner 的一个拒绝映射成一个信封：owner 的原因码原样保留。"""
        name = type(exc).__name__
        data: dict[str, Any] = {}
        if artifact_id:
            data["artifact_id"] = artifact_id
        if "Unavailable" in name:
            return _envelope(False, STATUS_UNAVAILABLE, data,
                             reason_code="PRACTICE_OWNER_UNAVAILABLE")
        owner_status = str(getattr(exc, "status", "") or "")
        reason = str(getattr(exc, "reason", "") or "") or owner_status or "OWNER_REFUSED"
        detail = str(getattr(exc, "detail", "") or str(exc))
        data["detail"] = detail[:200]
        status = STATUS_NOT_FOUND if owner_status == "NOT_FOUND" else STATUS_UNAVAILABLE
        return _envelope(False, status, data, reason_code=reason.upper()[:64])


def _trim_trials(trials: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """有界保留试用记录：满了先丢最旧的**非反例**条目，反例尽量留住。"""
    if len(trials) <= MAX_TRIALS:
        return trials
    trimmed = list(trials)
    while len(trimmed) > MAX_TRIALS:
        drop_at = -1
        for index, row in enumerate(trimmed):
            if row.get("outcome") != OUTCOME_NOT_HELPED:
                drop_at = index
                break
        if drop_at < 0:
            drop_at = 0
        trimmed.pop(drop_at)
    return trimmed


def _excerpt(text: Any) -> str:
    if not isinstance(text, str):
        return ""
    cleaned = " ".join(text.split())
    return cleaned[:MAX_EXCERPT_CHARS]


def _sort_key(row: Mapping[str, Any]) -> float:
    for name in ("updated_at", "created_at"):
        value = row.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return 0.0


def _limit(value: Any, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return min(DEFAULT_LIMIT, maximum)
    return max(1, min(int(value), int(maximum)))


def _envelope(ok: bool, status: str, data: Optional[Mapping[str, Any]] = None,
              reason_code: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": bool(ok),
        "status": str(status),
        "source": SOURCE_OWNER,
        "data": dict(data or {}),
    }
    if reason_code:
        payload["reason_code"] = str(reason_code)
    return payload


def _rejected(reason_code: str, data: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    return _envelope(False, STATUS_REJECTED, data, reason_code=reason_code)


def _first_env(environment: Mapping[str, str], names: Iterable[str]) -> Optional[str]:
    for name in names:
        value = environment.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def memory_service_from_env(environment: Optional[Mapping[str, str]] = None) -> Any:
    """A 的 Memory 工厂（可能返回未绑定服务）；A 的模块不在时返回 ``None``。"""
    env = dict(environment or {})
    try:
        from life_memory_service import memory_service_from_env as factory  # A 的文件
    except Exception:
        return None
    try:
        return factory(env)
    except Exception:
        return None


def practice_from_env(port: Any = None,
                      env: Optional[Mapping[str, str]] = None) -> Optional[PersonalPracticeService]:
    """按同一套 env bindings 建一个可用的实践工作台；缺一件就返回 ``None``。

    ``port`` 可以是一个现成的 ``LifeArtifactPort``，也可以是 socket 路径；省略时按
    ``private_project_service`` 的同一套 env 名建一个。Memory 服务用 A 的工厂
    （``life_memory_service.memory_service_from_env``）：它连未绑定的服务也返回，所以
    "没有 Memory" 与 "有 Memory 但没绑定" 在这里都是可用的工作台，只是不能声称已证经验。
    """
    environment: dict[str, str] = dict(env or {})
    try:
        workspace_id = _first_env(environment, ENV_WORKSPACE_NAMES)
        grant_id = _first_env(environment, ENV_GRANT_NAMES)
        subject_id = _first_env(environment, ENV_SUBJECT_NAMES)
        if not workspace_id or not grant_id or not subject_id:
            return None
        resolved = port
        if resolved is None or isinstance(resolved, str):
            if isinstance(resolved, str) and resolved.strip():
                from life_artifact_port import LifeArtifactPort

                resolved = LifeArtifactPort(resolved.strip(), subject_id)
            else:
                from private_project_service import life_supply_port_from_env

                resolved = life_supply_port_from_env(environment)
        projects = PrivateProjectService(resolved, workspace_id, grant_id)
        return PersonalPracticeService(projects,
                                       memory_service=memory_service_from_env(environment))
    except (ProjectUnavailable, ProjectContractRefused, PracticeContractRefused,
            PracticeUnavailable):
        return None
    except Exception:
        # 形态不对的 port / env：宁可回答"没有可用工作台"，也不升一个半坏的上来。
        return None


#: A/C 可能用别的名字找同一个工厂。
practice_service_from_env = practice_from_env
personal_practice_from_env = practice_from_env
from_env = practice_from_env


__all__ = [
    "BASIS_EXPERIENCE",
    "BASIS_NONE",
    "CHECK_CURRENT",
    "CHECK_UNAVAILABLE",
    "DEFAULT_LIMIT",
    "DEFAULT_UNCERTAINTY",
    "EVIDENCE_EXPERIENCE",
    "EVIDENCE_REVIEW",
    "KINDS",
    "KIND_METHOD",
    "KIND_REVIEW",
    "MARKER_CLOSE",
    "MARKER_OPEN",
    "MARKER_VERSION",
    "MAX_BODY_CHARS",
    "MAX_EVIDENCE_REFS",
    "MAX_EXCERPT_CHARS",
    "MAX_INSTRUCTION_CHARS",
    "MAX_METHODS",
    "MAX_QUESTION_CHARS",
    "MAX_REVIEWS",
    "MAX_REVIEW_REFS",
    "MAX_SCOPE_CHARS",
    "MAX_TRIALS",
    "MAX_TRIAL_DETAIL_CHARS",
    "MAX_UNCERTAINTY_CHARS",
    "METHOD_STATES",
    "METHOD_STATE_CONTESTED",
    "METHOD_STATE_RETIRED",
    "METHOD_STATE_TENTATIVE",
    "METHOD_STATE_TRIAL",
    "NOT_VERIFIED_SKILL_NOTE",
    "NO_EXPERIENCE_NOTE",
    "OUTCOME_HELPED",
    "OUTCOME_NOT_HELPED",
    "OUTCOME_UNKNOWN",
    "PRACTICE_MARKER",
    "PersonalPracticeService",
    "PracticeContractRefused",
    "PracticeError",
    "PracticeUnavailable",
    "TRIAL_OUTCOMES",
    "build_practice_content",
    "from_env",
    "is_practice_content",
    "memory_service_from_env",
    "parse_practice_content",
    "personal_practice_from_env",
    "practice_from_env",
    "practice_kind",
    "practice_service_from_env",
]

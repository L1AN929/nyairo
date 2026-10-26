#!/usr/bin/env python3
"""v0.5 同一件作品的可读上下文：head / 旧版与来历 / 真实结果 / 用过的做法与评价 / 本地草稿。

这不是新的 owner，也不是新的持久化：每一段都只是把**已经存在**的 owner 读口按同一件作品
聚合一次，并如实标出每段来自谁、读没读成。

关联规则（唯一的，缺证据就留空）：

* ``work`` / ``versions`` / ``provenance`` -- B 的 ``PrivateProjectService`` 对同一个
  ``artifact_id`` 的读；
* ``results`` -- A 的 Memory 读口返回的**可回忆经历**，只保留 ``artifact_ref`` 与目标作品
  相同的那几条；``version_ref`` / ``owner_result`` 原样保留，不把标题或方法文档认领成结果；
* ``methods`` -- 只列**证据真的指回该作品结果**的做法：方法的 ``experience_ids`` 或某条
  trial 的 ``experience_ids`` 与该作品的经历 id 有交集；交集为空的做法不出现（不是"没关系"，
  而是"这件事上没有证据"）。trial 的 ``version_id`` 是**方法自己的版本**，与作品版本分开；
* ``drafts`` -- 本地分享草稿里 ``source.artifact_id`` 与目标作品相同的那些；带来源版本与
  当前位置（``stale`` / ``matches_recorded``），不带正文。

读失败一个段落不影响其它段落，但失败本身必须具名（``reason_code``），绝不变成"空"。
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

SCHEMA_VERSION = "chiyo.life.work_context.v1"

DEFAULT_MAX_VERSIONS = 5
DEFAULT_MAX_RESULTS = 5
DEFAULT_MAX_METHODS = 6
DEFAULT_MAX_DRAFTS = 5
DEFAULT_TEXT_CHARS = 280
#: How many newest governed experiences are scanned for "belongs to this work".  This is a read
#: bound, not a claim that older results do not exist -- the answer says which window it used.
DEFAULT_MEMORY_SCAN = 60
#: How many method documents may be read (one governed read each) to decide the association.
DEFAULT_METHOD_SCAN = 8

STATE_OK = "OK"
STATE_PARTIAL = "PARTIAL"
STATE_UNAVAILABLE = "UNAVAILABLE"
STATE_NO_BOUND_WORK = "NO_BOUND_WORK"

REASON_ID_REQUIRED = "WORK_ARTIFACT_ID_REQUIRED"
REASON_PROJECTS_UNWIRED = "WORK_CONTEXT_PROJECTS_NOT_WIRED"
REASON_MEMORY_UNWIRED = "WORK_CONTEXT_MEMORY_NOT_WIRED"
REASON_PRACTICE_UNWIRED = "WORK_CONTEXT_PRACTICE_NOT_WIRED"
REASON_SHARING_UNWIRED = "WORK_CONTEXT_SHARING_NOT_WIRED"

NOTE = (
    "同一件作品的有界上下文：版本、来历、真实结果、证据指回该作品的做法与最近一次评价、"
    "以及指向该作品的本地草稿。全部来自既有 owner 的读口；读不到是具名失败，不是空。"
)


def _line(value: Any, limit: int = 120) -> str:
    text = " ".join(str(value or "").split())
    if limit and len(text) > limit:
        return text[:limit].rstrip() + "…"
    return text


def _int_or_none(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def bare_artifact_id(value: Any) -> str:
    """``artifact:<id>`` / ``<id>`` -> ``<id>`` (the owner's own prefix is never rewritten)."""

    text = str(value or "").strip()
    while text.startswith("artifact:"):
        text = text[len("artifact:"):]
    return text


def same_artifact(first: Any, second: Any) -> bool:
    """Two owner ids name the same artifact.  Prefix noise is the only normalisation."""

    left = bare_artifact_id(first)
    right = bare_artifact_id(second)
    return bool(left) and left == right


def _answer_data(answer: Any) -> tuple[dict[str, Any], str]:
    """One owner envelope -> ``(data, reason_code)``; a refusal keeps its own reason."""

    if not isinstance(answer, Mapping):
        return {}, "WORK_CONTEXT_ANSWER_INVALID"
    data = answer.get("data") if isinstance(answer.get("data"), Mapping) else {}
    if answer.get("ok") is False:
        return dict(data), str(answer.get("reason_code") or answer.get("status")
                               or "WORK_CONTEXT_REFUSED")[:80]
    status = str(answer.get("status") or "")
    if answer.get("ok") is None and status and status.lower() != "ok":
        return dict(data), str(answer.get("reason_code") or status)[:80]
    return dict(data), ""


class WorkContextReader:
    """One bounded, read-only aggregation over ports that already exist."""

    def __init__(
        self,
        *,
        projects: Any = None,
        practice: Any = None,
        sharing: Any = None,
        memory: Any = None,
        max_versions: int = DEFAULT_MAX_VERSIONS,
        max_results: int = DEFAULT_MAX_RESULTS,
        max_methods: int = DEFAULT_MAX_METHODS,
        max_drafts: int = DEFAULT_MAX_DRAFTS,
        text_chars: int = DEFAULT_TEXT_CHARS,
        memory_scan: int = DEFAULT_MEMORY_SCAN,
        method_scan: int = DEFAULT_METHOD_SCAN,
    ) -> None:
        self.projects = projects
        #: A ``life_practice_context.PracticeSeam`` (``methods`` + ``read_method``).
        self.practice = practice
        #: B's ``LifeSharingService`` or the phone's ``PhoneSharing`` (``list_shares`` /
        #: ``listing``): both answer ``data.shares`` rows whose ``source`` is the artifact.
        self.sharing = sharing
        #: A's ``LifeMemoryService`` (``list_experiences``) or a raw ``LifeExperiencePort``
        #: (``recent``).  Neither is re-implemented here.
        self.memory = memory
        self.max_versions = max(0, int(max_versions))
        self.max_results = max(0, int(max_results))
        self.max_methods = max(0, int(max_methods))
        self.max_drafts = max(0, int(max_drafts))
        self.text_chars = max(0, int(text_chars))
        self.memory_scan = max(0, int(memory_scan))
        self.method_scan = max(0, int(method_scan))

    # -- the one entry -------------------------------------------------------------------

    def read(self, artifact_id: Any, *, version_id: Any = "") -> dict[str, Any]:
        target = str(artifact_id or "").strip()
        requested = str(version_id or "").strip()
        out: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "artifact_id": target,
            "requested_version_id": requested or None,
            "note": NOTE,
            "bounds": {
                "max_versions": self.max_versions,
                "max_results": self.max_results,
                "max_methods": self.max_methods,
                "max_drafts": self.max_drafts,
                "memory_scan": self.memory_scan,
                "method_scan": self.method_scan,
            },
        }
        if not target:
            out.update({"state": STATE_UNAVAILABLE, "reason_code": REASON_ID_REQUIRED})
            return out

        work = self._read_work(target)
        versions = self._read_versions(target)
        provenance = self._read_provenance(target)
        results, result_scan = self._read_results(target)
        methods = self._read_methods(target, results)
        drafts = self._read_drafts(target)
        # The association input never leaves this reader.
        results.pop("_all_ids", None)

        out.update({
            "work": work,
            "versions": versions,
            "provenance": provenance,
            "results": results,
            "result_scan": result_scan,
            "methods": methods,
            "drafts": drafts,
        })
        out["state"] = (STATE_OK if work.get("available") else
                        STATE_PARTIAL if (results.get("rows") or versions.get("entries")
                                          or drafts.get("rows"))
                        else STATE_UNAVAILABLE)
        if not work.get("available") and not work.get("reason_code"):
            out["state"] = STATE_UNAVAILABLE
        return out

    # -- work head / versions / provenance ------------------------------------------------

    def _read_work(self, target: str) -> dict[str, Any]:
        if self.projects is None:
            return {"available": False, "reason_code": REASON_PROJECTS_UNWIRED}
        try:
            answer = self.projects.read_project(target, include_history=False)
        except Exception as exc:
            return {"available": False,
                    "reason_code": "WORK_CONTEXT_PROJECT_READ_FAILED",
                    "detail": "%s: %s" % (type(exc).__name__, str(exc)[:120])}
        if not isinstance(answer, Mapping):
            return {"available": False, "reason_code": "WORK_CONTEXT_PROJECT_ANSWER_INVALID"}
        if not answer.get("ok"):
            return {"available": False,
                    "reason_code": str(answer.get("reason_code") or answer.get("status")
                                       or "WORK_CONTEXT_PROJECT_UNAVAILABLE")[:80],
                    "detail": str(answer.get("detail") or "")[:160]}
        project = answer.get("project") if isinstance(answer.get("project"), Mapping) else {}
        return {
            "available": True,
            "artifact_id": str(answer.get("artifact_id") or project.get("artifact_id") or target),
            "title": _line(project.get("title"), 160),
            "lifecycle": str(project.get("lifecycle") or ""),
            "status": str(project.get("status") or ""),
            "head_version_id": str(project.get("head_version_id") or ""),
            "version_number": _int_or_none(project.get("version_number")),
            "parent_version_id": str(project.get("parent_version_id") or "") or None,
            "content_chars": _int_or_none(project.get("content_chars")),
            "content_available": project.get("content_available") is not False,
            "continuable": project.get("continuable") is not False,
            "source_tag": str(project.get("source") or "ManagedArtifact"),
        }

    def _read_versions(self, target: str) -> dict[str, Any]:
        if self.projects is None:
            return {"state": STATE_UNAVAILABLE, "reason_code": REASON_PROJECTS_UNWIRED,
                    "entries": []}
        try:
            answer = self.projects.read_project_history(target, limit=max(1, self.max_versions))
        except Exception as exc:
            return {"state": STATE_UNAVAILABLE,
                    "reason_code": "WORK_CONTEXT_HISTORY_READ_FAILED",
                    "detail": "%s: %s" % (type(exc).__name__, str(exc)[:120]), "entries": []}
        if not isinstance(answer, Mapping) or not answer.get("ok"):
            reason = (str(answer.get("reason_code") or answer.get("status")
                          or "WORK_CONTEXT_HISTORY_UNAVAILABLE")[:80]
                      if isinstance(answer, Mapping) else "WORK_CONTEXT_HISTORY_ANSWER_INVALID")
            return {"state": STATE_UNAVAILABLE, "reason_code": reason, "entries": []}
        entries: list[dict[str, Any]] = []
        for row in answer.get("entries") or ():
            if not isinstance(row, Mapping):
                continue
            version_id = str(row.get("version_id") or "")
            if not version_id:
                continue
            entries.append({
                "version_id": version_id,
                "version_number": _int_or_none(row.get("version_number")),
                "parent_version_id": str(row.get("parent_version_id") or "") or None,
                "created_at": str(row.get("created_at") or "")[:40],
                "content_chars": _int_or_none(row.get("content_chars")),
            })
            if len(entries) >= self.max_versions:
                break
        return {
            "state": STATE_OK,
            "entries": entries,
            "count": _int_or_none(answer.get("count")),
            "total_versions": _int_or_none(answer.get("total_versions")),
            "has_more": bool(answer.get("has_more") or
                             (answer.get("total_versions") or 0) > len(entries)),
        }

    def _read_provenance(self, target: str) -> dict[str, Any]:
        if self.projects is None:
            return {"state": STATE_UNAVAILABLE, "reason_code": REASON_PROJECTS_UNWIRED}
        try:
            answer = self.projects.read_project_provenance(target)
        except Exception as exc:
            return {"state": STATE_UNAVAILABLE,
                    "reason_code": "WORK_CONTEXT_PROVENANCE_READ_FAILED",
                    "detail": "%s: %s" % (type(exc).__name__, str(exc)[:120])}
        if not isinstance(answer, Mapping) or not answer.get("ok"):
            reason = (str(answer.get("reason_code") or answer.get("status")
                          or "WORK_CONTEXT_PROVENANCE_UNAVAILABLE")[:80]
                      if isinstance(answer, Mapping)
                      else "WORK_CONTEXT_PROVENANCE_ANSWER_INVALID")
            return {"state": STATE_UNAVAILABLE, "reason_code": reason}
        provenance = answer.get("provenance")
        return {
            "state": STATE_OK if provenance else "NONE",
            "reason_code": str(answer.get("reason_code") or "")[:80] or None,
            "provenance": dict(provenance) if isinstance(provenance, Mapping) else None,
            "version_number": _int_or_none(answer.get("version_number")),
            "source_gate": (dict(answer.get("source_gate"))
                            if isinstance(answer.get("source_gate"), Mapping) else None),
        }

    # -- results (A's own recallable outcomes) --------------------------------------------

    def _read_results(self, target: str) -> tuple[dict[str, Any], dict[str, Any]]:
        if self.memory is None:
            return ({"state": STATE_UNAVAILABLE, "reason_code": REASON_MEMORY_UNWIRED,
                     "rows": []},
                    {"owner": "Memory", "state": STATE_UNAVAILABLE,
                     "reason_code": REASON_MEMORY_UNWIRED})
        rows: list[dict[str, Any]]
        reason = ""
        try:
            if callable(getattr(self.memory, "list_experiences", None)):
                answer = self.memory.list_experiences(query="", limit=max(1, self.memory_scan))
                data, reason = _answer_data(answer)
                raw_rows = data.get("experiences") if isinstance(
                    data.get("experiences"), list) else []
                rows = [dict(row) for row in raw_rows if isinstance(row, Mapping)]
            elif callable(getattr(self.memory, "recent", None)):
                raw_rows = self.memory.recent(limit=max(1, self.memory_scan))
                rows = []
                for row in raw_rows or ():
                    if hasattr(row, "to_dict"):
                        row = row.to_dict()
                    if isinstance(row, Mapping):
                        rows.append(dict(row))
            else:
                return ({"state": STATE_UNAVAILABLE,
                         "reason_code": "WORK_CONTEXT_MEMORY_UNREADABLE", "rows": []},
                        {"owner": "Memory", "state": STATE_UNAVAILABLE,
                         "reason_code": "WORK_CONTEXT_MEMORY_UNREADABLE"})
        except Exception as exc:
            return ({"state": STATE_UNAVAILABLE,
                     "reason_code": "WORK_CONTEXT_MEMORY_READ_FAILED",
                     "detail": "%s: %s" % (type(exc).__name__, str(exc)[:120]), "rows": []},
                    {"owner": "Memory", "state": STATE_UNAVAILABLE,
                     "reason_code": "WORK_CONTEXT_MEMORY_READ_FAILED"})

        owned: list[dict[str, Any]] = []
        scanned = len(rows)
        for row in rows:
            if not same_artifact(row.get("artifact_ref"), target):
                continue
            owned.append({
                "experience_id": str(row.get("experience_id") or row.get("event_id") or ""),
                "occurred_at": str(row.get("occurred_at") or "")[:40],
                # The owner ref that made this row belong to the work: kept verbatim.
                "artifact_ref": str(row.get("artifact_ref") or "") or None,
                "version_ref": str(row.get("version_ref") or "") or None,
                "owner_result": str(row.get("owner_result") or "") or None,
                "outcome_kind": str(row.get("outcome_kind") or "") or None,
                "activity_ref": str(row.get("activity_ref") or "") or None,
                "text": _line(row.get("text"), self.text_chars),
            })
        owned.sort(key=lambda item: item.get("occurred_at") or "", reverse=True)
        scan = {"owner": "Memory", "state": STATE_OK if not reason else STATE_PARTIAL,
                "scanned": scanned, "matched": len(owned),
                "window": "newest_verified_outcomes", "limit": self.memory_scan}
        if reason:
            scan["reason_code"] = reason
        block: dict[str, Any] = {
            "state": STATE_OK if owned else "EMPTY",
            "rows": owned[: self.max_results],
            "truncated": len(owned) > self.max_results,
            # Internal association input (not serialised as a second list): every matched
            # experience id inside the scanned window.
            "_all_ids": [item["experience_id"] for item in owned if item["experience_id"]],
        }
        if reason:
            block["reason_code"] = reason
        return block, scan

    # -- methods whose evidence points back at this work ----------------------------------

    def _read_methods(self, target: str, results: Mapping[str, Any]) -> dict[str, Any]:
        owned_ids = {str(item) for item in (results.get("_all_ids") or []) if str(item)}
        if self.practice is None:
            return {"state": STATE_UNAVAILABLE, "reason_code": REASON_PRACTICE_UNWIRED,
                    "rows": []}
        if not callable(getattr(self.practice, "methods", None)) or \
                not callable(getattr(self.practice, "read_method", None)):
            return {"state": STATE_UNAVAILABLE, "reason_code": "WORK_CONTEXT_PRACTICE_UNREADABLE",
                    "rows": []}
        if not owned_ids:
            # Without a single owner-confirmed result for this work there is no evidence that a
            # method belongs to it; listing the global method list here would be an invented
            # association.  The block says exactly that.
            return {"state": "NO_EVIDENCE", "rows": [],
                    "reason_code": "WORK_CONTEXT_NO_WORK_RESULT_EVIDENCE",
                    "note": "这件作品还没有可读的真实结果；不把全局做法认领成属于它。"}
        try:
            listed = self.practice.methods(query="", limit=max(self.max_methods * 3,
                                                               self.method_scan),
                                           available_only=False)
        except Exception as exc:
            return {"state": STATE_UNAVAILABLE, "reason_code": "WORK_CONTEXT_METHODS_READ_FAILED",
                    "detail": "%s: %s" % (type(exc).__name__, str(exc)[:120]), "rows": []}

        rows: list[dict[str, Any]] = []
        reads = 0
        withheld: list[dict[str, Any]] = []
        for row in listed or ():
            if len(rows) >= self.max_methods or reads >= self.method_scan:
                break
            if not isinstance(row, Mapping):
                continue
            artifact_id = str(row.get("artifact_id") or "")
            if not artifact_id:
                continue
            reads += 1
            try:
                detail = self.practice.read_method(artifact_id)
            except Exception as exc:
                withheld.append({"method_ref": str(row.get("method_ref") or ""),
                                 "reason_code": "WORK_CONTEXT_METHOD_DETAIL_FAILED",
                                 "detail": type(exc).__name__})
                continue
            if not isinstance(detail, Mapping):
                withheld.append({"method_ref": str(row.get("method_ref") or ""),
                                 "reason_code": "WORK_CONTEXT_METHOD_DETAIL_UNAVAILABLE"})
                continue
            if detail.get("governance_blocked") or detail.get("content_available") is False:
                # The owner withheld the document and everything inferred from it.  Evidence
                # cannot be re-derived from the list row.
                withheld.append({"method_ref": str(row.get("method_ref") or ""),
                                 "reason_code": "WORK_CONTEXT_METHOD_EVIDENCE_WITHHELD"})
                continue
            basis = {str(x) for x in (detail.get("experience_ids") or []) if str(x)}
            trials = [dict(item) for item in (detail.get("trials") or [])
                      if isinstance(item, Mapping)]
            for trial in trials:
                basis |= {str(x) for x in (trial.get("experience_ids") or []) if str(x)}
            hits = sorted(basis & owned_ids)
            if not hits:
                continue
            latest: Optional[dict[str, Any]] = None
            for trial in reversed(trials):
                outcome = str(trial.get("outcome") or "").strip().upper()
                if not outcome:
                    continue
                evidence = trial.get("experience_ids")
                latest = {
                    "outcome": outcome,
                    # This is the *method's own* version the verdict was about -- never the work
                    # version and never a result identity.
                    "evaluated_method_version_id": str(trial.get("version_id") or "") or None,
                    "evidence_count": (len([x for x in evidence if str(x or "").strip()])
                                       if isinstance(evidence, (list, tuple)) else None),
                }
                break
            rows.append({
                "method_ref": str(row.get("method_ref") or ""),
                "artifact_id": artifact_id,
                "title": _line(row.get("title"), 120),
                "instruction": _line(row.get("instruction"), 240),
                "scope": _line(row.get("scope"), 160),
                "uncertainty": _line(row.get("uncertainty"), 160),
                "method_state": row.get("method_state"),
                "available": bool(row.get("available", True)),
                "head_version_id": str(detail.get("head_version_id") or "") or None,
                "trial_count": _int_or_none(row.get("trial_count")),
                "counterexample_count": _int_or_none(row.get("counterexample_count")),
                "latest_evaluation": latest,
                # The evidence that actually ties it to *this* work (bounded).
                "work_result_experience_ids": hits[:3],
                "not_memory": True,
                "verified_skill": False,
            })
        out: dict[str, Any] = {
            "state": STATE_OK if rows else "NO_EVIDENCE",
            "rows": rows,
            "reads": reads,
            "note": ("只列证据指回这件作品结果的做法；trial 的版本是方法自己的版本，"
                     "不是作品版本，也不代表本轮重新确证。"),
        }
        if not rows:
            out["reason_code"] = "WORK_CONTEXT_NO_METHOD_EVIDENCE_FOR_WORK"
        if withheld:
            out["withheld"] = withheld[: self.max_methods]
        return out

    # -- local drafts whose source is this work -------------------------------------------

    def _read_drafts(self, target: str) -> dict[str, Any]:
        if self.sharing is None:
            return {"state": STATE_UNAVAILABLE, "reason_code": REASON_SHARING_UNWIRED, "rows": []}
        lister = getattr(self.sharing, "list_shares", None)
        if callable(lister):
            try:
                answer = lister(include_text=False, limit=max(1, self.max_drafts * 4))
            except TypeError:
                answer = lister(limit=max(1, self.max_drafts * 4))
            except Exception as exc:
                return {"state": STATE_UNAVAILABLE,
                        "reason_code": "WORK_CONTEXT_DRAFTS_READ_FAILED",
                        "detail": "%s: %s" % (type(exc).__name__, str(exc)[:120]), "rows": []}
        else:
            lister = getattr(self.sharing, "listing", None)
            if not callable(lister):
                return {"state": STATE_UNAVAILABLE,
                        "reason_code": "WORK_CONTEXT_SHARING_UNREADABLE", "rows": []}
            try:
                answer = lister(limit=max(1, self.max_drafts * 4))
            except Exception as exc:
                return {"state": STATE_UNAVAILABLE,
                        "reason_code": "WORK_CONTEXT_DRAFTS_READ_FAILED",
                        "detail": "%s: %s" % (type(exc).__name__, str(exc)[:120]), "rows": []}
        data, reason = _answer_data(answer)
        shares = data.get("shares") if isinstance(data.get("shares"), list) else []
        rows: list[dict[str, Any]] = []
        for row in shares:
            if not isinstance(row, Mapping):
                continue
            source = row.get("source") if isinstance(row.get("source"), Mapping) else {}
            if not same_artifact(source.get("artifact_id"), target):
                continue
            rows.append({
                "share_id": str(row.get("share_id") or ""),
                "state": str(row.get("state") or ""),
                "title": _line(row.get("title"), 120),
                "source_version_id": str(source.get("version_id") or "") or None,
                "source_state": str(row.get("source_state") or "") or None,
                "source_reason_code": str(row.get("source_reason_code") or "") or None,
                "has_receipt": bool(row.get("has_receipt")),
                "delivered": bool(row.get("delivered")),
                "updated_at": str(row.get("updated_at") or "")[:40],
            })
            if len(rows) >= self.max_drafts:
                break
        out: dict[str, Any] = {"state": STATE_OK if not reason else STATE_PARTIAL,
                               "rows": rows,
                               "note": "只列来源就是这件作品的本地草稿；列表不带正文，也不发送。"}
        if reason:
            out["reason_code"] = reason
        return out

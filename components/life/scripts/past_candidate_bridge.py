"""把 owner 自己记录的 target refs 投成「这一轮该继续哪件作品」——只读、窄投影。

为什么需要这一层
----------------
LR-2 的 canonical Activity 与 AG-0 的 ``CONTINUE_CURRENT`` 候选都带着 owner 自己写下的
``subject_refs`` / ``target_ref`` / ``checkpoint_ref``（见
``candidate_sources_ag0.CurrentActivityCandidateSource.materialize``）。但正式 work 步骤只从
候选的 ``source_ref`` 取对象，而那是 **activity id**，不是作品：于是步骤退回本轮绑定的/历史
选中的 resume point。当前台 Activity 指向另一件作品时，CONTINUE 会被写到另一件作品上。

本模块只回答一个问题：**这条 owner 记录点名的作品 ref 是哪一个**。它不读 owner、不造 ref、
不判断可执行性，也不产生候选；可执行性仍由 ``LifeArtifactPort.resume_point`` 与 AR-0 准入
决定。没有作品 ref 时返回 ``None``，调用方保留原来的 fallback 行为。
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional, Sequence

WORK_PREFIX = "artifact:"
VERSION_PREFIX = "artifactversion:"


def _bare(ref: Any) -> str:
    text = str(ref or "").strip()
    while text.startswith(WORK_PREFIX):
        text = text[len(WORK_PREFIX):]
    return text


def _is_work_ref(ref: Any) -> bool:
    text = str(ref or "").strip()
    # A version names a revision, not the artifact; closeout:/place:/world_fact: and every other
    # namespace is another owner's object, not the work this record continues.
    return text.startswith(WORK_PREFIX) and not text.startswith(VERSION_PREFIX)


def work_ref_from_refs(
    refs: Optional[Iterable[Any]], *, exclusions: Iterable[str] = ()
) -> Optional[str]:
    """The first ``artifact:`` ref in ``refs`` that is not a version or an excluded artifact.

    ``exclusions`` are *bare* ids (``LifeArtifactPort`` spelling after stripping ``artifact:``),
    so the reading material / private-items document can never become a work target here.
    """

    blocked = {_bare(item) for item in (exclusions or ()) if str(item or "").strip()}
    for ref in refs or ():
        text = str(ref or "").strip()
        if not _is_work_ref(text):
            continue
        bare = _bare(text)
        if not bare or bare in blocked:
            continue
        return text
    return None


def work_ref_from_candidate(
    candidate: Optional[Mapping[str, Any]], *, exclusions: Iterable[str] = ()
) -> Optional[str]:
    """The work ref a committed candidate's own owner target refs name, or ``None``."""

    if not isinstance(candidate, Mapping):
        return None
    return work_ref_from_refs(candidate.get("target_refs") or (), exclusions=exclusions)


def work_ref_from_activity(
    record: Optional[Mapping[str, Any]], *, exclusions: Iterable[str] = ()
) -> Optional[str]:
    """The work ref a canonical Activity record names, or ``None``.

    The order is the owner's own adoption order: ``subject_refs`` first (what AG-2 wrote when the
    Activity was created), then ``target_ref``, then ``checkpoint_ref``.  A record with no work
    ref (a World/stay/reading Activity) answers ``None`` and the caller keeps its fallback.
    """

    if not isinstance(record, Mapping):
        return None
    refs: list[Any] = []
    for key in ("subject_refs", "target_ref", "checkpoint_ref"):
        value = record.get(key)
        if isinstance(value, (list, tuple)):
            refs.extend(value)
        elif value:
            refs.append(value)
    return work_ref_from_refs(refs, exclusions=exclusions)


__all__ = [
    "VERSION_PREFIX",
    "WORK_PREFIX",
    "work_ref_from_activity",
    "work_ref_from_candidate",
    "work_ref_from_refs",
]

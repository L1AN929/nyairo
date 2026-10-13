#!/usr/bin/env python3
"""B7/E11: the read-only life projection the chat turn may use.

The problem this solves
-----------------------
Without it, an instance can really have written a version of her own work and then
have no idea in chat, or -- worse -- can talk about a life she did not have.  Both
are lies; the second is the one this module exists to make impossible.

What it is
----------
A **projection of current facts**, not a new fact store and not a second persona
prompt.  Every field is read through a port that already owns the fact:

======================  =============================================
field                   owner / port
======================  =============================================
switch, last wake       the wake runner's own journal
current Activity        ``runtime.canonical_state()`` (LR-2)
confirmed work version  ``LifeArtifactPort.read_head`` (Supply, grant-scoped)
recent confirmed action ``runtime.action_read.list_actions()`` (AR-0)
unresolved result       the same AR-0 read, ``status == UNKNOWN``
disclosable experiences ``LifeExperiencePort.prompt_view()`` (B5, governed)
======================  =============================================

Privacy (E11.3)
---------------
The work is read through the **grant-scoped** port, so a work the subject may not
disclose simply is not there.  Experiences arrive already filtered by the memory
governance port.  Nothing is read straight off disk, and no full text of a private
work is ever put in the block -- only references, counts and states.

Silence
-------
With nothing to say (no activity, no action, no experience) ``render()`` returns the
empty string, and the chat turn is byte-for-byte what it was before.  There is no
"today's life report" opener anywhere in here: the user's own message stays the
subject of the conversation.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

#: Hard bound on the rendered block.  A chat turn must not grow because she has a life.
DEFAULT_MAX_CHARS = 700
DEFAULT_MAX_EXPERIENCES = 2
DEFAULT_MAX_ACTIONS = 3


def _now() -> datetime:
    return datetime.now(timezone.utc)


class LifeProjection:
    """A bounded, read-only view of the current life facts for one chat turn."""

    def __init__(
        self,
        *,
        runtime: Any = None,
        runner: Any = None,
        artifact_port: Any = None,
        experience_port: Any = None,
        subject_id: Optional[str] = None,
        max_chars: int = DEFAULT_MAX_CHARS,
        max_experiences: int = DEFAULT_MAX_EXPERIENCES,
        max_actions: int = DEFAULT_MAX_ACTIONS,
    ) -> None:
        self.runtime = runtime
        self.runner = runner
        self.artifact_port = artifact_port
        self.experience_port = experience_port
        self.subject_id = subject_id
        self.max_chars = max(0, int(max_chars))
        self.max_experiences = max(0, int(max_experiences))
        self.max_actions = max(0, int(max_actions))
        self.last_error: Optional[str] = None
        #: Set when the projection itself could not read a fact.  It never guesses.
        self.degraded: list[str] = []

    # -- facts ------------------------------------------------------------------

    def _activity(self) -> Optional[dict[str, Any]]:
        if self.runtime is None:
            return None
        try:
            state = self.runtime.canonical_state()
        except Exception as exc:  # a read failure is reported, never invented
            self.degraded.append("activity:%s" % type(exc).__name__)
            return None
        ref = state.get("foreground_activity_ref")
        activities = state.get("activities") or {}
        if ref and isinstance(activities, dict):
            found = activities.get(str(ref))
            if isinstance(found, Mapping):
                return dict(found)
        if isinstance(activities, Mapping) and len(activities) == 1:
            only = next(iter(activities.values()))
            return dict(only) if isinstance(only, Mapping) else None
        return None

    def _work(self) -> Optional[dict[str, Any]]:
        """The current work, read through the grant-scoped port.  Absent means absent."""

        if self.artifact_port is None:
            return None
        try:
            point = self.artifact_port.latest_resumable()
        except Exception as exc:
            self.degraded.append("work:%s" % type(exc).__name__)
            return None
        if point is None:
            return None
        try:
            return point.to_dict()
        except AttributeError:
            return dict(point) if isinstance(point, Mapping) else None

    def _actions(self) -> list[dict[str, Any]]:
        if self.runtime is None:
            return []
        try:
            rows = list(self.runtime.action_read.list_actions())
        except Exception as exc:
            self.degraded.append("actions:%s" % type(exc).__name__)
            return []
        rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
        return rows

    def _experiences(self) -> list[dict[str, Any]]:
        """Already governed: the port returns only what may be disclosed."""

        if self.experience_port is None:
            return []
        try:
            view = self.experience_port.prompt_view()
        except Exception as exc:
            self.degraded.append("experiences:%s" % type(exc).__name__)
            return []
        return [dict(item) for item in (view or []) if isinstance(item, Mapping)]

    def _switch(self) -> Optional[bool]:
        if self.runner is not None:
            return bool(self.runner.enabled)
        return None

    # -- the snapshot -----------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Every field the projection may disclose, as facts -- not prose."""

        actions = self._actions()
        confirmed = [row for row in actions
                     if str(row.get("status")) in {"SUCCEEDED", "ACKNOWLEDGED"}]
        unresolved = [row for row in actions if str(row.get("status")) == "UNKNOWN"]
        work = self._work()
        return {
            "autonomous_enabled": self._switch(),
            "activity": self._activity(),
            "work": work,
            "recent_actions": actions[: self.max_actions],
            "last_confirmed_action": confirmed[0] if confirmed else None,
            "unresolved_actions": unresolved,
            "experiences": self._experiences()[: self.max_experiences],
            "degraded": list(self.degraded),
        }

    # -- the chat block ---------------------------------------------------------

    def render(self) -> str:
        """A short, factual block for the chat context -- or '' when there is nothing.

        Deliberately terse and reference-shaped.  It tells her what is true right now
        so she can speak from it if the conversation goes there; it never instructs her
        to bring it up.
        """

        if self.max_chars <= 0:
            return ""
        try:
            facts = self.snapshot()
        except Exception as exc:  # never break a conversation over a projection
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
            return ""

        parts: list[str] = []
        if facts["autonomous_enabled"] is not None:
            parts.append("自主生活=%s" % ("启用" if facts["autonomous_enabled"] else "关闭"))

        activity = facts["activity"]
        if activity:
            kind = str(activity.get("activity_kind") or activity.get("kind") or "活动")
            status = str(activity.get("status") or "")
            title = str(activity.get("title") or "").strip()
            label = "%s%s" % (kind, ("《%s》" % title) if title else "")
            parts.append("当前活动=%s(%s)" % (label, status))

        work = facts["work"]
        if work:
            parts.append("当前作品=%s 最新已确认版本=%s" % (
                str(work.get("artifact_id") or "?"),
                str(work.get("head_version_id") or work.get("version_id") or "无")))

        last = facts["last_confirmed_action"]
        if last:
            parts.append("最近一次已确认动作=%s(%s)" % (
                str(last.get("action_kind") or "?"), str(last.get("status") or "")))

        unresolved = facts["unresolved_actions"]
        if unresolved:
            # Requirement: an unresolved result must never be spoken as a finished one.
            parts.append("未决结果=%d（结果未确认，不得声称已完成）" % len(unresolved))

        for experience in facts["experiences"]:
            text = str(experience.get("text") or "").strip()
            if text:
                parts.append("可提及的过往经历：%s" % text)

        if not parts:
            return ""
        block = " | ".join(parts)
        if len(block) > self.max_chars:
            block = block[: self.max_chars] + " …"
        return block

    # -- diagnostics ------------------------------------------------------------

    def diagnostics(self) -> dict[str, Any]:
        """The E11.4 view: what the chat could know, and what it could not read."""

        facts = self.snapshot()
        return {
            "autonomous_enabled": facts["autonomous_enabled"],
            "activity_kind": (facts["activity"] or {}).get("activity_kind"),
            "activity_status": (facts["activity"] or {}).get("status"),
            "activity_ref": (facts["activity"] or {}).get("activity_id"),
            "work_ref": (facts["work"] or {}).get("artifact_id"),
            "work_confirmed_version": (facts["work"] or {}).get("head_version_id"),
            "recent_action_refs": [row.get("action_id") for row in facts["recent_actions"]],
            "unresolved_action_refs": [row.get("action_id") for row in facts["unresolved_actions"]],
            "experience_ids": [item.get("experience_id") for item in facts["experiences"]],
            "degraded": facts["degraded"],
            "rendered_chars": len(self.render()),
            "last_error": self.last_error,
        }


def life_projection_from_paths(
    *, runtime: Any = None, runner: Any = None, supply_socket: Optional[str] = None,
    subject_id: Optional[str] = None, evidence_db: Optional[str] = None,
    controls_db: Optional[str] = None, **kwargs: Any
) -> LifeProjection:
    """Assemble the projection from the same paths the instance already uses.

    Each optional port is opened only when its path exists, so an instance without a
    Supply owner or without memory simply has fewer facts -- it never gets a guess.
    """

    artifact_port = None
    if supply_socket and subject_id:
        try:
            import sys

            here = Path(__file__).resolve().parent
            if str(here) not in sys.path:
                sys.path.insert(0, str(here))
            from life_artifact_port import LifeArtifactPort

            artifact_port = LifeArtifactPort(supply_socket, subject_id)
        except Exception:
            artifact_port = None

    experience_port = None
    if evidence_db and controls_db and subject_id:
        try:
            import sys

            native = Path(__file__).resolve().parents[2] / "native"
            for path in (str(native), str(native / "src"), str(native / "memory_runtime_v1")):
                if path not in sys.path:
                    sys.path.insert(0, path)
            from life_experience_port import LifeExperiencePort

            experience_port = LifeExperiencePort(
                evidence_db, controls_db=controls_db, subject=subject_id)
        except Exception:
            experience_port = None

    return LifeProjection(runtime=runtime, runner=runner, artifact_port=artifact_port,
                          experience_port=experience_port, subject_id=subject_id, **kwargs)


__all__ = ["DEFAULT_MAX_CHARS", "LifeProjection", "life_projection_from_paths"]

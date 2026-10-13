#!/usr/bin/env python3
"""B7/E09.5: the minimal local control surface for autonomous life.

Five verbs, and nothing else: ``enable``, ``disable``, ``status``, ``run-once``,
``list-recent-actions``.  No web platform, no mobile front end, no second
management service.

Four of them touch only what the runner and the cycle already persist, so they work
on a *stopped* instance and need no model key.  ``run-once`` is the development
entry: it performs exactly one real consideration window, through the same runner an
event or a timer would use.  It is explicitly **not** the user commanding her -- it
is the operator asking "would a wake do anything right now?", and the answer it
returns is the runner's own honest verdict, including "nothing to consider".
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

SWITCH_FILENAME = "autonomous_switch.json"
BUDGET_FILENAME = "autonomous_budget.json"
WAKE_LOG_FILENAME = "autonomous_wakes.jsonl"
STEP_JOURNAL_FILENAME = "life_step_journal.jsonl"

#: The Life state root lives one level under the instance's Life install root.
LIFE_STATE_DIRNAME = "state"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _tail_jsonl(path: Path, limit: int) -> list[dict[str, Any]]:
    if limit <= 0 or not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows[-int(limit):]


def life_state_root(install_root: Path | str) -> Path:
    """Where the cycle and the runner keep their own files."""

    return Path(install_root) / LIFE_STATE_DIRNAME


def set_enabled(install_root: Path | str, enabled: bool, *, actor: str = "operator") -> dict[str, Any]:
    """The global switch.  Persisted, so it survives a restart.  Deletes nothing."""

    from autonomous_life_runner import AutonomousSwitch

    switch = AutonomousSwitch(life_state_root(install_root))
    switch.set(bool(enabled), actor=actor)
    return switch.view()


def read_status(install_root: Path | str) -> dict[str, Any]:
    """Everything E11.4 asks a diagnostic to be able to see, read-only."""

    state_root = life_state_root(install_root)
    switch = _read_json(state_root / SWITCH_FILENAME)
    budget = _read_json(state_root / BUDGET_FILENAME)
    wakes = _tail_jsonl(state_root / WAKE_LOG_FILENAME, 5)
    steps = _tail_jsonl(state_root / STEP_JOURNAL_FILENAME, 3)
    return {
        "install_root": str(Path(install_root)),
        "life_state_root": str(state_root),
        "autonomous_switch": {
            "enabled": switch.get("enabled"),
            "changed_at": switch.get("changed_at"),
            "changed_by": switch.get("changed_by"),
        },
        "budget_today": {
            "day": budget.get("day"),
            "rounds": {"used": budget.get("spent"), "max": budget.get("max_rounds_per_day")},
            "cognition_calls": {"used": budget.get("cognition_used"),
                                "max": budget.get("max_cognition_calls_per_day")},
            "actions": {"used": budget.get("actions_used"),
                        "max": budget.get("max_actions_per_day")},
        },
        "last_wake": wakes[-1] if wakes else None,
        "recent_wakes": wakes,
        "recent_steps": steps,
        "pending_unknown_actions": list_pending_unknown(install_root),
    }


def _action_read(install_root: Path | str):
    """The AR-0 read port over this instance's own store.  Read-only by construction."""

    from action_reality_ledger import ActionReadService, ActionStore
    import life_runtime_production as lrp

    store = ActionStore(life_state_root(install_root), namespace=lrp.NAMESPACE_AGENCY)
    return ActionReadService(store)


def list_recent_actions(install_root: Path | str, limit: int = 20) -> list[dict[str, Any]]:
    """The real AR-0 actions, newest first, including the unresolved ones."""

    try:
        rows = list(_action_read(install_root).list_actions())
    except Exception as exc:
        return [{"error": "%s: %s" % (type(exc).__name__, exc)}]
    rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    keep = ("action_id", "action_kind", "target_ref", "status", "created_at",
            "provider_operation_ref", "idempotency_key")
    return [{k: row.get(k) for k in keep} for row in rows[: max(0, int(limit))]]


def list_pending_unknown(install_root: Path | str) -> list[dict[str, Any]]:
    """Results that were submitted but never confirmed.  Never reported as success."""

    try:
        rows = list(_action_read(install_root).list_actions(status="UNKNOWN"))
    except Exception:
        return []
    return [{"action_id": row.get("action_id"), "action_kind": row.get("action_kind"),
             "status": row.get("status")} for row in rows]


def run_once(
    install_root: Path | str,
    *,
    runner: Any = None,
    runner_factory: Optional[Callable[[], Any]] = None,
    act: bool = True,
) -> dict[str, Any]:
    """One real consideration window, through the real runner.

    A caller supplies the assembled runner (the plugin has one) or a factory that
    builds one.  Without either, this refuses instead of pretending a round happened:
    a control surface that silently does nothing would be the worst possible answer.
    """

    target = runner
    if target is None and runner_factory is not None:
        target = runner_factory()
    if target is None:
        return {"ok": False, "reason": "NO_RUNNER",
                "detail": "run-once needs an assembled life runtime; none was supplied"}
    record = target.run_due(act=act)
    return {"ok": True, "wake": record.to_dict()}


def reconcile_pending(install_root: Path | str, *, runner: Any = None) -> dict[str, Any]:
    """Resolve already-submitted unknown results.  Allowed even when disabled."""

    if runner is None:
        return {"ok": False, "reason": "NO_RUNNER",
                "detail": "reconciliation needs the assembled runtime"}
    return {"ok": True, "results": runner.reconcile_pending()}


__all__ = [
    "LIFE_STATE_DIRNAME",
    "life_state_root",
    "list_pending_unknown",
    "list_recent_actions",
    "read_status",
    "reconcile_pending",
    "run_once",
    "set_enabled",
]

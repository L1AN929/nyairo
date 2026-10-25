#!/usr/bin/env python3
"""Body Consequence Adapter (M9) — the only legal World-result -> Body path.

Chain
-----
    Action Proposal -> World Resolver -> Canonical Result -> Execution Ledger
      -> Body Consequence Adapter -> Body Runtime -> BodyTransition -> BodySignal

The adapter's whole job is:

    Canonical ActionResult + World/Substrate facts  ->  BodyRuntimeInput

The Resolver never learns ``fatigue`` / ``activation`` / ``comfort`` /
``tension``.  It emits facts only; the body interprets its own physical
consequences.

Hard rules
----------
* **SUCCESS only.**  A FAILURE / REJECTED result may not apply success
  consequences.  An UNKNOWN result is held in ``pending_body_consequence`` and
  is never guessed, never applied early, never auto-retried.
* **Exactly once.**  One ``execution_id`` maps to at most one canonical
  consequence outcome, survived across resolver retry, restart, and ledger
  replay.
* **Deterministic.**  Same ActionResult + same World facts + same previous body
  state + same runtime version -> same BodyRuntimeInput.  No LLM, no random, no
  wall clock.
* **No verb knowledge in the body.**  The adapter maps action facts to a load
  class; the runtime decides how that moves the variables.
* **No intake class.**  ``CONSUME`` is a physical operation, not a meal: the
  body has no hunger, nutrition, satiety or intake variable and this adapter
  never invents one.  Eating therefore does not "restore" fatigue or award
  comfort; the only input it may carry is the tiny ``PHYSICAL_RESULT``
  manipulation load of the act itself (see ``CONSUME_LOAD_NOTE``).
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

SCHEMA_CONSEQUENCE = "body.consequence.v1"
KIND_CONSEQUENCE = "body_consequence"
KIND_PENDING = "pending_body_consequence"

#: Laws of the adapter: which action types may produce a body consequence.
#: F: ``CONSUME`` is here only for the small physical manipulation of the
#: operation itself.  It is explicitly *not* an intake/nutrition class and grants
#: no recovery, comfort or satiety: see ``CONSUME_LOAD_NOTE``.
SUPPORTED_ACTIONS = ("MOVE", "POSE", "REST", "PICK_UP", "PLACE", "CONSUME")

#: Movement / posture duration.  The world owner *can* now supply a real
#: duration (a measured walk, or the world topology's own route seconds); the
#: table below is only the fallback used when it cannot, and every fallback is
#: recorded in ``notes``.  This is a load model, NOT an activity label.
MOVEMENT_SECONDS_BY_ACTION = {
    "MOVE": 600.0,
    "POSE": 5.0,
}

#: Where a movement duration came from.  ``assumed_default`` means "nobody could
#: measure this", and it is never presented as a measurement.
MEASUREMENT_MEASURED_TRAVEL = "measured_travel"
MEASUREMENT_MEASURED_TRAVEL_RECOVERED = "measured_travel_recovered"
MEASUREMENT_ROUTE_ESTIMATE = "route_estimate"
MEASUREMENT_ASSUMED = "assumed_default"
MEASUREMENT_SOURCES = (
    MEASUREMENT_MEASURED_TRAVEL,
    MEASUREMENT_MEASURED_TRAVEL_RECOVERED,
    MEASUREMENT_ROUTE_ESTIMATE,
    MEASUREMENT_ASSUMED,
)
#: A load duration is bounded here so a corrupt travel record cannot ask for an
#: unbounded advance.
MAX_MEASUREMENT_SECONDS = 24 * 3600.0

#: A timed departure defers the walk's load until the owner's arrival settlement.
MOVEMENT_DEFERRED_NOTE = "movement_load_deferred:arrival_pending"

#: Effort classification per action type.  The resolver stays ignorant of it.
EFFORT_BY_ACTION = {
    "MOVE": "moderate",
    "POSE": "light",
    "PICK_UP": "light",
    "PLACE": "light",
    "REST": "light",
    "CONSUME": "light",
}

#: Versioned, minimal assumption for a consumption operation: the physical act of
#: bringing one already-obtained portion to the mouth.  Two seconds is the same
#: order as PICK_UP/PLACE (a hand movement), deliberately *not* a made-up
#: "120 s spent eating" and deliberately not a meal duration, because no real
#: duration is measured anywhere in this system yet.
#: This is a manipulation-load model.  It says nothing about nutrition, satiety,
#: hunger, health, energy intake or recovery, and nothing may map it onto one.
CONSUME_OPERATION_SECONDS = 2.0
CONSUME_LOAD_NOTE = (
    "consume_operation_load_model:%.1fs manipulation only; "
    "not nutrition, not satiety, not hunger, not health, not recovery"
    % CONSUME_OPERATION_SECONDS
)

#: Actions that may open a recovery session when they SUCCEED.
RECOVERY_ACTIONS = ("REST",)

#: Actions whose posture consequence is worth recording.
POSTURE_ACTIONS = ("POSE", "MOVE")

#: Posture is integrated over the interval the body owner has *actually*
#: accounted for: ``[body.last_advanced_at, at]``.  The interval is attributed
#: to the World-measured pose in force at its end (the action's ``before.pose``),
#: never to an inferred "pose since" instant.  A missing anchor means no
#: interval: the first settlement after a fresh/old runtime charges nothing and
#: only establishes the cursor.  Every owner advance that moves that cursor
#: (an action settlement, the environment tick, an arrival) must also settle the
#: interval it consumes, or those seconds are silently lost; a measured walk is
#: billed as movement and only its static remainder as posture, so no second is
#: ever billed twice.
POSTURE_INTERVAL_SOURCE = "elapsed_since_last_body_advance"
POSTURE_MAX_SECONDS = MAX_MEASUREMENT_SECONDS

#: An UNKNOWN POSE/MOVE may or may not have changed the pose at its own instant,
#: so the interval that ends at a later settlement cannot be attributed to any
#: one pose.  The window is closed with an input-free cursor advance and the
#: pending record carries this note; a later reconciliation charges no posture
#: for the same window instead of inventing one.
POSTURE_INTERVAL_UNKNOWN_NOTE = "posture_interval_unknown:no_attribution"

STATUS_SUCCESS = frozenset({"applied", "already_applied"})
STATUS_FAILURE = frozenset({"rejected", "failed", "disabled", "noop"})
STATUS_UNKNOWN = frozenset({"prepared", "uncertain"})

CONSEQUENCE_STATE_PENDING = "pending"
CONSEQUENCE_STATE_APPLIED = "applied"
CONSEQUENCE_STATE_SKIPPED = "skipped"

MAX_RECORDS = 4096


class BodyConsequenceError(ValueError):
    """The consequence contract was violated."""


def _text(value: Any, label: str, *, maximum: int = 256) -> str:
    if not isinstance(value, str):
        raise BodyConsequenceError(f"{label} must be a string")
    text = value.strip()
    if not text or len(text) > maximum:
        raise BodyConsequenceError(f"{label} must be non-empty and bounded")
    return text


def _detach(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False))


def _digest(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def classify_status(result: Mapping[str, Any]) -> str:
    """Map a canonical ActionResult onto SUCCESS / FAILURE / UNKNOWN."""

    status = str(result.get("status") or "")
    if status in STATUS_SUCCESS:
        return "SUCCESS"
    if status in STATUS_UNKNOWN:
        return "UNKNOWN"
    if status in STATUS_FAILURE:
        return "FAILURE"
    # an unrecognised status is never treated as success
    return "UNKNOWN"


def consequence_id(execution_id: str, action_type: str, body_id: str) -> str:
    """Deterministic identity.  Same execution -> same id, across restarts."""

    identity = {
        "execution_id": _text(execution_id, "execution_id"),
        "action_type": _text(action_type, "action_type", maximum=64),
        "body_id": _text(body_id, "body_id"),
    }
    return "body-consequence:" + _digest(identity)[:32]


def _movement_measurement(
    action: str, world_facts: Optional[Mapping[str, Any]]
) -> tuple[float, str]:
    """``(seconds, source)`` for a movement action.  Never invents a measurement.

    The caller (the World/Body service) may pass what it actually knows:

    * ``measured_travel`` -- the owner's own completed walk, in seconds;
    * ``measured_travel_recovered`` -- the same, settled after a crash window;
    * ``route_estimate`` -- the world topology's declared route duration.

    Anything else (missing, out of bounds, not a number) falls back to the
    per-action constant and says ``assumed_default`` out loud.
    """

    facts = world_facts or {}
    source = facts.get("movement_source")
    seconds: Optional[float] = None
    raw = facts.get("movement_seconds")
    if raw is not None and not isinstance(raw, bool):
        try:
            candidate = float(raw)
        except (TypeError, ValueError):
            candidate = None
        if (
            candidate is not None
            and candidate > 0.0
            and candidate == candidate  # NaN guard
            and candidate != float("inf")
        ):
            seconds = min(candidate, MAX_MEASUREMENT_SECONDS)
        else:
            source = None
    if seconds is None or source not in MEASUREMENT_SOURCES:
        return MOVEMENT_SECONDS_BY_ACTION[action], MEASUREMENT_ASSUMED
    return round(seconds, 3), str(source)


def _posture_interval(elapsed_seconds: Optional[float]) -> float:
    """Bound the accounted interval.  No number -> no posture load."""

    if elapsed_seconds is None or isinstance(elapsed_seconds, bool):
        return 0.0
    try:
        value = float(elapsed_seconds)
    except (TypeError, ValueError):
        return 0.0
    if not (0.0 < value < float("inf")) or value != value:  # NaN guard
        return 0.0
    return round(min(value, POSTURE_MAX_SECONDS), 3)


def _elapsed_since(anchor: Any, at: Any) -> Optional[float]:
    """Seconds between the body's persisted cursor and this settlement.

    ``None`` when either side is missing or unreadable: an unknown anchor is
    never backfilled.  A cursor in the future yields ``None`` (no negative load).
    """
    if anchor is None or at is None:
        return None

    def _instant(value: Any) -> Optional[datetime]:
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        try:
            text = str(value).strip().replace("Z", "+00:00")
            parsed = datetime.fromisoformat(text)
        except (TypeError, ValueError):
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    start = _instant(anchor)
    end = _instant(at)
    if start is None or end is None:
        return None
    seconds = (end - start).total_seconds()
    return seconds if seconds > 0.0 else 0.0


def _pose_in_force(result: Mapping[str, Any], pose: Optional[str]) -> Optional[str]:
    """The pose the body was in for the interval that ends now.

    ``before`` is the World's own measured model *before* this action, so for a
    POSE change it is the pose that actually held during the elapsed interval.
    ``after``/``pose`` are only fallbacks for callers that provide no before
    snapshot; they are never used to backfill history.
    """

    before = result.get("before")
    if isinstance(before, Mapping):
        candidate = before.get("pose")
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip().lower()
    if isinstance(pose, str) and pose.strip():
        return pose.strip().lower()
    after = result.get("after")
    if isinstance(after, Mapping):
        candidate = after.get("pose")
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip().lower()
    return None

# --------------------------------------------------------------------------
# the mapping  (Resolver facts -> BodyRuntimeInput)
# --------------------------------------------------------------------------


def build_consequence(
    result: Mapping[str, Any],
    *,
    body_id: str,
    world_facts: Optional[Mapping[str, Any]] = None,
    pose: Optional[str] = None,
    object_id: Optional[str] = None,
    applied_at: Optional[str] = None,
    elapsed_seconds: Optional[float] = None,
) -> dict[str, Any]:
    """Derive the body input from a canonical result.  Facts in, facts out.

    ``elapsed_seconds`` is the body owner's accounted interval ending at this
    settlement (see ``POSTURE_INTERVAL_SOURCE``).  It replaces the old fixed
    ``posture_seconds=60`` assumption; ``None`` means "no trustworthy anchor",
    which charges no posture rather than inventing one.
    """

    execution_id = _text(result.get("execution_id"), "execution_id")
    action_type = result.get("action") or result.get("action_type")
    action = _text(action_type, "action_type", maximum=64)
    verdict = classify_status(result)

    record: dict[str, Any] = {
        "kind": KIND_CONSEQUENCE,
        "schema_version": SCHEMA_CONSEQUENCE,
        "consequence_id": consequence_id(execution_id, action, body_id),
        "execution_id": execution_id,
        "body_id": _text(body_id, "body_id"),
        "action_type": action,
        "result_ref": _digest(_detach(dict(result))),
        "result_status": str(result.get("status") or ""),
        "verdict": verdict,
        "world_revision": (world_facts or {}).get("world_revision"),
        "world_ref": (world_facts or {}).get("world_id"),
        "applied_at": applied_at,
        "body_transition_ref": None,
        "state": CONSEQUENCE_STATE_PENDING,
        "inputs": [],
        "notes": [],
        "raw_body_values": False,
    }

    if verdict != "SUCCESS":
        # FAILURE applies nothing.  UNKNOWN waits for reconciliation.
        record["state"] = (
            CONSEQUENCE_STATE_SKIPPED if verdict == "FAILURE" else CONSEQUENCE_STATE_PENDING
        )
        record["notes"].append(
            "failure_applies_no_consequence" if verdict == "FAILURE" else "awaiting_reconciliation"
        )
        return record

    if action not in SUPPORTED_ACTIONS:
        record["state"] = CONSEQUENCE_STATE_SKIPPED
        record["notes"].append(f"unsupported_action:{action}")
        return record

    inputs: list[dict[str, Any]] = []
    interval = _posture_interval(elapsed_seconds)
    posture_pose = _pose_in_force(result, pose)
    if posture_pose is None:
        # no measured pose -> no attribution; a default strain would be invented
        interval = 0.0

    if action in MOVEMENT_SECONDS_BY_ACTION:
        seconds, measurement_source = _movement_measurement(action, world_facts)
        item: dict[str, Any] = {
            "class": "PHYSICAL_RESULT",
            "movement_duration_seconds": seconds,
            "effort": EFFORT_BY_ACTION[action],
            "execution_id": execution_id,
        }
        if pose:
            item["pose"] = pose
        inputs.append(item)
        # The provenance of the duration travels with the consequence: an
        # assumed 600 s walk and a measured 150 s walk must never look alike.
        record["movement_measurement"] = {
            "source": measurement_source,
            "duration_seconds": seconds,
        }
        if measurement_source == MEASUREMENT_ASSUMED:
            record["notes"].append(
                "movement_load_assumed:%ss:route_duration_unavailable" % seconds
            )
        else:
            record["notes"].append(
                "movement_load_measured:%ss:%s" % (seconds, measurement_source)
            )
        if action == "MOVE" and interval > 0.0:
            # The measured walk is billed as movement; only the static remainder
            # of the accounted interval (time spent in the old pose *before*
            # departure) is billed as posture.  The walk seconds are never charged
            # twice, and pre-departure stillness is never dropped.
            static = round(max(0.0, interval - seconds), 3)
            if static > 0.0 and posture_pose:
                inputs.append(
                    {
                        "class": "PHYSICAL_RESULT",
                        "movement_duration_seconds": 0.0,
                        "effort": EFFORT_BY_ACTION[action],
                        "posture_seconds": static,
                        "pose": posture_pose,
                        "execution_id": execution_id,
                    }
                )
                record["notes"].append(
                    "posture_before_departure:%.3fs:pose=%s" % (static, posture_pose)
                )
            elif interval > 0.0:
                record["notes"].append(
                    "posture_covered_by_movement:%.3fs" % interval
                )

    elif action in ("PICK_UP", "PLACE", "CONSUME"):
        # a small manipulation: resource context, deliberately tiny load so the
        # runtime can decide whether it is even enough to form a transition.
        # For CONSUME this is the physical act of the operation -- the same order
        # of magnitude as picking one portion up -- never a meal duration and
        # never an intake/nutrition input.
        seconds = CONSUME_OPERATION_SECONDS if action == "CONSUME" else 2.0
        item = {
            "class": "PHYSICAL_RESULT",
            "movement_duration_seconds": seconds,
            "effort": EFFORT_BY_ACTION[action],
            "execution_id": execution_id,
            # the accounted interval since the last settlement, in the pose that
            # actually held; continuous posture integration, not a per-action 60s
            "posture_seconds": interval,
        }
        if posture_pose:
            item["pose"] = posture_pose
        inputs.append(item)
        record["resource_ref"] = object_id or result.get("object_id")
        record["notes"].append(
            CONSUME_LOAD_NOTE if action == "CONSUME" else "manipulation_load_minimal"
        )
        if interval > 0.0:
            record["notes"].append(
                "posture_elapsed:%.3fs:pose=%s" % (interval, posture_pose or "unknown")
            )

    if action == "POSE":
        # The interval that just ended was spent in the *old* pose; the new pose
        # begins at this settlement and is billed by the next one.  A zero
        # interval is still recorded (zero load) so the cursor is established.
        item = {
            "class": "PHYSICAL_RESULT",
            "movement_duration_seconds": 0.0,
            "effort": EFFORT_BY_ACTION[action],
            "posture_seconds": interval,
            "execution_id": execution_id,
        }
        if posture_pose:
            item["pose"] = posture_pose
        inputs.append(item)
        record["notes"].append(
            "posture_elapsed:%.3fs:pose=%s" % (interval, posture_pose or "unknown")
            if interval > 0.0
            else "posture_elapsed_zero:first_or_same_instant"
        )

    if action in RECOVERY_ACTIONS:
        # REST is a *command*: start opens one recovery session, stop closes it.
        # Both travel as the one legal RECOVERY input class; the runtime still
        # owns the state machine (threshold, interruption, completion).
        mode = str(result.get("recovery_mode") or "rest")
        state = str(result.get("recovery_state") or "start")
        inputs.append({"class": "RECOVERY", "mode": mode, "state": state})

    if not inputs:
        record["state"] = CONSEQUENCE_STATE_SKIPPED
        record["notes"].append("no_body_input_for_action")
        return record

    latest = result.get("after")
    if isinstance(latest, Mapping) and isinstance(latest.get("pose"), str):
        for item in inputs:
            if item["class"] == "PHYSICAL_RESULT" and "pose" not in item:
                item["pose"] = latest["pose"]

    record["inputs"] = inputs
    record["input_summary"] = {
        "count": len(inputs),
        "classes": sorted({item["class"] for item in inputs}),
    }
    return record


def validate_consequence(record: Any) -> dict[str, Any]:
    if not isinstance(record, Mapping):
        raise BodyConsequenceError("consequence must be an object")
    if record.get("kind") != KIND_CONSEQUENCE:
        raise BodyConsequenceError("consequence kind is not supported")
    if record.get("schema_version") != SCHEMA_CONSEQUENCE:
        raise BodyConsequenceError("consequence schema is not supported")
    if record.get("state") not in (
        CONSEQUENCE_STATE_PENDING,
        CONSEQUENCE_STATE_APPLIED,
        CONSEQUENCE_STATE_SKIPPED,
    ):
        raise BodyConsequenceError("consequence state is not supported")
    return _detach(dict(record))


# --------------------------------------------------------------------------
# exactly-once journal + pending queue
# --------------------------------------------------------------------------


class ConsequenceLedger:
    """Append-only consequence records with deterministic replay to a state map.

    Recovery is by *reading history*, not by trusting a mutable pointer, so a
    crash between "body applied" and "reference finalised" cannot double-apply.
    """

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.lock_path = self.path.with_name(self.path.name + ".lock")

    @contextmanager
    def locked(self) -> Any:
        if fcntl is None:
            raise BodyConsequenceError("consequence ledger lock unavailable")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield self.read_all()
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        for number, line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(), 1
        ):
            if not line.strip():
                continue
            try:
                records.append(validate_consequence(json.loads(line)))
            except (TypeError, ValueError) as exc:
                raise BodyConsequenceError(
                    f"consequence ledger malformed at line {number}"
                ) from exc
        return records

    def append_unlocked(self, record: Mapping[str, Any]) -> dict[str, Any]:
        normalized = validate_consequence(record)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(normalized, ensure_ascii=False, sort_keys=True) + "\n"
                )
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise BodyConsequenceError("consequence ledger cannot be appended") from exc
        return normalized

    def append(self, record: Mapping[str, Any]) -> dict[str, Any]:
        with self.locked():
            return self.append_unlocked(record)

    # -- deterministic views --------------------------------------------

    @staticmethod
    def current_state(records: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
        """Latest record per consequence_id, in first-seen order."""

        ordered: dict[str, dict[str, Any]] = {}
        for record in records:
            ordered[record["consequence_id"]] = dict(record)
        return ordered

    def state_of(self, cid: str) -> Optional[dict[str, Any]]:
        return self.current_state(self.read_all()).get(cid)

    def applied_execution_ids(self) -> set[str]:
        return {
            record["execution_id"]
            for record in self.current_state(self.read_all()).values()
            if record["state"] == CONSEQUENCE_STATE_APPLIED
        }

    def pending(self) -> list[dict[str, Any]]:
        return [
            record
            for record in self.current_state(self.read_all()).values()
            if record["state"] == CONSEQUENCE_STATE_PENDING
        ]

    def pending_body_consequence(self) -> list[dict[str, Any]]:
        """The ticket's ``pending_body_consequence`` queue (UNKNOWN results)."""

        return [
            record
            for record in self.pending()
            if record["verdict"] == "UNKNOWN"
        ]


# --------------------------------------------------------------------------
# application
# --------------------------------------------------------------------------


def apply_consequence(
    record: Mapping[str, Any],
    runtime_state: Mapping[str, Any],
    *,
    at: Any,
    runtime_version: Optional[str] = None,
) -> dict[str, Any]:
    """Advance the body by this consequence.  Pure; the caller persists."""

    import body_runtime as br

    validated = validate_consequence(record)
    if validated["state"] == CONSEQUENCE_STATE_APPLIED:
        return {
            "applied": False,
            "reason": "already_applied",
            "consequence": validated,
            "state": runtime_state,
            "transition": None,
        }
    if validated["verdict"] != "SUCCESS":
        return {
            "applied": False,
            "reason": f"verdict_{validated['verdict'].lower()}",
            "consequence": validated,
            "state": runtime_state,
            "transition": None,
        }
    if not validated["inputs"]:
        return {
            "applied": False,
            "reason": "no_body_input",
            "consequence": validated,
            "state": runtime_state,
            "transition": None,
        }

    before = br.validate_runtime(runtime_state)
    from_time = before["last_advanced_at"] or at
    result = br.advance(
        before, from_time, at, validated["inputs"], runtime_version or before["runtime_version"]
    )

    transition = None
    if result["advanced"]:
        transition = br.build_transition(
            before,
            result["state"],
            at=at,
            cause_class=br.CAUSE_PHYSICAL,
            cause_classes=result["cause_classes"],
            input_summary={
                **result["inputs_summary"],
                "execution_id": validated["execution_id"],
                "consequence_id": validated["consequence_id"],
                "input_refs": [validated["consequence_id"]],
            },
        )

    updated = dict(validated)
    updated["state"] = CONSEQUENCE_STATE_APPLIED
    updated["applied_at"] = (
        transition["valid_from"] if transition else str(at)
    )
    updated["body_transition_ref"] = transition["transition_id"] if transition else None
    updated["body_version_before"] = before["version"]
    updated["body_version_after"] = result["state"]["version"]
    updated["notes"] = list(validated["notes"]) + (
        [] if result["advanced"] else ["advance_produced_no_change"]
    )

    return {
        "applied": True,
        "reason": "applied",
        "consequence": validate_consequence(updated),
        "state": result["state"],
        "transition": transition,
        "advance": result,
    }


def apply_for_execution(
    result: Mapping[str, Any],
    *,
    body_id: str,
    ledger: ConsequenceLedger,
    runtime_state: Mapping[str, Any],
    at: Any,
    world_facts: Optional[Mapping[str, Any]] = None,
    pose: Optional[str] = None,
    object_id: Optional[str] = None,
    runtime_version: Optional[str] = None,
) -> dict[str, Any]:
    """End-to-end, exactly-once application for one execution.

    Safe against resolver retry, restart and ledger replay: a repeated
    ``execution_id`` returns the already-recorded outcome instead of applying a
    second time.
    """

    import body_runtime as br

    execution_id = _text(result.get("execution_id"), "execution_id")
    action = str(result.get("action") or result.get("action_type") or "")
    cid = consequence_id(execution_id, action, body_id)

    with ledger.locked() as records:
        existing = ledger.current_state(records).get(cid)
        if existing is not None and existing["state"] == CONSEQUENCE_STATE_APPLIED:
            return {
                "applied": False,
                "reason": "duplicate_execution_id",
                "consequence": existing,
                "state": runtime_state,
                "transition": None,
            }

        # A prior UNKNOWN posture-relevant result already closed its own window
        # without attribution; the reconciled record must not re-claim it.
        prior_notes = list((existing or {}).get("notes") or [])
        unattributable = any(
            isinstance(item, str) and item.startswith(POSTURE_INTERVAL_UNKNOWN_NOTE)
            for item in prior_notes
        )
        record = build_consequence(
            result,
            body_id=body_id,
            world_facts=world_facts,
            pose=pose,
            object_id=object_id,
            applied_at=str(at),
            # the same cursor ``apply_consequence`` advances from: one interval,
            # one owner, no second clock
            elapsed_seconds=(
                0.0
                if unattributable
                else _elapsed_since(runtime_state.get("last_advanced_at"), at)
            ),
        )
        if unattributable:
            record["notes"].append(
                "posture_interval_unknown:reconciled_without_attribution"
            )
        if record["state"] == CONSEQUENCE_STATE_SKIPPED:
            ledger.append_unlocked(record)
            return {
                "applied": False,
                "reason": f"skipped:{','.join(record['notes'])}",
                "consequence": record,
                "state": runtime_state,
                "transition": None,
            }
        if record["verdict"] == "UNKNOWN":
            if action in POSTURE_ACTIONS:
                # The pose in force after this instant is not known until the
                # result is reconciled, so the window is closed here: it is
                # never later billed to ``before.pose`` from the end state.
                record["notes"].append(POSTURE_INTERVAL_UNKNOWN_NOTE)
            ledger.append_unlocked(record)
            outcome: dict[str, Any] = {
                "applied": False,
                "reason": "pending_reconciliation",
                "consequence": record,
                "state": runtime_state,
                "transition": None,
            }
            if action in POSTURE_ACTIONS:
                before = br.validate_runtime(runtime_state)
                anchor = before["last_advanced_at"]
                elapsed = _elapsed_since(anchor, at)
                if anchor is None or elapsed is not None:
                    # An unreadable or future cursor makes no claim either way.
                    moved = br.advance(
                        before,
                        anchor or at,
                        at,
                        [],
                        runtime_version or before["runtime_version"],
                    )
                    # Bounded, input-free advance: no load is claimed, only the
                    # boundary is established (the same idiom as an unknown
                    # environment gap).  The state moves even when no somatic
                    # variable changed -- that is the point of a boundary.
                    outcome["state"] = moved["state"]
                    outcome["cursor_advanced"] = True
                    outcome["transition"] = br.build_transition(
                        before,
                        moved["state"],
                        at=at,
                        cause_class=br.CAUSE_TIME_ADVANCE,
                        cause_classes=moved["cause_classes"],
                        input_summary={
                            **moved["inputs_summary"],
                            "execution_id": execution_id,
                            "posture_interval_unknown": True,
                        },
                    )
            return outcome

        outcome = apply_consequence(
            record, runtime_state, at=at, runtime_version=runtime_version
        )
        ledger.append_unlocked(outcome["consequence"])
        return {
            **outcome,
            "reason": "applied" if outcome["applied"] else outcome["reason"],
        }


def defer_for_execution(
    result: Mapping[str, Any],
    *,
    body_id: str,
    ledger: "ConsequenceLedger",
    at: Any,
    world_facts: Optional[Mapping[str, Any]] = None,
    pose: Optional[str] = None,
    note: str = MOVEMENT_DEFERRED_NOTE,
) -> dict[str, Any]:
    """Record that this execution's load is *not* settled yet.

    A timed departure is a real World fact whose physical cost has not happened
    yet: the walk is not over.  Charging the whole route at departure time would
    be a fabricated measurement, so the adapter writes an explicit ``pending``
    record instead -- no inputs, no advance, no runtime write.  The owner's
    arrival settlement later appends the *applied* record for the same
    ``consequence_id`` (exactly once).
    """

    execution_id = _text(result.get("execution_id"), "execution_id")
    action = str(result.get("action") or result.get("action_type") or "")
    cid = consequence_id(execution_id, action, body_id)

    with ledger.locked() as records:
        existing = ledger.current_state(records).get(cid)
        if existing is not None:
            # A replay of the same execution never creates a second record.
            return {
                "deferred": False,
                "reason": "already_recorded",
                "consequence": existing,
                "state": None,
                "transition": None,
            }
        record = build_consequence(
            result,
            body_id=body_id,
            world_facts=world_facts,
            pose=pose,
            applied_at=str(at),
        )
        record["state"] = CONSEQUENCE_STATE_PENDING
        record["inputs"] = []
        record.pop("input_summary", None)
        record["notes"] = [
            item for item in record["notes"] if not item.startswith("movement_load")
        ]
        record["notes"].append(note)
        record["movement_settlement"] = "pending"
        stored = ledger.append_unlocked(record)
    return {
        "deferred": True,
        "reason": note,
        "consequence": stored,
        "state": None,
        "transition": None,
    }


def pending_movements(ledger: "ConsequenceLedger") -> list[dict[str, Any]]:
    """Pending records whose load is still owed (read-only).

    Used by the owner tick to settle a walk whose arrival was written but whose
    body load never landed (process died in between).
    """

    return [
        record
        for record in ledger.pending()
        if record.get("action_type") == "MOVE"
    ]

def reconcile_pending(
    ledger: ConsequenceLedger,
    *,
    execution_id: str,
    confirmed_result: Mapping[str, Any],
    body_id: str,
    runtime_state: Mapping[str, Any],
    at: Any,
    world_facts: Optional[Mapping[str, Any]] = None,
    pose: Optional[str] = None,
    runtime_version: Optional[str] = None,
) -> dict[str, Any]:
    """Apply a previously UNKNOWN consequence, but only once SUCCESS is confirmed."""

    verdict = classify_status(confirmed_result)
    if verdict != "SUCCESS":
        return {
            "applied": False,
            "reason": f"not_confirmed_success:{verdict.lower()}",
            "state": runtime_state,
            "transition": None,
        }
    return apply_for_execution(
        confirmed_result,
        body_id=body_id,
        ledger=ledger,
        runtime_state=runtime_state,
        at=at,
        world_facts=world_facts,
        pose=pose,
        runtime_version=runtime_version,
    )


# ---------------------------------------------------------------------------
# v0.4 room-living: the one legal ENVIRONMENT (room light) body input
# ---------------------------------------------------------------------------

#: A gap longer than this is *not* filled with a fabricated "the lamp was on the
#: whole time" observation.  A stopped process means unknown, not continuous.
MAX_ENVIRONMENT_GAP_SECONDS = 900.0
MIN_ENVIRONMENT_SECONDS = 60.0


def build_environment_input(light: Optional[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
    """The single ENVIRONMENT input for a light fact (``None`` = no stimulus).

    Delegated to ``world_room_environment`` so the band table has exactly one
    definition.  ``None`` is returned for unknown light, an off lamp and an
    undeclared room -- never a zero that would look like a measurement.
    """

    import world_room_environment as room

    return room.environment_input(light)


def _instant_of(value: Any) -> Any:
    """Parse a bounded ISO instant (``None`` when it is absent/unreadable)."""

    if value is None:
        return None
    try:
        from datetime import datetime

        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo is not None else None


def advance_environment(
    *,
    runtime_state: Mapping[str, Any],
    light: Optional[Mapping[str, Any]],
    at: Any,
    min_seconds: float = MIN_ENVIRONMENT_SECONDS,
    max_gap_seconds: float = MAX_ENVIRONMENT_GAP_SECONDS,
    posture: Optional[str] = None,
    movement_in_flight_since: Optional[str] = None,
) -> dict[str, Any]:
    """Advance the body by the declared room light, at most once per tick.

    Pure: the caller persists ``state`` and appends ``transition``.  The Body's
    rate constants, thresholds and signal bands are untouched -- this only feeds
    the existing ``ENVIRONMENT.sensory_intensity`` input.  A ``dim`` lamp that
    never reaches ``sensory_load_elevated`` is a correct outcome, not a bug.

    ``posture`` is the World-measured pose in force (from the substrate owner).
    When given, the same advance also bills the accounted interval since
    ``last_advanced_at`` as posture, so a tick between two POSE actions cannot
    silently consume those seconds.  It is *not* billed while a recovery session
    is active (REST owns that interval; passive posture must not interrupt it)
    and never across an unknown gap or an unreadable clock.

    ``movement_in_flight_since`` (a departure instant) does **not** stop the tick:
    the light still lands on time.  It only bounds the posture interval at the
    departure, so the walk's own seconds are never billed twice and the stillness
    before departure is billed once, here, instead of being swallowed by the
    cursor move.  The arrival settlement then sees a cursor at/after departure
    and bills the measured walk without a second static remainder.
    """

    from datetime import datetime, timedelta

    import body_runtime as br
    import world_room_environment as room

    before = br.validate_runtime(runtime_state)
    last = before.get("last_advanced_at")
    if not last:
        return {"advanced": False, "reason": "no_body_clock", "wrote": False}
    try:
        last_dt = datetime.fromisoformat(str(last))
        now_dt = datetime.fromisoformat(str(at))
    except ValueError:
        return {"advanced": False, "reason": "unreadable_body_clock", "wrote": False}
    if now_dt < last_dt:
        return {"advanced": False, "reason": "clock_went_backwards", "wrote": False}
    # The observation anchor: we may only speak about the window we can justify.
    # If the lamp was switched on *after* the last body advance, the light has
    # only been on since then -- claiming the whole interval would be a fabricated
    # "it was lit all along".  This is also what keeps an old root from being
    # stuck: a lamp switched on now starts a fresh (short) window instead of
    # inheriting a days-old gap.
    started = _instant_of(light.get("since")) if isinstance(light, Mapping) else None
    anchor = last_dt if started is None or started <= last_dt else started
    if now_dt < anchor:
        return {"advanced": False, "reason": "clock_went_backwards", "wrote": False}
    elapsed = (now_dt - anchor).total_seconds()
    if elapsed < float(min_seconds):
        return {
            "advanced": False,
            "reason": "interval_too_short",
            "elapsed_seconds": round(elapsed, 3),
            "anchor": anchor.isoformat(),
            "wrote": False,
        }

    gap_unknown = elapsed > float(max_gap_seconds)
    posture_seconds = 0.0
    posture_pose = None
    if gap_unknown:
        # The process was not running across this window: what the light did in
        # between is unknown.  We therefore apply **no** environment input and
        # claim nothing about the gap -- but we still move the Body clock to now
        # over one bounded, input-free window, exactly so the next tick is a
        # normal one instead of being refused by the same gap forever.
        start_dt = now_dt - timedelta(seconds=float(max_gap_seconds))
        inputs = None
        result = br.advance(
            before,
            start_dt.isoformat(),
            now_dt.isoformat(),
            [],
            before["runtime_version"],
        )
    else:
        inputs = build_environment_input(light)
        if inputs is None:
            return {
                "advanced": False,
                "reason": "no_environment_input",
                "elapsed_seconds": round(elapsed, 3),
                "anchor": anchor.isoformat(),
                "wrote": False,
            }
        posture_seconds = 0.0
        posture_pose = None
        candidate = str(posture or "").strip().lower()
        posture_end_dt = now_dt
        if movement_in_flight_since:
            departure = _instant_of(movement_in_flight_since)
            if departure is not None:
                if departure.tzinfo is None:
                    departure = departure.replace(tzinfo=now_dt.tzinfo)
                # Only the static interval before departure is attributable here;
                # the walk itself is settled by the arrival.
                posture_end_dt = min(now_dt, departure)
        if candidate and before["recovery"]["state"] != "active":
            # the body cursor, not the lamp's `since`: the pose held for the
            # whole accounted interval even if the lamp was switched on later
            posture_seconds = _posture_interval(
                (posture_end_dt - last_dt).total_seconds()
            )
            if posture_seconds > 0.0:
                posture_pose = candidate
                inputs = [inputs, {
                    "class": "PHYSICAL_RESULT",
                    "movement_duration_seconds": 0.0,
                    "effort": "light",
                    "posture_seconds": posture_seconds,
                    "pose": candidate,
                    "execution_id": "environment-tick:%s" % now_dt.isoformat(),
                }]
        result = br.advance(
            before,
            anchor.isoformat(),
            now_dt.isoformat(),
            inputs,
            before["runtime_version"],
        )

    if not result.get("advanced") and not gap_unknown:
        return {
            "advanced": False,
            "reason": "advance_produced_no_change",
            "elapsed_seconds": round(elapsed, 3),
            "wrote": False,
            "input": inputs,
        }
    transition = br.build_transition(
        before,
        result["state"],
        at=now_dt.isoformat(),
        cause_class=br.CAUSE_ENVIRONMENT if inputs is not None else br.CAUSE_TIME_ADVANCE,
        cause_classes=result["cause_classes"],
        input_summary={
            **result["inputs_summary"],
            "environment_source": room.environment_note(light or {}),
            "environment_model": room.ENVIRONMENT_MODEL,
            "observation_anchor": anchor.isoformat(),
            "environment_gap_unknown": bool(gap_unknown),
            "posture_interval_seconds": round(posture_seconds, 3),
            "posture_pose": posture_pose,
            "posture_interval_source": POSTURE_INTERVAL_SOURCE if posture_pose else None,
            "movement_in_flight_since": movement_in_flight_since,
        },
    )
    return {
        "advanced": bool(result.get("advanced")),
        "clock_moved": True,
        "reason": "environment_gap_unknown" if gap_unknown else "environment_applied",
        "gap_unknown": bool(gap_unknown),
        "elapsed_seconds": round(elapsed, 3),
        "wrote": True,
        "input": inputs,
        "state": result["state"],
        "transition": transition,
    }
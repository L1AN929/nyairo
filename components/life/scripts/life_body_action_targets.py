#!/usr/bin/env python3
"""W: one encoding for a *body* action's whole parameter set, and nothing else.

Why this module exists
----------------------
AR-0's World adapter carries exactly one string per action (``verb`` + ``target``),
and the Life core reads the target's namespace back to decide which owner verb an
adopted opportunity implies.  ``place:`` / ``object:`` / ``stay:`` already work that
way.  Two of the World owner's verbs need **two** parameters and therefore had no
spelling at all:

* ``POSE`` needs ``pose`` *and* ``orientation`` (``world_action_resolver:1489``);
* ``REST`` needs ``state`` (start|stop) *and* ``mode`` (``world_action_resolver:1638``).

This module is the single place that spelling lives:

    bodyact:pose:<pose>:<orientation>
    bodyact:rest:<start|stop>:rest

It provides exactly three things, and the core re-uses them instead of inventing a
second encoding:

* ``parse_body_action_target(ref)`` -- the encoded ref back to the owner's own
  parameters, plus the ``fact_key``/``expected_value`` a completion contract must
  name (``-> None`` for any other namespace);
* ``build_body_action_step(ref, *, place, revision)`` -- the AG-2 world step spec,
  with the idempotency key bound to every parameter *and* the precondition revision;
* ``body_action_observed_fact(parsed, snapshot, ...)`` -- a pure reading of what the
  canonical World snapshot says about that exact action.

What it deliberately does NOT do
--------------------------------
* it does not decide whether she needs to sit down, lie down or rest: the
  opportunity set comes from the World owner's own ``get_available_actions``
  (``world_move_opportunity_source.observed_body_action_opportunities``);
* it does not submit, mint, gate or receipt anything -- AR-0 still owns the ledger,
  the Intent Executor still mints the one-shot authorization and the Resolver is
  still the only committer;
* a successful REST **command** is not body recovery: the fact this module can read
  is the recovery *session* state, never a fatigue/healing/health claim.  There is
  no ``sleep_like`` at all: the mode is refused by the encoding and the parser, not merely
  absent from the opportunity list.

Enumerations
------------
The tuples below are a *copy* of the World owner's published vocabulary
(``world_body_substrate.POSES``/``ORIENTATIONS``, ``world_action_resolver.REST_STATES``).
They are used only to refuse a malformed or incomplete encoding *before* submission; the
owner stays the only authority over what may commit, and a vocabulary the owner extends
must be extended here in the same change.

Rest modes: narrow admission
----------------------------
The World owner can execute two rest modes (``world_action_resolver.REST_MODES``:
``rest`` and ``sleep_like``).  **This round admits ``rest`` only.**  The encoding, the
parser and the step builder all refuse ``sleep_like``, and the World bridge decodes
through the parser -- so an executable ``sleep_like`` rest cannot be smuggled in by
hand-writing a target, and "the opportunity emitter happens not to offer it" is not the
only thing keeping it out.  Widening this is a separate, explicit change that must also
justify the lying-pose + declared-surface preconditions the owner already enforces.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional
from urllib.parse import quote

#: The target namespace this module owns.  Every other prefix is somebody else's.
BODY_ACTION_REF_PREFIX = "bodyact:"

POSE_VERB = "POSE"
REST_VERB = "REST"
#: The verbs this encoding can express completely.
BODY_ACTION_VERBS = (POSE_VERB, REST_VERB)

POSE_ACTION = "pose"
REST_ACTION = "rest"
BODY_ACTION_ACTIONS = (POSE_ACTION, REST_ACTION)

#: Owner posture vocabulary (copied, see the module docstring).
POSES = ("standing", "seated", "lying", "transitioning")
ORIENTATIONS = ("north", "south", "east", "west", "up", "down")
REST_STATES = ("start", "stop")
#: The owner's *full* rest-mode vocabulary (``world_action_resolver.REST_MODES``), kept
#: here as the documented fact it is -- and deliberately **not** the admission set.
OWNER_REST_MODES = ("rest", "sleep_like")
#: The modes this encoding admits.  ``rest`` only, this round: ``sleep_like`` is refused
#: by ``encode_rest_target``, ``parse_body_action_target`` and therefore by
#: ``build_body_action_step`` and the World bridge, which decode through the parser.
REST_MODES = ("rest",)

#: LR-5 observed-fact keys.  Same ``world_fact:`` namespace LR-5 already owns
#: (``activity_progress_completion_lr5.POLICY_WORLD_FACT_V1``); no new policy version.
FACT_KEY_BODY_POSE = "world_body_pose_ref"
FACT_KEY_BODY_RECOVERY = "world_body_recovery_ref"

FACT_PREFIX = "world_fact:body:"
POSE_FACT = FACT_PREFIX + "pose:%s:%s"
RECOVERY_FACT = FACT_PREFIX + "recovery:%s"
#: The two recovery *session* states the gateway reports (``_rest_availability``).
RECOVERY_STATE_ACTIVE = "active"
RECOVERY_STATE_INACTIVE = "inactive"
#: v0.4 A (pacing): the owner's **other** closed session words (``body_runtime.RECOVERY_STATES``).
#: A stop really does end the session, and the owner then reports one of these -- measured on the
#: real chain: after a successful ``REST`` stop the gateway says ``completed``, which used to make
#: the completion fact unmatchable, so the REST Activity could never finish and kept the
#: foreground.  All three mean the same *session* fact ("no session is open"); none of them is a
#: recovery claim, which is why the fact value stays the session word rather than "recovered".
RECOVERY_STATES_CLOSED = frozenset({RECOVERY_STATE_INACTIVE, "completed", "interrupted"})

#: Where each fact is really read from.  Named in the evidence so a report can say
#: which owner surface answered.
SOURCE_SNAPSHOT = "world_gateway.get_snapshot"
SOURCE_AVAILABLE_ACTIONS = "world_gateway.get_available_actions"

#: Effect predicates AG-2 records for the step.  They are Life-side labels, not World
#: facts: the World fact is the one ``fact_key``/``expected_value`` names.
EFFECT_POSE_CHANGED = "body_pose_changed"
EFFECT_RECOVERY_STARTED = "recovery_session_started"
EFFECT_RECOVERY_STOPPED = "recovery_session_stopped"


class BodyActionTargetError(ValueError):
    """A ``bodyact:`` ref that cannot be expressed completely and legally."""


def is_body_action_target(ref: Any) -> bool:
    """Is this ref in the body-action namespace at all?  Says nothing about validity."""

    return str(ref or "").strip().startswith(BODY_ACTION_REF_PREFIX)


def encode_pose_target(pose: str, orientation: str) -> str:
    """``bodyact:pose:<pose>:<orientation>`` -- both parameters, always both."""

    pose_text = str(pose or "").strip()
    orientation_text = str(orientation or "").strip()
    if pose_text not in POSES:
        raise BodyActionTargetError(
            "pose must be one of %s (got %r)" % (list(POSES), pose_text))
    if orientation_text not in ORIENTATIONS:
        raise BodyActionTargetError(
            "orientation must be one of %s (got %r)"
            % (list(ORIENTATIONS), orientation_text))
    return "%s%s:%s:%s" % (BODY_ACTION_REF_PREFIX, POSE_ACTION, pose_text,
                           orientation_text)


def encode_rest_target(state: str, mode: str = "rest") -> str:
    """``bodyact:rest:<start|stop>:rest`` -- both parameters, always both.

    ``sleep_like`` is refused: the owner can execute it, but this round does not admit it,
    and an encoding that cannot be built cannot be submitted by hand either.
    """

    state_text = str(state or "").strip().lower()
    mode_text = str(mode or "").strip().lower()
    if state_text not in REST_STATES:
        raise BodyActionTargetError(
            "rest state must be one of %s (got %r)" % (list(REST_STATES), state_text))
    if mode_text not in REST_MODES:
        raise BodyActionTargetError(
            "rest mode must be one of %s (got %r)" % (list(REST_MODES), mode_text))
    return "%s%s:%s:%s" % (BODY_ACTION_REF_PREFIX, REST_ACTION, state_text, mode_text)


def parse_body_action_target(ref: Any) -> Optional[dict[str, Any]]:
    """Split a ``bodyact:`` ref back into the owner's exact parameters.

    Returns ``None`` when the ref is not a body-action ref at all (another namespace
    is not this module's business), and raises :class:`BodyActionTargetError` when it
    *is* one but is incomplete, carries extra fields, or names a value outside the
    owner's vocabulary -- a half-specified posture or rest command is never guessed at.

    The returned mapping is the whole contract, and only these five keys:

    ``verb``           ``POSE`` | ``REST``
    ``target``         the encoded ref, unchanged (AR-0's single-target identity)
    ``params``         the owner's own parameter mapping, nothing added
    ``fact_key``       the LR-5 observed-fact key a completion contract names
    ``expected_value`` the LR-5 fact value that means "this action's world state holds"
    """

    text = str(ref or "").strip()
    if not text.startswith(BODY_ACTION_REF_PREFIX):
        return None
    fields = text.split(":")
    if len(fields) != 4:
        raise BodyActionTargetError(
            "%r must have exactly 4 colon-separated fields "
            "(bodyact:<pose|rest>:<a>:<b>), got %d" % (text, len(fields)))
    _prefix, action, first, second = fields
    if action not in BODY_ACTION_ACTIONS:
        raise BodyActionTargetError(
            "%r names action %r; this encoding carries %s"
            % (text, action, list(BODY_ACTION_ACTIONS)))

    if action == POSE_ACTION:
        if first not in POSES:
            raise BodyActionTargetError(
                "%r: pose must be one of %s" % (text, list(POSES)))
        if second not in ORIENTATIONS:
            raise BodyActionTargetError(
                "%r: orientation must be one of %s" % (text, list(ORIENTATIONS)))
        return {
            "verb": POSE_VERB,
            "target": text,
            "params": {"pose": first, "orientation": second},
            "fact_key": FACT_KEY_BODY_POSE,
            "expected_value": POSE_FACT % (first, second),
        }

    if first not in REST_STATES:
        raise BodyActionTargetError(
            "%r: rest state must be one of %s" % (text, list(REST_STATES)))
    if second not in REST_MODES:
        raise BodyActionTargetError(
            "%r: rest mode must be one of %s" % (text, list(REST_MODES)))
    return {
        "verb": REST_VERB,
        "target": text,
        "params": {"state": first, "mode": second},
        "fact_key": FACT_KEY_BODY_RECOVERY,
        "expected_value": RECOVERY_FACT % (
            RECOVERY_STATE_ACTIVE if first == "start" else RECOVERY_STATE_INACTIVE),
    }


def body_action_effect_predicates(parsed: Mapping[str, Any]) -> list[str]:
    """The AG-2 effect label for one parsed body action."""

    if parsed["verb"] == POSE_VERB:
        return [EFFECT_POSE_CHANGED]
    state = str((parsed.get("params") or {}).get("state") or "")
    return [EFFECT_RECOVERY_STARTED if state == "start" else EFFECT_RECOVERY_STOPPED]


def build_body_action_step(
    ref: Any,
    *,
    place: Any = None,
    revision: Any = None,
    decision_ref: Any = None,
) -> dict[str, Any]:
    """The AG-2 world step spec for one body action.

    Same shape the core already builds for ``place:`` / ``object:`` (``step_kind``
    ``WORLD_ACTION``, one frozen ``payload_spec`` with a single ``target``), so AR-0's
    payload contract does not change: the *encoded ref itself* is the parameter set.

    Identity
    --------
    ``decision_ref`` is the committed Decision that chose this action.  When it is
    present the idempotency key names the Decision and the encoded target, and nothing
    observed: a retry or an UNKNOWN reconciliation for the *same* Decision must keep the
    same execution even if the owner's revision or her observed pose has moved on since
    the decision was committed, while a *different* Decision is a different logical body
    action even when place, revision and target all look identical again.  Observation
    coordinates cannot express that; they only describe where the decision was taken.

    Callers that carry no Decision identity keep the previous deterministic key
    (``place`` + ``revision`` + target).  That fallback is a compatibility path for
    hand-built steps, not a safety guarantee: two independent callers that happen to
    observe the same place/revision/target do collapse onto one execution by design.
    """

    parsed = parse_body_action_target(ref)
    if parsed is None:
        raise BodyActionTargetError(
            "%r is not a body action target" % (ref,))

    import life_integration_ag2 as ag2
    from action_reality_ledger import ACTION_KIND_WORLD

    place_text = str(place) if place else "unknown"
    target_ref = str(parsed["target"])
    decision_identity = "" if decision_ref is None else str(decision_ref).strip()
    if len(decision_identity) > 256:
        raise BodyActionTargetError(
            "decision_ref is too long (%d > 256)" % len(decision_identity))
    if decision_identity:
        # Quoted so a Decision id can never be read as one of the key's own fields.
        idempotency_key = "idem_step:v02:world:bodyact:decision:%s:%s" % (
            quote(decision_identity, safe=""), target_ref)
    else:
        idempotency_key = "idem_step:v02:world:bodyact:%s:r%s:%s" % (
            place_text, revision, target_ref)
    return {
        "step_kind": ag2.STEP_KIND_WORLD_ACTION,
        "action_kind": ACTION_KIND_WORLD,
        "target_ref": target_ref,
        "payload_spec": {
            "verb": str(parsed["verb"]),
            # AR-0 keeps its single-target contract: the whole parameter set is the
            # encoded ref, and the World bridge decodes it at submission time.
            "target": target_ref,
            # AR-0-side bookkeeping only: the World's own dependency evidence is
            # produced by the Intent Executor's fresh read, never by this payload.
            "expected_dependencies": {},
        },
        "expected_effect_predicates": body_action_effect_predicates(parsed),
        "post_submit_life_behavior": ag2.POST_SUBMIT_KEEP_ACTIVE,
        "idempotency_key": idempotency_key,
    }


def _recovery_state(rest_availability: Any) -> Optional[str]:
    """The Body's own recovery session state, or ``None`` when it cannot be read."""

    if not isinstance(rest_availability, Mapping):
        return None
    current = rest_availability.get("current")
    if not isinstance(current, Mapping):
        return None
    state = current.get("recovery_state")
    text = str(state or "").strip().lower()
    return text or None


def body_action_observed_fact(
    parsed: Mapping[str, Any],
    snapshot: Any,
    *,
    rest_availability: Any = None,
) -> dict[str, Any]:
    """What the canonical World observation says about *this* action, purely.

    ``snapshot`` is the gateway's own ``get_snapshot`` payload; ``rest_availability``
    is the gateway's own ``REST`` entry from ``get_available_actions`` (the only place
    the recovery session state is exposed).

    The result is what an LR-5 ``WORLD_FACT_MATCH`` contract needs -- and nothing more:

    ``value`` is ``None`` whenever the owner cannot answer, so an unreadable snapshot
    can never satisfy a criterion, and ``satisfied`` is true only when the read value
    is *exactly* the expected one.  For ``REST`` the value is the recovery **session**
    state: a committed command is not evidence that the body recovered.
    """

    if not isinstance(parsed, Mapping) or "fact_key" not in parsed:
        raise BodyActionTargetError("body_action_observed_fact needs a parsed target")
    verb = str(parsed.get("verb") or "")
    expected = parsed.get("expected_value")
    value: Optional[str] = None
    observed: dict[str, Any] = {}

    if verb == POSE_VERB:
        pose = (snapshot or {}).get("pose") if isinstance(snapshot, Mapping) else None
        orientation = ((snapshot or {}).get("orientation")
                       if isinstance(snapshot, Mapping) else None)
        observed = {"pose": pose, "orientation": orientation}
        pose_text = str(pose or "").strip()
        orientation_text = str(orientation or "").strip()
        if pose_text and orientation_text:
            value = POSE_FACT % (pose_text, orientation_text)
        source = SOURCE_SNAPSHOT
    elif verb == REST_VERB:
        state = _recovery_state(rest_availability)
        observed = {"recovery_state": state}
        # v0.4 A (pacing): the *session* fact is "open" vs "not open".  The owner spells the
        # closed side ``inactive`` / ``completed`` / ``interrupted`` (a stop normally answers
        # ``completed``), and all three mean the same thing: no recovery session is open.  The
        # owner's own word is echoed in ``observed``; the fact value is the session token the
        # completion contract names.  Any other/unread word stays ``None`` (unknown), so a failed
        # read can never satisfy the criterion.
        if state == RECOVERY_STATE_ACTIVE:
            value = RECOVERY_FACT % RECOVERY_STATE_ACTIVE
        elif state in RECOVERY_STATES_CLOSED:
            value = RECOVERY_FACT % RECOVERY_STATE_INACTIVE
        source = SOURCE_AVAILABLE_ACTIONS
    else:  # pragma: no cover - parse_body_action_target only returns the two verbs
        raise BodyActionTargetError("unknown body action verb %r" % (verb,))

    return {
        "fact_key": str(parsed["fact_key"]),
        "expected_value": expected,
        "value": value,
        "satisfied": bool(value is not None and value == expected),
        "source": source,
        "observed": observed,
    }


__all__ = [
    "BODY_ACTION_ACTIONS",
    "BODY_ACTION_REF_PREFIX",
    "BODY_ACTION_VERBS",
    "BodyActionTargetError",
    "EFFECT_POSE_CHANGED",
    "EFFECT_RECOVERY_STARTED",
    "EFFECT_RECOVERY_STOPPED",
    "FACT_KEY_BODY_POSE",
    "FACT_KEY_BODY_RECOVERY",
    "FACT_PREFIX",
    "ORIENTATIONS",
    "OWNER_REST_MODES",
    "POSES",
    "POSE_ACTION",
    "POSE_FACT",
    "POSE_VERB",
    "RECOVERY_FACT",
    "RECOVERY_STATE_ACTIVE",
    "RECOVERY_STATE_INACTIVE",
    "REST_ACTION",
    "REST_MODES",
    "REST_STATES",
    "REST_VERB",
    "SOURCE_AVAILABLE_ACTIONS",
    "SOURCE_SNAPSHOT",
    "body_action_effect_predicates",
    "body_action_observed_fact",
    "build_body_action_step",
    "encode_pose_target",
    "encode_rest_target",
    "is_body_action_target",
    "parse_body_action_target",
]

#!/usr/bin/env python3
"""chiyo-world-body.service -- standalone World / Body production runtime (M13A).

This is *productionisation glue*.  It owns process lifecycle, authority, health,
readiness, reconciliation and shutdown.  It changes no World rule, no Resolver
semantics and no somatic mathematics -- it calls the M12-verified modules exactly
as the acceptance harness does.

Design notes worth stating out loud:

* **Fail closed.**  Every startup condition in ticket section 10 raises
  :class:`StartupRefused`.  Nothing is auto-repaired, auto-created or reset.
* **Absent is not lost.**  A body layer that never existed is bootstrapped once
  and reported as ``body_bootstrapped``; it is never reported as a lost state.
* **Idle means idle.**  With no recovery session open and no event, a tick
  writes nothing at all (ticket sections 18/19).
* **No debug mutation.**  The control surface is read-only plus ``reconcile``
  (ticket section 31).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import pathlib
import selectors
import signal
import socket
import stat
import errno
import sys
import time
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional

HERE = pathlib.Path(__file__).resolve().parent

SCHEMA_SERVICE = "world.body.service.v1"
KIND_SERVICE = "world_body_service_status"
SERVICE_UNIT = "chiyo-world-body.service"

CONTROL_SOCKET_NAME = "control.sock"
DEFAULT_TICK_SECONDS = 60.0
MIN_ADVANCE_SECONDS = 60.0

LOG = logging.getLogger("chiyo-world-body")


def _ensure_sys_path(runtime_root: pathlib.Path) -> pathlib.Path:
    scripts = runtime_root / "scripts"
    text = str(scripts)
    if text not in sys.path:
        sys.path.insert(0, text)
    return scripts


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


class StartupRefused(RuntimeError):
    """The service must not run.  Carries the ticket section 10 condition name."""

    def __init__(self, condition: str, detail: str):
        super().__init__("%s: %s" % (condition, detail))
        self.condition = condition
        self.detail = detail


#: M14A: only this uid may call the gateway surface (transport layer).
GATEWAY_ALLOWED_PEER_UID = os.geteuid()


def gateway_peer_allowed(uid: int) -> bool:
    """Transport-layer admission: OS peer authorization (M14A section 7).

    Belt-and-braces with the 0600 socket mode.  Note this is *not* the
    business authority: a permitted peer still needs an Intent-Executor-issued
    authorization for any mutation.
    """

    return int(uid) == GATEWAY_ALLOWED_PEER_UID


class WorldBodyService:
    """One process, one World/Body home, one writer."""

    def __init__(
        self,
        config: Any,
        *,
        now: Optional[Any] = None,
        #: 显式压缩演示模式的业务时钟（无参 callable）。``None`` 是普通真实时间路径：
        #: 本服务继续用系统墙钟；非 None 时业务时刻（暴露/恢复/到期）读同一个域。
        clock: Optional[Callable[[], datetime]] = None,
        instance_id: Optional[str] = None,
        socket_path: Optional[pathlib.Path | str] = None,
        tick_seconds: float = DEFAULT_TICK_SECONDS,
    ):
        self.config = config
        self._now = now
        self._clock = clock
        self.instance_id = instance_id
        self.tick_seconds = float(tick_seconds)
        self.socket_path = pathlib.Path(socket_path) if socket_path else (
            config.run_dir / CONTROL_SOCKET_NAME
        )

        self.scripts_dir = _ensure_sys_path(config.runtime_root)

        import body_consequence as bcon
        import body_continuity as bct
        import body_reader as bread
        import body_runtime as br
        import world_action_resolver as war
        import world_body_binding as wbb
        import world_body_substrate as wbs
        import world_execution_ledger as wel

        self.br, self.bcon, self.bct, self.bread = br, bcon, bct, bread
        self.war, self.wbb, self.wbs, self.wel = war, wbb, wbs, wel

        self.body_store = br.BodyStore(config.body_dir)
        self.consequence_ledger = bcon.ConsequenceLedger(config.body_dir / "consequences.jsonl")
        self.execution_ledger = wel.ExecutionLedger(config.ledger_path)
        # The Resolver must see the *service's* runtime identity, not whatever
        # the caller happened to pass in: the World substrate gate lives here,
        # and a caller-supplied environment must never be able to disable or
        # redirect production authority.
        self.resolver = war.WorldResolver(
            config.world_home,
            ledger_path=config.ledger_path,
            environment={**dict(config.environment), **config.owned_environment()},
            # The service's own moment is the resolver's clock too: the one
            # live-source rule (a place's opening hours) must agree with the
            # moment this service reports everywhere else.
            now=self._moment,
        )

        self.lease = None  # set in startup()
        # The gateway talks to a separate, narrow socket (M13B).  Different
        # recipient, different surface, different permissions story.
        from world_body_gateway_api import GATEWAY_SOCKET_NAME, GatewaySurface

        self.gateway_socket_path = config.run_dir / GATEWAY_SOCKET_NAME
        self.request_timeout_seconds = 5.0
        # v0.4: the explicit local-owner (phone) binding.  Server-side only: the
        # subject is read from this process's own environment, and the uid from
        # this process's real identity.  Unset (or a malformed value) leaves the
        # surface unbound, so local-owner commands are refused.
        _local_owner_subject = str(
            os.environ.get("CHIYO_WORLD_LOCAL_OWNER_PHONE") or ""
        ).strip()
        self.local_owner_phone_binding = (
            {
                "source": "local_owner_phone",
                "uid": os.getuid(),
                "subject": _local_owner_subject,
                "world_home": str(config.world_home),
            }
            if _local_owner_subject
            else None
        )
        self.gateway_surface = GatewaySurface(
            self,
            include_test_objects=bool(getattr(config, "include_test_objects", False)),
            local_owner_binding=self.local_owner_phone_binding,
        )
        self.ready = False
        #: Set by ``shutdown()`` so a serve loop actually stops (a graceful stop
        #: must not leave a ticking thread behind).
        self._stop_requested = False
        self.started_at: Optional[str] = None
        self.shutdown_at: Optional[str] = None
        self.body_bootstrapped = False
        self.startup_report: dict[str, Any] = {}
        self.counters = {"ticks": 0, "idle_ticks": 0, "writes": 0, "reconciliations": 0}
        self.world_state: Optional[dict[str, Any]] = None
        self.body_state: Optional[dict[str, Any]] = None
        self.startup_reconciliation: dict[str, Any] = {}
        self.startup_catch_up: Optional[dict[str, Any]] = None

    # ------------------------------------------------------------------
    # time
    # ------------------------------------------------------------------
    def _moment(self) -> datetime:
        if self._clock is not None:
            return self._clock()
        return self._now if self._now is not None else datetime.now(timezone.utc)

    # ------------------------------------------------------------------
    # startup
    # ------------------------------------------------------------------
    def startup(self) -> dict[str, Any]:
        from world_body_authority import AuthorityLease
        from world_body_config import apply_process_environment

        cfg = self.config
        steps: list[dict[str, Any]] = []

        def step(name: str, detail: str, **extra: Any) -> None:
            entry = {"step": name, "ok": True, "detail": detail}
            entry.update(extra)
            steps.append(entry)
            LOG.info("startup %s: %s", name, detail)

        # 1. environment ---------------------------------------------------
        apply_process_environment(cfg)
        step("environment", "HERMES_HOME -> %s (process-local)" % cfg.world_home)

        # 2. runtime root / home existence ---------------------------------
        if not cfg.scripts_dir.is_dir():
            raise StartupRefused("HOME_PATH_WRONG", "missing scripts dir %s" % cfg.scripts_dir)
        if self.scripts_dir != cfg.scripts_dir:
            raise StartupRefused("HOME_PATH_WRONG", "sys.path scripts dir mismatch")
        if not os.access(cfg.world_home, os.R_OK | os.W_OK):
            raise StartupRefused("HOME_PATH_WRONG", "world home not read/write: %s" % cfg.world_home)
        step("paths", "runtime=%s home=%s" % (cfg.runtime_root, cfg.world_home))

        # 3. authority (single writer, fail closed) ------------------------
        # 权威是内核 flock 决定的（与时间无关）；marker 的 ``acquired_at`` 属于安全/审计面，
        # 显式压缩演示模式也写真实墙钟，不写业务逻辑时刻。
        self.lease = AuthorityLease(
            cfg.run_dir,
            service_name=SERVICE_UNIT,
            instance_id=self.instance_id,
            world_home=cfg.world_home,
            now=datetime.now(timezone.utc),
        )
        try:
            authority = self.lease.acquire()
        except Exception as error:  # AuthorityConflict and friends
            raise StartupRefused("DUPLICATE_AUTHORITY", str(error)) from error
        step(
            "authority",
            "instance=%s pid=%s" % (authority["instance_id"], authority["pid"]),
            reclaimed_from=authority.get("reclaimed_from"),
        )

        # 4. world ---------------------------------------------------------
        if not cfg.world_state_path.exists():
            raise StartupRefused(
                "WORLD_UNAVAILABLE", "no world state at %s" % cfg.world_state_path
            )
        try:
            world = json.loads(cfg.world_state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise StartupRefused("WORLD_UNAVAILABLE", "world state unreadable") from error
        schema = str(world.get("schema_version") or "")
        if not schema.startswith("world."):
            raise StartupRefused("WORLD_SCHEMA_UNKNOWN", "schema=%r" % schema)
        if not world.get("world_id"):
            raise StartupRefused("WORLD_SCHEMA_UNKNOWN", "world_id missing")
        if str(world.get("world_id")) != cfg.expected_world_id:
            raise StartupRefused(
                "WORLD_ID_MISMATCH",
                "expected %s, found %s" % (cfg.expected_world_id, world.get("world_id")),
            )
        self.world_state = world
        step("world", "world_id=%s revision=%s schema=%s"
             % (world.get("world_id"), world.get("revision"), schema))

        # 5. binding -------------------------------------------------------
        substrate = None
        try:
            store = self.wbs.SubstrateStore(self.wbs.substrate_path(cfg.world_home))
            substrate = store.load_with_migration()[0]
        except Exception as error:
            raise StartupRefused("BINDING_INVALID", "substrate unreadable: %s" % error) from error
        if substrate is not None:
            binding = self.wbs.verify_binding(substrate, world)
            if not binding.get("bound"):
                raise StartupRefused("BINDING_INVALID", "reasons=%s" % binding.get("reasons"))
        step("binding", "substrate=%s" % ("present" if substrate else "absent"))

        # 6. ledger --------------------------------------------------------
        try:
            chain = self.execution_ledger.verify()
        except Exception as error:
            raise StartupRefused("LEDGER_CORRUPT", str(error)) from error
        step("ledger", "valid=%s legacy=%s v2=%s"
             % (chain.get("valid"), chain.get("legacy_entries"), chain.get("v2_entries")))

        # 7. body ----------------------------------------------------------
        self.body_bootstrapped = False
        if self.body_store.runtime_path.exists():
            try:
                body, migrated = self.body_store.load_runtime_with_migration()
            except Exception as error:
                raise StartupRefused("BODY_SCHEMA_UNKNOWN", str(error)) from error
            if body is None:
                raise StartupRefused("BODY_SCHEMA_UNKNOWN", "runtime state vanished")
        else:
            body = self.br.initial_runtime(at=self._moment())
            self.body_store.save_runtime(body)
            self.body_bootstrapped = True
            migrated = False
        if self.body_store.load_profile() is None:
            self.body_store.save_profile(self.br.build_profile())
        self.body_state = body
        step("body", "body_id=%s version=%s bootstrapped=%s migrated=%s"
             % (body["body_id"], body["version"], self.body_bootstrapped, migrated))

        # 8. continuity / migration (fail closed on unknown schema) --------
        migration = self.bct.migrate_all(cfg.world_home)
        if not migration.get("all_ok"):
            broken = [k for k, v in migration["layers"].items() if not v.get("ok")]
            raise StartupRefused("MIGRATION_REQUIRED_UNAVAILABLE", "layers=%s" % broken)
        step("continuity", "layers=%d external_untouched=%s"
             % (len(migration["layers"]), migration.get("external_layers_untouched")))

        # 9. reconciliation ------------------------------------------------
        reconciliation = self.reconcile_now(persist=True)
        self.startup_reconciliation = reconciliation
        step("reconciliation", "in_flight=%s healed=%d unresolved=%d"
             % (reconciliation["in_flight"], len(reconciliation["healed"]),
                len(reconciliation["unresolved"])))

        # 10. bounded catch-up --------------------------------------------
        catch_up = self._advance_due(force=True)
        self.startup_catch_up = catch_up
        step("catch_up", "advanced=%s" % (catch_up or {}).get("advanced"))

        # 11. canonical activation ----------------------------------------
        mode = self.bread.resolve_mode(dict(os.environ))
        if cfg.canonical and mode != self.bread.MODE_CANONICAL:
            raise StartupRefused(
                "CANONICAL_NOT_ACTIVE", "requested canonical but reader mode=%s" % mode
            )
        step("authority_mode", "reader_mode=%s canonical=%s" % (mode, cfg.canonical))

        # 12. ready --------------------------------------------------------
        self.started_at = _iso(self._moment())
        self.ready = True
        report = {
            "kind": KIND_SERVICE,
            "schema_version": SCHEMA_SERVICE,
            "service": "chiyo-world-body.service",
            "ready": True,
            "started_at": self.started_at,
            "config": cfg.as_dict(),
            "steps": steps,
            "body_bootstrapped": self.body_bootstrapped,
            "authority": self.lease.status(),
        }
        self.startup_report = report
        return report

    # ------------------------------------------------------------------
    # work
    # ------------------------------------------------------------------
    def _live_substrate(self) -> Optional[dict[str, Any]]:
        """The World substrate as it is on disk now (read-only), or ``None``.

        Reconciliation must judge a CONSUME execution against the *owner's*
        current lifecycle record, not against a copy this process cached at
        startup.
        """

        try:
            store = self.wbs.SubstrateStore(self.wbs.substrate_path(self.config.world_home))
            return store.load_with_migration()[0]
        except Exception:
            return None

    def _measured_pose(self) -> Optional[str]:
        """The World substrate's pose, or ``None`` when it cannot be claimed.

        A pose is only attributed to an accounted Body interval when the World
        owner still holds one.  Nothing is inferred from the body cursor or from
        the last action's name.
        """

        substrate = self._live_substrate() or {}
        pose = substrate.get("pose")
        if isinstance(pose, str) and pose.strip():
            return pose.strip().lower()
        return None

    def _walk_in_flight(self) -> bool:
        try:
            return bool(self.bcon.pending_movements(self.consequence_ledger))
        except Exception:  # noqa: BLE001 - unreadable ledger: make no posture claim
            return True

    def _in_flight_movement(self) -> Optional[str]:
        """Departure instant of an in-flight walk whose load is still owed.

        The consequence ledger's own pending record carries ``applied_at`` (the
        departure) and ``movement_settlement=pending``; nothing is inferred from
        the action name or from the body cursor.
        """

        stamps = [
            str(record.get("applied_at"))
            for record in self.bcon.pending_movements(self.consequence_ledger)
            if record.get("movement_settlement") == "pending"
            and record.get("applied_at")
        ]
        return min(stamps) if stamps else None

    def reconcile_now(self, *, persist: bool = False) -> dict[str, Any]:
        from world_body_reconcile import reconcile_startup

        cfg = self.config
        report = reconcile_startup(
            ledger=self.execution_ledger,
            resolver=self.resolver,
            consequence_ledger=self.consequence_ledger,
            body_store=self.body_store,
            body_id=self.body_state["body_id"],
            runtime_state=self.body_state,
            at=self._moment(),
            world_facts={"world_id": (self.world_state or {}).get("world_id"),
                         "world_revision": (self.world_state or {}).get("revision")},
            pose=None,
            substrate=self._live_substrate(),
        )
        self.body_state = report["state"]
        if persist:
            self.body_store.save_runtime(self.body_state)
        self.counters["reconciliations"] += 1
        return report


    def _persist_consequence(self, outcome: Mapping[str, Any]) -> dict[str, Any]:
        """Persist an applied consequence: runtime **and** its transition record.

        `apply_for_execution` returns the transition; the caller owns writing it.
        Skipping this leaves every body consequence missing from the transition
        log, so the consequence ledger would reference a transition the journal
        never contained.
        """

        state = outcome["state"]
        self.body_store.save_runtime(state)
        transition = outcome.get("transition")
        if transition:
            self.body_store.append_transition(transition)
        self.counters["writes"] += 1
        return state

    # -- v0.4: REST is gated by the Body owner, before the Resolver ---------
    def _rest_body_gate(self, proposal: Mapping[str, Any]) -> Optional[dict[str, Any]]:
        """Refuse a REST command the Body itself says is impossible.

        The Body owns its thresholds (``RECOVERY_OPEN_FATIGUE``, one session at a
        time, "stop only what is open").  Those are *not* World facts, so they are
        decided here, before the Resolver is contacted: no ledger entry, no world
        write, no body write -- and no raw fatigue value is returned.
        """

        if not isinstance(proposal, Mapping):
            return None
        if str(proposal.get("action") or "").strip().upper() != "REST":
            return None
        params = proposal.get("params") or {}
        desired = str((params or {}).get("state") or "").strip().lower()
        recovery = (self.body_state or {}).get("recovery") or {}
        current = str(recovery.get("state") or "inactive")
        reason = None
        detail = None
        if desired == "start":
            if current == "active":
                reason = "RECOVERY_ALREADY_ACTIVE"
                detail = "a recovery session is already open; stop it instead"
            else:
                try:
                    fatigue = float(((self.body_state or {}).get("somatic") or {})["fatigue"])
                except (KeyError, TypeError, ValueError):
                    fatigue = None
                if fatigue is None:
                    reason = "BODY_STATE_UNAVAILABLE"
                    detail = "the runtime state cannot answer whether rest is needed"
                elif fatigue < self.br.RECOVERY_OPEN_FATIGUE:
                    reason = "RECOVERY_NOT_NEEDED"
                    detail = "rest is not needed right now (below the recovery threshold)"
        elif desired == "stop":
            if current != "active":
                reason = "NO_OPEN_RECOVERY"
                detail = "no recovery session is open, so there is nothing to stop"
        if reason is None:
            return None
        self.counters["preflight_rejections"] = self.counters.get("preflight_rejections", 0) + 1
        return {
            "execution_id": proposal.get("execution_id"),
            "action": "REST",
            "status": "rejected",
            "reason_code": reason,
            "detail": detail,
            "applied_to_body": False,
            "world_unchanged": True,
            "body_unchanged": True,
            "source": "body_preflight",
        }

    def _route_seconds(self, result: Mapping[str, Any]) -> Optional[int]:
        """The World topology's own duration for the hop this MOVE just took."""

        before = result.get("before")
        after = result.get("after")
        if not isinstance(before, Mapping) or not isinstance(after, Mapping):
            return None
        origin = before.get("world_location_id")
        destination = after.get("world_location_id")
        if not origin or not destination or str(origin) == str(destination):
            return None
        try:
            import world_travel_delivery as wtd

            seconds = wtd.route_duration_seconds(
                origin, destination, getattr(self.config, "environment", None)
            )
        except Exception:  # noqa: BLE001 - no route authority, no estimate
            return None
        if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds <= 0:
            return None
        return seconds

    #: v0.4 A (pacing): the one boundary that reconciles the two owner rules for REST.
    def _owner_rest_params(self, proposal: Mapping[str, Any]) -> dict[str, Any]:
        """Hand the Resolver only the REST parameters its own contract accepts.

        Two rules are the owner's and neither is changed here:

        * the bridge's *admission* vocabulary is ``state in {start, stop}`` **plus**
          ``mode = rest`` (``action_intent.REST_PARAMS``), and the intent/lifecycle evidence keeps
          that full parameter set;
        * the Resolver accepts ``mode`` **only** with ``state=start``
          (``world_action_resolver.py:1656``: "mode is only accepted with state=start").

        Measured on the real chain: a stop that spells ``mode`` was therefore refused by the
        Resolver, so a legal ``REST`` stop could never commit.  The service is the boundary that
        keeps the two consistent -- it forwards ``state`` alone for a stop, and touches nothing
        else about the proposal (no other verb, no other field, no widening of either rule).
        """

        if not isinstance(proposal, Mapping):
            return dict(proposal or {})
        if str(proposal.get("action") or "").strip().upper() != "REST":
            return dict(proposal)
        params = proposal.get("params")
        if not isinstance(params, Mapping):
            return dict(proposal)
        state = str(params.get("state") or "").strip().lower()
        if state == "start" or "mode" not in params:
            return dict(proposal)
        bounded = dict(proposal)
        bounded["params"] = {key: value for key, value in params.items() if key != "mode"}
        return bounded

    def act(self, proposal: Mapping[str, Any]) -> dict[str, Any]:
        """Resolve one proposal and apply its body consequence, in-process.

        This is the only mutation path, and it is the Resolver's path.  The
        service is the single writer, so actions are submitted to it rather than
        written alongside it.

        v0.4 adds exactly two honest behaviours, both of them about *time*:

        * a timed MOVE departure does **not** charge the walk yet -- the walk has
          not happened.  A ``pending`` consequence is recorded and the owner tick
          settles the real, measured load on arrival;
        * an instant MOVE charges the World topology's own route duration instead
          of the old fixed 600 s assumption.
        """

        proposal = self._owner_rest_params(proposal)
        now = self._moment()
        preflight = self._rest_body_gate(proposal)
        if preflight is not None:
            return preflight
        result = self.resolver.resolve(proposal)
        payload: dict[str, Any] = {
            "execution_id": result.get("execution_id"),
            "action": result.get("action") or result.get("action_type"),
            "status": result.get("status"),
            "reason_code": result.get("reason_code"),
            "legacy_reason_code": result.get("legacy_reason_code"),
            "applied_to_body": False,
        }
        action = str(result.get("action") or result.get("action_type") or "")
        if result.get("status") == "applied":
            world_facts: dict[str, Any] = {
                "world_id": (self.world_state or {}).get("world_id"),
                "world_revision": (self.world_state or {}).get("revision"),
            }
            departed = (
                result.get("legacy_reason_code") == "move_departed"
                or result.get("reason_code") == "move_departed"
            )
            if action == "MOVE" and departed:
                # The walk started; the load is owed, not paid.
                deferred = self.bcon.defer_for_execution(
                    result,
                    body_id=self.body_state["body_id"],
                    ledger=self.consequence_ledger,
                    at=now,
                    world_facts=world_facts,
                    pose=result.get("pose"),
                )
                payload["consequence_reason"] = "deferred:movement_load_pending"
                payload["consequence_id"] = (deferred.get("consequence") or {}).get(
                    "consequence_id"
                )
                payload["movement_settlement"] = "pending"
            else:
                if action == "MOVE":
                    seconds = self._route_seconds(result)
                    if seconds is not None:
                        world_facts["movement_seconds"] = float(seconds)
                        world_facts["movement_source"] = (
                            self.bcon.MEASUREMENT_ROUTE_ESTIMATE
                        )
                outcome = self.bcon.apply_for_execution(
                    result,
                    body_id=self.body_state["body_id"],
                    ledger=self.consequence_ledger,
                    runtime_state=self.body_state,
                    at=now,
                    world_facts=world_facts,
                    pose=result.get("pose"),
                    # CONSUME results name the instance they consumed; the body
                    # records the resource reference only (no intake semantics).
                    object_id=result.get("object_id"),
                )
                payload["applied_to_body"] = bool(outcome["applied"])
                payload["consequence_reason"] = outcome.get("reason")
                payload["consequence_id"] = (outcome.get("consequence") or {}).get(
                    "consequence_id"
                )
                if outcome["applied"] or outcome.get("cursor_advanced"):
                    # an UNKNOWN POSE/MOVE closes its window with an
                    # input-free cursor advance; it must be persisted too
                    self.body_state = self._persist_consequence(outcome)
                if action == "MOVE":
                    payload["movement_settlement"] = (
                        "settled" if outcome["applied"] else "unsettled"
                    )
        if action == "REST":
            payload["recovery_state"] = (
                (self.body_state or {}).get("recovery") or {}
            ).get("state")
        # v0.4: the owner's own evidence travels with the result.  It is read
        # from the resolver's payload, never re-derived here: a receipt is the
        # only proof of a consumption, so a caller must not have to guess it.
        for _key in (
            "object_id",
            "consumption_receipt",
            "purchase_voucher",
            "fixture",
            "arranged",
            "rest_surface_id",
        ):
            if result.get(_key) is not None:
                payload[_key] = result[_key]
        return payload

    def apply_result(self, result: Mapping[str, Any]) -> dict[str, Any]:
        """Apply one canonical execution result to the body, exactly once.

        This is the M9 integration seam: an execution layer submits a canonical
        world action result and the body derives its consequence.  In M13A the
        execution layer is not connected yet (that is M13B), so a local client
        may submit a well-formed result here.  It is *not* a way to set body
        state: the consequence mapping rejects anything that is not a canonical
        result, and `build_consequence` refuses raw somatic input.
        """

        outcome = self.bcon.apply_for_execution(
            result,
            body_id=self.body_state["body_id"],
            ledger=self.consequence_ledger,
            runtime_state=self.body_state,
            at=self._moment(),
            world_facts={
                "world_id": (self.world_state or {}).get("world_id"),
                "world_revision": (self.world_state or {}).get("revision"),
            },
            pose=result.get("pose"),
            object_id=result.get("object_id"),
        )
        payload = {
            "execution_id": (outcome.get("consequence") or {}).get("execution_id"),
            "action": (outcome.get("consequence") or {}).get("action_type"),
            "verdict": (outcome.get("consequence") or {}).get("verdict"),
            "applied": bool(outcome["applied"]),
            "reason": outcome.get("reason"),
            "consequence_id": (outcome.get("consequence") or {}).get("consequence_id"),
        }
        if outcome["applied"] or outcome.get("cursor_advanced"):
            self.body_state = self._persist_consequence(outcome)
        return payload
    def _recovery_open(self) -> bool:
        return (self.body_state or {}).get("recovery", {}).get("state") == "active"

    def _advance_due(self, *, force: bool = False) -> Optional[dict[str, Any]]:
        """Advance only when there is real work: an open recovery session.

        A REST session owns its interval: the catch-up moves the Body cursor but
        deliberately bills no posture, because passive posture strain must not
        be re-read as the physical load that interrupts recovery.  Idle ticks
        outside recovery advance nothing at all, so the interval stays available
        to the next real settlement instead of being dropped.
        """

        state = self.body_state
        if state is None:
            return None
        last = state.get("last_advanced_at")
        if not last:
            return None
        try:
            last_dt = datetime.fromisoformat(str(last))
        except ValueError:
            return None
        now = self._moment()
        elapsed = (now - last_dt).total_seconds()
        if not force and not self._recovery_open():
            return None
        if not force and elapsed < MIN_ADVANCE_SECONDS:
            return None

        result = self.br.catch_up(state, last, now)
        if not result.get("advanced"):
            # Nothing actually changed.  An idle restart must not fabricate body
            # history, so memory and disk are both left exactly as they were.
            # (`advance` always refreshes last_advanced_at, which would otherwise
            # make "state differs" true on every single boot.)
            return {
                "advanced": False,
                "elapsed_seconds": round(elapsed, 3),
                "wrote": False,
                "catch_up": result.get("catch_up"),
            }

        before_state, after_state = state, result["state"]
        self.body_state = after_state
        transition = self.br.build_transition(
            before_state,
            after_state,
            at=now,
            cause_class=self.br.CAUSE_RECOVERY if self._recovery_open()
            else self.br.CAUSE_TIME_ADVANCE,
            input_summary={"catch_up": result.get("catch_up")},
        )
        self.body_store.append_transition(transition)
        self.body_store.save_runtime(after_state)
        self.counters["writes"] += 1
        return {
            "advanced": True,
            "elapsed_seconds": round(elapsed, 3),
            "wrote": True,
            "catch_up": result.get("catch_up"),
        }

    # -- v0.4: the owner tick is the only arrival writer --------------------
    def _travel_module(self):
        try:
            import world_travel_delivery as wtd

            return wtd
        except Exception:  # noqa: BLE001 - no travel owner, no arrivals
            return None

    def _travel_state(self) -> Optional[dict[str, Any]]:
        wtd = self._travel_module()
        if wtd is None:
            return None
        try:
            if not wtd.travel_enabled(getattr(self.config, "environment", None)):
                return None
            state = wtd.load_state(
                self.config.world_home, getattr(self.config, "environment", None)
            )
        except Exception:  # noqa: BLE001
            return None
        return state if isinstance(state, Mapping) else None

    def _travel_record(self, execution_id: Any) -> Optional[dict[str, Any]]:
        state = self._travel_state()
        if state is None:
            return None
        for item in state.get("travels") or []:
            if isinstance(item, Mapping) and str(item.get("execution_id")) == str(execution_id):
                return dict(item)
        return None

    @staticmethod
    def _measured_seconds(record: Mapping[str, Any], now: Any) -> Optional[float]:
        """``min(actually elapsed, the owner's own route duration)``.

        Never the full route *before* it is walked, and never more than the route
        the World declared.  When neither number is readable there is no
        measurement (and the adapter falls back to an explicit assumption).
        """

        def _instant(value: Any) -> Optional[float]:
            if value is None:
                return None
            try:
                text = str(value).replace("Z", "+00:00")
                return datetime.fromisoformat(text).timestamp()
            except (TypeError, ValueError):
                return None

        started = _instant(record.get("started_at"))
        arrived = _instant(record.get("arrived_at")) or _instant(now)
        candidates: list[float] = []
        if started is not None and arrived is not None and arrived > started:
            candidates.append(arrived - started)
        declared = record.get("route_duration_seconds")
        if isinstance(declared, (int, float)) and not isinstance(declared, bool) and declared > 0:
            candidates.append(float(declared))
        return round(min(candidates), 3) if candidates else None

    def _apply_arrival(
        self, execution_id: str, record: Mapping[str, Any], *, source: str
    ) -> dict[str, Any]:
        """Settle the walk's real load, exactly once, for one execution."""

        now = self._moment()
        seconds = self._measured_seconds(record, now)
        world_facts: dict[str, Any] = {
            "world_id": (self.world_state or {}).get("world_id"),
            "world_revision": (self.world_state or {}).get("revision"),
        }
        if seconds is not None:
            world_facts["movement_seconds"] = seconds
            world_facts["movement_source"] = source
        result = {
            "kind": "world_action_result",
            "schema_version": getattr(self.war, "SCHEMA_VERSION", "world.action.resolver.v1"),
            "execution_id": execution_id,
            "action": "MOVE",
            "status": "applied",
            "reason_code": "move_arrived",
            "legacy_reason_code": "move_arrived",
            "occurred_at": _iso(now),
        }
        outcome = self.bcon.apply_for_execution(
            result,
            body_id=self.body_state["body_id"],
            ledger=self.consequence_ledger,
            runtime_state=self.body_state,
            at=now,
            world_facts=world_facts,
            pose=None,
        )
        if outcome["applied"]:
            self.body_state = self._persist_consequence(outcome)
        return {
            "execution_id": execution_id,
            "applied": bool(outcome["applied"]),
            "reason": outcome.get("reason"),
            "movement_seconds": seconds,
            "movement_source": source if seconds is not None else "assumed_default",
            "consequence_id": (outcome.get("consequence") or {}).get("consequence_id"),
        }

    def _settle_arrivals(self) -> Optional[dict[str, Any]]:
        """Realize one due arrival (the only writer) and settle its load."""

        wtd = self._travel_module()
        if wtd is None:
            return None
        try:
            arrived = wtd.realize_due_travel(
                hermes_home=self.config.world_home,
                environment=getattr(self.config, "environment", None),
                now=self._moment(),
            )
        except Exception:  # noqa: BLE001 - a broken travel owner is not an arrival
            return None
        if not isinstance(arrived, Mapping) or arrived.get("status") != "arrived":
            return None
        execution_id = str(arrived.get("execution_id") or "")
        if not execution_id:
            return None
        settlement = self._apply_arrival(
            execution_id, arrived, source=self.bcon.MEASUREMENT_MEASURED_TRAVEL
        )
        return {
            "travel_id": arrived.get("travel_id"),
            "destination_location_id": arrived.get("destination_location_id"),
            "world_changed": arrived.get("world_changed"),
            "settlement": settlement,
        }

    def _settle_pending_movements(self) -> Optional[dict[str, Any]]:
        """Settle walks whose arrival landed but whose load never did.

        The crash window between "the World moved" and "the body was charged" is
        real: the departure record is still ``pending`` and the travel record is
        already ``ARRIVED``.  Settling is idempotent (one consequence id per
        execution), so replaying it is safe.
        """

        wtd = self._travel_module()
        if wtd is None:
            return None
        try:
            pending = self.bcon.pending_movements(self.consequence_ledger)
        except Exception:  # noqa: BLE001
            return None
        if not pending:
            return None
        settled: list[dict[str, Any]] = []
        for record in pending:
            execution_id = str(record.get("execution_id") or "")
            if not execution_id:
                continue
            travel = self._travel_record(execution_id)
            if travel is None:
                continue
            if str(travel.get("status")) != wtd.TRAVEL_STATUS_ARRIVED:
                continue
            settled.append(
                self._apply_arrival(
                    execution_id,
                    travel,
                    source=self.bcon.MEASUREMENT_MEASURED_TRAVEL_RECOVERED,
                )
            )
        return {"settled": settled} if settled else None

    def tick(self) -> Optional[dict[str, Any]]:
        """One bounded wake.  Returns None when there was nothing to do.

        Order matters: an arrival is realized first (the single World writer),
        its walk's load is settled second, any leftover pending settlement is
        retried third, then a due home delivery is realized, and only then does
        the Body advance -- so a walk that interrupts a rest is charged before
        the rest is advanced.

        v0.4 room-living adds exactly one rule on top: **at most one Body time
        advance per tick**.  If the recovery advance already ran, the room-light
        input waits for the next wake instead of double-counting the same
        interval.
        """

        self.counters["ticks"] += 1
        arrival = self._settle_arrivals()
        pending = self._settle_pending_movements()
        deliveries = self._settle_deliveries()
        work = self._advance_due()
        environment = None
        if not (work and work.get("wrote")):
            environment = self._settle_environment()
        report: dict[str, Any] = {}
        if arrival:
            report["arrival"] = arrival
        if pending:
            report["pending_settlement"] = pending
        if deliveries:
            report["deliveries"] = deliveries
        if work:
            report["advance"] = work
        if environment:
            report["environment"] = environment
        if not report:
            self.counters["idle_ticks"] += 1
            return None
        return report

    # -- v0.4 room-living: the owner tick also delivers and feeds the light ----

    def _economy_module(self):
        try:
            import world_property_economy as economy

            return economy
        except Exception:  # noqa: BLE001 - no economy owner, no deliveries
            return None

    def _room_environment_module(self):
        try:
            import world_room_environment as room

            return room
        except Exception:  # noqa: BLE001 - no declarations, no light input
            return None

    def _materialize_module(self):
        """The one substrate materialization seam (shared with the phone path).

        Imported lazily so a service that never bought anything pays nothing,
        and so the seam stays a single implementation instead of a second copy
        in the service.
        """

        try:
            import world_phone_shopping as shopping

            return shopping.materialize_owned_item
        except Exception:  # noqa: BLE001 - no seam, no materialization
            return None

    def _delivered_unplaced(self) -> list[str]:
        """Owned + delivered + **not placed** instances (read-only).

        That is exactly "it arrived home but nothing has arranged it yet": the
        economy's coarse location says ``home/bedroom`` and the placement map has
        no entry.  A consumed instance has no placement either, but it is not a
        candidate here only if the economy still says delivered -- the seam
        refuses it as a terminal state anyway, so this set can never resurrect
        one.
        """

        economy = self._economy_module()
        if economy is None:
            return []
        try:
            state = economy.load_state(self.config.world_home)
        except Exception:  # noqa: BLE001
            return []
        if not isinstance(state, Mapping):
            return []
        try:
            substrate = self.wbs.SubstrateStore(
                self.wbs.substrate_path(self.config.world_home)
            ).load()
        except Exception:  # noqa: BLE001
            return []
        if substrate is None:
            return []
        placements = substrate.get("placements") or {}
        return [
            str(item["item_instance_id"])
            for item in state.get("items") or []
            if isinstance(item, Mapping)
            and str(item.get("disposition")) == economy.DISPOSITION_OWNED
            and item.get("current_location") == economy.HOME_ITEM_LOCATION
            and str(item.get("item_instance_id")) not in placements
        ]

    def _materialize_delivered(self) -> list[dict[str, Any]]:
        materialize = self._materialize_module()
        if materialize is None:
            return []
        environment = getattr(self.config, "environment", None)
        outcomes: list[dict[str, Any]] = []
        for item_id in self._delivered_unplaced():
            try:
                outcome = materialize(
                    hermes_home=self.config.world_home,
                    item_instance_id=item_id,
                    environment=environment,
                )
            except Exception:  # noqa: BLE001 - a failure is not a placement
                continue
            status = str(outcome.get("status") or "")
            if status == "already_materialized":
                continue
            outcomes.append(
                {
                    "item_instance_id": item_id,
                    "status": status,
                    "reason_code": outcome.get("reason_code"),
                    "placement": outcome.get("placement"),
                }
            )
        return outcomes

    def _settle_deliveries(self) -> Optional[dict[str, Any]]:
        """Realize due deliveries **and** put the arrived instance home.

        Two owner writes, both idempotent, both only from the tick:

        1. ``economy.realize_due_deliveries`` moves a due delivery to DELIVERED
           and its instance's coarse location to ``home/bedroom``;
        2. the one materialization seam gives every owned, delivered, unplaced
           instance its legal ``home/bedroom`` placement -- otherwise a counter
           purchase would be "delivered" and still impossible to arrange, which
           is exactly the dead end this step closes.

        A read path never calls either of them.
        """

        report: dict[str, Any] = {}
        economy = self._economy_module()
        if economy is not None:
            try:
                realized = economy.realize_due_deliveries(
                    hermes_home=self.config.world_home,
                    environment=getattr(self.config, "environment", None),
                    now=self._moment(),
                )
            except Exception:  # noqa: BLE001 - a failed realization is not an arrival
                realized = None
            if isinstance(realized, Mapping) and realized.get("status") == "applied":
                report["realized"] = realized
        # A tick runs several times a second: scanning both owner documents on
        # every wake would be pure overhead.  The scan only re-runs when one of
        # them actually changed since the last look (or when this tick realized
        # something), which is exactly when a new arrival could exist.
        signature = self._delivery_scan_signature()
        if report or signature != getattr(self, "_delivery_scan_signature_last", None):
            materialized = self._materialize_delivered()
            if materialized:
                report["materialized"] = materialized
            self._delivery_scan_signature_last = self._delivery_scan_signature()
        if not report:
            return None
        return report

    def _delivery_scan_signature(self):
        """Cheap ``(economy mtime, substrate mtime)`` fingerprint, or ``None``."""

        economy = self._economy_module()
        wbs = getattr(self, "wbs", None)
        if economy is None or wbs is None:
            return None
        try:
            return (
                economy.state_path(self.config.world_home).stat().st_mtime_ns,
                wbs.substrate_path(self.config.world_home).stat().st_mtime_ns,
            )
        except Exception:  # noqa: BLE001
            return None

    def _settle_environment(self) -> Optional[dict[str, Any]]:
        """Feed the declared room light to the Body -- at most once per tick.

        The flag is deny-by-default (``CHIYO_WORLD_ROOM_ENVIRONMENT_ENABLED``), so
        an existing instance keeps its behaviour until it is enabled explicitly.
        ``None`` input (no declared light / lamp off / unknown power) writes
        nothing at all, and a gap longer than the bound is reported as
        ``environment_gap_unknown`` instead of fabricating a whole night.
        """

        room = self._room_environment_module()
        if room is None:
            return None
        environment = getattr(self.config, "environment", None)
        if not room.feature_enabled(environment):
            return None
        if self.body_state is None:
            return None
        try:
            walk_since = self._in_flight_movement()
        except Exception:  # noqa: BLE001 - unreadable ledger: make no posture claim
            return {"advanced": False, "reason": "movement_state_unreadable", "wrote": False}
        now = self._moment()
        try:
            light = room.light_for_home(self.config.world_home, now=now)
        except Exception:  # noqa: BLE001 - unknown light is not an input
            return {"advanced": False, "reason": "light_unavailable", "wrote": False}
        outcome = self.bcon.advance_environment(
            runtime_state=self.body_state,
            light=light,
            at=now.isoformat(),
            # the World's own measured pose (substrate owner), never inferred from
            # the body cursor
            posture=self._measured_pose(),
            # a walk in flight does not stop the light sample; it only bounds the
            # posture interval at its departure
            movement_in_flight_since=walk_since,
        )
        if outcome.get("wrote") and outcome.get("state") is not None:
            self.body_state = outcome["state"]
            if outcome.get("transition") is not None:
                self.body_store.append_transition(outcome["transition"])
            self.body_store.save_runtime(self.body_state)
            self.counters["writes"] += 1
        outcome["light"] = {
            "status": light.get("status"),
            "power": light.get("power"),
            "band": light.get("band"),
            "since": light.get("since"),
            "reason_code": light.get("reason_code"),
        }
        return outcome

    # ------------------------------------------------------------------
    # reporting
    # ------------------------------------------------------------------
    def health(self) -> dict[str, Any]:
        """Liveness only.  Says nothing about whether the runtime is usable."""

        checks = {
            "process_alive": True,
            "home_readable": os.access(self.config.world_home, os.R_OK),
            "schemas_valid": self.body_state is not None and self.world_state is not None,
            "authority_lock_held": bool(self.lease and getattr(self.lease, "held", False)),
        }
        return {
            "kind": "world_body_health",
            "status": "OK" if all(checks.values()) else "DEGRADED",
            "checks": checks,
        }

    def readiness(self) -> dict[str, Any]:
        """Usability only.  Every clause in ticket section 27 must hold."""

        try:
            chain = self.execution_ledger.chain_status()
            ledger_ok = bool(chain.get("valid"))
        except Exception:
            ledger_ok = False
        binding_ok = True
        try:
            store = self.wbs.SubstrateStore(self.wbs.substrate_path(self.config.world_home))
            substrate = store.load_with_migration()[0]
            if substrate is not None and self.world_state is not None:
                binding_ok = bool(self.wbs.verify_binding(substrate, self.world_state).get("bound"))
        except Exception:
            binding_ok = False

        checks = {
            "world_loaded": self.world_state is not None,
            "ledger_verified": ledger_ok,
            "body_loaded": self.body_state is not None,
            "binding_valid": binding_ok,
            "reconciliation_complete": bool(
                self.startup_report.get("steps") and self.ready
            ),
            "canonical_mode_active": self.config.canonical
            and self.bread.resolve_mode(dict(os.environ)) == self.bread.MODE_CANONICAL,
            "single_authority_confirmed": bool(self.lease and getattr(self.lease, "held", False)),
        }
        return {
            "kind": "world_body_readiness",
            "status": "READY" if all(checks.values()) else "NOT_READY",
            "checks": checks,
        }

    def status(self) -> dict[str, Any]:
        return {
            "kind": KIND_SERVICE,
            "schema_version": SCHEMA_SERVICE,
            "service": "chiyo-world-body.service",
            "ready": self.ready,
            "started_at": self.started_at,
            "shutdown_at": self.shutdown_at,
            "counters": dict(self.counters),
            "authority": self.lease.status() if self.lease else None,
            "config": self.config.as_dict(),
            "body_bootstrapped": self.body_bootstrapped,
        }

    def world_summary(self) -> dict[str, Any]:
        world = self.world_state or {}
        return {
            "world_id": world.get("world_id"),
            "revision": world.get("revision"),
            "schema_version": world.get("schema_version"),
            "location": world.get("location"),
        }

    def body_summary(self) -> dict[str, Any]:
        """Qualitative only: no raw somatic value ever leaves here."""

        state = self.body_state or {}
        import body_signal as bs
        import body_signal as bsi

        signal = bsi.produce_signals(state, occurred_at=self._moment()) if state else None
        return {
            "body_id": state.get("body_id"),
            "version": state.get("version"),
            "recovery": {
                "state": (state.get("recovery") or {}).get("state"),
                "mode": (state.get("recovery") or {}).get("mode"),
                "trend": (state.get("recovery") or {}).get("trend"),
            },
            "signal": signal,
            "raw_values_exposed": False,
        }

    def ledger_summary(self) -> dict[str, Any]:
        chain = self.execution_ledger.chain_status()
        return {
            "valid": chain.get("valid"),
            "anchor": chain.get("anchor"),
            "legacy_entries": chain.get("legacy_entries"),
            "v2_entries": chain.get("v2_entries"),
            "verified_v2_entries": chain.get("verified_v2_entries"),
            "total_entries": (chain.get("legacy_entries") or 0) + (chain.get("v2_entries") or 0),
            "errors": chain.get("errors"),
        }

    # ------------------------------------------------------------------
    # control surface
    # ------------------------------------------------------------------
    READ_ONLY_COMMANDS = ("status", "health", "readiness", "world", "body", "ledger", "ping")
    #: `act` is not a debug back door: it submits an ActionProposal to the same
    #: Resolver that owns every World mutation.  There is deliberately no command
    #: that writes body or world state directly.  (Ticket sections 30, 31.)
    MUTATING_COMMANDS = ("reconcile", "act", "apply_result")
    ALLOWED_COMMANDS = READ_ONLY_COMMANDS + MUTATING_COMMANDS

    def handle(self, command: str, argument: Any = None) -> dict[str, Any]:
        name = str(command or "").strip().lower()
        if name not in self.ALLOWED_COMMANDS:
            # Ticket section 31: no debug mutation, no test back door.
            return {
                "ok": False,
                "error": "unknown_or_forbidden_command",
                "command": name,
                "allowed": list(self.ALLOWED_COMMANDS),
                "mutating_commands": list(self.MUTATING_COMMANDS),
                "debug_mutation_enabled": False,
            }
        if name == "ping":
            return {"ok": True, "command": name, "pong": True}
        if name == "reconcile":
            report = self.reconcile_now(persist=True)
            return {"ok": True, "command": name, "result": {
                "candidates": report["candidate_count"],
                "healed": len(report["healed"]),
                "unresolved": len(report["unresolved"]),
                "wrote_anything": report["wrote_anything"],
            }}
        if name == "act":
            if not isinstance(argument, Mapping):
                return {"ok": False, "error": "act_requires_a_proposal_object"}
            return {"ok": True, "command": name, "result": self.act(argument)}
        if name == "apply_result":
            if not isinstance(argument, Mapping):
                return {"ok": False, "error": "apply_result_requires_a_result_object"}
            return {"ok": True, "command": name, "result": self.apply_result(argument)}
        payload = {
            "status": self.status,
            "health": self.health,
            "readiness": self.readiness,
            "world": self.world_summary,
            "body": self.body_summary,
            "ledger": self.ledger_summary,
        }[name]()
        return {"ok": True, "command": name, "result": payload}

    def _bind_socket(self, path: pathlib.Path, mode: int = 0o600) -> socket.socket:
        """Bind one unix socket, owned by the service and readable only by root."""

        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            info = path.lstat()
        except FileNotFoundError:
            info = None
        if info is not None:
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid():
                raise StartupRefused("SOCKET_PATH_REFUSED", "socket path is not an owned socket")
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.3)
                try:
                    probe.connect(str(path))
                except OSError as exc:
                    if exc.errno != errno.ECONNREFUSED:
                        raise StartupRefused("SOCKET_PATH_REFUSED", "socket liveness could not be established") from None
                else:
                    raise StartupRefused("SOCKET_PATH_REFUSED", "socket is already live")
            current = path.lstat()
            if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                raise StartupRefused("SOCKET_PATH_REFUSED", "socket path changed during startup")
            path.unlink()
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(path))
        os.chmod(path, mode)
        server.listen(8)
        server.setblocking(False)
        if not hasattr(self, '_bound_sockets'):
            self._bound_sockets = {}
        info = path.lstat()
        self._bound_sockets[path] = (info.st_dev, info.st_ino)
        return server

    def _control_socket(self) -> socket.socket:
        return self._bind_socket(self.socket_path)

    def _gateway_socket(self) -> socket.socket:
        return self._bind_socket(self.gateway_socket_path)

    def _serve_connection(
        self, connection: socket.socket, *, surface: str = "admin"
    ) -> None:
        try:
            connection.settimeout(self.request_timeout_seconds)
            data = connection.recv(65536)
            if not data:
                return
            try:
                request = json.loads(data.decode("utf-8").strip().splitlines()[0])
            except (ValueError, IndexError):
                request = {"command": data.decode("utf-8", "replace").strip()}
            if surface == "gateway":
                payload = self.gateway_surface.handle(
                    request.get("command"), request.get("argument")
                )
            else:
                payload = self.handle(request.get("command"), request.get("argument"))
        except Exception as error:  # never take the service down for a control call
            payload = {"ok": False, "error": type(error).__name__, "detail": str(error)[:400]}
        try:
            connection.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode())
        except OSError:
            pass
        finally:
            connection.close()

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def shutdown(self) -> dict[str, Any]:
        """Stop accepting work, flush, release authority, exit cleanly."""

        self.ready = False
        self._stop_requested = True
        self.shutdown_at = _iso(self._moment())
        reports: dict[str, Any] = {"shutdown_at": self.shutdown_at}
        if self.body_state is not None:
            self.body_store.save_runtime(self.body_state)
            reports["runtime_persisted"] = True
        for path in (self.socket_path, self.gateway_socket_path):
            try:
                info = path.lstat()
                if stat.S_ISSOCK(info.st_mode) and getattr(self, '_bound_sockets', {}).get(path) == (info.st_dev, info.st_ino):
                    path.unlink()
            except OSError:
                pass
        if self.lease is not None:
            reports["authority"] = self.lease.release()
        reports["lease_released"] = True
        return reports

    def serve(self, *, max_seconds: Optional[float] = None, stop_after_ticks: Optional[int] = None) -> dict[str, Any]:
        """Event-driven loop with a bounded wake.  Never a 1s spin."""

        stopping = {"flag": False}

        def _stop(signum, _frame):
            LOG.info("signal %s received: shutting down", signum)
            stopping["flag"] = True

        previous = {}
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                previous[sig] = signal.signal(sig, _stop)
            except (ValueError, OSError):
                pass

        admin_server = self._bind_socket(self.socket_path)
        gateway_server = self._bind_socket(self.gateway_socket_path)
        selector = selectors.DefaultSelector()
        selector.register(admin_server, selectors.EVENT_READ, "admin")
        selector.register(gateway_server, selectors.EVENT_READ, "gateway")
        started = time.monotonic()
        ticks = 0
        try:
            while not stopping["flag"] and not self._stop_requested:
                if max_seconds is not None and time.monotonic() - started >= max_seconds:
                    break
                for key, _events in selector.select(timeout=self.tick_seconds):
                    try:
                        connection, _ = key.fileobj.accept()
                    except OSError:
                        continue
                    if key.data == "gateway":
                        # M14A: transport peer-credential check (fail-closed).
                        # OS peer authorization != business intent
                        # authorization; both layers are enforced.
                        try:
                            import struct as _struct
                            _cr = getattr(socket, "SO_PEERCRED", None)
                            if _cr is not None:
                                _raw = connection.getsockopt(
                                    socket.SOL_SOCKET, _cr, _struct.calcsize("3i")
                                )
                                _pid, _uid, _gid = _struct.unpack("3i", _raw)
                                if not gateway_peer_allowed(_uid):
                                    LOG.warning(
                                        "gateway surface refused non-root peer uid=%s pid=%s",
                                        _uid, _pid,
                                    )
                                    try:
                                        connection.sendall(
                                            (json.dumps({
                                                "ok": False,
                                                "error": "unauthorized_peer",
                                                "detail": "gateway surface requires uid 0",
                                            }) + "\n").encode()
                                        )
                                    except OSError:
                                        pass
                                    connection.close()
                                    continue
                        except OSError:
                            pass
                    self._serve_connection(connection, surface=key.data)
                work = self.tick()
                ticks += 1
                if work:
                    LOG.info("tick work: %s", work)
                if stop_after_ticks is not None and ticks >= stop_after_ticks:
                    break
        finally:
            selector.close()
            admin_server.close()
            gateway_server.close()
            for sig, handler in previous.items():
                try:
                    signal.signal(sig, handler)
                except (ValueError, OSError):
                    pass
        return {"ticks": ticks, "counters": dict(self.counters)}


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------



def _cli(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="world_body_service")
    parser.add_argument(
        "command", choices=("run", "check", "status"), nargs="?", default="run"
    )
    parser.add_argument("--runtime-root", default=None)
    parser.add_argument("--world-home", default=None)
    parser.add_argument("--tick-seconds", type=float, default=DEFAULT_TICK_SECONDS)
    parser.add_argument("--max-seconds", type=float, default=None)
    args = parser.parse_args(argv)

    from world_body_config import ConfigError, load_config

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )

    try:
        config = load_config(
            runtime_root=args.runtime_root, world_home=args.world_home
        )
    except ConfigError as error:
        print(json.dumps({"ok": False, "condition": "CONFIG_INVALID", "detail": str(error)}))
        return 2

    if args.command == "status":
        # Read-only: report files and the lease without taking it.
        from world_body_authority import read_marker, lock_is_held

        payload = {
            "ok": True,
            "command": "status",
            "config": config.as_dict(),
            "authority_lock_held": lock_is_held(config.run_dir),
            "authority_marker": read_marker(config.run_dir),
            "world_state_present": config.world_state_path.exists(),
            "ledger_present": config.ledger_path.exists(),
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
        return 0

    service = WorldBodyService(config, tick_seconds=args.tick_seconds)
    try:
        report = service.startup()
    except StartupRefused as error:
        print(json.dumps({"ok": False, "condition": error.condition, "detail": error.detail}))
        return 3

    if args.command == "check":
        service.shutdown()
        print(json.dumps({"ok": True, "command": "check", "startup": report}, ensure_ascii=False))
        return 0

    try:
        loop = service.serve(max_seconds=args.max_seconds)
    finally:
        service.shutdown()
    print(json.dumps({"ok": True, "command": "run", "loop": loop}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())

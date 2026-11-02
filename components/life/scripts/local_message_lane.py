#!/usr/bin/env python3
"""The one local-message lane: ordinary messages get answered while a step is in flight.

Why this module exists
----------------------
The resident runs one step at a time: ``runner_tick`` maintains, runs the contact pass, polls
the feeds and then runs the decision window -- all on the same thread.  While a real Action is
crossing the submission boundary, that thread is *inside* the step, so an ordinary message that
arrives in that window waits for the next tick.  That is correct for the Life writers and wrong
for the person standing at the door.

What this lane is
-----------------
* **one** background thread that consumes **ordinary** local messages only
  (``SocialContactPort.tick_ordinary_messages``): claim -> bounded responder -> archive the
  reply on the arrival.  Nothing else;
* it never runs a cycle, never touches AG-1/AG-2/AR-0/LR-2/LR-4, never creates or pauses an
  Activity, never takes the lease and never handles an invitation or an end -- those stay in the
  serial resident step, so there is still exactly **one** Life writer;
* it holds no lock across model I/O: the claim is the durable, atomic claim of the inbox owner
  (write lock + claim token), so a message is answered once even if the main tick and this lane
  look at it in the same moment;
* it stops politely: ``stop()`` signals, joins, and reports a thread that did not finish
  instead of killing it, clearing a claim or dropping an action.

What it is not
--------------
Not a chat service, not a second executor, not a delivery channel.  The reply it archives is
local only; nobody is told the user has read it.
"""
from __future__ import annotations

import threading
from typing import Any, Mapping, Optional

#: The consumer name this lane claims with.  It is deliberately *not* the main contact pass's
#: name: a different consumer cannot replay another consumer's claim as its own work.
LANE_CONSUMER = "local_message_lane"

DEFAULT_POLL_SECONDS = 0.2
MAX_POLL_SECONDS = 5.0


class LocalMessageLane:
    """One bounded worker thread over one ``SocialContactPort``."""

    def __init__(self, port: Any, *, poll_seconds: float = DEFAULT_POLL_SECONDS,
                 max_per_pass: int = 1, consumer: str = LANE_CONSUMER,
                 name: str = "local-message-lane") -> None:
        self.port = port
        self.poll_seconds = min(max(0.01, float(poll_seconds)), MAX_POLL_SECONDS)
        self.max_per_pass = max(1, int(max_per_pass))
        self.consumer = str(consumer or LANE_CONSUMER)
        self.name = str(name or "local-message-lane")
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.last_error: Optional[str] = None
        self.last_pass: Optional[dict[str, Any]] = None
        self.counters: dict[str, int] = {"passes": 0, "processed": 0, "responded": 0,
                                         "failed": 0, "no_reply": 0}

    # -- what this thing is ----------------------------------------------------------------

    def available(self) -> bool:
        check = getattr(self.port, "tick_ordinary_messages", None)
        return callable(check)

    def is_running(self) -> bool:
        thread = self._thread
        return bool(thread is not None and thread.is_alive())

    def describe(self) -> dict[str, Any]:
        return {
            "owner": "LocalMessageLane",
            "consumer": self.consumer,
            "poll_seconds": self.poll_seconds,
            "max_per_pass": self.max_per_pass,
            "running": self.is_running(),
            "handles": ["LOCAL_MESSAGE"],
            "writes_life_state": False,
            "creates_activities": False,
            "pauses_anything": False,
            "takes_lease": False,
            "runs_cycle": False,
            "delivered_to_user": False,
        }

    def stats(self) -> dict[str, Any]:
        """Counts only -- never a message body and never a reply's words."""

        out = dict(self.counters)
        out.update({"running": self.is_running(), "consumer": self.consumer,
                    "last_error": self.last_error,
                    "last_reason_code": (self.last_pass or {}).get("reason_code") or None})
        return out

    # -- lifecycle -------------------------------------------------------------------------

    def start(self) -> dict[str, Any]:
        """Start the lane once.  Idempotent: a second call reports the existing thread."""

        if not self.available():
            return {"ok": False, "started": False,
                    "reason_code": "LOCAL_MESSAGE_LANE_PORT_UNAVAILABLE",
                    "detail": "the contact port cannot answer ordinary messages"}
        with self._lock:
            if self.is_running():
                return {"ok": True, "started": False, "already_running": True,
                        "consumer": self.consumer}
            self._stop.clear()
            thread = threading.Thread(target=self._loop, name=self.name, daemon=True)
            self._thread = thread
            thread.start()
        return {"ok": True, "started": True, "consumer": self.consumer,
                "poll_seconds": self.poll_seconds}

    def stop(self, *, timeout: float = 5.0) -> dict[str, Any]:
        """Signal and join.  A lane that does not finish is *reported*, never killed.

        The lane's own step is a bounded model call plus two small owner writes; the resident's
        stop already happens after the loop ends, and a kill here would leave a claim behind.
        """

        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=max(0.0, float(timeout)))
        still_alive = bool(thread is not None and thread.is_alive())
        out = self.stats()
        out.update({"ok": not still_alive, "stopped": not still_alive,
                    "still_alive": still_alive})
        return out

    # -- the loop --------------------------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._one_pass()
            except Exception as exc:  # a lane failure is recorded, never silently swallowed
                self.last_error = "%s: %s" % (type(exc).__name__, str(exc)[:200])
                self.counters["failed"] += 1
            self._stop.wait(self.poll_seconds)

    def _one_pass(self) -> None:
        tick = self.port.tick_ordinary_messages(limit=self.max_per_pass,
                                                consumer=self.consumer)
        row = dict(tick or {})
        rows = list(row.get("processed") or [])
        self.counters["passes"] += 1
        self.counters["processed"] += len(rows)
        self.counters["responded"] += sum(1 for item in rows if item.get("responded"))
        self.counters["no_reply"] += sum(
            1 for item in rows
            if not item.get("responded") and item.get("outcome") == "OBSERVED")
        # A pass that could not read the ledger at all is a failure, not "nothing to do".
        if row.get("ok") is False:
            self.counters["failed"] += 1
            self.last_error = str(row.get("reason_code") or "LOCAL_MESSAGE_LANE_PASS_FAILED")
        self.last_pass = {"ok": row.get("ok"), "reason_code": row.get("reason_code") or None,
                          "processed": len(rows),
                          "responded": sum(1 for item in rows if item.get("responded")),
                          "at_pass": self.counters["passes"]}


__all__ = [
    "DEFAULT_POLL_SECONDS",
    "LANE_CONSUMER",
    "LocalMessageLane",
    "MAX_POLL_SECONDS",
]

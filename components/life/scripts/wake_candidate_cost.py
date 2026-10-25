"""One wake, one owner read: a round-scoped read memo for the artifact port.

Why this module exists
----------------------
One decision round reads the *same* ManagedArtifact owner several times at the same
instant: the zero-model material check asks for the continuable work, the reading
situation asks again, ``CandidateMaterializer.build_candidate_set`` refreshes the open
candidates and then materialises the same source once more, and the selected work is
read a second time after the material check.  Each of those reads is a real socket
round trip to the Life Supply owner, and they are all about the same point-in-time
view of the same artifact owner.

This module makes those reads reusable **inside one round only**:

* the cache lives on the transport (``LifeArtifactPort._call``), keyed by the exact
  request (op + artifact id + page args).  Two different artifacts, two different
  pages or two different source revisions can never collapse into each other;
* a round starts with an empty memo and ends by dropping it.  Nothing survives into
  the next wake, so no old permission, window, World or Body fact is frozen;
* any command op (create / commit / archive / resolve) invalidates the memo before
  and after it runs, so a write can never be followed by a stale read;
* **the explicit re-checks drop it first**: ``refresh=True`` on the material
  situation, the post-response ``_recheck_reading_sources``, and the pre-adoption
  freshness check (``_pause_gate_hook`` -> ``validate_candidate``) all invalidate
  before they read, so a change that landed during a model wait is observed at the
  point that decides whether anything may be written;
* failures are never cached: an owner refusal or transport error is raised again on
  the next call instead of being remembered as an answer.

It is a read cache, not a second store: it holds copies of owner answers for the
duration of one round and writes nothing anywhere.

What this deliberately does **not** promise
-------------------------------------------
A wake is a point-in-time read, not an immutable snapshot with its own revision
protocol.  Between the first cached answer and the Decision, an operator can archive,
tombstone or commit; the round may still *mention* the object it read a moment earlier
(its candidate ref and revision -- the candidate prompt carries no body text at all).
What the memo must never do is answer a deliberate re-check, and it does not: the
pre-adoption check and the reading pre-commit re-check both drop it and ask the owner.
"""
from __future__ import annotations

import copy
import json
from typing import Any, Mapping, Optional

from life_artifact_port import LifeArtifactPort

#: Only immutable/point-in-time artifact reads are cacheable.  Operation-effect
#: lookups (``lookup_operation`` / ``read_effect_receipt``) are deliberately left
#: out: resolving an UNKNOWN effect must keep asking the owner, even inside one
#: round.  Command ops always pass through and invalidate the memo.
CACHEABLE_READ_OPS = frozenset({
    "list_artifacts", "read_artifact", "read_head", "list_versions", "read_version",
})


def payload_bytes(value: Any) -> int:
    """The UTF-8 JSON size of one owner answer (0 when it is not JSON-shaped)."""

    try:
        return len(json.dumps(value, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError):
        return 0


class RoundCachedArtifactPort(LifeArtifactPort):
    """A ``LifeArtifactPort`` that remembers read answers for one round.

    It is a real ``LifeArtifactPort`` subclass on purpose: ``PrivateDailyItems`` and
    the private-material readers check ``isinstance(port, LifeArtifactPort)``, and a
    duck-typed proxy would silently break them.  All methods are inherited; only the
    transport hook ``_call`` is overridden.
    """

    def __init__(self, port: LifeArtifactPort, *, enabled: bool = True) -> None:
        super().__init__(port.socket_path, port.subject_id, timeout=port.timeout)
        self._inner = port
        self._enabled = bool(enabled)
        self._active = False
        self._token: Optional[str] = None
        self._cache: dict[str, Any] = {}
        self.cache_hits = 0
        self.cache_misses = 0
        self.bytes_avoided = 0
        self.write_invalidations = 0
        self.rounds_opened = 0

    # -- round boundary -------------------------------------------------------------

    @property
    def inner(self) -> LifeArtifactPort:
        return self._inner

    def begin_round(self, token: Any = None) -> None:
        """Start a new round: always an empty memo, even for a repeated token.

        "Same timestamp" is not a reason to reuse a previous round's answer; the
        owner may have moved between two wakes that happen to share a second.
        """

        self._cache.clear()
        self._active = bool(self._enabled)
        self._token = None if token is None else str(token)
        self.rounds_opened += 1

    def end_round(self) -> None:
        self._cache.clear()
        self._active = False
        self._token = None

    def invalidate(self) -> None:
        """Drop every remembered answer (used after a wait, before a re-check)."""

        self._cache.clear()

    def stats(self) -> dict[str, Any]:
        return {
            "enabled": self._enabled,
            "active": self._active,
            "rounds_opened": self.rounds_opened,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "bytes_avoided_estimate": self.bytes_avoided,
            "write_invalidations": self.write_invalidations,
            "inner_call_count": int(getattr(self._inner, "call_count", 0)),
        }

    # -- transport hook -------------------------------------------------------------

    def _call(self, request: Mapping[str, Any]) -> dict[str, Any]:
        op = request.get("op")
        is_read = op in CACHEABLE_READ_OPS
        if not is_read:
            # A command may move the owner: invalidate on both sides of it, whatever
            # the answer (APPLIED / NO_CHANGE / UNKNOWN all mean "re-read later").
            self._cache.clear()
            self.write_invalidations += 1
            self.call_count += 1
            try:
                return self._inner._call(request)
            finally:
                self._cache.clear()
        if not self._active:
            self.call_count += 1
            return self._inner._call(request)

        key = json.dumps(dict(request), ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"))
        if key in self._cache:
            self.cache_hits += 1
            value = self._cache[key]
            self.bytes_avoided += payload_bytes(value)
            return copy.deepcopy(value)

        self.cache_misses += 1
        self.call_count += 1
        result = self._inner._call(request)
        self._cache[key] = copy.deepcopy(result)
        return result


def maybe_round_cached_port(port: Any, *, enabled: bool = True) -> Any:
    """Wrap a real ``LifeArtifactPort``; leave duck-typed fixtures untouched.

    Tests and some callers pass their own narrow port objects.  Wrapping those would
    change their type and is unnecessary; only the production port is wrapped.
    """

    if port is None or isinstance(port, RoundCachedArtifactPort):
        return port
    if isinstance(port, LifeArtifactPort):
        return RoundCachedArtifactPort(port, enabled=enabled)
    return port


__all__ = [
    "CACHEABLE_READ_OPS",
    "RoundCachedArtifactPort",
    "maybe_round_cached_port",
    "payload_bytes",
]

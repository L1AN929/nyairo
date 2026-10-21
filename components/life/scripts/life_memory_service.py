"""v0.4 A: the stable, read-only Memory product seam Life and the phone both call.

What this module is
-------------------
One narrow, stable object -- :class:`LifeMemoryService` -- so that the practice workspace (B) and
the Life main chain (C) never grow their own SQLite grep.  It answers four questions and nothing
else:

* :meth:`LifeMemoryService.status` -- is this binding bound, and can it actually recall?
* :meth:`LifeMemoryService.list_experiences` -- which governance-cleared experiences exist,
  optionally matching a query;
* :meth:`LifeMemoryService.read_experience` -- one experience, bounded and governed;
* :meth:`LifeMemoryService.context` -- a bounded, relevant body for a prompt.

Why the envelope is shaped this way
-----------------------------------
Every call returns ``{"ok", "status", "data"}`` plus a named ``reason``/``detail`` when it is not
``READY``.  A failure **never** looks like an empty library: on any non-``READY`` status the
experience payload is ``None``, not ``[]``, so "cannot read" and "there is nothing" can never be
confused by a caller, a report, or a later model context.

Read-only means read-only
-------------------------
``EvidenceStore.__init__`` creates the parent directory, runs migrations and ``chmod``s the file,
and ``MemoryControlLedger.initialize()`` creates tables.  None of that may happen on a GET, so
every read path here goes through ``LifeExperiencePort.readiness/search/read``, which only open
``mode=ro`` handles.  The only writer stays M0's own ``append``.

Binding scope
-------------
:func:`memory_service_from_env` reads exactly the three names the shipped resident reads
(``NYA02_MEMORY_DB`` / ``NYA02_MEMORY_CONTROLS_DB`` / ``NYA02_MEMORY_SUBJECT`` and their
``CHIYO_LIFE_*`` fallbacks).  It never scans for unknown databases, and an owner is always the
binding's own -- never inferred from the Supply/Activity domain.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Mapping, Optional

_LIFE_SCRIPTS = Path(__file__).resolve().parent
_COMPONENTS = _LIFE_SCRIPTS.parents[1]
_NATIVE = _COMPONENTS / "native"
for _path in (str(_LIFE_SCRIPTS), str(_NATIVE / "memory_runtime_v1"), str(_NATIVE),
              str(_NATIVE / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

#: Status vocabulary.  ``UNBOUND`` is a legitimate, deliberate state, not a fault -- but it is
#: also never "she has no past", so it can never answer ``ok``.
STATUS_READY = "READY"
STATUS_UNBOUND = "UNBOUND"
STATUS_BLOCKED = "BLOCKED"
STATUS_UNAVAILABLE = "UNAVAILABLE"
STATUS_NOT_FOUND = "NOT_FOUND"

#: Same participation wording as the phone preflight, so "not bound" cannot be read as "empty".
PARTICIPATION = {
    STATUS_READY: "BOUND_READY_READONLY_VERIFIED",
    STATUS_UNBOUND: "NOT_BOUND_OPTIONAL_DEGRADATION",
    STATUS_BLOCKED: "BOUND_BUT_UNUSABLE",
    STATUS_UNAVAILABLE: "UNRESOLVED",
    STATUS_NOT_FOUND: "NOT_RECALLABLE",
}

#: ``autonomous_life_resident``'s own names (``:74-76``), taken from the module when importable.
MEMORY_ENV_FALLBACK = {
    "database": ("NYA02_MEMORY_DB", "CHIYO_LIFE_MEMORY_DB"),
    "controls": ("NYA02_MEMORY_CONTROLS_DB", "CHIYO_LIFE_MEMORY_CONTROLS_DB"),
    "owner": ("NYA02_MEMORY_SUBJECT", "NYA02_SUBJECT"),
}
MEMORY_DEFAULT_OWNER = "chiyo"

DEFAULT_LIST_LIMIT = 20
DEFAULT_CONTEXT_LIMIT = 5
#: A list row may be longer than a prompt line, but neither is a document dump.
LIST_MAX_CHARS = 1200
CONTEXT_MAX_CHARS = 600

#: Payload keys of one experience row.  Stable on purpose: B and C read these names.
EXPERIENCE_FIELDS = (
    "experience_id",
    "event_id",
    "occurred_at",
    "text",
    "source_ref",
    "activity_ref",
    "action_ref",
    "artifact_ref",
    "version_ref",
    "owner_result",
    "outcome_kind",
)


# ---------------------------------------------------------------------------------------------
# environment binding (same names as the shipped resident)
# ---------------------------------------------------------------------------------------------


def memory_env_names() -> dict[str, tuple[str, ...]]:
    """The exact names the shipped resident reads.  No fifth spelling is ever invented here."""

    names = {key: tuple(value) for key, value in MEMORY_ENV_FALLBACK.items()}
    try:
        resident = _resident_module()
    except Exception:  # not importable in this host: the frozen names still hold
        return names
    for key, attribute in (("database", "MEMORY_DB_ENV"), ("controls", "MEMORY_CONTROLS_ENV"),
                           ("owner", "MEMORY_SUBJECT_ENV")):
        value = getattr(resident, attribute, None)
        if isinstance(value, str) and value.strip():
            names[key] = (value.strip(),)
        elif isinstance(value, (tuple, list)) and value:
            names[key] = tuple(str(item) for item in value if str(item).strip())
    return names


def _resident_module():
    import importlib

    if str(_LIFE_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_LIFE_SCRIPTS))
    return importlib.import_module("autonomous_life_resident")


def _port_module():
    import importlib

    return importlib.import_module("life_experience_port")


def _first_bound(environment: Mapping[str, str], names: tuple[str, ...]
                 ) -> tuple[Optional[str], Optional[str]]:
    for name in names:
        value = str(environment.get(name) or "").strip()
        if value:
            return value, name
    return None, None


# ---------------------------------------------------------------------------------------------
# the seam
# ---------------------------------------------------------------------------------------------


class LifeMemoryService:
    """Read-only Memory product seam.  ``port=None`` is an explicit UNBOUND, never a store."""

    def __init__(
        self,
        port: Any = None,
        *,
        owner: Optional[str] = None,
        database: Optional[str] = None,
        controls_database: Optional[str] = None,
        bind_error: Optional[str] = None,
        source: str = "explicit",
    ) -> None:
        self.port = port
        self.owner = str(owner) if owner else (
            str(getattr(port, "subject", "") or "") or None)
        self.database = str(database) if database else (
            str(getattr(port, "evidence_db", "") or "") or None)
        self.controls_database = str(controls_database) if controls_database else (
            str(getattr(port, "controls_db", "") or "") or None)
        self.bind_error = str(bind_error) if bind_error else None
        self.source = str(source)

    # -- shape ---------------------------------------------------------------------------

    @staticmethod
    def _envelope(status: str, data: dict[str, Any], reason: Optional[str] = None,
                  detail: Optional[str] = None) -> dict[str, Any]:
        payload = dict(data)
        payload.setdefault("binding_status", status)
        payload.setdefault("participation", PARTICIPATION.get(status, "UNRESOLVED"))
        payload["reason"] = reason
        payload["detail"] = detail
        return {"ok": status == STATUS_READY, "status": status,
                "reason": reason, "detail": detail, "data": payload}

    def _binding_state(self) -> dict[str, Any]:
        """READY / UNBOUND / BLOCKED / UNAVAILABLE for the current binding, read-only."""

        data: dict[str, Any] = {
            "binding_status": STATUS_UNBOUND,
            "recall_ready": False,
            "owner": self.owner,
            "database": self.database,
            "controls_database": self.controls_database,
            "controls_defaulted": bool(
                self.database and self.controls_database == self.database),
            "source": self.source,
            "integrity": None,
            "evidence_table": None,
            "governance_healthy": None,
            "controls": None,
        }
        if self.port is None:
            if self.bind_error:
                data.update({"binding_status": STATUS_UNAVAILABLE, "reason": "MEMORY_BINDING_UNRESOLVED"})
                return {"status": STATUS_UNAVAILABLE, "reason": "MEMORY_BINDING_UNRESOLVED",
                        "detail": self.bind_error, "data": data}
            if self.database:
                # Paths were recorded but no port could be built: that is not "nothing bound".
                data.update({"binding_status": STATUS_BLOCKED,
                             "path_configured": True})
                return {"status": STATUS_BLOCKED, "reason": "MEMORY_PORT_UNAVAILABLE",
                        "detail": "a Memory database is recorded but no boundary port was built",
                        "data": data}
            data.update({"reason": "MEMORY_OPTIONAL_UNBOUND",
                         "note": "this binding records no Memory database at all"})
            return {"status": STATUS_UNBOUND, "reason": "MEMORY_OPTIONAL_UNBOUND",
                    "detail": None, "data": data}
        try:
            readiness = dict(self.port.readiness())
        except Exception as exc:
            data.update({"binding_status": STATUS_UNAVAILABLE,
                         "reason": "MEMORY_READINESS_PROBE_FAILED"})
            return {"status": STATUS_UNAVAILABLE, "reason": "MEMORY_READINESS_PROBE_FAILED",
                    "detail": "%s: %s" % (type(exc).__name__, exc), "data": data}
        status = str(readiness.get("binding_status") or STATUS_UNAVAILABLE)
        if status not in (STATUS_READY, STATUS_BLOCKED, STATUS_UNAVAILABLE):
            status = STATUS_UNAVAILABLE
        for key in ("recall_ready", "integrity", "evidence_table", "governance_healthy",
                    "controls", "controls_defaulted", "database", "controls_database"):
            if key in readiness:
                data[key] = readiness[key]
        data["owner"] = readiness.get("owner") or self.owner
        return {"status": status, "reason": readiness.get("reason"),
                "detail": readiness.get("detail"), "data": data}

    # -- public API ----------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """``data`` carries ``binding_status`` / ``recall_ready`` / ``owner`` and the reason."""

        state = self._binding_state()
        return self._envelope(state["status"], state["data"], state["reason"], state["detail"])

    def list_experiences(self, *, query: str = "", limit: int = DEFAULT_LIST_LIMIT,
                         since: Optional[str] = None) -> dict[str, Any]:
        """Governance-cleared experiences for this owner, newest relevant first."""

        state = self._binding_state()
        data = dict(state["data"])
        data.update({"experiences": None, "count": None, "query": str(query or ""),
                     "limit": int(limit), "since": since})
        if state["status"] != STATUS_READY or self.port is None:
            return self._envelope(state["status"], data, state["reason"], state["detail"])
        try:
            records = self.port.search(query=query, limit=int(limit), since=since)
        except Exception as exc:
            return self._read_failure(data, exc)
        rows = []
        for record in records:
            item = self._row(record, LIST_MAX_CHARS)
            if item is not None:
                rows.append(item)
        data.update({"experiences": rows, "count": len(rows)})
        return self._envelope(STATUS_READY, data)

    def read_experience(self, experience_id: str) -> dict[str, Any]:
        """One experience.  Absent, another owner's and governed-away are one answer: NOT_FOUND."""

        key = str(experience_id or "").strip()
        state = self._binding_state()
        data = dict(state["data"])
        data.update({"experience": None, "experience_id": key or None})
        if not key:
            return self._envelope(STATUS_BLOCKED, data, "MEMORY_EXPERIENCE_ID_REQUIRED",
                                  "an experience id is required")
        if state["status"] != STATUS_READY or self.port is None:
            return self._envelope(state["status"], data, state["reason"], state["detail"])
        try:
            record = self.port.read(key)
        except Exception as exc:
            return self._read_failure(data, exc)
        if record is None:
            return self._envelope(
                STATUS_NOT_FOUND, data, "MEMORY_EXPERIENCE_NOT_RECALLABLE",
                "no recallable experience with this id for this owner: absent, another owner's, "
                "corrected away or deleted")
        data["experience"] = self._row(record, LIST_MAX_CHARS)
        return self._envelope(STATUS_READY, data)

    def context(self, *, query: str = "", limit: int = DEFAULT_CONTEXT_LIMIT) -> dict[str, Any]:
        """A bounded, actually relevant body for a prompt (never the newest five by default)."""

        state = self._binding_state()
        data = dict(state["data"])
        data.update({"experiences": None, "count": None, "query": str(query or ""),
                     "limit": int(limit), "bounded_chars": CONTEXT_MAX_CHARS})
        if state["status"] != STATUS_READY or self.port is None:
            return self._envelope(state["status"], data, state["reason"], state["detail"])
        try:
            records = self.port.search(query=query, limit=int(limit))
        except Exception as exc:
            return self._read_failure(data, exc)
        rows = []
        for record in records:
            item = self._row(record, CONTEXT_MAX_CHARS)
            if item is not None:
                rows.append(item)
        data.update({"experiences": rows, "count": len(rows)})
        return self._envelope(STATUS_READY, data)

    def describe(self) -> dict[str, Any]:
        """A content-free summary: safe for a report or a log line."""

        state = self._binding_state()
        return {"kind": "life_memory_service", "source": self.source,
                "status": state["status"], "reason": state["reason"],
                "owner": state["data"].get("owner"),
                "database": state["data"].get("database"),
                "controls_database": state["data"].get("controls_database"),
                "recall_ready": bool(state["data"].get("recall_ready"))}

    # -- internals -----------------------------------------------------------------------

    def _read_failure(self, data: dict[str, Any], exc: BaseException) -> dict[str, Any]:
        """A failed read is named.  It never becomes ``experiences: []``."""

        code = str(getattr(exc, "code", "") or "")
        detail = getattr(exc, "detail", None)
        if code:
            status = STATUS_UNAVAILABLE if code == "MEMORY_GOVERNANCE_PROBE_FAILED" else STATUS_BLOCKED
            return self._envelope(status, data, code, detail)
        return self._envelope(STATUS_UNAVAILABLE, data, "MEMORY_READ_FAILED",
                              "%s: %s" % (type(exc).__name__, exc))

    @staticmethod
    def _row(record: Any, max_chars: int) -> Optional[dict[str, Any]]:
        try:
            raw = record.to_dict()
        except Exception:
            return None
        row = {field: raw.get(field) for field in EXPERIENCE_FIELDS}
        text = str(row.get("text") or "")
        if max_chars and len(text) > max_chars:
            row["text"] = text[:max_chars].rstrip() + "…"
        return row


def memory_service_from_env(environment: Optional[Mapping[str, str]] = None) -> LifeMemoryService:
    """The service for one instance's own recorded binding -- or an explicit UNBOUND.

    Only the three resident names are consulted; no database is discovered, guessed or created.
    An unimportable boundary module yields a named UNBOUND/UNAVAILABLE service rather than an
    exception, so a caller can report "cannot read" honestly.
    """

    env = {str(key): str(value) for key, value in dict(environment or {}).items()
           if value is not None and str(value).strip()}
    names = memory_env_names()
    database, _database_env = _first_bound(env, names["database"])
    controls, _controls_env = _first_bound(env, names["controls"])
    owner, _owner_env = _first_bound(env, names["owner"])
    if not database:
        return LifeMemoryService(None, owner=None, database=None, controls_database=None,
                                 source="env")
    owner = owner or MEMORY_DEFAULT_OWNER
    try:
        module = _port_module()
        port = module.LifeExperiencePort(database, controls_db=controls or None, subject=owner)
    except Exception as exc:
        return LifeMemoryService(None, owner=owner, database=database,
                                 controls_database=controls or database,
                                 bind_error="%s: %s" % (type(exc).__name__, exc),
                                 source="env")
    return LifeMemoryService(port, owner=owner, database=database,
                             controls_database=controls or database, source="env")


__all__ = [
    "CONTEXT_MAX_CHARS",
    "DEFAULT_CONTEXT_LIMIT",
    "DEFAULT_LIST_LIMIT",
    "EXPERIENCE_FIELDS",
    "LifeMemoryService",
    "LIST_MAX_CHARS",
    "MEMORY_DEFAULT_OWNER",
    "MEMORY_ENV_FALLBACK",
    "PARTICIPATION",
    "STATUS_BLOCKED",
    "STATUS_NOT_FOUND",
    "STATUS_READY",
    "STATUS_UNBOUND",
    "STATUS_UNAVAILABLE",
    "memory_env_names",
    "memory_service_from_env",
]

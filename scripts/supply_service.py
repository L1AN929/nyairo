"""Run the bundled single-database Life Supply owner with explicit env settings.

A *recovered* instance is bound here, not inside the owner: when the instance environment names
a recovery directory, the launcher builds the activation gate (the same ``RecoveryGate`` the
Memory service uses) and hands it to the Supply owner.  Without that binding a restored artifact
database would answer reads as if nothing had ever been deleted.  The gate is duck-typed by the
store, so ``components/supply`` keeps no dependency on Life policy modules; this launcher is the
place both halves meet.  An activation directory that names no owner/authority is a configured
gate that cannot be built, and the service refuses to start instead of running unguarded.
"""
import dataclasses
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "components" / "supply"))
from lifesupply.service.life_supply import ServiceSettings, run_service  # noqa: E402

RECOVERY_DIR_ENV = ("NYA02_F154_RECOVERY_DIR", "CHIYO_F154_RECOVERY_DIR")
OWNER_ENV = ("NYA02_MEMORY_SUBJECT", "CHIYO_MEMORY_SUBJECT")
AUTHORITY_ENV = ("NYA02_MEMORY_AUTHORITY_DB", "CHIYO_MEMORY_AUTHORITY_DB")
MIN_SEQ_ENV = ("NYA02_MEMORY_AUTHORITY_MIN_SEQ", "CHIYO_MEMORY_AUTHORITY_MIN_SEQ")


def _first(env, names):
    for name in names:
        value = str(env.get(name) or "").strip()
        if value:
            return value
    return ""


def recovery_gate_from_env(env=None):
    """The activation gate this instance's environment names, or ``None`` for an ordinary run."""
    source = dict(os.environ if env is None else env)
    if not _first(source, RECOVERY_DIR_ENV):
        return None
    owner = _first(source, OWNER_ENV)
    authority = _first(source, AUTHORITY_ENV)
    if not owner or not authority:
        raise SystemExit(
            "RECOVERY_GATE_CONFIG_INCOMPLETE: a recovery directory is configured but "
            "NYA02_MEMORY_SUBJECT / NYA02_MEMORY_AUTHORITY_DB are not set")
    for extra in (ROOT / "components" / "life" / "scripts",
                  ROOT / "components" / "native" / "memory_runtime_v1"):
        if str(extra) not in sys.path:
            sys.path.insert(0, str(extra))
    from forget_request import RecoveryGate  # noqa: E402  (Life policy, only when recovered)
    from life_experience_port import life_conversation_id  # noqa: E402

    raw_min = _first(source, MIN_SEQ_ENV)
    try:
        minimum = int(raw_min) if raw_min else None
    except ValueError:
        minimum = None
    return RecoveryGate.from_env(source, owner=owner,
                                 conversation=life_conversation_id(owner),
                                 authority_db=authority, minimum_seq=minimum)


def main(env=None) -> int:
    gate = recovery_gate_from_env(env)
    settings = ServiceSettings.from_env(env)
    if gate is not None:
        settings = dataclasses.replace(settings, recovery_gate=gate)
    return run_service(settings)


if __name__ == "__main__":
    sys.exit(main())

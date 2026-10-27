#!/usr/bin/env python3
"""Explicitly enable the optional virtual lived-needs profile on ONE instance.

This is the only entry that creates ``data/body/lived_needs_profile.json``.
Without that file the Body runtime stays ``body.runtime.v1``: no hunger, no
thirst, no toilet need, no drift, and the old CONSUME non-nutrition rule is
untouched.

The initial values are explicit operator parameters -- never inferred from a
past run, a seed workload or a clock.  Existing levels are preserved when the
profile is re-written; only the rates/profile reference are refreshed.

Example (WSL, from the repo root):

    /tmp/rc104-venv/bin/python -B scripts/body_lived_needs_enable.py \
        --instance /tmp/nyairo-body-demo --hunger 0.72 --thirst 0.78 --toilet 0.66
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "supply")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import body_lived_needs as bln  # noqa: E402
import body_runtime as br  # noqa: E402

DEFAULT_NOTE = "explicit virtual lived-needs profile; not a human measurement"


def enable_profile(
    body_dir: Path | str,
    *,
    hunger: float,
    thirst: float,
    toilet: float,
    fatigue: float | None = None,
    note: str | None = None,
) -> dict:
    """Write the explicit profile and, when a runtime already exists, upgrade it to v2.

    A brand-new instance has no ``runtime.json`` yet: the World/Body service
    creates ``body.runtime.v2`` from this profile on its first start.  Nothing
    here invents a past history; the values are exactly the caller's.
    """

    target = Path(body_dir)
    profile = bln.build_profile(hunger=hunger, thirst=thirst, toilet=toilet,
                                body_id=br.BODY_ID, note=note or DEFAULT_NOTE)
    bln.save_profile(target, profile)

    store = br.BodyStore(target)
    action = "profile_only"
    state = store.load_runtime()
    final_state = None
    if state is not None:
        needs = bln.initial_state(profile)
        if state.get("lived_needs") is not None:
            # Keep the real current levels; refresh only rates/profile ref.
            needs["levels"] = dict(state["lived_needs"]["levels"])
            action = "profile_refreshed"
        else:
            action = "runtime_upgraded_v1_to_v2"
        upgraded = br.upgrade_to_v2(state, needs)
        if fatigue is not None and state.get("schema_version") == br.SCHEMA_RUNTIME:
            upgraded["somatic"]["fatigue"] = br._clamp(fatigue, "fatigue")
            upgraded = br.validate_runtime(upgraded)
        store.save_runtime(upgraded)
        final_state = upgraded
    return {
        "ok": True,
        "action": action,
        "body_dir": str(target),
        "profile_ref": bln.initial_state(profile)["profile_ref"],
        "needs": (bln.bands(final_state) if final_state is not None else
                  {name: bln.band_for(profile["needs"][name]["initial"])
                   for name in bln.NEEDS}),
        "initial": {"hunger": float(hunger), "thirst": float(thirst),
                    "toilet": float(toilet)},
        "note": "virtual needs only; no human measurement, no timer, no automatic action",
    }


def _body_dir(args: argparse.Namespace) -> Path:
    if args.body_dir:
        return Path(args.body_dir)
    if not args.instance:
        raise SystemExit("either --instance or --body-dir is required")
    # A nyairo_day root carries its own phone instance file; the one-day file is
    # the nested Life instance.  Accept both so the documented root path works.
    try:
        from components.phone import service as phone_service

        data = phone_service.load_instance(Path(args.instance))
        world_home = str(data.get("world_home") or "")
        if world_home:
            return Path(world_home) / "data" / "body"
    except Exception:
        pass
    import one_day_life as odl

    data = odl.read_instance(Path(args.instance))
    return Path(str(data["world_home"])) / "data" / "body"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance", help="a prepared nyairo instance root")
    parser.add_argument("--body-dir", help="explicit data/body directory")
    parser.add_argument("--hunger", type=float, required=True)
    parser.add_argument("--thirst", type=float, required=True)
    parser.add_argument("--toilet", type=float, required=True)
    parser.add_argument("--fatigue", type=float, default=None,
                        help="optional explicit starting fatigue (somatic v1 state only)")
    parser.add_argument("--note", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result = enable_profile(_body_dir(args), hunger=args.hunger, thirst=args.thirst,
                            toilet=args.toilet, fatigue=args.fatigue, note=args.note)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=1)
          if args.json else "LIVED_NEEDS_ENABLED %s needs=%s"
          % (result["action"], json.dumps(result["needs"], ensure_ascii=False)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

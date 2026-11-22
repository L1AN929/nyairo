import sys, json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "tests"), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge"),
              str(ROOT / "components" / "supply")):
    if entry not in sys.path:
        sys.path.insert(0, entry)
import importlib.util

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

demo_mod = load("v07_probe_demo", ROOT / "scripts" / "v07_daily_life_demo.py")
d = demo_mod.Demo()
try:
    d.build()
    print("assembled keys:", sorted(d.fixture.assembled.keys()))
    print("research:", d.fixture.assembled.get("research"))
    print("claims:", d.fixture.assembled.get("claims"))
    print("contacts:", (d.fixture.assembled.get("contacts") or {}).get("shared_practice"))
    print("cycle claims:", d.cycle.claims_service is not None)
    print("cycle social:", d.cycle.social_contacts is not None)
    print("seam avail:", d.cycle.research.available())
    print("seam status:", d.cycle.research.availability())
    print("privacy items:", d.data.get("private_items_artifact_id"))
    print("env s03:", d.env.get("CHIYO_S03_CLAIMS_DB"))
    import one_day_life as odl
    pdi = odl.private_items_adapter(d.instance)
    print("pdi:", pdi is not None)
    print("artifact list count:", len(d.port.list_artifacts()))
finally:
    d.close()

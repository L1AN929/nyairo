import sys
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

spec = importlib.util.spec_from_file_location("v07_probe_demo", ROOT / "scripts" / "v07_daily_life_demo.py")
mod = importlib.util.module_from_spec(spec)
sys.modules["v07_probe_demo"] = mod
spec.loader.exec_module(mod)
demo = mod._run_stage8_chain(keep_demo=True)
try:
    print("STAGE8-KEEP-OK")
    print("STAGE9-EXIT", mod._run_stage9_chain(demo=demo))
finally:
    demo.close()

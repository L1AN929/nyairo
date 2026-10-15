"""NyAiro Life Heartbeat Daemon
Periodically runs in the background to:
1. Realize due travel arrivals (automatically transitioning in-progress walks to arrived).
2. Continue multi-leg transit (e.g. convenience_store -> nearby_street -> home).
3. Provide autonomous life wandering rhythm when idle (e.g. heading home in the evening, taking a walk during the day).
"""
import os, sys, time, datetime, json, logging
from pathlib import Path

REL = Path("/srv/nyairo-v0.2.0-rc2/app/releases/v0.2.0-rc2")
WORLD_HOME = Path("/srv/nyairo-v0.2.0-rc2/world")
PROFILE = Path("/srv/nyairo-v0.2.0-rc2/profile")
LOG_FILE = PROFILE / "logs" / "life_heartbeat.log"

for p in (str(REL), str(REL / "components/world/scripts"), str(REL / "components/native")):
    if p not in sys.path:
        sys.path.insert(0, p)

import world_travel_delivery as wtd
import world_living_skeleton as wls
from chiyo_bundle.world_move_tool import move_handler

logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

ENV = {
    "CHIYO_WORLD_LIVING_SKELETON_ENABLED": "1",
    "CHIYO_WORLD_TRAVEL_TIME_ENABLED": "1",
    "CHIYO_WORLD_HOME": str(WORLD_HOME)
}

LAST_AUTO_WANDER = time.time()
INTENT_TARGET_FILE = WORLD_HOME / "data/world/intended_target.json"

def get_intended_target() -> str:
    if INTENT_TARGET_FILE.exists():
        try:
            return json.loads(INTENT_TARGET_FILE.read_text(encoding="utf-8")).get("target", "")
        except Exception:
            return ""
    return ""

def set_intended_target(target: str):
    try:
        INTENT_TARGET_FILE.write_text(json.dumps({"target": target, "set_at": time.time()}), encoding="utf-8")
    except Exception:
        pass

def clear_intended_target():
    if INTENT_TARGET_FILE.exists():
        try:
            INTENT_TARGET_FILE.unlink()
        except Exception:
            pass

def heartbeat_step():
    global LAST_AUTO_WANDER
    # 1. Realize due travel
    wtd.realize_due_travel(hermes_home=str(WORLD_HOME), environment=ENV)
    tr = wtd.observe_travel(hermes_home=str(WORLD_HOME), environment=ENV)
    state = wls.read_move_state(hermes_home=str(WORLD_HOME), environment=ENV)
    curr = state.get("world_location_id") if state else "unknown"

    if tr.get("status") == "in_progress":
        dest = tr.get("destination_location_id")
        exp = datetime.datetime.fromisoformat(tr["expected_arrival_at"].replace("Z", "+00:00"))
        now = datetime.datetime.now(datetime.timezone.utc)
        rem = max(0, int((exp - now).total_seconds()))
        logging.debug(f"In-progress travel to {dest}, remaining {rem}s")
        return

    # If travel just finished or not travelling:
    intended = get_intended_target()
    if intended and curr != intended:
        # We were transiting! E.g. landed at nearby_street, now continue to intended target
        logging.info(f"Transit arrived at {curr}, now continuing to intended destination {intended}")
        res = move_handler({"location": intended}, environment=ENV)
        logging.info(f"Continued move to {intended}: {res}")
        clear_intended_target()
        return
    elif intended and curr == intended:
        clear_intended_target()

    # 2. Autonomous wandering if idle for long
    now_hour = datetime.datetime.now().hour  # Local server hour
    now_ts = time.time()
    
    # Evening curfew: if it is late (> 21:00 or < 7:00) and not home, head home!
    if (now_hour >= 21 or now_hour < 7) and curr != "home":
        if now_ts - LAST_AUTO_WANDER > 300:  # Check every 5 min
            LAST_AUTO_WANDER = now_ts
            logging.info(f"Evening curfew (hour={now_hour}): currently at {curr}, heading home autonomously.")
            set_intended_target("home")
            res = move_handler({"location": "home"}, environment=ENV)
            logging.info(f"Autonomous move home: {res}")

def run_loop():
    logging.info("NyAiro Life Heartbeat starting loop...")
    while True:
        try:
            heartbeat_step()
        except Exception as exc:
            logging.error(f"Heartbeat step exception: {exc}")
        time.sleep(15)

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "once":
        heartbeat_step()
        print("STEP_DONE")
    else:
        run_loop()
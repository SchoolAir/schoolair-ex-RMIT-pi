#!/usr/bin/env python3
import json, random, math, time, sys
from datetime import datetime, timezone
from pathlib import Path

STATE_FILE = Path(__file__).parent / "spike_state.json"

def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except:
        return {"spiking": False, "countdown": 0}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state))

t = time.time()
wave = math.sin(t / 3600)

def jitter(base, spread):
    return round(base + wave * spread + random.gauss(0, spread * 0.3), 2)

state = load_state()

# Update spike state
if state["spiking"]:
    state["countdown"] -= 1
    if state["countdown"] <= 0:
        state["spiking"] = False
elif random.random() < 0.05:
    state["spiking"] = True
    state["countdown"] = random.randint(5, 15)

save_state(state)

def v(base, spread, max_spike):
    val = jitter(base, spread)
    return round(val + max_spike, 2) if state["spiking"] else val

print(json.dumps({
    "mgs": {
        "co":      int(v(823,  40,  600)),
        "no2":     int(v(270,  20,  300)),
        "voc":     int(v(380,  30,  400)),
        "c2h5oh":  int(v(427,  25,  300)),
        "success": True,
        "timestamp": int(time.time())
    },
    "sen6x": {
        "co2":      int(v(499,  30,  600)),
        "pm10":     round(v(1.0, 0.4, 44), 1),
        "pm25":     round(v(2.2, 0.5, 23), 1),
        "pm40":     round(v(3.1, 0.5, 20), 1),
        "pm100":    round(v(3.5, 0.5, 20), 1),
        "temp":     round(v(26.0, 1.5, 10), 2),
        "humidity": round(v(25.2, 2.0, 10), 2),
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    }
}))
#!/usr/bin/env python3
import json, random, math, time
from datetime import datetime, timezone

t = time.time()
wave = math.sin(t / 3600)

def jitter(base, spread):
    return round(base + wave * spread + random.gauss(0, spread * 0.3), 2)

print(json.dumps({
    "mgs": {
        "co":      int(jitter(823, 40)),
        "no2":     int(jitter(270, 20)),
        "voc":     int(jitter(380, 30)),
        "c2h5oh":  int(jitter(427, 25)),
        "success": True,
        "timestamp": int(time.time())
    },
    "sen6x": {
        "co2":      int(jitter(499, 30)),
        "pm10":     round(jitter(1.0, 0.4), 1),
        "pm25":     round(jitter(2.2, 0.5), 1),
        "pm40":     round(jitter(3.1, 0.5), 1),
        "pm100":    round(jitter(3.5, 0.5), 1),
        "temp":     round(jitter(26.02, 1.5), 2),
        "humidity": round(jitter(25.21, 2.0), 2),
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    }
}))
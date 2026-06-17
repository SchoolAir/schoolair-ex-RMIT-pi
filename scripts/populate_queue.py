"""scripts/populate_queue.py

Dev utility: Populates the local SQLite queue with fake measurements
to test batch drain behaviour.

NOTE: Must run from in the /pi dir, or the db is created in scripts.

Usage:
    python scripts/populate_queue.py     # default 75 readings
    python scripts/populate_queue.py 200 # custom count
"""

import json
import sys
import random
import math
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import db.queue as queue

COUNT = int(sys.argv[1]) if len(sys.argv) > 1 else 75


def fake_reading(offset_seconds: int = 0) -> tuple[dict, str]:
    """Generate a plausible fake sensor reading."""
    t = time.time() - offset_seconds
    wave = math.sin(t / 3600)

    def jitter(base, spread):
        return round(base + wave * spread + random.gauss(0, spread * 0.3), 2)

    recorded_at = (
        datetime.now(timezone.utc) - timedelta(seconds=offset_seconds)
    ).isoformat()

    data = {
        "temp":     jitter(26.0, 1.5),
        "humidity": jitter(25.2, 2.0),
        "co2":      int(jitter(499, 30)),
        "pm10":     round(jitter(1.0, 0.4), 1),
        "pm25":     round(jitter(2.2, 0.5), 1),
        "pm40":     round(jitter(3.1, 0.5), 1),
        "pm100":    round(jitter(3.5, 0.5), 1),
        "voc":      int(jitter(380, 30)),
        "no2":      int(jitter(270, 20)),
        "raw": {
            "sen6x": {
                "co2":       int(jitter(499, 30)),
                "pm10":      round(jitter(1.0, 0.4), 1),
                "pm25":      round(jitter(2.2, 0.5), 1),
                "pm40":      round(jitter(3.1, 0.5), 1),
                "pm100":     round(jitter(3.5, 0.5), 1),
                "temp":      jitter(26.0, 1.5),
                "humidity":  jitter(25.2, 2.0),
                "timestamp": recorded_at,
            },
            "mgs": {
                "co":       int(jitter(823, 40)),
                "no2":      int(jitter(270, 20)),
                "voc":      int(jitter(380, 30)),
                "c2h5oh":   int(jitter(427, 25)),
                "success":  True,
                "timestamp": int(t),
            }
        }
    }

    return data, recorded_at


def main():
    queue.init()

    before = queue.count_pending()
    print(f"Queue before: {before} `pending`")
    print(f"  Adding {COUNT} fake readings...")

    # Insert oldest first so drain processes them in chronological order
    for i in range(COUNT, 0, -1):
        offset = i * 300  # 5 minutes apart, going back in time
        data, recorded_at = fake_reading(offset_seconds=offset)
        queue.enqueue(data, recorded_at)

    after = queue.count_pending()
    print(f"Queue after:  {after} `pending`")

if __name__ == "__main__":
    main()

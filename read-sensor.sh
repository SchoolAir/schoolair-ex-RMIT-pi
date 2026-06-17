#!/usr/bin/env bash
# Bridge: read the sen6x daemon's flat JSON and wrap it in {"sen6x": {...}}
# so it matches the shape expected by services/sensor.py:_flatten().
#
# The daemon writes:
#   {"timestamp": "...", "temp": 24.5, "humidity": 55.0, "co2": 460,
#    "pm10": 1.2, "pm25": 2.3, "pm40": 3.0, "pm100": 3.1}
#
# sensor.py expects:
#   {"<sensor_name>": {"temp": ..., "humidity": ..., ...}}
#
# The "timestamp" key is ignored by _flatten() (not in KNOWN_FIELDS) but is
# preserved in the "raw" field of the stored payload.

set -euo pipefail

SEN6X_JSON="/home/admin/i2c/sen6x/sen6x.json"

if [ ! -f "$SEN6X_JSON" ]; then
    echo "sen6x.json not found — is the sen6x.service running?" >&2
    exit 1
fi

python3 -c "
import json, sys
with open('$SEN6X_JSON') as f:
    data = json.load(f)
print(json.dumps({'sen6x': data}))
"

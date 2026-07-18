"""services/sensor.py

Reads sensor data by executing an external script and parsing its JSON output.
Returns the raw nested payload keyed by sensor name (e.g. {"sen6x": {...}}).
Multiple sensors work naturally: {"sen6x": {...}, "mgs": {...}}.
"""

import json
import subprocess
import os

# Sensirion SEN6x sentinel values per datasheet Table 22 (Read Measured Values SEN66):
#   uint16 metrics (PM*, CO2): sentinel = 0xFFFF = 65535
#   int16  metrics (temp, humidity, VOC, NOx): sentinel = 0x7FFF = 32767
# After the binary applies scaling factors these become:
#   CO2:      65535 ppm   (no scaling)  → caught by explicit sentinel check below
#   VOC/NOx:  3276.7      (÷10)         → caught by range check (valid 1–500)
#   humidity: 327.67 %RH (÷100)        → caught by range check (valid 0–100)
#   PM*:      6553.5 µg/m³ (÷10)       → caught by range check (valid 0–1000)
#   temp:     163.8 °C   (÷200)        → excluded: negating a legitimate negative
#                                          temp produces a valid-looking positive
_SENTINELS: dict[str, tuple[float, ...]] = {
    "co2": (65535.0,),   # 0xFFFF — also caught by range, but explicit for clarity
}

# Valid physical ranges per metric. Out-of-range values are flagged the same way
# as sentinels (negated). Temperature is excluded because it can legitimately be
# negative, and negating a below-zero reading produces a positive that looks valid.
_VALID_RANGES: dict[str, tuple[float, float]] = {
    "co2":      (0.0,   40000.0),
    "voc":      (1.0,   500.0),
    "nox":      (1.0,   500.0),
    "humidity": (0.0,   100.0),
    "pm10":     (0.0,   1000.0),
    "pm25":     (0.0,   1000.0),
    "pm40":     (0.0,   1000.0),
    "pm100":    (0.0,   1000.0),
}

SCRIPT = os.getenv("MOCK_SENSOR_SCRIPT", "./read-sensor.sh")

# Re-init is skipped in mock/dev mode (MOCK_SENSOR_SCRIPT set) because there
# is no real binary to call.  In production, sen6x_read --init recovers from
# sensor power-cycles, I2C lockups, and hardware swaps without a Pi reboot.
_REINIT_BIN = (
    ""
    if "MOCK_SENSOR_SCRIPT" in os.environ
    else os.getenv("SENSOR_REINIT_BIN", "/home/admin/i2c/sen6x/sen6x_read")
)
_REINIT_AFTER = 5  # consecutive failures before attempting re-init

_consecutive_failures = 0


def _try_reinit() -> None:
    print("[sensor] repeated failures — attempting re-init")
    try:
        r = subprocess.run(
            [_REINIT_BIN, "--init"],
            capture_output=True, text=True, timeout=90,
        )
        if r.returncode == 0:
            print("[sensor] re-init succeeded")
        else:
            print(f"[sensor] re-init failed (exit {r.returncode}): {r.stderr.strip()}")
    except subprocess.TimeoutExpired:
        print("[sensor] re-init timed out after 90 s")


def extract_metric(data: dict, metric: str) -> float | None:
    """Extract a named metric from a nested sensor reading.

    Searches all top-level sensor dicts (e.g. data["sen6x"]["co2"]).
    Returns the first numeric match, or None if not found in any sensor.
    """
    for sensor_data in data.values():
        if not isinstance(sensor_data, dict):
            continue
        val = sensor_data.get(metric)
        if isinstance(val, (int, float)):
            return float(val)
    return None


def sanitize_reading(data: dict, recorded_at: str = "") -> dict:
    """Return a sanitized copy of a sensor reading for transmission.

    Raw values are preserved in the buffer/SQLite so the breach-alert
    pipeline can still detect persistent sensor faults. This copy is what
    gets sent to the server: sentinel and out-of-range values are replaced
    with their negative (or -1 for zero) so the server knows the metric was
    captured but invalid for that sample.
    """
    result: dict = {}
    for sensor_name, sensor_data in data.items():
        if not isinstance(sensor_data, dict):
            result[sensor_name] = sensor_data
            continue
        clean = dict(sensor_data)
        for metric, val in sensor_data.items():
            if not isinstance(val, (int, float)):
                continue
            f = float(val)
            flagged = False
            sentinels = _SENTINELS.get(metric, ())
            if any(f == s for s in sentinels):
                flagged = True
            elif metric in _VALID_RANGES:
                lo, hi = _VALID_RANGES[metric]
                if not (lo <= f <= hi):
                    flagged = True
            if flagged:
                clean[metric] = -f if f != 0.0 else -1.0
                label = f" ({recorded_at})" if recorded_at else ""
                print(f"[sensor] flagged {metric}={f} → {clean[metric]}{label}")
        result[sensor_name] = clean
    return result


def read_sensor() -> dict:
    """Execute the sensor script and return its raw nested JSON payload.

    Raises RuntimeError if the script fails or output is not valid JSON.
    After _REINIT_AFTER consecutive failures, calls sen6x_read --init to
    recover from mid-run sensor resets (power-cycle, I2C lockup, swap).
    """
    global _consecutive_failures
    error: Exception | None = None

    try:
        result = subprocess.run(
            SCRIPT,
            shell=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            error = RuntimeError(f"Sensor script failed: {result.stderr.strip()}")
        else:
            data = json.loads(result.stdout)
            _consecutive_failures = 0
            return data
    except json.JSONDecodeError as e:
        error = RuntimeError(f"Sensor script returned invalid JSON: {e}")
    except subprocess.TimeoutExpired:
        error = RuntimeError("Sensor script timed out")

    _consecutive_failures += 1
    if _consecutive_failures == _REINIT_AFTER and _REINIT_BIN:
        _try_reinit()
    raise error

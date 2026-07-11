"""services/sensor.py

Reads sensor data by executing an external script and parsing its JSON output.
Returns the raw nested payload keyed by sensor name (e.g. {"sen6x": {...}}).
Multiple sensors work naturally: {"sen6x": {...}, "mgs": {...}}.
"""

import json
import subprocess
import os

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

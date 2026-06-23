"""services/sensor.py

Reads sensor data by executing an external script and parsing its JSON output.
Returns the raw nested payload keyed by sensor name (e.g. {"sen6x": {...}}).
Multiple sensors work naturally: {"sen6x": {...}, "mgs": {...}}.
"""

import json
import subprocess
import os

SCRIPT = os.getenv("MOCK_SENSOR_SCRIPT", "./read-sensor.sh")


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
    """
    try:
        result = subprocess.run(
            SCRIPT,
            shell=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Sensor script failed: {result.stderr.strip()}")

        return json.loads(result.stdout)

    except json.JSONDecodeError as e:
        raise RuntimeError(f"Sensor script returned invalid JSON: {e}")
    except subprocess.TimeoutExpired:
        raise RuntimeError("Sensor script timed out")

"""services/sensor.py

Reads sensor data by executing an external script and parsing its JSON output.
Flattens nested sensor output into a single dict of known fields,
preserving the raw nested payload for JSONB storage on the server.
"""

import json
import subprocess
import os

# NOTE: To use the mock sensor, set MOCK_SENSOR_SCRIPT in .env
SCRIPT = os.getenv("MOCK_SENSOR_SCRIPT", "./read-sensor.sh")

# Fields we extract from sensor output into dedicated DB columns
KNOWN_FIELDS = {"temp", "humidity", "pm10", "pm25", "pm40", "pm100", "co2", "voc", "no2"}


def _flatten(raw: dict) -> dict:
    """
    Flatten nested sensor output into known fields + raw payload.
    Iterates over each sensor's data (e.g. sen6x, mgs) and extracts
    numeric values for known fields. If two sensors report the same
    field, last one wins.

    Returns a flat dict ready to POST to the server:
    {
        "temp": 24.75, "humidity": 23.98, "co2": 460, ...
        "raw": { "sen6x": {...}, "mgs": {...} }  # full original output
    }
    """
    flat = {}

    for sensor_data in raw.values():
        if not isinstance(sensor_data, dict):
            continue
        for key, value in sensor_data.items():
            if key in KNOWN_FIELDS and isinstance(value, (int, float)):
                flat[key] = value

    flat["raw"] = raw 
    return flat


def read_sensor() -> dict:
    """
    Execute the sensor script, parse JSON output and return flattened payload.
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

        raw = json.loads(result.stdout)
        return _flatten(raw)

    except json.JSONDecodeError as e:
        raise RuntimeError(f"Sensor script returned invalid JSON: {e}")
    except subprocess.TimeoutExpired:
        raise RuntimeError("Sensor script timed out")
    
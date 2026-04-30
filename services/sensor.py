"""services/sensor.py

Reads sensor data by executing an external script and parsing its JSON output.
Maps raw output to the server's expected payload shape.
"""

import json
import subprocess
import os

# NOTE: To use the mock sensor, set MOCK_SENSOR_SCRIPT in .env
# e.g. MOCK_SENSOR_SCRIPT=./mock-sensor.sh
SCRIPT = os.getenv("MOCK_SENSOR_SCRIPT", "./read-sensor.sh")

# Sensor fields the server expects as dedicated columns.
# Anything else is passed through as-is (JSONB overflow on server side).
KNOWN_FIELDS = {"temp", "humidity", "pm10", "pm25", "pm40", "pm100", "co2", "voc", "no2"}


def read_sensor() -> dict:
    """
    Execute the sensor script and return parsed JSON output.
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
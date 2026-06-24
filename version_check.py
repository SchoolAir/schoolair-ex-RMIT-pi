#!/usr/bin/env python3
import sys
import subprocess
import json
import os
import datetime


VERSION = "2.0.0"
BINARY_PATH = "/home/admin/i2c/sen6x/sen6x_read"

FIELD_LABELS = {
    "temp":     ("Temperature",  "°C"),
    "humidity": ("Humidity",     "%"),
    "co2":      ("CO2",          " ppm"),
    "pm10":     ("PM1.0",        " µg/m³"),
    "pm25":     ("PM2.5",        " µg/m³"),
    "pm40":     ("PM4.0",        " µg/m³"),
    "pm100":    ("PM10",         " µg/m³"),
    "voc":      ("VOC Index",    ""),
    "nox":      ("NOx Index",    ""),
    "no2":      ("NO2",          " ppb"),
}


def get_last_modified():
    path = os.path.realpath(__file__)
    timestamp = os.path.getmtime(path)
    dt_object = datetime.datetime.fromtimestamp(timestamp)
    return dt_object.strftime("%Y-%m-%d %H:%M:%S")


def run_cli():
    if len(sys.argv) > 1:
        arg = sys.argv[1]

        if arg == "-v":
            print(f"SchoolAir v{VERSION}")
            print(f"Build Date (Last Edited): {get_last_modified()}")
            return

        if arg == "--status":
            if not os.path.exists(BINARY_PATH):
                print(f"Sensor binary not found: {BINARY_PATH}")
                return
            result = subprocess.run([BINARY_PATH], capture_output=True, text=True)
            if result.returncode != 0:
                print(f"Sensor read failed (exit {result.returncode}). Is sen6x.service running?")
                return
            try:
                outer = json.loads(result.stdout)
                data = outer.get("sen6x", outer)
                print(f"Latest reading: {data.get('measured_at', 'unknown')}")
                for key, (label, unit) in FIELD_LABELS.items():
                    if key in data:
                        print(f"  {label:<12} {data[key]}{unit}")
            except (json.JSONDecodeError, KeyError):
                print("Could not parse sensor output.")
            return

    print("Usage: schoolair [-v | --status]")


if __name__ == "__main__":
    run_cli()

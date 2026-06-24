#!/usr/bin/env bash
# One-shot sensor read.  Outputs {"sen6x": {..., "measured_at": "..."}} to
# stdout and exits with the sen6x_read exit code (0=ok, 1=I2C error,
# 2=not ready).  sensor.py treats any non-zero exit as a RuntimeError.
#
# Continuous measurement must already be running — sen6x.service starts it
# at boot via `sen6x_read --init`.
#
# For local development without hardware, override MOCK_SENSOR_SCRIPT:
#   MOCK_SENSOR_SCRIPT=./scripts/mock-sensor.sh python main.py
exec /home/admin/i2c/sen6x/sen6x_read

# schoolair-pi

Raspberry Pi client for the SchoolAir platform. Handles device registration,
sensor reading, data ingestion, and local alert detection.

---

## Overview

On startup the Pi validates its auth token with the central server. If valid,
a startup menu lets the operator continue normally or re-register the device.
If no token exists, the registration wizard runs automatically.

Once registered, the Pi runs two concurrent processes:
- **Ingest job** — reads the sensor on a fixed interval, queues the reading
  locally, then drains the queue to the server. Checks consecutive threshold
  breaches and fires confirmed alerts to the server.
- **Microdot server** — a lightweight local web server accessible on the
  school network for on-device status (to be expanded into a student-facing
  dashboard).

---

## File Structure

```
pi/
├── config/
│   ├── identity.json       # Device identity written on registration (device_id, asset_id, org_id, site_id)
│   └── criteria.json       # Latest alert criteria received from server, persisted across restarts
│
├── db/
│   └── queue.py            # SQLite queue — enqueue, drain, and retry failed measurements
│
├── jobs/
│   └── ingest.py           # Main ingest loop — sensor read, queue, drain, alert checking
│
├── scripts/
│   ├── mock-sensor.sh      # Runs the mock sensor script for development
│   └── preview.sh          # Preview sensor output in terminal
│
├── services/
│   └── sensor.py           # Executes sensor script, flattens nested output to known fields + raw payload
│
├── main.py                 # Entrypoint — runs ensure_registered() then starts ingest job and Microdot
├── setup.py                # Registration wizard — validates token, prompts for credentials and asset
├── pi-schema.sql           # Local SQLite schema for the measurements queue
├── queue.db                # SQLite database (auto-created on first run, gitignored)
└── requirements.txt        # Python dependencies
```

---

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

```bash
cp .env.example .env
```

Fill in `SERVER_URL` in `.env` before running. `AUTH_TOKEN` is written
automatically on successful registration.

---

## Running

```bash
# Real sensor
python main.py

# Mock sensor (development)
MOCK_SENSOR_SCRIPT=./scripts/mock-sensor.sh python main.py
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AUTH_TOKEN` | (empty) | Device authentication token for API authorization with the central server |
| `SERVER_URL` | `http://localhost:3000` | Central server URL where sensor data and alerts are sent |
| `INGEST_INTERVAL` | `10` | How often to read sensors and send data to central server (seconds) |
| `ALERT_COOLDOWN` | `3` | Cooldown period between alerts for the same metric (hours) |
| `ALERT_CONFIDENCE` | `3` | Number of consecutive threshold breaches required to confirm and send an alert |
| `MOCK_SENSOR_SCRIPT` | `./scripts/mock-sensor.sh` | Path to mock sensor script used for development and testing without real hardware |
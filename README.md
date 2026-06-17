# schoolair-pi

Raspberry Pi client for the SchoolAir platform. Handles device registration,
sensor reading, data ingestion, and local alert detection.

---

## Overview

On startup the Pi checks for a valid auth token. If the token is missing or
the server rejects it, the process exits and asks you to register
(`python -m setup`). If the server is simply unreachable, the Pi starts anyway
and queues readings locally until it can reconnect.

Once running, the Pi runs two concurrent processes:
- **Ingest job** — reads the sensor on a variable interval (active vs. idle
  window), queues each reading locally, then drains the queue to the server.
  Tracks consecutive threshold breaches and fires confirmed alerts.
- **Microdot server** — a lightweight local web server on the school network
  for on-device status (to be expanded into a student-facing dashboard).

Registration is a one-time manual step (`python -m setup`); the running
service never prompts.

---

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
```

Fill in `SERVER_URL` in `.env` before running. `AUTH_TOKEN` is written
automatically on successful registration.

Register the device once:

```bash
python -m setup
```

---

## Unit Testing

Navigate to the root of the pi repository directory and run the
following command.

```bash
# Make sure in virtual environment
source .venv/bin/activate
python -m pytest
```

---

## Running (development)

```bash
# Real sensor
python main.py

# Mock sensor
MOCK_SENSOR_SCRIPT=./scripts/mock-sensor.sh python main.py
```

---

## Deployment

The Pi runs under `systemd`. There are two unit files in `deploy/`:

- `schoolair.service` — production (system service, runs as the `pi` user).
- `schoolair-dev.service.example` — development (user service, runs as you).

### Development (user service)

Runs under your own account — no `sudo` required.

```bash
# Register once if you haven't already
python -m setup

# Copy the example, replace repo_path, install
cp deploy/schoolair-dev.service.example ~/.config/systemd/user/schoolair-dev.service
# edit the copy: replace repo_path with your path under $HOME

systemctl --user daemon-reload
systemctl --user start schoolair-dev
journalctl --user -u schoolair-dev -f
```

### Production (system service)

```bash
# Replace placeholders in the prod unit
sed -i 's|USERNAME|pi|g; s|REPO_PATH|/home/pi/schoolair|g' deploy/schoolair.service

sudo cp deploy/schoolair.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now schoolair.service
```

A missing or rejected token makes the service exit and retry every 10s;
once you register, the next restart picks up the token automatically.

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AUTH_TOKEN` | (empty) | Device auth token; written automatically on registration |
| `SERVER_URL` | (empty) | Central server URL for ingest and alerts |
| `PORT` | `3001` | Port for the local Microdot status server |
| `ALERT_CONFIDENCE` | `3` | Consecutive threshold breaches required to confirm an alert |
| `ALERT_COOLDOWN_HOURS` | `1` | Hours between alerts for the same metric |
| `QUEUE_ALERT_SKIP` | `50` | Queue depth above which alert checking is skipped during a drain |
| `MOCK_SENSOR_SCRIPT` | `./scripts/mock-sensor.sh` | Mock sensor script for development without hardware |

---

## Configuration

`config/settings.json` controls the ingest schedule. The Pi uses the active
interval inside the active window and the idle interval outside it.

```json
{
    "interval_active": 60,
    "interval_idle": 300,
    "active_window": { "start": "07:00", "end": "16:00" }
}
```

The active window has a hard limit of **9 hours** (e.g. 07:00–16:00). A longer
window fails validation at startup.

> [!WARNING]
> `interval_active` and `interval_idle` should not be modified — the server
> enforces a minimum ingest interval.

---

## File Structure

```
pi/
├── config/
│   ├── criteria.json       # Latest alert criteria from server, persisted across restarts
│   └── settings.json       # Ingest schedule (intervals + active window)
│
├── db/
│   └── queue.py            # SQLite queue — enqueue, drain, and retry failed measurements
│
├── deploy/
│   ├── schoolair.service           # Production systemd unit
│   └── schoolair-dev.service.example  # Development (user) systemd unit template
│
├── jobs/
│   └── ingest.py           # Main ingest loop — sensor read, queue, drain, alert checking
│
├── scripts/
│   ├── mock-sensor.sh      # Mock sensor for development
│   ├── populate_queue.sh   # Debug util to populate local sqlite
│   └── preview.sh          # Preview sensor output in terminal
│
├── services/
│   └── sensor.py           # Executes sensor script, flattens output to known fields + raw payload
│
├── tests/jobs              # pytest suite
│   ├── test_aggregate.py   
│   └── test_ingest.py           
│
├── main.py                 # Entrypoint — runs check_registration() then ingest job + Microdot
├── setup.py                # Registration tool (python -m setup) and headless startup gate
├── pi-schema.sql           # Local SQLite schema for the measurements queue
├── pyproject.toml          # Project + pytest config
└── requirements.txt        # Python dependencies
```

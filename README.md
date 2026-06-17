# schoolair-pi

Raspberry Pi client for the SchoolAir air-quality monitoring platform.
Handles device registration, sensor sampling, data ingestion, and local
alert detection with server-side dashboard visibility.

---

## What's in this repository

This codebase is the result of integrating two prior projects:

- **`install_files/`** — working field prototype: SEN6x C daemon, browser-based
  registration wizard, Node-RED telemetry viewer.
- **`pi-main/`** — improved but untested rewrite: async pipeline, SQLite queue,
  variable polling intervals, alert detection.

The integration uses `pi-main` as the structural base and ports the missing
production components from `install_files`. The result is a single deployable
stack with no Node-RED dependency.

### Added from `install_files`

| Component | Location | Notes |
|-----------|----------|-------|
| SEN6x C daemon | `i2c/sen6x/` | Reads sensor over I2C every 60 s; writes flat JSON to `/home/admin/i2c/sen6x/sen6x.json` |
| Registration wizard | `registration_wizard/` | Browser-based captive portal; writes `AUTH_TOKEN` to `.env` on completion |
| Launcher script | `registration_wizard/launcher.sh` | Decides at boot whether to start AP+wizard, wizard-only, or skip straight to telemetry |
| systemd services | `deploy/` | `sen6x.service`, `schoolair-wizard.service`, `schoolair-launcher.service` |
| Local dashboard | `static/` | Alpine.js + Pico CSS; live sensor readings via WebSocket |

### Key changes to `pi-main` during integration

| Change | Detail |
|--------|--------|
| Two-speed pipeline | Separate read loop (5/15 min) and drain loop (30 min/2 h) replace the original single loop |
| RAM-first storage | Readings live in an in-memory buffer; SQLite is only written when the buffer is full **and** the server is unreachable — protects SD card longevity |
| SIGUSR1 daemon trigger | Python sends `SIGUSR1` to the C daemon during alert verification to get a sub-minute fresh reading without touching I2C directly |
| Two-stage alert verification | A single threshold breach starts a background coroutine: T+10s/T+30s (Stage 1) rules out transient spikes; T+1m/T+2m (Stage 2) confirms persistence. Confirmed alerts send immediately; fleeting events are logged for retrospective dashboard visibility |
| In-memory alert buffer | Alerts mirror the measurement buffer — RAM-first, SQLite overflow only at capacity |
| Registration model | Ported org-token wizard from `install_files`; server validation errors are non-fatal (warns and continues) |
| Ingest endpoint | Uses `/node/aqc/v1/ingest` (old path); batch payload `{"measurements": [...]}` |
| WebSocket endpoint | `/ws/sensors` pushed to the local dashboard every 30 s |
| `read-sensor.sh` bridge | Wraps the daemon's flat JSON into `{"sen6x": {...}}` so `services/sensor.py` can flatten it uniformly |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  sen6x.service  (C daemon)                                  │
│  reads I2C sensor every 60 s  →  sen6x.json                 │
│  responds to SIGUSR1 for on-demand reads                    │
└──────────────────────────┬──────────────────────────────────┘
                           │ read-sensor.sh
┌──────────────────────────▼──────────────────────────────────┐
│  schoolair.service  (main.py)                               │
├─────────────────────────────┬───────────────────────────────┤
│  _read_loop  (5 / 15 min)   │  _drain_loop  (30 min / 2 h)  │
│                             │                               │
│  read sensor                │  POST _buffer + SQLite        │
│  append to _buffer          │    to server                  │
│  threshold breach?          │  update criteria.json         │
│    → start _verify_alert    │  drain _alert_buffer          │
├─────────────────────────────┴───────────────────────────────┤
│  _verify_alert  (background task, per breaching metric)     │
│                                                             │
│  Stage 1  T+10s, T+30s  (SIGUSR1 triggers fresh reads)      │
│    avg below threshold  →  patch buffer entry, stop         │
│    avg at / near        →  proceed to Stage 2               │
│                                                             │
│  Stage 2  T+1m, T+2m                                        │
│    avg below threshold  →  log as fleeting event            │
│    avg at / near        →  POST alert immediately           │
├─────────────────────────────────────────────────────────────┤
│  Microdot HTTP  (port 8080)                                 │
│  /             →  dashboard.html                            │
│  /ws/sensors   →  WebSocket live readings  (30 s push)      │
│  /re-register  →  starts schoolair-wizard.service           │
└─────────────────────────────────────────────────────────────┘
```

### Storage lifecycle

```
Sensor read
    │
    ▼
_buffer (RAM)  ──── normal drain ──► server  ──► cleared
    │
    │ buffer full AND server unreachable
    ▼
SQLite measurements_queue  ──── next drain ──► server  ──► deleted

Alert fired (Stage 2 persistent)
    │
    ├── POST immediately ──► success: done
    │
    └── POST failed / fleeting event
            │
            ▼
        _alert_buffer (RAM)  ──── next drain ──► server  ──► cleared
            │
            │ alert buffer full AND server unreachable
            ▼
        SQLite alerts_queue  ──── next drain ──► server  ──► deleted
```

---

## First-time deployment

Run once on the Pi after rsyncing this repo to `/home/admin/schoolair/`:

```bash
bash /home/admin/schoolair/deploy/deploy.sh
```

The script:
1. Installs Python dependencies (`httpx`, `python-dotenv`, `questionary`, `netifaces`)
2. Creates `.env` from `.env.example` if absent
3. Migrates any existing auth token from the old wizard format
4. **Compiles the SEN6x C daemon** from source (`i2c/sen6x/`) and places the binary at `/home/admin/i2c/sen6x/sen6x_d`
5. Installs all four systemd service files
6. Adds a sudoers rule so the telemetry service can start the wizard
7. Reloads systemd
8. Stops/disables legacy services
9. Enables `sen6x`, `schoolair`, and `schoolair-launcher`
10. Starts `sen6x.service` then `schoolair.service`

> **Prerequisite:** `gcc` must be available (`sudo apt install build-essential`).

---

## Registration

On first boot (no `AUTH_TOKEN` in `.env`), `schoolair-launcher.service` detects
the missing token and starts the registration wizard:

- **No Wi-Fi**: creates a `SchoolAir-Setup` access point and serves the wizard at `http://192.168.4.1`
- **Wi-Fi connected, no token**: serves the wizard directly on the LAN

The wizard walks through Wi-Fi credentials (if needed) and org-token entry, then
writes `AUTH_TOKEN` to `.env` and restarts `schoolair.service`.

To re-register a running device, visit `http://<pi-ip>:8080` and click
**Re-register**.

---

## Development

```bash
# Install dependencies (system Python, Bookworm)
pip3 install --break-system-packages -r requirements.txt

# Run with mock sensor
MOCK_SENSOR_SCRIPT=./scripts/mock-sensor.sh python3 main.py

# Run tests
python3 -m pytest
```

---

## Configuration

### `config/settings.json`

Controls the two-speed ingest schedule. The Pi uses active intervals inside the
active window and idle intervals outside it.

```json
{
    "interval_read_active":  300,
    "interval_read_idle":    900,
    "interval_drain_active": 1800,
    "interval_drain_idle":   7200,
    "active_window": { "start": "07:00", "end": "16:00" }
}
```

| Key | Default | Description |
|-----|---------|-------------|
| `interval_read_active` | `300` | Seconds between sensor reads during school hours |
| `interval_read_idle` | `900` | Seconds between sensor reads outside school hours |
| `interval_drain_active` | `1800` | Seconds between server drains during school hours |
| `interval_drain_idle` | `7200` | Seconds between server drains outside school hours |
| `active_window` | `07:00–16:00` | Daily window treated as active; max 9 hours |

### `config/criteria.json`

Alert thresholds per metric. Updated automatically from the server response
after each successful drain. Edit locally for testing or seed defaults.

```json
[
    { "metric": "co2",  "threshold": "800", "condition": "above", "severity": "warning"  },
    { "metric": "pm25", "threshold": "15",  "condition": "above", "severity": "critical" },
    { "metric": "temp", "threshold": "30",  "condition": "above", "severity": "warning"  }
]
```

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AUTH_TOKEN` | (empty) | Device auth token; written by the registration wizard |
| `SERVER_URL` | (empty) | Central server base URL (no trailing slash) |
| `INGEST_URL` | `{SERVER_URL}/node/aqc/v1/ingest` | Override ingest endpoint |
| `PORT` | `8080` | Port for the local Microdot web server |
| `MOCK_SENSOR_SCRIPT` | `./read-sensor.sh` | Sensor script; override for development |
| `DEVICE_NICKNAME` | (hostname) | Friendly name shown on the dashboard |
| `BUFFER_CAPACITY` | `500` | Max readings held in RAM before SQLite fallback |
| `ALERT_NEAR_PCT` | `10` | How close to threshold (%) counts as "near" in alert verification |
| `ALERT_COOLDOWN_HOURS` | `1` | Hours before the same metric can trigger another alert |
| `ALERT_BUFFER_CAPACITY` | `50` | Max alerts held in RAM before SQLite fallback |

---

## File structure

```
schoolair-pi/
├── i2c/
│   └── sen6x/
│       ├── sen6x_d.c                   # SEN6x daemon (SIGUSR1 on-demand sampling)
│       ├── sen63c_d.c                  # SEN63C-only variant
│       ├── Makefile.daemon             # Build: make -f Makefile.daemon
│       └── raspberry-pi-i2c-sen6x/    # Sensirion I2C driver library
│
├── config/
│   ├── criteria.json                   # Alert thresholds (updated from server)
│   └── settings.json                   # Ingest schedule
│
├── db/
│   └── queue.py                        # SQLite queues: measurements + alerts
│
├── deploy/
│   ├── deploy.sh                       # Full deployment script (run once on Pi)
│   ├── sen6x.service                   # systemd: C daemon
│   ├── schoolair.service               # systemd: main telemetry service
│   ├── schoolair-launcher.service      # systemd: boot-time registration gate
│   ├── schoolair-wizard.service        # systemd: browser registration wizard
│   └── schoolair-dev.service.example   # Development (user) service template
│
├── jobs/
│   ├── ingest.py                       # Read loop, drain loop, alert verification
│   └── aggregate.py                    # Hourly aggregation of old SQLite rows
│
├── registration_wizard/
│   ├── wizard.py                       # Microdot wizard app (AP + browser flow)
│   ├── launcher.sh                     # Decides: AP+wizard / wizard-only / skip
│   └── config.py                       # Wizard paths and service names
│
├── scripts/
│   ├── mock-sensor.sh                  # Mock sensor for development
│   ├── populate_queue.py               # Dev util: seed SQLite with fake readings
│   └── preview.sh                      # Print current sensor JSON to terminal
│
├── services/
│   ├── sensor.py                       # Runs sensor script, normalises field names
│   └── trigger.py                      # Sends SIGUSR1 to C daemon for on-demand reads
│
├── static/
│   ├── dashboard.html                  # Local dashboard (Alpine.js + Pico CSS)
│   ├── alpine.min.js
│   └── pico.min.css
│
├── tests/
│   └── jobs/
│       ├── test_ingest.py              # Interval and window logic
│       └── test_aggregate.py           # Aggregation bucketing
│
├── main.py                             # Entrypoint: registration gate + async loops
├── state.py                            # Shared latest-reading state for WebSocket
├── setup.py                            # Token validation at startup
├── migrate_token.py                    # Migrates auth token from old wizard format
├── read-sensor.sh                      # Wraps daemon JSON for sensor.py
├── pi-schema.sql                       # SQLite schema reference
├── requirements.txt                    # Python dependencies
├── pyproject.toml                      # Project + pytest config
└── .env.example                        # All supported environment variables
```

# schoolair-pi

Raspberry Pi gateway for the SchoolAir platform. Handles device registration,
sensor reading, data ingestion to the central server, and local alert detection.

---

## Quick start — fresh Pi

```bash
curl -sSL https://raw.githubusercontent.com/SchoolAir/schoolair-ex-RMIT-pi/main/schoolair_setup.sh | sudo bash
```

This clones the repo to `/home/admin/schoolair/`, installs dependencies, builds
the SEN6x binaries, configures a Wi-Fi hotspot for first-boot registration,
and enables all systemd services. Idempotent — safe to re-run.

To use a different admin username:

```bash
curl -sSL ... | sudo ADMIN_USER=pi bash
```

---

## How it works

### Service topology

Five systemd services run in sequence on every boot:

```
sen6x.service            One-shot — runs `sen6x_read --init` at boot. Starts
                         continuous measurement and waits for the first valid
                         sample (fast if sensor already running, up to 75 s
                         cold). schoolair.service depends on it completing.

schoolair-first-boot     One-shot — assigns a unique hostname to cloned images
  .service               (schoolair-YYMDD-XXXX). No-op if already set.

schoolair-launcher       One-shot — checks whether AUTH_TOKEN is present in
  .service               .env. If missing → starts the wizard. If present →
                         exits so schoolair.service can run.

schoolair-wizard         Browser-based registration + Wi-Fi setup (Microdot,
  .service               port 80). Started on demand by the launcher. Stops
                         itself after a successful registration.

schoolair.service        Main telemetry process (see below). Starts after the
                         launcher exits. Restarts automatically on failure.
```

### Telemetry process

`main.py` runs two concurrent coroutines:

- **Ingest loop** — reads the sensor on a clock-aligned schedule (5 min during
  the active window, 15 min outside it), buffers readings in RAM, and drains
  them to the server in batches. Checks each reading against alert criteria and
  runs a two-stage verification routine (T+10 s / T+30 s → T+1 m / T+2 m) when
  a threshold is breached. Alerts are only sent after the verification confirms
  the breach is persistent, not a spike.

- **Microdot server** — lightweight local web server (port 8080). Serves the
  real-time dashboard at `/` and pushes live sensor state over WebSocket at
  `/ws`. nginx on port 80 proxies to this once the device is registered.

### Buffer strategy

The ingest loop is RAM-first: up to 500 readings are held in memory. SQLite is
only written when the server is unreachable **and** the RAM buffer is full. On
reconnect the next drain sends both the SQLite backlog and the current RAM
buffer in a single batch. On clean shutdown (`systemctl stop`, `sudo reboot`)
both in-memory buffers (measurements and alerts) are flushed to SQLite before
exit.

### Drain timing

Drains are event-driven: the read loop triggers a drain whenever the time since
the last drain would exceed the configured interval if the current read were
skipped. This guarantees drains happen *within* the configured window (25–30 min
active, 105–120 min idle) without a separate polling timer.

---

## First boot — registration

On first boot the launcher detects no `AUTH_TOKEN` and starts the wizard.
Connect to the `SchoolAir_Setup` Wi-Fi hotspot or navigate to
`http://schoolair-register.local` on the same network. The wizard guides you
through:

1. Device registration with the SchoolAir server
2. Wi-Fi configuration (connects the Pi to the school network)

After registration the wizard writes `AUTH_TOKEN` to `.env`, restarts
`schoolair.service`, and exits. nginx activates to proxy port 80 → port 8080.

---

## Local development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env: set SERVER_URL and optionally DEVICE_NICKNAME
```

Run with the mock sensor (no Pi hardware needed):

```bash
MOCK_SENSOR_SCRIPT=./scripts/mock-sensor.sh python main.py
```

Run with the real sensor bridge (Pi only):

```bash
python main.py   # uses read-sensor.sh by default via MOCK_SENSOR_SCRIPT in .env
```

For one-off sensor checks:

```bash
bash scripts/preview.sh
```

---

## Configuration

`config/settings.json` controls the active window — the period during which the
Pi reads and drains at the higher cadence (school hours).

```json
{
    "active_window": { "start": "07:00", "end": "16:00" }
}
```

- Window boundaries must be on a 15-minute mark (`:00`, `:15`, `:30`, `:45`).
- Maximum window length is 9 hours.
- Read and drain intervals are fixed in source (`jobs/ingest.py`) and
  overridable via env vars for testing:

| Env var                  | Default   | Meaning                        |
|--------------------------|-----------|--------------------------------|
| `READ_INTERVAL_ACTIVE`   | `300` s   | 5 min — sensor read cadence inside the window  |
| `READ_INTERVAL_IDLE`     | `900` s   | 15 min — sensor read cadence outside the window |
| `DRAIN_INTERVAL_ACTIVE`  | `1800` s  | 30 min — max time between drains inside the window |
| `DRAIN_INTERVAL_IDLE`    | `7200` s  | 2 hr — max time between drains outside the window  |

---

## Environment variables

See `.env.example` for the full list. Key ones:

| Variable              | Default                   | Description |
|-----------------------|---------------------------|-------------|
| `AUTH_TOKEN`          | (empty)                   | Device auth token; written by the wizard on first boot |
| `SERVER_URL`          | `https://data.schoolair.org` | Central server base URL (no trailing slash) |
| `INGEST_URL`          | `{SERVER_URL}/node/aqc/v1/ingest` | Override the full ingest endpoint |
| `MOCK_SENSOR_SCRIPT`  | `./read-sensor.sh`        | Sensor script; replace with `./scripts/mock-sensor.sh` for development |
| `DEVICE_NICKNAME`     | (hostname)                | Display name shown on the dashboard |
| `PORT`                | `8080`                    | Local Microdot server port |
| `ALERT_NEAR_PCT`      | `10`                      | How close to a threshold counts as "near" during verification (%) |
| `ALERT_COOLDOWN_HOURS`| `1`                       | Minimum hours between alerts for the same metric |
| `BUFFER_CAPACITY`     | `500`                     | RAM buffer size (readings) before SQLite overflow |

---

## Testing

103 tests. All pass on a laptop except one hardware test that requires a real
SEN6x sensor connected via I2C on a Pi.

```bash
cd schoolair/        # repo root
pytest -m "not hardware"    # laptop-safe (102 tests)
pytest                      # full suite — Pi only
```

See `tests/README.md` for a breakdown by module.

---

## File structure

```
schoolair-pi/
├── config/
│   ├── criteria.json          Alert thresholds — written by the server on first
│   │                          successful drain; persisted across restarts
│   └── settings.json          Active window configuration
│
├── db/
│   └── queue.py               SQLite offline buffer — measurements and alerts
│
├── deploy/
│   ├── sen6x.service          SEN6x one-shot initialisation service
│   ├── schoolair-first-boot   One-shot hostname assignment
│   │   .service
│   ├── schoolair-launcher     Network check → start wizard or proceed
│   │   .service
│   ├── schoolair-wizard       Browser registration + Wi-Fi setup
│   │   .service
│   ├── schoolair.service      Main telemetry service
│   └── schoolair-dev          Development (user) service template
│       .service.example
│
├── i2c/sen6x/                 SEN6x C binaries source (sen6x_d, sen6x_read) and Makefile
│
├── jobs/
│   ├── aggregate.py           Hourly folding of old readings to reduce DB size
│   └── ingest.py              Read loop, drain loop, alert verification
│
├── registration_wizard/
│   ├── launcher.sh            Decides whether to start the wizard
│   └── wizard.py              Microdot browser portal (registration + Wi-Fi)
│
├── scripts/
│   ├── mock-sensor.sh         Fake sensor output for local development
│   ├── preview.sh             One-shot sensor read to terminal
│   └── populate_queue.py      Debug util — fill the local SQLite queue
│
├── services/
│   ├── sensor.py              Runs sensor script, parses nested JSON output
│   └── trigger.py             Retired — previously SIGUSR1 daemon trigger
│
├── static/
│   └── dashboard.html         Real-time Alpine.js dashboard (served at /)
│
├── tests/                     103 tests across 7 modules
│
├── main.py                    Entrypoint — ingest loop + Microdot server
├── setup.py                   Registration gate (check_registration) and
│                              recovery CLI (python -m setup)
├── read-sensor.sh             Invokes sen6x_read; sensor.py captures its stdout
├── schoolair_setup.sh         One-command fresh-Pi installer
├── state.py                   Shared in-memory sensor state for the dashboard
├── pyproject.toml             Project + pytest config
└── requirements.txt           Python dependencies
```

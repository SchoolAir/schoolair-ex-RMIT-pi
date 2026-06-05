"""jobs/ingest.py

Reads sensor data on a fixed interval, queues locally first, then drains
the queue to the server in a single batch. Alert checking runs over the
batch before sending when the queue is small (< QUEUE_ALERT_SKIP), where
readings are fresh enough for meaningful alerting.
"""

import asyncio
import json
import os
from datetime import datetime, timezone, timedelta, time
from pathlib import Path
import httpx
from dotenv import load_dotenv
from services.sensor import read_sensor
import db.queue as queue
import jobs.aggregate as aggregate

load_dotenv()

SERVER_URL          = os.getenv("SERVER_URL", "").rstrip("/")
ALERT_CONFIDENCE    = int(os.getenv("ALERT_CONFIDENCE", 3))       # consecutive breaches to confirm alert
ALERT_COOLDOWN_HRS  = float(os.getenv("ALERT_COOLDOWN_HOURS", 1)) # hours between alerts per metric
QUEUE_ALERT_SKIP    = int(os.getenv("QUEUE_ALERT_SKIP", 50))      # queue depth above which alert checking is skipped

CRITERIA_PATH = Path("config/criteria.json")
SETTINGS_PATH = Path("config/settings.json")

DEFAULT_SETTINGS = {
    "interval_active": 60,
    "interval_idle": 300,
    "active_window": {"start": "07:00", "end": "16:00"},
}

MAX_ACTIVE_HOURS = 9  # check to prevent creating long active windows
BATCH_SIZE = 500  # hard limit (central server rejects anything over this)

breach_counts:  dict[str, int]      = {}  # metric -> consecutive breach count
alert_cooldown: dict[str, datetime] = {}  # metric -> datetime last alert was sent


# --------------------------- Settings storage ---------------------------


def _parse_hhmm(hhmm: str) -> time:
    h, m = map(int, hhmm.split(":"))
    return time(h, m)


def _window_hours(window: dict) -> float:
    start, end = _parse_hhmm(window["start"]), _parse_hhmm(window["end"])
    s = start.hour + start.minute / 60
    e = end.hour + end.minute / 60
    return (e - s) % 24


def load_settings() -> dict:
    """Load settings.json, falling back to defaults if missing/malformed."""
    if not SETTINGS_PATH.exists():
        print("settings.json not found — using defaults")
        return DEFAULT_SETTINGS
    try:
        return json.loads(SETTINGS_PATH.read_text())
    except json.JSONDecodeError:
        print("settings.json is malformed — using defaults")
        return DEFAULT_SETTINGS


def validate_settings(settings: dict):
    """Fail loud at startup if the active window is too long."""
    hours = _window_hours(settings["active_window"])
    if hours > MAX_ACTIVE_HOURS:
        raise SystemExit(
            f"Config error: active window is {hours:.1f}h, "
            f"max allowed is {MAX_ACTIVE_HOURS}h. Edit config/settings.json."
        )


def current_interval(settings: dict, now: time | None = None) -> int:
    """Return the active rate if now is inside the window, else idle."""
    if now is None:
        now = datetime.now().time()  # local time, Pi timezone must be correct
    w = settings["active_window"]
    start, end = _parse_hhmm(w["start"]), _parse_hhmm(w["end"])
    if start <= end:
        # Regular window i.e. 08:00 -> 18:00
        # 
        # 00:00 -- 07:00 ====== 16:00 -- 23:59
        #                ACTIVE
        #
        #   now >= 08:00
        #   AND 
        #   now < 18:00
        active = start <= now < end
    else:
        # Window crosses midnight i.e 22:00 -> 06:00
        #
        # 0 ===== 6 ----------- 22 ===== 24
        #  ACTIVE                  ACTIVE
        #
        #   now >= 22:00
        #   OR
        #   now < 06:00
        active = (now >= start) or (now < end)  # window crosses midnight
    return settings["interval_active"] if active else settings["interval_idle"]


# --------------------------- Criteria storage ---------------------------


def save_criteria(criteria: list[dict]):
    """Persist latest alert criteria from server to config/criteria.json."""
    CRITERIA_PATH.parent.mkdir(parents=True, exist_ok=True)
    CRITERIA_PATH.write_text(json.dumps(criteria, indent=4))


def load_criteria() -> list[dict]:
    """Load last known criteria from disk. Returns empty list if not found."""
    if not CRITERIA_PATH.exists():
        return []
    try:
        return json.loads(CRITERIA_PATH.read_text())
    except json.JSONDecodeError:
        return []


# --------------------------- Alert checking ---------------------------


def check_reading(data: dict, criteria: list[dict], recorded_at: str) -> list[dict]:
    """
    Check a single reading against criteria, tracking consecutive breaches
    per metric. Returns a list of confirmed alerts (i.e. those that have
    breached ALERT_CONFIDENCE times in a row and are outside cooldown).
    """
    confirmed = []
    now = datetime.now(timezone.utc)

    for criterion in criteria:
        metric    = criterion["metric"]
        threshold = float(criterion["threshold"])
        condition = criterion["condition"]
        severity  = criterion["severity"]

        value = data.get(metric)
        if value is None:
            breach_counts[metric] = 0
            continue

        breached = (
            (condition == "above" and float(value) > threshold) or
            (condition == "below" and float(value) < threshold)
        )

        if breached:
            breach_counts[metric] = breach_counts.get(metric, 0) + 1
        else:
            breach_counts[metric] = 0
            continue

        if breach_counts[metric] < ALERT_CONFIDENCE:
            print(f"breach_counts[{metric}]: {breach_counts[metric]}")
            continue

        last_alert = alert_cooldown.get(metric)
        if last_alert and (now - last_alert) < timedelta(hours=ALERT_COOLDOWN_HRS):
            continue

        try:
            recorded = datetime.fromisoformat(recorded_at)
            if recorded.tzinfo is None:
                recorded = recorded.replace(tzinfo=timezone.utc)
            delta_seconds = int((now - recorded).total_seconds())
        except ValueError:
            delta_seconds = None

        alert_cooldown[metric] = now
        breach_counts[metric]  = 0

        confirmed.append({
            "metric":        metric,
            "value":         value,
            "threshold":     threshold,
            "condition":     condition,
            "severity":      severity,
            "recorded_at":   recorded_at,
            "delta_seconds": delta_seconds,
        })

    return confirmed


async def send_alerts(client: httpx.AsyncClient, alerts: list[dict]):
    """
    POST confirmed alerts to the server alert endpoint.
    Logs locally if the send fails: alerts are not queued for retry.
    """
    token = os.getenv("AUTH_TOKEN", "").strip()

    for alert in alerts:
        delta = alert.get("delta_seconds")
        delta_str = f"{delta}s ago" if delta is not None else "unknown delay"
        print(
            f"[ALERT] {alert['severity'].upper()} — "
            f"{alert['metric']} is {alert['value']} "
            f"({alert['condition']} threshold of {alert['threshold']}) "
            f"recorded {delta_str}"
        )
        try:
            await client.post(
                f"{SERVER_URL}/aqc/v1/alert",
                headers=_auth_headers(),
                json=alert,
            )
        except (httpx.ConnectError, httpx.HTTPStatusError) as e:
            print(f"  Failed to send alert to server: {e}")


# --------------------------- HTTP helpers ---------------------------


def _auth_headers() -> dict:
    return {
        "Authorization": f"Bearer {os.getenv('AUTH_TOKEN', '').strip()}",
        "Content-Type": "application/json",
    }


async def _post_batch(client: httpx.AsyncClient, measurements: list[dict]) -> dict:
    """POST a batch of measurements. Raises on 401 or non-2xx."""
    res = await client.post(
        f"{SERVER_URL}/aqc/v1/ingest",
        headers=_auth_headers(),
        json={"measurements": measurements},
    )
    if res.status_code == 401:
        print("Batch ingest failed: auth token rejected.")
        raise httpx.HTTPStatusError("Unauthorised", request=res.request, response=res)
    res.raise_for_status()
    return res.json()


# --------------------------- Drain ---------------------------


async def _drain(client: httpx.AsyncClient, criteria: list[dict]):
    """
    Drain the queue in a single batch of up to BATCH_SIZE rows.
    Alert checking runs over the rows before sending when the queue depth
    is below QUEUE_ALERT_SKIP — meaning readings are fresh enough to be
    meaningful. Above that threshold, alerts are skipped.
    """
    depth = queue.count_pending()
    if not depth:
        return

    # Hard cap: if downsampling alone hasn't bounded the queue, drop
    # oldest aggregated rows before shipping.
    if depth > aggregate.MAX_QUEUE_ROWS:
        dropped = queue.trim_aggregated(depth - aggregate.QUEUE_LOW_WATER)
        if dropped:
            print(f"Queue over cap — discarded {dropped} oldest aggregated row(s)")
            depth -= dropped

    check_alerts = depth < QUEUE_ALERT_SKIP
    if not check_alerts:
        print(f"Queue large ({depth} pending) — alert checking skipped")

    pending_rows = queue.get_pending(limit=BATCH_SIZE)

    ids = [row["id"] for row in pending_rows]
    queue.set_status_many(ids, "sending")

    # Build the outbound API payload
    payload = [
        {
            "recorded_at": row["recorded_at"],
            "data": json.loads(row["data"]),
            "is_aggregated": bool(row["is_aggregated"]),
        }
        for row in pending_rows
    ]

     # Run alert checking over fresh, non-aggregated rows before sending.
    confirmed_alerts = []
    if check_alerts:
        for measurement in payload:
            if measurement["is_aggregated"]:
                continue

            alerts = check_reading(
                measurement["data"],
                criteria,
                measurement["recorded_at"],
            )
            confirmed_alerts.extend(alerts)

    print(f"Draining {len(payload)} measurement(s)...")

    try:
        response = await _post_batch(client, payload)

        queue.remove_many(ids)

        if response.get("criteria"):
            save_criteria(response["criteria"])

        print(f"  Sent {response.get('count', len(pending_rows))} measurement(s)")

        if confirmed_alerts:
            await send_alerts(client, confirmed_alerts)

    except httpx.ConnectError:
        queue.set_status_many(ids, "pending")
        print("  Lost connection during drain — will retry next cycle")
    except httpx.HTTPStatusError:
        queue.set_status_many(ids, "pending")

# --------------------------- Main ingest loop ---------------------------


async def ingest_loop():
    """Queues readings, drains to server in a batch, checks alerts when fresh."""
    queue.init()
    settings = load_settings()
    validate_settings(settings)
    print(
        f"Ingest job started — active {settings['active_window']['start']}"
        f"–{settings['active_window']['end']} "
        f"({settings['interval_active']}s active / {settings['interval_idle']}s idle)"
    )

    while True:
        await _run_ingest()
        interval = current_interval(settings)
        mode = "active" if interval == settings["interval_active"] else "idle"
        print(f"[{mode}] next reading in {interval}s")
        await asyncio.sleep(interval)


async def _run_ingest():
    recorded_at = datetime.now(timezone.utc).isoformat()

    # 1. Read sensor
    try:
        data = read_sensor()
    except RuntimeError as e:
        print(f"Sensor read failed: {e}")
        return

    # 2. Queue first to SQLite
    queue.enqueue(data, recorded_at)

    # 3. Load last known criteria (updated on successful drain)
    criteria = load_criteria()
    
    # 4. Aggregate >14-day-old rows into 1hr buckets
    try:
        summary = aggregate.run_aggregation()
        if summary["buckets"]:
            print(
                f"Aggregated {summary['rows_in']} old row(s) into "
                f"{summary['buckets']} hourly row(s)"
            )
    except Exception as e:
        print(f"Aggregation failed: {e}")

    # 5. Drain queue (slices of 500 max), alert checking if queue is small
    #    Also handles trimming queue in case of extreme growth
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await _drain(client, criteria)

        pending = queue.count_pending()
        if pending:
            print(f"{pending} measurement(s) still queued — server may be offline")

    except Exception as e:
        print(f"Drain failed unexpectedly: {e}")
        
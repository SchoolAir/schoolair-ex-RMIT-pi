"""jobs/ingest.py

Reads sensor data on a fixed interval, queues locally first, then drains
the queue to the server. Tracks consecutive threshold breaches per metric
and fires alerts to the server once ALERT_CONFIDENCE readings in a row breach.
A per-metric cooldown prevents alert spam after an alert is confirmed.
"""

import asyncio
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
import httpx
from dotenv import load_dotenv
from services.sensor import read_sensor
import db.queue as queue

load_dotenv()

SERVER_URL          = os.getenv("SERVER_URL", "").rstrip("/")
INTERVAL            = int(os.getenv("INGEST_INTERVAL", 60))       # seconds
ALERT_CONFIDENCE    = int(os.getenv("ALERT_CONFIDENCE", 3))       # consecutive breaches to confirm alert
ALERT_COOLDOWN_HRS  = float(os.getenv("ALERT_COOLDOWN_HOURS", 1)) # hours between alerts per metric
CRITERIA_PATH       = Path("config/criteria.json")

breach_counts:  dict[str, int]      = {}  # metric -> consecutive breach count
alert_cooldown: dict[str, datetime] = {}  # metric -> datetime last alert was sent


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

    # Check EACH metric's criteria (i.e. for this loop, temp)
    for criterion in criteria:
        metric    = criterion["metric"]
        threshold = float(criterion["threshold"])
        condition = criterion["condition"]
        severity  = criterion["severity"]

        # Missing/invalid data?
        value = data.get(metric)
        if value is None:
            breach_counts[metric] = 0
            continue

        # Does breach exist for this check?
        breached = (
            (condition == "above" and float(value) > threshold) or
            (condition == "below" and float(value) < threshold)
        )

        # Update consecutive breach counter
        if breached:
            breach_counts[metric] = breach_counts.get(metric, 0) + 1
        else:
            breach_counts[metric] = 0  # reset streak on clean reading
            continue

        # Check if we've hit confidence threshold
        if breach_counts[metric] < ALERT_CONFIDENCE:
            print(f"breach_counts[metric]: {breach_counts[metric]}")
            continue

        # Check cooldown. Skip if alerted for this metric recently
        last_alert = alert_cooldown.get(metric)
        if last_alert and (now - last_alert) < timedelta(hours=ALERT_COOLDOWN_HRS):
            continue

        # Confirmed alert! Calculate delta from when reading was recorded to now
        try:
            recorded = datetime.fromisoformat(recorded_at)
            if recorded.tzinfo is None:
                recorded = recorded.replace(tzinfo=timezone.utc)
            delta_seconds = int((now - recorded).total_seconds())
        except ValueError:
            delta_seconds = None

        alert_cooldown[metric] = now
        breach_counts[metric]  = 0  # reset after alert fires

        confirmed.append({
            "metric":         metric,
            "value":          value,
            "threshold":      threshold,
            "condition":      condition,
            "severity":       severity,
            "recorded_at":    recorded_at,
            "delta_seconds":  delta_seconds,  # how stale is this alert
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
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=alert,
            )
        except (httpx.ConnectError, httpx.HTTPStatusError) as e:
            print(f"  Failed to send alert to server: {e}")


# --------------------------- Sending measurement ---------------------------


async def _post_measurement(
    client: httpx.AsyncClient,
    recorded_at: str,
    data: dict
) -> dict:
    """
    POST a single measurement to the server.
    Returns the response JSON on success.
    Raises on non-2xx or network failure.
    """
    token = os.getenv("AUTH_TOKEN", "").strip()

    res = await client.post(
        f"{SERVER_URL}/aqc/v1/ingest",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={"recorded_at": recorded_at, "data": data},
    )

    if res.status_code == 401:
        print("Ingest failed: auth token rejected: device may need re-registration.")
        raise httpx.HTTPStatusError("Unauthorised", request=res.request, response=res)

    res.raise_for_status()
    return res.json()


# ----------------------- Queue drain + alert check -----------------------


async def _drain_queue(client: httpx.AsyncClient, criteria: list[dict]):
    """
    Send all pending queued measurements to the server oldest-first.
    Checks each reading against alert criteria as it's processed.
    Removes successfully sent rows, resets failures to 'pending' for next cycle.
    """
    pending = queue.get_pending(limit=50)
    if not pending:
        return

    print(f"Draining {len(pending)} queued measurement(s)...")
    confirmed_alerts = []

    for row in pending:
        queue.set_status(row["id"], "sending")
        try:
            data = json.loads(row["data"])
            response = await _post_measurement(client, row["recorded_at"], data)
            queue.remove(row["id"])

            # Update criteria if server returns fresher ones
            if response.get("criteria"):
                criteria = response["criteria"]
                save_criteria(criteria)

            # Check this reading against criteria
            alerts = check_reading(data, criteria, row["recorded_at"])
            confirmed_alerts.extend(alerts)

        except httpx.ConnectError:
            queue.set_status(row["id"], "pending")
            print("  Lost connection during drain — will retry next cycle")
            break  # no point continuing if we lost connectivity
        except httpx.HTTPStatusError:
            queue.set_status(row["id"], "pending")

    if confirmed_alerts:
        await send_alerts(client, confirmed_alerts)


# --------------------------- Main ingest loop ---------------------------


async def ingest_loop():
    """Queues readings, drains queue & checks alerts, POSTs data."""
    queue.init()
    print(f"Ingest job started — running every {INTERVAL}s")

    while True:
        await _run_ingest()
        await asyncio.sleep(INTERVAL)


async def _run_ingest():
    recorded_at = datetime.now(timezone.utc).isoformat()

    # 1. Read sensor
    try:
        data = read_sensor()
    except RuntimeError as e:
        print(f"Sensor read failed: {e}")
        return

    # 2. Queue first to sqlite
    queue.enqueue(data, recorded_at)

    # 3. Load last known criteria (updated during drain)
    criteria = load_criteria()

    # 4. Drain queue (all pending + one we just added)
    #    We also check alerts here and POST as needed
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await _drain_queue(client, criteria)

        pending = queue.count_pending()
        if pending:
            print(f"{pending} measurement(s) still queued: server may be offline")

    except Exception as e:
        print(f"Drain failed unexpectedly: {e}")
        
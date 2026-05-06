"""jobs/ingest.py

Reads sensor data on a fixed interval, queues locally first, then drains
the queue to the server in a single batch. Alert checking runs over the
batch before sending when the queue is small (< QUEUE_ALERT_SKIP), where
readings are fresh enough for meaningful alerting.
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
QUEUE_ALERT_SKIP    = int(os.getenv("QUEUE_ALERT_SKIP", 50))      # queue depth above which alert checking is skipped

CRITERIA_PATH = Path("config/criteria.json")

BATCH_SIZE = 100  # hard limit (central server rejects anything over this)

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

    check_alerts = depth < QUEUE_ALERT_SKIP
    if not check_alerts:
        print(f"Queue large ({depth} pending) — alert checking skipped")

    pending = queue.get_pending(limit=BATCH_SIZE)

    for row in pending:
        queue.set_status(row["id"], "sending")

    measurements = [
        {"recorded_at": row["recorded_at"], "data": json.loads(row["data"])}
        for row in pending
    ]

    # Run alert checking over the batch rows before we send
    confirmed_alerts = []
    if check_alerts:
        for row, measurement in zip(pending, measurements):
            alerts = check_reading(measurement["data"], criteria, row["recorded_at"])
            confirmed_alerts.extend(alerts)

    print(f"Draining {len(pending)} measurement(s)...")

    try:
        response = await _post_batch(client, measurements)

        for row in pending:
            queue.remove(row["id"])

        if response.get("criteria"):
            save_criteria(response["criteria"])

        print(f"  Sent {response.get('count', len(pending))} measurement(s)")

        if confirmed_alerts:
            await send_alerts(client, confirmed_alerts)

    except httpx.ConnectError:
        for row in pending:
            queue.set_status(row["id"], "pending")
        print("  Lost connection during drain — will retry next cycle")
    except httpx.HTTPStatusError:
        for row in pending:
            queue.set_status(row["id"], "pending")


# --------------------------- Main ingest loop ---------------------------


async def ingest_loop():
    """Queues readings, drains to server in a batch, checks alerts when fresh."""
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

    # 2. Queue first to SQLite
    queue.enqueue(data, recorded_at)

    # 3. Load last known criteria (updated on successful drain)
    criteria = load_criteria()

    # 4. Drain queue (slices of 100 max), alert checking if queue is small
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await _drain(client, criteria)

        pending = queue.count_pending()
        if pending:
            print(f"{pending} measurement(s) still queued — server may be offline")

    except Exception as e:
        print(f"Drain failed unexpectedly: {e}")
        
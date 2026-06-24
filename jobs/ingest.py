"""jobs/ingest.py

Two-speed data pipeline:

  Read loop  — collects a sensor reading every READ_ACTIVE_SECONDS (active
               window) or READ_IDLE_SECONDS (outside window) and holds it in
               an in-memory buffer.  Nothing is written to disk here.

  Drain loop — event-driven: the read loop triggers a drain after each standard
               read once DRAIN_ACTIVE_SECONDS / DRAIN_IDLE_SECONDS have elapsed
               since the last drain.  The drain loop itself is a pure event
               consumer with a long fallback timeout as a safety net.

SQLite is written only when the buffer hits BUFFER_CAPACITY *and* the server is
unreachable — so during normal operation the SD card is never touched for
telemetry data.  If the device later reconnects, the next drain sends both the
SQLite backlog and the current in-memory buffer together.
"""

import asyncio
import json
import os
from datetime import datetime, timezone, timedelta, time
from pathlib import Path
import httpx
from dotenv import load_dotenv
from services.sensor import read_sensor, extract_metric
import db.queue as queue
import jobs.aggregate as aggregate
import state

load_dotenv()

SERVER_URL         = os.getenv("SERVER_URL", "").rstrip("/")
INGEST_URL         = os.getenv("INGEST_URL", f"{SERVER_URL}/aqc/v1/ingest")
ALERT_NEAR_PCT     = float(os.getenv("ALERT_NEAR_PCT", 10))  # within N% of threshold = "near"
ALERT_COOLDOWN_HRS = float(os.getenv("ALERT_COOLDOWN_HOURS", 1))
BUFFER_CAPACITY    = int(os.getenv("BUFFER_CAPACITY", 500))

# Read and drain intervals are hardcoded, not user-configurable.
# Overridable via env var for development and testing only.
READ_ACTIVE_SECONDS  = int(os.getenv("READ_INTERVAL_ACTIVE",  300))   # 5 min
READ_IDLE_SECONDS    = int(os.getenv("READ_INTERVAL_IDLE",    900))   # 15 min
DRAIN_ACTIVE_SECONDS = int(os.getenv("DRAIN_INTERVAL_ACTIVE", 1800))  # 30 min
DRAIN_IDLE_SECONDS   = int(os.getenv("DRAIN_INTERVAL_IDLE",   7200))  # 2 h

CRITERIA_PATH = Path("config/criteria.json")
SETTINGS_PATH = Path("config/settings.json")

DEFAULT_SETTINGS = {
    "active_window": {"start": "07:00", "end": "16:00"},
}

MAX_ACTIVE_HOURS = 9
BATCH_SIZE       = 500

# In-memory buffer: readings not yet sent to the server.
# Each entry: {"data": dict, "recorded_at": str}
_buffer: list[dict] = []

alert_cooldown:       dict[str, datetime] = {}
_verifying:           set[str]            = set()   # metrics currently in a verify routine
_alert_buffer:        list[dict]          = []      # alerts pending send (in-memory first)
ALERT_BUFFER_CAPACITY = int(os.getenv("ALERT_BUFFER_CAPACITY", 50))

# Set by trigger_drain() (SIGUSR2 handler or internal callers) to wake the
# drain loop.  Initialised to None until the event loop is running.
_drain_trigger:   asyncio.Event | None = None
_last_drained_at: datetime | None     = None


def trigger_drain() -> None:
    """Request an immediate out-of-schedule drain.

    Safe to call from asyncio signal handlers or from any coroutine.
    No-op if the drain loop hasn't started yet.
    """
    if _drain_trigger is not None:
        _drain_trigger.set()


# --------------------------- Settings ---------------------------


def _parse_hhmm(hhmm: str) -> time:
    h, m = map(int, hhmm.split(":"))
    return time(h, m)


def _window_hours(window: dict) -> float:
    start = _parse_hhmm(window["start"])
    end   = _parse_hhmm(window["end"])
    s = start.hour + start.minute / 60
    e = end.hour   + end.minute   / 60
    return (e - s) % 24


def _in_active_window(settings: dict, now: time | None = None) -> bool:
    if now is None:
        now = datetime.now().time()
    w = settings["active_window"]
    start, end = _parse_hhmm(w["start"]), _parse_hhmm(w["end"])
    if start <= end:
        return start <= now < end
    return (now >= start) or (now < end)


def load_settings() -> dict:
    if not SETTINGS_PATH.exists():
        print("settings.json not found — using defaults")
        return DEFAULT_SETTINGS
    try:
        return json.loads(SETTINGS_PATH.read_text())
    except json.JSONDecodeError:
        print("settings.json is malformed — using defaults")
        return DEFAULT_SETTINGS


def validate_settings(settings: dict):
    hours = _window_hours(settings["active_window"])
    if hours > MAX_ACTIVE_HOURS:
        raise SystemExit(
            f"Config error: active window is {hours:.1f}h, "
            f"max allowed is {MAX_ACTIVE_HOURS}h. Edit config/settings.json."
        )


def current_read_interval(settings: dict, now: time | None = None) -> int:
    return READ_ACTIVE_SECONDS if _in_active_window(settings, now) else READ_IDLE_SECONDS


def current_drain_interval(settings: dict, now: time | None = None) -> int:
    return DRAIN_ACTIVE_SECONDS if _in_active_window(settings, now) else DRAIN_IDLE_SECONDS


# --------------------------- Criteria ---------------------------


def save_criteria(criteria: list[dict]):
    CRITERIA_PATH.parent.mkdir(parents=True, exist_ok=True)
    CRITERIA_PATH.write_text(json.dumps(criteria, indent=4))


def load_criteria() -> list[dict]:
    if not CRITERIA_PATH.exists():
        return []
    try:
        return json.loads(CRITERIA_PATH.read_text())
    except json.JSONDecodeError:
        return []


# --------------------------- Alert verification ---------------------------


def _breached(value: float, threshold: float, condition: str) -> bool:
    if condition == "above":
        return value > threshold
    return value < threshold  # "below"


def _near_or_breached(value: float, threshold: float, condition: str) -> bool:
    """True if value is at or within ALERT_NEAR_PCT% of the threshold.

    'Near' prevents the verify routine from declaring an event safe when the
    reading has only just crept back below the line — e.g. PM2.5 = 24.8 with
    threshold 25 is still worth watching.
    """
    margin = threshold * ALERT_NEAR_PCT / 100
    if condition == "above":
        return value >= threshold - margin
    return value <= threshold + margin  # "below" — near means slightly above the floor


async def _take_verify_read(metric: str) -> float | None:
    """Out-of-schedule sensor read during verification.

    Sends SIGUSR1 to the C daemon so it reads the sensor immediately rather
    than returning the stale value from its last 60 s cycle.
    Added to buffer like any other reading.
    """
    from services.trigger import trigger_fresh_sample
    fresh = await trigger_fresh_sample()
    if not fresh:
        print(f"[verify/{metric}] daemon trigger timed out — using last available sample")

    recorded_at = datetime.now(timezone.utc).isoformat()
    try:
        data = read_sensor()
    except RuntimeError as e:
        print(f"[verify/{metric}] sensor read failed: {e}")
        return None
    state.set(data, recorded_at)
    _buffer.append({"data": data, "recorded_at": recorded_at})
    return extract_metric(data, metric)


def _buffer_alert(alert: dict):
    """Add alert to the in-memory buffer; flush to SQLite if it hits capacity.

    Mirrors the measurement buffer philosophy: SQLite is only written when both
    the buffer is full AND the server is unreachable, keeping SD card writes rare.
    With a 1h cooldown per metric, 50 slots covers days of outage.
    """
    _alert_buffer.append(alert)
    if len(_alert_buffer) >= ALERT_BUFFER_CAPACITY:
        for a in _alert_buffer:
            queue.enqueue_alert(a, a.get("recorded_at", datetime.now(timezone.utc).isoformat()))
        _alert_buffer.clear()
        print(f"Alert buffer at capacity ({ALERT_BUFFER_CAPACITY}) — flushed to SQLite")


def _log_queued_alert(alert: dict):
    delta       = alert.get("delta_seconds")
    delta_str   = f"{delta}s ago" if delta is not None else "unknown delay"
    persistence = "persistent" if alert.get("persistent") else "fleeting"
    stage_info  = ""
    if "stage2_avg" in alert:
        stage_info = f" | s1={alert['stage1_avg']} s2={alert['stage2_avg']}"
    elif "stage1_avg" in alert:
        stage_info = f" | s1={alert['stage1_avg']}"
    print(
        f"[ALERT] {alert['severity'].upper()} — "
        f"{alert['metric']} = {alert['value']} "
        f"({alert['condition']} {alert['threshold']}) "
        f"triggered {delta_str} | {persistence}{stage_info}"
    )


async def _do_verify(metric: str, criterion: dict, breach_entry: dict):
    """Two-stage verification routine. Runs as a background task.

    Stage 1 — T+10s and T+30s:
      avg < threshold  → transient spike (e.g. sweeping dust); patch breach value in buffer, stop.
      avg at/near threshold → real but possibly fleeting; advance to Stage 2.

    Stage 2 — T+1m and T+2m:
      avg < threshold  → fleeting event; log for dashboard, no active alarm.
      avg at/near threshold → persistent breach; queue alert for sending at next drain.

    Total time to confirmed alarm: ~2 minutes.
    """
    threshold   = float(criterion["threshold"])
    condition   = criterion["condition"]
    severity    = criterion["severity"]
    breach_val  = extract_metric(breach_entry["data"], metric)
    recorded_at = breach_entry["recorded_at"]

    # ── Stage 1: T+10s and T+30s ──────────────────────────────────────────────
    await asyncio.sleep(10)
    v1 = await _take_verify_read(metric)
    await asyncio.sleep(20)   # 20s more = T+30s total
    v2 = await _take_verify_read(metric)

    if v1 is None or v2 is None:
        print(f"[verify/{metric}] sensor unavailable — aborting")
        return

    avg1 = (v1 + v2) / 2

    if not _near_or_breached(avg1, threshold, condition):
        # Transient spike — patch the buffer entry in-place so the server
        # receives the corrected average instead of the momentary spike.
        data = breach_entry["data"]
        for sensor_name, sensor_data in data.items():
            if isinstance(sensor_data, dict) and metric in sensor_data:
                breach_entry["data"] = {
                    **data,
                    sensor_name: {**sensor_data, metric: round(avg1, 4)},
                }
                break
        print(
            f"[verify/{metric}] Stage 1 avg {avg1:.2f} is below threshold "
            f"{threshold} — transient spike, breach value replaced"
        )
        return

    print(
        f"[verify/{metric}] Stage 1 avg {avg1:.2f} "
        f"{'exceeds' if _breached(avg1, threshold, condition) else 'is near'} "
        f"threshold {threshold} — advancing to Stage 2"
    )

    # ── Stage 2: T+1m and T+2m ────────────────────────────────────────────────
    await asyncio.sleep(30)   # we're at T+30s → sleep 30s → T+1m
    v3 = await _take_verify_read(metric)
    await asyncio.sleep(60)   # T+1m → T+2m
    v4 = await _take_verify_read(metric)

    now = datetime.now(timezone.utc)
    try:
        t = datetime.fromisoformat(recorded_at)
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        delta_seconds = int((now - t).total_seconds())
    except ValueError:
        delta_seconds = None

    base_alert = {
        "metric":        metric,
        "value":         breach_val,
        "threshold":     threshold,
        "condition":     condition,
        "severity":      severity,
        "recorded_at":   recorded_at,
        "delta_seconds": delta_seconds,
        "stage1_avg":    round(avg1, 2),
        "verified":      v3 is not None and v4 is not None,
    }

    if v3 is None or v4 is None:
        alert = {**base_alert, "persistent": False}
        _buffer_alert(alert)
        alert_cooldown[metric] = now
        print(f"[verify/{metric}] sensor unavailable in Stage 2 — logged as inconclusive")
        return

    avg2 = (v3 + v4) / 2
    persistent = _near_or_breached(avg2, threshold, condition)

    alert = {**base_alert, "stage2_avg": round(avg2, 2), "persistent": persistent}
    alert_cooldown[metric] = now

    if persistent:
        # Send immediately — don't wait for the next drain cycle
        token = os.getenv("AUTH_TOKEN", "").strip()
        if not token:
            print(f"[verify/{metric}] persistent breach confirmed but no token yet — queuing alert")
        else:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.post(
                        f"{SERVER_URL}/aqc/v1/alert",
                        headers=_auth_headers(),
                        json=alert,
                    )
                print(
                    f"[verify/{metric}] Stage 2 avg {avg2:.2f} "
                    f"{'exceeds' if _breached(avg2, threshold, condition) else 'is near'} "
                    f"threshold {threshold} — persistent breach confirmed, alert sent"
                )
                trigger_drain()  # don't wait for the next scheduled drain
                return  # sent — no need to queue
            except (httpx.ConnectError, httpx.HTTPStatusError) as e:
                print(
                    f"[verify/{metric}] Immediate alert send failed ({e}) "
                    f"— queuing for retry at next drain"
                )

    else:
        print(
            f"[verify/{metric}] Stage 2 avg {avg2:.2f} is below threshold "
            f"{threshold} — fleeting event, logged for dashboard"
        )

    # Persistent alert that failed immediate send, or fleeting event for retrospective dashboard
    _buffer_alert(alert)


async def _verify_alert(metric: str, criterion: dict, breach_entry: dict):
    _verifying.add(metric)
    try:
        await _do_verify(metric, criterion, breach_entry)
    finally:
        _verifying.discard(metric)


async def _drain_alerts():
    """Flush the in-memory alert buffer (and any SQLite overflow) to the server.

    Called after a successful measurement batch so we know we have connectivity.

    Buffer-first:   alerts live in _alert_buffer in memory.
    SQLite fallback: only written when the buffer hits ALERT_BUFFER_CAPACITY
                     while the server is unreachable — rare with a 1h cooldown.
    """
    # ── Send in-memory buffer ─────────────────────────────────────────────────
    if _alert_buffer:
        sent = 0
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                for alert in list(_alert_buffer):
                    _log_queued_alert(alert)
                    try:
                        await client.post(
                            f"{SERVER_URL}/aqc/v1/alert",
                            headers=_auth_headers(),
                            json=alert,
                        )
                    except httpx.HTTPStatusError as e:
                        print(f"  Alert rejected ({e.response.status_code}) — will not retry")
                    sent += 1

            _alert_buffer.clear()
            if sent:
                print(f"  Sent {sent} buffered alert(s)")

        except httpx.ConnectError:
            # Keep unsent alerts in buffer; if now full, overflow to SQLite
            unsent = _alert_buffer[sent:]
            _alert_buffer.clear()
            _alert_buffer.extend(unsent)
            if sent:
                print(f"  Sent {sent} alert(s) before connection dropped")
            if len(_alert_buffer) >= ALERT_BUFFER_CAPACITY:
                for a in _alert_buffer:
                    queue.enqueue_alert(a, a.get("recorded_at", datetime.now(timezone.utc).isoformat()))
                _alert_buffer.clear()
                print(f"  Alert buffer full — flushed to SQLite")
            return  # connection is down; no point trying SQLite drain

    # ── Drain SQLite overflow (from previous capacity events) ─────────────────
    sqlite_rows = queue.get_pending_alerts()
    if not sqlite_rows:
        return

    all_ids  = [r["id"] for r in sqlite_rows]
    sent_ids = []
    queue.set_alert_status_many(all_ids, "sending")

    async with httpx.AsyncClient(timeout=10) as client:
        for row in sqlite_rows:
            alert = json.loads(row["data"])
            _log_queued_alert(alert)
            try:
                await client.post(
                    f"{SERVER_URL}/aqc/v1/alert",
                    headers=_auth_headers(),
                    json=alert,
                )
                sent_ids.append(row["id"])
            except httpx.ConnectError:
                print(f"  Lost connection — {len(sqlite_rows) - len(sent_ids)} SQLite alert(s) held")
                break
            except httpx.HTTPStatusError as e:
                print(f"  Alert rejected ({e.response.status_code}) — will not retry")
                sent_ids.append(row["id"])

    if sent_ids:
        queue.remove_alerts(sent_ids)
        print(f"  Drained {len(sent_ids)} SQLite alert(s)")

    unsent = [i for i in all_ids if i not in set(sent_ids)]
    if unsent:
        queue.set_alert_status_many(unsent, "pending")


# --------------------------- HTTP helpers ---------------------------


def _auth_headers() -> dict:
    return {
        "Authorization": f"Bearer {os.getenv('AUTH_TOKEN', '').strip()}",
        "Content-Type": "application/json",
    }


async def _post_batch(client: httpx.AsyncClient, measurements: list[dict]) -> dict:
    res = await client.post(
        INGEST_URL,
        headers=_auth_headers(),
        json={"measurements": measurements},
    )
    if res.status_code == 401:
        print("Batch ingest failed: auth token rejected.")
        raise httpx.HTTPStatusError("Unauthorised", request=res.request, response=res)
    res.raise_for_status()
    try:
        return res.json()
    except Exception:
        return {}  # server returned 2xx with empty/non-JSON body (e.g. LEGACY endpoint)


# --------------------------- Read step ---------------------------


def _should_drain(settings: dict) -> bool:
    """True if skipping this read would cause the drain interval to be exceeded.

    Fires when the time remaining until the drain deadline is less than one read
    interval — i.e. the next scheduled read would arrive after the deadline.
    This guarantees drains happen *within* the configured interval (25–30 min
    active, 105–120 min idle) rather than potentially one read interval late.
    """
    if _last_drained_at is None:
        return False  # let the initial drain in _drain_loop handle startup
    elapsed = (datetime.now(timezone.utc) - _last_drained_at).total_seconds()
    return elapsed >= current_drain_interval(settings) - current_read_interval(settings)


async def _run_read(settings: dict):
    """Read one sensor sample, update buffer, and trigger verification on any breach."""
    recorded_at = datetime.now(timezone.utc).isoformat()
    try:
        data = read_sensor()
    except RuntimeError as e:
        print(f"Sensor read failed: {e}")
        return

    state.set(data, recorded_at)
    entry = {"data": data, "recorded_at": recorded_at}
    _buffer.append(entry)

    criteria = load_criteria()
    if criteria:
        now = datetime.now(timezone.utc)
        for criterion in criteria:
            metric    = criterion["metric"]
            threshold = float(criterion["threshold"])
            condition = criterion["condition"]
            value     = extract_metric(data, metric)

            if value is None:
                continue
            if not _breached(float(value), threshold, condition):
                continue
            if metric in _verifying:
                continue

            last_alert = alert_cooldown.get(metric)
            if last_alert and (now - last_alert) < timedelta(hours=ALERT_COOLDOWN_HRS):
                continue

            print(
                f"[breach] {metric} = {value} ({condition} threshold {threshold}) "
                f"— starting verification"
            )
            asyncio.create_task(_verify_alert(metric, criterion, entry))

    # Trigger drain on standard reads — not during alert verification.
    if not _verifying and _should_drain(settings):
        trigger_drain()


# --------------------------- Drain step ---------------------------


async def _run_drain(settings: dict):
    """Send the in-memory buffer plus any SQLite backlog to the server.

    SQLite is written only when both conditions are true:
      1. The buffer has reached BUFFER_CAPACITY.
      2. The server is unreachable.
    All other drain attempts go memory → server with no disk I/O.
    """
    global _buffer, _last_drained_at

    # Compact old SQLite rows into hourly means (no-op when SQLite is empty)
    try:
        summary = aggregate.run_aggregation()
        if summary["buckets"]:
            print(f"Aggregated {summary['rows_in']} old row(s) into {summary['buckets']} hourly row(s)")
    except Exception as e:
        print(f"Aggregation failed: {e}")

    # Hard cap: if SQLite has grown very large during an extended outage,
    # drop the oldest aggregated rows to keep it bounded.
    depth = queue.count_pending()
    if depth > aggregate.MAX_QUEUE_ROWS:
        dropped = queue.trim_aggregated(depth - aggregate.QUEUE_LOW_WATER)
        if dropped:
            print(f"SQLite queue over cap — discarded {dropped} oldest aggregated row(s)")

    _last_drained_at = datetime.now(timezone.utc)

    token = os.getenv("AUTH_TOKEN", "").strip()
    if not token:
        # No token yet — keep buffering; spill to SQLite only when buffer is full
        if len(_buffer) >= BUFFER_CAPACITY:
            for item in _buffer:
                queue.enqueue(item["data"], item["recorded_at"])
            _buffer.clear()
            print(f"[drain] no token — buffer full, flushed {BUFFER_CAPACITY} readings to SQLite")
        else:
            print(f"[drain] no token — {len(_buffer)} reading(s) held in memory, waiting for registration")
        return

    if not _buffer and not queue.count_pending():
        return

    # Build payload: fresh buffer readings first, then any SQLite backlog
    sqlite_rows = queue.get_pending(limit=BATCH_SIZE)
    sqlite_ids  = [r["id"] for r in sqlite_rows]

    payload = [
        {"recorded_at": item["recorded_at"], "data": item["data"], "is_aggregated": False}
        for item in _buffer
    ] + [
        {
            "recorded_at":   r["recorded_at"],
            "data":          json.loads(r["data"]),
            "is_aggregated": bool(r["is_aggregated"]),
        }
        for r in sqlite_rows
    ]

    print(
        f"Draining {len(payload)} measurement(s) "
        f"({len(_buffer)} buffered, {len(sqlite_rows)} from SQLite)…"
    )

    if sqlite_ids:
        queue.set_status_many(sqlite_ids, "sending")

    # Snapshot the buffer length before the async POST so any reading appended
    # by _run_read during the HTTP call is not accidentally discarded.
    n_buffered = len(_buffer)

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await _post_batch(client, payload)

        del _buffer[:n_buffered]
        if sqlite_ids:
            queue.remove_many(sqlite_ids)

        if response.get("criteria"):
            save_criteria(response["criteria"])

        print(f"  Sent {response.get('count', len(payload))} measurement(s)")

        # Flush alert buffer now that we know we have connectivity
        await _drain_alerts()

    except httpx.ConnectError:
        if sqlite_ids:
            queue.set_status_many(sqlite_ids, "pending")

        if len(_buffer) >= BUFFER_CAPACITY:
            print(
                f"Server unreachable and buffer full ({len(_buffer)}/{BUFFER_CAPACITY}) "
                f"— flushing buffer to SQLite"
            )
            for item in _buffer:
                queue.enqueue(item["data"], item["recorded_at"])
            _buffer.clear()
        else:
            remaining = BUFFER_CAPACITY - len(_buffer)
            print(
                f"Server unreachable — {len(_buffer)} reading(s) held in memory "
                f"({remaining} slot(s) before SQLite fallback)"
            )

    except httpx.HTTPStatusError:
        if sqlite_ids:
            queue.set_status_many(sqlite_ids, "pending")
        print("Server returned an error — will retry next drain cycle")

    except Exception as e:
        if sqlite_ids:
            queue.set_status_many(sqlite_ids, "pending")
        print(f"Drain failed unexpectedly: {e}")


# --------------------------- Loops ---------------------------


async def _read_loop(settings: dict):
    while True:
        await _run_read(settings)
        interval = current_read_interval(settings)
        mode = "active" if _in_active_window(settings) else "idle"
        print(f"[read/{mode}] next in {interval}s")
        await asyncio.sleep(interval)


async def _drain_loop(settings: dict):
    global _drain_trigger
    _drain_trigger = asyncio.Event()

    # Brief settle so the read loop collects at least one reading, and to clear
    # any SQLite backlog left from a previous outage.
    await asyncio.sleep(15)
    await _run_drain(settings)

    # Drains are triggered by _run_read once the drain interval has elapsed.
    # The fallback timeout fires only if reads stop arriving for an extended period
    # (e.g. sensor failure) so the loop never blocks indefinitely.
    fallback = float(DRAIN_IDLE_SECONDS * 2)
    while True:
        try:
            await asyncio.wait_for(_drain_trigger.wait(), timeout=fallback)
            _drain_trigger.clear()
            print("[drain] triggered")
        except asyncio.TimeoutError:
            print("[drain] fallback — no reads received, draining anyway")
        await _run_drain(settings)


async def ingest_loop():
    """Initialise SQLite, then run the read and drain loops concurrently."""
    queue.init()
    settings = load_settings()
    validate_settings(settings)
    print(
        f"Ingest started — "
        f"reads {READ_ACTIVE_SECONDS}s/{READ_IDLE_SECONDS}s (active/idle) | "
        f"drains {DRAIN_ACTIVE_SECONDS}s/{DRAIN_IDLE_SECONDS}s (active/idle) | "
        f"buffer capacity {BUFFER_CAPACITY}"
    )
    await asyncio.gather(
        _read_loop(settings),
        _drain_loop(settings),
    )

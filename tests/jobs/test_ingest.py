"""tests/jobs/test_ingest.py

Unit tests for jobs.ingest: scheduling intervals, breach detection,
alert buffering, trigger_drain event, drain token guard, and buffer
correction on transient spikes.

Run laptop-safe tests only:
    pytest -m "not hardware"
"""

import asyncio
from datetime import time
from unittest.mock import AsyncMock, patch

import pytest
import jobs.ingest as ingest
import db.queue as queue
from jobs.ingest import (
    current_read_interval,
    current_drain_interval,
    _window_hours,
    validate_settings,
    _breached,
    _near_or_breached,
    _buffer_alert,
    _do_verify,
    _run_drain,
    trigger_drain,
    ALERT_NEAR_PCT,
    ALERT_BUFFER_CAPACITY,
)

READ_ACTIVE  = 300
READ_IDLE    = 900
DRAIN_ACTIVE = 1800
DRAIN_IDLE   = 7200

S = {
    "interval_read_active":  READ_ACTIVE,
    "interval_read_idle":    READ_IDLE,
    "interval_drain_active": DRAIN_ACTIVE,
    "interval_drain_idle":   DRAIN_IDLE,
    "active_window": {"start": "07:00", "end": "16:00"},
}


# ── Shared fixtures ────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_ingest_state():
    """Clear mutable module-level state before and after each test."""
    ingest._alert_buffer.clear()
    ingest._buffer.clear()
    ingest._verifying.clear()
    ingest.alert_cooldown.clear()
    yield
    ingest._alert_buffer.clear()
    ingest._buffer.clear()


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(queue, "DB_PATH", tmp_path / "test_queue.db")
    queue.init()


# ── Read interval ──────────────────────────────────────────────────────────────

def test_read_inside_window():
    assert current_read_interval(S, time(9, 0)) == READ_ACTIVE

def test_read_before_window():
    assert current_read_interval(S, time(6, 59)) == READ_IDLE

def test_read_start_is_inclusive():
    assert current_read_interval(S, time(7, 0)) == READ_ACTIVE

def test_read_end_is_exclusive():
    assert current_read_interval(S, time(16, 0)) == READ_IDLE

def test_read_midnight_crossing():
    night = {**S, "active_window": {"start": "22:00", "end": "06:00"}}
    assert current_read_interval(night, time(23, 0)) == READ_ACTIVE
    assert current_read_interval(night, time(2, 0))  == READ_ACTIVE
    assert current_read_interval(night, time(12, 0)) == READ_IDLE


# ── Drain interval ─────────────────────────────────────────────────────────────

def test_drain_inside_window():
    assert current_drain_interval(S, time(9, 0)) == DRAIN_ACTIVE

def test_drain_before_window():
    assert current_drain_interval(S, time(6, 59)) == DRAIN_IDLE

def test_drain_end_is_exclusive():
    assert current_drain_interval(S, time(16, 0)) == DRAIN_IDLE


# ── Window helpers ─────────────────────────────────────────────────────────────

def test_window_hours_handles_midnight():
    assert _window_hours({"start": "22:00", "end": "06:00"}) == 8
    assert _window_hours({"start": "07:00", "end": "16:00"}) == 9

def test_validate_rejects_long_window():
    bad = {**S, "active_window": {"start": "06:00", "end": "20:00"}}  # 14 h
    with pytest.raises(SystemExit):
        validate_settings(bad)


# ── Breach detection ───────────────────────────────────────────────────────────

def test_breached_above_true_when_value_exceeds():
    assert _breached(1001.0, 1000.0, "above") is True

def test_breached_above_false_at_exactly_threshold():
    assert _breached(1000.0, 1000.0, "above") is False

def test_breached_above_false_when_below():
    assert _breached(999.0, 1000.0, "above") is False

def test_breached_below_true_when_value_under():
    assert _breached(9.0, 10.0, "below") is True

def test_breached_below_false_at_exactly_threshold():
    assert _breached(10.0, 10.0, "below") is False

def test_breached_below_false_when_above():
    assert _breached(11.0, 10.0, "below") is False


def test_near_or_breached_exactly_at_threshold_above():
    assert _near_or_breached(1000.0, 1000.0, "above") is True

def test_near_or_breached_exactly_at_threshold_below():
    assert _near_or_breached(10.0, 10.0, "below") is True

def test_near_or_breached_within_margin_above():
    margin = 1000.0 * ALERT_NEAR_PCT / 100
    assert _near_or_breached(1000.0 - margin, 1000.0, "above") is True

def test_near_or_breached_outside_margin_above():
    margin = 1000.0 * ALERT_NEAR_PCT / 100
    assert _near_or_breached(1000.0 - margin - 1, 1000.0, "above") is False

def test_near_or_breached_within_margin_below():
    margin = 10.0 * ALERT_NEAR_PCT / 100
    assert _near_or_breached(10.0 + margin, 10.0, "below") is True

def test_near_or_breached_outside_margin_below():
    margin = 10.0 * ALERT_NEAR_PCT / 100
    assert _near_or_breached(10.0 + margin + 1, 10.0, "below") is False


# ── Alert buffer ───────────────────────────────────────────────────────────────

def test_buffer_alert_appends_to_in_memory_buffer(tmp_db):
    alert = {
        "metric": "co2", "value": 1500, "threshold": 800,
        "recorded_at": "2026-06-23T10:00:00+00:00",
    }
    _buffer_alert(alert)
    assert len(ingest._alert_buffer) == 1
    assert queue.get_pending_alerts() == []   # SQLite must not be touched yet


def test_buffer_alert_flushes_to_sqlite_at_capacity(tmp_db):
    alert = {
        "metric": "co2", "value": 1500, "threshold": 800,
        "recorded_at": "2026-06-23T10:00:00+00:00",
    }
    for _ in range(ALERT_BUFFER_CAPACITY):
        _buffer_alert(alert)
    assert ingest._alert_buffer == []
    assert len(queue.get_pending_alerts()) == ALERT_BUFFER_CAPACITY


# ── trigger_drain ──────────────────────────────────────────────────────────────

def test_trigger_drain_noop_before_event_created():
    """trigger_drain must not raise when _drain_trigger is None."""
    original = ingest._drain_trigger
    ingest._drain_trigger = None
    try:
        trigger_drain()
    finally:
        ingest._drain_trigger = original


async def test_trigger_drain_sets_event():
    original = ingest._drain_trigger
    ingest._drain_trigger = asyncio.Event()
    try:
        assert not ingest._drain_trigger.is_set()
        trigger_drain()
        assert ingest._drain_trigger.is_set()
    finally:
        ingest._drain_trigger = original


# ── _run_drain token guard ─────────────────────────────────────────────────────

async def test_run_drain_holds_buffer_in_memory_when_no_token(tmp_db, monkeypatch):
    """Buffer below capacity stays in memory when AUTH_TOKEN is absent."""
    monkeypatch.setenv("AUTH_TOKEN", "")
    ingest._buffer.append({
        "data": {"sen6x": {"co2": 400}},
        "recorded_at": "2026-06-23T10:00:00+00:00",
    })
    with patch("jobs.ingest.aggregate.run_aggregation",
               return_value={"buckets": 0, "rows_in": 0, "rows_removed": 0}):
        await _run_drain(S)

    assert len(ingest._buffer) == 1
    assert queue.count_pending() == 0


async def test_run_drain_flushes_to_sqlite_when_full_and_no_token(tmp_db, monkeypatch):
    """Buffer at capacity is flushed to SQLite when AUTH_TOKEN is absent."""
    monkeypatch.setenv("AUTH_TOKEN", "")
    monkeypatch.setattr(ingest, "BUFFER_CAPACITY", 5)
    entry = {
        "data": {"sen6x": {"co2": 400}},
        "recorded_at": "2026-06-23T10:00:00+00:00",
    }
    for _ in range(5):
        ingest._buffer.append(entry)

    with patch("jobs.ingest.aggregate.run_aggregation",
               return_value={"buckets": 0, "rows_in": 0, "rows_removed": 0}):
        await _run_drain(S)

    assert ingest._buffer == []
    assert queue.count_pending() == 5


# ── Buffer correction on transient spike ──────────────────────────────────────

async def test_do_verify_patches_nested_data_on_transient_spike():
    """Stage 1 avg well below threshold: breach value is replaced in nested data."""
    criterion = {
        "metric": "co2",
        "threshold": 1000.0,
        "condition": "above",
        "severity": "warning",
    }
    breach_entry = {
        "data": {"sen6x": {"co2": 1500, "temp": 25.0}},
        "recorded_at": "2026-06-23T10:00:00+00:00",
    }

    # Both Stage 1 reads return 400 → avg = 400, below 900 (near-zone edge) → transient
    with patch("asyncio.sleep", new_callable=AsyncMock), \
         patch("jobs.ingest._take_verify_read", new=AsyncMock(return_value=400.0)):
        await _do_verify("co2", criterion, breach_entry)

    corrected = breach_entry["data"]
    assert "sen6x" in corrected, "nested structure must be preserved"
    assert corrected["sen6x"]["co2"] == pytest.approx(400.0, abs=0.01)
    assert corrected["sen6x"]["temp"] == 25.0, "unrelated fields must be preserved"
    assert "co2" not in corrected, "metric must not be flattened to top level"


def test_buffer_correction_preserves_nested_shape():
    """Correction logic rebuilds the nested dict without flattening."""
    data   = {"sen6x": {"co2": 1500, "temp": 25.0}, "ts": "2026-06-23"}
    avg    = 399.1234
    metric = "co2"

    for sensor_name, sensor_data in data.items():
        if isinstance(sensor_data, dict) and metric in sensor_data:
            data = {
                **data,
                sensor_name: {**sensor_data, metric: round(avg, 4)},
            }
            break

    assert data["sen6x"]["co2"] == round(avg, 4)
    assert data["sen6x"]["temp"] == 25.0
    assert data["ts"] == "2026-06-23"
    assert "co2" not in data

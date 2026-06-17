from datetime import time
from jobs.ingest import current_read_interval, current_drain_interval, _window_hours, validate_settings

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
    import pytest
    bad = {**S, "active_window": {"start": "06:00", "end": "20:00"}}  # 14 h
    with pytest.raises(SystemExit):
        validate_settings(bad)

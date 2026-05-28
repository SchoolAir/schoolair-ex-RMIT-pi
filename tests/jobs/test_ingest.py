from datetime import time
from jobs.ingest import current_interval, _window_hours, validate_settings

# -------------------- Ingest Settings Test Suite -----------------------
#
# These tests cover that the functions used in ingest to ensure the
# interval is set correctly are working as expected.
#

ACTIVE_INTERVAL = 60
IDLE_INTERVAL = 300

S = {
        "interval_active": ACTIVE_INTERVAL, 
        "interval_idle": IDLE_INTERVAL,
        "active_window": {"start": "07:00", "end": "16:00"}
    }

def test_inside_window():
    assert current_interval(S, time(9, 0)) == ACTIVE_INTERVAL

def test_before_window():
    assert current_interval(S, time(6, 59)) == IDLE_INTERVAL

def test_start_is_inclusive():
    assert current_interval(S, time(7, 0)) == ACTIVE_INTERVAL   # >= start

def test_end_is_exclusive():
    assert current_interval(S, time(16, 0)) == IDLE_INTERVAL  # < end, so idle at exactly 16:00

def test_midnight_crossing_window():
    night = {**S, "active_window": {"start": "22:00", "end": "06:00"}}
    assert current_interval(night, time(23, 0)) == ACTIVE_INTERVAL   # late evening, active
    assert current_interval(night, time(2, 0))  == ACTIVE_INTERVAL   # small hours, active
    assert current_interval(night, time(12, 0)) == IDLE_INTERVAL  # midday, idle

def test_window_hours_handles_midnight():
    assert _window_hours({"start": "22:00", "end": "06:00"}) == 8
    assert _window_hours({"start": "07:00", "end": "16:00"}) == 9

def test_validate_rejects_long_window():
    import pytest
    bad = {**S, "active_window": {"start": "06:00", "end": "20:00"}}  # 14h
    with pytest.raises(SystemExit):
        validate_settings(bad)
        
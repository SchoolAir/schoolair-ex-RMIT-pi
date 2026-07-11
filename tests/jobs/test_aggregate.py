"""tests/jobs/test_aggregate.py

Simple behavioural tests for jobs.aggregate.

1. A timestamp can be grouped into an hourly bucket.
2. Sensor readings can be averaged.
3. Old queued readings from the same hour are folded into one row.
4. Recent readings are left alone.
"""

import json
from datetime import datetime, timezone, timedelta

import pytest

import db.queue as queue
import jobs.aggregate as aggregate


# ---------------------------------------------------------------------
# Test database setup
# ---------------------------------------------------------------------


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Use a fresh SQLite database for each test."""
    monkeypatch.setattr(queue, "DB_PATH", tmp_path / "test_queue.db")
    queue.init()


def seed_row(data: dict, recorded_at: datetime):
    """Insert one pending raw reading into the queue."""
    with queue._connect() as con:
        con.execute(
            """
            INSERT INTO measurements_queue
                (data, recorded_at, status, is_aggregated)
            VALUES
                (?, ?, 'pending', 0)
            """,
            (json.dumps(data), recorded_at.isoformat()),
        )


def get_rows():
    """Return all queued rows in send order."""
    with queue._connect() as con:
        return con.execute(
            """
            SELECT id, data, recorded_at, is_aggregated
            FROM measurements_queue
            ORDER BY id ASC
            """
        ).fetchall()


def sample_reading(**overrides):
    """A small realistic sensor reading.

    Includes a raw frame so we can check that aggregation removes it.
    """
    reading = {
        "temp": 20.0,
        "humidity": 50.0,
        "co2": 400,
        "raw": {
            "sen6x": {"co2": 400}
        },
    }

    reading.update(overrides)
    return reading


# ---------------------------------------------------------------------
# _bucket_start
# ---------------------------------------------------------------------


def test_bucket_start_truncates_timestamp_to_the_hour():
    recorded_at = "2026-05-01T14:23:45+00:00"

    bucket = aggregate._bucket_start(recorded_at)

    assert bucket == datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------
# _mean_data
# ---------------------------------------------------------------------


def test_mean_data_averages_known_sensor_values_and_drops_raw():
    readings = [
        sample_reading(temp=20.0, humidity=40.0, co2=400),
        sample_reading(temp=24.0, humidity=60.0, co2=600),
    ]

    averaged = aggregate._mean_data(readings)

    assert averaged["temp"] == 22.0
    assert averaged["humidity"] == 50.0
    assert averaged["co2"] == 500

    # The raw sensor frame can be large, so aggregated rows should not keep it.
    assert "raw" not in averaged


# ---------------------------------------------------------------------
# run_aggregation
# ---------------------------------------------------------------------


def test_run_aggregation_folds_old_readings_from_same_hour(db):
    """Two old raw readings from the same hour become one aggregated row."""

    old_hour = (
        datetime.now(timezone.utc) - timedelta(days=20)
    ).replace(minute=0, second=0, microsecond=0)

    seed_row(
        sample_reading(temp=20.0, humidity=40.0, co2=400),
        old_hour + timedelta(minutes=10),
    )

    seed_row(
        sample_reading(temp=24.0, humidity=60.0, co2=600),
        old_hour + timedelta(minutes=40),
    )

    summary = aggregate.run_aggregation(max_age_days=14)

    rows = get_rows()

    assert summary == {
        "buckets": 1,
        "rows_in": 2,
        "rows_removed": 1,
    }

    assert len(rows) == 1

    row = rows[0]
    data = json.loads(row["data"])

    assert row["is_aggregated"] == 1
    assert row["recorded_at"] == old_hour.isoformat()

    assert data["temp"] == 22.0
    assert data["humidity"] == 50.0
    assert data["co2"] == 500
    assert "raw" not in data


def test_mean_data_handles_nested_sensor_format():
    """Aggregation works on the new nested sensor data format."""
    readings = [
        {"sen6x": {"temp": 20.0, "humidity": 40.0, "co2": 400}},
        {"sen6x": {"temp": 24.0, "humidity": 60.0, "co2": 600}},
    ]
    averaged = aggregate._mean_data(readings)
    assert averaged["temp"] == 22.0
    assert averaged["humidity"] == 50.0
    assert averaged["co2"] == 500
    assert "sen6x" not in averaged    # output is always flat metric keys
    assert "raw" not in averaged


def test_run_aggregation_leaves_recent_readings_alone(db):
    """Rows newer than the retention window should stay raw."""

    recent_hour = (
        datetime.now(timezone.utc) - timedelta(days=2)
    ).replace(minute=0, second=0, microsecond=0)

    seed_row(sample_reading(temp=20.0), recent_hour + timedelta(minutes=10))
    seed_row(sample_reading(temp=24.0), recent_hour + timedelta(minutes=40))

    summary = aggregate.run_aggregation(max_age_days=14)

    rows = get_rows()

    assert summary == {
        "buckets": 0,
        "rows_in": 0,
        "rows_removed": 0,
    }

    assert len(rows) == 2
    assert all(row["is_aggregated"] == 0 for row in rows)

    for row in rows:
        data = json.loads(row["data"])
        assert "raw" in data
        
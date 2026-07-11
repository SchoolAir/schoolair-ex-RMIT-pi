"""tests/test_main.py

Unit tests for main.py: _graceful_shutdown flushes both in-memory
buffers to SQLite before cancelling asyncio tasks (SIGTERM path).
"""

from unittest.mock import patch

import pytest
import db.queue as queue
import jobs.ingest as ingest
import main


@pytest.fixture(autouse=True)
def clean_buffers():
    ingest._buffer.clear()
    ingest._alert_buffer.clear()
    yield
    ingest._buffer.clear()
    ingest._alert_buffer.clear()


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(queue, "DB_PATH", tmp_path / "test_queue.db")
    queue.init()


async def test_graceful_shutdown_flushes_measurement_buffer(tmp_db):
    ts = "2026-06-23T10:00:00+00:00"
    ingest._buffer.append({"data": {"sen6x": {"co2": 400}}, "recorded_at": ts})

    with patch("asyncio.all_tasks", return_value=[]):
        await main._graceful_shutdown()

    assert ingest._buffer == []
    assert queue.count_pending() == 1


async def test_graceful_shutdown_flushes_alert_buffer(tmp_db):
    ts = "2026-06-23T10:00:00+00:00"
    ingest._alert_buffer.append({"metric": "co2", "recorded_at": ts})

    with patch("asyncio.all_tasks", return_value=[]):
        await main._graceful_shutdown()

    assert ingest._alert_buffer == []
    assert len(queue.get_pending_alerts()) == 1


async def test_graceful_shutdown_flushes_both_buffers(tmp_db):
    ts = "2026-06-23T10:00:00+00:00"
    ingest._buffer.append({"data": {"sen6x": {"co2": 400}}, "recorded_at": ts})
    ingest._alert_buffer.append({"metric": "co2", "recorded_at": ts})

    with patch("asyncio.all_tasks", return_value=[]):
        await main._graceful_shutdown()

    assert ingest._buffer == []
    assert ingest._alert_buffer == []
    assert queue.count_pending() == 1
    assert len(queue.get_pending_alerts()) == 1


async def test_graceful_shutdown_empty_buffers_is_noop(tmp_db):
    """Calling _graceful_shutdown with empty buffers must not raise."""
    with patch("asyncio.all_tasks", return_value=[]):
        await main._graceful_shutdown()

    assert queue.count_pending() == 0
    assert queue.get_pending_alerts() == []

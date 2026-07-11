"""tests/conftest.py

Shared fixtures and pytest configuration for the SchoolAir gateway test suite.

Run laptop-safe tests only:
    pytest -m "not hardware"

Run everything (requires Raspberry Pi with SEN6x attached):
    pytest
"""

import pytest
import db.queue as queue


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "hardware: requires Raspberry Pi with attached sensor — "
        "skip on laptop with: pytest -m 'not hardware'",
    )


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Fresh SQLite DB in a temp dir, patched into queue.DB_PATH."""
    monkeypatch.setattr(queue, "DB_PATH", tmp_path / "test_queue.db")
    queue.init()


@pytest.fixture
def fake_settings():
    return {
        "active_window": {"start": "07:00", "end": "16:00"},
    }

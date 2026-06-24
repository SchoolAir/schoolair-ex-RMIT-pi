"""tests/registration_wizard/test_wizard.py

Unit tests for registration_wizard/wizard.py: _has_token (reads AUTH_TOKEN
from the telemetry .env) and the _idle_watchdog early-exit paths.

The conftest.py in this directory adds registration_wizard/ to sys.path
so that wizard.py's bare `from config import ...` resolves correctly.
"""

from unittest.mock import AsyncMock, patch

import pytest
import wizard


# ── _has_token ─────────────────────────────────────────────────────────────────

def test_has_token_true_when_env_file_has_token(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("SERVER_URL=http://example.com\nAUTH_TOKEN=abc123\n")
    monkeypatch.setattr(wizard, "PI_MAIN_ENV_PATH", str(env))
    assert wizard._has_token() is True


def test_has_token_false_when_token_value_is_empty(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("AUTH_TOKEN=\n")
    monkeypatch.setattr(wizard, "PI_MAIN_ENV_PATH", str(env))
    assert wizard._has_token() is False


def test_has_token_false_when_token_is_only_whitespace(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("AUTH_TOKEN=   \n")
    monkeypatch.setattr(wizard, "PI_MAIN_ENV_PATH", str(env))
    assert wizard._has_token() is False


def test_has_token_false_when_file_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(wizard, "PI_MAIN_ENV_PATH", str(tmp_path / "missing.env"))
    assert wizard._has_token() is False


def test_has_token_true_with_no_trailing_newline(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("AUTH_TOKEN=tok456")
    monkeypatch.setattr(wizard, "PI_MAIN_ENV_PATH", str(env))
    assert wizard._has_token() is True


# ── _idle_watchdog early exits ─────────────────────────────────────────────────

async def test_idle_watchdog_returns_immediately_in_ap_mode():
    """In AP mode the wizard self-shuts after registration; watchdog steps aside."""
    with patch("asyncio.sleep", new_callable=AsyncMock), \
         patch("wizard._ap_is_active", new=AsyncMock(return_value=True)):
        await wizard._idle_watchdog()   # must return without entering the while loop


async def test_idle_watchdog_returns_immediately_without_token():
    """On LAN without a token (first registration), watchdog must stay out of the way."""
    with patch("asyncio.sleep", new_callable=AsyncMock), \
         patch("wizard._ap_is_active", new=AsyncMock(return_value=False)), \
         patch("wizard._has_token", return_value=False):
        await wizard._idle_watchdog()   # must return without entering the while loop

"""tests/test_setup.py

Unit tests for setup.py: write_env_token and check_registration.
"""

from unittest.mock import patch

import pytest
import setup


def test_write_env_token_creates_file_if_absent(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    monkeypatch.setattr(setup, "ENV_PATH", env_file)
    setup.write_env_token("abc123")
    assert "AUTH_TOKEN=abc123" in env_file.read_text()


def test_write_env_token_updates_existing_token(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("SERVER_URL=http://example.com\nAUTH_TOKEN=oldtoken\n")
    monkeypatch.setattr(setup, "ENV_PATH", env_file)
    setup.write_env_token("newtoken")
    content = env_file.read_text()
    assert "AUTH_TOKEN=newtoken" in content
    assert "AUTH_TOKEN=oldtoken" not in content
    assert "SERVER_URL=http://example.com" in content


def test_write_env_token_appends_if_key_absent(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("SERVER_URL=http://example.com\n")
    monkeypatch.setattr(setup, "ENV_PATH", env_file)
    setup.write_env_token("tok456")
    assert "AUTH_TOKEN=tok456" in env_file.read_text()


def test_check_registration_returns_false_without_token(monkeypatch):
    monkeypatch.delenv("AUTH_TOKEN", raising=False)
    assert setup.check_registration() is False


def test_check_registration_returns_true_with_valid_token(monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN", "tok123")
    with patch("setup.validate_token", return_value=True):
        assert setup.check_registration() is True


def test_check_registration_returns_true_when_server_unreachable(monkeypatch):
    """Server errors must not block startup — readings queue locally instead."""
    monkeypatch.setenv("AUTH_TOKEN", "tok123")
    with patch("setup.validate_token", side_effect=Exception("unreachable")):
        assert setup.check_registration() is True


def test_check_registration_returns_true_on_non_2xx(monkeypatch):
    """Non-2xx validation is a warning, not a gate."""
    monkeypatch.setenv("AUTH_TOKEN", "tok123")
    with patch("setup.validate_token", return_value=False):
        assert setup.check_registration() is True

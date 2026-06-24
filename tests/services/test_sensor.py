"""tests/services/test_sensor.py

Unit tests for services.sensor.

  LAPTOP-SAFE: extract_metric(), read_sensor() with mocked subprocess.
  HARDWARE:    read_sensor() against the real SEN6x sensor.
"""

import json
import subprocess
from unittest.mock import patch, MagicMock

import pytest

import services.sensor as sensor_module
from services.sensor import extract_metric, read_sensor


@pytest.fixture(autouse=True)
def reset_sensor_state():
    """Reset module-level failure counter between tests."""
    sensor_module._consecutive_failures = 0
    yield
    sensor_module._consecutive_failures = 0


# ── extract_metric ─────────────────────────────────────────────────────────────

def test_extract_metric_finds_value_in_sensor_dict():
    data = {"sen6x": {"co2": 1504, "temp": 29.5}}
    assert extract_metric(data, "co2") == 1504.0


def test_extract_metric_always_returns_float():
    data = {"sen6x": {"co2": 1504}}  # raw output is int
    result = extract_metric(data, "co2")
    assert result == 1504.0
    assert isinstance(result, float)


def test_extract_metric_returns_none_when_metric_absent():
    data = {"sen6x": {"temp": 29.5}}
    assert extract_metric(data, "co2") is None


def test_extract_metric_returns_none_for_empty_data():
    assert extract_metric({}, "co2") is None


def test_extract_metric_skips_non_dict_top_level_values():
    data = {"timestamp": "2026-06-23", "sen6x": {"co2": 400}}
    assert extract_metric(data, "co2") == 400.0


def test_extract_metric_first_sensor_wins_on_conflict():
    data = {
        "sen6x": {"temp": 29.0},
        "bmp280": {"temp": 25.0},
    }
    assert extract_metric(data, "temp") == 29.0


def test_extract_metric_falls_back_to_second_sensor():
    data = {
        "sen6x": {"co2": 400},
        "bmp280": {"temp": 25.0},
    }
    assert extract_metric(data, "temp") == 25.0


def test_extract_metric_ignores_non_numeric_values():
    data = {"sen6x": {"co2": "n/a", "temp": 29.0}}
    assert extract_metric(data, "co2") is None
    assert extract_metric(data, "temp") == 29.0


# ── read_sensor ────────────────────────────────────────────────────────────────

_MOCK_OUTPUT = json.dumps({
    "sen6x": {
        "co2": 1504,
        "pm10": 3.7,
        "pm25": 5.1,
        "pm40": 6.2,
        "pm100": 6.8,
        "temp": 29.54,
        "humidity": 51.22,
        "timestamp": "2026-06-23 10:25:56",
    }
})


def _mock_run(stdout=_MOCK_OUTPUT, returncode=0, stderr=""):
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


def test_read_sensor_returns_nested_dict():
    with patch("subprocess.run", return_value=_mock_run()):
        data = read_sensor()
    assert "sen6x" in data
    assert data["sen6x"]["co2"] == 1504


def test_read_sensor_no_flat_top_level_fields():
    """No flattening — top level should only contain sensor keys."""
    with patch("subprocess.run", return_value=_mock_run()):
        data = read_sensor()
    assert "co2" not in data
    assert "temp" not in data
    assert "raw" not in data


def test_read_sensor_preserves_sensor_timestamp():
    with patch("subprocess.run", return_value=_mock_run()):
        data = read_sensor()
    assert data["sen6x"]["timestamp"] == "2026-06-23 10:25:56"


def test_read_sensor_multi_sensor_output():
    multi = json.dumps({
        "sen6x": {"co2": 400, "temp": 22.0},
        "mgs": {"no2": 0.05, "voc": 1.2},
    })
    with patch("subprocess.run", return_value=_mock_run(stdout=multi)):
        data = read_sensor()
    assert "sen6x" in data
    assert "mgs" in data


def test_read_sensor_raises_on_nonzero_exit():
    with patch("subprocess.run", return_value=_mock_run(returncode=1, stderr="I2C error")):
        with pytest.raises(RuntimeError, match="Sensor script failed"):
            read_sensor()


def test_read_sensor_raises_on_invalid_json():
    with patch("subprocess.run", return_value=_mock_run(stdout="not json at all")):
        with pytest.raises(RuntimeError, match="invalid JSON"):
            read_sensor()


def test_read_sensor_raises_on_timeout():
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 10)):
        with pytest.raises(RuntimeError, match="timed out"):
            read_sensor()


# ── Re-init / failure counting ─────────────────────────────────────────────────

def test_failure_counter_increments_on_error():
    with patch("subprocess.run", return_value=_mock_run(returncode=1)):
        with pytest.raises(RuntimeError):
            read_sensor()
    assert sensor_module._consecutive_failures == 1


def test_failure_counter_resets_on_success():
    sensor_module._consecutive_failures = 3
    good = json.dumps({"sen6x": {"temp": 22.0}})
    with patch("subprocess.run", return_value=_mock_run(stdout=good)):
        read_sensor()
    assert sensor_module._consecutive_failures == 0


def test_reinit_triggered_at_threshold(monkeypatch):
    monkeypatch.setattr(sensor_module, "_REINIT_BIN", "/fake/sen6x_read")
    with patch("subprocess.run", return_value=_mock_run(returncode=1)):
        with patch.object(sensor_module, "_try_reinit") as mock_reinit:
            for _ in range(sensor_module._REINIT_AFTER):
                with pytest.raises(RuntimeError):
                    read_sensor()
    mock_reinit.assert_called_once()


def test_reinit_not_triggered_before_threshold(monkeypatch):
    monkeypatch.setattr(sensor_module, "_REINIT_BIN", "/fake/sen6x_read")
    with patch("subprocess.run", return_value=_mock_run(returncode=1)):
        with patch.object(sensor_module, "_try_reinit") as mock_reinit:
            for _ in range(sensor_module._REINIT_AFTER - 1):
                with pytest.raises(RuntimeError):
                    read_sensor()
    mock_reinit.assert_not_called()


def test_reinit_skipped_when_reinit_bin_empty(monkeypatch):
    monkeypatch.setattr(sensor_module, "_REINIT_BIN", "")
    sensor_module._consecutive_failures = sensor_module._REINIT_AFTER - 1
    with patch("subprocess.run", return_value=_mock_run(returncode=1)):
        with patch.object(sensor_module, "_try_reinit") as mock_reinit:
            with pytest.raises(RuntimeError):
                read_sensor()
    mock_reinit.assert_not_called()


# ── Hardware ───────────────────────────────────────────────────────────────────

@pytest.mark.hardware
def test_read_sensor_real_hardware():
    """Call the actual sensor script. Requires Pi + sen6x.service initialised."""
    data = read_sensor()
    assert "sen6x" in data
    assert isinstance(extract_metric(data, "co2"), float)
    assert isinstance(extract_metric(data, "temp"), float)

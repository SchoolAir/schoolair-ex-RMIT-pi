# Automated Test Suite

87 tests across 7 modules. All tests are laptop-safe except one hardware test
that calls the real SEN6x I2C daemon on a Pi.

---

## Running the tests

**Laptop (no Pi required):**
```bash
cd gateway/
pytest -m "not hardware"          # skip the one hardware test
```

**Pi (full suite including hardware):**
```bash
cd /home/admin/schoolair/
pytest                            # runs everything
```

**Deps not yet installed?**
```bash
pip install pytest pytest-asyncio httpx python-dotenv questionary "microdot[websocket]"
```
All tests are async-aware via `asyncio_mode = "auto"` (set in `pyproject.toml`).

---

## Suite overview

| File | Tests | Hardware | Covers |
|---|---|---|---|
| `db/test_queue.py` | 18 | — | SQLite measurements + alerts queue |
| `jobs/test_aggregate.py` | 5 | — | Hourly aggregation of old rows |
| `jobs/test_ingest.py` | 30 | — | Scheduling, breach detection, alert buffer, drain |
| `registration_wizard/test_wizard.py` | 7 | — | Token detection, idle watchdog exits |
| `services/test_sensor.py` | 16 | 1 | Sensor data parsing + subprocess call |
| `test_main.py` | 4 | — | SIGTERM flush of both buffers to SQLite |
| `test_setup.py` | 7 | — | `.env` token write + startup registration gate |
| **Total** | **87** | **1** | |

---

## `db/test_queue.py` — SQLite queue (18 tests)

Tests the two-table SQLite queue (`measurements_queue` and `alerts_queue`) that
serves as the offline buffer when the server is unreachable. Every test runs
against a fresh in-memory-backed DB via the `tmp_db` fixture.

**Initialisation**
- `test_init_is_idempotent` — calling `init()` twice does not raise or corrupt data
- `test_init_resets_sending_status_to_pending` — rows stuck in `"sending"` at crash time are reset to `"pending"` on restart so they are retried

**Measurements queue**
- `test_enqueue_and_get_pending` — enqueued rows come back with correct data
- `test_get_pending_returns_in_insertion_order` — oldest rows first (chronological drain)
- `test_get_pending_respects_limit` — `limit=` cap is honoured
- `test_count_pending` — returns correct count
- `test_set_status_many_marks_as_sending` — rows marked `"sending"` are excluded from `get_pending`
- `test_remove_many_deletes_specified_rows` — sent rows are deleted; others remain
- `test_remove_many_empty_list_is_noop` — empty id list does not raise or delete anything

**Aggregation helpers**
- `test_get_aggregatable_returns_only_old_unaggregated_rows` — only rows older than the retention cutoff are returned for folding; recent rows are excluded
- `test_fold_bucket_rewrites_keeper_drops_others` — the earliest row is rewritten to the mean, all others in the bucket are deleted, `is_aggregated` is set, `recorded_at` is set to bucket start
- `test_trim_aggregated_only_removes_aggregated_rows` — trimming the queue cap only removes aggregated rows; raw rows survive
- `test_trim_aggregated_zero_count_is_noop` — trim with `count=0` returns 0 and deletes nothing

**Alerts queue**
- `test_enqueue_alert_and_get_pending_alerts` — alert stored and retrieved correctly
- `test_alerts_returned_in_insertion_order` — FIFO ordering preserved
- `test_set_alert_status_many` — alert marked `"sending"` is excluded from pending
- `test_remove_alerts` — sent alert deleted
- `test_alert_init_resets_sending_to_pending` — same crash-recovery guarantee as the measurements queue

---

## `jobs/test_aggregate.py` — Hourly aggregation (5 tests)

Tests the local data retention logic: readings older than the window are folded
into one hourly mean row per hour bucket.

- `test_bucket_start_truncates_timestamp_to_the_hour` — `_bucket_start("2026-05-01T14:23:45")` returns `14:00:00`
- `test_mean_data_averages_known_sensor_values_and_drops_raw` — flat-format readings (`{"temp": 20, ...}`) are averaged correctly; unknown keys like `"raw"` are dropped from the output
- `test_mean_data_handles_nested_sensor_format` — nested-format readings (`{"sen6x": {"temp": 20, ...}}`) are averaged correctly; sensor sub-dict key is not forwarded to the output
- `test_run_aggregation_folds_old_readings_from_same_hour` — two raw rows 20 days old in the same hour become one aggregated row with the averaged values; summary counts are correct
- `test_run_aggregation_leaves_recent_readings_alone` — rows within the retention window are untouched

---

## `jobs/test_ingest.py` — Ingest pipeline (30 tests)

The largest module. Covers scheduling, breach detection, alert buffering, the
`trigger_drain` event mechanism, the no-token drain guard, and the nested-data
buffer correction that fires on a transient spike.

**Read interval scheduling** (5 tests)
- `test_read_inside_window` / `test_read_before_window` — active vs idle interval selected correctly
- `test_read_start_is_inclusive` / `test_read_end_is_exclusive` — window boundary semantics
- `test_read_midnight_crossing` — windows that wrap past midnight work in all three time zones (before, inside, outside)

**Drain interval scheduling** (3 tests)
- Mirror of the read tests for the drain timers

**Window helpers** (2 tests)
- `test_window_hours_handles_midnight` — `_window_hours` counts hours correctly across midnight
- `test_validate_rejects_long_window` — `validate_settings` exits if the active window exceeds 9 hours

**Breach detection** (12 tests)
- `_breached` (6 tests): `"above"` condition is `value > threshold` (exclusive at the line); `"below"` is `value < threshold` (exclusive); both return `False` at exactly the threshold
- `_near_or_breached` (6 tests): verifies the near-zone margin (default 10% of threshold) is applied correctly for both conditions — values inside the margin return `True`, values outside return `False`

**Alert buffer** (2 tests)
- `test_buffer_alert_appends_to_in_memory_buffer` — alert goes into `_alert_buffer`; SQLite untouched
- `test_buffer_alert_flushes_to_sqlite_at_capacity` — when the in-memory buffer hits `ALERT_BUFFER_CAPACITY`, all alerts are flushed to SQLite and the buffer is cleared

**`trigger_drain` event** (2 tests)
- `test_trigger_drain_noop_before_event_created` — calling before the drain loop starts (`_drain_trigger is None`) must not raise
- `test_trigger_drain_sets_event` — after the event is created, `trigger_drain()` sets it so the drain loop wakes early

**`_run_drain` no-token guard** (2 async tests)
- `test_run_drain_holds_buffer_in_memory_when_no_token` — when `AUTH_TOKEN` is absent and the buffer is below capacity, readings stay in memory and SQLite is not written
- `test_run_drain_flushes_to_sqlite_when_full_and_no_token` — when buffer is at capacity with no token, readings spill to SQLite and the buffer is cleared

**Buffer correction on transient spike** (2 tests)
- `test_do_verify_patches_nested_data_on_transient_spike` — full async test of `_do_verify`: Stage 1 reads return a value well below threshold, so the breach entry's nested data dict is rewritten with the Stage 1 average; the sensor sub-dict key is preserved and the metric is not flattened to the top level
- `test_buffer_correction_preserves_nested_shape` — unit test of the dict-rewriting logic alone: spread operator rebuild preserves all other fields in the sensor dict and at the top level

---

## `registration_wizard/test_wizard.py` — Wizard helpers (7 tests)

Tests the parts of `wizard.py` that are pure logic and do not require a running
browser session or network interface.

**`_has_token`** (5 tests) — reads `AUTH_TOKEN` from the telemetry `.env` file
- `test_has_token_true_when_env_file_has_token` — file exists with a non-empty token → `True`
- `test_has_token_false_when_token_value_is_empty` — `AUTH_TOKEN=` with no value → `False`
- `test_has_token_false_when_token_is_only_whitespace` — `AUTH_TOKEN=   ` → `False`
- `test_has_token_false_when_file_absent` — file does not exist (OSError) → `False`
- `test_has_token_true_with_no_trailing_newline` — last line without newline still parsed correctly → `True`

**`_idle_watchdog` early exits** (2 async tests) — the watchdog shuts down the wizard after idle timeout, but only in certain modes
- `test_idle_watchdog_returns_immediately_in_ap_mode` — in AP mode the wizard manages its own lifecycle; watchdog exits after the 5s settle
- `test_idle_watchdog_returns_immediately_without_token` — on LAN with no token (first registration), watchdog must stay out of the way so the user can complete registration; exits immediately without entering the timeout loop

---

## `services/test_sensor.py` — Sensor module (16 tests, 1 hardware)

Tests `extract_metric()` and `read_sensor()` from `services/sensor.py`.

**`extract_metric`** (8 tests) — traverses nested sensor dicts to extract a named metric
- `test_extract_metric_finds_value_in_sensor_dict` — basic case: finds `co2` inside `{"sen6x": {...}}`
- `test_extract_metric_always_returns_float` — even when the raw value is an `int`
- `test_extract_metric_returns_none_when_metric_absent` — metric not in any sub-dict → `None`
- `test_extract_metric_returns_none_for_empty_data` — empty top-level dict → `None`
- `test_extract_metric_skips_non_dict_top_level_values` — non-dict values (e.g. `"timestamp": "..."`) are skipped without error
- `test_extract_metric_first_sensor_wins_on_conflict` — if two sensors report the same metric, the first one's value is returned
- `test_extract_metric_falls_back_to_second_sensor` — if the first sensor does not have the metric, the second is checked
- `test_extract_metric_ignores_non_numeric_values` — string like `"n/a"` is not returned; only `int` or `float`

**`read_sensor`** (7 tests, mocked subprocess)
- `test_read_sensor_returns_nested_dict` — output is `{"sen6x": {...}}`, not flat
- `test_read_sensor_no_flat_top_level_fields` — `co2`, `temp`, `raw` are not present at the top level
- `test_read_sensor_preserves_sensor_timestamp` — timestamp inside the sensor sub-dict is forwarded unchanged
- `test_read_sensor_multi_sensor_output` — JSON with two sensor keys is returned as-is
- `test_read_sensor_raises_on_nonzero_exit` — non-zero return code raises `RuntimeError("Sensor script failed")`
- `test_read_sensor_raises_on_invalid_json` — non-JSON stdout raises `RuntimeError("invalid JSON")`
- `test_read_sensor_raises_on_timeout` — `subprocess.TimeoutExpired` raises `RuntimeError("timed out")`

**Hardware** (1 test — Pi only, `@pytest.mark.hardware`)
- `test_read_sensor_real_hardware` — calls the actual SEN6x C daemon over I2C; asserts the result contains a `sen6x` key and that `co2` and `temp` are valid floats

---

## `test_main.py` — SIGTERM handler (4 async tests)

Tests `_graceful_shutdown()` in `main.py`, which is called on SIGTERM
(`systemctl stop`, `sudo reboot`, `sudo poweroff`). It must flush both
in-memory buffers to SQLite before cancelling asyncio tasks, so readings and
alerts are not lost on a clean shutdown.

- `test_graceful_shutdown_flushes_measurement_buffer` — one reading in `_buffer` → ends up in `measurements_queue`, buffer cleared
- `test_graceful_shutdown_flushes_alert_buffer` — one alert in `_alert_buffer` → ends up in `alerts_queue`, buffer cleared
- `test_graceful_shutdown_flushes_both_buffers` — one of each → both queues have one row, both buffers empty
- `test_graceful_shutdown_empty_buffers_is_noop` — called with both buffers empty → no rows written, no error

---

## `test_setup.py` — Registration gate (7 tests)

Tests `write_env_token()` and `check_registration()` from `setup.py`.

**`write_env_token`** (3 tests) — writes `AUTH_TOKEN=<value>` to the `.env` file
- `test_write_env_token_creates_file_if_absent` — file does not exist → created with the token line
- `test_write_env_token_updates_existing_token` — `AUTH_TOKEN=old` is replaced in-place; other lines (e.g. `SERVER_URL`) are preserved
- `test_write_env_token_appends_if_key_absent` — file exists but has no `AUTH_TOKEN` line → token is appended

**`check_registration`** (4 tests) — non-interactive startup gate used by `main.py`
- `test_check_registration_returns_false_without_token` — no `AUTH_TOKEN` env var → `False` (device will buffer locally)
- `test_check_registration_returns_true_with_valid_token` — token present and server confirms valid → `True`
- `test_check_registration_returns_true_when_server_unreachable` — token present but server throws → still `True` (ingest loop starts and queues readings)
- `test_check_registration_returns_true_on_non_2xx` — server returns non-2xx validation response → still `True` (warning only, not a gate)

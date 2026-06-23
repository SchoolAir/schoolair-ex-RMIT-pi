"""services/trigger.py

Prompts the sen6x C daemon to produce a fresh measurement on demand.

The daemon owns the I2C bus and samples every 60 s.  Sending it SIGUSR1 makes
it break out of its sleep and read the sensor immediately, writing the result
to sen6x.json within ~100 ms.  This lets the Python verification routine take
sub-minute readings without touching I2C directly.
"""

import asyncio
import os
import signal
import subprocess
from pathlib import Path

SENSOR_JSON = Path("/home/admin/i2c/sen6x/sen6x.json")


def _get_daemon_pid() -> int | None:
    """Return the MainPID of sen6x.service, or None if unavailable."""
    try:
        r = subprocess.run(
            ["systemctl", "show", "-p", "MainPID", "--value", "sen6x.service"],
            capture_output=True, text=True, timeout=3,
        )
        pid = int(r.stdout.strip())
        return pid if pid > 0 else None
    except (ValueError, subprocess.SubprocessError):
        return None


async def trigger_fresh_sample(timeout: float = 5.0) -> bool:
    """Send SIGUSR1 to the sen6x daemon and wait for a fresh JSON write.

    Returns True once sen6x.json mtime advances (fresh sample confirmed).
    Returns False if the daemon is unreachable or no write appears within timeout.
    """
    pid = _get_daemon_pid()
    if pid is None:
        return False

    before = SENSOR_JSON.stat().st_mtime if SENSOR_JSON.exists() else 0.0

    try:
        os.kill(pid, signal.SIGUSR1)
    except ProcessLookupError:
        return False

    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        await asyncio.sleep(0.1)
        try:
            if SENSOR_JSON.stat().st_mtime > before:
                return True
        except FileNotFoundError:
            pass

    return False

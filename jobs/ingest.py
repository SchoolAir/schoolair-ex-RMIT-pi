"""jobs/ingest.py

Reads sensor data on a fixed interval and POSTs to the server ingest endpoint.
On failure, logs the error for now (SQLite retry queue to be added later).
"""

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import httpx
from dotenv import load_dotenv
from services.sensor import read_sensor
load_dotenv()

SERVER_URL  = os.getenv("SERVER_URL", "").rstrip("/")
INTERVAL    = int(os.getenv("INGEST_INTERVAL", 60))  # seconds, default 1 min


async def ingest_loop():
    """Runs forever. Reads sensor every INTERVAL seconds and ships to server."""
    print(f"Ingest job started — running every {INTERVAL}s")

    while True:
        await _run_ingest()
        await asyncio.sleep(INTERVAL)


async def _run_ingest():
    recorded_at = datetime.now(timezone.utc).isoformat()
    token = os.getenv("AUTH_TOKEN", "").strip()

    try:
        data = read_sensor()
    except RuntimeError as e:
        print(f"Sensor read failed: {e}")
        return

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.post(
                f"{SERVER_URL}/aqc/v1/ingest",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={"recorded_at": recorded_at, "data": data},
            )

            if res.status_code == 401:
                print("Ingest failed: auth token rejected!")
                return

            if not res.is_success:
                raise RuntimeError(f"Server responded with {res.status_code}")

            print(f"Ingested at {recorded_at}")

    except httpx.ConnectError:
        print(f"Ingest failed: could not reach server, will retry in {INTERVAL}s")
    except RuntimeError as e:
        print(f"Ingest failed: {e}, will retry in {INTERVAL}s")
        # TODO: enqueue to local SQLite for retry
"""state.py

Shared in-process state for the latest sensor reading.
Written by the ingest loop; read by the WebSocket dashboard handler.
"""

latest_data: dict | None = None
latest_recorded_at: str | None = None


def set(data: dict, recorded_at: str) -> None:
    global latest_data, latest_recorded_at
    latest_data = data
    latest_recorded_at = recorded_at

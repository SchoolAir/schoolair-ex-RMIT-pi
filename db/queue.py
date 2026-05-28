"""db/queue.py

Manages the local SQLite queue for measurements that failed to send.
On startup, resets any rows stuck in 'sending' back to 'pending' for retry.
"""

import sqlite3
import json
from pathlib import Path


DB_PATH = Path("queue.db")
DB_MAX_SEND = 100 # max rows returned from `get_pending`


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def init():
    """Create table and reset any stuck 'sending' rows on startup."""
    with _connect() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS measurements_queue (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                data        TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'pending'
            )
        """)
        # Reset rows stuck in 'sending' on last run back to 'pending'
        con.execute("""
            UPDATE measurements_queue
            SET status = 'pending'
            WHERE status = 'sending'
        """)


def enqueue(data: dict, recorded_at: str):
    """Add a failed measurement to the queue for later retry."""
    with _connect() as con:
        con.execute(
            "INSERT INTO measurements_queue (data, recorded_at) VALUES (?, ?)",
            (json.dumps(data), recorded_at)
        )


def get_pending(limit: int = DB_MAX_SEND) -> list[sqlite3.Row]:
    """Fetch up to `limit` pending measurements."""
    with _connect() as con:
        return con.execute(
            "SELECT * FROM measurements_queue WHERE status = 'pending' ORDER BY id ASC LIMIT ?",
            (limit,)
        ).fetchall()


def count_pending() -> int:
    """Return number of pending measurements in the queue."""
    with _connect() as con:
        row = con.execute(
            "SELECT COUNT(*) as count FROM measurements_queue WHERE status = 'pending'"
        ).fetchone()
        return row["count"]


def set_status_many(ids: list[int], status: str):
    if not ids:
        return
    with _connect() as con:
        con.executemany(
            "UPDATE measurements_queue SET status = ? WHERE id = ?",
            [(status, id) for id in ids]
        )
        

def remove_many(ids: list[int]):
    if not ids:
        return
    with _connect() as con:
        con.executemany(
            "DELETE FROM measurements_queue WHERE id = ?",
            [(id,) for id in ids]
        )

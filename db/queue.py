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
                status      TEXT NOT NULL DEFAULT 'pending',
                is_aggregated INTEGER NOT NULL DEFAULT 0
            )
        """)
        
        # Speeds up both get_pending and get_aggregatable, esp. during a long
        # outage when the queue grows large.
        con.execute("""
            CREATE INDEX IF NOT EXISTS idx_queue_status_recorded
            ON measurements_queue (status, recorded_at)
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


def get_aggregatable(before: str) -> list[sqlite3.Row]:
    """Pending, not-yet-aggregated rows recorded before the given ISO timestamp.
 
    Coarse pre-filter only: the caller applies the precise per-bucket cutoff.
    """
    with _connect() as con:
        return con.execute(
            """SELECT * FROM measurements_queue
               WHERE status = 'pending' AND is_aggregated = 0 AND recorded_at < ?
               ORDER BY recorded_at ASC""",
            (before,)
        ).fetchall()


def fold_bucket(keeper_id: int, data: dict, recorded_at: str, drop_ids: list[int]):
    """Atomically collapse one hourly bucket into a single aggregated row.
 
    The earliest row in the bucket (`keeper_id`) is rewritten to hold the
    aggregated data at the bucket-start timestamp and flagged is_aggregated;
    the remaining rows (`drop_ids`) are deleted. Single transaction, so a
    crash mid-fold leaves the bucket untouched rather than half-merged.
    """
    with _connect() as con:
        con.execute(
            """UPDATE measurements_queue
               SET data = ?, recorded_at = ?, is_aggregated = 1
               WHERE id = ?""",
            (json.dumps(data), recorded_at, keeper_id)
        )
        if drop_ids: # might be only one row in the bucket, so check before
            con.executemany(
                "DELETE FROM measurements_queue WHERE id = ?",
                [(i,) for i in drop_ids]
            )



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

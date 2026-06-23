"""jobs/aggregate.py

Local retention (phase 1): fold old raw queued readings into hourly means.

Readings older than AGGREGATE_AFTER_DAYS are grouped by clock hour and
collapsed into a single mean row per hour. The raw sensor frame is dropped,
and the surviving row is flagged is_aggregated so it is never folded again.

Folding is in-place: the earliest row in each bucket is kept (rewritten to
the mean) and the rest are deleted, so ids stay monotonic and the queue's
id-ordered drain remains chronological.

A bucket is only folded once its entire clock hour is past the cutoff, so
a partially-elapsed boundary hour never gets folded multiple times.

TODO: discarding aggregated rows when the queue is huge
"""

import os
import json
from collections import defaultdict
from datetime import datetime, timezone, timedelta

import db.queue as queue

AGGREGATE_AFTER_DAYS = 14 
# TODO: possibly make .env but for now it's a code-level 
# constantsince it's a bit of a niche config and not something 
# we expect to change often or per-deployment

MAX_QUEUE_ROWS = 10000  # guard against unbounded sqlite growth
QUEUE_LOW_WATER = 9500  # ^when triggered, trim down to here

METRIC_KEYS = {
    "temp":     "float",
    "humidity": "float",
    "pm10":     "float",
    "pm25":     "float",
    "pm40":     "float",
    "pm100":    "float",
    "co2":      "int",
    "voc":      "float",
    "nox":      "float",
    "no2":      "float",
}
# TODO: probably need a place to centralise this info but fine for now


def _bucket_start(recorded_at: str) -> datetime:
    """Truncate an ISO timestamp to the start of its clock hour (UTC)."""
    # e.g. "2024-05-01T14:23:45Z" -> "2024-05-01T14:00:00Z"
    dt = datetime.fromisoformat(recorded_at)
    return dt.replace(minute=0, second=0, microsecond=0)


def _mean_data(readings: list[dict]) -> dict:
    """Average known sensor metrics. Handles both nested (raw) and flat (aggregated) readings."""
    out = {}

    for key, kind in METRIC_KEYS.items():
        values = []

        for reading in readings:
            value = reading.get(key)
            if isinstance(value, (int, float)):
                values.append(value)

        if not values:
            continue

        avg = sum(values) / len(values)

        if kind == "int":
            out[key] = int(round(avg))
        else:
            out[key] = round(avg, 2)

    return out


def run_aggregation(max_age_days: int = AGGREGATE_AFTER_DAYS) -> dict:
    """Fold eligible rows into hourly means. Returns a summary dict."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max_age_days)

    rows = queue.get_aggregatable(cutoff.isoformat())
    if not rows:
        return {"buckets": 0, "rows_in": 0, "rows_removed": 0}

    # Group by clock hour, keeping only buckets whose whole hour is past cutoff
    buckets: dict[datetime, list] = defaultdict(list)
    for row in rows:
        start = _bucket_start(row["recorded_at"])
        if start + timedelta(hours=1) > cutoff:
            continue  # boundary hour not fully elapsed yet so leave it raw
        buckets[start].append(row)

    rows_in = 0
    rows_removed = 0
    for start, bucket in buckets.items():
        bucket.sort(key=lambda r: r["recorded_at"])  # earliest first = keeper
        keeper = bucket[0]
        drop_ids = [r["id"] for r in bucket[1:]]
        merged = _mean_data([json.loads(r["data"]) for r in bucket])
        
        queue.fold_bucket(keeper["id"], merged, start.isoformat(), drop_ids)

        rows_in += len(bucket)
        rows_removed += len(drop_ids)

    return {
        "buckets": len(buckets),
        "rows_in": rows_in,             # raw rows consumed
        "rows_removed": rows_removed,   # net rows deleted from the queue
    }


def main():
    """Standalone test entry point.

    Usage (from the /pi dir):
        python -m jobs.aggregate        # fold rows older than AGGREGATE_AFTER_DAYS
        python -m jobs.aggregate 0      # fold every fully-elapsed past hour now
    """
    import sys
    max_age = int(sys.argv[1]) if len(sys.argv) > 1 else AGGREGATE_AFTER_DAYS

    queue.init()
    before = queue.count_pending()
    summary = run_aggregation(max_age)
    after = queue.count_pending()

    print(f"Aggregation pass (folding rows older than {max_age}d):")
    print(f"  Pending before: {before}")
    print(f"  Buckets folded: {summary['buckets']}")
    print(f"  Raw rows in:    {summary['rows_in']}")
    print(f"  Rows removed:   {summary['rows_removed']}")
    print(f"  Pending after:  {after}")


if __name__ == "__main__":
    main()

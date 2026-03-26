-- SchoolAir Database Schema
-- NOTE: db/queue.ts already creates the table

CREATE TABLE IF NOT EXISTS measurements_queue (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    data        TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending'
);
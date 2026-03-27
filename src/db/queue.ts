import Database from "better-sqlite3";
import path from "path";
import { QueuedMeasurement } from "../types/queue";

/**
 * Manages the sqlite database for the measurements queue. 
 * Provides functions to enqueue measurements, retrieve pending measurements, 
 * update their status, and remove them after processing.
 * Uses WAL journal mode for better concurrency and performance.
 * On startup, resets any measurements for retry.
 */

const db = new Database(path.join(__dirname, "../../queue.db"));

db.pragma("journal_mode = WAL");

// Init if not exists
db.exec(`
  CREATE TABLE IF NOT EXISTS measurements_queue (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    data        TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending'
  )
`);

// On boot, reset any rows stuck in 'sending' status back to 'pending' for retry
db.exec(`
  UPDATE measurements_queue
  SET status = 'pending'
  WHERE status = 'sending'
`);

export function enqueue(data: string, recorded_at: string): void {
  db.prepare(
    "INSERT INTO measurements_queue (data, recorded_at) VALUES (?, ?)"
  ).run(data, recorded_at);
}

export function getPending(limit: number): QueuedMeasurement[] {
  return db
    .prepare("SELECT * FROM measurements_queue WHERE status = 'pending' LIMIT ?")
    .all(limit) as QueuedMeasurement[];
}

export function countPending(): number {
  const row = db
    .prepare("SELECT COUNT(*) as count FROM measurements_queue WHERE status = 'pending'")
    .get() as { count: number };
  return row.count;
}

export function setStatus(id: number, status: QueuedMeasurement["status"]): void {
  db.prepare("UPDATE measurements_queue SET status = ? WHERE id = ?").run(status, id);
}

export function remove(id: number): void {
  db.prepare("DELETE FROM measurements_queue WHERE id = ?").run(id);
}

export default db;
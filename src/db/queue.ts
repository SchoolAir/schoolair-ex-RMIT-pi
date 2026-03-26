import Database from "better-sqlite3";
import path from "path";
import { QueuedMeasurement } from "../types/queue";

const db = new Database(path.join(__dirname, "../../queue.db"));

// Initialise the queue table if it doesn't exist
db.exec(`
  CREATE TABLE IF NOT EXISTS measurements_queue (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    data        TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending'
  )
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
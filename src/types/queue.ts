// Row shape for the local SQLite measurements queue
export interface QueuedMeasurement {
  id: number;
  data: string;        // JSON stringified sensor payload
  recorded_at: string; // ISO 8601 timestamp
  status: "pending" | "sending" | "failed";
}

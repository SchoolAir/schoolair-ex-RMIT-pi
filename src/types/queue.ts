/**
 * Defines the structure of a measurement queued for sending to the central server.
 */
export interface QueuedMeasurement {
  id: number;
  data: string;        // JSON stringified sensor payload
  recorded_at: string; // ISO 8601 timestamp
  status: "pending" | "sending" | "failed";
  retry_count: number; // number of retry attempts
}

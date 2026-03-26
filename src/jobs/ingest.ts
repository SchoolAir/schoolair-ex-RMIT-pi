import { enqueue } from "../db/queue";

/**
 * Reads the latest sensor data and POSTs to the central server ingest endpoint.
 * If the request fails, the measurement is saved to the local SQLite queue for retry.
 */
export async function sendIngest(): Promise<void> {
  // TODO: read sensor data and POST to ${process.env.SERVER_URL}/aqc/v1/ingest
  // On failure: enqueue(JSON.stringify(sensorData), recorded_at)
}

export function startIngestJob(): void {
  const interval = Number(process.env.INGEST_INTERVAL) || 3600000;
  setInterval(sendIngest, interval);
  console.log(`Ingest job started — running every ${interval / 1000}s`);
}
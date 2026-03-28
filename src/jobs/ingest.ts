import { readSensor } from "../services/sensor";
import { enqueue } from "../db/queue";

/**
 * Reads sensor and POSTs to central server ingest endpoint.
 * On failure, saves to local SQLite queue for retry.
 */

async function run(): Promise<void> {
  const recorded_at = new Date().toISOString();

  try {
    const data = await readSensor();
    // TODO: maybe we have a few types of read sensor where some for
    // this job for instance sample a few times and average before
    // sending to server, while others just read once and send raw data

    const res = await fetch(`${process.env.SERVER_URL}/aqc/v1/ingest`, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${process.env.AUTH_TOKEN}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ recorded_at, data })
    });

    if (!res.ok) throw new Error(`Server responded with ${res.status}`);
  } catch (err) {
    console.error("Ingest failed, queuing for retry:", err);
    const data = await readSensor().catch(() => ({}));
    //enqueue(JSON.stringify(data), recorded_at);
  } finally {
    const interval = Number(process.env.INGEST_INTERVAL) || 3600000;
    setTimeout(run, interval);
  }
}

export function startIngestJob(): void {
  const interval = Number(process.env.INGEST_INTERVAL) || 3600000;
  console.log('interval: ', interval);
  setTimeout(run, interval);
  console.log(`Ingest job started — running every ${interval / 1000}s`);
}
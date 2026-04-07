import { readSensor } from "../services/sensor";

/**
 * Reads sensor and POSTs to central server snapshot endpoint.
 * Fire and forget — if it fails we don't queue it.
 */

async function run(): Promise<void> {
  try {
    const data = await readSensor();
    const recorded_at = new Date().toISOString();

    await fetch(`${process.env.SERVER_URL}/aqc/v1/snapshot`, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${process.env.AUTH_TOKEN}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ recorded_at, data })
    });
  } catch {
    // Fire and forget — log but don't queue
    console.error("Snapshot failed, skipping");
  } finally {
    const interval = Number(process.env.SNAPSHOT_INTERVAL) || 300000;
    setTimeout(run, interval);
  }
}

export function startSnapshotJob(): void {
  const interval = Number(process.env.SNAPSHOT_INTERVAL) || 300000;
  setTimeout(run, interval);
  console.log(`Snapshot job started — running every ${interval / 1000}s`);
}
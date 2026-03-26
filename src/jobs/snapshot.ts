/**
 * Reads the latest sensor data and POSTs to the central server snapshot endpoint.
 * Fire and forget — if it fails, we don't queue it. Next snapshot comes in 5 minutes.
 */
export async function sendSnapshot(): Promise<void> {
  // TODO: read sensor data and POST to ${process.env.SERVER_URL}/aqc/v1/snapshot
}

export function startSnapshotJob(): void {
  const interval = Number(process.env.SNAPSHOT_INTERVAL) || 300000;
  setInterval(sendSnapshot, interval);
  console.log(`Snapshot job started — running every ${interval / 1000}s`);
}
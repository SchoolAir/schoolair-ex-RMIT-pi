import { countPending, getPending, setStatus, remove } from "../db/queue";

// If queue has fewer than 100 pending entries, flush all at once.
// Otherwise flush in batches of 100 per interval to avoid overwhelming the server.
// TODO: maybe move to .env?
const BATCH_SIZE = 100;

export async function flushQueue(): Promise<void> {
  const pending = countPending();

  if (pending === 0) return;

  const limit = pending < BATCH_SIZE ? pending : BATCH_SIZE;
  const entries = getPending(limit);

  console.log(`Flushing ${entries.length} of ${pending} queued entries`);

  for (const entry of entries) {
    setStatus(entry.id, "sending");
    // TODO: POST entry to ${process.env.SERVER_URL}/aqc/v1/ingest
    // On success: remove(entry.id)
    // On failure: setStatus(entry.id, "failed")
  }
}

export function startFlushJob(): void {
  const interval = Number(process.env.QUEUE_FLUSH_INTERVAL) || 1800000;
  setInterval(flushQueue, interval);
  console.log(`Queue flush job started — running every ${interval / 1000}s`);
}
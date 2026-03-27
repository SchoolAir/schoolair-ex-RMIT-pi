import { countPending, getPending, setStatus, remove } from "../db/queue";

/**
 * Periodically flushes queued measurements to the central server.
 * If queue has fewer than 100 pending entries, flush all at once.
 * Otherwise flush in batches of 100 per interval to avoid overwhelming the server.
 */

// TODO: maybe make batch size an .env variable and or change approach completely

const BATCH_SIZE = 100;

async function flushQueue(): Promise<void> {
  const pending = countPending();

  if (pending === 0) return;

  const limit = pending < BATCH_SIZE ? pending : BATCH_SIZE;
  const entries = getPending(limit);

  console.log(`Flushing ${entries.length} of ${pending} queued entries`);

  for (const entry of entries) {
    setStatus(entry.id, "sending");
    try {
      const res = await fetch(`${process.env.SERVER_URL}/aqc/v1/ingest`, {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${process.env.AUTH_TOKEN}`,
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          recorded_at: entry.recorded_at,
          data: JSON.parse(entry.data)
        })
      });

      if (!res.ok) throw new Error(`Server responded with ${res.status}`);
      remove(entry.id);
    } catch (err) {
      console.error(`Failed to flush entry ${entry.id}:`, err);
      setStatus(entry.id, "failed");
    }
  }
}

async function run(): Promise<void> {
  try {
    await flushQueue();
  } finally {
    const interval = Number(process.env.QUEUE_FLUSH_INTERVAL) || 1800000;
    setTimeout(run, interval);
  }
}

export function startFlushJob(): void {
  const interval = Number(process.env.QUEUE_FLUSH_INTERVAL) || 1800000;
  setTimeout(run, interval);
  console.log(`Queue flush job started — running every ${interval / 1000}s`);
}
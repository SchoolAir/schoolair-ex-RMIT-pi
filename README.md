# schoolair-pi
Raspberry Pi client for the SchoolAir platform.

## Overview

`src/`
- `index.ts` — entry point, starts all background jobs
- `db/queue.ts` — SQLite connection, WAL mode, queue helpers, boot cleanup
- `jobs/alert.ts` — reads sensor every 60s, checks thresholds, POSTs to central server if breached
- `jobs/snapshot.ts` — reads sensor every 5 mins, POSTs to central server (fire and forget)
- `jobs/ingest.ts` — reads sensor every 60 mins, POSTs to central server (queues on failure)
- `jobs/flushQueue.ts` — retries failed ingest entries every 30 mins, batches if queue > 100
- `services/sensor.ts` — executes sensor shell script, returns parsed JSON
- `services/threshold.ts` — checks sensor data against thresholds with global cooldown
- `types/queue.ts` — TypeScript interface for queued measurement rows

## How it works

```
Every 60s  → jobs/alert.ts      → readSensor() → checkThresholds() → POST /aqc/v1/alert if breached
Every 5m   → jobs/snapshot.ts   → readSensor() → POST /aqc/v1/snapshot
Every 60m  → jobs/ingest.ts     → readSensor() → POST /aqc/v1/ingest (queue if offline)
Every 30m  → jobs/flushQueue.ts → drain SQLite queue → retry failed ingest POSTs
```

## Setup

```bash
npm install
cp .env.example .env  # fill in AUTH_TOKEN and SERVER_URL after registering the device
```

Run in development:
```bash
npm run dev
```

Build for production:
```bash
npm run build
npm start
```

## Device Registration

On first boot, register the Pi against an organisation:

```bash
curl -X POST https://data.schoolair.org/aqc/register \
  -H "Authorization: Bearer <prov_token>" \
  -H "Content-Type: application/json" \
  -d '{"mac_address": "aa:bb:cc:dd:ee:ff", "nickname": "Room 3B"}'
```

Store the returned `auth_token` and `device_id` in `.env`.
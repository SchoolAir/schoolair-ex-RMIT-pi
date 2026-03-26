# schoolair-pi
Raspberry Pi client for the SchoolAir platform.

## Overview

`src/` — Express backend
- `index.ts` — entry point, mounts routes and starts background jobs
- `db/queue.ts` — SQLite connection and queue helper functions
- `jobs/snapshot.ts` — reads sensor and POSTs to central server every 5 minutes (fire and forget)
- `jobs/ingest.ts` — reads sensor and POSTs to central server every 60 minutes (queues on failure)
- `jobs/flushQueue.ts` — retries failed ingest entries every 30 minutes, batches if queue > 100
- `routes/sensor.ts` — `POST /sensor/read` — collect a sensor reading
- `routes/alert.ts` — `POST /sensor/alert` — check reading against thresholds, post alert if breached
- `routes/snapshot.ts` — `POST /sensor/snapshot` — send latest reading to central server
- `types/queue.ts` — TypeScript interface for queued measurement rows

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

On first boot, register the Pi against an organisation using the central server:

```bash
curl -X POST https://data.schoolair.org/aqc/register \
  -H "Authorization: Bearer <prov_token>" \
  -H "Content-Type: application/json" \
  -d '{"mac_address": "aa:bb:cc:dd:ee:ff", "nickname": "Room 3B"}'
```

Store the returned `auth_token` and `device_id` in `.env`.

## How it works

```
Every 5 mins  → read sensor → POST /aqc/v1/snapshot   (live dashboard, fire and forget)
Every 60 mins → read sensor → POST /aqc/v1/ingest     (historical data, queue if offline)
Every 30 mins → drain queue → retry failed ingest POSTs
                              < 100 entries → flush all
                              ≥ 100 entries → flush 100, remainder next interval
```
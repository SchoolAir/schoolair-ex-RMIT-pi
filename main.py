"""main.py

Entrypoint. Validates registration then starts:
  - Ingest job (reads sensors, posts measurements to server)
  - Alert job  (checks thresholds, fires alerts)
  - Microdot   (local web server for on-device status page)
"""

import asyncio
import os
from dotenv import load_dotenv
from microdot import Microdot, Response
from setup import ensure_registered
from jobs.ingest import ingest_loop
# from jobs.alert import alert_loop

load_dotenv()

PORT = int(os.getenv("PORT", 3001))
app = Microdot()

# ----------------------- Routes -----------------------

@app.get("/health")
async def health(request):
    return {"status": "ok"}


@app.get("/")
async def index(request):
    # TODO: expand into a student-facing status page later
    return Response(
        body="<h1>SchoolAir</h1><p>Device is running.</p>",
        headers={"Content-Type": "text/html"}
    )


# ----------------------- Entry point -----------------------

async def main():
    await asyncio.gather(
        ingest_loop(),
        # alert_loop(),
        app.start_server(host="0.0.0.0", port=PORT, debug=False),
    )
 
 
if __name__ == "__main__":
    try:
        ensure_registered()  # sync — runs before event loop starts
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutting down.")

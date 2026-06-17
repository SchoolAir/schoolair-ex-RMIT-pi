"""main.py

Entrypoint. Validates registration then starts:
  - Ingest job (reads sensors, checks alerts & posts measurements)
  - Microdot   (local web server: real-time dashboard + WebSocket)
"""

import asyncio
import json
import os
import socket
from pathlib import Path
from dotenv import load_dotenv
from microdot import Microdot, Response
from microdot.websocket import with_websocket
from setup import check_registration
from jobs.ingest import ingest_loop
import state

load_dotenv()

PORT     = int(os.getenv("PORT", 8080))
NICKNAME = os.getenv("DEVICE_NICKNAME", socket.gethostname())

app = Microdot()

STATIC_DIR = Path(__file__).parent / "static"


# ----------------------- Static helpers -----------------------

def _serve_file(path: str, content_type: str) -> Response:
    try:
        with open(path, "rb") as f:
            return Response(body=f.read(), headers={"Content-Type": content_type})
    except FileNotFoundError:
        return Response(status_code=404)


# ----------------------- Routes -----------------------

@app.get("/")
async def index(request):
    return _serve_file(STATIC_DIR / "dashboard.html", "text/html; charset=utf-8")


@app.get("/static/<path:path>")
async def static_files(request, path):
    # Prevent path traversal
    resolved = (STATIC_DIR / path).resolve()
    if not str(resolved).startswith(str(STATIC_DIR.resolve())):
        return Response(status_code=403)
    ext = path.rsplit(".", 1)[-1].lower()
    ct = {"js": "text/javascript", "css": "text/css", "html": "text/html"}.get(ext, "application/octet-stream")
    return _serve_file(resolved, ct)


@app.get("/health")
async def health(request):
    return {"status": "ok"}


@app.post("/re-register")
async def re_register(request):
    """Start the registration wizard (called from dashboard "Re-register" button)."""
    check = await asyncio.create_subprocess_exec(
        "systemctl", "is-active", "--quiet", "schoolair-wizard",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await check.wait()
    already_up = check.returncode == 0

    if not already_up:
        start = await asyncio.create_subprocess_exec(
            "sudo", "systemctl", "start", "schoolair-wizard",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await start.wait()

    host = (request.headers.get("Host") or "").split(":")[0]
    return {"url": f"http://{host}/", "already_up": already_up}


@app.route("/ws/sensors")
@with_websocket
async def ws_sensors(request, ws):
    """Push the latest sensor reading to the dashboard every 30 s."""
    while True:
        frame = {
            "temp":       state.latest_data.get("temp") if state.latest_data else None,
            "pm25":       state.latest_data.get("pm25") if state.latest_data else None,
            "registered": True,
            "nickname":   NICKNAME,
            "sent_at":    state.latest_recorded_at,
        }
        await ws.send(json.dumps(frame))
        await asyncio.sleep(30)


# ----------------------- Entry point -----------------------

async def main():
    await asyncio.gather(
        ingest_loop(),
        app.start_server(host="0.0.0.0", port=PORT, debug=False),
    )


if __name__ == "__main__":
    if not check_registration():
        raise SystemExit(1)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutting down.")

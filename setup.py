"""setup.py

Device commissioning and registration.

Run manually to register a device:   python -m setup

Also exposes check_registration(), a NON-interactive gate used by main.py
under systemd. It never prompts, and treats an unreachable server as
"proceed" so the ingest loop can start and queue locally.
"""

import os
import re
import json
import uuid
import httpx
import questionary
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

SERVER_URL = os.getenv("SERVER_URL", "").rstrip("/")
ENV_PATH = Path(".env")
HTTP_TIMEOUT = 10

# ----------------------- Helpers -----------------------

def print_banner():
    print("""
┌─────────────────────────────┐
│          SchoolAir          │
│     Device Registration     │
└─────────────────────────────┘
""")


def get_mac_address() -> str:
    """Return first non-loopback MAC address."""
    mac = uuid.getnode()
    if (mac >> 40) % 2:  # multicast bit set = not a real MAC
        raise RuntimeError("Unable to determine a real MAC address")
    return ":".join(f"{(mac >> (i * 8)) & 0xFF:02x}" for i in range(5, -1, -1))


def write_env_token(token: str):
    """Update AUTH_TOKEN in .env, or append it if not present."""
    content = ENV_PATH.read_text() if ENV_PATH.exists() else ""
    if re.search(r"^AUTH_TOKEN=.*$", content, re.MULTILINE):
        content = re.sub(r"^AUTH_TOKEN=.*$", f"AUTH_TOKEN={token}", content, flags=re.MULTILINE)
    else:
        content += f"\nAUTH_TOKEN={token}\n"
    ENV_PATH.write_text(content)
    
def validate_token(token: str) -> bool:
    """Check with the server whether the provided token is valid."""
    with httpx.Client(timeout=HTTP_TIMEOUT) as client:
        res = client.get(
            f"{SERVER_URL}/aqc/v1/validate",
            headers={"Authorization": f"Bearer {token}"},
        )
        return res.is_success


# ----------------------- Registration flow -----------------------

def prompt_asset() -> tuple[int | None, dict | None]:
    """
    Ask whether the asset already exists.
    - If yes: user enters the asset_id directly (server handles assignment).
    - If no:  collect name + type for the server to create.

    Returns (asset_id, new_asset_payload) — one will always be None.
    """
    asset_exists = questionary.confirm(
        "Does this asset already exist on the server?"
    ).ask()

    if asset_exists:
        asset_id = int(questionary.text(
            "Asset ID:",
            validate=lambda v: v.isdigit() or "Please enter a valid numeric ID"
        ).ask())
        return asset_id, None
    else:
        asset_name = questionary.text(
            "Asset name (e.g. 'Classroom 3B', 'Main Entrance'):"
        ).ask()
        asset_type = questionary.select(
            "Asset type (e.g. 'indoor', 'outdoor'):",
            choices=["indoor", "outdoor"]
        ).ask()
        return None, {"nickname": asset_name, "type": asset_type}


def run_registration():
    print_banner()
    mac_address = get_mac_address()

    org_token   = questionary.text("Organisation Token:").ask()
    username    = questionary.text("Teacher Username:").ask()
    password    = questionary.password("Teacher Password:").ask()
    device_name = questionary.text("Device Nickname (e.g. 'pi-mini-2'):").ask()

    asset_id, new_asset = prompt_asset()

    auth_headers = {
        "Authorization": f"Bearer {org_token}",
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=HTTP_TIMEOUT) as client:
        res = client.post(
            f"{SERVER_URL}/aqc/v1/register",
            headers=auth_headers,
            json={
                "mac_address": mac_address,
                "nickname": device_name,
                "username": username,
                "password": password,
                "asset_id": asset_id,
                "new_asset": new_asset,
            },
        )

        if not res.is_success:
            raise RuntimeError(res.json().get("error", "Registration failed"))

        data = res.json()
        print(f"\n{data.get('message', 'Registered successfully!')}\n")

        write_env_token(data["auth_token"])
        load_dotenv(override=True)


# -------------------- Headless gate (used by main.py) --------------------

def check_registration() -> bool:
    """Non-interactive startup gate. No prompts (safe under systemd).

    Returns False (caller should exit) if there's no token or the server
    actively rejects it. Returns True if the token is valid OR the server
    is merely unreachable — in the unreachable case the ingest loop starts
    anyway and queues readings locally until the server returns.
    """
    token = os.getenv("AUTH_TOKEN", "").strip()
    if not token:
        print("(err) No AUTH_TOKEN - Register with: `python -m setup`")
        return False
    try:
        if not validate_token(token):
            print("(err) Token rejected by server — Re-register: `python -m setup`")
            return False
        return True
    except httpx.ConnectError:
        print("(warn) Server unreachable. Starting anyway - readings will queue locally.")
        return True


# ----------------------- Manual entry point -----------------------

if __name__ == "__main__":
    try:
        run_registration()
    except RuntimeError as e:
        print(f"Registration failed: {e}")
        raise SystemExit(1)

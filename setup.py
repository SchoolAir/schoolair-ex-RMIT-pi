"""setup.py

Handles registration and setup for the pi.

This module handles:
- Validate auth token
- Registration
- Re-registration
"""

import os
import re
import json
import uuid
import httpx
import questionary
import netifaces
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

SERVER_URL = os.getenv("SERVER_URL", "").rstrip("/")
IDENTITY_PATH = Path("config/identity.json")
ENV_PATH = Path(".env")

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


def write_identity(device_id: int, asset_id: int, org_id: int, site_id: int):
    """Persist device identity to config/identity.json."""
    IDENTITY_PATH.parent.mkdir(parents=True, exist_ok=True)
    identity = {
        "device": {
            "device_id": device_id,
            "asset_id": asset_id,
        },
        "locale": {
            "org_id": org_id,
            "site_id": site_id,
        }
    }
    IDENTITY_PATH.write_text(json.dumps(identity, indent=4))


def load_identity() -> dict | None:
    """Load identity.json, returning None if missing or malformed."""
    if not IDENTITY_PATH.exists():
        return None
    try:
        return json.loads(IDENTITY_PATH.read_text())
    except json.JSONDecodeError:
        return None


def identity_is_complete(identity: dict) -> bool:
    """Check all required fields are present and non-empty."""
    try:
        return all([
            identity["device"]["device_id"],
            identity["device"]["asset_id"],
            identity["locale"]["org_id"],
            "site_id" in identity["locale"], # Value can be None
        ])
    except KeyError:
        return False


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

    with httpx.Client(timeout=10) as client:
        res = client.post(
            f"{SERVER_URL}/aqc/v1/register",
            headers=auth_headers,
            json={
                "mac_address": mac_address,
                "nickname": device_name,
                "username": username,
                "password": password,
                # One of these will be None (register v re-register)
                "asset_id": asset_id,
                "new_asset": new_asset,
            },
        )

        if not res.is_success:
            raise RuntimeError(res.json().get("error", "Registration failed"))

        data = res.json()
        print(f"\n{data.get('message', 'Registered successfully!')}\n")

        write_env_token(data["auth_token"])
        write_identity(
            device_id=data["device_id"],
            asset_id=data["asset_id"],
            org_id=data["org_id"],
            site_id=data["site_id"],
        )
        load_dotenv(override=True)

def validate_token(token: str) -> bool:
    """Check if current auth token is valid."""
    try:
        with httpx.Client(timeout=10) as client:
            res = client.get(
                f"{SERVER_URL}/aqc/v1/validate",
                headers={"Authorization": f"Bearer {token}"},
            )
            return res.is_success
    except httpx.ConnectError:
        print("Could not reach server: check your connection.")
        raise SystemExit(1)


# ----------------------- Entry point (called in main.py) -----------------------

def ensure_registered():
    """
    Validate existing auth token, or run registration if missing/invalid.
    Exits the process if something is unrecoverable.
    """
    token = os.getenv("AUTH_TOKEN", "").strip()
 
    if token and validate_token(token):
        identity = load_identity()
        if not identity or not identity_is_complete(identity):
            print("Warning: identity.json is missing or incomplete. Please re-register.")
        else:
            # Token valid. Show startup menu
            choice = questionary.select(
                "SchoolAir — what would you like to do?",
                choices=[
                    {"name": "Start up normally", "value": "startup"},
                    {"name": "Re-register this device", "value": "reregister"},
                ]
            ).ask()
 
            if choice == "startup":
                return
            # else fall through to registration below
 
    elif token:
        print("Token invalid or expired, please re-register.")
    else:
        print("Auth token missing, please register.")
 
    try:
        run_registration()
    except RuntimeError as e:
        print(f"Registration failed: {e}")
        raise SystemExit(1)

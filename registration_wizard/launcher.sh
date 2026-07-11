#!/usr/bin/env bash
# SchoolAir Gatekeeper – boot launcher
#
# Called by schoolair-launcher.service on every boot.
# Logic:
#   1. Wait up to 60 s for a client (non-AP) Wi-Fi connection.
#   2. If connected AND AUTH_TOKEN is set in pi-main's .env → everything OK, exit.
#   3. If connected but no AUTH_TOKEN → start wizard in re-registration mode (no AP).
#   4. If no connection → bring up the AP hotspot and start the registration wizard.

set -euo pipefail

WIZARD_SERVICE="schoolair-wizard"
AP_CONN="SchoolAir_AP"
PI_MAIN_ENV="/home/admin/schoolair/.env"
POLL_INTERVAL=5   # seconds between connectivity checks
MAX_WAIT=60       # total seconds to wait for a client connection

log() { echo "[schoolair-launcher] $*"; }

has_auth_token() {
    grep -q '^AUTH_TOKEN=.\+' "$PI_MAIN_ENV" 2>/dev/null
}

# ── 1. Wait for a known client Wi-Fi connection ────────────────────────────────
# We specifically exclude the SchoolAir_AP hotspot connection — it would have
# an IP too, but that is not a real upstream network.
log "Waiting up to ${MAX_WAIT}s for a client Wi-Fi network…"

elapsed=0
connected=false
active_sta=""

while [ "$elapsed" -lt "$MAX_WAIT" ]; do
    # List active, activated Wi-Fi connections that are NOT the AP hotspot.
    active_sta=$(nmcli -t -f NAME,TYPE,STATE con show --active 2>/dev/null \
        | awk -F: '$2=="802-11-wireless" && $3=="activated" && $1!="'"$AP_CONN"'" {print $1; exit}')

    if [ -n "$active_sta" ]; then
        connected=true
        break
    fi
    sleep "$POLL_INTERVAL"
    elapsed=$((elapsed + POLL_INTERVAL))
done

if "$connected"; then
    if has_auth_token; then
        log "Connected to '${active_sta}' with AUTH_TOKEN — no wizard needed. Exiting."
        exit 0
    else
        log "Connected to '${active_sta}' but no AUTH_TOKEN found — starting wizard for registration."
        systemctl start "$WIZARD_SERVICE"
        exit 0
    fi
fi

# ── 3. No client network — start the AP and wizard ────────────────────────────
log "No client network after ${MAX_WAIT}s. Starting AP hotspot…"

if nmcli con up "$AP_CONN" 2>/dev/null; then
    log "AP '${AP_CONN}' is up."
else
    log "WARNING: Could not bring up NM connection '${AP_CONN}'."
    log "         Falling back to hostapd (if installed)…"
    systemctl start hostapd 2>/dev/null || log "WARNING: hostapd also unavailable."
fi

# Give the AP a moment to initialise before the wizard opens port 80.
sleep 3

# Redirect all HTTP and HTTPS from AP clients to the wizard, regardless of what
# IP the client is trying to reach.  Catches OS captive-portal probes that use
# hardcoded IPs instead of (or before) DNS.
iptables -t nat -A PREROUTING -i wlan0 -p tcp --dport 80  -j REDIRECT --to-port 80  2>/dev/null || true
iptables -t nat -A PREROUTING -i wlan0 -p tcp --dport 443 -j REDIRECT --to-port 443 2>/dev/null || true
log "Captive-portal iptables rules active (ports 80 + 443)"

log "Starting ${WIZARD_SERVICE}…"
systemctl start "$WIZARD_SERVICE"

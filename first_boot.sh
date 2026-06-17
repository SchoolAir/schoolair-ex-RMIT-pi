#!/usr/bin/env bash
# SchoolAir First-Boot Hostname Assignment
#
# Runs on every boot via schoolair-first-boot.service.
# Acts only when the hostname is exactly "schoolair-template" (freshly flashed clone).

set -euo pipefail

# Regenerate SSH host keys if missing (wiped by prepare_image.sh on the source device).
# Must run before sshd starts — enforced via Before=ssh.service in the unit file.
if ! ls /etc/ssh/ssh_host_*_key &>/dev/null 2>&1; then
    echo "[schoolair-first-boot] SSH host keys missing — regenerating…"
    ssh-keygen -A
    echo "[schoolair-first-boot] SSH host keys generated"
fi

[[ "$(hostname)" == "schoolair-template" ]] || exit 0

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "[schoolair-first-boot] Template hostname detected — assigning unique hostname…"
NEW_HN=$(bash "${SCRIPT_DIR}/set_hostname.sh")
echo "[schoolair-first-boot] Hostname is now: ${NEW_HN}"

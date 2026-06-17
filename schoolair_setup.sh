#!/usr/bin/env bash
# SchoolAir – Device setup script
#
# Run on a fresh Raspberry Pi OS Lite (Bookworm or later):
#
#   curl -sSL https://raw.githubusercontent.com/agt-rmit-schoolair/pi/integration/schoolair_setup.sh | sudo bash
#
# To override the Pi username (default: admin):
#   curl ... | sudo ADMIN_USER=pi bash
#
# What this script does:
#   0.  Pre-flight checks
#   1.  Hostname  →  schoolair-YYMDD-XXXX  (skipped if already set; M = hex month)
#   2.  System packages
#   3.  Clone SchoolAir repo  →  ~/schoolair/  (preserves .env on re-runs)
#   4.  Python dependencies
#   5.  Device utility scripts  →  ~/  (first_boot.sh, set_hostname.sh, add_wifi.sh, schoolair)
#   6.  Registration wizard TLS certificate
#   7.  Build sen6x daemon
#   8.  I2C enable + 100 kHz baudrate
#   9.  NetworkManager Wi-Fi hotspot (SchoolAir_Setup, open)
#   10. Captive-portal DNS hijacking via NM dnsmasq plugin
#   11. Avahi  →  schoolair-register.local
#   12. dhcpcd conflict prevention (Bullseye only)
#   13. nginx  →  proxies port 80 → telemetry :8080 (disabled until registered)
#   14. Sudoers rule for telemetry to start wizard
#   15. systemd services
#   16. Verification + summary
#
# Port-80 lifecycle:
#   Unregistered:  wizard (Microdot) holds port 80
#   Registered:    wizard exits, nginx activates → proxies port 80 to telemetry :8080
#
# Idempotent — safe to re-run.  Hostname and .env are preserved once set.

set -euo pipefail

# ── Configuration ──────────────────────────────────────────────────────────────
ADMIN_USER="${ADMIN_USER:-admin}"
ADMIN_HOME="/home/${ADMIN_USER}"

REPO_URL="https://github.com/agt-rmit-schoolair/pi.git"
REPO_BRANCH="integration"
REPO_DIR="/tmp/schoolair-pi"

SCHOOLAIR_DIR="${ADMIN_HOME}/schoolair"
WIZARD_DIR="${SCHOOLAIR_DIR}/registration_wizard"
I2C_DIR="${ADMIN_HOME}/i2c"

AP_IFACE="wlan0"
AP_CONN="SchoolAir_AP"
AP_SSID="SchoolAir_Setup"
AP_IP="192.168.4.1"

TELEMETRY_PORT=8080

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_FILE="/var/log/schoolair-setup.log"
exec > >(tee -a "$LOG_FILE") 2>&1
echo "━━━ SchoolAir setup started: $(date) ━━━"

# ── Helpers ────────────────────────────────────────────────────────────────────
BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
step()  { echo; echo -e "${BOLD}── $* ──${NC}"; }
ok()    { echo -e "  ${GREEN}✓${NC}  $*"; }
warn()  { echo -e "  ${YELLOW}⚠${NC}   $*"; }
skip()  { echo -e "  –   $* (skipped)"; }
die()   { echo -e "${RED}${BOLD}FATAL: $*${NC}"; exit 1; }

# ── 0. Pre-flight ──────────────────────────────────────────────────────────────
step "0 / Pre-flight"
[[ $EUID -eq 0 ]] \
    || die "Must run as root.  Try:  sudo bash $0"
id -u "$ADMIN_USER" >/dev/null 2>&1 \
    || die "User '${ADMIN_USER}' not found.  Set ADMIN_USER=<name> and re-run."
command -v nmcli   >/dev/null 2>&1 || die "nmcli not found — is NetworkManager installed?"
command -v python3 >/dev/null 2>&1 || die "python3 not found."
ok "Root, user='${ADMIN_USER}', home='${ADMIN_HOME}'"

# Fetch set_hostname.sh before the repo clone — needed at step 1.
_RAW_BASE="$(echo "$REPO_URL" | sed 's|github\.com|raw.githubusercontent.com|; s|\.git$||')"
_SET_HN_TMP="/tmp/schoolair-set_hostname.sh"
curl -fsSL "${_RAW_BASE}/${REPO_BRANCH}/set_hostname.sh" \
    -o "$_SET_HN_TMP" \
    || die "Cannot fetch set_hostname.sh from GitHub — check connectivity."
chmod +x "$_SET_HN_TMP"
ok "set_hostname.sh fetched from GitHub"

# ── 1. Hostname ────────────────────────────────────────────────────────────────
step "1 / Hostname"
CURRENT_HN=$(hostname)
if [[ "$CURRENT_HN" == schoolair-[0-9]* ]]; then
    ok "Hostname already set: ${CURRENT_HN}  (not regenerated)"
    _HN_FILE=$(cat /etc/hostname 2>/dev/null | tr -d '[:space:]')
    if [ "$_HN_FILE" != "$CURRENT_HN" ]; then
        bash "$_SET_HN_TMP" "$CURRENT_HN" > /dev/null
        ok "Hostname locations synced to ${CURRENT_HN}"
    fi
else
    NEW_HN=$(bash "$_SET_HN_TMP")
    ok "Hostname set to ${NEW_HN}"
fi

# ── 2. System packages ─────────────────────────────────────────────────────────
step "2 / System packages"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y \
    git python3-pip i2c-tools nginx avahi-daemon gcc make
ok "git python3-pip i2c-tools nginx avahi-daemon gcc make"

systemctl disable nginx 2>/dev/null || true
systemctl stop    nginx 2>/dev/null || true

# ── 3. Clone / update SchoolAir app ───────────────────────────────────────────
step "3 / Clone SchoolAir app  →  ${SCHOOLAIR_DIR}"
rm -rf "$REPO_DIR"
git clone --depth 1 --branch "$REPO_BRANCH" "$REPO_URL" "$REPO_DIR" \
    || die "git clone failed — check connectivity and repo URL."
ok "Cloned to ${REPO_DIR}"

mkdir -p "$SCHOOLAIR_DIR"
if [ -f "${SCHOOLAIR_DIR}/.env" ]; then
    cp "${SCHOOLAIR_DIR}/.env" /tmp/schoolair-env.bak
    cp -r "${REPO_DIR}/." "${SCHOOLAIR_DIR}/"
    mv /tmp/schoolair-env.bak "${SCHOOLAIR_DIR}/.env"
    ok "App deployed  (existing .env preserved)"
else
    cp -r "${REPO_DIR}/." "${SCHOOLAIR_DIR}/"
    cp "${SCHOOLAIR_DIR}/.env.example" "${SCHOOLAIR_DIR}/.env"
    ok "App deployed + .env created from .env.example"
fi
chown -R "${ADMIN_USER}:${ADMIN_USER}" "$SCHOOLAIR_DIR"
rm -rf "$REPO_DIR"

# ── 4. Python dependencies ─────────────────────────────────────────────────────
step "4 / Python dependencies"
pip3 install --quiet --break-system-packages --root-user-action=ignore \
    "microdot>=2.0.0" simple-websocket httpx python-dotenv questionary netifaces
python3 -c "import microdot" 2>/dev/null \
    || die "microdot failed to import after install."
ok "microdot, simple-websocket, httpx, python-dotenv, questionary, netifaces installed"

# ── 5. Device utility scripts ─────────────────────────────────────────────────
step "5 / Device utility scripts  →  ${ADMIN_HOME}/"
for _f in first_boot.sh set_hostname.sh version_check.py add_wifi.sh; do
    if [ -f "${SCHOOLAIR_DIR}/${_f}" ]; then
        cp "${SCHOOLAIR_DIR}/${_f}" "${ADMIN_HOME}/${_f}"
        chmod +x "${ADMIN_HOME}/${_f}"
        chown "${ADMIN_USER}:${ADMIN_USER}" "${ADMIN_HOME}/${_f}"
        ok "${_f}  →  ${ADMIN_HOME}/"
    else
        warn "${_f} not found in app dir — skipped"
    fi
done
ln -sf "${ADMIN_HOME}/version_check.py" /usr/local/bin/schoolair
ok "schoolair command  →  /usr/local/bin/schoolair"

# ── 6. Registration wizard TLS certificate ────────────────────────────────────
step "6 / Registration wizard TLS certificate"
if [ ! -f "${WIZARD_DIR}/cert.pem" ] || [ ! -f "${WIZARD_DIR}/key.pem" ]; then
    openssl req -x509 -newkey rsa:2048 \
        -keyout "${WIZARD_DIR}/key.pem" \
        -out    "${WIZARD_DIR}/cert.pem" \
        -days 3650 -nodes \
        -subj "/CN=schoolair-setup" \
        2>/dev/null
    chmod 640 "${WIZARD_DIR}/key.pem"
    chown "${ADMIN_USER}:${ADMIN_USER}" "${WIZARD_DIR}/cert.pem" "${WIZARD_DIR}/key.pem"
    ok "TLS certificate generated (self-signed, 10 yr)"
else
    skip "TLS certificate already present — not regenerated"
fi

# ── 7. Build sen6x daemon ──────────────────────────────────────────────────────
step "7 / Build sen6x daemon"
MAKEFILE="${SCHOOLAIR_DIR}/i2c/sen6x/Makefile.daemon"
if [ ! -f "$MAKEFILE" ]; then
    skip "i2c/sen6x/Makefile.daemon not found in app"
else
    systemctl stop sen6x 2>/dev/null || true
    if make -C "${SCHOOLAIR_DIR}/i2c/sen6x" -f Makefile.daemon; then
        mkdir -p "${I2C_DIR}/sen6x"
        cp "${SCHOOLAIR_DIR}/i2c/sen6x/sen6x_d" "${I2C_DIR}/sen6x/sen6x_d"
        chmod +x "${I2C_DIR}/sen6x/sen6x_d"
        chown -R "${ADMIN_USER}:${ADMIN_USER}" "$I2C_DIR"
        ok "sen6x daemon compiled  →  ${I2C_DIR}/sen6x/sen6x_d"
    else
        warn "sen6x make failed — check gcc output above (non-fatal)"
    fi
fi

# ── 8. I2C + baudrate ────────────────────────────────────────────────────────
step "8 / I2C enable + baudrate"
raspi-config nonint do_i2c 0
ok "I2C enabled (takes effect after reboot)"

if   [ -f /boot/firmware/config.txt ]; then CFG=/boot/firmware/config.txt
elif [ -f /boot/config.txt ];           then CFG=/boot/config.txt
else die "Cannot find config.txt — is this a Raspberry Pi?"; fi
ok "config.txt → ${CFG}"

sed -i '/dtparam=i2c_arm_baudrate/d' "$CFG"
echo "dtparam=i2c_arm_baudrate=100000" >> "$CFG"
ok "I2C baudrate → 100 kHz"

# ── 9. NM hotspot ────────────────────────────────────────────────────────────
step "9 / NetworkManager hotspot  (${AP_SSID})"
if nmcli con show "$AP_CONN" &>/dev/null; then
    nmcli con delete "$AP_CONN" >/dev/null
fi
nmcli con add           \
    type wifi           \
    ifname "$AP_IFACE"  \
    con-name "$AP_CONN" \
    wifi.mode ap        \
    ssid "$AP_SSID"     \
    ipv4.method shared  \
    ipv4.addresses "${AP_IP}/24" \
    connection.autoconnect no   \
    >/dev/null
ok "Open hotspot on ${AP_IP}  (autoconnect disabled — launcher controls it)"

# ── 10. Captive-portal DNS ────────────────────────────────────────────────────
step "10 / Captive-portal DNS hijacking"
mkdir -p /etc/NetworkManager/dnsmasq-shared.d
cat > /etc/NetworkManager/dnsmasq-shared.d/schoolair-captive.conf << EOF
address=/#/${AP_IP}
address=/schoolair-register.local/${AP_IP}
EOF
systemctl reload NetworkManager 2>/dev/null || systemctl restart NetworkManager
ok "Captive-portal DNS config written"

# ── 11. Avahi ─────────────────────────────────────────────────────────────────
step "11 / Avahi  →  schoolair-register.local"
mkdir -p /etc/avahi/services
cat > /etc/avahi/services/schoolair.service << 'EOF'
<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name>SchoolAir Registration Portal</name>
  <service><type>_http._tcp</type><port>80</port></service>
</service-group>
EOF
systemctl enable --quiet avahi-daemon
systemctl restart avahi-daemon
ok "Avahi configured"

# ── 12. dhcpcd (Bullseye only) ────────────────────────────────────────────────
if [ -f /etc/dhcpcd.conf ]; then
    step "12 / dhcpcd conflict prevention  (Bullseye)"
    if grep -q "denyinterfaces ${AP_IFACE}" /etc/dhcpcd.conf; then
        ok "Already configured"
    else
        printf '\n# SchoolAir — NetworkManager manages %s\ndenyinterfaces %s\n' \
            "$AP_IFACE" "$AP_IFACE" >> /etc/dhcpcd.conf
        ok "Added denyinterfaces ${AP_IFACE}"
    fi
fi

# ── 13. nginx ─────────────────────────────────────────────────────────────────
step "13 / nginx  (configured, disabled until registration)"
# nginx proxies port 80 → telemetry :8080 once the device is registered.
# Stays disabled here — wizard.py's _delayed_shutdown() enables it on success.
cat > /etc/nginx/sites-available/default << NGINXEOF
server {
    listen 80;
    server_name _;
    location / {
        proxy_pass http://127.0.0.1:${TELEMETRY_PORT};
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_read_timeout 300s;
    }
}
NGINXEOF
ln -sf /etc/nginx/sites-available/default /etc/nginx/sites-enabled/default
systemctl disable nginx 2>/dev/null || true
systemctl stop    nginx 2>/dev/null || true
ok "nginx config written  (proxies to :${TELEMETRY_PORT}, service disabled)"

# ── 14. Sudoers rule ──────────────────────────────────────────────────────────
step "14 / Sudoers rule for telemetry → wizard"
SUDOERS_FILE="/etc/sudoers.d/schoolair-wizard"
cat > "$SUDOERS_FILE" << EOF
# Allow the telemetry server (runs as ${ADMIN_USER}) to start the wizard service
${ADMIN_USER} ALL=(ALL) NOPASSWD: /usr/bin/systemctl start schoolair-wizard
EOF
chmod 440 "$SUDOERS_FILE"
ok "sudoers: ${ADMIN_USER} may start schoolair-wizard without password"

# ── 15. systemd services ──────────────────────────────────────────────────────
step "15 / systemd services"
DEPLOY_DIR="${SCHOOLAIR_DIR}/deploy"

for svc in sen6x.service schoolair.service schoolair-wizard.service schoolair-launcher.service; do
    if [ -f "${DEPLOY_DIR}/${svc}" ]; then
        cp "${DEPLOY_DIR}/${svc}" /etc/systemd/system/
        ok "${svc} installed"
    else
        warn "${svc} not found in deploy/ — skipped"
    fi
done

if [ -f "${DEPLOY_DIR}/schoolair-first-boot.service" ]; then
    cp "${DEPLOY_DIR}/schoolair-first-boot.service" /etc/systemd/system/
    ok "schoolair-first-boot.service installed"
fi

systemctl daemon-reload
systemctl enable schoolair-launcher.service
systemctl enable schoolair.service
systemctl enable sen6x.service
systemctl enable schoolair-first-boot.service 2>/dev/null || true
ok "Services enabled"

# ── 16. Verification ───────────────────────────────────────────────────────────
step "16 / Verification"
ERRORS=0
chk() {
    local label="$1"; shift
    if "$@" >/dev/null 2>&1; then ok "$label"
    else warn "$label"; ERRORS=$((ERRORS+1)); fi
}

chk "hostname is schoolair-*"              bash -c '[[ "$(hostname)" == schoolair-* ]]'
chk "microdot importable"                  python3 -c "import microdot"
chk "httpx importable"                     python3 -c "import httpx"
chk "launcher.sh executable"              test -x "${WIZARD_DIR}/launcher.sh"
chk "main.py present"                      test -f "${SCHOOLAIR_DIR}/main.py"
chk "first_boot.sh executable"            test -x "${ADMIN_HOME}/first_boot.sh"
chk "schoolair command available"         test -L /usr/local/bin/schoolair
chk "NM hotspot '${AP_CONN}'"             nmcli con show "$AP_CONN"
chk "Captive-portal DNS config"           test -f /etc/NetworkManager/dnsmasq-shared.d/schoolair-captive.conf
chk "Avahi service file"                  test -f /etc/avahi/services/schoolair.service
chk "schoolair-launcher enabled"          systemctl is-enabled schoolair-launcher.service
chk "schoolair.service enabled"           systemctl is-enabled schoolair.service
chk "sen6x.service enabled"              systemctl is-enabled sen6x.service
chk "schoolair-first-boot enabled"        systemctl is-enabled schoolair-first-boot.service
chk "nginx proxies to ${TELEMETRY_PORT}"  grep -q "${TELEMETRY_PORT}" /etc/nginx/sites-available/default
chk "nginx disabled (correct pre-reg)"   bash -c "! systemctl is-enabled nginx >/dev/null 2>&1"
chk "sudoers rule present"               test -f /etc/sudoers.d/schoolair-wizard

# ── Summary ────────────────────────────────────────────────────────────────────
echo
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [ "$ERRORS" -eq 0 ]; then
    echo -e "  ${GREEN}${BOLD}Setup complete — all checks passed.${NC}"
else
    echo -e "  ${YELLOW}${BOLD}Setup complete with ${ERRORS} warning(s) — see above.${NC}"
fi
echo
echo "  This device hostname:  $(hostname)"
echo
echo "  After rebooting:"
echo "  1. Join Wi-Fi:  SchoolAir_Setup  (open, no password)"
echo "  2. Open:        http://${AP_IP}"
echo "  3. Complete the registration form."
echo "  4. On success the hotspot closes; nginx activates on port 80"
echo "     and proxies to the telemetry server on :${TELEMETRY_PORT}."
echo
echo "  Logs:"
echo "    journalctl -u schoolair-launcher -u schoolair-wizard -u schoolair -f"
echo
echo "  Developer notes (re-run only):"
echo -e "  ${YELLOW}➜${NC}  sen6x daemon was stopped to allow binary replacement."
echo "     Start it now without rebooting:  sudo systemctl start sen6x"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

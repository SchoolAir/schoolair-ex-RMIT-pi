#!/usr/bin/env python3
"""SchoolAir Gatekeeper Registration Wizard — two-page setup flow."""

import asyncio
import html as _html
import json
import os
import pwd
import random
import re
import secrets
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from microdot import Microdot, Response
from microdot.websocket import with_websocket

from config import (
    AP_CONNECTION_NAME,
    AP_INTERFACE,
    AP_IP,
    CONFIG_DIR,
    ERROR_FILE,
    HEARTBEAT_TIMEOUT,
    HEARTBEAT_URL,
    NODE_RED_TOKEN_FILE,
    PI_MAIN_ENV_PATH,
    SERVER_PORT,
    STAGING_FILE,
    STATUS_FILE,
)

LEGACY_URL    = "https://data.schoolair.org/node/aqc/register"  # old server — LEGACY path only
TEMP_PROFILE  = "school-air-temp"
SAVED_PREFIX  = "schoolair-"
IDLE_TIMEOUT  = 15 * 60  # seconds before the wizard auto-shuts down when idle

app = Microdot()


# ── In-memory state ────────────────────────────────────────────────────────────

reg_state: dict = {"state": "idle", "message": ""}

# Populated when user passes Page 1 validation; gates access to Page 2.
wifi_session: dict = {
    "active":        False,
    "session_token": "",     # per-client UUID; embedded in every Page 2 request
    "token":         "",
    "site":          "",
    "asset":         "",
    "environment":   "indoor",
    "pre_verified":  False,  # True if cloud already confirmed on Page 1
    "legacy_resp":   {},     # filled when LEGACY + pre_verified
}

# Activity tracking for idle-shutdown watchdog
_last_activity: float = time.time()


@app.before_request
async def _track_activity(request):
    global _last_activity
    _last_activity = time.time()
    # Captive portal detection: browsers probe via a domain-name Host header.
    # If the Host isn't an IP or our known aliases, redirect to the portal so
    # the browser shows its "Sign in to network" notification bar.
    host = (request.headers.get("Host") or "").split(":")[0].lower().strip()
    if host and not re.match(r"^\d+\.\d+\.\d+\.\d+$", host) \
             and host not in ("localhost", "schoolair-register.local"):
        return Response("", status_code=302, headers={"Location": f"http://{AP_IP}/"})


# ── HTML helpers ──────────────────────────────────────────────────────────────

def _render(template: str, raw: dict = None, **kwargs) -> str:
    """Replace [[key]] placeholders. kwargs values are HTML-escaped; raw verbatim."""
    for k, v in kwargs.items():
        template = template.replace(f"[[{k}]]", _html.escape(str(v)))
    for k, v in (raw or {}).items():
        template = template.replace(f"[[{k}]]", str(v))
    return template


def _html_response(body: str, status: int = 200) -> Response:
    return Response(body, status_code=status,
                    headers={"Content-Type": "text/html; charset=utf-8"})


def _json_response(data: dict, status: int = 200) -> Response:
    return Response(json.dumps(data), status_code=status,
                    headers={"Content-Type": "application/json"})


def _friendly_error(msg: str) -> str:
    if "Token rejected" in msg or "401" in msg or "403" in msg:
        return "Invalid or outdated token."
    if "Could not reach" in msg or "URLError" in msg:
        return "SchoolAir Cloud is unreachable — check internet connection."
    return msg


def _session_ok(request) -> bool:
    """Verify that a Page 2 request carries the current per-client session token."""
    expected = wifi_session.get("session_token", "")
    if not expected:
        return False
    token = (
        request.headers.get("X-Session")
        or (request.json or {}).get("session")
        or (request.form or {}).get("session")
    )
    return token == expected


# ── Wizard greetings ──────────────────────────────────────────────────────────

_SKIN_TONES = ["\U0001F3FB", "\U0001F3FC", "\U0001F3FD", "\U0001F3FE", "\U0001F3FF"]
_WIZARD_BASE = "\U0001F9D9"
_ZWJ  = "‍"
_VS16 = "️"
_MALE   = "♂"
_FEMALE = "♀"
_skin_tone_counts = [0] * len(_SKIN_TONES)


def _pick_skin_tone() -> str:
    min_c = min(_skin_tone_counts)
    candidates = [i for i, c in enumerate(_skin_tone_counts) if c == min_c]
    idx = random.choice(candidates)
    _skin_tone_counts[idx] += 1
    return _SKIN_TONES[idx]


def _wizard_emoji(gender: str) -> str:
    tone = _pick_skin_tone()
    if gender == "m":
        return _WIZARD_BASE + tone + _ZWJ + _MALE + _VS16
    if gender == "f":
        return _WIZARD_BASE + tone + _ZWJ + _FEMALE + _VS16
    return _WIZARD_BASE + tone + _VS16


_GANDALF_QUOTE   = ("A wizard is never late. Nor is he early. He arrives precisely when he means to.", "Gandalf")
_GLINDA_QUOTE    = ("You've always had the power, my dear. You just had to learn it for yourself.", "Glinda")
_RAINE_QUOTE = ("Go. You know I can't stand an audience.", "Raine Whispers")


# ── Page 1: Registration / Authentication ─────────────────────────────────────

FORM_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SchoolAir Setup</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
     background:linear-gradient(135deg,#667eea,#764ba2);min-height:100vh;
     display:flex;align-items:center;justify-content:center;padding:1rem}
.card{background:#fff;border-radius:16px;padding:2rem;max-width:440px;
      width:100%;box-shadow:0 8px 32px rgba(0,0,0,.18)}
.logo{text-align:center;margin-bottom:1.5rem}
.logo h1{font-size:1.5rem;color:#1a56db;font-weight:700}
.logo p{color:#6b7280;font-size:.875rem;margin-top:.25rem}
.sect{font-size:.72rem;font-weight:600;color:#6b7280;text-transform:uppercase;
      letter-spacing:.05em;margin:1.25rem 0 .4rem}
label{display:block;font-size:.875rem;font-weight:500;color:#374151;
      margin-bottom:.2rem;margin-top:.65rem}
.field-row{display:flex;gap:.4rem;align-items:flex-end}
.field-row input{flex:1;min-width:0}
input[type=text],input[type=password]{width:100%;padding:.6rem .75rem;
  border:1.5px solid #d1d5db;border-radius:8px;font-size:1rem;outline:none;
  transition:border-color .2s;background:#fff}
input:focus{border-color:#1a56db}
input:disabled{background:#f3f4f6;color:#374151;cursor:default}
.lock-btn{padding:.55rem .7rem;border:1.5px solid #d1d5db;border-radius:8px;
  background:#f9fafb;cursor:pointer;font-size:.95rem;line-height:1;
  flex-shrink:0;transition:border-color .2s}
.lock-btn:hover{border-color:#1a56db}
.tog{display:flex;gap:.5rem;margin-top:.5rem}
.tog input[type=radio]{display:none}
.tog label{flex:1;text-align:center;padding:.6rem;border:1.5px solid #d1d5db;
  border-radius:8px;cursor:pointer;font-weight:500;color:#6b7280;
  transition:all .2s;margin:0}
.tog input[type=radio]:checked+label{background:#1a56db;color:#fff;border-color:#1a56db}
.notice{padding:.75rem;border-radius:8px;font-size:.875rem;margin-top:.75rem}
.notice-ok{background:#ecfdf5;color:#065f46;border:1.5px solid #6ee7b7}
.notice-err{background:#fef2f2;color:#dc2626;border:1.5px solid #fca5a5}
.notice-quote{background:#eef2ff;color:#3730a3;border:1.5px solid #a5b4fc}
.quote-author{font-size:.75rem;font-weight:600;font-style:normal;display:block;margin-top:.25rem}
.btn-row{display:flex;gap:.75rem;margin-top:1.5rem}
.btn{flex:1;padding:.8rem .5rem;border:none;border-radius:10px;font-size:.95rem;
     font-weight:600;cursor:pointer;transition:background .2s}
.btn-primary{background:#1a56db;color:#fff}
.btn-primary:hover:not(:disabled){background:#1649c0}
.btn-primary:disabled{background:#93c5fd;cursor:not-allowed}
.btn-secondary{background:#f3f4f6;color:#374151;border:1.5px solid #d1d5db}
.btn-secondary:hover:not(:disabled){background:#e5e7eb}
.btn-secondary:disabled{opacity:.5;cursor:not-allowed}
.migrate-row{display:flex;align-items:center;gap:.6rem;margin:.75rem 0 .25rem}
.migrate-row input[type=checkbox]{width:1.1rem;height:1.1rem;accent-color:#1a56db;
  flex-shrink:0;cursor:pointer}
.migrate-lbl{font-size:.9rem;color:#374151;cursor:pointer;
  display:flex;align-items:center;gap:.4rem}
.tip{position:relative;display:inline-flex;align-items:center;
  justify-content:center;width:1.1rem;height:1.1rem;border-radius:50%;
  background:#d1d5db;color:#374151;font-size:.7rem;font-weight:700;
  cursor:help;flex-shrink:0}
.tip-body{display:none;position:absolute;left:1.4rem;top:50%;
  transform:translateY(-50%);background:#1f2937;color:#f9fafb;
  font-size:.78rem;line-height:1.5;font-weight:400;padding:.6rem .75rem;
  border-radius:8px;width:220px;z-index:10;pointer-events:none;
  box-shadow:0 4px 12px rgba(0,0,0,.3)}
.tip:hover .tip-body,.tip:focus .tip-body{display:block}
</style>
</head>
<body>
<div class="card">
  <div class="logo"><h1>[[wizard_emoji]] SchoolAir Registration Wizard</h1></div>
  <div class="notice notice-quote" id="quote-notice"><em>"[[quote]]"</em><span class="quote-author">— [[quote_author]]</span></div>
  <div id="notice" class="notice" style="display:none"></div>

  <form id="pg1" onsubmit="return false">
    <div class="sect">Identity</div>

    <label for="token">Registration Token</label>
    <input type="text" id="token" name="token" autocomplete="on"
           placeholder="e.g. SA-2024-XXXXX" oninput="update()">

    <label for="site">Site Name</label>
    <div class="field-row">
      <input type="text" id="site" name="site"
             placeholder="e.g. Lincoln Elementary" oninput="update()">
      <button type="button" id="site-lock-btn" class="lock-btn"
              onclick="toggleLock('site')" style="display:none" title="Edit / Lock">✏️</button>
    </div>

    <label for="asset">Asset Name</label>
    <div class="field-row">
      <input type="text" id="asset" name="asset_name"
             placeholder="e.g. Room 302" oninput="update()">
      <button type="button" id="asset-lock-btn" class="lock-btn"
              onclick="toggleLock('asset')" style="display:none" title="Edit / Lock">✏️</button>
    </div>

    <div class="migrate-row">
      <input type="checkbox" id="migrate" name="migrate">
      <label for="migrate" class="migrate-lbl">
        New monitoring location
        <span class="tip" tabindex="0" aria-label="What does this mean?">?
          <span class="tip-body">Check this when you are moving the sensor to a
            different physical location and want to keep the old location's
            historical data separate. Leave unchecked to simply rename the
            current monitoring point.</span>
        </span>
      </label>
    </div>

    <div id="env-section">
      <div class="sect">Environment</div>
      <div class="tog">
        <input type="radio" id="ev_in" name="environment" value="indoor" [[indoor_checked]]>
        <label for="ev_in">Indoor</label>
        <input type="radio" id="ev_out" name="environment" value="outdoor" [[outdoor_checked]]>
        <label for="ev_out">Outdoor</label>
      </div>
    </div>

    <div class="btn-row">
      <button type="button" id="reg-btn" class="btn btn-secondary"
              onclick="doRegister()" disabled>Register Device</button>
      <button type="button" id="wifi-btn" class="btn btn-primary"
              onclick="doConfigWifi()">Configure Wi-Fi →</button>
    </div>
  </form>
</div>
<script>
const INIT = [[init_json]];

const tokenEl    = () => document.getElementById('token');
const siteEl     = () => document.getElementById('site');
const assetEl    = () => document.getElementById('asset');
const siteLockBtn  = document.getElementById('site-lock-btn');
const assetLockBtn = document.getElementById('asset-lock-btn');
const regBtn     = document.getElementById('reg-btn');
const wifiBtn    = document.getElementById('wifi-btn');
const envSection = document.getElementById('env-section');
const noticeEl   = document.getElementById('notice');

const locked = {site: INIT.siteLocked, asset: INIT.assetLocked};

function applyLock(field) {
  const el   = field === 'site' ? siteEl() : assetEl();
  const btn  = field === 'site' ? siteLockBtn : assetLockBtn;
  el.disabled = locked[field];
  btn.textContent = locked[field] ? '✏️' : '🔒';
}

function init() {
  siteEl().value  = INIT.site;
  assetEl().value = INIT.asset;
  if (INIT.site)  { siteLockBtn.style.display  = ''; }
  if (INIT.asset) { assetLockBtn.style.display = ''; }
  applyLock('site');
  applyLock('asset');
  update();
}

function toggleLock(field) {
  const el      = field === 'site' ? siteEl() : assetEl();
  const initVal = field === 'site' ? INIT.site : INIT.asset;
  if (locked[field]) {
    locked[field] = false;
    applyLock(field);
    el.focus();
  } else {
    if (!initVal) {
      showNotice('No saved value to revert to — leave this field unlocked.', 'err');
      return;
    }
    locked[field] = true;
    el.value = initVal;
    applyLock(field);
  }
  update();
}

function update() {
  const token = tokenEl().value.trim();
  const site  = siteEl().value.trim();
  const asset = assetEl().value.trim();
  const allFilled   = !!(token && site && asset);
  const siteChanged  = !locked.site  && site  !== INIT.site;
  const assetChanged = !locked.asset && asset !== INIT.asset;
  regBtn.disabled = !(allFilled && (siteChanged || assetChanged));
  envSection.style.display = site === 'LEGACY' ? 'none' : '';
}

function showNotice(msg, type) {
  noticeEl.className = 'notice notice-' + type;
  noticeEl.textContent = msg;
  noticeEl.style.display = '';
  if (type === 'ok') setTimeout(() => { noticeEl.style.display = 'none'; }, 5000);
}

async function doRegister() {
  const token   = tokenEl().value.trim();
  const site    = siteEl().value.trim();
  const asset   = assetEl().value.trim();
  const env     = document.querySelector('input[name="environment"]:checked')?.value || 'indoor';
  const migrate = document.getElementById('migrate').checked;
  regBtn.disabled = true;
  const orig = regBtn.textContent;
  regBtn.textContent = 'Registering…';
  try {
    const r = await fetch('/register', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({token, site, asset_name: asset, environment: env, migrate}),
    });
    const d = await r.json();
    if (d.ok) showNotice('Device registered successfully.', 'ok');
    else showNotice(d.error || 'Registration failed.', 'err');
  } catch { showNotice('Could not reach the device. Try again.', 'err'); }
  regBtn.textContent = orig;
  update();
}

async function doConfigWifi() {
  const token = tokenEl().value.trim();
  const site  = siteEl().value.trim();
  const asset = assetEl().value.trim();
  const missing = [];
  if (!token) missing.push('Token');
  if (!site)  missing.push('Site');
  if (!asset) missing.push('Asset');
  if (missing.length) { showNotice(missing.join(' and ') + ' cannot be empty.', 'err'); return; }
  const env     = document.querySelector('input[name="environment"]:checked')?.value || 'indoor';
  const migrate = document.getElementById('migrate').checked;
  document.getElementById('quote-notice').innerHTML = '<em>The WiFi Wizard will reveal itself once authentication is complete…</em>';
  wifiBtn.disabled = true;
  wifiBtn.textContent = 'Verifying…';
  try {
    const r = await fetch('/configure-wifi', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({token, site, asset_name: asset, environment: env, migrate}),
    });
    const d = await r.json();
    if (d.redirect) { window.location.href = d.redirect; return; }
    showNotice(d.error || 'Verification failed.', 'err');
  } catch { showNotice('Could not reach the device. Try again.', 'err'); }
  wifiBtn.disabled = false;
  wifiBtn.textContent = 'Configure Wi-Fi →';
}

init();
</script>
</body></html>"""


# ── Page 2: Wi-Fi Configuration ───────────────────────────────────────────────

WIFI_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SchoolAir – Wi-Fi Setup</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
     background:linear-gradient(135deg,#667eea,#764ba2);min-height:100vh;
     display:flex;align-items:center;justify-content:center;padding:1rem}
.card{background:#fff;border-radius:16px;padding:2rem;max-width:440px;
      width:100%;box-shadow:0 8px 32px rgba(0,0,0,.18)}
.logo{text-align:center;margin-bottom:1.25rem}
.logo h1{font-size:1.4rem;color:#1a56db;font-weight:700}
.logo p{color:#6b7280;font-size:.8rem;margin-top:.25rem}
.badge{display:inline-block;background:#ecfdf5;color:#065f46;border:1.5px solid #6ee7b7;
       border-radius:20px;padding:.2rem .75rem;font-size:.78rem;font-weight:600;margin-top:.4rem}
.badge-none{background:#f9fafb;color:#6b7280;border-color:#e5e7eb}
.sect{font-size:.72rem;font-weight:600;color:#6b7280;text-transform:uppercase;
      letter-spacing:.05em;margin:1.25rem 0 .4rem}
label{display:block;font-size:.875rem;font-weight:500;color:#374151;
      margin-bottom:.2rem;margin-top:.65rem}
input[type=text],input[type=password]{width:100%;padding:.6rem .75rem;
  border:1.5px solid #d1d5db;border-radius:8px;font-size:1rem;outline:none;
  transition:border-color .2s}
input:focus{border-color:#1a56db}
.pw{position:relative}
.pw input{padding-right:3.5rem}
.pw button{position:absolute;right:.75rem;top:50%;transform:translateY(-50%);
  background:none;border:none;cursor:pointer;color:#6b7280;font-size:.85rem;padding:.2rem}
.notice{padding:.75rem;border-radius:8px;font-size:.875rem;margin:.5rem 0}
.notice-ok{background:#ecfdf5;color:#065f46;border:1.5px solid #6ee7b7}
.notice-err{background:#fef2f2;color:#dc2626;border:1.5px solid #fca5a5}
.btn{width:100%;margin-top:1rem;padding:.8rem;border:none;border-radius:10px;
     font-size:.95rem;font-weight:600;cursor:pointer;transition:background .2s}
.btn-blue{background:#1a56db;color:#fff}
.btn-blue:hover{background:#1649c0}
.btn-warn{background:#fff7ed;color:#92400e;border:1.5px solid #fcd34d}
.btn-warn:hover{background:#fef3c7}
.btn-danger{background:#fef2f2;color:#dc2626;border:1.5px solid #fca5a5;margin-top:.5rem}
.btn-danger:hover{background:#fee2e2}
.network-row{display:flex;align-items:center;padding:.55rem .75rem;
  border:1.5px solid #e5e7eb;border-radius:8px;margin-top:.4rem}
.net-active{font-size:.75rem;color:#059669;margin-left:.4rem}
.net-name{font-size:.875rem;color:#374151;font-weight:500;word-break:break-all;
  flex:1;min-width:0;margin-right:.5rem}
.net-controls{display:flex;align-items:center;gap:.3rem;flex-shrink:0}
.forget-btn{padding:.3rem .6rem;border:1.5px solid #fca5a5;border-radius:6px;
  background:#fff;color:#dc2626;font-size:.95rem;cursor:pointer;line-height:1}
.forget-btn:hover{background:#fef2f2}
.prio-btn{padding:.3rem .5rem;border:1.5px solid #c7d2fe;border-radius:6px;
  background:#eef2ff;color:#3730a3;font-size:.95rem;cursor:pointer;line-height:1}
.prio-btn:hover{background:#e0e7ff}
.net-priority{font-size:.7rem;color:#6b7280;font-family:monospace;background:#f3f4f6;
  border-radius:4px;padding:.1rem .35rem;white-space:nowrap}
.empty-msg{font-size:.85rem;color:#9ca3af;padding:.5rem 0}
.back-link{display:block;text-align:center;margin-top:1.25rem;font-size:.8rem;color:#6b7280}
.back-link a{color:#1a56db;text-decoration:none}
.divider{border:none;border-top:1px solid #e5e7eb;margin:1.25rem 0}
.btn-scan{width:100%;margin-top:.75rem;padding:.65rem;border:1.5px solid #1a56db;
  border-radius:10px;background:#eff6ff;color:#1a56db;font-size:.9rem;font-weight:600;
  cursor:pointer;transition:background .2s}
.btn-scan:hover:not(:disabled){background:#dbeafe}
.btn-scan:disabled{opacity:.5;cursor:not-allowed}
.scan-list{margin-top:.4rem}
.scan-item{display:flex;align-items:center;justify-content:space-between;
  padding:.5rem .75rem;border:1.5px solid #e5e7eb;border-radius:8px;
  margin-top:.35rem;cursor:pointer;transition:border-color .15s,background .15s}
.scan-item:hover{border-color:#1a56db;background:#eff6ff}
.scan-ssid{font-size:.875rem;color:#374151;font-weight:500;word-break:break-all}
.scan-meta{font-size:.75rem;color:#6b7280;white-space:nowrap;margin-left:.5rem}
.notice-quote{background:#eef2ff;color:#3730a3;border:1.5px solid #a5b4fc;
  padding:.65rem .85rem;border-radius:8px;font-size:.82rem;font-style:italic;margin-bottom:.75rem}
.quote-author{font-size:.72rem;font-weight:600;font-style:normal;display:block;margin-top:.2rem}
</style>
</head>
<body>
<div class="card">
  <div class="logo">
    <h1>[[wizard_emoji]] Wi-Fi Configuration</h1>
    <div class="[[conn_badge_class]]">[[conn_status]]</div>
  </div>
  <div id="quote-notice" class="notice-quote"><em>"[[quote]]"</em><span class="quote-author">— [[quote_author]]</span></div>

  <div id="notice" class="notice" style="display:none"></div>

  <div class="sect">Add Network</div>
  <button type="button" id="scan-btn" class="btn-scan" onclick="doScan()">🔍 Scan for Networks</button>
  <div id="scan-list" class="scan-list" onclick="handleScanClick(event)"></div>
  <form onsubmit="doConnect(event)">
    <label for="ssid">Network Name (SSID)</label>
    <input type="text" id="ssid" name="ssid" required placeholder="School Wi-Fi name (or scan above)">
    <label for="password">Password</label>
    <div class="pw">
      <input type="password" id="password" name="password"
             placeholder="Leave blank for open networks">
      <button type="button" onclick="tpw()">Show</button>
    </div>
    <button type="submit" class="btn btn-blue">Connect →</button>
  </form>

  [[saved_networks_html]]

  <hr class="divider">

  <button type="button" class="btn btn-warn" onclick="doForgetAll()">
    🗑 Forget All Saved Networks
  </button>
  <button type="button" class="btn btn-danger" onclick="doReboot()">
    ↻ Reboot Device
  </button>

  <span class="back-link"><a href="/">← Back to Registration</a></span>
</div>
<script>
const SESSION="[[session_token]]";
function authHdr(){return{'Content-Type':'application/json','X-Session':SESSION};}
function tpw(){
  const f=document.getElementById('password'),b=f.nextElementSibling;
  if(f.type==='password'){f.type='text';b.textContent='Hide';}
  else{f.type='password';b.textContent='Show';}
}
function showNotice(msg,type){
  const el=document.getElementById('notice');
  const qn=document.getElementById('quote-notice');
  el.className='notice notice-'+type;el.textContent=msg;el.style.display='';
  if(qn) qn.style.display='none';
  if(type==='ok') setTimeout(()=>{el.style.display='none';if(qn) qn.style.display='';},4000);
}
function signalBars(s){
  if(s>=75)return'||||';
  if(s>=50)return'||| ';
  if(s>=25)return'||  ';
  return'|   ';
}
function handleScanClick(e){
  const item=e.target.closest('.scan-item');
  if(!item) return;
  document.getElementById('ssid').value=item.dataset.ssid;
  if(item.dataset.secured==='1') document.getElementById('password').focus();
  else document.getElementById('ssid').focus();
}
async function doScan(){
  const btn=document.getElementById('scan-btn');
  const list=document.getElementById('scan-list');
  btn.disabled=true;btn.textContent='Scanning…';list.innerHTML='';
  try{
    const r=await fetch('/wifi/scan',{method:'POST',headers:authHdr()});
    const d=await r.json();
    if(d.error){showNotice(d.error,'err');return;}
    const nets=d.networks||[];
    if(!nets.length){list.innerHTML='<p class="empty-msg">No networks found. You may be in AP-only mode.</p>';return;}
    list.innerHTML=nets.map(n=>{
      const safe=n.ssid.replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      const disp=n.ssid.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      return`<div class="scan-item" data-ssid="${safe}" data-secured="${n.secured?'1':'0'}">
        <span class="scan-ssid">${disp}</span>
        <span class="scan-meta">${signalBars(n.signal)} ${n.secured?'🔒':'🔓'}</span>
      </div>`;
    }).join('');
  }catch{showNotice('Scan failed — try again.','err');}
  btn.disabled=false;btn.textContent='🔍 Scan for Networks';
}
function doConnect(e){
  e.preventDefault();
  const ssid=document.getElementById('ssid').value.trim();
  if(!ssid){showNotice('Please enter the network name.','err');return;}
  const pw=document.getElementById('password').value;
  const form=document.createElement('form');
  form.method='POST';form.action='/wifi/connect';
  [['ssid',ssid],['password',pw],['session',SESSION]].forEach(([k,v])=>{
    const i=document.createElement('input');i.type='hidden';i.name=k;i.value=v;form.appendChild(i);
  });
  document.body.appendChild(form);form.submit();
}
let forgetConfirmed=false;
function doForget(profile,isActive){
  const ssid=profile.replace(/^schoolair-/,'').replace(/_/g,' ');
  if(isActive){
    if(prompt('⚠️ This is the ACTIVE network.\\nForgetting it will disconnect the device.\\n\\nType DELETE to confirm:')!=='DELETE') return;
  } else if(!forgetConfirmed){
    if(prompt('Type DELETE to forget "'+ssid+'":')!=='DELETE') return;
    forgetConfirmed=true;
  }
  fetch('/wifi/forget',{method:'POST',headers:authHdr(),body:JSON.stringify({profile})})
    .then(r=>r.json())
    .then(d=>{if(d.ok)location.reload();else showNotice(d.error||'Failed.','err');})
    .catch(()=>showNotice('Request failed.','err'));
}
function doPrioritize(profile){
  fetch('/wifi/prioritize',{method:'POST',headers:authHdr(),body:JSON.stringify({profile})})
    .then(r=>r.json())
    .then(d=>{if(d.ok)location.reload();else showNotice(d.error||'Failed.','err');})
    .catch(()=>showNotice('Request failed.','err'));
}
async function doForgetAll(){
  if(prompt('⚠️ This will disconnect the device from ALL saved networks.\\n\\nType DELETE to confirm:')!=='DELETE') return;
  try{
    const d=await(await fetch('/wifi/forget-all',{method:'POST',headers:authHdr()})).json();
    if(d.ok) location.reload(); else showNotice(d.error||'Failed.','err');
  }catch{showNotice('Request failed.','err');}
}
function doReboot(){
  if(!confirm('Reboot the device now?')) return;
  fetch('/wifi/reboot',{method:'POST',headers:authHdr()});
  showNotice('Rebooting…','ok');
}
</script>
</body></html>"""


# ── Connecting page (shown while background task runs) ────────────────────────

CONNECTING_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SchoolAir – Connecting</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
     background:linear-gradient(135deg,#667eea,#764ba2);min-height:100vh;
     display:flex;align-items:center;justify-content:center;padding:1rem}
.card{background:#fff;border-radius:16px;padding:2rem 1.5rem;max-width:420px;
      width:100%;text-align:center;box-shadow:0 8px 32px rgba(0,0,0,.18)}
h1{font-size:1.4rem;color:#1a56db;margin-bottom:.5rem}
#icon{font-size:3rem;margin:1.5rem 0}
#msg{color:#374151;font-size:1rem;min-height:3rem;line-height:1.5}
#hint{color:#6b7280;font-size:.8rem;margin-top:1rem;padding:.75rem;
      background:#f9fafb;border-radius:8px;display:none;line-height:1.5}
.spin{display:inline-block;width:2.5rem;height:2.5rem;
      border:4px solid #e5e7eb;border-top-color:#1a56db;
      border-radius:50%;animation:sp .8s linear infinite}
@keyframes sp{to{transform:rotate(360deg)}}
#retry{display:none;margin-top:1.5rem;padding:.75rem 1.5rem;
       background:#1a56db;color:#fff;border:none;border-radius:8px;
       font-size:1rem;cursor:pointer}
.quote-block{margin-top:1.25rem;font-size:.82rem;color:#3730a3;
  background:#eef2ff;border:1.5px solid #a5b4fc;border-radius:8px;
  padding:.65rem .85rem;font-style:italic;line-height:1.5;text-align:left}
.q-author{font-size:.72rem;font-weight:600;font-style:normal;color:#3730a3;
  display:block;margin-top:.2rem}
</style>
</head>
<body>
<div class="card">
  <h1>[[wizard_emoji]] SchoolAir Setup</h1>
  <div id="icon"><div class="spin"></div></div>
  <div id="msg">Connecting to network&hellip;</div>
  <div id="hint"></div>
  <div class="quote-block" style="display:none"><em>"[[quote]]"</em><span class="q-author">— [[quote_author]]</span></div>
  <button id="retry" onclick="location.href='[[retry_url]]'">Try Again</button>
</div>
<script>
const AP_DROP="The setup hotspot dropped — the device is connecting to the school Wi-Fi. "+
  "If successful, registration is complete. If the hotspot reappears within 60 seconds, "+
  "tap Try Again to review the error.";
function icon(t){const el=document.getElementById('icon');
  if(t==='spin')el.innerHTML='<div class="spin"></div>';else el.textContent=t;}
function hint(t){const h=document.getElementById('hint');h.textContent=t;h.style.display='block';}
function msg(t){document.getElementById('msg').textContent=t;}
function showQuote(){const q=document.querySelector('.quote-block');if(q)q.style.display='';}
let ws,dropped=false,reconnTimer;
function connect(){
  const proto=location.protocol==='https:'?'wss':'ws';
  ws=new WebSocket(proto+'://'+location.host+'/ws/status');
  ws.onmessage=function(e){
    const d=JSON.parse(e.data);
    if(d.state==='ping')return;
    msg(d.message);
    if(d.state==='success'){icon('✅');hint('Registration complete. This hotspot will close shortly.');showQuote();}
    else if(d.state==='error'){icon('❌');document.getElementById('retry').style.display='inline-block';dropped=true;ws.close();}
  };
  ws.onclose=function(){
    clearTimeout(reconnTimer);
    if(!dropped){dropped=true;icon('📶');msg(AP_DROP);
      hint('On Pi Zero hardware the setup hotspot may drop during connection — this is normal.');}
    reconnTimer=setTimeout(connect,3000);
  };
  ws.onerror=function(){ws.close();};
}
connect();
</script>
</body></html>"""


# ── Persistence helpers ───────────────────────────────────────────────────────

# Resolve the admin user's uid/gid once at import time so we can chown files
# written by this process (which runs as root) back to the owning user.
try:
    _pw = pwd.getpwnam("admin")
    _ADMIN_UID, _ADMIN_GID = _pw.pw_uid, _pw.pw_gid
except KeyError:
    _ADMIN_UID, _ADMIN_GID = -1, -1


def _fix_owner(path: str) -> None:
    """Re-assign ownership of a file or directory from root to admin."""
    if _ADMIN_UID >= 0:
        try:
            os.chown(path, _ADMIN_UID, _ADMIN_GID)
        except OSError:
            pass


def _write_env_key(key: str, token: str) -> None:
    """Upsert a single key=value line in pi-main's .env."""
    content = ""
    if os.path.exists(PI_MAIN_ENV_PATH):
        with open(PI_MAIN_ENV_PATH) as f:
            content = f.read()
    pattern = rf"^{re.escape(key)}=.*$"
    if re.search(pattern, content, re.MULTILINE):
        content = re.sub(pattern, f"{key}={token}", content, flags=re.MULTILINE)
    else:
        content += f"\n{key}={token}\n"
    with open(PI_MAIN_ENV_PATH, "w") as f:
        f.write(content)
    _fix_owner(PI_MAIN_ENV_PATH)


def _write_auth_token(token: str) -> None:
    """Write AUTH_TOKEN (legacy secondary server) into pi-main's .env."""
    _write_env_key("AUTH_TOKEN", token)


def _write_new_auth_token(token: str) -> None:
    """Write NEW_AUTH_TOKEN (primary AWS server) into pi-main's .env.

    This is the device-specific token issued by the server on registration —
    not the org provisioning token used to make the request.  The ingest
    service reads this key to authenticate every drain.
    """
    _write_env_key("NEW_AUTH_TOKEN", token)


def _ensure_dir() -> None:
    created = not os.path.exists(CONFIG_DIR)
    os.makedirs(CONFIG_DIR, exist_ok=True)
    if created:
        _fix_owner(CONFIG_DIR)


def write_staging(data: dict) -> None:
    _ensure_dir()
    tmp = STAGING_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, STAGING_FILE)
    _fix_owner(STAGING_FILE)


def read_staging() -> dict:
    try:
        with open(STAGING_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_status(data: dict) -> None:
    _ensure_dir()
    tmp = STATUS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, STATUS_FILE)
    _fix_owner(STATUS_FILE)


def write_error(message: str) -> None:
    _ensure_dir()
    with open(ERROR_FILE, "w") as f:
        f.write(f"{datetime.now(timezone.utc).isoformat()}  {message}\n")
    _fix_owner(ERROR_FILE)


def status_exists() -> bool:
    return os.path.exists(STATUS_FILE)


def read_wizard_registration() -> dict:
    try:
        with open(STATUS_FILE) as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("token"):
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {}


def read_node_red_registration() -> dict:
    try:
        with open(NODE_RED_TOKEN_FILE) as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("token"):
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {}


# ── Network helpers ───────────────────────────────────────────────────────────

def _profile_name(ssid: str) -> str:
    """Convert an SSID to a safe nmcli connection profile name."""
    safe = re.sub(r"[^a-zA-Z0-9._-]", "_", ssid)
    return f"{SAVED_PREFIX}{safe}"


async def _cmd(cmd: str) -> tuple:
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return proc.returncode, out.decode().strip(), err.decode().strip()


async def _current_wifi() -> tuple:
    """Return (profile_name, display_ssid) of active non-AP wifi, or ('', '')."""
    _, out, _ = await _cmd("nmcli -t -f NAME,TYPE,STATE con show --active")
    for line in out.splitlines():
        parts = line.split(":")
        if len(parts) < 3:
            continue
        name, ctype, state = parts[0], parts[1], parts[2]
        if ctype in ("wifi", "802-11-wireless") and state == "activated" and name != AP_CONNECTION_NAME:
            display = name[len(SAVED_PREFIX):].replace("_", " ") if name.startswith(SAVED_PREFIX) else name
            return name, display
    return "", ""


async def _list_saved_profiles() -> list:
    """Return list of {name, priority} dicts for committed schoolair-* profiles."""
    _, out, _ = await _cmd("nmcli -t -f NAME con show")
    names = [
        line.strip() for line in out.splitlines()
        if line.strip().startswith(SAVED_PREFIX) and line.strip() != TEMP_PROFILE
    ]
    profiles = []
    for name in names:
        _, pout, _ = await _cmd(
            f'nmcli -t -f connection.autoconnect-priority con show "{name}"')
        try:
            priority = int(pout.split(":")[-1].strip())
        except (ValueError, IndexError):
            priority = 0
        profiles.append({"name": name, "priority": priority})
    profiles.sort(key=lambda x: -x["priority"])
    return profiles


def _saved_networks_html(profiles: list, current_profile: str) -> str:
    if not profiles:
        return '<div class="sect">Saved Networks</div><p class="empty-msg">No saved networks.</p>'
    rows = []
    for p in profiles:
        profile   = p["name"]
        priority  = p["priority"]
        ssid      = profile[len(SAVED_PREFIX):].replace("_", " ")
        is_active = "1" if profile == current_profile else "0"
        is_active_js = "true" if profile == current_profile else "false"
        active_tag   = '<span class="net-active">● active</span>' if profile == current_profile else ""
        rows.append(
            f'<div class="network-row">'
            f'<span class="net-name">{_html.escape(ssid)}{active_tag}</span>'
            f'<div class="net-controls">'
            f'<button type="button" class="prio-btn" title="Prioritize this network"'
            f" onclick=\"doPrioritize('{profile}')\">⬆️</button>"
            f'<span class="net-priority">P{priority}</span>'
            f'<button type="button" class="forget-btn" title="Forget this network"'
            f" onclick=\"doForget('{profile}',{is_active_js})\">🗑️</button>"
            f'</div>'
            f'</div>'
        )
    return '<div class="sect">Saved Networks</div>' + "".join(rows)


async def _scan_networks() -> list:
    """Scan for available Wi-Fi networks; return list sorted by signal strength."""
    await _cmd(f"nmcli dev wifi rescan ifname {AP_INTERFACE} 2>/dev/null; true")
    await asyncio.sleep(3)
    _, out, _ = await _cmd(
        f"nmcli -t -f SSID,SIGNAL,SECURITY dev wifi list ifname {AP_INTERFACE}"
    )
    seen: dict = {}
    for line in out.splitlines():
        parts = line.split(":")
        ssid = parts[0].strip()
        if not ssid:
            continue
        try:
            signal = int(parts[1]) if len(parts) > 1 else 0
        except ValueError:
            signal = 0
        security = parts[2].strip() if len(parts) > 2 else ""
        secured = bool(security and security not in ("--", "none", "None", ""))
        if ssid not in seen or signal > seen[ssid]["signal"]:
            seen[ssid] = {"ssid": ssid, "signal": signal, "secured": secured}
    return sorted(seen.values(), key=lambda x: -x["signal"])


async def _setup_client_profile(ssid: str, password: str) -> tuple:
    """Create (or replace) the temporary staging Wi-Fi connection profile."""
    committed = _profile_name(ssid)
    await _cmd(f'nmcli con delete "{TEMP_PROFILE}" 2>/dev/null; true')
    await _cmd(f'nmcli con delete "{committed}" 2>/dev/null; true')
    if password:
        rc, _, err = await _cmd(
            f'nmcli con add type wifi ifname {AP_INTERFACE} '
            f'con-name "{TEMP_PROFILE}" '
            f'ssid "{ssid}" '
            f'wifi-sec.key-mgmt wpa-psk '
            f'wifi-sec.psk "{password}" '
            f'ipv4.method auto '
            f'ipv6.method ignore'
        )
    else:
        rc, _, err = await _cmd(
            f'nmcli con add type wifi ifname {AP_INTERFACE} '
            f'con-name "{TEMP_PROFILE}" '
            f'ssid "{ssid}" '
            f'ipv4.method auto '
            f'ipv6.method ignore'
        )
    if rc != 0:
        return False, f"Could not create connection profile: {err}"
    return True, "ok"


async def _wait_for_ip(timeout: int = 30) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rc, out, _ = await _cmd(f"nmcli -t -f IP4.ADDRESS dev show {AP_INTERFACE}")
        if rc == 0 and out.strip():
            return True
        await asyncio.sleep(2)
    return False


async def _revert_to_ap() -> None:
    await _cmd(f'nmcli con delete "{TEMP_PROFILE}" 2>/dev/null; true')
    rc, _, _ = await _cmd(f'nmcli con up "{AP_CONNECTION_NAME}" 2>/dev/null')
    if rc != 0:
        await _cmd("systemctl restart hostapd 2>/dev/null; true")


def _get_cpu_serial() -> str:
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("Serial"):
                    return line.split(":")[1].strip()
    except OSError:
        pass
    return "unknown"


def _get_mac_address() -> str:
    """Return MAC of eth0, wlan0, or the first available non-loopback interface."""
    import pathlib
    for iface in ("eth0", "wlan0"):
        try:
            return pathlib.Path(f"/sys/class/net/{iface}/address").read_text().strip()
        except OSError:
            pass
    for p in pathlib.Path("/sys/class/net").iterdir():
        if p.name == "lo":
            continue
        try:
            return (p / "address").read_text().strip()
        except OSError:
            pass
    return "unknown"


# ── Cloud calls ───────────────────────────────────────────────────────────────

async def _post_legacy_registration(org_token: str, nickname: str) -> tuple:
    body = json.dumps({"cpu_serial": _get_cpu_serial(), "nickname": nickname}).encode()

    def _do() -> tuple:
        req = urllib.request.Request(
            LEGACY_URL, data=body,
            headers={"Authorization": f"Bearer {org_token}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=HEARTBEAT_TIMEOUT) as r:
            return r.status, r.read().decode()

    loop = asyncio.get_running_loop()
    try:
        code, body_str = await loop.run_in_executor(None, _do)
        if code == 200:
            try:
                resp = json.loads(body_str)
            except (json.JSONDecodeError, ValueError):
                resp = {}
            return True, "Registered successfully", resp
        return False, f"Server returned HTTP {code}", {}
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return False, "Token rejected by SchoolAir Cloud", {}
        return False, f"Server error HTTP {exc.code}", {}
    except urllib.error.URLError as exc:
        return False, f"Could not reach SchoolAir Cloud: {exc.reason}", {}
    except Exception as exc:
        return False, f"Legacy registration failed: {exc}", {}


async def _post_heartbeat(payload: dict) -> tuple[bool, str, str]:
    """POST to the primary server's register endpoint.

    Returns (success, message, device_auth_token).  device_auth_token is the
    token the server issues for this specific device — it must be written to
    NEW_AUTH_TOKEN in .env so the ingest service can authenticate drains.
    On failure, device_auth_token is an empty string.
    """
    token = payload.pop("token", "")
    body = json.dumps(payload).encode()

    def _do() -> tuple:
        req = urllib.request.Request(
            HEARTBEAT_URL, data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=HEARTBEAT_TIMEOUT) as r:
            return r.status, r.read().decode()

    loop = asyncio.get_running_loop()
    try:
        code, resp_body = await loop.run_in_executor(None, _do)
        print(f"[heartbeat] HTTP {code}: {resp_body[:200]}")
        if code == 200:
            try:
                device_auth_token = json.loads(resp_body).get("auth_token", "")
            except Exception:
                device_auth_token = ""
            return True, "Registered successfully", device_auth_token
        return False, f"Server returned HTTP {code}", ""
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode()[:200]
        except Exception:
            pass
        print(f"[heartbeat] HTTP {exc.code}: {body}")
        if exc.code in (401, 403):
            return False, "Token rejected by SchoolAir Cloud", ""
        return False, f"Server error HTTP {exc.code}", ""
    except urllib.error.URLError as exc:
        return False, f"Could not reach SchoolAir Cloud: {exc.reason}", ""
    except Exception as exc:
        return False, f"Heartbeat failed: {exc}", ""


# ── Registration background task ──────────────────────────────────────────────

def _set(state: str, message: str) -> None:
    reg_state["state"] = state
    reg_state["message"] = message


async def run_connect(ssid: str, password: str) -> None:
    """
    Connect to WiFi and conditionally register with SchoolAir Cloud.

    Uses wifi_session for all registration metadata.  If pre_verified is True
    the cloud was already contacted on Page 1, so we skip the cloud call here
    and only update local state.  If pre_verified is False (no internet at
    Page 1 time) we do the full cloud registration after getting an IP.
    """
    # 1. Create temp profile
    _set("connecting", f'Adding connection profile for "{ssid}"…')
    ok, msg = await _setup_client_profile(ssid, password)
    if not ok:
        _set("error", msg)
        write_error(msg)
        await _revert_to_ap()
        return

    # 2. Bring up connection
    _set("connecting", f'Connecting to "{ssid}"…')
    rc, _, err = await _cmd(f'nmcli con up "{TEMP_PROFILE}"')
    if rc != 0:
        detail = err or "Check SSID and password."
        msg = f'Could not connect to "{ssid}": {detail}'
        _set("error", msg)
        write_error(msg)
        await _revert_to_ap()
        return

    # 3. Wait for IP
    _set("wifi_up", f'Joined "{ssid}". Waiting for IP address…')
    got_ip = await _wait_for_ip(timeout=30)
    if not got_ip:
        msg = f'Joined "{ssid}" but did not receive an IP address within 30 s.'
        _set("error", msg)
        write_error(msg)
        await _revert_to_ap()
        return

    # 4. Cloud call (only if not pre-verified)
    success = True
    hb_msg = ""
    device_auth_token = ""
    legacy_resp: dict = wifi_session.get("legacy_resp", {})

    if not wifi_session["pre_verified"]:
        sess = wifi_session
        if sess["site"] == "LEGACY":
            _set("heartbeat", "On the school network. Sending legacy registration…")
            success, hb_msg, legacy_resp = await _post_legacy_registration(
                sess["token"], sess["asset"])
        else:
            _set("heartbeat", "On the school network. Sending registration…")
            payload = {
                "token":       sess["token"],
                "mac_address": _get_mac_address(),
                "cpu_serial":  _get_cpu_serial(),
                "nickname":    sess["asset"],
                "migrate":     sess.get("migrate", False),
                "new_asset":   {"nickname": sess["asset"], "type": sess["environment"], "site_name": sess["site"] or None},
            }
            success, hb_msg, device_auth_token = await _post_heartbeat(payload)

    if success:
        # 5. Commit temp profile → permanent named profile; set incremental priority
        committed = _profile_name(ssid)
        await _cmd(f'nmcli con modify "{TEMP_PROFILE}" connection.id "{committed}"')
        all_profiles = await _list_saved_profiles()
        other_max = max((p["priority"] for p in all_profiles if p["name"] != committed), default=0)
        await _cmd(f'nmcli con modify "{committed}" connection.autoconnect-priority {other_max + 1}')

        # 6. Write local status (LEGACY devices keep device_token as their record)
        sess = wifi_session
        if sess["site"] != "LEGACY":
            write_status({
                "token":         sess["token"],
                "site":          sess["site"],
                "asset_name":    sess["asset"],
                "environment":   sess["environment"],
                "ssid":          ssid,
                "registered_at": datetime.now(timezone.utc).isoformat(),
            })
            try:
                _write_auth_token(sess["token"])
            except Exception as e:
                print(f"[wizard] Warning: could not write AUTH_TOKEN to pi-main .env: {e}")
            if device_auth_token:
                try:
                    _write_new_auth_token(device_auth_token)
                except Exception as e:
                    print(f"[wizard] Warning: could not write NEW_AUTH_TOKEN to pi-main .env: {e}")

        if sess["site"] == "LEGACY":
            device_token = {
                "token":     legacy_resp.get("token", sess["token"]),
                "device_id": legacy_resp.get("device_id", ""),
                "nickname":  sess["asset"],
            }
            with open(NODE_RED_TOKEN_FILE, "w") as _f:
                json.dump(device_token, _f)
            _fix_owner(NODE_RED_TOKEN_FILE)
            try:
                _write_auth_token(device_token["token"])
            except Exception as e:
                print(f"[wizard] Warning: could not write AUTH_TOKEN to pi-main .env: {e}")

        try:
            os.remove(STAGING_FILE)
        except FileNotFoundError:
            pass

        _set("success", "Registration complete! This hotspot will shut down shortly.")
        asyncio.create_task(_delayed_shutdown())
    else:
        # Cloud rejected — delete temp profile, restore AP
        await _cmd(f'nmcli con delete "{TEMP_PROFILE}" 2>/dev/null; true')
        _set("error", f"Wi-Fi connected, but: {hb_msg}")
        write_error(hb_msg)
        await _revert_to_ap()


async def _delayed_shutdown() -> None:
    await asyncio.sleep(6)
    await _cmd("iptables -t nat -D PREROUTING -i wlan0 -p tcp --dport 80  -j REDIRECT --to-port 80  2>/dev/null; true")
    await _cmd("iptables -t nat -D PREROUTING -i wlan0 -p tcp --dport 443 -j REDIRECT --to-port 443 2>/dev/null; true")
    await _cmd(f'nmcli con down "{AP_CONNECTION_NAME}" 2>/dev/null; true')
    await _cmd("systemctl stop hostapd 2>/dev/null; true")
    await _cmd("systemctl disable hostapd 2>/dev/null; true")
    await _cmd("systemctl restart schoolair 2>/dev/null; true")
    await _cmd("systemctl stop schoolair-wizard 2>/dev/null; true")


# ── Idle watchdog ────────────────────────────────────────────────────────────

async def _ap_is_active() -> bool:
    """Return True if the SchoolAir AP hotspot is currently active."""
    _, out, _ = await _cmd("nmcli -t -f NAME,STATE con show --active")
    return AP_CONNECTION_NAME in out


def _has_token() -> bool:
    """Return True if AUTH_TOKEN is non-empty in the telemetry .env file."""
    try:
        with open(PI_MAIN_ENV_PATH) as f:
            for line in f:
                if line.startswith("AUTH_TOKEN=") and line[len("AUTH_TOKEN="):].strip():
                    return True
    except OSError:
        pass
    return False


async def _idle_watchdog() -> None:
    """Auto-shutdown after IDLE_TIMEOUT seconds of inactivity.

    In AP mode the wizard shuts itself down after a successful registration.
    On LAN (re-registration or first boot on an already-networked Pi), the idle
    timeout applies unconditionally — 15 minutes of no browser activity is
    plenty for any registration flow, and prevents the wizard sitting idle for
    days on an already-registered device.
    """
    await asyncio.sleep(5)  # brief settle after process start
    if await _ap_is_active():
        return  # AP mode: wizard self-shuts after successful registration
    while True:
        await asyncio.sleep(60)
        if time.time() - _last_activity > IDLE_TIMEOUT:
            print(f"[schoolair-wizard] No activity for {IDLE_TIMEOUT}s — shutting down.")
            await _cmd("systemctl stop schoolair-wizard 2>/dev/null; true")
            break


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
async def index(request):
    wiz = read_wizard_registration()
    nr  = read_node_red_registration()

    init_site   = ""
    init_asset  = ""
    site_locked = False
    asset_locked = False

    if wiz and wiz.get("site") == "LEGACY":
        # Old code may have written status.json with site=LEGACY; migrate it away.
        try:
            os.remove(STATUS_FILE)
        except FileNotFoundError:
            pass
        wiz = {}

    if wiz:
        init_site    = wiz.get("site", "")
        init_asset   = wiz.get("asset_name", "")
        site_locked  = bool(init_site)
        asset_locked = bool(init_asset)
    elif nr:
        # LEGACY: asset = nickname, site stays open
        init_asset   = nr.get("nickname", "")
        asset_locked = bool(init_asset)

    prefill = read_staging()
    env = prefill.get("environment", "indoor")

    init_json = json.dumps({
        "site":       init_site,
        "asset":      init_asset,
        "siteLocked":  site_locked,
        "assetLocked": asset_locked,
    })

    quote, author = _GANDALF_QUOTE
    body = _render(
        FORM_HTML,
        raw={
            "init_json":       init_json,
            "indoor_checked":  "checked" if env == "indoor" else "",
            "outdoor_checked": "checked" if env == "outdoor" else "",
            "wizard_emoji":    _wizard_emoji("m"),
            "quote":           quote,
            "quote_author":    author,
        },
    )
    return _html_response(body)


@app.route("/register", methods=["POST"])
async def do_register(request):
    """Register Device button — cloud registration only, no WiFi change."""
    data        = request.json or {}
    token       = data.get("token", "").strip()
    site        = data.get("site", "").strip()
    asset       = data.get("asset_name", "").strip()
    environment = data.get("environment", "indoor").strip()
    migrate     = bool(data.get("migrate", False))

    if not (token and site and asset):
        return _json_response({"error": "Token, Site, and Asset are required."}, 400)

    legacy_resp: dict = {}
    if site == "LEGACY":
        success, msg, legacy_resp = await _post_legacy_registration(token, asset)
    else:
        payload = {
            "token":       token,
            "mac_address": _get_mac_address(),
            "cpu_serial":  _get_cpu_serial(),
            "nickname":    asset,
            "migrate":     migrate,
            "new_asset":   {"nickname": asset, "type": environment, "site_name": site or None},
        }
        success, msg, device_auth_token = await _post_heartbeat(payload)

    if not success:
        return _json_response({"error": _friendly_error(msg)})

    if site == "LEGACY":
        device_token_value = legacy_resp.get("token", token)
        try:
            _write_auth_token(device_token_value)
        except Exception as e:
            print(f"[wizard] Warning: could not write AUTH_TOKEN to pi-main .env: {e}")
        with open(NODE_RED_TOKEN_FILE, "w") as _f:
            json.dump({
                "token":     device_token_value,
                "device_id": legacy_resp.get("device_id", ""),
                "nickname":  asset,
            }, _f)
        _fix_owner(NODE_RED_TOKEN_FILE)
    else:
        existing = read_wizard_registration()
        write_status({
            "token":         token,
            "site":          site,
            "asset_name":    asset,
            "environment":   environment,
            "ssid":          existing.get("ssid", ""),
            "registered_at": datetime.now(timezone.utc).isoformat(),
        })
        try:
            _write_auth_token(token)
        except Exception as e:
            print(f"[wizard] Warning: could not write AUTH_TOKEN to pi-main .env: {e}")
        if device_auth_token:
            try:
                _write_new_auth_token(device_auth_token)
            except Exception as e:
                print(f"[wizard] Warning: could not write NEW_AUTH_TOKEN to pi-main .env: {e}")

    # Restart the telemetry service so it picks up the new token immediately
    asyncio.create_task(_cmd("systemctl restart schoolair"))
    return _json_response({"ok": True})


@app.route("/configure-wifi", methods=["POST"])
async def configure_wifi(request):
    """Configure WiFi button — verify/register, set session, redirect to /wifi."""
    data        = request.json or {}
    token       = data.get("token", "").strip()
    site        = data.get("site", "").strip()
    asset       = data.get("asset_name", "").strip()
    environment = data.get("environment", "indoor").strip()
    migrate     = bool(data.get("migrate", False))

    if not (token and site and asset):
        return _json_response({"error": "Token, Site, and Asset are required."}, 400)

    pre_verified = False
    legacy_resp: dict = {}

    if site == "LEGACY":
        success, msg, legacy_resp = await _post_legacy_registration(token, asset)
        if not success:
            if "Could not reach" not in msg:
                return _json_response({"error": _friendly_error(msg)})
            # Genuine network unreachability — proceed deferred
        else:
            pre_verified = True
    else:
        payload = {
            "token":       token,
            "mac_address": _get_mac_address(),
            "cpu_serial":  _get_cpu_serial(),
            "nickname":    asset,
            "migrate":     migrate,
            "new_asset":   {"nickname": asset, "type": environment, "site_name": site or None},
        }
        success, msg, device_auth_token = await _post_heartbeat(payload)
        if not success:
            if "Could not reach" not in msg:
                return _json_response({"error": _friendly_error(msg)})
            # Genuine network unreachability — proceed deferred
        else:
            pre_verified = True
            existing = read_wizard_registration()
            write_status({
                "token": token, "site": site, "asset_name": asset,
                "environment": environment, "ssid": existing.get("ssid", ""),
                "registered_at": datetime.now(timezone.utc).isoformat(),
            })
            if device_auth_token:
                try:
                    _write_new_auth_token(device_auth_token)
                except Exception as e:
                    print(f"[wizard] Warning: could not write NEW_AUTH_TOKEN to pi-main .env: {e}")

    # Activate session — generate per-client token and redirect to /wifi?s=<token>
    session_token = secrets.token_urlsafe(16)
    wifi_session["active"]        = True
    wifi_session["session_token"] = session_token
    wifi_session["token"]         = token
    wifi_session["site"]          = site
    wifi_session["asset"]         = asset
    wifi_session["environment"]   = environment
    wifi_session["migrate"]       = migrate
    wifi_session["pre_verified"]  = pre_verified
    wifi_session["legacy_resp"]   = legacy_resp

    return _json_response({"redirect": f"/wifi?s={session_token}"})


@app.route("/wifi", methods=["GET"])
async def wifi_page(request):
    expected = wifi_session.get("session_token", "")
    if not expected or request.args.get("s") != expected:
        return Response("", status_code=302, headers={"Location": "/"})

    profiles = await _list_saved_profiles()
    current_profile, current_ssid = await _current_wifi()

    if current_ssid:
        conn_status = f"Connected: {current_ssid}"
        conn_badge_class = "badge"
    else:
        conn_status = "Not connected to a network"
        conn_badge_class = "badge badge-none"

    quote, author = _GLINDA_QUOTE
    body = _render(
        WIFI_HTML,
        raw={
            "saved_networks_html": _saved_networks_html(profiles, current_profile),
            "wizard_emoji":        _wizard_emoji("f"),
            "quote":               quote,
            "quote_author":        author,
        },
        conn_status=conn_status,
        conn_badge_class=conn_badge_class,
        session_token=expected,
    )
    return _html_response(body)


@app.route("/wifi/connect", methods=["POST"])
async def wifi_connect(request):
    if not _session_ok(request):
        return Response("", status_code=302, headers={"Location": "/"})

    f        = request.form
    ssid     = (f.get("ssid") or "").strip()
    password = (f.get("password") or "").strip()

    if not ssid:
        return Response("", status_code=302, headers={"Location": "/wifi"})

    _set("connecting", "Starting connection…")
    write_staging({"ssid": ssid, "environment": wifi_session.get("environment", "indoor")})
    asyncio.create_task(run_connect(ssid, password))
    quote, author = _RAINE_QUOTE
    return _html_response(_render(CONNECTING_HTML, raw={
        "retry_url":    "/wifi",
        "wizard_emoji": _wizard_emoji("n"),
        "quote":        quote,
        "quote_author": author,
    }))


@app.route("/wifi/scan", methods=["POST"])
async def wifi_scan(request):
    if not _session_ok(request):
        return _json_response({"error": "Not authorized."}, 403)
    networks = await _scan_networks()
    return _json_response({"networks": networks})


@app.route("/wifi/forget", methods=["POST"])
async def wifi_forget(request):
    if not _session_ok(request):
        return _json_response({"error": "Not authorized."}, 403)

    data    = request.json or {}
    profile = data.get("profile", "").strip()
    if not profile.startswith(SAVED_PREFIX) or profile == TEMP_PROFILE:
        return _json_response({"error": "Invalid profile name."}, 400)

    await _cmd(f'nmcli con delete "{profile}" 2>/dev/null; true')
    return _json_response({"ok": True})


@app.route("/wifi/forget-all", methods=["POST"])
async def wifi_forget_all(request):
    if not _session_ok(request):
        return _json_response({"error": "Not authorized."}, 403)

    profiles = await _list_saved_profiles()
    for p in profiles:
        await _cmd(f'nmcli con delete "{p["name"]}" 2>/dev/null; true')
    return _json_response({"ok": True})


@app.route("/wifi/reboot", methods=["POST"])
async def wifi_reboot(request):
    if not _session_ok(request):
        return _json_response({"error": "Not authorized."}, 403)
    asyncio.get_event_loop().call_later(1, lambda: os.system("sudo reboot"))
    return _json_response({"ok": True})


@app.route("/wifi/prioritize", methods=["POST"])
async def wifi_prioritize(request):
    if not _session_ok(request):
        return _json_response({"error": "Not authorized."}, 403)
    data    = request.json or {}
    profile = data.get("profile", "").strip()
    if not profile.startswith(SAVED_PREFIX) or profile == TEMP_PROFILE:
        return _json_response({"error": "Invalid profile."}, 400)
    profiles = await _list_saved_profiles()
    other_max = max((p["priority"] for p in profiles if p["name"] != profile), default=0)
    new_priority = other_max + 1
    await _cmd(f'nmcli con modify "{profile}" connection.autoconnect-priority {new_priority}')
    return _json_response({"ok": True, "priority": new_priority})


@app.route("/ws/status")
@with_websocket
async def ws_status(request, ws):
    last = {}
    while True:
        current = dict(reg_state)
        if current != last:
            await ws.send(json.dumps(current))
            last = dict(current)
            if current["state"] in ("success", "error"):
                await asyncio.sleep(1)
                break
        await asyncio.sleep(0.4)


@app.route("/status.json", methods=["GET"])
async def status_endpoint(request):
    return _json_response(reg_state)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import ssl as _ssl

    _SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    _CERT = os.path.join(_SCRIPT_DIR, "cert.pem")
    _KEY  = os.path.join(_SCRIPT_DIR, "key.pem")

    async def _main():
        asyncio.create_task(_idle_watchdog())
        tasks = [app.start_server(host="0.0.0.0", port=SERVER_PORT, debug=False)]
        if os.path.exists(_CERT) and os.path.exists(_KEY):
            _ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
            _ctx.load_cert_chain(_CERT, _KEY)
            tasks.append(app.start_server(host="0.0.0.0", port=443, debug=False, ssl=_ctx))
            print("[schoolair-wizard] HTTPS on port 443")
        else:
            print("[schoolair-wizard] cert.pem/key.pem not found — HTTP only")
        print(f"[schoolair-wizard] HTTP on port {SERVER_PORT}")
        await asyncio.gather(*tasks)

    asyncio.run(_main())

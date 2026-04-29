/**
 * Threshold syncing and checking for air quality metrics.
 * Syncs from central server before each alert check.
 */

import fs from "fs";

const CONFIG_FILE = process.env.THRESHOLD_CONFIG || "./thresholds.json";

// this is the structure of thresholds we get from central server and store locally
interface Threshold {
  metric:    string;
  threshold: number;
  condition: "above" | "below";
  severity:  "info" | "warning" | "critical";
}

 // this is structure to match what central server expects in alert endpoint
export interface ThresholdBreach {
  metric:    string;
  value:     number;
  threshold: number;
  severity:  string;
}

// Read in the local copy of thresholds
function loadLocal(): { hash: string | null; thresholds: Threshold[] } {
  try {
    return JSON.parse(fs.readFileSync(CONFIG_FILE, "utf-8"));
  } catch {
    return { hash: null, thresholds: [] };
  }
}

// Sync with central server and update if different. This is called on startup 
// AND before each alert check.
export async function syncThresholds(): Promise<void> {
  try {
    const remote = await fetch(`${process.env.SERVER_URL}/aqc/v1/config`, {
      headers: { Authorization: `Bearer ${process.env.AUTH_TOKEN}` }
    }).then(r => r.json()) as { hash: string; thresholds: Threshold[] };

    const local = loadLocal();

    if (remote.hash !== local.hash) {
      fs.writeFileSync(CONFIG_FILE, JSON.stringify({ hash: remote.hash, thresholds: remote.thresholds }, null, 2));
      console.log("Thresholds are out of date, updated local config!");
    }
  } catch (err) {
    console.error("Config sync failed, using existing thresholds:", err);
  }
}

/**
 * Here is the logic for actually checking if any thresholds are breached. 
 * This is called by the alert job each time it runs, after syncing thresholds.
 */

// we keep track of last alerted so we can have a simple cooldown
// mechanism to avoid alerting too frequently. We will need to change
// this if we want to support multiple thresholds with different 
// cooldowns, but for now this is a simple approach that works.
let lastAlerted: number = 0;

export function checkThresholds(data: Record<string, unknown>): ThresholdBreach[] {
  const cooldown = Number(process.env.ALERT_COOLDOWN) || 300000;

  // if within cooldown, skip checking
  if (Date.now() - lastAlerted < cooldown) return [];

  const { thresholds } = loadLocal();
  const breaches: ThresholdBreach[] = [];

  // NOTE: we just grab data from the sen6x sensor for now
  // we will need to change our approach to support the data from the
  // other sensors but it is a start
  const sen6x = data.sen6x as Record<string, unknown> || {};

  // for each threshold condition (i.e. PM2.5 above 35)
  for (const t of thresholds) {
    // we check if metric is in current data from sensor
    const value = sen6x[t.metric];

    // we may get weird data from sensor or new metric
    // so we just skip if it's not a number
    if (typeof value !== "number") {
      console.log(`Invalid threshold check for ${t.metric}: value=${value} (type: ${typeof value})`);
      continue;
    }

    // here we actually check the threshold condition
    const breached = t.condition === "above" ? value > t.threshold : value < t.threshold;
    
    if (breached) {
      breaches.push({ 
        metric: t.metric, 
        value, 
        threshold: t.threshold, 
        severity: t.severity
      });
    }
  }

  if (breaches.length > 0) {
    lastAlerted = Date.now();
    console.log(`Warning: ${breaches.length} threshold breaches detected!`);
    console.log(`Cooling down now for ${cooldown / 1000} seconds...`);
  }
  
  return breaches;
}
import { alertAction } from "../services/alertAction";
import { readSensor } from "../services/sensor";
import { checkThresholds, syncThresholds } from "../services/thresholds";

/**
 * Reads sensor data and checks against thresholds every 60 seconds.
 * If a breach is found and outside the cooldown window, posts to central server.
 * Data is not stored or queued — purely for real time alerting.
 */

const interval = Number(process.env.ALERT_INTERVAL) || 60000;

async function run(): Promise<void> {
  try {
    const data = await readSensor();
    
    await syncThresholds();
    const breaches = checkThresholds(data);
    // TODO: checks just once. We should check a few times to avoid false positives,
    // maybe average over a minute or so before firing an alert
    
    // TODO: This is for organisation alerts and dashboard. We need an additional
    // alert here that is configured in .env of device to notify user directly

    // TODO: find way to change LED on device itself

    if (breaches.length > 0) await alertAction(breaches);

  } catch (err) {
    console.error("Alert check failed:", err);
  } finally {
    setTimeout(run, interval); // fixed 60 second interval
  }
}

export function startAlertJob(): void {
  setTimeout(run, interval);
  console.log(`Alert job started — checking thresholds every ${interval / 1000} seconds`);
}
import { readSensor } from "../services/sensor";
import { checkThresholds } from "../services/threshold";

/**
 * Reads sensor data and checks against thresholds every 60 seconds.
 * If a breach is found and outside the cooldown window, posts to central server.
 * Data is not stored or queued — purely for real time alerting.
 */

async function run(): Promise<void> {
  try {
    const data = await readSensor();
    const breaches = checkThresholds(data);

    for (const breach of breaches) {
      await fetch(`${process.env.SERVER_URL}/aqc/v1/alert`, {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${process.env.AUTH_TOKEN}`,
          "Content-Type": "application/json"
        },
        body: JSON.stringify(breach)
      });
    }
  } catch (err) {
    console.error("Alert check failed:", err);
  } finally {
    setTimeout(run, 60000); // fixed 60 second interval
  }
}

export function startAlertJob(): void {
  setTimeout(run, 60000);
  console.log("Alert job started — checking thresholds every 60s");
}
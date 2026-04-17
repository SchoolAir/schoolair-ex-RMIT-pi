/**
 * Does a specified action depending on the breached air quality metrics
 */

import { ThresholdBreach } from "./thresholds";

export async function alertAction(breaches: ThresholdBreach[]) {
  await fetch(`${process.env.SERVER_URL}/aqc/v1/alert`, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${process.env.AUTH_TOKEN}`,
      "Content-Type": "application/json"
    },
    body: JSON.stringify(breaches)
  });
}
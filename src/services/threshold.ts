/**
 * Thresholds for air quality metrics.
 * Adjust these values based on relevant air quality standards.
 */

const THRESHOLDS: Record<string, number> = {
  pm2_5: 25,       // µg/m³ WHO guideline
  pm10: 45,        // µg/m³
  co2: 1000,       // ppm
  temperature: 35, // °C
};

// Expected structure in central server alert table
export interface ThresholdBreach {
  metric: string;
  value: number;
  threshold: number;
}

// Timestamp of the last alert fired — global cooldown across all metrics
let lastAlerted: number = 0;

/**
 * Checks sensor data against thresholds.
 * Returns breaches found, or empty array if still within cooldown period.
 */
export function checkThresholds(data: Record<string, unknown>): ThresholdBreach[] {
  const cooldown = Number(process.env.ALERT_COOLDOWN) || 300000;
  const now = Date.now();

  if (now - lastAlerted < cooldown) return [];

  const breaches: ThresholdBreach[] = [];

  for (const [metric, threshold] of Object.entries(THRESHOLDS)) {
    const value = data[metric];
    if (typeof value === "number" && value > threshold) {
      breaches.push({ metric, value, threshold });
    }
  }

  if (breaches.length > 0) lastAlerted = now;

  return breaches;
}
/**
 * Reads sensor data by executing an external script and parsing its JSON output.
 */

// TODO: actually get read script from Oded and adjust logic as needed

import { exec } from "child_process";

// NOTE: To use the mock sensor, run `npm run dev:mock`
const script = process.env.MOCK_SENSOR_SCRIPT ?? "./read-sensor.sh";

export function readSensor(): Promise<Record<string, unknown>> {
  return new Promise((resolve, reject) => {
    exec(script, (error, stdout) => {
      if (error) return reject(error);
      resolve(JSON.parse(stdout));
    });
  });
}
/**
 * Reads sensor data by executing an external script and parsing its JSON output.
 */

// TODO: actually get read script from Oded and adjust logic as needed

import { exec } from "child_process";

export function readSensor(): Promise<Record<string, unknown>> {
  return new Promise((resolve, reject) => {
    exec("./read-sensor.sh", (error, stdout) => {
      if (error) return reject(error);
      resolve(JSON.parse(stdout));
    });
  });
}
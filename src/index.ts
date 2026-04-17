import "dotenv/config";
import express from "express";

import { startIngestJob }   from "./jobs/ingest";
import { startFlushJob }    from "./jobs/flushQueue";
import { startAlertJob }    from "./jobs/alert";
import { syncThresholds } from "./services/thresholds";

const app = express();

// On startup, sync thresholds and start alert job
// syncThresholds()
//   .then(() =>  {
//     console.log("Initial threshold sync complete");
//     startAlertJob();
//   })
//   .catch(err => console.error("Initial threshold sync failed:", err));

startIngestJob(); 

// startFlushJob(); // NOT DONE YET
// TODO: Add a dashboard/local ui

app.get("/health", (_req, res) => res.json({ status: "ok" }));

const PORT = process.env.PORT || 3001;
app.listen(PORT, () => {
  console.log(`SchoolAir Pi running on port ${PORT}`);
});
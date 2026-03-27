import "dotenv/config";
import express from "express";

import { startSnapshotJob } from "./jobs/snapshot";
import { startIngestJob }   from "./jobs/ingest";
import { startFlushJob }    from "./jobs/flushQueue";
import { startAlertJob }    from "./jobs/alert";

const app = express();

app.get("/health", (_req, res) => res.json({ status: "ok" }));

// TODO: in future when we add a dashboard/local ui for customisation,
// we can add endpoints here to get queue status, recent measurements, etc.
// i.e. app.use("/dashboard", dashboardRoutes);

startSnapshotJob();
startIngestJob();
startFlushJob();
startAlertJob();

const PORT = process.env.PORT || 3001;
app.listen(PORT, () => {
  console.log(`SchoolAir Pi running on port ${PORT}`);
});
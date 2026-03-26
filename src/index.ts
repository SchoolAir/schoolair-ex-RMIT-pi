import "dotenv/config";
import express from "express";

import { startSnapshotJob } from "./jobs/snapshot";
import { startIngestJob }   from "./jobs/ingest";
import { startFlushJob }    from "./jobs/flushQueue";

import sensorRoutes   from "./routes/sensor";
import alertRoutes    from "./routes/alert";
import snapshotRoutes from "./routes/snapshot";

const app = express();
app.use(express.json());

app.use("/sensor", sensorRoutes);
app.use("/sensor", alertRoutes);
app.use("/sensor", snapshotRoutes);

app.get("/health", (_req, res) => res.json({ status: "ok" }));

// Start background jobs
startSnapshotJob();
startIngestJob();
startFlushJob();

const PORT = process.env.PORT || 3001;
app.listen(PORT, () => {
  console.log(`SchoolAir Pi running on port ${PORT}`);
});
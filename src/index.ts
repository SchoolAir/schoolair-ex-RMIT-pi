import "dotenv/config";
import express from "express";

import { ensureRegistered } from "./setup";
import { startIngestJob } from "./jobs/ingest";
import { startAlertJob } from "./jobs/alert";

const PORT = process.env.PORT ?? 3001;
const app = express();

app.get("/health", (_req, res) => res.json({ status: "ok" }));

const start = async () => {
    await ensureRegistered();
    startIngestJob();
    //startAlertJob();
    app.listen(PORT, () => console.log(`SchoolAir Pi running on port ${PORT}`));
};

start().catch((err) => {
    console.error("Failed to start:", err);
    process.exit(1);
});

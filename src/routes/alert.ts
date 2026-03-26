import { Router, Request, Response } from "express";

const router = Router();

// POST /sensor/alert — checks latest reading against thresholds, posts to central server if breached
router.post("/alert", (_req: Request, res: Response) => {
  res.status(501).json({ error: "Not implemented" });
});

export default router;

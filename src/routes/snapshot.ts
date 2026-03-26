import { Router, Request, Response } from "express";

const router = Router();

// POST /sensor/snapshot — sends latest reading to central server, no queue
router.post("/snapshot", (_req: Request, res: Response) => {
  res.status(501).json({ error: "Not implemented" });
});

export default router;
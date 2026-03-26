import { Router, Request, Response } from "express";

const router = Router();

// POST /sensor/read — triggered internally to collect and queue a sensor reading
router.post("/read", (_req: Request, res: Response) => {
  res.status(501).json({ error: "Not implemented" });
});

export default router;

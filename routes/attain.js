import express from "express";
import dotenv from "dotenv";
import { csvUpload } from "../middleware/upload.js";
import { scheduleFritoLayJob } from "../utils/attainJobQueue.js";

dotenv.config();
const router = express.Router();

router.post("/fritolay", csvUpload.single('csv'), async (req, res) => {
  try {
    const filePath = req.file.path;
    scheduleFritoLayJob(filePath);
    return res.status(200).send("FritoLay ordering started");
  } catch (error) {
    console.log(error);
    return res.status(400).send("Something went wrong");
  }
});

export default router;
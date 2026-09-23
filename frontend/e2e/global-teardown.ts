import { execSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const META_FILE = path.join(__dirname, ".backend-meta.json");

export default function globalTeardown(): void {
  if (!fs.existsSync(META_FILE)) return;
  const { pid, dbPath } = JSON.parse(fs.readFileSync(META_FILE, "utf-8")) as {
    pid: number;
    dbPath: string;
  };
  try {
    if (process.platform === "win32") {
      execSync(`taskkill /pid ${pid} /T /F`, { stdio: "ignore" });
    } else {
      process.kill(-pid, "SIGTERM"); // negative pid: the whole process group (spawned detached)
    }
  } catch {
    // already exited
  }
  for (const suffix of ["", "-shm", "-wal"]) {
    try {
      fs.unlinkSync(dbPath + suffix);
    } catch {
      // never existed or already gone
    }
  }
  fs.unlinkSync(META_FILE);
}

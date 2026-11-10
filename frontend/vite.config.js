import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

import { defineConfig } from "vite";

// Stamps each build into the service worker (public/sw.js), so a new deploy
// is a new worker: it installs, and drops the old build's page cache.
function stampServiceWorker() {
  let outDir = "dist";
  return {
    name: "floodscout-stamp-service-worker",
    apply: "build",
    configResolved(config) {
      outDir = resolve(config.root, config.build.outDir);
    },
    closeBundle() {
      const file = resolve(outDir, "sw.js");
      const stamp = new Date().toISOString().replace(/[-:.TZ]/g, "");
      writeFileSync(file, readFileSync(file, "utf8").replace("__BUILD__", stamp));
    },
  };
}

// Port 5173 is not incidental: floodscout/settings.py already lists
// http://localhost:5173 and http://127.0.0.1:5173 in CORS_ALLOWED_ORIGINS.
// Changing it here means changing CORS_ALLOWED_ORIGINS too.
export default defineConfig({
  plugins: [stampServiceWorker()],
  server: {
    port: 5173,
    strictPort: true,
  },
});

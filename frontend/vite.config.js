import { defineConfig } from "vite";

// Port 5173 is not incidental: floodscout/settings.py already lists
// http://localhost:5173 and http://127.0.0.1:5173 in CORS_ALLOWED_ORIGINS.
// Changing it here means changing CORS_ALLOWED_ORIGINS too.
export default defineConfig({
  server: {
    port: 5173,
    strictPort: true,
  },
});

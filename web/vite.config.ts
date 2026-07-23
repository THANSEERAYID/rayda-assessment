import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    // Bind IPv4 explicitly — on Windows, Vite often listens on ::1 only, which
    // breaks /api proxying when the API is on 127.0.0.1.
    host: "127.0.0.1",
    port: 5173,
    // The API is same-origin through this proxy, so the browser never needs
    // to know the backend's address and CORS stays out of the picture.
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        // Full fleet scans are multi-call turns and routinely take 1–2 minutes.
        timeout: 300_000,
        proxyTimeout: 300_000,
      },
    },
  },
});

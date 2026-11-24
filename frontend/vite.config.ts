/// <reference types="vitest/config" />
import { fileURLToPath, URL } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The Flask API runs on :5000 in development; /api is proxied so the browser
// talks to a single origin and no CORS round-trips are needed locally.
const apiTarget = process.env.VITE_API_PROXY_TARGET ?? "http://localhost:5000";
// The collab service (Hocuspocus) on :1234; the browser reaches it at /collab.
const collabTarget = process.env.VITE_COLLAB_PROXY_TARGET ?? "ws://localhost:1234";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: apiTarget, changeOrigin: true },
      "/collab": {
        target: collabTarget,
        ws: true,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/collab/, ""),
      },
    },
  },
  test: {
    environment: "jsdom",
  },
});

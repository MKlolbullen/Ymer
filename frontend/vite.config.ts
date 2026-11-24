/// <reference types="vitest/config" />
import { cpSync, createReadStream, existsSync, statSync } from "node:fs";
import { join, normalize, resolve } from "node:path";
import { fileURLToPath, URL } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

// The Flask API runs on :5000 in development; /api is proxied so the browser
// talks to a single origin and no CORS round-trips are needed locally.
const apiTarget = process.env.VITE_API_PROXY_TARGET ?? "http://localhost:5000";
// The collab service (Hocuspocus) on :1234; the browser reaches it at /collab.
const collabTarget = process.env.VITE_COLLAB_PROXY_TARGET ?? "ws://localhost:1234";

/**
 * Excalidraw loads its fonts at runtime from `window.EXCALIDRAW_ASSET_PATH`. Serve them
 * from this app at /excalidraw-assets/ (dev server and build output) so drawings work
 * without reaching a CDN, which matters for self-hosting.
 */
function excalidrawAssets(): Plugin {
  const source = fileURLToPath(new URL("./node_modules/@excalidraw/excalidraw/dist/prod/fonts", import.meta.url));
  const prefix = "/excalidraw-assets/fonts/";
  let outDir = "dist";
  return {
    name: "ymer-excalidraw-assets",
    configResolved(config) {
      outDir = resolve(config.root, config.build.outDir);
    },
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const path = decodeURIComponent((req.url ?? "").split("?")[0] ?? "");
        if (!path.startsWith(prefix)) return next();
        const file = normalize(join(source, path.slice(prefix.length)));
        if (!file.startsWith(source) || !existsSync(file) || !statSync(file).isFile()) return next();
        res.setHeader("Content-Type", file.endsWith(".woff2") ? "font/woff2" : "application/octet-stream");
        res.setHeader("Cache-Control", "public, max-age=31536000, immutable");
        createReadStream(file).pipe(res);
      });
    },
    closeBundle() {
      if (existsSync(source)) cpSync(source, join(outDir, "excalidraw-assets", "fonts"), { recursive: true });
    },
  };
}

export default defineConfig({
  plugins: [react(), tailwindcss(), excalidrawAssets()],
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

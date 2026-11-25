/// <reference types="vitest/config" />
import { cpSync, createReadStream, existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, normalize, resolve } from "node:path";
import { fileURLToPath, URL } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";
import { VitePWA } from "vite-plugin-pwa";

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

const proxy = {
  "/api": { target: apiTarget, changeOrigin: true },
  "/collab": {
    target: collabTarget,
    ws: true,
    changeOrigin: true,
    rewrite: (path: string) => path.replace(/^\/collab/, ""),
  },
};

/**
 * The built JS files a cold offline start needs: the app entry and the drawing canvas,
 * plus everything they import statically. Excalidraw's optional extras (Mermaid import,
 * font subsetting, ...) are left out of the precache; they are cached the first time
 * they load instead, which keeps installing the app around 3 MB instead of 9.
 */
function coreChunks(assetsDir: string): Set<string> {
  const seen = new Set<string>();
  if (!existsSync(assetsDir)) return seen;
  const visit = (file: string) => {
    if (seen.has(file)) return;
    seen.add(file);
    const source = readFileSync(join(assetsDir, file), "utf8");
    for (const match of source.matchAll(/(?:\bfrom|\bimport)\s*"\.\/([\w.-]+\.js)"/g)) visit(match[1]!);
  };
  readdirSync(assetsDir)
    .filter((file) => /^(index|DrawingCanvas)-[\w-]+\.js$/.test(file))
    .forEach(visit);
  return seen;
}

/**
 * Installable app plus offline start-up. The service worker (Workbox, generated at build
 * time) precaches the app shell, answers page navigations with index.html when offline,
 * and keeps the latest response of most API reads (network first, so online use always
 * sees fresh data). Page content itself is kept per page in IndexedDB by the collab
 * session (src/offline/). Signing out deletes the "ymer-api" cache.
 */
const pwa = VitePWA({
  registerType: "prompt",
  injectRegister: false,
  manifest: {
    name: "Ymer",
    short_name: "Ymer",
    description: "Notes, databases, mind maps and drawings, together.",
    start_url: "/",
    scope: "/",
    display: "standalone",
    background_color: "#ffffff",
    theme_color: "#4f46e5",
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png" },
      { src: "/icons/maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  },
  workbox: {
    globPatterns: ["**/*.{js,css,html,svg,png,webmanifest}"],
    maximumFileSizeToCacheInBytes: 8 * 1024 * 1024,
    manifestTransforms: [
      async (entries) => {
        const core = coreChunks(fileURLToPath(new URL("./dist/assets", import.meta.url)));
        const manifest = entries.filter((entry) => {
          const match = /^assets\/(.+\.js)$/.exec(entry.url);
          return !match || core.has(match[1]!);
        });
        return { manifest, warnings: [] };
      },
    ],
    navigateFallback: "/index.html",
    navigateFallbackDenylist: [/^\/api\//, /^\/collab/, /^\/excalidraw-assets\//],
    cleanupOutdatedCaches: true,
    runtimeCaching: [
      {
        // Reads the app needs to show the sidebar and open pages. Sign-in, health, AI
        // and downloads always go to the network.
        urlPattern: ({ url, request, sameOrigin }) =>
          sameOrigin &&
          request.method === "GET" &&
          url.pathname.startsWith("/api/") &&
          !/^\/api\/(auth|health|ai)(\/|$)/.test(url.pathname) &&
          !/\/(download|content)$/.test(url.pathname),
        handler: "NetworkFirst",
        options: {
          cacheName: "ymer-api",
          networkTimeoutSeconds: 10,
          cacheableResponse: { statuses: [200] },
          expiration: { maxEntries: 500, maxAgeSeconds: 30 * 24 * 60 * 60 },
        },
      },
      {
        // Hashed build files outside the precache (see coreChunks); never change.
        urlPattern: ({ url, sameOrigin }) => sameOrigin && url.pathname.startsWith("/assets/"),
        handler: "CacheFirst",
        options: { cacheName: "ymer-assets", expiration: { maxEntries: 300, maxAgeSeconds: 90 * 24 * 60 * 60 } },
      },
      {
        urlPattern: ({ url, sameOrigin }) => sameOrigin && url.pathname.startsWith("/excalidraw-assets/"),
        handler: "CacheFirst",
        options: { cacheName: "ymer-drawing-fonts", expiration: { maxEntries: 100 } },
      },
    ],
  },
});

export default defineConfig({
  plugins: [react(), tailwindcss(), excalidrawAssets(), pwa],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: {
    port: 5173,
    proxy,
  },
  // `npm run build && npm run preview` serves the production build, service worker
  // included, behind the same proxy.
  preview: {
    port: 4173,
    proxy,
  },
  test: {
    environment: "jsdom",
  },
});

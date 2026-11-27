/// <reference types="vite/client" />
/// <reference types="vite-plugin-pwa/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  /** "mobile" in the phone apps' bundled build (src/lib/app.ts). */
  readonly VITE_YMER_APP?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

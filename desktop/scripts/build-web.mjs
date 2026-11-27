// Builds Ymer's web app for the phone apps and copies it to desktop/mobile-web,
// the frontendDist of tauri.android.conf.json and tauri.ios.conf.json.
// VITE_YMER_APP=mobile turns off the service worker (the app's relay does that job)
// and shows "Change server" in the app.
import { execSync } from "node:child_process";
import { cpSync, existsSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const desktop = join(dirname(fileURLToPath(import.meta.url)), "..");
const frontend = join(desktop, "..", "frontend");
const out = join(desktop, "mobile-web");

const run = (cmd) => execSync(cmd, { cwd: frontend, stdio: "inherit", env: { ...process.env, VITE_YMER_APP: "mobile" } });
if (!existsSync(join(frontend, "node_modules"))) run("npm ci");
run("npm run build");
rmSync(out, { recursive: true, force: true });
cpSync(join(frontend, "dist"), out, { recursive: true });
console.log(`Web app copied to ${out}`);

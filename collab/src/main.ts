import { loadConfig } from "./config.ts";
import { createServer } from "./server.ts";

try {
  process.loadEnvFile(new URL("../../.env", import.meta.url));
} catch {
  // No .env file: rely on the real environment.
}

const config = loadConfig();
const server = createServer(config);
await server.listen();
console.log(`ymer-collab listening on :${config.port}, backend ${config.backendUrl}`);

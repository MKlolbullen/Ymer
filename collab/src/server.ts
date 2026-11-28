import { Database } from "@hocuspocus/extension-database";
import { Logger } from "@hocuspocus/extension-logger";
import { Server } from "@hocuspocus/server";

import { AccessDenied, Backend } from "./backend.ts";
import type { Config } from "./config.ts";
import { newCounters, renderMetrics } from "./metrics.ts";

/** What each connection carries after authentication. */
export type Context = {
  user: { id: string; name: string };
  role: string;
};

/**
 * Messages clients may relay to everyone else on the same document, e.g. "comments
 * changed, refetch". Anything else is dropped so the channel cannot be used to push
 * arbitrary data to other users.
 */
export const RELAYED_MESSAGES = new Set(["comments-changed", "versions-changed", "files-changed"]);

export function relayable(payload: string): boolean {
  if (payload.length > 512) return false;
  try {
    const message = JSON.parse(payload) as { type?: unknown };
    return typeof message.type === "string" && RELAYED_MESSAGES.has(message.type);
  } catch {
    return false;
  }
}

export function createServer(config: Config, backend = new Backend(config.backendUrl, config.collabSecret)) {
  const counters = newCounters();
  return new Server<Context>({
    port: config.port,
    name: "ymer-collab",
    quiet: config.quiet,
    debounce: config.debounce,
    maxDebounce: config.maxDebounce,
    websocketOptions: { maxPayload: 10 * 1024 * 1024 },

    extensions: [
      ...(config.quiet ? [] : [new Logger({ onChange: false, onLoadDocument: false })]),
      new Database({
        fetch: async ({ documentName }) => {
          try {
            return await backend.fetchDocument(documentName);
          } catch (error) {
            counters.loadErrors += 1;
            throw error;
          }
        },
        store: async ({ documentName, state, lastContext }) => {
          counters.stores += 1;
          try {
            await backend.storeDocument(documentName, state, lastContext?.user?.id);
          } catch (error) {
            counters.storeErrors += 1;
            throw error;
          }
        },
      }),
    ],

    async onAuthenticate({ token, documentName, connectionConfig }) {
      try {
        const auth = await backend.authorize(token, documentName);
        // Viewers and commenters get the live document but their edits are discarded.
        connectionConfig.readOnly = auth.read_only;
        return { user: { id: auth.user.id, name: auth.user.display_name }, role: auth.role };
      } catch (error) {
        if (error instanceof AccessDenied) {
          counters.authDenied += 1;
          throw new Error("Permission denied");
        }
        counters.authErrors += 1;
        throw error;
      }
    },

    async onStateless({ payload, document, connection }) {
      if (!relayable(payload)) return;
      document.broadcastStateless(payload, (other) => other !== connection);
    },

    async onRequest({ request, response, instance }) {
      if (request.url === "/metrics") {
        const gauges = {
          connections: instance.getConnectionsCount(),
          documents: instance.getDocumentsCount(),
        };
        response.writeHead(200, { "Content-Type": "text/plain; version=0.0.4" });
        response.end(renderMetrics(counters, gauges, config.version));
        throw null;
      }
      if (request.url === "/health") {
        response.writeHead(200, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ status: "ok", service: "ymer-collab" }));
        // Hocuspocus treats a rejected onRequest as "handled, stop here".
        throw null;
      }
    },
  });
}

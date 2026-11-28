/**
 * Runs the real Hocuspocus server against a stub of Flask's internal API and connects
 * real providers to it over WebSockets.
 */
import assert from "node:assert/strict";
import { createServer as createHttpServer, type IncomingMessage, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { after, before, test } from "node:test";

import { HocuspocusProvider } from "@hocuspocus/provider";
import * as Y from "yjs";

import { relayable, createServer } from "../src/server.ts";

const SECRET = "test-secret";
const DOC = "6f1c8a40-0000-4000-8000-000000000001";
const USERS: Record<string, { id: string; display_name: string; read_only: boolean; role: string }> = {
  "alice-token": { id: "a1", display_name: "Alice", read_only: false, role: "editor" },
  "bob-token": { id: "b2", display_name: "Bob", read_only: false, role: "owner" },
  "vic-token": { id: "v3", display_name: "Vic", read_only: true, role: "viewer" },
};

const stored = new Map<string, Uint8Array>();
const storeCalls: { doc: string; userId: string | undefined }[] = [];
let flask: Server;
let collab: ReturnType<typeof createServer>;
let wsUrl: string;

async function body(req: IncomingMessage): Promise<Buffer> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) chunks.push(chunk as Buffer);
  return Buffer.concat(chunks);
}

before(async () => {
  flask = createHttpServer(async (req, res) => {
    if (req.headers["x-ymer-collab-secret"] !== SECRET) return res.writeHead(401).end();
    const url = req.url ?? "";
    if (url === "/internal/collab/authorize") {
      const { token, document } = JSON.parse((await body(req)).toString());
      const user = USERS[token];
      if (!user || document !== DOC) return res.writeHead(404).end();
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(
        JSON.stringify({
          user: { id: user.id, display_name: user.display_name },
          role: user.role,
          read_only: user.read_only,
        }),
      );
    }
    const match = url.match(/^\/internal\/collab\/documents\/(.+)$/);
    if (match) {
      const name = decodeURIComponent(match[1]!);
      if (req.method === "PUT") {
        stored.set(name, new Uint8Array(await body(req)));
        storeCalls.push({ doc: name, userId: req.headers["x-ymer-user-id"] as string | undefined });
        return res.writeHead(204).end();
      }
      const state = stored.get(name);
      if (!state) return res.writeHead(204).end();
      res.writeHead(200, { "Content-Type": "application/octet-stream" });
      return res.end(Buffer.from(state));
    }
    res.writeHead(404).end();
  });
  await new Promise<void>((resolve) => flask.listen(0, resolve));
  const flaskPort = (flask.address() as AddressInfo).port;

  collab = createServer({
    port: 0,
    backendUrl: `http://127.0.0.1:${flaskPort}`,
    collabSecret: SECRET,
    debounce: 50,
    maxDebounce: 200,
    quiet: true,
    version: "test",
  });
  await collab.listen();
  wsUrl = `ws://127.0.0.1:${collab.address.port}`;
});

after(async () => {
  await collab.destroy();
  await new Promise((resolve) => flask.close(resolve));
});

type Client = { provider: HocuspocusProvider; doc: Y.Doc };

function connect(token: string, name = DOC): Promise<Client & { failed?: string }> {
  return new Promise((resolve) => {
    const doc = new Y.Doc();
    const provider: HocuspocusProvider = new HocuspocusProvider({
      url: wsUrl,
      name,
      document: doc,
      token,
      onSynced: () => resolve({ provider, doc }),
      onAuthenticationFailed: ({ reason }) => resolve({ provider, doc, failed: reason }),
    });
  });
}

async function until(check: () => boolean, ms = 3000): Promise<void> {
  const start = Date.now();
  while (!check()) {
    if (Date.now() - start > ms) throw new Error("timed out");
    await new Promise((r) => setTimeout(r, 20));
  }
}

test("edits sync between editors and are saved with the last editor's id", async () => {
  const alice = await connect("alice-token");
  const bob = await connect("bob-token");
  alice.doc.getText("t").insert(0, "hello");
  await until(() => bob.doc.getText("t").toString() === "hello");
  await until(() => storeCalls.some((c) => c.userId === "a1"));

  // A fresh client loads the stored state from "Flask".
  alice.provider.destroy();
  bob.provider.destroy();
  await new Promise((r) => setTimeout(r, 300)); // let the server unload the document
  const carol = await connect("bob-token");
  assert.equal(carol.doc.getText("t").toString(), "hello");
  carol.provider.destroy();
});

test("read-only connections receive edits but cannot make them", async () => {
  const editor = await connect("alice-token");
  const viewer = await connect("vic-token");
  editor.doc.getText("ro").insert(0, "from editor");
  await until(() => viewer.doc.getText("ro").toString() === "from editor");

  viewer.doc.getText("ro").insert(0, "sneaky ");
  await new Promise((r) => setTimeout(r, 300));
  assert.equal(editor.doc.getText("ro").toString(), "from editor");
  editor.provider.destroy();
  viewer.provider.destroy();
});

test("unknown tokens and inaccessible documents are refused", async () => {
  const stranger = await connect("nobody");
  assert.ok(stranger.failed);
  stranger.provider.destroy();
  const elsewhere = await connect("alice-token", "another-doc");
  assert.ok(elsewhere.failed);
  elsewhere.provider.destroy();
});

test("only known notification messages are relayed, to the other clients", async () => {
  const alice = await connect("alice-token");
  const bob = await connect("bob-token");
  const got: string[] = [];
  bob.provider.on("stateless", ({ payload }: { payload: string }) => got.push(payload));
  const echoed: string[] = [];
  alice.provider.on("stateless", ({ payload }: { payload: string }) => echoed.push(payload));

  alice.provider.sendStateless(JSON.stringify({ type: "evil", html: "<script>" }));
  alice.provider.sendStateless(JSON.stringify({ type: "comments-changed" }));
  await until(() => got.length > 0);
  await new Promise((r) => setTimeout(r, 100));
  assert.deepEqual(got, [JSON.stringify({ type: "comments-changed" })]);
  assert.deepEqual(echoed, []);
  alice.provider.destroy();
  bob.provider.destroy();
});

test("relayable() filter", () => {
  assert.equal(relayable('{"type":"versions-changed"}'), true);
  assert.equal(relayable("not json"), false);
  assert.equal(relayable('{"type":"x"}'), false);
  assert.equal(relayable(JSON.stringify({ type: "comments-changed", pad: "x".repeat(600) })), false);
});

test("/metrics reports connections, saves and refusals in the Prometheus format", async () => {
  const alice = await connect("alice-token");
  const refused = await connect("nobody");
  assert.ok(refused.failed);
  refused.provider.destroy();
  const response = await fetch(`http://127.0.0.1:${collab.address.port}/metrics`);
  assert.equal(response.status, 200);
  const text = await response.text();
  assert.match(text, /^ymer_collab_info\{version="test"\} 1$/m);
  assert.match(text, /^ymer_collab_connections [1-9]\d*$/m);
  assert.match(text, /^ymer_collab_auth_denied_total [1-9]\d*$/m);
  assert.match(text, /^ymer_collab_stores_total [1-9]\d*$/m);
  alice.provider.destroy();
});

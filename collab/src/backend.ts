/**
 * Client for Flask's internal collab API (backend/ymer/internal.py). Flask stays the
 * single source of truth for who may open a document and owns the database schema;
 * this service only relays Yjs state to and from it.
 */

export type Authorization = {
  user: { id: string; display_name: string };
  role: "owner" | "editor" | "commenter" | "viewer";
  read_only: boolean;
};

/** Raised when Flask refuses a connection (bad token, no access, not a document). */
export class AccessDenied extends Error {
  readonly status: number;
  constructor(status: number) {
    super(status === 401 ? "unauthorized" : "forbidden");
    this.status = status;
  }
}

export class Backend {
  private readonly baseUrl: string;
  private readonly secret: string;

  constructor(baseUrl: string, secret: string) {
    this.baseUrl = baseUrl;
    this.secret = secret;
  }

  private url(path: string): string {
    return `${this.baseUrl}/internal/collab${path}`;
  }

  private headers(extra: Record<string, string> = {}): Record<string, string> {
    return { "X-Ymer-Collab-Secret": this.secret, ...extra };
  }

  async authorize(token: string, documentName: string): Promise<Authorization> {
    const response = await fetch(this.url("/authorize"), {
      method: "POST",
      headers: this.headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ token, document: documentName }),
    });
    if (response.status === 401 || response.status === 403 || response.status === 404) {
      throw new AccessDenied(response.status);
    }
    if (!response.ok) throw new Error(`authorize failed: ${response.status}`);
    return (await response.json()) as Authorization;
  }

  /** The stored Yjs state, or null for a document nobody has written to yet. */
  async fetchDocument(documentName: string): Promise<Uint8Array | null> {
    const response = await fetch(this.url(`/documents/${encodeURIComponent(documentName)}`), {
      headers: this.headers(),
    });
    if (response.status === 204) return null;
    // Anything else must fail loudly: treating an outage as "empty" would let the next
    // save overwrite real content with a blank document.
    if (!response.ok) throw new Error(`fetch ${documentName} failed: ${response.status}`);
    return new Uint8Array(await response.arrayBuffer());
  }

  async storeDocument(documentName: string, state: Uint8Array, userId?: string): Promise<void> {
    const response = await fetch(this.url(`/documents/${encodeURIComponent(documentName)}`), {
      method: "PUT",
      headers: this.headers({
        "Content-Type": "application/octet-stream",
        ...(userId ? { "X-Ymer-User-Id": userId } : {}),
      }),
      body: state as Uint8Array<ArrayBuffer>,
    });
    if (!response.ok) throw new Error(`store ${documentName} failed: ${response.status}`);
  }
}

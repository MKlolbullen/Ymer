/**
 * Prometheus metrics for the collab service, served at GET /metrics in the text format.
 * Only the Docker network reaches this service directly (nginx forwards /collab to
 * WebSocket upgrades), so the endpoint is not exposed to browsers.
 */

export type Counters = {
  authDenied: number;
  authErrors: number;
  loadErrors: number;
  stores: number;
  storeErrors: number;
};

export function newCounters(): Counters {
  return { authDenied: 0, authErrors: 0, loadErrors: 0, stores: 0, storeErrors: 0 };
}

type Gauges = { connections: number; documents: number };

export function renderMetrics(counters: Counters, gauges: Gauges, version: string): string {
  const lines: string[] = [];
  const metric = (name: string, type: string, help: string, value: number, labels = "") => {
    lines.push(`# HELP ${name} ${help}`, `# TYPE ${name} ${type}`, `${name}${labels} ${value}`);
  };
  metric("ymer_collab_info", "gauge", "Collab service build.", 1, `{version="${version}"}`);
  metric("ymer_collab_connections", "gauge", "Open WebSocket connections.", gauges.connections);
  metric("ymer_collab_documents", "gauge", "Documents loaded in memory.", gauges.documents);
  metric("ymer_collab_auth_denied_total", "counter", "Connections refused by Flask.", counters.authDenied);
  metric("ymer_collab_auth_errors_total", "counter", "Authorizations that failed (backend down).", counters.authErrors);
  metric("ymer_collab_load_errors_total", "counter", "Documents that could not be loaded.", counters.loadErrors);
  metric("ymer_collab_stores_total", "counter", "Document saves sent to Flask.", counters.stores);
  metric("ymer_collab_store_errors_total", "counter", "Document saves that failed.", counters.storeErrors);
  metric("process_resident_memory_bytes", "gauge", "Resident memory.", process.memoryUsage().rss);
  metric("process_uptime_seconds", "gauge", "Seconds since the process started.", Math.round(process.uptime()));
  return `${lines.join("\n")}\n`;
}

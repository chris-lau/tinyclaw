// Gateway resolution, in priority order:
//   1. localStorage["tinyclaw.gateway"] — runtime override (no rebuild needed:
//      useful when the Cloudflare Pages build predates the Render deploy URL)
//   2. VITE_GATEWAY build-time env (the standard Pages/CI path)
//   3. same-origin in production (gateway-served bundle), localhost in dev
const RUNTIME = typeof localStorage !== "undefined" ? localStorage.getItem("tinyclaw.gateway") : null;
const BASE =
  RUNTIME ??
  (import.meta as any).env?.VITE_GATEWAY ??
  ((import.meta as any).DEV ? "http://127.0.0.1:9100" : "");

async function j<T = any>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    headers: { "content-type": "application/json" },
    ...init,
  });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

export const api = {
  health: () => j("/api/health"),
  tasks: () => j("/api/tasks"),
  task: (id: string) => j(`/api/tasks/${id}`),
  approvals: (status?: string) => j(`/api/approvals${status ? `?status=${status}` : ""}`),
  decide: (id: string, body: any) => j(`/api/approvals/${id}/decision`, { method: "POST", body: JSON.stringify(body) }),
  audit: (limit = 200) => j(`/api/audit?limit=${limit}`),
  auditFiltered: (f: { actor?: string; action?: string; decision?: string; q?: string }) => {
    const p = new URLSearchParams({ limit: "300" });
    if (f.actor) p.set("actor", f.actor);
    if (f.action) p.set("action", f.action);
    if (f.decision) p.set("decision", f.decision);
    if (f.q) p.set("q", f.q);
    return j(`/api/audit?${p.toString()}`);
  },
  getPolicySet: (scenario: string) => j(`/api/policy-sets/${scenario}`),
  putPolicySet: (scenario: string, yaml: string) =>
    j(`/api/policy-sets/${scenario}`, { method: "PUT", body: JSON.stringify({ yaml, updated_by: "dashboard" }) }),
  testPolicySet: (scenario: string, yaml: string | null, payload: any, posture = "balanced") =>
    j(`/api/policy-sets/${scenario}/test`, { method: "POST", body: JSON.stringify({ yaml: yaml ?? undefined, payload, posture }) }),
  auditVerify: () => j("/api/audit/verify"),
  events: (limit = 120) => j(`/api/events?limit=${limit}`),
  kpis: () => j("/api/kpis"),
  posture: () => j("/api/posture"),
  setPosture: (posture: string) => j("/api/posture", { method: "POST", body: JSON.stringify({ posture }) }),
  agents: () => j("/api/agents"),
  policies: () => j("/api/policies"),
  scenarios: () => j("/api/scenarios"),
  playground: (scenario: string, requests: any[]) =>
    j("/api/playground/submit", { method: "POST", body: JSON.stringify({ scenario, requests }) }),
  studioTools: () => j("/api/studio/tools"),
  studioAgents: () => j("/api/studio/agents"),
  studioCreate: (body: any) => j("/api/studio/agents", { method: "POST", body: JSON.stringify(body) }),
  studioDeploy: (name: string) => j(`/api/studio/agents/${name}/deploy`, { method: "POST" }),
  studioDelete: (name: string) => j(`/api/studio/agents/${name}`, { method: "DELETE" }),
  studioTest: (name: string, message: string) =>
    j(`/api/studio/agents/${name}/test`, { method: "POST", body: JSON.stringify({ message }) }),
  studioDryRun: (name: string, payload: any) =>
    j(`/api/studio/agents/${name}/dry-run`, { method: "POST", body: JSON.stringify({ payload }) }),
};

export function sse(onEvent: (ev: any) => void): () => void {
  const es = new EventSource(`${BASE}/api/events/stream`);
  es.onmessage = (m) => {
    try {
      onEvent(JSON.parse(m.data));
    } catch {
      /* ignore malformed */
    }
  };
  return () => es.close();
}

export const AGENT_COLORS: Record<string, string> = {
  orchestrator: "#a78bfa",
  intake: "#38bdf8",
  research: "#34d399",
  policy: "#fbbf24",
  executor: "#fb7185",
  gateway: "#94a3b8",
};

export const money = (n: number | null | undefined) =>
  n == null ? "—" : `$${Number(n).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;

export const ago = (ts: number) => {
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
};

export const fmtClock = (ts: number) =>
  new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

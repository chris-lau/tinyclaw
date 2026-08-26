import { useEffect, useState } from "react";
import { api, fmtClock } from "../api";

const DRAFT_PAYLOAD: Record<string, any> = {
  procurement: { amount: 12400, vendor: { sanctioned: false }, injection_flags: 0 },
  support: { refund_amount: 180, abuse_flag: false, legal_flag: false, churn_risk: false },
};

export default function Governance({ tick, scenario }: { tick: number; scenario: string; live: any[] }) {
  const [audit, setAudit] = useState<any[]>([]);
  const [allAudit, setAllAudit] = useState<any[]>([]);
  const [verify, setVerify] = useState<any>(null);
  const [policies, setPolicies] = useState<any[]>([]);
  const [filter, setFilter] = useState({ q: "", actor: "", action: "", decision: "" });
  const [editor, setEditor] = useState<{ scenario: string; kind: string; yaml: string; version: number | null } | null>(null);
  const [testResult, setTestResult] = useState<any>(null);
  const [editorMsg, setEditorMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [prompt, setPrompt] = useState<{ agent: string; text: string } | null>(null);

  useEffect(() => {
    api.audit(300).then((a) => { setAudit(a); setAllAudit(a); }).catch(() => {});
    api.auditVerify().then(setVerify).catch(() => {});
    api.policies().then(setPolicies).catch(() => {});
    setTestResult(null);
    setEditorMsg(null);
  }, [tick]);

  useEffect(() => {
    api.auditFiltered(filter).then(setAudit).catch(() => {});
  }, [filter]);

  const KINDS = ["policy", "risk", "hooks", "identity"];

  async function openEditor(scen: string, kind: string) {
    setTestResult(null);
    setEditorMsg(null);
    try {
      const s = await api.getPolicySet(scen, kind);
      setEditor({ scenario: scen, kind, yaml: s.yaml, version: s.version });
    } catch (e: any) {
      setEditorMsg(`cannot load set: ${e.message}`);
    }
  }

  async function savePolicy() {
    if (!editor) return;
    setBusy(true);
    try {
      const r = await api.putPolicySet(editor.scenario, editor.yaml, editor.kind);
      const what = r.rules != null ? `${r.rules} rules` : r.actions != null ? `${r.actions} actions` : r.hooks != null ? `${r.hooks} hooks` : `${r.agents} agents`;
      setEditorMsg(`saved · v${r.version} · ${what} · effective immediately · audited`);
      const list = await api.policies();
      setPolicies(list);
    } catch (e: any) {
      setEditorMsg(`error: ${e.message}`);
    } finally {
      setBusy(false);
    }
  }

  async function openPrompt(agent: string) {
    setEditorMsg(null);
    try {
      const p = await api.getAgentPrompt(agent);
      setPrompt({ agent, text: p.system_prompt ?? "" });
    } catch (e: any) {
      setEditorMsg(`error: ${e.message}`);
    }
  }

  async function savePrompt() {
    if (!prompt) return;
    setBusy(true);
    try {
      const r = await api.putAgentPrompt(prompt.agent, prompt.text);
      setEditorMsg(`prompt for ${prompt.agent} saved · v${r.version} · applies to the next request`);
    } catch (e: any) {
      setEditorMsg(`error: ${e.message}`);
    } finally {
      setBusy(false);
    }
  }

  async function testPolicy() {
    if (!editor) return;
    try {
      const r = await api.testPolicySet(editor.scenario, editor.yaml, DRAFT_PAYLOAD[editor.scenario] ?? {});
      setTestResult(r);
      setEditorMsg(null);
    } catch (e: any) {
      setTestResult(null);
      setEditorMsg(`error: ${e.message}`);
    }
  }

  const inScenario = (p: any) => scenario === "all" || p.scenario === scenario;
  const ruleSets = policies.filter((p) => p.kind === "policy" && inScenario(p));
  const scenariosAll = [...new Set(policies.map((p: any) => p.scenario))];
  const distinct = (key: string) => [...new Set(allAudit.map((a) => a[key]).filter(Boolean))] as string[];

  return (
    <div className="gv-grid">
      <div className="gv-col">
        <div className="card gv-card" style={{ flex: 1 }}>
          <h3 className="sec" style={{ marginBottom: 8 }}>
            Policy rules (as code)
            {policies.length > 0 && (
              <button className="btn btn-gh" style={{ float: "right", fontSize: 11, padding: "4px 10px" }}
                      onClick={() => (editor ? setEditor(null) : openEditor(scenariosAll[0] ?? "procurement", "policy"))}>
                {editor ? "Close editor" : "✎ Edit sets"}
              </button>
            )}
          </h3>

          {editor && (
            <div className="pk-card" style={{ marginBottom: 10 }}>
              <div className="drow" style={{ paddingBottom: 6, gap: 6 }}>
                <select className="inp" style={{ width: "auto" }} value={editor.scenario}
                        onChange={(e) => openEditor(e.target.value, editor.kind)}>
                  {scenariosAll.map((s: string) => <option key={s}>{s}</option>)}
                </select>
                <select className="inp" style={{ width: "auto" }} value={editor.kind}
                        onChange={(e) => openEditor(editor.scenario, e.target.value)}>
                  {KINDS.map((k) => <option key={k}>{k}</option>)}
                </select>
                <span style={{ color: "#8b98a5", fontSize: 11 }}>
                  v{editor.version} · validated, versioned, audited, hot-applied
                  {editor.kind === "identity" && " (advisory — scope enforcement is roadmap)"}
                </span>
              </div>
              <textarea className="inp ta" style={{ minHeight: 220, width: "100%" }} spellCheck={false}
                        value={editor.yaml}
                        onChange={(e) => setEditor({ ...editor, yaml: e.target.value })} />
              <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap", alignItems: "center" }}>
                {editor.kind === "policy" && <button className="btn btn-gh" onClick={testPolicy}>▶ Test draft</button>}
                <button className="btn btn-g" disabled={busy} onClick={savePolicy}>Save (audited)</button>
              </div>
              {testResult && (
                <div className="drow" style={{ marginTop: 6 }}>
                  <span className="chip c-blue">dry-run</span>
                  <span style={{ color: "#c3cdd9", fontSize: 11.5 }}>
                    {testResult.summary} → <b>{testResult.effect}</b>{testResult.tier ? ` · tier ${testResult.tier}` : ""}
                  </span>
                </div>
              )}
              {editorMsg && <div style={{ fontSize: 11.5, color: editorMsg.startsWith("error") || editorMsg.startsWith("cannot") ? "#f87171" : "#34d399", marginTop: 6 }}>{editorMsg}</div>}
            </div>
          )}

          {ruleSets.flatMap((p) =>
            [{ header: p.scenario }].concat((p.yaml?.policies ?? []) as any[]).map((r: any, i: number) =>
              r.header ? (
                <div key={`${p.scenario}-h`} className="drow" style={{ paddingBottom: 2 }}>
                  <span className="chip c-blue" style={{ fontSize: 9.5 }}>{r.header}</span>
                  {p.version != null && <span className="chip c-gray" style={{ fontSize: 9.5 }}>v{p.version}</span>}
                </div>
              ) : (
                <div className="drow" key={`${p.scenario}-${r.id}-${i}`}>
                  <span className="rid">{r.id}</span>
                  <span style={{ color: "#8b98a5", flex: 1, minWidth: 160 }}>{r.description}</span>
                  <span className={`chip ${r.effect === "deny" ? "c-red" : r.effect === "require_approval" ? "c-amber" : "c-green"}`}>{r.effect}</span>
                </div>
              ),
            ),
          )}
          <h3 className="sec" style={{ margin: "14px 0 8px" }}>Risk registry</h3>
          {policies.filter((p) => p.kind === "risk" && inScenario(p)).map((p) =>
            Object.entries(p.yaml?.actions ?? {}).map(([name, spec]: any) => (
              <div className="drow" key={`${p.scenario}-${name}`}>
                {scenario === "all" && <span className="chip c-blue" style={{ fontSize: 9.5, flexShrink: 0 }}>{p.scenario}</span>}
                <span className="rid">{name}</span>
                <span style={{ color: "#8b98a5", flex: 1 }}>{spec.description}</span>
                <span className={`chip ${spec.risk_class === "blocked" ? "c-red" : spec.risk_class === "auto" ? "c-green" : "c-amber"}`}>{spec.risk_class}</span>
              </div>
            )),
          )}
          <h3 className="sec" style={{ margin: "14px 0 8px" }}>Agent identities &amp; scopes</h3>
          {policies.filter((p) => p.kind === "identity" && inScenario(p)).map((p) =>
            Object.entries(p.yaml?.agents ?? {}).map(([name, spec]: any) => (
              <div className="drow" key={`${p.scenario}-${name}`}>
                <span className="rid">{name}</span>
                <span style={{ flex: 1, display: "flex", gap: 5, flexWrap: "wrap" }}>
                  {(spec.scopes ?? []).map((s: string) => <span key={s} className="chip c-blue">{s}</span>)}
                </span>
                <span className="chip c-gray">tier {spec.tier}</span>
              </div>
            )),
          )}
          <h3 className="sec" style={{ margin: "14px 0 8px" }}>Coded-agent prompts (hot)</h3>
          <div className="drow" style={{ gap: 8 }}>
            <span style={{ color: "#8b98a5", flex: 1 }}>
              the LLM-driven coded agents' system prompts are editable at runtime; orchestration logic stays code by design
            </span>
            {["intake", "support-intake"].map((a) => (
              <button key={a} className="btn btn-gh" style={{ fontSize: 11, padding: "4px 10px" }}
                      onClick={() => (prompt && prompt.agent === a ? setPrompt(null) : openPrompt(a))}>
                {prompt && prompt.agent === a ? "close" : `✎ ${a}`}
              </button>
            ))}
          </div>
          {prompt && (
            <div className="pk-card" style={{ marginTop: 8 }}>
              <div className="drow" style={{ paddingBottom: 6 }}>
                <span className="chip c-blue">{prompt.agent}</span>
                <span style={{ color: "#8b98a5", fontSize: 11 }}>empty = the code default</span>
              </div>
              <textarea className="inp ta" style={{ minHeight: 130, width: "100%" }} spellCheck={false}
                        value={prompt.text} onChange={(e) => setPrompt({ ...prompt, text: e.target.value })} />
              <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                <button className="btn btn-g" disabled={busy} onClick={savePrompt}>Save (audited)</button>
              </div>
            </div>
          )}
          {editorMsg && !editor && <div style={{ fontSize: 11.5, color: editorMsg.startsWith("error") ? "#f87171" : "#34d399", marginTop: 6 }}>{editorMsg}</div>}
          <div style={{ marginTop: 12, fontSize: 10.5, color: "#5d6b7a", lineHeight: 1.5 }}>
            Controls map to NIST AI RMF (GOVERN/MAP/MEASURE/MANAGE) and EU AI Act Art. 12 (logging) &amp; Art. 14 (human oversight) — see docs/governance.md.
          </div>
        </div>
      </div>

      <div className="gv-col">
        <div className="card gv-card" style={{ flex: 1 }}>
          <h3 className="sec" style={{ marginBottom: 8 }}>
            Audit log — hash-chained, tamper-evident{" "}
            {verify && (
              <span className={`chip ${verify.ok ? "c-green" : "c-red"}`} style={{ marginLeft: 8 }}>
                {verify.ok ? `chain verified ✓ ${verify.entries} entries` : `BROKEN at seq ${verify.first_bad_seq}`}
              </span>
            )}
          </h3>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 8 }}>
            <input className="inp" style={{ flex: 2, minWidth: 140 }} placeholder="search actor / action / subject / details…"
                   value={filter.q} onChange={(e) => setFilter({ ...filter, q: e.target.value })} />
            <select className="inp" style={{ flex: 1, minWidth: 110 }} value={filter.actor}
                    onChange={(e) => setFilter({ ...filter, actor: e.target.value })}>
              <option value="">actor: any</option>
              {distinct("actor").map((a) => <option key={a}>{a}</option>)}
            </select>
            <select className="inp" style={{ flex: 1, minWidth: 110 }} value={filter.action}
                    onChange={(e) => setFilter({ ...filter, action: e.target.value })}>
              <option value="">action: any</option>
              {distinct("action").map((a) => <option key={a}>{a}</option>)}
            </select>
            <select className="inp" style={{ flex: 1, minWidth: 100 }} value={filter.decision}
                    onChange={(e) => setFilter({ ...filter, decision: e.target.value })}>
              <option value="">decision: any</option>
              {distinct("decision").map((d) => <option key={d}>{d}</option>)}
            </select>
          </div>
          {audit.map((a) => (
            <div className="drow" key={a.seq} style={{ gridTemplateColumns: "none", display: "block" }}>
              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                <span className="mono" style={{ fontSize: 10, color: "#5d6b7a" }}>#{a.hash?.slice(0, 8)}</span>
                <span className="mono" style={{ fontSize: 10, color: "#3a4a5e" }}>← {a.prev_hash?.slice(0, 8)}</span>
                <span className={`chip ${a.actor?.startsWith("human") ? "c-violet" : a.actor === "guardrail" ? "c-red" : "c-gray"}`}>{a.actor}</span>
                <b style={{ fontSize: 11.5 }}>{a.action}</b>
                <span className={`chip ${a.decision === "deny" || a.decision === "rejected" ? "c-red" : a.decision === "approve" || a.decision === "auto" || a.decision === "approved" ? "c-green" : a.decision === "pending" || a.decision === "require_approval" || a.decision === "human" ? "c-amber" : "c-blue"}`}>{a.decision}</span>
                <span style={{ color: "#5d6b7a", fontSize: 10.5, marginLeft: "auto" }}>{fmtClock(a.ts)}</span>
              </div>
              {Object.keys(a.details ?? {}).length > 0 && (
                <div className="mono" style={{ fontSize: 10, color: "#5d6b7a", marginTop: 2, wordBreak: "break-all" }}>
                  {JSON.stringify(a.details).slice(0, 180)}
                </div>
              )}
            </div>
          ))}
          {audit.length === 0 && <div className="empty">no audit entries yet</div>}
        </div>
      </div>
    </div>
  );
}

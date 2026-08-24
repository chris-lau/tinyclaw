import { useEffect, useState } from "react";
import { api } from "../api";

const BLANK = {
  name: "",
  description: "",
  system_prompt: "You are a helpful corporate agent.",
  model: "gpt-4o",
  tools: [] as string[],
  risk_class: "tier1",
  skills: "",
};

export default function Studio({ tick, onDeployed }: { tick: number; onDeployed: () => void }) {
  const [defs, setDefs] = useState<any[]>([]);
  const [tools, setTools] = useState<any[]>([]);
  const [selName, setSelName] = useState<string | null>(null);
  const [form, setForm] = useState<any>(BLANK);
  const [tab, setTab] = useState<"define" | "test">("define");
  const [chat, setChat] = useState<{ role: "u" | "a"; text: string }[]>([]);
  const [input, setInput] = useState("");
  const [dry, setDry] = useState<any[] | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    api.studioAgents().then((d: any[]) => {
      setDefs(d);
      const sel = d.find((x: any) => x.name === selName);
      if (sel) setForm({ ...BLANK, ...sel.definition, tools: sel.definition.tools ?? [] });
    }).catch(() => {});
    api.studioTools().then(setTools).catch(() => {});
  }, [tick]);

  async function save() {
    try {
      const r = await api.studioCreate({ ...form, skills: form.skills.split(",").map((s: string) => s.trim()).filter(Boolean) });
      setMsg(`saved ${r.name} as draft v${r.version}`);
      setSelName(r.name);
      onDeployed();
    } catch (e: any) {
      setMsg(`error: ${e.message}`);
    }
  }

  async function deploy() {
    if (!selName) return;
    try {
      const r = await api.studioDeploy(selName);
      setMsg(r.route === "human"
        ? `governed deploy: approval ${r.approval_id} created — decide it in Approvals`
        : `deployed live at ${r.url}`);
      onDeployed();
    } catch (e: any) {
      setMsg(`error: ${e.message}`);
    }
  }

  async function send() {
    if (!input.trim() || !selName) return;
    const text = input;
    setInput("");
    setChat((c) => [...c, { role: "u", text }]);
    try {
      const r = await api.studioTest(selName, text);
      setChat((c) => [...c, { role: "a", text: r.reply }]);
    } catch (e: any) {
      setChat((c) => [...c, { role: "a", text: `error: ${e.message}` }]);
    }
  }

  async function dryRun() {
    if (!selName) return;
    const r = await api.studioDryRun(selName, { amount: 12400, vendor: { sanctioned: false }, injection_flags: 0 });
    setDry(r.results);
  }

  const selectedDef = defs.find((d) => d.name === selName);
  const highRisk = form.tools.some((t: string) => tools.find((x) => x.name === t)?.high_risk) || ["tier2", "tier3", "always_human"].includes(form.risk_class);

  return (
    <div className="st-wrap" style={{ flex: 1, minHeight: 0 }}>
      <div className="card reg">
        <button className="newb" onClick={() => { setSelName(null); setForm(BLANK); setChat([]); setDry(null); }}>＋ New agent</button>
        <div style={{ padding: "6px 14px 4px" }}><h3 className="sec">Registry</h3></div>
        {defs.map((d) => (
          <div className={`rl ${d.name === selName ? "sel" : ""} ${d.status === "retired" ? "retired" : ""}`} key={d.id}
               onClick={() => { setSelName(d.name); setForm({ ...BLANK, ...d.definition, tools: d.definition.tools ?? [] }); setChat([]); setDry(null); }}>
            <span className="dot" style={{ background: d.status === "live" ? "#34d399" : "#5d6b7a" }} />
            <span className="nm">{d.name}</span>
            <span className={`chip ${d.status === "live" ? "c-green" : d.status === "retired" ? "c-red" : "c-gray"}`}>
              {d.status} v{d.version}
            </span>
            <button
              className="del"
              title="Delete: removed entirely if the agent has no recorded activity; retired (history preserved) if it has"
              onClick={async (e) => {
                e.stopPropagation();
                if (!window.confirm(`Delete “${d.name}”?\n\nNo recorded activity → hard delete.\nAny activity → retired, history preserved.`)) return;
                try {
                  const r = await api.studioDelete(d.name);
                  setMsg(r.deleted === "hard" ? `“${d.name}” hard-deleted (no activity)` : `“${d.name}” retired — ${r.evidence}`);
                  if (selName === d.name) { setSelName(null); setForm(BLANK); }
                  const list = await api.studioAgents();
                  setDefs(list);
                  onDeployed();
                } catch (err: any) {
                  setMsg(`error: ${err.message}`);
                }
              }}
            >✕</button>
          </div>
        ))}
        {defs.length === 0 && <div className="empty" style={{ padding: 16 }}>no drafts yet</div>}
      </div>

      <div className="card st-main">
        <div className="st-hdr">
          <div>
            <div className="t">{form.name || "new agent"} {selName && defs.find((d) => d.name === selName) && <span className="chip c-gray" style={{ marginLeft: 6 }}>{defs.find((d) => d.name === selName).status} v{defs.find((d) => d.name === selName).version}</span>}</div>
            <div className="s">declarative agent · definition is data, deployment is governed</div>
          </div>
          <div className="sttabs">
            <div className={`sttab ${tab === "define" ? "on" : ""}`} onClick={() => setTab("define")}>Define</div>
            <div className={`sttab ${tab === "test" ? "on" : ""}`} onClick={() => setTab("test")}>Test</div>
          </div>
        </div>

        <div className="stbody">
          <div className="form">
            <div className="fld"><label>NAME</label><input className="inp" value={form.name} placeholder="travel-booker" onChange={(e) => setForm({ ...form, name: e.target.value })} disabled={!!selName} /></div>
            <div className="fld"><label>DESCRIPTION</label><input className="inp" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></div>
            <div className="fld"><label>SYSTEM PROMPT</label><textarea className="inp ta" rows={5} value={form.system_prompt} onChange={(e) => setForm({ ...form, system_prompt: e.target.value })} /></div>
            <div style={{ display: "flex", gap: 9 }}>
              <div className="fld" style={{ flex: 1 }}><label>MODEL</label><input className="inp" value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} /></div>
              <div className="fld" style={{ flex: 1 }}><label>RISK CLASS</label>
                <select className="inp" value={form.risk_class} onChange={(e) => setForm({ ...form, risk_class: e.target.value })}>
                  <option value="tier1">Tier 1 — autonomous</option>
                  <option value="tier2">Tier 2 — supervised</option>
                  <option value="always_human">always human</option>
                </select>
              </div>
            </div>
            <div className="fld"><label>TOOLS</label>
              <div className="tools">
                {tools.map((t) => {
                  const on = form.tools.includes(t.name);
                  return (
                    <div className={`tool ${on ? "on" : ""}`} key={t.name}
                         onClick={() => setForm({ ...form, tools: on ? form.tools.filter((x: string) => x !== t.name) : [...form.tools, t.name] })}>
                      <span className="cb" />{t.name}{t.high_risk && <span style={{ color: "#fbbf24", fontWeight: 700, fontSize: 9.5 }}>HIGH-RISK</span>}
                    </div>
                  );
                })}
              </div>
            </div>
            <div className="fld"><label>SKILLS (AGENT CARD)</label><input className="inp" value={form.skills} placeholder="travel-booking, itinerary" onChange={(e) => setForm({ ...form, skills: e.target.value })} /></div>
          </div>

          <div className="stside">
            <div className="card chat">
              <h3 className="sec" style={{ marginBottom: 9 }}>Test console {form.name ? `· ${form.name}` : ""}</h3>
              {chat.length === 0 && <div className="empty" style={{ padding: 12 }}>save the draft, then send test messages — replies come from the {`agent's`} configured model (mock by default)</div>}
              {chat.map((m, i) => (
                <div className="cm" key={i}>
                  {m.role === "u" ? <div className="u">{m.text}</div> : <div className="a">{m.text}</div>}
                </div>
              ))}
              <div style={{ display: "flex", gap: 8, marginTop: "auto" }}>
                <input className="inp" style={{ flex: 1 }} value={input} placeholder="send a test message…" onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send()} disabled={!selName} />
                <button className="btn btn-gh" onClick={send} disabled={!selName}>Send</button>
              </div>
            </div>

            <div className="card" style={{ padding: "12px 14px" }}>
              <h3 className="sec" style={{ marginBottom: 5 }}>Policy dry-run — sample: $12,400 request</h3>
              {!dry && <button className="btn btn-gh" onClick={dryRun} disabled={!selName}>Run against bound policies</button>}
              {dry?.map((r: any, i: number) => (
                <div className="drow" key={i}>
                  <span className="rid">{r.policy_set.split("/").pop()}</span>
                  <span style={{ color: "#8b98a5", flex: 1 }}>{r.effect}{r.tier ? ` · tier ${r.tier}` : ""}</span>
                  <span className={`chip ${r.effect === "deny" ? "c-red" : r.effect === "require_approval" ? "c-amber" : "c-green"}`}>{r.effect}</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="st-act">
          <div className="st-note">
            {selectedDef?.status === "retired"
              ? "⛔ retired agent — history is preserved and it cannot be redeployed; create a new agent to replace it."
              : highRisk
              ? "⚠ high-risk tool binding or elevated risk class ⇒ deployment itself routes through the human approval queue — the platform governs its own expansion."
              : "tier-1 definition with safe tools deploys directly (still audited)."}
          </div>
          {msg && <span style={{ fontSize: 11.5, color: "#34d399" }}>{msg}</span>}
          <button className="btn btn-gh" style={{ marginLeft: "auto" }} onClick={save} disabled={selectedDef?.status === "retired"}>Save draft</button>
          <button className="btn btn-o" onClick={deploy} disabled={!selName || selectedDef?.status === "retired"}>
            Deploy {highRisk ? "→ requires approval" : ""}
          </button>
        </div>
      </div>
    </div>
  );
}

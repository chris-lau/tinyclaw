import { useEffect, useState } from "react";
import { AGENT_COLORS, ago, api, fmtClock, money } from "../api";

// Langfuse base for trace deep-links: set VITE_LANGFUSE_URL at build time
// (e.g. https://cloud.langfuse.com or your self-hosted URL). Unset = hidden.
const LANGFUSE_URL = (import.meta as any).env?.VITE_LANGFUSE_URL ?? "";

function stateChip(state: string) {
  const cls =
    state === "completed" ? "c-green" :
    state === "input_required" ? "c-amber" :
    ["rejected", "failed", "denied"].includes(state) ? "c-red" : "c-blue";
  const label = state === "input_required" ? "input-required · awaiting human" :
    state === "completed" ? "completed" : state;
  return <span className={`chip ${cls}`}>{label}</span>;
}

export default function Tasks({ tick, live, scenario }: { tick: number; live: any[]; scenario: string }) {
  const [tasks, setTasks] = useState<any[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [detail, setDetail] = useState<any>(null);

  useEffect(() => {
    api.tasks().then(setTasks).catch(() => {});
  }, [tick]);

  useEffect(() => {
    if (!sel) return setDetail(null);
    api.task(sel).then(setDetail).catch(() => setDetail(null));
  }, [sel, tick, live.length]);

  if (sel && detail) return <TaskDetail detail={detail} onBack={() => setSel(null)} />;
  const visible = tasks.filter((t) => scenario === "all" || t.scenario === scenario);

  return (
    <div className="card" style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
      <div className="table-hdr">
        <div>
          <div style={{ fontSize: 15.5, fontWeight: 700 }}>Tasks</div>
          <div className="sub" style={{ color: "#8b98a5", fontSize: 11.5 }}>
            {scenario === "all" ? "every request, its route through the agent mesh" : `requests in scenario: ${scenario}`}
          </div>
        </div>
      </div>
      <div className="rowlist" style={{ flex: 1 }}>
        <div className="trow" style={{ cursor: "default", color: "#5d6b7a", fontSize: 11, textTransform: "uppercase", letterSpacing: ".6px" }}>
          <span>Request</span><span>Amount</span><span>Stage</span><span>State</span><span>Requester</span><span>Updated</span>
        </div>
        {visible.map((t) => (
          <div className="trow" key={t.task_id} onClick={() => setSel(t.task_id)}>
            <div>
              <div className="ttl" style={{ display: "flex", gap: 6, alignItems: "center" }}>
                {t.scenario && <span className="chip c-blue" style={{ fontSize: 8.5, padding: "1px 5px", flexShrink: 0 }}>{t.scenario}</span>}
                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.title}</span>
              </div>
              <div className="sub mono">{t.task_id.slice(0, 13)}… · trace {t.trace_id ? t.trace_id.slice(0, 10) : "—"}</div>
            </div>
            <div style={{ fontWeight: 600 }}>{money(t.amount)}</div>
            <div>{t.stage}</div>
            <div>{stateChip(t.state)}</div>
            <div style={{ color: "#8b98a5", fontSize: 11.5 }}>{t.requester ?? "—"}</div>
            <div style={{ color: "#5d6b7a", fontSize: 11 }}>{ago(t.updated_at)}</div>
          </div>
        ))}
        {visible.length === 0 && <div className="empty">no tasks{scenario !== "all" ? ` in ${scenario}` : " yet"} — submit some from the Playground</div>}
      </div>
    </div>
  );
}

function TaskDetail({ detail, onBack }: { detail: any; onBack: () => void }) {
  const t = detail.task;
  const events: any[] = detail.events ?? [];
  const [approval, setApproval] = useState<any>(null);
  const [approvalLoaded, setApprovalLoaded] = useState(false);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<string | null>(null);

  useEffect(() => {
    setApproval(null);
    setApprovalLoaded(false);
    if (t.state === "input_required") {
      api.approvals("pending")
        .then((list: any[]) => setApproval(list.find((a) => a.task_id === t.task_id) ?? null))
        .catch(() => {})
        .finally(() => setApprovalLoaded(true));
    } else {
      setApprovalLoaded(true);
    }
  }, [t.task_id, t.state, t.updated_at]);

  async function decide(d: "approve" | "reject") {
    if (!approval) return;
    setBusy(true);
    try {
      const r = await api.decide(approval.id, { decision: d, approver: "chris", comment });
      setFlash(d === "approve" ? `Approved — task ${r.task_state ?? "resuming"}` : "Rejected");
      setComment("");
    } catch (e: any) {
      setFlash(`error: ${e.message}`);
    } finally {
      setBusy(false);
    }
  }
  const hopEvents = events.filter((e) => e.type === "a2a.hop");
  const policyEvents = events.filter((e) => e.type === "policy.decision");
  const guardEvents = events.filter((e) => e.type === "guardrail.hit");
  const permitEvents = events.filter((e) => e.type === "permit.rejected");
  const seq = [
    { agent: "gateway", type: "task.received", ts: t.created_at, data: { state: "submitted" } },
    ...events,
  ].sort((a, b) => a.ts - b.ts);

  const bars = [
    { label: "gateway.request", w: 3, color: "#94a3b8", dur: "12ms" },
    { label: "orchestrator.plan", w: 9, color: AGENT_COLORS.orchestrator, dur: "" },
    { label: "intake.extract · llm", w: 28, color: AGENT_COLORS.intake, dur: "" },
    { label: "research.enrich · tools", w: 20, color: AGENT_COLORS.research, dur: "" },
    { label: "policy.evaluate", w: 4, color: AGENT_COLORS.policy, dur: "" },
    { label: "hitl.await_human", w: 36, hatch: true, dur: t.state === "input_required" ? "waiting…" : "" },
  ];
  let offset = 0;
  const barsWithOffset = bars.map((b) => {
    const bar = { ...b, left: offset };
    offset += b.w;
    return bar;
  });

  return (
    <>
      <div className="card thdr" style={{ display: "flex", alignItems: "center", gap: 14, padding: "15px 18px", flexWrap: "wrap" }}>
        <div className="back" style={{ color: "#8b98a5", fontSize: 13, cursor: "pointer" }} onClick={onBack}>← Tasks</div>
        <div>
          <div style={{ fontSize: 15.5, fontWeight: 700 }}>{t.title}</div>
          <div className="sub" style={{ color: "#8b98a5", fontSize: 11.5 }}>
            scenario {t.scenario} · task <span className="mono">{t.task_id.slice(0, 13)}</span> · A2A state machine
          </div>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          {stateChip(t.state)}
          <div style={{ fontSize: 19, fontWeight: 700 }}>{money(t.amount)}</div>
          {t.trace_id && LANGFUSE_URL && (
            <a className="trace-lnk" style={{ fontSize: 12, border: "1px solid #1e3a52", padding: "5px 11px", borderRadius: 7, background: "rgba(56,189,248,.07)", textDecoration: "none" }}
               href={`${LANGFUSE_URL}/trace/${t.trace_id}`} target="_blank" rel="noreferrer">
              Open in Langfuse ↗
            </a>
          )}
        </div>
      </div>

      {t.state === "input_required" && approvalLoaded && (
        <div className="card" style={{ padding: "12px 18px" }}>
          {approval ? (
            <div className="dact" style={{ borderTop: "none", padding: 0 }}>
              <div className="cmt" style={{ flex: 1 }}>
                <input
                  className="cmt" style={{ border: "none", background: "transparent", padding: 0, width: "100%" }}
                  placeholder="Add a comment (stored with your decision)…"
                  value={comment} onChange={(e) => setComment(e.target.value)}
                />
              </div>
              <button className="btn btn-g" disabled={busy} onClick={() => decide("approve")}>✓ Approve &amp; execute</button>
              <button className="btn btn-r" disabled={busy} onClick={() => decide("reject")}>✕ Reject</button>
            </div>
          ) : (
            <div className="dnote" style={{ width: "auto" }}>
              ⚠ approval already decided, but the task could not resume (the orchestrator restarted since it
              parked — see docs/deployment.md). This state is terminal; resubmit the request if needed.
            </div>
          )}
          {flash && <div style={{ fontSize: 12, color: "#34d399", marginTop: 8 }}>{flash}</div>}
        </div>
      )}

      <div className="tdetail" style={{ flex: 1, minHeight: 0 }}>
        <div className="card tl-col">
          <h3 className="sec" style={{ marginBottom: 4 }}>Agent-to-agent timeline</h3>
          {seq.map((e, i) => (
            <div className="msg" key={i}>
              <div className="ava" style={{ background: `${AGENT_COLORS[e.agent] ?? "#5d6b7a"}22`, color: AGENT_COLORS[e.agent] ?? "#8b98a5" }}>
                {(e.agent ?? "?").slice(0, 2).toUpperCase()}
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="mmeta">
                  <span className="mfrom" style={{ color: AGENT_COLORS[e.agent] }}>{e.agent}</span>
                  <span className="arrow">·</span>
                  <span style={{ color: "#7f93aa", fontSize: 11 }}>{e.type}</span>
                  <span className="mtime">{fmtClock(e.ts)}</span>
                </div>
                <div className="mtxt">
                  {e.type === "task.state" && `A2A task state → ${e.data?.state}`}
                  {e.type === "a2a.hop" && `delegating over A2A → ${e.data?.to} (tracecontext propagated)`}
                  {e.type === "a2a.artifact" && `artifact published: ${e.data?.artifact} — ${e.data?.preview ?? ""}`}
                  {e.type === "policy.decision" && `${e.data?.summary} → route ${e.data?.route}${e.data?.posture && e.data.posture !== "balanced" ? ` (posture: ${e.data.posture})` : ""}`}
                  {e.type === "hook.blocked" && `⛔ boundary hook “${e.data?.hook}” refused the outbound message — task rejected at the boundary`}
                  {e.type === "hook.redacted" && `boundary redaction applied before send (${(e.data?.annotations ?? []).map((a: any) => a.hook).join(", ")})`}
                  {e.type === "guardrail.hit" && `guardrail: ${e.data?.redactions ?? 0} redaction(s)${e.data?.injection_patterns?.length ? ` · ${e.data.injection_patterns.length} injection flag(s)` : ""} — before any LLM call`}
                  {e.type === "permit.rejected" && `executor REFUSED action — no valid permit`}
                  {e.type === "task.received" && "request submitted to orchestrator (message/send)"}
                  {["task.state", "a2a.hop", "a2a.artifact", "policy.decision", "guardrail.hit", "permit.rejected", "task.received", "hook.blocked", "hook.redacted"].includes(e.type) ? "" : JSON.stringify(e.data).slice(0, 140)}
                </div>
              </div>
            </div>
          ))}
        </div>

          <div className="rrail">
          <div className="card tracebox">
            <h3 className="sec" style={{ marginBottom: 8 }}>Plan — published before execution</h3>
            {[
              { id: "intake", label: "Extract structured request", done: events.some((e) => e.type === "a2a.artifact" && e.data?.artifact === "request.extracted") },
              { id: "research", label: "Vendor, sanctions & budget", done: events.some((e) => e.type === "a2a.artifact" && e.data?.artifact === "vendor.profile") },
              { id: "policy", label: "Evaluate policy & risk route", done: policyEvents.length > 0 },
              { id: "route", label: "Execution permit", done: policyEvents.length > 0 },
              { id: "execute", label: "Issue PO with signed permit", done: t.state === "completed" },
            ].map((s) => (
              <div className="drow" key={s.id} style={{ gap: 10 }}>
                <span className="dot" style={{ background: s.done ? "#34d399" : "#3a4a5e", opacity: s.done ? 1 : 0.7 }} />
                <span style={{ flex: 1, color: s.done ? "#dbe5f0" : "#8b98a5" }}>{s.label}</span>
                {s.done && <span className="chip c-green">done</span>}
              </div>
            ))}
            <div style={{ fontSize: 10.5, color: "#5d6b7a", marginTop: 6, lineHeight: 1.45 }}>
              intent published as an A2A artifact before any action — pattern borrowed from coding-agent plan mode
            </div>
          </div>
          <div className="card tracebox">
            <h3 className="sec" style={{ marginBottom: 8 }}>Distributed trace — one span chain, 5 agents</h3>
            {barsWithOffset.map((b, i) => (
              <div className="trow-span" key={i}>
                <div className="wl">{b.label}</div>
                <div className="track">
                  <div className={`bar ${b.hatch ? "hatch" : ""}`} style={{ left: `${b.left}%`, width: `${b.w}%`, background: b.hatch ? undefined : b.color }} />
                </div>
                <div className="wd">{b.dur}</div>
              </div>
            ))}
            <div style={{ display: "flex", justifyContent: "space-between", marginTop: 9, paddingTop: 9, borderTop: "1px solid #161f2c", fontSize: 11, color: "#8b98a5" }}>
              <span>hops: {hopEvents.length}</span>
              <span>trace {t.trace_id?.slice(0, 16) ?? "—"}…</span>
            </div>
          </div>

          {policyEvents[0] && (
            <div className="card" style={{ padding: "13px 15px" }}>
              <h3 className="sec" style={{ marginBottom: 6 }}>Policy evaluation</h3>
              <div className="drow">
                <span className="rid">route</span>
                <span>{policyEvents[0].data.effect} → <b>{policyEvents[0].data.route}</b> (tier {policyEvents[0].data.tier})</span>
              </div>
              {(policyEvents[0].data.hits ?? []).map((h: string) => (
                <div className="drow" key={h}>
                  <span className="rid">{h}</span>
                  <span className="chip c-amber">hit</span>
                </div>
              ))}
            </div>
          )}

          {guardEvents[0] && (
            <div className="card" style={{ padding: "13px 15px" }}>
              <h3 className="sec" style={{ marginBottom: 6 }}>Guardrails</h3>
              <div className="drow"><span className="rid">pii.redact</span><span>{guardEvents[0].data.redactions} field(s) masked pre-LLM</span></div>
              {guardEvents[0].data.injection_patterns?.length > 0 && (
                <div className="drow"><span className="rid">injection.scan</span><span className="chip c-red">flagged</span></div>
              )}
            </div>
          )}

          {permitEvents[0] && (
            <div className="card" style={{ padding: "13px 15px" }}>
              <h3 className="sec">Executor</h3>
              <div className="drow"><span className="chip c-red">permit REFUSED</span><span>{permitEvents[0].data.reason}</span></div>
            </div>
          )}
        </div>
      </div>
    </>
  );
}

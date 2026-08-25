import { useEffect, useState } from "react";
import { api, money } from "../api";

export default function Approvals({ tick, scenario, onDecided }: { tick: number; scenario: string; onDecided: () => void }) {
  const [pending, setPending] = useState<any[]>([]);
  const [resolved, setResolved] = useState<any[]>([]);
  const [selId, setSelId] = useState<string | null>(null);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<string | null>(null);
  const [box, setBox] = useState<"pending" | "resolved">("pending");

  useEffect(() => {
    api.approvals("pending").then((p) => {
      setPending(p);
      if (!selId && p.length) setSelId(p[0].id);
    }).catch(() => {});
    api.approvals().then(setResolved).catch(() => {});
  }, [tick]);

  const inScenario = (a: any) => scenario === "all" || a.scenario === scenario;
  const sel = pending.filter(inScenario).find((a) => a.id === selId) ?? pending.filter(inScenario)[0];

  async function decide(decision: "approve" | "reject") {
    if (!sel) return;
    setBusy(true);
    try {
      const r = await api.decide(sel.id, { decision, approver: "chris", comment });
      setFlash(decision === "approve" ? `Approved — task ${r.task_state ?? "resumed"}` : `Rejected — task ${r.task_state ?? "rejected"}`);
      setComment("");
      onDecided();
      setTimeout(() => setFlash(null), 4000);
    } catch (e: any) {
      setFlash(`error: ${e.message}`);
    } finally {
      setBusy(false);
    }
  }

  const packet = sel?.context_packet ?? {};
  const req = packet.request ?? {};
  const research = packet.research ?? {};
  const policy = packet.policy ?? {};
  const isDeploy = sel?.action === "agent.deploy";

  return (
    <div className="ap-wrap" style={{ flex: 1, minHeight: 0 }}>
      <div className="card inbox">
        <div className="itabs">
          <div className={`itab ${box === "pending" ? "on" : ""}`} onClick={() => setBox("pending")}>
            Pending ({pending.filter(inScenario).length})
          </div>
          <div className={`itab ${box === "resolved" ? "on" : ""}`} onClick={() => setBox("resolved")}>
            Resolved ({resolved.filter((a) => a.status !== "pending" && inScenario(a)).length})
          </div>
        </div>
        <div className="ilist">
          {box === "pending" ? (
            <>
              {pending.filter(inScenario).map((a) => (
                <div className={`item ${a.id === sel?.id ? "sel" : ""}`} key={a.id} onClick={() => setSelId(a.id)}>
                  <div className="r1"><span className="id">{a.subject?.slice(0, 30)}</span><span className="amt">{a.amount != null ? money(a.amount) : ""}</span></div>
                  <div className="ttl">{a.scenario} · tier {a.tier}</div>
                  <div className="ft">
                    <span className="chip c-amber">{a.action}</span>
                    <span className="ago">{new Date(a.ts * 1000).toLocaleTimeString()}</span>
                  </div>
                </div>
              ))}
              {pending.filter(inScenario).length === 0 && <div className="empty">inbox clear — nothing waiting on a human</div>}
            </>
          ) : (
            <>
              {resolved.filter((a) => a.status !== "pending" && inScenario(a)).map((a) => (
                <div className="item" key={a.id} title={a.comment || undefined}>
                  <div className="r1"><span className="id">{a.subject?.slice(0, 30)}</span><span className="amt">{a.amount != null ? money(a.amount) : ""}</span></div>
                  <div className="ttl">{a.scenario} · tier {a.tier}</div>
                  <div className="ft">
                    <span className={`chip ${a.status === "approved" ? "c-green" : "c-red"}`}>{a.status}</span>
                    <span className="ago">{a.decided_by ? `by ${a.decided_by}` : ""} · {a.decided_at ? new Date(a.decided_at * 1000).toLocaleTimeString() : ""}</span>
                  </div>
                </div>
              ))}
              {resolved.filter((a) => a.status !== "pending" && inScenario(a)).length === 0 && (
                <div className="empty">nothing decided yet</div>
              )}
            </>
          )}
        </div>
      </div>

      <div className="card det">
        {sel ? (
          <>
            <div className="dhdr">
              <div>
                <div className="t">{sel.subject}</div>
                <div className="s">
                  escalated by <b style={{ color: "#fbbf24" }}>{isDeploy ? "deploy gate" : "policy agent"}</b> ·
                  approver: you (local demo user)
                </div>
              </div>
              <div className="sla">⏱ SLA 15m</div>
            </div>
            <div className="pk">
              {isDeploy ? (
                <div className="pk-card">
                  <div className="pk-h"><span className="dot" style={{ background: "#f97316" }} />Deployment request — Agent Studio</div>
                  <div className="kv"><span className="k">Reason</span><span className="v">{packet.reason ?? "high-risk definition"}</span></div>
                  <div className="kv"><span className="k">System prompt</span><span className="v mono" style={{ fontSize: 11 }}>{packet.definition?.system_prompt?.slice(0, 220)}</span></div>
                  <div className="kv"><span className="k">Tools</span><span className="v">{(packet.definition?.tools ?? []).join(", ")}</span></div>
                  <div className="kv"><span className="k">Risk class</span><span className="v">{packet.definition?.risk_class ?? "—"}</span></div>
                  <div className="kv"><span className="k">Governance</span><span className="v">the platform gates its own expansion — deploying a high-risk agent needs a human decision</span></div>
                </div>
              ) : (
                <>
                  <div className="pk-card">
                    <div className="pk-h"><span className="dot" style={{ background: "#38bdf8" }} />Request — extracted by intake</div>
                    <div className="kv"><span className="k">Amount</span><span className="v"><b>{money(req.amount ?? req.refund_amount)}</b></span></div>
                    <div className="kv"><span className="k">Vendor / Order</span><span className="v">{req.vendor ?? `${req.order_id ?? "—"} · ${req.customer ?? ""}`}</span></div>
                    <div className="kv"><span className="k">Description</span><span className="v">{req.body_summary ?? req.description}</span></div>
                    <div className="kv"><span className="k">Context</span><span className="v">{req.cost_center ?? `churn-risk: ${research.churn_risk ?? "—"}`}</span></div>
                  </div>
                  <div className="pk-card">
                    <div className="pk-h"><span className="dot" style={{ background: "#34d399" }} />Research findings</div>
                    <div className="kv"><span className="k">Record</span><span className="v">{research.vendor?.vendor_id ?? research.order?.order_id} · {research.vendor?.tier ? `tier ${research.vendor.tier}` : research.customer_tier ?? ""}</span></div>
                    <div className="kv"><span className="k">Quality</span><span className="v">{research.vendor?.on_time_pct ? `${research.vendor.on_time_pct}% on-time` : research.order?.found != null ? (research.order.found ? "order verified" : "order NOT FOUND") : "—"}</span></div>
                    <div className="kv"><span className="k">Risk flags</span><span className="v">{research.sanctioned ? <b style={{ color: "#f87171" }}>sanctions HIT</b> : research.churn_risk ? <b style={{ color: "#fbbf24" }}>churn risk</b> : "clear"}</span></div>
                    <div className="kv"><span className="k">Exposure</span><span className="v">{research.budget ? `${money(research.budget.remaining)} budget left` : research.lifetime_value ? `LTV ${money(research.lifetime_value)}` : "—"}</span></div>
                  </div>
                  <div className="pk-card">
                    <div className="pk-h"><span className="dot" style={{ background: "#fbbf24" }} />Policy evaluation</div>
                    <div className="kv"><span className="k">Route</span><span className="v mono" style={{ fontSize: 11 }}>{policy.effect} → {policy.route} (tier {policy.tier})</span></div>
                    <div className="kv"><span className="k">Reason</span><span className="v">{policy.reason}</span></div>
                    <div className="kv"><span className="k">Hits</span><span className="v">{(policy.hits ?? []).join(", ") || "none"}</span></div>
                  </div>
                </>
              )}
            </div>
            <div className="dact">
              <input className="cmt" placeholder="Add a comment (stored with your decision)…" value={comment} onChange={(e) => setComment(e.target.value)} />
              <button className="btn btn-g" disabled={busy} onClick={() => decide("approve")}>✓ Approve &amp; execute</button>
              <button className="btn btn-r" disabled={busy} onClick={() => decide("reject")}>✕ Reject</button>
              <div className="dnote" style={{ width: 210 }}>
                Decision is signed, resumes the A2A <span className="mono">input-required</span> task, and lands in the hash-chained audit log.
              </div>
            </div>
          </>
        ) : (
          <div className="empty">select an approval</div>
        )}
        {flash && <div style={{ padding: "8px 18px", fontSize: 12, color: "#34d399" }}>{flash}</div>}
      </div>
    </div>
  );
}

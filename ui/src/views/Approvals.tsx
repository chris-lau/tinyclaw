import { useEffect, useState } from "react";
import { api, money } from "../api";

export default function Approvals({ tick, onDecided }: { tick: number; onDecided: () => void }) {
  const [pending, setPending] = useState<any[]>([]);
  const [resolved, setResolved] = useState<any[]>([]);
  const [selId, setSelId] = useState<string | null>(null);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<string | null>(null);

  useEffect(() => {
    api.approvals("pending").then((p) => {
      setPending(p);
      if (!selId && p.length) setSelId(p[0].id);
    }).catch(() => {});
    api.approvals().then(setResolved).catch(() => {});
  }, [tick]);

  const sel = pending.find((a) => a.id === selId) ?? pending[0];

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
          <div className="itab on">Pending ({pending.length})</div>
          <div className="itab" style={{ cursor: "default" }}>Resolved</div>
        </div>
        <div className="ilist">
          {pending.map((a) => (
            <div className={`item ${a.id === sel?.id ? "sel" : ""}`} key={a.id} onClick={() => setSelId(a.id)}>
              <div className="r1"><span className="id">{a.subject?.slice(0, 30)}</span><span className="amt">{a.amount != null ? money(a.amount) : ""}</span></div>
              <div className="ttl">{a.scenario} · tier {a.tier}</div>
              <div className="ft">
                <span className="chip c-amber">{a.action}</span>
                <span className="ago">{new Date(a.ts * 1000).toLocaleTimeString()}</span>
              </div>
            </div>
          ))}
          {pending.length === 0 && <div className="empty">inbox clear — nothing waiting on a human</div>}
          {resolved.filter((a) => a.status !== "pending").slice(0, 6).map((a) => (
            <div className="item dim" key={a.id}>
              <div className="r1"><span className="id">{a.subject?.slice(0, 30)}</span><span className="amt">{a.amount != null ? money(a.amount) : ""}</span></div>
              <div className="ft">
                <span className={`chip ${a.status === "approved" ? "c-green" : "c-red"}`}>{a.status}</span>
                <span className="ago">{a.decided_by ? `by ${a.decided_by}` : ""}</span>
              </div>
            </div>
          ))}
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
                    <div className="kv"><span className="k">Amount</span><span className="v"><b>{money(req.amount)}</b></span></div>
                    <div className="kv"><span className="k">Vendor</span><span className="v">{req.vendor}</span></div>
                    <div className="kv"><span className="k">Description</span><span className="v">{req.description}</span></div>
                    <div className="kv"><span className="k">Cost center</span><span className="v">{req.cost_center}</span></div>
                  </div>
                  <div className="pk-card">
                    <div className="pk-h"><span className="dot" style={{ background: "#34d399" }} />Research findings</div>
                    <div className="kv"><span className="k">Vendor id</span><span className="v">{research.vendor?.vendor_id} · tier {research.vendor?.tier}</span></div>
                    <div className="kv"><span className="k">On-time</span><span className="v">{research.vendor?.on_time_pct}%</span></div>
                    <div className="kv"><span className="k">Sanctions</span><span className="v">{research.sanctioned ? <b style={{ color: "#f87171" }}>HIT</b> : "clear"}</span></div>
                    <div className="kv"><span className="k">Budget</span><span className="v">{money(research.budget?.remaining)} remaining in {req.cost_center}</span></div>
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

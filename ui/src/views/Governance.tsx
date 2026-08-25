import { useEffect, useState } from "react";
import { api, fmtClock } from "../api";

export default function Governance({ tick, scenario }: { tick: number; scenario: string; live: any[] }) {
  const [audit, setAudit] = useState<any[]>([]);
  const [verify, setVerify] = useState<any>(null);
  const [policies, setPolicies] = useState<any[]>([]);

  useEffect(() => {
    api.audit(150).then(setAudit).catch(() => {});
    api.auditVerify().then(setVerify).catch(() => {});
    api.policies().then(setPolicies).catch(() => {});
  }, [tick]);

  const inScenario = (p: any) => scenario === "all" || p.scenario === scenario;
  // policy rule sets (not risk/identity/hooks): every pack's main policy file
  const ruleSets = policies.filter((p) => /policies\/(procurement|support)\.yaml$/.test(p.file) && inScenario(p));

  return (
    <div className="gv-grid">
      <div className="gv-col">
        <div className="card gv-card" style={{ flex: 1 }}>
          <h3 className="sec" style={{ marginBottom: 8 }}>Policy rules (as code)</h3>
          {ruleSets.flatMap((p) =>
            [{ header: p.scenario }].concat((p.yaml?.policies ?? []) as any[]).map((r: any, i: number) =>
              r.header ? (
                <div key={`${p.scenario}-h`} className="drow" style={{ paddingBottom: 2 }}>
                  <span className="chip c-blue" style={{ fontSize: 9.5 }}>{r.header}</span>
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
          {policies.filter((p) => p.file.endsWith("risk.yaml") && inScenario(p)).map((p) =>
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
          {policies.filter((p) => p.file.endsWith("identity.yaml") && inScenario(p)).map((p) =>
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

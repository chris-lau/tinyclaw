import { useEffect, useState } from "react";
import { AGENT_COLORS, ago, api, fmtClock, money } from "../api";

const STAGES: { stage: string; label: string; color: string }[] = [
  { stage: "intake", label: "Intake", color: AGENT_COLORS.intake },
  { stage: "research", label: "Research", color: AGENT_COLORS.research },
  { stage: "policy", label: "Policy", color: AGENT_COLORS.policy },
  { stage: "approval", label: "Human approval", color: "#f59e0b" },
  { stage: "executed", label: "Executed", color: AGENT_COLORS.orchestrator },
];

function stateChip(state: string) {
  const cls =
    state === "completed" || state === "working" ? "c-green" :
    state === "input_required" || state === "approval" ? "c-amber" :
    state === "rejected" || state === "failed" || state === "denied" ? "c-red" : "c-gray";
  const label =
    state === "input_required" ? "input-required" :
    state === "completed" ? "done" : state;
  return <span className={`chip ${cls}`}>{label}</span>;
}

export default function Overview({ live, tick, scenario, onOpenTask }: { live: any[]; tick: number; scenario: string; onOpenTask: () => void }) {
  const [kpis, setKpis] = useState<any>(null);
  const [tasks, setTasks] = useState<any[]>([]);
  const [fleet, setFleet] = useState<any[]>([]);

  useEffect(() => {
    api.kpis().then(setKpis).catch(() => {});
    api.tasks().then(setTasks).catch(() => {});
    api.agents().then(setFleet).catch(() => {});
  }, [tick]);

  const k = kpis ?? {};

  return (
    <>
      <div className="kpis">
        <div className="card kpi">
          <div className="lbl">Autonomy rate</div>
          <div className="val">{k.autonomy_rate != null ? `${Math.round(k.autonomy_rate * 100)}%` : "—"}</div>
          <div className="dlt">{k.executions?.auto ?? 0} auto of {k.executions?.total ?? 0} executions</div>
        </div>
        <div className="card kpi">
          <div className="lbl">Escalation rate</div>
          <div className="val">{k.escalation_rate != null ? `${Math.round(k.escalation_rate * 100)}%` : "—"}</div>
          <div className="dlt">{k.executions?.human ?? 0} human-routed</div>
        </div>
        <div className="card kpi">
          <div className="lbl">Mean approval latency</div>
          <div className="val">
            {k.mean_approval_latency_s != null
              ? `${Math.floor(k.mean_approval_latency_s / 60)}m ${Math.round(k.mean_approval_latency_s % 60)}s`
              : "—"}
          </div>
          <div className="dlt">human decision → task resumed</div>
        </div>
        <div className="card kpi">
          <div className="lbl">Policy denials / guardrail hits</div>
          <div className="val">{k.policy_denials ?? 0} / {k.guardrail_hits ?? 0}</div>
          <div className="dlt">{k.pending_approvals ?? 0} pending approvals</div>
        </div>
      </div>

      <div className="main2">
        <div className="card pipe">
          <h3 className="sec" style={{ marginBottom: 11 }}>
            Live pipeline — {scenario === "all" ? "requests across all scenarios" : `scenario: ${scenario}`}
          </h3>
          <div className="stages">
            {STAGES.map((s) => {
              const inStage = tasks.filter(
                (t) => t.stage === s.stage
                && (scenario === "all" || t.scenario === scenario)
                && !["rejected", "denied", "failed", "blocked"].includes(t.state),
              );
              return (
                <div className="stage" key={s.stage}>
                  <div className="stg-h">
                    <span className="dot" style={{ background: s.color }} />
                    {s.label}
                    <span className="cnt">{inStage.length}</span>
                  </div>
                  {inStage.slice(0, 5).map((t) => (
                    <div className="tk" key={t.task_id} onClick={onOpenTask} title={t.title}>
                      <div className="r1">
                        <span style={{ display: "flex", gap: 4, alignItems: "center" }}>
                          {t.scenario && t.scenario !== "procurement" && (
                            <span className="chip c-blue" style={{ fontSize: 8.5, padding: "1px 5px" }}>{t.scenario}</span>
                          )}
                          <span className="mono" style={{ fontSize: 11 }}>{t.task_id.slice(0, 8)}</span>
                        </span>
                        <span className="amt">{money(t.amount)}</span>
                      </div>
                      <div className="ttl">{t.title}</div>
                      <div className="ft">
                        {stateChip(t.state)}
                        <span className="ago">{ago(t.updated_at)}</span>
                      </div>
                    </div>
                  ))}
                  {inStage.length === 0 && <div className="ago" style={{ fontSize: 11 }}>—</div>}
                </div>
              );
            })}
          </div>
        </div>

        <div className="side">
          <div className="card feed">
            <h3 className="sec" style={{ marginBottom: 6 }}>Live A2A traffic</h3>
            {live.length === 0 && <div className="empty">waiting for events…</div>}
            {live.slice(0, 14).map((e) => (
              <div className="fr" key={e.id ?? Math.random()}>
                <span className="dot" style={{ background: AGENT_COLORS[e.agent] ?? "#5d6b7a", marginTop: 5 }} />
                <div style={{ flex: 1 }}>
                  <b style={{ color: AGENT_COLORS[e.agent] ?? "#8b98a5", fontSize: 11.5 }}>{e.agent}</b>{" "}
                  <span style={{ color: "#7f93aa", fontSize: 11 }}>{e.type}</span>
                  <div className="what">
                    {e.type === "task.state" && e.data?.state}
                    {e.type === "policy.decision" && `${e.data?.effect} → ${e.data?.route} (tier ${e.data?.tier})${e.data?.posture && e.data.posture !== "balanced" ? ` · posture ${e.data.posture}` : ""}`}
                    {e.type === "guardrail.hit" &&
                      `${e.data?.redactions ?? 0} redaction(s)${e.data?.injection_patterns?.length ? `, ${e.data.injection_patterns.length} injection flag(s)` : ""}`}
                    {e.type === "hook.blocked" && `⛔ boundary hook “${e.data?.hook}” refused message → ${e.data?.to}`}
                    {e.type === "hook.redacted" && `boundary redaction before send → ${e.data?.to}`}
                    {e.type === "posture.changed" && `autonomy dial: ${e.data?.previous} → ${e.data?.posture}`}
                    {e.type === "a2a.hop" && `→ ${e.data?.to}`}
                    {e.type === "audit.append" && `${e.data?.actor} ${e.data?.action} → ${e.data?.decision}`}
                    {e.type === "approval.created" && `${e.data?.subject} ${money(e.data?.amount)}`}
                    {e.type === "agent.deployed" && `${e.data?.name} live at ${e.data?.url}`}
                    {!["task.state", "policy.decision", "guardrail.hit", "hook.blocked", "hook.redacted", "posture.changed", "a2a.hop", "audit.append", "approval.created", "agent.deployed"].includes(e.type) && JSON.stringify(e.data).slice(0, 90)}
                  </div>
                </div>
                <div className="ms">{fmtClock(e.ts)}</div>
              </div>
            ))}
          </div>

          <div className="card fleet-card" style={{ padding: "13px 14px" }}>
            <h3 className="sec" style={{ marginBottom: 7 }}>Agent fleet</h3>
            {fleet
              .filter((a) => scenario === "all" || a.scenario === scenario)
              .map((a, i) => (
                <div className="fl" key={i}>
                  <span className="dot" style={{ background: AGENT_COLORS[a.card?.name] ?? (a.live ? "#34d399" : "#5d6b7a") }} />
                  <span className="nm">{a.card?.name ?? "?"}</span>
                  {a.studio && <span className={`chip ${a.live ? "c-green" : "c-gray"}`}>{a.status ?? (a.live ? "live" : "draft")}</span>}
                  {!a.studio && <span className={`chip ${a.live ? "c-green" : "c-red"}`}>{a.live ? "Live" : "Down"}</span>}
                  <span className="lat">{a.card?.version ? `v${a.card.version}` : ""}</span>
                </div>
              ))}
          </div>
        </div>
      </div>
    </>
  );
}

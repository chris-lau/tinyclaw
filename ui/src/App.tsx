import { useCallback, useEffect, useState } from "react";
import { api, sse } from "./api";
import Overview from "./views/Overview";
import Tasks from "./views/Tasks";
import Approvals from "./views/Approvals";
import Studio from "./views/Studio";
import Governance from "./views/Governance";
import Playground from "./views/Playground";

export type View = "overview" | "tasks" | "approvals" | "studio" | "governance" | "playground";

const TABS: { id: View; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "tasks", label: "Tasks" },
  { id: "approvals", label: "Approvals" },
  { id: "studio", label: "Agent Studio" },
  { id: "governance", label: "Governance" },
  { id: "playground", label: "Playground" },
];

export default function App() {
  const [view, setView] = useState<View>("overview");
  const [live, setLive] = useState<any[]>([]);
  const [health, setHealth] = useState<any>(null);
  const [tick, setTick] = useState(0);
  const [posture, setPosture] = useState<string>("balanced");

  const bump = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    api.health().then((h) => {
      setHealth(h);
      if (h?.posture) setPosture(h.posture);
    }).catch(() => setHealth(null));
    api.posture().then((p) => setPosture(p.posture)).catch(() => {});
    const stop = sse((ev: any) => {
      if (ev?.type && ev.type !== "hello") setLive((l) => [ev, ...l].slice(0, 60));
      if (ev?.type === "posture.changed") setPosture(ev.data?.posture);
      bump();
    });
    return stop;
  }, [bump]);

  async function changePosture(p: string) {
    setPosture(p);
    try {
      await api.setPosture(p);
    } catch {
      /* SSE will correct us if the change failed */
    }
    bump();
  }

  return (
    <div className="app">
      <div className="topnav">
        <div className="logo" onClick={() => setView("overview")}>
          <div className="mark">🦞</div>tinyclaw
        </div>
        <div className="navlinks">
          {TABS.map((t) => (
            <div key={t.id} className={`nl ${view === t.id ? "on" : ""}`} onClick={() => setView(t.id)}>
              {t.label}
            </div>
          ))}
        </div>
        <div className="nr">
          <div className="pill pill-posture" title="Autonomy dial — rewrites tier-rule effects; tier 3 and denies are never relaxed">
            Autonomy:&nbsp;
            <select
              value={posture}
              onChange={(e) => changePosture(e.target.value)}
              style={{ background: "transparent", border: "none", color: "#f59e0b", fontWeight: 700, fontSize: 12, cursor: "pointer" }}
            >
              <option value="conservative">conservative</option>
              <option value="balanced">balanced</option>
              <option value="full">full autonomy</option>
            </select>
          </div>
          <div className="pill pill-scenario">
            Scenario: <b>{health?.scenarios?.[0] ?? "—"}</b>
          </div>
          <div className="pill pill-llm">
            {health?.llm ?? "…"} {health?.llm === "mock" ? "· mock mode" : ""}
          </div>
          <div className="av">CL</div>
        </div>
      </div>
      <div className="content">
        {view === "overview" && <Overview live={live} tick={tick} onOpenTask={() => setView("tasks")} />}
        {view === "tasks" && <Tasks tick={tick} live={live} />}
        {view === "approvals" && <Approvals tick={tick} onDecided={bump} />}
        {view === "studio" && <Studio tick={tick} onDeployed={bump} />}
        {view === "governance" && <Governance tick={tick} live={live} />}
        {view === "playground" && <Playground onSubmitted={bump} />}
      </div>
    </div>
  );
}

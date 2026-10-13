import { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";
import { cx } from "./components/ui.jsx";
import Challenges from "./views/Challenges.jsx";
import Executions from "./views/Executions.jsx";
import Security from "./views/Security.jsx";
import Workspace from "./views/Workspace.jsx";

const TABS = [
  ["dashboard", "Dashboard", "", ""],
  ["executions", "Executions", "Execution history", "Every run with its violations, evidence and report."],
  ["challenges", "Challenges", "Official challenges", "Reproducible tests for the five hackathon scenarios, run against the live sandbox."],
  ["security", "Security", "Security controls", "What the sandbox enforces, and whether each control was verified inside a real container."],
];
const fromHash = () => (TABS.find(([k]) => `#${k}` === window.location.hash) || TABS[0])[0];

function Cell({ label, children, className = "" }) {
  return (
    <div className={cx("hidden h-12 flex-col justify-center border-l border-line px-4 leading-tight sm:flex", className)}>
      <span className="text-[9px] font-semibold uppercase tracking-label text-faint">{label}</span>
      <span className="font-mono text-xs text-fg">{children}</span>
    </div>
  );
}

export default function App() {
  const [view, setView] = useState(fromHash());
  const [health, setHealth] = useState(null);
  const [cfg, setCfg] = useState(null);
  const [refresh, setRefresh] = useState(0);
  const [openId, setOpenId] = useState(null);

  useEffect(() => {
    const onHash = () => setView(fromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  const go = (k) => { window.location.hash = k; setView(k); };

  useEffect(() => {
    let alive = true;
    const loadHealth = () => api.health().then((h) => alive && setHealth(h)).catch(() => alive && setHealth({ status: "offline", docker: false }));
    loadHealth();
    const t = setInterval(loadHealth, 3000);
    return () => { alive = false; clearInterval(t); };
  }, []);
  useEffect(() => {
    let alive = true;
    const load = () => api.config().then((c) => alive && setCfg(c)).catch(() => alive && setTimeout(load, 2000));
    load();
    return () => { alive = false; };
  }, []);

  const bump = useCallback(() => setRefresh((n) => n + 1), []);
  const online = health?.docker;
  const sch = health?.scheduler;
  const [, , title, blurb] = TABS.find(([k]) => k === view) || TABS[0];
  const busy = sch && (sch.active > 0 || sch.pending > 0);

  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-20 border-b border-line bg-bg">
        <div className="absolute inset-x-0 top-0 h-0.5 bg-red" />
        <div className="flex h-12 items-stretch pt-0.5">
          <a href="#dashboard" onClick={() => go("dashboard")} className="flex shrink-0 items-center gap-2.5 border-r border-line pl-4 pr-5">
            <span className="flex h-6 w-6 items-center justify-center bg-red font-mono text-[11px] font-bold text-white">KG</span>
            <span className="text-sm font-semibold uppercase tracking-[0.14em]">KernelGuard</span>
          </a>
          <nav className="flex min-w-0 items-stretch overflow-x-auto" aria-label="Main">
            {TABS.map(([k, l]) => (
              <button key={k} type="button" onClick={() => go(k)} aria-current={view === k ? "page" : undefined}
                className={cx("press relative shrink-0 px-4 text-xs font-semibold uppercase tracking-label",
                  view === k ? "bg-raised text-fg" : "text-muted hover:bg-surface hover:text-fg")}>
                {l}
                <span className={cx("absolute inset-x-0 bottom-0 h-0.5 origin-left bg-red transition-transform duration-200", view === k ? "scale-x-100" : "scale-x-0")} />
              </button>
            ))}
          </nav>
          <div className="ml-auto flex items-stretch">
            <Cell label="Running">{sch ? `${sch.active}/${sch.max_active}` : "-"}</Cell>
            <Cell label="Queued">{sch ? `${sch.pending}/${sch.max_pending}` : "-"}</Cell>
            <Cell label="Explainer" className="hidden md:flex">{health?.ai?.configured ? health.ai.provider : "rule-based"}</Cell>
            <div className="flex h-12 items-center gap-2 border-l border-line px-4 font-mono text-xs font-semibold">
              <span className={cx("h-2 w-2 rounded-full bg-current", !health ? "text-faint" : !online ? "text-red" : busy ? "text-warn" : "text-ok", (online || !health) && "led")} />
              <span className={online ? "text-ok" : health ? "text-bad" : "text-faint"}>{online ? "ONLINE" : health ? "DOCKER DOWN" : "CONNECTING"}</span>
            </div>
          </div>
        </div>
      </header>

      {health && !health.docker && (
        <div className="border-b border-red bg-red/10 px-5 py-2.5 text-sm text-bad">
          Docker is not reachable, so jobs cannot start. Start Docker Desktop and wait for the engine to report running.
        </div>
      )}

      <main className="flex-1">
        {view !== "dashboard" && (
          <div className="enter border-b border-line bg-surface px-5 py-4" key={view}>
            <h1 className="text-lg font-semibold tracking-tight">{title}</h1>
            <p className="mt-0.5 text-[13px] text-muted">{blurb}</p>
          </div>
        )}
        <div hidden={view !== "dashboard"}><Workspace cfg={cfg} health={health} onJobSaved={bump} /></div>
        {view === "executions" && <div className="p-5"><Executions cfg={cfg} refreshKey={refresh} openId={openId} onOpened={() => setOpenId(null)} /></div>}
        {view === "challenges" && <div className="p-5"><Challenges onDone={bump} onOpenJob={(id) => { setOpenId(id); go("executions"); }} /></div>}
        {view === "security" && <div className="p-5"><Security /></div>}
      </main>
    </div>
  );
}

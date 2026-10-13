import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown, ChevronRight, Loader2, Play, X } from "lucide-react";
import { api } from "../api.js";
import { Button, Chip, cx } from "../components/ui.jsx";

function Card({ c, onOpenJob, onDone }) {
  const [run, setRun] = useState(null);
  const [err, setErr] = useState("");
  const [open, setOpen] = useState(false);
  const timer = useRef(null);
  useEffect(() => () => clearInterval(timer.current), []);

  async function start() {
    setErr("");
    setRun({ status: "RUNNING", checks: [], log: [], jobs: [] });
    try {
      const { run_id } = await api.runChallenge(c.id);
      timer.current = setInterval(async () => {
        try {
          const r = await api.challengeRun(run_id);
          setRun(r);
          if (r.status !== "RUNNING") { clearInterval(timer.current); onDone?.(); }
        } catch { /* keep polling */ }
      }, 1000);
    } catch (e) {
      setRun(null);
      setErr(e.message);
    }
  }

  const running = run?.status === "RUNNING";
  const result = run?.result;
  const last = c.last_run;
  const state = run ? run.status : last ? (last.passed ? "PASSED" : "FAILED") : null;
  const checks = run?.checks?.length ? run.checks : [];
  const stripe = running ? "bg-fg" : state === "PASSED" ? "bg-ok" : state ? "bg-red" : "bg-line2";
  const passed = checks.filter((k) => k.passed).length;

  return (
    <section className="relative border border-line bg-surface">
      <span className={cx("absolute inset-y-0 left-0 w-1 transition-colors", stripe)} />
      <div className="flex flex-wrap items-start gap-x-5 gap-y-3 py-4 pl-7 pr-4">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center bg-red font-mono text-sm font-bold text-white">{c.id}</span>
        <div className="min-w-0 flex-1 basis-72">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-[15px] font-semibold">{c.title}</h3>
            <Chip>{c.difficulty}</Chip>
            {c.simulated && <Chip tone="warn" title="The monitor fault is injected deterministically; kernel and container measurements are real.">Simulated fault</Chip>}
            {state && !running && (
              <Chip tone={state === "PASSED" ? "ok" : "bad"}>
                {state === "PASSED" ? <Check size={11} /> : <X size={11} />} {state}{!run && " · last run"}
              </Chip>
            )}
          </div>
          <p className="mt-1.5 text-[13px] leading-relaxed text-muted">{c.objective}</p>
          <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
            className="press mt-2.5 inline-flex items-center gap-1 text-[11px] font-semibold uppercase tracking-label text-faint hover:text-fg">
            {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />} OS mechanism and method
          </button>
        </div>
        <Button variant="default" onClick={start} disabled={running} className="h-9 shrink-0 px-4 text-xs font-semibold uppercase tracking-wide">
          {running ? <Loader2 size={14} className="animate-spin" /> : <Play size={13} fill="currentColor" />} {running ? "Running" : "Run test"}
        </Button>
      </div>
      {err && <p className="border-t border-line px-7 py-2.5 text-sm text-warn">{err}</p>}

      {open && (
        <div className="enter grid gap-4 border-t border-line bg-bg px-7 py-4 text-xs leading-relaxed text-muted md:grid-cols-2">
          <p><b className="block pb-1 text-[10px] uppercase tracking-label text-faint">Mechanism</b>{c.os_mechanism}</p>
          <p><b className="block pb-1 text-[10px] uppercase tracking-label text-faint">Test</b>{c.how}</p>
        </div>
      )}

      {run && (checks.length > 0 || running) && (
        <div className="border-t border-line">
          <div className="flex items-center justify-between bg-raised px-7 py-2 text-[10px] font-semibold uppercase tracking-label text-faint">
            <span>Checks</span>
            <span className="font-mono">{passed}/{checks.length} passed</span>
          </div>
          <ul className="divide-y divide-line">
            {checks.map((k, i) => (
              <li key={i} className="enter flex gap-3 px-7 py-2 text-xs" style={{ animationDelay: `${Math.min(i, 12) * 25}ms` }}>
                {k.passed ? <Check size={14} className="mt-px shrink-0 text-ok" /> : <X size={14} className="mt-px shrink-0 text-bad" />}
                <span className="min-w-0">
                  <span className={cx("block", k.passed ? "text-fg" : "text-bad")}>{k.name}</span>
                  {k.detail && <span className="block break-words font-mono text-[11px] text-faint">{k.detail}</span>}
                </span>
              </li>
            ))}
            {running && (
              <li className="flex items-center gap-3 px-7 py-2.5 text-xs text-muted">
                <Loader2 size={13} className="animate-spin" /> {run.log?.[run.log.length - 1] || "Starting..."}
              </li>
            )}
          </ul>
          {run.jobs?.length > 0 && (
            <div className="flex flex-wrap gap-2 border-t border-line px-7 py-3">
              {run.jobs.map((j) => (
                <button key={j.id} type="button" onClick={() => onOpenJob(j.id)}
                  className="press border border-line2 px-2 py-1 text-[11px] text-muted hover:border-fg hover:text-fg">
                  <span className="font-mono">{j.id.slice(0, 8)}</span> &middot; {j.label}
                </button>
              ))}
            </div>
          )}
          {result?.evidence?.peak_active !== undefined && (
            <p className="border-t border-line px-7 py-2.5 font-mono text-xs text-muted">
              peak active {result.evidence.peak_active}/2 &middot; peak pending {result.evidence.peak_pending}/4 &middot; host budget {result.evidence.budget?.cpu} CPU / {Math.round(result.evidence.budget?.memory_mb || 0)} MB
            </p>
          )}
          {result && <p className="border-t border-line px-7 py-2 text-[11px] text-faint">Finished in {result.duration_s} s</p>}
        </div>
      )}
    </section>
  );
}

export default function Challenges({ onOpenJob, onDone }) {
  const [list, setList] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => { api.challenges().then((d) => setList(d.challenges)).catch((e) => setErr(e.message)); }, []);
  if (err) return <p className="border border-red p-4 text-sm text-bad">{err}</p>;
  if (!list) return <p className="p-4 text-sm text-muted">Loading challenges...</p>;
  return (
    <div className="max-w-5xl space-y-3">
      {list.map((c) => <Card key={c.id} c={c} onOpenJob={onOpenJob} onDone={onDone} />)}
    </div>
  );
}

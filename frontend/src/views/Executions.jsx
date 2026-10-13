import { useEffect, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import { api } from "../api.js";
import { ExecutionView } from "../components/Result.jsx";
import { Button, Chip, Panel, TONES, cx, dash, fmtTime, num, statusTone } from "../components/ui.jsx";

const SHORT = { TIME_LIMIT: "time", MEMORY_LIMIT: "memory", PROCESS_LIMIT: "pids", CPU_LIMIT: "cpu", OUTPUT_LIMIT: "output", SECURITY_POLICY_VIOLATION: "seccomp" };

export default function Executions({ cfg, refreshKey, openId, onOpened }) {
  const [rows, setRows] = useState([]);
  const [sel, setSel] = useState(null);
  const [job, setJob] = useState(null);
  const [loading, setLoading] = useState(false);
  const finalRef = useRef(false);

  const load = () => {
    setLoading(true);
    api.jobs().then(setRows).catch(() => {}).finally(() => setLoading(false));
  };
  useEffect(load, [refreshKey]);
  useEffect(() => { if (openId) { setSel(openId); onOpened?.(); } }, [openId]); // eslint-disable-line
  useEffect(() => {
    if (!sel) { setJob(null); return undefined; }
    let stop = false;
    finalRef.current = false;
    const tick = () => api.job(sel).then((j) => { if (!stop) { setJob(j); finalRef.current = j.is_final; } }).catch(() => {});
    tick();
    const t = setInterval(() => { if (!finalRef.current) tick(); }, 1000);
    return () => { stop = true; clearInterval(t); };
  }, [sel]); // eslint-disable-line

  return (
    <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,30rem)_minmax(0,1fr)]">
      <Panel title={`History · ${rows.length}`} flush
        right={<Button variant="ghost" onClick={load} className="!px-1.5 !py-0.5 text-[11px]"><RefreshCw size={12} className={loading ? "animate-spin" : ""} /> Refresh</Button>}>
        {rows.length === 0 ? (
          <p className="p-4 text-sm text-muted">No executions yet. Run a program from the Dashboard and it will be listed here with its full record.</p>
        ) : (
          <div className="max-h-[78vh] overflow-auto">
            <table className="w-full border-collapse text-left text-xs">
              <thead className="sticky top-0 z-10 bg-surface text-[10px] uppercase tracking-label text-faint">
                <tr className="border-b border-line">
                  <th className="px-3 py-2 font-semibold">Time</th><th className="py-2 font-semibold">Program</th>
                  <th className="py-2 font-semibold">Outcome</th><th className="px-3 py-2 text-right font-semibold">Run</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id} onClick={() => setSel(r.id)} tabIndex={0} onKeyDown={(e) => e.key === "Enter" && setSel(r.id)}
                    className={cx("cursor-pointer border-b border-line align-top transition-colors hover:bg-raised", sel === r.id && "bg-raised")}>
                    <td className="relative whitespace-nowrap px-3 py-2.5 font-mono text-faint">
                      <span className={cx("absolute inset-y-0 left-0 w-0.5 transition-opacity", TONES[statusTone(r.status)].bar, sel === r.id ? "opacity-100" : "opacity-0")} />
                      {fmtTime(r.created_at)}
                    </td>
                    <td className="max-w-[9rem] py-2.5 pr-2">
                      <div className="truncate font-mono text-fg">{r.entry_point || r.filename}</div>
                      <div className="text-faint">{r.language} &middot; {r.resource_profile}{r.challenge_id ? ` · ${r.challenge_id}` : ""}</div>
                    </td>
                    <td className="py-2.5 pr-2">
                      <span className={cx("font-mono text-[11px] font-semibold uppercase", TONES[statusTone(r.status)].text)}>{dash(r.status_label || r.status)}</span>
                      {r.violations?.length > 0 && (
                        <div className="mt-1 flex flex-wrap gap-1">
                          {r.violations.map((v, i) => <Chip key={i} tone={v.role === "PRIMARY" ? "bad" : "mute"}>{SHORT[v.type] || v.type}</Chip>)}
                        </div>
                      )}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2.5 text-right font-mono text-faint">{num(r.runtime, 1)} s</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <div className="min-w-0">
        {job ? (
          <ExecutionView job={job} ai={cfg?.ai} onChange={setJob} />
        ) : (
          <div className="border border-dashed border-line2 p-8 text-center text-sm text-muted">
            Select an execution to see its result, every recorded violation, the explanation, telemetry and the PDF report.
          </div>
        )}
      </div>
    </div>
  );
}

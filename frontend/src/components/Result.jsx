import { Fragment, useEffect, useState } from "react";
import { Bot, Download, RefreshCw, Sparkles } from "lucide-react";
import { api } from "../api.js";
import { Button, Chip, Expand, Meter, TONES, cx, dash, fmtTime, num, statusTone } from "./ui.jsx";

const ACTIVE = ["QUEUED", "STARTING", "COMPILING", "RUNNING"];
const VTITLE = {
  TIME_LIMIT: "Time limit", MEMORY_LIMIT: "Memory limit", PROCESS_LIMIT: "Process limit", CPU_LIMIT: "CPU limit",
  OUTPUT_LIMIT: "Output limit", SECURITY_POLICY_VIOLATION: "Security policy",
};
const ACTION = {
  TERMINATED: ["Terminated by sandbox", "bad"], KILLED_BY_KERNEL: ["Killed by kernel", "bad"], DENIED: ["Denied by kernel", "warn"],
  THROTTLED: ["Throttled", "accent"], OBSERVED: ["Reported by program", "warn"],
};

/* ---------------------------------------------------------------- metrics strip */
export function Metrics({ job, limits }) {
  const running = job && ACTIVE.includes(job.status);
  const cur = job?.current || {};
  const L = job
    ? { cpu: job.cpu_limit, mem: job.memory_limit, time: job.time_limit, pids: job.pid_limit }
    : { cpu: limits.cpu, mem: limits.memory_mb, time: limits.time, pids: limits.pids };
  const cpu = running ? cur.cpu_pct ?? 0 : job?.cpu_usage ?? 0;
  const mem = running ? cur.memory_mb ?? 0 : job?.peak_memory ?? 0;
  const pids = running ? cur.pids ?? 0 : job?.process_count ?? 0;
  const tag = running ? "now" : job ? "peak" : "";
  const live = running && job.status === "RUNNING";
  return (
    <div className="grid grid-cols-2 gap-px border border-line bg-line lg:grid-cols-4">
      <Meter label={`CPU ${tag}`} value={cpu} display={`${num(cpu, 0)}%`} limit={L.cpu * 100} limitText={`${num(L.cpu * 100, 0)}% (${L.cpu} CPU)`}
        running={live} note={job && job.cpu_usage > 0 && running ? `peak ${num(job.cpu_usage, 0)}%` : null} />
      <Meter label={`Memory ${tag}`} value={mem} display={`${num(mem, 0)} MB`} limit={L.mem} limitText={`${L.mem} MB`}
        running={live} note={job && running && job.peak_memory > 0 ? `peak ${num(job.peak_memory, 0)} MB` : null} />
      <Meter label={`Processes ${tag}`} value={pids} display={pids} limit={L.pids} limitText={`${L.pids} PIDs`}
        running={live} note={job && running && job.process_count > 0 ? `peak ${job.process_count}` : "includes 2 support tasks"} />
      <Meter label="Runtime" value={job?.runtime ?? 0} display={`${num(job?.runtime ?? 0, 1)} s`} limit={L.time} limitText={`${L.time} s`}
        running={live} note={job?.status === "COMPILING" ? "compiling (not counted)" : null} />
    </div>
  );
}

/* ---------------------------------------------------------------- status bar */
function Fact({ label, children }) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] font-semibold uppercase tracking-label text-faint">{label}</div>
      <div className="truncate font-mono text-xs text-fg">{children}</div>
    </div>
  );
}

function StatusBar({ job }) {
  const tone = job ? statusTone(job.status) : "mute";
  const active = job && ["STARTING", "COMPILING", "RUNNING"].includes(job.status);
  const label = job ? dash(job.status_label || job.status) : "READY";
  const final = job?.is_final;
  const canReport = final && !["REJECTED_CAPACITY", "CANCELLED"].includes(job.status);
  return (
    <div key={final ? job.id + job.status : "live"} className={cx("relative overflow-hidden border border-line bg-surface", final && "flash")}>
      <span className={cx("absolute inset-y-0 left-0 w-1.5", TONES[tone].bar)} />
      <div className="flex flex-wrap items-center gap-x-8 gap-y-3 py-3.5 pl-6 pr-4">
        <div className="min-w-0 flex-1 basis-64">
          <div className="text-[10px] font-semibold uppercase tracking-label text-faint">Execution status</div>
          <div key={label} className={cx("stamp mt-1 flex items-center gap-2.5 font-mono text-[22px] font-semibold uppercase leading-tight tracking-wide", TONES[tone].text)}>
            {active && <span className="h-2.5 w-2.5 shrink-0 rounded-full bg-red led" />}
            <span className="min-w-0 break-words">{label}</span>
          </div>
        </div>
        {job && (
          <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
            <Fact label="Job">{job.id}</Fact>
            <Fact label="Entry">{job.entry_point || "n/a"}</Fact>
            {job.exit_code !== null && job.exit_code !== undefined && <Fact label="Exit">{job.exit_code}</Fact>}
            {job.status === "QUEUED" && job.queue_position && <Fact label="Queue">position {job.queue_position}</Fact>}
            {job.challenge_id && <Fact label="Challenge">{job.challenge_id}{job.simulated ? " · simulated" : ""}</Fact>}
            {canReport && (
              <a href={api.reportUrl(job.id)}
                className="press inline-flex items-center gap-2 border border-line2 bg-raised px-3 py-1.5 text-xs font-medium text-fg hover:border-fg">
                <Download size={14} /> PDF report
              </a>
            )}
          </div>
        )}
      </div>
      {active && (
        <div className="absolute inset-x-0 bottom-0 h-0.5 overflow-hidden bg-line">
          <div className="scan h-full w-1/4 bg-red" />
        </div>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- tab contents */
export function Violations({ list }) {
  if (!list?.length) {
    return <p className="p-5 text-sm text-muted">No limit violations were observed in this run.</p>;
  }
  return (
    <ul className="divide-y divide-line">
      {list.map((v, i) => {
        const [aLabel, aTone] = ACTION[v.action] || [v.action, "mute"];
        return (
          <li key={i} className="enter relative py-4 pl-6 pr-4" style={{ animationDelay: `${i * 60}ms` }}>
            <span className={cx("absolute inset-y-0 left-0 w-1", TONES[aTone].bar)} />
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-[13px] font-semibold uppercase tracking-wide">{VTITLE[v.type] || v.type}</span>
              <Chip tone={aTone}>{aLabel}</Chip>
              <Chip tone={v.confidence === "CONFIRMED" ? "ok" : "warn"} title="CONFIRMED = direct kernel/Docker/watchdog evidence; PROBABLE = inferred">
                {v.confidence === "CONFIRMED" ? "Confirmed" : "Probable"}
              </Chip>
              {v.role === "PRIMARY" ? <Chip tone="red">Ended the job</Chip> : <Chip>Concurrent</Chip>}
              {v.t_offset !== null && v.t_offset !== undefined && <span className="ml-auto font-mono text-[11px] text-faint">t+{num(v.t_offset, 2)} s</span>}
            </div>
            <dl className="mt-3 grid grid-cols-3 gap-4 font-mono text-xs">
              <div><dt className="text-[10px] uppercase tracking-label text-faint">Limit</dt><dd className="mt-0.5 text-fg">{v.limit ?? "n/a"} {v.limit != null ? v.unit : ""}</dd></div>
              <div><dt className="text-[10px] uppercase tracking-label text-faint">Observed</dt><dd className="mt-0.5 text-fg">{v.observed ?? "not measured"} {v.observed != null ? v.unit : ""}</dd></div>
              <div><dt className="text-[10px] uppercase tracking-label text-faint">Source</dt><dd className="mt-0.5 text-fg">{v.source}</dd></div>
            </dl>
            <p className="mt-3 text-xs leading-relaxed text-muted">{v.evidence}</p>
          </li>
        );
      })}
    </ul>
  );
}

function Output({ job, kind }) {
  const compile = kind === "compile";
  const empty = compile ? !job.compile_output : !job.stdout && !job.stderr;
  return (
    <pre className="m-4 max-h-[26rem] min-h-[8rem] overflow-auto whitespace-pre-wrap break-words border border-line bg-bg p-3 font-mono text-xs leading-relaxed">
      {empty ? (
        <span className="text-faint">{ACTIVE.includes(job.status) && !compile ? "Waiting for output..." : compile ? "(no compiler output)" : "(no output)"}</span>
      ) : compile ? (
        <span className="text-warn">{job.compile_output}</span>
      ) : (
        <>
          <span className="text-fg">{job.stdout}</span>
          <span className="text-bad">{job.stderr}</span>
        </>
      )}
    </pre>
  );
}

function List({ items, tone }) {
  if (!items?.length) return null;
  return (
    <ul className="space-y-1.5 text-[13px] leading-relaxed text-muted">
      {items.map((x, i) => (
        <li key={i} className="flex gap-2.5">
          <span className={cx("mt-[7px] h-1.5 w-1.5 shrink-0", tone === "warn" ? "bg-warn" : "bg-line2")} />
          <span className="min-w-0">{x}</span>
        </li>
      ))}
    </ul>
  );
}

const H = ({ children }) => <div className="mb-2 text-[10px] font-semibold uppercase tracking-label text-faint">{children}</div>;

function Explanation({ job, ai, onChange }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const ex = job.explanation;
  const c = ex?.content;
  async function run(useAi) {
    setBusy(true);
    setErr("");
    try {
      const rec = await api.explain(job.id, useAi);
      onChange({ ...job, explanation: rec });
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }
  if (!c) {
    return <p className="p-5 text-sm text-muted">{ACTIVE.includes(job.status) ? "The explanation appears when the job finishes." : "Preparing explanation..."}</p>;
  }
  return (
    <div className="space-y-5 p-5">
      <div className="flex flex-wrap items-center gap-3">
        <Chip tone={ex.source === "AI" ? "red" : "mute"}>
          {ex.source === "AI" ? <Sparkles size={11} /> : <Bot size={11} />} {ex.source === "AI" ? `AI${ex.model ? ` · ${ex.model}` : ""}` : "RULE_BASED"}
        </Chip>
        {ex.source !== "AI" && ex.ai_error && (
          <span className="text-[11px] text-faint" title={ex.ai_error}>AI unavailable: {ex.ai_error.slice(0, 80)}</span>
        )}
        {ai?.configured && (
          <Button variant="default" disabled={busy} onClick={() => run(true)} className="ml-auto !py-1 text-xs">
            <RefreshCw size={13} className={busy ? "animate-spin" : ""} /> {ex.source === "AI" ? "Regenerate" : ex.ai_attempted ? "Retry AI" : "Explain with AI"}
          </Button>
        )}
      </div>
      {err && <p className="text-xs text-bad">{err}</p>}
      <div>
        <p className="text-base font-semibold leading-snug">{c.summary}</p>
        <p className="mt-1.5 text-[13px] leading-relaxed text-muted">{c.why_ended}</p>
      </div>
      {c.violations?.length > 0 && (
        <div>
          <H>Each violation</H>
          <ul className="space-y-2 text-[13px] text-muted">
            {c.violations.map((v, i) => (
              <li key={i}><span className="font-mono text-xs text-fg">{v.type}</span> &mdash; {v.explanation}</li>
            ))}
          </ul>
          {c.relationships && <p className="mt-3 text-[13px] text-muted">{c.relationships}</p>}
        </div>
      )}
      <div className="grid gap-6 md:grid-cols-2">
        <div><H>Observed facts</H><List items={c.observed_facts} /></div>
        <div>
          <H>Probable causes (not proven)</H>
          {c.probable_causes?.length ? <List items={c.probable_causes} tone="warn" /> : <p className="text-[13px] text-faint">None inferred.</p>}
        </div>
      </div>
      <div><H>What to do</H><List items={c.suggestions} /></div>
    </div>
  );
}

function Technical({ job }) {
  const t = job.telemetry || {};
  const sv = t.security_verified;
  const mon = job.monitor || {};
  const c = job.config || {};
  return (
    <div>
      <Expand title="Operating-system enforcement" defaultOpen>
        <List items={job.explanation?.content?.os_mechanism} />
      </Expand>
      <Expand title="Measurements and reliability">
        <dl className="grid gap-x-6 gap-y-1.5 text-xs sm:grid-cols-[9rem_1fr]">
          <dt className="text-faint">Source</dt><dd className="text-muted">{t.source || "n/a"}</dd>
          {Object.entries(t.reliability || {}).filter(([k]) => k !== "simulated").map(([k, v]) => (
            <Fragment key={k}><dt className="text-faint">{k}</dt><dd className="text-muted">{v}</dd></Fragment>
          ))}
          <dt className="text-faint">Kernel counters</dt>
          <dd className="break-all font-mono text-muted">{t.cgroup_run_delta ? JSON.stringify(t.cgroup_run_delta) : "not available"}</dd>
        </dl>
      </Expand>
      <Expand title="Security controls verified inside this container">
        {sv ? (
          <ul className="grid gap-1 text-xs text-muted sm:grid-cols-2">
            <li>Probe user: uid {sv.uid} (workload uid 1000)</li>
            <li>Capabilities (effective): {sv.cap_eff === 0 ? "none" : sv.cap_eff}</li>
            <li>no-new-privileges: {sv.no_new_privs === 1 ? "on" : "OFF"}</li>
            <li>Seccomp: {sv.seccomp === 2 ? `filter active (${t.seccomp_profile})` : "OFF"}</li>
            <li>Root filesystem: {sv.rootfs_read_only ? "read-only" : "WRITABLE"}</li>
            <li>Network interfaces besides loopback: {sv.net_ifaces}</li>
            <li>Docker socket visible: {sv.docker_sock ? "YES" : "no"}</li>
            <li>Run-phase pids.max: {t.limits_verified?.pids_max ?? "n/a"} &middot; memory.max: {t.limits_verified?.memory_max ?? "n/a"} B</li>
          </ul>
        ) : <p className="text-xs text-faint">Not available for this job (it never reached a container).</p>}
      </Expand>
      <Expand title="Build plan and configuration">
        <div className="space-y-1.5 font-mono text-xs text-muted">
          <div>entry: {job.entry_point}</div>
          {job.plan?.compile?.map((a, i) => <div key={i}>compile: {a.join(" ")}</div>)}
          <div>run: {job.plan?.run?.join(" ")}</div>
          {job.plan?.ignored_programs?.length > 0 && <div>other programs not built: {job.plan.ignored_programs.join(", ")}</div>}
          <div>profile: {c.profile} ({c.source}) &middot; {c.cpu} CPU &middot; {c.memory_mb} MB &middot; {c.time} s &middot; {c.pids} PIDs &middot; network none</div>
        </div>
      </Expand>
      <Expand title="Queue and monitoring" badge={mon.simulated ? <Chip tone="warn">Simulated fault</Chip> : null}>
        <div className="space-y-1.5 text-xs text-muted">
          <div>Monitor samples: {mon.samples_received ?? 0} &middot; restarts: {mon.restarts ?? 0} &middot; probe: {mon.probe_available ? "available" : "unavailable"}</div>
          {mon.failed && <div className="text-bad">Monitor failure: {mon.failed.detail}</div>}
          {mon.fault_injection && <div className="text-warn">Fault injection (deterministic test): {JSON.stringify(mon.fault_injection)}</div>}
          {(mon.events || []).map((e, i) => <div key={i} className="font-mono"><span className="text-faint">{fmtTime(e.ts)}</span> {e.event} {e.detail}</div>)}
        </div>
      </Expand>
    </div>
  );
}

/* ---------------------------------------------------------------- tabbed result */
function Tabs({ job, ai, onChange }) {
  const final = job.is_final;
  const nViol = job.violations?.length || 0;
  const [tab, setTab] = useState(null);
  useEffect(() => { setTab(null); }, [job.id]);
  const current = tab || (final && nViol ? "violations" : "output");
  const items = [
    ["violations", "Violations", nViol || null],
    ["output", "Output", null],
    ["compiler", "Compiler", job.compile_output ? "!" : null],
    ["why", "Explanation", null],
    ["details", "Technical", null],
  ];
  return (
    <section className="border border-line bg-surface">
      <div role="tablist" className="flex overflow-x-auto border-b border-line bg-raised">
        {items.map(([k, l, badge]) => (
          <button key={k} type="button" role="tab" aria-selected={current === k} onClick={() => setTab(k)}
            className={cx("press relative flex shrink-0 items-center gap-2 px-4 py-2.5 text-[11px] font-semibold uppercase tracking-label",
              current === k ? "bg-surface text-fg" : "text-muted hover:text-fg")}>
            {current === k && <span className="absolute inset-x-0 top-0 h-0.5 bg-red" />}
            {l}
            {badge && <span className={cx("min-w-[18px] px-1 text-center font-mono text-[10px]", k === "violations" ? "bg-red text-white" : "bg-warn text-bg")}>{badge}</span>}
          </button>
        ))}
      </div>
      <div key={current} className="enter">
        {current === "violations" && (final ? <Violations list={job.violations} /> : (
          <p className="p-5 text-sm text-muted">Violations are finalised from kernel counters when the run ends. Live usage is shown above.</p>
        ))}
        {current === "output" && <Output job={job} kind="run" />}
        {current === "compiler" && <Output job={job} kind="compile" />}
        {current === "why" && <Explanation job={job} ai={ai} onChange={onChange} />}
        {current === "details" && <Technical job={job} />}
      </div>
    </section>
  );
}

/** Status bar + metric strip + tabs: the whole execution record. `empty` is rendered below the metrics when there is no job. */
export function ExecutionView({ job, limits, ai, onChange, banner, empty }) {
  return (
    <div className="min-w-0 space-y-4">
      <StatusBar job={job} />
      {banner}
      <Metrics job={job} limits={limits || {}} />
      {job ? <Tabs job={job} ai={ai} onChange={onChange} /> : empty}
    </div>
  );
}

export default ExecutionView;
export { dash };

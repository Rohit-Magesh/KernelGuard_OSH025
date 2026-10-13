import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, ChevronDown, ChevronRight, FileArchive, FileCode2, Loader2, Play, Save, Square, Upload, X } from "lucide-react";
import { api } from "../api.js";
import { ExecutionView } from "../components/Result.jsx";
import { Button, Chip, Field, Panel, Segmented, Step, cx, fmtBytes, inputCls } from "../components/ui.jsx";

const LANGS = [["c", "C"], ["cpp", "C++"], ["python", "Python"], ["java", "Java"]];
const PROFILES = [["safe", "Safe"], ["standard", "Standard"], ["high", "High"], ["preset", "Saved"], ["custom", "Custom"]];
const CATEGORY = { c: "c_cpp", cpp: "c_cpp", python: "python", java: "java" };
const CATEGORY_LABEL = { c_cpp: "C / C++", python: "Python", java: "Java" };
const FIELDS = [["cpu", "CPUs", "cpu", 0.25], ["memory_mb", "Memory (MB)", "memory_mb", 1], ["time", "Runtime (s)", "time", 1], ["pids", "PIDs", "pids", 1]];
const MAX_FILES = 20, MAX_FILE = 2 * 1024 * 1024, MAX_PROJECT = 10 * 1024 * 1024;
const ACTIVE = ["QUEUED", "STARTING", "COMPILING", "RUNNING"];
const FEATURED = ["c-normal", "c-multi-violation", "c-memory", "c-timeout", "c-two-programs", "c-seccomp"];
let seq = 0;

function treeRows(files, entry) {
  const rows = [];
  const seen = new Set();
  [...files].sort((a, b) => a.path.localeCompare(b.path)).forEach((f) => {
    const parts = f.path.split("/");
    parts.slice(0, -1).forEach((_, i) => {
      const d = parts.slice(0, i + 1).join("/");
      if (!seen.has(d)) { seen.add(d); rows.push({ dir: true, name: parts[i], depth: i }); }
    });
    rows.push({ name: parts[parts.length - 1], path: f.path, size: f.size, depth: parts.length - 1, entry: f.path === entry });
  });
  return rows;
}

export default function Workspace({ cfg, health, active, onJobSaved }) {
  const [items, setItems] = useState([]);
  const [language, setLanguage] = useState("c");
  const [profile, setProfile] = useState("standard");
  const [nums, setNums] = useState({ cpu: 1, memory_mb: 512, time: 30, pids: 50 });
  const [info, setInfo] = useState(null);
  const [problems, setProblems] = useState([]);
  const [notice, setNotice] = useState("");
  const [entry, setEntry] = useState("");
  const [job, setJob] = useState(null);
  const [runError, setRunError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [drag, setDrag] = useState(false);
  const [demos, setDemos] = useState([]);
  const [presetMsg, setPresetMsg] = useState("");
  const [showLimits, setShowLimits] = useState(false);
  const input = useRef(null);

  const category = CATEGORY[language];
  const platform = cfg?.platform_max || { cpu: 4, memory_mb: 4096, time: 120, pids: 200 };
  const minimum = cfg?.platform_min || { cpu: 0.25, memory_mb: 32, time: 1, pids: 5 };
  const running = job && ACTIVE.includes(job.status);

  useEffect(() => { api.demos().then(setDemos).catch(() => {}); }, []);

  // keep the numeric fields in step with the selected profile
  const applyProfile = useCallback((name, lang = language) => {
    setProfile(name);
    if (!cfg) return;
    const src = cfg.profiles[name] || (name === "preset" ? cfg.presets[CATEGORY[lang]] : null);
    if (src) setNums({ cpu: src.cpu, memory_mb: src.memory_mb, time: src.time, pids: src.pids });
  }, [cfg, language]);
  useEffect(() => { if (cfg && !running) applyProfile("standard"); /* first load */ }, [cfg]); // eslint-disable-line

  const edit = (k, v) => { setProfile("custom"); setNums((n) => ({ ...n, [k]: v })); };
  const bad = FIELDS.filter(([k]) => {
    const v = Number(nums[k]);
    return !(v >= minimum[k] && v <= platform[k]) || Number.isNaN(v);
  }).map(([k]) => k);

  // ---- files --------------------------------------------------------------
  function addFiles(list) {
    const next = [...items];
    const notes = [];
    for (const f of list) {
      if (next.some((x) => x.name === f.name && x.size === f.size)) { notes.push(`${f.name} is already in the project`); continue; }
      if (next.length >= MAX_FILES) { notes.push(`Limit of ${MAX_FILES} files reached; ${f.name} was not added`); continue; }
      const isZip = f.name.toLowerCase().endsWith(".zip");
      if (f.size > (isZip ? MAX_PROJECT : MAX_FILE)) { notes.push(`${f.name} is ${fmtBytes(f.size)}; the limit is ${isZip ? "10 MB for archives" : "2 MB per file"}`); continue; }
      next.push({ id: ++seq, file: f, name: f.name, size: f.size, zip: isZip });
    }
    if (next.reduce((s, x) => s + x.size, 0) > MAX_PROJECT) { notes.push("Project would exceed 10 MB; nothing was added"); setNotice(notes.join(". ")); return; }
    setItems(next);
    setNotice(notes.join(". "));
    setJob(null);
    setRunError(null);
  }
  const removeItem = (id) => { setItems(items.filter((x) => x.id !== id)); setJob(null); };
  const clearAll = () => { setItems([]); setInfo(null); setProblems([]); setEntry(""); setNotice(""); setJob(null); setRunError(null); };

  // server-side validation + entry-point detection whenever the project or language changes
  useEffect(() => {
    if (!items.length) { setInfo(null); setProblems([]); return undefined; }
    let cancelled = false;
    const t = setTimeout(() => {
      api.validate(items, language).then((r) => {
        if (cancelled) return;
        setInfo(r);
        setProblems([]);
        const cands = r.entry.candidates.map((c) => c.path);
        setEntry((cur) => (cur && cands.includes(cur) ? cur : r.entry.default || ""));
      }).catch((e) => {
        if (cancelled) return;
        setInfo(null);
        setProblems(e.details?.length ? e.details : [{ path: "", reason: e.message, code: e.code }]);
      });
    }, 250);
    return () => { cancelled = true; clearTimeout(t); };
  }, [items, language]);

  // ---- job polling --------------------------------------------------------
  useEffect(() => {
    if (!job || job.is_final) return undefined;
    const t = setInterval(() => {
      api.job(job.id).then((j) => { setJob(j); if (j.is_final) onJobSaved?.(); }).catch(() => {});
    }, 700);
    return () => clearInterval(t);
  }, [job?.id, job?.is_final]); // eslint-disable-line

  async function run() {
    setRunError(null);
    setBusy(true);
    try {
      const named = ["safe", "standard", "high"].includes(profile);
      const extra = { entry_point: entry || undefined, profile: named ? profile : "custom" };
      if (!named) Object.assign(extra, { cpu: nums.cpu, memory_mb: nums.memory_mb, time_limit: nums.time, pids: nums.pids });
      const j = await api.createJob(items, language, extra);
      setJob(j);
      onJobSaved?.();
    } catch (e) {
      if (e.jobId) {
        try { setJob(await api.job(e.jobId)); } catch { /* fall through to banner */ }
      }
      setRunError({ code: e.code, message: e.message, details: e.details, capacity: e.capacity });
    } finally {
      setBusy(false);
    }
  }

  async function loadDemo(id) {
    if (!id) return;
    const d = await api.demo(id);
    const files = d.files.map((f) => new File([f.content], f.name, { type: "text/plain" }));
    setItems(files.map((f) => ({ id: ++seq, file: f, name: f.name, size: f.size, zip: false })));
    setLanguage(d.language);
    setEntry("");
    setJob(null);
    setRunError(null);
    setNotice(`Loaded demo: ${d.name}. Expected: ${d.expect}`);
    applyProfile(d.profile || "standard", d.language);
  }

  async function savePreset() {
    try {
      await api.savePreset(category, { cpu: nums.cpu, memory_mb: nums.memory_mb, time: nums.time, pids: nums.pids });
      setPresetMsg(`Saved for ${CATEGORY_LABEL[category]}`);
      cfg.presets[category] = { ...nums };
      setProfile("preset");
    } catch (e) {
      setPresetMsg(e.message);
    }
    setTimeout(() => setPresetMsg(""), 2500);
  }

  const rows = useMemo(() => (info ? treeRows(info.files, entry || info.entry.default) : []), [info, entry]);
  const needsPick = info?.entry.needs_selection && !entry;
  const noEntry = info && info.entry.kind === "no-entry-point";
  const canRun = items.length > 0 && !problems.length && !bad.length && !needsPick && !noEntry && !busy && !running && !!info;
  const limits = { cpu: Number(nums.cpu) || 0, memory_mb: Number(nums.memory_mb) || 0, time: Number(nums.time) || 0, pids: Number(nums.pids) || 0 };
  const limitsOpen = showLimits || profile === "custom" || bad.length > 0;
  const featured = FEATURED.map((id) => demos.find((d) => d.id === id)).filter(Boolean);
  const blocked = !canRun && items.length > 0 && !busy && !running;

  const banner = runError && (
    <div className="enter relative border border-line bg-surface py-3 pl-6 pr-4">
      <span className="absolute inset-y-0 left-0 w-1.5 bg-warn" />
      <div className="flex items-center gap-2 font-mono text-sm font-semibold uppercase tracking-wide text-warn">
        <AlertTriangle size={15} />
        {runError.code === "CAPACITY_FULL" ? "REJECTED — CAPACITY FULL" : runError.code === "ENTRY_POINT_SELECTION_REQUIRED" ? "Choose an entry point" : "Could not start the job"}
      </div>
      <p className="mt-1 text-xs text-muted">{runError.message}</p>
      {runError.capacity && (
        <p className="mt-1 font-mono text-[11px] text-faint">
          running {runError.capacity.active}/{runError.capacity.max_active} &middot; queued {runError.capacity.pending}/{runError.capacity.max_pending}
        </p>
      )}
      {runError.details?.length > 0 && runError.code !== "ENTRY_POINT_SELECTION_REQUIRED" && (
        <ul className="mt-1 list-disc pl-5 text-xs text-muted">{runError.details.slice(0, 6).map((d, i) => <li key={i}>{d.path} {d.reason}</li>)}</ul>
      )}
    </div>
  );

  return (
    <div className="grid min-h-[calc(100vh-3rem)] lg:grid-cols-[23rem_minmax(0,1fr)]">
      {/* ================= input rail ================= */}
      <aside className="border-b border-line bg-surface lg:sticky lg:top-12 lg:h-[calc(100vh-3rem)] lg:overflow-y-auto lg:border-b-0 lg:border-r">
        <Step index="01" title="Source"
          right={items.length > 0 && <button type="button" onClick={clearAll} className="text-[11px] font-medium text-faint hover:text-red">Clear all</button>}>
          <div
            role="button" tabIndex={0} aria-label="Add files"
            onClick={() => input.current?.click()}
            onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
            onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
            onDragLeave={() => setDrag(false)}
            onDrop={(e) => { e.preventDefault(); setDrag(false); addFiles([...e.dataTransfer.files]); }}
            className={cx("press cursor-pointer border border-dashed px-4 text-center",
              items.length ? "py-3" : "py-8", drag ? "border-red bg-red/10" : "border-line2 bg-bg hover:border-fg")}>
            <Upload size={items.length ? 15 : 22} className={cx("mx-auto transition-transform", drag ? "-translate-y-0.5 text-red" : "text-muted")} />
            <p className="mt-2 text-[13px] font-medium">{items.length ? "Add more files" : "Drop source files or a ZIP"}</p>
            {!items.length && <p className="mt-1 text-[11px] text-faint">or click to browse &middot; 20 files &middot; 2 MB each &middot; 10 MB total</p>}
            <input ref={input} type="file" multiple className="hidden"
              onChange={(e) => { addFiles([...e.target.files]); e.target.value = ""; }} />
          </div>

          {items.length === 0 && (
            <select aria-label="Load a demo program" defaultValue="" onChange={(e) => { loadDemo(e.target.value); e.target.value = ""; }} className={cx(inputCls, "mt-3")}>
              <option value="">Or load a demo program...</option>
              {demos.map((d) => <option key={d.id} value={d.id}>{d.name} ({d.language})</option>)}
            </select>
          )}

          {notice && <p className="mt-3 border-l-2 border-warn pl-2.5 text-xs text-warn">{notice}</p>}

          {items.length > 0 && (
            <div className="mt-3">
              <div className="mb-1.5 flex items-center justify-between text-[10px] font-semibold uppercase tracking-label text-faint">
                <span>{info ? `${info.file_count} file${info.file_count === 1 ? "" : "s"}` : `${items.length} selected`}</span>
                <span className="font-mono normal-case tracking-normal">{fmtBytes(info ? info.total_bytes : items.reduce((s, x) => s + x.size, 0))}</span>
              </div>
              <ul className="divide-y divide-line border border-line bg-bg">
                {items.map((it) => (
                  <li key={it.id} className="enter flex items-center gap-2 px-2.5 py-1.5 transition-colors hover:bg-raised">
                    {it.zip ? <FileArchive size={14} className="shrink-0 text-warn" /> : <FileCode2 size={14} className="shrink-0 text-muted" />}
                    <span className="min-w-0 flex-1 truncate font-mono text-xs">{it.name}</span>
                    <span className="shrink-0 font-mono text-[11px] text-faint">{fmtBytes(it.size)}</span>
                    <button type="button" aria-label={`Remove ${it.name}`} onClick={() => removeItem(it.id)} className="press shrink-0 text-faint hover:text-red">
                      <X size={14} />
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {problems.length > 0 && (
            <div className="enter mt-3 border border-red/50 border-l-4 border-l-red bg-red/10 p-3 text-xs">
              <div className="mb-1 flex items-center gap-1.5 font-semibold text-bad"><AlertTriangle size={13} /> Rejected before any container was created</div>
              <ul className="space-y-1 text-muted">
                {problems.slice(0, 8).map((p, i) => <li key={i}><span className="font-mono text-fg">{p.path}</span> {p.reason}</li>)}
              </ul>
            </div>
          )}

          {info && rows.length > 0 && (
            <details className="mt-3 text-xs" open={rows.length <= 10}>
              <summary className="cursor-pointer font-medium text-muted hover:text-fg">Project tree</summary>
              <ul className="mt-2 space-y-0.5 font-mono">
                {rows.map((r, i) => (
                  <li key={i} style={{ paddingLeft: r.depth * 14 }} className="flex items-center gap-2">
                    {r.dir ? <span className="text-muted">{r.name}/</span> : (
                      <>
                        <span className={r.entry ? "text-fg" : "text-muted"}>{r.name}</span>
                        <span className="text-faint">{fmtBytes(r.size)}</span>
                        {r.entry && <Chip tone="red">entry</Chip>}
                      </>
                    )}
                  </li>
                ))}
              </ul>
              {info.ignored?.length > 0 && <p className="mt-1.5 text-faint">Skipped: {info.ignored.join(", ")}</p>}
            </details>
          )}
        </Step>

        <Step index="02" title="Target">
          <div className="space-y-4">
            <div>
              <div className="mb-1.5 text-xs font-medium text-muted">Language</div>
              <Segmented label="Language" options={LANGS} value={language}
                onChange={(k) => { setLanguage(k); if (profile === "preset") applyProfile("preset", k); }} />
            </div>
            {info && info.entry.candidates.length > 1 && (
              <Field label={needsPick ? "Several programs found - choose one" : "Entry point"}
                hint="Only the chosen program and its dependencies are compiled">
                <select id="entry" value={entry} onChange={(e) => setEntry(e.target.value)} className={cx(inputCls, needsPick && "!border-warn")}>
                  <option value="" disabled>Select entry point...</option>
                  {info.entry.candidates.map((c) => <option key={c.path} value={c.path}>{c.label}</option>)}
                </select>
              </Field>
            )}
            {noEntry && <p className="border-l-2 border-warn pl-2.5 text-xs text-warn">No entry point found (a main() function{language === "java" ? " in class Main" : ""}).</p>}
          </div>
        </Step>

        <Step index="03" title="Budget"
          right={
            <button type="button" onClick={() => setShowLimits(!showLimits)} aria-expanded={limitsOpen}
              className="press inline-flex items-center gap-1 text-[11px] font-medium text-faint hover:text-fg">
              {limitsOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />} Adjust
            </button>
          }>
          <Segmented label="Resource profile" options={PROFILES} value={profile} onChange={applyProfile} />
          {!limitsOpen && (
            <dl className="mt-3 grid grid-cols-4 gap-px border border-line bg-line text-center">
              {[["CPU", limits.cpu], ["MB", limits.memory_mb], ["SEC", limits.time], ["PIDs", limits.pids]].map(([l, v]) => (
                <div key={l} className="bg-bg py-1.5">
                  <dd className="font-mono text-[13px] text-fg">{v}</dd>
                  <dt className="text-[9px] font-semibold uppercase tracking-label text-faint">{l}</dt>
                </div>
              ))}
            </dl>
          )}
          {limitsOpen && (
            <div className="enter mt-3">
              <div className="grid grid-cols-2 gap-3">
                {FIELDS.map(([k, l]) => (
                  <Field key={k} label={l} hint={`${minimum[k]} - ${platform[k]}`}>
                    <input id={`lim-${k}`} type="number" inputMode="decimal" step={k === "cpu" ? 0.25 : 1} value={nums[k]}
                      onChange={(e) => edit(k, e.target.value)}
                      aria-invalid={bad.includes(k)}
                      className={cx(inputCls, "font-mono", bad.includes(k) && "!border-red")} />
                  </Field>
                ))}
              </div>
              <div className="mt-3 flex items-start justify-between gap-3">
                <p className="text-[11px] leading-snug text-faint">An execution budget. Exceeding a limit is recorded, not assumed malicious.</p>
                <Button onClick={savePreset} disabled={bad.length > 0} className="shrink-0 !py-1 text-xs" title={`Save as the default for ${CATEGORY_LABEL[category]}`}>
                  <Save size={13} /> Save
                </Button>
              </div>
              {presetMsg && <p className="mt-1 text-right text-[11px] text-ok">{presetMsg}</p>}
            </div>
          )}
        </Step>

        <div className="p-4">
          <div className="flex gap-2">
            <Button variant="primary" className="h-11 flex-1 text-[13px]" disabled={!canRun} onClick={run}>
              {busy ? <Loader2 size={15} className="animate-spin" /> : <Play size={14} fill="currentColor" />} Run sandbox
            </Button>
            {running && (
              <Button variant="danger" className="h-11 px-4" onClick={() => api.cancel(job.id)}><Square size={13} fill="currentColor" /> Stop</Button>
            )}
          </div>
          {blocked && (
            <p className="mt-2 text-[11px] text-faint">
              {problems.length ? "Fix the rejected files to continue." : needsPick ? "Choose an entry point to continue." : noEntry ? "No runnable entry point." : bad.length ? "A resource limit is out of range." : "Validating project..."}
            </p>
          )}
        </div>
      </aside>

      {/* ================= execution ================= */}
      <div className="min-w-0 p-5">
        <ExecutionView job={job} limits={limits} ai={cfg?.ai} onChange={setJob} banner={banner}
          empty={<EmptyState demos={featured} onPick={loadDemo} />} />
      </div>
    </div>
  );
}

function EmptyState({ demos, onPick }) {
  const steps = [["01", "Source", "Drop files or a ZIP"], ["02", "Target", "Pick language and entry"], ["03", "Budget", "Choose resource limits"], ["04", "Run", "Inspect the verdict"]];
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-px border border-line bg-line lg:grid-cols-4">
        {steps.map(([n, t, d]) => (
          <div key={n} className="bg-surface px-4 py-3">
            <div className="font-mono text-xs text-red">{n}</div>
            <div className="mt-1 text-[13px] font-semibold">{t}</div>
            <div className="text-xs text-muted">{d}</div>
          </div>
        ))}
      </div>
      {demos.length > 0 && (
        <Panel title="Try a demo" flush>
          <div className="grid gap-px bg-line sm:grid-cols-2 xl:grid-cols-3">
            {demos.map((d, i) => (
              <button key={d.id} type="button" onClick={() => onPick(d.id)} style={{ animationDelay: `${i * 40}ms` }}
                className="press enter group flex flex-col items-start gap-1.5 bg-surface p-4 text-left hover:bg-raised">
                <span className="flex w-full items-center justify-between">
                  <span className="text-[13px] font-semibold">{d.name}</span>
                  <ChevronRight size={14} className="text-faint transition-transform group-hover:translate-x-0.5 group-hover:text-red" />
                </span>
                <span className="font-mono text-[11px] uppercase text-faint">{d.language} &middot; {d.profile}</span>
                <span className="text-xs leading-snug text-muted">{d.expect}</span>
              </button>
            ))}
          </div>
        </Panel>
      )}
    </div>
  );
}

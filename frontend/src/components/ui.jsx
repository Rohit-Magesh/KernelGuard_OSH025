import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

export const cx = (...a) => a.filter(Boolean).join(" ");
export const dash = (s) => (s || "").replace(/ - /g, " — ");

// Tone -> classes. `bar` is the solid colour used for stripes and fills.
export const TONES = {
  ok: { text: "text-ok", chip: "text-ok border-ok/40 bg-ok/10", bar: "bg-ok" },
  bad: { text: "text-bad", chip: "text-bad border-bad/40 bg-bad/10", bar: "bg-red" },
  warn: { text: "text-warn", chip: "text-warn border-warn/40 bg-warn/10", bar: "bg-warn" },
  red: { text: "text-red", chip: "text-fg border-red bg-red", bar: "bg-red" },
  accent: { text: "text-fg", chip: "text-fg border-line2 bg-raised", bar: "bg-fg" },
  mute: { text: "text-muted", chip: "text-muted border-line bg-transparent", bar: "bg-line2" },
};

export function statusTone(status) {
  if (status === "COMPLETED") return "ok";
  if (["TERMINATED", "MONITOR_FAILURE", "ERROR"].includes(status)) return "bad";
  if (["COMPILATION_FAILED", "RUNTIME_ERROR", "REJECTED_CAPACITY", "INTERRUPTED"].includes(status)) return "warn";
  if (["QUEUED", "STARTING", "COMPILING", "RUNNING"].includes(status)) return "accent";
  return "mute";
}

export function Chip({ tone = "mute", children, className = "", title }) {
  return (
    <span title={title}
      className={cx("inline-flex items-center gap-1 border px-1.5 py-px font-mono text-[10px] font-medium uppercase leading-4 tracking-wide",
        (TONES[tone] || TONES.mute).chip, className)}>
      {children}
    </span>
  );
}

/** Compact status label (history rows, chips). */
export function StatusBadge({ job }) {
  const tone = statusTone(job?.status);
  const active = ["STARTING", "COMPILING", "RUNNING"].includes(job?.status);
  const label = job ? dash(job.status_label || job.status) : "IDLE";
  return (
    <span className={cx("inline-flex items-center gap-2 font-mono text-xs font-semibold uppercase tracking-wide", TONES[job ? tone : "mute"].text)}>
      <span className={cx("h-2 w-2 rounded-full bg-current", active && "led")} />
      {label}
    </span>
  );
}

/** Rectangular titled panel with a graphite header bar. */
export function Panel({ title, right, children, className = "", flush = false, index }) {
  return (
    <section className={cx("border border-line bg-surface", className)}>
      {(title || right) && (
        <header className="flex h-9 items-center justify-between gap-3 border-b border-line bg-raised px-3">
          <h2 className="flex min-w-0 items-center gap-2 truncate text-[11px] font-semibold uppercase tracking-label text-muted">
            {index && <span className="font-mono text-red">{index}</span>}
            {title}
          </h2>
          {right}
        </header>
      )}
      <div className={flush ? "" : "p-4"}>{children}</div>
    </section>
  );
}

/** Numbered block of the input rail. */
export function Step({ index, title, right, children }) {
  return (
    <div className="border-b border-line px-4 py-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-label text-muted">
          <span className="font-mono text-red">{index}</span>
          {title}
        </h2>
        {right}
      </div>
      {children}
    </div>
  );
}

export function Expand({ title, children, defaultOpen = false, badge }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border-t border-line first:border-t-0">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
        className="press flex w-full items-center gap-2 px-4 py-3 text-left text-[13px] text-muted hover:bg-raised hover:text-fg">
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span className="font-medium">{title}</span>
        {badge}
      </button>
      {open && <div className="enter px-4 pb-4">{children}</div>}
    </div>
  );
}

/** One instrument cell of the metric strip. */
export function Meter({ label, value, display, limit, limitText, pct, note, running }) {
  const p = Math.max(0, Math.min(100, pct ?? (limit ? (value / limit) * 100 : 0)));
  const hot = p >= 90;
  return (
    <div className="min-w-0 bg-surface px-4 py-3">
      <div className="flex items-center justify-between text-[10px] font-semibold uppercase tracking-label text-faint">
        <span>{label}</span>
        <span className={cx("font-mono", hot ? "text-bad" : "text-faint")}>{Math.round(p)}%</span>
      </div>
      <div className="mt-1.5 flex items-baseline gap-2 font-mono">
        <span className={cx("text-[26px] font-medium leading-none tabular-nums transition-colors", hot ? "text-bad" : "text-fg")}>{display}</span>
        <span className="truncate text-[11px] text-faint">/ {limitText}</span>
      </div>
      <div className="relative mt-3 h-1 bg-line">
        <div className={cx("h-1 transition-[width] duration-500 ease-out", hot ? "bg-red" : "bg-fg")} style={{ width: `${p}%` }} />
        {running && p < 100 && <div className="absolute inset-y-0 right-0 w-px bg-red led" />}
      </div>
      <div className="mt-1.5 h-4 text-[11px] text-faint">{note}</div>
    </div>
  );
}

export function Field({ label, children, hint }) {
  return (
    <label className="block text-xs text-muted">
      <span className="mb-1 block font-medium">{label}</span>
      {children}
      {hint && <span className="mt-1 block font-mono text-[10px] text-faint">{hint}</span>}
    </label>
  );
}

export const inputCls =
  "w-full border border-line2 bg-bg px-2.5 py-1.5 text-sm text-fg transition-colors placeholder:text-faint hover:border-faint focus:border-red focus:outline-none";

export function Button({ children, variant = "default", className = "", ...p }) {
  const v = {
    primary: "bg-red text-white font-semibold uppercase tracking-wide hover:bg-[#f2433d] disabled:bg-raised disabled:text-faint",
    default: "border border-line2 bg-raised text-fg hover:border-fg disabled:opacity-40 disabled:hover:border-line2",
    danger: "border border-red text-bad hover:bg-red hover:text-white disabled:opacity-40",
    ghost: "text-muted hover:text-fg disabled:opacity-40",
  }[variant];
  return (
    <button type="button" {...p} className={cx("press inline-flex items-center justify-center gap-2 px-3 py-1.5 text-[13px]", v, className)}>
      {children}
    </button>
  );
}

/** Segmented control: connected square cells, selected one inverted. */
export function Segmented({ options, value, onChange, label, cols }) {
  return (
    <div role="radiogroup" aria-label={label} className="grid border border-line2" style={{ gridTemplateColumns: `repeat(${cols || options.length}, minmax(0, 1fr))` }}>
      {options.map(([k, l], i) => (
        <button key={k} type="button" role="radio" aria-checked={value === k} onClick={() => onChange(k)}
          className={cx("press py-1.5 text-xs font-medium", i > 0 && "border-l border-line2",
            value === k ? "bg-fg text-bg" : "bg-bg text-muted hover:bg-raised hover:text-fg")}>
          {l}
        </button>
      ))}
    </div>
  );
}

export const fmtBytes = (n) => (n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(2)} MB`);
export const fmtTime = (iso) => (iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "");
export const num = (v, d = 1) => (v === null || v === undefined ? "n/a" : Number(v).toFixed(d).replace(/\.0+$/, ""));

import { useEffect, useState } from "react";
import { Check, Minus, X } from "lucide-react";
import { api } from "../api.js";
import { Chip, Panel } from "../components/ui.jsx";

export default function Security() {
  const [s, setS] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => { api.security().then(setS).catch((e) => setErr(e.message)); }, []);
  if (err) return <p className="border border-red p-4 text-sm text-bad">{err}</p>;
  if (!s) return <p className="p-4 text-sm text-muted">Loading...</p>;
  const verified = s.controls.filter((c) => c.verified === true).length;
  return (
    <div className="max-w-5xl space-y-5">
      <Panel title={`Controls · ${verified}/${s.controls.length} verified`} flush
        right={<span className="font-mono text-[11px] text-faint">{s.verified_at ? `read from inside job ${s.verified_job}` : "run any job to verify"}</span>}>
        <ul className="divide-y divide-line">
          {s.controls.map((c, i) => (
            <li key={c.key} className="enter flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3" style={{ animationDelay: `${i * 30}ms` }}>
              <span className={`flex h-5 w-5 shrink-0 items-center justify-center ${c.verified === true ? "bg-ok text-bg" : c.verified === false ? "bg-red text-white" : "border border-line2 text-faint"}`}>
                {c.verified === true ? <Check size={13} strokeWidth={3} /> : c.verified === false ? <X size={13} strokeWidth={3} /> : <Minus size={13} />}
              </span>
              <div className="min-w-0 flex-1 basis-72">
                <div className="text-[13px] font-semibold">{c.name}</div>
                <div className="text-xs leading-relaxed text-muted">{c.description}</div>
              </div>
              <div className="flex gap-2">
                <Chip tone={c.configured ? "mute" : "warn"}>{c.configured ? "configured" : "not configured"}</Chip>
                {c.verified === true && <Chip tone="ok">verified</Chip>}
                {c.verified === false && <Chip tone="bad">verification failed</Chip>}
                {c.verified === null && <Chip>unverified</Chip>}
              </div>
            </li>
          ))}
        </ul>
      </Panel>
      <Panel title="Limitations">
        <ul className="space-y-2 text-[13px] leading-relaxed text-muted">
          {s.limitations.map((l, i) => (
            <li key={i} className="flex gap-2.5"><span className="mt-[7px] h-1.5 w-1.5 shrink-0 bg-line2" />{l}</li>
          ))}
        </ul>
      </Panel>
    </div>
  );
}

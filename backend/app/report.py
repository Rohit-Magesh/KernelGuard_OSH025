"""PDF execution report built server-side from persisted job data (ReportLab) - works without AI."""
import io
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, Preformatted, SimpleDocTemplate, Spacer, Table, TableStyle)

from . import ai, config as C
from . import violations as V

NAVY, CYAN, GREY, RED, GREEN, AMBER = (colors.HexColor(x) for x in ("#0b1220", "#0891b2", "#64748b", "#b91c1c", "#15803d", "#b45309"))
CONCEPTS = {
    "TIME_LIMIT": "Process lifetime & scheduling: a wall-clock watchdog ends a process that holds the CPU past its budget.",
    "MEMORY_LIMIT": "Virtual memory & cgroups: memory.max caps resident pages; the OOM killer reclaims by killing.",
    "PROCESS_LIMIT": "Process management: the pids controller bounds tasks (fork/clone/threads) per cgroup.",
    "CPU_LIMIT": "CPU scheduling: the CFS bandwidth controller limits CPU time per period.",
    "OUTPUT_LIMIT": "I/O control: bounded pipes protect the host from unbounded output.",
    "SECURITY_POLICY_VIOLATION": "System calls & protection: seccomp-bpf filters the syscall interface.",
}


def _t(s, n=None):
    """Latin-1 safe text for the built-in PDF fonts."""
    s = "" if s is None else str(s)
    s = s.encode("latin-1", "replace").decode("latin-1")
    return s if n is None or len(s) <= n else s[:n] + " ..."


def _p(text, style):
    return Paragraph(escape(_t(text)), style)


def build_pdf(job):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
                            title=f"KernelGuard report {job['id']}", author="KernelGuard")
    ss = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=ss["BodyText"], fontSize=9, leading=12, alignment=TA_LEFT)
    small = ParagraphStyle("small", parent=body, fontSize=8, leading=10, textColor=GREY)
    cell = ParagraphStyle("cell", parent=body, fontSize=8, leading=10)
    h1 = ParagraphStyle("h1", parent=ss["Title"], fontSize=18, textColor=NAVY, alignment=TA_LEFT, spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=11, textColor=CYAN, spaceBefore=10, spaceAfter=4)
    mono = ParagraphStyle("mono", fontName="Courier", fontSize=7.5, leading=9)
    S = []

    status, primary = job.get("status"), job.get("primary_reason")
    label = job.get("status_label") or V.label_for(status, primary, job.get("violations"))
    col = GREEN if status == "COMPLETED" else (RED if status in ("TERMINATED", "MONITOR_FAILURE", "ERROR", "COMPILATION_FAILED") else AMBER)
    S.append(Paragraph("KernelGuard - Execution Report", h1))
    S.append(_p(f"Job {job['id']}  |  generated from persisted backend data (not a screenshot)", small))
    S.append(Spacer(1, 6))
    banner = Table([[Paragraph(f"<font color='white'><b>{escape(_t(label))}</b></font>",
                               ParagraphStyle("b", parent=body, fontSize=13, leading=16))]], colWidths=[178 * mm])
    banner.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), col), ("TOPPADDING", (0, 0), (-1, -1), 6),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 6), ("LEFTPADDING", (0, 0), (-1, -1), 8)]))
    S.append(banner)

    def kv(rows, widths=(48 * mm, 130 * mm)):
        t = Table([[_p(k, cell), _p(v, cell)] for k, v in rows], colWidths=list(widths))
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
                               ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f1f5f9")), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        return t

    cfg = job.get("config") or {}
    S.append(Paragraph("1. Job summary", h2))
    rows = [("Job ID", job["id"]), ("Submitted", job.get("created_at")), ("Started / finished", f"{job.get('started_at')} / {job.get('finished_at')}"),
            ("Language", C.LANGS.get(job.get("language"), {}).get("label", job.get("language"))),
            ("Entry point", job.get("entry_point")), ("Final status", label),
            ("Primary termination reason", V.REASON_LABEL.get(primary, primary) if primary not in (None, "NONE") else "none"),
            ("Exit code", job.get("exit_code")), ("Run-phase runtime", f"{job.get('runtime')} s")]
    if job.get("challenge_id"):
        rows.append(("Challenge", f"{job['challenge_id']}" + ("  (SIMULATED fault injection - see section 8)" if job.get("simulated") else "")))
    if job.get("error"):
        rows.append(("Error / note", job["error"]))
    S.append(kv(rows))

    S.append(Paragraph("2. Uploaded files", h2))
    files = job.get("files") or []
    S.append(kv([(f["path"], f"{f['size']} bytes") for f in files] or [("(none)", "")]))
    plan = job.get("plan") or {}
    if plan:
        S.append(Spacer(1, 4))
        S.append(_p("Build plan - sources: " + ", ".join(plan.get("sources", [])[:12]) + " | compile: " +
                    (" ; ".join(" ".join(a) for a in plan.get("compile", [])) or "(interpreted)") + " | run: " +
                    " ".join(plan.get("run", [])), small))
        if plan.get("ignored_programs"):
            S.append(_p("Other independent programs in the upload (not built): " + ", ".join(plan["ignored_programs"]), small))

    S.append(Paragraph("3. Configured resource limits (immutable for this job)", h2))
    S.append(kv([("Profile", f"{job.get('resource_profile')}  (source: {cfg.get('source')})"), ("CPU", f"{cfg.get('cpu')} CPU (CFS quota)"),
                 ("Memory", f"{cfg.get('memory_mb')} MB (swap disabled)"), ("Runtime", f"{cfg.get('time')} s wall clock (run phase)"),
                 ("PIDs", f"{cfg.get('pids')} (pids cgroup)"),
                 ("Compile phase budget", f"{(cfg.get('compile') or {}).get('memory_mb')} MB / {(cfg.get('compile') or {}).get('pids')} PIDs / "
                                          f"{(cfg.get('compile') or {}).get('time')} s - toolchain only; replaced by the limits above before user code runs"),
                 ("Network", "disabled (network mode none)")]))

    tel = job.get("telemetry") or {}
    S.append(Paragraph("4. Observed resource usage", h2))
    S.append(kv([("Peak CPU", f"{job.get('cpu_usage')} %  (100 % = one core; quota {cfg.get('cpu')} CPU)"),
                 ("Peak memory", f"{job.get('peak_memory')} MB of {cfg.get('memory_mb')} MB"),
                 ("Peak processes", f"{job.get('process_count')} of {cfg.get('pids')}"),
                 ("Runtime", f"{job.get('runtime')} s of {cfg.get('time')} s"),
                 ("Telemetry source", tel.get("source", "n/a")),
                 ("Reliability - memory", (tel.get("reliability") or {}).get("memory", "n/a")),
                 ("Reliability - CPU", (tel.get("reliability") or {}).get("cpu", "n/a")),
                 ("Reliability - PIDs", (tel.get("reliability") or {}).get("pids", "n/a")),
                 ("Kernel counters (run-phase delta)", str(tel.get("cgroup_run_delta")) if tel.get("cgroup_run_delta") else "not available - "
                                                       "values above come from Docker statistics only")]))

    S.append(Paragraph("5. Detected violations", h2))
    vs = job.get("violations") or []
    if not vs:
        S.append(_p("No limit violations were observed.", body))
    else:
        data = [[_p(x, cell) for x in ("Type", "Role / confidence", "Action", "Limit", "Observed", "t+ / evidence")]]
        for v in vs:
            data.append([_p(v["type"], cell), _p(f"{v.get('role')} / {v.get('confidence')}", cell), _p(v.get("action"), cell),
                         _p(f"{v.get('limit') if v.get('limit') is not None else 'n/a'} {v.get('unit') or ''}", cell),
                         _p(f"{v.get('observed') if v.get('observed') is not None else 'not measured'} {v.get('unit') or ''}", cell),
                         _p(("t+" + str(v["t_offset"]) + " s: " if v.get("t_offset") is not None else "") + str(v.get("evidence")), cell)])
        t = Table(data, colWidths=[33 * mm, 26 * mm, 23 * mm, 19 * mm, 21 * mm, 56 * mm], repeatRows=1)
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
                               ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        S.append(t)
        S.append(_p("Resource limits are execution budgets: a violation shows a program exceeded its budget, not that it is malicious. "
                    "CONFIRMED = direct kernel/Docker/watchdog evidence; PROBABLE = inferred from program output or sampled metrics.", small))

    S.append(Paragraph("6. Program output", h2))
    for title, key in (("Compiler output", "compile_output"), ("stdout", "stdout"), ("stderr", "stderr")):
        txt = job.get(key) or ""
        S.append(_p(title + (" (empty)" if not txt else ""), small))
        if txt:
            S.append(Preformatted(_t(txt, 3500), mono))

    S.append(PageBreak())
    ex = job.get("explanation") or {}
    content = ex.get("content") or ai.rule_based(job)
    src = ex.get("source", "RULE_BASED")
    S.append(Paragraph(f"7. Explanation  [source: {src}{' - ' + str(ex.get('model')) if ex.get('model') else ''}]", h2))
    S.append(_p(content.get("summary"), body))
    S.append(_p("Why execution ended: " + str(content.get("why_ended")), body))
    for k, title in (("observed_facts", "Observed facts"), ("probable_causes", "Probable causes (not proven)")):
        if content.get(k):
            S.append(_p(title, ParagraphStyle("t", parent=body, fontName="Helvetica-Bold")))
            for x in content[k]:
                S.append(_p("- " + x, body))
    if content.get("violations"):
        S.append(_p("Per violation", ParagraphStyle("t2", parent=body, fontName="Helvetica-Bold")))
        for x in content["violations"]:
            S.append(_p(f"- {x.get('type')}: {x.get('explanation')}", body))
    if content.get("relationships"):
        S.append(_p("Relationships: " + content["relationships"], body))
    S.append(_p("Limits and metrics: " + str(content.get("limits_and_metrics")), body))
    if content.get("suggestions"):
        S.append(_p("Recommended improvements", ParagraphStyle("t3", parent=body, fontName="Helvetica-Bold")))
        for x in content["suggestions"]:
            S.append(_p("- " + x, body))

    S.append(Paragraph("8. Operating-system concepts and enforcement", h2))
    for line in content.get("os_mechanism", []):
        S.append(_p("- " + line, body))
    for t in dict.fromkeys(v["type"] for v in vs):
        if t in CONCEPTS:
            S.append(_p("- " + CONCEPTS[t], small))

    S.append(Paragraph("9. Security controls applied", h2))
    hello = tel.get("security_verified") or {}
    verified = bool(hello)
    ctrls = [
        ("Non-root user", f"uid {hello.get('uid')} probe / workload uid 1000" if verified else "uid 1000 (configured; not verified)"),
        ("Capabilities", ("all dropped - CapEff=%s" % hello.get("cap_eff")) if verified else "cap_drop ALL (configured)"),
        ("no-new-privileges", ("active" if hello.get("no_new_privs") == 1 else "NOT active") if verified else "configured"),
        ("Seccomp", (f"filter mode {hello.get('seccomp')} ({tel.get('seccomp_profile')} profile)") if verified else "configured"),
        ("Read-only root filesystem", ("verified: writes to / fail with EROFS" if hello.get("rootfs_read_only") else "NOT read-only") if verified else "configured"),
        ("Network", (f"{hello.get('net_ifaces')} non-loopback interfaces" if hello.get("net_ifaces") is not None else "configured none")),
        ("Docker socket in sandbox", "absent" if verified and not hello.get("docker_sock") else ("PRESENT" if verified else "not mounted (by design)")),
        ("cgroup limits applied by kernel", (f"run phase: pids.max={(tel.get('limits_verified') or {}).get('pids_max')}, memory.max={(tel.get('limits_verified') or {}).get('memory_max')} B, cpu.max='{hello.get('cpu_max')}'"
                                             if verified else "configured")),
        ("Workspace", "tmpfs mounted at /workspace and /tmp (size-capped); no host bind mounts; project streamed in via tar"),
    ]
    S.append(kv(ctrls))
    S.append(_p("'verified' items were read from inside the job's container by the telemetry probe at start-up; "
                "'configured' items could not be verified for this job.", small))

    S.append(Paragraph("10. Queue and monitoring", h2))
    mon = job.get("monitor") or {}
    S.append(kv([("Queue position at submission", job.get("queue_position") if job.get("queue_position") else "started immediately / n/a"),
                 ("Monitor samples received", mon.get("samples_received")), ("Monitor restarts", mon.get("restarts")),
                 ("Monitor failure", (mon.get("failed") or {}).get("detail") or "none"),
                 ("Fault injection", "SIMULATED fault (deterministic test): " + str(mon.get("fault_injection")) if mon.get("simulated") else "none")]))
    for e in (mon.get("events") or [])[:12]:
        S.append(_p(f"{e['ts']}  {e['event']}  {e.get('detail', '')}", small))
    S.append(Spacer(1, 8))
    S.append(_p("Measurements marked 'not measured' were unavailable and are never estimated. Challenge C4 failures are simulated; "
                "all kernel and container measurements in this report are real.", small))
    doc.build(S)
    return buf.getvalue()

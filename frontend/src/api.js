// Thin API client. Errors carry the backend's structured body: {error:{code,message,details[],capacity?,job_id?}}.
async function parse(r) {
  let body = null;
  try { body = await r.json(); } catch { /* non-JSON body */ }
  if (!r.ok) {
    const e = new Error(body?.error?.message || body?.detail || r.statusText || "Request failed");
    e.status = r.status;
    e.code = body?.error?.code || "ERROR";
    e.details = body?.error?.details || [];
    e.capacity = body?.error?.capacity;
    e.jobId = body?.error?.job_id;
    throw e;
  }
  return body;
}
const get = (p) => fetch(p).then(parse);
const send = (p, method, body) =>
  fetch(p, { method, headers: body ? { "Content-Type": "application/json" } : undefined, body: body ? JSON.stringify(body) : undefined }).then(parse);

export function buildForm(items, language, extra = {}) {
  const fd = new FormData();
  items.forEach((it) => fd.append("files", it.file, it.name));
  fd.append("language", language);
  Object.entries(extra).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== "") fd.append(k, v); });
  return fd;
}

export const api = {
  health: () => get("/api/health"),
  config: () => get("/api/config"),
  validate: (items, language) => fetch("/api/projects/validate", { method: "POST", body: buildForm(items, language) }).then(parse),
  createJob: (items, language, extra) => fetch("/api/jobs", { method: "POST", body: buildForm(items, language, extra) }).then(parse),
  job: (id) => get(`/api/jobs/${id}`),
  jobs: () => get("/api/jobs?limit=100"),
  cancel: (id) => send(`/api/jobs/${id}/cancel`, "POST"),
  explain: (id, useAi = true) => send(`/api/jobs/${id}/explanation`, "POST", { use_ai: useAi }),
  reportUrl: (id) => `/api/jobs/${id}/report`,
  savePreset: (category, v) => send(`/api/presets/${category}`, "PUT", v),
  challenges: () => get("/api/challenges"),
  runChallenge: (id) => send(`/api/challenges/${id}/run`, "POST"),
  challengeRun: (runId) => get(`/api/challenges/runs/${runId}`),
  security: () => get("/api/security"),
  demos: () => get("/api/demos"),
  demo: (id) => get(`/api/demos/${id}`),
};